# FF2 Xbox `.mdl` character packages

Reader: `pz_core/ff2x/pz_model_ff2x.py`, `parse_ff2x_model()`.

An `.mdl` is a character container, not a standalone geometry stream. The
matching same-stem `.ppd` supplies the vertex bytes. For example,
`ch000.mdl` must be opened together with `ch000.ppd`.

## Observed container framing

The inspected character files use a two-entry `pk3\0` outer directory. Their
nested model section is observed as a `pk1\0` region. The implementation
searches the bounded model region, starting at `0x40` and ending at the
`xvd\0` texture section when present, for 4-byte-aligned `0x1070` model
headers. Candidates are retained only when their header and block table fit
inside the region.

This describes the observed files and current reader strategy; fields in the
outer container that are not used to validate a model record are not assigned
additional semantics here.

## Model record

Each retained record starts with ten little-endian 32-bit words:

| Offset | Field | Reader interpretation |
|---:|---|---|
| `0x00` | Version | `0x1070` |
| `0x04` | Unknown | Preserved, not interpreted |
| `0x08` | Unknown | Preserved, not interpreted |
| `0x0C` | Material count | Number of 144-byte material records |
| `0x10` | Vertex-buffer offset | Relative to this model record |
| `0x14` | Vertex-buffer size | Exact required PPD payload length |
| `0x18` | Coordinate-table offset | Relative to this record |
| `0x1C` | Material-table offset | Relative to this record |
| `0x20` | Source-pool descriptor offset | Zero when absent |
| `0x24` | Block count | Number of block-offset table entries; the reader derives `bone_count = block_count - 1` |
| `0x28` | Block-offset table | `block_count` relative 32-bit offsets |

The file's 0x1070 record is adapted to the shared bounded Xbox geometry
reader's 0x1060 entry representation. This is an internal compatibility
adapter; the source file remains 0x1070 and is not rewritten.

Character `.mdl` files can contain many records. Their successfully parsed
meshes, bones and materials are combined into one viewer scene, with indices
adjusted while merging. Texture archives are associated with records in their
container order.

## Geometry details

Coordinate records, material records, source pools and command blocks follow
the supported Xbox 0x1060 subset described in [the `.pk2` geometry section](pk2-format.md#0x1070-model-record-and-geometry).
Material records are 144 bytes; the reader extracts diffuse, ambient,
specular and emission values, shininess, texture resource index, name and
flags. Unknown fields remain diagnostic data.

The reader checks model and PPD bounds, command sizes, index ranges, vertex
declarations and source mappings before accepting a record. A package with no
renderable supported geometry is rejected.

The observed character stream repeats a small untextured category-1 helper
tetrahedron with four triangles and one vertex at the origin. Only that exact
structural pattern is suppressed from rendering; other untextured meshes are
kept.

## Related references

- [`.ppd` sidecar and vertex buffers](ppd-format.md)
- [XPR0 textures in the model container](xpr0-format.md)
- [Format overview and known limits](README.md)
