"""One supported stage material per model, with native Blender corner UVs."""
import json
import bpy
import bmesh
from .protocol import StageError, digest


def material_id(material):
    return material.get('mme_model_material_id') if material else None


def create_materials(stage):
    result = {}
    for entry in stage.get('modelMaterials', []):
        material = bpy.data.materials.new(entry['name'])
        material.diffuse_color = (0.45, 0.45, 0.45, 1)
        material['mme_model_material_id'] = entry['id']
        material['mme_model_material_source'] = stage['source']['sha256']
        material['mme_model_uses_uv'] = entry['usesUv']
        result[entry['id']] = material
    return result


def import_uvs(mesh, source):
    coordinates = source.get('texCoords0')
    if coordinates:
        uv = mesh.uv_layers.new(name='UVMap')
        for loop in mesh.loops:
            value = coordinates[loop.vertex_index]
            uv.data[loop.index].uv = (value['x'], 1 - value['y'])


def fingerprint(obj):
    slots = [material_id(m) for m in obj.data.materials]
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        uv = bm.loops.layers.uv.active
        assignments = [slots[f.material_index] if f.material_index < len(slots) else None for f in bm.faces]
        coordinates = [[list(loop[uv].uv) for loop in f.loops] for f in bm.faces] if uv else None
    else:
        mesh = obj.data
        assignments = [slots[f.material_index] if f.material_index < len(slots) else None for f in mesh.polygons]
        coordinates = [[list(mesh.uv_layers.active.data[i].uv) for i in f.loop_indices]
                       for f in mesh.polygons] if mesh.uv_layers.active else None
    # Untagged Blender materials retain the legacy grey export behavior.
    return digest({'materials': assignments, 'uvs': coordinates}) if any(assignments) else digest(None)


def assigned_material(mesh, stage):
    assignments = {material_id(mesh.materials[f.material_index])
                   if f.material_index < len(mesh.materials) else None for f in mesh.polygons}
    if not any(assignments):
        return None
    if len(assignments) != 1:
        raise StageError('Use one stage material for the entire model. Assign it to all faces, or use Grey Export.')
    key = next(iter(assignments))
    entry = next((m for m in stage.get('modelMaterials', []) if m['id'] == key), None)
    if entry is None:
        raise StageError('Assigned material belongs to another stage or is unsupported. Assign a material from this session.')
    return entry


def assign(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        for face in bm.faces:
            face.material_index = 0
        bmesh.update_edit_mesh(obj.data)
    else:
        for face in obj.data.polygons:
            face.material_index = 0
