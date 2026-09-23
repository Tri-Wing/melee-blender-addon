using System.Text.Json;
using System.Text.Json.Nodes;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class ApplyTests
{
    private sealed class Session : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "mme-apply-" + Guid.NewGuid().ToString("N"));
        public string Directory => Path.Combine(Root, "session");
        public string Output => Path.Combine(Root, "edited.dat");
        public StageArchive Source { get; }
        public Session()
        {
            Source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
            SessionExtractor.Extract(Source, Directory);
        }
        public CollisionEdits Edits()
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(Directory, "collision/collision.json")));
            var c = doc.RootElement;
            return new(SessionExtractor.ProtocolVersion, "game",
                c.GetProperty("vertices").EnumerateArray().Select(v => new CollisionEditVertex(v.GetProperty("id").GetString()!, v.GetProperty("position").GetProperty("x").GetSingle(), v.GetProperty("position").GetProperty("y").GetSingle())).ToArray(),
                c.GetProperty("lines").EnumerateArray().Select(l => new CollisionEditLine(l.GetProperty("id").GetString()!, l.GetProperty("vertex0Id").GetString()!, l.GetProperty("vertex1Id").GetString()!, l.GetProperty("jointId").GetString()!, l.GetProperty("category").GetString()!, l.GetProperty("highFlags").GetUInt16(), l.GetProperty("lowFlags").GetUInt16())).ToArray());
        }
        public void Write(CollisionEdits edits) => File.WriteAllText(Path.Combine(Directory, "edits/collision.json"), JsonSerializer.Serialize(edits));
        public void Dispose() => System.IO.Directory.Delete(Root, true);
    }

    [PrimaryFixtureFact]
    public void NoEditApplyIsByteIdenticalAndOverwritesExistingOutput()
    {
        using var session = new Session();
        var result = SessionApplier.Apply(session.Directory, session.Output);
        Assert.False(result.CollisionChanged);
        Assert.Equal(session.Source.Layout.Bytes, File.ReadAllBytes(session.Output));
        File.WriteAllText(session.Output, "Existing output to replace");
        SessionApplier.Apply(session.Directory, session.Output);
        Assert.Equal(session.Source.Layout.Bytes, File.ReadAllBytes(session.Output));
        Assert.Equal("OUTPUT_SESSION", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, Path.Combine(session.Directory, "source.dat"))).Code);
        Assert.Equal(session.Source.Layout.Bytes, File.ReadAllBytes(Path.Combine(session.Directory, "source.dat")));
    }

    [PrimaryFixtureFact]
    public void MoveVertexPreservesEveryUnrelatedByteAndModelIdentity()
    {
        using var session = new Session(); var edits = session.Edits(); var original = edits.Vertices[0];
        edits.Vertices[0] = original with { Y = original.Y + 1 }; session.Write(edits);
        File.WriteAllBytes(session.Output, session.Source.Layout.Bytes);
        var result = SessionApplier.Apply(session.Directory, session.Output); Assert.True(result.CollisionChanged);
        var output = new StageArchive(session.Output); var collision = CollisionData.Read(output.Layout);
        Assert.Contains(new CollisionVertex(original.X, original.Y + 1), collision.Vertices);
        int header = session.Source.Layout.Roots.Single(r => r.Name == "coll_data").Offset;
        Assert.Equal(session.Source.Layout.Bytes.AsSpan(32, header).ToArray(), output.Layout.Bytes.AsSpan(32, header).ToArray());
        Assert.Equal(session.Source.Layout.Bytes.AsSpan(32 + header + 44, session.Source.Layout.DataSize - header - 44).ToArray(),
            output.Layout.Bytes.AsSpan(32 + header + 44, session.Source.Layout.DataSize - header - 44).ToArray());
        Assert.Equal(session.Source.Layout.Roots, output.Layout.Roots);
        var catalog = new ModelIdentityCatalog(); ModelIdentity.Capture(session.Source.Layout, catalog).RequireUnchanged(ModelIdentity.Capture(output.Layout, catalog));
        Assert.Equal(session.Source.Layout.Bytes, File.ReadAllBytes(Path.Combine(session.Directory, "source.dat")));
    }

    [PrimaryFixtureFact]
    public void AddAndRemoveLineRebuildsCountsRangesAndBounds()
    {
        using var session = new Session(); var edits = session.Edits();
        string a = Guid.NewGuid().ToString("N"), b = Guid.NewGuid().ToString("N");
        edits = edits with
        {
            Vertices = [.. edits.Vertices, new(a, 500, 500), new(b, 510, 500)],
            Lines = [.. edits.Lines.Skip(1), new(Guid.NewGuid().ToString("N"), a, b, edits.Lines[0].JointId, "floor", 1, 0x0302)]
        };
        session.Write(edits); SessionApplier.Apply(session.Directory, session.Output);
        var c = CollisionData.Read(new StageArchive(session.Output).Layout);
        Assert.Equal(16, c.Lines.Length); Assert.True(c.Joints[0].Right >= 518); Assert.True(c.Joints[0].Top >= 508);
        Assert.Contains(c.Lines, line => line.LowFlags == 0x0302); Assert.Empty(c.Validate(forEditedExport: true));
    }

    [PrimaryFixtureFact]
    public void RejectsAmbiguousVertexBeforePublishingOutput()
    {
        using var session = new Session(); var edits = session.Edits(); string id = Guid.NewGuid().ToString("N");
        edits = edits with { Vertices = [.. edits.Vertices, new(id, 200, 200)],
            Lines = [.. edits.Lines, new(Guid.NewGuid().ToString("N"), edits.Lines[0].Vertex0Id, id, edits.Lines[0].JointId, "floor", 1, 0)] };
        session.Write(edits);
        Assert.Equal("COLLISION_AMBIGUOUS_VERTEX", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        Assert.False(File.Exists(session.Output)); Assert.Empty(System.IO.Directory.GetFiles(session.Root, "*.tmp"));
    }

    [PrimaryFixtureFact]
    public void InvalidEditLeavesExistingOutputIntact()
    {
        using var session = new Session();
        byte[] original = session.Source.Layout.Bytes;
        File.WriteAllBytes(session.Output, original);
        var edits = session.Edits();
        edits.Lines[0] = edits.Lines[0] with { Vertex1Id = edits.Lines[0].Vertex0Id };
        session.Write(edits);
        Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output));
        Assert.Equal(original, File.ReadAllBytes(session.Output));
        Assert.Empty(System.IO.Directory.GetFiles(session.Root, "*.tmp"));
    }

    [PrimaryFixtureFact]
    public void RejectsChangedSourceAndProtectedFiles()
    {
        using var session = new Session(); string source = Path.Combine(session.Directory, "source.dat");
        var bytes = File.ReadAllBytes(source); bytes[28] ^= 1; File.WriteAllBytes(source, bytes);
        Assert.Equal("SOURCE_HASH", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        File.WriteAllBytes(source, session.Source.Layout.Bytes);
        string group = Path.Combine(session.Directory, "models/group-000/group.json");
        File.AppendAllText(group, " ");
        Assert.Equal("SESSION_BASELINE_CHANGED", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        Assert.False(File.Exists(session.Output));
    }

    [PrimaryFixtureFact]
    public void RejectsIncompleteEditRecords()
    {
        using var session = new Session();
        File.WriteAllText(Path.Combine(session.Directory, "edits/collision.json"),
            "{\"protocolVersion\":2,\"coordinateSpace\":\"game\",\"vertices\":[],\"lines\":[{}]}");
        Assert.Equal("SESSION_FORMAT", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        Assert.False(File.Exists(session.Output));
    }

    [PrimaryFixtureFact]
    public void RejectsUnsupportedEditsAndOldProtocol()
    {
        using var session = new Session(); var path = Path.Combine(session.Directory, "edits/shapes.json"); File.WriteAllText(path, "{}");
        Assert.Equal("EDIT_UNSUPPORTED", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code); File.Delete(path);
        string manifest = Path.Combine(session.Directory, "stage.json"); var node = JsonNode.Parse(File.ReadAllText(manifest))!; node["protocolVersion"] = 1; File.WriteAllText(manifest, node.ToJsonString());
        Assert.Equal("SESSION_VERSION", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        Assert.False(File.Exists(session.Output));
    }

    [PrimaryFixtureFact]
    public void AppliesAdditionAsOrdinaryModelGeometry()
    {
        using var session = new Session();
        using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(session.Directory, "stage.json")));
        string target = manifest.RootElement.GetProperty("modelAdditionTargets")[0].GetProperty("id").GetString()!;
        string materialId = Guid.NewGuid().ToString("N");
        var part = new ModelAdditionPart(Guid.NewGuid().ToString("N"), materialId,
            [new(0, 0, 0), new(1, 0, 0), new(0, 1, 0)], [0, 1, 2],
            [new(0, 0, 1), new(0, 0, 1), new(0, 0, 1)], null);
        var material = new ModelAdditionMaterial(materialId, "Constant", new ColorData(1, 1, 1, 1),
            null, "repeat", "repeat", "linear", "linear", ModelAdditionEditing.MaterialPreset);
        var edits = new ModelAdditionEdits(SessionExtractor.ProtocolVersion, ModelAddition.SchemaVersion,
            "game-joint-local", [new(Guid.NewGuid().ToString("N"), "Test", target, [part])], [material], []);
        File.WriteAllText(Path.Combine(session.Directory, "edits/additions.json"), JsonSerializer.Serialize(edits));
        var collisionEdits = session.Edits();
        collisionEdits.Vertices[0] = collisionEdits.Vertices[0] with
        {
            Y = collisionEdits.Vertices[0].Y + 1
        };
        session.Write(collisionEdits);

        var result = SessionApplier.Apply(session.Directory, session.Output);
        var output = new StageArchive(session.Output);
        var catalog = new ModelIdentityCatalog();
        var original = ModelIdentity.Capture(session.Source.Layout, catalog);
        var extended = ModelIdentity.Capture(output.Layout, catalog);
        Assert.True(result.ModelChanged);
        Assert.True(result.CollisionChanged);
        Assert.Equal(1, result.ModelTriangles);
        Assert.Empty(output.Validate());
        Assert.Equal(original.Nodes.Count + 2, extended.Nodes.Count);
        Assert.True(manifest.RootElement.GetProperty("capabilities").GetProperty("modelAddition").GetBoolean());

        byte[] published = File.ReadAllBytes(session.Output);
        var invalid = edits with
        {
            Additions = [edits.Additions[0] with { TargetJobjId = Guid.NewGuid().ToString("N") }]
        };
        File.WriteAllText(Path.Combine(session.Directory, "edits/additions.json"), JsonSerializer.Serialize(invalid));
        Assert.Equal("MODEL_ADDITION_TARGET", Assert.Throws<StageException>(() =>
            SessionApplier.Apply(session.Directory, session.Output)).Code);
        Assert.Equal(published, File.ReadAllBytes(session.Output));
    }
}
