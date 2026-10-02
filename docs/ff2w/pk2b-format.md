> Nota de alcance, 2026-10-02: las mediciones de habitaciones de esta página
> proceden de la documentación anterior y no se han repetido: esos assets no
> están en el ZIP actual. Para ENOB y PPDB consulta las correcciones en
> [MDLB_FORMAT.md](mdlb-format.md) y [PPDB_FORMAT.md](ppdb-format.md).
> La paleta CI8 sí está localizada en los tres ejemplos reciclados. La
> compatibilidad nativa se ha probado con fixtures de layout, no con esas
> habitaciones reales.

# PK2B Format Specification (Item / Prop Model Container)

## Overview

Files with the `.pk2b` extension hold two kinds of 3D model: **props** (lanterns, cameras, notebooks, umbrellas, glasses, hats, masks, dolls) in the `item` directory — **35** files, each with a `.ppdb` — and **room geometry** in the `room` directory — **58** files, each with a `.ppdb` plus a `.ppdbMono`. Room geometry uses a different display list record layout and carries vertex colours; see section 0.

Like the other Nintendo formats, every `.pk2b` is compressed with **LZ11**. All 35 decompress successfully. The significant finding is that **the inner container uses the same mesh format as `.mdlb`** — only the container magic differs.

| Property | `.mdlb` | `.pk2b` |
| :--- | :--- | :--- |
| Compression | LZ11 (`0x11`) | LZ11 (`0x11`) |
| Container magic | `02pk3` | `02pk2` |
| `u32` at `0x00` (version) | `2` | `2` |
| `u32` at `0x08` (header size) | `32` | `32` |
| `u32` at `0x0C` (total size) | payload length | payload length |
| Mesh chunks | `PAHS` `MELE` `TREV` `MRON` `GIEW` `DCXT` `HSEM` `MIRP` `LDIV` | identical |

Because the mesh payload is byte-for-byte the same layout, the existing `parse_mdlb_geometry()` reads `.pk2b` files with **no changes required**. The only difference in the container is the identifier, which `analyze()` reports as `project_zero_wii_pk2b`.

### Header comparison

```
i005_key.pk2b            ch000_goth.mdlb
u32_00   2               u32_00   2
magic    b'pk2\x00'      magic    b'pk3\x00'
u32_08   32              u32_08   32
u32_0C   7456            u32_0C   368928
u32_40   0               u32_40   1
u32_44   0               u32_44   1886073088
```

---

## 0. Room Geometry (`.pk2b` in the `room` folder)

The `room` folder holds **58** room models (`rch*`, `rks*`, `rch*`, `ry*`, `rtb*`, `rry*`, `ros*`), each with a `.ppdb` and a `.ppdbMono` companion. They share the container and most chunk tags with the item models but differ in two important ways.

### 0.1 Display list records are 8 bytes, not 6

`LDIV` payloads are sequences of `opcode(1) + count(u16 BE) + count × record`. The record size differs:

| Model family | Record | Fields |
| :--- | :---: | :--- |
| `.mdlb` characters, item props | 6 | `pos, nrm, uv` |
| `.pk2b` rooms | 8 | `pos, nrm, colour, uv` |

Verified by the stride that consumes each payload exactly with every index in range. With the 6-byte layout a room list reads every UV as zero and then overruns the payload, which is why rooms previously decoded a small fraction of their geometry and looked deformed.

Field identification for the 8-byte record:

| Offset | Field | Evidence |
| :---: | :--- | :--- |
| 0 | vertex index | `rch02` shape 0 max = 159 with 160 vertices |
| 2 | normal index | max equals vertex count − 1 |
| 4 | vertex colour index | independent values, own range |
| 6 | UV index | max equals the shape's `DCXT` count − 1 in 14 of 18 sampled shapes |

Consequence for the whole `room` folder: **60970 → 1175319 triangles (×19.3)**, with no file regressing. Characters and items are untouched — an audit of 160 characters (4705 display lists) and 35 items (93 display lists) selects the 6-byte layout for every one of them.

Opcode handling now also covers `0xA0` quads, and a failed primitive no longer aborts the rest of the list.

### 0.2 Vertex colour palette lives in the `LOCV` chunk

Room geometry carries per-vertex tints. They are not in `TREV` (float32 positions, 12 bytes) nor in `MRON` (6 bytes of normal data plus 2 zero bytes per vertex). They live in a **`LOCV` chunk nested inside the shape's `MELE` chunk**, and its header states both the offset and the length of the table, so no guessing is needed:

| Offset | Size | Meaning |
| ---: | ---: | :--- |
| `0x00` | `u16` | version, `0x0500` on all 1949 chunks of the 58 room files |
| `0x02` | `u16` | `count`, number of RGBA8 entries |
| `0x04` | `20` | reserved, all zero |
| `0x18` | `count*4` | the RGBA8 palette |
| `0x18+count*4` | `pad` | zero padding out to the chunk size |

The chunk size always equals `24 + count*4 + pad`, `pad` being one of 0, 4, 8, 12, 16, 20, 24 or 28 bytes, and the padding is all zero on all 1949 chunks. Every shape has at most one `LOCV`, and exactly one shape out of 1370 across the room files indexes colours without having one.

Entry 0 and its neighbours, read straight from the header, for the tables that were previously located by hand:

| Model | Entry 0 | Following |
| :--- | :--- | :--- |
| `rks10` shape 1 | `4A4239` | `494038 656562 000000` |
| `rch02` shape 0 | `15110F` | `3C3128 3E3329 030303` |
| `rkh00` shape 0 | `52483A` | `584C3B 463725 322C23` |
| `ros02` shape 3 | `030303` | `040404 100D0C 251B18` |
| `ry08` shape 1 | `050505` | `040404 030505 050505` |
| `rch00` shape 15 | `191511` | `110F0C 0C0A09 030303` |

Note the last row: it is the table that the scan below used to get wrong. Reading `count` = 27 from the header gives 27 brown entries, none of them green.

**Why the byte scan had to go.** Before `LOCV` was decoded, the table was located as the maximal run of four-byte entries with an opaque alpha byte, and its size came from the highest colour index the shape's display lists reference. That combination failed on `rch00` shape 15. The scan started the table 17 entries past the real one, ran off the end of `LOCV`, and read the *following* `DCXT` chunk as colours. Its ASCII tag `DCXT` is not RGBA data, but it still parsed: entry 13 became `444358`, the chunk size field became `000001`, and the rest of the chunk's contents turned into `030A00`, `015006`, `00D106`, `00D205`, `FFF606`, `FF8105`, `FF3905`. Roughly a dozen vertices on one arch segment referenced those indices, which is why that segment glowed green while the rest of the cave looked correct. The misread entries were also self-consistent enough to defeat the filters built to catch them: the "rare entry far from the median" rule computed its median from the misread tail, where `(1, 21, 5)` is a perfectly stable median, so a genuinely green `015006` sat only 60 away from it and passed a threshold of 120. No filter on the output can repair a wrong offset; only the header gives the right one.

**Remaining fallback.** `_read_locv_palette` handles the normal case. When a shape has no `LOCV`, or the chunk fails its version/count/length check, the scan below still runs. Its documented behaviour, kept because it is the only path for shapes that store colours in some other chunk:

**Palette size.** Without a `LOCV` count, the size is taken from the highest colour index the shape's display lists reference, collected over *every* material group of the shape. Deriving it from the first group alone truncates the table: later groups then reference indices past its end and silently lose their colours. This one change moved the average room from 13.4% to 73.1% of triangles coloured.

**Locating the array.** It is the maximal run of four-byte entries whose alpha byte is `0xFF`, and it is normally *longer* than the index range that uses it:

| Model / shape | Entries stored | Entries referenced |
| :--- | ---: | ---: |
| `rks10` 1 | 14 | 14 |
| `rch02` 0 | 61 | 61 |
| `rkh00` 0 | 29 | 29 |
| `rkr00` 0 | 801 | 11 |
| `ros02` 3 | 4210 | 228 |
| `ry08` 1 | 7012 | 350 |

Per shape there is exactly one run long enough, and it is always preceded by zero padding, so the start of the run is the start of the palette. Two rules that look reasonable break it:

* requiring an **exact** length rejects every palette that stores spare entries;
* accepting **any interior offset** that satisfies the index range starts the palette in the middle of the table, which silently yields the wrong colours.

Verified first entries, against tables located by hand:

| Model | Entry 0 | Following |
| :--- | :--- | :--- |
| `rks10` shape 1 | `4A4239` | `494038 656562 000000` |
| `rch02` shape 0 | `15110F` | `3C3128 3E3329 030303` |
| `rkh00` shape 0 | `52483A` | `584C3B 463725 322C23` |
| `ros02` shape 3 | `030303` | `040404 100D0C 251B18` |
| `ry08` shape 1 | `050505` | `040404 030303 050505` |
| `rkr00` shape 0 | `111515` | `171B1B 272E2E …` (merged 811-entry table) |

The mapping is confirmed spatially: in `rks10`, 74% of coloured triangles have all three vertices equal or within 24/255 of each other, which is surface continuity rather than noise.

**Validation by coherence.** Shape alone is not enough: `rch00` proved that opaque runs can be false positives. Its shape 15 carries a 5-entry saturated run (`C0058E…`) that scores coherence 0.000 — the bytes are not an RGBA array at all — and shapes 21, 12 and 31 carry 1–2 entry runs that cannot explain hundreds of distinct indices. Every candidate run is therefore scored by coherence (fraction of covered triangles whose vertices match) times coverage (fraction of triangles fully inside the run). Hand-verified tables score 0.19–0.90 with full coverage; proven false runs score at most 0.06, so the bar (product ≥ 0.10 with coherence ≥ 0.10 and coverage ≥ 0.5) sits between the clusters. Anything below gets no tint: untinted beats a clamped bogus colour, which is what used to paint the floor band magenta and the arches flat purple.

**Merging fragmented tables.** Real palettes are often one table broken by a few translucent entries: `rch00` shape 4 — the main cave wall — stores a single 1210-entry table whose opaque fragments (255, 28, 55, 136, 90, 31, 207, 27) are split by single non-opaque entries. Runs separated by at most two entries are merged before scoring; without this the wall's fragments cover only 43% of its triangles and the whole wall loses its colours. With it the wall reaches 100% with 557 distinct colours.

**Shapes without colours.** Some shapes have no coherent opaque table: their third record field is a second UV set — rooms ship `Mono.ppdb` lightmaps — rather than a colour index. A full RGB-coherence scan is tried as fallback with a stricter bar (sn ≥ 0.40, coverage ≥ 0.5, search sized from the 99th percentile of indices, triangles subsampled adaptively to bound the scan to ~2M operations per shape). This recovers the mixed-alpha rock-detail tables of `rch00` (shapes 10, 14, 16, 19, 21: browns scoring 0.52–0.80). Tables whose alpha bytes are mostly intermediate values are rejected even when RGB-coherent: every real palette stores alpha as opaque-or-transparent per entry (0.70–1.00 of entries in {0x00, 0xFF}), while `rch00` shape 22 scores 0.00 and paints vivid green psychedelia over rock. The alpha byte itself is not applied as transparency — it shows no spatial correlation, and blending would erase solid arch geometry — so tables apply as opaque RGB tints.

**Exporting vertex colours.** `export_to_gltf` writes a `COLOR_0` attribute (VEC4 float, as glTF expects) so viewers that support it show the baked tint instead of flat texturing. glTF multiplies `COLOR_0` into `baseColorTexture`, which is exactly how the GL viewer applies it via `GL_MODULATE`, so the two agree.

The remap matches the viewer (`_compute_vcol_range` / `_fill_textured_list`): a single scale per file — never per group, which flattens rooms by stretching every surface to full brightness — onto `[floor, 1]`, with `floor` = 0.08 and the 99th percentile as the top so a few bright entries cannot sink the model. Alpha is written as 1.0 throughout because the palette's alpha byte is not opacity (see below). Characters and props, which carry no colour data in this format, correctly export pure white `COLOR_0`.

Verified on the emitted binaries: `COLOR_0` is `VEC4`, every value lies inside the buffer, and the tint is real for `rch00` (R 0.116–0.377) and exactly 1.0 for `ch000_mio` and `i005_key`.

**Vertex colour alpha is not opacity.** The palette's fourth byte is a real per-vertex field, but nothing in the files says it means visibility, and both polarities of a cutout behave badly. Measured:

| Room | alpha=00 vertices | median luminance alpha=00 | median luminance alpha=FF |
| :--- | ---: | ---: | ---: |
| `ry00` | 102 (0.07%) | **127** | 22 |
| `ry08` | 91 | **127** | 15 |
| `ry11` | 303 | 11 | 9 |
| `rch00` | 2221 (5.58%) | 17 | 18 |
| `rkh00` | 206 | 2 | 6 |

In `ry00` and `ry08` the `alpha=00` entries are the *brightest* vertices in the file; in `rkh00` they are the darkest. There is no consistent relationship with brightness, so the byte is not an emissive flag either. It is spatially coherent though — 99.7% of adjacent vertex pairs in `ry00` agree (92.1% in `rch00`) — which points at a smooth weight, most plausibly between baked ambient and dynamic lighting.

Applying it as a cutout was implemented and then removed again. Both polarities fail: cutting `alpha=00` keeps 88–100% of the triangles and so does almost nothing; cutting `alpha=FF` keeps 0–1% and deletes the model. There is also no radial structure to support an inner-versus-outer reading — `rch00` shapes 15 and 18 are 0% `alpha=FF` at both their innermost and outermost 10%, while shapes 12, 13, 14, 19 and 21 show no consistent direction (correlation radius→alpha ≈ −0.03). The viewer therefore ignores the byte entirely.

What the failed cutout *looked* like is worth recording, since it is easy to misread: only thin bands survived, tracing the arch and prop outlines. Because the alpha is interpolated across each triangle, an alpha test keeps only the fragments where it crosses the threshold, so every triangle interior was discarded and just its contour remained. Those contours are an artefact of the test, not ring geometry in the model.

Note that `rch00` shapes 10–19, which show a roughly 50/50 alpha split, do read their palette from `LOCV`, so that split is real data and not an artefact of the fallback scan.

**Transfer fill.** Vertices that still have no colour after all palette attempts (the arches, pool and floor slabs of `rch00` shapes 9, 13, 22, 31, 33, 34) copy the nearest decoded colour in 3D through a uniform grid. The ambient tint varies smoothly, so this lands far closer to the game look than the white those surfaces showed before — the bright bands circling the cave and the glowing pool rim. Transferred colours are flagged per group (`colors_transferred`) separately from palette-decoded ones. No entry is filtered out of a `LOCV` palette: the header gives the exact length, so every entry in it is a real colour. The earlier rule that dropped rarely used entries far from the median existed only to hide the misread tail described above, and it silently discarded legitimate entries whenever the table it was given was itself wrong.

The nearest-neighbour search is memoised on the **exact vertex position**. The display lists repeat positions heavily, so `ry00`'s 11806 uncoloured vertices are only 2344 distinct positions, cutting the searches to a fifth with a bit-identical answer.

**Coverage.** Across the 58 rooms, every triangle (1175297/1175297, 100%) carries a colour on all three vertices, whether decoded or transferred. Characters and item props have no colour data in this format and correctly report 0%.

**Viewer range.** The tint is remapped with a single scale per file (`_vcol_lo`/`_vcol_hi` measured at load, before the display lists are built), which preserves the game's relative lighting: walls stay dark next to a glowing portal instead of each surface being stretched to full brightness on its own, which is what made rooms look flatly lit before. The top of the range uses the 99th percentile rather than the maximum so a handful of bright garbage entries cannot sink the whole model; highlights above it still clamp to 1.0.

### 0.3 Units: the files are not in metres

Positions are stored in a native unit of roughly **5 cm**, not metres. Calibration from the data: the median of the 160 character models is 30.72 units tall, and matching that to a ~1.54 m person gives 0.05 m/unit. The factor is corroborated elsewhere in the set rather than resting on that one assumption:

| Model | span (units) | at 0.05 m |
| :--- | ---: | :--- |
| `ch000_mio` (person) | 30.8 | 1.54 m |
| `ch126_kubitsuri` (giant demon) | 89.5 | 4.5 m |
| `ch123_kusamaka` (giant demon) | 76.9 | 3.8 m |
| `i014_kabuto` (helmet) | 7.25 | 36 cm |
| `i106_ningyou_head03` (doll head) | 4.16 | 21 cm |
| `ch211_ghosthand` (prop) | 1.5 | 7.5 cm |
| `rch00` (cave) | 1549 | 77 m |
| `ry00` (village) | 4800 | 240 m |

The demons come out at 2–3× a person and the props at centimetres, which is what they should be.

**glTF export.** glTF defines its units as metres, so writing the raw numbers produced models about 20× oversized — a 30.8 m tall character in Blender. `MODEL_UNIT_TO_METRES = 0.05` in `mdlb_parser.py` is applied to the exported positions, and it is the single place to retune. Only positions need scaling: for a uniform S, `invert(S'W) · S'W · (S v) = S v` where S' is the bone matrix with its translation column scaled, so skinning is unaffected and neither the bone nodes nor the inverse bind matrices are touched.

**Viewer.** The viewer used to normalise every model to a 2.6-unit span, which made a 2.7-unit key and a 30.8-unit character identical on screen and destroyed relative size. It now draws at real scale. Three consequences had to be handled:

* Camera framing is rescaled by `real_span / 2.6` so each model keeps the framing it had before (`dist/span_real` is 0.900 for every non-room model, unchanged).
* The depth range follows the model instead of a fixed 0.05..100, which would have clipped the far side of a 240 m village.
* Characters and items aim at mid-height instead of the origin. The model is based at `min_y`, so the origin is at the feet and the previous framing showed only legs and shoes with the head off screen. This was pre-existing, not caused by the scale change: the reference screenshots look correct only because `shots.py` frames by bounding radius.

### 0.4 Load time

Room loads were dominated by three costs, all now addressed without changing any output:

| Change | Effect |
| :--- | :--- |
| `LOCV` read replaces the byte scan for the palette | `_scan_coherent_table` no longer runs on most shapes |
| `_palette_coherence` takes pre-extracted indices | `rkh00` 33.9s → 2.4s, verified bit-identical over 1200 randomised comparisons |
| Transfer results memoised on exact position | 5× fewer nearest-neighbour searches, identical answer |
| `analyze` passes its decompressed container to `parse_mdlb_geometry` | LZ11 ran twice per load; `ry00` expands to 1.8 MB |

Measured per model after the change: `ry00` 1.58s (was ~12s), `rkh00` 2.96s, `rch00` 0.28s, `ch000_mio` 0.10s. Room average 1.19 s/model, 58 rooms in 69s, 100% colour coverage and zero regressions across all 118 models.

### 0.5 Default camera for rooms

Room geometry is enclosed by a rock shell, so framing it from outside — as the viewer does for characters and props — shows an opaque lump and hides the only surfaces the vertex colours actually affect. The game is always seen from inside at roughly eye height, and that is the only view in which the baked shading reads correctly.

`frame_default_view()` therefore places the camera for `room/*.pk2b` at the middle of the room, at 6° pitch, at a distance derived from the **narrower** horizontal axis (clamped to 0.3–1.0) so it stays inside a long corridor instead of clipping a wall in a wide hall. Characters and props keep the outside framing.

### 0.6 Applying the colours in the viewer

Comparing `rch00` against an in-game screenshot confirms the match: with vertex colours the walls read grey rock with variation, the floor black and the torii dark red; with them disabled the whole cave flattens to uniform grey. The stone arches render as rock — the purple barrier glow visible in-game is a dynamic effect, not baked vertex data.

Vertex colours are stored at their **absolute in-game ambient brightness**. Measured medians are around 20/255 and the brightest vertex of a group reaches roughly 100/255. Multiplying a texture by them directly renders every room black, and dividing by the group peak still leaves 90% of vertices near 0.2.

The viewer remaps the file-wide channel range linearly onto `[vertex_color_floor, 1.0]`, measured once per model at load (`_compute_vcol_range`, before the display lists are built — building them first left every first load untinted white, with each reload showing the previous model's range). A single scale per file preserves the relative lighting between surfaces; the earlier per-group normalisation stretched every group to full brightness on its own and flattened rooms to fully lit. The floor defaults to **0.08**, which reproduces how the colours read in game: deep shadow falls almost to black while lit rock stays warm brown. Measured on `rch02`, the floor shifts the image noticeably:

| Floor | Warm pixels | Mean brightness |
| ---: | ---: | ---: |
| 0.00 | 52.2% | 37.9 |
| 0.08 | 52.2% | 37.5 |
| 0.25 | 37.7% | 36.6 |
| 0.50 | 19.5% | 38.9 |
| 1.00 | 4.5% | 54.0 (no tint) |

The range is measured over the colours the group actually uses rather than over the whole shape palette, because a group referencing a narrow slice of the palette would otherwise land on a wider and darker scale than its neighbours.

Some rooms are very dark by nature (`rkh00` is close to black at the default floor), so the top bar carries a **🎨 VtxCol** button and **V** to toggle, plus **[** and **]** to raise or lower the floor in steps of 0.08; the button label shows both the percentage of triangles carrying a colour and the current floor, which makes it obvious when a model simply has none. Textures are mipmapped where the size allows it, without which the minified rock and floor alias into per-pixel speckle.

**This is a visualisation remap, not the game's lighting.** Rooms also rely on dynamic lights and the `Mono.ppdb` lightmap, neither of which is reproduced.

### 0.7 Distinguishing a model bug from an overlay

A green patch on a room surface was chased through the palette twice before the cause turned out to be neither. Two checks settle this class of question in one pass:

* **Render with textures off** (clay mode, `T`). Clay shows only vertex colours, so a patch that survives it comes from colour data; one that disappears is coming from the texture or the texture binding.
* **Render with the gizmo off** (`A` for axes, `G` for grid). These are drawn in world space at the origin with depth testing on, so they can cross geometry.

Both were needed here. `draw_axes` drew the green Y axis with the same endpoints as the red X axis (`(0,0,0)→(0.8,0,0)`), so a green line lay along X and crossed the view wherever the camera pointed; it now goes to `(0,0.8,0)`. That was a real bug, but not the one reported: with the axes hidden the green patch was still there, and only the clay/texture comparison located it in the `LOCV` chunk.

### 0.8 What is still unknown for rooms

* The `Mono.ppdb` files are not loaded; they look like a lightmap or ambient pass.
* `PAML`, `SLDM` and `LEDM` remain undecoded.
* `rks02` has 11 of 76 display lists that decode at neither stride.
* One shape out of 1370 across the room files indexes colours but carries no `LOCV` chunk. Where its palette lives is still unknown; it currently gets colours only through the transfer fill.

---

## 1. Chunk Inventory

FourCC tags found across all 35 decompressed `.pk2b` item files, all stored **reversed** due to big-endian word writes:

| FourCC | Reversed | Count | Role |
| :--- | :--- | :--- | :--- |
| `EDNE` | `ENDE` | 332 | Terminator / padding node |
| `EMAN` | `NAME` | 226 | Name strings (bones, materials) |
| `ENOB` | `BONE` | 160 | Bone, 184 bytes each |
| `HSEM` | `MESH` | 93 | Material subgroup |
| `MIRP` | `PRIM` | 93 | Primitive list |
| `LDIV` | `VIDL` | 93 | GX display list |
| `PAHS` | `SHAP` | 67 | Shape |
| `MELE` | `ELEM` | 67 | Element |
| `TREV` | `VERT` | 67 | Unskinned vertices |
| `MRON` | `NORM` | 67 | Normals |
| `DCXT` | `TXCD` | 67 | UV coordinates |
| `ETAM` | `MATE` | 66 | Material descriptor |
| `GIEW` | `WEIG` | 47 | Skinned vertices & weights |
| `OBHP` | `PHBO` | 43 | Physics body |
| `SLDM` | `MDLS` | 35 | Model list |
| `LEDM` | `MDEL` | 35 | Model element list |
| `PAML` | `LMAP` | 17 | Parameter map |

The tags unique to items are `PAML`, `SLDM` and `LEDM`, each appearing once per file or close to it, suggesting a small per-model parameter table. Their internals have not been decoded.

`GAME` appears in 14 files and is not an item-specific structure; it is also present in `.mdlb` character files.

---

## 2. Content

All 35 files parse successfully with the shared geometry pipeline:

| File | Shapes | Verts | Tris | Bones |
| :--- | ---: | ---: | ---: | ---: |
| `i000_play_camera` | 1 | 1233 | 0 | 3 |
| `i001_play_light` | 1 | 102 | 200 | 2 |
| `i002_note` | 2 | 16 | 24 | 4 |
| `i003_gyakusatu` | 1 | 9 | 8 | 2 |
| `i004_play_camera_3` | 1 | 796 | 1276 | 3 |
| `i005_key` | 1 | 114 | 200 | 2 |
| `i006_gantai` | 1 | 315 | 530 | 2 |
| `i006_megane` | 1 | 182 | 276 | 2 |
| `i007_meganeA` | 3 | 487 | 890 | 4 |
| `i008_meganeB` | 2 | 473 | 910 | 3 |
| `i008_sunvisor` | 1 | 472 | 624 | 2 |
| `i009_meganeC` | 3 | 571 | 1046 | 4 |
| `i009_mugiwara` | 1 | 560 | 830 | 2 |
| `i010_tongari` | 1 | 574 | 768 | 2 |
| `i011_kituneA` | 1 | 251 | 430 | 2 |
| `i011_pumpkin` | 1 | 572 | 914 | 2 |
| `i012_kituneA` | 1 | 528 | 860 | 2 |
| `i013_hanaA` | 1 | 442 | 663 | 2 |
| `i014_kabuto` | 1 | 2471 | 1524 | 2 |
| `i015_hatimaki` | 1 | 330 | 492 | 2 |
| `i015_meidoA` | 5 | 575 | 772 | 6 |
| `i016_meidoB` | 4 | 896 | 1340 | 5 |
| `i017_usamimi` | 1 | 323 | 642 | 2 |
| `i100_rousoku` | 1 | 308 | 604 | 3 |
| `i101_syokudai` | 1 | 426 | 836 | 3 |
| `i102_jyuwaki` | 1 | 204 | 398 | 2 |
| `i103_pocketbook` | 1 | 8 | 12 | 3 |
| `i104_ningyou_head01` | 9 | 2272 | 1368 | 32 |
| `i105_ningyou_head02` | 7 | 1961 | 916 | 23 |
| `i106_ningyou_head03` | 5 | 3639 | 4508 | 21 |

The three `ningyou_head` (doll head) files carry the most complex rigs, with 21 to 32 bones and several shapes — consistent with articulated heads that the player can move.

### Bone naming

Props name their root bone after the file (`i005_key` → bone `i005_key`), while many others use the generic `model_0`. The doll heads use descriptive names such as `model_top`, `head`, `eye`.

### Zero-triangle entries

`i000_play_camera` (1233 verts) and `i010_mbag` (1511 verts) report `tris = 0`. Their vertices decode, but no `LDIV` primitive list was recovered, so they render as geometry-less. The likely cause is a display-list encoding variant in these two files; it has not been investigated.

---

## 3. Textures

All 35 `.pk2b` files have a matching `.ppdb`.

### Format distribution

| Format | Dimensions | Count |
| :--- | :--- | :---: |
| `14` (CMPR) | 256×256 | 25 |
| `14` | 512×512 | 9 |
| `9` (unresolved) | 256×256 | 6 |
| `14` | 512×256 | 6 |
| `14` | 128×128 | 4 |
| `14` | 256×512 | 2 |
| `14` | 128×256 | 2 |
| `9` | 16×16 | 2 |
| `14` | 256×128 | 2 |
| `14` | 128×64 | 1 |

### Format 9 — unresolved

Eight item textures use `fmt = 9`, a palettised layout that does not appear anywhere in the character `.ppdb` files.

Established facts:

* The pixel payload at `0x40 + data_rel` is exactly `width * height` bytes. For `i014_kabuto` (256×256) that is 65536 bytes, and for `i006_megane` (16×16) exactly 256 bytes — both precisely fill the buffer to the end.
* All 256 index values are used in the 256×256 case, so the texture is not 4-bit packed in the straightforward way.
* `data_rel` resolves correctly with the same `0x40 + rel` formula used for format 14, which is confirmed independently by the CMPR textures in the same files decoding without error.

Rejected hypotheses:

| Attempt | Result |
| :--- | :--- |
| 4bpp packed indices, palette immediately before the descriptor | Colour noise, background nearly white |
| 8bpp indices, 256-entry RGB555 palette 512 bytes before the descriptor | Colour noise |
| RGB555 direct at 2 bytes per pixel | Top half decodes, bottom half blank — wrong stride |
| Exhaustive palette-offset scan across the whole header region | Best case still yields ~200 distinct colours in a sample; no offset produces a coherent image |

The payload is therefore palettised in a way whose palette is not stored in the header region that was searched — plausibly a separate palette block, an indexed container with its own table, or a palette located after the pixel data.

**Current behaviour:** `parse_ppdb()` reports format 9 as `fmt_9_palettised_unresolved` with `rgba_data = None`, so it renders as untextured rather than incorrectly textured. `decode_ci8_texture()` is retained in the source as a documented reference for continuing the work.

### Non-power-of-two dimensions

Item textures use `512×256`, `256×128`, `128×64` and `128×256`, which CMPR's 8×8 tiling handles correctly, but which will clamp when uploaded as OpenGL 1.x textures without mipmaps.

---

## 4. Integration

`SUPPORTED_EXTENSIONS` in `mdlb_viewer.py` now includes `.pk2b`, so items appear in the Asset Browser alongside characters. `find_ppdb()` searches the `item` folder as well as `character`, and the viewer labels these entries `Prop (PK2B)` with a box icon.

`export_to_gltf()` resolves a sibling `.ppdb` through the same helper, so items export with their textures using the existing pipeline.

Command line:

```
python mdlb_parser.py item\i014_kabuto.pk2b --gltf
```

---

## 5. Open Questions

1. **Format 9 palette location** — blocks textured rendering of 8 item textures.
2. **`PAML`, `SLDM`, `LEDM`** — likely per-model parameters, undecoded.
3. **Zero-triangle files** — `i000_play_camera` and `i010_mbag` decode vertices but no primitives.
4. **`ch000_goth.ppdb` uses a different header** — its `versionId` at `0x00` is `20` rather than `2`, and the texture count at `0x44` is `0`, so no textures are extracted from it. The same applies to at least one other character file. This is a separate container revision and currently means those characters render untextured.