"""Collision attributes and moving-joint preview helpers."""
import json
import math
import uuid
import bpy
from mathutils import Matrix
from .protocol import SESSION_PROTOCOL, StageError, digest

CATEGORIES = ('floor', 'ceiling', 'right-wall', 'left-wall', 'dynamic')
ATTRS = ('line', 'start', 'end', 'joint', 'category', 'high', 'low')
COLORS = ((0.25, 0.9, 0.35, 1), (0.95, 0.35, 0.3, 1),
          (0.3, 0.6, 1, 1), (0.9, 0.6, 0.2, 1), (0.75, 0.35, 0.9, 1))


SOLID_FLOOR_COLOR = (0.06, 0.38, 0.12, 1)
SELECTION_COLOR = (1.0, 0.32, 0.02, 1)


def overlay_color(category, low_flags):
    if category == 0 and not low_flags & 0x100:
        return SOLID_FLOOR_COLOR
    return COLORS[category]


def _components(source):
    """Return deterministic connected line/vertex sets within each collision joint."""
    vertex_index = {vertex['id']: i for i, vertex in enumerate(source['vertices'])}
    lines_by_joint = {joint['id']: [] for joint in source['joints']}
    for index, line in enumerate(source['lines']):
        lines_by_joint[line['jointId']].append(index)
    result = []
    for joint_index, joint in enumerate(source['joints']):
        line_indices = lines_by_joint[joint['id']]
        by_vertex = {}
        for line_index in line_indices:
            line = source['lines'][line_index]
            for vertex_id in (line['vertex0Id'], line['vertex1Id']):
                by_vertex.setdefault(vertex_id, []).append(line_index)
        remaining = set(line_indices)
        while remaining:
            seed = min(remaining)
            stack = [seed]
            component_lines = set()
            component_vertices = set()
            while stack:
                line_index = stack.pop()
                if line_index not in remaining:
                    continue
                remaining.remove(line_index)
                component_lines.add(line_index)
                line = source['lines'][line_index]
                for vertex_id in (line['vertex0Id'], line['vertex1Id']):
                    component_vertices.add(vertex_index[vertex_id])
                    stack.extend(by_vertex[vertex_id])
            result.append((joint_index, sorted(component_vertices),
                           sorted(component_lines)))
        record = joint['source']
        owned = range(record['vertexStart'],
                      record['vertexStart'] + record['vertexCount'])
        used = {vertex_index[vertex_id] for line_index in line_indices
                for vertex_id in (source['lines'][line_index]['vertex0Id'],
                                   source['lines'][line_index]['vertex1Id'])}
        for vertex in sorted(set(owned) - used):
            result.append((joint_index, [vertex], []))
    return result


def create(collection, source, tag, collection_factory=None):
    """Create one collision mesh object per joint-local connected component."""
    vertex_index = {vertex['id']: index
                    for index, vertex in enumerate(source['vertices'])}
    joint_collections = {}
    if collection_factory is not None:
        for joint_index, joint in enumerate(source['joints']):
            parent = collection_factory(
                'Collision Joint', collection,
                f"collision-joint:{joint['id']}", 'collision-joint',
                joint_index)
            parent['mme_collision_joint'] = joint_index + 1
            parent['mme_collision_joint_id'] = joint['id']
            joint_collections[joint_index] = parent
    objects = []
    for joint_index, component_vertices, component_lines in _components(source):
        joint = source['joints'][joint_index]
        parent = joint_collections.get(joint_index, collection)
        local = {source['vertices'][index]['id']: local_index
                 for local_index, index in enumerate(component_vertices)}
        positions = [(source['vertices'][index]['position']['x'], 0,
                      source['vertices'][index]['position']['y'])
                     for index in component_vertices]
        edges = [(local[source['lines'][index]['vertex0Id']],
                  local[source['lines'][index]['vertex1Id']])
                 for index in component_lines]
        component_seed = ('line:' + str(component_lines[0]) if component_lines
                          else 'vertices:' + ','.join(map(str, component_vertices)))
        component_id = uuid.uuid5(uuid.UUID(joint['id']), component_seed).hex
        name = 'Collision Component'
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(positions, edges, [])
        obj = bpy.data.objects.new(name, mesh)
        parent.objects.link(obj)
        tag(obj, 'collision', f'collision-component:{component_id}')
        obj['mme_collision_component_id'] = component_id
        obj['mme_collision_joint'] = joint_index + 1
        obj['mme_collision_joint_id'] = joint['id']
        obj.show_in_front = True
        obj.display_type = 'WIRE'
        attr = mesh.attributes.new('mme_vertex', 'INT', 'POINT')
        for item, source_index in zip(attr.data, component_vertices):
            item.value = source_index + 1
        values = {key: [] for key in ATTRS}
        for line_index in component_lines:
            edge = source['lines'][line_index]
            row = (line_index + 1,
                   vertex_index[edge['vertex0Id']] + 1,
                   vertex_index[edge['vertex1Id']] + 1,
                   joint_index + 1, CATEGORIES.index(edge['category']),
                   edge['highFlags'], edge['lowFlags'])
            for key, value in zip(ATTRS, row):
                values[key].append(value)
        for key in ATTRS:
            attr = mesh.attributes.new('mme_' + key, 'INT', 'EDGE')
            for item, value in zip(attr.data, values[key]):
                item.value = value
        objects.append(obj)
    return objects


def joint_registry(source):
    """Build the shared joint/attachment registry used by every component."""
    grouped = {}
    for attachment in source.get('attachments', []):
        joint = attachment['source']['jointIndex']
        grouped.setdefault(joint, []).append(attachment)
    bindings, unresolved, registry = {}, {}, []
    for joint_index, joint in enumerate(source['joints']):
        attachments = grouped.get(joint_index, [])
        if len(attachments) == 1 and attachments[0].get('jobjId'):
            bindings[str(joint_index)] = attachments[0]['jobjId']
        elif attachments:
            unresolved[str(joint_index)] = (
                'external-target' if len(attachments) == 1
                and not attachments[0].get('jobjId') else 'multiple-bindings')
        registry.append({'index': joint_index, 'id': joint['id'],
                         'attachments': attachments,
                         'previewJobjId': bindings.get(str(joint_index)),
                         'previewError': unresolved.get(str(joint_index))})
    return registry, bindings, unresolved


def _registry(scene):
    try:
        return {entry['id']: entry
                for entry in json.loads(scene.get('mme_collision_joints', '[]'))}
    except (TypeError, ValueError, KeyError):
        return {}


def _preview_jobj(scene, obj):
    entry = _registry(scene).get(obj.get('mme_collision_joint_id'))
    if entry is not None:
        return entry.get('previewJobjId')
    # Compatibility with preview-only saved files created before components.
    return obj.get('mme_collision_jobj_id')


def _expected_matrix(scene, obj):
    jobj_id = _preview_jobj(scene, obj)
    if not jobj_id:
        return Matrix.Identity(4)
    matches = [(armature, bone) for armature in scene.objects
               if armature.type == 'ARMATURE'
               and armature.get('mme_session_id') == scene.mme_session_id
               and armature.get('mme_role') == 'jobj-armature'
               for bone in armature.pose.bones
               if bone.get('mme_id') == jobj_id]
    if len(matches) != 1:
        raise StageError('A moving-collision JOBJ target is missing or duplicated. Re-import the DAT.')
    armature, bone = matches[0]
    return armature.matrix_world @ bone.matrix


def _matrix_close(first, second, tolerance=0.000001):
    return all(abs(first[row][column] - second[row][column]) <= tolerance
               for row in range(4) for column in range(4))


def _stored_matrix(obj):
    try:
        values = json.loads(obj.get('mme_collision_managed_matrix', 'null'))
        if not isinstance(values, list) or len(values) != 4:
            return None
        return Matrix(values)
    except (TypeError, ValueError):
        return None


def update_component_transforms(scene):
    """Update JOBJ preview poses without discarding user object transforms."""
    for obj in collision_objects(scene):
        expected = _expected_matrix(scene, obj)
        previous = _stored_matrix(obj)
        # Preserve the user's transform relative to the previous managed JOBJ
        # pose when animation or an edited JOBJ changes that pose.
        user_transform = (previous.inverted_safe() @ obj.matrix_world
                          if previous is not None else Matrix.Identity(4))
        target = expected @ user_transform
        if not _matrix_close(target, obj.matrix_world):
            obj.matrix_world = target
        obj['mme_collision_managed_matrix'] = json.dumps(
            [[expected[row][column] for column in range(4)] for row in range(4)])


def component_transform(scene, obj):
    """Return the object-space edit to bake into joint-local collision vertices."""
    return _expected_matrix(scene, obj).inverted_safe() @ obj.matrix_world


def collision_objects(scene):
    return sorted((obj for obj in bpy.data.objects
                   if obj.get('mme_role') == 'collision'
                   and obj.get('mme_session_id') == scene.mme_session_id),
                  key=lambda obj: (obj.get('mme_collision_joint', 0),
                                   obj.get('mme_collision_component_id', '')))


def attachment_transforms(scene, obj):
    """Resolve serialized one-to-one collision bindings to current Blender poses."""
    return {candidate['mme_collision_joint'] - 1: candidate.matrix_world.copy()
            for candidate in collision_objects(scene)
            if _preview_jobj(scene, candidate)}


def display_edge_points(scene, obj, edge, transforms=None):
    """Return an edge's evaluated viewport endpoints in source direction."""
    transforms = transforms if transforms is not None else attachment_transforms(scene, obj)
    vertex = obj.data.attributes.get('mme_vertex')
    start = obj.data.attributes.get('mme_start')
    joint = obj.data.attributes.get('mme_joint')
    if vertex is None or start is None or joint is None:
        raise StageError('Collision attributes are missing. Re-import the DAT.')
    matrix = obj.matrix_world
    points = [matrix @ obj.data.vertices[index].co for index in edge.vertices]
    if vertex.data[edge.vertices[0]].value != start.data[edge.index].value:
        points.reverse()
    return points


def _subset_mesh(mesh, vertex_indices, edge_indices, name):
    """Copy one identity-preserving edge island into a new mesh datablock."""
    ordered_vertices = sorted(vertex_indices)
    local = {source_index: index
             for index, source_index in enumerate(ordered_vertices)}
    ordered_edges = sorted(edge_indices)
    result = bpy.data.meshes.new(name)
    result.from_pydata(
        [mesh.vertices[index].co.copy() for index in ordered_vertices],
        [(local[mesh.edges[index].vertices[0]],
          local[mesh.edges[index].vertices[1]]) for index in ordered_edges], [])
    for attribute_name, domain, indices in (
            ('mme_vertex', 'POINT', ordered_vertices),
            *((f'mme_{key}', 'EDGE', ordered_edges) for key in ATTRS)):
        source_attribute = mesh.attributes.get(attribute_name)
        if source_attribute is None or source_attribute.domain != domain \
                or source_attribute.data_type != 'INT':
            bpy.data.meshes.remove(result)
            raise StageError(
                f'Collision attribute missing or changed: {attribute_name[4:]}. '
                'Undo the edit or re-import.')
        target_attribute = result.attributes.new(attribute_name, 'INT', domain)
        for item, source_index in zip(target_attribute.data, indices):
            item.value = source_attribute.data[source_index].value
    return result


def _mesh_islands(obj, source, allow_mixed):
    mesh = obj.data
    if mesh.polygons:
        raise StageError('Collision must contain edges only; delete faces before separating components.')
    vertex_attr = mesh.attributes.get('mme_vertex')
    joint_attr = mesh.attributes.get('mme_joint')
    if vertex_attr is None or joint_attr is None:
        raise StageError('Collision identities are missing. Undo the edit or re-import.')
    edges_by_joint = {}
    for edge in mesh.edges:
        edges_by_joint.setdefault(joint_attr.data[edge.index].value, []).append(edge.index)
    owner = obj.get('mme_collision_joint')
    if not allow_mixed and any(joint != owner for joint in edges_by_joint):
        raise StageError(
            f'{obj.name}: collision geometry from different joints cannot be joined. '
            'Undo the native Join and use Connect Vertices within one joint.')
    islands = []
    used_vertices = set()
    for joint in sorted(edges_by_joint):
        remaining = set(edges_by_joint[joint])
        by_vertex = {}
        for edge_index in remaining:
            for vertex_index in mesh.edges[edge_index].vertices:
                by_vertex.setdefault(vertex_index, []).append(edge_index)
        while remaining:
            stack = [min(remaining)]
            component_edges, component_vertices = set(), set()
            while stack:
                edge_index = stack.pop()
                if edge_index not in remaining:
                    continue
                remaining.remove(edge_index)
                component_edges.add(edge_index)
                for vertex_index in mesh.edges[edge_index].vertices:
                    component_vertices.add(vertex_index)
                    stack.extend(by_vertex[vertex_index])
            used_vertices.update(component_vertices)
            islands.append((joint, component_vertices, component_edges))
    for vertex_index in sorted(set(range(len(mesh.vertices))) - used_vertices):
        joint = owner
        if not isinstance(joint, int):
            handle = vertex_attr.data[vertex_index].value
            owners = []
            if 1 <= handle <= len(source['vertices']):
                source_index = handle - 1
                owners = [index + 1 for index, record in enumerate(source['joints'])
                          if record['source']['vertexStart'] <= source_index
                          < record['source']['vertexStart'] + record['source']['vertexCount']]
            if len(owners) != 1:
                raise StageError(
                    'A legacy isolated collision vertex has ambiguous joint ownership. '
                    'Connect or remove it before converting this saved scene.')
            joint = owners[0]
        islands.append((joint, {vertex_index}, set()))
    def order(item):
        joint, vertices, edges = item
        edge_handles = [mesh.attributes['mme_line'].data[index].value
                        for index in edges]
        vertex_handles = [vertex_attr.data[index].value for index in vertices]
        return joint, min(edge_handles or [1 << 30]), min(vertex_handles)
    return sorted(islands, key=order)


def normalize_components(scene, source, legacy=False):
    """Repartition disconnected islands at an explicit, undo-safe boundary."""
    changed = 0
    for obj in list(collision_objects(scene)):
        if obj.mode == 'EDIT':
            raise StageError('Exit collision Edit Mode before normalizing components.')
        islands = _mesh_islands(obj, source, legacy)
        old_mesh = obj.data
        if not islands:
            bpy.data.objects.remove(obj, do_unlink=True)
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)
            changed += 1
            continue
        if len(islands) == 1 and obj.get('mme_collision_component_id') \
                and obj.get('mme_collision_joint') == islands[0][0]:
            continue
        collections = list(obj.users_collection)
        matrix = obj.matrix_world.copy()
        # Legacy moving-preview scenes hid the source mesh internally. That
        # marker is not a user visibility choice and must not hide converted
        # authoritative components.
        hidden = obj.hide_get() and not obj.get('mme_collision_source_hidden')
        viewport_hidden = obj.hide_viewport
        properties = {key: obj[key] for key in obj.keys()}
        created = []
        for island_index, (joint, vertices, edges) in enumerate(islands):
            if not 1 <= joint <= len(source['joints']):
                raise StageError(f'{obj.name}: collision joint identity changed.')
            component_id = (obj.get('mme_collision_component_id')
                            if island_index == 0 else None) or uuid.uuid4().hex
            mesh = _subset_mesh(old_mesh, vertices, edges, 'Collision Component')
            if island_index == 0:
                target = obj
                target.data = mesh
            else:
                target = bpy.data.objects.new('Component', mesh)
                for collection in collections:
                    collection.objects.link(target)
                for key, value in properties.items():
                    target[key] = value
            target['mme_collision_component_id'] = component_id
            target['mme_collision_joint'] = joint
            target['mme_collision_joint_id'] = source['joints'][joint-1]['id']
            target['mme_id'] = f'collision-component:{component_id}'
            for obsolete in ('mme_collision_source_hidden',
                             'mme_collision_bindings',
                             'mme_collision_unresolved_bindings',
                             'mme_collision_jobj_id',
                             'mme_collision_binding_error'):
                if obsolete in target:
                    del target[obsolete]
            target.matrix_world = matrix
            target.show_in_front = True
            target.display_type = 'WIRE'
            target.hide_viewport = viewport_hidden
            target.hide_set(hidden)
            target.name = 'Collision Component'
            created.append(target)
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
        changed += max(1, len(created) - 1)
    update_component_transforms(scene)
    return changed


def ensure_component_representation(scene, source):
    """Convert pre-component saved scenes without replacing pending edits."""
    if int(scene.get('mme_collision_representation_version', 0)) >= 2:
        return False
    registry, _, _ = joint_registry(source)
    scene['mme_collision_joints'] = json.dumps(registry)
    normalize_components(scene, source, legacy=True)
    scene['mme_collision_representation_version'] = 2
    objects = collision_objects(scene)
    vertex_handles = [item.value for obj in objects
                      if obj.data.attributes.get('mme_vertex')
                      for item in obj.data.attributes['mme_vertex'].data]
    line_handles = [item.value for obj in objects
                    if obj.data.attributes.get('mme_line')
                    for item in obj.data.attributes['mme_line'].data]
    scene['mme_collision_next_vertex'] = max(
        [len(source['vertices']), *vertex_handles]) + 1
    scene['mme_collision_next_line'] = max(
        [len(source['lines']), *line_handles]) + 1
    scene['mme_collision_fingerprint'] = fingerprint_components(objects, scene)
    return True


def serialize(obj, source, coordinate_matrix=None):
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
    if coordinate_matrix is not None:
        coords = [coordinate_matrix @ coordinate for coordinate in coords]
    if face_count:
        raise StageError('Collision must contain edges only; delete faces before exporting.')

    handles = values('vertex', 'POINT')
    attrs = {name: values(name, 'EDGE') for name in ATTRS}
    if any(h <= 0 for h in handles + attrs['line']) or len(set(handles)) != len(handles) or len(set(attrs['line'])) != len(attrs['line']):
        raise StageError('Collision identities were lost or duplicated. Undo the native topology edit and use the collision tools.')
    vertices = []
    for co, handle in zip(coords, handles):
        if not all(math.isfinite(x) for x in co) or abs(co.y) > 0.00001:
            raise StageError(
                f'Collision vertex {handle} must stay on the joint-local X/Z plane. '
                'Undo any Object Mode Y movement or X/Z-axis rotation.')
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


def serialize_components(objects, source, scene=None):
    vertices = {}
    lines = {}
    component_ids = set()
    for obj in objects:
        if obj.type != 'MESH' or obj.get('mme_role') != 'collision':
            raise StageError('Collision component inventory contains an invalid object.')
        component_id = obj.get('mme_collision_component_id')
        if not component_id or component_id in component_ids:
            raise StageError('Collision component identity is missing or duplicated. Undo the native duplicate or re-import.')
        component_ids.add(component_id)
        joint = obj.get('mme_collision_joint')
        if not isinstance(joint, int) or not 1 <= joint <= len(source['joints']) \
                or obj.get('mme_collision_joint_id') != source['joints'][joint-1]['id']:
            raise StageError(f'{obj.name}: collision joint ownership changed. Re-import the DAT.')
        coordinate_matrix = None
        if scene is not None:
            if obj.name not in scene.objects or not obj.users_collection:
                raise StageError(f'{obj.name}: collision component was unlinked rather than deleted.')
            coordinate_matrix = component_transform(scene, obj)
        if obj.matrix_parent_inverse != Matrix.Identity(4) or obj.parent is not None \
                or obj.modifiers or obj.constraints:
            raise StageError(
                f'{obj.name}: collision components cannot use parenting, modifiers, or constraints.')
        payload = serialize(obj, source, coordinate_matrix)
        for vertex in payload['vertices']:
            previous = vertices.get(vertex['id'])
            if previous is not None and previous != vertex:
                raise StageError('A collision vertex identity has conflicting coordinates across components.')
            vertices[vertex['id']] = vertex
        for line in payload['lines']:
            if line['jointId'] != obj.get('mme_collision_joint_id'):
                raise StageError(
                    f'{obj.name}: collision geometry from different joints cannot be joined. '
                    'Undo the native Join and use Connect Vertices within one joint.')
            if line['id'] in lines:
                raise StageError('A collision edge identity is duplicated across components.')
            lines[line['id']] = line
    return {'protocolVersion': SESSION_PROTOCOL, 'coordinateSpace': 'game',
            'vertices': sorted(vertices.values(), key=lambda item: item['id']),
            'lines': sorted(lines.values(), key=lambda item: item['id'])}


def fingerprint(obj, coordinate_matrix=None):
    # Do not update_from_editmode inside a dependency-graph handler.
    if obj.mode == 'EDIT':
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        coordinates = [v.co.copy() for v in bm.verts]
        values = {'edges': len(bm.edges), 'faces': len(bm.faces)}
        for key in ATTRS:
            layer = bm.edges.layers.int.get('mme_' + key)
            values[key] = [e[layer] for e in bm.edges] if layer is not None else None
    else:
        coordinates = [v.co.copy() for v in obj.data.vertices]
        values = {'edges': len(obj.data.edges), 'faces': len(obj.data.polygons)}
        for key in ATTRS:
            attr = obj.data.attributes.get('mme_' + key)
            values[key] = [e.value for e in attr.data] if attr is not None and attr.data_type == 'INT' else None
    if coordinate_matrix is not None:
        coordinates = [coordinate_matrix @ coordinate
                       for coordinate in coordinates]
    values['vertices'] = [list(coordinate) for coordinate in coordinates]
    try:
        return digest(values)
    except ValueError:
        return 'invalid-coordinates'


def fingerprint_components(objects, scene=None):
    return digest([(obj.get('mme_collision_component_id'),
                    fingerprint(obj, component_transform(scene, obj)
                                if scene is not None else None))
                   for obj in sorted(objects,
                                     key=lambda item: item.get('mme_collision_component_id', ''))])


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
