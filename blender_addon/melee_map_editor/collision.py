"""Collision edge attributes retain orientation independently of Blender edge order."""
import math
import bpy
from .protocol import StageError, digest

CATEGORIES = ('floor', 'ceiling', 'right-wall', 'left-wall', 'dynamic')
ATTRS = ('line', 'start', 'end', 'joint', 'category', 'high', 'low')
COLORS = ((0.25, 0.9, 0.35, 1), (0.95, 0.35, 0.3, 1),
          (0.3, 0.6, 1, 1), (0.9, 0.6, 0.2, 1), (0.75, 0.35, 0.9, 1))


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
    if len(coords) != len(source['vertices']) or len(endpoints) != len(source['lines']) or face_count:
        raise StageError('Collision topology editing is not available yet. Keep the original vertices and edges.')

    handles = values('vertex', 'POINT')
    attrs = {name: values(name, 'EDGE') for name in ATTRS}
    if sorted(handles) != list(range(1, len(source['vertices'])+1)) or sorted(attrs['line']) != list(range(1, len(source['lines'])+1)):
        raise StageError('Collision identities were lost or duplicated. Undo the topology edit or re-import.')
    vertices = []
    for co, handle in zip(coords, handles):
        if not all(math.isfinite(x) for x in co) or abs(co.y) > 0.00001:
            raise StageError(f'Collision vertex {handle} must stay on the X/Z plane (Blender Y = 0).')
        vertices.append({'id': source['vertices'][handle-1]['id'], 'x': co.x, 'y': co.z})
    lines = []
    for i, edge in enumerate(endpoints):
        original = source['lines'][attrs['line'][i]-1]
        start, end = attrs['start'][i], attrs['end'][i]
        if (not 1 <= start <= len(handles) or not 1 <= end <= len(handles)
                or set((start, end)) != {handles[v] for v in edge}
                or source['vertices'][start-1]['id'] != original['vertex0Id']
                or source['vertices'][end-1]['id'] != original['vertex1Id']):
            raise StageError(f'Collision edge {i+1} endpoints or direction changed. Topology editing is deferred.')
        joint = attrs['joint'][i]
        category = attrs['category'][i]
        high, low = attrs['high'][i], attrs['low'][i]
        if not 1 <= joint <= len(source['joints']) or source['joints'][joint-1]['id'] != original['jointId']:
            raise StageError('Collision joint identity changed.')
        if not 0 <= category < len(CATEGORIES) or not 0 <= high <= 65535 or not 0 <= low <= 65535:
            raise StageError('Invalid collision type or flags.')
        if high != original['highFlags'] or (low & 0xFC00) != (original['lowFlags'] & 0xFC00):
            raise StageError('Protected collision flag bits changed.')
        lines.append({'id': original['id'], 'vertex0Id': original['vertex0Id'],
                      'vertex1Id': original['vertex1Id'], 'jointId': original['jointId'],
                      'category': CATEGORIES[category], 'highFlags': high, 'lowFlags': low})
    return {'protocolVersion': 2, 'coordinateSpace': 'game',
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
