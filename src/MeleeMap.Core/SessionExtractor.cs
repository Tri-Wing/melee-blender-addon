using System.Text.Json;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ExtractionResult(string SessionDirectory, int ModelGroups, int CollisionLines, string MeshId, int Triangles, int MeshCount);

public static class SessionExtractor
{
    public const int ProtocolVersion = 4;
    public const int SchemaVersion = 2;
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, WriteIndented = true };

    /// <summary>Extract a complete identity/collision session and every supported polygon.
    /// Payloads stay in game coordinates; a Blender adapter applies the declared reversible transform.</summary>
    public static ExtractionResult Extract(StageArchive stage, string directory)
    {
        directory = Path.GetFullPath(directory);
        Require(!Directory.Exists(directory) && !File.Exists(directory), "SESSION_EXISTS", "Session destination already exists; choose a new directory.");
        var warnings = stage.Validate(); var info = stage.Inspect();
        var catalog = new ModelIdentityCatalog();
        var identity = ModelIdentity.Capture(stage.Layout, catalog);
        var collision = CollisionData.Read(stage.Layout);
        var lighting = StageLightingReader.Read(stage.Layout);
        var atmosphere = StageAtmosphereReader.Read(stage.Layout, lighting.PreviewSetId);
        var camera = StageCameraReader.Read(stage.Layout);
        var gameplay = StageGameplayEditing.Read(stage.Layout);
        var modelSnapshot = ModelSourceSnapshot.Capture(stage.Layout, identity);
        var editableModels = modelSnapshot.EditableModels.ToArray();
        var readOnlyReasons = modelSnapshot.Models.Values
            .Where(model => model.ReadOnlyReason != null)
            .ToDictionary(model => model.Pobj.Id, model => model.ReadOnlyReason!,
                StringComparer.Ordinal);
        var editableJobjs = JobjEditing.Select(stage.Layout, identity, out var jobjReadOnlyReasons);
        var modelAdditionTargets = ModelAddition.Select(stage, identity);
        var reader = new ArchiveDataReader(stage.Layout);
        var meshes = new List<(ModelIdentityNode Node, MeshData Mesh)>();
        var deferredMeshes = new List<object>();
        foreach (var node in identity.Nodes.Where(n => n.Kind == "pobj"))
        {
            try { meshes.Add((node, GxMeshDecoder.Decode(stage.Layout, node.SourceOffset))); }
            catch (StageException e) when (e.Code is "GX_UNSUPPORTED_BINDING" or "GX_ATTRIBUTE" or "GX_PRIMITIVE")
            { deferredMeshes.Add(new { id = node.Id, reason = e.Code, message = e.Message }); }
        }
        Require(meshes.Count > 0, "EXTRACT_NO_MODEL_TARGET", "No supported model target found; session extraction has not been published.");
        var selected = meshes[0].Node;
        var groups = identity.Nodes.Where(n => n.Kind is "group" or "sentinel-group").ToArray();
        var jointAnimationsByGroup = groups.ToDictionary(group => group.GroupIndex,
            group => StageJointAnimations.Read(stage, identity, group.GroupIndex));
        Dictionary<int, MaterialAnimationSet[]> materialAnimationsByGroup = [];
        var vertexIds = collision.Vertices.Select(_ => Guid.NewGuid().ToString("N")).ToArray();
        var lineIds = collision.Lines.Select(_ => Guid.NewGuid().ToString("N")).ToArray();
        var jointIds = collision.Joints.Select(_ => Guid.NewGuid().ToString("N")).ToArray();
        var categories = new int[collision.Lines.Length]; var owners = new int[collision.Lines.Length];
        for (int k = 0; k < 5; k++)
            for (int i = collision.Ranges[k].Start; i < collision.Ranges[k].Start + collision.Ranges[k].Count; i++) categories[i] = k;
        for (int j = 0; j < collision.Joints.Length; j++)
            foreach (var range in collision.Joints[j].Ranges)
                for (int i = range.Start; i < range.Start + range.Count; i++) owners[i] = j;
        string parent = Path.GetDirectoryName(directory)!;
        Directory.CreateDirectory(parent);
        string temporary = Path.Combine(parent, ".meleemap-session-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(temporary);
        try
        {
            File.WriteAllBytes(Path.Combine(temporary, "source.dat"), stage.Layout.Bytes);
            Directory.CreateDirectory(Path.Combine(temporary, "edits"));
            materialAnimationsByGroup = groups.ToDictionary(group => group.GroupIndex,
                group => StageMaterialAnimations.Read(stage, identity, group.GroupIndex, temporary));
            foreach (var group in groups)
            {
                string groupPath = $"models/group-{group.GroupIndex:D3}";
                var nodes = identity.Nodes.Where(n => n.GroupIndex == group.GroupIndex).ToArray();
                var jointAnimations = jointAnimationsByGroup[group.GroupIndex];
                var materialAnimations = materialAnimationsByGroup[group.GroupIndex];
                Write($"{groupPath}/group.json", new
                {
                    protocolVersion = ProtocolVersion, id = group.Id, index = group.GroupIndex, protectedIdentity = true, nodes,
                    joints = nodes.Where(n => n.Kind.EndsWith("jobj")).Select(n => new
                    {
                        id = n.Id, flags = unchecked((uint)reader.Int(n.SourceOffset + 4)),
                        editable = editableJobjs.Any(target => target.Id == n.Id),
                        readOnlyReason = jobjReadOnlyReasons.GetValueOrDefault(n.Id),
                        transformSpace = "game", rotationEncoding = "source-jobj-xyz",
                        rotation = Vec(n.SourceOffset + 0x14), scale = Vec(n.SourceOffset + 0x20), translation = Vec(n.SourceOffset + 0x2C),
                        inverseBindMatrix = InverseBind(n.SourceOffset),
                        childSourceOffset = reader.Pointer(n.SourceOffset + 8), nextSourceOffset = reader.Pointer(n.SourceOffset + 12)
                    }),
                    jointAnimations, materialAnimations,
                    meshes = meshes.Where(m => m.Node.GroupIndex == group.GroupIndex).Select(m => $"mesh-{m.Node.Id}.json")
                });
            }
            string meshPath = $"models/group-{selected.GroupIndex:D3}/mesh-{selected.Id}.json";
            foreach (var (node, mesh) in meshes)
            {
                string? JointId(int? offset)
                {
                    if (!offset.HasValue) return null;
                    var joint = identity.Nodes.FirstOrDefault(n => n.GroupIndex == node.GroupIndex && n.SourceOffset == offset && n.Kind.EndsWith("jobj"));
                    Require(joint != null, "GX_BINDING_TARGET", "Mesh binding points outside its model group.");
                    return joint!.Id;
                }
                Write($"models/group-{node.GroupIndex:D3}/mesh-{node.Id}.json", new
                {
                    protocolVersion = ProtocolVersion, id = node.Id, ownerId = node.OwnerId, groupIndex = node.GroupIndex,
                    pobjIndex = node.Index, sourceOffset = node.SourceOffset, coordinateSpace = "game", representation = "untextured-grey",
                    pobjFlags = reader.UShort(node.SourceOffset + 12),
                    editable = editableModels.Any(t => t.Id == node.Id),
                    operationCapabilities = modelSnapshot.Models[node.Id].Capabilities,
                    readOnlyReason = mesh.Envelopes != null ? "Envelope geometry editing is not supported yet."
                        : mesh.BoundJobjSourceOffset != null ? "Shared-joint binding is not supported yet."
                        : readOnlyReasons.GetValueOrDefault(node.Id),
                    vertexSpace = mesh.Envelopes != null ? "envelope-source" : "joint-local",
                    positions = mesh.Positions, normals = mesh.Normals, triangleIndices = mesh.TriangleIndices,
                    texCoords0 = mesh.TexCoords0, texCoords1 = mesh.TexCoords1,
                    colors0 = mesh.Colors0, colors1 = mesh.Colors1,
                    boundJobjId = JointId(mesh.BoundJobjSourceOffset), envelopeIndices = mesh.EnvelopeIndices,
                    envelopes = mesh.Envelopes?.Select(e => e.Select(w => new { jobjId = JointId(w.JobjSourceOffset), weight = w.Weight }))
                });
            }
            Write("collision/collision.json", new
            {
                protocolVersion = ProtocolVersion, coordinateSpace = "game", ranges = collision.Ranges,
                vertices = collision.Vertices.Select((v, i) => new { id = vertexIds[i], sourceIndex = i, position = v }),
                lines = collision.Lines.Select((l, i) => new
                {
                    id = lineIds[i], sourceIndex = i, vertex0Id = vertexIds[l.Vertex0], vertex1Id = vertexIds[l.Vertex1],
                    jointId = jointIds[owners[i]], category = new[] { "floor", "ceiling", "right-wall", "left-wall", "dynamic" }[categories[i]],
                    readOnly = categories[i] == 4 || warnings.Count > 0,
                    previous0Id = Link(l.Previous0), next0Id = Link(l.Next0), previous1Id = Link(l.Previous1), next1Id = Link(l.Next1),
                    highFlags = l.HighFlags, lowFlags = l.LowFlags, materialId = l.LowFlags & 255,
                    propertyFlags = l.LowFlags >> 8, empty = (l.HighFlags & 0x80) != 0,
                    source = l
                }),
                joints = collision.Joints.Select((j, i) => new { id = jointIds[i], sourceIndex = i, source = j }),
                attachments = collision.Attachments.Select(a => new
                {
                    id = Guid.NewGuid().ToString("N"), groupId = groups.Single(g => g.GroupIndex == a.GroupIndex).Id,
                    jointId = jointIds[a.JointIndex],
                    jobjId = identity.Nodes.FirstOrDefault(n => n.GroupIndex == a.GroupIndex && n.Kind.EndsWith("jobj") && n.Index == a.JobjIndex)?.Id,
                    readOnly = true, source = a
                })
            });
            var previewMaterials = modelSnapshot.Materials.Select(material => new
            {
                material.Id, material.Name, material.MobjOffset, material.UsesUv,
                preview = TexturePreview.Extract(stage.Layout, material, temporary),
                animationImages = AnimationImages(material.Id), animationWarning = AnimationWarning(material.Id)
            }).ToArray();
            // Preview-only materials never enter the reusable export material catalog.
            var catalogIds = previewMaterials.Select(m => m.Id).ToHashSet();
            var editableMaterialProperties = modelSnapshot.EditableMaterialProperties;
            var editableMaterialIds = editableMaterialProperties.Select(material => material.Id).ToHashSet();
            var editableModelsById = editableModels.ToDictionary(model => model.Id);
            var nodesById = identity.Nodes.ToDictionary(n => n.Id);
            var basePreviews = meshes.Where(m => !catalogIds.Contains(m.Node.Id)).Select(m =>
            {
                var dobj = nodesById[m.Node.OwnerId!];
                var joint = nodesById[dobj.OwnerId!];
                return (m.Node, Dobj: dobj, Joint: joint, Mobj: reader.Pointer(dobj.SourceOffset + 8));
            }).Where(m => m.Mobj.HasValue && reader.Pointer(m.Mobj.Value) == null).Select(m =>
            {
                var material = new ModelMaterial(m.Node.Id,
                    $"Base Material G{m.Node.GroupIndex:D3} J{m.Joint.Index:D3} D{m.Dobj.Index:D3}",
                    m.Mobj!.Value, reader.Pointer(m.Mobj.Value + 8) != null);
                string? materialReadOnlyReason = editableMaterialIds.Contains(material.Id) ? null
                    : editableModelsById.TryGetValue(material.Id, out var target) && target.PositionsOnly
                        ? "Material editing is unavailable for this object because it uses material animation."
                        : editableModelsById.ContainsKey(material.Id)
                            ? "Material editing is unavailable for this object because its source texture or render configuration is not supported for DAT export."
                            : "Material editing is unavailable for this object because its geometry and material layout cannot be safely exported.";
                return new { material.Id, material.Name, material.UsesUv, materialReadOnlyReason,
                    preview = TexturePreview.Extract(stage.Layout, material, temporary),
                    animationImages = AnimationImages(material.Id), animationWarning = AnimationWarning(material.Id) };
            }).ToArray();
            var baselineFiles = Directory.GetFiles(temporary, "*", SearchOption.AllDirectories)
                .Where(path => Path.GetFileName(path) != "source.dat")
                .Select(path => new { file = Path.GetRelativePath(temporary, path).Replace('\\', '/'), sha256 = SessionApplier.Hash(File.ReadAllBytes(path)) }).ToArray();
            Write("stage.json", new
            {
                protocolVersion = ProtocolVersion, assetType = "melee-stage", schemaVersion = SchemaVersion,
                source = new { file = "source.dat", filename = info.Filename, sha256 = info.Sha256, datVersion = info.DatVersion },
                publicRoots = info.Roots, externalReferences = info.References,
                lighting, atmosphere, camera, gameplay,
                modelGroupCount = groups.Length,
                modelGroups = groups.Select(g => new { id = g.Id, index = g.GroupIndex, file = $"models/group-{g.GroupIndex:D3}/group.json" }),
                coordinates = new { payloadSpace = "game", gameAxes = "X right, Y up, Z depth", blenderFromGame = "(X, -Z, Y)", unitScale = 1 },
                modelAdditionSchemaVersion = ModelAddition.SchemaVersion,
                modelAdditionTargets,
                capabilities = new { modelIdentities = true, collisionExtraction = true, extractedMeshCount = meshes.Count, allModelGeometry = deferredMeshes.Count == 0,
                    collisionEdit = warnings.Count == 0 && collision.Ranges[4].Count == 0 && collision.Attachments.Length == 0,
                    modelEdit = editableModels.Length > 0, jobjTransformEdit = editableJobjs.Length > 0,
                    jobjAnimationEdit = jointAnimationsByGroup.Values.SelectMany(animations => animations)
                        .SelectMany(animation => animation.Nodes).Any(node => node.Editable),
                    lightEdit = lighting.LightSets.Any(set => set.Lights.Length > 0),
                    gameplayPointEdit = gameplay.EditablePoints.Length > 0,
                    gameplayBoundsEdit = gameplay.EditableBounds.Length > 0,
                    gameplayItemSpawnTopologyEdit = gameplay.Sets.Any(set =>
                        set.ItemSpawnTopologyEditable),
                    materialAnimationPreview = materialAnimationsByGroup.Values.Any(animations => animations.Length > 0),
                    textureImageAnimationPreview = materialAnimationsByGroup.Values.SelectMany(animations => animations)
                        .SelectMany(animation => animation.Materials).SelectMany(material => material.Textures)
                        .Any(texture => texture.Images.Length > 0),
                    textureRegisterAnimationPreview = materialAnimationsByGroup.Values.SelectMany(animations => animations)
                        .SelectMany(animation => animation.Materials).SelectMany(material => material.Textures)
                        .SelectMany(texture => texture.Tracks)
                        .Any(track => track.Channel.StartsWith("konst.") || track.Channel.StartsWith("tev0.")
                            || track.Channel.StartsWith("tev1.")),
                    modelAddition = modelAdditionTargets.Length > 0,
                    dynamicCollisionEdit = false, apply = true },
                deferredCapabilities = new[] { "material-animation-export", "material-pixel-animation-preview", "shape-animations",
                    "jobj-animation-duration-edit", "dynamic-collision-editing", "stage-parameters" },
                editableMaterialProperties,
                editableJobjs = editableJobjs.Select(e => new { e.Id, e.GroupIndex, e.JobjIndex }),
                editableGameplayPoints = gameplay.EditablePoints.Select(point => new
                {
                    point.Id, point.SetIndex, point.EntryIndex, point.Kind,
                    point.Player
                }),
                editableJointAnimations = jointAnimationsByGroup.SelectMany(pair => pair.Value.SelectMany(animation =>
                    animation.Nodes.Where(node => node.Editable).Select(node => new
                    {
                        groupIndex = pair.Key, animation.Slot, jobjId = node.JobjId
                    }))),
                modelMaterials = previewMaterials,
                modelPreviews = basePreviews,
                editableMeshes = editableModels.Select(e => new { e.Id, e.GroupIndex, e.JobjIndex,
                    e.DobjIndex, e.PobjIndex, file = $"models/group-{e.GroupIndex:D3}/mesh-{e.Id}.json",
                    operationCapabilities = modelSnapshot.Models[e.Id].Capabilities,
                    representation = "opaque-grey-flat-shaded", maxTriangles = ModelEditing.MaxTriangles }),
                selectedMesh = new { id = selected.Id, file = meshPath }, deferredMeshes, warnings, baselineFiles
            });
            Directory.Move(temporary, directory);
        }
        finally { if (Directory.Exists(temporary)) Directory.Delete(temporary, recursive: true); }
        return new(directory, groups.Length, collision.Lines.Length, selected.Id, meshes.Sum(m => m.Mesh.TriangleIndices.Length / 3), meshes.Count);

        float[]? InverseBind(int joint)
        {
            int? matrix = reader.Pointer(joint + 0x38);
            if (!matrix.HasValue) return null;
            reader.Check(matrix.Value, 48);
            return Enumerable.Range(0, 12).Select(i => reader.Float(matrix.Value + i * 4)).ToArray();
        }
        string? Link(int index) => index < 0 ? null : lineIds[index];
        Vector3Data Vec(int offset) => new(reader.Float(offset), reader.Float(offset + 4), reader.Float(offset + 8));
        MaterialAnimationImage[] AnimationImages(string materialId) => materialAnimationsByGroup.Values
            .SelectMany(sets => sets)
            .SelectMany(set => set.Materials.Where(material => material.MaterialId == materialId)
                .SelectMany(material => material.Textures.SelectMany(texture => texture.Images.Select(image =>
                    new MaterialAnimationImage(set.Slot, texture.TextureIndex, image.ImageIndex,
                        image.PaletteIndex, image.File, image.Width, image.Height)))))
            .Distinct().ToArray();
        string? AnimationWarning(string materialId)
        {
            string[] messages = materialAnimationsByGroup.Values.SelectMany(sets => sets)
                .SelectMany(set => set.Materials).Where(material => material.MaterialId == materialId)
                .SelectMany(material => material.Textures).Select(texture => texture.ImageWarning)
                .Where(message => !string.IsNullOrEmpty(message)).Distinct().Cast<string>().ToArray();
            return messages.Length == 0 ? null : string.Join(" ", messages);
        }
        void Write(string relative, object data)
        {
            string path = Path.Combine(temporary, relative); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, JsonSerializer.Serialize(data, Json));
        }
    }
}
