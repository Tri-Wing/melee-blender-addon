using System.Text.Json;
using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public class StageCameraTests
{
    [PrimaryFixtureFact]
    public void ReadsGroundParamCameraAndPublishesItInTheSession()
    {
        string source = Path.Combine(CorpusTests.CorpusDirectory, "GrNLa.dat");
        var stage = new StageArchive(source);
        var camera = StageCameraReader.Read(stage.Layout);
        Assert.False(camera.FixedCamera);
        Assert.True(camera.RuntimeTracksSubjects);
        Assert.Equal(0, camera.Position.X);
        Assert.Equal(45, camera.Position.Y);
        Assert.Equal(356, camera.Position.Z);
        Assert.Equal(30, camera.FieldOfViewDegrees);
        Assert.Equal(-2, camera.VerticalAngleDegrees);
        Assert.Equal(0, camera.HorizontalAngleDegrees);
        Assert.Equal(0, camera.Interest.Z);
        Assert.InRange(camera.Interest.Y, 32.55f, 32.57f);

        string session = Path.Combine(Path.GetTempPath(), "meleemap-camera-test-" + Guid.NewGuid().ToString("N"));
        try
        {
            SessionExtractor.Extract(stage, session);
            using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(session, "stage.json")));
            var manifest = document.RootElement.GetProperty("camera");
            Assert.Equal(356, manifest.GetProperty("position").GetProperty("z").GetSingle());
            Assert.Equal(30, manifest.GetProperty("fieldOfViewDegrees").GetSingle());
            Assert.True(manifest.GetProperty("runtimeTracksSubjects").GetBoolean());
        }
        finally
        {
            if (Directory.Exists(session)) Directory.Delete(session, recursive: true);
        }
    }
}
