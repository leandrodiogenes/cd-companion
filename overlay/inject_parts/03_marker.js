  function getMap() {
    if (map) return map;
    if (window.map && typeof window.map.easeTo === 'function') {
      map = window.map;
      createMarker();
      createMapMarker();
      adjustIconSize();
      map.on('zoom', adjustIconSize);
      initLocationTooltip(map);
    }
    return map;
  }
  const mapIv = setInterval(() => { if (getMap()) clearInterval(mapIv); }, 500);

  function createMapMarker() {
    if (mapMarker || !map) return;
    const el = document.createElement('div');
    el.style.cssText = 'position:relative;width:0;height:0;cursor:pointer';
    const img = document.createElement('img');
    img.src = 'https://raw.githubusercontent.com/leandrodiogenes/cd-companion/main/mark.png';
    img.style.cssText = 'position:absolute;width:32px;height:32px;transform:translate(-50%,-100%);filter:drop-shadow(0 0 4px rgba(255,80,80,.9));';
    el.appendChild(img);
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      showMapMarkerPopup(el);
    });
    mapMarker = new mapboxgl.Marker({ element: el, anchor: 'bottom' })
      .setLngLat([0, 0]).addTo(map);
    mapMarker.getElement().style.display = 'none';
  }

  function showMapMarkerPopup(anchorEl) {
    let popup = document.getElementById('cdMapMarkerPopup');
    if (popup) { popup.remove(); return; }
    if (!teleportEnabled) return;
    popup = document.createElement('div');
    popup.id = 'cdMapMarkerPopup';
    const rect = anchorEl.getBoundingClientRect();
    popup.style.cssText = `
      position:fixed;z-index:99999;
      left:${rect.left}px;top:${rect.top - 40}px;
      transform:translate(-50%,-100%);
      background:rgba(12,12,18,.95);
      border:1px solid rgba(255,80,80,.45);border-radius:6px;
      padding:6px 10px;box-shadow:0 3px 12px rgba(0,0,0,.5);
      display:flex;flex-direction:column;align-items:center;gap:6px;
      font:12px 'Segoe UI',sans-serif;color:#e8e8e8;white-space:nowrap;
    `;
    popup.innerHTML = `
      <span style="font-size:11px;color:#aaa">${_t('marker.map_marker_label')}</span>
      <button id="cdMapMarkerTpBtn"
        style="background:rgba(255,80,80,.2);border:1px solid rgba(255,80,80,.5);
        color:#ff6666;font:11px 'Segoe UI';padding:3px 10px;border-radius:4px;cursor:pointer">
        ${_t('marker.teleport_here')}
      </button>
    `;
    document.body.appendChild(popup);
    popup.querySelector('#cdMapMarkerTpBtn').addEventListener('click', () => {
      sendCmd({ cmd: 'teleport_marker' });
      popup.remove();
    });
    const close = (e) => { if (!popup.contains(e.target) && e.target !== anchorEl) { popup.remove(); document.removeEventListener('click', close); } };
    setTimeout(() => document.addEventListener('click', close), 0);
  }

  // ── Off-screen map destination indicator ──────────────────────────
  function ensureEdgeIndicator() {
    if (document.getElementById('cdEdgeIndicator')) return;
    const el = document.createElement('div');
    el.id = 'cdEdgeIndicator';
    el.style.cssText = `
      position:fixed;z-index:9000;width:28px;height:28px;
      pointer-events:none;display:none;
      transform-origin:center center;
    `;
    el.innerHTML = `
      <img src="https://raw.githubusercontent.com/leandrodiogenes/cd-companion/main/mark.png"
        style="width:28px;height:28px;filter:drop-shadow(0 0 4px rgba(255,80,80,.9));display:block;">
    `;
    document.body.appendChild(el);
  }

  function updateEdgeIndicator() {
    const el = document.getElementById('cdEdgeIndicator');
    if (!el || mapDestLng === null || mapDestLat === null) return;

    const container = map.getContainer();
    const rect = container.getBoundingClientRect();
    const pt = map.project([mapDestLng, mapDestLat]);

    const pad = 14;
    const isRound = !!(window.__cdSettings && window.__cdSettings.roundWindow);
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const hw = cx - pad;
    const hh = cy - pad;

    let inView;
    if (isRound) {
      const dx = (pt.x - cx) / hw;
      const dy = (pt.y - cy) / hh;
      inView = dx * dx + dy * dy <= 1;
    } else {
      inView = pt.x >= pad && pt.x <= rect.width - pad &&
               pt.y >= pad && pt.y <= rect.height - pad;
    }

    if (inView) { el.style.display = 'none'; return; }

    el.style.display = 'block';

    const angle = Math.atan2(pt.y - cy, pt.x - cx);
    let ex, ey;
    if (isRound) {
      ex = rect.left + cx + hw * Math.cos(angle);
      ey = rect.top  + cy + hh * Math.sin(angle);
    } else {
      const tx = Math.cos(angle);
      const ty = Math.sin(angle);
      const scale = Math.min(Math.abs(hw / (tx || 1e-9)), Math.abs(hh / (ty || 1e-9)));
      ex = rect.left + cx + tx * scale;
      ey = rect.top  + cy + ty * scale;
    }

    el.style.left = (ex - 14) + 'px';
    el.style.top  = (ey - 14) + 'px';
  }

  function installEdgeIndicatorListener() {
    if (map.__cdEdgeListener) return;
    map.__cdEdgeListener = true;
    map.on('move', updateEdgeIndicator);
    map.on('zoom', updateEdgeIndicator);
  }

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
      .setLngLat(lastPos ? [lastPos.lng, lastPos.lat] : [0, 0]).addTo(map);
    if (lastPos && following) liveEaseTo(map, { center: [lastPos.lng, lastPos.lat] });
  }

