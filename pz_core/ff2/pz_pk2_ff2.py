"""Strict FF2 PK2 entry points.

The linked PK2 container layout is shared; GS texture reconstruction uses the
FF2 colour-mode decoder with full TEX0/CLUT identity. TIM2 remains separate.
"""

from pz_core.common.pz_pk2 import unpack_pk2 as _unpack_pk2
from pz_core.ff2.pz_tim2_ff2 import decode_tim2
from pz_core.ff2.pz_gs_ff2 import reconstruct_sgd_textures, bind_textures


def unpack_pk2(data_or_path):
    """Unpack an FF2 PK2 using the shared compatible container parser."""
    return _unpack_pk2(data_or_path)


def unpack_room_pk2(data_or_path):
    """Return FF2 room entries using the shared PK2 layout."""
    entries = unpack_pk2(data_or_path)
    names = ("near_sgd", "far_sgd", "ss_sgd", "sh_sgd")
    result = {name: None for name in names}
    result["all_entries"] = entries
    for name, entry in zip(names, entries):
        result[name] = entry["data"]
    return result


def iter_embedded_tim2(data_or_path):
    """Yield embedded TIM2 pictures using the explicit FF2 texture decoder."""
    for entry in unpack_pk2(data_or_path):
        payload = entry["data"]
        search_from = 0
        while True:
            offset = payload.find(b"TIM2", search_from)
            if offset < 0:
                break
            try:
                pictures = decode_tim2(payload[offset:])
            except ValueError:
                pictures = []
            for picture_index, picture in enumerate(pictures):
                picture["_pk2_entry_index"] = entry["index"]
                picture["_tim2_picture_index"] = picture_index
                yield picture
            search_from = offset + 4



__all__ = [
    "bind_textures",
    "iter_embedded_tim2",
    "reconstruct_sgd_textures",
    "unpack_pk2",
    "unpack_room_pk2",
]
