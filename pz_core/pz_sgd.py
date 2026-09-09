"""Compatibility facade for the legacy FF1 SGD parser import path."""

from .pz_sgd_ff1 import (
    SGDMaterial,
    SGDMesh,
    SGDBone,
    SGDModel,
    apply_lighting_to_vertex,
    fix_colors,
    fix_uv,
    get_next_unpack,
    merge_sgd_models,
    normalize_mesh_normals,
    parse_lit_lights,
    parse_sgd,
    set_triangle_indices,
    transform_norm,
    transform_pos,
)

__all__ = [
    "SGDMaterial",
    "SGDMesh",
    "SGDBone",
    "SGDModel",
    "apply_lighting_to_vertex",
    "fix_colors",
    "fix_uv",
    "get_next_unpack",
    "merge_sgd_models",
    "normalize_mesh_normals",
    "parse_lit_lights",
    "parse_sgd",
    "set_triangle_indices",
    "transform_norm",
    "transform_pos",
]
