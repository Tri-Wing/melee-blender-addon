using System.Buffers.Binary;
using HSDRaw;
using HSDRaw.Melee.Gr;
using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class ArchiveTests
{
    public static byte[] Synthetic()
    {
        byte[] bytes = new byte[52];
        void Put(int offset, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
        Put(0, bytes.Length); Put(4, 8); Put(8, 1); Put(12, 0);
        Put(32, 4); Put(36, 123); Put(40, 0);
        return bytes;
    }

    [Fact]
    public void RejectsTruncatedHeader() => Assert.Equal("ARCHIVE_HEADER",
        Assert.Throws<StageException>(() => new ArchiveLayout(new byte[8])).Code);

    [Fact]
    public void RejectsOutOfBoundsPointerBeforeHsdRaw()
    {
        var bytes = Synthetic();
        BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(32, 4), 8);
        Assert.Equal("RELOCATION_TARGET", Assert.Throws<StageException>(() => new ArchiveLayout(bytes)).Code);
    }

    [Fact]
    public void SemanticComparisonDetectsChangedOpaquePayload()
    {
        var bytes = Synthetic();
        string hash = new ArchiveLayout(bytes).SemanticHash();
        bytes[39] ^= 1;
        Assert.NotEqual(hash, new ArchiveLayout(bytes).SemanticHash());
    }

    [Fact]
    public void PreservationKeepsOpaquePrefixUnalignedTargetsAndRootInventory()
    {
        byte[] bytes = new byte[65];
        void Put(int offset, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
        Put(0, bytes.Length); Put(4, 16); Put(8, 1); Put(12, 1);
        Put(32, 0x12345678); // Opaque bytes before any root/target.
        Put(36, 10); // Root at +4 points at an unaligned target at +10.
        bytes[40] = 42; bytes[42] = 99;
        Put(48, 4); Put(52, 4); Put(56, 0);
        System.Text.Encoding.ASCII.GetBytes("root\0").CopyTo(bytes, 60);
        string output = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".dat");
        try
        {
            new HSDRawFile(bytes).SavePreserving(output);
            var saved = new ArchiveLayout(File.ReadAllBytes(output));
            Assert.Equal(new ArchiveLayout(bytes).SemanticHash(), saved.SemanticHash());
            Assert.Single(saved.Roots);
            Assert.Equal(10, saved.Pointers[4]);
        }
        finally { File.Delete(output); }
    }

    [Fact]
    public void SchemaFieldsUseEngineOffsetsAndStrides()
    {
        var group = new SBM_Map_GOBJ
        {
            AnimationFlags = new HSDByteArray { Array = [0, 1, 0] },
            JOBJIndices = new HSDShortArray { Array = [2, 17, -1] }
        };
        Assert.Equal(3, group.JOBJIndexCount);
        Assert.Equal(6, group._s.GetReference<HSDShortArray>(0x2C)._s.Length);
        Assert.Equal(17, group.JOBJIndices[1]);
        Assert.Equal(1, group._s.GetReference<HSDByteArray>(0x28)[1]);
        var collision = new SBM_Coll_Data { Unknown2C = 0x12345678 };
        Assert.Equal(0x30, collision.TrimmedSize);
        Assert.Equal(0x12345678, collision._s.GetInt32(0x2C));
    }
}
