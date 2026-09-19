"""Animated-material rigid meshes preserve appearance and reject replacement edits."""
import os
from pathlib import Path
import sys
import tempfile
import bpy
import bmesh

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, modeling
from melee_map_editor.protocol import read, run, StageError
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)


def rejects(action):
    try:
        action()
    except StageError as error:
        assert 'animated materials' in str(error), str(error)
    else:
        raise AssertionError('Expected position-only restriction')


with tempfile.TemporaryDirectory(prefix='mme-animated-') as tmp:
    tmp = Path(tmp)
    directory = tmp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    stage = read(directory / 'stage.json')
    scene.import_session(bpy.context, directory)
    s = bpy.context.scene
    infos = [i for i in modeling.targets(s) if i.get('positionsOnly')]
    assert len(infos) == 45 and len(modeling.targets(s)) == 90
    previews = {m['id']: m for m in stage['modelPreviews']}
    assert {i['id'] for i in infos} <= set(previews)
    textured = 0
    for info in infos:
        material = modeling.target_object(s, info).active_material
        assert material.get('mme_preview_model_id') == info['id']
        assert not material.get('mme_model_material_id')
        assert material.use_nodes
        preview = previews[info['id']]['preview']
        if preview['texture']:
            textured += 1
            node = material.node_tree.nodes['Stage Texture']
            assert node.image.packed_file
            assert list(node.image.size) == [preview['texture']['width'], preview['texture']['height']]
    assert textured > 0
    print(f'ANIMATED BASE PREVIEWS: {len(previews)} materials, {textured} textures')
    scene.apply(s, CLI, 'dotnet', tmp / 'unchanged.dat')
    assert (tmp / 'unchanged.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()
    for info in infos:
        modeling.target_object(s, info).data.vertices[0].co.z += 2
    obj = modeling.target_object(s, infos[0])
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    assert bpy.ops.mme.edit_model() == {'FINISHED'}
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.x += 1
    bmesh.update_edit_mesh(obj.data)
    edits = modeling.edits(s, stage)
    assert len(edits['meshes']) == 45
    assert all(set(m) == {'id', 'positions', 'triangleIndices'} for m in edits['meshes'])
    scene.apply(s, CLI, 'dotnet', tmp / 'moved.dat')
    # Native topology edits are rejected in Edit Mode.
    new_vertex = bm.verts.new((0, 0, 0))
    bmesh.update_edit_mesh(obj.data)
    rejects(lambda: modeling.edits(s, stage))
    bm.verts.remove(new_vertex)
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode='OBJECT')
    uv_obj = next(modeling.target_object(s, i) for i in infos if modeling.target_object(s, i).data.uv_layers.active)
    uv = uv_obj.data.uv_layers.active.data[0]
    saved_uv = uv.uv.copy()
    uv.uv.x += .1
    rejects(lambda: modeling.edits(s, stage))
    uv.uv = saved_uv
    original = obj.data.materials[0]
    obj.data.materials[0] = bpy.data.materials.new('Replacement')
    rejects(lambda: modeling.edits(s, stage))
    obj.data.materials[0] = original
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'saved.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'saved.blend'))
    scene.apply(bpy.context.scene, CLI, 'dotnet', tmp / 'reopened.dat')
    assert (tmp / 'moved.dat').read_bytes() == (tmp / 'reopened.dat').read_bytes()
print('ANIMATED MODELS PASS')
