"""Live user-facing descriptions derived from stable stage metadata."""
import json


ROLE_LABELS = {
    'stage': 'Stage',
    'models': 'Models',
    'group': 'Model Group',
    'jobj-armature': 'JOBJ Armature',
    'jobj': 'JOBJ',
    'root-jobj': 'Root JOBJ',
    'deform-jobj': 'Deform JOBJ',
    'dobj': 'DOBJ',
    'pobj': 'Model',
    'collision': 'Collision Component',
    'collision-joint': 'Collision Joint',
    'gameplay-point': 'Gameplay Point',
    'gameplay-bounds': 'Gameplay Bounds',
    'light': 'Stage Light',
    'light-set': 'Light Set',
    'preview-camera': 'Preview Camera',
    'model-addition': 'Added Model',
    'model-addition-dobj': 'Added DOBJ',
    'model-addition-jobj': 'Added JOBJ',
}


def selected(context):
    bone = getattr(context, 'active_pose_bone', None)
    if bone and context.active_object \
            and context.active_object.get('mme_session_id') == context.scene.mme_session_id:
        return bone
    obj = context.active_object
    return obj if obj and obj.get('mme_session_id') == context.scene.mme_session_id else None


def label(item):
    role = item.get('mme_role', '')
    if role == 'gameplay-point':
        kind = item.get('mme_gameplay_kind')
        player = item.get('mme_gameplay_player')
        if kind == 'player-spawn':
            return f'Player {player} Spawn'
        if kind == 'player-respawn':
            return f'Player {player} Respawn'
        if kind == 'item-spawn':
            return 'Item Spawn'
    if role == 'gameplay-bounds':
        return ('Camera Bounds' if item.get('mme_gameplay_kind') == 'camera-boundary'
                else 'Blast Zone')
    if role == 'light':
        kind = str(item.get('mme_light_type', 'stage')).replace('-', ' ').title()
        return f'{kind} Light'
    return ROLE_LABELS.get(role, role.replace('-', ' ').title() or 'Stage Object')


def _items(scene):
    result = [obj for obj in scene.objects
              if obj.get('mme_session_id') == scene.mme_session_id]
    result.extend(bone for obj in scene.objects if obj.type == 'ARMATURE'
                  and obj.get('mme_session_id') == scene.mme_session_id
                  for bone in obj.pose.bones
                  if bone.get('mme_session_id') == scene.mme_session_id)
    return result


def _source_path(scene, item):
    by_id = {candidate.get('mme_id'): candidate for candidate in _items(scene)
             if candidate.get('mme_id')}
    result = {}
    current = item
    visited = set()
    while current is not None and current.get('mme_id') not in visited:
        identity = current.get('mme_id')
        if identity:
            visited.add(identity)
        role = current.get('mme_role', '')
        index = current.get('mme_source_index')
        if isinstance(index, int):
            if role in {'jobj', 'root-jobj', 'model-addition-jobj'}:
                result.setdefault('JOBJ', index)
            elif role in {'dobj', 'model-addition-dobj'}:
                result.setdefault('DOBJ', index)
            elif role in {'pobj', 'model-addition'}:
                result.setdefault('POBJ', index)
        current = by_id.get(current.get('mme_owner_id'))
    return result


def _collision_attachment(scene, item):
    try:
        registry = json.loads(scene.get('mme_collision_joints', '[]'))
        entry = next(value for value in registry
                     if value.get('id') == item.get('mme_collision_joint_id'))
    except (TypeError, ValueError, KeyError, StopIteration):
        return None
    attachments = entry.get('attachments') or []
    if not attachments:
        return 'No DAT attachment'
    if len(attachments) > 1:
        return f'{len(attachments)} serialized attachments'
    return ('Local serialized attachment' if attachments[0].get('jobjId')
            else 'External serialized attachment')


def describe(scene, item):
    """Return current labels without consulting or parsing Blender names."""
    rows = [('Type', label(item))]
    role = item.get('mme_role')
    group = item.get('mme_group_index')
    if role in {'group', 'jobj-armature', 'jobj', 'root-jobj', 'deform-jobj',
                'dobj', 'pobj', 'model-addition', 'model-addition-dobj',
                'model-addition-jobj'} and isinstance(group, int) and group >= 0:
        rows.append(('Source group', str(group)))
    path = _source_path(scene, item)
    if path:
        rows.append(('Source path', ' · '.join(
            f'{kind} {path[kind]}' for kind in ('JOBJ', 'DOBJ', 'POBJ')
            if kind in path)))
    if role in {'gameplay-point', 'gameplay-bounds'}:
        rows.append(('General-point set', str(group) if isinstance(group, int)
                    and group >= 0 else 'Unknown'))
        if role == 'gameplay-point' and item.get('mme_gameplay_kind') == 'item-spawn':
            type_id = item.get('mme_gameplay_type_id')
            if isinstance(type_id, int):
                rows.append(('Runtime slot', f'{type_id - 0x7E} (type 0x{type_id:02X})'))
    if role == 'collision':
        joint = item.get('mme_collision_joint')
        if isinstance(joint, int):
            rows.append(('Collision joint', str(joint)))
        attachment = _collision_attachment(scene, item)
        if attachment:
            rows.append(('Attachment', attachment))
    if role == 'light':
        rows.append(('Light set', str(item.get('mme_light_set', 'Unknown'))))
    if item.get('mme_read_only_reason'):
        rows.append(('Editing', 'Read-only'))
        rows.append(('Reason', item['mme_read_only_reason']))
    elif item.get('mme_model_edit_scope') == 'full-geometry':
        rows.append(('Editing', 'Full geometry'))
    elif item.get('mme_model_edit_scope') == 'vertex-movement':
        rows.append(('Editing', 'Vertex movement only'))
    elif role in {'gameplay-point', 'gameplay-bounds'}:
        rows.append(('Editing', 'Editable' if item.get('mme_gameplay_editable')
                     else 'Read-only'))
    elif item.get('mme_editable'):
        rows.append(('Editing', 'Editable'))
    identity = item.get('mme_id')
    if identity:
        rows.append(('Stable ID', str(identity)))
    return rows
