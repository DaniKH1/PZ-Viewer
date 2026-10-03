# FF3 auxiliary and room asset observations

The FF3 tree contains many non-model sidecars. This page records their
locations, signatures and repeatable byte-layout observations without
inventing field semantics. Counts refer to the supplied source-tree census in
the [format atlas](../source-asset-formats.md). A signature or plausible
integer/float is not enough to establish a field's purpose.

## Room-data members

### `.bin`

There are 362 room-data files. The sampled members begin with 16 zero bytes,
then contain small integer-looking tables. The zeros alone do not identify a
version or record family; table offsets, counts and references have not been
validated across the full set. No `.bin` decoder is documented here.

### `.pzb`

The 250 samples begin with `pzb\0` and continue with integer/count/offset-like
words and float-like data. The extension occurs in the room object/data tree.
Its relationship to same-room `.pob`, `.rcb` and `.zld` files is suggested by
co-location, but their cross-references and payload semantics remain
unresolved.

### `.pob`

The 149 object/placement members begin with `pob\0`, followed by an
integer-looking word and float-like values. The observed fields are not
enough to label them as particular transforms, bounds or object IDs. No
complete POB layout or parser is present.

### `.rcb`

The 112 samples begin with `rcb\0`; subsequent words resemble lengths,
counts and offsets, and later bytes include identifiers. The exact table
boundaries and identifier meaning have not been established.

### `.zld`

FF3 `.zld` samples begin with `zld\0`. This suffix is also used for FF2 and
FF2 Xbox room sidecars, but those are separate game/platform sets. Shared
magic alone does not demonstrate a common format or compatible offsets.

## CLD collision members

The 54 `.cld` files under the FF3 collision tree have a 16-byte header read
by the existing helper as four little-endian 32-bit words: record count,
version-like value and two additional words. The helper accepts bounded
counts and version values and expects 48 bytes per record. It reads two
32-bit primitive/flag words and ten 32-bit floats from each record.

The exact primitive schemas have not been decoded. In particular, type-2
records combine coordinates and primitive parameters; the current viewer
collects finite float triples and makes a conservative axis-aligned box.
That output is a visualization bound, not a reconstructed collision shape.
The raw primitive type and flags are retained by the helper, but are not
semantically resolved.

## Camera and control records

### `.evec`

The 23 standalone EVEC files begin with ASCII `EVEC`; small integer fields
precede float-like vectors. Their count/stride, coordinate system and role
are not established. A `.evec` file must be distinguished from camera
sidecars in other games, including FF2 Xbox `.pvc` and FF2 Wii `.pvcb`.

### `.ecb`

The six `.ecb` samples begin with `ecb\0` and have integer/count/offset-like
values near the start. Neither a full header definition nor the meaning of
the referenced data has been confirmed.

## Furniture members

There are 1,967 `.bmd` and 1,967 `.rmd` members in the supplied furniture
motion/asset tree. BMD samples begin `BMD\0`; RMD samples begin `RMD\0`. Some
RMD samples are only 16 bytes long. Their names and co-location are evidence
of association, not a demonstrated one-to-one or shared record layout. No
complete BMD/RMD parser is documented.

## Other observed-but-unresolved sidecars

### `.ccs`

The 84 CCS files are small and commonly contain runs of little-endian
float-like values. No stable magic or reliable record stride was found in
the inspected samples. Float-looking words can also be IDs or packed values;
their semantics remain unknown.

### `.tmpflags`

The 2,395 samples are 16 or 20 bytes in the supplied tree. Values toward the
tail vary. The name suggests temporary state, but the encoding, flag
definitions, versioning and consumer are not established by the bytes alone.

## Empty markers and manifests

All 238 `.phf` files and all eight extensionless marker files are zero bytes.
Examples of the latter include `VCI_START` and `ROOM_LABEL_END`. They are
directory/extraction markers, not payload-bearing format files.

Two files have the unusual underscore-only suffix recorded in the
[cross-game atlas](../source-asset-formats.md): one four-byte sample contains
`]rif`, while the other is empty. The four-byte value is evidence only; no
interpretation or purpose is known.

The 175 `.txt` files are text rather than proprietary binary records. The
`__phf_meta.txt` examples are comma-separated manifests of extracted entries;
other text files should be read as per-file metadata. The 33 `.png` files are
standard PNG images.

## Scope

The observed structures above are limited to the provided FF3 PS2 tree.
Aside from the bounded conservative CLD visualization helper, these files
are cataloged rather than parsed. No parser, game resource, or source asset
was changed as part of this documentation.
