using System.Buffers.Binary;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

/// <summary>Detaches one independently owned POBJ while preserving its DOBJ and all source bytes.</summary>
public static class ModelDeletionWriter
{
    public static byte[] Write(ArchiveLayout source, EditableModel target)
    {
        int field = target.PobjLinkField >= 0 ? target.PobjLinkField : target.DobjOffset + 12;
        var reader = new ArchiveDataReader(source);
        Require(reader.Pointer(field) == target.PobjOffset, "MODEL_DELETE_TARGET",
            "The deleted model is no longer attached to its source DOBJ.");

        int oldRelocationCount = source.Read(8);
        int? next = reader.Pointer(target.PobjOffset + 4);
        int detachedLink = next.HasValue ? target.PobjOffset + 4 : field;
        int[] relocations = Enumerable.Range(0, oldRelocationCount)
            .Select(index => source.Read(32 + source.DataSize + index * 4))
            .Where(offset => offset != detachedLink).ToArray();
        Require(relocations.Length == oldRelocationCount - 1,
            "MODEL_DELETE_TARGET", "The deleted model pointer is missing or duplicated.");

        byte[] payload = source.Bytes.AsSpan(32, source.DataSize).ToArray();
        Put(payload, field, next ?? 0);
        using var result = new MemoryStream();
        result.Write(source.Bytes.AsSpan(0, 32));
        result.Write(payload);
        foreach (int offset in relocations) Int(result, offset);
        result.Write(source.Bytes.AsSpan(32 + source.DataSize + oldRelocationCount * 4));

        byte[] output = result.ToArray();
        Put(output, 0, output.Length);
        Put(output, 8, relocations.Length);
        Verify(source, new ArchiveLayout(output), target, next);
        return output;
    }

    public static void Verify(ArchiveLayout source, ArchiveLayout output, EditableModel target,
        int? next = null)
    {
        int field = target.PobjLinkField >= 0 ? target.PobjLinkField : target.DobjOffset + 12;
        Require(source.Roots.SequenceEqual(output.Roots)
            && source.References.SequenceEqual(output.References), "MODEL_DELETE_ROOTS",
            "Root or external-reference inventory changed while deleting a model.");
        Require(output.Pointers.TryGetValue(field, out int successor) == next.HasValue
            && (!next.HasValue || successor == next.Value)
            && output.Read(32 + field) == (next ?? 0),
            "MODEL_DELETE_WRITE", "The deleted POBJ remains in its DOBJ's POBJ list.");
        if (next.HasValue)
            Require(!output.Pointers.ContainsKey(target.PobjOffset + 4),
                "MODEL_DELETE_WRITE", "The detached POBJ still owns its successor relocation.");
        for (int offset = 0; offset < source.DataSize; offset++)
            Require(offset >= field && offset < field + 4
                || source.Bytes[32 + offset] == output.Bytes[32 + offset],
                "MODEL_DELETE_PRESERVATION", "Unrelated model/archive data changed while deleting a model.");
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
