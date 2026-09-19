"""One editable rigid mesh; export uses a single grey material for all faces."""
import json
import bpy
import bmesh
from .protocol import StageError, digest


def target_info(scene):
    return json.loads(scene.get('mme_editable_mesh', 'null'))


def target_object(scene):
    info = target_info(scene)
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
    info = target_info(scene)
    if info != stage.get('editableMesh'):
        # Old scenes/sessions remain collision-only; do not silently relax their guards.
        if info is not None:
            raise StageError('Editable model identity changed. Re-import the stage.')
        return None
    if info is None:
        return None
    obj = target_object(scene)
    changed = fingerprint(obj) != scene.get('mme_model_baseline')
    obj['mme_dirty'] = changed
    if not changed:
        return None
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    # A copy exposes synchronized edit-mode data without changing the user's mesh.
    mesh = obj.data.copy()
    try:
        mesh.calc_loop_triangles()
        if not mesh.loop_triangles or len(mesh.loop_triangles) > info['maxTriangles'] or len(mesh.vertices) > 65535:
            raise StageError(f"Editable model needs triangles (maximum {info['maxTriangles']} triangles and 65535 vertices).")
        positions = [{'x': v.co.x, 'y': v.co.z, 'z': -v.co.y} for v in mesh.vertices]
        return {'protocolVersion': 2, 'coordinateSpace': 'game-joint-local', 'meshes': [
            {'id': info['id'], 'positions': positions,
             'triangleIndices': [i for triangle in mesh.loop_triangles for i in triangle.vertices]}]}
    finally:
        bpy.data.meshes.remove(mesh)


def update_dirty(scene, depsgraph):
    if not target_info(scene):
        return
    obj = target_object(scene)
    if not any(update.id.original in (obj, obj.data) for update in depsgraph.updates):
        return
    try:
        changed = fingerprint(obj) != scene.get('mme_model_baseline')
    except ValueError:
        changed = True
    if bool(obj.get('mme_dirty')) != changed:
        obj['mme_dirty'] = changed
