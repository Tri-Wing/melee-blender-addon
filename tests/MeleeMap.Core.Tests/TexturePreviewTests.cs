using System.Buffers.Binary;
using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class TexturePreviewTests
{
    [Theory]
    [InlineData(6)] // RGBA8 split AR/GB planes, 4x4 tiles.
    [InlineData(8)] // CI4 with RGB565 palette, 8x8 tiles.
    [InlineData(14)] // CMPR subblocks.
    public void DecodesKnownPixelsAndCropsPaddedTiles(int format)
    {
        using var fixture = new Fixture(format);
        var preview = TexturePreview.Extract(fixture.Archive, new("test", "test", 0, true), fixture.Directory);
        Assert.Null(preview.Warning); Assert.NotNull(preview.Texture);
        var image = preview.Texture!; Assert.Equal(2, image.Width); Assert.Equal(2, image.Height);
        var bytes = File.ReadAllBytes(Path.Combine(fixture.Directory, image.File));
        Assert.Equal(18 + 2 * 2 * 4, bytes.Length); Assert.Equal(0x28, bytes[17]);
        // Top-left is red; top-right is green. TGA stores BGRA, top row first.
        Assert.Equal(new byte[] { 0, 0, 255, 255, 0, 255, 0, 255 }, bytes.AsSpan(18, 8).ToArray());
    }

    [Fact]
    public void InvalidTextureFallsBackWithoutPublishingAnImage()
    {
        using var fixture = new Fixture(123);
        var preview = TexturePreview.Extract(fixture.Archive, new("test", "test", 0, true), fixture.Directory);
        Assert.Null(preview.Texture); Assert.Contains("Unsupported texture format", preview.Warning);
        Assert.Empty(Directory.GetFiles(fixture.Directory, "*", SearchOption.AllDirectories));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(1)]
    public void IntensityTexturesDecodeBlackAsTransparentAndWhiteAsOpaque(int format)
    {
        using var fixture = new Fixture(format);
        var preview = TexturePreview.Extract(fixture.Archive, new("test", "test", 0, true), fixture.Directory);
        var bytes = File.ReadAllBytes(Path.Combine(fixture.Directory, preview.Texture!.File));
        Assert.Equal(new byte[] { 0, 0, 0, 0, 255, 255, 255, 255 }, bytes.AsSpan(18, 8).ToArray());
    }

    private sealed class Fixture : IDisposable
    {
        public string Directory { get; } = Path.Combine(Path.GetTempPath(), "mme-texture-" + Guid.NewGuid().ToString("N"));
        public ArchiveLayout Archive { get; }
        public Fixture(int format)
        {
            System.IO.Directory.CreateDirectory(Directory);
            int[] pointers = format == 8 ? [8, 108, 128, 112, 300] : [8, 108, 128];
            const int dataSize = 384;
            byte[] bytes = new byte[32 + dataSize + pointers.Length * 4];
            void Put(int at, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(32 + at), value);
            void Short(int at, int value) => BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(32 + at), (ushort)value);
            Put(-32, bytes.Length); Put(-28, dataSize); Put(-24, pointers.Length);
            Put(8, 32); Put(44, 4); Put(108, 128); Put(128, 192); Short(132, 2); Short(134, 2); Put(136, format);
            foreach (int at in new[] { 32 + 0x1C, 32 + 0x20, 32 + 0x24 }) Put(at, BitConverter.SingleToInt32Bits(1));
            bytes[32 + 32 + 0x3C] = 1; bytes[32 + 32 + 0x3D] = 1;
            if (format == 0) bytes[32 + 192] = 0x0F;
            else if (format == 1) bytes[32 + 193] = 255;
            else if (format == 6)
            {
                // Red then green in the first tile's top row.
                bytes[32 + 192] = 255; bytes[32 + 193] = 255;
                bytes[32 + 194] = 255; bytes[32 + 192 + 34] = 255;
            }
            else if (format == 8)
            {
                Put(112, 300); Put(300, 336); Put(304, 1); Short(312, 2);
                Short(336, 0xF800); Short(338, 0x07E0);
                bytes[32 + 192] = 0x01;
            }
            else if (format == 14)
            {
                Short(192, 0xF800); Short(194, 0x07E0);
                bytes[32 + 196] = 0x10; // selectors 0,1,0,0.
            }
            for (int i = 0; i < pointers.Length; i++) Put(dataSize + i * 4, pointers[i]);
            Archive = new(bytes);
        }
        public void Dispose() => System.IO.Directory.Delete(Directory, true);
    }
}
