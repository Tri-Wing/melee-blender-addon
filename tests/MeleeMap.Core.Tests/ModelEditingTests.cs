using System.Text.Json;
using System.Text.Json.Nodes;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class ModelEditingTests
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    private static ModelEdits Triangle(string id) => new(2, "game-joint-local", [new(id,
        [new(0, 0, 0), new(10, 0, 0), new(0, 10, 0)], [0, 1, 2])]);

    [Fact]
    public void CompilesFlatNormalsAndDropsOnlyDegenerateTriangles()
    {
        var target = new EditableModel(Guid.NewGuid().ToString("N"), 0, 0, 0, 0, 0, 0);
        var input = Triangle(target.Id);
        input.Meshes[0] = input.Meshes[0] with { TriangleIndices = [0, 1, 2, 0, 0, 1] };
        var compiled = ModelEditing.Compile(input, target);
        Assert.Equal(3, compiled.Positions.Length);
        Assert.Equal(new[] { 0, 1, 2 }, compiled.TriangleIndices);
        Assert.All(compiled.Normals!, normal => Assert.Equal(new Vector3Data(0, 0, 1), normal));
    }

    [Theory]
    [InlineData("target", "MODEL_EDIT_TARGET")]
    [InlineData("indices", "MODEL_INDEX")]
    [InlineData("empty", "MODEL_EMPTY")]
    [InlineData("nonfinite", "MODEL_NONFINITE")]
    [InlineData("limit", "MODEL_EDIT_COUNT")]
    [InlineData("space", "MODEL_EDIT_VERSION")]
    public void RejectsInvalidModelEdits(string kind, string code)
    {
        var target = new EditableModel(Guid.NewGuid().ToString("N"), 0, 0, 0, 0, 0, 0);
        var input = Triangle(target.Id); var mesh = input.Meshes[0];
        switch (kind)
        {
            case "target": input.Meshes[0] = mesh with { Id = Guid.NewGuid().ToString("N") }; break;
            case "indices": mesh.TriangleIndices[2] = 9; break;
            case "empty": mesh.TriangleIndices[2] = 0; break;
            case "nonfinite": mesh.Positions[0] = new(float.NaN, 0, 0); break;
            case "limit": input.Meshes[0] = mesh with { TriangleIndices = new int[(ModelEditing.MaxTriangles + 1) * 3] }; break;
            case "space": input = input with { CoordinateSpace = "blender" }; break;
        }
        Assert.Equal(code, Assert.Throws<StageException>(() => ModelEditing.Compile(input, target)).Code);
    }

    [PrimaryFixtureFact]
    public void ModelApplyPreservesUnrelatedBytesIdentitiesAndCollision()
    {
        using var session = new Fixture();
        var target = session.Target;
        Assert.Equal((3, 1, 0), (target.GroupIndex, target.JobjIndex, target.DobjIndex));
        var input = Triangle(target.Id); session.Write(input);
        var result = SessionApplier.Apply(session.Directory, session.Output);
        Assert.True(result.ModelChanged); Assert.False(result.CollisionChanged); Assert.Equal(1, result.ModelTriangles);
        var output = new StageArchive(session.Output); var decoded = GxMeshDecoder.Decode(output.Layout, target.PobjOffset);
        Assert.Equal(input.Meshes[0].Positions, decoded.Positions);
        // GrNLa's original target culls front faces. Newly authored Blender
        // geometry must instead retain its outward-facing surfaces.
        int sourceFlags = new ArchiveDataReader(session.Source.Layout).UShort(target.PobjOffset + 12);
        int outputFlags = new ArchiveDataReader(output.Layout).UShort(target.PobjOffset + 12);
        Assert.Equal(0x8000, sourceFlags & 0xC000);
        Assert.Equal(0x4000, outputFlags & 0xC000);
        Assert.Equal(sourceFlags & ~0xC000, outputFlags & ~0xC000);
        Assert.All(decoded.Normals!, n => Assert.Equal(new Vector3Data(0, 0, 1), n));
        Assert.Equal(session.Source.Layout.Roots, output.Layout.Roots);
        session.Identity.RequireUnchanged(ModelIdentity.Capture(output.Layout, session.Catalog));
        for (int i = 0; i < session.Source.Layout.DataSize; i++)
        {
            if (i >= target.PobjOffset + 8 && i < target.PobjOffset + 20
                || i >= target.DobjOffset + 8 && i < target.DobjOffset + 12) continue;
            Assert.Equal(session.Source.Layout.Bytes[32 + i], output.Layout.Bytes[32 + i]);
        }
        foreach (var node in session.Identity.Nodes.Where(n => n.Kind == "pobj" && n.Id != target.Id))
        {
            var before = GxMeshDecoder.Decode(session.Source.Layout, node.SourceOffset);
            var after = GxMeshDecoder.Decode(output.Layout, node.SourceOffset);
            Assert.Equal(before.Positions, after.Positions); Assert.Equal(before.Normals, after.Normals);
            Assert.Equal(before.TriangleIndices, after.TriangleIndices);
        }
        Assert.Equal(CollisionData.Read(session.Source.Layout).Lines, CollisionData.Read(output.Layout).Lines);
        Assert.Equal(session.Source.Layout.Bytes, File.ReadAllBytes(Path.Combine(session.Directory, "source.dat")));
    }

    [PrimaryFixtureFact]
    public void CombinesModelAndCollisionEditsAndPreservesOutputOnFailure()
    {
        using var session = new Fixture(); session.Write(Triangle(session.Target.Id));
        using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(session.Directory, "collision/collision.json")));
        var c = document.RootElement;
        var vertices = c.GetProperty("vertices").EnumerateArray().Select(v => new CollisionEditVertex(v.GetProperty("id").GetString()!,
            v.GetProperty("position").GetProperty("x").GetSingle(), v.GetProperty("position").GetProperty("y").GetSingle())).ToArray();
        vertices[0] = vertices[0] with { Y = vertices[0].Y + 1 };
        var lines = c.GetProperty("lines").EnumerateArray().Select(l => new CollisionEditLine(l.GetProperty("id").GetString()!,
            l.GetProperty("vertex0Id").GetString()!, l.GetProperty("vertex1Id").GetString()!, l.GetProperty("jointId").GetString()!,
            l.GetProperty("category").GetString()!, l.GetProperty("highFlags").GetUInt16(), l.GetProperty("lowFlags").GetUInt16())).ToArray();
        File.WriteAllText(Path.Combine(session.Directory, "edits/collision.json"), JsonSerializer.Serialize(new CollisionEdits(2, "game", vertices, lines), Json));
        var result = SessionApplier.Apply(session.Directory, session.Output);
        Assert.True(result.CollisionChanged && result.ModelChanged);
        Assert.Contains(new CollisionVertex(vertices[0].X, vertices[0].Y), CollisionData.Read(new StageArchive(session.Output).Layout).Vertices);
        var saved = File.ReadAllBytes(session.Output);
        session.Write(Triangle(Guid.NewGuid().ToString("N")));
        Assert.Equal("MODEL_EDIT_TARGET", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        Assert.Equal(saved, File.ReadAllBytes(session.Output));
        Assert.Empty(System.IO.Directory.GetFiles(session.Root, "*.tmp"));
    }

    [PrimaryFixtureFact]
    public void OldSessionsRemainCollisionOnlyAndTargetsAreRecomputed()
    {
        using var session = new Fixture(); session.Write(Triangle(session.Target.Id));
        string path = Path.Combine(session.Directory, "stage.json"); var manifest = JsonNode.Parse(File.ReadAllText(path))!;
        manifest.AsObject().Remove("editableMesh"); File.WriteAllText(path, manifest.ToJsonString());
        Assert.Equal("MODEL_EDIT_TARGET", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        File.Delete(Path.Combine(session.Directory, "edits/models.json"));
        SessionApplier.Apply(session.Directory, session.Output);
        Assert.Equal(session.Source.Layout.Bytes, File.ReadAllBytes(session.Output));
    }

    private sealed class Fixture : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "mme-model-" + Guid.NewGuid().ToString("N"));
        public string Directory => Path.Combine(Root, "session");
        public string Output => Path.Combine(Root, "edited.dat");
        public StageArchive Source { get; } = new(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
        public ModelIdentityCatalog Catalog { get; }
        public ModelIdentitySnapshot Identity { get; }
        public EditableModel Target { get; }
        public Fixture()
        {
            SessionExtractor.Extract(Source, Directory);
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(Directory, "stage.json")));
            var nodes = new List<ModelIdentityNode>();
            foreach (var entry in manifest.RootElement.GetProperty("modelGroups").EnumerateArray())
            {
                using var group = JsonDocument.Parse(File.ReadAllText(Path.Combine(Directory, entry.GetProperty("file").GetString()!)));
                nodes.AddRange(group.RootElement.GetProperty("nodes").Deserialize<ModelIdentityNode[]>(Json)!);
            }
            Catalog = ModelIdentityCatalog.Restore(nodes); Identity = ModelIdentity.Capture(Source.Layout, Catalog);
            Target = ModelEditing.Select(Source.Layout, Identity)!; Assert.NotNull(Target);
            Assert.Equal(Target.Id, manifest.RootElement.GetProperty("editableMesh").GetProperty("id").GetString());
        }
        public void Write(ModelEdits edits) => File.WriteAllText(Path.Combine(Directory, "edits/models.json"), JsonSerializer.Serialize(edits, Json));
        public void Dispose() => System.IO.Directory.Delete(Root, true);
    }
}
