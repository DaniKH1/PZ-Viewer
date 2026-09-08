import os
import struct

def unpack_pk2(data_or_path):
    if isinstance(data_or_path, (str, os.PathLike)):
        with open(data_or_path, 'rb') as f:
            data = f.read()
    else:
        data = data_or_path

    if len(data) < 16:
        return []

    file_num = struct.unpack('<I', data[:4])[0]
    entries = []
    offset = 16

    for i in range(file_num):
        if offset + 16 > len(data):
            break
        file_size, file_type = struct.unpack('<II', data[offset:offset+8])
        file_data = data[offset + 16 : offset + 16 + file_size]
        entries.append({
            'index': i,
            'offset': offset,
            'size': file_size,
            'type': file_type,
            'data': file_data
        })
        offset += 16 + file_size

    return entries

def unpack_room_pk2(data_or_path):
    entries = unpack_pk2(data_or_path)
    result = {
        'near_sgd': None,
        'far_sgd': None,
        'ss_sgd': None,
        'sh_sgd': None,
        'all_entries': entries
    }
    if len(entries) > 0:
        result['near_sgd'] = entries[0]['data']
    if len(entries) > 1:
        result['far_sgd'] = entries[1]['data']
    if len(entries) > 2:
        result['ss_sgd'] = entries[2]['data']
    if len(entries) > 3:
        result['sh_sgd'] = entries[3]['data']
    return result
