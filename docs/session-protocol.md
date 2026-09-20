# Session protocol v2

The CLI's process-result envelope remains version 1. Editing sessions use version
2; older sessions must be re-extracted before `apply`.

```sh
meleemap extract <input.dat> --session <new-directory>
meleemap apply <session-directory> --output <output.dat>
```

Extraction prepares a sibling temporary directory and publishes it only after
success. Apply requires an output outside the session, writes a temporary DAT,
reopens and validates it, checks model identities and collision records, then
publishes it, replacing an existing output file if present. Failed validation
leaves the existing output intact. The session source snapshot remains protected;
the original imported DAT outside the session may be selected as the output.
Extraction still requires a new session directory.

```text
stage.json
source.dat
models/group-000/group.json
models/group-001/group.json
models/group-001/mesh-<opaque-id>.json
... all supported mesh payloads and group directories ...
collision/collision.json
edits/collision.json                     # optional replacement collision graph
edits/models.json                        # optional batch of rigid model replacements
edits/materials.json                     # optional static material changes
edits/lights.json                        # optional static LOBJ changes
edits/jobjs.json                         # optional static JOBJ SRT changes
```

## Manifest and protected identity

`stage.json` declares `protocolVersion: 2`, `assetType: "melee-stage"`,
`schemaVersion: 1`, source filename/hash/DAT version, public roots and external
references, group count/file inventory, capabilities, warnings, deferred meshes,
and `baselineFiles` containing SHA-256 hashes of every extracted JSON payload.
Paths are relative to the session. `selectedMesh` retains the first decoded mesh
as a convenient initial target; it is not the full mesh inventory.

IDs are opaque UUID strings created once per extraction. Offsets, array positions,
group indices, JOBJ preorder, and list positions are separate source locators.
Re-extracting creates a new session. Retain IDs when editing an existing session;
new collision vertices/lines receive new UUIDs. Do not recycle an ID for a new role.

Apply verifies `source.dat`'s hash before parsing it, verifies the protected file
hashes and inventory, and restores the persisted model identity catalog against
the source hierarchy. Modify only files under `edits`; changing/deleting meshes,
groups, transforms, identities, or collision baseline files fails export. Model
edits are accepted only for the eligible targets described below. Unknown
edit files remain rejected.

## Coordinates and geometry

Positions, bounds, and source transforms remain in **game space**, unit scale 1.
A Blender adapter must use `(X, Y, Z) -> (X, -Z, Y)` and the inverse on export, with
a corresponding basis change for joint transforms.

Each `group.json` contains the protected group identity and index, complete JOBJ,
DOBJ, and POBJ identity/ownership records, joint flags and source SRT, inverse-bind
matrices when present (original row-major 3x4 floats), transform-only
`jointAnimations`, `materialAnimations`, and mesh paths. Joint XYZ rotation fields retain their source
encoding; unusual transform modes still need explicit handling in the Blender importer.

Every decoded POBJ has local positions, optional normals, corner-based triangle
indices, owner/group locators, raw `pobjFlags`, and binding information:

- `joint-local`: use the owning JOBJ, or `boundJobjId` when supplied.
- `envelope-source`: raw source positions plus per-corner `envelopeIndices` and
  `envelopes` containing JOBJ IDs and weights. Follow HSD's envelope matrix rules,
  including JOBJ flags/inverse matrices. These are not pre-baked world coordinates.

`GrNLa` now extracts **93 POBJs / 13,597 triangles**, including two enveloped
POBJs. All ten groups and all model identities remain present. Other archives can
still contain unsupported encodings, reported in `deferredMeshes`; consult
`allModelGeometry` instead of assuming coverage. Materials/textures/animation
data remain explicit in the session. Blender imports envelope weights and JOBJ
animation playback. Supported existing JOBJ transform tracks are editable and
exportable; envelope-weight editing is not exported. Supported rigid models can
be edited through the model replacement path.

## Collision baseline

`collision.json` preserves vertex/line/joint IDs, source indices, all positions,
category ranges, joint bounds, primary and alternate links, raw high/low flags,
material/property bytes, and attachment records. Locally resolvable attachments
have JOBJ IDs; unresolved companion targets have null IDs and source warnings.
Dynamic lines/attachments are read-only.

## Collision edits

`edits/collision.json` is a **complete replacement graph**, not a list of deltas:

```json
{
  "protocolVersion": 2,
  "coordinateSpace": "game",
  "vertices": [
    { "id": "<existing-or-new-UUID>", "x": 0.0, "y": 0.0 }
  ],
  "lines": [
    {
      "id": "<existing-or-new-UUID>",
      "vertex0Id": "<vertex-UUID>",
      "vertex1Id": "<vertex-UUID>",
      "jointId": "<protected-joint-UUID>",
      "category": "floor",
      "highFlags": 1,
      "lowFlags": 0
    }
  ]
}
```

The placeholders above are illustrative; real IDs must be 32 hexadecimal digits.
Every property is required. Unknown properties are rejected to catch misspellings.
Copy vertices and lines from the baseline to begin an edit; remove records to
delete geometry. Unreferenced vertices are omitted by compilation. Supported
categories are `floor`, `ceiling`, `right-wall`, and `left-wall`.

The compiler:

- Keeps existing joint identities and existing lines' joint membership; joints
  cannot be added, removed, or emptied.
- Welds endpoints per joint within 0.0001 game units on each axis and rejects
  non-finite/zero-length geometry, ambiguous junctions, or inconsistent direction.
- Orders lines by category then joint and rebuilds primary links from endpoints.
- Preserves alternate links only when referenced original lines and both original
  endpoint coordinates still exist; otherwise clears the link.
- Derives direction bits from category, preserves unknown bits on existing lines,
  and permits material/drop-through/ledge changes in `lowFlags` (material low byte,
  platform bit 8, ledge bit 9). New lines cannot set unknown bits.
- Preserves enclosing bounds; otherwise expands bounds using an 8-unit margin.
- Enforces signed 16-bit limits and runs strict collision validation.

Collision edits are refused if the source has warnings, dynamic lines, or
attachments. Collision buffers with external/interior references are also refused
until alias-aware editing exists. A no-edit apply remains available and returns
byte-identical source contents even for read-only collision.

## Preservation write path

Static collision apply uses an append-only writer: it appends new leaf buffers,
updates only the collision header (0x2C bytes), and updates archive size/relocation
metadata. All original data offsets, public roots, root names, unknown fields,
and unrelated raw data bytes remain unchanged. Old buffers remain in the archive;
output size therefore grows. This narrow path is separate from HSDRaw's general
preservation round-trip writer and avoids relocating model/opaque structures.

## Example: move one vertex

After extraction, run this with the session path, then call `apply`:

```python
import json
from pathlib import Path

session = Path("/tmp/GrNLa-session")
source = json.loads((session / "collision/collision.json").read_text())
edit = {
    "protocolVersion": 2,
    "coordinateSpace": "game",
    "vertices": [{"id": v["id"], **v["position"]} for v in source["vertices"]],
    "lines": [
        {k: line[k] for k in (
            "id", "vertex0Id", "vertex1Id", "jointId", "category", "highFlags", "lowFlags"
        )}
        for line in source["lines"]
    ],
}
edit["vertices"][0]["y"] += 1.0
(session / "edits/collision.json").write_text(json.dumps(edit, indent=2))
```

The exported DAT passes structural checks, but manual game testing is still
required for the POC acceptance criteria. The [Blender add-on](blender-addon.md)
now creates these edits from an imported scene; it leaves baseline files intact
and removes its temporary edit payload after each apply attempt.

## Model edits (additive v2 capability)

New sessions set `capabilities.modelEdit` and list supported rigid targets in
`editableMeshes`. Each entry contains `id`, `groupIndex`, `jobjIndex`, `dobjIndex`,
`pobjIndex`, baseline `file`, topology-replacement
`representation: "opaque-grey-flat-shaded"`, and
`maxTriangles: 14000`. Mesh payloads also contain `editable`, `readOnlyReason`, and optional `texCoords0`
(one game-space UV per decoded vertex). `modelMaterials` advertises reusable
static opaque materials by donor mesh ID, display name, source MOBJ offset, and
`usesUv`. Source offsets are informational; apply recomputes the catalog.
The legacy `editableMesh` field remains as a single-target compatibility alias.
Old sessions with only this alias remain single-target; sessions without either
field remain collision-only. `selectedMesh` is only a preview selection.

`edits/models.json` uses this shape (all fields required):

```json
{
  "protocolVersion": 2,
  "coordinateSpace": "game-joint-local",
  "meshes": [{
    "id": "<editableMeshes entry id>",
    "positions": [{"x": 0, "y": 0, "z": 0}, {"x": 10, "y": 0, "z": 0}, {"x": 0, "y": 10, "z": 0}],
    "triangleIndices": [0, 1, 2]
  }]
}
```

Optional model-edit fields:

- `sourceMaterialId`: a supported donor mesh ID from `modelMaterials`.
- `texCoords`: `{x, y}` UVs in game coordinates, one entry per triangle index
  (corner order), required when the assigned source material uses a texture.
- `useGreyMaterial`: force grey replacement even if topology is unchanged;
  mutually exclusive with `sourceMaterialId`.

A supplied material is resolved from the immutable DAT and validated again.
The writer retains its source MOBJ/TOBJ/image data and points the target DOBJ at
that MOBJ. UV0 is written as direct float32 pairs alongside positions/normals;
zero-area triangle removal removes the corresponding UV corners. Separate UV
values at shared vertex indices support seams. Mixed-material faces must be
resolved by the adapter before submission; this protocol has one material per
editable DOBJ.

Supply one or more changed meshes with unique eligible IDs; omit untouched
meshes. Positions are local to each original owning JOBJ,
not world coordinates. The backend independently recomputes target eligibility;
changing the manifest cannot enable structurally unsupported mesh IDs. Unknown fields, nonfinite
positions, invalid indices, unsupported targets and oversized/empty geometry fail.
The backend compares vertex count and ordered triangle indices with the original
decoded source. Matching topology selects the appearance-preserving position
writer. It clones the attribute descriptors, changes only position storage to
direct XYZ float32, and rebuilds the original primitive command stream with new
positions. Every non-position token (including normal, color, UV and texture
matrix indices) is copied exactly. Their original buffers, DOBJ material pointer,
textures, render state and culling flags stay unchanged. Shared position buffers
are never patched in place. Original normals and strip degenerates are retained.

The Blender adapter sends the original triangle expansion when indexed faces and
vertex count match the baseline, avoiding degenerate-corner rotations introduced
by re-triangulation. Older sessions and `.blend` files do not need migration.

When topology differs, the replacement path uses an explicitly assigned source
material or the grey fallback. UV/material-only replacements retain original
normals and culling when topology matches; topology changes use generated flat
normals and back-face culling. The entire
batch is compiled before writing, and any invalid target prevents replacing the
output file. For topology replacements, the compiler removes exact zero-area
triangles and generates one normalized face normal per triangle corner. Per-mesh
limits are 65,535 positions and 14,000 input triangles.

The replacement writer appends a GX triangle display list with direct float32
position/normal attributes and optional UV0. It reuses the assigned source MOBJ
or appends a constant opaque grey MOBJ/material. It patches only the existing
POBJ attribute pointer, culling bits, display-list size/pointer, and DOBJ material pointer. Old
buffers are retained; archive output grows. The POBJ/JOBJ/DOBJ identities, next
pointers, non-culling flags, joint transforms/animation, original shared
materials and all other source payloads remain intact. Model and collision edits
compose sequentially without relocating original offsets. Apply reloads the
output and verifies exact decoded positions, normals, triangles, material fields,
collision records and protected model identities before replacing the output.

Topology-replacement geometry uses back-face culling (`POBJ_CULLBACK`, 0x4000), matching the
Blender-authored triangle winding and generated normals. Source front-face or
both-face culling must not be inherited by the replacement. Unedited meshes
retain their original culling flags.

Apply results now include `modelChanged` and nullable `modelTriangles` in addition
to the existing collision fields; `modelTriangles` totals the edited meshes.
An omitted model edit preserves original model
bytes regardless of collision changes. This model path has automated Blender and
backend coverage; in-game acceptance remains pending.

## Static JOBJ transform edits (additive v2 capability)

New sessions set `capabilities.jobjTransformEdit` and list supported nodes in
`editableJobjs` by opaque ID, model-group index, and JOBJ preorder index. The
corresponding `group.json` joint records include `editable` and a
`readOnlyReason` when the base transform cannot be edited safely.

`edits/jobjs.json` contains only changed nodes:

```json
{
  "protocolVersion": 2,
  "coordinateSpace": "game-jobj-local",
  "jobjs": [{
    "id": "<editableJobjs entry id>",
    "rotation": {"x": 0, "y": 0, "z": 0},
    "scale": {"x": 1, "y": 1, "z": 1},
    "translation": {"x": 0, "y": 0, "z": 0}
  }]
}
```

The backend recomputes eligibility from `source.dat`, requires finite XYZ Euler
rotation/scale/translation and nonzero scale, then patches the nine big-endian
SRT floats at JOBJ offsets `0x14` through `0x34`. It reloads the DAT and verifies
the exact values before publishing. Hierarchy pointers, flags, render objects,
animation descriptors, and all unrelated bytes remain unchanged. Apply results
include `jobjChanged`.

This first transform slice excludes transform-animated JOBJs, custom classes,
RObj constraints, inverse-bind matrices, instancing, billboard/IK/quaternion or
custom/independent matrix modes, and groups with collision attachments. Blender
creates one armature per model group, represents every JOBJ as a source-identified
bone, and uses native Pose Mode Move, Rotate, and Scale with XYZ Euler rotation.
Rigid models are bone-parented to their owning JOBJ. Blender's full classical
inheritance or aligned compensated inheritance is selected from each JOBJ's
scale flag. A JOBJ with child joints currently permits only uniform scale changes. Adding, deleting,
reparenting, or reordering JOBJs remains protected. Skinned meshes are still
geometry-edit protected, but Blender imports their decoded envelopes as vertex
groups and evaluates them through an Armature modifier. Single-joint envelopes
remain solid 100% assignments and multi-joint envelopes retain their source
weights. Enveloped mesh objects retain their fixed bind-pose transform instead
of remaining bone-parented; otherwise the animated owner transform would enter
the Armature modifier's skin input a second time.

### JOBJ animation playback and transform-track edits

Each group stores a `jointAnimations` array keyed by its original model-group
animation `slot`. A set records its maximum `endFrame`, effective loop flag,
the source model-group `groupFlag` byte, and animated
nodes. Nodes use stable JOBJ IDs and contain transform tracks named
`rotation.x/y/z`, `translation.x/y/z`, or `scale.x/y/z`. Every decoded key retains
its floating-point `frame`, `value`, `tangent`, and HSD interpolation opcode. A
node's `editable` property is true when all of its source descriptors are supported
transform channels and its timing and base scale can be safely round-tripped.

Melee sets `AOBJ_LOOP` at runtime when `SBM_Map_GOBJ.AnimationFlags[slot]` is
nonzero. A node's exported `loop` therefore combines that runtime rule with its
serialized AOBJ loop bit; `flags` still retains the unmodified AOBJ flags.

Blender creates one Action for each set and keeps the slot identity in Action
metadata. Editable nodes use native bone Location, Rotation, and Scale F-curves;
the timeline handler continues to evaluate decoded HSD constant, linear, Hermite,
zero-tangent, and slope operations for unsupported nodes. Both paths start from
each JOBJ's source SRT for absent channels. Blender frame 1 maps to HSD frame 0.
Looping wraps at `endFrame`, using the zero rewind frame initialized by the engine.

When native curves change, Blender emits `edits/animations.json`:

```json
{
  "protocolVersion": 2,
  "coordinateSpace": "game-jobj-animation",
  "nodes": [
    {
      "groupIndex": 2,
      "slot": 0,
      "jobjId": "<source JOBJ UUID>",
      "tracks": [
        {
          "channel": "translation.x",
          "keys": [
            { "frame": 0, "value": 0, "tangent": 0, "interpolation": "HSD_A_OP_LIN" },
            { "frame": 30, "value": 10, "tangent": 0, "interpolation": "HSD_A_OP_LIN" }
          ]
        }
      ]
    }
  ]
}
```

Targets must appear in the manifest's `editableJointAnimations` list. Each track
uses a unique supported transform channel, begins at frame zero, and has strictly
increasing whole-number frames within the existing node duration. Blender samples
all nine transform channels at every whole source frame and reduces collinear
samples before writing linear HSD keys. The writer appends replacement FOBJ buffers
and descriptors, redirects the existing AOBJ, and preserves duration and loop flags.
Unchanged sessions preserve the source animation structures byte-for-byte.

### Read-only material animation playback

Each group also stores `materialAnimations` by the same model-group animation
`slot`. Material targets use the stable POBJ/material preview ID and retain MOBJ
tracks for ambient, diffuse, specular, alpha, and pixel-engine values. Nested
texture targets identify the source GX texture-map ID and resolved TObj layer,
and retain image/palette, translation, scale, rotation, blend, LOD, konst, and
TEV-register tracks. Track keys use the same decoded HSD frame/value/tangent/
interpolation representation as JOBJ animation. `materialEndFrame` and
`materialLoop` keep the MOBJ AOBJ timing separate from each nested texture AOBJ;
the target and set `endFrame`/`loop` values summarize their combined slot.

Blender combines joint and material data for a slot into one Stage Animation
Action. The timeline currently evaluates MOBJ ambient/diffuse/specular/
alpha and TObj translation/scale/rotation/blend against the imported preview
shader. It resets animated values to their source material state when another
slot omits that target. Image/palette swaps, texture registers, pixel-engine
reference values, and animation export remain deferred. No-op DAT export retains
all original material animation descriptors and buffers byte-for-byte.


### Read-only texture preview assets

New `modelMaterials` entries include `preview: { color, texture, textures, warning }`.
`texture` remains the first-layer compatibility alias. `textures` stores the
ordered TObj layers; each contains its `file`, dimensions, wrap modes, repeats,
source SRT, color operation/blend, UV-channel index, `coordinateType`, and
`lightmapFlags`. Coordinate type 0 uses mesh UVs; type 1 generates reflection
coordinates from the camera-space surface normal. The lightmap field preserves
the HSD diffuse, specular, ambient, extension, and shadow routing bits (`0x10`
through `0x100`).
Decoded top-left BGRA TGA files live under `models/textures/` and participate in
`baselineFiles` hashes alongside model/collision JSON. Existing sessions without
preview fields remain valid. Preview assets never supply DAT texture data.

The reader accepts tiled GX I4/I8/IA4/IA8/RGB565/RGB5A3/RGBA8/CI4/CI8/CI14X2/CMPR
images, checks data/palette bounds, decodes padded tiles and crops to logical
size. Dimensions are limited to 2048 per axis; unsupported or malformed texture
previews produce a warning and solid fallback rather than blocking extraction.
Blender loads each image as sRGB, packs it, and creates an unlit preview shader.
Source SRT follows `tobj.c:MakeTextureMtx` (S @ R @ T), conjugated by the adapter's
V flip, with independent wrap handling. Reflection maps follow the engine's
camera-space normal projection before applying the same TObj SRT. These nodes are
visual aids and are not used by apply.

### Animated-material rigid meshes

Each `editableMeshes` entry now includes an additive `positionsOnly` boolean
(default false for older sessions). When true, edits must retain the original
vertex count and ordered triangle indices and omit `sourceMaterialId`,
`texCoords`, and `useGreyMaterial` (or leave the latter false). The backend
recomputes this restriction from the immutable source, independently of the
manifest. Violations fail with `MODEL_POSITION_ONLY` before output is written.
The position writer preserves every non-position vertex attribute, source
material pointer and animation record. These meshes are excluded from the
reusable static material catalog. Existing sessions keep their declared targets.

`modelPreviews` contains base-material previews for imported meshes outside the
export material catalog, keyed by mesh ID. This includes static vertex-color
materials, animated materials and read-only geometry. These entries use the same `preview` color/texture structure as
`modelMaterials`, but are not export material donors. Blender imports them with
`mme_preview_model_id` rather than `mme_model_material_id`. No animation is
evaluated: source base colors and supported TObj layers using UV0/UV1 are displayed
in list order. Both GX UV channels are decoded as separate Blender UV maps.
The preview also carries `diffuseLighting` and `specularLighting` from the MOBJ
render flags, plus the material's ambient/diffuse/specular colors and `shininess`. Blender uses
an unlit emission shader when both flags are false. Lit previews evaluate
normal/light diffuse and colored Blinn-Phong specular terms directly and feed
the result to emission, avoiding Blender environment reflections. Fresh sessions
use the selected static LOBJ set; older sessions retain the fixed-light fallback.
Unsupported coordinate generators fall back to the source diffuse color with a warning.
Preview images are hashed
as session baseline files and packed into the Blender scene.

### LOBJ lighting

Top-level `lighting` contains `previewSetId`, `lightSets`, and an optional
selection warning. Model-group sets come from each 0x34-byte map GOBJ descriptor;
the `map-plit` set comes from the separate public root used for player lighting.
Each set contains flattened LOBJ descriptors with type, raw flags, normalized
RGBA, position/interest WOBJs, computed GX attenuation coefficients, original
point/spot parameters, animation presence, and source offset. Lists and linked
descriptors are bounds-checked and cycle-checked.

Melee stage code, rather than the DAT, marks the model group whose lights become
current. Extraction selects the only animated model-group set when exactly one
exists, otherwise the first nonempty model-group set, and retains every set for
inspection. The warning makes this heuristic explicit. Blender imports all sets
and uses `previewSetId` for material nodes. The selected set's Blender objects
drive the preview shader's direction/position, color, intensity, and enabled
state. The Blender exporter replicates preview-set edits across model-group sets
whose baseline light descriptors are equivalent because stage code can select
any of those duplicate copies at runtime. Infinite LOBJ vectors describe ray travel, so Blender maps them to a
Sun's local `-Z` axis and negates them for the surface-to-light shading vector.
`edits/lights.json` accepts a nonempty list of light IDs with optional `enabled`,
three-byte `color`, game-space `position`, and game-space `interest` fields:

```json
{
  "protocolVersion": 2,
  "lights": [
    {"id": "group-003-light-001", "enabled": true,
     "color": [180, 220, 255], "position": {"x": 0, "y": -20, "z": 0}}
  ]
}
```

Ambient lights accept visibility and color. Infinite and point lights also
accept position; spot lights accept position and interest. The backend
recomputes the light catalog from `source.dat`, rejects duplicate, unknown,
nonfinite, or type-incompatible edits, clones edited WOBJ records, and preserves
LOBJ animation and attenuation records. Blender derives infinite position from
Sun rotation while preserving vector length, and derives spot interest from
position/rotation while preserving the original interest distance. Apply results
include `lightChanged`.

Static descriptor edits can still be overridden by existing LOBJ/WOBJ animation
at runtime. Animation curves, light type, diffuse/specular flags, attenuation,
spot cutoff/functions, and alpha remain read-only.

### Vertex colors

Mesh payloads include nullable `colors0` and `colors1`, each an array of
`{r,g,b,a}` values indexed like positions. Channels are normalized to 0–1 after
GX packed-color expansion (RGB565, RGB8, RGBX8, RGBA4, RGBA6, RGBA8); RGB formats
have alpha 1. Direct, index8 and index16 attributes are supported. Blender stores
these values in FLOAT_COLOR/CORNER attributes and previews the first available
channel as a color multiplier.

Model edits accept optional `colors0` and `colors1` arrays with one finite RGBA
value in [0,1] per triangle corner. These edits require an existing channel on a
static editable rigid mesh, unchanged topology, UVs and material assignment.
The writer expands primitive corners to preserve painted seams and encodes
changed channels as direct RGBA8. Original normals, UVs, other GX attributes,
culling and material bindings are retained. Both color channels are verified
on reload. Omitted channels retain their source values; animated position-only
meshes reject color edits.

Editable material definitions also expose `useVertexColor` and
`canToggleVertexColor`. A nullable `useVertexColor` material edit switches a
supported source vertex-color MOBJ between vertex RGB/alpha and material
diffuse/alpha sources. The copied MOBJ uses explicit matching color and alpha
source modes; the original shared MOBJ remains unchanged.

### Alpha preview state

`preview.alpha` adds the source material alpha, vertex-alpha selection and
material multiplication flag, first texture alpha operation/blend factor, and
pixel-processing blend mode/factors and both alpha comparison references with
their combining operation. Missing PE descriptors use the source render flags'
opaque/translucent and alpha-test defaults. Unsupported blending/custom TEV
settings produce a preview warning. Blender combines Transparent and Emission
shaders with dithered transparency; additive materials use a shader sum so black
leaves the background intact. Older sessions without this metadata retain their
previous preview behavior. No export edit fields are added.

Source references: HSDLib `Common/HSD_MOBJ.cs`, `Common/HSD_TOBJ.cs`,
`GX/Enums.cs`, `Tools/Textures/GXImageConverter.cs`, and renderer alpha operations;
Melee `baselib/tobj.c` alpha expressions and `baselib/state.c` PE defaults.

### Diffuse modulation in previews

`preview.texture.colorOperation` carries the source TOBJ color operation. For
MODULATE (4), Blender multiplies the texture by the source diffuse color, or by
white when `preview.useVertexColor` selects mesh color instead. REPLACE (5)
retains the texture color. BLEND (3) mixes the base color and texture using
`preview.texture.colorBlend` (the source TOBJ blending value). GrNLa Group 003 /
JOBJ 003 / DOBJ 003 uses 75% diffuse RGB (38, 25, 25) and 25% texture. Older sessions without these fields keep their
previous preview behavior. Texture layers assigned to diffuse/ambient and
specular lightmap passes are evaluated separately before the corresponding HSD
lighting calculation; extension layers are applied afterward. Other TEV details
remain approximate.
Regression coverage includes GrNLa Group 003 / JOBJ 004 / DOBJ 000, whose diffuse
RGB is (12, 25, 76), GrSt's vertex-color material, and the colored diffuse plus
grayscale specular maps on GrGb Group 001's first two POBJs.

For BLEND previews, interpolation occurs in GX's stored color-value domain.
Blender's sRGB texture samples are converted back to those values before mixing
with the diffuse RGB; the result is converted to scene-linear once, after the
blend. Mixing already-linearized colors produces an excessively bright result
for dark materials (including GrNLa G003 J003 D003). This affects previews only
and leaves shared image color-space settings and DAT export data unchanged.

### Static material property edits

The additive `editableMaterialProperties` manifest catalog identifies eligible
static model materials by their source mesh ID. It includes original RGB bytes,
ambient/specular RGB, shininess, material alpha, independent alpha source,
MOBJ render flags, the editable flag mask, standard transparency mode, and an
ordered `textures` list. Each texture entry records its source offset, lightmap
role, coordinate source, color/alpha operations, blend value, and blend-edit
permission. The legacy first-layer `textureBlend` fields remain for compatibility. Legacy
sessions without this catalog retain their old permissions.

`edits/materials.json` accepts only the current protocol and a nonempty material
list, for example:

```json
{
  "protocolVersion": 2,
  "materials": [
    {"id": "<source-mesh-id>", "ambient": [64, 64, 70],
     "diffuse": [30, 100, 210], "specular": [255, 255, 255],
     "shininess": 40, "alpha": 0.375,
     "textureBlends": [0.75, 0.5], "alphaSource": 1,
     "transparencyMode": 1, "renderFlags": 1610612756}
  ]
}
```

Each entry changes at least one optional field. Color components are integer
bytes; alpha and every texture blend must be finite in [0,1], while shininess
must be finite in [0,128]. `textureBlends` must match the source TObj count and
can change only layers using a BLEND operation. The backend recomputes eligibility
and field permissions from source.dat. `transparencyMode` is 0 opaque, 1 alpha
blend, 2 additive, or 3 subtractive. `renderFlags` may change only the declared
mask: diffuse/specular/toon, depth offset, effect, shadow, depth always, all
textures, no depth write, and user. Texture/source-selector and undocumented
bits remain protected. `alphaSource` selects Compatibility (0), Material (1),
Vertex (2), or Material × Vertex (3) independently of RGB source. The backend rejects duplicates/unknown fields and
animated targets, and validates the entire batch before output publication.
Materials no longer used after geometry replacement are rejected rather than
silently dropping edits.

The writer appends copied MOBJ/material records, copies and relinks the complete
TObj chain when any layer's blending value changes, and copies or creates a 12-byte PE descriptor
when transparency needs one. Standard transparency updates MOBJ XLU and PE
blend mode/factors together. Depth-always and no-depth-write changes are also
mirrored into an existing PE descriptor. It retains relocation entries and all other
source fields, patches only the selected DOBJ material bindings in existing
data, and resolves source-material assignments to the copied records. Source
materials shared with other objects/animations remain intact. Final reload
verifies bindings and every byte of each copied record. Apply results include
`materialChanged` independently of `modelChanged` and `collisionChanged`.
