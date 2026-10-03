# Fatal Frame 1 PS2 asset formats

This file inventories FF1 PS2 extensions found in the supplied game tree.
It complements the [main source-format atlas](../source-asset-formats.md) and
the existing [FF1 guide](guide.md), [lighting guide](lighting-guide.md),
[room-lighting notes](room-lighting.md) and [color/pose analysis](color-and-pose.md).

## Model and texture path

The supplied tree has 40 room `.pk2` packages, 269 `.sgd` model/resource
members, 67 `.mdl` character packages and 40 `.lit` room-lighting sidecars.
They are not one universal package format: the extension indicates a role in
this game tree, but the surrounding container determines how a payload is
found and interpreted.

### `.pk2`

The room PK2 samples contain four entries and embed the room SGD/TIM2 resource
data. Their entry headers are traversed as linked relative offsets; the
first entry begins at offset `0x20`. This is the FF1 PS2 package reader in
`pz_core/common/pz_pk2.py`, not the Xbox `.pk2` directory used by FF2 Xbox.
Room subentries may contain a short wrapper before an embedded TIM2 picture.
Texture lookup therefore stays bounded to the entries and uses the TIM2
signature rather than treating the whole archive as a flat image.

FF1 and FF1 Xbox room packages also have similarly named assets, but the Xbox
`.pkx` framing is separate. Do not infer that identical filenames mean the
same bytes or semantics.

### `.sgd`

The PS2 parser reads the little-endian SGD 0x1050 family. A 24-byte header
contains the version, map/kind bytes, material count, coordinate/material
table offsets, source-pool pointer and primitive-block count. A following
32-bit offset table locates command chains. Common command categories include
VUVN/source data, mesh data, material selection, coordinate selection and
bounds. Material records are 176 bytes in the observed reader; coordinate
records are 224 bytes. Actual geometry and supported vertex attributes are
interpreted by `pz_core/ff1/pz_sgd_ff1.py`.

The same `.sgd` extension also labels PS2-compatible model members nested in
MPK/PK4-style extractions and a separate Xbox tree. Inspect the header and
game-specific location before applying the PS2 layout. Xbox MPX has an
internally bounded 0x1060 record format documented separately.

### `.mdl`

FF1 `.mdl` character packages are not the FF2 PS2 0x1050-SGD package and not
the FF2 Xbox 0x1070 container. The FF1 reader is
`pz_core/ff1/pz_mdl_ff1.py`; its model/package handling is game-specific.
The sample census lists 67 files, of which 59 are non-empty. The supplied
reference notes describe model/pose fixes, but do not claim an exhaustive
field-by-field specification of every FF1 MDL variant.

### `.lit`

The 40 supplied `.lit` files are little-endian room-lighting resources. In
the observed samples their leading 24-byte header follows the same 0x1050
field family used by the room SGD path, followed by a count-sized offset
table and light command blocks. The FF1 lighting guide documents the
validated point-light and ambient-light records, the same-stem sidecar
association, static vertex-lighting calculation and known limitations.
The viewer does not fabricate lights if a valid LIT file is absent.

## Texture resources

### `.tm2`

FF1 PS2 TIM2 pictures use the standard `TIM2` picture header and GS-related
metadata. FF1 `.pk2` packages can embed pictures inside entry payloads, so
the package reader searches only inside each bounded entry. Picture formats,
palette variants and PS2 alpha adjustment are handled by
`pz_core/ff1/pz_tim2_ff1.py`.

### `.mim`

The 42 files occur beside room/furnmime assets; 34 are empty and eight have
payload. A non-empty sample begins with a count-style 16-byte envelope and
contains `MIME` at `0x20`, followed by a data size. No decoder in the current
project establishes the complete MIME record layout or its use in-game.
Treat the tag and size as evidence, not as a complete specification.

### `.bwc` and `.clt`

Five `.bwc` and five `.clt` files occur beside character assets. The examined
`m000_miku` examples are both 34,256 bytes and each embeds a TIM2 resource at
`0x20`. Their contents differ, so they are related companion resources, not
aliases. The complete relationship between the BWC and CLT records and
character materials has not been established.

## Animation and auxiliary package files

### `.anm`

The 77 FF1 `.anm` files are distinct from FF1 Xbox, FF2/FF2 Xbox and Wii
`.anmb` animation files. A representative FF1 sample begins with a little-
endian count of 3 and a directory/offset-like region; no stable FourCC was
found in the first bytes. The repository has no FF1 ANM decoder, so the track,
timing and rig-binding structures remain undocumented beyond these samples.

### `.mpk`

Three files occur beside FF1 character models. The representative package
starts with count 10; entries include 0x1050 SGD payloads. The examined
same-name FF1 and FF1 Xbox `m000_miku.mpk` samples are byte-identical, but this
does not establish that all MPK files or consumers are interchangeable.
The project has no dedicated MPK table specification; preserve nested entry
boundaries when inspecting it.

### `.acs`

Two PS2 `.acs` files occur with character models. The sample starts with a
count of 3 and has a count/offset-style envelope before an embedded 0x1050
payload at `0x20`. This file is not the Xbox `.acs` form, which includes an
XPR0 resource. No complete ACS entry schema is confirmed.

### `.dmy`, `.txt`

All 58 FF1 `.dmy` files in the census are zero bytes. They are extracted-tree
markers, not parseable payloads. Four `.txt` files are plain-text metadata;
they are not binary model or texture records.

## Reproducibility and unresolved details

The inventory covers the supplied directory tree and the format descriptions
above are limited to observed bytes and existing reader behavior. Animation
tracks, full MIME/BWC/CLT/ACS semantics, and variants not represented in the
sample set remain open. Nothing in this document changes or supersedes the
existing parsers.
