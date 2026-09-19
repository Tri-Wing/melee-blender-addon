using System.Buffers.Binary;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

/// <summary>Bounded data-section reads, including arrays split by interior relocation targets.</summary>
public sealed class ArchiveDataReader(ArchiveLayout archive)
{
    public void Check(int offset, long size) => Require(offset >= 0 && size >= 0 && offset + size <= archive.DataSize,
        "STRUCTURE_BOUNDS", $"Data at 0x{offset:X} with length {size} exceeds archive bounds.");
    public int Int(int offset) { Check(offset, 4); return archive.Read(32 + offset); }
    public short Short(int offset) { Check(offset, 2); return BinaryPrimitives.ReadInt16BigEndian(archive.Bytes.AsSpan(32 + offset, 2)); }
    public ushort UShort(int offset) => unchecked((ushort)Short(offset));
    public byte Byte(int offset) { Check(offset, 1); return archive.Bytes[32 + offset]; }
    public float Float(int offset) => BitConverter.Int32BitsToSingle(Int(offset));
    public int? Pointer(int field)
    {
        Check(field, 4);
        if (archive.Pointers.TryGetValue(field, out int target)) return target;
        Require(Int(field) == 0, "STRUCTURE_POINTER", $"Pointer at 0x{field:X} is missing relocation.");
        return null;
    }
    public int Array(int field, int count, int stride)
    {
        Require(count >= 0, "STRUCTURE_COUNT", $"Negative array count at 0x{field:X}.");
        int? target = Pointer(field);
        Require(count == 0 || target.HasValue, "STRUCTURE_POINTER", $"Nonempty array at 0x{field:X} has no pointer.");
        if (target.HasValue) Check(target.Value, (long)count * stride);
        return target ?? 0;
    }
}
