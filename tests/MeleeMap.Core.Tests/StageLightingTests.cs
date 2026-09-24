using MeleeMap.Core;
using MeleeMap.Core.Gx;
using System.Text.Json;
using Xunit;

namespace MeleeMap.Core.Tests;

public class StageLightingTests
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
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

    [PrimaryFixtureFact]
    public void AppliesStaticAmbientInfinitePointAndSpotEditsWithoutChangingSourceWobjs()
    {
        string root = Path.Combine(Path.GetTempPath(), "mme-light-edit-" + Guid.NewGuid().ToString("N"));
        string session = Path.Combine(root, "session"), output = Path.Combine(root, "out.dat");
        try
        {
            var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNSr.dat"));
            SessionExtractor.Extract(source, session);
            var before = StageLightingReader.Read(source.Layout);
            StageLight Pick(string type) => before.LightSets.SelectMany(set => set.Lights).First(light => light.Type == type);
            var ambient = Pick("ambient"); var infinite = Pick("infinite");
            var point = Pick("point"); var spot = Pick("spot");
            var edits = new StageLightEdits(SessionExtractor.ProtocolVersion, [
                new(ambient.Id, false, [12, 34, 56]),
                new(infinite.Id, Position: new(1.25f, -2.5f, 3.75f)),
                new(point.Id, Position: new(10, 20, 30)),
                new(spot.Id, true, [90, 80, 70], new(-4, 5, 6), new(7, 8, 9))]);
            File.WriteAllText(Path.Combine(session, "edits/lights.json"), JsonSerializer.Serialize(edits, Json));
            using (var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(session, "stage.json"))))
            {
                string material = manifest.RootElement.GetProperty("editableMaterialProperties").EnumerateArray()
                    .First(entry => entry.GetProperty("canEditDiffuse").GetBoolean()).GetProperty("id").GetString()!;
                File.WriteAllText(Path.Combine(session, "edits/materials.json"),
                    JsonSerializer.Serialize(new MaterialPropertyEdits(SessionExtractor.ProtocolVersion, [new(material, [11, 22, 33])]), Json));
            }
            var result = SessionApplier.Apply(session, output);
            Assert.True(result.LightChanged); Assert.False(result.CollisionChanged);
            Assert.False(result.ModelChanged); Assert.True(result.MaterialChanged);
            var writtenArchive = new StageArchive(output);
            var written = StageLightingReader.Read(writtenArchive.Layout).LightSets.SelectMany(set => set.Lights).ToDictionary(light => light.Id);
            Assert.True(written[ambient.Id].Hidden);
            Assert.Equal(new[] { 12 / 255f, 34 / 255f, 56 / 255f }, written[ambient.Id].Color.Take(3));
            Assert.Equal(new Vector3Data(1.25f, -2.5f, 3.75f), written[infinite.Id].Position);
            Assert.Equal(new Vector3Data(10, 20, 30), written[point.Id].Position);
            Assert.Equal(new Vector3Data(-4, 5, 6), written[spot.Id].Position);
            Assert.Equal(new Vector3Data(7, 8, 9), written[spot.Id].Interest);
            var sourceReader = new ArchiveDataReader(source.Layout);
            foreach (var light in new[] { infinite, point, spot })
            {
                int original = sourceReader.Pointer(light.SourceOffset + 0x10)!.Value;
                Assert.Equal(source.Layout.Bytes.AsSpan(32 + original, 16).ToArray(),
                    writtenArchive.Layout.Bytes.AsSpan(32 + original, 16).ToArray());
            }
        }
        finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
    }

    [PrimaryFixtureFact]
    public void RejectsMalformedOrUnsupportedLightEdits()
    {
        var source = new StageArchive(Path.Combine(CorpusTests.CorpusDirectory, "GrNSr.dat"));
        var lights = StageLightingReader.Read(source.Layout).LightSets.SelectMany(set => set.Lights).ToArray();
        var ambient = lights.First(light => light.Type == "ambient");
        string[] declared = lights.Select(light => light.Id).ToArray();
        void Reject(StageLightEdits edits, string code) => Assert.Equal(code,
            Assert.Throws<StageException>(() => StageLightEditing.Write(source.Layout, source.Layout, edits, declared)).Code);
        Reject(new(SessionExtractor.ProtocolVersion, []), "LIGHT_EDIT_FORMAT");
        Reject(new(SessionExtractor.ProtocolVersion, [new("missing", true)]), "LIGHT_EDIT_TARGET");
        Reject(new(SessionExtractor.ProtocolVersion, [new(ambient.Id, Color: [0, 1])]), "LIGHT_COLOR");
        Reject(new(SessionExtractor.ProtocolVersion, [new(ambient.Id, Position: new(1, 2, 3))]), "LIGHT_POSITION");
        Reject(new(SessionExtractor.ProtocolVersion, [new(ambient.Id)]), "LIGHT_EDIT_FORMAT");
        Reject(new(SessionExtractor.ProtocolVersion, [new(ambient.Id, Color: [0, 1, 256])]), "LIGHT_COLOR");
    }
}
