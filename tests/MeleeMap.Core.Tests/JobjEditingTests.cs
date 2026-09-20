using System.Text.Json;
using MeleeMap.Core;
using MeleeMap.Core.Gx;
using Xunit;

namespace MeleeMap.Core.Tests;

public class JobjEditingTests
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

    [PrimaryFixtureFact]
    public void SelectsStaticOrdinaryJobjsAndWritesOnlyTheirSrtFields()
    {
        var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
        var catalog = new ModelIdentityCatalog();
        var identity = ModelIdentity.Capture(source.Layout, catalog);
        var eligible = JobjEditing.Select(source.Layout, identity, out var reasons);
        Assert.NotEmpty(eligible);
        Assert.Contains(reasons.Values, reason => reason.Contains("animation"));
        var target = eligible[0];
        var reader = new ArchiveDataReader(source.Layout);
        var edit = new JobjTransformEdit(target.Id,
            Read(target.SourceOffset + 0x14) with { Z = Read(target.SourceOffset + 0x14).Z + 0.125f },
            Read(target.SourceOffset + 0x20),
            Read(target.SourceOffset + 0x2C) with { X = Read(target.SourceOffset + 0x2C).X + 3.5f });
        var written = JobjEditing.Write(source.Layout, source.Layout, identity,
            new(2, "game-jobj-local", [edit]), [target.Id]);
        var output = new ArchiveLayout(written.Bytes);
        JobjEditing.Verify(output, identity, written);
        for (int i = 0; i < source.Layout.Bytes.Length; i++)
        {
            int dataOffset = i - 32;
            bool transform = dataOffset >= target.SourceOffset + 0x14
                && dataOffset < target.SourceOffset + 0x38;
            Assert.True(transform || source.Layout.Bytes[i] == written.Bytes[i],
                $"Unrelated byte changed at file offset 0x{i:X}.");
        }
        Vector3Data Read(int offset) => new(reader.Float(offset), reader.Float(offset + 4), reader.Float(offset + 8));
    }

    [PrimaryFixtureFact]
    public void ApplyPublishesDeclaredJobjTransformAndRejectsInvalidTargets()
    {
        string root = Path.Combine(Path.GetTempPath(), "mme-jobj-edit-" + Guid.NewGuid().ToString("N"));
        string session = Path.Combine(root, "session"), output = Path.Combine(root, "out.dat");
        try
        {
            var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat"));
            SessionExtractor.Extract(source, session);
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(session, "stage.json")));
            var target = manifest.RootElement.GetProperty("editableJobjs")[0];
            string id = target.GetProperty("id").GetString()!;
            int groupIndex = target.GetProperty("groupIndex").GetInt32();
            int jobjIndex = target.GetProperty("jobjIndex").GetInt32();
            using var group = JsonDocument.Parse(File.ReadAllText(Path.Combine(session, $"models/group-{groupIndex:D3}/group.json")));
            var joint = group.RootElement.GetProperty("joints").EnumerateArray()
                .Single(j => j.GetProperty("id").GetString() == id);
            Vector3Data Vec(string name) => joint.GetProperty(name).Deserialize<Vector3Data>(new JsonSerializerOptions
                { PropertyNameCaseInsensitive = true })!;
            var translation = Vec("translation") with { Y = Vec("translation").Y + 7 };
            var edit = new JobjTransformEdit(id, Vec("rotation"), Vec("scale"), translation);
            File.WriteAllText(Path.Combine(session, "edits/jobjs.json"),
                JsonSerializer.Serialize(new JobjTransformEdits(2, "game-jobj-local", [edit]), Json));
            var result = SessionApplier.Apply(session, output);
            Assert.True(result.JobjChanged);
            Assert.False(result.CollisionChanged || result.ModelChanged || result.MaterialChanged || result.LightChanged);
            var reloadedIdentity = ModelIdentity.Capture(new StageArchive(output).Layout, new ModelIdentityCatalog());
            var reloaded = reloadedIdentity.Nodes.Single(n => n.GroupIndex == groupIndex
                && n.Kind == "jobj" && n.Index == jobjIndex);
            Assert.Equal(translation.Y, new ArchiveDataReader(new StageArchive(output).Layout).Float(reloaded.SourceOffset + 0x30));

            File.WriteAllText(Path.Combine(session, "edits/jobjs.json"),
                JsonSerializer.Serialize(new JobjTransformEdits(2, "game-jobj-local", [edit with { Id = Guid.NewGuid().ToString("N") }]), Json));
            Assert.Equal("JOBJ_EDIT_TARGET", Assert.Throws<StageException>(() => SessionApplier.Apply(session, output)).Code);
            File.WriteAllText(Path.Combine(session, "edits/jobjs.json"),
                JsonSerializer.Serialize(new JobjTransformEdits(2, "game-jobj-local", [edit with { Scale = edit.Scale with { X = 0 } }]), Json));
            Assert.Equal("JOBJ_ZERO_SCALE", Assert.Throws<StageException>(() => SessionApplier.Apply(session, output)).Code);
        }
        finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
    }
}
