using System.Buffers.Binary;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class ModelPositionWriterTests
{
    [Theory]
    [InlineData(1, 0)] // Direct float XY (a move may add depth).
    [InlineData(1, 1)] // Direct float XYZ.
    [InlineData(2, 1)] // Indexed8 fixed-point XYZ, aliased source positions.
    [InlineData(3, 1)] // Indexed16 fixed-point XYZ, aliased source positions.
    public void PreservesMixedAttributeTokensAndSharedBuffers(int positionType, int components)
    {
        const int dataSize = 768, polygon = 32, descriptors = 64, display = 512;
        int[] pointers = [8, 12, polygon + 8, polygon + 16, descriptors + 24 + 20, descriptors + 72 + 20];
        if (positionType != 1) pointers = [.. pointers, descriptors + 20];
        byte[] bytes = new byte[32 + dataSize + pointers.Length * 4];
        void Put(int field, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(32 + field), value);
        void Short(int field, int value) => BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(32 + field), (ushort)value);
        void Float(int field, float value) => Put(field, BitConverter.SingleToInt32Bits(value));
        Put(-32, bytes.Length); Put(-28, dataSize); Put(-24, pointers.Length);
        Put(8, 672); Put(12, polygon); Put(polygon + 8, descriptors); Put(polygon + 16, display);
        Short(polygon + 12, 0x8000); Short(polygon + 14, 4);
        void Attr(int at, int name, int type, int count, int format, int stride, int buffer)
        { Put(at, name); Put(at + 4, type); Put(at + 8, count); Put(at + 12, format); Short(at + 18, stride); Put(at + 20, buffer); }
        Attr(descriptors, 9, positionType, components, positionType == 1 ? 4 : 3, 6, positionType == 1 ? 0 : 192);
        bytes[32 + descriptors + 16] = 4;
        Attr(descriptors + 24, 10, 3, 0, 4, 12, 256);
        Attr(descriptors + 48, 11, 1, 1, 5, 4, 0); // Direct RGBA8.
        Attr(descriptors + 72, 13, 2, 1, 4, 8, 384); // Indexed UVs.
        Put(descriptors + 96, 255);
        Short(192, 16); Short(194, 32); Short(196, 48);
        for (int i = 0; i < 3; i++) { Float(256 + i * 12 + 8, 1); Float(384 + i * 8, i * 0.5f); }
        bytes[32 + display] = 0x90; Short(display + 1, 3);
        int cursor = display + 3;
        int positionBytes = positionType == 1 ? (components + 2) * 4 : positionType == 2 ? 1 : 2;
        for (int i = 0; i < 3; i++)
        {
            if (positionType == 1)
                for (int k = 0; k < components + 2; k++) Float(cursor + k * 4, i + k);
            cursor += positionBytes;
            Short(cursor, i); cursor += 2;
            Put(cursor, unchecked((int)(0x102030FFu + (uint)(i << 16)))); cursor += 4;
            bytes[32 + cursor++] = (byte)i;
        }
        for (int i = 0; i < pointers.Length; i++) Put(dataSize + i * 4, pointers[i]);
        var source = new ArchiveLayout(bytes);
        var original = GxMeshDecoder.Decode(source, polygon);
        Vector3Data[] moved = [new(1.25f, 2, 3), new(4, 5, 6), new(7, 8, 9)];
        var expected = original with { Positions = moved };
        var target = new EditableModel("test", 0, 0, 0, 0, polygon, 0);
        var output = new ArchiveLayout(ModelPositionWriter.Write(source, target, expected));
        ModelPositionWriter.Verify(output, source, target, expected);
        var r = new ArchiveDataReader(output);
        int newAttrs = r.Pointer(polygon + 8)!.Value, newDl = r.Pointer(polygon + 16)!.Value;
        Assert.Equal(bytes.AsSpan(32 + descriptors + 24, 72).ToArray(), output.Bytes.AsSpan(32 + newAttrs + 24, 72).ToArray());
        for (int i = 0; i < 3; i++)
        {
            // Independently compare normal indices, vertex colors and UV indices.
            int oldToken = display + 3 + i * (positionBytes + 7) + positionBytes;
            int newToken = newDl + 3 + i * 19 + 12;
            Assert.Equal(bytes.AsSpan(32 + oldToken, 7).ToArray(), output.Bytes.AsSpan(32 + newToken, 7).ToArray());
        }
        for (int i = 0; i < dataSize; i++)
            if (!(i >= polygon + 8 && i < polygon + 12 || i >= polygon + 14 && i < polygon + 20))
                Assert.Equal(bytes[32 + i], output.Bytes[32 + i]);
        // Every cloned indexed attribute retains a relocation to the original buffer.
        Assert.Equal(256, output.Pointers[newAttrs + 24 + 20]);
        Assert.Equal(384, output.Pointers[newAttrs + 72 + 20]);
        Assert.False(output.Pointers.ContainsKey(newAttrs + 20));

        // A reflected transform reverses triangle winding while carrying every
        // source corner's attributes to the matching reflected corner.
        var reflectedNormals = original.Normals!.Select((_, i) =>
            new Vector3Data(i == 0 ? 1 : 0, i == 1 ? 1 : 0,
                i == 2 ? 1 : 0)).ToArray();
        var reflected = original with { Positions = moved, Normals = reflectedNormals };
        var reflectedOutput = new ArchiveLayout(ModelPositionWriter.Write(source,
            target, reflected, reverseWinding: true));
        ModelPositionWriter.Verify(reflectedOutput, source, target, reflected,
            reverseWinding: true);
        var decoded = GxMeshDecoder.Decode(reflectedOutput, polygon);
        int[] order = [0, 2, 1];
        Assert.Equal(order.Select(i => moved[i]), decoded.Positions);
        Assert.Equal(order.Select(i => reflectedNormals[i]), decoded.Normals!);
        Assert.Equal(order.Select(i => original.TexCoords0![i]), decoded.TexCoords0!);
        Assert.Equal(order.Select(i => original.Colors0![i]), decoded.Colors0!);
        Assert.Equal(new[] { 0, 1, 2 }, decoded.TriangleIndices);

        var painted = new[] {
            new ColorData(1, 0, 0, 1), new ColorData(0, 1, 0, 1),
            new ColorData(0, 0, 1, 1)
        };
        var expanded = reflected with {
            Positions = original.TriangleIndices.Select(i => moved[i]).ToArray(),
            Normals = original.TriangleIndices.Select(i => reflectedNormals[i]).ToArray(),
            TriangleIndices = Enumerable.Range(0, original.TriangleIndices.Length).ToArray(),
            Colors0 = painted
        };
        var paintedOutput = new ArchiveLayout(ModelPositionWriter.Write(source,
            target, expanded, reverseWinding: true));
        ModelPositionWriter.Verify(paintedOutput, source, target, expanded,
            reverseWinding: true);
        decoded = GxMeshDecoder.Decode(paintedOutput, polygon);
        Assert.Equal(order.Select(i => painted[i]), decoded.Colors0!);
    }
}
