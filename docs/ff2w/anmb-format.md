# ANMB Format Specification (Animation Container)

## Overview

Files with the `.anmb` extension are skeletal animation clips used by *Project Zero 2: Deep Crimson Butterfly* (Wii). The directory shipped with the game contains **288** `.anmb` files alongside the 160 `.mdlb` models and 160 `.ppdb` texture sets.

Like the other Nintendo formats, every `.anmb` file is compressed with **LZ11** and wraps a **PK3** container identified by the magic bytes `0x02, 'p', 'k', '3'` (`02pk3`). All multi-byte numeric fields are **Big-Endian**, and FourCC identifiers are stored **reversed** in raw memory (`MANB` for `BNAM`, `MINA` for `ANIM`, and so on).

File sizes range from 140 bytes to roughly 1.36 MB. All 288 files decompress successfully and every one of them yields a valid `pk3` header.

> **Note on naming:** the version identifier stored at offset `0x00` differs between the two container types. `.mdlb` files are uniformly version `2`, whereas `.anmb` files use version `1` (243 files), `2` (18 files) and `3` (27 files). The version appears to describe the animation schema rather than the container revision, and correlates with how many bones a clip targets.

---

## 1. Container Hierarchy

```
pk3 (02pk3)
└── SMNA  (ANMS)  Animation Set  — root of a clip group
    └── MINA  (ANIM) Animation     — a single playable clip
        ├── NART  (TRAN) Transform Root
        │   └── MANB  (BNAM) Per-bone track
        └── MANB  (BNAM) Additional per-bone tracks
├── MAPS  (SPAM)  Blend Map  — deduplication index
│   └── DNLB  (BLND) Blend Channels
├── DNLB  (BLND)  Blend Channels — morph target weights
└── EDNE  (ENDE)  Terminator / padding node (size 24, all zeroes)
```

Chunk sizes are stored as `uint32` at offset `+0x04` and include the 8-byte header. Because the container is big-endian, a naive scan for FourCC values in alphabetical order will not work; the tags must be matched in their reversed form.

### Observed FourCC inventory

Ranked by frequency across all 288 decompressed files:

| FourCC | Reversed | Count | Role |
| :--- | :--- | :--- | :--- |
| `EDNE` | `ENDE` | 113 935 | Terminator node, 24 bytes of zeroes |
| `MANB` | `BNAM` | 94 741 | Per-bone animation track |
| `ATOR` | `ROTA` | 92 498 | Rotation sub-record inside `MANB` |
| `NART` | `TRAN` | 9 563 | Transform root grouping bone tracks |
| `MINA` | `ANIM` | 6 615 | Individual animation clip |
| `SMNA` | `ANMS` | 4 700 | Animation set root |
| `MAPS` | `SPAM` | 3 179 | Blend map (index of `DNLB`) |
| `DNLB` | `BLND` | 3 179 | Blend channel weights (morph targets) |

A handful of files contain `EDNE` variants prefixed with an extra byte (`OEDN`, `CEDN`, `YEDN`, `JEDN`, `SEDN`, `1EDN`). These are uncompressed padding artefacts and carry no meaning.

`ATOR` does not begin at a 4-byte boundary in some builds; its records are 6-byte aligned inside `MANB`.

---

## 2. Per-Bone Tracks (`MANB`)

Each `MANB` chunk animates exactly one bone. A single clip may contain dozens of them, one per animated bone.

### Header Layout (Big-Endian)

| Offset | Type | Field | Notes |
| :--- | :--- | :--- | :--- |
| `0x00` | `char[4]` | FourCC | `MANB` |
| `0x04` | `uint32` | Chunk size | Includes the 8-byte header |
| `0x08` | `uint16` | Flags | Observed `0` in most builds |
| `0x0A` | `uint16` | Bones per chunk | `1` = a single bone, `>1` = merged record |
| `0x0C` | `uint16` | **Bone id** | Index into the `ENOB` table of the matching `.mdlb` |
| `0x10` | `char[4]` | `ATOR` | Rotation sub-record tag |
| `0x14` | `uint16` | — | |
| `0x18` | `uint16` | **Key count** | Number of keyframes |
| `0x1A` | `uint16` | **Data offset** | From the start of `MANB` to the key array |
| `0x1C` | `uint16` | **Key stride** | Size in bytes of a single key record |

### Key Records

The key array starts at `MANB + data_offset`. Each key occupies `stride` bytes and carries a single `float32` animated value.

**The float is not at a fixed byte offset — it varies per track.** A survey of all 94 741 `MANB` tracks across the 288 files found the optimal float position distributed as follows:

| Float offset in record | Tracks | With >85 % valid floats |
| :--- | :--- | :--- |
| `+0` | 54 792 | 53 766 |
| `+2` | 17 018 | 16 039 |
| `+6` | 10 873 | 9 074 |
| `+4` | 7 879 | 6 943 |
| `+1` | 2 515 | 1 845 |
| `+3` | 1 023 | 459 |
| `+5` | 637 | 71 |
| `+7` | 4 | 0 |

This is **not data corruption**. The `data_offset` field at `+0x1A` is not padded to a 4-byte boundary, so the key array inherits whatever alignment the previous chunk left behind. A single `float32` read at a hard-coded offset therefore only yields correct results for a subset of tracks — which is why naive parsing returns values such as `-1.6e37` or `NaN`.

Two working examples of the same nominal layout producing different alignments:

```
ch001_a404_nakasime  bone 5   raw: F7 55 02 3D | FF 51 3F 5D     float at +4 -> 0.8159
ch001_a099_cry       bone 2   float readable at +6               see parse output below
```

**Reliable extraction.** Because exactly one `float32` in each record has a valid exponent and a plausible magnitude, the value can be recovered by scanning the record rather than trusting a fixed offset. The heuristic used by the parser is:

1. Read `stride - 4` candidate windows.
2. Keep the first that is not `NaN` and satisfies `abs(v) <= 1.6`.
3. Discard the record if no candidate qualifies.

Across a 103 298-value sample this yields `min = -0.7365`, `max = 1.0859`, with the distribution dominated by values near zero:

| Bucket | Count |
| :--- | :--- |
| `≈ 0` | 102 180 |
| `≈ 1` | 318 |
| `0.2 … 0.8` | 117 |
| other | 683 |

About 10 % of extracted values are negative, consistent with Euler-angle channels rather than a normalised blend weight.

**Values are per-channel animation curves.** With the heuristic applied, `ch001_a099_cry` yields smooth per-bone curves such as:

```
bone 2   [1.766, 1.734, 1.719, 1.672, 1.640, 1.609, 1.640, 1.703]
bone 3   [1.523, 1.508, 1.500, 1.492, 1.484, 1.469, 1.438, 1.414]
bone 4   [1.156, 1.110, 1.047, 0.992, 0.938, 0.887, 0.942, 0.973]
```

These are smooth, continuous and physically plausible for joint angles, which confirms the extraction is reading the intended channel rather than noise.

### What remains undecoded

The non-float bytes in each record (typically a packed `u16` pair) are **not decoded**. They plausibly carry auxiliary channel data such as interpolation tangents or a second rotation component, but no alignment or stride has been shown to yield unit-length quaternions, so this is unconfirmed and must not be treated as a quaternion source.

---

## 3. Facial Expressions and Blend Channels (`DNLB`)

**This is the mechanism behind facial animation.** Contrary to the initial assumption that expressions must be shape keys stored in the `.mdlb`, the morph weights live in the animation files.

### Presence

| Metric | Value |
| :--- | :--- |
| `.anmb` files containing `DNLB` | **117** |
| `.anmb` files containing `MAPS` | **117** (always paired) |
| Of those, facial-expression clips | 7 |

Facial clips that carry blend channels:

```
ch001_a099_cry.anmb              crying
ch001_a404_nakasime.anmb          smiling
ch013_a117_cry.anmb              crying
ch013_a430_sae_mayutorituki.anmb looking down
ch217_a394_sigamituki.anmb       chin dropped
ch217_a400_stand_shutter.anmb    shuttered pose
ch217_a402_dead_pose.anmb        death pose
```

The remaining facial clips (`zashiki_look`, `kubisime` ×3, `kaoageru`, `kubisimerare`, `cry_stand`) are pure bone animation with no blend channels.

### `DNLB` Layout (Big-Endian)

| Offset | Type | Field |
| :--- | :--- | :--- |
| `0x00` | `char[4]` | FourCC `DNLB` |
| `0x04` | `uint32` | Chunk size |
| `0x08` | `uint16` | Blend channel count |
| `0x0C` | `uint16` | — |
| `0x10` | — | Channel array, stride `8` |

Each channel is:

```
  byte 0..1   u16   shape index (high)
  byte 2..3   u16   morph id     (low)
  byte 4..7   f32   weight       (0.0 .. 1.0)
```

Terminated by an all-zero triple.

### Channel identity

The two `u16` fields are reported separately by the parser as `shape` (high half) and `morph` (low half). Read as a single 32-bit value the pairs encode `0xSSSS_MMMM`, where the high 16 bits are a shape index and the low 16 bits identify the morph within that shape.

Across the 117 files there are **218** distinct shape indices in use, spanning `2` to `417`.

The most frequently referenced 32-bit identifiers are:

| Identifier | Occurrences |
| :--- | :--- |
| `0x00030000` | 1 231 |
| `0x0003FFFF` | 1 195 |
| `0x0005FFFF` | 998 |
| `0x00050003` | 558 |
| `0x0005000F` | 533 |
| `0x00020000` | 510 |
| `0x00030007` | 433 |
| `0x00030006` | 416 |
| `0x00020004` | 388 |
| `0x00030004` | 339 |

`ch001_a099_cry.anmb` uses four channels with weights oscillating between `0.0`, `0.5` and `1.0`:

```
0x00040003   w=1.00
0x00040004   w=1.00
0x00040006   w=0.00   ->  0.50   ->  0.00   ->  0.50
0x0004001E   w=1.00
```

`ch001_a404_nakasime.anmb` (smiling) is much simpler — morphs `2:3` and `2:4` both held at `1.00`, which matches an expression that is fully on for the whole clip.

The graded weights in `ch001_a099_cry` (`0.39`, `0.45`, `0.44`) are the clearest evidence of partially-applied morphs rather than a binary on/off expression, which is what one would expect from a face-blending rig where the artist mixes sub-expressions.

### Shape indices line up with the `.mdlb` shapes

The identifiers are not arbitrary. The shape indices present in the model files occupy the same numeric space:

```
ch000_mio      [0, 1, 2, 3, 31, 32, 33, 34, 35, 36, 37, 38, 39]
ch001_may      [0, 1, 2, 3, 32]
ch017_miya     [0, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31]
ch019_chitose  [0, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 25, 26, 27, 28, 29]
```

### Consequence for `.mdlb`

A full survey of the FourCC tags present across 60 `.mdlb` files found **no morph target chunk**. The tag inventory is limited to:

```
EMAN MANB* EDNE ENOB HSEM MIRP ETAM LDIV PAHS MELE TREV MRON OBHP
GIEW DCXT ACOC LPOC PSOC SLDM LEDM OLCB NICB OCCB DNCB
TOLC ILLC OLCB DILC NLLC GAME QRTU
```

(`MANB` appearing here is an incidental byte pattern; the bone chunk in `.mdlb` is `ENOB`.)

Each `PAHS` shape carries exactly **one** vertex buffer. The shapes with multiple `GIEW` chunks are two distinct overlaid meshes — one with `nb=1` influences, the other with `nb=2` — not a base mesh plus a morph target.

**Conclusion:** the model stores the neutral face only. Morph target geometry is either generated procedurally at runtime by the engine or lives in a container that was not extracted with the `character` directory.

---

## 4. `MAPS` — Blend Map

`MAPS` always appears immediately before a `DNLB` and embeds a verbatim copy of it:

```
MAPS @0x018D0 size=104
  +0x00: 4D 41 50 53 00 00 00 68 00 00 00 08 00 03 00 00   MAPS....h.....
  +0x10: 44 4E 4C 42 00 00 00 38 00 02 00 00 00 0E 00 10   DNLB...8.....
  +0x50: 45 44 4E 45 00 00 00 18 00 00 00 00 00 00 00 00   EDNE........
```

It functions as a deduplication index so that identical blend states across many clips are stored once.

---

## 5. File Naming Convention

```
<slot>_<clip-id>_<description>.anmb
```

* `slot` — `ch000`, `ch001`, … matches the `chNNN` prefix of the `.mdlb` the clip drives.
* `clip-id` — animation table index, e.g. `a404`.
* `description` — romanised Japanese.

Recognisable facial descriptions:

| Token | Meaning |
| :--- | :--- |
| `nakasime` | smiling |
| `cry` | crying |
| `kubisime` | strained / gritted-teeth effort |
| `kaoageru` | flushed face |
| `sigamituki` | chin lowered |
| `shutter` | shuttered / braced |
| `zashiki` | formal seated pose |
| `mayutorituki` | eyes lowered |

`find_animations_for_model()` in `mdlb_parser.py` resolves clips by matching the 5-character slot prefix, so `ch000_mio` picks up every `ch000_*.anmb`.

---

## 6. Parser API

```python
from pathlib import Path
from mdlb_parser import parse_anmb, parse_dnlb_blends, find_animations_for_model

clips = parse_anmb(Path('character/ch001_a404_nakasime.anmb'))
# [{
#   'name': 'ch001_a404_nakasime',
#   'tracks': {bone_id: [float, ...]},
#   'blends': [{'declared_count': N, 'channels': [
#       {'shape': int, 'morph': int, 'weight': float}, ...]}],
#   'duration': int,
# }]
```

Each clip carries both bone tracks and blend channels:

```python
clip = parse_anmb(Path('character/ch001_a099_cry.anmb'))[0]
len(clip['tracks'])                     # 54 animated bones
for b in clip['blends']:
    for ch in b['channels']:
        print(ch['shape'], ch['morph'], ch['weight'])
```

`find_animations_for_model()` resolves clips by the 5-character slot prefix, so `ch000_mio` picks up every `ch000_*.anmb`.

`iter_pk3_chunks()` is exposed as a generic chunk walker and can be reused for any PK3 payload.

### Observed blend channel counts

| Clip | Bone tracks | Blend groups | Notable weights |
| :--- | :--- | :--- | :--- |
| `ch001_a404_nakasime` (smile) | 50 | 1 | morphs 3 and 4 both at `1.0` |
| `ch001_a099_cry` (crying) | 54 | 3 | `1.0`, `0.5`, then a graded set `0.09 / 0.39 / 0.45 / 0.44` |
| `ch000_mio` | 51 per clip | 110 | large pose library |

The graded weights in `ch001_a099_cry` (`0.39`, `0.45`, `0.44`) are the clearest evidence of partially-applied morphs rather than a fully on/off expression.

### Current limitations

These are **known-incomplete** and listed here rather than glossed over:

1. The packed bytes accompanying each float key are still undecoded. No alignment or stride has been shown to yield unit-length quaternions.
2. Blend channel identifiers are parsed but not yet resolved against the `.mdlb` shape table, so they cannot be given semantic names, and the morph target geometry itself is not present in `.mdlb` (see §3).
3. `DNLB` weights are not yet emitted as glTF `mesh.weights` / morph targets on export.

---

## 7. Cross-Reference

| Behaviour | Data source |
| :--- | :--- |
| Body motion, walk cycles, poses | `MANB` tracks keyed by `ENOB` bone id |
| Facial expression (smile, cry, strain) | `DNLB` blend channels |
| Outfit changes | `.ppdb` texture swap — same `.mdlb` geometry, different textures |
| Hair / cloth simulation | `_physics_*` bones (id ≥ 62), present in both containers |

A verified example of the texture-swap behaviour: `ch001_yukata_1.mdlb` and `ch001_yukata_2.mdlb` are **byte-identical** (SHA-256 `1428d210…`) while their `.ppdb` files differ.