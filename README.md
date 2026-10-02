# Project Zero / Fatal Frame 3D Viewer (PZViewer)

A standalone **desktop viewer** for inspecting, rendering and exporting
Project Zero / Fatal Frame assets. Runs in its own native window through
Microsoft Edge WebView2, renders through WebGL on the GPU, and needs no web
browser tab.

---

## Games

| Entry | Games | Containers |
|---|---|---|
| **Original** | Fatal Frame 1 (PS2) | `.mdl`, `.pk2`, `.sgd`, `.tim2`|
| | Fatal Frame 2 (PS2) | `.pk2`, `.sgd`, `.tim2`, `.tm2` |
| | Fatal Frame 3 (PS2) | `.pk4`, `.sgd`, `.tm2` |
| **Extra** | Fatal Frame 1 XBOX | `.mpx` |
| | Fatal Frame 2 Wii | `.mdlb`, `.pk2b` |

The two Xbox/Wii ports live on their own tab because each is a port of a game
that already has an entry, and each uses a different container set. Two
"Fatal Frame 2" rows side by side with nothing to tell them apart is worse than
a second tab.

### Fatal Frame 1 XBOX

`.mpx` files carry geometry and their XPR0 textures together, and are read
natively: real UVs, triangle-strip indices, weighted bones and BC3 textures
with alpha. The `.mdl` files that ship next to them share the PK2_HEAD
container with the PS2 originals but use geometry records the PS2 parser does
not read, so they are hidden from the tree; two of the 64 parse if you type
the path by hand.

Character .mdl files are handled by the PS2 parser and come out geometrically
identical to the PS2 original, only without textures, because the surfaces live
in the embedded archive.

The container layout is written up in
[docs/ff1x-sgd1060.md](docs/ff1x-sgd1060.md) and the texture archive in
[docs/xbox-xpr.md](docs/xbox-xpr.md). Both describe the byte layout, so they
stay valid even though the sample files they were measured against are gone.

### Fatal Frame 2 Wii

Each model ships in its own unit scale, so a per-kind factor is applied on
load (character, room, prop) to bring them into the same range as the FF3
assets. The rig scale is baked into the geometry rather than the mesh nodes.

---

## Quick start

```bash
python pz_viewer.py
```

Or double-click `run_viewer.bat`. Add `--browser` to open in your own browser
instead of the native window.

Then pick a game folder once with **Select** on its row in the Asset Browser;
the row remembers it, **Update** points it somewhere else and **✖** forgets it.
Selecting the game's `3ddata` folder as the root is recommended, so sibling
SGD, texture and linked resource files resolve correctly.

---

## Asset browser

- **Original / Extra** tabs over the saved game folders; the active tab is
  remembered.
- **Reload** re-reads the current folder.
- The filter box searches the listing.
- Navigation never leaves the saved root. At a root there is no
  `.. (Parent Directory)` row, and the up control is gone — a folder with
  nothing loadable in it offers **`... (Parent Folder)`** instead.

---

## Viewport controls

The shading looks are toggles rather than a picker: each button owns two
appearances and the button pressed last is the one in force.

| Control | Key | Behaviour |
|---|---|---|
| **Textures** | `T` | textured ⇄ solid white |
| **Wireframe** | `W` | textured → wireframe drawn over the model → wireframe alone |
| **Vertex Colors** | `V` | texture with the baked vertex colours, or on its own |
| **Bones** | `B` | skeleton on or off, drawn over the model |
| **Layers** | `L` | the material list |
| **Reset Cam** | `F` | frame the model again; the orbit inertia is killed so it stops dead |
| recolour | `←` `→` | previous / next palette, on the assets that have more than one |

Drag to orbit, right-drag to pan, wheel to zoom.

The wireframe "over the model" step is a **second pass**: extra meshes sharing
the model's geometry with depth testing off, because one material cannot draw
a shaded surface and a cage over it. Backface culling is on everywhere.

### Material layers

The Layers panel lists one row per **material**, not per strip of geometry: a
character is ~128 submeshes drawn with ~30 materials, and what is worth
switching is the material — a face, a sleeve, a piece of set dressing. Each row
shows how many submeshes it draws.

Rows are grouped by name **and** texture slot, which collapses the genuine
duplicates these files contain (four materials named `m001_hair01` all pointing
at the same page become one row) while keeping materials that merely share a
name but draw different pages apart.

Drag the panel by its title bar to move it — the position is remembered — and
`▾` shrinks it to the title while you keep working. The panel is a floating
tool, not a modal: the layer behind it does not intercept clicks, so the 3D
viewport stays interactive with the list open, minimised or not.

### Texture VRAM slots

One square slot per decoded page. Click one to open it large; close it with
**✖**, **Escape**, or a click outside. **Extract PNGs** writes every page of
the loaded model to a zip.

### Recolour

Some Xbox `.mpx` assets have several XPR archives holding the same geometry in
different colourways. A recolour button appears when the model on screen has
them, and cycles through them; `←` and `→` go both ways. For an asset with no
same-stem archive, the first declared palette is bound by default rather than
loading untextured.

---

## Themes

**Themes** picks a palette or **Dynamic** to follow the folder you are
browsing. Each game has its own: FF1, FF1 XBOX (the FF1 palette shifted a
little), FF2 (amber and parchment, from the "Play Data" menu), FF2 Wii (crimson
Butterfly), FF3 (cold gold). The icon at the top left is the camera of the game
on screen.

---

## Export

**Export 3D Model** writes **glTF 2.0**, **Wavefront OBJ**, **COLLADA** or
**FBX**. Vertex colours are always preserved and the textures are always
extracted as PNG next to the file — neither is an option.

Collision export was removed: the 2D half-plane data these builds carry
converts into something not yet useful, so the target picker is hidden rather
than left selectable. T-pose export was removed for the same kind of reason.

**Batch Convert** does a whole folder at once: pick the format, leave it
recursive to keep the subfolder structure, and choose whether an existing file
is skipped or overwritten.

### Blender

1. **File > Import > glTF 2.0 (.glb/.gltf)**.
2. Viewport shading to **Material Preview** or **Rendered**; in Solid mode set
   **Color** to **Attribute**.
3. **Object Data Properties > Color Attributes** shows `COLOR_0`. In the shader
   editor, an **Attribute** node set to `COLOR_0` into **Base Color**.

---

## Command-line export

```bash
python pz_export_cli.py "f:/r3data/room/r000_genkan.pk4" -o ./exported/r000.glb
python pz_export_cli.py "f:/r3data/room/0000.sgd" -f obj -o ./exported/room.obj
python pz_export_cli.py "f:/r1x/man/mdl/m000_miku4.mpx" -f glb,obj -o ./exported
```

---

## GPU pipeline

- Direct3D 11/12 hardware acceleration; vertex data is uploaded into GPU VBOs
  with `StaticDrawUsage` so it stays resident in VRAM.
- Textures are uploaded into VRAM with mipmapping and **linear magnification**
  on every model: these atlases are small and get stretched over whole
  surfaces, and nearest sampling breaks them into texel blocks that are not in
  the game. Minification keeps nearest plus mipmaps, which is what avoids the
  aliasing linear sampling would introduce when shrinking.
- Shaders are pre-compiled on the GPU to avoid render stutters.

---

## Known limitations

- **FF1 non-character textures come out grey.** Every FF1 item, door and
  furniture page decodes to neutral grey across all 24 assets. Vertex colours
  and material channels are grey too, so it is not a shading setting. GS VRAM
  addressing and PSMT8 swizzling match the reference implementation byte for
  byte, which applies no colour modulation either. Not reproduced.
- The camera fit has a 25-unit minimum distance, so small FF2 Wii props render
  small in the viewport.
- FF1 room sheets carry colour in the vertex buffer rather than a texture, so
  they preview untextured.

---

## Project layout

```
pz_viewer.py            native-window launcher
pz_export_cli.py        command-line export
run_viewer.bat          Windows launcher
PZViewer_paths.json     saved game roots
viewer/                 HTTP server and the whole UI
  server.py             parsing, serialisation, export, file browsing
  static/app.js         scene, viewport, layers panel, theme picker
  static/index.html     markup
  static/style.css      styles and the five palettes
  static/*.png          the viewer icon and the per-game camera icons
pz_core/                the parsers
docs/                   Xbox format specifications and delivery notes
tools/                  analysis scripts referenced by docs/
```

---

## Credits

- **Original idea** — DaniKH
- **Collaborators** — [Yokimitsuro](https://x.com/Yokimitsuro) · Scream\*Alyx\*Psalm
- **Reference documentation** — [Obscura](https://github.com/Mikompilation/Obscura) ·
  [Mikupan](https://github.com/Mikompilation/MikuPan) ·
  [Mikompilation](https://github.com/Mikompilation/Mikompilation)
- **Assembler and builder of the tool** — Claude

Fatal Frame / Project Zero assets belong to their respective publishers. This
viewer reads the files you already own; it ships no game data.
