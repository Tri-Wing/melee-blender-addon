using System.Buffers.Binary;
using System.Text.Json.Serialization;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record JobjTransformEdit([property: JsonRequired] string Id,
    [property: JsonRequired] Vector3Data Rotation, [property: JsonRequired] Vector3Data Scale,
    [property: JsonRequired] Vector3Data Translation);
public sealed record JobjTransformEdits([property: JsonRequired] int ProtocolVersion,
    [property: JsonRequired] string CoordinateSpace,
    [property: JsonRequired] JobjTransformEdit[] Jobjs);
public sealed record EditableJobj(string Id, int GroupIndex, int JobjIndex, int SourceOffset);
public sealed record JobjTransformWrite(byte[] Bytes, JobjTransformEdit[] Edits);

/// <summary>Reads and writes base SRT fields for ordinary, unanimated HSD JOBJs.</summary>
public static class JobjEditing
{
    private const int UnsupportedFlags = 0xE00 | 0x2000 | 0x1000 | 0x20000
        | 0x600000 | 0x800000 | 0x1000000 | 0x2000000;

    public static EditableJobj[] Select(ArchiveLayout archive, ModelIdentitySnapshot identity)
        => Select(archive, identity, out _);

    public static EditableJobj[] Select(ArchiveLayout archive, ModelIdentitySnapshot identity,
        out Dictionary<string, string> readOnlyReasons)
    {
        var r = new ArchiveDataReader(archive);
        var nodes = identity.Nodes;
        var byId = nodes.ToDictionary(node => node.Id);
        var attachedGroups = CollisionData.Read(archive).Attachments.Select(a => a.GroupIndex).ToHashSet();
        var instanceGroups = nodes.Where(node => node.Kind == "instance-jobj")
            .Select(node => node.GroupIndex).ToHashSet();
        var result = new List<EditableJobj>();
        readOnlyReasons = [];

        foreach (var joint in nodes.Where(node => node.Kind.EndsWith("jobj")))
        {
            string? reason = null;
            var group = nodes.First(node => node.GroupIndex == joint.GroupIndex
                && node.Kind is "group" or "sentinel-group");
            if (group.Kind != "group" || joint.Kind != "jobj")
                reason = "This is an instance, particle, spline, or sentinel JOBJ.";
            else if (attachedGroups.Contains(joint.GroupIndex))
                reason = "This group has collision attached to its JOBJ hierarchy.";
            else if (instanceGroups.Contains(joint.GroupIndex))
                reason = "This group contains JOBJ instances.";
            else
            {
                int flags = r.Int(joint.SourceOffset + 4);
                if (HasReference(joint.SourceOffset))
                    reason = "This JOBJ uses a custom class.";
                else if ((flags & UnsupportedFlags) != 0)
                    reason = "This JOBJ uses a billboard, IK, quaternion, or custom matrix mode.";
                else if (HasReference(joint.SourceOffset + 0x38))
                    reason = "This JOBJ has an inverse-bind matrix.";
                else if (HasReference(joint.SourceOffset + 0x3C))
                    reason = "This JOBJ has transform constraints.";
                else if (HasTransformAnimation(group, joint))
                    reason = "This JOBJ has transform animation.";
                else if (Enumerable.Range(0, 3).Any(i => MathF.Abs(r.Float(joint.SourceOffset + 0x20 + i * 4)) <= 1e-6f))
                    reason = "This JOBJ has a zero scale component.";
            }
            if (reason == null)
                result.Add(new(joint.Id, joint.GroupIndex, joint.Index, joint.SourceOffset));
            else readOnlyReasons[joint.Id] = reason;
        }
        return result.ToArray();

        bool HasTransformAnimation(ModelIdentityNode group, ModelIdentityNode target)
        {
            int? array = Pointer(group.SourceOffset + 4, out bool externalArray);
            if (externalArray) return true;
            if (!array.HasValue) return false;
            var path = new List<ModelIdentityNode>();
            for (var cursor = target; cursor.Id != group.Id; cursor = byId[cursor.OwnerId!])
                path.Add(cursor);
            path.Reverse();
            for (int field = array.Value; ; field += 4)
            {
                int? animation = Pointer(field, out bool externalAnimation);
                if (externalAnimation) return true;
                if (!animation.HasValue) return false;
                for (int depth = 0; depth < path.Count && animation.HasValue; depth++)
                {
                    var node = path[depth];
                    if (depth > 0)
                    {
                        animation = Pointer(animation.Value, out bool externalChild);
                        if (externalChild) return true;
                    }
                    int sibling = nodes.Where(n => n.OwnerId == node.OwnerId && n.Kind.EndsWith("jobj"))
                        .OrderBy(n => n.Index).TakeWhile(n => n.Id != node.Id).Count();
                    for (int i = 0; i < sibling && animation.HasValue; i++)
                    {
                        animation = Pointer(animation.Value + 4, out bool externalNext);
                        if (externalNext) return true;
                    }
                }
                if (animation.HasValue && HasReference(animation.Value + 8)) return true;
            }
        }

        bool HasReference(int field) => archive.Pointers.ContainsKey(field) || r.Int(field) != 0;
        int? Pointer(int field, out bool external)
        {
            if (archive.Pointers.TryGetValue(field, out int target))
            {
                external = false;
                return target;
            }
            external = r.Int(field) != 0;
            return null;
        }
    }

    public static JobjTransformWrite Write(ArchiveLayout source, ArchiveLayout current,
        ModelIdentitySnapshot identity, JobjTransformEdits edits, string[] declaredIds)
    {
        Require(edits.ProtocolVersion == SessionExtractor.ProtocolVersion
            && edits.CoordinateSpace == "game-jobj-local" && edits.Jobjs is { Length: > 0 },
            "JOBJ_EDIT_FORMAT", "JOBJ edits require the current protocol, game-jobj-local coordinates, and at least one transform.");
        var eligible = Select(source, identity).ToDictionary(target => target.Id);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var edit in edits.Jobjs)
        {
            Require(edit != null && edit.Id != null && seen.Add(edit.Id)
                && declaredIds.Contains(edit.Id) && eligible.ContainsKey(edit.Id),
                "JOBJ_EDIT_TARGET", "JOBJ transform is unsupported, duplicated, or absent from this session.");
            Require(Finite(edit!.Rotation) && Finite(edit.Scale) && Finite(edit.Translation),
                "JOBJ_NONFINITE", "JOBJ rotation, scale, and translation must be finite.");
            Require(MathF.Abs(edit.Scale.X) > 1e-6f && MathF.Abs(edit.Scale.Y) > 1e-6f
                && MathF.Abs(edit.Scale.Z) > 1e-6f, "JOBJ_ZERO_SCALE",
                "JOBJ scale cannot be zero because descendant transforms would be undefined.");
        }

        byte[] bytes = current.Bytes.ToArray();
        foreach (var edit in edits.Jobjs)
        {
            int offset = eligible[edit.Id].SourceOffset;
            Put(bytes, 32 + offset + 0x14, edit.Rotation.X);
            Put(bytes, 32 + offset + 0x18, edit.Rotation.Y);
            Put(bytes, 32 + offset + 0x1C, edit.Rotation.Z);
            Put(bytes, 32 + offset + 0x20, edit.Scale.X);
            Put(bytes, 32 + offset + 0x24, edit.Scale.Y);
            Put(bytes, 32 + offset + 0x28, edit.Scale.Z);
            Put(bytes, 32 + offset + 0x2C, edit.Translation.X);
            Put(bytes, 32 + offset + 0x30, edit.Translation.Y);
            Put(bytes, 32 + offset + 0x34, edit.Translation.Z);
        }
        var result = new JobjTransformWrite(bytes, edits.Jobjs);
        Verify(new ArchiveLayout(bytes), identity, result);
        return result;
    }

    public static void Verify(ArchiveLayout archive, ModelIdentitySnapshot identity, JobjTransformWrite expected)
    {
        var eligible = Select(archive, identity).ToDictionary(target => target.Id);
        var r = new ArchiveDataReader(archive);
        foreach (var edit in expected.Edits)
        {
            Require(eligible.TryGetValue(edit.Id, out var target), "JOBJ_WRITE_MISMATCH",
                "Edited JOBJ disappeared or became unsupported after export.");
            int offset = target.SourceOffset;
            Require(Read(offset + 0x14) == edit.Rotation && Read(offset + 0x20) == edit.Scale
                && Read(offset + 0x2C) == edit.Translation, "JOBJ_WRITE_MISMATCH",
                "Reloaded JOBJ transform differs from the edit.");
        }
        Vector3Data Read(int offset) => new(r.Float(offset), r.Float(offset + 4), r.Float(offset + 8));
    }

    private static bool Finite(Vector3Data value) => float.IsFinite(value.X)
        && float.IsFinite(value.Y) && float.IsFinite(value.Z);
    private static void Put(byte[] bytes, int offset, float value) =>
        BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), BitConverter.SingleToInt32Bits(value));
}
