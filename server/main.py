"""
Crimson Desert — Player Position WebSocket Server
==================================================
Reads the player's real-time world position from CrimsonDesert.exe and
broadcasts it to connected clients via WebSocket (ws://localhost:7891).

Requirements:
  pip install pymem websockets
  Run as Administrator

Message format (JSON, sent every ~500ms while position is valid):
  { "lng": -0.72, "lat": 0.61, "x": -8432.1, "y": 12.4, "z": 3201.7, "realm": "pywel" }
"""

import asyncio
import ctypes
import ctypes.wintypes
import json
import logging
import os
import struct
import sys
import threading
import time

from server.memory import TeleportEngine
from server.memory.engine import (
    PROCESS_NAME, SAVE_DIR, HOOK_OFFSETS_FILE, HEIGHT_BOOST,
    _load_hook_offsets, _save_hook_offsets,
)
from shared.coord_math import (
    game_to_lnglat,
    lnglat_to_game,
    calibration_span as _calibration_span,
)

# Suprime erros de handshake inválido (ex: checagem TCP do launcher)
logging.getLogger('websockets').setLevel(logging.CRITICAL)

# ── Logging ──────────────────────────────────────────────────────────

log = logging.getLogger('cd_server')
log.setLevel(logging.DEBUG)

_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(logging.Formatter(
    '[%(asctime)s] %(levelname)-5s  %(message)s', datefmt='%H:%M:%S'))
_log_handler.setLevel(logging.DEBUG)
log.addHandler(_log_handler)

# Arquivo de log opcional via variável de ambiente CD_LOG_FILE
_log_file = os.environ.get('CD_LOG_FILE')
if _log_file:
    _file_handler = logging.FileHandler(_log_file, encoding='utf-8')
    _file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)-5s  %(message)s'))
    _file_handler.setLevel(logging.DEBUG)
    log.addHandler(_file_handler)

# ── Admin check ──────────────────────────────────────────────────────

def _is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

def assert_admin():
    """Verifica privilégios de admin. Exibe MessageBox e encerra se não for admin.
    Chamado explicitamente pelo ponto de entrada (overlay/launcher), não no import."""
    if not _is_admin():
        ctypes.windll.user32.MessageBoxW(
            0,
            "This program must be run as Administrator.\n\n"
            "Right-click and select 'Run as administrator'.",
            "CD Position Server", 0x10)
        sys.exit(1)

def _check_dependencies():
    """Verifica dependências obrigatórias. Exibe MessageBox se faltar alguma."""
    try:
        import pymem          # noqa: F401
        import pymem.process  # noqa: F401
    except ImportError:
        ctypes.windll.user32.MessageBoxW(
            0, "pymem is required.\n\nInstall with:\n  pip install pymem",
            "Missing Dependency", 0x10)
        sys.exit(1)

    try:
        import websockets         # noqa: F401
        import websockets.server  # noqa: F401
    except ImportError:
        ctypes.windll.user32.MessageBoxW(
            0, "websockets is required.\n\nInstall with:\n  pip install websockets",
            "Missing Dependency", 0x10)
        sys.exit(1)

# ── Constants (servidor) ──────────────────────────────────────────────
# TeleportEngine, PROCESS_NAME, SAVE_DIR, HEIGHT_BOOST importados de memory.engine

ABYSS_HEIGHT_THRESHOLD = 1400.0
INVULN_SECONDS = 10
WS_PORT        = 7891
BROADCAST_INTERVAL = 1/60   # seconds between position broadcasts (~60 Hz)
RETRY_INTERVAL     = 5     # seconds between attach/hook retries
POS_CHANGE_THRESHOLD = 0.01  # squared distance threshold to consider position changed
IDLE_BROADCAST_INTERVAL = 1.0  # seconds between broadcasts when position unchanged
CAMERA_HEADING_HEARTBEAT_INTERVAL = 1.0  # seconds between unchanged camera broadcasts

# ── Hotkeys globais ──────────────────────────────────────────────────────────
from server.hotkeys import (
    HOTKEY_SETTINGS_FILE, DEFAULT_HOTKEYS,
    VK_NAMES, VK_MOD_SHIFT, VK_MOD_CTRL, VK_MOD_ALT, MOD_NAMES,
    HOTKEY_MODIFIERS,
    XINPUT_GAMEPAD_DPAD_UP, XINPUT_GAMEPAD_DPAD_DOWN,
    XINPUT_GAMEPAD_DPAD_LEFT, XINPUT_GAMEPAD_DPAD_RIGHT,
    XINPUT_GAMEPAD_BACK, XINPUT_GAMEPAD_LEFT_SHOULDER,
    XINPUT_GAMEPAD_A, XINPUT_GAMEPAD_B,
    XINPUT_OPEN_NEARBY_MASK, XINPUT_NEARBY_INPUTS,
    XINPUT_WAYPOINT_INPUTS, XINPUT_FOCUS_TOGGLE_MASK,
    XINPUT_GAMEPAD, XINPUT_STATE,
    _load_xinput_get_state, _controller_buttons,
    _load_hotkey_settings, _save_hotkey_settings,
    _load_controller_hotkey_settings,
    _nearby_controls_enabled,
)

# ── Calibracao + Waypoints ───────────────────────────────────────────────────
from server.persistence import (
    CALIBRATION_FILES,
    _load_calibration, _save_calibration, _get_cal, _cal_cache,
    WAYPOINTS_FILE, _load_waypoints, _save_waypoints,
)

# ── WebSocket transport ───────────────────────────────────────────────────────
from server.ws_transport import (
    _clients, _client_locks, _client_options,
    _safe_send, _safe_send_many, _broadcast_all, _client_label,
    _broadcast_realtime, get_latest_realtime_frame,
)

_engine: TeleportEngine = None
_last_pos: dict = None          # última posição conhecida
_pre_teleport_pos: tuple = None # posição antes do último teleport (para abort)
_default_teleport_y: float = 1000.0  # Y padrão quando marcador não tem altura (atualizado pelo cliente)
ABYSS_DEFAULT_Y = 2400.0  # Y padrão para teleport no Abyss
_waypoints: list = _load_waypoints()

def _effective_marker_y(marker_y: float) -> float:
    """Retorna o Y efetivo para teleport ao marcador do mapa.
    Se o marcador tem Y ≈ 0 (sem dados de altura), usa _default_teleport_y
    ou ABYSS_DEFAULT_Y se o jogador está no Abyss."""
    if abs(marker_y) < 1.0:
        if _last_pos and _last_pos["y"] > ABYSS_HEIGHT_THRESHOLD:
            return ABYSS_DEFAULT_Y
        return _default_teleport_y
    return marker_y

async def _send_waypoints(websocket=None):
    """Envia lista de waypoints. Se websocket=None, broadcast para todos."""
    msg = json.dumps({"type": "waypoints", "data": _waypoints})
    if websocket:
        await _safe_send(websocket, msg)
    else:
        await _broadcast_all(msg)

async def _handle_client(websocket):
    global _waypoints, _pre_teleport_pos, _default_teleport_y
    port = websocket.local_address[1] if websocket.local_address else '?'
    log.info("Client connected from %s on port %s (%d total)",
             websocket.remote_address, port, len(_clients) + 1)
    _clients.add(websocket)
    _client_locks[websocket] = asyncio.Lock()
    _client_options[websocket] = {}
    log.info("Client connected  (%d total)", len(_clients))

    # Enviar waypoints salvos ao conectar
    await _send_waypoints(websocket)

    try:
        async for raw in websocket:
            try:
                cmd = json.loads(raw)
            except Exception:
                continue

            action = cmd.get("cmd")

            if action == "client_options":
                opts = _client_options.setdefault(websocket, {})
                opts["realtime_bundle"] = bool(cmd.get("realtimeBundle"))
                opts["native_realtime"] = bool(cmd.get("nativeRealtime"))
                client_name = cmd.get("clientName")
                if isinstance(client_name, str) and client_name.strip():
                    opts["client_name"] = client_name.strip()[:32]

            elif action == "teleport":
                # Teleport para coordenadas absolutas (de waypoint ou comunidade)
                x, y, z = cmd.get("x"), cmd.get("y"), cmd.get("z")
                if None not in (x, y, z) and _engine and _engine.hooks_installed:
                    ax, ay, az = float(x), float(y) + HEIGHT_BOOST, float(z)
                    if _last_pos:
                        _pre_teleport_pos = (_last_pos["x"], _last_pos["y"], _last_pos["z"])
                    ok, err = _engine.teleport_to_abs(ax, ay, az)
                    if ok:
                        _engine.set_invuln(True)
                        loop = asyncio.get_event_loop()
                        loop.call_later(INVULN_SECONDS, _engine.set_invuln, False)
                    log.info("Teleport → (%.1f, %.1f, %.1f) — %s", ax, ay, az, 'ok' if ok else err)

            elif action == "teleport_map":
                # Teleport para um ponto clicado no MapGenie.
                # Y é absoluto/configurável no cliente; não recebe HEIGHT_BOOST.
                lng, lat, y = cmd.get("lng"), cmd.get("lat"), cmd.get("y")
                realm = cmd.get("realm") or (_last_pos["realm"] if _last_pos else "pywel")
                ok, err = False, "Dados inválidos para teleport pelo mapa"
                if None not in (lng, lat, y) and realm in CALIBRATION_FILES:
                    # Atualiza o Y padrão para uso pelo teleport_marker/hotkey
                    _default_teleport_y = float(y)
                    cal = _get_cal(realm)
                    game_pos = lnglat_to_game(float(lng), float(lat), cal)
                    err = "Falha ao converter coordenadas do mapa"
                    if not (_engine and _engine.hooks_installed):
                        err = "Teleport hook não instalado"
                    elif game_pos:
                        ax, az = game_pos
                        ay = float(y)
                        if _last_pos:
                            _pre_teleport_pos = (_last_pos["x"], _last_pos["y"], _last_pos["z"])
                        ok, err = _engine.teleport_to_abs(ax, ay, az)
                        if ok:
                            _engine.set_invuln(True)
                            loop = asyncio.get_event_loop()
                            loop.call_later(INVULN_SECONDS, _engine.set_invuln, False)
                        log.info("Map teleport → (%.1f, %.1f, %.1f) realm=%s — %s", ax, ay, az, realm, 'ok' if ok else err)
                await _safe_send(websocket, json.dumps({
                    "type": "teleport_map_result",
                    "ok": ok,
                    "err": err,
                }))

            elif action == "abort":
                # Retornar à posição antes do último teleport
                if _pre_teleport_pos and _engine and _engine.hooks_installed:
                    ax, ay, az = _pre_teleport_pos
                    ok, err = _engine.teleport_to_abs(ax, ay, az)
                    if ok:
                        _engine.set_invuln(True)
                        loop = asyncio.get_event_loop()
                        loop.call_later(INVULN_SECONDS, _engine.set_invuln, False)
                    log.info("Abort → (%.1f, %.1f, %.1f) — %s", ax, ay, az, 'ok' if ok else err)
                    _pre_teleport_pos = None

            elif action == "teleport_marker":
                # Teleport para o marcador do mapa in-game
                if _engine and _engine.hooks_installed:
                    dest = _engine.get_map_dest()
                    if dest:
                        ax = dest[0]
                        ay = _effective_marker_y(dest[1]) + HEIGHT_BOOST
                        az = dest[2]
                        if _last_pos:
                            _pre_teleport_pos = (_last_pos["x"], _last_pos["y"], _last_pos["z"])
                        ok, err = _engine.teleport_to_abs(ax, ay, az)
                        if ok:
                            _engine.set_invuln(True)
                            loop = asyncio.get_event_loop()
                            loop.call_later(INVULN_SECONDS, _engine.set_invuln, False)
                        result = json.dumps({"type": "teleport_marker_result", "ok": ok, "err": err})
                    else:
                        result = json.dumps({"type": "teleport_marker_result",
                                             "ok": False, "err": "Nenhum marcador definido no mapa"})
                    await _safe_send(websocket, result)

            elif action == "save_waypoint":
                if _last_pos:
                    name = cmd.get("name", "Sem nome").strip() or "Sem nome"
                    wp = {
                        "name": name,
                        "absX": round(_last_pos["x"], 2),
                        "absY": round(_last_pos["y"], 2),
                        "absZ": round(_last_pos["z"], 2),
                        "realm": _last_pos["realm"],
                    }
                    _waypoints.append(wp)
                    _save_waypoints(_waypoints)
                    log.info("Waypoint salvo: %s", name)
                    await _send_waypoints()

            elif action == "delete_waypoint":
                idx = cmd.get("index")
                if idx is not None and 0 <= int(idx) < len(_waypoints):
                    removed = _waypoints.pop(int(idx))
                    _save_waypoints(_waypoints)
                    log.info("Waypoint removido: %s", removed['name'])
                    await _send_waypoints()

            elif action == "rename_waypoint":
                idx = cmd.get("index")
                name = cmd.get("name", "").strip()
                if idx is not None and name and 0 <= int(idx) < len(_waypoints):
                    _waypoints[int(idx)]["name"] = name
                    _save_waypoints(_waypoints)
                    await _send_waypoints()

            elif action == "get_waypoints":
                await _send_waypoints(websocket)

            elif action == "waypoints_state":
                set_waypoints_panel_open(bool(cmd.get("open", False)))
                if cmd.get("open") and _waypoints_open_cb:
                    _waypoints_open_cb()

            elif action == "add_calibration":
                lng = cmd.get("lng")
                lat = cmd.get("lat")
                realm = cmd.get("realm", "pywel")
                if None not in (lng, lat) and _last_pos and realm in CALIBRATION_FILES:
                    cal = _get_cal(realm)
                    cal.append({"game": [_last_pos["x"], _last_pos["z"]],
                                "map":  [float(lng), float(lat)]})
                    _save_calibration(realm, cal)
                    log.info("Calibration point added (%s): game=(%.1f,%.1f) map=(%.6f,%.6f)",
                             realm, _last_pos['x'], _last_pos['z'], lng, lat)
                    await _safe_send(websocket, json.dumps(
                        {"type": "calibration_result", "ok": True, "count": len(cal)}))

            elif action == "reset_calibration":
                realm = cmd.get("realm", "pywel")
                if realm in CALIBRATION_FILES:
                    try:
                        os.remove(CALIBRATION_FILES[realm])
                    except Exception:
                        pass
                    _cal_cache.pop(realm, None)
                    log.info("Calibration reset: %s", realm)
                    await _safe_send(websocket, json.dumps(
                        {"type": "calibration_result", "ok": True, "count": 0, "reset": True}))

            elif action == "move":
                # Injeta um delta de posição único via physics hook (flag=2)
                # {cmd:"move", dx:0, dy:0, dz:0}  — coordenadas relativas de jogo
                dx = float(cmd.get("dx", 0.0))
                dy = float(cmd.get("dy", 0.0))
                dz = float(cmd.get("dz", 0.0))
                if _engine and _engine.tp and _engine.hook_e:
                    try:
                        data = struct.pack('<ffffI', dx, dy, dz, 0.0, 2)
                        _engine.pm.write_bytes(_engine.tp, data, len(data))
                    except Exception as e:
                        log.warning("Move error: %s", e)

            elif action == "location_toggle":
                location_id = cmd.get("locationId")
                found = cmd.get("found")
                source_client_id = cmd.get("sourceClientId")
                if location_id is not None and found is not None:
                    bcast = json.dumps({
                        "type": "location_toggle",
                        "locationId": str(location_id),
                        "found": bool(found),
                        "sourceClientId": source_client_id,
                    })
                    for client in set(_clients):
                        if client is not websocket:
                            await _safe_send(client, bcast)
                    log.info("Location toggle: id=%s found=%s", location_id, found)

            elif action == "pan_location":
                location_id = cmd.get("locationId")
                if location_id is not None:
                    await _broadcast_all(json.dumps({
                        "type": "pan_location",
                        "locationId": str(location_id),
                    }))

    except Exception:
        pass
    finally:
        _clients.discard(websocket)
        _client_locks.pop(websocket, None)
        _client_options.pop(websocket, None)
        log.info("Client disconnected  (%d total)", len(_clients))

async def _broadcast_status(status: str):
    """Envia status do engine para todos os clientes."""
    payload = {"type": "engine_status", "status": status}
    if _engine:
        payload["teleportEnabled"] = _engine.teleport_enabled
    await _broadcast_all(json.dumps(payload))

# ── Hotkey polling thread ────────────────────────────────────────────

_hotkey_loop = None  # referência ao asyncio loop para schedule de ações
_keyboard_hotkey_paused = False  # True enquanto usuário edita atalho de teclado
_nearby_hotkey_paused = False  # True enquanto usuário edita o atalho nas Settings
_controller_hotkey_paused = False  # True enquanto usuário grava combo de controle nas Settings
_nearby_popup_hwnd = None
_waypoints_panel_open = False
_waypoints_open_cb  = None
_waypoints_close_cb = None

def set_waypoints_focus_callbacks(on_open, on_close):
    global _waypoints_open_cb, _waypoints_close_cb
    _waypoints_open_cb  = on_open
    _waypoints_close_cb = on_close

def set_waypoints_panel_open(open_: bool):
    global _waypoints_panel_open
    _waypoints_panel_open = open_
    if not open_ and _waypoints_close_cb:
        # Delay para que o botão B do controller seja solto antes de devolver
        # foco ao jogo — evita que o jogo processe o B residual.
        loop = _hotkey_loop
        if loop:
            def _delayed_focus():
                # Só devolve foco se o painel continua fechado
                if not _waypoints_panel_open:
                    _waypoints_close_cb()
            loop.call_later(0.2, _delayed_focus)
        else:
            _waypoints_close_cb()

def set_nearby_hotkey_paused(paused: bool):
    global _nearby_hotkey_paused
    _nearby_hotkey_paused = paused

def set_keyboard_hotkey_paused(paused: bool):
    global _keyboard_hotkey_paused
    _keyboard_hotkey_paused = paused

def set_controller_hotkey_paused(paused: bool):
    global _controller_hotkey_paused
    _controller_hotkey_paused = paused

def set_nearby_popup_hwnd(hwnd):
    """Registra a janela do nearby popup para filtrar inputs globais do controle."""
    global _nearby_popup_hwnd
    _nearby_popup_hwnd = int(hwnd) if hwnd else None

def _nearby_popup_is_foreground():
    hwnd = _nearby_popup_hwnd
    if not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]
        user32.GetAncestor.restype = ctypes.wintypes.HWND
        fg_hwnd = user32.GetForegroundWindow()
        root_hwnd = user32.GetAncestor(fg_hwnd, 2)  # GA_ROOT
        return int(fg_hwnd) == hwnd or int(root_hwnd) == hwnd
    except Exception:
        return False

_waypoints_popup_hwnd = None

def set_waypoints_popup_hwnd(hwnd):
    global _waypoints_popup_hwnd
    _waypoints_popup_hwnd = int(hwnd) if hwnd else None

def _waypoints_popup_is_foreground():
    hwnd = _waypoints_popup_hwnd
    if not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]
        user32.GetAncestor.restype = ctypes.wintypes.HWND
        fg_hwnd = user32.GetForegroundWindow()
        root_hwnd = user32.GetAncestor(fg_hwnd, 2)
        return int(fg_hwnd) == hwnd or int(root_hwnd) == hwnd
    except Exception:
        return False

def _send_key(vk):
    user32 = ctypes.windll.user32
    user32.keybd_event.argtypes = [
        ctypes.wintypes.BYTE, ctypes.wintypes.BYTE,
        ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.ULONG),
    ]
    user32.keybd_event(vk, 0, 0, None)
    user32.keybd_event(vk, 0, 0x0002, None)

def _send_key_return():
    _send_key(0x0D)  # VK_RETURN

def _send_key_escape():
    _send_key(0x1B)  # VK_ESCAPE

def _hotkey_thread():
    """Thread que faz polling de hotkeys globais via GetAsyncKeyState."""
    import threading
    get_key = ctypes.windll.user32.GetAsyncKeyState
    get_xinput_state = _load_xinput_get_state()
    hotkeys = _load_hotkey_settings()
    controller_hotkeys = _load_controller_hotkey_settings()
    key_state = {}
    controller_open_nearby_pressed = False
    controller_button_state = {}
    controller_open_waypoints_pressed = False
    controller_wp_button_state = {}
    controller_focus_toggle_pressed = False

    def _display(hk):
        vk = hk["vk"]
        mods = hk.get("mods") or ([hk["mod"]] if hk.get("mod") else [])
        name = VK_NAMES.get(vk, f"0x{vk:02X}")
        if mods:
            return "+".join(MOD_NAMES.get(m, f"0x{m:02X}") for m in mods) + "+" + name
        return name

    for hk_id, cfg in hotkeys.items():
        if cfg["enabled"]:
            log.info("Hotkey: %s → %s", _display(cfg), hk_id)

    if get_xinput_state:
        log.info("Controller hotkey: LB+Down -> open_nearby")

    while True:
        time.sleep(0.05)  # 50ms polling

        if not _engine or not _engine.attached or not _engine.hooks_installed:
            continue

        nearby_enabled = _nearby_controls_enabled()
        controller_buttons = _controller_buttons(get_xinput_state)
        if nearby_enabled:
            open_nb_mask = controller_hotkeys.get("open_nearby", XINPUT_OPEN_NEARBY_MASK)
            controller_pressed = open_nb_mask and bool((controller_buttons & open_nb_mask) == open_nb_mask)
            if controller_pressed and not controller_open_nearby_pressed and _hotkey_loop and not _nearby_hotkey_paused:
                _hotkey_loop.call_soon_threadsafe(
                    asyncio.ensure_future, _hotkey_open_nearby())
            controller_open_nearby_pressed = controller_pressed

            for button_mask, action in XINPUT_NEARBY_INPUTS.items():
                pressed = bool(controller_buttons & button_mask)
                was_pressed = controller_button_state.get(button_mask, False)
                controller_button_state[button_mask] = pressed
                if not pressed or was_pressed:
                    continue
                # Nao processar botoes que fazem parte do combo de abertura
                if button_mask & open_nb_mask and (controller_buttons & open_nb_mask) == open_nb_mask:
                    continue
                if not _nearby_popup_is_foreground():
                    continue
                if _hotkey_loop:
                    _hotkey_loop.call_soon_threadsafe(
                        asyncio.ensure_future, _hotkey_nearby_input(action))
        else:
            controller_open_nearby_pressed = False
            controller_button_state.clear()

        # -- Controle Xbox - waypoints
        open_wp_mask = controller_hotkeys.get("open_waypoints", 0x1002)
        wp_pressed = open_wp_mask and bool((controller_buttons & open_wp_mask) == open_wp_mask)
        if wp_pressed and not controller_open_waypoints_pressed and _hotkey_loop and not _controller_hotkey_paused:
            _hotkey_loop.call_soon_threadsafe(
                asyncio.ensure_future, _hotkey_open_waypoints())
        controller_open_waypoints_pressed = wp_pressed

        if _waypoints_panel_open and not _controller_hotkey_paused:
            for button_mask, action in XINPUT_WAYPOINT_INPUTS.items():
                pressed = bool(controller_buttons & button_mask)
                was_pressed = controller_wp_button_state.get(button_mask, False)
                controller_wp_button_state[button_mask] = pressed
                if not pressed or was_pressed:
                    continue
                # Nao processar botoes que fazem parte do combo de abertura
                if button_mask & open_wp_mask and (controller_buttons & open_wp_mask) == open_wp_mask:
                    continue
                # Popup nao em foco: provavelmente prompt aberto — A confirma, B cancela
                if not _waypoints_popup_is_foreground():
                    if button_mask == XINPUT_GAMEPAD_A:
                        _send_key_return()
                    elif button_mask == XINPUT_GAMEPAD_B:
                        _send_key_escape()
                    continue
                if _hotkey_loop:
                    _hotkey_loop.call_soon_threadsafe(
                        asyncio.ensure_future, _hotkey_waypoint_input(action))
        else:
            controller_wp_button_state.clear()

        # -- Controle Xbox - focus toggle
        focus_mask = controller_hotkeys.get("focus_toggle", XINPUT_FOCUS_TOGGLE_MASK)
        focus_pressed = focus_mask and bool((controller_buttons & focus_mask) == focus_mask)
        if focus_pressed and not controller_focus_toggle_pressed and _hotkey_loop and not _controller_hotkey_paused:
            _hotkey_loop.call_soon_threadsafe(
                asyncio.ensure_future, _hotkey_focus_toggle())
        controller_focus_toggle_pressed = focus_pressed

        for hk_id, cfg in hotkeys.items():
            if _keyboard_hotkey_paused:
                key_state[hk_id] = False
                continue
            if not cfg["enabled"]:
                key_state[hk_id] = False
                continue
            if hk_id == "open_nearby" and (not nearby_enabled or _nearby_hotkey_paused):
                key_state[hk_id] = False
                continue
            if hk_id == "open_waypoints" and _nearby_hotkey_paused:
                key_state[hk_id] = False
                continue

            vk = cfg["vk"]
            mods = cfg.get("mods") or ([cfg["mod"]] if cfg.get("mod") else [])

            key_down = bool(get_key(vk) & 0x8000)
            down_mods = {
                m for m in HOTKEY_MODIFIERS
                if get_key(m) & 0x8000
            }
            mod_down = down_mods == set(mods)

            pressed = key_down and mod_down
            was_pressed = key_state.get(hk_id, False)

            if pressed and not was_pressed:
                if _hotkey_loop:
                    if hk_id == "teleport_marker":
                        _hotkey_loop.call_soon_threadsafe(
                            asyncio.ensure_future, _hotkey_teleport_marker())
                    elif hk_id == "abort":
                        _hotkey_loop.call_soon_threadsafe(
                            asyncio.ensure_future, _hotkey_abort())
                    elif hk_id == "open_nearby":
                        _hotkey_loop.call_soon_threadsafe(
                            asyncio.ensure_future, _hotkey_open_nearby())
                    elif hk_id == "open_waypoints":
                        _hotkey_loop.call_soon_threadsafe(
                            asyncio.ensure_future, _hotkey_open_waypoints())
                    elif hk_id == "focus_toggle":
                        _hotkey_loop.call_soon_threadsafe(
                            asyncio.ensure_future, _hotkey_focus_toggle())

            key_state[hk_id] = pressed

async def _hotkey_teleport_marker():
    """Teleporta para o marcador do mapa in-game (hotkey)."""
    global _pre_teleport_pos
    if not _engine or not _engine.hooks_installed:
        return
    dest = _engine.get_map_dest()
    if not dest:
        log.info("Hotkey teleport_marker: nenhum marcador no mapa")
        return
    ax = dest[0]
    ay = _effective_marker_y(dest[1]) + HEIGHT_BOOST
    az = dest[2]
    if _last_pos:
        _pre_teleport_pos = (_last_pos["x"], _last_pos["y"], _last_pos["z"])
    ok, err = _engine.teleport_to_abs(ax, ay, az)
    if ok:
        _engine.set_invuln(True)
        loop = asyncio.get_event_loop()
        loop.call_later(INVULN_SECONDS, _engine.set_invuln, False)
    log.info("Hotkey teleport_marker → (%.1f, %.1f, %.1f) — %s", ax, ay, az, 'ok' if ok else err)

async def _hotkey_abort():
    """Retorna à posição pré-teleport (hotkey)."""
    global _pre_teleport_pos
    if not _engine or not _engine.hooks_installed:
        return
    if not _pre_teleport_pos:
        log.info("Hotkey abort: sem posição de retorno")
        return
    ax, ay, az = _pre_teleport_pos
    ok, err = _engine.teleport_to_abs(ax, ay, az)
    if ok:
        _engine.set_invuln(True)
        loop = asyncio.get_event_loop()
        loop.call_later(INVULN_SECONDS, _engine.set_invuln, False)
    log.info("Hotkey abort → (%.1f, %.1f, %.1f) — %s", ax, ay, az, 'ok' if ok else err)
    _pre_teleport_pos = None

async def _hotkey_open_nearby():
    """Opens the nearby locations popup in the overlay (hotkey)."""
    await _broadcast_all(json.dumps({"type": "open_nearby"}))

async def _hotkey_nearby_input(action: str):
    """Sends navigation command from the controller to the nearby popup.."""
    await _broadcast_all(json.dumps({"type": "nearby_input", "action": action}))

async def _hotkey_open_waypoints():
    """Toggle the waypoints panel (hotkey)."""
    await _broadcast_all(json.dumps({"type": "open_waypoints"}))

async def _hotkey_waypoint_input(action: str):
    """Sends navigation command from the controller to the waypoints panel."""
    await _broadcast_all(json.dumps({"type": "waypoint_input_wp", "action": action}))

_focus_toggle_cb = None

def set_focus_toggle_callback(cb):
    global _focus_toggle_cb
    _focus_toggle_cb = cb

async def _hotkey_focus_toggle():
    """Toggles focus between the game and the overlay."""
    if _focus_toggle_cb:
        _focus_toggle_cb()

async def _broadcast_loop():
    global _engine, _last_pos
    teleport_enabled = os.environ.get('CD_TELEPORT_ENABLED', '1') != '0'
    use_shared_memory_entity = os.environ.get('CD_USE_SHARED_MEMORY_ENTITY', '1') != '0'
    _engine = TeleportEngine(teleport_enabled=teleport_enabled,
                              use_shared_memory_entity=use_shared_memory_entity)
    if not teleport_enabled:
        log.info("Teleport DISABLED — hooks hook_e and hook_c will not be injected")
    _last_broadcast_time: float = 0.0
    _last_entity_refresh: float = 0.0
    _last_map_marker_event = None
    _last_client_count = 0
    _last_camera_heading = None
    _last_camera_broadcast_time: float = 0.0
    _was_attached = False

    def _camera_heading_event(now):
        nonlocal _last_camera_heading, _last_camera_broadcast_time
        if not (_engine and _engine.hook_cam):
            return None
        cam_heading, cam_raw = _engine.get_camera_heading()
        if cam_heading is None:
            return None
        heading = round(cam_heading, 1)
        if (
            heading == _last_camera_heading and
            now - _last_camera_broadcast_time < CAMERA_HEADING_HEARTBEAT_INTERVAL
        ):
            return None
        _last_camera_heading = heading
        _last_camera_broadcast_time = now
        return {
            "type": "camera_heading",
            "heading": heading,
            "raw": round(cam_raw, 4),
        }

    while True:
        # Detecta o jogo fechado: o handle ainda esta "attached" mas o processo
        # morreu. As leituras retornam None sem lancar, entao sem este check o
        # engine nunca soltaria o handle morto para re-anexar ao novo processo.
        if _engine.attached and not _engine.is_process_alive():
            _engine.detach()

        # Attach if needed
        if not _engine.attached:
            if _was_attached:
                log.warning("Game disconnected — waiting for re-attach...")
                _was_attached = False
                await _broadcast_status("disconnected")
            try:
                _engine.attach()
                log.info("Attached to CrimsonDesert.exe")
                _was_attached = True
                await _broadcast_status("attached")
            except Exception as e:
                log.warning("Cannot attach: %s — retrying in %ds", e, RETRY_INTERVAL)
                await asyncio.sleep(RETRY_INTERVAL)
                continue

        # Install hooks if needed
        if not _engine.hooks_installed:
            try:
                _engine.scan_and_hook()
                log.info("Hooks installed, reading position...")
                await _broadcast_status("attached")
            except Exception as e:
                log.error("Hook failed: %s — retrying in %ds", e, RETRY_INTERVAL)
                _engine.detach()
                await asyncio.sleep(RETRY_INTERVAL)
                continue

        # Read position — se falhar, desconecta e tenta re-attach no próximo ciclo
        try:
            apos = _engine.get_player_abs()
        except Exception as e:
            log.warning("Read error: %s — re-attaching", e)
            try:
                _engine.detach()
            except Exception:
                pass
            continue

        realtime_events = []
        now = asyncio.get_event_loop().time()

        # Refresh entity base via Freedom Flyer shared memory a cada 5s
        if now - _last_entity_refresh >= 5.0:
            _last_entity_refresh = now
            _engine.refresh_entity_base()

        # Broadcast to connected clients; throttle to 1s when position unchanged
        if apos and _clients:
            x, y, z = apos
            realm = "abyss" if y > ABYSS_HEIGHT_THRESHOLD else "pywel"
            if _last_pos and realm == _last_pos["realm"]:
                dx = x - _last_pos["x"]
                dz = z - _last_pos["z"]
                dy = y - _last_pos["y"]
                if dx*dx + dz*dz + dy*dy < POS_CHANGE_THRESHOLD and now - _last_broadcast_time < IDLE_BROADCAST_INTERVAL:
                    cam_event = _camera_heading_event(now)
                    if cam_event:
                        await _broadcast_realtime([cam_event])
                    await asyncio.sleep(BROADCAST_INTERVAL)
                    continue
            _last_broadcast_time = now
            cal = _get_cal(realm)
            lng, lat = game_to_lnglat(x, z, cal)
            heading = _engine.get_player_heading()
            _last_pos = {"x": x, "y": y, "z": z, "realm": realm}
            payload = {
                "type": "position",
                "lng": round(lng, 8),
                "lat": round(lat, 8),
                "x": round(x, 2),
                "y": round(y, 2),
                "z": round(z, 2),
                "realm": realm,
            }
            if heading is not None:
                payload["heading"] = round(heading, 1)
            realtime_events.append(payload)

        # Broadcast camera heading (independente da posição)
        if _clients and _engine and _engine.hooks_installed:
            cam_event = _camera_heading_event(now)
            if cam_event:
                realtime_events.append(cam_event)

        # Broadcast map marker position
        if _clients and _engine and _engine.hooks_installed:
            force_map_marker = len(_clients) != _last_client_count
            dest = _engine.get_map_dest()
            if dest:
                realm = "abyss" if dest[1] > ABYSS_HEIGHT_THRESHOLD else "pywel"
                cal = _get_cal(realm)
                lng, lat = game_to_lnglat(dest[0], dest[2], cal)
                marker_event = {
                    "type": "map_marker",
                    "lng": round(lng, 8),
                    "lat": round(lat, 8),
                    "x": round(dest[0], 2),
                    "y": round(dest[1], 2),
                    "z": round(dest[2], 2),
                }
            else:
                marker_event = {"type": "map_marker_cleared"}
            if force_map_marker or marker_event != _last_map_marker_event:
                realtime_events.append(marker_event)
                _last_map_marker_event = marker_event
            _last_client_count = len(_clients)

        await _broadcast_realtime(realtime_events)
        await asyncio.sleep(BROADCAST_INTERVAL)

WSS_PORT = 7892

async def _main(ready_event=None):
    import ssl as _ssl, os, contextlib, websockets, websockets.server
    _dir  = os.path.dirname(os.path.abspath(__file__))
    _cert = os.path.join(_dir, 'certs', 'server.crt')
    _key  = os.path.join(_dir, 'certs', 'server.key')

    ssl_ctx = None
    if os.path.exists(_cert) and os.path.exists(_key):
        ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(_cert, _key)
        ssl_ctx.set_ciphers('DEFAULT')
        logging.getLogger('websockets').setLevel(logging.DEBUG)

    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(
            websockets.serve(_handle_client, "0.0.0.0", WS_PORT, origins=None))
        log.info("WS  → ws://0.0.0.0:%d  (Chrome / overlay)", WS_PORT)

        if ssl_ctx:
            await stack.enter_async_context(
                websockets.serve(_handle_client, "0.0.0.0", WSS_PORT, origins=None,
                                 ssl=ssl_ctx))
            log.info("WSS → wss://0.0.0.0:%d (Firefox Android)", WSS_PORT)
        else:
            log.warning("server.crt não encontrado para habilitar WSS")

        # Inicia thread de hotkeys globais
        global _hotkey_loop
        _hotkey_loop = asyncio.get_event_loop()
        import threading
        _hk_thread = threading.Thread(target=_hotkey_thread, daemon=True)
        _hk_thread.start()

        if ready_event is not None:
            ready_event.set()

        await _broadcast_loop()

import atexit

def _cleanup():
    global _engine
    if _engine:
        try:
            _engine.detach()
        except Exception:
            pass

atexit.register(_cleanup)

if __name__ == "__main__":
    assert_admin()
    _check_dependencies()
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("Shutting down")
