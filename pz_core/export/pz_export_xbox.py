"""Xbox-only export adapters; the shared/PS2 exporters remain unchanged."""
import copy
from pz_core.export.pz_export import export_obj as _export_obj


def _is_xbox_model(model):
    return (
        getattr(model, "xbox_native_mpx", False)
        or getattr(model, "xbox_native_pkx", False)
    )


def export_obj(model, output_path, include_vertex_colors=True, textures=None):
    # glTF/Three uses the stored top-left UVs, whereas OBJ expects bottom-left.
    # A shallow adapter changes only the convention flag, never cached source data.
    if _is_xbox_model(model):
        model = copy.copy(model)
        model.uvs_are_flipped = False
    return _export_obj(model, output_path, include_vertex_colors, textures)


def export_glb(model, output_path, *args, **kwargs):
    """Keep BC alpha visible in Xbox GLBs; no change for other model families.

    The source pixel alpha is exact. MASK/BLEND is a documented generic
    presentation policy, NOT a recovered Xbox render-state/shader command.
    """
    import json
    import struct
    from pz_core.export.pz_export import export_glb as legacy_export
    result = legacy_export(model, output_path, *args, **kwargs)
    if not _is_xbox_model(model):
        return result
    textures = kwargs.get("textures", args[2] if len(args) > 2 else None) or []
    with open(output_path, "rb") as stream:
        data = stream.read()
    json_size, kind = struct.unpack_from("<II", data, 12)
    if kind != 0x4E4F534A:
        raise ValueError("Xbox GLB adapter: exporter did not emit an initial JSON chunk")
    gltf = json.loads(data[20:20 + json_size])
    for source, material in zip(model.materials, gltf["materials"]):
        slot = source.texture_index
        if 0 <= slot < len(textures) and "A" in textures[slot].getbands():
            low, high = textures[slot].getchannel("A").getextrema()
            if low < 255:
                material["alphaMode"] = "MASK" if high == 255 else "BLEND"
                if high == 255:
                    material["alphaCutoff"] = 0.5
    format_name = "xbox_pkx" if getattr(model, "xbox_native_pkx", False) else "xbox_mpx"
    gltf.setdefault("extras", {})[format_name] = {
        "source_uvs": "top-left", "alpha": "faithfully decoded BC pixels",
        "material_passes": "generic cutout/blend policy; original Xbox states not reconstructed"}
    encoded = json.dumps(gltf, separators=(",", ":"), allow_nan=False).encode("utf-8")
    encoded += b" " * ((-len(encoded)) % 4)
    tail = data[20 + json_size:]
    with open(output_path, "wb") as stream:
        stream.write(struct.pack("<III", 0x46546C67, 2, 20 + len(encoded) + len(tail)))
        stream.write(struct.pack("<II", len(encoded), 0x4E4F534A))
        stream.write(encoded)
        stream.write(tail)
    return result
