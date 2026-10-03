# FF3 PS2 TIM2 texture pictures

The FF3 texture decoder lives in `pz_core/ff3/pz_tim2_ff3.py` and is shared by
some other game-specific adapters after their own signature checks. The
8,358 `.tm2` files in the supplied FF3 tree are TIM2 picture resources; many
additional pictures are leaves of nested texture packages.

## File and picture headers

The decoder requires at least 16 bytes and the ASCII `TIM2` signature. The
file header begins with `TIM2`, one-byte format version, one-byte format ID,
a little-endian 16-bit picture count and eight additional header bytes.
Pictures are normally read beginning at offset `0x10`.

The decoder also accommodates a sample-specific aligned/padded layout used by
extracted FF3 object TPKs: when the word at `0x10` is zero, it searches
16-byte-aligned candidate offsets from `0x20` through the first 256 bytes
for a plausible picture header whose declared total size and dimensions fit
inside the file. This is a compatibility observation, not a general TIM2
rule.

Each picture has a 48-byte base header. Fields are little-endian:

| Picture-header offset | Type | Field |
|---:|---|---|
| `0x00` | `u32` | Total picture size |
| `0x04` | `u32` | CLUT/palette byte size |
| `0x08` | `u32` | Image byte size |
| `0x0C` | `u16` | Picture-header size |
| `0x0E` | `u16` | CLUT color count |
| `0x10` | `u8` | Picture format |
| `0x11` | `u8` | Mipmap count/texture count field |
| `0x12` | `u8` | CLUT storage type |
| `0x13` | `u8` | Image storage type |
| `0x14` | `u16` | Width |
| `0x16` | `u16` | Height |
| `0x18` | `u64` | GS TEX0 register value |
| `0x20` | `u64` | GS TEX1 register value |
| `0x28` | `u32` | GS TEXA register value |
| `0x2C` | `u32` | GS CLUT register value |

Image bytes begin at `picture offset + header size`; CLUT bytes follow the
declared image span when the picture declares palette colors. The next
picture begins after `total size`. The implementation bounds reads by the
declared picture/file spans and can return CLUT-only records when image size
is zero.

## Decoded storage types

The current FF3 decoder handles the following observed values:

| Image type | Interpretation in decoder |
|---:|---|
| `1` | 16-bit RGB/alpha word, expanded to RGBA |
| `3` | 32-bit RGBA; PS2 alpha is adjusted |
| `4` | 4-bit indexed pixels with 16-bit or 32-bit CLUT |
| `5` | 8-bit indexed pixels with 16-bit or 32-bit CLUT; applies the observed CSM1 index rearrangement |

For palette types, low six bits `1` selects 16-bit entries and `3` selects
32-bit entries. The 16-bit path extracts the low three 5-bit color channels
and uses bit 15 for binary alpha. The 32-bit path preserves RGB and applies
the decoder's PS2 alpha adjustment: values through 127 are doubled and
values above 127 become opaque. If the final decoded image has no non-zero
alpha, it is treated as fully opaque.

Unsupported or malformed image/palette types are not fully specified by this
reference. A TIM2 signature alone does not guarantee that every picture
variant is decoded correctly.

## Limits and texture-page relationships

The 4-bit indexed path decodes pixel nibbles but does not apply the 8-bit
CSM1 rearrangement. The decoder also exposes GS register values and palette
bytes for consumers; it does not emulate the PS2 GS. Palette-only pictures
can be paired with indexed base images for color variations where the caller
has established that relationship. Texture package extraction and picture
decoding are separate steps; see [PK4 containers](pk4-format.md).
