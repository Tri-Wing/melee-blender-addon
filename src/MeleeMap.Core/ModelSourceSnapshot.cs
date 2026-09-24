using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public enum ModelOperationKind
{
    VertexMovement,
    TopologyReplacement,
    UvEditing,
    VertexColorEditing,
    MaterialAssignment,
    MaterialPropertyEditing,
    WholeObjectDeletion
}

public sealed record ModelOperationCapability(bool Allowed, string? ReasonCode = null,
    string? Reason = null);

public sealed record ModelOperationCapabilities(
    ModelOperationCapability VertexMovement,
    ModelOperationCapability TopologyReplacement,
    ModelOperationCapability UvEditing,
    ModelOperationCapability VertexColorEditing,
    ModelOperationCapability MaterialAssignment,
    ModelOperationCapability MaterialPropertyEditing,
    ModelOperationCapability WholeObjectDeletion)
{
    public ModelOperationCapability For(ModelOperationKind operation) => operation switch
    {
        ModelOperationKind.VertexMovement => VertexMovement,
        ModelOperationKind.TopologyReplacement => TopologyReplacement,
        ModelOperationKind.UvEditing => UvEditing,
        ModelOperationKind.VertexColorEditing => VertexColorEditing,
        ModelOperationKind.MaterialAssignment => MaterialAssignment,
        ModelOperationKind.MaterialPropertyEditing => MaterialPropertyEditing,
        ModelOperationKind.WholeObjectDeletion => WholeObjectDeletion,
        _ => throw new ArgumentOutOfRangeException(nameof(operation))
    };
}

public sealed record ModelSourceLocator(string PobjId, int PobjOffset,
    string DobjId, int DobjOffset, string JobjId, int JobjOffset);

public sealed record ModelSourceModel(ModelSourceLocator Locator,
    ModelIdentityNode Pobj, ModelIdentityNode Dobj, ModelIdentityNode Jobj,
    MeshData? Geometry, EditableModel? EditableTarget, int? SourceMobjOffset,
    bool SharesDobj, bool HasMaterialAnimation, string? ReadOnlyReasonCode,
    string? ReadOnlyReason, ModelOperationCapabilities Capabilities);

internal sealed record StructuralModelAnalysis(ModelIdentityNode Pobj,
    ModelIdentityNode Dobj, ModelIdentityNode Jobj, MeshData? Geometry,
    EditableModel? EditableTarget, int? SourceMobjOffset, bool SharesDobj,
    bool HasMaterialAnimation, string? ReadOnlyReasonCode, string? ReadOnlyReason);

/// <summary>
/// Immutable source-side facts used by extraction, planning, and verification.
/// Source locators are never replaced with transaction allocations.
/// </summary>
public sealed class ModelSourceSnapshot
{
    public ArchiveLayout Archive { get; }
    public ModelIdentitySnapshot Identity { get; }
    public IReadOnlyDictionary<string, ModelSourceModel> Models { get; }
    public IReadOnlyList<EditableModel> EditableModels { get; }
    public IReadOnlyList<ModelMaterial> Materials { get; }
    public IReadOnlyList<EditableMaterialProperties> EditableMaterialProperties { get; }
    public IReadOnlyDictionary<int, int[]> IncomingReferences { get; }

    private ModelSourceSnapshot(ArchiveLayout archive, ModelIdentitySnapshot identity,
        IReadOnlyDictionary<string, ModelSourceModel> models,
        IReadOnlyList<EditableModel> editableModels,
        IReadOnlyList<ModelMaterial> materials,
        IReadOnlyList<EditableMaterialProperties> editableMaterialProperties,
        IReadOnlyDictionary<int, int[]> incomingReferences)
    {
        Archive = archive;
        Identity = identity;
        Models = models;
        EditableModels = editableModels;
        Materials = materials;
        EditableMaterialProperties = editableMaterialProperties;
        IncomingReferences = incomingReferences;
    }

    public static ModelSourceSnapshot Capture(ArchiveLayout archive,
        ModelIdentitySnapshot identity)
    {
        var structural = ModelCapabilityAnalyzer.AnalyzeStructure(archive, identity);
        var editableModels = structural.Where(model => model.EditableTarget != null)
            .Select(model => model.EditableTarget!).ToArray();
        var materials = ModelMaterials.Select(archive, editableModels);
        var editableProperties = MaterialProperties.Select(archive, editableModels);
        var propertyIds = editableProperties.Select(material => material.Id)
            .ToHashSet(StringComparer.Ordinal);
        var models = structural.Select(model =>
        {
            var capabilities = ModelCapabilityAnalyzer.Analyze(model, propertyIds);
            return new ModelSourceModel(new(model.Pobj.Id, model.Pobj.SourceOffset,
                    model.Dobj.Id, model.Dobj.SourceOffset, model.Jobj.Id,
                    model.Jobj.SourceOffset), model.Pobj, model.Dobj, model.Jobj,
                model.Geometry, model.EditableTarget, model.SourceMobjOffset,
                model.SharesDobj, model.HasMaterialAnimation,
                model.ReadOnlyReasonCode, model.ReadOnlyReason, capabilities);
        }).ToDictionary(model => model.Pobj.Id, StringComparer.Ordinal);
        var incoming = archive.Pointers.GroupBy(pointer => pointer.Value)
            .ToDictionary(group => group.Key,
                group => group.Select(pointer => pointer.Key).Order().ToArray());
        return new(archive, identity, models, editableModels, materials,
            editableProperties, incoming);
    }

    public ModelSourceModel RequireModel(string id)
    {
        Models.TryGetValue(id ?? "", out var model);
        Require(model != null,
            "MODEL_EDIT_TARGET", "Model edit target is absent from the source graph.");
        return model!;
    }
}

/// <summary>Single backend authority for model operation permissions.</summary>
public static class ModelCapabilityAnalyzer
{
    internal static StructuralModelAnalysis[] AnalyzeStructure(ArchiveLayout archive,
        ModelIdentitySnapshot identity)
    {
        var result = new List<StructuralModelAnalysis>();
        var reader = new ArchiveDataReader(archive);
        var nodes = identity.Nodes;
        var byId = nodes.GroupBy(node => node.Id)
            .ToDictionary(group => group.Key, group => group.First(), StringComparer.Ordinal);
        foreach (var pobj in nodes.Where(node => node.Kind == "pobj"))
        {
            var dobj = byId[pobj.OwnerId!];
            var jobj = byId[dobj.OwnerId!];
            var group = nodes.First(node => node.GroupIndex == pobj.GroupIndex
                && node.Kind is "group" or "sentinel-group");
            var siblings = nodes.Where(node => node.OwnerId == dobj.Id && node.Kind == "pobj")
                .OrderBy(node => node.Index).ToArray();
            int siblingIndex = Array.FindIndex(siblings, node => node.Id == pobj.Id);
            bool sharesDobj = siblings.Length > 1;
            StructuralModelAnalysis Denied(string code, string reason,
                MeshData? geometry = null, int? material = null, bool animated = false) =>
                new(pobj, dobj, jobj, geometry, null, material, sharesDobj,
                    animated, code, reason);

            if (group.Kind != "group" || jobj.Kind != "jobj" || siblingIndex < 0
                || pobj.Index != siblingIndex
                || nodes.Count(node => node.SourceOffset == pobj.SourceOffset && node.Kind == "pobj") != 1
                || nodes.Count(node => node.SourceOffset == dobj.SourceOffset && node.Kind == "dobj") != 1)
            {
                result.Add(Denied("MODEL_CAP_IDENTITY",
                    "Shared or inconsistent model descriptors."));
                continue;
            }
            try
            {
                int? material = reader.Pointer(dobj.SourceOffset + 8);
                if (reader.Pointer(pobj.SourceOffset + 20) != null
                    || (reader.UShort(pobj.SourceOffset + 12) & ~0xC001) != 0)
                {
                    result.Add(Denied("MODEL_CAP_BINDING",
                        "Skinned, shared-joint, or shape-bound geometry.", material: material));
                    continue;
                }
                if (reader.Pointer(group.SourceOffset + 12) != null)
                {
                    result.Add(Denied("MODEL_CAP_SHAPE_ANIMATION",
                        "This group has shape animation.", material: material));
                    continue;
                }
                if (material == null || reader.Pointer(pobj.SourceOffset) != null
                    || reader.Pointer(dobj.SourceOffset) != null
                    || reader.Pointer(material.Value) != null
                    || (reader.Int(jobj.SourceOffset + 4) & (0x20000 | 0x1000 | 0xE00)) != 0)
                {
                    result.Add(Denied("MODEL_CAP_CUSTOM_STRUCTURE",
                        "Custom classes, instancing, or billboard transforms.", material: material));
                    continue;
                }
                int linkField = siblingIndex == 0 ? dobj.SourceOffset + 12
                    : siblings[siblingIndex - 1].SourceOffset + 4;
                if (archive.Pointers.Count(pointer => pointer.Value == pobj.SourceOffset) != 1
                    || !archive.Pointers.TryGetValue(linkField, out int target)
                    || target != pobj.SourceOffset
                    || archive.Pointers.Count(pointer => pointer.Value == dobj.SourceOffset) != 1)
                {
                    result.Add(Denied("MODEL_CAP_SHARED_DESCRIPTOR",
                        "Shared model descriptors.", material: material));
                    continue;
                }
                bool HasInteriorReference(int start, int size) =>
                    archive.Pointers.Values.Any(offset => offset > start && offset < start + size)
                    || archive.Roots.Concat(archive.References)
                        .Any(root => root.Offset >= start && root.Offset < start + size);
                if (HasInteriorReference(pobj.SourceOffset, 24)
                    || HasInteriorReference(dobj.SourceOffset, 16))
                {
                    result.Add(Denied("MODEL_CAP_INTERIOR_REFERENCE",
                        "External or interior model descriptor references.", material: material));
                    continue;
                }
                bool animated = HasMaterialAnimation(group, jobj, dobj.Index);
                var geometry = GxMeshDecoder.Decode(archive, pobj.SourceOffset);
                if (geometry.Envelopes != null || geometry.BoundJobjSourceOffset != null)
                {
                    result.Add(Denied("MODEL_CAP_BINDING",
                        "Skinned or shared-joint geometry.", geometry, material, animated));
                    continue;
                }
                var editable = new EditableModel(pobj.Id, pobj.GroupIndex, jobj.Index,
                    dobj.Index, pobj.Index, pobj.SourceOffset, dobj.SourceOffset,
                    animated, linkField, sharesDobj);
                result.Add(new(pobj, dobj, jobj, geometry, editable, material,
                    sharesDobj, animated, null, null));
            }
            catch (StageException error)
            {
                result.Add(Denied(error.Code, error.Message));
            }
        }
        return result.ToArray();

        bool HasMaterialAnimation(ModelIdentityNode group, ModelIdentityNode joint,
            int dobjIndex)
        {
            var path = new List<ModelIdentityNode>();
            var cursor = joint;
            while (cursor.OwnerId != group.Id)
            {
                path.Add(cursor);
                cursor = byId[cursor.OwnerId!];
            }
            if (cursor.Index != 0) return true;
            path.Reverse();
            int? array = reader.Pointer(group.SourceOffset + 8);
            if (array == null) return false;
            for (int field = array.Value; ; field += 4)
            {
                int? animation = reader.Pointer(field);
                if (animation == null) return false;
                foreach (var node in path)
                {
                    int sibling = nodes.Where(candidate => candidate.OwnerId == node.OwnerId
                            && candidate.Kind.EndsWith("jobj"))
                        .OrderBy(candidate => candidate.Index)
                        .TakeWhile(candidate => candidate.Id != node.Id).Count();
                    animation = animation.HasValue ? reader.Pointer(animation.Value) : null;
                    for (int i = 0; i < sibling && animation.HasValue; i++)
                        animation = reader.Pointer(animation.Value + 4);
                }
                int? material = animation.HasValue
                    ? reader.Pointer(animation.Value + 8) : null;
                for (int i = 0; i < dobjIndex && material.HasValue; i++)
                    material = reader.Pointer(material.Value);
                if (material.HasValue && (reader.Pointer(material.Value + 4) != null
                    || reader.Pointer(material.Value + 8) != null
                    || reader.Int(material.Value + 12) != 0)) return true;
            }
        }
    }

    internal static ModelOperationCapabilities Analyze(StructuralModelAnalysis model,
        IReadOnlySet<string> editableMaterialIds)
    {
        static ModelOperationCapability Allowed() => new(true);
        static ModelOperationCapability Denied(string code, string reason) =>
            new(false, code, reason);
        if (model.EditableTarget == null)
        {
            var denied = Denied(model.ReadOnlyReasonCode ?? "MODEL_CAP_UNSUPPORTED",
                model.ReadOnlyReason ?? "This model structure is not editable.");
            return new(denied, denied, denied, denied, denied, denied, denied);
        }

        var animated = Denied("MODEL_POSITION_ONLY",
            "Material animation permits vertex movement and whole-object deletion only.");
        var full = model.HasMaterialAnimation ? animated : Allowed();
        var vertexColor = model.HasMaterialAnimation ? animated
            : model.Geometry?.Colors0 != null || model.Geometry?.Colors1 != null
                ? Allowed()
                : Denied("MODEL_CAP_VERTEX_COLOR_INPUT",
                    "The source mesh has no editable vertex-color channel.");
        var materialProperties = editableMaterialIds.Contains(model.Pobj.Id)
            ? Allowed()
            : Denied("MATERIAL_EDIT_TARGET",
                "The source material properties are not supported for editing.");
        return new(Allowed(), full, full, vertexColor, full,
            materialProperties, Allowed());
    }

    public static void RequireAllowed(ModelSourceModel model, ModelOperationKind operation)
    {
        var capability = model.Capabilities.For(operation);
        Require(capability.Allowed, capability.ReasonCode ?? "MODEL_CAP_UNSUPPORTED",
            capability.Reason ?? $"{operation} is not supported for this model.");
    }
}
