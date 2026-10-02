"""FF2 stored VIF V3-32 colours, without relighting or filename-specific rules.

128 is the neutral GS MODULATE value. Floats below one are still in GS
units, not normalized RGB. An all-black strip in an otherwise populated
cache is not missing data; it must not be replaced by uniform grey.
"""
from collections import defaultdict, deque
import math
import struct

from pz_core.ff2.pz_gs_ff2 import FF2FormatError, need, sgd_chains


def next_rgb_unpack(data, cursor, end):
    """Read VIF control words up to a bounded RGB UNPACK, not an arbitrary scan."""
    while cursor + 4 <= end:
        word = struct.unpack_from('<I', data, cursor)[0]
        cmd = (word >> 24) & 127
        if cmd & 0x60 == 0x60:
            # VN=2, VL=0; bit 4 is the VIF mask, not another component.
            if cmd & 15 != 8:
                raise FF2FormatError(f'FF2 RGB at 0x{cursor:x}: expected V3-32, got 0x{cmd:x}')
            count = (word >> 16) & 255 or 256
            if cursor + 4 + count * 12 > end:
                raise FF2FormatError(f'FF2 RGB UNPACK at 0x{cursor:x} crosses process boundary')
            return cursor, count
        size = 4
        if cmd == 0x20:  # STMASK
            size += 4
        elif cmd in (0x30, 0x31):  # STROW / STCOL
            size += 16
        elif cmd not in (0, 1, 2, 3, 4, 5, 6, 7, 0x10, 0x11, 0x13, 0x14, 0x15, 0x17):
            raise FF2FormatError(f'FF2 RGB VIF at 0x{cursor:x}: unexpected command 0x{cmd:x}')
        cursor += size
    raise FF2FormatError(f'FF2 missing RGB UNPACK before 0x{end:x}')


def decode_rgb_strip(data, offset, count):
    need(data, offset, count * 12, 'RGB strip')
    out, repeats = [], 0
    for index in range(count):
        address = offset + index * 12
        if struct.unpack_from('<I', data, address)[0] == 1:
            if index < 2:
                raise FF2FormatError(f'FF2 RGB repeat without predecessor at 0x{address:x}')
            out.append(list(out[index-2]))
            repeats += 1
        else:
            rgb = list(struct.unpack_from('<3f', data, address))
            if not all(math.isfinite(v) and 0 <= v <= 255 for v in rgb):
                raise FF2FormatError(f'FF2 invalid GS RGB at 0x{address:x}: {rgb}')
            out.append(rgb)
    return out, repeats


def preset_sources(data, model):
    """Recover colour provenance only; shared parser geometry is unchanged."""
    queues = defaultdict(deque)
    count = struct.unpack_from('<I', data, 20)[0]
    for block, chain in sgd_chains(data):
        if count > 1 and block == 0:
            continue
        coord = block
        for offset, end, category in chain:
            if category == 3:
                candidate = struct.unpack_from('<i', data, offset+8)[0]
                if 0 <= candidate < len(model.bones):
                    coord = candidate
                    break
        material, vuvn = 0, None
        for offset, end, category in chain:
            if category == 0:
                vuvn = offset
            elif category == 2:
                material = struct.unpack_from('<I', data, offset+8)[0]
            elif category == 1 and vuvn is not None:
                kind, loops = data[offset+13], data[offset+14]
                if kind not in (0x12, 0x32) or not loops:
                    continue
                need(data, offset+20, 4, 'preset RGB pointer')
                _, prim = struct.unpack_from('<hh', data, offset+20)
                if prim <= 0:  # Reference-only packet: no own colour stream.
                    continue
                cursor = offset + prim * 4
                if cursor < offset + 16 or cursor >= end:
                    raise FF2FormatError(f'FF2 RGB pointer outside process at 0x{offset:x}')
                strips, repeats, locations = [], 0, []
                for _ in range(loops):
                    up, n = next_rgb_unpack(data, cursor, end)
                    if n < 3:
                        break
                    rgb, repeated = decode_rgb_strip(data, up+4, n)
                    strips.append(rgb)
                    locations.append({'unpack_offset': up, 'count': n})
                    repeats += repeated
                    cursor = up + 4 + 12*n
                if strips:
                    queues[(coord, kind, material)].append({
                        'process_offset': offset, 'strips': strips,
                        'locations': locations, 'repeat_markers': repeats})
    return queues


def apply_stored_vertex_colors(model, data):
    """Commit only after source and geometry counts agree for every match.

    No ambient/lamp data are invented. Other mesh types keep their existing
    behaviour. Empty whole-model caches are exposed, not claimed to be lit.
    """
    report = {'format': 'ff2_stored_gs_vertex_colors', 'status': 'no_supported_streams',
              'meshes_decoded': 0, 'vertices_decoded': 0, 'strips_decoded': 0,
              'zero_vertices': 0, 'subunit_vertices': 0, 'overbright_vertices': 0,
              'repeat_markers': 0, 'rgb_min': None, 'rgb_max': None,
              'normalization': 'GS MODULATE RGB / 128 (no per-vertex scale guessing)',
              'warnings': []}
    model.ff2_vertex_colors, model.ff2_prelit = report, False
    queues, pending = preset_sources(data, model), []
    for mesh in model.meshes:
        kind = int(mesh.name.rsplit('_t0x', 1)[1], 16) if '_t0x' in mesh.name else -1
        if kind not in (0x12, 0x32):
            continue
        key = (mesh.bone_index, kind, mesh.material_index)
        if not queues[key]:
            report['warnings'].append(f'No owned RGB stream for {mesh.name}; keeping existing colours')
            continue
        source = queues[key].popleft()
        raw = [rgb for strip in source['strips'] for rgb in strip]
        if len(raw) != len(mesh.positions):
            raise FF2FormatError(f'FF2 RGB/geometry mismatch at 0x{source["process_offset"]:x}: '
                                 f'{len(raw)} colours, {len(mesh.positions)} vertices')
        colors = [[r/128., g/128., b/128., 1.] for r, g, b in raw]
        pending.append((mesh, colors, raw, source))
    leftover = sum(len(queue) for queue in queues.values())
    if leftover:
        raise FF2FormatError(f'FF2 0x0: {leftover} RGB processes have no corresponding output mesh')
    flat = [rgb for _, _, raw, _ in pending for rgb in raw]
    if flat:
        report['rgb_min'] = [min(c[i] for c in flat) for i in range(3)]
        report['rgb_max'] = [max(c[i] for c in flat) for i in range(3)]
        populated = max(report['rgb_max']) > 0
        for mesh, colors, raw, source in pending:
            mesh.colors = colors
            mesh.ff2_prelit = populated
            mesh.ff2_color_source = {key: value for key, value in source.items() if key != 'strips'}
            report['strips_decoded'] += len(source['strips'])
            report['repeat_markers'] += source['repeat_markers']
        report.update({'meshes_decoded': len(pending), 'vertices_decoded': len(flat),
                       'zero_vertices': sum(max(c) == 0 for c in flat),
                       'subunit_vertices': sum(0 < max(c) <= 1 for c in flat),
                       'overbright_vertices': sum(max(c) > 128 for c in flat),
                       'status': 'stored_prelight' if populated else 'empty_preset_cache'})
        model.ff2_prelit = populated
        if not populated:
            report['warnings'].append('RGB cache is entirely zero: no stored illumination; preview defaults to textures only')
    return report


def merge_color_diagnostics(base, extra):
    report = getattr(extra, 'ff2_vertex_colors', None)
    if not report:
        return
    old = getattr(base, 'ff2_vertex_colors', None)
    if old is None:
        base.ff2_vertex_colors = report
    else:
        old.setdefault('parts', []).append({k:v for k,v in report.items() if k != 'parts'})
        for key in ('meshes_decoded', 'vertices_decoded', 'strips_decoded', 'zero_vertices',
                    'subunit_vertices', 'overbright_vertices', 'repeat_markers'):
            old[key] += report[key]
        old['warnings'].extend(report['warnings'])
        for key, func in (('rgb_min', min), ('rgb_max', max)):
            if report[key] is not None:
                old[key] = ([func(a,b) for a,b in zip(old[key], report[key])]
                            if old[key] is not None else report[key])
        if report['status'] == 'stored_prelight':
            old['status'] = 'stored_prelight'
    base.ff2_prelit = bool(getattr(base, 'ff2_prelit', False) or getattr(extra, 'ff2_prelit', False))
