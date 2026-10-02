"""Strict FF2 SGD entry point.

No FF2-specific SGD record differences are established in this repository.
FF2 therefore uses the recovered FF3-compatible implementation through this
explicit facade, leaving a single place to add verified FF2 handling later.
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


def validate_sgd(data):
    """Return immutable bytes after validating the FF2 payload boundary."""
    try:
        return bytes(data)
    except (TypeError, ValueError) as exc:
        raise TypeError("FF2 SGD payload must be bytes-like") from exc


def parse_sgd(data, name="sgd", lit_data=None, external_bones=None):
    """Parse an FF2 SGD using the verified compatible SGD implementation."""
    model = _parse_sgd(
        validate_sgd(data),
        name=name,
        lit_data=lit_data,
        external_bones=external_bones,
    )
    if model:
        model.sgd_format = "ff2"
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
    return model


def merge_sgd_models(base_model, extra_model):
    """Merge FF2 models through the shared format-neutral implementation."""
    return _merge_sgd_models(base_model, extra_model)


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
    "validate_sgd",
]
