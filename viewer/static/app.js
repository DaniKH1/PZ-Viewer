/**
 * PZViewer - GPU Accelerated 3D Engine for Project Zero / Fatal Frame Assets
 */

// Theme support. The active theme is a data-theme attribute on <html>, so
// switching it only re-points CSS custom properties: no layout, geometry or
// WebGL state is involved. PZ_THEME_KEY is duplicated by the inline snippet in
// index.html that applies the stored theme before the first paint, which is
// what stops a reload from flashing the wrong palette.
const PZ_THEME_KEY = 'pzviewer.theme';
// Picker order, 'dynamic' last on purpose: it is a mode rather than a palette,
// so it belongs after the four concrete choices.
const PZ_THEMES = ['default', 'ff1', 'ff1x', 'ff2', 'ff2w', 'ff3', 'dynamic'];
// The theme picker's contents, in the order they are listed. No icons: the list
// is a list of names, and the picture that stands for each game lives in the
// top-left corner instead, where it says which game is actually on screen rather
// than adding a column of decoration to a menu.
//
// The icon stays here because this is the one place that knows which picture
// belongs to which theme. 'img:' marks a file, anything else is a literal emoji,
// and a theme with neither keeps the viewer's own icon in the corner.
// FF1 XBOX borrows FF1's camera on purpose: it is the same game on a different
// machine, and it is meant to look like FF1.
// The theme-*.png files are the supplied pictures fitted to a square with their
// backgrounds already removed; they ship only for the games that have one.
const PZ_THEME_ITEMS = [
  { id: 'default', label: 'Default', icon: '' },
  { id: 'ff1', label: 'FF1', icon: 'img:theme-ff1.png' },
  { id: 'ff1x', label: 'FF1 XBOX', icon: 'img:theme-ff1.png' },
  { id: 'ff2', label: 'FF2', icon: 'img:theme-ff2.png' },
  { id: 'ff2w', label: 'FF2 Wii', icon: 'img:theme-ff2w.png' },
  { id: 'ff3', label: 'FF3', icon: 'img:theme-ff3.png' },
  { id: 'dynamic', label: 'Dynamic', icon: 'img:app-icon.png' }
];
// What the corner shows for a theme with no picture of its own.
const PZ_FALLBACK_ICON = 'app-icon.png';
// What the viewer starts on when nothing has been stored yet. Kept apart from
// PZ_THEMES[0] so the picker order can stay as above while the default is
// 'dynamic', which paints the palette of the game folder being browsed.
const PZ_DEFAULT_THEME = 'dynamic';
// 'dynamic' is a choice, not a palette: it resolves to the theme of whichever
// game folder the Asset Browser is currently pointed at, so the UI adopts the
// palette of the game you are actually working on. PZ_DYNAMIC_KEY remembers
// that folder across reloads so the first paint already resolves correctly.
const PZ_DYNAMIC_KEY = 'pzviewer.dynamicGame';
const PZ_DYNAMIC_FALLBACK = 'ff3';
// Asset Browser rows. "ff2w" is the Wii release of Fatal Frame 2: its models
// live in .mdlb / .pk2b containers, so it browses a different extension set
// but reuses the same folder plumbing as the PS2 releases.
const PZ_GAMES = [
  { id: 'ff1', label: 'Fatal Frame 1 Files' },
  { id: 'ff1x', label: 'Fatal Frame 1 XBOX Files' },
  { id: 'ff2', label: 'Fatal Frame 2 Files' },
  { id: 'ff2w', label: 'Fatal Frame 2 Wii Files' },
  { id: 'ff3', label: 'Fatal Frame 3 Files' },
];
// The Asset Browser's saved roots, split over two tabs.
//
// 'Original' holds the three PS2 releases. 'Extra' holds the two ports, which
// are kept off the main list on purpose: each is a port of a game that already
// has a tab, each browses a different container set (.mpx for the FF1 Xbox
// build, .mdlb / .pk2b for FF2 Wii), and side by side two "Fatal Frame 2" rows
// with nothing to tell them apart is worse than a second tab. The groups are
// declared by id rather than by slicing the list above, so the split survives a
// game being added, removed or renamed.
const PZ_ROOT_TABS = [
  { id: 'original', label: 'Original', games: ['ff1', 'ff2', 'ff3'] },
  { id: 'extra', label: 'Extra', games: ['ff1x', 'ff2w'] }
];
const PZ_ROOT_TAB_KEY = 'pzviewer.rootTab';
const PZ_DEFAULT_ROOT_TAB = 'original';
const PZ_GAME_IDS = PZ_GAMES.map((g) => g.id);
// Which theme a game resolves to under Dynamic. The two ports get their own: the
// Wii release of Fatal Frame 2 has the crimson Butterfly menu where the PS2 build
// has the amber "Play Data" one, and FF1's Xbox build is the same game with the
// palette shifted, so it sits next to FF1 rather than replacing it.
const PZ_GAME_THEME = {
  ff1: 'ff1', ff1x: 'ff1x', ff2: 'ff2', ff2w: 'ff2w', ff3: 'ff3'
};

function is3ddataDirectory(path) {
  const normalized = String(path || '').replace(/\\/g, '/').replace(/\/+$/, '');
  return normalized.slice(normalized.lastIndexOf('/') + 1).toLowerCase() === '3ddata';
}

/**
 * A readable name for one layer group. These formats name their materials
 * ("m000_sodena", "m001_eye02.tm2"), so that is what a row shows; when the
 * parser recovered no name, the texture slot is the next most useful thing,
 * because that is what decides what the surface looks like.
 */
/**
 * The shading looks, as the steps each viewport button cycles through.
 *
 * The picker this replaced offered one list of every mode, which meant
 * remembering which entry held which look. Each button now steps through the
 * appearances it owns, and whichever button was pressed last is the one in force
 * -- so exactly one mode is ever on screen.
 *
 * The wireframe button has three stops rather than two because wireframing is
 * only half a decision: 'textured' is its off position, 'wireframe_over' draws
 * the blue cage on top of the textured model, and 'wireframe' drops the model
 * and leaves the cage alone. 'wireframe_over' is a second pass -- see
 * applyShadingMode() -- because one material cannot draw a shaded surface and a
 * cage over it.
 */
const SHADING_STEPS = {
  textures: ['textured', 'solid'],
  wireframe: ['textured', 'wireframe_over', 'wireframe'],
};

function materialName(group) {
  const material = group.material;
  if (material && material.name) return material.name;
  if (material && Number.isInteger(material.texture_index)) {
    return `Texture ${material.texture_index}`;
  }
  if (Number.isInteger(group.texId) && group.texId >= 0) return `Texture ${group.texId}`;
  return `Material ${group.materialIndex}`;
}

class PZViewerApp {
  constructor() {
    this.container = document.getElementById('viewport');
    this.currentModelData = null;
    this.textures = {};
    this.selectedBrowserPath = '';

    this.shadingMode = 'textured';
    // Vertex colours live outside the shading picker now: they are a modifier on
    // top of Textures, toggled with V or the button beside the picker.
    this.vertexColorsOn = false;
    this.exportDestination = '';
    this.showBones = false;
    this.currentTheme = this.readStoredTheme();
    this.themeSelectionRevision = 0;
    // Xbox recolour support: which archive is bound, and the asset it came from.
    this.currentSourcePath = '';
    this.xprVariantList = null;
    this.xprVariantIndex = -1;

    // Layers panel, grouped by material. Rebuilt on every load.
    this.materialLayerGroups = [];
    // Which tab of the Asset Browser's saved roots is showing ('original' or
    // 'extra'); read from storage by initRootTabs().
    this.currentRootTab = PZ_DEFAULT_ROOT_TAB;
    // VRAM slot currently enlarged in the lightbox, or null.
    this.currentTexturePreview = null;
    this.dataFolderNoticeResolver = null;

    this.initThreeGPU();
    this.initUI();
    this.initEventListeners();
    this.restoreThemePreference();
    this.restoreGamePaths();

    this.animate = this.animate.bind(this);
    requestAnimationFrame(this.animate);

    const inputDir = document.getElementById('input-dir');
    if (inputDir && inputDir.value.trim()) {
      this.browseDir(inputDir.value.trim());
    }
  }

  initThreeGPU() {
    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
      stencil: false,
      depth: true
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0f1013);

    const aspect = this.container.clientWidth / this.container.clientHeight;
    this.camera = new THREE.PerspectiveCamera(55, aspect, 0.5, 60000);
    this.camera.position.set(0, 140, 360);

    this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;

    this.ambientLight = new THREE.AmbientLight(0xffffff, 0.75);
    this.scene.add(this.ambientLight);

    this.dirLight = new THREE.DirectionalLight(0xffffff, 0.85);
    this.dirLight.position.set(120, 260, 180);
    this.scene.add(this.dirLight);

    this.scene.add(this.camera);

    this.grid = new THREE.GridHelper(1200, 60, 0x3f4458, 0x242731);
    this.grid.position.y = 0;
    this.scene.add(this.grid);

    this.axes = new THREE.AxesHelper(40);
    this.scene.add(this.axes);

    this.modelGroup = new THREE.Group();
    this.bonesGroup = new THREE.Group();
    this.collisionGroup = new THREE.Group();

    this.bonesGroup.visible = false;
    this.collisionGroup.visible = false;

    this.scene.add(this.modelGroup);
    this.scene.add(this.bonesGroup);
    this.scene.add(this.collisionGroup);
    // The wireframe cage drawn over the textured model. Kept apart from
    // modelGroup so it can be emptied and rebuilt on every look change without
    // disturbing the layer panel, which indexes into modelGroup's children.
    this.wireframeGroup = new THREE.Group();
    this.scene.add(this.wireframeGroup);

    window.addEventListener('resize', () => {
      if (!this.container) return;
      const w = this.container.clientWidth;
      const h = this.container.clientHeight;
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(w, h);
    });
  }

  initUI() {
    // ── Force modal layout fix (bypasses CSS/HTML cache) ──────────────────
    const modalContent = document.querySelector('.modal-content');
    const modalBody    = document.querySelector('.modal-body');
    const modalHeader  = document.querySelector('.modal-header');
    const modalFooter  = document.querySelector('.modal-footer');
    if (modalContent) {
      Object.assign(modalContent.style, {
        maxHeight: '90vh', display: 'flex', flexDirection: 'column', overflow: 'hidden'
      });
    }
    if (modalHeader) modalHeader.style.flexShrink = '0';
    if (modalBody)   Object.assign(modalBody.style, { flex: '1 1 auto', overflowY: 'auto' });
    if (modalFooter) modalFooter.style.flexShrink = '0';
    // ──────────────────────────────────────────────────────────────────────

    document.querySelectorAll('.btn-quick').forEach(btn => {
      btn.addEventListener('click', () => {
        const path = btn.getAttribute('data-path');
        this.loadFile(path);
      });
    });

    const dirInput = document.getElementById('input-dir');
    const dirGo = document.getElementById('btn-dir-go');
    const refreshBtn = document.getElementById('btn-browse-refresh');
    const dirUpBtn = document.getElementById('btn-dir-up');
    const fileFilter = document.getElementById('file-filter');

    if (dirGo && dirInput) {
      dirGo.addEventListener('click', () => this.browseDir(dirInput.value));
      dirInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') this.browseDir(dirInput.value);
      });
    }
    if (refreshBtn && dirInput) {
      refreshBtn.addEventListener('click', () => this.browseDir(dirInput.value));
    }
    if (dirUpBtn) {
      dirUpBtn.addEventListener('click', () => {
        if (this.currentParentDir) {
          this.browseDir(this.currentParentDir);
        }
      });
    }
    if (fileFilter) {
      fileFilter.addEventListener('input', () => this.filterBrowserItems(fileFilter.value));
    }

    const shadeTexturesBtn = document.getElementById('btn-shade-textures');
    if (shadeTexturesBtn) {
      shadeTexturesBtn.addEventListener('click', () => this.cycleShadingButton('textures'));
    }
    const shadeWireframeBtn = document.getElementById('btn-shade-wireframe');
    if (shadeWireframeBtn) {
      shadeWireframeBtn.addEventListener('click', () => this.cycleShadingButton('wireframe'));
    }

    const vcolorsBtn = document.getElementById('btn-vcolors');
    if (vcolorsBtn) {
      vcolorsBtn.addEventListener('click', () => this.toggleVertexColors());
    }

    // Texture preview: the backdrop and the button both close it, and Escape is
    // handled with the other shortcuts.
    const previewClose = document.getElementById('btn-texture-preview-close');
    if (previewClose) {
      previewClose.addEventListener('click', () => this.closeTexturePreview());
    }
    const previewBackdrop = document.getElementById('texture-preview-backdrop');
    if (previewBackdrop) {
      previewBackdrop.addEventListener('click', () => this.closeTexturePreview());
    }

    const bonesBtn = document.getElementById('btn-bones');
    if (bonesBtn) {
      bonesBtn.addEventListener('click', () => this.toggleBones());
    }

    this.initThemePicker();
    // The tabs have to exist before restoreGamePaths() asks which tab is active:
    // that decides which folder the browser opens on load.
    this.initRootTabs();

    const layersModal = document.getElementById('layers-modal');
    const minimizeBtn = document.getElementById('btn-layers-minimize');
    document.getElementById('btn-mesh-layers')?.addEventListener('click', () => layersModal?.classList.remove('hidden'));
    document.getElementById('btn-close-layers')?.addEventListener('click', () => {
      // Closing resets the minimised state too, so the Layers button always
      // brings the list back rather than an empty title bar.
      layersModal?.classList.add('hidden');
      layersModal?.classList.remove('minimized');
      if (minimizeBtn) minimizeBtn.textContent = '▾';
    });
    // Minimise keeps the panel open but shrinks it to its title bar, so the
    // material list stops covering the viewport while the model is inspected.
    if (minimizeBtn) {
      minimizeBtn.addEventListener('click', () => {
        const minimized = layersModal.classList.toggle('minimized');
        minimizeBtn.textContent = minimized ? '▸' : '▾';
        minimizeBtn.title = minimized ? 'Expand the layers panel' : 'Minimise the layers panel';
      });
    }
    this.initLayersPanelDrag(layersModal);
    document.getElementById('btn-layers-all')?.addEventListener('click', () => this.setAllMeshLayers(true));
    document.getElementById('btn-layers-none')?.addEventListener('click', () => this.setAllMeshLayers(false));

    const resetCam = document.getElementById('btn-reset-cam');
    if (resetCam) {
      resetCam.addEventListener('click', () => this.fitCameraToModel());
    }

    const xprVariant = document.getElementById('btn-xpr-variant');
    if (xprVariant) {
      xprVariant.addEventListener('click', () => this.cycleXprVariant());
    }

    const btnExportModal = document.getElementById('btn-export-modal');
    const modal = document.getElementById('export-modal');
    const btnModalClose = document.getElementById('btn-modal-close');
    const btnModalCancel = document.getElementById('btn-modal-cancel');
    const btnModalDoExport = document.getElementById('btn-modal-do-export');

    if (btnExportModal && modal) {
      btnExportModal.addEventListener('click', () => {
        if (!this.currentModelData) {
          this.showToast('No model loaded to export!', 'error');
          return;
        }
        const modelRadio = document.querySelector('input[name="export-target"][value="model"]');
        if (modelRadio) modelRadio.checked = true;
        this.updateExportTargetUI();
        modal.classList.remove('hidden');
      });
    }

    const closeModal = () => modal && modal.classList.add('hidden');
    if (btnModalClose) btnModalClose.addEventListener('click', closeModal);
    if (btnModalCancel) btnModalCancel.addEventListener('click', closeModal);
    if (btnModalDoExport) btnModalDoExport.addEventListener('click', () => this.executeExport());

    const btnExtractTex = document.getElementById('btn-extract-textures');
    if (btnExtractTex) {
      btnExtractTex.addEventListener('click', async () => {
        if (!this.currentModelData || !this.currentModelData.textures || this.currentModelData.textures.length === 0) {
          this.showToast('No textures loaded to extract for this model!', 'error');
          return;
        }
        this.showLoading('Extracting and converting textures to PNG...');
        try {
          const res = await fetch('/api/export_textures', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              textures: this.currentModelData.textures
            })
          });
          if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || 'Extraction failed');
          }
          const blob = await res.blob();
          const base = this.currentModelData.filename || 'model';
          const filename = `${base}_textures.zip`;
          const url = window.URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          a.remove();
          window.URL.revokeObjectURL(url);
          this.showToast(`Saved ${filename} & extracted to exports/${base}_textures/!`, 'success');
        } catch (err) {
          this.showToast('Extraction error: ' + err.message, 'error');
        } finally {
          this.hideLoading();
        }
      });
    }
  }

  /**
   * Reads the persisted theme name, falling back to the default one. The
   * attribute itself is already on <html> from the inline <head> snippet, so
   * this only keeps the picker and the stylesheet in sync.
   */
  readStoredTheme() {
    let stored = '';
    try {
      stored = localStorage.getItem(PZ_THEME_KEY) || '';
    } catch (err) {
      console.warn('Saved theme could not be read:', err);
    }
    return PZ_THEMES.indexOf(stored) !== -1 ? stored : PZ_DEFAULT_THEME;
  }

  async restoreThemePreference() {
    const revision = this.themeSelectionRevision;
    try {
      const response = await fetch('/api/theme?_=' + Date.now(), { cache: 'no-store' });
      if (!response.ok) throw new Error('Saved theme could not be loaded.');
      const data = await response.json();
      if (revision === this.themeSelectionRevision &&
          PZ_THEMES.indexOf(data.theme) !== -1 && data.theme !== this.currentTheme) {
        this.applyTheme(data.theme, false);
      }
    } catch (err) {
      console.warn('Server theme preference could not be read:', err);
    }
  }

  /**
   * The palette that `data-theme` should actually carry. For every concrete
   * theme this is the theme itself; for 'dynamic' it is the palette of the
   * game currently being browsed.
   */
  effectiveTheme() {
    if (this.currentTheme !== 'dynamic') return this.currentTheme;
    const game = this.currentBrowserGame || this.readDynamicGame();
    return PZ_GAME_THEME[game] || PZ_DYNAMIC_FALLBACK;
  }

  readDynamicGame() {
    try {
      return localStorage.getItem(PZ_DYNAMIC_KEY) || '';
    } catch (err) {
      return '';
    }
  }

  /**
 * The theme picker: a button that opens a list, built from PZ_THEME_ITEMS.
 *
 * It was a <select>, which cannot hold the pictures the rows need -- the two
 * Fatal Frame 2 cameras, or the viewer's own icon for 'dynamic' -- because an
 * <option> only takes text. Built here rather than in the markup so the list,
 * the icons and the active check stay in one place with the theme list itself.
 */
initThemePicker() {
    const button = document.getElementById('btn-themes');
    const menu = document.getElementById('themes-menu');
    if (!button || !menu) return;

    button.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = menu.classList.toggle('hidden');
      button.setAttribute('aria-expanded', open ? 'false' : 'true');
      if (!open) this.renderThemeMenu();
    });
    // The menu opens from the button, so a click anywhere else closes it.
    document.addEventListener('click', (e) => {
      if (menu.contains(e.target) || button.contains(e.target)) return;
      this.closeThemeMenu();
    });
    this.renderThemeMenu();
  }

  closeThemeMenu() {
    const menu = document.getElementById('themes-menu');
    const button = document.getElementById('btn-themes');
    if (menu) menu.classList.add('hidden');
    if (button) button.setAttribute('aria-expanded', 'false');
  }

  renderThemeMenu() {
    const menu = document.getElementById('themes-menu');
    if (!menu) return;
    menu.innerHTML = '';
    PZ_THEME_ITEMS.forEach((item) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'theme-row';
      row.setAttribute('role', 'menuitemradio');
      row.setAttribute('aria-checked', item.id === this.currentTheme ? 'true' : 'false');

      const label = document.createElement('span');
      label.textContent = item.label;
      const check = document.createElement('span');
      check.className = 'theme-check';
      check.textContent = item.id === this.currentTheme ? '✔' : '';

      row.append(label, check);
      row.addEventListener('click', () => {
        this.applyTheme(item.id);
        this.closeThemeMenu();
      });
      menu.appendChild(row);
    });
  }

  /**
   * The top-left icon: the picture of whichever game is on screen.
   *
   * It used to be the viewer's own logo, which says nothing once you are four
   * folders deep. Under 'dynamic' it follows the browsed game, so the corner
   * answers "which Fatal Frame am I in" at a glance; under a fixed theme it
   * follows that theme instead. Themes with no picture of their own fall back
   * to the logo rather than showing a hole.
   */
  updateCornerIcon() {
    const logo = document.querySelector('.app-logo');
    if (!logo) return;
    const item = PZ_THEME_ITEMS.find((entry) => entry.id === this.effectiveTheme());
    const src = item && item.icon.indexOf('img:') === 0
      ? item.icon.slice(4)
      : PZ_FALLBACK_ICON;
    if (logo.getAttribute('src') !== src) logo.setAttribute('src', src);
  }

  /**
   * Swaps the active theme. Only the data-theme attribute changes, so the DOM
   * is never touched and nothing in the WebGL scene is re-created.
   */
  applyTheme(theme, persist = true) {
    const next = PZ_THEMES.indexOf(theme) !== -1 ? theme : PZ_DEFAULT_THEME;
    if (persist) this.themeSelectionRevision += 1;
    this.currentTheme = next;
    document.documentElement.setAttribute('data-theme', this.effectiveTheme());
    this.updateCornerIcon();
    this.renderThemeMenu();
    try {
      localStorage.setItem(PZ_THEME_KEY, next);
    } catch (err) {
      console.warn('Theme could not be saved:', err);
    }
    if (persist) {
      fetch('/api/theme', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: next })
      }).then(async (response) => {
        if (!response.ok) {
          const data = await response.json();
          throw new Error(data.error || 'Saved theme could not be written.');
        }
      }).catch((err) => {
        console.warn('Server theme preference could not be saved:', err);
      });
    }
  }

  /**
   * Re-resolves the palette after the browser target changed. Only does
   * anything while 'dynamic' is the active choice, so picking a folder is free
   * for every other theme.
   */
  syncDynamicTheme() {
    // The corner icon follows the game as well, which is the whole point of it
    // being there: browsing a different Fatal Frame changes both the palette
    // and the picture at once.
    this.updateCornerIcon();
    if (this.currentTheme !== 'dynamic') return;
    const resolved = this.effectiveTheme();
    if (document.documentElement.getAttribute('data-theme') !== resolved) {
      document.documentElement.setAttribute('data-theme', resolved);
    }
  }

  updateExportTargetUI() {
    const daeLbl = document.getElementById('lbl-fmt-dae');
    const fbxLbl = document.getElementById('lbl-fmt-fbx');
    const titleEl = document.getElementById('export-modal-title');
    const btnDo = document.getElementById('btn-modal-do-export');
    const destinationEl = document.getElementById('export-destination');
    const chooseExportBtn = document.getElementById('btn-choose-export-folder');
    const assetName = (this.currentModelData && (this.currentModelData.filename || this.currentModelData.name)) || 'asset';
    if (destinationEl) {
      const root = this.exportDestination || 'PZViewer/Exports';
      destinationEl.textContent = root + '/' + assetName.replace(/\.[^.]+$/, '') + '/';
    }
    if (chooseExportBtn && !chooseExportBtn.dataset.bound) {
      chooseExportBtn.dataset.bound = 'true';
      chooseExportBtn.addEventListener('click', async () => {
        try {
          const res = await fetch('/api/choose_folder?dir=' + encodeURIComponent(this.exportDestination || ''));
          const data = await res.json();
          if (data.chosen) {
            this.exportDestination = data.chosen;
            this.updateExportTargetUI();
          }
        } catch (err) {
          this.showToast('Folder picker error: ' + err.message, 'error');
        }
      });
    }

    // Collision export was removed from the UI: the 2D half-plane data these
    // builds carry converts into something that is not useful yet, so the radio
    // is gone rather than left selectable. Model is the only target, which is
    // why there is nothing to branch on any more. The model-options box stays
    // hidden too -- it is empty, and unhiding it would show a bare heading.
    if (daeLbl) daeLbl.classList.remove('hidden');
    if (fbxLbl) fbxLbl.classList.remove('hidden');
    if (titleEl) titleEl.textContent = 'Export 3D Model';
    if (btnDo) btnDo.textContent = 'Export';
  }

  initEventListeners() {
    // The two read-only dialogs. Same wiring as the batch one: open, close by
    // button, by the backdrop, or by Escape (handled with the other shortcuts).
    [['btn-credits', 'credits-modal', ['btn-credits-done']],
     ['btn-howto', 'howto-modal', ['btn-howto-close', 'btn-howto-done']]
    ].forEach(([openId, modalId, closeIds]) => {
      const dialog = document.getElementById(modalId);
      const open = document.getElementById(openId);
      const close = () => dialog && dialog.classList.add('hidden');
      if (open) open.addEventListener('click', () => dialog && dialog.classList.remove('hidden'));
      closeIds.forEach((id) => {
        const button = document.getElementById(id);
        if (button) button.addEventListener('click', close);
      });
      const backdrop = dialog && dialog.querySelector('.modal-backdrop');
      if (backdrop) backdrop.addEventListener('click', close);
    });

    const dataFolderNotice = document.getElementById('data-folder-notice');
    const resolveDataFolderNotice = (confirmed) => {
      if (dataFolderNotice) dataFolderNotice.classList.add('hidden');
      if (this.dataFolderNoticeResolver) {
        this.dataFolderNoticeResolver(confirmed);
        this.dataFolderNoticeResolver = null;
      }
    };
    const continueButton = document.getElementById('btn-data-folder-continue');
    const cancelButton = document.getElementById('btn-data-folder-cancel');
    if (continueButton) continueButton.addEventListener('click', () => resolveDataFolderNotice(true));
    if (cancelButton) cancelButton.addEventListener('click', () => resolveDataFolderNotice(false));
    const noticeBackdrop = dataFolderNotice && dataFolderNotice.querySelector('.modal-backdrop');
    if (noticeBackdrop) noticeBackdrop.addEventListener('click', () => resolveDataFolderNotice(false));
    this.resolveDataFolderNotice = resolveDataFolderNotice;

    // The viewport toggles say what they do by changing what they draw, not by
    // lighting up, so the browser's focus ring is taken off them: it otherwise
    // sits on whichever was pressed last and reads as a selection the user did
    // not make.
    document.querySelectorAll('.viewport-controls button').forEach((button) => {
      button.addEventListener('mouseup', () => button.blur());
    });

    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' ||
          e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
      if (e.code === 'ArrowDown' || e.code === 'ArrowUp') {
        e.preventDefault();
        this.moveFileTreeSelection(e.code === 'ArrowDown' ? 1 : -1);
        return;
      }
      if (e.code === 'Enter' &&
          document.querySelector('#file-list .file-item.selected')) {
        e.preventDefault();
        this.activateFileTreeSelection();
        return;
      }
      // Every viewport button carries its shortcut in brackets, and this is the
      // one place that has to keep up with them.
      switch (e.code) {
        case 'KeyF':
          this.fitCameraToModel();
          break;
        case 'KeyT':
          this.cycleShadingButton('textures');
          break;
        case 'KeyW':
          this.cycleShadingButton('wireframe');
          break;
        case 'KeyV':
          this.toggleVertexColors();
          break;
        case 'KeyB':
          this.toggleBones();
          break;
        case 'KeyL':
          document.getElementById('layers-modal')?.classList.toggle('hidden');
          break;
        // The recolour switch: right steps forward, left steps back. The button
        // only goes one way, so this is the way back.
        case 'ArrowRight':
          this.stepXprVariant(1);
          break;
        case 'ArrowLeft':
          this.stepXprVariant(-1);
          break;
        case 'Escape':
          if (this.currentTexturePreview) this.closeTexturePreview();
          this.closeThemeMenu();
          if (this.resolveDataFolderNotice &&
              document.getElementById('data-folder-notice') &&
              !document.getElementById('data-folder-notice').classList.contains('hidden')) {
            this.resolveDataFolderNotice(false);
            break;
          }
          // Read-only dialogs first: Escape should close the topmost thing, and
          // leaving one open while dismissing a preview behind it is not that.
          ['credits-modal', 'howto-modal'].forEach((id) => {
            const dialog = document.getElementById(id);
            if (dialog && !dialog.classList.contains('hidden')) dialog.classList.add('hidden');
          });
          break;
        default:
          return;
      }
      // Pressed by keyboard or by mouse: either way the button should not be
      // left holding focus.
      const focused = document.activeElement;
      if (focused && focused.blur) focused.blur();
    });
  }

  /**
   * Drag the layers panel around the viewport, by its header.
   *
   * It is a floating tool rather than a dialog, so where the user parks it is
   * their choice -- the material list is long enough that on a small screen it
   * would otherwise sit over exactly the part of the model being inspected. The
   * position is remembered, because a panel that jumps back to the corner on
   * every reload is a panel you move again on every reload.
   */
  initLayersPanelDrag(modal) {
    const panel = modal && modal.querySelector('.layers-modal-content');
    const header = modal && modal.querySelector('.layers-modal-header');
    if (!panel || !header) return;

    const clamp = (x, y) => ({
      x: Math.max(0, Math.min(window.innerWidth - panel.offsetWidth, x)),
      y: Math.max(0, Math.min(window.innerHeight - panel.offsetHeight, y))
    });

    // Restored position, and clamped again on restore: the window may be smaller
    // than it was when the panel was parked, and an off-screen panel cannot be
    // dragged back because its header is off-screen too.
    let saved = null;
    try {
      const raw = localStorage.getItem('pzviewer.layersPos');
      if (raw) saved = JSON.parse(raw);
    } catch (err) {
      /* storage blocked; the panel still drags, it just starts in the corner */
    }
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
      const pos = clamp(saved.x, saved.y);
      panel.style.position = 'absolute';
      panel.style.left = pos.x + 'px';
      panel.style.top = pos.y + 'px';
      panel.style.right = 'auto';
    }

    let drag = null;
    header.addEventListener('pointerdown', (event) => {
      // ✖ and ▾ are buttons: a click that starts on one is that button's.
      if (event.target.closest('button')) return;
      const rect = panel.getBoundingClientRect();
      // Switch from the flex placement to explicit coordinates at the exact
      // place the panel already is, so the grab does not jump.
      panel.style.position = 'absolute';
      panel.style.left = rect.left + 'px';
      panel.style.top = rect.top + 'px';
      panel.style.right = 'auto';
      drag = { dx: event.clientX - rect.left, dy: event.clientY - rect.top };
      panel.classList.add('dragging');
      try {
        header.setPointerCapture(event.pointerId);
      } catch (err) {
        /* capture is a nicety; the pointermove on the document still works */
      }
      event.preventDefault();
    });

    header.addEventListener('pointermove', (event) => {
      if (!drag) return;
      const pos = clamp(event.clientX - drag.dx, event.clientY - drag.dy);
      panel.style.left = pos.x + 'px';
      panel.style.top = pos.y + 'px';
    });

    const stop = (event) => {
      if (!drag) return;
      drag = null;
      panel.classList.remove('dragging');
      try {
        header.releasePointerCapture(event.pointerId);
      } catch (err) {
        /* already released */
      }
      try {
        localStorage.setItem('pzviewer.layersPos', JSON.stringify({
          x: parseFloat(panel.style.left),
          y: parseFloat(panel.style.top)
        }));
      } catch (err) {
        /* storage blocked; the position just does not survive the reload */
      }
    };
    header.addEventListener('pointerup', stop);
    header.addEventListener('pointercancel', stop);
  }

  async restoreGamePaths() {
    let serverPaths = {};
    try {
      const response = await fetch('/api/preferences?_=' + Date.now(), { cache: 'no-store' });
      if (response.ok) {
        serverPaths = await response.json();
      }
    } catch (err) {
      console.warn('Server folder preferences could not be read:', err);
    }
    let previousPaths = {};
    try {
      const parsedPaths = JSON.parse(localStorage.getItem('pzviewer.savedPaths') || '{}');
      if (parsedPaths && typeof parsedPaths === 'object' && !Array.isArray(parsedPaths)) {
        previousPaths = parsedPaths;
      }
    } catch (err) {
      console.warn('Saved folder preferences could not be read:', err);
    }
    const savedPaths = {};
    PZ_GAME_IDS.forEach((game) => {
      const serverPath = serverPaths[game];
      const localPath = localStorage.getItem('pzviewer.' + game + 'Path') ||
        previousPaths[game] || '';
      const path = is3ddataDirectory(serverPath) ? serverPath :
        (is3ddataDirectory(localPath) ? localPath : '');
      if (path) {
        savedPaths[game] = path;
        localStorage.setItem('pzviewer.' + game + 'Path', path);
      } else {
        localStorage.removeItem('pzviewer.' + game + 'Path');
      }
    });
    localStorage.setItem('pzviewer.savedPaths', JSON.stringify(savedPaths));
    let firstPath = '';
    let firstGame = '';
    this.currentBrowserGame = '';
    // Which folder the browser opens on load. The active tab's games go first, so
    // reloading while the Extra tab is showing reopens the Wii folder rather than
    // jumping back to a PS2 one; the rest of the games are the fallback for when
    // the active tab has nothing saved yet.
    const preferred = this.visibleRootGames().map((game) => game.id)
      .concat(PZ_GAME_IDS.filter((id) =>
        this.visibleRootGames().every((game) => game.id !== id)));
    preferred.forEach((game) => {
      const path = savedPaths[game] ||
        localStorage.getItem('pzviewer.' + game + 'Path') || '';
      if (!firstPath && path) {
        firstPath = path;
        firstGame = game;
      }
    });
    this.renderSavedRoots();
    if (firstPath) {
      const dirInput = document.getElementById('input-dir');
      if (dirInput) dirInput.value = firstPath;
      this.browseDir(firstPath, firstGame);
    }
  }

  saveGamePath(game, path) {
    if (PZ_GAME_IDS.indexOf(game) === -1) return;
    const normalizedPath = (path || '').trim();
    if (!normalizedPath) return;
    if (!is3ddataDirectory(normalizedPath)) {
      this.showToast("Select the game's 3ddata folder itself.", 'error');
      return;
    }
    try {
      localStorage.setItem('pzviewer.' + game + 'Path', normalizedPath);
      const savedPaths = {};
      PZ_GAME_IDS.forEach((key) => {
        const value = localStorage.getItem('pzviewer.' + key + 'Path');
        if (value) savedPaths[key] = value;
      });
      savedPaths[game] = normalizedPath;
      localStorage.setItem('pzviewer.savedPaths', JSON.stringify(savedPaths));
      fetch('/api/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ game: game, path: normalizedPath })
      }).then(async (response) => {
        if (!response.ok) {
          const data = await response.json();
          throw new Error(data.error || 'Could not save the selected folder.');
        }
      }).catch((err) => {
        console.warn('Folder preference could not be saved:', err);
        this.showToast('Folder preference error: ' + err.message, 'error');
      });
    } catch (err) {
      console.warn('Folder preference could not be saved:', err);
    }
  }

  /**
   * The two tabs over the saved roots, and which one is showing.
   *
   * The active tab is remembered because it decides which folders are on screen:
   * somebody working on the Wii port would otherwise land back on the PS2 roots
   * on every reload. 'Original' is the default because it is the common case.
   */
  initRootTabs() {
    const host = document.getElementById('root-tabs');
    if (!host) return;
    let stored = PZ_DEFAULT_ROOT_TAB;
    try {
      stored = localStorage.getItem(PZ_ROOT_TAB_KEY) || PZ_DEFAULT_ROOT_TAB;
    } catch (err) {
      /* storage blocked; the default tab is fine */
    }
    if (!PZ_ROOT_TABS.some((tab) => tab.id === stored)) stored = PZ_DEFAULT_ROOT_TAB;
    this.currentRootTab = stored;
    this.renderRootTabs();
  }

  renderRootTabs() {
    const host = document.getElementById('root-tabs');
    if (!host) return;
    host.innerHTML = '';
    PZ_ROOT_TABS.forEach((tab) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'root-tab' + (tab.id === this.currentRootTab ? ' active' : '');
      button.textContent = tab.label;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', tab.id === this.currentRootTab ? 'true' : 'false');
      button.addEventListener('click', () => this.setRootTab(tab.id));
      host.appendChild(button);
    });
  }

  setRootTab(id) {
    if (!PZ_ROOT_TABS.some((tab) => tab.id === id)) return;
    this.currentRootTab = id;
    try {
      localStorage.setItem(PZ_ROOT_TAB_KEY, id);
    } catch (err) {
      /* storage blocked; the tab still switches for this session */
    }
    this.renderRootTabs();
    this.renderSavedRoots();
  }

  /** The game ids the active tab shows, in the order it shows them. */
  visibleRootGames() {
    const tab = PZ_ROOT_TABS.find((t) => t.id === this.currentRootTab);
    const ids = tab ? tab.games : PZ_GAME_IDS;
    // Driven off PZ_GAMES so a game whose id is not on any tab is dropped rather
    // than silently appearing nowhere, and the labels stay in one place.
    return PZ_GAMES.filter((game) => ids.indexOf(game.id) !== -1);
  }

  renderSavedRoots() {
    // #saved-roots, not #file-list: the two used to be the same element, so
    // redrawing the roots erased the folders of the directory being browsed.
    const savedRootsEl = document.getElementById('saved-roots');
    if (!savedRootsEl) return;
    savedRootsEl.innerHTML = '';
    this.visibleRootGames().forEach(({ id: game, label: title }) => {
      const path = localStorage.getItem('pzviewer.' + game + 'Path') || '';
      const row = document.createElement('div');
      row.className = 'file-item saved-root';
      row.dataset.search = (title + ' ' + path).toLowerCase();
      row.innerHTML = '<span class="file-icon">📁</span><span class="file-name">' +
        title + '</span><span class="file-meta root-status">' +
        (path ? 'checking…' : 'Not set') + '</span>';
      row.addEventListener('click', () => {
        if (path) this.browseDir(path, game);
        else this.chooseSavedRoot(game);
      });
      const choose = document.createElement('button');
      choose.className = 'btn btn-sm root-action';
      choose.textContent = path ? 'Update' : 'Select';
      choose.title = 'Set ' + title + ' folder';
      choose.addEventListener('click', (event) => {
        event.stopPropagation();
        this.chooseSavedRoot(game);
      });
      row.appendChild(choose);
      const clear = document.createElement('button');
      clear.className = 'btn-icon root-clear';
      clear.textContent = '✖';
      clear.title = 'Clear saved ' + title + ' path';
      clear.addEventListener('click', (event) => {
        event.stopPropagation();
        localStorage.removeItem('pzviewer.' + game + 'Path');
        fetch('/api/preferences', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ game: game, path: '' })
        }).catch((err) => console.warn('Folder preference could not be cleared:', err));
        try {
          const savedPaths = JSON.parse(localStorage.getItem('pzviewer.savedPaths') || '{}');
          delete savedPaths[game];
          localStorage.setItem('pzviewer.savedPaths', JSON.stringify(savedPaths));
        } catch (err) {
          console.warn('Saved folder preferences could not be updated:', err);
        }
        this.renderSavedRoots();
      });
      row.appendChild(clear);
      savedRootsEl.appendChild(row);
      if (path) this.checkSavedRoot(row, path, game);
    });
  }

  async checkSavedRoot(row, path, game) {
    try {
      const res = await fetch('/api/browse?dir=' + encodeURIComponent(path) +
        '&game=' + encodeURIComponent(game));
      const status = row.querySelector('.root-status');
      if (status) status.textContent = res.ok ? 'Saved path' : 'Missing folder';
    } catch (err) {
      const status = row.querySelector('.root-status');
      if (status) status.textContent = 'Unavailable';
    }
  }

  async chooseSavedRoot(game) {
    const notice = document.getElementById('data-folder-notice');
    if (!notice) return;
    notice.classList.remove('hidden');
    document.getElementById('btn-data-folder-continue')?.focus();
    const confirmed = await new Promise((resolve) => {
      this.dataFolderNoticeResolver = resolve;
    });
    if (!confirmed) return;

    const path = localStorage.getItem('pzviewer.' + game + 'Path') || '';
    try {
      const res = await fetch('/api/choose_folder?game=' + game +
        '&dir=' + encodeURIComponent(path));
      const data = await res.json();
      if (data.error) {
        this.showToast(data.error, 'error');
        return;
      }
      if (data.chosen) {
        if (!is3ddataDirectory(data.chosen)) {
          this.showToast("Select the game's 3ddata folder itself.", 'error');
          return;
        }
        this.saveGamePath(game, data.chosen);
        this.renderSavedRoots();
        document.getElementById('input-dir').value = data.chosen;
        this.browseDir(data.chosen, game);
      }
    } catch (err) {
      this.showToast('Folder picker error: ' + err.message, 'error');
    }
  }

  async browseDir(dirPath, game = 'all') {
    const fileListEl = document.getElementById('file-list');
    if (!fileListEl) return;
    if (game === 'all' && this.currentBrowserGame) {
      game = this.currentBrowserGame;
    }
    if (PZ_GAME_IDS.indexOf(game) !== -1) {
      this.currentBrowserGame = game;
      try {
        localStorage.setItem(PZ_DYNAMIC_KEY, game);
      } catch (err) {
        /* storage blocked; the in-memory value still drives the theme */
      }
      this.syncDynamicTheme();
    }
    fileListEl.innerHTML = '<div class="loading-hint">Reading directory...</div>';

    try {
      const res = await fetch('/api/browse?dir=' + encodeURIComponent(dirPath) +
        '&game=' + encodeURIComponent(game));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Folder could not be opened');
      fileListEl.innerHTML = '';
      this.renderSavedRoots();

      const inputDir = document.getElementById('input-dir');
      if (inputDir && data.current_dir) {
        inputDir.value = data.current_dir;
      }
      this.currentParentDir = data.parent_dir;
      const location = document.getElementById('browser-location');
      if (location) location.textContent = data.current_dir;
      const dirUpBtn = document.getElementById('btn-dir-up');
      if (dirUpBtn) {
        dirUpBtn.disabled = !data.parent_dir || data.parent_dir === data.current_dir;
      }

      if (data.parent_dir && data.parent_dir !== data.current_dir) {
        const upRow = document.createElement('div');
        upRow.className = 'file-item dir';
        upRow.dataset.path = data.parent_dir;
        upRow.style.fontWeight = 'bold';
        upRow.style.color = '#38bdf8';
        upRow.innerHTML = '<span>📁</span> <span>.. (Parent Directory)</span>';
        upRow.addEventListener('click', () => {
          this.setFileTreeSelection(upRow);
          this.browseDir(data.parent_dir, game);
        });
        fileListEl.appendChild(upRow);
      }

      if (data.items && data.items.length > 0) {
        data.items.forEach(item => {
          const row = document.createElement('div');
          row.className = 'file-item ' + (item.is_dir ? 'dir' : 'file-' + item.type);
          row.dataset.path = item.path;
          if (item.path === this.selectedBrowserPath) {
            row.classList.add('selected');
          }
          row.dataset.search = (item.name + ' ' + item.type).toLowerCase();
          const iconMap = { sgd_pack: '🧩', mdl: '🧍', mpk: '📦', sgd: '📄', cld: '🛡️', tm2: '🖼️', tim2: '🖼️', png: '🖼️', pk2: '📦', pk4: '📦' };
          const icon = item.is_dir ? '📁' : (iconMap[item.type] || '📄');
          const size = item.is_dir ? 'folder' : this.formatFileSize(item.size);
          row.innerHTML = '<span class="file-icon">' + icon + '</span><span class="file-name">' +
            item.name + '</span><span class="file-meta">' + size + '</span>';

          row.addEventListener('click', () => {
            this.setFileTreeSelection(row);
            if (item.load_path) {
              this.loadFile(item.load_path);
            } else if (item.is_dir) {
              this.browseDir(item.path, game);
            } else {
              this.loadFile(item.path);
            }
          });
          fileListEl.appendChild(row);
        });
      } else {
        // An empty folder is a dead end: the message says so and the tree offers
        // the way out as a row of its own, the same shape as the one shown above
        // a populated folder, rather than as a button that looks like it belongs
        // to the app. At a configured root there is no parent to offer -- the
        // browser will not navigate above it -- so the message stands alone.
        fileListEl.innerHTML = '';
        fileListEl.appendChild(this.buildEmptyFolderNotice(data, game));
      }
      const fileFilter = document.getElementById('file-filter');
      if (fileFilter) this.filterBrowserItems(fileFilter.value);
    } catch (e) {
      this.renderSavedRoots();
      const error = document.createElement('div');
      error.className = 'loading-hint';
      error.style.color = '#ef4444';
      error.textContent = 'Error: ' + e.message;
      fileListEl.appendChild(error);
    }
  }

  /**
   * The listing shown when a folder holds nothing this game can open: the
   * message, plus a "... (Parent Folder)" row when there is a parent to go back
   * to. The row is built like the one above a populated folder so the tree has
   * one way of saying "up" rather than two.
   */
  buildEmptyFolderNotice(data, game) {
    const notice = document.createElement('div');
    notice.className = 'empty-folder';

    // The way out comes first, in the same position it occupies above a
    // populated folder: the tree keeps one place for "up", and when there is
    // nothing else to click that is the first thing worth pressing.
    if (data.parent_dir && data.parent_dir !== data.current_dir) {
      const upRow = document.createElement('div');
      upRow.className = 'file-item dir';
      upRow.dataset.path = data.parent_dir;
      upRow.style.fontWeight = 'bold';
      upRow.innerHTML = '<span>📁</span> <span>... (Parent Folder)</span>';
      upRow.addEventListener('click', () => {
        this.setFileTreeSelection(upRow);
        this.browseDir(data.parent_dir, game);
      });
      notice.appendChild(upRow);
    }

    const message = document.createElement('div');
    message.className = 'loading-hint';
    message.textContent = 'No compatible 3D files found';
    notice.appendChild(message);
    return notice;
  }

  setFileTreeSelection(row, focusTree = true) {
    if (!row || !row.dataset.path) return;
    this.selectedBrowserPath = row.dataset.path;
    const fileList = document.getElementById('file-list');
    if (focusTree && fileList && document.activeElement !== fileList) {
      fileList.focus({ preventScroll: true });
    }
    document.querySelectorAll('#file-list .file-item.selected').forEach((selectedRow) => {
      selectedRow.classList.remove('selected');
    });
    row.classList.add('selected');
  }

  moveFileTreeSelection(direction) {
    const rows = Array.from(document.querySelectorAll(
      '#file-list .file-item:not([hidden])'
    ));
    if (!rows.length) return;
    const selectedIndex = rows.findIndex((row) => row.classList.contains('selected'));
    const nextIndex = selectedIndex < 0
      ? (direction > 0 ? 0 : rows.length - 1)
      : Math.max(0, Math.min(rows.length - 1, selectedIndex + direction));
    this.setFileTreeSelection(rows[nextIndex]);
    rows[nextIndex].scrollIntoView({ block: 'nearest' });
  }

  activateFileTreeSelection() {
    const selected = document.querySelector('#file-list .file-item.selected');
    if (selected) selected.click();
  }

  filterBrowserItems(query) {
    const needle = query.trim().toLowerCase();
    document.querySelectorAll('#file-list .file-item').forEach(row => {
      row.hidden = !!needle && !(row.dataset.search || '').includes(needle);
    });
    if (!document.querySelector('#file-list .file-item.selected:not([hidden])')) {
      const firstVisible = document.querySelector('#file-list .file-item:not([hidden])');
      // Filtering runs on every keystroke; moving focus to the tree here would
      // interrupt typing as soon as the current selection is filtered out.
      if (firstVisible) this.setFileTreeSelection(firstVisible, false);
    }
  }

  formatFileSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  showLoading(text) {
    const overlay = document.getElementById('loading-overlay');
    const msg = document.getElementById('loading-text');
    if (overlay) overlay.classList.add('active');
    if (msg) msg.textContent = text || 'Loading Model into GPU...';
  }

  hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) overlay.classList.remove('active');
  }

  /**
   * Shows the recolour button only for the asset that has variants.
   *
   * m000_spe5.mpx and m000_spe6.mpx are byte-for-byte identical to
   * m000_miku4.mpx, so m000_miku4/spe5/spe6.xpr are three colourways of one
   * piece of geometry. Only that kind of asset gets a switch: the server sends
   * `xpr_variant_switch` when a model has more than one palette, so anything
   * else -- including a plain PS2 model -- leaves the button hidden rather than
   * offering to switch to geometry that is not what was clicked.
   */
  syncXprVariantButton(data, activeXpr) {
    const button = document.getElementById('btn-xpr-variant');
    const label = document.getElementById('xpr-variant-name');
    if (!button) return;
    const variants = (data && data.xpr_variants) || [];
    // The server decides: it only sets this flag for a model that really has
    // more than one palette. Matching on the file name here was fragile -- the
    // payload's `filename` comes without its extension -- so the rule lives in
    // one place instead of being re-guessed in the browser.
    if (!(data && data.xpr_variant_switch) || variants.length < 2) {
      this.hideXprVariantButton(true);
      return;
    }
    this.xprVariantList = variants;
    // The server reports the bound archive separately, so the cycle order stays
    // fixed no matter which variant is on screen.
    const active = (data && data.xpr_active) || activeXpr || variants[0];
    this.xprVariantIndex = variants.findIndex(
      (v) => v.toLowerCase() === String(active).toLowerCase());
    if (this.xprVariantIndex < 0) this.xprVariantIndex = 0;
    if (label) label.textContent = variants[this.xprVariantIndex];
    button.title = 'Recolour archives for this geometry: ' + variants.join(', ') +
      ' — click for the next one';
    button.classList.remove('hidden');
  }

  hideXprVariantButton(forget) {
    const button = document.getElementById('btn-xpr-variant');
    if (button) button.classList.add('hidden');
    // Only drop the cycle when the asset really is not one of these: hiding the
    // button while its own load is in flight must not make the next click a
    // no-op, which is what clearing the list here used to do.
    if (forget) {
      this.xprVariantList = null;
      this.xprVariantIndex = -1;
    }
  }

  cycleXprVariant() {
    return this.stepXprVariant(1);
  }

  /**
   * Moves the recolour switch by `delta` places, wrapping at both ends.
   *
   * Both directions are on the keyboard as well as on the button: the button
   * cycles one way only, so a left arrow is the only way back to the previous
   * colourway without going all the way round. Does nothing when the asset on
   * screen has no recolour archives, which is the same rule the button follows
   * by not being there at all.
   */
  stepXprVariant(delta) {
    if (!this.xprVariantList || !this.currentSourcePath) return;
    const total = this.xprVariantList.length;
    // The index is -1 until a model with variants has been loaded; starting it
    // from 0 means the first press lands on the first archive rather than
    // jumping past it.
    const current = this.xprVariantIndex < 0 ? 0 : this.xprVariantIndex;
    const next = (current + delta + total * 2) % total;
    this.loadFile(this.currentSourcePath, this.xprVariantList[next]);
  }

  showToast(msg, type) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.className = 'toast show ' + (type || 'info');
    setTimeout(() => {
      toast.className = 'toast';
    }, 3500);
  }

  async loadFile(filePath, xprName) {
    const fn = filePath.split('/').pop();
    // Hidden up front, not only when the answer arrives: a load that fails
    // returns early without reaching syncXprVariantButton, and the button would
    // otherwise stay on screen describing the previous model. The cycle list is
    // kept so a click during this load still works.
    this.hideXprVariantButton(false);
    this.showLoading('Loading ' + fn + ' into GPU VRAM...');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 90000);
    try {
      let url = '/api/load?path=' + encodeURIComponent(filePath) +
        '&game=' + encodeURIComponent(this.currentBrowserGame || '') +
        '&_=' + Date.now();
      if (xprName) url += '&xpr=' + encodeURIComponent(xprName);
      const res = await fetch(url, {
        cache: 'no-store',
        signal: controller.signal
      });
      const data = await res.json();
      if (data.error) {
        this.hideXprVariantButton(true);
        this.showToast('Error: ' + data.error, 'error');
        return;
      }

      this.currentModelData = data;
      this.currentSourcePath = filePath;
      this.syncXprVariantButton(data, xprName);

      // Shading defaults to textured for all model types (rooms, characters, props)
      this.shadingMode = 'textured';
      // Baked FF1 Xbox room lighting is meaningful on textured surfaces, so
      // show it automatically. Other assets start without vertex colours.
      const roomLighting = (((data.diagnostics || {}).parser || {}).ff1_lighting || {});
      this.vertexColorsOn =
        roomLighting.format === 'ff1_xbox_static_vertex_lighting' &&
        roomLighting.status === 'baked';

      this.buildGPUScene(data);
      // After the scene, not before: the panel binds each row to the objects that
      // draw the material, and those objects do not exist until the scene is built.
      this.buildMeshLayers(data);
      this.updateStatsUI(data);

      this.fitCameraToModel();
      this.showToast('Loaded ' + data.filename + ' on GPU successfully!', 'success');
    } catch (err) {
      console.error(err);
      this.showToast(
        err.name === 'AbortError'
          ? 'Loading timed out. The asset parser stopped before the viewer could respond.'
          : 'Failed: ' + err.message,
        'error'
      );
    } finally {
      clearTimeout(timeout);
      this.hideLoading();
    }
  }

  /**
   * Reproduces the three alpha passes the reference MDLB renderer uses:
   *
   *   1. opaque texture        -> no alpha test, no blending
   *   2. texture with cutout   -> hard alpha test at 0.5, depth writes left on so
   *                              discarded fragments cannot occlude
   *   3. soft masked material  -> low alpha test plus blending
   *
   * The previous version set `transparent` for any texture with an alpha
   * channel and `alphaTest: 0`, which both let fully transparent texels
   * composite over the model *and* switched depth writing off. Without depth
   * writes three.js sorts the surfaces per object instead of per depth, so
   * interior geometry drew over the outside of the character: that read as the
   * body "clipping" through itself.
   *
   * Rooms use the same three passes. Room alpha is a real mask, not baked GS
   * colour data: the shadow sheets in rre01 are textures with zero fully opaque
   * pixels that 23-25 meshes sample (tex19 is 45% cutout / 55% soft), and they
   * need genuine blending or they render as solid blocks on the floor. Forcing
   * rooms opaque was a wrong guess and it is what made those shadows opaque.
   */
  getTextureAlphaSettings(map, isRoomAsset = false, keepPartialAlphaDepth = false) {
    const userData = map && map.userData ? map.userData : {};
    const alphaMin = Number.isFinite(userData.alphaMin) ? userData.alphaMin : 255;
    const alphaMax = Number.isFinite(userData.alphaMax) ? userData.alphaMax : 255;
    const hasPartialAlpha = !!userData.alphaHasPartial;
    const opaque = !userData.hasAlpha || alphaMin >= 255;

    if (opaque) {
      // Pass 1: fully opaque surface, no test and no blending.
      return { transparent: false, alphaTest: 0, depthWrite: true };
    }
    if (alphaMax >= 255 && !(keepPartialAlphaDepth && hasPartialAlpha)) {
      // Pass 2: binary cutout (lace, hair edges). Discard instead of blending
      // and keep depth writes, which is what makes the cutout read clean.
      return { transparent: false, alphaTest: 0.5, depthWrite: true };
    }
    if (keepPartialAlphaDepth && hasPartialAlpha) {
      // FF2 Wii characters use many intersecting hair, lash, and lace cards.
      // Blend their fractional coverage, but keep depth writes on so nearer
      // cards occlude farther cards per fragment instead of showing sorting
      // seams or the body through overlapping layers.
      return { transparent: true, alphaTest: 0.01, depthWrite: true };
    }
    // Pass 3: genuinely soft masks need blending without occluding later
    // transparent surfaces.
    return { transparent: true, alphaTest: 0.2, depthWrite: false };
  }

  buildGPUScene(data) {
    const isRoomAsset = data.model_type === 'room' || data.type === 'room';
    const parserDiagnostics = data.diagnostics && data.diagnostics.parser;
    const isFF2WCharacter = !!(
      (data.model_type === 'character' || data.type === 'character') &&
      parserDiagnostics &&
      parserDiagnostics.container === 'pk3'
    );
    const textureFlipY = data.uvs_flipped ? false : true;
    // FF2 Wii ships small atlases that get magnified over whole surfaces;
    // nearest sampling turns them into hard texel blocks. The TIM2 games keep
    // nearest so their pixel art stays crisp.
    const magFilter = data.texture_mag_linear ? THREE.LinearFilter : THREE.NearestFilter;
    while (this.modelGroup.children.length) {
      const obj = this.modelGroup.children[0];
      if (obj.geometry) obj.geometry.dispose();
      this.modelGroup.remove(obj);
    }
    while (this.bonesGroup.children.length) {
      const obj = this.bonesGroup.children[0];
      if (obj.geometry) obj.geometry.dispose();
      this.bonesGroup.remove(obj);
    }
    while (this.collisionGroup.children.length) {
      const obj = this.collisionGroup.children[0];
      if (obj.geometry) obj.geometry.dispose();
      this.collisionGroup.remove(obj);
    }

    this.textures = {};
    const textureUsers = {};
    const applyLoadedTexture = (idx, loadedTex) => {
      const users = textureUsers[idx];
      if (!users) return;
      users.forEach((object) => {
        object.material.map = loadedTex;
        object.material.needsUpdate = true;
      });
    };
    const applyTexturesToModel = true;
    if (applyTexturesToModel && data.textures) {
      const loader = new THREE.TextureLoader();
      data.textures.forEach((tex, idx) => {
        if (tex.data_uri) {
          const threeTex = loader.load(tex.data_uri, (loadedTex) => {
            loadedTex.colorSpace = THREE.SRGBColorSpace;
            loadedTex.flipY = textureFlipY;
            loadedTex.needsUpdate = true;
            if (this.renderer && this.renderer.initTexture) {
              this.renderer.initTexture(loadedTex);
            }
            applyLoadedTexture(idx, loadedTex);
          });
          threeTex.flipY = textureFlipY;
          threeTex.colorSpace = THREE.SRGBColorSpace;
          threeTex.wrapS = THREE.RepeatWrapping;
          threeTex.wrapT = THREE.RepeatWrapping;
          threeTex.magFilter = magFilter;
          threeTex.minFilter = THREE.LinearMipmapLinearFilter;
          threeTex.generateMipmaps = true;
          threeTex.userData = {
            hasAlpha: !!tex.has_alpha,
            alphaMin: Number.isFinite(tex.alpha_min) ? tex.alpha_min : 255,
            alphaMax: Number.isFinite(tex.alpha_max) ? tex.alpha_max : 255,
            alphaHasPartial: !!tex.alpha_has_partial,
            sourceTextureIndex: idx
          };
          threeTex.needsUpdate = true;
          this.textures[idx] = threeTex;
        }
      });
    }

    if (data.meshes) {
      data.meshes.forEach((meshData, mIdx) => {
        try {
          const positions = Array.isArray(meshData.positions) ? meshData.positions : [];
          if (positions.length < 3 || positions.length % 3 !== 0 ||
              positions.some(value => !Number.isFinite(value))) {
            throw new Error('invalid position buffer');
          }
          const vertexCount = positions.length / 3;
          const geom = new THREE.BufferGeometry();

          const posAttr = new THREE.BufferAttribute(new Float32Array(positions), 3);
          posAttr.setUsage(THREE.StaticDrawUsage);
          geom.setAttribute('position', posAttr);

          const addAttribute = (name, values, itemSize) => {
            if (!Array.isArray(values) || values.length !== vertexCount * itemSize ||
                values.some(value => !Number.isFinite(value))) return false;
            const attr = new THREE.BufferAttribute(new Float32Array(values), itemSize);
            attr.setUsage(THREE.StaticDrawUsage);
            geom.setAttribute(name, attr);
            return true;
          };

          const hasNormals = addAttribute('normal', meshData.normals, 3);
          if (!hasNormals) geom.computeVertexNormals();
          addAttribute('uv', meshData.uvs, 2);
          const hasColors = addAttribute('color', meshData.colors, 3);

          const rawIndices = Array.isArray(meshData.indices) ? meshData.indices : [];
          const validIndices = [];
          for (let i = 0; i + 2 < rawIndices.length; i += 3) {
            const a = rawIndices[i];
            const b = rawIndices[i + 1];
            const c = rawIndices[i + 2];
            if ([a, b, c].every(index => Number.isInteger(index) && index >= 0 && index < vertexCount)) {
              validIndices.push(a, b, c);
            }
          }
          if (validIndices.length > 0) {
            const maxIndex = Math.max(...validIndices);
            const canUseUint32 = maxIndex > 65535 &&
              (this.renderer.capabilities.isWebGL2 ||
               !!this.renderer.extensions.get('OES_element_index_uint'));
            if (maxIndex <= 65535 || canUseUint32) {
              const IndexArray = maxIndex <= 65535 ? Uint16Array : Uint32Array;
              const idxAttr = new THREE.BufferAttribute(new IndexArray(validIndices), 1);
              idxAttr.setUsage(THREE.StaticDrawUsage);
              geom.setIndex(idxAttr);
            }
          }

          geom.computeBoundingSphere();
          geom.computeBoundingBox();

          const tex = this.textures[meshData.tex_id];
          const isRoom = isRoomAsset;
          // Backface culling everywhere. These files wind their triangles
          // consistently, and drawing the far side of every surface doubles the
          // fill cost for a layer that is never meant to be seen -- on a
          // single-sided room sheet it also let the interior bleed through the
          // walls. The collision solids are the exception: they are translucent
          // volumes meant to be read from any angle, so they keep DoubleSide.
          const meshSide = THREE.FrontSide;
          // Room textures use the alpha channel as baked GS color data in
          // some assets; only prop/character materials use it for cutouts.
          const hasAlpha = !!(tex && tex.userData && tex.userData.hasAlpha);
          const MaterialClass = isRoom && tex ? THREE.MeshBasicMaterial : THREE.MeshStandardMaterial;
          const mat = new MaterialClass({
            map: tex || null,
            // Parsed FF1 room color buffers can be zero-filled; multiplying
            // a recovered texture by them makes the whole room black.
            vertexColors: this.vertexColorsOn && hasColors,
            side: meshSide,
            ...this.getTextureAlphaSettings(tex, isRoomAsset, isFF2WCharacter),
            opacity: 1,
            ...(MaterialClass === THREE.MeshStandardMaterial ? {
              roughness: 0.82,
              metalness: 0.08
            } : {})
          });

          const threeMesh = new THREE.Mesh(geom, mat);
          threeMesh.name = meshData.name || ('submesh_' + mIdx);
          const textureIndex = Number.isInteger(meshData.tex_id) ? meshData.tex_id : -1;
          threeMesh.userData = {
            // Stamped rather than assumed: a malformed submesh is skipped by the
            // catch below, so a child's position in modelGroup.children is not
            // its index in the payload. Whatever maps payload entries onto scene
            // objects (the layers panel) goes through this instead.
            meshIndex: mIdx,
            hasTexture: !!tex,
            textureIndex: textureIndex,
            hasColors: hasColors,
            texturedVertexColors: this.vertexColorsOn && hasColors,
            originalMat: mat,
            boneIndex: meshData.bone_index || 0
          };
          if (textureIndex >= 0) {
            if (!textureUsers[textureIndex]) textureUsers[textureIndex] = [];
            textureUsers[textureIndex].push(threeMesh);
            if (this.textures[textureIndex]) {
              applyLoadedTexture(textureIndex, this.textures[textureIndex]);
            }
          }

          this.modelGroup.add(threeMesh);
        } catch (err) {
          console.warn('Skipping malformed mesh ' + mIdx + ':', err);
        }
      });
    }
    if (this.modelGroup.children.length === 0 && data.meshes && data.meshes.length > 0) {
      console.error('No valid meshes were added to modelGroup', {
        received: data.meshes.length,
        filename: data.filename || data.name || 'model'
      });
      this.showToast('No valid mesh geometry was found in this asset.', 'error');
    }

    if (data.bones && data.bones.length > 0) {
      this.buildBonesVisualizer(data.bones);
    }

    if (data.collision) {
      this.buildCollisionVisualizer(data.collision);
      const hasCollision = (data.collision.boxes && data.collision.boxes.length > 0) ||
        (data.collision.polygons && data.collision.polygons.length > 0) ||
        (data.collision.spheres && data.collision.spheres.length > 0);
      if (hasCollision && this.currentModelData && this.currentModelData.model_type === 'collision') {
        this.collisionGroup.visible = true;
        this.modelGroup.visible = true;
      }
    }

    // Shader precompilation is an optimization; it must not prevent the
    // camera fit or leave an otherwise valid scene blank on WebGL errors.
    try {
      this.renderer.compile(this.scene, this.camera);
    } catch (err) {
      console.warn('GPU shader precompilation failed; continuing with lazy compilation:', err);
    }
    this.applyShadingMode();
  }

  buildBonesVisualizer(bones) {
    // The rig is an overlay, not part of the model: it sits mostly *inside* the
    // body, so with the depth test on it vanished behind the mesh exactly where
    // it is most wanted. depthTest off plus a late renderOrder draws it last and
    // untested, so the skeleton reads through the character. The depth buffer is
    // left alone so the overlay cannot smear into whatever draws next.
    const sphereGeom = new THREE.SphereGeometry(0.45, 8, 8);
    const sphereMat = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
      depthTest: false,
      depthWrite: false
    });

    this.boneNodes = [];
    const nodesById = new Map();
    const linePositions = [];

    bones.forEach((b) => {
      const marker = new THREE.Mesh(sphereGeom, sphereMat);
      marker.position.set(b.pos[0], b.pos[1], b.pos[2]);
      marker.renderOrder = 999;
      this.bonesGroup.add(marker);

      this.boneNodes.push({
        id: b.id,
        parent: b.parent,
        mesh: marker,
        restPos: [...b.pos],
        restRot: [...b.rot]
      });
      nodesById.set(b.id, this.boneNodes[this.boneNodes.length - 1]);
    });

    this.boneNodes.forEach((node) => {
      const parent = nodesById.get(node.parent);
      if (parent) {
        linePositions.push(node.restPos[0], node.restPos[1], node.restPos[2]);
        linePositions.push(parent.restPos[0], parent.restPos[1], parent.restPos[2]);
      }
    });

    if (linePositions.length > 0) {
      this.boneLineGeom = new THREE.BufferGeometry();
      this.boneLineGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(linePositions), 3));
      const lineMat = new THREE.LineBasicMaterial({
        color: 0x0284c7,
        depthTest: false,
        depthWrite: false
      });
      this.boneLinesMesh = new THREE.LineSegments(this.boneLineGeom, lineMat);
      this.boneLinesMesh.renderOrder = 999;
      this.bonesGroup.add(this.boneLinesMesh);
    }
  }

  buildCollisionVisualizer(collision) {
    if (collision.polygons && collision.polygons.length > 0) {
      const positions = [];
      collision.polygons.forEach(poly => {
        if (poly.length >= 3) {
          for (let i = 1; i < poly.length - 1; i++) {
            positions.push(poly[0][0], poly[0][1], poly[0][2]);
            positions.push(poly[i][0], poly[i][1], poly[i][2]);
            positions.push(poly[i + 1][0], poly[i + 1][1], poly[i + 1][2]);
          }
        }
      });

      if (positions.length > 0) {
        const polyGeom = new THREE.BufferGeometry();
        polyGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions), 3));
        polyGeom.computeVertexNormals();

        // Fill translucent 3D solid surface
        const polyMat = new THREE.MeshStandardMaterial({
          color: 0x10b981,
          transparent: true,
          opacity: 0.38,
          side: THREE.DoubleSide,
          depthWrite: false,
          roughness: 0.4
        });
        const colMesh = new THREE.Mesh(polyGeom, polyMat);
        this.collisionGroup.add(colMesh);

        // Edge contours for volumetric boundaries
        const edgesGeom = new THREE.EdgesGeometry(polyGeom, 20);
        const edgeMat = new THREE.LineBasicMaterial({
          color: 0x34d399,
          transparent: true,
          opacity: 0.85
        });
        const edgeMesh = new THREE.LineSegments(edgesGeom, edgeMat);
        this.collisionGroup.add(edgeMesh);
      }
    }

    if (collision.spheres && collision.spheres.length > 0) {
      collision.spheres.forEach(s => {
        const r = s.radius || 1.8;
        const geom = new THREE.SphereGeometry(r, 16, 16);
        const mat = new THREE.MeshBasicMaterial({ color: 0x38bdf8, wireframe: true, transparent: true, opacity: 0.85 });
        const sphereMesh = new THREE.Mesh(geom, mat);
        sphereMesh.position.set(s.center[0], s.center[1], s.center[2]);
        this.collisionGroup.add(sphereMesh);
      });
    }

    if (collision.boxes && collision.boxes.length > 0) {
      collision.boxes.forEach(b => {
        const min = new THREE.Vector3(b.min[0], b.min[1], b.min[2]);
        const max = new THREE.Vector3(b.max[0], b.max[1], b.max[2]);
        const size = new THREE.Vector3().subVectors(max, min);
        const center = new THREE.Vector3().addVectors(min, max).multiplyScalar(0.5);
        const boxGeom = new THREE.BoxGeometry(
          Math.max(size.x, 0.02),
          Math.max(size.y, 0.02),
          Math.max(size.z, 0.02)
        );
        const boxMat = new THREE.MeshBasicMaterial({
          color: 0xf59e0b,
          wireframe: true,
          transparent: true,
          opacity: 0.95
        });
        const boxMesh = new THREE.Mesh(boxGeom, boxMat);
        boxMesh.position.copy(center);
        this.collisionGroup.add(boxMesh);
      });
    }
  }

  /**
   * Vertex colours on or off, as a modifier on the textured looks.
   *
   * These used to be two entries in the shading picker ("Textures + Vertex
   * Color" and "Vertex Colors (Rooms)"), so the answer to "I want the colours"
   * depended on which entry you remembered. They are now a flag on the textured
   * looks rather than modes of their own, so the two shading buttons keep
   * meaning what they say, and this is a plain on/off.
   */
  toggleVertexColors() {
    this.vertexColorsOn = !this.vertexColorsOn;
    this.applyShadingMode();
  }

  /**
   * Cycles one of the two shading buttons through the two looks it owns.
   *
   * Exactly one mode is ever in force: the button that was pressed last owns the
   * viewport, and the other one goes inactive. That is why the two lists exist
   * instead of one list of four -- each button answers "what does this look
   * like" without the user having to remember which entry it was.
   */
  cycleShadingButton(which) {
    const steps = SHADING_STEPS[which];
    if (!steps) return;
    const current = steps.indexOf(this.shadingMode);
    // Not in this button's list (the other one had it): start at its first stop.
    const next = current === -1 ? steps[0] : steps[(current + 1) % steps.length];
    this.shadingMode = next;
    this.applyShadingMode();
  }

  toggleBones() {
    this.showBones = !this.showBones;
    this.bonesGroup.visible = this.showBones;
  }

  applyShadingMode() {
    // The wireframe cage is a second set of meshes sharing the model's
    // geometries, so it has to be torn down and rebuilt whenever the look
    // changes rather than toggled.
    this.wireframeGroup.clear();
    const overlayMeshes = [];

    this.modelGroup.traverse(child => {
      if (!child.isMesh || !child.userData) return;
      const ud = child.userData;
      const isRoom = this.currentModelData &&
        (this.currentModelData.model_type === 'room' || this.currentModelData.type === 'room');
      const parserDiagnostics = this.currentModelData &&
        this.currentModelData.diagnostics &&
        this.currentModelData.diagnostics.parser;
      const isFF2WCharacter = !!(
        this.currentModelData &&
        (this.currentModelData.model_type === 'character' ||
         this.currentModelData.type === 'character') &&
        parserDiagnostics &&
        parserDiagnostics.container === 'pk3'
      );
      const map = ud.hasTexture ? (child.material.map || ud.originalMat.map) : null;
      // Vertex colours are a modifier on the textured looks, not a mode of their
      // own, so the two shading buttons keep their meaning. Texture-less room
      // meshes are the exception: their baked colours are the only thing they
      // have, so they always show them.
      const useVertexColors = ud.hasColors && (map ? this.vertexColorsOn : true);

      switch (this.shadingMode) {
        case 'solid':
          child.material = new THREE.MeshStandardMaterial({
            color: 0xdddddd,
            roughness: 0.6,
            metalness: 0.1,
            side: THREE.FrontSide
          });
          break;

        case 'wireframe':
          child.material = new THREE.MeshBasicMaterial({
            color: 0x38bdf8,
            wireframe: true,
            side: THREE.FrontSide
          });
          break;

        // 'wireframe_over' keeps the textured model exactly as 'textured' draws
        // it and stacks a cage over it, which one material cannot do; the cage
        // is collected below and added once the loop is done.
        case 'wireframe_over':
        case 'textured':
        default: {
          const TexturedMaterial = isRoom && map
            ? THREE.MeshBasicMaterial
            : THREE.MeshStandardMaterial;
          child.material = new TexturedMaterial({
            map: map,
            // Three.js multiplies the sampled texture by COLOR_0 when both are
            // enabled; the default white base colour keeps the vertex colours
            // the only multiplier besides the texture itself.
            color: 0xffffff,
            vertexColors: useVertexColors,
            side: THREE.FrontSide,
            ...this.getTextureAlphaSettings(map, isRoom, isFF2WCharacter),
            ...(TexturedMaterial === THREE.MeshStandardMaterial ? {
              roughness: 0.82,
              metalness: 0.08
            } : {})
          });
          break;
        }
      }

      if (this.shadingMode === 'wireframe_over') overlayMeshes.push(child);
    });

    if (overlayMeshes.length > 0) {
      // One material for the whole cage: it is a diagnostic overlay, not part of
      // the asset, so it gets no per-mesh state. Sharing the geometry means no
      // extra memory for the positions, and depthTest off plus a high renderOrder
      // is what puts the lines in front of the surface instead of inside it.
      const overlayMaterial = new THREE.MeshBasicMaterial({
        color: 0x38bdf8,
        wireframe: true,
        transparent: true,
        opacity: 0.9,
        depthTest: false,
        depthWrite: false
      });
      overlayMeshes.forEach((mesh) => {
        const cage = new THREE.Mesh(mesh.geometry, overlayMaterial);
        cage.renderOrder = 998;
        cage.frustumCulled = false;
        this.wireframeGroup.add(cage);
      });
    }
  }

  /**
   * The layers panel, grouped by material rather than by submesh.
   *
   * A character is a hundred-odd submeshes drawn with a few dozen materials, and
   * what is worth switching on and off is the material -- a face, a sleeve, a
   * whole piece of set dressing -- not the strips each one is cut into. The
   * payloads carry real material names ("m000_sodena", "m001_eye02.tm2"), so the
   * rows read as what they draw rather than as "submesh_47".
   */
  buildMeshLayers(data) {
    const panel = document.getElementById('mesh-layers');
    if (!panel) return;
    panel.innerHTML = '';
    const meshes = data.meshes || [];
    const materials = data.materials || [];

    // Payload mesh index -> the object that draws it. The scene builder stamps
    // the index on each mesh because a malformed submesh is skipped, so
    // positions in modelGroup.children do not line up with the payload.
    const objectForMesh = new Map();
    this.modelGroup.children.forEach((child) => {
      const meshIndex = child.userData && child.userData.meshIndex;
      if (Number.isInteger(meshIndex)) objectForMesh.set(meshIndex, child);
    });

    const groups = [];
    const byLook = new Map();
    meshes.forEach((meshData, meshIndex) => {
      const materialIndex = Number.isInteger(meshData.material_index) ? meshData.material_index : -1;
      const material = materials[materialIndex] || null;
      const texId = material ? material.texture_index : (meshData.tex_id ?? -1);
      // Grouped by what the row actually switches -- a name and the texture it
      // draws -- rather than by the material slot. These files repeat both:
      // m001_mafuyu holds nine materials named "m001_mafuyu" that differ only in
      // which body page they point at, so keying on the name alone would hide
      // real differences, while keying on the slot alone would list four
      // identical "m001_hair01" rows. Name plus texture slot collapses exactly
      // the duplicates and nothing that looks different: 33 rows become 23 for
      // m001_mafuyu, 40 become 31 for m000_miku.
      const key = materialIndex >= 0
        ? `mat:${material && material.name ? material.name : materialIndex}:${texId}`
        : `tex:${texId}`;
      let group = byLook.get(key);
      if (!group) {
        group = { materialIndex, material, texId, meshIndices: [] };
        byLook.set(key, group);
        groups.push(group);
      }
      group.meshIndices.push(meshIndex);
    });

    if (groups.length === 0) {
      panel.innerHTML = '<span class="muted">No material layers.</span>';
      return;
    }

    // Count how often each name is reused first: these formats repeat the same
    // name across materials that differ only in their texture slot, and the
    // label has to say which one is which or the rows are indistinguishable.
    const nameUse = new Map();
    groups.forEach((group) => {
      const name = materialName(group);
      nameUse.set(name, (nameUse.get(name) || 0) + 1);
    });

    // A repeated name is numbered by which one it is rather than by the VRAM
    // slot it draws: "r-00-tex-01 ·2" is a row, "r-00-tex-01 · tex 17" is a
    // sentence. The slot it names goes to the tooltip, where there is room for
    // it and where it is actually wanted.
    const seen = new Map();

    groups.forEach((group) => {
      const row = document.createElement('label');
      row.className = 'mesh-layer-row';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = true;
      const objects = group.meshIndices
        .map((meshIndex) => objectForMesh.get(meshIndex))
        .filter(Boolean);
      const setVisible = (visible) => { objects.forEach((obj) => { obj.visible = visible; }); };
      checkbox.addEventListener('change', () => setVisible(checkbox.checked));

      const name = materialName(group);
      let suffix = '';
      if (nameUse.get(name) > 1) {
        const occurrence = (seen.get(name) || 0) + 1;
        seen.set(name, occurrence);
        suffix = ` ·${occurrence}`;
      }
      const count = group.meshIndices.length;
      const text = document.createElement('span');
      // Just the material name. The submesh count and the VRAM slot it draws are
      // still there, in the tooltip -- on screen they were noise on every row.
      text.textContent = `${name}${suffix}`;
      const slot = group.material ? group.material.texture_index : group.texId;
      row.title = `${count} submesh(es) · VRAM tex ${slot}` +
        (nameUse.get(name) > 1 ? '' : ' · only material with this name');
      row.append(checkbox, text);
      panel.appendChild(row);

      // Remembered so a future "hide all / show all" and any other batch
      // visibility change can go through the same grouping.
      group.objects = objects;
      group.setVisible = setVisible;
    });

    this.materialLayerGroups = groups;
  }

  setAllMeshLayers(visible) {
    this.modelGroup.children.forEach(mesh => { mesh.visible = visible; });
    document.querySelectorAll('#mesh-layers input[type="checkbox"]').forEach(box => { box.checked = visible; });
  }

  updateStatsUI(data) {
    const setVal = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    };

    setVal('stat-name', data.filename || '-');
    setVal('stat-type', data.type ? data.type.toUpperCase() : '-');
    setVal('stat-verts', data.stats ? data.stats.vertices.toLocaleString() : '0');
    setVal('stat-tris', data.stats ? data.stats.triangles.toLocaleString() : '0');
    setVal('stat-submeshes', data.stats ? data.stats.submeshes : '0');
    setVal('stat-bones', data.stats ? data.stats.bones : '0');
    setVal('stat-textures', data.stats ? data.stats.textures : '0');

    // Which internal name each VRAM page goes by. The parsers carry the name on
    // the material rather than on the image ("m001_sodena", "m001_eye02.tm2"),
    // so the page's name is the name of the materials that draw it. Several
    // materials can share a page, and several pages can share a name (the
    // character's body is nine pages all called "m001_mafuyu"), so this is a
    // page -> names lookup and nothing more.
    const namesBySlot = new Map();
    (data.materials || []).forEach((mat) => {
      if (!Number.isInteger(mat.texture_index) || !mat.name) return;
      if (!namesBySlot.has(mat.texture_index)) namesBySlot.set(mat.texture_index, new Set());
      namesBySlot.get(mat.texture_index).add(mat.name);
    });
    this.textureNamesBySlot = namesBySlot;

    const texGrid = document.getElementById('texture-grid');
    if (texGrid) {
      texGrid.innerHTML = '';
      if (data.textures && data.textures.length > 0) {
        this.closeTexturePreview();
        data.textures.forEach((tex, idx) => {
          // A real <button>, so it is reachable by keyboard and announces
          // itself; the card is the square slot in the grid.
          const card = document.createElement('button');
          card.type = 'button';
          card.className = 'texture-card';
          const name = this.textureNameForSlot(idx);
          card.title = (name ? name + ' · ' : '') + 'VRAM Tex ' + idx +
            ' (' + tex.width + 'x' + tex.height + ') — click to enlarge';
          card.innerHTML = '<img src="' + tex.data_uri + '" alt="Texture ' + idx +
            ' (' + tex.width + ' by ' + tex.height + ')" /><div class="texture-card-label">' +
            tex.width + 'x' + tex.height + '</div>';
          card.addEventListener('click', () => this.openTexturePreview(idx, tex));
          texGrid.appendChild(card);
        });
      } else {
        texGrid.innerHTML = '<div style="color: var(--text-muted); font-size: 10px;">No textures in VRAM</div>';
      }
    }
  }

  /**
   * The internal name the asset itself gives a VRAM page, or '' when the
   * parsers recovered no name for it. Several materials can share a page and
   * can carry different names, so the names are joined rather than one of them
   * being picked arbitrarily.
   */
  textureNameForSlot(index) {
    const names = this.textureNamesBySlot && this.textureNamesBySlot.get(index);
    return names ? [...names].join(' / ') : '';
  }

  /**
   * One decoded texture at full size, over a backdrop that dims the rest of the
   * tool. The slot grid stays at thumbnail size because a 32x128 sheet is
   * unreadable in it, and decoding these again for a second grid would double
   * the memory for no reason -- so the preview reuses the data URI the card
   * already holds.
   */
  openTexturePreview(index, tex) {
    const overlay = document.getElementById('texture-preview');
    const image = document.getElementById('texture-preview-image');
    const title = document.getElementById('texture-preview-title');
    const meta = document.getElementById('texture-preview-meta');
    if (!overlay || !image) return;
    image.src = tex.data_uri;
    // Titled by the name the asset gives the page, not by its slot number: the
    // slot only says where it sits in VRAM, the name says what it is. The slot
    // moves to the subtitle so it is still on screen.
    const name = this.textureNameForSlot(index);
    image.alt = (name ? name + ', ' : '') + 'VRAM texture ' + index +
      ', ' + tex.width + ' by ' + tex.height + ' pixels';
    if (title) title.textContent = name || 'Texture ' + index;
    if (meta) meta.textContent = tex.width + ' × ' + tex.height + ' px · VRAM ' + index;
    overlay.classList.remove('hidden');
    this.currentTexturePreview = index;
    const close = document.getElementById('btn-texture-preview-close');
    if (close) close.focus();
  }

  closeTexturePreview() {
    const overlay = document.getElementById('texture-preview');
    if (!overlay) return;
    overlay.classList.add('hidden');
    this.currentTexturePreview = null;
    const image = document.getElementById('texture-preview-image');
    // Drops the decoded bitmap instead of leaving it attached to the document.
    if (image) image.removeAttribute('src');
  }

  fitCameraToModel() {
    const box = new THREE.Box3().setFromObject(this.modelGroup);
    if (box.isEmpty()) {
      box.setFromObject(this.collisionGroup);
    }
    if (box.isEmpty() || !Number.isFinite(box.min.x) || !Number.isFinite(box.max.x)) {
      console.warn('Unable to fit camera: model has no finite bounds');
      return;
    }

    const center = new THREE.Vector3();
    box.getCenter(center);
    const size = new THREE.Vector3();
    box.getSize(size);

    const maxDim = Math.max(size.x, size.y, size.z);
    if (!Number.isFinite(maxDim) || maxDim <= 0) {
      console.warn('Unable to fit camera: model bounds have invalid dimensions');
      return;
    }
    const fov = this.camera.fov * (Math.PI / 180);
    let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 1.6;
    cameraZ = Math.max(cameraZ, 25);

    // Scale the depth range to the model instead of using the fixed 0.5..60000
    // from the constructor. For a ~170 unit character that old range gave a
    // 1:120000 near/far ratio, so the depth buffer ran out of precision and
    // coplanar surfaces (body against clothing, hair against face) z-fought.
    // The reference MDLB renderer derives both planes from the bounding sphere
    // and the orbit distance for the same reason; the ratio stays small because
    // the near plane only ever reaches the closest visible geometry.
    const radius = Math.max(size.length() * 0.5, maxDim * 0.5, 1e-4);
    const reach = radius * 1.15 + 0.05;
    const farPlane = Math.max(reach * 1.5, cameraZ + reach);
    const nearPlane = cameraZ > reach
      ? Math.max(0.002, (cameraZ - reach) * 0.5)
      : Math.max(0.002, farPlane * 0.0005);
    this.camera.near = nearPlane;
    this.camera.far = farPlane;
    // Remembered so updateDepthRange() can retune the planes as the user orbits.
    this.modelRadius = radius;
    this.camera.updateProjectionMatrix();
    // Kill the orbit inertia before moving, not after. With damping on, the
    // controls keep a leftover rotation delta that update() applies and then
    // decays, so resetting the camera mid-drag left it drifting for a second or
    // two afterwards. One update with damping off consumes the whole delta and
    // leaves none behind, and it happens before the new position is set so the
    // consumed throwaway does not move the camera being placed.
    const damping = this.controls.enableDamping;
    this.controls.enableDamping = false;
    this.controls.update();
    this.controls.enableDamping = damping;
    this.camera.position.set(center.x, center.y + maxDim * 0.35, center.z + cameraZ);
    this.controls.target.copy(center);
    this.camera.lookAt(center);
    this.controls.update();
  }

  async executeExport() {
    if (!this.currentModelData) return;

    const targetRadio = document.querySelector('input[name="export-target"]:checked');
    const target = targetRadio ? targetRadio.value : 'model';

    const fmtRadio = document.querySelector('input[name="export-fmt"]:checked');
    const format = fmtRadio ? fmtRadio.value : 'glb';
    const statusEl = document.getElementById('export-status');

    const label = target === 'collision' ? 'Collision' : 'Model';
    this.showLoading('Exporting ' + label + ' to ' + format.toUpperCase() + '...');
    if (statusEl) statusEl.textContent = 'Generating 3D ' + label.toLowerCase() + ' file...';

    try {
      // Vertex colours and texture extraction are no longer options: rooms and
      // props always carry their colours out, and every model writes its PNGs
      // next to itself. T-pose was the opposite case -- it mangled the animation
      // rig for anyone who wanted the bind pose and was never wanted by anyone
      // who did not -- so it is simply off now instead of on by default.
      const body = {
        target: target,
        format: format,
        include_colors: true,
        include_textures: true,
        include_collision: false,
        tpose_only: false,
        destination: this.exportDestination || ''
      };

      const res = await fetch('/api/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || 'Export failed');
      }

      const blob = await res.blob();
      const base = (this.currentModelData.filename || this.currentModelData.name || 'export').replace(/\.[^.]+$/, '');
      let filename = target === 'collision' ? `${base}_collision.${format}` : `${base}.${format}`;
      if (format.includes('zip')) {
        filename = `${base}_bundle.zip`;
      } else if (format === 'png') {
        filename = `${base}_textures.zip`;
      }

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      const modal = document.getElementById('export-modal');
      if (modal) modal.classList.add('hidden');

      this.exportDestination = '';
      this.updateExportTargetUI();
      this.showToast('Saved ' + filename + ' to downloads!', 'success');
    } catch (err) {
      this.showToast('Export error: ' + err.message, 'error');
    } finally {
      this.hideLoading();
    }
  }

  /**
   * Keep the depth range tight around the model as the orbit distance changes.
   * The reference renderer recomputes both planes every frame for this reason:
   * a near plane far in front of the geometry wastes depth precision, and a far
   * plane far past it makes z-fighting visible on close-up body surfaces.
   */
  updateDepthRange() {
    if (!this.modelRadius || !Number.isFinite(this.modelRadius)) return;
    const distance = this.camera.position.distanceTo(this.controls.target);
    if (!Number.isFinite(distance)) return;
    const reach = this.modelRadius * 1.15 + 0.05;
    const far = Math.max(reach * 1.5, distance + reach);
    const near = distance > reach
      ? Math.max(0.002, (distance - reach) * 0.5)
      : Math.max(0.002, far * 0.0005);
    if (near === this.camera.near && far === this.camera.far) return;
    this.camera.near = near;
    this.camera.far = far;
    this.camera.updateProjectionMatrix();
  }

  animate() {
    requestAnimationFrame(this.animate);

    this.controls.update();
    this.updateDepthRange();
    this.renderer.render(this.scene, this.camera);
  }
}


window.addEventListener('DOMContentLoaded', () => {
  window.pzApp = new PZViewerApp();
  window.batchManager = new BatchManager(window.pzApp);
});


// ─────────────────────────────────────────────────────────────────────────────
// Batch Convert Manager
// ─────────────────────────────────────────────────────────────────────────────
class BatchManager {
  constructor(app) {
    this.app = app;
    this.jobId = null;
    this.pollTimer = null;
    this.logEntries = [];
    this.init();
  }

  init() {
    const modal       = document.getElementById('batch-modal');
    const btnOpen     = document.getElementById('btn-batch-modal');
    const btnClose    = document.getElementById('btn-batch-close');
    const btnCancel   = document.getElementById('btn-batch-modal-cancel');
    const btnStart    = document.getElementById('btn-batch-start');
    const btnStop     = document.getElementById('btn-batch-cancel-job');
    const btnNew      = document.getElementById('btn-batch-new');
    const btnSrc      = document.getElementById('btn-batch-pick-source');
    const btnOut      = document.getElementById('btn-batch-pick-output');
    const btnCurrent  = document.getElementById('btn-batch-use-current');

    const close = () => modal && modal.classList.add('hidden');

    if (btnOpen)   btnOpen.addEventListener('click',  () => modal.classList.remove('hidden'));
    if (btnClose)  btnClose.addEventListener('click',  close);
    if (btnCancel) btnCancel.addEventListener('click', close);
    if (modal) modal.querySelector('.modal-backdrop')?.addEventListener('click', close);

    if (btnStart) btnStart.addEventListener('click', () => this.startBatch());
    if (btnStop)  btnStop.addEventListener('click',  () => this.cancelBatch());
    if (btnNew)   btnNew.addEventListener('click',   () => this.resetToForm());

    // Folder pickers
    if (btnSrc) {
      btnSrc.addEventListener('click', async () => {
        const dir = document.getElementById('batch-source-dir').value;
        const chosen = await this.pickFolder(dir);
        if (chosen) document.getElementById('batch-source-dir').value = chosen;
      });
    }
    if (btnOut) {
      btnOut.addEventListener('click', async () => {
        const dir = document.getElementById('batch-output-dir').value;
        const chosen = await this.pickFolder(dir);
        if (chosen) document.getElementById('batch-output-dir').value = chosen;
      });
    }

    // "Use Current Browser Folder" — reads app's current browsing directory
    if (btnCurrent) {
      btnCurrent.addEventListener('click', () => {
        const curDir = document.getElementById('input-dir')?.value || '';
        if (curDir) {
          document.getElementById('batch-source-dir').value = curDir;
        } else {
          this.app.showToast('Navigate to a folder in the browser first.', 'error');
        }
      });
    }
  }

  async pickFolder(initialDir) {
    try {
      const res = await fetch('/api/choose_folder?dir=' + encodeURIComponent(initialDir || ''));
      const data = await res.json();
      return data.chosen || '';
    } catch (e) {
      this.app.showToast('Folder picker error: ' + e.message, 'error');
      return '';
    }
  }

  async startBatch() {
    const sourceDir = document.getElementById('batch-source-dir').value.trim();
    const outputDir = document.getElementById('batch-output-dir').value.trim();
    const fmt = document.querySelector('input[name="batch-fmt"]:checked')?.value || 'glb';
    const recursive     = document.getElementById('batch-recursive').checked;
    const incTextures   = document.getElementById('batch-textures').checked;
    const incColors     = document.getElementById('batch-colors').checked;
    const mirror        = document.getElementById('batch-mirror').checked;
    const conflict      = document.getElementById('batch-conflict').value;

    if (!sourceDir) {
      this.app.showToast('Please choose a source folder first.', 'error');
      return;
    }

    this.showProgress();
    this.logEntries = [];

    try {
      const res = await fetch('/api/batch_export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_dir:       sourceDir,
          output_dir:       outputDir,
          format:           fmt,
          recursive:        recursive,
          include_textures: incTextures,
          include_colors:   incColors,
          tpose_only:       false,
          mirror_structure: mirror,
          conflict:         conflict,
        })
      });

      const data = await res.json();
      if (!res.ok || data.error) {
        throw new Error(data.error || 'Failed to start batch');
      }

      this.jobId = data.job_id;
      this.startPolling();

    } catch (err) {
      this.resetToForm();
      this.app.showToast('Batch error: ' + err.message, 'error');
    }
  }

  startPolling() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = setInterval(() => this.pollStatus(), 1200);
  }

  async pollStatus() {
    if (!this.jobId) return;
    try {
      const res = await fetch(`/api/batch_status?job=${this.jobId}`);
      if (!res.ok) return;
      const job = await res.json();
      this.updateProgress(job);
      if (job.status !== 'running') {
        clearInterval(this.pollTimer);
        this.pollTimer = null;
        this.onBatchDone(job);
      }
    } catch (e) {
      // network hiccup — keep polling
    }
  }

  updateProgress(job) {
    const total = job.total || 0;
    const done  = job.done  || 0;
    const pct   = total > 0 ? Math.min(100, Math.round(done / total * 100)) : 0;

    const label = document.getElementById('batch-progress-label');
    const count = document.getElementById('batch-progress-count');
    const bar   = document.getElementById('batch-progress-bar');
    const logEl = document.getElementById('batch-log');

    if (label) label.textContent = job.current ? `Processing: ${job.current}` : 'Running...';
    if (count) count.textContent = total > 0 ? `${done} / ${total} (${job.failed || 0} errors)` : '';
    if (bar)   bar.style.width = pct + '%';

    // Append new log lines
    if (logEl && job.log) {
      const newEntries = job.log.slice(this.logEntries.length);
      newEntries.forEach(entry => {
        this.logEntries.push(entry);
        const line = document.createElement('div');
        const cls = entry.startsWith('✓') ? 'log-ok' : entry.startsWith('⏭') ? 'log-skip' : entry.startsWith('✗') ? 'log-err' : '';
        if (cls) line.className = cls;
        line.textContent = entry;
        logEl.appendChild(line);
      });
      if (newEntries.length > 0) logEl.scrollTop = logEl.scrollHeight;
    }
  }

  onBatchDone(job) {
    const done    = job.done    || 0;
    const failed  = job.failed  || 0;
    const skipped = job.skipped || 0;
    const ok      = done - failed - skipped;

    const msgEl  = document.getElementById('batch-done-msg');
    const detail = document.getElementById('batch-done-detail');
    const link   = document.getElementById('batch-output-link');
    const label  = document.getElementById('batch-progress-label');
    const bar    = document.getElementById('batch-progress-bar');

    if (label) label.textContent = job.status === 'cancelled' ? '⏹ Batch cancelled.' : '✅ Batch complete!';
    if (bar)   { bar.style.width = '100%'; bar.style.background = failed > 0 ? '#ef4444' : '#4ade80'; }

    // Update stop/new buttons
    document.getElementById('btn-batch-cancel-job')?.classList.add('hidden');
    document.getElementById('btn-batch-new')?.classList.remove('hidden');

    if (msgEl) msgEl.classList.remove('hidden');
    if (detail) detail.textContent = ` Converted: ${ok}, Skipped: ${skipped}, Errors: ${failed}.`;
    if (link && job.output_dir) {
      link.textContent = `📂 Output: ${job.output_dir}`;
      link.href = '#';
      link.onclick = (e) => { e.preventDefault(); navigator.clipboard?.writeText(job.output_dir); };
    }
  }

  async cancelBatch() {
    if (!this.jobId) return;
    try {
      await fetch('/api/batch_cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: this.jobId })
      });
    } catch (e) { /* ignore */ }
  }

  showProgress() {
    document.getElementById('batch-setup-form')?.classList.add('hidden');
    document.getElementById('batch-progress-view')?.classList.remove('hidden');
    document.getElementById('btn-batch-start')?.classList.add('hidden');
    document.getElementById('btn-batch-cancel-job')?.classList.remove('hidden');
    document.getElementById('btn-batch-new')?.classList.add('hidden');
    document.getElementById('batch-done-msg')?.classList.add('hidden');
    document.getElementById('batch-log').innerHTML = '';
    document.getElementById('batch-progress-bar').style.width = '0%';
    document.getElementById('batch-progress-bar').style.background = 'linear-gradient(90deg, #6366f1, #38bdf8)';
    document.getElementById('batch-progress-label').textContent = 'Scanning files...';
    document.getElementById('batch-progress-count').textContent = '';
  }

  resetToForm() {
    if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; }
    this.jobId = null;
    this.logEntries = [];
    document.getElementById('batch-setup-form')?.classList.remove('hidden');
    document.getElementById('batch-progress-view')?.classList.add('hidden');
    document.getElementById('btn-batch-start')?.classList.remove('hidden');
    document.getElementById('btn-batch-cancel-job')?.classList.add('hidden');
    document.getElementById('btn-batch-new')?.classList.add('hidden');
  }
}
