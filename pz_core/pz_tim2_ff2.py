"""Strict FF2 TIM2/TM2 entry point.

FF2-specific TIM2/TM2 differences have not been verified from assets here.
The decoder is intentionally delegated to the shared recovered decoder, while
this module provides an explicit FF2 boundary and a future validation hook.
"""

from .pz_tim2_ff3 import (
    adjust_ps2_alpha,
    decode_tim2 as _decode_tim2,
    render_tim2_clut_variation,
)


def validate_tim2(data):
    """Return immutable bytes after validating the TIM2 signature."""
    payload = bytes(data)
    if len(payload) < 16 or payload[:4] != b"TIM2":
        raise ValueError("FF2 TIM2/TM2 payload does not have a TIM2 signature")
    return payload


def decode_tim2(data):
    """Decode an FF2 TIM2/TM2 payload via the shared compatible decoder."""
    return _decode_tim2(validate_tim2(data))


decode_tm2 = decode_tim2

__all__ = [
    "adjust_ps2_alpha",
    "decode_tim2",
    "decode_tm2",
    "render_tim2_clut_variation",
    "validate_tim2",
]
