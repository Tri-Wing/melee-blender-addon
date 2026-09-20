using HSDRaw.Common.Animation;
using HSDRaw.Common;
using HSDRaw.Melee.Gr;
using HSDRaw.Tools;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record JointAnimationKey(float Frame, float Value, float Tangent, string Interpolation);
public sealed record JointAnimationTrack(string Channel, JointAnimationKey[] Keys);
public sealed record JointAnimationNode(string JobjId, uint Flags, float EndFrame,
    bool Loop, JointAnimationTrack[] Tracks, bool Editable);
public sealed record JointAnimationSet(int Slot, byte GroupFlag, float EndFrame, bool Loop,
    JointAnimationNode[] Nodes);

/// <summary>Decodes stage AnimJoint/AOBJ/FOBJ trees while retaining model-group slot identity.</summary>
public static class StageJointAnimations
{
    private static readonly Dictionary<JointTrackType, string> Channels = new()
    {
        [JointTrackType.HSD_A_J_ROTX] = "rotation.x",
        [JointTrackType.HSD_A_J_ROTY] = "rotation.y",
        [JointTrackType.HSD_A_J_ROTZ] = "rotation.z",
        [JointTrackType.HSD_A_J_TRAX] = "translation.x",
        [JointTrackType.HSD_A_J_TRAY] = "translation.y",
        [JointTrackType.HSD_A_J_TRAZ] = "translation.z",
        [JointTrackType.HSD_A_J_SCAX] = "scale.x",
        [JointTrackType.HSD_A_J_SCAY] = "scale.y",
        [JointTrackType.HSD_A_J_SCAZ] = "scale.z"
    };

    public static JointAnimationSet[] Read(StageArchive stage, ModelIdentitySnapshot identity,
        int groupIndex)
    {
        var head = stage.File["map_head"]?.Data as SBM_Map_Head;
        Require(head != null, "ANIM_GROUP", "Stage model groups are unavailable for animation extraction.");
        // Identity capture can expose sentinel roots after the map_head array.
        // They do not own map-group animation arrays.
        if (groupIndex < 0 || groupIndex >= head!.ModelGroups.Length) return [];
        var group = head.ModelGroups[groupIndex];
        var animations = group.JointAnimations;
        if (animations == null) return [];
        var joints = identity.Nodes.Where(node => node.GroupIndex == groupIndex
            && node.Kind.EndsWith("jobj")).OrderBy(node => node.Index).ToArray();
        var reader = new ArchiveDataReader(stage.Layout);
        var result = new List<JointAnimationSet>();
        for (int slot = 0; slot < animations.Length; slot++)
        {
            var root = animations[slot];
            if (root == null) continue;
            byte groupFlag = group.AnimationFlags?[slot] ?? 0;
            var nodes = new List<JointAnimationNode>();
            int jointIndex = 0;
            Visit(group.RootNode, root);
            Require(jointIndex == joints.Length, "ANIM_TOPOLOGY",
                $"Group {groupIndex:D3} animation {slot:D3} visited {jointIndex} of {joints.Length} JOBJs.");
            if (nodes.Count > 0)
                result.Add(new(slot, groupFlag, nodes.Max(node => Math.Max(node.EndFrame,
                    node.Tracks.SelectMany(track => track.Keys).Max(key => key.Frame))), nodes.Any(node => node.Loop),
                    nodes.ToArray()));

            void Visit(HSD_JOBJ? jobj, HSD_AnimJoint? animationNode)
            {
                if (jobj == null) return;
                Require(jointIndex < joints.Length, "ANIM_TOPOLOGY",
                    $"Group {groupIndex:D3} animation {slot:D3} exceeds the model JOBJ hierarchy.");
                var joint = joints[jointIndex++];
                var animation = animationNode?.AOBJ;
                if (animation?.FObjDesc != null) ReadNode(joint, animation);
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

            void ReadNode(ModelIdentityNode joint, HSD_AOBJ animation)
            {
                var tracks = new List<JointAnimationTrack>();
                var descriptors = animation.FObjDesc.List;
                foreach (var descriptor in descriptors)
                {
                    if (!Channels.TryGetValue(descriptor.JointTrackType, out string? channel))
                        continue;
                    var keys = descriptor.GetDecodedKeys().Select(key => new JointAnimationKey(
                        key.Frame, key.Value, key.Tan, key.InterpolationType.ToString())).ToArray();
                    if (keys.Length > 0) tracks.Add(new(channel, keys));
                }
                if (tracks.Count == 0) return;
                uint flags = unchecked((uint)animation.Flags);
                bool editable = joint.Kind == "jobj" && descriptors.All(descriptor =>
                    Channels.ContainsKey(descriptor.JointTrackType))
                    && animation.EndFrame >= 1 && MathF.Abs(animation.EndFrame - MathF.Round(animation.EndFrame)) < 1e-5f
                    && Enumerable.Range(0, 3).All(axis => reader.Float(joint.SourceOffset + 0x20 + axis * 4) > 1e-6f);
                nodes.Add(new(joint.Id, flags, animation.EndFrame,
                    groupFlag != 0 || animation.Flags.HasFlag(AOBJ_Flags.ANIM_LOOP), tracks.ToArray(), editable));
            }
        }
        return result.ToArray();
    }
}
