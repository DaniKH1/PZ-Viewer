import os
import json
import struct
import math
import copy
import base64
from io import BytesIO
from PIL import Image


def _normalized_normal(normal):
    length = math.sqrt(sum(float(component) ** 2 for component in normal[:3]))
    if length <= 1e-8:
        return [0.0, 1.0, 0.0]
    return [float(component) / length for component in normal[:3]]


def _invert_matrix(matrix):
    m = list(matrix[:16])
    if len(m) < 16:
        return [1.0 if i % 5 == 0 else 0.0 for i in range(16)]
    out = [0.0] * 16
    inv = [
        m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10],
        -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10],
        m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6],
        -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6],
        -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10],
        m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10],
        -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6],
        m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6],
        m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9],
        -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9],
        m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5],
        -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5],
        -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9],
        m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9],
        -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5],
        m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5],
    ]
    determinant = sum(m[i] * inv[i * 4] for i in range(4))
    if abs(determinant) <= 1e-10:
        return [1.0 if i % 5 == 0 else 0.0 for i in range(16)]
    return [value / determinant for value in inv]


def _multiply_matrix(a, b):
    return [
        sum(a[row + k * 4] * b[k + col * 4] for k in range(4))
        for col in range(4) for row in range(4)
    ]


def _merge_meshes_by_texture(model):
    """Combine compatible submeshes so one exported mesh represents one texture."""
    groups = {}
    for mesh in getattr(model, "meshes", []):
        material = model.materials[mesh.material_index] if (
            0 <= mesh.material_index < len(getattr(model, "materials", []))
        ) else None
        key = getattr(material, "texture_index", -1)
        if key not in groups:
            groups[key] = copy.copy(mesh)
            groups[key].positions = list(mesh.positions)
            groups[key].normals = list(mesh.normals)
            groups[key].uvs = list(mesh.uvs)
            groups[key].colors = list(mesh.colors)
            groups[key].indices = [list(t) for t in mesh.indices]
            groups[key].joints = list(mesh.joints)
            groups[key].weights = list(mesh.weights)
            continue

        target = groups[key]
        offset = len(target.positions)
        target.positions.extend(mesh.positions)
        target.normals.extend(mesh.normals)
        target.uvs.extend(mesh.uvs)
        target.colors.extend(mesh.colors)
        target.joints.extend(mesh.joints)
        target.weights.extend(mesh.weights)
        target.indices.extend([[a + offset, b + offset, c + offset] for a, b, c in mesh.indices])
    return list(groups.values())

def _texture_image(texture, index):
    """Return a detached PIL image from a decoded image or serialized data URI."""
    if isinstance(texture, Image.Image):
        return texture
    if not isinstance(texture, dict):
        raise ValueError(f"Texture {index} has no decoded image data")

    data_uri = texture.get("data_uri")
    if not isinstance(data_uri, str) or not data_uri.strip():
        raise ValueError(f"Texture {index} has missing or empty data_uri")
    try:
        header, encoded = data_uri.split(",", 1)
        if ";base64" not in header.lower():
            raise ValueError("data_uri is not base64 encoded")
        image = Image.open(BytesIO(base64.b64decode(encoded, validate=True)))
        image.load()
        return image
    except (ValueError, OSError) as exc:
        raise ValueError(f"Texture {index} has invalid data_uri: {exc}") from exc


def export_textures_png(model, textures, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    saved_files = []
    if textures is None:
        raise ValueError("No texture data was provided")
    if not isinstance(textures, (list, tuple)):
        raise ValueError("Texture data must be a list")
    if not textures:
        return saved_files

    base_name = getattr(model, 'name', 'model')
    for idx, texture in enumerate(textures):
        img = _texture_image(texture, idx)
        mat_names = [m.name for m in getattr(model, 'materials', []) if getattr(m, 'texture_index', -1) == idx]
        if mat_names:
            clean_name = os.path.splitext(mat_names[0])[0].replace(' ', '_')
            t_name = f"{idx:02d}_{clean_name}.png"
        else:
            t_name = f"{base_name}_tex_{idx:02d}.png"

        t_path = os.path.join(output_dir, t_name)
        img.save(t_path, format='PNG')
        saved_files.append((t_name, t_path, idx))

    return saved_files

def export_obj(model, output_path, include_vertex_colors=True, textures=None):
    base_name = os.path.splitext(os.path.basename(output_path))[0]
    out_dir = os.path.dirname(output_path) or "."
    mtl_filename = f"{base_name}.mtl"
    mtl_path = os.path.join(out_dir, mtl_filename)

    # Save textures as PNGs both in out_dir and in dedicated textures subfolder
    tex_filenames = {}
    if textures:
        saved = export_textures_png(model, textures, out_dir)
        for t_name, t_path, idx in saved:
            tex_filenames[idx] = t_name
        tex_subfolder = os.path.join(out_dir, f"{base_name}_textures")
        export_textures_png(model, textures, tex_subfolder)

    # Write MTL
    with open(mtl_path, 'w', encoding='utf-8') as mf:
        for mat in getattr(model, 'materials', []):
            mf.write(f"newmtl {mat.name}\n")
            mf.write(f"Kd {mat.diffuse[0]:.4f} {mat.diffuse[1]:.4f} {mat.diffuse[2]:.4f}\n")
            mf.write(f"Ka {mat.ambient[0]:.4f} {mat.ambient[1]:.4f} {mat.ambient[2]:.4f}\n")
            mf.write(f"Ks {mat.specular[0]:.4f} {mat.specular[1]:.4f} {mat.specular[2]:.4f}\n")
            mf.write(f"d {mat.diffuse[3]:.4f}\n")
            if mat.texture_index in tex_filenames:
                mf.write(f"map_Kd {tex_filenames[mat.texture_index]}\n")
            mf.write("\n")

    export_meshes = _merge_meshes_by_texture(model)
    # Write OBJ
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# Fatal Frame PZ Extractor OBJ Export\n")
        f.write(f"mtllib {mtl_filename}\n\n")

        v_offset = 1
        vn_offset = 1
        vt_offset = 1

        for mesh in export_meshes:
            f.write(f"o {mesh.name}\n")
            mat_name = "Default"
            if getattr(model, 'materials', None) and mesh.material_index < len(model.materials):
                mat_name = model.materials[mesh.material_index].name
            f.write(f"usemtl {mat_name}\n")

            has_colors = include_vertex_colors and len(mesh.colors) == len(mesh.positions)
            has_normals = len(mesh.normals) == len(mesh.positions)
            has_uvs = len(mesh.uvs) == len(mesh.positions)

            # Vertices (+ vertex colors)
            for i, p in enumerate(mesh.positions):
                if has_colors:
                    c = mesh.colors[i]
                    f.write(f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f} {c[0]:.4f} {c[1]:.4f} {c[2]:.4f}\n")
                else:
                    f.write(f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n")

            # Normals
            if has_normals:
                for n in mesh.normals:
                    f.write(f"vn {n[0]:.4f} {n[1]:.4f} {n[2]:.4f}\n")

            # UVs
            if has_uvs:
                for uv in mesh.uvs:
                    v = uv[1] if getattr(model, "uvs_are_flipped", False) else 1.0 - uv[1]
                    f.write(f"vt {uv[0]:.6f} {v:.6f}\n")

            # Faces
            for tri in mesh.indices:
                v0 = v_offset + tri[0]
                v1 = v_offset + tri[1]
                v2 = v_offset + tri[2]
                vt0 = vt_offset + tri[0]
                vt1 = vt_offset + tri[1]
                vt2 = vt_offset + tri[2]
                vn0 = vn_offset + tri[0]
                vn1 = vn_offset + tri[1]
                vn2 = vn_offset + tri[2]
                if has_uvs and has_normals:
                    f.write(f"f {v0}/{vt0}/{vn0} {v1}/{vt1}/{vn1} {v2}/{vt2}/{vn2}\n")
                elif has_normals:
                    f.write(f"f {v0}//{vn0} {v1}//{vn1} {v2}//{vn2}\n")
                elif has_uvs:
                    f.write(f"f {v0}/{vt0} {v1}/{vt1} {v2}/{vt2}\n")
                else:
                    f.write(f"f {v0} {v1} {v2}\n")

            v_offset += len(mesh.positions)
            if has_normals:
                vn_offset += len(mesh.normals)
            if has_uvs:
                vt_offset += len(mesh.uvs)
            f.write("\n")

    return output_path

def export_glb(model, output_path, export_t_pose=True, animations=None, include_vertex_colors=True, textures=None, save_textures_folder=True, include_armature=True):
    base_name = os.path.splitext(os.path.basename(output_path))[0]
    out_dir = os.path.dirname(output_path) or "."
    if textures and save_textures_folder:
        tex_subfolder = os.path.join(out_dir, f"{base_name}_textures")
        export_textures_png(model, textures, tex_subfolder)

    bin_chunks = bytearray()

    def add_buffer_data(data_bytes):
        offset = len(bin_chunks)
        bin_chunks.extend(data_bytes)
        # Pad to 4 bytes
        while len(bin_chunks) % 4 != 0:
            bin_chunks.append(0)
        return offset, len(data_bytes)

    gltf = {
        "asset": {"version": "2.0", "generator": "Fatal Frame PZ Viewer"},
        "scenes": [{"nodes": []}],
        "nodes": [],
        "meshes": [],
        "materials": [],
        "textures": [],
        "images": [],
        "skins": [],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "accessors": [],
        "bufferViews": [],
        "buffers": []
    }
    export_meshes = _merge_meshes_by_texture(model)

    # Textures & Images
    if textures:
        for idx, img in enumerate(textures):
            buf = BytesIO()
            img.save(buf, format='PNG')
            png_bytes = buf.getvalue()
            offset, length = add_buffer_data(png_bytes)
            bv_idx = len(gltf["bufferViews"])
            gltf["bufferViews"].append({
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": length
            })
            img_idx = len(gltf["images"])
            gltf["images"].append({
                "bufferView": bv_idx,
                "mimeType": "image/png",
                "name": f"Texture_{idx}"
            })
            tex_idx = len(gltf["textures"])
            gltf["textures"].append({
                "sampler": 0,
                "source": img_idx
            })

    # Materials
    for m in getattr(model, 'materials', []):
        mat_def = {
            "name": m.name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [m.diffuse[0], m.diffuse[1], m.diffuse[2], m.diffuse[3]],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.8
            },
            "doubleSided": True
        }
        if m.texture_index >= 0 and m.texture_index < len(gltf["textures"]):
            mat_def["pbrMetallicRoughness"]["baseColorTexture"] = {"index": m.texture_index}
        gltf["materials"].append(mat_def)

    if not gltf["materials"]:
        gltf["materials"].append({
            "name": "Default",
            "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.8, 0.8, 1.0], "metallicFactor": 0.0, "roughnessFactor": 0.8},
            "doubleSided": True
        })

    # Meshes
    mesh_node_indices = []
    for mesh in export_meshes:
        if not mesh.positions or not mesh.indices:
            continue

        # Positions accessor
        pos_bytes = bytearray()
        min_p = [float('inf')]*3
        max_p = [float('-inf')]*3
        for p in mesh.positions:
            pos_bytes.extend(struct.pack('<3f', p[0], p[1], p[2]))
            for k in range(3):
                min_p[k] = min(min_p[k], p[k])
                max_p[k] = max(max_p[k], p[k])

        p_off, p_len = add_buffer_data(pos_bytes)
        p_bv = len(gltf["bufferViews"])
        gltf["bufferViews"].append({"buffer": 0, "byteOffset": p_off, "byteLength": p_len, "target": 34962})
        pos_acc = len(gltf["accessors"])
        gltf["accessors"].append({
            "bufferView": p_bv, "byteOffset": 0, "componentType": 5126, "count": len(mesh.positions),
            "type": "VEC3", "max": max_p, "min": min_p
        })

        attributes = {"POSITION": pos_acc}

        # Normals accessor
        if len(mesh.normals) == len(mesh.positions):
            norm_bytes = bytearray()
            for n in mesh.normals:
                norm_bytes.extend(struct.pack('<3f', *_normalized_normal(n)))
            n_off, n_len = add_buffer_data(norm_bytes)
            n_bv = len(gltf["bufferViews"])
            gltf["bufferViews"].append({"buffer": 0, "byteOffset": n_off, "byteLength": n_len, "target": 34962})
            norm_acc = len(gltf["accessors"])
            gltf["accessors"].append({
                "bufferView": n_bv, "byteOffset": 0, "componentType": 5126, "count": len(mesh.normals), "type": "VEC3"
            })
            attributes["NORMAL"] = norm_acc

        # UVs accessor
        if len(mesh.uvs) == len(mesh.positions):
            uv_bytes = bytearray()
            for uv in mesh.uvs:
                v = uv[1] if getattr(model, "uvs_are_flipped", False) else 1.0 - uv[1]
                uv_bytes.extend(struct.pack('<2f', uv[0], v))
            uv_off, uv_len = add_buffer_data(uv_bytes)
            uv_bv = len(gltf["bufferViews"])
            gltf["bufferViews"].append({"buffer": 0, "byteOffset": uv_off, "byteLength": uv_len, "target": 34962})
            uv_acc = len(gltf["accessors"])
            gltf["accessors"].append({
                "bufferView": uv_bv, "byteOffset": 0, "componentType": 5126, "count": len(mesh.uvs), "type": "VEC2"
            })
            attributes["TEXCOORD_0"] = uv_acc

        # Vertex Colors (COLOR_0)
        if include_vertex_colors and len(mesh.colors) == len(mesh.positions):
            col_bytes = bytearray()
            for c in mesh.colors:
                col_bytes.extend(struct.pack('<4f', c[0], c[1], c[2], c[3]))
            c_off, c_len = add_buffer_data(col_bytes)
            c_bv = len(gltf["bufferViews"])
            gltf["bufferViews"].append({"buffer": 0, "byteOffset": c_off, "byteLength": c_len, "target": 34962})
            col_acc = len(gltf["accessors"])
            gltf["accessors"].append({
                "bufferView": c_bv, "byteOffset": 0, "componentType": 5126, "count": len(mesh.colors), "type": "VEC4"
            })
            attributes["COLOR_0"] = col_acc

        if len(mesh.joints) == len(mesh.positions) and len(mesh.weights) == len(mesh.positions):
            joint_bytes = bytearray()
            weight_bytes = bytearray()
            for joints, weights in zip(mesh.joints, mesh.weights):
                joint_bytes.extend(struct.pack('<4H', *(int(max(0, j)) for j in joints[:4])))
                weight_bytes.extend(struct.pack('<4f', *(float(w) for w in weights[:4])))
            j_off, j_len = add_buffer_data(joint_bytes)
            j_bv = len(gltf["bufferViews"])
            gltf["bufferViews"].append({"buffer": 0, "byteOffset": j_off, "byteLength": j_len, "target": 34962})
            j_acc = len(gltf["accessors"])
            gltf["accessors"].append({"bufferView": j_bv, "byteOffset": 0, "componentType": 5123, "count": len(mesh.joints), "type": "VEC4"})
            w_off, w_len = add_buffer_data(weight_bytes)
            w_bv = len(gltf["bufferViews"])
            gltf["bufferViews"].append({"buffer": 0, "byteOffset": w_off, "byteLength": w_len, "target": 34962})
            w_acc = len(gltf["accessors"])
            gltf["accessors"].append({"bufferView": w_bv, "byteOffset": 0, "componentType": 5126, "count": len(mesh.weights), "type": "VEC4"})
            attributes["JOINTS_0"] = j_acc
            attributes["WEIGHTS_0"] = w_acc

        # Indices accessor
        idx_bytes = bytearray()
        for tri in mesh.indices:
            idx_bytes.extend(struct.pack('<3I', tri[0], tri[1], tri[2]))
        i_off, i_len = add_buffer_data(idx_bytes)
        i_bv = len(gltf["bufferViews"])
        gltf["bufferViews"].append({"buffer": 0, "byteOffset": i_off, "byteLength": i_len, "target": 34963})
        idx_acc = len(gltf["accessors"])
        gltf["accessors"].append({
            "bufferView": i_bv, "byteOffset": 0, "componentType": 5125, "count": len(mesh.indices) * 3, "type": "SCALAR"
        })

        mat_idx = mesh.material_index if mesh.material_index < len(gltf["materials"]) else 0
        gltf_mesh_idx = len(gltf["meshes"])
        gltf["meshes"].append({
            "name": mesh.name,
            "primitives": [{
                "attributes": attributes,
                "indices": idx_acc,
                "material": mat_idx
            }]
        })

        node_idx = len(gltf["nodes"])
        gltf["nodes"].append({"name": mesh.name, "mesh": gltf_mesh_idx})
        mesh_node_indices.append(node_idx)

    # Export the same armature layout as the reference exporter: an Armature
    # root, local bone matrices, and inverse bind matrices from bone space.
    bones = list(getattr(model, "bones", [])) if include_armature else []
    bone_names = {
        3: "hips", 12: "left leg", 8: "left knee", 5: "left ankle",
        23: "right leg", 19: "right knee", 16: "right ankle",
        25: "spine", 1: "chest", 14: "neck", 2: "head",
        4: "left eye", 15: "right eye", 9: "left shoulder",
        10: "left arm", 11: "Bone_11", 7: "left elbow",
        13: "Bone_13", 6: "left wrist", 20: "right shoulder",
        21: "right arm", 22: "Bone_22", 18: "right elbow",
        24: "Bone_24", 17: "right wrist"
    }
    bone_parents = {
        3: -1, 12: 3, 8: 12, 5: 8, 23: 3, 19: 23, 16: 19,
        25: 3, 1: 25, 14: 1, 2: 14, 4: 2, 15: 2, 9: 1,
        10: 9, 11: 10, 7: 11, 13: 7, 6: 13, 20: 1, 21: 20,
        22: 21, 18: 22, 24: 18, 17: 24
    }
    if bones:
        armature_node = len(gltf["nodes"])
        gltf["nodes"].append({"name": "Armature", "children": []})
        joint_nodes = []
        local_matrices = []
        for bone in bones:
            joint_nodes.append(len(gltf["nodes"]))
            parent = bone_parents.get(bone.index, getattr(bone, "parent", -1))
            bone_matrix = list(getattr(bone, "matrix", []))
            if 0 <= parent < len(bones):
                parent_matrix = list(getattr(bones[parent], "matrix", []))
                local_matrix = _multiply_matrix(_invert_matrix(parent_matrix), bone_matrix)
            else:
                local_matrix = bone_matrix
            local_matrices.append(local_matrix)
            name = bone_names.get(bone.index, f"Bone_{bone.index:02d}")
            gltf["nodes"].append({"name": name, "matrix": local_matrix})
        for i, bone in enumerate(bones):
            parent = bone_parents.get(bone.index, getattr(bone, "parent", -1))
            if 0 <= parent < len(joint_nodes):
                gltf["nodes"][joint_nodes[parent]].setdefault("children", []).append(joint_nodes[i])
            else:
                gltf["nodes"][armature_node]["children"].append(joint_nodes[i])
        ibm = bytearray()
        for bone in bones:
            ibm.extend(struct.pack('<16f', *_invert_matrix(getattr(bone, "matrix", []))))
        ibm_off, ibm_len = add_buffer_data(ibm)
        ibm_bv = len(gltf["bufferViews"])
        gltf["bufferViews"].append({"buffer": 0, "byteOffset": ibm_off, "byteLength": ibm_len})
        ibm_acc = len(gltf["accessors"])
        gltf["accessors"].append({"bufferView": ibm_bv, "byteOffset": 0, "componentType": 5126, "count": len(bones), "type": "MAT4"})
        skin_idx = len(gltf["skins"])
        gltf.setdefault("skins", []).append({"name": "Armature", "joints": joint_nodes, "skeleton": joint_nodes[0], "inverseBindMatrices": ibm_acc})
        for node_idx in mesh_node_indices:
            gltf["nodes"][node_idx]["skin"] = skin_idx
    # Add Root Scene Nodes
    gltf["scenes"][0]["nodes"] = list(mesh_node_indices)
    if bones:
        gltf["scenes"][0]["nodes"].append(armature_node)
    gltf["buffers"].append({"byteLength": len(bin_chunks)})

    # Package as GLB
    json_bytes = json.dumps(gltf).encode('utf-8')
    while len(json_bytes) % 4 != 0:
        json_bytes += b' '

    glb_len = 12 + 8 + len(json_bytes) + 8 + len(bin_chunks)
    header = struct.pack('<4sII', b'glTF', 2, glb_len)
    json_chunk_hdr = struct.pack('<II', len(json_bytes), 0x4E4F534A) # 'JSON'
    bin_chunk_hdr = struct.pack('<II', len(bin_chunks), 0x004E4942) # 'BIN\0'

    with open(output_path, 'wb') as f:
        f.write(header)
        f.write(json_chunk_hdr)
        f.write(json_bytes)
        f.write(bin_chunk_hdr)
        f.write(bin_chunks)

    return output_path

def export_dae(model, output_path, export_t_pose=True, animations=None, include_vertex_colors=True):
    # COLLADA 1.4 exporter
    out_lines = []
    out_lines.append('<?xml version="1.0" encoding="utf-8"?>')
    out_lines.append('<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">')
    out_lines.append('  <asset>')
    out_lines.append('    <contributor><authoring_tool>Fatal Frame PZ Viewer</authoring_tool></contributor>')
    out_lines.append('    <created>2026-09-04T00:00:00Z</created>')
    out_lines.append('    <up_axis>Y_UP</up_axis>')
    out_lines.append('  </asset>')
    out_lines.append('  <library_geometries>')

    export_meshes = _merge_meshes_by_texture(model)
    for m_idx, mesh in enumerate(export_meshes):
        geo_id = f"geom-{m_idx}"
        pos_id = f"{geo_id}-positions"
        norm_id = f"{geo_id}-normals"
        uv_id = f"{geo_id}-uvs"
        col_id = f"{geo_id}-colors"

        out_lines.append(f'    <geometry id="{geo_id}" name="{mesh.name}">')
        out_lines.append('      <mesh>')

        # Positions
        pos_str = " ".join(f"{p[0]:.4f} {p[1]:.4f} {p[2]:.4f}" for p in mesh.positions)
        out_lines.append(f'        <source id="{pos_id}">')
        out_lines.append(f'          <float_array id="{pos_id}-array" count="{len(mesh.positions)*3}">{pos_str}</float_array>')
        out_lines.append('          <technique_common>')
        out_lines.append(f'            <accessor source="#{pos_id}-array" count="{len(mesh.positions)}" stride="3">')
        out_lines.append('              <param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>')
        out_lines.append('            </accessor>')
        out_lines.append('          </technique_common>')
        out_lines.append('        </source>')

        # Normals
        norm_str = " ".join(f"{n[0]:.4f} {n[1]:.4f} {n[2]:.4f}" for n in mesh.normals)
        out_lines.append(f'        <source id="{norm_id}">')
        out_lines.append(f'          <float_array id="{norm_id}-array" count="{len(mesh.normals)*3}">{norm_str}</float_array>')
        out_lines.append('          <technique_common>')
        out_lines.append(f'            <accessor source="#{norm_id}-array" count="{len(mesh.normals)}" stride="3">')
        out_lines.append('              <param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>')
        out_lines.append('            </accessor>')
        out_lines.append('          </technique_common>')
        out_lines.append('        </source>')

        # UVs
        uv_str = " ".join(
            f"{uv[0]:.4f} {(uv[1] if getattr(model, 'uvs_are_flipped', False) else 1.0 - uv[1]):.4f}"
            for uv in mesh.uvs
        )
        out_lines.append(f'        <source id="{uv_id}">')
        out_lines.append(f'          <float_array id="{uv_id}-array" count="{len(mesh.uvs)*2}">{uv_str}</float_array>')
        out_lines.append('          <technique_common>')
        out_lines.append(f'            <accessor source="#{uv_id}-array" count="{len(mesh.uvs)}" stride="2">')
        out_lines.append('              <param name="S" type="float"/><param name="T" type="float"/>')
        out_lines.append('            </accessor>')
        out_lines.append('          </technique_common>')
        out_lines.append('        </source>')

        # Colors
        has_cols = include_vertex_colors and len(mesh.colors) == len(mesh.positions)
        if has_cols:
            col_str = " ".join(f"{c[0]:.3f} {c[1]:.3f} {c[2]:.3f} {c[3]:.3f}" for c in mesh.colors)
            out_lines.append(f'        <source id="{col_id}">')
            out_lines.append(f'          <float_array id="{col_id}-array" count="{len(mesh.colors)*4}">{col_str}</float_array>')
            out_lines.append('          <technique_common>')
            out_lines.append(f'            <accessor source="#{col_id}-array" count="{len(mesh.colors)}" stride="4">')
            out_lines.append('              <param name="R" type="float"/><param name="G" type="float"/><param name="B" type="float"/><param name="A" type="float"/>')
            out_lines.append('            </accessor>')
            out_lines.append('          </technique_common>')
            out_lines.append('        </source>')

        out_lines.append(f'        <vertices id="{geo_id}-vertices">')
        out_lines.append(f'          <input semantic="POSITION" source="#{pos_id}"/>')
        out_lines.append('        </vertices>')

        # Triangles
        tri_indices = []
        for tri in mesh.indices:
            for vi in tri:
                if has_cols:
                    tri_indices.extend([str(vi), str(vi), str(vi), str(vi)])
                else:
                    tri_indices.extend([str(vi), str(vi), str(vi)])

        p_str = " ".join(tri_indices)
        out_lines.append(f'        <triangles count="{len(mesh.indices)}">')
        out_lines.append(f'          <input semantic="VERTEX" source="#{geo_id}-vertices" offset="0"/>')
        out_lines.append(f'          <input semantic="NORMAL" source="#{norm_id}" offset="1"/>')
        out_lines.append(f'          <input semantic="TEXCOORD" source="#{uv_id}" offset="2" set="0"/>')
        if has_cols:
            out_lines.append(f'          <input semantic="COLOR" source="#{col_id}" offset="3" set="0"/>')
        out_lines.append(f'          <p>{p_str}</p>')
        out_lines.append('        </triangles>')
        out_lines.append('      </mesh>')
        out_lines.append('    </geometry>')

    out_lines.append('  </library_geometries>')
    out_lines.append('  <library_visual_scenes>')
    out_lines.append('    <visual_scene id="Scene" name="Scene">')
    for m_idx, mesh in enumerate(export_meshes):
        out_lines.append(f'      <node id="node-{m_idx}" name="{mesh.name}">')
        out_lines.append(f'        <instance_geometry url="#geom-{m_idx}"/>')
        out_lines.append('      </node>')
    out_lines.append('    </visual_scene>')
    out_lines.append('  </library_visual_scenes>')
    out_lines.append('  <scene><instance_visual_scene url="#Scene"/></scene>')
    out_lines.append('</COLLADA>')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(out_lines))

    return output_path

def export_fbx(model, output_path, export_t_pose=True, animations=None, include_vertex_colors=True):
    # ASCII FBX 7.4 exporter
    out_lines = [
        "; FBX 7.4.0 project",
        "FBXHeaderExtension: {",
        "    FBXHeaderVersion: 1003",
        "    FBXVersion: 7400",
        "}",
        "GlobalSettings: {",
        "    Version: 1000",
        "    Properties70: {",
        "        P: \"UpAxis\", \"int\", \"Integer\", \"\", 1",
        "        P: \"UpAxisSign\", \"int\", \"Integer\", \"\", 1",
        "        P: \"FrontAxis\", \"int\", \"Integer\", \"\", 2",
        "        P: \"FrontAxisSign\", \"int\", \"Integer\", \"\", 1",
        "        P: \"CoordAxis\", \"int\", \"Integer\", \"\", 0",
        "        P: \"CoordAxisSign\", \"int\", \"Integer\", \"\", 1",
        "    }",
        "}",
        "Objects: {"
    ]

    model_uid = 1000000000
    geom_uid = 2000000000

    connections = []

    export_meshes = _merge_meshes_by_texture(model)
    for m_idx, mesh in enumerate(export_meshes):
        m_id = model_uid + m_idx
        g_id = geom_uid + m_idx

        # Vertices
        v_list = []
        for p in mesh.positions:
            v_list.extend([f"{p[0]:.4f}", f"{p[1]:.4f}", f"{p[2]:.4f}"])

        # Polygon vertex indices (negative for last index of face)
        poly_indices = []
        for tri in mesh.indices:
            poly_indices.extend([str(tri[0]), str(tri[1]), str(-tri[2]-1)])

        # Normals
        n_list = []
        for n in mesh.normals:
            n_list.extend([f"{n[0]:.4f}", f"{n[1]:.4f}", f"{n[2]:.4f}"])

        # UVs
        uv_list = []
        for uv in mesh.uvs:
            v = uv[1] if getattr(model, "uvs_are_flipped", False) else 1.0 - uv[1]
            uv_list.extend([f"{uv[0]:.4f}", f"{v:.4f}"])

        out_lines.extend([
            f"    Geometry: {g_id}, \"Geometry::{mesh.name}\", \"Mesh\" {{",
            f"        Vertices: *{len(v_list)} {{",
            f"            a: {','.join(v_list)}",
            f"        }}",
            f"        PolygonVertexIndex: *{len(poly_indices)} {{",
            f"            a: {','.join(poly_indices)}",
            f"        }}",
            "        GeometryVersion: 124",
            "        LayerElementNormal: 0 {",
            "            Version: 101",
            "            Name: \"\"",
            "            MappingInformationType: \"ByVertice\"",
            "            ReferenceInformationType: \"Direct\"",
            f"            Normals: *{len(n_list)} {{",
            f"                a: {','.join(n_list)}",
            "            }",
            "        }",
            "        LayerElementUV: 0 {",
            "            Version: 101",
            "            Name: \"UVMap\"",
            "            MappingInformationType: \"ByVertice\"",
            "            ReferenceInformationType: \"Direct\"",
            f"            UV: *{len(uv_list)} {{",
            f"                a: {','.join(uv_list)}",
            "            }",
            "        }"
        ])

        if include_vertex_colors and len(mesh.colors) == len(mesh.positions):
            col_list = []
            for c in mesh.colors:
                col_list.extend([f"{c[0]:.3f}", f"{c[1]:.3f}", f"{c[2]:.3f}", f"{c[3]:.3f}"])
            out_lines.extend([
                "        LayerElementColor: 0 {",
                "            Version: 101",
                "            Name: \"Col\"",
                "            MappingInformationType: \"ByVertice\"",
                "            ReferenceInformationType: \"Direct\"",
                f"            Colors: *{len(col_list)} {{",
                f"                a: {','.join(col_list)}",
                "            }",
                "        }"
            ])

        out_lines.append("    }")

        out_lines.extend([
            f"    Model: {m_id}, \"Model::{mesh.name}\", \"Mesh\" {{",
            "        Version: 232",
            "    }"
        ])

        connections.append(f"    C: \"OO\", {g_id}, {m_id}")
        connections.append(f"    C: \"OO\", {m_id}, 0")

    out_lines.append("}")
    out_lines.append("Connections: {")
    out_lines.extend(connections)
    out_lines.append("}")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(out_lines))

    return output_path
