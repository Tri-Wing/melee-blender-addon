using System.Buffers.Binary;
using HSDRaw.GX;
using HSDRaw.Tools;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record MaterialPreview(float[] Color, PreviewTexture? Texture, string? Warning, AlphaPreview? Alpha = null, bool UseVertexColor = false);
public sealed record PreviewTexture(string File, int Width, int Height, int WrapS, int WrapT,
    int RepeatS, int RepeatT, float[] Scale, float[] Rotation, float[] Translation, int ColorOperation = 5, float ColorBlend = 1);

/// <summary>Read-only, bounded texture decoding for Blender previews; never used as DAT export input.</summary>
public static class TexturePreview
{
    public static MaterialPreview Extract(ArchiveLayout archive, ModelMaterial material, string directory)
    {
        var r = new ArchiveDataReader(archive);
        int? color = r.Pointer(material.MobjOffset + 12);
        float[] diffuse = color.HasValue ? Enumerable.Range(0, 3).Select(i => r.Byte(color.Value + 4 + i) / 255f).Append(1f).ToArray()
            : [0.45f, 0.45f, 0.45f, 1];
        var alpha = AlphaPreview.Read(archive, material.MobjOffset);
        MaterialPreview Preview(PreviewTexture? texture, string? warning = null) => new(diffuse, texture,
            string.IsNullOrEmpty(warning) ? alpha.Warning : string.IsNullOrEmpty(alpha.Warning) ? warning : warning + " " + alpha.Warning, alpha, (r.Int(material.MobjOffset + 4) & 2) != 0);
        if (!material.UsesUv) return Preview(null);
        try
        {
            int t = r.Pointer(material.MobjOffset + 8)!.Value;
            Require(r.Pointer(t) == null && r.Int(t + 12) == 4
                && (r.Int(t + 0x40) & 0x0100000F) == 0,
                "TEXTURE_PREVIEW", "Base texture requires unsupported texture coordinates or a custom class.");
            int image = r.Pointer(t + 0x4C) ?? throw new StageException("TEXTURE_PREVIEW", "Texture has no image descriptor.");
            int width = r.UShort(image + 4), height = r.UShort(image + 6), format = r.Int(image + 8);
            var (blockWidth, blockHeight, blockBytes) = format switch
            {
                0 or 8 or 14 => (8, 8, 32),
                1 or 2 or 9 => (8, 4, 32),
                3 or 4 or 5 or 10 => (4, 4, 32),
                6 => (4, 4, 64),
                _ => throw new StageException("TEXTURE_PREVIEW", $"Unsupported texture format {format}.")
            };
            Require(width is > 0 and <= 2048 && height is > 0 and <= 2048, "TEXTURE_PREVIEW", "Preview dimensions must be between 1 and 2048.");
            int paddedWidth = (width + blockWidth - 1) / blockWidth * blockWidth;
            int paddedHeight = (height + blockHeight - 1) / blockHeight * blockHeight;
            int length = paddedWidth / blockWidth * (paddedHeight / blockHeight) * blockBytes;
            byte[] Data(int field, int size)
            {
                int pointer = r.Pointer(field) ?? throw new StageException("TEXTURE_PREVIEW", "Missing image or palette buffer.");
                r.Check(pointer, size);
                int boundary = archive.Pointers.Values.Concat(archive.Roots.Select(root => root.Offset))
                    .Append(archive.DataSize).Where(offset => offset > pointer).Min();
                Require(pointer + size <= boundary, "TEXTURE_PREVIEW", "Image or palette buffer is truncated.");
                return archive.Bytes.AsSpan(32 + pointer, size).ToArray();
            }
            var encoded = Data(image, length);
            int paletteFormat = 0, count = 0; byte[] palette = [];
            if (format is 8 or 9 or 10)
            {
                int tlut = r.Pointer(t + 0x50) ?? throw new StageException("TEXTURE_PREVIEW", "Indexed texture has no palette.");
                paletteFormat = r.Int(tlut + 4); count = r.UShort(tlut + 12);
                Require(paletteFormat is >= 0 and <= 2 && count > 0 && count <= (format == 8 ? 16 : format == 9 ? 256 : 16384),
                    "TEXTURE_PREVIEW", "Unsupported texture palette.");
                palette = Data(tlut, count * 2);
            }
            // Decode full tiled dimensions, then crop padded edges. HSDRaw returns BGRA bytes.
            byte[] bgra = GXImageConverter.DecodeTPL((GXTexFmt)format, paddedWidth, paddedHeight, encoded,
                (GXTlutFmt)paletteFormat, count, palette);
            Require(bgra.Length == paddedWidth * paddedHeight * 4, "TEXTURE_PREVIEW", "Texture decoder returned an incomplete image.");
            // HSDRaw's RGB565 palette decoder left-shifts channels without bit
            // replication. Expand those five/six-bit values to the full range.
            if (format is 8 or 9 or 10 && paletteFormat == 1)
                for (int i = 0; i < bgra.Length; i += 4)
                {
                    bgra[i] |= (byte)(bgra[i] >> 5);
                    bgra[i + 1] |= (byte)(bgra[i + 1] >> 6);
                    bgra[i + 2] |= (byte)(bgra[i + 2] >> 5);
                }
            float[] Vec(int at) => [r.Float(at), r.Float(at + 4), r.Float(at + 8)];
            var scale = Vec(t + 0x1C); var rotation = Vec(t + 0x10); var translation = Vec(t + 0x28);
            int wrapS = r.Int(t + 0x34), wrapT = r.Int(t + 0x38), repeatS = r.Byte(t + 0x3C), repeatT = r.Byte(t + 0x3D);
            Require(wrapS is >= 0 and <= 2 && wrapT is >= 0 and <= 2 && repeatS > 0 && repeatT > 0
                && scale.Concat(rotation).Concat(translation).All(float.IsFinite), "TEXTURE_PREVIEW", "Unsupported texture transform or wrap mode.");
            string file = $"models/textures/{image:x8}-{r.Pointer(t + 0x50).GetValueOrDefault():x8}.tga";
            string path = Path.Combine(directory, file); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllBytes(path, Tga(width, height, paddedWidth, bgra));
            return Preview(new(file, width, height, wrapS, wrapT, repeatS, repeatT, scale, rotation, translation, (r.Int(t + 0x40) >> 16) & 15, alpha.TextureBlend),
                r.Pointer(t + 4) != null ? "Only the first base texture is previewed; additional texture layers are omitted." : null);
        }
        catch (Exception e) when (e is StageException or IndexOutOfRangeException or ArgumentException)
        { return Preview(null, $"Texture preview unavailable: {e.Message}"); }
    }

    public static byte[] Tga(int width, int height, int rowWidth, byte[] bgra)
    {
        byte[] output = new byte[18 + width * height * 4]; output[2] = 2; // Uncompressed truecolor.
        BinaryPrimitives.WriteUInt16LittleEndian(output.AsSpan(12), checked((ushort)width));
        BinaryPrimitives.WriteUInt16LittleEndian(output.AsSpan(14), checked((ushort)height));
        output[16] = 32; output[17] = 0x28; // Top-left origin, eight alpha bits.
        for (int y = 0; y < height; y++) bgra.AsSpan(y * rowWidth * 4, width * 4).CopyTo(output.AsSpan(18 + y * width * 4));
        return output;
    }
}
