using System.Text.Json;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class VertexColorEditingTests
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, PropertyNameCaseInsensitive = true };

    [CorpusFact]
    public void CornerColorsRoundtripAndInvalidEditsCannotOverwriteOutput()
    {
        using var f = new Fixture();
        var target = ModelEditing.SelectAll(f.Source.Layout, f.Identity).Single(t => t.GroupIndex == 3 && t.JobjIndex == 4 && t.DobjIndex == 3);
        var original = GxMeshDecoder.Decode(f.Source.Layout, target.PobjOffset);
        var colors = original.TriangleIndices.Select(i => original.Colors0![i]).ToArray();
        colors[0] = new(.1f, .3f, .5f, .7f);
        var edit = new ModelEdit(target.Id, original.Positions, original.TriangleIndices, Colors0: colors);
        f.Write("models", new ModelEdits(2, "game-joint-local", [edit]));
        Assert.True(SessionApplier.Apply(f.Session, f.Output).ModelChanged);
        byte[] saved = File.ReadAllBytes(f.Output);
        var output = new StageArchive(f.Output);
        var actual = GxMeshDecoder.Decode(output.Layout, target.PobjOffset);
        Assert.Equal(new ColorData(26/255f, 77/255f, 128/255f, 179/255f), actual.Colors0![0]);
        Assert.Equal(original.TriangleIndices.Select(i => original.Positions[i]), actual.Positions);
        if (original.Normals != null) Assert.Equal(original.TriangleIndices.Select(i => original.Normals[i]), actual.Normals!);
        else Assert.Null(actual.Normals);
        Assert.Equal(original.TriangleIndices.Select(i => original.TexCoords0![i]), actual.TexCoords0!);
        Assert.Equal(new ArchiveDataReader(f.Source.Layout).Pointer(target.DobjOffset + 8), new ArchiveDataReader(output.Layout).Pointer(target.DobjOffset + 8));
        foreach (var invalid in new[] {
            edit with { Colors0 = [new(1,1,1,1)] },
            edit with { Colors1 = colors },
            edit with { Colors0 = colors.Select(c => c with { A = -1 }).ToArray() },
            edit with { TriangleIndices = original.TriangleIndices.Reverse().ToArray() }
        })
        {
            f.Write("models", new ModelEdits(2, "game-joint-local", [invalid]));
            Assert.Throws<StageException>(() => SessionApplier.Apply(f.Session, f.Output));
            Assert.Equal(saved, File.ReadAllBytes(f.Output));
        }
    }

    [CorpusFact]
    public void VertexColorMaterialCanSwitchToDiffuseAndMaterialAlpha()
    {
        using var f = new Fixture();
        var target = ModelEditing.SelectAll(f.Source.Layout, f.Identity)
            .Single(t => t.GroupIndex == 3 && t.JobjIndex == 4 && t.DobjIndex == 3);
        var definition = MaterialProperties.Select(f.Source.Layout, f.Identity).Single(m => m.Id == target.Id);
        Assert.True(definition.UseVertexColor);
        Assert.True(definition.CanToggleVertexColor);
        uint renderFlags = definition.RenderFlags | (1u << 2) | (1u << 29);
        f.Write("materials", new MaterialPropertyEdits(2,
            [new(target.Id, [24, 80, 160], .375f, UseVertexColor: false,
                RenderFlags: renderFlags, TransparencyMode: 1, AlphaSource: 1)]));
        Assert.True(SessionApplier.Apply(f.Session, f.Output).MaterialChanged);
        var output = new StageArchive(f.Output);
        var reader = new ArchiveDataReader(output.Layout);
        int mobj = reader.Pointer(target.DobjOffset + 8)!.Value;
        int flags = reader.Int(mobj + 4);
        Assert.Equal(1, flags & 3);
        Assert.Equal(1, (flags >> 13) & 3);
        Assert.NotEqual(0, flags & (1 << 2));
        Assert.NotEqual(0, flags & (1 << 29));
        Assert.NotEqual(0, flags & (1 << 30));
        int mat = reader.Pointer(mobj + 12)!.Value;
        Assert.Equal(new byte[] { 24, 80, 160 }, Enumerable.Range(0, 3).Select(i => reader.Byte(mat + 4 + i)).ToArray());
        Assert.Equal(.375f, reader.Float(mat + 12));
        int pixel = reader.Pointer(mobj + 20)!.Value;
        Assert.Equal(1, reader.Byte(pixel + 4));
        Assert.Equal(4, reader.Byte(pixel + 5));
        Assert.Equal(5, reader.Byte(pixel + 6));
        Assert.Equal(0, reader.Byte(pixel) & 32);
        var changed = MaterialProperties.Select(output.Layout, f.Identity).Single(m => m.Id == target.Id);
        Assert.False(changed.UseVertexColor);
        Assert.True(changed.CanToggleVertexColor);
        Assert.True(changed.CanEditDiffuse);
        Assert.True(changed.CanEditAlpha);
        Assert.Equal(1, changed.TransparencyMode);
        Assert.Equal(1, changed.AlphaSource);
        uint expectedFlags = ((renderFlags | (1u << 30)) & ~3u) | 1u;
        expectedFlags = (expectedFlags & ~(3u << 13)) | (1u << 13);
        Assert.Equal(expectedFlags, changed.RenderFlags);
    }

    private sealed class Fixture : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "mme-vertex-colors-"+Guid.NewGuid().ToString("N"));
        public string Session => Path.Combine(Root, "session");
        public string Output => Path.Combine(Root, "out.dat");
        public StageArchive Source { get; } = new(Path.Combine(CorpusTests.CorpusDirectory,"GrSt.dat"));
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
