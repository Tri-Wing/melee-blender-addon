using HSDRaw.Common.Animation;
using HSDRaw.Common;
using HSDRaw.Melee.Gr;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record MaterialAnimationTrack(string Channel, JointAnimationKey[] Keys);
public sealed record TextureAnimationTracks(int TextureIndex, int TextureMapId,
    uint Flags, float EndFrame, bool Loop, MaterialAnimationTrack[] Tracks);
public sealed record MaterialAnimationTarget(string MaterialId, uint Flags,
    float EndFrame, bool Loop, float MaterialEndFrame, bool MaterialLoop,
    MaterialAnimationTrack[] Tracks,
    TextureAnimationTracks[] Textures);
public sealed record MaterialAnimationSet(int Slot, byte GroupFlag, float EndFrame,
    bool Loop, MaterialAnimationTarget[] Materials);

/// <summary>Decodes stage MatAnimJoint/MOBJ/TObj tracks with model identity and slot ownership.</summary>
public static class StageMaterialAnimations
{
    private static readonly Dictionary<MatTrackType, string> MaterialChannels = new()
    {
        [MatTrackType.HSD_A_M_AMBIENT_R] = "ambient.r",
        [MatTrackType.HSD_A_M_AMBIENT_G] = "ambient.g",
        [MatTrackType.HSD_A_M_AMBIENT_B] = "ambient.b",
        [MatTrackType.HSD_A_M_DIFFUSE_R] = "diffuse.r",
        [MatTrackType.HSD_A_M_DIFFUSE_G] = "diffuse.g",
        [MatTrackType.HSD_A_M_DIFFUSE_B] = "diffuse.b",
        [MatTrackType.HSD_A_M_SPECULAR_R] = "specular.r",
        [MatTrackType.HSD_A_M_SPECULAR_G] = "specular.g",
        [MatTrackType.HSD_A_M_SPECULAR_B] = "specular.b",
        [MatTrackType.HSD_A_M_ALPHA] = "alpha",
        [MatTrackType.HSD_A_M_PE_REF0] = "pixel.reference0",
        [MatTrackType.HSD_A_M_PE_REF1] = "pixel.reference1",
        [MatTrackType.HSD_A_M_PE_DSTALPHA] = "pixel.destinationAlpha"
    };

    private static readonly Dictionary<TexTrackType, string> TextureChannels = new()
    {
        [TexTrackType.HSD_A_T_TIMG] = "image",
        [TexTrackType.HSD_A_T_TRAU] = "translation.x",
        [TexTrackType.HSD_A_T_TRAV] = "translation.y",
        [TexTrackType.HSD_A_T_SCAU] = "scale.x",
        [TexTrackType.HSD_A_T_SCAV] = "scale.y",
        [TexTrackType.HSD_A_T_ROTX] = "rotation.x",
        [TexTrackType.HSD_A_T_ROTY] = "rotation.y",
        [TexTrackType.HSD_A_T_ROTZ] = "rotation.z",
        [TexTrackType.HSD_A_T_BLEND] = "blend",
        [TexTrackType.HSD_A_T_TCLT] = "palette",
        [TexTrackType.HSD_A_T_LOD_BIAS] = "lodBias",
        [TexTrackType.HSD_A_T_KONST_R] = "konst.r",
        [TexTrackType.HSD_A_T_KONST_G] = "konst.g",
        [TexTrackType.HSD_A_T_KONST_B] = "konst.b",
        [TexTrackType.HSD_A_T_KONST_A] = "konst.a",
        [TexTrackType.HSD_A_T_TEV0_R] = "tev0.r",
        [TexTrackType.HSD_A_T_TEV0_G] = "tev0.g",
        [TexTrackType.HSD_A_T_TEV0_B] = "tev0.b",
        [TexTrackType.HSD_A_T_TEV0_A] = "tev0.a",
        [TexTrackType.HSD_A_T_TEV1_R] = "tev1.r",
        [TexTrackType.HSD_A_T_TEV1_G] = "tev1.g",
        [TexTrackType.HSD_A_T_TEV1_B] = "tev1.b",
        [TexTrackType.HSD_A_T_TEV1_A] = "tev1.a",
        [TexTrackType.HSD_A_T_TS_BLEND] = "blend"
    };

    public static MaterialAnimationSet[] Read(StageArchive stage,
        ModelIdentitySnapshot identity, int groupIndex)
    {
        var head = stage.File["map_head"]?.Data as SBM_Map_Head;
        Require(head != null, "MATANIM_GROUP", "Stage model groups are unavailable for material animation extraction.");
        if (groupIndex < 0 || groupIndex >= head!.ModelGroups.Length) return [];
        var group = head.ModelGroups[groupIndex];
        var animations = group.MaterialAnimations;
        if (animations == null) return [];
        var nodes = identity.Nodes.Where(node => node.GroupIndex == groupIndex).ToArray();
        var joints = nodes.Where(node => node.Kind.EndsWith("jobj"))
            .OrderBy(node => node.Index).ToArray();
        var byOwner = nodes.Where(node => node.OwnerId != null)
            .GroupBy(node => node.OwnerId!).ToDictionary(items => items.Key, items => items.ToArray());
        var reader = new ArchiveDataReader(stage.Layout);
        var result = new List<MaterialAnimationSet>();
        for (int slot = 0; slot < animations.Length; slot++)
        {
            var root = animations[slot];
            if (root == null) continue;
            byte groupFlag = group.AnimationFlags?[slot] ?? 0;
            var targets = new List<MaterialAnimationTarget>();
            int jointIndex = 0;
            Visit(group.RootNode, root);
            Require(jointIndex == joints.Length, "MATANIM_TOPOLOGY",
                $"Group {groupIndex:D3} material animation {slot:D3} visited {jointIndex} of {joints.Length} JOBJs.");
            if (targets.Count > 0)
                result.Add(new(slot, groupFlag, targets.Max(target => target.EndFrame),
                    targets.Any(target => target.Loop), targets.ToArray()));

            // Match HSD_JObjAddAnimAll: model and animation children advance in
            // parallel, a missing animation branch stays missing, and instance
            // references never recurse into their shared target hierarchy.
            void Visit(HSD_JOBJ? jobj, HSD_MatAnimJoint? animationNode)
            {
                if (jobj == null) return;
                Require(jointIndex < joints.Length, "MATANIM_TOPOLOGY",
                    $"Group {groupIndex:D3} material animation {slot:D3} exceeds the model JOBJ hierarchy.");
                var joint = joints[jointIndex++];
                if (animationNode != null) ReadMaterials(joint, animationNode);
                if (jobj.Flags.HasFlag(JOBJ_FLAG.INSTANCE)) return;
                var child = jobj.Child;
                var childAnimation = animationNode?.Child;
                while (child != null)
                {
                    Visit(child, childAnimation);
                    child = child.Next;
                    childAnimation = childAnimation?.Next;
                }
            }

            void ReadMaterials(ModelIdentityNode joint, HSD_MatAnimJoint animationNode)
            {
                var dobjs = byOwner.GetValueOrDefault(joint.Id, [])
                    .Where(node => node.Kind == "dobj").OrderBy(node => node.Index).ToArray();
                var materialAnimations = animationNode.MaterialAnimation?.List ?? [];
                // The JOBJ union stores spline/particle data in the same field as
                // a DOBJ list. HSD ignores the MatAnim pointer for those JOBJ
                // classes, so do not interpret the accompanying list as materials.
                if (joint.Kind is "spline-jobj" or "particle-jobj") return;
                Require(materialAnimations.Count <= dobjs.Length, "MATANIM_TOPOLOGY",
                    $"Group {groupIndex:D3} material animation {slot:D3} has {materialAnimations.Count} materials for {joint.Kind} JOBJ {joint.Index:D3} at 0x{joint.SourceOffset:X}, which owns {dobjs.Length} DOBJs.");
                for (int materialIndex = 0; materialIndex < materialAnimations.Count; materialIndex++)
                {
                    var animation = materialAnimations[materialIndex];
                    var dobj = dobjs[materialIndex];
                    var tracks = Decode(animation.AnimationObject, descriptor =>
                        MaterialChannels.GetValueOrDefault(descriptor.MatTrackType));
                    var textures = animation.TextureAnimation?.List.Select(texture =>
                    {
                        var textureTracks = Decode(texture.AnimationObject, descriptor =>
                            TextureChannels.GetValueOrDefault(descriptor.TexTrackType));
                        var aobj = texture.AnimationObject;
                        return new TextureAnimationTracks(TextureIndex(dobj, (int)texture.GXTexMapID),
                            (int)texture.GXTexMapID, aobj == null ? 0 : unchecked((uint)aobj.Flags),
                            EndFrame(aobj, textureTracks), groupFlag != 0 || aobj?.Flags.HasFlag(AOBJ_Flags.ANIM_LOOP) == true,
                            textureTracks);
                    }).Where(texture => texture.Tracks.Length > 0).ToArray() ?? [];
                    if (tracks.Length == 0 && textures.Length == 0) continue;
                    var materialAobj = animation.AnimationObject;
                    float materialEnd = EndFrame(materialAobj, tracks);
                    bool materialLoop = groupFlag != 0
                        || materialAobj?.Flags.HasFlag(AOBJ_Flags.ANIM_LOOP) == true;
                    float end = Math.Max(materialEnd,
                        textures.Select(texture => texture.EndFrame).DefaultIfEmpty(0).Max());
                    bool loop = materialLoop || textures.Any(texture => texture.Loop);
                    foreach (var pobj in byOwner.GetValueOrDefault(dobj.Id, []).Where(node => node.Kind == "pobj"))
                        targets.Add(new(pobj.Id, materialAobj == null ? 0 : unchecked((uint)materialAobj.Flags),
                            end, loop, materialEnd, materialLoop, tracks, textures));
                }
            }
        }
        return result.ToArray();

        int TextureIndex(ModelIdentityNode dobj, int mapId)
        {
            int? mobj = reader.Pointer(dobj.SourceOffset + 8);
            int? texture = mobj.HasValue ? reader.Pointer(mobj.Value + 8) : null;
            for (int index = 0; texture.HasValue && index < 8; index++)
            {
                if (reader.Int(texture.Value + 8) == mapId) return index;
                texture = reader.Pointer(texture.Value + 4);
            }
            return -1;
        }
    }

    private static MaterialAnimationTrack[] Decode(HSD_AOBJ? animation,
        Func<HSD_FOBJDesc, string?> channel)
    {
        if (animation?.FObjDesc == null) return [];
        return animation.FObjDesc.List.Select(descriptor => new
        {
            Channel = channel(descriptor),
            Keys = descriptor.GetDecodedKeys().Select(key => new JointAnimationKey(
                key.Frame, key.Value, key.Tan, key.InterpolationType.ToString())).ToArray()
        }).Where(track => track.Channel != null && track.Keys.Length > 0)
          .Select(track => new MaterialAnimationTrack(track.Channel!, track.Keys)).ToArray();
    }

    private static float EndFrame(HSD_AOBJ? animation, MaterialAnimationTrack[] tracks)
        => Math.Max(animation?.EndFrame ?? 0,
            tracks.SelectMany(track => track.Keys).Select(key => key.Frame).DefaultIfEmpty(0).Max());
}
