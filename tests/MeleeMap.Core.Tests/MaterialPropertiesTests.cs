using System.Text.Json;
using System.Text.Json.Nodes;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class MaterialPropertiesTests
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, PropertyNameCaseInsensitive = true };

    [CorpusFact]
    public void GreenGreensTwoLayerMaterialEditsLightingColorsAndBothTextureBlends()
    {
        string sourcePath = Path.Combine(CorpusTests.CorpusDirectory, "GrGb.dat");
        if (!File.Exists(sourcePath)) return;
        string root = Path.Combine(Path.GetTempPath(), "mme-grgb-material-" + Guid.NewGuid().ToString("N"));
        string session = Path.Combine(root, "session"), output = Path.Combine(root, "out.dat");
        try
        {
            var source = new StageArchive(sourcePath);
            SessionExtractor.Extract(source, session);
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(session, "stage.json")));
            var definition = manifest.RootElement.GetProperty("editableMaterialProperties").EnumerateArray()
                .Single(entry => entry.GetProperty("mobjOffset").GetInt32() == 0x6760);
            string id = definition.GetProperty("id").GetString()!;
            Assert.Equal(new[] { 128, 128, 134 }, definition.GetProperty("ambient").EnumerateArray().Select(v => v.GetInt32()));
            Assert.Equal(2, definition.GetProperty("textures").GetArrayLength());
            Assert.Equal(new[] { 0x10, 0x20 }, definition.GetProperty("textures").EnumerateArray()
                .Select(texture => texture.GetProperty("lightmapFlags").GetInt32()));
            File.WriteAllText(Path.Combine(session, "edits/materials.json"), JsonSerializer.Serialize(
                new MaterialPropertyEdits(2, [new(id, Ambient: [64, 32, 16], Specular: [10, 20, 30],
                    Shininess: 77, TextureBlends: [.25f, .75f])]), Json));

            SessionApplier.Apply(session, output);
            var written = new StageArchive(output); var r = new ArchiveDataReader(written.Layout);
            int mobj = r.Pointer(0xF418 + 8)!.Value;
            Assert.NotEqual(0x6760, mobj);
            int material = r.Pointer(mobj + 12)!.Value;
            Assert.Equal(new[] { 64, 32, 16 }, Enumerable.Range(0, 3).Select(i => (int)r.Byte(material + i)));
            Assert.Equal(new[] { 10, 20, 30 }, Enumerable.Range(0, 3).Select(i => (int)r.Byte(material + 8 + i)));
            Assert.Equal(77, r.Float(material + 16));
            int first = r.Pointer(mobj + 8)!.Value, second = r.Pointer(first + 4)!.Value;
            Assert.Equal(.25f, r.Float(first + 0x44)); Assert.Equal(.75f, r.Float(second + 0x44));
            Assert.Null(r.Pointer(second + 4));
            Assert.Equal(0x30010, r.Int(first + 0x40)); Assert.Equal(0x30020, r.Int(second + 0x40));
        }
        finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
    }

    [PrimaryFixtureFact]
    public void PropertyEditsCloneOnlySelectedMaterialAndComposeWithGeometryAndAssignments()
    {
        using var f = new Fixture();
        var all = ModelEditing.SelectAll(f.Source.Layout, f.Identity);
        var target = all.Single(t => t.GroupIndex == 3 && t.JobjIndex == 3 && t.DobjIndex == 3);
        var before = MaterialProperties.Select(f.Source.Layout, f.Identity).Single(m => m.Id == target.Id);
        var original = GxMeshDecoder.Decode(f.Source.Layout, target.PobjOffset);
        var other = all.First(t => !t.PositionsOnly && t.Id != target.Id);
        var model = new ModelEdits(2, "game-joint-local", [
            new(target.Id, original.Positions.Select(p => p with { X = p.X + 1 }).ToArray(), original.TriangleIndices),
            new(other.Id, [new(0,0,0), new(10,0,0), new(0,10,0)], [0,1,2], target.Id, [new(0,0),new(1,0),new(0,1)])]);
        f.Write("models", model);
        f.Write("materials", new MaterialPropertyEdits(2, [new(target.Id, [20, 80, 140], .375f, .75f)]));
        var applied = SessionApplier.Apply(f.Session, f.Output);
        Assert.True(applied.MaterialChanged); Assert.True(applied.ModelChanged);
        var output = new StageArchive(f.Output); var r = new ArchiveDataReader(output.Layout);
        int mobj = r.Pointer(target.DobjOffset + 8)!.Value;
        Assert.NotEqual(before.MobjOffset, mobj);
        Assert.Equal(mobj, r.Pointer(other.DobjOffset + 8));
        int mat = r.Pointer(mobj + 12)!.Value, tex = r.Pointer(mobj + 8)!.Value;
        Assert.Equal(new byte[] {20,80,140}, Enumerable.Range(0,3).Select(i => r.Byte(mat+4+i)).ToArray());
        Assert.Equal(.375f, r.Float(mat+12)); Assert.Equal(.75f, r.Float(tex+0x44));
        Assert.Equal(f.Source.Layout.Bytes.AsSpan(32+before.MobjOffset,24).ToArray(), output.Layout.Bytes.AsSpan(32+before.MobjOffset,24).ToArray());
        Assert.Equal(f.Source.Layout.Bytes.AsSpan(32+before.MaterialOffset,20).ToArray(), output.Layout.Bytes.AsSpan(32+before.MaterialOffset,20).ToArray());
        Assert.Equal(f.Source.Layout.Bytes.AsSpan(32+before.TextureOffset!.Value,0x5C).ToArray(), output.Layout.Bytes.AsSpan(32+before.TextureOffset.Value,0x5C).ToArray());
        foreach (var untouched in all.Where(t => t.Id != target.Id && t.Id != other.Id))
            Assert.Equal(new ArchiveDataReader(f.Source.Layout).Pointer(untouched.DobjOffset+8), r.Pointer(untouched.DobjOffset+8));
    }

    [PrimaryFixtureFact]
    public void InvalidBatchAndForgedAnimatedPermissionsNeverOverwriteOutput()
    {
        using var f = new Fixture();
        var targets = ModelEditing.SelectAll(f.Source.Layout, f.Identity);
        var target = targets.Single(t => t.GroupIndex == 3 && t.JobjIndex == 3 && t.DobjIndex == 3);
        var valid = new MaterialPropertyEdit(target.Id, [10,20,30]);
        f.Write("materials", new MaterialPropertyEdits(2, [valid]));
        SessionApplier.Apply(f.Session, f.Output); byte[] saved = File.ReadAllBytes(f.Output);
        var animated = targets.First(t => t.PositionsOnly);
        string path = Path.Combine(f.Session, "stage.json"); var manifest = JsonNode.Parse(File.ReadAllText(path))!;
        manifest["editableMaterialProperties"]!.AsArray().Add(new JsonObject { ["id"] = animated.Id });
        File.WriteAllText(path, manifest.ToJsonString());
        var invalid = new (MaterialPropertyEdit[] Values, string Code)[] {
            ([valid, valid], "MATERIAL_EDIT_TARGET"),
            ([valid, new(animated.Id, [1,2,3])], "MATERIAL_EDIT_TARGET"),
            ([new(target.Id, [256,0,0])], "MATERIAL_DIFFUSE"),
            ([new(target.Id, [1,2])], "MATERIAL_DIFFUSE"),
            ([new(target.Id, Ambient: [256,0,0])], "MATERIAL_AMBIENT"),
            ([new(target.Id, Specular: [1,2])], "MATERIAL_SPECULAR"),
            ([new(target.Id, Shininess: 129)], "MATERIAL_SHININESS"),
            ([new(target.Id, Alpha: -1)], "MATERIAL_ALPHA"),
            ([new(target.Id, TextureBlend: 2)], "MATERIAL_BLEND"),
            ([new(target.Id, TextureBlends: [])], "MATERIAL_BLEND"),
            ([new(target.Id, TextureBlends: [2])], "MATERIAL_BLEND"),
            ([new(target.Id, RenderFlags: 0)], "MATERIAL_RENDER_FLAGS"),
            ([new(target.Id, TransparencyMode: 4)], "MATERIAL_TRANSPARENCY"),
            ([new(target.Id, AlphaSource: 4)], "MATERIAL_ALPHA_SOURCE"),
            ([new(target.Id)], "MATERIAL_EDIT_FORMAT")
        };
        foreach (var (values, code) in invalid)
        {
            f.Write("materials", new MaterialPropertyEdits(2, values));
            Assert.Equal(code, Assert.Throws<StageException>(() => SessionApplier.Apply(f.Session, f.Output)).Code);
            Assert.Equal(saved, File.ReadAllBytes(f.Output));
        }
    }

    [PrimaryFixtureFact]
    public void MaterialOnlyExportPreservesAllOriginalDataExceptBindingAndOldSessionsStayRestricted()
    {
        using var f = new Fixture();
        var target = ModelEditing.SelectAll(f.Source.Layout, f.Identity).Single(t => t.GroupIndex == 3 && t.JobjIndex == 3 && t.DobjIndex == 3);
        f.Write("materials", new MaterialPropertyEdits(2, [new(target.Id, TextureBlend: .1f)]));
        var result = SessionApplier.Apply(f.Session, f.Output);
        Assert.True(result.MaterialChanged); Assert.False(result.ModelChanged); Assert.False(result.CollisionChanged);
        var output = new StageArchive(f.Output);
        for (int i = 0; i < f.Source.Layout.DataSize; i++)
            if (i < target.DobjOffset+8 || i >= target.DobjOffset+12)
                Assert.Equal(f.Source.Layout.Bytes[32+i], output.Layout.Bytes[32+i]);
        string path = Path.Combine(f.Session, "stage.json"); var manifest = JsonNode.Parse(File.ReadAllText(path))!;
        manifest.AsObject().Remove("editableMaterialProperties"); File.WriteAllText(path, manifest.ToJsonString());
        Assert.Equal("MATERIAL_EDIT_TARGET", Assert.Throws<StageException>(() => SessionApplier.Apply(f.Session, f.Output)).Code);
    }

    private sealed class Fixture : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "mme-material-properties-"+Guid.NewGuid().ToString("N"));
        public string Session => Path.Combine(Root, "session");
        public string Output => Path.Combine(Root, "out.dat");
        public StageArchive Source { get; } = new(Path.Combine(CorpusTests.CorpusDirectory,"GrNLa.dat"));
        public ModelIdentitySnapshot Identity { get; }
        public Fixture()
        {
            SessionExtractor.Extract(Source, Session);
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(Session,"stage.json")));
            var nodes = new List<ModelIdentityNode>();
            foreach (var group in manifest.RootElement.GetProperty("modelGroups").EnumerateArray())
            {
                using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(Session,group.GetProperty("file").GetString()!)));
                nodes.AddRange(doc.RootElement.GetProperty("nodes").Deserialize<ModelIdentityNode[]>(Json)!);
            }
            Identity = ModelIdentity.Capture(Source.Layout, ModelIdentityCatalog.Restore(nodes));
        }
        public void Write(string kind, object value) => File.WriteAllText(Path.Combine(Session,$"edits/{kind}.json"),JsonSerializer.Serialize(value,Json));
        public void Dispose() => Directory.Delete(Root,true);
    }
}
