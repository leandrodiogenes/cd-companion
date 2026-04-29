
(function () {
  if (window.__cdOverlay) return;
  window.__cdOverlay = true;

  const WS_URL = '$WS_URL';
  const RECONNECT_MS = 3000;
  const CENTER_TELEPORT_Y_KEY = 'cd_center_teleport_y';

  const LANG_URL_BASE = 'http://127.0.0.1:8765';

  let currentLang = 'en';
  let LANG = {};

  let ws              = null;
  let marker          = null;
  let map             = null;
  let following       = true;
  let shiftHeld       = false;
  let lastPos         = null;
  let lastHeading     = 0;
  let lastCameraHeading = 0;
  let hasCameraHeading = false;
  let rotateWithPlayer = !!(window.__cdSettings && window.__cdSettings.rotateWithPlayer);
  let rotateWithCamera = !!(window.__cdSettings && window.__cdSettings.rotateWithCamera);
  let waypoints       = [];
  let waypointPopup   = null;
  let waypointFilter  = '';
  let calibrationMode = false;
  let hasPreTeleport  = false;

  document.addEventListener('keydown', (e) => { if (e.key === 'Shift') shiftHeld = true;  });
  document.addEventListener('keyup',   (e) => { if (e.key === 'Shift') shiftHeld = false; });

  async function loadLanguage(lang) {
    try {
      const res = await fetch(`${LANG_URL_BASE}/${lang}.json`);
      LANG = await res.json();
      currentLang = lang;

      if (!window.__cdSettings) window.__cdSettings = {};
      window.__cdSettings.language = lang;

      localStorage.setItem('cd_language', lang);

      console.log('[LANG] Loaded:', lang);
      refreshLocalizedText();
    } catch (err) {
      console.error('[LANG] Failed to load:', lang, err);
    }
  }

  function t(key) {
    return LANG[key] || key;
  }

  function tf(key, vars) {
    let text = String(t(key));
    Object.keys(vars || {}).forEach(k => {
      text = text.split(`{${k}}`).join(String(vars[k]));
    });
    return text;
  }

  function refreshLocalizedText() {
    const langLabel = document.getElementById('cdLangLabel');
    if (langLabel) langLabel.textContent = t('languageLabel');

    const langSelect = document.getElementById('cdLangSelect');
    if (langSelect && langSelect.value !== currentLang) {
      langSelect.value = currentLang;
    }
    const expand = document.getElementById('cdOvExpandBtn');
    if (expand) expand.title = t('expandPanelTitle');
    const marker = document.getElementById('cdOvMarker');
    if (marker) {
      marker.title = t('teleportMarkerTitle');
      marker.textContent = t('teleportMarkerButton');
    }
    const abort = document.getElementById('cdOvAbort');
    if (abort) {
      abort.title = t('abortTitle');
      abort.textContent = t('abortButton');
    }
    const calib = document.getElementById('cdOvCalibrate');
    if (calib) calib.title = t('calibrationButtonTitle');
    const wpToggle = document.getElementById('cdWpToggle');
    if (wpToggle) wpToggle.title = t('waypointsToggleTitle');
    const centerTp = document.getElementById('cdCenterTp');
    if (centerTp) centerTp.title = t('centerTeleportOpenTitle');
    const centerPanelTitle = document.getElementById('cdCenterPanelTitle');
    if (centerPanelTitle) centerPanelTitle.textContent = t('centerScreenTitle');
    const centerPanelTp = document.getElementById('cdCenterPanelTp');
    if (centerPanelTp) {
      centerPanelTp.title = t('centerTeleportButtonTitle');
      centerPanelTp.textContent = t('teleportButton');
    }
    const wpPanelTitle = document.getElementById('cdWpPanelTitle');
    if (wpPanelTitle) wpPanelTitle.textContent = t('waypointsPanelTitle');
    const wpSave = document.getElementById('cdWpSave');
    if (wpSave) {
      wpSave.title = t('saveCurrentPositionTitle');
      wpSave.textContent = t('saveButton');
    }
    const wpFilter = document.getElementById('cdWpFilter');
    if (wpFilter) wpFilter.placeholder = t('filterWaypointsPlaceholder');
    const popupDoc = getWaypointPopupDoc && getWaypointPopupDoc();
    if (popupDoc) {
      const popupTitle = popupDoc.getElementById('cdWpPopupTitle');
      if (popupTitle) popupTitle.textContent = t('waypointsTitle');
      const popupSave = popupDoc.getElementById('cdWpPopupSave');
      if (popupSave) popupSave.textContent = t('saveButton');
      const popupFilter = popupDoc.getElementById('cdWpPopupFilter');
      if (popupFilter) popupFilter.placeholder = t('filterWaypointsPlaceholder');
      if (popupDoc.title !== undefined) popupDoc.title = t('waypointsWindowTitle');
    }

    updatePanel();
    renderWaypoints();
  }


  // ── Tooltip de localização (hover sobre ícones do mapa) ──────────────
  let _tooltip = null;
  function ensureTooltip() {
    if (_tooltip) return _tooltip;
    _tooltip = document.createElement('div');
    _tooltip.id = 'cdLocTooltip';
    _tooltip.style.cssText = `
      position:fixed;z-index:20000;pointer-events:none;
      background:rgba(12,12,18,.92);color:#e8e8e8;
      font:12px/1.4 'Segoe UI',system-ui,sans-serif;
      border:1px solid rgba(255,208,96,.35);border-radius:6px;
      padding:5px 10px;max-width:220px;white-space:normal;
      box-shadow:0 3px 12px rgba(0,0,0,.5);backdrop-filter:blur(4px);
      display:none;
    `;
    document.body.appendChild(_tooltip);
    return _tooltip;
  }

  function initLocationTooltip(m) {
    const tip = ensureTooltip();
    m.on('mousemove', (e) => {
      const features = m.queryRenderedFeatures(e.point);
      const f = features.find(ft =>
        ft.properties && (ft.properties.title || ft.properties.name));
      if (f) {
        const label = f.properties.title || f.properties.name;
        tip.textContent = label;
        tip.style.display = 'block';
        tip.style.left = (e.originalEvent.clientX + 14) + 'px';
        tip.style.top  = (e.originalEvent.clientY - 8)  + 'px';
        m.getCanvas().style.cursor = 'pointer';
      } else {
        tip.style.display = 'none';
        m.getCanvas().style.cursor = '';
      }
    });
    m.on('mouseout', () => {
      tip.style.display = 'none';
      m.getCanvas().style.cursor = '';
    });
  }

  function adjustIconSize() {
    try {
      const zoom    = map.getZoom();
      const maxZoom = map.getMaxZoom();
      const minZoom = map.getMinZoom();
      const iconSizeAtMaxZoom = 0.35;
      const iconSizeAtMinZoom = 0.25;
      const scale = Math.max(0,
        Math.log(iconSizeAtMaxZoom / iconSizeAtMinZoom) /
        Math.log(maxZoom / minZoom) *
        Math.log(zoom / minZoom)) * 2.5;
      if (window.mapManager && typeof window.mapManager.setIconSize === 'function')
        window.mapManager.setIconSize(scale);
    } catch (_) {}
  }

  function getMap() {
    if (map) return map;
    if (window.map && typeof window.map.easeTo === 'function') {
      map = window.map;
      createMarker();
      map.on('click', onMapClick);
      adjustIconSize();
      map.on('zoom', adjustIconSize);
      initLocationTooltip(map);
    }
    return map;
  }
  const mapIv = setInterval(() => { if (getMap()) clearInterval(mapIv); }, 500);

  function createMarker() {
    if (marker || !map) return;
    const el = document.createElement('div');
    el.style.cssText = 'position:relative;width:0;height:0;pointer-events:none';
    el.innerHTML = `
      <svg id="cdArrow" viewBox="-12 -12 24 24" xmlns="http://www.w3.org/2000/svg"
        style="position:absolute;width:24px;height:24px;transform:translate(-50%,-50%);
        filter:drop-shadow(0 0 4px rgba(255,208,96,.9));">
        <polygon points="0,-10 7,6 0,2 -7,6" fill="#ffd060" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>
      </svg>
      <div style="position:absolute;width:16px;height:16px;
        border:2px solid rgba(255,208,96,.5);border-radius:50%;
        transform:translate(-50%,-50%);animation:cdPulse 2s ease-out infinite;"></div>
    `;
    if (!document.getElementById('cdOverlayStyle')) {
      const s = document.createElement('style');
      s.id = 'cdOverlayStyle';
      s.textContent = '@keyframes cdPulse{0%{width:16px;height:16px;opacity:.8}100%{width:38px;height:38px;opacity:0}}';
      document.head.appendChild(s);
    }
    marker = new mapboxgl.Marker({ element: el, anchor: 'center' })
      .setLngLat([0, 0]).addTo(map);
  }

  function updateHeading(newPos) {
    const src = (window.__cdSettings && window.__cdSettings.headingSource) || 'auto';
    let deg = null;
    if (src !== 'delta' && typeof newPos.heading === 'number') {
      deg = newPos.heading;
    } else if (src !== 'entity') {
      if (!lastPos) return;
      const dx = newPos.x - lastPos.x;
      const dz = newPos.z - lastPos.z;
      if (dx*dx + dz*dz < 0.001) return;
      deg = Math.atan2(dx, dz) * 180 / Math.PI;
    }
    if (deg === null) return;
    lastHeading = deg;
    updateArrowRotation();
  }

  function updateArrowRotation() {
    const arrow = document.getElementById('cdArrow');
    if (arrow) {
      const arrowDeg = following && rotateWithCamera
        ? lastHeading - lastCameraHeading
        : (following && rotateWithPlayer ? 0 : lastHeading);
      arrow.style.transform = `translate(-50%,-50%) rotate(${arrowDeg}deg)`;
    }
  }

  function resetMapBearing() {
    const m = getMap();
    if (!m) return;
    if (typeof m.jumpTo === 'function') {
      m.jumpTo({ bearing: 0 });
    } else {
      m.easeTo({ bearing: 0, duration: 0 });
    }
  }

  function setRotateWithPlayer(val) {
    rotateWithPlayer = !!val;
    if (rotateWithPlayer) rotateWithCamera = false;
    if (window.__cdSettings) window.__cdSettings.rotateWithPlayer = rotateWithPlayer;
    if (window.__cdSettings) window.__cdSettings.rotateWithCamera = rotateWithCamera;
    updateArrowRotation();
    if (!following || (!rotateWithPlayer && !rotateWithCamera)) resetMapBearing();
  }

  function setRotateWithCamera(val) {
    rotateWithCamera = !!val;
    if (rotateWithCamera) rotateWithPlayer = false;
    if (window.__cdSettings) window.__cdSettings.rotateWithCamera = rotateWithCamera;
    if (window.__cdSettings) window.__cdSettings.rotateWithPlayer = rotateWithPlayer;
    updateArrowRotation();
    if (!following || (!rotateWithCamera && !rotateWithPlayer)) resetMapBearing();
  }

  function isSamePositionMessage(pos, prev) {
    if (!prev) return false;
    return pos.lng === prev.lng &&
      pos.lat === prev.lat &&
      pos.x === prev.x &&
      pos.y === prev.y &&
      pos.z === prev.z &&
      pos.realm === prev.realm &&
      pos.heading === prev.heading;
  }

  function onCameraHeading(msg) {
    if (typeof msg.heading !== 'number') return;
    if (hasCameraHeading && msg.heading === lastCameraHeading) return;
    hasCameraHeading = true;
    lastCameraHeading = msg.heading;
    if (!rotateWithCamera || !following) {
      updateArrowRotation();
      return;
    }
    updateArrowRotation();
    const mm = getMap();
    if (!mm) return;
    const view = { bearing: lastCameraHeading, duration: 50 };
    if (following && !shiftHeld && lastPos) {
      view.center = [lastPos.lng, lastPos.lat];
    }
    mm.easeTo(view);
  }

  // ── Botão flutuante status (direita) — toggle follow + expandir ───
  function toggleFollow() {
    following = !following;
    updatePanel();
    updateArrowRotation();
    if (!following) resetMapBearing();
    else if (lastPos && !rotateWithCamera) pan(lastPos.lng, lastPos.lat);
  }

  function ensureStatusToggleBtn() {
    if (document.getElementById('cdOvBar')) return;
    const bar = document.createElement('div');
    bar.id = 'cdOvBar';
    bar.style.cssText = `position:fixed;bottom:12px;right:12px;z-index:10000;
      display:flex;gap:4px;align-items:center`;

    // Botão expand/collapse (abre o painel completo)
    const expand = document.createElement('button');
    expand.id = 'cdOvExpandBtn';
    expand.title = t('expandPanelTitle');
    expand.textContent = '⊞';
    expand.style.cssText = `width:28px;height:36px;border-radius:6px;
      background:rgba(12,12,18,.9);border:1px solid rgba(255,208,96,.25);
      color:#555;font:14px monospace;cursor:pointer;
      box-shadow:0 3px 12px rgba(0,0,0,.5);backdrop-filter:blur(4px);
      transition:color .15s,border-color .15s`;
    expand.addEventListener('mouseenter', () => { expand.style.color='#ffd060'; expand.style.borderColor='rgba(255,208,96,.6)'; });
    expand.addEventListener('mouseleave', () => { expand.style.color='#555'; expand.style.borderColor='rgba(255,208,96,.25)'; });
    expand.addEventListener('click', () => {
      const panel = document.getElementById('cdOvPanel');
      if (!panel) { ensurePanel(); return; }
      const visible = panel.style.display !== 'none';
      panel.style.display = visible ? 'none' : 'block';
      expand.textContent = visible ? '⊞' : '⊟';
    });

    // Botão follow (sempre visível, reflete estado)
    const followBtn = document.createElement('button');
    followBtn.id = 'cdOvFollowFloat';
    followBtn.title = t('followToggleTitle');
    followBtn.style.cssText = `height:36px;padding:0 12px;border-radius:6px;
      background:rgba(12,30,20,.95);border:1.5px solid rgba(80,220,120,.6);
      color:#60e890;font:bold 11px 'Segoe UI',sans-serif;cursor:pointer;
      box-shadow:0 3px 12px rgba(0,0,0,.6);backdrop-filter:blur(6px);
      white-space:nowrap;transition:background .15s,border-color .15s,color .15s`;
    followBtn.textContent = t('followOn');
    followBtn.addEventListener('click', toggleFollow);

    bar.appendChild(expand);
    bar.appendChild(followBtn);
    document.body.appendChild(bar);
  }

  // ── Painel de status (direita, oculto por padrão) ─────────────────
  function ensurePanel() {
    if (document.getElementById('cdOvPanel')) return;
    const el = document.createElement('div');
    el.id = 'cdOvPanel';
    el.style.cssText = `position:fixed;bottom:56px;right:12px;z-index:9999;
      background:rgba(12,12,18,.88);color:#e8e8e8;
      font:12px/1.5 'Segoe UI',system-ui,sans-serif;
      border:1px solid rgba(255,208,96,.3);border-radius:7px;
      padding:7px 11px;min-width:210px;backdrop-filter:blur(5px);
      box-shadow:0 4px 18px rgba(0,0,0,.5);user-select:none;display:none`;
    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
        <span id="cdLangLabel" style="font-size:10px;color:#aaa">${t('languageLabel')}</span>
        <select id="cdLangSelect"
          style="flex:1;background:#111;border:1px solid #333;color:#eee;
          font-size:10px;border-radius:4px;padding:2px">
          <option value="en">English</option>
          <option value="pt">Português</option>
        </select>
      </div>
      <div id="cdOvCoords" style="font:11px/1.5 Consolas,monospace;color:#bbb;margin-bottom:2px">--</div>
      <div id="cdOvStatus" style="font-size:10px;color:#e07070">${t('connecting')}</div>
      <div style="display:flex;gap:4px;margin-top:5px">
        <button id="cdOvMarker" title="${t('teleportMarkerTitle')}"
          style="flex:1;background:rgba(100,160,255,.15);border:1px solid rgba(100,160,255,.4);
          color:#80b4ff;font:10px 'Segoe UI';padding:3px 5px;border-radius:4px;cursor:pointer">
          ${t('teleportMarkerButton')}
        </button>
        <button id="cdOvAbort" title="${t('abortTitle')}"
          style="flex:1;background:rgba(255,100,100,.12);border:1px solid rgba(255,100,100,.35);
          color:#ff8080;font:10px 'Segoe UI';padding:3px 5px;border-radius:4px;
          cursor:pointer;opacity:.35;pointer-events:none">
          ${t('abortButton')}
        </button>
      </div>
      <button id="cdOvCalibrate" title="${t('calibrationButtonTitle')}"
        style="width:100%;margin-top:4px;background:rgba(255,208,96,.08);
        border:1px solid rgba(255,208,96,.2);color:#888;
        font:10px 'Segoe UI';padding:3px 5px;border-radius:4px;cursor:pointer">
        ${t('calibrationOff')}
      </button>
    `;
    document.body.appendChild(el);
    const langSelect = document.getElementById('cdLangSelect');
    if (langSelect) {
      langSelect.value = localStorage.getItem('cd_language') || currentLang;
      langSelect.addEventListener('change', () => {
        loadLanguage(langSelect.value);
      });
    }
    document.getElementById('cdOvMarker').addEventListener('click', () => {
      sendCmd({ cmd: 'teleport_marker' });
    });
    document.getElementById('cdOvAbort').addEventListener('click', () => {
      if (!hasPreTeleport) return;
      hasPreTeleport = false;
      sendCmd({ cmd: 'abort' });
      updatePanel();
    });
    document.getElementById('cdOvCalibrate').addEventListener('click', toggleCalibrationMode);
  }

  function updatePanel() {
    ensureStatusToggleBtn();
    ensurePanel();

    // Botão flutuante de follow
    const followFloat = document.getElementById('cdOvFollowFloat');
    if (followFloat) {
      const isRound = !!(window.__cdSettings && window.__cdSettings.roundWindow);
      followFloat.textContent  = isRound ? 'F' : (following ? t('followOn') : t('followOff'));
      followFloat.title = tf('followToggleStateTitle', { state: following ? t('on') : t('off') });
      followFloat.style.background  = following ? 'rgba(12,30,20,.95)'  : 'rgba(30,20,0,.95)';
      followFloat.style.borderColor = following ? 'rgba(80,220,120,.6)' : 'rgba(255,208,96,.6)';
      followFloat.style.color       = following ? '#60e890' : '#ffd060';
    }

    // Painel expandido
    const coords  = document.getElementById('cdOvCoords');
    const status  = document.getElementById('cdOvStatus');
    const abort   = document.getElementById('cdOvAbort');
    const calib   = document.getElementById('cdOvCalibrate');
    if (coords && lastPos)
      coords.textContent = `X ${lastPos.x.toFixed(0)}  Z ${lastPos.z.toFixed(0)}  Y ${lastPos.y.toFixed(0)}`;
    if (status) {
      const ok = ws && ws.readyState === 1;
      status.textContent = ok
      ? (lastPos ? tf('realmStatus', { realm: lastPos.realm }) : t('moveCharacterToStart')) : t('serverOffline');
      status.style.color = ok ? '#60e890' : '#e07070';
    }
    if (abort) {
      abort.style.opacity       = hasPreTeleport ? '1'    : '.35';
      abort.style.pointerEvents = hasPreTeleport ? 'auto' : 'none';
    }
    if (calib) {
      calib.textContent       = calibrationMode ? t('calibrationOnClickMap') : t('calibrationOff');
      calib.style.color       = calibrationMode ? '#ffd060' : '#888';
      calib.style.borderColor = calibrationMode ? 'rgba(255,208,96,.5)' : 'rgba(255,208,96,.2)';
      calib.style.background  = calibrationMode ? 'rgba(255,208,96,.15)' : 'rgba(255,208,96,.08)';
    }
  }

  function setStatus(text, color, ms) {
    const s = document.getElementById('cdOvStatus');
    if (!s) return;
    s.textContent = text;
    s.style.color = color || '#ffd060';
    if (ms) setTimeout(() => updatePanel(), ms);
  }

  // ── Calibração ────────────────────────────────────────────────────
  function toggleCalibrationMode() {
    calibrationMode = !calibrationMode;
    updatePanel();
    const canvas = document.querySelector('.mapboxgl-canvas');
    if (canvas) canvas.style.cursor = calibrationMode ? 'crosshair' : '';
    if (calibrationMode)
      setStatus(t('clickMapAtCharacterPosition'), '#ffd060');
  }

  function onMapClick(e) {
    if (!calibrationMode) return;
    const { lng, lat } = e.lngLat;
    const realm = (lastPos && lastPos.realm) || 'pywel';
    sendCmd({ cmd: 'add_calibration', lng, lat, realm });
  }

  function pan(lng, lat) {
    const m = getMap();
    if (m) m.easeTo({ center: [lng, lat], duration: 350 });
  }

  function createCenterCrosshair() {
    if (document.getElementById('cdCenterCrosshair')) return;
    const el = document.createElement('div');
    el.id = 'cdCenterCrosshair';
    document.body.appendChild(el);
  }

  function sendCmd(obj) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
  }

  function syncCenterTeleportInputs() {
    ['cdCenterY', 'cdCenterPanelY'].forEach(id => {
      const input = document.getElementById(id);
      if (input) input.value = getCenterTeleportY();
    });
    ['cdCenterYVal', 'cdCenterPanelYVal'].forEach(id => {
      const label = document.getElementById(id);
      if (label) label.textContent = Math.round(getCenterTeleportY()).toString();
    });
  }

  function setCenterTeleportY(value) {
    const y = Number(value);
    if (!Number.isFinite(y)) return false;
    if (!window.__cdSettings) window.__cdSettings = {};
    window.__cdSettings.centerTeleportY = y;
    try { localStorage.setItem(CENTER_TELEPORT_Y_KEY, String(y)); } catch (_) {}
    syncCenterTeleportInputs();
    return true;
  }

  function getCenterTeleportY() {
    let raw = null;
    try { raw = localStorage.getItem(CENTER_TELEPORT_Y_KEY); } catch (_) {}
    if (raw === null || raw === '') raw = window.__cdSettings && window.__cdSettings.centerTeleportY;
    const y = Number(raw);
    return Number.isFinite(y) ? y : 1000;
  }

  function teleportMapCenter() {
    const m = getMap();
    if (!m || typeof m.getCenter !== 'function') return;
    const center = m.getCenter();
    const realm = (lastPos && lastPos.realm) || 'pywel';
    hasPreTeleport = true;
    updatePanel();
    sendCmd({ cmd: 'teleport_map', lng: center.lng, lat: center.lat, y: getCenterTeleportY(), realm });
  }

  // ── MapGenie location sync ────────────────────────────────────────
  let _replayingToggle = false;
  (function () {
    // ── Fetch patch ──
    const _origFetch = window.fetch;
    window.fetch = async function (...args) {
      const res = await _origFetch.apply(this, args);
      if (!_replayingToggle && res.ok) {
        try {
          const url = typeof args[0] === 'string' ? args[0]
            : (args[0] instanceof Request ? args[0].url : '');
          const method = ((args[1] && args[1].method) ||
            (args[0] instanceof Request ? args[0].method : 'GET')).toUpperCase();
          const parts = url.split('/api/v1/user/locations/');
          if (parts.length > 1 && (method === 'PUT' || method === 'DELETE')) {
            const locationId = parts[1].split('/')[0].split('?')[0];
            if (locationId) sendCmd({ cmd: 'location_toggle', locationId, found: method === 'PUT' });
          }
        } catch (_) {}
      }
      return res;
    };

    // ── XHR patch ──
    const _origOpen = XMLHttpRequest.prototype.open;
    const _origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      this._cdMethod = method ? method.toUpperCase() : 'GET';
      this._cdUrl    = typeof url === 'string' ? url : String(url);
      return _origOpen.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function (...args) {
      const xhr = this;
      xhr.addEventListener('load', function () {
        if (_replayingToggle) return;
        if (xhr.status < 200 || xhr.status >= 300) return;
        const parts = (xhr._cdUrl || '').split('/api/v1/user/locations/');
        if (parts.length > 1 && (xhr._cdMethod === 'PUT' || xhr._cdMethod === 'DELETE')) {
          const locationId = parts[1].split('/')[0].split('?')[0];
          if (locationId) sendCmd({ cmd: 'location_toggle', locationId, found: xhr._cdMethod === 'PUT' });
        }
      });
      return _origSend.apply(this, args);
    };
  })();

  function _showLocationToast(locationId, found) {
    let toast = document.getElementById('cdLocSyncToast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'cdLocSyncToast';
      toast.style.cssText = 'position:fixed;bottom:56px;left:50%;transform:translateX(-50%);z-index:99999;' +
        'background:rgba(12,12,18,.93);color:#e8e8e8;' +
        "font:12px/1.5 'Segoe UI',system-ui,sans-serif;" +
        'border:1px solid rgba(255,208,96,.45);border-radius:6px;' +
        'padding:6px 14px;pointer-events:none;' +
        'box-shadow:0 3px 12px rgba(0,0,0,.5);' +
        'transition:opacity .3s;opacity:0;white-space:nowrap';
      document.body.appendChild(toast);
    }
    const action = found ? t('locationFoundAction') : t('locationUnfoundAction');
    toast.textContent = tf('locationSyncToast', { locationId, action });
    toast.style.opacity = '1';
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { toast.style.opacity = '0'; }, 4000);
  }

  function _onLocationToggle(locationId, found) {
    _showLocationToast(locationId, found);
    if (typeof window.mapManager?.markLocationAsFound === 'function') {
      _replayingToggle = true;
      window.mapManager.markLocationAsFound(parseInt(locationId, 10), found);
      setTimeout(() => { _replayingToggle = false; }, 2000);
    }
  }

  // ── WebSocket ─────────────────────────────────────────────────────
  function connect() {
    if (ws && (ws.readyState === 0 || ws.readyState === 1)) return;
    ws = new WebSocket(WS_URL);
    ws.onopen  = () => updatePanel();
    ws.onclose = () => { updatePanel(); setTimeout(connect, RECONNECT_MS); };
    ws.onerror = () => updatePanel();
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);

        if (msg.type === 'position') {
          if (isSamePositionMessage(msg, lastPos)) return;
          updateHeading(msg);
          lastPos = msg;
          if (marker) marker.setLngLat([msg.lng, msg.lat]);
          const mm = window.mapManager && window.mapManager.map;
          if (rotateWithCamera) {
            // camera_heading controla bearing e centro nesse modo.
          } else if (following && !shiftHeld && rotateWithPlayer && mm) {
            mm.easeTo({ center: [msg.lng, msg.lat], bearing: lastHeading, duration: 150 });
          } else if (following && !shiftHeld) {
            pan(msg.lng, msg.lat);
          }
          updatePanel();

        } else if (msg.type === 'camera_heading') {
          onCameraHeading(msg);

        } else if (msg.type === 'waypoints') {
          waypoints = msg.data || [];
          renderWaypoints();

        } else if (msg.type === 'teleport_marker_result') {
          if (msg.ok) {
            hasPreTeleport = true; updatePanel();
          } else {
            setStatus(msg.err || t('noMarkerOnMap'), '#e07070', 3000);
          }

        } else if (msg.type === 'teleport_map_result') {
          if (msg.ok) {
            hasPreTeleport = true; updatePanel();
          } else {
            hasPreTeleport = false; updatePanel();
            setStatus(msg.err || t('mapTeleportFailed'), '#e07070', 3000);
          }

        } else if (msg.type === 'calibration_result') {
          if (msg.reset) {
            calibrationMode = false; updatePanel();
            setStatus(tf('calibrationReset'), '#60e890', 3000);
          } else if (msg.ok) {
            setStatus(tf('calibrationPointsSaved', { count: msg.count }), '#60e890', 3000);
          }
        } else if (msg.type === 'location_toggle') {
          _onLocationToggle(msg.locationId, msg.found);
        }

        // backward-compat: mensagens sem type são posição
        if (!msg.type && typeof msg.lng === 'number') {
          if (isSamePositionMessage(msg, lastPos)) return;
          updateHeading(msg);
          lastPos = msg;
          if (marker) marker.setLngLat([msg.lng, msg.lat]);
          const mm2 = window.mapManager && window.mapManager.map;
          if (rotateWithCamera) {
            // camera_heading controla bearing e centro nesse modo.
          } else if (following && !shiftHeld && rotateWithPlayer && mm2) {
            mm2.easeTo({ center: [msg.lng, msg.lat], bearing: lastHeading, duration: 150 });
          } else if (following && !shiftHeld) {
            pan(msg.lng, msg.lat);
          }
          updatePanel();
        }
      } catch (_) {}
    };
  }

  // ── Botão flutuante para abrir/fechar waypoints ───────────────────
  function ensureWpToggleBtn() {
    if (document.getElementById('cdWpToggle')) return;
    const btn = document.createElement('button');
    btn.id = 'cdWpToggle';
    btn.title = t('waypointsToggleTitle');
    btn.textContent = '⭕';
    btn.style.cssText = `position:fixed;bottom:12px;left:12px;z-index:10000;
      width:36px;height:36px;border-radius:50%;
      background:rgba(12,12,18,.9);border:1px solid rgba(255,208,96,.35);
      color:#ffd060;font:16px 'Segoe UI';cursor:pointer;
      box-shadow:0 3px 12px rgba(0,0,0,.5);
      display:flex;align-items:center;justify-content:center;
      backdrop-filter:blur(4px);transition:border-color .15s,background .15s`;
    btn.addEventListener('mouseenter', () => {
      btn.style.background = 'rgba(255,208,96,.18)';
      btn.style.borderColor = 'rgba(255,208,96,.7)';
    });
    btn.addEventListener('mouseleave', () => {
      btn.style.background = 'rgba(12,12,18,.9)';
      btn.style.borderColor = 'rgba(255,208,96,.35)';
    });
    btn.addEventListener('click', () => {
      if (ensureWaypointPopup()) return;
      const panel = document.getElementById('cdWpPanel');
      if (!panel) { ensureWaypointPanel(); return; }
      const visible = panel.style.display !== 'none';
      panel.style.display = visible ? 'none' : 'flex';
    });
    document.body.appendChild(btn);
  }

  function ensureCenterTeleportBtn() {
    if (document.getElementById('cdCenterTp')) return;
    const btn = document.createElement('button');
    btn.id = 'cdCenterTp';
    btn.title = t('centerTeleportOpenTitle');
    btn.textContent = '◎';
    btn.style.cssText = `position:fixed;bottom:12px;left:56px;z-index:10000;
      width:36px;height:36px;border-radius:50%;
      background:rgba(12,12,18,.9);border:1px solid rgba(100,160,255,.4);
      color:#80b4ff;font:18px 'Segoe UI';cursor:pointer;
      box-shadow:0 3px 12px rgba(0,0,0,.5);
      display:flex;align-items:center;justify-content:center;
      backdrop-filter:blur(4px);transition:border-color .15s,background .15s`;
    btn.addEventListener('mouseenter', () => {
      btn.style.background = 'rgba(100,160,255,.18)';
      btn.style.borderColor = 'rgba(100,160,255,.75)';
    });
    btn.addEventListener('mouseleave', () => {
      btn.style.background = 'rgba(12,12,18,.9)';
      btn.style.borderColor = 'rgba(100,160,255,.4)';
    });
    btn.addEventListener('click', () => {
      const panel = document.getElementById('cdCenterTpPanel');
      if (!panel) { ensureCenterTeleportPanel(); return; }
      const visible = panel.style.display !== 'none';
      panel.style.display = visible ? 'none' : 'flex';
    });
    document.body.appendChild(btn);
  }

  function ensureCenterTeleportPanel() {
    if (document.getElementById('cdCenterTpPanel')) return;
    const el = document.createElement('div');
    el.id = 'cdCenterTpPanel';
    el.style.cssText = `position:fixed;bottom:56px;left:56px;z-index:9999;
      background:rgba(12,12,18,.92);color:#e8e8e8;
      font:12px/1.5 'Segoe UI',system-ui,sans-serif;
      border:1px solid rgba(100,160,255,.3);border-radius:7px;
      padding:8px 10px;width:210px;backdrop-filter:blur(5px);
      box-shadow:0 4px 18px rgba(0,0,0,.5);
      display:none;flex-direction:column;gap:7px;overflow:hidden`;
    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px">
        <span id="cdCenterPanelTitle" style="color:#80b4ff;font-weight:600;flex:1;font-size:12px">${t('centerScreenTitle')}</span>
      </div>
      <div style="display:flex;align-items:center;gap:7px">
        <span style="color:#bbb;font-size:11px;white-space:nowrap">Y <span id="cdCenterPanelYVal">${Math.round(getCenterTeleportY())}</span></span>
        <input type="range" id="cdCenterPanelY" min="-5000" max="5000" step="10"
          value="${getCenterTeleportY()}"
          style="flex:1;min-width:110px;accent-color:#80b4ff;cursor:pointer">
      </div>
      <button id="cdCenterPanelTp" title="${t('centerTeleportButtonTitle')}"
        style="background:rgba(100,160,255,.14);border:1px solid rgba(100,160,255,.45);
        color:#80b4ff;font:11px 'Segoe UI';padding:4px 8px;border-radius:4px;
        cursor:pointer;width:100%">
        ${t('teleportButton')}
      </button>
    `;
    document.body.appendChild(el);
    document.getElementById('cdCenterPanelY').addEventListener('input', (e) => {
      if (!setCenterTeleportY(e.target.value)) e.target.value = getCenterTeleportY();
    });
    document.getElementById('cdCenterPanelTp').addEventListener('click', teleportMapCenter);
  }

  function getWaypointPopupDoc() {
    try {
      if (waypointPopup && !waypointPopup.closed && waypointPopup.document)
        return waypointPopup.document;
    } catch (_) {}
    return null;
  }

  function bindWaypointPopupControls(doc) {
    const save = doc.getElementById('cdWpPopupSave');
    const filter = doc.getElementById('cdWpPopupFilter');
    if (filter) {
      filter.value = waypointFilter;
      filter.addEventListener('input', () => setWaypointFilter(filter.value));
    }
    if (save) save.addEventListener('click', () => {
      const name = prompt(tf('waypointNamePrompt'), lastPos
        ? `${lastPos.realm === 'abyss' ? '[Abyss] ' : ''}${Math.round(lastPos.x)}, ${Math.round(lastPos.z)}`
        : t('waypointDefaultName'));
      if (name !== null) sendCmd({ cmd: 'save_waypoint', name });
    });
  }

  function ensureWaypointPopup() {
    try {
      if (waypointPopup && !waypointPopup.closed) {
        waypointPopup.focus();
        return true;
      }
    } catch (_) {
      waypointPopup = null;  // janela Qt destruída — reseta referência
    }
    try {
      waypointPopup = window.open('', 'cdOverlayWaypoints',
        'width=300,height=560,resizable=yes,scrollbars=no');
      if (!waypointPopup) return false;
      const doc = waypointPopup.document;
      doc.open();
      doc.write(`<!doctype html>
        <html>
        <head>
          <meta charset="utf-8">
          <title>${t('waypointsWindowTitle')}</title>
          <style>
            html,body{
              margin:0;width:100%;height:100%;overflow:hidden;
              background:#0f0f1a;color:#e8e8e8;
              font:12px/1.5 'Segoe UI',system-ui,sans-serif;
            }
            *{box-sizing:border-box}
            button{font-family:'Segoe UI',system-ui,sans-serif}
            .wrap{
              height:100%;display:flex;flex-direction:column;gap:7px;
              padding:10px;background:rgba(12,12,18,.96);
              border:1px solid rgba(255,208,96,.25);
            }
            .row{display:flex;align-items:center;gap:6px;flex-shrink:0}
            .title{flex:1;font-size:12px;font-weight:600;color:#ffd060}
            .list{
              display:flex;flex-direction:column;gap:3px;overflow-y:auto;
              min-height:72px;border-radius:5px;
            }
            #cdWpPopupList{flex:1}
            .sep{height:1px;background:rgba(255,255,255,.07);flex-shrink:0}
            .filter{
              width:100%;flex-shrink:0;border-radius:5px;
              background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);
              color:#e8e8e8;padding:5px 8px;outline:none;
            }
            .filter:focus{border-color:rgba(255,208,96,.45)}
            .btn{
              border-radius:4px;cursor:pointer;padding:3px 8px;
              background:rgba(255,208,96,.13);
              border:1px solid rgba(255,208,96,.35);color:#ffd060;
            }
            .btn.blue{
              background:rgba(100,160,255,.11);
              border-color:rgba(100,160,255,.35);color:#80b4ff;
            }
            .full{width:100%;flex-shrink:0}
          </style>
        </head>
        <body>
          <div class="wrap">
            <div class="row">
              <div id="cdWpPopupTitle" class="title">${t('waypointsTitle')}</div>
              <button id="cdWpPopupSave" class="btn">${t('saveButton')}</button>
            </div>
            <input id="cdWpPopupFilter" class="filter" placeholder="${t('filterWaypointsPlaceholder')}">
            <div id="cdWpPopupList" class="list"></div>
          </div>
        </body>
        </html>`);
      doc.close();
      bindWaypointPopupControls(doc);
      renderWaypoints();
      waypointPopup.focus();
      return true;
    } catch (_) {
      waypointPopup = null;
      return false;
    }
  }

  // ── Painel de Waypoints (esquerda) ────────────────────────────────
  function ensureWaypointPanel() {
    if (document.getElementById('cdWpPanel')) return;
    const el = document.createElement('div');
    el.id = 'cdWpPanel';
    el.style.cssText = `position:fixed;bottom:56px;left:12px;z-index:9999;
      background:rgba(12,12,18,.92);color:#e8e8e8;
      font:12px/1.5 'Segoe UI',system-ui,sans-serif;
      border:1px solid rgba(255,208,96,.25);border-radius:7px;
      padding:8px 10px;width:224px;max-height:520px;
      backdrop-filter:blur(5px);box-shadow:0 4px 18px rgba(0,0,0,.5);
      display:none;flex-direction:column;gap:5px;overflow:hidden;`;
    el.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px">
        <span id="cdWpPanelTitle" style="color:#ffd060;font-weight:600;flex:1;font-size:12px">${t('waypointsPanelTitle')}</span>
        <button id="cdWpSave" title="${t('saveCurrentPositionTitle')}"
          style="background:rgba(255,208,96,.15);border:1px solid rgba(255,208,96,.4);
          color:#ffd060;font:11px 'Segoe UI';padding:2px 8px;border-radius:4px;cursor:pointer">
          ${t('saveButton')}
        </button>
      </div>
      <input id="cdWpFilter" placeholder="${t('filterWaypointsPlaceholder')}"
        style="width:100%;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);
        color:#e8e8e8;font:11px 'Segoe UI';padding:4px 7px;border-radius:4px;outline:none">
      <div id="cdWpList" style="overflow-y:auto;max-height:170px;display:flex;
        flex-direction:column;gap:3px;flex-shrink:0"></div>
    `;
    document.body.appendChild(el);

    document.getElementById('cdWpFilter').addEventListener('input', (e) => setWaypointFilter(e.target.value));
    document.getElementById('cdWpSave').addEventListener('click', () => {
      const name = prompt(tf('waypointNamePrompt'), lastPos
        ? `${lastPos.realm === 'abyss' ? '[Abyss] ' : ''}${Math.round(lastPos.x)}, ${Math.round(lastPos.z)}`
        : t('waypointDefaultName'));
      if (name !== null) sendCmd({ cmd: 'save_waypoint', name });
    });
  }

  function setWaypointFilter(value) {
    waypointFilter = (value || '').trim().toLowerCase();
    const panelInput = document.getElementById('cdWpFilter');
    const popupInput = getWaypointPopupDoc()?.getElementById('cdWpPopupFilter');
    if (panelInput && panelInput.value !== value) panelInput.value = value || '';
    if (popupInput && popupInput.value !== value) popupInput.value = value || '';
    renderWaypoints();
  }

  function matchesWaypointFilter(wp) {
    if (!waypointFilter) return true;
    const text = [
      wp.name,
      wp.realm,
      wp.absX, wp.absY, wp.absZ,
      wp.x, wp.y, wp.z
    ].filter(v => v !== undefined && v !== null).join(' ').toLowerCase();
    return text.includes(waypointFilter);
  }

  function renderWaypointList(list) {
    if (!list) return;
    if (waypoints.length === 0) {
      list.innerHTML = `<div style="color:#555;font-size:11px;text-align:center;padding:4px 0">
        ${t('noWaypointsSaved')}</div>`;
      return;
    }
    const items = waypoints
      .map((wp, i) => ({ wp, i }))
      .filter(item => matchesWaypointFilter(item.wp));
    if (items.length === 0) {
      list.innerHTML = `<div style="color:#555;font-size:11px;text-align:center;padding:4px 0">
        ${t('noWaypointsFound')}</div>`;
      return;
    }
    list.innerHTML = items.map(({ wp, i }) => `
      <div style="display:flex;align-items:center;gap:4px;background:rgba(255,255,255,.04);
        border-radius:4px;padding:3px 6px;">
        <span style="flex:1;font-size:11px;white-space:nowrap;overflow:hidden;
          text-overflow:ellipsis;color:#ccc" title="${wp.name}">${wp.name}</span>
        <button data-tp="${i}" title="${t('teleportWaypointTitle')}"
          style="background:rgba(255,208,96,.15);border:1px solid rgba(255,208,96,.35);
          color:#ffd060;font:10px 'Segoe UI';padding:1px 5px;border-radius:3px;
          cursor:pointer;flex-shrink:0">⭕</button>
        <button data-del="${i}" title="${t('removeWaypointTitle')}"
          style="background:transparent;border:none;color:#555;font:12px monospace;
          cursor:pointer;padding:0 2px;flex-shrink:0">✕</button>
      </div>
    `).join('');

    list.querySelectorAll('[data-tp]').forEach(btn => {
      btn.addEventListener('click', () => {
        const wp = waypoints[+btn.dataset.tp];
        if (wp) {
          hasPreTeleport = true;
          updatePanel();
          sendCmd({ cmd: 'teleport', x: wp.absX, y: wp.absY, z: wp.absZ });
        }
      });
    });
    list.querySelectorAll('[data-del]').forEach(btn => {
      btn.addEventListener('click', () => {
        sendCmd({ cmd: 'delete_waypoint', index: +btn.dataset.del });
      });
    });
  }

  function renderWaypoints() {
    ensureWaypointPanel();
    renderWaypointList(document.getElementById('cdWpList'));
    renderWaypointList(getWaypointPopupDoc()?.getElementById('cdWpPopupList'));
  }

  // ── Layout adaptativo para janela circular ────────────────────────
  function applyRoundLayout(isRound) {
    ensureStatusToggleBtn();
    ensureWpToggleBtn();
    ensureCenterTeleportBtn();
    const bar    = document.getElementById('cdOvBar');
    const expand = document.getElementById('cdOvExpandBtn');
    const follow = document.getElementById('cdOvFollowFloat');
    const wpBtn  = document.getElementById('cdWpToggle');
    const tpBtn  = document.getElementById('cdCenterTp');
    if (!bar || !wpBtn || !tpBtn) return;

    if (isRound) {
      // Botão waypoints: remove position:fixed para entrar no flow do bar
      if (wpBtn.parentNode !== bar) bar.insertBefore(wpBtn, bar.firstChild);
      if (tpBtn.parentNode !== bar) bar.insertBefore(tpBtn, wpBtn.nextSibling);
      wpBtn.style.cssText = 'width:30px;height:30px;border-radius:50%;flex:0 0 30px;' +
        'background:rgba(12,12,18,.9);border:1px solid rgba(255,208,96,.35);' +
        'color:#ffd060;font:14px "Segoe UI";cursor:pointer;' +
        'box-shadow:0 3px 12px rgba(0,0,0,.5);backdrop-filter:blur(4px);' +
        'display:flex;align-items:center;justify-content:center;';
      tpBtn.style.cssText = 'width:30px;height:30px;border-radius:50%;flex:0 0 30px;' +
        'background:rgba(12,12,18,.9);border:1px solid rgba(100,160,255,.4);' +
        'color:#80b4ff;font:15px "Segoe UI";cursor:pointer;' +
        'box-shadow:0 3px 12px rgba(0,0,0,.5);backdrop-filter:blur(4px);' +
        'display:flex;align-items:center;justify-content:center;';

      // Bar centralizada dentro da largura útil do círculo, invisível por padrão
      bar.style.cssText = 'position:fixed;bottom:30px;left:50%;' +
        'transform:translateX(-50%);z-index:10000;display:flex;gap:5px;' +
        'align-items:center;justify-content:center;max-width:132px;' +
        'opacity:0;transition:opacity .16s;pointer-events:none;';
      if (expand) expand.style.display = 'none';
      if (follow) {
        follow.style.width = '30px';
        follow.style.height = '30px';
        follow.style.padding = '0';
        follow.style.borderRadius = '50%';
        follow.style.flex = '0 0 30px';
        follow.style.font = 'bold 12px "Segoe UI",sans-serif';
        follow.textContent = 'F';
      }

      // Hover na borda inferior -> mostra botões; sair da zona oculta de novo.
      if (!window.__cdRoundBottomBound) {
        window.__cdRoundBottomBound = true;
        let _roundBottomOverBar = false;
        let _roundBottomHideTimer = null;
        window.__cdSetRoundBottomVisible = (visible) => {
          const b = document.getElementById('cdOvBar');
          if (!b) return;
          b.style.opacity = visible ? '1' : '0';
          b.style.pointerEvents = visible ? 'auto' : 'none';
        };
        document.addEventListener('mousemove', (e) => {
          const b = document.getElementById('cdOvBar');
          if (!b) return;
          if (!(window.__cdSettings && window.__cdSettings.roundWindow)) return;
          const inBottomHoverZone = e.clientY >= window.innerHeight - 76;
          if (inBottomHoverZone || _roundBottomOverBar) {
            clearTimeout(_roundBottomHideTimer);
            window.__cdSetRoundBottomVisible(true);
          } else {
            clearTimeout(_roundBottomHideTimer);
            _roundBottomHideTimer = setTimeout(() => {
              if (!_roundBottomOverBar) window.__cdSetRoundBottomVisible(false);
            }, 180);
          }
        });
        bar.addEventListener('mouseenter', () => {
          _roundBottomOverBar = true;
          clearTimeout(_roundBottomHideTimer);
          window.__cdSetRoundBottomVisible(true);
        });
        bar.addEventListener('mouseleave', () => {
          _roundBottomOverBar = false;
          _roundBottomHideTimer = setTimeout(() => window.__cdSetRoundBottomVisible(false), 180);
        });
      }
    } else {
      // Restaura waypoints button para body com estilo original
      if (wpBtn.parentNode === bar) document.body.appendChild(wpBtn);
      if (tpBtn.parentNode === bar) document.body.appendChild(tpBtn);
      wpBtn.style.cssText = 'position:fixed;bottom:12px;left:12px;z-index:10000;' +
        'width:36px;height:36px;border-radius:50%;' +
        'background:rgba(12,12,18,.9);border:1px solid rgba(255,208,96,.35);' +
        'color:#ffd060;font:16px "Segoe UI";cursor:pointer;' +
        'box-shadow:0 3px 12px rgba(0,0,0,.5);' +
        'display:flex;align-items:center;justify-content:center;' +
        'backdrop-filter:blur(4px);transition:border-color .15s,background .15s';
      tpBtn.style.cssText = 'position:fixed;bottom:12px;left:56px;z-index:10000;' +
        'width:36px;height:36px;border-radius:50%;' +
        'background:rgba(12,12,18,.9);border:1px solid rgba(100,160,255,.4);' +
        'color:#80b4ff;font:18px "Segoe UI";cursor:pointer;' +
        'box-shadow:0 3px 12px rgba(0,0,0,.5);' +
        'display:flex;align-items:center;justify-content:center;' +
        'backdrop-filter:blur(4px);transition:border-color .15s,background .15s';
      bar.style.cssText = 'position:fixed;bottom:12px;right:12px;z-index:10000;' +
        'display:flex;gap:4px;align-items:center;opacity:1;pointer-events:auto;';
      if (expand) expand.style.display = '';
      if (follow) {
        follow.style.width = '';
        follow.style.height  = '36px';
        follow.style.padding = '0 12px';
        follow.style.borderRadius = '6px';
        follow.style.flex = '';
        follow.style.font    = 'bold 11px "Segoe UI",sans-serif';
      }
    }
  }

  // ── Map settings ──────────────────────────────────────────────────
  const POSITION_KEY = 'mgxbox_last_position';

  function waitForElement(selector, callback, timeout = 15000) {
    const el = document.querySelector(selector);
    if (el) { callback(el); return; }
    const iv = setInterval(() => {
      const el = document.querySelector(selector);
      if (el) { clearInterval(iv); callback(el); }
    }, 300);
    setTimeout(() => clearInterval(iv), timeout);
  }

  function saveMapPosition(m) {
    const c = m.getCenter();
    localStorage.setItem(POSITION_KEY, JSON.stringify(
      { lng: c.lng, lat: c.lat, zoom: m.getZoom() }));
  }

  function restoreMapPosition(m) {
    try {
      const saved = localStorage.getItem(POSITION_KEY);
      if (!saved) return;
      const { lng, lat, zoom } = JSON.parse(saved);
      m.jumpTo({ center: [lng, lat], zoom });
    } catch (_) {}
  }

  function applySettings(cfg) {
    if (cfg.language && cfg.language !== currentLang) {
      loadLanguage(cfg.language);
    }
    if (cfg.autoHideFound) {
      waitForElement('#toggle-found', (btn) => {
        if (!btn.classList.contains('disabled')) btn.click();
      });
    }
    if (cfg.autoHideLeftSidebar) {
      waitForElement('.sidebar-close .left-arrow, .sidebar-close', (btn) => btn.click());
    }
    if (cfg.autoHideRightSidebar) {
      waitForElement('#right-sidebar .sidebar-close', (btn) => btn.click());
    }

    const waitMap = setInterval(() => {
      const m = getMap();
      if (!m) return;
      clearInterval(waitMap);
      if (cfg.restoreLastPosition) restoreMapPosition(m);
      m.on('moveend', () => saveMapPosition(m));
    }, 300);
    setTimeout(() => clearInterval(waitMap), 30000);
    applyRoundLayout(!!cfg.roundWindow);
    setRotateWithPlayer(!!cfg.rotateWithPlayer);
    setRotateWithCamera(!!cfg.rotateWithCamera);
  }

  window.__cdApplyRotationSettings = function(cfg) {
    if (!cfg) return;
    setRotateWithPlayer(!!cfg.rotateWithPlayer);
    setRotateWithCamera(!!cfg.rotateWithCamera);
  };

  window.__cdApplyRoundLayout = function(cfg) {
    applyRoundLayout(!!(cfg && cfg.roundWindow));
    updatePanel();
  };

  // ── Detecção de login necessário ───────────────────────────────────
  (function detectLogin() {
    if (window.location.pathname.includes('login')) return;
    setTimeout(() => {
      const needsLogin =
        document.querySelector('a[href="https://mapgenie.io/crimson-desert/logout"]') === null ||
        (window.Inertia && !window.__page?.props?.auth?.user);
      if (needsLogin) {
        window.location.href = 'cdcompanion://login-needed';
      }
    }, 3000);
  })();

  loadLanguage((window.__cdSettings && window.__cdSettings.language) || localStorage.getItem('cd_language') || 'en');

  // ── CSS overrides ──────────────────────────────────────────────────
  (function injectCSS() {
    if (document.getElementById('cdOverrideCSS')) return;
    const s = document.createElement('style');
    s.id = 'cdOverrideCSS';
    s.textContent = `
      @media (max-width: 767.98px) {
        body.map .navbar { display: none !important; }
        #left-sidebar, #right-sidebar { display: block !important; }
      }
      .mapboxgl-ctrl-bottom-right, #map-type-control { display: none !important; }
      #cdCenterCrosshair {
        position:fixed;inset:0;width:100vw;height:100vh;
        pointer-events:none;z-index:9998;
      }
      #cdCenterCrosshair::before,
      #cdCenterCrosshair::after {
        content:'';position:absolute;
        background:rgba(255,208,96,.42);
        box-shadow:0 0 4px rgba(0,0,0,.55);
      }
      #cdCenterCrosshair::before {
        top:50%;left:0;width:100%;height:1px;
        transform:translateY(-50%);
      }
      #cdCenterCrosshair::after {
        top:0;left:50%;width:1px;height:100%;
        transform:translateX(-50%);
      }
    `;
    document.head.appendChild(s);
  })();

  window.__cdApplySettings = applySettings;
  applySettings(window.__cdSettings || {});

  createCenterCrosshair();
  ensureStatusToggleBtn();
  updatePanel();
  ensureWpToggleBtn();
  ensureCenterTeleportBtn();
  ensureCenterTeleportPanel();
  applyRoundLayout(!!(window.__cdSettings && window.__cdSettings.roundWindow));
  // ambos os painéis começam ocultos
  ensureWaypointPanel();
  renderWaypoints();
  connect();
  setInterval(() => {
    if (window.mapManager && typeof window.mapManager.updateFoundLocationsStyle === 'function')
      window.mapManager.updateFoundLocationsStyle();
  }, 50);
})();
