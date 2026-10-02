import os
import sys
import argparse

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pz_core.ff3.pz_pk4 import parse_pk4_model
from pz_core.common.pz_pk2 import unpack_room_pk2
from pz_core.ff2.pz_pk2_ff2 import unpack_room_pk2 as unpack_room_pk2_ff2
from pz_core.ff1.pz_sgd_ff1 import parse_sgd as parse_sgd_ff1
from pz_core.ff2.pz_sgd_ff2 import parse_sgd as parse_sgd_ff2
from pz_core.ff1.pz_mdl_ff1 import FF1MDLError, parse_ff1_mdl
from pz_core.export.pz_export import export_glb, export_obj, export_dae, export_fbx
from pz_core.ff1x.pz_mpx_ff1x import XboxMPXError, parse_mpx
from pz_core.ff1x.pz_xpr0 import XPR0Error
from pz_core.export.pz_export_xbox import (
    export_obj as export_xbox_aware_obj,
    export_glb as export_xbox_aware_glb,
)

def convert_file(
    input_path,
    output_dir,
    formats=['glb'],
    export_t_pose=True,
    include_vertex_colors=True,
    game='auto',
):
    os.makedirs(output_dir, exist_ok=True)
    ext = os.path.splitext(input_path)[1].lower()
    base_name = os.path.splitext(os.path.basename(input_path))[0]

    model = None
    textures = None

    print(f"Processing: {input_path}")

    if ext == '.pk2':
        unpack_room = unpack_room_pk2_ff2 if game == 'ff2' else unpack_room_pk2
        parse_sgd = parse_sgd_ff2 if game == 'ff2' else parse_sgd_ff1
        room = unpack_room(input_path)
        if room["near_sgd"] is None:
            print(f"  Error: PK2 does not contain a room SGD.")
            return
        lit_path = os.path.splitext(input_path)[0] + ".lit"
        lit_data = open(lit_path, "rb").read() if os.path.exists(lit_path) else None
        model = parse_sgd(room["near_sgd"], name=base_name, lit_data=lit_data)
    elif ext == '.pk4':
        is_room = '\\room\\' in os.path.normcase(os.path.abspath(input_path))
        model = parse_pk4_model(input_path, name=base_name, flip_uv=is_room)

    elif ext == '.sgd':
        with open(input_path, 'rb') as f:
            parse_sgd = parse_sgd_ff2 if game == 'ff2' else parse_sgd_ff1
            model = parse_sgd(f.read(), name=base_name)
    elif ext == '.mpx':
        try:
            xbox_result = parse_mpx(input_path, name=base_name)
        except (XboxMPXError, XPR0Error) as exc:
            print(f"  Error: Malformed or unsupported Xbox MPX/XPR: {exc}")
            return
        model = xbox_result.model
        textures = [picture['image'] for picture in xbox_result.textures]
        for warning in xbox_result.diagnostics['warnings']:
            print(f"  Warning: {warning}")
    elif ext in ('.mdl', '.mpk'):
        try:
            mdl_result = parse_ff1_mdl(input_path, name=base_name)
        except FF1MDLError as exc:
            print(f"  Error: Unsupported or malformed FF1 MDL: {exc}")
            return
        model = mdl_result.model
        textures = mdl_result.textures

    if not model or not model.meshes:
        print(f"  Error: No 3D meshes extracted from {input_path}")
        return

    # Export to requested formats
    for fmt in formats:
        fmt = fmt.lower().strip('.')
        out_file = os.path.join(output_dir, f"{base_name}.{fmt}")
        if fmt in ('glb', 'gltf'):
            export_xbox_aware_glb(model, out_file, export_t_pose=export_t_pose,
                       include_vertex_colors=include_vertex_colors, textures=textures)
        elif fmt == 'obj':
            export_xbox_aware_obj(model, out_file, include_vertex_colors=include_vertex_colors, textures=textures)
        elif fmt == 'dae':
            export_dae(model, out_file, export_t_pose=export_t_pose,
                       include_vertex_colors=include_vertex_colors)
        elif fmt == 'fbx':
            export_fbx(model, out_file, export_t_pose=export_t_pose,
                       include_vertex_colors=include_vertex_colors)
        print(f"  -> Exported: {out_file} ({os.path.getsize(out_file):,} bytes)")

def main():
    parser = argparse.ArgumentParser(description="Fatal Frame / Project Zero Model & Animation Exporter")
    parser.add_argument("input", help="Path to .mpx (Xbox), .mdl, .mpk, .pk2, .pk4, .sgd file or directory")
    parser.add_argument("-o", "--output", default="./exported", help="Output directory (default: ./exported)")
    parser.add_argument("-f", "--format", default="glb,obj", help="Comma-separated formats: glb,obj,dae,fbx (default: glb,obj)")
    parser.add_argument("--t-pose", action="store_true", default=True, help="Export character models in T-Pose (default: True)")
    parser.add_argument(
        "--game",
        choices=("auto", "ff1", "ff2", "ff3"),
        default="auto",
        help="Explicit parser family for .pk2/.sgd inputs (default: auto/legacy behavior)",
    )
    parser.add_argument("--no-vertex-colors", dest="vertex_colors", action="store_false", help="Disable vertex colors")
    parser.set_defaults(vertex_colors=True)

    args = parser.parse_args()
    formats = [f.strip() for f in args.format.split(',')]

    if os.path.isdir(args.input):
        for root, _, files in os.walk(args.input):
            for f in files:
                if os.path.splitext(f)[1].lower() in ('.mpx', '.mdl', '.mpk', '.pk2', '.pk4', '.sgd'):
                    convert_file(
                        os.path.join(root, f),
                        args.output,
                        formats,
                        args.t_pose,
                        args.vertex_colors,
                        args.game,
                    )
    else:
        convert_file(
            args.input,
            args.output,
            formats,
            args.t_pose,
            args.vertex_colors,
            args.game,
        )

if __name__ == "__main__":
    main()
