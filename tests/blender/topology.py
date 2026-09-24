"""Run with Blender 4.5.0 --background --factory-startup --python-exit-code 1."""
import os
from pathlib import Path
import sys
import tempfile
import bpy
import bmesh
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import scene, collision, topology
from melee_map_editor.protocol import read, run, StageError
CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')
bpy.context.preferences.addons['melee_map_editor'].preferences.cli_path = str(CLI)


def select(obj, vertices=(), edges=()):
    bm = bmesh.from_edit_mesh(obj.data)
    for v in bm.verts:
        v.select_set(False)
    for e in bm.edges:
        e.select_set(False)
    vh = bm.verts.layers.int['mme_vertex']
    eh = bm.edges.layers.int['mme_line']
    for v in bm.verts:
        if v[vh] in vertices:
            v.select_set(True)
    for e in bm.edges:
        if e[eh] in edges:
            e.select_set(True)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def collision_object_with_line(scene_value, handle):
    for candidate in scene.collision_objects(scene_value):
        if candidate.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(candidate.data)
            layer = bm.edges.layers.int.get('mme_line')
            if layer is not None and any(edge[layer] == handle for edge in bm.edges):
                return candidate
        else:
            attr = candidate.data.attributes.get('mme_line')
            if attr is not None and any(item.value == handle for item in attr.data):
                return candidate
    raise AssertionError(f'Collision edge handle {handle} is missing')


def aggregate(scene_value, source):
    return collision.serialize_components(scene.collision_objects(scene_value), source)


with tempfile.TemporaryDirectory(prefix='mme-topology-') as tmp:
    tmp = Path(tmp)
    run(CLI, 'dotnet', 'extract', CORPUS / 'GrNLa.dat', '--session', tmp / 'session')
    obj = scene.import_session(bpy.context, tmp / 'session')
    source = read(tmp / 'session/collision/collision.json')
    s = bpy.context.scene
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    original = aggregate(s, source)
    # View rays are projected onto local Y=0 and create one selected isolated vertex.
    projected = topology.project_to_collision_plane(
        obj, Vector((125, -100, 75)), Vector((0, 1, 0)))
    assert projected == Vector((125, 0, 75))
    handle = topology.add_isolated_vertex(obj, source, projected)
    assert handle == len(source['vertices']) + 1
    placed = aggregate(s, source)
    assert len(placed['vertices']) == len(original['vertices']) + 1
    assert placed['lines'] == original['lines']
    bm = bmesh.from_edit_mesh(obj.data)
    vertex_layer = bm.verts.layers.int['mme_vertex']
    created = next(vertex for vertex in bm.verts if vertex[vertex_layer] == handle)
    assert created.select and created.co == Vector((125, 0, 75))
    assert sum(vertex.select for vertex in bm.verts) == 1
    bm.verts.remove(created)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)
    assert aggregate(s, source) == original
    try:
        topology.project_to_collision_plane(
            obj, Vector((0, 0, 0)), Vector((1, 0, 0)))
    except StageError as exc:
        assert 'face-on' in str(exc)
    else:
        raise AssertionError('An edge-on placement ray should be rejected')
    # Invalid selections are rejected before touching geometry or metadata.
    for operation, vertices, edges in [('extend', (), (1,)), ('connect', (1, 2), ())]:
        select(obj, vertices=vertices, edges=edges)
        try:
            topology.edit(obj, source, operation)
        except StageError:
            pass
        else:
            raise AssertionError('Expected invalid selection rejection')
        assert aggregate(s, source) == original
    select(obj, edges=(1,))
    bpy.ops.ed.undo_push(message='Before collision split')
    assert bpy.ops.mme.collision_topology(operation='split') == {'FINISHED'}
    split = scene.prepare(s)[1]
    assert len(split['vertices']) == len(split['lines']) == 17
    bpy.ops.ed.undo_push(message='After collision split')
    bpy.ops.ed.undo()
    obj = collision_object_with_line(bpy.context.scene, 1)
    assert aggregate(bpy.context.scene, source) == original
    bpy.ops.ed.redo()
    s = bpy.context.scene
    obj = collision_object_with_line(s, 1)
    assert aggregate(s, source) == split
    new_id = collision.identity(source, 'lines', 17)
    new = next(e for e in split['lines'] if e['id'] == new_id)
    old = next(e for e in split['lines'] if e['id'] == source['lines'][0]['id'])
    assert old['vertex1Id'] == new['vertex0Id']
    midpoint_handle = len(source['vertices']) + 2  # The deleted placement identity is not reused.
    assert new['vertex0Id'] == collision.identity(source, 'vertices', midpoint_handle)
    assert new['vertex1Id'] == source['lines'][0]['vertex1Id']
    assert new['lowFlags'] == old['lowFlags'] == source['lines'][0]['lowFlags']
    assert new['jointId'] == old['jointId']
    scene.apply(s, CLI, 'dotnet', tmp / 'split.dat')
    run(CLI, 'dotnet', 'extract', tmp / 'split.dat', '--session', tmp / 'split-session')
    assert len(read(tmp / 'split-session/collision/collision.json')['lines']) == 17
    # Reverse an entire closed chain; reversing it twice restores identical edit input.
    select(obj, edges=range(1, 18))
    bpy.ops.mme.collision_topology(operation='reverse')
    try:
        scene.validate(s, CLI, 'dotnet')
    except StageError as exc:
        assert 'COLLISION_FACING' in str(exc)
    else:
        raise AssertionError('Reversed edges with unchanged categories must fail')
    reversed_lines = aggregate(s, source)['lines']
    previous = {e['id']: e for e in split['lines']}
    for edge in reversed_lines:
        assert edge['vertex0Id'] == previous[edge['id']]['vertex1Id']
    bpy.ops.mme.collision_topology(operation='reverse')
    assert aggregate(s, source) == split
    # Native edge deletion, then reconnect the gap with explicit settings.
    select(obj, edges=(1,))
    bpy.ops.mesh.delete(type='EDGE')
    deleted = scene.prepare(s)[1]
    assert len(deleted['lines']) == 16
    scene.validate(s, CLI, 'dotnet')
    start = next(i+1 for i, v in enumerate(source['vertices']) if v['id'] == source['lines'][0]['vertex0Id'])
    select(obj, vertices=(start, midpoint_handle))
    s.mme_collision_type = 'floor'
    s.mme_collision_material = 7
    bpy.ops.mme.collision_topology(operation='connect')
    connected = scene.prepare(s)[1]
    assert len(connected['lines']) == 17
    added = next(e for e in connected['lines'] if e['id'] not in {x['id'] for x in deleted['lines']})
    assert added['category'] == 'floor' and added['lowFlags'] == 7
    scene.validate(s, CLI, 'dotnet')
    # Reopen the gap and extend one terminal, selecting only the new vertex afterward.
    bm = bmesh.from_edit_mesh(obj.data)
    handle = next(e[bm.edges.layers.int['mme_line']] for e in bm.edges
                  if collision.identity(source, 'lines', e[bm.edges.layers.int['mme_line']]) == added['id'])
    select(obj, edges=(handle,))
    bpy.ops.mesh.delete(type='EDGE')
    select(obj, vertices=(start,))
    bpy.ops.mme.collision_topology(operation='extend', offset_x=0, offset_z=20)
    extended = scene.prepare(s)[1]
    assert len(extended['lines']) == 17 and len(extended['vertices']) == 18
    terminal_handle = midpoint_handle + 1
    bm = bmesh.from_edit_mesh(obj.data)
    assert sum(v.select for v in bm.verts) == 1
    scene.apply(s, CLI, 'dotnet', tmp / 'extended.dat')
    # IDs survive saving/loading .blend and the session baseline remains intact.
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp / 'topology.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp / 'topology.blend'))
    s = bpy.context.scene
    obj = collision_object_with_line(s, 2)
    assert scene.prepare(s)[1] == extended
    scene.validate(s, CLI, 'dotnet')
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    # Native deletion of the new terminal removes its edge and vertex cleanly.
    select(obj, vertices=(terminal_handle,))
    bpy.ops.mesh.delete(type='VERT')
    assert len(scene.prepare(s)[1]['lines']) == 16
    scene.validate(s, CLI, 'dotnet')
    # Reversing one segment of a connected chain must fail export, then recover.
    select(obj, edges=(2,))
    bpy.ops.mme.collision_topology(operation='reverse')
    try:
        scene.validate(s, CLI, 'dotnet')
    except StageError as exc:
        assert 'COLLISION_ORIENTATION' in str(exc) or 'COLLISION_FACING' in str(exc)
    else:
        raise AssertionError('Expected incompatible direction rejection')
    bpy.ops.mme.collision_topology(operation='reverse')
    scene.validate(s, CLI, 'dotnet')
    # Unsupported native subdivision must not silently duplicate identities.
    select(obj, edges=(2,))
    bpy.ops.mesh.subdivide(number_cuts=1)
    try:
        scene.prepare(s)
    except StageError as exc:
        assert 'native topology' in str(exc), str(exc)
    else:
        raise AssertionError('Native subdivision should be rejected')
print('BLENDER_TOPOLOGY_OK: isolated placement, split, reverse, native deletion, connect, extend, save/load, exports, metadata guard')
