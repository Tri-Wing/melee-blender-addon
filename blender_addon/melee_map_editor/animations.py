"""Read-only HSD joint-animation playback on imported JOBJ armatures."""
import math
from pathlib import Path

import bpy

from .protocol import read
from .transforms import AXES, joint_srt


_group_cache = {}
_applied = {}


def clear_cache():
    _group_cache.clear()
    _applied.clear()


def _group(scene, armature):
    path = Path(bpy.path.abspath(scene.mme_session)) / armature['mme_animation_source']
    key = str(path.resolve())
    payload = _group_cache.get(key)
    if payload is None:
        payload = read(path)
        _group_cache[key] = payload
    return payload


def _is_action(action, armature):
    return (action is not None and action.get('mme_role') == 'jobj-animation'
            and action.get('mme_session_id') == armature.get('mme_session_id')
            and action.get('mme_group_index') == armature.get('mme_group_index'))


def actions(armature):
    return sorted((action for action in bpy.data.actions if _is_action(action, armature)),
                  key=lambda action: action.get('mme_animation_slot', -1))


def active_action(armature):
    data = armature.animation_data
    return data.action if data and _is_action(data.action, armature) else None


def create(armature, group, source_file, source_hash, session_id, created):
    """Create one lightweight Blender Action for every source animation slot."""
    sets = group.get('jointAnimations', [])
    if not sets:
        return 0
    armature['mme_animation_source'] = source_file
    animated = {node['jobjId'] for animation in sets for node in animation['nodes']}
    for bone in armature.pose.bones:
        if bone.get('mme_id') in animated:
            bone['mme_animated'] = True
            bone.bone['mme_animated'] = True
    made = []
    for animation in sets:
        slot = animation['slot']
        action = bpy.data.actions.new(f"Group {group['index']:03d} JOBJ Animation {slot:03d}")
        created.append(action)
        made.append(action)
        action.use_fake_user = True
        action['mme_role'] = 'jobj-animation'
        action['mme_session_id'] = session_id
        action['mme_id'] = f"{group['id']}:animation:{slot}"
        action['mme_group_index'] = group['index']
        action['mme_source_hash'] = source_hash
        action['mme_animation_slot'] = slot
        action['mme_end_frame'] = animation['endFrame']
        action['mme_loop'] = animation['loop']
    armature['mme_animation_count'] = len(made)
    armature.animation_data_create()
    armature.animation_data.action = made[0]
    return max(math.ceil(action.get('mme_end_frame', 0)) + 1 for action in made)


def _value(keys, frame):
    """Evaluate decoded FOBJ keys exactly as HSDLib's FOBJ_Player.GetValue."""
    if not keys:
        return 0.0
    if len(keys) > 1 and frame >= keys[-1]['frame']:
        return keys[-1]['value']
    p0 = p1 = d0 = d1 = t0 = t1 = 0.0
    previous = operation = 'HSD_A_OP_CON'
    for key in keys:
        previous = operation
        operation = key['interpolation']
        if operation in ('HSD_A_OP_CON', 'HSD_A_OP_LIN'):
            p0, p1 = p1, key['value']
            if previous != 'HSD_A_OP_SLP':
                d0, d1 = d1, 0.0
            t0, t1 = t1, key['frame']
        elif operation == 'HSD_A_OP_SPL0':
            p0, d0, p1, d1 = p1, d1, key['value'], 0.0
            t0, t1 = t1, key['frame']
        elif operation == 'HSD_A_OP_SPL':
            p0, p1, d0, d1 = p1, key['value'], d1, key['tangent']
            t0, t1 = t1, key['frame']
        elif operation == 'HSD_A_OP_SLP':
            d0, d1 = d1, key['tangent']
        elif operation == 'HSD_A_OP_KEY':
            p0 = p1 = key['value']
        if t1 > frame and operation != 'HSD_A_OP_SLP':
            break
        previous = operation
    if frame <= t0:
        return p0
    if frame >= t1:
        return p1
    if t0 == t1 or previous in ('HSD_A_OP_CON', 'HSD_A_OP_KEY'):
        return p0
    time = frame - t0
    span = t1 - t0
    if previous == 'HSD_A_OP_LIN':
        return (p1 - p0) / span * time + p0
    if previous in ('HSD_A_OP_SPL', 'HSD_A_OP_SPL0', 'HSD_A_OP_SLP'):
        inverse = 1.0 / span
        squared = time * time
        cubic = inverse * inverse * squared * time
        three = 3.0 * squared * inverse * inverse
        tangent = cubic - squared * inverse
        cubic2 = 2.0 * cubic * inverse
        return (d1 * tangent + d0 * (time + tangent - squared * inverse)
                + p0 * (1.0 + cubic2 - three) + p1 * (-cubic2 + three))
    return p0


def _set_component(value, axis, component):
    value['xyz'.index(axis)] = component


def _apply_armature(scene, armature):
    payload = _group(scene, armature)
    action = active_action(armature)
    source_frame = max(0.0, scene.frame_current_final - 1.0)
    animation = None
    if action is not None:
        slot = action['mme_animation_slot']
        animation = next((item for item in payload.get('jointAnimations', [])
                          if item['slot'] == slot), None)
    key = (action.as_pointer() if action else 0, source_frame)
    if _applied.get(armature.as_pointer()) == key:
        return
    # Mark the state before touching matrices because those changes schedule a
    # depsgraph callback, which should see this application as already current.
    _applied[armature.as_pointer()] = key
    joints = {joint['id']: joint for joint in payload['joints']}
    bones = {bone.get('mme_id'): bone for bone in armature.pose.bones}
    animated_nodes = {node['jobjId']: node for node in animation['nodes']} if animation else {}
    for jobj_id, joint in joints.items():
        bone = bones.get(jobj_id)
        if bone is None or not bone.get('mme_animated'):
            continue
        values = {name: [joint[name][axis] for axis in 'xyz']
                  for name in ('rotation', 'scale', 'translation')}
        node = animated_nodes.get(jobj_id)
        if node:
            node_frame = source_frame
            end = max(0.0, float(node['endFrame']))
            if node['loop'] and end > 0:
                # HSD_AObjInterpretAnim wraps when curr_frame reaches
                # end_frame, with rewind_frame initialized to zero.
                node_frame %= end
            for track in node['tracks']:
                field, axis = track['channel'].split('.')
                _set_component(values[field], axis, _value(track['keys'], node_frame))
        source = {name: dict(zip('xyz', value)) for name, value in values.items()}
        bone.matrix_basis = AXES @ joint_srt(source) @ AXES.inverted()


def apply(scene):
    if not getattr(scene, 'mme_session', ''):
        return
    for armature in scene.objects:
        if (armature.type == 'ARMATURE' and armature.get('mme_role') == 'jobj-armature'
                and armature.get('mme_session_id') == scene.mme_session_id
                and armature.get('mme_animation_source')):
            _apply_armature(scene, armature)


def cycle(context, direction):
    candidates = [obj for obj in context.selected_objects
                  if obj.type == 'ARMATURE' and actions(obj)]
    armature = (context.active_object if context.active_object in candidates else
                candidates[0] if candidates else
                next((obj for obj in context.scene.objects
                      if obj.type == 'ARMATURE' and actions(obj)), None))
    if armature is None:
        return None
    available = actions(armature)
    current = active_action(armature)
    index = available.index(current) if current in available else 0
    armature.animation_data_create()
    armature.animation_data.action = available[(index + direction) % len(available)]
    _applied.pop(armature.as_pointer(), None)
    apply(context.scene)
    return armature
