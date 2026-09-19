using System.Text.Json;
using Xunit;

public class CliTests
{
    public static IEnumerable<object[]> InvalidArguments => new[]
    {
        Array.Empty<string>(), new[] { "extract", "source.dat" },
        new[] { "roundtrip", "source.dat", "--output" },
        new[] { "inspect", "source.dat", "--unknown" }
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
}
