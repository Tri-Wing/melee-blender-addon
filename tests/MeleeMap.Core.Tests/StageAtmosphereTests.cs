using System.Text.Json;
using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class StageAtmosphereTests
{
    [GrGbFixtureFact]
    public void ReadsFogBackgroundAndPublishesItInTheSession()
    {
        string source = Path.Combine(CorpusTests.CorpusDirectory, "GrGb.dat");
        var stage = new StageArchive(source);
        var lighting = StageLightingReader.Read(stage.Layout);
        var atmosphere = StageAtmosphereReader.Read(stage.Layout, lighting.PreviewSetId);
        var preview = Assert.Single(atmosphere.Fogs.Where(fog => fog.Id == atmosphere.PreviewFogId));
        Assert.Equal(2, preview.Type);
        Assert.Equal(900, preview.Start);
        Assert.Equal(5000, preview.End);
        Assert.Equal(new[] { 202 / 255f, 240 / 255f, 1, 1 }, preview.Color);
        Assert.Equal(preview.Color, atmosphere.BackgroundColor);

        string session = Path.Combine(Path.GetTempPath(), "meleemap-atmosphere-test-" + Guid.NewGuid().ToString("N"));
        try
        {
            SessionExtractor.Extract(stage, session);
            using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(session, "stage.json")));
            var manifest = document.RootElement.GetProperty("atmosphere");
            Assert.Equal(preview.Id, manifest.GetProperty("previewFogId").GetString());
            Assert.Equal(202 / 255f, manifest.GetProperty("backgroundColor")[0].GetSingle());
        }
        finally
        {
            if (Directory.Exists(session)) Directory.Delete(session, recursive: true);
        }
    }
}
