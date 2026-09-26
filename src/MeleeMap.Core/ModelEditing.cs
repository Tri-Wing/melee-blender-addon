using System.Text.Json.Serialization;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ModelEdit([property: JsonRequired] string Id,
    [property: JsonRequired] Vector3Data[] Positions, [property: JsonRequired] int[] TriangleIndices,
    string? SourceMaterialId = null, Vector2Data[]? TexCoords = null, bool UseGreyMaterial = false,
    ColorData[]? Colors0 = null, ColorData[]? Colors1 = null,
    Vector3Data[]? Normals = null, bool ReverseWinding = false);
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

    public static EditableModel[] SelectAll(ArchiveLayout archive, ModelIdentitySnapshot identity)
        => SelectAll(archive, identity, out _);

    public static EditableModel[] SelectAll(ArchiveLayout archive, ModelIdentitySnapshot identity,
        out Dictionary<string, string> readOnlyReasons)
    {
        var snapshot = ModelSourceSnapshot.Capture(archive, identity);
        readOnlyReasons = snapshot.Models.Values.Where(model => model.ReadOnlyReason != null)
            .ToDictionary(model => model.Pobj.Id, model => model.ReadOnlyReason!,
                StringComparer.Ordinal);
        return snapshot.EditableModels.ToArray();
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
        Require(edit.Normals == null || (edit.Normals.Length == edit.Positions.Length
            && original?.Normals != null && HasSameTopology(edit, original)
            && edit.Normals.All(normal => float.IsFinite(normal.X)
                && float.IsFinite(normal.Y) && float.IsFinite(normal.Z)
                && MathF.Abs(normal.X * normal.X + normal.Y * normal.Y
                    + normal.Z * normal.Z - 1) <= 0.0001f)),
            "MODEL_NORMAL", "Model normals require the original topology and one finite normalized vector per source vertex.");
        Require(!edit.ReverseWinding || (original != null && HasSameTopology(edit, original)),
            "MODEL_WINDING", "Reversed winding requires the original vertex count and triangle indices.");
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
            var preservedNormals = edit.Normals ?? original.Normals;
            if (!hasColors) return original with {
                Positions = edit.Positions, Normals = preservedNormals
            };
            // Expand corners so painting a seam never changes the neighboring face.
            return original with {
                Positions = original.TriangleIndices.Select(i => edit.Positions[i]).ToArray(),
                Normals = preservedNormals == null ? null
                    : original.TriangleIndices.Select(i => preservedNormals[i]).ToArray(),
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
            int cornerA = i, cornerB = edit.ReverseWinding ? i + 2 : i + 1,
                cornerC = edit.ReverseWinding ? i + 1 : i + 2;
            int indexA = edit.TriangleIndices[cornerA],
                indexB = edit.TriangleIndices[cornerB],
                indexC = edit.TriangleIndices[cornerC];
            var a = edit.Positions[indexA]; var b = edit.Positions[indexB];
            var c = edit.Positions[indexC];
            double ux = (double)b.X - a.X, uy = (double)b.Y - a.Y, uz = (double)b.Z - a.Z;
            double vx = (double)c.X - a.X, vy = (double)c.Y - a.Y, vz = (double)c.Z - a.Z;
            double nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
            double length = Math.Sqrt(nx * nx + ny * ny + nz * nz);
            if (length == 0) continue; // GX strips commonly contain deliberate degenerate triangles.
            var normal = new Vector3Data((float)(nx / length), (float)(ny / length), (float)(nz / length));
            positions.AddRange([a, b, c]);
            if (keepNormals)
                normals.AddRange(new[] { indexA, indexB, indexC }
                    .Select(index => edit.Normals?[index] ?? original!.Normals![index]));
            else normals.AddRange([normal, normal, normal]);
            if (texCoords != null) texCoords.AddRange([
                edit.TexCoords![cornerA], edit.TexCoords[cornerB],
                edit.TexCoords[cornerC]]);
        }
        Require(positions.Count > 0, "MODEL_EMPTY", "The replacement model has no nondegenerate triangles.");
        return new(positions.ToArray(), normals.ToArray(), Enumerable.Range(0, positions.Count).ToArray(), TexCoords0: texCoords?.ToArray());
    }
}
