using System.Text.Json.Serialization;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record CollisionEditVertex([property: JsonRequired] string Id, [property: JsonRequired] float X, [property: JsonRequired] float Y);
public sealed record CollisionEditLine([property: JsonRequired] string Id, [property: JsonRequired] string Vertex0Id,
    [property: JsonRequired] string Vertex1Id, [property: JsonRequired] string JointId, [property: JsonRequired] string Category,
    [property: JsonRequired] ushort HighFlags, [property: JsonRequired] ushort LowFlags);
public sealed record CollisionEdits([property: JsonRequired] int ProtocolVersion, [property: JsonRequired] string CoordinateSpace,
    [property: JsonRequired] CollisionEditVertex[] Vertices, [property: JsonRequired] CollisionEditLine[] Lines);
public sealed record CollisionSourceIds(string[] Vertices, string[] Lines, string[] Joints);

/// <summary>Compile a replacement static collision graph. Endpoint welding is per joint within 0.0001 game units.</summary>
public static class CollisionCompiler
{
    public const float WeldEpsilon = 0.0001f;
    public const float BoundsMargin = 8;
    private static readonly string[] Categories = ["floor", "ceiling", "right-wall", "left-wall", "dynamic"];

    public static CollisionData Compile(CollisionData source, CollisionSourceIds ids, CollisionEdits edits)
    {
        Require(edits.ProtocolVersion == SessionExtractor.ProtocolVersion && edits.CoordinateSpace == "game", "COLLISION_EDIT_VERSION", "Expected current protocol and game-space collision coordinates.");
        Require(source.Validate().Count == 0, "COLLISION_SOURCE_WARNING", "Resolve source collision warnings before editing.");
        Require(edits.Vertices != null && edits.Lines != null && edits.Lines.Length > 0
            && edits.Vertices.Length <= short.MaxValue && edits.Lines.Length <= short.MaxValue, "COLLISION_EDIT_COUNT", "Invalid collision edit counts.");
        Require(ids.Vertices.Length == source.Vertices.Length && ids.Lines.Length == source.Lines.Length && ids.Joints.Length == source.Joints.Length,
            "SESSION_COLLISION_IDS", "Source collision identity count changed.");
        CheckIds(ids.Vertices.Concat(ids.Lines).Concat(ids.Joints));
        Require(edits.Vertices.All(v => v != null) && edits.Lines.All(l => l != null && l.Vertex0Id != null && l.Vertex1Id != null && l.JointId != null),
            "COLLISION_EDIT_FORMAT", "Null collision records or references are invalid.");
        CheckIds(edits.Vertices.Select(v => v.Id).Concat(edits.Lines.Select(l => l.Id)).Concat(ids.Joints));
        Require(!edits.Vertices.Any(v => ids.Lines.Contains(v.Id)) && !edits.Lines.Any(l => ids.Vertices.Contains(l.Id)),
            "COLLISION_ID", "Existing collision IDs cannot change roles.");
        var positions = edits.Vertices.ToDictionary(v => v.Id, v => new CollisionVertex(v.X, v.Y));
        Require(positions.Values.All(v => float.IsFinite(v.X) && float.IsFinite(v.Y)), "COLLISION_NONFINITE", "Collision edit has non-finite coordinates.");
        var groupIndex = ids.Joints.Select((id, i) => (id, i)).ToDictionary(x => x.id, x => x.i);
        var oldLineIndex = ids.Lines.Select((id, i) => (id, i)).ToDictionary(x => x.id, x => x.i);
        var oldOwners = new int[source.Lines.Length];
        var oldCategories = new int[source.Lines.Length];
        for (int category = 0; category < source.Ranges.Length; category++)
            for (int i = source.Ranges[category].Start;
                 i < source.Ranges[category].Start + source.Ranges[category].Count; i++)
                oldCategories[i] = category;
        for (int g = 0; g < source.Joints.Length; g++)
            foreach (var range in source.Joints[g].Ranges)
                for (int i = range.Start; i < range.Start + range.Count; i++) oldOwners[i] = g;
        foreach (var line in edits.Lines)
        {
            Require(positions.ContainsKey(line.Vertex0Id) && positions.ContainsKey(line.Vertex1Id), "COLLISION_VERTEX", $"Line {line.Id} has a missing endpoint.");
            Require(groupIndex.ContainsKey(line.JointId), "COLLISION_JOINT_ID", $"Line {line.Id} has an unknown/protected joint ID.");
            Require(Array.IndexOf(Categories, line.Category) >= 0, "COLLISION_CATEGORY", $"Line {line.Id} has an unsupported category.");
            if (oldLineIndex.TryGetValue(line.Id, out int old))
            {
                Require(groupIndex[line.JointId] == oldOwners[old], "COLLISION_JOINT_CHANGED", $"Line {line.Id} moved to a different joint.");
                Require((oldCategories[old] == 4) == (line.Category == "dynamic"),
                    "COLLISION_DYNAMIC_MEMBERSHIP",
                    $"Line {line.Id} cannot move into or out of the dynamic range yet.");
                Require((line.HighFlags & ~15) == (source.Lines[old].HighFlags & ~15), "COLLISION_UNKNOWN_FLAGS", "Unknown high flag bits must be preserved.");
                Require((line.LowFlags & 0xFC00) == (source.Lines[old].LowFlags & 0xFC00), "COLLISION_UNKNOWN_FLAGS", "Unknown property bits must be preserved.");
            }
            else
            {
                int category = Array.IndexOf(Categories, line.Category);
                bool knownHighFlags = category == 4
                    ? (line.HighFlags & ~31) == 0 && (line.HighFlags & 16) != 0
                        && (line.HighFlags & 15) is 1 or 2 or 4 or 8
                    : (line.HighFlags & ~15) == 0;
                Require(knownHighFlags && (line.LowFlags & 0xFC00) == 0,
                    "COLLISION_UNKNOWN_FLAGS", "New lines may only set known collision bits.");
            }
        }
        var sorted = edits.Lines.OrderBy(l => Array.IndexOf(Categories, l.Category)).ThenBy(l => groupIndex[l.JointId]).ToArray();
        var lineIndex = sorted.Select((line, i) => (line.Id, i)).ToDictionary(x => x.Id, x => x.i);
        var vertices = new List<CollisionVertex>();
        var endpointIndex = new Dictionary<(int Group, string Id), int>();
        var joints = new CollisionJoint[source.Joints.Length];
        for (int g = 0; g < joints.Length; g++)
        {
            int start = vertices.Count;
            foreach (var id in sorted.Where(l => groupIndex[l.JointId] == g).SelectMany(l => new[] { l.Vertex0Id, l.Vertex1Id }).Distinct())
            {
                var p = positions[id]; int found = -1;
                for (int i = start; i < vertices.Count; i++)
                    if (Math.Abs((double)vertices[i].X - p.X) <= WeldEpsilon && Math.Abs((double)vertices[i].Y - p.Y) <= WeldEpsilon) { found = i; break; }
                if (found < 0) { found = vertices.Count; vertices.Add(p); }
                endpointIndex[(g, id)] = found;
            }
            Require(vertices.Count > start, "COLLISION_EMPTY_JOINT", $"Protected joint {g} cannot become empty.");
            var points = vertices.Skip(start).ToArray(); var old = source.Joints[g];
            float left = points.Min(v => v.X), right = points.Max(v => v.X), bottom = points.Min(v => v.Y), top = points.Max(v => v.Y);
            bool contains = old.Left <= left && old.Right >= right && old.Bottom <= bottom && old.Top >= top;
            joints[g] = new(Enumerable.Range(0, 5).Select(k => Range(i => groupIndex[sorted[i].JointId] == g && Array.IndexOf(Categories, sorted[i].Category) == k)).ToArray(),
                contains ? old.Left : Math.Min(old.Left, left - BoundsMargin), contains ? old.Bottom : Math.Min(old.Bottom, bottom - BoundsMargin),
                contains ? old.Right : Math.Max(old.Right, right + BoundsMargin), contains ? old.Top : Math.Max(old.Top, top + BoundsMargin), start, vertices.Count - start);
        }
        Require(vertices.Count <= short.MaxValue, "COLLISION_LIMIT", "Compiled vertex count exceeds signed 16-bit limits.");
        var lines = sorted.Select(l =>
        {
            int category = Array.IndexOf(Categories, l.Category);
            // Dynamic-range lines keep their stored initial kind. The game
            // recomputes those low four bits after applying the joint transform.
            ushort highFlags = category == 4 ? l.HighFlags
                : (ushort)((l.HighFlags & ~15) | (1 << category));
            return new CollisionLine(endpointIndex[(groupIndex[l.JointId], l.Vertex0Id)],
                endpointIndex[(groupIndex[l.JointId], l.Vertex1Id)],
                -1, -1, -1, -1, highFlags, l.LowFlags);
        }).ToArray();
        for (int i = 0; i < lines.Length; i++)
            Require(vertices[lines[i].Vertex0] != vertices[lines[i].Vertex1], "COLLISION_ZERO_LENGTH", $"Line {sorted[i].Id} has coincident endpoints after welding.");
        // mplib.c: mpLineIntersectionH and the Floor/Ceiling/Wall lookup
        // routines require endpoints ordered for the requested collision side.
        for (int i = 0; i < lines.Length; i++)
        {
            var a = vertices[lines[i].Vertex0]; var b = vertices[lines[i].Vertex1];
            bool? facing = sorted[i].Category switch
            {
                "floor" => b.X > a.X,
                "ceiling" => b.X < a.X,
                "right-wall" => b.Y < a.Y,
                "left-wall" => b.Y > a.Y,
                "dynamic" => null,
                _ => false
            };
            Require(facing != false, "COLLISION_FACING", $"Line {sorted[i].Id} direction does not match {sorted[i].Category}. Floors must run left-to-right, ceilings right-to-left, right walls downward, and left walls upward. Reassign the collision type to orient endpoints; vertical floors/ceilings and horizontal walls are invalid.");
        }
        var incident = new Dictionary<int, List<int>>();
        for (int i = 0; i < lines.Length; i++)
            foreach (int v in new[] { lines[i].Vertex0, lines[i].Vertex1 })
            {
                if (!incident.TryGetValue(v, out var edges)) incident[v] = edges = [];
                edges.Add(i);
            }
        foreach (var (vertex, edges) in incident)
        {
            Require(edges.Count <= 2, "COLLISION_AMBIGUOUS_VERTEX", $"Vertex {vertex} has ambiguous continuation among lines {string.Join(", ", edges.Select(i => sorted[i].Id))}.");
            if (edges.Count != 2) continue;
            int a = edges[0], b = edges[1];
            if (lines[a].Vertex0 == vertex) (a, b) = (b, a);
            Require(lines[a].Vertex1 == vertex && lines[b].Vertex0 == vertex && a != b,
                "COLLISION_ORIENTATION", $"Edges at vertex {vertex} need one incoming and one outgoing line.");
            lines[a] = lines[a] with { Next0 = b }; lines[b] = lines[b] with { Previous0 = a };
        }
        for (int i = 0; i < lines.Length; i++)
        {
            if (!oldLineIndex.TryGetValue(sorted[i].Id, out int old)) continue;
            lines[i] = lines[i] with { Previous1 = Alternate(source.Lines[old].Previous1, true), Next1 = Alternate(source.Lines[old].Next1, false) };
            int Alternate(int target, bool previous)
            {
                if (target < 0 || !lineIndex.TryGetValue(ids.Lines[target], out int mapped)) return -1;
                int sourceEndpoint = previous ? source.Lines[old].Vertex0 : source.Lines[old].Vertex1;
                int sourceTarget = previous ? source.Lines[target].Vertex1 : source.Lines[target].Vertex0;
                var endpoint = vertices[previous ? lines[i].Vertex0 : lines[i].Vertex1];
                var targetEndpoint = vertices[previous ? lines[mapped].Vertex1 : lines[mapped].Vertex0];
                return endpoint == source.Vertices[sourceEndpoint] && targetEndpoint == source.Vertices[sourceTarget] ? mapped : -1;
            }
        }
        // Attachments are part of the collision transaction even though this
        // edit schema currently changes geometry only. Keeping the structured
        // records here makes a later attachment writer an additive operation
        // instead of requiring another geometry/compiler redesign.
        var result = new CollisionData(vertices.ToArray(), lines, Enumerable.Range(0, 5).Select(k => Range(i => Array.IndexOf(Categories, sorted[i].Category) == k)).ToArray(), joints, source.Attachments);
        result.Validate(forEditedExport: true);
        return result;

        CollisionRange Range(Func<int, bool> predicate)
        {
            int[] matching = Enumerable.Range(0, sorted.Length).Where(predicate).ToArray();
            return new(matching.Length == 0 ? 0 : matching[0], matching.Length);
        }
    }

    private static void CheckIds(IEnumerable<string> ids)
    {
        var seen = new HashSet<string>();
        foreach (var id in ids) Require(Guid.TryParseExact(id, "N", out _) && seen.Add(id), "COLLISION_ID", "Collision IDs must be unique opaque UUIDs.");
    }
}
