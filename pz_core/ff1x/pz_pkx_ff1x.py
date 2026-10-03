"""FF1 Xbox PKX package and embedded XPR0 reader."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import struct

import numpy as np
from PIL import Image

from pz_core.ff1x.pz_mpx_ff1x import XboxAsset, XboxMPXError, parse_pkx_geometry
from pz_core.ff1x.pz_xpr0 import XPR0Error, decode_xpr0_texture


class PKXError(ValueError):
    """Malformed or explicitly unsupported FF1 Xbox PKX package."""


@dataclass
class _Resource:
    kind: int
    descriptor_offset: int
    data_offset: int
    allocation: int
    tag: int | None = None
    size_word: int = 0


def _fail(name, offset, message):
    raise PKXError(f"{name}: PKX @0x{offset:X}: {message}")


def _mip_dimensions(tag, *, name, offset):
    if tag & 4 or ((tag >> 4) & 0xF) != 2:
        _fail(name, offset, "only non-cubemap 2D resources are supported")
    if ((tag >> 28) & 0xF) != 0:
        _fail(name, offset, "2D texture depth must be one")
    width = 1 << ((tag >> 20) & 0xF)
    height = 1 << ((tag >> 24) & 0xF)
    levels = (tag >> 16) & 0xF
    if max(width, height) > 4096 or not 1 <= levels <= max(width, height).bit_length():
        _fail(name, offset, f"invalid mip chain {width}x{height} with {levels} levels")
    return [(max(1, width >> level), max(1, height >> level)) for level in range(levels)]


def _morton_offset(x, y, width, height):
    xb, yb = width.bit_length() - 1, height.bit_length() - 1
    paired = min(xb, yb)
    result = 0
    for bit in range(paired):
        result |= ((x >> bit) & 1) << (2 * bit)
        result |= ((y >> bit) & 1) << (2 * bit + 1)
    if xb > paired:
        result |= (x >> paired) << (paired * 2)
    elif yb > paired:
        result |= (y >> paired) << (paired * 2)
    return result


def _unswizzle_p8(payload, width, height):
    size = width * height
    if len(payload) < size:
        raise PKXError(f"P8 mip {width}x{height}: need {size} bytes, have {len(payload)}")
    result = bytearray(size)
    for y in range(height):
        for x in range(width):
            result[y * width + x] = payload[_morton_offset(x, y, width, height)]
    return result


def _decode_palette(payload, name, offset):
    if len(payload) < 256 * 4:
        _fail(name, offset, f"P8 palette needs 1024 bytes, has {len(payload)}")
    # Xbox P8 palettes are stored as little-endian A8R8G8B8 words (BGRA bytes).
    entries = np.frombuffer(payload[:1024], dtype=np.uint8).reshape(256, 4)
    return entries[:, [2, 1, 0, 3]].copy()


def _parse_xpr0(xpr, name):
    if len(xpr) < 16 or xpr[:4] != b"XPR0":
        _fail(name, 0, "first package segment is not an XPR0 archive")
    total, header = struct.unpack_from("<II", xpr, 4)
    if total != len(xpr):
        _fail(name, 4, f"XPR0 size {total} does not match package segment size {len(xpr)}")
    if header < 16 or header > total or header % 4:
        _fail(name, 8, f"invalid XPR0 data-section base 0x{header:X}")

    resources = []
    pos = 12
    while True:
        if pos + 4 > header:
            _fail(name, pos, "XPR0 resource table has no terminator")
        common = struct.unpack_from("<I", xpr, pos)[0]
        if common == 0xFFFFFFFF:
            break
        kind = common & 0x00070000
        if kind == 0x00040000:
            descriptor_size = 20
        elif kind == 0x00030000:
            descriptor_size = 12
        else:
            _fail(name, pos, f"unsupported XPR0 resource type 0x{common:08X}")
        if pos + descriptor_size > header:
            _fail(name, pos, "resource descriptor exceeds XPR0 header")
        values = struct.unpack_from(f"<{descriptor_size // 4}I", xpr, pos)
        relative = values[1]
        if values[2] != 0:
            _fail(name, pos + 8, "nonzero resource Lock field is unsupported")
        tag = values[3] if kind == 0x00040000 else None
        size_word = values[4] if kind == 0x00040000 else 0
        resources.append(_Resource(kind, pos, relative, 0, tag, size_word))
        pos += descriptor_size

    data_size = total - header
    offsets = [resource.data_offset for resource in resources]
    if offsets != sorted(offsets) or len(set(offsets)) != len(offsets):
        _fail(name, 12, "resource offsets are not strictly increasing")
    for index, resource in enumerate(resources):
        if resource.data_offset >= data_size:
            _fail(name, resource.descriptor_offset + 4, "resource starts outside XPR0 data")
        end = offsets[index + 1] if index + 1 < len(offsets) else data_size
        resource.allocation = end - resource.data_offset
        if resource.allocation <= 0:
            _fail(name, resource.descriptor_offset + 4, "empty or overlapping resource allocation")
    return header, resources


def _decode_textures(xpr, name, header, resources):
    textures = []
    for resource_index, resource in enumerate(resources):
        if resource.kind != 0x00040000:
            continue
        tag = resource.tag
        code = (tag >> 8) & 0xFF
        levels = _mip_dimensions(tag, name=name, offset=resource.descriptor_offset + 12)
        offset = header + resource.data_offset
        payload = xpr[offset:offset + resource.allocation]
        if code in (0x0E, 0x0F):
            if resource.size_word:
                _fail(name, resource.descriptor_offset + 16,
                      "nonzero DXT resource Size is unsupported")
            try:
                images, info = decode_xpr0_texture(payload, tag)
            except XPR0Error as exc:
                raise PKXError(str(exc)) from exc
            format_name = info["format"]
        elif code == 0x0B:
            if resource.size_word:
                _fail(name, resource.descriptor_offset + 16,
                      "nonzero P8 resource Size is unsupported")
            if resource_index + 1 >= len(resources) or resources[resource_index + 1].kind != 0x00030000:
                _fail(name, resource.descriptor_offset,
                      "P8 texture is not followed by its palette resource")
            palette_resource = resources[resource_index + 1]
            palette_start = header + palette_resource.data_offset
            palette = _decode_palette(
                xpr[palette_start:palette_start + palette_resource.allocation],
                name, palette_start,
            )
            required = sum(width * height for width, height in levels)
            if required > resource.allocation:
                _fail(name, resource.descriptor_offset + 4,
                      f"P8 mip chain needs {required} bytes, allocation has {resource.allocation}")
            images, cursor = [], 0
            for width, height in levels:
                indices = _unswizzle_p8(payload[cursor:cursor + width * height], width, height)
                rgba = palette[np.frombuffer(indices, dtype=np.uint8)].reshape(height, width, 4)
                images.append(Image.fromarray(rgba, "RGBA"))
                cursor += width * height
            format_name = "P8"
        else:
            _fail(name, resource.descriptor_offset + 12,
                  f"unsupported Xbox pixel format 0x{code:02X}")
        textures.append({
            "index": len(textures),
            "resource_index": resource_index,
            "width": levels[0][0],
            "height": levels[0][1],
            "mip_levels": len(levels),
            "format": format_name,
            "image": images[0],
            "mip_images": images,
            "xpr_offset": offset,
            "xpr_size": resource.allocation,
            "xpr_tag": tag,
        })
    return textures


def decode_xpr0_archive(data, *, name="<XPR0 buffer>"):
    """Decode a complete Xbox XPR0 archive into its level-zero textures."""
    xpr = bytes(data)
    header, resources = _parse_xpr0(xpr, name)
    return _decode_textures(xpr, name, header, resources)


def _triangle_key(mesh, triangle):
    return tuple(sorted(
        tuple(round(float(component), 4) for component in mesh.positions[index])
        for index in triangle
    ))


def _suppress_duplicate_room_proxies(result):
    """Drop position-only room proxy triangles that duplicate textured surfaces."""
    mesh_records = []
    mesh_index = 0
    for entry_index, entry in enumerate(result.diagnostics["entries"]):
        for mesh_info in entry["meshes"]:
            mesh_records.append((entry_index, mesh_info, result.model.meshes[mesh_index]))
            mesh_index += 1

    candidates = [
        (entry_index, info, mesh) for entry_index, info, mesh in mesh_records
        if info["flags"] == 8 and mesh.indices
    ]
    if not candidates:
        return

    textured_triangles = set()
    for _, info, mesh in mesh_records:
        if info["flags"] & 1 and len(mesh.uvs) == len(mesh.positions):
            textured_triangles.update(_triangle_key(mesh, triangle) for triangle in mesh.indices)

    suppressed = set()
    report = []
    for entry_index, info, mesh in candidates:
        triangle_keys = [_triangle_key(mesh, triangle) for triangle in mesh.indices]
        matched = sum(key in textured_triangles for key in triangle_keys)
        coverage = matched / len(triangle_keys)
        if coverage < 0.98:
            continue
        suppressed.add(id(mesh))
        info["render_suppressed"] = "duplicate_position_only_room_proxy"
        report.append({
            "entry": entry_index,
            "block": info["block"],
            "offset": info["offset"],
            "triangles": len(triangle_keys),
            "matched_textured_triangles": matched,
            "coverage": round(coverage, 6),
        })

    if not suppressed:
        return
    result.model.meshes = [mesh for mesh in result.model.meshes if id(mesh) not in suppressed]
    result.diagnostics["pkx_suppressed_duplicate_proxies"] = report
    result.diagnostics["meshes"] = len(result.model.meshes)
    result.diagnostics["vertices"] = sum(len(mesh.positions) for mesh in result.model.meshes)
    result.diagnostics["triangles"] = sum(len(mesh.indices) for mesh in result.model.meshes)


def parse_pkx(data_or_path, *, name=None):
    """Read a framed PKX package with an embedded XPR0 and 0x1060 geometry."""
    if isinstance(data_or_path, (str, os.PathLike)):
        with open(data_or_path, "rb") as stream:
            data = stream.read()
        source_name = name or os.fspath(data_or_path)
    elif isinstance(data_or_path, (bytes, bytearray, memoryview)):
        data = bytes(data_or_path)
        source_name = name or "<PKX buffer>"
    else:
        raise TypeError("PKX source must be a path or bytes-like object")

    if len(data) < 16:
        _fail(source_name, 0, "truncated package header")
    segment_count = struct.unpack_from("<I", data)[0]
    if not 2 <= segment_count <= min(4096, (len(data) - 16) // 16):
        _fail(source_name, 0, f"invalid package segment count {segment_count}")
    if any(data[4:16]):
        _fail(source_name, 4, "unsupported package header flags")

    segments = []
    pos = 16
    for index in range(segment_count):
        if pos + 16 > len(data):
            _fail(source_name, pos, f"truncated segment {index} descriptor")
        size, kind, pad0, pad1 = struct.unpack_from("<4I", data, pos)
        if size == 0 or size % 16 or kind or pad0 or pad1:
            _fail(source_name, pos, f"malformed segment {index} descriptor")
        start = pos + 16
        if start > len(data) or size > len(data) - start:
            _fail(source_name, pos, f"segment {index} payload exceeds package")
        segments.append((pos, start, size))
        pos = start + size
    if data[pos:] != b"\xFF" * 16:
        _fail(source_name, pos, "missing final 16-byte FF descriptor")

    _, xpr_start, xpr_size = segments[0]
    xpr = data[xpr_start:xpr_start + xpr_size]
    header, resources = _parse_xpr0(xpr, source_name)
    try:
        geometry = bytearray(struct.pack("<4I", segment_count - 1, 0, 0, 0))
        for descriptor, start, size in segments[1:]:
            geometry += data[descriptor:descriptor + 16]
            geometry += data[start:start + size]
        geometry += b"\xFF" * 16
        result = parse_pkx_geometry(geometry, name=source_name)
    except XboxMPXError as exc:
        raise PKXError(str(exc)) from exc
    if not result.model.meshes:
        _fail(source_name, segments[1][0], "PKX package contains no supported geometry")
    _suppress_duplicate_room_proxies(result)

    textures = _decode_textures(xpr, source_name, header, resources)
    for material in result.model.materials:
        reference = material.xbox_resource_index
        if reference < 0:
            continue
        if reference >= len(textures):
            raise PKXError(
                f"{source_name}: material {material.name!r} references texture slot "
                f"{reference}, but only {len(textures)} texture resources exist"
            )
        material.texture_image = textures[reference]["image"]
    result.textures = [
        {key: value for key, value in texture.items() if key != "mip_images"}
        for texture in textures
    ]
    result.texture_names = [f"pkx_{texture['index']:03d}" for texture in textures]
    result.mipmaps = {texture["index"]: texture["mip_images"] for texture in textures}
    result.diagnostics.update({
        "parser": "xbox_pkx_native_v1",
        "source": source_name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "package_segments": segment_count,
        "xpr0_resources": len(resources),
        "xpr0_textures": len(textures),
        "xpr0_palettes": sum(r.kind == 0x00030000 for r in resources),
        "xpr0_formats": dict((fmt, sum(t["format"] == fmt for t in textures))
                             for fmt in sorted({t["format"] for t in textures})),
        "xpr0_embedded_bytes": xpr_size,
    })
    result.model.xbox_native_mpx = False
    result.model.xbox_native_pkx = True
    return result


__all__ = ["PKXError", "decode_xpr0_archive", "parse_pkx"]
