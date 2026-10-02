"""Static FF1 lighting adapters; other families use the existing Xbox dispatcher.

Portable glTF COLOR_0 must be [0,1]. FF1's GS gains can reach 255/128;
only the export copy is clipped. The live viewer retains those highlights.
This exports baked vertex light, not native FF1 GPU programs or active lamps.
"""
import copy
import json
import struct
from pz_core.export.pz_export_xbox import export_glb as _export_glb, export_obj as _export_obj


def _export_copy(model):
    if not getattr(model, 'ff1_prelit', False):
        return model, set()
    out = copy.copy(model)
    out.meshes = []
    affected = set()
    for mesh in model.meshes:
        if getattr(mesh, 'ff1_prelit', False):
            affected.add(mesh.material_index)
            clone = copy.copy(mesh)
            clone.colors = [[min(1., max(0., v)) for v in rgba] for rgba in mesh.colors]
            out.meshes.append(clone)
        else:
            out.meshes.append(mesh)
    out.materials = []
    for index, material in enumerate(model.materials):
        if index in affected:
            clone = copy.copy(material)
            # Ambient/material diffuse/emission are already baked into COLOR_0.
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
    if not affected:
        return result
    with open(output_path, 'rb') as stream:
        data = stream.read()
    size, kind = struct.unpack_from('<II', data, 12)
    if kind != 0x4E4F534A:
        raise ValueError('FF1 GLB adapter: missing JSON chunk')
    document = json.loads(data[20:20+size])
    for index in sorted(affected):
        material = document['materials'][index]
        material.setdefault('extensions', {})['KHR_materials_unlit'] = {}
        material['pbrMetallicRoughness']['baseColorFactor'] = [1., 1., 1., 1.]
    extensions = document.setdefault('extensionsUsed', [])
    if 'KHR_materials_unlit' not in extensions:
        extensions.append('KHR_materials_unlit')
    include_colors = kwargs.get('include_vertex_colors', args[1] if len(args)>1 else True)
    document.setdefault('extras', {})['ff1_lighting'] = {
        'mode': 'static_vertex_bake' if include_colors else 'textures_without_lighting',
        'source': 'category-11 LIT records and SGD materials/normals',
        'overbright_vertices_clamped': getattr(model, 'ff1_lighting', {}).get('overbright_vertices', 0) if include_colors else 0,
        'display_limit': 'modern linear/sRGB material; not PS2 byte-space framebuffer emulation',
        'runtime_shadows_flicker_and_fog': False,
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
