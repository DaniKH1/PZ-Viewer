"""Strict FF2 PK2 entry points.

The FF2 PK2 container is treated as structurally compatible with the existing
PK2 implementation until verified FF2 assets establish otherwise.  Parsing is
delegated; embedded texture scanning uses the explicit FF2 TIM2 facade.
"""

from .pz_pk2 import unpack_pk2 as _unpack_pk2
from .pz_tim2_ff2 import decode_tim2
from .pz_tim2_ff1 import reconstruct_sgd_textures as _reconstruct_sgd_textures


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


def reconstruct_sgd_textures(data_or_path, materials, diagnostics=None):
    """Decode FF2 headerless GS uploads using the compatible GS decoder."""
    images, uploads = _reconstruct_sgd_textures(
        data_or_path,
        materials,
        diagnostics=diagnostics,
        preserve_zero_alpha=True,
    )
    return images, uploads


__all__ = [
    "iter_embedded_tim2",
    "reconstruct_sgd_textures",
    "unpack_pk2",
    "unpack_room_pk2",
]
