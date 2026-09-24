"""Scene import, protected scene inventory, and transactional collision export."""
import json
from pathlib import Path
import tempfile
import uuid
import bpy
from mathutils import Matrix
from . import animations, atmosphere, camera, collision, gameplay, jobjs, lighting, modeling, model_additions, surface
from .protocol import StageError, digest, load_session, read, run
from .transforms import AXES, deform_rest, joint_matrices, joint_srt, mesh_binding, mesh_pose


def session(scene):
    if not scene.mme_session:
        raise StageError('Import a stage first.')
    return Path(bpy.path.abspath(scene.mme_session))


def collision_object(scene):
    matches = [obj for obj in scene.objects if obj.get('mme_role') == 'collision'
               and obj.get('mme_session_id') == scene.mme_session_id]
    if len(matches) != 1:
        raise StageError('The protected collision object is missing or duplicated.')
    return matches[0]


def properties(item):
    preview_controls = {'mme_light_enabled', 'mme_light_intensity', 'mme_light_color'}
    return {key: item[key] for key in item.keys()
            if key.startswith('mme_') and key != 'mme_dirty' and key not in preview_controls
            and not (isinstance(item, bpy.types.Object) and key.startswith('mme_anim_'))}


def inventory(scene, editable_id=None, editable_transform_ids=None):
    editable_ids = {editable_id} if isinstance(editable_id, str) else set(editable_id or ())
    editable_transform_ids = set(editable_transform_ids or ())
    sid = scene.mme_session_id
    collections = [c for c in bpy.data.collections if c.get('mme_session_id') == sid
                   and c.get('mme_role') != model_additions.COLLECTION_ROLE]
    # Preview cameras are disposable editor aids. Moving, renaming, or deleting
    # one must never turn into a DAT edit or fail protected-scene validation.
    objects = [o for o in bpy.data.objects if o.get('mme_session_id') == sid
               and o.get('mme_role') != 'preview-camera'
               and not model_additions.is_pending(o) and not gameplay.is_pending(o)]
    result = {'collections': [], 'objects': []}
    session_actions = [action for action in bpy.data.actions
                       if action.get('mme_session_id') == sid]
    if session_actions:
        result['actions'] = []
    for action in session_actions:
        if action.get('mme_role') != 'jobj-animation':
            raise StageError(f'{action.name}: unsupported animation data is not allowed.')
        result['actions'].append({'props': properties(action), 'name': action.name,
            'fakeUser': action.use_fake_user, 'curves': animations.structure(scene, action)})
    for c in collections:
        result['collections'].append({'props': properties(c),
            'parents': sorted(p.get('mme_id', p.name) for p in bpy.data.collections if c.name in p.children)
                       + (['SCENE'] if c.name in scene.collection.children else []),
            'objects': sorted(o.get('mme_id', o.name) for o in c.objects
                              if o.get('mme_role') != 'preview-camera'
                              and not model_additions.is_pending(o)
                              and not gameplay.is_pending(o)),
            'children': sorted(x.get('mme_id', x.name) for x in c.children
                               if x.get('mme_role') != model_additions.COLLECTION_ROLE)})
    for o in objects:
        if o.mode == 'EDIT':
            o.update_from_editmode()
        row = {'props': properties(o), 'parent': o.parent.get('mme_id', o.parent.name) if o.parent else None,
               'collections': sorted(c.get('mme_id', c.name) for c in o.users_collection),
               'type': o.type, 'inScene': o.name in scene.objects}
        if o.get('mme_role') != 'light':
            # LOBJ transforms are editable light data rather than protected hierarchy.
            row['parentInverse'] = [list(r) for r in o.matrix_parent_inverse]
            if o.get('mme_id') not in editable_transform_ids:
                row['matrix'] = [list(r) for r in o.matrix_basis]
        animation_data = o.animation_data
        valid_animation = (o.get('mme_role') == 'jobj-armature' and animation_data is not None
            and (animation_data.action is None or
                 (animation_data.action.get('mme_role') == 'jobj-animation'
                  and animation_data.action.get('mme_session_id') == sid
                  and animation_data.action.get('mme_group_index') == o.get('mme_group_index')))
            and not animation_data.nla_tracks and not animation_data.drivers)
        if o.constraints or (animation_data and not valid_animation) \
                or (o.data and o.data.animation_data) or (o.type == 'MESH' and o.data.shape_keys):
            raise StageError(f'{o.name}: constraints, shape keys and animation are not supported.')
        if o.modifiers:
            valid = (o.type == 'MESH' and o.get('mme_enveloped') and len(o.modifiers) == 1
                and o.modifiers[0].type == 'ARMATURE'
                and o.modifiers[0].object is not None
                and o.modifiers[0].object.get('mme_role') == 'jobj-armature')
            if not valid:
                raise StageError(f'{o.name}: unsupported modifiers are not allowed.')
            modifier = o.modifiers[0]
            row['modifiers'] = [{'name': modifier.name, 'type': modifier.type,
                'object': modifier.object.get('mme_id'),
                'useVertexGroups': modifier.use_vertex_groups,
                'useBoneEnvelopes': modifier.use_bone_envelopes,
                'usePreserveVolume': modifier.use_deform_preserve_volume}]
        if o.vertex_groups:
            if o.type != 'MESH' or not o.get('mme_enveloped'):
                raise StageError(f'{o.name}: vertex groups are only supported on imported envelope meshes.')
            weights = {group.index: [] for group in o.vertex_groups}
            for vertex in o.data.vertices:
                for assignment in vertex.groups:
                    weights[assignment.group].append([vertex.index, assignment.weight])
            row['vertexGroups'] = [{'name': group.name, 'lockWeight': group.lock_weight,
                'weights': weights[group.index]} for group in o.vertex_groups]
        if o.type == 'ARMATURE':
            row['bones'] = []
            for bone in sorted((item for item in o.pose.bones
                                if not model_additions.is_pending(item)),
                               key=lambda item: item.get('mme_id', item.name)):
                record = {'props': properties(bone), 'name': bone.name,
                    'parent': bone.parent.get('mme_id', bone.parent.name) if bone.parent else None,
                    'rotationMode': bone.rotation_mode,
                    'restMatrix': [list(r) for r in bone.bone.matrix_local],
                    'inheritScale': bone.bone.inherit_scale,
                    'useConnect': bone.bone.use_connect,
                    'useDeform': bone.bone.use_deform}
                if bone.constraints:
                    valid = (bone.get('mme_role') == 'deform-jobj' and len(bone.constraints) == 1
                        and bone.constraints[0].type == 'COPY_TRANSFORMS'
                        and bone.constraints[0].target is o)
                    if not valid:
                        raise StageError(f'{o.name} / {bone.name}: unsupported pose-bone constraints are not allowed.')
                    constraint = bone.constraints[0]
                    record['constraints'] = [{'name': constraint.name, 'type': constraint.type,
                        'target': constraint.target.get('mme_id'), 'subtarget': constraint.subtarget,
                        'targetSpace': constraint.target_space, 'ownerSpace': constraint.owner_space,
                        'influence': constraint.influence, 'mute': constraint.mute}]
                if bone.get('mme_id') not in editable_transform_ids and not bone.get('mme_animated'):
                    record['matrix'] = [list(r) for r in bone.matrix_basis]
                row['bones'].append(record)
        if o.type == 'MESH' and o.get('mme_id') in editable_ids:
            # Native Join brings material slots along with geometry. The model
            # writer emits one grey material, so these slots are not protected.
            pass
        elif o.type == 'MESH' and o.get('mme_role') != 'collision':
            row['geometry'] = digest({'vertices': [list(v.co) for v in o.data.vertices],
                'edges': [list(e.vertices) for e in o.data.edges],
                'faces': [list(p.vertices) for p in o.data.polygons],
                'materials': [m.name if m else None for m in o.data.materials],
                'faceMaterials': [p.material_index for p in o.data.polygons],
                'normals': [list(n.vector) for n in o.data.corner_normals]})
        result['objects'].append(row)
    for key in result:
        result[key].sort(key=lambda r: r['props']['mme_id'])
    return result


def protected_inventory_matches(scene, editable_id, editable_transform_ids=None,
                                deletable_ids=None):
    current = inventory(scene, editable_id, editable_transform_ids)
    expected = scene.get('mme_guard')
    if digest(current) == expected:
        return True
    editable_ids = {editable_id} if isinstance(editable_id, str) else set(editable_id or ())
    allowed_deletions = editable_ids | set(deletable_ids or ())
    stored = scene.get('mme_guard_inventory')
    if stored and allowed_deletions:
        try:
            adjusted = json.loads(stored)
            current_ids = {row['props']['mme_id'] for row in current['objects']}
            deleted_ids = allowed_deletions - current_ids
            if deleted_ids:
                adjusted['objects'] = [row for row in adjusted['objects']
                                       if row['props']['mme_id'] not in deleted_ids]
                for collection in adjusted['collections']:
                    collection['objects'] = [identity for identity in collection['objects']
                                             if identity not in deleted_ids]
                if digest(current) == digest(adjusted):
                    return True
        except (TypeError, ValueError, KeyError):
            pass
    return False


def import_session(context, directory):
    scene = context.scene
    if scene.mme_session:
        raise StageError('Use a new Blender scene to import another stage.')
    directory = Path(directory).resolve()
    stage = load_session(directory)
    if (directory / 'edits').exists() and any((directory / 'edits').iterdir()):
        raise StageError('Import requires a fresh extracted session with no pending edits.')
    groups = [read(directory / entry['file']) for entry in stage['modelGroups']]
    source = read(directory / 'collision/collision.json')
    nodes, joints, world = joint_matrices(groups)
    sid = uuid.uuid4().hex
    created_objects, created_collections, created_meshes, created_data, created_armatures, created_actions, created_cameras, created_worlds = [], [], [], [], [], [], [], []
    previous_world = scene.world
    material = None
    source_materials = {}
    editable_jobjs = jobjs.stage_targets(stage)
    editable_jobj_ids = {info['id'] for info in editable_jobjs}
    deform_rests = {key: deform_rest(joint) for key, joint in joints.items()
                    if joint.get('inverseBindMatrix') is not None}

    def tag(item, role, key, group=-1):
        item['mme_role'], item['mme_session_id'], item['mme_id'] = role, sid, key
        item['mme_group_index'] = group
        item['mme_source_hash'] = stage['source']['sha256']

    def collection(name, parent, key, role='group', index=-1):
        c = bpy.data.collections.new(name)
        created_collections.append(c)
        parent.children.link(c)
        tag(c, role, key, index)
        return c

    try:
        root = collection('Melee Stage', scene.collection, 'stage', 'stage')
        models = collection('Models', root, 'models', 'models')
        collisions = collection('Collision', root, 'collisions', 'collisions')
        lights = collection('Lights', root, 'lights', 'lights')
        reference = collection('Reference', root, 'reference', 'reference')
        gameplay.create(root, stage, tag, created_objects, created_meshes,
                        collection)
        camera.create(reference, stage, tag, created_objects, created_cameras, scene)
        atmosphere.create(stage, scene, created_worlds)
        light_objects = lighting.create(lights, stage, tag, created_objects, created_data, collection)
        material = bpy.data.materials.new('Melee Preview Grey')
        material.diffuse_color = (0.45, 0.45, 0.45, 1)
        source_materials = surface.create_materials(stage, directory, light_objects)
        scene['mme_model_materials'] = json.dumps(stage.get('modelMaterials', []))
        objects = {}
        bone_names = {}
        deform_names = {}

        def joint_chain(key):
            chain = []
            while key in joints:
                chain.append(key)
                key = nodes[key]['ownerId']
            return chain

        animation_end = 0
        for entry, group in zip(stage['modelGroups'], groups):
            c = collection(f"Group {group['index']:03d}", models, group['id'], index=group['index'])
            group_joints = [node for node in group['nodes'] if node['id'] in joints]
            armature_obj = None
            if group_joints:
                armature = bpy.data.armatures.new(f"Group {group['index']:03d} JOBJ Armature")
                created_armatures.append(armature)
                armature_obj = bpy.data.objects.new(armature.name, armature)
                created_objects.append(armature_obj)
                c.objects.link(armature_obj)
                tag(armature_obj, 'jobj-armature', group['id'] + ':armature', group['index'])
                armature_obj['mme_group_id'] = group['id']
                armature_obj.show_in_front = True
                armature.display_type = 'OCTAHEDRAL'
                bpy.ops.object.select_all(action='DESELECT')
                armature_obj.select_set(True)
                context.view_layer.objects.active = armature_obj
                bpy.ops.object.mode_set(mode='EDIT')
                edit_bones = {}
                for node in group_joints:
                    name = f"JOBJ {node['index']:03d}"
                    edit_bone = armature.edit_bones.new(name)
                    edit_bone.head = (0, 0, 0)
                    edit_bone.tail = (0, 0.25, 0)
                    edit_bone.use_deform = False
                    edit_bones[node['id']] = edit_bone
                    bone_names[node['id']] = name
                for node in group_joints:
                    if node['id'] not in deform_rests:
                        continue
                    name = f"JOBJ {node['index']:03d} Deform"
                    edit_bone = armature.edit_bones.new(name)
                    edit_bone.head = (0, 0, 0)
                    edit_bone.tail = (0, 0.25, 0)
                    edit_bone.matrix = AXES @ deform_rests[node['id']] @ AXES.inverted()
                    edit_bone.length = 0.25
                    edit_bone.use_deform = True
                    deform_names[node['id']] = name
                for node in group_joints:
                    if node['ownerId'] in edit_bones:
                        edit_bones[node['id']].parent = edit_bones[node['ownerId']]
                        edit_bones[node['id']].use_connect = False
                bpy.ops.object.mode_set(mode='POSE')
                for node in group_joints:
                    pose_bone = armature_obj.pose.bones[bone_names[node['id']]]
                    source_joint = joints[node['id']]
                    for item in (pose_bone, pose_bone.bone):
                        tag(item, node['kind'], node['id'], group['index'])
                        item['mme_source_index'] = node['index']
                        item['mme_owner_id'] = node['ownerId'] or ''
                        item['mme_instance_target'] = node['instanceTargetId'] or ''
                        item['mme_editable'] = node['id'] in editable_jobj_ids
                        if source_joint.get('readOnlyReason'):
                            item['mme_read_only_reason'] = source_joint['readOnlyReason']
                    pose_bone.rotation_mode = 'XYZ'
                    pose_bone.bone.inherit_scale = ('FULL' if source_joint['flags'] & 8 else 'ALIGNED')
                    pose_bone.matrix_basis = AXES @ joint_srt(source_joint) @ AXES.inverted()
                for node in group_joints:
                    if node['id'] not in deform_names:
                        continue
                    pose_bone = armature_obj.pose.bones[deform_names[node['id']]]
                    for item in (pose_bone, pose_bone.bone):
                        tag(item, 'deform-jobj', node['id'] + ':deform', group['index'])
                        item['mme_source_jobj_id'] = node['id']
                        item['mme_source_index'] = node['index']
                    constraint = pose_bone.constraints.new('COPY_TRANSFORMS')
                    constraint.name = 'Melee JOBJ Deform Binding'
                    constraint.target = armature_obj
                    constraint.subtarget = bone_names[node['id']]
                    constraint.target_space = 'POSE'
                    constraint.owner_space = 'POSE'
                bpy.ops.object.mode_set(mode='OBJECT')
                animation_end = max(animation_end, animations.create(armature_obj, group,
                    entry['file'], stage['source']['sha256'], sid, created_actions))
            for node in group['nodes']:
                if node['kind'] in ('group', 'sentinel-group') or node['id'] in joints:
                    continue
                obj = bpy.data.objects.new(f"{node['kind'].upper()} {node['index']:03d}", None)
                created_objects.append(obj)
                c.objects.link(obj)
                tag(obj, node['kind'], node['id'], group['index'])
                obj['mme_source_index'] = node['index']
                obj['mme_owner_id'] = node['ownerId'] or ''
                obj['mme_instance_target'] = node['instanceTargetId'] or ''
                obj.empty_display_size = 0.25
                obj.hide_render = True
                objects[node['id']] = obj
            for node in group['nodes']:
                obj = objects.get(node['id'])
                if obj:
                    if node['ownerId'] in joints:
                        obj.parent = armature_obj
                        obj.parent_type = 'BONE'
                        obj.parent_bone = bone_names[node['ownerId']]
                        obj.matrix_parent_inverse = Matrix.Translation((0, -0.25, 0))
                        obj['mme_jobj_chain'] = json.dumps(joint_chain(node['ownerId']))
                    else:
                        obj.parent = objects.get(node['ownerId'])
                        owner = objects.get(node['ownerId'])
                        if owner and owner.get('mme_jobj_chain'):
                            obj['mme_jobj_chain'] = owner['mme_jobj_chain']
            for filename in group['meshes']:
                payload = read((directory / entry['file']).parent / filename)
                assignments = None
                imported_normals = None
                if payload.get('envelopes'):
                    positions, assignments, imported_normals = mesh_binding(
                        payload, nodes, joints, world, deform_rests)
                else:
                    positions, _ = mesh_pose(payload, nodes, joints, world)
                    if payload.get('normals'):
                        from .transforms import vector
                        imported_normals = [vector(normal) for normal in payload['normals']]
                mesh = bpy.data.meshes.new(f"Group {group['index']:03d} Mesh")
                created_meshes.append(mesh)
                indices, reversed_faces = surface.display_triangle_indices(
                    payload, positions, imported_normals)
                mesh.from_pydata([AXES @ p for p in positions], [],
                                 [indices[i:i+3] for i in range(0, len(indices), 3)])
                mesh.materials.append(source_materials.get(payload['id'], material))
                surface.import_uvs(mesh, payload)
                color_layers = surface.import_colors(mesh, payload)
                if color_layers:
                    if payload['id'] not in source_materials:
                        preview_material = material.copy()
                        preview_material.name = f"Vertex Colors - {mesh.name}"
                        source_materials[payload['id']] = preview_material
                        mesh.materials[0] = preview_material
                    surface.configure_color_preview(mesh.materials[0], color_layers[0])
                if imported_normals:
                    source_normals = mesh.attributes.new(
                        name='Stage Normal', type='FLOAT_VECTOR', domain='POINT')
                    source_normal_valid = mesh.attributes.new(
                        name='Stage Normal Valid', type='FLOAT', domain='POINT')
                    for item, normal in zip(source_normals.data, imported_normals):
                        item.vector = AXES.to_3x3() @ normal
                    for item in source_normal_valid.data:
                        item.value = 1
                    surface.enable_source_normals(mesh.materials[0])
                    mesh.normals_split_custom_set_from_vertices(
                        [AXES.to_3x3() @ normal for normal in imported_normals])
                    for polygon in mesh.polygons:
                        polygon.use_smooth = True
                    mesh['mme_reversed_source_faces'] = [int(value) for value in reversed_faces]
                obj = objects[payload['id']]
                # Blender object types cannot change from Empty to Mesh: replace the placeholder.
                replacement = bpy.data.objects.new(obj.name, mesh)
                created_objects.append(replacement)
                c.objects.link(replacement)
                for key in obj.keys():
                    replacement[key] = obj[key]
                replacement.parent = obj.parent
                replacement.parent_type = obj.parent_type
                replacement.parent_bone = obj.parent_bone
                replacement.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
                replacement.matrix_basis = obj.matrix_basis.copy()
                if assignments is not None:
                    # Skinned source positions must enter the Armature modifier
                    # from a fixed bind-space object transform. Bone-parenting
                    # the mesh would apply the animated owner once before the
                    # modifier and then apply the skin matrices a second time.
                    owner = nodes[payload['ownerId']]['ownerId']
                    replacement.parent = armature_obj
                    replacement.parent_type = 'OBJECT'
                    replacement.parent_bone = ''
                    replacement.matrix_parent_inverse = Matrix.Identity(4)
                    replacement.matrix_basis = AXES @ world[owner] @ AXES.inverted()
                    replacement['mme_enveloped'] = True
                    groups_by_bone = {}
                    for vertex_index, weights in enumerate(assignments):
                        for bone, weight in weights:
                            vertex_group = groups_by_bone.get(bone)
                            if vertex_group is None:
                                vertex_group = replacement.vertex_groups.new(name=deform_names[bone])
                                groups_by_bone[bone] = vertex_group
                            vertex_group.add([vertex_index], weight, 'REPLACE')
                    modifier = replacement.modifiers.new('Melee JOBJ Envelope', 'ARMATURE')
                    modifier.object = armature_obj
                    modifier.use_vertex_groups = True
                    modifier.use_bone_envelopes = False
                if payload.get('readOnlyReason'):
                    replacement['mme_read_only_reason'] = payload['readOnlyReason']
                    replacement.name = f"Read-only Model - Group {group['index']:03d} POBJ {payload['pobjIndex']:03d}"
                objects[payload['id']] = replacement
                created_objects.remove(obj)
                bpy.data.objects.remove(obj, do_unlink=True)
        obj = collision.create(collisions, source, tag)
        created_objects.append(obj)
        created_meshes.append(obj.data)
        scene.mme_session = str(directory)
        scene.mme_session_id = sid
        if animation_end:
            scene.frame_start = 1
            scene.frame_end = animation_end
            scene.frame_set(1)
        animations.apply(scene)
        context.view_layer.update()
        editable_models = modeling.stage_targets(stage)
        scene['mme_editable_meshes'] = json.dumps(editable_models)
        scene['mme_editable_jobjs'] = json.dumps(editable_jobjs)
        jobj_baselines = {}
        for info in editable_jobjs:
            _, target = jobjs.target_bone(scene, info)
            jobj_baselines[info['id']] = jobjs.matrix_values(target.matrix_basis)
        scene['mme_jobj_baselines'] = json.dumps(jobj_baselines)
        baselines = {}
        for info in editable_models:
            target = modeling.target_object(scene, info)
            target.name = f"Editable Model - Group {info['groupIndex']:03d} JOBJ {info['jobjIndex']:03d} DOBJ {info['dobjIndex']:03d} POBJ {info['pobjIndex']:03d}"
            if not modeling.allows(info, 'topologyReplacement'):
                target.name = target.name.replace('Editable Model', 'Vertex Editable Model', 1)
            baselines[info['id']] = modeling.fingerprint(target)
        scene['mme_model_baselines'] = json.dumps(baselines)
        scene['mme_color_baselines'] = json.dumps({info['id']: modeling.color_fingerprint(modeling.target_object(scene, info)) for info in editable_models})
        scene['mme_appearance_baselines'] = json.dumps({info['id']: surface.fingerprint(
            modeling.target_object(scene, info), modeling.appearance_locked(info))
            for info in editable_models})
        editable_transforms = jobjs.target_ids(scene) | gameplay.editable_transform_ids(scene)
        guard = inventory(scene, modeling.target_ids(scene), editable_transforms)
        scene['mme_guard_inventory'] = json.dumps(guard)
        scene['mme_guard'] = digest(guard)
        scene['mme_collision_baseline'] = digest(collision.serialize(obj, source))
        scene['mme_collision_fingerprint'] = collision.fingerprint(obj)
        scene['mme_stage_info'] = json.dumps({'filename': stage['source']['filename'],
            'groups': len(groups), 'lines': len(source['lines']), 'editable': stage['capabilities']['collisionEdit'],
            'deferred': len(stage.get('deferredMeshes', []))})
        # Melee's display colors should not pass through AgX's cinematic tone mapping.
        scene.view_settings.view_transform = 'Standard'
        scene.view_settings.look = 'None'
        scene.view_settings.exposure = 0
        scene.view_settings.gamma = 1
        scene.mme_status = f"Imported {stage['source']['filename']}: {len(groups)} groups, {len(source['lines'])} collision lines"
        return obj
    except Exception:
        scene.mme_session = ''
        scene.mme_session_id = ''
        scene.world = previous_world
        for obj in reversed(created_objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for c in reversed(created_collections):
            bpy.data.collections.remove(c)
        for mesh in created_meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for data in created_data:
            if data.users == 0:
                bpy.data.lights.remove(data)
        for data in created_cameras:
            if data.users == 0:
                bpy.data.cameras.remove(data)
        for world in created_worlds:
            if world.users == 0:
                bpy.data.worlds.remove(world)
        for armature in created_armatures:
            if armature.users == 0:
                bpy.data.armatures.remove(armature)
        for action in created_actions:
            bpy.data.actions.remove(action)
        for source_material in source_materials.values():
            if source_material.users == 0:
                bpy.data.materials.remove(source_material)
        if material and material.users == 0:
            bpy.data.materials.remove(material)
        raise


def prepare(scene):
    directory = session(scene)
    stage = load_session(directory)
    editable_transforms = jobjs.target_ids(scene) | gameplay.editable_transform_ids(scene)
    if not protected_inventory_matches(scene, modeling.target_ids(scene),
                                       editable_transforms,
                                       gameplay.deletable_ids(scene, stage)):
        raise StageError('Protected model geometry, hierarchy, identities, or object transforms changed. Undo those changes before export.')
    source = read(directory / 'collision/collision.json')
    obj = collision_object(scene)
    edits = collision.serialize(obj, source)
    dirty = digest(edits) != scene.get('mme_collision_baseline')
    obj['mme_dirty'] = dirty
    if dirty and not stage['capabilities']['collisionEdit']:
        raise StageError('This stage has read-only collision (dynamic attachments or source warnings). Undo collision changes.')
    modeling.edits(scene, stage)
    from . import material_properties
    material_properties.edits(scene, stage)
    lighting.edits(scene, stage)
    gameplay.edits(scene, stage)
    groups = [read(directory / entry['file']) for entry in stage['modelGroups']]
    jobjs.edits(scene, stage, groups)
    animations.edits(scene, stage, groups)
    model_additions.edits(scene, stage)
    return directory, edits if dirty else None


def apply(scene, cli, dotnet, output):
    directory, edits = prepare(scene)
    stage = load_session(directory)
    model_edits = modeling.edits(scene, stage)
    from . import material_properties
    material_edits = material_properties.edits(scene, stage)
    light_edits = lighting.edits(scene, stage)
    gameplay_edits = gameplay.edits(scene, stage)
    groups = [read(directory / entry['file']) for entry in stage['modelGroups']]
    jobj_edits = jobjs.edits(scene, stage, groups)
    animation_edits = animations.edits(scene, stage, groups)
    addition_edits, addition_assets = model_additions.edits(scene, stage)
    payloads = {}
    if light_edits is not None:
        payloads[directory / 'edits/lights.json'] = light_edits
    if gameplay_edits is not None:
        payloads[directory / 'edits/gameplay.json'] = gameplay_edits
    if jobj_edits is not None:
        payloads[directory / 'edits/jobjs.json'] = jobj_edits
    if animation_edits is not None:
        payloads[directory / 'edits/animations.json'] = animation_edits
    if material_edits is not None:
        payloads[directory / 'edits/materials.json'] = material_edits
    if edits is not None:
        payloads[directory / 'edits/collision.json'] = edits
    if model_edits is not None:
        payloads[directory / 'edits/models.json'] = model_edits
    if addition_edits is not None:
        payloads[directory / 'edits/additions.json'] = addition_edits
        payloads.update({directory / relative: value for relative, value in addition_assets.items()})
    # Edits are temporary process input; the .blend is the authoritative edited scene.
    if (directory / 'edits').exists() and any((directory / 'edits').iterdir()):
        raise StageError('Session has external pending edits. Use a fresh import to avoid mixing edits.')
    try:
        for target, payload in payloads.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, bytes):
                target.write_bytes(payload)
            else:
                target.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
        return run(cli, dotnet, 'apply', directory, '--output', output)
    finally:
        for target in payloads:
            target.unlink(missing_ok=True)
        asset_directory = directory / 'edits/addition-assets'
        if asset_directory.is_dir() and not any(asset_directory.iterdir()):
            asset_directory.rmdir()


def validate(scene, cli, dotnet):
    with tempfile.TemporaryDirectory(prefix='meleemap-validate-') as tmp:
        apply(scene, cli, dotnet, Path(tmp) / 'validated.dat')
