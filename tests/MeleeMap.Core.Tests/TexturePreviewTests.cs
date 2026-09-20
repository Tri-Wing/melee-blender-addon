using System.Buffers.Binary;
using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class TexturePreviewTests
{
    [CorpusFact]
    public void ExtractsGreenGreensDiffuseAndSpecularLightmapsSeparately()
    {
        string source = Path.Combine(CorpusTests.CorpusDirectory, "GrGb.dat");
        if (!File.Exists(source)) return;
        using var directory = new TemporaryDirectory();
        var archive = new StageArchive(source);
        foreach (int mobj in new[] { 0x27E84, 0x27D30 })
        {
            var preview = TexturePreview.Extract(archive.Layout,
                new("test", "test", mobj, true), directory.Path);
            Assert.Equal(2, preview.Textures!.Length);
            Assert.Equal(new[] { 0x10, 0x20 }, preview.Textures.Select(texture => texture.LightmapFlags));
            Assert.All(preview.Textures, texture => Assert.Equal(5, texture.ColorOperation));
        }
    }

    [CorpusFact]
    public void ExtractsGreenGreensReflectionMappedMetalMaterials()
    {
        string source = Path.Combine(CorpusTests.CorpusDirectory, "GrGb.dat");
        if (!File.Exists(source)) return;
        using var directory = new TemporaryDirectory();
        var archive = new StageArchive(source);
        var reflectionOnly = TexturePreview.Extract(archive.Layout,
            new("test", "test", 0x62B8, true), directory.Path);
        Assert.Null(reflectionOnly.Warning);
        var reflectionTextures = reflectionOnly.Textures!;
        Assert.Equal([1], reflectionTextures.Select(texture => texture.CoordinateType));
        Assert.Equal([0x80], reflectionTextures.Select(texture => texture.LightmapFlags));
        Assert.Equal([4], reflectionTextures.Select(texture => texture.ColorOperation));

        var diffuseAndReflection = TexturePreview.Extract(archive.Layout,
            new("test", "test", 0x6914, true), directory.Path);
        Assert.Null(diffuseAndReflection.Warning);
        var combinedTextures = diffuseAndReflection.Textures!;
        Assert.Equal([0, 1], combinedTextures.Select(texture => texture.CoordinateType));
        Assert.Equal([0x10, 0x80], combinedTextures.Select(texture => texture.LightmapFlags));
        Assert.All(combinedTextures, texture => Assert.Equal(3, texture.ColorOperation));
    }

    [CorpusFact]
    public void ExtractsYoshisStoryWaterTextureLayersAndBothUvChannels()
    {
        string source = Path.Combine(CorpusTests.CorpusDirectory, "GrYt.dat");
        if (!File.Exists(source)) return;
        using var directory = new TemporaryDirectory();
        var archive = new StageArchive(source);
        var preview = TexturePreview.Extract(archive.Layout,
            new("water", "water", 0xB71C, true), directory.Path);
        Assert.Null(preview.Warning);
        Assert.Equal(2, preview.Textures!.Length);
        Assert.Equal(new[] { 0, 1 }, preview.Textures.Select(texture => texture.TexCoord));
        Assert.All(preview.Textures, texture => Assert.Equal(3, texture.ColorOperation));
        Assert.Equal(.548023f, preview.Textures[0].ColorBlend);
        Assert.Equal(.581921f, preview.Textures[1].ColorBlend);
        Assert.Equal(-.3333f, preview.Textures[1].Translation[0]);
        var mesh = MeleeMap.Core.Gx.GxMeshDecoder.Decode(archive.Layout, 0x16500);
        Assert.NotNull(mesh.TexCoords0); Assert.NotNull(mesh.TexCoords1);
        Assert.Equal(mesh.TexCoords0!.Length, mesh.TexCoords1!.Length);
        Assert.NotEqual(mesh.TexCoords0[0], mesh.TexCoords1[0]);
    }

    [Theory]
    [InlineData(6)] // RGBA8 split AR/GB planes, 4x4 tiles.
    [InlineData(8)] // CI4 with RGB565 palette, 8x8 tiles.
    [InlineData(14)] // CMPR subblocks.
    public void DecodesKnownPixelsAndCropsPaddedTiles(int format)
    {
        using var fixture = new Fixture(format);
        var preview = TexturePreview.Extract(fixture.Archive, new("test", "test", 0, true), fixture.Directory);
        Assert.Null(preview.Warning); Assert.NotNull(preview.Texture);
        Assert.Equal(4, preview.Texture!.ColorOperation);
        Assert.Equal(.25f, preview.Texture.ColorBlend);
        Assert.True(preview.UseVertexColor);
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
            Put(4, 2); Put(96, 4 << 16); Put(100, BitConverter.SingleToInt32Bits(.25f));
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

    private sealed class TemporaryDirectory : IDisposable
    {
        public string Path { get; } = System.IO.Path.Combine(System.IO.Path.GetTempPath(),
            "mme-water-preview-" + Guid.NewGuid().ToString("N"));
        public TemporaryDirectory() => Directory.CreateDirectory(Path);
        public void Dispose() => Directory.Delete(Path, true);
    }
}
