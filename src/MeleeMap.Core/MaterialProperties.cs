using System.Buffers.Binary;
using System.Text.Json.Serialization;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record EditableTextureProperties(int Index, int Offset, int ColorOperation, int AlphaOperation,
    int LightmapFlags, int TexCoord, int CoordinateType, float Blend, bool CanEditBlend);
public sealed record EditableMaterialProperties(string Id, int MobjOffset, int MaterialOffset, int? TextureOffset, int? PixelOffset,
    int[] Diffuse, float Alpha, float? TextureBlend, bool CanEditDiffuse, bool CanEditAlpha, bool CanEditBlend,
    bool AlphaAffectsVisibility, bool UseVertexColor, bool CanToggleVertexColor,
    int AlphaSource, uint RenderFlags, uint EditableRenderFlagsMask, int TransparencyMode,
    int[] Ambient, int[] Specular, float Shininess, EditableTextureProperties[] Textures);
public sealed record MaterialPropertyEdit([property: JsonRequired] string Id, int[]? Diffuse = null,
    float? Alpha = null, float? TextureBlend = null, bool? UseVertexColor = null,
    uint? RenderFlags = null, int? TransparencyMode = null, int? AlphaSource = null,
    int[]? Ambient = null, int[]? Specular = null, float? Shininess = null,
    float[]? TextureBlends = null);
public sealed record MaterialPropertyEdits([property: JsonRequired] int ProtocolVersion,
    [property: JsonRequired] MaterialPropertyEdit[] Materials);
public sealed record MaterialPropertyWrite(byte[] Bytes, Dictionary<string, int> Bindings,
    Dictionary<int, byte[]> Blocks);

/// <summary>Copy-on-write static material properties, leaving source/shared/animated data intact.</summary>
public static class MaterialProperties
{
    // Texture bits, source selectors and undocumented bits remain protected.
    public const uint EditableRenderFlagsMask = 0xBF00100C;

    public static EditableMaterialProperties[] Select(ArchiveLayout archive, ModelIdentitySnapshot identity)
    {
        var r = new ArchiveDataReader(archive);
        var result = new List<EditableMaterialProperties>();
        foreach (var target in ModelEditing.SelectAll(archive, identity).Where(t => !t.PositionsOnly))
        {
            int mobj = r.Pointer(target.DobjOffset + 8)!.Value;
            int? material = r.Pointer(mobj + 12), texture = r.Pointer(mobj + 8), pixel = r.Pointer(mobj + 20);
            if (!material.HasValue) continue;
            r.Check(material.Value, 20);
            var textures = new List<EditableTextureProperties>(); var seen = new HashSet<int>();
            int? current = texture; bool supportedTextures = true;
            while (current.HasValue)
            {
                int t = current.Value;
                if (textures.Count >= 8 || !seen.Add(t) || r.Pointer(t) != null) { supportedTextures = false; break; }
                int source = r.Int(t + 12), flags = r.Int(t + 0x40), coordinateType = flags & 15;
                float blend = r.Float(t + 0x44);
                if (source is < 4 or > 5 || (flags & 0x01000000) != 0 || coordinateType is < 0 or > 1
                    || !float.IsFinite(blend) || blend < 0 || blend > 1)
                { supportedTextures = false; break; }
                int colorOperation = (flags >> 16) & 15, alphaOperation = (flags >> 20) & 15;
                textures.Add(new(textures.Count, t, colorOperation, alphaOperation, flags & 0x1F0,
                    source - 4, coordinateType, blend, colorOperation == 3 || alphaOperation == 2));
                current = r.Pointer(t + 4);
            }
            if (!supportedTextures) continue;
            float sourceAlpha = r.Float(material.Value + 12);
            float shininess = r.Float(material.Value + 16);
            float? sourceBlend = textures.Count > 0 ? textures[0].Blend : null;
            if (!float.IsFinite(sourceAlpha) || sourceAlpha < 0 || sourceAlpha > 1
                || !float.IsFinite(shininess) || shininess < 0 || shininess > 128) continue;
            var alpha = AlphaPreview.Read(archive, mobj);
            bool useVertexColor = (r.Int(mobj + 4) & 2) != 0;
            bool hasVertexColors = Gx.GxMeshDecoder.Decode(archive, target.PobjOffset).Colors0 != null;
            uint renderFlags = unchecked((uint)r.Int(mobj + 4));
            int transparency = alpha.BlendMode switch
            {
                0 when (renderFlags & (1u << 30)) == 0 => 0,
                1 when alpha.SourceFactor == 4 && alpha.DestinationFactor == 5 => 1,
                1 when alpha.SourceFactor is 1 or 4 && alpha.DestinationFactor == 1 => 2,
                3 => 3,
                _ => 4
            };
            result.Add(new(target.Id, mobj, material.Value, texture, pixel,
                Enumerable.Range(0, 3).Select(i => (int)r.Byte(material.Value + 4 + i)).ToArray(),
                sourceAlpha, sourceBlend,
                (r.Int(mobj + 4) & 2) == 0, !alpha.Vertex || alpha.MultiplyMaterial,
                textures.FirstOrDefault()?.CanEditBlend == true,
                alpha.BlendMode == 1 || alpha.Compare0 != 7 || alpha.Compare1 != 7,
                useVertexColor, hasVertexColors, (int)((renderFlags >> 13) & 3),
                renderFlags, EditableRenderFlagsMask, transparency,
                Enumerable.Range(0, 3).Select(i => (int)r.Byte(material.Value + i)).ToArray(),
                Enumerable.Range(0, 3).Select(i => (int)r.Byte(material.Value + 8 + i)).ToArray(),
                shininess, textures.ToArray()));
        }
        return result.ToArray();
    }

    public static MaterialPropertyWrite Write(ArchiveLayout source, ArchiveLayout current,
        ModelIdentitySnapshot identity, ModelIdentitySnapshot bindingIdentity,
        MaterialPropertyEdits edits, string[] declaredIds,
        IReadOnlyDictionary<string, string?> assignments)
    {
        Require(edits.ProtocolVersion == SessionExtractor.ProtocolVersion && edits.Materials is { Length: > 0 },
            "MATERIAL_EDIT_FORMAT", "Material edits require the current protocol and at least one material.");
        var eligible = Select(source, identity).ToDictionary(m => m.Id);
        var seen = new HashSet<string>();
        foreach (var edit in edits.Materials)
        {
            Require(edit != null && edit.Id != null && seen.Add(edit.Id) && declaredIds.Contains(edit.Id)
                && eligible.ContainsKey(edit.Id), "MATERIAL_EDIT_TARGET", "Material is unsupported, animated, duplicated, or absent from this session.");
            var target = eligible[edit!.Id];
            Require(edit.Diffuse != null || edit.Alpha.HasValue || edit.TextureBlend.HasValue || edit.UseVertexColor.HasValue
                || edit.RenderFlags.HasValue || edit.TransparencyMode.HasValue || edit.AlphaSource.HasValue
                || edit.Ambient != null || edit.Specular != null || edit.Shininess.HasValue || edit.TextureBlends != null,
                "MATERIAL_EDIT_FORMAT", "A material edit must change at least one supported property.");
            bool materialMode = edit.UseVertexColor == false || !target.UseVertexColor;
            bool materialAlphaMode = materialMode || edit.AlphaSource is 1 or 3 || target.CanEditAlpha;
            Require(edit.UseVertexColor == null || (target.CanToggleVertexColor
                && edit.UseVertexColor.Value != target.UseVertexColor),
                "MATERIAL_VERTEX_COLOR", "Vertex-color mode cannot be changed for this material.");
            Require(edit.Diffuse == null || ((target.CanEditDiffuse || materialMode) && edit.Diffuse.Length == 3 && edit.Diffuse.All(v => v is >= 0 and <= 255)),
                "MATERIAL_DIFFUSE", "Diffuse edits require three RGB bytes and a material using diffuse color.");
            Require(edit.Ambient == null || (edit.Ambient.Length == 3 && edit.Ambient.All(v => v is >= 0 and <= 255)),
                "MATERIAL_AMBIENT", "Ambient edits require three RGB bytes.");
            Require(edit.Specular == null || (edit.Specular.Length == 3 && edit.Specular.All(v => v is >= 0 and <= 255)),
                "MATERIAL_SPECULAR", "Specular edits require three RGB bytes.");
            Require(edit.Shininess == null || (float.IsFinite(edit.Shininess.Value) && edit.Shininess is >= 0 and <= 128),
                "MATERIAL_SHININESS", "Shininess must be finite and between zero and 128.");
            Require(edit.Alpha == null || (materialAlphaMode && float.IsFinite(edit.Alpha.Value) && edit.Alpha >= 0 && edit.Alpha <= 1),
                "MATERIAL_ALPHA", "Material alpha must be finite, between zero and one, and used by this material.");
            Require(edit.TextureBlend == null || (target.CanEditBlend && float.IsFinite(edit.TextureBlend.Value)
                && edit.TextureBlend >= 0 && edit.TextureBlend <= 1), "MATERIAL_BLEND", "Texture blend requires a supported blend operation and a finite value between zero and one.");
            Require(edit.TextureBlend == null || edit.TextureBlends == null, "MATERIAL_BLEND",
                "Use either the legacy first-layer blend or the per-layer blend list, not both.");
            Require(edit.TextureBlends == null || (edit.TextureBlends.Length == target.Textures.Length
                && edit.TextureBlends.Select((value, index) => float.IsFinite(value) && value >= 0 && value <= 1
                    && (!BitConverter.SingleToInt32Bits(value).Equals(BitConverter.SingleToInt32Bits(target.Textures[index].Blend))
                        ? target.Textures[index].CanEditBlend : true)).All(value => value)),
                "MATERIAL_BLEND", "Texture blends must match the source layer count and only change supported blend operations.");
            Require(edit.RenderFlags == null || ((edit.RenderFlags.Value ^ target.RenderFlags) & ~target.EditableRenderFlagsMask) == 0,
                "MATERIAL_RENDER_FLAGS", "The material edit changes protected render flags.");
            Require(edit.TransparencyMode == null || edit.TransparencyMode is >= 0 and <= 3,
                "MATERIAL_TRANSPARENCY", "Transparency mode must be opaque, alpha blend, additive, or subtractive.");
            Require(edit.AlphaSource == null || (edit.AlphaSource is >= 0 and <= 3
                && (edit.AlphaSource is 0 or 1 || target.CanToggleVertexColor)),
                "MATERIAL_ALPHA_SOURCE", "Alpha source must be compatibility, material, vertex, or material times vertex.");
            Require(assignments.Values.Contains(edit.Id), "MATERIAL_NOT_USED", "Edited material is no longer used by exported geometry. Assign a supported stage material or retain the original topology.");
        }
        using var data = new MemoryStream(); data.Write(current.Bytes.AsSpan(32, current.DataSize));
        int oldCount = current.Read(8);
        var relocations = Enumerable.Range(0, oldCount).Select(i => current.Read(32 + current.DataSize + i * 4)).ToList();
        var blocks = new Dictionary<int, byte[]>();
        int Copy(int offset, int size, Action<byte[]> change)
        {
            while (data.Position % 4 != 0) data.WriteByte(0);
            int at = checked((int)data.Position);
            var block = source.Bytes.AsSpan(32 + offset, size).ToArray();
            change(block); data.Write(block); blocks.Add(at, block);
            foreach (int field in source.Pointers.Keys.Where(f => f >= offset && f < offset + size))
                relocations.Add(at + field - offset);
            return at;
        }
        var materials = new Dictionary<string, int>();
        foreach (var edit in edits.Materials)
        {
            var original = eligible[edit.Id];
            uint finalFlags = edit.RenderFlags ?? original.RenderFlags;
            if (edit.UseVertexColor.HasValue)
                finalFlags = (finalFlags & ~3u) | (edit.UseVertexColor.Value ? 2u : 1u);
            if (edit.AlphaSource.HasValue)
                finalFlags = (finalFlags & ~(3u << 13)) | ((uint)edit.AlphaSource.Value << 13);
            if (edit.TransparencyMode.HasValue)
                finalFlags = edit.TransparencyMode.Value == 0 ? finalFlags & ~(1u << 30) : finalFlags | (1u << 30);
            int mat = Copy(original.MaterialOffset, 20, block =>
            {
                if (edit.Ambient != null)
                    for (int i = 0; i < 3; i++) block[i] = (byte)edit.Ambient[i];
                if (edit.Diffuse != null)
                    for (int i = 0; i < 3; i++) block[4 + i] = (byte)edit.Diffuse[i];
                if (edit.Specular != null)
                    for (int i = 0; i < 3; i++) block[8 + i] = (byte)edit.Specular[i];
                if (edit.Alpha.HasValue) Put(block, 12, BitConverter.SingleToInt32Bits(edit.Alpha.Value));
                if (edit.Shininess.HasValue) Put(block, 16, BitConverter.SingleToInt32Bits(edit.Shininess.Value));
            });
            int? tex = original.TextureOffset;
            float[]? blends = edit.TextureBlends;
            if (edit.TextureBlend.HasValue)
            {
                blends = original.Textures.Select(texture => texture.Blend).ToArray();
                blends[0] = edit.TextureBlend.Value;
            }
            if (blends != null)
            {
                int? next = null;
                for (int i = original.Textures.Length - 1; i >= 0; i--)
                {
                    int nextValue = next ?? 0, index = i;
                    next = Copy(original.Textures[i].Offset, 0x5C, block =>
                    {
                        Put(block, 4, nextValue);
                        Put(block, 0x44, BitConverter.SingleToInt32Bits(blends[index]));
                    });
                }
                tex = next;
            }
            bool depthFlagsChanged = edit.RenderFlags.HasValue
                && ((edit.RenderFlags.Value ^ original.RenderFlags) & ((1u << 27) | (1u << 29))) != 0;
            int? pixel = original.PixelOffset;
            if (edit.TransparencyMode.HasValue || (depthFlagsChanged && pixel.HasValue))
            {
                byte[] DefaultPixel()
                {
                    byte[] value = new byte[12];
                    value[0] = (byte)(1 | 16 | ((finalFlags & (1u << 29)) == 0 ? 32 : 0)
                        | (((finalFlags & (1u << 29)) != 0 || (finalFlags & (1u << 30)) == 0) ? 8 : 0));
                    value[4] = (byte)(((finalFlags & (1u << 30)) != 0) ? 1 : 0);
                    value[5] = 4; value[6] = 5; value[7] = 15;
                    value[8] = (byte)(((finalFlags & (1u << 27)) != 0) ? 7 : 3);
                    value[9] = value[11] = (byte)(((finalFlags & (1u << 29)) == 0
                        && (finalFlags & (1u << 30)) != 0) ? 4 : 7);
                    return value;
                }
                while (data.Position % 4 != 0) data.WriteByte(0);
                int at = checked((int)data.Position);
                byte[] block = pixel.HasValue
                    ? source.Bytes.AsSpan(32 + pixel.Value, 12).ToArray()
                    : DefaultPixel();
                if (depthFlagsChanged)
                {
                    block[0] = (byte)((block[0] & ~32) | ((finalFlags & (1u << 29)) == 0 ? 32 : 0));
                    block[8] = (byte)(((finalFlags & (1u << 27)) != 0) ? 7 : 3);
                }
                if (edit.TransparencyMode.HasValue)
                {
                    (block[4], block[5], block[6]) = edit.TransparencyMode.Value switch
                    {
                        0 => ((byte)0, (byte)4, (byte)5),
                        1 => ((byte)1, (byte)4, (byte)5),
                        2 => ((byte)1, (byte)1, (byte)1),
                        _ => ((byte)3, (byte)1, (byte)1)
                    };
                }
                data.Write(block); blocks.Add(at, block); pixel = at;
            }
            int mobj = Copy(original.MobjOffset, 24, block =>
            {
                Put(block, 4, unchecked((int)finalFlags));
                Put(block, 12, mat);
                if (tex.HasValue) Put(block, 8, tex.Value);
                if (pixel.HasValue) Put(block, 20, pixel.Value);
            });
            if (pixel.HasValue && !source.Pointers.ContainsKey(original.MobjOffset + 20))
                relocations.Add(mobj + 20);
            materials.Add(edit.Id, mobj);
        }
        byte[] payload = data.ToArray(); var bindings = new Dictionary<string, int>(); var fields = new HashSet<int>();
        var bindingNodes = bindingIdentity.Nodes.ToDictionary(node => node.Id);
        foreach (var target in ModelEditing.SelectAll(source, identity))
            if (assignments.TryGetValue(target.Id, out string? id) && id != null && materials.TryGetValue(id, out int mobj))
            {
                var pobj = bindingNodes[target.Id];
                int dobjOffset = bindingNodes[pobj.OwnerId!].SourceOffset;
                Put(payload, dobjOffset + 8, mobj); bindings.Add(target.Id, mobj);
                fields.Add(dobjOffset + 8);
            }
        using var stream = new MemoryStream(); stream.Write(current.Bytes.AsSpan(0, 32)); stream.Write(payload);
        foreach (int field in relocations) { byte[] value = new byte[4]; Put(value, 0, field); stream.Write(value); }
        stream.Write(current.Bytes.AsSpan(32 + current.DataSize + oldCount * 4));
        byte[] bytes = stream.ToArray(); Put(bytes, 0, bytes.Length); Put(bytes, 4, payload.Length); Put(bytes, 8, relocations.Count);
        for (int i = 0; i < current.DataSize; i++)
            Require(fields.Any(f => i >= f && i < f + 4) || bytes[32 + i] == current.Bytes[32 + i],
                "MATERIAL_PRESERVATION", "Material writing changed unrelated source data.");
        var output = new MaterialPropertyWrite(bytes, bindings, blocks);
        Verify(new ArchiveLayout(bytes), bindingIdentity, output);
        return output;
    }

    public static void Verify(ArchiveLayout archive, ModelIdentitySnapshot bindingIdentity,
        MaterialPropertyWrite expected)
    {
        var byId = bindingIdentity.Nodes.ToDictionary(n => n.Id); var r = new ArchiveDataReader(archive);
        foreach (var (id, mobj) in expected.Bindings)
        {
            int dobj = byId[byId[id].OwnerId!].SourceOffset;
            int? actual = r.Pointer(dobj + 8);
            Require(actual == mobj, "MATERIAL_WRITE_MISMATCH",
                $"Edited material binding changed after export (DOBJ 0x{dobj:X}, expected 0x{mobj:X}, actual {(actual.HasValue ? $"0x{actual.Value:X}" : "null")}).");
        }
        foreach (var (offset, block) in expected.Blocks)
            Require(archive.Bytes.AsSpan(32 + offset, block.Length).SequenceEqual(block),
                "MATERIAL_WRITE_MISMATCH", "Edited material properties changed after export.");
    }
    private static void Put(byte[] bytes, int offset, int value) => BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
}
