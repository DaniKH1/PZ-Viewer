# Fatal Frame 1 Xbox asset formats

This page adds the supplied FF1 Xbox file census to the existing
[FF1 Xbox guide](guide.md), [0x1060/MPX reference](sgd-1060-format.md),
[PKX reference](pkx-format.md), [XPR0 reference](xpr-format.md) and
[implementation report](report.md). See also the
[cross-game asset atlas](../source-asset-formats.md).

## Formats with detailed specifications

| Extension | Supplied files | Established use |
|---|---:|---|
| `.mpx` | 5 | Character container with bounded 0x1060 geometry entries. |
| `.pkx` | 316 | Standalone item/furniture/door/room packages with geometry and embedded XPR0 resources. |
| `.xpr` | 9 | Xbox texture archive; used alongside MPX in the supported character path. |
| `.sgd` | 156 | Extracted model/resource members; header/layout is game-context-specific and not the MPX outer container. |

MPX and PKX are fully described in their linked format notes. Their geometry
and texture decoders are separate from the PS2 SGD/TIM2 readers.

## Other observed extensions

### `.anm`

The 80 FF1 Xbox `.anm` files are not established as a variant of the FF1 PS2
animation file merely because the suffix is shared. A representative header
begins with a little-endian count of 3 and count/offset-like fields; there is
no stable ASCII FourCC in the first 64 bytes. No track decoder is present in
the project. Bone channels, interpolation and timing remain unresolved.

### `.mdl`

The 64 Xbox `.mdl` samples are legacy/auxiliary model packages, distinct from
the actively supported FF1 Xbox `.mpx` and from FF2 Xbox `.mdl`. The
representative `m000_miku.mdl` includes an XPR0 section at `0x20`; this
observation does not make it equivalent to an MPX. The current FF1 Xbox model
format reference covers MPX and PKX, not a complete MDL reader.

### `.mpk`, `.acs`

The three `.mpk` files share the count-10 package sample and 0x1050 nested
records described in [FF1 notes](../ff1/formats.md#mpk). The three Xbox
`.acs` files use an outer count-style envelope with an XPR0 section at `0x20`;
they are not the PS2 `.acs` variant. The collection includes two files named
`m000_miku.acs` with different sizes and different bytes, demonstrating why
platform scoping is required.

### `.mim` and `.lit`

Seven `.mim` files occur in `furnmime`; the sample signature is `MIME` at
offset `0x20`, matching the observed FF1 PS2 auxiliary family. Their full
records are not parsed. The 40 Xbox room `.lit` files match the FF1 PS2 sample
set byte-for-byte (same names and hashes in the supplied tree) and are used
as same-stem Xbox room-lighting sidecars. See
[FF1 lighting notes](../ff1/lighting-guide.md) and the
[PKX vertex declaration section](pkx-format.md#vertex-declarations) for the
Xbox-specific transform and bake behavior.

## Boundaries

The tree contains 683 files across ten suffixes. Extension counts are not
support claims: only the 316 PKX packages, five MPX files and XPR resources
have the detailed format coverage linked above. `.anm`, `.mdl`, `.mpk` and
`.acs` are documented here as observed/unresolved and were not given a new
parser or guessed record interpretation.
