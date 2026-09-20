"""One supported stage material per model, with native Blender corner UVs."""
import json
from pathlib import Path
import bpy
import bmesh
from mathutils import Vector
from .protocol import StageError, digest


def material_id(material):
    return material.get('mme_model_material_id') if material else None


def create_materials(stage, directory=None, light_objects=None):
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
            if entry.get('materialReadOnlyReason'):
                material['mme_material_read_only_reason'] = entry['materialReadOnlyReason']
        else:
            material['mme_model_material_id'] = entry['id']
        material['mme_model_material_source'] = stage['source']['sha256']
        material['mme_model_uses_uv'] = entry['usesUv']
        configure_preview(material, entry.get('preview'), directory, stage, light_objects)
        if entry['id'] in definitions:
            material_properties.initialize(material, entry, definitions[entry['id']], directory, stage)
        result[entry['id']] = material
    return result


def import_uvs(mesh, source):
    for channel in range(2):
        coordinates = source.get(f'texCoords{channel}')
        if not coordinates:
            continue
        uv = mesh.uv_layers.new(name='UVMap' if channel == 0 else f'UVMap.{channel:03d}')
        for loop in mesh.loops:
            value = coordinates[loop.vertex_index]
            uv.data[loop.index].uv = (value['x'], 1 - value['y'])


def enable_source_normals(material):
    """Enable the per-vertex GX normal path for a material assigned to a source mesh."""
    if not material or not material.node_tree:
        return
    nodes, links = material.node_tree.nodes, material.node_tree.links
    for node in nodes:
        if not node.name.startswith('Stage Preview Normal'):
            continue
        suffix = node.name.removeprefix('Stage Preview Normal')
        valid = nodes.get(f'Stage Source Normal Available{suffix}')
        if valid is not None:
            links.new(valid.outputs['Fac'], node.inputs[0])


def display_triangle_indices(source, positions=None, normals=None):
    """Orient GX triangles toward their stored normals for Blender display."""
    indices = source['triangleIndices']
    normals = normals if normals is not None else source.get('normals')
    if not normals:
        return indices, [False] * (len(indices) // 3)
    positions = positions if positions is not None else source['positions']

    def value(items, index):
        item = items[index]
        return item.copy() if isinstance(item, Vector) else Vector(tuple(item[axis] for axis in ('x', 'y', 'z')))

    result = []
    reversed_faces = []
    for offset in range(0, len(indices), 3):
        a, b, c = (value(positions, indices[offset + corner]) for corner in range(3))
        face = (b - a).cross(c - a)
        normal = sum((value(normals, indices[offset + corner]) for corner in range(3)), Vector())
        reverse = face.length_squared > 0 and normal.length_squared > 0 and face.dot(normal) < 0
        reversed_faces.append(reverse)
        corners = indices[offset:offset + 3]
        result.extend((corners[0], corners[2], corners[1]) if reverse else corners)
    return result, reversed_faces


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


def configure_preview(material, preview, directory, stage, light_objects=None):
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
    specular_color_input = None
    surface_result = None
    shader = nodes.new('ShaderNodeEmission')
    shader.name = 'Stage Surface'
    color_input = shader.inputs['Color']

    def preview_normal(geometry, suffix=''):
        """Use exact GX normals without Blender's polygon-hemisphere clamping."""
        attribute = nodes.new('ShaderNodeAttribute')
        attribute.name = f'Stage Source Normal{suffix}'
        attribute.attribute_type = 'GEOMETRY'
        attribute.attribute_name = 'Stage Normal'
        transform = nodes.new('ShaderNodeVectorTransform')
        transform.name = f'Stage Source Normal Transform{suffix}'
        transform.vector_type = 'NORMAL'
        transform.convert_from = 'OBJECT'
        transform.convert_to = 'WORLD'
        links.new(attribute.outputs['Vector'], transform.inputs['Vector'])
        normalize = nodes.new('ShaderNodeVectorMath')
        normalize.name = f'Stage Source Normal Normalize{suffix}'
        normalize.operation = 'NORMALIZE'
        links.new(transform.outputs['Vector'], normalize.inputs[0])
        valid = nodes.new('ShaderNodeAttribute')
        valid.name = f'Stage Source Normal Available{suffix}'
        valid.attribute_type = 'GEOMETRY'
        valid.attribute_name = 'Stage Normal Valid'
        mix = nodes.new('ShaderNodeMix')
        mix.name = f'Stage Preview Normal{suffix}'
        mix.data_type = 'VECTOR'
        mix.inputs[0].default_value = 0
        links.new(geometry.outputs['Normal'], mix.inputs[4])
        links.new(normalize.outputs['Vector'], mix.inputs[5])
        return mix.outputs[1]

    # Byte colors describe sRGB; shader color sockets are scene-linear.
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in color[:3]]
    # Vertex-color materials take their base color from the mesh, not diffuse RGB.
    if preview.get('useVertexColor'):
        linear = [1, 1, 1]
    color_input.default_value = (*linear, 1)
    if diffuse_lighting or specular_lighting:
        from . import lighting
        geometry = nodes.new('ShaderNodeNewGeometry')
        geometry.name = 'Stage Lighting Normal'

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

        def vector(operation, a=None, b=None):
            node = nodes.new('ShaderNodeVectorMath')
            node.operation = operation
            for value, socket in ((a, node.inputs[0]), (b, node.inputs[1])):
                if value is None:
                    continue
                if isinstance(value, (tuple, list)):
                    socket.default_value = value
                else:
                    links.new(value, socket)
            return node

        def rgb(name, value):
            node = nodes.new('ShaderNodeRGB')
            node.name = name
            node.outputs[0].default_value = (*value[:3], 1)
            return node.outputs[0]

        def color_scale(name, color_value, amount):
            node = nodes.new('ShaderNodeMixRGB')
            node.name = name
            node.blend_type = 'MULTIPLY'
            node.inputs[0].default_value = 1
            if isinstance(color_value, (tuple, list)):
                node.inputs[1].default_value = (*color_value[:3], 1)
            else:
                links.new(color_value, node.inputs[1])
            if isinstance(amount, (int, float)):
                node.inputs[2].default_value = (amount, amount, amount, 1)
            else:
                links.new(amount, node.inputs[2])
            return node.outputs[0]

        def color_add(name, a, b):
            node = nodes.new('ShaderNodeMixRGB')
            node.name = name
            node.blend_type = 'ADD'
            node.inputs[0].default_value = 1
            links.new(a, node.inputs[1])
            links.new(b, node.inputs[2])
            return node.outputs[0]

        def linear_color(value):
            return tuple(c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
                         for c in value[:3])

        def drive(socket, variables, expression, index=None):
            curve = (socket.driver_add('default_value', index) if index is not None
                     else socket.driver_add('default_value'))
            driver = curve.driver
            driver.type = 'SCRIPTED'
            for name, target, path in variables:
                variable = driver.variables.new()
                variable.name = name
                variable.type = 'SINGLE_PROP'
                if isinstance(target, bpy.types.Light):
                    variable.targets[0].id_type = 'LIGHT'
                variable.targets[0].id = target
                variable.targets[0].data_path = path
            driver.expression = expression

        def light_color(source, obj):
            value = linear_color(source['color'])
            node = nodes.new('ShaderNodeRGB')
            node.name = f"Stage Light Color {source.get('id', source['type'])}"
            node.outputs[0].default_value = (*value, 1)
            if obj is not None:
                target = obj if source['type'] == 'ambient' else obj.data
                for axis in range(3):
                    path = f'["mme_light_color"][{axis}]' if source['type'] == 'ambient' else f'color[{axis}]'
                    drive(node.outputs[0], [('value', target, path)], 'value', axis)
            return node.outputs[0]

        def light_strength(source, obj):
            node = nodes.new('ShaderNodeValue')
            node.name = f"Stage Light Strength {source.get('id', source['type'])}"
            node.outputs[0].default_value = 0 if source.get('hidden') else 1
            if obj is not None:
                energy_target = obj if source['type'] == 'ambient' else obj.data
                energy_path = '["mme_light_intensity"]' if source['type'] == 'ambient' else 'energy'
                drive(node.outputs[0], [('energy', energy_target, energy_path),
                                       ('enabled', obj, '["mme_light_enabled"]')],
                      'energy * enabled')
            return node.outputs[0]

        def driven_position(source, obj):
            value = source['position']
            initial = (value['x'], -value['z'], value['y'])
            node = nodes.new('ShaderNodeCombineXYZ')
            node.name = f"Stage Light Position {source.get('id', source['type'])}"
            for axis, component in enumerate(initial):
                node.inputs[axis].default_value = component
                if obj is not None:
                    drive(node.inputs[axis], [('value', obj, f'location[{axis}]')], 'value')
            return node.outputs['Vector']

        def driven_axis(source, obj):
            value = source['position']
            initial = Vector((value['x'], -value['z'], value['y']))
            if source['type'] == 'infinite':
                initial.negate()
            elif source['type'] == 'spot':
                initial -= lighting.game_vector(source['interest'])
            initial.normalize()
            node = nodes.new('ShaderNodeCombineXYZ')
            node.name = f"Stage Light Direction {source.get('id', source['type'])}"
            expressions = (
                'cos(rz)*sin(ry)*cos(rx)+sin(rz)*sin(rx)',
                'sin(rz)*sin(ry)*cos(rx)-cos(rz)*sin(rx)',
                'cos(ry)*cos(rx)')
            for axis, component in enumerate(initial):
                node.inputs[axis].default_value = component
                if obj is not None:
                    variables = [('rx', obj, 'rotation_euler[0]'),
                                 ('ry', obj, 'rotation_euler[1]'),
                                 ('rz', obj, 'rotation_euler[2]')]
                    drive(node.inputs[axis], variables, expressions[axis])
            return node.outputs['Vector']

        active_lights = lighting.preview_lights(stage)
        game_lights = bool(active_lights)
        if not active_lights:
            # Compatibility for sessions extracted before LOBJ support.
            active_lights = [
                dict(type='ambient', color=[.65, .65, .65, 1], diffuse=True,
                     specular=False, hidden=False),
                dict(type='infinite', color=[.4, .4, .4, 1], diffuse=True,
                     specular=True, hidden=False, position=dict(x=.35, y=.82, z=.45))]

        normal = preview_normal(geometry)
        if not game_lights:
            camera_normal = nodes.new('ShaderNodeVectorTransform')
            camera_normal.name = 'Stage Camera Normal'
            camera_normal.vector_type = 'NORMAL'
            camera_normal.convert_from = 'WORLD'
            camera_normal.convert_to = 'CAMERA'
            links.new(normal, camera_normal.inputs['Vector'])
            normal = camera_normal.outputs['Vector']
        incoming = geometry.outputs['Incoming']
        if game_lights:
            view = vector('MULTIPLY', incoming, (-1, -1, -1)).outputs['Vector']
            view = vector('NORMALIZE', view).outputs['Vector']
        else:
            view = (0, 0, 1)

        def light_vector(source, obj):
            value = source['position']
            position = (value['x'], -value['z'], value['y']) if game_lights else (value['x'], value['z'], value['y'])
            if source['type'] == 'infinite':
                if game_lights:
                    return driven_axis(source, obj), 1
                length = sum(v * v for v in position) ** .5
                return tuple(v / length for v in position) if length else (0, 0, 1), 1
            position_socket = driven_position(source, obj) if game_lights else position
            delta = vector('SUBTRACT', position_socket, geometry.outputs['Position'])
            direction = vector('NORMALIZE', delta.outputs['Vector']).outputs['Vector']
            distance = vector('DISTANCE', position_socket, geometry.outputs['Position']).outputs['Value']
            att = source['attenuation']
            distance2 = scalar('ShaderNodeMath', 'MULTIPLY', distance, distance).outputs[0]
            denominator = scalar('ShaderNodeMath', 'MULTIPLY', distance2, att['k2']).outputs[0]
            denominator = scalar('ShaderNodeMath', 'ADD', denominator,
                                 scalar('ShaderNodeMath', 'MULTIPLY', distance, att['k1']).outputs[0]).outputs[0]
            denominator = scalar('ShaderNodeMath', 'ADD', denominator, att['k0']).outputs[0]
            if source['type'] == 'spot':
                axis = driven_axis(source, obj) if game_lights else (0, 0, 1)
                spot_dot = vector('DOT_PRODUCT', direction, axis).outputs['Value']
                spot_dot = scalar('ShaderNodeMath', 'MAXIMUM', spot_dot, 0).outputs[0]
                spot2 = scalar('ShaderNodeMath', 'MULTIPLY', spot_dot, spot_dot).outputs[0]
                numerator = scalar('ShaderNodeMath', 'MULTIPLY', spot2, att['a2']).outputs[0]
                numerator = scalar('ShaderNodeMath', 'ADD', numerator,
                                   scalar('ShaderNodeMath', 'MULTIPLY', spot_dot, att['a1']).outputs[0]).outputs[0]
                numerator = scalar('ShaderNodeMath', 'ADD', numerator, att['a0']).outputs[0]
                numerator = scalar('ShaderNodeMath', 'MAXIMUM', numerator, 0).outputs[0]
            else:
                numerator = 1
            denominator = scalar('ShaderNodeMath', 'MAXIMUM', denominator, 1e-8).outputs[0]
            return direction, scalar('ShaderNodeMath', 'DIVIDE', numerator, denominator).outputs[0]

        illumination = rgb('Stage Ambient Light', (0, 0, 0) if diffuse_lighting else (1, 1, 1))
        if diffuse_lighting:
            material_ambient = rgb('Stage Material Ambient',
                                   linear_color(preview.get('ambientColor') or [1, 1, 1, 1]))
            for source in active_lights:
                if source['type'] != 'ambient':
                    continue
                obj = (light_objects or {}).get(source.get('id'))
                contribution = color_scale('Stage Ambient Light Color', light_color(source, obj),
                                           light_strength(source, obj))
                contribution = color_scale('Stage Material Ambient Color', contribution, material_ambient)
                illumination = color_add('Stage Ambient Light Add', illumination, contribution)
        specular_light = rgb('Stage Specular Light', (0, 0, 0))
        first_diffuse = first_specular = True
        for source in active_lights:
            obj = (light_objects or {}).get(source.get('id'))
            if source['type'] == 'ambient' or (source.get('hidden') and obj is None):
                continue
            direction, attenuation = light_vector(source, obj)
            strength = light_strength(source, obj)
            source_color = light_color(source, obj)
            ndotl = vector('DOT_PRODUCT', normal, direction)
            if diffuse_lighting and source.get('diffuse'):
                ndotl.name = 'Stage Diffuse N dot L' if first_diffuse else 'Stage Diffuse N dot L (additional)'
                positive = scalar('ShaderNodeMath', 'MAXIMUM', ndotl.outputs['Value'], 0).outputs[0]
                amount = scalar('ShaderNodeMath', 'MULTIPLY', positive, attenuation).outputs[0]
                amount = scalar('ShaderNodeMath', 'MULTIPLY', amount, strength).outputs[0]
                contribution = color_scale('Stage Diffuse Light Color', source_color, amount)
                illumination = color_add('Stage Diffuse Light Add', illumination, contribution)
                first_diffuse = False
            if specular_lighting and source.get('specular'):
                half_vector = vector('ADD', direction, view)
                half_vector = vector('NORMALIZE', half_vector.outputs['Vector']).outputs['Vector']
                ndoth = vector('DOT_PRODUCT', normal, half_vector)
                ndoth.name = 'Stage Specular N dot H' if first_specular else 'Stage Specular N dot H (additional)'
                positive = scalar('ShaderNodeMath', 'MAXIMUM', ndoth.outputs['Value'], 0).outputs[0]
                exponent = max(1, min(128, preview.get('shininess', 50)))
                power = scalar('ShaderNodeMath', 'POWER', positive, exponent)
                power.name = 'Stage Specular Power' if first_specular else 'Stage Specular Power (additional)'
                front = scalar('ShaderNodeMath', 'GREATER_THAN', ndotl.outputs['Value'], 0).outputs[0]
                amount = scalar('ShaderNodeMath', 'MULTIPLY', power.outputs[0], front).outputs[0]
                amount = scalar('ShaderNodeMath', 'MULTIPLY', amount, attenuation).outputs[0]
                amount = scalar('ShaderNodeMath', 'MULTIPLY', amount, strength).outputs[0]
                contribution = color_scale('Stage Specular Light Color', source_color, amount)
                specular_light = color_add('Stage Specular Light Add', specular_light, contribution)
                first_specular = False

        diffuse = nodes.new('ShaderNodeMixRGB')
        diffuse.name = 'Stage Diffuse Lighting'
        diffuse.blend_type = 'MULTIPLY'
        diffuse.inputs[0].default_value = 1
        diffuse.inputs[1].default_value = (*linear, 1)
        color_input = diffuse.inputs[1]
        links.new(illumination, diffuse.inputs[2])
        result = diffuse.outputs[0]

        if specular_lighting:
            specular = preview.get('specularColor') or [.25, .25, .25, 1]
            specular_color = nodes.new('ShaderNodeMixRGB')
            specular_color.name = 'Stage Specular Color'
            specular_color.blend_type = 'MULTIPLY'
            specular_color.inputs[0].default_value = 1
            specular_color.inputs[1].default_value = (*linear_color(specular), 1)
            specular_color_input = specular_color.inputs[1]
            links.new(specular_light, specular_color.inputs[2])
            add = nodes.new('ShaderNodeMixRGB')
            add.name = 'Stage Specular Add'
            add.blend_type = 'ADD'
            add.inputs[0].default_value = 1
            links.new(result, add.inputs[1])
            links.new(specular_color.outputs[0], add.inputs[2])
            result = add.outputs[0]
        surface_result = result
        geometry.location = (-900, -500)
        diffuse.location = (50, -150)
    links.new(shader.outputs[0], output.inputs['Surface'])
    warning = preview.get('warning')
    texture = preview.get('texture')
    textures = preview.get('textures') or ([texture] if texture else [])
    if warning:
        material['mme_preview_warning'] = warning
    if not textures or directory is None:
        if surface_result is not None:
            links.new(surface_result, shader.inputs['Color'])
        configure_alpha_preview(material, preview.get('alpha'))
        return
    directory = Path(directory).resolve()
    baseline = {entry['file'] for entry in stage['baselineFiles']}
    samplers = []
    for index, layer in enumerate(textures):
        path = (directory / layer['file']).resolve()
        if not path.is_relative_to(directory) or layer['file'] not in baseline:
            raise StageError('Texture preview file is outside the protected session baseline.')
        image = bpy.data.images.load(str(path), check_existing=True)
        image.colorspace_settings.name = 'sRGB'
        image.alpha_mode = 'STRAIGHT'
        image.pack()
        sampler = nodes.new('ShaderNodeTexImage')
        sampler.name = 'Stage Texture' if index == 0 else f'Stage Texture {index + 1}'
        sampler.image = image
        sampler.interpolation = 'Linear'
        sampler.extension = 'EXTEND'
        coordinate_type = layer.get('coordinateType', 0)
        if coordinate_type == 1:
            geometry = nodes.new('ShaderNodeNewGeometry')
            geometry.name = 'Stage Reflection Normal' if index == 0 else f'Stage Reflection Normal {index + 1}'
            camera_normal = nodes.new('ShaderNodeVectorTransform')
            camera_normal.name = ('Stage Reflection Camera Normal' if index == 0
                                  else f'Stage Reflection Camera Normal {index + 1}')
            camera_normal.vector_type = 'NORMAL'
            camera_normal.convert_from = 'WORLD'
            camera_normal.convert_to = 'CAMERA'
            links.new(preview_normal(geometry, f' Reflection {index + 1}'),
                      camera_normal.inputs['Vector'])
            separate = nodes.new('ShaderNodeSeparateXYZ')
            links.new(camera_normal.outputs['Vector'], separate.inputs['Vector'])
            reflection = nodes.new('ShaderNodeCombineXYZ')
            reflection.name = ('Stage Reflection Coordinates' if index == 0
                               else f'Stage Reflection Coordinates {index + 1}')
            for axis in range(2):
                scale = nodes.new('ShaderNodeMath')
                scale.operation = 'MULTIPLY_ADD'
                scale.inputs[1].default_value = .5
                scale.inputs[2].default_value = .5
                links.new(separate.outputs[axis], scale.inputs[0])
                links.new(scale.outputs[0], reflection.inputs[axis])
            reflection.inputs[2].default_value = 0
            coordinates = reflection.outputs[0]
        else:
            uv = nodes.new('ShaderNodeUVMap')
            uv.name = 'Stage UV' if index == 0 else f'Stage UV {index + 1}'
            coordinate = layer.get('texCoord', 0)
            uv.uv_map = 'UVMap' if coordinate == 0 else f'UVMap.{coordinate:03d}'
            coordinates = uv.outputs['UV']
        matrix = preview_matrix(layer)
        combine = nodes.new('ShaderNodeCombineXYZ')
        combine.name = 'Stage Texture Coordinates' if index == 0 else f'Stage Texture Coordinates {index + 1}'
        for axis, wrap in enumerate((layer['wrapS'], layer['wrapT'])):
            dot = nodes.new('ShaderNodeVectorMath')
            dot.operation = 'DOT_PRODUCT'
            dot.inputs[1].default_value = tuple(matrix[axis][i] for i in range(3))
            links.new(coordinates, dot.inputs[0])
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
        if coordinate_type == 0:
            uv.location = (-900, -index * 260)
        combine.location = (-550, -index * 260)
        sampler.location = (-300, -index * 260)
        samplers.append(sampler)

    def texture_chain(label, selected, base):
        """Apply one HSD lightmap pass in stored byte-color space."""
        encoded_result = None
        for pass_index, (index, layer, sampler) in enumerate(selected):
            encoded_texture = color_transfer(material, sampler.outputs['Color'], to_linear=False)
            operation = layer.get('colorOperation', 5)
            combine_color = nodes.new('ShaderNodeMixRGB')
            combine_color.name = (label if pass_index == 0 else
                                  f'Stage Texture Blend {pass_index + 1}' if label == 'Stage Diffuse Tint'
                                  else f'{label} {pass_index + 1}')
            if isinstance(base, (tuple, list)):
                combine_color.inputs[1].default_value = base
            else:
                links.new(base, combine_color.inputs[1])
            if encoded_result is not None:
                links.new(encoded_result, combine_color.inputs[1])
            links.new(encoded_texture, combine_color.inputs[2])
            if operation == 1:
                combine_color.blend_type = 'MIX'
                links.new(sampler.outputs['Alpha'], combine_color.inputs[0])
            elif operation == 3:
                combine_color.blend_type = 'MIX'
                combine_color.inputs[0].default_value = layer.get('colorBlend', 1)
            elif operation == 4:
                combine_color.blend_type = 'MULTIPLY'
                combine_color.inputs[0].default_value = 1
            elif operation in (0, 6):
                combine_color.blend_type = 'MIX'
                combine_color.inputs[0].default_value = 0
            elif operation == 7:
                combine_color.blend_type = 'ADD'
                combine_color.inputs[0].default_value = 1
            elif operation == 8:
                combine_color.blend_type = 'SUBTRACT'
                combine_color.inputs[0].default_value = 1
            else:
                combine_color.blend_type = 'MIX'
                combine_color.inputs[0].default_value = 1
            combine_color.location = (0, -index * 220)
            encoded_result = combine_color.outputs[0]
        return encoded_result

    # HSD evaluates diffuse/ambient, specular, and extension lightmaps in
    # separate passes. Older session files lack this field and retain their
    # original all-diffuse behavior.
    tagged = [(index, layer, sampler, layer.get('lightmapFlags', 0x10))
              for index, (layer, sampler) in enumerate(zip(textures, samplers))]
    diffuse_layers = [(i, layer, sampler) for i, layer, sampler, flags in tagged if flags & 0x50]
    specular_layers = [(i, layer, sampler) for i, layer, sampler, flags in tagged if flags & 0x20]
    extension_layers = [(i, layer, sampler) for i, layer, sampler, flags in tagged if flags & 0x80]

    diffuse_result = None
    if len(diffuse_layers) == 1 and diffuse_layers[0][1].get('colorOperation', 5) == 4:
        # Preserve the established preview response for the common single
        # modulate texture: Blender already supplies the sampled color in linear space.
        _, layer, sampler = diffuse_layers[0]
        tint = nodes.new('ShaderNodeMixRGB')
        tint.name = 'Stage Diffuse Tint'
        tint.blend_type = 'MULTIPLY'
        tint.inputs[0].default_value = 1
        tint.inputs[1].default_value = (*linear, 1)
        links.new(sampler.outputs['Color'], tint.inputs[2])
        diffuse_result = tint.outputs[0]
    elif len(diffuse_layers) == 1 and diffuse_layers[0][1].get('colorOperation', 5) == 5:
        diffuse_result = diffuse_layers[0][2].outputs['Color']
    else:
        diffuse_encoded = texture_chain('Stage Diffuse Tint', diffuse_layers,
                                        (1, 1, 1, 1) if preview.get('useVertexColor') else color)
        diffuse_result = color_transfer(material, diffuse_encoded, to_linear=True) if diffuse_encoded else None
    if diffuse_result is not None:
        if diffuse_lighting or specular_lighting:
            links.new(diffuse_result, color_input)
        else:
            surface_result = diffuse_result

    if specular_color_input is not None and specular_layers:
        specular = preview.get('specularColor') or [.25, .25, .25, 1]
        specular_encoded = texture_chain('Stage Specular Texture', specular_layers, specular)
        links.new(color_transfer(material, specular_encoded, to_linear=True), specular_color_input)

    if extension_layers:
        if surface_result is None:
            base = nodes.new('ShaderNodeRGB')
            base.name = 'Stage Extension Base'
            base.outputs[0].default_value = (*linear, 1)
            surface_result = base.outputs[0]
        extension_base = color_transfer(material, surface_result, to_linear=False)
        extension_encoded = texture_chain('Stage Extension Texture', extension_layers, extension_base)
        surface_result = color_transfer(material, extension_encoded, to_linear=True)

    if surface_result is not None:
        links.new(surface_result, shader.inputs['Color'])
    nodes.active = samplers[0]
    samplers[0].select = True
    # A legible layout if the user opens the Shader Editor.
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
