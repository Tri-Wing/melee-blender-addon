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


def inverse_bind(joints, key):
    values = joints[key]['inverseBindMatrix']
    if values is None:
        raise StageError('Envelope preview requires an inverse-bind matrix.')
    return Matrix([values[i:i+4] for i in range(0, 12, 4)] + [[0, 0, 0, 1]])


def deform_rest(joint):
    """Return the rigid Blender-compatible rest matrix for an HSD inverse bind."""
    values = joint['inverseBindMatrix']
    if values is None:
        raise StageError('A deform bone requires an inverse-bind matrix.')
    bind = Matrix([values[i:i+4] for i in range(0, 12, 4)] + [[0, 0, 0, 1]])
    rest = bind.inverted()
    location, rotation, _ = rest.decompose()
    return Matrix.LocRotScale(location, rotation, None)


def envelope_context(payload, nodes, joints, world):
    owner = nodes[payload['ownerId']]['ownerId']
    owner_world = world[owner]
    right = None
    if not joints[owner]['flags'] & 2:
        skeleton = owner
        while skeleton in joints and not joints[skeleton]['flags'] & 3:
            skeleton = nodes[skeleton]['ownerId']
        if skeleton not in joints:
            raise StageError('Envelope owner has no skeleton ancestor.')
        if skeleton == owner:
            right = inverse_bind(joints, skeleton).inverted()
        elif joints[skeleton]['flags'] & 2:
            right = world[skeleton].inverted() @ owner_world
        else:
            right = (world[skeleton] @ inverse_bind(joints, skeleton)).inverted() @ owner_world
    return owner_world, right


def mesh_binding(payload, nodes, joints, world, rests):
    """Prepare an enveloped POBJ for Blender vertex groups and an armature modifier."""
    envelopes = payload.get('envelopes')
    if not envelopes:
        raise StageError('Mesh binding requires HSD envelope data.')
    owner_world, right = envelope_context(payload, nodes, joints, world)
    has_right = right is not None
    right = right or Matrix.Identity(4)
    owner_inverse = owner_world.inverted()
    positions, assignments = [], []
    normals = [] if payload.get('normals') else None
    epsilon = 1.1920929e-7
    for vertex_index, (position, envelope_index) in enumerate(zip(payload['positions'], payload['envelopeIndices'])):
        envelope = envelopes[envelope_index]
        binding = right
        if envelope[0]['weight'] >= 1 - epsilon:
            bone = envelope[0]['jobjId']
            # HSD omits inverse bind multiplication for root-space, single-bone
            # vertices. Fold that exception into the stored vertex position.
            binding = (rests[bone] if not has_right else
                       rests[bone] @ inverse_bind(joints, bone) @ right)
            weights = [(bone, 1.0)]
        else:
            weights = [(item['jobjId'], item['weight']) for item in envelope
                       if item['weight'] > epsilon]
            if not weights:
                raise StageError('Envelope vertex has no positive joint weight.')
            for bone, _ in weights:
                residual = rests[bone] @ inverse_bind(joints, bone)
                error = max(abs(residual[i][j] - (1 if i == j else 0))
                            for i in range(4) for j in range(4))
                if error > 5e-4:
                    raise StageError(f'Blended envelope uses a scaled or sheared inverse bind that Blender cannot represent ({error:.6g}).')
        transform = owner_inverse @ binding
        positions.append(transform @ vector(position))
        if normals is not None:
            normal = transform.to_3x3().inverted_safe().transposed() @ vector(payload['normals'][vertex_index])
            if normal.length_squared:
                normal.normalize()
            normals.append(normal)
        assignments.append(weights)
    return positions, assignments, normals


def mesh_pose(payload, nodes, joints, world):
    owner = nodes[payload['ownerId']]['ownerId']
    owner_world = world[owner]
    envelopes = payload.get('envelopes')
    if not envelopes:
        if payload.get('boundJobjId'):
            raise StageError('Shared-joint mesh preview is not supported yet.')
        return [vector(p) for p in payload['positions']], owner_world

    owner_world, right = envelope_context(payload, nodes, joints, world)
    matrices = []
    for envelope in envelopes:
        if envelope[0]['weight'] >= 1 - 1.1920929e-7:
            bone = envelope[0]['jobjId']
            matrix = world[bone] @ inverse_bind(joints, bone) if right is not None else world[bone]
        else:
            matrix = Matrix(((0,)*4,)*4)
            for weight in envelope:
                matrix += (world[weight['jobjId']] @ inverse_bind(joints, weight['jobjId'])) * weight['weight']
        matrices.append(matrix @ right if right is not None else matrix)
    # Bake the static deformation into the owner's local space; retain hierarchy.
    inverse = owner_world.inverted()
    return [inverse @ matrices[index] @ vector(position)
            for position, index in zip(payload['positions'], payload['envelopeIndices'])], owner_world
