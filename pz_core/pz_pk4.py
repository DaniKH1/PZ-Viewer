"""Project Zero 3 PK4 archive and model helpers."""

import os
import struct

from .pz_sgd import parse_sgd


def _read_data(data_or_path):
    if isinstance(data_or_path, (str, os.PathLike)):
        with open(data_or_path, "rb") as archive:
            return archive.read()
    return bytes(data_or_path)


def _parse_offsets(data, count, table_offset):
    table_end = table_offset + count * 4
    if count <= 0 or table_end > len(data):
        return []
    offsets = list(struct.unpack(f"<{count}I", data[table_offset:table_end]))
    if any(offset < table_end or offset >= len(data) for offset in offsets):
        return []
    if offsets != sorted(offsets) or len(set(offsets)) != len(offsets):
        return []
    return offsets


def unpack_pk4(data_or_path):
    """Return PK4 payloads and their 4-byte type from each subheader."""
    data = _read_data(data_or_path)
    if len(data) < 16 or data[:4] != b"PK4\x00":
        return []

    archive_id, file_num, _padding = struct.unpack("<III", data[4:16])
    offsets = _parse_offsets(data, file_num, 16)
    if not offsets:
        return []

    entries = []
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else len(data)
        if offset + 16 > end:
            continue
        type_bytes = data[offset + 8:offset + 12]
        entry_type = type_bytes.rstrip(b"\x00").decode("ascii", errors="ignore").lower()
        entries.append({
            "index": index,
            "offset": offset,
            "size": end - offset - 16,
            "type": entry_type,
            "id": archive_id,
            "data": data[offset + 16:end],
        })
    return entries


def _iter_nested_entries(entries):
    for entry in entries:
        nested = unpack_pk4(entry["data"])
        if nested:
            yield from _iter_nested_entries(nested)
        else:
            yield entry


def iter_pk4_entries(data_or_path):
    """Yield leaf entries from a PK4, recursively unpacking MPK/TPK containers."""
    yield from _iter_nested_entries(unpack_pk4(data_or_path))


def flip_uvs_vertical(model):
    """Convert PS2 texture coordinates to the top-left image convention."""
    for mesh in getattr(model, "meshes", []):
        mesh.uvs = [[u, 1.0 - v] for u, v in mesh.uvs]
    model.uvs_are_flipped = True
    return model


def parse_pk4_model(data_or_path, name="model", flip_uv=False):
    """Parse and merge SGD models in a PK4/MPK hierarchy."""
    if isinstance(data_or_path, (str, os.PathLike)):
        name = os.path.splitext(os.path.basename(data_or_path))[0]
    entries = list(iter_pk4_entries(data_or_path))
    sgd_entries = [entry for entry in entries if entry["type"] == "sgd"]
    if sgd_entries:
        numbered = [entry["index"] for entry in sgd_entries if isinstance(entry["index"], int)]
        collision_index = None
        if 15 in numbered:
            collision_index = 15
        elif 14 in numbered and max(numbered) == 14 and len(numbered) >= 14:
            # Some characters, such as ch066, omit 15.sgd and store collision
            # geometry in the last component instead.
            collision_index = 14
        if collision_index is not None:
            sgd_entries = [entry for entry in sgd_entries if entry["index"] != collision_index]
    base_model = None
    for entry in sgd_entries:
        model = parse_sgd(
            entry["data"],
            name=name,
            external_bones=base_model.bones if base_model and base_model.bones else None,
        )
        if not model or not model.meshes:
            continue
        if base_model is None:
            base_model = model
        else:
            from .pz_sgd import merge_sgd_models
            merge_sgd_models(base_model, model)
    return flip_uvs_vertical(base_model) if base_model and flip_uv else base_model
