import os
import struct
from .pz_pk2 import unpack_pk2
from .pz_tim2 import decode_tim2
from .pz_sgd import parse_sgd, SGDModel
from .pz_collision import create_sphere_collider_mesh, create_bounding_box_mesh

class CharacterModel:
    def __init__(self, name="character"):
        self.name = name
        self.bones = []
        self.meshes = []
        self.materials = []
        self.textures = [] # list of PIL.Image
        self.collision_meshes = []

def parse_mdl(data_or_path, name="character"):
    if isinstance(data_or_path, (str, os.PathLike)):
        name = os.path.splitext(os.path.basename(data_or_path))[0]
        with open(data_or_path, 'rb') as f:
            data = f.read()
    else:
        data = data_or_path

    entries = unpack_pk2(data)
    if len(entries) < 2:
        return None

    mpk_data = entries[0]['data']
    pk2_tex_data = entries[1]['data']

    char_model = CharacterModel(name)

    # 1. Decode textures and build TBP map
    tex_entries = unpack_pk2(pk2_tex_data)
    tbp_to_tex_idx = {}
    for ti, te in enumerate(tex_entries):
        td = te['data']
        imgs = decode_tim2(td)
        if imgs:
            tex_idx = len(char_model.textures)
            char_model.textures.append(imgs[0]['image'])
            # Read GsTex0 TBP0 from TIM2 picture header (offset 40)
            if len(td) >= 44:
                tbp0 = struct.unpack('<I', td[40:44])[0] & 0x3FFF
                tbp_to_tex_idx[tbp0] = tex_idx

    # 2. Parse sub-SGDs from MPK
    sgd_entries = unpack_pk2(mpk_data)

    for s_idx, se in enumerate(sgd_entries):
        sub_model = parse_sgd(se['data'], name=f"{name}_part_{s_idx}")
        if not sub_model:
            continue

        # Copy bones from the first sub-model that has bones
        if not char_model.bones and sub_model.bones:
            char_model.bones = sub_model.bones

        # Copy bounding boxes as collision meshes
        for bb in sub_model.bounding_boxes:
            char_model.collision_meshes.append(create_bounding_box_mesh(bb, name=f"{name}_bbox_{s_idx}"))

        # Map materials with exact TBP matching
        mat_map = {}
        for m_idx, mat in enumerate(sub_model.materials):
            new_idx = len(char_model.materials)
            mat.index = new_idx

            if hasattr(mat, 'tbp0') and mat.tbp0 in tbp_to_tex_idx:
                mat.texture_index = tbp_to_tex_idx[mat.tbp0]
            elif char_model.textures:
                mat.texture_index = m_idx % len(char_model.textures)
            else:
                mat.texture_index = -1

            char_model.materials.append(mat)
            mat_map[m_idx] = new_idx

        # Add meshes
        for m in sub_model.meshes:
            m.name = f"{name}_{m.name}"
            m.material_index = mat_map.get(m.material_index, 0)
            char_model.meshes.append(m)

    # 3. Add character collision spheres with accurate anatomical radius (head/chest/waist)
    if char_model.bones:
        neck_id = min(13, len(char_model.bones) - 1)
        b_mat = char_model.bones[neck_id].matrix
        neck_pos = [b_mat[12], b_mat[13], b_mat[14]]
        # Head / Neck collision sphere (~1.8 unit radius)
        char_model.collision_meshes.append(create_sphere_collider_mesh(neck_pos, radius=1.8, name="col_head_sphere"))

        waist_id = min(21, len(char_model.bones) - 1)
        w_mat = char_model.bones[waist_id].matrix
        waist_pos = [w_mat[12], w_mat[13], w_mat[14]]
        # Waist / Torso collision sphere (~2.5 unit radius)
        char_model.collision_meshes.append(create_sphere_collider_mesh(waist_pos, radius=2.5, name="col_body_sphere"))

    return char_model
