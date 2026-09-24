using System.Text.Json.Serialization;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ModelEdit([property: JsonRequired] string Id,
    [property: JsonRequired] Vector3Data[] Positions, [property: JsonRequired] int[] TriangleIndices,
    string? SourceMaterialId = null, Vector2Data[]? TexCoords = null, bool UseGreyMaterial = false,
    ColorData[]? Colors0 = null, ColorData[]? Colors1 = null);
public sealed record ModelEdits([property: JsonRequired] int ProtocolVersion,
    [property: JsonRequired] string CoordinateSpace, [property: JsonRequired] ModelEdit[] Meshes,
    string[]? DeletedIds = null);
public sealed record EditableModel(string Id, int GroupIndex, int JobjIndex, int DobjIndex,
    int PobjIndex, int PobjOffset, int DobjOffset, bool PositionsOnly = false,
    int PobjLinkField = -1, bool SharesDobj = false);

/// <summary>Structurally eligible rigid POBJs for position, topology, or deletion edits.</summary>
public static class ModelEditing
{
    public const int MaxTriangles = 14000;

    public static EditableModel? Select(ArchiveLayout archive, ModelIdentitySnapshot identity)
    {
        // Retain the original POC target as the legacy session alias.
        var r = new ArchiveDataReader(archive);
        return SelectAll(archive, identity).FirstOrDefault(t =>
        {
            int material = r.Pointer(t.DobjOffset + 8)!.Value;
            var joint = identity.Nodes.First(n => n.GroupIndex == t.GroupIndex && n.Kind == "jobj" && n.Index == t.JobjIndex);
            return !t.PositionsOnly && r.Int(material + 4) == 1 && r.Pointer(material + 8) == null
                && r.Pointer(material + 20) == null && (r.Int(joint.SourceOffset + 4) & 16) == 0;
        });
    }

    public static EditableModel[] SelectAll(ArchiveLayout archive, ModelIdentitySnapshot identity)
        => SelectAll(archive, identity, out _);

    public static EditableModel[] SelectAll(ArchiveLayout archive, ModelIdentitySnapshot identity,
        out Dictionary<string, string> readOnlyReasons)
    {
        readOnlyReasons = new();
        var result = new List<EditableModel>();
        var r = new ArchiveDataReader(archive); var nodes = identity.Nodes;
        var byId = nodes.GroupBy(n => n.Id).ToDictionary(g => g.Key, g => g.First());
        foreach (var p in nodes.Where(n => n.Kind == "pobj"))
        {
            var d = byId[p.OwnerId!]; var j = byId[d.OwnerId!];
            var group = nodes.First(n => n.GroupIndex == p.GroupIndex && n.Kind is "group" or "sentinel-group");
            var siblings = nodes.Where(n => n.OwnerId == d.Id && n.Kind == "pobj")
                .OrderBy(n => n.Index).ToArray();
            int siblingIndex = Array.FindIndex(siblings, node => node.Id == p.Id);
            if (group.Kind != "group" || j.Kind != "jobj" || siblingIndex < 0
                || p.Index != siblingIndex
                || nodes.Count(n => n.SourceOffset == p.SourceOffset && n.Kind == "pobj") != 1
                || nodes.Count(n => n.SourceOffset == d.SourceOffset && n.Kind == "dobj") != 1)
            { readOnlyReasons[p.Id] = "Shared or inconsistent model descriptors."; continue; }
            try
            {
                // Custom classes, bindings and shape animation need dedicated writers.
                // Material animation is safe only with position-only writes.
                int? material = r.Pointer(d.SourceOffset + 8);
                if (r.Pointer(p.SourceOffset + 20) != null || (r.UShort(p.SourceOffset + 12) & ~0xC001) != 0)
                { readOnlyReasons[p.Id] = "Skinned, shared-joint, or shape-bound geometry."; continue; }
                if (r.Pointer(group.SourceOffset + 12) != null)
                { readOnlyReasons[p.Id] = "This group has shape animation."; continue; }
                if (material == null || r.Pointer(p.SourceOffset) != null
                    || r.Pointer(d.SourceOffset) != null
                    || r.Pointer(material.Value) != null
                    || (r.Int(j.SourceOffset + 4) & (0x20000 | 0x1000 | 0xE00)) != 0)
                { readOnlyReasons[p.Id] = "Custom classes, instancing, or billboard transforms."; continue; }
                int linkField = siblingIndex == 0 ? d.SourceOffset + 12
                    : siblings[siblingIndex - 1].SourceOffset + 4;
                if (archive.Pointers.Count(x => x.Value == p.SourceOffset) != 1
                    || !archive.Pointers.TryGetValue(linkField, out int target) || target != p.SourceOffset
                    || archive.Pointers.Count(x => x.Value == d.SourceOffset) != 1)
                { readOnlyReasons[p.Id] = "Shared model descriptors."; continue; }
                bool HasInteriorReference(int start, int size) => archive.Pointers.Values.Any(x => x > start && x < start + size)
                    || archive.Roots.Concat(archive.References).Any(x => x.Offset >= start && x.Offset < start + size);
                if (HasInteriorReference(p.SourceOffset, 24) || HasInteriorReference(d.SourceOffset, 16))
                { readOnlyReasons[p.Id] = "External or interior model descriptor references."; continue; }
                bool positionsOnly = HasMaterialAnimation(group, j, d.Index);
                var mesh = GxMeshDecoder.Decode(archive, p.SourceOffset);
                if (mesh.Envelopes != null || mesh.BoundJobjSourceOffset != null)
                { readOnlyReasons[p.Id] = "Skinned or shared-joint geometry."; continue; }
                result.Add(new(p.Id, p.GroupIndex, j.Index, d.Index, p.Index,
                    p.SourceOffset, d.SourceOffset, positionsOnly, linkField,
                    siblings.Length > 1));
            }
            catch (StageException e) { readOnlyReasons[p.Id] = e.Message; }
        }
        return result.ToArray();

        bool HasMaterialAnimation(ModelIdentityNode group, ModelIdentityNode joint, int dobjIndex)
        {
            var path = new List<ModelIdentityNode>(); var cursor = joint;
            while (cursor.OwnerId != group.Id) { path.Add(cursor); cursor = byId[cursor.OwnerId!]; }
            if (cursor.Index != 0) return true;
            path.Reverse();
            int? array = r.Pointer(group.SourceOffset + 8);
            if (array == null) return false;
            for (int field = array.Value; ; field += 4)
            {
                int? animation = r.Pointer(field);
                if (animation == null) return false;
                foreach (var node in path)
                {
                    int sibling = nodes.Where(n => n.OwnerId == node.OwnerId && n.Kind.EndsWith("jobj"))
                        .OrderBy(n => n.Index).TakeWhile(n => n.Id != node.Id).Count();
                    animation = animation.HasValue ? r.Pointer(animation.Value) : null;
                    for (int i = 0; i < sibling && animation.HasValue; i++) animation = r.Pointer(animation.Value + 4);
                }
                int? mat = animation.HasValue ? r.Pointer(animation.Value + 8) : null;
                for (int i = 0; i < dobjIndex && mat.HasValue; i++) mat = r.Pointer(mat.Value);
                if (mat.HasValue && (r.Pointer(mat.Value + 4) != null || r.Pointer(mat.Value + 8) != null || r.Int(mat.Value + 12) != 0)) return true;
            }
        }
    }

    public static bool HasSameTopology(ModelEdit edit, MeshData original) =>
        edit.Positions?.Length == original.Positions.Length && edit.TriangleIndices != null
        && edit.TriangleIndices.SequenceEqual(original.TriangleIndices);

    public static bool PreservesAppearance(ModelEdit edit, MeshData original, EditableModel target) =>
        !edit.UseGreyMaterial && HasSameTopology(edit, original) && (edit.SourceMaterialId == null ||
            (edit.SourceMaterialId == target.Id && (original.TexCoords0 == null ? edit.TexCoords == null
                : edit.TexCoords != null && edit.TexCoords.Length == original.TriangleIndices.Length
                && edit.TexCoords.SequenceEqual(original.TriangleIndices.Select(i => original.TexCoords0[i])))));

    public static MeshData Compile(ModelEdits edits, EditableModel target, MeshData? original = null)
    {
        Require(edits.ProtocolVersion == SessionExtractor.ProtocolVersion && edits.CoordinateSpace == "game-joint-local",
            "MODEL_EDIT_VERSION", "Model edits must use the current protocol and game-joint-local coordinates.");
        Require(edits.Meshes is { Length: 1 } && edits.Meshes[0] != null && edits.Meshes[0].Id == target.Id,
            "MODEL_EDIT_TARGET", "Each compiled replacement must match an eligible rigid mesh.");
        var edit = edits.Meshes[0];
        Require(!target.PositionsOnly || (original != null && HasSameTopology(edit, original)
            && edit.SourceMaterialId == null && edit.TexCoords == null && !edit.UseGreyMaterial
            && edit.Colors0 == null && edit.Colors1 == null),
            "MODEL_POSITION_ONLY", "Models with animated materials support vertex movement only. Keep topology, UVs and material assignments unchanged.");
        Require(edit.Positions is { Length: > 0 and <= 65535 } && edit.TriangleIndices is { Length: > 0 }
            && edit.TriangleIndices.Length % 3 == 0 && edit.TriangleIndices.Length <= MaxTriangles * 3,
            "MODEL_EDIT_COUNT", $"Model edits require vertices and triangles (at most 65535 vertices and {MaxTriangles} triangles).");
        Require(edit.Positions.All(v => float.IsFinite(v.X) && float.IsFinite(v.Y) && float.IsFinite(v.Z)), "MODEL_NONFINITE", "Model positions must be finite.");
        Require(edit.TriangleIndices.All(i => i >= 0 && i < edit.Positions.Length), "MODEL_INDEX", "Model triangle index is out of bounds.");
        Require(!edit.UseGreyMaterial || edit.SourceMaterialId == null, "MODEL_MATERIAL", "Choose either grey or a stage material.");
        Require(edit.TexCoords == null || (edit.SourceMaterialId != null && edit.TexCoords.Length == edit.TriangleIndices.Length
            && edit.TexCoords.All(uv => float.IsFinite(uv.X) && float.IsFinite(uv.Y))),
            "MODEL_UV", "UVs require a source material and one finite coordinate per triangle corner.");
        bool hasColors = edit.Colors0 != null || edit.Colors1 != null;
        Require(!hasColors || (original != null && PreservesAppearance(edit, original, target)),
            "MODEL_COLOR_TOPOLOGY", "Vertex colors require the original topology and material assignment.");
        ColorData[]? Colors(ColorData[]? values, ColorData[]? source)
        {
            if (values == null) return source == null ? null : original!.TriangleIndices.Select(i => source[i]).ToArray();
            Require(source != null && values.Length == edit.TriangleIndices.Length
                && values.All(c => new[] { c.R, c.G, c.B, c.A }.All(v => float.IsFinite(v) && v >= 0 && v <= 1)),
                "MODEL_COLOR", "Colors require an existing channel and one finite RGBA value in [0,1] per triangle corner.");
            float Q(float v) => MathF.Round(v * 255, MidpointRounding.AwayFromZero) / 255;
            return values.Select(c => new ColorData(Q(c.R), Q(c.G), Q(c.B), Q(c.A))).ToArray();
        }
        if (original != null && PreservesAppearance(edit, original, target))
        {
            if (!hasColors) return original with { Positions = edit.Positions };
            // Expand corners so painting a seam never changes the neighboring face.
            return original with {
                Positions = original.TriangleIndices.Select(i => edit.Positions[i]).ToArray(),
                Normals = original.Normals == null ? null : original.TriangleIndices.Select(i => original.Normals[i]).ToArray(),
                TexCoords0 = original.TexCoords0 == null ? null : original.TriangleIndices.Select(i => original.TexCoords0[i]).ToArray(),
                TriangleIndices = Enumerable.Range(0, original.TriangleIndices.Length).ToArray(),
                Colors0 = Colors(edit.Colors0, original.Colors0), Colors1 = Colors(edit.Colors1, original.Colors1)
            };
        }
        var positions = new List<Vector3Data>(); var normals = new List<Vector3Data>();
        var texCoords = edit.TexCoords == null ? null : new List<Vector2Data>();
        bool keepNormals = edit.SourceMaterialId != null && original?.Normals != null && HasSameTopology(edit, original);
        for (int i = 0; i < edit.TriangleIndices.Length; i += 3)
        {
            var a = edit.Positions[edit.TriangleIndices[i]]; var b = edit.Positions[edit.TriangleIndices[i + 1]]; var c = edit.Positions[edit.TriangleIndices[i + 2]];
            double ux = (double)b.X - a.X, uy = (double)b.Y - a.Y, uz = (double)b.Z - a.Z;
            double vx = (double)c.X - a.X, vy = (double)c.Y - a.Y, vz = (double)c.Z - a.Z;
            double nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
            double length = Math.Sqrt(nx * nx + ny * ny + nz * nz);
            if (length == 0) continue; // GX strips commonly contain deliberate degenerate triangles.
            var normal = new Vector3Data((float)(nx / length), (float)(ny / length), (float)(nz / length));
            positions.AddRange([a, b, c]);
            if (keepNormals)
                normals.AddRange(edit.TriangleIndices.Skip(i).Take(3).Select(index => original!.Normals![index]));
            else normals.AddRange([normal, normal, normal]);
            if (texCoords != null) texCoords.AddRange(edit.TexCoords!.Skip(i).Take(3));
        }
        Require(positions.Count > 0, "MODEL_EMPTY", "The replacement model has no nondegenerate triangles.");
        return new(positions.ToArray(), normals.ToArray(), Enumerable.Range(0, positions.Count).ToArray(), TexCoords0: texCoords?.ToArray());
    }
}
