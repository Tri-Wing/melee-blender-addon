using System.Buffers.Binary;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ArchivePatch(int Offset, int Length, string Owner);

/// <summary>
/// Checked append-only DAT mutation. This type is the sole owner of data-section
/// allocation, scalar patches, internal relocation entries, and archive-tail
/// preservation for model writers.
/// </summary>
public sealed class ArchiveMutationBuilder
{
    private readonly ArchiveLayout source;
    private readonly MemoryStream data = new();
    private readonly HashSet<int> relocations;
    private readonly HashSet<int> externalPointerFields;
    private readonly byte[] tail;
    private readonly bool[] patchedSourceBytes;
    private readonly List<ArchivePatch> permitted = [];
    private readonly List<ArchivePatch> patches = [];

    public ArchiveLayout Source => source;
    public int OriginalDataSize => source.DataSize;
    public int DataSize => checked((int)data.Length);
    public IReadOnlyList<ArchivePatch> PatchLog => patches.AsReadOnly();
    public IReadOnlyCollection<int> RelocationFields => relocations;

    public ArchiveMutationBuilder(ArchiveLayout source)
    {
        this.source = source;
        data.Write(source.Bytes.AsSpan(32, source.DataSize));
        int count = source.Read(8);
        relocations = Enumerable.Range(0, count)
            .Select(index => source.Read(32 + source.DataSize + index * 4))
            .ToHashSet();
        externalPointerFields = source.Pointers.Keys.Where(field => !relocations.Contains(field))
            .ToHashSet();
        tail = source.Bytes.AsSpan(32 + source.DataSize + count * 4).ToArray();
        patchedSourceBytes = new bool[source.DataSize];
    }

    public void PermitSourcePatch(int offset, int length, string owner)
    {
        Require(offset >= 0 && length > 0 && (long)offset + length <= source.DataSize,
            "ARCHIVE_PATCH_PERMISSION", "A permitted source patch is outside the original data section.");
        permitted.Add(new(offset, length, owner));
    }

    public int AppendAligned(ReadOnlySpan<byte> value, int alignment = 32)
    {
        Require(alignment > 0 && (alignment & (alignment - 1)) == 0,
            "ARCHIVE_ALIGNMENT", "Archive allocation alignment must be a positive power of two.");
        while (data.Position % alignment != 0) data.WriteByte(0);
        int offset = checked((int)data.Position);
        data.Write(value);
        return offset;
    }

    public int AppendZeroed(int length, int alignment = 32)
    {
        Require(length >= 0, "ARCHIVE_ALLOCATION", "Archive allocation length cannot be negative.");
        return AppendAligned(new byte[length], alignment);
    }

    public int AppendCopy(ArchiveLayout copySource, int sourceOffset, int length,
        int alignment = 4)
    {
        Require(sourceOffset >= 0 && length >= 0
            && (long)sourceOffset + length <= copySource.DataSize,
            "ARCHIVE_COPY", "Copied archive block is outside its source data section.");
        int offset = AppendAligned(copySource.Bytes.AsSpan(32 + sourceOffset, length), alignment);
        int relocationCount = copySource.Read(8);
        foreach (int field in Enumerable.Range(0, relocationCount)
                     .Select(index => copySource.Read(32 + copySource.DataSize + index * 4))
                     .Where(field => field >= sourceOffset && field < sourceOffset + length))
            relocations.Add(offset + field - sourceOffset);
        return offset;
    }

    public byte[] ReadBytes(int offset, int length)
    {
        Check(offset, length);
        return data.GetBuffer().AsSpan(offset, length).ToArray();
    }

    public int ReadInt32(int offset)
    {
        Check(offset, 4);
        return BinaryPrimitives.ReadInt32BigEndian(data.GetBuffer().AsSpan(offset, 4));
    }

    public ushort ReadUInt16(int offset)
    {
        Check(offset, 2);
        return BinaryPrimitives.ReadUInt16BigEndian(data.GetBuffer().AsSpan(offset, 2));
    }

    public void PatchInt32(int offset, int value, string owner)
    {
        Require(!relocations.Contains(offset) && !externalPointerFields.Contains(offset),
            "ARCHIVE_POINTER_PATCH", "Pointer fields must be changed with SetPointer or ClearPointer.");
        PatchBytes(offset, IntBytes(value), owner);
    }

    public void PatchUInt16(int offset, int value, string owner)
    {
        Require(value is >= 0 and <= ushort.MaxValue, "ARCHIVE_SCALAR", "Unsigned 16-bit patch is out of range.");
        Span<byte> bytes = stackalloc byte[2];
        BinaryPrimitives.WriteUInt16BigEndian(bytes, (ushort)value);
        PatchBytes(offset, bytes, owner);
    }

    public void PatchBytes(int offset, ReadOnlySpan<byte> value, string owner)
    {
        Check(offset, value.Length);
        Authorize(offset, value.Length, owner);
        int patchEnd = offset + value.Length;
        foreach (int field in relocations.Concat(externalPointerFields).Where(field =>
                     field < patchEnd && field + 4 > offset))
        {
            int relative = field - offset;
            Require(relative >= 0 && relative + 4 <= value.Length
                && data.GetBuffer().AsSpan(field, 4)
                    .SequenceEqual(value.Slice(relative, 4)),
                "ARCHIVE_POINTER_PATCH",
                "Raw byte patches cannot change or partially overlap pointer fields.");
        }
        value.CopyTo(data.GetBuffer().AsSpan(offset, value.Length));
        LogPatch(offset, value.Length, owner);
    }

    public void SetPointer(int field, int target, string owner)
    {
        Check(field, 4);
        Require(target > 0 && target < data.Length, "ARCHIVE_POINTER_TARGET",
            "Archive pointer target is outside the current data section.");
        Require(!externalPointerFields.Contains(field), "ARCHIVE_POINTER_KIND",
            "An external-reference chain field cannot become an internal relocation.");
        Authorize(field, 4, owner);
        IntBytes(target).CopyTo(data.GetBuffer().AsSpan(field, 4));
        relocations.Add(field);
        LogPatch(field, 4, owner);
    }

    public void ClearPointer(int field, string owner)
    {
        Check(field, 4);
        Require(!externalPointerFields.Contains(field), "ARCHIVE_POINTER_KIND",
            "An external-reference chain field cannot be cleared as an internal relocation.");
        Authorize(field, 4, owner);
        data.GetBuffer().AsSpan(field, 4).Clear();
        relocations.Remove(field);
        LogPatch(field, 4, owner);
    }

    public byte[] Build()
    {
        byte[] payload = data.ToArray();
        using var result = new MemoryStream();
        result.Write(source.Bytes.AsSpan(0, 32));
        result.Write(payload);
        foreach (int field in relocations.Order()) result.Write(IntBytes(field));
        result.Write(tail);
        byte[] output = result.ToArray();
        Put(output, 0, output.Length);
        Put(output, 4, payload.Length);
        Put(output, 8, relocations.Count);
        var parsed = new ArchiveLayout(output);
        Require(source.Roots.SequenceEqual(parsed.Roots)
            && source.References.SequenceEqual(parsed.References),
            "ARCHIVE_ROOT_PRESERVATION", "Root or external-reference inventory changed during archive mutation.");
        for (int offset = 0; offset < source.DataSize; offset++)
            Require(IsPatched(offset) || source.Bytes[32 + offset] == output[32 + offset],
                "ARCHIVE_SOURCE_PRESERVATION", $"Undeclared source byte 0x{offset:X} changed during archive mutation.");
        return output;
    }

    public ArchiveLayout BuildLayout() => new(Build());

    private void Authorize(int offset, int length, string owner)
    {
        if (offset >= source.DataSize) return;
        Require((long)offset + length <= source.DataSize
            && permitted.Any(patch => patch.Owner == owner && offset >= patch.Offset
                && (long)offset + length <= patch.Offset + patch.Length),
            "ARCHIVE_PATCH_PERMISSION",
            $"{owner} attempted undeclared source patch 0x{offset:X}+0x{length:X}.");
    }

    private bool IsPatched(int offset) => patchedSourceBytes[offset];

    private void LogPatch(int offset, int length, string owner)
    {
        patches.Add(new(offset, length, owner));
        int end = Math.Min(source.DataSize, offset + length);
        for (int index = Math.Min(offset, source.DataSize); index < end; index++)
            patchedSourceBytes[index] = true;
    }

    private void Check(int offset, int length) => Require(offset >= 0 && length >= 0
        && (long)offset + length <= data.Length, "ARCHIVE_MUTATION_BOUNDS",
        $"Archive mutation 0x{offset:X}+0x{length:X} exceeds the data section.");

    private static byte[] IntBytes(int value)
    {
        byte[] bytes = new byte[4];
        Put(bytes, 0, value);
        return bytes;
    }

    private static void Put(byte[] bytes, int offset, int value) =>
        BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
}
