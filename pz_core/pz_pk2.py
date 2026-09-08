"""Project Zero PK2 archive parsing.

The original Obscura converter treats PK2 entries as a linked offset table:
the first offset is stored at byte 16 and each following table node is found
by adding the current relative offset plus its 16-byte node header.  Some
older tools instead emit sequential size/type headers, while PZ1 room
wrappers contain absolute offsets or a single embedded SGD.  This module
keeps those layouts behind one public API.
"""

import os
import struct


MAX_PACK_FILES = 4096


def _read_data(data_or_path):
    if isinstance(data_or_path, (str, os.PathLike)):
        with open(data_or_path, "rb") as archive:
            return archive.read()
    return bytes(data_or_path)


def _entry(data, index, offset, end, payload_offset=16, entry_type=None):
    if offset < 0 or end > len(data) or offset + payload_offset > end:
        return None
    block = data[offset:end]
    if entry_type is None:
        type_at_zero = block[:4].rstrip(b"\x00").decode("ascii", errors="ignore")
        type_at_eight = block[8:12].rstrip(b"\x00").decode("ascii", errors="ignore")
        if type_at_zero and all(32 <= ord(char) < 127 for char in type_at_zero):
            entry_type = type_at_zero
        elif type_at_eight and all(32 <= ord(char) < 127 for char in type_at_eight):
            entry_type = type_at_eight
        else:
            entry_type = ""
    return {
        "index": index,
        "offset": offset,
        "size": end - offset - payload_offset,
        "type": entry_type,
        "data": data[offset + payload_offset:end],
    }


def _unpack_sequential(data, count):
    entries = []
    offset = 16
    for index in range(count):
        if offset + 16 > len(data):
            return []
        file_size, file_type = struct.unpack_from("<II", data, offset)
        end = offset + 16 + file_size
        if end > len(data):
            return []
        entries.append({
            "index": index,
            "offset": offset,
            "size": file_size,
            "type": file_type,
            "data": data[offset + 16:end],
        })
        offset = end
    return entries


def _unpack_linked(data, count):
    """Read the linked table layout used by Obscura's PK2 implementation."""
    if count <= 0 or count > MAX_PACK_FILES or len(data) < 20:
        return []

    starts = [20]
    table = 16
    for _ in range(1, count):
        relative = struct.unpack_from("<I", data, table)[0]
        next_table = table + relative + 16
        next_start = next_table + 4
        if next_start <= starts[-1] or next_start >= len(data):
            return []
        starts.append(next_start)
        table = next_table

    entries = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(data)
        item = _entry(data, index, start - 16, end, payload_offset=16)
        if item is None:
            # The table node itself may be immediately before the payload.
            item = _entry(data, index, start, end, payload_offset=0)
        if item is None:
            return []
        entries.append(item)
    return entries


def _unpack_pz1(data, count):
    """Read PZ1 absolute-offset and single-embedded-SGD wrappers."""
    if count <= 0 or count > MAX_PACK_FILES or len(data) < 20:
        return []
    table_end = 16 + count * 4
    if table_end > len(data):
        return []

    offsets = list(struct.unpack_from(f"<{count}I", data, 16))
    single_sgd = (
        count == 1
        and len(data) >= 36
        and struct.unpack_from("<I", data, 32)[0] == 0x1050
    )
    if single_sgd:
        starts = [32]
        payload_offsets = [0]
    elif count == 1:
        starts = [table_end]
        payload_offsets = [16]
    elif offsets == sorted(offsets) and len(set(offsets)) == len(offsets) and all(
        table_end <= offset < len(data) for offset in offsets
    ):
        starts = offsets
        payload_offsets = [16] * count
    else:
        return []

    entries = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(data)
        item = _entry(
            data,
            index,
            start,
            end,
            payload_offset=payload_offsets[index],
        )
        if item is None:
            return []
        entries.append(item)
    return entries


def unpack_pk2(data_or_path, pz1=False):
    data = _read_data(data_or_path)
    if len(data) < 20:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    if count <= 0 or count > MAX_PACK_FILES:
        return []

    if pz1:
        return _unpack_pz1(data, count)

    sequential = _unpack_sequential(data, count)
    if sequential:
        return sequential
    linked = _unpack_linked(data, count)
    if linked:
        return linked
    return _unpack_pz1(data, count)


def unpack_pk2_pz1(data_or_path):
    return unpack_pk2(data_or_path, pz1=True)


def unpack_room_pk2(data_or_path):
    entries = unpack_pk2(data_or_path)
    result = {
        "near_sgd": entries[0]["data"] if len(entries) > 0 else None,
        "far_sgd": entries[1]["data"] if len(entries) > 1 else None,
        "ss_sgd": entries[2]["data"] if len(entries) > 2 else None,
        "sh_sgd": entries[3]["data"] if len(entries) > 3 else None,
        "all_entries": entries,
    }
    return result


def unpack_room_pk2_pz1(data_or_path):
    entries = unpack_pk2_pz1(data_or_path)
    names = ("near_sgd", "far_sgd", "ss_sgd", "sh_sgd")
    result = {name: None for name in names}
    result["all_entries"] = entries
    for name, item in zip(names, entries):
        result[name] = item["data"]
    return result
