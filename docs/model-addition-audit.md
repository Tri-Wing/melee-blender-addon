# Model-addition attachment analysis

This records the engine evidence and programmatic rules used to discover safe
attachment points. Selection does not depend on a filename, archive hash, stage
name, hardcoded offset, or explicit target allowlist.

## Engine behavior

- `HSD_JObjDispAll` in `melee/src/sysdolphin/baselib/jobj.c` renders a JOBJ's
  DOBJ list only when the JOBJ participates in the requested render pass, and
  follows children only when the corresponding root traversal bit is present.
  The initial preset is opaque, so the selected JOBJ must have `1 << 18` and
  every ancestor must have `JOBJ_ROOT_OPA` (`1 << 28`). No source flags are
  changed to enable an attachment.
- `JObjLoad` selects custom classes from the descriptor class-name pointer and
  loads particle/spline data from the same union used for DOBJ lists. Initial
  targets therefore require null class pointers and ordinary JOBJ/DOBJ types.
- `HSD_JObjDispAll` traverses instance targets as another rendered hierarchy.
  A group containing an instance is excluded so an addition cannot unexpectedly
  render through both the owning and instance paths.
- `grAnime_801C8138` in `melee/src/melee/gr/granime.c` loads joint, material,
  and shape animation trees for a selected stage group/slot, then calls
  `HSD_JObjAddAnimAll` and `HSD_JObjReqAnimAll`.
- `HSD_DObjAddAnimAll` in `melee/src/sysdolphin/baselib/dobj.c` advances the
  DOBJ and animation lists in parallel. `next_p(NULL)` remains null, so a DOBJ
  appended after all original DOBJ descriptors receives no material or shape
  animation. This list behavior is engine-wide, so existing material animation
  does not require a stage-specific exception. Shape animation remains rejected.

## Programmatic eligibility

For every ordinary JOBJ in every model group, the selector recomputes eligibility
from immutable `source.dat`. A target is emitted only when:

- its JOBJ path uses ordinary descriptors with unique, non-interior ownership;
- the path has invertible scale and no billboard, IK, quaternion, constraint,
  inverse-bind, custom-matrix, particle, spline, or instance behavior;
- the selected JOBJ participates in the opaque pass and every ancestor traverses
  children during that pass;
- the path has no joint/visibility animation and the selected node has no shape
  animation;
- an existing DOBJ chain, when present, is ordinary, ordered, uniquely owned,
  unaliased, and has a null tail link.

Targets report whether they are hidden at rest, already own geometry, or have
existing material animation so the Blender UI can describe inherited behavior.
The writer never changes source visibility or traversal flags.

As corpus examples, this analysis finds one target in `GrNLa.dat` and thirteen in
`GrGd.dat`. Other stages expose however many descriptors satisfy the same rules;
zero eligible targets leaves the capability disabled.

The DOBJ/POBJ/material writer, exact graph-extension validation, RGBA8 decode
verification, and Blender export/reimport fixtures pass automatically. Runtime
stage code can still make an otherwise structurally valid group appear only in
particular stage states, so in-game testing remains required; structural
validation is not a substitute for that acceptance check.
