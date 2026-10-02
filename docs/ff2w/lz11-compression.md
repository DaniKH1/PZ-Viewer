# Nintendo LZ11 Compression Specification

## Overview

All 3D assets (`.mdlb`) and texture packages (`.ppdb`) in *Project Zero 2: Deep Crimson Butterfly* (Wii) are compressed using Nintendo's standard **LZ11** format (LZSS Type `0x11`).

LZ11 is an LZSS variant optimized for ARM (Nintendo DS/3DS) and PowerPC (GameCube/Wii). It supports variable-length back-references up to 65,809 bytes and sliding dictionary lookups up to 4,096 bytes.

---

## Header Structure

The compression header starts at offset `0x00`:

| Offset | Size | Field | Description |
| :--- | :--- | :--- | :--- |
| `0x00` | 1 byte | **Magic** | Magic identifier byte: `0x11`. |
| `0x01` | 3 bytes | **Decompressed Size** | 24-bit decompressed size (Little-Endian / Big-Endian packed). |
| `0x04` | 4 bytes | **Extended Size** | *(Optional)* If the 24-bit size is `0`, a 32-bit decompressed size is stored here. |

### Size Extraction Logic
```python
magic = data[0]
if magic != 0x11:
    raise ValueError("Invalid LZ11 magic")

decomp_size = data[1] | (data[2] << 8) | (data[3] << 16)
in_offset = 4
if decomp_size == 0:
    decomp_size = struct.unpack_from(">I", data, 4)[0]
    in_offset = 8
```

---

## Decompression Algorithm

The compressed stream is divided into chunks governed by an **8-bit flag byte**. Each bit (from MSB bit 7 down to LSB bit 0) dictates whether the following token is an uncompressed literal or a compressed back-reference.

```
Flag Byte (8 bits, MSB to LSB)
├── Bit = 0: Literal Byte (copy 1 raw byte to output)
└── Bit = 1: Compressed Back-Reference (Length + Displacement)
```

### Back-Reference Decoding Rules

When a bit is `1`, the decoder reads the next byte `b1`. The upper 4 bits `(b1 >> 4)` indicate the length category:

#### Case 1: Short Back-Reference (`(b1 >> 4) > 1`)
- Length: `(b1 >> 4) + 1` (range: $3$ to $17$ bytes).
- Displacement: `((b1 & 0x0F) << 8) | b2 + 1` (range: $1$ to $4096$ bytes).
- Bytes consumed: 2 bytes.

#### Case 2: Medium Back-Reference (`(b1 >> 4) == 0`)
- Length: `((b1 & 0x0F) << 4) | (b2 >> 4) + 17` (range: $17$ to $272$ bytes).
- Displacement: `((b2 & 0x0F) << 8) | b3 + 1` (range: $1$ to $4096$ bytes).
- Bytes consumed: 3 bytes.

#### Case 3: Extended Back-Reference (`(b1 >> 4) == 1`)
- Length: `((b1 & 0x0F) << 12) | (b2 << 4) | (b3 >> 4) + 273` (range: $273$ to $65809$ bytes).
- Displacement: `((b3 & 0x0F) << 8) | b4 + 1` (range: $1$ to $4096$ bytes).
- Bytes consumed: 4 bytes.

---

## Reference Python Implementation

```python
def decompress_lz11(data: bytes) -> bytes:
    if len(data) < 4 or data[0] != 0x11:
        return data  # Return uncompressed if no LZ11 header

    decomp_size = data[1] | (data[2] << 8) | (data[3] << 16)
    in_pos = 4
    if decomp_size == 0:
        decomp_size = struct.unpack_from(">I", data, 4)[0]
        in_pos = 8

    out = bytearray()
    while in_pos < len(data) and len(out) < decomp_size:
        flag = data[in_pos]
        in_pos += 1
        for bit in range(7, -1, -1):
            if in_pos >= len(data) or len(out) >= decomp_size:
                break
            if (flag >> bit) & 1 == 0:
                out.append(data[in_pos])
                in_pos += 1
            else:
                b1 = data[in_pos]
                in_pos += 1
                ind = b1 >> 4
                if ind > 1:
                    length = ind + 1
                    b2 = data[in_pos]
                    in_pos += 1
                    disp = (((b1 & 0x0F) << 8) | b2) + 1
                elif ind == 0:
                    b2 = data[in_pos]
                    b3 = data[in_pos + 1]
                    in_pos += 2
                    length = (((b1 & 0x0F) << 4) | (b2 >> 4)) + 17
                    disp = (((b2 & 0x0F) << 8) | b3) + 1
                else:  # ind == 1
                    b2 = data[in_pos]
                    b3 = data[in_pos + 1]
                    b4 = data[in_pos + 2]
                    in_pos += 3
                    length = (((b1 & 0x0F) << 12) | (b2 << 4) | (b3 >> 4)) + 273
                    disp = (((b3 & 0x0F) << 8) | b4) + 1

                for _ in range(length):
                    out.append(out[-disp])
    return bytes(out)
```
