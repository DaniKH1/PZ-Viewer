"""Small GS VRAM emulator for FF1 room TRI2 texture uploads.

The room TRI2 stream contains GS uploads rather than TIM2 files.  This module
keeps the emulation deliberately local to PK2 loading and follows the address
functions used by Obscura/MikuPan.
"""

import struct
from PIL import Image

VRAM_SIZE = 4 * 1024 * 1024
MAX_TRANSFER_DIMENSION = 2048
MAX_IMAGE_PIXELS = 4 * 1024 * 1024
PSMCT32 = 0
PSMT8 = 19
PSMT4 = 20

_BLOCK32 = (
    0, 1, 4, 5, 16, 17, 20, 21, 2, 3, 6, 7, 18, 19, 22, 23,
    8, 9, 12, 13, 24, 25, 28, 29, 10, 11, 14, 15, 26, 27, 30, 31,
)
_COL32 = (0, 1, 4, 5, 8, 9, 12, 13, 2, 3, 6, 7, 10, 11, 14, 15)
_COL8 = (
    0, 4, 16, 20, 32, 36, 48, 52, 2, 6, 18, 22, 34, 38, 50, 54,
    8, 12, 24, 28, 40, 44, 56, 60, 10, 14, 26, 30, 42, 46, 58, 62,
    33, 37, 49, 53, 1, 5, 17, 21, 35, 39, 51, 55, 3, 7, 19, 23,
    41, 45, 57, 61, 9, 13, 25, 29, 43, 47, 59, 63, 11, 15, 27, 31,
    96, 100, 112, 116, 64, 68, 80, 84, 98, 102, 114, 118, 66, 70, 82, 86,
    104, 108, 120, 124, 72, 76, 88, 92, 106, 110, 122, 126, 74, 78, 90, 94,
    65, 69, 81, 85, 97, 101, 113, 117, 67, 71, 83, 87, 99, 103, 115, 119,
    73, 77, 89, 93, 105, 109, 121, 125, 75, 79, 91, 95, 107, 111, 123, 127,
    128, 132, 144, 148, 160, 164, 176, 180, 130, 134, 146, 150, 162, 166, 178, 182,
    136, 140, 152, 156, 168, 172, 184, 188, 138, 142, 154, 158, 170, 174, 186, 190,
    161, 165, 177, 181, 129, 133, 145, 149, 163, 167, 179, 183, 131, 135, 147, 151,
    169, 173, 185, 189, 137, 141, 153, 157, 171, 175, 187, 191, 139, 143, 155, 159,
    224, 228, 240, 244, 192, 196, 208, 212, 226, 230, 242, 246, 194, 198, 210, 214,
    232, 236, 248, 252, 200, 204, 216, 220, 234, 238, 250, 254, 202, 206, 218, 222,
    193, 197, 209, 213, 225, 229, 241, 245, 195, 199, 211, 215, 227, 231, 243, 247,
    201, 205, 217, 221, 233, 237, 249, 253, 203, 207, 219, 223, 235, 239, 251, 255,
)

_COL4 = (
    0, 8, 32, 40, 64, 72, 96, 104, 2, 10, 34, 42, 66, 74, 98, 106,
    4, 12, 36, 44, 68, 76, 100, 108, 6, 14, 38, 46, 70, 78, 102, 110,
    16, 24, 48, 56, 80, 88, 112, 120, 18, 26, 50, 58, 82, 90, 114, 122,
    20, 28, 52, 60, 84, 92, 116, 124, 22, 30, 54, 62, 86, 94, 118, 126,
    65, 73, 97, 105, 1, 9, 33, 41, 67, 75, 99, 107, 3, 11, 35, 43,
    69, 77, 101, 109, 5, 13, 37, 45, 71, 79, 103, 111, 7, 15, 39, 47,
    81, 89, 113, 121, 17, 25, 49, 57, 83, 91, 115, 123, 19, 27, 51, 59,
    85, 93, 117, 125, 21, 29, 53, 61, 87, 95, 119, 127, 23, 31, 55, 63,
    192, 200, 224, 232, 128, 136, 160, 168, 194, 202, 226, 234, 130, 138, 162, 170,
    196, 204, 228, 236, 132, 140, 164, 172, 198, 206, 230, 238, 134, 142, 166, 174,
    208, 216, 240, 248, 144, 152, 176, 184, 210, 218, 242, 250, 146, 154, 178, 186,
    212, 220, 244, 252, 148, 156, 180, 188, 214, 222, 246, 254, 150, 158, 182, 190,
    129, 137, 161, 169, 193, 201, 225, 233, 131, 139, 163, 171, 195, 203, 227, 235,
    133, 141, 165, 173, 197, 205, 229, 237, 135, 143, 167, 175, 199, 207, 231, 239,
    145, 153, 177, 185, 209, 217, 241, 249, 147, 155, 179, 187, 211, 219, 243, 251,
    149, 157, 181, 189, 213, 221, 245, 253, 151, 159, 183, 191, 215, 223, 247, 255,
    256, 264, 288, 296, 320, 328, 352, 360, 258, 266, 290, 298, 322, 330, 354, 362,
    260, 268, 292, 300, 324, 332, 356, 364, 262, 270, 294, 302, 326, 334, 358, 366,
    272, 280, 304, 312, 336, 344, 368, 376, 274, 282, 306, 314, 338, 346, 370, 378,
    276, 284, 308, 316, 340, 348, 372, 380, 278, 286, 310, 318, 342, 350, 374, 382,
    321, 329, 353, 361, 257, 265, 289, 297, 323, 331, 355, 363, 259, 267, 291, 299,
    325, 333, 357, 365, 261, 269, 293, 301, 327, 335, 359, 367, 263, 271, 295, 303,
    337, 345, 369, 377, 273, 281, 305, 313, 339, 347, 371, 379, 275, 283, 307, 315,
    341, 349, 373, 381, 277, 285, 309, 317, 343, 351, 375, 383, 279, 287, 311, 319,
    448, 456, 480, 488, 384, 392, 416, 424, 450, 458, 482, 490, 386, 394, 418, 426,
    452, 460, 484, 492, 388, 396, 420, 428, 454, 462, 486, 494, 390, 398, 422, 430,
    464, 472, 496, 504, 400, 408, 432, 440, 466, 474, 498, 506, 402, 410, 434, 442,
    468, 476, 500, 508, 404, 412, 436, 444, 470, 478, 502, 510, 406, 414, 438, 446,
    385, 393, 417, 425, 449, 457, 481, 489, 387, 395, 419, 427, 451, 459, 483, 491,
    389, 397, 421, 429, 453, 461, 485, 493, 391, 399, 423, 431, 455, 463, 487, 495,
    401, 409, 433, 441, 465, 473, 497, 505, 403, 411, 435, 443, 467, 475, 499, 507,
    405, 413, 437, 445, 469, 477, 501, 509, 407, 415, 439, 447, 471, 479, 503, 511,
)


def _addr32(block, width, x, y):
    page = (block >> 5) + (y >> 5) * width + (x >> 6)
    column = (((y >> 1) & 3) << 4) + _COL32[((y & 1) << 3) | (x & 7)]
    block_id = (block & 31) + (((x & 0x3f) >> 1) & ~31) + _BLOCK32[
        (((y & 0x1f) >> 3) << 3) | ((x & 0x3f) >> 3)
    ]
    return ((page << 11) + (block_id << 6) + column) << 2 & 0x3ffffc


def _addr8(block, width, x, y):
    # PSMT8 uses 8-bit pixels in 128x64 pages.  The block permutation is the
    # same 32-block permutation as PSMCT32; the column permutation is the
    # standard GS 16x16 interleave expressed as bit lanes.
    page = (block >> 5) + (y >> 6) * max(1, width >> 1) + (x >> 7)
    bx = (x & 0x7f) >> 4
    by = (y & 0x3f) >> 4
    block_id = (block & 31) + (((x & 0x7f) >> 2) & ~31) + _BLOCK32[(by & 3) * 8 + (bx & 7)]
    column = _COL8[((y & 15) << 4) | (x & 15)]
    return (page << 13) + (block_id << 8) + column


def _addr4(block, width, x, y):
    page = (block >> 5) + (y >> 7) * max(1, width >> 1) + (x >> 7)
    block_id = (block & 31) + (((x & 0x7f) >> 2) & ~31)
    block_id += (((y & 0x7f) >> 6) << 4)
    block_id += (0, 2, 8, 10, 1, 3, 9, 11, 4, 6, 12, 14, 5, 7, 13, 15)[
        (((y & 0x3f) >> 4) & 3) * 4 + (((x & 0x7f) >> 5) & 3)
    ]
    column = _COL4[((y & 15) << 5) | (x & 31)]
    return (page << 14) + (block_id << 9) + column


class GSVram:
    def __init__(self):
        self.mem = bytearray(VRAM_SIZE)
        self.uploads = []

    def upload(self, dbp, dbw, dpsm, dsax, dsay, width, height, raw):
        if (
            width <= 0 or height <= 0
            or width > MAX_TRANSFER_DIMENSION
            or height > MAX_TRANSFER_DIMENSION
            or width * height > MAX_IMAGE_PIXELS
        ):
            return False
        if dpsm == PSMCT32:
            address = _addr32
            bpp = 4
        elif dpsm == PSMT8:
            address = _addr8
            bpp = 1
        elif dpsm == PSMT4:
            address = _addr4
            bpp = 0.5
        else:
            return False
        required_bytes = (width * height * 4 if dpsm == PSMCT32 else
                          width * height if dpsm == PSMT8 else
                          (width * height + 1) // 2)
        if len(raw) < required_bytes:
            return False
        pos = 0
        for y in range(dsay, dsay + height):
            for x in range(dsax, dsax + width):
                addr = address(dbp, dbw, x, y)
                if dpsm == PSMT4:
                    if pos // 2 >= len(raw) or addr // 2 >= len(self.mem):
                        return False
                    shift = (addr & 1) * 4
                    self.mem[addr // 2] = (self.mem[addr // 2] & ~(0xf << shift)) | (
                        ((raw[pos // 2] >> ((pos & 1) * 4)) & 0xf) << shift
                    )
                    pos += 1
                else:
                    if addr + bpp > len(self.mem) or pos + int(bpp) > len(raw):
                        return False
                    self.mem[addr:addr + int(bpp)] = raw[pos:pos + int(bpp)]
                    pos += int(bpp)
        self.uploads.append((dbp, dpsm, dsax, dsay, width, height))
        return True

    def image(self, tbp0, tbw, psm, tw, th, cbp=0, csa=0):
        width, height = 1 << tw, 1 << th
        if (width > MAX_TRANSFER_DIMENSION or height > MAX_TRANSFER_DIMENSION
                or width * height > MAX_IMAGE_PIXELS):
            return None
        out = Image.new("RGBA", (width, height))
        pix = out.load()
        raw_alpha_max = 0
        for y in range(height):
            for x in range(width):
                if psm == PSMCT32:
                    addr = _addr32(tbp0, tbw, x, y)
                    if addr + 4 > len(self.mem):
                        return None
                    r, g, b, a = self.mem[addr:addr + 4]
                elif psm == PSMT8:
                    addr = _addr8(tbp0, tbw, x, y)
                    if addr >= len(self.mem):
                        return None
                    idx = self.mem[addr]
                    # CSM1 palette addressing is not linear: MikuPan maps
                    # the 8-bit index into the 16x16 CLUT swizzle.
                    cy = (idx & 0xe0) >> 4
                    cx = idx & 7
                    if idx & 8:
                        cy += 1
                    if idx & 0x10:
                        cx += 8
                    ca = _addr32(cbp, tbw, cx, cy)
                    r, g, b, a = self.mem[ca:ca + 4]
                elif psm == PSMT4:
                    addr = _addr4(tbp0, tbw, x, y)
                    idx = (self.mem[addr // 2] >> ((addr & 1) * 4)) & 0xf
                    cy = ((idx >> 3) & 1) + (csa & 0xe)
                    cx = (idx & 7) + ((csa & 1) << 3)
                    ca = _addr32(cbp, tbw, cx, cy)
                    r, g, b, a = self.mem[ca:ca + 4]
                else:
                    return None
                raw_alpha_max = max(raw_alpha_max, a)
                # PS2 stores alpha on a 0..128 scale. MikuPan's renderer
                # expands the lower half and treats 128+ as fully opaque.
                pix[x, y] = (r, g, b, (a << 1) if a <= 127 else 255)
        # Some FF1 room uploads leave the alpha byte unset for an otherwise
        # valid opaque palette. Do not turn that entire image invisible;
        # preserve mixed/real alpha images unchanged.
        if raw_alpha_max == 0:
            out.putalpha(255)
        return out


def _iter_tri2_records(data, start, end):
    pos = start
    while pos + 112 <= end:
        direct = struct.unpack_from("<I", data, pos + 12)[0]
        size = direct & 0xffff
        header_is_image = data[pos:pos + 12] == b"\x00" * 12
        if size <= 0:
            break
        bitblt = struct.unpack_from("<Q", data, pos + 32)[0]
        trxpos = struct.unpack_from("<Q", data, pos + 48)[0]
        trxreg = struct.unpack_from("<Q", data, pos + 64)[0]
        dpsm = (bitblt >> 56) & 0x3f
        transfer_width = trxreg & 0xfff
        transfer_height = (trxreg >> 32) & 0xfff
        header_is_clut = (
            dpsm == PSMCT32 and transfer_width == 16
            and transfer_height == 16 and size <= 4
        )
        pixels = (trxreg & 0xfff) * ((trxreg >> 32) & 0xfff)
        if (not (header_is_image or header_is_clut)
                or dpsm not in (PSMCT32, PSMT8, PSMT4)
                or pixels <= 0
                or (trxreg & 0xfff) > MAX_TRANSFER_DIMENSION
                or ((trxreg >> 32) & 0xfff) > MAX_TRANSFER_DIMENSION
                or pixels > MAX_IMAGE_PIXELS):
            break
        nbytes = (pixels * 4 if dpsm == PSMCT32 else
                  (pixels + 1) // 2 if dpsm == PSMT4 else pixels)
        # SGDTRI2FILEHEADER ends after the second GIF tag.  Obscura's
        # UploadGsTexture takes the image bytes from &header[1], i.e. 112
        # bytes after the VIF/DMA record start; the GS register packet at
        # +96 is metadata, not texture pixels.
        image_start = pos + 112
        if image_start + nbytes > end:
            break
        raw = data[image_start:image_start + nbytes]
        yield ((bitblt >> 32) & 0x3fff, (bitblt >> 48) & 0x3f,
               dpsm, trxpos & 0x7ff, (trxpos >> 48) & 0x7ff,
               trxreg & 0xfff, (trxreg >> 32) & 0xfff, raw)
        next_pos = image_start + nbytes
        # TRI2 records may include alignment/padding between IMAGE payloads.
        # Find the next valid VIF/DMA header instead of treating DIRECT.size
        # as the image record size (it is only the VIF DIRECT packet size).
        while next_pos + 112 <= end:
            next_direct = struct.unpack_from("<I", data, next_pos + 12)[0]
            next_size = next_direct & 0xffff
            next_header_is_image = data[next_pos:next_pos + 12] == b"\x00" * 12
            next_bits = struct.unpack_from("<Q", data, next_pos + 32)[0]
            next_reg = struct.unpack_from("<Q", data, next_pos + 64)[0]
            next_psm = (next_bits >> 56) & 0x3f
            next_w = next_reg & 0xfff
            next_h = (next_reg >> 32) & 0xfff
            next_header_is_clut = (
                next_psm == PSMCT32 and next_w == 16 and next_h == 16
                and next_size <= 4
            )
            if (next_size and next_header_is_image
                    and next_psm in (PSMCT32, PSMT8, PSMT4)
                    and next_w and next_h
                    and next_w <= MAX_TRANSFER_DIMENSION
                    and next_h <= MAX_TRANSFER_DIMENSION
                    and next_w * next_h <= MAX_IMAGE_PIXELS
                    and next_pos + 112 + (
                        next_w * next_h * (4 if next_psm == PSMCT32 else
                                           1 if next_psm == PSMT8 else 0.5)
                    ) <= end):
                break
            if next_size and next_header_is_clut:
                break
            next_pos += 16
        pos = next_pos


def reconstruct_sgd_textures(data, materials, diagnostics=None):
    """Upload structured FF1 GS records and return exact TBP0 images.

    FF1 PK2 texture payloads are headerless GS IMAGE uploads.  Their format
    comes from the surrounding BITBLT/TRXREG metadata and the material TEX0
    word, rather than a TIM2 signature.
    """
    vram = GSVram()
    detected = []
    for off in range(0, len(data) - 16, 16):
        pn, category = struct.unpack_from("<II", data, off)
        if category not in (10, 13) or pn < 80 or off + pn > len(data):
            continue
        count = struct.unpack_from("<I", data, off + 8)[0]
        end = min(len(data), off + pn)
        # The TRI2 payload is stored after the 16-byte process-unit header
        # and its texture padding field.  The padding varies between rooms;
        # fixed offsets (80/112) miss valid uploads such as r001_fusuma.
        padding = struct.unpack_from("<I", data, off + 12)[0]
        payload_start = off + 16 + padding
        records = (
            list(_iter_tri2_records(data, payload_start, end))
            if payload_start < end else []
        )
        if not records or count and len(records) < min(count, 1):
            continue
        for record in records:
            vram.upload(*record)
            detected.append({
                "block_offset": off,
                "tbp0": record[0],
                "tbw": record[1],
                "psm": record[2],
                "width": record[5],
                "height": record[6],
                "payload_bytes": len(record[7]),
            })
    result = {}
    image_cache = {}
    for mat in materials:
        tex0 = getattr(mat, "tex0", 0) or getattr(mat, "tex0_low", 0)
        if not tex0:
            continue
        tbp0 = tex0 & 0x3fff
        tbw = (tex0 >> 14) & 0x3f
        psm = (tex0 >> 20) & 0x3f
        tw = (tex0 >> 26) & 0xf
        th = (tex0 >> 30) & 0xf
        cbp = (tex0 >> 37) & 0x3fff
        # TEX0 packs CPSM at bits 51-54, CSM at 55, and CSA at 56-60.
        # CSA is only consumed by PSMT4 CLUT addressing.
        csa = (tex0 >> 56) & 0x1f
        image_key = (tbp0, tbw, psm, tw, th, cbp, csa)
        if image_key not in image_cache:
            image_cache[image_key] = vram.image(tbp0, tbw, psm, tw, th, cbp, csa)
        image = image_cache[image_key]
        if image:
            result[tbp0] = image
    if diagnostics is not None:
        material_by_tbp0 = {}
        for mat in materials:
            tex0 = getattr(mat, "tex0", 0) or getattr(mat, "tex0_low", 0)
            if tex0:
                material_by_tbp0.setdefault(tex0 & 0x3FFF, []).append({
                    "index": getattr(mat, "index", -1),
                    "name": getattr(mat, "name", ""),
                    "psm": (tex0 >> 20) & 0x3F,
                    "width": 1 << ((tex0 >> 26) & 0xF),
                    "height": 1 << ((tex0 >> 30) & 0xF),
                    "cbp": (tex0 >> 37) & 0x3FFF,
                    "csa": (tex0 >> 56) & 0x1F,
                })
        diagnostics.update({
            "detected_uploads": detected,
            "decoded_tbp0": sorted(result),
            "material_associations": [
                {"tbp0": tbp0, "materials": material_by_tbp0.get(tbp0, [])}
                for tbp0 in sorted({item["tbp0"] for item in detected})
                if tbp0 in material_by_tbp0
            ],
        })
    return result, vram.uploads
