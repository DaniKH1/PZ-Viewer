"""Validated Xbox XPR0 texture resources (BC1, BC2 and BC3).

Offsets in D3DResource.Data are relative to the data section at header +8.
Format is an Xbox D3DFORMAT bitfield, not a game-specific size tag. Resource
zero is a real resource. Pixels, including alpha, are decoded faithfully.
See docs/xbox-xpr.md for the recovered layout, evidence and supported subset.
"""
from __future__ import annotations

import os
import struct
from PIL import Image

XPR0_MAGIC = b"XPR0"
XPR0_HEADER_SIZE = 12
XPR0_RECORD_SIZE = 20
XPR0_MAX_RECORDS = 4096
DXT1_BLOCK_BYTES = 8
DXT3_BLOCK_BYTES = 16
DXT5_BLOCK_BYTES = 16
_FORMATS = {0x0C: ("DXT1", 8), 0x0E: ("DXT3", 16), 0x0F: ("DXT5", 16)}


class XPR0Error(ValueError):
    """Invalid, truncated or unsupported XPR resource, with byte context."""


def _read_data(source):
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            return stream.read(), os.fspath(source)
    return bytes(source), "<XPR0 buffer>"


def _fail(name, offset, message):
    raise XPR0Error(f"{name}: XPR0 @0x{offset:X}: {message}")


def is_xpr0(data):
    return len(data) >= 12 and data[:4] == XPR0_MAGIC


def _surface(word, size_word=0, *, name="<XPR0 buffer>", offset=0):
    code = (word >> 8) & 0xFF
    dimension = (word >> 4) & 0xF
    if word & 4 or dimension != 2:
        _fail(name, offset, "only non-cubemap 2D texture resources are supported")
    if code not in _FORMATS:
        _fail(name, offset, f"unsupported Xbox pixel format 0x{code:02X}; not guessed")
    if size_word:
        _fail(name, offset + 4, "nonzero Size is not supported for these BC textures")
    width = 1 << ((word >> 20) & 15)
    height = 1 << ((word >> 24) & 15)
    depth = 1 << ((word >> 28) & 15)
    levels = (word >> 16) & 15
    if depth != 1 or max(width, height) > 4096:
        _fail(name, offset, f"unsupported texture dimensions {width}x{height}x{depth}")
    if not 1 <= levels <= max(width, height).bit_length():
        _fail(name, offset, f"invalid mip count {levels} for {width}x{height}")
    fmt, block_bytes = _FORMATS[code]
    mips = []
    cursor = 0
    for level in range(levels):
        w, h = max(1, width >> level), max(1, height >> level)
        length = ((w + 3) // 4) * ((h + 3) // 4) * block_bytes
        mips.append({"level": level, "width": w, "height": h,
                     "relative_offset": cursor, "size": length})
        cursor += length
    return {"format": fmt, "pixel_format": code, "block_bytes": block_bytes,
            "width": width, "height": height, "depth": depth,
            "mip_levels": levels, "mips": mips, "bytes_used": cursor,
            "dma_channel": word & 3, "border_source": bool(word & 8)}


def parse_xpr0_records(data_or_path):
    """One validated descriptor per resource; ``offset`` is a FILE offset.

    ``data_offset`` preserves the original data-section-relative field.
    ``size`` includes allocation padding; ``bytes_used`` does not.
    """
    data, name = _read_data(data_or_path)
    if not is_xpr0(data):
        _fail(name, 0, "missing/truncated XPR0 header")
    total, header = struct.unpack_from("<II", data, 4)
    if total > len(data) or total < 16:
        _fail(name, 4, f"declared size {total} outside buffer of {len(data)} bytes")
    if not 16 <= header <= total or header % 4:
        _fail(name, 8, f"invalid data-section base 0x{header:X}")
    records = []
    pos = 12
    while True:
        if pos + 4 > header:
            _fail(name, pos, "resource table has no 0xFFFFFFFF terminator")
        common = struct.unpack_from("<I", data, pos)[0]
        if common == 0xFFFFFFFF:
            break
        if len(records) >= XPR0_MAX_RECORDS or pos + 20 > header:
            _fail(name, pos, "resource descriptor exceeds bounded header")
        common, relative, lock, word, size_word = struct.unpack_from("<5I", data, pos)
        if common & 0x00070000 != 0x00040000:
            _fail(name, pos, f"unsupported resource Common=0x{common:08X}; expected texture")
        info = _surface(word, size_word, name=name, offset=pos + 12)
        start = header + relative
        if start >= total or info["bytes_used"] > total - start:
            _fail(name, pos + 4, f"texture at 0x{start:X} exceeds declared archive size")
        records.append({"index": len(records), "descriptor_offset": pos,
                        "common": common, "lock": lock, "tag": word,
                        "format_word": word, "size_word": size_word,
                        "header_size": header, "total_size": total,
                        "data_offset": relative, "offset": start, **info})
        pos += 20
    ends = sorted({r["offset"] for r in records} | {total})
    for record in records:
        end = next(p for p in ends if p > record["offset"])
        record["size"] = end - record["offset"]
        if record["bytes_used"] > record["size"]:
            _fail(name, record["descriptor_offset"] + 4, "mip chain overlaps the next resource")
        record["trailing_bytes"] = record["size"] - record["bytes_used"]
        record["mips"] = [{**m, "offset": record["offset"] + m["relative_offset"]}
                          for m in record["mips"]]
    return records


def _rgb565(value):
    r, g, b = (value >> 11) & 31, (value >> 5) & 63, value & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _decode_block(raw, fmt, use_alpha=True, punch_through=True):
    color_offset = 0 if fmt == "DXT1" else 8
    c0, c1, selectors = struct.unpack_from("<HHI", raw, color_offset)
    a, b = _rgb565(c0), _rgb565(c1)
    palette = [(*a, 255), (*b, 255)]
    if c0 > c1 or fmt != "DXT1":
        palette += [tuple((2 * a[i] + b[i]) // 3 for i in range(3)) + (255,),
                    tuple((a[i] + 2 * b[i]) // 3 for i in range(3)) + (255,)]
    else:
        palette += [tuple((a[i] + b[i]) // 2 for i in range(3)) + (255,),
                    (0, 0, 0, 0 if punch_through else 255)]
    alpha = None
    if fmt == "DXT3":
        bits = int.from_bytes(raw[:8], "little")
        alpha = [((bits >> (4 * i)) & 15) * 17 for i in range(16)]
    elif fmt == "DXT5":
        a0, a1 = raw[:2]
        table = [a0, a1]
        if a0 > a1:
            table += [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
        else:
            table += [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
        bits = int.from_bytes(raw[2:8], "little")
        alpha = [table[(bits >> (3 * i)) & 7] for i in range(16)]
    pixels = []
    for i in range(16):
        color = palette[(selectors >> (2 * i)) & 3]
        coverage = alpha[i] if alpha is not None else color[3]
        pixels.append((*color[:3], coverage if use_alpha else 255))
    return pixels


def _decode_level(payload, width, height, fmt, use_alpha=True, punch_through=True):
    block_bytes = 8 if fmt == "DXT1" else 16
    nx, ny = (width + 3) // 4, (height + 3) // 4
    expected = nx * ny * block_bytes
    if len(payload) < expected:
        raise XPR0Error(f"<texture buffer>: mip @0x0: need {expected} bytes, have {len(payload)}")
    rgba = bytearray(width * height * 4)
    for by in range(ny):
        for bx in range(nx):
            p = (by * nx + bx) * block_bytes
            pixels = _decode_block(payload[p:p + block_bytes], fmt, use_alpha, punch_through)
            for y in range(min(4, height - by * 4)):
                for x in range(min(4, width - bx * 4)):
                    dest = ((by * 4 + y) * width + bx * 4 + x) * 4
                    rgba[dest:dest + 4] = bytes(pixels[y * 4 + x])
    return Image.frombytes("RGBA", (width, height), bytes(rgba))


def decode_xpr0_texture(payload, tag, use_alpha=True, punch_through=True):
    """Decode the format word's full mip chain, preserving alpha by default."""
    info = _surface(tag)
    if len(payload) < info["bytes_used"]:
        _fail("<texture buffer>", 0, f"truncated mip chain: need {info['bytes_used']}, have {len(payload)}")
    images = [_decode_level(payload[m["relative_offset"]:m["relative_offset"] + m["size"]],
                            m["width"], m["height"], info["format"], use_alpha, punch_through)
              for m in info["mips"]]
    return images, {**info, "base_edge": info["width"], "mip_levels_declared": info["mip_levels"],
                    "mip_levels_decoded": len(images), "tag_recognised": True,
                    "trailing_bytes": len(payload) - info["bytes_used"]}


def parse_xpr0(data_or_path, include_self_record=None, decode=True,
               use_alpha=True, punch_through=True):
    """Picture dictionary per mip, with ``image=None`` in metadata-only mode.

    ``include_self_record`` is retained as a no-op for API compatibility:
    there is no self record and resource zero must never be silently dropped.
    ``gs_tex0`` is only an adapter key, not an actual PS2 GPU register.
    """
    data, _ = _read_data(data_or_path)
    records = parse_xpr0_records(data_or_path)
    pictures = []
    for r in records:
        images = []
        if decode:
            images, _ = decode_xpr0_texture(data[r["offset"]:r["offset"] + r["size"]],
                                           r["tag"], use_alpha, punch_through)
        for m in r["mips"]:
            pictures.append({"index": r["index"], "level": m["level"],
                "width": m["width"], "height": m["height"],
                "image": images[m["level"]] if decode else None,
                "gs_tex0": r["index"], "xpr_offset": r["offset"],
                "xpr_data_offset": r["data_offset"], "xpr_mip_offset": m["offset"],
                "xpr_size": r["size"], "xpr_mip_size": m["size"],
                "xpr_tag": r["tag"], "xpr_format": r["format"],
                "xpr_base_edge": r["width"], "xpr_mip_levels": r["mip_levels"],
                "xpr_mips_decoded": len(images), "xpr_trailing_bytes": r["trailing_bytes"],
                "xpr_tag_recognised": True})
    return pictures


def xpr0_surface_table(data_or_path):
    """Metadata without pixel decoding; width and height are independent."""
    return [{**r, "edge": r["width"], "mips": r["mip_levels"], "tag_recognised": True}
            for r in parse_xpr0_records(data_or_path)]


__all__ = ["XPR0Error", "XPR0_MAGIC", "decode_xpr0_texture", "is_xpr0",
           "parse_xpr0", "parse_xpr0_records", "xpr0_surface_table"]
