"""Editable JOBJ animation and read-only material animation playback."""
import copy
import json
import math
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

from .protocol import StageError, digest, read
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


def _set_rgb(socket, value):
    socket.default_value = (*(_linear(component) for component in value[:3]), 1)


def _apply_material(material, target, source_frame, animation_slot):
    from . import surface
    preview = copy.deepcopy(json.loads(material.get('mme_animation_preview', '{}')))
    if not preview or not material.node_tree:
        return
    frame = source_frame
    end = max(0.0, float(target.get('materialEndFrame', target['endFrame'])))
    if target.get('materialLoop', target['loop']) and end > 0:
        frame %= end
    colors = {
        'ambient': list((preview.get('ambientColor') or preview.get('color') or [1, 1, 1, 1])[:3]),
        'diffuse': list((preview.get('color') or [1, 1, 1, 1])[:3]),
        'specular': list((preview.get('specularColor') or [0, 0, 0, 1])[:3])
    }
    # HSD's vertex-color source bypasses MOBJ diffuse RGB. Keep the texture
    # pass neutral while material animation updates transforms and alpha.
    if preview.get('useVertexColor'):
        colors['diffuse'] = [1, 1, 1]
    alpha = (preview.get('alpha') or {}).get('material', 1)
    for track in target['tracks']:
        value = _value(track['keys'], frame)
        field, component = (track['channel'].split('.', 1) + [None])[:2]
        if field in colors and component in 'rgb':
            colors[field]['rgb'.index(component)] = value
        elif field == 'alpha':
            # HSD stores the animated material channel as transparency.
            alpha = 1 - value
    nodes = material.node_tree.nodes
    ambient = nodes.get('Stage Material Ambient')
    if ambient:
        _set_rgb(ambient.outputs[0], colors['ambient'])
    specular = nodes.get('Stage Specular Color')
    if specular:
        _set_rgb(specular.inputs[1], colors['specular'])
    diffuse_targets = []
    lighting = nodes.get('Stage Diffuse Lighting')
    if lighting and not lighting.inputs[1].is_linked:
        diffuse_targets.append(lighting.inputs[1])
    stage_surface = nodes.get('Stage Surface')
    if stage_surface and not stage_surface.inputs['Color'].is_linked:
        diffuse_targets.append(stage_surface.inputs['Color'])
    color_modulation = nodes.get('Stage Color Modulation')
    if color_modulation and not color_modulation.inputs[1].is_linked:
        diffuse_targets.append(color_modulation.inputs[1])
    for node in nodes:
        if node.name.startswith('Stage Diffuse Tint') and not node.inputs[1].is_linked:
            diffuse_targets.append(node.inputs[1])
    extension = nodes.get('Stage Extension Base')
    if extension:
        _set_rgb(extension.outputs[0], colors['diffuse'])
    for socket in diffuse_targets:
        _set_rgb(socket, colors['diffuse'])
    alpha_node = nodes.get('Stage Material Alpha')
    if alpha_node:
        alpha_node.outputs[0].default_value = max(0.0, min(1.0, alpha))

    textures = preview.get('textures') or ([preview['texture']] if preview.get('texture') else [])
    animation_images = json.loads(material.get('mme_animation_images', '[]'))

    def set_matrix(index, layer):
        matrix = surface.preview_matrix(layer)
        suffix = '' if index == 0 else f' {index + 1}'
        for axis, name in enumerate(('U', 'V')):
            row = nodes.get(f'Stage Texture Matrix {name}{suffix}')
            offset = nodes.get(f'Stage Texture Offset {name}{suffix}')
            if row:
                row.inputs[1].default_value = tuple(matrix[axis][component] for component in range(3))
            if offset:
                offset.inputs[1].default_value = matrix[axis][3]

    # Always restore source texture state before applying the selected slot.
    # This prevents values from a previous Action persisting when the new slot
    # omits a material or one of its texture tracks.
    samplers = {node.get('mme_texture_index'): node for node in nodes
                if node.type == 'TEX_IMAGE' and node.get('mme_texture_index') is not None}
    for sampler in samplers.values():
        base = bpy.data.images.get(sampler.get('mme_base_image', ''))
        if base is not None:
            sampler.image = base
    for index, layer in enumerate(textures):
        set_matrix(index, layer)
        for node in nodes:
            if (node.get('mme_texture_index') == index
                    and node.get('mme_texture_operation') == 3):
                node.inputs[0].default_value = max(0.0, min(1.0, layer.get('colorBlend', 1)))

    for texture_animation in target['textures']:
        index = texture_animation['textureIndex']
        if index < 0 or index >= len(textures):
            continue
        texture_frame = source_frame
        texture_end = max(0.0, float(texture_animation['endFrame']))
        if texture_animation['loop'] and texture_end > 0:
            texture_frame %= texture_end
        layer = textures[index]
        image_index = palette_index = -1
        for track in texture_animation['tracks']:
            channel = track['channel']
            if channel == 'image':
                image_index = int(_value(track['keys'], texture_frame))
                continue
            if channel == 'palette':
                palette_index = int(_value(track['keys'], texture_frame))
                continue
            if channel == 'lodBias' or channel.startswith(('konst.', 'tev0.', 'tev1.')):
                continue
            value = _value(track['keys'], texture_frame)
            if channel == 'blend':
                for node in nodes:
                    if (node.get('mme_texture_index') == index
                            and node.get('mme_texture_operation') == 3):
                        node.inputs[0].default_value = max(0.0, min(1.0, value))
                continue
            field, axis = channel.split('.')
            layer[field]['xyz'.index(axis)] = value
        if image_index >= 0 or palette_index >= 0:
            frame = next((entry for entry in animation_images
                          if entry['slot'] == animation_slot and entry['textureIndex'] == index
                          and entry['imageIndex'] == image_index
                          and entry['paletteIndex'] == palette_index), None)
            image = bpy.data.images.get(frame['imageName']) if frame else None
            if image is not None and index in samplers:
                samplers[index].image = image
        set_matrix(index, layer)


def _apply_materials(scene, payload, action, source_frame):
    slot = action.get('mme_animation_slot') if action else None
    animation = next((item for item in payload.get('materialAnimations', [])
                      if item['slot'] == slot), None)
    targets = {item['materialId']: item for item in animation['materials']} if animation else {}
    animated_ids = {target['materialId'] for animation_set in payload.get('materialAnimations', [])
                    for target in animation_set['materials']}
    materials = {slot.material for obj in scene.objects
                 if obj.get('mme_session_id') == scene.mme_session_id
                 for slot in getattr(obj, 'material_slots', []) if slot.material}
    for material in materials:
        material_id = material.get('mme_model_material_id') or material.get('mme_preview_model_id')
        if material_id not in animated_ids:
            continue
        target = targets.get(material_id, {
            'endFrame': 0, 'loop': False, 'tracks': [], 'textures': []})
        _apply_material(material, target, source_frame, slot)


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
    fcurve_ids = set(json.loads(action.get('mme_fcurve_jobj_ids', '[]'))) if action else set()
    for jobj_id, joint in joints.items():
        bone = bones.get(jobj_id)
        if bone is None or not bone.get('mme_animated') or jobj_id in fcurve_ids:
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
    _apply_materials(scene, payload, action, source_frame)


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
