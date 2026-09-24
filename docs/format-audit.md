# Initial format audit and implementation status

## Completed foundation

- .NET 8 solution with Core, CLI, and two xUnit test projects.
- `inspect`, `validate`, `roundtrip --compare`, `extract`, and `apply`, with versioned JSON results.
- HSDRaw stage schema corrections and an explicit `SavePreserving` entry point.
- Local corpus inventory and strict no-edit preservation regression test.
- `GrNLa`: 10 model groups, 16 vertices, 16 lines, one joint, no dynamic lines.
- All 71 local DATs pass inventory, initial validation, and preservation comparison.
  Source warnings are reported for zero-length lines and companion-archive attachments (see below). The five USD variants have not
  been included in this test run.

Milestone 0 is implemented. This also starts milestone 1, but does **not** complete
its full validator. Model hierarchy validation, collision consistency checks, protected-identity
comparison, full `GrNLa` geometry extraction, and static collision `apply` are now
implemented. Blender 4.5.0 import and static collision vertex/property editing are
now available; see [the add-on guide](blender-addon.md). One-target grey model editing is now implemented with automated reload checks;
model in-game acceptance and full GX/reference and remaining GroundParam audits remain pending.

## Source evidence

The local decomp baseline is `a1172b7bffb752efc097fbb59268fbd1f4e18a5c`.
Paths below refer to that checkout, avoiding dependence on moving web sources.

| Structure | Evidence | Implementation |
| --- | --- | --- |
| Map group +0x28 | `melee/src/melee/gr/granime.c:941` and `:1033` index one byte per animation and conditionally set AObj flags | `AnimationFlags` is `HSDByteArray` |
| Map group +0x2C / +0x30 | `melee/src/melee/gr/types.h:2041` defines `s16*` and count; `ground.c:906` resolves each entry as a JOBJ index | `JOBJIndices` is `HSDShortArray`; count is explicit |
| MapLine +4/+6/+8/+A | `melee/src/melee/mp/types.h:47` defines prev_id0, next_id0, prev_id1, next_id1 | Added `PreviousId0`, `NextId0`, `PreviousId1`, `NextId1`; legacy NextLine/PreviousLine names retain their old offsets for compatibility, which are opposite to engine naming |
| MapCollData +0x2C | `melee/src/melee/mp/types.h:124` includes an **inferred** int x2C | New accessor size is 0x30 and exposes `Unknown2C`; existing raw structures are never resized on read/save |
| `map_head` +0x00 / +0x04 | `HSDRaw/Melee/Gr/SBM_Map_Head.cs` identifies the general-point array and count | Typed general-point sets are read directly with bounded counts and stable set/entry IDs |
| General-point entry +0x00 / +0x02 | `SBM_GeneralPointInfo` stores a JOBJ traversal index and `PointType`; HSDRawViewer's `GeneralPointEditor` resolves the index and edits the JOBJ translation | Player/item spawns and camera/blast corner types are exposed; only flat identity-root records are writable |

The incorrect `CollisionLinks2`/`CollisionLinkCount2` API was removed, rather than
keeping a six-byte accessor over a two-byte array. No references to those names
exist elsewhere in the current HSDLib checkout. Do not read beyond
`JOBJIndexCount`: raw buffers can contain padding.

### Collision header qualification

Retail files frequently have only 0x2C bytes between `coll_data` and the next
relocation target. For example, `GrBb` has 0x2C while `GrNLa` has 0x30. Therefore,
the inferred runtime field is not proof that every serialized header has a
separate trailing word. Preserve existing bytes; only inspect `Unknown2C` when
the backing structure actually contains it. The original plan's blanket 0x30
requirement must not be used to reject or expand the shorter retail records.

### Interior pointers and alignment

`GrPs` collision arrays contain interior relocation targets. HSDRaw splits the
array at these targets, so an accessor's fragment length is not the array's
logical element count. Core reads explicit serialized counts and bounds-checks
them against the data section, rather than inferring counts from fragment sizes.
Future edit compilers must account for these aliases before replacing arrays.

`GrCn`, `GrHr`, `GrPs`, and `GrVe` failed strict comparison because the old writer
aligned every fragment to four bytes. Preservation save keeps each existing
fragment's source alignment modulo 32; new buffers retain normal alignment.
Source prefix bytes before the first target are retained too. These changes are
covered by a synthetic archive test and the retail corpus.

## Preservation and validation limits

Preservation save disables trimming, unreachable-structure pruning, and buffer
deduplication. Original source order is retained where HSDRaw permits it. Debug
builds no longer invent public `Orphan0x...` roots. Every original public root
and opaque data fragment participates in comparison.

The comparison normalizes pointers to source-order structure IDs, then hashes
root/reference inventories, version, payload bytes, lengths, and reference
edges. Padding remains significant: it intentionally rejects uncertain changes
rather than ignoring possible payload loss. This is a strict no-edit comparator,
not yet an arbitrary edited/reordered graph equivalence algorithm.

Current validation covers file size, section bounds, root strings, relocation
fields/targets, external reference chains, required stage roots, counted buffers,
finite collision vertices, vertex/link indices, category flags, nonoverlapping
complete category ranges, joint ownership/ranges/bounds, reciprocal links at
simple vertices, and local dynamic attachment indices. Zero-length retail lines
and companion-archive attachments produce warnings. Full GX validation,
complex-link compilation, and stage-code attachment discovery remain pending.
The `grGroundParam` fixed-camera pose/FOV fields are decoded for preview, but
the remaining names and write semantics are not fully audited.
Edited collision exports are covered by automated reload tests. A user has now
reported a successful in-game vertex-edit test; see the acceptance note below.

The relationship between collision joints, dynamic line ranges, serialized
`map_head` bindings, companion targets, and stage-code `GrJoint` arrays is
documented in [the Phase 2 dynamic-collision audit](dynamic-collision-audit.md).
In particular, absence of a serialized binding is not proof that stage code does
not move a collision joint.

## Gameplay general points

`map_head` stores an array of 0x0C-byte general-point descriptors. Each descriptor
points to a JOBJ hierarchy plus an array of four-byte records containing a signed
JOBJ preorder index and signed point type. Corpus and HSDRaw evidence agree on
types 0–3 (player spawns), 4–7 (respawns), 127–147 (21 item spawns), 149/150
(camera-boundary corners), and 151/152 (blast-zone corners). Their authoritative
coordinates are the referenced JOBJ translation fields at +0x2C/+0x30/+0x34.

The first writable subset requires the descriptor root to have identity SRT and
each referenced point to be its direct child. Shared point JOBJs and nested or
transformed hierarchies remain read-only because their stored translations are
not necessarily world coordinates. Multiple general-point sets remain separate;
the editor does not assume set zero is always active. Writes patch only the
twelve translation bytes for movement. Item-spawn topology writes append a leaf
JOBJ and replacement point-record array, preserve unknown records, enforce unique
runtime slots, and verify the result after reloading.

The match camera combines these camera-boundary points with offsets and tuning
loaded from `grGroundParam`. The latter's previewed fixed-camera pose and FOV are
not writable yet. Spawn facing direction has not been identified as a generic
authoritative general-point field and is also deferred.

## HSDLib fork workflow

`HSDLib/` is an independent Git checkout. The top-level project pins the
`Tri-Wing/HSDLib` fork's `blender_plugin` branch; upstream HSDLib remains
`Ploaj/HSDLib`. Keep the fork's history independent from the application.

Keep local HSDRaw patches in that checkout and review them with
`git -C HSDLib diff`. Before updating upstream, commit the project patches on a
project branch, fetch the upstream baseline, rebase that branch, and rerun
`dotnet test MeleeMap.sln`. Review all schema and preservation regressions before
accepting the update. Application projects reference the pinned fork project;
never replace it with an unpatched NuGet binary. HSDRawViewer is outside the
Linux build.


## Model identity validation

`ModelIdentity.Capture` walks group slots and JOBJ descriptors in preorder,
recording parent ownership and ordered DOBJ/POBJ lists. It validates structure
bounds, group array bounds, missing relocations, repeated/cyclic owned joints,
cyclic render lists, and finite JOBJ transforms. `validate` and `roundtrip` invoke
this structural pass. The CLI reports the expanded validation tier as
`archive-model-hierarchy-and-collision-consistency`.

`ModelIdentityCatalog` assigns opaque IDs independently of traversal indices.
`ModelIdentitySnapshot.RequireUnchanged` rejects changes to group slots, node
identity, traversal/list order, roles, and ownership. Transform values are not
part of identity. Shared render descriptors retain their identity while each
ownership occurrence is recorded; they are not assumed to have exclusive owners.
The catalog must be reused for an editing session. A relocated candidate requires
an explicit `Relocate` mapping from source locators to output offsets; creating a
fresh catalog or inferring identity from the new traversal order is incorrect.
Session extraction persists the identity graph. `apply` restores its catalog,
compares it to the source hierarchy, checks protected baseline file hashes, and
verifies model identity again after writing. This API does not detect mutations to the live
HSDRaw graph until they have been serialized and captured with the proper map.

Source evidence at the decomp baseline above:

- `sysdolphin/baselib/jobj.h:127` defines descriptor size, links, transform fields,
  and the DOBJ/spline/particle union.
- `sysdolphin/baselib/jobj.c:JObjLoad` loads child before next and skips owned-child
  loading for `JOBJ_INSTANCE`; `HSD_JObjResolveRefs` resolves that instance target
  from an already loaded descriptor. The validator records instance references
  without traversing them twice and requires an owned target in the same group.
- `melee/gr/ground.c:Ground_801C3FA4` also skips instance children during traversal.
  Further audit of `Ground_GetStageGObj` and `HSD_JObjAddNext` shows that the
  game inserts a runtime wrapper **above** the serialized root. Its child is
  therefore the serialized root, and the snapshot's preorder index matches
  attachment indices. This corrects the earlier root-exclusion assumption.

The Pokémon Stadium DAT family contains literal, non-relocated `-1` group-root
markers. Those slots are retained as `sentinel-group` records without attempting
to dereference them. This is a corpus-observed special case limited to group
roots, not general permission for invalid JOBJ/DOBJ/POBJ pointers.

Synthetic tests exercise hierarchy/list cycles, identity-preserving transforms,
explicit relocation, sibling/group reorder, reparenting, removal, ownership
changes, instance references, non-finite values, truncated targets, missing
relocations, union payloads, and sentinel slots. The corpus test now compares
model identity snapshots before and after no-edit preservation for every DAT.
Remaining model work includes GX buffers, envelopes/ROBJ references, reference
arrays, attachments supplied by stage code, and validation of edits against persisted session IDs.


## Collision consistency and source exceptions

`CollisionData` reads counted records directly, retaining both 16-bit flag words,
both primary links, both alternate links, ranges, joint bounds, and six-byte
attachment records. Validation checks signed 16-bit limits, finite values,
category flags, unique line-to-joint membership, enclosing bounds, vertex/range
membership, and reciprocity/endpoint agreement at simple same-joint vertices.
Bounds tolerate 0.001 game units for independently rounded retail floats.
Dynamic range flags are not forced into static categories. Three-way junctions
are not forced into a single reciprocal chain.

- `GrNSr` contains four zero-length lines; `GrTZd` contains one.
  `mpPruneEmptyLines` in `melee/mp/mplib.c` explicitly handles coincident endpoints
  (with a Pura exception). They produce `COLLISION_ZERO_LENGTH` source warnings,
  and are preserved during no-edit round-trips. `Validate(forEditedExport: true)`
  rejects zero-length geometry; it is the intended collision-compiler gate.
- `GrPu` has nonreciprocal links at branched vertices. These are outside the
  simple-vertex reciprocal constraint, not filename-based exceptions.
- `GrPs`, `GrPs1`, `GrPs2`, and `GrPs4` have attachment targets in sentinel group
  slots. These produce `COLLISION_ATTACHMENT_EXTERNAL` warnings. Their local
  collision-joint indices are still checked; missing companion JOBJs cannot be
  verified from one archive. Other locally defined attachment targets are checked.
- `Ground_InitMapColl` uses GrJoint.x as collision joint and GrJoint.z as JOBJ
  index; its middle field is preserved without promoting a guessed meaning.
  Additional `StageData.joints` attachments in C code remain outside DAT-only
  validation. A valid dynamic line need not have its attachment in this file.

`melee/mp/forward.h` defines high-word kind bits 0..3 and empty bit 7. Low-word
platform and ledge bits are 8 and 9; material occupies its low byte. Extraction
also retains all raw bits. It does not reuse HSDRaw's guessed `Disabled = 16`
label as an engine fact.

## Initial session extraction

`extract` emits all group and model-descriptor identities, source transforms,
complete collision metadata, and every supported POBJ.
`GrNLa` extracts all 93 POBJs (13,597 triangles), including two enveloped POBJs.
The first mesh, retained as `selectedMesh`, is group 1 POBJ 0 at offset 0xFFA0
(14 triangles). Unsupported encodings in other archives remain explicitly deferred. Coordinates remain in game space; the protocol declares the
reversible Blender conversion and numerical tests cover it.

The new asset-independent `GxMeshDecoder` reads bounded display lists and
attributes directly. This avoids HSDRaw's best-effort decoder silently replacing
out-of-range attributes with zeros. It supports indexed/direct positions and
ordinary normals, consumes color/UV attributes without presenting materials,
and triangulates triangles, strips, fans, and quads. Position matrix indices,
envelope tables/weights, single-joint bindings, and source inverse-bind matrices
are extracted explicitly; texture matrix indices are consumed but not previewed.
Shape animation, NBT, and joint/parent matrix blending remain unsupported.
`pobj.c:SetupEnvelopeModelMtx` distinguishes single-weight and blended matrix
paths: extracted envelope-source positions must not be assumed to be pre-baked
world coordinates. The Blender importer implements those source matrix rules for
its static preview; animations, billboards, constraints, and runtime pose updates
remain deferred.
Attribute bounds conservatively stop at the next known relocation/root target;
interior-buffer aliases can therefore require future decoder work.

See [session protocol](session-protocol.md) for the extraction schema and its
limits. Tests cover numeric axis round-trips, primitive winding, malformed GX
indices/lists, session contents, unchanged source hashes, existing-directory
protection, and failed extraction without a published partial session.


## Collision apply

Session protocol v2 adds protected JSON baseline hashes and collision edit input.
`apply` validates the snapshot hash before parsing, restores persistent model
identities, verifies baseline inventory/hashes, rejects unsupported edit files,
and reopens/validates the completed DAT before publishing it to a new path.
No-edit apply is byte-identical, including for read-only collision sessions.

The compiler supports static and dynamic vertex/topology, category, material,
drop-through, and ledge edits within existing collision joints. It welds per
joint at 0.0001 units, sorts all five category/joint ranges, regenerates
directional links, rejects ambiguous junctions, preserves surviving unchanged
alternate-link endpoints, retains enclosing bounds or expands them with an
8-unit margin, and validates the result against signed 16-bit limits. Dynamic
lines retain their stored initial kind while their category range remains
dynamic. Geometry-only writes keep serialized attachment records unchanged.
Extraction dry-runs the replacement compiler before advertising geometry edits.
Zero-length source warnings, existing ambiguous junctions or inconsistent fixed
directions, and buffers with external/interior references remain explicit
restrictions. The manifest and Blender panel report the compiler denial reason;
runtime stage-code uncertainty is not a denial reason.

The collision writer deliberately uses a narrower preservation path than the
general HSDRaw serializer: new leaf buffers are appended before the archive tables,
while original offsets and all original data bytes outside the 0x2C collision
header remain fixed. Unknown trailing header fields survive. Relocation entries
are extended only as necessary; root tables/names are copied exactly. Old buffers
remain, so exports grow. This avoids guessing how to relocate opaque data when
only collision changed. Model export will still require the graph writer path.

Integration tests move a `GrNLa` vertex, add/remove lines, reload collision,
compare all unrelated original data bytes and model identities, and check failure
atomicity, source hash mismatches, protected-file edits, old protocol versions,
malformed edit records, and unsupported edits. Unit tests cover welding, bounds,
links, unknown flag preservation, dynamic-range rebuilding, attachment
preservation, and envelope decoding. Mute City integration coverage edits
attached local geometry, validates/reimports it, and compares all five serialized
attachments. GrGb coverage edits dynamic geometry even though its runtime binding
is supplied by stage code.
A user has reported edited collision vertices working in game. Topology edits
still need manual Dolphin/hardware testing.

## Blender collision preview

The add-on is pinned to Blender 4.5.0. Static pose calculations follow
`jobj.c:HSD_JObjMakeMatrix`, `mtx.c:HSD_MtxSRT`,
`displayfunc.c:_HSD_mkEnvelopeModelNodeMtx`, and
`pobj.c:SetupEnvelopeModelMtx` in the local decomp. Joint parent matrices retain
affine scale compensation without flattening the protected identity hierarchy.

Headless smoke tests import all 93 GrNLa meshes, preserve no-edit DAT bytes, move a
collision vertex, assign flags/materials in Edit Mode, save/reopen a .blend, and
validate edited output. GrGb dynamic collision is geometry-editable and its
edited output passes structural validation. Protected geometry, hierarchy,
transforms, metadata, and
unsupported topology changes are checked before invoking the backend. The ZIP
is a development package with a separately configured CLI. GPU appearance and
manual topology acceptance have not been tested by the agent.

## Collision topology and user acceptance update

Dedicated Split Edge, Extend Collision, Connect Vertices, and Reverse Direction
operators now maintain edge direction and stable IDs. Native edge/vertex deletion
is supported. Export rebuilds adjacency, category ranges, and bounds using the
existing compiler; empty joints, branches, and incompatible directions remain
errors. New IDs derive from the session joint UUID, element role, and an integer
mesh handle, so Undo/Redo and save/load require no separate identity registry.
Native tools that produce duplicate handles or stale endpoints are rejected.

Blender headless tests cover these operations, Undo/Redo, property inheritance,
identity persistence, repeated validation, and export/reload of changed topology.
The user reported importing a stage, moving collision vertices, exporting, and
successfully using the result in game. The exact stage/platform and a broader
ledge/drop-through test matrix were not specified. This confirms that tested
vertex-edit workflow; it does not establish in-game acceptance of topology or
model edits.

## Endpoint direction and collision side correction

The user reported floor platforms assigned Ceiling allowing passage from both
sides. The importer/operator had changed the category without orienting endpoints.
`mplib.c:mpCheckCeiling` checks upward movement, while `mpLineIntersectionH`
branches on endpoint X order: left-to-right accepts downward crossings and
right-to-left accepts upward crossings. `mpLineIntersection` similarly tests
oriented half-planes for sloped edges. The Floor/Ceiling/LeftWall/RightWall lookup
routines also assume category-specific endpoint order.

Assign Type now orients floor edges toward increasing X, ceilings toward decreasing
X, right walls toward decreasing game Y, and left walls toward increasing game Y.
The edited collision compiler rejects mismatches (`COLLISION_FACING`) before
writing, including vertical floors/ceilings and horizontal walls. Existing
no-edit preservation paths are unchanged. Synthetic backend tests and a Blender
floor-to-ceiling-to-floor export/reload regression cover the correction; no
in-game retest of this fix has yet been reported.

The user also confirmed the drop-through toggle working in game. The viewport
now distinguishes solid floors (dark green) from drop-through floors (bright
green); white markers indicate ledge-grab flags.

## First grey model replacement

The user reported all existing features tested and apparently working before
moving to this milestone. This records acceptance feedback for the collision
workflow, without implying a measured all-stage test matrix.

The designated GrNLa target is Group 003 / JOBJ 001 / DOBJ 000 / POBJ 000, with
135 decoded source triangles (including 23 zero-area strip degenerates). Selection
uses structural eligibility, not a filename/offset allowlist. Material animation
matching follows `jobj.c:HSD_JObjAddAnimAll`: material-animation child/sibling
nodes follow the JOBJ tree, and material records follow the DOBJ list. Shared
POBJ/DOBJ descriptors, custom classes, skin bindings, shape animation, translucent
or textured source materials, and material-animated targets remain excluded.

The append-only model writer emits GX_TRIANGLES with GX_DIRECT float32 XYZ
positions and normals, 32-byte display-list alignment, and a constant grey
material (`RENDER_CONSTANT`, diffuse RGBA 128/128/128/255, alpha 1). HSD descriptor
layouts are corroborated by `pobj.c`, `mobj.c:MObjLoad`, and the HSDRaw accessors.
No texture or animation pointers are removed from existing materials: the target
DOBJ alone points to a fresh material. Its group/joint traversal order stays
intact. Edited geometry uses `POBJ_CULLBACK` (0x4000); `pobj.c:HSD_PObjDisp`
maps the POBJ culling bits to GX culling. GrNLa's target originally has
`POBJ_CULLFRONT` (0x8000); inheriting it made a user-tested joined cube appear
inside out in game. Non-culling flags are preserved. Material and display-list
append sizes remain bounded. The corrected culling awaits an in-game retest.

Tests cover selecting the intended target, encoding/decoding exact triangles and
flat normals, input limits/errors, preservation of all 92 other GrNLa meshes and
unrelated source bytes, combined collision/model export, failure-safe overwrites,
Blender Edit Mode movement/replacement, no-op byte equality, protected transforms,
and `.blend` save/load. Backend geometry validation is not an in-game test. A
manual test that the modified grey model appears and the stage remains playable
is still required before declaring milestone 4 complete.


### Multi-target rigid editing

Extraction now advertises `editableMeshes` and per-mesh read-only reasons. Each
changed eligible POBJ is compiled separately and written sequentially; the final
archive verifies all changed meshes and the unchanged identity inventory.
Static textured/translucent materials and pixel descriptors can be replaced by
an independent grey MOBJ without changing the original material data. Hidden
JOBJ flags no longer exclude a rigid target; source visibility stays intact.
Material/texture animation, shared descriptors, bindings, shape animation and
unsupported transforms remain excluded. Multi-POBJ DOBJ meshes use copy-on-write
DOBJ splitting for topology and material edits; position-only edits retain the
source shared DOBJ.

GrNLa exposes 45 editable meshes. The other 48 comprise 45 material-animated
meshes, two skinned meshes, and one billboard mesh. The prior one-target export,
joined cube geometry, and culling correction have user-confirmed in-game results.
Multi-target output still needs an in-game check. Automated coverage exercises
all eligible backend targets in one batch, duplicate/unsupported/invalid targets,
untouched mesh preservation, two Blender joins, multi-object Edit Mode, collision
combined with model edits, save/reopen, and current capability-based permissions.


### Appearance-preserving vertex edits

`ModelPositionWriter` retains original primitive commands/counts, all non-position
attribute tokens and buffers, material/texture pointers, and POBJ flags. It
appends cloned attribute descriptors with direct float32 XYZ positions plus a
replacement display list. Only POBJ +8 and +14..+19 change in the original data
section. Original position buffers remain intact even when shared by meshes.
Normals are retained exactly; this path does not regenerate lighting normals.

Tests cover direct XY/XYZ float positions, indexed8/indexed16 fixed-point
positions, aliased source positions, mixed normal/color/UV storage, descriptor
relocations and exact preservation of original bytes outside allowed fields.
Integration coverage moves all but one eligible GrNLa mesh in one batch while
replacing the final mesh's topology, checks source material/shading preservation,
and exercises mixed paths through Blender Edit Mode and save/reopen. Legacy
single-target scenes also preserve appearance for vertex-only edits. User tests
have confirmed multi-target grey exports; this new preservation path awaits an
in-game test.


### Stage material assignment and new-geometry UVs

The first material-authoring slice reuses static opaque MOBJ/TOBJ data from
eligible rigid targets. `HSD_MOBJ.cs` / `mobj.c` render flags identify unsupported
vertex-color, alpha, special-pass and toon requirements; `HSD_TOBJ.cs` /
`tobj.c` identify a single regular UV source (`GX_TG_TEX0 = 4`, coordinate mode
UV). Original texture images, transforms, filtering and TEV data are reused.
This is not arbitrary Blender-shader or texture-image import.

UV0 (GX attribute 13) is decoded with the same component formats and fixed-point
fractions as other scalar attributes. Blender flips V at its boundary and stores
UVs per loop. The replacement protocol supplies per-triangle-corner UVs and a
source material identity; apply resolves that identity against the original
archive. Direct float32 UV0 data is emitted, maintaining seams and dropping UV
corners alongside degenerate faces. A material/UV-only edit retains source culling
and normals; topology replacement uses flat normals and back-face culling.

Regression checks cover UV seams, degenerate UV alignment, material identity,
source-byte preservation, unsupported/missing/nonfinite UV inputs, output failure
safety, UV-only changes, material-only assignment, native cube Join, all-face
assignment, `.blend` persistence, and explicit grey fallback. Appearance-preserving
vertex edits have now been user-tested in game. New material/UV exports await
manual game testing.


### Packed texture previews

`TexturePreview` uses the existing HSDRaw GX image converter behind explicit
format, dimension, palette, and buffer bounds checks. Image/TLUT layout is taken
from `HSD_TOBJ.cs`: TOBJ +0x4C/+0x50, image dimensions +4/+6 and format +8,
TLUT format +4/count +12. Tiles decode at padded dimensions and crop before TGA
serialization. BGRA byte order/top-left origin are checked with known RGBA8,
CI4/RGB565 palette, and CMPR fixtures. RGB565 palette channels are expanded to
the full range after HSDRaw's truncated conversion.

`tobj.c:MakeTextureMtx` provides repeat/scale, signed Z rotation, translation and
mirror-T offset. Preview nodes use the corresponding S @ R @ T matrix with
Blender V-flip conjugation and independent wrap axes. Previews deliberately show
unlit image colors rather than reproducing the game's lighting/TEV pipeline.
Checks cover packed images, shader links, UV transforms, Material Preview/UV
Editor selection, .blend persistence, unchanged no-op DAT bytes, and a CPU
render inspected for actual texture output. Material/UV export is user-confirmed
working in game; preview appearance remains an approximation.

## Animated-material rigid vertex editing

Material/texture animation now selects a position-only editing mode instead of
excluding an otherwise eligible rigid mesh. GrNLa exposes 90 targets: 45 full
geometry targets and 45 position-only targets; two skinned meshes and one
billboard remain protected. The position writer retains source primitive
structure and all non-position attributes. Original archive data stays byte
identical outside the approved POBJ attribute/display-list pointer and size
fields, including material and animation data. Backend coverage mixes all
position-only targets with full geometry replacements and rejects attempts to
relax permissions through the manifest. Blender coverage includes Edit Mode,
UV/material/topology rejection, no-edit export and save/reopen. In-game animation
preservation still requires user verification.
