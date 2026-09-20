"""Static HSD pose, following jobj.c, mtx.c and displayfunc.c in the decomp.

Animation, billboarding, constraints and stage-code pose changes are not evaluated.
"""
from mathutils import Euler, Matrix, Vector
from .protocol import StageError

AXES = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def vector(value):
    return Vector(tuple(value[k] for k in ('x', 'y', 'z')))


def joint_srt(joint):
    scale = vector(joint['scale'])
    result = Euler(vector(joint['rotation']), 'XYZ').to_matrix().to_4x4() @ Matrix.Diagonal((*scale, 1))
    result.translation = vector(joint['translation'])
    return result


def joint_matrices(groups, local_matrices=None):
    nodes = {n['id']: n for g in groups for n in g['nodes']}
    joints = {j['id']: j for g in groups for j in g['joints']}
    world, scales = {}, {}

    def visit(key):
        if key in world:
            return world[key]
        joint = joints[key]
        if joint['flags'] & 0x20000:
            raise StageError('Quaternion JOBJ preview is not supported yet.')
        parent = nodes[key]['ownerId']
        parent_world = visit(parent) if parent in joints else Matrix.Identity(4)
        parent_scale = scales.get(parent)
        scale = vector(joint['scale'])
        local = joint_srt(joint)
        # HSD compensates inherited scale on both sides of the rotation.
        if parent_scale is not None:
            if any(abs(x) < 1e-12 for x in parent_scale):
                raise StageError('Cannot preview a joint with zero inherited scale.')
            local = (Matrix.Diagonal(tuple(1/x for x in parent_scale) + (1,))
                     @ local @ Matrix.Diagonal((*parent_scale, 1)))
        local.translation = vector(joint['translation'])
        scales[key] = (parent_scale if joint['flags'] & 8 else
                       Vector(tuple(scale[i] * (parent_scale[i] if parent_scale is not None else 1)
                                    for i in range(3))))
        if local_matrices is not None:
            local_matrices[key] = local
        world[key] = parent_world @ local
        return world[key]

    for key in joints:
        visit(key)
    return nodes, joints, world


def mesh_pose(payload, nodes, joints, world):
    owner = nodes[payload['ownerId']]['ownerId']
    owner_world = world[owner]
    envelopes = payload.get('envelopes')
    if not envelopes:
        if payload.get('boundJobjId'):
            raise StageError('Shared-joint mesh preview is not supported yet.')
        return [vector(p) for p in payload['positions']], owner_world

    def bind(key):
        values = joints[key]['inverseBindMatrix']
        if values is None:
            raise StageError('Envelope preview requires an inverse-bind matrix.')
        return Matrix([values[i:i+4] for i in range(0, 12, 4)] + [[0, 0, 0, 1]])

    right = None
    if not joints[owner]['flags'] & 2:
        skeleton = owner
        while skeleton in joints and not joints[skeleton]['flags'] & 3:
            skeleton = nodes[skeleton]['ownerId']
        if skeleton not in joints:
            raise StageError('Envelope owner has no skeleton ancestor.')
        if skeleton == owner:
            right = bind(skeleton).inverted()
        elif joints[skeleton]['flags'] & 2:
            right = world[skeleton].inverted() @ owner_world
        else:
            right = (world[skeleton] @ bind(skeleton)).inverted() @ owner_world
    matrices = []
    for envelope in envelopes:
        if envelope[0]['weight'] >= 1 - 1.1920929e-7:
            bone = envelope[0]['jobjId']
            matrix = world[bone] @ bind(bone) if right is not None else world[bone]
        else:
            matrix = Matrix(((0,)*4,)*4)
            for weight in envelope:
                matrix += (world[weight['jobjId']] @ bind(weight['jobjId'])) * weight['weight']
        matrices.append(matrix @ right if right is not None else matrix)
    # Bake the static deformation into the owner's local space; retain hierarchy.
    inverse = owner_world.inverted()
    return [inverse @ matrices[index] @ vector(position)
            for position, index in zip(payload['positions'], payload['envelopeIndices'])], owner_world
