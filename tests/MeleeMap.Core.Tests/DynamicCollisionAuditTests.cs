using MeleeMap.Core;
using System.Text.Json;
using Xunit;

namespace MeleeMap.Core.Tests;

public class DynamicCollisionAuditTests
{
    [CorpusFact]
    public void RepresentativeStagesKeepDistinctDynamicAndAttachmentPatterns()
    {
        var expected = new Dictionary<string, (int Joints, int Dynamic, int Attachments)>
        {
            ["GrNLa.dat"] = (1, 0, 0),
            ["GrFs.dat"] = (5, 0, 0),
            ["GrGb.dat"] = (8, 6, 0),
            ["GrPs.dat"] = (8, 24, 1),
            ["GrMc.dat"] = (9, 5, 5),
            ["GrBb.dat"] = (67, 0, 41),
            ["GrRc.dat"] = (45, 11, 74),
        };

        int checkedStages = 0;
        foreach (var (filename, counts) in expected)
        {
            string path = Path.Combine(CorpusTests.CorpusDirectory, filename);
            if (!File.Exists(path))
                continue;
            var collision = CollisionData.Read(new StageArchive(path).Layout);
            Assert.Equal(counts.Joints, collision.Joints.Length);
            Assert.Equal(counts.Dynamic, collision.Ranges[4].Count);
            Assert.Equal(counts.Attachments, collision.Attachments.Length);
            checkedStages++;
        }
        Assert.True(checkedStages > 0,
            "The corpus contains none of the representative dynamic-collision stages.");
    }

    [CorpusFact]
    public void RepresentativeSerializedAttachmentTargetsRemainClassifiable()
    {
        string stadium = Path.Combine(CorpusTests.CorpusDirectory, "GrPs.dat");
        string cruise = Path.Combine(CorpusTests.CorpusDirectory, "GrRc.dat");
        if (!File.Exists(stadium) || !File.Exists(cruise))
            return;

        var stadiumStage = new StageArchive(stadium);
        var stadiumModels = ModelIdentity.Capture(stadiumStage.Layout,
            new ModelIdentityCatalog());
        var stadiumCollision = CollisionData.Read(stadiumStage.Layout);
        Assert.Contains(stadiumCollision.Validate(stadiumModels), issue =>
            issue.Code == "COLLISION_ATTACHMENT_EXTERNAL");

        var cruiseCollision = CollisionData.Read(new StageArchive(cruise).Layout);
        Assert.Contains(cruiseCollision.Attachments.GroupBy(link => link.JointIndex),
            links => links.Select(link => link.GroupIndex).Distinct().Count() > 1);
        Assert.All(cruiseCollision.Attachments, link =>
            Assert.Equal(-1, link.RawMiddleIndex));
    }

    [CorpusFact]
    public void SessionPublishesMovingCollisionPreviewCapabilities()
    {
        string source = Path.Combine(CorpusTests.CorpusDirectory, "GrMc.dat");
        if (!File.Exists(source))
            return;
        string session = Path.Combine(Path.GetTempPath(),
            "mme-dynamic-preview-" + Guid.NewGuid().ToString("N"));
        try
        {
            SessionExtractor.Extract(new StageArchive(source), session);
            using var document = JsonDocument.Parse(File.ReadAllText(
                Path.Combine(session, "stage.json")));
            var root = document.RootElement;
            Assert.True(root.GetProperty("capabilities")
                .GetProperty("dynamicCollisionPreview").GetBoolean());
            Assert.True(root.GetProperty("capabilities")
                .GetProperty("collisionEdit").GetBoolean());
            Assert.True(root.GetProperty("capabilities")
                .GetProperty("dynamicCollisionEdit").GetBoolean());
            Assert.True(root.GetProperty("capabilities")
                .GetProperty("collisionGeometryEdit").GetBoolean());
            Assert.False(root.GetProperty("capabilities")
                .GetProperty("collisionAttachmentEdit").GetBoolean());
            var summary = root.GetProperty("collisionBindingSummary");
            Assert.Equal(5, summary.GetProperty("dynamicLines").GetInt32());
            Assert.Equal(5, summary.GetProperty("serializedAttachments").GetInt32());
            Assert.Equal(5, summary.GetProperty("locallyResolvedAttachments").GetInt32());
            Assert.Equal(5, summary.GetProperty("oneToOnePreviewJoints").GetInt32());
            Assert.Equal(0, summary.GetProperty("externalAttachments").GetInt32());
            Assert.False(summary.GetProperty("stageCodeBindingsDiscoverable")
                .GetBoolean());
        }
        finally
        {
            if (Directory.Exists(session))
                Directory.Delete(session, true);
        }
    }
}
