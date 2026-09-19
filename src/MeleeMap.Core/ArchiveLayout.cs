using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;

namespace MeleeMap.Core;

public sealed class StageException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}

public sealed record RootInfo(string Name, int Offset);

/// <summary>Bounds-check the on-disk archive before passing it to HSDRaw.</summary>
public sealed class ArchiveLayout
{
    public byte[] Bytes { get; }
    public int DataSize { get; }
    public string Version { get; }
    public List<RootInfo> Roots { get; } = [];
    public List<RootInfo> References { get; } = [];
    public SortedDictionary<int, int> Pointers { get; } = [];

    public ArchiveLayout(byte[] bytes)
    {
        Bytes = bytes;
        Require(bytes.Length >= 32, "ARCHIVE_HEADER", "Archive header is truncated.");
        Require(Read(0) == bytes.Length, "ARCHIVE_SIZE", "Header file size does not match the file.");
        DataSize = Read(4);
        int relocations = Read(8), roots = Read(12), references = Read(16);
        long table = 32L + DataSize;
        long names = table + 4L * relocations + 8L * (roots + (long)references);
        Require(DataSize >= 0 && relocations >= 0 && roots >= 0 && references >= 0 && names <= bytes.Length,
            "ARCHIVE_BOUNDS", "Archive sections exceed file bounds.");
        Version = Encoding.ASCII.GetString(bytes, 20, 4).TrimEnd('\0');
        for (int i = 0; i < relocations; i++)
        {
            int field = Read((int)table + i * 4);
            Require(field >= 0 && field <= DataSize - 4, "RELOCATION_FIELD", $"Invalid pointer field {field}.");
            int target = Read(32 + field);
            Require(target >= 0 && target < DataSize, "RELOCATION_TARGET", $"Invalid target {target} at {field}.");
            Require(Pointers.TryAdd(field, target), "RELOCATION_DUPLICATE", $"Duplicate relocation at {field}.");
        }
        for (int i = 0; i < roots + references; i++)
        {
            int entry = (int)table + 4 * relocations + i * 8;
            int target = Read(entry);
            long name = names + Read(entry + 4);
            Require(target >= 0 && target < DataSize, "ROOT_TARGET", "Root target is outside the data section.");
            Require(name >= names && name < bytes.Length, "ROOT_NAME", "Root name is outside the string table.");
            int end = Array.IndexOf(bytes, (byte)0, (int)name);
            Require(end >= 0, "ROOT_NAME", "Root name is not terminated.");
            var root = new RootInfo(Encoding.UTF8.GetString(bytes, (int)name, end - (int)name), target);
            (i < roots ? Roots : References).Add(root);
        }
        // External references are linked lists whose pointer fields are absent from relocation entries.
        foreach (var root in References)
        {
            int field = root.Offset;
            var seen = new HashSet<int>();
            while (true)
            {
                Require(field >= 0 && field <= DataSize - 4 && seen.Add(field), "REFERENCE_CHAIN", "Invalid or cyclic external reference chain.");
                int target = Read(32 + field);
                if (target is 0 or -1) break;
                Require(target >= 0 && target < DataSize && Pointers.TryAdd(field, target), "REFERENCE_CHAIN", "Invalid external reference target.");
                field = target;
            }
        }
    }

    public int Read(int offset) => BinaryPrimitives.ReadInt32BigEndian(Bytes.AsSpan(offset, 4));
    public static void Require([System.Diagnostics.CodeAnalysis.DoesNotReturnIf(false)] bool condition, string code, string message)
    {
        if (!condition) throw new StageException(code, message);
    }

    /// <summary>Strict topology/payload digest: pointer addresses become source-order structure IDs.
    /// Padding remains significant, so uncertainty fails comparison instead of hiding lost data.</summary>
    public string SemanticHash()
    {
        int[] starts = Pointers.Values.Concat(Roots.Select(r => r.Offset))
            .Concat(References.Select(r => r.Offset)).Append(0).Distinct().Order().ToArray();
        var ids = starts.Select((offset, id) => (offset, id)).ToDictionary(x => x.offset, x => x.id);
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream);
        writer.Write(Version);
        foreach (var list in new[] { Roots, References })
        {
            writer.Write(list.Count);
            foreach (var root in list) { writer.Write(root.Name); writer.Write(ids[root.Offset]); }
        }
        var pendingLinks = new Queue<KeyValuePair<int, int>>(Pointers);
        for (int i = 0; i < starts.Length; i++)
        {
            int end = i + 1 < starts.Length ? starts[i + 1] : DataSize;
            byte[] payload = Bytes.AsSpan(32 + starts[i], end - starts[i]).ToArray();
            var links = new List<KeyValuePair<int, int>>();
            while (pendingLinks.TryPeek(out var next) && next.Key < end)
                links.Add(pendingLinks.Dequeue());
            foreach (var link in links)
            {
                Require(link.Key + 4 <= end, "RELOCATION_OVERLAP", "Pointer crosses a structure boundary.");
                payload.AsSpan(link.Key - starts[i], 4).Clear();
            }
            writer.Write(payload.Length);
            writer.Write(payload);
            writer.Write(links.Count);
            foreach (var link in links) { writer.Write(link.Key - starts[i]); writer.Write(ids[link.Value]); }
        }
        writer.Flush();
        return Convert.ToHexString(SHA256.HashData(stream.ToArray())).ToLowerInvariant();
    }
}
