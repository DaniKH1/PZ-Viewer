"""FF2 SGD geometry with verified texture identities and stored vertex colours.

The legacy geometry decoder remains unchanged. This facade validates the
FF2 boundary, restores complete TEX0 metadata and replaces only the colour
attributes of verified preset VIF streams. See the FF2 documentation.
"""

import copy
import struct

from pz_core.ff2.pz_gs_ff2 import FF2FormatError, need, sgd_chains
from pz_core.ff2.pz_vertex_ff2 import apply_stored_vertex_colors, merge_color_diagnostics

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
    normalize_mesh_normals,
    parse_lit_lights,
    parse_sgd as _parse_sgd,
    set_triangle_indices,
)


def validate_sgd(data):
    """Validate bounded chains before invoking the compatible geometry decoder."""
    if isinstance(data, (int, str)):
        raise TypeError("FF2 SGD payload must be bytes-like")
    try:
        payload = bytes(data)
    except (TypeError, ValueError) as exc:
        raise TypeError("FF2 SGD payload must be bytes-like") from exc
    # Exhaust all chains now: malformed pointers must not reach the old reader.
    list(sgd_chains(payload))
    _, _, _, count, coordp, matp, phead, blocks = struct.unpack_from('<IBBHIIII', payload)
    if count:
        if not matp:
            raise FF2FormatError('FF2 materials at 0x0: nonzero count with null pointer')
        need(payload, matp, count * 176, 'material table')
    return payload


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
        apply_stored_vertex_colors(model, bytes(data))
    return model


def merge_sgd_models(base_model, extra_model):
    """Preserve full TEX0/CLUT and colour provenance when merging room parts."""
    offset = len(base_model.materials)
    for material in extra_model.materials:
        clone = copy.copy(material)
        clone.index = material.index + offset
        base_model.materials.append(clone)
    for mesh in extra_model.meshes:
        mesh.material_index += offset
        base_model.meshes.append(mesh)
    merge_color_diagnostics(base_model, extra_model)
    return base_model



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
