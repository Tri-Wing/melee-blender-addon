using System.Security.Cryptography;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public sealed class GrGdFixtureFactAttribute : FactAttribute
{
    public GrGdFixtureFactAttribute()
    {
        if (!File.Exists(Path.Combine(CorpusTests.CorpusDirectory, "GrGd.dat")))
            Skip = "GrGd.dat is required for the programmatic attachment integration test.";
    }
}

public class ModelAdditionEditingTests
{
    [PrimaryFixtureFact]
    public void ValidatesConstantAndTexturedPartsWithVerifiedAssets()
    {
        using var fixture = new Fixture();
        string imageId = Id();
        byte[] pixels = [255, 0, 0, 255, 0, 255, 0, 128];
        string relative = $"edits/addition-assets/{imageId}.rgba";
        Directory.CreateDirectory(Path.Combine(fixture.Directory, "edits/addition-assets"));
        File.WriteAllBytes(Path.Combine(fixture.Directory, relative), pixels);
        var constant = Material(Id(), null);
        var textured = Material(Id(), imageId);
        var edit = Batch(fixture.Target.Id,
            [Part(Id(), constant.Id, null), Part(Id(), textured.Id,
                [new(0, 0), new(1, 0), new(0, 1)])],
            [constant, textured],
            [new(imageId, 2, 1, ModelAdditionEditing.PixelEncoding, relative, Hash(pixels))]);

        var result = ModelAdditionEditing.Validate(fixture.Directory, fixture.Source,
            fixture.Identity, edit, [fixture.Target.Id]);

        Assert.Equal(2, result.TriangleCount);
        Assert.Equal(pixels.Length, result.PixelBytes);
        Assert.Equal(pixels, result.Images[imageId].Pixels);
        Assert.Equal(fixture.Target.Id, Assert.Single(result.Targets).Key);
    }

    [PrimaryFixtureFact]
    public void RejectsInvalidGeometryMaterialAndForgedTarget()
    {
        using var fixture = new Fixture();
        var material = Material(Id(), null);
        var valid = Batch(fixture.Target.Id, [Part(Id(), material.Id, null)], [material], []);
        var part = valid.Additions[0].Parts[0];

        var cases = new (ModelAdditionEdits Edits, string Code)[]
        {
            (valid with { CoordinateSpace = "blender" }, "MODEL_ADDITION_VERSION"),
            (valid with { Additions = [valid.Additions[0] with { TargetJobjId = Id() }] }, "MODEL_ADDITION_TARGET"),
            (valid with { Additions = [valid.Additions[0] with { Parts = [part with { TriangleIndices = [0, 1, 9] }] }] }, "MODEL_ADDITION_INDEX"),
            (valid with { Additions = [valid.Additions[0] with { Parts = [part with { CornerNormals = [new(0, 0, 0), new(0, 0, 1), new(0, 0, 1)] }] }] }, "MODEL_ADDITION_NORMAL"),
            (valid with { Additions = [valid.Additions[0] with { Parts = [part with { TexCoords0 = [new(0, 0), new(1, 0), new(0, 1)] }] }] }, "MODEL_ADDITION_UV"),
            (valid with { Materials = [material with { BaseColor = new(float.NaN, 1, 1, 1) }] }, "MODEL_ADDITION_MATERIAL"),
            (valid with { Materials = [material with { Preset = "unknown" }] }, "MODEL_ADDITION_MATERIAL"),
        };

        foreach (var test in cases)
            Assert.Equal(test.Code, Assert.Throws<StageException>(() =>
                ModelAdditionEditing.Validate(fixture.Directory, fixture.Source,
                    fixture.Identity, test.Edits, [fixture.Target.Id])).Code);

        string unsupported = fixture.Identity.Nodes.First(node => node.Kind == "jobj"
            && node.Id != fixture.Target.Id).Id;
        var forged = valid with { Additions = [valid.Additions[0] with { TargetJobjId = unsupported }] };
        Assert.Equal("MODEL_ADDITION_TARGET", Assert.Throws<StageException>(() =>
            ModelAdditionEditing.Validate(fixture.Directory, fixture.Source,
                fixture.Identity, forged, [unsupported])).Code);
    }

    [PrimaryFixtureFact]
    public void RejectsBadImagePayloadsUndeclaredAssetsAndLinks()
    {
        using var fixture = new Fixture();
        string assets = Path.Combine(fixture.Directory, "edits/addition-assets");
        Directory.CreateDirectory(assets);
        string imageId = Id();
        string relative = $"edits/addition-assets/{imageId}.rgba";
        string payload = Path.Combine(fixture.Directory, relative);
        byte[] pixels = [1, 2, 3, 4];
        File.WriteAllBytes(payload, pixels);
        var material = Material(Id(), imageId);
        var valid = Batch(fixture.Target.Id,
            [Part(Id(), material.Id, [new(0, 0), new(1, 0), new(0, 1)])], [material],
            [new(imageId, 1, 1, ModelAdditionEditing.PixelEncoding, relative, Hash(pixels))]);

        var badHash = valid with { Images = [valid.Images[0] with { Sha256 = new string('0', 64) }] };
        Assert.Equal("MODEL_ADDITION_IMAGE_HASH", Error(badHash));
        File.WriteAllBytes(payload, [1, 2, 3]);
        Assert.Equal("MODEL_ADDITION_IMAGE_LENGTH", Error(valid));
        File.WriteAllBytes(payload, pixels);
        File.WriteAllBytes(Path.Combine(assets, "undeclared.rgba"), pixels);
        Assert.Equal("MODEL_ADDITION_ASSET_INVENTORY", Error(valid));
        File.Delete(Path.Combine(assets, "undeclared.rgba"));

        string outside = Path.Combine(fixture.Root, "outside.rgba");
        File.WriteAllBytes(outside, pixels);
        File.Delete(payload);
        File.CreateSymbolicLink(payload, outside);
        Assert.Equal("MODEL_ADDITION_IMAGE_PATH", Error(valid));

        string Error(ModelAdditionEdits edits) => Assert.Throws<StageException>(() =>
            ModelAdditionEditing.Validate(fixture.Directory, fixture.Source,
                fixture.Identity, edits, [fixture.Target.Id])).Code;
    }

    [PrimaryFixtureFact]
    public void AppendsConstantGeometryAsOrdinaryDobjPobjExtensionDeterministically()
    {
        using var fixture = new Fixture();
        var material = Material(Id(), null) with { BaseColor = new(0.25f, 0.5f, 0.75f, 1) };
        var part = Part(Id(), material.Id, null) with
        {
            TriangleIndices = [0, 1, 2, 0, 0, 1],
            CornerNormals = Enumerable.Repeat(new Vector3Data(0, 0, 1), 6).ToArray()
        };
        var edits = Batch(fixture.Target.Id, [part], [material], []);
        var batch = ModelAdditionEditing.Validate(fixture.Directory, fixture.Source,
            fixture.Identity, edits, [fixture.Target.Id]);

        var write = ModelAdditionArchiveWriter.Write(fixture.Source.Layout, fixture.Identity, batch);
        var repeated = ModelAdditionArchiveWriter.Write(fixture.Source.Layout, fixture.Identity, batch);
        var output = new StageArchive("added.dat", write.Bytes);
        var planned = ModelAdditionPlanner.Plan(batch);

        Assert.Equal(write.Bytes, repeated.Bytes);
        Assert.Single(write.Chunks);
        Assert.Equal(1, write.Chunks[0].TriangleCount);
        Assert.Single(write.PatchedSourceFields);
        Assert.Empty(output.Validate());
        Assert.Equal(CollisionData.Read(fixture.Source.Layout).Lines, CollisionData.Read(output.Layout).Lines);
        var catalog = ModelIdentityCatalog.Restore(fixture.Identity.Nodes);
        var extended = ModelIdentity.Capture(output.Layout, catalog);
        Assert.Equal(fixture.Identity.Nodes.Count + 2, extended.Nodes.Count);
        Assert.Equal(fixture.Identity.Nodes.Count(node => node.Kind == "dobj"
            && node.OwnerId == fixture.Target.Id) + 1,
            extended.Nodes.Count(node => node.Kind == "dobj" && node.OwnerId == fixture.Target.Id));
        ModelAdditionVerifier.Verify(fixture.Source.Layout, output.Layout,
            fixture.Identity, planned);
        var wrongPositions = planned.Chunks[0].Mesh.Positions
            .Select(position => position with { X = position.X + 10 }).ToArray();
        var wrong = planned with
        {
            Chunks = [planned.Chunks[0] with
                { Mesh = planned.Chunks[0].Mesh with { Positions = wrongPositions } }]
        };
        Assert.Equal("MODEL_ADDITION_PLAN_MISMATCH",
            Assert.Throws<StageException>(() => ModelAdditionVerifier.Verify(
                fixture.Source.Layout, output.Layout, fixture.Identity, wrong)).Code);
    }

    [PrimaryFixtureFact]
    public void AppendsRgba8TextureAndUvsWithContentDeduplication()
    {
        using var fixture = new Fixture();
        string firstImage = Id(), secondImage = Id(), thirdImage = Id();
        byte[] pixels = Enumerable.Range(0, 3 * 5).SelectMany(index => new byte[]
        {
            (byte)(index * 13 + 1), (byte)(index * 7 + 2), (byte)(index * 3 + 4),
            (byte)(index % 3 == 0 ? 31 + index : 255)
        }).ToArray();
        var firstDefinition = Image(fixture, firstImage, 3, 5, pixels);
        var secondDefinition = Image(fixture, secondImage, 3, 5, pixels);
        byte[] distinctPixels = (byte[])pixels.Clone();
        distinctPixels[0] ^= 0x7F;
        var thirdDefinition = Image(fixture, thirdImage, 3, 5, distinctPixels);
        var firstMaterial = Material(Id(), firstImage) with { BaseColor = new(0.25f, 0.5f, 0.75f, 1) };
        var secondMaterial = Material(Id(), secondImage) with { BaseColor = firstMaterial.BaseColor };
        var thirdMaterial = Material(Id(), thirdImage) with { BaseColor = firstMaterial.BaseColor };
        Vector2Data[] uvs = [new(0.125f, 0.25f), new(1.5f, -0.25f), new(0.75f, 1.25f)];
        var edits = Batch(fixture.Target.Id,
            [Part(Id(), firstMaterial.Id, uvs), Part(Id(), secondMaterial.Id, uvs),
                Part(Id(), thirdMaterial.Id, uvs)],
            [firstMaterial, secondMaterial, thirdMaterial],
            [firstDefinition, secondDefinition, thirdDefinition]);
        var batch = ModelAdditionEditing.Validate(fixture.Directory, fixture.Source,
            fixture.Identity, edits, [fixture.Target.Id]);

        var write = ModelAdditionArchiveWriter.Write(fixture.Source.Layout, fixture.Identity, batch);
        var output = new StageArchive("textured.dat", write.Bytes);

        Assert.Empty(output.Validate());
        Assert.Equal(3, write.Chunks.Length);
        Assert.All(write.Chunks, chunk => Assert.Equal(uvs, chunk.Mesh.TexCoords0));
        Assert.Equal(write.Images[0].DescriptorOffset, write.Images[1].DescriptorOffset);
        Assert.Equal(write.Images[0].DataOffset, write.Images[1].DataOffset);
        Assert.Equal(write.Materials[0].MobjOffset, write.Materials[1].MobjOffset);
        Assert.NotEqual(write.Images[0].DescriptorOffset, write.Images[2].DescriptorOffset);
        Assert.NotEqual(write.Materials[0].MobjOffset, write.Materials[2].MobjOffset);
        Assert.Equal(3 * 5 * 4, write.Images[0].Pixels.Length);
        Assert.Equal(4 * 8 * 4, write.Images[0].EncodedLength);
    }

    [PrimaryFixtureFact]
    public void SplitsLargeGeometryWithoutDroppingNondegenerateTriangles()
    {
        using var fixture = new Fixture();
        var material = Material(Id(), null);
        int triangles = ModelAdditionArchiveWriter.MaxTrianglesPerChunk + 1;
        var part = Part(Id(), material.Id, null) with
        {
            TriangleIndices = Enumerable.Range(0, triangles)
                .SelectMany(_ => new[] { 0, 1, 2 }).ToArray(),
            CornerNormals = Enumerable.Repeat(new Vector3Data(0, 0, 1), triangles * 3).ToArray()
        };
        var edits = Batch(fixture.Target.Id, [part], [material], []);
        var batch = ModelAdditionEditing.Validate(fixture.Directory, fixture.Source,
            fixture.Identity, edits, [fixture.Target.Id]);

        var write = ModelAdditionArchiveWriter.Write(fixture.Source.Layout, fixture.Identity, batch);

        Assert.Equal(new[] { ModelAdditionArchiveWriter.MaxTrianglesPerChunk, 1 },
            write.Chunks.Select(chunk => chunk.TriangleCount).ToArray());
        Assert.Equal(triangles, write.Chunks.Sum(chunk => chunk.TriangleCount));
        Assert.Empty(new StageArchive("large-added.dat", write.Bytes).Validate());
    }

    [PrimaryFixtureFact]
    public void CreatesIndependentLitJobjChainUnderModelGroupRoot()
    {
        using var fixture = new Fixture();
        var target = fixture.NewChainTarget;
        var material = Material(Id(), null) with
        {
            BaseColor = new(0.25f, 0.5f, 0.75f, 1),
            Preset = ModelAdditionEditing.DiffuseMaterialPreset
        };
        var edits = Batch(target.Id, [Part(Id(), material.Id, null)], [material], [],
            ModelAddition.NewJobjChainPlacement);
        var batch = ModelAdditionEditing.Validate(fixture.Directory, fixture.Source,
            fixture.Identity, edits, [target.Id]);

        var write = ModelAdditionArchiveWriter.Write(fixture.Source.Layout, fixture.Identity, batch);
        var output = new StageArchive("new-jobj-added.dat", write.Bytes);
        var catalog = ModelIdentityCatalog.Restore(fixture.Identity.Nodes);
        var extended = ModelIdentity.Capture(output.Layout, catalog);
        var jobj = Assert.Single(write.Jobjs);
        var chunk = Assert.Single(write.Chunks);
        var addedJobj = extended.Nodes.Single(node => node.Kind == "jobj"
            && node.SourceOffset == jobj.JobjOffset);

        Assert.Empty(output.Validate());
        Assert.Equal(fixture.Identity.Nodes.Count + 3, extended.Nodes.Count);
        Assert.Equal(target.AnchorJobjId, jobj.AnchorJobjId);
        Assert.Equal(target.AnchorJobjId, addedJobj.OwnerId);
        Assert.Equal(addedJobj.Id, extended.Nodes.Single(node => node.Kind == "dobj"
            && node.SourceOffset == chunk.DobjOffset).OwnerId);
        Assert.Equal(ModelAddition.NewJobjChainPlacement, chunk.Placement);
        Assert.Equal(ModelAdditionEditing.DiffuseMaterialPreset,
            Assert.Single(write.Materials).Preset);
    }

    [GrGdFixtureFact]
    public void SelectsAndWritesProgrammaticAttachmentForGrGd()
    {
        using var fixture = new Fixture("GrGd.dat");
        var material = Material(Id(), null);
        var edits = Batch(fixture.Target.Id, [Part(Id(), material.Id, null)], [material], []);
        var batch = ModelAdditionEditing.Validate(fixture.Directory, fixture.Source,
            fixture.Identity, edits, [fixture.Target.Id]);

        var write = ModelAdditionArchiveWriter.Write(fixture.Source.Layout, fixture.Identity, batch);
        var output = new StageArchive("grgd-added.dat", write.Bytes);
        var catalog = ModelIdentityCatalog.Restore(fixture.Identity.Nodes);
        var extended = ModelIdentity.Capture(output.Layout, catalog);

        Assert.Empty(output.Validate());
        Assert.Equal(fixture.Identity.Nodes.Count + 2, extended.Nodes.Count);
        Assert.Equal(fixture.Target.Id, Assert.Single(write.Chunks).TargetId);
    }

    private static ModelAdditionEdits Batch(string target, ModelAdditionPart[] parts,
        ModelAdditionMaterial[] materials, ModelAdditionImage[] images,
        string placement = ModelAddition.ExistingJobjPlacement) => new(
        SessionExtractor.ProtocolVersion, ModelAddition.SchemaVersion, "game-joint-local",
        [new(Id(), "Test addition", placement, target, parts)], materials, images);

    private static ModelAdditionPart Part(string id, string material, Vector2Data[]? uvs) => new(
        id, material, [new(0, 0, 0), new(1, 0, 0), new(0, 1, 0)], [0, 1, 2],
        [new(0, 0, 1), new(0, 0, 1), new(0, 0, 1)], uvs);

    private static ModelAdditionMaterial Material(string id, string? image) => new(id, "Material",
        new(1, 1, 1, 1), image, "repeat", "clamp", "linear", "nearest",
        ModelAdditionEditing.MaterialPreset);

    private static ModelAdditionImage Image(Fixture fixture, string id, int width, int height, byte[] pixels)
    {
        string relative = $"edits/addition-assets/{id}.rgba";
        Directory.CreateDirectory(Path.Combine(fixture.Directory, "edits/addition-assets"));
        File.WriteAllBytes(Path.Combine(fixture.Directory, relative), pixels);
        return new(id, width, height, ModelAdditionEditing.PixelEncoding, relative, Hash(pixels));
    }

    private static string Id() => Guid.NewGuid().ToString("N");
    private static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private sealed class Fixture : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "mme-addition-" + Id());
        public string Directory => Path.Combine(Root, "session");
        public StageArchive Source { get; }
        public ModelIdentitySnapshot Identity { get; }
        public ModelAdditionTarget Target { get; }
        public ModelAdditionTarget NewChainTarget { get; }

        public Fixture(string filename = "GrNLa.dat")
        {
            System.IO.Directory.CreateDirectory(Root);
            Source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, filename));
            var catalog = new ModelIdentityCatalog();
            Identity = ModelIdentity.Capture(Source.Layout, catalog);
            var targets = ModelAddition.Select(Source, Identity, out var reasons);
            Assert.NotEmpty(targets);
            Target = targets.FirstOrDefault(target => target.Placement == ModelAddition.ExistingJobjPlacement
                && !target.HiddenAtRest && !target.ExistingMaterialAnimation)
                ?? targets.First(target => target.Placement == ModelAddition.ExistingJobjPlacement);
            NewChainTarget = targets.FirstOrDefault(target => target.Placement == ModelAddition.NewJobjChainPlacement)
                ?? throw new InvalidOperationException(string.Join(" | ", reasons.Values.Distinct()));
            System.IO.Directory.CreateDirectory(Path.Combine(Directory, "edits"));
        }

        public void Dispose() => System.IO.Directory.Delete(Root, true);
    }
}
