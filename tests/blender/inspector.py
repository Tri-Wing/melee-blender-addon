"""Inspect live edge metadata independently of assignment controls."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import bpy
import bmesh

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
import melee_map_editor as addon
from melee_map_editor import scene, inspector
from melee_map_editor.protocol import read, run, StageError
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
with tempfile.TemporaryDirectory(prefix='mme-inspector-') as directory:
    directory = Path(directory)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory / 'session')
    obj = scene.import_session(bpy.context, directory / 'session')
    original = inspector.source(directory / 'session')
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    for e in bm.edges:
        e.select_set(False)
    bm.select_history.clear()
    try:
        inspector.describe(obj, original)
    except StageError as exc:
        assert 'Select' in str(exc)
    else:
        raise AssertionError('No selection should prompt for an edge')
    first = bm.edges[0]
    first.select_set(True)
    bpy.context.scene.mme_collision_material = 15
    bpy.context.scene.mme_collision_type = 'ceiling'
    info = inspector.describe(obj, original)
    assert info['surface'] == original['lines'][0]['lowFlags'] & 255
    assert info['category'] == 'floor'  # Not the assignment dropdown's Ceiling.
    assert info['id'] == original['lines'][0]['id']
    assert bpy.ops.mme.assign_collision(property='material') == {'FINISHED'}
    info = inspector.describe(obj, original)
    assert info['surface'] == 15 and info['surfaceName'] == 'Ice'
    before_drop = info['drop']
    bpy.ops.mme.assign_collision(property='drop')
    assert inspector.describe(obj, original)['drop'] != before_drop
    bpy.context.scene.mme_collision_material = 255
    bpy.ops.mme.assign_collision(property='material')
    assert inspector.describe(obj, original)['surfaceName'] == 'Custom / Unknown'
    # Exercise both panel draw paths without requiring a GPU context.
    labels = []
    layout = SimpleNamespace(label=lambda **kw: labels.append(kw['text']), separator=lambda: None)
    panel = SimpleNamespace(layout=layout, details=addon.MME_PT_edge.details)
    addon.MME_PT_edge.draw(panel, bpy.context)
    addon.MME_PT_edge_raw.draw(panel, bpy.context)
    assert 'Surface: Custom / Unknown (255)' in labels
    assert any('Original source records' in label for label in labels)
    # Multiple selection without an active edge is not silently assigned a target.
    bm.edges[1].select_set(True)
    try:
        inspector.describe(obj, original)
    except StageError as exc:
        assert '2 edges selected' in str(exc)
    else:
        raise AssertionError('Ambiguous selection must be explicit')
    bm.select_history.add(bm.edges[1])
    assert inspector.describe(obj, original)['handle'] == 2
    assert inspector.describe(obj, original)['selected'] == 2
    bm.edges[1].select_set(False)
    bm.select_history.clear()
    # New topology has a stable identity and is explicitly marked as new.
    bpy.ops.mme.collision_topology(operation='split')
    bm = bmesh.from_edit_mesh(obj.data)
    line = bm.edges.layers.int['mme_line']
    for e in bm.edges:
        e.select_set(False)
    edge = max(bm.edges, key=lambda e: e[line])
    edge.select_set(True)
    info = inspector.describe(obj, original)
    assert info['baseline'] is None and info['handle'] == 17
    labels.clear()
    addon.MME_PT_edge.draw(panel, bpy.context)
    addon.MME_PT_edge_raw.draw(panel, bpy.context)
    assert 'New edge (no original line index)' in labels
print('BLENDER_INSPECTOR_OK: selection, live values, surfaces/flags, source records, active edge, new topology, panel draw')
