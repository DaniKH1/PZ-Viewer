# Fatal Frame 2 Xbox reader

The Extra tab loads FF2 Xbox `.mdl` character packages and `.pk2` prop, door,
furniture, item and room packages. A matching same-stem `.ppd` is required for
either model extension. A `.ppd` is a sidecar and cannot be opened as a model.

The adapter in `pz_core/ff2x/pz_model_ff2x.py` identifies `0x1070` model
records, pairs each record with an `xpd` vertex buffer from the PPD, and sends a
bounded geometry payload to the shared Xbox `0x1060` reader. It then restores
the FF2 Xbox orientation, merges records, decodes XPR0 textures and binds
material slots. Multi-archive rooms resolve each XPR allocation through its
own PPD reference, preventing secondary shadow textures from reusing the
primary archive's bytes. FF2 Xbox has its own browser entry and theme.

## Format references

| Reference | Contents |
|---|---|
| [`.mdl`](mdl-format.md) | Character container framing and nested model records |
| [`.pk2`](pk2-format.md) | Directory layout, model records and command blocks |
| [`.ppd`](ppd-format.md) | PPD directory and geometry-buffer blocks |
| [XPR0 textures](xpr0-format.md) | Resource descriptors, payload placement and supported images |

The [format index](formats.md) links to each reference. The
[ancillary-file inventory](ancillary-files.md) records observed animation,
room and camera sidecars whose internal semantics are not yet decoded.

## Rendering and file browser

- Packed vertex colors are read from each interleaved vertex at its declared
  stride. Room vertex colors are enabled by default as baked room lighting.
- The viewer suppresses only the observed untextured four-triangle character
  helper with a vertex at the origin; it retains other meshes.
- The FF2 Xbox file tree hides `camera` at the configured root and `mh`, `pzb`
  and `zld` directly inside `room`. Hiding these folders does not remove files.
- The folder picker expects the FF2 Xbox `3ddata` root.

## Known limits and validation

The supplied `rks00.pk2` and `ry11.pk2` room packages contain large category
`14` commands in dedicated blocks. Their payload semantics are not established.
The FF2X reader accepts them only when the command is the sole content of its
block, followed by the block terminator; it records the command in diagnostics
and leaves the opaque payload unused when building render geometry.
`camera/VciTest.pk2` is not a model package. Other unsupported resource and
command layouts still fail explicitly.

The local sample set contains 88 `.mdl` and 443 `.pk2` files. The supported
character packages and 442 of the PK2 model packages were parsed in the
recorded validation run, including the two rooms with isolated category `14`
blocks. The remaining PK2 file is `camera/VciTest.pk2`. Texture-format coverage
depends on the resource layouts present in those samples; see
[XPR0 texture notes](xpr0-format.md).

No automated test suite is included in the repository. The parser has been
validated against local game files; preserve the sample-based checks when
changing record pairing, vertex layouts, texture offsets or material binding.
