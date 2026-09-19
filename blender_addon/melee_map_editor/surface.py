"""One supported stage material per model, with native Blender corner UVs."""
from pathlib import Path
import bpy
import bmesh
from .protocol import StageError, digest


def material_id(material):
    return material.get('mme_model_material_id') if material else None


def create_materials(stage, directory=None):
    result = {}
    entries = [(entry, False) for entry in stage.get('modelMaterials', [])]
    entries += [(entry, True) for entry in stage.get('modelPreviews', [])]
    for entry, preview_only in entries:
        material = bpy.data.materials.new(entry['name'])
        material.diffuse_color = (0.45, 0.45, 0.45, 1)
        if preview_only:
            material['mme_preview_model_id'] = entry['id']
        else:
            material['mme_model_material_id'] = entry['id']
        material['mme_model_material_source'] = stage['source']['sha256']
        material['mme_model_uses_uv'] = entry['usesUv']
        configure_preview(material, entry.get('preview'), directory, stage)
        result[entry['id']] = material
    return result


def import_uvs(mesh, source):
    coordinates = source.get('texCoords0')
    if coordinates:
        uv = mesh.uv_layers.new(name='UVMap')
        for loop in mesh.loops:
            value = coordinates[loop.vertex_index]
            uv.data[loop.index].uv = (value['x'], 1 - value['y'])


def fingerprint(obj, protect_all=False):
    slots = [(material_id(m) or m.name if m else None) if protect_all else material_id(m)
             for m in obj.data.materials]
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
    if protect_all:
        return digest({'slots': slots, 'materials': assignments, 'uvs': coordinates})
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


def preview_matrix(texture):
    """HSD tobj.c MakeTextureMtx: S @ R @ T, conjugated by Blender's V flip."""
    from mathutils import Matrix, Euler
    scale = texture['scale']
    repeats = (texture['repeatS'], texture['repeatT'])
    factors = [repeats[i] / scale[i] if abs(scale[i]) >= 1.1920929e-7 else 0 for i in range(2)] + [scale[2]]
    rotation = texture['rotation']
    translation = texture['translation']
    shift = (-translation[0], -translation[1] - (scale[1] / repeats[1] if texture['wrapT'] == 2 else 0), translation[2])
    matrix = (Matrix.Diagonal((*factors, 1))
              @ Euler((rotation[0], rotation[1], -rotation[2]), 'XYZ').to_matrix().to_4x4()
              @ Matrix.Translation(shift))
    flip = Matrix(((1, 0, 0, 0), (0, -1, 0, 1), (0, 0, 1, 0), (0, 0, 0, 1)))
    return flip @ matrix @ flip


def configure_preview(material, preview, directory, stage):
    if not preview:
        return
    color = preview.get('color', [0.45, 0.45, 0.45, 1])
    material.diffuse_color = color
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    emission = nodes.new('ShaderNodeEmission')
    # Byte colors describe sRGB; shader color sockets are scene-linear.
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in color[:3]]
    emission.inputs['Color'].default_value = (*linear, 1)
    links.new(emission.outputs[0], output.inputs['Surface'])
    warning = preview.get('warning')
    texture = preview.get('texture')
    if warning:
        material['mme_preview_warning'] = warning
    if not texture or directory is None:
        return
    directory = Path(directory).resolve()
    path = (directory / texture['file']).resolve()
    if (not path.is_relative_to(directory)
            or texture['file'] not in {entry['file'] for entry in stage['baselineFiles']}):
        raise StageError('Texture preview file is outside the protected session baseline.')
    image = bpy.data.images.load(str(path), check_existing=True)
    image.colorspace_settings.name = 'sRGB'
    image.pack()
    sampler = nodes.new('ShaderNodeTexImage')
    sampler.name = 'Stage Texture'
    sampler.image = image
    sampler.interpolation = 'Linear'
    sampler.extension = 'EXTEND'
    uv = nodes.new('ShaderNodeTexCoord')
    matrix = preview_matrix(texture)
    combine = nodes.new('ShaderNodeCombineXYZ')
    for axis, wrap in enumerate((texture['wrapS'], texture['wrapT'])):
        dot = nodes.new('ShaderNodeVectorMath')
        dot.operation = 'DOT_PRODUCT'
        dot.inputs[1].default_value = tuple(matrix[axis][i] for i in range(3))
        links.new(uv.outputs['UV'], dot.inputs[0])
        add = nodes.new('ShaderNodeMath')
        add.operation = 'ADD'
        add.inputs[1].default_value = matrix[axis][3]
        links.new(dot.outputs['Value'], add.inputs[0])
        value = add.outputs[0]
        if wrap == 0:
            clamp = nodes.new('ShaderNodeClamp')
            links.new(value, clamp.inputs['Value'])
            value = clamp.outputs[0]
        else:
            wrapped = nodes.new('ShaderNodeMath')
            wrapped.operation = 'FRACT' if wrap == 1 else 'PINGPONG'
            wrapped.inputs[1].default_value = 1
            links.new(value, wrapped.inputs[0])
            value = wrapped.outputs[0]
        links.new(value, combine.inputs[axis])
    links.new(combine.outputs[0], sampler.inputs['Vector'])
    links.new(sampler.outputs['Color'], emission.inputs['Color'])
    nodes.active = sampler
    sampler.select = True
    # A legible layout if the user opens the Shader Editor.
    uv.location = (-900, 0)
    combine.location = (-300, 0)
    sampler.location = (-100, 0)
    emission.location = (200, 0)
    output.location = (400, 0)


def show_preview(context, switch_viewport=True):
    obj = context.active_object
    material = obj.active_material if obj and obj.type == 'MESH' else None
    image = None
    if material and material.use_nodes:
        node = material.node_tree.nodes.get('Stage Texture')
        image = node.image if node else None
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D' and switch_viewport:
                area.spaces.active.shading.type = 'MATERIAL'
            elif area.type == 'IMAGE_EDITOR' and area.ui_type == 'UV' and image:
                area.spaces.active.image = image
    return image


def import_colors(mesh, source):
    """Keep GX color seams as corner attributes, including the second channel."""
    layers = []
    for channel in range(2):
        colors = source.get(f'colors{channel}')
        if colors is None:
            continue
        name = f'Stage Color {channel}'
        layer = mesh.color_attributes.new(name=name, type='FLOAT_COLOR', domain='CORNER')
        for loop in mesh.loops:
            value = colors[loop.vertex_index]
            layer.data[loop.index].color = tuple(value[c] for c in ('r', 'g', 'b', 'a'))
        layers.append(name)
    if layers:
        mesh.color_attributes.active_color = mesh.color_attributes[layers[0]]
    return layers


def configure_color_preview(material, layer_name):
    """Approximate GX raster color modulation; TEV channel routing is not emulated."""
    if not material.node_tree or not any(n.type == 'EMISSION' for n in material.node_tree.nodes):
        configure_preview(material, {'color': [1, 1, 1, 1]}, None, {})
    nodes, links = material.node_tree.nodes, material.node_tree.links
    emission = next(n for n in nodes if n.type == 'EMISSION')
    color = nodes.new('ShaderNodeVertexColor')
    color.name = 'Stage Vertex Color'
    color.layer_name = layer_name
    multiply = nodes.new('ShaderNodeMixRGB')
    multiply.name = 'Stage Color Modulation'
    multiply.blend_type = 'MULTIPLY'
    multiply.inputs[0].default_value = 1
    base = emission.inputs['Color']
    if base.is_linked:
        links.new(base.links[0].from_socket, multiply.inputs[1])
    else:
        multiply.inputs[1].default_value = base.default_value
    links.new(color.outputs['Color'], multiply.inputs[2])
    links.new(multiply.outputs[0], base)
    color.location = (-100, -300)
    multiply.location = (150, -100)
