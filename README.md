# Project Zero / Fatal Frame 1 - 3D Viewer & Extractor (PZViewer)

A standalone **Desktop GUI Application** designed for inspecting, rendering, and exporting 3D assets from *Fatal Frame / Project Zero 1* (PS2) on dedicated **hardware GPU**, built using `MikuPan` as technical reference.

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

- **Room Geometry (`.pk2`)**:
  - Extracts full room geometry from `near_sgd` (and far/ss/sh).
  - **Vertex Colors Preserved**: Automatically extracts pre-baked vertex colors (`pVMCD->avColor`) or reads/bakes lighting data from matching `.lit` SGD files (`SetPreRender`).
  - Degenerate strip color and UV fixups (`MikuPan_FixColors`).
- **Items, Furniture & Doors (`.sgd`)**:
  - Full support for VIF/SGD packets (0x10, 0x12, 0x32, 0x80, 0x82).
- **Characters (`.mdl`)**:
  - Unpacks MPK sub-SGDs and PK2 TIM2 textures.
  - Automatically decodes PS2 TIM2/TM2 palettes with CSM1 unswizzling and alpha correction.
  - Skeletal armature bones (`coordp` hierarchy).
- **Animations (`.anm`)**:
  - Full `MOTN` keyframe stream decoding (rotation, translation, scaling tracks).
  - **T-Pose / Rest Pose Toggle**: Option to load models in pure T-Pose or play back all animation clips.
  - Load animations separately or detach them at any time.
- **Collision Data**:
  - Room collision polygons (`msnXXmap.obj` hitcheck data).
  - SGD ProcUnit 4 bounding box colliders.
  - Character bone colliders (head, chest, waist spheres).
- **Multi-Format Export**:
  - **`.glb` / `.gltf`**: Blender-ready with standard `COLOR_0` vertex color attribute.
  - **`.obj`**: Wavefront OBJ with RGB vertex color extensions (`v x y z r g b`).
  - **`.dae`**: Collada 1.4 with color sources and skeleton nodes.
  - **`.fbx`**: ASCII FBX with `LayerElementColor` and bone clusters.

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
python pz_export_cli.py "f:/Project Zero Modding/Obscura/bin/3ddata/room/r000_genkan.pk2" -o ./exported/r000.glb

# Export character in T-Pose to GLB
python pz_export_cli.py "f:/Project Zero Modding/Obscura/bin/3ddata/man/mdl/m000_miku.mdl" --tpose -o ./exported/miku_tpose.glb

# Export character with animation to Collada / DAE
python pz_export_cli.py "f:/Project Zero Modding/Obscura/bin/3ddata/man/mdl/m000_miku.mdl" --anim "f:/Project Zero Modding/Obscura/bin/3ddata/man/anm/m000_miku.anm" -f dae -o ./exported/miku.dae

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
