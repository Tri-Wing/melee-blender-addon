using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ApplyResult(string Output, string Sha256, bool CollisionChanged, int CollisionVertices,
    int CollisionLines, bool ModelChanged, int? ModelTriangles, bool MaterialChanged = false,
    bool LightChanged = false, bool JobjChanged = false, bool AnimationChanged = false,
    bool GameplayChanged = false);

public static class SessionApplier
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true, UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow };
    public static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    public static ApplyResult Apply(string directory, string output)
    {
        try { return ApplyCore(Path.GetFullPath(directory), Path.GetFullPath(output)); }
        catch (Exception e) when (e is JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        { throw new StageException("SESSION_FORMAT", $"Malformed session or edit: {e.Message}"); }
    }

    private static ApplyResult ApplyCore(string directory, string output)
    {
        Require(!Directory.Exists(output), "OUTPUT_DIRECTORY", "Output must be a file, not a directory.");
        Require(!output.StartsWith(directory + Path.DirectorySeparatorChar, StringComparison.Ordinal), "OUTPUT_SESSION", "Output must be outside the session directory.");
        using var manifest = Load("stage.json"); var m = manifest.RootElement;
        bool currentSession = m.TryGetProperty("protocolVersion", out var protocol)
            && protocol.TryGetInt32(out int protocolVersion)
            && protocolVersion == SessionExtractor.ProtocolVersion
            && m.TryGetProperty("schemaVersion", out var schema)
            && schema.TryGetInt32(out int schemaVersion)
            && schemaVersion == SessionExtractor.SchemaVersion
            && m.TryGetProperty("assetType", out var assetType)
            && assetType.ValueKind == JsonValueKind.String
            && assetType.GetString() == "melee-stage";
        Require(currentSession, "SESSION_VERSION", "Unsupported session version; rebuild/update and re-extract the DAT with the current CLI.");
        Require(m.GetProperty("source").GetProperty("file").GetString() == "source.dat", "SESSION_SOURCE", "Session must use its source.dat snapshot.");
        string sourcePath = Path.Combine(directory, "source.dat");
        byte[] sourceBytes = File.ReadAllBytes(sourcePath);
        Require(Hash(sourceBytes) == m.GetProperty("source").GetProperty("sha256").GetString(), "SOURCE_HASH", "Session source.dat no longer matches its expected hash.");
        var source = new StageArchive(sourcePath, sourceBytes);
        source.Validate();
        var expectedFiles = new HashSet<string>(StringComparer.Ordinal);
        foreach (var entry in m.GetProperty("baselineFiles").EnumerateArray())
        {
            string file = entry.GetProperty("file").GetString()!;
            Require((file.StartsWith("models/", StringComparison.Ordinal) || file == "collision/collision.json") && expectedFiles.Add(file), "SESSION_BASELINE", "Invalid/duplicate baseline file.");
            string path = Contained(file);
            Require(File.Exists(path) && Hash(File.ReadAllBytes(path)) == entry.GetProperty("sha256").GetString(),
                "SESSION_BASELINE_CHANGED", $"Protected baseline file {file} changed or is missing. Put supported changes in edits/collision.json.");
        }
        var actualFiles = new[] { "models", "collision" }.SelectMany(folder => Directory.GetFiles(Path.Combine(directory, folder), "*", SearchOption.AllDirectories))
            .Select(path => Path.GetRelativePath(directory, path).Replace('\\', '/')).ToHashSet(StringComparer.Ordinal);
        Require(expectedFiles.SetEquals(actualFiles) && expectedFiles.Contains("collision/collision.json"), "SESSION_BASELINE", "Baseline file inventory differs from the manifest.");
        var nodes = new List<ModelIdentityNode>(); int groupIndex = 0;
        foreach (var group in m.GetProperty("modelGroups").EnumerateArray())
        {
            string file = $"models/group-{groupIndex:D3}/group.json";
            Require(group.GetProperty("index").GetInt32() == groupIndex && group.GetProperty("file").GetString() == file && expectedFiles.Contains(file),
                "MODEL_IDENTITY_CHANGED", "Model group order or inventory changed.");
            using var doc = Load(file); var g = doc.RootElement;
            Require(g.GetProperty("id").GetString() == group.GetProperty("id").GetString() && g.GetProperty("index").GetInt32() == groupIndex, "MODEL_IDENTITY_CHANGED", "Group identity changed.");
            nodes.AddRange(g.GetProperty("nodes").Deserialize<ModelIdentityNode[]>(Json)!); groupIndex++;
        }
        Require(groupIndex == source.Inspect().ModelGroups && groupIndex == m.GetProperty("modelGroupCount").GetInt32(), "MODEL_IDENTITY_CHANGED", "Protected group count changed.");
        var catalog = ModelIdentityCatalog.Restore(nodes);
        var modelBaseline = ModelIdentity.Capture(source.Layout, catalog);
        modelBaseline.RequireUnchanged(new ModelIdentitySnapshot(nodes));
        var collision = CollisionData.Read(source.Layout);
        using var collisionDoc = Load("collision/collision.json");
        var c = collisionDoc.RootElement;
        string[] SourceIds(string field, int count)
        {
            var entries = c.GetProperty(field).EnumerateArray().ToArray();
            Require(entries.Length == count, "SESSION_COLLISION_IDS", $"Original {field} count changed.");
            for (int i = 0; i < count; i++) Require(entries[i].GetProperty("sourceIndex").GetInt32() == i, "SESSION_COLLISION_IDS", "Source collision ordering changed.");
            return entries.Select(e => e.GetProperty("id").GetString()!).ToArray();
        }
        var ids = new CollisionSourceIds(SourceIds("vertices", collision.Vertices.Length), SourceIds("lines", collision.Lines.Length), SourceIds("joints", collision.Joints.Length));
        string editPath = Path.Combine(directory, "edits/collision.json");
        string modelPath = Path.Combine(directory, "edits/models.json");
        string materialPath = Path.Combine(directory, "edits/materials.json");
        string lightPath = Path.Combine(directory, "edits/lights.json");
        string jobjPath = Path.Combine(directory, "edits/jobjs.json");
        string animationPath = Path.Combine(directory, "edits/animations.json");
        string gameplayPath = Path.Combine(directory, "edits/gameplay.json");
        string additionPath = Path.Combine(directory, "edits/additions.json");
        string additionAssets = Path.Combine(directory, "edits/addition-assets");
        string[] editFiles = Directory.GetFiles(Path.Combine(directory, "edits"), "*", SearchOption.AllDirectories);
        foreach (string edit in editFiles)
            Require(edit == editPath || edit == modelPath || edit == materialPath || edit == lightPath
                || edit == jobjPath || edit == animationPath || edit == additionPath
                || edit == gameplayPath
                || edit.StartsWith(additionAssets + Path.DirectorySeparatorChar, StringComparison.Ordinal),
                "EDIT_UNSUPPORTED", $"Unsupported edit file {Path.GetRelativePath(directory, edit)}.");
        Require(File.Exists(additionPath) || !editFiles.Any(edit =>
            edit.StartsWith(additionAssets + Path.DirectorySeparatorChar, StringComparison.Ordinal)),
            "EDIT_UNSUPPORTED", "Addition assets require edits/additions.json.");
        MaterialPropertyEdits? materialEdits = null;
        string[] declaredMaterialIds = [];
        if (File.Exists(materialPath))
        {
            declaredMaterialIds = m.TryGetProperty("editableMaterialProperties", out var list)
                ? list.EnumerateArray().Select(e => e.GetProperty("id").GetString()!).ToArray() : [];
            materialEdits = JsonSerializer.Deserialize<MaterialPropertyEdits>(File.ReadAllText(materialPath), Json);
            Require(materialEdits != null, "MATERIAL_EDIT_FORMAT", "Empty material edit document.");
        }
        ValidatedModelAdditionBatch? additionBatch = null;
        if (File.Exists(additionPath))
        {
            Require(new FileInfo(additionPath).Length <= 64L * 1024 * 1024,
                "MODEL_ADDITION_FORMAT", "Model-addition JSON exceeds the 64 MiB limit.");
            var additions = JsonSerializer.Deserialize<ModelAdditionEdits>(File.ReadAllText(additionPath), Json);
            Require(additions != null, "MODEL_ADDITION_FORMAT", "Empty model-addition edit document.");
            string[] declared = m.TryGetProperty("modelAdditionTargets", out var targets)
                ? targets.EnumerateArray().Select(target => target.GetProperty("id").GetString()!).ToArray() : [];
            additionBatch = ModelAdditionEditing.Validate(directory, source, modelBaseline, additions!, declared);
        }
        bool changed = File.Exists(editPath);
        if (changed)
        {
            var edits = JsonSerializer.Deserialize<CollisionEdits>(File.ReadAllText(editPath), Json);
            Require(edits != null, "COLLISION_EDIT_FORMAT", "Empty collision edit document.");
            collision = CollisionCompiler.Compile(collision, ids, edits!);
        }
        bool modelChanged = File.Exists(modelPath);
        var sourceModels = ModelSourceSnapshot.Capture(source.Layout, modelBaseline);
        string[] declaredModelIds = m.GetProperty("editableMeshes").EnumerateArray()
            .Select(target => target.GetProperty("id").GetString()!).ToArray();
        ModelEdits? modelEdits = null;
        if (modelChanged)
        {
            modelEdits = JsonSerializer.Deserialize<ModelEdits>(File.ReadAllText(modelPath), Json);
            Require(modelEdits != null, "MODEL_EDIT_FORMAT", "Empty model edit document.");
        }
        var modelPlan = ModelEditPlanner.Plan(sourceModels, modelEdits, declaredModelIds,
            materialEdits, declaredMaterialIds, additionBatch);

        StageLightEditRequest? lightRequest = null;
        if (File.Exists(lightPath))
        {
            var declared = m.TryGetProperty("lighting", out var lighting)
                ? lighting.GetProperty("lightSets").EnumerateArray()
                    .SelectMany(set => set.GetProperty("lights").EnumerateArray())
                    .Select(light => light.GetProperty("id").GetString()!).ToArray() : [];
            var edits = JsonSerializer.Deserialize<StageLightEdits>(File.ReadAllText(lightPath), Json);
            Require(edits != null, "LIGHT_EDIT_FORMAT", "Empty light edit document.");
            lightRequest = new(edits!, declared);
        }
        JobjTransformEditRequest? jobjRequest = null;
        if (File.Exists(jobjPath))
        {
            var declared = m.TryGetProperty("editableJobjs", out var list)
                ? list.EnumerateArray().Select(e => e.GetProperty("id").GetString()!).ToArray() : [];
            var edits = JsonSerializer.Deserialize<JobjTransformEdits>(File.ReadAllText(jobjPath), Json);
            Require(edits != null, "JOBJ_EDIT_FORMAT", "Empty JOBJ transform edit document.");
            jobjRequest = new(edits!, declared);
        }
        JointAnimationEditRequest? animationRequest = null;
        if (File.Exists(animationPath))
        {
            var declared = m.TryGetProperty("editableJointAnimations", out var list)
                ? list.EnumerateArray().Select(entry => JointAnimationEditing.TargetKey(
                    entry.GetProperty("groupIndex").GetInt32(), entry.GetProperty("slot").GetInt32(),
                    entry.GetProperty("jobjId").GetString()!)).ToArray() : [];
            var edits = JsonSerializer.Deserialize<JointAnimationEdits>(File.ReadAllText(animationPath), Json);
            Require(edits != null, "ANIMATION_EDIT_FORMAT", "Empty JOBJ animation edit document.");
            animationRequest = new(edits!, declared);
        }
        GameplayEditRequest? gameplayRequest = null;
        if (File.Exists(gameplayPath))
        {
            var declared = m.TryGetProperty("editableGameplayPoints", out var list)
                ? list.EnumerateArray().Select(entry =>
                    entry.GetProperty("id").GetString()!).ToArray() : [];
            var edits = JsonSerializer.Deserialize<GameplayEdits>(
                File.ReadAllText(gameplayPath), Json);
            Require(edits != null, "GAMEPLAY_EDIT_FORMAT",
                "Empty gameplay edit document.");
            gameplayRequest = new(edits!, declared);
        }
        var transactionPlan = new StageEditTransactionPlan(collision, changed, modelPlan,
            lightRequest, jobjRequest, animationRequest, gameplayRequest);
        var execution = StageEditTransaction.Execute(source, catalog, modelBaseline,
            transactionPlan);
        byte[] bytes = execution.Bytes;
        string temp = output + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var file = new FileStream(temp, FileMode.CreateNew)) file.Write(bytes);
            var reloaded = new StageArchive(temp); reloaded.Validate();
            StageEditTransaction.Verify(source, reloaded, catalog, modelBaseline,
                transactionPlan, execution);
            File.Move(temp, output, overwrite: true);
        }
        finally { if (File.Exists(temp)) File.Delete(temp); }
        var modelExecution = execution.Model;
        bool anyModelChanged = modelChanged || modelExecution.AdditionWrite != null;
        int modelTriangles = modelExecution.Changes.Sum(change => change.Geometry.TriangleIndices.Length / 3)
            + (modelExecution.AdditionWrite?.Chunks.Sum(chunk => chunk.TriangleCount) ?? 0);
        return new(output, Hash(bytes), changed, collision.Vertices.Length, collision.Lines.Length,
            anyModelChanged, anyModelChanged ? modelTriangles : null,
            modelExecution.MaterialWrite != null, execution.Lights != null,
            execution.Jobjs != null, execution.Animations != null,
            execution.Gameplay != null);

        string Contained(string relative)
        {
            string path = Path.GetFullPath(Path.Combine(directory, relative));
            Require(path.StartsWith(directory + Path.DirectorySeparatorChar, StringComparison.Ordinal), "SESSION_PATH", "Session path escapes its directory.");
            return path;
        }
        JsonDocument Load(string relative) => JsonDocument.Parse(File.ReadAllText(Contained(relative)));
    }
}
