# FF1 Xbox PKX package reader

`pz_core/ff1x/pz_pkx_ff1x.py` reads standalone Xbox `.pkx` resources from the
`item`, `furniture`, `door` and `room` directories. It validates the package
framing, parses the embedded XPR0 resource table, and passes only the bounded
`0x1060` geometry segments to the native Xbox geometry reader. It does not
search the payload for guessed signatures.

## Package framing

The first word is the number of segments; the remaining three header words are
zero. Each segment has a 16-byte descriptor containing its byte size followed
by three zero words, then exactly that many bytes. Segment zero is the XPR0
archive and its size must match the archive's declared size. Later segments
are aligned `0x1060` geometry records. The package ends in exactly 16 bytes of
`FF`.

The descriptor offsets and geometry payload lengths are preserved when handing
geometry to the existing parser. Unsupported descriptors, truncated payloads,
and unexplained trailing bytes fail with an offset-specific error.

## Vertex declarations

The mesh command's declaration word identifies the room's compact formats:

| Declaration | Layout | Stride |
|---|---|---:|
| `0x8` | position: three float32 values | 12 |
| `0x9` | position: three float32 values, UV: two float32 values | 20 |
| `0x2` | position: float3, normal: float3, packed attribute DWORD | 28 |
| `0x3` | position: float3, normal: float3, packed attribute DWORD, UV: float2 | 36 |

These layouts are confirmed by the vertex ranges and command counts in all 40
room packages; the `0x2`/`0x3` layouts also occur in furniture and doors. The
DWORD is decoded as candidate packed RGBA on non-room assets, but its color
semantics are not confirmed. For room packages, the viewer ignores that
candidate and bakes static vertex lighting from the same-name `.lit` sidecar
using the Xbox room geometry and material values. The Xbox room geometry's X/Z
basis is rotated 180 degrees around Y relative to the LIT coordinates before
evaluating lights. Without a valid sidecar, the viewer uses neutral white
vertex colors rather than the unverified packed value. The result is shown
through the viewer's **Vertex Colors (V)** toggle, enabled by default for rooms
with a successful bake. Since `0x8`/`0x9` carry no normals, the reader
generates vertex normals from the validated triangle strips. Unknown
declaration bits are rejected.

Each room also contains a large declaration-`0x8` mesh with no UVs or texture
slot. Its triangles duplicate the textured room geometry: all 44 such meshes
across the supplied room files overlap the textured triangles by at least
98.6%. Drawing this untextured proxy after the textured surfaces obscures them
in the viewer. The reader suppresses that proxy from rendering only when this
overlap is verified, and reports the omitted entry, command, and overlap in
`pkx_suppressed_duplicate_proxies`; the source command remains in parse
diagnostics.

## Embedded XPR0 resources

The resource table contains texture descriptors (type 4, 20 bytes) and P8
palette descriptors (type 3, 12 bytes). Texture resources appear in material
slot order; a P8 texture is immediately followed by its 256-entry,
little-endian A8R8G8B8 palette. Palette records are not counted as material
texture slots.

Observed pixel formats are P8 (`0x0B`), DXT3 (`0x0E`) and DXT5 (`0x0F`).
P8 mip levels contain one index byte per texel and use rectangular Morton
ordering; each mip is unswizzled independently and expanded through its
palette. DXT resources reuse the validated BC2/BC3 decoder. Unsupported pixel
formats, palette associations, mip sizes or resource overlaps produce errors
instead of a success-shaped empty scene.

One supplied room package contains an extreme finite UV value on a vertex. The
reader preserves it and records a diagnostic warning rather than silently
clamping source data.

The viewer exposes `.pkx` in the FF1 Xbox file tree, reports items as `item`,
rooms as `room`, and furniture/doors as `prop`. OBJ and GLB exports use the
Xbox UV convention without changing the cached model.

## Sample validation

The parser was exercised on the 316 supplied packages: 36 items, 160 pieces of
furniture, 80 doors and 40 rooms. This covers 1,605 P8 textures, 60 DXT3
textures, 72 DXT5 textures, all observed mesh declarations and the textureless
`i041_men.pkx`. Material references are checked against texture-slot order.
