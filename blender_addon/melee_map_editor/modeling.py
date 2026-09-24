"""Rigid edits preserve source appearance when vertex count and faces are unchanged."""
import json
from pathlib import Path
import bpy
import bmesh
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
        changed = (fingerprint(obj) != baseline.get(info['id'])
                   or surface.fingerprint(obj, appearance_locked(info))
                   != appearance_baseline.get(info['id'], digest(None)))
        obj['mme_dirty'] = changed
        if changed or color_fingerprint(obj) != color_baseline.get(info['id']):
            source = read(Path(bpy.path.abspath(scene.mme_session)) / info['file'])
            edit = mesh_edit(obj, info, source, stage,
                             surface.fingerprint(obj, appearance_locked(info))
                             != appearance_baseline.get(info['id'], digest(None)))
            changed = changed or 'colors0' in edit or 'colors1' in edit
            obj['mme_dirty'] = changed
            if changed:
                meshes.append(edit)
    return ({'protocolVersion': SESSION_PROTOCOL, 'coordinateSpace': 'game-joint-local',
             'meshes': meshes, 'deletedIds': deleted_ids}
            if meshes or deleted_ids else None)


def mesh_edit(obj, info, source=None, stage=None, appearance_changed=True):
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    # A copy exposes synchronized edit-mode data without changing the user's mesh.
    mesh = obj.data.copy()
    try:
        mesh.calc_loop_triangles()
        if not mesh.loop_triangles or len(mesh.loop_triangles) > info['maxTriangles'] or len(mesh.vertices) > 65535:
            raise StageError(f"Editable model needs triangles (maximum {info['maxTriangles']} triangles and 65535 vertices).")
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
        require_operation(info, 'vertexMovement')
        if not same_topology:
            require_operation(info, 'topologyReplacement')
        if appearance_changed:
            require_operation(info, 'materialAssignment')
        material = surface.assigned_material(mesh, stage or {})
        result = {'id': info['id'], 'positions': positions,
                  'triangleIndices': original_indices if same_topology else
                      [i for triangle in mesh.loop_triangles for i in triangle.vertices]}
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
                   != appearance_baseline.get(info['id'], digest(None)))
            if info['id'] in color_baseline:
                changed = changed or color_fingerprint(obj) != color_baseline[info['id']]
        except ValueError:
            changed = True
        if bool(obj.get('mme_dirty')) != changed:
            obj['mme_dirty'] = changed
