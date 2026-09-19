"""One supported stage material per model, with native Blender corner UVs."""
import json
from pathlib import Path
import bpy
import bmesh
from .protocol import StageError, digest


def material_id(material):
    return material.get('mme_model_material_id') if material else None


def create_materials(stage, directory=None):
    from . import material_properties
    definitions = {entry['id']: entry for entry in stage.get('editableMaterialProperties', [])}
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
        if entry['id'] in definitions:
            material_properties.initialize(material, entry, definitions[entry['id']], directory, stage)
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
    diffuse_lighting = preview.get('diffuseLighting', False)
    specular_lighting = preview.get('specularLighting', False)
    shader = nodes.new('ShaderNodeEmission')
    shader.name = 'Stage Surface'
    color_input = shader.inputs['Color']
    # Byte colors describe sRGB; shader color sockets are scene-linear.
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in color[:3]]
    # Vertex-color materials take their base color from the mesh, not diffuse RGB.
    if preview.get('useVertexColor'):
        linear = [1, 1, 1]
    color_input.default_value = (*linear, 1)
    if diffuse_lighting or specular_lighting:
        geometry = nodes.new('ShaderNodeNewGeometry')
        geometry.name = 'Stage Lighting Normal'
        camera_normal = nodes.new('ShaderNodeVectorTransform')
        camera_normal.name = 'Stage Camera Normal'
        camera_normal.vector_type = 'NORMAL'
        camera_normal.convert_from = 'WORLD'
        camera_normal.convert_to = 'CAMERA'
        links.new(geometry.outputs['Normal'], camera_normal.inputs['Vector'])

        def scalar(kind, operation, a=None, b=None):
            node = nodes.new(kind)
            node.operation = operation
            if a is not None:
                if isinstance(a, (int, float)):
                    node.inputs[0].default_value = a
                else:
                    links.new(a, node.inputs[0])
            if b is not None:
                if isinstance(b, (int, float)):
                    node.inputs[1].default_value = b
                else:
                    links.new(b, node.inputs[1])
            return node

        diffuse = nodes.new('ShaderNodeMixRGB')
        diffuse.name = 'Stage Diffuse Lighting'
        diffuse.blend_type = 'MULTIPLY'
        diffuse.inputs[0].default_value = 1
        diffuse.inputs[1].default_value = (*linear, 1)
        color_input = diffuse.inputs[1]
        if diffuse_lighting:
            dot = nodes.new('ShaderNodeVectorMath')
            dot.name = 'Stage Diffuse N dot L'
            dot.operation = 'DOT_PRODUCT'
            dot.inputs[1].default_value = (.35, -.45, .82)
            links.new(camera_normal.outputs['Vector'], dot.inputs[0])
            positive = scalar('ShaderNodeMath', 'MAXIMUM', dot.outputs['Value'], 0)
            directional = scalar('ShaderNodeMath', 'MULTIPLY', positive.outputs[0], .4)
            light = scalar('ShaderNodeMath', 'ADD', directional.outputs[0], .65)
            links.new(light.outputs[0], diffuse.inputs[2])
        else:
            diffuse.inputs[2].default_value = (1, 1, 1, 1)
        result = diffuse.outputs[0]

        if specular_lighting:
            half_dot = nodes.new('ShaderNodeVectorMath')
            half_dot.name = 'Stage Specular N dot H'
            half_dot.operation = 'DOT_PRODUCT'
            half_dot.inputs[1].default_value = (.184, -.236, .954)
            links.new(camera_normal.outputs['Vector'], half_dot.inputs[0])
            positive = scalar('ShaderNodeMath', 'MAXIMUM', half_dot.outputs['Value'], 0)
            # HSD evaluates pow(max(dot(N, H), 0), shininess). Unlike a BSDF,
            # this does not reflect Blender's HDRI or world environment.
            exponent = max(1, min(128, preview.get('shininess', 50)))
            power = scalar('ShaderNodeMath', 'POWER', positive.outputs[0], exponent)
            power.name = 'Stage Specular Power'
            specular = preview.get('specularColor') or [.25, .25, .25, 1]
            specular_linear = tuple(
                c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in specular[:3]) + (1,)
            specular_color = nodes.new('ShaderNodeMixRGB')
            specular_color.name = 'Stage Specular Color'
            specular_color.blend_type = 'MULTIPLY'
            specular_color.inputs[0].default_value = 1
            specular_color.inputs[1].default_value = specular_linear
            links.new(power.outputs[0], specular_color.inputs[2])
            add = nodes.new('ShaderNodeMixRGB')
            add.name = 'Stage Specular Add'
            add.blend_type = 'ADD'
            add.inputs[0].default_value = 1
            links.new(result, add.inputs[1])
            links.new(specular_color.outputs[0], add.inputs[2])
            result = add.outputs[0]
        links.new(result, shader.inputs['Color'])
        geometry.location = (-900, -500)
        camera_normal.location = (-700, -500)
        diffuse.location = (50, -150)
    links.new(shader.outputs[0], output.inputs['Surface'])
    warning = preview.get('warning')
    texture = preview.get('texture')
    if warning:
        material['mme_preview_warning'] = warning
    if not texture or directory is None:
        configure_alpha_preview(material, preview.get('alpha'))
        return
    directory = Path(directory).resolve()
    path = (directory / texture['file']).resolve()
    if (not path.is_relative_to(directory)
            or texture['file'] not in {entry['file'] for entry in stage['baselineFiles']}):
        raise StageError('Texture preview file is outside the protected session baseline.')
    image = bpy.data.images.load(str(path), check_existing=True)
    image.colorspace_settings.name = 'sRGB'
    image.alpha_mode = 'STRAIGHT'
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
    operation = texture.get('colorOperation', 5)
    if operation in (3, 4):
        tint = nodes.new('ShaderNodeMixRGB')
        tint.name = 'Stage Diffuse Tint'
        tint.blend_type = 'MULTIPLY' if operation == 4 else 'MIX'
        tint.inputs[0].default_value = 1 if operation == 4 else texture.get('colorBlend', 1)
        if operation == 3:
            # GX interpolates stored color values, not scene-linear light.
            # Decode the mixed result only after the TEV blend calculation.
            tint.inputs[1].default_value = (1, 1, 1, 1) if preview.get('useVertexColor') else color
            encoded = color_transfer(material, sampler.outputs['Color'], to_linear=False)
            links.new(encoded, tint.inputs[2])
            result = color_transfer(material, tint.outputs[0], to_linear=True)
            links.new(result, color_input)
        else:
            tint.inputs[1].default_value = (*linear, 1)
            links.new(sampler.outputs['Color'], tint.inputs[2])
            links.new(tint.outputs[0], color_input)
        tint.location = (100, 100)
    else:
        links.new(sampler.outputs['Color'], color_input)
    nodes.active = sampler
    sampler.select = True
    # A legible layout if the user opens the Shader Editor.
    uv.location = (-900, 0)
    combine.location = (-300, 0)
    sampler.location = (-100, 0)
    shader.location = (400, 0)
    output.location = (600, 0)
    configure_alpha_preview(material, preview.get('alpha'))


def update_uv_editor(context):
    obj = context.active_object
    material = obj.active_material if obj and obj.type == 'MESH' else None
    image = None
    if material and material.use_nodes:
        node = material.node_tree.nodes.get('Stage Texture')
        image = node.image if node else None
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'IMAGE_EDITOR' and area.ui_type == 'UV' and image:
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
    material['mme_vertex_color_layer'] = layer_name
    if not material.node_tree or material.node_tree.nodes.get('Stage Surface') is None:
        configure_preview(material, {'color': [1, 1, 1, 1]}, None, {})
    nodes, links = material.node_tree.nodes, material.node_tree.links
    shader = nodes.get('Stage Diffuse Lighting') or nodes['Stage Surface']
    color = nodes.new('ShaderNodeVertexColor')
    color.name = 'Stage Vertex Color'
    color.layer_name = layer_name
    multiply = nodes.new('ShaderNodeMixRGB')
    multiply.name = 'Stage Color Modulation'
    multiply.blend_type = 'MULTIPLY'
    multiply.inputs[0].default_value = 1
    base = shader.inputs[1] if shader.name == 'Stage Diffuse Lighting' else shader.inputs['Color']
    if base.is_linked:
        links.new(base.links[0].from_socket, multiply.inputs[1])
    else:
        multiply.inputs[1].default_value = base.default_value
    links.new(color.outputs['Color'], multiply.inputs[2])
    links.new(multiply.outputs[0], base)
    color.location = (-100, -300)
    multiply.location = (150, -100)
    if material.get('mme_alpha_preview'):
        configure_alpha_preview(material, json.loads(material['mme_alpha_preview']))


def configure_alpha_preview(material, settings):
    """Static HSD alpha operations and common framebuffer blends, without DAT edits."""
    if not settings:
        return
    material['mme_alpha_preview'] = json.dumps(settings)
    nodes, links = material.node_tree.nodes, material.node_tree.links
    for node in list(nodes):
        if node.get('mme_alpha_node'):
            nodes.remove(node)
    stage_surface = nodes['Stage Surface']
    output = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')
    if stage_surface.type == 'EMISSION':
        stage_surface.inputs['Strength'].default_value = 1

    def node(kind):
        result = nodes.new(kind)
        result['mme_alpha_node'] = True
        result.label = 'Stage alpha preview'
        return result

    def connect(value, socket):
        if isinstance(value, (float, int)):
            socket.default_value = value
        else:
            links.new(value, socket)

    def math(op, a, b=0, c=None):
        result = node('ShaderNodeMath')
        result.operation = op
        connect(a, result.inputs[0])
        connect(b, result.inputs[1])
        if c is not None:
            connect(c, result.inputs[2])
        return result.outputs[0]

    vertex = nodes.get('Stage Vertex Color')
    alpha = settings['material']
    if settings['vertex']:
        alpha = vertex.outputs['Alpha'] if vertex else 1
        if settings['multiplyMaterial']:
            alpha = math('MULTIPLY', alpha, settings['material'])
    texture = nodes.get('Stage Texture')
    if texture:
        tex = texture.outputs['Alpha']
        operation = settings['textureOperation']
        if operation in (1, 2):
            factor = tex if operation == 1 else settings['textureBlend']
            alpha = math('ADD', math('MULTIPLY', alpha, math('SUBTRACT', 1, factor)),
                         math('MULTIPLY', tex, factor))
        elif operation == 3:
            alpha = math('MULTIPLY', alpha, tex)
        elif operation == 4:
            alpha = tex
        elif operation == 6:
            alpha = math('ADD', alpha, tex)
        elif operation == 7:
            alpha = math('SUBTRACT', alpha, tex)
    alpha = math('MINIMUM', 1, math('MAXIMUM', 0, alpha))

    def compare(kind, reference):
        reference /= 255
        if kind in (0, 7):
            return 1 if kind == 7 else 0
        if kind == 1:
            return math('LESS_THAN', alpha, reference)
        if kind == 4:
            return math('GREATER_THAN', alpha, reference)
        if kind in (2, 5):
            equal = math('COMPARE', alpha, reference, 0.000001)
            return equal if kind == 2 else math('SUBTRACT', 1, equal)
        return math('SUBTRACT', 1, math('GREATER_THAN' if kind == 3 else 'LESS_THAN', alpha, reference))

    a = compare(settings['compare0'], settings['reference0'])
    b = compare(settings['compare1'], settings['reference1'])
    operation = settings['operation']
    passed = (math('MULTIPLY', a, b) if operation == 0 else math('MAXIMUM', a, b) if operation == 1
              else math('ABSOLUTE', math('SUBTRACT', a, b)))
    if operation == 3:
        passed = math('SUBTRACT', 1, passed)
    transparent = node('ShaderNodeBsdfTransparent')
    mode, src, dst = settings['blendMode'], settings['sourceFactor'], settings['destinationFactor']
    if mode == 1 and dst == 1 and src in (1, 4) and stage_surface.type == 'EMISSION':
        # Additive effects transmit the background completely; black adds nothing.
        connect(math('MULTIPLY', passed, alpha if src == 4 else 1), stage_surface.inputs['Strength'])
        shader = node('ShaderNodeAddShader')
        links.new(transparent.outputs[0], shader.inputs[0])
        links.new(stage_surface.outputs[0], shader.inputs[1])
    else:
        opacity = passed if mode == 0 or (mode == 1 and src == 1 and dst == 0) else math('MULTIPLY', passed, alpha)
        shader = node('ShaderNodeMixShader')
        connect(opacity, shader.inputs[0])
        links.new(transparent.outputs[0], shader.inputs[1])
        links.new(stage_surface.outputs[0], shader.inputs[2])
    shader.name = 'Stage Alpha Surface'
    links.new(shader.outputs[0], output.inputs['Surface'])
    if hasattr(material, 'surface_render_method'):
        material.surface_render_method = 'DITHERED'
    elif hasattr(material, 'blend_method'):
        material.blend_method = 'HASHED'


def color_transfer(material, socket, *, to_linear):
    """Convert RGB for GX byte-space arithmetic without changing shared image settings."""
    name = 'Melee GX to Linear' if to_linear else 'Melee Linear to GX'
    group = next((g for g in bpy.data.node_groups
                  if g.get('mme_color_transfer') == name and g.bl_idname == 'ShaderNodeTree'), None)
    if group is None:
        group = bpy.data.node_groups.new(name, 'ShaderNodeTree')
        group['mme_color_transfer'] = name
        group.interface.new_socket(name='Color', in_out='INPUT', socket_type='NodeSocketColor')
        group.interface.new_socket(name='Color', in_out='OUTPUT', socket_type='NodeSocketColor')
        nodes, links = group.nodes, group.links
        inputs = nodes.new('NodeGroupInput')
        outputs = nodes.new('NodeGroupOutput')
        separate = nodes.new('ShaderNodeSeparateColor'); separate.mode = 'RGB'
        combine = nodes.new('ShaderNodeCombineColor'); combine.mode = 'RGB'
        links.new(inputs.outputs['Color'], separate.inputs['Color'])

        def math(op, a, b):
            node = nodes.new('ShaderNodeMath'); node.operation = op
            for value, target in zip((a, b), node.inputs):
                if isinstance(value, (int, float)):
                    target.default_value = value
                else:
                    links.new(value, target)
            return node.outputs[0]

        for index in range(3):
            value = math('MAXIMUM', separate.outputs[index], 0)
            if to_linear:
                low = math('DIVIDE', value, 12.92)
                high = math('POWER', math('DIVIDE', math('ADD', value, .055), 1.055), 2.4)
                threshold = .04045
            else:
                low = math('MULTIPLY', value, 12.92)
                high = math('SUBTRACT', math('MULTIPLY', math('POWER', value, 1 / 2.4), 1.055), .055)
                threshold = .0031308
            result = math('ADD', low, math('MULTIPLY', math('SUBTRACT', high, low),
                                          math('GREATER_THAN', value, threshold)))
            links.new(result, combine.inputs[index])
        links.new(combine.outputs['Color'], outputs.inputs['Color'])
    node = material.node_tree.nodes.new('ShaderNodeGroup')
    node.node_tree = group
    node.name = name
    material.node_tree.links.new(socket, node.inputs['Color'])
    return node.outputs['Color']
