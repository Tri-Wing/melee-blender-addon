"""Collision components remain independent editor objects and one DAT graph."""
from collections import defaultdict
import os
from pathlib import Path
import sys
import tempfile

import bmesh
import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_addon'))
from melee_map_editor import collision, scene, topology
from melee_map_editor.protocol import read, run, StageError

CLI = ROOT / 'src/MeleeMap.Cli/bin/Debug/net8.0/meleemap.dll'
CORPUS = Path(os.environ.get('MELEEMAP_CORPUS', ROOT / 'example_assets'))
bpy.ops.preferences.addon_enable(module='melee_map_editor')


def endpoints(obj):
    """Return (direction role, stable handle) for open endpoints."""
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        vertex = bm.verts.layers.int['mme_vertex']
        start = bm.edges.layers.int['mme_start']
        return [('start' if item.link_edges[0][start] == item[vertex] else 'end',
                 item[vertex]) for item in bm.verts if len(item.link_edges) == 1]
    vertex = obj.data.attributes['mme_vertex']
    start = obj.data.attributes['mme_start']
    linked = defaultdict(list)
    for edge in obj.data.edges:
        for index in edge.vertices:
            linked[index].append(edge)
    return [('start' if start.data[edges[0].index].value
             == vertex.data[index].value else 'end', vertex.data[index].value)
            for index, edges in linked.items() if len(edges) == 1]


def select_handle(obj, handle):
    bm = bmesh.from_edit_mesh(obj.data)
    vertex = bm.verts.layers.int['mme_vertex']
    for edge in bm.edges:
        edge.select_set(False)
    for item in bm.verts:
        item.select_set(item[vertex] == handle)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def choose_compatible_pair(objects):
    for first_index, first in enumerate(objects):
        for second in objects[first_index + 1:]:
            for first_role, first_handle in endpoints(first):
                for second_role, second_handle in endpoints(second):
                    if first_role != second_role:
                        return first, first_handle, second, second_handle
    raise AssertionError('Fixture has no compatible cross-component endpoints')


def enter_edit(objects, active=None):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or objects[0]
    bpy.ops.object.mode_set(mode='EDIT')


with tempfile.TemporaryDirectory(prefix='mme-collision-components-') as temporary:
    temporary = Path(temporary)
    source_path = CORPUS / 'GrMc.dat'
    directory = temporary / 'session'
    run(CLI, 'dotnet', 'extract', source_path, '--session', directory)
    source = read(directory / 'collision/collision.json')
    scene.import_session(bpy.context, directory)
    s = bpy.context.scene
    objects = scene.collision_objects(s)
    baseline_count = len(objects)
    assert baseline_count > len(source['joints'])
    assert s['mme_collision_representation_version'] == 2
    joint_collections = [item for item in bpy.data.collections
                         if item.get('mme_session_id') == s.mme_session_id
                         and item.get('mme_role') == 'collision-joint']
    assert len(joint_collections) == len(source['joints'])

    # Every imported object owns exactly one connected joint-local island.
    for obj in objects:
        assert obj.get('mme_collision_component_id')
        joint = obj['mme_collision_joint']
        assert obj['mme_collision_joint_id'] == source['joints'][joint-1]['id']
        assert all(item.value == joint
                   for item in obj.data.attributes['mme_joint'].data)
        if obj.data.edges:
            reached = {obj.data.edges[0].vertices[0]}
            changed = True
            while changed:
                changed = False
                for edge in obj.data.edges:
                    if reached.intersection(edge.vertices) and not set(edge.vertices) <= reached:
                        reached.update(edge.vertices)
                        changed = True
            assert reached == set(range(len(obj.data.vertices)))
        else:
            assert len(obj.data.vertices) == 1

    # User and collection visibility are editor-only; hidden data still exports.
    hidden = objects[0]
    hidden.hide_set(True)
    hidden.hide_viewport = True
    hidden.users_collection[0].hide_viewport = True
    assert scene.prepare(s)[1] is None
    unchanged = temporary / 'hidden-unchanged.dat'
    scene.apply(s, CLI, 'dotnet', unchanged)
    assert unchanged.read_bytes() == source_path.read_bytes()
    hidden.users_collection[0].hide_viewport = False
    hidden.hide_viewport = False
    hidden.hide_set(False)

    # Component object transforms are managed preview state, not implicit
    # geometry edits, and are rejected rather than baked into local vertices.
    original_matrix = hidden.matrix_world.copy()
    hidden.location.x += 3
    bpy.context.view_layer.update()
    collision.update_component_transforms(s)
    assert hidden.matrix_world != original_matrix
    try:
        scene.prepare(s)
    except StageError as exc:
        assert 'display-managed' in str(exc)
    else:
        raise AssertionError('Arbitrary collision object transforms must fail')
    hidden.matrix_world = original_matrix
    collision.update_component_transforms(s)

    owner_collection = hidden.users_collection[0]
    owner_collection.objects.unlink(hidden)
    try:
        scene.prepare(s)
    except StageError as exc:
        assert 'unlinked rather than deleted' in str(exc)
    else:
        raise AssertionError('Unlinked collision must not look like deleted geometry')
    owner_collection.objects.link(hidden)

    # A same-joint connection merges the objects, keeps the active component ID,
    # and allocates a session-global line identity.
    by_joint = defaultdict(list)
    for obj in scene.collision_objects(s):
        by_joint[obj['mme_collision_joint']].append(obj)
    candidates = next(items for items in by_joint.values() if len(items) > 1
                      and any(endpoints(item) for item in items))
    first, first_handle, second, second_handle = choose_compatible_pair(candidates)
    retained_id = second['mme_collision_component_id']
    enter_edit([first, second], second)
    bpy.ops.mesh.select_all(action='DESELECT')
    select_handle(first, first_handle)
    select_handle(second, second_handle)
    counts = [(obj.name, topology.selected_counts(obj))
              for obj in scene.editing_collision_objects(bpy.context)]
    assert sum(sum(value) for _, value in counts) == 2, counts
    previous_lines = len(collision.serialize_components(
        scene.collision_objects(s), source)['lines'])
    bpy.ops.ed.undo_push(message='Before component connection')
    assert bpy.ops.mme.collision_topology(operation='connect') == {'FINISHED'}
    merged = bpy.context.edit_object
    assert merged['mme_collision_component_id'] == retained_id
    # The second object stays as an empty undo-safe shell until normalization.
    assert len(scene.collision_objects(s)) == baseline_count
    connected = collision.serialize_components(scene.collision_objects(s), source)
    assert len(connected['lines']) == previous_lines + 1
    bm = bmesh.from_edit_mesh(merged.data)
    line_layer = bm.edges.layers.int['mme_line']
    vertex_layer = bm.verts.layers.int['mme_vertex']
    start_layer = bm.edges.layers.int['mme_start']
    new_edge = next(edge for edge in bm.edges
                    if edge[line_layer] > len(source['lines']))
    start_vertex = next(vertex for vertex in new_edge.verts
                        if vertex[vertex_layer] == new_edge[start_layer])
    delta = new_edge.other_vert(start_vertex).co - start_vertex.co
    valid_category = (0 if delta.x > 0 else 1) if abs(delta.x) >= abs(delta.z) \
        else (3 if delta.z > 0 else 2)
    collision.assign_type(bm, [new_edge], valid_category)
    bmesh.update_edit_mesh(merged.data, loop_triangles=False, destructive=False)
    scene.validate(s, CLI, 'dotnet')

    # Undo restores both component objects and their geometry.
    bpy.ops.ed.undo_push(message='After component connection')
    bpy.ops.ed.undo()
    assert len(scene.collision_objects(s)) == baseline_count, len(scene.collision_objects(s))
    undone_line_count = len(collision.serialize_components(
        scene.collision_objects(s), source)['lines'])
    assert undone_line_count == previous_lines, (undone_line_count, previous_lines)
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Cross-joint rejection happens before Join and leaves aggregate data intact.
    objects = scene.collision_objects(s)
    first = next(obj for obj in objects if obj['mme_collision_joint'] == 1
                 and endpoints(obj))
    second = next(obj for obj in objects if obj['mme_collision_joint'] == 2
                  and endpoints(obj))
    first_handle = endpoints(first)[0][1]
    second_handle = endpoints(second)[0][1]
    enter_edit([first, second], first)
    bpy.ops.mesh.select_all(action='DESELECT')
    select_handle(first, first_handle)
    select_handle(second, second_handle)
    before = collision.serialize_components(scene.collision_objects(s), source)
    before_ids = {obj['mme_collision_component_id'] for obj in scene.collision_objects(s)}
    try:
        result = bpy.ops.mme.collision_topology(operation='connect')
    except RuntimeError as exc:
        assert 'Cannot connect Joint 001 to Joint 002' in str(exc)
    else:
        assert result == {'CANCELLED'}
    assert collision.serialize_components(scene.collision_objects(s), source) == before
    assert {obj['mme_collision_component_id'] for obj in scene.collision_objects(s)} == before_ids
    bpy.ops.object.mode_set(mode='OBJECT')

    # Native deletion can disconnect an island; the explicit organization tool
    # normalizes it after Blender's ordinary Tab/Object Mode transition.
    chain = next(obj for obj in scene.collision_objects(s)
                 if len(obj.data.edges) >= 3 and endpoints(obj))
    enter_edit([chain])
    bpy.ops.ed.undo_push(message='Before component normalization')
    bm = bmesh.from_edit_mesh(chain.data)
    for edge in bm.edges:
        edge.select_set(False)
    bridge = next(edge for edge in bm.edges
                  if all(len(vertex.link_edges) > 1 for vertex in edge.verts))
    bridge.select_set(True)
    bmesh.update_edit_mesh(chain.data, loop_triangles=False, destructive=False)
    bpy.ops.mesh.delete(type='EDGE')
    count_before_exit = len(scene.collision_objects(s))
    bpy.ops.object.mode_set(mode='OBJECT')
    assert bpy.ops.mme.normalize_collision_components() == {'FINISHED'}
    normalized_count = len(scene.collision_objects(s))
    assert normalized_count > count_before_exit
    bpy.ops.ed.undo_push(message='After component normalization')
    bpy.ops.ed.undo()
    s = bpy.context.scene
    assert len(scene.collision_objects(s)) == count_before_exit
    bpy.ops.ed.redo()
    s = bpy.context.scene
    assert len(scene.collision_objects(s)) == normalized_count

    # Component object deletion removes only its aggregate geometry.
    victim = next(obj for obj in scene.collision_objects(s) if obj.data.edges)
    line_count = len(collision.serialize_components(scene.collision_objects(s), source)['lines'])
    removed_lines = len(victim.data.edges)
    bpy.ops.object.select_all(action='DESELECT')
    victim.select_set(True)
    bpy.context.view_layer.objects.active = victim
    bpy.ops.object.delete()
    deleted = collision.serialize_components(scene.collision_objects(s), source)
    assert len(deleted['lines']) == line_count - removed_lines

    # Saved scenes from the preview-only representation are converted in place;
    # their internal source-mesh hiding does not become a user hide choice.
    bpy.context.window.scene = bpy.data.scenes.new('Legacy Collision Conversion')
    legacy_directory = temporary / 'legacy-session'
    run(CLI, 'dotnet', 'extract', source_path, '--session', legacy_directory)
    scene.import_session(bpy.context, legacy_directory)
    legacy_scene = bpy.context.scene
    legacy_source = read(legacy_directory / 'collision/collision.json')
    legacy_objects = scene.collision_objects(legacy_scene)
    legacy_payload = collision.serialize_components(legacy_objects, legacy_source)
    for obj in legacy_objects:
        del obj['mme_collision_component_id']
        del obj['mme_collision_joint']
        del obj['mme_collision_joint_id']
    legacy_objects[0]['mme_collision_source_hidden'] = True
    legacy_objects[0].hide_set(True)
    legacy_scene['mme_collision_representation_version'] = 0
    assert collision.ensure_component_representation(legacy_scene, legacy_source)
    converted = scene.collision_objects(legacy_scene)
    assert all(obj.get('mme_collision_component_id') for obj in converted)
    assert not any(obj.hide_get() for obj in converted)
    assert collision.serialize_components(converted, legacy_source) == legacy_payload

print('BLENDER_COLLISION_COMPONENTS_OK: partition, visibility, merge/reject, normalization, deletion, legacy conversion')
