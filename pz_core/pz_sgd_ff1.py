"""Project Zero 1/PK2 SGD parser entry point.

FF1 and FF3 use the same recovered SGD record layout in this project, so the
legacy parser delegates to the shared implementation rather than maintaining
a second copy.  Container-specific callers are responsible for selecting
this entry point for PK2 payloads; it intentionally does not auto-detect
formats.
"""

import struct

from .pz_sgd_ff3 import (
    SGDMaterial,
    SGDMesh,
    SGDBone,
    SGDModel,
    apply_lighting_to_vertex,
    fix_colors,
    fix_uv,
    get_next_unpack,
    merge_sgd_models as _merge_sgd_models,
    normalize_mesh_normals,
    parse_lit_lights,
    parse_sgd as _parse_sgd,
    set_triangle_indices,
    transform_norm,
    transform_pos,
)


def parse_sgd(data, name="sgd", lit_data=None, external_bones=None):
    """Parse one SGD payload from a Project Zero 1/PK2 container."""
    model = _parse_sgd(
        data,
        name=name,
        lit_data=lit_data,
        external_bones=external_bones,
    )
    if model:
        # FF1 SGD UVs use the opposite vertical origin from the PNG images
        # produced by the GS deswizzler.  Preserve that fact for both the
        # viewer and exporters so the V coordinate is not inverted twice.
        model.uvs_are_flipped = True
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
    return model


def merge_sgd_models(base_model, extra_model):
    """Merge FF1 models while retaining the canonical GS TEX0 alias."""
    model = _merge_sgd_models(base_model, extra_model)
    for material in model.materials:
        material.tex0 = getattr(material, "tex0", material.tex0_low)
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
    "merge_sgd_models",
    "normalize_mesh_normals",
    "parse_lit_lights",
    "parse_sgd",
    "set_triangle_indices",
    "transform_norm",
    "transform_pos",
]
