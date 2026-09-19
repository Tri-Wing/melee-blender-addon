using System.Buffers.Binary;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class GxDecoderTests
{
    private static ArchiveLayout Fixture(bool badIndex = false, bool truncatedList = false)
    {
        // POBJ @0, attributes @24, terminator @48, float positions @72, display list @128.
        int[] relocations = [8, 16, 44];
        byte[] bytes = new byte[32 + 160 + relocations.Length * 4];
        void Put(int offset, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
        Put(0, bytes.Length); Put(4, 160); Put(8, relocations.Length);
        Put(32 + 8, 24); Put(32 + 16, 128);
        BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(32 + 14, 2), 1);
        Put(32 + 24, 9); Put(32 + 28, 2); Put(32 + 32, 1); Put(32 + 36, 4);
        BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(32 + 42, 2), 12);
        Put(32 + 44, 72); Put(32 + 48, 255);
        float[] positions = [0, 0, 0, 1, 0, 0, 0, 1, 0];
        for (int i = 0; i < positions.Length; i++) Put(32 + 72 + i * 4, BitConverter.SingleToInt32Bits(positions[i]));
        bytes[32 + 128] = 0x90;
        BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(32 + 129, 2), (ushort)(truncatedList ? 99 : 3));
        bytes[32 + 132] = 1; bytes[32 + 133] = (byte)(badIndex ? 255 : 2);
        for (int i = 0; i < relocations.Length; i++) Put(32 + 160 + i * 4, relocations[i]);
        return new(bytes);
    }

    [Fact]
    public void DecodesKnownIndexedTriangle()
    {
        var mesh = GxMeshDecoder.Decode(Fixture(), 0);
        Assert.Equal(new[] { new Vector3Data(0, 0, 0), new Vector3Data(1, 0, 0), new Vector3Data(0, 1, 0) }, mesh.Positions);
        Assert.Equal(new[] { 0, 1, 2 }, mesh.TriangleIndices); Assert.Null(mesh.Normals);
    }

    [Fact]
    public void RejectsAttributeIndexOutsideBuffer() => Assert.Equal("GX_ATTRIBUTE_INDEX",
        Assert.Throws<StageException>(() => GxMeshDecoder.Decode(Fixture(badIndex: true), 0)).Code);

    [Fact]
    public void RejectsTruncatedDisplayList() => Assert.Equal("GX_DISPLAY_LIST",
        Assert.Throws<StageException>(() => GxMeshDecoder.Decode(Fixture(truncatedList: true), 0)).Code);
}
