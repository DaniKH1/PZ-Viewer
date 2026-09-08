/**
 * PZViewer - GPU Accelerated 3D Engine for Project Zero / Fatal Frame Assets
 */

class PZViewerApp {
  constructor() {
    this.container = document.getElementById('viewport');
    this.currentModelData = null;
    this.currentAnimData = null;
    this.textures = {};

    this.isPlaying = false;
    this.isTPose = true;
    this.currentClipIndex = 0;
    this.currentFrame = 0;
    this.totalFrames = 0;
    this.playbackSpeed = 1.0;
    this.lastFrameTime = 0;
    this.baseFps = 30;

    this.shadingMode = 'textured';
    this.exportDestination = '';
    this.showCollision = false;
    this.showBones = false;

    this.initThreeGPU();
    this.detectGPUInfo();
    this.initUI();
    this.initEventListeners();

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

    window.addEventListener('resize', () => {
      if (!this.container) return;
      const w = this.container.clientWidth;
      const h = this.container.clientHeight;
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(w, h);
    });
  }

  detectGPUInfo() {
    let gpuRenderer = 'Hardware Accelerated GPU';
    try {
      const gl = this.renderer.getContext();
      if (gl) {
        const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
        if (debugInfo) {
          const raw = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL);
          if (raw) {
            const match = raw.match(/ANGLE \([^,]+,\s*([^,]+?)(?: Direct3D|\))/i);
            gpuRenderer = (match && match[1]) ? match[1] : raw;
          }
        } else {
          const renderer = gl.getParameter(gl.RENDERER);
          if (renderer) gpuRenderer = renderer;
        }
      }
    } catch (e) {
      console.warn('Could not query GPU debug info:', e);
      gpuRenderer = 'Direct3D / WebGL GPU';
    }
    const gpuEl = document.getElementById('gpu-name');
    if (gpuEl) {
      gpuEl.textContent = 'GPU: ' + gpuRenderer;
    }
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
    const chooseFolderBtn = document.getElementById('btn-choose-folder');
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
    if (chooseFolderBtn && dirInput) {
      chooseFolderBtn.addEventListener('click', async () => {
        try {
          const res = await fetch('/api/choose_folder?dir=' + encodeURIComponent(dirInput.value));
          const data = await res.json();
          if (data.chosen) {
            dirInput.value = data.chosen;
            this.browseDir(data.chosen);
          }
        } catch (err) {
          console.error('Folder picker error:', err);
        }
      });
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

    const shadingSelect = document.getElementById('select-shading');
    if (shadingSelect) {
      shadingSelect.addEventListener('change', (e) => {
        this.shadingMode = e.target.value;
        this.applyShadingMode();
      });
    }

    const chkBones = document.getElementById('chk-bones');
    if (chkBones) {
      chkBones.addEventListener('change', (e) => {
        this.showBones = e.target.checked;
        this.bonesGroup.visible = this.showBones;
      });
    }

    const chkCollision = document.getElementById('chk-collision');
    if (chkCollision) {
      chkCollision.addEventListener('change', (e) => {
        this.showCollision = e.target.checked;
        this.collisionGroup.visible = this.showCollision;
      });
    }

    const layersModal = document.getElementById('layers-modal');
    document.getElementById('btn-mesh-layers')?.addEventListener('click', () => layersModal?.classList.remove('hidden'));
    document.getElementById('btn-close-layers')?.addEventListener('click', () => layersModal?.classList.add('hidden'));
    document.getElementById('btn-layers-all')?.addEventListener('click', () => this.setAllMeshLayers(true));
    document.getElementById('btn-layers-none')?.addEventListener('click', () => this.setAllMeshLayers(false));

    const resetCam = document.getElementById('btn-reset-cam');
    if (resetCam) {
      resetCam.addEventListener('click', () => this.fitCameraToModel());
    }

    const btnTPose = document.getElementById('btn-t-pose');
    const btnAnimated = document.getElementById('btn-animated');

    if (btnTPose && btnAnimated) {
      btnTPose.addEventListener('click', () => {
        btnTPose.classList.add('active');
        btnAnimated.classList.remove('active');
        this.isTPose = true;
        this.pause();
        this.resetToTPose();
      });

      btnAnimated.addEventListener('click', () => {
        btnAnimated.classList.add('active');
        btnTPose.classList.remove('active');
        this.isTPose = false;
        this.applyAnimationFrame(this.currentFrame);
      });
    }

    const btnPlay = document.getElementById('btn-play-pause');
    if (btnPlay) {
      btnPlay.addEventListener('click', () => {
        if (this.isPlaying) {
          this.pause();
        } else {
          if (this.isTPose && btnAnimated) {
            btnAnimated.click();
          }
          this.play();
        }
      });
    }

    const slider = document.getElementById('timeline-slider');
    if (slider) {
      slider.addEventListener('input', (e) => {
        this.pause();
        if (this.isTPose && btnAnimated) {
          btnAnimated.click();
        }
        this.seekFrame(parseInt(e.target.value));
      });
    }

    const speedSelect = document.getElementById('select-speed');
    if (speedSelect) {
      speedSelect.addEventListener('change', (e) => {
        this.playbackSpeed = parseFloat(e.target.value);
      });
    }

    const clipSelect = document.getElementById('select-clip');
    if (clipSelect) {
      clipSelect.addEventListener('change', (e) => {
        this.selectClip(parseInt(e.target.value));
      });
    }

    const btnLoadAnm = document.getElementById('btn-load-anm');
    if (btnLoadAnm) {
      btnLoadAnm.addEventListener('click', () => {
        const animPath = prompt('Enter path to .anm animation file:');
        if (animPath) this.loadAnimation(animPath);
      });
    }

    const btnDetachAnm = document.getElementById('btn-detach-anm');
    if (btnDetachAnm) {
      btnDetachAnm.addEventListener('click', () => this.detachAnimation());
    }

    const btnExportModal = document.getElementById('btn-export-modal');
    const btnExportCollision = document.getElementById('btn-export-collision');
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
        if (modelRadio) {
          modelRadio.checked = true;
          this.updateExportTargetUI('model');
        }
        modal.classList.remove('hidden');
      });
    }

    if (btnExportCollision && modal) {
      btnExportCollision.addEventListener('click', () => {
        if (!this.currentModelData) {
          this.showToast('No model loaded to export!', 'error');
          return;
        }
        const colRadio = document.querySelector('input[name="export-target"][value="collision"]');
        if (colRadio) {
          colRadio.checked = true;
          this.updateExportTargetUI('collision');
        }
        modal.classList.remove('hidden');
      });
    }

    document.querySelectorAll('input[name="export-target"]').forEach(r => {
      r.addEventListener('change', (e) => {
        this.updateExportTargetUI(e.target.value);
      });
    });

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
          const res = await fetch('/api/export_textures');
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

  updateExportTargetUI(target) {
    const modelOpts = document.getElementById('model-options-box');
    const colOpts = document.getElementById('collision-options-box');
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
            this.updateExportTargetUI(target);
          }
        } catch (err) {
          this.showToast('Folder picker error: ' + err.message, 'error');
        }
      });
    }

    if (target === 'collision') {
      if (modelOpts) modelOpts.classList.add('hidden');
      if (colOpts) colOpts.classList.remove('hidden');
      if (daeLbl) daeLbl.classList.add('hidden');
      if (fbxLbl) fbxLbl.classList.add('hidden');
      if (titleEl) titleEl.textContent = 'Export 3D Collision Geometry';
      if (btnDo) btnDo.textContent = 'Export';
    } else {
      if (modelOpts) modelOpts.classList.remove('hidden');
      if (colOpts) colOpts.classList.add('hidden');
      if (daeLbl) daeLbl.classList.remove('hidden');
      if (fbxLbl) fbxLbl.classList.remove('hidden');
      if (titleEl) titleEl.textContent = 'Export 3D Model';
      if (btnDo) btnDo.textContent = 'Export';
    }
    const vertexColorsLabel = document.getElementById('lbl-exp-vcolors');
    const isRoom = this.currentModelData &&
      (this.currentModelData.model_type === 'room' || this.currentModelData.type === 'room');
    if (vertexColorsLabel) vertexColorsLabel.classList.toggle('hidden', !isRoom);
  }

  initEventListeners() {
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
      if (e.code === 'Space') {
        e.preventDefault();
        const playBtn = document.getElementById('btn-play-pause');
        if (playBtn) playBtn.click();
      } else if (e.code === 'KeyF') {
        this.fitCameraToModel();
      } else if (e.code === 'KeyT') {
        const btnT = document.getElementById('btn-t-pose');
        const btnA = document.getElementById('btn-animated');
        if (this.isTPose && btnA) btnA.click();
        else if (!this.isTPose && btnT) btnT.click();
      } else if (e.code === 'ArrowRight') {
        this.seekFrame(Math.min(this.totalFrames - 1, this.currentFrame + 1));
      } else if (e.code === 'ArrowLeft') {
        this.seekFrame(Math.max(0, this.currentFrame - 1));
      }
    });
  }

  async browseDir(dirPath) {
    const fileListEl = document.getElementById('file-list');
    if (!fileListEl) return;
    fileListEl.innerHTML = '<div class="loading-hint">Reading directory...</div>';

    try {
      const res = await fetch('/api/browse?dir=' + encodeURIComponent(dirPath));
      const data = await res.json();
      fileListEl.innerHTML = '';

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
        upRow.style.fontWeight = 'bold';
        upRow.style.color = '#38bdf8';
        upRow.innerHTML = '<span>📁</span> <span>.. (Parent Directory)</span>';
        upRow.addEventListener('click', () => {
          this.browseDir(data.parent_dir);
        });
        fileListEl.appendChild(upRow);
      }

      if (data.items && data.items.length > 0) {
        data.items.forEach(item => {
          const row = document.createElement('div');
          row.className = 'file-item ' + (item.is_dir ? 'dir' : 'file-' + item.type);
          row.dataset.search = (item.name + ' ' + item.type).toLowerCase();
          const iconMap = { sgd_pack: '🧩', sgd: '📄', mdl: '🤖', anm: '🎬', bmd: '🎬', cld: '🛡️', tm2: '🖼️', png: '🖼️', pk2: '📦', pk4: '📦' };
          const icon = item.is_dir ? '📁' : (iconMap[item.type] || '📄');
          const size = item.is_dir ? 'folder' : this.formatFileSize(item.size);
          row.innerHTML = '<span class="file-icon">' + icon + '</span><span class="file-name">' +
            item.name + '</span><span class="file-meta">' + size + '</span>';

          row.addEventListener('click', () => {
            if (item.is_dir) {
              this.browseDir(item.path);
            } else {
              this.loadFile(item.path);
            }
          });
          fileListEl.appendChild(row);
        });
      } else {
        fileListEl.innerHTML = '<div class="loading-hint">No compatible 3D files found</div>';
      }
    } catch (e) {
      fileListEl.innerHTML = '<div class="loading-hint" style="color: #ef4444;">Error: ' + e.message + '</div>';
    }
  }

  filterBrowserItems(query) {
    const needle = query.trim().toLowerCase();
    document.querySelectorAll('#file-list .file-item').forEach(row => {
      row.hidden = !!needle && !(row.dataset.search || '').includes(needle);
    });
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

  showToast(msg, type) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.className = 'toast show ' + (type || 'info');
    setTimeout(() => {
      toast.className = 'toast';
    }, 3500);
  }

  async loadFile(filePath) {
    const fn = filePath.split('/').pop();
    this.showLoading('Loading ' + fn + ' into GPU VRAM...');
    try {
      const res = await fetch('/api/load?path=' + encodeURIComponent(filePath));
      const data = await res.json();
      if (data.error) {
        this.showToast('Error: ' + data.error, 'error');
        return;
      }

      this.currentModelData = data;
      this.buildMeshLayers(data);

      // Shading mode defaults to textured for all model types (rooms, characters, props)
      const shadingSelect = document.getElementById('select-shading');
      this.shadingMode = 'textured';
      if (shadingSelect) shadingSelect.value = 'textured';

      this.buildGPUScene(data);
      this.updateStatsUI(data);

      const animPanel = document.getElementById('anim-panel');
      if (data.animations && data.animations.length > 0) {
        this.setupAnimations(data.animations);
        if (animPanel) animPanel.classList.remove('hidden');
      } else {
        this.setupAnimations([]);
        if (animPanel) animPanel.classList.add('hidden');
      }

      this.fitCameraToModel();
      this.showToast('Loaded ' + data.filename + ' on GPU successfully!', 'success');
    } catch (err) {
      console.error(err);
      this.showToast('Failed: ' + err.message, 'error');
    } finally {
      this.hideLoading();
    }
  }

  async loadAnimation(animPath) {
    this.showLoading('Loading Animation Keyframes...');
    try {
      const res = await fetch('/api/load_anim?path=' + encodeURIComponent(animPath));
      const data = await res.json();
      if (data.error) {
        this.showToast(data.error, 'error');
        return;
      }
      this.setupAnimations(data.animations);
      const animPanel = document.getElementById('anim-panel');
      if (animPanel) animPanel.classList.remove('hidden');
      this.showToast('Loaded ' + data.animations.length + ' animation clips!', 'success');
    } catch (err) {
      this.showToast(err.message, 'error');
    } finally {
      this.hideLoading();
    }
  }

  async detachAnimation() {
    try {
      await fetch('/api/detach_anim', { method: 'POST' });
      this.setupAnimations([]);
      this.resetToTPose();
      const btnT = document.getElementById('btn-t-pose');
      const btnA = document.getElementById('btn-animated');
      if (btnT && btnA) {
        btnT.classList.add('active');
        btnA.classList.remove('active');
      }
      this.isTPose = true;
      this.showToast('Animation detached. Pure T-Pose active.', 'info');
    } catch (e) {
      console.error(e);
    }
  }

  buildGPUScene(data) {
    const isRoomAsset = data.model_type === 'room' || data.type === 'room';
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
    if (data.textures) {
      const loader = new THREE.TextureLoader();
      data.textures.forEach((tex, idx) => {
        if (tex.data_uri) {
          const threeTex = loader.load(tex.data_uri);
          // Character UVs are normalized by the SGD parser. Rooms retain the
          // existing image-side flip used by their room UV convention.
          threeTex.flipY = true;
          threeTex.colorSpace = THREE.SRGBColorSpace;
          threeTex.wrapS = THREE.RepeatWrapping;
          threeTex.wrapT = THREE.RepeatWrapping;
          threeTex.magFilter = THREE.NearestFilter;
          threeTex.minFilter = THREE.LinearMipmapLinearFilter;
          threeTex.generateMipmaps = true;
          this.renderer.initTexture(threeTex);
          this.textures[idx] = threeTex;
        }
      });
    }

    if (data.meshes) {
      data.meshes.forEach((meshData, mIdx) => {
        const geom = new THREE.BufferGeometry();

        const posAttr = new THREE.BufferAttribute(new Float32Array(meshData.positions), 3);
        posAttr.setUsage(THREE.StaticDrawUsage);
        geom.setAttribute('position', posAttr);

        if (meshData.normals && meshData.normals.length > 0) {
          const normAttr = new THREE.BufferAttribute(new Float32Array(meshData.normals), 3);
          normAttr.setUsage(THREE.StaticDrawUsage);
          geom.setAttribute('normal', normAttr);
        } else {
          geom.computeVertexNormals();
        }

        if (meshData.uvs && meshData.uvs.length > 0) {
          const uvAttr = new THREE.BufferAttribute(new Float32Array(meshData.uvs), 2);
          uvAttr.setUsage(THREE.StaticDrawUsage);
          geom.setAttribute('uv', uvAttr);
        }

        const hasColors = meshData.colors && meshData.colors.length > 0;
        if (hasColors) {
          const colAttr = new THREE.BufferAttribute(new Float32Array(meshData.colors), 3);
          colAttr.setUsage(THREE.StaticDrawUsage);
          geom.setAttribute('color', colAttr);
        }

        if (meshData.indices && meshData.indices.length > 0) {
          const idxAttr = new THREE.BufferAttribute(new Uint32Array(meshData.indices), 1);
          idxAttr.setUsage(THREE.StaticDrawUsage);
          geom.setIndex(idxAttr);
        }

        geom.computeBoundingSphere();
        geom.computeBoundingBox();

        const tex = this.textures[meshData.tex_id];
        const isRoom = isRoomAsset;
        const meshSide = isRoom ? THREE.FrontSide : THREE.DoubleSide;
        const mat = new THREE.MeshStandardMaterial({
          map: tex || null,
          vertexColors: hasColors,
          side: meshSide,
          transparent: true,
          alphaTest: 0.01,
          roughness: 0.82,
          metalness: 0.08
        });

        const threeMesh = new THREE.Mesh(geom, mat);
        threeMesh.name = meshData.name || ('submesh_' + mIdx);
        threeMesh.userData = {
          hasTexture: !!tex,
          hasColors: hasColors,
          originalMat: mat,
          boneIndex: meshData.bone_index || 0
        };

        this.modelGroup.add(threeMesh);
      });
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
        this.showCollision = true;
        this.collisionGroup.visible = true;
        this.modelGroup.visible = true;
        const chkCollision = document.getElementById('chk-collision');
        if (chkCollision) chkCollision.checked = true;
      }
    }

    this.renderer.compile(this.scene, this.camera);
    this.applyShadingMode();
  }

  buildBonesVisualizer(bones) {
    const sphereGeom = new THREE.SphereGeometry(1.6, 8, 8);
    const sphereMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });

    this.boneNodes = [];
    const linePositions = [];

    bones.forEach((b) => {
      const marker = new THREE.Mesh(sphereGeom, sphereMat);
      marker.position.set(b.pos[0], b.pos[1], b.pos[2]);
      this.bonesGroup.add(marker);

      this.boneNodes.push({
        id: b.id,
        parent: b.parent,
        mesh: marker,
        restPos: [...b.pos],
        restRot: [...b.rot]
      });

      if (b.parent >= 0 && b.parent < bones.length) {
        const p = bones[b.parent];
        linePositions.push(b.pos[0], b.pos[1], b.pos[2]);
        linePositions.push(p.pos[0], p.pos[1], p.pos[2]);
      }
    });

    if (linePositions.length > 0) {
      this.boneLineGeom = new THREE.BufferGeometry();
      this.boneLineGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(linePositions), 3));
      const lineMat = new THREE.LineBasicMaterial({ color: 0x0284c7 });
      this.boneLinesMesh = new THREE.LineSegments(this.boneLineGeom, lineMat);
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

  applyShadingMode() {
    this.modelGroup.traverse(child => {
      if (child.isMesh && child.userData) {
        const ud = child.userData;
        const isRoom = this.currentModelData &&
          (this.currentModelData.model_type === 'room' || this.currentModelData.type === 'room');

        switch (this.shadingMode) {
          case 'textured':
            child.material = new THREE.MeshStandardMaterial({
              map: ud.hasTexture ? child.material.map || ud.originalMat.map : null,
              vertexColors: false,
              side: isRoom ? THREE.FrontSide : THREE.DoubleSide,
              transparent: true,
              alphaTest: 0.01,
              roughness: 0.82,
              metalness: 0.08
            });
            break;

          case 'textured_vertex':
            child.material = new THREE.MeshStandardMaterial({
              map: ud.hasTexture ? child.material.map || ud.originalMat.map : null,
              vertexColors: ud.hasColors,
              side: isRoom ? THREE.FrontSide : THREE.DoubleSide,
              transparent: true,
              alphaTest: 0.01,
              roughness: 0.82,
              metalness: 0.08
            });
            break;

          case 'vcolors':
            child.material = new THREE.MeshBasicMaterial({
              vertexColors: ud.hasColors,
              color: ud.hasColors ? 0xffffff : 0x888888,
              side: isRoom ? THREE.FrontSide : THREE.DoubleSide
            });
            break;

          case 'wireframe':
            child.material = new THREE.MeshBasicMaterial({
              color: 0x38bdf8,
              wireframe: true
            });
            break;

          case 'solid':
            child.material = new THREE.MeshStandardMaterial({
              color: 0xdddddd,
              roughness: 0.6,
              metalness: 0.1,
              side: isRoom ? THREE.FrontSide : THREE.DoubleSide
            });
            break;
        }
      }
    });
  }

  buildMeshLayers(data) {
    const panel = document.getElementById('mesh-layers');
    if (!panel) return;
    panel.innerHTML = '';
    (data.meshes || []).forEach((meshData, index) => {
      const label = document.createElement('label');
      label.className = 'mesh-layer-row';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = true;
      checkbox.addEventListener('change', () => {
        const mesh = this.modelGroup.children[index];
        if (mesh) mesh.visible = checkbox.checked;
      });
      const text = document.createElement('span');
      text.textContent = meshData.name || `Mesh ${index + 1}`;
      label.append(checkbox, text);
      panel.appendChild(label);
    });
    if (!data.meshes || data.meshes.length === 0) {
      panel.innerHTML = '<span class="muted">No mesh layers.</span>';
    }
  }

  setAllMeshLayers(visible) {
    this.modelGroup.children.forEach(mesh => { mesh.visible = visible; });
    document.querySelectorAll('#mesh-layers input[type="checkbox"]').forEach(box => { box.checked = visible; });
  }

  setupAnimations(clips) {
    const clipSelect = document.getElementById('select-clip');
    if (!clipSelect) return;

    clipSelect.innerHTML = '';
    this.currentAnimData = clips;

    if (!clips || clips.length === 0) {
      clipSelect.innerHTML = '<option value="-1">No Animations</option>';
      this.totalFrames = 0;
      this.updateTimelineUI();
      return;
    }

    clips.forEach((clip, idx) => {
      const opt = document.createElement('option');
      opt.value = idx;
      opt.textContent = clip.name + ' (' + clip.num_frames + 'f)';
      clipSelect.appendChild(opt);
    });

    this.selectClip(0);
  }

  selectClip(index) {
    if (!this.currentAnimData || index < 0 || index >= this.currentAnimData.length) return;
    this.currentClipIndex = index;
    const clip = this.currentAnimData[index];
    this.totalFrames = clip.num_frames;
    this.currentFrame = 0;
    this.updateTimelineUI();

    if (!this.isTPose) {
      this.applyAnimationFrame(0);
    }
  }

  updateTimelineUI() {
    const slider = document.getElementById('timeline-slider');
    const counter = document.getElementById('frame-counter');
    if (slider) {
      slider.max = Math.max(0, this.totalFrames - 1);
      slider.value = this.currentFrame;
    }
    if (counter) {
      counter.textContent = 'Frame: ' + this.currentFrame + ' / ' + this.totalFrames;
    }
  }

  play() {
    if (this.totalFrames <= 0) return;
    this.isPlaying = true;
    const playBtn = document.getElementById('btn-play-pause');
    if (playBtn) playBtn.textContent = '⏸️';
  }

  pause() {
    this.isPlaying = false;
    const playBtn = document.getElementById('btn-play-pause');
    if (playBtn) playBtn.textContent = '▶️';
  }

  seekFrame(frame) {
    this.currentFrame = frame;
    this.updateTimelineUI();
    if (!this.isTPose) {
      this.applyAnimationFrame(frame);
    }
  }

  resetToTPose() {
    if (!this.boneNodes) return;
    const linePositions = [];
    this.boneNodes.forEach(bn => {
      bn.mesh.position.set(bn.restPos[0], bn.restPos[1], bn.restPos[2]);
      bn.mesh.rotation.set(bn.restRot[0], bn.restRot[1], bn.restRot[2]);
      if (bn.parent >= 0 && bn.parent < this.boneNodes.length) {
        const p = this.boneNodes[bn.parent];
        linePositions.push(bn.restPos[0], bn.restPos[1], bn.restPos[2]);
        linePositions.push(p.restPos[0], p.restPos[1], p.restPos[2]);
      }
    });
    if (this.boneLineGeom && linePositions.length > 0) {
      this.boneLineGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(linePositions), 3));
      this.boneLineGeom.attributes.position.needsUpdate = true;
    }
  }

  applyAnimationFrame(frame) {
    // BMD transform decoding is still experimental; keep the model in its
    // known rest pose until the track-to-bone mapping is validated.
    this.resetToTPose();
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

    const texGrid = document.getElementById('texture-grid');
    if (texGrid) {
      texGrid.innerHTML = '';
      if (data.textures && data.textures.length > 0) {
        data.textures.forEach((tex, idx) => {
          const card = document.createElement('div');
          card.className = 'texture-card';
          card.title = 'VRAM Tex ' + idx + ' (' + tex.width + 'x' + tex.height + ')';
          card.innerHTML = '<img src="' + tex.data_uri + '" alt="Tex ' + idx + '" /><div class="texture-card-label">' + tex.width + 'x' + tex.height + '</div>';
          texGrid.appendChild(card);
        });
      } else {
        texGrid.innerHTML = '<div style="color: var(--text-muted); font-size: 10px;">No textures in VRAM</div>';
      }
    }
  }

  fitCameraToModel() {
    const box = new THREE.Box3().setFromObject(this.modelGroup);
    if (box.isEmpty()) {
      box.setFromObject(this.collisionGroup);
    }
    if (box.isEmpty()) return;

    const center = new THREE.Vector3();
    box.getCenter(center);
    const size = new THREE.Vector3();
    box.getSize(size);

    const maxDim = Math.max(size.x, size.y, size.z);
    const fov = this.camera.fov * (Math.PI / 180);
    let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 1.6;
    cameraZ = Math.max(cameraZ, 25);

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
    const isRoom = this.currentModelData.model_type === 'room' || this.currentModelData.type === 'room';
    const includeColors = isRoom && (document.getElementById('exp-vcolors')?.checked ?? true);
    const includeTextures = document.getElementById('exp-textures')?.checked ?? true;
    const tposeOnly = document.getElementById('exp-tpose')?.checked ?? true;
    const statusEl = document.getElementById('export-status');

    const label = target === 'collision' ? 'Collision' : 'Model';
    this.showLoading('Exporting ' + label + ' to ' + format.toUpperCase() + '...');
    if (statusEl) statusEl.textContent = 'Generating 3D ' + label.toLowerCase() + ' file...';

    try {
      const body = {
        target: target,
        format: format,
        include_colors: includeColors,
        include_textures: includeTextures,
        include_collision: true,
        tpose_only: tposeOnly,
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
      this.updateExportTargetUI(target);
      this.showToast('Saved ' + filename + ' to downloads!', 'success');
    } catch (err) {
      this.showToast('Export error: ' + err.message, 'error');
    } finally {
      this.hideLoading();
    }
  }

  animate(time) {
    requestAnimationFrame(this.animate);

    if (this.isPlaying && this.totalFrames > 0 && !this.isTPose) {
      if (!this.lastFrameTime) this.lastFrameTime = time;
      const delta = (time - this.lastFrameTime) / 1000;
      const interval = (1 / (this.baseFps * this.playbackSpeed));

      if (delta >= interval) {
        this.currentFrame = (this.currentFrame + 1) % this.totalFrames;
        this.updateTimelineUI();
        this.applyAnimationFrame(this.currentFrame);
        this.lastFrameTime = time;
      }
    }

    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}

window.addEventListener('DOMContentLoaded', () => {
  window.pzApp = new PZViewerApp();
});
