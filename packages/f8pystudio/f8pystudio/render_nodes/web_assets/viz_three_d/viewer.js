(function () {
  const root = document.getElementById('gl-root');
  const fitBtn = document.getElementById('fit-btn');
  const liveToggle = document.getElementById('live-toggle');
  const fpsCapInput = document.getElementById('fps-cap');
  const axisSearchInput = document.getElementById('axis-search');
  const axisTreeEl = document.getElementById('axis-tree');
  const axisAllOnBtn = document.getElementById('axis-all-on');
  const axisAllOffBtn = document.getElementById('axis-all-off');
  const statusEl = document.getElementById('status');
  const toggleHudBtn = document.getElementById('toggle-hud');
  const hudElement = document.getElementById('hud');

  const state = {
    worldUp: '+y',
    liveUpdate: true,
    fpsCap: 60,
    pendingPayload: null,
    payload: null,
    lastBounds: null,
    lastPeopleSignature: '',
    lastLargeSkeletonMode: false,
    keyDown: new Set(),
    roamSpeed: 2.0,
    frameHandle: 0,
    running: true,
    lastFrameMs: 0,
    lastPayloadApplyMs: 0,
    lastTickS: performance.now() / 1000.0,
    axisSearchText: '',
    axisVisibilityByKey: new Map(),
    modelAxisVisibilityByName: new Map(),
    personDisplayNameByName: new Map(),
    personDisplaySignature: '',
    personLabelByName: new Map(),
    nodeLabelByKey: new Map(),
    personRenderCacheByName: new Map(),
    axisTreeSignature: '',
    axisTreeExpandedModels: new Set(),
    axisTreeDisplayedNodeKeys: [],
    axisTreeDisplayedModelNames: [],
  };

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0f0f12);

  const camera = new THREE.PerspectiveCamera(55, 1, 0.01, 100000.0);
  camera.position.set(3.5, 2.0, 4.2);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setClearColor(0x0f0f12, 1.0);
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setSize(300, 200);
  root.appendChild(renderer.domElement);

  const labelRenderer = new THREE.CSS2DRenderer();
  labelRenderer.setSize(300, 200);
  labelRenderer.domElement.style.position = 'absolute';
  labelRenderer.domElement.style.left = '0';
  labelRenderer.domElement.style.top = '0';
  labelRenderer.domElement.style.pointerEvents = 'none';
  root.appendChild(labelRenderer.domElement);

  function createOrbitControls(worldUpToken) {
    const c = new THREE.OrbitControls(camera, renderer.domElement);
    c.enableDamping = true;
    c.dampingFactor = 0.06;
    const upTok = normalizedWorldUpToken(worldUpToken);
    c.screenSpacePanning = !(upTok === '+y' || upTok === '-y');
    c.minPolarAngle = 0.02;
    c.maxPolarAngle = Math.PI - 0.02;
    c.target.set(0, 1, 0);
    return c;
  }

  let controls = createOrbitControls(state.worldUp);

  const ambient = new THREE.AmbientLight(0xffffff, 0.65);
  scene.add(ambient);
  const dir = new THREE.DirectionalLight(0xffffff, 0.7);
  dir.position.set(3.0, 5.0, 2.0);
  scene.add(dir);

  const worldAxes = new THREE.AxesHelper(0.8);
  scene.add(worldAxes);

  const peopleRoot = new THREE.Group();
  scene.add(peopleRoot);
  const labelsRoot = new THREE.Group();
  scene.add(labelsRoot);

  const tmpVecA = new THREE.Vector3();
  const tmpVecB = new THREE.Vector3();

  function updateStatus(text) {
    if (!statusEl) return;
    statusEl.textContent = String(text || '');
  }

  function normalizedWorldUpToken(up) {
    const n = String(up || '').toLowerCase();
    if (n === 'x' || n === '+x') return '+x';
    if (n === '-x') return '-x';
    if (n === 'y' || n === '+y') return '+y';
    if (n === '-y') return '-y';
    if (n === 'z' || n === '+z') return '+z';
    if (n === '-z') return '-z';
    return '+y';
  }

  function upVectorForToken(worldUp) {
    switch (normalizedWorldUpToken(worldUp)) {
      case '+x':
        return new THREE.Vector3(1, 0, 0);
      case '-x':
        return new THREE.Vector3(-1, 0, 0);
      case '-y':
        return new THREE.Vector3(0, -1, 0);
      case '+z':
        return new THREE.Vector3(0, 0, 1);
      case '-z':
        return new THREE.Vector3(0, 0, -1);
      case '+y':
      default:
        return new THREE.Vector3(0, 1, 0);
    }
  }

  function upVectorForWorld() {
    return upVectorForToken(state.worldUp);
  }

  function perpendicularBasisForUp(up) {
    const basis = Math.abs(up.x) < 0.8
      ? new THREE.Vector3(1, 0, 0)
      : new THREE.Vector3(0, 1, 0);
    basis.addScaledVector(up, -basis.dot(up));
    if (basis.lengthSq() < 1e-8) {
      basis.set(0, 0, 1);
      basis.addScaledVector(up, -basis.dot(up));
    }
    return basis.normalize();
  }

  function stabilizeOrbitOffset(offset, up) {
    const radius = Math.max(0.001, offset.length());
    const direction = offset.clone().normalize();
    const alignment = direction.dot(up);
    if (Math.abs(alignment) < 0.985) {
      return direction.multiplyScalar(radius);
    }

    const side = perpendicularBasisForUp(up);
    const lifted = side.multiplyScalar(0.92).addScaledVector(up, alignment >= 0 ? 0.38 : -0.38).normalize();
    return lifted.multiplyScalar(radius);
  }

  function reorientCameraForWorldUp(previousUp, nextUp) {
    const target = controls.target.clone();
    const offset = camera.position.clone().sub(target);
    let nextOffset = offset;

    if (nextOffset.lengthSq() < 1e-8) {
      const fallback = perpendicularBasisForUp(nextUp).multiplyScalar(4.0);
      fallback.addScaledVector(nextUp, 2.2);
      nextOffset = fallback;
    } else {
      const quat = new THREE.Quaternion().setFromUnitVectors(
        previousUp.clone().normalize(),
        nextUp.clone().normalize()
      );
      nextOffset = nextOffset.applyQuaternion(quat);
    }

    nextOffset = stabilizeOrbitOffset(nextOffset, nextUp);
    camera.position.copy(target).add(nextOffset);
    camera.up.copy(nextUp);
    camera.lookAt(target);
  }

  function setWorldUp(up) {
    const previousUp = upVectorForWorld();
    const nextWorldUp = normalizedWorldUpToken(up);
    if (state.worldUp !== nextWorldUp) {
      state.worldUp = nextWorldUp;
      reorientCameraForWorldUp(previousUp, upVectorForWorld());

      const target = controls.target.clone();
      if (controls && typeof controls.dispose === 'function') {
        controls.dispose();
      }
      controls = createOrbitControls(state.worldUp);
      controls.target.copy(target);
    } else {
      camera.up.copy(upVectorForWorld());
    }
    controls.update();
  }

  function coerceVec3(v) {
    if (!Array.isArray(v) || v.length < 3) return null;
    const x = Number(v[0]);
    const y = Number(v[1]);
    const z = Number(v[2]);
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return null;
    return new THREE.Vector3(x, y, z);
  }

  function coerceQuat(v) {
    if (!Array.isArray(v) || v.length < 4) return null;
    const w = Number(v[0]);
    const x = Number(v[1]);
    const y = Number(v[2]);
    const z = Number(v[3]);
    if (!Number.isFinite(w) || !Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return null;
    return new THREE.Quaternion(x, y, z, w);
  }

  function coerceEdgeList(v) {
    if (!Array.isArray(v)) return null;
    const out = [];
    for (const item of v) {
      if (!Array.isArray(item) || item.length < 2) continue;
      const i = Number(item[0]);
      const j = Number(item[1]);
      if (!Number.isFinite(i) || !Number.isFinite(j)) continue;
      out.push([Math.trunc(i), Math.trunc(j)]);
    }
    return out.length > 0 ? out : null;
  }

  function hashColorFromName(name) {
    const s = String(name || '');
    let h = 0;
    for (let i = 0; i < s.length; i += 1) {
      h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    }
    const hue = Math.abs(h % 360);
    const c = new THREE.Color();
    c.setHSL(hue / 360.0, 0.78, 0.58);
    return c;
  }

  function disposeObject3D(obj) {
    obj.traverse(function (child) {
      if (child.geometry && typeof child.geometry.dispose === 'function') {
        child.geometry.dispose();
      }
      if (child.material) {
        if (Array.isArray(child.material)) {
          for (const m of child.material) {
            if (m && typeof m.dispose === 'function') m.dispose();
          }
        } else if (typeof child.material.dispose === 'function') {
          child.material.dispose();
        }
      }
    });
  }

  function createLabel(text) {
    const el = document.createElement('div');
    el.className = 'label2d';
    el.textContent = String(text || '');
    return new THREE.CSS2DObject(el);
  }

  function setLabelText(lbl, text) {
    if (!lbl) return;
    if (!(lbl.element instanceof Element)) return;
    lbl.element.textContent = String(text || '');
  }

  function commonPrefix(names) {
    if (!Array.isArray(names) || names.length <= 0) return '';
    let prefix = String(names[0] || '');
    for (let i = 1; i < names.length; i += 1) {
      const s = String(names[i] || '');
      let j = 0;
      const maxJ = Math.min(prefix.length, s.length);
      while (j < maxJ && prefix.charCodeAt(j) === s.charCodeAt(j)) j += 1;
      prefix = prefix.slice(0, j);
      if (!prefix) break;
    }
    return prefix;
  }

  function commonSuffix(names) {
    if (!Array.isArray(names) || names.length <= 0) return '';
    let suffix = String(names[0] || '');
    for (let i = 1; i < names.length; i += 1) {
      const s = String(names[i] || '');
      let j = 0;
      const maxJ = Math.min(suffix.length, s.length);
      while (
        j < maxJ &&
        suffix.charCodeAt(suffix.length - 1 - j) === s.charCodeAt(s.length - 1 - j)
      ) {
        j += 1;
      }
      suffix = suffix.slice(suffix.length - j);
      if (!suffix) break;
    }
    return suffix;
  }

  function normalizeTrimResult(text) {
    const s = String(text || '');
    const trimmedStart = s.replace(/^[\s\-_:|/\\]+/, '');
    return trimmedStart.replace(/[\s\-_:|/\\]+$/, '');
  }

  function computeDisplayNameMap(names) {
    const out = new Map();
    const input = Array.isArray(names) ? names.map((n) => String(n || '')) : [];
    if (input.length <= 1) {
      if (input.length === 1) out.set(input[0], input[0]);
      return out;
    }

    const MIN_AFFIX = 8;
    let prefix = commonPrefix(input);
    let suffix = commonSuffix(input);
    if (prefix.length < MIN_AFFIX) prefix = '';
    if (suffix.length < MIN_AFFIX) suffix = '';

    for (const fullName of input) {
      let next = fullName;
      if (prefix && next.startsWith(prefix)) next = next.slice(prefix.length);
      if (suffix && next.endsWith(suffix)) next = next.slice(0, Math.max(0, next.length - suffix.length));
      next = normalizeTrimResult(next);
      if (!next || next.length < 2) next = fullName;
      out.set(fullName, next);
    }

    const countByDisplay = new Map();
    for (const display of out.values()) {
      countByDisplay.set(display, (countByDisplay.get(display) || 0) + 1);
    }
    for (const [fullName, display] of out.entries()) {
      if ((countByDisplay.get(display) || 0) > 1) {
        out.set(fullName, fullName);
      }
    }

    return out;
  }

  function ensurePersonDisplayNames(payload) {
    if (!payload || !Array.isArray(payload.people)) {
      state.personDisplayNameByName.clear();
      state.personDisplaySignature = '';
      return;
    }
    const people = payload.people;
    const names = [];
    for (const person of people) {
      names.push(String(person && person.name ? person.name : 'Person'));
    }
    names.sort();
    const signature = names.join('|');
    if (state.personDisplaySignature === signature) {
      return;
    }
    state.personDisplaySignature = signature;
    state.personDisplayNameByName = computeDisplayNameMap(names);
  }

  function displayNameForPerson(personName) {
    const key = String(personName || '');
    return state.personDisplayNameByName.get(key) || key;
  }

  function mergeBounds(bounds, p) {
    if (!bounds) {
      return {
        minX: p.x,
        minY: p.y,
        minZ: p.z,
        maxX: p.x,
        maxY: p.y,
        maxZ: p.z,
      };
    }
    if (p.x < bounds.minX) bounds.minX = p.x;
    if (p.y < bounds.minY) bounds.minY = p.y;
    if (p.z < bounds.minZ) bounds.minZ = p.z;
    if (p.x > bounds.maxX) bounds.maxX = p.x;
    if (p.y > bounds.maxY) bounds.maxY = p.y;
    if (p.z > bounds.maxZ) bounds.maxZ = p.z;
    return bounds;
  }

  function clearGeometryRoot() {
    while (peopleRoot.children.length > 0) {
      const child = peopleRoot.children[0];
      if (!child) break;
      peopleRoot.remove(child);
      disposeObject3D(child);
    }
    state.personRenderCacheByName.clear();
    state.lastBounds = null;
  }

  function removeLabelObject(lbl) {
    if (!lbl) return;
    try {
      if (lbl.element instanceof Element && lbl.element.parentNode) {
        lbl.element.parentNode.removeChild(lbl.element);
      }
    } catch (_err) {}
    if (lbl.parent) {
      lbl.parent.remove(lbl);
    }
  }

  function clearAllLabels() {
    for (const lbl of state.personLabelByName.values()) {
      removeLabelObject(lbl);
    }
    state.personLabelByName.clear();
    for (const lbl of state.nodeLabelByKey.values()) {
      removeLabelObject(lbl);
    }
    state.nodeLabelByKey.clear();
  }

  function axisKey(personName, nodeName) {
    return String(personName || '') + '::' + String(nodeName || '');
  }

  function isAxisEnabled(personName, nodeName) {
    const key = axisKey(personName, nodeName);
    if (!state.axisVisibilityByKey.has(key)) return true;
    return !!state.axisVisibilityByKey.get(key);
  }

  function setAxisEnabled(personName, nodeName, enabled) {
    state.axisVisibilityByKey.set(axisKey(personName, nodeName), !!enabled);
  }

  function isModelAxisEnabled(personName) {
    const key = String(personName || '');
    if (!state.modelAxisVisibilityByName.has(key)) return true;
    return !!state.modelAxisVisibilityByName.get(key);
  }

  function setModelAxisEnabled(personName, enabled) {
    state.modelAxisVisibilityByName.set(String(personName || ''), !!enabled);
  }

  function setAllAxesEnabled(enabled) {
    const value = !!enabled;
    for (const key of state.axisVisibilityByKey.keys()) {
      state.axisVisibilityByKey.set(key, value);
    }
    for (const key of state.modelAxisVisibilityByName.keys()) {
      state.modelAxisVisibilityByName.set(key, value);
    }
  }

  function buildAxisTreeSignature(payload) {
    if (!payload || !Array.isArray(payload.people)) return '';
    const chunks = [];
    for (const person of payload.people) {
      const personName = String(person && person.name ? person.name : 'Person');
      const nodes = Array.isArray(person.nodes) ? person.nodes : [];
      const nodeNames = [];
      for (const node of nodes) {
        nodeNames.push(String(node && node.name ? node.name : 'node'));
      }
      nodeNames.sort();
      chunks.push(personName + '::' + nodeNames.join(','));
    }
    chunks.sort();
    return chunks.join('|');
  }

  function rebuildAxisTree(payload, force) {
    if (!axisTreeEl) return;
    state.axisTreeDisplayedNodeKeys = [];
    state.axisTreeDisplayedModelNames = [];
    if (!payload || !Array.isArray(payload.people)) {
      axisTreeEl.innerHTML = '';
      state.axisTreeSignature = '';
      return;
    }
    ensurePersonDisplayNames(payload);
    const performanceHints = readPerformanceHints(payload);
    const stableMode = !!performanceHints.largeSkeletonMode;
    if (performanceHints.suppressAxisTree) {
      const suppressedSignature = '__suppressed__:' + String(performanceHints.totalNodes);
      if (!force && state.axisTreeSignature === suppressedSignature) {
        return;
      }
      state.axisTreeSignature = suppressedSignature;
      axisTreeEl.innerHTML = '';
      const row = document.createElement('div');
      row.className = 'axis-item';
      row.textContent = 'Axis tree hidden in stable mode (' + String(performanceHints.totalNodes) + ' nodes)';
      axisTreeEl.appendChild(row);
      return;
    }
    const nextSignature = buildAxisTreeSignature(payload);
    if (!force && state.axisTreeSignature === nextSignature) {
      return;
    }
    state.axisTreeSignature = nextSignature;
    axisTreeEl.innerHTML = '';

    const searchText = String(state.axisSearchText || '').trim().toLowerCase();
    const nextNodeKeys = new Set();
    const nextModelKeys = new Set();

    if (stableMode) {
      const info = document.createElement('div');
      info.className = 'axis-item';
      info.style.opacity = '0.85';
      info.textContent = 'Stable mode: bones default OFF; enable bones to render points/axis/name.';
      axisTreeEl.appendChild(info);
    }

    for (const person of payload.people) {
      const personName = String(person && person.name ? person.name : 'Person');
      const personDisplayName = displayNameForPerson(personName);
      const personNameLower = personName.toLowerCase();
      const personDisplayNameLower = personDisplayName.toLowerCase();
      const personMatch = !searchText || personNameLower.includes(searchText) || personDisplayNameLower.includes(searchText);
      const nodes = Array.isArray(person.nodes) ? person.nodes : [];
      nextModelKeys.add(personName);
      state.axisTreeDisplayedModelNames.push(personName);
      if (!state.modelAxisVisibilityByName.has(personName)) {
        state.modelAxisVisibilityByName.set(personName, true);
      }

      let totalBones = 0;
      let selectedBones = 0;
      for (const node of nodes) {
        const nodeName = String(node && node.name ? node.name : 'node');
        const key = axisKey(personName, nodeName);
        nextNodeKeys.add(key);
        totalBones += 1;
        if (!state.axisVisibilityByKey.has(key)) {
          state.axisVisibilityByKey.set(key, stableMode ? false : true);
        }
        if (state.axisVisibilityByKey.get(key)) {
          selectedBones += 1;
        }
      }

      if (totalBones <= 0) continue;

      const details = document.createElement('details');
      details.className = 'axis-model';
      const openByUser = state.axisTreeExpandedModels.has(personName);
      details.open = (!stableMode) || openByUser || !!searchText;
      details.addEventListener('toggle', function () {
        if (details.open) {
          state.axisTreeExpandedModels.add(personName);
        } else {
          state.axisTreeExpandedModels.delete(personName);
        }
      });

      const modelRow = document.createElement('summary');
      modelRow.className = 'axis-item';
      const modelCk = document.createElement('input');
      modelCk.type = 'checkbox';
      modelCk.checked = isModelAxisEnabled(personName);
      modelCk.addEventListener('click', function (event) {
        event.stopPropagation();
      });
      modelCk.addEventListener('change', function () {
        setModelAxisEnabled(personName, modelCk.checked);
        if (state.payload) {
          rebuildAxisTree(state.payload, true);
          applyPayload(state.payload);
        }
      });
      const modelText = document.createElement('span');
      modelText.textContent = personDisplayName + ' (' + String(selectedBones) + '/' + String(totalBones) + ')';
      modelRow.appendChild(modelCk);
      modelRow.appendChild(modelText);
      details.appendChild(modelRow);

      const nodeContainer = document.createElement('div');
      nodeContainer.className = 'bone-list';
      details.appendChild(nodeContainer);

      const shouldRenderNodes = details.open || !!searchText;
      if (shouldRenderNodes) {
        const maxRows = stableMode && !searchText ? 400 : 2000;
        let rowCount = 0;
        for (const node of nodes) {
          const nodeName = String(node && node.name ? node.name : 'node');
          const nodeNameLower = nodeName.toLowerCase();
          if (searchText && !((personMatch || personDisplayNameLower.includes(searchText)) || nodeNameLower.includes(searchText))) {
            continue;
          }
          if (rowCount >= maxRows) {
            const truncated = document.createElement('div');
            truncated.className = 'axis-item';
            truncated.style.opacity = '0.75';
            truncated.textContent = '… more bones';
            nodeContainer.appendChild(truncated);
            break;
          }

          const key = axisKey(personName, nodeName);
          state.axisTreeDisplayedNodeKeys.push(key);

          const row = document.createElement('label');
          row.className = 'axis-item';

          const checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.checked = !!state.axisVisibilityByKey.get(key);
          checkbox.disabled = !isModelAxisEnabled(personName);
          checkbox.addEventListener('change', function () {
            setAxisEnabled(personName, nodeName, checkbox.checked);
            if (state.payload) applyPayload(state.payload);
          });

          const text = document.createElement('span');
          text.textContent = nodeName;
          row.style.opacity = checkbox.disabled ? '0.6' : '1.0';
          row.appendChild(checkbox);
          row.appendChild(text);
          nodeContainer.appendChild(row);
          rowCount += 1;
        }
      }

      axisTreeEl.appendChild(details);

    }

    for (const key of Array.from(state.axisVisibilityByKey.keys())) {
      if (!nextNodeKeys.has(key)) state.axisVisibilityByKey.delete(key);
    }
    for (const key of Array.from(state.modelAxisVisibilityByName.keys())) {
      if (!nextModelKeys.has(key)) state.modelAxisVisibilityByName.delete(key);
    }
  }

  function ensurePersonLabel(personName, displayName) {
    const key = String(personName || '');
    let lbl = state.personLabelByName.get(key);
    if (!lbl) {
      lbl = createLabel(displayName);
      labelsRoot.add(lbl);
      state.personLabelByName.set(key, lbl);
    }
    setLabelText(lbl, displayName);
    return lbl;
  }

  function ensureNodeLabel(personName, nodeName) {
    const key = axisKey(personName, nodeName);
    let lbl = state.nodeLabelByKey.get(key);
    if (!lbl) {
      lbl = createLabel(nodeName);
      labelsRoot.add(lbl);
      state.nodeLabelByKey.set(key, lbl);
    }
    return lbl;
  }

  function cleanupStaleLabels(activePersonNames, activeNodeKeys) {
    for (const [name, lbl] of Array.from(state.personLabelByName.entries())) {
      if (activePersonNames.has(name)) continue;
      removeLabelObject(lbl);
      state.personLabelByName.delete(name);
    }
    for (const [key, lbl] of Array.from(state.nodeLabelByKey.entries())) {
      if (activeNodeKeys.has(key)) continue;
      removeLabelObject(lbl);
      state.nodeLabelByKey.delete(key);
    }
  }

  function ensurePersonRenderCache(personName) {
    const key = String(personName || 'Person');
    let cache = state.personRenderCacheByName.get(key);
    if (cache) {
      return cache;
    }

    const color = hashColorFromName(key);
    const group = new THREE.Group();
    const axisRoot = new THREE.Group();
    group.add(axisRoot);
    peopleRoot.add(group);

    cache = {
      name: key,
      color: color,
      group: group,
      axisRoot: axisRoot,
      axisHelperByNodeKey: new Map(),
      pointCapacity: 0,
      lineCapacity: 0,
      points: null,
      lines: null,
      boxHelper: null,
    };
    state.personRenderCacheByName.set(key, cache);
    return cache;
  }

  function ensurePersonAxisHelper(cache, nodeKey) {
    let helper = cache.axisHelperByNodeKey.get(nodeKey);
    if (helper) {
      return helper;
    }
    helper = new THREE.AxesHelper(1.0);
    cache.axisRoot.add(helper);
    cache.axisHelperByNodeKey.set(nodeKey, helper);
    return helper;
  }

  function hideAllPersonAxisHelpers(cache) {
    for (const helper of cache.axisHelperByNodeKey.values()) {
      helper.visible = false;
    }
  }

  function cleanupStalePersonAxisHelpers(cache, activeAxisKeys) {
    for (const [nodeKey, helper] of Array.from(cache.axisHelperByNodeKey.entries())) {
      if (activeAxisKeys.has(nodeKey)) {
        continue;
      }
      helper.visible = false;
    }
  }

  function removeStalePersonRenderCaches(activePersonNames) {
    for (const [name, cache] of Array.from(state.personRenderCacheByName.entries())) {
      if (activePersonNames.has(name)) continue;
      if (cache.group.parent) {
        cache.group.parent.remove(cache.group);
      }
      disposeObject3D(cache.group);
      state.personRenderCacheByName.delete(name);
    }
  }

  function ensureDynamicPositionAttribute(geometry, requiredFloats) {
    let positionAttr = geometry.getAttribute('position');
    const required = Math.max(0, Math.floor(requiredFloats));
    if (!positionAttr || !positionAttr.array || positionAttr.array.length < required) {
      const nextCapacity = Math.max(required, positionAttr && positionAttr.array ? positionAttr.array.length * 2 : 96);
      positionAttr = new THREE.Float32BufferAttribute(new Float32Array(nextCapacity), 3);
      positionAttr.setUsage(THREE.DynamicDrawUsage);
      geometry.setAttribute('position', positionAttr);
    }
    return positionAttr;
  }

  function updateDynamicPositions(geometry, values, drawCount) {
    const positionAttr = ensureDynamicPositionAttribute(geometry, values.length);
    positionAttr.array.fill(0, 0, values.length);
    positionAttr.array.set(values, 0);
    positionAttr.needsUpdate = true;
    geometry.setDrawRange(0, Math.max(0, Math.floor(drawCount)));
  }

  function ensurePersonPoints(cache) {
    if (cache.points) {
      return cache.points;
    }
    const geometry = new THREE.BufferGeometry();
    const material = new THREE.PointsMaterial({
      size: 0.03,
      sizeAttenuation: true,
      color: cache.color,
    });
    const points = new THREE.Points(geometry, material);
    points.frustumCulled = false;
    cache.group.add(points);
    cache.points = points;
    return points;
  }

  function ensurePersonLines(cache) {
    if (cache.lines) {
      return cache.lines;
    }
    const geometry = new THREE.BufferGeometry();
    const material = new THREE.LineBasicMaterial({
      color: cache.color,
      transparent: true,
      opacity: 0.9,
    });
    const lines = new THREE.LineSegments(geometry, material);
    lines.frustumCulled = false;
    cache.group.add(lines);
    cache.lines = lines;
    return lines;
  }

  function ensurePersonBoxHelper(cache) {
    if (cache.boxHelper) {
      return cache.boxHelper;
    }
    const helper = new THREE.Box3Helper(new THREE.Box3(), cache.color);
    cache.group.add(helper);
    cache.boxHelper = helper;
    return helper;
  }

  function setPersonBox(cache, minV, maxV) {
    const helper = ensurePersonBoxHelper(cache);
    helper.box.min.copy(minV);
    helper.box.max.copy(maxV);
    helper.visible = true;
    helper.updateMatrixWorld(true);
    if (helper.material && helper.material.color) {
      helper.material.color.copy(cache.color);
    }
  }

  function hidePersonBox(cache) {
    if (cache.boxHelper) {
      cache.boxHelper.visible = false;
    }
  }

  function updatePersonGeometry(cache, person, renderFlags, activePersonNames, activeNodeKeys, boneLabelBudget, performanceHints, personDisplayName) {
    const name = cache.name;
    const stableMode = !!(performanceHints && performanceHints.largeSkeletonMode);
    const modelEnabled = isModelAxisEnabled(name);
    let bounds = null;
    let boxCenter = null;
    let boxSize = null;
    const activeAxisKeys = new Set();

    hideAllPersonAxisHelpers(cache);
    hidePersonBox(cache);

    if (!stableMode && modelEnabled && renderFlags.showPersonBoxes && Array.isArray(person.bbox) && person.bbox.length >= 6) {
      const x0 = Number(person.bbox[0]);
      const y0 = Number(person.bbox[1]);
      const z0 = Number(person.bbox[2]);
      const x1 = Number(person.bbox[3]);
      const y1 = Number(person.bbox[4]);
      const z1 = Number(person.bbox[5]);
      if (
        Number.isFinite(x0) && Number.isFinite(y0) && Number.isFinite(z0) &&
        Number.isFinite(x1) && Number.isFinite(y1) && Number.isFinite(z1)
      ) {
        const minV = new THREE.Vector3(Math.min(x0, x1), Math.min(y0, y1), Math.min(z0, z1));
        const maxV = new THREE.Vector3(Math.max(x0, x1), Math.max(y0, y1), Math.max(z0, z1));
        setPersonBox(cache, minV, maxV);

        bounds = mergeBounds(bounds, minV);
        bounds = mergeBounds(bounds, maxV);
        boxCenter = minV.clone().add(maxV).multiplyScalar(0.5);
        boxSize = maxV.clone().sub(minV);
      }
    }

    const nodes = Array.isArray(person.nodes) ? person.nodes : [];
    const markerScaleRaw = Number(renderFlags.markerScale);
    const markerScale = Number.isFinite(markerScaleRaw) ? markerScaleRaw : 1.0;
    const pointPositions = [];
    const posByIndex = new Map();
    const showSelectedBoneAxes = !!renderFlags.showBoneAxes || stableMode;
    const showSelectedBoneNames = !!renderFlags.showBoneNames || stableMode;

    for (const node of nodes) {
      const nodeName = String(node && node.name ? node.name : 'node');
      const boneVisible = modelEnabled && isAxisEnabled(name, nodeName);
      if (stableMode && !boneVisible) {
        continue;
      }
      const pos = coerceVec3(node && node.pos);
      if (!pos) continue;

      if (renderFlags.showBonePoints) {
        pointPositions.push(pos.x, pos.y, pos.z);
      }
      const nodeIndex = Number(node && node.index);
      if (Number.isFinite(nodeIndex)) {
        posByIndex.set(Math.trunc(nodeIndex), pos.clone());
      }
      bounds = mergeBounds(bounds, pos);

      if (showSelectedBoneAxes && boneVisible) {
        const nodeKey = axisKey(name, nodeName);
        const axes = ensurePersonAxisHelper(cache, nodeKey);
        axes.position.copy(pos);
        const q = coerceQuat(node && node.rot);
        if (q) {
          axes.quaternion.copy(q);
        } else {
          axes.quaternion.identity();
        }
        axes.scale.setScalar(0.08 * markerScale);
        axes.visible = true;
        activeAxisKeys.add(nodeKey);
      }

      if (showSelectedBoneNames && boneVisible && boneLabelBudget.remaining > 0) {
        const lbl = ensureNodeLabel(name, nodeName);
        lbl.position.copy(pos);
        activeNodeKeys.add(axisKey(name, nodeName));
        boneLabelBudget.remaining -= 1;
      }
    }

    if (renderFlags.showBonePoints && pointPositions.length >= 3) {
      const points = ensurePersonPoints(cache);
      points.visible = true;
      points.material.size = 0.03 * markerScale;
      points.material.color.copy(cache.color);
      updateDynamicPositions(points.geometry, pointPositions, pointPositions.length / 3);
    } else if (cache.points) {
      cache.points.visible = false;
      cache.points.geometry.setDrawRange(0, 0);
    }

    if (modelEnabled && renderFlags.showSkeletonLines) {
      const skeletonEdges = coerceEdgeList(person && person.skeletonEdges);
      if (skeletonEdges && skeletonEdges.length > 0) {
        const linePositions = [];
        for (const edge of skeletonEdges) {
          const i = edge[0];
          const j = edge[1];
          const p0 = posByIndex.get(i);
          const p1 = posByIndex.get(j);
          if (!p0 || !p1) continue;
          linePositions.push(p0.x, p0.y, p0.z, p1.x, p1.y, p1.z);
        }
        if (linePositions.length >= 6) {
          const lines = ensurePersonLines(cache);
          lines.visible = true;
          lines.material.color.copy(cache.color);
          updateDynamicPositions(lines.geometry, linePositions, linePositions.length / 3);
        } else if (cache.lines) {
          cache.lines.visible = false;
          cache.lines.geometry.setDrawRange(0, 0);
        }
      } else if (cache.lines) {
        cache.lines.visible = false;
        cache.lines.geometry.setDrawRange(0, 0);
      }
    } else if (cache.lines) {
      cache.lines.visible = false;
      cache.lines.geometry.setDrawRange(0, 0);
    }

    if (!stableMode && modelEnabled && renderFlags.showPersonNames && boxCenter && boxSize) {
      const lbl = ensurePersonLabel(name, personDisplayName);
      const up = upVectorForWorld().clone().multiplyScalar(Math.max(0.08, boxSize.length() * 0.04));
      lbl.position.copy(boxCenter.clone().add(up));
      activePersonNames.add(name);
    }

    cleanupStalePersonAxisHelpers(cache, activeAxisKeys);
    cache.group.visible = true;
    return bounds;
  }

  function payloadSignature(payload) {
    const people = Array.isArray(payload.people) ? payload.people : [];
    const names = [];
    for (const p of people) {
      names.push(String(p && p.name ? p.name : ''));
    }
    names.sort();
    return names.join('|');
  }

  function readPerformanceHints(payload) {
    const hints = payload && typeof payload === 'object' ? payload.performanceHints : null;
    const totalNodes = Number(hints && hints.totalNodes);
    const recommendedFpsCap = Number(hints && hints.recommendedFpsCap);
    const maxVisibleBoneLabelsRaw = hints ? hints.maxVisibleBoneLabels : null;
    const maxVisibleBoneLabels = maxVisibleBoneLabelsRaw === null || maxVisibleBoneLabelsRaw === undefined
      ? null
      : Number(maxVisibleBoneLabelsRaw);
    return {
      totalNodes: Number.isFinite(totalNodes) ? Math.max(0, Math.floor(totalNodes)) : 0,
      largeSkeletonMode: !!(hints && hints.largeSkeletonMode),
      suppressBoneAxes: !!(hints && hints.suppressBoneAxes),
      suppressBoneNames: !!(hints && hints.suppressBoneNames),
      suppressAxisTree: !!(hints && hints.suppressAxisTree),
      suppressPersonBoxes: !!(hints && hints.suppressPersonBoxes),
      maxVisibleBoneLabels: Number.isFinite(maxVisibleBoneLabels) ? Math.max(0, Math.floor(maxVisibleBoneLabels)) : null,
      recommendedFpsCap: Number.isFinite(recommendedFpsCap) ? Math.max(1, Math.floor(recommendedFpsCap)) : null,
    };
  }

  function mergeRenderFlags(payload) {
    const performanceHints = readPerformanceHints(payload);
    const renderFlags = Object.assign(
      {
        showPersonBoxes: true,
        showPersonNames: false,
        showBonePoints: true,
        showSkeletonLines: true,
        showBoneAxes: false,
        showBoneNames: false,
        autoZoomOnNewPeople: false,
        markerScale: 1.0,
      },
      payload && payload.renderFlags ? payload.renderFlags : {}
    );
    renderFlags.showPersonBoxes = !!renderFlags.showPersonBoxes && !performanceHints.suppressPersonBoxes;
    renderFlags.showBoneAxes = !!renderFlags.showBoneAxes && !performanceHints.suppressBoneAxes;
    renderFlags.showBoneNames = !!renderFlags.showBoneNames && !performanceHints.suppressBoneNames;
    return { performanceHints: performanceHints, renderFlags: renderFlags };
  }

  function applyPayload(payload) {
    if (!payload || typeof payload !== 'object') return;

    state.payload = payload;
    setWorldUp(payload.worldUp);

    const mergedConfig = mergeRenderFlags(payload);
    const performanceHints = mergedConfig.performanceHints;
    const renderFlags = mergedConfig.renderFlags;
    ensurePersonDisplayNames(payload);

    if (performanceHints.largeSkeletonMode && !state.lastLargeSkeletonMode) {
      for (const key of state.axisVisibilityByKey.keys()) {
        state.axisVisibilityByKey.set(key, false);
      }
    }
    state.lastLargeSkeletonMode = !!performanceHints.largeSkeletonMode;

    const boneLabelBudget = {
      remaining: performanceHints.maxVisibleBoneLabels === null ? Number.MAX_SAFE_INTEGER : performanceHints.maxVisibleBoneLabels,
    };

    const uiFpsCap = Number(payload.uiFpsCap);
    let nextFpsCap = Number.isFinite(uiFpsCap) && uiFpsCap >= 1 && uiFpsCap <= 120
      ? Math.floor(uiFpsCap)
      : state.fpsCap;
    if (Number.isFinite(performanceHints.recommendedFpsCap)) {
      nextFpsCap = Math.min(nextFpsCap, performanceHints.recommendedFpsCap);
    }
    if (Number.isFinite(nextFpsCap) && nextFpsCap >= 1 && nextFpsCap <= 120) {
      state.fpsCap = Math.floor(nextFpsCap);
      if (fpsCapInput) fpsCapInput.value = String(state.fpsCap);
    }

    rebuildAxisTree(payload, false);

    const activeLabelPersonNames = new Set();
    const activeNodeKeys = new Set();
    const activeRenderPersonNames = new Set();
    const people = Array.isArray(payload.people) ? payload.people : [];
    let mergedBounds = null;

    for (const person of people) {
      const personName = String(person && person.name ? person.name : 'Person');
      const personDisplayName = displayNameForPerson(personName);
      activeRenderPersonNames.add(personName);
      const cache = ensurePersonRenderCache(personName);
      const personBounds = updatePersonGeometry(
        cache,
        person,
        renderFlags,
        activeLabelPersonNames,
        activeNodeKeys,
        boneLabelBudget,
        performanceHints,
        personDisplayName
      );
      if (personBounds) {
        const b = personBounds;
        mergedBounds = mergeBounds(mergedBounds, new THREE.Vector3(b.minX, b.minY, b.minZ));
        mergedBounds = mergeBounds(mergedBounds, new THREE.Vector3(b.maxX, b.maxY, b.maxZ));
      }
    }

    removeStalePersonRenderCaches(activeRenderPersonNames);

    if (!renderFlags.showPersonNames) activeLabelPersonNames.clear();
    if (!renderFlags.showBoneNames && !performanceHints.largeSkeletonMode) activeNodeKeys.clear();
    cleanupStaleLabels(activeLabelPersonNames, activeNodeKeys);

    state.lastBounds = mergedBounds;
    const sig = payloadSignature(payload);
    if (renderFlags.autoZoomOnNewPeople && sig !== state.lastPeopleSignature) {
      zoomToFit();
    }
    state.lastPeopleSignature = sig;

    updateStatus(
      'people=' + people.length +
      ' nodes=' + performanceHints.totalNodes +
      ' up=' + state.worldUp +
      ' fps=' + state.fpsCap +
      (performanceHints.largeSkeletonMode ? ' stable-mode' : '')
    );
  }

  function setData(payload) {
    state.pendingPayload = payload;
  }

  function maybeApplyPendingPayload(nowMs) {
    if (!state.liveUpdate || !state.pendingPayload) return;
    const cap = Math.max(1, Math.min(120, Number(state.fpsCap) || 60));
    const applyIntervalMs = 1000.0 / cap;
    if (state.lastPayloadApplyMs > 0 && nowMs - state.lastPayloadApplyMs < applyIntervalMs) {
      return;
    }
    const payload = state.pendingPayload;
    state.pendingPayload = null;
    state.lastPayloadApplyMs = nowMs;
    applyPayload(payload);
  }

  function zoomToFit() {
    const b = state.lastBounds;
    if (!b) return;

    const center = new THREE.Vector3(
      (b.minX + b.maxX) * 0.5,
      (b.minY + b.maxY) * 0.5,
      (b.minZ + b.maxZ) * 0.5
    );

    const size = new THREE.Vector3(
      Math.max(0.001, b.maxX - b.minX),
      Math.max(0.001, b.maxY - b.minY),
      Math.max(0.001, b.maxZ - b.minZ)
    );

    const radius = Math.max(size.x, size.y, size.z);
    const fovRad = (camera.fov * Math.PI) / 180.0;
    const dist = Math.max(0.6, (radius * 1.2) / Math.max(0.1, Math.tan(fovRad * 0.5)));

    const up = upVectorForWorld();
    const forward = tmpVecA.copy(camera.position).sub(controls.target);
    if (forward.lengthSq() < 1e-8) {
      forward.copy(perpendicularBasisForUp(up)).addScaledVector(up, 0.45);
    }
    forward.normalize();
    const offset = stabilizeOrbitOffset(forward.multiplyScalar(dist), up);
    camera.position.copy(center).add(offset);
    camera.up.copy(up);
    camera.lookAt(center);
    controls.target.copy(center);
    controls.update();
  }

  function updateRoam(deltaS) {
    const moveForward = state.keyDown.has('KeyW') ? 1 : 0;
    const moveBack = state.keyDown.has('KeyS') ? 1 : 0;
    const moveLeft = state.keyDown.has('KeyA') ? 1 : 0;
    const moveRight = state.keyDown.has('KeyD') ? 1 : 0;
    const speedMul = state.keyDown.has('ShiftLeft') || state.keyDown.has('ShiftRight') ? 2.5 : 1.0;

    if (moveForward + moveBack + moveLeft + moveRight <= 0) return;

    const up = upVectorForWorld();
    const forward = tmpVecA;
    camera.getWorldDirection(forward);
    forward.addScaledVector(up, -forward.dot(up));
    if (forward.lengthSq() < 1e-8) return;
    forward.normalize();

    const right = tmpVecB.copy(forward).cross(up).normalize();
    const move = new THREE.Vector3();
    if (moveForward) move.add(forward);
    if (moveBack) move.sub(forward);
    if (moveRight) move.add(right);
    if (moveLeft) move.sub(right);
    if (move.lengthSq() < 1e-8) return;

      move.normalize().multiplyScalar(state.roamSpeed * speedMul * Math.max(0.0, deltaS));
    camera.position.add(move);
    controls.target.add(move);
  }

  function onResize() {
    const rect = root.getBoundingClientRect();
    const w = Math.max(1, Math.floor(rect.width));
    const h = Math.max(1, Math.floor(rect.height));
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    labelRenderer.setSize(w, h);
  }

  function setRunning(run) {
    state.running = !!run;
    if (state.running) {
      if (!state.frameHandle) {
        state.lastFrameMs = performance.now();
        state.frameHandle = requestAnimationFrame(tick);
      }
    } else {
      if (state.frameHandle) {
        cancelAnimationFrame(state.frameHandle);
        state.frameHandle = 0;
      }
    }
  }

  function onKeyDown(ev) {
    if (!ev || !ev.code) return;
    state.keyDown.add(ev.code);
    if (ev.code === 'KeyF') {
      zoomToFit();
      ev.preventDefault();
    }
    if (ev.code === 'KeyH') {
      toggleHud();
      ev.preventDefault();
    }
  }

  function onKeyUp(ev) {
    if (!ev || !ev.code) return;
    state.keyDown.delete(ev.code);
  }

  function toggleHud() {
    if (!hudElement) return;
    hudElement.classList.toggle('collapsed');
  }

  function tick(nowMs) {
    if (!state.running) return;
    state.frameHandle = requestAnimationFrame(tick);

    const nowS = nowMs / 1000.0;
    const deltaS = Math.max(0.0, Math.min(0.2, nowS - state.lastTickS));
    state.lastTickS = nowS;

    maybeApplyPendingPayload(nowMs);
    updateRoam(deltaS);
    controls.update();

    const cap = Math.max(1, Math.min(120, Number(state.fpsCap) || 60));
    const frameInterval = 1000.0 / cap;
    if (nowMs - state.lastFrameMs < frameInterval) return;
    state.lastFrameMs = nowMs;

    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  }

  function detach() {
    state.running = false;
    if (state.frameHandle) {
      cancelAnimationFrame(state.frameHandle);
      state.frameHandle = 0;
    }
    window.removeEventListener('keydown', onKeyDown);
    window.removeEventListener('keyup', onKeyUp);
    if (resizeObserver) resizeObserver.disconnect();

    clearGeometryRoot();
    clearAllLabels();
    state.axisVisibilityByKey.clear();
    state.modelAxisVisibilityByName.clear();
    state.axisTreeSignature = '';
    state.pendingPayload = null;
    if (axisTreeEl) axisTreeEl.innerHTML = '';
  }

  if (fitBtn) {
    fitBtn.addEventListener('click', function () {
      zoomToFit();
    });
  }

  if (liveToggle) {
    liveToggle.checked = true;
    liveToggle.addEventListener('change', function () {
      state.liveUpdate = !!liveToggle.checked;
      if (state.liveUpdate) state.lastPayloadApplyMs = 0;
    });
  }

  if (fpsCapInput) {
    fpsCapInput.value = String(state.fpsCap);
    fpsCapInput.addEventListener('change', function () {
      const n = Number(fpsCapInput.value);
      if (!Number.isFinite(n)) {
        fpsCapInput.value = String(state.fpsCap);
        return;
      }
      const clamped = Math.max(1, Math.min(120, Math.floor(n)));
      state.fpsCap = clamped;
      fpsCapInput.value = String(clamped);
    });
  }

  if (axisSearchInput) {
    axisSearchInput.addEventListener('input', function () {
      state.axisSearchText = String(axisSearchInput.value || '');
      if (state.payload) rebuildAxisTree(state.payload, true);
    });
  }

  if (axisAllOnBtn) {
    axisAllOnBtn.addEventListener('click', function () {
      if (!state.payload) return;
      const performanceHints = readPerformanceHints(state.payload);
      if (performanceHints.largeSkeletonMode) {
        for (const personName of state.axisTreeDisplayedModelNames) {
          setModelAxisEnabled(personName, true);
        }
        for (const key of state.axisTreeDisplayedNodeKeys) {
          state.axisVisibilityByKey.set(key, true);
        }
      } else {
        setAllAxesEnabled(true);
      }
      rebuildAxisTree(state.payload, true);
      applyPayload(state.payload);
    });
  }

  if (toggleHudBtn) {
    toggleHudBtn.addEventListener('click', function () {
      toggleHud();
    });
  }

  if (axisAllOffBtn) {
    axisAllOffBtn.addEventListener('click', function () {
      if (!state.payload) return;
      const performanceHints = readPerformanceHints(state.payload);
      if (performanceHints.largeSkeletonMode) {
        for (const key of state.axisVisibilityByKey.keys()) {
          state.axisVisibilityByKey.set(key, false);
        }
        for (const personName of state.modelAxisVisibilityByName.keys()) {
          state.modelAxisVisibilityByName.set(personName, true);
        }
      } else {
        setAllAxesEnabled(false);
      }
      rebuildAxisTree(state.payload, true);
      applyPayload(state.payload);
    });
  }

  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);

  const resizeObserver = new ResizeObserver(function () {
    onResize();
  });
  resizeObserver.observe(root);
  onResize();
  requestAnimationFrame(tick);

  window.Skeleton3DViewer = {
    setData: setData,
    setWorldUp: setWorldUp,
    zoomToFit: zoomToFit,
    detach: detach,
    setRunning: setRunning,
  };
})();
