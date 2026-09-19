"""Regression: assigning Ceiling must flip endpoints, not just the category."""
import os
from pathlib import Path
import sys
import tempfile
import bpy
import bmesh

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, collision
from melee_map_editor.protocol import read, run, StageError
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)
with tempfile.TemporaryDirectory(prefix='mme-ceiling-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', tmp / 'session')
    obj = scene.import_session(bpy.context, tmp / 'session')
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    line = bm.edges.layers.int['mme_line']
    # Isolate one horizontal platform to test category conversion independently
    # of the closed stage boundary's adjacency constraints.
    bmesh.ops.delete(bm, geom=[e for e in bm.edges if e[line] != 2], context='EDGES')
    bm.edges.ensure_lookup_table()
    edge = bm.edges[0]
    edge.select_set(True)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)
    source = read(tmp / 'session/collision/collision.json')
    before = collision.serialize(obj, source)
    bpy.context.scene.mme_collision_type = 'ceiling'
    assert bpy.ops.mme.assign_collision(property='type') == {'FINISHED'}
    after = collision.serialize(obj, source)
    assert before['vertices'] == after['vertices']
    assert after['lines'][0]['vertex0Id'] == before['lines'][0]['vertex1Id']
    assert after['lines'][0]['vertex1Id'] == before['lines'][0]['vertex0Id']
    assert after['lines'][0]['lowFlags'] == before['lines'][0]['lowFlags']
    scene.apply(bpy.context.scene, CLI, 'dotnet', tmp / 'ceiling.dat')
    run(CLI, 'dotnet', 'extract', tmp / 'ceiling.dat', '--session', tmp / 'output')
    exported = read(tmp / 'output/collision/collision.json')
    assert len(exported['lines']) == 1 and exported['lines'][0]['category'] == 'ceiling'
    positions = {v['id']: v['position'] for v in exported['vertices']}
    ceiling = exported['lines'][0]
    assert positions[ceiling['vertex0Id']]['x'] > positions[ceiling['vertex1Id']]['x']
    # Reassigning is idempotent; previous bad Ceiling assignments are repaired.
    assert bpy.ops.mme.assign_collision(property='type') == {'FINISHED'}
    assert collision.serialize(obj, source) == after
    a, b = bm.edges.layers.int['mme_start'], bm.edges.layers.int['mme_end']
    edge[a], edge[b] = edge[b], edge[a]
    try:
        scene.validate(bpy.context.scene, CLI, 'dotnet')
    except StageError as exc:
        assert 'COLLISION_FACING' in str(exc)
    else:
        raise AssertionError('Wrong-facing ceiling was accepted')
    bpy.ops.mme.assign_collision(property='type')
    assert collision.serialize(obj, source) == after
    # Floor restoration reverses back, without moving geometry or changing flags.
    bpy.context.scene.mme_collision_type = 'floor'
    bpy.ops.mme.assign_collision(property='type')
    assert collision.serialize(obj, source) == before
    scene.validate(bpy.context.scene, CLI, 'dotnet')
print('BLENDER_CEILING_OK: type assignment, endpoint order, repair, flags, DAT reload, export guard')
