# Dynamic collision and moving-platform audit

## Scope

This audit defines the generic collision/JOBJ relationship that can be derived
from a stage DAT and separates it from bindings or behavior supplied by stage
code and companion archives. The editor's write contract covers the selected
DAT only: unknown runtime behavior may limit preview or require an in-game
warning, but does not by itself make serialized collision geometry read-only.

The evidence comes from the bundled Melee decomp, the HSDRaw fork, Core's strict
archive reader, and representative retail DATs in the local corpus.

## Serialized structures

`coll_data` owns three relevant arrays:

- vertices in joint-local game X/Y coordinates;
- lines divided into floor, ceiling, right-wall, left-wall, and dynamic ranges;
- collision joints, each with five line ranges, local bounds, and a contiguous
  vertex range.

Each `map_head` model-group record may also contain an array of six-byte
`GrJoint` records at group offset `+0x20`. The three signed 16-bit values are:

1. collision-joint index;
2. a reserved/source value (all inspected serialized records use `-1`); and
3. JOBJ preorder index.

The containing `map_head` record identifies the model group. The JOBJ index is
not a global model index: it selects within that group's serialized JOBJ
preorder. `Ground_InitMapColl` passes the group's runtime wrapper JOBJ to
`mpLib_800552B0`; that traversal starts at the wrapper's child, so index zero is
the serialized root JOBJ. Core's existing attachment validation therefore uses
the correct traversal basis.

## Runtime transform behavior

`mpLib_80055E9C` updates every vertex owned by an attached collision joint. It
forms `(localX, localY, 0)`, multiplies it by the selected JOBJ's complete world
matrix, and stores the resulting world X/Y. Uniform-scale/translation matrices
use a fast path; arbitrary JOBJ matrices use the general matrix-vector path.
Runtime bounds are transformed from the joint's local bounds and expanded by 30
game units.

This means the DAT collision coordinates must remain joint-local. A Blender
preview may evaluate a JOBJ transform, but serialization must write the
unmodified local-space coordinates rather than baking the displayed world pose.

The fifth line range is not an attachment list. After a joint moves,
`mpJointUpdateDynamics` classifies every line in that range as floor, ceiling,
right wall, or left wall from its transformed endpoint direction. Direction
therefore remains meaningful, but the stored line does not have one fixed
surface-side category. An attached joint may also own ordinary, fixed-category
lines, and dynamic-range lines may be controlled by stage code even when the DAT
contains no attachment record.

## Bindings outside the primary DAT

`Ground_InitMapColl` combines two sources:

- serialized `map_head` bindings from the loaded stage or companion archive;
- per-stage `StageData.joints` arrays compiled into game code.

The first source is inspectable and potentially writable. The second is not
represented in the DAT. Stage code may also directly hide, enable, disable,
merge, or update collision joints through `mpLib` calls and callbacks. Therefore
“no serialized attachments” does not prove that a joint is static. That
uncertainty affects runtime preview, not permission to edit local DAT geometry.

Companion archives introduce another unresolved case. A serialized attachment
can target a model group whose JOBJ tree is supplied by another archive. Core
can validate the collision-joint index and preserve the record, but cannot
resolve or preview the target JOBJ from the primary DAT alone.

## Representative corpus patterns

Counts below are facts read from the local retail DAT corpus. “Attachments”
means only serialized `map_head` records; it excludes bindings in game code.

| DAT | Joints | Dynamic lines | Serialized attachments | Important pattern |
| --- | ---: | ---: | ---: | --- |
| `GrNLa` | 1 | 0 | 0 | Current static-collision reference fixture. |
| `GrFs` | 5 | 0 | 0 | Stage code declares three `GrJoint` bindings despite no DAT signal. |
| `GrGb` | 8 | 6 | 0 | Dynamic lines whose bindings come from stage code. |
| `GrPs` | 8 | 24 | 1 | Serialized target is external to the primary DAT. |
| `GrMc` | 9 | 5 | 5 | Dynamic lines and locally resolvable serialized bindings coexist. |
| `GrBb` | 67 | 0 | 41 | Attached joints can contain only fixed-category lines. |
| `GrRc` | 45 | 11 | 74 | A collision joint may have multiple bindings in different model groups. |

These cases rule out treating attachment presence, dynamic-line presence, or
joint count as interchangeable capability tests.

## Capability classes

Phase 2 should expose capabilities per collision joint and per operation:

| Source situation | Preview | DAT geometry editing | Serialized attachment editing |
| --- | --- | --- | --- |
| No known binding, fixed-category lines | Existing local/static preview | Existing compiler, subject to source warnings | Not applicable |
| One locally resolved serialized binding | Parent/evaluate under the target JOBJ | Edit local geometry | Reassignment after link-array writing is implemented |
| Multiple locally resolved bindings | Show explicit stage-state variants, not one guessed pose | Edit the shared local geometry | Edit each serialized record explicitly |
| External/companion target | Explain unresolved target | Edit local geometry | Preserve the raw target or retarget/delete explicitly |
| Known or suspected stage-code binding | Preview only with an explicit stage profile | Edit local geometry | No serialized record exists to edit; never pretend a DAT edit changes code |
| Dynamic-range lines | Derive displayed side from the evaluated endpoints | Preserve dynamic membership and directed endpoints | Independent of line membership |

Backend validation must continue to come from immutable source facts. Stage
profiles may improve preview but are not an authority gate for DAT geometry.
Blender metadata must not be able to grant backend authority.

## Implementation sequence

1. Export immutable collision identities, serialized binding identities, target
   resolution, dynamic membership, and denial reasons.
2. Split Blender's collision representation by collision joint. Keep vertices in
   joint-local coordinates and evaluate locally resolved bindings under their
   target JOBJ for animation preview. Repeated bindings must be represented as
   explicit variants rather than stacked transforms.
3. Change collision serialization to submit per-joint local geometry while
   retaining stable source joint IDs.
4. Extend the compiler to preserve unchanged attachment arrays and dynamic-range
   membership, then enable DAT geometry editing independently of preview support.
5. Add append-only writing and reload verification for safe serialized
   attachment reassignment. Addition/deletion must preserve the reserved field
   and all unrelated group records.
6. Add stage profiles or companion-archive loading for code-controlled and
   external preview. Until then, report those preview cases as unresolved instead
   of guessing; DAT-local geometry editing remains available.

## In-game acceptance boundary

Automated archive validation can prove local-coordinate writes, reference
preservation, and deterministic reload. It cannot prove that stage code selects
the expected binding or that moving collision follows the intended animation.
At least one locally serialized moving platform and one stage-code-controlled
case must be tested in Dolphin or on hardware before Phase 2 is complete.
