"""Multiple rigid targets: native joins, batch edits, reload and protected objects."""
import os
from pathlib import Path
import sys
import tempfile
import bpy

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
    except StageError:
        pass
    else:
        raise AssertionError('Expected protected edit to fail')


with tempfile.TemporaryDirectory(prefix='mme-multi-') as tmp:
    tmp = Path(tmp)
    directory = tmp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    stage = read(directory / 'stage.json')
    scene.import_session(bpy.context, directory)
    s = bpy.context.scene
    infos = modeling.targets(s)
    assert len(infos) > 1
    assert infos == stage['editableMeshes']
    scene.apply(s, CLI, 'dotnet', tmp / 'unchanged.dat')
    assert (tmp / 'unchanged.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()
    full_infos = [i for i in infos if modeling.allows(i, 'topologyReplacement')]
    chosen = full_infos[:2]
    for index, info in enumerate(chosen):
        obj = modeling.target_object(s, info)
        bpy.ops.object.select_all(action='DESELECT')
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        assert bpy.ops.mme.edit_model() == {'FINISHED'}
        assert bpy.context.edit_object == obj
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.mesh.primitive_cube_add(location=(index * 20, 0, 40))
        cube = bpy.context.object
        cube.data.materials.append(bpy.data.materials.new('Joined Material'))
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.join()
    # A third model keeps its topology and must retain its original appearance.
    vertex_info = full_infos[2]
    vertex_target = modeling.target_object(s, vertex_info)
    vertex_target.data.vertices[0].co.z += 3
    changed_ids = {i['id'] for i in chosen} | {vertex_info['id']}
    # Multi-object Edit Mode must serialize every changed mesh independently.
    bpy.ops.object.select_all(action='DESELECT')
    for info in chosen:
        modeling.target_object(s, info).select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    edits = modeling.edits(s, stage)
    assert {m['id'] for m in edits['meshes']} == changed_ids
    collision = scene.collision_objects(s)[0]
    collision.data.vertices[0].co.z += 1
    result = scene.apply(s, CLI, 'dotnet', tmp / 'multi.dat')
    assert result['modelChanged'] and result['collisionChanged']
    assert not list((directory / 'edits').iterdir())
    run(CLI, 'dotnet', 'extract', tmp / 'multi.dat', '--session', tmp / 'exported')
    # Match by stable source offset across extractions (session IDs are opaque).
    exported = read(tmp / 'exported/stage.json')
    for group, new_group in zip(stage['modelGroups'], exported['modelGroups']):
        old_g = read(directory / group['file'])
        new_g = read(tmp / 'exported' / new_group['file'])
        new_meshes = {m['sourceOffset']: m for name in new_g['meshes']
                      for m in [read((tmp / 'exported' / new_group['file']).parent / name)]}
        for name in old_g['meshes']:
            old = read((directory / group['file']).parent / name)
            new = new_meshes[old['sourceOffset']]
            if old['id'] == vertex_info['id']:
                assert new['positions'] != old['positions']
                assert len(new['positions']) == len(old['positions'])
                for key in ('normals', 'triangleIndices', 'pobjFlags'):
                    assert old[key] == new[key]
            elif old['id'] not in changed_ids:
                for key in ('positions', 'normals', 'triangleIndices', 'pobjFlags'):
                    assert old[key] == new[key]
            else:
                assert new['positions'] != old['positions']
                assert new['pobjFlags'] & 0xC000 == 0x4000
    bpy.ops.object.mode_set(mode='OBJECT')
    obj = modeling.target_object(s, chosen[1])
    obj.location.x += 1
    scene.prepare(s)
    obj.location.x -= 1
    protected = next(o for o in s.objects if o.get('mme_role') == 'pobj'
                     and o.type == 'MESH' and o.get('mme_id') not in modeling.target_ids(s))
    assert protected.get('mme_read_only_reason')
    original = protected.data.vertices[0].co.copy()
    protected.data.vertices[0].co.x += 1
    rejects(lambda: scene.prepare(s))
    protected.data.vertices[0].co = original
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'multi.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'multi.blend'))
    s = bpy.context.scene
    assert modeling.edits(s, stage) == edits
    scene.validate(s, CLI, 'dotnet')
    # Blender's normal object deletion removes an editable POBJ on export.
    deleted = chosen[1]
    bpy.data.objects.remove(modeling.target_object(s, deleted), do_unlink=True)
    deletion_edits = modeling.edits(s, stage)
    assert deletion_edits['deletedIds'] == [deleted['id']]
    scene.apply(s, CLI, 'dotnet', tmp / 'deleted.dat')
    run(CLI, 'dotnet', 'extract', tmp / 'deleted.dat', '--session', tmp / 'deleted-session')
    deleted_stage = read(tmp / 'deleted-session/stage.json')
    deleted_offsets = {mesh['sourceOffset']
        for group in deleted_stage['modelGroups']
        for mesh in read(tmp / 'deleted-session' / group['file'])['nodes']
        if mesh['kind'] == 'pobj'}
    source_group = read(directory / stage['modelGroups'][deleted['groupIndex']]['file'])
    deleted_offset = next(node['sourceOffset'] for node in source_group['nodes']
                          if node['id'] == deleted['id'])
    assert deleted_offset not in deleted_offsets
print('BLENDER_MULTI_MODELS_OK: batch joins, multi-object edit mode, deletion, collision, guards, save/load')
