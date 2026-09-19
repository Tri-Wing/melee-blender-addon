# Blender collision editing preview

Development target: **Blender 4.5.0 on Linux**, with .NET 8 for the development
backend. This milestone imports grey models and edits existing static collision
vertices, properties, and topology, plus all structurally supported rigid models.
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
```

The launcher uses `blender` from your PATH (currently the Snap installation on
this machine). Set `BLENDER_BIN=/path/to/blender` to choose another executable.
It works from any working directory and forwards arguments to Blender; put any
`.blend` path and normal Blender options after the launcher name. Avoid Blender's
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
run the loader again. Large scene-schema changes may still require re-importing.

Backend C# changes still need `dotnet build MeleeMap.sln`; the CLI runs as a new
process on each operation, so it does not need a Blender restart. To test the
reload workflow headlessly:

```sh
/path/to/blender-4.5.0/blender --background --factory-startup \
  --python-exit-code 1 --python tests/blender/reload.py
```

## Edit a stage

1. Open the 3D View sidebar (`N`), then **Melee Map → Import Stage DAT**.
2. Choose `GrNLa.dat`. Its ten groups appear under **Melee Stage → Models**,
   containing 93 grey meshes and the protected source hierarchy. Toggle all
   models with **Show / Hide Model Groups**, or individual groups in the Outliner.
3. Choose **Enter Collision Editing**. Front view (`Numpad 1`) looks onto the
   gameplay plane. Move vertices along Blender X and Z; keep Blender Y at zero.
   Use the dedicated topology tools below to add or reconnect collision. Native
   vertex/edge deletion is supported. Object transforms and modifiers are rejected.
4. Select edges to assign a collision type or named surface type, or toggle
   drop-through and ledge-grab bits. Assigning a type also orients its endpoints
   for the game: floors left-to-right, ceilings right-to-left, right walls down,
   and left walls up. Solid floors are dark green and drop-through floors bright green.
   Ceilings are red, right walls blue, left walls amber, and dynamic collision
   purple. White crosses mark lines with ledge-grab set. Blender's Overlays toggle controls this
   visualization. Unknown source flag bits remain protected.
5. Choose **Validate Stage**, then **Export Stage DAT**. The filename defaults
   to the imported DAT name.
   Both actions check the source/session hashes, protected scene content,
   collision compilation, and output reload. Blender asks for confirmation when
   replacing an existing file. Replacement happens only after validation succeeds;
   failed exports leave the previous file intact.
6. Use **Open Export Directory** to find the result for manual insertion/testing.

Vertex changes and property assignments can be exported while still in Edit
Mode. An untouched scene exports a byte-identical copy of the input DAT. The
sidebar tracks collision changes; validation/export also detects unsupported
model or hierarchy changes and explains which class of edit must be undone.

Use a new Blender scene for a second import. Sources with dynamic collision,
attachments, or collision warnings are imported read-only for collision; a
no-edit export preserves them. `GrGb.dat` is covered by the read-only smoke test.
Other stages may contain unsupported preview transforms/bindings and are not yet
certified. Missing extracted meshes are reported in the sidebar.

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
full geometry editing and 45 support vertex movement with animated materials. Select an object named **Editable Model** in the
Outliner or viewport, then use **Edit Selected Model**. Read-only meshes have a
**Read-only Model** prefix, and selecting one shows the reason in the sidebar.
Meshes named **Vertex Editable Model** have animated materials: move vertices
using Blender's native tools, but keep topology, UVs and material assignments
unchanged. Their original normals, materials, textures and animation data are
preserved on export. Blender previews their base material colors and supported
base textures without playing material or texture animation. Additional texture
layers and game shader effects are not reproduced. The sidebar displays
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
2. **Import the DAT into a new Blender scene.** Existing scenes retain their old
   protection rules. To carry over collision work, export your current stage
   first, then import that edited DAT. Keep the old `.blend` and session as needed.
3. Select an **Editable Model**, then click **Edit Selected Model** to enter
   Edit Mode. The sidebar displays the selected model's edit status.
4. Move vertices. For fully editable models, use native Blender mesh tools to add/delete faces and replace
   geometry **inside this object**. Unlike collision, render geometry can be
   edited freely in all three dimensions. Quads/ngons are triangulated on export.
   Do not delete the object, change its parenting or object transforms, or add
   other identity-bearing objects. Apply desired shape changes in Edit Mode.
5. Validate and export normally. Model and collision changes can be exported
   together. Editing model geometry does **not** automatically change collision.

To add a separate shape, create and shape it normally in Object Mode, select it,
then select **Editable Model** last so that the supported model is active. Use
**Ctrl+J** to join. Blender converts the added shape into the target's local
coordinates and keeps its placement. Keep the supported model's own transform
and parenting unchanged. Material slots brought in by Join are accepted; all
joined geometry can use a supported stage material or the grey fallback. Separate objects are
not exported until joined into the supported model.

Scenes already imported with an editable model can use this Join fix after
reloading the add-on; no re-import is required. Older collision-only scenes still
need the new-scene import described above to enable model editing.

**Moving vertices without changing topology preserves the source appearance in
game:** material, textures, UVs, vertex colors, normals, transparency, and culling
remain intact. This works with existing editable scenes after reloading the
add-on and rebuilding/updating the backend; no re-import is needed. Source normals
are retained exactly rather than recalculated, so large shape changes may need
future normal-editing support. Supported stage materials now have texture previews.

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

Object transforms, group/JOBJ/DOBJ/POBJ identities, and unsupported meshes remain
protected. Eligible meshes must be rigid and unbound, with one POBJ per DOBJ,
no custom class, no material/texture animation on that DOBJ, and no group shape
animation. Shared/interior descriptors, instancing, billboard transforms, and
quaternion joints remain unsupported. Static textured and translucent source
materials are preserved for vertex-only edits. Topology changes use a supported
assigned stage material or opaque grey. Source-hidden
models are editable, but their original visibility and joint animation remain
intact: editing them does not force them to become visible in game.

**Existing saved scenes retain their old permissions.** Export any work you want
to keep, reload the add-on, and import that DAT into a fresh scene to enable the
full target list. No automatic rebasing of protected geometry is performed.

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

This is an **unlit texture-placement preview** for the supported material catalog.
Full game lighting, TEV color/alpha effects, mip filtering, animated materials,
and unsupported material types are not reproduced. Unsupported/invalid images
fall back to a solid material with a preview warning. Texture painting and shader
node changes do not author game assets or affect DAT export.

## Inspect a selected edge

In Collision Edit Mode, select an edge and expand **Selected Edge Metadata**
under Melee Map. It reads the actual selected edge, independently of the type and
surface dropdowns used for assignment. It shows the current type, named surface
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

- **Split Edge:** select one or more edges. Each is split at its midpoint,
  preserving direction, type, material, flags, and joint. Move the new vertices
  with Blender's normal move tool.
- **Extend Collision:** select exactly one open endpoint (one incident edge).
  Enter an X/Z offset in game units. The new edge inherits its neighbor's
  properties and continues its direction. The new vertex is selected so you can
  reposition it with `G`, constrained to X or Z as needed.
- **Connect Vertices:** select two open endpoints or isolated surviving vertices.
  Choose a collision type and named surface in the dialog. Direction and joint are inferred from
  connected edges. Two isolated vertices use the chosen joint (numbered from 1)
  and a stable endpoint order; use Reverse if needed. New connections start with
  drop-through and ledge flags off; assign them afterward as appropriate.
- **Reverse Direction:** select edges to exchange their start/end directions.
  Direction also determines the collidable side; it must agree with the type.
  Reassigning a type automatically repairs direction. Reversing edges without
  updating their types can fail the facing check; inconsistent directions within
  a connected chain also fail validation.
- **Native delete:** Blender's Delete Edges/Delete Vertices operations are
  supported, including removal of unused vertices. Each existing collision joint
  must retain at least one edge. Export rebuilds the connections and ranges.

Do not use native Extrude, Subdivide, Merge, Dissolve, Duplicate, or Make Edge/Face
for collision topology yet. They can destroy or duplicate collision metadata;
validation rejects ambiguous results and asks you to undo them. Dedicated tools
support Blender Undo/Redo, and new identities persist through `.blend` save/load.
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

Exports generate temporary `edits/collision.json` input, invoke the CLI, then
remove that edit file even on failure. The saved Blender scene holds the edited
state. Existing external edit files cause an error to avoid mixing two editing
sessions. Do not edit the session's baseline files.

## Preview limitations

The importer implements the static HSD joint pose, inherited scale compensation,
and envelope deformation. Positions use `(X, -Z, Y)` exactly once at the boundary.
The source hierarchy is retained, including separate JOBJ/DOBJ/POBJ identities.
Affine parent matrices retain shear without Blender's local TRS decomposition.
Rigid meshes retain decoded normals; deformed preview meshes use generated
normals. Textures, source materials, animation, billboard behavior, constraints,
instanced drawing, and stage-code pose updates are not simulated. Source-hidden
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
```

The smoke script checks registration, coordinate and inherited-scale rules,
real stage import, protected identities, no-edit byte equality, vertex/property
edits through the CLI, Edit Mode serialization, `.blend` save/load, overwrite confirmation defaults, validated output
replacement, failed-export cleanup, and dynamic collision restrictions. It uses
temporary sessions and outputs. The topology script covers the new tools, native
deletion, Undo/Redo, stable identities, and DAT export/reload. GPU arrow appearance
still needs interactive checking; the user has reported the collision workflow
working in game. New model exports still need in-game acceptance.

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

## Vertex-color previews

Reload the add-on and re-import the DAT to load GX vertex colors. Available
channels appear under Mesh Data > Color Attributes as **Stage Color 0** and
**Stage Color 1**. Both retain RGBA values on face corners, preserving seams.
The first available channel multiplies the base material/texture color. The second channel
is stored for inspection. Material, texture and vertex alpha are previewed using
the source settings; custom game TEV channel routing remains approximate.

Vertex painting currently changes only the Blender preview. Vertex-only DAT
exports retain the original colors; topology replacements still do not export
vertex colors. This also applies to vertex colors on read-only geometry.

Material previews are independent of export-material eligibility. Static meshes
using vertex-color materials (for example GrSt Group 003 / JOBJ 004 / DOBJ 013)
and read-only meshes also receive their supported base textures. These previews
do not make the source materials available for assignment to replacement
geometry. Unsupported texture coordinates and additional texture layers retain
the existing preview limitations.

## Alpha previews

Transparency uses the source material and pixel-processing settings:

- Texture alpha, including I4/I8 intensity textures where black has zero alpha.
- Material alpha, vertex alpha and combined material/vertex alpha.
- Base texture alpha mask, blend, multiply, replace, pass, add and subtract.
- Alpha-test cutouts and standard transparency.
- Additive blending, where black contributes no light and leaves the background visible.

Opaque materials stay opaque even if their image contains black pixels or unused
alpha. Custom TEV routing, multiple texture layers and uncommon framebuffer
blending remain approximate; unsupported source settings are recorded in the
material’s `mme_preview_warning` property. Material/texture animation is still not evaluated.
These previews do not change DAT export behavior or enable alpha/material editing
in exports. Rendered regression tests cover alpha sources, operations, cutouts,
additive effects and opaque black on Blender 4.5 and 5.2.
