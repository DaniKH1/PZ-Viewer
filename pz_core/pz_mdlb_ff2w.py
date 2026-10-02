"""Fatal Frame 2 / Project Zero 2 (Wii) MDLB + PK2B adapter for the PZ viewer.

Fatal Frame 2 Wii ships its 3D data in a Nintendo "pk3"/"pk2" chunk container
with a completely different layout from every other game in this viewer
(PS2 SGD/TIM2).  A working reverse-engineered parser for it lives outside the
repository, next to the extracted game data, in::

    F:\\Games\\Retro\\Emuladores\\Dolphin\\Project Zero 2 Wii\\mdlb_parser.py

Vendoring, not importing
------------------------
The parsing layer below is **vendored** from that reference rather than
imported from its absolute path.  Reasons, in order of weight:

1. ``pz_core`` must stay self-contained.  Every other module in this package is
   importable with nothing but this repository on ``sys.path``; an absolute
   ``import`` of a file under ``F:\\Games`` would make the viewer fail to start
   on any machine where the user has not extracted the Wii game in exactly the
   same folder (or at all).
2. The reference lives outside version control and is not part of any release.
   Depending on it turns a working checkout into a build that breaks silently
   the day the folder is moved.
3. The reference is a 2400-line research tool: JSON reports, a CLI, ANMB
   animation parsing and a full glTF/GLB exporter.  Only ~600 lines of it are
   geometry and texture decoding, and that subset is pure stdlib (PIL is
   imported lazily inside the texture functions), so it ports without dragging
   in a single new dependency.
4. A vendored copy can be adapted instead of merely called.  Two things are
   fixed below and are marked as deviations:
   * the PPDB texture table is located through the header field at ``0x20``
     instead of the hardcoded ``0x40`` the reference assumes (see
     ``parse_ppdb``), which is what makes character PPDB files such as
     ``ch000_bontage.ppdb`` decode at all instead of returning zero textures;
   * the vertex-colour tint is baked into ``SGDMesh.colors`` here, following
     ``_compute_vcol_range`` in the reference *viewer*.

If the reference folder is missing this module still works, which is the point:
nothing here reads from outside the repository, and no import of the reference
path is attempted at runtime.

Format summary (all big-endian, reversed-tag chunk tree)
-------------------------------------------------------
* ``0x11`` LZ11 wrapper around a container whose magic at ``0x04`` is
  ``pk3`` (``.mdlb`` characters) or ``pk2`` (``.pk2b`` rooms and props).  Both
  share one geometry layout, only the container magic differs, so
  ``analyze``-style detection from the reference is reused verbatim.
* ``PAHS`` shape -> ``GIEW``/``WEIG`` preweighted multi-bone skinning records
  (s16 positions scaled by 1/1024) or ``TREV`` world-space vertices for
  unskinned pieces -> ``DCXT`` UVs (s16 pairs scaled by 1/1024) ->
  ``HSEM`` material groups -> ``LDIV`` GX display lists.
* ``ENOB`` bones, each with an affine 4x4 and an ``EMAN`` name.  Those matrices
  come in two transposed layouts (translation in elements 3/7/11 or in
  12/13/14); ``_affine_row_major`` resolves them per bone.
* ``ETAM`` materials: a texture index and an optional alpha-mask index.
* ``LOCV`` per-shape vertex colour palette inside the shape's ``MELE`` chunk.
* ``PPDB`` is the paired texture container: CMPR (DXT1 with 8x8 tiling) and a
  palettised CI8 format whose palette has not been located (see
  ``_ci8_palette``).

Units
-----
Positions use a native unit of roughly 5 cm, not metres.  The calibration is
the reference's (its module docstring): the median of the 160 character models
is 30.72 units tall and matches a ~1.54 m person at 0.05 m/unit, and the same
factor holds across helmets, dolls, props, giant demons and rooms.  Everything
is therefore scaled by ``MODEL_UNIT_TO_METRES`` and re-based so the model's
feet sit on ``y = 0`` and its footprint is centred on the origin, which is what
the viewer's cameras assume.

Public API
----------
``is_ff2w_asset(path)``
    True for a ``.mdlb`` / ``.pk2b`` file whose container magic is pk3/pk2.
``parse_ff2w_model(path_or_bytes, name=None, textures=None)``
    ``SGDModel`` ready for the viewer.
``parse_ff2w_textures(path_or_bytes, model=None)``
    ``FF2WTextureSet`` -- a ``list`` of ``PIL.Image`` in a stable order that
    also carries the material / ``tex_id`` -> image-index mapping.
"""

from __future__ import annotations

import math
import os
import struct
import copy
from pathlib import Path

import numpy as np
from PIL import Image

from .pz_sgd_ff3 import SGDBone, SGDMaterial, SGDMesh, SGDModel

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
# Everything below up to the "adapter" banner is vendored from
# mdlb_parser.py (see the module docstring).  Function names, control flow and
# comments are kept so the two files stay diffable against each other; only the
# marked deviations were changed.
# =====================================================================


def decompress_lz11(data: bytes) -> bytes:
    """Decompress Nintendo LZ11 compressed data (ported: mdlb_parser.decompress_lz11)."""
    if not data or data[0] != 0x11:
        return data
    size = data[1] | (data[2] << 8) | (data[3] << 16)
    src = 4
    if size == 0:
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
                    break
                for _ in range(length):
                    out.append(out[-disp])
                    if len(out) >= size:
                        break
    return bytes(out)


def parse_bones(pk3: bytes, search_start: int = 0x80) -> list[dict]:
    """Extract every ENOB bone (ported: mdlb_parser.parse_bones).

    The 16 floats at chunk offset 0x50 are an affine 4x4, but two transposed
    layouts exist across the game and neither can be assumed: 7742 of the 10567
    character bones store the translation in elements 3/7/11 (column-major, the
    layout this viewer and glTF already expect) and 350 store it in elements
    12/13/14 (row-major). No bone in any of the 347 MDLB/PK2B files is
    ambiguous, so ``_affine_row_major`` normalises them one at a time.
    """
    bones = []
    pos = search_start
    bone_id_counter = 0
    while pos < len(pk3) - 8:
        if pk3[pos:pos+4] == b"ENOB":
            size = struct.unpack_from(">I", pk3, pos+4)[0]
            bone_end = pos + size
            bone_id = struct.unpack_from(">H", pk3, pos+0x10)[0] if pos+0x12 <= len(pk3) else bone_id_counter
            parent_id = struct.unpack_from(">h", pk3, pos+0x12)[0] if pos+0x14 <= len(pk3) else -1

            if pos + 0x50 + 64 <= len(pk3):
                mtx = list(struct.unpack_from(">16f", pk3, pos + 0x50))
            else:
                mtx = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]

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
            })
            bone_id_counter += 1
            pos += size
        else:
            pos += 4
    return bones


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

        if prim in (0x98, 0x9A):  # triangle strip / fan
            for i in range(len(vtx) - 2):
                tri = (vtx[i + 1], vtx[i], vtx[i + 2]) if i % 2 == 0 else (vtx[i], vtx[i + 1], vtx[i + 2])
                tris.append(list(tri))
        elif prim == 0x90:  # triangle list
            for i in range(0, len(vtx) - 2, 3):
                tris.append([vtx[i], vtx[i + 2], vtx[i + 1]])
        elif prim == 0xA0:  # quad list
            for i in range(0, len(vtx) - 3, 4):
                tris.append([vtx[i], vtx[i + 1], vtx[i + 2]])
                tris.append([vtx[i], vtx[i + 2], vtx[i + 3]])

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

    An MDLB/PK2B material is only a name plus two texture slots: short 10 is
    the diffuse texture index and short 14 an optional alpha-mask index. There
    are no lighting constants to read, so ``SGDMaterial`` keeps its neutral
    defaults and only the texture binding is carried over.
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
    bone_matrices = [b["matrix"] for b in bones]
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
            shap_end = pos + shap_size

            # Check if shape has VIDL display list
            v_check = pk3.find(b"LDIV", pos, shap_end)
            if v_check != -1:
                shape_vtxs = []
                shape_weights = []

                # Check all WEIG (GIEW) chunks for multi-bone skinning.
                # NOTE: the blend formula is the reference's verified one and is
                # kept byte for byte, including its reading of the bone
                # translation from elements 3/7/11. That is the right slot for
                # the majority of the game (ch000_mio's rig puts Hips at
                # y=16.8 in a 30.8-unit character and its drawn vertices already
                # span y=0..30.8), but 350 bones across the character set use
                # the transposed layout where 3/7/11 are zero, so their
                # vertices come out in bone-local space. The affected assets are
                # prop-sized ones (ch000_bontage draws 12.6 units = 0.63 m
                # instead of a 30.8-unit body), and "fixing" the formula here
                # would move the other 97% of the game's geometry, so it is
                # left as the reference has it. ``_affine_row_major`` still
                # reads each bone's real position for the skeleton overlay.
                w_cur = pk3.find(b"GIEW", pos, shap_end)
                while w_cur != -1 and w_cur < shap_end:
                    w_size = struct.unpack_from(">I", pk3, w_cur+4)[0]
                    w_cnt = struct.unpack_from(">H", pk3, w_cur+0x0c)[0]
                    w_str = struct.unpack_from(">H", pk3, w_cur+0x0e)[0]
                    nb = pk3[w_cur+0x12]
                    w_data = w_cur + 0x20
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
                            m = bone_matrices[b_idx] if b_idx < len(bone_matrices) else None
                            if m:
                                # Verified Nintendo preweighted skinning formula:
                                wx = (m[0]*lx + m[1]*ly + m[2]*lz) + w * m[3]
                                wy = (m[4]*lx + m[5]*ly + m[6]*lz) + w * m[7]
                                wz = (m[8]*lx + m[9]*ly + m[10]*lz) + w * m[11]
                            else:
                                wx, wy, wz = lx, ly, lz
                            tot_x += wx
                            tot_y += wy
                            tot_z += wz
                            if k < 4:
                                v_joints[k] = b_idx
                                v_weights[k] = max(0.0, w)
                        w_sum = sum(v_weights)
                        if w_sum > 1e-6:
                            v_weights = [w / w_sum for w in v_weights]
                        else:
                            v_weights = [1.0, 0.0, 0.0, 0.0]
                        shape_vtxs.append((tot_x, tot_y, tot_z))
                        shape_weights.append((v_joints, v_weights))
                    w_cur = pk3.find(b"GIEW", w_cur + w_size, shap_end)

                # If no skinned vertices, check TREV (unskinned world vertices,
                # e.g. face mesh, cloth, props).
                if len(shape_vtxs) == 0:
                    t_cur = pk3.find(b"TREV", pos, shap_end)
                    if t_cur != -1:
                        t_size = struct.unpack_from(">I", pk3, t_cur+4)[0]
                        v_cnt = struct.unpack_from(">H", pk3, t_cur+10)[0]
                        pahs_hdr = struct.unpack_from(">16H", pk3, pos)
                        att_bone = pahs_hdr[10]
                        target_bone = att_bone if (0 <= att_bone < len(bones)) else head_bone_id

                        if v_cnt > 0:
                            data_bytes = t_size - 0x20
                            is_float = (data_bytes >= v_cnt * 10)
                            m = bone_matrices[target_bone] if target_bone < len(bone_matrices) else None
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
                                    lx, ly, lz = x / 1024.0, y / 1024.0, z / 1024.0
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
                    tx_size = struct.unpack_from(">I", pk3, tx_cur+4)[0]
                    uv_cnt = struct.unpack_from(">H", pk3, tx_cur+10)[0] if tx_cur + 12 <= len(pk3) else 0
                    if uv_cnt == 0 or tx_cur + 0x20 + uv_cnt * 4 > len(pk3):
                        uv_cnt = (tx_size - 0x20) // 4
                    for uvi in range(uv_cnt):
                        u_raw, v_raw = struct.unpack_from(">2h", pk3, tx_cur + 0x20 + uvi*4)
                        shape_uvs.append((u_raw / 1024.0, v_raw / 1024.0))

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
                    msize = struct.unpack_from(">I", pk3, m_cur+4)[0]
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
# PPDB texture pixel data is relative to 0x40 (verified on every file).
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
    pixels = [(0, 0, 0, 255)] * (width * height)
    tiles_x = (width + 7) // 8
    tiles_y = (height + 7) // 8
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


def decode_ci8_texture(pk2: bytes, data_start: int, width: int, height: int,
                       palette: list[tuple[int, int, int, int]]) -> bytes | None:
    """Decode an 8-bit palettised (CI8) texture through a palette.

    Ported: mdlb_parser.decode_ci8_texture.

    Returns None when the payload runs past the buffer or the palette is empty,
    so a caller can tell "not decoded" from "decoded to black".
    """
    needed = width * height
    if data_start + needed > len(pk2):
        return None
    if not palette:
        return None

    out = bytearray(needed * 4)
    npal = len(palette)
    for p in range(needed):
        idx = pk2[data_start + p]
        if idx < npal:
            r, g, b, a = palette[idx]
        else:
            r = g = b = a = 0
        o = p * 4
        out[o] = r
        out[o + 1] = g
        out[o + 2] = b
        out[o + 3] = a
    return bytes(out)


def _ci8_palette(_payload: bytes) -> list[tuple[int, int, int, int]] | None:
    """Palette for a format-9 (CI8) PPDB texture, or None when unlocatable.

    Format 9 is a genuine CI8 texture: the payload is exactly ``width*height``
    bytes, every one of the 256 index values is used, and the slots are packed
    back to back with no gaps (verified on ``ch000_bontage.ppdb``).  The CLUT
    itself has not been located: the header fields around it are zero and the
    file ends exactly at the last pixel, so the palette is either shared,
    derived or stored in a sibling resource.  The reference parser reached the
    same conclusion and reported the format instead of guessing a palette,
    which would have produced noise.

    So the caller gets a neutral greyscale identity ramp. It keeps the image
    dimensions and the spatial structure (so a material still binds and nothing
    renders as a black hole) while making the placeholder obvious rather than
    inventing a colour. Callers are told which slots are affected through
    ``FF2WTextureSet.unresolved``.
    """
    return [(i, i, i, 255) for i in range(256)]


def _read_ppdb_table(pk2: bytes) -> tuple[int, int]:
    """Locate the PPDB texture descriptor table, returning (base, count).

    Deviation from the reference, which hardcodes 0x40/0x4C. The header field
    at 0x20 holds the real table base: it is 0x40 for the PPDB files the
    reference was developed against (``ch000_mio``, every room) and 0x80 for
    others (``ch000_bontage``, where the hardcoded offset returns a zeroed
    descriptor and the file yields no textures at all). Reading the field makes
    both layouts work with the same code.
    """
    candidates = []
    field = struct.unpack_from(">I", pk2, PPDB_TABLE_BASE_OFFSET)[0]
    if 0x20 <= field <= len(pk2) - 8:
        candidates.append(field)
    # The reference's hardcoded layout is the historical default; keep it as a
    # fallback for files whose 0x20 field is not a table pointer.
    if PPDB_DATA_BASE not in candidates:
        candidates.append(PPDB_DATA_BASE)

    for base in candidates:
        count = struct.unpack_from(">I", pk2, base + 4)[0]
        if 0 < count <= 8192 and base + PPDB_TABLE_ENTRY_OFFSET + count * 8 <= len(pk2):
            return base, count
    return 0, 0


def parse_ppdb(data: bytes) -> list[dict]:
    """Parse a PPDB texture container (ported: mdlb_parser.parse_ppdb).

    Each descriptor is 36 bytes at ``table_base + rel``::

        u16 height  u16 width  u32 format  u32 data_rel (+ 16 bytes unused)

    and the pixel payload starts at ``0x40 + data_rel`` regardless of where the
    table itself sits.  CMPR payloads decode exactly; format 9 (CI8) decodes
    through ``_ci8_palette`` and is reported as unresolved there.
    """
    pk2 = decompress_lz11(data)
    if len(pk2) < 0x50:
        return []

    base, tex_count = _read_ppdb_table(pk2)
    textures = []
    if not tex_count:
        return textures

    for i in range(tex_count):
        entry_off = base + struct.unpack_from(">I", pk2, base + PPDB_TABLE_ENTRY_OFFSET + i * 8)[0]
        if entry_off + 16 > len(pk2):
            continue

        height = struct.unpack_from(">H", pk2, entry_off)[0]
        width = struct.unpack_from(">H", pk2, entry_off + 2)[0]
        fmt = struct.unpack_from(">I", pk2, entry_off + 4)[0]
        data_rel = struct.unpack_from(">I", pk2, entry_off + 8)[0]

        if width <= 0 or height <= 0 or width > 8192 or height > 8192:
            textures.append({
                "index": i, "width": max(width, 0), "height": max(height, 0),
                "format": f"fmt_{fmt}", "rgba_data": None, "resolved": False,
            })
            continue

        tex_data_start = PPDB_DATA_BASE + data_rel
        if fmt == PPDB_FMT_CMPR:
            block_size = (width * height) // 2
            if tex_data_start + block_size <= len(pk2):
                textures.append({
                    "index": i,
                    "width": width,
                    "height": height,
                    "format": "CMPR",
                    "rgba_data": decode_cmpr_texture(pk2[tex_data_start:], width, height),
                    "resolved": True,
                })
            else:
                textures.append({
                    "index": i, "width": width, "height": height,
                    "format": "CMPR", "rgba_data": None, "resolved": False,
                })
        elif fmt == PPDB_FMT_CI8:
            payload_end = tex_data_start + width * height
            payload = pk2[tex_data_start:payload_end] if payload_end <= len(pk2) else b""
            rgba = decode_ci8_texture(payload, 0, width, height, _ci8_palette(payload))
            textures.append({
                "index": i,
                "width": width,
                "height": height,
                "format": "CI8",
                "rgba_data": rgba,
                "resolved": False,
            })
        else:
            textures.append({
                "index": i,
                "width": width,
                "height": height,
                "format": f"fmt_{fmt}",
                "rgba_data": None,
                "resolved": False,
            })
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

    ``ETAM`` carries no lighting constants, so ambient/diffuse/specular/emission
    keep the neutral defaults of ``SGDMaterial`` and only the texture binding is
    carried over. ``tex_id`` is mirrored into ``tbp0`` as well: that is the slot
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
    """Normalise a stored ENOB matrix to a row-major 4x4 with translation at 12..14.

    The 16 floats are an affine 4x4 in one of two transposed layouts:

    * **column-major** (7742 of the 10567 character bones) -- translation in
      elements 3/7/11, homogeneous 1 in element 15, affine row in 12/13/14;
    * **row-major** (the other 350) -- the exact transpose, translation in
      12/13/14 and affine row in 3/7/11.

    Both appear in the same game and even the same asset family, and no bone in
    any of the 347 files puts a non-zero translation in both triplets, so
    picking the non-empty one is unambiguous. This matters because
    ``server.py`` reads a bone's position straight out of ``matrix[12:15]``:
    with the wrong layout every bone of a rig draws as one dot at the origin.

    The two layouts also differ in where the *rotation* sits, and the transpose
    is a real rotation (inverse orientation), so the 3x3 block is transposed
    along with the translation. The block is kept, not flattened: anything that
    later feeds a bone matrix through ``transform_pos`` -- a glTF armature
    export, for instance -- needs it.
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

    Matrices go through ``_affine_row_major`` and are then scaled and re-based
    into the same metres/feet-at-zero space as the vertices: the 3x4 block is
    multiplied by the unit scale and the translation column becomes
    ``S * (position - origin)``. Scaling the block as well (not just the
    translation) keeps the result a true world matrix.
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
        normalized = _affine_row_major(raw)

        matrix = [0.0] * 16
        for i in range(12):
            matrix[i] = normalized[i] * scale
        matrix[12] = normalized[12] * scale - ox
        matrix[13] = normalized[13] * scale - oy
        matrix[14] = normalized[14] * scale - oz
        matrix[15] = normalized[15]

        bone = SGDBone(position, parents[position])
        bone.matrix = matrix
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

    The container stores no normals at all -- there is no normal chunk and the
    reference viewer had to derive them the same way -- so they are accumulated
    from the triangles here. Area weighting is free with the unnormalised cross
    product and is what makes large triangles dominate the corner the way they
    should. A vertex touched only by degenerate triangles gets (0, 1, 0), the
    same fallback ``pz_sgd_ff3.normalize_mesh_normals`` uses.
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
    """Return a copy of ``model`` laid out the way the MDLB Viewer exports it.

    The mesh *data* follows ``mdlb_parser.export_to_gltf``, which is the
    authority for the layout: every triangle corner gets its own vertex (600 for
    200 triangles) instead of the shared, de-duplicated vertices the viewport
    wants, because the exporter writes flat face normals and a seam vertex needs
    both of its UVs written out; UVs go out raw, with no V inversion.

    The *unit*, though, has to be this viewer's rather than the reference's. The
    MDLB Viewer writes true metres, but every other game here exports in FF3
    viewer units -- an FF3 character comes out ~28 units tall. Writing FF2 Wii in
    metres put its characters at 0.68 units, a 41x size difference between two
    exports of the same kind of asset. So the FF2W_SCALE_* applied at load is
    kept, and only ``parse_ff2w_model``'s re-basing is undone: the model returns
    to the file's own origin (the reference does not re-base either) while
    staying in the unit the rest of the tool speaks.

    The input model is left untouched: the viewport keeps using it, and a
    second export must not scale on top of the first.
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
            # The 3x4 block must come out as a pure rotation, with no scale in
            # it at all. _build_bones multiplied it by MODEL_UNIT_TO_METRES,
            # which does not change units -- it just shrinks every bone to 5% of
            # its size -- so the exported rig carried a 0.05 scale (2.275 once
            # the viewer scale is included) that the mesh did not have. The bind
            # pose hides it because inverseBindMatrices cancels it exactly, but
            # an importer that honours joint scale applies it to the mesh on the
            # way in, which is why the model showed up slightly too big and only
            # snapped to size once a pose was entered. Undo the factor here: the
            # translations stay in mesh units, the rotation goes back to unit
            # scale, and bone and mesh finally agree.
            # Undo exactly the factor _build_bones applied to the rotation
            # block, and nothing else: the viewer scale is deliberately not
            # applied here because a scale in a bone's rotation block is not a
            # unit conversion, it shrinks the bone itself.
            block_factor = 1.0 / MODEL_UNIT_TO_METRES
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
    parse. ``.mdlb`` is a skinned, multi-bone character; ``.pk2b`` is a room or
    prop with a single identity bone and baked vertex colours.

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
    # Raw DCXT UVs already use the texture space the game samples with, so the
    # images must be uploaded without the vertical flip three.js would
    # otherwise apply. `uvs_are_flipped=False` -> flipY=True.
    model.uvs_are_flipped = False

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

    # Which (diffuse, mask) pairs exist? From the materials when a model was
    # supplied, otherwise from the fixed pairings the reference hardcodes for
    # the character textures.
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
    if not pairs:
        for tex_id, alpha_tex_id in ((0, 9), (7, 10)):
            if tex_id in rgba_by_tex and alpha_tex_id in rgba_by_tex:
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
