using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public sealed class GrGbFixtureFactAttribute : FactAttribute
{
    public GrGbFixtureFactAttribute()
    {
        if (!File.Exists(Path.Combine(CorpusTests.CorpusDirectory, "GrGb.dat")))
            Skip = "GrGb.dat is required for the joint-animation integration test.";
    }
}

public class StageJointAnimationTests
{
    [CorpusFact]
    public void EveryStageJointAnimationTreeMatchesItsJobjIdentityGraph()
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
                    StageJointAnimations.Read(stage, identity, group);
            }
            catch (Exception exception)
            {
                failures.Add($"{Path.GetFileName(path)}: {exception.Message}");
            }
        }
        Assert.True(failures.Count == 0, string.Join(Environment.NewLine, failures));
    }

    [GrGbFixtureFact]
    public void ExtractsEveryTransformTrackWithItsAnimationSlot()
    {
        var stage = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrGb.dat"));
        var identity = ModelIdentity.Capture(stage.Layout, new());
        var sets = Enumerable.Range(0, 10)
            .SelectMany(group => StageJointAnimations.Read(stage, identity, group))
            .ToArray();

        Assert.Equal(11, sets.Length);
        var multi = Enumerable.Range(0, 10)
            .Select(group => StageJointAnimations.Read(stage, identity, group))
            .Single(items => items.Length == 3);
        Assert.Equal(new[] { 0, 1, 2 }, multi.Select(item => item.Slot));
        Assert.Equal(new[] { 386f, 600f, 300f }, multi.Select(item => item.EndFrame));
        Assert.Contains(sets, item => item.EndFrame == 4690f);

        var ids = identity.Nodes.Where(node => node.Kind.EndsWith("jobj"))
            .Select(node => node.Id).ToHashSet();
        var channels = new HashSet<string>
        {
            "rotation.x", "rotation.y", "rotation.z", "translation.x",
            "translation.y", "translation.z", "scale.x", "scale.y", "scale.z"
        };
        Assert.All(sets.SelectMany(item => item.Nodes), node =>
        {
            Assert.Contains(node.JobjId, ids);
            Assert.All(node.Tracks, track =>
            {
                Assert.Contains(track.Channel, channels);
                Assert.NotEmpty(track.Keys);
                Assert.All(track.Keys, key =>
                {
                    Assert.True(float.IsFinite(key.Frame));
                    Assert.True(float.IsFinite(key.Value));
                    Assert.True(float.IsFinite(key.Tangent));
                });
                Assert.True(track.Keys.Zip(track.Keys.Skip(1))
                    .All(pair => pair.First.Frame <= pair.Second.Frame));
            });
        });
    }

    [PrimaryFixtureFact]
    public void AppliesTheMapGroupRuntimeLoopFlag()
    {
        var stage = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
        var identity = ModelIdentity.Capture(stage.Layout, new());
        var animation = Assert.Single(StageJointAnimations.Read(stage, identity, 3));

        Assert.Equal(0, animation.Slot);
        Assert.NotEqual(0, animation.GroupFlag);
        Assert.True(animation.Loop);
        Assert.All(animation.Nodes, node => Assert.True(node.Loop));
        Assert.All(animation.Nodes, node => Assert.Equal(0u, node.Flags));
    }
}
