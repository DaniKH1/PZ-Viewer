"""Project Zero 1 PK2 archive parser.

This is intentionally separate from the legacy parser used by character
formats. PZ1 room/model PK2 files store absolute payload offsets.
"""

import os
import struct


def _read_data(data_or_path):
    if isinstance(data_or_path, (str, os.PathLike)):
        with open(data_or_path, "rb") as archive:
            return archive.read()
    return bytes(data_or_path)


def unpack_pk2_pz1(data_or_path):
    data = _read_data(data_or_path)
    if len(data) < 20:
        return []

    file_num = struct.unpack_from("<I", data, 0)[0]
    table_end = 16 + file_num * 4
    if file_num <= 0 or table_end > len(data):
        return []

    offsets = list(struct.unpack_from(f"<{file_num}I", data, 16))
    starts = []
    single_sgd_layout = file_num == 1 and len(data) >= 36 and struct.unpack_from("<I", data, 32)[0] == 0x1050
    if file_num == 1:
        # The normal PZ1 room wrapper contains one SGD payload.  Its offset
        # word points to the end of the wrapper's offset chain, not to the
        # payload itself; the payload starts immediately after the 16-byte
        # PK2 header.
        starts = [32 if single_sgd_layout else table_end]
    elif offsets == sorted(offsets) and len(set(offsets)) == len(offsets) and all(
        table_end <= offset < len(data) for offset in offsets
    ):
        # Support the absolute-offset variant used by small standalone PK2s.
        starts = offsets
    else:
        # Full PZ1 PK2 files use a linked offset table.  Each offset is a
        # relative distance to the next table, while the data for the current
        # entry follows its table header.
        table = 16
        starts = [table_end]
        for offset in offsets[:-1]:
            next_table = table + offset + 16
            next_start = next_table + 16
            if next_start <= starts[-1] or next_start >= len(data):
                return []
            starts.append(next_start)
            table = next_table

    entries = []
    for index, offset in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(data)
        if offset + 16 > end:
            continue
        block = data[offset:end]
        # PZ1/PS2 PK2 blocks begin with a four-byte resource signature
        # (for example ``SGD `` or ``CAN ``), followed by a 16-byte block
        # header.  Some tools emit the type at +8 instead, so accept both
        # layouts while keeping the payload boundary explicit.
        type_at_zero = block[:4].rstrip(b"\x00").decode("ascii", errors="ignore")
        type_at_eight = block[8:12].rstrip(b"\x00").decode("ascii", errors="ignore")
        if type_at_zero and all(32 <= ord(char) < 127 for char in type_at_zero):
            entry_type = type_at_zero
        else:
            entry_type = type_at_eight
        payload_offset = 0 if single_sgd_layout and index == 0 else 16
        entries.append({
            "index": index,
            "offset": offset,
            "size": end - offset - payload_offset,
            "type": entry_type,
            "data": data[offset + payload_offset:end],
        })
    return entries


def unpack_room_pk2_pz1(data_or_path):
    entries = unpack_pk2_pz1(data_or_path)
    names = ("near_sgd", "far_sgd", "ss_sgd", "sh_sgd")
    result = {name: None for name in names}
    result["all_entries"] = entries
    for name, entry in zip(names, entries):
        result[name] = entry["data"]
    return result
