"""Rigid edits preserve source appearance when vertex count and faces are unchanged."""
import json
from pathlib import Path
import bpy
import bmesh
from .protocol import StageError, digest, read
from . import surface


def target_info(scene):
    return json.loads(scene.get('mme_editable_mesh', 'null'))


def stage_targets(stage):
    if 'editableMeshes' in stage:
        return stage['editableMeshes']
    return [stage['editableMesh']] if stage.get('editableMesh') else []


def targets(scene):
    if 'mme_editable_meshes' in scene:
        return json.loads(scene['mme_editable_meshes'])
    info = target_info(scene)
    return [info] if info else []


def target_ids(scene):
    return {info['id'] for info in targets(scene)}


def baselines(scene):
    if 'mme_model_baselines' in scene:
        return json.loads(scene['mme_model_baselines'])
    info = target_info(scene)
    return {info['id']: scene.get('mme_model_baseline')} if info else {}


def target_object(scene, info=None):
    info = info or target_info(scene)
    if not info:
        raise StageError('This scene has no editable model target. Re-import with the updated backend.')
    objects = [o for o in scene.objects if o.get('mme_session_id') == scene.mme_session_id
               and o.get('mme_id') == info['id']]
    if len(objects) != 1 or objects[0].type != 'MESH':
        raise StageError('The editable model object is missing or duplicated. Undo the change.')
    return objects[0]


def fingerprint(obj):
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.index_update()
        vertices = [list(v.co) for v in bm.verts]
        faces = [[v.index for v in f.verts] for f in bm.faces]
    else:
        vertices = [list(v.co) for v in obj.data.vertices]
        faces = [list(f.vertices) for f in obj.data.polygons]
    return digest({'vertices': vertices, 'faces': faces})


def edits(scene, stage):
    infos = targets(scene)
    declared = stage_targets(stage)
    # Saved legacy scenes remain restricted to their original permissions.
    if 'mme_editable_meshes' not in scene:
        declared = [stage['editableMesh']] if stage.get('editableMesh') else []
    if infos and infos != declared:
        raise StageError('Editable model identities changed. Re-import the stage.')
    baseline = baselines(scene)
    appearance_baseline = json.loads(scene.get('mme_appearance_baselines', '{}'))
    meshes = []
    for info in infos:
        obj = target_object(scene, info)
        changed = (fingerprint(obj) != baseline.get(info['id'])
                   or surface.fingerprint(obj, info.get('positionsOnly', False)) != appearance_baseline.get(info['id'], digest(None)))
        obj['mme_dirty'] = changed
        if changed:
            source = read(Path(bpy.path.abspath(scene.mme_session)) / info['file'])
            meshes.append(mesh_edit(obj, info, source, stage,
                                    surface.fingerprint(obj, info.get('positionsOnly', False)) != appearance_baseline.get(info['id'], digest(None))))
    return {'protocolVersion': 2, 'coordinateSpace': 'game-joint-local', 'meshes': meshes} if meshes else None


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
        same_topology = (source is not None and len(positions) == len(source['positions'])
                         and len(mesh.polygons) * 3 == len(original_indices)
                         and all(list(face.vertices) == original_indices[i * 3:i * 3 + 3]
                                 for i, face in enumerate(mesh.polygons)))
        if info.get('positionsOnly') and (not same_topology or appearance_changed):
            raise StageError('This model has animated materials: move vertices only. Undo topology, UV or material assignment changes before export.')
        material = surface.assigned_material(mesh, stage or {})
        result = {'id': info['id'], 'positions': positions,
                  'triangleIndices': original_indices if same_topology else
                      [i for triangle in mesh.loop_triangles for i in triangle.vertices]}
        if same_topology and not appearance_changed:
            return result
        if not material and appearance_changed:
            result['useGreyMaterial'] = True
        if material:
            result['sourceMaterialId'] = material['id']
            if material['usesUv']:
                uv = mesh.uv_layers.active
                if uv is None:
                    raise StageError('This stage material needs a UV map. Unwrap the model in Blender before exporting.')
                # Same-topology faces retain original corner ordering, including
                # strip degenerates. Otherwise use Blender's triangulated loops.
                loops = ([i for face in mesh.polygons for i in face.loop_indices] if same_topology else
                         [i for triangle in mesh.loop_triangles for i in triangle.loops])
                result['texCoords'] = [{'x': uv.data[i].uv.x, 'y': 1 - uv.data[i].uv.y} for i in loops]
        return result
    finally:
        bpy.data.meshes.remove(mesh)


def update_dirty(scene, depsgraph):
    infos = targets(scene)
    if not infos:
        return
    baseline = baselines(scene)
    appearance_baseline = json.loads(scene.get('mme_appearance_baselines', '{}'))
    updates = {update.id.original for update in depsgraph.updates}
    for info in infos:
        obj = target_object(scene, info)
        if obj not in updates and obj.data not in updates:
            continue
        try:
            changed = (fingerprint(obj) != baseline.get(info['id'])
                   or surface.fingerprint(obj, info.get('positionsOnly', False)) != appearance_baseline.get(info['id'], digest(None)))
        except ValueError:
            changed = True
        if bool(obj.get('mme_dirty')) != changed:
            obj['mme_dirty'] = changed
