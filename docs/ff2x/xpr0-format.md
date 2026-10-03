# FF2 Xbox XPR0 texture resources

Reader: `_xpr0_archives()` and `_decode_archives()` in
`pz_core/ff2x/pz_model_ff2x.py`, with resource decoding delegated to
`decode_xpr0_archive()` in `pz_core/ff1x/pz_pkx_ff1x.py`.

The `.mdl` and `.pk2` model containers carry XPR0 resource descriptors. For
the observed FF2 Xbox layout, the texture allocation itself is stored in the
matching PPD, not beside the descriptor table. The viewer reconstructs a
bounded XPR0 image from both files before decoding it.

## Archive framing

The archive begins with:

| Offset | Type | Meaning |
|---:|---|---|
| `0x00` | `char[4]` | `XPR0` |
| `0x04` | `u32` | Total archive size |
| `0x08` | `u32` | Header size and start of resource data |
| `0x0C` | descriptor table | Ends with a `0xFFFFFFFF` word |

The scanner recognizes texture descriptors of 20 bytes and palette
descriptors of 12 bytes, bounded by the declared header size. Unknown resource
descriptor types, truncated descriptors, a missing terminator, invalid sizes
or descriptors extending beyond the header are errors.

## Resource descriptors

The `Common` word's resource-kind bits distinguish the observed descriptor
types:

| Kind bits | Size | Role |
|---:|---:|---|
| `0x00040000` | 20 bytes | Texture resource |
| `0x00030000` | 12 bytes | Palette resource |

The shared decoder interprets each resource's second word as a relative data
offset and derives the allocation from the next resource offset or the end of
the archive data. Offsets must be strictly increasing and non-overlapping.
Texture descriptors also carry the Xbox format/dimension/mip tag; a nonzero
Lock or unsupported Size value is rejected for the supported layouts.

For P8 textures, the next resource must be the associated palette resource.
The palette has 256 little-endian A8R8G8B8 entries (1024 bytes). Palette
resources do not become material texture slots. Material resource indices are
translated to decoded texture indices, and invalid references fail explicitly.

## FF2 Xbox payload placement in PPD

The XPR0 header and resource table are copied from the model container. The
image allocation begins at PPD offset `0x100`, after the first 16-byte PPD
block descriptor and the observed marker/padding area. The reader copies only
the byte count declared by `total_size - header_size`, checks that the range
fits in the PPD and then calls the XPR0 decoder. This `0x100` base is important:
using the earlier `0x90` position reads marker bytes as texture data and
corrupts the decoded images.

In a `.pk2` with multiple XPR0 sections, the latest section preceding each
model record is used for its materials. Character packages can likewise carry
more than one model record and archive section.

## Supported texture data

The reused Xbox decoder handles:

- **P8 (`0x0B`)**: one byte per texel, unswizzled per mip using rectangular
  Morton ordering and expanded through the following 256-entry palette.
- **DXT3 (`0x0E`) and DXT5 (`0x0F`)**: decoded using the shared Xbox BC2/BC3
  texture path.

Width, height and mip count come from the Xbox texture tag. Only supported
2D, non-cubemap resources with valid mip dimensions are accepted. Unsupported
formats, invalid palette associations, out-of-range mip chains and malformed
resource ranges are reported as parser errors rather than rendered as empty
textures.

## Related references

- [PPD sidecar layout](ppd-format.md)
- [`.mdl` container](mdl-format.md)
- [`.pk2` container](pk2-format.md)
