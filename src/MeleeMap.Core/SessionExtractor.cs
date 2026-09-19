using System.Text.Json;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ExtractionResult(string SessionDirectory, int ModelGroups, int CollisionLines, string MeshId, int Triangles);

public static class SessionExtractor
{
    public const int ProtocolVersion = 1;
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, WriteIndented = true };

    /// <summary>Extract a complete identity/collision session and the first supported rigid polygon.
    /// Payloads stay in game coordinates; a Blender adapter applies the declared reversible transform.</summary>
    public static ExtractionResult Extract(StageArchive stage, string directory)
    {
        directory = Path.GetFullPath(directory);
        Require(!Directory.Exists(directory) && !File.Exists(directory), "SESSION_EXISTS", "Session destination already exists; choose a new directory.");
        var warnings = stage.Validate(); var info = stage.Inspect();
        var catalog = new ModelIdentityCatalog();
        var identity = ModelIdentity.Capture(stage.Layout, catalog);
        var collision = CollisionData.Read(stage.Layout);
        var reader = new ArchiveDataReader(stage.Layout);
        ModelIdentityNode? selected = null; MeshData? mesh = null;
        var deferredMeshes = new List<object>();
        foreach (var node in identity.Nodes.Where(n => n.Kind == "pobj"))
        {
            if (selected != null) { deferredMeshes.Add(new { id = node.Id, reason = "single-target-extraction" }); continue; }
            try { mesh = GxMeshDecoder.Decode(stage.Layout, node.SourceOffset); selected = node; }
            catch (StageException e) when (e.Code is "GX_UNSUPPORTED_BINDING" or "GX_ATTRIBUTE" or "GX_PRIMITIVE")
            { deferredMeshes.Add(new { id = node.Id, reason = e.Code, message = e.Message }); }
        }
        Require(selected != null && mesh != null, "EXTRACT_NO_MODEL_TARGET", "No supported rigid model target found; session extraction has not been published.");
        var groups = identity.Nodes.Where(n => n.Kind is "group" or "sentinel-group").ToArray();
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
            foreach (var group in groups)
            {
                string groupPath = $"models/group-{group.GroupIndex:D3}";
                var nodes = identity.Nodes.Where(n => n.GroupIndex == group.GroupIndex).ToArray();
                Write($"{groupPath}/group.json", new
                {
                    protocolVersion = ProtocolVersion, id = group.Id, index = group.GroupIndex, protectedIdentity = true, nodes,
                    joints = nodes.Where(n => n.Kind.EndsWith("jobj")).Select(n => new
                    {
                        id = n.Id, flags = unchecked((uint)reader.Int(n.SourceOffset + 4)),
                        transformSpace = "game", rotationEncoding = "source-jobj-xyz",
                        rotation = Vec(n.SourceOffset + 0x14), scale = Vec(n.SourceOffset + 0x20), translation = Vec(n.SourceOffset + 0x2C),
                        childSourceOffset = reader.Pointer(n.SourceOffset + 8), nextSourceOffset = reader.Pointer(n.SourceOffset + 12)
                    }),
                    meshes = group.GroupIndex == selected!.GroupIndex ? new[] { $"mesh-{selected.Id}.json" } : Array.Empty<string>()
                });
            }
            string meshPath = $"models/group-{selected!.GroupIndex:D3}/mesh-{selected.Id}.json";
            Write(meshPath, new { protocolVersion = ProtocolVersion, id = selected.Id, ownerId = selected.OwnerId, groupIndex = selected.GroupIndex,
                pobjIndex = selected.Index, sourceOffset = selected.SourceOffset, coordinateSpace = "game", representation = "untextured-grey",
                positions = mesh!.Positions, normals = mesh.Normals, triangleIndices = mesh.TriangleIndices });
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
            Write("stage.json", new
            {
                protocolVersion = ProtocolVersion, assetType = "melee-stage", schemaVersion = 1,
                source = new { file = "source.dat", filename = info.Filename, sha256 = info.Sha256, datVersion = info.DatVersion },
                publicRoots = info.Roots, externalReferences = info.References,
                modelGroupCount = groups.Length,
                modelGroups = groups.Select(g => new { id = g.Id, index = g.GroupIndex, file = $"models/group-{g.GroupIndex:D3}/group.json" }),
                coordinates = new { payloadSpace = "game", gameAxes = "X right, Y up, Z depth", blenderFromGame = "(X, -Z, Y)", unitScale = 1 },
                capabilities = new { modelIdentities = true, collisionExtraction = true, extractedMeshCount = 1, allModelGeometry = false,
                    collisionEdit = false, modelEdit = false, dynamicCollisionEdit = false, apply = false },
                deferredCapabilities = new[] { "remaining-model-geometry", "textures", "materials", "animations", "dynamic-collision-editing", "stage-parameters", "Blender-integration" },
                selectedMesh = new { id = selected.Id, file = meshPath }, deferredMeshes, warnings
            });
            Directory.Move(temporary, directory);
        }
        finally { if (Directory.Exists(temporary)) Directory.Delete(temporary, recursive: true); }
        return new(directory, groups.Length, collision.Lines.Length, selected!.Id, mesh!.TriangleIndices.Length / 3);

        string? Link(int index) => index < 0 ? null : lineIds[index];
        Vector3Data Vec(int offset) => new(reader.Float(offset), reader.Float(offset + 4), reader.Float(offset + 8));
        void Write(string relative, object data)
        {
            string path = Path.Combine(temporary, relative); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, JsonSerializer.Serialize(data, Json));
        }
    }
}
