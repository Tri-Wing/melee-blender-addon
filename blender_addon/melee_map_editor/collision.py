"""Collision attributes and moving-joint preview helpers."""
import json
import math
import uuid
import bpy
from .protocol import SESSION_PROTOCOL, StageError, digest

CATEGORIES = ('floor', 'ceiling', 'right-wall', 'left-wall', 'dynamic')
ATTRS = ('line', 'start', 'end', 'joint', 'category', 'high', 'low')
COLORS = ((0.25, 0.9, 0.35, 1), (0.95, 0.35, 0.3, 1),
          (0.3, 0.6, 1, 1), (0.9, 0.6, 0.2, 1), (0.75, 0.35, 0.9, 1))


SOLID_FLOOR_COLOR = (0.06, 0.38, 0.12, 1)


def overlay_color(category, low_flags):
    if category == 0 and not low_flags & 0x100:
        return SOLID_FLOOR_COLOR
    return COLORS[category]


def create(collection, source, tag):
    vertices = {v['id']: i for i, v in enumerate(source['vertices'])}
    joints = {j['id']: i for i, j in enumerate(source['joints'])}
    mesh = bpy.data.meshes.new('Stage Collision')
    mesh.from_pydata([(v['position']['x'], 0, v['position']['y']) for v in source['vertices']],
                     [(vertices[e['vertex0Id']], vertices[e['vertex1Id']]) for e in source['lines']], [])
    obj = bpy.data.objects.new('Stage Collision', mesh)
    collection.objects.link(obj)
    tag(obj, 'collision', 'collision')
    obj.show_in_front = True
    obj.display_type = 'WIRE'
    attr = mesh.attributes.new('mme_vertex', 'INT', 'POINT')
    for i, item in enumerate(attr.data):
        item.value = i + 1  # Zero is invalid; Blender assigns it to new elements.
    values = {key: [] for key in ATTRS}
    for i, edge in enumerate(source['lines']):
        row = (i+1, vertices[edge['vertex0Id']]+1, vertices[edge['vertex1Id']]+1,
               joints[edge['jointId']]+1, CATEGORIES.index(edge['category']), edge['highFlags'], edge['lowFlags'])
        for key, value in zip(ATTRS, row):
            values[key].append(value)
    for key in ATTRS:
        attr = mesh.attributes.new('mme_' + key, 'INT', 'EDGE')
        for item, value in zip(attr.data, values[key]):
            item.value = value
    return obj


def configure_moving_preview(obj, source):
    """Record unambiguous serialized JOBJ bindings for the viewport overlay."""
    grouped = {}
    for attachment in source.get('attachments', []):
        joint = attachment['source']['jointIndex']
        grouped.setdefault(joint, []).append(attachment)
    bindings = {}
    unresolved = {}
    for joint, attachments in grouped.items():
        if len(attachments) == 1 and attachments[0].get('jobjId'):
            bindings[str(joint)] = attachments[0]['jobjId']
        else:
            unresolved[str(joint)] = ('external-target' if len(attachments) == 1
                                      and not attachments[0].get('jobjId')
                                      else 'multiple-bindings')
    obj['mme_collision_bindings'] = json.dumps(bindings, sort_keys=True)
    obj['mme_collision_unresolved_bindings'] = json.dumps(unresolved,
                                                          sort_keys=True)
    # Hide the untransformed source mesh outside Edit Mode when at least one
    # attachment can be previewed; the GPU overlay remains visible and evaluates
    # the current pose-bone matrices. Edit Mode exposes the DAT's local geometry.
    if bindings:
        obj['mme_collision_source_hidden'] = True
        obj.hide_set(True)
    return bindings, unresolved


def attachment_transforms(scene, obj):
    """Resolve serialized one-to-one collision bindings to current Blender poses."""
    try:
        bindings = json.loads(obj.get('mme_collision_bindings', '{}'))
    except (TypeError, ValueError):
        raise StageError('Moving-collision binding metadata is invalid. Re-import the DAT.')
    result = {}
    for joint, jobj_id in bindings.items():
        matches = [(armature, bone) for armature in scene.objects
                   if armature.type == 'ARMATURE'
                   and armature.get('mme_session_id') == scene.mme_session_id
                   and armature.get('mme_role') == 'jobj-armature'
                   for bone in armature.pose.bones if bone.get('mme_id') == jobj_id]
        if len(matches) != 1:
            raise StageError('A moving-collision JOBJ target is missing or duplicated. Re-import the DAT.')
        armature, bone = matches[0]
        result[int(joint)] = armature.matrix_world @ bone.matrix
    return result


def display_edge_points(scene, obj, edge, transforms=None):
    """Return an edge's evaluated viewport endpoints in source direction."""
    transforms = transforms if transforms is not None else attachment_transforms(scene, obj)
    vertex = obj.data.attributes.get('mme_vertex')
    start = obj.data.attributes.get('mme_start')
    joint = obj.data.attributes.get('mme_joint')
    if vertex is None or start is None or joint is None:
        raise StageError('Collision attributes are missing. Re-import the DAT.')
    matrix = transforms.get(joint.data[edge.index].value - 1, obj.matrix_world)
    points = [matrix @ obj.data.vertices[index].co for index in edge.vertices]
    if vertex.data[edge.vertices[0]].value != start.data[edge.index].value:
        points.reverse()
    return points


def serialize(obj, source):
    if obj.mode == 'EDIT':
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.index_update()
        coords = [v.co.copy() for v in bm.verts]
        endpoints = [[v.index for v in e.verts] for e in bm.edges]
        face_count = len(bm.faces)

        def values(name, domain):
            elements = bm.verts if domain == 'POINT' else bm.edges
            layer = elements.layers.int.get('mme_' + name)
            if layer is None:
                raise StageError(f'Collision attribute missing or changed: {name}. Undo the edit or re-import.')
            return [element[layer] for element in elements]
    else:
        mesh = obj.data
        coords = [v.co.copy() for v in mesh.vertices]
        endpoints = [list(e.vertices) for e in mesh.edges]
        face_count = len(mesh.polygons)

        def values(name, domain):
            attr = mesh.attributes.get('mme_' + name)
            if attr is None or attr.domain != domain or attr.data_type != 'INT':
                raise StageError(f'Collision attribute missing or changed: {name}. Undo the edit or re-import.')
            return [item.value for item in attr.data]
    if face_count:
        raise StageError('Collision must contain edges only; delete faces before exporting.')

    handles = values('vertex', 'POINT')
    attrs = {name: values(name, 'EDGE') for name in ATTRS}
    if any(h <= 0 for h in handles + attrs['line']) or len(set(handles)) != len(handles) or len(set(attrs['line'])) != len(attrs['line']):
        raise StageError('Collision identities were lost or duplicated. Undo the native topology edit and use the collision tools.')
    vertices = []
    for co, handle in zip(coords, handles):
        if not all(math.isfinite(x) for x in co) or abs(co.y) > 0.00001:
            raise StageError(f'Collision vertex {handle} must stay on the X/Z plane (Blender Y = 0).')
        vertices.append({'id': identity(source, 'vertices', handle), 'x': co.x, 'y': co.z})
    lines = []
    for i, edge in enumerate(endpoints):
        handle = attrs['line'][i]
        original = source['lines'][handle-1] if handle <= len(source['lines']) else None
        start, end = attrs['start'][i], attrs['end'][i]
        if start == end or set((start, end)) != {handles[v] for v in edge}:
            raise StageError(f'Collision edge {i+1} lost its endpoint direction. Undo the native topology edit and use the collision tools.')
        joint = attrs['joint'][i]
        category = attrs['category'][i]
        high, low = attrs['high'][i], attrs['low'][i]
        if not 1 <= joint <= len(source['joints']) or (original and source['joints'][joint-1]['id'] != original['jointId']):
            raise StageError('Collision joint identity changed.')
        if not 0 <= category < len(CATEGORIES) or not 0 <= high <= 65535 or not 0 <= low <= 65535:
            raise StageError('Invalid collision type or flags.')
        new_high_known = ((high & ~31) == 0 and high & 16
                          and high & 15 in (1, 2, 4, 8)) \
            if category == CATEGORIES.index('dynamic') else (high & ~15) == 0
        if (original and (high != original['highFlags'] or (low & 0xFC00) != (original['lowFlags'] & 0xFC00))) \
                or (not original and (not new_high_known or low & 0xFC00)):
            raise StageError('Protected collision flag bits changed.')
        lines.append({'id': identity(source, 'lines', handle), 'vertex0Id': identity(source, 'vertices', start),
                      'vertex1Id': identity(source, 'vertices', end), 'jointId': source['joints'][joint-1]['id'],
                      'category': CATEGORIES[category], 'highFlags': high, 'lowFlags': low})
    return {'protocolVersion': SESSION_PROTOCOL, 'coordinateSpace': 'game',
            'vertices': sorted(vertices, key=lambda x: x['id']), 'lines': sorted(lines, key=lambda x: x['id'])}


def fingerprint(obj):
    # Do not update_from_editmode inside a dependency-graph handler.
    if obj.mode == 'EDIT':
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        values = {'vertices': [list(v.co) for v in bm.verts], 'edges': len(bm.edges), 'faces': len(bm.faces)}
        for key in ATTRS:
            layer = bm.edges.layers.int.get('mme_' + key)
            values[key] = [e[layer] for e in bm.edges] if layer is not None else None
    else:
        values = {'vertices': [list(v.co) for v in obj.data.vertices], 'edges': len(obj.data.edges), 'faces': len(obj.data.polygons)}
        for key in ATTRS:
            attr = obj.data.attributes.get('mme_' + key)
            values[key] = [e.value for e in attr.data] if attr is not None and attr.data_type == 'INT' else None
    try:
        return digest(values)
    except ValueError:
        return 'invalid-coordinates'


def identity(source, kind, handle):
    if handle <= len(source[kind]):
        return source[kind][handle-1]['id']
    # Stable through save/load and mesh undo, without a separate mutable ID registry.
    return uuid.uuid5(uuid.UUID(source['joints'][0]['id']), f'{kind}:{handle}').hex


def assign_type(bm, edges, category):
    """Orient selected edges for the game's one-sided collision tests."""
    vertex = bm.verts.layers.int.get('mme_vertex')
    layers = {key: bm.edges.layers.int.get('mme_' + key)
              for key in ('start', 'end', 'category')}
    if vertex is None or any(layer is None for layer in layers.values()):
        raise StageError('Collision attributes are missing. Undo the edit or re-import.')
    plans = []
    for edge in edges:
        a, b = edge.verts
        delta = b.co.x - a.co.x if category in (0, 1) else b.co.z - a.co.z
        if not math.isfinite(delta) or delta == 0:
            raise StageError('Floors/ceilings need horizontal extent; walls need vertical extent. No selected edges were changed.')
        if (delta > 0) != (category in (0, 3)):
            a, b = b, a
        plans.append((edge, a[vertex], b[vertex]))
    for edge, start, end in plans:
        edge[layers['category']] = category
        edge[layers['start']], edge[layers['end']] = start, end
