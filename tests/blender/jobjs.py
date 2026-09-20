"""Run with Blender 4.5: --background --factory-startup --python-exit-code 1 --python tests/blender/jobjs.py"""
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
import melee_map_editor
from melee_map_editor import jobjs, scene
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))

bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

def world(armature, bone):
    return armature.matrix_world @ bone.matrix


with tempfile.TemporaryDirectory(prefix='mme-jobj-blender-') as tmp:
    tmp = Path(tmp)
    directory = tmp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    scene.import_session(bpy.context, directory)
    s = bpy.context.scene
    stage = read(directory / 'stage.json')
    groups = [read(directory / entry['file']) for entry in stage['modelGroups']]
    nodes = {node['id']: node for group in groups for node in group['nodes']}
    joints = {joint['id']: joint for group in groups for joint in group['joints']}
    parents = {nodes[key]['ownerId'] for key in joints}
    imported_armatures = jobjs.armatures(s)
    assert len(imported_armatures) == sum(bool(group['joints']) for group in groups)
    assert sum(sum(bone.get('mme_role') == 'jobj' for bone in armature.pose.bones)
               for armature in imported_armatures) == len(joints)
    assert not [obj for obj in s.objects if str(obj.get('mme_role', '')).endswith('jobj')]

    info = next(item for item in jobjs.targets(s) if item['id'] not in parents
                and all(joints[item['id']]['scale'][axis] > 0 for axis in 'xyz'))
    armature, bone = jobjs.target_bone(s, info)
    before_world = world(armature, bone).copy()
    bone.location += Vector((3, -2, 5))
    bone.rotation_euler.rotate_axis('X', 0.12)
    bone.rotation_euler.rotate_axis('Y', -0.08)
    bone.rotation_euler.rotate_axis('Z', 0.04)
    bone.scale = Vector((bone.scale.x * 1.1, bone.scale.y * 0.9, bone.scale.z * 1.2))

    parent_info = next(item for item in jobjs.targets(s) if item['id'] in parents
                       and all(joints[item['id']]['scale'][axis] > 0 for axis in 'xyz')
                       and any(candidate.type == 'MESH'
                           and json.loads(candidate.get('mme_jobj_chain', '[]'))[:1] == [item['id']]
                           for candidate in s.objects))
    parent_armature, parent_bone = jobjs.target_bone(s, parent_info)
    bound_model = next(candidate for candidate in s.objects
        if candidate.type == 'MESH' and json.loads(candidate.get('mme_jobj_chain', '[]'))[:1] == [parent_info['id']])
    parent_bone.location += Vector((-4, 1, 2))
    parent_bone.rotation_euler.rotate_axis('Z', -0.06)
    parent_bone.scale *= 1.15
    bpy.context.view_layer.update()
    assert world(armature, bone) != before_world
    payload = jobjs.edits(s, stage, groups)
    assert payload and len(payload['jobjs']) == 2

    # A selected rigid model resolves to its owning JOBJ bone and enters Pose Mode.
    bpy.ops.object.mode_set(mode='OBJECT') if bpy.context.mode != 'OBJECT' else None
    bpy.ops.object.select_all(action='DESELECT')
    bound_model.select_set(True)
    bpy.context.view_layer.objects.active = bound_model
    assert bpy.ops.mme.edit_jobj() == {'FINISHED'}
    assert bpy.context.mode == 'POSE'
    assert bpy.context.active_pose_bone.get('mme_id') == parent_info['id']
    bpy.ops.object.mode_set(mode='OBJECT')

    scene.apply(s, CLI, 'dotnet', tmp / 'edited.dat')
    assert not (directory / 'edits/jobjs.json').exists()
    edited_world = world(armature, bone).copy()
    bound_world = bound_model.matrix_world.copy()

    output_session = tmp / 'output-session'
    run(CLI, 'dotnet', 'extract', tmp / 'edited.dat', '--session', output_session)
    output_stage = read(output_session / 'stage.json')
    for edit in payload['jobjs']:
        expected_info = next(item for item in (info, parent_info) if item['id'] == edit['id'])
        joint_id = next(item['id'] for item in output_stage['editableJobjs']
            if item['groupIndex'] == expected_info['groupIndex'] and item['jobjIndex'] == expected_info['jobjIndex'])
        written_joint = next(joint for group_entry in output_stage['modelGroups']
            for joint in read(output_session / group_entry['file'])['joints'] if joint['id'] == joint_id)
        for field in ('rotation', 'scale', 'translation'):
            for axis in 'xyz':
                assert math.isclose(written_joint[field][axis], edit[field][axis], rel_tol=1e-6, abs_tol=1e-6)

    bpy.context.window.scene = bpy.data.scenes.new('Reimported JOBJ')
    scene.import_session(bpy.context, output_session)
    reimported_scene = bpy.context.scene
    reimported_info = next(item for item in jobjs.targets(reimported_scene)
        if item['groupIndex'] == info['groupIndex'] and item['jobjIndex'] == info['jobjIndex'])
    reimported_armature, reimported_bone = jobjs.target_bone(reimported_scene, reimported_info)
    assert all(math.isclose(world(reimported_armature, reimported_bone)[i][j], edited_world[i][j], rel_tol=1e-5, abs_tol=1e-4)
               for i in range(4) for j in range(4))
    reimported_parent_info = next(item for item in jobjs.targets(reimported_scene)
        if item['groupIndex'] == parent_info['groupIndex'] and item['jobjIndex'] == parent_info['jobjIndex'])
    reimported_model = next(candidate for candidate in reimported_scene.objects
        if candidate.type == 'MESH' and json.loads(candidate.get('mme_jobj_chain', '[]'))[:1] == [reimported_parent_info['id']]
        and candidate.get('mme_source_index') == bound_model.get('mme_source_index'))
    assert all(math.isclose(reimported_model.matrix_world[i][j], bound_world[i][j], rel_tol=1e-5, abs_tol=1e-4)
               for i in range(4) for j in range(4))

print('BLENDER_JOBJS_OK: armature hierarchy, rigid binding, Pose Mode SRT export, and reimport')
