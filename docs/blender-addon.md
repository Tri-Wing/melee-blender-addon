# Blender collision editing preview

Development target: **Blender 4.5.0 on Linux**, with .NET 8 for the development
backend. This milestone imports stage models and edits existing static collision,
supported rigid geometry/material properties, lights, static JOBJ transforms, and
existing JOBJ transform animations. It imports animation slots as Blender Actions,
uses native editable bone curves for supported JOBJ tracks, and plays supported
material animation channels on preview materials.
The user has confirmed collision editing, model vertex movement, joined cubes,
and corrected face culling in game. Multi-target export has also been confirmed in game. Appearance-preserving
vertex editing has also been confirmed in game. New-geometry material/UV export
awaits an in-game check.

## Install

From the repository root:

```sh
dotnet build MeleeMap.sln
python3 scripts/package_blender.py
```

In Blender, open **Edit → Preferences → Add-ons → Install from Disk**, select
`artifacts/melee-map-editor-0.1.0.zip`, and enable **Melee Map Editor**. Expand its
preferences and set **MeleeMap CLI** to the absolute path of
`src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll`. Leave **dotnet executable** as
`dotnet`, or supply its absolute path if Blender's environment cannot find it.
A published standalone `meleemap` executable can also be selected. The current
ZIP contains Python only; bundling the backend is a later packaging milestone.

## Development: load directly from the repository

You do not need to build or reinstall a ZIP during development. To launch
Blender with the repository add-on loaded automatically, run:

```sh
./scripts/launch_blender.sh
# Or open an existing scene before loading the add-on:
./scripts/launch_blender.sh /path/to/stage.blend
# Or immediately import a DAT into the startup scene:
./scripts/launch_blender.sh ~/projects/melee-map-editor/example_assets/GrNLa.dat
```

The launcher uses `blender` from your PATH (currently the Snap installation on
this machine). Set `BLENDER_BIN=/path/to/blender` to choose another executable.
It works from any working directory. An optional first `.dat` argument is imported
after the add-on loads; remaining arguments are forwarded to Blender. Without a
DAT argument, `.blend` paths and normal Blender options work as before. Avoid Blender's
`--` script-argument separator, since the launcher appends `--python` itself.
It does not rebuild the backend; run `dotnet build MeleeMap.sln` after C# changes.

To load or reload inside an already running Blender session:

1. In Blender, switch an editor to **Text Editor** (or use the Scripting workspace).
2. Choose **Text → Open** and open `scripts/load_blender_addon.py` from this checkout.
3. Choose **Text → Run Script** (`Alt+P` with the pointer over the Text Editor).

Open the actual file from the checkout rather than copying it into an unnamed
Text block. The loader uses the active Text block's saved filepath when Blender
supplies a synthetic script filename. For a copied script, set `REPOSITORY = ""`
at the top to the absolute checkout folder (the folder containing
`blender_addon`), or set the `MELEEMAP_REPO` environment variable before launching
Blender. If you already have an older loader open, reopen the updated file from
disk before running it; Blender can retain the old text in memory.

This loads `blender_addon/melee_map_editor` directly from disk. It unregisters any
currently loaded copy, including a ZIP installation, and then loads the checkout.
If the CLI preference is empty and a Debug build exists, it selects that build.
An existing CLI preference is preserved.

After changing add-on Python files, **Run Script again**. The loader refreshes
all helper modules, registered classes, and callbacks, while retaining your
current scene, session, collision edits, and preferences. Running individual
package files such as `__init__.py` as standalone scripts will not work because
they use package-relative imports.

This setup is per Blender process: after restarting Blender, run the loader once
again. It does not move or delete an installed ZIP, create symlinks, or save a
machine-specific repository path in your preferences. Keep the loader open in
the Text Editor for quick access. Saving your `.blend` before reloading unfinished
code is useful: a Python error can leave the add-on disabled until you fix it and
run the loader again. Session or scene-schema changes require a fresh DAT import;
the add-on reports this instead of migrating older scenes.

Backend C# changes still need `dotnet build MeleeMap.sln`; the CLI runs as a new
process on each operation, so it does not need a Blender restart. To test the
reload workflow headlessly:

```sh
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/reload.py
```

## Edit a stage

1. Open the 3D View sidebar (`N`), then **Melee Map → Stage → Import Stage DAT**.
2. Choose `GrNLa.dat`. Its ten groups appear under **Melee Stage → Models**,
   containing 93 grey meshes and the protected source hierarchy. Toggle all
   models with **Show / Hide Model Groups**, or individual groups in the Outliner.
   The **Reference** collection contains an active **Melee Camera** built from
   the camera pose and field of view stored in `grGroundParam`. Press Numpad 0
   to view through it. On ordinary stages this is a stable stage preview because
   the in-game match camera moves around the active fighters; fixed-camera stages
   use the same stored pose directly. The Blender camera is preview-only and may
   be moved, renamed, or deleted without affecting DAT export.
   The scene World uses the selected group fog color, matching the clear color
   Melee displays behind all stage geometry. This atmosphere is also preview-only.
3. In the Outliner, expand **Melee Stage → Gameplay** and select a spawn or
   boundary guide. Player spawn points are colored diamond outlines (P1 red, P2
   blue, P3 yellow, P4 green), respawns use smaller diamond outlines, camera
   bounds are cyan, and the blast zone is red. These guides use the same
   always-visible viewport line rendering as collision rather than filled faces
   or materials. Move a marker with Blender's Move tool. Move a rectangle to
   shift it, or scale it on Blender X/Z to change its width and height. The Item
   panel provides exact numeric transforms. Expand **Gameplay Tools** to see the
   selected set's item-spawn count or add a spawn with a viewport click. Select
   an item-spawn marker and use Blender's normal Delete command to remove it.
   Selected player/item diamonds and camera/blast rectangles retain their guide
   color with the same wide orange selection halo used by collision components.
4. Expand **Collision** in the Outliner to find one collection per collision
   joint and one mesh object per connected component. Hide components with the
   normal eye toggle or `H` when overlapping edges get in the way; hidden
   components remain part of export. Select one or more visible collision
   components and press `Tab`, just like any other Blender mesh, to enter
   multi-object Edit Mode. Front view (`Numpad 1`) looks onto the gameplay plane.
   Move vertices along Blender X and Z; keep Blender Y at zero. Press `Tab` again
   to return to Object Mode. If native deletion leaves disconnected geometry,
   use **Separate Disconnected Islands** to rebuild the Outliner components.
   Selected component lines retain their collision-type color with a wide orange
   halo, so selection remains visible despite the always-on-top overlay. Selected
   isolated construction vertices receive an orange cross.
   Use the dedicated topology tools below to add or reconnect collision. Native
   vertex/edge deletion is supported. Object Mode translation on local X/Z,
   scaling on local X/Z, and rotation around local Y are baked into the exported
   collision vertices. The component origin is its collision-joint origin, which
   is also the pivot for scaling and rotation. Moving on local Y or rotating around
   local X/Z takes collision out of Melee's 2D plane and is rejected. Parenting,
   modifiers, and constraints are also rejected.
5. Select edges to assign a collision type or named surface type, or toggle
   drop-through and ledge-grab bits. Assigning a type also orients its endpoints
   for the game: floors left-to-right, ceilings right-to-left, right walls down,
   and left walls up. Solid floors are dark green and drop-through floors bright green.
   Ceilings are red, right walls blue, left walls amber, and dynamic collision
   purple. White crosses mark lines with ledge-grab set. Blender's Overlays toggle controls this
   visualization. Unknown source flag bits remain protected.
6. Under **Validate & Export**, choose **Validate Stage**, then **Export Stage
   DAT**. The filename defaults to the imported DAT name.
   Both actions check the source/session hashes, protected scene content,
   collision compilation, and output reload. Blender asks for confirmation when
   replacing an existing file. Replacement happens only after validation succeeds;
   failed exports leave the previous file intact.
7. Use **Open Export Directory** to find the result for manual insertion/testing.

Vertex changes and property assignments can be exported while still in
multi-object Edit Mode. An untouched scene exports a byte-identical copy of the
input DAT, regardless of component visibility, names, selection, or Outliner
order. The
sidebar tracks collision changes; validation/export also detects unsupported
model or hierarchy changes and explains which class of edit must be undone.

Use a new Blender scene for a second import. Dynamic and attached collision
geometry is editable in the local coordinate space stored by the DAT. Existing
serialized attachment records are preserved exactly during geometry export;
adding, deleting, and retargeting attachments is a separate pending tool. When a
collision joint has exactly one serialized attachment to a JOBJ in the same DAT,
all of its component objects and colored collision lines follow that JOBJ's
current pose and animation while their stored vertices remain joint-local. Any
Object Mode transform made on top of that managed pose follows the attachment and
is baked relative to the collision joint on export; the JOBJ's pose itself is not
baked into collision coordinates.
External targets, repeated bindings, and bindings supplied only by stage code can
limit animated preview, but do not lock the serialized collision geometry. The
editor deliberately does not infer or modify stage-code behavior. `GrGb.dat`
covers code-controlled dynamic geometry export, and `GrMc.dat` covers resolved
moving-collision preview plus local geometry export/reimport. Sources containing
zero-length lines or source graphs that the replacement compiler cannot yet
reproduce exactly remain read-only, with the concrete compiler reason shown in
the panel. This includes some pre-existing branch and direction patterns; it is
unrelated to whether stage code manipulates the collision at runtime. Missing
extracted meshes are reported in the sidebar.

## Gameplay points and boundaries

The **Gameplay** collection represents typed `map_head` general points as scene
objects instead of raw numbers. The current writable subset includes player
spawns, respawns, all 21 item-spawn types, camera bounds, and blast zones.
Stages with more than one general-point set receive a separate collection and
guides for each set. **Gameplay Tools** targets the selected guide's set, falling
back to the numeric target-set field when no guide is selected. **Add Item
Spawn** assigns the lowest unused slot, then projects the viewport click onto the
same depth plane as the set's existing item markers. Blender's normal Delete
command removes imported editable item markers and cancels newly added markers;
there is no separate deletion button. The count is capped at Melee's 21 runtime
slots per set.

Boundary guides are reconstructed from their two source corner points. Their
orientation is retained even when a source stage stores the corners in an
unusual order. Export rejects zero-size rectangles and, on stages whose original
blast zone contains the camera bounds, rejects edits that break that containment.
Marker scale and all gameplay-guide rotation are visual structure rather than
stage data and must remain unchanged.

For safety, points are writable only when their general-point root is an identity
transform and the point is a direct child of that root. A transformed, nested,
or shared JOBJ remains read-only with an explanation in the sidebar. This avoids
guessing how stage-specific hierarchies convert local translations into runtime
positions. Facing direction and match-camera tuning are not editable in this
first Phase 1 slice.

## Add an external model

Stages with eligible static JOBJ/DOBJ chains expose attachment choices computed
from their archive structure; no filename or hash allowlist is used. Import an
external mesh with Blender's normal OBJ, glTF, or FBX importer (or a Collada/DAE
import extension), select its mesh objects, expand **Import Models**, and choose
**Add Selected Models to Stage**. Pick either an existing-JOBJ attachment or a
**New JOBJ Chain** entry beneath a compatible model-group root. The existing mode inherits that JOBJ's
render behavior. The new-chain mode creates an identity-transform child with its
own opaque lighting flags, so it can use the stage's diffuse lights without
changing any existing model's lighting mode. After every selected mesh validates,
the add-on replaces it with an evaluated, bone-parented model inside the selected
**Melee Stage → Models → Model Group** collection. Its object name starts with
the imported source object's name and remains user-editable. Each material partition is represented as a
POBJ mesh beneath its own DOBJ object; new-chain placement also adds a generated
identity-transform JOBJ bone to the group's existing armature and bone-parents
those DOBJ branches to it. This matches the hierarchy emitted to
the DAT, while any additional GX-size chunking remains an export detail. Duplicate first if you want to retain an
imported reference. To remove a model, select its mesh object and use Blender's
normal **Delete** command. This is the same workflow for imported additions and
original editable stage models. A failed registration leaves the selected source
objects untouched.

Each face must have a material. The initial converter accepts either a constant
base color or one Image Texture connected to a Principled BSDF Base Color input.
The texture may be direct or pass through a simple Multiply with vertex color,
as produced by common DAE importers. Vertex-color modulation is omitted with a
conversion warning while the connected texture is retained. The image may use
the active UV map, a direct UV Map node, or the UV output of Texture Coordinate.
Flat projection, Repeat/Extend wrapping, and Closest/Linear filtering are
supported. Other shader inputs are omitted and reported; image alpha is embedded
but renders opaque. Complex node graphs, missing UVs/images, animation,
constraints, modifiers, and shape keys are rejected with an actionable error.
Object transforms, evaluated corner normals, material assignments, UV seams, UV
tiling, packed images, and unsaved image pixels are retained.
Stored sRGB channel values are copied directly into the DAT texture; Blender's
image color-space setting is not applied a second time during export.
Registration also replaces each imported shader with the chosen Melee material
preset immediately. Existing-JOBJ attachments use the unlit opaque preview.
New-JOBJ-chain additions use the stage-light diffuse preview, including the
material's half-strength ambient channel and current editable stage lights. This
keeps the registered object aligned with its expected in-engine appearance;
unsupported Principled metallic, roughness, alpha, specular, and vertex-color
behavior is still omitted.

Validate and export normally. Temporary `edits/additions.json` and raw RGBA
payloads exist only for the backend call and are removed afterward; registered
objects and packed images remain in the `.blend`. Repeated exports always apply
to the immutable imported source, so additions do not accumulate. Reimporting
the exported DAT treats them as ordinary source models. The automated Blender
round trip is passing, but this initial path still awaits an in-game textured
fixture check. The new placement schema requires a fresh DAT import; pending
models registered by an older add-on/backend must be registered again.

## Edit static JOBJ transforms

Fresh imports create one armature for each model group and represent every JOBJ
as a bone. Select a supported bone directly, or select one of its model
descendants and click **Select Editable JOBJ** under **JOBJ & Animation**. Use
Blender's normal Pose Mode Move, Rotate, and Scale tools. Rotation mode must
remain **XYZ Euler**. Validate and export normally; JOBJ transforms compose with
geometry, collision, material, and light edits in the same export.

The first static-transform slice keeps animated, constrained, skinned,
instanced, billboard/IK/quaternion, custom-matrix, and collision-attached
hierarchies read-only. Scaling a JOBJ that has child joints is limited to a
uniform scale change; leaf JOBJs may be scaled independently per axis. JOBJ
creation, deletion, reparenting, sibling reordering, and moving render objects
between JOBJs remain read-only. A fresh import is required because older `.blend`
files do not contain the armatures, JOBJ eligibility catalog, or transform
baselines. Rigid models follow their owning bones. Enveloped models import with
their source weights as Blender vertex groups and use an Armature modifier driven
by generated deform bones. The mesh object retains its fixed bind-pose transform;
the armature supplies all animated motion so the owner JOBJ is not applied twice.
Their geometry and weights remain read-only.

## Edit and preview stage animations

Each model-group joint or material animation slot imports as a Blender Action
with the user-facing name `Stage Animation`. Joint and material data sharing a
source slot play together. The first slot is active by default. Move the
timeline to preview the stage motion. The sidebar shows the current Action for
the selected armature and provides **Previous** and **Next** controls when a
group has multiple slots. You can also choose an imported Action in Blender's
Action Editor.

Playback evaluates the decoded HSD constant, linear, Hermite, and slope keys
and bakes editable JOBJ motion into native Blender bone Location, Rotation, and
Scale curves. Edit those curves in the Dope Sheet or Graph Editor, or pose a bone
and insert transform keys. It drives the source JOBJ bones, generated envelope
bones, rigid children, and weighted meshes.
Material playback drives MOBJ ambient, diffuse, specular, and alpha values, plus
TObj translation, scale, rotation, blend values, texture image/palette swaps, and
konst/TEV0/TEV1 color registers used by supported custom TEV stages.
For faster blended animation, turn off **Render Properties > Sampling > Viewport >
Temporal Reprojection**. With that setting off, changing numeric values use
object attributes instead of repeated shader-socket updates. Blending, color,
alpha, UV, and TEV equations remain intact; image/palette swaps still change the
sampled image. Reload the add-on to use this with an existing scene.
Temporal reprojection smooths edges/noise across frames, so disabling it can
change antialiasing. The add-on does not change the setting automatically:
with it enabled, playback retains the original shader-socket path to avoid
trails observed with animated attributes. Neither path changes DAT export.
Indexed textures are decoded for the image and palette combinations reached by
the timeline. Animated pixel-engine reference values and material animation
editing/export are not implemented yet.
When an MOBJ selects vertex color as its RGB source, playback keeps the texture
pass neutral and continues to use the mesh's vertex RGB instead of its unused
MOBJ diffuse value.
Looping follows both serialized AOBJ flags and the model-group animation flag
byte that Melee applies at runtime; the latter is used by animations such as
GrNLa Group 003. The preview wraps when the HSD end frame is reached, matching
`HSD_AObjInterpretAnim`.
Actions and their selected slots survive `.blend` save/load. Export samples edited
JOBJ curves at each whole source frame, converts them back to game-local JOBJ SRT,
and writes HSD linear transform tracks. The existing animation duration and loop
behavior remain fixed. This first editing slice only supports nodes whose existing
FOBJ descriptors are all recognized transform channels; it does not add animation
to a previously static JOBJ. Unedited animation data remains byte-identical, and
material animation remains preview-only. Re-import the stage after updating the
add-on because older scenes do not contain these native editable curves.

## Collision surfaces

The **Surface** dropdown replaces the numeric Material ID control. Select edges,
choose a surface, and click **Assign**. Connect Vertices uses the same selector;
Split and Extend inherit the existing edge's surface.

This value selects the game's collision surface response, including friction
and contact effects. It does not change the visible stage texture or Blender
shading. Response tables are stage-specific, so a name alone does not guarantee
identical behavior on every stage.

Names follow `melee/src/melee/mp/forward.h` (`mp_Terrain`): Basic, Rock, Grass,
Dirt, Wood, Light Metal, Heavy Metal, Paper, Goop, Birdo, Water, Unknown (11), UFO,
Turtle, Snow, Ice, Game & Watch, Unknown (17), Checkered, and Unknown (19).
Unresolved IDs are deliberately not given speculative names. **Custom / Unknown
ID** exposes numeric values outside that range. Imported values remain unchanged
until you assign a surface; existing saved scenes retain their integer setting.

Source behavior: `mplib.c:mpLib_800569EC` indexes the stage response table using
the low byte for friction, reached via `mpcoll.c:mpColl_8004CA6C` and
`ft_081B.c:ft_GetGroundFrictionMultiplier`. The adjacent `mpLib_80056A*` functions
supply terrain-dependent contact responses to `ft_80084A80`.

## Edit the supported model

New imports advertise **90 of 93 meshes in the original GrNLa.dat**: 45 support
full geometry editing and 45 support vertex movement with animated materials.
Select a model in the Outliner or viewport, then use **Edit Selected Model**.
The **Selected Stage Object** panel reports its live source Group/JOBJ/DOBJ/POBJ
path, editing capability, and any read-only reason. Vertex-only models with
animated materials can move vertices
using Blender's native tools, but keep topology, UVs and material assignments
unchanged. Their original normals, materials, textures and animation data are
preserved on export. Blender previews supported material and texture animation,
including image and palette swaps. Additional game shader effects are not reproduced. The sidebar displays
this restriction and disables material assignment. Re-import after reloading
the add-on to enable these newly supported meshes.

The three remaining read-only meshes use skinning (2) or billboard transforms (1).

Each editable object keeps its own identity and geometry. Edit multiple objects
and export them together; native multi-object Edit Mode also works. Only changed
meshes receive geometry updates; topology replacements also receive grey materials. Never join two imported stage
objects together: doing so removes a protected identity. Join newly created
shapes into a fully editable object instead; joining shapes to a vertex-only
model is rejected.

1. Reload the add-on or launch with `scripts/launch_blender.sh`. The development
   backend must be rebuilt after C# updates (`dotnet build MeleeMap.sln`).
2. **Import the DAT into a new Blender scene.** Protocol v3/schema v2 sessions
   carry per-model operation capabilities. Older sessions and saved scene
   bookkeeping are not migrated. To carry over work, export it with its matching
   build first, then import that edited DAT.
3. Select an **Editable Model**, then click **Edit Selected Model** to enter
   Edit Mode. The sidebar displays the selected model's edit status.
4. Move vertices. For fully editable models, use native Blender mesh tools to add/delete faces and replace
   geometry **inside this object**. Unlike collision, render geometry can be
   edited freely in all three dimensions. Quads/ngons are triangulated on export.
   Delete the mesh object with Blender's normal **Delete** command to remove that
   model from the exported stage. Object Mode move, rotate, and scale
   transforms on editable rigid models are applied to a temporary mesh copy and
   baked into exported positions and normals; the Blender object is not mutated.
   Negative scale also reverses the exported triangle winding and retains each
   source corner's UV and color data, so mirrored models remain outward-facing
   without losing their appearance. Parenting changes and other identity-bearing
   objects remain unsupported. Apply other desired shape changes in Edit Mode.
5. Validate and export normally. Model and collision changes can be exported
   together. Editing model geometry does **not** automatically change collision.

To add a separate shape, create and shape it normally in Object Mode, select it,
then select **Editable Model** last so that the supported model is active. Use
**Ctrl+J** to join. Blender converts the added shape into the target's local
coordinates and keeps its placement. Keep the supported model's own transform
and parenting unchanged. Material slots brought in by Join are accepted; all
joined geometry can use a supported stage material or the grey fallback. Separate objects are
not exported until joined into the supported model.

**Moving vertices without changing topology preserves the source appearance in
game:** material, textures, UVs, vertex colors, normals, transparency, and culling
remain intact. Source normals are retained exactly rather than recalculated, so
large shape changes may need future normal-editing support. Supported stage
materials have texture previews.

**Changing topology** (adding/joining shapes, deleting faces, splitting or merging
vertices, or changing indexed faces) exports generated flat normals and back-face
culling, using the assigned supported stage material or the grey fallback. Keep Blender face normals pointing
outward; do not flip them to compensate for the earlier cube culling bug.

Both paths can be used on different models in one export. Untouched models keep
their original data; an entirely untouched stage remains a byte-identical export.

Limits are 65,535 input vertices and 14,000 triangulated faces. Exact zero-area
triangles (including source GX strip degenerates) are omitted on the grey
topology-replacement path; an empty result fails validation. Vertex-only edits
retain original primitive topology, including strip degenerates. Loose vertices/edges do not render. The selected source mesh
can contain separate vertices at the same position; native Merge by Distance is
available for this model if appropriate for your edit.

Object transforms on read-only models, group/JOBJ/DOBJ/POBJ identities, and
unsupported meshes remain protected. Editable rigid meshes accept Object Mode
transforms, and explicitly marked **Editable JOBJ** nodes accept SRT edits.
Eligible meshes must be rigid and unbound,
have no custom class, and have no group shape animation. A sole POBJ in a DOBJ
supports full topology and material editing. Multiple POBJs under one DOBJ are
also fully editable and deletable. Vertex-only edits retain their shared source
DOBJ; topology, material-assignment, and material-property edits use copy-on-write
splitting to move only the affected POBJ beneath an appended one-POBJ DOBJ. The
unedited sibling keeps the original DOBJ and material. Shared/interior descriptors, instancing, billboard transforms, and
quaternion joints remain unsupported. Static textured and translucent source
materials are preserved for vertex-only edits. Topology changes use a supported
assigned stage material or opaque grey. Source-hidden
models are editable, but their original visibility and joint animation remain
intact: editing them does not force them to become visible in game.

Blender object, collection, and Action names are user-facing labels, not stage
identities. Rename them freely. Stable `mme_id` metadata and actual ownership
relationships drive validation and export; the **Selected Stage Object** panel
derives source locators and attachment state from that metadata instead of
parsing names. Bone names remain internal Blender binding keys for animation and
skinning and should not be renamed.

## Materials and UVs on new geometry

1. Export existing work you want to keep, reload the add-on, and **import the DAT
   into a new scene** to populate the stage-material catalog and source UV maps.
   Older scenes still support their existing geometry/vertex-edit workflows.
2. Select an editable model. After joining shapes, click **Assign Model Material**
   in the Melee Map sidebar. Choose a named **Stage G… J… D…** material. Entries
   marked **(UV)** use a texture. This assigns the material to **all faces**.
3. Use Blender's usual Edit Mode/UV tools to unwrap and adjust faces. Export uses
   the active UV map. UVs use Blender's bottom-origin convention; import/export
   reverses V relative to the game's coordinates. UV seams are retained per corner.
4. Export normally. Several objects may use different materials in the same DAT.
   Test the final
   appearance in game; lighting and shader effects are approximate in the preview.

Compatible source materials are placed in Blender's material list as well, so
native material-slot assignment works if every face uses the same supported
material. This step reuses **existing static opaque stage materials**, including
regular UV0 textures and source lighting settings. It does not import image files
or translate arbitrary Blender shader nodes. Unsupported vertex-color inputs,
transparency, animation, toon/reflection/bump mapping, and multiple texture chains
are excluded from the assignment catalog.

There is **one exported material per model object**. Mixed stage-material slots,
or a stage material mixed with a cube's ordinary Blender material, fail export
with an explanation. Use **Assign Model Material** to unify all faces, or choose
**Grey Export** to explicitly use the old grey path. Missing UV maps on a textured
assignment also fail export; they never silently discard the selected texture.

UV-only and material-assignment edits are detected even without moving vertices.
When topology is unchanged, source normals and culling are retained for stage
material/UV edits. Ordinary vertex-only edits still preserve all original rendering
attributes. Source texture transforms and filtering remain intact, so final UV
mapping may include the original material's scale/offset.

## Texture previews

Export existing edits you want to retain, reload `load_blender_addon.py`, and
import that DAT into a new scene. New extraction sessions include decoded images
for the supported stage-material catalog. Existing `.blend` files without those
images need a fresh import to enable this feature.

Assigning a different stage material also updates open UV Editors.

Images are packed into the `.blend` and remain available after saving/reopening.
The preview applies the source UV repeat, scale, rotation, translation, clamp,
and mirror settings without changing the editable UV map. The UV Editor shows
the original image; the viewport shows it with the source material transforms.
Standard multi-texture materials using the first two GX UV channels apply each
TObj in source order within its HSD lightmap pass. Diffuse/ambient textures,
specular textures, and extension textures are routed separately. Both UV channels
and each layer's independent transform, color operation, and blend value are
retained. Supported custom TEV ADD/SUB color stages also apply their source inputs,
constant registers, bias, scale, and clamp. This includes the two moving water layers on GrYt Group 001 / JOBJ 008 /
DOBJ 031 and the colored diffuse plus grayscale specular textures on GrGb Group
001's first two POBJs.

Reflection-mapped metallic materials generate coordinates from the camera-space
surface normal, matching HSD's environment-map projection before applying the
texture transform. GrGb Group 002 / JOBJ 052 / DOBJ 007 and JOBJ 053 / DOBJ 004
use this path.

The texture preview also uses supported MOBJ lighting flags and imported LOBJ
descriptor values. TEV comparison operations and full cross-stage channel routing,
mip filtering, animated materials
and lights, and unsupported material types are not reproduced. Unsupported/invalid images
fall back to a solid material with a preview warning. Texture painting and shader
node changes do not author game assets or affect DAT export.

## Inspect a selected edge

In Collision Edit Mode, select an edge and expand **Selected Edge Metadata**
under **Melee Map → Collision**. It reads the actual selected edge,
independently of the type and surface dropdowns used for assignment. It shows the current type, named surface
and numeric ID, drop-through/ledge flags, disabled state, collision joint,
direction, length, endpoint coordinates, and adjacent edges. Drop-through on a
non-floor is labeled as floor-only. New edges are identified explicitly.

When several edges are selected, the panel displays the active edge and selection
count. Use Edge Select mode and make an edge active; without an active edge it
asks you to choose one rather than displaying an arbitrary selection.

Expand **Raw Flags and Source Metadata** for stored/export high flags, low flags,
unknown bits, UUIDs, Blender coordinates, and the original source flags, vertex
indices, and previous/next/alternate links. Original links are labeled as source
records, not current exported links: export rebuilds them from edited topology.
All inspector values are read-only and refresh from the current selection.

## Collision topology tools

Work in Collision Edit Mode. Use vertex selection for Extend and Connect, and
edge selection for Split and Reverse. The overlay arrows show edge direction.

- **Place Collision Vertex:** click the button, then click in the 3D viewport.
  The view ray is projected onto the collision object's plane, so the new vertex
  always has Blender-local Y = 0 even in perspective view. The isolated vertex is
  selected after placement. Connect it to another endpoint before export;
  unconnected points are editing aids and do not become game collision by themselves.
- **Split Edge:** select one or more edges. Each is split at its midpoint,
  preserving direction, type, material, flags, and joint. Move the new vertices
  with Blender's normal move tool.
- **Extend Collision:** select exactly one open endpoint (one incident edge).
  Enter an X/Z offset in game units. The new edge inherits its neighbor's
  properties and continues its direction. The new vertex is selected so you can
  reposition it with `G`, constrained to X or Z as needed.
- **Connect Vertices:** select two open endpoints or isolated surviving vertices,
  including endpoints in two different component objects. Components owned by
  the same collision joint are merged and connected; cross-joint connections are
  rejected before either object changes. Choose a collision type and named
  surface in the dialog. Direction and joint are inferred from connected edges.
  Two isolated vertices use the chosen joint (numbered from 1) and a stable
  endpoint order; use Reverse if needed. New connections start with drop-through
  and ledge flags off; assign them afterward as appropriate.
- **Reverse Direction:** select edges to exchange their start/end directions.
  Direction also determines the collidable side; it must agree with the type.
  Reassigning a type automatically repairs direction. Reversing edges without
  updating their types can fail the facing check; inconsistent directions within
  a connected chain also fail validation.
- **Native delete:** Blender's Delete Edges/Delete Vertices operations are
  supported, including removal of unused vertices. Return to Object Mode and use
  **Separate Disconnected Islands** to split surviving islands into separate
  Outliner objects. A whole component object may also be deleted in Object Mode.
  Each existing collision joint must retain at least one edge. Export rebuilds
  the connections and ranges.

Do not use native Extrude, Subdivide, Merge, Dissolve, Duplicate, or Make Edge/Face
for collision topology yet. They can destroy or duplicate collision metadata;
validation rejects ambiguous results and asks you to undo them. Dedicated tools
support Blender Undo/Redo, and new identities persist through `.blend` save/load.
New edge and vertex identities come from saved scene-wide counters, so deleting a
new element does not make a later component reuse its identity. For older
`.blend` files using the former single collision mesh, run **Separate
Disconnected Islands** before editing; validation/export also performs the same
in-place conversion. Pending geometry and the aggregate dirty baseline are
preserved.
Edges carrying unknown source flag bits cannot be split or extended because
those bits cannot safely be assigned to new collision records. Read-only stages
remain read-only. Branches, inconsistent directions, and coincident endpoints
are rejected rather than guessed.

## Save and resume

Save the `.blend` normally. The add-on creates a persistent session under Blender's
user data directory, in `melee_map_editor/sessions/<session-id>/`, and stores its
absolute path in the scene. **Keep that directory with the `.blend`**: it contains
the source DAT and immutable model/collision payloads needed for export. Packing
Blender resources does not embed the session. Session relocation and cleanup UI
are not implemented yet; sessions are intentionally not auto-deleted.

Exports generate temporary edit JSON and model-addition RGBA payloads, invoke the
CLI, then remove those files even on failure. The saved Blender scene holds the
edited state. Existing external edit files cause an error to avoid mixing two
editing sessions. Do not edit the session's baseline files.

## Preview limitations

The importer implements the HSD joint pose, inherited scale compensation,
envelope deformation, and transform-animation playback. Positions use
`(X, -Z, Y)` exactly once at the boundary.
The source hierarchy is retained, including separate JOBJ/DOBJ/POBJ identities.
Affine parent matrices retain shear without Blender's local TRS decomposition.
Rigid and envelope-deformed meshes retain decoded normals. GX triangles whose winding opposes those
normals are reversed for Blender display and restored to their original order
on export. The material preview reads an exact normal attribute because Blender's
native custom normals clamp values that cross a polygon's hemisphere. Animated
pixel-engine values, shape and light animation, billboard
behavior, constraints, instanced drawing, and
stage-code pose updates are not simulated. Source-hidden
geometry is visible for inspection. Supported rigid targets allow
geometry export; unsupported preview meshes remain read-only.
Quaternion joints and shared-joint mesh previews currently fail import with an
explicit error, rather than showing a guessed pose.

## Automated checks

With locally obtained fixtures in `example_assets`, or `MELEEMAP_CORPUS`:

```sh
dotnet test MeleeMap.sln
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/smoke.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/topology.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/models.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/model_additions.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/model_addition_boat.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/model_addition_grgd.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/jobjs.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/envelopes.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/animations.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/material_animations.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/camera.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/atmosphere.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/dynamic_collision.py
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/collision_components.py
```

The smoke script checks registration, coordinate and inherited-scale rules,
real stage import, protected identities, no-edit byte equality, vertex/property
edits through the CLI, Edit Mode serialization, `.blend` save/load, overwrite confirmation defaults, validated output
replacement, failed-export cleanup, and dynamic collision editing. It uses
temporary sessions and outputs. The topology script covers the new tools, native
deletion, Undo/Redo, stable identities, and DAT export/reload. GPU arrow appearance
still needs interactive checking; the user has reported the collision workflow
working in game. The addition scripts cover synthetic asymmetric textures,
multiple materials, negative scale, save/reopen, deterministic repeated export,
the local Salvage Boat OBJ/MTL/PNG fixture, and programmatic discovery/export on
`GrGd.dat`. Added textured models still need in-game acceptance.

The dynamic-collision script verifies that Mute City's five unambiguous
serialized collision attachments resolve to their JOBJ pose bones, respond to a
pose change, export edited local geometry, preserve all attachment records and
the dynamic range after reimport, and preserve a no-edit DAT byte-for-byte.
The component script verifies joint-local connectivity partitioning, hidden
component export, same-joint merging, cross-joint rejection without mutation,
native-delete repartitioning, whole-component deletion, and legacy saved-scene
conversion. Outliner hide/isolate and overlay alignment still require an
interactive Blender check because headless tests cannot render the viewport.

## Ceiling assignment correction

Earlier versions changed the category without changing endpoint order. A floor
marked Ceiling could therefore become non-colliding from both above and below.
Assign Type now sets the engine-required direction, and edited exports reject
mismatches with `COLLISION_FACING`. After updating, select previously converted
edges and assign Ceiling again to repair them; their positions and property flags
are retained. A ceiling needs horizontal extent; a wall needs vertical extent.
Converting only part of a connected boundary can still produce incompatible
adjacency, which must be resolved before export. Regression tests cover ceiling
conversion and output reload, but the fix still needs an in-game retest.

## Vertex colors

Reload the add-on and re-import the DAT to load GX vertex colors. Available
channels appear under Mesh Data > Color Attributes as **Stage Color 0** and
**Stage Color 1**. Both retain RGBA values on face corners, preserving seams.
The first available channel multiplies the base material/texture color. The second channel
is stored for inspection. Material, texture and vertex alpha are previewed using
the source settings; unsupported custom game TEV routing remains approximate.

Vertex painting and alpha edits on static editable rigid meshes are exported
to DAT, including independent colors at face corners. The **Melee Material**
panel identifies materials that use vertex color and points to Blender's native
Vertex Paint workflow; it does not apply an object-wide color. Native
color-attribute editing and vertex painting export. Color edits currently require unchanged topology, UVs and material
assignment; animated position-only meshes reject them. Painting read-only
geometry remains preview-only. Source transparency settings are retained.

Material previews are independent of export-material eligibility. Static meshes
using vertex-color materials (for example GrSt Group 003 / JOBJ 004 / DOBJ 013)
and read-only meshes also receive their supported base textures. These previews
do not make the source materials available for assignment to replacement
geometry. Highlight, shadow, toon, gradation, and other unsupported
texture-coordinate generators, along with unsupported TEV comparisons and
cross-stage inputs, retain the
existing preview limitations.

## Alpha previews

Enable **Dithered Transparency** under **Melee Map → Viewport** to use dithering
for all stage transparency previews, including additive effects. This can speed
up the viewport but may introduce visible noise or change overlapping effects.
Disable it to restore normal blending. The setting defaults off, is saved with
the Blender scene, and does not affect DAT export.

Transparency uses the source material and pixel-processing settings:

- Texture alpha, including I4/I8 intensity textures where black has zero alpha.
- Material alpha, vertex alpha and combined material/vertex alpha.
- Base texture alpha mask, blend, multiply, replace, pass, add and subtract.
- Alpha-test cutouts and standard transparency.
- Additive blending, where black contributes no light and leaves the background visible.

Opaque materials stay opaque even if their image contains black pixels or unused
alpha. Custom TEV routing, uncommon multi-layer alpha routing, and framebuffer
blending remain approximate; unsupported source settings are recorded in the
material’s `mme_preview_warning` property. Material/texture animation is still not evaluated.
Preview nodes do not export material edits. Use the supported Melee Material
properties below for exportable changes. Rendered regression tests cover alpha sources, operations, cutouts,
additive effects and opaque black on Blender 4.5 and 5.2.

Stage imports set scene color management to Standard, with Look None, Exposure 0
and Gamma 1, to preserve the saturated colors used by Melee.

## Editing material properties

After reloading the add-on and importing a fresh DAT session, select a supported
static model and open **Material Properties > Melee Material**. The panel
supports **Use Vertex Colors**, **Alpha Source**, **Diffuse Color**, **Material Alpha**,
**Ambient Color**, **Specular Color**, **Shininess**, per-layer **Texture Blend**, and
**Transparency**. Transparency can be Opaque, Alpha
Blend, Additive, or Subtractive; changing it updates both the MOBJ render queue
flag and the PE blend state. For source vertex-color materials, clearing **Use Vertex
Colors** switches the exported MOBJ to material color and material alpha, then
exposes the diffuse and alpha controls. The painted color attribute remains on
the mesh and can be enabled again. Changes
update the preview immediately and are included in normal validation/export.
Unavailable controls are disabled according to the source material's settings.
Multi-texture materials show each layer's diffuse/specular/ambient/extension
role, UV or reflection coordinate source, and color operation. Texture Blend is
enabled independently for layers using a color or alpha BLEND operation. Export
copies and relinks the complete source TObj chain, preserving its images,
transforms, coordinate selectors, roles, and unedited flags.

**Alpha Source** is independent of RGB source and supports the four HSD modes:
Compatibility, Material Alpha, Vertex Alpha, and Material × Vertex. Vertex
alpha is painted in Blender's `Stage Color 0` attribute with Vertex Paint.

The **Render Flags** box exposes the documented MOBJ flags that can be changed
without inventing or removing referenced records: diffuse lighting, specular,
toon shading, depth offset, depth-test-always, depth writing, shadow, all
textures, effect, and user. Depth settings are mirrored into an existing PE
descriptor when necessary. Texture-enable bits remain derived from the actual
TOBJ chain, source selectors are controlled by **Use Vertex Colors**, and
undocumented bits are preserved.

Diffuse Lighting and Specular Highlight also change the Blender preview shader.
Unlit Melee materials keep the emission preview used for exact base colors.
Lit materials also use emission, fed by explicit normal/light calculations that
follow HSD's diffuse and Blinn-Phong specular equations. Specular uses the DAT
material's RGB and shininess and never reflects Blender's HDRI or world
environment. The ambient LOBJ contribution is multiplied by the material's
ambient RGB before directional and positional diffuse light is added. Fresh
imports use the selected stage LOBJ set's ambient, infinite,
point, and spot lights, including diffuse/specular flags and attenuation. The
main HSD texture-lightmap passes and supported custom TEV ADD/SUB color stages are
preserved; TEV comparisons and complete GX material-channel routing remain approximate.

Preview-only materials that remain ineligible show an object-specific reason in
the panel, such as material animation or an unsupported texture/render layout,
instead of describing the entire session as read-only.

## Stage lights

Fresh sessions import every model-group LOBJ set and the `map_plit` player-light
set under **Melee Stage > Lights**. The set driving the material preview is named
**Preview Light Set**; other sets are imported but hidden in the viewport. Each
LOBJ retains its type, flags, color, diffuse/specular use, position/interest,
attenuation parameters, source offset, and whether it has animation data.
Infinite, point, and spot LOBJs use Blender light objects for inspection;
ambient LOBJs use empties because Blender has no equivalent ambient-light object.

The preview evaluates source ambient and directional/positional light colors,
spot and distance attenuation, and each material's shininess directly in its
emission graph. The objects in **Preview Light Set** drive that graph: rotate an
infinite light, move a point light, move or rotate a spot light, or change a
Blender light's color and energy to update the material preview. Ambient empties use the
color-picker property `mme_light_color` plus `mme_light_intensity` and
`mme_light_enabled`; every imported light has `mme_light_enabled`. Native Blender illumination and world reflections
are not mixed into the Melee shader.

For infinite lights, the imported LOBJ vector and the Blender Sun's local `-Z`
axis indicate the direction the rays travel. The diffuse/specular calculation
uses the opposite vector from the surface toward the light source.

These controls export to the static LOBJ descriptors. **Enabled** updates the
hidden flag. Blender color multiplied by energy is encoded into the LOBJ RGB
bytes; the product must remain in the representable 0–1 range. Infinite rotation,
point position, and spot position/rotation update cloned WOBJ records. Infinite
vector length and spot interest distance are retained from the source. Ambient
color and intensity export through the same RGB conversion.
Hidden non-preview light sets can also be unhidden and edited for export, though
only **Preview Light Set** drives the Blender material preview. When several
model groups contain the same baseline light descriptors, edits made to the
preview representative are exported to every equivalent copy. This covers
stages whose code selects a different duplicate at runtime, while distinct
light sets remain independently editable.

LOBJ color alpha, type, diffuse/specular flags, attenuation, spot size/functions,
and animation curves remain unchanged. Existing animation can override edited
static values in game. The preview uses descriptor defaults without evaluating
animation. Melee chooses among model-group
sets through stage code, which is outside the DAT; when several distinct sets
exist, extraction records a warning and prefers the only animated set when that
choice is unambiguous.

Geometry, UV, collision, material, and static light edits can be exported in the
same operation. Editing a Blender material affects supported models assigned to that material. The DAT
writer copies edited material records before changing bindings, so other source
meshes sharing the original records retain their appearance. Blender copies
with the same source identity but conflicting property values are rejected.

Animated materials, unsupported material layouts and read-only model targets
remain protected for this slice. Native shader-node and image
edits still affect previews only. Vertex-color editing is described above. Property export is covered by automated
roundtrip tests; in-game verification is still needed.
