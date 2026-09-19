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
matrices when present (original row-major 3x4 floats), and mesh paths. Joint XYZ
rotation fields retain their source encoding; unusual transform modes still need
explicit handling in the Blender importer.

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
preview and weight editing are not implemented. One rigid model target can now
be replaced with grey geometry.

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


### Read-only texture preview assets

New `modelMaterials` entries include `preview: { color, texture, warning }`.
A texture contains its `file`, dimensions, wrap modes, repeats, and source SRT.
Decoded top-left BGRA TGA files live under `models/textures/` and participate in
`baselineFiles` hashes alongside model/collision JSON. Existing sessions without
preview fields remain valid. Preview assets never supply DAT texture data.

The reader accepts tiled GX I4/I8/IA4/IA8/RGB565/RGB5A3/RGBA8/CI4/CI8/CI14X2/CMPR
images, checks data/palette bounds, decodes padded tiles and crops to logical
size. Dimensions are limited to 2048 per axis; unsupported or malformed texture
previews produce a warning and solid fallback rather than blocking extraction.
Blender loads each image as sRGB, packs it, and creates an unlit preview shader.
Source SRT follows `tobj.c:MakeTextureMtx` (S @ R @ T), conjugated by the adapter's
V flip, with independent wrap handling. These nodes are visual aids and are not
used by apply.
