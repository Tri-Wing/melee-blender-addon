# Melee Map Editor Blender Add-on Implementation Plan

Status: Headless foundation implemented; see [format audit and current status](format-audit.md). Collision consistency checks, full GrNLa geometry extraction, and static collision apply are implemented. Blender 4.5.0 import and static collision vertex/property editing are implemented; see [the add-on guide](blender-addon.md). Dedicated collision topology tools and native deletion are now implemented. The user has reported all existing collision features working. Milestone 4 now supports geometry replacement for one eligible rigid mesh with an opaque grey material; automated export/reload tests pass, and model in-game acceptance remains pending.

## 1. Summary

Build a Linux-first Blender add-on for modifying existing Super Smash Bros. Melee stage `.dat` files. The add-on will use Blender for scene editing and a bundled, headless C# tool for parsing, validating, and writing the HSD archive format.

The first proof of concept (POC) will target `example_assets/GrNLa.dat` and support:

- Importing the stage's model groups as untextured grey geometry.
- Editing or replacing geometry within existing model groups.
- Importing and editing static gameplay collision.
- Exporting a new `.dat` that the user can insert into the game and test manually.
- Preserving every not-yet-editable or unedited part of the source file without presenting it in Blender.

The POC will not yet edit textures, materials, animations, lights, particles, stage parameters, item data, or stage-specific `yakumono_param` data. Those structures must survive an export unchanged until their editors are implemented.

All capability exclusions in this document are sequencing decisions for the POC, not permanent product non-goals. The product architecture will grow beyond maps and is intended to support editing every game-asset family, adding asset-specific backends where the HSD archive/GX foundation does not apply. Later capabilities will receive their own implementation plans rather than being designed in detail here.

The implementation will fork HSDLib/HSDRaw and correct its stage schemas using the Melee decompilation as the behavioral source of truth.

## 2. Product decisions

| Topic | Decision |
| --- | --- |
| Audience | Both experienced Melee modders and Blender users who do not know the DAT format. |
| Host application | Blender 4.5.0, pinned for development and headless smoke tests. |
| Initial platform | Linux. Keep paths, process execution, and packaging portable so Windows can be added later. |
| POC input | Existing stage `.dat` only. Creating complete archives from scratch is deferred until after editing existing files is reliable. |
| POC editing | Static collision and untextured grey model geometry. |
| Not-yet-editable data | Preserve silently. Do not expose a raw-data editor in the initial add-on. |
| Binary backend | Self-contained .NET command-line application invoked by the Python add-on. |
| HSDLib strategy | Maintain a project fork and modify HSDRaw as needed. |
| Game testing | Manual file insertion and manual testing in Dolphin or on hardware. |
| Export safety | Default to the imported filename, confirm overwrites in Blender, and validate before replacing an existing output. Avoid elaborate backup/versioning systems. |
| Post-POC assets | Every game-asset family is a planned later capability. This plan details maps only, while shared archive and GX code must remain asset-agnostic. |

## 3. Goals and capability sequencing

### 3.1 Goals

1. Make the simplest useful stage-editing loop short and understandable:
   `Open DAT -> edit in Blender -> Export DAT -> test in game`.
2. Match the game's interpretation of stage data, even when it differs from existing community names or assumptions.
3. Preserve unedited data, including roots and structures the editor does not understand.
4. Keep model-group and JOBJ identities stable because stage code uses numeric indices.
5. Provide clear validation errors for invalid collision or edits not yet available in the current milestone.
6. Keep Blender-specific code separate from DAT parsing and generation.
7. Use the full retail-stage corpus for regression coverage from the beginning.

### 3.2 Capabilities deferred until after the POC

The following capabilities are deliberately postponed to keep the first end-to-end export small. None is rejected or intended to remain permanently unavailable:

- Creating a stage DAT from an empty file.
- Adding, deleting, or reordering `map_head` model groups.
- Editing the JOBJ hierarchy.
- Textures, GX/TEV materials, palette data, or faithful material preview.
- Joint, material, or shape animation editing.
- Dynamic collision editing or changing collision-to-JOBJ attachments.
- Cameras, lights, fog, splines, particles, items, audio, or stage parameters.
- Editing stage-specific roots such as `yakumono_param`.
- Installing files into an ISO, SD card, or Dolphin virtual filesystem.
- Running or controlling Dolphin.
- Windows packaging during the POC.

## 4. Format model and constraints

A stage DAT is a relocated graph of structures with named public roots. It is not a single scene or model. `map_head`, `coll_data`, `grGroundParam`, particles, items, lights, and stage-specific parameters are retrieved independently by the engine.

The editor must preserve this separation:

- `map_head` owns indexed model groups and related scene data.
- A model group owns an HSD JOBJ hierarchy, render objects, animation arrays, camera/light/fog references, collision attachments, animation flags, and other indexed metadata.
- `coll_data` owns the global 2D gameplay collision graph and its collision joints.
- Stage C code may address model groups and JOBJs by numeric index.
- Animation trees and collision attachments rely on the original JOBJ traversal order.
- Public roots not in the POC remain opaque but reachable graph data.

Consequences for the POC:

- Model-group indices are visible but not editable.
- Imported Blender objects carry stable source identifiers.
- Deleting a protected collection or identity-bearing object produces an export error rather than silently renumbering the stage.
- Unchanged model groups retain their original HSD structures, not regenerated approximations.
- A modified render object may be rebuilt, but its owning model group and JOBJ identity remain stable.

## 5. Architecture

```text
Blender add-on (Python)
  - operators and panels
  - scene collections and mesh attributes
  - coordinate conversion
  - dirty tracking and session files
             |
             | versioned session protocol
             v
MeleeMap CLI (.NET)
  - inspect / extract / apply / validate
             |
             v
MeleeMap.Core + forked HSDRaw
  - DAT graph
  - engine-derived stage schemas
  - GX geometry decoding/encoding
  - collision compiler
  - graph-preserving writer
```

### 5.1 Proposed repository layout

```text
HSDLib/                         # fork, retaining its own history initially
  HSDRaw/                       # patched archive and HSD type library
src/
  MeleeMap.Core/                # stage domain model and operations
  MeleeMap.Cli/                 # headless command-line interface
blender_addon/
  melee_map_editor/
    __init__.py
    operators/
    panels/
    scene/
    protocol/
tests/
  MeleeMap.Core.Tests/
  MeleeMap.Cli.Tests/
  blender/                      # headless Blender smoke tests
docs/
  blender-addon-plan.md
```

The top-level C# projects should reference the forked `HSDLib/HSDRaw` project. UI-dependent HSDRawViewer code should not be referenced by `MeleeMap.Core`.

### 5.2 Component responsibilities

`HSDRaw` is responsible for:

- Reading and writing the archive header, relocation table, public roots, and object graph.
- Low-level HSD structures such as JOBJ, DOBJ, MOBJ, POBJ, and GX buffers.
- Preserving raw bytes and references for structures not interpreted by the editor.

`MeleeMap.Core` is responsible for:

- Corrected, engine-derived Melee stage schemas.
- Converting HSD model data to and from a neutral mesh representation.
- Loading and compiling collision graphs.
- Stable object identities within an editing session.
- Validation and semantic comparison.
- Applying only the user's edits to the original archive graph.

`MeleeMap.Cli` is responsible for:

- A stable process boundary for Blender.
- Machine-readable results and errors.
- Session directory creation and consumption.
- Never displaying interactive UI.

The Blender add-on is responsible for:

- User interaction and scene organization.
- Converting session data into Blender objects and back.
- Tracking which supported objects were edited.
- Preventing or reporting destructive operations that the POC cannot serialize.

## 6. HSDRaw fork work

Before implementing Blender export, make the following corrections in the fork and cover each with fixture tests:

1. Correct `SBM_Map_GOBJ` offset `0x28` to an animation-flag buffer.
2. Correct offset `0x2C` to the engine-observed `s16` JOBJ-index array instead of a second six-byte collision-link array.
3. Preserve the `MapCollData` field at offset `0x2C` and use a `0x30`-byte structure size.
4. Rename collision fields to match the decompiled `MapLine` layout while documenting compatibility aliases if needed.
5. Audit `GroundParam` types against the decomp. Do not promote guessed names to stable API without supporting usage in game code.
6. Add an explicit preservation save profile:
   - no trimming;
   - no unreachable-structure removal;
   - no buffer deduplication;
   - preserve source structure order where possible;
   - retain all public roots and root names.
7. Separate any reusable model conversion code from WinForms dialogs and HSDRawViewer state.

The preservation profile does not need byte-identical output because changed structure sizes require relocation. It must instead preserve graph semantics and the raw contents of untouched structures.

## 7. CLI and session protocol

### 7.1 Initial commands

```text
meleemap inspect <input.dat> --json
meleemap extract <input.dat> --session <directory>
meleemap apply <session-directory> --output <output.dat>
meleemap validate <input.dat> --json
meleemap roundtrip <input.dat> --output <output.dat> --compare
```

All commands return a non-zero exit code on failure. Normal output intended for Blender is JSON. Human-readable diagnostics go to standard error.

### 7.2 Session directory

The protocol is versioned independently from the add-on:

```text
session/
  stage.json
  source.dat                  # copy or immutable path reference for the POC
  models/
    group-000/
      group.json
      mesh-*.json
  collision/
    collision.json
  edits/
    models.json
    collision.json
```

`stage.json` includes:

- Protocol version.
- Source DAT SHA-256.
- Source filename and DAT version.
- Public-root inventory.
- Model-group count and stable IDs.
- Available/deferred capability flags.
- Coordinate-system declaration.

Every extracted graph object receives an opaque, persistent session ID. Traversal indices, parent IDs, sibling order, model-group index, DOBJ/POBJ position, and source byte offset are stored separately as source locators and diagnostics; they are not the object's permanent identity. POC exports require the protected hierarchy locators to remain unchanged, but this identity design allows later add/delete/reparent/reorder operations without replacing the IDs of unaffected objects. New objects will receive new IDs.

For the POC, model payloads may be JSON containing positions, normals, and triangle indices. This favors implementation clarity over compactness. The protocol should allow a later binary payload without changing Blender scene semantics.

### 7.3 Applying edits

`apply` must:

1. Verify the source DAT hash expected by the session.
2. Reopen the source DAT into a fresh graph.
3. Apply collision edits if present.
4. Rebuild only model objects listed as dirty.
5. Preserve all other structures and roots.
6. Validate the modified graph.
7. Write the requested output path using the preservation profile.
8. Reopen the output and run structural validation again.

Export defaults to the imported filename. Blender confirms overwriting existing files; CLI `apply` validates a temporary output before replacing the destination. The immutable session source snapshot is protected, but the original imported DAT outside the session can be overwritten.

## 8. Blender scene design

### 8.1 Top-level collections

```text
Melee Stage
  Models
    Group 000 [locked identity]
    Group 001 [locked identity]
    ...
  Collision
  Lights
    Preview Light Set [static LOBJ preview and export controls]
    Other Light Sets [hidden static LOBJ export controls]
  Reference
```

The initial POC did not import deferred systems. The current implementation now
imports static LOBJ descriptor representations under Lights; particles and other
stage-specific systems still have no placeholders.

Each generated object has custom properties including:

- `mme_role`
- `mme_session_id`
- `mme_group_index`
- `mme_jobj_index`
- `mme_dobj_index`
- `mme_pobj_index`
- `mme_source_hash`
- `mme_dirty`

Custom properties are implementation metadata. User-facing panels should show friendly roles and indices without requiring users to edit these values.

### 8.2 Coordinate system

Use one reversible transform at the protocol boundary:

```text
Game (X, Y, Z) -> Blender (X, -Z, Y)
```

This maps the game's X/Y gameplay plane to Blender's X/Z plane, makes game Y visually up, and preserves handedness. Store the declared transform in `stage.json` and test round trips numerically.

Do not bake ad hoc rotations into individual meshes or armatures.

### 8.3 Add-on panels and operators

Initial sidebar panel: `Melee Map`.

Operators:

- `Import Stage DAT`
- `Show/Hide Model Groups`
- `Enter Collision Editing`
- `Assign Collision Type`
- `Assign Collision Material`
- `Toggle Drop-through`
- `Toggle Ledge-grab`
- `Validate Stage`
- `Export Stage DAT`
- `Open Export Directory`

The panel shows:

- Source filename and hash status.
- Number of model groups and collision lines.
- Dirty model/collision summary.
- Deferred-capability notices, such as “dynamic collision is present and read-only in this version.”
- Last validation/export result.

## 9. Collision editing

### 9.1 Blender representation

Represent collision with an edge-only Blender mesh in the X/Z plane. Use mesh attributes on the EDGE domain for:

- Original line ID.
- Collision group/joint ID.
- Direction/type: floor, ceiling, right wall, left wall, or dynamic.
- Material ID.
- Property flags: drop-through, ledge-grab, and currently unknown bits.
- Disabled state.
- Original alternate-link IDs for preservation and diagnostics.

Use viewport colors for collision type and overlays/icons for drop-through and ledge-grab. Never infer all gameplay properties solely from geometry or face normals.

### 9.2 Static collision compilation

When collision is dirty, the compiler will:

1. Transform edited vertices back to game X/Y coordinates.
2. Reject non-finite values and edges with coincident endpoints.
3. Group edges by collision joint/group.
4. Deduplicate vertices within a small documented epsilon.
5. Order lines into the engine's floor, ceiling, right-wall, left-wall, and dynamic ranges.
6. Rebuild same-group previous/next links from shared endpoints.
7. Preserve explicit alternate-group links when their endpoints and referenced edges still exist.
8. Recompute vertex ranges and group bounds.
9. Enforce signed 16-bit index/count limits.

If an edited endpoint has more than one possible continuation, export fails with the ambiguous vertex and involved Blender edge names. A later custom link editor can resolve complex junctions; the POC should not guess.

Preserve an original group bound if it still encloses the edited group. Otherwise expand the geometric bound by the same margin established from retail data or, until that is characterized, a documented default of 8 game units.

### 9.3 Dynamic collision policy

For the first POC:

- Import dynamic lines for visualization.
- Mark them read-only in Blender.
- Preserve their original records and model attachments when collision is otherwise unchanged.
- Refuse collision export if a dynamic line, its collision group, or its referenced attachment was modified.

Supporting dynamic collision becomes the first collision follow-up after the `GrNLa` POC. It requires preserving or rebuilding the `GrJoint` records that bind collision joints to map-group JOBJs.

## 10. Model editing

### 10.1 Import

Decode each model group's JOBJ/DOBJ/POBJ hierarchy into grey Blender meshes. Preserve transforms and identity boundaries rather than flattening the whole stage into one mesh.

For the POC:

- Import position and, where available, normal data.
- Triangulate GX primitives for Blender display.
- Ignore textures, UVs, vertex colors, and TEV state in the visible material.
- Give every imported mesh a generated grey material.
- Keep all original HSD material and texture structures in the source graph for unchanged objects.

### 10.2 Supported edits

The POC supports:

- Moving vertices.
- Adding and deleting vertices and triangles inside an existing render object.
- Replacing the geometry of an existing DOBJ/POBJ target.
- Editing an existing object's local transform when that maps cleanly to its original JOBJ.

The following model edits are deferred until after the POC:

- Adding or removing model groups.
- Reparenting or adding JOBJs.
- Moving a mesh to a different model group/JOBJ.
- Editing skin weights or envelopes.
- Editing animation data.
- Editing material assignments.

### 10.3 Export

Any dirty render object is rebuilt as a minimal untextured HSD model object:

- Triangle primitives; triangle-strip optimization can be deferred.
- Position and normal attributes only where supported by the source/export path.
- A known-good, single grey MOBJ configuration.
- No texture references.
- Existing JOBJ identity and local transform retained.

Use HSDRaw's GX attribute and POBJ generation code where it is correct and testable. Extract or rewrite headless conversion logic rather than invoking HSDRawViewer dialogs.

An untouched render object keeps its original DOBJ/MOBJ/POBJ graph. A dirty object receives the POC grey representation; users should see this explicitly in the dirty summary before export.

The first implementation may support one designated replaceable render object in `GrNLa`. Generalize to multiple DOBJ/POBJ targets only after that path loads in game.

### 10.4 Post-POC JOBJ hierarchy editing

JOBJ hierarchy editing is an intended later capability, not a permanent restriction. It includes adding and deleting JOBJs, reparenting nodes, changing sibling order, moving render objects between JOBJs, and intentionally changing traversal order.

The POC must prepare for it by exporting the complete JOBJ identity graph and keeping persistent object IDs separate from traversal indices. A later hierarchy-editing protocol version will express graph operations against those IDs rather than asking Blender to rewrite numeric references directly.

Before hierarchy edits can be exported safely, `MeleeMap.Core` will add a reference-indexing and remapping pass covering at least:

- JOBJ child, sibling, parent, and instance relationships.
- DOBJ/POBJ ownership and ROBJ constraints.
- Envelope and skin-weight references.
- Joint, material, and shape animation trees.
- General-point JOBJ indices.
- Dynamic collision `GrJoint` attachments.
- Model-group JOBJ-index arrays such as the data currently stored at offset `0x2C`.
- Known stage-specific structures that address model groups or JOBJs.

Destructive graph operations will be enabled progressively as each reference family is understood and test-covered. Until then, the POC validator rejects hierarchy changes instead of producing silently misindexed files. Model-group identity remains a separate concern: editing a hierarchy inside a group does not inherently require renumbering the group itself.

## 11. Validation

Validation is practical rather than exhaustive. It should prevent files known to be structurally invalid without trying to simulate the entire game.

### 11.1 Archive validation

- Header size and section bounds are valid.
- Relocation entries reference valid pointer fields and targets.
- Public roots and names survive.
- Required stage roots `map_head`, `coll_data`, and `grGroundParam` exist.
- Every reachable reference is serializable.

### 11.2 Model validation

- Model-group count and indices are unchanged in the POC.
- Protected JOBJ identities and traversal counts remain stable.
- DOBJ/POBJ lists are well formed.
- Vertex attribute indices and display-list buffers are in range.
- Primitive counts and generated buffer sizes fit their encoded fields.
- All floats are finite.

### 11.3 Collision validation

- Vertex and line indices are in range.
- Category ranges are in range, non-overlapping, and agree with line flags.
- Every line belongs to exactly one usable category.
- Previous/next and alternate links are either `-1` or valid line indices.
- Links at simple vertices are reciprocal.
- Collision-joint line and vertex ranges are valid.
- Group bounds enclose their referenced vertices.
- No zero-length lines exist.
- Dynamic attachment indices remain valid when present.

Validation results use stable error codes so the Blender add-on can select or name the offending object when possible.

## 12. Testing strategy

### 12.1 Fixture corpus

`example_assets` currently contains 71 `.dat` files and five `.usd` variants. Treat the `.dat` files as a local integration corpus; do not copy or redistribute game assets as part of packaged releases.

Use these representative tiers:

| Fixture | Purpose |
| --- | --- |
| `GrNLa.dat` | Primary POC: 10 model groups, 16 static lines, one collision joint. |
| `GrGb.dat` | Small amount of dynamic collision. |
| `GrPs.dat` | Multiple model groups and 24 dynamic lines; related sub-archives are also present. |
| `GrKr.dat` | Heavily dynamic collision: all 98 lines are in the dynamic range. |
| `GrIm.dat` | Large collision-joint count and multiple general-point groups. |
| `GrBb.dat` | Large stress case: 41 model groups, hundreds of vertices/lines, many roots. |
| Target-test stages | Repeated three-group layouts and multiple general-point groups. |

### 12.2 Automated test levels

1. **Unit tests**
   - Big-endian fields and relocation behavior.
   - Corrected stage structure sizes and offsets.
   - Axis conversion round trips.
   - Collision ordering and link generation.
   - Mesh primitive decoding and POBJ generation.

2. **Corpus parse tests**
   - Open all 71 `.dat` files without exceptions.
   - Inventory public roots, model groups, collision counts, and feature flags.
   - Run structural validation without modifying the files.

3. **Semantic round-trip tests**
   - Load, preservation-save, and reload every fixture.
   - Compare root names, graph reference topology, typed counts, and raw payload hashes for untouched structures.
   - Do not require identical offsets or whole-file hashes.

4. **Edit tests**
   - Move one `GrNLa` collision vertex, export, reload, and assert the intended coordinate changed.
   - Add/remove a static line and verify rebuilt ranges and links.
   - Replace the selected grey model geometry and verify the generated GX structures reload.
   - Assert unrelated public roots and payloads are unchanged semantically.

5. **Headless Blender tests**
   - Enable the add-on in the pinned Blender version.
   - Import the generated session.
   - Verify expected collections, IDs, and edge attributes.
   - Export an unmodified session and a scripted simple edit.

6. **Manual game tests**
   - Insert the output as the appropriate stage file.
   - Confirm the match loads.
   - Walk across every edited collision segment.
   - Test ledges and drop-through platforms when used.
   - Confirm the grey model is visible and stable.
   - Allow the match to run long enough to expose obvious animation or memory issues.

## 13. Implementation milestones

Milestones are ordered by dependency. Do not begin broad Blender UI work until preservation round trips are trustworthy.

### Milestone 0: Project skeleton and format audit

- Create `MeleeMap.Core`, `MeleeMap.Cli`, and test projects.
- Establish the HSDLib fork workflow.
- Add CLI JSON result/error conventions.
- Add a corpus inventory test for all stage DATs.
- Document the initial structure corrections with decomp source references.

Exit criteria:

- `meleemap inspect example_assets/GrNLa.dat --json` reports roots and stage counts.
- All 71 DATs parse and produce an inventory, with known exceptions documented rather than swallowed.

### Milestone 1: Preservation writer and validation

- Implement corrected stage schemas.
- Implement the preservation save profile.
- Add archive, model-identity, and collision validators.
- Add semantic graph comparison.
- Round-trip the corpus.

Exit criteria:

- Every supported fixture can be loaded, saved, reloaded, and semantically compared.
- No public root disappears.
- `GrNLa` collision and model-group counts remain unchanged.

### Milestone 2: Headless extraction

- Decode `GrNLa` JOBJ/DOBJ/POBJ geometry.
- Export the versioned session manifest and grey mesh payloads.
- Export collision edges and metadata.
- Implement coordinate conversion tests.

Exit criteria:

- The session contains all ten `GrNLa` model-group identities.
- Extracted collision exactly reconstructs the original 16 vertices, 16 lines, category ranges, flags, and links after coordinate round trip.

### Milestone 3: Blender import and collision POC

- Create the add-on skeleton and preferences for locating the CLI.
- Implement DAT import through `meleemap extract`.
- Build model-group collections and grey meshes.
- Build collision mesh attributes and visualization.
- Implement collision assignment operators and dirty tracking.
- Export collision edits through `meleemap apply`.

Exit criteria:

- A user can move a collision vertex in Blender and produce a structurally valid output DAT.
- Unedited model and deferred data survive.
- The edited collision works in a manual in-game test.

### Milestone 4: Grey model editing POC

- Select the first replaceable `GrNLa` render target.
- Serialize dirty Blender mesh geometry.
- Build a minimal grey HSD material and generated POBJ.
- Replace only that render target in the source graph.
- Validate and reload the result.

Exit criteria:

- A visibly different grey mesh exported from Blender appears in game.
- Collision still functions.
- The stage loads without removing unrelated roots or model groups.

This is the first complete POC release.

### Milestone 5: Generalize the initial map editor

- Support multiple dirty render targets and model groups.
- Improve collision tools for multiple static groups.
- Characterize and implement dynamic collision attachments.
- Run import/export tests across increasingly complex fixtures.
- Add Linux packaging with a bundled self-contained CLI.
- Add user documentation and troubleshooting output.

Exit criteria:

- Static stages in the corpus can be edited without stage-specific code in the add-on.
- Dynamic stages are either supported or clearly restricted without corrupting their data.
- Installation requires enabling one add-on package rather than manually configuring a development environment.

### Milestone 6: Windows support

- Publish the CLI for Windows.
- Remove remaining POSIX path/process assumptions.
- Add Windows Blender smoke tests.
- Package and document the Windows add-on bundle.

## 14. POC acceptance criteria

The POC is complete when all of the following are true:

1. The add-on installs and runs in the pinned Blender version on Linux.
2. `GrNLa.dat` imports through the bundled or configured CLI.
3. Blender displays its ten model groups as grey geometry and its complete static collision graph.
4. Model-group identities cannot be accidentally renumbered.
5. A collision vertex or edge can be edited and exported.
6. At least one existing render target can be replaced with edited grey geometry and exported.
7. The output DAT reopens successfully and passes archive, model, and collision validation.
8. Public roots for capabilities not yet editable remain present and semantically unchanged.
9. Blender confirms overwriting existing files, and failed validation leaves the previous output intact. The session source snapshot remains unchanged.
10. The modified collision and grey geometry are confirmed manually in game.

## 15. Risks and mitigations

### HSDRaw assumptions conflict with engine behavior

Mitigation: treat decompiled load/use sites as authoritative, retain unknown fields, and add a fixture test for every corrected field.

### Rebuilding a DAT drops obscure data

Mitigation: apply edits to a fresh parse of the original, disable optimization/deduplication, preserve every root, and compare untouched payloads semantically.

### Blender topology edits lose collision metadata

Mitigation: use explicit edge attributes, provide add-on operators for creating/configuring edges, and fail on ambiguous topology rather than guessing.

### Model conversion is larger than the collision work

Mitigation: start with one `GrNLa` render target, positions/triangles, and a known grey material. Preserve all other render objects. Add normals and generalization after the first in-game result.

### GX limits are discovered only in game

Mitigation: validate encoded field widths and buffer indices, keep generated primitives simple, and add limits based on both retail fixtures and decompiled loader behavior.

### Blender API changes

Mitigation: pin one Blender version for the POC, isolate Blender access behind small scene/protocol modules, and add headless smoke tests before supporting more versions.

### Game assets cannot be shipped with releases

Mitigation: keep corpus discovery optional in tests, ship synthetic unit fixtures where possible, and document how developers point tests at locally obtained stage files.

## 16. Post-POC capability map and extension boundaries

No game-asset feature is declared permanently unsupported by this plan. The POC and initial milestones implement only the smallest map-editing path; later work is expected to expand through separate, focused plans.

Planned map-editor expansion areas include:

- Full JOBJ hierarchy editing and reference remapping.
- Adding, deleting, and reordering model groups with stage-code-aware validation.
- Textures, palettes, UVs, vertex colors, GX/TEV materials, and faithful previews.
- Joint, material, texture, and shape animation editing.
- Dynamic collision and collision-to-model attachments.
- General points, spawn points, camera and blast-zone controls, and stage parameters.
- Cameras, light animation and unsupported LOBJ fields, fog, splines, shadows, particles, and effects.
- Items, articles, audio references, and stage-specific parameter schemas.
- Creating new stage archives from templates and, later, from an empty project.

Planned asset-family expansion areas include fighters and costumes, fighter animations and gameplay data, items and articles, effects and particles, menus and UI assets, trophies, common-data archives, audio, and other game content. This list is illustrative rather than exhaustive: every asset family is post-POC scope. Their workflows are outside this map POC's milestone schedule, but not outside the intended product direction.

To keep those expansions possible, the architecture must preserve these seams:

- The archive graph and relocation writer live below all asset-specific code.
- GX mesh/texture primitives are shared HSD concepts, not map-only utilities.
- The CLI protocol declares asset and schema versions.
- Blender scene roles are namespaced (`mme_*`) instead of tied directly to current class names.
- Stage-specific roots are handled through optional schema modules rather than switches in the archive reader.
- Future asset tools can use the lower archive/GX layer without depending on the map domain or Blender scene layout.

This plan does not attempt to design every later editor now. It ensures the DAT parser, writer, identity model, and GX conversion code are not embedded in map-specific UI code, so those capabilities can be added without replacing the foundation.

## 17. First implementation slice

The first development slice should be entirely headless:

1. Add the C# solution and CLI.
2. Patch the three known stage-structure discrepancies in the HSDRaw fork.
3. Implement `inspect`, `validate`, and preservation-mode `roundtrip`.
4. Run them across all 71 DAT fixtures.
5. Implement extraction of `GrNLa` collision and one model target into a versioned session.

Only after this slice passes should implementation move into Blender. It establishes whether the project can safely own the output file before investing in the editing interface.
