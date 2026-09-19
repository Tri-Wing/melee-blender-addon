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

    /// <summary>Validation of archive bounds, model hierarchy/lists, and collision consistency; source exceptions return warnings.</summary>
    public IReadOnlyList<ValidationIssue> Validate()
    {
        foreach (var name in new[] { "map_head", "coll_data", "grGroundParam" })
            Require(File[name] != null, "STAGE_ROOT_MISSING", $"Required stage root '{name}' is missing.");
        Inspect();
        var models = ModelIdentity.Capture(Layout, new ModelIdentityCatalog());
        return CollisionData.Read(Layout).Validate(models);
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
