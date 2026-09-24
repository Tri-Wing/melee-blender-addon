"""Explicit Edit Mode operations; ordinary Blender deletion remains supported."""
import bmesh
import bpy
import math
from mathutils import Vector
from . import collision
from .protocol import StageError


def project_to_collision_plane(obj, ray_origin, ray_direction):
    """Intersect a world-space view ray with the collision object's local Y=0 plane."""
    plane_origin = obj.matrix_world @ Vector((0, 0, 0))
    normal = (obj.matrix_world.inverted_safe().transposed().to_3x3()
              @ Vector((0, 1, 0))).normalized()
    direction = Vector(ray_direction)
    denominator = direction.dot(normal)
    if not all(math.isfinite(value) for value in (*ray_origin, *direction)) \
            or direction.length_squared == 0:
        raise StageError('The viewport produced an invalid placement ray.')
    if abs(denominator) <= 0.000001:
        raise StageError('View the collision more face-on before placing a vertex.')
    distance = (plane_origin - Vector(ray_origin)).dot(normal) / denominator
    if distance < 0:
        raise StageError('The collision plane is behind the current view.')
    local = obj.matrix_world.inverted_safe() @ (Vector(ray_origin) + direction * distance)
    local.y = 0
    if not all(math.isfinite(value) for value in local):
        raise StageError('The projected collision position is invalid.')
    return local


def _maximum_handle(objects, domain, name, baseline):
    maximum = baseline
    for obj in objects:
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            elements = bm.verts if domain == 'POINT' else bm.edges
            layers = bm.verts.layers.int if domain == 'POINT' else bm.edges.layers.int
            layer = layers.get('mme_' + name)
            if layer is not None:
                maximum = max([maximum] + [element[layer] for element in elements])
        else:
            attr = obj.data.attributes.get('mme_' + name)
            if attr is not None:
                maximum = max([maximum] + [item.value for item in attr.data])
    return maximum


def _next_handle(objects, domain, name, baseline):
    key = f'mme_collision_next_{name}'
    current = _maximum_handle(objects, domain, name, baseline) + 1
    scene = bpy.context.scene
    if objects and all(obj.get('mme_session_id') == scene.mme_session_id
                       for obj in objects):
        current = max(current, int(scene.get(key, baseline + 1)))
    return current


def _allocate_handles(objects, domain, name, baseline, count=1):
    """Reserve identities monotonically across every component and save/reload."""
    key = f'mme_collision_next_{name}'
    current = _next_handle(objects, domain, name, baseline)
    scene = bpy.context.scene
    if objects and all(obj.get('mme_session_id') == scene.mme_session_id
                       for obj in objects):
        scene[key] = current + count
    if current + count - 1 > 32767:
        raise StageError('Collision exceeds the 32767 element limit.')
    return current


def add_isolated_vertex(obj, source, coordinate, objects=None):
    """Create and select one identity-bearing collision vertex without an edge."""
    collision.serialize(obj, source)
    bm = bmesh.from_edit_mesh(obj.data)
    vertex = bm.verts.layers.int['mme_vertex']
    objects = list(objects or [obj])
    next_vertex = _allocate_handles(
        objects, 'POINT', 'vertex', len(source['vertices']))
    coordinate = Vector(coordinate)
    if not all(math.isfinite(value) for value in coordinate):
        raise StageError('Collision vertex coordinates must be finite.')
    coordinate.y = 0
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for existing in bm.verts:
        existing.select_set(False)
    created = bm.verts.new(coordinate)
    created[vertex] = next_vertex
    created.select_set(True)
    bm.select_mode = {'VERT'}
    bm.select_history.clear()
    bm.select_history.add(created)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)
    return next_vertex


def edit(obj, source, operation, dx=10, dz=0, category=0, material=0,
         joint=1, objects=None):
    # Check existing metadata before mutation, including native tool damage.
    collision.serialize(obj, source)
    bm = bmesh.from_edit_mesh(obj.data)
    vertex = bm.verts.layers.int['mme_vertex']
    layers = {key: bm.edges.layers.int['mme_' + key] for key in collision.ATTRS}
    objects = list(objects or [obj])

    def properties(edge):
        return {key: edge[layer] for key, layer in layers.items()}

    def directed(edge):
        start = next(v for v in edge.verts if v[vertex] == edge[layers['start']])
        return start, edge.other_vert(start)

    def known(row):
        dynamic = row['category'] == collision.CATEGORIES.index('dynamic')
        known_high = ((row['high'] & ~31) == 0 and row['high'] & 16
                      and row['high'] & 15 in (1, 2, 4, 8)) if dynamic \
            else (row['high'] & ~15) == 0
        if not known_high or row['low'] & 0xFC00:
            raise StageError('This edge has unknown flags that cannot be copied to new collision. Choose another edge.')

    def create(a, b, row, handle):
        edge = bm.edges.new((a, b))
        for key, layer in layers.items():
            edge[layer] = row[key]
        edge[layers['line']] = handle
        edge[layers['start']], edge[layers['end']] = a[vertex], b[vertex]
        return edge

    selected_edges = [e for e in bm.edges if e.select and not e.hide]
    selected_vertices = [v for v in bm.verts if v.select and not v.hide]
    if operation in ('split', 'reverse'):
        if not selected_edges:
            raise StageError('Select collision edges first.')
        plans = [(e, *directed(e), properties(e)) for e in selected_edges]
        if operation == 'split':
            for _, _, _, row in plans:
                known(row)
            next_vertex = _allocate_handles(
                objects, 'POINT', 'vertex', len(source['vertices']), len(plans))
            next_line = _allocate_handles(
                objects, 'EDGE', 'line', len(source['lines']), len(plans))
        for edge, a, b, row in plans:
            if operation == 'reverse':
                edge[layers['start']], edge[layers['end']] = b[vertex], a[vertex]
            else:
                mid = bm.verts.new((a.co + b.co) * 0.5)
                mid[vertex] = next_vertex
                next_vertex += 1
                bm.edges.remove(edge)
                create(a, mid, row, row['line']).select_set(True)
                create(mid, b, row, next_line).select_set(True)
                next_line += 1
    elif operation == 'extend':
        if len(selected_vertices) != 1 or len(selected_vertices[0].link_edges) != 1:
            raise StageError('Select exactly one open endpoint (a vertex with one edge).')
        if not math.isfinite(dx) or not math.isfinite(dz):
            raise StageError('Extension offsets must be finite.')
        if abs(dx) <= 0.0001 and abs(dz) <= 0.0001:
            raise StageError('Extension offset must be nonzero.')
        tip = selected_vertices[0]
        edge = tip.link_edges[0]
        a, b = directed(edge)
        row = properties(edge)
        known(row)
        next_vertex = _allocate_handles(
            objects, 'POINT', 'vertex', len(source['vertices']))
        next_line = _allocate_handles(
            objects, 'EDGE', 'line', len(source['lines']))
        new = bm.verts.new(tip.co + Vector((dx, 0, dz)))
        new[vertex] = next_vertex
        create(tip, new, row, next_line) if tip == b else create(new, tip, row, next_line)
        for v in bm.verts:
            v.select_set(False)
        for e in bm.edges:
            e.select_set(False)
        new.select_set(True)
        bm.select_history.clear()
        bm.select_history.add(new)
    elif operation == 'connect':
        if len(selected_vertices) != 2:
            raise StageError('Select exactly two open endpoints or isolated vertices.')
        a, b = sorted(selected_vertices, key=lambda v: v[vertex])
        if any(len(v.link_edges) > 1 for v in (a, b)) or bm.edges.get((a, b)):
            raise StageError('Connecting these vertices would duplicate an edge or create a branch.')
        if (a.co - b.co).length <= 0.0001:
            raise StageError('Cannot connect coincident vertices.')
        if not 1 <= joint <= len(source['joints']):
            raise StageError('Choose an existing collision joint (numbered from 1).')
        incidents = [v.link_edges[0] for v in (a, b) if v.link_edges]
        joints = {e[layers['joint']] for e in incidents}
        if len(joints) > 1:
            raise StageError('Cannot connect endpoints from different collision joints.')
        joint = next(iter(joints), joint)
        # Infer continuation from either existing endpoint; isolated pairs use handle order.
        if a.link_edges:
            if directed(a.link_edges[0])[0] == a:
                a, b = b, a
        elif b.link_edges and directed(b.link_edges[0])[1] == b:
            a, b = b, a
        if ((a.link_edges and directed(a.link_edges[0])[1] != a)
                or (b.link_edges and directed(b.link_edges[0])[0] != b)):
            raise StageError('Endpoints have incompatible directions. Reverse a connected chain first.')
        next_line = _allocate_handles(
            objects, 'EDGE', 'line', len(source['lines']))
        row = dict(line=next_line, start=a[vertex], end=b[vertex], joint=joint,
                   category=category, high=1 << category, low=material)
        create(a, b, row, next_line).select_set(True)
    else:
        raise StageError('Unknown collision operation.')
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)


def selected_counts(obj):
    if obj.mode != 'EDIT':
        return 0, 0
    bm = bmesh.from_edit_mesh(obj.data)
    return (sum(vertex.select and not vertex.hide for vertex in bm.verts),
            sum(edge.select and not edge.hide for edge in bm.edges))


def _merge_component_meshes(target, other):
    """Merge live Edit Mode meshes as part of the caller's single undo step."""
    target_bm = bmesh.from_edit_mesh(target.data)
    other_bm = bmesh.from_edit_mesh(other.data)
    target_vertex = target_bm.verts.layers.int.get('mme_vertex')
    other_vertex = other_bm.verts.layers.int.get('mme_vertex')
    target_edges = {key: target_bm.edges.layers.int.get('mme_' + key)
                    for key in collision.ATTRS}
    other_edges = {key: other_bm.edges.layers.int.get('mme_' + key)
                   for key in collision.ATTRS}
    if target_vertex is None or other_vertex is None \
            or any(value is None for value in (*target_edges.values(),
                                                *other_edges.values())):
        raise StageError('Collision identities are missing. Undo the edit or re-import.')
    transform = target.matrix_world.inverted_safe() @ other.matrix_world
    copied = {}
    for vertex in other_bm.verts:
        created = target_bm.verts.new(transform @ vertex.co)
        created[target_vertex] = vertex[other_vertex]
        copied[vertex] = created
    for edge in other_bm.edges:
        created = target_bm.edges.new(tuple(copied[vertex]
                                            for vertex in edge.verts))
        for key in collision.ATTRS:
            created[target_edges[key]] = edge[other_edges[key]]
    bmesh.ops.delete(other_bm, geom=list(other_bm.verts), context='VERTS')


def connect_components(context, objects, source, category, material):
    """Join two same-joint component meshes and connect their selected endpoints."""
    selected = []
    for obj in objects:
        if obj.mode != 'EDIT':
            continue
        bm = bmesh.from_edit_mesh(obj.data)
        vertex = bm.verts.layers.int.get('mme_vertex')
        if vertex is None:
            raise StageError('Collision vertex identities are missing.')
        start = bm.edges.layers.int.get('mme_start')
        joint = bm.edges.layers.int.get('mme_joint')
        if start is None or joint is None:
            raise StageError('Collision edge identities are missing.')
        for item in bm.verts:
            if not item.select or item.hide:
                continue
            incident = item.link_edges[0] if len(item.link_edges) == 1 else None
            selected.append({'object': obj, 'handle': item[vertex],
                             'coordinate': item.co.copy(),
                             'degree': len(item.link_edges),
                             'role': (None if incident is None else
                                      ('start' if incident[start] == item[vertex]
                                       else 'end')),
                             'edgeJoint': (None if incident is None
                                           else incident[joint])})
    if len(selected) != 2:
        raise StageError('Select exactly two open endpoints or isolated vertices.')
    first, second = selected
    first_joint = first['object'].get('mme_collision_joint')
    second_joint = second['object'].get('mme_collision_joint')
    if not isinstance(first_joint, int) or not isinstance(second_joint, int):
        raise StageError('Collision joint ownership changed. Re-import the DAT.')
    if first_joint != second_joint:
        raise StageError(
            f'Cannot connect Joint {first_joint:03d} to Joint {second_joint:03d}. '
            'Both endpoints must belong to the same collision joint.')
    if any(item['degree'] > 1 for item in selected):
        raise StageError('Select exactly two open endpoints or isolated vertices.')
    if any(item['edgeJoint'] not in (None, first_joint) for item in selected):
        raise StageError('Collision joint ownership changed. Undo the native Join or re-import.')
    if first['role'] is not None and first['role'] == second['role']:
        raise StageError('Endpoints have incompatible directions. Reverse a connected chain first.')
    first_world = first['object'].matrix_world @ first['coordinate']
    second_world = second['object'].matrix_world @ second['coordinate']
    if (first_world - second_world).length <= 0.0001:
        raise StageError('Cannot connect coincident vertices.')
    if first['object'] == second['object']:
        bm = bmesh.from_edit_mesh(first['object'].data)
        vertex = bm.verts.layers.int['mme_vertex']
        chosen = [item for item in bm.verts
                  if item[vertex] in {first['handle'], second['handle']}]
        if len(chosen) == 2 and bm.edges.get(tuple(chosen)):
            raise StageError('Connecting these vertices would duplicate an edge or create a branch.')
    collision.serialize_components(
        collision.collision_objects(context.scene), source, context.scene)
    if _next_handle(collision.collision_objects(context.scene), 'EDGE', 'line',
                    len(source['lines'])) > 32767:
        raise StageError('Collision exceeds the 32767 element limit.')
    if context.edit_object in {first['object'], second['object']}:
        primary = context.edit_object
        selected.sort(key=lambda item: item['object'] != primary)
        first, second = selected
    if first['object'] != second['object']:
        target = first['object']
        other = second['object']
        _merge_component_meshes(target, other)
        bm = bmesh.from_edit_mesh(target.data)
        vertex = bm.verts.layers.int['mme_vertex']
        handles = {first['handle'], second['handle']}
        for item in bm.verts:
            item.select_set(item[vertex] in handles)
    else:
        target = first['object']
    edit(target, source, 'connect', category=category, material=material,
         joint=first_joint, objects=collision.collision_objects(context.scene))
    if first['object'] != second['object']:
        bmesh.update_edit_mesh(other.data, loop_triangles=False,
                               destructive=True)
    return target
