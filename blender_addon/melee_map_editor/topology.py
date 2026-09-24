"""Explicit Edit Mode operations; ordinary Blender deletion remains supported."""
import bmesh
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


def add_isolated_vertex(obj, source, coordinate):
    """Create and select one identity-bearing collision vertex without an edge."""
    collision.serialize(obj, source)
    bm = bmesh.from_edit_mesh(obj.data)
    vertex = bm.verts.layers.int['mme_vertex']
    next_vertex = max([len(source['vertices'])] + [v[vertex] for v in bm.verts]) + 1
    if len(bm.verts) >= 32767 or next_vertex > 32767:
        raise StageError('Collision exceeds the 32767 vertex limit.')
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


def edit(obj, source, operation, dx=10, dz=0, category=0, material=0, joint=1):
    # Check existing metadata before mutation, including native tool damage.
    collision.serialize(obj, source)
    bm = bmesh.from_edit_mesh(obj.data)
    vertex = bm.verts.layers.int['mme_vertex']
    layers = {key: bm.edges.layers.int['mme_' + key] for key in collision.ATTRS}
    next_vertex = max([len(source['vertices'])] + [v[vertex] for v in bm.verts]) + 1
    next_line = max([len(source['lines'])] + [e[layers['line']] for e in bm.edges]) + 1

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
            if len(bm.verts) + len(plans) > 32767 or len(bm.edges) + len(plans) > 32767:
                raise StageError('Collision exceeds the 32767 element limit.')
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
        if len(bm.verts) >= 32767 or len(bm.edges) >= 32767:
            raise StageError('Collision exceeds the 32767 element limit.')
        tip = selected_vertices[0]
        edge = tip.link_edges[0]
        a, b = directed(edge)
        row = properties(edge)
        known(row)
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
        if len(bm.edges) >= 32767:
            raise StageError('Collision exceeds the 32767 element limit.')
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
        row = dict(line=next_line, start=a[vertex], end=b[vertex], joint=joint,
                   category=category, high=1 << category, low=material)
        create(a, b, row, next_line).select_set(True)
    else:
        raise StageError('Unknown collision operation.')
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)
