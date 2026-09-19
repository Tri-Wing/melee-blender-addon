using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core.Gx;

public readonly record struct Vector2Data(float X, float Y);
public readonly record struct Vector3Data(float X, float Y, float Z);
public sealed record EnvelopeWeight(int JobjSourceOffset, float Weight);
public sealed record MeshData(Vector3Data[] Positions, Vector3Data[]? Normals, int[] TriangleIndices,
    int[]? EnvelopeIndices = null, EnvelopeWeight[][]? Envelopes = null, int? BoundJobjSourceOffset = null, Vector2Data[]? TexCoords0 = null);

/// <summary>Bounded, headless decoder for rigid and enveloped GX triangle geometry. Bindings remain explicit.</summary>
public static class GxMeshDecoder
{
    internal sealed record Attribute(int Name, int Type, int Components, int Format, int Fraction, int Stride, int? Buffer);

    public static MeshData Decode(ArchiveLayout archive, int polygon)
    {
        var r = new ArchiveDataReader(archive); r.Check(polygon, 0x18);
        int flags = r.UShort(polygon + 12);
        Require((flags & 0x1004) == 0, "GX_UNSUPPORTED_BINDING", "Shape animation and joint/parent matrix blending are not supported yet.");
        bool enveloped = (flags & 0x2000) != 0;
        int? binding = r.Pointer(polygon + 20);
        var envelopes = new List<EnvelopeWeight[]>();
        if (enveloped)
        {
            Require(binding.HasValue, "GX_ENVELOPE", "Enveloped polygon has no weight table.");
            for (int field = binding!.Value; ; field += 4)
            {
                int? weights = r.Pointer(field);
                if (!weights.HasValue) break;
                var entries = new List<EnvelopeWeight>();
                for (int entry = weights.Value; ; entry += 8)
                {
                    int? joint = r.Pointer(entry);
                    if (!joint.HasValue) break;
                    r.Check(joint.Value, 0x40);
                    float weight = r.Float(entry + 4);
                    Require(float.IsFinite(weight) && weight >= 0, "GX_ENVELOPE", "Invalid skin weight.");
                    entries.Add(new(joint.Value, weight));
                }
                Require(entries.Count > 0 && MathF.Abs(entries.Sum(e => e.Weight) - 1) <= 0.001f,
                    "GX_ENVELOPE", "Envelope weights must sum to one.");
                envelopes.Add(entries.ToArray());
            }
            Require(envelopes.Count > 0, "GX_ENVELOPE", "Empty envelope table.");
        }
        else if (binding.HasValue) r.Check(binding.Value, 0x40);
        int? attributes = r.Pointer(polygon + 8), display = r.Pointer(polygon + 16);
        Require(attributes.HasValue && display.HasValue, "GX_POINTER", "Polygon is missing its attributes or display list.");
        var attrs = new List<Attribute>(); var names = new HashSet<int>();
        int at = attributes!.Value;
        while (true)
        {
            r.Check(at, 24); int name = r.Int(at);
            if (name == 255) break;
            Require(attrs.Count < 21 && name >= 0 && name <= 20 && names.Add(name), "GX_ATTRIBUTE", "Unsupported or duplicate GX attribute.");
            var attr = new Attribute(name, r.Int(at + 4), r.Int(at + 8), r.Int(at + 12), r.Byte(at + 16), r.UShort(at + 18), r.Pointer(at + 20));
            Require(attr.Type is >= 1 and <= 3, "GX_ATTRIBUTE", "Unsupported GX attribute storage type.");
            Require(name >= 9 || attr.Type == 1, "GX_ATTRIBUTE", "Matrix indices must be direct bytes.");
            Require(name != 0 || enveloped, "GX_UNSUPPORTED_BINDING", "Position matrix index has no envelope binding.");
            Require(name != 10 || attr.Components == 0, "GX_ATTRIBUTE", "NBT normals are not supported yet.");
            if (name is 9 or 10)
                Require(attr.Format is >= 0 and <= 4 && (name == 10 || attr.Components is 0 or 1), "GX_ATTRIBUTE", "Unsupported position/normal encoding.");
            if (attr.Type != 1) Require(attr.Buffer.HasValue && attr.Stride > 0, "GX_ATTRIBUTE", "Indexed attribute has no buffer/stride.");
            attrs.Add(attr); at += 24;
        }
        Require(names.Contains(9), "GX_POSITION", "Mesh has no position attribute.");
        Require(!enveloped || names.Contains(0), "GX_ENVELOPE", "Enveloped polygon has no position matrix indices.");
        int length = r.UShort(polygon + 14) * 32;
        int cursor = display!.Value, end = cursor + length; r.Check(cursor, length);
        var positions = new List<Vector3Data>(); var normals = names.Contains(10) ? new List<Vector3Data>() : null;
        var texCoords = names.Contains(13) ? new List<Vector2Data>() : null;
        var triangles = new List<int>();
        var envelopeIndices = enveloped ? new List<int>() : null;
        var boundaries = archive.Pointers.Values.Concat(archive.Roots.Select(x => x.Offset)).Append(archive.DataSize).Distinct().Order().ToArray();
        while (cursor < end)
        {
            byte command = ReadByte();
            if (command == 0)
            {
                while (cursor < end) Require(ReadByte() == 0, "GX_COMMAND", "Nonzero data follows display-list padding.");
                break;
            }
            Require(command is 0x80 or 0x90 or 0x98 or 0xA0, "GX_PRIMITIVE", $"Unsupported GX command 0x{command:X2}.");
            Need(2); int count = r.UShort(cursor); cursor += 2;
            int first = positions.Count;
            for (int i = 0; i < count; i++)
                foreach (var attr in attrs)
                {
                    int size = ElementSize(attr);
                    int offset;
                    if (attr.Type == 1) { Need(size); offset = cursor; cursor += size; }
                    else
                    {
                        int index;
                        if (attr.Type == 2) index = ReadByte();
                        else { Need(2); index = r.UShort(cursor); cursor += 2; }
                        long address = attr.Buffer!.Value + (long)index * attr.Stride;
                        int boundary = boundaries.First(x => x > attr.Buffer.Value);
                        Require(size <= attr.Stride && address + size <= boundary, "GX_ATTRIBUTE_INDEX", $"Attribute {attr.Name}, index {index} exceeds its buffer.");
                        offset = (int)address;
                    }
                    if (attr.Name == 0)
                    {
                        int matrix = r.Byte(offset);
                        Require(matrix % 3 == 0 && matrix / 3 < envelopes.Count, "GX_ENVELOPE_INDEX", "Position matrix index exceeds the envelope table.");
                        envelopeIndices!.Add(matrix / 3);
                    }
                    if (attr.Name == 9) positions.Add(ReadVector(attr, offset));
                    if (attr.Name == 10) normals!.Add(ReadVector(attr, offset));
                    if (attr.Name == 13)
                    {
                        var uv = ReadVector(attr, offset); texCoords!.Add(new(uv.X, uv.Y));
                    }
                }
            triangles.AddRange(Triangulate(command, first, count));
        }
        Require(triangles.Count > 0, "GX_EMPTY_MESH", "No triangles decoded from polygon.");
        return new(positions.ToArray(), normals?.ToArray(), triangles.ToArray(), envelopeIndices?.ToArray(),
            enveloped ? envelopes.ToArray() : null, enveloped ? null : binding, texCoords?.ToArray());

        void Need(int bytes) => Require(cursor <= end - bytes, "GX_DISPLAY_LIST", "Truncated display-list primitive.");
        byte ReadByte() { Need(1); return r.Byte(cursor++); }
        Vector3Data ReadVector(Attribute attr, int offset)
        {
            int count = attr.Name == 13 ? attr.Components + 1 : attr.Name == 9 && attr.Components == 0 ? 2 : 3;
            int size = attr.Format < 2 ? 1 : attr.Format < 4 ? 2 : 4;
            float Component(int n)
            {
                if (n >= count) return 0;
                int p = offset + n * size;
                float value = attr.Format switch { 0 => r.Byte(p), 1 => (sbyte)r.Byte(p), 2 => r.UShort(p), 3 => r.Short(p), _ => r.Float(p) };
                // GX normals use fixed fractional precision; the position/UV fraction is configurable.
                int fraction = attr.Name == 10 ? (attr.Format < 2 ? 6 : 14) : attr.Fraction;
                if (attr.Format != 4) value = MathF.ScaleB(value, -fraction);
                Require(float.IsFinite(value), "GX_NONFINITE", "Mesh attribute is not finite."); return value;
            }
            return new(Component(0), Component(1), Component(2));
        }
    }

    internal static int ElementSize(Attribute attr)
    {
        if (attr.Name < 9) return 1;
        if (attr.Name is 11 or 12)
            return attr.Format switch { 0 or 3 => 2, 1 or 4 => 3, 2 or 5 => 4, _ => throw new StageException("GX_ATTRIBUTE", "Unsupported color format.") };
        Require(attr.Format is >= 0 and <= 4, "GX_ATTRIBUTE", "Unsupported component format.");
        int count = attr.Name == 10 ? 3 : attr.Name == 9 ? attr.Components + 2 : attr.Components + 1;
        Require(count is >= 1 and <= 3, "GX_ATTRIBUTE", "Invalid component count.");
        return count * (attr.Format < 2 ? 1 : attr.Format < 4 ? 2 : 4);
    }

    public static int[] Triangulate(int command, int first, int count)
    {
        Require(count >= 3 && first >= 0, "GX_PRIMITIVE_COUNT", "Triangle primitive has too few vertices.");
        var result = new List<int>();
        void Add(int a, int b, int c) { result.Add(first + a); result.Add(first + b); result.Add(first + c); }
        switch (command)
        {
            case 0x90:
                Require(count % 3 == 0, "GX_PRIMITIVE_COUNT", "Triangle vertex count is not divisible by three.");
                for (int i = 0; i < count; i += 3) Add(i, i + 1, i + 2); break;
            case 0x80:
                Require(count % 4 == 0, "GX_PRIMITIVE_COUNT", "Quad vertex count is not divisible by four.");
                for (int i = 0; i < count; i += 4) { Add(i, i + 1, i + 2); Add(i, i + 2, i + 3); } break;
            case 0x98:
                for (int i = 2; i < count; i++) { if (i % 2 == 0) Add(i - 2, i - 1, i); else Add(i - 1, i - 2, i); } break;
            case 0xA0:
                for (int i = 2; i < count; i++) Add(0, i - 1, i); break;
            default: throw new StageException("GX_PRIMITIVE", "Unsupported primitive type.");
        }
        return result.ToArray();
    }
}
