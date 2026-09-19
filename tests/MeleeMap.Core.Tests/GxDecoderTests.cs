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

    private static ArchiveLayout EnvelopedFixture(int matrix = 0, float weight = 1)
    {
        int[] relocations = [8, 16, 20, 68, 192, 208];
        byte[] bytes = new byte[32 + 320 + relocations.Length * 4];
        void Put(int field, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(32 + field, 4), value);
        Put(-32, bytes.Length); Put(-28, 320); Put(-24, relocations.Length);
        Put(8, 24); Put(16, 144); Put(20, 192);
        BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(32 + 12), 0x2000);
        BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(32 + 14), 1);
        Put(24, 0); Put(28, 1); // direct PNMTXIDX
        Put(48, 9); Put(52, 2); Put(56, 1); Put(60, 4); Put(68, 96);
        BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(32 + 66), 12); Put(72, 255);
        float[] points = [0, 0, 0, 1, 0, 0, 0, 1, 0];
        for (int i = 0; i < points.Length; i++) Put(96 + i * 4, BitConverter.SingleToInt32Bits(points[i]));
        bytes[32 + 144] = 0x90;
        BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(32 + 145), 3);
        for (int i = 0; i < 3; i++) { bytes[32 + 147 + i * 2] = (byte)matrix; bytes[32 + 148 + i * 2] = (byte)i; }
        Put(192, 208); Put(208, 256); Put(212, BitConverter.SingleToInt32Bits(weight));
        for (int i = 0; i < relocations.Length; i++) Put(320 + i * 4, relocations[i]);
        return new(bytes);
    }

    [Fact]
    public void ExtractsSkinBindingsWithoutBakingOrDiscardingThem()
    {
        var mesh = GxMeshDecoder.Decode(EnvelopedFixture(), 0);
        Assert.Equal(new[] { 0, 0, 0 }, mesh.EnvelopeIndices);
        Assert.Equal(new EnvelopeWeight(256, 1), Assert.Single(Assert.Single(mesh.Envelopes!)));
        Assert.Equal(new Vector3Data(1, 0, 0), mesh.Positions[1]);
    }

    [Fact]
    public void RejectsInvalidSkinMatricesAndWeights()
    {
        Assert.Equal("GX_ENVELOPE_INDEX", Assert.Throws<StageException>(() => GxMeshDecoder.Decode(EnvelopedFixture(matrix: 3), 0)).Code);
        Assert.Equal("GX_ENVELOPE", Assert.Throws<StageException>(() => GxMeshDecoder.Decode(EnvelopedFixture(weight: 0.5f), 0)).Code);
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
