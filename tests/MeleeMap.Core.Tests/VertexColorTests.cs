using System.Buffers.Binary;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class VertexColorTests
{
    [Theory]
    [InlineData(0, 1)] [InlineData(1, 1)] [InlineData(2, 1)]
    [InlineData(3, 1)] [InlineData(4, 1)] [InlineData(5, 1)]
    [InlineData(0, 2)] [InlineData(1, 2)] [InlineData(2, 2)]
    [InlineData(3, 2)] [InlineData(4, 2)] [InlineData(5, 2)]
    [InlineData(0, 3)] [InlineData(1, 3)] [InlineData(2, 3)]
    [InlineData(3, 3)] [InlineData(4, 3)] [InlineData(5, 3)]
    public void DecodesBothChannelsWithDirectAndIndexedStorage(int format, int storage)
    {
        byte[] encoded = format switch
        {
            0 => [0xFC, 0x00], // R31 G32 B0 => 255,130,0
            1 => [255, 130, 0],
            2 => [255, 130, 0, 0], // X must not become alpha.
            3 => [0xF8, 0x04], // R15 G8 B0 A4
            4 => [0xFE, 0x00, 0x10], // R63 G32 B0 A16
            _ => [255, 130, 0, 65]
        };
        var expected = format switch
        {
            3 => new ColorData(1, 136 / 255f, 0, 68 / 255f),
            4 or 5 => new ColorData(1, 130 / 255f, 0, 65 / 255f),
            _ => new ColorData(1, 130 / 255f, 0, 1)
        };
        int[] relocations = storage == 1 ? [8, 16] : [8, 16, 68, 92];
        byte[] bytes = new byte[32 + 256 + relocations.Length * 4];
        void Put(int field, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(32 + field), value);
        void Short(int field, int value) => BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(32 + field), (ushort)value);
        Put(-32, bytes.Length); Put(-28, 256); Put(-24, relocations.Length);
        Put(8, 24); Put(16, 160); Short(14, 3);
        Put(24, 9); Put(28, 1); Put(32, 1); Put(36, 4);
        for (int channel = 0; channel < 2; channel++)
        {
            int at = 48 + channel * 24;
            Put(at, 11 + channel); Put(at + 4, storage); Put(at + 8, format < 3 ? 0 : 1); Put(at + 12, format);
            if (storage != 1) { Short(at + 18, encoded.Length); Put(at + 20, 128); }
        }
        Put(96, 255); encoded.CopyTo(bytes, 32 + 128);
        bytes[32 + 160] = 0x90; Short(161, 3);
        int cursor = 163;
        for (int vertex = 0; vertex < 3; vertex++)
        {
            Put(cursor, BitConverter.SingleToInt32Bits(vertex == 1 ? 1 : 0));
            Put(cursor + 4, BitConverter.SingleToInt32Bits(vertex == 2 ? 1 : 0)); cursor += 12;
            for (int channel = 0; channel < 2; channel++)
                if (storage == 1) { encoded.CopyTo(bytes, 32 + cursor); cursor += encoded.Length; }
                else cursor += storage == 2 ? 1 : 2;
        }
        for (int i = 0; i < relocations.Length; i++) Put(256 + 4 * i, relocations[i]);
        var decoded = GxMeshDecoder.Decode(new ArchiveLayout(bytes), 0);
        Assert.Equal(3, decoded.Colors0!.Length);
        Assert.Equal(3, decoded.Colors1!.Length);
        Assert.All(decoded.Colors0, c => Assert.Equal(expected, c));
        Assert.Equal(decoded.Colors0, decoded.Colors1);
    }
}
