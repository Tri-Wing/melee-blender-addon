"""Run: blender --background --factory-startup --python-exit-code 1 --python tests/blender/smoke.py"""
import math
import os
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
import melee_map_editor as addon
from melee_map_editor import scene, collision, transforms
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


# Coordinate and nonuniform inherited scale regression (HSD_MtxSRT).
v = Vector((3, 5, 7))
assert tuple(transforms.AXES @ v) == (3, -7, 5)
assert (transforms.AXES.inverted() @ transforms.AXES @ v - v).length < 1e-6
nodes = [{'id': 'a', 'ownerId': 'g'}, {'id': 'b', 'ownerId': 'a'}]
def joint(key, rotation, scale):
    return {'id': key, 'flags': 0, 'rotation': dict(zip('xyz', rotation)),
            'scale': dict(zip('xyz', scale)), 'translation': dict(zip('xyz', (0, 0, 0)))}
_, _, matrices = transforms.joint_matrices([{'nodes': nodes, 'joints': [
    joint('a', (0, 0, 0), (2, 3, 4)), joint('b', (0, 0, 1.5707963267948966), (1, 1, 1))]}])
assert (matrices['b'] @ Vector((1, 0, 0)) - Vector((0, 2, 0))).length < 1e-5

# HSD envelope rules: root single-weight binding ignores the inverse bind;
# non-root single weight and multi-weight paths use it.
def affine_translation(x):
    return Matrix.Translation((x, 0, 0))
def bind_values(x):
    return [value for row in affine_translation(x) for value in row][:12]
envelope_nodes = {'d': {'ownerId': 'root'}, 'root': {'ownerId': 'group'}}
envelope_joints = {'root': {'flags': 2, 'inverseBindMatrix': bind_values(-5)},
                   'bone': {'flags': 1, 'inverseBindMatrix': bind_values(-7)},
                   'bone2': {'flags': 1, 'inverseBindMatrix': bind_values(-10)}}
envelope_world = {'root': affine_translation(5), 'bone': affine_translation(10),
                  'bone2': affine_translation(20)}
payload = {'ownerId': 'd', 'positions': [{'x': 1, 'y': 0, 'z': 0}],
           'envelopeIndices': [0], 'envelopes': [[{'jobjId': 'bone', 'weight': 1}]]}
positions, world = transforms.mesh_pose(payload, envelope_nodes, envelope_joints, envelope_world)
assert abs((world @ positions[0]).x - 11) < 1e-5
payload['envelopes'] = [[{'jobjId': 'bone', 'weight': 0.25}, {'jobjId': 'bone2', 'weight': 0.75}]]
positions, world = transforms.mesh_pose(payload, envelope_nodes, envelope_joints, envelope_world)
assert abs((world @ positions[0]).x - 9.25) < 1e-5
payload['envelopes'] = [[{'jobjId': 'bone', 'weight': 1}]]
envelope_joints['root']['flags'] = 1
positions, world = transforms.mesh_pose(payload, envelope_nodes, envelope_joints, envelope_world)
assert abs((world @ positions[0]).x - 9) < 1e-5

bpy.ops.preferences.addon_enable(module='melee_map_editor')
prefs = bpy.context.preferences.addons['melee_map_editor'].preferences
prefs.cli_path = str(CLI)
# Repeated enable/disable must be clean.
bpy.ops.preferences.addon_disable(module='melee_map_editor')
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)

with tempfile.TemporaryDirectory(prefix='mme-blender-smoke-') as tmp:
    tmp = Path(tmp)
    directory = tmp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    obj = scene.import_session(bpy.context, directory)
    s = bpy.context.scene
    sid = s.mme_session_id
    # Default filename and Blender's native overwrite confirmation.
    from types import SimpleNamespace
    from bpy_extras.io_utils import ExportHelper
    invoke = ExportHelper.invoke
    try:
        ExportHelper.invoke = lambda *args: {'RUNNING_MODAL'}
        export = SimpleNamespace(filepath='')
        assert addon.MME_OT_export.invoke(export, bpy.context, None) == {'RUNNING_MODAL'}
        assert export.filepath == 'GrNLa.dat'
        export.filepath = str(tmp / 'previous-choice.dat')
        addon.MME_OT_export.invoke(export, bpy.context, None)
        assert export.filepath == str(tmp / 'GrNLa.dat')
        assert bpy.ops.mme.export_stage.get_rna_type().properties['check_existing'].default
    finally:
        ExportHelper.invoke = invoke
    models = [o for o in s.objects if o.get('mme_session_id') == sid and o.get('mme_role') == 'pobj']
    groups = [c for c in bpy.data.collections if c.get('mme_session_id') == sid and c.get('mme_role') == 'group']
    assert len(groups) == 10, len(groups)
    assert len(models) == 93, len(models)
    assert sum(len(o.data.polygons) for o in models) == 13597
    assert len(obj.data.vertices) == len(obj.data.edges) == 16
    assert obj.data.attributes['mme_line'].domain == 'EDGE'
    assert all(o.parent and o.parent.get('mme_role') == 'dobj' for o in models)
    # Imported world transforms must retain the exact affine joint pose, including shear.
    manifest = read(directory / 'stage.json')
    payloads = [read(directory / e['file']) for e in manifest['modelGroups']]
    _, _, expected_world = transforms.joint_matrices(payloads)
    for o in s.objects:
        if o.get('mme_id') in expected_world:
            expected = transforms.AXES @ expected_world[o['mme_id']] @ transforms.AXES.inverted()
            assert all(math.isclose(o.matrix_world[i][j], expected[i][j], rel_tol=1e-6, abs_tol=1e-4)
                       for i in range(4) for j in range(4)), o.name
    assert scene.prepare(s)[1] is None
    scene.apply(s, CLI, 'dotnet', tmp / 'unchanged.dat')
    assert (tmp / 'unchanged.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()
    # Show/hide is presentation only.
    assert bpy.ops.mme.toggle_models() == {'FINISHED'}
    assert scene.prepare(s)[1] is None
    bpy.ops.mme.toggle_models()
    # Move one collision vertex and round-trip through the writer and extractor.
    old = obj.data.vertices[0].co.z
    obj.data.vertices[0].co.z += 1
    assert scene.prepare(s)[1] is not None
    assert bpy.ops.mme.export_stage(filepath=str(tmp / 'moved.dat')) == {'FINISHED'}
    run(CLI, 'dotnet', 'extract', tmp / 'moved.dat', '--session', tmp / 'moved-session')
    moved = read(tmp / 'moved-session/collision/collision.json')
    assert any(abs(v['position']['y'] - old - 1) < 1e-5 and
               abs(v['position']['x'] - obj.data.vertices[0].co.x) < 1e-5 for v in moved['vertices'])
    assert not (directory / 'edits/collision.json').exists()
    # Edit-mode bmesh edits and operators serialize correctly.
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    for e in bm.edges:
        e.select_set(False)
    bm.edges.ensure_lookup_table()
    bm.edges[0].select_set(True)
    original_low = bm.edges[0][bm.edges.layers.int['mme_low']]
    selected_id = read(directory / 'collision/collision.json')['lines'][bm.edges[0][bm.edges.layers.int['mme_line']]-1]['id']
    # Named selection shares the original integer storage, including old .blend values.
    s.mme_collision_material = 15
    assert s.mme_collision_surface == 'SURFACE_15'
    s.mme_collision_surface = 'SURFACE_7'
    assert s.mme_collision_material == 7
    s.mme_collision_material = 211
    assert s.mme_collision_surface == 'CUSTOM'
    s.mme_collision_surface = 'CUSTOM'
    assert s.mme_collision_material == 211
    s.mme_collision_surface = 'SURFACE_7'
    assert bpy.ops.mme.assign_collision(property='material') == {'FINISHED'}
    assert bpy.ops.mme.assign_collision(property='drop') == {'FINISHED'}
    assert bpy.ops.mme.assign_collision(property='ledge') == {'FINISHED'}
    assert next(e['lowFlags'] for e in scene.prepare(s)[1]['lines'] if e['id'] == selected_id) == ((original_low & 0xFF00) | 7) ^ 0x300
    assert bpy.ops.mme.validate_stage() == {'FINISHED'}
    bpy.ops.object.mode_set(mode='OBJECT')
    # Plane, topology, duplicate identities, protected transforms/geometry/modifiers.
    obj.data.vertices[0].co.y = 1
    rejects(lambda: scene.prepare(s), 'X/Z plane')
    obj.data.vertices[0].co.y = 0
    original = obj.data.attributes['mme_vertex'].data[0].value
    obj.data.attributes['mme_vertex'].data[0].value = 2
    rejects(lambda: scene.prepare(s), 'identities')
    obj.data.attributes['mme_vertex'].data[0].value = original
    original_mesh = obj.data
    obj.data = original_mesh.copy()
    obj.data.vertices.add(1)
    rejects(lambda: scene.prepare(s), 'topology')
    temporary_mesh = obj.data
    obj.data = original_mesh
    bpy.data.meshes.remove(temporary_mesh)
    editable = {m['id'] for m in manifest.get('editableMeshes', [])}
    model = next(o for o in models if o.get('mme_id') not in editable)
    model.location.x += 1
    rejects(lambda: scene.prepare(s), 'protected')
    model.location.x -= 1
    original = model.data.vertices[0].co.copy()
    model.data.vertices[0].co.x += 1
    rejects(lambda: scene.prepare(s), 'protected')
    model.data.vertices[0].co = original
    modifier = obj.modifiers.new('Unsupported', 'MIRROR')
    rejects(lambda: scene.prepare(s), 'modifiers')
    obj.modifiers.remove(modifier)
    # Session persists through .blend save/load and remains exportable.
    expected = scene.prepare(s)[1]
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'edited.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'edited.blend'))
    s = bpy.context.scene
    assert scene.prepare(s)[1] == expected
    assert s.mme_collision_surface == 'SURFACE_7' and s.mme_collision_material == 7
    scene.apply(s, CLI, 'dotnet', tmp / 'after-load.dat')
    scene.validate(s, CLI, 'dotnet')
    # Existing exports are replaced after validation.
    (tmp / 'after-load.dat').write_bytes(b'old output')
    assert bpy.ops.mme.export_stage(filepath=str(tmp / 'after-load.dat')) == {'FINISHED'}
    run(CLI, 'dotnet', 'validate', tmp / 'after-load.dat', '--json')
    # Publication errors still clean temporary edit input.
    rejects(lambda: scene.apply(s, CLI, 'dotnet', tmp), 'directory')
    assert not (directory / 'edits/collision.json').exists()
    model = next(o for o in s.objects if o.get('mme_role') == 'pobj')
    bpy.data.objects.remove(model, do_unlink=True)
    rejects(lambda: scene.prepare(s), 'protected')

    # A dynamic stage can be inspected and preserved, but collision is read-only.
    bpy.context.window.scene = bpy.data.scenes.new('Dynamic Stage')
    user_resource = bpy.utils.user_resource
    try:
        bpy.utils.user_resource = lambda *args, **kwargs: str(tmp)
        assert bpy.ops.mme.import_stage(filepath=str(CORPUS / 'GrGb.dat')) == {'FINISHED'}
    finally:
        bpy.utils.user_resource = user_resource
    ds = bpy.context.scene
    dynamic = scene.collision_object(ds)
    assert not read(scene.session(ds) / 'stage.json')['capabilities']['collisionEdit']
    scene.apply(ds, CLI, 'dotnet', tmp / 'dynamic-unchanged.dat')
    assert (tmp / 'dynamic-unchanged.dat').read_bytes() == (CORPUS / 'GrGb.dat').read_bytes()
    dynamic.data.vertices[0].co.z += 1
    rejects(lambda: scene.prepare(ds), 'read-only')

print('BLENDER_SMOKE_OK: import, transforms, no-op, collision edit/properties, guards, save/load, dynamic restrictions, CLI validation')
