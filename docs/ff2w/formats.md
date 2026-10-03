# Fatal Frame 2 Wii asset formats

This inventory complements the detailed
[MDLB](mdlb-format.md), [PK2B](pk2b-format.md), [PPDB](ppdb-format.md),
[ANMB](anmb-format.md) and [LZ11](lz11-compression.md) references. The source
set contains 1,280 files across nine suffixes. The game-wide census is in the
[asset atlas](../source-asset-formats.md).

## LZ11-compressed model, texture and animation families

### `.mdlb`

The 160 model files are LZ11 streams. After decompression their observed
outer/nested `pk3`/`pk1` chunk structure contains records such as `ENOB`,
`PAHS`, `TREV`, `GIEW`, `HSEM`, `LDIV`, `ETAM` and `EMAN`. The detailed note
documents geometry, hierarchy, inverse-bind evidence and supported layouts.
Its real-sample validation is specifically the three supplied recycled Xbox
models; it does not imply that every native Wii model or animation variant
has been validated.

### `.ppdb`

The 405 PPDB files are LZ11-compressed texture sets. Decompressed samples have
a `pk2` tag and a TPL-style table at a header-relative base. The existing
reference records descriptor pointers, CI8 tile ordering, RGB5A3 palettes
and CMPR blocks. Only the layouts and image modes explicitly listed there
are validated.

### `.anmb`

The 288 animation files are LZ11 streams with a PK3 chunk hierarchy. Existing
notes document reversed FourCCs (`SMNA`, `MINA`, `MANB`, `DNLB`), observed
track data and blend channels; several key subfields and target resolution
remain incomplete. ANMB is Wii-specific and distinct from each `.anm` and
`.mot` family.

## Other Wii room/door companions

| Extension | Count | Sample observation | Current status |
|---|---:|---|---|
| `.pakb` | 116 | LZ11/PK1-like chunk stream; `pzb` tag appears near the start. | No PAKB record decoder; probably paired by stem with PZB room resources, but internal semantics remain open. |
| `.mhb` | 58 | Variable-size binary room file; no stable plain FourCC in the header. | No MHB parser or validated record map. |
| `.zldb` | 58 | Begins `zld\0`; the common sample is 10,032 bytes and contains repeated room-name text such as `rch00`. | Header/table observations only; no decoded semantic records. |
| `.pvcb` | 6 | Small camera sidecar; a compressed/chunked prefix precedes `EVEC`. | No PVCB decoder; do not treat as a plain EVEC array. |
| `.motb` | 1 | Door motion file with LZ11/PK1-like prefix and animation chunk tags. | No motion-track decoder. |

These file names and locations establish their role as Wii siblings or room
sidecars, not the meaning of every field. Their LZ11/chunk envelopes are
described only where an existing format note verifies the structure. The
single MOTB sample is insufficient to generalize version coverage.

## Parser coverage boundary

The existing FF2 Wii reader supports MDLB/PK2B geometry and PPDB textures.
ANMB has a separate analysis document, but full animation playback/export is
not implemented. PAKB, MHB, ZLDB, PVCB and MOTB are cataloged from supplied
bytes only. No new parser is implied by this inventory.
