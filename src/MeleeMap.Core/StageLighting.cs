using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record LightAttenuation(float A0, float A1, float A2, float K0, float K1, float K2);
public sealed record LightSourceParameters(bool Raw, float? Cutoff = null, int? SpotFunction = null,
    float? ReferenceDistance = null, float? ReferenceBrightness = null, int? DistanceFunction = null);
public sealed record StageLight(string Id, string Type, int Flags, bool Diffuse, bool Specular, bool Hidden,
    float[] Color, Vector3Data? Position, Vector3Data? Interest, LightAttenuation Attenuation,
    LightSourceParameters Parameters, bool Animated, int SourceOffset);
public sealed record StageLightSet(string Id, string Source, int? GroupIndex, StageLight[] Lights, bool Animated);
public sealed record StageLighting(string? PreviewSetId, StageLightSet[] LightSets, string? Warning);

/// <summary>Read-only extraction of static LOBJ descriptors used by stage and fighter lighting.</summary>
public static class StageLightingReader
{
    private const int Ambient = 0, Infinite = 1, Point = 2, Spot = 3;

    public static StageLighting Read(ArchiveLayout archive)
    {
        var r = new ArchiveDataReader(archive);
        var map = archive.Roots.SingleOrDefault(root => root.Name == "map_head");
        Require(map != null, "STAGE_ROOT_MISSING", "Required stage root 'map_head' is missing.");
        r.Check(map!.Offset, 0x30);
        int groupCount = r.Int(map.Offset + 0x0C);
        int groups = r.Array(map.Offset + 8, groupCount, 0x34);
        var sets = new List<StageLightSet>();
        for (int group = 0; group < groupCount; group++)
        {
            int descriptor = groups + group * 0x34;
            // Pokémon Stadium retains unused group slots with a literal -1
            // root marker; the remaining descriptor bytes are not fields.
            if (!archive.Pointers.ContainsKey(descriptor) && r.Int(descriptor) == -1)
                continue;
            int? list = r.Pointer(descriptor + 0x18);
            if (list.HasValue)
                sets.Add(ReadSet($"group-{group:D3}", "model-group", group, list.Value));
        }
        var player = archive.Roots.SingleOrDefault(root => root.Name == "map_plit");
        if (player != null)
            sets.Add(ReadSet("map-plit", "map_plit", null, player.Offset));

        var modelSets = sets.Where(set => set.Source == "model-group" && set.Lights.Length > 0).ToArray();
        var animatedSets = modelSets.Where(set => set.Animated).ToArray();
        int distinctModelSets = modelSets.Select(Signature).Distinct().Count();
        string? preview = animatedSets.Length == 1 ? animatedSets[0].Id
            : modelSets.Length > 0 ? modelSets[0].Id
            : sets.FirstOrDefault(set => set.Id == "map-plit" && set.Lights.Length > 0)?.Id;
        string? warning = distinctModelSets > 1
            ? animatedSets.Length == 1
                ? "Stage code selects among multiple model-group light sets. The only animated set is used for the static descriptor preview."
                : "Stage code selects among multiple model-group light sets. The first set is used for the static preview."
            : null;
        return new(preview, sets.ToArray(), warning);

        static string Signature(StageLightSet set) => string.Join('|', set.Lights.Select(light =>
            $"{light.Type},{light.Flags},{light.Animated}," +
            $"{string.Join(':', light.Color.Select(BitConverter.SingleToInt32Bits))}," +
            $"{light.Position?.ToString() ?? "-"},{light.Interest?.ToString() ?? "-"}," +
            $"{light.Attenuation},{light.Parameters}"));

        StageLightSet ReadSet(string id, string source, int? groupIndex, int array)
        {
            var lights = new List<StageLight>();
            for (int entry = 0; entry < 256; entry++)
            {
                int field = checked(array + entry * 4);
                int? wrapper = r.Pointer(field);
                if (!wrapper.HasValue)
                    return new(id, source, groupIndex, lights.ToArray(), lights.Any(light => light.Animated));
                r.Check(wrapper.Value, 8);
                int? descriptor = r.Pointer(wrapper.Value);
                bool animated = r.Pointer(wrapper.Value + 4).HasValue;
                var chain = new HashSet<int>();
                while (descriptor.HasValue)
                {
                    Require(chain.Add(descriptor.Value), "LIGHT_LIST", $"Light set '{id}' contains a cyclic LOBJ chain.");
                    lights.Add(ReadLight($"{id}-light-{lights.Count:D3}", descriptor.Value, animated));
                    descriptor = r.Pointer(descriptor.Value + 4);
                }
            }
            throw new StageException("LIGHT_LIST", $"Light set '{id}' is missing its null terminator.");
        }

        StageLight ReadLight(string id, int offset, bool animated)
        {
            r.Check(offset, 0x1C);
            int flags = r.UShort(offset + 8), type = flags & 3;
            Require(type is >= Ambient and <= Spot, "LIGHT_TYPE", $"LOBJ at 0x{offset:X} has an invalid type.");
            float[] color = Enumerable.Range(0, 4).Select(i => r.Byte(offset + 0x0C + i) / 255f).ToArray();
            Vector3Data? position = ReadWObj(offset + 0x10);
            Vector3Data? interest = ReadWObj(offset + 0x14);
            if (type != Ambient) Require(position.HasValue, "LIGHT_POSITION", $"LOBJ at 0x{offset:X} has no position.");
            if (type == Spot) Require(interest.HasValue, "LIGHT_POSITION", $"Spot LOBJ at 0x{offset:X} has no interest point.");
            var attenuation = new LightAttenuation(1, 0, 0, 1, 0, 0);
            var parameters = new LightSourceParameters(false);
            int? data = r.Pointer(offset + 0x18);
            if (type is Point or Spot)
            {
                Require(data.HasValue, "LIGHT_ATTENUATION", $"LOBJ at 0x{offset:X} has no attenuation data.");
                if (r.UShort(offset + 0x0A) != 0)
                {
                    r.Check(data!.Value, 0x18);
                    attenuation = new(r.Float(data.Value), r.Float(data.Value + 4), r.Float(data.Value + 8),
                        r.Float(data.Value + 12), r.Float(data.Value + 16), r.Float(data.Value + 20));
                    if (type == Point) attenuation = attenuation with { A0 = 1, A1 = 0, A2 = 0 };
                    parameters = new(true);
                }
                else if (type == Point)
                {
                    r.Check(data!.Value, 0x0C);
                    float brightness = r.Float(data.Value), distance = r.Float(data.Value + 4);
                    int function = r.Int(data.Value + 8);
                    attenuation = Distance(distance, brightness, function);
                    parameters = new(false, ReferenceDistance: distance, ReferenceBrightness: brightness,
                        DistanceFunction: function);
                }
                else
                {
                    r.Check(data!.Value, 0x14);
                    float cutoff = r.Float(data.Value), brightness = r.Float(data.Value + 8), distance = r.Float(data.Value + 0x0C);
                    int spotFunction = r.Int(data.Value + 4), distanceFunction = r.Int(data.Value + 0x10);
                    var distanceCoefficients = Distance(distance, brightness, distanceFunction);
                    var spot = SpotCoefficients(cutoff, spotFunction);
                    attenuation = new(spot.A0, spot.A1, spot.A2, distanceCoefficients.K0,
                        distanceCoefficients.K1, distanceCoefficients.K2);
                    parameters = new(false, cutoff, spotFunction, distance, brightness, distanceFunction);
                }
            }
            Require(color.Concat(new[] { attenuation.A0, attenuation.A1, attenuation.A2,
                    attenuation.K0, attenuation.K1, attenuation.K2 }).All(float.IsFinite),
                "LIGHT_NONFINITE", $"LOBJ at 0x{offset:X} contains a non-finite value.");
            return new(id, new[] { "ambient", "infinite", "point", "spot" }[type], flags,
                (flags & 4) != 0, (flags & 8) != 0, (flags & 0x20) != 0,
                color, position, interest, attenuation, parameters, animated, offset);
        }

        Vector3Data? ReadWObj(int field)
        {
            int? offset = r.Pointer(field);
            if (!offset.HasValue) return null;
            r.Check(offset.Value, 0x10);
            var value = new Vector3Data(r.Float(offset.Value + 4), r.Float(offset.Value + 8), r.Float(offset.Value + 12));
            Require(new[] { value.X, value.Y, value.Z }.All(float.IsFinite),
                "LIGHT_NONFINITE", $"WOBJ at 0x{offset.Value:X} contains a non-finite position.");
            return value;
        }
    }

    private static LightAttenuation Distance(float distance, float brightness, int function)
    {
        if (!float.IsFinite(distance) || !float.IsFinite(brightness) || distance <= 0 || brightness <= 0 || brightness >= 1)
            function = 0;
        return function switch
        {
            1 => new(1, 0, 0, 1, (1 - brightness) / (brightness * distance), 0),
            2 => new(1, 0, 0, 1, .5f * (1 - brightness) / (brightness * distance),
                .5f * (1 - brightness) / (brightness * distance * distance)),
            3 => new(1, 0, 0, 1, 0, (1 - brightness) / (brightness * distance * distance)),
            _ => new(1, 0, 0, 1, 0, 0)
        };
    }

    private static LightAttenuation SpotCoefficients(float cutoff, int function)
    {
        if (!float.IsFinite(cutoff) || cutoff < 0 || cutoff > 90) function = 0;
        float cosine = MathF.Cos(cutoff * MathF.PI / 180);
        float d = (1 - cosine) * (1 - cosine);
        return function switch
        {
            1 => new(-1000 * cosine, 1000, 0, 1, 0, 0),
            2 => new(-cosine / (1 - cosine), 1 / (1 - cosine), 0, 1, 0, 0),
            3 => new(0, -cosine / (1 - cosine), 1 / (1 - cosine), 1, 0, 0),
            4 => new(cosine * (cosine - 2) / d, 2 / d, -1 / d, 1, 0, 0),
            5 => new(-4 * cosine / d, 4 * (1 + cosine) / d, -4 / d, 1, 0, 0),
            6 => new(1 - 2 * cosine * cosine / d, 4 * cosine / d, -2 / d, 1, 0, 0),
            _ => new(1, 0, 0, 1, 0, 0)
        };
    }
}
