"""Project Zero 1/PK2 SGD parser entry point.

FF1 and FF3 use the same recovered SGD record layout in this project, so the
legacy parser delegates to the shared implementation rather than maintaining
a second copy.  Container-specific callers are responsible for selecting
this entry point for PK2 payloads; it intentionally does not auto-detect
formats.
"""

import os
import re
import struct

from pz_core.common.model import (
    SGDMaterial,
    SGDMesh,
    SGDBone,
    SGDModel,
    transform_norm,
    transform_pos,
)
from pz_core.ff3.pz_sgd_ff3 import (
    apply_lighting_to_vertex,
    fix_colors,
    fix_uv,
    get_next_unpack,
    merge_sgd_models as _merge_sgd_models,
    normalize_mesh_normals,
    parse_sgd as _parse_sgd,
    set_triangle_indices,
)

from pz_core.ff1.pz_lighting_ff1 import (parse_lit_lights, apply_ff1_lighting,
                              merge_lighting_diagnostics)


def parse_sgd(data, name="sgd", lit_data=None, external_bones=None):
    """Parse one SGD payload from a Project Zero 1/PK2 container."""
    model = _parse_sgd(
        data,
        name=name,
        lit_data=None,  # FF1 category-11 lighting is applied below, not FF3.
        external_bones=external_bones,
        # FF1 SGDCOORDINATE rows carry a uniform ~1.919 scale that Obscura
        # removes via StripCoordinateScale (Obscura-ff1
        # ModelConverter/game/Model.cpp:33) before using the matrix as a world
        # transform.  FF3 matrices are already unit length, so this stays
        # opt-in and the FF2/FF3 entry points are unaffected.
        strip_bone_scale=True,
    )
    if model:
        model.sgd_format = "ff1"
        # FF1 SGD UVs use the opposite vertical origin from the PNG images
        # produced by the GS deswizzler.  Preserve that fact for both the
        # viewer and exporters so the V coordinate is not inverted twice.
        model.uvs_are_flipped = True
        try:
            from pz_core.ff1.pz_gs_ff1 import iter_process_units
            mesh_types = [data[offset + 13] for _, offset, _, category
                          in iter_process_units(data) if category == 1]
            # The shared parser already flips V in compact 0x00/0x02 meshes;
            # preset environment commands retain native V. Keep mixed/other
            # layouts on the existing convention instead of guessing per name.
            if mesh_types and all(t in (0x00, 0x02) for t in mesh_types):
                model.uvs_are_flipped = False
        except ValueError:
            pass
        # FF1 stores the GS TEX0 word in the legacy parser's tex0_low field.
        # Keep the canonical alias local to the PK2 path so GS reconstruction
        # can derive PSM, CLUT, TBP0, and texture dimensions.
        if len(data) >= 24:
            _ver, _mapflag, _kind, mats, _coordp, matp, _phead, _blocks = struct.unpack(
                "<IBBHIIII", data[:24]
            )
        else:
            mats, matp = 0, 0
        for index, material in enumerate(model.materials):
            tex0 = material.tex0_low
            if matp > 0 and index < mats:
                raw_offset = matp + index * 176 + 112
                if raw_offset + 8 <= len(data):
                    tex0 = struct.unpack_from("<Q", data, raw_offset)[0]
            material.tex0 = tex0
            material.tex0_low = tex0 & 0xFFFFFFFF
            material.tbp0 = tex0 & 0x3FFF
        # FF1 room records with type 0x30/0x10 are auxiliary VIF packets.
        # The shared parser's environment branch can expose them as tiny
        # triangle strips; they are not render geometry and create the
        # stretched/deformed lines seen in the room viewer.
        model.meshes = [
            mesh for mesh in model.meshes
            if not mesh.name.lower().endswith(("t0x30", "t0x10"))
        ]
        apply_ff1_lighting(model, data, lit_data)
    return model


def is_ff1_sgd(data, path=""):
    """Recognize standalone FF1 item SGDs without affecting FF3 dispatch."""
    normalized_path = str(path).replace("\\", "/").lower()
    basename = os.path.basename(normalized_path)
    embedded_ff1_tri2 = False
    if len(data) >= 24 and data[:4] == b"\x50\x10\x00\x00":
        try:
            from pz_core.ff1.pz_gs_ff1 import iter_process_units
            categories = {unit[3] for unit in iter_process_units(data)}
            embedded_ff1_tri2 = 10 in categories and 13 in categories
        except ValueError:
            pass
    return (
        embedded_ff1_tri2
        or "/item/" in normalized_path
        or "/furniture/" in normalized_path
        or "/door/" in normalized_path
        or "_pk2_linked" in normalized_path
        or re.search(r"(?:^|[-_])i\d{3}(?:_|$)", basename) is not None
    )


def merge_sgd_models(base_model, extra_model):
    """Merge FF1 models while retaining the canonical GS TEX0 alias."""
    first_new_material = len(base_model.materials)
    source_materials = list(extra_model.materials)
    model = _merge_sgd_models(base_model, extra_model)
    # The shared merger copies the low word only. Preserve FF1's complete
    # TEX0 (including CBP/CSA) when a room's later panel SGDs are merged.
    for material, source in zip(model.materials[first_new_material:], source_materials):
        material.tex0 = getattr(source, "tex0", source.tex0_low)
    merge_lighting_diagnostics(model, extra_model)
    return model


__all__ = [
    "SGDMaterial",
    "SGDMesh",
    "SGDBone",
    "SGDModel",
    "apply_lighting_to_vertex",
    "fix_colors",
    "fix_uv",
    "get_next_unpack",
    "is_ff1_sgd",
    "merge_sgd_models",
    "normalize_mesh_normals",
    "parse_lit_lights",
    "parse_sgd",
    "set_triangle_indices",
    "transform_norm",
    "transform_pos",
]
