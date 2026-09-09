"""Project Zero 1 texture entry point.

FF1 room PK2 files normally contain headerless GS uploads, not standalone
TIM2 pictures. Literal TIM2 fallback decoding is kept separate from FF3;
room reconstruction is provided by the MikuPan/Obscura-compatible GS module.
"""

from .pz_tim2_ff3 import (
    adjust_ps2_alpha,
    decode_tim2,
    render_tim2_clut_variation,
)
from .pz_gs_vram import reconstruct_sgd_textures

__all__ = [
    "adjust_ps2_alpha",
    "decode_tim2",
    "render_tim2_clut_variation",
    "reconstruct_sgd_textures",
]
