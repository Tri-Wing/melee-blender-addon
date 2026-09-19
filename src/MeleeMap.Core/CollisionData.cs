using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ValidationIssue(string Code, string Message, int? LineIndex = null);
public readonly record struct CollisionVertex(float X, float Y);
public readonly record struct CollisionRange(int Start, int Count);
public sealed record CollisionLine(int Vertex0, int Vertex1, int Previous0, int Next0, int Previous1, int Next1,
    ushort HighFlags, ushort LowFlags);
public sealed record CollisionJoint(CollisionRange[] Ranges, float Left, float Bottom, float Right, float Top,
    int VertexStart, int VertexCount);
public sealed record CollisionAttachment(int GroupIndex, int JointIndex, int RawMiddleIndex, int JobjIndex);
public sealed record CollisionData(CollisionVertex[] Vertices, CollisionLine[] Lines, CollisionRange[] Ranges,
    CollisionJoint[] Joints, CollisionAttachment[] Attachments)
{
    public static CollisionData Read(ArchiveLayout archive)
    {
        var root = archive.Roots.SingleOrDefault(r => r.Name == "coll_data");
        Require(root != null, "STAGE_ROOT_MISSING", "Required root coll_data is missing.");
        var r = new ArchiveDataReader(archive); int c = root!.Offset;
        r.Check(c, 0x2C);
        int nv = r.Int(c + 4), nl = r.Int(c + 12), nj = r.Int(c + 40);
        Require(nv <= short.MaxValue && nl <= short.MaxValue && nj <= short.MaxValue,
            "COLLISION_LIMIT", "Collision counts exceed signed 16-bit limits.");
        int v = r.Array(c, nv, 8), l = r.Array(c + 8, nl, 16), j = r.Array(c + 36, nj, 40);
        var vertices = Enumerable.Range(0, nv).Select(i => new CollisionVertex(r.Float(v + i * 8), r.Float(v + i * 8 + 4))).ToArray();
        var lines = Enumerable.Range(0, nl).Select(i => { int o = l + i * 16; return new CollisionLine(r.UShort(o), r.UShort(o + 2),
            r.Short(o + 4), r.Short(o + 6), r.Short(o + 8), r.Short(o + 10), r.UShort(o + 12), r.UShort(o + 14)); }).ToArray();
        CollisionRange[] RangesAt(int o) => Enumerable.Range(0, 5).Select(k => new CollisionRange(r.Short(o + k * 4), r.Short(o + k * 4 + 2))).ToArray();
        var joints = Enumerable.Range(0, nj).Select(i => { int o = j + i * 40; return new CollisionJoint(RangesAt(o),
            r.Float(o + 20), r.Float(o + 24), r.Float(o + 28), r.Float(o + 32), r.Short(o + 36), r.Short(o + 38)); }).ToArray();
        var attachments = new List<CollisionAttachment>();
        var map = archive.Roots.SingleOrDefault(x => x.Name == "map_head");
        if (map != null)
        {
            int ng = r.Int(map.Offset + 12), groups = r.Array(map.Offset + 8, ng, 0x34);
            for (int g = 0; g < ng; g++)
            {
                int group = groups + g * 0x34, count = r.Int(group + 0x24), links = r.Array(group + 0x20, count, 6);
                for (int i = 0; i < count; i++)
                    attachments.Add(new(g, r.Short(links + i * 6), r.Short(links + i * 6 + 2), r.Short(links + i * 6 + 4)));
            }
        }
        return new(vertices, lines, RangesAt(c + 16), joints, attachments.ToArray());
    }

    public IReadOnlyList<ValidationIssue> Validate(ModelIdentitySnapshot? models = null, bool forEditedExport = false)
    {
        Require(Vertices.Length <= short.MaxValue && Lines.Length <= short.MaxValue && Joints.Length <= short.MaxValue,
            "COLLISION_LIMIT", "Collision counts exceed signed 16-bit limits.");
        var warnings = new List<ValidationIssue>();
        int[] categories = Enumerable.Repeat(-1, Lines.Length).ToArray();
        int[] owners = Enumerable.Repeat(-1, Lines.Length).ToArray();
        foreach (var (vertex, i) in Vertices.Select((v, i) => (v, i)))
            Require(float.IsFinite(vertex.X) && float.IsFinite(vertex.Y), "COLLISION_NONFINITE", $"Vertex {i} is not finite.");
        CheckRanges(Ranges, (i, k) => { Require(categories[i] == -1, "COLLISION_RANGE_OVERLAP", $"Line {i} has multiple categories."); categories[i] = k; });
        Require(categories.All(k => k >= 0), "COLLISION_RANGE_MISSING", "Some lines have no category.");
        for (int i = 0; i < Lines.Length; i++)
        {
            var line = Lines[i];
            Require(line.Vertex0 >= 0 && line.Vertex0 < Vertices.Length && line.Vertex1 >= 0 && line.Vertex1 < Vertices.Length,
                "COLLISION_VERTEX", $"Line {i} references an invalid vertex.");
            foreach (int link in new[] { line.Previous0, line.Next0, line.Previous1, line.Next1 })
                Require(link >= -1 && link < Lines.Length, "COLLISION_LINK", $"Line {i} has an invalid link.");
            Require(categories[i] == 4 || (line.HighFlags & 15) == 1 << categories[i],
                "COLLISION_CATEGORY_FLAGS", $"Line {i} flags disagree with its static category.");
            if (Vertices[line.Vertex0] == Vertices[line.Vertex1])
            {
                Require(!forEditedExport, "COLLISION_ZERO_LENGTH", $"Line {i} has coincident endpoints.");
                warnings.Add(new("COLLISION_ZERO_LENGTH", $"Source line {i} has coincident endpoints; preserved for the game's empty-line handling.", i));
            }
        }
        for (int g = 0; g < Joints.Length; g++)
        {
            var joint = Joints[g];
            Require(joint.VertexStart >= 0 && joint.VertexCount >= 0 && (long)joint.VertexStart + joint.VertexCount <= Vertices.Length,
                "COLLISION_JOINT_VERTICES", $"Joint {g} vertex range is invalid.");
            Require(new[] { joint.Left, joint.Bottom, joint.Right, joint.Top }.All(float.IsFinite)
                && joint.Left <= joint.Right && joint.Bottom <= joint.Top, "COLLISION_JOINT_BOUNDS", $"Joint {g} bounds are invalid.");
            // Retail bounds and vertices have independently rounded floats. Tolerance: 0.001 game units.
            for (int v = joint.VertexStart; v < joint.VertexStart + joint.VertexCount; v++)
                Require(Vertices[v].X >= joint.Left - 0.001f && Vertices[v].X <= joint.Right + 0.001f
                    && Vertices[v].Y >= joint.Bottom - 0.001f && Vertices[v].Y <= joint.Top + 0.001f,
                    "COLLISION_JOINT_BOUNDS", $"Joint {g} bounds do not enclose vertex {v}.");
            CheckRanges(joint.Ranges, (i, k) =>
            {
                Require(categories[i] == k, "COLLISION_JOINT_CATEGORY", $"Joint {g}, line {i}: category differs from global range.");
                Require(owners[i] == -1, "COLLISION_JOINT_OVERLAP", $"Line {i} belongs to multiple joints."); owners[i] = g;
                Require(InRange(Lines[i].Vertex0) && InRange(Lines[i].Vertex1), "COLLISION_JOINT_ENDPOINT", $"Joint {g} does not own line {i}'s vertices.");
                bool InRange(int v) => v >= joint.VertexStart && v < joint.VertexStart + joint.VertexCount;
            });
        }
        Require(owners.All(g => g >= 0), "COLLISION_JOINT_MISSING", "Some lines have no collision joint.");
        var incident = new Dictionary<(int Joint, int Vertex), HashSet<int>>();
        for (int i = 0; i < Lines.Length; i++)
            foreach (int v in new[] { Lines[i].Vertex0, Lines[i].Vertex1 })
            {
                if (!incident.TryGetValue((owners[i], v), out var edges)) incident[(owners[i], v)] = edges = [];
                edges.Add(i);
            }
        for (int i = 0; i < Lines.Length; i++)
        {
            var line = Lines[i];
            CheckLink(line.Previous0, line.Vertex0, true); CheckLink(line.Next0, line.Vertex1, false);
            void CheckLink(int other, int endpoint, bool previous)
            {
                if (other == -1 || incident[(owners[i], endpoint)].Count != 2 || owners[other] != owners[i]) return;
                var target = Lines[other];
                Require((previous ? target.Next0 : target.Previous0) == i,
                    "COLLISION_LINK_RECIPROCAL", $"Line {i} link to {other} is not reciprocal at a simple vertex.");
                Require((previous ? target.Vertex1 : target.Vertex0) == endpoint,
                    "COLLISION_LINK_ENDPOINT", $"Line {i} link to {other} does not share the expected endpoint.");
            }
        }
        foreach (var attachment in Attachments)
        {
            Require(attachment.JointIndex >= 0 && attachment.JointIndex < Joints.Length && attachment.JobjIndex >= 0,
                "COLLISION_ATTACHMENT", $"Group {attachment.GroupIndex} has an invalid collision attachment.");
            if (models != null)
            {
                var group = models.Nodes.FirstOrDefault(n => n.GroupIndex == attachment.GroupIndex && n.Kind is "group" or "sentinel-group");
                if (group?.Kind == "sentinel-group")
                {
                    warnings.Add(new("COLLISION_ATTACHMENT_EXTERNAL", $"Group {attachment.GroupIndex} attachment requires a companion archive; target JOBJ {attachment.JobjIndex} cannot be checked locally."));
                    continue;
                }
                // Ground_GetStageGObj inserts a runtime wrapper above the serialized root.
                // mpLib_800552B0 starts at that wrapper's child: serialized preorder index zero.
                int count = models.Nodes.Count(n => n.GroupIndex == attachment.GroupIndex && n.Kind.EndsWith("jobj"));
                Require(attachment.JobjIndex < count, "COLLISION_ATTACHMENT", $"Group {attachment.GroupIndex}: JOBJ attachment index {attachment.JobjIndex} is outside the serialized traversal.");
            }
        }
        return warnings.AsReadOnly();

        void CheckRanges(CollisionRange[] ranges, Action<int, int> visit)
        {
            Require(ranges.Length == 5, "COLLISION_RANGE", "Expected five collision category ranges.");
            for (int k = 0; k < ranges.Length; k++)
            {
                var range = ranges[k];
                Require(range.Count >= 0 && (range.Count == 0 || range.Start >= 0 && (long)range.Start + range.Count <= Lines.Length),
                    "COLLISION_RANGE", $"Category {k} range is invalid.");
                for (int i = range.Start; i < range.Start + range.Count; i++) visit(i, k);
            }
        }
    }
}
