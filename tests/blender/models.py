"""One-target model edits, replacement geometry, combined export and scene guards."""
import os
import math
import json
from pathlib import Path
import sys
import tempfile
import bpy
import bmesh
from mathutils import Matrix, Vector

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
    stage = read(directory / 'stage.json')
    scene.import_session(bpy.context, directory)
    s = bpy.context.scene
    assert stage['capabilities']['modelEdit']
    target_info = next(info for info in modeling.targets(s)
                       if modeling.allows(info, 'topologyReplacement')
                       and read(directory / info['file']).get('normals'))
    target_info, target = modeling.resolve(s, info=target_info,
                                           operation='vertexMovement')
    assert modeling.edits(s, stage) is None
    scene.apply(s, CLI, 'dotnet', tmp / 'unchanged.dat')
    assert (tmp / 'unchanged.dat').read_bytes() == (CORPUS / 'GrNLa.dat').read_bytes()

    # Object Mode transforms are applied to a temporary export copy. The live
    # mesh and its matrix remain untouched, while positions and source normals
    # are rewritten in joint-local coordinates.
    original_basis = target.matrix_basis.copy()
    original_coordinate = target.data.vertices[0].co.copy()
    # Simulate a scene imported before model matrices became authorable. Its
    # immutable guard contains the original matrices rather than a dedicated
    # transform-baseline property.
    transform_baselines = json.loads(s['mme_model_transform_baselines'])
    legacy_guard = json.loads(s['mme_guard_inventory'])
    for row in legacy_guard['objects']:
        identity = row.get('props', {}).get('mme_id')
        if identity in transform_baselines:
            row['matrix'] = transform_baselines[identity]
    s['mme_guard_inventory'] = json.dumps(legacy_guard)
    s['mme_guard'] = digest(legacy_guard)
    del s['mme_model_transform_baselines']
    target.location = (3, -2, 5)
    target.rotation_euler.z = 0.25
    target.scale = (1.2, 0.8, 1.1)
    bpy.context.view_layer.update()
    transformed = modeling.edits(s, stage)
    assert transformed is not None
    transformed_mesh = transformed['meshes'][0]
    assert transformed_mesh.get('normals')
    delta = Matrix(transform_baselines[target_info['id']]).inverted() \
        @ target.matrix_basis
    expected_coordinate = delta @ original_coordinate
    expected_position = {'x': expected_coordinate.x,
                         'y': expected_coordinate.z,
                         'z': -expected_coordinate.y}
    assert all(math.isclose(transformed_mesh['positions'][0][key], value,
                            rel_tol=1e-6, abs_tol=1e-5)
               for key, value in expected_position.items())
    assert target.data.vertices[0].co == original_coordinate
    scene.apply(s, CLI, 'dotnet', tmp / 'object-transform.dat')
    assert s.get('mme_model_transform_baselines')
    run(CLI, 'dotnet', 'extract', tmp / 'object-transform.dat',
        '--session', tmp / 'object-transform')
    transformed_stage = read(tmp / 'object-transform/stage.json')
    locator = tuple(target_info[key] for key in
                    ('groupIndex', 'jobjIndex', 'dobjIndex', 'pobjIndex'))
    transformed_info = next(info for info in transformed_stage['editableMeshes']
                            if tuple(info[key] for key in
                                     ('groupIndex', 'jobjIndex', 'dobjIndex', 'pobjIndex')) == locator)
    transformed_output = read(tmp / 'object-transform' / transformed_info['file'])
    for actual, expected in zip(transformed_output['positions'],
                                transformed_mesh['positions']):
        assert all(math.isclose(actual[key], expected[key], rel_tol=1e-6,
                                abs_tol=1e-5) for key in ('x', 'y', 'z'))
    for actual, expected in zip(transformed_output['normals'],
                                transformed_mesh['normals']):
        assert all(math.isclose(actual[key], expected[key], rel_tol=1e-6,
                                abs_tol=1e-5) for key in ('x', 'y', 'z'))
    target.matrix_basis = original_basis
    bpy.context.view_layer.update()
    assert modeling.edits(s, stage) is None

    # Negative Object Mode scale is exported as a true reflection: positions
    # and normals are transformed, triangle winding is reversed, and source
    # corner attributes remain attached to the corresponding corners.
    target.scale.x = -1
    bpy.context.view_layer.update()
    reflected = modeling.edits(s, stage)
    reflected_mesh = reflected['meshes'][0]
    assert reflected_mesh['reverseWinding'] is True
    assert target.data.vertices[0].co == original_coordinate
    scene.apply(s, CLI, 'dotnet', tmp / 'reflected.dat')
    run(CLI, 'dotnet', 'extract', tmp / 'reflected.dat',
        '--session', tmp / 'reflected')
    reflected_stage = read(tmp / 'reflected/stage.json')
    reflected_info = next(info for info in reflected_stage['editableMeshes']
                          if tuple(info[key] for key in
                                   ('groupIndex', 'jobjIndex', 'dobjIndex', 'pobjIndex')) == locator)
    reflected_output = read(tmp / 'reflected' / reflected_info['file'])
    source_output = read(directory / target_info['file'])
    corner_order = []
    for i in range(0, len(source_output['triangleIndices']), 3):
        corner_order.extend((i, i + 2, i + 1))
    source_vertices = [source_output['triangleIndices'][corner]
                       for corner in corner_order]
    for actual, source_vertex in zip(reflected_output['positions'], source_vertices):
        expected = reflected_mesh['positions'][source_vertex]
        assert all(math.isclose(actual[key], expected[key], rel_tol=1e-6,
                                abs_tol=1e-5) for key in ('x', 'y', 'z'))
    for actual, source_vertex in zip(reflected_output['normals'], source_vertices):
        expected = reflected_mesh['normals'][source_vertex]
        assert all(math.isclose(actual[key], expected[key], rel_tol=1e-6,
                                abs_tol=1e-5) for key in ('x', 'y', 'z'))
    for attribute in ('texCoords0', 'texCoords1', 'colors0', 'colors1'):
        if source_output.get(attribute) is not None:
            assert reflected_output[attribute] == [source_output[attribute][i]
                                                    for i in source_vertices]
    assert reflected_output['triangleIndices'] == list(range(len(source_vertices)))
    target.matrix_basis = original_basis
    bpy.context.view_layer.update()
    assert modeling.edits(s, stage) is None

    # Keep the established topology/material test target for the remaining
    # cases; the transform target above was selected specifically for normals.
    target_info = next(info for info in modeling.targets(s)
                       if modeling.allows(info, 'topologyReplacement'))
    target_info, target = modeling.resolve(s, info=target_info,
                                           operation='vertexMovement')

    bpy.ops.object.select_all(action='DESELECT')
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
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
    locator = tuple(target_info[key] for key in ('groupIndex', 'jobjIndex', 'dobjIndex', 'pobjIndex'))
    moved_info = next(info for info in moved_stage['editableMeshes']
                      if tuple(info[key] for key in ('groupIndex', 'jobjIndex', 'dobjIndex', 'pobjIndex')) == locator)
    moved = read(tmp / 'moved' / moved_info['file'])
    original_mesh = read(directory / target_info['file'])
    assert moved['normals'] == original_mesh['normals']
    assert moved['pobjFlags'] == original_mesh['pobjFlags']
    assert moved['triangleIndices'] == original_mesh['triangleIndices']
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
    collision = scene.collision_objects(s)[0]
    collision.data.vertices[0].co.z += 1
    result = scene.apply(s, CLI, 'dotnet', tmp / 'combined.dat')
    assert result['modelChanged'] and result['collisionChanged'] and result['modelTriangles'] == 2
    assert not list((directory / 'edits').iterdir())
    # Native Object Mode Join accepts both unmaterialed and materialed cubes.
    bpy.ops.object.mode_set(mode='OBJECT')
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
        scene.prepare(s)
    assert any(p.material_index != 0 for p in target.data.polygons)
    replacement = modeling.edits(s, stage)
    assert len(replacement['meshes'][0]['triangleIndices']) == 26 * 3
    result = scene.apply(s, CLI, 'dotnet', tmp / 'joined.dat')
    assert result['modelTriangles'] == 26
    run(CLI, 'dotnet', 'extract', tmp / 'joined.dat', '--session', tmp / 'joined')
    joined_stage = read(tmp / 'joined/stage.json')
    joined_info = next(info for info in joined_stage['editableMeshes']
                       if tuple(info[key] for key in ('groupIndex', 'jobjIndex', 'dobjIndex', 'pobjIndex')) == locator)
    joined = read(tmp / 'joined' / joined_info['file'])
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
    # Reflections also work after arbitrary topology replacement; the temporary
    # export mesh receives Blender's reflected winding without mutating the scene.
    target.scale.x = -1
    replacement_reflection = modeling.edits(s, stage)
    assert not replacement_reflection['meshes'][0].get('reverseWinding')
    scene.validate(s, CLI, 'dotnet')
    target.scale.x = 1
    other = next(o for o in s.objects if o.type == 'MESH' and o.get('mme_role') == 'pobj'
                 and o.get('mme_id') not in modeling.target_ids(s))
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
    print('BLENDER_MODELS_OK: no-op, Object Mode transforms/reflections/normals, movement, replacement, native join, scene guards, combined export, protected read-only transforms/materials, save/load, failure safety')
