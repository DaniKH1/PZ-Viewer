# Fatal Frame 2 PS2 asset formats

This reference scopes the `.mdl`, `.pk2`, `.anm`, `.mh`, `.pak`, `.zld`,
`.mot` and text files found in the supplied FF2 PS2 tree. Existing detailed
notes remain authoritative for the supported parts:
[MDL package flow](mdl-format.md) and
[textures / vertex colors](textures-and-vertex-colors.md). The full
[cross-game census](../source-asset-formats.md) distinguishes same-suffix
files from other ports.

## Supported model package

### `.mdl`

FF2 PS2 character MDL is an outer package with an inner model pack and texture
pack. The observed path is:

1. Outer PK2 entry 0 contains the model pack.
2. Nested model entries contain FF2 0x1050 SGD records.
3. Outer PK2 entry 1 contains the TIM2 texture pack.
4. Later SGDs lacking coordinate tables reuse the first model's skeleton.

The parser path is `pz_core/ff2/pz_mdl_ff2.py`,
`pz_core/ff2/pz_sgd_ff2.py` and `pz_core/ff2/pz_tim2_ff2.py`. FF2 PS2 `.mdl`
is distinct from the FF1 PS2 `.mdl` and FF2 Xbox `.mdl`.

### `.pk2`

There are 504 FF2 `.pk2` files, including camera and room data. In the observed
FF2 character path, PK2 is the package framing around nested model or texture
content. FF2 room PK2s contain SGD and GS texture-transfer information used by
the FF2-specific path. Do not apply the FF1 linked-entry PK2 reader or FF2
Xbox's absolute-offset directory rules solely based on this extension.

The detailed FF2 texture/color note uses `rch00.pk2` and `rch00Mono.pk2` to
distinguish ordinary `TRI2` uploads from `MonotoneTRI2` palette uploads and
documents the identity of material texture state (including TEX0/CLUT), stored
vertex colors and GS upload data. It does not claim to emulate the PS2
framebuffer or every GS state.

## Animation and room sidecars

### `.anm`

148 FF2 animation files occur under `man/anm`. The inspected sample begins
with a count of 4 and has a `MOTN` tag at `0x40`; the project has no FF2 PS2
ANM track parser. Do not equate this with FF1 `.anm`, FF1 Xbox `.anm`, FF2
Xbox `.anm`, FF2 Wii `.anmb` or door `.mot`.

### `.mh`, `.pak`, `.zld`

The 58 `.mh` files are small room companions: the common sample is 1,088 bytes
and begins with a count of 2 and a repeated integer/offset table. The 58 `.pak`
files occur under `room/pzb`; they have a one-entry count-style wrapper and
contain a `pzb\0` tag at `0x20`. The 58 `.zld` files begin `zld\0` and carry a
declared-looking size word. These suffixes and observed headers are
documented, but no complete field semantics or reader is present here.

### `.mot`

The one `door_anim.mot` file begins with a count-style prefix and `MOTN` at
`0x20`. The matching FF2 Xbox file is the same size and byte-identical in this
collection. That single pair does not establish that all game-specific
consumers share the same animation schema.

### `.txt`

Seven plain-text files occur in the tree. Their encoding and content are
file-specific; they are not binary model data.

## Scope cautions

FF2 PS2 has eight suffixes and 909 supplied files. Only the character MDL
flow and selected room texture/vertex-color structures have detailed
references. The auxiliary room and animation formats remain observations,
not a complete reverse-engineered schema. This documentation does not modify
the existing FF2 parser path.
