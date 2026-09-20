using System.Text.Json;
using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class JointAnimationEditingTests
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

    [GrGbFixtureFact]
    public void ReplacesExistingJobjTracksAndReloadsThem()
    {
        var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrGb.dat"));
        var identity = ModelIdentity.Capture(source.Layout, new());
        var target = Enumerable.Range(0, source.Inspect().ModelGroups!.Value)
            .SelectMany(group => StageJointAnimations.Read(source, identity, group)
                .SelectMany(set => set.Nodes.Where(node => node.Editable)
                    .Select(node => (Group: group, set.Slot, Node: node))))
            .First(item => item.Node.EndFrame >= 2);
        var track = new JointAnimationTrack("translation.x",
        [
            new(0, 12.5f, 0, "HSD_A_OP_LIN"),
            new(1, 18.25f, 0, "HSD_A_OP_LIN"),
            new(2, -4.75f, 0, "HSD_A_OP_LIN")
        ]);
        var edit = new JointAnimationNodeEdit(target.Group, target.Slot, target.Node.JobjId, [track]);
        string declared = JointAnimationEditing.TargetKey(target.Group, target.Slot, target.Node.JobjId);

        var written = JointAnimationEditing.Write(source.Layout, source.Layout, identity,
            new(SessionExtractor.ProtocolVersion, "game-jobj-animation", [edit]), [declared]);
        var reloaded = new StageArchive("edited.dat", written.Bytes);
        var actual = StageJointAnimations.Read(reloaded, identity, target.Group)
            .Single(set => set.Slot == target.Slot).Nodes.Single(node => node.JobjId == target.Node.JobjId);

        var actualTrack = Assert.Single(actual.Tracks);
        Assert.Equal("translation.x", actualTrack.Channel);
        Assert.Equal(new[] { 12.5f, 18.25f, -4.75f }, actualTrack.Keys.Select(key => key.Value),
            new FloatComparer(.002f));
        Assert.True(written.Bytes.Length > source.Layout.Bytes.Length);
    }

    [GrGbFixtureFact]
    public void SessionApplyAcceptsDeclaredAnimationEditsAndRejectsFractionalFrames()
    {
        using var temp = new TemporaryDirectory();
        string session = Path.Combine(temp.Path, "session");
        string sourcePath = Path.Combine(CorpusTests.CorpusDirectory, "GrGb.dat");
        var source = new StageArchive(sourcePath);
        SessionExtractor.Extract(source, session);
        using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(session, "stage.json")));
        var target = manifest.RootElement.GetProperty("editableJointAnimations")[0];
        int group = target.GetProperty("groupIndex").GetInt32();
        int slot = target.GetProperty("slot").GetInt32();
        string id = target.GetProperty("jobjId").GetString()!;
        string groupPath = Path.Combine(session, $"models/group-{group:D3}/group.json");
        using var groupDocument = JsonDocument.Parse(File.ReadAllText(groupPath));
        float end = groupDocument.RootElement.GetProperty("jointAnimations").EnumerateArray()
            .Single(set => set.GetProperty("slot").GetInt32() == slot).GetProperty("nodes").EnumerateArray()
            .Single(node => node.GetProperty("jobjId").GetString() == id).GetProperty("endFrame").GetSingle();
        var valid = new JointAnimationEdits(SessionExtractor.ProtocolVersion, "game-jobj-animation",
        [
            new(group, slot, id,
            [
                new("scale.x",
                [
                    new(0, 1, 0, "HSD_A_OP_LIN"),
                    new(MathF.Min(1, end), 1.25f, 0, "HSD_A_OP_LIN")
                ])
            ])
        ]);
        string editPath = Path.Combine(session, "edits/animations.json");
        File.WriteAllText(editPath, JsonSerializer.Serialize(valid, Json));
        string output = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".dat");
        try
        {
            var result = SessionApplier.Apply(session, output);
            Assert.True(result.AnimationChanged);
            valid.Nodes[0].Tracks[0].Keys[1] = valid.Nodes[0].Tracks[0].Keys[1] with { Frame = .5f };
            File.WriteAllText(editPath, JsonSerializer.Serialize(valid, Json));
            Assert.Equal("ANIMATION_EDIT_KEY",
                Assert.Throws<StageException>(() => SessionApplier.Apply(session, output)).Code);
        }
        finally { if (File.Exists(output)) File.Delete(output); }
    }

    private sealed class FloatComparer(float tolerance) : IEqualityComparer<float>
    {
        public bool Equals(float x, float y) => MathF.Abs(x - y) <= tolerance;
        public int GetHashCode(float obj) => 0;
    }

    private sealed class TemporaryDirectory : IDisposable
    {
        public string Path { get; } = System.IO.Path.Combine(System.IO.Path.GetTempPath(),
            "mme-animation-edit-" + Guid.NewGuid().ToString("N"));
        public TemporaryDirectory() => Directory.CreateDirectory(Path);
        public void Dispose() => Directory.Delete(Path, recursive: true);
    }
}
