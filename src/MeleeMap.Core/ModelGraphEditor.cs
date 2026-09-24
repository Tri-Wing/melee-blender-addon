using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ModelGraphEditResult(
    IReadOnlyDictionary<string, int> GeneratedDobjOffsets,
    IReadOnlyList<int> PatchedSourceFields);

/// <summary>Applies planned JOBJ/DOBJ/POBJ list membership in one graph pass.</summary>
public static class ModelGraphEditor
{
    private const string Owner = "model-graph";

    public static ModelGraphEditResult Apply(ArchiveMutationBuilder builder,
        ModelIdentitySnapshot sourceIdentity, ModelGraphPlan plan)
    {
        Require(ReferenceEquals(plan.Source, sourceIdentity)
            || plan.Source.Nodes.SequenceEqual(sourceIdentity.Nodes),
            "MODEL_GRAPH_SOURCE", "Model graph plan does not match the supplied source identity.");
        var byId = sourceIdentity.Nodes.ToDictionary(node => node.Id,
            StringComparer.Ordinal);
        var reader = new ArchiveDataReader(builder.Source);
        var generated = new Dictionary<string, int>(StringComparer.Ordinal);
        var patched = new HashSet<int>();

        void Permit(int field)
        {
            if (field >= builder.OriginalDataSize) return;
            builder.PermitSourcePatch(field, 4, Owner);
            patched.Add(field);
        }

        void Pointer(int field, int? target)
        {
            Permit(field);
            if (target.HasValue) builder.SetPointer(field, target.Value, Owner);
            else builder.ClearPointer(field, Owner);
        }

        foreach (var split in plan.Splits)
        {
            var sourceDobj = byId[split.SourceDobjId];
            int material = reader.Pointer(sourceDobj.SourceOffset + 8)
                ?? throw new StageException("MODEL_DOBJ_SPLIT_MATERIAL",
                    "A shared DOBJ has no material to preserve while splitting it.");
            int dobj = builder.AppendZeroed(0x10);
            builder.SetPointer(dobj + 8, material, Owner);
            builder.SetPointer(dobj + 0x0C, byId[split.PobjId].SourceOffset, Owner);
            generated.Add(split.PobjId, dobj);
        }

        var splitIds = plan.SplitPobjIds;
        var affectedDobjIds = sourceIdentity.Nodes.Where(node => node.Kind == "pobj"
                && (splitIds.Contains(node.Id) || plan.DeletedPobjIds.Contains(node.Id)))
            .Select(node => node.OwnerId!).Distinct(StringComparer.Ordinal).ToArray();
        foreach (string dobjId in affectedDobjIds)
        {
            var dobj = byId[dobjId];
            var sourcePobjs = sourceIdentity.Nodes.Where(node => node.Kind == "pobj"
                    && node.OwnerId == dobjId).OrderBy(node => node.Index).ToArray();
            var survivors = sourcePobjs.Where(node => !splitIds.Contains(node.Id)
                && !plan.DeletedPobjIds.Contains(node.Id)).ToArray();
            Pointer(dobj.SourceOffset + 0x0C,
                survivors.Length == 0 ? null : survivors[0].SourceOffset);
            for (int index = 0; index < survivors.Length; index++)
                Pointer(survivors[index].SourceOffset + 4,
                    index + 1 < survivors.Length ? survivors[index + 1].SourceOffset : null);
            foreach (var detached in sourcePobjs.Where(node => !survivors.Contains(node)))
                Pointer(detached.SourceOffset + 4, null);
        }

        foreach (var jobjGroup in plan.Splits.GroupBy(split => split.JobjId,
                     StringComparer.Ordinal))
        {
            var jobj = byId[jobjGroup.Key];
            var members = sourceIdentity.Nodes.Where(node => node.Kind == "dobj"
                    && node.OwnerId == jobj.Id)
                .Select(node => (Index: node.Index, Offset: node.SourceOffset))
                .Concat(jobjGroup.Select(split =>
                    (Index: split.DobjIndex, Offset: generated[split.PobjId])))
                .OrderBy(member => member.Index).ToArray();
            Require(members.Select(member => member.Index)
                    .SequenceEqual(Enumerable.Range(0, members.Length)),
                "MODEL_GRAPH_PLAN", "Planned DOBJ indexes are not contiguous.");
            Pointer(jobj.SourceOffset + 0x10,
                members.Length == 0 ? null : members[0].Offset);
            for (int index = 0; index < members.Length; index++)
                Pointer(members[index].Offset + 4,
                    index + 1 < members.Length ? members[index + 1].Offset : null);
        }

        return new(generated, patched.Order().ToArray());
    }

    public static int AppendDobjList(ArchiveMutationBuilder builder,
        ModelIdentitySnapshot identity, string jobjId, IReadOnlyList<int> appended,
        string patchOwner)
    {
        Require(appended.Count > 0, "MODEL_GRAPH_APPEND", "A DOBJ append requires at least one descriptor.");
        var byId = identity.Nodes.ToDictionary(node => node.Id, StringComparer.Ordinal);
        Require(byId.TryGetValue(jobjId, out var jobj) && jobj.Kind == "jobj",
            "MODEL_GRAPH_APPEND", "A DOBJ append target must be an ordinary JOBJ.");
        var existing = identity.Nodes.Where(node => node.Kind == "dobj" && node.OwnerId == jobjId)
            .OrderBy(node => node.Index).ToArray();
        int field = existing.Length == 0 ? jobj.SourceOffset + 0x10
            : existing[^1].SourceOffset + 4;
        Require(builder.ReadInt32(field) == 0, "MODEL_GRAPH_APPEND",
            "The planned JOBJ DOBJ tail is no longer empty.");
        Permit(builder, field, patchOwner);
        builder.SetPointer(field, appended[0], patchOwner);
        WriteList(builder, appended, 4, patchOwner);
        return field;
    }

    public static void SetGeneratedDobjList(ArchiveMutationBuilder builder, int jobjOffset,
        IReadOnlyList<int> members, string patchOwner)
    {
        Require(jobjOffset >= builder.OriginalDataSize && members.Count > 0,
            "MODEL_GRAPH_APPEND", "A generated JOBJ requires a nonempty generated DOBJ list.");
        builder.SetPointer(jobjOffset + 0x10, members[0], patchOwner);
        WriteList(builder, members, 4, patchOwner);
    }

    public static int AppendJobjChildren(ArchiveMutationBuilder builder,
        ModelIdentitySnapshot identity, string anchorJobjId, IReadOnlyList<int> appended,
        string patchOwner)
    {
        Require(appended.Count > 0, "MODEL_GRAPH_APPEND", "A JOBJ append requires at least one descriptor.");
        var byId = identity.Nodes.ToDictionary(node => node.Id, StringComparer.Ordinal);
        Require(byId.TryGetValue(anchorJobjId, out var anchor) && anchor.Kind == "jobj",
            "MODEL_GRAPH_APPEND", "A JOBJ child append anchor must be an ordinary JOBJ.");
        var children = identity.Nodes.Where(node => node.OwnerId == anchorJobjId
                && node.Kind.EndsWith("jobj", StringComparison.Ordinal))
            .OrderBy(node => node.Index).ToArray();
        int field = children.Length == 0 ? anchor.SourceOffset + 8
            : children[^1].SourceOffset + 12;
        Require(builder.ReadInt32(field) == 0, "MODEL_GRAPH_APPEND",
            "The planned JOBJ child tail is no longer empty.");
        Permit(builder, field, patchOwner);
        builder.SetPointer(field, appended[0], patchOwner);
        WriteList(builder, appended, 12, patchOwner);
        return field;
    }

    private static void WriteList(ArchiveMutationBuilder builder,
        IReadOnlyList<int> members, int linkOffset, string patchOwner)
    {
        for (int index = 0; index < members.Count; index++)
        {
            int field = members[index] + linkOffset;
            if (index + 1 < members.Count)
                builder.SetPointer(field, members[index + 1], patchOwner);
            else
                builder.ClearPointer(field, patchOwner);
        }
    }

    private static void Permit(ArchiveMutationBuilder builder, int field, string owner)
    {
        if (field < builder.OriginalDataSize)
            builder.PermitSourcePatch(field, 4, owner);
    }
}
