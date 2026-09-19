namespace MeleeMap.Core;

/// <summary>Static raster alpha and pixel processing state; preview only.</summary>
public sealed record AlphaPreview(float Material, bool Vertex, bool MultiplyMaterial,
    int TextureOperation, float TextureBlend, int BlendMode, int SourceFactor, int DestinationFactor,
    int Compare0, int Reference0, int Operation, int Compare1, int Reference1, string? Warning)
{
    public static AlphaPreview Read(ArchiveLayout archive, int mobj)
    {
        var r = new ArchiveDataReader(archive);
        int flags = r.Int(mobj + 4);
        int? mat = r.Pointer(mobj + 12), texture = r.Pointer(mobj + 8), pe = r.Pointer(mobj + 20);
        float alpha = mat.HasValue ? r.Float(mat.Value + 12) : 1;
        int mode = (flags >> 13) & 3;
        bool vertex = mode is 2 or 3 || (mode == 0 && (flags & 7) == 2);
        bool multiply = mode == 3;
        int blendMode = (flags & 0x40000000) != 0 ? 1 : 0, src = 4, dst = 5;
        int comp = (flags & 0x60000000) == 0x40000000 ? 4 : 7;
        int comp0 = comp, comp1 = comp, ref0 = 0, ref1 = 0, op = 0;
        if (pe.HasValue)
        {
            int p = pe.Value;
            blendMode = r.Byte(p + 4); src = r.Byte(p + 5); dst = r.Byte(p + 6);
            ref0 = r.Byte(p + 1); ref1 = r.Byte(p + 2);
            comp0 = r.Byte(p + 9); op = r.Byte(p + 10); comp1 = r.Byte(p + 11);
        }
        var warnings = new List<string>();
        bool supported = blendMode == 0 || (blendMode == 1 &&
            ((src == 4 && dst == 5) || ((src == 1 || src == 4) && dst == 1) || (src == 1 && dst == 0)));
        if (!supported) warnings.Add("This framebuffer blend mode is approximated with standard alpha blending.");
        if (!float.IsFinite(alpha)) { alpha = 1; warnings.Add("Invalid material alpha; preview uses opaque alpha."); }
        int texOp = texture.HasValue ? (r.Int(texture.Value + 0x40) >> 20) & 15 : 0;
        float texBlend = texture.HasValue ? r.Float(texture.Value + 0x44) : 1;
        if (!float.IsFinite(texBlend)) texBlend = 1;
        if (texOp > 7) warnings.Add("Unsupported texture alpha operation is omitted.");
        if (texture.HasValue && r.Pointer(texture.Value + 0x58) != null)
            warnings.Add("Custom TEV alpha routing is approximated with the base texture alpha operation.");
        if (comp0 > 7 || comp1 > 7 || op > 3)
        {
            warnings.Add("Unsupported alpha comparison is omitted.");
            comp0 = comp1 = 7; op = 0;
        }
        return new(Math.Clamp(alpha, 0, 1), vertex, multiply, texOp, Math.Clamp(texBlend, 0, 1),
            blendMode, src, dst, comp0, ref0, op, comp1, ref1,
            warnings.Count == 0 ? null : string.Join(" ", warnings));
    }
}
