"""Editable JOBJ animation and read-only material animation playback."""
import json
import math
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

from .protocol import StageError, digest, read
from .transforms import AXES, joint_srt


_group_cache = {}
_applied = {}
_armature_cache = {}
_material_cache = {}


def clear_cache():
    _group_cache.clear()
    _applied.clear()
    _armature_cache.clear()
    _material_cache.clear()


def invalidate_material(material):
    """Discard preview bindings after a material node tree is rebuilt."""
    _material_cache.pop(material.as_pointer(), None)
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


def fcurves(action):
    """Return F-curves from legacy or layered Blender Actions."""
    if hasattr(action, 'fcurves'):
        return list(action.fcurves)
    return [curve for layer in action.layers for strip in layer.strips
            if hasattr(strip, 'channelbags') for bag in strip.channelbags
            for curve in bag.fcurves]


def _curve(action, path, index):
    return next((curve for curve in fcurves(action)
                 if curve.data_path == path and curve.array_index == index), None)


def fingerprint(action, curves=None):
    payload = []
    for curve in sorted(fcurves(action) if curves is None else curves,
                        key=lambda item: (item.data_path, item.array_index)):
        payload.append([curve.data_path, curve.array_index, curve.extrapolation,
            [[*point.co, point.interpolation, *point.handle_left, *point.handle_right,
              point.handle_left_type, point.handle_right_type]
             for point in curve.keyframe_points],
            [[modifier.type, modifier.mute] for modifier in curve.modifiers]])
    return digest(payload)


def _node_fingerprint(action, bone):
    paths = {bone.path_from_id(field) for field in ('location', 'rotation_euler', 'scale')}
    return fingerprint(action, [curve for curve in fcurves(action) if curve.data_path in paths])


def structure(scene, action):
    """Validate and fingerprint protected F-curve targets without protecting values."""
    ids = set(json.loads(action.get('mme_fcurve_jobj_ids', '[]')))
    if not ids:
        if fcurves(action):
            raise StageError(f'{action.name}: this animation slot has no editable JOBJ channels.')
        return []
    armature = next((obj for obj in scene.objects
                     if obj.type == 'ARMATURE' and _is_action(action, obj)), None)
    if armature is None:
        raise StageError(f'{action.name}: owning JOBJ armature is missing.')
    bones = {bone.get('mme_id'): bone for bone in armature.pose.bones}
    allowed = {(bones[key].path_from_id(field), axis)
               for key in ids if key in bones
               for field in ('location', 'rotation_euler', 'scale') for axis in range(3)}
    actual = {(curve.data_path, curve.array_index) for curve in fcurves(action)}
    if len(bones.keys() & ids) != len(ids) or actual != allowed:
        raise StageError(f'{action.name}: animation channel targets changed. Restore the imported transform channels.')
    if any(len(curve.modifiers) > 1 or any(modifier.type != 'CYCLES' for modifier in curve.modifiers)
           for curve in fcurves(action)):
        raise StageError(f'{action.name}: only the imported loop modifiers are supported for DAT export.')
    return sorted([path, index] for path, index in actual)


def create(armature, group, source_file, source_hash, session_id, created):
    """Create one Blender Action for every source animation slot."""
    joint_sets = group.get('jointAnimations', [])
    material_sets = group.get('materialAnimations', [])
    slots = {}
    for animation in (*joint_sets, *material_sets):
        current = slots.setdefault(animation['slot'], {'endFrame': 0, 'loop': False})
        current['endFrame'] = max(current['endFrame'], animation['endFrame'])
        current['loop'] = current['loop'] or animation['loop']
    if not slots:
        return 0
    armature['mme_animation_source'] = source_file
    animated = {node['jobjId'] for animation in joint_sets for node in animation['nodes']}
    for bone in armature.pose.bones:
        if bone.get('mme_id') in animated:
            bone['mme_animated'] = True
            bone.bone['mme_animated'] = True
    made = []
    for slot, animation in sorted(slots.items()):
        action = bpy.data.actions.new(f"Group {group['index']:03d} Stage Animation {slot:03d}")
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
        joint_animation = next((item for item in joint_sets if item['slot'] == slot), None)
        editable_ids = []
        if joint_animation:
            armature.animation_data_create()
            armature.animation_data.action = action
            joints = {joint['id']: joint for joint in group['joints']}
            bones = {bone.get('mme_id'): bone for bone in armature.pose.bones}
            for node in joint_animation['nodes']:
                if node.get('editable') and _bake_node(action, bones[node['jobjId']],
                                                       joints[node['jobjId']], node):
                    editable_ids.append(node['jobjId'])
        action['mme_fcurve_jobj_ids'] = json.dumps(editable_ids)
        action['mme_curve_baselines'] = json.dumps({jobj_id: _node_fingerprint(action, bones[jobj_id])
                                                   for jobj_id in editable_ids})
        action['mme_curve_baseline'] = fingerprint(action)
    armature['mme_animation_count'] = len(made)
    armature.animation_data_create()
    armature.animation_data.action = made[0]
    return max(math.ceil(action.get('mme_end_frame', 0)) + 1 for action in made)


def _bake_node(action, bone, joint, node):
    end = float(node['endFrame'])
    if abs(end - round(end)) > 1e-5 or end < 1:
        return False
    values_by_field = {field: [[] for _ in range(3)]
                       for field in ('location', 'rotation_euler', 'scale')}
    previous_euler = None
    for source_frame in range(round(end) + 1):
        node_frame = source_frame % end if node['loop'] and end > 0 else source_frame
        values = {name: [joint[name][axis] for axis in 'xyz']
                  for name in ('rotation', 'scale', 'translation')}
        for track in node['tracks']:
            field, axis = track['channel'].split('.')
            _set_component(values[field], axis, _value(track['keys'], node_frame))
        if min(abs(component) for component in values['scale']) < 1e-6 \
                or any(component < 0 for component in values['scale']):
            return False
        source = {name: dict(zip('xyz', value)) for name, value in values.items()}
        matrix = AXES @ joint_srt(source) @ AXES.inverted()
        location, rotation, scale = matrix.decompose()
        euler = rotation.to_euler('XYZ', previous_euler)
        previous_euler = euler.copy()
        for axis in range(3):
            values_by_field['location'][axis].append(location[axis])
            values_by_field['rotation_euler'][axis].append(euler[axis])
            values_by_field['scale'][axis].append(scale[axis])
    for field in ('location', 'rotation_euler', 'scale'):
        setattr(bone, field, type(getattr(bone, field))(
            values_by_field[field][axis][0] for axis in range(3)))
        bone.keyframe_insert(data_path=field, frame=1, group=bone.name)
    for field in ('location', 'rotation_euler', 'scale'):
        path = bone.path_from_id(field)
        for axis in range(3):
            curve = _curve(action, path, axis)
            if curve is None:
                raise StageError(f'{action.name}: Blender did not create the expected bone F-curve.')
            reduced = _compress(values_by_field[field][axis], tolerance=1e-4)
            points = curve.keyframe_points
            points.add(len(reduced) - 1)
            for point, (source_frame, value) in zip(points, reduced):
                point.co = (source_frame + 1, value)
            for point in points:
                point.interpolation = 'LINEAR'
            curve.extrapolation = 'CONSTANT'
            if node['loop']:
                modifier = curve.modifiers.new('CYCLES')
                modifier.mode_before = 'REPEAT'
                modifier.mode_after = 'REPEAT'
            curve.update()
    return True


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


def _linear(value):
    value = max(0.0, min(1.0, value))
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def _material_runtime(material):
    """Cache immutable source state and direct Blender socket bindings."""
    preview_text = material.get('mme_animation_preview', '{}')
    images_text = material.get('mme_animation_images', '[]')
    if not material.node_tree:
        return None
    nodes = material.node_tree.nodes
    surface_node = nodes.get('Stage Surface')
    signature = (preview_text, images_text, material.node_tree.as_pointer(),
                 surface_node.as_pointer() if surface_node else 0, len(nodes))
    key = material.as_pointer()
    cached = _material_cache.get(key)
    if cached and cached['signature'] == signature:
        return cached
    preview = json.loads(preview_text)
    if not preview:
        return None
    textures = preview.get('textures') or ([preview['texture']] if preview.get('texture') else [])
    ambient = nodes.get('Stage Material Ambient')
    specular = nodes.get('Stage Specular Color')
    lighting = nodes.get('Stage Diffuse Lighting')
    color_modulation = nodes.get('Stage Color Modulation')
    extension = nodes.get('Stage Extension Base')
    color_sockets = {
        'ambient': [ambient.outputs[0]] if ambient else [],
        'specular': [specular.inputs[1]] if specular else [],
        'diffuse': []
    }
    if lighting and not lighting.inputs[1].is_linked:
        color_sockets['diffuse'].append(lighting.inputs[1])
    if surface_node and not surface_node.inputs['Color'].is_linked:
        color_sockets['diffuse'].append(surface_node.inputs['Color'])
    if color_modulation and not color_modulation.inputs[1].is_linked:
        color_sockets['diffuse'].append(color_modulation.inputs[1])
    color_sockets['diffuse'].extend(node.inputs[1] for node in nodes
        if node.name.startswith('Stage Diffuse Tint') and not node.inputs[1].is_linked)
    if extension:
        color_sockets['diffuse'].append(extension.outputs[0])
    samplers = {node.get('mme_texture_index'): node for node in nodes
                if node.type == 'TEX_IMAGE' and node.get('mme_texture_index') is not None}
    controls = []
    for index, layer in enumerate(textures):
        suffix = '' if index == 0 else f' {index + 1}'
        sampler = samplers.get(index)
        controls.append({
            'matrix': [(nodes.get(f'Stage Texture Matrix {name}{suffix}'),
                        nodes.get(f'Stage Texture Offset {name}{suffix}')) for name in ('U', 'V')],
            'blends': [node.inputs[0] for node in nodes
                       if node.get('mme_texture_index') == index
                       and node.get('mme_texture_operation') == 3],
            'sampler': sampler,
            'base_image': bpy.data.images.get(sampler.get('mme_base_image', '')) if sampler else None,
            'tev': {key: (nodes.get(f'Stage TEV {label}{suffix}'),
                          nodes.get(f'Stage TEV {label} Alpha{suffix}'))
                    for key, label in (('konst', 'Konst'), ('tev0', 'Register 0'),
                                       ('tev1', 'Register 1'))}
        })
    image_catalog = {(entry['slot'], entry['textureIndex'], entry['imageIndex'], entry['paletteIndex']):
                     bpy.data.images.get(entry['imageName'])
                     for entry in json.loads(images_text)}
    cached = {
        'signature': signature, 'preview': preview, 'textures': textures,
        'color_sockets': color_sockets,
        'alpha': nodes.get('Stage Material Alpha'),
        'controls': controls, 'images': image_catalog,
        'state': {}, 'slot': object()
    }
    _material_cache[key] = cached
    return cached


def _assign(runtime, key, socket, value):
    if socket is None:
        return
    value = tuple(value) if isinstance(value, (tuple, list, Vector)) else value
    if runtime['state'].get(key) == value:
        return
    socket.default_value = value
    runtime['state'][key] = value


def _assign_color(runtime, field, value):
    encoded = (*(_linear(component) for component in value[:3]), 1)
    for index, socket in enumerate(runtime['color_sockets'][field]):
        _assign(runtime, ('color', field, index), socket, encoded)


def _assign_matrix(runtime, index, layer):
    from . import surface
    matrix = surface.preview_matrix(layer)
    for axis, (row, offset) in enumerate(runtime['controls'][index]['matrix']):
        _assign(runtime, ('matrix', index, axis), row.inputs[1] if row else None,
                tuple(matrix[axis][component] for component in range(3)))
        _assign(runtime, ('offset', index, axis), offset.inputs[1] if offset else None,
                matrix[axis][3])


def _assign_tev(runtime, index, tev):
    if not tev:
        return
    for key, (color, alpha) in runtime['controls'][index]['tev'].items():
        value = tev[key]
        _assign(runtime, ('tev-color', index, key), color.outputs[0] if color else None,
                (*value[:3], 1))
        _assign(runtime, ('tev-alpha', index, key), alpha.outputs[0] if alpha else None,
                value[3])


def _assign_image(runtime, index, image):
    sampler = runtime['controls'][index]['sampler']
    if sampler is not None and sampler.image != image and image is not None:
        sampler.image = image


def _apply_material(material, target, source_frame, animation_slot):
    runtime = _material_runtime(material)
    if runtime is None:
        return
    preview, textures = runtime['preview'], runtime['textures']
    reset = runtime['slot'] != animation_slot
    frame = source_frame
    end = max(0.0, float(target.get('materialEndFrame', target['endFrame'])))
    if target.get('materialLoop', target['loop']) and end > 0:
        frame %= end
    colors = {
        'ambient': list((preview.get('ambientColor') or preview.get('color') or [1, 1, 1, 1])[:3]),
        'diffuse': list((preview.get('color') or [1, 1, 1, 1])[:3]),
        'specular': list((preview.get('specularColor') or [0, 0, 0, 1])[:3])
    }
    if preview.get('useVertexColor'):
        colors['diffuse'] = [1, 1, 1]
    alpha = (preview.get('alpha') or {}).get('material', 1)
    animated_colors = set()
    animated_alpha = False
    for track in target['tracks']:
        value = _value(track['keys'], frame)
        field, component = (track['channel'].split('.', 1) + [None])[:2]
        if field in colors and component in 'rgb':
            colors[field]['rgb'.index(component)] = value
            animated_colors.add(field)
        elif field == 'alpha':
            alpha = 1 - value
            animated_alpha = True
    for field, value in colors.items():
        if reset or field in animated_colors:
            _assign_color(runtime, field, value)
    if reset or animated_alpha:
        alpha_node = runtime['alpha']
        _assign(runtime, ('alpha',), alpha_node.outputs[0] if alpha_node else None,
                max(0.0, min(1.0, alpha)))

    texture_targets = {}
    for texture_animation in target['textures']:
        index = texture_animation['textureIndex']
        if 0 <= index < len(textures):
            texture_targets.setdefault(index, []).append(texture_animation)
    for index, base in enumerate(textures):
        texture_animations = texture_targets.get(index, [])
        channels = {track['channel'] for texture_animation in texture_animations
                    for track in texture_animation['tracks']}
        needs_matrix = reset or bool(channels & {
            'translation.x', 'translation.y', 'scale.x', 'scale.y',
            'rotation.x', 'rotation.y', 'rotation.z'})
        needs_blend = reset or 'blend' in channels
        needs_tev = reset or any(channel.startswith(('konst.', 'tev0.', 'tev1.')) for channel in channels)
        needs_image = reset or bool(channels & {'image', 'palette'})
        layer = None
        tev = None
        selected_image = runtime['controls'][index]['base_image'] if reset else None
        blend = base.get('colorBlend', 1)
        if needs_matrix:
            layer = dict(base)
            for field in ('translation', 'scale', 'rotation'):
                layer[field] = list(base[field])
        if needs_tev and base.get('tev'):
            tev = {key: list(base['tev'][key]) for key in ('konst', 'tev0', 'tev1')}
        for texture_animation in texture_animations:
            texture_frame = source_frame
            texture_end = max(0.0, float(texture_animation['endFrame']))
            if texture_animation['loop'] and texture_end > 0:
                texture_frame %= texture_end
            image_index = palette_index = -1
            for track in texture_animation['tracks']:
                channel = track['channel']
                if channel == 'lodBias':
                    continue
                value = _value(track['keys'], texture_frame)
                if channel == 'image':
                    image_index = int(value)
                elif channel == 'palette':
                    palette_index = int(value)
                elif channel == 'blend':
                    blend = value
                elif channel.startswith(('konst.', 'tev0.', 'tev1.')):
                    field, component = channel.split('.')
                    if tev and component in 'rgba':
                        byte = max(0, min(255, int(255.0 * value)))
                        tev[field]['rgba'.index(component)] = byte / 255.0
                else:
                    field, axis = channel.split('.')
                    layer[field]['xyz'.index(axis)] = value
            if image_index >= 0 or palette_index >= 0:
                selected_image = runtime['images'].get(
                    (animation_slot, index, image_index, palette_index), selected_image)
        if needs_matrix:
            _assign_matrix(runtime, index, layer)
        if needs_blend:
            value = max(0.0, min(1.0, blend))
            for control_index, socket in enumerate(runtime['controls'][index]['blends']):
                _assign(runtime, ('blend', index, control_index), socket, value)
        if needs_tev:
            _assign_tev(runtime, index, tev or base.get('tev'))
        if needs_image:
            _assign_image(runtime, index, selected_image)
    runtime['slot'] = animation_slot


def _apply_materials(runtime, action, source_frame):
    slot = action.get('mme_animation_slot') if action else None
    targets = runtime['material_targets'].get(slot, {})
    empty = {'endFrame': 0, 'loop': False, 'tracks': [], 'textures': []}
    for material_id, materials in runtime['materials'].items():
        target = targets.get(material_id, empty)
        for material in materials:
            _apply_material(material, target, source_frame, slot)


def _armature_runtime(scene, armature, payload):
    key = armature.as_pointer()
    signature = (scene.mme_session_id, armature.data.as_pointer(), len(armature.pose.bones), id(payload))
    cached = _armature_cache.get(key)
    if cached and cached['signature'] == signature:
        return cached
    joints = {joint['id']: joint for joint in payload['joints']}
    bones = {bone.get('mme_id'): bone for bone in armature.pose.bones}
    animated_bones = [(jobj_id, joint, bones.get(jobj_id)) for jobj_id, joint in joints.items()
                      if bones.get(jobj_id) is not None and bones[jobj_id].get('mme_animated')]
    joint_nodes = {animation['slot']: {node['jobjId']: node for node in animation['nodes']}
                   for animation in payload.get('jointAnimations', [])}
    material_targets = {animation['slot']: {target['materialId']: target
                                            for target in animation['materials']}
                        for animation in payload.get('materialAnimations', [])}
    animated_ids = {material_id for targets in material_targets.values() for material_id in targets}
    materials = {}
    if animated_ids:
        used = {slot.material for obj in scene.objects
                if obj.get('mme_session_id') == scene.mme_session_id
                for slot in getattr(obj, 'material_slots', []) if slot.material}
        for material in used:
            material_id = material.get('mme_model_material_id') or material.get('mme_preview_model_id')
            if material_id in animated_ids:
                materials.setdefault(material_id, []).append(material)
    cached = {
        'signature': signature, 'animated_bones': animated_bones,
        'joint_nodes': joint_nodes, 'material_targets': material_targets,
        'materials': materials, 'fcurve_ids': {}
    }
    _armature_cache[key] = cached
    return cached


def _apply_armature(scene, armature):
    action = active_action(armature)
    source_frame = max(0.0, scene.frame_current_final - 1.0)
    slot = action.get('mme_animation_slot') if action else None
    key = (action.as_pointer() if action else 0, slot, source_frame)
    if _applied.get(armature.as_pointer()) == key:
        return
    # Mark the state before touching matrices because those changes schedule a
    # depsgraph callback, which should see this application as already current.
    _applied[armature.as_pointer()] = key
    payload = _group(scene, armature)
    runtime = _armature_runtime(scene, armature, payload)
    animated_nodes = runtime['joint_nodes'].get(slot, {})
    curve_text = action.get('mme_fcurve_jobj_ids', '[]') if action else '[]'
    curve_key = (action.as_pointer() if action else 0, curve_text)
    fcurve_ids = runtime['fcurve_ids'].get(curve_key)
    if fcurve_ids is None:
        fcurve_ids = set(json.loads(curve_text))
        runtime['fcurve_ids'][curve_key] = fcurve_ids
    for jobj_id, joint, bone in runtime['animated_bones']:
        if jobj_id in fcurve_ids:
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
    _apply_materials(runtime, action, source_frame)


def apply(scene):
    if not getattr(scene, 'mme_session', ''):
        return
    for armature in scene.objects:
        if (armature.type == 'ARMATURE' and armature.get('mme_role') == 'jobj-armature'
                and armature.get('mme_session_id') == scene.mme_session_id
                and armature.get('mme_animation_source')):
            _apply_armature(scene, armature)


def edits(scene, stage, groups):
    """Serialize changed native bone F-curves as sampled game-local HSD tracks."""
    declared = {(item['groupIndex'], item['slot'], item['jobjId'])
                for item in stage.get('editableJointAnimations', [])}
    result = []
    axes_inverse = AXES.inverted()
    groups_by_index = {group['index']: group for group in groups}
    for armature in (obj for obj in scene.objects
                     if obj.type == 'ARMATURE' and obj.get('mme_role') == 'jobj-armature'
                     and obj.get('mme_session_id') == scene.mme_session_id):
        group = groups_by_index[armature['mme_group_index']]
        joints = {joint['id']: joint for joint in group['joints']}
        bones = {bone.get('mme_id'): bone for bone in armature.pose.bones}
        for action in actions(armature):
            structure(scene, action)
            dirty = fingerprint(action) != action.get('mme_curve_baseline', fingerprint(action))
            action['mme_dirty'] = dirty
            if not dirty:
                continue
            slot = action['mme_animation_slot']
            source_set = next((item for item in group.get('jointAnimations', [])
                               if item['slot'] == slot), None)
            if source_set is None:
                raise StageError(f'{action.name}: this slot has no editable JOBJ animation.')
            source_nodes = {node['jobjId']: node for node in source_set['nodes']}
            baselines = json.loads(action.get('mme_curve_baselines', '{}'))
            for jobj_id in json.loads(action.get('mme_fcurve_jobj_ids', '[]')):
                if _node_fingerprint(action, bones[jobj_id]) == baselines.get(jobj_id):
                    continue
                if (group['index'], slot, jobj_id) not in declared:
                    raise StageError(f'{action.name}: animation target is not declared by this session.')
                node = source_nodes[jobj_id]
                end = float(node['endFrame'])
                if abs(end - round(end)) > 1e-5 or end < 1:
                    raise StageError(f'{action.name}: editable animation duration must be a positive whole number.')
                bone = bones[jobj_id]
                curves = {field: [_curve(action, bone.path_from_id(field), axis)
                                  for axis in range(3)]
                          for field in ('location', 'rotation_euler', 'scale')}
                if any(point.co.x < 1 - 1e-4 or point.co.x > end + 1 + 1e-4
                       for field_curves in curves.values() for curve in field_curves
                       for point in curve.keyframe_points):
                    raise StageError(f'{action.name} / {bone.name}: keys must stay within the existing animation duration.')
                samples = {name: [[] for _ in range(3)]
                           for name in ('rotation', 'scale', 'translation')}
                previous_euler = None
                for source_frame in range(round(end) + 1):
                    frame = source_frame + 1
                    location = Vector(curves['location'][axis].evaluate(frame) for axis in range(3))
                    rotation = Euler(tuple(curves['rotation_euler'][axis].evaluate(frame)
                                           for axis in range(3)), 'XYZ')
                    scale = Vector(curves['scale'][axis].evaluate(frame) for axis in range(3))
                    if min(abs(value) for value in scale) < 1e-6 or any(value < 0 for value in scale):
                        raise StageError(f'{action.name} / {bone.name}: animated scale must stay positive and nonzero.')
                    local = axes_inverse @ Matrix.LocRotScale(location, rotation, scale) @ AXES
                    source_scale = Vector(local.to_3x3().col[axis].length for axis in range(3))
                    rotation_matrix = local.to_3x3()
                    for axis in range(3):
                        rotation_matrix.col[axis] /= source_scale[axis]
                    if max(abs(rotation_matrix.col[a].dot(rotation_matrix.col[b]))
                           for a, b in ((0, 1), (0, 2), (1, 2))) > 1e-4 \
                            or abs(rotation_matrix.determinant() - 1) > 1e-4:
                        raise StageError(f'{action.name} / {bone.name}: animated transform contains shear or reflection.')
                    source_rotation = rotation_matrix.to_euler('XYZ', previous_euler)
                    previous_euler = source_rotation.copy()
                    for axis in range(3):
                        samples['translation'][axis].append(local.translation[axis])
                        samples['rotation'][axis].append(source_rotation[axis])
                        samples['scale'][axis].append(source_scale[axis])
                tracks = []
                for field in ('rotation', 'translation', 'scale'):
                    for axis, name in enumerate('xyz'):
                        keys = [{'frame': frame, 'value': value, 'tangent': 0,
                                 'interpolation': 'HSD_A_OP_LIN'}
                                for frame, value in _compress(samples[field][axis])]
                        tracks.append({'channel': f'{field}.{name}', 'keys': keys})
                result.append({'groupIndex': group['index'], 'slot': slot,
                               'jobjId': jobj_id, 'tracks': tracks})
    return {'protocolVersion': 2, 'coordinateSpace': 'game-jobj-animation',
            'nodes': result} if result else None


def _compress(values, tolerance=1e-6):
    """Reduce sampled values to piecewise-linear keys within a vertical error."""
    points = [(frame, value) for frame, value in enumerate(values)]
    if len(points) <= 2:
        return points
    keep = {0, len(points) - 1}
    pending = [(0, len(points) - 1)]
    while pending:
        start, end = pending.pop()
        left, right = points[start], points[end]
        worst_error = -1
        worst = None
        for index in range(start + 1, end):
            amount = (points[index][0] - left[0]) / (right[0] - left[0])
            expected = left[1] + (right[1] - left[1]) * amount
            error = abs(points[index][1] - expected)
            if error > worst_error:
                worst_error, worst = error, index
        if worst is not None and worst_error > tolerance:
            keep.add(worst)
            pending.extend(((start, worst), (worst, end)))
    return [points[index] for index in sorted(keep)]


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
    context.view_layer.update()
    _applied.pop(armature.as_pointer(), None)
    apply(context.scene)
    return armature
