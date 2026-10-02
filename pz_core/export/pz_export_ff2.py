"""FF2 stored-prelight export; other assets use the unmodified FF1 dispatcher.

COLOR_0 is restricted to [0,1] by glTF. Only an export copy is clipped; the
viewer keeps the original GS gains up to 255/128. This is a portable material,
not a full PS2 framebuffer/ALPHA/TFX emulation.
"""
import copy
import json
import struct
from pz_core.export.pz_export_ff1 import export_glb as _export_glb, export_obj as _export_obj


def _export_copy(model):
    if not getattr(model, 'ff2_prelit', False):
        return model, set()
    out = copy.copy(model)
    out.meshes, affected = [], set()
    for mesh in model.meshes:
        if getattr(mesh, 'ff2_prelit', False):
            clone = copy.copy(mesh)
            clone.colors = [[min(1., max(0., v)) for v in rgba] for rgba in mesh.colors]
            affected.add(mesh.material_index)
            out.meshes.append(clone)
        else:
            out.meshes.append(mesh)
    out.materials = []
    for index, material in enumerate(model.materials):
        if index in affected:
            clone = copy.copy(material)
            clone.diffuse = [1., 1., 1., 1.]
            out.materials.append(clone)
        else:
            out.materials.append(material)
    return out, affected


def export_obj(model, output_path, include_vertex_colors=True, textures=None):
    converted, _ = _export_copy(model)
    return _export_obj(converted, output_path, include_vertex_colors, textures)


def export_glb(model, output_path, *args, **kwargs):
    converted, affected = _export_copy(model)
    result = _export_glb(converted, output_path, *args, **kwargs)
    if not affected and getattr(model, 'sgd_format', '') != 'ff2':
        return result
    with open(output_path, 'rb') as stream:
        data = stream.read()
    size, kind = struct.unpack_from('<II', data, 12)
    if kind != 0x4E4F534A:
        raise ValueError('FF2 GLB adapter: missing JSON chunk')
    document = json.loads(data[20:20+size])
    textures = kwargs.get('textures', args[2] if len(args) > 2 else None) or []
    for source, material in zip(model.materials, document['materials']):
        slot = source.texture_index
        if 0 <= slot < len(textures) and 'A' in textures[slot].getbands():
            low, high = textures[slot].getchannel('A').getextrema()
            if low < 255:
                material['alphaMode'] = 'MASK' if high == 255 else 'BLEND'
                if high == 255:
                    material['alphaCutoff'] = .5
    for index in sorted(affected):
        material = document['materials'][index]
        material.setdefault('extensions', {})['KHR_materials_unlit'] = {}
        material['pbrMetallicRoughness']['baseColorFactor'] = [1., 1., 1., 1.]
    extensions = document.setdefault('extensionsUsed', [])
    if affected and 'KHR_materials_unlit' not in extensions:
        extensions.append('KHR_materials_unlit')
    include_colors = kwargs.get('include_vertex_colors', args[1] if len(args) > 1 else True)
    document.setdefault('extras', {})['ff2_vertex_colors'] = {
        'mode': 'stored_vertex_prelight' if include_colors else 'textures_without_lighting',
        'source': 'SGD preset VIF V3-32 RGB / 128',
        'overbright_vertices_clamped': getattr(model, 'ff2_vertex_colors', {}).get('overbright_vertices', 0) if include_colors else 0,
        'display_limit': 'portable linear/sRGB glTF material, not GS byte-space framebuffer emulation',
        'runtime_lamps_or_shadows_reconstructed': False,
        'alpha_policy': 'decoded alpha with generic mask/blend, not recovered GS ALPHA state',
    }
    encoded = json.dumps(document, separators=(',', ':'), allow_nan=False).encode('utf-8')
    encoded += b' '*((-len(encoded)) % 4)
    tail = data[20+size:]
    with open(output_path, 'wb') as stream:
        stream.write(struct.pack('<III', 0x46546C67, 2, 20+len(encoded)+len(tail)))
        stream.write(struct.pack('<II', len(encoded), 0x4E4F534A))
        stream.write(encoded)
        stream.write(tail)
    return result
