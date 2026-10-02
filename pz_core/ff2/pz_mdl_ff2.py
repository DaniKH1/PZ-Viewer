"""Fatal Frame 2 PS2 MDL package parser.

The character-package layout follows Obscura's Model::ExtractModel,
ReadTextures, and ReadSGD flow, while geometry and TIM2 decoding stay on the
dedicated FF2 parser paths.
"""

import os
import struct
from dataclasses import dataclass

from pz_core.ff2.pz_pk2_ff2 import unpack_pk2
from pz_core.ff2.pz_sgd_ff2 import merge_sgd_models, parse_sgd
from pz_core.ff2.pz_tim2_ff2 import decode_tim2


@dataclass
class FF2MDLResult:
    model: object
    textures: list
    diagnostics: dict


class FF2MDLError(ValueError):
    """Raised when an FF2 MDL package is malformed or unsupported."""


def _read_data(data_or_path):
    if isinstance(data_or_path, (str, os.PathLike)):
        with open(data_or_path, "rb") as stream:
            return stream.read()
    try:
        return bytes(data_or_path)
    except (TypeError, ValueError) as exc:
        raise FF2MDLError("FF2 MDL source must be a path or bytes-like data") from exc


def _is_sgd(entry):
    data = entry.get("data", b"")
    return len(data) >= 24 and struct.unpack_from("<I", data)[0] == 0x1050


def parse_ff2_mdl(data_or_path, name="mdl"):
    """Parse an FF2 character MDL's model and texture package entries."""
    data = _read_data(data_or_path)
    source = os.fspath(data_or_path) if isinstance(
        data_or_path, (str, os.PathLike)
    ) else name
    outer_entries = unpack_pk2(data)
    if len(outer_entries) < 2:
        raise FF2MDLError(
            f"{source}: FF2 character MDL must contain a model pack and texture pack"
        )

    model_entries = unpack_pk2(outer_entries[0]["data"])
    if not model_entries or not _is_sgd(model_entries[0]):
        raise FF2MDLError(
            f"{source}: first outer PK2 entry is not an FF2 SGD model pack"
        )

    texture_entries = unpack_pk2(outer_entries[1]["data"])
    if not texture_entries or not texture_entries[0]["data"].startswith(b"TIM2"):
        raise FF2MDLError(
            f"{source}: second outer PK2 entry is not an FF2 TIM2 texture pack"
        )

    diagnostics = {
        "format": "ff2_mdl",
        "layout": "obscura_character_package",
        "outer_entries": len(outer_entries),
        "model_entries": len(model_entries),
        "model_entries_parsed": 0,
        "model_entries_shared_skeleton": 0,
        "model_entries_omitted": [],
        "texture_entries": len(texture_entries),
        "texture_entries_decoded": 0,
        "texture_entries_omitted": [],
        "materials_mapped": 0,
        "textures_output": 0,
    }

    model = None
    shared_bones = None
    for index, entry in enumerate(model_entries):
        if not _is_sgd(entry):
            diagnostics["model_entries_omitted"].append({
                "index": index,
                "reason": "entry is not an FF2 0x1050 SGD",
            })
            continue
        payload = entry["data"]
        coordinate_offset = struct.unpack_from("<I", payload, 8)[0]
        external_bones = (
            shared_bones
            if coordinate_offset == 0 and shared_bones
            else None
        )
        try:
            part = parse_sgd(
                payload,
                name=f"{name}_{index:04d}",
                external_bones=external_bones,
            )
        except (IndexError, struct.error, ValueError) as exc:
            diagnostics["model_entries_omitted"].append({
                "index": index,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            continue
        if index == 0 and not part.bones:
            raise FF2MDLError(
                f"{source}: top FF2 SGD has no coordinate table for the character skeleton"
            )
        if model is None:
            model = part
            shared_bones = part.bones or None
        if not part or not part.meshes:
            diagnostics["model_entries_omitted"].append({
                "index": index,
                "reason": "no renderable meshes",
            })
            continue
        if model is not part:
            if external_bones:
                diagnostics["model_entries_shared_skeleton"] += 1
            merge_sgd_models(model, part)
        diagnostics["model_entries_parsed"] += 1

    if model is None or not model.meshes:
        raise FF2MDLError(f"{source}: FF2 model pack contains no renderable SGD entries")

    decoded_textures = []
    for entry in texture_entries:
        try:
            pictures = decode_tim2(entry["data"])
        except (IndexError, struct.error, ValueError) as exc:
            diagnostics["texture_entries_omitted"].append({
                "index": entry["index"],
                "reason": f"{type(exc).__name__}: {exc}",
            })
            continue
        decoded = 0
        for picture in pictures:
            image = picture.get("image")
            if image is not None:
                decoded_textures.append(
                    (picture.get("gs_tex0", 0) & 0x3FFF, image)
                )
                decoded += 1
        diagnostics["texture_entries_decoded"] += decoded

    by_tbp0 = {}
    for tbp0, image in decoded_textures:
        by_tbp0.setdefault(tbp0, image)
    textures = []
    mapped = 0
    for material in model.materials:
        image = by_tbp0.get(getattr(material, "tbp0", -1))
        if image is None:
            material.texture_index = -1
            continue
        texture_index = next(
            (index for index, existing in enumerate(textures) if existing is image),
            None,
        )
        if texture_index is None:
            textures.append(image)
            texture_index = len(textures) - 1
        material.texture_index = texture_index
        mapped += 1

    diagnostics["materials_mapped"] = mapped
    diagnostics["textures_output"] = len(textures)
    model.texture_debug = {
        "ff2_mdl_tbp0": sorted(by_tbp0),
        "unmapped_materials": [
            {
                "index": material.index,
                "name": material.name,
                "tbp0": material.tbp0,
            }
            for material in model.materials
            if material.texture_index < 0
        ],
    }
    model.parse_diagnostics = diagnostics
    return FF2MDLResult(model=model, textures=textures, diagnostics=diagnostics)


__all__ = ["FF2MDLError", "FF2MDLResult", "parse_ff2_mdl"]
