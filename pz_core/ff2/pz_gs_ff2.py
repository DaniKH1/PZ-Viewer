"""FF2 PS2 TRI2 textures: explicit colour/monochrome, full TEX0 identity.

FF2 SGD 0x1050 uses the same *hardware* GIF transfers as FF1, but has its
own process-chain validation and palette policy.  The shared GIF packet
reader and GS address functions are reused unchanged, not the FF1 loader.
See docs/ff2/textures-and-vertex-colors.md for offsets in the supplied assets.
"""
import os
import struct

from pz_core.ff1.pz_gs_ff1 import _gif_transfers, FF1TextureError
from pz_core.common.pz_gs_vram import GSVram, PSMCT32, PSMT8, PSMT4


class FF2FormatError(ValueError):
    """A bounded FF2 SGD/GIF record is truncated, invalid or unsupported."""


def need(data, offset, size, context):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise FF2FormatError(
            f'FF2 {context} at 0x{offset:x}: need {size} bytes; file has {len(data)}')


def sgd_chains(data):
    """Yield (block index, [(offset, end, category)]) without scanning pixels.

    Entries shared by different block lists remain in their respective lists;
    texture uploads are deduplicated separately. Relative next pointers are
    positive; last units end at the next block head (or the end of the file).
    """
    need(data, 0, 24, 'SGD header')
    version, _, _, _, _, _, _, count = struct.unpack_from('<IBBHIIII', data)
    if version != 0x1050 or not 1 <= count <= 4096:
        raise FF2FormatError(f'FF2 SGD at 0x0: version 0x{version:x}, block count {count}')
    need(data, 24, 4 * count, 'block table')
    heads = struct.unpack_from(f'<{count}I', data, 24)
    for block, head in enumerate(heads):
        chain, seen, offset = [], set(), head
        while offset:
            if offset in seen:
                raise FF2FormatError(f'FF2 process cycle at 0x{offset:x}')
            need(data, offset, 16, f'block {block} process header')
            seen.add(offset)
            relative, category = struct.unpack_from('<II', data, offset)
            end = offset + relative if relative else min(
                (value for value in heads if value > offset), default=len(data))
            if relative and (relative < 16 or end + 16 > len(data)):
                raise FF2FormatError(f'FF2 next process at 0x{offset:x}: invalid +0x{relative:x}')
            chain.append((offset, end, category))
            offset = end if relative else 0
        yield block, chain


def material_tex0(material):
    return int(getattr(material, 'tex0', 0) or getattr(material, 'tex0_low', 0))


class FF2TextureMap(dict):
    """Compatibility TBP0 lookup plus authoritative full TEX0/CLUT lookup."""
    def __init__(self):
        super().__init__()
        self.by_tex0 = {}

    def for_material(self, material):
        return self.by_tex0.get(material_tex0(material))


def bind_textures(images, materials, textures=None):
    textures = [] if textures is None else textures
    for material in materials:
        image = images.for_material(material)
        if image is None:
            continue
        index = next((i for i, other in enumerate(textures) if other is image), None)
        if index is None:
            index = len(textures)
            textures.append(image)
        material.texture_index = index
    return textures


def reconstruct_sgd_textures(data, materials, diagnostics=None, *,
                             monochrome=False, preserve_zero_alpha=True):
    """Read one SGD/path or a list of SGD streams sharing a room's GS memory.

    Normal colour never applies category 13. Monochrome is an explicit option,
    not a filename heuristic; its CLUTs overlay all ordinary category-10 data.
    Alpha is preserved, including fully black shadow/cutout texture RGB.
    """
    if isinstance(data, (str, os.PathLike)):
        with open(data, 'rb') as stream:
            data = stream.read()
    streams = list(data) if isinstance(data, (tuple, list)) else [data]
    vram, result = GSVram(), FF2TextureMap()
    records, skipped, selected, warnings = [], [], [], []
    for entry, payload in enumerate(streams):
        payload = bytes(payload)
        seen = set()
        for block, chain in sgd_chains(payload):
            for offset, end, category in chain:
                if offset in seen:
                    continue
                seen.add(offset)
                if category == 13 and not monochrome:
                    skipped.append({'entry_index': entry, 'block_offset': offset,
                                    'category': 13, 'reason': 'alternative monochrome CLUTs'})
                if category == 10 or (category == 13 and monochrome):
                    selected.append((category, entry, block, offset, end, payload))
    selected.sort(key=lambda item: item[0])
    for category, entry, block, offset, end, payload in selected:
        count, padding = struct.unpack_from('<II', payload, offset + 8)
        if count > 4096 or padding > end - offset - 16:
            raise FF2FormatError(f'FF2 TRI2 count/padding at 0x{offset:x} exceeds unit')
        cursor = offset + 16 + padding
        for index in range(count):
            if cursor + 16 > end:
                raise FF2FormatError(f'FF2 TRI2 {index}: missing VIF header at 0x{cursor:x}')
            word = struct.unpack_from('<I', payload, cursor + 12)[0]
            opcode, qwords = (word >> 24) & 127, word & 65535
            stop = cursor + 16 + qwords * 16
            if opcode not in (0x50, 0x51) or not qwords or stop > end:
                raise FF2FormatError(f'FF2 DIRECT 0x{word:x} at 0x{cursor:x} exceeds unit')
            try:
                for image_offset, record in _gif_transfers(payload, cursor + 16, stop):
                    if not vram.upload(*record):
                        raise FF2FormatError(f'FF2 unsupported GS upload at 0x{image_offset:x}')
                    records.append({'entry_index': entry, 'block': block,
                                    'block_offset': offset, 'category': category,
                                    'tri2_index': index, 'image_offset': image_offset,
                                    'tbp0': record[0], 'tbw': record[1], 'psm': record[2],
                                    'dsax': record[3], 'dsay': record[4],
                                    'width': record[5], 'height': record[6],
                                    'payload_bytes': len(record[7])})
            except FF1TextureError as exc:
                raise FF2FormatError(f'FF2 GIF in process 0x{offset:x}: {exc}') from exc
            cursor = stop
    if records:
        for material in materials:
            tex0 = material_tex0(material)
            if not tex0 or tex0 in result.by_tex0:
                continue
            psm, cpsm, csm = (tex0 >> 20) & 63, (tex0 >> 51) & 15, (tex0 >> 55) & 1
            if psm in (PSMT8, PSMT4) and (cpsm != PSMCT32 or csm):
                warnings.append(f'TEX0 0x{tex0:016x}: unsupported CLUT CPSM={cpsm}, CSM={csm}')
                continue
            try:
                image = vram.image(tex0 & 16383, (tex0 >> 14) & 63, psm,
                                   (tex0 >> 26) & 15, (tex0 >> 30) & 15,
                                   (tex0 >> 37) & 16383, (tex0 >> 56) & 31,
                                   preserve_zero_alpha=preserve_zero_alpha)
            except (IndexError, ValueError) as exc:
                raise FF2FormatError(f'FF2 invalid TEX0 0x{tex0:016x}: {exc}') from exc
            if image is None:
                warnings.append(f'TEX0 0x{tex0:016x}: unsupported/invalid surface')
                continue
            result.by_tex0[tex0] = image
            result.setdefault(tex0 & 16383, image)
    if diagnostics is not None:
        diagnostics.update({'decoder': 'ff2_structured_tri2',
                            'palette_mode': 'monochrome' if monochrome else 'colour',
                            'detected_uploads': records,
                            'skipped_monochrome_blocks': skipped,
                            'decoded_tbp0': sorted(result),
                            'decoded_tex0': [f'0x{key:016x}' for key in sorted(result.by_tex0)],
                            'warnings': warnings})
    return result, vram.uploads
