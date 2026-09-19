"""Editable rigid meshes; export uses a single grey material for all faces."""
import json
import bpy
import bmesh
from .protocol import StageError, digest


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
    meshes = []
    for info in infos:
        obj = target_object(scene, info)
        changed = fingerprint(obj) != baseline.get(info['id'])
        obj['mme_dirty'] = changed
        if changed:
            meshes.append(mesh_edit(obj, info))
    return {'protocolVersion': 2, 'coordinateSpace': 'game-joint-local', 'meshes': meshes} if meshes else None


def mesh_edit(obj, info):
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    # A copy exposes synchronized edit-mode data without changing the user's mesh.
    mesh = obj.data.copy()
    try:
        mesh.calc_loop_triangles()
        if not mesh.loop_triangles or len(mesh.loop_triangles) > info['maxTriangles'] or len(mesh.vertices) > 65535:
            raise StageError(f"Editable model needs triangles (maximum {info['maxTriangles']} triangles and 65535 vertices).")
        positions = [{'x': v.co.x, 'y': v.co.z, 'z': -v.co.y} for v in mesh.vertices]
        return {'id': info['id'], 'positions': positions,
                'triangleIndices': [i for triangle in mesh.loop_triangles for i in triangle.vertices]}
    finally:
        bpy.data.meshes.remove(mesh)


def update_dirty(scene, depsgraph):
    infos = targets(scene)
    if not infos:
        return
    baseline = baselines(scene)
    updates = {update.id.original for update in depsgraph.updates}
    for info in infos:
        obj = target_object(scene, info)
        if obj not in updates and obj.data not in updates:
            continue
        try:
            changed = fingerprint(obj) != baseline.get(info['id'])
        except ValueError:
            changed = True
        if bool(obj.get('mme_dirty')) != changed:
            obj['mme_dirty'] = changed
