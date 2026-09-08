import struct
from PIL import Image

def adjust_ps2_alpha(alpha):
    if alpha <= 127:
        return alpha << 1
    return 255

def decode_tim2(data):
    if len(data) < 16 or data[:4] != b'TIM2':
        return []

    format_ver, format_id, pictures = struct.unpack('<BBH', data[4:8])
    offset = 16
    images = []

    for _ in range(pictures):
        if offset + 48 > len(data):
            break

        (total_size, clut_size, image_size, header_size, clut_colors,
         pict_format, mipmap_textures, clut_type, image_type,
         width, height, gs_tex0, gs_tex1, gs_texa, gs_clut) = struct.unpack(
            '<IIIHHBBBBHHQQII', data[offset:offset+48]
        )

        img_offset = offset + header_size
        clut_offset = img_offset + image_size

        img_data = data[img_offset:img_offset + image_size]
        clut_data = data[clut_offset:clut_offset + clut_size] if clut_colors > 0 else b''

        if image_size == 0:
            images.append({
                'width': width,
                'height': height,
                'image_type': image_type,
                'clut_type': clut_type,
                'clut_colors': clut_colors,
                'clut_data': clut_data,
                'gs_tex0': gs_tex0,
                'image': None,
                'is_clut_only': True
            })
            offset += total_size
            continue

        # Parse image pixels
        rgba_pixels = bytearray(width * height * 4)

        if image_type == 5: # IDTEX8 (8-bit index)
            for y in range(height):
                for x in range(width):
                    idx = y * width + x
                    if idx < len(img_data):
                        pal_idx = img_data[idx]
                        # PS2 CSM1 unswizzle
                        n = pal_idx
                        mod32 = n & 31
                        if 8 <= mod32 < 16:
                            n += 8
                        elif 16 <= mod32 < 24:
                            n -= 8

                        clut_raw_type = clut_type & 0x3F
                        dst_idx = idx * 4
                        if clut_raw_type == 3: # RGB32
                            co = n * 4
                            if co + 4 <= len(clut_data):
                                r, g, b, a = clut_data[co:co+4]
                                rgba_pixels[dst_idx] = r
                                rgba_pixels[dst_idx+1] = g
                                rgba_pixels[dst_idx+2] = b
                                rgba_pixels[dst_idx+3] = adjust_ps2_alpha(a)
                        elif clut_raw_type == 1: # RGB16
                            co = n * 2
                            if co + 2 <= len(clut_data):
                                c = struct.unpack('<H', clut_data[co:co+2])[0]
                                rgba_pixels[dst_idx] = ((c & 0x1F) << 3)
                                rgba_pixels[dst_idx+1] = (((c >> 5) & 0x1F) << 3)
                                rgba_pixels[dst_idx+2] = (((c >> 10) & 0x1F) << 3)
                                rgba_pixels[dst_idx+3] = 255 if (c & 0x8000) else 0

        elif image_type == 4: # IDTEX4 (4-bit index)
            for y in range(height):
                for x in range(width):
                    idx = y * width + x
                    byte_idx = idx // 2
                    if byte_idx < len(img_data):
                        b_val = img_data[byte_idx]
                        pal_idx = (b_val & 0x0F) if (idx % 2 == 0) else ((b_val >> 4) & 0x0F)
                        n = pal_idx
                        clut_raw_type = clut_type & 0x3F
                        dst_idx = idx * 4
                        if clut_raw_type == 3: # RGB32
                            co = n * 4
                            if co + 4 <= len(clut_data):
                                r, g, b, a = clut_data[co:co+4]
                                rgba_pixels[dst_idx] = r
                                rgba_pixels[dst_idx+1] = g
                                rgba_pixels[dst_idx+2] = b
                                rgba_pixels[dst_idx+3] = adjust_ps2_alpha(a)
                        elif clut_raw_type == 1: # RGB16
                            co = n * 2
                            if co + 2 <= len(clut_data):
                                c = struct.unpack('<H', clut_data[co:co+2])[0]
                                rgba_pixels[dst_idx] = ((c & 0x1F) << 3)
                                rgba_pixels[dst_idx+1] = (((c >> 5) & 0x1F) << 3)
                                rgba_pixels[dst_idx+2] = (((c >> 10) & 0x1F) << 3)
                                rgba_pixels[dst_idx+3] = 255 if (c & 0x8000) else 0

        elif image_type == 3: # RGB32
            for idx in range(width * height):
                src = idx * 4
                dst = idx * 4
                if src + 4 <= len(img_data):
                    rgba_pixels[dst] = img_data[src]
                    rgba_pixels[dst+1] = img_data[src+1]
                    rgba_pixels[dst+2] = img_data[src+2]
                    rgba_pixels[dst+3] = adjust_ps2_alpha(img_data[src+3])

        elif image_type == 1: # RGB16
            for idx in range(width * height):
                src = idx * 2
                dst = idx * 4
                if src + 2 <= len(img_data):
                    c = struct.unpack('<H', img_data[src:src+2])[0]
                    rgba_pixels[dst] = ((c & 0x1F) << 3)
                    rgba_pixels[dst+1] = (((c >> 5) & 0x1F) << 3)
                    rgba_pixels[dst+2] = (((c >> 10) & 0x1F) << 3)
                    rgba_pixels[dst+3] = 255 if (c & 0x8000) else 0

        img = Image.frombytes('RGBA', (width, height), bytes(rgba_pixels))
        # If all alpha values are 0, it means the texture did not have an alpha channel in PS2 (RGB only); make it fully opaque
        a_min, a_max = img.split()[-1].getextrema()
        if a_max == 0:
            img.putalpha(255)

        images.append({
            'width': width,
            'height': height,
            'image_type': image_type,
            'clut_type': clut_type,
            'clut_colors': clut_colors,
            'clut_data': clut_data,
            'img_data': img_data,
            'gs_tex0': gs_tex0,
            'image': img,
            'is_clut_only': False
        })

        offset += total_size

    return images

def render_tim2_clut_variation(base_timg, clut_timg):
    if not base_timg or not clut_timg:
        return None
    w = base_timg.get('width', 0)
    h = base_timg.get('height', 0)
    img_data = base_timg.get('img_data', b'')
    clut_data = clut_timg.get('clut_data', b'')
    image_type = base_timg.get('image_type', 0)
    clut_type = clut_timg.get('clut_type', 3) & 0x3F

    if not img_data or not clut_data or w <= 0 or h <= 0:
        return None

    rgba_pixels = bytearray(w * h * 4)

    if image_type == 5: # IDTEX8
        for idx in range(min(len(img_data), w * h)):
            pal_idx = img_data[idx]
            n = pal_idx
            mod32 = n & 31
            if 8 <= mod32 < 16:
                n += 8
            elif 16 <= mod32 < 24:
                n -= 8
            dst_idx = idx * 4
            if clut_type == 3: # RGB32
                co = n * 4
                if co + 4 <= len(clut_data):
                    r, g, b, a = clut_data[co:co+4]
                    rgba_pixels[dst_idx] = r
                    rgba_pixels[dst_idx+1] = g
                    rgba_pixels[dst_idx+2] = b
                    rgba_pixels[dst_idx+3] = adjust_ps2_alpha(a)
            elif clut_type == 1: # RGB16
                co = n * 2
                if co + 2 <= len(clut_data):
                    c = struct.unpack('<H', clut_data[co:co+2])[0]
                    rgba_pixels[dst_idx] = ((c & 0x1F) << 3)
                    rgba_pixels[dst_idx+1] = (((c >> 5) & 0x1F) << 3)
                    rgba_pixels[dst_idx+2] = (((c >> 10) & 0x1F) << 3)
                    rgba_pixels[dst_idx+3] = 255 if (c & 0x8000) else 0

    elif image_type == 4: # IDTEX4
        for idx in range(w * h):
            byte_idx = idx // 2
            if byte_idx < len(img_data):
                b_val = img_data[byte_idx]
                pal_idx = (b_val & 0x0F) if (idx % 2 == 0) else ((b_val >> 4) & 0x0F)
                n = pal_idx
                dst_idx = idx * 4
                if clut_type == 3:
                    co = n * 4
                    if co + 4 <= len(clut_data):
                        r, g, b, a = clut_data[co:co+4]
                        rgba_pixels[dst_idx] = r
                        rgba_pixels[dst_idx+1] = g
                        rgba_pixels[dst_idx+2] = b
                        rgba_pixels[dst_idx+3] = adjust_ps2_alpha(a)
                elif clut_type == 1:
                    co = n * 2
                    if co + 2 <= len(clut_data):
                        c = struct.unpack('<H', clut_data[co:co+2])[0]
                        rgba_pixels[dst_idx] = ((c & 0x1F) << 3)
                        rgba_pixels[dst_idx+1] = (((c >> 5) & 0x1F) << 3)
                        rgba_pixels[dst_idx+2] = (((c >> 10) & 0x1F) << 3)
                        rgba_pixels[dst_idx+3] = 255 if (c & 0x8000) else 0

    img = Image.frombytes('RGBA', (w, h), bytes(rgba_pixels))
    a_min, a_max = img.split()[-1].getextrema()
    base_img = base_timg.get('image')
    base_is_opaque = base_img and base_img.split()[-1].getextrema()[0] == 255
    if a_max == 0 or (base_is_opaque and a_max < 128):
        img.putalpha(255)

    return img
