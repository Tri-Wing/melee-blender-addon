# External model addition and material conversion plan

Status: initial implementation complete through the automated Blender round trip.
Sessions advertise `modelAddition: true` whenever structural analysis finds at
least one safe static attachment; in-game acceptance remains outstanding.

Implemented foundation:

- schema-versioned attachment discovery based on archive structure and animation
  data, without filename, hash, stage, or offset allowlists;
- strict normalized additions/material/image records;
- bounded geometry and raw RGBA validation, exact hashes and byte counts;
- contained flat asset paths with undeclared-file and symbolic-link rejection;
- deterministic rigid DOBJ/POBJ append writing with exact graph-extension checks;
- opaque constant and RGBA8 base-color materials, UVs, samplers, decoded image
  verification, and content deduplication;
- Blender selection registration, editable persistent copies, conversion
  reporting, transactional staging, removal, export, and reimport coverage.

`MODEL_ADDITION_*` names are internal validation/error-code identifiers for this
protocol. Successful additions are serialized as ordinary HSD model structures;
there is no runtime `MODEL_ADDITION` marker in the DAT.

## Goal

Add a model imported into Blender to an existing Melee stage DAT as **new render
geometry**, with its own converted materials and embedded textures. Preserve its
UV mapping, UV seams, and face-to-material assignments. Do not require replacing
an existing mesh, borrowing stage materials, or combining textures into an atlas.

Some visual simplification is acceptable. Preserving the base-color textures and
their mapping takes priority over reproducing another game's shading model.
The starting DAT supplies the stage's existing configuration and behavior;
creating a complete stage archive from scratch is outside this feature.

## User workflow

1. Import a supported Melee stage DAT as usual.
2. Import the external model with Blender's available format importer, such as
   glTF, FBX, or OBJ. The add-on accepts the resulting Blender meshes; it does not
   need its own parsers for those file formats or another game's native archives.
3. Position and scale the model in the stage. Select its mesh objects and choose
   **Add Selected Models to Stage**.
4. Choose a supported stage attachment from a list with readable group/joint
   names. Show whether the attachment moves or hides with the original stage.
   The first release offers only structurally eligible static attachment targets.
5. Review a conversion report listing materials, textures, output dimensions,
   estimated texture bytes, mesh chunks, and any lost shading features. Missing
   images or unsupported base-color graphs must be actionable errors, not an
   unexpected grey export.
6. Preview the converted materials. Replace each successfully registered source
   mesh with its evaluated stage copy in an **Added Models** collection; users
   can duplicate before registration when they want a reference. Keep registered
   objects editable and pack their texture images for `.blend` save/reopen. A
   failed registration leaves its sources untouched.
7. Validate and export the DAT. New geometry appears alongside the existing
   stage geometry. Edit gameplay collision separately using the existing tools.

New objects can be removed from the pending additions before export. Repeated
exports from the same session must produce one copy of each registered addition.
Exporting does not silently rebase the session onto the previous output DAT.

## Scope and conversion policy

| Input | First release behavior |
| --- | --- |
| Static mesh positions and placement | Convert to the chosen attachment's local coordinates while preserving placement. |
| Multiple objects and material slots | Preserve face assignments; partition geometry by object/material and split further for GX limits. |
| Base-color image texture | Embed a newly encoded GameCube texture and create its material structures. |
| Constant base color | Create a new untextured Melee material. |
| UV coordinates and seams | Preserve per triangle corner; retain values outside `[0, 1]` for tiling. |
| UV Map node or active UV map | Use the channel selected by the supported base-color texture connection and emit it as UV0. |
| Simple UV Mapping transforms | Bake supported transforms into exported UVs, with identity texture transforms; report unsupported chains. |
| Texture repeat/clamp and nearest/linear sampling | Translate supported modes explicitly and verify in preview/reimport. |
| Smooth/custom normals | Preserve evaluated corner normals where valid; otherwise calculate normals and report the fallback. |
| Metallic, roughness, normal maps, and complex lighting | Omit with named warnings; use a simple, documented Melee shading preset. |
| Procedural or layered base color | Require an explicitly selected image/UV map or a user-baked base-color texture. Automatic shader baking is deferred. |
| Texture alpha | Preserve alpha in image data; initially render opaque and report this loss when alpha affects appearance. |
| Skinning, imported animation, and shape keys | No animation export; offer an explicit static evaluated snapshot of the selected pose. |

The first automatic material recognizer should follow the active Material Output
surface to a supported Principled BSDF and its base-color input: either a color
or one Image Texture connected directly or through a simple Multiply with vertex
color. Vertex-color modulation is reported as a lossy conversion because the
opaque addition preset exports the image texture but not Blender color attributes.
The Image Texture may use the active UV map, a direct UV Map node, or the UV
output of a Texture Coordinate node.
Do not choose an arbitrary image merely because it exists somewhere in the graph.
An explicit image/UV override makes other imported material layouts usable
without pretending to support their full shader graphs.

Reject missing UVs for textured faces, unavailable images, nonfinite values, and
ambiguous base-color inputs. Handle packed images and unsaved pixel edits from
Blender's current image data. UDIMs, movies, sequences, generated coordinates,
and environment projections require baking or a supported override initially.

An opaque textured preset is the first rendering target. Alpha cutout is a
follow-up milestone requiring explicit alpha-test and depth-state verification;
blended transparency additionally requires draw-order and joint render-pass
analysis. Neither is implied by successfully encoding an RGBA texture.

## Integrated foundation and remaining gaps

The implementation already provides useful pieces:

- [modeling.py](../blender_addon/melee_map_editor/modeling.py) serializes rigid
  replacement meshes and UVs, but targets only existing protected mesh IDs.
- [surface.py](../blender_addon/melee_map_editor/surface.py) handles preview
  materials and corner UVs for replacement editing. The separate
  [model_additions.py](../blender_addon/melee_map_editor/model_additions.py)
  recognizes supported ordinary Blender materials and stages normalized additions.
- [ModelArchiveWriter.cs](../src/MeleeMap.Core/ModelArchiveWriter.cs) appends GX
  geometry and can create a grey material, but updates an existing DOBJ/POBJ.
- [GXImageConverter.cs](../HSDLib/HSDRaw/Tools/Textures/GXImageConverter.cs) exposes
  texture encoding and decoding in HSDRaw. The addition writer explicitly
  bridges its BGRA byte-array convention and verifies tiled RGBA8 decode.
- [ModelIdentity.cs](../src/MeleeMap.Core/ModelIdentity.cs) requires unchanged
  object counts, order, and ownership for existing workflows. The addition
  writer uses a separate exact expected-extension verifier.
- [SessionApplier.cs](../src/MeleeMap.Core/SessionApplier.cs) validates edits,
  reopens the output, and publishes it only after validation. It now composes
  additions with supported existing edits while retaining strict file inventory.

Use HSDRaw's headless facilities. Do not introduce a dependency on HSDRawViewer,
its model importer, or its image-loading UI.

## Archive attachment design

Append new DOBJ entries to the **end** of an eligible existing JOBJ's DOBJ list.
Create a POBJ and GX buffers per generated geometry chunk, plus new materials,
texture descriptors, and image buffers. Chunks may share newly generated
material/image structures when their normalized definitions match.

This preserves existing group indices, JOBJ preorder, and existing DOBJ/POBJ
indices. A joint with no DOBJ list can receive one when otherwise eligible.
No new JOBJ or model group is needed for the initial feature. The new geometry
inherits the attachment's transform, visibility, and stage-controlled behavior;
attachment selection cannot promise independence from stage code.

Eligibility must be separate from existing mesh-replacement eligibility:

- Require a normal, uniquely owned JOBJ with a supported invertible transform
  chain and an ordinary, unshared DOBJ list.
- Exclude instance, particle/spline, billboard, constrained, custom-class,
  externally referenced, and interior-aliased attachment structures initially.
  Account for ancestor behavior and instance references to the hierarchy.
- Initially exclude attachment paths affected by joint, material, or shape
  animation. Audit all source animation slots and runtime stage behavior before
  broadening support. Appending to a list alone is not sufficient evidence that
  animation and render traversal will remain compatible.
- Do not change original JOBJ render flags just to make new materials visible.
  Verify that the selected target and its ancestors support the chosen opaque
  material preset; reject or omit targets needing unimplemented flag changes.
- Recompute eligibility in the backend from the immutable source. A modified
  manifest or Blender tag must not enable an unsupported target.

Milestone 1 must demonstrate at least one useful attachment in `GrNLa.dat`.
Audit `melee/src/sysdolphin/baselib/{jobj,dobj,mobj,tobj,pobj}.c` and stage loading/
animation behavior in `melee/src/melee/gr/{ground,granime}.c`, plus relevant
stage-specific code. Record exact source evidence and tested targets. If these
restrictions leave no useful attachment, document and design the missing case
before calling the feature viable; replacing existing geometry is not a fallback
that meets this requirement.

## Material and texture generation

Create a small normalized material representation independent of Blender nodes:
base color, optional image ID, UV source resolved to corner coordinates, wrap
modes, filtering, opaque render mode, and a documented lighting preset. Start
with a texture-focused constant-color preset; validate the exact GX/HSD flags
against the local engine source and an in-game fixture.

For textured materials write a new MOBJ, material color block, TOBJ, HSD image
descriptor, and aligned encoded image buffer. Include pixel-engine or LOD
descriptors only when required by the supported preset. Emit and validate every
pointer relocation; leave original materials and textures untouched.

Use RGBA8 as the first fidelity/reference encoding, with no generated mip chain.
Use matching non-mipmap filtering. Texture compression, palette formats, and
mip generation are later optimizations after the reference path works. Deduplicate
new image payloads by normalized pixels and encoding settings; different wrap
or material settings may share an image without sharing a TOBJ/MOBJ.

Define the pixel interchange precisely: 8-bit RGBA, straight alpha, documented
row origin, and sRGB color encoding. Convert Blender's working pixel values
explicitly; do not bake scene exposure, the view transform, or display lighting
into base-color pixels. Adapt to HSDRaw's actual expected channel order, verified
with asymmetric colored fixtures rather than parameter names.

Preserve source dimensions when valid. During the format audit set explicit
dimension and aggregate byte limits, distinguish tile-storage padding from image
resizing, and establish any power-of-two restrictions required by sampling.
Oversized or unsupported dimensions should request a specific resize setting;
never silently rescale or stretch textures. Show decoded and encoded byte totals
because RGBA8 can make large external models impractical without optimization.
DAT structural validity alone does not establish a safe in-game memory budget.

## Geometry conversion

Capture mesh geometry and material assignments from evaluated Blender mesh
copies before consuming the successfully registered source objects. This keeps
registration transactional while ensuring the resulting addition is a static,
deterministic snapshot and cannot imply imported animation support.

Preserve placement by converting each mesh's world-space geometry into the
chosen attachment's game-local space using the existing axis conversion. Define
the reference pose used at registration and keep it stable. If the attachment
also receives a supported static JOBJ edit, use the final composed transform so
new geometry previews and exports consistently.

Triangulate with per-corner UVs and normals. Transform normals with the inverse
transpose, and account for mirrored transforms and winding. Partition triangles
by material without merging UV seams. Split large partitions into deterministic
chunks that satisfy vertex counts, GX command counts, display-list byte length,
and the POBJ length field. The existing 14,000-triangle replacement limit is not
by itself sufficient: textured vertex strides can hit the byte limit earlier.

One material can produce several DOBJ/POBJ chunks. Never drop triangles to meet
a size limit. Report degenerate-triangle removal and reject empty results.
UVs must round-trip per corner within float32 precision; coordinate-origin
conversion must occur exactly once.

## Proposed session extension

Keep session protocol v2 and add an explicit, versioned capability:
`capabilities.modelAddition: true`, `modelAdditionSchemaVersion: 1`, plus
`modelAdditionTargets` containing source JOBJ IDs and eligibility information.
Require fresh extraction to enable additions in older saved scenes. The CLI
process-result envelope remains version 1; it is separate from session versions.

Introduce `edits/additions.json` with these logical fields:

- Protocol and addition-schema versions, coordinate space `game-joint-local`.
- Additions with stable session UUIDs, display names, target JOBJ IDs, geometry,
  per-corner UVs/normals, and material references per triangle or partition.
- Normalized material definitions and image references.
- Image records with dimensions, pixel encoding, payload path, and SHA-256.

Use referenced raw RGBA payloads under `edits/addition-assets/` initially. Blender
already resolves source image formats; the backend need not add a PNG/JPEG loader
or accept machine-specific source image paths. Bound payload sizes, validate exact
byte counts and hashes, enforce resolved path containment including symlinks,
and reject undeclared assets and duplicate IDs. Payload hashes check consistency;
the backend must still validate all supplied image and geometry data.

Older backends already reject unfamiliar edit files; the add-on should fail
earlier with a matching-backend message when the capability is unavailable.
Additions must be counted as real changes in apply results and validation.

Stage edit files and image payloads transactionally for each validate/export.
Extend the existing scene export cleanup to remove only files created by that
attempt, including on failure. Preserve preexisting user edit files and report
conflicts. Keep packed images and registration metadata in `.blend`; temporary
session payloads must not be the only surviving copy of a converted texture.

## Preservation and validation changes

Keep `RequireUnchanged` for existing workflows. Add a separate verifier for an
explicit expected graph extension, backed by an export-time mapping from each
addition/chunk ID to its emitted DOBJ/POBJ offsets. Extend the identity catalog
through that mapping; do not infer new IDs from shifted traversal positions.

The extension verifier must require all original descriptors, ownership, list
positions, and instance targets to remain intact. Additional descriptors must
match exactly the declared additions and their approved attachment tails. Do
not implement this as a permissive subset comparison that admits arbitrary
new nodes or reparenting.

The writer should append all new data while retaining existing offsets. For an
addition-only export, allowed changes to original data are limited to each
approved list-tail `next` field, or the JOBJ DOBJ-head field for an empty list.
Rebuild archive size/relocation metadata as needed and preserve roots, names,
external reference chains, and all other original payload bytes. For combined
edits, compose this patch set with the explicitly allowed existing edit patches.

Validate the complete batch before writing, then reopen the temporary output and:

- Run archive, hierarchy, and collision validation.
- Verify the exact expected graph extension and unchanged original ordering.
- Decode all added meshes and compare geometry, UVs, normals, and assignments.
- Validate every new material/image pointer, flags, alignment, dimensions,
  payload length, and sampler configuration. Decode textures and compare with
  expected RGBA8 pixels; future lossy formats need explicit error tolerances.
- Verify original materials, textures, animations, collision, and opaque data
  remain unchanged except for independently requested supported edits.

Only publish the result after all checks pass. Preserve byte-identical no-edit
exports and the existing behavior that a failed export leaves its output intact.
Applying identical edits to the immutable baseline repeatedly must not accumulate
duplicate additions or grow the output on every attempt.

Reimporting an exported DAT starts a new session: added geometry becomes ordinary
source geometry and receives new session identities. It should preview and,
where eligible, edit normally. Removing those now-baseline descriptors remains
outside this feature; pending additions are freely removable in their original
session.

## Implementation milestones

1. **Attachment and rendering analysis.** Establish programmatic static-target
   rules across the corpus, exact material/texture defaults, image limits, and engine behavior.
   Build a small synthetic append fixture and record source evidence. Exit:
   demonstrate the append strategy without shifting original indices.
2. **Backend additions and identity verification.** Add normalized addition
   records, eligibility, deterministic partitioning/chunking, an append writer,
   explicit identity extension, and apply integration. Start with a newly
   authored solid material. Exit: add a mesh through the CLI, reimport it, and
   prove original graph/payload preservation.
3. **Texture authoring.** Implement RGBA pixel transport, RGBA8 encoding, material
   and texture descriptor generation, sampling conversion, deduplication, and
   decoded verification. Exit: add two materials with separate textures and UV
   seams, with no donor-material references or atlas.
4. **Blender workflow.** Add selection registration, attachment selection,
   evaluated mesh conversion, supported node recognition, image/UV overrides,
   converted preview, loss reporting, persistence, removal, and export staging.
   Exit: import an external textured asset using Blender and export additions
   through the normal stage workflow.
5. **Integration and in-game acceptance.** Exercise combined edits and the
   existing regression suite, document the workflow, and manually test a textured
   model in Dolphin or on hardware. Exit: UVs, textures, placement, and original
   stage behavior are confirmed in game; structural validation alone is not
   completion of this milestone.

Likely new modules: `ModelAdditions.cs`, `ModelAdditionWriter.cs`, and
`MaterialConversion.cs`/`TextureEncoding.cs` in Core; `model_additions.py` and
`material_conversion.py` in the add-on. Extend `SessionExtractor`,
`SessionApplier`, `ModelIdentity`, scene validation, operators, and development
reload registration. Reuse existing GX/preview code where applicable without
turning the replacement-only writer into an unchecked general graph editor.

## Acceptance and regression tests

The user supplied `example_assets/wind_waker_boat/` as a local integration fixture
for implementation testing. It contains `Salvage Boat.obj`, its `.mtl`, a `.dae`
alternative, three PNG images, and `materials.txt`. Start with the OBJ/MTL path:
the material `is2_v` references `V_svsp_red.png` as its base-color texture.
`materials.txt` also describes a two-texture effect involving `ZAtoon`; use that
as evidence for the conversion report's shading-loss warning, not as a promise
that the first-release base-color converter reproduces the original effect.
Check UVs, placement, and base-color appearance through Blender import, DAT export,
reimport, and eventual in-game testing. The OBJ/MTL/PNG path now passes automated
Blender export and reimport; in-game validation remains. Keep it local, consistent with the repository's policy
of excluding game assets from release packages; retain synthetic fixtures for
asset-independent tests and multiple-material coverage.

- Asset-independent synthetic DAT: append to empty and nonempty DOBJ lists;
  verify all original indices, ownership, roots, opaque bytes, and pointers.
- Reject forged targets, conflicting IDs, shared/aliased structures, bad indices,
  missing UVs/images, invalid asset paths, oversized payloads, and malformed
  materials; failed apply must preserve an existing output file.
- Two-material model with an asymmetric checker and a distinct second texture:
  verify seams, orientation, channels, color space, repeat/clamp, and assignments
  after decode and reimport. Test packed and modified image pixels.
- Nonuniform and negative object scales, nonidentity attachment transforms,
  multiple objects, multiple UV maps, and smooth corner normals.
- Large textured geometry: exercise display-list limits and deterministic
  splitting while preserving every nondegenerate triangle and its material.
- `.blend` save/reopen and add-on reload: additions and textures survive, duplicate
  registration is handled explicitly, removal works, and repeated exports do
  not duplicate geometry. Test additions alongside supported existing edits.
- Existing no-edit, collision, replacement, material, lighting, and animation
  regressions continue passing; local corpus tests retain their existing scope.
- Manual game test: original stage geometry remains; the added textured model
  appears in the expected location with correct culling and mapping; gameplay
  collision changes only when separately edited; check visibility across match
  startup and relevant stage states. Record stage, asset complexity, texture
  memory estimate, and observed performance instead of claiming all stages work.

## Follow-up capabilities

After the first usable release: alpha cutout and blended transparency, texture
compression and mipmaps, optional baking, animated/instanced attachment support,
and explicit new-joint/group authoring. Independent runtime motion, imported skinning, and
automatic gameplay-collision generation require their own designs. An atlas may
later be an optional optimization; it is not required for importing models with
multiple materials.
