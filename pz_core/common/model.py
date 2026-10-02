"""Shared model types and coordinate helpers used by the game parsers."""

import math


class SGDMesh:
    def __init__(self, name="mesh"):
        self.name = name
        self.material_index = 0
        self.bone_index = 0
        self.positions = []
        self.normals = []
        self.uvs = []
        self.colors = []
        self.indices = []
        self.joints = []
        self.weights = []


class SGDBone:
    def __init__(self, index, parent=-1):
        self.index = index
        self.parent = parent
        self.matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        self.rot = [0.0, 0.0, 0.0, 0.0]
        self.trans = [0.0, 0.0, 0.0]


class SGDMaterial:
    def __init__(self, index, name="material"):
        self.index = index
        self.name = name
        self.ambient = [0.2, 0.2, 0.2, 1.0]
        self.diffuse = [0.8, 0.8, 0.8, 1.0]
        self.specular = [0.0, 0.0, 0.0, 1.0]
        self.emission = [0.0, 0.0, 0.0, 1.0]
        self.texture_index = -1
        self.tex0_low = 0
        self.tbp0 = 0


class SGDModel:
    def __init__(self, name="model"):
        self.name = name
        self.uvs_are_flipped = False
        self.materials = []
        self.bones = []
        self.meshes = []
        self.bounding_boxes = []


def transform_pos(position, matrix):
    if not matrix or len(matrix) < 16:
        return position
    x, y, z = position
    return [
        x * matrix[0] + y * matrix[4] + z * matrix[8] + matrix[12],
        x * matrix[1] + y * matrix[5] + z * matrix[9] + matrix[13],
        x * matrix[2] + y * matrix[6] + z * matrix[10] + matrix[14],
    ]


def transform_norm(normal, matrix):
    if not matrix or len(matrix) < 16:
        return normal
    x, y, z = normal
    transformed = [
        x * matrix[0] + y * matrix[4] + z * matrix[8],
        x * matrix[1] + y * matrix[5] + z * matrix[9],
        x * matrix[2] + y * matrix[6] + z * matrix[10],
    ]
    length = math.sqrt(sum(component * component for component in transformed))
    if length > 1e-6:
        return [component / length for component in transformed]
    return transformed


def strip_matrix_scale(matrix):
    """Normalise a bone matrix's basis rows while preserving translation."""
    if not matrix or len(matrix) < 16:
        return matrix
    out = list(matrix)
    for base in (0, 4, 8):
        length = math.sqrt(out[base] ** 2 + out[base + 1] ** 2 + out[base + 2] ** 2)
        if length > 1e-6:
            out[base] /= length
            out[base + 1] /= length
            out[base + 2] /= length
    return out


def face_along_positive_z(model):
    """Rotate FF1 geometry and its rig 180 degrees around the Y axis."""
    for mesh in getattr(model, "meshes", []):
        mesh.positions = [[-p[0], p[1], -p[2]] for p in mesh.positions]
        if getattr(mesh, "normals", None):
            mesh.normals = [[-n[0], n[1], -n[2]] for n in mesh.normals]
    for bone in getattr(model, "bones", []):
        matrix = getattr(bone, "matrix", None)
        if matrix is not None and len(matrix) >= 16:
            matrix = list(matrix)
            matrix[0] = -matrix[0]
            matrix[1] = -matrix[1]
            matrix[8] = -matrix[8]
            matrix[9] = -matrix[9]
            matrix[12] = -matrix[12]
            matrix[14] = -matrix[14]
            bone.matrix = matrix
        trans = getattr(bone, "trans", None)
        if trans and len(trans) >= 3:
            bone.trans = [-trans[0], trans[1], -trans[2]]
    return model
