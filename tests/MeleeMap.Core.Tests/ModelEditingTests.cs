using System.Text.Json;
using System.Text.Json.Nodes;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class ModelEditingTests
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    private static ModelEdits Triangle(string id) => new(2, "game-joint-local", [new(id,
        [new(0, 0, 0), new(10, 0, 0), new(0, 10, 0)], [0, 1, 2])]);

    [Fact]
    public void CompilesFlatNormalsAndDropsOnlyDegenerateTriangles()
    {
        var target = new EditableModel(Guid.NewGuid().ToString("N"), 0, 0, 0, 0, 0, 0);
        var input = Triangle(target.Id);
        input.Meshes[0] = input.Meshes[0] with { TriangleIndices = [0, 1, 2, 0, 0, 1] };
        var compiled = ModelEditing.Compile(input, target);
        Assert.Equal(3, compiled.Positions.Length);
        Assert.Equal(new[] { 0, 1, 2 }, compiled.TriangleIndices);
        Assert.All(compiled.Normals!, normal => Assert.Equal(new Vector3Data(0, 0, 1), normal));
    }

    [Theory]
    [InlineData("target", "MODEL_EDIT_TARGET")]
    [InlineData("indices", "MODEL_INDEX")]
    [InlineData("empty", "MODEL_EMPTY")]
    [InlineData("nonfinite", "MODEL_NONFINITE")]
    [InlineData("limit", "MODEL_EDIT_COUNT")]
    [InlineData("space", "MODEL_EDIT_VERSION")]
    public void RejectsInvalidModelEdits(string kind, string code)
    {
        var target = new EditableModel(Guid.NewGuid().ToString("N"), 0, 0, 0, 0, 0, 0);
        var input = Triangle(target.Id); var mesh = input.Meshes[0];
        switch (kind)
        {
            case "target": input.Meshes[0] = mesh with { Id = Guid.NewGuid().ToString("N") }; break;
            case "indices": mesh.TriangleIndices[2] = 9; break;
            case "empty": mesh.TriangleIndices[2] = 0; break;
            case "nonfinite": mesh.Positions[0] = new(float.NaN, 0, 0); break;
            case "limit": input.Meshes[0] = mesh with { TriangleIndices = new int[(ModelEditing.MaxTriangles + 1) * 3] }; break;
            case "space": input = input with { CoordinateSpace = "blender" }; break;
        }
        Assert.Equal(code, Assert.Throws<StageException>(() => ModelEditing.Compile(input, target)).Code);
    }

    [Fact]
    public void TopologyComparisonUsesIndicesNotOnlyCounts()
    {
        var target = new EditableModel("test", 0, 0, 0, 0, 0, 0);
        var edit = Triangle(target.Id);
        var original = new MeshData(edit.Meshes[0].Positions,
            [new(1, 0, 0), new(1, 0, 0), new(1, 0, 0)], [0, 1, 2]);
        Assert.Equal(original.Normals, ModelEditing.Compile(edit, target, original).Normals);
        edit.Meshes[0] = edit.Meshes[0] with { TriangleIndices = [0, 2, 1] };
        Assert.All(ModelEditing.Compile(edit, target, original).Normals!, normal => Assert.Equal(new Vector3Data(0, 0, -1), normal));
    }

    [PrimaryFixtureFact]
    public void ModelApplyPreservesUnrelatedBytesIdentitiesAndCollision()
    {
        using var session = new Fixture();
        var target = session.Target;
        Assert.Equal((3, 1, 0), (target.GroupIndex, target.JobjIndex, target.DobjIndex));
        var input = Triangle(target.Id); session.Write(input);
        var result = SessionApplier.Apply(session.Directory, session.Output);
        Assert.True(result.ModelChanged); Assert.False(result.CollisionChanged); Assert.Equal(1, result.ModelTriangles);
        var output = new StageArchive(session.Output); var decoded = GxMeshDecoder.Decode(output.Layout, target.PobjOffset);
        Assert.Equal(input.Meshes[0].Positions, decoded.Positions);
        // GrNLa's original target culls front faces. Newly authored Blender
        // geometry must instead retain its outward-facing surfaces.
        int sourceFlags = new ArchiveDataReader(session.Source.Layout).UShort(target.PobjOffset + 12);
        int outputFlags = new ArchiveDataReader(output.Layout).UShort(target.PobjOffset + 12);
        Assert.Equal(0x8000, sourceFlags & 0xC000);
        Assert.Equal(0x4000, outputFlags & 0xC000);
        Assert.Equal(sourceFlags & ~0xC000, outputFlags & ~0xC000);
        Assert.All(decoded.Normals!, n => Assert.Equal(new Vector3Data(0, 0, 1), n));
        Assert.Equal(session.Source.Layout.Roots, output.Layout.Roots);
        session.Identity.RequireUnchanged(ModelIdentity.Capture(output.Layout, session.Catalog));
        for (int i = 0; i < session.Source.Layout.DataSize; i++)
        {
            if (i >= target.PobjOffset + 8 && i < target.PobjOffset + 20
                || i >= target.DobjOffset + 8 && i < target.DobjOffset + 12) continue;
            Assert.Equal(session.Source.Layout.Bytes[32 + i], output.Layout.Bytes[32 + i]);
        }
        foreach (var node in session.Identity.Nodes.Where(n => n.Kind == "pobj" && n.Id != target.Id))
        {
            var before = GxMeshDecoder.Decode(session.Source.Layout, node.SourceOffset);
            var after = GxMeshDecoder.Decode(output.Layout, node.SourceOffset);
            Assert.Equal(before.Positions, after.Positions); Assert.Equal(before.Normals, after.Normals);
            Assert.Equal(before.TriangleIndices, after.TriangleIndices);
        }
        Assert.Equal(CollisionData.Read(session.Source.Layout).Lines, CollisionData.Read(output.Layout).Lines);
        Assert.Equal(session.Source.Layout.Bytes, File.ReadAllBytes(Path.Combine(session.Directory, "source.dat")));
    }

    [PrimaryFixtureFact]
    public void DeletesAnEligiblePobjWithoutChangingItsDobjOrUnrelatedData()
    {
        using var session = new Fixture();
        var target = session.Target;
        session.Write(new ModelEdits(2, "game-joint-local", [], [target.Id]));
        var result = SessionApplier.Apply(session.Directory, session.Output);
        Assert.True(result.ModelChanged); Assert.Equal(0, result.ModelTriangles);

        var output = new StageArchive(session.Output);
        var reader = new ArchiveDataReader(output.Layout);
        Assert.Null(reader.Pointer(target.DobjOffset + 12));
        session.Identity.WithoutPobjs([target.Id]).RequireUnchanged(
            ModelIdentity.Capture(output.Layout, session.Catalog));
        for (int i = 0; i < session.Source.Layout.DataSize; i++)
        {
            if (i >= target.DobjOffset + 12 && i < target.DobjOffset + 16) continue;
            Assert.Equal(session.Source.Layout.Bytes[32 + i], output.Layout.Bytes[32 + i]);
        }
        Assert.Equal(session.Source.Layout.Bytes,
            File.ReadAllBytes(Path.Combine(session.Directory, "source.dat")));
    }

    [PrimaryFixtureFact]
    public void RejectsDuplicateUnsupportedOrEditedAndDeletedModelIds()
    {
        using var session = new Fixture();
        var target = session.Target;
        foreach (var edits in new[] {
            new ModelEdits(2, "game-joint-local", [], [target.Id, target.Id]),
            new ModelEdits(2, "game-joint-local", [Triangle(target.Id).Meshes[0]], [target.Id]),
            new ModelEdits(2, "game-joint-local", [], [Guid.NewGuid().ToString("N")]) })
        {
            session.Write(edits);
            Assert.Equal("MODEL_DELETE_TARGET",
                Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        }
    }

    [GrGdFixtureFact]
    public void MultiPobjDobjSupportsTopologyMaterialSplittingAndLinkedListDeletion()
    {
        var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrGd.dat"));
        var catalog = new ModelIdentityCatalog();
        var identity = ModelIdentity.Capture(source.Layout, catalog);
        var targets = ModelEditing.SelectAll(source.Layout, identity)
            .Where(target => target.GroupIndex == 2 && target.JobjIndex == 7
                && target.DobjIndex == 2).OrderBy(target => target.PobjIndex).ToArray();
        Assert.Equal(2, targets.Length);
        Assert.All(targets, target => { Assert.False(target.PositionsOnly); Assert.True(target.SharesDobj); });
        Assert.Equal(targets[0].DobjOffset + 12, targets[0].PobjLinkField);
        Assert.Equal(targets[0].PobjOffset + 4, targets[1].PobjLinkField);

        var original = GxMeshDecoder.Decode(source.Layout, targets[1].PobjOffset);
        var moved = new ModelEdit(targets[1].Id,
            original.Positions.Select(position => position with { X = position.X + 1 }).ToArray(),
            original.TriangleIndices);
        ModelEditing.Compile(new(2, "game-joint-local", [moved]), targets[1], original);
        var topology = Triangle(targets[1].Id);
        var compiled = ModelEditing.Compile(topology, targets[1], original);

        var split = ModelDobjSplitter.Write(source.Layout, identity, [targets[1].Id]);
        var splitLayout = new ArchiveLayout(split.Bytes);
        var splitIdentity = ModelIdentity.Capture(splitLayout, catalog);
        var splitTargets = ModelEditing.SelectAll(splitLayout, splitIdentity)
            .Where(target => targets.Any(originalTarget => originalTarget.Id == target.Id))
            .ToDictionary(target => target.Id);
        var first = splitTargets[targets[0].Id];
        var second = splitTargets[targets[1].Id];
        Assert.False(first.SharesDobj); Assert.False(second.SharesDobj);
        Assert.Equal(targets[0].DobjOffset, first.DobjOffset);
        Assert.Equal(split.DobjOffsets[targets[1].Id], second.DobjOffset);
        Assert.NotEqual(first.DobjOffset, second.DobjOffset);
        Assert.Equal(0, first.PobjIndex); Assert.Equal(0, second.PobjIndex);
        var splitReader = new ArchiveDataReader(splitLayout);
        int sharedMaterial = new ArchiveDataReader(source.Layout).Pointer(targets[0].DobjOffset + 8)!.Value;
        Assert.Equal(sharedMaterial, splitReader.Pointer(first.DobjOffset + 8));
        Assert.Equal(sharedMaterial, splitReader.Pointer(second.DobjOffset + 8));

        var replaced = new ArchiveLayout(ModelArchiveWriter.Write(splitLayout, second, compiled));
        ModelArchiveWriter.Verify(replaced, second, compiled);
        Assert.Equal(sharedMaterial, new ArchiveDataReader(replaced).Pointer(first.DobjOffset + 8));
        Assert.NotEqual(sharedMaterial, new ArchiveDataReader(replaced).Pointer(second.DobjOffset + 8));
        splitIdentity.RequireUnchanged(ModelIdentity.Capture(replaced, catalog));

        foreach (var target in targets)
        {
            var deleted = new ArchiveLayout(ModelDeletionWriter.Write(source.Layout, target));
            identity.WithoutPobjs([target.Id]).RequireUnchanged(
                ModelIdentity.Capture(deleted, catalog));
            var survivors = ModelEditing.SelectAll(deleted, ModelIdentity.Capture(deleted, catalog))
                .Where(candidate => candidate.GroupIndex == 2 && candidate.JobjIndex == 7
                    && candidate.DobjIndex == 2).ToArray();
            Assert.Single(survivors);
            Assert.Equal(0, survivors[0].PobjIndex);
        }

        ArchiveLayout bothDeleted = source.Layout;
        foreach (var target in targets.OrderByDescending(target => target.PobjIndex))
            bothDeleted = new(ModelDeletionWriter.Write(bothDeleted, target));
        identity.WithoutPobjs(targets.Select(target => target.Id)).RequireUnchanged(
            ModelIdentity.Capture(bothDeleted, catalog));
    }

    [GrGdFixtureFact]
    public void MultiPobjSessionApplySplitsForTopologyAndMaterialReplacement()
    {
        string root = Path.Combine(Path.GetTempPath(), "mme-grgd-split-" + Guid.NewGuid().ToString("N"));
        string directory = Path.Combine(root, "session");
        string outputPath = Path.Combine(root, "edited.dat");
        try
        {
            var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrGd.dat"));
            SessionExtractor.Extract(source, directory);
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "stage.json")));
            var nodes = new List<ModelIdentityNode>();
            foreach (var entry in manifest.RootElement.GetProperty("modelGroups").EnumerateArray())
            {
                using var group = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory,
                    entry.GetProperty("file").GetString()!)));
                nodes.AddRange(group.RootElement.GetProperty("nodes").Deserialize<ModelIdentityNode[]>(Json)!);
            }
            var catalog = ModelIdentityCatalog.Restore(nodes);
            var identity = ModelIdentity.Capture(source.Layout, catalog);
            var targets = ModelEditing.SelectAll(source.Layout, identity)
                .Where(target => target.GroupIndex == 2 && target.JobjIndex == 7
                    && target.DobjIndex == 2).OrderBy(target => target.PobjIndex).ToArray();
            var assignedMaterial = ModelMaterials.Select(source.Layout,
                ModelEditing.SelectAll(source.Layout, identity)).First(material => !material.UsesUv);
            var replacement = Triangle(targets[1].Id);
            replacement.Meshes[0] = replacement.Meshes[0] with {
                SourceMaterialId = assignedMaterial.Id
            };
            File.WriteAllText(Path.Combine(directory, "edits/models.json"),
                JsonSerializer.Serialize(replacement, Json));

            SessionApplier.Apply(directory, outputPath);
            var output = new StageArchive(outputPath);
            var editedIdentity = ModelIdentity.Capture(output.Layout, catalog);
            var editedTargets = ModelEditing.SelectAll(output.Layout, editedIdentity)
                .Where(target => targets.Any(original => original.Id == target.Id))
                .ToDictionary(target => target.Id);
            var first = editedTargets[targets[0].Id];
            var second = editedTargets[targets[1].Id];
            Assert.NotEqual(first.DobjOffset, second.DobjOffset);
            Assert.False(first.SharesDobj); Assert.False(second.SharesDobj);
            Assert.Equal(identity.Nodes.Count + 1, editedIdentity.Nodes.Count);
            ModelArchiveWriter.Verify(output.Layout, second,
                ModelEditing.Compile(replacement, second), assignedMaterial.MobjOffset);
            int originalMaterial = new ArchiveDataReader(source.Layout)
                .Pointer(targets[0].DobjOffset + 8)!.Value;
            Assert.Equal(originalMaterial, new ArchiveDataReader(output.Layout).Pointer(first.DobjOffset + 8));
            Assert.Equal(assignedMaterial.MobjOffset,
                new ArchiveDataReader(output.Layout).Pointer(second.DobjOffset + 8));
        }
        finally
        {
            if (System.IO.Directory.Exists(root)) System.IO.Directory.Delete(root, true);
        }
    }

    [GrGdFixtureFact]
    public void MultiPobjMaterialPropertyEditUsesCopyOnWriteDobjSplit()
    {
        string root = Path.Combine(Path.GetTempPath(), "mme-grgd-material-split-" + Guid.NewGuid().ToString("N"));
        string directory = Path.Combine(root, "session");
        string outputPath = Path.Combine(root, "edited.dat");
        try
        {
            var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrGd.dat"));
            SessionExtractor.Extract(source, directory);
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "stage.json")));
            var nodes = new List<ModelIdentityNode>();
            foreach (var entry in manifest.RootElement.GetProperty("modelGroups").EnumerateArray())
            {
                using var group = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory,
                    entry.GetProperty("file").GetString()!)));
                nodes.AddRange(group.RootElement.GetProperty("nodes").Deserialize<ModelIdentityNode[]>(Json)!);
            }
            var catalog = ModelIdentityCatalog.Restore(nodes);
            var identity = ModelIdentity.Capture(source.Layout, catalog);
            var targets = ModelEditing.SelectAll(source.Layout, identity)
                .Where(target => target.GroupIndex == 2 && target.JobjIndex == 7
                    && target.DobjIndex == 2).OrderBy(target => target.PobjIndex).ToArray();
            Assert.Contains(MaterialProperties.Select(source.Layout, identity),
                material => material.Id == targets[1].Id);
            var edits = new MaterialPropertyEdits(2,
                [new MaterialPropertyEdit(targets[1].Id, Ambient: [1, 2, 3])]);
            File.WriteAllText(Path.Combine(directory, "edits/materials.json"),
                JsonSerializer.Serialize(edits, Json));

            var result = SessionApplier.Apply(directory, outputPath);
            Assert.True(result.MaterialChanged);
            var output = new StageArchive(outputPath);
            var editedIdentity = ModelIdentity.Capture(output.Layout, catalog);
            var editedTargets = ModelEditing.SelectAll(output.Layout, editedIdentity)
                .Where(target => targets.Any(original => original.Id == target.Id))
                .ToDictionary(target => target.Id);
            var first = editedTargets[targets[0].Id];
            var second = editedTargets[targets[1].Id];
            var sourceReader = new ArchiveDataReader(source.Layout);
            var outputReader = new ArchiveDataReader(output.Layout);
            int originalMobj = sourceReader.Pointer(targets[0].DobjOffset + 8)!.Value;
            Assert.Equal(originalMobj, outputReader.Pointer(first.DobjOffset + 8));
            int editedMobj = outputReader.Pointer(second.DobjOffset + 8)!.Value;
            Assert.NotEqual(originalMobj, editedMobj);
            int editedMaterial = outputReader.Pointer(editedMobj + 12)!.Value;
            Assert.Equal(new byte[] { 1, 2, 3 }, Enumerable.Range(0, 3)
                .Select(index => outputReader.Byte(editedMaterial + index)).ToArray());
        }
        finally
        {
            if (System.IO.Directory.Exists(root)) System.IO.Directory.Delete(root, true);
        }
    }

    [PrimaryFixtureFact]
    public void CombinesModelAndCollisionEditsAndPreservesOutputOnFailure()
    {
        using var session = new Fixture(); session.Write(Triangle(session.Target.Id));
        using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(session.Directory, "collision/collision.json")));
        var c = document.RootElement;
        var vertices = c.GetProperty("vertices").EnumerateArray().Select(v => new CollisionEditVertex(v.GetProperty("id").GetString()!,
            v.GetProperty("position").GetProperty("x").GetSingle(), v.GetProperty("position").GetProperty("y").GetSingle())).ToArray();
        vertices[0] = vertices[0] with { Y = vertices[0].Y + 1 };
        var lines = c.GetProperty("lines").EnumerateArray().Select(l => new CollisionEditLine(l.GetProperty("id").GetString()!,
            l.GetProperty("vertex0Id").GetString()!, l.GetProperty("vertex1Id").GetString()!, l.GetProperty("jointId").GetString()!,
            l.GetProperty("category").GetString()!, l.GetProperty("highFlags").GetUInt16(), l.GetProperty("lowFlags").GetUInt16())).ToArray();
        File.WriteAllText(Path.Combine(session.Directory, "edits/collision.json"), JsonSerializer.Serialize(new CollisionEdits(2, "game", vertices, lines), Json));
        var result = SessionApplier.Apply(session.Directory, session.Output);
        Assert.True(result.CollisionChanged && result.ModelChanged);
        Assert.Contains(new CollisionVertex(vertices[0].X, vertices[0].Y), CollisionData.Read(new StageArchive(session.Output).Layout).Vertices);
        var saved = File.ReadAllBytes(session.Output);
        session.Write(Triangle(Guid.NewGuid().ToString("N")));
        Assert.Equal("MODEL_EDIT_TARGET", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        Assert.Equal(saved, File.ReadAllBytes(session.Output));
        Assert.Empty(System.IO.Directory.GetFiles(session.Root, "*.tmp"));
    }

    [PrimaryFixtureFact]
    public void MultipleRigidTargetsExportTogetherAndRejectDuplicateOrUnsupportedTargets()
    {
        using var session = new Fixture();
        var targets = ModelEditing.SelectAll(session.Source.Layout, session.Identity).Where(t => !t.PositionsOnly).ToArray();
        Assert.True(targets.Length > 1);
        using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(session.Directory, "stage.json")));
        Assert.Equal(targets.Select(t => t.Id), manifest.RootElement.GetProperty("editableMeshes")
            .EnumerateArray().Where(t => !t.GetProperty("positionsOnly").GetBoolean()).Select(t => t.GetProperty("id").GetString()));
        var edits = new ModelEdits(2, "game-joint-local", targets.Select(t => Triangle(t.Id).Meshes[0]).ToArray());
        session.Write(edits);
        var result = SessionApplier.Apply(session.Directory, session.Output);
        Assert.Equal(targets.Length, result.ModelTriangles);
        var output = new StageArchive(session.Output);
        foreach (var target in targets)
            ModelArchiveWriter.Verify(output.Layout, target, ModelEditing.Compile(Triangle(target.Id), target));
        foreach (var node in session.Identity.Nodes.Where(n => n.Kind == "pobj" && !targets.Any(t => t.Id == n.Id)))
        {
            var before = GxMeshDecoder.Decode(session.Source.Layout, node.SourceOffset);
            var after = GxMeshDecoder.Decode(output.Layout, node.SourceOffset);
            Assert.Equal(before.Positions, after.Positions);
            Assert.Equal(before.Normals, after.Normals);
            Assert.Equal(before.TriangleIndices, after.TriangleIndices);
        }
        var saved = File.ReadAllBytes(session.Output);
        session.Write(edits with { Meshes = [edits.Meshes[0], edits.Meshes[0]] });
        Assert.Equal("MODEL_EDIT_TARGET", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        Assert.Equal(saved, File.ReadAllBytes(session.Output));
        string unsupported = session.Identity.Nodes.First(n => n.Kind == "pobj" && !ModelEditing.SelectAll(session.Source.Layout, session.Identity).Any(t => t.Id == n.Id)).Id;
        session.Write(edits with { Meshes = [edits.Meshes[0], Triangle(unsupported).Meshes[0]] });
        Assert.Equal("MODEL_EDIT_TARGET", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        Assert.Equal(saved, File.ReadAllBytes(session.Output));
        var invalid = Triangle(targets[1].Id).Meshes[0]; invalid.TriangleIndices[2] = 99;
        session.Write(edits with { Meshes = [edits.Meshes[0], invalid] });
        Assert.Equal("MODEL_INDEX", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        Assert.Equal(saved, File.ReadAllBytes(session.Output));
    }

    [PrimaryFixtureFact]
    public void VertexMovesPreserveAppearanceAcrossTargetsAndComposeWithTopologyReplacement()
    {
        using var session = new Fixture();
        var targets = ModelEditing.SelectAll(session.Source.Layout, session.Identity).OrderByDescending(t => t.PositionsOnly).ToArray();
        Assert.Equal(45, targets.Count(t => t.PositionsOnly));
        var originals = targets.Select(t => GxMeshDecoder.Decode(session.Source.Layout, t.PobjOffset)).ToArray();
        var meshes = targets.Select((t, i) => new ModelEdit(t.Id,
            originals[i].Positions.Select(p => p with { Y = p.Y + 1.25f }).ToArray(), originals[i].TriangleIndices)).ToArray();
        // Mix the two paths in the same transaction.
        meshes[^1] = Triangle(targets[^1].Id).Meshes[0];
        session.Write(new(2, "game-joint-local", meshes));
        SessionApplier.Apply(session.Directory, session.Output);
        var output = new StageArchive(session.Output);
        for (int i = 0; i < targets.Length - 1; i++)
            ModelPositionWriter.Verify(output.Layout, session.Source.Layout, targets[i], originals[i] with { Positions = meshes[i].Positions });
        ModelArchiveWriter.Verify(output.Layout, targets[^1], ModelEditing.Compile(Triangle(targets[^1].Id), targets[^1]));
        // The original material/texture/UV/normal buffers all remain byte-identical.
        for (int i = 0; i < session.Source.Layout.DataSize; i++)
        {
            bool changed = targets.Any(t => i >= t.PobjOffset + 8 && i < t.PobjOffset + 12
                || i >= t.PobjOffset + 14 && i < t.PobjOffset + 20)
                || i >= targets[^1].PobjOffset + 12 && i < targets[^1].PobjOffset + 14
                || i >= targets[^1].DobjOffset + 8 && i < targets[^1].DobjOffset + 12;
            if (!changed) Assert.Equal(session.Source.Layout.Bytes[32 + i], output.Layout.Bytes[32 + i]);
        }
    }

    [PrimaryFixtureFact]
    public void StageMaterialAssignmentWritesCornerUvsAndPreservesSourceMaterialData()
    {
        using var session = new Fixture();
        var targets = ModelEditing.SelectAll(session.Source.Layout, session.Identity);
        var material = ModelMaterials.Select(session.Source.Layout, targets).First(m => m.UsesUv);
        Vector3Data[] points = [new(0, 0, 0), new(2, 0, 0), new(2, 2, 0), new(0, 2, 0)];
        int[] indices = [0, 1, 2, 0, 0, 1, 0, 2, 3];
        Vector2Data[] uvs = [new(0, 0), new(1, 0), new(1, 1), new(9, 9), new(9, 9), new(9, 9), new(0.25f, 0), new(1, 1), new(0, 1)];
        var edit = new ModelEdits(2, "game-joint-local", [new(session.Target.Id, points, indices, material.Id, uvs)]);
        session.Write(edit);
        SessionApplier.Apply(session.Directory, session.Output);
        var output = new StageArchive(session.Output);
        var decoded = GxMeshDecoder.Decode(output.Layout, session.Target.PobjOffset);
        Assert.Equal(new[] { uvs[0], uvs[1], uvs[2], uvs[6], uvs[7], uvs[8] }, decoded.TexCoords0);
        Assert.Equal(material.MobjOffset, new ArchiveDataReader(output.Layout).Pointer(session.Target.DobjOffset + 8));
        for (int i = 0; i < session.Source.Layout.DataSize; i++)
        {
            bool changed = i >= session.Target.PobjOffset + 8 && i < session.Target.PobjOffset + 20
                || i >= session.Target.DobjOffset + 8 && i < session.Target.DobjOffset + 12;
            if (!changed) Assert.Equal(session.Source.Layout.Bytes[32 + i], output.Layout.Bytes[32 + i]);
        }
        var saved = File.ReadAllBytes(session.Output);
        var nonfinite = edit with { Meshes = [edit.Meshes[0] with {
            TexCoords = Enumerable.Repeat(new Vector2Data(float.NaN, 0), indices.Length).ToArray() }] };
        Assert.Equal("MODEL_UV", Assert.Throws<StageException>(() => ModelEditing.Compile(nonfinite, session.Target)).Code);
        foreach (var invalid in new[] {
            edit.Meshes[0] with { TexCoords = null },
            edit.Meshes[0] with { TexCoords = [new(0, 0)] },
            edit.Meshes[0] with { SourceMaterialId = "unsupported" },
            edit.Meshes[0] with { UseGreyMaterial = true } })
        {
            session.Write(edit with { Meshes = [invalid] });
            Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output));
            Assert.Equal(saved, File.ReadAllBytes(session.Output));
        }
    }

    [PrimaryFixtureFact]
    public void UvOnlyEditsRetainSourceNormalsAndCulling()
    {
        using var session = new Fixture();
        var targets = ModelEditing.SelectAll(session.Source.Layout, session.Identity);
        var material = ModelMaterials.Select(session.Source.Layout, targets).First(m => m.UsesUv);
        var target = targets.Single(t => t.Id == material.Id);
        var original = GxMeshDecoder.Decode(session.Source.Layout, target.PobjOffset);
        var uv = original.TriangleIndices.Select(i => original.TexCoords0![i] with { X = original.TexCoords0[i].X + 0.125f }).ToArray();
        var edit = new ModelEdits(2, "game-joint-local", [new(target.Id, original.Positions, original.TriangleIndices, material.Id, uv)]);
        session.Write(edit); SessionApplier.Apply(session.Directory, session.Output);
        var output = new StageArchive(session.Output);
        int culling = new ArchiveDataReader(session.Source.Layout).UShort(target.PobjOffset + 12) & 0xC000;
        var expected = ModelEditing.Compile(edit, target, original);
        ModelArchiveWriter.Verify(output.Layout, target, expected, material.MobjOffset, culling);
        Assert.Equal(original.Positions[original.TriangleIndices[0]], expected.Positions[0]);
        Assert.Equal(original.Normals![original.TriangleIndices[0]], expected.Normals![0]);
    }

    [PrimaryFixtureFact]
    public void OldSessionsRemainCollisionOnlyAndTargetsAreRecomputed()
    {
        using var session = new Fixture(); session.Write(Triangle(session.Target.Id));
        string path = Path.Combine(session.Directory, "stage.json"); var manifest = JsonNode.Parse(File.ReadAllText(path))!;
        manifest.AsObject().Remove("editableMeshes"); manifest.AsObject().Remove("editableMesh"); File.WriteAllText(path, manifest.ToJsonString());
        Assert.Equal("MODEL_EDIT_TARGET", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
        File.Delete(Path.Combine(session.Directory, "edits/models.json"));
        SessionApplier.Apply(session.Directory, session.Output);
        Assert.Equal(session.Source.Layout.Bytes, File.ReadAllBytes(session.Output));
    }

    [PrimaryFixtureFact]
    public void AnimatedMaterialsRejectReplacementEvenWithForgedManifestPermission()
    {
        using var session = new Fixture();
        var targets = ModelEditing.SelectAll(session.Source.Layout, session.Identity);
        var target = targets.First(t => t.PositionsOnly);
        Assert.DoesNotContain(ModelMaterials.Select(session.Source.Layout, targets), m => m.Id == target.Id);
        var original = GxMeshDecoder.Decode(session.Source.Layout, target.PobjOffset);
        var move = new ModelEdit(target.Id, original.Positions.Select(p => p with { X = p.X + 2 }).ToArray(), original.TriangleIndices);
        session.Write(new(2, "game-joint-local", [move]));
        SessionApplier.Apply(session.Directory, session.Output);
        var saved = File.ReadAllBytes(session.Output);
        string path = Path.Combine(session.Directory, "stage.json");
        var manifest = JsonNode.Parse(File.ReadAllText(path))!;
        foreach (var entry in manifest["editableMeshes"]!.AsArray()) entry!["positionsOnly"] = false;
        File.WriteAllText(path, manifest.ToJsonString());
        var reversed = original.TriangleIndices.Reverse().ToArray();
        foreach (var invalid in new[] { Triangle(target.Id).Meshes[0], move with { TriangleIndices = reversed },
            move with { UseGreyMaterial = true }, move with { SourceMaterialId = session.Target.Id },
            move with { TexCoords = [new(0, 0)] } })
        {
            session.Write(new(2, "game-joint-local", [invalid]));
            Assert.Equal("MODEL_POSITION_ONLY", Assert.Throws<StageException>(() => SessionApplier.Apply(session.Directory, session.Output)).Code);
            Assert.Equal(saved, File.ReadAllBytes(session.Output));
        }
    }

    private sealed class Fixture : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "mme-model-" + Guid.NewGuid().ToString("N"));
        public string Directory => Path.Combine(Root, "session");
        public string Output => Path.Combine(Root, "edited.dat");
        public StageArchive Source { get; } = new(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
        public ModelIdentityCatalog Catalog { get; }
        public ModelIdentitySnapshot Identity { get; }
        public EditableModel Target { get; }
        public Fixture()
        {
            SessionExtractor.Extract(Source, Directory);
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(Directory, "stage.json")));
            var nodes = new List<ModelIdentityNode>();
            foreach (var entry in manifest.RootElement.GetProperty("modelGroups").EnumerateArray())
            {
                using var group = JsonDocument.Parse(File.ReadAllText(Path.Combine(Directory, entry.GetProperty("file").GetString()!)));
                nodes.AddRange(group.RootElement.GetProperty("nodes").Deserialize<ModelIdentityNode[]>(Json)!);
            }
            Catalog = ModelIdentityCatalog.Restore(nodes); Identity = ModelIdentity.Capture(Source.Layout, Catalog);
            Target = ModelEditing.Select(Source.Layout, Identity)!; Assert.NotNull(Target);
            Assert.Equal(Target.Id, manifest.RootElement.GetProperty("editableMesh").GetProperty("id").GetString());
        }
        public void Write(ModelEdits edits) => File.WriteAllText(Path.Combine(Directory, "edits/models.json"), JsonSerializer.Serialize(edits, Json));
        public void Dispose() => System.IO.Directory.Delete(Root, true);
    }
}
