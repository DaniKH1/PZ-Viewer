import struct
import math
from .pz_pk2 import unpack_pk2

class MotionClip:
    def __init__(self, name, bone_num, frame_num, fps=30.0):
        self.name = name
        self.bone_num = bone_num
        self.frame_num = frame_num
        self.fps = fps
        self.parent_ids = []
        self.trans_ids = []
        # frames[f][b] = {'rot': [rx, ry, rz], 'scale': [sx, sy, sz], 'trans': [tx, ty, tz]}
        self.frames = []

def parse_motn(data, name='motion'):
    if len(data) < 32 or data[:4] != b'MOTN':
        return None

    magic, map_flg, bone_num, trans_num, frame_num, interp, flg, si = struct.unpack('<4sIIIIIII', data[:32])

    parent_ids = [data[0x20 + i*2] for i in range(bone_num)]
    trans_ids = [data[0x21 + i*2] for i in range(bone_num)]

    include_rst = (flg >> 2) & 1
    table_off = 160 # 40 words
    if include_rst:
        table_off += bone_num * 24 * 4

    clip = MotionClip(name, bone_num, frame_num)
    clip.parent_ids = parent_ids
    clip.trans_ids = trans_ids

    frame_offsets = []
    for f in range(frame_num):
        pos = table_off + f * 4
        if pos + 4 <= len(data):
            fo = struct.unpack('<I', data[pos:pos+4])[0]
            frame_offsets.append(fo)

    if not frame_offsets:
        return None

    base_frame = []
    # Read Frame 0 (Full RST: 9 floats per bone)
    f0_off = frame_offsets[0]
    for b in range(bone_num):
        bo = f0_off + b * 36
        if bo + 36 <= len(data):
            vals = struct.unpack('<9f', data[bo:bo+36])
            base_frame.append({
                'rot': list(vals[0:3]),
                'scale': list(vals[3:6]),
                'trans': list(vals[6:9])
            })
        else:
            base_frame.append({
                'rot': [0.0, 0.0, 0.0],
                'scale': [1.0, 1.0, 1.0],
                'trans': [0.0, 0.0, 0.0]
            })

    clip.frames.append(base_frame)

    # Read remaining frames
    for f in range(1, frame_num):
        fo = frame_offsets[f]
        curr_frame = []
        ptr = fo
        # Read rotations for all bones (3 floats each)
        rots = []
        for b in range(bone_num):
            if ptr + 12 <= len(data):
                rots.append(list(struct.unpack('<3f', data[ptr:ptr+12])))
                ptr += 12
            else:
                rots.append(list(base_frame[b]['rot']))

        # Read translations for bones with trans_id != 0xFF (3 floats each)
        trans_dict = {}
        for b in range(bone_num):
            if trans_ids[b] != 0xFF:
                if ptr + 12 <= len(data):
                    trans_dict[b] = list(struct.unpack('<3f', data[ptr:ptr+12]))
                    ptr += 12

        for b in range(bone_num):
            t = trans_dict.get(b, list(base_frame[b]['trans']))
            s = list(base_frame[b]['scale'])
            r = rots[b]
            curr_frame.append({
                'rot': r,
                'scale': s,
                'trans': t
            })
        clip.frames.append(curr_frame)

    return clip

def parse_anm(data_or_path):
    entries = unpack_pk2(data_or_path)
    motions = []
    for entry in entries:
        # File type 0 contains motions
        if entry['type'] == 0:
            edata = entry['data']
            if len(edata) >= 16:
                mot_count = struct.unpack('<I', edata[:4])[0]
                m_entries = unpack_pk2(edata)
                for i, me in enumerate(m_entries):
                    m_clip = parse_motn(me['data'], name=f'Clip_{i:03d}')
                    if m_clip:
                        motions.append(m_clip)
        elif entry['data'][:4] == b'MOTN':
            m_clip = parse_motn(entry['data'], name=f'Clip_{len(motions):03d}')
            if m_clip:
                motions.append(m_clip)
    return motions
