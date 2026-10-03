# Fatal Frame 2 Xbox format references

This page is the entry point for the FF2 Xbox format notes. The files describe
the currently implemented reader, observed structures, validation rules and
known limits; uncertain fields are marked as such rather than assigned guessed
meanings.

| File | Scope |
|---|---|
| [Overview](README.md) | Reader flow, shared structures, support status and limitations |
| [`.mdl`](mdl-format.md) | Character model container and its `0x1070` records |
| [`.pk2`](pk2-format.md) | Object/room model container and directory entries |
| [`.ppd`](ppd-format.md) | Geometry vertex-buffer sidecar and `xpd` blocks |
| [XPR0 textures](xpr0-format.md) | Embedded resource table and texture data stored in PPD |
| [Ancillary files](ancillary-files.md) | Observed ANM, MOT, MH, PAK, ZLD and PVC samples; unresolved semantics |

All offsets and numeric fields are little-endian unless noted. The parser
implementation is in `pz_core/ff2x/pz_model_ff2x.py`; bounded Xbox geometry
and texture decoders are reused from `pz_core/ff1x/`.
