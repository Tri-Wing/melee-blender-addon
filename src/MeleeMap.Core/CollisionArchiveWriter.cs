using System.Buffers.Binary;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

/// <summary>Append-only collision rewrite. All original data and offsets outside the 0x2C collision header survive byte-for-byte.</summary>
public static class CollisionArchiveWriter
{
    public static byte[] Write(ArchiveLayout source, CollisionData collision)
    {
        collision.Validate(forEditedExport: true);
        var r = new ArchiveDataReader(source);
        int header = source.Roots.Single(x => x.Name == "coll_data").Offset;
        r.Check(header, 0x2C);
        foreach (var (field, countField, stride) in new[] { (0, 4, 8), (8, 12, 16), (36, 40, 40) })
        {
            int count = r.Int(header + countField), start = r.Array(header + field, count, stride);
            long end = start + (long)count * stride;
            Require(!source.Pointers.Any(p => p.Key != header + field && p.Value >= start && p.Value < end)
                && !source.Roots.Concat(source.References).Any(root => root.Offset >= start && root.Offset < end),
                "COLLISION_ALIASED_BUFFER", "Collision buffers have external/interior references; editing them is not supported yet.");
            Require(!source.Pointers.Keys.Any(p => p >= start && p < end), "COLLISION_BUFFER_POINTER", "Collision leaf buffer contains unexpected pointers.");
        }
        using var data = new MemoryStream();
        data.Write(source.Bytes.AsSpan(32, source.DataSize));
        int Append(Action<MemoryStream> write)
        {
            while (data.Position % 32 != 0) data.WriteByte(0);
            int start = checked((int)data.Position); write(data); return start;
        }
        int vertices = Append(s => { foreach (var v in collision.Vertices) { Float(s, v.X); Float(s, v.Y); } });
        int lines = Append(s =>
        {
            foreach (var l in collision.Lines)
            {
                foreach (int value in new[] { l.Vertex0, l.Vertex1, l.Previous0, l.Next0, l.Previous1, l.Next1, l.HighFlags, l.LowFlags }) Short(s, value);
            }
        });
        int joints = Append(s =>
        {
            foreach (var joint in collision.Joints)
            {
                foreach (var range in joint.Ranges) { Short(s, range.Start); Short(s, range.Count); }
                foreach (float value in new[] { joint.Left, joint.Bottom, joint.Right, joint.Top }) Float(s, value);
                Short(s, joint.VertexStart); Short(s, joint.VertexCount);
            }
        });
        byte[] payload = data.ToArray();
        Put(payload, header, vertices); Put(payload, header + 4, collision.Vertices.Length);
        Put(payload, header + 8, lines); Put(payload, header + 12, collision.Lines.Length);
        for (int k = 0; k < 5; k++)
        {
            BinaryPrimitives.WriteInt16BigEndian(payload.AsSpan(header + 16 + k * 4), (short)collision.Ranges[k].Start);
            BinaryPrimitives.WriteInt16BigEndian(payload.AsSpan(header + 18 + k * 4), (short)collision.Ranges[k].Count);
        }
        Put(payload, header + 36, joints); Put(payload, header + 40, collision.Joints.Length);
        int relocationCount = source.Read(8);
        var relocations = Enumerable.Range(0, relocationCount).Select(i => source.Read(32 + source.DataSize + i * 4)).ToList();
        foreach (int field in new[] { header, header + 8, header + 36 }) if (!relocations.Contains(field)) relocations.Add(field);
        using var result = new MemoryStream();
        result.Write(source.Bytes.AsSpan(0, 32)); result.Write(payload);
        foreach (int field in relocations) Int(result, field);
        result.Write(source.Bytes.AsSpan(32 + source.DataSize + relocationCount * 4));
        byte[] output = result.ToArray();
        Put(output, 0, output.Length); Put(output, 4, payload.Length); Put(output, 8, relocations.Count);
        var parsed = new ArchiveLayout(output);
        Require(source.Roots.SequenceEqual(parsed.Roots) && source.References.SequenceEqual(parsed.References), "PRESERVATION_ROOTS", "Root inventory changed.");
        Require(source.Bytes.AsSpan(32, header).SequenceEqual(output.AsSpan(32, header))
            && source.Bytes.AsSpan(32 + header + 0x2C, source.DataSize - header - 0x2C).SequenceEqual(output.AsSpan(32 + header + 0x2C, source.DataSize - header - 0x2C)),
            "PRESERVATION_PAYLOAD", "Unrelated source data changed.");
        return output;
    }
    private static void Put(byte[] bytes, int offset, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
    private static void Int(Stream stream, int value) { Span<byte> bytes = stackalloc byte[4]; BinaryPrimitives.WriteInt32BigEndian(bytes, value); stream.Write(bytes); }
    private static void Short(Stream stream, int value) { Span<byte> bytes = stackalloc byte[2]; BinaryPrimitives.WriteUInt16BigEndian(bytes, unchecked((ushort)value)); stream.Write(bytes); }
    private static void Float(Stream stream, float value) => Int(stream, BitConverter.SingleToInt32Bits(value));
}
