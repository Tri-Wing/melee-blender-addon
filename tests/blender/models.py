"""One-target model edits, replacement geometry, combined export and scene guards."""
import os
import math
from pathlib import Path
import sys
import tempfile
import bpy
import bmesh
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, modeling
from melee_map_editor.protocol import digest, read, run, StageError
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)


def rejects(action, message):
    try:
        action()
    except StageError as exc:
        assert message.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError('Expected: ' + message)


with tempfile.TemporaryDirectory(prefix='mme-models-') as tmp:
    tmp = Path(tmp)
    directory = tmp / 'session'
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', directory)
    scene.import_session(bpy.context, directory)
    s = bpy.context.scene
    stage = read(directory / 'stage.json')
    assert stage['capabilities']['modelEdit']
    target = modeling.target_object(s)
    assert modeling.edits(s, stage) is None
    scene.apply(s, CLI, 'dotnet', tmp / 'unchanged.dat')
    assert (tmp / 'unchanged.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()
    assert bpy.ops.mme.edit_model() == {'FINISHED'}
    assert bpy.context.edit_object == target
    assert modeling.edits(s, stage) is None  # Entering Edit Mode alone is not a model edit.
    bm = bmesh.from_edit_mesh(target.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.z += 2
    bmesh.update_edit_mesh(target.data)
    edit = modeling.edits(s, stage)
    assert edit is not None
    assert scene.apply(s, CLI, 'dotnet', tmp / 'moved.dat')['modelChanged']
    assert not (directory / 'edits/models.json').exists()
    run(CLI, 'dotnet', 'extract', tmp / 'moved.dat', '--session', tmp / 'moved')
    moved_stage = read(tmp / 'moved/stage.json')
    moved = read(tmp / 'moved' / moved_stage['editableMesh']['file'])
    expected = edit['meshes'][0]['positions'][0]
    assert any(all(math.isclose(p[k], expected[k], rel_tol=1e-6, abs_tol=1e-5) for k in ('x', 'y', 'z'))
               for p in moved['positions']), expected
    # Replace geometry within the existing POBJ object using native Edit Mode ops.
    bmesh.ops.delete(bm, geom=list(bm.verts), context='VERTS')
    vertices = [bm.verts.new(p) for p in ((-10, -3, 0), (10, -3, 0), (10, -3, 10), (-10, -3, 10))]
    bm.faces.new(vertices)  # Export triangulates quads.
    bmesh.update_edit_mesh(target.data)
    replacement = modeling.edits(s, stage)
    assert len(replacement['meshes'][0]['triangleIndices']) == 6
    collision = scene.collision_object(s)
    collision.data.vertices[0].co.z += 1
    result = scene.apply(s, CLI, 'dotnet', tmp / 'combined.dat')
    assert result['modelChanged'] and result['collisionChanged'] and result['modelTriangles'] == 2
    assert not list((directory / 'edits').iterdir())
    # Native Object Mode Join accepts both unmaterialed and materialed cubes,
    # including in .blend files imported before editable materials were allowed.
    bpy.ops.object.mode_set(mode='OBJECT')
    modern_guard = s['mme_guard']
    legacy = scene.inventory(s, target['mme_id'])
    row = next(r for r in legacy['objects'] if r['props']['mme_id'] == target['mme_id'])
    row['editableMaterials'] = [m.name for m in target.data.materials]
    legacy_guard = digest(legacy)
    expected_world = []
    for with_material in (False, True):
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.mesh.primitive_cube_add(location=(30, 20, 40 if with_material else 50))
        cube = bpy.context.object
        cube.scale = (2, 3, 4)
        cube.rotation_euler.z = 0.3
        if with_material:
            cube.data.materials.append(bpy.data.materials.new('Joined Cube Material'))
        bpy.context.view_layer.update()
        expected_world.extend(cube.matrix_world @ v.co for v in cube.data.vertices)
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
        assert bpy.ops.object.join() == {'FINISHED'}
        for guard in (modern_guard, legacy_guard):
            s['mme_guard'] = guard
            scene.prepare(s)
    assert any(p.material_index != 0 for p in target.data.polygons)
    replacement = modeling.edits(s, stage)
    assert len(replacement['meshes'][0]['triangleIndices']) == 26 * 3
    result = scene.apply(s, CLI, 'dotnet', tmp / 'joined.dat')
    assert result['modelTriangles'] == 26
    run(CLI, 'dotnet', 'extract', tmp / 'joined.dat', '--session', tmp / 'joined')
    joined_stage = read(tmp / 'joined/stage.json')
    joined = read(tmp / 'joined' / joined_stage['editableMesh']['file'])
    assert joined['pobjFlags'] & 0xC000 == 0x4000  # Back-face culling, regardless of source policy.
    # Ensure the writer retains Blender winding and its outward flat normals.
    target.data.calc_loop_triangles()
    assert len(target.data.loop_triangles) * 3 == len(joined['positions'])
    for i, triangle in enumerate(target.data.loop_triangles):
        points = [target.data.vertices[j].co for j in triangle.vertices]
        normal = (points[1] - points[0]).cross(points[2] - points[0]).normalized()
        for corner in range(3):
            n = joined['normals'][i * 3 + corner]
            assert Vector((n['x'], -n['z'], n['y'])).dot(normal) > 0.99999
    for point in expected_world:
        local = target.matrix_world.inverted() @ point
        expected = {'x': local.x, 'y': local.z, 'z': -local.y}
        assert any(all(math.isclose(p[k], expected[k], rel_tol=1e-6, abs_tol=1e-5)
                       for k in expected) for p in joined['positions']), expected
    # Legacy compatibility must still reject protected edits.
    target.location.x = 1
    rejects(lambda: scene.prepare(s), 'protected')
    target.location.x = 0
    other = next(o for o in s.objects if o.type == 'MESH' and o.get('mme_role') == 'pobj' and o != target)
    original = other.data.vertices[0].co.copy()
    other.data.vertices[0].co.x += 1
    rejects(lambda: scene.prepare(s), 'protected')
    other.data.vertices[0].co = original
    parent = target.parent
    target.parent = None
    rejects(lambda: scene.prepare(s), 'protected')
    target.parent = parent
    other.data.materials.append(bpy.data.materials.new('Protected Material'))
    rejects(lambda: scene.prepare(s), 'protected')
    other.data.materials.pop(index=len(other.data.materials) - 1)
    modifier = target.modifiers.new('Unsupported', 'MIRROR')
    rejects(lambda: scene.prepare(s), 'modifiers')
    target.modifiers.remove(modifier)
    # Save/reopen retains permissions, target identity and replacement geometry.
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'model.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'model.blend'))
    s = bpy.context.scene
    assert modeling.edits(s, stage) == replacement
    scene.validate(s, CLI, 'dotnet')
    target = modeling.target_object(s)
    # Deleting all faces fails without replacing an existing output or leaving edits.
    saved = (tmp / 'combined.dat').read_bytes()
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(target.data)
    bmesh.ops.delete(bm, geom=list(bm.faces), context='FACES')
    bmesh.update_edit_mesh(target.data)
    rejects(lambda: scene.apply(s, CLI, 'dotnet', tmp / 'combined.dat'), 'needs triangles')
    assert (tmp / 'combined.dat').read_bytes() == saved
    assert not list((directory / 'edits').iterdir())
print('BLENDER_MODELS_OK: no-op, movement, replacement, native join, legacy guards, combined export, protected transforms/materials, save/load, failure safety')
