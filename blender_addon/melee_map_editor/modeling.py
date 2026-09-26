"""Rigid edits preserve source appearance when vertex count and faces are unchanged."""
import json
import math
from pathlib import Path
import bpy
import bmesh
from mathutils import Matrix, Vector
from .protocol import StageError, SESSION_PROTOCOL, digest, read
from . import surface

def stage_targets(stage):
    return stage['editableMeshes']


def targets(scene):
    return json.loads(scene.get('mme_editable_meshes', '[]'))


def target_ids(scene):
    return {info['id'] for info in targets(scene)}


def baselines(scene):
    return json.loads(scene.get('mme_model_baselines', '{}'))


def transform_baselines(scene):
    try:
        values = json.loads(scene.get('mme_model_transform_baselines', '{}'))
    except (TypeError, ValueError):
        values = {}
    if values:
        return values
    # Scenes imported before Object Mode transform baking already carry the
    # original matrices in their protected inventory. Reuse that immutable
    # snapshot so an add-on reload can enable the feature without rebasing an
    # existing user transform or requiring a fresh import.
    try:
        inventory = json.loads(scene.get('mme_guard_inventory', '{}'))
        values = {row['props']['mme_id']: row['matrix']
                  for row in inventory.get('objects', [])
                  if row.get('props', {}).get('mme_id') in target_ids(scene)
                  and isinstance(row.get('matrix'), list)}
    except (TypeError, ValueError, KeyError):
        values = {}
    return values


def ensure_transform_baselines(scene):
    """Upgrade a pre-feature protected inventory without rebasing transforms."""
    if scene.get('mme_model_transform_baselines'):
        return
    try:
        inventory = json.loads(scene.get('mme_guard_inventory', '{}'))
        ids = target_ids(scene)
        values = {row['props']['mme_id']: row['matrix']
                  for row in inventory.get('objects', [])
                  if row.get('props', {}).get('mme_id') in ids
                  and isinstance(row.get('matrix'), list)}
        if set(values) != ids:
            raise KeyError
        for row in inventory['objects']:
            if row.get('props', {}).get('mme_id') in ids:
                row.pop('matrix', None)
    except (TypeError, ValueError, KeyError):
        raise StageError(
            'Editable model transform baselines are missing. Re-import the DAT.')
    scene['mme_model_transform_baselines'] = json.dumps(values)
    scene['mme_guard_inventory'] = json.dumps(inventory)
    scene['mme_guard'] = digest(inventory)


def matrix_values(matrix):
    return [[matrix[row][column] for column in range(4)] for row in range(4)]


def transform_delta(scene, obj, info):
    values = transform_baselines(scene).get(info['id'])
    if not isinstance(values, list) or len(values) != 4:
        raise StageError('Editable model transform baselines are missing. Re-import the DAT.')
    baseline = Matrix(values)
    if not all(math.isfinite(value) for row in obj.matrix_basis for value in row):
        raise StageError(f'{obj.name}: object transform values must be finite.')
    return baseline.inverted_safe() @ obj.matrix_basis


def transform_changed(scene, obj, info, tolerance=0.000001):
    delta = transform_delta(scene, obj, info)
    return any(abs(delta[row][column] - (1 if row == column else 0)) > tolerance
               for row in range(4) for column in range(4))


def capability(info, operation):
    value = info.get('operationCapabilities', {}).get(operation)
    if not isinstance(value, dict) or not isinstance(value.get('allowed'), bool):
        raise StageError('Model capabilities are missing or obsolete. Re-import the DAT.')
    return value


def allows(info, operation):
    return capability(info, operation)['allowed']


def require_operation(info, operation):
    value = capability(info, operation)
    if not value['allowed']:
        raise StageError(value.get('reason') or
                         f'{operation} is unavailable for this model. Re-import the DAT.')


def appearance_locked(info):
    return not allows(info, 'materialAssignment') or not allows(info, 'uvEditing')


def resolve(scene, obj=None, info=None, operation=None):
    infos = targets(scene)
    if info is not None:
        info = next((candidate for candidate in infos
                     if candidate['id'] == info.get('id')), None)
    elif obj is not None:
        info = next((candidate for candidate in infos
                     if candidate['id'] == obj.get('mme_id')), None)
    if info is None and obj is None:
        info = infos[0] if infos else None
    if info is None:
        raise StageError('This scene has no editable model target. Re-import the DAT.')
    matches = target_matches(scene, info)
    if len(matches) != 1 or matches[0].type != 'MESH':
        raise StageError('The editable model object is missing or duplicated. Undo the change.')
    if obj is not None and matches[0] is not obj:
        raise StageError('The selected object is not this editable model target.')
    if operation:
        require_operation(info, operation)
    return info, matches[0]


def target_object(scene, info=None):
    return resolve(scene, info=info)[1]


def target_matches(scene, info):
    return [obj for obj in scene.objects
            if obj.get('mme_session_id') == scene.mme_session_id
            and obj.get('mme_id') == info['id']]


def fingerprint(obj):
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.index_update()
        vertices = [list(v.co) for v in bm.verts]
        faces = [[v.index for v in f.verts] for f in bm.faces]
    else:
        vertices = [list(v.co) for v in obj.data.vertices]
        faces = [list(f.vertices) for f in obj.data.polygons]
    return digest({'vertices': vertices, 'faces': faces})


def color_fingerprint(obj):
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    return digest({layer.name: {'domain': layer.domain, 'values': [list(value.color) for value in layer.data]}
                   for layer in obj.data.color_attributes if layer.name in ('Stage Color 0', 'Stage Color 1')})


def edits(scene, stage):
    infos = targets(scene)
    declared = stage_targets(stage)
    if infos != declared:
        raise StageError('Editable model identities changed. Re-import the stage.')
    baseline = baselines(scene)
    appearance_baseline = json.loads(scene.get('mme_appearance_baselines', '{}'))
    color_baseline = json.loads(scene.get('mme_color_baselines', '{}'))
    meshes = []
    deleted_ids = []
    for info in infos:
        matches = target_matches(scene, info)
        if not matches:
            require_operation(info, 'wholeObjectDeletion')
            deleted_ids.append(info['id'])
            continue
        if len(matches) != 1 or matches[0].type != 'MESH':
            raise StageError('The editable model object is duplicated or has an invalid type. Undo the change.')
        obj = matches[0]
        coordinate_matrix = transform_delta(scene, obj, info)
        transformed = transform_changed(scene, obj, info)
        changed = (fingerprint(obj) != baseline.get(info['id'])
                   or surface.fingerprint(obj, appearance_locked(info))
                   != appearance_baseline.get(info['id'], digest(None))
                   or transformed)
        obj['mme_dirty'] = changed
        if changed or color_fingerprint(obj) != color_baseline.get(info['id']):
            source = read(Path(bpy.path.abspath(scene.mme_session)) / info['file'])
            edit = mesh_edit(obj, info, source, stage,
                             surface.fingerprint(obj, appearance_locked(info))
                             != appearance_baseline.get(info['id'], digest(None)),
                             coordinate_matrix if transformed else None)
            changed = changed or 'colors0' in edit or 'colors1' in edit
            obj['mme_dirty'] = changed
            if changed:
                meshes.append(edit)
    return ({'protocolVersion': SESSION_PROTOCOL, 'coordinateSpace': 'game-joint-local',
             'meshes': meshes, 'deletedIds': deleted_ids}
            if meshes or deleted_ids else None)


def mesh_edit(obj, info, source=None, stage=None, appearance_changed=True,
              coordinate_matrix=None):
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    # A copy exposes synchronized edit-mode data without changing the user's mesh.
    mesh = obj.data.copy()
    try:
        if coordinate_matrix is not None:
            mesh.transform(coordinate_matrix, shape_keys=True)
            mesh.update()
        positions = [{'x': v.co.x, 'y': v.co.z, 'z': -v.co.y} for v in mesh.vertices]
        # Use the original primitive expansion, including GX strip degenerates,
        # when the indexed Blender faces still match the imported topology.
        # Re-triangulating can rotate degenerate triangle corners and would
        # incorrectly turn a vertex move into a grey topology replacement.
        original_indices = source['triangleIndices'] if source else []
        display_indices, reversed_faces = (surface.display_triangle_indices(source) if source else ([], []))
        same_topology = (source is not None and len(positions) == len(source['positions'])
                         and len(mesh.polygons) * 3 == len(original_indices)
                         and all(list(face.vertices) == display_indices[i * 3:i * 3 + 3]
                                 for i, face in enumerate(mesh.polygons)))
        reflected = (coordinate_matrix is not None
                     and coordinate_matrix.to_3x3().determinant() < 0)
        # Appearance-preserving reflections retain the imported indices in the
        # request and ask the DAT writer to reverse each source triangle. For
        # replacement topology, mirror Blender's Apply Transform behavior on
        # the temporary copy before triangulation.
        if reflected and not same_topology:
            mesh.flip_normals()
            mesh.update()
        mesh.calc_loop_triangles()
        if not mesh.loop_triangles or len(mesh.loop_triangles) > info['maxTriangles'] or len(mesh.vertices) > 65535:
            raise StageError(f"Editable model needs triangles (maximum {info['maxTriangles']} triangles and 65535 vertices).")
        require_operation(info, 'vertexMovement')
        if not same_topology:
            require_operation(info, 'topologyReplacement')
        if appearance_changed:
            require_operation(info, 'materialAssignment')
        material = surface.assigned_material(mesh, stage or {})
        result = {'id': info['id'], 'positions': positions,
                  'triangleIndices': original_indices if same_topology else
                      [i for triangle in mesh.loop_triangles for i in triangle.vertices]}
        if reflected and same_topology:
            result['reverseWinding'] = True
        if same_topology and coordinate_matrix is not None and source.get('normals'):
            normal_matrix = coordinate_matrix.to_3x3()
            if abs(normal_matrix.determinant()) < 0.000000000001:
                raise StageError(
                    f'{obj.name}: a zero-scale transform cannot preserve source normals.')
            normal_matrix = normal_matrix.inverted().transposed()
            normals = []
            for value in source['normals']:
                normal = normal_matrix @ Vector(
                    (value['x'], -value['z'], value['y']))
                if normal.length_squared < 0.000000000001:
                    raise StageError(
                        f'{obj.name}: transformed source normal has zero length.')
                normal.normalize()
                normals.append({'x': normal.x, 'y': normal.z,
                                'z': -normal.y})
            result['normals'] = normals
        for channel in range(2):
            key = f'colors{channel}'
            original_colors = source.get(key) if source else None
            if original_colors is None:
                continue
            layer = mesh.color_attributes.get(f'Stage Color {channel}')
            if layer is None:
                if same_topology:
                    raise StageError('An imported stage color attribute was removed. Restore it before export.')
                # Replacement topology cannot retain source corner colors. It
                # will use the assigned compatible stage material or grey.
                continue
            if not same_topology:
                continue
            loops = (source_order_loops(mesh, reversed_faces) if same_topology else
                     [i for triangle in mesh.loop_triangles for i in triangle.loops])
            colors = [list(layer.data[mesh.loops[i].vertex_index if layer.domain == 'POINT' else i].color) for i in loops]
            changed = (not same_topology or any(abs(value - original_colors[index][component]) > 1e-6
                       for color, index in zip(colors, original_indices)
                       for value, component in zip(color, ('r', 'g', 'b', 'a'))))
            if changed:
                require_operation(info, 'vertexColorEditing')
                if not same_topology or appearance_changed:
                    raise StageError('Vertex color export requires the original topology, UVs and material assignment.')
                result[key] = [dict(zip(('r', 'g', 'b', 'a'), color)) for color in colors]
        if same_topology and not appearance_changed:
            return result
        if not material and (appearance_changed or not same_topology):
            result['useGreyMaterial'] = True
        if material:
            result['sourceMaterialId'] = material['id']
            if material['usesUv']:
                require_operation(info, 'uvEditing')
                uv = mesh.uv_layers.active
                if uv is None:
                    raise StageError('This stage material needs a UV map. Unwrap the model in Blender before exporting.')
                # Same-topology faces retain original corner ordering, including
                # strip degenerates. Otherwise use Blender's triangulated loops.
                loops = (source_order_loops(mesh, reversed_faces) if same_topology else
                         [i for triangle in mesh.loop_triangles for i in triangle.loops])
                result['texCoords'] = [{'x': uv.data[i].uv.x, 'y': 1 - uv.data[i].uv.y} for i in loops]
        return result
    finally:
        bpy.data.meshes.remove(mesh)


def source_order_loops(mesh, reversed_faces):
    loops = []
    for face, reverse in zip(mesh.polygons, reversed_faces):
        values = list(face.loop_indices)
        loops.extend((values[0], values[2], values[1]) if reverse else values)
    return loops


def update_dirty(scene, depsgraph):
    infos = targets(scene)
    if not infos:
        return
    baseline = baselines(scene)
    appearance_baseline = json.loads(scene.get('mme_appearance_baselines', '{}'))
    color_baseline = json.loads(scene.get('mme_color_baselines', '{}'))
    updates = {update.id.original for update in depsgraph.updates}
    for info in infos:
        matches = target_matches(scene, info)
        if len(matches) != 1 or matches[0].type != 'MESH':
            continue
        obj = matches[0]
        if obj not in updates and obj.data not in updates:
            continue
        try:
            changed = (fingerprint(obj) != baseline.get(info['id'])
                   or surface.fingerprint(obj, appearance_locked(info))
                   != appearance_baseline.get(info['id'], digest(None))
                   or transform_changed(scene, obj, info))
            if info['id'] in color_baseline:
                changed = changed or color_fingerprint(obj) != color_baseline[info['id']]
        except ValueError:
            changed = True
        if bool(obj.get('mme_dirty')) != changed:
            obj['mme_dirty'] = changed
