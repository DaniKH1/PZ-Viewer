"""Coordinate-system conversion for FF1 PS2 characters; not a T-pose solver.

Matrices are stored as column-major arrays by the model/export contract.
The existing common helper negates only four basis coefficients. That is
not a rotation and produces non-orthogonal/reflected bones. Keep this fix
local to FF1; Wii/Xbox/FF2/FF3 use their existing entry points unchanged.
"""


def face_along_positive_z(model):
    """Rotate model space 180 degrees about Y, including *all* bone bases.

    C = diag(-1, 1, -1, 1); each global bone becomes C @ M, not M @ C,
    not C @ M @ C, and not a selected subset of its rotation coefficients.
    Mesh positions/normals undergo the same change. Stored articulation is
    preserved: rotating the entire asset cannot turn an A-pose into a T-pose.
    """
    for mesh in model.meshes:
        mesh.positions = [[-p[0], p[1], -p[2]] for p in mesh.positions]
        mesh.normals = [[-n[0], n[1], -n[2]] for n in mesh.normals]
    for bone in model.bones:
        matrix = list(bone.matrix)
        if len(matrix) != 16:
            raise ValueError(f"FF1 bone {bone.index}: expected a 4x4 matrix")
        for index in (0, 2, 4, 6, 8, 10, 12, 14):
            matrix[index] = -matrix[index]
        bone.matrix = matrix
        bone.trans = matrix[12:15]
        # FF3's friendly index->name table in the shared exporter is not an
        # FF1 skeleton definition. Do not label FF1's elbow as 'hips', etc.
        if not getattr(bone, "name", None):
            bone.name = f"FF1_Bone_{bone.index:02d}"
    return model
