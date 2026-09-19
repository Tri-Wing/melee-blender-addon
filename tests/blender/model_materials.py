"""Native UV edits and joined shapes using a supported stage material."""
import os
from pathlib import Path
import sys
import tempfile
import bpy
import bmesh

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, modeling, surface
from melee_map_editor.protocol import read, run, StageError
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)


def rejects(action, text):
    try:
        action()
    except StageError as exc:
        assert text.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError('Expected ' + text)


with tempfile.TemporaryDirectory(prefix='mme-materials-') as tmp:
    tmp = Path(tmp)
    directory = tmp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    stage = read(directory / 'stage.json')
    scene.import_session(bpy.context, directory)
    s = bpy.context.scene
    assert modeling.edits(s, stage) is None
    scene.apply(s, CLI, 'dotnet', tmp / 'noop.dat')
    assert (tmp / 'noop.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()
    entry = next(m for m in stage['modelMaterials'] if m['usesUv'])
    info = next(m for m in stage['editableMeshes'] if m['id'] == entry['id'])
    obj = modeling.target_object(s, info)
    source = read(directory / info['file'])
    assert obj.data.uv_layers.active
    assert surface.material_id(obj.data.materials[0]) == entry['id']
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    # Material-only assignment must be dirty without changing a single vertex.
    solid = next(m for m in stage['modelMaterials'] if not m['usesUv'])
    assert bpy.ops.mme.model_material(material_id=solid['id']) == {'FINISHED'}
    assert modeling.edits(s, stage)['meshes'][0]['sourceMaterialId'] == solid['id']
    scene.apply(s, CLI, 'dotnet', tmp / 'material-only.dat')
    assert bpy.ops.mme.model_material(material_id=entry['id']) == {'FINISHED'}
    assert modeling.edits(s, stage) is None
    bpy.ops.object.mode_set(mode='EDIT')
    assert modeling.edits(s, stage) is None
    bm = bmesh.from_edit_mesh(obj.data)
    uv = bm.loops.layers.uv.active
    for f in bm.faces:
        for loop in f.loops:
            loop[uv].uv.x += 0.125
    bmesh.update_edit_mesh(obj.data)
    edits = modeling.edits(s, stage)
    assert len(edits['meshes']) == 1
    assert edits['meshes'][0]['sourceMaterialId'] == entry['id']
    assert scene.apply(s, CLI, 'dotnet', tmp / 'uv.dat')['modelChanged']
    run(CLI, 'dotnet', 'extract', tmp / 'uv.dat', '--session', tmp / 'uv')
    # Session IDs change; identify the exported mesh by original source offset.
    def find_mesh(folder):
        manifest = read(folder / 'stage.json')
        group = manifest['modelGroups'][info['groupIndex']]
        for name in read(folder / group['file'])['meshes']:
            mesh = read((folder / group['file']).parent / name)
            if mesh['sourceOffset'] == source['sourceOffset']:
                return mesh
        raise AssertionError('Missing exported mesh')
    changed = find_mesh(tmp / 'uv')
    assert changed['pobjFlags'] == source['pobjFlags']
    assert changed['texCoords0']
    # Native Join brings another UV map and an unsupported material slot.
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 40))
    cube = bpy.context.object
    cube.data.materials.append(bpy.data.materials.new('Cube Material'))
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.join()
    rejects(lambda: scene.prepare(s), 'one stage material')
    bpy.ops.object.mode_set(mode='EDIT')
    assert bpy.ops.mme.model_material(material_id=entry['id']) == {'FINISHED'}
    obj.update_from_editmode()
    assert len(obj.data.materials) == 1
    assert all(p.material_index == 0 for p in obj.data.polygons)
    bpy.ops.object.mode_set(mode='EDIT')
    assert scene.apply(s, CLI, 'dotnet', tmp / 'joined.dat')['modelChanged']
    bpy.ops.object.mode_set(mode='OBJECT')
    run(CLI, 'dotnet', 'extract', tmp / 'joined.dat', '--session', tmp / 'joined')
    joined = find_mesh(tmp / 'joined')
    assert joined['texCoords0'] and joined['pobjFlags'] & 0xC000 == 0x4000
    edits = modeling.edits(s, stage)
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'materials.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'materials.blend'))
    s = bpy.context.scene
    assert modeling.edits(s, stage) == edits
    obj = modeling.target_object(s, info)
    # Missing UVs must not silently replace a textured material with grey.
    while obj.data.uv_layers:
        obj.data.uv_layers.remove(obj.data.uv_layers[0])
    saved = (tmp / 'joined.dat').read_bytes()
    rejects(lambda: scene.apply(s, CLI, 'dotnet', tmp / 'joined.dat'), 'UV map')
    assert (tmp / 'joined.dat').read_bytes() == saved
    bpy.context.view_layer.objects.active = obj
    assert bpy.ops.mme.model_material(material_id='GREY') == {'FINISHED'}
    scene.apply(s, CLI, 'dotnet', tmp / 'grey.dat')
    assert not list((directory / 'edits').iterdir())
print('BLENDER_MODEL_MATERIALS_OK: no-op, UV-only edits, native join, assignment, save/load, missing UV rejection, grey fallback')
