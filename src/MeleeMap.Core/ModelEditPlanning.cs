using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public enum ModelGeometryChangeKind
{
    AppearancePreserving,
    Replacement
}

public sealed record PlannedModelChange(ModelSourceModel Source, ModelEdit Request,
    MeshData Geometry, ModelGeometryChangeKind Kind, ModelMaterial? AssignedMaterial,
    int Culling);

public sealed record ModelMaterialDependency(string MaterialId,
    IReadOnlyList<string> ConsumerIds);

public sealed record ModelEditPlan(ModelSourceSnapshot Source,
    IReadOnlyList<PlannedModelChange> Changes,
    IReadOnlyList<ModelSourceModel> Deletions,
    MaterialPropertyEdits? MaterialEdits,
    IReadOnlyList<string> DeclaredMaterialIds,
    IReadOnlyDictionary<string, string?> FinalMaterialAssignments,
    IReadOnlyList<ModelMaterialDependency> MaterialDependencies,
    ModelGraphPlan Graph,
    PlannedModelAdditions? Additions);

/// <summary>Validates and compiles a complete model transaction without writing archive bytes.</summary>
public static class ModelEditPlanner
{
    public static ModelEditPlan Plan(ModelSourceSnapshot source,
        ModelEdits? modelEdits, IEnumerable<string> declaredModelIds,
        MaterialPropertyEdits? materialEdits, IEnumerable<string> declaredMaterialIds,
        ValidatedModelAdditionBatch? additions = null)
    {
        var declaredModels = declaredModelIds.ToHashSet(StringComparer.Ordinal);
        var declaredMaterials = declaredMaterialIds.ToHashSet(StringComparer.Ordinal);
        var changes = new List<PlannedModelChange>();
        var deletions = new List<ModelSourceModel>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var reader = new ArchiveDataReader(source.Archive);

        if (modelEdits != null)
        {
            Require(modelEdits.ProtocolVersion == SessionExtractor.ProtocolVersion
                && modelEdits.CoordinateSpace == "game-joint-local",
                "MODEL_EDIT_VERSION", "Model edits must use the current protocol and game-joint-local coordinates.");
            Require(modelEdits.Meshes != null && (modelEdits.Meshes.Length > 0
                || modelEdits.DeletedIds is { Length: > 0 }),
                "MODEL_EDIT_FORMAT", "Empty model edit document.");
            foreach (var edit in modelEdits.Meshes)
            {
                source.Models.TryGetValue(edit?.Id ?? "", out var model);
                Require(edit != null && edit.Id != null && seen.Add(edit.Id)
                    && declaredModels.Contains(edit.Id) && model?.EditableTarget != null,
                    "MODEL_EDIT_TARGET", "Model edit target is unsupported, duplicated, or absent from this session.");
                var target = model!;
                var original = target.Geometry
                    ?? throw new StageException("MODEL_EDIT_TARGET", "Editable model source geometry is unavailable.");
                ModelCapabilityAnalyzer.RequireAllowed(target, ModelOperationKind.VertexMovement);
                bool sameTopology = ModelEditing.HasSameTopology(edit!, original);
                if (!sameTopology)
                    ModelCapabilityAnalyzer.RequireAllowed(target, ModelOperationKind.TopologyReplacement);
                if (edit!.TexCoords != null)
                    ModelCapabilityAnalyzer.RequireAllowed(target, ModelOperationKind.UvEditing);
                if (edit.Colors0 != null || edit.Colors1 != null)
                    ModelCapabilityAnalyzer.RequireAllowed(target, ModelOperationKind.VertexColorEditing);
                if (edit.SourceMaterialId != null || edit.UseGreyMaterial)
                    ModelCapabilityAnalyzer.RequireAllowed(target, ModelOperationKind.MaterialAssignment);

                ModelMaterial? material = null;
                if (edit.SourceMaterialId != null)
                {
                    material = source.Materials.FirstOrDefault(candidate => candidate.Id == edit.SourceMaterialId);
                    Require(material != null, "MODEL_MATERIAL",
                        "Assigned material is not a supported static opaque stage material.");
                }
                Require(material == null || !material.UsesUv || edit.TexCoords != null,
                    "MODEL_UV", "The assigned textured material requires UV coordinates for every triangle corner.");
                var compiled = ModelEditing.Compile(modelEdits with { Meshes = [edit] },
                    target.EditableTarget!, original);
                bool preserve = ModelEditing.PreservesAppearance(edit, original,
                    target.EditableTarget!);
                if (!preserve)
                    ModelCapabilityAnalyzer.RequireAllowed(target, ModelOperationKind.TopologyReplacement);
                int culling = material != null && sameTopology
                    ? reader.UShort(target.Locator.PobjOffset + 12) & 0xC000 : 0x4000;
                changes.Add(new(target, edit, compiled,
                    preserve ? ModelGeometryChangeKind.AppearancePreserving
                        : ModelGeometryChangeKind.Replacement,
                    material, culling));
            }
            foreach (string id in modelEdits.DeletedIds ?? [])
            {
                source.Models.TryGetValue(id ?? "", out var model);
                Require(id != null && seen.Add(id) && declaredModels.Contains(id)
                    && model?.EditableTarget != null, "MODEL_DELETE_TARGET",
                    "Deleted model target is unsupported, duplicated, edited, or absent from this session.");
                ModelCapabilityAnalyzer.RequireAllowed(model!, ModelOperationKind.WholeObjectDeletion);
                deletions.Add(model!);
            }
        }

        changes = changes.OrderBy(change => change.Source.Pobj.GroupIndex)
            .ThenBy(change => change.Source.Locator.PobjOffset).ToList();
        deletions = deletions.OrderBy(model => model.Pobj.GroupIndex)
            .ThenBy(model => model.Locator.PobjOffset).ToList();

        var assignments = source.EditableModels.ToDictionary(model => model.Id,
            model => (string?)model.Id, StringComparer.Ordinal);
        foreach (var change in changes.Where(change =>
                     change.Kind == ModelGeometryChangeKind.Replacement))
            assignments[change.Source.Pobj.Id] = change.AssignedMaterial?.Id;
        foreach (var deletion in deletions) assignments.Remove(deletion.Pobj.Id);

        MaterialPropertyEdits? normalizedMaterialEdits = null;
        if (materialEdits != null)
        {
            foreach (var edit in materialEdits.Materials ?? [])
                if (edit != null && source.Models.TryGetValue(edit.Id ?? "", out var model))
                    ModelCapabilityAnalyzer.RequireAllowed(model,
                        ModelOperationKind.MaterialPropertyEditing);
            MaterialProperties.Validate(materialEdits, declaredMaterials, assignments,
                source.EditableMaterialProperties);
            var order = source.EditableMaterialProperties
                .Select((material, index) => (material.Id, index))
                .ToDictionary(item => item.Id, item => item.index, StringComparer.Ordinal);
            normalizedMaterialEdits = materialEdits with
            {
                Materials = materialEdits.Materials!.OrderBy(edit => order[edit.Id]).ToArray()
            };
        }

        var splitRequests = changes.Where(change => change.Source.SharesDobj
                && change.Kind == ModelGeometryChangeKind.Replacement)
            .Select(change => change.Source.Pobj.Id)
            .Concat((normalizedMaterialEdits?.Materials ?? [])
                .Where(edit => source.Models.TryGetValue(edit.Id, out var model)
                    && model.SharesDobj)
                .Select(edit => edit.Id));
        var graph = ModelGraphPlanner.Plan(source.Identity, splitRequests,
            deletions.Select(model => model.Pobj.Id));
        var dependencies = assignments.Where(assignment => assignment.Value != null)
            .GroupBy(assignment => assignment.Value!, StringComparer.Ordinal)
            .OrderBy(group => group.Key, StringComparer.Ordinal)
            .Select(group => new ModelMaterialDependency(group.Key,
                group.Select(assignment => assignment.Key).Order(StringComparer.Ordinal).ToArray()))
            .ToArray();
        return new(source, changes.AsReadOnly(), deletions.AsReadOnly(),
            normalizedMaterialEdits, declaredMaterials.Order(StringComparer.Ordinal).ToArray(),
            assignments, dependencies, graph,
            additions == null ? null : ModelAdditionPlanner.Plan(additions));
    }
}

public sealed record ResolvedModelChange(EditableModel Target, MeshData Geometry,
    ModelGeometryChangeKind Kind, ModelMaterial? AssignedMaterial, int Culling,
    bool ReverseWinding);

public sealed record ModelEditExecution(byte[] Bytes,
    ModelIdentitySnapshot GraphIdentity,
    IReadOnlyList<ResolvedModelChange> Changes,
    MaterialPropertyWrite? MaterialWrite,
    ArchiveLayout? AdditionBase,
    ModelAdditionWrite? AdditionWrite);

/// <summary>
/// Executes a validated model plan through the shared graph and archive layers.
/// </summary>
public static class ModelEditExecutor
{
    public static ModelEditExecution Execute(ArchiveLayout current,
        ModelIdentityCatalog catalog, ModelEditPlan plan)
    {
        var builder = new ArchiveMutationBuilder(current);
        ModelGraphEditor.Apply(builder, plan.Source.Identity, plan.Graph);
        var currentIdentity = ModelIdentity.Capture(builder.BuildLayout(), catalog);
        var resolved = plan.Changes.Select(change => new ResolvedModelChange(
            ResolveTarget(currentIdentity, change.Source), change.Geometry, change.Kind,
            change.AssignedMaterial, change.Culling,
            change.Request.ReverseWinding)).ToArray();
        foreach (var change in resolved)
            if (change.Kind == ModelGeometryChangeKind.AppearancePreserving)
                ModelPositionWriter.Write(builder, change.Target, change.Geometry,
                    change.ReverseWinding);
            else ModelArchiveWriter.Write(builder, change.Target, change.Geometry,
                change.AssignedMaterial?.MobjOffset, change.Culling);
        MaterialPropertyWrite? materialWrite = null;
        if (plan.MaterialEdits != null)
        {
            materialWrite = MaterialProperties.Write(plan.Source.Archive,
                builder, plan.Source.Identity, currentIdentity,
                plan.MaterialEdits, plan.DeclaredMaterialIds.ToArray(),
                plan.FinalMaterialAssignments, plan.Source.EditableModels);
        }
        var additionBase = builder.BuildLayout();
        ModelGraphVerifier.Verify(plan.Graph,
            ModelIdentity.Capture(additionBase, catalog));
        ModelAdditionWrite? additionWrite = null;
        if (plan.Additions != null)
            additionWrite = ModelAdditionArchiveWriter.Write(builder, additionBase,
                currentIdentity, plan.Additions);
        byte[] bytes = additionWrite?.Bytes ?? additionBase.Bytes;
        return new(bytes, currentIdentity, resolved, materialWrite,
            plan.Additions == null ? null : additionBase, additionWrite);
    }

    private static EditableModel ResolveTarget(ModelIdentitySnapshot finalIdentity,
        ModelSourceModel source)
    {
        var byId = finalIdentity.Nodes.ToDictionary(node => node.Id,
            StringComparer.Ordinal);
        var pobj = byId.GetValueOrDefault(source.Pobj.Id);
        var dobj = pobj?.OwnerId == null ? null : byId.GetValueOrDefault(pobj.OwnerId);
        var jobj = dobj?.OwnerId == null ? null : byId.GetValueOrDefault(dobj.OwnerId);
        Require(pobj?.Kind == "pobj" && dobj?.Kind == "dobj" && jobj?.Kind == "jobj",
            "MODEL_FINAL_LOCATOR", "A planned source model has no final descriptor locator.");
        var siblings = finalIdentity.Nodes.Where(node => node.Kind == "pobj"
                && node.OwnerId == dobj!.Id).OrderBy(node => node.Index).ToArray();
        int index = Array.FindIndex(siblings, node => node.Id == pobj!.Id);
        Require(index >= 0 && pobj.Index == index, "MODEL_FINAL_LOCATOR",
            "A planned source model has inconsistent final list membership.");
        int linkField = index == 0 ? dobj!.SourceOffset + 12
            : siblings[index - 1].SourceOffset + 4;
        return new(pobj.Id, pobj.GroupIndex, jobj!.Index, dobj.Index, pobj.Index,
            pobj.SourceOffset, dobj.SourceOffset,
            source.EditableTarget!.PositionsOnly, linkField, siblings.Length > 1);
    }
}
