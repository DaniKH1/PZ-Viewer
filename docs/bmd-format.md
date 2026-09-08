# Project Zero 3 BMD Format

This document records the current reverse-engineering status of the `.bmd`
animation files used by Project Zero 3 / Fatal Frame 3. Information marked as
experimental must not be treated as a definitive format specification.

## Identification

- Signature: `BMD\0` in the first four bytes.
- Integers are little-endian.
- The files examined mainly come from:
  `3ddata/character/motion`.
- The BMD format is different from the MOTN format used by earlier versions.

## Known header

| Offset | Type | Meaning |
| ---: | --- | --- |
| `0x00` | 4 bytes | `BMD\0` signature |
| `0x04` | `uint16` | Frame count |
| `0x06` | `uint16` | Record or block count |
| `0x08` | `uint16` | Bone count, normally 25 |
| `0x0A` | bytes | Parent table, one byte per bone |
| `0x0E` | `uint16` | Flags or another header value |
| `0x23` | `uint16[]` | Translation-channel table, provisional |
| `0x54` | `uint32[]` | Track-reference table, provisional |

For the `0001.bmd` sample:

- Frames: `490`
- Records: `246`
- Bones: `25`
- Size: `57,536` bytes

## Record area

The end of the file consists of fixed-size blocks:

```text
224 bytes per record
```

The calculated start of this area is:

```text
record_offset = file_size - record_count * 224
```

Records must not be interpreted directly as 112 half-float values. That
interpretation was rejected because it does not produce coherent transform
values.

In several samples, a 224-byte block can be read as 56 `float32` values when
accessed through a valid reference. This demonstrates that IEEE-754 data is
present, but does not demonstrate that the 56 values form a complete frame or
that their semantics are simply rotation/scale/translation.

## Base pose

The examined files contain an initial structure with enough space for:

```text
bone_count * 9 * 4 bytes
```

For 25 bones this is 900 bytes. The provisional per-bone division is:

```text
3 rotation values
3 scale values
3 translation values
```

This division is useful for inspection and for preserving the base pose, but
it has not been proven to be the complete layout of every frame.

## Hierarchy

The table beginning at `0x0A` contains 25 parent identifiers in normal files.
SGD character models may contain 26 bones, so the exact correspondence between
all BMD bones and model bones has not yet been confirmed.

The following must not be assumed without validation:

```text
bmd_bone_i == model_bone_(i + 1)
```

This association was used experimentally in the viewer and may be incorrect.

## Tracks and references

The area near `0x54` contains pairs of 32-bit integers that appear to be
start and end references for each bone's tracks. Some short clips lack valid
references for the final bones, so the current parser tolerates incomplete
entries.

Address tables pointing to blocks in the record area were also located. In the
file family examined, references were observed from `0x260` onward, although
the exact offset and length of this table are not confirmed for every variant.

Repeated pointers indicate that frames may reuse packets. Some files also
contain many more frames than distinct packets, suggesting reuse,
interpolation, or an intermediate index table.

## Experimental decoder not considered definitive

The parser contains a provisional expansion that groups seven values per bone
and interprets them as follows:

```text
3 rotation values
3 translation values
1 reserved value
```

This hypothesis is unconfirmed and does not produce visually reliable
animation. It has also not been proven whether the values are Euler angles,
quaternions, absolute translations, or deltas from the base pose.

## Current parser status

`pz_core/pz_bmd.py` currently exposes:

- `BMDHeader`
- `BMDTrack`
- `BMDFile`
- `BMDMotionClip`
- `parse_bmd()`
- `parse_bmd_motion()`

The parser can currently:

1. Validate the signature.
2. Read the frame count, record count, and bone count.
3. Read the parent table.
4. Read the provisional channel table.
5. Calculate the start of the 224-byte records.
6. Preserve records without altering their contents.
7. Extract the base pose when the expected block is available.
8. Locate packets referenced by valid addresses.

## Open questions

The following are still unproven:

- The exact meaning of every bit or quantized field.
- The relationship between records, packets, bones, and frames.
- Interpolation between packets.
- The exact order of rotation, scale, and translation.
- The unit and representation of angles.
- The mapping between the 25 BMD bones and the 26-bone SGD model.
- Mesh deformation using BMD transforms.
- The difference between clips with complete tables and short clips.

The safest way to continue is to compare several clips from the same
character, especially consecutive frames with known movement, and trace
repeated references before connecting the result to mesh deformation.
