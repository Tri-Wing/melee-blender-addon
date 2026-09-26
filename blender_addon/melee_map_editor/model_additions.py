"""Registration and export of external meshes as append-only stage models."""
import hashlib
import json
import math
import uuid
from pathlib import Path

import bpy
import bmesh
from mathutils import Matrix

from .protocol import SESSION_PROTOCOL, StageError
from .transforms import AXES
from . import lighting, surface


ROLE = 'model-addition'
DOBJ_ROLE = 'model-addition-dobj'
JOBJ_ROLE = 'model-addition-jobj'
COLLECTION_ROLE = 'model-additions'
UNLIT_PRESET = 'opaque-texture-focused-v1'
DIFFUSE_PRESET = 'opaque-diffuse-texture-v2'
_TARGET_ITEMS_CACHE = {}


def stage_targets(stage):
    return stage.get('modelAdditionTargets', []) if stage.get('capabilities', {}).get('modelAddition') else []


def objects(scene):
    return [obj for obj in scene.objects if obj.get('mme_role') == ROLE
            and obj.get('mme_session_id') == scene.mme_session_id]


def is_pending(item):
    return item.get('mme_role') in {ROLE, DOBJ_ROLE, JOBJ_ROLE}


def target_items(_operator, context):
    try:
        manifest = Path(bpy.path.abspath(context.scene.mme_session)) / 'stage.json'
        stage = json.loads(manifest.read_text())
        cache_key = (str(manifest.resolve()), stage.get('source', {}).get('sha256'),
                     stage.get('modelAdditionSchemaVersion'))
        if cache_key in _TARGET_ITEMS_CACHE:
            return _TARGET_ITEMS_CACHE[cache_key]
        descriptions = {
            'existing-jobj': 'Append geometry to this existing JOBJ using the unlit opaque preset.',
            'new-jobj-chain': 'Create a separately lit JOBJ beneath this model-group root.'}
        prefixes = {'existing-jobj': 'Existing JOBJ', 'new-jobj-chain': 'New JOBJ Chain'}
        targets = sorted(stage_targets(stage), key=lambda item:
                         (item.get('placement') != 'new-jobj-chain', item.get('name', '')))
        # Blender's dynamic EnumProperty retains pointers to callback strings.
        # Keep the complete tuple alive for the lifetime of this loaded module.
        items = tuple((item['id'], f"{prefixes.get(item.get('placement'), 'Placement')} — "
                       f"{item.get('name', item['id'])}",
                       descriptions.get(item.get('placement'), item.get('readOnlyReason', '')))
                      for item in targets)
        _TARGET_ITEMS_CACHE[cache_key] = items
        return items
    except (OSError, ValueError, KeyError):
        return []


def _collection(scene, target):
    matches = [collection for collection in bpy.data.collections
               if collection.get('mme_role') == 'group'
               and collection.get('mme_session_id') == scene.mme_session_id
               and collection.get('mme_group_index') == target['groupIndex']]
    if len(matches) > 1:
        raise StageError('The selected model-group collection is duplicated.')
    if not matches:
        raise StageError('The selected model-group collection is missing.')
    return matches[0]


def _target_binding(scene, target):
    anchor_id = target.get('anchorJobjId')
    matches = [(obj, bone) for obj in scene.objects if obj.type == 'ARMATURE'
               for bone in obj.pose.bones if bone.get('mme_id') == anchor_id]
    if len(matches) != 1:
        raise StageError('The selected model-addition attachment is missing or duplicated.')
    return matches[0]


def _display_jobj_index(scene, target):
    if target['placement'] == 'existing-jobj':
        return target['jobjIndex']
    source_indices = [bone.get('mme_source_index') for obj in scene.objects
                      if obj.type == 'ARMATURE'
                      and obj.get('mme_group_index') == target['groupIndex']
                      for bone in obj.pose.bones
                      if isinstance(bone.get('mme_source_index'), int)]
    return max(source_indices) + 1 if source_indices else 0


def _slot_id(addition_id, kind, slot):
    return hashlib.sha256(
        f'mme-model-addition-v2:{addition_id}:{kind}:{slot}'.encode()).hexdigest()[:32]


def _bone_parent(obj, armature, bone):
    obj.parent = armature
    obj.parent_type = 'BONE'
    obj.parent_bone = bone.name
    obj.matrix_parent_inverse = Matrix.Translation((0, -0.25, 0))
    obj.matrix_basis = Matrix.Identity(4)


def _new_jobj_bone(context, armature, parent_bone, index, addition_id, target, stage):
    selected = list(context.selected_objects)
    active = context.view_layer.objects.active
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    armature.select_set(True)
    context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='EDIT')
    bone = armature.data.edit_bones.new('JOBJ')
    bone.head = (0, 0, 0)
    bone.tail = (0, 0.25, 0)
    bone.parent = armature.data.edit_bones[parent_bone.name]
    bone.use_connect = False
    name = bone.name
    bpy.ops.object.mode_set(mode='OBJECT')
    pose_bone = armature.pose.bones[name]
    for item in (pose_bone, pose_bone.bone):
        item['mme_role'] = JOBJ_ROLE
        item['mme_session_id'] = context.scene.mme_session_id
        item['mme_id'] = addition_id
        item['mme_addition_id'] = addition_id
        item['mme_target_jobj_id'] = target['id']
        item['mme_group_index'] = target['groupIndex']
        item['mme_owner_id'] = target['anchorJobjId']
        item['mme_source_index'] = index
        item['mme_source_hash'] = stage['source']['sha256']
    pose_bone.rotation_mode = 'XYZ'
    pose_bone.matrix_basis = Matrix.Identity(4)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in selected:
        if obj.name in context.scene.objects:
            obj.select_set(True)
    if active and active.name in context.scene.objects:
        context.view_layer.objects.active = active
    return pose_bone


def _remove_jobj_bones(context, addition_ids):
    targets = [(obj, bone.name) for obj in context.scene.objects if obj.type == 'ARMATURE'
               for bone in obj.pose.bones if bone.get('mme_role') == JOBJ_ROLE
               and bone.get('mme_id') in addition_ids]
    if not targets:
        return
    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for armature, names in ((armature, [name for owner, name in targets if owner == armature])
                            for armature in {owner for owner, _name in targets}):
        bpy.ops.object.select_all(action='DESELECT')
        armature.select_set(True)
        context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode='EDIT')
        for name in names:
            bone = armature.data.edit_bones.get(name)
            if bone is not None:
                armature.data.edit_bones.remove(bone)
        bpy.ops.object.mode_set(mode='OBJECT')


def _part_mesh(source, slot, name):
    result = source.copy()
    result.name = name
    bm = bmesh.new()
    try:
        bm.from_mesh(result)
        discarded = [face for face in bm.faces if face.material_index != slot]
        if discarded:
            bmesh.ops.delete(bm, geom=discarded, context='FACES')
        loose = [vertex for vertex in bm.verts if not vertex.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context='VERTS')
        bm.to_mesh(result)
    finally:
        bm.free()
    material = source.materials[slot]
    result.materials.clear()
    result.materials.append(material)
    for polygon in result.polygons:
        polygon.material_index = 0
    result.update()
    return result


def register_selected(context, target_id):
    scene = context.scene
    stage = json.loads((Path(bpy.path.abspath(scene.mme_session)) / 'stage.json').read_text())
    if stage.get('modelAdditionSchemaVersion') != 2:
        raise StageError('New-JOBJ model addition requires a fresh stage import with the current backend.')
    targets = {item['id']: item for item in stage_targets(stage)}
    if target_id not in targets:
        raise StageError('Choose a structurally eligible model-addition attachment from this session.')
    target = targets[target_id]
    selected = [obj for obj in context.selected_objects if obj.type == 'MESH'
                and obj.get('mme_role') != ROLE]
    if not selected:
        raise StageError('Select at least one external mesh object.')
    collection = _collection(scene, target)
    armature, bone = _target_binding(scene, target)
    created = []
    created_meshes = []
    created_materials = []
    created_images = []
    created_bones = []
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
            used_slots = sorted({polygon.material_index for polygon in mesh.polygons})
            if any(slot >= len(mesh.materials) or mesh.materials[slot] is None for slot in used_slots):
                raise StageError(f'{source.name}: every face must use a material slot with a material.')
            addition_id = uuid.uuid4().hex
            jobj_index = _display_jobj_index(scene, target)
            jobj = None
            if target['placement'] == 'new-jobj-chain':
                jobj = _new_jobj_bone(context, armature, bone, jobj_index,
                                       addition_id, target, stage)
                created_bones.append(addition_id)
            for material in mesh.materials:
                _configure_material_preview(material, target, stage) if material else None
            existing_dobjs = sum(obj.get('mme_role') == 'dobj'
                                 and obj.get('mme_owner_id') == target['anchorJobjId']
                                 for obj in scene.objects) if target['placement'] == 'existing-jobj' else 0
            pending_dobjs = sum(obj.get('mme_role') == DOBJ_ROLE
                                and obj.get('mme_target_jobj_id') == target_id
                                for obj in scene.objects) if target['placement'] == 'existing-jobj' else 0
            for partition, slot in enumerate(used_slots):
                dobj_index = existing_dobjs + pending_dobjs + partition \
                    if target['placement'] == 'existing-jobj' else partition
                dobj = bpy.data.objects.new('DOBJ', None)
                created.append(dobj)
                collection.objects.link(dobj)
                dobj['mme_role'] = DOBJ_ROLE
                dobj['mme_session_id'] = scene.mme_session_id
                dobj['mme_id'] = uuid.uuid4().hex
                dobj['mme_addition_id'] = addition_id
                dobj['mme_target_jobj_id'] = target_id
                dobj['mme_group_index'] = target['groupIndex']
                dobj['mme_owner_id'] = jobj['mme_id'] if jobj else target['anchorJobjId']
                dobj['mme_source_index'] = dobj_index
                dobj['mme_source_hash'] = stage['source']['sha256']
                if jobj:
                    _bone_parent(dobj, armature, jobj)
                else:
                    _bone_parent(dobj, armature, bone)
                part_mesh = _part_mesh(mesh, slot, f'{source.name} Mesh')
                created_meshes.append(part_mesh)
                pobj = bpy.data.objects.new(source.name, part_mesh)
                created.append(pobj)
                collection.objects.link(pobj)
                pobj['mme_role'] = ROLE
                pobj['mme_session_id'] = scene.mme_session_id
                pobj['mme_id'] = uuid.uuid4().hex
                pobj['mme_group_index'] = target['groupIndex']
                pobj['mme_owner_id'] = dobj['mme_id']
                pobj['mme_source_index'] = 0
                pobj['mme_source_hash'] = stage['source']['sha256']
                pobj['mme_editable'] = True
                pobj['mme_model_edit_scope'] = 'full-geometry'
                pobj.parent = dobj
                pobj.matrix_world = source.matrix_world.copy()
            bpy.data.meshes.remove(mesh)
            created_meshes.remove(mesh)
        for image in tuple(created_images):
            if image.users == 0:
                bpy.data.images.remove(image)
                created_images.remove(image)
        for material in tuple(created_materials):
            if material.users == 0:
                bpy.data.materials.remove(material)
                created_materials.remove(material)
        for source in selected:
            bpy.data.objects.remove(source, do_unlink=True)
        bpy.ops.object.select_all(action='DESELECT')
        registered = [obj for obj in created if obj.get('mme_role') == ROLE]
        for obj in registered:
            obj.select_set(True)
        context.view_layer.objects.active = registered[-1]
        return registered
    except Exception:
        for copy in created:
            bpy.data.objects.remove(copy, do_unlink=True)
        _remove_jobj_bones(context, set(created_bones))
        for mesh in created_meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for material in created_materials:
            if material.users == 0:
                bpy.data.materials.remove(material)
        for image in created_images:
            if image.users == 0:
                bpy.data.images.remove(image)
        raise


def _target_matrix(scene, target):
    armature, bone = _target_binding(scene, target)
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
    if preview:
        if shader.name not in {'Stage Surface', 'Added Model Opaque Surface'}:
            raise StageError(f'{material.name}: registered preview surface is missing; re-register the model.')
        try:
            settings = json.loads(material.get('mme_model_addition_settings', ''))
        except (TypeError, ValueError):
            raise StageError(f'{material.name}: stored conversion settings are corrupt; re-register the model.')
        if not isinstance(settings, dict) or settings.get('placement') not in {
                'existing-jobj', 'new-jobj-chain'}:
            raise StageError(f'{material.name}: stored conversion settings are corrupt; re-register the model.')
        placement = settings['placement']
        if material.get('mme_model_addition_preview') != placement:
            raise StageError(f'{material.name}: registered preview mode is corrupt; re-register the model.')
        warnings = settings.get('warnings')
        color = settings.get('color')
        if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings) \
                or not isinstance(color, list) or len(color) != 4 \
                or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in color):
            raise StageError(f'{material.name}: stored conversion settings are corrupt; re-register the model.')
        if not settings.get('textured'):
            if placement == 'new-jobj-chain':
                diffuse = material.node_tree.nodes.get('Stage Diffuse Lighting')
                if diffuse is None or not shader.inputs['Color'].is_linked \
                        or shader.inputs['Color'].links[0].from_node != diffuse \
                        or diffuse.inputs[1].is_linked:
                    raise StageError(f'{material.name}: registered diffuse preview was modified; re-register the model.')
            elif shader.inputs['Color'].is_linked:
                raise StageError(f'{material.name}: registered unlit preview was modified; re-register the model.')
            return tuple(color), None, None, 'repeat', 'repeat', 'linear', 'linear', warnings
        texture = material.node_tree.nodes.get('Added Model Base Color')
        if texture is None or texture.bl_idname != 'ShaderNodeTexImage':
            raise StageError(f'{material.name}: registered base-color texture is missing; re-register the model.')
        base = shader.inputs['Color']
        if placement == 'new-jobj-chain':
            diffuse = material.node_tree.nodes.get('Stage Diffuse Lighting')
            if diffuse is None or not base.is_linked or base.links[0].from_node != diffuse:
                raise StageError(f'{material.name}: registered diffuse preview was modified; re-register the model.')
            base = diffuse.inputs[1]
        if len(base.links) != 1 or base.links[0].from_node != texture \
                or base.links[0].from_socket.name != 'Color':
            raise StageError(f'{material.name}: registered base-color preview was modified; re-register the model.')
    else:
        base = shader.inputs.get('Base Color')
        if base is None:
            raise StageError(f'{material.name}: supported shader has no Base Color input.')
        warnings = []
        if not base.is_linked:
            value = tuple(base.default_value)
            return value, None, None, 'repeat', 'repeat', 'linear', 'linear', warnings
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
    return tuple(color) if preview else (1, 1, 1, 1), image, uv_name, wrap, wrap, filtering, filtering, warnings


def _configure_material_preview(material, target, stage):
    """Replace an imported shader with the exact opaque addition-preset preview."""
    color, image, uv_name, wrap_s, wrap_t, min_filter, mag_filter, warnings = \
        _material(material, inspect_only=True)
    if wrap_s != wrap_t or min_filter != mag_filter:
        raise StageError(f'{material.name}: addition preview requires matching texture axes and filters.')
    placement = target['placement']
    material['mme_model_addition_preview'] = placement
    material['mme_model_addition_settings'] = json.dumps({
        'color': list(color), 'textured': image is not None, 'uvName': uv_name,
        'wrapS': wrap_s, 'wrapT': wrap_t, 'minFilter': min_filter,
        'magFilter': mag_filter, 'warnings': warnings, 'placement': placement})
    material.diffuse_color = color
    material.use_nodes = True
    if placement == 'new-jobj-chain':
        def ambient(value):
            channel = max(0, min(255, math.floor(value * 255 + 0.5)))
            return (channel // 2) / 255
        preview = {'color': list(color), 'ambientColor': [ambient(color[0]),
            ambient(color[1]), ambient(color[2]), 1], 'diffuseLighting': True,
            'specularLighting': False}
        source_hash = stage.get('source', {}).get('sha256')
        surface.configure_preview(material, preview, None, stage,
                                  lighting.object_map(stage, source_hash))
        nodes = material.node_tree.nodes
        if image is None:
            return
        texture = nodes.new('ShaderNodeTexImage')
        texture.name = 'Added Model Base Color'
        texture.image = image
        texture.projection = 'FLAT'
        texture.extension = 'REPEAT' if wrap_s == 'repeat' else 'EXTEND'
        texture.interpolation = 'Closest' if mag_filter == 'nearest' else 'Linear'
        diffuse = nodes.get('Stage Diffuse Lighting')
        if diffuse is None:
            raise StageError(f'{material.name}: stage diffuse preview could not be created.')
        material.node_tree.links.new(texture.outputs['Color'], diffuse.inputs[1])
        if uv_name:
            uv = nodes.new('ShaderNodeUVMap')
            uv.name = 'Added Model UV'
            uv.uv_map = uv_name
            material.node_tree.links.new(uv.outputs['UV'], texture.inputs['Vector'])
        return
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
    if stage.get('modelAdditionSchemaVersion') != 2:
        raise StageError('Pending imported models use an older schema; re-import the stage and register them again.')
    targets = {item['id']: item for item in stage_targets(stage)}
    material_records, image_records, addition_records, assets = [], [], [], {}
    additions_by_id = {}
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
        dobj = obj.parent
        if dobj is None or dobj.get('mme_role') != DOBJ_ROLE \
                or dobj.get('mme_session_id') != scene.mme_session_id \
                or obj.get('mme_owner_id') != dobj.get('mme_id'):
            raise StageError(f'{obj.name}: DOBJ parent is missing or changed; re-register the model.')
        addition_id = dobj.get('mme_addition_id')
        target_id = dobj.get('mme_target_jobj_id')
        if target_id not in targets:
            raise StageError(f'{obj.name}: model-addition attachment is no longer available.')
        target = targets[target_id]
        placement = target['placement']
        if obj.get('mme_group_index') != target['groupIndex'] \
                or dobj.get('mme_group_index') != target['groupIndex'] \
                or obj.get('mme_source_hash') != stage['source']['sha256'] \
                or dobj.get('mme_source_hash') != stage['source']['sha256']:
            raise StageError(f'{obj.name}: model-group identity is corrupt; re-register the model.')
        parent_bone = (dobj.parent.pose.bones.get(dobj.parent_bone)
                       if dobj.parent and dobj.parent.type == 'ARMATURE'
                       and dobj.parent_type == 'BONE' else None)
        if placement == 'new-jobj-chain':
            jobj = parent_bone
            if jobj is None or jobj.get('mme_role') != JOBJ_ROLE \
                    or jobj.get('mme_id') != addition_id \
                    or jobj.get('mme_target_jobj_id') != target_id \
                    or dobj.get('mme_owner_id') != addition_id \
                    or jobj.parent is None or jobj.parent.get('mme_id') != target['anchorJobjId']:
                raise StageError(f'{obj.name}: generated JOBJ hierarchy is missing or changed.')
        elif dobj.get('mme_owner_id') != target['anchorJobjId'] \
                or parent_bone is None or parent_bone.get('mme_id') != target['anchorJobjId']:
            raise StageError(f'{obj.name}: existing JOBJ ownership is missing or changed.')
        if addition_id not in additions_by_id:
            use_id(addition_id, f'{obj.name} JOBJ addition')
            additions_by_id[addition_id] = {'id': addition_id,
                'name': display_name(obj.name, 'Editable Model'),
                'placement': placement, 'targetJobjId': target_id, 'parts': []}
            addition_records.append(additions_by_id[addition_id])
        elif additions_by_id[addition_id]['targetJobjId'] != target_id:
            raise StageError(f'{obj.name}: DOBJ branches disagree on their JOBJ target.')
        if obj.type != 'MESH' or obj.constraints or obj.modifiers or obj.data.shape_keys or obj.animation_data:
            raise StageError(f'{obj.name}: registered additions must remain static meshes without modifiers, constraints, shape keys or animation.')
        mesh = obj.data
        mesh.calc_loop_triangles()
        if not mesh.loop_triangles:
            raise StageError(f'{obj.name}: addition has no triangles.')
        target_inverse = _target_matrix(scene, target).inverted_safe()
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
        used_slots = sorted({triangle.material_index for triangle in mesh.loop_triangles})
        if used_slots != [0] or len(mesh.materials) != 1:
            raise StageError(f'{obj.name}: each imported POBJ must retain its single material partition.')
        for slot in used_slots:
            if slot >= len(mesh.materials) or mesh.materials[slot] is None:
                raise StageError(f'{obj.name}: every face must use a material slot with a material.')
            material = mesh.materials[slot]
            part_id = obj.get('mme_id')
            material_id = _slot_id(part_id, 'material', slot)
            use_id(part_id, f'{obj.name} POBJ')
            use_id(material_id, f'{obj.name} material slot {slot}')
            color, image, uv_name, wrap_s, wrap_t, min_filter, mag_filter, losses = _material(material)
            warnings.extend(losses)
            image_id = _slot_id(obj['mme_id'], 'image', slot) if image else None
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
            material_records.append({'id': material_id, 'name': display_name(material.name, 'Material'),
                'baseColor': dict(zip(('r', 'g', 'b', 'a'), color)), 'imageId': image_id,
                'wrapS': wrap_s, 'wrapT': wrap_t, 'minFilter': min_filter,
                'magFilter': mag_filter, 'preset': DIFFUSE_PRESET
                if placement == 'new-jobj-chain' else UNLIT_PRESET})
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
            additions_by_id[addition_id]['parts'].append({'id': part_id, 'materialId': material_id,
                'positions': positions, 'triangleIndices': indices,
                'cornerNormals': normals, 'texCoords0': uvs})
    scene['mme_addition_report'] = json.dumps({'objects': len(addition_records),
        'parts': sum(len(item['parts']) for item in addition_records),
        'triangles': sum(len(part['triangleIndices']) // 3 for item in addition_records
                         for part in item['parts']),
        'chunks': sum((len(part['triangleIndices']) // 3 + 13_999) // 14_000
                      for item in addition_records for part in item['parts']),
        'images': len(image_records), 'textureBytes': sum(len(value) for value in assets.values()),
        'imageDimensions': [f"{image['width']}×{image['height']}" for image in image_records],
        'warnings': sorted(set(warnings))})
    return {'protocolVersion': SESSION_PROTOCOL, 'modelAdditionSchemaVersion': 2,
        'coordinateSpace': 'game-joint-local', 'additions': addition_records,
        'materials': material_records, 'images': image_records}, assets
