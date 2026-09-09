# Project Zero - 3D Viewer & Extractor (PZViewer)

A standalone **Desktop GUI Application** for inspecting, rendering, and exporting Project Zero 1, 2 and 3 assets, built using `MikuPan` as technical reference.

---

## Highlights & GPU Architecture

- **Native Desktop GUI**:
  - Runs in its own standalone native desktop window via Microsoft Edge WebView2 (`edgechromium`).
  - No web browser tabs or address bar needed.
  - Smooth native window controls (resizable, custom dark theme, HUD).
- **GPU Hardware Accelerated Pipeline**:
  - Direct3D 11/12 hardware acceleration via dedicated GPU (NVIDIA / AMD / Intel).
  - Vertex data (positions, normals, UVs, vertex colors, indices) are uploaded directly into **GPU VBOs (Vertex Buffer Objects)** using `StaticDrawUsage` so they stay resident in GPU VRAM.
  - Textures are loaded and uploaded directly into **GPU VRAM** with mipmapping (`renderer.initTexture`).
  - Shaders are pre-compiled on GPU (`renderer.compile`) to eliminate render stutters.
  - Real-time **GPU hardware indicator** in the top bar displaying the active GPU device.

---

## Asset Features

- **Room Geometry (`.pk4` / `.pk2` / `.sgd`)**:
  - Extracts full room geometry from Project Zero files and SGD resources.
  - Preserves vertex colors and applies PS2 UV conventions.
- **Items, Furniture & Doors (`.sgd`)**:
  - Full support for VIF/SGD packets (0x10, 0x12, 0x32, 0x80, 0x82).
- **Characters (`.pk4` / `.mdl` / `.sgd`)**:
  - Unpacks nested PK4/PK2 sub-SGDs and TIM2/TM2 textures.
  - Automatically decodes PS2 palettes with CSM1 unswizzling and alpha correction.
  - Skeletal armature bones (`coordp` hierarchy).
- **BORKED | Animations (`.bmd`)**:
  - Project Zero 3 BMD motion loading and playback.
  - **T-Pose / Rest Pose Toggle**: Option to load models in pure T-Pose or play back all animation clips.
  - Load animations separately or detach them at any time.
- **BORKED | Collision Data**:
  - Room collision polygons (`msnXXmap.obj` hitcheck data).
  - SGD ProcUnit 4 bounding box colliders.
  - Character bone colliders (head, chest, waist spheres).
- **Multi-Format Export**:
  - **`.glb` / `.gltf`**: Blender-ready with standard `COLOR_0` vertex color attribute.
  - **`.obj`**: Wavefront OBJ with RGB vertex color extensions (`v x y z r g b`).
  - **`.dae`**: Collada 1.4 with color sources and skeleton nodes.
  - **`.fbx`**: ASCII FBX with `LayerElementColor` and bone clusters.

### SGD parser modules

SGD parsing is selected by the containing archive: PK4 payloads use
`pz_core.pz_sgd_ff3`, while PK2 payloads use `pz_core.pz_sgd_ff1`. The older
`pz_core.pz_sgd` and top-level `pz_sgd` imports remain compatibility facades.

---

## Asset folder selection and navigation

The viewer keeps one root folder for each game in `PZViewer_paths.json`, located
next to the executable (or next to `pz_viewer.py` when running from source):

```json
{
  "ff1": "C:/Games/Fatal Frame/3ddata",
  "ff2": "C:/Games/Fatal Frame 2/3ddata",
  "ff3": "C:/Games/Fatal Frame 3/3ddata"
}
```

The file is updated only when a folder is explicitly selected with the folder
picker. Browsing into a subfolder, refreshing the listing, entering a path
manually, or going back with `..` does not replace the saved root. For FF1,
FF2, and FF3, navigation is restricted to the saved root; the viewer will not
allow access to parent folders above it, including through a manually entered
path. Use the clear button beside a saved game folder to remove its entry.

The asset browser has separate entries for Fatal Frame 1, Fatal Frame 2, and
Fatal Frame 3. The FF2 entry accepts the PS2 container/resource extensions
currently understood by the browser (`.pk2`, `.sgd`, `.tim2`, and `.tm2`).

It is recommended to select the game's `3ddata` folder as the root rather than
one of its child folders. This matches the expected `room`, `character`,
`object`, `furniture`, `accessory`, and related asset layout, and allows the
viewer to resolve sibling SGD, texture, animation, and linked resource files
correctly.

---

## Quick Start (Desktop GUI Application)

### 1. Launch the Desktop App
Double-click `run_viewer.bat` or run:
```bash
python pz_viewer.py
```
This opens the standalone Native Desktop GUI window rendered directly on your GPU!

*(Optional: if you ever want to launch in your system browser instead of the desktop window, run `python pz_viewer.py --browser`)*

### 2. Viewport Controls
- **Left Mouse**: Rotate camera (Orbit).
- **Right Mouse**: Pan camera.
- **Scroll Wheel**: Zoom in/out.
- **F key**: Center / Fit camera to model.
- **Space**: Play / Pause animation.
- **T key**: Toggle T-Pose / Animated pose.
- **Left / Right Arrow**: Step backward / forward 1 frame.

---

## Command-Line Export (Batch Processing)

You can export assets directly without opening the GUI using `pz_export_cli.py`:

```bash
# Export room with vertex colors to GLB
# Export a Project Zero 3 room/model PK4 to GLB
python pz_export_cli.py "f:/Project Zero Modding/Obscura/bin/3ddata/room/r000_genkan.pk4" -o ./exported/r000.glb

# Export a Project Zero 3 SGD to OBJ
python pz_export_cli.py "f:/Project Zero Modding/Obscura/bin/3ddata/room/0000.sgd" -f obj -o ./exported/room.obj

# Export furniture or door to OBJ
python pz_export_cli.py "f:/Project Zero Modding/Obscura/bin/3ddata/furniture/f000_clock_l.sgd" -f obj -o ./exported/clock.obj
```

---

## Blender Import & Vertex Colors Guide

1. In Blender, go to **File > Import > glTF 2.0 (.glb/.gltf)** and select the exported `.glb` file.
2. In the 3D Viewport:
   - Change Viewport Shading to **Material Preview** or **Rendered**.
   - Or in Solid mode, click the Shading drop-down (top right of viewport) and set **Color** to **Attribute**.
3. Under **Object Data Properties** (green triangle icon) > **Color Attributes**, you will see `COLOR_0`. In Shader Editor, add an **Attribute** node set to `COLOR_0` and connect `Color` to `Base Color` of your Principled BSDF shader.
