# Project Zero / Fatal Frame 3D Viewer & Extractor

PZViewer is a standalone desktop application for inspecting, rendering, and
exporting 3D assets from the *Fatal Frame / Project Zero* games. It uses
`MikuPan` as a technical reference and renders through a GPU-accelerated
Three.js viewport hosted in a native Edge WebView2 window.

The project is an active reverse-engineering effort. Some formats and
animation systems are understood well enough for production use, while other
parts remain experimental.

## Progress

- [ ] Fatal Frame 1 / Project Zero 1: **5%**
- [ ] Fatal Frame 3 / Project Zero 3: **45%**

These percentages describe overall format discovery, decoding, rendering, and
export coverage rather than completion of any single feature.

## Highlights

- Native desktop GUI with no browser tabs or address bar.
- Direct3D 11/12 hardware acceleration through Edge WebView2.
- GPU-resident vertex buffers, textures, mipmaps, and precompiled shaders.
- Real-time GPU device indicator in the application header.
- Directory browser with direct loading of supported assets.
- Mesh-layer visibility controls, shading modes, collision visualization, and
  camera fitting.
- Batch export through `pz_export_cli.py`.

## Supported formats and features

### Fatal Frame 1 / Project Zero 1

- **Rooms (`.pk2`)**: extracts room geometry from `near_sgd` and related
  near/far/side streams. (Borked, nees rewrite)
- **Items, furniture, and doors (`.sgd`)**: decodes VIF/SGD packets including
  packet types `0x10`, `0x12`, `0x32`, `0x80`, and `0x82`.
- **Characters (`.mdl`)**: unpacks MPK sub-SGD data and associated PK2 TIM2
  textures.
- **Animations (`.anm`)**: decodes MOTN rotation, translation, and scaling
  tracks. (Borked)
- **Collision**: reads room hit-check polygons, SGD ProcUnit 4 bounding boxes,
  and character bone colliders. (Borked)

### Fatal Frame 3 / Project Zero 3

- **SGD assets**: loads model meshes, materials, bones, and textures.
- **PK4 assets**: unpacks PK4 model packages and resolves their embedded or
  adjacent SGD data.
- **PK2 assets**: supports room packages and their extracted model content.
- **CLD assets**: loads collision files directly from the browser and displays
  them as 3D models.
- **BMD assets**: parses the known header, hierarchy, base pose, track
  references, and record area while preserving experimental data for further
  research.
- **Room collision**: reads collision data from map files and extracted
  `02_cld` directories when available.

## CLD direct loading and primitive geometry

`.cld` files can be selected directly in the directory browser. The viewer
parses the compact collision records and creates separate meshes for each
validated primitive record instead of merging unrelated records into one
visual object.

Supported CLD primitive generation includes:

- axis-aligned boxes with visible wireframe bounds;
- spheres and other supported collider primitives;
- flat collision polygons;
- extruded room polygons with floor, ceiling, and perimeter wall faces.

The same collision meshes are used for viewport visualization and collision
export. This makes it possible to inspect collision geometry independently of
the source room or character model.

## Textures, UVs, and collision

- Decodes PS2 TIM2/TM2 palettes, including CSM1 unswizzling and alpha
  correction.
- Preserves pre-baked room vertex colors from `pVMCD->avColor` when available.
- Reads and bakes matching `.lit` SGD lighting data.
- Applies the required UV and degenerate-strip corrections for supported room
  data.
- Uses room-aware texture orientation and material settings.
- Displays boxes, polygons, spheres, and extruded collision meshes with
  dedicated collision materials.
- Supports toggling collision visibility independently from model visibility.

## Export

The desktop UI and command-line tools support:

- **`.glb` / `.gltf`**: Blender-ready glTF with standard `COLOR_0` vertex
  colors.
- **`.obj`**: Wavefront OBJ with RGB vertex-color extensions.
- **`.dae`**: Collada 1.4 with color sources and skeleton nodes.
- **`.fbx`**: ASCII FBX with `LayerElementColor` and bone clusters.
- Collision exports using the same GLB, OBJ, DAE, and FBX pipeline.
- Optional texture PNG export and configurable export destinations.

## BMD reverse-engineering status

The BMD parser is intentionally documented as provisional. Current knowledge
includes:

- `BMD\0` signature and little-endian values;
- frame, record, and bone counts;
- parent hierarchy table;
- provisional channel and track-reference tables;
- a fixed-size 224-byte record area in the examined samples;
- preservation and lookup of referenced packet data;
- extraction of the initial base pose when the expected block is present.

The exact relationship between records, packets, bones, frames, interpolation,
angle representation, and the 25-bone BMD hierarchy is not yet proven.
Animation playback therefore remains disabled for unverified BMD transforms;
the viewer keeps the model in its validated rest pose until the mapping is
confirmed. See [docs/bmd-format.md](docs/bmd-format.md) for the detailed
research notes.

## Quick start

### Launch the desktop application

Double-click `run_viewer.bat`, or run:

```bash
python pz_viewer.py
```

To use the system browser instead of the native desktop window:

```bash
python pz_viewer.py --browser
```

### Viewport controls

- **Left mouse button**: orbit the camera.
- **Right mouse button**: pan the camera.
- **Mouse wheel**: zoom in or out.
- **F**: fit and center the camera on the model.
- **Space**: play or pause animation.
- **T**: toggle T-pose or animated pose.
- **Left / Right Arrow**: step one animation frame backward or forward.

## Command-line export

Assets can be exported without opening the GUI through `pz_export_cli.py`:

```bash
# Export a Fatal Frame 1 room with vertex colors to GLB
python pz_export_cli.py "f:/Project Zero Modding/Obscura/bin/3ddata/room/r000_genkan.pk2" -o ./exported/r000.glb

# Export a character in T-pose to GLB
python pz_export_cli.py "f:/Project Zero Modding/Obscura/bin/3ddata/man/mdl/m000_miku.mdl" --tpose -o ./exported/miku_tpose.glb

# Export a character with animation to Collada
python pz_export_cli.py "f:/Project Zero Modding/Obscura/bin/3ddata/man/mdl/m000_miku.mdl" --anim "f:/Project Zero Modding/Obscura/bin/3ddata/man/anm/m000_miku.anm" -f dae -o ./exported/miku.dae

# Export furniture or a door to OBJ
python pz_export_cli.py "f:/Project Zero Modding/Obscura/bin/3ddata/furniture/f000_clock_l.sgd" -f obj -o ./exported/clock.obj
```

## Blender vertex-color guide

1. In Blender, choose **File > Import > glTF 2.0 (.glb/.gltf)** and select the
   exported file.
2. In the 3D Viewport, use **Material Preview** or **Rendered** shading. In
   Solid mode, set the Shading **Color** option to **Attribute**.
3. Under **Object Data Properties** (the green triangle), open **Color
   Attributes** and select `COLOR_0`. In the Shader Editor, connect an
   **Attribute** node set to `COLOR_0` to the Principled BSDF Base Color input.

