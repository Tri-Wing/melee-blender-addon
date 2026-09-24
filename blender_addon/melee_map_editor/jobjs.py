"""Blender armature adapter for supported static HSD JOBJ transforms."""
import json
from mathutils import Vector
from .protocol import SESSION_PROTOCOL, StageError
from .transforms import AXES, vector


def stage_targets(stage):
    return stage.get('editableJobjs', [])


def targets(scene):
    return json.loads(scene.get('mme_editable_jobjs', '[]'))


def target_ids(scene):
    return {info['id'] for info in targets(scene)}


def armatures(scene):
    return [obj for obj in scene.objects if obj.type == 'ARMATURE'
            and obj.get('mme_session_id') == scene.mme_session_id
            and obj.get('mme_role') == 'jobj-armature']


def target_bone(scene, info):
    matches = [(obj, bone) for obj in armatures(scene) for bone in obj.pose.bones
               if bone.get('mme_id') == info['id']]
    if len(matches) != 1:
        raise StageError('An editable JOBJ bone is missing or duplicated. Undo the change.')
    return matches[0]


def bone_by_id(scene, key):
    matches = [(obj, bone) for obj in armatures(scene) for bone in obj.pose.bones
               if bone.get('mme_id') == key]
    return matches[0] if len(matches) == 1 else (None, None)


def selected_info(context):
    allowed = {info['id']: info for info in targets(context.scene)}
    bone = context.active_pose_bone
    if bone and context.active_object in armatures(context.scene) and bone.get('mme_id') in allowed:
        return allowed[bone['mme_id']]
    if bone and context.active_object in armatures(context.scene) \
            and bone.get('mme_source_jobj_id') in allowed:
        return allowed[bone['mme_source_jobj_id']]
    obj = context.active_object
    if obj and obj.get('mme_session_id') == context.scene.mme_session_id:
        for key in json.loads(obj.get('mme_jobj_chain', '[]')):
            if key in allowed:
                return allowed[key]
    return None


def matrix_values(matrix):
    return [[value for value in row] for row in matrix]


def changed(matrix, baseline):
    return any(abs(matrix[i][j] - baseline[i][j]) > 1e-6
               for i in range(4) for j in range(4))


def edits(scene, stage, groups):
    infos = targets(scene)
    if infos != stage_targets(stage):
        raise StageError('Editable JOBJ identities changed. Re-import the stage.')
    baselines = json.loads(scene.get('mme_jobj_baselines', '{}'))
    nodes = {node['id']: node for group in groups for node in group['nodes']}
    joints = {joint['id']: joint for group in groups for joint in group['joints']}
    children = {key: [] for key in joints}
    for key, node in nodes.items():
        if key in joints and node['ownerId'] in children:
            children[node['ownerId']].append(key)

    result = []
    axes_inverse = AXES.inverted()
    for info in infos:
        armature, bone = target_bone(scene, info)
        baseline = baselines.get(info['id'])
        if baseline is None:
            raise StageError('JOBJ transform baseline is missing. Re-import the stage.')
        dirty = changed(bone.matrix_basis, baseline)
        bone['mme_dirty'] = dirty
        if not dirty:
            continue
        if bone.rotation_mode != 'XYZ':
            raise StageError(f'{armature.name} / {bone.name}: use XYZ Euler rotation mode for JOBJ transforms.')
        joint = joints[info['id']]
        local = axes_inverse @ bone.matrix_basis @ AXES
        if any(abs(local[3][i] - (1 if i == 3 else 0)) > 1e-5 for i in range(4)):
            raise StageError(f'{bone.name}: transform is not affine.')
        # The armature uses Blender's aligned scale inheritance to reproduce
        # HSD's parent-scale compensation.  matrix_basis therefore remains the
        # source JOBJ's ordinary local SRT matrix and can be decomposed directly.
        srt = local.to_3x3()
        original_scale = vector(joint['scale'])
        scales = Vector((srt.col[0].length, srt.col[1].length, srt.col[2].length))
        for axis in range(3):
            if original_scale[axis] < 0:
                scales[axis] *= -1
            if abs(scales[axis]) < 1e-6:
                raise StageError(f'{bone.name}: scale cannot be zero.')
        rotation = srt.copy()
        for column in range(3):
            rotation.col[column] /= scales[column]
        orthogonal = max(abs(rotation.col[a].dot(rotation.col[b]))
                         for a, b in ((0, 1), (0, 2), (1, 2))) < 1e-4
        unit = all(abs(rotation.col[i].length - 1) < 1e-4 for i in range(3))
        if not orthogonal or not unit or abs(rotation.determinant() - 1) > 1e-4:
            raise StageError(f'{bone.name}: shear or a changed negative-scale axis cannot be represented by a Melee JOBJ.')
        if children[info['id']]:
            ratios = [scales[i] / original_scale[i] for i in range(3)]
            if max(ratios) - min(ratios) > 1e-4:
                raise StageError(f'{bone.name}: a JOBJ with child joints can only be scaled uniformly in this version.')
        euler = rotation.to_euler('XYZ')
        result.append({'id': info['id'],
            'rotation': dict(zip('xyz', euler)),
            'scale': dict(zip('xyz', scales)),
            'translation': dict(zip('xyz', local.translation))})
    return {'protocolVersion': SESSION_PROTOCOL, 'coordinateSpace': 'game-jobj-local', 'jobjs': result} if result else None


def update_dirty(scene, depsgraph):
    baselines = json.loads(scene.get('mme_jobj_baselines', '{}'))
    updates = {update.id.original for update in depsgraph.updates}
    if not any(obj in updates or obj.data in updates for obj in armatures(scene)):
        return
    for info in targets(scene):
        _, bone = target_bone(scene, info)
        dirty = changed(bone.matrix_basis, baselines.get(info['id'], bone.matrix_basis))
        if bool(bone.get('mme_dirty')) != dirty:
            bone['mme_dirty'] = dirty
