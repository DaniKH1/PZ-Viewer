"""Fatal Frame 2 / Project Zero 2 Wii MDLB, PK2B and PPDB adapter.

Self-contained geometry/texture parser. The original module was adapted from
an external research parser; no external game folder or parser is imported.
This revision fixes the Xbox-derived example rigs using evidence from ENOB
local TRS, not filenames or hand-adjusted bone positions.

Format summary (big-endian reversed-tag chunks, optionally LZ11-compressed):
* ENOB +0x20/+0x30/+0x40: local quaternion xyzw, scale, translation.
* ENOB +0x50: stored matrix. In the three supplied recycled rigs it is a
  row-vector INVERSE bind, independently checked against the local hierarchy.
  _resolve_bone_matrices distinguishes that from a verified row-vector world
  matrix; the existing ordinary Wii world-matrix path is kept.
* PAHS -> TREV rigid local positions or GIEW preweighted positions; DCXT UVs;
  HSEM -> LDIV indexed GX primitives. The legacy vertex-colour heuristics and
  recomputed normals remain, not a claim of a complete Wii graphics pipeline.
* PPDB contains a TPL header. All descriptor, pixel and palette pointers are
  relative to that header. CI8 has 8x4 index tiles and a separate TLUT.

MODEL_UNIT_TO_METRES=0.05 and the existing viewer per-kind scale are retained
for compatibility. Their physical calibration was not re-measured here.
See docs/ff2w/mdlb-format.md, docs/ff2w/ppdb-format.md and docs/ff2w/repair.md for byte
examples, validation, and limitations (including absent native Wii samples).
"""

from __future__ import annotations

import math
import os
import struct
import copy
from pathlib import Path

import numpy as np
from PIL import Image

from pz_core.common.model import SGDBone, SGDMaterial, SGDMesh, SGDModel

# Native MDLB/PK2B unit expressed in metres.  See the module docstring; single
# place to retune if a better reference turns up.
MODEL_UNIT_TO_METRES = 0.05

# Floor the remapped vertex tint is allowed to reach.  0.08 is how the baked
# ambient reads in game: deep shadow falls almost to black while lit rock stays
# warm brown.  This is a visualisation remap, not the game's lighting -- rooms
# also rely on dynamic lights and the Mono.ppdb lightmap.
VERTEX_COLOR_FLOOR = 0.08

# Extensions the adapter claims.  The container magic decides for real, these
# only keep ``is_ff2w_asset`` cheap and give a good default model name.
FF2W_EXTENSIONS = (".mdlb", ".pk2b")

CONTAINER_PK3 = b"pk3"
CONTAINER_PK2 = b"pk2"


class FF2WError(ValueError):
    """Raised when a file is not a supported Fatal Frame 2 Wii container."""


# =====================================================================
# Container and chunk decoding
#
# Original decoding functions were adapted from mdlb_parser.py. Corrections
# and evidence for this version are documented under docs/ff2w/repair.md.
# =====================================================================


def decompress_lz11(data: bytes) -> bytes:
    """Decompress Nintendo LZ11 compressed data (ported: mdlb_parser.decompress_lz11)."""
    if not data or data[0] != 0x11:
        return data
    if len(data) < 4:
        raise FF2WError("LZ11 at 0x0: truncated header")
    size = data[1] | (data[2] << 8) | (data[3] << 16)
    src = 4
    if size == 0:
        if len(data) < 8:
            raise FF2WError("LZ11 at 0x4: truncated extended size")
        size = int.from_bytes(data[4:8], "little")
        src = 8
    out = bytearray()
    while src < len(data) and len(out) < size:
        flags = data[src]
        src += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if (flags & (0x80 >> bit)) == 0:
                if src >= len(data):
                    break
                out.append(data[src])
                src += 1
            else:
                if src >= len(data):
                    break
                b1 = data[src]
                src += 1
                ind = b1 >> 4
                if ind == 0:
                    if src + 2 > len(data):
                        break
                    b2, b3 = data[src], data[src + 1]
                    src += 2
                    length = (((b1 & 0x0F) << 4) | (b2 >> 4)) + 0x11
                    disp = (((b2 & 0x0F) << 8) | b3) + 1
                elif ind == 1:
                    if src + 3 > len(data):
                        break
                    b2, b3, b4 = data[src], data[src + 1], data[src + 2]
                    src += 3
                    length = (((b1 & 0x0F) << 12) | (b2 << 4) | (b3 >> 4)) + 0x111
                    disp = (((b3 & 0x0F) << 8) | b4) + 1
                else:
                    if src + 1 > len(data):
                        break
                    b2 = data[src]
                    src += 1
                    length = ind + 1
                    disp = (((b1 & 0x0F) << 8) | b2) + 1

                if disp > len(out):
                    raise FF2WError(f"LZ11 at 0x{src:X}: invalid back-reference {disp}")
                for _ in range(length):
                    out.append(out[-disp])
                    if len(out) >= size:
                        break
    if len(out) != size:
        raise FF2WError(f"LZ11 at 0x{src:X}: truncated stream ({len(out)}/{size} bytes)")
    return bytes(out)


def parse_bones(pk3: bytes, search_start: int = 0x80) -> list[dict]:
    """Read ENOB records and resolve the verified Xbox-derived bind convention.

    +0x20 is a local xyzw quaternion, +0x30 a local scale, +0x40 a
    local translation and +0x50 a stored 4x4. See _resolve_bone_matrices:
    the latter is NOT a world matrix in the three recycled example models.
    """
    bones = []
    pos = search_start
    bone_id_counter = 0
    while pos < len(pk3) - 8:
        if pk3[pos:pos+4] == b"ENOB":
            size = struct.unpack_from(">I", pk3, pos+4)[0]
            bone_end = pos + 8 + size
            if size < 0x88 or bone_end > len(pk3):
                raise FF2WError(f"ENOB at 0x{pos:X}: invalid/truncated chunk size 0x{size:X}")
            bone_id = struct.unpack_from(">H", pk3, pos+0x10)[0] if pos+0x12 <= len(pk3) else bone_id_counter
            parent_id = struct.unpack_from(">h", pk3, pos+0x12)[0] if pos+0x14 <= len(pk3) else -1

            if pos + 0x50 + 64 <= len(pk3):
                mtx = list(struct.unpack_from(">16f", pk3, pos + 0x50))
            else:
                mtx = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]

            if not all(math.isfinite(v) for v in mtx):
                raise FF2WError(f"ENOB at 0x{pos:X}: non-finite matrix")
            name = f"bone_{bone_id}"
            eman_pos = pk3.find(b"EMAN", pos, bone_end)
            if eman_pos >= 0 and eman_pos + 12 <= len(pk3):
                name_len = struct.unpack_from(">I", pk3, eman_pos + 8)[0]
                name_start = eman_pos + 12
                if name_start + name_len <= len(pk3):
                    try:
                        name = pk3[name_start:name_start + name_len].decode("utf-8", "replace").rstrip("\x00")
                    except Exception:
                        pass

            bones.append({
                "id": bone_id,
                "parent_id": parent_id,
                "name": name,
                "matrix": mtx,
                "chunk_offset": pos,
                "local_rotation": list(struct.unpack_from(">4f", pk3, pos + 0x20)),
                "local_scale": list(struct.unpack_from(">3f", pk3, pos + 0x30)),
                "local_translation": list(struct.unpack_from(">3f", pk3, pos + 0x40)),
            })
            bone_id_counter += 1
            pos += size
        else:
            pos += 4
    _resolve_bone_matrices(bones)
    return bones


def _bone_local_matrix(bone: dict) -> np.ndarray:
    """ENOB local T * R(quaternion xyzw) * S, with column vectors."""
    q = np.asarray(bone["local_rotation"], dtype=np.float64)
    s = np.asarray(bone["local_scale"], dtype=np.float64)
    t = np.asarray(bone["local_translation"], dtype=np.float64)
    norm = float(q @ q)
    if not (np.isfinite(q).all() and np.isfinite(s).all()
            and np.isfinite(t).all() and abs(norm - 1.0) < 0.01
            and np.all(np.abs(s) > 1e-8)):
        raise FF2WError(
            f"ENOB at 0x{bone['chunk_offset']:X}: invalid local TRS for bone {bone['id']}"
        )
    x, y, z, w = q / math.sqrt(norm)
    rotation = np.array([
        [1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)],
        [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)],
        [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)],
    ])
    matrix = np.eye(4)
    matrix[:3, :3] = rotation @ np.diag(s)
    matrix[:3, 3] = t
    return matrix


def _resolve_bone_matrices(bones: list[dict]) -> None:
    """Resolve the Xbox-derived ENOB inverse binds using independent local TRS.

    Ordinary Wii world matrices (translation in 3/7/11) are left unchanged.
    For a row-vector rig, the independent hierarchy proves whether +0x50 is
    a world matrix or an inverse bind: W @ stored.T must equal identity for
    the latter. The decision is rig-wide, including zero-translation bones;
    it is NOT made from filenames, the direction of a limb or a render score.

    The three supplied rigs have a maximum residual below 2.1e-4. A 5e-4
    tolerance allows their baked quaternion/matrix rounding disagreement,
    but cannot confuse a 19-unit inverse translation with a world position.
    """
    if not bones:
        return
    raw = [np.asarray(b["matrix"], dtype=np.float64).reshape(4, 4) for b in bones]
    informative = [i for i, m in enumerate(raw)
                   if np.max(np.abs(m[3, :3])) > 1e-4]
    for bone in bones:
        bone["matrix_semantics"] = "legacy_world"
    # Do not reinterpret the existing, ordinary Wii path. A mixed/unclassified
    # layout is retained as legacy rather than forcing all Wii assets through
    # this newly verified Xbox-derived convention.
    if not informative or not all(np.allclose(m[:3, 3], 0.0, atol=1e-5, rtol=0)
                                 and abs(m[3, 3] - 1.0) < 1e-5 for m in raw):
        return

    by_id = {b["id"]: i for i, b in enumerate(bones)}
    if len(by_id) != len(bones):
        raise FF2WError("ENOB: duplicate bone IDs in row-vector rig")
    local = [_bone_local_matrix(b) for b in bones]
    worlds: dict[int, np.ndarray] = {}
    # Iterative traversal also handles parents stored after their children.
    for start in range(len(bones)):
        chain = []
        visiting = set()
        node = start
        while node not in worlds:
            if node in visiting:
                raise FF2WError(f"ENOB: parent cycle at bone {bones[node]['id']}")
            visiting.add(node)
            chain.append(node)
            parent_id = bones[node]["parent_id"]
            if parent_id == -1:
                break
            if parent_id not in by_id:
                raise FF2WError(f"ENOB: bone {bones[node]['id']} references missing parent {parent_id}")
            node = by_id[parent_id]
        for node in reversed(chain):
            parent_id = bones[node]["parent_id"]
            worlds[node] = (local[node] if parent_id == -1
                            else worlds[by_id[parent_id]] @ local[node])

    inverse_errors = [float(np.max(np.abs(worlds[i] @ m.T - np.eye(4))))
                      for i, m in enumerate(raw)]
    direct_errors = [float(np.max(np.abs(worlds[i] - m.T)))
                     for i, m in enumerate(raw)]
    tolerance = 5e-4
    if max(inverse_errors) <= tolerance:
        semantics = "inverse_bind_row_vector"
        try:
            resolved = [np.linalg.inv(m.T) for m in raw]
        except np.linalg.LinAlgError as exc:
            raise FF2WError("ENOB: singular inverse bind matrix") from exc
        errors = inverse_errors
    elif max(direct_errors) <= tolerance:
        semantics = "world_row_vector"
        resolved = [m.T.copy() for m in raw]
        errors = direct_errors
    else:
        worst = int(np.argmax(inverse_errors))
        bone = bones[worst]
        raise FF2WError(
            f"ENOB at 0x{bone['chunk_offset']:X}: unverified row-vector rig; "
            f"bone {bone['id']}, inverse-bind residual={max(inverse_errors):.6g}, "
            f"world residual={max(direct_errors):.6g}. Refusing to guess a skeleton."
        )
    for i, (bone, world) in enumerate(zip(bones, resolved)):
        bone["stored_matrix"] = list(bone["matrix"])
        bone["matrix"] = world.ravel().tolist()  # row-major, column-vector math
        bone["matrix_semantics"] = semantics
        bone["matrix_layout"] = "row_major_column_vector"
        bone["local_matrix"] = local[i].ravel().tolist()
        bone["trs_world_matrix"] = worlds[i].ravel().tolist()
        bone["bind_validation_error"] = errors[i]
        # A mathematical affine inverse can retain a tiny m33 rounding error.
        # Homogeneous normalisation is not needed: the file's matrices and
        # their inverses are preserved, and the residual is reported instead.


def _walk_display_list(body: bytes, nverts: int, nuvs: int,
                       stride: int) -> list[list[tuple[int, int, int]]] | None:
    """Decode one LDIV payload with a fixed vertex record stride.

    Ported: mdlb_parser._walk_display_list.

    Record layouts differ between the two model families that share this
    container:

    * `.mdlb` / small `.pk2b` props use 6-byte records: ``pos, nrm, uv``.
    * `.pk2b` room geometry uses 8-byte records: ``pos, nrm, colour, uv``.
      The UV moved to the fourth u16 slot and a third one indexes the vertex
      colour palette, which is why a 6-byte read of a room list reads every
      UV as zero and then overruns the payload.

    Returns a list of triangles, each a list of
    ``(vertex_index, uv_index, colour_index)`` triples, or None when the
    payload does not decode cleanly at this stride. ``colour_index`` is -1 for
    the 6-byte layout, which has no colour slot.
    """
    uv_off = 6 if stride == 8 else 4
    col_off = 4 if stride == 8 else -1
    uv_limit = nuvs if nuvs > 0 else 1 << 30

    tris: list[list[tuple[int, int, int]]] = []
    cur = 0
    end = len(body)
    while cur < end:
        op = body[cur]
        if op == 0x00:
            # NOP / padding between primitive runs
            cur += 1
            continue
        if cur + 3 > end:
            return None
        cnt = struct.unpack_from(">H", body, cur + 1)[0]
        if cnt == 0:
            cur += 1
            continue
        start = cur + 3
        if start + cnt * stride > end:
            return None

        prim = op & 0xF8
        vtx: list[tuple[int, int, int]] = []
        for k in range(cnt):
            off = start + k * stride
            pi = struct.unpack_from(">H", body, off)[0]
            ti = struct.unpack_from(">H", body, off + uv_off)[0]
            ci = struct.unpack_from(">H", body, off + col_off)[0] if col_off >= 0 else -1
            if pi >= nverts or ti >= uv_limit:
                return None
            vtx.append((pi, ti, ci))

        if prim == 0x98:  # GX triangle strip (low three bits select VAT)
            for i in range(len(vtx) - 2):
                tri = (vtx[i + 1], vtx[i], vtx[i + 2]) if i % 2 == 0 else (vtx[i], vtx[i + 1], vtx[i + 2])
                tris.append(list(tri))
        elif prim == 0x90:  # triangle list
            for i in range(0, len(vtx) - 2, 3):
                tris.append([vtx[i], vtx[i + 2], vtx[i + 1]])
        elif prim == 0xA0:  # GX triangle fan
            for i in range(1, len(vtx) - 1):
                tris.append([vtx[0], vtx[i + 1], vtx[i]])
        elif prim == 0x80:  # GX independent quads
            if len(vtx) % 4:
                return None
            for i in range(0, len(vtx), 4):
                tris.append([vtx[i], vtx[i + 2], vtx[i + 1]])
                tris.append([vtx[i], vtx[i + 3], vtx[i + 2]])
        else:
            # An unsupported command is not a successfully decoded primitive.
            return None

        cur = start + cnt * stride

    return tris


def _palette_coherence(body: bytes, off: int, runlen: int,
                       tris: list[list[tuple[int, int, int]]],
                       tri_idx: list[tuple[int, int, int]]) -> tuple[float, float]:
    """Score a candidate palette by how smoothly its colours vary.

    Ported: mdlb_parser._palette_coherence.

    A real palette paints surfaces, so the three vertices of a triangle tend
    to carry the same colour or neighbours within a small distance. Random
    byte runs interpreted as colours give neighbours that differ wildly.
    Triangles referencing an index past the run are left out of the score and
    reported as uncovered instead of being clamped to an invented colour.

    Returns (coherence, coverage): the fraction of covered triangles whose
    vertices match, and the fraction of triangles fully covered by the run.

    This runs once per candidate run over every triangle of the shape, so it is
    the hot spot of the whole fallback: `rkh00` spent 34s here across 1042
    candidates. `tri_idx` therefore holds the colour indices extracted once per
    shape instead of rebuilding a list of them per candidate, and the per-triangle
    work avoids slices and generators. Both are pure speedups; the score is
    unchanged.
    """
    same = 0
    covered = 0
    total = len(tri_idx)
    limit = runlen
    for i0, i1, i2 in tri_idx:
        if i0 < 0 or i1 < 0 or i2 < 0 or i0 >= limit or i1 >= limit or i2 >= limit:
            continue
        covered += 1
        a = off + i0 * 4
        b = off + i1 * 4
        c = off + i2 * 4
        d01 = (abs(body[a] - body[b]) + abs(body[a + 1] - body[b + 1])
               + abs(body[a + 2] - body[b + 2]))
        d02 = (abs(body[a] - body[c]) + abs(body[a + 1] - body[c + 1])
               + abs(body[a + 2] - body[c + 2]))
        # Identical colour is the cheap common case; otherwise "near" means the
        # larger of the two distances from vertex 0, matching the original
        # max(d01, d02) <= 24 test.
        if d01 == 0 and d02 == 0:
            same += 1
        elif (d01 if d01 > d02 else d02) <= 24:
            same += 1
    if not total:
        return 0.0, 0.0
    return same / total, covered / total


LOCV_PALETTE_OFFSET = 0x18
LOCV_VERSION = 0x0500

# Offsets x triangles the fallback palette scan may cost, per shape. The scan
# is a pure-Python loop, so this is the real limiter on room load time; shapes
# over it skip the scan and rely on the vertex colour transfer fill instead.
_SCAN_WORK_BUDGET = 400_000


def _alpha_structured(pal: list[tuple]) -> bool:
    """Check that a candidate table looks like RGBA data.

    Ported: mdlb_parser._alpha_structured.

    Every real palette found so far stores alpha as fully opaque or fully
    transparent per entry: across all accepted tables the share of entries
    with alpha in {0x00, 0xFF} is 0.70-1.00. A run whose alpha bytes are
    mostly intermediate values is some other array that happens to be
    RGB-coherent, not a colour table, and accepting it paints vivid green
    psychedelia over rock.
    """
    if not pal:
        return False
    good = 0
    for c in pal:
        if len(c) < 4:
            return False
        if c[3] in (0x00, 0xFF):
            good += 1
    return good / len(pal) >= 0.50


def _read_locv_palette(pk3: bytes, shape_start: int,
                       shape_end: int) -> list[tuple[int, int, int, int]]:
    """Read the vertex colour palette from the shape's LOCV chunk.

    Ported: mdlb_parser._read_locv_palette.

    Every room shape that indexes vertex colours stores them in a LOCV chunk
    nested inside its MELE chunk. The layout is fixed:

        u16  version   always 0x0500
        u16  count     number of RGBA8 entries that follow
        u8[20]         reserved, all zero
        u8[count*4]    RGBA8 palette
        u8[pad]        zero padding to the chunk size

    `count` is the whole table, so the array never needs to be guessed from
    the colour indices that reference it.

    Returns an empty list when the shape has no LOCV chunk or it does not
    validate, letting the caller fall back to a scan.
    """
    me = pk3.find(b"MELE", shape_start, shape_end)
    if me == -1 or me + 8 > len(pk3):
        return []
    size = struct.unpack_from(">I", pk3, me + 4)[0]
    if size <= 8 or me + size > len(pk3):
        return []
    me_end = me + size

    # Walk the MELE body for the LOCV chunk; shapes hold exactly one.
    pos = me + 8
    while pos + 8 <= me_end:
        tag = pk3[pos:pos + 4]
        csize = struct.unpack_from(">I", pk3, pos + 4)[0]
        if tag == b"LOCV" and 8 <= csize <= (me_end - pos):
            body = pk3[pos + 8:pos + csize]
            if len(body) >= LOCV_PALETTE_OFFSET + 4:
                if struct.unpack_from(">H", body, 0)[0] != LOCV_VERSION:
                    return []
                count = struct.unpack_from(">H", body, 2)[0]
                lo = LOCV_PALETTE_OFFSET
                hi = lo + count * 4
                if count == 0 or hi > len(body):
                    return []
                pal = [tuple(body[lo + k * 4:lo + k * 4 + 4]) for k in range(count)]
                if _alpha_structured(pal):
                    return pal
                return []
            return []
        if not all(33 <= c < 127 for c in tag) or not (8 <= csize <= (me_end - pos)):
            pos += 4
            continue
        pos += csize
    return []


def find_vertex_color_table(pk3: bytes, shape_start: int, shape_end: int,
                            ncolors: int,
                            tris: list | None = None) -> list[tuple[int, int, int, int]]:
    """Locate the per-shape vertex colour palette inside the MELE chunk.

    Ported: mdlb_parser.find_vertex_color_table.

    The authoritative source is the LOCV chunk (see `_read_locv_palette`), which
    stores the table's exact offset and length. The byte-scanning fallback
    below only runs when a shape has no usable LOCV chunk.

    Room geometry indexes vertex colours with the third u16 of the 8-byte
    display list record. The palette itself is an RGBA8 array stored in the
    MELE chunk at a position that varies per shape, so it is recovered in two
    steps. First, maximal runs of four-byte entries with an opaque alpha byte
    are collected. Then each run long enough to matter is scored by spatial
    coherence: a real palette paints surfaces, so neighbouring vertices share
    colours, while a random byte run gives neighbours that differ wildly.

    Returns an empty list when the shape has no colours or none are found.
    """
    if ncolors <= 0:
        return []
    # Exact, structure-derived read first. It wins whenever the shape carries a
    # LOCV chunk, which is the normal case for rooms.
    exact = _read_locv_palette(pk3, shape_start, shape_end)
    if exact:
        return exact
    me = pk3.find(b"MELE", shape_start, shape_end)
    if me == -1:
        return []
    size = struct.unpack_from(">I", pk3, me + 4)[0]
    if size <= 0x20:
        return []
    body = pk3[me + 0x20:me + size]

    need = ncolors * 4
    end = len(body) - 4

    # Collect maximal runs of four-byte entries with an opaque alpha byte.
    runs: list[tuple[int, int]] = []
    i = 0
    while i <= end:
        if body[i + 3] != 0xFF:
            i += 1
            continue
        j = i
        r = 0
        while j + 3 <= end and body[j + 3] == 0xFF:
            r += 1
            j += 4
        runs.append((i, r))
        i = j

    # Merge runs separated by at most two entries: a single table is often
    # broken by a few translucent entries.
    merged: list[tuple[int, int]] = []
    for off, r in runs:
        if merged and off - (merged[-1][0] + merged[-1][1] * 4) <= 8:
            prev_off, _prev_r = merged[-1]
            merged[-1] = (prev_off, (off + r * 4 - prev_off) // 4)
        else:
            merged.append((off, r))
    cands = [(off, r) for off, r in merged if r >= 4]
    if not cands or tris is None:
        if not cands:
            return []
        padded = [off for off, _r in cands if not any(body[max(0, off - 16):off])]
        off = padded[0] if padded else max(cands, key=lambda t: t[1])[0]
        return [tuple(body[off + k * 4:off + k * 4 + 4]) for k in range(ncolors)]

    # Score every candidate by coherence times coverage and take the winner.
    # Both matter: a tiny run can be perfectly coherent over the two triangles
    # it covers, and a long run can cover everything with noise.
    #
    # Return length is sized from the 99th percentile of referenced indices,
    # not the maximum: a table region often continues into adjacent garbage,
    # and a single misparsed record inflates the maximum. Indices past the
    # returned table get their colour transferred from neighbouring faces.
    want = ncolors
    tri_idx: list[tuple[int, int, int]] = []
    if tris:
        all_ci = sorted(v[2] for tri in tris for v in tri)
        if all_ci:
            want = min(ncolors, all_ci[int(len(all_ci) * 0.99)] + 1)
        # Extracted once per shape: the scoring loop below runs per candidate
        # run and there can be over a thousand of them.
        tri_idx = [(t[0][2], t[1][2], t[2][2]) for t in tris]
    scored = []
    for off, r in cands:
        coh, cov = _palette_coherence(body, off, r, tris, tri_idx)
        scored.append((coh * cov, coh, cov, off, r))
    scored.sort(reverse=True)
    best, coh, cov, off, r = scored[0]
    # A real palette explains most of its shape smoothly; proven false runs stay
    # far below this bar (verified tables score 0.19-0.90, false ones <= 0.06).
    if best >= 0.10 and coh >= 0.10 and cov >= 0.5:
        ncolors = min(want, r)
        pal = [tuple(body[off + k * 4:off + k * 4 + 4]) for k in range(ncolors)]
        if _alpha_structured(pal):
            return pal
        # Falls through to the fallback scan below when the winner is not
        # an RGBA table at all (intermediate alphas throughout).

    # Fallback: no opaque run validates. Some real tables mix translucent
    # entries, so a full scan scored by RGB coherence is tried, with a stricter
    # bar since there is no opacity evidence.
    budget = _SCAN_WORK_BUDGET
    if len(body) * len(tris) > budget:
        return []
    dense = _scan_coherent_table(body, want, tris)
    if dense is None:
        return []
    off, _sn, _cov = dense
    # Clamp to the buffer: short tuples would crash a `col[3]` read.
    avail = (len(body) - off) // 4
    ncolors = min(want, avail)
    pal = [tuple(body[off + k * 4:off + k * 4 + 4]) for k in range(ncolors)]
    return pal if _alpha_structured(pal) else []


def _scan_coherent_table(body: bytes, ncolors: int,
                         tris: list) -> tuple[int, float, float] | None:
    """Scan every 4-byte offset for the most coherent RGB table.

    Ported: mdlb_parser._scan_coherent_table.

    Used only when no opaque run validates. The RGB channels must still vary
    smoothly across the surface. The bar is stricter than the opaque path
    because there is no opacity evidence; zero padding before the table breaks
    ties. The scan is subsampled to bound its cost on the big rooms.
    """
    if ncolors <= 0 or not tris:
        return None
    all_ci = sorted(v[2] for tri in tris for v in tri)
    need = all_ci[int(len(all_ci) * 0.99)] + 1 if all_ci else 0
    if need <= 0 or len(body) < need * 4 + 4:
        return None
    ncolors = need
    n_offsets = max(1, (len(body) - ncolors * 4 + 1) // 4)
    keep = max(1, (n_offsets * len(tris)) // 2000000)
    step_tris = tris[::keep]
    best: tuple[float, float, float, int] | None = None
    for off in range(0, len(body) - ncolors * 4 + 1, 4):
        same = near = tot = 0
        for tri in step_tris:
            idx = [v[2] for v in tri]
            if max(idx) >= ncolors:
                continue
            tot += 1
            cols = [body[off + ci * 4:off + ci * 4 + 3] for ci in idx]
            if cols[0] == cols[1] == cols[2]:
                same += 1
            else:
                dist = max(
                    sum(abs(cols[0][k] - cols[1][k]) for k in range(3)),
                    sum(abs(cols[0][k] - cols[2][k]) for k in range(3)),
                )
                if dist <= 24:
                    near += 1
        if not tot:
            continue
        sn = (same + near) / tot
        cov = tot / len(step_tris)
        tot_score = sn * cov
        padded = not any(body[max(0, off - 16):off])
        key = (tot_score, sn, cov, 1 if padded else 0, -off)
        if best is None or key > best[0]:
            best = (key, tot_score, sn, cov, off)
    if best is None:
        return None
    _key, tot_score, sn, cov, off = best
    if sn < 0.40 or cov < 0.5:
        return None
    return off, sn, cov


def decode_display_list(body: bytes, nverts: int, nuvs: int) -> list[list[tuple[int, int, int]]]:
    """Decode an LDIV payload, auto-detecting the vertex record stride.

    Ported: mdlb_parser.decode_display_list.

    Tries the 6-byte character/prop layout first and only falls back to the
    8-byte room layout when it fails, so models that already decode keep
    exactly the behaviour they had. The 6-byte layout has no colour slot and
    reports -1 for it. Only one of the two survives the bounds check inside
    _walk_display_list, so this choice is not what makes a few pk2b items look
    smeared.

    Each returned triangle vertex is ``(vertex_index, uv_index, colour_index)``.
    """
    if nverts <= 0 or not body:
        return []
    best: list[list[tuple[int, int, int]]] = []
    for stride in (6, 8):
        tris = _walk_display_list(body, nverts, nuvs, stride)
        if tris is None:
            continue
        if len(tris) > len(best):
            best = tris
    return best


def parse_materials(pk3: bytes) -> dict[int, dict]:
    """Extract every ETAM material chunk (ported: mdlb_parser.parse_materials).

    This reader extracts a name and two slots: short 10 is the diffuse texture
    index and short 14 an optional mask index. Other material state is not
    reconstructed; SGDMaterial retains its neutral defaults.
    """
    materials = {}
    pos = 0x80
    mat_idx = 0
    while pos < len(pk3) - 8:
        if pk3[pos:pos+4] == b"ETAM":
            size = struct.unpack_from(">I", pk3, pos+4)[0]
            data = pk3[pos:pos+size]
            shorts = struct.unpack_from(f">{min(len(data)//2, 32)}h", data, 0)

            tex_id = shorts[10] if len(shorts) > 10 else -1
            alpha_tex_id = shorts[14] if len(shorts) > 14 else -1

            # Find name from preceding EMAN chunk if available
            p_eman = pk3.rfind(b"EMAN", max(0, pos - 0x40), pos)
            mat_name = f"material_{mat_idx}"
            if p_eman != -1:
                nlen = struct.unpack_from(">I", pk3, p_eman + 8)[0]
                nstart = p_eman + 12
                if nstart + nlen <= pos:
                    try:
                        mat_name = pk3[nstart:nstart + nlen].decode("utf-8", "ignore").rstrip("\x00")
                    except Exception:
                        pass

            materials[mat_idx] = {
                "id": mat_idx,
                "name": mat_name,
                "tex_id": tex_id,
                "alpha_tex_id": alpha_tex_id,
            }
            mat_idx += 1
            pos += size
        else:
            pos += 4
    return materials


def parse_mdlb_geometry(data: bytes, pk3: bytes | None = None) -> dict:
    """Parse an MDLB/PK2B container into raw geometry (ported: mdlb_parser.parse_mdlb_geometry).

    `pk3` may be passed in when the caller already decompressed the container.
    LZ11 is not cheap (ry00 expands to 1.8 MB), so decompressing twice would
    double the cost of every room load.

    Returns the reference's dictionary shape: a global ``vertices``/``uvs``
    pool, per-shape records, per-material-group ``mesh_groups`` whose triangles
    carry absolute vertex indices, plus ``bones`` and ``materials``.  One
    triangle vertex is the 7-tuple::

        (position, uv, global_vertex_index, shape_local_uv_index,
         joints[4], weights[4], palette_colour_or_None)

    The shapes share one global vertex pool, so a shape's indices are offset by
    its ``vert_base`` while the UV index stays shape-local and the UV pair is
    already resolved inline.
    """
    if pk3 is None:
        pk3 = decompress_lz11(data)
    bones = parse_bones(pk3, search_start=0x80)
    bone_matrices = {b["id"]: b["matrix"] for b in bones}
    verified_rig = bool(bones) and all(
        b.get("matrix_layout") == "row_major_column_vector" for b in bones)
    materials = parse_materials(pk3)
    head_bone_id = next((b["id"] for b in bones if "head" in b["name"].lower()), 0)

    all_vertices: list[tuple[float, float, float]] = []
    all_uvs: list[tuple[float, float]] = []
    all_faces: list[tuple[int, int, int]] = []
    shape_list = []
    mesh_groups = []

    pos = 0x80
    s_idx = 0
    while pos < len(pk3) - 8:
        if pk3[pos:pos+4] == b"PAHS":
            shap_size = struct.unpack_from(">I", pk3, pos+4)[0]
            shap_end = pos + 8 + shap_size
            if shap_size < 0x18 or shap_end > len(pk3):
                raise FF2WError(f"PAHS at 0x{pos:X}: invalid/truncated shape")

            # Check if shape has VIDL display list
            v_check = pk3.find(b"LDIV", pos, shap_end)
            if v_check != -1:
                shape_vtxs = []
                shape_weights = []

                # Both GIEW (preweighted local positions) and rigid TREV use
                # the SAME resolved world bind matrices. The Xbox-derived
                # ENOB +0x50 records were inverse binds, not transposed worlds.
                w_cur = pk3.find(b"GIEW", pos, shap_end)
                while w_cur != -1 and w_cur < shap_end:
                    _checked_range(pk3, w_cur, 0x20, "GIEW header")
                    w_size = struct.unpack_from(">I", pk3, w_cur+4)[0]
                    w_cnt = struct.unpack_from(">H", pk3, w_cur+0x0c)[0]
                    w_str = struct.unpack_from(">H", pk3, w_cur+0x0e)[0]
                    nb = pk3[w_cur+0x12]
                    w_data = w_cur + 0x20
                    if (w_size < 0x18 or w_cur + 8 + w_size > shap_end
                            or (w_cnt and (not 1 <= nb <= 4 or w_str < 4 + 8 * nb))
                            or 0x20 + w_cnt * w_str > w_size + 8):
                        raise FF2WError(f"GIEW at 0x{w_cur:X}: invalid count/stride/influences")
                    vertex_base = struct.unpack_from(">H", pk3, w_cur + 0x10)[0]
                    if verified_rig and w_cnt and vertex_base != len(shape_vtxs):
                        raise FF2WError(f"GIEW at 0x{w_cur:X}: non-contiguous vertex base {vertex_base}")
                    for vi in range(w_cnt):
                        rec = pk3[w_data + vi*w_str : w_data + (vi+1)*w_str]
                        tot_x, tot_y, tot_z = 0.0, 0.0, 0.0
                        v_joints = [0, 0, 0, 0]
                        v_weights = [0.0, 0.0, 0.0, 0.0]
                        for k in range(nb):
                            b_idx = rec[k]
                            off = 4 + k * 8
                            px, py, pz = struct.unpack_from(">3h", rec, off)
                            w = 1.0 if nb == 1 else struct.unpack_from(">h", rec, off + 6)[0] / 1024.0
                            lx, ly, lz = px / 1024.0, py / 1024.0, pz / 1024.0
                            m = bone_matrices.get(b_idx)
                            if m:
                                # Verified Nintendo preweighted skinning formula:
                                wx = (m[0]*lx + m[1]*ly + m[2]*lz) + w * m[3]
                                wy = (m[4]*lx + m[5]*ly + m[6]*lz) + w * m[7]
                                wz = (m[8]*lx + m[9]*ly + m[10]*lz) + w * m[11]
                            else:
                                if verified_rig:
                                    raise FF2WError(f"GIEW at 0x{w_data + vi*w_str:X}: missing bone ID {b_idx}")
                                wx, wy, wz = lx, ly, lz
                            if verified_rig and not 0.0 <= w <= 1.0:
                                raise FF2WError(f"GIEW at 0x{w_data + vi*w_str:X}: invalid weight {w}")
                            tot_x += wx
                            tot_y += wy
                            tot_z += wz
                            if k < 4:
                                v_joints[k] = b_idx
                                v_weights[k] = max(0.0, w)
                        w_sum = sum(v_weights)
                        if verified_rig and abs(w_sum - 1.0) > 2.0 / 1024.0:
                            raise FF2WError(f"GIEW at 0x{w_data + vi*w_str:X}: weights sum to {w_sum}")
                        if w_sum > 1e-6:
                            v_weights = [w / w_sum for w in v_weights]
                        else:
                            v_weights = [1.0, 0.0, 0.0, 0.0]
                        shape_vtxs.append((tot_x, tot_y, tot_z))
                        shape_weights.append((v_joints, v_weights))
                    w_cur = pk3.find(b"GIEW", w_cur + w_size, shap_end)

                # Rigid TREV positions are local to PAHS's attached bone in
                # these rigs, not world-space positions with optional offsets.
                if len(shape_vtxs) == 0:
                    t_cur = pk3.find(b"TREV", pos, shap_end)
                    if t_cur != -1:
                        _checked_range(pk3, t_cur, 0x20, "TREV header")
                        t_size = struct.unpack_from(">I", pk3, t_cur+4)[0]
                        v_cnt = struct.unpack_from(">H", pk3, t_cur+10)[0]
                        pahs_hdr = struct.unpack_from(">16H", pk3, pos)
                        att_bone = pahs_hdr[10]
                        if verified_rig and att_bone not in bone_matrices:
                            raise FF2WError(f"PAHS at 0x{pos:X}: missing attached bone {att_bone}")
                        target_bone = att_bone if att_bone in bone_matrices else head_bone_id
                        if t_size < 0x18 or t_cur + 8 + t_size > shap_end:
                            raise FF2WError(f"TREV at 0x{t_cur:X}: invalid/truncated vertex chunk")

                        if v_cnt > 0:
                            data_bytes = t_size - 0x20
                            is_float = (data_bytes >= v_cnt * 10)
                            fraction = 10
                            if verified_rig:
                                component_type, fraction = pk3[t_cur + 8:t_cur + 10]
                                if component_type not in (3, 4):
                                    raise FF2WError(f"TREV at 0x{t_cur:X}: unsupported GX component type {component_type}")
                                is_float = component_type == 4
                            if v_cnt * (12 if is_float else 6) > data_bytes + 8:
                                raise FF2WError(f"TREV at 0x{t_cur:X}: vertices exceed chunk")
                            m = bone_matrices.get(target_bone)
                            if is_float:
                                for vi in range(v_cnt):
                                    x, y, z = struct.unpack_from(">3f", pk3, t_cur + 0x20 + vi*12)
                                    if m:
                                        wx = (m[0]*x + m[1]*y + m[2]*z) + m[3]
                                        wy = (m[4]*x + m[5]*y + m[6]*z) + m[7]
                                        wz = (m[8]*x + m[9]*y + m[10]*z) + m[11]
                                    else:
                                        wx, wy, wz = x, y, z
                                    shape_vtxs.append((wx, wy, wz))
                                    shape_weights.append(([target_bone, 0, 0, 0], [1.0, 0.0, 0.0, 0.0]))
                            else:
                                for vi in range(v_cnt):
                                    x, y, z = struct.unpack_from(">3h", pk3, t_cur + 0x20 + vi*6)
                                    divisor = float(2 ** fraction)
                                    lx, ly, lz = x / divisor, y / divisor, z / divisor
                                    if m:
                                        wx = (m[0]*lx + m[1]*ly + m[2]*lz) + m[3]
                                        wy = (m[4]*lx + m[5]*ly + m[6]*lz) + m[7]
                                        wz = (m[8]*lx + m[9]*ly + m[10]*lz) + m[11]
                                    else:
                                        wx, wy, wz = lx, ly, lz
                                    shape_vtxs.append((wx, wy, wz))
                                    shape_weights.append(([target_bone, 0, 0, 0], [1.0, 0.0, 0.0, 0.0]))

                # UVs (DCXT)
                shape_uvs = []
                tx_cur = pk3.find(b"DCXT", pos, shap_end)
                if tx_cur != -1:
                    _checked_range(pk3, tx_cur, 0x20, "DCXT header")
                    tx_size = struct.unpack_from(">I", pk3, tx_cur+4)[0]
                    uv_cnt = struct.unpack_from(">H", pk3, tx_cur+10)[0] if tx_cur + 12 <= len(pk3) else 0
                    if verified_rig and (tx_size < 0x18 or tx_cur + 8 + tx_size > shap_end
                            or 0x20 + uv_cnt * 4 > tx_size + 8 or pk3[tx_cur + 8] != 3):
                        raise FF2WError(f"DCXT at 0x{tx_cur:X}: invalid/unsupported UV array")
                    uv_divisor = float(2 ** pk3[tx_cur + 9]) if verified_rig else 1024.0
                    if uv_cnt == 0 or tx_cur + 0x20 + uv_cnt * 4 > len(pk3):
                        uv_cnt = (tx_size - 0x20) // 4
                    for uvi in range(uv_cnt):
                        u_raw, v_raw = struct.unpack_from(">2h", pk3, tx_cur + 0x20 + uvi*4)
                        shape_uvs.append((u_raw / uv_divisor, v_raw / uv_divisor))

                v_base = len(all_vertices)
                for v in shape_vtxs:
                    all_vertices.append(v)
                for uv in shape_uvs:
                    all_uvs.append(uv)

                shape_faces = []

                # Pass 1: decode every material group's display list of this
                # shape. The vertex colour palette size depends on the highest
                # colour index the WHOLE shape references, so it cannot be
                # resolved from the first group alone.
                decoded_groups: list[tuple[int, dict, list]] = []
                m_cur = pk3.find(b"HSEM", pos, shap_end)
                while m_cur != -1 and m_cur < shap_end:
                    _checked_range(pk3, m_cur, 0x20, "HSEM header")
                    msize = struct.unpack_from(">I", pk3, m_cur+4)[0]
                    if msize < 0x18 or m_cur + 8 + msize > shap_end:
                        raise FF2WError(f"HSEM at 0x{m_cur:X}: invalid/truncated group")
                    mat_id = struct.unpack_from(">h", pk3, m_cur + 0x14)[0]
                    mat_def = materials.get(mat_id, {})
                    ldiv = pk3.find(b"LDIV", m_cur, m_cur + msize)
                    if ldiv != -1:
                        lsize = struct.unpack_from(">I", pk3, ldiv+4)[0]
                        data_size = struct.unpack_from(">I", pk3, ldiv+12)[0] if ldiv + 16 <= len(pk3) else 0
                        actual_size = max(lsize, 0x20 + data_size)
                        vend = min(ldiv + actual_size, len(pk3))
                        raw_list = decode_display_list(
                            pk3[ldiv + 0x20:vend], len(shape_vtxs), len(shape_uvs)
                        )
                        if raw_list:
                            decoded_groups.append((mat_id, mat_def, raw_list))
                    m_cur = pk3.find(b"HSEM", m_cur + msize, shap_end)

                # Pass 2: resolve the palette once for the shape, from the
                # highest colour index any of its groups references. The
                # decoded triangles go along so candidates can be scored by
                # spatial coherence.
                cm = 0
                shape_tris: list = []
                for _mat_id, _mat_def, raw_list in decoded_groups:
                    for tri_idx in raw_list:
                        shape_tris.append(tri_idx)
                        for (_p, _t, ci) in tri_idx:
                            if ci > cm:
                                cm = ci
                shape_wants_colors = cm > 0
                shape_palette: list[tuple[int, int, int, int]] = []
                if shape_wants_colors:
                    shape_palette = find_vertex_color_table(
                        pk3, pos, shap_end, cm + 1, shape_tris
                    )

                # Pass 3: build the triangles
                for mat_id, mat_def, raw_list in decoded_groups:
                    tex_id = mat_def.get("tex_id", -1)
                    alpha_tex_id = mat_def.get("alpha_tex_id", -1)
                    group_tris = []
                    for tri_idx in raw_list:
                        vtx_list = []
                        for (pi, ti, ci) in tri_idx:
                            v_pos = shape_vtxs[pi] if pi < len(shape_vtxs) else (0.0, 0.0, 0.0)
                            v_uv = shape_uvs[ti] if ti < len(shape_uvs) else (0.0, 0.0)
                            v_sk = shape_weights[pi] if pi < len(shape_weights) else ([0, 0, 0, 0], [1.0, 0.0, 0.0, 0.0])
                            if shape_palette and 0 <= ci < len(shape_palette):
                                v_col = shape_palette[ci]
                            else:
                                # No colour: either the shape has no valid
                                # palette (its third record field is a second
                                # UV set, not a colour index) or this index
                                # falls outside it. Untinted beats inventing a
                                # colour: the transfer pass fills the local
                                # tint later.
                                v_col = None
                            vtx_list.append((v_pos, v_uv, v_base + pi, ti, v_sk[0], v_sk[1], v_col))
                        if (vtx_list[0][2] != vtx_list[1][2]
                                and vtx_list[1][2] != vtx_list[2][2]
                                and vtx_list[0][2] != vtx_list[2][2]):
                            tri = tuple(vtx_list)
                            group_tris.append(tri)
                            shape_faces.append((tri[0][2], tri[1][2], tri[2][2]))
                            all_faces.append((tri[0][2], tri[1][2], tri[2][2]))

                    if group_tris:
                        # Skip hidden belly skin patch in Shape 1 (Material 88)
                        # when top shirt (Shape 33) is worn.
                        is_hidden_patch = (s_idx == 1 and mat_id == 88)
                        if not is_hidden_patch:
                            if shape_palette:
                                lo = min(max(c[0], c[1], c[2]) for c in shape_palette)
                                hi = max(max(c[0], c[1], c[2]) for c in shape_palette)
                            else:
                                lo = hi = 0
                            mesh_groups.append({
                                "shape_index": s_idx,
                                "material_id": mat_id,
                                "material_name": mat_def.get("name", f"mat_{mat_id}"),
                                "tex_id": tex_id,
                                "alpha_tex_id": alpha_tex_id,
                                "triangles": group_tris,
                                "vertex_colors": bool(shape_palette),
                                "color_wanted": shape_wants_colors,
                                "color_lo": lo,
                                "color_hi": hi,
                            })

                shape_list.append({
                    "shape_index": s_idx,
                    "vertex_count": len(shape_vtxs),
                    "face_count": len(shape_faces),
                    "vert_base": v_base,
                })
            s_idx += 1
            pos += shap_size
        else:
            pos += 4

    transfer = _transfer_vertex_colors(mesh_groups)

    return {
        "vertices": all_vertices,
        "uvs": all_uvs,
        "triangles": all_faces,
        "bones": bones,
        "shapes": shape_list,
        "materials": materials,
        "mesh_groups": mesh_groups,
        "stats": {
            "vertex_count": len(all_vertices),
            "triangle_count": len(all_faces),
            "bone_count": len(bones),
            "shape_count": len(shape_list),
            "group_count": len(mesh_groups),
            "color_transferred": transfer.get("filled", 0),
        },
    }


def _transfer_vertex_colors(mesh_groups: list[dict]) -> dict:
    """Fill uncoloured vertices from the nearest coloured vertex.

    Ported: mdlb_parser._transfer_vertex_colors.

    Some room shapes reference no colour palette at all (their display-list
    field is a second UV set, and no coherent table exists anywhere for them),
    so they would render white. The ambient tint varies smoothly across a
    room, so copying the nearest decoded colour is far closer to the game
    look than white. A uniform grid keeps the nearest-neighbour search cheap.
    Groups that receive colours this way get ``colors_transferred`` set; the
    ``vertex_colors`` flag keeps meaning "decoded from a palette".
    """
    colored: list[tuple[float, float, float, tuple]] = []
    # Missing vertices are grouped by triangle so each triangle is rebuilt and
    # stored once.
    missing: dict[tuple[int, int], list[tuple[int, tuple]]] = {}
    n_missing = 0
    for gi, mg in enumerate(mesh_groups):
        for ti, tri in enumerate(mg.get("triangles") or []):
            for vi, v in enumerate(tri):
                if len(v) > 6 and v[6] is not None:
                    p = v[0]
                    colored.append((p[0], p[1], p[2], v[6]))
                else:
                    missing.setdefault((gi, ti), []).append((vi, v[0]))
                    n_missing += 1
    if not colored or not n_missing:
        return {"filled": 0, "missing": n_missing, "groups": 0}
    xs = [c[0] for c in colored]
    ys = [c[1] for c in colored]
    zs = [c[2] for c in colored]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    minz, maxz = min(zs), max(zs)
    span = max(maxx - minx, maxy - miny, maxz - minz) or 1.0
    cell = span / 64.0
    grid: dict[tuple[int, int, int], list[int]] = {}
    for idx, (x, y, z, _c) in enumerate(colored):
        key = (int((x - minx) / cell), int((y - miny) / cell), int((z - minz) / cell))
        grid.setdefault(key, []).append(idx)

    coarse_step = max(1, len(colored) // 2048)
    # Results are memoised on the exact vertex position: display lists repeat
    # positions heavily, so this cuts the searches with a bit-identical answer.
    found_cache: dict[tuple[float, float, float], int] = {}

    def nearest_from(px: float, py: float, pz: float) -> int:
        """Index of the closest coloured vertex to an exact position.

        Distance is measured from the real vertex position, not from the cell
        centre, so the answer is the true nearest neighbour among the coloured
        vertices reachable within 10 rings.
        """
        bx = int((px - minx) / cell)
        by = int((py - miny) / cell)
        bz = int((pz - minz) / cell)
        best = -1
        bestd = None
        for ring in range(0, 10):
            if best >= 0 and bestd is not None and (ring * cell) ** 2 > bestd:
                break
            if ring == 0:
                shells = ((0, 0, 0),)
            else:
                shells = []
                for dx in range(-ring, ring + 1):
                    for dy in range(-ring, ring + 1):
                        for dz in range(-ring, ring + 1):
                            if max(abs(dx), abs(dy), abs(dz)) == ring:
                                shells.append((dx, dy, dz))
            for dx, dy, dz in shells:
                pts = grid.get((bx + dx, by + dy, bz + dz))
                if not pts:
                    continue
                for idx in pts:
                    x, y, z, _c = colored[idx]
                    d2 = (x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2
                    if bestd is None or d2 < bestd:
                        bestd = d2
                        best = idx
        if best < 0:
            # Nothing in range: coarse sweep over all coloured vertices.
            for idx in range(0, len(colored), coarse_step):
                x, y, z, _c = colored[idx]
                d2 = (x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2
                if bestd is None or d2 < bestd:
                    bestd = d2
                    best = idx
        return best

    filled = 0
    filled_groups: set[int] = set()
    for (gi, ti), slots in missing.items():
        mg = mesh_groups[gi]
        tri = mg["triangles"][ti]
        lst = None
        for vi, pos in slots:
            pk = (pos[0], pos[1], pos[2])
            best = found_cache.get(pk)
            if best is None:
                best = nearest_from(pos[0], pos[1], pos[2])
                found_cache[pk] = best
            if best < 0:
                continue
            if lst is None:
                lst = list(tri)
            vv = list(lst[vi])
            vv[6] = colored[best][3]
            lst[vi] = tuple(vv)
            filled += 1
        if lst is not None:
            mg["triangles"][ti] = tuple(lst)
            filled_groups.add(gi)
    for gi in filled_groups:
        mesh_groups[gi]["colors_transferred"] = True
    return {"filled": filled, "missing": n_missing, "groups": len(filled_groups)}


def fix_bone_positions(geo: dict) -> dict:
    """Repair bones that sit at the origin (ported: mdlb_parser.fix_bone_positions).

    Some models carry cloth/hand/face bones whose matrix is all zeros around
    the origin instead of their real placement. Such a bone is snapped to its
    parent's translation when the parent is not itself at the origin.
    """
    bones = geo.get("bones", [])
    if not bones:
        return geo

    bone_map = {b['id']: b for b in bones}
    problematic_keywords = ['cloth', 'hand', 'face', 'sode', 'arm']

    for b in bones:
        # Resolved bind matrices are authoritative, including a legitimate
        # zero-origin joint. Never snap them to a parent based on a name.
        if b.get("matrix_layout") == "row_major_column_vector":
            continue
        name = b['name'].lower()
        m = b['matrix']
        pos = (m[3], m[7], m[11])

        is_at_origin = all(abs(p) < 0.01 for p in pos)
        should_have_position = any(kw in name for kw in problematic_keywords)

        if is_at_origin and should_have_position:
            parent_id = b['parent_id']
            if parent_id in bone_map:
                parent = bone_map[parent_id]
                pm = parent['matrix']
                parent_pos = (pm[3], pm[7], pm[11])
                if not all(abs(p) < 0.01 for p in parent_pos):
                    b['matrix'][3] = parent_pos[0]
                    b['matrix'][7] = parent_pos[1]
                    b['matrix'][11] = parent_pos[2]
    return geo


# =====================================================================
# Texture decoding (vendored: mdlb_parser.decode_cmpr_block /
# decode_cmpr_texture / decode_ci8_texture / parse_ppdb /
# load_and_composite_textures)
# =====================================================================

# PPDB header field holding the offset of the texture descriptor table.
PPDB_TABLE_BASE_OFFSET = 0x20
# PPDB texture descriptors are relative to that same base.
PPDB_TABLE_ENTRY_OFFSET = 0x0C
# Legacy TPL-base candidate, NOT the pixel-data base for every PPDB.
PPDB_DATA_BASE = 0x40

# GX texture formats seen in PPDB containers.
PPDB_FMT_CI8 = 9
PPDB_FMT_CMPR = 14


def decode_cmpr_block(data: bytes, offset: int) -> list:
    """Decode a single 4x4 DXT1/CMPR block into 16 RGBA tuples.

    Ported: mdlb_parser.decode_cmpr_block.
    """
    def unpack_rgb565(v):
        r = ((v >> 11) & 0x1F) * 255 // 31
        g = ((v >> 5) & 0x3F) * 255 // 63
        b = (v & 0x1F) * 255 // 31
        return (r, g, b, 255)

    if offset + 8 > len(data):
        return [(0, 0, 0, 255)] * 16

    c0v, c1v = struct.unpack_from(">2H", data, offset)
    lut_raw = struct.unpack_from(">I", data, offset + 4)[0]

    c0 = unpack_rgb565(c0v)
    c1 = unpack_rgb565(c1v)

    if c0v > c1v:
        c2 = ((2*c0[0]+c1[0])//3, (2*c0[1]+c1[1])//3, (2*c0[2]+c1[2])//3, 255)
        c3 = ((c0[0]+2*c1[0])//3, (c0[1]+2*c1[1])//3, (c0[2]+2*c1[2])//3, 255)
    else:
        c2 = ((c0[0]+c1[0])//2, (c0[1]+c1[1])//2, (c0[2]+c1[2])//2, 255)
        c3 = (0, 0, 0, 0)

    palette = [c0, c1, c2, c3]
    pixels = []
    for row in range(4):
        for col in range(4):
            shift = (row * 4 + col) * 2
            idx = (lut_raw >> (30 - shift)) & 3
            pixels.append(palette[idx])
    return pixels


def decode_cmpr_texture(data: bytes, width: int, height: int) -> bytes:
    """Decode Nintendo GX CMPR (DXT1 with 8x8 tiling) to RGBA bytes.

    Ported: mdlb_parser.decode_cmpr_texture.
    """
    if width <= 0 or height <= 0:
        raise FF2WError("CMPR: dimensions must be positive")
    tiles_x = (width + 7) // 8
    tiles_y = (height + 7) // 8
    _checked_range(data, 0, tiles_x * tiles_y * 32, "CMPR image")
    pixels = [(0, 0, 0, 255)] * (width * height)
    offset = 0

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            for sub_y in range(2):
                for sub_x in range(2):
                    block_pixels = decode_cmpr_block(data, offset)
                    offset += 8
                    for row in range(4):
                        for col in range(4):
                            px = tx * 8 + sub_x * 4 + col
                            py = ty * 8 + sub_y * 4 + row
                            if px < width and py < height:
                                pixels[py * width + px] = block_pixels[row * 4 + col]
    return bytes(b for px in pixels for b in px)


def _checked_range(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise FF2WError(f"{label} at 0x{max(offset, 0):X}: truncated/out-of-range data ({size} bytes)")


def decode_ci8_texture(pk2: bytes, data_start: int, width: int, height: int,
                       palette: list[tuple[int, int, int, int]] | None) -> bytes | None:
    """Decode GX CI8: 8x4 tiles of indices into a supplied RGBA TLUT.

    Edge tiles are stored in full; crop only after untile. A missing palette
    remains unresolved rather than being replaced with an invented grey ramp.
    """
    if width <= 0 or height <= 0 or not palette:
        return None
    tiles_x, tiles_y = (width + 7) // 8, (height + 3) // 4
    needed = tiles_x * tiles_y * 32
    _checked_range(pk2, data_start, needed, "CI8 image")
    indices = np.frombuffer(pk2, dtype=np.uint8, count=needed, offset=data_start)
    indices = indices.reshape(tiles_y, tiles_x, 4, 8).transpose(0, 2, 1, 3)
    indices = indices.reshape(tiles_y * 4, tiles_x * 8)[:height, :width]
    if int(indices.max()) >= len(palette):
        raise FF2WError(f"CI8 image at 0x{data_start:X}: index exceeds {len(palette)}-entry palette")
    return np.asarray(palette, dtype=np.uint8)[indices].tobytes()


def decode_gx_palette(payload: bytes, fmt: int) -> list[tuple[int, int, int, int]] | None:
    """Decode big-endian GX TLUT entries (IA8=0, RGB565=1, RGB5A3=2).

    Implemented from the component layouts; component expansion repeats bits,
    not floor(value*255/max). All supplied PPDB palettes are RGB5A3.
    """
    if fmt not in (0, 1, 2):
        return None
    if len(payload) % 2:
        raise FF2WError("GX palette: truncated 16-bit entry")
    out = []
    for (v,) in struct.iter_unpack(">H", payload):
        if fmt == 0:
            a, intensity = v >> 8, v & 255
            out.append((intensity, intensity, intensity, a))
        elif fmt == 1:
            r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
            out.append(((r << 3) | (r >> 2), (g << 2) | (g >> 4),
                        (b << 3) | (b >> 2), 255))
        elif v & 0x8000:
            r, g, b = (v >> 10) & 31, (v >> 5) & 31, v & 31
            out.append(((r << 3) | (r >> 2), (g << 3) | (g >> 2),
                        (b << 3) | (b >> 2), 255))
        else:
            a = (v >> 12) & 7
            out.append((((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17,
                        (v & 15) * 17, (a << 5) | (a << 2) | (a >> 1)))
    return out


def _read_ppdb_table(pk2: bytes) -> tuple[int, int]:
    """Locate the embedded TPL header; all its pointers share this base.

    +0x20 contains the base (0x80 in the three supplied PPDBs). Keep the legacy
    0x40 candidate, but require the TPL magic instead of accepting random counts.
    The header's +8 points to the descriptor-pair table (usually +0x0c).
    """
    if len(pk2) < PPDB_TABLE_BASE_OFFSET + 4:
        return 0, 0
    field = struct.unpack_from(">I", pk2, PPDB_TABLE_BASE_OFFSET)[0]
    for base in dict.fromkeys((field, PPDB_DATA_BASE)):
        if base < 0x20 or base + 12 > len(pk2):
            continue
        magic, count, table_rel = struct.unpack_from(">III", pk2, base)
        if magic != 0x0020AF30:
            continue
        if count > 8192 or table_rel < 12:
            raise FF2WError(f"PPDB table at 0x{base:X}: invalid header")
        _checked_range(pk2, base + table_rel, count * 8, "PPDB descriptor table")
        return base, count
    return 0, 0


def parse_ppdb(data: bytes) -> list[dict]:
    """Read embedded TPL image/palette pairs in a PPDB, preserving slot IDs.

    Each table entry is (image_descriptor_rel, palette_descriptor_rel).
    Image, palette and pixel offsets are relative to the SAME TPL base.
    Unsupported image/TLUT formats remain explicitly unresolved. A malformed
    known format raises an offset-specific FF2WError, never a false success.
    """
    pk2 = decompress_lz11(data)
    if len(pk2) < 0x50:
        return []
    base, tex_count = _read_ppdb_table(pk2)
    if not base:
        return []
    table = base + struct.unpack_from(">I", pk2, base + 8)[0]
    textures = []
    for i in range(tex_count):
        image_rel, palette_rel = struct.unpack_from(">II", pk2, table + i * 8)
        entry_off = base + image_rel
        _checked_range(pk2, entry_off, 36, f"PPDB image {i} descriptor")
        height, width, fmt, data_rel = struct.unpack_from(">HHII", pk2, entry_off)
        if not (0 < width <= 8192 and 0 < height <= 8192):
            raise FF2WError(f"PPDB image {i} at 0x{entry_off:X}: invalid dimensions {width}x{height}")
        pixel_off = base + data_rel
        entry = {
            "index": i, "width": width, "height": height,
            "format": {PPDB_FMT_CMPR: "CMPR", PPDB_FMT_CI8: "CI8"}.get(fmt, f"fmt_{fmt}"),
            "gx_format": fmt, "rgba_data": None, "resolved": False,
            "table_base": base, "descriptor_offset": entry_off,
            "data_offset": pixel_off, "palette_descriptor_offset": base + palette_rel if palette_rel else None,
        }
        if fmt == PPDB_FMT_CMPR:
            size = ((width + 7) // 8) * ((height + 7) // 8) * 32
            _checked_range(pk2, pixel_off, size, f"PPDB CMPR image {i}")
            entry["data_size"] = size
            entry["rgba_data"] = decode_cmpr_texture(pk2[pixel_off:pixel_off + size], width, height)
            entry["resolved"] = True
        elif fmt == PPDB_FMT_CI8:
            size = ((width + 7) // 8) * ((height + 3) // 4) * 32
            _checked_range(pk2, pixel_off, size, f"PPDB CI8 image {i}")
            entry["data_size"] = size
            palette = None
            if palette_rel:
                pal_off = base + palette_rel
                _checked_range(pk2, pal_off, 12, f"PPDB image {i} palette descriptor")
                count, _flags, pal_fmt, pal_data_rel = struct.unpack_from(">HHII", pk2, pal_off)
                if not 0 < count <= 256:
                    raise FF2WError(f"PPDB CI8 palette at 0x{pal_off:X}: invalid entry count {count}")
                pal_data_off = base + pal_data_rel
                _checked_range(pk2, pal_data_off, count * 2, f"PPDB image {i} palette")
                entry.update(palette_entries=count, palette_format=pal_fmt,
                             palette_data_offset=pal_data_off)
                palette = decode_gx_palette(pk2[pal_data_off:pal_data_off + count * 2], pal_fmt)
            entry["rgba_data"] = decode_ci8_texture(pk2, pixel_off, width, height, palette)
            entry["resolved"] = entry["rgba_data"] is not None
        textures.append(entry)
    return textures


def composite_alpha_variant(base_rgba: bytes, width: int, height: int,
                            mask_rgba: bytes, mask_width: int,
                            mask_height: int) -> bytes:
    """Return ``base_rgba`` with the mask's red channel moved into alpha.

    Ported from the body of ``mdlb_parser.load_and_composite_textures``. MDLB
    stores cutout masks (lashes, hair, lace) as a separate texture referenced by
    ``ETAM`` short 14; the game samples the mask's red channel as coverage. The
    diffuse image is left untouched, so the composite can be uploaded as a
    single RGBA texture and the material needs no second sampler.
    """
    if mask_width == width and mask_height == height:
        alpha_rgba = mask_rgba
    else:
        mask = Image.frombytes("RGBA", (mask_width, mask_height), bytes(mask_rgba))
        mask = mask.resize((width, height), Image.Resampling.BILINEAR)
        alpha_rgba = mask.tobytes()

    masked = bytearray(base_rgba)
    num_pixels = width * height
    for p in range(num_pixels):
        masked[p * 4 + 3] = alpha_rgba[p * 4]
    return bytes(masked)


# =====================================================================
# Adapter: reference output -> viewer SGDModel / SGDMesh / SGDBone /
# SGDMaterial
# =====================================================================


def _read_data(path_or_bytes) -> tuple[bytes, str | None]:
    """Return (data, default name) for a path or an in-memory buffer."""
    if isinstance(path_or_bytes, (str, os.PathLike)):
        path = Path(path_or_bytes)
        return path.read_bytes(), path.stem
    return bytes(path_or_bytes), None


def detect_ff2w_container(data: bytes) -> tuple[str, bytes]:
    """Decompress and classify an FF2W container.

    Mirrors the detection in the reference's ``analyze()``: LZ11 first, then the
    three-byte magic at offset 4. Returns ``(kind, container)`` with kind in
    ``"character"`` (pk3), ``"room"`` (pk2) or None. Raises ``FF2WError`` for
    anything else so a wrong file fails loudly instead of producing an empty
    scene.
    """
    container = decompress_lz11(data)
    magic = container[4:7] if len(container) >= 7 else b""
    if magic == CONTAINER_PK3:
        return "character", container
    if magic == CONTAINER_PK2:
        return "room", container
    raise FF2WError(
        "not a Fatal Frame 2 Wii container: expected a pk3/pk2 magic at offset "
        f"0x04, found {magic!r} in {len(data)} bytes"
    )


def is_ff2w_asset(path) -> bool:
    """True when `path` is a Fatal Frame 2 Wii ``.mdlb`` / ``.pk2b`` asset.

    The container magic is what decides, so a PS2 ``.mdlb`` from another
    release cannot be mistaken for one of these; the extension is only used to
    short-circuit obvious non-candidates before reading the file.
    """
    try:
        candidate = Path(path)
    except TypeError:
        return False
    if candidate.suffix.lower() not in FF2W_EXTENSIONS:
        return False
    try:
        data = candidate.read_bytes()
    except OSError:
        return False
    if len(data) < 8:
        return False
    try:
        detect_ff2w_container(data)
    except FF2WError:
        return False
    return True


def find_ff2w_texture_file(path) -> Path | None:
    """Return the PPDB paired with an MDLB/PK2B model, or None.

    Same lookup order as the reference viewer's ``find_ppdb``, so both tools
    pick the same file: same folder first, then the shared ``character`` and
    ``item`` pools of the extracted ``3ddata`` tree.
    """
    candidate = Path(path)
    stem = candidate.stem
    roots = []
    for parent in (candidate.parent, *candidate.parent.parents):
        roots.append(parent)
        if parent.name.lower() == "3ddata":
            break
    pool = roots[-1] if roots else candidate.parent
    options = [
        candidate.with_suffix(".ppdb"),
        candidate.parent / f"{stem}.ppdb",
        pool / "character" / f"{stem}.ppdb",
        pool / "item" / f"{stem}.ppdb",
        pool / f"{stem}.ppdb",
    ]
    for option in options:
        if option.is_file():
            return option
    return None


def _build_materials(material_defs: dict[int, dict]) -> tuple[list, dict]:
    """Build ``SGDMaterial`` list plus a material-id -> slot index map.

    Only texture bindings are decoded from ETAM here, so the adapter keeps
    neutral lighting defaults rather than claiming the game's material state. ``tex_id`` is mirrored into ``tbp0`` as well: that is the slot
    every generic texture mapper in this viewer keys off, and for FF2W the PPDB
    descriptor index plays exactly that role. ``tex_id`` / ``alpha_tex_id`` are
    kept as plain attributes for the caller to bind with ``FF2WTextureSet``.
    """
    materials = []
    slots: dict[int, int] = {}
    for mat_id in sorted(material_defs):
        definition = material_defs[mat_id]
        material = SGDMaterial(len(materials), definition.get("name") or f"mat_{mat_id}")
        material.tex_id = definition.get("tex_id", -1)
        material.alpha_tex_id = definition.get("alpha_tex_id", -1)
        material.tbp0 = material.tex_id
        material.tex0_low = 0
        material.texture_index = -1
        slots[mat_id] = len(materials)
        materials.append(material)
    return materials, slots


def _extra_material_slot(materials: list, slots: dict, mat_id: int) -> int:
    """Return the slot for a material a group references but ETAM never declared.

    Happens with negative or out-of-range ``HSEM`` ids. A placeholder keeps the
    group's ``material_index`` valid instead of pointing at material 0.
    """
    existing = slots.get(mat_id)
    if existing is not None:
        return existing
    material = SGDMaterial(len(materials), f"mat_{mat_id}")
    material.tex_id = -1
    material.alpha_tex_id = -1
    material.texture_index = -1
    slots[mat_id] = len(materials)
    materials.append(material)
    return len(materials) - 1


# Below this magnitude a matrix element counts as zero when deciding which slot
# holds a bone translation. Real bone offsets in this game span single units
# (sub-millimetre at 0.05 m/unit), so 1e-4 is far under anything meaningful and
# far over the float noise in an identity matrix.
_BONE_TRANSLATION_EPSILON = 1e-4


def _affine_row_major(raw: list[float]) -> list[float]:
    """Legacy storage-layout adapter (not an inverse-bind resolver).

    Returns a flat column-major matrix for SGDBone/glTF. It keeps the old
    ordinary-Wii fallback behaviour. Verified matrices bypass this heuristic
    in _build_bones so zero-translation rotations cannot be mis-transposed.
    """
    column_major = any(abs(raw[k]) > _BONE_TRANSLATION_EPSILON for k in (3, 7, 11))
    row_major = any(abs(raw[k]) > _BONE_TRANSLATION_EPSILON for k in (12, 13, 14))
    if column_major and not row_major:
        return [raw[0], raw[4], raw[8], raw[12],
                raw[1], raw[5], raw[9], raw[13],
                raw[2], raw[6], raw[10], raw[14],
                raw[3], raw[7], raw[11], raw[15]]
    # Row-major already, or no translation at all. In the latter case the affine
    # row being (0, 0, 0, 1) is exactly what makes the row-major reading valid,
    # so keeping the array untouched is the right tie-break for rig roots and
    # for the single identity bone every room carries.
    return list(raw[:16])


def _build_bones(bone_defs: list[dict], scale: float,
                 offset: tuple[float, float, float]) -> list:
    """Build the ``SGDBone`` list from the reference's ENOB records.

    Parent links are remapped from the file's bone *id* space to the list index
    and validated: anything unknown, self-referential, or part of a cycle
    becomes -1 so the viewer's skeleton walk cannot loop.

    Verified world binds are converted explicitly to SGDBone's column-major
    layout. Only translations change units; unit conversion must not scale
    a joint's rotation. The legacy native path keeps its previous adapter
    behaviour and is tagged for its existing export-time scale compensation.
    """
    id_to_index = {b["id"]: i for i, b in enumerate(bone_defs)}
    parents = []
    for position, bone in enumerate(bone_defs):
        parent_id = bone.get("parent_id", -1)
        parent = id_to_index.get(parent_id, -1)
        if parent < 0 or parent >= len(bone_defs) or parent == position:
            parent = -1
        parents.append(parent)

    # Break any remaining cycle by detaching the deepest member of it.
    for start in range(len(parents)):
        seen = set()
        node = start
        while node >= 0 and node not in seen:
            seen.add(node)
            node = parents[node]
        if node in seen:
            parents[start] = -1

    ox, oy, oz = offset
    bones = []
    for position, definition in enumerate(bone_defs):
        raw = list(definition.get("matrix") or [])
        if len(raw) < 16:
            raw = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                   0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        else:
            raw = raw[:16]
        raw = [value if math.isfinite(value) else 0.0 for value in raw]
        verified = definition.get("matrix_layout") == "row_major_column_vector"
        normalized = (np.asarray(raw).reshape(4, 4).T.ravel().tolist()
                      if verified else _affine_row_major(raw))

        matrix = [0.0] * 16
        # Change position units, not joint scale. Keep the legacy scale only
        # on the unchanged native path (its export adapter already removes it).
        rotation_scale = 1.0 if verified else scale
        for i in range(12):
            matrix[i] = normalized[i] * rotation_scale
        matrix[12] = normalized[12] * scale - ox
        matrix[13] = normalized[13] * scale - oy
        matrix[14] = normalized[14] * scale - oz
        matrix[15] = normalized[15]

        bone = SGDBone(position, parents[position])
        bone.matrix = matrix
        bone.ff2w_rotation_scaled = not verified
        bone.ff2w_matrix_semantics = definition.get("matrix_semantics", "legacy_world")
        bone.trans = [matrix[12], matrix[13], matrix[14]]
        bone.rot = [0.0, 0.0, 0.0, 0.0]
        # Carry the file's own bone name through. Without it every bone reaches
        # the glTF exporter nameless and comes out as "Bone_07", which is what
        # the MDLB Viewer avoids by writing the ENOB name ("model_7") it read.
        name = definition.get("name")
        if name:
            bone.name = str(name)
        bones.append(bone)
    return bones



def _vertex_color_range(groups: list[dict]) -> tuple[int, int]:
    """Measure the vertex colour range of the whole model, once.

    Ported from ``_compute_vcol_range`` in the reference viewer. Two decisions
    matter and both are the opposite of the obvious per-channel scaling:

    * the range is measured over *every* group of the file, not per group, so
      surfaces keep their relative brightness (a wall stays dark next to a
      glowing portal instead of each being stretched to full brightness);
    * the top of the range is the 99th percentile rather than the maximum,
      because a handful of bright garbage entries would otherwise stretch the
      scale and sink the whole model into darkness.

    ``lo`` comes from ``max(r, g, b)`` per vertex, the same statistic the
    reference uses to build the palette bounds. Returns ``(0, 0)`` when the
    model carries no colours, which the caller reads as "leave it untinted".
    """
    peaks = []
    low = 255
    for group in groups:
        for triangle in group.get("triangles") or []:
            for vertex in triangle:
                colour = vertex[6] if len(vertex) > 6 else None
                if not colour:
                    continue
                peak = max(colour[0], colour[1], colour[2])
                peaks.append(peak)
                if peak < low:
                    low = peak
    if not peaks:
        return 0, 0
    peaks.sort()
    high = peaks[min(len(peaks) - 1, int(len(peaks) * 0.99))]
    if high < low:
        return 0, 0
    return low, high


def _remap_vertex_colors(raw_colors: list, low: int, high: int) -> list:
    """Apply the reference's absolute-brightness remap to palette colours.

    Room vertex colours are stored at their absolute in-game ambient
    brightness: the measured median luminance is around 20/255 while the
    brightest vertex reaches roughly 100/255, so using them raw multiplies every
    texture to black. The range is therefore remapped linearly onto
    ``[VERTEX_COLOR_FLOOR, 1]`` -- deep shadow falls almost to black while lit
    rock stays warm brown.

    Note what is deliberately *not* here: a per-channel ``x/128 if x > 1 else
    x``. Deciding the scale per channel turns a neutral palette entry whose
    channels straddle 1.0 (r=0.72, g=b=1.99) into saturated red, because red is
    left alone while green and blue are divided by 128. One scale for all three
    channels, derived once from the max-channel statistics, is the only
    self-consistent reading.

    The palette's alpha byte is dropped (1.0) on purpose. Measured over 594271
    palette entries across the room files it is 0x00 or 0xFF on 99.665% of them
    but has no relationship to visibility -- alpha=0x00 is the brightest field
    in ry00 and ry08 and the darkest in rkh00 -- it is a weight between baked
    ambient and dynamic light. Feeding it to the viewer as transparency would
    delete large parts of the room. Real cutouts come from the texture alpha
    instead, which is preserved in ``parse_ff2w_textures``.
    """
    if high <= low:
        return [[1.0, 1.0, 1.0, 1.0] for _ in raw_colors]
    span = float(high - low)
    floor = VERTEX_COLOR_FLOOR
    gain = 1.0 - floor
    out = []
    for colour in raw_colors:
        out.append([
            min(1.0, max(0.0, floor + (colour[0] - low) / span * gain)),
            min(1.0, max(0.0, floor + (colour[1] - low) / span * gain)),
            min(1.0, max(0.0, floor + (colour[2] - low) / span * gain)),
            1.0,
        ])
    return out


def _compute_normals(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Area-weighted smooth normals for one mesh, normalised.

    MRON and packed normal records do exist, but this adapter does not decode
    them yet. Keep the existing area-weighted reconstruction; vertices touched
    only by degenerate triangles get the legacy (0, 1, 0) fallback.
    """
    vertex_count = positions.shape[0]
    if vertex_count == 0 or indices.size == 0:
        return np.zeros((vertex_count, 3), dtype=np.float64)

    corner = positions[indices[:, 0]]
    edge1 = positions[indices[:, 1]] - corner
    edge2 = positions[indices[:, 2]] - corner
    face = np.cross(edge1, edge2)

    flat = indices.ravel()
    accumulated = np.empty((vertex_count, 3), dtype=np.float64)
    for axis in range(3):
        accumulated[:, axis] = np.bincount(
            flat, weights=np.repeat(face[:, axis], 3), minlength=vertex_count
        )[:vertex_count]

    lengths = np.linalg.norm(accumulated, axis=1)
    safe = lengths > 1e-9
    normals = np.zeros_like(accumulated)
    normals[safe] = accumulated[safe] / lengths[safe, None]
    normals[~safe] = (0.0, 1.0, 0.0)
    return normals


def _vertex_key(vertex) -> tuple[int, float, float]:
    """Dedup key for one triangle vertex: its position slot *and* its UV.

    Keying on the position slot alone is what collapses texture seams. The
    reference emits one entry per triangle corner, so a position on a seam
    appears twice with different UVs and both are real; folding them into one
    entry keeps a single UV and stretches the triangles that should have used
    the other. See ``_build_meshes``.
    """
    uv = vertex[1] if len(vertex) > 1 else (0.0, 0.0)
    return (vertex[2], float(uv[0]), float(uv[1]))


def _build_meshes(groups: list[dict], materials: list, material_slots: dict,
                  bone_id_to_index: dict, low: int, high: int) -> list:
    """Turn the reference's per-shape material groups into viewer meshes.

    The reference emits one triangle list per shape+material group, with
    vertices addressed by *absolute* index into the whole file's vertex pool.
    Two groups of the same shape therefore share vertices, and a group only ever
    uses part of its shape's range, so neither the absolute index nor "copy the
    shape's vertices" can be handed to the viewer as-is: the viewer's index
    buffer has to address its own vertex arrays.

    Each mesh therefore keeps only the vertices its own triangles reference, in
    first-use order, and remaps every index into that compact list. Building the
    vertex list *from* the indices is what makes an out-of-range index
    impossible by construction instead of something that has to be validated
    afterwards; ``_verify_indices`` re-checks it anyway, because this is the
    single most common way an adapter of this shape breaks.

    Sharing the absolute index as the dedup key also makes the per-vertex
    attributes consistent: the same vertex index reached from two different
    display lists of one group resolves to one entry.

    The key has to carry the UV as well, though. A position sitting on a
    texture seam is deliberately referenced with two different UVs, so keying on
    the position alone collapses the seam into one entry and throws the second UV
    away. Every triangle on that seam then interpolates between a UV it should not
    use and its own, stretching it across the atlas: long smeared triangles in a
    UV view, and a texture that slides along the surface instead of sticking to
    it. Keying on (position, UV) keeps both sides of a seam while still sharing
    the vertices that genuinely repeat.
    """
    meshes = []
    for group in groups:
        triangles = group.get("triangles") or []
        if not triangles:
            continue

        shape_index = group.get("shape_index", 0)
        mat_id = group.get("material_id", -1)
        mat_slot = _extra_material_slot(materials, material_slots, mat_id)

        # First-use order keeps the vertex arrays cache-friendly and stable for
        # a given file, and keeps the reported vertex count honest.
        order: list[tuple[int, float, float]] = []
        remap: dict[tuple[int, float, float], int] = {}
        slots: dict[tuple[int, float, float], dict] = {}
        for triangle in triangles:
            for vertex in triangle:
                key = _vertex_key(vertex)
                if key in remap:
                    continue
                remap[key] = len(order)
                order.append(key)
                src_joints = vertex[4] if len(vertex) > 4 else (0, 0, 0, 0)
                src_weights = vertex[5] if len(vertex) > 5 else (1.0, 0.0, 0.0, 0.0)
                uv = vertex[1] if len(vertex) > 1 else (0.0, 0.0)
                slots[key] = {
                    "position": (float(vertex[0][0]), float(vertex[0][1]),
                                 float(vertex[0][2])),
                    "uv": [float(uv[0]), float(uv[1])],
                    "color": vertex[6] if len(vertex) > 6 else None,
                    "joints": [
                        bone_id_to_index.get(int(src_joints[k]), 0)
                        if k < len(src_joints) else 0
                        for k in range(4)
                    ],
                    "weights": [
                        float(src_weights[k]) if k < len(src_weights) else 0.0
                        for k in range(4)
                    ],
                }

        positions = np.array([slots[a]["position"] for a in order], dtype=np.float64)
        # A misdecoded record can still hand through a NaN; the viewer's buffer
        # upload would then propagate it into every normal in the mesh.
        positions = np.nan_to_num(positions, nan=0.0, posinf=0.0, neginf=0.0)

        indices = [
            [remap[_vertex_key(triangle[0])],
             remap[_vertex_key(triangle[1])],
             remap[_vertex_key(triangle[2])]]
            for triangle in triangles
        ]
        indices_array = np.array(indices, dtype=np.int64)

        raw_colors = [slots[a]["color"] for a in order]
        if raw_colors and any(colour is not None for colour in raw_colors):
            # Any vertex without a palette entry keeps the untinted default,
            # which is what the reference viewer draws for it too.
            colours = _remap_vertex_colors(
                [c if c is not None else (high, high, high, 255) for c in raw_colors],
                low, high,
            )
        else:
            colours = [[1.0, 1.0, 1.0, 1.0]] * len(order)

        joints = [slots[a]["joints"] for a in order]
        weights = [slots[a]["weights"] for a in order]

        mesh = SGDMesh(f"shape{shape_index}_mat{mat_id}")
        mesh.material_index = mat_slot
        mesh.positions = positions.tolist()
        mesh.normals = _compute_normals(positions, indices_array).tolist()
        mesh.uvs = [list(slots[a]["uv"]) for a in order]
        mesh.colors = colours
        mesh.indices = indices
        mesh.joints = joints
        mesh.weights = weights
        mesh.bone_index = _dominant_bone(joints, weights, len(bone_id_to_index))
        # Kept so the caller (and the server wiring) can trace a mesh back to
        # its source group without re-running the reference parser.
        mesh.shape_index = shape_index
        mesh.material_id = mat_id
        mesh.tex_id = group.get("tex_id", -1)
        mesh.alpha_tex_id = group.get("alpha_tex_id", -1)
        meshes.append(mesh)
    return meshes


def _dominant_bone(joints: list[list], weights: list[list], bone_count: int) -> int:
    """Bone with the largest accumulated skinning weight on this mesh.

    ``server.py`` reports one ``bone_index`` per mesh and the viewer's mesh
    filtering keys off it, so it has to name a real bone. The weight sum is a
    better answer than "first joint seen" for a mesh that straddles two bones.
    """
    if not joints or bone_count <= 0:
        return 0
    totals: dict[int, float] = {}
    for joint_row, weight_row in zip(joints, weights):
        for joint, weight in zip(joint_row, weight_row):
            if 0 <= joint < bone_count and weight > 0.0:
                totals[joint] = totals.get(joint, 0.0) + weight
    if not totals:
        return 0
    best = max(totals.items(), key=lambda item: item[1])[0]
    return best if 0 <= best < bone_count else 0


def build_ff2w_export_model(model: SGDModel) -> SGDModel:
    """Prepare a copy of a viewer-scaled Wii model for the shared GLB writer.

    Keep the existing policy: retain ff2w_applied_scale, undo the parser's
    re-centering, and expand triangle corners with flat normals. Geometry and
    bone translations stay in the same units. Only legacy bones that actually
    received a 0.05 rotation-block factor have it removed; verified binds
    already have unscaled rotations. The input remains untouched.

    A direct CLI model without ff2w_applied_scale is returned as-is, in the
    parser's normalized units. See docs/architecture/gltf-export-pipeline.md.
    """
    applied = getattr(model, "ff2w_applied_scale", None)
    if not applied:
        return model

    # parse_ff2w_model wrote (raw - centre) * MODEL_UNIT_TO_METRES and the load
    # multiplied that by `applied`, so adding the re-basing offset back in the
    # same units yields raw * MODEL_UNIT_TO_METRES * applied: the file's own
    # origin, in this viewer's unit.
    applied = float(applied)
    ox, oy, oz = (float(c) * applied
                  for c in getattr(model, "ff2w_origin_offset", (0.0, 0.0, 0.0)))
    out = copy.deepcopy(model)

    for mesh in out.meshes:
        mesh.positions = [
            [p[0] + ox, p[1] + oy, p[2] + oz]
            for p in mesh.positions
        ]
        mesh.uvs = [list(uv) for uv in getattr(mesh, "uvs", [])]
        mesh.indices = _expand_to_triangle_corners(mesh)

    for bone in out.bones:
        matrix = getattr(bone, "matrix", None)
        if matrix is not None and len(matrix) >= 16:
            matrix = list(matrix)
            # Remove only the compatibility factor actually introduced by
            # _build_bones. Do not rescale the verified Xbox-derived binds.
            block_factor = (1.0 / MODEL_UNIT_TO_METRES
                            if getattr(bone, "ff2w_rotation_scaled", True) else 1.0)
            for i in range(12):
                matrix[i] *= block_factor
            matrix[12] = matrix[12] + ox
            matrix[13] = matrix[13] + oy
            matrix[14] = matrix[14] + oz
            bone.matrix = matrix
            bone.trans = [matrix[12], matrix[13], matrix[14]]

    return out


def _expand_to_triangle_corners(mesh: SGDMesh) -> list[list[int, int, int]]:
    """Rebuild ``mesh`` with one vertex per triangle corner and flat normals.

    This is the layout ``export_to_gltf`` produces: it walks the triangle list,
    appends three fresh vertices per triangle and hands the exporter sequential
    indices. Sharing vertices across triangles would drop one side of a UV
    seam, and the smooth normals the viewport wants are not what the exporter
    writes, so both arrays are rebuilt rather than remapped.
    """
    positions = getattr(mesh, "positions", [])
    uvs = getattr(mesh, "uvs", [])
    colors = getattr(mesh, "colors", [])
    joints = getattr(mesh, "joints", [])
    weights = getattr(mesh, "weights", [])

    new_positions: list[list[float]] = []
    new_uvs: list[list[float]] = []
    new_colors: list[list[float]] = []
    new_joints: list[list[int]] = []
    new_weights: list[list[float]] = []
    new_normals: list[list[float]] = []
    new_indices: list[list[int, int, int]] = []

    for tri in mesh.indices:
        base = len(new_positions)
        for corner in tri:
            new_positions.append([float(c) for c in positions[corner]])
            new_uvs.append([float(c) for c in uvs[corner]] if corner < len(uvs) else [0.0, 0.0])
            new_colors.append(
                [float(c) for c in colors[corner]]
                if corner < len(colors) else [1.0, 1.0, 1.0, 1.0]
            )
            new_joints.append(
                [int(c) for c in joints[corner]]
                if corner < len(joints) else [0, 0, 0, 0]
            )
            new_weights.append(
                [float(c) for c in weights[corner]]
                if corner < len(weights) else [1.0, 0.0, 0.0, 0.0]
            )
            new_normals.append([0.0, 1.0, 0.0])

        # One face normal shared by the three corners, matching the reference's
        # per-triangle cross product.
        a, b, c = new_positions[base], new_positions[base + 1], new_positions[base + 2]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        length = (nx * nx + ny * ny + nz * nz) ** 0.5
        if length > 1e-6:
            nx, ny, nz = nx / length, ny / length, nz / length
        else:
            nx, ny, nz = 0.0, 1.0, 0.0
        for k in range(3):
            new_normals[base + k] = [nx, ny, nz]

        new_indices.append([base, base + 1, base + 2])

    mesh.positions = new_positions
    mesh.normals = new_normals
    mesh.uvs = new_uvs
    mesh.colors = new_colors
    mesh.joints = new_joints
    mesh.weights = new_weights
    mesh.indices = new_indices
    return new_indices


def _verify_indices(model: SGDModel) -> None:
    """Fail loudly if any mesh's index buffer addresses outside its arrays.

    This is the classic failure mode of a per-shape to per-mesh conversion and
    it is invisible in a screenshot (the mesh silently disappears) while being
    fatal in a WebGL buffer upload, so it is checked explicitly on every load
    rather than trusted.
    """
    for mesh in model.meshes:
        count = len(mesh.positions)
        if not (len(mesh.normals) == len(mesh.uvs) == len(mesh.colors)
                == len(mesh.joints) == len(mesh.weights) == count):
            raise FF2WError(
                f"mesh {mesh.name!r}: attribute arrays disagree "
                f"(positions={count}, normals={len(mesh.normals)}, "
                f"uvs={len(mesh.uvs)}, colors={len(mesh.colors)})"
            )
        for triangle in mesh.indices:
            for index in triangle:
                if not 0 <= index < count:
                    raise FF2WError(
                        f"mesh {mesh.name!r}: index {index} outside "
                        f"{count} vertices"
                    )


def _bind_textures(model: SGDModel, textures) -> None:
    """Point every ``SGDMaterial.texture_index`` at the image it samples.

    A material whose ``ETAM`` declares an alpha mask prefers the composited
    variant (``tex_id`` + mask) because that is what the game renders; anything
    else falls back to the plain diffuse image. Materials without a usable
    texture stay at -1, which the viewer renders untextured.
    """
    if textures is None:
        return
    for material in model.materials:
        tex_id = getattr(material, "tex_id", -1)
        alpha_tex_id = getattr(material, "alpha_tex_id", -1)
        slot = textures.index_for_alpha(tex_id, alpha_tex_id)
        if slot < 0:
            slot = textures.index_for_tex(tex_id)
        material.texture_index = slot


def parse_ff2w_model(path_or_bytes, name=None, textures=None) -> SGDModel:
    """Parse a Fatal Frame 2 Wii ``.mdlb`` or ``.pk2b`` into a viewer model.

    `path_or_bytes` is a path or the raw file bytes. `name` overrides the model
    name (defaulting to the file stem). `textures`, when given, is an
    ``FF2WTextureSet`` (or any list of images from ``parse_ff2w_textures``) and
    is bound to the materials straight away, so a caller that wants a fully
    textured model can hand the PPDB in and be done.

    Both containers go through one geometry pipeline -- only the container magic
    differs -- so the dispatch below exists to label the model, not to fork the
    parse. ``.mdlb`` normally carries characters; ``.pk2b`` also covers props
    with multiple bones, not only rooms with a single dummy bone.

    Coordinates are converted to metres and re-based (feet on ``y = 0``,
    footprint centred) so the viewer's cameras frame a 1.3-1.6 m character the
    same way they frame a PS2 one.
    """
    data, default_name = _read_data(path_or_bytes)
    kind, container = detect_ff2w_container(data)

    geometry = parse_mdlb_geometry(data, pk3=container)
    geometry = fix_bone_positions(geometry)
    groups = geometry.get("mesh_groups") or []
    if not groups:
        raise FF2WError(
            "no decodable geometry (no PAHS shape with a display list survived "
            "the stride detection)"
        )

    model = SGDModel(name or default_name or "ff2w_model")
    # Raw DCXT UVs match the PPDB's top-down image rows. For verified imported
    # rigs expose that orientation also to direct exports, not just server.py
    # (which already sets this flag for every Wii asset before display).
    model.uvs_are_flipped = all(
        b.get("matrix_layout") == "row_major_column_vector"
        for b in geometry.get("bones", [])) and bool(geometry.get("bones"))

    materials, material_slots = _build_materials(geometry.get("materials") or {})
    bone_defs = geometry.get("bones") or []
    bone_id_to_index = {b["id"]: i for i, b in enumerate(bone_defs)}

    low, high = _vertex_color_range(groups)
    meshes = _build_meshes(groups, materials, material_slots, bone_id_to_index,
                           low, high)

    # Bounds from the built geometry rather than from the reference's pool: a
    # shape can contribute vertices that no group ends up drawing, and the
    # camera only ever frames what is drawn.
    all_positions = np.concatenate(
        [np.array(mesh.positions, dtype=np.float64) for mesh in meshes], axis=0
    )
    raw_min = all_positions.min(axis=0)
    raw_max = all_positions.max(axis=0)
    centre_x = (raw_min[0] + raw_max[0]) * 0.5
    centre_z = (raw_min[2] + raw_max[2]) * 0.5
    offset = (centre_x * MODEL_UNIT_TO_METRES,
              raw_min[1] * MODEL_UNIT_TO_METRES,
              centre_z * MODEL_UNIT_TO_METRES)

    for mesh in meshes:
        positions = np.array(mesh.positions, dtype=np.float64)
        positions[:, 0] = (positions[:, 0] - centre_x) * MODEL_UNIT_TO_METRES
        positions[:, 1] = (positions[:, 1] - raw_min[1]) * MODEL_UNIT_TO_METRES
        positions[:, 2] = (positions[:, 2] - centre_z) * MODEL_UNIT_TO_METRES
        mesh.positions = positions.tolist()

    model.materials = materials
    model.meshes = meshes
    # The vertices and bones below are re-based into a centred, feet-at-zero
    # space. The glTF export has to write the MDLB Viewer's coordinates instead,
    # which are raw * MODEL_UNIT_TO_METRES with no re-basing, so the offset that
    # was subtracted is kept here to be added back at export time.
    model.ff2w_origin_offset = [float(offset[0]), float(offset[1]), float(offset[2])]
    model.bones = _build_bones(bone_defs, MODEL_UNIT_TO_METRES, offset)
    for mesh in meshes:
        if not 0 <= mesh.bone_index < len(model.bones):
            mesh.bone_index = 0
    _verify_indices(model)
    _bind_textures(model, textures)

    scaled_min = [(raw_min[0] - centre_x) * MODEL_UNIT_TO_METRES,
                  0.0,
                  (raw_min[2] - centre_z) * MODEL_UNIT_TO_METRES]
    scaled_max = [(raw_max[0] - centre_x) * MODEL_UNIT_TO_METRES,
                  (raw_max[1] - raw_min[1]) * MODEL_UNIT_TO_METRES,
                  (raw_max[2] - centre_z) * MODEL_UNIT_TO_METRES]
    model.parse_diagnostics = {
        "container": "pk3" if kind == "character" else "pk2",
        "asset_kind": kind,
        "file_bytes": len(data),
        "container_bytes": len(container),
        "reference_stats": geometry.get("stats", {}),
        "vertex_color_range": [low, high],
        "vertex_color_floor": VERTEX_COLOR_FLOOR,
        "unit_to_metres": MODEL_UNIT_TO_METRES,
        "bounds_min": scaled_min,
        "bounds_max": scaled_max,
        "mesh_count": len(meshes),
        "triangle_count": sum(len(mesh.indices) for mesh in meshes),
        "material_count": len(materials),
        "bone_count": len(model.bones),
        "bone_matrix_modes": sorted({b.get("matrix_semantics", "legacy_world")
                                     for b in bone_defs}),
        "bind_validation_max_error": max(
            (b.get("bind_validation_error", 0.0) for b in bone_defs), default=0.0),
    }
    return model


class FF2WTextureSet(list):
    """Textures of one PPDB, as an ordered list of ``PIL.Image`` plus its map.

    It *is* a list, so ``for index, image in enumerate(textures)`` and
    ``textures.index(image)`` -- the shape ``server.py`` expects -- work
    unchanged, while the extra attributes answer the question a bare list
    cannot: which slot belongs to which PPDB texture, and to which material.

    Slots are appended in ascending PPDB texture index and then the alpha-mask
    composites, so the order is stable across loads of the same file.
    """

    def __init__(self):
        super().__init__()
        self.slots: list = []            # parallel to self: int tex id or "t_alpha_a"
        self.tex_ids: list[int] = []     # parallel to self: the base tex id
        self.by_tex_id: dict[int, int] = {}
        self.alpha_by_pair: dict[tuple[int, int], int] = {}
        self.material_slots: dict[int, int] = {}
        self.material_alpha_slots: dict[int, int] = {}
        self.unresolved: set[int] = set()  # image indices with no faithful decode
        self.widths: list[int] = []
        self.heights: list[int] = []

    def _add(self, image: Image.Image, slot, tex_id: int, width: int,
             height: int, resolved: bool = True) -> int:
        self.append(image)
        index = len(self) - 1
        self.slots.append(slot)
        self.tex_ids.append(tex_id)
        self.widths.append(width)
        self.heights.append(height)
        if not resolved:
            self.unresolved.add(index)
        return index

    def index_for_tex(self, tex_id: int) -> int:
        """Image index of a plain PPDB texture, or -1."""
        return self.by_tex_id.get(tex_id, -1) if isinstance(tex_id, int) else -1

    def index_for_alpha(self, tex_id: int, alpha_tex_id: int) -> int:
        """Image index of the ``tex_id`` + ``alpha_tex_id`` composite, or -1."""
        if not isinstance(tex_id, int) or not isinstance(alpha_tex_id, int):
            return -1
        return self.alpha_by_pair.get((tex_id, alpha_tex_id), -1)

    def index_for_material(self, material_id: int, masked: bool = False) -> int:
        """Image index a material samples, by its id in the container."""
        table = self.material_alpha_slots if masked else self.material_slots
        return table.get(material_id, -1)

    def has_cutout(self, index: int) -> bool:
        """True when the image at `index` has genuinely transparent texels.

        Ported from the reference viewer's ``_note_texture_alpha``. CMPR (DXT1)
        always yields an alpha channel, so only real transparency counts; this is
        what keeps hair, lace and foliage crisp without alpha-testing every
        surface in the model.
        """
        if not 0 <= index < len(self):
            return False
        image = self[index]
        if image.mode != "RGBA":
            return False
        alpha = image.getchannel("A")
        low, _high = alpha.getextrema()
        return low < 128


def parse_ff2w_textures(path_or_bytes, model=None) -> FF2WTextureSet:
    """Decode a Fatal Frame 2 Wii PPDB into an ordered set of images.

    `path_or_bytes` is the PPDB path or bytes. `model`, when given, is used
    only to fill in the material -> image mapping from the ``tex_id`` /
    ``alpha_tex_id`` attributes ``parse_ff2w_model`` attaches to each
    ``SGDMaterial``; without it that part of the mapping is simply empty and the
    list still works.

    Alpha-mask pairs declared by the materials are composited exactly as the
    reference viewer does it: the diffuse image is copied and the mask's red
    channel is moved into its alpha, so a cutout texture can be uploaded as a
    single RGBA image instead of needing a second sampler. The mask is resampled
    bilinearly when its size differs from the base image's.
    """
    data, _default_name = _read_data(path_or_bytes)
    decoded = parse_ppdb(data)

    result = FF2WTextureSet()
    rgba_by_tex: dict[int, bytes] = {}
    images_by_tex: dict[int, Image.Image] = {}
    for entry in decoded:
        rgba = entry.get("rgba_data")
        index = entry["index"]
        if not rgba:
            images_by_tex[index] = _placeholder_image(entry["width"], entry["height"])
            result._add(images_by_tex[index], index, index,
                        entry["width"], entry["height"], resolved=False)
            continue
        rgba_by_tex[index] = rgba
        images_by_tex[index] = Image.frombytes("RGBA", (entry["width"], entry["height"]), rgba)
        result._add(images_by_tex[index], index, index,
                    entry["width"], entry["height"],
                    resolved=entry.get("resolved", True))
        result.by_tex_id[index] = len(result) - 1

    # Only ETAM-declared masks are meaningful. Slot 9 is not implicitly an
    # alpha mask for slot 0: those are ordinary diffuse textures in ch000_doa.
    pairs: set[tuple[int, int]] = set()
    material_slots: dict[int, tuple[int, int]] = {}
    for material in getattr(model, "materials", []) if model is not None else []:
        tex_id = getattr(material, "tex_id", -1)
        alpha_tex_id = getattr(material, "alpha_tex_id", -1)
        if not isinstance(tex_id, int) or not isinstance(alpha_tex_id, int):
            continue
        # A material without a mask still needs its diffuse slot recorded, so
        # the two maps are tracked separately and only the pair needs both ids.
        material_slots[material.index] = (tex_id, alpha_tex_id)
        if tex_id >= 0 and alpha_tex_id >= 0 \
                and tex_id in rgba_by_tex and alpha_tex_id in rgba_by_tex:
            pairs.add((tex_id, alpha_tex_id))

    for tex_id, alpha_tex_id in sorted(pairs):
        base_image = images_by_tex[tex_id]
        mask_image = images_by_tex[alpha_tex_id]
        composited = composite_alpha_variant(
            rgba_by_tex[tex_id], base_image.width, base_image.height,
            rgba_by_tex[alpha_tex_id], mask_image.width, mask_image.height,
        )
        image = Image.frombytes("RGBA", (base_image.width, base_image.height),
                                composited)
        slot = f"{tex_id}_alpha_{alpha_tex_id}"
        result.alpha_by_pair[(tex_id, alpha_tex_id)] = result._add(
            image, slot, tex_id, base_image.width, base_image.height
        )

    for material_index, (tex_id, alpha_tex_id) in material_slots.items():
        result.material_slots[material_index] = result.index_for_tex(tex_id)
        result.material_alpha_slots[material_index] = result.index_for_alpha(
            tex_id, alpha_tex_id
        )
    return result


def _placeholder_image(width: int, height: int) -> Image.Image:
    """A flat mid-grey image for a slot that could not be decoded.

    Keeps the slot's real dimensions so the viewer's texture code still has a
    sane size to work with, while making an unresolved texture obvious on
    screen instead of silently black.
    """
    width = width if width > 0 else 1
    height = height if height > 0 else 1
    return Image.new("RGBA", (width, height), (160, 160, 160, 255))
