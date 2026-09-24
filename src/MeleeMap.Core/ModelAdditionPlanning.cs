using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record PlannedModelAdditionChunk(string AdditionId, string PartId,
    int ChunkIndex, string TargetId, string Placement, string MaterialId,
    MeshData Mesh);

public sealed record PlannedModelAdditions(ValidatedModelAdditionBatch Source,
    IReadOnlyList<PlannedModelAdditionChunk> Chunks);

/// <summary>Compiles deterministic addition chunks before archive allocation.</summary>
public static class ModelAdditionPlanner
{
    public static PlannedModelAdditions Plan(ValidatedModelAdditionBatch batch)
    {
        var chunks = new List<PlannedModelAdditionChunk>();
        foreach (var addition in batch.Edits.Additions)
        {
            var target = batch.Targets[addition.TargetJobjId];
            foreach (var part in addition.Parts)
            {
                int chunkIndex = 0;
                foreach (var range in NondegenerateTriangles(part)
                             .Chunk(ModelAdditionArchiveWriter.MaxTrianglesPerChunk))
                    chunks.Add(new(addition.Id, part.Id, chunkIndex++,
                        addition.TargetJobjId, target.Placement, part.MaterialId,
                        Mesh(part, range)));
            }
        }
        Require(chunks.Count > 0, "MODEL_ADDITION_EMPTY",
            "No nondegenerate addition geometry remains.");
        return new(batch, chunks.AsReadOnly());
    }

    private static List<int> NondegenerateTriangles(ModelAdditionPart part)
    {
        var result = new List<int>();
        for (int triangle = 0; triangle < part.TriangleIndices.Length / 3; triangle++)
        {
            int corner = triangle * 3;
            var a = part.Positions[part.TriangleIndices[corner]];
            var b = part.Positions[part.TriangleIndices[corner + 1]];
            var c = part.Positions[part.TriangleIndices[corner + 2]];
            double ux = (double)b.X - a.X, uy = (double)b.Y - a.Y, uz = (double)b.Z - a.Z;
            double vx = (double)c.X - a.X, vy = (double)c.Y - a.Y, vz = (double)c.Z - a.Z;
            double nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
            if (nx * nx + ny * ny + nz * nz > 0) result.Add(triangle);
        }
        return result;
    }

    private static MeshData Mesh(ModelAdditionPart part, int[] triangles)
    {
        var positions = new Vector3Data[triangles.Length * 3];
        var normals = new Vector3Data[positions.Length];
        var texCoords = part.TexCoords0 == null ? null : new Vector2Data[positions.Length];
        for (int triangle = 0; triangle < triangles.Length; triangle++)
        {
            int sourceCorner = triangles[triangle] * 3;
            for (int corner = 0; corner < 3; corner++)
            {
                int output = triangle * 3 + corner;
                positions[output] = part.Positions[part.TriangleIndices[sourceCorner + corner]];
                normals[output] = part.CornerNormals[sourceCorner + corner];
                if (texCoords != null) texCoords[output] = part.TexCoords0![sourceCorner + corner];
            }
        }
        return new(positions, normals, Enumerable.Range(0, positions.Length).ToArray(),
            TexCoords0: texCoords);
    }
}
