using System.Buffers.Binary;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

/// <summary>Append GX triangles with grey or reused stage material and optional UV0.</summary>
public static class ModelArchiveWriter
{
    private const string Owner = "model-geometry";

    public static byte[] Write(ArchiveLayout source, EditableModel target,
        MeshData mesh, int? sourceMaterial = null, int culling = 0x4000)
    {
        var builder = new ArchiveMutationBuilder(source);
        Write(builder, target, mesh, sourceMaterial, culling);
        byte[] output = builder.Build();
        Verify(new ArchiveLayout(output), target, mesh, sourceMaterial, culling);
        return output;
    }

    internal static void Write(ArchiveMutationBuilder builder, EditableModel target,
        MeshData mesh, int? sourceMaterial = null, int culling = 0x4000)
    {
        int attributeCount = mesh.TexCoords0 == null ? 2 : 3;
        var attributes = new byte[(attributeCount + 1) * 24];
        for (int i = 0; i < attributeCount; i++)
        {
            Put(attributes, i * 24, i == 2 ? 13 : 9 + i);
            Put(attributes, i * 24 + 4, 1);
            Put(attributes, i * 24 + 8, i == 1 ? 0 : 1);
            Put(attributes, i * 24 + 12, 4);
            Short(attributes, i * 24 + 18, i == 2 ? 8 : 12);
        }
        Put(attributes, attributeCount * 24, 255);
        int attrs = builder.AppendAligned(attributes);
        int stride = mesh.TexCoords0 == null ? 24 : 32;
        int length = checked((3 + mesh.Positions.Length * stride + 31) / 32 * 32);
        Require(length / 32 <= short.MaxValue && mesh.Positions.Length <= ushort.MaxValue,
            "MODEL_GX_LIMIT", "Replacement exceeds GX display-list limits.");
        byte[] display = new byte[length];
        display[0] = 0x90;
        Short(display, 1, mesh.Positions.Length);
        for (int i = 0; i < mesh.Positions.Length; i++)
        {
            var position = mesh.Positions[i];
            var normal = mesh.Normals![i];
            int offset = 3 + i * stride;
            foreach (float value in new[] { position.X, position.Y, position.Z,
                normal.X, normal.Y, normal.Z })
            {
                Float(display, offset, value);
                offset += 4;
            }
            if (mesh.TexCoords0 != null)
            {
                Float(display, offset, mesh.TexCoords0[i].X);
                Float(display, offset + 4, mesh.TexCoords0[i].Y);
            }
        }
        int displayList = builder.AppendAligned(display);
        int materialOffset;
        if (sourceMaterial.HasValue) materialOffset = sourceMaterial.Value;
        else
        {
            byte[] color = new byte[20];
            for (int i = 0; i < 8; i++) color[i] = (byte)(i % 4 == 3 ? 255 : 128);
            color[11] = 255;
            Float(color, 12, 1);
            Float(color, 16, 0);
            int material = builder.AppendAligned(color);
            byte[] mobj = new byte[24];
            Put(mobj, 4, 1);
            materialOffset = builder.AppendAligned(mobj);
            builder.SetPointer(materialOffset + 12, material, Owner);
        }

        int flags = builder.ReadUInt16(target.PobjOffset + 12);
        builder.PermitSourcePatch(target.PobjOffset + 8, 12, Owner);
        if (target.DobjOffset < builder.OriginalDataSize)
            builder.PermitSourcePatch(target.DobjOffset + 8, 4, Owner);
        builder.SetPointer(target.PobjOffset + 8, attrs, Owner);
        builder.PatchUInt16(target.PobjOffset + 12,
            (flags & ~0xC000) | culling, Owner);
        builder.PatchUInt16(target.PobjOffset + 14, length / 32, Owner);
        builder.SetPointer(target.PobjOffset + 16, displayList, Owner);
        builder.SetPointer(target.DobjOffset + 8, materialOffset, Owner);
    }

    public static void Verify(ArchiveLayout archive, EditableModel target,
        MeshData expected, int? sourceMaterial = null, int culling = 0x4000)
    {
        var actual = GxMeshDecoder.Decode(archive, target.PobjOffset);
        Require(actual.Positions.SequenceEqual(expected.Positions)
            && actual.Normals!.SequenceEqual(expected.Normals!)
            && actual.TriangleIndices.SequenceEqual(expected.TriangleIndices)
            && actual.Envelopes == null && actual.BoundJobjSourceOffset == null,
            "MODEL_WRITE_MISMATCH", "Reloaded model differs from replacement geometry.");
        Require(actual.TexCoords0 == null ? expected.TexCoords0 == null
            : expected.TexCoords0 != null && actual.TexCoords0.SequenceEqual(expected.TexCoords0),
            "MODEL_UV_MISMATCH", "Reloaded UVs differ from assigned triangle corners.");
        var reader = new ArchiveDataReader(archive);
        int mobj = reader.Pointer(target.DobjOffset + 8)!.Value;
        Require((reader.UShort(target.PobjOffset + 12) & 0xC000) == culling,
            "MODEL_CULL_MISMATCH", "Replacement model culling differs from the selected export path.");
        if (sourceMaterial.HasValue)
        {
            Require(mobj == sourceMaterial.Value, "MODEL_MATERIAL_MISMATCH",
                "Assigned stage material changed.");
            return;
        }
        int material = reader.Pointer(mobj + 12)!.Value;
        Require(reader.Int(mobj + 4) == 1 && reader.Pointer(mobj + 8) == null
            && reader.Pointer(mobj + 20) == null
            && reader.Int(material + 4) == unchecked((int)0x808080FF)
            && reader.Float(material + 12) == 1,
            "MODEL_MATERIAL_MISMATCH", "Replacement grey material differs after reload.");
    }

    private static void Put(byte[] bytes, int offset, int value) =>
        BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
    private static void Short(byte[] bytes, int offset, int value) =>
        BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(offset, 2), checked((ushort)value));
    private static void Float(byte[] bytes, int offset, float value) =>
        Put(bytes, offset, BitConverter.SingleToInt32Bits(value));
}
