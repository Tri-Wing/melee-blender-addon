using System.Buffers.Binary;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

/// <summary>Append GX triangles with grey or reused stage material and optional UV0. Original offsets remain stable.</summary>
public static class ModelArchiveWriter
{
    public static byte[] Write(ArchiveLayout source, EditableModel target, MeshData mesh, int? sourceMaterial = null, int culling = 0x4000)
    {
        using var data = new MemoryStream(); data.Write(source.Bytes.AsSpan(32, source.DataSize));
        int Append(byte[] bytes)
        {
            while (data.Position % 32 != 0) data.WriteByte(0);
            int start = checked((int)data.Position); data.Write(bytes); return start;
        }
        int attributeCount = mesh.TexCoords0 == null ? 2 : 3;
        var attributes = new byte[(attributeCount + 1) * 24];
        for (int i = 0; i < attributeCount; i++)
        {
            Put(attributes, i * 24, i == 2 ? 13 : 9 + i); // GX_VA_POS / GX_VA_NRM
            Put(attributes, i * 24 + 4, 1); // GX_DIRECT
            Put(attributes, i * 24 + 8, i == 1 ? 0 : 1); // XYZ / NRM_XYZ
            Put(attributes, i * 24 + 12, 4); // GX_F32
            Short(attributes, i * 24 + 18, i == 2 ? 8 : 12);
        }
        Put(attributes, attributeCount * 24, 255); // GX_VA_NULL
        int attrs = Append(attributes);
        int stride = mesh.TexCoords0 == null ? 24 : 32;
        int length = checked((3 + mesh.Positions.Length * stride + 31) / 32 * 32);
        Require(length / 32 <= short.MaxValue && mesh.Positions.Length <= ushort.MaxValue, "MODEL_GX_LIMIT", "Replacement exceeds GX display-list limits.");
        byte[] display = new byte[length]; display[0] = 0x90; Short(display, 1, mesh.Positions.Length);
        for (int i = 0; i < mesh.Positions.Length; i++)
        {
            var p = mesh.Positions[i]; var n = mesh.Normals![i];
            int offset = 3 + i * stride;
            foreach (float value in new[] { p.X, p.Y, p.Z, n.X, n.Y, n.Z }) { Float(display, offset, value); offset += 4; }
            if (mesh.TexCoords0 != null) { Float(display, offset, mesh.TexCoords0[i].X); Float(display, offset + 4, mesh.TexCoords0[i].Y); }
        }
        int dl = Append(display);
        int mobjOffset;
        if (sourceMaterial.HasValue) mobjOffset = sourceMaterial.Value;
        else
        {
            byte[] color = new byte[20];
            for (int i = 0; i < 8; i++) color[i] = (byte)(i % 4 == 3 ? 255 : 128);
            color[11] = 255; Float(color, 12, 1); Float(color, 16, 0);
            int material = Append(color);
            byte[] mobj = new byte[24]; Put(mobj, 4, 1); Put(mobj, 12, material);
            mobjOffset = Append(mobj);
        }
        byte[] payload = data.ToArray(); int pOffset = target.PobjOffset;
        // New topology uses Blender's winding/back-face culling. Material/UV-only
        // changes retain the original winding, normals and culling instead.
        int flags = new ArchiveDataReader(source).UShort(pOffset + 12);
        Short(payload, pOffset + 12, (flags & ~0xC000) | culling);
        Put(payload, pOffset + 8, attrs); Short(payload, pOffset + 14, length / 32); Put(payload, pOffset + 16, dl);
        Put(payload, target.DobjOffset + 8, mobjOffset);
        int relocationCount = source.Read(8);
        var relocations = Enumerable.Range(0, relocationCount).Select(i => source.Read(32 + source.DataSize + i * 4)).ToList();
        foreach (int field in new[] { pOffset + 8, pOffset + 16, target.DobjOffset + 8 })
            if (!relocations.Contains(field)) relocations.Add(field);
        if (!sourceMaterial.HasValue) relocations.Add(mobjOffset + 12);
        using var result = new MemoryStream();
        result.Write(source.Bytes.AsSpan(0, 32)); result.Write(payload);
        foreach (int field in relocations) { byte[] bytes = new byte[4]; Put(bytes, 0, field); result.Write(bytes); }
        result.Write(source.Bytes.AsSpan(32 + source.DataSize + relocationCount * 4));
        byte[] output = result.ToArray(); Put(output, 0, output.Length); Put(output, 4, payload.Length); Put(output, 8, relocations.Count);
        var parsed = new ArchiveLayout(output);
        Require(source.Roots.SequenceEqual(parsed.Roots) && source.References.SequenceEqual(parsed.References), "PRESERVATION_ROOTS", "Root inventory changed.");
        for (int i = 0; i < source.DataSize; i++)
        {
            bool changed = (i >= pOffset + 8 && i < pOffset + 20)
                || (i >= target.DobjOffset + 8 && i < target.DobjOffset + 12);
            Require(changed || source.Bytes[32 + i] == output[32 + i], "PRESERVATION_PAYLOAD", "Unrelated model/archive data changed.");
        }
        Verify(parsed, target, mesh, sourceMaterial, culling);
        return output;
    }

    public static void Verify(ArchiveLayout archive, EditableModel target, MeshData expected, int? sourceMaterial = null, int culling = 0x4000)
    {
        var actual = GxMeshDecoder.Decode(archive, target.PobjOffset);
        Require(actual.Positions.SequenceEqual(expected.Positions) && actual.Normals!.SequenceEqual(expected.Normals!)
            && actual.TriangleIndices.SequenceEqual(expected.TriangleIndices) && actual.Envelopes == null && actual.BoundJobjSourceOffset == null,
            "MODEL_WRITE_MISMATCH", "Reloaded model differs from replacement geometry.");
        Require(actual.TexCoords0 == null ? expected.TexCoords0 == null
            : expected.TexCoords0 != null && actual.TexCoords0.SequenceEqual(expected.TexCoords0),
            "MODEL_UV_MISMATCH", "Reloaded UVs differ from assigned triangle corners.");
        var r = new ArchiveDataReader(archive); int mobj = r.Pointer(target.DobjOffset + 8)!.Value;
        Require((r.UShort(target.PobjOffset + 12) & 0xC000) == culling,
            "MODEL_CULL_MISMATCH", "Replacement model culling differs from the selected export path.");
        if (sourceMaterial.HasValue)
        {
            Require(mobj == sourceMaterial.Value, "MODEL_MATERIAL_MISMATCH", "Assigned stage material changed.");
            return;
        }
        int material = r.Pointer(mobj + 12)!.Value;
        Require(r.Int(mobj + 4) == 1 && r.Pointer(mobj + 8) == null && r.Pointer(mobj + 20) == null
            && r.Int(material + 4) == unchecked((int)0x808080FF) && r.Float(material + 12) == 1,
            "MODEL_MATERIAL_MISMATCH", "Replacement grey material differs after reload.");
    }
    private static void Put(byte[] bytes, int offset, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
    private static void Short(byte[] bytes, int offset, int value) => BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(offset, 2), checked((ushort)value));
    private static void Float(byte[] bytes, int offset, float value) => Put(bytes, offset, BitConverter.SingleToInt32Bits(value));
}
