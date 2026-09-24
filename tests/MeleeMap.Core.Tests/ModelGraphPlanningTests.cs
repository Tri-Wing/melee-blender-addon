using System.Text.Json;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class ModelGraphPlanningTests
{
    private static ModelEdit Triangle(string id) => new(id,
        [new(0, 0, 0), new(10, 0, 0), new(0, 10, 0)], [0, 1, 2]);

    [GrGdFixtureFact]
    public void IndependentVerifierRejectsStructurallyValidWrongOwnership()
    {
        var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrGd.dat"));
        var catalog = new ModelIdentityCatalog();
        var identity = ModelIdentity.Capture(source.Layout, catalog);
        var targets = SharedTargets(source, identity);
        var plan = ModelGraphPlanner.Plan(identity, [targets[1].Id], []);

        // This is a valid archive transformation, but it isolates the wrong sibling.
        var wrongPlan = ModelGraphPlanner.Plan(identity, [targets[0].Id], []);
        var builder = new ArchiveMutationBuilder(source.Layout);
        ModelGraphEditor.Apply(builder, identity, wrongPlan);
        var actual = ModelIdentity.Capture(builder.BuildLayout(), catalog);
        var error = Assert.Throws<StageException>(() => ModelGraphVerifier.Verify(plan, actual));
        Assert.Equal("MODEL_GRAPH_MISMATCH", error.Code);
    }

    [GrGdFixtureFact]
    public void VertexMovementAndPropertyEditUseSourceGeometryAndFinalSplitOwner()
    {
        using var fixture = new GrGdSession();
        var target = fixture.Targets[1];
        var original = GxMeshDecoder.Decode(fixture.Source.Layout, target.PobjOffset);
        var moved = new ModelEdit(target.Id,
            original.Positions.Select(position => position with { X = position.X + 0.5f }).ToArray(),
            original.TriangleIndices);
        fixture.Write("models", new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", [moved]));
        fixture.Write("materials", new MaterialPropertyEdits(SessionExtractor.ProtocolVersion,
            [new MaterialPropertyEdit(target.Id, Ambient: [11, 22, 33])]));

        var result = SessionApplier.Apply(fixture.Session, fixture.Output);
        Assert.True(result.ModelChanged); Assert.True(result.MaterialChanged);
        var output = new StageArchive(fixture.Output);
        var targets = fixture.OutputTargets(output);
        Assert.Equal(fixture.Targets[0].DobjOffset, targets[0].DobjOffset);
        Assert.NotEqual(targets[0].DobjOffset, targets[1].DobjOffset);
        var expected = ModelEditing.Compile(new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", [moved]),
            target, original);
        var reader = new ArchiveDataReader(output.Layout);
        int mobj = reader.Pointer(targets[1].DobjOffset + 8)!.Value;
        ModelPositionWriter.Verify(output.Layout, fixture.Source.Layout, targets[1], expected, mobj);
        int material = reader.Pointer(mobj + 12)!.Value;
        Assert.Equal(new byte[] { 11, 22, 33 },
            Enumerable.Range(0, 3).Select(index => reader.Byte(material + index)).ToArray());
    }

    [GrGdFixtureFact]
    public void SiblingsCanReceivePositionAndTopologyEditsInOneTransaction()
    {
        using var fixture = new GrGdSession();
        var firstOriginal = GxMeshDecoder.Decode(fixture.Source.Layout, fixture.Targets[0].PobjOffset);
        var moved = new ModelEdit(fixture.Targets[0].Id,
            firstOriginal.Positions.Select(position => position with { Y = position.Y + 0.25f }).ToArray(),
            firstOriginal.TriangleIndices);
        var replacement = Triangle(fixture.Targets[1].Id);
        fixture.Write("models", new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", [moved, replacement]));

        SessionApplier.Apply(fixture.Session, fixture.Output);
        var output = new StageArchive(fixture.Output);
        var targets = fixture.OutputTargets(output);
        Assert.Equal(fixture.Targets[0].DobjOffset, targets[0].DobjOffset);
        Assert.NotEqual(targets[0].DobjOffset, targets[1].DobjOffset);
        var movedMesh = ModelEditing.Compile(new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", [moved]),
            fixture.Targets[0], firstOriginal);
        ModelPositionWriter.Verify(output.Layout, fixture.Source.Layout, targets[0], movedMesh);
        var replacementMesh = ModelEditing.Compile(
            new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", [replacement]), fixture.Targets[1]);
        ModelArchiveWriter.Verify(output.Layout, targets[1], replacementMesh);
    }

    [GrGdFixtureFact]
    public void EditingOnlySurvivorWhileDeletingSiblingDoesNotCreateSplitDobj()
    {
        using var fixture = new GrGdSession();
        var replacement = Triangle(fixture.Targets[1].Id);
        fixture.Write("models", new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", [replacement],
            [fixture.Targets[0].Id]));

        SessionApplier.Apply(fixture.Session, fixture.Output);
        var output = new StageArchive(fixture.Output);
        var actual = ModelIdentity.Capture(output.Layout, fixture.Catalog);
        Assert.DoesNotContain(actual.Nodes, node => node.Id == fixture.Targets[0].Id);
        var survivor = ModelEditing.SelectAll(output.Layout, actual)
            .Single(target => target.Id == fixture.Targets[1].Id);
        Assert.Equal(fixture.Targets[0].DobjOffset, survivor.DobjOffset);
        Assert.Equal(0, survivor.PobjIndex);
        Assert.Equal(fixture.Identity.Nodes.Count - 1, actual.Nodes.Count);
        ModelArchiveWriter.Verify(output.Layout, survivor,
            ModelEditing.Compile(new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", [replacement]), survivor));
    }

    [GrGdFixtureFact]
    public void SplitDeleteAndAdditionOnOneJobjRemainEditableAfterReimport()
    {
        using var fixture = new GrGdSession();
        var replaced = fixture.Targets[1];
        var deleted = ModelEditing.SelectAll(fixture.Source.Layout, fixture.Identity)
            .First(target => target.GroupIndex == replaced.GroupIndex
                && target.JobjIndex == replaced.JobjIndex
                && target.DobjOffset != replaced.DobjOffset);
        string jobjId = fixture.Identity.Nodes.Single(node => node.Kind == "jobj"
            && node.GroupIndex == replaced.GroupIndex && node.Index == replaced.JobjIndex).Id;
        var additionTarget = ModelAddition.Select(fixture.Source, fixture.Identity)
            .Single(target => target.Id == jobjId
                && target.Placement == ModelAddition.ExistingJobjPlacement);
        fixture.Write("models", new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local",
            [Triangle(replaced.Id)], [deleted.Id]));

        string materialId = Guid.NewGuid().ToString("N");
        var part = new ModelAdditionPart(Guid.NewGuid().ToString("N"), materialId,
            [new(0, 0, 0), new(1, 0, 0), new(0, 1, 0)], [0, 1, 2],
            [new(0, 0, 1), new(0, 0, 1), new(0, 0, 1)], null);
        var material = new ModelAdditionMaterial(materialId, "Constant",
            new ColorData(1, 1, 1, 1), null, "repeat", "repeat", "linear", "linear",
            ModelAdditionEditing.MaterialPreset);
        fixture.Write("additions", new ModelAdditionEdits(SessionExtractor.ProtocolVersion,
            ModelAddition.SchemaVersion, "game-joint-local",
            [new(Guid.NewGuid().ToString("N"), "Mixed graph addition",
                additionTarget.Placement, additionTarget.Id, [part])], [material], []));

        SessionApplier.Apply(fixture.Session, fixture.Output);
        var output = new StageArchive(fixture.Output);
        var actual = ModelIdentity.Capture(output.Layout, fixture.Catalog);
        Assert.Empty(output.Validate());
        Assert.DoesNotContain(actual.Nodes, node => node.Id == deleted.Id);
        var replacedPobj = actual.Nodes.Single(node => node.Id == replaced.Id);
        var splitDobj = actual.Nodes.Single(node => node.Id == replacedPobj.OwnerId);
        Assert.NotEqual(fixture.Targets[0].DobjOffset, splitDobj.SourceOffset);
        Assert.False(output.Layout.Pointers.ContainsKey(deleted.DobjOffset + 0x0C));
        Assert.Equal(fixture.Identity.Nodes.Count + 2, actual.Nodes.Count);

        string reimport = Path.Combine(fixture.Root, "reimport");
        SessionExtractor.Extract(output, reimport);
        var (reimportIdentity, reimportCatalog) = ReadIdentity(reimport, output);
        var snapshot = ModelSourceSnapshot.Capture(output.Layout, reimportIdentity);
        var added = snapshot.EditableModels.First(model =>
            model.PobjOffset >= fixture.Source.Layout.DataSize);
        var geometry = snapshot.Models[added.Id].Geometry!;
        var moved = new ModelEdit(added.Id,
            geometry.Positions.Select(position => position with { X = position.X + 0.125f }).ToArray(),
            geometry.TriangleIndices);
        File.WriteAllText(Path.Combine(reimport, "edits/models.json"),
            JsonSerializer.Serialize(new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", [moved]),
                GrGdSession.Json));
        string secondOutput = Path.Combine(fixture.Root, "reimported-out.dat");
        var reapplied = SessionApplier.Apply(reimport, secondOutput);
        var editedAgain = new StageArchive(secondOutput);
        Assert.True(reapplied.ModelChanged);
        Assert.Empty(editedAgain.Validate());
        Assert.Contains(ModelIdentity.Capture(editedAgain.Layout, reimportCatalog).Nodes,
            node => node.Id == added.Id);
    }

    private static (ModelIdentitySnapshot Identity, ModelIdentityCatalog Catalog) ReadIdentity(
        string session, StageArchive source)
    {
        using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(session, "stage.json")));
        var nodes = new List<ModelIdentityNode>();
        foreach (var entry in manifest.RootElement.GetProperty("modelGroups").EnumerateArray())
        {
            using var group = JsonDocument.Parse(File.ReadAllText(Path.Combine(session,
                entry.GetProperty("file").GetString()!)));
            nodes.AddRange(group.RootElement.GetProperty("nodes")
                .Deserialize<ModelIdentityNode[]>(GrGdSession.Json)!);
        }
        var catalog = ModelIdentityCatalog.Restore(nodes);
        return (ModelIdentity.Capture(source.Layout, catalog), catalog);
    }

    private static EditableModel[] SharedTargets(StageArchive source, ModelIdentitySnapshot identity) =>
        ModelEditing.SelectAll(source.Layout, identity)
            .Where(target => target.GroupIndex == 2 && target.JobjIndex == 7
                && target.DobjIndex == 2)
            .OrderBy(target => target.PobjIndex).ToArray();

    private sealed class GrGdSession : IDisposable
    {
        internal static readonly JsonSerializerOptions Json = new()
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            PropertyNameCaseInsensitive = true
        };

        public string Root { get; } = Path.Combine(Path.GetTempPath(),
            "mme-model-graph-" + Guid.NewGuid().ToString("N"));
        public string Session => Path.Combine(Root, "session");
        public string Output => Path.Combine(Root, "out.dat");
        public StageArchive Source { get; } = new(
            Path.Combine(CorpusTests.CorpusDirectory, "GrGd.dat"));
        public ModelIdentityCatalog Catalog { get; }
        public ModelIdentitySnapshot Identity { get; }
        public EditableModel[] Targets { get; }

        public GrGdSession()
        {
            SessionExtractor.Extract(Source, Session);
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(Session, "stage.json")));
            var nodes = new List<ModelIdentityNode>();
            foreach (var entry in manifest.RootElement.GetProperty("modelGroups").EnumerateArray())
            {
                using var group = JsonDocument.Parse(File.ReadAllText(Path.Combine(Session,
                    entry.GetProperty("file").GetString()!)));
                nodes.AddRange(group.RootElement.GetProperty("nodes")
                    .Deserialize<ModelIdentityNode[]>(Json)!);
            }
            Catalog = ModelIdentityCatalog.Restore(nodes);
            Identity = ModelIdentity.Capture(Source.Layout, Catalog);
            Targets = SharedTargets(Source, Identity);
            Assert.Equal(2, Targets.Length);
        }

        public void Write(string kind, object value) => File.WriteAllText(
            Path.Combine(Session, $"edits/{kind}.json"), JsonSerializer.Serialize(value, Json));

        public EditableModel[] OutputTargets(StageArchive output)
        {
            var identity = ModelIdentity.Capture(output.Layout, Catalog);
            return ModelEditing.SelectAll(output.Layout, identity)
                .Where(target => Targets.Any(source => source.Id == target.Id))
                .OrderBy(target => Array.FindIndex(Targets, source => source.Id == target.Id))
                .ToArray();
        }

        public void Dispose() => Directory.Delete(Root, true);
    }
}
