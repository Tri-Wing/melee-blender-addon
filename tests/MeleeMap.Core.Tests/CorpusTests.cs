using MeleeMap.Core;
using Xunit;

namespace MeleeMap.Core.Tests;

public sealed class CorpusFactAttribute : FactAttribute
{
    public CorpusFactAttribute()
    {
        if (!Directory.Exists(CorpusTests.CorpusDirectory))
            Skip = "Local game corpus unavailable. Set MELEEMAP_CORPUS to a directory containing DAT files.";
    }
}

public class CorpusTests
{
    public static string CorpusDirectory => Environment.GetEnvironmentVariable("MELEEMAP_CORPUS")
        ?? Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../../example_assets"));

    [CorpusFact]
    public void EveryDatParsesValidatesAndPreservesGraph()
    {
        string[] files = Directory.GetFiles(CorpusDirectory, "*.dat");
        Assert.NotEmpty(files);
        string output = Path.Combine(Path.GetTempPath(), "meleemap-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(output);
        var failures = new List<string>();
        try
        {
            foreach (string file in files)
            {
                try
                {
                    var archive = new StageArchive(file);
                    var inventory = archive.Inspect();
                    Assert.NotEmpty(inventory.Roots);
                    archive.Validate();
                    StageLightingReader.Read(archive.Layout);
                    var identities = new ModelIdentityCatalog();
                    var before = ModelIdentity.Capture(archive.Layout, identities);
                    string saved = Path.Combine(output, Path.GetFileName(file));
                    archive.Roundtrip(saved, compare: true);
                    // No-edit preservation retains descriptor offsets; edited/relocated archives
                    // must provide an explicit catalog relocation mapping instead.
                    before.RequireUnchanged(ModelIdentity.Capture(new StageArchive(saved).Layout, identities));
                }
                catch (Exception e) { failures.Add($"{Path.GetFileName(file)}: {e.Message}"); }
            }
            Assert.True(failures.Count == 0, string.Join(Environment.NewLine, failures));
        }
        finally { Directory.Delete(output, recursive: true); }
    }

    [CorpusFact]
    public void PrimaryFixtureMatchesPlanAndCannotOverwriteSource()
    {
        string source = Path.Combine(CorpusDirectory, "GrNLa.dat");
        if (!File.Exists(source)) return; // Custom corpora need not contain the primary fixture.
        var archive = new StageArchive(source);
        var info = archive.Inspect();
        Assert.Equal(10, info.ModelGroups);
        Assert.Equal(new CollisionInventory(16, 16, 1, 0), info.Collision);
        Assert.Throws<StageException>(() => archive.Roundtrip(source, true));
        Assert.Equal(info.Sha256, new StageArchive(source).Inspect().Sha256);
    }
}
