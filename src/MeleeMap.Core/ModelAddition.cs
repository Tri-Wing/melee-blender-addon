using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ModelAdditionTarget(string Id, int GroupIndex, int JobjIndex,
    string Name, string CoordinateSpace, bool HasExistingGeometry,
    bool InheritsStageTransform, bool InheritsStageVisibility, bool HiddenAtRest,
    bool ExistingMaterialAnimation);

/// <summary>
/// Selects structurally safe JOBJ tails. This is intentionally separate from
/// replacement-mesh eligibility: additions change DOBJ list topology and have a
/// different risk set.
/// </summary>
public static class ModelAddition
{
    public const int SchemaVersion = 1;

    private const int Hidden = 1 << 4;
    private const int Opaque = 1 << 18;
    private const int RootOpaque = 1 << 28;
    private const int UnsupportedTransformFlags = 0xE00 | 0x2000 | 0x8000
        | 0x20000 | 0x600000 | 0x800000 | 0x1000000 | 0x2000000;

    public static ModelAdditionTarget[] Select(StageArchive stage, ModelIdentitySnapshot identity)
        => Select(stage, identity, out _);

    public static ModelAdditionTarget[] Select(StageArchive stage, ModelIdentitySnapshot identity,
        out Dictionary<string, string> readOnlyReasons)
    {
        var archive = stage.Layout;
        var reader = new ArchiveDataReader(archive);
        var nodes = identity.Nodes;
        var byId = nodes.ToDictionary(node => node.Id);
        var result = new List<ModelAdditionTarget>();
        readOnlyReasons = [];

        foreach (var joint in nodes.Where(node => node.Kind.EndsWith("jobj")))
        {
            string? reason = StructuralReason(joint);

            if (reason != null)
            {
                readOnlyReasons[joint.Id] = reason;
                continue;
            }

            bool hasGeometry = nodes.Any(node => node.Kind == "dobj" && node.OwnerId == joint.Id);
            int flags = reader.Int(joint.SourceOffset + 4);
            var group = nodes.First(node => node.GroupIndex == joint.GroupIndex && node.Kind == "group");
            bool materialAnimation = HasDobjAnimation(PathFromGroup(joint, group).ToArray(), group.SourceOffset + 8);
            result.Add(new(joint.Id, joint.GroupIndex, joint.Index,
                $"Group {joint.GroupIndex:D3} / Joint {joint.Index:D3}", "game-joint-local",
                hasGeometry, true, true, (flags & Hidden) != 0, materialAnimation));
        }
        return result.ToArray();

        string? StructuralReason(ModelIdentityNode joint)
        {
            var group = nodes.First(node => node.GroupIndex == joint.GroupIndex
                && node.Kind is "group" or "sentinel-group");
            if (group.Kind != "group" || joint.Kind != "jobj")
                return "This is an instance, particle, spline, or sentinel JOBJ.";

            // An instance renders its target subtree a second time. Conservatively
            // reject the whole group until instance-aware attachment semantics exist.
            if (nodes.Any(node => node.GroupIndex == joint.GroupIndex && node.Kind == "instance-jobj"))
                return "This group contains JOBJ instances.";

            var path = PathFromGroup(joint, group).ToArray();
            for (int pathIndex = 0; pathIndex < path.Length; pathIndex++)
            {
                var pathJoint = path[pathIndex];
                int flags = reader.Int(pathJoint.SourceOffset + 4);
                int ownerReferences = archive.Pointers.Count(pointer => pointer.Value == pathJoint.SourceOffset);
                bool interiorAlias = HasInteriorReference(pathJoint.SourceOffset, 0x40);
                // Ancestors may be referenced by stage attachment records while
                // still owning one ordinary hierarchy. The selected JOBJ itself
                // must have exactly one incoming descriptor pointer.
                if ((pathIndex + 1 == path.Length && ownerReferences != 1) || interiorAlias)
                    return $"This attachment path has a shared or interior-aliased JOBJ descriptor (0x{pathJoint.SourceOffset:X}, references {ownerReferences}, interior alias {interiorAlias}).";
                if (HasReference(pathJoint.SourceOffset))
                    return "This attachment path uses a custom JOBJ class.";
                if ((flags & UnsupportedTransformFlags) != 0)
                    return "This attachment path uses a billboard, IK, quaternion, effector, or custom matrix mode.";
                if (HasReference(pathJoint.SourceOffset + 0x38) || HasReference(pathJoint.SourceOffset + 0x3C))
                    return "This attachment path uses an inverse-bind matrix or transform constraint.";
                if (Enumerable.Range(0, 3).Any(axis =>
                    MathF.Abs(reader.Float(pathJoint.SourceOffset + 0x20 + axis * 4)) <= 1e-6f))
                    return "This attachment path has a non-invertible scale.";
                if (pathIndex + 1 < path.Length && (flags & RootOpaque) == 0)
                    return "An attachment ancestor is not traversed in the opaque render pass.";
            }

            if (HasJointAnimation(group, path))
                return "This attachment path has joint or visibility animation.";
            if (HasDobjAnimation(path, group.SourceOffset + 12))
                return "This JOBJ has shape animation.";
            // HSD_DObjAddAnimAll advances DOBJ and animation lists in parallel.
            // An appended tail after the original DOBJ list therefore receives
            // no material animation, independent of the stage or archive hash.

            int jointFlags = reader.Int(joint.SourceOffset + 4);
            if ((jointFlags & Opaque) == 0)
                return "This JOBJ is not traversed in the opaque render pass.";

            int? head = reader.Pointer(joint.SourceOffset + 16);
            if (!head.HasValue)
                return null;
            if (archive.Pointers.Count(pointer => pointer.Value == head.Value) != 1)
                return "This JOBJ has a shared DOBJ list.";

            var dobjs = nodes.Where(node => node.Kind == "dobj" && node.OwnerId == joint.Id)
                .OrderBy(node => node.Index).ToArray();
            if (dobjs.Length == 0 || dobjs[0].SourceOffset != head.Value)
                return "This JOBJ's DOBJ list does not match its protected identity graph.";
            for (int index = 0; index < dobjs.Length; index++)
            {
                var dobj = dobjs[index];
                if (HasReference(dobj.SourceOffset))
                    return "This attachment has a custom DOBJ class.";
                if (HasInteriorReference(dobj.SourceOffset, 0x10))
                    return "This attachment has an interior-aliased DOBJ descriptor.";
                if (archive.Pointers.Count(pointer => pointer.Value == dobj.SourceOffset) != 1)
                    return "This attachment has a shared DOBJ descriptor.";
                int? next = reader.Pointer(dobj.SourceOffset + 4);
                int? expected = index + 1 < dobjs.Length ? dobjs[index + 1].SourceOffset : null;
                if (next != expected)
                    return "This attachment's DOBJ list order is not ordinary.";
            }
            return null;
        }

        IEnumerable<ModelIdentityNode> PathFromGroup(ModelIdentityNode joint, ModelIdentityNode group)
        {
            var path = new Stack<ModelIdentityNode>();
            for (var cursor = joint; cursor.Id != group.Id; cursor = byId[cursor.OwnerId!])
                path.Push(cursor);
            return path;
        }

        bool HasJointAnimation(ModelIdentityNode group, ModelIdentityNode[] path)
        {
            int? array = reader.Pointer(group.SourceOffset + 4);
            if (!array.HasValue) return false;
            for (int field = array.Value; ; field += 4)
            {
                int? root = reader.Pointer(field);
                if (!root.HasValue) return false;
                for (int depth = 0; depth < path.Length; depth++)
                {
                    int? node = ResolveAnimationNode(root, path, depth);
                    if (node.HasValue && (reader.Pointer(node.Value + 8) != null
                        || HasReference(node.Value + 12) || reader.Int(node.Value + 16) != 0))
                        return true;
                }
            }
        }

        bool HasDobjAnimation(ModelIdentityNode[] path, int arrayField)
        {
            int? array = reader.Pointer(arrayField);
            if (!array.HasValue) return false;
            for (int field = array.Value; ; field += 4)
            {
                int? root = reader.Pointer(field);
                if (!root.HasValue) return false;
                int? node = ResolveAnimationNode(root, path, path.Length - 1);
                if (node.HasValue && reader.Pointer(node.Value + 8) != null)
                    return true;
            }
        }

        int? ResolveAnimationNode(int? root, ModelIdentityNode[] path, int targetDepth)
        {
            int? animation = root;
            for (int depth = 0; depth <= targetDepth && animation.HasValue; depth++)
            {
                if (depth > 0) animation = reader.Pointer(animation.Value);
                int sibling = nodes.Where(node => node.OwnerId == path[depth].OwnerId
                        && node.Kind.EndsWith("jobj"))
                    .OrderBy(node => node.Index).TakeWhile(node => node.Id != path[depth].Id).Count();
                for (int index = 0; index < sibling && animation.HasValue; index++)
                    animation = reader.Pointer(animation.Value + 4);
            }
            return animation;
        }

        bool HasReference(int field) => archive.Pointers.ContainsKey(field) || reader.Int(field) != 0;
        bool HasInteriorReference(int start, int size) => archive.Pointers.Values.Any(offset => offset > start && offset < start + size)
            || archive.Roots.Concat(archive.References).Any(root => root.Offset >= start && root.Offset < start + size);
    }
}
