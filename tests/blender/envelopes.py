"""Run with Blender: --background --factory-startup --python-exit-code 1 --python tests/blender/envelopes.py"""
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
from melee_map_editor import animations, jobjs, scene, transforms
from melee_map_editor.protocol import StageError, read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
STAGE = os.environ.get('MME_ENVELOPE_STAGE', 'GrBb.dat')


def rejects(action, message):
    try:
        action()
    except StageError as exc:
        assert message.lower() in str(exc).lower(), (message, str(exc))
    else:
        raise AssertionError('Expected rejection: ' + message)


bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

with tempfile.TemporaryDirectory(prefix='mme-envelope-blender-') as temp:
    temp = Path(temp)
    directory = temp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / STAGE, '--session', directory)
    scene.import_session(bpy.context, directory)
    current = bpy.context.scene
    # This test compares the source bind pose. Joint-animation playback has a
    # separate test and is explicitly disabled here.
    original_actions = {}
    for armature in jobjs.armatures(current):
        if armature.animation_data:
            original_actions[armature] = armature.animation_data.action
            armature.animation_data.action = None
    animations.apply(current)
    stage = read(directory / 'stage.json')
    groups = [read(directory / entry['file']) for entry in stage['modelGroups']]
    nodes, joints, world = transforms.joint_matrices(groups)
    payloads = [(read((directory / entry['file']).parent / filename), group)
                for entry, group in zip(stage['modelGroups'], groups)
                for filename in group['meshes']]
    enveloped = [(payload, group) for payload, group in payloads if payload.get('envelopes')]
    assert enveloped
    objects = {obj.get('mme_id'): obj for obj in current.objects}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    found_blend = False
    worst_error = 0
    worst_info = None

    found_source_normals = False
    for payload, _ in enveloped:
        obj = objects[payload['id']]
        assert obj.get('mme_enveloped')
        assert len(obj.modifiers) == 1 and obj.modifiers[0].type == 'ARMATURE'
        assert obj.modifiers[0].object.get('mme_role') == 'jobj-armature'
        expected_positions, owner_world = transforms.mesh_pose(payload, nodes, joints, world)
        evaluated = obj.evaluated_get(depsgraph)
        evaluated_mesh = evaluated.to_mesh()
        try:
            assert len(evaluated_mesh.vertices) == len(expected_positions)
            if payload.get('normals'):
                found_source_normals = True
                assert all(polygon.use_smooth for polygon in obj.data.polygons)
                assert len(evaluated_mesh.corner_normals) == len(evaluated_mesh.loops)
            for vertex, expected_position in zip(evaluated_mesh.vertices, expected_positions):
                actual = evaluated.matrix_world @ vertex.co
                expected = transforms.AXES @ (owner_world @ expected_position)
                error = (actual - expected).length
                if error > worst_error:
                    worst_error = error
                    worst_info = (obj.name, vertex.index, tuple(actual), tuple(expected))
        finally:
            evaluated.to_mesh_clear()
        for vertex_index, envelope_index in enumerate(payload['envelopeIndices']):
            envelope = payload['envelopes'][envelope_index]
            if len([item for item in envelope if item['weight'] > 1e-6]) > 1:
                found_blend = True
                assignments = obj.data.vertices[vertex_index].groups
                assert len(assignments) > 1
                assert math.isclose(sum(item.weight for item in assignments),
                                    sum(item['weight'] for item in envelope), abs_tol=1e-5)
                break
    assert found_blend
    assert found_source_normals
    assert worst_error < 2e-2, (STAGE, worst_error, worst_info)

    # A source control bone drives its generated deform bone and weighted mesh.
    payload, _ = next((payload, group) for payload, group in enveloped
                      if any(len(envelope) > 1 for envelope in payload['envelopes']))
    obj = objects[payload['id']]
    vertex_index = next(index for index, envelope_index in enumerate(payload['envelopeIndices'])
                        if len(payload['envelopes'][envelope_index]) > 1)
    influence = payload['envelopes'][payload['envelopeIndices'][vertex_index]][0]['jobjId']
    armature, control = jobjs.bone_by_id(current, influence)
    deform = next(bone for bone in armature.pose.bones
                  if bone.get('mme_source_jobj_id') == influence)
    control_location = control.location.copy()
    control_rotation = control.rotation_euler.copy()
    control_scale = control.scale.copy()
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh()
    try:
        before = evaluated.matrix_world @ evaluated_mesh.vertices[vertex_index].co
    finally:
        evaluated.to_mesh_clear()
    control.location += Vector((2, 0, 0))
    bpy.context.view_layer.update()
    assert max(abs(control.matrix[i][j] - deform.matrix[i][j])
               for i in range(4) for j in range(4)) < 1e-5
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh()
    try:
        after = evaluated.matrix_world @ evaluated_mesh.vertices[vertex_index].co
    finally:
        evaluated.to_mesh_clear()
    assert (after - before).length > 1e-3
    control.location = control_location
    control.rotation_euler = control_rotation
    control.scale = control_scale
    for armature, action in original_actions.items():
        armature.animation_data.action = action
    animations.apply(current)
    bpy.context.view_layer.update()
    assert scene.prepare(current)[1] is None

    # Source envelope weights and generated binding machinery remain protected.
    group = obj.vertex_groups[0]
    vertex_index = next(vertex.index for vertex in obj.data.vertices
                        if any(item.group == group.index for item in vertex.groups))
    original = group.weight(vertex_index)
    group.add([vertex_index], original * 0.5, 'REPLACE')
    rejects(lambda: scene.prepare(current), 'protected')
    group.add([vertex_index], original, 'REPLACE')
    assert scene.prepare(current)[1] is None
    scene.apply(current, CLI, 'dotnet', temp / 'unchanged.dat')
    assert (temp / 'unchanged.dat').read_bytes() == (CORPUS / STAGE).read_bytes()

print(f'BLENDER_ENVELOPES_OK ({STAGE}): HSD weights, inverse binds, deform controls, guards, no-op export; worst error {worst_error:.6g}')
