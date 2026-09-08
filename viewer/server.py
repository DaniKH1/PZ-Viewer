import os
import sys
import json
import base64
import zipfile
import struct
import re
from io import BytesIO
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Add parent directory to path so pz_core can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from pz_core.pz_pk2_pz1 import unpack_room_pk2_pz1
from pz_core.pz_pk4 import flip_uvs_vertical, iter_pk4_entries, parse_pk4_model
from pz_core.pz_sgd import parse_sgd, merge_sgd_models
from pz_core.pz_mdl import parse_mdl
from pz_core.pz_anm import parse_anm
from pz_core.pz_bmd import parse_bmd_motion
from pz_core.pz_tim2 import decode_tim2, render_tim2_clut_variation
from pz_core.pz_collision import (
    parse_room_collision_from_map,
    parse_all_rooms_collision_from_map,
    collision_to_sgd_model,
    parse_cld,
    parse_cld_folder
)
from pz_core.pz_export import export_glb, export_obj, export_dae, export_fbx, export_textures_png

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
EXPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)

CURRENT_STATE = {
    "model": None,
    "textures": [],
    "animations": [],
    "collision": [],
    "source_file": "",
    "model_type": "none"
}

def find_matching_bmd_animations(model_path):
    """Load FF3 motion clips whose character prefix matches a PK4 model."""
    model_text = os.path.abspath(model_path).lower()
    match = re.search(r"([a-z]+\d+)_pk4", model_text)
    if not match:
        model_stem = os.path.splitext(os.path.basename(model_path))[0].lower()
        match = re.match(r"([a-z]+\d+)", model_stem)
    if not match:
        return []
    prefix = match.group(1)
    model_abs = os.path.abspath(model_path)
    parts = model_abs.split(os.sep)
    try:
        data_index = next(i for i, part in enumerate(parts) if part.lower() == "3ddata")
    except StopIteration:
        return []
    data_root = os.sep.join(parts[:data_index + 1])
    motion_root = os.path.join(data_root, "character", "motion")
    if not os.path.isdir(motion_root):
        return []

    clips = []
    motion_dirs = sorted(
        os.path.join(motion_root, entry)
        for entry in os.listdir(motion_root)
        if entry.lower().startswith(prefix)
        and os.path.isdir(os.path.join(motion_root, entry))
    )
    default_dirs = [
        path for path in motion_dirs
        if "_default_" in os.path.basename(path).lower()
    ]
    if default_dirs:
        motion_dirs = default_dirs
    for motion_dir in motion_dirs:
        for bmd_path in sorted(
            os.path.join(root, filename)
            for root, _, files in os.walk(motion_dir)
            for filename in files
            if filename.lower().endswith(".bmd")
        ):
            clip = parse_bmd_motion(
                bmd_path,
                name=f"{os.path.basename(motion_dir)}/{os.path.splitext(os.path.basename(bmd_path))[0]}"
            )
            if clip:
                clips.append(clip)
    return clips

def serialize_model(model, textures=None, animations=None, collision_meshes=None, model_type="model"):
    tex_list = []
    if textures:
        for img in textures:
            buf = BytesIO()
            img.save(buf, format='PNG')
            w, h = img.size
            tex_list.append({
                "data_uri": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode('ascii'),
                "width": w,
                "height": h
            })

    materials_data = []
    for mat in getattr(model, 'materials', []):
        materials_data.append({
            "index": mat.index,
            "name": mat.name,
            "ambient": mat.ambient,
            "diffuse": mat.diffuse,
            "specular": mat.specular,
            "texture_index": mat.texture_index
        })

    meshes_data = []
    total_verts = 0
    total_tris = 0

    for m in model.meshes:
        total_verts += len(m.positions)
        total_tris += len(m.indices)
        tex_id = -1
        if 0 <= m.material_index < len(materials_data):
            tex_id = materials_data[m.material_index]["texture_index"]

        meshes_data.append({
            "name": m.name,
            "material_index": m.material_index,
            "tex_id": tex_id,
            "bone_index": m.bone_index,
            "positions": [coord for p in m.positions for coord in p],
            "normals": [coord for n in m.normals for coord in n],
            "uvs": [coord for uv in m.uvs for coord in uv],
            "colors": [c for col in m.colors for c in (col[:3] if len(col) >= 3 else [1.0, 1.0, 1.0])],
            "indices": [idx for tri in m.indices for idx in tri]
        })

    bones_data = []
    for b in getattr(model, 'bones', []):
        pos = [b.matrix[12], b.matrix[13], b.matrix[14]] if len(b.matrix) >= 15 and any(b.matrix[12:15]) else b.trans
        bones_data.append({
            "id": b.index,
            "index": b.index,
            "parent": b.parent,
            "matrix": b.matrix,
            "pos": pos,
            "rot": b.rot[:3] if len(b.rot) >= 3 else [0.0, 0.0, 0.0]
        })

    all_col = (collision_meshes or []) + getattr(model, 'collision_meshes', [])
    col_polygons = []
    col_spheres = []
    col_boxes = []

    for c in all_col:
        c_type = getattr(c, 'type', 'poly')
        if c_type == 'sphere':
            # Extract sphere center
            cx = sum(p[0] for p in c.positions) / max(1, len(c.positions))
            cy = sum(p[1] for p in c.positions) / max(1, len(c.positions))
            cz = sum(p[2] for p in c.positions) / max(1, len(c.positions))
            radius = 25.0
            if c.positions:
                p0 = c.positions[0]
                radius = ((p0[0]-cx)**2 + (p0[1]-cy)**2 + (p0[2]-cz)**2)**0.5
            col_spheres.append({"center": [cx, cy, cz], "radius": radius})
        elif c_type in ('bbox', 'box'):
            min_p = [min(p[i] for p in c.positions) for i in range(3)] if c.positions else [-10,-10,-10]
            max_p = [max(p[i] for p in c.positions) for i in range(3)] if c.positions else [10,10,10]
            col_boxes.append({"min": min_p, "max": max_p})
        else:
            for tri in c.indices:
                if len(tri) >= 3 and tri[0] < len(c.positions) and tri[1] < len(c.positions) and tri[2] < len(c.positions):
                    col_polygons.append([c.positions[tri[0]], c.positions[tri[1]], c.positions[tri[2]]])

    collision_struct = {
        "polygons": col_polygons,
        "spheres": col_spheres,
        "boxes": col_boxes
    }

    anims_data = []
    if animations:
        for c in animations:
            anims_data.append({
                "name": c.name,
                "bone_num": c.bone_num,
                "num_frames": c.frame_num,
                "frame_num": c.frame_num,
                "fps": c.fps,
                "parent_ids": c.parent_ids,
                "frames": c.frames
            })

    return {
        "filename": getattr(model, 'export_name', getattr(model, 'name', 'model')),
        "name": getattr(model, 'name', 'model'),
        "type": model_type,
        "model_type": model_type,
        "meshes": meshes_data,
        "materials": materials_data,
        "bones": bones_data,
        "collision": collision_struct,
        "textures": tex_list,
        "uvs_flipped": bool(getattr(model, "uvs_are_flipped", False)),
        "animations": anims_data,
        "stats": {
            "vertices": total_verts,
            "triangles": total_tris,
            "submeshes": len(meshes_data),
            "bones": len(bones_data),
            "textures": len(tex_list),
            "animations": len(anims_data)
        }
    }

def find_textures_for_model(file_path, model):
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    dir_path = os.path.dirname(os.path.abspath(file_path))

    candidate_dirs = [
        os.path.join(dir_path, base_name),
        dir_path,
        os.path.join(os.path.dirname(dir_path), "room", base_name),
    ]

    # Fatal Frame 1: door -> room guess
    if base_name.startswith('d') and len(base_name) >= 4 and base_name[1:4].isdigit():
        r_guess = f"r{base_name[1:4]}"
        room_dir = os.path.join(os.path.dirname(dir_path), "room")
        if os.path.exists(room_dir):
            for d in os.listdir(room_dir):
                if d.startswith(r_guess) and os.path.isdir(os.path.join(room_dir, d)):
                    candidate_dirs.append(os.path.join(room_dir, d))

    # Fatal Frame 3: mpk -> tpk discovery (model packages map to sibling texture packages)
    curr = os.path.abspath(file_path)
    for _ in range(5):
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent
        if os.path.isdir(curr):
            for item in os.listdir(curr):
                if 'tpk' in item.lower():
                    tpk_full = os.path.join(curr, item)
                    for root, subdirs, files in os.walk(tpk_full):
                        if any(f.lower().endswith(('.tm2', '.tim2', '.png')) for f in files) or 'tm2' in os.path.basename(root).lower():
                            if root not in candidate_dirs:
                                candidate_dirs.append(root)

    found_images_by_tbp0 = {}
    texture_variants_by_tbp0 = {}
    found_images_by_name = {}
    base_timgs_by_tbp0 = {}
    clut_only_entries = []
    archive_paths = []
    if os.path.splitext(file_path)[1].lower() == '.pk4':
        archive_dir = os.path.dirname(os.path.abspath(file_path))
        archive_paths.append(file_path)
        archive_paths.extend(
            os.path.join(archive_dir, fname)
            for fname in os.listdir(archive_dir)
            if fname.lower().endswith('.pk4') and 'tpk' in fname.lower()
        )

    for cdir in candidate_dirs:
        if os.path.exists(cdir) and os.path.isdir(cdir):
            for fname in sorted(os.listdir(cdir)):
                fpath = os.path.join(cdir, fname)
                if not os.path.isfile(fpath):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                stem = os.path.splitext(fname)[0].lower()
                if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.tga'):
                    try:
                        img = Image.open(fpath)
                        found_images_by_name[stem] = img
                    except Exception:
                        pass
                elif ext in ('.tm2', '.tim2'):
                    try:
                        with open(fpath, 'rb') as tf:
                            timgs = decode_tim2(tf.read())
                            if timgs:
                                for timg in timgs:
                                    tex0 = timg.get('gs_tex0', 0)
                                    tbp0 = tex0 & 0x3FFF
                                    if timg.get('is_clut_only'):
                                        clut_only_entries.append((stem, tbp0, timg))
                                    else:
                                        img = timg.get('image')
                                        if img:
                                            if tbp0 >= 0:
                                                found_images_by_tbp0[tbp0] = img
                                                base_timgs_by_tbp0[tbp0] = timg
                                                texture_variants_by_tbp0.setdefault(tbp0, []).append(img)
                                            found_images_by_name[stem] = img
                    except Exception:
                        pass

    for archive_path in archive_paths:
        try:
            for entry in iter_pk4_entries(archive_path):
                if entry["type"] not in ("tm2", "tim2"):
                    continue
                timgs = decode_tim2(entry["data"])
                for timg in timgs or []:
                    img = timg.get("image")
                    if not img:
                        continue
                    tbp0 = timg.get("gs_tex0", 0) & 0x3FFF
                    stem = f"{os.path.splitext(os.path.basename(archive_path))[0]}_{entry['index']}"
                    if tbp0 >= 0:
                        found_images_by_tbp0[tbp0] = img
                        base_timgs_by_tbp0[tbp0] = timg
                        texture_variants_by_tbp0.setdefault(tbp0, []).append(img)
                    found_images_by_name[stem.lower()] = img
        except (OSError, ValueError, struct.error):
            raise

    # Render palette variations from CLUT-only files
    for stem, tbp0, clut_timg in clut_only_entries:
        base_timg = base_timgs_by_tbp0.get(tbp0)
        if base_timg:
            var_img = render_tim2_clut_variation(base_timg, clut_timg)
            if var_img:
                found_images_by_name[stem] = var_img
                texture_variants_by_tbp0.setdefault(tbp0, []).append(var_img)

    if not found_images_by_tbp0 and not found_images_by_name:
        return []

    for mat in getattr(model, 'materials', []):
        mat.texture_index = -1

    textures = []
    # 1. Match specific variant names (e.g. tukue1_cl00, akari1_cl00)
    for mat in getattr(model, 'materials', []):
        mat_name_clean = mat.name.lower().replace('-', '_').replace(' ', '_').strip()
        mat_stem = os.path.splitext(mat_name_clean)[0]
        if mat_stem in found_images_by_name:
            img = found_images_by_name[mat_stem]
            if img not in textures:
                textures.append(img)
            mat.texture_index = textures.index(img)

    # 2. Match remaining by hardware TBP0 register (PS2 Texture Base Pointer, 100% exact)
    for mat in getattr(model, 'materials', []):
        if mat.texture_index >= 0:
            continue
        mat_name_clean = mat.name.lower().replace('-', '_').replace(' ', '_').strip()
        mat_stem = os.path.splitext(mat_name_clean)[0]
        tbp0 = getattr(mat, 'tbp0', 0)
        if tbp0 >= 0 and tbp0 in found_images_by_tbp0:
            variants = texture_variants_by_tbp0.get(tbp0, [])
            use_variant = '_cl' in mat_stem and len(variants) > 1
            img = variants[1] if use_variant else found_images_by_tbp0[tbp0]
            if img not in textures:
                textures.append(img)
            mat.texture_index = textures.index(img)

    # 3. Match remaining materials by name/stem
    for mat in getattr(model, 'materials', []):
        if mat.texture_index >= 0:
            continue
        mat_name_clean = mat.name.lower().replace('-', '_').replace(' ', '_').strip()
        mat_stem = os.path.splitext(mat_name_clean)[0]
        for stem, img in found_images_by_name.items():
            stem_clean = stem.replace('-', '_').replace(' ', '_')
            if mat_stem and (mat_stem in stem_clean or stem_clean in mat_stem):
                if img not in textures:
                    textures.append(img)
                mat.texture_index = textures.index(img)
                break

    # 4. Fallback: single image
    if len(getattr(model, 'materials', [])) == 1 and len(found_images_by_name) == 1 and not textures:
        img = list(found_images_by_name.values())[0]
        textures.append(img)
        model.materials[0].texture_index = 0

    return textures

def handle_load_file(file_path):
    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}"}

    if os.path.isdir(file_path):
        # Support loading model package directory (e.g. 00_sgd containing 0000.sgd)
        entries = sorted(os.listdir(file_path))
        sgd_files = [f for f in entries if f.lower().endswith('.sgd')]
        pk_files = [f for f in entries if f.lower().endswith(('.pk2', '.pk4'))]
        mdl_files = [f for f in entries if f.lower().endswith('.mdl')]
        if sgd_files:
            file_path = os.path.join(file_path, sgd_files[0])
        elif pk_files:
            file_path = os.path.join(file_path, pk_files[0])
        elif mdl_files:
            file_path = os.path.join(file_path, mdl_files[0])
        else:
            nested_sgds = []
            for root, _, files in os.walk(file_path):
                nested_sgds.extend(
                    os.path.join(root, f)
                    for f in files
                    if f.lower().endswith(".sgd")
                )
            if nested_sgds:
                file_path = sorted(nested_sgds)[0]

    ext = os.path.splitext(file_path)[1].lower()
    base_name = os.path.splitext(os.path.basename(file_path))[0]

    model = None
    textures = []
    animations = []
    collision = []
    model_type = "model"

    if ext == '.pk2':
        room = unpack_room_pk2_pz1(file_path)
        if room['near_sgd']:
            lit_path = os.path.splitext(file_path)[0] + '.lit'
            lit_data = None
            if os.path.exists(lit_path):
                with open(lit_path, 'rb') as lf:
                    lit_data = lf.read()
            try:
                model = parse_sgd(room['near_sgd'], name=base_name, lit_data=lit_data)
            except struct.error:
                model = None
            if not model:
                return {"error": f"PK2 does not contain a valid room SGD: {file_path}"}
            textures = find_textures_for_model(file_path, model)
            path_lower = os.path.normcase(os.path.abspath(file_path))
            is_pz1_room = "\\room\\" in path_lower or "/room/" in path_lower
            model_type = "room" if is_pz1_room else (
                "character" if "\\character\\" in path_lower or "/character/" in path_lower
                else "prop"
            )

            # Auto-check for room collision in map_data/msnXXmap.obj.
            if is_pz1_room:
                try:
                    room_num_str = base_name[1:4] # '000'
                    room_idx = int(room_num_str)
                    map_dir = os.path.join(os.path.dirname(os.path.dirname(file_path)), "map_data")
                    if not os.path.exists(map_dir):
                        map_dir = "f:/Project Zero Modding/Obscura/bin/map_data"
                    if os.path.exists(map_dir):
                        for msn_idx in range(5):
                            map_file = os.path.join(map_dir, f"msn0{msn_idx}map.obj")
                            if os.path.exists(map_file):
                                with open(map_file, 'rb') as mf:
                                    col_meshes = parse_room_collision_from_map(mf.read(), room_idx=room_idx)
                                    if col_meshes:
                                        collision.extend(col_meshes)
                                        break
                except Exception:
                    pass

    elif ext == '.pk4':
        normalized_path = os.path.normcase(os.path.abspath(file_path))
        is_room_path = "\\room\\" in normalized_path
        is_object_path = "\\object\\" in normalized_path
        is_furniture_path = "\\furniture\\" in normalized_path
        is_accessory_path = "\\accessory\\" in normalized_path
        is_fly_path = "\\fly\\" in normalized_path
        model = parse_pk4_model(
            file_path,
            name=base_name,
            flip_uv=(
                is_room_path
                or is_object_path
                or is_furniture_path
                or is_accessory_path
                or is_fly_path
            )
        )
        if model:
            # Keep the selected archive name for the export folder, even when
            # the first SGD inside the archive uses a numeric display name.
            model.name = base_name
            textures = find_textures_for_model(file_path, model)
            model_type = "room" if is_room_path else (
                "character" if "character" in file_path.lower() else "prop"
            )
            if model_type == "character":
                # Load one representative clip automatically. Loading all
                # 255 BMDs at once creates a very large JSON response and
                # prevents the animation panel from rendering. Individual
                # BMDs can still be loaded through "Load animation...".
                animations = find_matching_bmd_animations(file_path)[:1]
            if is_room_path:
                cld_candidates = [
                    os.path.join(os.path.dirname(file_path), "02_cld"),
                    os.path.join(os.path.splitext(file_path)[0], "02_cld"),
                    os.path.join(os.path.dirname(file_path), f"{base_name}_pk4", "02_cld"),
                    os.path.join(os.path.dirname(os.path.dirname(file_path)), base_name, "02_cld"),
                ]
                for cld_dir in cld_candidates:
                    collision = parse_cld_folder(cld_dir, name=f"{base_name}_collision")
                    if collision:
                        break

    elif ext == '.sgd':
        with open(file_path, 'rb') as f:
            base_data = f.read()
        model = parse_sgd(base_data, name=base_name)
        path_lower = os.path.normcase(os.path.abspath(file_path))
        is_room_sgd = "\\room\\" in path_lower or "/room/" in path_lower
        is_object_sgd = "\\object\\" in path_lower or "/object/" in path_lower
        is_furniture_sgd = "\\furniture\\" in path_lower or "/furniture/" in path_lower
        is_accessory_sgd = "\\accessory\\" in path_lower or "/accessory/" in path_lower
        is_fly_sgd = "\\fly\\" in path_lower or "/fly/" in path_lower
        model_type = "room" if is_room_sgd else "prop"
        if not is_room_sgd and "character" in path_lower:
            model_type = "character"
            animations = find_matching_bmd_animations(file_path)[:1]
        if (
            is_room_sgd
            or is_object_sgd
            or is_furniture_sgd
            or is_accessory_sgd
            or is_fly_sgd
        ):
            flip_uvs_vertical(model)

        # ── Auto-merge sibling numbered SGDs (e.g. 0000–0015 for one character) ──
        sgd_dir    = os.path.dirname(file_path)
        sgd_stem   = os.path.splitext(os.path.basename(file_path))[0]

        if sgd_stem.isdigit():
            if not is_room_sgd and int(sgd_stem) == 15:
                candidates = sorted(
                    f for f in os.listdir(sgd_dir)
                    if f.lower().endswith('.sgd')
                    and os.path.splitext(f)[0].isdigit()
                    and (is_room_sgd or int(os.path.splitext(f)[0]) not in ({14, 15} if not any(
                        os.path.splitext(x)[0] == "15" for x in os.listdir(sgd_dir)
                    ) else {15}))
                )
                if candidates:
                    file_path = os.path.join(sgd_dir, candidates[0])
                    sgd_stem = os.path.splitext(candidates[0])[0]
                    with open(file_path, 'rb') as f:
                        model = parse_sgd(f.read(), name=sgd_stem)

            # Collect all sibling numbered SGDs sorted, excluding the file we just loaded
            norm_loaded = os.path.normcase(os.path.abspath(file_path))
            all_numbered = sorted(
                f for f in os.listdir(sgd_dir)
                if f.lower().endswith('.sgd')
                and os.path.splitext(f)[0].isdigit()
                and (is_room_sgd or int(os.path.splitext(f)[0]) != 15)
                and os.path.normcase(os.path.join(sgd_dir, f)) != norm_loaded
            )

            if all_numbered:
                # Ensure we have bones — if not, look for them in a sibling first
                if not model.bones:
                    for sib in all_numbered:
                        try:
                            with open(os.path.join(sgd_dir, sib), 'rb') as sf:
                                candidate = parse_sgd(sf.read(), name=sib)
                            if candidate and candidate.bones:
                                model.bones = candidate.bones
                                break
                        except Exception:
                            pass

                # Merge every other numbered SGD into the base model
                merged_count = 0
                for sib_name in all_numbered:
                    sib_path = os.path.join(sgd_dir, sib_name)
                    try:
                        with open(sib_path, 'rb') as sf:
                            sib_data = sf.read()
                        sib_model = parse_sgd(
                            sib_data,
                            name=os.path.splitext(sib_name)[0],
                            external_bones=model.bones if model.bones else None
                        )
                        if sib_model and sib_model.meshes:
                            merge_sgd_models(model, sib_model)
                            merged_count += 1
                    except Exception as e:
                        print(f"[merge] Warning: could not merge {sib_name}: {e}")

                if merged_count > 0:
                    model.name = sgd_stem  # keep original stem as display name
                    model_type = "room" if is_room_sgd else "character"
                    print(f"[merge] Merged {merged_count} sibling SGDs into '{sgd_stem}'")
        # ────────────────────────────────────────────────────────────────────────

        textures = find_textures_for_model(file_path, model)

    elif ext == '.mdl':
        model = parse_mdl(file_path, name=base_name)
        if model:
            textures = model.textures
            collision = list(model.collision_meshes)
            model_type = "character"
            # Auto-check if a matching .anm exists
            anm_guess = file_path.replace('/mdl/', '/anm/').replace('\\mdl\\', '\\anm\\').replace('.mdl', '.anm')
            if os.path.exists(anm_guess):
                try:
                    animations = parse_anm(anm_guess)
                except Exception:
                    pass

    elif ext in ('.anm', '.bmd'):
        if ext == '.bmd':
            clip = parse_bmd_motion(
                file_path,
                name=os.path.splitext(os.path.basename(file_path))[0]
            )
            animations = [clip] if clip else []
        else:
            if os.path.splitext(file_path)[1].lower() == '.bmd':
                clip = parse_bmd_motion(
                    file_path,
                    name=os.path.splitext(os.path.basename(file_path))[0]
                )
                animations = [clip] if clip else []
            else:
                animations = parse_anm(file_path)
        if CURRENT_STATE["model"]:
            CURRENT_STATE["animations"] = animations
            return {"status": "ok", "animations": [
                {
                    "name": c.name,
                    "bone_num": c.bone_num,
                    "num_frames": c.frame_num,
                    "fps": c.fps,
                    "frames": c.frames
                } for c in animations
            ]}
        else:
            return {"error": "Please load a character before loading an animation!"}

    elif ext == '.obj' and os.path.basename(file_path).lower().startswith('msn'):
        with open(file_path, 'rb') as f:
            col_meshes = parse_all_rooms_collision_from_map(f.read())
        if col_meshes:
            collision = col_meshes
            model = collision_to_sgd_model(col_meshes, name=base_name)
            model_type = "map_collision"
        else:
            return {"error": f"No collision data found in {file_path}"}

    elif ext == '.cld':
        with open(file_path, 'rb') as f:
            collision = parse_cld(f.read(), name=base_name)
        if collision:
            model = collision_to_sgd_model(collision, name=base_name)
            model_type = "collision"
        else:
            return {"error": f"Failed to parse collision data from {file_path}"}

    if not model:
        return {"error": f"Failed to parse model from {file_path}"}

    # The export folder must follow the selected file, not an internal SGD
    # name such as 0000.
    model.export_name = os.path.splitext(os.path.basename(file_path))[0]
    CURRENT_STATE["model"] = model
    CURRENT_STATE["textures"] = textures
    CURRENT_STATE["animations"] = animations
    CURRENT_STATE["collision"] = collision
    CURRENT_STATE["source_file"] = file_path
    CURRENT_STATE["model_type"] = model_type

    return serialize_model(model, textures, animations, collision, model_type)

class PZViewerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/browse':
            qs = parse_qs(parsed.query)
            target_dir = qs.get('dir', [''])[0].strip()
            if not target_dir:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Choose a folder first"}).encode('utf-8'))
                return
            if not os.path.exists(target_dir):
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Folder not found"}).encode('utf-8'))
                return
            
            parent_dir = os.path.dirname(os.path.abspath(target_dir)).replace('\\', '/')
            items = []
            try:
                all_entries = sorted(os.listdir(target_dir))

                # Detect folders that contain a multi-part numbered SGD pack
                # (e.g. 0000.sgd … 0015.sgd).  Only show the anchor (lowest-numbered)
                # and hide the rest so the browser doesn't show 16 entries per character.
                numbered_sgds = [
                    e for e in all_entries
                    if e.lower().endswith('.sgd') and os.path.splitext(e)[0].isdigit()
                ]
                hidden_numbered = set()
                if len(numbered_sgds) > 1:
                    anchor = numbered_sgds[0]         # e.g. "0000.sgd"
                    hidden_numbered = set(numbered_sgds[1:])  # hide the rest

                for entry in all_entries:
                    if entry in hidden_numbered:
                        continue
                    full_p = os.path.join(target_dir, entry)
                    is_dir = os.path.isdir(full_p)
                    ext = os.path.splitext(entry)[1].lower()
                    if is_dir or ext in ('.pk2', '.pk4', '.sgd', '.mdl', '.anm', '.bmd', '.cld', '.obj', '.lit', '.tm2', '.png'):
                        t_str = "dir" if is_dir else ext.lstrip('.')
                        # Mark the anchor of a multi-part pack specially
                        if entry in numbered_sgds and hidden_numbered:
                            t_str = "sgd_pack"
                        items.append({
                            "name": entry if not hidden_numbered or entry not in numbered_sgds
                                    else f"{entry}  (+{len(hidden_numbered)} parts)",
                            "path": full_p.replace('\\', '/'),
                            "type": t_str,
                            "is_dir": is_dir,
                            "size": os.path.getsize(full_p) if not is_dir else 0
                        })
            except Exception as e:
                pass

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "current_dir": target_dir.replace('\\', '/'),
                "parent_dir": parent_dir,
                "items": items
            }).encode('utf-8'))
            return

        elif parsed.path == '/api/choose_folder':
            qs = parse_qs(parsed.query)
            init_dir = qs.get('dir', [''])[0]
            chosen = ""
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                chosen = filedialog.askdirectory(initialdir=init_dir or None, title="Select Fatal Frame / Project Zero Folder")
                root.destroy()
            except Exception as e:
                pass

            resp = {"status": "ok", "chosen": chosen.replace('\\', '/') if chosen else ""}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode('utf-8'))
            return

        elif parsed.path == '/api/load':
            qs = parse_qs(parsed.query)
            file_path = qs.get('path', [''])[0]
            res = handle_load_file(file_path)
            status_code = 400 if "error" in res else 200
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(res).encode('utf-8'))
            return

        elif parsed.path == '/api/load_anim':
            qs = parse_qs(parsed.query)
            file_path = qs.get('path', [''])[0]
            if not os.path.exists(file_path):
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Animation file not found"}).encode('utf-8'))
                return
            try:
                animations = parse_anm(file_path)
                CURRENT_STATE["animations"] = animations
                resp = {
                    "status": "ok",
                    "animations": [
                        {
                            "name": c.name,
                            "bone_num": c.bone_num,
                            "num_frames": c.frame_num,
                            "fps": c.fps,
                            "frames": c.frames
                        } for c in animations
                    ]
                }
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        elif parsed.path == '/api/export_textures':
            model = CURRENT_STATE.get("model")
            textures = CURRENT_STATE.get("textures", [])
            if not model or not textures:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No textures loaded for current model."}).encode('utf-8'))
                return

            source_file = CURRENT_STATE.get("source_file", "")
            base_name = os.path.splitext(os.path.basename(source_file))[0] or getattr(model, 'export_name', getattr(model, 'name', 'model'))
            tex_subfolder = os.path.join(EXPORTS_DIR, f"{base_name}_textures")
            saved = export_textures_png(model, textures, tex_subfolder)

            zip_buf = BytesIO()
            with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for t_name, t_path, idx in saved:
                    zf.write(t_path, arcname=t_name)

            zip_bytes = zip_buf.getvalue()
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', f'attachment; filename="{base_name}_textures.zip"')
            self.send_header('Content-Length', str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        if parsed.path == '/api/load':
            req = json.loads(post_data.decode('utf-8')) if post_data else {}
            file_path = req.get('path', '')
            res = handle_load_file(file_path)
            status_code = 400 if "error" in res else 200
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(res).encode('utf-8'))
            return

        elif parsed.path == '/api/detach_anim':
            CURRENT_STATE["animations"] = []
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "message": "Animation detached"}).encode('utf-8'))
            return

        elif parsed.path in ('/api/export', '/api/export_collision'):
            req = json.loads(post_data.decode('utf-8')) if post_data else {}
            fmt = req.get('format', 'glb').lower()
            target = req.get('target', 'model').lower()
            if parsed.path == '/api/export_collision':
                target = 'collision'

            # ----------------------------------------------------
            # Collision-Only Export
            # ----------------------------------------------------
            if target == 'collision':
                collision = CURRENT_STATE.get("collision", [])
                base_model = CURRENT_STATE.get("model")
                base_name = getattr(base_model, 'name', 'model') if base_model else 'map'

                if not collision:
                    # Auto-check if source file was a room and collision can be found
                    src = CURRENT_STATE.get("source_file", "")
                    bname = os.path.splitext(os.path.basename(src))[0]
                    if bname.startswith('r') and len(bname) >= 4 and bname[1:4].isdigit():
                        try:
                            ridx = int(bname[1:4])
                            map_dir = "f:/Project Zero Modding/Obscura/bin/map_data"
                            for msn_idx in range(5):
                                map_file = os.path.join(map_dir, f"msn0{msn_idx}map.obj")
                                if os.path.exists(map_file):
                                    with open(map_file, 'rb') as mf:
                                        col_meshes = parse_room_collision_from_map(mf.read(), room_idx=ridx)
                                        if col_meshes:
                                            collision = col_meshes
                                            CURRENT_STATE["collision"] = collision
                                            break
                        except Exception:
                            pass

                if not collision:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "No 3D collision geometry available to export for this asset."}).encode('utf-8'))
                    return

                col_model = collision_to_sgd_model(collision, name=f"{base_name}_collision")
                export_filename = f"{base_name}_collision.{fmt}"
                out_path = os.path.join(EXPORTS_DIR, export_filename)

                try:
                    if fmt in ('glb', 'gltf'):
                        export_glb(col_model, out_path, export_t_pose=True, include_vertex_colors=True, include_armature=False)
                    elif fmt == 'obj':
                        export_obj(col_model, out_path, include_vertex_colors=True)
                    elif fmt == 'dae':
                        export_dae(col_model, out_path, export_t_pose=True, include_vertex_colors=True)
                    elif fmt == 'fbx':
                        export_fbx(col_model, out_path, export_t_pose=True, include_vertex_colors=True)
                    else:
                        raise ValueError(f"Unsupported collision export format: {fmt}")

                    with open(out_path, 'rb') as f:
                        file_bytes = f.read()

                    content_type = 'model/gltf-binary' if fmt == 'glb' else 'application/octet-stream'
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Disposition', f'attachment; filename="{export_filename}"')
                    self.send_header('Content-Length', str(len(file_bytes)))
                    self.end_headers()
                    self.wfile.write(file_bytes)
                    return

                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"Collision export failed: {str(e)}"}).encode('utf-8'))
                    return

            # ----------------------------------------------------
            # Standard 3D Model Export
            # ----------------------------------------------------
            include_colors = req.get('include_colors', True)
            include_textures = req.get('include_textures', True)
            include_collision = req.get('include_collision', True)
            tpose_only = req.get('tpose_only', True)

            model = CURRENT_STATE["model"]
            if not model:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No model loaded"}).encode('utf-8'))
                return

            source_file = CURRENT_STATE.get("source_file", "")
            base_name = os.path.splitext(os.path.basename(source_file))[0]
            if not base_name:
                base_name = getattr(model, 'export_name', '') or getattr(model, 'name', 'model')
            base_name = os.path.basename(base_name) or 'model'
            selected_destination = req.get('destination') or ''
            if selected_destination:
                asset_export_dir = os.path.abspath(os.path.expanduser(selected_destination))
            else:
                asset_export_dir = os.path.join(EXPORTS_DIR, base_name)
            os.makedirs(asset_export_dir, exist_ok=True)
            export_filename = f"{base_name}.{fmt}"
            out_path = os.path.join(asset_export_dir, export_filename)

            textures = CURRENT_STATE["textures"] if include_textures else []
            animations = None if tpose_only else CURRENT_STATE["animations"]

            try:
                # Always extract and convert textures to PNG in exports folder
                tex_subfolder = os.path.join(asset_export_dir, f"{base_name}_textures")
                saved_tex = []
                if textures and include_textures:
                    saved_tex = export_textures_png(model, textures, tex_subfolder)

                # Check if zip bundle requested or textures only
                if fmt in ('zip', 'glb_zip', 'obj_zip') or target == 'textures':
                    zip_filename = f"{base_name}_with_textures.zip" if target != 'textures' else f"{base_name}_textures.zip"
                    zip_path = os.path.join(asset_export_dir, zip_filename)
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                        if target != 'textures':
                            if fmt == 'obj_zip':
                                obj_path = os.path.join(asset_export_dir, f"{base_name}.obj")
                                export_obj(model, obj_path, include_vertex_colors=include_colors, textures=textures)
                                zf.write(obj_path, arcname=f"{base_name}.obj")
                                mtl_path = os.path.join(asset_export_dir, f"{base_name}.mtl")
                                if os.path.exists(mtl_path):
                                    zf.write(mtl_path, arcname=f"{base_name}.mtl")
                            else:
                                glb_path = os.path.join(asset_export_dir, f"{base_name}.glb")
                                export_glb(model, glb_path, export_t_pose=tpose_only,
                                           animations=animations,
                                           include_vertex_colors=include_colors,
                                           textures=textures,
                                           include_armature=(CURRENT_STATE.get("model_type") != "room"))
                                zf.write(glb_path, arcname=f"{base_name}.glb")

                        # Add all texture PNGs
                        for t_name, t_path, idx in saved_tex:
                            zf.write(t_path, arcname=f"textures/{t_name}")

                    with open(zip_path, 'rb') as f:
                        file_bytes = f.read()

                    self.send_response(200)
                    self.send_header('Content-Type', 'application/zip')
                    self.send_header('Content-Disposition', f'attachment; filename="{zip_filename}"')
                    self.send_header('Content-Length', str(len(file_bytes)))
                    self.end_headers()
                    self.wfile.write(file_bytes)
                    return

                if fmt in ('glb', 'gltf'):
                    export_glb(model, out_path, export_t_pose=tpose_only,
                               animations=animations,
                               include_vertex_colors=include_colors,
                               textures=textures,
                               include_armature=(CURRENT_STATE.get("model_type") != "room"))
                elif fmt == 'obj':
                    export_obj(model, out_path,
                               include_vertex_colors=include_colors,
                               textures=textures)
                elif fmt == 'dae':
                    export_dae(model, out_path, export_t_pose=tpose_only,
                               animations=animations,
                               include_vertex_colors=include_colors)
                elif fmt == 'fbx':
                    export_fbx(model, out_path, export_t_pose=tpose_only,
                               animations=animations,
                               include_vertex_colors=include_colors)
                else:
                    raise ValueError(f"Unsupported export format: {fmt}")

                # Read the exported file and send back directly for download
                with open(out_path, 'rb') as f:
                    file_bytes = f.read()

                content_type = 'model/gltf-binary' if fmt == 'glb' else 'application/octet-stream'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Disposition', f'attachment; filename="{export_filename}"')
                self.send_header('Content-Length', str(len(file_bytes)))
                self.end_headers()
                self.wfile.write(file_bytes)
                return

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Export failed: {str(e)}"}).encode('utf-8'))
                return

        self.send_error(404)

def run_server(port=8088):
    server = HTTPServer(('127.0.0.1', port), PZViewerHandler)
    print(f"PZ Viewer Server running on http://127.0.0.1:{port}")
    server.serve_forever()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
    run_server(port)
