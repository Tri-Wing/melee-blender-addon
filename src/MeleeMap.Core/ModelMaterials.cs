using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ModelMaterial(string Id, string Name, int MobjOffset, bool UsesUv);

/// <summary>Static opaque source materials whose vertex inputs the replacement writer can provide.</summary>
public static class ModelMaterials
{
    public static ModelMaterial[] Select(ArchiveLayout archive, IEnumerable<EditableModel> targets)
    {
        var r = new ArchiveDataReader(archive); var result = new List<ModelMaterial>();
        foreach (var target in targets.Where(t => !t.PositionsOnly))
        {
            int mobj = r.Pointer(target.DobjOffset + 8)!.Value;
            int flags = r.Int(mobj + 4);
            // No vertex-color/alpha inputs, translucency, toon, or special render passes.
            if ((flags & ~0x2FFD) != 0 || (flags & 0x1000) != 0) continue;
            int? texture = r.Pointer(mobj + 8);
            if (texture.HasValue)
            {
                int t = texture.Value;
                // A single regular UV0 texture; retain its original transform/TEV state.
                if (r.Pointer(t) != null || r.Pointer(t + 4) != null || r.Int(t + 12) != 4
                    || (r.Int(t + 0x40) & 0x0100000F) != 0) continue;
            }
            result.Add(new(target.Id,
                $"Stage G{target.GroupIndex:D3} J{target.JobjIndex:D3} D{target.DobjIndex:D3}", mobj, texture.HasValue));
        }
        return result.ToArray();
    }

    public static ModelMaterial Resolve(ArchiveLayout archive, IEnumerable<EditableModel> targets, string id)
    {
        var material = Select(archive, targets).FirstOrDefault(m => m.Id == id);
        Require(material != null, "MODEL_MATERIAL", "Assigned material is not a supported static opaque stage material.");
        return material!;
    }
}
