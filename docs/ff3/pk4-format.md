# FF3 PK4, SHP and SGD model resources

Fatal Frame 3 character/model data is commonly exposed as nested archives and
SGD leaves. The implementation is in `pz_core/ff3/pz_pk4.py` and
`pz_core/ff3/pz_sgd_ff3.py`. The sample tree has 24 `.shp` files and 2,388
`.sgd` members. Its 903 standalone `.pk4` files are all empty placeholders,
not binary PK4 archives.

## PK4 archive framing

All integer fields and offsets described here are little-endian.

| Offset | Width | Observed field |
|---:|---:|---|
| `0x00` | 4 | ASCII `PK4\0` signature |
| `0x04` | 4 | Archive ID, retained on every entry |
| `0x08` | 4 | Entry count |
| `0x0C` | 4 | Reserved/padding in the reader |
| `0x10` | `count × 4` | Absolute offsets to entry subheaders |

Offsets must be within the file, at or after the end of the offset table,
strictly increasing and unique. Each entry extends to the next entry offset,
or to end-of-file for the final entry. Its 16-byte subheader is followed by
the bounded payload:

| Entry-relative offset | Width | Reader use |
|---:|---:|---|
| `0x00` | 8 | Preserved as opaque subheader fields |
| `0x08` | 4 | ASCII entry type, trailing NULs removed |
| `0x0C` | 4 | Preserved as opaque subheader field |
| `0x10` | remaining entry span | Payload |

The implementation recursively treats an entry payload as another archive
only when it begins with `PK4\0` and its directory validates. The resulting
leaf entries retain their indices, offsets, type and archive ID. Observed
leaf types include `sgd`, `mpk` and `tpk`; `MPK`/`TPK` here are encountered as
nested PK4-framed data rather than standalone specifications for every file
bearing those names.

## SHP files and empty PK4 placeholders

The 24 non-empty `.shp` samples start with `PK4\0` and contain nested archive
directories. In this extracted tree, a `.pk4` suffix is instead attached to
903 zero-byte placeholder files; they have no header or parseable entry
table. The existence of an empty `.pk4` marker does not imply that its
contents are a PK4 archive. Conversely, archive bytes can exist inside an
`.shp` or another nested entry.

## SGD leaves and model assembly

The FF3 SGD reader uses a 24-byte little-endian header with the observed
0x1050 family fields: version, map/kind bytes, material count, coordinate
table pointer, material table pointer, PHEAD pointer and primitive-block
count. A following table contains one 32-bit offset per primitive block.
Material records are read with a 176-byte stride; coordinate/bone records
with a 224-byte stride. The PHEAD structure supplies offsets/counts for
vertex/normal pools. Primitive blocks are traversed as linked commands with
relative sizes and category values; supported records populate geometry,
material selection, coordinate association and vertex attributes.

This is a description of the parser's observed/implemented path, not a
complete specification of every 0x1050 SGD variant. In particular,
unhandled command categories and auxiliary blocks should remain opaque. The
reader can reuse bones from an earlier sibling SGD when a later component
omits its own coordinate table. PK4 model assembly merges the renderable
SGD leaves and, for known character package layouts, excludes the final
collision/non-renderable component by entry index. That package-specific
filter must not be interpreted as an SGD command rule.

## Boundaries

The parser validates table boundaries and entry spans before using a payload.
It does not decode all nested package families, animation tracks, or the
semantics of every SGD command. The `.shp` signature establishes container
framing, not the full meaning of all shape entries. See
[TIM2 pictures](tim2-format.md) for texture leaf decoding and
[FF3 collision observations](ancillary-formats.md#cld-collision-members) for
the separate CLD path.
