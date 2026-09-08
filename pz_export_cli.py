import os
import sys
import argparse
import struct

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pz_core.pz_pk2_pz1 import unpack_room_pk2_pz1
from pz_core.pz_pk4 import parse_pk4_model
from pz_core.pz_sgd import parse_sgd
from pz_core.pz_mdl import parse_mdl
from pz_core.pz_anm import parse_anm
from pz_core.pz_export import export_glb, export_obj, export_dae, export_fbx

def convert_file(input_path, output_dir, formats=['glb'], export_t_pose=True, include_vertex_colors=True):
    os.makedirs(output_dir, exist_ok=True)
    ext = os.path.splitext(input_path)[1].lower()
    base_name = os.path.splitext(os.path.basename(input_path))[0]

    model = None
    textures = None
    animations = None

    print(f"Processing: {input_path}")

    if ext == '.pk2':
        # Check if room PK2 or generic
        room = unpack_room_pk2_pz1(input_path)
        if room['near_sgd']:
            lit_path = os.path.splitext(input_path)[0] + '.lit'
            lit_data = None
            if os.path.exists(lit_path):
                with open(lit_path, 'rb') as lf:
                    lit_data = lf.read()
            try:
                model = parse_sgd(room['near_sgd'], name=base_name, lit_data=lit_data)
            except struct.error:
                model = None
        else:
            print(f"  Warning: PK2 did not contain room SGD geometry.")
            return

    elif ext == '.pk4':
        is_room = '\\room\\' in os.path.normcase(os.path.abspath(input_path))
        model = parse_pk4_model(input_path, name=base_name, flip_uv=is_room)

    elif ext == '.sgd':
        with open(input_path, 'rb') as f:
            model = parse_sgd(f.read(), name=base_name)

    elif ext == '.mdl':
        model = parse_mdl(input_path, name=base_name)
        if model:
            textures = model.textures

    elif ext == '.anm':
        animations = parse_anm(input_path)
        print(f"  Parsed {len(animations)} animation clips from {input_path}")
        return

    if not model or not model.meshes:
        print(f"  Error: No 3D meshes extracted from {input_path}")
        return

    # Export to requested formats
    for fmt in formats:
        fmt = fmt.lower().strip('.')
        out_file = os.path.join(output_dir, f"{base_name}.{fmt}")
        if fmt in ('glb', 'gltf'):
            export_glb(model, out_file, export_t_pose=export_t_pose, animations=animations,
                       include_vertex_colors=include_vertex_colors, textures=textures)
        elif fmt == 'obj':
            export_obj(model, out_file, include_vertex_colors=include_vertex_colors, textures=textures)
        elif fmt == 'dae':
            export_dae(model, out_file, export_t_pose=export_t_pose, animations=animations,
                       include_vertex_colors=include_vertex_colors)
        elif fmt == 'fbx':
            export_fbx(model, out_file, export_t_pose=export_t_pose, animations=animations,
                       include_vertex_colors=include_vertex_colors)
        print(f"  -> Exported: {out_file} ({os.path.getsize(out_file):,} bytes)")

def main():
    parser = argparse.ArgumentParser(description="Fatal Frame / Project Zero Model & Animation Exporter")
    parser.add_argument("input", help="Path to .pk2, .sgd, .mdl file or directory")
    parser.add_argument("-o", "--output", default="./exported", help="Output directory (default: ./exported)")
    parser.add_argument("-f", "--format", default="glb,obj", help="Comma-separated formats: glb,obj,dae,fbx (default: glb,obj)")
    parser.add_argument("--t-pose", action="store_true", default=True, help="Export character models in T-Pose (default: True)")
    parser.add_argument("--no-vertex-colors", dest="vertex_colors", action="store_false", help="Disable vertex colors")
    parser.set_defaults(vertex_colors=True)

    args = parser.parse_args()
    formats = [f.strip() for f in args.format.split(',')]

    if os.path.isdir(args.input):
        for root, _, files in os.walk(args.input):
            for f in files:
                if os.path.splitext(f)[1].lower() in ('.pk2', '.sgd', '.mdl', '.anm'):
                    convert_file(os.path.join(root, f), args.output, formats, args.t_pose, args.vertex_colors)
    else:
        convert_file(args.input, args.output, formats, args.t_pose, args.vertex_colors)

if __name__ == "__main__":
    main()
