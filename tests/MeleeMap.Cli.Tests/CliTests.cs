using System.Text.Json;
using Xunit;

public class CliTests
{
    public static IEnumerable<object[]> InvalidArguments => new[]
    {
        Array.Empty<string>(), new[] { "extract", "source.dat" },
        new[] { "roundtrip", "source.dat", "--output" },
        new[] { "inspect", "source.dat", "--unknown" },
        new[] { "extract", "source.dat", "--session" },
        new[] { "extract", "source.dat", "--session", "one", "--session", "two" },
        new[] { "inspect", "source.dat", "--session", "one" },
        new[] { "apply", "session" }, new[] { "apply", "session", "--output" },
        new[] { "apply", "session", "--output", "new.dat", "--compare" }
    }.Select(args => new object[] { args });

    [Theory]
    [MemberData(nameof(InvalidArguments))]
    public void UsageErrorsAreMachineReadable(string[] args)
    {
        using var output = new StringWriter();
        using var error = new StringWriter();
        Assert.Equal(1, Cli.Run(args, output, error));
        using var json = JsonDocument.Parse(output.ToString());
        Assert.False(json.RootElement.GetProperty("ok").GetBoolean());
        Assert.Equal("USAGE", json.RootElement.GetProperty("error").GetProperty("code").GetString());
        Assert.Contains("USAGE", error.ToString());
    }

    [Fact]
    public void MissingInputReportsIoError()
    {
        using var output = new StringWriter();
        Assert.Equal(1, Cli.Run(["inspect", Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".dat")], output, TextWriter.Null));
        Assert.Contains("IO_ERROR", output.ToString());
    }

    [Fact]
    public void ApplyIsByteIdenticalRepeatableAndFailurePreservingThroughCli()
    {
        string source = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,
            "../../../../../example_assets/GrNLa.dat"));
        Assert.True(File.Exists(source), "The primary CLI fixture is missing.");
        string root = Path.Combine(Path.GetTempPath(), "mme-cli-transaction-"
            + Guid.NewGuid().ToString("N"));
        string session = Path.Combine(root, "session");
        string output = Path.Combine(root, "out.dat");
        Directory.CreateDirectory(root);
        try
        {
            Assert.Equal(0, Run("extract", source, "--session", session));
            Assert.Equal(0, Run("apply", session, "--output", output));
            byte[] expected = File.ReadAllBytes(source);
            Assert.Equal(expected, File.ReadAllBytes(output));

            File.WriteAllText(output, "replace this output");
            Assert.Equal(0, Run("apply", session, "--output", output));
            Assert.Equal(expected, File.ReadAllBytes(output));

            File.WriteAllText(Path.Combine(session, "edits/models.json"), "{}");
            Assert.Equal(1, Run("apply", session, "--output", output));
            Assert.Equal(expected, File.ReadAllBytes(output));
            Assert.Empty(Directory.GetFiles(root, "*.tmp"));
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }

        static int Run(params string[] args) =>
            Cli.Run(args, TextWriter.Null, TextWriter.Null);
    }
}
