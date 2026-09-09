import os
import sys
import json
import math
import base64
import zipfile
import struct
import re
import time
import logging
import traceback
from io import BytesIO
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Add parent directory to path so pz_core can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from pz_core.pz_pk2 import iter_embedded_tim2, unpack_pk2, unpack_room_pk2
from pz_core.pz_pk4 import (
    flip_uvs_vertical,
    iter_pk4_entries,
    parse_pk4_model,
)
from pz_core.pz_sgd_ff1 import merge_sgd_models, parse_sgd as parse_sgd_ff1
from pz_core.pz_sgd_ff3 import merge_sgd_models as merge_sgd_ff3, parse_sgd as parse_sgd_ff3
from pz_core.pz_bmd import parse_bmd_motion
from pz_core.pz_tim2_ff3 import decode_tim2, render_tim2_clut_variation
from pz_core.pz_tim2_ff1 import reconstruct_sgd_textures
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
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(
    sys.executable if getattr(sys, "frozen", False) else __file__
)))
PREFERENCES_FILE = os.path.join(APP_DIR, "PZViewer_paths.json")
os.makedirs(EXPORTS_DIR, exist_ok=True)


def load_folder_preferences():
    try:
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as preferences:
            data = json.load(preferences)
        return {
            key: str(value).replace("\\", "/")
            for key, value in data.items()
            if key in ("ff1", "ff3") and isinstance(value, str) and value.strip()
        }
    except (OSError, ValueError, TypeError):
        return {}


def save_folder_preference(game, path):
    if game not in ("ff1", "ff3"):
        return load_folder_preferences()
    paths = load_folder_preferences()
    if path and path.strip():
        paths[game] = path.strip().replace("\\", "/")
    else:
        paths.pop(game, None)
    temporary = PREFERENCES_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as preferences:
        json.dump(paths, preferences, indent=2)
        preferences.write("\n")
    os.replace(temporary, PREFERENCES_FILE)
    return paths

CURRENT_STATE = {
    "model": None,
    "textures": [],
    "animations": [],
    "collision": [],
    "source_file": "",
    "model_type": "none"
}
LOAD_CACHE = {}

_LOAD_LOGGER = logging.getLogger("pzviewer.load")
if not _LOAD_LOGGER.handlers:
    _LOAD_LOGGER.addHandler(logging.StreamHandler())
_LOAD_LOGGER.setLevel(logging.INFO)
_LOAD_LOGGER.propagate = False


class LoadProgress:
    """Immediate, compact progress output for one viewer file load."""

    def __init__(self, file_path):
        self.file_path = os.path.basename(os.path.normpath(file_path))
        self.started = time.perf_counter()

    def log(self, phase, message=""):
        elapsed_ms = (time.perf_counter() - self.started) * 1000.0
        suffix = f" {message}" if message else ""
        _LOAD_LOGGER.info(
            "[load] file=%s phase=%s elapsed_ms=%.1f%s",
            self.file_path, phase, elapsed_ms, suffix
        )

    def error(self, phase, exc):
        self.log(
            phase,
            f"error={type(exc).__name__}: {exc} "
            f"traceback={traceback.format_exc().splitlines()[-1] if traceback.format_exc() else 'n/a'}"
        )

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
                "height": h,
                "has_alpha": (
                    "A" in img.getbands()
                    and img.getchannel("A").getextrema()[0] == 0
                    and img.getchannel("A").getextrema()[1] > 0
                )
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
    position_values = []
    texture_dimensions = {
        index: list(img.size)
        for index, img in enumerate(textures or [])
        if img is not None and hasattr(img, "size")
    }
    foliage_diagnostics = []

    for m in model.meshes:
        total_verts += len(m.positions)
        total_tris += len(m.indices)
        position_values.extend(
            value for point in m.positions for value in point
            if isinstance(value, (int, float)) and math.isfinite(value)
        )
        tex_id = getattr(m, "texture_index_override", -1)
        if tex_id < 0 and 0 <= m.material_index < len(materials_data):
            tex_id = materials_data[m.material_index]["texture_index"]
        if m.name in {"mesh_b111_t0x12", "mesh_b112_t0x12"}:
            mat = (
                model.materials[m.material_index]
                if 0 <= m.material_index < len(model.materials)
                else None
            )
            uv_values_for_mesh = [
                value
                for uv in getattr(m, "uvs", [])
                for value in uv
                if isinstance(value, (int, float)) and math.isfinite(value)
            ]
            foliage_diagnostics.append({
                "mesh": m.name,
                "material_index": m.material_index,
                "material_name": getattr(mat, "name", None),
                "tbp0": getattr(mat, "tbp0", None),
                "tex0_low": getattr(mat, "tex0_low", None),
                "texture_index": tex_id,
                "texture_dimensions": texture_dimensions.get(tex_id),
                "vertex_count": len(m.positions),
                "triangle_count": len(m.indices),
                "uv_min": min(uv_values_for_mesh) if uv_values_for_mesh else None,
                "uv_max": max(uv_values_for_mesh) if uv_values_for_mesh else None,
                "uv_samples": [list(uv) for uv in getattr(m, "uvs", [])[:6]],
                "named_uv_flip_applied": bool(
                    getattr(m, "_named_uv_flip_applied", False)
                ),
            })

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

    uv_values = [
        uv for mesh in getattr(model, 'meshes', [])
        for uv_pair in getattr(mesh, 'uvs', [])
        for uv in uv_pair
        if isinstance(uv, (int, float))
    ]
    texture_mapped = sum(
        1 for mat in getattr(model, 'materials', [])
        if getattr(mat, 'texture_index', -1) >= 0
    )

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

    diagnostics = {
        "materials_mapped": texture_mapped,
        "materials_total": len(getattr(model, 'materials', [])),
        "uv_min": min(uv_values) if uv_values else 0.0,
        "uv_max": max(uv_values) if uv_values else 0.0,
        "position_min": min(position_values) if position_values else 0.0,
        "position_max": max(position_values) if position_values else 0.0,
        "position_values": len(position_values)
    }
    if hasattr(model, "texture_debug"):
        diagnostics["texture_debug"] = model.texture_debug
    diagnostics["foliage_meshes"] = foliage_diagnostics

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
        },
        "diagnostics": diagnostics
    }


def apply_named_foliage_uv_corrections(model):
    """Apply the narrow PS2 foliage correction after every parse/merge path."""
    target_names = frozenset({"mesh_b111_t0x12", "mesh_b112_t0x12"})
    for mesh in getattr(model, "meshes", []):
        if mesh.name in target_names and not getattr(mesh, "_named_uv_flip_applied", False):
            mesh.uvs = [[u, 1.0 - v] for u, v in mesh.uvs]
            mesh._named_uv_flip_applied = True
    return model

def find_textures_for_model(file_path, model, progress=None):
    if progress:
        progress.log("material_texture_mapping", "status=start")
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    dir_path = os.path.dirname(os.path.abspath(file_path))

    candidate_dirs = [
        os.path.join(dir_path, base_name),
        dir_path,
        os.path.join(os.path.dirname(dir_path), "room", base_name),
    ]
    # Linked PK2 room extraction keeps SGD files in a directory named
    # <room>_pk2_linked; texture payloads, when extracted, live beside it or
    # in the corresponding original room directory.
    if "_pk2_linked" in base_name.lower():
        room_stem = base_name[:base_name.lower().index("_pk2_linked")]
        candidate_dirs.extend((
            os.path.join(dir_path, room_stem),
            os.path.join(dir_path, room_stem + "_pk2"),
        ))

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
    found_images_by_resource_index = {}
    base_timgs_by_tbp0 = {}
    tbp0_resource_index = {}
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
    elif os.path.splitext(file_path)[1].lower() == '.pk2':
        archive_paths.append(file_path)

    texture_files = set()
    for cdir in candidate_dirs:
        if os.path.exists(cdir) and os.path.isdir(cdir):
            for root, _, files in os.walk(cdir):
                for fname in sorted(files):
                    fpath = os.path.join(root, fname)
                    if fpath in texture_files:
                        continue
                    texture_files.add(fpath)
                    if not os.path.isfile(fpath):
                        continue
                    ext = os.path.splitext(fname)[1].lower()
                    stem = os.path.splitext(fname)[0].lower()
                    if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.tga'):
                        try:
                            img = Image.open(fpath)
                            found_images_by_name[stem] = img
                            # TPK extraction tools emit tex/<index>.png beside
                            # the matching 00_tm2/<index>.tm2 resource.
                            if os.path.basename(root).lower() == "tex" and stem.isdigit():
                                found_images_by_resource_index[int(stem)] = img
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
                                                    if stem.isdigit():
                                                        tbp0_resource_index.setdefault(tbp0, int(stem))
                                                    texture_variants_by_tbp0.setdefault(tbp0, []).append(img)
                                                found_images_by_name[stem] = img
                        except Exception:
                            pass

    for archive_path in archive_paths:
        try:
            if archive_path.lower().endswith(".pk4"):
                entries = (
                    entry for entry in iter_pk4_entries(archive_path)
                    if entry["type"] in ("tm2", "tim2")
                )
            else:
                entries = None
            if archive_path.lower().endswith(".pk2"):
                timgs_with_entries = (
                    (timg, timg.get("_pk2_entry_index", -1))
                    for timg in iter_embedded_tim2(archive_path)
                )
                for timg, entry_index in timgs_with_entries:
                    if timg.get("is_clut_only"):
                        clut_only_entries.append((
                            f"{os.path.splitext(os.path.basename(archive_path))[0]}_{entry_index}",
                            timg.get("gs_tex0", 0) & 0x3FFF,
                            timg,
                        ))
                        continue
                    img = timg.get("image")
                    if not img:
                        continue
                    tbp0 = timg.get("gs_tex0", 0) & 0x3FFF
                    stem = f"{os.path.splitext(os.path.basename(archive_path))[0]}_{entry_index}"
                    if tbp0 >= 0:
                        found_images_by_tbp0[tbp0] = img
                        base_timgs_by_tbp0[tbp0] = timg
                        if str(entry_index).isdigit():
                            tbp0_resource_index.setdefault(tbp0, int(entry_index))
                        texture_variants_by_tbp0.setdefault(tbp0, []).append(img)
                    found_images_by_name[stem.lower()] = img
            else:
                for entry in entries:
                    for timg in decode_tim2(entry["data"]):
                        img = timg.get("image")
                        if not img:
                            continue
                        tbp0 = timg.get("gs_tex0", 0) & 0x3FFF
                        stem = f"{os.path.splitext(os.path.basename(archive_path))[0]}_{entry['index']}"
                        if tbp0 >= 0:
                            found_images_by_tbp0[tbp0] = img
                            base_timgs_by_tbp0[tbp0] = timg
                            if str(entry["index"]).isdigit():
                                tbp0_resource_index.setdefault(tbp0, int(entry["index"]))
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

    if progress:
        progress.log(
            "material_texture_mapping",
            f"files={len(texture_files)} tbp0_candidates={len(found_images_by_tbp0)} "
            f"name_candidates={len(found_images_by_name)}"
        )
    if not found_images_by_tbp0 and not found_images_by_name:
        if progress:
            progress.log("material_texture_mapping", "mapped=0 textures=0")
        return []

    for mat in getattr(model, 'materials', []):
        mat.texture_index = -1

    textures = []
    def has_meaningful_alpha(img):
        if not img or "A" not in img.getbands():
            return False
        alpha_min, alpha_max = img.getchannel("A").getextrema()
        return alpha_min < 255 and alpha_max > 0

    # Match by hardware TBP0 first. This is exact and avoids assigning a
    # same-named texture from another resource to a mesh (notably rre02's
    # kaidan1 panel behind the denwa material).
    for mat in getattr(model, 'materials', []):
        mat_name_clean = mat.name.lower().replace('-', '_').replace(' ', '_').strip()
        mat_stem = os.path.splitext(mat_name_clean)[0]
        tbp0 = getattr(mat, 'tbp0', 0)
        if tbp0 >= 0 and tbp0 in found_images_by_tbp0:
            variants = texture_variants_by_tbp0.get(tbp0, [])
            use_variant = '_cl' in mat_stem and len(variants) > 1
            resource_img = found_images_by_resource_index.get(tbp0_resource_index.get(tbp0))
            decoded_img = found_images_by_tbp0[tbp0]
            # Extracted indexed PNGs can flatten a TIM2 alpha channel. Keep
            # the decoded resource when it is the only alpha-bearing version.
            if use_variant:
                img = variants[1]
            elif resource_img and has_meaningful_alpha(decoded_img) and not has_meaningful_alpha(resource_img):
                img = decoded_img
            else:
                img = resource_img or decoded_img
            if img not in textures:
                textures.append(img)
            mat.texture_index = textures.index(img)

    # Match remaining materials by name/stem.
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

    # mesh_b91_t0x32 is a small ita1-cl00 submesh that shares material 45
    # with other geometry. Its UVs target the original 0011.tm2 image, not
    # the CLUT variation selected for the shared material.
    mesh_overrides = []
    target_tbp0 = 11348
    target_img = found_images_by_resource_index.get(11)
    if target_img is None and tbp0_resource_index.get(target_tbp0) == 11:
        target_img = base_timgs_by_tbp0.get(target_tbp0, {}).get("image")
    if target_img is not None and tbp0_resource_index.get(target_tbp0) == 11:
        if target_img not in textures:
            textures.append(target_img)
        target_texture_index = textures.index(target_img)
        for mesh in getattr(model, "meshes", []):
            if (
                mesh.name == "mesh_b91_t0x32"
                and mesh.indices
                and 0 <= mesh.material_index < len(model.materials)
                and model.materials[mesh.material_index].tbp0 == target_tbp0
            ):
                mesh.texture_index_override = target_texture_index
                mesh_overrides.append({
                    "mesh": mesh.name,
                    "material_index": mesh.material_index,
                    "tbp0": target_tbp0,
                    "resource_index": 11,
                    "texture_index": target_texture_index,
                })

    if progress:
        progress.log(
            "material_texture_mapping",
            f"mapped={sum(1 for m in getattr(model, 'materials', []) if m.texture_index >= 0)} "
            f"materials={len(getattr(model, 'materials', []))} textures={len(textures)}"
        )
    # Keep the hardware-address mapping available in the serialized response.
    # This makes a missing TPK resource distinguishable from a viewer-side
    # assignment problem without changing the texture selection behavior.
    model.texture_debug = {
        "candidate_tbp0": sorted(found_images_by_tbp0),
        "candidate_names": sorted(found_images_by_name),
        "resource_tbp0": {
            str(tbp0): tbp0_resource_index[tbp0]
            for tbp0 in sorted(tbp0_resource_index)
        },
        "unmapped_materials": [
            {
                "index": mat.index,
                "name": mat.name,
                "tbp0": mat.tbp0,
                "tex0_low": mat.tex0_low,
            }
            for mat in getattr(model, 'materials', [])
            if mat.texture_index < 0
        ],
        "mesh_overrides": mesh_overrides,
    }
    return textures

def handle_load_file(file_path):
    progress = LoadProgress(file_path)
    progress.log("request_path_validation", f"path={os.path.abspath(file_path)}")
    if not os.path.exists(file_path):
        progress.log("request_path_validation", "status=missing")
        return {"error": f"File not found: {file_path}"}
    progress.log("request_path_validation", "status=ok")
    cache_key = os.path.normcase(os.path.abspath(file_path))
    try:
        cache_stamp = (os.path.getmtime(file_path), os.path.getsize(file_path))
    except OSError:
        cache_stamp = None
    cached = LOAD_CACHE.get(cache_key)
    if cached and cached[0] == cache_stamp:
        cached_response = dict(cached[1])
        cached_response["diagnostics"] = dict(cached[1].get("diagnostics", {}))
        cached_response["diagnostics"]["cache"] = "hit"
        progress.log("response_completion", "cache=hit")
        return cached_response
    load_started = time.perf_counter()

    source_dir = file_path if os.path.isdir(file_path) else None
    if os.path.isdir(file_path):
        # Support loading model package directory (e.g. 00_sgd containing 0000.sgd)
        entries = sorted(os.listdir(file_path))
        sgd_files = [f for f in entries if f.lower().endswith('.sgd')]
        pk_files = [f for f in entries if f.lower().endswith(('.pk2', '.pk4'))]
        if sgd_files:
            file_path = os.path.join(file_path, sgd_files[0])
        elif pk_files:
            file_path = os.path.join(file_path, pk_files[0])
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
        progress.log(
            "request_path_validation",
            f"directory_selected={os.path.basename(file_path)}"
        )

    ext = os.path.splitext(file_path)[1].lower()
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    progress.log("file_type_detection", f"extension={ext or '<none>'} base={base_name}")

    model = None
    textures = []
    animations = []
    collision = []
    model_type = "model"
    sgd_parse_metrics = {
        "sgd_processed": 0,
        "sgd_omitted": 0,
        "sgd_format": "",
        "unpack_records": 0,
        "unpack_skipped_invalid": 0,
        "unpack_skipped_oversized": 0,
        "unpack_skipped_budget": 0,
        "unpack_vertices": 0,
    }

    if ext == '.pk2':
        entries = unpack_pk2(file_path)
        progress.log("pk2_extraction", f"entries={len(entries)}")
        if not entries:
            progress.log("pk2_extraction", "status=error entries=0")
            return {"error": f"PK2 does not contain a room SGD: {file_path}"}
        lit_path = os.path.splitext(file_path)[0] + ".lit"
        lit_data = open(lit_path, "rb").read() if os.path.exists(lit_path) else None
        parsed_entries = 0
        skipped_entries = []
        # Keep TEX0 descriptions per PK2 entry.  Reconstructing after merging
        # all entries makes auxiliary materials query unrelated VRAM, while
        # reconstructing only the first entry loses late panel textures.
        entry_materials = {}
        for index, entry in enumerate(entries):
            try:
                part = parse_sgd_ff1(
                    entry["data"],
                    name=f"{base_name}_{index:04d}",
                    lit_data=lit_data,
                )
            except (ValueError, IndexError, struct.error) as exc:
                progress.error("sgd_parse", exc)
                skipped_entries.append({
                    "index": index,
                    "type": entry.get("type", ""),
                    "reason": f"{type(exc).__name__}: {exc}"
                })
                continue
            if not part or not part.meshes:
                skipped_entries.append({
                    "index": index,
                    "type": entry.get("type", ""),
                    "reason": "no renderable meshes"
                })
                continue
            parsed_entries += 1
            entry_materials[entry["index"]] = list(part.materials)
            progress.log(
                "sgd_parse",
                f"entry={index} type={entry.get('type', '') or 'unknown'} "
                f"meshes={len(part.meshes)} materials={len(getattr(part, 'materials', []))}"
            )
            if model is None:
                model = part
            else:
                merge_sgd_models(model, part)
                progress.log(
                    "geometry_merge",
                    f"entry={index} meshes={len(model.meshes)}"
                )
        if model:
            progress.log(
                "geometry_merge",
                f"status=complete entries_parsed={parsed_entries} meshes={len(model.meshes)}"
            )
            # PK2 room textures are authoritative GS uploads. Do not let
            # adjacent name-matched files override a material before the
            # TEX0/TBP0 reconstruction below (notably 01_05_kabeS).
            textures = []
            for material in model.materials:
                material.texture_index = -1
            # FF1 rooms carry raw GS uploads in TRI2 blocks, not TIM2 files.
            # Reconstruct each SGD independently and match only exact TBP0.
            gs_texture_debug = []
            # The first GS upload stream initializes the room VRAM.  Include
            # valid TEX0 descriptions from later PK2 parts when querying that
            # VRAM; those parts contain the panel materials but no duplicate
            # upload stream of their own.
            gs_reconstruction_materials = [
                material
                for materials in entry_materials.values()
                for material in materials
                if 1 << (((getattr(material, "tex0", 0) or 0) >> 30) & 0xF) >= 16
            ]
            for entry in entries:
                try:
                    entry_debug = {"entry_index": entry["index"]}
                    reconstruction_materials = entry_materials.get(entry["index"], [])
                    if entry["index"] == min(entry_materials):
                        reconstruction_materials = gs_reconstruction_materials
                    images, _uploads = reconstruct_sgd_textures(
                        entry["data"],
                        reconstruction_materials,
                        diagnostics=entry_debug
                    )
                    progress.log(
                        "gs_vram_texture_reconstruction",
                        f"entry={entry['index']} uploads={len(_uploads)} "
                        f"detected={len(entry_debug.get('detected_uploads', []))} "
                        f"images={len(images)} tbp0={entry_debug.get('decoded_tbp0', [])}"
                    )
                    gs_texture_debug.append(entry_debug)
                    if _uploads:
                        for mat in model.materials:
                            img = images.get(getattr(mat, "tbp0", -1))
                            if img is not None:
                                # PIL image equality compares pixels, so two
                                # distinct GS textures with identical/flat
                                # pixels can collapse to the wrong index.
                                texture_index = next(
                                    (index for index, existing in enumerate(textures)
                                     if existing is img),
                                    -1,
                                )
                                if texture_index < 0:
                                    textures.append(img)
                                    texture_index = len(textures) - 1
                                mat.texture_index = texture_index
                except (ValueError, struct.error) as exc:
                    progress.error("gs_vram_texture_reconstruction", exc)
                    continue
            model.texture_debug = getattr(model, "texture_debug", {})
            model.texture_debug["ff1_headerless_gs"] = gs_texture_debug
            model_type = "room"
            progress.log("gs_vram_texture_reconstruction", f"textures={len(textures)}")

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
            flip_uv=False
        )
        progress.log(
            "pk4_extraction",
            f"status=complete meshes={len(getattr(model, 'meshes', [])) if model else 0}"
        )
        if model:
            # Keep the selected archive name for the export folder, even when
            # the first SGD inside the archive uses a numeric display name.
            model.name = base_name
            textures = find_textures_for_model(file_path, model, progress)
            model_type = "room" if is_room_path else (
                "character" if "character" in file_path.lower() else "prop"
            )
            # FF3 room images need the WebGL-origin inversion, while FF3
            # character atlases already match the parsed character UVs.
            model.uvs_are_flipped = model_type != "character"
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
        path_lower = os.path.normcase(os.path.abspath(file_path))
        with open(file_path, 'rb') as f:
            base_data = f.read()
        lit_data = None
        if source_dir:
            linked_name = os.path.basename(source_dir)
            room_stem = linked_name.split("_pk2_linked", 1)[0]
            room_dir = os.path.dirname(source_dir)
            lit_candidates = [
                os.path.join(room_dir, room_stem + ".lit"),
                os.path.join(os.path.dirname(room_dir), "room", room_stem + ".lit"),
            ]
            for lit_path in lit_candidates:
                if os.path.exists(lit_path):
                    with open(lit_path, "rb") as lf:
                        lit_data = lf.read()
                    break
        # Standalone SGD files are FF3 resources by default.  FF1 SGDs only
        # reach this branch when opened from the generated PK2-linked folder.
        use_ff1_parser = "_pk2_linked" in path_lower
        parse_sgd = parse_sgd_ff1 if use_ff1_parser else parse_sgd_ff3
        merge_sgd = merge_sgd_models if use_ff1_parser else merge_sgd_ff3
        model = parse_sgd(base_data, name=base_name, lit_data=lit_data)
        sgd_parse_metrics["sgd_processed"] += 1
        for key, value in getattr(model, "parse_diagnostics", {}).items():
            if key in sgd_parse_metrics:
                if key == "sgd_format":
                    sgd_parse_metrics[key] = value
                else:
                    sgd_parse_metrics[key] += value
        progress.log(
            "sgd_parse",
            f"entry=base meshes={len(getattr(model, 'meshes', []))} "
            f"materials={len(getattr(model, 'materials', []))}"
        )
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
        if not use_ff1_parser:
            model.uvs_are_flipped = model_type != "character"

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
                            lit_data=lit_data,
                            external_bones=model.bones if model.bones else None,
                        )
                        sgd_parse_metrics["sgd_processed"] += 1
                        for key, value in getattr(sib_model, "parse_diagnostics", {}).items():
                            if key in sgd_parse_metrics:
                                sgd_parse_metrics[key] += value
                        if sib_model and sib_model.meshes:
                            merge_sgd(model, sib_model)
                            merged_count += 1
                    except Exception as e:
                        sgd_parse_metrics["sgd_omitted"] += 1
                        progress.error("geometry_merge", e)

                if merged_count > 0:
                    model.name = sgd_stem  # keep original stem as display name
                    model_type = "room" if is_room_sgd else "character"
                    progress.log(
                        "geometry_merge",
                        f"merged_siblings={merged_count} meshes={len(model.meshes)}"
                    )
        # ────────────────────────────────────────────────────────────────────────

        textures = find_textures_for_model(file_path, model, progress)

    elif ext == '.bmd':
        clip = parse_bmd_motion(
            file_path,
            name=os.path.splitext(os.path.basename(file_path))[0]
        )
        animations = [clip] if clip else []
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

    # Apply after sibling/PK4 merging so extracted SGD loads receive the same
    # narrow correction as direct PK4 loads.
    apply_named_foliage_uv_corrections(model)

    # The export folder must follow the selected file, not an internal SGD
    # name such as 0000.
    model.export_name = os.path.splitext(os.path.basename(file_path))[0]
    CURRENT_STATE["model"] = model
    CURRENT_STATE["textures"] = textures
    CURRENT_STATE["animations"] = animations
    CURRENT_STATE["collision"] = collision
    CURRENT_STATE["source_file"] = file_path
    CURRENT_STATE["model_type"] = model_type

    progress.log(
        "serialization",
        f"meshes={len(getattr(model, 'meshes', []))} materials={len(getattr(model, 'materials', []))} "
        f"textures={len(textures)} animations={len(animations)}"
    )
    response = serialize_model(model, textures, animations, collision, model_type)
    if ext == '.pk2':
        response["diagnostics"].update({
            "pk2_entries": len(entries),
            "pk2_parsed": parsed_entries,
            "pk2_skipped": len(skipped_entries),
            "pk2_skipped_entries": skipped_entries,
        })
    if ext == '.sgd':
        response["diagnostics"].update(sgd_parse_metrics)
    response["diagnostics"]["load_ms"] = round((time.perf_counter() - load_started) * 1000.0, 1)
    response["diagnostics"]["cache"] = "miss"
    LOAD_CACHE[cache_key] = (cache_stamp, response)
    # Keep memory bounded while retaining the common repeat-load fast path.
    if len(LOAD_CACHE) > 4:
        LOAD_CACHE.pop(next(iter(LOAD_CACHE)))
    progress.log(
        "response_completion",
        f"status=ok model_type={model_type} meshes={len(model.meshes)} "
        f"textures={len(textures)} bytes={len(json.dumps(response))}"
    )
    return response

class PZViewerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/preferences':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(load_folder_preferences()).encode('utf-8'))
            return

        if parsed.path == '/api/browse':
            qs = parse_qs(parsed.query)
            target_dir = qs.get('dir', [''])[0].strip()
            game = qs.get('game', ['all'])[0].lower()
            file_extensions = {
                'ff1': ('.pk2', '.sgd', '.tim2'),
                'ff3': ('.pk4', '.sgd', '.tm2'),
                'all': ('.pk2', '.pk4', '.sgd', '.bmd', '.cld', '.obj', '.tm2', '.tim2', '.png'),
            }.get(game, ('.pk2', '.pk4', '.sgd', '.bmd', '.cld', '.obj', '.tm2', '.tim2', '.png'))
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
                    if is_dir or ext in file_extensions:
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
            game = qs.get('game', ['all'])[0].lower()
            picker_titles = {
                'ff1': 'Select Fatal Frame 1 Files',
                'ff3': 'Select Fatal Frame 3 Files',
            }
            chosen = ""
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                chosen = filedialog.askdirectory(
                    initialdir=init_dir or None,
                    title=picker_titles.get(game, "Select Fatal Frame / Project Zero Folder")
                )
                root.destroy()
            except Exception as e:
                pass

            resp = {"status": "ok", "chosen": chosen.replace('\\', '/') if chosen else ""}
            if resp["chosen"] and game in ("ff1", "ff3"):
                try:
                    save_folder_preference(game, resp["chosen"])
                except OSError as exc:
                    _LOAD_LOGGER.warning("Could not save folder preference: %s", exc)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode('utf-8'))
            return

        elif parsed.path == '/api/load':
            qs = parse_qs(parsed.query)
            file_path = qs.get('path', [''])[0]
            try:
                res = handle_load_file(file_path)
                status_code = 400 if "error" in res else 200
            except (OSError, ValueError, struct.error, RuntimeError) as exc:
                progress = LoadProgress(file_path)
                progress.error("error", exc)
                res = {"error": f"Failed to load asset: {exc}"}
                status_code = 500
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
                if os.path.splitext(file_path)[1].lower() != '.bmd':
                    raise ValueError("Only Project Zero 3 BMD animations are supported")
                clip = parse_bmd_motion(
                    file_path,
                    name=os.path.splitext(os.path.basename(file_path))[0]
                )
                animations = [clip] if clip else []
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

        if parsed.path == '/api/preferences':
            req = json.loads(post_data.decode('utf-8')) if post_data else {}
            game = req.get("game", "")
            path = req.get("path", "")
            try:
                preferences = save_folder_preference(game, path)
                status_code = 200
            except (OSError, ValueError, TypeError) as exc:
                preferences = {"error": str(exc)}
                status_code = 500
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(preferences).encode('utf-8'))
            return

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
