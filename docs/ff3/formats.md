# Fatal Frame 3 PS2 asset formats

This index scopes the 19,625 files in the supplied FF3 tree. It links the
implemented model-container and texture paths to their references and keeps
unresolved sidecars explicitly observational. See the
[cross-game source-asset atlas](../source-asset-formats.md) for the full
24,625-file census and the reason identical suffixes must be scoped by game.

## Format inventory

| Extension | Count | Role and evidence | Reference |
|---|---:|---|---|
| `.shp` | 24 | Character shape packages beginning `PK4\0`; contain nested PK4 archives. | [PK4/SGD containers](pk4-format.md) |
| `.pk4` | 903 | All standalone files with this suffix in the supplied tree are empty markers. Non-empty PK4 archives occur inside `.shp` and nested entries. | [PK4/SGD containers](pk4-format.md) |
| `.sgd` | 2,388 | FF3 0x1050-family model/resource members, frequently leaves in nested PK4/MPK/TPK containers. | [PK4/SGD containers](pk4-format.md) |
| `.tm2` | 8,358 | PS2 TIM2 picture files, both standalone and extracted from texture packages. | [TIM2 pictures](tim2-format.md) |
| `.png` | 33 | Standard extracted PNG images. | [Auxiliary files](ancillary-formats.md#empty-markers-and-manifests) |
| `.bin` | 362 | Room-data members; sampled headers begin with 16 zero bytes and integer-like tables. | [Auxiliary files](ancillary-formats.md#room-data-members) |
| `.ccs` | 84 | Small files with float-like sequences; no stable signature found. | [Auxiliary files](ancillary-formats.md#other-observed-but-unresolved-sidecars) |
| `.cld` | 54 | Collision records: 16-byte header followed by fixed 48-byte records. Viewer bounds are conservative approximations. | [Auxiliary files](ancillary-formats.md#cld-collision-members) |
| `.ecb` | 6 | Begins `ecb\0`; followed by integer/count/offset-like fields. | [Auxiliary files](ancillary-formats.md#camera-and-control-records) |
| `.evec` | 23 | Begins `EVEC`; small integers and float-like vector records. | [Auxiliary files](ancillary-formats.md#camera-and-control-records) |
| `.pob` | 149 | Begins `pob\0`; room object/placement-related location and float-like data. | [Auxiliary files](ancillary-formats.md#room-data-members) |
| `.pzb` | 250 | Begins `pzb\0`; count/offset-like values followed by float-like data. | [Auxiliary files](ancillary-formats.md#room-data-members) |
| `.rcb` | 112 | Begins `rcb\0`; size/count/offset-like words and embedded identifiers. | [Auxiliary files](ancillary-formats.md#room-data-members) |
| `.bmd` | 1,967 | Begins `BMD\0`; found in furniture motion/asset packages. | [Auxiliary files](ancillary-formats.md#furniture-members) |
| `.rmd` | 1,967 | Begins `RMD\0`; some samples are only a 16-byte header. | [Auxiliary files](ancillary-formats.md#furniture-members) |
| `.tmpflags` | 2,395 | 16- or 20-byte temporary-state records in the supplied samples. | [Auxiliary files](ancillary-formats.md#other-observed-but-unresolved-sidecars) |
| `.zld` | 127 | Begins `zld\0`; FF3 room/object data, separate from FF2/FF2 Xbox ZLD sidecars. | [Auxiliary files](ancillary-formats.md#room-data-members) |
| `.phf` | 238 | All files are empty placeholders in extracted room object-pack trees. | [Auxiliary files](ancillary-formats.md#empty-markers-and-manifests) |
| `.___________` | 2 | Unusual underscore-only suffix; one four-byte sample contains `]rif`, one is empty. | [Auxiliary files](ancillary-formats.md#empty-markers-and-manifests) |
| `<no extension>` | 8 | All are empty start/end markers, including `VCI_START` and `ROOM_LABEL_END`. | [Auxiliary files](ancillary-formats.md#empty-markers-and-manifests) |
| `.txt` | 175 | Plain text; includes comma-separated `__phf_meta.txt` entry manifests. | [Auxiliary files](ancillary-formats.md#empty-markers-and-manifests) |

## Reading a model

The viewer recursively unpacks PK4 entries and parses leaf entries typed
`sgd`, merging model components that share a skeleton. The format page
documents the observed archive framing and the conservative FF3 SGD reader
scope. Some terminal SGD indices in character packages are collision or
non-renderable components and are excluded by index in the current model
assembly path; this is not a general SGD file-format rule.

Texture pictures use the FF3 TIM2 decoder. Both model and texture details,
including accepted signatures, boundaries and known unsupported cases, are
described in their respective references.

## Evidence limits

The counts are for the supplied extracted source tree, including zero-byte
placeholders. The viewer's presence of a helper for a file type does not mean
the original game semantics are fully decoded: CLD collision primitives are
reduced to conservative boxes, and the auxiliary room, furniture, camera and
temporary-state formats remain only partly characterized. No claim here
extends FF3 observations to other games or ports.
