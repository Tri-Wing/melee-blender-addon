"""Scene import, protected scene inventory, and transactional collision export."""
import json
from pathlib import Path
import tempfile
import uuid
import bpy
from . import collision
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


def inventory(scene):
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
        if o.type == 'MESH' and o.get('mme_role') != 'collision':
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
                mesh.materials.append(material)
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
                objects[payload['id']] = replacement
                created_objects.remove(obj)
                bpy.data.objects.remove(obj, do_unlink=True)
        obj = collision.create(collisions, source, tag)
        created_objects.append(obj)
        created_meshes.append(obj.data)
        scene.mme_session = str(directory)
        scene.mme_session_id = sid
        context.view_layer.update()
        scene['mme_guard'] = digest(inventory(scene))
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
        if material and material.users == 0:
            bpy.data.materials.remove(material)
        raise


def prepare(scene):
    directory = session(scene)
    stage = load_session(directory)
    if digest(inventory(scene)) != scene.get('mme_guard'):
        raise StageError('Protected model geometry, hierarchy, identities, or object transforms changed. Undo those changes before export.')
    source = read(directory / 'collision/collision.json')
    obj = collision_object(scene)
    edits = collision.serialize(obj, source)
    dirty = digest(edits) != scene.get('mme_collision_baseline')
    obj['mme_dirty'] = dirty
    if dirty and not stage['capabilities']['collisionEdit']:
        raise StageError('This stage has read-only collision (dynamic attachments or source warnings). Undo collision changes.')
    return directory, edits if dirty else None


def apply(scene, cli, dotnet, output):
    directory, edits = prepare(scene)
    target = directory / 'edits/collision.json'
    # Edits are temporary process input; the .blend is the authoritative edited scene.
    if (directory / 'edits').exists() and any((directory / 'edits').iterdir()):
        raise StageError('Session has external pending edits. Use a fresh import to avoid mixing edits.')
    try:
        if edits is not None:
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(edits, allow_nan=False), encoding='utf-8')
        return run(cli, dotnet, 'apply', directory, '--output', output)
    finally:
        if edits is not None:
            target.unlink(missing_ok=True)


def validate(scene, cli, dotnet):
    with tempfile.TemporaryDirectory(prefix='meleemap-validate-') as tmp:
        apply(scene, cli, dotnet, Path(tmp) / 'validated.dat')
