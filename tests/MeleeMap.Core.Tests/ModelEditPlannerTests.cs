using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class ModelEditPlannerTests
{
    [GrGdFixtureFact]
    public void PlansFinalOwnershipBindingsAndDependenciesWithoutWriting()
    {
        var archive = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrGd.dat"));
        var identity = ModelIdentity.Capture(archive.Layout, new ModelIdentityCatalog());
        var source = ModelSourceSnapshot.Capture(archive.Layout, identity);
        var siblings = source.EditableModels.Where(target => target.GroupIndex == 2
                && target.JobjIndex == 7 && target.DobjIndex == 2)
            .OrderBy(target => target.PobjIndex).ToArray();
        Assert.Equal(2, siblings.Length);
        var donor = source.Materials.First(material => !material.UsesUv);
        var propertyTarget = source.EditableMaterialProperties
            .First(material => material.Id == siblings[0].Id);
        var replacement = new ModelEdit(siblings[1].Id,
            [new(0, 0, 0), new(10, 0, 0), new(0, 10, 0)], [0, 1, 2], donor.Id);
        byte[] sourceBytes = archive.Layout.Bytes.ToArray();

        var plan = ModelEditPlanner.Plan(source,
            new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", [replacement]),
            siblings.Select(target => target.Id),
            new MaterialPropertyEdits(SessionExtractor.ProtocolVersion,
                [new MaterialPropertyEdit(propertyTarget.Id, Ambient: [1, 2, 3])]),
            [propertyTarget.Id]);

        Assert.Equal(sourceBytes, archive.Layout.Bytes);
        var change = Assert.Single(plan.Changes);
        Assert.Equal(siblings[1].Id, change.Source.Pobj.Id);
        Assert.Equal(siblings[1].DobjOffset, change.Source.Locator.DobjOffset);
        Assert.Equal(ModelGeometryChangeKind.Replacement, change.Kind);
        Assert.Equal(donor.Id, plan.FinalMaterialAssignments[siblings[1].Id]);
        Assert.Contains(plan.MaterialDependencies,
            dependency => dependency.MaterialId == donor.Id
                && dependency.ConsumerIds.Contains(siblings[1].Id));
        var split = Assert.Single(plan.Graph.Splits);
        Assert.Equal(siblings[1].Id, split.PobjId);
        Assert.Equal(siblings[0].DobjOffset,
            identity.Nodes.Single(node => node.Id == split.SourceDobjId).SourceOffset);
    }

    [PrimaryFixtureFact]
    public void PlanningOrderIsStableAndUnsupportedOperationsFailBeforeExecution()
    {
        var archive = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
        var identity = ModelIdentity.Capture(archive.Layout, new ModelIdentityCatalog());
        var source = ModelSourceSnapshot.Capture(archive.Layout, identity);
        var targets = source.EditableModels.Where(target => !target.PositionsOnly)
            .Take(2).ToArray();
        var edits = targets.Select(target =>
        {
            var mesh = source.Models[target.Id].Geometry!;
            return new ModelEdit(target.Id,
                mesh.Positions.Select(position => position with { X = position.X + 1 }).ToArray(),
                mesh.TriangleIndices);
        }).ToArray();
        var forward = ModelEditPlanner.Plan(source,
            new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", edits), targets.Select(target => target.Id),
            null, []);
        var reverse = ModelEditPlanner.Plan(source,
            new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", edits.Reverse().ToArray()),
            targets.Select(target => target.Id), null, []);
        Assert.Equal(forward.Changes.Select(change => change.Source.Pobj.Id),
            reverse.Changes.Select(change => change.Source.Pobj.Id));

        var animated = source.EditableModels.First(target => target.PositionsOnly);
        var replacement = new ModelEdit(animated.Id,
            [new(0, 0, 0), new(10, 0, 0), new(0, 10, 0)], [0, 1, 2]);
        var error = Assert.Throws<StageException>(() => ModelEditPlanner.Plan(source,
            new ModelEdits(SessionExtractor.ProtocolVersion, "game-joint-local", [replacement]), [animated.Id],
            null, []));
        Assert.Equal("MODEL_POSITION_ONLY", error.Code);
    }

    [PrimaryFixtureFact]
    public void SnapshotExposesExplicitCapabilitiesForEverySourcePobj()
    {
        var archive = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
        var identity = ModelIdentity.Capture(archive.Layout, new ModelIdentityCatalog());
        var source = ModelSourceSnapshot.Capture(archive.Layout, identity);
        Assert.Equal(identity.Nodes.Count(node => node.Kind == "pobj"), source.Models.Count);

        var animated = source.Models.Values.First(model => model.HasMaterialAnimation
            && model.EditableTarget != null);
        Assert.True(animated.Capabilities.VertexMovement.Allowed);
        Assert.True(animated.Capabilities.WholeObjectDeletion.Allowed);
        Assert.False(animated.Capabilities.TopologyReplacement.Allowed);
        Assert.Equal("MODEL_POSITION_ONLY",
            animated.Capabilities.TopologyReplacement.ReasonCode);

        var full = source.Models.Values.First(model =>
            model.Capabilities.TopologyReplacement.Allowed);
        Assert.True(full.Capabilities.UvEditing.Allowed);
        Assert.True(full.Capabilities.MaterialAssignment.Allowed);
        Assert.NotEmpty(source.IncomingReferences[full.Locator.PobjOffset]);
    }
}
