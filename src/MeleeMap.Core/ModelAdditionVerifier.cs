using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

/// <summary>
/// Verifies additions from pre-write planned chunks without using allocated
/// offsets or descriptor mappings reported by the writer.
/// </summary>
public static class ModelAdditionVerifier
{
    public static void Verify(ArchiveLayout additionBase, ArchiveLayout archive,
        ModelIdentitySnapshot identity, PlannedModelAdditions plan)
    {
        Require(additionBase.Roots.SequenceEqual(archive.Roots)
            && additionBase.References.SequenceEqual(archive.References),
            "MODEL_ADDITION_PLAN_MISMATCH",
            "The planned addition changed public roots or external references.");
        var catalog = ModelIdentityCatalog.Restore(identity.Nodes);
        var actual = ModelIdentity.Capture(archive, catalog);
        var actualById = actual.Nodes.ToDictionary(node => node.Id, StringComparer.Ordinal);
        var originalIds = identity.Nodes.Select(node => node.Id)
            .ToHashSet(StringComparer.Ordinal);
        foreach (var original in identity.Nodes)
            Require(actualById.TryGetValue(original.Id, out var found) && found == original,
                "MODEL_ADDITION_PLAN_MISMATCH",
                "An original model descriptor differs from the planned addition base.");

        var newNodes = actual.Nodes.Where(node => !originalIds.Contains(node.Id)).ToArray();
        var reader = new ArchiveDataReader(archive);
        var newChainEdits = plan.Source.Edits.Additions.Where(addition =>
                plan.Source.Targets[addition.TargetJobjId].Placement
                    == ModelAddition.NewJobjChainPlacement)
            .ToArray();
        Require(newNodes.Length == plan.Chunks.Count * 2 + newChainEdits.Length,
            "MODEL_ADDITION_PLAN_MISMATCH",
            "The final model graph has an unexpected number of addition descriptors.");

        var claimed = new HashSet<string>(StringComparer.Ordinal);
        var jobjsByAddition = new Dictionary<string, ModelIdentityNode>(StringComparer.Ordinal);
        foreach (var anchorGroup in newChainEdits.GroupBy(addition =>
                     plan.Source.Targets[addition.TargetJobjId].AnchorJobjId,
                     StringComparer.Ordinal))
        {
            var expected = anchorGroup.ToArray();
            var found = newNodes.Where(node => node.Kind == "jobj"
                    && node.OwnerId == anchorGroup.Key)
                .OrderBy(node => node.Index).ToArray();
            Require(found.Length == expected.Length, "MODEL_ADDITION_PLAN_MISMATCH",
                "The planned JOBJ addition chain has unexpected membership.");
            for (int index = 0; index < expected.Length; index++)
            {
                jobjsByAddition.Add(expected[index].Id, found[index]);
                claimed.Add(found[index].Id);
            }
        }

        foreach (var targetGroup in plan.Chunks.Where(chunk =>
                     chunk.Placement == ModelAddition.ExistingJobjPlacement)
                 .GroupBy(chunk => chunk.TargetId, StringComparer.Ordinal))
        {
            var expected = targetGroup.ToArray();
            var found = newNodes.Where(node => node.Kind == "dobj"
                    && node.OwnerId == targetGroup.Key)
                .OrderBy(node => node.Index).ToArray();
            VerifyChunks(expected, found);
        }

        foreach (var additionGroup in plan.Chunks.Where(chunk =>
                     chunk.Placement == ModelAddition.NewJobjChainPlacement)
                 .GroupBy(chunk => chunk.AdditionId, StringComparer.Ordinal))
        {
            var expected = additionGroup.ToArray();
            var jobj = jobjsByAddition[additionGroup.Key];
            var found = newNodes.Where(node => node.Kind == "dobj"
                    && node.OwnerId == jobj.Id)
                .OrderBy(node => node.Index).ToArray();
            VerifyChunks(expected, found);
        }

        Require(claimed.Count == newNodes.Length,
            "MODEL_ADDITION_PLAN_MISMATCH",
            "The final model graph contains an unplanned addition descriptor.");

        void VerifyChunks(PlannedModelAdditionChunk[] expected,
            ModelIdentityNode[] dobjs)
        {
            Require(dobjs.Length == expected.Length, "MODEL_ADDITION_PLAN_MISMATCH",
                "A planned addition DOBJ list has unexpected membership.");
            for (int index = 0; index < expected.Length; index++)
            {
                var dobj = dobjs[index];
                claimed.Add(dobj.Id);
                var pobjs = newNodes.Where(node => node.Kind == "pobj"
                    && node.OwnerId == dobj.Id).OrderBy(node => node.Index).ToArray();
                Require(pobjs.Length == 1 && pobjs[0].Index == 0,
                    "MODEL_ADDITION_PLAN_MISMATCH",
                    "A planned addition DOBJ does not own exactly one POBJ.");
                claimed.Add(pobjs[0].Id);
                Require(reader.Pointer(dobj.SourceOffset + 8) != null
                    && reader.Pointer(dobj.SourceOffset + 0x0C) == pobjs[0].SourceOffset
                    && reader.UShort(pobjs[0].SourceOffset + 0x0C) == 0x4001,
                    "MODEL_ADDITION_PLAN_MISMATCH",
                    "A planned addition descriptor has an invalid material or geometry binding.");
                var mesh = GxMeshDecoder.Decode(archive, pobjs[0].SourceOffset);
                Require(Equal(expected[index].Mesh, mesh),
                    "MODEL_ADDITION_PLAN_MISMATCH",
                    "Decoded addition geometry differs from the pre-write plan.");
            }
        }
    }

    private static bool Equal(MeshData expected, MeshData actual) =>
        expected.Positions.SequenceEqual(actual.Positions)
        && expected.TriangleIndices.SequenceEqual(actual.TriangleIndices)
        && expected.Normals != null && actual.Normals != null
        && expected.Normals.SequenceEqual(actual.Normals)
        && ((expected.TexCoords0 == null && actual.TexCoords0 == null)
            || (expected.TexCoords0 != null && actual.TexCoords0 != null
                && expected.TexCoords0.SequenceEqual(actual.TexCoords0)))
        && actual.TexCoords1 == null && actual.Envelopes == null
        && actual.BoundJobjSourceOffset == null;
}
