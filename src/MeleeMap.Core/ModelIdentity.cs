using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ModelLocator(string Kind, int Offset);
public sealed record ModelIdentityNode(string Id, string Kind, int GroupIndex, int Index,
    string? OwnerId, string? InstanceTargetId, int SourceOffset);

/// <summary>Session identity map. IDs are opaque; offsets only locate source descriptors.
/// Reuse this catalog when checking edits; a relocated archive requires an explicit locator mapping.</summary>
public sealed class ModelIdentityCatalog
{
    private readonly Dictionary<ModelLocator, string> ids = [];
    public string GetId(string kind, int offset)
    {
        var key = new ModelLocator(kind, offset);
        if (!ids.TryGetValue(key, out var id)) ids.Add(key, id = Guid.NewGuid().ToString("N"));
        return id;
    }

    public static ModelIdentityCatalog Restore(IEnumerable<ModelIdentityNode> nodes)
    {
        var result = new ModelIdentityCatalog();
        var byId = new Dictionary<string, ModelLocator>();
        foreach (var node in nodes)
        {
            string kind = node.Kind.EndsWith("jobj") ? "jobj" : node.Kind is "group" or "sentinel-group" ? "group" : node.Kind;
            var locator = new ModelLocator(kind, node.SourceOffset);
            Require(Guid.TryParseExact(node.Id, "N", out _), "MODEL_IDENTITY_MAPPING", "Invalid persisted model ID.");
            Require(!result.ids.TryGetValue(locator, out var old) || old == node.Id, "MODEL_IDENTITY_MAPPING", "A source descriptor has conflicting IDs.");
            Require(!byId.TryGetValue(node.Id, out var previous) || previous == locator, "MODEL_IDENTITY_MAPPING", "A persisted ID refers to multiple descriptors.");
            result.ids[locator] = node.Id; byId[node.Id] = locator;
        }
        return result;
    }

    public ModelIdentityCatalog Relocate(Func<ModelLocator, int> offsetMapping)
    {
        var result = new ModelIdentityCatalog();
        foreach (var (locator, id) in ids)
            Require(result.ids.TryAdd(locator with { Offset = offsetMapping(locator) }, id),
                "MODEL_IDENTITY_MAPPING", "Relocation maps multiple identities to one descriptor.");
        return result;
    }
}

public sealed class ModelIdentitySnapshot
{
    public IReadOnlyList<ModelIdentityNode> Nodes { get; }
    internal ModelIdentitySnapshot(List<ModelIdentityNode> nodes) => Nodes = nodes.AsReadOnly();

    internal ModelIdentitySnapshot WithoutSourceOffsets(IReadOnlySet<int> offsets) =>
        offsets.Count == 0 ? this : new(Nodes.Where(node =>
            !offsets.Contains(node.SourceOffset)).ToList());

    public ModelIdentitySnapshot WithoutPobjs(IEnumerable<string> ids)
    {
        var removed = ids.ToHashSet(StringComparer.Ordinal);
        Require(Nodes.Where(node => removed.Contains(node.Id)).All(node => node.Kind == "pobj")
            && removed.All(id => Nodes.Any(node => node.Id == id)),
            "MODEL_DELETE_TARGET", "Only declared POBJ models can be deleted.");
        var remaining = Nodes.Where(node => !removed.Contains(node.Id)).ToList();
        var pobjIndexes = remaining.Where(node => node.Kind == "pobj")
            .GroupBy(node => node.OwnerId)
            .SelectMany(group => group.OrderBy(node => node.Index)
                .Select((node, index) => (node.Id, index)))
            .ToDictionary(item => item.Id, item => item.index, StringComparer.Ordinal);
        return new(remaining.Select(node => node.Kind == "pobj"
            ? node with { Index = pobjIndexes[node.Id] } : node).ToList());
    }

    public void RequireUnchanged(ModelIdentitySnapshot edited)
    {
        Require(Nodes.Count == edited.Nodes.Count, "MODEL_IDENTITY_CHANGED", "Protected model object count changed.");
        for (int i = 0; i < Nodes.Count; i++)
        {
            var before = Nodes[i];
            var after = edited.Nodes[i];
            Require(before with { SourceOffset = 0 } == after with { SourceOffset = 0 },
                "MODEL_IDENTITY_CHANGED", $"Protected {before.Kind} identity/order/ownership changed in group {before.GroupIndex}, index {before.Index} (ID {before.Id}).");
        }
    }
}

/// <summary>Walk serialized descriptors directly: HSDRaw fragments may be split by interior pointers.</summary>
public static class ModelIdentity
{
    private const int Instance = 1 << 12, Particle = 1 << 5, Spline = 1 << 14;

    public static ModelIdentitySnapshot Capture(ArchiveLayout archive, ModelIdentityCatalog catalog)
    {
        var nodes = new List<ModelIdentityNode>();
        var root = archive.Roots.SingleOrDefault(r => r.Name == "map_head");
        Require(root != null, "STAGE_ROOT_MISSING", "Required stage root 'map_head' is missing.");
        Span(root!.Offset, 0x30, "map_head");
        int count = Int(root.Offset + 12);
        int? groups = Pointer(root.Offset + 8);
        Require(count >= 0 && (count == 0 || groups.HasValue && (long)groups.Value + 0x34L * count <= archive.DataSize),
            "MODEL_GROUP_COUNT", "Model group count exceeds the archive data section.");
        for (int group = 0; group < count; group++)
        {
            int descriptor = groups!.Value + group * 0x34;
            string groupId = catalog.GetId("group", descriptor);
            // Pokémon Stadium archives retain group slots with a literal -1 root marker.
            bool sentinel = !archive.Pointers.ContainsKey(descriptor) && Int(descriptor) == -1;
            nodes.Add(new(groupId, sentinel ? "sentinel-group" : "group", group, group, null, null, descriptor));
            if (sentinel) continue;
            var pending = new Stack<(int Offset, string Owner)>();
            var seen = new HashSet<int>();
            var instances = new List<int>();
            int? first = Pointer(descriptor);
            if (first.HasValue) pending.Push((first.Value, groupId));
            int traversal = 0;
            while (pending.TryPop(out var entry))
            {
                int joint = entry.Offset;
                Require(seen.Add(joint), "MODEL_JOBJ_CYCLE", $"Group {group}: repeated/cyclic JOBJ at 0x{joint:X}.");
                Span(joint, 0x40, "JOBJ");
                int flags = Int(joint + 4);
                for (int field = 0x14; field <= 0x34; field += 4)
                    Require(float.IsFinite(BitConverter.Int32BitsToSingle(Int(joint + field))),
                        "MODEL_NONFINITE", $"Group {group}, JOBJ {traversal}: non-finite transform at +0x{field:X}.");
                string id = catalog.GetId("jobj", joint);
                int? child = Pointer(joint + 8), next = Pointer(joint + 12);
                bool instance = (flags & Instance) != 0;
                Require(!instance || child.HasValue, "MODEL_INSTANCE_TARGET", $"Group {group}, JOBJ {traversal}: instance has no target.");
                nodes.Add(new(id, instance ? "instance-jobj" : (flags & Particle) != 0 ? "particle-jobj" : (flags & Spline) != 0 ? "spline-jobj" : "jobj",
                    group, traversal++, entry.Owner, instance && child.HasValue ? catalog.GetId("jobj", child.Value) : null, joint));
                // Preorder: child before next; instance child is a reference, not an owned subtree.
                if (next.HasValue) pending.Push((next.Value, entry.Owner));
                if (child.HasValue)
                {
                    if (instance) { Span(child.Value, 0x40, "instance JOBJ"); instances.Add(child.Value); }
                    else pending.Push((child.Value, id));
                }
                int? payload = Pointer(joint + 16);
                if ((flags & (Particle | Spline)) == 0)
                    ReadList(payload, "dobj", 0x10, id, group);
            }
            foreach (int target in instances)
                Require(seen.Contains(target), "MODEL_INSTANCE_TARGET", $"Group {group}: instance target 0x{target:X} is outside its owned hierarchy.");
        }
        return new(nodes);

        int Int(int offset) => archive.Read(32 + offset);
        void Span(int offset, int size, string kind) => Require(offset >= 0 && (long)offset + size <= archive.DataSize,
            "MODEL_STRUCTURE", $"{kind} at 0x{offset:X} is truncated.");
        int? Pointer(int field)
        {
            Span(field, 4, "pointer field");
            if (archive.Pointers.TryGetValue(field, out int target)) return target;
            Require(Int(field) == 0, "MODEL_POINTER", $"Non-null model pointer at 0x{field:X} is missing relocation.");
            return null;
        }
        void ReadList(int? offset, string kind, int size, string owner, int group)
        {
            var visited = new HashSet<int>();
            int index = 0;
            while (offset.HasValue)
            {
                int current = offset.Value;
                Require(visited.Add(current), "MODEL_LIST_CYCLE", $"Group {group}: cyclic {kind} list at 0x{current:X}.");
                Span(current, size, kind);
                string id = catalog.GetId(kind, current);
                nodes.Add(new(id, kind, group, index++, owner, null, current));
                if (kind == "dobj") ReadList(Pointer(current + 12), "pobj", 0x18, id, group);
                offset = Pointer(current + 4);
            }
        }
    }
}
