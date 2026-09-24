namespace MeleeMap.Core;

/// <summary>Independently decodes and verifies the final model transaction.</summary>
public static class ModelEditVerifier
{
    public static void Verify(ArchiveLayout archive, ModelIdentityCatalog catalog,
        ModelEditPlan plan, ModelEditExecution execution)
    {
        if (execution.AdditionWrite == null)
            ModelGraphVerifier.Verify(plan.Graph, ModelIdentity.Capture(archive, catalog));
        else
        {
            ModelAdditionVerifier.Verify(execution.AdditionBase!, archive,
                execution.GraphIdentity, plan.Additions!);
            ModelAdditionArchiveWriter.Verify(execution.AdditionBase!, archive,
                execution.GraphIdentity, execution.AdditionWrite,
                verifyPreservation: false);
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
