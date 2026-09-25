"""Serialized one-to-one collision attachments follow their current JOBJ pose."""
from pathlib import Path
import json
import os
import sys
import tempfile

import bpy
import bmesh
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import collision, scene, topology
from melee_map_editor.protocol import read, run

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))

bpy.ops.preferences.addon_enable(module='melee_map_editor')

with tempfile.TemporaryDirectory(prefix='mme-dynamic-collision-') as temporary:
    temporary = Path(temporary)
    directory = temporary / 'session'
    source_path = CORPUS / 'GrMc.dat'
    run(CLI, 'dotnet', 'extract', source_path, '--session', directory)
    stage = read(directory / 'stage.json')
    assert stage['capabilities']['dynamicCollisionPreview']
    assert stage['capabilities']['collisionEdit']
    assert stage['capabilities']['dynamicCollisionEdit']
    assert stage['collisionBindingSummary']['oneToOnePreviewJoints'] == 5

    scene.import_session(bpy.context, directory)
    source = read(directory / 'collision/collision.json')
    objects = scene.collision_objects(bpy.context.scene)
    registry = json.loads(bpy.context.scene['mme_collision_joints'])
    bindings = {entry['index']: entry['previewJobjId'] for entry in registry
                if entry.get('previewJobjId')}
    assert set(bindings) == {0, 1, 2, 3, 5}
    assert len(objects) > 1
    bpy.ops.object.select_all(action='DESELECT')
    for candidate in objects:
        candidate.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.mode_set(mode='EDIT')
    assert all(obj.mode == 'EDIT' for obj in objects)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert all(obj.mode == 'OBJECT' for obj in objects)

    obj = next(obj for obj in objects
               if obj['mme_collision_joint'] - 1 in bindings)
    edge = obj.data.edges[0]
    joint_index = obj['mme_collision_joint'] - 1
    before = collision.display_edge_points(
        bpy.context.scene, obj, edge)[0]
    jobj_id = bindings[joint_index]
    matches = [(armature, bone) for armature in bpy.context.scene.objects
               if armature.type == 'ARMATURE'
               for bone in armature.pose.bones if bone.get('mme_id') == jobj_id]
    assert len(matches) == 1
    armature, bone = matches[0]
    original = bone.matrix_basis.copy()
    bone.matrix_basis.translation.x += 7
    bpy.context.view_layer.update()
    collision.update_component_transforms(bpy.context.scene)
    after = collision.display_edge_points(bpy.context.scene, obj, edge)[0]
    assert (after - before).length > 1
    bone.matrix_basis = original
    bpy.context.view_layer.update()
    collision.update_component_transforms(bpy.context.scene)

    # A user-authored Object Mode transform stays relative to the attachment
    # while the JOBJ pose changes, rather than being lost or baking the pose.
    managed_matrix = obj.matrix_world.copy()
    user_transform = Matrix.Translation((2, 0, 0))
    obj.matrix_world = managed_matrix @ user_transform
    collision.update_component_transforms(bpy.context.scene)
    bone.matrix_basis.translation.x += 4
    bpy.context.view_layer.update()
    collision.update_component_transforms(bpy.context.scene)
    actual_transform = collision.component_transform(bpy.context.scene, obj)
    assert all(abs(actual_transform[row][column] - user_transform[row][column]) < 0.00001
               for row in range(4) for column in range(4))
    bone.matrix_basis = original
    bpy.context.view_layer.update()
    collision.update_component_transforms(bpy.context.scene)
    obj.matrix_world = managed_matrix
    collision.update_component_transforms(bpy.context.scene)

    output = temporary / 'unchanged.dat'
    scene.apply(bpy.context.scene, CLI, 'dotnet', output)
    assert output.read_bytes() == source_path.read_bytes()

    baseline_attachments = source['attachments']
    vertex_index = edge.vertices[0]
    obj.data.vertices[vertex_index].co.z += 0.25
    dynamic_obj = next(candidate for candidate in objects
                       if any(value.value == collision.CATEGORIES.index('dynamic')
                              for value in candidate.data.attributes['mme_category'].data))
    bpy.ops.object.select_all(action='DESELECT')
    dynamic_obj.select_set(True)
    bpy.context.view_layer.objects.active = dynamic_obj
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(dynamic_obj.data)
    category = bm.edges.layers.int['mme_category']
    for candidate in bm.edges:
        candidate.select_set(False)
    dynamic_edge = next(candidate for candidate in bm.edges
                        if candidate[category] == collision.CATEGORIES.index('dynamic'))
    dynamic_edge.select_set(True)
    topology.edit(dynamic_obj, source, 'split',
                  objects=scene.collision_objects(bpy.context.scene))
    edited = temporary / 'edited.dat'
    scene.apply(bpy.context.scene, CLI, 'dotnet', edited)
    run(CLI, 'dotnet', 'validate', edited, '--json')
    reimport = temporary / 'reimport'
    run(CLI, 'dotnet', 'extract', edited, '--session', reimport)
    reimported = read(reimport / 'collision/collision.json')
    assert len(reimported['attachments']) == len(baseline_attachments)
    assert [(entry['source']['groupIndex'], entry['source']['jointIndex'],
             entry['source']['rawMiddleIndex'], entry['source']['jobjIndex'])
            for entry in reimported['attachments']] == [
                (entry['source']['groupIndex'], entry['source']['jointIndex'],
                 entry['source']['rawMiddleIndex'], entry['source']['jobjIndex'])
                for entry in baseline_attachments]
    assert reimported['ranges'][4]['count'] == source['ranges'][4]['count'] + 1

print('BLENDER_DYNAMIC_COLLISION_OK: preview, local geometry edit/split, attachment preservation')
