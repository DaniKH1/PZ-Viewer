"""FF1 SGD/LIT static prelighting, isolated from the FF2/FF3 readers.

The room's preset VIF RGB buffer is mutable runtime data, often zero on disk.
LIT records are category 11; their subtype (0..3) is NOT the category itself.
See docs/ff1/room-lighting.md for field evidence, references and limitations.
No filename, sample coordinates, exposure correction or arbitrary lamp radius
participates in decoding. Inputs are never modified.
"""
from collections import defaultdict, deque
import math
from pathlib import Path
import struct
import warnings


class FF1LightingError(ValueError):
    pass


class FF1LightingWarning(UserWarning):
    pass


def _need(data, offset, size, context):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise FF1LightingError(f'{context} at 0x{offset:x}: need {size} bytes (file {len(data)})')


def _vec(data, offset):
    _need(data, offset, 16, 'FF1 light vector')
    value = list(struct.unpack_from('<4f', data, offset))
    if not all(math.isfinite(v) for v in value):
        raise FF1LightingError(f'Non-finite FF1 light vector at 0x{offset:x}')
    return value


def _chains(data):
    """Bounded relative process chains. Shared tails are legal, cycles are not."""
    _need(data, 0, 24, 'FF1 SGD/LIT header')
    version, _, _, _, _, _, _, count = struct.unpack_from('<IBBHIIII', data)
    if version != 0x1050 or not 1 <= count <= 4096:
        raise FF1LightingError(f'Invalid FF1 SGD/LIT header at 0x0: version={version:x}, blocks={count}')
    _need(data, 24, count * 4, 'FF1 SGD/LIT block table')
    for block, offset in enumerate(struct.unpack_from(f'<{count}I', data, 24)):
        chain, visited = [], set()
        while offset:
            if offset in visited:
                raise FF1LightingError(f'Cycle in FF1 block {block} at 0x{offset:x}')
            if offset < 24 + count * 4 or offset % 4:
                raise FF1LightingError(f'Invalid FF1 process address at 0x{offset:x}')
            _need(data, offset, 16, f'FF1 block {block} process')
            visited.add(offset)
            relative, category = struct.unpack_from('<iI', data, offset)
            next_offset = offset + relative if relative else 0
            end = next_offset if relative > 0 else len(data)
            if relative and (abs(relative) < 16 or next_offset < 24 + count*4):
                raise FF1LightingError(f'Invalid FF1 link at 0x{offset:x}: {relative}')
            if next_offset:
                _need(data, next_offset, 16, f'FF1 next process from 0x{offset:x}')
            chain.append((offset, end, category))
            offset = next_offset
        yield block, chain


def parse_lit_lights(data):
    """Read FF1 category-11 directional, point, spot and ambient records.

    Retain zero-colour records and the original W fields for inspection.
    A point colour.w == 0 disables the cutoff, NOT the light. A spot's
    position.w is the cone half-angle in degrees; target.w is not an inner cone.
    Coordinates/power below are in the SGD's native room space. A common
    uniform world scale cancels between distance and 60*s / 200*s power.
    """
    lights = {'format': 'ff1_lit', 'ambient': [0., 0., 0., 0.],
              'directionals': [], 'points': [], 'spots': [], 'records': []}
    visited = set()
    # SgReadLights takes HeaderSection.primitives[0], not a scan of every block.
    for block, chain in _chains(data):
        if block != 0:
            break
        for offset, end, category in chain:
            if category != 11 or offset in visited:
                continue
            visited.add(offset)
            subtype, count = struct.unpack_from('<II', data, offset + 8)
            if subtype not in (0, 1, 2, 3):
                raise FF1LightingError(f'Unsupported FF1 light subtype {subtype} at 0x{offset:x}')
            # Repeated groups of the same subtype replace the previous array,
            # matching SgReadLights' assignment of num and writes from index 0.
            if subtype != 3:
                lights[('directionals', 'points', 'spots')[subtype]] = []
            stride = (32, 32, 48, 16)[subtype]
            if count > 4096 or offset + 16 + count*stride > end:
                raise FF1LightingError(f'FF1 light count {count} exceeds record at 0x{offset:x}')
            _need(data, offset + 16, count*stride, 'FF1 light payload')
            lights['records'].append({'block': block, 'offset': offset, 'subtype': subtype, 'count': count})
            for index in range(count):
                base = offset + 16 + index*stride
                color = _vec(data, base)
                if min(color[:3]) < 0:
                    raise FF1LightingError(f'Negative FF1 light colour at 0x{base:x}')
                record = {'offset': base, 'diffuse': color}
                if subtype == 3:
                    lights['ambient'] = color
                elif subtype == 0:
                    record['direction'] = _vec(data, base + 16)
                    lights['directionals'].append(record)
                elif subtype == 1:
                    record.update(pos=_vec(data, base + 16), power=60.0,
                                  cutoff_parameter=color[3])
                    if color[3] < 0:
                        raise FF1LightingError(f'Negative point cutoff at 0x{base:x}')
                    lights['points'].append(record)
                else:
                    pos, target = _vec(data, base + 16), _vec(data, base + 32)
                    if not 0 < pos[3] < 90:
                        raise FF1LightingError(f'Unsupported spot half-angle {pos[3]} at 0x{base+16:x}')
                    direction = [pos[k]-target[k] for k in range(3)]
                    if sum(v*v for v in direction) < 1e-12:
                        raise FF1LightingError(f'Zero-length spotlight direction at 0x{base:x}')
                    record.update(pos=pos, target=target, direction=direction,
                                  half_angle_degrees=pos[3], power=200.0)
                    lights['spots'].append(record)
    return lights


def read_lit_sidecar(path):
    """Same-stem sibling only, with case-insensitive extension on Linux too.

    Never pick a neighbouring room's light file. Return (bytes, actual path).
    Multiple case variants are an explicit error rather than filesystem order.
    """
    source = Path(path)
    candidates = [p for p in source.parent.iterdir()
                  if p.is_file() and p.stem == source.stem and p.suffix.lower() == '.lit']
    if len(candidates) > 1:
        raise FF1LightingError(f'Ambiguous .lit sidecar for {source.name}: {[p.name for p in candidates]}')
    if not candidates:
        return None, None
    return candidates[0].read_bytes(), str(candidates[0])


def _unit(v):
    length = math.sqrt(sum(x*x for x in v[:3]))
    return [x/length for x in v[:3]] if length > 1e-12 else [0., 0., 0.]


def _dot(a, b):
    return sum(x*y for x, y in zip(a[:3], b[:3]))


def _local_contribution(pos, normal, material, light, spot=False):
    """Equivalent scalar diffuse + n^8 specular terms from FF1 prerender.

    SetMaxColor255 normalises DColor and folds its maximum into power.
    The resulting attenuation is n.L / |L|^2 * power, NOT a linear radius.
    A view-dependent specular term for directional lights is not reconstructed.
    """
    direction = [light['pos'][k] - pos[k] for k in range(3)]
    distance2 = _dot(direction, direction)
    if distance2 <= 1e-12:
        return [0., 0., 0.]  # coincident source: no defined incident direction
    color = light['diffuse']
    power = light['power']
    if not spot:
        cutoff = light.get('cutoff_parameter', 0.)
        if cutoff and distance2 > power * cutoff * sum(color[:3]):
            return [0., 0., 0.]
    cone = 1.
    if spot:
        axis = _unit(light['direction'])  # from target towards source
        cosine2 = math.cos(math.radians(light['half_angle_degrees'])) ** 2
        projection = max(0., _dot(axis, direction))
        cone = max(0., min(1., (projection*projection / distance2 - cosine2) / (1.-cosine2)))
        if not cone:
            return [0., 0., 0.]
    textured = getattr(material, 'ff1_primtype', 1) != 0
    dscale = 192. if textured else 255.
    dcolor = [max(0., color[k]*material.diffuse[k]*dscale) for k in range(3)]
    maximum = max(dcolor) / 255.
    if maximum <= 0:
        # SetMaxColor255 uses 1 when the diffuse maximum is zero.
        # A black diffuse material can still have a specular response.
        maximum = 1.
    factor = min(1., max(0., _dot(normal, direction)) * power * maximum / distance2)
    sscale = sum(material.specular[:3]) * (43. if textured else 86.)
    return [cone * (dcolor[k]/maximum*factor +
                    color[k]*material.specular[k]*sscale/maximum*factor**8)
            for k in range(3)]


def evaluate_vertex(pos, normal, material, lights, base_rgb=(0., 0., 0.), *, local=True):
    """Return (display RGB, components) in GS modulation units (128=neutral).

    Preserve gains above one in the viewer (255/128 is legal PS2 modulation).
    Portable GLB export separately clamps COLOR_0 to its standard [0,1] range.
    This is a static vertex-light approximation, not the game's complete frame.
    """
    normal = _unit(normal)
    textured = getattr(material, 'ff1_primtype', 1) != 0
    neutral = 128. if textured else 255.
    ambient = [(lights['ambient'][k]*material.ambient[k] + material.emission[k])
               for k in range(3)]
    parallel = [0., 0., 0.]
    for light in lights['directionals']:
        factor = max(0., _dot(normal, _unit(light['direction'])))
        for k in range(3):
            parallel[k] += light['diffuse'][k]*material.diffuse[k]*(192. if textured else 255.)/neutral*factor
    points, spots = [0., 0., 0.], [0., 0., 0.]
    if local:
        for field, dest, is_spot in [('points', points, False), ('spots', spots, True)]:
            for light in lights[field]:
                result = _local_contribution(pos, normal, material, light, is_spot)
                for k in range(3):
                    dest[k] += result[k] / neutral
    # SgPreRender clamps the local RGB cache to 255 before later parallel light.
    raw = [ambient[k] + parallel[k] + min(255., max(0., base_rgb[k]) +
           (points[k]+spots[k])*neutral)/neutral for k in range(3)]
    rgb = [min(255./neutral, max(0., value)) for value in raw]
    return rgb, {'ambient': ambient, 'directional': parallel, 'point': points,
                 'spot': spots, 'unclipped_rgb': raw}


def apply_ff1_xbox_room_lighting(model, lit_data):
    """Bake an FF1 Xbox room's static vertex lighting from its matching LIT."""
    report = {'format': 'ff1_xbox_static_vertex_lighting',
              'status': 'missing_lit' if lit_data is None else 'invalid_lit',
              'meshes_baked': 0, 'vertices_baked': 0,
              'point_lit_vertices': 0, 'spot_lit_vertices': 0,
              'overbright_vertices': 0, 'warnings': []}
    model.ff1_lighting = report
    if lit_data is None:
        for mesh in model.meshes:
            mesh.colors = [[1., 1., 1., 1.] for _ in mesh.positions]
        return report

    try:
        lights = parse_lit_lights(lit_data)
        if not lights['records']:
            raise FF1LightingError('No category-11 lights in FF1 Xbox LIT')

        # PKX room geometry uses the opposite X/Z basis from the room LIT.
        # Rotate the light basis 180 degrees around Y to match the decoded mesh.
        for group in ('directionals', 'points', 'spots'):
            for light in lights[group]:
                for field in ('pos', 'target', 'direction'):
                    if field in light:
                        light[field][0] *= -1
                        light[field][2] *= -1

        pending = []
        for mesh in model.meshes:
            if len(mesh.positions) != len(mesh.normals):
                raise FF1LightingError(
                    f'Position/normal count mismatch in Xbox room mesh {mesh.name}'
                )
            if not 0 <= mesh.material_index < len(model.materials):
                raise FF1LightingError(
                    f'Invalid material for Xbox room mesh {mesh.name}'
                )
            material = model.materials[mesh.material_index]
            material.ff1_primtype = int(
                getattr(material, 'xbox_resource_index', -1) >= 0
            )
            colors = []
            for index, (position, normal) in enumerate(
                    zip(mesh.positions, mesh.normals)):
                color, components = evaluate_vertex(
                    position, normal, material, lights, base_rgb=(0., 0., 0.)
                )
                colors.append(color + [1.])
                report['point_lit_vertices'] += int(
                    max(components['point']) > 1e-9
                )
                report['spot_lit_vertices'] += int(
                    max(components['spot']) > 1e-9
                )
                report['overbright_vertices'] += int(
                    max(components['unclipped_rgb']) > 1.
                )
            pending.append((mesh, colors))

        for mesh, colors in pending:
            mesh.colors = colors
            mesh.ff1_prelit = True
        report['meshes_baked'] = len(pending)
        report['vertices_baked'] = sum(len(colors) for _, colors in pending)
        report['status'] = 'baked' if pending else 'no_render_geometry'
    except (FF1LightingError, struct.error) as exc:
        for mesh in model.meshes:
            mesh.colors = [[1., 1., 1., 1.] for _ in mesh.positions]
        report['warnings'].append(str(exc))
        warnings.warn(
            f'{getattr(model, "name", "FF1 Xbox room")}: {exc}; '
            'using neutral vertex colors',
            FF1LightingWarning,
        )
    return report


def _unpack(data, start, end):
    """Find a VIF RGB UNPACK without leaving its process unit.

    The original uses V3-32 (0x68 or masked 0x78); RGB are floats, not RGBA8.
    This matches GetNextUnpackAddr's command scan, with explicit bounds.
    """
    for offset in range(start, end-3, 4):
        word = struct.unpack_from('<I', data, offset)[0]
        if ((word >> 24) & 0x60) == 0x60:
            if ((word >> 24) & 0x0F) != 8:
                raise FF1LightingError(f'Unsupported RGB VIF format at 0x{offset:x}')
            return offset, ((word >> 16) & 0xFF) or 256
    raise FF1LightingError(f'Missing RGB UNPACK at 0x{start:x} (unit end 0x{end:x})')


def decode_rgb_strip(data, offset, count):
    """Recover GS-unit float RGB including raw uint32(1) repeat-vi-2 markers."""
    _need(data, offset, count*12, 'FF1 preset RGB')
    colors, repeats = [], 0
    for index in range(count):
        address = offset + 12*index
        if struct.unpack_from('<I', data, address)[0] == 1:
            if index < 2:
                raise FF1LightingError(f'RGB repeat without predecessor at 0x{address:x}')
            colors.append(colors[index-2][:])
            repeats += 1
        else:
            rgb = list(struct.unpack_from('<3f', data, address))
            if not all(math.isfinite(v) and 0 <= v <= 255 for v in rgb):
                raise FF1LightingError(f'Invalid GS-unit RGB at 0x{address:x}: {rgb}')
            colors.append(rgb)
    return colors, repeats


def _preset_sources(data, model):
    """Re-read ONLY colour stream provenance; never reinterpret geometry.

    Map process units to the shared reader's ordered (coord,type,material)
    queues. Match strip/vertex counts before changing any colors. No mesh/sample
    filename is used as an asset heuristic; mesh names are parser-generated IDs.
    """
    queues = defaultdict(deque)
    blocks = struct.unpack_from('<I', data, 20)[0]
    for block, chain in _chains(data):
        if blocks > 1 and block == 0:
            continue
        coord = block
        for offset, _, category in chain:
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
                _need(data, offset+20, 4, 'FF1 preset offsets')
                _, prim = struct.unpack_from('<hh', data, offset+20)
                # Reference-only far/display-list entries have no own RGB data.
                if prim <= 0:
                    continue
                cursor, strips, repeat_flags, repeats = offset+prim*4, [], [], 0
                if cursor < offset+16 or cursor >= end:
                    raise FF1LightingError(f'Preset RGB address outside unit at 0x{offset:x}')
                for _ in range(loops):
                    up, count = _unpack(data, cursor, end)
                    if count < 3:
                        break
                    if up + 4 + count*12 > end:
                        raise FF1LightingError(f'Preset RGB crosses next process at 0x{up:x}')
                    colors, repeat_count = decode_rgb_strip(data, up+4, count)
                    strips.append(colors)
                    repeat_flags.append([struct.unpack_from('<I', data, up+4+i*12)[0] == 1
                                         for i in range(count)])
                    repeats += repeat_count
                    cursor = up + 4 + count*12
                if strips:
                    queues[(coord, kind, material)].append({'offset': offset, 'strips': strips, 'repeat_flags': repeat_flags, 'repeats': repeats})
    return queues


def apply_ff1_lighting(model, data, lit_data):
    """Attach static colors/diagnostics only when valid FF1 LIT data are present.

    Invalid sidecars leave the existing visible geometry intact with a warning.
    Already populated preset caches keep their local term (no double bake).
    Compact 0x82 props receive static light without inventing a preset cache.
    Character compact/skinned formats are intentionally unaffected.
    """
    report = {'format': 'ff1_static_vertex_lighting', 'status': 'missing_lit',
              'meshes_baked': 0, 'vertices_baked': 0, 'preset_vertices': 0,
              'stored_local_vertices': 0, 'repeat_markers': 0,
              'point_lit_vertices': 0, 'spot_lit_vertices': 0,
              'overbright_vertices': 0, 'warnings': []}
    model.ff1_lighting = report
    model.ff1_prelit = False
    if lit_data is None:
        # The game can keep LIGHT units in the main SGD instead of a sidecar.
        try:
            _, first_chain = next(_chains(data))
            if not any(category == 11 for _, _, category in first_chain):
                return
        except (FF1LightingError, StopIteration):
            return
        lit_data = data
        report['source'] = 'embedded_sgd'
    else:
        report['source'] = 'lit_sidecar'
    try:
        lights = parse_lit_lights(lit_data)
        report['lights'] = lights
        if not lights['records']:
            raise FF1LightingError('No category-11 lights in FF1 LIT at 0x0')
        if not model.meshes:
            report['status'] = 'no_render_geometry'
            return
        # Material primtype is not retained by the legacy shared reader.
        material_base = struct.unpack_from('<I', data, 12)[0]
        material_count = struct.unpack_from('<H', data, 6)[0]
        for index, material in enumerate(model.materials[:material_count]):
            _need(data, material_base+index*176, 80, 'FF1 light material')
            material.ff1_primtype = struct.unpack_from('<I', data, material_base+index*176)[0]
        queues = _preset_sources(data, model)
        pending = []
        for mesh in model.meshes:
            kind = int(mesh.name.rsplit('_t0x', 1)[1], 16) if '_t0x' in mesh.name else -1
            if kind not in (0x12, 0x32, 0x82):
                continue
            if not 0 <= mesh.material_index < len(model.materials):
                raise FF1LightingError(f'Invalid material for FF1 {mesh.name}')
            material = model.materials[mesh.material_index]
            source = None
            if kind in (0x12, 0x32):
                key = (mesh.bone_index, kind, mesh.material_index)
                if not queues[key]:
                    raise FF1LightingError(f'Missing RGB provenance for {mesh.name}, material {mesh.material_index}')
                source = queues[key].popleft()
                strips = source['strips']
                if sum(map(len, strips)) != len(mesh.positions):
                    raise FF1LightingError(f'RGB/geometry vertex mismatch at 0x{source["offset"]:x}')
            else:
                strips = [[[0., 0., 0.] for _ in mesh.positions]]
            colors, components, counter = [], [], 0
            for strip_index, strip in enumerate(strips):
                # The whole strip is the runtime cache unit. A genuinely dark
                # vertex within an existing bake must not be relit independently.
                stored = any(any(v != 0 for v in rgb) for rgb in strip)
                if stored:
                    report['stored_local_vertices'] += len(strip)
                for vertex_index, rgb in enumerate(strip):
                    if source and source['repeat_flags'][strip_index][vertex_index]:
                        result, terms = colors[counter-2][:3], components[counter-2]
                    else:
                        result, terms = evaluate_vertex(mesh.positions[counter], mesh.normals[counter],
                                                        material, lights, rgb, local=not stored)
                    colors.append(result + [mesh.colors[counter][3] if counter < len(mesh.colors) else 1.])
                    components.append(terms)
                    report['point_lit_vertices'] += int(max(terms['point']) > 1e-9)
                    report['spot_lit_vertices'] += int(max(terms['spot']) > 1e-9)
                    report['overbright_vertices'] += int(max(terms['unclipped_rgb']) > 1.)
                    counter += 1
            if source:
                report['repeat_markers'] += source['repeats']
                report['preset_vertices'] += len(colors)
            pending.append((mesh, colors, components, source))
        # Commit only after all eligible meshes validated, no half-lit model.
        for mesh, colors, terms, source in pending:
            mesh.colors = colors
            mesh.ff1_light_components = terms
            mesh.ff1_color_source_offset = source['offset'] if source else None
            mesh.ff1_prelit = True
        report['meshes_baked'] = len(pending)
        report['vertices_baked'] = sum(len(item[1]) for item in pending)
        report['status'] = 'baked' if pending else 'no_supported_meshes'
        model.ff1_prelit = bool(pending)
    except (FF1LightingError, struct.error) as exc:
        report['status'] = 'invalid_lit_or_preset'
        report['warnings'].append(str(exc))
        for key in ('meshes_baked', 'vertices_baked', 'preset_vertices', 'stored_local_vertices',
                    'repeat_markers', 'point_lit_vertices', 'spot_lit_vertices', 'overbright_vertices'):
            report[key] = 0
        warnings.warn(f'{getattr(model, "name", "FF1")}: {exc}; keeping unlit preview', FF1LightingWarning)


def merge_lighting_diagnostics(base, extra):
    first, second = getattr(base, 'ff1_lighting', None), getattr(extra, 'ff1_lighting', None)
    if not second:
        return
    if first is None:
        base.ff1_lighting = second
    else:
        first.setdefault('parts', []).append({k: v for k, v in second.items() if k not in ('lights', 'parts')})
        for key in ('meshes_baked', 'vertices_baked', 'preset_vertices', 'stored_local_vertices',
                    'repeat_markers', 'point_lit_vertices', 'spot_lit_vertices', 'overbright_vertices'):
            first[key] = first.get(key, 0) + second.get(key, 0)
        first['warnings'].extend(second.get('warnings', []))
        if second.get('status') == 'baked':
            first['status'] = 'baked'
    if base.ff1_lighting['warnings'] and base.ff1_lighting['meshes_baked']:
        base.ff1_lighting['status'] = 'partial'
    base.ff1_prelit = bool(getattr(base, 'ff1_prelit', False) or getattr(extra, 'ff1_prelit', False))
