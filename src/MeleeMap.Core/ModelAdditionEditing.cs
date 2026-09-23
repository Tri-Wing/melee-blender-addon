using System.Security.Cryptography;
using System.Text.Json.Serialization;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ModelAdditionEdits(
    [property: JsonRequired] int ProtocolVersion,
    [property: JsonRequired] int ModelAdditionSchemaVersion,
    [property: JsonRequired] string CoordinateSpace,
    [property: JsonRequired] ModelAdditionEdit[] Additions,
    [property: JsonRequired] ModelAdditionMaterial[] Materials,
    [property: JsonRequired] ModelAdditionImage[] Images);

public sealed record ModelAdditionEdit(
    [property: JsonRequired] string Id,
    [property: JsonRequired] string Name,
    [property: JsonRequired] string Placement,
    [property: JsonRequired] string TargetJobjId,
    [property: JsonRequired] ModelAdditionPart[] Parts);

public sealed record ModelAdditionPart(
    [property: JsonRequired] string Id,
    [property: JsonRequired] string MaterialId,
    [property: JsonRequired] Vector3Data[] Positions,
    [property: JsonRequired] int[] TriangleIndices,
    [property: JsonRequired] Vector3Data[] CornerNormals,
    [property: JsonRequired] Vector2Data[]? TexCoords0);

public sealed record ModelAdditionMaterial(
    [property: JsonRequired] string Id,
    [property: JsonRequired] string Name,
    [property: JsonRequired] ColorData BaseColor,
    [property: JsonRequired] string? ImageId,
    [property: JsonRequired] string WrapS,
    [property: JsonRequired] string WrapT,
    [property: JsonRequired] string MinFilter,
    [property: JsonRequired] string MagFilter,
    [property: JsonRequired] string Preset);

public sealed record ModelAdditionImage(
    [property: JsonRequired] string Id,
    [property: JsonRequired] int Width,
    [property: JsonRequired] int Height,
    [property: JsonRequired] string PixelEncoding,
    [property: JsonRequired] string PayloadPath,
    [property: JsonRequired] string Sha256);

public sealed record ValidatedModelAdditionImage(ModelAdditionImage Definition, byte[] Pixels);
public sealed record ValidatedModelAdditionBatch(ModelAdditionEdits Edits,
    IReadOnlyDictionary<string, ModelAdditionTarget> Targets,
    IReadOnlyDictionary<string, ModelAdditionMaterial> Materials,
    IReadOnlyDictionary<string, ValidatedModelAdditionImage> Images,
    int TriangleCount, long PixelBytes);

/// <summary>
/// Strict, bounded validation for the model-addition interchange. This performs
/// no archive writes; callers receive a self-contained batch with verified asset
/// bytes and source-recomputed attachment targets.
/// </summary>
public static class ModelAdditionEditing
{
    public const int MaxAdditions = 128;
    public const int MaxParts = 512;
    public const int MaxMaterials = 512;
    public const int MaxImages = 256;
    public const int MaxImageDimension = 1024;
    public const long MaxPixelBytes = 32L * 1024 * 1024;
    public const int MaxTriangles = 200_000;
    public const int MaxPositions = 1_000_000;
    public const string PixelEncoding = "rgba8-srgb-straight-top-left";
    public const string UnlitMaterialPreset = "opaque-texture-focused-v1";
    public const string DiffuseMaterialPreset = "opaque-diffuse-texture-v2";
    public const string MaterialPreset = UnlitMaterialPreset;

    public static ValidatedModelAdditionBatch Validate(string sessionDirectory, StageArchive source,
        ModelIdentitySnapshot identity, ModelAdditionEdits edits, IEnumerable<string> declaredTargetIds)
    {
        Require(edits != null && edits.ProtocolVersion == SessionExtractor.ProtocolVersion
            && edits.ModelAdditionSchemaVersion == ModelAddition.SchemaVersion
            && edits.CoordinateSpace == "game-joint-local", "MODEL_ADDITION_VERSION",
            "Model additions require the current protocol, schema, and game-joint-local coordinates.");
        Require(edits.Additions is { Length: > 0 and <= MaxAdditions }
            && edits.Materials is { Length: > 0 and <= MaxMaterials }
            && edits.Images is { Length: <= MaxImages }, "MODEL_ADDITION_COUNT",
            "Model additions, materials, or images exceed the batch limits.");

        string directory = Path.GetFullPath(sessionDirectory);
        var eligible = ModelAddition.Select(source, identity).ToDictionary(target => target.Id);
        var declared = declaredTargetIds.ToHashSet(StringComparer.Ordinal);
        var ids = new HashSet<string>(StringComparer.Ordinal);
        var materials = new Dictionary<string, ModelAdditionMaterial>(StringComparer.Ordinal);
        var images = new Dictionary<string, ValidatedModelAdditionImage>(StringComparer.Ordinal);
        var paths = new HashSet<string>(StringComparer.Ordinal);

        foreach (var material in edits.Materials)
        {
            Require(material != null && AddId(material.Id) && Name(material.Name)
                && FiniteColor(material.BaseColor), "MODEL_ADDITION_MATERIAL",
                "Materials require unique UUIDs, a display name, and finite RGBA values in [0,1].");
            Require(material.Preset is UnlitMaterialPreset or DiffuseMaterialPreset
                && material.WrapS is "repeat" or "clamp" && material.WrapT is "repeat" or "clamp"
                && material.MinFilter is "nearest" or "linear" && material.MagFilter is "nearest" or "linear",
                "MODEL_ADDITION_MATERIAL", "Unsupported model-addition material preset or sampler setting.");
            materials.Add(material.Id, material);
        }

        long pixelBytes = 0;
        foreach (var image in edits.Images)
        {
            Require(image != null && AddId(image.Id), "MODEL_ADDITION_IMAGE", "Images require unique UUIDs.");
            Require(image.Width is > 0 and <= MaxImageDimension && image.Height is > 0 and <= MaxImageDimension
                && image.PixelEncoding == PixelEncoding, "MODEL_ADDITION_IMAGE",
                $"Images must be 1-{MaxImageDimension} pixels per axis using {PixelEncoding}.");
            long expectedBytes = checked((long)image.Width * image.Height * 4);
            pixelBytes = checked(pixelBytes + expectedBytes);
            Require(pixelBytes <= MaxPixelBytes, "MODEL_ADDITION_IMAGE_LIMIT",
                $"Decoded addition images exceed the {MaxPixelBytes} byte batch limit.");
            Require(IsLowerHex(image.Sha256, 64), "MODEL_ADDITION_IMAGE_HASH",
                "Image SHA-256 must contain 64 lowercase hexadecimal characters.");
            string path = AssetPath(directory, image.PayloadPath);
            Require(paths.Add(path) && File.Exists(path), "MODEL_ADDITION_IMAGE_PATH",
                "Image payload paths must be unique existing files under edits/addition-assets.");
            RejectLink(new FileInfo(path));
            byte[] pixels = File.ReadAllBytes(path);
            Require(pixels.LongLength == expectedBytes, "MODEL_ADDITION_IMAGE_LENGTH",
                "Image payload byte count does not match its RGBA dimensions.");
            string hash = Convert.ToHexString(SHA256.HashData(pixels)).ToLowerInvariant();
            Require(hash == image.Sha256, "MODEL_ADDITION_IMAGE_HASH", "Image payload SHA-256 mismatch.");
            images.Add(image.Id, new(image, pixels));
        }
        ValidateAssetInventory(directory, paths);

        foreach (var material in materials.Values)
            Require(material.ImageId == null || images.ContainsKey(material.ImageId),
                "MODEL_ADDITION_MATERIAL", "A material references an undeclared image.");

        int partCount = 0, triangleCount = 0, positionCount = 0;
        var usedMaterials = new HashSet<string>(StringComparer.Ordinal);
        foreach (var addition in edits.Additions)
        {
            Require(addition != null && AddId(addition.Id) && Name(addition.Name)
                && addition.Parts is { Length: > 0 }, "MODEL_ADDITION_FORMAT",
                "Additions require unique UUIDs, a display name, and at least one geometry part.");
            eligible.TryGetValue(addition.TargetJobjId ?? "", out var target);
            Require(addition.TargetJobjId != null && declared.Contains(addition.TargetJobjId)
                && target != null && addition.Placement == target.Placement, "MODEL_ADDITION_TARGET",
                "Addition attachment is unsupported or absent from this session.");
            partCount = checked(partCount + addition.Parts.Length);
            Require(partCount <= MaxParts, "MODEL_ADDITION_COUNT", "Model additions exceed the geometry-part limit.");
            foreach (var part in addition.Parts)
            {
                Require(part != null && AddId(part.Id) && part.MaterialId != null
                    && materials.ContainsKey(part.MaterialId),
                    "MODEL_ADDITION_PART", "Geometry parts require unique UUIDs and a declared material.");
                var material = materials[part!.MaterialId];
                string requiredPreset = target!.Placement == ModelAddition.NewJobjChainPlacement
                    ? DiffuseMaterialPreset : UnlitMaterialPreset;
                Require(material.Preset == requiredPreset, "MODEL_ADDITION_MATERIAL",
                    "The material preset does not match the selected placement mode.");
                usedMaterials.Add(part.MaterialId);
                Require(part.Positions is { Length: > 0 and <= ushort.MaxValue }
                    && part.TriangleIndices is { Length: > 0 }
                    && part.TriangleIndices.Length <= MaxTriangles * 3
                    && part.TriangleIndices.Length % 3 == 0,
                    "MODEL_ADDITION_GEOMETRY", "Geometry parts require positions and complete triangles.");
                Require(part.TriangleIndices.All(index => index >= 0 && index < part.Positions.Length),
                    "MODEL_ADDITION_INDEX", "A model-addition triangle index is out of bounds.");
                Require(part.Positions.All(Finite), "MODEL_ADDITION_NONFINITE", "Addition positions must be finite.");
                Require(part.CornerNormals is not null && part.CornerNormals.Length == part.TriangleIndices.Length
                    && part.CornerNormals.All(Normal), "MODEL_ADDITION_NORMAL",
                    "Addition normals must be one finite normalized vector per triangle corner.");
                bool textured = material.ImageId != null;
                Require(textured
                    ? part.TexCoords0 is not null && part.TexCoords0.Length == part.TriangleIndices.Length
                        && part.TexCoords0.All(Finite)
                    : part.TexCoords0 == null, "MODEL_ADDITION_UV",
                    "Textured parts require one finite UV0 per triangle corner; constant-color parts must omit UV0.");
                Require(HasNondegenerateTriangle(part), "MODEL_ADDITION_EMPTY",
                    "A geometry part must contain at least one nondegenerate triangle.");
                triangleCount = checked(triangleCount + part.TriangleIndices.Length / 3);
                positionCount = checked(positionCount + part.Positions.Length);
                Require(triangleCount <= MaxTriangles && positionCount <= MaxPositions,
                    "MODEL_ADDITION_GEOMETRY_LIMIT", "Model-addition geometry exceeds the batch limits.");
            }
        }

        Require(materials.Keys.All(usedMaterials.Contains), "MODEL_ADDITION_UNUSED",
            "Every declared material must be referenced by geometry.");
        var usedImages = materials.Values.Where(material => usedMaterials.Contains(material.Id))
            .Select(material => material.ImageId).Where(id => id != null).Cast<string>().ToHashSet(StringComparer.Ordinal);
        Require(images.Keys.All(usedImages.Contains), "MODEL_ADDITION_UNUSED",
            "Every declared image must be referenced by a material.");

        var targets = edits.Additions.Select(addition => eligible[addition.TargetJobjId])
            .DistinctBy(target => target.Id).ToDictionary(target => target.Id);
        return new(edits, targets, materials, images, triangleCount, pixelBytes);

        bool AddId(string id) => id != null && Guid.TryParseExact(id, "N", out _) && ids.Add(id);
    }

    private static bool Name(string value) => value != null && value.Length is > 0 and <= 128
        && value.All(character => !char.IsControl(character));
    private static bool Finite(Vector3Data value) => float.IsFinite(value.X)
        && float.IsFinite(value.Y) && float.IsFinite(value.Z);
    private static bool Finite(Vector2Data value) => float.IsFinite(value.X) && float.IsFinite(value.Y);
    private static bool FiniteColor(ColorData value) => new[] { value.R, value.G, value.B, value.A }
        .All(component => float.IsFinite(component) && component is >= 0 and <= 1);
    private static bool Normal(Vector3Data value)
    {
        if (!Finite(value)) return false;
        double length = Math.Sqrt((double)value.X * value.X + (double)value.Y * value.Y + (double)value.Z * value.Z);
        return Math.Abs(length - 1) <= 0.01;
    }
    private static bool HasNondegenerateTriangle(ModelAdditionPart part)
    {
        for (int index = 0; index < part.TriangleIndices.Length; index += 3)
        {
            var a = part.Positions[part.TriangleIndices[index]];
            var b = part.Positions[part.TriangleIndices[index + 1]];
            var c = part.Positions[part.TriangleIndices[index + 2]];
            double ux = (double)b.X - a.X, uy = (double)b.Y - a.Y, uz = (double)b.Z - a.Z;
            double vx = (double)c.X - a.X, vy = (double)c.Y - a.Y, vz = (double)c.Z - a.Z;
            double nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
            if (nx * nx + ny * ny + nz * nz > 0) return true;
        }
        return false;
    }
    private static bool IsLowerHex(string value, int length) => value != null && value.Length == length
        && value.All(character => character is >= '0' and <= '9' or >= 'a' and <= 'f');

    private static string AssetPath(string directory, string relative)
    {
        Require(relative != null && !Path.IsPathRooted(relative) && !relative.Contains('\\')
            && relative.Split('/') is ["edits", "addition-assets", var filename]
            && filename.Length > 5 && filename.EndsWith(".rgba", StringComparison.Ordinal)
            && filename.IndexOfAny(Path.GetInvalidFileNameChars()) < 0, "MODEL_ADDITION_IMAGE_PATH",
            "Image payloads must use flat edits/addition-assets/*.rgba paths.");
        string path = Path.GetFullPath(Path.Combine(directory, relative));
        string root = Path.GetFullPath(Path.Combine(directory, "edits", "addition-assets"));
        Require(path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.Ordinal),
            "MODEL_ADDITION_IMAGE_PATH", "Image payload path escapes addition-assets.");
        return path;
    }

    private static void ValidateAssetInventory(string directory, HashSet<string> declared)
    {
        var edits = new DirectoryInfo(Path.Combine(directory, "edits"));
        Require(edits.Exists, "MODEL_ADDITION_IMAGE_PATH", "Session edits directory is missing.");
        RejectLink(edits);
        var assetDirectory = new DirectoryInfo(Path.Combine(edits.FullName, "addition-assets"));
        if (!assetDirectory.Exists)
        {
            Require(declared.Count == 0, "MODEL_ADDITION_IMAGE_PATH", "Addition asset directory is missing.");
            return;
        }
        RejectLink(assetDirectory);
        Require(assetDirectory.GetDirectories().Length == 0, "MODEL_ADDITION_IMAGE_PATH",
            "Addition assets must not contain subdirectories or links.");
        string[] actual = assetDirectory.GetFiles().Select(file =>
        {
            RejectLink(file);
            return file.FullName;
        }).ToArray();
        Require(declared.SetEquals(actual), "MODEL_ADDITION_ASSET_INVENTORY",
            "Addition asset inventory differs from the image declarations.");
    }

    private static void RejectLink(FileSystemInfo info) => Require(info.LinkTarget == null,
        "MODEL_ADDITION_IMAGE_PATH", "Symbolic links are not accepted in addition asset paths.");
}
