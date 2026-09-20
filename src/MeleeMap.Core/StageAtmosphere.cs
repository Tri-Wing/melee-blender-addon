using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record StageFog(string Id, int GroupIndex, int Type, float Start, float End,
    float[] Color, int SourceOffset);
public sealed record StageAtmosphere(string? PreviewFogId, float[] BackgroundColor,
    StageFog[] Fogs, string? Warning);

/// <summary>Reads the group fog descriptors that Melee also uses for the camera clear color.</summary>
public static class StageAtmosphereReader
{
    private static readonly HashSet<int> FogTypes = [0, 2, 4, 5, 6, 7, 0xA, 0xC, 0xD, 0xE, 0xF];

    public static StageAtmosphere Read(ArchiveLayout archive, string? previewLightSetId = null)
    {
        var reader = new ArchiveDataReader(archive);
        var map = archive.Roots.SingleOrDefault(root => root.Name == "map_head");
        Require(map != null, "STAGE_ROOT_MISSING", "Required stage root 'map_head' is missing.");
        reader.Check(map!.Offset, 0x10);
        int groupCount = reader.Int(map.Offset + 0x0C);
        int groups = reader.Array(map.Offset + 8, groupCount, 0x34);
        var fogs = new List<StageFog>();
        for (int group = 0; group < groupCount; group++)
        {
            int descriptor = groups + group * 0x34;
            if (!archive.Pointers.ContainsKey(descriptor) && reader.Int(descriptor) == -1)
                continue;
            int? offset = reader.Pointer(descriptor + 0x1C);
            if (!offset.HasValue) continue;
            reader.Check(offset.Value, 0x14);
            int type = reader.Int(offset.Value);
            float start = reader.Float(offset.Value + 8), end = reader.Float(offset.Value + 0x0C);
            Require(FogTypes.Contains(type), "FOG_TYPE", $"Fog at 0x{offset.Value:X} has an invalid type.");
            Require(float.IsFinite(start) && float.IsFinite(end), "FOG_NONFINITE",
                $"Fog at 0x{offset.Value:X} contains a non-finite distance.");
            float[] color = Enumerable.Range(0, 4)
                .Select(index => reader.Byte(offset.Value + 0x10 + index) / 255f).ToArray();
            fogs.Add(new($"group-{group:D3}-fog", group, type, start, end, color, offset.Value));
        }

        int? pairedGroup = null;
        if (previewLightSetId?.StartsWith("group-", StringComparison.Ordinal) == true
            && int.TryParse(previewLightSetId.AsSpan(6), out int parsed))
            pairedGroup = parsed;
        StageFog? preview = fogs.FirstOrDefault(fog => fog.GroupIndex == pairedGroup)
            ?? fogs.FirstOrDefault();
        int distinct = fogs.Select(fog => $"{fog.Type}:{BitConverter.SingleToInt32Bits(fog.Start)}:" +
            $"{BitConverter.SingleToInt32Bits(fog.End)}:{string.Join(':', fog.Color.Select(BitConverter.SingleToInt32Bits))}")
            .Distinct().Count();
        string? warning = distinct > 1
            ? preview?.GroupIndex == pairedGroup
                ? "Stage code can select among multiple fog descriptors. The static preview uses the fog paired with the selected light set."
                : "Stage code can select among multiple fog descriptors. The static preview uses the first available fog."
            : null;
        return new(preview?.Id, preview?.Color ?? [0, 0, 0, 1], fogs.ToArray(), warning);
    }
}
