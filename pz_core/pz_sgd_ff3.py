"""Project Zero 3 FF3 SGD parser and model types."""

import struct
import math
import numpy as np

class SGDMesh:
    def __init__(self, name="mesh"):
        self.name = name
        self.material_index = 0
        self.bone_index = 0
        self.positions = []   # list of [x, y, z]
        self.normals = []     # list of [nx, ny, nz]
        self.uvs = []         # list of [u, v]
        self.colors = []      # list of [r, g, b, a]
        self.indices = []     # list of [v0, v1, v2]
        self.joints = []      # list of [j0, j1, 0, 0]
        self.weights = []     # list of [w0, w1, 0, 0]

class SGDBone:
    def __init__(self, index, parent=-1):
        self.index = index
        self.parent = parent
        self.matrix = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]
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
        self.materials = []
        self.bones = []
        self.meshes = []
        self.bounding_boxes = []

def transform_pos(p, mat):
    if not mat or len(mat) < 16:
        return p
    x, y, z = p
    tx = x * mat[0] + y * mat[4] + z * mat[8] + mat[12]
    ty = x * mat[1] + y * mat[5] + z * mat[9] + mat[13]
    tz = x * mat[2] + y * mat[6] + z * mat[10] + mat[14]
    return [tx, ty, tz]

def transform_norm(n, mat):
    if not mat or len(mat) < 16:
        return n
    x, y, z = n
    tx = x * mat[0] + y * mat[4] + z * mat[8]
    ty = x * mat[1] + y * mat[5] + z * mat[9]
    tz = x * mat[2] + y * mat[6] + z * mat[10]
    l = (tx*tx + ty*ty + tz*tz)**0.5
    if l > 1e-6:
        return [tx/l, ty/l, tz/l]
    return [tx, ty, tz]

def normalize_mesh_normals(model):
    for mesh in model.meshes:
        normalized = []
        for normal in mesh.normals:
            length = math.sqrt(sum(component * component for component in normal))
            normalized.append(
                [component / length for component in normal]
                if length > 1e-6 else [0.0, 1.0, 0.0]
            )
        mesh.normals = normalized

def get_next_unpack(data, offset):
    while offset + 4 <= len(data):
        w = struct.unpack('<I', data[offset:offset+4])[0]
        if (w & 0x60000000) == 0x60000000:
            return offset
        offset += 4
    return -1

def fix_uv(uv_list):
    pass

def fix_colors(col_list):
    pass

def set_triangle_indices(vertex_count, offset, target_indices):
    for j in range(vertex_count - 2):
        if j % 2 == 0:
            v0 = offset + j
            v1 = offset + j + 1
            v2 = offset + j + 2
        else:
            v0 = offset + j + 1
            v1 = offset + j
            v2 = offset + j + 2
        if v0 != v1 and v1 != v2 and v0 != v2:
            target_indices.append([v0, v1, v2])

def parse_lit_lights(lit_data):
    if not lit_data or len(lit_data) < 24:
        return {'ambient': [0.18, 0.18, 0.18, 1.0], 'points': [], 'spots': []}

    ver, mapflag, kind, mats, coordp, matp, phead, blocks = struct.unpack('<IBBHIIII', lit_data[:24])
    ambient = [0.18, 0.18, 0.18, 1.0]
    points = []
    spots = []

    pk_offsets = struct.unpack(f'<{blocks}I', lit_data[24:24 + blocks*4])
    for off in pk_offsets:
        if off == 0 or off >= len(lit_data): continue
        curr = off
        while curr != 0 and curr + 8 <= len(lit_data):
            pnext, cat = struct.unpack('<II', lit_data[curr:curr+8])
            if cat == 1 and curr + 64 <= len(lit_data):
                num = (pnext - 16) // 32
                ptr = curr + 16
                for i in range(max(0, num)):
                    if ptr + 32 <= len(lit_data):
                        diffuse = struct.unpack('<4f', lit_data[ptr:ptr+16])
                        pos = struct.unpack('<4f', lit_data[ptr+16:ptr+32])
                        points.append({
                            'pos': [pos[0], pos[1], pos[2]],
                            'diffuse': [diffuse[0], diffuse[1], diffuse[2]],
                            'power': max(1.0, diffuse[3] * 100.0)
                        })
                        ptr += 32
            elif cat == 3 and curr + 32 <= len(lit_data):
                amb = struct.unpack('<4f', lit_data[curr+16:curr+32])
                ambient = [amb[0], amb[1], amb[2], 1.0]
            if pnext == 0: break
            curr += pnext

    return {'ambient': ambient, 'points': points, 'spots': spots}

def apply_lighting_to_vertex(pos, normal, lights):
    amb = lights['ambient']
    cr = amb[0]
    cg = amb[1]
    cb = amb[2]

    px, py, pz = pos
    nx, ny, nz = normal

    for pt in lights['points']:
        lx = pt['pos'][0] - px
        ly = pt['pos'][1] - py
        lz = pt['pos'][2] - pz
        dist_sq = lx*lx + ly*ly + lz*lz
        dist = math.sqrt(dist_sq) if dist_sq > 0 else 1.0
        inv_d = 1.0 / dist
        lx *= inv_d
        ly *= inv_d
        lz *= inv_d

        ndotl = max(0.0, nx*lx + ny*ly + nz*lz)
        att = max(0.0, 1.0 - (dist / pt['power']))
        pd = pt['diffuse']
        cr += pd[0] * ndotl * att
        cg += pd[1] * ndotl * att
        cb += pd[2] * ndotl * att

    return [min(1.0, max(0.0, cr)), min(1.0, max(0.0, cg)), min(1.0, max(0.0, cb)), 1.0]

def parse_sgd(data, name="sgd", lit_data=None, external_bones=None):
    if len(data) < 24:
        return None

    ver, mapflag, kind, mats, coordp, matp, phead_off, blocks = struct.unpack('<IBBHIIII', data[:24])
    model = SGDModel(name)

    lights = parse_lit_lights(lit_data) if lit_data else None

    # Use provided external bones (for sibling SGDs sharing a skeleton)
    if external_bones is not None:
        model.bones = list(external_bones)
    # Parse bones from coordp
    elif coordp > 0 and coordp < len(data):
        num_bones = blocks - 1 if blocks > 1 else 1
        for b in range(num_bones):
            co = coordp + b * 224
            if co + 224 <= len(data):
                mat = list(struct.unpack('<16f', data[co:co+64]))
                rot = list(struct.unpack('<4f', data[co+192:co+208]))
                parent = struct.unpack('<i', data[co+208:co+212])[0]
                bone = SGDBone(b, parent)
                bone.matrix = mat
                bone.rot = rot
                bone.trans = [mat[12], mat[13], mat[14]]
                model.bones.append(bone)

    # Parse PHEAD (master vertex / normal pool)
    pUniqV = 0
    pUniqN = 0
    pWeightV = 0
    pWeightN = 0
    if phead_off > 0 and phead_off + 52 <= len(data):
        ph = struct.unpack('<13I', data[phead_off:phead_off+52])
        pUniqV = ph[2]
        pUniqN = ph[3]
        pWeightV = ph[10]
        pWeightN = ph[11]

    # Parse materials (0xB0 = 176 bytes per SgMaterial)
    if matp > 0 and matp < len(data):
        mat_size = 176
        for m in range(mats):
            mo = matp + m * mat_size
            if mo + 64 <= len(data):
                ptype = struct.unpack('<I', data[mo:mo+4])[0]
                mname = data[mo+4:mo+16].decode('ascii', errors='ignore').strip("\x00") or f'Material_{m}'
                amb = list(struct.unpack('<4f', data[mo+16:mo+32]))
                diff = list(struct.unpack('<4f', data[mo+32:mo+48]))
                spec = list(struct.unpack('<4f', data[mo+48:mo+64]))
                emiss = list(struct.unpack('<4f', data[mo+64:mo+80])) if mo+80 <= len(data) else [0,0,0,1]
                tex0_low = struct.unpack('<I', data[mo+112:mo+116])[0] if mo+116 <= len(data) else 0
                tbp0 = tex0_low & 0x3FFF

                mat = SGDMaterial(m, mname)
                mat.ambient = amb
                mat.diffuse = diff
                mat.specular = spec
                mat.emission = emiss
                mat.tex0_low = tex0_low
                mat.tbp0 = tbp0
                mat.texture_index = -1
                model.materials.append(mat)

    if not model.materials:
        model.materials.append(SGDMaterial(0, "Default"))

    # Read Primitive blocks
    pk_offsets = struct.unpack(f'<{blocks}I', data[24:24 + blocks*4])
    start_block = 0 if blocks == 1 else 1

    for b_idx in range(start_block, blocks):
        off = pk_offsets[b_idx]
        if off == 0 or off >= len(data):
            continue

        # Pre-scan this block for a cat=3 COORDINATE unit to get the real bone index.
        # The CAT3 iCoordId0 is the authoritative bone ID; b_idx is just the array slot.
        # Example: all b_idx=26 blocks store iCoordId0=1 (attached to the torso bone).
        coord_id = b_idx  # fallback: use block index directly
        _pre = off
        while _pre != 0 and _pre + 16 <= len(data):
            _pn, _cat = struct.unpack('<II', data[_pre:_pre+8])
            if _cat == 3 and _pre + 12 <= len(data):
                _cid = struct.unpack('<i', data[_pre+8:_pre+12])[0]
                if 0 <= _cid < len(model.bones):
                    coord_id = _cid
                    break
            if _pn == 0:
                break
            _pre += _pn

        curr = off
        vuvn_off = None
        vnum = 0
        nnum = 0
        vtype = 0
        current_mat_idx = 0

        while curr != 0 and curr + 16 <= len(data):
            pnext, cat = struct.unpack('<II', data[curr:curr+8])

            if cat == 0: # VUVN
                vuvn_off = curr
                vnum, nnum = struct.unpack('<hh', data[curr+8:curr+12])
                vtype = data[curr+13]

            elif cat == 3: # COORDINATE — already handled in pre-scan, skip
                pass

            elif cat == 2: # MATERIAL
                current_mat_idx = struct.unpack('<I', data[curr+8:curr+12])[0]

            elif cat == 4: # BOUNDING_BOX
                if curr + 16 + 128 <= len(data):
                    bb_verts = []
                    for vi in range(8):
                        bb_verts.append(list(struct.unpack('<4f', data[curr+16+vi*16:curr+32+vi*16])[:3]))
                    model.bounding_boxes.append(bb_verts)

            elif cat == 1 and vuvn_off is not None: # MESH
                mtype = data[curr+13]
                num_mesh = data[curr+14]

                # Extract tex0 from MESH unit if available (at curr + 40)
                mesh_tex0_low = struct.unpack('<I', data[curr+40:curr+44])[0] if curr + 44 <= len(data) else 0
                mesh_tbp0 = mesh_tex0_low & 0x3FFF
                if mesh_tbp0 >= 0 and current_mat_idx < len(model.materials):
                    material = model.materials[current_mat_idx]
                    if material.tbp0 == 0 and mesh_tex0_low != 0:
                        material.tex0_low = mesh_tex0_low
                        material.tbp0 = mesh_tbp0

                # Case 1: Skinned Character Meshes (0x2, 0xA)
                if (mtype & 0xD3) == 0x2:
                    mesh = SGDMesh(f'mesh_b{coord_id}_t0x{mtype:x}')
                    mesh.material_index = current_mat_idx
                    mesh.bone_index = coord_id

                    # Read (v_idx, n_idx) pairs from vuvn_off + 48
                    v_indices = []
                    n_indices = []
                    idx_ptr = vuvn_off + 48
                    for _ in range(vnum):
                        if idx_ptr + 8 <= len(data):
                            vi_val, ni_val = struct.unpack('<II', data[idx_ptr:idx_ptr+8])
                            v_indices.append(vi_val)
                            n_indices.append(ni_val)
                        else:
                            v_indices.append(0)
                            n_indices.append(0)
                        idx_ptr += 8

                    # Submesh strip counts at curr + 64
                    pinfo_off = curr + 64
                    # Character files use the compact point-number/ST layout.
                    st_ptr = pinfo_off + num_mesh * 8 + 12
                    mesh_vert_offset = 0

                    for m_i in range(num_mesh):
                        if pinfo_off + m_i * 8 + 8 > len(data):
                            break
                        pt_num = struct.unpack('<I', data[pinfo_off + m_i * 8 + 4 : pinfo_off + m_i * 8 + 8])[0]
                        if pt_num < 3 or pt_num > 4096:
                            st_ptr += 4 + max(0, pt_num) * 8
                            continue

                        # Read submesh UVs
                        sub_uvs = []
                        if st_ptr + 4 + pt_num * 8 <= len(data):
                            for vi in range(pt_num):
                                uv_off = st_ptr + 4 + vi * 8
                                u = struct.unpack('<f', data[uv_off:uv_off + 4])[0]
                                raw_v = struct.unpack('<f', data[uv_off + 4:uv_off + 8])[0]
                                if struct.unpack('<I', data[uv_off + 4:uv_off + 8])[0] == 1:
                                    if vi >= 2 and sub_uvs:
                                        raw_v = 1.0 - sub_uvs[vi - 2][1]
                                    elif vi >= 1 and sub_uvs:
                                        raw_v = 1.0 - sub_uvs[vi - 1][1]
                                sub_uvs.append([u, 1.0 - raw_v])
                        else:
                            sub_uvs = [[0.0, 0.0]] * pt_num
                        fix_uv(sub_uvs)

                        # Read vertices and normals
                        for vi in range(pt_num):
                            gv = mesh_vert_offset + vi
                            pos = [0.0, 0.0, 0.0]
                            norm = [0.0, 1.0, 0.0]
                            vert_weights = [1.0, 0.0, 0.0, 0.0]
                            joint0, joint1 = coord_id, 0

                            if gv < len(v_indices):
                                vi_idx = v_indices[gv]
                                ni_idx = n_indices[gv]

                                if vtype == 0 or pWeightV == 0:
                                    # SVA_UNIQUE: rigid single-bone vertex (16 bytes each)
                                    po = pUniqV + vi_idx * 16
                                    no = pUniqN + ni_idx * 16
                                    if po + 12 <= len(data):
                                        pos = list(struct.unpack('<3f', data[po:po+12]))
                                    if pUniqN and no + 12 <= len(data):
                                        norm = list(struct.unpack('<3f', data[no:no+12]))
                                    bone = model.bones[coord_id] if coord_id < len(model.bones) else None
                                    if bone:
                                        pos = transform_pos(pos, bone.matrix)
                                        norm = transform_norm(norm, bone.matrix)
                                else:
                                    # SVA_WEIGHTED: dual-bone blended vertex
                                    # _SGDVUVNDATA_WEIGHTEDVERTEX_3 (32 bytes):
                                    #   +0  vVertex (Vector4): xyz=pos_bone0, w=blend_weight (0-255 float)
                                    #   +16 aui     (Vector3): pos_bone1
                                    #   +28 ucBoneId0 (1 byte), ucBoneId1 (1 byte), pad[2]
                                    po = pWeightV + vi_idx * 32
                                    bid0, bid1 = coord_id, 0
                                    if po + 30 <= len(data):
                                        vx, vy, vz, vw = struct.unpack('<4f', data[po:po+16])
                                        ax, ay, az = struct.unpack('<3f', data[po+16:po+28])
                                        bid0, bid1 = struct.unpack('<BB', data[po+28:po+30])
                                        # PS2 stores blend weight as 0-255 float
                                        w = max(0.0, min(1.0, vw / 255.0 if vw > 1.0 else vw))
                                        b0 = model.bones[bid0] if bid0 < len(model.bones) else None
                                        b1 = model.bones[bid1] if bid1 < len(model.bones) else None
                                        p0 = transform_pos([vx, vy, vz], b0.matrix) if b0 else [vx, vy, vz]
                                        p1 = transform_pos([ax, ay, az], b1.matrix) if b1 else [ax, ay, az]
                                        pos = [p0[i] * w + p1[i] * (1.0 - w) for i in range(3)]
                                        joint0, joint1 = bid0, bid1
                                        vert_weights = [w, 1.0 - w, 0.0, 0.0]
                                    # Normal: use pWeightN, transform with bone0 (simplified)
                                    no = pWeightN + ni_idx * 32 if pWeightN else 0
                                    if pWeightN and no + 12 <= len(data):
                                        norm = list(struct.unpack('<3f', data[no:no+12]))
                                        b0n = model.bones[bid0] if bid0 < len(model.bones) else None
                                        if b0n:
                                            norm = transform_norm(norm, b0n.matrix)

                            mesh.positions.append(pos)
                            mesh.normals.append(norm)
                            mesh.uvs.append(sub_uvs[vi] if vi < len(sub_uvs) else [0.0, 0.0])
                            mesh.colors.append([0.9, 0.9, 0.9, 1.0])
                            mesh.joints.append([joint0, joint1, 0, 0])
                            mesh.weights.append(vert_weights)


                        set_triangle_indices(pt_num, mesh_vert_offset, mesh.indices)
                        mesh_vert_offset += pt_num
                        st_ptr += 4 + pt_num * 8

                    # Room 0x30 packets are auxiliary VIF data, not render triangles.
                    if mtype == 0x30:
                        continue
                    if mesh.positions and mesh.indices:
                        model.meshes.append(mesh)

                # Case 2: Rigid / Prop Character Meshes (0x82)
                elif mtype == 0x82:
                    mesh = SGDMesh(f'mesh_b{coord_id}_t0x{mtype:x}')
                    mesh.material_index = current_mat_idx
                    mesh.bone_index = coord_id

                    vert_start = vuvn_off + 56
                    pinfo_off = curr + 64
                    st_ptr = pinfo_off + num_mesh * 8 + 12
                    mesh_vert_offset = 0

                    for m_i in range(num_mesh):
                        if pinfo_off + m_i * 8 + 8 > len(data):
                            break
                        pt_num = struct.unpack('<I', data[pinfo_off + m_i * 8 + 4 : pinfo_off + m_i * 8 + 8])[0]
                        if pt_num < 3 or pt_num > 4096:
                            st_ptr += 4 + max(0, pt_num) * 8
                            continue

                        sub_uvs = []
                        if st_ptr + 4 + pt_num * 8 <= len(data):
                            for vi in range(pt_num):
                                uv_off = st_ptr + 4 + vi * 8
                                u = struct.unpack('<f', data[uv_off:uv_off + 4])[0]
                                raw_v = struct.unpack('<f', data[uv_off + 4:uv_off + 8])[0]
                                if struct.unpack('<I', data[uv_off + 4:uv_off + 8])[0] == 1:
                                    if vi >= 2 and sub_uvs:
                                        raw_v = 1.0 - sub_uvs[vi - 2][1]
                                    elif vi >= 1 and sub_uvs:
                                        raw_v = 1.0 - sub_uvs[vi - 1][1]
                                sub_uvs.append([u, 1.0 - raw_v])
                        else:
                            sub_uvs = [[0.0, 0.0]] * pt_num
                        fix_uv(sub_uvs)

                        for vi in range(pt_num):
                            gv = mesh_vert_offset + vi
                            vo = vert_start + gv * 24
                            pos = [0.0, 0.0, 0.0]
                            norm = [0.0, 1.0, 0.0]
                            if vo + 24 <= len(data):
                                vx, vy, vz, nx, ny, nz = struct.unpack('<6f', data[vo:vo+24])
                                pos = [vx, vy, vz]
                                norm = [nx, ny, nz]

                            bone = model.bones[coord_id] if coord_id < len(model.bones) else None
                            b_mat = bone.matrix if bone else None
                            if b_mat:
                                pos = transform_pos(pos, b_mat)
                                norm = transform_norm(norm, b_mat)

                            mesh.positions.append(pos)
                            mesh.normals.append(norm)
                            mesh.uvs.append(sub_uvs[vi] if vi < len(sub_uvs) else [0.0, 0.0])
                            mesh.colors.append([0.9, 0.9, 0.9, 1.0])
                            mesh.joints.append([coord_id, 0, 0, 0])
                            mesh.weights.append([1.0, 0.0, 0.0, 0.0])

                        set_triangle_indices(pt_num, mesh_vert_offset, mesh.indices)
                        mesh_vert_offset += pt_num
                        st_ptr += 4 + pt_num * 8

                    if mtype == 0x30:
                        continue
                    if mesh.positions and mesh.indices:
                        model.meshes.append(mesh)

                # Case 3: Room Environment Meshes (0x32 and preset unpacked)
                elif mtype == 0x32 or (mtype & 0x10):
                    proc_data = curr + 16
                    offset_to_st, offset_to_prim = struct.unpack('<hh', data[proc_data+4:proc_data+8])
                    st_data_off = proc_data + (offset_to_st - 1) * 4
                    prim_data_off = curr + offset_to_prim * 4

                    if mtype == 0x32:
                        pvuvn_data = vuvn_off + 16
                        vert_start = pvuvn_data + (nnum * 3 + 10) * 4
                    else:
                        vert_start = vuvn_off + 14 * 4

                    mesh = SGDMesh(f'mesh_b{coord_id}_t0x{mtype:x}')
                    mesh.material_index = current_mat_idx
                    mesh.bone_index = coord_id

                    vmcd_off = prim_data_off
                    curr_st = st_data_off
                    mesh_vert_offset = 0

                    for m_i in range(num_mesh):
                        up = get_next_unpack(data, vmcd_off)
                        if up == -1 or up >= len(data):
                            break

                        unpack_word = struct.unpack('<I', data[up:up+4])[0]
                        v_count = (unpack_word >> 16) & 0xFF
                        if v_count < 3:
                            break

                        sub_normal = [0.0, 1.0, 0.0]
                        if mtype == 0x32:
                            norm_off = vuvn_off + 16 + (m_i * 3 + 10) * 4
                            if norm_off + 12 <= len(data):
                                sub_normal = list(struct.unpack('<3f', data[norm_off:norm_off+12]))

                        sub_uvs = []
                        up_st = get_next_unpack(data, curr_st)
                        if up_st != -1 and up_st + 4 + v_count * 8 <= len(data):
                            for vi in range(v_count):
                                u_off = up_st + 4 + vi * 8
                                u, v = struct.unpack('<ff', data[u_off : u_off + 8])
                                # PS2 tristrip T-sentinel: raw int 0x1 means "repeat T from vi-2"
                                raw_t = struct.unpack('<I', data[u_off + 4 : u_off + 8])[0]
                                if vi >= 2 and raw_t == 0x1:
                                    v = sub_uvs[vi - 2][1]
                                sub_uvs.append([u, v])
                            curr_st = up_st + 4 + v_count * 8
                        else:
                            for vi in range(v_count):
                                uo = curr_st + vi * 8
                                if uo + 8 <= len(data):
                                    u, v = struct.unpack('<ff', data[uo:uo+8])
                                    raw_t = struct.unpack('<I', data[uo + 4 : uo + 8])[0]
                                    if vi >= 2 and raw_t == 0x1:
                                        v = sub_uvs[vi - 2][1]
                                    sub_uvs.append([u, v])
                                else:
                                    sub_uvs.append([0.0, 0.0])
                            curr_st += v_count * 8

                        sub_cols = []
                        col_base = up + 4
                        any_col_nonzero = False
                        for vi in range(v_count):
                            co = col_base + vi * 12
                            if co + 12 <= len(data):
                                # PS2 tristrip color-sentinel: raw int 0x1 in R means "repeat color from vi-2"
                                raw_r = struct.unpack('<I', data[co : co + 4])[0]
                                if vi >= 2 and raw_r == 0x1:
                                    sub_cols.append(list(sub_cols[vi - 2]))
                                    continue
                                r, g, b = struct.unpack('<fff', data[co:co+12])
                                if r > 0.001 or g > 0.001 or b > 0.001:
                                    any_col_nonzero = True
                                cr = min(1.0, max(0.0, r / 128.0 if r > 1.0 else r))
                                cg = min(1.0, max(0.0, g / 128.0 if g > 1.0 else g))
                                cb = min(1.0, max(0.0, b / 128.0 if b > 1.0 else b))
                                sub_cols.append([cr, cg, cb, 1.0])
                            else:
                                sub_cols.append([0.8, 0.8, 0.8, 1.0])

                        for vi in range(v_count):
                            joint0, joint1 = coord_id, 0
                            vert_weights = [1.0, 0.0, 0.0, 0.0]
                            if mtype == 0x32:
                                vo = vert_start + (mesh_vert_offset + vi) * 12
                                if vo + 12 <= len(data):
                                    vx, vy, vz = struct.unpack('<fff', data[vo:vo+12])
                                    pos = [vx, vy, vz]
                                else:
                                    pos = [0.0, 0.0, 0.0]
                                norm = list(sub_normal)
                            else:
                                vo = vert_start + (mesh_vert_offset + vi) * 24
                                if vo + 24 <= len(data):
                                    vx, vy, vz, nx, ny, nz = struct.unpack('<6f', data[vo:vo+24])
                                    pos = [vx, vy, vz]
                                    norm = [nx, ny, nz]
                                else:
                                    pos = [0.0, 0.0, 0.0]
                                    norm = [0.0, 1.0, 0.0]

                            mesh.positions.append(pos)
                            mesh.normals.append(norm)
                            mesh.uvs.append(sub_uvs[vi])

                            if lights and not any_col_nonzero:
                                lit_col = apply_lighting_to_vertex(pos, norm, lights)
                                mesh.colors.append(lit_col)
                            else:
                                if any_col_nonzero:
                                    mesh.colors.append(sub_cols[vi])
                                else:
                                    mesh.colors.append([0.85, 0.85, 0.85, 1.0])

                            mesh.joints.append([joint0, joint1, 0, 0])
                            mesh.weights.append(vert_weights)

                        set_triangle_indices(v_count, mesh_vert_offset, mesh.indices)
                        mesh_vert_offset += v_count
                        vmcd_off = up + 4 + v_count * 12

                    if mesh.positions and mesh.indices:
                        model.meshes.append(mesh)

            if pnext == 0:
                break
            curr += pnext

    normalize_mesh_normals(model)
    return model


def merge_sgd_models(base_model, extra_model):
    """Merge extra_model's meshes and materials into base_model.

    The base_model's skeleton (bones) is kept.  Material indices in the
    extra meshes are offset so they reference the appended material slots.
    Returns base_model (modified in place).
    """
    mat_offset = len(base_model.materials)

    # Append materials (re-index them)
    for mat in extra_model.materials:
        new_mat = SGDMaterial(mat.index + mat_offset, mat.name)
        new_mat.ambient       = mat.ambient
        new_mat.diffuse       = mat.diffuse
        new_mat.specular      = mat.specular
        new_mat.emission      = mat.emission
        new_mat.tex0_low      = mat.tex0_low
        new_mat.tbp0          = mat.tbp0
        new_mat.texture_index = mat.texture_index
        base_model.materials.append(new_mat)

    # Append meshes with adjusted material index
    for mesh in extra_model.meshes:
        mesh.material_index += mat_offset
        base_model.meshes.append(mesh)

    return base_model
