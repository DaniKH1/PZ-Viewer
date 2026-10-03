# Source asset format atlas

This atlas records the file types found beneath `F:\Tools\PZ\Project Zero
Files` on **2026-10-03** and links them to the detailed format notes in this
project. The source collection contained **24,625 files, 48 filename
extensions (including extensionless markers), and six game/platform trees**:
FF1 PS2, FF1 Xbox, FF2 PS2, FF2 Xbox, FF2 Wii and FF3 PS2.

The inventory is a census of the supplied directory tree, not a claim that all
formats used by each game have been extracted. Counts can change as the source
set changes. Empty placeholder files are counted because they occur in the
tree; they contain no format payload to reverse engineer.

## How to read the status labels

- **Documented** — the available binary samples and/or existing implementation
  support a concrete structural description. A linked document gives scope
  and evidence.
- **Observed only** — the extension, location, byte signature or repeated
  layout is recorded, but the data semantics are not established well enough
  to claim a complete format specification.
- **Container/standard** — the file is an established wrapper or image type;
  any game-specific embedding or unsupported variant is called out separately.
- **Empty marker** — all sampled files for that row are zero bytes. The name
  may be meaningful to the extracted directory tree, but there is no payload.

Identical extensions do **not** imply identical formats across games. In
particular `.anm`, `.mdl`, `.pk2`, `.pak`, `.zld` and `.sgd` are scoped by the
game column, not decoded by extension alone. Similarly, records found inside
an archive do not necessarily share the format of a same-named standalone
file.

## Collection census by game

| Tree | Files | Extensions |
|---|---:|---:|
| FF1 PS2 | 612 | 12 |
| FF1 Xbox | 683 | 10 |
| FF2 PS2 | 909 | 8 |
| FF2 Xbox | 1,516 | 9 |
| FF2 Wii | 1,280 | 9 |
| FF3 PS2 | 19,625 | 21 |
| **Total** | **24,625** | **48** |

## Format inventory

| Extension | FF1 | FF1 Xbox | FF2 | FF2 Xbox | FF2 Wii | FF3 | Observed role / signature | Status and reference |
|---|---:|---:|---:|---:|---:|---:|---|---|
| `.___________` | — | — | — | — | — | 2 | Four-byte marker sample contains ASCII `]rif`; one same-named file is empty. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.acs` | 2 | 3 | — | — | — | — | Distinct per-game package. FF1 Xbox sample contains XPR0 at `0x20`; FF1 PS2 sample begins with a count/offset-style header and an embedded `0x1050` record. | Observed only; [FF1](ff1/formats.md), [FF1 Xbox](ff1x/formats.md). |
| `.anm` | 77 | 80 | 148 | 158 | — | — | Game-scoped animation data. FF2/FF2 Xbox examples have a little-endian count-style header and `MOTN` at `0x40`; FF1 and FF1 Xbox are separate samples. | Observed only; [FF1](ff1/formats.md), [FF1 Xbox](ff1x/formats.md), [FF2](ff2/formats.md), [FF2 Xbox](ff2x/ancillary-files.md). |
| `.anmb` | — | — | — | — | 288 | — | LZ11-compressed Wii animation container with reversed FourCC chunk names such as `SMNA`, `MINA`, `MANB`, `DNLB`. | Documented; [ANMB](ff2w/anmb-format.md), [LZ11](ff2w/lz11-compression.md). |
| `.bin` | — | — | — | — | — | 362 | FF3 room-data members; sampled files have zeroed first 16 bytes followed by small integer tables. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.bmd` | — | — | — | — | — | 1,967 | Begins `BMD\0`; stored in furniture motion/asset packages. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.bwc` | 5 | — | — | — | — | — | FF1 character/model sidecar; sample embeds a TIM2 picture at `0x20`. | Observed only; [FF1](ff1/formats.md). |
| `.ccs` | — | — | — | — | — | 84 | Small FF3 data files, commonly sequences of little-endian floats; no stable signature observed. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.cld` | — | — | — | — | — | 54 | FF3 room collision members; observed header plus fixed 48-byte records. Current viewer helper derives conservative bounds, not exact primitive semantics. | Partly characterized; [FF3 collision notes](ff3/ancillary-formats.md#cld-collision-members). |
| `.clt` | 5 | — | — | — | — | — | FF1 character/model sidecar with the same sample length as `.bwc`; sampled payload includes TIM2. | Observed only; [FF1](ff1/formats.md). |
| `.dmy` | 58 | — | — | — | — | — | All supplied files are zero bytes. | Empty marker; [FF1](ff1/formats.md). |
| `.ecb` | — | — | — | — | — | 6 | FF3 camera/room control data; starts `ecb\0`, followed by integer counts/offsets. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.evec` | — | — | — | — | — | 23 | FF3 camera records; starts `EVEC`, then small integers and float-like vectors. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.lit` | 40 | 40 | — | — | — | — | FF1 and FF1 Xbox room light data; the supplied Xbox samples match the FF1 data byte-for-byte. | Documented for FF1 lighting; [lighting guide](ff1/lighting-guide.md). |
| `.mdl` | 67 | 64 | 75 | 88 | — | — | Four incompatible scopes: FF1, FF1 Xbox legacy/package, FF2 PS2 character package, FF2 Xbox 0x1070 character container. | Documented per platform where known: [FF1](ff1/formats.md), [FF1 Xbox](ff1x/formats.md), [FF2](ff2/mdl-format.md), [FF2 Xbox](ff2x/mdl-format.md). |
| `.mdlb` | — | — | — | — | 160 | — | LZ11-compressed Wii skeletal model; `pk3`/`pk1` chunk hierarchy. | Documented; [MDLB](ff2w/mdlb-format.md), [LZ11](ff2w/lz11-compression.md). |
| `.mh` | — | — | 58 | 58 | — | — | FF2 and FF2 Xbox room sidecar; supplied samples share the same 1,088-byte example and leading count/offset-like table. | Observed only; [FF2](ff2/formats.md), [FF2 Xbox](ff2x/ancillary-files.md). |
| `.mhb` | — | — | — | — | 58 | — | Wii room counterpart to `.mh`; compressed/chunked bytes, no confirmed semantic decoder. | Observed only; [FF2 Wii](ff2w/formats.md). |
| `.mim` | 42 | 7 | — | — | — | — | FF1 room/furniture-mime metadata; sample has `MIME` at `0x20`; many entries are empty. | Observed only; [FF1](ff1/formats.md). |
| `.mot` | — | — | 1 | 1 | — | — | FF2 and FF2 Xbox door animation sample; both files are byte-identical and contain `MOTN` at `0x20`. | Observed only; [FF2 Xbox ancillary files](ff2x/ancillary-files.md). |
| `.motb` | — | — | — | — | 1 | — | LZ11-compressed Wii motion container with `pk1`-style chunks and animation tags. | Observed only; [FF2 Wii](ff2w/formats.md). |
| `.mpk` | 3 | 3 | — | — | — | — | FF1 / FF1 Xbox package of model components; sample has ten entries and embedded `0x1050` model data. Matching PS2/Xbox sample bytes were identical. | Observed only; [FF1 Xbox](ff1x/formats.md). |
| `.mpx` | — | 5 | — | — | — | — | Xbox character container, ten-entry layout with 0x1060 geometry records and sibling XPR. | Documented; [MPX/0x1060](ff1x/sgd-1060-format.md), [XPR](ff1x/xpr-format.md). |
| `.pak` | — | — | 58 | 116 | — | — | FF2/FF2 Xbox room sidecar; outer count-style wrapper with `pzb\0` payload at `0x20`. | Observed only; [FF2 Xbox ancillary files](ff2x/ancillary-files.md). |
| `.pakb` | — | — | — | — | 116 | — | Wii room sidecar with LZ11/PK1-like chunk data and `pzb` text near the beginning. | Observed only; [FF2 Wii](ff2w/formats.md). |
| `.phf` | — | — | — | — | — | 238 | All supplied files are zero bytes; filenames act as placeholders in the extracted FF3 room object-pack tree. | Empty marker; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.pk2` | 40 | — | 504 | 443 | — | — | Three unrelated families: FF1 package wrapper, FF2 PS2 container, and FF2 Xbox directory of 0x1070 model records. | Documented where implemented; [FF1](ff1/formats.md), [FF2](ff2/formats.md), [FF2 Xbox](ff2x/pk2-format.md). |
| `.pk2b` | — | — | — | — | 188 | — | LZ11 Wii prop/room package; inner geometry shares MDLB chunk vocabulary, room lists use an 8-byte vertex record. | Documented; [PK2B](ff2w/pk2b-format.md), [LZ11](ff2w/lz11-compression.md). |
| `.pk4` | — | — | — | — | — | 903 | All files at this extension in the census are empty placeholders. Actual extracted archives with a PK4 signature occur under `.shp` and nested directories. | Empty marker vs PK4 container distinction; [FF3 PK4](ff3/pk4-format.md). |
| `.pkx` | — | 316 | — | — | — | — | FF1 Xbox framed package with embedded XPR0 and bounded 0x1060 geometry entries. | Documented; [PKX](ff1x/pkx-format.md). |
| `.png` | — | — | — | — | — | 33 | Standard PNG images extracted under FF3 texture folders. | Standard image format. |
| `.pob` | — | — | — | — | — | 149 | FF3 room object/placement member; starts `pob\0`, with an integer word and float-like values. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.ppd` | — | — | — | 588 | — | — | FF2 Xbox geometry/texture sidecar; `pk2\0` directory with `xpd\0` vertex buffers and texture allocation. | Documented; [PPD](ff2x/ppd-format.md), [XPR0](ff2x/xpr0-format.md). |
| `.ppdb` | — | — | — | — | 405 | — | LZ11 Wii texture container with TPL-style descriptors, CI8/CMPR pixels and palette descriptors. | Documented; [PPDB](ff2w/ppdb-format.md), [LZ11](ff2w/lz11-compression.md). |
| `.pvc` | — | — | — | 6 | — | — | FF2 Xbox camera data package; sample wraps `EVEC` records. | Observed only; [FF2 Xbox ancillary files](ff2x/ancillary-files.md). |
| `.pvcb` | — | — | — | — | 6 | — | Wii camera data counterpart; sample has a compressed/chunked prefix and `EVEC` tag. | Observed only; [FF2 Wii](ff2w/formats.md). |
| `.pzb` | — | — | — | — | — | 250 | FF3 room payload; starts `pzb\0` followed by counts/offset-like fields and float data. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.rcb` | — | — | — | — | — | 112 | FF3 room data table; starts `rcb\0`, followed by length/count/offset-like words; contains embedded identifiers. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.rmd` | — | — | — | — | — | 1,967 | FF3 furniture motion/asset member; begins `RMD\0`; some files are only a 16-byte header. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.sgd` | 269 | 156 | — | — | — | 2,388 | PS2 0x1050 model/resource members, plus game/platform-specific extracted variants. Xbox MPX geometry also uses an internal 0x1060 record, not a standalone PS2 SGD file. | Documented 0x1060 family; PS2 parser notes in [FF1](ff1/formats.md) and [FF3](ff3/pk4-format.md). |
| `.shp` | — | — | — | — | — | 24 | FF3 character shape pack; sample begins with `PK4\0` and contains nested PK4 directories. | Documented; [PK4/SH P container](ff3/pk4-format.md). |
| `.tm2` | — | — | — | — | — | 8,358 | Standard PS2 TIM2 texture pictures; many are leaves from nested FF3 TPK archives. | Container described in [FF3 TIM2 notes](ff3/tim2-format.md); picture decoder supports documented types. |
| `.tmpflags` | — | — | — | — | — | 2,395 | FF3 temporary flag data, 16 or 20 bytes in the supplied sample; values at the tail vary. | Observed only; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.txt` | 4 | — | 7 | — | — | 175 | Plain text/metadata. FF3 `__phf_meta.txt` files are comma-separated extracted entry manifests. | Text, not a proprietary binary format; [FF3 auxiliary files](ff3/ancillary-formats.md). |
| `.xpr` | — | 9 | — | — | — | — | Xbox texture archive with `XPR0` signature, resource directory and GPU texture payloads. | Documented; [XPR0](ff1x/xpr-format.md). |
| `.zld` | — | — | 58 | 58 | — | 127 | Same extension used by FF2/FF2 Xbox room sidecars and FF3 room object data; observed leading `zld\0` and size field, different sets/counts. | Observed only and game-scoped; [FF2 Xbox](ff2x/ancillary-files.md), [FF3](ff3/ancillary-formats.md). |
| `.zldb` | — | — | — | — | 58 | — | Wii room counterpart; begins `zld\0`, includes repeated room-name strings in sample. | Observed only; [FF2 Wii](ff2w/formats.md). |
| `<no extension>` | — | — | — | — | — | 8 | All eight FF3 names are zero-byte start/end markers such as `VCI_START` and `ROOM_LABEL_END`. | Empty marker; [FF3 auxiliary files](ff3/ancillary-formats.md). |

## Existing specifications retained

The atlas supplements rather than replaces the format documentation already
present in this repository:

- **FF1 PS2:** [game guide](ff1/guide.md), [static lighting](ff1/lighting-guide.md),
  [room-lighting evidence](ff1/room-lighting.md), and [color/pose notes](ff1/color-and-pose.md).
- **FF1 Xbox:** [guide](ff1x/guide.md), [MPX/0x1060](ff1x/sgd-1060-format.md),
  [PKX](ff1x/pkx-format.md), and [XPR0](ff1x/xpr-format.md).
- **FF2 PS2:** [MDL package flow](ff2/mdl-format.md) and
  [textures/vertex-color notes](ff2/textures-and-vertex-colors.md).
- **FF2 Xbox:** [format index](ff2x/formats.md), [overview](ff2x/README.md),
  [MDL](ff2x/mdl-format.md), [PK2](ff2x/pk2-format.md), [PPD](ff2x/ppd-format.md),
  [XPR0](ff2x/xpr0-format.md), and the new [sidecar inventory](ff2x/ancillary-files.md).
- **FF2 Wii:** [MDLB](ff2w/mdlb-format.md), [PK2B](ff2w/pk2b-format.md),
  [PPDB](ff2w/ppdb-format.md), [ANMB](ff2w/anmb-format.md), and
  [LZ11](ff2w/lz11-compression.md).
- **FF3:** new [format index](ff3/formats.md), [PK4 containers](ff3/pk4-format.md),
  [TIM2 pictures](ff3/tim2-format.md), and [auxiliary-file observations](ff3/ancillary-formats.md).

## Limits of this reverse-engineering pass

The collection includes several opaque, extracted sidecars for which the
provided bytes and existing code do not establish a safe field interpretation.
For those types, the new references preserve observed signatures, sizes,
locations, associations and byte-layout clues while marking semantics as
unresolved. No unsupported format claim has been promoted to a parser or a
guess-based decoding rule. This work changes documentation only; it does not
change parsers, the viewer, or source game files.
