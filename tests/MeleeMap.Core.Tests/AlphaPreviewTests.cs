using System.Buffers.Binary;
using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class AlphaPreviewTests
{
    [Theory]
    [InlineData(0, false, false)]
    [InlineData(2, true, false)]
    [InlineData(0x2002, false, false)]
    [InlineData(0x4001, true, false)]
    [InlineData(0x6001, true, true)]
    public void ReadsMaterialVertexAndCombinedAlpha(int flags, bool vertex, bool combined)
    {
        var preview = AlphaPreview.Read(Fixture(flags, false), 0);
        Assert.Equal(.25f, preview.Material);
        Assert.Equal(vertex, preview.Vertex);
        Assert.Equal(combined, preview.MultiplyMaterial);
        Assert.Equal(3, preview.TextureOperation);
        Assert.Equal(.5f, preview.TextureBlend);
        Assert.Equal(0, preview.BlendMode);
        Assert.Equal(7, preview.Compare0);
    }

    [Fact]
    public void ReadsExplicitPixelProcessingAndDefaults()
    {
        var explicitState = AlphaPreview.Read(Fixture(0, true), 0);
        Assert.Equal((1, 4, 1), (explicitState.BlendMode, explicitState.SourceFactor, explicitState.DestinationFactor));
        Assert.Equal((4, 128, 1, 1, 32), (explicitState.Compare0, explicitState.Reference0,
            explicitState.Operation, explicitState.Compare1, explicitState.Reference1));
        Assert.Null(explicitState.Warning);
        var defaults = AlphaPreview.Read(Fixture(0x40000000, false), 0);
        Assert.Equal((1, 4, 5), (defaults.BlendMode, defaults.SourceFactor, defaults.DestinationFactor));
        Assert.Equal(4, defaults.Compare0);
        Assert.Equal(0, defaults.Reference0);
    }

    private static ArchiveLayout Fixture(int flags, bool pixelState)
    {
        int[] relocations = pixelState ? [8, 12, 20] : [8, 12];
        byte[] bytes = new byte[32 + 160 + relocations.Length * 4];
        void Put(int at, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(32 + at), value);
        Put(-32, bytes.Length); Put(-28, 160); Put(-24, relocations.Length);
        Put(4, flags); Put(8, 64); Put(12, 24); Put(36, BitConverter.SingleToInt32Bits(.25f));
        Put(128, 3 << 20); Put(132, BitConverter.SingleToInt32Bits(.5f));
        if (pixelState)
        {
            Put(20, 48);
            byte[] pe = [0, 128, 32, 0, 1, 4, 1, 0, 3, 4, 1, 1];
            pe.CopyTo(bytes, 32 + 48);
        }
        for (int i = 0; i < relocations.Length; i++) Put(160 + 4 * i, relocations[i]);
        return new(bytes);
    }
}
