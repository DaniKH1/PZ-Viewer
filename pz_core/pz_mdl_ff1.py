"""Fatal Frame 1 MDL package parser.

A retail FF1 character ``.mdl`` is *not* a bespoke "MDL header plus embedded
blocks".  It is a plain ``PK2_HEAD`` archive whose entries are themselves
PK2 archives, exactly like a room ``.pk2``:

    Obscura-ff1/ModelConverter/game/packfile.h:12
        struct PK2_HEAD { int pack_num; int pad[3]; unsigned int offset[1]; };

    Obscura-ff1/ModelConverter/game/packfile.cpp:49 (GetFileInPak)
        the offset[] "array" is a linked list of 16-byte table records;
        record i holds {u32 rel_to_next_record, u32 type, u32 pad, u32 pad}
        and the entry payload starts 0x10 bytes later, so
        table[i + 1] == table[i] + rel[i] + 0x10.

``Model::ResolveModelLayout`` (Model.cpp:574) classifies every entry by
*content* rather than by the 16-bit type word: an entry whose first payload
is a ``0x1050`` SGD record is the model pack
(``LooksLikeSgdPak``, Model.cpp:405), an entry whose first payload starts
with ``TIM2`` is the texture pack (``LooksLikeTexturePak``, Model.cpp:415).
A handful of containers wrap an entry in an extra ``{u32 size, u32 type}``
0x10-byte header (``TryUnwrapFF1Chunk``, Model.cpp:428), which is why the
type word is still honoured here.

Everything past the container boundary (SGD records, TIM2 pictures) is
delegated to :mod:`pz_sgd_ff1` / :mod:`pz_tim2_ff3` so FF1 keeps sharing the
verified record readers with FF2/FF3.
"""

import os
import struct
from dataclasses import dataclass

from .pz_sgd_ff1 import merge_sgd_models, parse_sgd
from .pz_sgd_ff3 import face_along_positive_z
from .pz_tim2_ff3 import decode_tim2


FF1_MODEL_CHUNK = 5
FF1_TEXTURE_CHUNK = 6
FF1_CHUNK_HEADER_SIZE = 0x10
MAX_PACK_ENTRIES = 4096
# ``m055_syouall.mdl`` nests three PK2 levels deep, so the resolver recurses a
# little further than Obscura's single-level ``InspectPackageEntry``.
MAX_PACK_DEPTH = 6


class FF1MDLError(ValueError):
    """Raised when an MDL is malformed or outside the supported subset."""


@dataclass
class FF1MDLResult:
    model: object
    textures: list
    diagnostics: dict


def _read_data(data_or_path):
    if isinstance(data_or_path, (str, os.PathLike)):
        with open(data_or_path, "rb") as stream:
            return stream.read()
    return bytes(data_or_path)


def _looks_like_sgd(data):
    """Mirror ``Model::LooksLikeSgd`` (Model.cpp:391)."""
    if len(data) < 24:
        return False
    version, _mapflag, _kind, _materials, _coord, _mat, _proc, blocks = (
        struct.unpack_from("<IBBHIIII", data, 0)
    )
    return version == 0x1050 and 0 < blocks < MAX_PACK_ENTRIES


def _looks_like_tim2(data):
    """Mirror ``Model::LooksLikeTexturePak``'s magic test (Model.cpp:424)."""
    return len(data) >= 4 and data[:4] == b"TIM2"


def _has_coordinate_table(data):
    """True when an SGD entry carries its own ``SGDCOORDINATE`` table.

    ``SGDFILEHEADER::pCoord`` sits at +0x08 and is a file-relative offset
    (sgd_types.h:426).  It is 0 for the body-part SGDs stored after entry 0 of
    a character MDL; ``GetCoordinatePtr`` (sgd_types.h:490) rejects 0 and also
    rejects anything at or above 0x30000000 (an already-remapped pointer, see
    ``sgdRemap`` in sgd.cpp:188), and ``Model::GetCurrentCoordinate``
    (Model.cpp:949) then reuses ``sgdTop``'s bones.  The distinction decides
    whether the entry needs the shared skeleton or keeps its own.
    """
    if len(data) < 12:
        return False
    coord_offset = struct.unpack_from("<I", data, 8)[0]
    return 0 < coord_offset < 0x30000000


def _looks_like_pack(data):
    """Mirror ``Model::LooksLikePak`` (Model.cpp:369)."""
    if len(data) < 0x20:
        return False
    entries = struct.unpack_from("<I", data, 0)[0]
    if not 0 < entries <= MAX_PACK_ENTRIES:
        return False
    return struct.unpack_from("<I", data, 0x10)[0] <= len(data)


def _pack_entries(data):
    """Walk a PK2_HEAD the way ``GetFileInPak`` does (packfile.cpp:49).

    Returns one dict per table record.  ``size`` is the byte length of the
    payload and ``type`` is the FF1 chunk tag stored in the record itself
    (5 = model, 6 = texture); Obscura never reads it for retail MDLs but it is
    the only cheap cross-check available for diagnostics.
    """
    if not _looks_like_pack(data):
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    table = 0x10
    entries = []
    for _ in range(count):
        if table + FF1_CHUNK_HEADER_SIZE > len(data):
            break
        size, kind = struct.unpack_from("<II", data, table)
        start = table + FF1_CHUNK_HEADER_SIZE
        entries.append({
            "index": len(entries),
            "table_offset": table,
            "offset": start,
            "size": size,
            "type": kind,
            "data": data[start:start + size],
        })
        table = start + size
    return entries


def _unwrap_ff1_chunk(data):
    """Mirror ``Model::TryUnwrapFF1Chunk`` (Model.cpp:428).

    Some FF1 containers store an entry as ``{u32 size, u32 type, pad, pad}``
    followed by a nested pack.  Retail character MDLs do not, but extracted
    sidecars do, and Obscura accepts both.
    """
    if len(data) < FF1_CHUNK_HEADER_SIZE:
        return None
    size, kind = struct.unpack_from("<II", data, 0)
    if kind not in (FF1_MODEL_CHUNK, FF1_TEXTURE_CHUNK):
        return None
    # Obscura only requires ``chunkSize <= available``: trailing padding
    # between a chunk and the next table record is normal.
    if size < FF1_CHUNK_HEADER_SIZE or size > len(data):
        return None
    return data[FF1_CHUNK_HEADER_SIZE:size]


def _resolve_packs(data, depth=0):
    """Return ``(model_entries, texture_entries)`` for an FF1 container.

    Mirrors ``Model::ResolveModelLayout`` (Model.cpp:574): try the buffer
    itself, then every entry, and remember the first model pack and the first
    texture pack found (``TryUseModelPak``/``TryUseTexturePak`` return early
    once their slot is filled, Model.cpp:458 and Model.cpp:469).
    """
    if depth > MAX_PACK_DEPTH:
        return None, None
    if _looks_like_sgd(data):
        return [{"index": 0, "type": None, "size": len(data), "data": data}], []

    entries = _pack_entries(data)
    if not entries:
        return None, None

    first = entries[0]["data"]
    if _looks_like_sgd(first):
        return entries, None
    if _looks_like_tim2(first):
        return None, entries

    model_entries = None
    texture_entries = None
    for entry in entries:
        if model_entries is not None and texture_entries is not None:
            break
        payload = _unwrap_ff1_chunk(entry["data"])
        if payload is None:
            payload = entry["data"]
        sub_model, sub_texture = _resolve_packs(payload, depth + 1)
        if model_entries is None:
            model_entries = sub_model
        if texture_entries is None:
            texture_entries = sub_texture
    return model_entries, texture_entries


def _sidecar_entries(base, suffix, source):
    """Load an optional ``.mpk``/``.pk2`` sidecar (Model.cpp:514, Model.cpp:544)."""
    sidecar = os.path.splitext(base)[0] + suffix
    if os.path.normcase(sidecar) == os.path.normcase(source):
        return None, None
    if not os.path.isfile(sidecar):
        return None, None
    with open(sidecar, "rb") as stream:
        return _resolve_packs(stream.read())


def parse_ff1_mdl(data_or_path, name="mdl"):
    """Parse the supported FF1 MDL package subset.

    The result contains the merged FF1 SGD model, decoded TIM2 images, and
    explicit layout/omission diagnostics.  Missing model packs raise
    ``FF1MDLError``; unreadable individual records are reported in
    ``diagnostics`` instead of aborting the whole package.
    """
    data = _read_data(data_or_path)
    source = os.fspath(data_or_path) if isinstance(data_or_path, (str, os.PathLike)) else name
    diagnostics = {
        "format": "ff1_mdl",
        "layout": "pack",
        "wrapped_chunks": 0,
        "chunks": [],
        "direct_model_packs": 0,
        "direct_texture_packs": 0,
        "model_entries": 0,
        "model_entries_parsed": 0,
        "model_entries_shared_skeleton": 0,
        "model_entries_omitted": [],
        "texture_entries": 0,
        "texture_entries_decoded": 0,
        "texture_entries_omitted": [],
    }

    model_entries, texture_entries = _resolve_packs(data)
    for entry in _pack_entries(data):
        diagnostics["chunks"].append({
            "offset": entry["offset"],
            "table_offset": entry["table_offset"],
            "type": entry["type"],
            "size": entry["size"],
            "source": source,
        })

    # Extracted FF1 releases keep the model pack in a sibling .mpk and the
    # TIM2 pack in a sibling .pk2; both are only consulted when the MDL itself
    # did not supply that slot (Model.cpp:609-610).
    if isinstance(data_or_path, (str, os.PathLike)):
        base = os.fspath(data_or_path)
        sidecar_model, sidecar_texture = _sidecar_entries(base, ".mpk", source)
        if model_entries is None:
            model_entries = sidecar_model
        if texture_entries is None:
            texture_entries = sidecar_texture
        sidecar_model, sidecar_texture = _sidecar_entries(base, ".pk2", source)
        if model_entries is None:
            model_entries = sidecar_model
        if texture_entries is None:
            texture_entries = sidecar_texture

    if not model_entries:
        raise FF1MDLError(
            f"{source}: no FF1 model pack (an MDL entry must start with an "
            f"0x1050 SGD record)"
        )
    diagnostics["model_entries"] = len(model_entries)
    for entry in texture_entries or []:
        diagnostics["texture_entries"] += 1

    model = None
    shared_bones = None
    for index, entry in enumerate(model_entries):
        try:
            part = parse_sgd(entry["data"], name=f"{name}_{index:04d}")
        except (IndexError, struct.error, ValueError) as exc:
            diagnostics["model_entries_omitted"].append(
                {"index": index, "reason": f"{type(exc).__name__}: {exc}"}
            )
            continue
        if not part or not part.meshes:
            diagnostics["model_entries_omitted"].append(
                {"index": index, "reason": "no renderable meshes"}
            )
            continue
        if model is None:
            # Entry 0 is Obscura's ``sgdTop`` (Model.cpp:617): it is the one
            # pack member that carries the skeleton.  A retail character MDL
            # splits the body into one SGD per part (head, arms, hair, ...) and
            # every part after entry 0 has ``pCoord == 0``, i.e. no coordinate
            # table of its own -- ``Model::GetCurrentCoordinate``
            # (Model.cpp:949) therefore falls back to ``sgdTop`` for those
            # entries, and ``GetCoordinateMatrix`` (Model.cpp:1696) transforms
            # their vertices with the top-level bone matrices.  Without that
            # fallback the extra parts keep raw model-space coordinates and
            # collapse onto the origin instead of onto their bones.
            model = part
            if part.bones:
                shared_bones = part.bones
        else:
            if shared_bones and not part.bones and not _has_coordinate_table(entry["data"]):
                part = parse_sgd(
                    entry["data"],
                    name=f"{name}_{index:04d}",
                    external_bones=shared_bones,
                )
                diagnostics["model_entries_shared_skeleton"] += 1
            merge_sgd_models(model, part)
        diagnostics["model_entries_parsed"] += 1

    if model is None:
        raise FF1MDLError(f"{source}: model pack contains no renderable FF1 SGD")

    textures = []
    for pack in texture_entries or []:
        try:
            pictures = decode_tim2(pack["data"])
        except (IndexError, struct.error, ValueError) as exc:
            diagnostics["texture_entries_omitted"].append(
                {"reason": f"{type(exc).__name__}: {exc}"}
            )
            continue
        for picture in pictures:
            image = picture.get("image")
            if image is not None:
                textures.append((picture.get("gs_tex0", 0) & 0x3FFF, image))
                diagnostics["texture_entries_decoded"] += 1

    by_tbp0 = {}
    for tbp0, image in textures:
        by_tbp0.setdefault(tbp0, image)
    material_textures = 0
    output_textures = []
    for material in model.materials:
        image = by_tbp0.get(getattr(material, "tbp0", -1))
        if image is not None:
            if image not in output_textures:
                output_textures.append(image)
            material.texture_index = output_textures.index(image)
            material_textures += 1
        else:
            material.texture_index = -1
    diagnostics["materials_mapped"] = material_textures
    diagnostics["textures_output"] = len(output_textures)
    face_along_positive_z(model)
    # The shared FF1 SGD entry point marks every FF1 payload as "V is already
    # bottom-up" (pz_sgd_ff1.py). That is wrong for the .mdl characters: their
    # atlases are wound the other way, so the face, tie and hem come out
    # vertically mirrored. Clearing the flag makes the viewer upload the
    # images flipped and the exporters write 1.0 - v, which is the same
    # correction in both paths. Only characters are touched here; rooms, items,
    # doors and furniture go through the .sgd path and are left alone until
    # they can be checked against something that is not a grey luminance map.
    model.uvs_are_flipped = False
    model.parse_diagnostics = diagnostics
    model.texture_debug = {
        "ff1_mdl_tbp0": sorted(by_tbp0),
        "unmapped_materials": [
            {"index": material.index, "name": material.name, "tbp0": material.tbp0}
            for material in model.materials
            if material.texture_index < 0
        ],
    }
    return FF1MDLResult(model=model, textures=output_textures, diagnostics=diagnostics)


__all__ = ["FF1MDLError", "FF1MDLResult", "parse_ff1_mdl"]