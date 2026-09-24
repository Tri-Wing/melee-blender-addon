using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record PlannedDobjSplit(string PobjId, string SourceDobjId,
    string JobjId, int GroupIndex, int DobjIndex);

/// <summary>
/// Source-derived ownership expectation for the split/delete portion of a model
/// transaction. Generated DOBJ offsets and archive bytes are deliberately absent.
/// </summary>
public sealed record ModelGraphPlan(ModelIdentitySnapshot Source,
    IReadOnlySet<string> DeletedPobjIds, IReadOnlyList<PlannedDobjSplit> Splits)
{
    public IReadOnlySet<string> SplitPobjIds { get; } =
        Splits.Select(split => split.PobjId).ToHashSet(StringComparer.Ordinal);
}

/// <summary>Plans POBJ deletion and DOBJ ownership without mutating an archive.</summary>
public static class ModelGraphPlanner
{
    public static ModelGraphPlan Plan(ModelIdentitySnapshot source,
        IEnumerable<string> requestedSplitIds, IEnumerable<string> deletedIds)
    {
        var byId = source.Nodes.ToDictionary(node => node.Id, StringComparer.Ordinal);
        var deleted = deletedIds.ToHashSet(StringComparer.Ordinal);
        var requested = requestedSplitIds.ToHashSet(StringComparer.Ordinal);
        Require(deleted.All(id => byId.TryGetValue(id, out var node) && node.Kind == "pobj"),
            "MODEL_DELETE_TARGET", "Only source POBJ models can be deleted.");
        Require(requested.All(id => byId.TryGetValue(id, out var node) && node.Kind == "pobj"),
            "MODEL_DOBJ_SPLIT_TARGET", "Only source POBJ models can receive an independent DOBJ.");

        requested.ExceptWith(deleted);
        var effective = new HashSet<string>(StringComparer.Ordinal);
        foreach (var siblings in source.Nodes.Where(node => node.Kind == "pobj")
                     .GroupBy(node => node.OwnerId!))
        {
            var sourceSiblings = siblings.OrderBy(node => node.Index).ToArray();
            if (sourceSiblings.Length < 2) continue;
            var survivors = sourceSiblings.Where(node => !deleted.Contains(node.Id)).ToArray();
            var split = survivors.Where(node => requested.Contains(node.Id)).ToList();
            // Preserve one surviving POBJ under the source DOBJ. The remaining
            // requested siblings receive ordinary appended DOBJ descriptors.
            if (split.Count > 0 && split.Count == survivors.Length) split.Remove(survivors[0]);
            effective.UnionWith(split.Select(node => node.Id));
        }

        var nextDobjIndex = source.Nodes.Where(node => node.Kind == "dobj")
            .GroupBy(node => node.OwnerId!)
            .ToDictionary(group => group.Key, group => group.Max(node => node.Index) + 1,
                StringComparer.Ordinal);
        var splits = new List<PlannedDobjSplit>();
        foreach (var dobj in source.Nodes.Where(node => node.Kind == "dobj")
                     .OrderBy(node => node.SourceOffset))
        {
            foreach (var pobj in source.Nodes.Where(node => node.Kind == "pobj"
                         && node.OwnerId == dobj.Id && effective.Contains(node.Id))
                     .OrderBy(node => node.Index))
            {
                int index = nextDobjIndex.TryGetValue(dobj.OwnerId!, out int next) ? next : 0;
                nextDobjIndex[dobj.OwnerId!] = index + 1;
                splits.Add(new(pobj.Id, dobj.Id, dobj.OwnerId!, pobj.GroupIndex, index));
            }
        }
        return new(source, deleted, splits.AsReadOnly());
    }
}

/// <summary>Compares an independently decoded result graph with a source-derived plan.</summary>
public static class ModelGraphVerifier
{
    public static void Verify(ModelGraphPlan plan, ModelIdentitySnapshot actual)
    {
        var sourceById = plan.Source.Nodes.ToDictionary(node => node.Id, StringComparer.Ordinal);
        var actualById = actual.Nodes.ToDictionary(node => node.Id, StringComparer.Ordinal);
        var splitByPobj = plan.Splits.ToDictionary(split => split.PobjId, StringComparer.Ordinal);
        var originalIds = sourceById.Keys.ToHashSet(StringComparer.Ordinal);
        int expectedCount = plan.Source.Nodes.Count - plan.DeletedPobjIds.Count + plan.Splits.Count;
        Require(actual.Nodes.Count == expectedCount, "MODEL_GRAPH_MISMATCH",
            $"Model graph contains {actual.Nodes.Count} descriptors; the plan requires {expectedCount}.");

        var remainingIndexes = plan.Source.Nodes.Where(node => node.Kind == "pobj"
                && !plan.DeletedPobjIds.Contains(node.Id) && !splitByPobj.ContainsKey(node.Id))
            .GroupBy(node => node.OwnerId!)
            .SelectMany(group => group.OrderBy(node => node.Index)
                .Select((node, index) => (node.Id, Index: index)))
            .ToDictionary(item => item.Id, item => item.Index, StringComparer.Ordinal);

        foreach (var expected in plan.Source.Nodes)
        {
            if (plan.DeletedPobjIds.Contains(expected.Id))
            {
                Require(!actualById.ContainsKey(expected.Id), "MODEL_GRAPH_MISMATCH",
                    $"Deleted POBJ {expected.Id} remains in the model graph.");
                continue;
            }
            Require(actualById.TryGetValue(expected.Id, out var found), "MODEL_GRAPH_MISMATCH",
                $"Source {expected.Kind} {expected.Id} is missing from the model graph.");
            Require(found!.Kind == expected.Kind && found.GroupIndex == expected.GroupIndex
                && found.SourceOffset == expected.SourceOffset
                && found.InstanceTargetId == expected.InstanceTargetId,
                "MODEL_GRAPH_MISMATCH", $"Source {expected.Kind} {expected.Id} changed identity or location.");
            if (expected.Kind != "pobj")
            {
                Require(found.OwnerId == expected.OwnerId && found.Index == expected.Index,
                    "MODEL_GRAPH_MISMATCH", $"Source {expected.Kind} {expected.Id} changed ownership or order.");
            }
            else if (!splitByPobj.ContainsKey(expected.Id))
            {
                Require(found.OwnerId == expected.OwnerId && found.Index == remainingIndexes[expected.Id],
                    "MODEL_GRAPH_MISMATCH", $"POBJ {expected.Id} changed ownership or survivor order.");
            }
        }

        var generated = actual.Nodes.Where(node => !originalIds.Contains(node.Id)).ToArray();
        Require(generated.Length == plan.Splits.Count, "MODEL_GRAPH_MISMATCH",
            "Unexpected generated descriptors appeared in the model graph.");
        var claimed = new HashSet<string>(StringComparer.Ordinal);
        foreach (var split in plan.Splits)
        {
            var pobj = actualById[split.PobjId];
            Require(pobj.Index == 0 && pobj.OwnerId != null
                && actualById.TryGetValue(pobj.OwnerId, out var owner)
                && !originalIds.Contains(owner.Id) && claimed.Add(owner.Id)
                && owner.Kind == "dobj" && owner.OwnerId == split.JobjId
                && owner.GroupIndex == split.GroupIndex && owner.Index == split.DobjIndex
                && owner.InstanceTargetId == null,
                "MODEL_GRAPH_MISMATCH", $"POBJ {split.PobjId} was not placed in its planned independent DOBJ.");
        }
        Require(claimed.Count == generated.Length, "MODEL_GRAPH_MISMATCH",
            "A generated DOBJ is not owned by a planned split POBJ.");
    }
}
