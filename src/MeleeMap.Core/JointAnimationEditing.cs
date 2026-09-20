using System.Buffers.Binary;
using System.Numerics;
using System.Text.Json.Serialization;
using HSDRaw.Common.Animation;
using HSDRaw.Tools;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record JointAnimationNodeEdit([property: JsonRequired] int GroupIndex,
    [property: JsonRequired] int Slot, [property: JsonRequired] string JobjId,
    [property: JsonRequired] JointAnimationTrack[] Tracks);
public sealed record JointAnimationEdits([property: JsonRequired] int ProtocolVersion,
    [property: JsonRequired] string CoordinateSpace,
    [property: JsonRequired] JointAnimationNodeEdit[] Nodes);
public sealed record JointAnimationWrite(byte[] Bytes, JointAnimationNodeEdit[] Edits);

/// <summary>Replaces transform FOBJ tracks on existing JOBJ animation nodes.</summary>
public static class JointAnimationEditing
{
    private static readonly Dictionary<string, JointTrackType> Channels = new()
    {
        ["rotation.x"] = JointTrackType.HSD_A_J_ROTX,
        ["rotation.y"] = JointTrackType.HSD_A_J_ROTY,
        ["rotation.z"] = JointTrackType.HSD_A_J_ROTZ,
        ["translation.x"] = JointTrackType.HSD_A_J_TRAX,
        ["translation.y"] = JointTrackType.HSD_A_J_TRAY,
        ["translation.z"] = JointTrackType.HSD_A_J_TRAZ,
        ["scale.x"] = JointTrackType.HSD_A_J_SCAX,
        ["scale.y"] = JointTrackType.HSD_A_J_SCAY,
        ["scale.z"] = JointTrackType.HSD_A_J_SCAZ
    };

    public static JointAnimationWrite Write(ArchiveLayout source, ArchiveLayout current,
        ModelIdentitySnapshot identity, JointAnimationEdits edits, string[] declaredTargets)
    {
        Require(edits.ProtocolVersion == SessionExtractor.ProtocolVersion
            && edits.CoordinateSpace == "game-jobj-animation"
            && edits.Nodes is { Length: > 0 }, "ANIMATION_EDIT_FORMAT",
            "JOBJ animation edits require the current protocol, game-jobj-animation coordinates, and at least one node.");
        var declared = declaredTargets.ToHashSet(StringComparer.Ordinal);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var locations = new List<(JointAnimationNodeEdit Edit, int Aobj)>();
        var sourceStage = new StageArchive("source.dat", source.Bytes);
        foreach (var edit in edits.Nodes)
        {
            string key = TargetKey(edit.GroupIndex, edit.Slot, edit.JobjId);
            Require(edit != null && edit.JobjId != null && seen.Add(key) && declared.Contains(key),
                "ANIMATION_EDIT_TARGET", "JOBJ animation target is unsupported, duplicated, or absent from this session.");
            var sourceSet = StageJointAnimations.Read(sourceStage, identity, edit.GroupIndex)
                .SingleOrDefault(set => set.Slot == edit.Slot);
            var sourceNode = sourceSet?.Nodes.SingleOrDefault(node => node.JobjId == edit.JobjId);
            Require(sourceNode?.Editable == true, "ANIMATION_EDIT_TARGET",
                "JOBJ animation target is not editable in the source animation tree.");
            Validate(edit, sourceNode!.EndFrame);
            locations.Add((edit, LocateAobj(source, identity, edit.GroupIndex, edit.Slot, edit.JobjId)));
        }

        using var data = new MemoryStream();
        data.Write(current.Bytes.AsSpan(32, current.DataSize));
        int Append(byte[] value, int alignment = 4)
        {
            while (data.Position % alignment != 0) data.WriteByte(0);
            int offset = checked((int)data.Position);
            data.Write(value);
            return offset;
        }

        int oldRelocationCount = current.Read(8);
        var relocations = Enumerable.Range(0, oldRelocationCount)
            .Select(index => current.Read(32 + current.DataSize + index * 4)).ToHashSet();
        foreach (var (edit, aobj) in locations)
        {
            var encoded = edit.Tracks.Select(track =>
            {
                var keys = track.Keys.Select(key => new FOBJKey
                {
                    Frame = key.Frame, Value = key.Value, Tan = key.Tangent,
                    InterpolationType = Enum.Parse<GXInterpolationType>(key.Interpolation)
                }).ToList();
                var fobj = FOBJFrameEncoder.EncodeFrames(keys, Channels[track.Channel]);
                return (Track: track, Fobj: fobj, Buffer: Append(fobj.Buffer, 4));
            }).ToArray();
            int firstDescriptor = checked((int)((data.Position + 3) / 4 * 4));
            var descriptorOffsets = new int[encoded.Length];
            for (int i = 0; i < encoded.Length; i++)
            {
                byte[] descriptor = new byte[0x14];
                Put(descriptor, 4, encoded[i].Fobj.Buffer.Length);
                Float(descriptor, 8, 0);
                descriptor[0xC] = (byte)Channels[encoded[i].Track.Channel];
                descriptor[0xD] = Flag(encoded[i].Fobj.ValueFormat, encoded[i].Fobj.ValueScale);
                descriptor[0xE] = Flag(encoded[i].Fobj.TanFormat, encoded[i].Fobj.TanScale);
                Put(descriptor, 0x10, encoded[i].Buffer);
                descriptorOffsets[i] = Append(descriptor, 4);
                relocations.Add(descriptorOffsets[i] + 0x10);
            }
            Require(descriptorOffsets[0] == firstDescriptor, "ANIMATION_WRITE", "Animation descriptor layout changed unexpectedly.");
            byte[] payload = data.GetBuffer();
            for (int i = 0; i + 1 < descriptorOffsets.Length; i++)
            {
                Put(payload, descriptorOffsets[i], descriptorOffsets[i + 1]);
                relocations.Add(descriptorOffsets[i]);
            }
            Put(payload, aobj + 8, descriptorOffsets[0]);
            relocations.Add(aobj + 8);
        }

        byte[] dataBytes = data.ToArray();
        using var result = new MemoryStream();
        result.Write(current.Bytes.AsSpan(0, 32));
        result.Write(dataBytes);
        foreach (int field in relocations.Order()) Int(result, field);
        result.Write(current.Bytes.AsSpan(32 + current.DataSize + oldRelocationCount * 4));
        byte[] output = result.ToArray();
        Put(output, 0, output.Length);
        Put(output, 4, dataBytes.Length);
        Put(output, 8, relocations.Count);
        var parsed = new ArchiveLayout(output);
        Require(current.Roots.SequenceEqual(parsed.Roots) && current.References.SequenceEqual(parsed.References),
            "PRESERVATION_ROOTS", "Root inventory changed while writing JOBJ animation.");
        var write = new JointAnimationWrite(output, edits.Nodes);
        Verify(parsed, identity, write);
        return write;
    }

    public static void Verify(ArchiveLayout archive, ModelIdentitySnapshot identity,
        JointAnimationWrite expected)
    {
        var stage = new StageArchive("animation-output.dat", archive.Bytes);
        foreach (var edit in expected.Edits)
        {
            var actual = StageJointAnimations.Read(stage, identity, edit.GroupIndex)
                .Single(set => set.Slot == edit.Slot).Nodes.Single(node => node.JobjId == edit.JobjId);
            AssertTracks(edit.Tracks, actual.Tracks);
        }
    }

    public static string TargetKey(int groupIndex, int slot, string jobjId)
        => $"{groupIndex}:{slot}:{jobjId}";

    private static void Validate(JointAnimationNodeEdit edit, float endFrame)
    {
        Require(edit.Tracks is { Length: > 0 }, "ANIMATION_EDIT_TRACKS",
            "An edited JOBJ animation node must contain tracks.");
        var channels = new HashSet<string>(StringComparer.Ordinal);
        foreach (var track in edit.Tracks)
        {
            Require(track != null && track.Channel != null && Channels.ContainsKey(track.Channel)
                && channels.Add(track.Channel) && track.Keys is { Length: > 0 },
                "ANIMATION_EDIT_TRACKS", "Animation tracks must use unique supported transform channels and contain keys.");
            float previous = -1;
            foreach (var key in track.Keys)
            {
                Require(float.IsFinite(key.Frame) && float.IsFinite(key.Value) && float.IsFinite(key.Tangent)
                    && key.Frame >= 0 && key.Frame <= endFrame + 1e-4f
                    && MathF.Abs(key.Frame - MathF.Round(key.Frame)) < 1e-4f && key.Frame > previous,
                    "ANIMATION_EDIT_KEY", "Animation keys require finite values and strictly increasing whole-number frames within the source duration.");
                Require(key.Interpolation is "HSD_A_OP_CON" or "HSD_A_OP_LIN" or "HSD_A_OP_SPL0"
                    or "HSD_A_OP_SPL" or "HSD_A_OP_KEY", "ANIMATION_EDIT_INTERPOLATION",
                    "Animation key uses an unsupported interpolation mode.");
                previous = key.Frame;
            }
            Require(track.Keys[0].Frame == 0, "ANIMATION_EDIT_KEY",
                "Each replacement animation track must begin at source frame zero.");
        }
    }

    private static int LocateAobj(ArchiveLayout archive, ModelIdentitySnapshot identity,
        int groupIndex, int slot, string targetId)
    {
        var r = new ArchiveDataReader(archive);
        var nodes = identity.Nodes.Where(node => node.GroupIndex == groupIndex).ToArray();
        var group = nodes.Single(node => node.Kind is "group" or "sentinel-group");
        Require(group.Kind == "group", "ANIMATION_EDIT_TARGET", "Sentinel groups do not have editable animation.");
        var joints = nodes.Where(node => node.Kind.EndsWith("jobj")).OrderBy(node => node.Index).ToArray();
        int? array = r.Pointer(group.SourceOffset + 4);
        Require(array.HasValue, "ANIMATION_EDIT_TARGET", "Model group has no JOBJ animation array.");
        int? animation = r.Pointer(array.Value + slot * 4);
        int? model = r.Pointer(group.SourceOffset);
        int index = 0;
        int? found = null;
        Visit(model, animation);
        Require(found.HasValue, "ANIMATION_EDIT_TARGET", "JOBJ has no existing editable AOBJ in this animation slot.");
        return found.Value;

        void Visit(int? jobj, int? anim)
        {
            if (!jobj.HasValue) return;
            Require(index < joints.Length, "ANIMATION_TOPOLOGY", "JOBJ animation exceeds the model hierarchy.");
            var joint = joints[index++];
            if (joint.Id == targetId)
                found = anim.HasValue ? r.Pointer(anim.Value + 8) : null;
            if ((r.Int(jobj.Value + 4) & 0x1000) != 0) return;
            int? child = r.Pointer(jobj.Value + 8);
            int? childAnim = anim.HasValue ? r.Pointer(anim.Value) : null;
            while (child.HasValue)
            {
                Visit(child, childAnim);
                child = r.Pointer(child.Value + 12);
                childAnim = childAnim.HasValue ? r.Pointer(childAnim.Value + 4) : null;
            }
        }
    }

    private static void AssertTracks(JointAnimationTrack[] expected, JointAnimationTrack[] actual)
    {
        Require(expected.Select(track => track.Channel).Order().SequenceEqual(
            actual.Select(track => track.Channel).Order()), "ANIMATION_WRITE_MISMATCH",
            "Reloaded JOBJ animation track inventory differs from the edit.");
        foreach (var track in expected)
        {
            var written = actual.Single(item => item.Channel == track.Channel);
            Require(track.Keys.Length == written.Keys.Length && track.Keys.Zip(written.Keys).All(pair =>
                pair.First.Frame == pair.Second.Frame && MathF.Abs(pair.First.Value - pair.Second.Value) <= .002f),
                "ANIMATION_WRITE_MISMATCH", "Reloaded JOBJ animation keys differ from the edit.");
        }
    }

    private static byte Flag(GXAnimDataFormat format, uint scale)
    {
        Require(scale > 0 && BitOperations.IsPow2(scale), "ANIMATION_ENCODING", "FOBJ scale is invalid.");
        return (byte)((byte)format | BitOperations.Log2(scale));
    }
    private static void Put(byte[] bytes, int offset, int value) =>
        BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
    private static void Float(byte[] bytes, int offset, float value) =>
        Put(bytes, offset, BitConverter.SingleToInt32Bits(value));
    private static void Int(Stream stream, int value)
    {
        Span<byte> bytes = stackalloc byte[4];
        BinaryPrimitives.WriteInt32BigEndian(bytes, value);
        stream.Write(bytes);
    }
}
