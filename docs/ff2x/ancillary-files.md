# FF2 Xbox ancillary files

This reference records FF2 Xbox suffixes in the supplied collection that are
not the supported `.mdl` / `.pk2` model path or its required `.ppd` sidecar.
The sample tree has 1,516 files and nine extensions. See the
[main format index](formats.md) and [source asset atlas](../source-asset-formats.md).

## `.anm` character animation

There are 158 FF2 Xbox `.anm` files under `character`. The inspected
`ch000_mio.anm` is 1,441,920 bytes. Its first 16 bytes resemble a four-entry
little-endian container header, and a `MOTN` tag appears at offset `0x40`.
The project has no decoder for these tracks, keyframes, timebase or skeleton
binding. The same filename suffix is used by three other games/platforms and
must not be treated as a universal ANM format.

## `.mot` door animation

The single FF2 Xbox `door_anim.mot` is 37,392 bytes. It begins with a
little-endian count-style prefix and contains `MOTN` at `0x20`. The provided
FF2 PS2 counterpart is byte-identical, including length and content. This
establishes a shared sample, not full compatibility across all versions or a
decoded MOTN record layout.

## Room sidecars

### `.mh`

There are 58 files in `room/mh`. The recurring 1,088-byte sample begins with
two small header words and then integer-looking values (including
`5, 5, 14, 10, 64, 96, 128`). FF2 PS2 includes matching `.mh` files with the
same sample bytes. The exact distinction between counts, offsets and flags
has not been established.

### `.pak`

There are 116 files in `room/pzb`, two per room stem in the supplied tree.
The file begins with a one-entry count-style wrapper; at `0x20` is a `pzb\0`
tag followed by a sequence of integer fields. For example, the first
`rch00_pzb.pak` sample has size 22,304 bytes and a first embedded size-like
value `0x43`. The second sibling uses the `_suv` name suffix. This prefix
does not yet resolve all internal payload types or the semantic difference
between the paired files.

### `.zld`

There are 58 room ZLD files. The first word is the literal `zld\0`; the next
word is `0x1890` in the common 6,288-byte sample. Text-like room/asset names
occur in later records. FF3 also has `.zld`, but its sample size/profile and
tree differ; don't reuse this observation across platforms without comparing
the bytes.

## Camera sidecar: `.pvc`

Six files occur under `camera`. The 160-byte `rks00.pvc` sample has a
one-entry wrapper and an `EVEC` tag at offset `0x20`. The following fields
contain small counts and float-like values. The record count, vector meaning
and any coordinate transform have not been decoded. This should not be
confused with the FF2 Wii `.pvcb` wrapper or FF3 standalone `.evec`.

## Models that are not this format

The `.mdl` entries in FF2 Xbox use 0x1070 records and matching `.ppd`;
their container is described in [`mdl-format.md`](mdl-format.md). `.pk2`
objects/rooms are described in [`pk2-format.md`](pk2-format.md). `.ppd` is a
required sidecar, not a standalone model. The camera `VciTest.pk2` is not a
model container in this game tree.

## Open questions

No parser was added for ANM, MOT, MH, PAK, ZLD or PVC. Their records should be
compared against additional versions and same-stem companions before
assigning semantic meanings to the observed counts, offsets, strings or
float-like fields.
