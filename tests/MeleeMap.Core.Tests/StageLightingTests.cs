using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class StageLightingTests
{
    [PrimaryFixtureFact]
    public void ExtractsStageAndPlayerLobjSetsAndSelectsAnimatedStageSet()
    {
        var archive = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
        var lighting = StageLightingReader.Read(archive.Layout);
        Assert.Equal("group-003", lighting.PreviewSetId);
        Assert.Equal(11, lighting.LightSets.Length);
        var preview = Assert.Single(lighting.LightSets.Where(set => set.Id == lighting.PreviewSetId));
        Assert.True(preview.Animated);
        Assert.Equal(3, preview.Lights.Length);
        var ambient = preview.Lights[0];
        Assert.Equal("ambient", ambient.Type);
        Assert.True(ambient.Diffuse);
        Assert.False(ambient.Specular);
        Assert.Equal(76 / 255f, ambient.Color[0]);
        Assert.Null(ambient.Position);
        Assert.All(preview.Lights.Skip(1), light =>
        {
            Assert.Equal("infinite", light.Type);
            Assert.True(light.Diffuse && light.Specular && light.Animated);
            Assert.NotNull(light.Position);
        });
        var player = Assert.Single(lighting.LightSets.Where(set => set.Id == "map-plit"));
        Assert.Equal("map_plit", player.Source);
        Assert.Equal(2, player.Lights.Length);
        Assert.Equal(179 / 255f, player.Lights[0].Color[0]);
        Assert.Equal(192 / 255f, player.Lights[1].Color[1]);
    }
}
