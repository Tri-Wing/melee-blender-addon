"""Serialized one-to-one collision attachments follow their current JOBJ pose."""
from pathlib import Path
import json
import os
import sys
import tempfile

import bpy
import bmesh

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

    obj = scene.import_session(bpy.context, directory)
    source = read(directory / 'collision/collision.json')
    bindings = {int(key): value for key, value in
                json.loads(obj['mme_collision_bindings']).items()}
    assert set(bindings) == {0, 1, 2, 3, 5}
    assert obj.hide_get()
    assert bpy.ops.mme.edit_collision() == {'FINISHED'}
    assert obj.mode == 'EDIT' and not obj.hide_get()
    assert bpy.ops.mme.exit_collision() == {'FINISHED'}
    assert obj.mode == 'OBJECT' and obj.hide_get()
    transforms = collision.attachment_transforms(bpy.context.scene, obj)
    assert set(transforms) == set(bindings)

    joint_attr = obj.data.attributes['mme_joint']
    edge = next(edge for edge in obj.data.edges
                if joint_attr.data[edge.index].value - 1 in transforms)
    joint_index = joint_attr.data[edge.index].value - 1
    before = collision.display_edge_points(
        bpy.context.scene, obj, edge, transforms)[0]
    jobj_id = bindings[joint_index]
    matches = [(armature, bone) for armature in bpy.context.scene.objects
               if armature.type == 'ARMATURE'
               for bone in armature.pose.bones if bone.get('mme_id') == jobj_id]
    assert len(matches) == 1
    armature, bone = matches[0]
    original = bone.matrix_basis.copy()
    bone.matrix_basis.translation.x += 7
    bpy.context.view_layer.update()
    after = collision.display_edge_points(bpy.context.scene, obj, edge)[0]
    assert (after - before).length > 1
    bone.matrix_basis = original
    bpy.context.view_layer.update()

    output = temporary / 'unchanged.dat'
    scene.apply(bpy.context.scene, CLI, 'dotnet', output)
    assert output.read_bytes() == source_path.read_bytes()

    baseline_attachments = source['attachments']
    vertex_index = edge.vertices[0]
    obj.data.vertices[vertex_index].co.z += 0.25
    bpy.context.view_layer.objects.active = obj
    obj.hide_set(False)
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    category = bm.edges.layers.int['mme_category']
    for candidate in bm.edges:
        candidate.select_set(False)
    dynamic_edge = next(candidate for candidate in bm.edges
                        if candidate[category] == collision.CATEGORIES.index('dynamic'))
    dynamic_edge.select_set(True)
    topology.edit(obj, source, 'split')
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
