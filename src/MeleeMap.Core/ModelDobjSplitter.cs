using System.Buffers.Binary;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ModelDobjSplitWrite(byte[] Bytes,
    IReadOnlyDictionary<string, int> DobjOffsets, int[] PatchedSourceFields);

/// <summary>
/// Gives selected POBJs independent material ownership by moving them from a
/// shared POBJ list into ordinary, appended one-POBJ DOBJ descriptors.
/// </summary>
public static class ModelDobjSplitter
{
    private sealed record Pending(string PobjId, string JobjId, int PobjOffset,
        int OriginalDobjOffset, int NewDobjOffset);

    public static ModelDobjSplitWrite Write(ArchiveLayout source,
        ModelIdentitySnapshot identity, IEnumerable<string> requestedIds)
    {
        var requested = requestedIds.ToHashSet(StringComparer.Ordinal);
        var byId = identity.Nodes.ToDictionary(node => node.Id);
        Require(requested.All(id => byId.TryGetValue(id, out var node) && node.Kind == "pobj"),
            "MODEL_DOBJ_SPLIT_TARGET", "Only declared POBJ models can receive an independent DOBJ.");

        var reader = new ArchiveDataReader(source);
        using var data = new MemoryStream();
        data.Write(source.Bytes.AsSpan(32, source.DataSize));
        var relocations = Enumerable.Range(0, source.Read(8))
            .Select(index => source.Read(32 + source.DataSize + index * 4)).ToHashSet();
        var pending = new List<Pending>();
        var patched = new HashSet<int>();

        int Append(byte[] value)
        {
            while (data.Position % 32 != 0) data.WriteByte(0);
            int offset = checked((int)data.Position);
            data.Write(value);
            return offset;
        }

        var shared = identity.Nodes.Where(node => node.Kind == "pobj" && requested.Contains(node.Id))
            .GroupBy(node => node.OwnerId!).OrderBy(group => byId[group.Key].SourceOffset);
        foreach (var group in shared)
        {
            var dobj = byId[group.Key];
            var siblings = identity.Nodes.Where(node => node.Kind == "pobj" && node.OwnerId == dobj.Id)
                .OrderBy(node => node.Index).ToArray();
            var detached = siblings.Where(node => requested.Contains(node.Id)).ToList();
            if (siblings.Length < 2 || detached.Count == 0) continue;
            // A DOBJ must retain a POBJ. If every sibling needs independence,
            // the first one can keep the existing DOBJ while the rest move.
            if (detached.Count == siblings.Length) detached.Remove(siblings[0]);
            if (detached.Count == 0) continue;

            int material = reader.Pointer(dobj.SourceOffset + 8)
                ?? throw new StageException("MODEL_DOBJ_SPLIT_MATERIAL",
                    "A shared DOBJ has no material to preserve while splitting it.");
            string jobjId = dobj.OwnerId!;
            foreach (var pobj in detached)
            {
                byte[] descriptor = new byte[0x10];
                Put(descriptor, 8, material);
                Put(descriptor, 0x0C, pobj.SourceOffset);
                int offset = Append(descriptor);
                relocations.Add(offset + 8);
                relocations.Add(offset + 0x0C);
                pending.Add(new(pobj.Id, jobjId, pobj.SourceOffset, dobj.SourceOffset, offset));
            }
        }

        byte[] payload = data.ToArray();
        void Pointer(int field, int? target)
        {
            Put(payload, field, target ?? 0);
            if (target.HasValue) relocations.Add(field);
            else relocations.Remove(field);
            if (field < source.DataSize) patched.Add(field);
        }

        foreach (var owner in pending.GroupBy(item => item.OriginalDobjOffset))
        {
            var dobj = identity.Nodes.Single(node => node.Kind == "dobj"
                && node.SourceOffset == owner.Key);
            var detached = owner.Select(item => item.PobjId).ToHashSet(StringComparer.Ordinal);
            var remaining = identity.Nodes.Where(node => node.Kind == "pobj" && node.OwnerId == dobj.Id
                    && !detached.Contains(node.Id)).OrderBy(node => node.Index).ToArray();
            Require(remaining.Length > 0, "MODEL_DOBJ_SPLIT_EMPTY",
                "DOBJ splitting cannot leave the original descriptor empty.");
            Pointer(dobj.SourceOffset + 0x0C, remaining[0].SourceOffset);
            for (int index = 0; index < remaining.Length; index++)
                Pointer(remaining[index].SourceOffset + 4,
                    index + 1 < remaining.Length ? remaining[index + 1].SourceOffset : null);
            foreach (var item in owner)
                Pointer(item.PobjOffset + 4, null);
        }

        foreach (var owner in pending.GroupBy(item => item.JobjId))
        {
            var appended = owner.ToArray();
            var existing = identity.Nodes.Where(node => node.Kind == "dobj" && node.OwnerId == owner.Key)
                .OrderBy(node => node.Index).ToArray();
            var jobj = byId[owner.Key];
            int tailField = existing.Length == 0 ? jobj.SourceOffset + 0x10
                : existing[^1].SourceOffset + 4;
            Require(reader.Pointer(tailField) == null, "MODEL_DOBJ_SPLIT_TAIL",
                "The owning JOBJ's DOBJ tail changed before it could be extended.");
            Pointer(tailField, appended[0].NewDobjOffset);
            for (int index = 0; index < appended.Length; index++)
                Pointer(appended[index].NewDobjOffset + 4,
                    index + 1 < appended.Length ? appended[index + 1].NewDobjOffset : null);
        }

        using var result = new MemoryStream();
        result.Write(source.Bytes.AsSpan(0, 32));
        result.Write(payload);
        foreach (int field in relocations.Order()) Int(result, field);
        int oldRelocations = source.Read(8);
        result.Write(source.Bytes.AsSpan(32 + source.DataSize + oldRelocations * 4));
        byte[] output = result.ToArray();
        Put(output, 0, output.Length);
        Put(output, 4, payload.Length);
        Put(output, 8, relocations.Count);

        var archive = new ArchiveLayout(output);
        Require(source.Roots.SequenceEqual(archive.Roots)
            && source.References.SequenceEqual(archive.References),
            "MODEL_DOBJ_SPLIT_ROOTS", "Root or external-reference inventory changed while splitting a DOBJ.");
        for (int offset = 0; offset < source.DataSize; offset++)
            Require(patched.Any(field => offset >= field && offset < field + 4)
                || source.Bytes[32 + offset] == output[32 + offset],
                "MODEL_DOBJ_SPLIT_PRESERVATION", "Unrelated model/archive data changed while splitting a DOBJ.");
        var offsets = pending.ToDictionary(item => item.PobjId,
            item => item.NewDobjOffset, StringComparer.Ordinal);
        foreach (var item in pending)
            Require(new ArchiveDataReader(archive).Pointer(item.NewDobjOffset + 0x0C) == item.PobjOffset
                && new ArchiveDataReader(archive).Pointer(item.PobjOffset + 4) == null,
                "MODEL_DOBJ_SPLIT_WRITE", "A split POBJ was not isolated in its new DOBJ.");
        return new(output, offsets, patched.Order().ToArray());
    }

    private static void Int(Stream stream, int value)
    {
        Span<byte> bytes = stackalloc byte[4];
        BinaryPrimitives.WriteInt32BigEndian(bytes, value);
        stream.Write(bytes);
    }

    private static void Put(byte[] bytes, int offset, int value) =>
        BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
}
