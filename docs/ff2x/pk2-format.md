# FF2 Xbox `.pk2` model packages

Reader: `pz_core/ff2x/pz_model_ff2x.py`, `parse_ff2x_model()`.

The supported `.pk2` files contain object, door, furniture, item or room
models. The same-stem `.ppd` is required for geometry vertex data. A `.pk2`
can contain directory entries that are not models, and a package may hold
multiple `0x1070` model records.

## Package directory

The observed header starts with a little-endian entry count and the `pk2\0`
tag. The absolute offset table starts at `0x10`:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `u32` | Directory entry count |
| `0x04` | `char[4]` | `pk2\0` |
| `0x08` | `u32` | Observed/reserved header word |
| `0x0C` | `u32` | Observed/reserved header word |
| `0x10` | `u32[count]` | Absolute offsets to entries; zero offsets are empty |

The reader bounds the count by both the file length and a defensive maximum,
rejects duplicate nonzero offsets, and verifies nonzero offsets lie within the
file. Entries whose first little-endian word is `0x1070` are treated as model
records; other entries are not assumed to be geometry.

## `0x1070` model record and geometry

The record header is the same layout documented for [`.mdl`](mdl-format.md#model-record).
The vertex-buffer size at offset `0x14` must match an `xpd` payload from the
companion PPD. If multiple payloads have that size, the bounded geometry
validation is used while selecting a candidate; keep the PPD ordering intact
when extracting or rebuilding packages.

The geometry reader validates coordinate/material tables, mesh commands,
vertex/index ranges and source mappings. Mesh command category `0`, `1`, `2`,
`3` and `4` are interpreted by the shared reader as source-mapped mesh,
direct mesh, material selection, coordinate selection and bounding-box data
respectively. Block commands are length-prefixed and end with a zero-size
terminator. Unknown categories are rejected rather than skipped.

Two supplied room files contain a large category `14` command in a dedicated
block. Its payload appears as opaque auxiliary data; its semantics have not
been identified. The FF2X adapter accepts it only if the command starts the
block, fills the block up to its zero-size terminator, and has no other
commands in that block. The payload is recorded in diagnostics but is not used
to create render geometry. A category `14` command in another position or
layout remains unsupported.

For a mesh with the color bit and UV bit set, each vertex is interleaved as:

| Byte range | Attribute |
|---:|---|
| `0..11` | Position: three float32 values |
| `12..23` | Normal: three float32 values |
| `24..27` | Packed ARGB color DWORD |
| `28..35` | UV: two float32 values |

The stride is 36 bytes for this declaration. Color data must be read from
offset `vertex_start + index * stride + 24`; treating the color slot as a
contiguous array consumes UV bytes as colors and corrupts the result. The
reader decodes the packed ARGB word; the viewer's serialized vertex-color
attribute uses RGB. FF2 Xbox room colors are enabled by default as baked
lighting.

## Multiple records and textures

Every supported model record in the package is parsed and merged. Materials
receive adjusted indices so each mesh keeps its material assignment. When
there are multiple embedded XPR0 archives, the latest archive before a model
record in file order is associated with that record. Each archive's PPD
payload is resolved independently; see
[PPD texture allocation](ppd-format.md#texture-allocation).

See [XPR0 texture notes](xpr0-format.md) for descriptors, texture payload
placement and supported pixel formats.

## Known unsupported inputs

The isolated category `14` blocks in `rks00.pk2` and `ry11.pk2` are retained in
diagnostics as opaque data; the remaining unknown category `14` layouts are
refused. `camera/VciTest.pk2` is not a `pk2\0` model package. See the
[overview](README.md#known-limits-and-validation) for validation scope.
