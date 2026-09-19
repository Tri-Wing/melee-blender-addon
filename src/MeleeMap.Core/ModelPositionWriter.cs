using System.Buffers.Binary;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

/// <summary>Replace only positions, retaining all original rendering attributes and material state.</summary>
public static class ModelPositionWriter
{
    public static byte[] Write(ArchiveLayout source, EditableModel target, MeshData mesh)
    {
        var original = GxMeshDecoder.Decode(source, target.PobjOffset);
        Require(mesh.Positions.Length == original.Positions.Length && mesh.TriangleIndices.SequenceEqual(original.TriangleIndices)
            && mesh.Positions.All(p => float.IsFinite(p.X) && float.IsFinite(p.Y) && float.IsFinite(p.Z)),
            "MODEL_POSITION_TOPOLOGY", "Appearance-preserving edits require the original vertex count and triangle indices.");
        var r = new ArchiveDataReader(source);
        int attrs = r.Pointer(target.PobjOffset + 8)!.Value;
        int dl = r.Pointer(target.PobjOffset + 16)!.Value;
        var attributes = new List<GxMeshDecoder.Attribute>();
        for (int at = attrs; r.Int(at) != 255; at += 24)
            attributes.Add(new(r.Int(at), r.Int(at + 4), r.Int(at + 8), r.Int(at + 12),
                r.Byte(at + 16), r.UShort(at + 18), r.Pointer(at + 20)));
        // Clone descriptors, leaving every non-position format and buffer intact.
        byte[] descriptors = new byte[(attributes.Count + 1) * 24];
        source.Bytes.AsSpan(32 + attrs, attributes.Count * 24).CopyTo(descriptors);
        Put(descriptors, attributes.Count * 24, 255);
        int pos = attributes.FindIndex(a => a.Name == 9) * 24;
        Array.Clear(descriptors, pos, 24);
        Put(descriptors, pos, 9); Put(descriptors, pos + 4, 1); // GX_DIRECT
        Put(descriptors, pos + 8, 1); Put(descriptors, pos + 12, 4); // XYZ, float32
        Short(descriptors, pos + 18, 12);
        using var display = new MemoryStream();
        int cursor = dl, end = dl + r.UShort(target.PobjOffset + 14) * 32, vertex = 0;
        // Decode above has already bounded and validated all commands/attributes.
        while (cursor < end)
        {
            byte command = r.Byte(cursor++);
            if (command == 0) break;
            int count = r.UShort(cursor); cursor += 2;
            display.WriteByte(command); byte[] countBytes = new byte[2]; Short(countBytes, 0, count); display.Write(countBytes);
            for (int i = 0; i < count; i++)
            {
                foreach (var attr in attributes)
                {
                    int size = attr.Type == 1 ? GxMeshDecoder.ElementSize(attr) : attr.Type == 2 ? 1 : 2;
                    if (attr.Name == 9)
                    {
                        var p = mesh.Positions[vertex]; byte[] value = new byte[12];
                        Float(value, 0, p.X); Float(value, 4, p.Y); Float(value, 8, p.Z); display.Write(value);
                    }
                    else display.Write(source.Bytes.AsSpan(32 + cursor, size));
                    cursor += size;
                }
                vertex++;
            }
        }
        while (display.Length % 32 != 0) display.WriteByte(0);
        Require(display.Length / 32 <= short.MaxValue, "MODEL_GX_LIMIT", "Position replacement exceeds GX display-list limits.");
        using var data = new MemoryStream(); data.Write(source.Bytes.AsSpan(32, source.DataSize));
        int Append(byte[] bytes)
        {
            while (data.Position % 32 != 0) data.WriteByte(0);
            int start = checked((int)data.Position); data.Write(bytes); return start;
        }
        int newAttrs = Append(descriptors), newDl = Append(display.ToArray());
        byte[] payload = data.ToArray();
        Put(payload, target.PobjOffset + 8, newAttrs);
        Short(payload, target.PobjOffset + 14, checked((int)display.Length / 32));
        Put(payload, target.PobjOffset + 16, newDl);
        int countRelocations = source.Read(8);
        var relocations = Enumerable.Range(0, countRelocations).Select(i => source.Read(32 + source.DataSize + i * 4)).ToList();
        for (int i = 0; i < attributes.Count; i++)
            if (attributes[i].Name != 9 && attributes[i].Buffer.HasValue) relocations.Add(newAttrs + i * 24 + 20);
        using var result = new MemoryStream(); result.Write(source.Bytes.AsSpan(0, 32)); result.Write(payload);
        foreach (int field in relocations) { byte[] value = new byte[4]; Put(value, 0, field); result.Write(value); }
        result.Write(source.Bytes.AsSpan(32 + source.DataSize + countRelocations * 4));
        byte[] output = result.ToArray(); Put(output, 0, output.Length); Put(output, 4, payload.Length); Put(output, 8, relocations.Count);
        var parsed = new ArchiveLayout(output);
        Require(source.Roots.SequenceEqual(parsed.Roots) && source.References.SequenceEqual(parsed.References),
            "PRESERVATION_ROOTS", "Root inventory changed.");
        for (int i = 0; i < source.DataSize; i++)
        {
            bool changed = (i >= target.PobjOffset + 8 && i < target.PobjOffset + 12)
                || (i >= target.PobjOffset + 14 && i < target.PobjOffset + 20);
            Require(changed || source.Bytes[32 + i] == output[32 + i], "PRESERVATION_PAYLOAD", "Unrelated archive data changed.");
        }
        Verify(parsed, source, target, mesh);
        return output;
    }

    public static void Verify(ArchiveLayout archive, ArchiveLayout source, EditableModel target, MeshData expected)
    {
        var actual = GxMeshDecoder.Decode(archive, target.PobjOffset);
        Require(actual.Positions.SequenceEqual(expected.Positions) && actual.TriangleIndices.SequenceEqual(expected.TriangleIndices)
            && ((actual.Normals == null && expected.Normals == null)
                || (actual.Normals != null && expected.Normals != null && actual.Normals.SequenceEqual(expected.Normals)))
            && (actual.TexCoords0 == null ? expected.TexCoords0 == null
                : expected.TexCoords0 != null && actual.TexCoords0.SequenceEqual(expected.TexCoords0))
            && actual.Envelopes == null && actual.BoundJobjSourceOffset == null,
            "MODEL_WRITE_MISMATCH", "Reloaded positions or original shading differ from the vertex edit.");
        var r = new ArchiveDataReader(archive); var before = new ArchiveDataReader(source);
        Require(r.UShort(target.PobjOffset + 12) == before.UShort(target.PobjOffset + 12)
            && r.Pointer(target.DobjOffset + 8) == before.Pointer(target.DobjOffset + 8),
            "MODEL_MATERIAL_MISMATCH", "Vertex editing changed original material or culling state.");
    }

    private static void Put(byte[] bytes, int offset, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
    private static void Short(byte[] bytes, int offset, int value) => BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(offset, 2), checked((ushort)value));
    private static void Float(byte[] bytes, int offset, float value) => Put(bytes, offset, BitConverter.SingleToInt32Bits(value));
}
