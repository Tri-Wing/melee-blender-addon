"""Visual editing for typed map_head general points and stage boundaries."""
import json
import math
import uuid
import bpy
from mathutils import Vector
from .protocol import SESSION_PROTOCOL, StageError

FIRST_ITEM_SPAWN = 0x7F
LAST_ITEM_SPAWN = 0x93

PLAYER_COLORS = {
    1: (0.9, 0.08, 0.08, 1.0),
    2: (0.08, 0.28, 0.95, 1.0),
    3: (0.95, 0.75, 0.05, 1.0),
    4: (0.08, 0.75, 0.22, 1.0),
}
BOUND_COLORS = {
    'camera-boundary': (0.1, 0.8, 1.0, 1.0),
    'blast-zone': (1.0, 0.2, 0.05, 1.0),
}
SELECTION_COLOR = (1.0, 0.32, 0.02, 1.0)


def _game_vector(value):
    return Vector((value['x'], -value['z'], value['y']))


def _point_marker(group, point, tag, created_objects=None, created_meshes=None,
                  added=False):
    kind = point['kind']
    player = point.get('player')
    if kind == 'player-spawn':
        vertices = [(0, 0, 4), (4, 0, 0), (0, 0, -4), (-4, 0, 0)]
        edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        color = PLAYER_COLORS.get(player, (0.8, 0.2, 0.8, 1))
        label = f'P{player} Spawn'
    elif kind == 'player-respawn':
        vertices = [(0, 0, 3), (3, 0, 0), (0, 0, -3), (-3, 0, 0)]
        edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        base = PLAYER_COLORS.get(player, (0.8, 0.2, 0.8, 1))
        color = tuple(min(1, component + 0.2) for component in base[:3]) + (1,)
        label = f'P{player} Respawn'
    elif kind == 'item-spawn':
        vertices = [(-3, 0, -2), (3, 0, -2), (0, 0, 3)]
        edges = [(0, 1), (1, 2), (2, 0)]
        color = (0.75, 0.25, 1.0, 1.0)
        label = 'Item Spawn'
    else:
        return None
    mesh = bpy.data.meshes.new(f'{label} Marker')
    if created_meshes is not None:
        created_meshes.append(mesh)
    mesh.from_pydata(vertices, edges, [])
    index = point['setIndex']
    obj = bpy.data.objects.new(label, mesh)
    if created_objects is not None:
        created_objects.append(obj)
    group.objects.link(obj)
    tag(obj, 'gameplay-point', point['id'], index)
    obj.color = color
    obj.show_in_front = True
    obj.hide_render = True
    obj.location = _game_vector(point['position'])
    obj.lock_rotation = (True, True, True)
    obj.lock_scale = (True, True, True)
    obj['mme_gameplay_editable'] = bool(point.get('editable'))
    obj['mme_gameplay_kind'] = kind
    obj['mme_gameplay_type_id'] = point['typeId']
    if player is not None:
        obj['mme_gameplay_player'] = player
    obj['mme_source_points'] = json.dumps(
        {} if added else {point['id']: point['position']}, sort_keys=True)
    if added:
        obj['mme_gameplay_added'] = True
    if point.get('readOnlyReason'):
        obj['mme_read_only_reason'] = point['readOnlyReason']
    return obj


def create(parent, stage, tag, created_objects, created_meshes, collection):
    """Create transformable diamond markers and rectangle guides."""
    gameplay = stage.get('gameplay') or {}
    sets = gameplay.get('sets') or []
    if not sets:
        return []
    root = collection('Gameplay', parent, 'gameplay', 'gameplay')
    result = []
    for source_set in sets:
        index = source_set['index']
        group = collection('Gameplay Set', root,
                           f'gameplay-set-{index:03d}', 'gameplay-set', index)
        points = {point['id']: point for point in source_set.get('points', [])}
        consumed = set()
        for bounds in source_set.get('bounds', []):
            first = points[bounds['firstPointId']]
            second = points[bounds['secondPointId']]
            consumed.update((first['id'], second['id']))
            a, b = first['position'], second['position']
            editable = bool(bounds.get('editable')) and abs(a['z'] - b['z']) <= 1e-5
            reason = bounds.get('readOnlyReason')
            if bounds.get('editable') and not editable:
                reason = 'Boundary corners use different depth values; rectangle editing is unavailable.'
            mesh = bpy.data.meshes.new('Gameplay Bounds Guide')
            created_meshes.append(mesh)
            mesh.from_pydata([(-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1)],
                             [(0, 1), (1, 2), (2, 3), (3, 0)], [])
            color = BOUND_COLORS[bounds['kind']]
            label = 'Camera Bounds' if bounds['kind'] == 'camera-boundary' else 'Blast Zone'
            obj = bpy.data.objects.new(label, mesh)
            created_objects.append(obj)
            group.objects.link(obj)
            tag(obj, 'gameplay-bounds', bounds['id'], index)
            obj.color = color
            obj.show_in_front = True
            obj.hide_render = True
            obj.display_type = 'WIRE'
            obj.location = ((a['x'] + b['x']) / 2,
                            -(a['z'] + b['z']) / 2,
                            (a['y'] + b['y']) / 2)
            obj.scale = (abs(a['x'] - b['x']) / 2, 1,
                         abs(a['y'] - b['y']) / 2)
            obj.lock_rotation = (True, True, True)
            obj.lock_scale[1] = True
            obj['mme_gameplay_editable'] = editable
            obj['mme_gameplay_kind'] = bounds['kind']
            obj['mme_gameplay_point_ids'] = json.dumps(
                [first['id'], second['id']])
            obj['mme_first_x_sign'] = -1 if a['x'] < b['x'] else 1
            obj['mme_first_y_sign'] = -1 if a['y'] < b['y'] else 1
            obj['mme_source_points'] = json.dumps(
                {first['id']: a, second['id']: b}, sort_keys=True)
            obj['mme_source_scale_y'] = obj.scale.y
            if reason:
                obj['mme_read_only_reason'] = reason
            result.append(obj)

        for point in source_set.get('points', []):
            if point['id'] in consumed:
                continue
            point = dict(point, setIndex=index)
            obj = _point_marker(group, point, tag, created_objects,
                                created_meshes)
            if obj is not None:
                result.append(obj)
    return result


def draw():
    """Draw guide edges with the same GPU overlay style as collision lines."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    context = bpy.context
    space = context.space_data
    if (not context.scene.mme_session or space is None
            or not space.overlay.show_overlays):
        return
    try:
        batches = {}
        selected_lines = []
        for obj in objects(context.scene):
            if obj.type != 'MESH' or not obj.visible_get() or obj.hide_get():
                continue
            coordinates = batches.setdefault(tuple(obj.color), [])
            for edge in obj.data.edges:
                points = [obj.matrix_world @ obj.data.vertices[index].co
                          for index in edge.vertices]
                coordinates.extend(points)
                if obj.select_get():
                    selected_lines.extend(points)
        if not batches:
            return
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        depth = gpu.state.depth_test_get()
        width = gpu.state.line_width_get()
        try:
            gpu.state.depth_test_set('NONE')
            shader.bind()
            if selected_lines:
                gpu.state.line_width_set(6)
                shader.uniform_float('color', SELECTION_COLOR)
                batch_for_shader(shader, 'LINES',
                                 {'pos': selected_lines}).draw(shader)
            gpu.state.line_width_set(2)
            for color, coordinates in batches.items():
                shader.uniform_float('color', color)
                batch_for_shader(shader, 'LINES', {'pos': coordinates}).draw(shader)
        finally:
            gpu.state.depth_test_set(depth)
            gpu.state.line_width_set(width)
    except (StageError, ReferenceError):
        pass


def objects(scene):
    return [obj for obj in scene.objects
            if obj.get('mme_session_id') == scene.mme_session_id
            and obj.get('mme_role') in ('gameplay-point', 'gameplay-bounds')]


def editable_transform_ids(scene):
    return {obj.get('mme_id') for obj in objects(scene)
            if obj.get('mme_gameplay_editable')}


def is_pending(obj):
    return bool(obj and obj.get('mme_role') == 'gameplay-point'
                and obj.get('mme_gameplay_added'))


def deletable_ids(scene, stage=None):
    if stage is not None:
        return {point['id'] for source_set in
                (stage.get('gameplay') or {}).get('sets', [])
                if source_set.get('itemSpawnTopologyEditable')
                for point in source_set.get('points', [])
                if point.get('editable') and point.get('kind') == 'item-spawn'}
    return {obj.get('mme_id') for obj in objects(scene)
            if not is_pending(obj) and obj.get('mme_gameplay_editable')
            and obj.get('mme_gameplay_kind') == 'item-spawn'}


def set_index(scene, stage, active=None):
    sets = (stage.get('gameplay') or {}).get('sets', [])
    indexes = {source_set['index'] for source_set in sets}
    active = active or bpy.context.active_object
    if (active and active.get('mme_session_id') == scene.mme_session_id
            and active.get('mme_group_index') in indexes
            and active.get('mme_role') in ('gameplay-point', 'gameplay-bounds')):
        return active['mme_group_index']
    selected = scene.mme_gameplay_set_index
    if selected in indexes:
        return selected
    if len(indexes) == 1:
        return next(iter(indexes))
    raise StageError('Select a gameplay guide or choose a valid general-point set.')


def item_objects(scene, index):
    return [obj for obj in objects(scene)
            if obj.get('mme_role') == 'gameplay-point'
            and obj.get('mme_gameplay_kind') == 'item-spawn'
            and obj.get('mme_group_index') == index]


def item_spawn_slot(scene, index):
    retained = {obj.get('mme_gameplay_type_id') for obj in item_objects(scene, index)
                if not is_pending(obj)}
    free = [value for value in range(FIRST_ITEM_SPAWN, LAST_ITEM_SPAWN + 1)
            if value not in retained]
    pending = sorted((obj for obj in item_objects(scene, index) if is_pending(obj)),
                     key=lambda obj: (obj.get('mme_gameplay_add_order', 0),
                                      obj.get('mme_id', '')))
    for obj, value in zip(pending, free):
        obj['mme_gameplay_type_id'] = value
    return free[len(pending)] if len(pending) < len(free) else None


def plane_y(scene, index):
    points = item_objects(scene, index)
    if not points:
        points = [obj for obj in objects(scene)
                  if obj.get('mme_role') == 'gameplay-point'
                  and obj.get('mme_group_index') == index]
    values = sorted(obj.location.y for obj in points)
    if not values:
        return 0.0
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def project_to_plane(ray_origin, ray_direction, y):
    origin = Vector(ray_origin)
    direction = Vector(ray_direction)
    if not all(math.isfinite(value) for value in (*origin, *direction, y)) \
            or direction.length_squared == 0:
        raise StageError('The viewport produced an invalid placement ray.')
    if abs(direction.y) <= 0.000001:
        raise StageError('View the gameplay plane more face-on before placing an item spawn.')
    distance = (y - origin.y) / direction.y
    if distance < 0:
        raise StageError('The gameplay plane is behind the current view.')
    value = origin + direction * distance
    value.y = y
    if not all(math.isfinite(component) for component in value):
        raise StageError('The projected item-spawn position is invalid.')
    return value


def add_item_spawn(scene, stage, index, type_id, location):
    source_set = next((entry for entry in (stage.get('gameplay') or {}).get('sets', [])
                       if entry['index'] == index), None)
    if not source_set or not source_set.get('itemSpawnTopologyEditable'):
        reason = (source_set or {}).get('itemSpawnTopologyReadOnlyReason')
        raise StageError(reason or 'Item-spawn topology editing is unavailable for this set.')
    if type_id != item_spawn_slot(scene, index):
        raise StageError('The requested item spawn is not the lowest unused slot.')
    group = next((collection for collection in bpy.data.collections
                  if collection.get('mme_session_id') == scene.mme_session_id
                  and collection.get('mme_role') == 'gameplay-set'
                  and collection.get('mme_group_index') == index), None)
    if group is None:
        raise StageError('The target gameplay collection is missing.')
    sequence = int(scene.get('mme_gameplay_add_sequence', 0)) + 1
    scene['mme_gameplay_add_sequence'] = sequence
    identity = uuid.uuid4().hex
    value = Vector(location)
    point = {'id': identity, 'setIndex': index, 'typeId': type_id,
             'kind': 'item-spawn', 'editable': True,
             'position': {'x': value.x, 'y': value.z, 'z': -value.y}}

    def tag(item, role, key, group_index):
        item['mme_role'] = role
        item['mme_session_id'] = scene.mme_session_id
        item['mme_id'] = key
        item['mme_group_index'] = group_index
        item['mme_source_hash'] = group.get('mme_source_hash', '')

    obj = _point_marker(group, point, tag, added=True)
    obj['mme_gameplay_add_order'] = sequence
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def edits(scene, stage):
    source_sets = (stage.get('gameplay') or {}).get('sets', [])
    source_points = {point['id']: dict(point, setIndex=source_set['index'])
                     for source_set in source_sets
                     for point in source_set.get('points', [])}
    if not source_sets:
        return None
    source_by_set = {source_set['index']: source_set for source_set in source_sets}
    expected = {}
    for source_set in source_sets:
        consumed = {identity for bounds in source_set.get('bounds', [])
                    for identity in (bounds['firstPointId'], bounds['secondPointId'])}
        expected.update({bounds['id']: ('bounds', source_set['index'])
                         for bounds in source_set.get('bounds', [])})
        expected.update({point['id']: (point['kind'], source_set['index'])
                         for point in source_set.get('points', [])
                         if point['id'] not in consumed and point['kind'] in
                         ('player-spawn', 'player-respawn', 'item-spawn')})
    current = objects(scene)
    if len({obj.get('mme_id') for obj in current}) != len(current):
        raise StageError('The gameplay guide inventory contains duplicated identities.')
    imported = {obj.get('mme_id'): obj for obj in current if not is_pending(obj)}
    if any(identity not in expected for identity in imported):
        raise StageError('The imported gameplay guide inventory contains an unknown object.')
    missing = set(expected) - set(imported)
    deletions = []
    for identity in missing:
        kind, index = expected[identity]
        point = source_points.get(identity)
        source_set = source_by_set[index]
        if (kind != 'item-spawn' or not point or not point.get('editable')
                or not source_set.get('itemSpawnTopologyEditable')):
            raise StageError('Only editable item-spawn markers can be deleted.')
        deletions.append(identity)

    compiled = {}
    for obj in imported.values():
        _validate_transform(obj)
        if not obj.get('mme_gameplay_editable'):
            continue
        sources = json.loads(obj['mme_source_points'])
        if obj.get('mme_role') == 'gameplay-bounds':
            first_id, second_id = json.loads(obj['mme_gameplay_point_ids'])
            half_x, half_y = abs(obj.scale.x), abs(obj.scale.z)
            if half_x <= 1e-5 or half_y <= 1e-5:
                raise StageError(f'{obj.name}: boundary width and height must be nonzero.')
            first_x = obj.location.x + obj['mme_first_x_sign'] * half_x
            first_y = obj.location.z + obj['mme_first_y_sign'] * half_y
            values = {
                first_id: {'x': first_x, 'y': first_y, 'z': -obj.location.y},
                second_id: {'x': 2 * obj.location.x - first_x,
                            'y': 2 * obj.location.z - first_y,
                            'z': -obj.location.y},
            }
        else:
            identity = obj.get('mme_id')
            values = {identity: {'x': obj.location.x, 'y': obj.location.z,
                                 'z': -obj.location.y}}
        for identity, value in values.items():
            if _different(value, sources[identity]):
                compiled[identity] = {'id': identity, 'position': value}

    additions = []
    pending_by_set = {}
    for obj in current:
        if not is_pending(obj):
            continue
        _validate_transform(obj)
        index = obj.get('mme_group_index')
        source_set = source_by_set.get(index)
        if (obj.get('mme_gameplay_kind') != 'item-spawn' or not source_set
                or not source_set.get('itemSpawnTopologyEditable')):
            raise StageError('An added gameplay marker has an unsupported target set or kind.')
        pending_by_set.setdefault(index, []).append(obj)
    for index, pending in pending_by_set.items():
        retained = {obj.get('mme_gameplay_type_id') for obj in item_objects(scene, index)
                    if not is_pending(obj)}
        free = [value for value in range(FIRST_ITEM_SPAWN, LAST_ITEM_SPAWN + 1)
                if value not in retained]
        pending.sort(key=lambda obj: (obj.get('mme_gameplay_add_order', 0),
                                      obj.get('mme_id', '')))
        if len(pending) > len(free):
            raise StageError('A general-point set cannot exceed 21 item spawns.')
        for obj, type_id in zip(pending, free):
            additions.append({'id': obj['mme_id'], 'setIndex': index,
                              'typeId': type_id,
                              'position': {'x': obj.location.x,
                                           'y': obj.location.z,
                                           'z': -obj.location.y}})
    points = sorted(compiled.values(), key=lambda entry: entry['id'])
    additions.sort(key=lambda entry: (entry['setIndex'], entry['typeId'], entry['id']))
    deletions.sort()
    return ({'protocolVersion': SESSION_PROTOCOL, 'coordinateSpace': 'game',
             'points': points, 'additions': additions, 'deletions': deletions}
            if points or additions or deletions else None)


def dirty(scene, stage):
    payload = edits(scene, stage)
    return bool(payload)


def _validate_transform(obj):
    if any(abs(value) > 1e-6 for value in obj.rotation_euler):
        raise StageError(f'{obj.name}: gameplay guides cannot be rotated.')
    if obj.get('mme_role') == 'gameplay-point':
        if any(abs(value - 1) > 1e-6 for value in obj.scale):
            raise StageError(f'{obj.name}: marker scale is visual-only and cannot be changed.')
    elif abs(obj.scale.y - obj.get('mme_source_scale_y', 1)) > 1e-6:
        raise StageError(f'{obj.name}: boundary depth scale cannot be changed.')
    if not all(math.isfinite(value) for value in (*obj.location, *obj.scale)):
        raise StageError(f'{obj.name}: gameplay transforms must be finite.')


def _different(a, b):
    return any(abs(a[key] - b[key]) > 1e-5 * max(1, abs(a[key]), abs(b[key]))
               for key in ('x', 'y', 'z'))
