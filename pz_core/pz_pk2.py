"""Minimal Project Zero 1 PK2 support for linked room archives."""

import os
import struct

from .pz_tim2_ff1 import decode_tim2


def _read_data(data_or_path):
    if isinstance(data_or_path, (str, os.PathLike)):
        with open(data_or_path, "rb") as archive:
            return archive.read()
    return bytes(data_or_path)


def unpack_pk2(data_or_path):
    data = _read_data(data_or_path)
    if len(data) < 20:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    if count <= 0 or count > 4096:
        return []

    tables = [16]
    table = 16
    for _ in range(1, count):
        relative = struct.unpack_from("<I", data, table)[0]
        next_table = table + relative + 16
        if next_table <= table or next_table + 16 >= len(data):
            return []
        tables.append(next_table)
        table = next_table

    entries = []
    for index, table in enumerate(tables):
        start = table + 16
        end = tables[index + 1] if index + 1 < len(tables) else len(data)
        if start > end:
            return []
        header = data[start:end]
        entry_type = header[:4].rstrip(b"\x00").decode("ascii", errors="ignore")
        entries.append({
            "index": index,
            "offset": start,
            "size": end - start,
            "type": entry_type,
            "data": data[start:end],
        })
    return entries


def unpack_room_pk2(data_or_path):
    entries = unpack_pk2(data_or_path)
    names = ("near_sgd", "far_sgd", "ss_sgd", "sh_sgd")
    result = {name: None for name in names}
    result["all_entries"] = entries
    for name, entry in zip(names, entries):
        result[name] = entry["data"]
    return result


def iter_embedded_tim2(data_or_path):
    """Yield decoded TIM2 pictures embedded anywhere in PK2 entries.

    FF1 room PK2 files can place TIM2 resources after an entry-specific
    wrapper, so the resource is not necessarily at the entry start.  Keep
    this scan local to PK2 instead of making the generic texture path infer
    formats from arbitrary binary files.
    """
    for entry in unpack_pk2(data_or_path):
        payload = entry["data"]
        search_from = 0
        while True:
            offset = payload.find(b"TIM2", search_from)
            if offset < 0:
                break
            pictures = decode_tim2(payload[offset:])
            for picture_index, picture in enumerate(pictures):
                picture["_pk2_entry_index"] = entry["index"]
                picture["_tim2_picture_index"] = picture_index
                yield picture
            search_from = offset + 4
