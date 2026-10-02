"""FF1 TRI2 colour reconstruction, separate from the legacy FF2 GS route.

Category 10 contains the normal VRAM image. Category 13 (MonotoneTRI2)
contains alternative grey CLUT uploads, NOT additional missing textures.
Only an explicit ``monochrome=True`` request applies that second stream.

References: Obscura ff1, Model::SgSortUnitPrim / HandleTri2DataBlock and
GsTexture.cpp. Addressing is delegated to the unchanged GSVram primitive.
See docs/ff1/color-and-pose.md for byte offsets and sample checksums.
"""

import os
import struct

from pz_core.common.pz_gs_vram import (
    GSVram, PSMCT32, PSMT4, PSMT8,
    MAX_TRANSFER_DIMENSION, MAX_IMAGE_PIXELS,
)


class FF1TextureError(ValueError):
    """An FF1 process chain or GIF transfer is malformed/unsupported."""


def _need(data, offset, size, context):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise FF1TextureError(f"{context} at 0x{offset:x}: need {size} bytes, file has {len(data)}")


def iter_process_units(data):
    """Follow the SGD's process chains, including block zero, once per unit.

    Return (block, offset, end, category). Do not scan arbitrary pixel bytes
    for numbers that happen to resemble a texture header.
    """
    _need(data, 0, 24, "SGD header")
    version, _, _, _, _, _, _, count = struct.unpack_from("<IBBHIIII", data)
    if version != 0x1050 or not 1 <= count <= 4096:
        raise FF1TextureError(f"Not an FF1 SGD: version 0x{version:x}, {count} blocks")
    _need(data, 24, count * 4, "SGD block table")
    heads = struct.unpack_from(f"<{count}I", data, 24)
    visited = set()
    for block, offset in enumerate(heads):
        while offset:
            if offset in visited:
                break  # Several blocks can share a chain tail.
            _need(data, offset, 16, f"SGD block {block} process header")
            visited.add(offset)
            relative, category = struct.unpack_from("<II", data, offset)
            end = offset + relative if relative else len(data)
            if relative and (relative < 16 or end + 16 > len(data)):
                raise FF1TextureError(f"SGD block {block} at 0x{offset:x}: invalid next offset 0x{relative:x}")
            yield block, offset, end, category
            offset = end if relative else 0


def _image_byte_count(bitblt, trxreg, offset):
    psm = (bitblt >> 56) & 0x3F
    width, height = trxreg & 0xFFF, (trxreg >> 32) & 0xFFF
    if (not width or not height or width > MAX_TRANSFER_DIMENSION
            or height > MAX_TRANSFER_DIMENSION or width * height > MAX_IMAGE_PIXELS):
        raise FF1TextureError(f"GIF IMAGE at 0x{offset:x}: invalid dimensions {width}x{height}")
    if psm not in (PSMCT32, PSMT8, PSMT4):
        raise FF1TextureError(f"GIF IMAGE at 0x{offset:x}: unsupported upload PSM {psm}")
    pixels = width * height
    return pixels * 4 if psm == PSMCT32 else pixels if psm == PSMT8 else (pixels + 1) // 2


def _gif_transfers(data, start, end):
    """Read GIF A+D / IMAGE packets within one bounded VIF DIRECT.

    A palette inside an indexed TRI2 is a bare sceGsLoadImage (96 bytes),
    not another 112-byte TRI2 header. Following GIF tags handles both forms.
    IMAGE payload sizes come from NLOOP; DIRECT bounds the entire packet.
    """
    pos = start
    registers = {}
    pending = bytearray()
    while pos < end:
        if pos + 16 > end:
            raise FF1TextureError(f"Truncated GIF tag at 0x{pos:x}")
        low, descriptors = struct.unpack_from("<QQ", data, pos)
        tag_offset = pos
        pos += 16
        loops = low & 0x7FFF
        fmt = (low >> 58) & 3
        nreg = (low >> 60) & 15 or 16
        if fmt == 0:  # PACKED: each register is a 128-bit word.
            size = loops * nreg * 16
            if pos + size > end:
                raise FF1TextureError(f"Truncated GIF PACKED at 0x{tag_offset:x}")
            for index in range(loops * nreg):
                descriptor = (descriptors >> (4 * (index % nreg))) & 15
                if descriptor == 0xE:  # A+D
                    value, address = struct.unpack_from("<QQ", data, pos + index * 16)
                    if address & 0xFF in (0x50, 0x51, 0x52, 0x53):
                        if pending:
                            raise FF1TextureError(f"Transfer registers changed during IMAGE at 0x{tag_offset:x}")
                        registers[address & 0xFF] = value
            pos += size
        elif fmt == 1:  # REGLIST cannot contain A+D transfer-register writes.
            size = ((loops * nreg * 8 + 15) // 16) * 16
            if pos + size > end:
                raise FF1TextureError(f"Truncated GIF REGLIST at 0x{tag_offset:x}")
            pos += size
        else:  # IMAGE / IMAGE2
            size = loops * 16
            if pos + size > end:
                raise FF1TextureError(f"Truncated GIF IMAGE at 0x{tag_offset:x}")
            if not size:
                continue
            if not all(key in registers for key in (0x50, 0x51, 0x52, 0x53)):
                raise FF1TextureError(f"GIF IMAGE at 0x{tag_offset:x}: missing transfer registers")
            if registers[0x53] & 3:
                raise FF1TextureError(f"GIF IMAGE at 0x{tag_offset:x}: not host-to-local")
            bitblt, trxpos, trxreg = (registers[k] for k in (0x50, 0x51, 0x52))
            expected = _image_byte_count(bitblt, trxreg, tag_offset)
            pending.extend(data[pos:pos + size])
            pos += size
            if len(pending) >= expected:
                if len(pending) - expected >= 16:
                    raise FF1TextureError(f"GIF IMAGE at 0x{tag_offset:x}: payload exceeds transfer dimensions")
                # TRXPOS.DSAY is bits 48..58; DSAX is 32..42, not SSAX.
                record = ((bitblt >> 32) & 0x3FFF, (bitblt >> 48) & 0x3F,
                          (bitblt >> 56) & 0x3F, (trxpos >> 32) & 0x7FF,
                          (trxpos >> 48) & 0x7FF, trxreg & 0xFFF,
                          (trxreg >> 32) & 0xFFF, bytes(pending[:expected]))
                yield tag_offset, record
                pending.clear()
    if pending:
        raise FF1TextureError(f"Incomplete split IMAGE ending at 0x{end:x}")


class FF1TextureMap(dict):
    """Legacy TBP0 mapping plus lossless lookup for differing TEX0/CLUTs."""
    def __init__(self):
        super().__init__()
        self.by_tex0 = {}

    def for_material(self, material):
        return self.by_tex0.get(material_tex0(material))


def material_tex0(material):
    return int(getattr(material, "tex0", 0) or getattr(material, "tex0_low", 0))


def bind_textures(images, materials, textures=None):
    """Bind complete TEX0 identities, preserving distinct palette variants."""
    textures = [] if textures is None else textures
    for material in materials:
        image = images.for_material(material)
        if image is None:
            continue
        slot = next((i for i, old in enumerate(textures) if old is image), None)
        if slot is None:
            slot = len(textures)
            textures.append(image)
        material.texture_index = slot
    return textures


def reconstruct_sgd_textures(data, materials, diagnostics=None,
                             preserve_zero_alpha=False, *, monochrome=False):
    """Reconstruct FF1 normal colour; optional monochrome is explicit.

    ``data`` may be one SGD, a path to it, or a list of SGD byte strings whose
    VRAM is shared (the near/source/panel members of a room PK2). No filenames
    or sample-specific palette/bone lists participate in decoding.
    """
    if isinstance(data, (str, os.PathLike)):
        with open(data, "rb") as stream:
            data = stream.read()
    streams = list(data) if isinstance(data, (list, tuple)) else [data]
    materials = list(materials)
    vram = GSVram()
    detected, skipped, warnings = [], [], []
    selected = []
    for entry, payload in enumerate(streams):
        payload = bytes(payload)
        for block, offset, end, category in iter_process_units(payload):
            if category == 13 and not monochrome:
                skipped.append({"entry_index": entry, "block_offset": offset,
                                "category": 13, "reason": "alternative monochrome CLUTs"})
            elif category == 10 or (category == 13 and monochrome):
                selected.append((category, entry, block, offset, end, payload))
    # The monochrome palette is an overlay over the ordinary VRAM image.
    selected.sort(key=lambda item: item[0])
    for category, entry, block, offset, end, payload in selected:
        count, padding = struct.unpack_from("<II", payload, offset + 8)
        if count > 4096 or padding > end - offset - 16:
            raise FF1TextureError(f"TRI2 at 0x{offset:x}: invalid count/padding")
        pos = offset + 16 + padding
        for index in range(count):
            if pos + 16 > end:
                raise FF1TextureError(f"TRI2 {index} at 0x{pos:x}: missing VIF header")
            direct = struct.unpack_from("<I", payload, pos + 12)[0]
            opcode, qwords = (direct >> 24) & 0x7F, direct & 0xFFFF
            packet_end = pos + 16 + qwords * 16
            if opcode not in (0x50, 0x51) or not qwords or packet_end > end:
                raise FF1TextureError(f"TRI2 {index} at 0x{pos:x}: invalid/truncated DIRECT 0x{direct:x}")
            for image_offset, record in _gif_transfers(payload, pos + 16, packet_end):
                if not vram.upload(*record):
                    raise FF1TextureError(f"GS upload at 0x{image_offset:x} is outside supported VRAM")
                detected.append({"entry_index": entry, "block": block,
                                 "block_offset": offset, "category": category,
                                 "tri2_index": index, "image_offset": image_offset,
                                 "tbp0": record[0], "tbw": record[1], "psm": record[2],
                                 "dsax": record[3], "dsay": record[4],
                                 "width": record[5], "height": record[6],
                                 "payload_bytes": len(record[7])})
            pos = packet_end
    result = FF1TextureMap()
    if detected:
        for material in materials:
            tex0 = material_tex0(material)
            if not tex0 or tex0 in result.by_tex0:
                continue
            psm, cpsm, csm = (tex0 >> 20) & 0x3F, (tex0 >> 51) & 15, (tex0 >> 55) & 1
            if psm in (PSMT8, PSMT4) and (cpsm != PSMCT32 or csm):
                warnings.append(f"TEX0 0x{tex0:016x}: unsupported CLUT CPSM={cpsm}, CSM={csm}")
                continue
            image = vram.image(tex0 & 0x3FFF, (tex0 >> 14) & 63, psm,
                               (tex0 >> 26) & 15, (tex0 >> 30) & 15,
                               (tex0 >> 37) & 0x3FFF, (tex0 >> 56) & 31,
                               preserve_zero_alpha=preserve_zero_alpha)
            if image is None:
                warnings.append(f"TEX0 0x{tex0:016x}: unsupported/invalid surface")
                continue
            result.by_tex0[tex0] = image
            result.setdefault(tex0 & 0x3FFF, image)
    if diagnostics is not None:
        diagnostics.update({"decoder": "ff1_structured_tri2", "palette_mode": "monochrome" if monochrome else "colour",
                            "detected_uploads": detected, "skipped_monochrome_blocks": skipped,
                            "decoded_tbp0": sorted(result), "decoded_tex0": [f"0x{v:016x}" for v in sorted(result.by_tex0)],
                            "warnings": warnings})
    return result, vram.uploads
