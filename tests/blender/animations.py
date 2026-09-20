"""Run with Blender: --background --factory-startup --python-exit-code 1 --python tests/blender/animations.py"""
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import animations, scene, transforms
from melee_map_editor.protocol import StageError, read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))


def rejects(action, message):
    try:
        action()
    except StageError as exc:
        assert message.lower() in str(exc).lower(), (message, str(exc))
    else:
        raise AssertionError('Expected rejection: ' + message)


# Exact ports of HSD constant, linear, and Hermite/slope behavior.
assert animations._value([
    {'frame': 0, 'value': 2, 'tangent': 0, 'interpolation': 'HSD_A_OP_LIN'},
    {'frame': 10, 'value': 12, 'tangent': 0, 'interpolation': 'HSD_A_OP_LIN'}], 5) == 7
assert animations._value([
    {'frame': 0, 'value': 2, 'tangent': 0, 'interpolation': 'HSD_A_OP_CON'},
    {'frame': 10, 'value': 12, 'tangent': 0, 'interpolation': 'HSD_A_OP_CON'}], 5) == 2

bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

with tempfile.TemporaryDirectory(prefix='mme-animation-blender-') as temp:
    temp = Path(temp)
    directory = temp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrGb.dat', '--session', directory)
    scene.import_session(bpy.context, directory)
    current = bpy.context.scene
    actions = [action for action in bpy.data.actions
               if action.get('mme_session_id') == current.mme_session_id]
    assert len(actions) == 11
    assert current.frame_start == 1
    assert current.frame_end == max(math.ceil(action['mme_end_frame']) + 1 for action in actions)
    assert all(action.get('mme_role') == 'jobj-animation' and action.use_fake_user
               for action in actions)

    armatures = [obj for obj in current.objects if obj.get('mme_role') == 'jobj-armature'
                 and animations.actions(obj)]
    assert armatures
    multi = next(obj for obj in armatures if len(animations.actions(obj)) == 3)
    assert [action['mme_animation_slot'] for action in animations.actions(multi)] == [0, 1, 2]
    bpy.context.view_layer.objects.active = multi
    multi.select_set(True)
    first = animations.active_action(multi)
    assert first is animations.actions(multi)[0]
    animations.cycle(bpy.context, 1)
    assert animations.active_action(multi) is animations.actions(multi)[1]
    animations.cycle(bpy.context, -1)
    assert animations.active_action(multi) is first

    # GrGb's seagulls are enveloped meshes. Their bind-space object transform
    # must remain fixed while the armature supplies all animated deformation.
    stage = read(directory / 'stage.json')
    group_entry = next(entry for entry in stage['modelGroups'] if entry['index'] == 2)
    bird_group = read(directory / group_entry['file'])
    bird_animation = bird_group['jointAnimations'][0]
    bird_payload = read((directory / group_entry['file']).parent / bird_group['meshes'][29])
    bird = next(obj for obj in current.objects if obj.get('mme_id') == bird_payload['id'])
    assert bird.get('mme_enveloped') and bird.name.endswith('POBJ 000.029')
    worst_skin_error = 0
    for source_frame in (0, 100, 200, 400, 600, 900, 1199):
        animated_group = copy.deepcopy(bird_group)
        animated_joints = {joint['id']: joint for joint in animated_group['joints']}
        for node in bird_animation['nodes']:
            node_frame = source_frame
            if node['loop'] and node['endFrame'] > 0:
                node_frame %= node['endFrame']
            for track in node['tracks']:
                field, axis = track['channel'].split('.')
                animated_joints[node['jobjId']][field][axis] = animations._value(track['keys'], node_frame)
        nodes, joints, world = transforms.joint_matrices([animated_group])
        expected_positions, owner_world = transforms.mesh_pose(bird_payload, nodes, joints, world)
        current.frame_set(source_frame + 1)
        bpy.context.view_layer.update()
        evaluated = bird.evaluated_get(bpy.context.evaluated_depsgraph_get())
        evaluated_mesh = evaluated.to_mesh()
        try:
            for vertex, expected_position in zip(evaluated_mesh.vertices, expected_positions):
                actual = evaluated.matrix_world @ vertex.co
                expected = transforms.AXES @ (owner_world @ expected_position)
                worst_skin_error = max(worst_skin_error, (actual - expected).length)
        finally:
            evaluated.to_mesh_clear()
    assert worst_skin_error < 2e-2, worst_skin_error

    # Find a source track that visibly changes and verify the corresponding
    # control bone follows it when Blender's timeline advances.
    moving = None
    for armature in armatures:
        action = animations.active_action(armature)
        group = read(directory / armature['mme_animation_source'])
        animation = next(item for item in group['jointAnimations']
                         if item['slot'] == action['mme_animation_slot'])
        candidates = [0, animation['endFrame'] * 0.25, animation['endFrame'] * 0.5,
                      animation['endFrame'] * 0.75, animation['endFrame']]
        for node in animation['nodes']:
            for track in node['tracks']:
                values = [animations._value(track['keys'], frame) for frame in candidates]
                differences = [abs(value - values[0]) for value in values]
                if max(differences) > 1e-4:
                    moving = armature, node['jobjId'], candidates[differences.index(max(differences))]
                    break
            if moving:
                break
        if moving:
            break
    assert moving
    armature, jobj_id, source_frame = moving
    bone = next(item for item in armature.pose.bones if item.get('mme_id') == jobj_id)
    current.frame_set(1)
    bpy.context.view_layer.update()
    initial = bone.matrix_basis.copy()
    current.frame_set(round(source_frame) + 1)
    bpy.context.view_layer.update()
    assert max(abs(bone.matrix_basis[i][j] - initial[i][j])
               for i in range(4) for j in range(4)) > 1e-5

    # Timeline playback is preview state and does not turn into a source edit.
    assert scene.prepare(current)[1] is None
    original_name = first.name
    first.name = original_name + ' changed'
    rejects(lambda: scene.prepare(current), 'protected')
    first.name = original_name
    assert scene.prepare(current)[1] is None
    armature_id = armature['mme_id']
    saved = temp / 'animations.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    current = bpy.context.scene
    armature = next(obj for obj in current.objects
                    if obj.get('mme_id') == armature_id)
    bone = next(item for item in armature.pose.bones if item.get('mme_id') == jobj_id)
    current.frame_set(1)
    bpy.context.view_layer.update()
    initial = bone.matrix_basis.copy()
    current.frame_set(round(source_frame) + 1)
    bpy.context.view_layer.update()
    assert max(abs(bone.matrix_basis[i][j] - initial[i][j])
               for i in range(4) for j in range(4)) > 1e-5
    assert scene.prepare(current)[1] is None
    scene.apply(current, CLI, 'dotnet', temp / 'unchanged.dat')
    assert (temp / 'unchanged.dat').read_bytes() == (CORPUS / 'GrGb.dat').read_bytes()

    # Native bone F-curves are editable. Change one imported location key,
    # export it, then re-extract the DAT and compare the encoded HSD track.
    stage = read(directory / 'stage.json')
    groups = [read(directory / entry['file']) for entry in stage['modelGroups']]
    candidates = [(obj, action) for obj in current.objects if obj.type == 'ARMATURE'
                  for action in animations.actions(obj)
                  if json.loads(action.get('mme_fcurve_jobj_ids', '[]'))]
    edit_armature, edit_action = min(candidates, key=lambda item: item[1]['mme_end_frame'])
    edit_armature.animation_data.action = edit_action
    edit_id = json.loads(edit_action['mme_fcurve_jobj_ids'])[0]
    edit_bone = next(bone for bone in edit_armature.pose.bones if bone.get('mme_id') == edit_id)
    edit_curve = next(curve for curve in animations.fcurves(edit_action)
                      if curve.data_path == edit_bone.path_from_id('location') and curve.array_index == 0)
    outside = edit_curve.keyframe_points.insert(edit_action['mme_end_frame'] + 2,
                                                edit_curve.keyframe_points[-1].co.y)
    rejects(lambda: animations.edits(current, stage, groups), 'existing animation duration')
    edit_curve.keyframe_points.remove(outside)
    edit_curve.keyframe_points[-1].co.y += 1.25
    edit_curve.update()
    serialized = animations.edits(current, stage, groups)
    assert serialized and len(serialized['nodes']) == 1
    modified = temp / 'animation-edited.dat'
    result = scene.apply(current, CLI, 'dotnet', modified)
    assert result['animationChanged'] and modified.read_bytes() != (CORPUS / 'GrGb.dat').read_bytes()
    exported_session = temp / 'animation-edited-session'
    run(CLI, 'dotnet', 'extract', modified, '--session', exported_session)
    requested = next(node for node in serialized['nodes']
                     if node['groupIndex'] == edit_armature['mme_group_index']
                     and node['slot'] == edit_action['mme_animation_slot'] and node['jobjId'] == edit_id)
    exported_group = read(exported_session / f"models/group-{requested['groupIndex']:03d}/group.json")
    exported_set = next(item for item in exported_group['jointAnimations']
                        if item['slot'] == requested['slot'])
    source_group = groups[requested['groupIndex']]
    source_index = next(item['index'] for item in source_group['nodes']
                        if item['id'] == requested['jobjId'])
    exported_id = next(item['id'] for item in exported_group['nodes']
                       if item['kind'].endswith('jobj') and item['index'] == source_index)
    exported_node = next(item for item in exported_set['nodes'] if item['jobjId'] == exported_id)
    requested_tx = next(track for track in requested['tracks'] if track['channel'] == 'translation.x')
    exported_tx = next(track for track in exported_node['tracks'] if track['channel'] == 'translation.x')
    assert abs(requested_tx['keys'][-1]['value'] - exported_tx['keys'][-1]['value']) < .002

    # GrNLa Group 003 has no serialized AOBJ loop bit. Its map-group flag byte
    # makes slot 0 loop at runtime, which the preview must reproduce.
    loop_directory = temp / 'loop-session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', loop_directory)
    bpy.context.window.scene = bpy.data.scenes.new('Runtime Loop Flag')
    scene.import_session(bpy.context, loop_directory)
    loop_scene = bpy.context.scene
    loop_armature = next(obj for obj in loop_scene.objects
                         if obj.get('mme_role') == 'jobj-armature'
                         and obj.get('mme_group_index') == 3)
    loop_action = animations.active_action(loop_armature)
    assert loop_action and loop_action['mme_loop']
    loop_group = read(loop_directory / loop_armature['mme_animation_source'])
    loop_set = loop_group['jointAnimations'][0]
    assert loop_set['groupFlag'] and loop_set['loop'] and loop_set['endFrame'] == 600
    loop_node = loop_set['nodes'][0]
    loop_bone = next(bone for bone in loop_armature.pose.bones
                     if bone.get('mme_id') == loop_node['jobjId'])
    matrices = []
    for frame in (1, 301, 601, 901):
        loop_scene.frame_set(frame)
        bpy.context.view_layer.update()
        matrices.append(loop_bone.matrix_basis.copy())
    difference = lambda left, right: max(abs(left[i][j] - right[i][j])
                                         for i in range(4) for j in range(4))
    assert difference(matrices[0], matrices[1]) > 0.1
    assert difference(matrices[0], matrices[2]) < 1e-5
    assert difference(matrices[1], matrices[3]) < 1e-5

print(f'BLENDER_ANIMATIONS_OK: native editable Actions, DAT curve export, HSD playback, runtime loops, seagull skinning ({worst_skin_error:.6g}), switching, guards, no-op export')
