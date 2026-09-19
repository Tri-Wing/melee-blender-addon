using System.Security.Cryptography;
using HSDRaw;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record CollisionInventory(int Vertices, int Lines, int Joints, int DynamicLines);
public sealed record StageInventory(string Filename, string Sha256, string DatVersion,
    IReadOnlyList<RootInfo> Roots, IReadOnlyList<RootInfo> References, bool IsStage,
    int? ModelGroups, CollisionInventory? Collision);

public sealed class StageArchive
{
    public ArchiveLayout Layout { get; }
    public HSDRawFile File { get; }
    public string Path { get; }

    public StageArchive(string path)
    {
        Path = System.IO.Path.GetFullPath(path);
        Layout = new ArchiveLayout(System.IO.File.ReadAllBytes(Path));
        File = new HSDRawFile(Layout.Bytes);
    }

    public StageInventory Inspect()
    {
        var map = Layout.Roots.Find(r => r.Name == "map_head");
        var collision = Layout.Roots.Find(r => r.Name == "coll_data");
        int? groups = map == null ? null : Count(map.Offset, 8, 12, 0x34);
        CollisionInventory? counts = collision == null ? null : new(
            Count(collision.Offset, 0, 4, 8), Count(collision.Offset, 8, 12, 16), Count(collision.Offset, 0x24, 0x28, 0x28),
            Short(collision.Offset + 0x22));
        return new(System.IO.Path.GetFileName(Path), Convert.ToHexString(SHA256.HashData(Layout.Bytes)).ToLowerInvariant(),
            Layout.Version, Layout.Roots, Layout.References, map != null, groups, counts);
    }

    private int Short(int offset) => System.Buffers.Binary.BinaryPrimitives.ReadInt16BigEndian(Layout.Bytes.AsSpan(32 + offset, 2));
    private int Int(int offset) => Layout.Read(32 + offset);
    private float Float(int offset) => BitConverter.Int32BitsToSingle(Int(offset));

    private int Count(int owner, int pointer, int countOffset, int stride)
    {
        Require(owner <= Layout.DataSize - countOffset - 4, "STAGE_STRUCTURE", "Stage structure is truncated.");
        int count = Int(owner + countOffset);
        int target = Int(owner + pointer);
        // Relocation targets can point inside arrays. The first HSDStruct fragment is not their full extent.
        Require(count >= 0 && (count == 0 || Layout.Pointers.ContainsKey(owner + pointer)
            && (long)target + (long)count * stride <= Layout.DataSize),
            "STAGE_COUNT", $"Count at 0x{owner + countOffset:X} exceeds its buffer.");
        return count;
    }

    /// <summary>Initial validation tier: archive bounds, model hierarchy/lists, counted buffers, collision indices/ranges.</summary>
    public void Validate()
    {
        foreach (var name in new[] { "map_head", "coll_data", "grGroundParam" })
            Require(File[name] != null, "STAGE_ROOT_MISSING", $"Required stage root '{name}' is missing.");
        var info = Inspect();
        ModelIdentity.Capture(Layout, new ModelIdentityCatalog());
        int c = Layout.Roots.Single(r => r.Name == "coll_data").Offset;
        // Retail archives commonly serialize 0x2C bytes; the decomp's x2C field is inferred.
        int vertices = info.Collision!.Vertices, lines = info.Collision.Lines;
        int vs = Int(c), ls = Int(c + 8);
        for (int i = 0; i < vertices; i++)
            Require(float.IsFinite(Float(vs + i * 8)) && float.IsFinite(Float(vs + i * 8 + 4)),
                "COLLISION_NONFINITE", $"Vertex {i} is not finite.");
        for (int i = 0; i < lines; i++)
        {
            int line = ls + i * 16;
            Require(Short(line) >= 0 && Short(line) < vertices && Short(line + 2) >= 0 && Short(line + 2) < vertices,
                "COLLISION_VERTEX", $"Line {i} has an invalid vertex index.");
            foreach (int offset in new[] { 4, 6, 8, 10 })
            {
                int link = Short(line + offset);
                Require(link >= -1 && link < lines, "COLLISION_LINK", $"Line {i} has an invalid link at 0x{offset:X}.");
            }
        }
        var used = new bool[lines];
        for (int i = 0; i < 5; i++)
        {
            int start = Short(c + 16 + i * 4), count = Short(c + 18 + i * 4);
            Require(count >= 0 && (count == 0 || start >= 0 && start + count <= lines), "COLLISION_RANGE", $"Category {i} is out of bounds.");
            for (int j = start; j < start + count; j++)
            {
                Require(!used[j], "COLLISION_RANGE_OVERLAP", $"Line {j} belongs to multiple categories.");
                used[j] = true;
            }
        }
        Require(used.All(x => x), "COLLISION_RANGE_MISSING", "Some lines have no category.");
    }

    public void Roundtrip(string output, bool compare)
    {
        output = System.IO.Path.GetFullPath(output);
        Require(!System.IO.File.Exists(output), "OUTPUT_EXISTS", "Output already exists; choose a new filename.");
        Require(output != Path, "OUTPUT_SOURCE", "Output must differ from source.");
        Validate();
        string temporary = output + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            File.SavePreserving(temporary);
            var reloaded = new StageArchive(temporary);
            reloaded.Validate();
            if (compare)
                Require(Layout.SemanticHash() == reloaded.Layout.SemanticHash(), "ROUNDTRIP_MISMATCH", "Archive topology or untouched payload changed.");
            System.IO.File.Move(temporary, output);
        }
        finally { if (System.IO.File.Exists(temporary)) System.IO.File.Delete(temporary); }
    }
}
