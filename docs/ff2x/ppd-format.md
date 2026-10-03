# FF2 Xbox `.ppd` sidecars

Reader: `_ppd_vertex_buffers()` in `pz_core/ff2x/pz_model_ff2x.py`.

A PPD accompanies an `.mdl` or `.pk2`; it is not itself a model. It can contain
geometry vertex buffers and the texture allocation used by XPR0 archives.
Open the corresponding model package in the viewer so the reader can match
the sidecar automatically.

## Directory

The observed PPD directory has the `pk2\0` tag and the same count/absolute
offset-table shape as the model package:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | Directory entry count |
| `0x04` | `char[4]` | `pk2\0` |
| `0x08`, `0x0C` | `u32` | Observed/reserved header words |
| `0x10` | `u32[count]` | Absolute offsets to blocks |

Zero offsets are skipped. Nonzero entries are ordered by their file offsets
before block boundaries are determined. The directory count, offsets and
block ranges are checked against the actual file length.

## `xpd` block descriptor

Each geometry block observed by the reader begins with a 16-byte descriptor:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | Header size; observed value is 16 |
| `0x04` | `char[4]` | `xpd\0` for a vertex-data block |
| `0x08` | `u32` | Payload offset or total block size, depending on form |
| `0x0C` | `u32` | Payload size; zero selects the size-based form |

Two forms are observed:

1. **Explicit payload range:** when the final word is nonzero, word `0x08` is
   an absolute file offset and word `0x0C` is the payload length.
2. **Inline payload:** when the final word is zero, word `0x08` is the full
   block size including the descriptor. The payload begins at block offset
   `+0x10` and has `block_size - 0x10` bytes. The next directory block bounds
   this block.

Blocks with tags other than `xpd\0` are not treated as geometry vertex buffers.
All payload offsets and sizes must remain within the PPD.

## Pairing to model records

The model record's `0x14` field declares the exact vertex-buffer byte count.
The reader selects an unused `xpd` payload with that size, then validates the
candidate through geometry parsing: vertex ranges, command streams, indices
and (where present) source-mapped positions/normals must be consistent. The
selected payload offset and directory index are included in parser
diagnostics. Do not reorder or deduplicate same-sized blocks without
revalidating their association.

## Texture allocation

For the observed FF2 Xbox packages, the XPR0 descriptor table is stored in the
`.mdl` or `.pk2`, but its allocation bytes are reconstructed from the PPD.
The first XPR0 archive's data begins at PPD offset `0x100`; the initial
descriptor and marker/padding region precede that location. Packages with
multiple XPR0 archives also contain explicit `xpd` ranges in the PPD
directory. In the inspected multi-archive PK2 samples there is one such
reference per archive; subsequent archives use the corresponding later
reference rather than reusing `0x100`. The model container's XPR0
total/header sizes determine the byte count, and each resolved range is
validated against the PPD before decoding. Reusing `0x100` for every archive
can decode a later P8 texture from unrelated primary-archive bytes. See
[XPR0 texture notes](xpr0-format.md).
