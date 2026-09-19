using System.Buffers.Binary;
using System.Text.Json.Serialization;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record StageLightVector([property: JsonRequired] float X,
    [property: JsonRequired] float Y, [property: JsonRequired] float Z);
public sealed record StageLightEdit([property: JsonRequired] string Id, bool? Enabled = null,
    int[]? Color = null, StageLightVector? Position = null, StageLightVector? Interest = null);
public sealed record StageLightEdits([property: JsonRequired] int ProtocolVersion,
    [property: JsonRequired] StageLightEdit[] Lights);
public sealed record StageLightWrite(byte[] Bytes, StageLightEdit[] Edits);

/// <summary>Writes supported static LOBJ fields while preserving animation and attenuation records.</summary>
public static class StageLightEditing
{
    public static StageLightWrite Write(ArchiveLayout source, ArchiveLayout current,
        StageLightEdits edits, string[] declaredIds)
    {
        Require(edits.ProtocolVersion == SessionExtractor.ProtocolVersion && edits.Lights is { Length: > 0 },
            "LIGHT_EDIT_FORMAT", "Light edits require the current protocol and at least one light.");
        var eligible = StageLightingReader.Read(source).LightSets.SelectMany(set => set.Lights).ToDictionary(light => light.Id);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var offsets = new HashSet<int>();
        foreach (var edit in edits.Lights)
        {
            Require(edit != null && edit.Id != null && seen.Add(edit.Id) && declaredIds.Contains(edit.Id)
                && eligible.ContainsKey(edit.Id), "LIGHT_EDIT_TARGET",
                "Light is unsupported, duplicated, or absent from this session.");
            var target = eligible[edit!.Id];
            Require(offsets.Add(target.SourceOffset), "LIGHT_EDIT_SHARED",
                "Multiple edit entries refer to one shared LOBJ descriptor.");
            Require(edit.Enabled.HasValue || edit.Color != null || edit.Position != null || edit.Interest != null,
                "LIGHT_EDIT_FORMAT", "A light edit must change at least one supported property.");
            Require(edit.Color == null || (edit.Color.Length == 3 && edit.Color.All(value => value is >= 0 and <= 255)),
                "LIGHT_COLOR", "Light color requires three byte values.");
            Require(edit.Position == null || target.Type != "ambient", "LIGHT_POSITION",
                "Ambient lights do not have a position.");
            Require(edit.Interest == null || target.Type == "spot", "LIGHT_INTEREST",
                "Only spot lights have an interest point.");
            Require(Finite(edit.Position) && Finite(edit.Interest), "LIGHT_NONFINITE",
                "Light positions and interest points must be finite.");
        }

        var data = current.Bytes.AsSpan(32, current.DataSize).ToArray().ToList();
        var addedRelocations = new List<int>();
        var reader = new ArchiveDataReader(current);
        int oldRelocationCount = current.Read(8);
        int oldTable = 32 + current.DataSize;
        var relocationFields = Enumerable.Range(0, oldRelocationCount)
            .Select(index => current.Read(oldTable + index * 4)).ToHashSet();
        foreach (var edit in edits.Lights)
        {
            var target = eligible[edit.Id];
            int offset = target.SourceOffset;
            if (edit.Enabled.HasValue)
            {
                ushort flags = reader.UShort(offset + 8);
                flags = edit.Enabled.Value ? (ushort)(flags & ~0x20) : (ushort)(flags | 0x20);
                Put(data, offset + 8, flags);
            }
            if (edit.Color != null)
                for (int channel = 0; channel < 3; channel++) data[offset + 0x0C + channel] = (byte)edit.Color[channel];
            if (edit.Position != null) CloneWobj(offset + 0x10, edit.Position);
            if (edit.Interest != null) CloneWobj(offset + 0x14, edit.Interest);
        }

        using var stream = new MemoryStream();
        stream.Write(current.Bytes.AsSpan(0, 32));
        stream.Write(data.ToArray());
        stream.Write(current.Bytes.AsSpan(oldTable, oldRelocationCount * 4));
        foreach (int field in addedRelocations)
        {
            byte[] value = new byte[4];
            BinaryPrimitives.WriteInt32BigEndian(value, field);
            stream.Write(value);
        }
        stream.Write(current.Bytes.AsSpan(oldTable + oldRelocationCount * 4));
        byte[] bytes = stream.ToArray();
        Put(bytes, 0, bytes.Length);
        Put(bytes, 4, data.Count);
        Put(bytes, 8, oldRelocationCount + addedRelocations.Count);
        var result = new StageLightWrite(bytes, edits.Lights);
        Verify(new ArchiveLayout(bytes), result);
        return result;

        void CloneWobj(int field, StageLightVector value)
        {
            int original = reader.Pointer(field) ?? throw new StageException("LIGHT_POSITION", "Light WOBJ is missing.");
            reader.Check(original, 16);
            Require(data.Count % 4 == 0, "LIGHT_ALIGNMENT", "Archive data is not word aligned.");
            int copy = data.Count;
            data.AddRange(current.Bytes.AsSpan(32 + original, 16).ToArray());
            Put(data, copy + 4, value.X);
            Put(data, copy + 8, value.Y);
            Put(data, copy + 12, value.Z);
            Put(data, field, copy);
            int classField = reader.Int(original);
            Require(classField == 0 || relocationFields.Contains(original), "LIGHT_WOBJ_CLASS",
                "Custom external WOBJ classes cannot be cloned safely.");
            if (classField != 0) addedRelocations.Add(copy);
        }
    }

    public static void Verify(ArchiveLayout archive, StageLightWrite expected)
    {
        var actual = StageLightingReader.Read(archive).LightSets.SelectMany(set => set.Lights).ToDictionary(light => light.Id);
        foreach (var edit in expected.Edits)
        {
            Require(actual.TryGetValue(edit.Id, out var light), "LIGHT_WRITE_MISMATCH",
                "Edited light disappeared after export.");
            if (edit.Enabled.HasValue) Require(!light.Hidden == edit.Enabled.Value,
                "LIGHT_WRITE_MISMATCH", "Edited light visibility changed after export.");
            if (edit.Color != null) Require(edit.Color.Select(value => value / 255f).SequenceEqual(light.Color.Take(3)),
                "LIGHT_WRITE_MISMATCH", "Edited light color changed after export.");
            if (edit.Position != null) Require(light.Position == new Vector3Data(edit.Position.X, edit.Position.Y, edit.Position.Z),
                "LIGHT_WRITE_MISMATCH", "Edited light position changed after export.");
            if (edit.Interest != null) Require(light.Interest == new Vector3Data(edit.Interest.X, edit.Interest.Y, edit.Interest.Z),
                "LIGHT_WRITE_MISMATCH", "Edited light interest changed after export.");
        }
    }

    private static bool Finite(StageLightVector? value) => value == null
        || (float.IsFinite(value.X) && float.IsFinite(value.Y) && float.IsFinite(value.Z));
    private static void Put(List<byte> bytes, int offset, ushort value)
    {
        Span<byte> data = stackalloc byte[2]; BinaryPrimitives.WriteUInt16BigEndian(data, value);
        bytes[offset] = data[0]; bytes[offset + 1] = data[1];
    }
    private static void Put(List<byte> bytes, int offset, int value)
    {
        Span<byte> data = stackalloc byte[4]; BinaryPrimitives.WriteInt32BigEndian(data, value);
        for (int i = 0; i < 4; i++) bytes[offset + i] = data[i];
    }
    private static void Put(List<byte> bytes, int offset, float value) => Put(bytes, offset, BitConverter.SingleToInt32Bits(value));
    private static void Put(byte[] bytes, int offset, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
}
