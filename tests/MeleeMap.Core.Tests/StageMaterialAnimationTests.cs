using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class StageMaterialAnimationTests
{
    [CorpusFact]
    public void EveryStageMaterialAnimationTreeMatchesItsModelIdentityGraph()
    {
        var failures = new List<string>();
        foreach (string path in Directory.GetFiles(CorpusTests.CorpusDirectory, "*.dat"))
        {
            try
            {
                var stage = new StageArchive(path);
                var identity = ModelIdentity.Capture(stage.Layout, new());
                foreach (int group in identity.Nodes.Where(node => node.Kind is "group" or "sentinel-group")
                             .Select(node => node.GroupIndex))
                    StageMaterialAnimations.Read(stage, identity, group);
            }
            catch (Exception exception)
            {
                failures.Add($"{Path.GetFileName(path)}: {exception.Message}");
            }
        }
        Assert.True(failures.Count == 0, string.Join(Environment.NewLine, failures));
    }

    [GrGbFixtureFact]
    public void ExtractsTextureTracksWithSlotsAndMaterialIdentity()
    {
        var stage = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrGb.dat"));
        var identity = ModelIdentity.Capture(stage.Layout, new());
        var sets = Enumerable.Range(0, 10)
            .SelectMany(group => StageMaterialAnimations.Read(stage, identity, group)).ToArray();

        Assert.Equal(5, sets.Length);
        Assert.Contains(sets.SelectMany(set => set.Materials).SelectMany(material => material.Textures)
            .SelectMany(texture => texture.Tracks), track => track.Channel == "image");
        Assert.Contains(sets.SelectMany(set => set.Materials).SelectMany(material => material.Textures)
            .SelectMany(texture => texture.Tracks), track => track.Channel == "translation.x");
        var ids = identity.Nodes.Where(node => node.Kind == "pobj").Select(node => node.Id).ToHashSet();
        Assert.All(sets.SelectMany(set => set.Materials), material => Assert.Contains(material.MaterialId, ids));
        Assert.All(sets.SelectMany(set => set.Materials), material =>
        {
            Assert.True(float.IsFinite(material.MaterialEndFrame));
            Assert.True(material.EndFrame >= material.MaterialEndFrame);
        });
        Assert.All(sets.SelectMany(set => set.Materials).SelectMany(material => material.Textures), texture =>
        {
            Assert.InRange(texture.TextureIndex, 0, 7);
            Assert.All(texture.Tracks.SelectMany(track => track.Keys), key =>
            {
                Assert.True(float.IsFinite(key.Frame));
                Assert.True(float.IsFinite(key.Value));
                Assert.True(float.IsFinite(key.Tangent));
            });
        });
    }

    [CorpusFact]
    public void ExtractsReachableImageAndPaletteCombinations()
    {
        string source = Path.Combine(CorpusTests.CorpusDirectory, "GrPs.dat");
        if (!File.Exists(source)) return;
        string directory = Path.Combine(Path.GetTempPath(), "meleemap-texture-animation-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        try
        {
            var stage = new StageArchive(source);
            var identity = ModelIdentity.Capture(stage.Layout, new());
            var textures = Enumerable.Range(0, 64)
                .SelectMany(group => StageMaterialAnimations.Read(stage, identity, group, directory))
                .SelectMany(set => set.Materials).SelectMany(material => material.Textures).ToArray();
            var palette = textures.FirstOrDefault(texture =>
                texture.Tracks.Any(track => track.Channel == "palette")
                && texture.Images.Select(image => (image.ImageIndex, image.PaletteIndex))
                    .SequenceEqual([(0, 0), (1, 1)]));
            Assert.NotNull(palette);
            Assert.Null(palette!.ImageWarning);
            Assert.All(palette.Images, image =>
            {
                Assert.True(image.Width > 0 && image.Height > 0);
                Assert.True(File.Exists(Path.Combine(directory, image.File)));
            });
        }
        finally { Directory.Delete(directory, recursive: true); }
    }
}
