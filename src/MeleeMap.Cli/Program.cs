using System.Text.Json;
using MeleeMap.Core;

return Cli.Run(args, Console.Out, Console.Error);

public static class Cli
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, WriteIndented = true };

    public static int Run(string[] args, TextWriter stdout, TextWriter stderr)
    {
        try
        {
            if (args.Length < 2) throw Usage();
            string command = args[0];
            if (command is not ("inspect" or "validate" or "roundtrip" or "extract" or "apply")) throw Usage();
            string? output = null;
            string? session = null;
            bool compare = false;
            for (int i = 2; i < args.Length; i++)
            {
                if (args[i] == "--json") continue;
                if (command == "roundtrip" && args[i] == "--compare") { compare = true; continue; }
                if ((command is "roundtrip" or "apply") && args[i] == "--output" && ++i < args.Length && output == null) { output = args[i]; continue; }
                if (command == "extract" && args[i] == "--session" && ++i < args.Length && session == null) { session = args[i]; continue; }
                throw Usage();
            }
            if ((command is "roundtrip" or "apply") && output == null) throw Usage();
            if (command == "extract" && session == null) throw Usage();
            object data;
            if (command == "apply") data = SessionApplier.Apply(args[1], output!);
            else
            {
                var stage = new StageArchive(args[1]);
                if (command == "inspect") data = stage.Inspect();
                else if (command == "validate") { var warnings = stage.Validate(); data = new { valid = true, validationTier = "archive-model-hierarchy-and-collision-consistency", warnings }; }
                else if (command == "extract") data = SessionExtractor.Extract(stage, session!);
                else { stage.Roundtrip(output!, compare); data = new { output = Path.GetFullPath(output!), compared = compare }; }
            }
            stdout.WriteLine(JsonSerializer.Serialize(new { protocolVersion = 1, ok = true, command, data }, Json));
            return 0;
        }
        catch (Exception e)
        {
            string code = e is StageException known ? known.Code : e is IOException or UnauthorizedAccessException ? "IO_ERROR" : "PARSE_ERROR";
            stdout.WriteLine(JsonSerializer.Serialize(new { protocolVersion = 1, ok = false, error = new { code, message = e.Message } }, Json));
            stderr.WriteLine($"{code}: {e.Message}");
            return 1;
        }
    }

    private static StageException Usage() => new("USAGE", "Usage: meleemap inspect|validate <input.dat> [--json], meleemap roundtrip <input.dat> --output <new.dat> [--compare], meleemap extract <input.dat> --session <new-directory>, or meleemap apply <session-directory> --output <new.dat>");
}
