using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ApplyResult(string Output, string Sha256, bool CollisionChanged, int CollisionVertices, int CollisionLines, bool ModelChanged, int? ModelTriangles);

public static class SessionApplier
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true, UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow };
    public static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    public static ApplyResult Apply(string directory, string output)
    {
        try { return ApplyCore(Path.GetFullPath(directory), Path.GetFullPath(output)); }
        catch (Exception e) when (e is JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        { throw new StageException("SESSION_FORMAT", $"Malformed session or collision edit: {e.Message}"); }
    }

    private static ApplyResult ApplyCore(string directory, string output)
    {
        Require(!Directory.Exists(output), "OUTPUT_DIRECTORY", "Output must be a file, not a directory.");
        Require(!output.StartsWith(directory + Path.DirectorySeparatorChar, StringComparison.Ordinal), "OUTPUT_SESSION", "Output must be outside the session directory.");
        using var manifest = Load("stage.json"); var m = manifest.RootElement;
        Require(m.GetProperty("protocolVersion").GetInt32() == SessionExtractor.ProtocolVersion && m.GetProperty("assetType").GetString() == "melee-stage"
            && m.GetProperty("schemaVersion").GetInt32() == 1, "SESSION_VERSION", "Unsupported session version; re-extract with the current CLI.");
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
        foreach (string edit in Directory.GetFiles(Path.Combine(directory, "edits"), "*", SearchOption.AllDirectories))
            Require(edit == editPath || edit == modelPath, "EDIT_UNSUPPORTED", $"Unsupported edit file {Path.GetRelativePath(directory, edit)}.");
        bool changed = File.Exists(editPath);
        byte[] bytes = source.Layout.Bytes;
        if (changed)
        {
            var edits = JsonSerializer.Deserialize<CollisionEdits>(File.ReadAllText(editPath), Json);
            Require(edits != null, "COLLISION_EDIT_FORMAT", "Empty collision edit document.");
            collision = CollisionCompiler.Compile(collision, ids, edits!);
            bytes = CollisionArchiveWriter.Write(source.Layout, collision);
        }
        bool modelChanged = File.Exists(modelPath);
        var compiledModels = new List<(EditableModel Target, MeshData Mesh)>();
        if (modelChanged)
        {
            var eligible = ModelEditing.SelectAll(source.Layout, modelBaseline).ToDictionary(t => t.Id);
            // Legacy sessions retain their single-target permissions.
            var declaredIds = m.TryGetProperty("editableMeshes", out var declaredMany)
                ? declaredMany.EnumerateArray().Select(t => t.GetProperty("id").GetString()!).ToArray()
                : m.TryGetProperty("editableMesh", out var declared) && declared.ValueKind == JsonValueKind.Object
                    ? new[] { declared.GetProperty("id").GetString()! } : Array.Empty<string>();
            var edits = JsonSerializer.Deserialize<ModelEdits>(File.ReadAllText(modelPath), Json);
            Require(edits?.Meshes is { Length: > 0 }, "MODEL_EDIT_FORMAT", "Empty model edit document.");
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var edit in edits!.Meshes)
            {
                Require(edit != null && edit.Id != null && seen.Add(edit.Id) && declaredIds.Contains(edit.Id)
                    && eligible.ContainsKey(edit.Id), "MODEL_EDIT_TARGET", "Model edit target is unsupported, duplicated, or absent from this session.");
                var target = eligible[edit!.Id];
                compiledModels.Add((target, ModelEditing.Compile(edits with { Meshes = [edit] }, target)));
            }
            // Compile the complete batch before writing any replacement.
            foreach (var (target, mesh) in compiledModels)
                bytes = ModelArchiveWriter.Write(new ArchiveLayout(bytes), target, mesh);
        }
        string temp = output + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var file = new FileStream(temp, FileMode.CreateNew)) file.Write(bytes);
            var reloaded = new StageArchive(temp); reloaded.Validate();
            modelBaseline.RequireUnchanged(ModelIdentity.Capture(reloaded.Layout, catalog));
            var written = CollisionData.Read(reloaded.Layout);
            Require(written.Vertices.SequenceEqual(collision.Vertices) && written.Lines.SequenceEqual(collision.Lines)
                && written.Ranges.SequenceEqual(collision.Ranges) && written.Attachments.SequenceEqual(collision.Attachments)
                && written.Joints.Length == collision.Joints.Length && written.Joints.Zip(collision.Joints).All(pair =>
                    pair.First.Ranges.SequenceEqual(pair.Second.Ranges)
                    && (pair.First.Left, pair.First.Bottom, pair.First.Right, pair.First.Top, pair.First.VertexStart, pair.First.VertexCount)
                        == (pair.Second.Left, pair.Second.Bottom, pair.Second.Right, pair.Second.Top, pair.Second.VertexStart, pair.Second.VertexCount)), "COLLISION_WRITE_MISMATCH", "Reloaded collision differs from compiled edits.");
            foreach (var (target, mesh) in compiledModels) ModelArchiveWriter.Verify(reloaded.Layout, target, mesh);
            if (!changed && !modelChanged) Require(source.Layout.SemanticHash() == reloaded.Layout.SemanticHash(), "ROUNDTRIP_MISMATCH", "No-edit apply changed archive semantics.");
            File.Move(temp, output, overwrite: true);
        }
        finally { if (File.Exists(temp)) File.Delete(temp); }
        return new(output, Hash(bytes), changed, collision.Vertices.Length, collision.Lines.Length, modelChanged, modelChanged ? compiledModels.Sum(pair => pair.Mesh.TriangleIndices.Length / 3) : null);

        string Contained(string relative)
        {
            string path = Path.GetFullPath(Path.Combine(directory, relative));
            Require(path.StartsWith(directory + Path.DirectorySeparatorChar, StringComparison.Ordinal), "SESSION_PATH", "Session path escapes its directory.");
            return path;
        }
        JsonDocument Load(string relative) => JsonDocument.Parse(File.ReadAllText(Contained(relative)));
    }
}
