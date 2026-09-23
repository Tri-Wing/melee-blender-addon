"""Registration and export of external meshes as append-only stage models."""
import hashlib
import json
import math
import uuid
from pathlib import Path

import bpy
from mathutils import Vector

from .protocol import StageError
from .transforms import AXES


ROLE = 'model-addition'
COLLECTION_ROLE = 'model-additions'
PRESET = 'opaque-texture-focused-v1'


def stage_targets(stage):
    return stage.get('modelAdditionTargets', []) if stage.get('capabilities', {}).get('modelAddition') else []


def objects(scene):
    return [obj for obj in scene.objects if obj.get('mme_role') == ROLE
            and obj.get('mme_session_id') == scene.mme_session_id]


def target_items(_operator, context):
    try:
        stage = json.loads((Path(bpy.path.abspath(context.scene.mme_session)) / 'stage.json').read_text())
        return [(item['id'], item.get('name', item['id']), item.get('readOnlyReason', ''))
                for item in stage_targets(stage)]
    except (OSError, ValueError, KeyError):
        return []


def _collection(scene):
    matches = [collection for collection in bpy.data.collections
               if collection.get('mme_role') == COLLECTION_ROLE
               and collection.get('mme_session_id') == scene.mme_session_id]
    if len(matches) > 1:
        raise StageError('The Added Models collection is duplicated.')
    if matches:
        return matches[0]
    parent = next((collection for collection in bpy.data.collections
                   if collection.get('mme_role') == 'stage'
                   and collection.get('mme_session_id') == scene.mme_session_id), scene.collection)
    collection = bpy.data.collections.new('Added Models')
    parent.children.link(collection)
    collection['mme_role'] = COLLECTION_ROLE
    collection['mme_session_id'] = scene.mme_session_id
    collection['mme_id'] = 'model-additions'
    return collection


def register_selected(context, target_id):
    scene = context.scene
    stage = json.loads((Path(bpy.path.abspath(scene.mme_session)) / 'stage.json').read_text())
    targets = {item['id']: item for item in stage_targets(stage)}
    if target_id not in targets:
        raise StageError('Choose a structurally eligible model-addition attachment from this session.')
    selected = [obj for obj in context.selected_objects if obj.type == 'MESH'
                and obj.get('mme_role') != ROLE]
    if not selected:
        raise StageError('Select at least one external mesh object.')
    collection = _collection(scene)
    created = []
    created_meshes = []
    created_materials = []
    created_images = []
    image_copies = {}
    depsgraph = context.evaluated_depsgraph_get()
    try:
        for source in selected:
            evaluated = source.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
            created_meshes.append(mesh)
            if not mesh.polygons:
                bpy.data.meshes.remove(mesh)
                created_meshes.remove(mesh)
                raise StageError(f'{source.name}: evaluated mesh has no faces.')
            for index, material in enumerate(tuple(mesh.materials)):
                if material is None:
                    continue
                material_copy = material.copy()
                material_copy.name = f'Added Model - {material.name}'
                created_materials.append(material_copy)
                if material_copy.use_nodes and material_copy.node_tree:
                    for node in material_copy.node_tree.nodes:
                        if node.bl_idname != 'ShaderNodeTexImage' or node.image is None:
                            continue
                        key = node.image.as_pointer()
                        if key not in image_copies:
                            source_image = node.image
                            width, height = source_image.size
                            if width <= 0 or height <= 0:
                                raise StageError(f'{source_image.name}: image has no readable pixels.')
                            values = list(source_image.pixels[:])
                            if len(values) != width * height * 4:
                                raise StageError(f'{source_image.name}: image must expose RGBA pixels.')
                            image_copy = bpy.data.images.new(f'Added Model - {source_image.name}',
                                width=width, height=height, alpha=True,
                                float_buffer=source_image.is_float)
                            created_images.append(image_copy)
                            image_copy.colorspace_settings.name = source_image.colorspace_settings.name
                            image_copy.alpha_mode = source_image.alpha_mode
                            image_copy.pixels = values
                            image_copy.update()
                            image_copy.pack()
                            image_copies[key] = image_copy
                        node.image = image_copies[key]
                mesh.materials[index] = material_copy
            copy = bpy.data.objects.new(f'Added Model - {source.name}', mesh)
            collection.objects.link(copy)
            copy.matrix_world = source.matrix_world.copy()
            copy['mme_role'] = ROLE
            copy['mme_session_id'] = scene.mme_session_id
            copy['mme_id'] = uuid.uuid4().hex
            copy['mme_target_jobj_id'] = target_id
            copy['mme_source_name'] = source.name
            copy['mme_material_ids'] = json.dumps([uuid.uuid4().hex for _ in mesh.materials])
            copy['mme_part_ids'] = json.dumps([uuid.uuid4().hex for _ in mesh.materials])
            copy['mme_image_ids'] = json.dumps([uuid.uuid4().hex for _ in mesh.materials])
            for material in mesh.materials:
                _configure_material_preview(material) if material else None
            created.append(copy)
        for image in tuple(created_images):
            if image.users == 0:
                bpy.data.images.remove(image)
                created_images.remove(image)
        for source in selected:
            bpy.data.objects.remove(source, do_unlink=True)
        bpy.ops.object.select_all(action='DESELECT')
        for copy in created:
            copy.select_set(True)
        context.view_layer.objects.active = created[-1]
        return created
    except Exception:
        for copy in created:
            bpy.data.objects.remove(copy, do_unlink=True)
        for mesh in created_meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for material in created_materials:
            if material.users == 0:
                bpy.data.materials.remove(material)
        for image in created_images:
            if image.users == 0:
                bpy.data.images.remove(image)
        if not collection.objects and not collection.children:
            bpy.data.collections.remove(collection)
        raise


def remove_selected(context):
    selected = [obj for obj in context.selected_objects if obj.get('mme_role') == ROLE
                and obj.get('mme_session_id') == context.scene.mme_session_id]
    if not selected:
        raise StageError('Select one or more registered Added Models to remove.')
    for obj in selected:
        mesh = obj.data
        materials = [material for material in mesh.materials if material]
        images = {node.image for material in materials if material.use_nodes and material.node_tree
                  for node in material.node_tree.nodes
                  if node.bl_idname == 'ShaderNodeTexImage' and node.image is not None}
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        for material in materials:
            if material.users == 0:
                bpy.data.materials.remove(material)
        for image in images:
            if image.users == 0:
                bpy.data.images.remove(image)
    if not objects(context.scene):
        context.scene.pop('mme_addition_report', None)
    return len(selected)


def _target_matrix(scene, target_id):
    matches = [(obj, bone) for obj in scene.objects if obj.type == 'ARMATURE'
               for bone in obj.pose.bones if bone.get('mme_id') == target_id]
    if len(matches) != 1:
        raise StageError('The selected model-addition attachment is missing or duplicated.')
    armature, bone = matches[0]
    return armature.matrix_world @ bone.matrix


def _base_color_texture(material, base, warnings):
    """Resolve the one image in a small, lossily convertible Base Color graph."""
    if len(base.links) != 1:
        raise StageError(f'{material.name}: Base Color must have exactly one incoming link.')

    images = []
    modulation = set()
    visited = set()

    def visit(link):
        node = link.from_node
        key = node.as_pointer()
        if key in visited:
            return
        visited.add(key)
        if node.bl_idname == 'ShaderNodeTexImage':
            if link.from_socket.name != 'Color':
                raise StageError(f'{material.name}: Base Color must use an Image Texture Color output.')
            images.append(node)
            return
        if node.bl_idname in {'ShaderNodeVertexColor', 'ShaderNodeAttribute'}:
            modulation.add('vertex color')
            return
        if node.bl_idname in {'ShaderNodeRGB', 'ShaderNodeValue'}:
            modulation.add('constant color')
            return
        if node.bl_idname == 'ShaderNodeMixRGB':
            if node.blend_type != 'MULTIPLY':
                raise StageError(
                    f'{material.name}: the Base Color Mix node must use Multiply.')
        elif node.bl_idname == 'ShaderNodeMix':
            if node.data_type != 'RGBA' or node.blend_type != 'MULTIPLY':
                raise StageError(
                    f'{material.name}: the Base Color Mix node must mix colors using Multiply.')
        else:
            raise StageError(
                f'{material.name}: unsupported {node.name} node in the Base Color path; '
                'use one Image Texture, optionally multiplied by vertex color.')
        modulation.add('multiplication')
        for socket in node.inputs:
            if len(socket.links) > 1:
                raise StageError(
                    f'{material.name}: {node.name} has an ambiguous input connection.')
            if socket.is_linked:
                visit(socket.links[0])

    visit(base.links[0])
    if len(images) != 1:
        raise StageError(
            f'{material.name}: Base Color must resolve to exactly one Image Texture; '
            f'found {len(images)}.')
    if 'vertex color' in modulation:
        warnings.append(
            f'{material.name}: Base Color vertex-color modulation is omitted; '
            'the connected image texture is exported.')
    elif modulation:
        warnings.append(
            f'{material.name}: Base Color multiplication is reduced to its connected image texture.')
    return images[0]


def _material(material, inspect_only=False):
    if material is None or not material.use_nodes or material.node_tree is None:
        color = tuple(material.diffuse_color) if material else (0.8, 0.8, 0.8, 1)
        return color, None, None, 'repeat', 'repeat', 'linear', 'linear', []
    outputs = [node for node in material.node_tree.nodes
               if node.bl_idname == 'ShaderNodeOutputMaterial' and node.is_active_output]
    preview = bool(material.get('mme_model_addition_preview'))
    expected_shader = 'ShaderNodeEmission' if preview else 'ShaderNodeBsdfPrincipled'
    expected_name = 'the Added Model preview' if preview else 'a Principled BSDF'
    if len(outputs) != 1 or not outputs[0].inputs['Surface'].is_linked:
        raise StageError(f'{material.name}: use one active Material Output connected to {expected_name}.')
    shader = outputs[0].inputs['Surface'].links[0].from_node
    if shader.bl_idname != expected_shader:
        if preview:
            raise StageError(f'{material.name}: the registered preview uses an unsupported shader.')
        raise StageError(f'{material.name}: only Principled BSDF base color is supported.')
    base = shader.inputs.get('Color' if preview else 'Base Color')
    if base is None:
        raise StageError(f'{material.name}: supported shader has no color input.')
    try:
        warnings = json.loads(material.get('mme_model_addition_warnings', '[]')) if preview else []
    except (TypeError, ValueError):
        raise StageError(f'{material.name}: stored conversion warnings are corrupt.')
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise StageError(f'{material.name}: stored conversion warnings are corrupt.')
    if not base.is_linked:
        value = tuple(base.default_value)
        return value, None, None, 'repeat', 'repeat', 'linear', 'linear', warnings
    if preview:
        if len(base.links) != 1 or base.links[0].from_node.bl_idname != 'ShaderNodeTexImage' \
                or base.links[0].from_socket.name != 'Color':
            raise StageError(f'{material.name}: registered preview must use one direct Image Texture.')
        texture = base.links[0].from_node
    else:
        texture = _base_color_texture(material, base, warnings)
    image = texture.image
    if image is None or image.source not in {'FILE', 'GENERATED'}:
        raise StageError(f'{material.name}: base-color image is missing or unsupported.')
    if texture.projection != 'FLAT':
        raise StageError(f'{material.name}: only flat UV image projection is supported.')
    extension = texture.extension
    if extension not in {'REPEAT', 'EXTEND'}:
        raise StageError(f'{material.name}: texture extension must be Repeat or Extend.')
    interpolation = texture.interpolation
    if interpolation not in {'Closest', 'Linear'}:
        warnings.append(f'{material.name}: {interpolation} filtering is reduced to linear.')
    vector = texture.inputs['Vector']
    uv_name = None
    if vector.is_linked:
        if len(vector.links) != 1:
            raise StageError(f'{material.name}: image Vector must have at most one incoming link.')
        link = vector.links[0]
        node = link.from_node
        if node.bl_idname == 'ShaderNodeUVMap':
            uv_name = node.uv_map or None
        elif node.bl_idname == 'ShaderNodeTexCoord' and link.from_socket.name == 'UV':
            uv_name = None
        else:
            raise StageError(
                f'{material.name}: image Vector must be unlinked, a direct UV Map, '
                'or the UV output of Texture Coordinate.')
    if image.channels < 4:
        warnings.append(f'{material.name}: missing image alpha is exported opaque.')
    if not inspect_only:
        warnings.append(f'{material.name}: metallic, roughness, normal and other shader inputs are omitted.')
    wrap = 'repeat' if extension == 'REPEAT' else 'clamp'
    filtering = 'nearest' if interpolation == 'Closest' else 'linear'
    return (1, 1, 1, 1), image, uv_name, wrap, wrap, filtering, filtering, warnings


def _configure_material_preview(material):
    """Replace an imported shader with the exact opaque addition-preset preview."""
    color, image, uv_name, wrap_s, wrap_t, min_filter, mag_filter, warnings = \
        _material(material, inspect_only=True)
    if wrap_s != wrap_t or min_filter != mag_filter:
        raise StageError(f'{material.name}: addition preview requires matching texture axes and filters.')
    material['mme_model_addition_preview'] = True
    material['mme_model_addition_warnings'] = json.dumps(warnings)
    material.diffuse_color = color
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    output.name = 'Added Model Output'
    output.is_active_output = True
    shader = nodes.new('ShaderNodeEmission')
    shader.name = 'Added Model Opaque Surface'
    shader.inputs['Strength'].default_value = 1
    material.node_tree.links.new(shader.outputs['Emission'], output.inputs['Surface'])
    if image is None:
        shader.inputs['Color'].default_value = color
        return
    texture = nodes.new('ShaderNodeTexImage')
    texture.name = 'Added Model Base Color'
    texture.image = image
    texture.projection = 'FLAT'
    texture.extension = 'REPEAT' if wrap_s == 'repeat' else 'EXTEND'
    texture.interpolation = 'Closest' if mag_filter == 'nearest' else 'Linear'
    material.node_tree.links.new(texture.outputs['Color'], shader.inputs['Color'])
    if uv_name:
        uv = nodes.new('ShaderNodeUVMap')
        uv.name = 'Added Model UV'
        uv.uv_map = uv_name
        material.node_tree.links.new(uv.outputs['UV'], texture.inputs['Vector'])


def _rgba(image):
    width, height = image.size
    if width <= 0 or height <= 0:
        raise StageError(f'{image.name}: image has no readable pixels.')
    values = list(image.pixels[:])
    if len(values) != width * height * image.channels:
        raise StageError(f'{image.name}: image pixel buffer is incomplete.')
    color_space = image.colorspace_settings.name.lower()
    if color_space not in {'srgb', 's-rgb', 'non-color'}:
        raise StageError(f'{image.name}: use the sRGB or Non-Color image color space for base-color export.')
    if image.alpha_mode == 'PREMUL':
        raise StageError(f'{image.name}: convert premultiplied alpha to Straight before base-color export.')
    def channel(value):
        value = max(0.0, min(1.0, float(value)))
        # Image.pixels exposes the image's stored channel values. The color-space
        # setting controls shader interpretation; applying an additional transfer
        # function here double-encodes ordinary sRGB files and washes them out.
        return round(value * 255)

    result = bytearray(width * height * 4)
    for output_y in range(height):
        input_y = height - 1 - output_y
        for x in range(width):
            source = (input_y * width + x) * image.channels
            target = (output_y * width + x) * 4
            result[target:target + 4] = bytes((channel(values[source]),
                channel(values[source + 1] if image.channels > 1 else values[source]),
                channel(values[source + 2] if image.channels > 2 else values[source]),
                round(max(0.0, min(1.0, values[source + 3]
                    if image.channels > 3 and image.alpha_mode != 'NONE' else 1.0)) * 255)))
    return width, height, bytes(result)


def edits(scene, stage):
    additions = objects(scene)
    if not additions:
        return None, {}
    if not stage.get('capabilities', {}).get('modelAddition'):
        raise StageError('This session backend does not support model additions. Re-import with a matching backend.')
    targets = {item['id'] for item in stage_targets(stage)}
    material_records, image_records, addition_records, assets = [], [], [], {}
    warnings = []
    identifiers = set()

    def use_id(value, label):
        if not isinstance(value, str) or len(value) != 32 \
                or any(character not in '0123456789abcdef' for character in value) \
                or value in identifiers:
            raise StageError(f'{label} has a missing, corrupt, or duplicated registration identity.')
        identifiers.add(value)

    def display_name(value, fallback):
        value = ''.join(character for character in str(value) if character.isprintable())[:128]
        return value or fallback

    for obj in additions:
        use_id(obj.get('mme_id'), obj.name)
        target_id = obj.get('mme_target_jobj_id')
        if target_id not in targets:
            raise StageError(f'{obj.name}: model-addition attachment is no longer available.')
        if obj.type != 'MESH' or obj.constraints or obj.modifiers or obj.data.shape_keys or obj.animation_data:
            raise StageError(f'{obj.name}: registered additions must remain static meshes without modifiers, constraints, shape keys or animation.')
        mesh = obj.data
        mesh.calc_loop_triangles()
        if not mesh.loop_triangles:
            raise StageError(f'{obj.name}: addition has no triangles.')
        material_ids = json.loads(obj.get('mme_material_ids', '[]'))
        part_ids = json.loads(obj.get('mme_part_ids', '[]'))
        image_ids = json.loads(obj.get('mme_image_ids', '[]'))
        if not (len(material_ids) == len(part_ids) == len(image_ids) == len(mesh.materials)):
            raise StageError(f'{obj.name}: stored addition material identities are corrupt.')
        target_inverse = _target_matrix(scene, target_id).inverted_safe()
        local_to_target = target_inverse @ obj.matrix_world
        if abs(local_to_target.to_3x3().determinant()) < 1e-12:
            raise StageError(f'{obj.name}: addition transform has a zero scale axis.')
        normal_matrix = local_to_target.to_3x3().inverted_safe().transposed()
        to_game = AXES.inverted()
        positions = []
        for vertex in mesh.vertices:
            point = to_game @ (local_to_target @ vertex.co)
            positions.append(dict(zip(('x', 'y', 'z'), point)))
        reverse = local_to_target.to_3x3().determinant() < 0
        parts = []
        used_slots = sorted({triangle.material_index for triangle in mesh.loop_triangles})
        for slot in used_slots:
            if slot >= len(mesh.materials) or mesh.materials[slot] is None:
                raise StageError(f'{obj.name}: every face must use a material slot with a material.')
            material = mesh.materials[slot]
            use_id(material_ids[slot], f'{obj.name} material slot {slot}')
            use_id(part_ids[slot], f'{obj.name} geometry part {slot}')
            color, image, uv_name, wrap_s, wrap_t, min_filter, mag_filter, losses = _material(material)
            warnings.extend(losses)
            image_id = image_ids[slot] if image else None
            if image:
                use_id(image_id, f'{obj.name} image slot {slot}')
                width, height, pixels = _rgba(image)
                if any(pixels[index] != 255 for index in range(3, len(pixels), 4)):
                    warnings.append(f'{material.name}: texture alpha is embedded but renders opaque.')
                relative = f'edits/addition-assets/{image_id}.rgba'
                assets[relative] = pixels
                image_records.append({'id': image_id, 'width': width, 'height': height,
                    'pixelEncoding': 'rgba8-srgb-straight-top-left', 'payloadPath': relative,
                    'sha256': hashlib.sha256(pixels).hexdigest()})
            elif color[3] < 1:
                warnings.append(f'{material.name}: material alpha is ignored by the opaque preset.')
            material_records.append({'id': material_ids[slot], 'name': display_name(material.name, 'Material'),
                'baseColor': dict(zip(('r', 'g', 'b', 'a'), color)), 'imageId': image_id,
                'wrapS': wrap_s, 'wrapT': wrap_t, 'minFilter': min_filter,
                'magFilter': mag_filter, 'preset': PRESET})
            uv_layer = None
            if image:
                uv_layer = mesh.uv_layers.get(uv_name) if uv_name else mesh.uv_layers.active
                if uv_layer is None:
                    raise StageError(f'{obj.name} / {material.name}: textured faces require the selected UV map.')
            indices, normals, uvs = [], [], [] if image else None
            for triangle in (item for item in mesh.loop_triangles if item.material_index == slot):
                corners = [0, 2, 1] if reverse else [0, 1, 2]
                for corner in corners:
                    loop_index = triangle.loops[corner]
                    indices.append(triangle.vertices[corner])
                    normal = to_game.to_3x3() @ (normal_matrix @ mesh.corner_normals[loop_index].vector)
                    if normal.length_squared == 0 or not all(math.isfinite(value) for value in normal):
                        raise StageError(f'{obj.name}: evaluated corner normal is invalid.')
                    normal.normalize()
                    normals.append(dict(zip(('x', 'y', 'z'), normal)))
                    if uvs is not None:
                        uv = uv_layer.data[loop_index].uv
                        uvs.append({'x': uv.x, 'y': 1 - uv.y})
            parts.append({'id': part_ids[slot], 'materialId': material_ids[slot],
                'positions': positions, 'triangleIndices': indices,
                'cornerNormals': normals, 'texCoords0': uvs})
        addition_records.append({'id': obj['mme_id'],
            'name': display_name(obj.get('mme_source_name', obj.name), 'Added Model'),
            'targetJobjId': target_id, 'parts': parts})
    scene['mme_addition_report'] = json.dumps({'objects': len(addition_records),
        'parts': sum(len(item['parts']) for item in addition_records),
        'triangles': sum(len(part['triangleIndices']) // 3 for item in addition_records
                         for part in item['parts']),
        'chunks': sum((len(part['triangleIndices']) // 3 + 13_999) // 14_000
                      for item in addition_records for part in item['parts']),
        'images': len(image_records), 'textureBytes': sum(len(value) for value in assets.values()),
        'imageDimensions': [f"{image['width']}×{image['height']}" for image in image_records],
        'warnings': sorted(set(warnings))})
    return {'protocolVersion': 2, 'modelAdditionSchemaVersion': 1,
        'coordinateSpace': 'game-joint-local', 'additions': addition_records,
        'materials': material_records, 'images': image_records}, assets
