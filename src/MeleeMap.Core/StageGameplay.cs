using System.Buffers.Binary;
using System.Text.Json.Serialization;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record GameplayPoint(string Id, int SetIndex, int EntryIndex,
    int JobjIndex, int TypeId, string Kind, int? Player, Vector3Data Position,
    bool Editable, string? ReadOnlyReason, int SourceOffset);
public sealed record GameplayBounds(string Id, string Kind, string FirstPointId,
    string SecondPointId, bool Editable, string? ReadOnlyReason);
public sealed record GameplayPointSet(int Index, GameplayPoint[] Points,
    GameplayBounds[] Bounds, bool ItemSpawnTopologyEditable,
    string? ItemSpawnTopologyReadOnlyReason,
    [property: JsonIgnore] int? RootSourceOffset);
public sealed record StageGameplay(GameplayPointSet[] Sets)
{
    [JsonIgnore]
    public GameplayPoint[] Points => Sets.SelectMany(set => set.Points).ToArray();
    [JsonIgnore]
    public GameplayPoint[] EditablePoints => Points.Where(point => point.Editable).ToArray();
    [JsonIgnore]
    public GameplayBounds[] EditableBounds => Sets.SelectMany(set => set.Bounds)
        .Where(bounds => bounds.Editable).ToArray();
}

public sealed record GameplayPointEdit([property: JsonRequired] string Id,
    [property: JsonRequired] Vector3Data Position);
public sealed record GameplayItemSpawnAddition([property: JsonRequired] string Id,
    [property: JsonRequired] int SetIndex, [property: JsonRequired] int TypeId,
    [property: JsonRequired] Vector3Data Position);
public sealed record GameplayEdits([property: JsonRequired] int ProtocolVersion,
    [property: JsonRequired] string CoordinateSpace,
    [property: JsonRequired] GameplayPointEdit[] Points,
    [property: JsonRequired] GameplayItemSpawnAddition[] Additions,
    [property: JsonRequired] string[] Deletions);
public sealed record GameplayExpectedPoint(int SetIndex, int TypeId,
    int SourceOffset, Vector3Data Position);
public sealed record GameplaySetWrite(int SetIndex, int DescriptorOffset,
    byte[] Entries);
public sealed record GameplayAddedJobjWrite(int Offset, int? NextOffset,
    Vector3Data Position);
public sealed record GameplayWrite(byte[] Bytes, GameplayExpectedPoint[] Points,
    GameplaySetWrite[] Sets, GameplayAddedJobjWrite[] AddedJobjs);

/// <summary>
/// Reads and edits the typed general-point JOBJs referenced by map_head. The
/// first writable subset deliberately requires an identity root and flat child
/// points, which makes each stored JOBJ translation an authoritative game-space
/// coordinate rather than an inferred world transform.
/// </summary>
public static class StageGameplayEditing
{
    private const float Epsilon = 1e-5f;
    private const int FirstItemSpawn = 0x7F;
    private const int LastItemSpawn = 0x93;
    private const int JobjInstance = 1 << 12;
    private const string MutationOwner = "gameplay";

    public static StageGameplay Read(ArchiveLayout archive)
    {
        var mapHead = archive.Roots.SingleOrDefault(root => root.Name == "map_head");
        if (mapHead == null) return new([]);
        var reader = new ArchiveDataReader(archive);
        int count = reader.Int(mapHead.Offset + 4);
        Require(count is >= 0 and <= 256, "GAMEPLAY_SET_COUNT",
            "General-point set count is outside the supported range.");
        int sets = reader.Array(mapHead.Offset, count, 12);
        var result = new List<GameplayPointSet>();
        for (int setIndex = 0; setIndex < count; setIndex++)
            result.Add(ReadSet(reader, sets + setIndex * 12, setIndex));
        var sharedRoots = result.Where(set => set.RootSourceOffset.HasValue)
            .GroupBy(set => set.RootSourceOffset!.Value)
            .Where(group => group.Count() > 1).Select(group => group.Key).ToHashSet();
        for (int index = 0; index < result.Count; index++)
            if (result[index].RootSourceOffset is int root && sharedRoots.Contains(root))
                result[index] = result[index] with
                {
                    ItemSpawnTopologyEditable = false,
                    ItemSpawnTopologyReadOnlyReason =
                        "This JOBJ root is shared by multiple general-point sets."
                };
        return new(result.ToArray());
    }

    public static GameplayWrite Write(ArchiveLayout source, ArchiveLayout current,
        GameplayEdits edits, string[] declaredIds)
    {
        Require(edits.ProtocolVersion == SessionExtractor.ProtocolVersion
            && edits.CoordinateSpace == "game" && edits.Points != null
            && edits.Additions != null && edits.Deletions != null
            && edits.Points.Length + edits.Additions.Length + edits.Deletions.Length > 0,
            "GAMEPLAY_EDIT_FORMAT",
            "Gameplay edits require the current protocol, game coordinates, and at least one operation.");
        var snapshot = Read(source);
        var eligible = snapshot.EditablePoints.ToDictionary(point => point.Id);
        var declared = declaredIds.ToHashSet(StringComparer.Ordinal);
        var moved = new HashSet<string>(StringComparer.Ordinal);
        foreach (var edit in edits.Points)
        {
            Require(edit != null && edit.Id != null && moved.Add(edit.Id)
                && declared.Contains(edit.Id) && eligible.ContainsKey(edit.Id),
                "GAMEPLAY_EDIT_TARGET",
                "Gameplay point is unsupported, duplicated, or absent from this session.");
            Require(Finite(edit!.Position), "GAMEPLAY_NONFINITE",
                "Gameplay point coordinates must be finite.");
        }

        var deleted = new HashSet<string>(StringComparer.Ordinal);
        foreach (string id in edits.Deletions)
        {
            Require(id != null && deleted.Add(id) && !moved.Contains(id)
                && declared.Contains(id) && eligible.TryGetValue(id, out var point)
                && point.Kind == "item-spawn"
                && snapshot.Sets[point.SetIndex].ItemSpawnTopologyEditable,
                "GAMEPLAY_DELETE_TARGET",
                "Only editable item spawns from topology-enabled sets can be deleted.");
        }

        var additionIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (var addition in edits.Additions)
        {
            Require(addition != null && addition.Id != null
                && Guid.TryParseExact(addition.Id, "N", out _)
                && additionIds.Add(addition.Id)
                && addition.SetIndex >= 0 && addition.SetIndex < snapshot.Sets.Length
                && snapshot.Sets[addition.SetIndex].ItemSpawnTopologyEditable
                && addition.TypeId is >= FirstItemSpawn and <= LastItemSpawn,
                "GAMEPLAY_ADDITION_TARGET",
                "Item-spawn additions require unique IDs, free item types, and a topology-enabled set.");
            Require(Finite(addition!.Position), "GAMEPLAY_NONFINITE",
                "Gameplay point coordinates must be finite.");
        }

        foreach (var set in snapshot.Sets)
        {
            var retainedTypes = set.Points.Where(point => point.Kind == "item-spawn"
                    && !deleted.Contains(point.Id)).Select(point => point.TypeId).ToHashSet();
            var additions = edits.Additions.Where(addition => addition.SetIndex == set.Index)
                .ToArray();
            Require(retainedTypes.Count + additions.Length <= LastItemSpawn - FirstItemSpawn + 1,
                "GAMEPLAY_ITEM_LIMIT", "A general-point set cannot exceed 21 item spawns.");
            int[] expectedTypes = Enumerable.Range(FirstItemSpawn,
                    LastItemSpawn - FirstItemSpawn + 1)
                .Where(type => !retainedTypes.Contains(type)).Take(additions.Length).ToArray();
            Require(additions.Select(addition => addition.TypeId).Order().SequenceEqual(expectedTypes),
                "GAMEPLAY_ADDITION_TYPE",
                "New item spawns must occupy the lowest unused item-spawn slots.");
        }

        var final = snapshot.Points.ToDictionary(point => point.Id,
            point => point.Position, StringComparer.Ordinal);
        foreach (var edit in edits.Points) final[edit.Id] = edit.Position;
        ValidateBounds(snapshot, final);

        var builder = new ArchiveMutationBuilder(current);
        foreach (var edit in edits.Points)
        {
            int offset = eligible[edit.Id].SourceOffset + 0x2C;
            builder.PermitSourcePatch(offset, 12, MutationOwner);
            builder.PatchBytes(offset, VectorBytes(edit.Position), MutationOwner);
        }

        var mapHead = source.Roots.Single(root => root.Name == "map_head");
        var reader = new ArchiveDataReader(source);
        var currentReader = new ArchiveDataReader(current);
        int setCount = reader.Int(mapHead.Offset + 4);
        int descriptors = reader.Array(mapHead.Offset, setCount, 12);
        var setWrites = new List<GameplaySetWrite>();
        var addedJobjs = new List<GameplayAddedJobjWrite>();
        var addedExpected = new List<GameplayExpectedPoint>();
        var deletedEntries = edits.Deletions.Select(id => eligible[id])
            .GroupBy(point => point.SetIndex)
            .ToDictionary(group => group.Key,
                group => group.Select(point => point.EntryIndex).ToHashSet());

        foreach (var set in snapshot.Sets)
        {
            var additions = edits.Additions.Where(addition => addition.SetIndex == set.Index)
                .OrderBy(addition => addition.TypeId).ToArray();
            deletedEntries.TryGetValue(set.Index, out var remove);
            remove ??= [];
            if (additions.Length == 0 && remove.Count == 0) continue;

            int descriptor = descriptors + set.Index * 12;
            int count = reader.Int(descriptor + 8);
            int entries = reader.Array(descriptor + 4, count, 4);
            var layout = ReadLayout(currentReader, descriptor);
            Require(layout.IdentityRoot && layout.RootOffset.HasValue,
                "GAMEPLAY_TOPOLOGY_ROOT",
                "Item-spawn topology edits require an identity general-point root.");
            var records = Enumerable.Range(0, count)
                .Where(index => !remove.Contains(index))
                .Select(index => source.Bytes.AsSpan(32 + entries + index * 4, 4).ToArray())
                .ToList();

            if (additions.Length > 0)
            {
                int firstIndex = layout.Nodes.Count;
                Require(firstIndex + additions.Length <= short.MaxValue,
                    "GAMEPLAY_POINT_INDEX", "Added item spawns exceed the JOBJ index range.");
                var offsets = new List<int>();
                foreach (var addition in additions)
                {
                    int offset = builder.AppendAligned(JobjBytes(addition.Position));
                    offsets.Add(offset);
                    byte[] record = new byte[4];
                    Put(record, 0, (short)(firstIndex + offsets.Count - 1));
                    Put(record, 2, (short)addition.TypeId);
                    records.Add(record);
                    addedExpected.Add(new(set.Index, addition.TypeId, offset,
                        addition.Position));
                }
                int link = layout.DirectChildren.Count == 0
                    ? layout.RootOffset.Value + 8 : layout.DirectChildren[^1] + 12;
                Require(builder.ReadInt32(link) == 0, "GAMEPLAY_JOBJ_LINK",
                    "The general-point child tail is no longer empty.");
                builder.PermitSourcePatch(link, 4, MutationOwner);
                builder.SetPointer(link, offsets[0], MutationOwner);
                for (int index = 0; index < offsets.Count; index++)
                {
                    int? next = index + 1 < offsets.Count ? offsets[index + 1] : null;
                    if (next.HasValue)
                        builder.SetPointer(offsets[index] + 12, next.Value, MutationOwner);
                    else
                        builder.ClearPointer(offsets[index] + 12, MutationOwner);
                    addedJobjs.Add(new(offsets[index], next, additions[index].Position));
                }
            }

            Require(records.Count <= 4096, "GAMEPLAY_POINT_COUNT",
                "General-point count is outside the supported range.");
            byte[] finalEntries = records.SelectMany(record => record).ToArray();
            builder.PermitSourcePatch(descriptor + 4, 8, MutationOwner);
            if (finalEntries.Length == 0)
                builder.ClearPointer(descriptor + 4, MutationOwner);
            else
            {
                int replacement = builder.AppendAligned(finalEntries, 4);
                builder.SetPointer(descriptor + 4, replacement, MutationOwner);
            }
            builder.PatchInt32(descriptor + 8, records.Count, MutationOwner);
            setWrites.Add(new(set.Index, descriptor, finalEntries));
        }

        var expectedPoints = snapshot.Points.Where(point => !deleted.Contains(point.Id))
            .Select(point => new GameplayExpectedPoint(point.SetIndex, point.TypeId,
                point.SourceOffset, final[point.Id])).Concat(addedExpected).ToArray();
        byte[] bytes = builder.Build();
        var result = new GameplayWrite(bytes, expectedPoints, setWrites.ToArray(),
            addedJobjs.ToArray());
        Verify(new ArchiveLayout(bytes), result);
        return result;
    }

    public static void Verify(ArchiveLayout archive, GameplayWrite expected)
    {
        var actual = Read(archive).Points;
        Require(actual.Length == expected.Points.Length,
            "GAMEPLAY_WRITE_MISMATCH",
            "Reloaded gameplay point count differs from the requested edit.");
        foreach (var point in expected.Points)
            Require(actual.Any(candidate => candidate.SetIndex == point.SetIndex
                && candidate.TypeId == point.TypeId
                && candidate.SourceOffset == point.SourceOffset
                && candidate.Position == point.Position), "GAMEPLAY_WRITE_MISMATCH",
                "Reloaded gameplay point differs from the requested edit.");

        var reader = new ArchiveDataReader(archive);
        foreach (var set in expected.Sets)
        {
            int count = reader.Int(set.DescriptorOffset + 8);
            int entries = reader.Array(set.DescriptorOffset + 4, count, 4);
            Require(count * 4 == set.Entries.Length
                && archive.Bytes.AsSpan(32 + entries, set.Entries.Length)
                    .SequenceEqual(set.Entries), "GAMEPLAY_WRITE_MISMATCH",
                "Reloaded general-point records differ from the requested topology.");
        }
        foreach (var jobj in expected.AddedJobjs)
            Require(reader.Int(jobj.Offset + 4) == 0
                && reader.Pointer(jobj.Offset + 8) == null
                && reader.Pointer(jobj.Offset + 12) == jobj.NextOffset
                && reader.Pointer(jobj.Offset + 16) == null
                && Enumerable.Range(0, 3).All(axis => reader.Float(
                    jobj.Offset + 0x14 + axis * 4) == 0)
                && Enumerable.Range(0, 3).All(axis => reader.Float(
                    jobj.Offset + 0x20 + axis * 4) == 1)
                && new Vector3Data(reader.Float(jobj.Offset + 0x2C),
                    reader.Float(jobj.Offset + 0x30),
                    reader.Float(jobj.Offset + 0x34)) == jobj.Position
                && reader.Pointer(jobj.Offset + 0x38) == null
                && reader.Pointer(jobj.Offset + 0x3C) == null,
                "GAMEPLAY_WRITE_MISMATCH",
                "An added item-spawn JOBJ differs from the requested topology.");
    }

    private static GameplayPointSet ReadSet(ArchiveDataReader reader, int descriptor,
        int setIndex)
    {
        int? root = reader.Pointer(descriptor);
        int count = reader.Int(descriptor + 8);
        Require(count is >= 0 and <= 4096, "GAMEPLAY_POINT_COUNT",
            "General-point count is outside the supported range.");
        int entries = reader.Array(descriptor + 4, count, 4);
        if (!root.HasValue)
        {
            Require(count == 0, "GAMEPLAY_POINT_ROOT",
                "A nonempty general-point set has no JOBJ root.");
            return new(setIndex, [], [], false,
                "This general-point set has no JOBJ root.", null);
        }

        var nodes = new List<(int Offset, int? Parent)>();
        var seen = new HashSet<int>();
        Walk(root.Value, null);
        bool identityRoot = IdentityTransform(nodes[0].Offset);
        var raw = new List<(int Entry, int Jobj, int Type, int Offset,
            string Kind, int? Player)>();
        for (int entryIndex = 0; entryIndex < count; entryIndex++)
        {
            int jobjIndex = reader.Short(entries + entryIndex * 4);
            int type = reader.Short(entries + entryIndex * 4 + 2);
            if (!Describe(type, out string? kind, out int? player)) continue;
            Require(jobjIndex >= 0 && jobjIndex < nodes.Count, "GAMEPLAY_POINT_INDEX",
                "General point references a JOBJ outside its hierarchy.");
            raw.Add((entryIndex, jobjIndex, type, nodes[jobjIndex].Offset, kind!, player));
        }
        var shared = raw.GroupBy(point => point.Offset)
            .Where(group => group.Count() > 1).Select(group => group.Key).ToHashSet();
        var points = raw.Select(point =>
        {
            string? reason = null;
            if (!identityRoot)
                reason = "The general-point root has a transform; world-space editing is not supported yet.";
            else if (nodes[point.Jobj].Parent != 0)
                reason = "This point uses a transformed JOBJ hierarchy; world-space editing is not supported yet.";
            else if (shared.Contains(point.Offset))
                reason = "Multiple gameplay records share this JOBJ translation.";
            return new GameplayPoint(PointId(setIndex, point.Entry), setIndex,
                point.Entry, point.Jobj, point.Type, point.Kind, point.Player,
                Vector(point.Offset + 0x2C), reason == null, reason, point.Offset);
        }).ToArray();

        var bounds = new List<GameplayBounds>();
        AddBounds("camera", "camera-boundary", 149, 150);
        AddBounds("blast", "blast-zone", 151, 152);
        var itemTypes = points.Where(point => point.Kind == "item-spawn")
            .Select(point => point.TypeId).ToArray();
        string? topologyReason = null;
        if (!identityRoot)
            topologyReason = "The general-point root has a transform; item-spawn topology editing is unavailable.";
        else if ((reader.Int(root.Value + 4) & JobjInstance) != 0)
            topologyReason = "An instanced general-point root cannot receive item-spawn children.";
        else if (itemTypes.Length != itemTypes.Distinct().Count())
            topologyReason = "This set contains duplicate item-spawn types.";
        else if (itemTypes.Length > LastItemSpawn - FirstItemSpawn + 1)
            topologyReason = "This set exceeds Melee's 21 item-spawn slots.";
        else if (nodes.Count >= short.MaxValue)
            topologyReason = "This set has no remaining signed JOBJ index capacity.";
        return new(setIndex, points, bounds.ToArray(), topologyReason == null,
            topologyReason, root.Value);

        void Walk(int offset, int? parent)
        {
            int? cursor = offset;
            while (cursor.HasValue)
            {
                Require(seen.Add(cursor.Value) && nodes.Count < 16384,
                    "GAMEPLAY_JOBJ_GRAPH", "General-point JOBJ hierarchy is cyclic or too large.");
                int index = nodes.Count;
                nodes.Add((cursor.Value, parent));
                int? child = reader.Pointer(cursor.Value + 8);
                if (child.HasValue && (reader.Int(cursor.Value + 4) & JobjInstance) == 0)
                    Walk(child.Value, index);
                cursor = reader.Pointer(cursor.Value + 12);
            }
        }

        bool IdentityTransform(int offset)
        {
            Vector3Data rotation = Vector(offset + 0x14);
            Vector3Data scale = Vector(offset + 0x20);
            Vector3Data translation = Vector(offset + 0x2C);
            return Near(rotation.X, 0) && Near(rotation.Y, 0) && Near(rotation.Z, 0)
                && Near(scale.X, 1) && Near(scale.Y, 1) && Near(scale.Z, 1)
                && Near(translation.X, 0) && Near(translation.Y, 0)
                && Near(translation.Z, 0);
        }

        Vector3Data Vector(int offset) => new(reader.Float(offset),
            reader.Float(offset + 4), reader.Float(offset + 8));

        void AddBounds(string suffix, string kind, int firstType, int secondType)
        {
            var first = points.Where(point => point.TypeId == firstType).ToArray();
            var second = points.Where(point => point.TypeId == secondType).ToArray();
            if (first.Length != 1 || second.Length != 1) return;
            string? reason = first[0].ReadOnlyReason ?? second[0].ReadOnlyReason;
            if (reason == null && (MathF.Abs(first[0].Position.X - second[0].Position.X) <= Epsilon
                || MathF.Abs(first[0].Position.Y - second[0].Position.Y) <= Epsilon))
                reason = "The source boundary has zero width or height.";
            bounds.Add(new($"gameplay-bounds-{setIndex:D3}-{suffix}", kind,
                first[0].Id, second[0].Id, reason == null, reason));
        }
    }

    private sealed record SetLayout(int? RootOffset,
        List<(int Offset, int? Parent)> Nodes, List<int> DirectChildren,
        bool IdentityRoot);

    private static SetLayout ReadLayout(ArchiveDataReader reader, int descriptor)
    {
        int? root = reader.Pointer(descriptor);
        if (!root.HasValue) return new(null, [], [], false);
        var nodes = new List<(int Offset, int? Parent)>();
        var seen = new HashSet<int>();
        Walk(root.Value, null);
        Vector3Data rotation = Vector(root.Value + 0x14);
        Vector3Data scale = Vector(root.Value + 0x20);
        Vector3Data translation = Vector(root.Value + 0x2C);
        bool identity = Near(rotation.X, 0) && Near(rotation.Y, 0)
            && Near(rotation.Z, 0) && Near(scale.X, 1) && Near(scale.Y, 1)
            && Near(scale.Z, 1) && Near(translation.X, 0)
            && Near(translation.Y, 0) && Near(translation.Z, 0);
        return new(root, nodes, nodes.Where(node => node.Parent == 0)
            .Select(node => node.Offset).ToList(), identity);

        void Walk(int offset, int? parent)
        {
            int? cursor = offset;
            while (cursor.HasValue)
            {
                Require(seen.Add(cursor.Value) && nodes.Count < 16384,
                    "GAMEPLAY_JOBJ_GRAPH",
                    "General-point JOBJ hierarchy is cyclic or too large.");
                int index = nodes.Count;
                nodes.Add((cursor.Value, parent));
                int? child = reader.Pointer(cursor.Value + 8);
                if (child.HasValue && (reader.Int(cursor.Value + 4) & JobjInstance) == 0)
                    Walk(child.Value, index);
                cursor = reader.Pointer(cursor.Value + 12);
            }
        }

        Vector3Data Vector(int offset) => new(reader.Float(offset),
            reader.Float(offset + 4), reader.Float(offset + 8));
    }

    private static void ValidateBounds(StageGameplay snapshot,
        IReadOnlyDictionary<string, Vector3Data> final)
    {
        foreach (var set in snapshot.Sets)
        {
            foreach (var bounds in set.Bounds.Where(bounds => bounds.Editable))
            {
                Vector3Data a = final[bounds.FirstPointId];
                Vector3Data b = final[bounds.SecondPointId];
                Require(MathF.Abs(a.X - b.X) > Epsilon
                    && MathF.Abs(a.Y - b.Y) > Epsilon, "GAMEPLAY_BOUNDS_SIZE",
                    "Camera and blast-zone rectangles must have nonzero width and height.");
                Vector3Data sourceA = set.Points.Single(point =>
                    point.Id == bounds.FirstPointId).Position;
                Vector3Data sourceB = set.Points.Single(point =>
                    point.Id == bounds.SecondPointId).Position;
                Require(MathF.Sign(a.X - b.X) == MathF.Sign(sourceA.X - sourceB.X)
                    && MathF.Sign(a.Y - b.Y) == MathF.Sign(sourceA.Y - sourceB.Y),
                    "GAMEPLAY_BOUNDS_ORDER",
                    "Boundary corners cannot cross or invert their source orientation.");
            }
            var camera = set.Bounds.SingleOrDefault(bounds => bounds.Kind == "camera-boundary");
            var blast = set.Bounds.SingleOrDefault(bounds => bounds.Kind == "blast-zone");
            if (camera is null || blast is null || !camera.Editable || !blast.Editable)
                continue;
            var source = set.Points.ToDictionary(point => point.Id,
                point => point.Position);
            if (Contains(source[blast.FirstPointId], source[blast.SecondPointId],
                    source[camera.FirstPointId], source[camera.SecondPointId]))
                Require(Contains(final[blast.FirstPointId], final[blast.SecondPointId],
                    final[camera.FirstPointId], final[camera.SecondPointId]),
                    "GAMEPLAY_BOUNDS_CONTAINMENT",
                    "The blast zone must contain the camera boundary for this stage.");
        }
    }

    private static bool Contains(Vector3Data outerA, Vector3Data outerB,
        Vector3Data innerA, Vector3Data innerB)
    {
        float outerLeft = MathF.Min(outerA.X, outerB.X);
        float outerRight = MathF.Max(outerA.X, outerB.X);
        float outerBottom = MathF.Min(outerA.Y, outerB.Y);
        float outerTop = MathF.Max(outerA.Y, outerB.Y);
        float innerLeft = MathF.Min(innerA.X, innerB.X);
        float innerRight = MathF.Max(innerA.X, innerB.X);
        float innerBottom = MathF.Min(innerA.Y, innerB.Y);
        float innerTop = MathF.Max(innerA.Y, innerB.Y);
        return outerLeft <= innerLeft + Epsilon && outerRight >= innerRight - Epsilon
            && outerBottom <= innerBottom + Epsilon && outerTop >= innerTop - Epsilon;
    }

    private static bool Describe(int type, out string? kind, out int? player)
    {
        player = null;
        if (type is >= 0 and <= 3)
        {
            kind = "player-spawn"; player = type + 1; return true;
        }
        if (type is >= 4 and <= 7)
        {
            kind = "player-respawn"; player = type - 3; return true;
        }
        if (type is >= 127 and <= 147)
        {
            kind = "item-spawn"; return true;
        }
        kind = type switch
        {
            149 or 150 => "camera-boundary",
            151 or 152 => "blast-zone",
            _ => null
        };
        return kind != null;
    }

    private static string PointId(int set, int entry) =>
        $"gameplay-point-{set:D3}-{entry:D3}";
    private static bool Finite(Vector3Data value) => float.IsFinite(value.X)
        && float.IsFinite(value.Y) && float.IsFinite(value.Z);
    private static bool Near(float left, float right) =>
        MathF.Abs(left - right) <= Epsilon;
    private static byte[] VectorBytes(Vector3Data value)
    {
        byte[] bytes = new byte[12];
        Put(bytes, 0, value.X);
        Put(bytes, 4, value.Y);
        Put(bytes, 8, value.Z);
        return bytes;
    }
    private static byte[] JobjBytes(Vector3Data position)
    {
        byte[] bytes = new byte[0x40];
        Put(bytes, 0x20, 1f);
        Put(bytes, 0x24, 1f);
        Put(bytes, 0x28, 1f);
        Put(bytes, 0x2C, position.X);
        Put(bytes, 0x30, position.Y);
        Put(bytes, 0x34, position.Z);
        return bytes;
    }
    private static void Put(byte[] bytes, int offset, short value) =>
        BinaryPrimitives.WriteInt16BigEndian(bytes.AsSpan(offset, 2), value);
    private static void Put(byte[] bytes, int offset, float value) =>
        BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4),
            BitConverter.SingleToInt32Bits(value));
}
