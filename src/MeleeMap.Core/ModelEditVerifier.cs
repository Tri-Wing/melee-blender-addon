namespace MeleeMap.Core;

/// <summary>Independently decodes and verifies the final model transaction.</summary>
public static class ModelEditVerifier
{
    public static void Verify(ArchiveLayout archive, ModelIdentityCatalog catalog,
        ModelEditPlan plan, ModelEditExecution execution,
        IReadOnlySet<int>? ignoredJobjOffsets = null)
    {
        ignoredJobjOffsets ??= new HashSet<int>();
        var captured = ModelIdentity.Capture(archive, catalog);
        var ignored = captured.Nodes.Where(node =>
            ignoredJobjOffsets.Contains(node.SourceOffset)).ToArray();
        ArchiveLayout.Require(ignored.Length == ignoredJobjOffsets.Count
            && ignored.All(node => node.Kind == "jobj"),
            "MODEL_GRAPH_MISMATCH",
            "Gameplay additions did not resolve to the expected ordinary JOBJ descriptors.");
        if (execution.AdditionWrite == null)
            ModelGraphVerifier.Verify(plan.Graph,
                captured.WithoutSourceOffsets(ignoredJobjOffsets));
        else
        {
            ModelAdditionVerifier.Verify(execution.AdditionBase!, archive,
                execution.GraphIdentity, plan.Additions!, ignoredJobjOffsets);
            ModelAdditionArchiveWriter.Verify(execution.AdditionBase!, archive,
                execution.GraphIdentity, execution.AdditionWrite,
                verifyPreservation: false,
                ignoredJobjOffsets: ignoredJobjOffsets);
        }

        foreach (var change in execution.Changes)
            if (change.Kind == ModelGeometryChangeKind.AppearancePreserving)
                ModelPositionWriter.Verify(archive, plan.Source.Archive,
                    change.Target, change.Geometry,
                    execution.MaterialWrite != null
                        && execution.MaterialWrite.Bindings.TryGetValue(change.Target.Id,
                            out int positionMaterial)
                            ? positionMaterial : null);
            else
                ModelArchiveWriter.Verify(archive, change.Target, change.Geometry,
                    execution.MaterialWrite != null
                        && execution.MaterialWrite.Bindings.TryGetValue(change.Target.Id,
                            out int replacementMaterial)
                            ? replacementMaterial : change.AssignedMaterial?.MobjOffset,
                    change.Culling);
        if (execution.MaterialWrite != null)
            MaterialProperties.Verify(archive, execution.GraphIdentity,
                execution.MaterialWrite);
    }
}
