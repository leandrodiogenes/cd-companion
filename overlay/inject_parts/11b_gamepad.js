  // ── Gamepad navigation (controller map interaction) ─────────────────
  const _GP_CONFIG = {
    deadzone: 0.15,
    panSpeed: 12,
    zoomSpeed: 0.05,
    zoomStickSpeed: 0.07,
  };

  const _GP_BUTTONS = {
    A: 0, B: 1,
    LB: 4, RB: 5,
  };

  let _gpIndex = null;
  let _gpPrevButtons = {};
  let _gpLoopId = null;
  let _gpChannel = null;

  function _gpApplyDeadzone(v, dz) {
    if (Math.abs(v) < dz) return 0;
    return (v - Math.sign(v) * dz) / (1 - dz);
  }

  function _gpWasJustPressed(buttons, index) {
    const pressed = buttons[index]?.pressed ?? false;
    const was = _gpPrevButtons[index] ?? false;
    _gpPrevButtons[index] = pressed;
    return pressed && !was;
  }

  function _gpHoverAtCenter() {
    const x = window.innerWidth / 2;
    const y = window.innerHeight / 2;
    const canvas = document.querySelector('.mapboxgl-canvas');
    if (!canvas) return;
    canvas.dispatchEvent(new MouseEvent('mousemove', {
      bubbles: true, cancelable: true, clientX: x, clientY: y,
    }));
  }

  function _gpClickAtCenter() {
    const x = window.innerWidth / 2;
    const y = window.innerHeight / 2;
    const target = document.elementFromPoint(x, y);
    if (!target) return;
    const opts = { bubbles: true, cancelable: true, clientX: x, clientY: y };
    target.dispatchEvent(new MouseEvent('mousedown', opts));
    target.dispatchEvent(new MouseEvent('mouseup', opts));
    target.dispatchEvent(new MouseEvent('click', opts));
  }

  function _gpClosePopup() {
    const btn = document.querySelector('.mapboxgl-popup-close-button');
    if (btn) btn.click();
  }

  function _gpLoop() {
    if (!_gpLoopId || _gpIndex === null) return;
    // Pause map navigation while nearby or waypoints popup is open
    if (isNearbyPopupOpen() || (waypointPopup && !waypointPopup.closed)) return;

    const gp = navigator.getGamepads()[_gpIndex];
    if (!gp || !gp.connected) { _gpIndex = null; return; }

    const m = getMap();
    if (!m) return;

    // Button actions
    if (_gpWasJustPressed(gp.buttons, _GP_BUTTONS.A)) _gpClickAtCenter();
    if (_gpWasJustPressed(gp.buttons, _GP_BUTTONS.B)) _gpClosePopup();

    // Pan — left stick
    const lx = _gpApplyDeadzone(gp.axes[0], _GP_CONFIG.deadzone);
    const ly = _gpApplyDeadzone(gp.axes[1], _GP_CONFIG.deadzone);
    if (lx !== 0 || ly !== 0) {
      m.panBy([lx * _GP_CONFIG.panSpeed, ly * _GP_CONFIG.panSpeed], { animate: false });
      // Hover at center for tooltips only while panning
      _gpHoverAtCenter();
    }

    // Zoom — LB / RB
    let zoomDelta = 0;
    if (gp.buttons[_GP_BUTTONS.RB]?.pressed) zoomDelta += _GP_CONFIG.zoomSpeed;
    if (gp.buttons[_GP_BUTTONS.LB]?.pressed) zoomDelta -= _GP_CONFIG.zoomSpeed;

    // Zoom — right stick Y
    const ry = _gpApplyDeadzone(gp.axes[3], _GP_CONFIG.deadzone);
    if (ry !== 0) zoomDelta -= ry * _GP_CONFIG.zoomStickSpeed;

    if (zoomDelta !== 0) {
      m.setZoom(m.getZoom() + zoomDelta, { animate: false });
    }
  }

  function _gpStart() {
    if (_gpLoopId) return;
    _gpPrevButtons = {};
    _gpLoopId = true;
    // Throttled loop at ~60fps using MessageChannel (immune to Chromium timer throttling)
    let _gpLastTick = 0;
    const channel = new MessageChannel();
    channel.port2.onmessage = () => {
      if (!_gpLoopId) return;
      const now = performance.now();
      if (now - _gpLastTick >= 16) {
        _gpLastTick = now;
        _gpLoop();
      }
      channel.port1.postMessage(null);
    };
    channel.port1.postMessage(null);
    _gpChannel = channel;
  }

  function _gpStop() {
    _gpLoopId = null;
    if (_gpChannel) {
      _gpChannel.port1.close();
      _gpChannel.port2.close();
      _gpChannel = null;
    }
    _gpPrevButtons = {};
  }

  // Gamepad connect/disconnect
  window.addEventListener('gamepadconnected', (e) => {
    _gpIndex = e.gamepad.index;
  });
  window.addEventListener('gamepaddisconnected', (e) => {
    if (e.gamepad.index === _gpIndex) _gpIndex = null;
  });

  // Detect initial gamepad
  for (const gp of navigator.getGamepads()) {
    if (gp?.connected) { _gpIndex = gp.index; break; }
  }

  // Exposed to Python — called by _toggle_focus
  window.__cdGamepadStart = _gpStart;
  window.__cdGamepadStop = _gpStop;


