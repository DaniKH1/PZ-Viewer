"""Fatal Frame 2 Xbox MDL/PK2 model and PPD buffer reader."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import os
from pathlib import Path
import struct

from pz_core.ff1x.pz_mpx_ff1x import XboxMPXError, parse_pkx_geometry
from pz_core.ff1x.pz_pkx_ff1x import PKXError, decode_xpr0_archive


MODEL_VERSION = 0x1070
PPD_MAGIC = b"pk2\0"
MODEL_TAG = b"pk2\0"
MDL_TAG = b"pk3\0"
_MAX_DIRECTORY_ENTRIES = 4096


class FF2XError(ValueError):
    """Malformed or unsupported Fatal Frame 2 Xbox model package."""


@dataclass
class FF2XModelResult:
    model: object
    diagnostics: dict
    textures: list


def _read_source(data_or_path, name):
    if isinstance(data_or_path, (str, os.PathLike)):
        path = Path(data_or_path)
        try:
            return path.read_bytes(), str(path), path
        except OSError as exc:
            raise FF2XError(f"{path}: {exc}") from exc
    try:
        return bytes(data_or_path), name, None
    except (TypeError, ValueError) as exc:
        raise FF2XError(f"{name}: source must be a path or bytes-like data") from exc


def _directory(data, source, expected_tag):
    if len(data) < 16:
        raise FF2XError(f"{source}: truncated {expected_tag[:3].decode('ascii')} header")
    count = struct.unpack_from("<I", data)[0]
    if data[4:8] != expected_tag:
        raise FF2XError(f"{source}: expected {expected_tag!r} container tag")
    if not 1 <= count <= min(_MAX_DIRECTORY_ENTRIES, (len(data) - 16) // 4):
        raise FF2XError(f"{source}: invalid directory count {count}")
    offsets = struct.unpack_from(f"<{count}I", data, 16)
    directory_end = 16 + count * 4
    nonzero = [offset for offset in offsets if offset]
    if any(offset < directory_end or offset > len(data) - 4 for offset in nonzero):
        raise FF2XError(f"{source}: directory offset outside container")
    if len(set(nonzero)) != len(nonzero):
        raise FF2XError(f"{source}: duplicate directory offsets")
    return offsets


def _model_records(data, source, suffix):
    records = []
    if suffix == ".mdl":
        if len(data) < 0x44 or data[4:8] != MDL_TAG:
            raise FF2XError(f"{source}: expected FF2 Xbox PK3/MDL container")
        texture_start = data.find(b"xvd\0", 0x40)
        region_end = texture_start if texture_start >= 0 else len(data)
        offsets = [
            offset for offset in range(0x40, region_end - 39, 4)
            if struct.unpack_from("<I", data, offset)[0] == MODEL_VERSION
        ]
        for index, offset in enumerate(offsets):
            end = offsets[index + 1] if index + 1 < len(offsets) else region_end
            if end - offset < 40:
                continue
            header = struct.unpack_from("<10I", data, offset)
            vertex_offset, vertex_size, block_count = header[4], header[5], header[9]
            if (vertex_size and 2 <= block_count <= 4097
                    and 40 + block_count * 4 <= vertex_offset <= end - offset):
                records.append((offset, data[offset:end]))
    else:
        offsets = _directory(data, source, MODEL_TAG)
        for offset in offsets:
            if offset and struct.unpack_from("<I", data, offset)[0] == MODEL_VERSION:
                records.append((offset, data[offset:]))
    if not records:
        raise FF2XError(f"{source}: no FF2 Xbox 0x1070 model record found")
    return records


def _xpr0_archives(data, source):
    archives = []
    search_from = 0
    while True:
        offset = data.find(b"XPR0", search_from)
        if offset < 0:
            break
        search_from = offset + 4
        if offset + 12 > len(data):
            raise FF2XError(f"{source}: truncated XPR0 header at 0x{offset:X}")
        total_size, header_size = struct.unpack_from("<II", data, offset + 4)
        if not 16 <= header_size <= 256 * 1024 * 1024 or total_size < header_size:
            raise FF2XError(f"{source}: invalid XPR0 sizes at 0x{offset:X}")
        cursor = offset + 12
        while cursor + 4 <= len(data):
            common = struct.unpack_from("<I", data, cursor)[0]
            if common == 0xFFFFFFFF:
                cursor += 4
                break
            resource_type = common & 0x00070000
            descriptor_size = 20 if resource_type == 0x00040000 else (
                12 if resource_type == 0x00030000 else 0
            )
            if not descriptor_size or cursor + descriptor_size > len(data):
                raise FF2XError(
                    f"{source}: malformed XPR0 resource descriptor at 0x{cursor:X}"
                )
            cursor += descriptor_size
        else:
            raise FF2XError(f"{source}: XPR0 resource table has no terminator")
        if cursor > offset + header_size:
            raise FF2XError(f"{source}: XPR0 descriptors exceed header at 0x{offset:X}")
        archives.append({
            "offset": offset,
            "total_size": total_size,
            "header_size": header_size,
            "header": data[offset:cursor],
        })
    return archives


def _texture_archive_data_offsets(archives, ppd_data, ppd_source):
    offsets = [0x100] * len(archives)
    if len(archives) <= 1:
        return offsets

    directory_offsets = _directory(ppd_data, ppd_source, PPD_MAGIC)
    explicit_payload_offsets = []
    for block_offset in sorted(offset for offset in directory_offsets if offset):
        if block_offset + 16 > len(ppd_data):
            raise FF2XError(
                f"{ppd_source}: truncated PPD block at 0x{block_offset:X}"
            )
        header_size, tag, payload_offset, payload_size = struct.unpack_from(
            "<I4sII", ppd_data, block_offset
        )
        if header_size != 16:
            raise FF2XError(
                f"{ppd_source}: invalid PPD block header size at 0x{block_offset:X}"
            )
        if tag == b"xpd\0" and payload_size:
                explicit_payload_offsets.append(payload_offset)

    if len(explicit_payload_offsets) != len(archives):
        raise FF2XError(
            f"{ppd_source}: {len(archives)} XPR0 archives require matching "
                f"PPD texture references; found {len(explicit_payload_offsets)} explicit ranges"
        )

    for index in range(1, len(archives)):
        data_size = archives[index]["total_size"] - archives[index]["header_size"]
        data_offset = explicit_payload_offsets[index]
        if data_offset > len(ppd_data) or data_size > len(ppd_data) - data_offset:
            raise FF2XError(
                f"{ppd_source}: XPR0 archive {index} needs 0x{data_size:X} bytes "
                f"at 0x{data_offset:X}, outside the PPD"
            )
        offsets[index] = data_offset
    return offsets


def _decode_archives(archives, ppd_data, source, ppd_source, data_offsets):
    decoded = []
    for archive_index, archive in enumerate(archives):
        data_size = archive["total_size"] - archive["header_size"]
        data_offset = data_offsets[archive_index]
        available_size = max(0, len(ppd_data) - data_offset)
        if data_size > available_size + 16:
            raise FF2XError(
                f"{ppd_source}: XPR0 archive {archive_index} needs "
                f"0x{data_size:X} bytes at 0x{data_offset:X}, outside the PPD"
            )
        virtual = bytearray(archive["total_size"])
        virtual[:len(archive["header"])] = archive["header"]
        virtual[4:12] = struct.pack(
            "<II", archive["total_size"], archive["header_size"]
        )
        source_end = min(len(ppd_data), data_offset + data_size)
        virtual[archive["header_size"]:archive["header_size"] + source_end - data_offset] = \
            ppd_data[data_offset:source_end]
        try:
            textures = decode_xpr0_archive(virtual, name=source)
        except (PKXError, ValueError, struct.error) as exc:
            raise FF2XError(f"{source}: could not decode XPR0 texture archive: {exc}") from exc
        decoded.append(textures)
    return decoded


def _ppd_vertex_buffers(data, source):
    offsets = _directory(data, source, PPD_MAGIC)
    ordered = sorted(offset for offset in offsets if offset)
    buffers = []
    for index, offset in enumerate(ordered):
        if offset + 16 > len(data):
            raise FF2XError(f"{source}: truncated PPD block at 0x{offset:X}")
        header_size, tag, field_a, field_b = struct.unpack_from("<I4sII", data, offset)
        if header_size != 16:
            raise FF2XError(f"{source}: invalid PPD block header size at 0x{offset:X}")
        if tag != b"xpd\0":
            continue
        if field_b:
            payload_offset, payload_size = field_a, field_b
        else:
            total_size = field_a
            next_offset = ordered[index + 1] if index + 1 < len(ordered) else len(data)
            if total_size < 16 or total_size > next_offset - offset:
                raise FF2XError(f"{source}: invalid PPD block size at 0x{offset:X}")
            payload_offset, payload_size = offset + 16, total_size - 16
        if payload_offset < 0 or payload_size < 0 or payload_offset > len(data) \
                or payload_size > len(data) - payload_offset:
            raise FF2XError(f"{source}: PPD payload outside file at 0x{offset:X}")
        buffers.append({
            "block_offset": offset,
            "payload_offset": payload_offset,
            "payload_size": payload_size,
            "data": data[payload_offset:payload_offset + payload_size],
        })
    return buffers


def _build_geometry_entry(record, ppd_buffer, source, record_offset):
    if len(record) < 40:
        raise FF2XError(f"{source}: truncated 0x1070 model header at 0x{record_offset:X}")
    version, _, _, _, vertex_offset, vertex_size = struct.unpack_from("<6I", record)
    if version != MODEL_VERSION:
        raise FF2XError(f"{source}: expected model version 0x1070")
    if vertex_offset < 40 or vertex_offset > len(record):
        raise FF2XError(f"{source}: invalid vertex-buffer offset 0x{vertex_offset:X}")
    if vertex_size != len(ppd_buffer):
        raise FF2XError(f"{source}: PPD buffer length does not match 0x1070 header")
    segment = bytearray(record[:vertex_offset])
    segment.extend(ppd_buffer)
    segment[:4] = struct.pack("<I", 0x1060)
    segment.extend(b"\0" * (-len(segment) % 16))
    return (
        struct.pack("<I12x", 1)
        + struct.pack("<4I", len(segment), 0, 0, 0)
        + segment
        + b"\xff" * 16
    )


def _restore_source_orientation(model):
    """Undo the FF1-only facing rotation performed by the shared 0x1060 reader."""
    for mesh in model.meshes:
        mesh.positions = [[-p[0], p[1], -p[2]] for p in mesh.positions]
        mesh.normals = [[-n[0], n[1], -n[2]] for n in mesh.normals]
    for bone in model.bones:
        matrix = list(bone.matrix)
        if len(matrix) >= 16:
            for index in (0, 1, 8, 9, 12, 14):
                matrix[index] = -matrix[index]
            bone.matrix = matrix
        if bone.trans and len(bone.trans) >= 3:
            bone.trans = [-bone.trans[0], bone.trans[1], -bone.trans[2]]


def _merge_model(base, extra):
    material_offset = len(base.materials)
    bone_offset = len(base.bones)
    for material in extra.materials:
        clone = copy.copy(material)
        clone.index = material.index + material_offset
        base.materials.append(clone)
    for bone in extra.bones:
        clone = copy.copy(bone)
        clone.index = bone.index + bone_offset
        clone.parent = bone.parent + bone_offset if bone.parent >= 0 else -1
        base.bones.append(clone)
    for mesh in extra.meshes:
        mesh.material_index += material_offset
        mesh.bone_index += bone_offset
        if mesh.joints:
            mesh.joints = [
                [
                    joint + bone_offset if weight > 0 else 0
                    for joint, weight in zip(joints, weights)
                ]
                for joints, weights in zip(mesh.joints, mesh.weights)
            ]
        base.meshes.append(mesh)
    return base


def _is_origin_helper(mesh, mesh_info):
    if (
        mesh_info["category"] != 1
        or mesh_info["flags"] != 0
        or mesh_info["vertex_count"] != 12
        or len(mesh.indices) != 4
        or mesh.uvs
    ):
        return False
    points = {
        tuple(round(float(value), 6) for value in position)
        for position in mesh.positions
    }
    return len(points) == 4 and (0.0, 0.0, 0.0) in points


def parse_ff2x_model(data_or_path, *, ppd=None, name=None):
    """Parse an FF2 Xbox .mdl or .pk2 and its required same-stem .ppd."""
    data, source, path = _read_source(data_or_path, name or "ff2x_model")
    suffix = path.suffix.casefold() if path else ""
    if suffix not in (".mdl", ".pk2"):
        raise FF2XError(f"{source}: only FF2 Xbox .mdl and .pk2 models are supported")
    display_name = name or (path.stem if path else "ff2x_model")
    records = _model_records(data, source, suffix)
    if ppd is None:
        if path is None:
            raise FF2XError(f"{source}: a companion .ppd path is required for byte input")
        ppd_path = path.with_suffix(".ppd")
        if not ppd_path.is_file():
            raise FF2XError(f"{source}: companion PPD not found: {ppd_path.name}")
        try:
            ppd_data, ppd_source = ppd_path.read_bytes(), str(ppd_path)
        except OSError as exc:
            raise FF2XError(f"{ppd_path}: {exc}") from exc
    else:
        ppd_data, ppd_source, _ = _read_source(ppd, f"{source}.ppd")

    buffers = _ppd_vertex_buffers(ppd_data, ppd_source)
    archives = _xpr0_archives(data, source)
    texture_data_offsets = _texture_archive_data_offsets(
        archives, ppd_data, ppd_source
    )
    decoded_archives = _decode_archives(
        archives, ppd_data, source, ppd_source, texture_data_offsets
    )
    textures = []
    archive_texture_offsets = []
    for archive_textures in decoded_archives:
        archive_texture_offsets.append(len(textures))
        textures.extend(texture["image"] for texture in archive_textures)

    merged_model = None
    parsed_records = []
    buffers_used = set()
    suppressed_origin_helpers = 0
    for entry_index, (record_offset, record) in enumerate(records):
        if len(record) < 24:
            raise FF2XError(f"{source}: truncated 0x1070 model record at 0x{record_offset:X}")
        vertex_size = struct.unpack_from("<I", record, 20)[0]
        matches = [
            (buffer_index, buffer) for buffer_index, buffer in enumerate(buffers)
            if buffer_index not in buffers_used and buffer["payload_size"] == vertex_size
        ]
        if not matches:
            raise FF2XError(
                f"{source}: no PPD xpd buffer matches required vertex size 0x{vertex_size:X}"
            )
        parsed = None
        selected = None
        selected_index = None
        errors = []
        for buffer_index, candidate in matches:
            try:
                wrapped = _build_geometry_entry(
                    record, candidate["data"], source, record_offset
                )
                parsed = parse_pkx_geometry(
                    wrapped,
                    name=f"{display_name}_{entry_index:02d}",
                    allow_auxiliary_category14=True,
                )
            except (XboxMPXError, FF2XError) as exc:
                errors.append(str(exc))
                continue
            selected = candidate
            selected_index = buffer_index
            buffers_used.add(buffer_index)
            break
        if parsed is None:
            reason = errors[-1] if errors else "no candidate PPD buffer parsed"
            raise FF2XError(f"{source}: unsupported 0x1070 geometry: {reason}")
        _restore_source_orientation(parsed.model)
        mesh_info = parsed.diagnostics["entries"][0]["meshes"]
        kept_meshes = []
        for mesh, info in zip(parsed.model.meshes, mesh_info):
            if _is_origin_helper(mesh, info):
                info["render_suppressed"] = "untextured_origin_helper"
                suppressed_origin_helpers += 1
            else:
                kept_meshes.append(mesh)
        parsed.model.meshes = kept_meshes
        archive_index = 0
        if suffix == ".pk2" and archives:
            preceding = [
                index for index, archive in enumerate(archives)
                if archive["offset"] < record_offset
            ]
            if preceding:
                archive_index = preceding[-1]
        texture_offset = archive_texture_offsets[archive_index] if decoded_archives else 0
        for material in parsed.model.materials:
            resource_index = getattr(material, "xbox_resource_index", -1)
            material.texture_index = (
                texture_offset + resource_index
                if 0 <= resource_index < len(decoded_archives[archive_index])
                else -1
            ) if decoded_archives else -1
        auxiliary_commands = parsed.diagnostics["entries"][0].get(
            "auxiliary_commands", []
        )
        if merged_model is None:
            merged_model = parsed.model
        else:
            _merge_model(merged_model, parsed.model)
        parsed_records.append({
            "record_offset": record_offset,
            "vertex_buffer_size": vertex_size,
            "ppd_buffer_offset": selected["payload_offset"],
            "ppd_buffer_index": selected_index,
            "texture_archive": archive_index if decoded_archives else None,
            "meshes": len(parsed.model.meshes),
            "materials": len(parsed.model.materials),
            "auxiliary_commands": auxiliary_commands,
        })
    if merged_model is None or not merged_model.meshes:
        raise FF2XError(f"{source}: model package contains no renderable geometry")
    merged_model.uvs_are_flipped = True
    diagnostics = {
        "format": "ff2x_1070",
        "model_container": suffix.lstrip("."),
        "model_records": parsed_records,
        "ppd_source": os.path.basename(ppd_source),
        "ppd_blocks": len(buffers),
        "geometry": {
            "meshes": len(merged_model.meshes),
            "materials": len(merged_model.materials),
            "bones": len(merged_model.bones),
        },
        "texture_archives": len(decoded_archives),
        "texture_data_offsets": texture_data_offsets,
        "textures_decoded": len(textures),
        "texture_status": "decoded" if textures else "not_found",
        "suppressed_origin_helpers": suppressed_origin_helpers,
    }
    merged_model.parse_diagnostics = diagnostics
    return FF2XModelResult(merged_model, diagnostics, textures)


__all__ = ["FF2XError", "FF2XModelResult", "parse_ff2x_model"]
