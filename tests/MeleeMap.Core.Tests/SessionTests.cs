using System.Security.Cryptography;
using System.Text.Json;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public sealed class PrimaryFixtureFactAttribute : FactAttribute
{
    public PrimaryFixtureFactAttribute()
    {
        if (!File.Exists(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat")))
            Skip = "GrNLa.dat is required for the local session integration test.";
    }
}

public class SessionTests
{
    [PrimaryFixtureFact]
    public void ExtractsCompleteCollisionIdentityGraphAndOneMeshWithoutChangingSource()
    {
        string source = Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat");
        var stage = new StageArchive(source); var original = CollisionData.Read(stage.Layout);
        string directory = Path.Combine(Path.GetTempPath(), "meleemap-session-test-" + Guid.NewGuid().ToString("N"));
        try
        {
            var result = SessionExtractor.Extract(stage, directory);
            Assert.Equal(10, result.ModelGroups); Assert.Equal(16, result.CollisionLines); Assert.Equal(14, result.Triangles);
            Assert.Equal(stage.Layout.Bytes, File.ReadAllBytes(Path.Combine(directory, "source.dat")));
            Assert.Equal(stage.Layout.Bytes, File.ReadAllBytes(source));
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "stage.json")));
            var root = manifest.RootElement;
            Assert.Equal(1, root.GetProperty("protocolVersion").GetInt32());
            Assert.Equal(Convert.ToHexString(SHA256.HashData(stage.Layout.Bytes)).ToLowerInvariant(), root.GetProperty("source").GetProperty("sha256").GetString());
            Assert.Equal(10, root.GetProperty("modelGroups").GetArrayLength());
            var baseline = ModelIdentity.Capture(stage.Layout, new());
            var stored = new List<ModelIdentityNode>();
            var json = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
            foreach (var group in root.GetProperty("modelGroups").EnumerateArray())
            {
                using var file = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, group.GetProperty("file").GetString()!)));
                stored.AddRange(file.RootElement.GetProperty("nodes").Deserialize<ModelIdentityNode[]>(json)!);
            }
            Assert.Equal(baseline.Nodes.Select(n => (n.Kind, n.GroupIndex, n.Index, n.SourceOffset)), stored.Select(n => (n.Kind, n.GroupIndex, n.Index, n.SourceOffset)));
            Assert.All(stored, n => Assert.True(Guid.TryParseExact(n.Id, "N", out _)));
            var ids = stored.Select(n => n.Id).ToHashSet();
            Assert.All(stored.Where(n => n.OwnerId != null), n => Assert.Contains(n.OwnerId!, ids));
            using var collision = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "collision/collision.json")));
            var c = collision.RootElement;
            var vertices = c.GetProperty("vertices").EnumerateArray().ToArray();
            for (int i = 0; i < vertices.Length; i++)
            {
                var v = vertices[i].GetProperty("position").Deserialize<CollisionVertex>(json);
                Assert.Equal(original.Vertices[i], v);
                Assert.Equal(new Vector3Data(v.X, v.Y, 0), CoordinateTransform.ToGame(CoordinateTransform.ToBlender(new(v.X, v.Y, 0))));
            }
            Assert.Equal(original.Ranges, c.GetProperty("ranges").Deserialize<CollisionRange[]>(json));
            var lines = c.GetProperty("lines").EnumerateArray().ToArray();
            for (int i = 0; i < lines.Length; i++)
            {
                Assert.Equal(original.Lines[i], lines[i].GetProperty("source").Deserialize<CollisionLine>(json));
                Assert.Equal(vertices[original.Lines[i].Vertex0].GetProperty("id").GetString(), lines[i].GetProperty("vertex0Id").GetString());
                foreach (var (name, index) in new[] { ("previous0Id", original.Lines[i].Previous0), ("next0Id", original.Lines[i].Next0), ("previous1Id", original.Lines[i].Previous1), ("next1Id", original.Lines[i].Next1) })
                    Assert.Equal(index < 0 ? null : lines[index].GetProperty("id").GetString(), lines[i].GetProperty(name).GetString());
            }
            using var mesh = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, root.GetProperty("selectedMesh").GetProperty("file").GetString()!)));
            var m = mesh.RootElement;
            Assert.Contains(m.GetProperty("ownerId").GetString()!, ids);
            int count = m.GetProperty("positions").GetArrayLength();
            Assert.All(m.GetProperty("triangleIndices").EnumerateArray(), i => Assert.InRange(i.GetInt32(), 0, count - 1));
            Assert.Equal("SESSION_EXISTS", Assert.Throws<StageException>(() => SessionExtractor.Extract(stage, directory)).Code);
            using var unchanged = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "stage.json")));
            Assert.Equal(result.MeshId, unchanged.RootElement.GetProperty("selectedMesh").GetProperty("id").GetString());
        }
        finally { if (Directory.Exists(directory)) Directory.Delete(directory, true); }
    }

    [PrimaryFixtureFact]
    public void FailedExtractionDoesNotPublishAPartialSession()
    {
        var stage = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
        var target = ModelIdentity.Capture(stage.Layout, new()).Nodes.First(n => n.Kind == "pobj");
        var r = new ArchiveDataReader(stage.Layout);
        // Corrupt primitive vertex count in the first target without changing archive relocation structure.
        int display = r.Pointer(target.SourceOffset + 16)!.Value;
        stage.Layout.Bytes[32 + display + 1] = 255; stage.Layout.Bytes[32 + display + 2] = 255;
        string parent = Path.Combine(Path.GetTempPath(), "mme-failed-session-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(parent);
        try
        {
            Assert.Throws<StageException>(() => SessionExtractor.Extract(stage, Path.Combine(parent, "session")));
            Assert.Empty(Directory.GetFileSystemEntries(parent));
        }
        finally { Directory.Delete(parent, true); }
    }
}
