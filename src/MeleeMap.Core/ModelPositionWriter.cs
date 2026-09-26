using System.Buffers.Binary;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

/// <summary>Replace positions and corner colors, retaining other rendering attributes and material state.</summary>
public static class ModelPositionWriter
{
    private const string Owner = "model-position";

    public static byte[] Write(ArchiveLayout source, EditableModel target, MeshData mesh,
        bool reverseWinding = false)
    {
        var builder = new ArchiveMutationBuilder(source);
        Write(builder, target, mesh, reverseWinding);
        byte[] output = builder.Build();
        Verify(new ArchiveLayout(output), source, target, mesh,
            reverseWinding: reverseWinding);
        return output;
    }

    internal static void Write(ArchiveMutationBuilder builder, EditableModel target,
        MeshData mesh, bool reverseWinding = false)
    {
        var source = builder.BuildLayout();
        var original = GxMeshDecoder.Decode(source, target.PobjOffset);
        bool expanded = mesh.Positions.Length == original.TriangleIndices.Length
            && mesh.TriangleIndices.SequenceEqual(Enumerable.Range(0, mesh.Positions.Length));
        Require((expanded || (mesh.Positions.Length == original.Positions.Length && mesh.TriangleIndices.SequenceEqual(original.TriangleIndices)))
            && mesh.Positions.All(p => float.IsFinite(p.X) && float.IsFinite(p.Y) && float.IsFinite(p.Z)),
            "MODEL_POSITION_TOPOLOGY", "Appearance-preserving edits require the original vertex count and triangle indices.");
        var r = new ArchiveDataReader(source);
        int attrs = r.Pointer(target.PobjOffset + 8)!.Value;
        int dl = r.Pointer(target.PobjOffset + 16)!.Value;
        var attributes = new List<GxMeshDecoder.Attribute>();
        for (int at = attrs; r.Int(at) != 255; at += 24)
            attributes.Add(new(r.Int(at), r.Int(at + 4), r.Int(at + 8), r.Int(at + 12),
                r.Byte(at + 16), r.UShort(at + 18), r.Pointer(at + 20)));
        bool ColorChanged(int name) => name == 11 && mesh.Colors0 != null &&
            !(expanded ? original.TriangleIndices.Select(i => original.Colors0![i]) : original.Colors0!).SequenceEqual(mesh.Colors0)
            || name == 12 && mesh.Colors1 != null &&
            !(expanded ? original.TriangleIndices.Select(i => original.Colors1![i]) : original.Colors1!).SequenceEqual(mesh.Colors1);
        var changedColors = new HashSet<int>(new[] { 11, 12 }.Where(ColorChanged));
        bool ReplaceColor(int name) => changedColors.Contains(name);
        bool replaceNormals = mesh.Normals != null && original.Normals != null
            && !(expanded ? original.TriangleIndices.Select(i => original.Normals[i])
                : original.Normals).SequenceEqual(mesh.Normals);
        // Clone descriptors and retain the original buffers for untouched attributes.
        byte[] descriptors = new byte[(attributes.Count + 1) * 24];
        source.Bytes.AsSpan(32 + attrs, attributes.Count * 24).CopyTo(descriptors);
        Put(descriptors, attributes.Count * 24, 255);
        int pos = attributes.FindIndex(a => a.Name == 9) * 24;
        Array.Clear(descriptors, pos, 24);
        Put(descriptors, pos, 9); Put(descriptors, pos + 4, 1); // GX_DIRECT
        Put(descriptors, pos + 8, 1); Put(descriptors, pos + 12, 4); // XYZ, float32
        Short(descriptors, pos + 18, 12);
        for (int i = 0; i < attributes.Count; i++)
        {
            if (attributes[i].Name != 10 || !replaceNormals)
            {
                if (!ReplaceColor(attributes[i].Name)) continue;
            }
            int at = i * 24; Array.Clear(descriptors, at, 24);
            Put(descriptors, at, attributes[i].Name); Put(descriptors, at + 4, 1); // GX_DIRECT
            if (attributes[i].Name == 10)
            {
                Put(descriptors, at + 8, 0); Put(descriptors, at + 12, 4); // XYZ normal, float32
                Short(descriptors, at + 18, 12);
            }
            else
            {
                Put(descriptors, at + 8, 1); Put(descriptors, at + 12, 5); // RGBA8
                Short(descriptors, at + 18, 4);
            }
        }
        using var display = new MemoryStream();
        var tokens = new List<int[]>();
        var commands = new List<(byte Command, int Count)>();
        int cursor = dl, end = dl + r.UShort(target.PobjOffset + 14) * 32, vertex = 0;
        // Decode above has already bounded and validated all commands/attributes.
        while (cursor < end)
        {
            byte command = r.Byte(cursor++);
            if (command == 0) break;
            int count = r.UShort(cursor); cursor += 2;
            commands.Add((command, count));
            for (int i = 0; i < count; i++)
            {
                var offsets = new int[attributes.Count];
                for (int j = 0; j < attributes.Count; j++)
                {
                    var attr = attributes[j]; offsets[j] = cursor;
                    cursor += attr.Type == 1 ? GxMeshDecoder.ElementSize(attr) : attr.Type == 2 ? 1 : 2;
                }
                tokens.Add(offsets);
            }
        }
        void EmitVertex(int sourceVertex, int outputVertex)
        {
            for (int j = 0; j < attributes.Count; j++)
            {
                var attr = attributes[j];
                int size = attr.Type == 1 ? GxMeshDecoder.ElementSize(attr) : attr.Type == 2 ? 1 : 2;
                if (attr.Name == 9)
                {
                    var p = mesh.Positions[outputVertex]; byte[] value = new byte[12];
                    Float(value, 0, p.X); Float(value, 4, p.Y); Float(value, 8, p.Z); display.Write(value);
                }
                else if (attr.Name == 10 && replaceNormals)
                {
                    var n = mesh.Normals![outputVertex]; byte[] value = new byte[12];
                    Float(value, 0, n.X); Float(value, 4, n.Y); Float(value, 8, n.Z); display.Write(value);
                }
                else if (ReplaceColor(attr.Name))
                {
                    var color = (attr.Name == 11 ? mesh.Colors0! : mesh.Colors1!)[outputVertex];
                    foreach (float value in new[] { color.R, color.G, color.B, color.A })
                        display.WriteByte(checked((byte)MathF.Round(value * 255)));
                }
                else display.Write(source.Bytes.AsSpan(32 + tokens[sourceVertex][j], size));
            }
        }
        if (reverseWinding)
        {
            display.WriteByte(0x90); byte[] countBytes = new byte[2];
            Short(countBytes, 0, original.TriangleIndices.Length); display.Write(countBytes);
            for (int i = 0; i < original.TriangleIndices.Length; i += 3)
                foreach (int corner in new[] { i, i + 2, i + 1 })
                    EmitVertex(original.TriangleIndices[corner],
                        expanded ? corner : original.TriangleIndices[corner]);
        }
        else if (expanded)
        {
            display.WriteByte(0x90); byte[] countBytes = new byte[2];
            Short(countBytes, 0, mesh.Positions.Length); display.Write(countBytes);
            for (int i = 0; i < mesh.Positions.Length; i++) EmitVertex(original.TriangleIndices[i], i);
        }
        else
        {
            foreach (var command in commands)
            {
                display.WriteByte(command.Command); byte[] countBytes = new byte[2];
                Short(countBytes, 0, command.Count); display.Write(countBytes);
                for (int i = 0; i < command.Count; i++, vertex++) EmitVertex(vertex, vertex);
            }
        }
        while (display.Length % 32 != 0) display.WriteByte(0);
        Require(display.Length / 32 <= short.MaxValue, "MODEL_GX_LIMIT", "Position replacement exceeds GX display-list limits.");
        int newAttrs = builder.AppendAligned(descriptors);
        int newDisplay = builder.AppendAligned(display.ToArray());
        for (int i = 0; i < attributes.Count; i++)
            if (attributes[i].Name != 9
                && !(attributes[i].Name == 10 && replaceNormals)
                && !ReplaceColor(attributes[i].Name)
                && attributes[i].Buffer.HasValue)
                builder.SetPointer(newAttrs + i * 24 + 20,
                    attributes[i].Buffer!.Value, Owner);
        builder.PermitSourcePatch(target.PobjOffset + 8, 4, Owner);
        builder.PermitSourcePatch(target.PobjOffset + 14, 6, Owner);
        builder.SetPointer(target.PobjOffset + 8, newAttrs, Owner);
        builder.PatchUInt16(target.PobjOffset + 14,
            checked((int)display.Length / 32), Owner);
        builder.SetPointer(target.PobjOffset + 16, newDisplay, Owner);
    }

    public static void Verify(ArchiveLayout archive, ArchiveLayout source,
        EditableModel target, MeshData expected, int? expectedMaterial = null,
        bool reverseWinding = false)
    {
        var original = GxMeshDecoder.Decode(source, target.PobjOffset);
        expected = OutputGeometry(original, expected, reverseWinding);
        var actual = GxMeshDecoder.Decode(archive, target.PobjOffset);
        Require(actual.Positions.SequenceEqual(expected.Positions) && actual.TriangleIndices.SequenceEqual(expected.TriangleIndices)
            && ((actual.Normals == null && expected.Normals == null)
                || (actual.Normals != null && expected.Normals != null && actual.Normals.SequenceEqual(expected.Normals)))
            && (actual.TexCoords0 == null ? expected.TexCoords0 == null
                : expected.TexCoords0 != null && actual.TexCoords0.SequenceEqual(expected.TexCoords0))
            && (actual.TexCoords1 == null ? expected.TexCoords1 == null
                : expected.TexCoords1 != null && actual.TexCoords1.SequenceEqual(expected.TexCoords1))
            && (actual.Colors0 == null ? expected.Colors0 == null
                : expected.Colors0 != null && actual.Colors0.SequenceEqual(expected.Colors0))
            && (actual.Colors1 == null ? expected.Colors1 == null
                : expected.Colors1 != null && actual.Colors1.SequenceEqual(expected.Colors1))
            && actual.Envelopes == null && actual.BoundJobjSourceOffset == null,
            "MODEL_WRITE_MISMATCH", "Reloaded positions or original shading differ from the vertex edit.");
        var r = new ArchiveDataReader(archive); var before = new ArchiveDataReader(source);
        Require(r.UShort(target.PobjOffset + 12) == before.UShort(target.PobjOffset + 12)
            && r.Pointer(target.DobjOffset + 8) == (expectedMaterial ?? before.Pointer(target.DobjOffset + 8)),
            "MODEL_MATERIAL_MISMATCH", "Vertex editing changed original material or culling state.");
    }

    private static MeshData OutputGeometry(MeshData original, MeshData mesh,
        bool reverseWinding)
    {
        if (!reverseWinding) return mesh;
        bool expanded = mesh.Positions.Length == original.TriangleIndices.Length
            && mesh.TriangleIndices.SequenceEqual(Enumerable.Range(0, mesh.Positions.Length));
        bool replaceNormals = mesh.Normals != null && original.Normals != null
            && !(expanded ? original.TriangleIndices.Select(i => original.Normals[i])
                : original.Normals).SequenceEqual(mesh.Normals);
        bool replaceColor0 = mesh.Colors0 != null && original.Colors0 != null
            && !(expanded ? original.TriangleIndices.Select(i => original.Colors0[i])
                : original.Colors0).SequenceEqual(mesh.Colors0);
        bool replaceColor1 = mesh.Colors1 != null && original.Colors1 != null
            && !(expanded ? original.TriangleIndices.Select(i => original.Colors1[i])
                : original.Colors1).SequenceEqual(mesh.Colors1);
        var corners = new List<(int Source, int Output)>();
        for (int i = 0; i < original.TriangleIndices.Length; i += 3)
            foreach (int corner in new[] { i, i + 2, i + 1 })
                corners.Add((original.TriangleIndices[corner],
                    expanded ? corner : original.TriangleIndices[corner]));
        return new(
            corners.Select(corner => mesh.Positions[corner.Output]).ToArray(),
            original.Normals == null ? null : corners.Select(corner => replaceNormals
                ? mesh.Normals![corner.Output] : original.Normals[corner.Source]).ToArray(),
            Enumerable.Range(0, corners.Count).ToArray(),
            TexCoords0: original.TexCoords0 == null ? null
                : corners.Select(corner => original.TexCoords0[corner.Source]).ToArray(),
            TexCoords1: original.TexCoords1 == null ? null
                : corners.Select(corner => original.TexCoords1[corner.Source]).ToArray(),
            Colors0: original.Colors0 == null ? null : corners.Select(corner => replaceColor0
                ? mesh.Colors0![corner.Output] : original.Colors0[corner.Source]).ToArray(),
            Colors1: original.Colors1 == null ? null : corners.Select(corner => replaceColor1
                ? mesh.Colors1![corner.Output] : original.Colors1[corner.Source]).ToArray());
    }

    private static void Put(byte[] bytes, int offset, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
    private static void Short(byte[] bytes, int offset, int value) => BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(offset, 2), checked((ushort)value));
    private static void Float(byte[] bytes, int offset, float value) => Put(bytes, offset, BitConverter.SingleToInt32Bits(value));
}
