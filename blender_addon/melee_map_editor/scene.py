"""Scene import, protected scene inventory, and transactional collision export."""
import json
from pathlib import Path
import tempfile
import uuid
import bpy
from . import collision, modeling, surface
from .protocol import StageError, digest, load_session, read, run
from .transforms import AXES, joint_matrices, mesh_pose


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
    return {key: item[key] for key in item.keys() if key.startswith('mme_') and key != 'mme_dirty'}


def inventory(scene, editable_id=None):
    editable_ids = {editable_id} if isinstance(editable_id, str) else set(editable_id or ())
    sid = scene.mme_session_id
    collections = [c for c in bpy.data.collections if c.get('mme_session_id') == sid]
    objects = [o for o in bpy.data.objects if o.get('mme_session_id') == sid]
    result = {'collections': [], 'objects': []}
    for c in collections:
        result['collections'].append({'props': properties(c),
            'parents': sorted(p.get('mme_id', p.name) for p in bpy.data.collections if c.name in p.children)
                       + (['SCENE'] if c.name in scene.collection.children else []),
            'objects': sorted(o.get('mme_id', o.name) for o in c.objects),
            'children': sorted(x.get('mme_id', x.name) for x in c.children)})
    for o in objects:
        if o.mode == 'EDIT':
            o.update_from_editmode()
        row = {'props': properties(o), 'parent': o.parent.get('mme_id', o.parent.name) if o.parent else None,
               'matrix': [list(r) for r in o.matrix_basis],
               'parentInverse': [list(r) for r in o.matrix_parent_inverse],
               'collections': sorted(c.get('mme_id', c.name) for c in o.users_collection),
               'type': o.type, 'inScene': o.name in scene.objects}
        if (o.modifiers or o.constraints or o.animation_data or (o.data and o.data.animation_data)
                or (o.type == 'MESH' and o.data.shape_keys)):
            raise StageError(f'{o.name}: modifiers, constraints, shape keys and animation are not supported.')
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


def protected_inventory_matches(scene, editable_id):
    current = inventory(scene, editable_id)
    expected = scene.get('mme_guard')
    if digest(current) == expected:
        return True
    # Older .blend files included the target's material slots in their guard.
    # All imported previews share one material. Try the surviving preview slot
    # lists against the OLD hash; never rebaseline hierarchy or other geometry.
    if not isinstance(editable_id, str):
        return False
    target = next((row for row in current['objects']
                   if row['props']['mme_id'] == editable_id), None)
    if target is not None:
        candidates = {tuple(m.name if m else None for m in obj.data.materials)
                      for obj in scene.objects if obj.type == 'MESH'
                      and obj.get('mme_session_id') == scene.mme_session_id
                      and obj.get('mme_role') == 'pobj'}
        for materials in candidates:
            target['editableMaterials'] = list(materials)
            if digest(current) == expected:
                return True
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
    local_matrices = {}
    nodes, joints, world = joint_matrices(groups, local_matrices)
    sid = uuid.uuid4().hex
    created_objects, created_collections, created_meshes = [], [], []
    material = None
    source_materials = {}

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
        collection('Reference', root, 'reference', 'reference')
        material = bpy.data.materials.new('Melee Preview Grey')
        material.diffuse_color = (0.45, 0.45, 0.45, 1)
        source_materials = surface.create_materials(stage, directory)
        scene['mme_model_materials'] = json.dumps(stage.get('modelMaterials', []))
        objects = {}
        for entry, group in zip(stage['modelGroups'], groups):
            c = collection(f"Group {group['index']:03d}", models, group['id'], index=group['index'])
            for node in group['nodes']:
                if node['kind'] in ('group', 'sentinel-group'):
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
                    obj.parent = objects.get(node['ownerId'])
                    if node['id'] in world:
                        local = local_matrices[node['id']]
                        # Parent inverse stores arbitrary affine transforms without TRS decomposition.
                        if obj.parent is not None:
                            obj.matrix_parent_inverse = AXES @ local @ AXES.inverted()
                        else:
                            obj.matrix_basis = AXES @ local @ AXES.inverted()
            for filename in group['meshes']:
                payload = read((directory / entry['file']).parent / filename)
                positions, _ = mesh_pose(payload, nodes, joints, world)
                mesh = bpy.data.meshes.new(f"Group {group['index']:03d} Mesh")
                created_meshes.append(mesh)
                indices = payload['triangleIndices']
                mesh.from_pydata([AXES @ p for p in positions], [],
                                 [indices[i:i+3] for i in range(0, len(indices), 3)])
                mesh.materials.append(source_materials.get(payload['id'], material))
                surface.import_uvs(mesh, payload)
                if payload.get('normals') and not payload.get('envelopes'):
                    from .transforms import vector
                    mesh.normals_split_custom_set_from_vertices([AXES.to_3x3() @ vector(n) for n in payload['normals']])
                obj = objects[payload['id']]
                # Blender object types cannot change from Empty to Mesh: replace the placeholder.
                replacement = bpy.data.objects.new(obj.name, mesh)
                created_objects.append(replacement)
                c.objects.link(replacement)
                for key in obj.keys():
                    replacement[key] = obj[key]
                replacement.parent = obj.parent
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
        context.view_layer.update()
        editable = stage.get('editableMesh')
        scene['mme_editable_mesh'] = json.dumps(editable)
        editable_models = modeling.stage_targets(stage)
        scene['mme_editable_meshes'] = json.dumps(editable_models)
        baselines = {}
        for info in editable_models:
            target = modeling.target_object(scene, info)
            target.name = f"Editable Model - Group {info['groupIndex']:03d} JOBJ {info['jobjIndex']:03d} DOBJ {info['dobjIndex']:03d} POBJ {info['pobjIndex']:03d}"
            baselines[info['id']] = modeling.fingerprint(target)
        scene['mme_model_baselines'] = json.dumps(baselines)
        scene['mme_appearance_baselines'] = json.dumps({info['id']: surface.fingerprint(modeling.target_object(scene, info)) for info in editable_models})
        # Preserve the single-target helpers for older saved scenes/scripts.
        if editable:
            scene['mme_model_baseline'] = baselines[editable['id']]
        scene['mme_guard'] = digest(inventory(scene, modeling.target_ids(scene)))
        scene['mme_collision_baseline'] = digest(collision.serialize(obj, source))
        scene['mme_collision_fingerprint'] = collision.fingerprint(obj)
        scene['mme_stage_info'] = json.dumps({'filename': stage['source']['filename'],
            'groups': len(groups), 'lines': len(source['lines']), 'editable': stage['capabilities']['collisionEdit'],
            'deferred': len(stage.get('deferredMeshes', []))})
        scene.mme_status = f"Imported {stage['source']['filename']}: {len(groups)} groups, {len(source['lines'])} collision lines"
        return obj
    except Exception:
        scene.mme_session = ''
        scene.mme_session_id = ''
        for obj in reversed(created_objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for c in reversed(created_collections):
            bpy.data.collections.remove(c)
        for mesh in created_meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for source_material in source_materials.values():
            if source_material.users == 0:
                bpy.data.materials.remove(source_material)
        if material and material.users == 0:
            bpy.data.materials.remove(material)
        raise


def prepare(scene):
    directory = session(scene)
    stage = load_session(directory)
    editable = modeling.target_info(scene)
    ids = modeling.target_ids(scene) if 'mme_editable_meshes' in scene else (editable['id'] if editable else None)
    if not protected_inventory_matches(scene, ids):
        raise StageError('Protected model geometry, hierarchy, identities, or object transforms changed. Undo those changes before export.')
    source = read(directory / 'collision/collision.json')
    obj = collision_object(scene)
    edits = collision.serialize(obj, source)
    dirty = digest(edits) != scene.get('mme_collision_baseline')
    obj['mme_dirty'] = dirty
    if dirty and not stage['capabilities']['collisionEdit']:
        raise StageError('This stage has read-only collision (dynamic attachments or source warnings). Undo collision changes.')
    modeling.edits(scene, stage)
    return directory, edits if dirty else None


def apply(scene, cli, dotnet, output):
    directory, edits = prepare(scene)
    model_edits = modeling.edits(scene, load_session(directory))
    payloads = {}
    if edits is not None:
        payloads[directory / 'edits/collision.json'] = edits
    if model_edits is not None:
        payloads[directory / 'edits/models.json'] = model_edits
    # Edits are temporary process input; the .blend is the authoritative edited scene.
    if (directory / 'edits').exists() and any((directory / 'edits').iterdir()):
        raise StageError('Session has external pending edits. Use a fresh import to avoid mixing edits.')
    try:
        for target, payload in payloads.items():
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
        return run(cli, dotnet, 'apply', directory, '--output', output)
    finally:
        for target in payloads:
            target.unlink(missing_ok=True)


def validate(scene, cli, dotnet):
    with tempfile.TemporaryDirectory(prefix='meleemap-validate-') as tmp:
        apply(scene, cli, dotnet, Path(tmp) / 'validated.dat')
