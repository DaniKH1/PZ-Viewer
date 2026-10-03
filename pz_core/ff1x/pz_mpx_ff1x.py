"""Native FF1 Xbox MPX/0x1060 reader; never calls a PS2 geometry parser.

Supported layouts are evidenced by the miku4 and miku5 MPX/XPR pairs.
All pointers, pools, vertex declarations and strips are explicit: there is
no geometry substitution, pool scanning, UV fabrication or guessed topology.
Only the viewer's existing model *data classes* are shared with other formats.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import math
import os
import re
import struct

import numpy as np

from pz_core.common.model import (
    SGDBone,
    SGDMaterial,
    SGDMesh,
    SGDModel,
    face_along_positive_z,
)


from pz_core.ff1x.pz_xpr0 import parse_xpr0, parse_xpr0_records

# Bits of a mesh command's vertex declaration that this parser understands:
# bit 0 = the record carries a UV pair (32-byte stride), bit 2 = the mesh is
# weighted across two bones. All four combinations occur in real files, so the
# guard tests for unknown bits rather than for a list of whole values.
KNOWN_DECLARATION_BITS = 0b101


class XboxMPXError(ValueError):
    """A contextual structural error or an explicitly unsupported MPX layout."""


@dataclass
class XboxAsset:
    model: object = None
    textures: list = field(default_factory=list)
    mipmaps: dict = field(default_factory=dict)
    texture_names: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


class _Reader:
    def __init__(self, data, name, base=0, section="MPX"):
        self.data, self.name, self.base, self.section = data, name, base, section

    def fail(self, offset, message):
        raise XboxMPXError(f"{self.name}: {self.section} @0x{self.base + offset:X} "
                           f"(section +0x{offset:X}): {message}")

    def require(self, offset, size, label="range"):
        if offset < 0 or size < 0 or offset > len(self.data) or size > len(self.data) - offset:
            self.fail(offset, f"{label}: {size} bytes outside section of {len(self.data)} bytes")

    def values(self, offset, code, count=1):
        size = struct.calcsize("<" + code) * count
        self.require(offset, size, f"{count} {code} values")
        values = struct.unpack_from(f"<{count}{code}", self.data, offset)
        if code == "f" and not all(math.isfinite(v) for v in values):
            self.fail(offset, "non-finite floating-point attribute")
        return values

    def u32(self, offset):
        return self.values(offset, "I")[0]


def _source(source, default):
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        return path.read_bytes(), str(path), path
    return bytes(source), default, None


def triangle_strip(indices):
    """Alternating strip winding; degenerates still advance strip parity."""
    result = []
    for i in range(2, len(indices)):
        a, b, c = indices[i - 2:i + 1]
        if len({a, b, c}) < 3:
            continue
        result.append([b, a, c] if i & 1 else [a, b, c])
    return result


def xbox_bind_rotation(euler_xyz):
    """Row-vector bind basis Rx(-x) @ Ry(-y) @ Rz(-z).

    Equivalently, column vectors use Rz(z) @ Ry(y) @ Rx(x). The stored
    scaled 3x3 in the entry is NOT substituted for this Euler-derived basis.
    Both local copies of weighted vertices independently verify this order.
    """
    x, y, z = (-float(v) for v in euler_xyz)
    cx, cy, cz, sx, sy, sz = math.cos(x), math.cos(y), math.cos(z), math.sin(x), math.sin(y), math.sin(z)
    rx = np.array([[1., 0., 0.], [0., cx, -sx], [0., sx, cx]])
    ry = np.array([[cy, 0., sy], [0., 1., 0.], [-sy, 0., cy]])
    rz = np.array([[cz, -sz, 0.], [sz, cz, 0.], [0., 0., 1.]])
    return rx @ ry @ rz


def _bones(r, start, count, stride=224, parent_offset=208):
    r.require(start, count * stride, "coordinate table")
    parents, matrices, rotations, translations, raw_matrices = [], [], [], [], []
    for i in range(count):
        p = start + i * stride
        raw = list(r.values(p, "f", 16))
        angles = list(r.values(p + 192, "f", 4))
        parent = r.values(p + parent_offset, "i")[0]
        if stride == 228 and r.u32(p + 224):
            r.fail(p + 224, "nonzero coordinate-record extension")
        if parent < -1 or parent >= count or parent == i:
            r.fail(p + parent_offset, f"invalid parent {parent} for bone {i}")
        basis = xbox_bind_rotation(angles[:3])
        matrix = np.eye(4)
        matrix[:3, :3] = basis
        matrix[3, :3] = raw[12:15]
        parents.append(parent)
        matrices.append(matrix)
        rotations.append(angles)
        translations.append(raw[12:15])
        raw_matrices.append(raw)
    # Forward parents occur in the actual files. Validate without recursive depth limits.
    checked = set()
    for i in range(count):
        chain = set()
        at = i
        while at != -1 and at not in checked:
            if at in chain:
                r.fail(start + at * stride + parent_offset, "cycle in coordinate hierarchy")
            chain.add(at)
            at = parents[at]
        checked.update(chain)
    return parents, np.array(matrices), rotations, translations, raw_matrices


def _normal(vector):
    length = float(np.linalg.norm(vector))
    # A genuinely zero source normal stays zero, rather than inventing a direction.
    return (vector / length).tolist() if length > 1e-12 else vector.tolist()


def _entry(
    r,
    entry_index,
    model,
    skeletons,
    pose_template=None,
    pkx_compat=False,
    allow_auxiliary_category14=False,
):
    h = r.values(0, "I", 10)
    ver, unknown1, unknown2, material_count, vb, vb_size, bone_ptr, mat_ptr, pool_ptr, block_count = h
    if ver != 0x1060:
        r.fail(0, f"unsupported entry version 0x{ver:X}; expected Xbox 0x1060")
    if not 2 <= block_count <= min(4097, len(r.data) // 4):
        r.fail(36, f"invalid block count {block_count}")
    blocks = r.values(40, "I", block_count)
    r.require(vb, vb_size, "GPU vertex buffer")
    bone_count = block_count - 1
    bone_table_size = mat_ptr - bone_ptr
    if bone_table_size % bone_count:
        r.fail(bone_ptr, "coordinate table size is not divisible by bone count")
    bone_stride = bone_table_size // bone_count
    if bone_stride not in ((224, 228) if pkx_compat else (224,)):
        r.fail(bone_ptr, f"unsupported coordinate-record stride {bone_stride}")
    parent_offset = 208
    if bone_ptr < 40 + block_count * 4 or mat_ptr < bone_ptr + bone_count * bone_stride:
        r.fail(24, "coordinate/material tables overlap header or each other")
    if not 0 < material_count <= 4096:
        r.fail(12, f"invalid material count {material_count}")
    r.require(mat_ptr, material_count * 144, "material table")
    if pool_ptr and (
        pool_ptr < mat_ptr + material_count * 144 or pool_ptr + 64 > vb
    ):
        r.fail(32, "pool descriptor overlaps materials or GPU data")
    descriptor_size = 64
    ph = r.values(pool_ptr, "I", 16) if pool_ptr else (0,) * 16
    if pool_ptr:
        if pkx_compat:
            compact = r.values(pool_ptr, "I", 13)
            if any(value == pool_ptr + 52 for value in compact[2:4] + compact[10:13] if value):
                ph = compact + (0, 0, 0)
                descriptor_size = 52
        if ph[0] != 3 or any(ph[i] != 4 for i in (1, 5, 9)):
            r.fail(pool_ptr, "unsupported source-pool descriptor layout")
        if any(ph[i] for i in (4, 6, 7, 8)):
            r.fail(pool_ptr, "unsupported additional source pools")
    pointers = [p for p in blocks if p]
    if len(set(pointers)) != len(pointers):
        r.fail(40, "duplicate block chain pointers")
    for p in pointers:
        if p % 4 or p < pool_ptr + 64 or p + 4 > vb:
            r.fail(p, "block pointer outside command section or misaligned")
    pool_specs = {"unique_positions": (ph[2], 16), "unique_normals": (ph[3], 16),
                  "weighted_positions": (ph[10], 32), "weighted_normals": (ph[11], 32)}
    first_block = min(pointers, default=vb)
    for label, (pointer, _) in pool_specs.items():
        if pointer and (
            pointer < pool_ptr + descriptor_size
            or pointer >= first_block
            or pointer % 4
        ):
            r.fail(pointer, f"{label} pointer outside source-pool region")
    pools = {label: {"offset": pointer, "size": 0, "count": 0, "stride": stride}
             for label, (pointer, stride) in pool_specs.items()}

    def group_tables(start, limit, weighted):
        """Two counted runs: positions then normals; each run entry is 8 bytes."""
        at, tables = start, []
        for semantic in ("positions", "normals"):
            if at + 4 > limit:
                r.fail(at, "truncated source group count")
            count = r.u32(at)
            if not 1 <= count <= (limit - at - 4) // 8:
                r.fail(at, f"invalid {semantic} group count {count}")
            at += 4
            groups_out, total = [], 0
            for _ in range(count):
                if weighted:
                    b0, b1 = r.values(at, "H", 2)
                else:
                    b0 = b1 = r.u32(at)
                length = r.u32(at + 4)
                if b0 >= bone_count or b1 >= bone_count or not length:
                    r.fail(at, "invalid source group bone/count")
                groups_out.append({"bone0": b0, "bone1": b1, "count": length,
                                   "first_index": total})
                total += length
                at += 8
            tables.append({"semantic": semantic, "count": total, "groups": groups_out})
        if at != limit:
            r.fail(at, "source groups do not end at the next known section")
        return tables

    group_info = {}
    if bool(ph[10]) != bool(ph[11]) or bool(ph[10]) != bool(ph[12]):
        r.fail(pool_ptr + 40, "incomplete weighted source pointers")
    if ph[12]:
        if not pool_ptr + 64 <= ph[12] < first_block:
            r.fail(ph[12], "weighted group pointer outside source region")
        tables = group_tables(ph[12], first_block, True)
        group_info["weighted"] = {"offset": ph[12], "size": first_block - ph[12], "tables": tables}
        for table in tables:
            pool = pools["weighted_" + table["semantic"]]
            pool["count"] = table["count"]
            pool["size"] = pool["count"] * 32
            if pool["offset"] + pool["size"] > ph[12]:
                r.fail(pool["offset"], "weighted source count exceeds its data region")
    if bool(ph[2]) != bool(ph[3]):
        r.fail(pool_ptr + 8, "incomplete unique source pointers")
    if ph[2]:
        limit = ph[12] or first_block
        if ph[12]:
            # Weighted counts locate the following unique group tables exactly.
            table_start = max(pools[k]["offset"] + pools[k]["size"]
                              for k in ("weighted_positions", "weighted_normals"))
            tables = group_tables(table_start, limit, False)
        else:
            # No pointer is stored for unique groups. Solve the bounded table
            # boundary from its counts AND exact pool byte sizes, not float scans.
            candidates = []
            lower = max(ph[3], limit - (8 + 16 * bone_count))
            for candidate in range((lower + 3) // 4 * 4, limit, 4):
                try:
                    found = group_tables(candidate, limit, False)
                except XboxMPXError:
                    continue
                if (ph[2] + found[0]["count"] * 16 == ph[3]
                        and ph[3] + found[1]["count"] * 16 == candidate):
                    candidates.append((candidate, found))
            if len(candidates) != 1:
                r.fail(ph[3], f"unique group boundary has {len(candidates)} structural solutions")
            table_start, tables = candidates[0]
        group_info["unique"] = {"offset": table_start, "size": limit - table_start, "tables": tables}
        for table in tables:
            pool = pools["unique_" + table["semantic"]]
            pool["count"] = table["count"]
            pool["size"] = pool["count"] * 16
            if pool["offset"] + pool["size"] > table_start:
                r.fail(pool["offset"], "unique source count exceeds its data region")
    elif ph[12]:
        pool_end = max(pools[k]["offset"] + pools[k]["size"] for k in
                       ("weighted_positions", "weighted_normals"))
        padding_size = ph[12] - pool_end
        max_padding = 15 if pkx_compat else 0
        if (padding_size < 0 or padding_size > max_padding
                or any(r.data[pool_end:ph[12]])):
            r.fail(ph[12], "unexplained gap between source buffers and weighted groups")
    source_intervals = sorted((v["offset"], v["offset"] + v["size"]) for v in pools.values() if v["offset"])
    for (begin, end), (next_begin, _) in zip(source_intervals, source_intervals[1:]):
        if end != next_begin:
            r.fail(end, "source buffers overlap or have unexplained intervening bytes")
    groups = group_info
    parents, matrices, angles, translations, raw_matrices = _bones(
        r, bone_ptr, bone_count, stride=bone_stride, parent_offset=parent_offset
    )
    if pose_template is not None:
        if parents != pose_template["parents"]:
            r.fail(bone_ptr, "reference bind-pose skeleton topology does not match")
        matrices = pose_template["matrices"]
        angles = pose_template["angles"]
        translations = pose_template["translations"]
    # Preserve distinct entry bind poses; only byte-equivalent decoded skeletons share joints.
    key = (tuple(parents), tuple(matrices.flatten()))
    bone_base = skeletons.get(key)
    if bone_base is None:
        bone_base = len(model.bones)
        skeletons[key] = bone_base
        for i in range(bone_count):
            bone = SGDBone(bone_base + i, bone_base + parents[i] if parents[i] >= 0 else 0)
            bone.name = f"entry{entry_index:02d}_bone{i:02d}"
            bone.matrix = matrices[i].flatten().tolist()
            bone.rot, bone.trans = angles[i], translations[i]
            bone.xbox_raw_matrix = raw_matrices[i]
            model.bones.append(bone)
    rotations = matrices[:, :3, :3]
    translations = matrices[:, 3, :3]
    mat_base = len(model.materials)
    material_records = []
    for i in range(material_count):
        p = mat_ptr + i * 144
        texture = r.u32(p + 72)
        raw_name = bytes(r.data[p + 76:p + 108]).split(b"\0", 1)[0]
        text = raw_name.decode("ascii", errors="backslashreplace")
        mat = SGDMaterial(mat_base + i, text or f"entry{entry_index:02d}_material{i:02d}")
        mat.diffuse, mat.ambient = list(r.values(p, "f", 4)), list(r.values(p + 16, "f", 4))
        mat.specular, mat.emission = list(r.values(p + 32, "f", 4)), list(r.values(p + 48, "f", 4))
        mat.shininess = r.values(p + 64, "f")[0]
        mat.texture_index = -1 if texture == 0xFFFFFFFF else texture
        mat.xbox_resource_index = mat.texture_index
        mat.xbox_flags = r.u32(p + 140)
        model.materials.append(mat)
        material_records.append({"offset": p, "name": mat.name, "texture_index": mat.texture_index,
                                 "flags": mat.xbox_flags, "unknown_44": r.u32(p + 68)})
    stats = {"entry": entry_index, "file_offset": r.base, "size": len(r.data),
             "version": ver, "header_unknown": [unknown1, unknown2],
             "gpu_buffer_offset": vb, "gpu_buffer_size": vb_size,
             "coordinate_offset": bone_ptr, "bone_count": bone_count, "bone_base": bone_base,
             "material_offset": mat_ptr, "materials": material_records,
             "pool_descriptor_offset": pool_ptr, "pools": pools, "group_table": groups,
             "block_count": block_count, "blocks_visited": 0,
             "meshes": [], "mapped_vertices": 0, "weighted_vertices": 0,
             "non_geometry_commands": [], "auxiliary_commands": [],
             "gpu_source_position_max_error": 0., "gpu_source_normal_max_error": 0.,
             "weighted_bind_pair_max_error": 0., "bounding_boxes": [], "warnings": []}
    ranges = []

    def source_value(pool_label, index):
        pool = pools[pool_label]
        if index >= pool["count"]:
            r.fail(pool["offset"], f"{pool_label} index {index} outside {pool['count']} entries")
        p = pool["offset"] + index * pool["stride"]
        kind = "weighted" if pool["stride"] == 32 else "unique"
        semantic = 0 if pool_label.endswith("positions") else 1
        table = group_info[kind]["tables"][semantic]
        group = next(g for g in table["groups"]
                     if g["first_index"] <= index < g["first_index"] + g["count"])
        if pool["stride"] == 16:
            return np.array(r.values(p, "f", 3)), group["bone0"]
        floats = r.values(p, "f", 7)
        b0, b1, pad = struct.unpack_from("<BBH", r.data, p + 28)
        if (b0 >= bone_count or b1 >= bone_count or not 0. <= floats[3] <= 1. or pad
                or (b0, b1) != (group["bone0"], group["bone1"])):
            r.fail(p, f"invalid weighted source (bones {b0}/{b1}, weight {floats[3]}, pad {pad})")
        return np.array(floats[:3]), (np.array(floats[4:7]), floats[3], b0, b1)

    for block_id, start in enumerate(blocks):
        if not start:
            continue
        stats["blocks_visited"] += 1
        limit = min([p for p in pointers if p > start] + [vb])
        p, coord, coord_mode, material = start, None, None, None
        while True:
            if p + 4 > limit:
                r.fail(p, f"block {block_id} missing 4-byte terminator")
            size = r.u32(p)
            if size == 0:
                if any(r.data[p + 4:limit]):
                    r.fail(p + 4, "unexplained nonzero bytes after block terminator")
                break
            if size < 8 or size % 4 or size > limit - p:
                r.fail(p, f"invalid command length 0x{size:X} in block {block_id}")
            category = r.u32(p + 4)
            if category == 3:
                if size != 16:
                    r.fail(p, "unexpected coordinate command size")
                coord, coord_mode = r.values(p + 8, "I", 2)
                if coord >= bone_count or coord_mode not in (0, 1):
                    r.fail(p + 8, "invalid coordinate/mode")
            elif category == 4:
                if size != 144:
                    r.fail(p, "unexpected bounding-box command size")
                box_bone = r.u32(p + 8)
                if box_bone >= bone_count:
                    r.fail(p + 8, "invalid bounding-box bone")
                corners = [list(r.values(p + 16 + j * 16, "f", 4)) for j in range(8)]
                # Raw bounds are kept for analysis; do not guess their renderer semantics.
                stats["bounding_boxes"].append({"offset": p, "bone": box_bone, "corners": corners})
            elif category == 2:
                if size != 12:
                    r.fail(p, "unexpected material command size")
                material = r.u32(p + 8)
                if material >= material_count:
                    r.fail(p + 8, f"material {material} outside {material_count} entries")
            elif category in (0, 1):
                if size < 56 or coord is None or material is None:
                    r.fail(p, "mesh has no complete header/coordinate/material state")
                if category == 0 and not pool_ptr:
                    r.fail(p, "source-mapped mesh has no source-pool descriptor")
                fields = r.values(p, "I", 14)
                voffset, vertex_count, ip, index_count, flags = (fields[i] for i in (3, 5, 7, 9, 11))
                # PKX room declarations 0x8/0x9 are position-only streams
                # (three floats, optionally followed by UVs). Other PKX flags
                # retain the MPX float position/normal layout.
                known_declaration_bits = KNOWN_DECLARATION_BITS | (0xA if pkx_compat else 0)
                if flags & ~known_declaration_bits:
                    r.fail(p + 44, f"unsupported vertex declaration 0x{flags:X}: "
                                   f"unknown bits 0x{flags & ~known_declaration_bits:X}")
                weighted = bool(flags & 4)
                if weighted and (category != 0 or coord_mode != 1):
                    r.fail(p, "weighted mesh lacks source mapping/coordinate mode")
                position_only = pkx_compat and bool(flags & 8)
                has_normals = not position_only
                if position_only and category == 0:
                    r.fail(p, "position-only source-mapped meshes are unsupported")
                has_vertex_colors = pkx_compat and bool(flags & 2) and not position_only
                stride = (
                    (12 if position_only else 24)
                    + (4 if pkx_compat and flags & 2 and not position_only else 0)
                    + (8 if flags & 1 else 0)
                )
                if not vertex_count or vertex_count > vb_size // stride:
                    r.fail(p + 20, f"invalid vertex count {vertex_count}")
                if voffset % 4 or voffset + vertex_count * stride > vb_size:
                    r.fail(p + 12, "vertex range outside GPU buffer")
                if ip != p + 56 or index_count > (size - 56) // 2:
                    r.fail(p + 28, "index range is not contained in mesh command")
                mapping_at = ip + index_count * 2  # uint16 pairs; DO NOT align this to 4.
                expected_end = mapping_at + (vertex_count * 4 if category == 0 else 0)
                if (expected_end + 3) // 4 * 4 != p + size:
                    r.fail(p, "mesh length does not match indices/source map/alignment")
                if any(r.data[expected_end:p + size]):
                    r.fail(expected_end, "nonzero mesh alignment bytes")
                indices = list(r.values(ip, "H", index_count))
                for k, index in enumerate(indices):
                    if index >= vertex_count:
                        r.fail(ip + k * 2, f"strip index {index} outside {vertex_count} vertices")
                raw = np.frombuffer(r.data, dtype="<f4", count=vertex_count * (stride // 4),
                                    offset=vb + voffset).reshape(vertex_count, stride // 4)
                finite_attributes = np.isfinite(raw)
                if has_vertex_colors:
                    finite_attributes[:, 6] = True
                if not finite_attributes.all():
                    r.fail(vb + voffset, "non-finite GPU vertex attribute")
                mesh = SGDMesh(f"entry{entry_index:02d}_block{block_id:02d}_mesh{len(stats['meshes']):02d}")
                mesh.material_index, mesh.bone_index = mat_base + material, bone_base + coord
                uv_offset = 3 if position_only else 6 + (
                    1 if pkx_compat and flags & 2 else 0
                )
                if pkx_compat and flags & 1 and np.max(np.abs(raw[:, uv_offset:uv_offset + 2])) > 1e8:
                    stats["warnings"].append(
                        f"Extreme UV value in mesh at 0x{p:X}; source values retained"
                    )
                mesh.uvs = raw[:, uv_offset:uv_offset + 2].astype(float).tolist() if flags & 1 else []
                mesh.colors = [[1., 1., 1., 1.] for _ in range(vertex_count)]
                if has_vertex_colors:
                    packed_colors = raw[:, 6].view("<u4")
                    mesh.colors = [
                        [
                            ((int(color) >> 16) & 0xFF) / 255.,
                            ((int(color) >> 8) & 0xFF) / 255.,
                            (int(color) & 0xFF) / 255.,
                            ((int(color) >> 24) & 0xFF) / 255.,
                        ]
                        for color in packed_colors
                    ]
                mesh.indices = triangle_strip(indices)
                mesh.xbox_vertex_declaration = flags
                mesh.xbox_has_vertex_colors = has_vertex_colors
                mesh.xbox_source_offset = r.base + p
                mesh.xbox_source_mappings = []
                for i in range(vertex_count):
                    pos = raw[i, :3].astype(float)
                    normal = raw[i, 3:6].astype(float) if has_normals else None
                    pos_info = normal_info = None
                    if category == 0:
                        vi, ni = r.values(mapping_at + i * 4, "H", 2)
                        prefix = "weighted_" if weighted else "unique_"
                        refpos, pos_info = source_value(prefix + "positions", vi)
                        refnormal, normal_info = source_value(prefix + "normals", ni)
                        ep, en = float(np.max(np.abs(pos - refpos))), float(np.max(np.abs(normal - refnormal)))
                        stats["gpu_source_position_max_error"] = max(stats["gpu_source_position_max_error"], ep)
                        stats["gpu_source_normal_max_error"] = max(stats["gpu_source_normal_max_error"], en)
                        if ep > 1e-5 or en > 1e-5:
                            r.fail(mapping_at + i * 4, "source mapping disagrees with GPU position/normal")
                        stats["mapped_vertices"] += 1
                        mesh.xbox_source_mappings.append([vi, ni])
                    if weighted:
                        second, weight, b0, b1 = pos_info
                        a = pos @ rotations[b0] + translations[b0]
                        b = second @ rotations[b1] + translations[b1]
                        stats["weighted_bind_pair_max_error"] = max(stats["weighted_bind_pair_max_error"], float(np.linalg.norm(a - b)))
                        world = a * weight + b * (1. - weight)
                        if has_normals:
                            nsecond, nw, nb0, nb1 = normal_info
                            normal = (normal @ rotations[nb0]) * nw + (nsecond @ rotations[nb1]) * (1. - nw)
                        mesh.joints.append([bone_base + b0, bone_base + b1, 0, 0])
                        mesh.weights.append([weight, 1. - weight, 0., 0.])
                        stats["weighted_vertices"] += 1
                    else:
                        position_bone = coord if pos_info is None else pos_info
                        world = pos @ rotations[position_bone] + translations[position_bone]
                        if has_normals:
                            normal_bone = coord if normal_info is None else normal_info
                            normal = normal @ rotations[normal_bone]
                        mesh.joints.append([bone_base + position_bone, 0, 0, 0])
                        mesh.weights.append([1., 0., 0., 0.])
                    mesh.positions.append(world.tolist())
                    if has_normals:
                        mesh.normals.append(_normal(normal))
                if not has_normals:
                    generated = np.zeros((vertex_count, 3), dtype=float)
                    positions = np.asarray(mesh.positions, dtype=float)
                    for a, b, c in mesh.indices:
                        face_normal = np.cross(positions[b] - positions[a], positions[c] - positions[a])
                        generated[a] += face_normal
                        generated[b] += face_normal
                        generated[c] += face_normal
                    mesh.normals = [_normal(normal) for normal in generated]
                ranges.append((voffset, voffset + vertex_count * stride))
                stats["meshes"].append({"offset": p, "category": category, "block": block_id,
                    "coordinate": coord, "coordinate_mode": coord_mode, "material": material,
                    "vertex_offset": voffset, "vertex_count": vertex_count, "stride": stride,
                    "index_offset": ip, "index_count": index_count, "triangle_count": len(mesh.indices),
                    "flags": flags, "has_normals": has_normals,
                    "has_vertex_colors": has_vertex_colors,
                    "mapping_offset": mapping_at if category == 0 else None,
                    "unknown_words": {str(i * 4): fields[i] for i in (2, 4, 6, 8, 10, 12, 13)}})
                model.meshes.append(mesh)
            elif (
                pkx_compat
                and category == 14
                and allow_auxiliary_category14
                and size > 0x24
            ):
                if (
                    p != start
                    or p + size + 4 != limit
                    or r.u32(p + size) != 0
                ):
                    r.fail(
                        p,
                        "large category 14 command must occupy its own block "
                        "and end at the block terminator",
                    )
                stats["auxiliary_commands"].append({
                    "offset": p,
                    "category": category,
                    "size": size,
                    "block": block_id,
                    "semantics": "uninterpreted_ff2x_auxiliary_data",
                })
            elif pkx_compat and category in (12, 14, 64, 65):
                expected_sizes = {12: {0x90}, 14: {0x1C, 0x24}, 64: {0x10}, 65: {0x10}}
                if size not in expected_sizes[category]:
                    r.fail(p, f"unsupported category {category} command size 0x{size:X}")
                stats["non_geometry_commands"].append({
                    "offset": p,
                    "category": category,
                    "size": size,
                })
            else:
                r.fail(p + 4, f"unsupported command category {category}; no silent skipping")
            p += size
    ranges.sort()
    covered = 0
    for start, end in ranges:
        if start > covered:
            stats["warnings"].append(f"Unreferenced GPU bytes 0x{covered:X}..0x{start:X}")
        if start < covered:
            stats["warnings"].append(f"Shared/overlapping GPU range at 0x{start:X}")
        covered = max(covered, end)
    if covered < vb_size:
        stats["warnings"].append(f"Unreferenced GPU tail: {vb_size - covered} bytes")
    stats["gpu_buffer_fully_covered"] = not any("GPU" in w for w in stats["warnings"])
    stats["vertices"] = sum(m["vertex_count"] for m in stats["meshes"])
    stats["triangles"] = sum(m["triangle_count"] for m in stats["meshes"])
    stats["trailing_alignment_bytes"] = len(r.data) - vb - vb_size
    if stats["trailing_alignment_bytes"] > 15 or any(r.data[vb + vb_size:]):
        r.fail(vb + vb_size, "unexplained data after GPU buffer")
    if stats["weighted_bind_pair_max_error"] > .02:
        stats["warnings"].append("Weighted bind copies differ by more than .02 units; inspect this new pose")
    return stats


def _attach_xpr(result, source, decode):
    records = parse_xpr0_records(source)
    pictures = parse_xpr0(source, decode=decode)
    result.textures = [p for p in pictures if p["level"] == 0]
    result.mipmaps = {r["index"]: [p["image"] for p in pictures if p["index"] == r["index"]]
                      for r in records}
    result.texture_names = [f"xpr_{r['index']:03d}" for r in records]
    result.diagnostics["xpr0_textures"] = len(records)
    result.diagnostics["xpr0_records"] = records
    result.diagnostics["xpr0_mip_images"] = len(pictures)
    if result.model is not None:
        for material in result.model.materials:
            slot = material.xbox_resource_index
            if slot >= len(records):
                raise XboxMPXError(f"{source}: XPR binding: material {material.name!r} references "
                                   f"resource {slot}, archive has {len(records)} resources")
            if slot >= 0:
                material.texture_image = result.textures[slot]["image"]
                result.texture_names[slot] = material.name
                result.diagnostics["bound_by"]["explicit_resource_index"] += 1


def parse_mpx(data_or_path, *, xpr=None, name=None, decode_textures=True):
    """Read an MPX and, for a path, its exact same-stem XPR sidecar.

    If weighted bind data is inconsistent, a same-directory unnumbered base
    model may supply a matching, self-consistent skeleton. An explicit ``xpr``
    may be bytes or a path. No MPK, MDL or PS2 resource is searched. Bytes-only
    input has no implicit sidecar. Missing textures are reported, never fabricated.
    """
    return _parse_mpx(
        data_or_path,
        xpr=xpr,
        name=name,
        decode_textures=decode_textures,
        pose_templates=None,
        allow_pose_fallback=True,
    )


def parse_pkx_geometry(
    data,
    *,
    name="xbox_pkx",
    allow_auxiliary_category14=False,
):
    """Parse bounded 0x1060 payloads from a validated PKX package adapter."""
    return _parse_mpx(
        data,
        name=name,
        decode_textures=False,
        pose_templates=None,
        allow_pose_fallback=False,
        pkx_compat=True,
        allow_auxiliary_category14=allow_auxiliary_category14,
    )


def _pose_templates_from_asset(asset):
    """Extract a validated sibling's decoded bind transforms by MPX entry."""
    templates = []
    for entry in asset.diagnostics["entries"]:
        base = entry["bone_base"]
        bones = asset.model.bones[base:base + entry["bone_count"]]
        if len(bones) != entry["bone_count"]:
            return None
        parents = [
            bone.parent - base if bone.parent >= base else -1
            for bone in bones
        ]
        angles = [list(bone.rot) for bone in bones]
        translations = [
            [-bone.trans[0], bone.trans[1], -bone.trans[2]]
            for bone in bones
        ]
        matrices = []
        for angle, translation in zip(angles, translations):
            matrix = np.eye(4)
            matrix[:3, :3] = xbox_bind_rotation(angle[:3])
            matrix[3, :3] = translation
            matrices.append(matrix)
        templates.append({
            "parents": parents,
            "matrices": np.asarray(matrices, dtype=float),
            "angles": angles,
            "translations": translations,
        })
    return templates


def _parse_mpx(
    data_or_path,
    *,
    xpr=None,
    name=None,
    decode_textures=True,
    pose_templates=None,
    allow_pose_fallback=True,
    pkx_compat=False,
    allow_auxiliary_category14=False,
):
    data, source_name, path = _source(data_or_path, "<MPX buffer>")
    r = _Reader(data, source_name)
    r.require(0, 16, "MPX header")
    count = r.u32(0)
    if not 1 <= count <= min(4096, (len(data) - 16) // 16):
        r.fail(0, f"invalid MPX entry count {count}")
    if any(r.values(4, "I", 3)):
        r.fail(4, "unsupported MPX header flags")
    model = SGDModel(name or (path.stem if path else "xbox_mpx"))
    model.uvs_are_flipped = True  # Xbox float UVs and decoded images both originate top-left.
    model.xbox_native_mpx = True
    # Different entries can store distinct bind poses; provide one common skin root.
    root_bone = SGDBone(0, -1)
    root_bone.name = "XboxRoot"
    model.bones.append(root_bone)
    result = XboxAsset(model=model, diagnostics={"parser": "xbox_mpx_native_v2",
        "source": source_name, "sha256": hashlib.sha256(data).hexdigest(),
        "entries": [], "sgd1050_entries": 0, "sgd1060_entries": count,
        "xpr0_textures": 0, "bound_by": {"explicit_resource_index": 0}, "warnings": []})
    pos, skeletons = 16, {}
    for i in range(count):
        size, kind, pad0, pad1 = r.values(pos, "I", 4)
        if not size or size % 16 or kind or pad0 or pad1:
            r.fail(pos, "unsupported/malformed MPX entry descriptor")
        r.require(pos + 16, size, f"entry {i} payload")
        er = _Reader(memoryview(data)[pos + 16:pos + 16 + size], source_name,
                     pos + 16, f"MPX entry {i}")
        template = pose_templates[i] if pose_templates is not None else None
        result.diagnostics["entries"].append(
            _entry(
                er, i, model, skeletons,
                pose_template=template,
                pkx_compat=pkx_compat,
                allow_auxiliary_category14=allow_auxiliary_category14,
            )
        )
        pos += 16 + size
    tail = data[pos:]
    if tail not in (b"", b"\xff" * 16):
        r.fail(pos, "missing/invalid final MPX descriptor (expected four FFFFFFFF words)")
    result.diagnostics["skeleton_sets"] = len(skeletons)
    result.diagnostics["meshes"] = len(model.meshes)
    result.diagnostics["vertices"] = sum(len(m.positions) for m in model.meshes)
    result.diagnostics["triangles"] = sum(len(m.indices) for m in model.meshes)
    result.diagnostics["materials"] = len(model.materials)
    result.diagnostics["weighted_vertices"] = sum(e["weighted_vertices"] for e in result.diagnostics["entries"])
    for e in result.diagnostics["entries"]:
        result.diagnostics["warnings"].extend(f"entry {e['entry']}: {w}" for w in e["warnings"])
    if (
        allow_pose_fallback
        and pose_templates is None
        and path is not None
        and any(e["weighted_bind_pair_max_error"] > 0.02
                for e in result.diagnostics["entries"])
    ):
        base_stem = re.sub(r"\d+$", "", path.stem)
        reference_path = path.with_name(base_stem + path.suffix)
        if base_stem != path.stem and reference_path.is_file():
            try:
                reference = _parse_mpx(
                    reference_path.read_bytes(),
                    name=reference_path.stem,
                    decode_textures=False,
                    allow_pose_fallback=False,
                )
            except XboxMPXError as exc:
                result.diagnostics["bind_pose_fallback_rejected"] = str(exc)
            else:
                reference_entries = reference.diagnostics["entries"]
                source_errors = [
                    e["weighted_bind_pair_max_error"]
                    for e in result.diagnostics["entries"]
                ]
                reference_is_consistent = all(
                    e["weighted_bind_pair_max_error"] <= 0.02
                    for e in reference_entries
                )
                matching_layout = (
                    len(reference_entries) == len(result.diagnostics["entries"])
                    and all(
                        target["bone_count"] == source["bone_count"]
                        for target, source in zip(
                            result.diagnostics["entries"], reference_entries
                        )
                    )
                )
                templates = (
                    _pose_templates_from_asset(reference)
                    if reference_is_consistent and matching_layout
                    else None
                )
                if templates is not None:
                    try:
                        repaired = _parse_mpx(
                            path,
                            xpr=xpr,
                            name=name,
                            decode_textures=decode_textures,
                            pose_templates=templates,
                            allow_pose_fallback=False,
                        )
                    except XboxMPXError as exc:
                        result.diagnostics["bind_pose_fallback_rejected"] = str(exc)
                    else:
                        repaired.diagnostics["bind_pose_fallback"] = {
                            "reference": str(reference_path),
                            "reason": "source weighted vertices disagree with their bind transforms",
                            "source_max_error": max(source_errors, default=0.0),
                        }
                        repaired.model.parse_diagnostics = repaired.diagnostics
                        return repaired
    if xpr is None and path is not None:
        candidates = [p for p in path.parent.iterdir() if p.is_file()
                      and p.suffix.lower() == ".xpr" and p.stem.casefold() == path.stem.casefold()]
        if len(candidates) > 1:
            r.fail(0, "ambiguous same-stem XPR sidecars; specify xpr explicitly")
        if candidates:
            xpr = candidates[0]
    if xpr is not None:
        _attach_xpr(result, xpr, decode_textures)
    else:
        result.diagnostics["warnings"].append("No same-stem XPR supplied/found; model has unbound texture references")
    # These characters are authored looking down -Z. Turn them so the viewer's
    # default +Z camera sees the face, exactly as the PS2 .mdl path does.
    face_along_positive_z(model)
    model.parse_diagnostics = result.diagnostics
    return result


def parse_xbox_asset(path, *, name=None, decode_textures=True, xpr=None):
    """Extension-specific viewer adapter: ONLY MPX and XPR, never PS2 assets.

    ``xpr`` names the texture archive to bind to an MPX instead of the same-stem
    sidecar, which is how a recolour variant of the same geometry is read.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".mpx":
        return parse_mpx(path, xpr=xpr, name=name, decode_textures=decode_textures)
    if suffix == ".xpr":
        result = XboxAsset(diagnostics={"parser": "xbox_xpr_native_v2", "source": str(path),
            "sgd1050_entries": 0, "sgd1060_entries": 0,
            "bound_by": {"explicit_resource_index": 0}, "warnings": []})
        _attach_xpr(result, path, decode_textures)
        return result
    raise XboxMPXError(f"{path}: this reader accepts only .mpx and .xpr")
