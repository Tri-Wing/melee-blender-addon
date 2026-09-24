using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record StageLightEditRequest(StageLightEdits Edits, string[] DeclaredIds);
public sealed record JobjTransformEditRequest(JobjTransformEdits Edits, string[] DeclaredIds);
public sealed record JointAnimationEditRequest(JointAnimationEdits Edits, string[] DeclaredTargets);
public sealed record GameplayEditRequest(GameplayEdits Edits, string[] DeclaredIds);

public sealed record StageEditTransactionPlan(
    CollisionData Collision,
    bool CollisionChanged,
    ModelEditPlan Model,
    StageLightEditRequest? Lights,
    JobjTransformEditRequest? Jobjs,
    JointAnimationEditRequest? Animations,
    GameplayEditRequest? Gameplay);

public sealed record StageEditTransactionExecution(
    byte[] Bytes,
    ModelEditExecution Model,
    StageLightWrite? Lights,
    JobjTransformWrite? Jobjs,
    JointAnimationWrite? Animations,
    GameplayWrite? Gameplay);

/// <summary>
/// Composes the independently owned stage domains into one in-memory transaction.
/// The model domain runs last so its graph, payloads, and additions share one
/// archive builder while preserving every preceding domain result.
/// </summary>
public static class StageEditTransaction
{
    public static StageEditTransactionExecution Execute(StageArchive source,
        ModelIdentityCatalog catalog, ModelIdentitySnapshot modelBaseline,
        StageEditTransactionPlan plan)
    {
        byte[] bytes = plan.CollisionChanged
            ? CollisionArchiveWriter.Write(source.Layout, plan.Collision)
            : source.Layout.Bytes;

        StageLightWrite? lightWrite = null;
        if (plan.Lights != null)
        {
            lightWrite = StageLightEditing.Write(source.Layout, new ArchiveLayout(bytes),
                plan.Lights.Edits, plan.Lights.DeclaredIds);
            bytes = lightWrite.Bytes;
        }

        JobjTransformWrite? jobjWrite = null;
        if (plan.Jobjs != null)
        {
            jobjWrite = JobjEditing.Write(source.Layout, new ArchiveLayout(bytes),
                modelBaseline, plan.Jobjs.Edits, plan.Jobjs.DeclaredIds);
            bytes = jobjWrite.Bytes;
        }

        JointAnimationWrite? animationWrite = null;
        if (plan.Animations != null)
        {
            animationWrite = JointAnimationEditing.Write(source.Layout,
                new ArchiveLayout(bytes), modelBaseline, plan.Animations.Edits,
                plan.Animations.DeclaredTargets);
            bytes = animationWrite.Bytes;
        }

        var model = ModelEditExecutor.Execute(new ArchiveLayout(bytes), catalog,
            plan.Model);
        bytes = model.Bytes;

        GameplayWrite? gameplayWrite = null;
        if (plan.Gameplay != null)
        {
            gameplayWrite = StageGameplayEditing.Write(source.Layout,
                new ArchiveLayout(bytes), plan.Gameplay.Edits,
                plan.Gameplay.DeclaredIds);
            bytes = gameplayWrite.Bytes;
        }

        return new(bytes, model, lightWrite, jobjWrite, animationWrite,
            gameplayWrite);
    }

    public static void Verify(StageArchive source, StageArchive reloaded,
        ModelIdentityCatalog catalog, ModelIdentitySnapshot modelBaseline,
        StageEditTransactionPlan plan, StageEditTransactionExecution execution)
    {
        VerifyCollision(reloaded.Layout, plan.Collision);
        ModelEditVerifier.Verify(reloaded.Layout, catalog, plan.Model, execution.Model,
            execution.Gameplay?.AddedJobjs.Select(jobj => jobj.Offset).ToHashSet());
        if (execution.Lights != null)
            StageLightEditing.Verify(reloaded.Layout, execution.Lights);
        if (execution.Jobjs != null)
            JobjEditing.Verify(reloaded.Layout, modelBaseline, execution.Jobjs);
        if (execution.Animations != null)
            JointAnimationEditing.Verify(reloaded.Layout, modelBaseline,
                execution.Animations);
        if (execution.Gameplay != null)
            StageGameplayEditing.Verify(reloaded.Layout, execution.Gameplay);

        bool modelWrite = plan.Model.Changes.Count > 0
            || plan.Model.Deletions.Count > 0
            || plan.Model.MaterialEdits != null
            || plan.Model.Additions != null;
        if (!plan.CollisionChanged && !modelWrite && plan.Lights == null
            && plan.Jobjs == null && plan.Animations == null
            && plan.Gameplay == null)
            Require(source.Layout.Bytes.SequenceEqual(reloaded.Layout.Bytes),
                "ROUNDTRIP_MISMATCH", "No-edit apply changed archive bytes.");
    }

    private static void VerifyCollision(ArchiveLayout archive, CollisionData expected)
    {
        var actual = CollisionData.Read(archive);
        Require(actual.Vertices.SequenceEqual(expected.Vertices)
            && actual.Lines.SequenceEqual(expected.Lines)
            && actual.Ranges.SequenceEqual(expected.Ranges)
            && actual.Attachments.SequenceEqual(expected.Attachments)
            && actual.Joints.Length == expected.Joints.Length
            && actual.Joints.Zip(expected.Joints).All(pair =>
                pair.First.Ranges.SequenceEqual(pair.Second.Ranges)
                && (pair.First.Left, pair.First.Bottom, pair.First.Right,
                    pair.First.Top, pair.First.VertexStart, pair.First.VertexCount)
                == (pair.Second.Left, pair.Second.Bottom, pair.Second.Right,
                    pair.Second.Top, pair.Second.VertexStart, pair.Second.VertexCount)),
            "COLLISION_WRITE_MISMATCH",
            "Reloaded collision differs from compiled edits.");
    }
}
