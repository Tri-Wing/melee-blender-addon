using System.Buffers.Binary;
using HSDRaw.GX;
using HSDRaw.Tools;
using MeleeMap.Core.Gx;
using static MeleeMap.Core.ArchiveLayout;

namespace MeleeMap.Core;

public sealed record ModelAdditionChunkWrite(string AdditionId, string PartId, int ChunkIndex,
    string TargetId, string Placement, int DobjOffset, int PobjOffset, int MaterialOffset,
    int TriangleCount, MeshData Mesh);
public sealed record ModelAdditionJobjWrite(string AdditionId, string TargetId,
    string AnchorJobjId, int JobjOffset, int FirstDobjOffset);
public sealed record ModelAdditionMaterialWrite(string MaterialId, int MobjOffset, int ColorOffset,
    int? TobjOffset, int? LodOffset, int? ImageDescriptorOffset,
    int WrapS, int WrapT, int MinFilter, int MagFilter, byte Red, byte Green, byte Blue,
    string Preset);
public sealed record ModelAdditionImageWrite(string ImageId, int DescriptorOffset, int DataOffset,
    int EncodedLength, int Width, int Height, byte[] Pixels);
public sealed record ModelAdditionWrite(byte[] Bytes, ModelAdditionChunkWrite[] Chunks,
    ModelAdditionJobjWrite[] Jobjs,
    ModelAdditionMaterialWrite[] Materials, ModelAdditionImageWrite[] Images,
    int[] PatchedSourceFields);

/// <summary>
/// Appends ordinary rigid DOBJ/POBJ geometry and an opaque constant or RGBA8
/// material preset while retaining every original descriptor offset.
/// </summary>
public static class ModelAdditionArchiveWriter
{
    public const int MaxTrianglesPerChunk = 14_000;
    private const int RenderConstant = 1;
    private const int RenderDiffuse = 1 << 2;
    private const int RenderTex0 = 1 << 4;
    private const int JobjLighting = 1 << 7;
    private const int JobjOpaque = 1 << 18;
    private const int JobjRootOpaque = 1 << 28;
    private const int TobjLightmapDiffuse = 1 << 4;
    private const int TobjColormapModulate = 4 << 16;

    private readonly record struct ImageKey(int Width, int Height, string Sha256);
    private readonly record struct MaterialKey(byte R, byte G, byte B, string Preset,
        int ImageDescriptor, int WrapS, int WrapT, int MinFilter, int MagFilter);
    private sealed record BuiltImage(int Descriptor, int Data, byte[] Encoded,
        ValidatedModelAdditionImage Source);
    private sealed record BuiltMaterial(int Mobj, int Color, int? Tobj, int? Lod,
        int? ImageDescriptor);

    public static ModelAdditionWrite Write(ArchiveLayout source, ModelIdentitySnapshot identity,
        ValidatedModelAdditionBatch batch)
    {
        var builder = new ArchiveMutationBuilder(source);
        return Write(builder, source, identity, ModelAdditionPlanner.Plan(batch));
    }

    internal static ModelAdditionWrite Write(ArchiveMutationBuilder builder,
        ArchiveLayout additionBase, ModelIdentitySnapshot identity,
        PlannedModelAdditions planned)
    {
        var batch = planned.Source;
        var source = additionBase;
        var sourceReader = new ArchiveDataReader(source);
        var nodes = identity.Nodes;
        var byId = nodes.ToDictionary(node => node.Id);

        int Append(byte[] value, int alignment = 32) =>
            builder.AppendAligned(value, alignment);

        var imageCache = new Dictionary<ImageKey, BuiltImage>();
        var imagesById = new Dictionary<string, BuiltImage>(StringComparer.Ordinal);
        var imageWrites = new List<ModelAdditionImageWrite>();
        foreach (var definition in batch.Edits.Images)
        {
            var validated = batch.Images[definition.Id];
            var key = new ImageKey(definition.Width, definition.Height, definition.Sha256);
            if (!imageCache.TryGetValue(key, out var built))
            {
                byte[] bgra = RgbaBgra(validated.Pixels);
                byte[] encoded = GXImageConverter.EncodeImage(bgra, definition.Width, definition.Height,
                    GXTexFmt.RGBA8, GXTlutFmt.RGB565, out byte[] palette);
                Require(palette.Length == 0 && encoded.Length == EncodedRgba8Length(definition.Width, definition.Height),
                    "MODEL_ADDITION_TEXTURE", "RGBA8 encoder returned an unexpected payload.");
                int pixels = Append(encoded);
                byte[] image = new byte[0x18];
                Put(image, 0, pixels);
                Short(image, 4, definition.Width);
                Short(image, 6, definition.Height);
                Put(image, 8, (int)GXTexFmt.RGBA8);
                int descriptor = Append(image);
                builder.SetPointer(descriptor, pixels, "model-addition");
                built = new(descriptor, pixels, encoded, validated);
                imageCache.Add(key, built);
            }
            else
                Require(built.Source.Pixels.SequenceEqual(validated.Pixels), "MODEL_ADDITION_IMAGE_HASH",
                    "Distinct image pixels unexpectedly share an image identity.");
            imagesById.Add(definition.Id, built);
            imageWrites.Add(new(definition.Id, built.Descriptor, built.Data, built.Encoded.Length,
                definition.Width, definition.Height, validated.Pixels));
        }

        var materialCache = new Dictionary<MaterialKey, BuiltMaterial>();
        var materialsById = new Dictionary<string, BuiltMaterial>(StringComparer.Ordinal);
        var materialWrites = new List<ModelAdditionMaterialWrite>();
        foreach (var definition in batch.Edits.Materials)
        {
            BuiltImage? image = definition.ImageId == null ? null : imagesById[definition.ImageId];
            var key = new MaterialKey(Channel(definition.BaseColor.R), Channel(definition.BaseColor.G),
                Channel(definition.BaseColor.B), definition.Preset, image?.Descriptor ?? -1, Wrap(definition.WrapS),
                Wrap(definition.WrapT), Filter(definition.MinFilter), Filter(definition.MagFilter));
            if (!materialCache.TryGetValue(key, out var built))
            {
                byte[] color = new byte[0x14];
                bool diffuse = key.Preset == ModelAdditionEditing.DiffuseMaterialPreset;
                color[0] = diffuse ? (byte)(key.R / 2) : key.R;
                color[1] = diffuse ? (byte)(key.G / 2) : key.G;
                color[2] = diffuse ? (byte)(key.B / 2) : key.B;
                color[4] = key.R;
                color[5] = key.G;
                color[6] = key.B;
                color[3] = color[7] = color[0x0B] = 255;
                Float(color, 0x0C, 1); // Both supported presets render opaque.
                Float(color, 0x10, 0); // No specular shininess.
                int colorOffset = Append(color);
                int? tobjOffset = null, lodOffset = null;
                if (image != null)
                {
                    byte[] lod = new byte[0x10];
                    Put(lod, 0, key.MinFilter);
                    lodOffset = Append(lod);
                    byte[] tobj = new byte[0x5C];
                    Put(tobj, 8, 0); // GX_TEXMAP0
                    Put(tobj, 0x0C, 4); // GX_TG_TEX0
                    Float(tobj, 0x1C, 1);
                    Float(tobj, 0x20, 1);
                    Float(tobj, 0x24, 1);
                    Put(tobj, 0x34, key.WrapS);
                    Put(tobj, 0x38, key.WrapT);
                    tobj[0x3C] = tobj[0x3D] = 1;
                    Put(tobj, 0x40, TobjLightmapDiffuse | TobjColormapModulate);
                    Float(tobj, 0x44, 1);
                    Put(tobj, 0x48, key.MagFilter);
                    Put(tobj, 0x4C, image.Descriptor);
                    Put(tobj, 0x54, lodOffset.Value);
                    tobjOffset = Append(tobj);
                    builder.SetPointer(tobjOffset.Value + 0x4C,
                        image.Descriptor, "model-addition");
                    builder.SetPointer(tobjOffset.Value + 0x54,
                        lodOffset.Value, "model-addition");
                }
                byte[] mobj = new byte[0x18];
                Put(mobj, 4, (diffuse ? RenderDiffuse : RenderConstant)
                    | (image == null ? 0 : RenderTex0));
                if (tobjOffset.HasValue) Put(mobj, 8, tobjOffset.Value);
                Put(mobj, 0x0C, colorOffset);
                int mobjOffset = Append(mobj);
                if (tobjOffset.HasValue)
                    builder.SetPointer(mobjOffset + 8, tobjOffset.Value,
                        "model-addition");
                builder.SetPointer(mobjOffset + 0x0C, colorOffset,
                    "model-addition");
                built = new(mobjOffset, colorOffset, tobjOffset, lodOffset, image?.Descriptor);
                materialCache.Add(key, built);
            }
            materialsById.Add(definition.Id, built);
            materialWrites.Add(new(definition.Id, built.Mobj, built.Color, built.Tobj,
                built.Lod, built.ImageDescriptor, key.WrapS, key.WrapT, key.MinFilter,
                key.MagFilter, key.R, key.G, key.B, key.Preset));
        }

        var pending = new List<(string AdditionId, string PartId, int ChunkIndex,
            string TargetId, string Placement, int Dobj, int Pobj, int Material, MeshData Mesh)>();
        foreach (var chunk in planned.Chunks)
        {
            int material = materialsById[chunk.MaterialId].Mobj;
            var mesh = chunk.Mesh;
            int attrs = Append(Attributes(mesh.TexCoords0 != null));
            byte[] display = Display(mesh);
            int dl = Append(display);
            byte[] pobj = new byte[0x18];
            Put(pobj, 8, attrs);
            Short(pobj, 0x0C, 0x4001); // Back-face culling, HSD_MTX_RIGID.
            Short(pobj, 0x0E, display.Length / 32);
            Put(pobj, 0x10, dl);
            int pobjOffset = Append(pobj);
            builder.SetPointer(pobjOffset + 8, attrs, "model-addition");
            builder.SetPointer(pobjOffset + 0x10, dl, "model-addition");
            byte[] dobj = new byte[0x10];
            Put(dobj, 8, material);
            Put(dobj, 0x0C, pobjOffset);
            int dobjOffset = Append(dobj);
            builder.SetPointer(dobjOffset + 8, material, "model-addition");
            builder.SetPointer(dobjOffset + 0x0C, pobjOffset, "model-addition");
            pending.Add((chunk.AdditionId, chunk.PartId, chunk.ChunkIndex,
                chunk.TargetId, chunk.Placement, dobjOffset, pobjOffset, material, mesh));
        }

        var jobjWrites = new List<ModelAdditionJobjWrite>();
        foreach (var addition in batch.Edits.Additions.Where(edit =>
            batch.Targets[edit.TargetJobjId].Placement == ModelAddition.NewJobjChainPlacement))
        {
            var chunks = pending.Where(chunk => chunk.AdditionId == addition.Id).ToArray();
            Require(chunks.Length > 0, "MODEL_ADDITION_EMPTY", "A new JOBJ has no geometry.");
            var target = batch.Targets[addition.TargetJobjId];
            byte[] jobj = new byte[0x40];
            Put(jobj, 4, JobjLighting | JobjOpaque);
            Float(jobj, 0x20, 1);
            Float(jobj, 0x24, 1);
            Float(jobj, 0x28, 1);
            int jobjOffset = Append(jobj);
            ModelGraphEditor.SetGeneratedDobjList(builder, jobjOffset,
                chunks.Select(chunk => chunk.Dobj).ToArray(), "model-addition");
            jobjWrites.Add(new(addition.Id, addition.TargetJobjId,
                target.AnchorJobjId, jobjOffset, chunks[0].Dobj));
        }

        var patchedFields = new List<int>();

        foreach (var group in pending.GroupBy(chunk => chunk.TargetId))
        {
            var definition = batch.Targets[group.Key];
            var chunks = group.ToArray();
            if (definition.Placement == ModelAddition.ExistingJobjPlacement)
            {
                int sourceField = ModelGraphEditor.AppendDobjList(builder, identity,
                    group.Key, chunks.Select(chunk => chunk.Dobj).ToArray(), "model-addition");
                patchedFields.Add(sourceField);
                continue;
            }

            var anchor = byId[definition.AnchorJobjId];
            var children = nodes.Where(node => node.OwnerId == anchor.Id && node.Kind.EndsWith("jobj"))
                .OrderBy(node => node.Index).ToArray();
            var additions = jobjWrites.Where(jobj => jobj.TargetId == group.Key).ToArray();
            Require(additions.Length > 0, "MODEL_ADDITION_JOBJ", "New-chain target has no generated JOBJ.");
            int childField = ModelGraphEditor.AppendJobjChildren(builder, identity,
                definition.AnchorJobjId, additions.Select(addition => addition.JobjOffset).ToArray(),
                "model-addition");
            patchedFields.Add(childField);
            int flagsField = anchor.SourceOffset + 4;
            int flags = sourceReader.Int(flagsField);
            if ((flags & JobjRootOpaque) == 0)
            {
                Require(children.Length == 0, "MODEL_ADDITION_JOBJ_FLAGS",
                    "Cannot enable opaque traversal for an existing child hierarchy.");
                builder.PermitSourcePatch(flagsField, 4, "model-addition");
                builder.PatchInt32(flagsField, flags | JobjRootOpaque,
                    "model-addition");
                patchedFields.Add(flagsField);
            }
        }

        byte[] output = builder.Build();

        var chunksWritten = pending.Select(chunk => new ModelAdditionChunkWrite(chunk.AdditionId,
            chunk.PartId, chunk.ChunkIndex, chunk.TargetId, chunk.Placement, chunk.Dobj, chunk.Pobj,
            chunk.Material, chunk.Mesh.TriangleIndices.Length / 3, chunk.Mesh)).ToArray();
        var write = new ModelAdditionWrite(output, chunksWritten, jobjWrites.ToArray(), materialWrites.ToArray(),
            imageWrites.ToArray(), patchedFields.ToArray());
        Verify(source, new ArchiveLayout(output), identity, write);
        return write;
    }

    public static void Verify(ArchiveLayout source, ModelIdentitySnapshot identity,
        ModelAdditionWrite expected) => Verify(source, new ArchiveLayout(expected.Bytes),
            identity, expected);

    public static void Verify(ArchiveLayout source, ArchiveLayout archive,
        ModelIdentitySnapshot identity, ModelAdditionWrite expected,
        bool verifyPreservation = true,
        IReadOnlySet<int>? ignoredJobjOffsets = null)
    {
        Require(source.Roots.SequenceEqual(archive.Roots) && source.References.SequenceEqual(archive.References),
            "MODEL_ADDITION_ROOTS", "Root or external-reference inventory changed while adding models.");
        if (verifyPreservation)
        {
            var allowed = expected.PatchedSourceFields.ToHashSet();
            for (int offset = 0; offset < source.DataSize; offset++)
                Require(allowed.Any(field => offset >= field && offset < field + 4)
                    || source.Bytes[32 + offset] == archive.Bytes[32 + offset],
                    "MODEL_ADDITION_PRESERVATION", "Unrelated original archive bytes changed while adding models.");
        }

        var catalog = ModelIdentityCatalog.Restore(identity.Nodes);
        var extended = ModelIdentity.Capture(archive, catalog)
            .WithoutSourceOffsets(ignoredJobjOffsets ?? new HashSet<int>());
        int expectedNewNodes = expected.Chunks.Length * 2 + expected.Jobjs.Length;
        Require(extended.Nodes.Count == identity.Nodes.Count + expectedNewNodes,
            "MODEL_ADDITION_GRAPH", $"Model graph extension contains an unexpected descriptor count "
            + $"({extended.Nodes.Count} actual, {identity.Nodes.Count + expectedNewNodes} expected).");
        var extendedById = extended.Nodes.ToDictionary(node => node.Id);
        foreach (var original in identity.Nodes)
        {
            Require(extendedById.TryGetValue(original.Id, out var actual)
                && actual == original, "MODEL_ADDITION_GRAPH",
                "An original model descriptor changed identity, ownership, order, or offset.");
        }

        var originalIds = identity.Nodes.Select(node => node.Id).ToHashSet();
        var newNodes = extended.Nodes.Where(node => !originalIds.Contains(node.Id)).ToArray();
        Require(newNodes.Length == expectedNewNodes, "MODEL_ADDITION_GRAPH",
            "Unexpected descriptors appeared in the extended model graph.");
        var reader = new ArchiveDataReader(archive);

        var addedJobjNodes = new Dictionary<string, ModelIdentityNode>(StringComparer.Ordinal);
        foreach (var jobj in expected.Jobjs)
        {
            Require(jobj.JobjOffset % 32 == 0, "MODEL_ADDITION_ALIGNMENT",
                "Added JOBJ descriptor is not 32-byte aligned.");
            var node = newNodes.SingleOrDefault(item => item.Kind == "jobj"
                && item.SourceOffset == jobj.JobjOffset);
            Require(node != null && node.OwnerId == jobj.AnchorJobjId,
                "MODEL_ADDITION_GRAPH", "Added JOBJ ownership differs from the write map.");
            addedJobjNodes.Add(jobj.AdditionId, node!);
            var siblings = expected.Jobjs.Where(item => item.TargetId == jobj.TargetId).ToArray();
            int siblingIndex = Array.IndexOf(siblings, jobj);
            int? next = siblingIndex + 1 < siblings.Length ? siblings[siblingIndex + 1].JobjOffset : null;
            Require(reader.Int(jobj.JobjOffset + 4) == (JobjLighting | JobjOpaque)
                && reader.Pointer(jobj.JobjOffset + 8) == null
                && reader.Pointer(jobj.JobjOffset + 12) == next
                && reader.Pointer(jobj.JobjOffset + 16) == jobj.FirstDobjOffset
                && Enumerable.Range(0, 3).All(axis => reader.Float(jobj.JobjOffset + 0x14 + axis * 4) == 0)
                && Enumerable.Range(0, 3).All(axis => reader.Float(jobj.JobjOffset + 0x20 + axis * 4) == 1)
                && Enumerable.Range(0, 3).All(axis => reader.Float(jobj.JobjOffset + 0x2C + axis * 4) == 0)
                && reader.Pointer(jobj.JobjOffset + 0x38) == null
                && reader.Pointer(jobj.JobjOffset + 0x3C) == null,
                "MODEL_ADDITION_JOBJ", "Added JOBJ descriptor differs from the write map.");
            var anchor = extendedById[jobj.AnchorJobjId];
            Require((reader.Int(anchor.SourceOffset + 4) & JobjRootOpaque) != 0,
                "MODEL_ADDITION_JOBJ", "Added JOBJ chain is not traversed in the opaque pass.");
        }

        foreach (var chunk in expected.Chunks)
        {
            Require(chunk.DobjOffset % 32 == 0 && chunk.PobjOffset % 32 == 0,
                "MODEL_ADDITION_ALIGNMENT", "Added model descriptors are not 32-byte aligned.");
            var dobj = newNodes.SingleOrDefault(node => node.Kind == "dobj" && node.SourceOffset == chunk.DobjOffset);
            var pobj = newNodes.SingleOrDefault(node => node.Kind == "pobj" && node.SourceOffset == chunk.PobjOffset);
            string expectedOwner = chunk.Placement == ModelAddition.NewJobjChainPlacement
                ? addedJobjNodes[chunk.AdditionId].Id : chunk.TargetId;
            Require(dobj != null && pobj != null && dobj.OwnerId == expectedOwner
                && pobj.OwnerId == dobj.Id && pobj.Index == 0,
                "MODEL_ADDITION_GRAPH", "Added DOBJ/POBJ ownership differs from the write map.");
            Require(reader.Pointer(dobj.SourceOffset + 8) == chunk.MaterialOffset
                && reader.Pointer(dobj.SourceOffset + 0x0C) == chunk.PobjOffset
                && reader.UShort(chunk.PobjOffset + 0x0C) == 0x4001,
                "MODEL_ADDITION_GRAPH", "Added material or polygon pointer differs from the write map.");
            var mesh = GxMeshDecoder.Decode(archive, chunk.PobjOffset);
            Require(mesh.Positions.SequenceEqual(chunk.Mesh.Positions)
                && mesh.Normals != null && mesh.Normals.SequenceEqual(chunk.Mesh.Normals!)
                && mesh.TriangleIndices.SequenceEqual(chunk.Mesh.TriangleIndices)
                && ((mesh.TexCoords0 == null && chunk.Mesh.TexCoords0 == null)
                    || (mesh.TexCoords0 != null && chunk.Mesh.TexCoords0 != null
                        && mesh.TexCoords0.SequenceEqual(chunk.Mesh.TexCoords0)))
                && mesh.TexCoords1 == null && mesh.Envelopes == null
                && mesh.BoundJobjSourceOffset == null,
                "MODEL_ADDITION_GEOMETRY", "Added geometry differs after archive reload.");
        }
        foreach (var target in expected.Chunks
            .Where(chunk => chunk.Placement == ModelAddition.ExistingJobjPlacement)
            .GroupBy(chunk => chunk.TargetId))
        {
            var offsets = newNodes.Where(node => node.Kind == "dobj" && node.OwnerId == target.Key)
                .OrderBy(node => node.Index).Select(node => node.SourceOffset).ToArray();
            Require(offsets.SequenceEqual(target.Select(chunk => chunk.DobjOffset)),
                "MODEL_ADDITION_GRAPH", "Added existing-JOBJ DOBJ order differs from the write map.");
        }
        foreach (var addition in expected.Chunks
            .Where(chunk => chunk.Placement == ModelAddition.NewJobjChainPlacement)
            .GroupBy(chunk => chunk.AdditionId))
        {
            string owner = addedJobjNodes[addition.Key].Id;
            var offsets = newNodes.Where(node => node.Kind == "dobj" && node.OwnerId == owner)
                .OrderBy(node => node.Index).Select(node => node.SourceOffset).ToArray();
            Require(offsets.SequenceEqual(addition.Select(chunk => chunk.DobjOffset)),
                "MODEL_ADDITION_GRAPH", "Added new-JOBJ DOBJ order differs from the write map.");
        }
        Require(newNodes.All(node => expected.Chunks.Any(chunk =>
                node.SourceOffset == chunk.DobjOffset || node.SourceOffset == chunk.PobjOffset)
            || expected.Jobjs.Any(jobj => node.SourceOffset == jobj.JobjOffset)),
            "MODEL_ADDITION_GRAPH", "Extended graph contains an undeclared descriptor.");

        foreach (var material in expected.Materials)
        {
            Require(material.MobjOffset % 32 == 0 && material.ColorOffset % 32 == 0
                && (!material.TobjOffset.HasValue || material.TobjOffset.Value % 32 == 0)
                && (!material.LodOffset.HasValue || material.LodOffset.Value % 32 == 0),
                "MODEL_ADDITION_ALIGNMENT", "Added material descriptors are not 32-byte aligned.");
            bool diffuse = material.Preset == ModelAdditionEditing.DiffuseMaterialPreset;
            Require(reader.Int(material.MobjOffset + 4) == (diffuse ? RenderDiffuse : RenderConstant)
                    + (material.TobjOffset.HasValue ? RenderTex0 : 0)
                && reader.Pointer(material.MobjOffset + 0x0C) == material.ColorOffset
                && reader.Pointer(material.MobjOffset + 8) == material.TobjOffset
                && reader.Byte(material.ColorOffset) == (diffuse ? material.Red / 2 : material.Red)
                && reader.Byte(material.ColorOffset + 1) == (diffuse ? material.Green / 2 : material.Green)
                && reader.Byte(material.ColorOffset + 2) == (diffuse ? material.Blue / 2 : material.Blue)
                && reader.Byte(material.ColorOffset + 4) == material.Red
                && reader.Byte(material.ColorOffset + 5) == material.Green
                && reader.Byte(material.ColorOffset + 6) == material.Blue
                && reader.Float(material.ColorOffset + 0x0C) == 1,
                "MODEL_ADDITION_MATERIAL", "Added material descriptor differs from the write map.");
            if (material.TobjOffset is not int tobj) continue;
            Require(reader.Int(tobj + 8) == 0 && reader.Int(tobj + 0x0C) == 4
                && reader.Float(tobj + 0x1C) == 1 && reader.Float(tobj + 0x20) == 1
                && reader.Float(tobj + 0x24) == 1
                && reader.Int(tobj + 0x34) == material.WrapS
                && reader.Int(tobj + 0x38) == material.WrapT
                && reader.Byte(tobj + 0x3C) == 1 && reader.Byte(tobj + 0x3D) == 1
                && reader.Int(tobj + 0x40) == (TobjLightmapDiffuse | TobjColormapModulate)
                && reader.Int(tobj + 0x48) == material.MagFilter
                && reader.Pointer(tobj + 0x4C) == material.ImageDescriptorOffset
                && reader.Pointer(tobj + 0x54) == material.LodOffset
                && reader.Int(material.LodOffset!.Value) == material.MinFilter,
                "MODEL_ADDITION_TEXTURE", "Added texture descriptor differs from the opaque preset.");
        }
        foreach (var image in expected.Images)
        {
            Require(image.DescriptorOffset % 32 == 0 && image.DataOffset % 32 == 0,
                "MODEL_ADDITION_ALIGNMENT", "Added image data is not 32-byte aligned.");
            Require(reader.Pointer(image.DescriptorOffset) == image.DataOffset
                && reader.UShort(image.DescriptorOffset + 4) == image.Width
                && reader.UShort(image.DescriptorOffset + 6) == image.Height
                && reader.Int(image.DescriptorOffset + 8) == (int)GXTexFmt.RGBA8
                && reader.Int(image.DescriptorOffset + 0x0C) == 0,
                "MODEL_ADDITION_TEXTURE", "Added RGBA8 image descriptor differs from the write map.");
            reader.Check(image.DataOffset, image.EncodedLength);
            byte[] encoded = archive.Bytes.AsSpan(32 + image.DataOffset, image.EncodedLength).ToArray();
            byte[] bgra = GXImageConverter.DecodeTPL(GXTexFmt.RGBA8, image.Width, image.Height, encoded);
            Require(RgbaBgra(bgra).SequenceEqual(image.Pixels), "MODEL_ADDITION_TEXTURE",
                "Added RGBA8 image differs after archive reload.");
        }
    }

    private static byte[] Attributes(bool textured)
    {
        int count = textured ? 3 : 2;
        byte[] result = new byte[(count + 1) * 0x18];
        for (int index = 0; index < count; index++)
        {
            Put(result, index * 0x18, index < 2 ? 9 + index : 13); // POS / NRM / TEX0
            Put(result, index * 0x18 + 4, 1); // GX_DIRECT
            Put(result, index * 0x18 + 8, index == 1 ? 0 : 1); // XYZ/NRM_XYZ/TEX_ST
            Put(result, index * 0x18 + 0x0C, 4); // GX_F32
            Short(result, index * 0x18 + 0x12, index == 2 ? 8 : 12);
        }
        Put(result, count * 0x18, 255); // GX_VA_NULL
        return result;
    }

    private static byte[] Display(MeshData mesh)
    {
        int stride = mesh.TexCoords0 == null ? 24 : 32;
        int length = checked((3 + mesh.Positions.Length * stride + 31) / 32 * 32);
        Require(length / 32 <= ushort.MaxValue && mesh.Positions.Length <= ushort.MaxValue,
            "MODEL_ADDITION_GX_LIMIT", "Added geometry chunk exceeds GX display-list limits.");
        byte[] result = new byte[length];
        result[0] = 0x90;
        Short(result, 1, mesh.Positions.Length);
        for (int index = 0; index < mesh.Positions.Length; index++)
        {
            int offset = 3 + index * stride;
            var position = mesh.Positions[index];
            var normal = mesh.Normals![index];
            foreach (float value in new[] { position.X, position.Y, position.Z,
                normal.X, normal.Y, normal.Z })
            {
                Float(result, offset, value);
                offset += 4;
            }
            if (mesh.TexCoords0 != null)
            {
                Float(result, offset, mesh.TexCoords0[index].X);
                Float(result, offset + 4, mesh.TexCoords0[index].Y);
            }
        }
        return result;
    }

    private static int EncodedRgba8Length(int width, int height) =>
        checked(((width + 3) / 4 * 4) * ((height + 3) / 4 * 4) * 4);
    private static byte[] RgbaBgra(byte[] pixels)
    {
        Require(pixels.Length % 4 == 0, "MODEL_ADDITION_TEXTURE", "Pixel data is not four-channel.");
        byte[] result = (byte[])pixels.Clone();
        for (int index = 0; index < result.Length; index += 4)
            (result[index], result[index + 2]) = (result[index + 2], result[index]);
        return result;
    }
    private static int Wrap(string value) => value == "repeat" ? 1 : 0;
    private static int Filter(string value) => value == "linear" ? 1 : 0;
    private static byte Channel(float value) => (byte)MathF.Round(value * 255,
        MidpointRounding.AwayFromZero);
    private static void Put(byte[] bytes, int offset, int value) =>
        BinaryPrimitives.WriteInt32BigEndian(bytes.AsSpan(offset, 4), value);
    private static void Short(byte[] bytes, int offset, int value) =>
        BinaryPrimitives.WriteUInt16BigEndian(bytes.AsSpan(offset, 2), checked((ushort)value));
    private static void Float(byte[] bytes, int offset, float value) =>
        Put(bytes, offset, BitConverter.SingleToInt32Bits(value));
}
