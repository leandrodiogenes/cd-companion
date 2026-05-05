"""
Crimson Desert Map Overlay
==========================
Always-on-top MapGenie window que segue a posição do jogador em tempo real.
O servidor WebSocket roda como thread daemon dentro deste mesmo processo.

Atalho padrão: Ctrl+Shift+M  →  mostrar/ocultar a janela

Uso:
  python -m overlay.main

Dependências:
  pip install PyQt5 PyQtWebEngine pymem websockets
"""

import asyncio
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import threading
from PyQt5.QtCore import Qt, QUrl, QTimer, pyqtSignal, QObject
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QCursor
from PyQt5.QtGui import QBitmap, QPainter, QColor, QPen
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget,
                             QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QCheckBox, QPushButton, QSlider)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

from overlay.config_defaults import SETTING_DEFAULTS
from overlay.mapgenie_patch import (
    install_mapgenie_patch,
    register_mapgenie_patch_scheme,
)

# ── Configuração ─────────────────────────────────────────────────────

# CD_APP_DIR é definido pelo launcher para apontar ao dir do exe.
# Fallback: diretório do próprio script.
_APP_DIR = os.environ.get(
    'CD_APP_DIR',
    os.path.dirname(os.path.abspath(__file__))
)
CONFIG_FILE = os.path.join(_APP_DIR, 'overlay_config.json')
DEFAULT_URL = 'https://mapgenie.io/crimson-desert/maps/pywel'
DEFAULT_W   = 520
DEFAULT_H   = 420
WS_URL      = 'ws://localhost:7891'

# Hotkey global: Ctrl+Shift+M
HOTKEY_ID   = 1
MOD_CONTROL = 0x0002
MOD_SHIFT   = 0x0004
MOD_ALT     = 0x0001
HOTKEY_VK   = ord('M')

# Hotkey dev-only: Ctrl+Shift+Alt+R (restart overlay + server)
if not getattr(sys, 'frozen', False):
    RESTART_HOTKEY_ID = 2
    RESTART_HOTKEY_VK = ord('R')

# SETTING_DEFAULTS importado de overlay_config_defaults.py

# ── Config ────────────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(cfg):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

def get_screen_size():
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

def find_game_window_rect():
    """Retorna RECT da janela principal do CrimsonDesert.exe, ou None."""
    user32   = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    found    = [None]
    pid      = ctypes.wintypes.DWORD()

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool,
                                   ctypes.wintypes.HWND,
                                   ctypes.wintypes.LPARAM)
    @EnumProc
    def _cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hproc = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not hproc:
            return True
        buf  = ctypes.create_unicode_buffer(260)
        size = ctypes.wintypes.DWORD(260)
        kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size))
        kernel32.CloseHandle(hproc)
        if buf.value.lower().endswith('crimsondesert.exe'):
            rc = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            if rc.right - rc.left > 200:   # ignora splash/janelas minúsculas
                found[0] = rc
                return False
        return True

    user32.EnumWindows(_cb, 0)
    return found[0]

# ── JS injetado no MapGenie ───────────────────────────────────────────
# Extraído para overlay_inject.js — lido em runtime com Template substitution.
# Se overlay/inject_parts/ existir com todos os 13 fragmentos, usa os fragmentos
# concatenados em ordem. Caso contrário, lê inject.js normalmente (fallback).

from string import Template as _Template

_INJECT_PARTS = [
    '00_bootstrap.js', '01_state.js', '02_tooltip.js', '03_marker.js',
    '04_arrow.js', '05_panel.js', '06_teleport.js', '07_location_sync.js',
    '08_nearby.js', '09_websocket.js', '10_waypoints.js', '11_layout.js',
    '11b_gamepad.js', '12_init.js',
]

def _load_inject_js():
    _dir = os.path.dirname(os.path.abspath(__file__))
    parts_dir = os.path.join(_dir, 'inject_parts')
    if os.path.isdir(parts_dir) and all(
        os.path.isfile(os.path.join(parts_dir, p)) for p in _INJECT_PARTS
    ):
        parts = []
        for p in _INJECT_PARTS:
            with open(os.path.join(parts_dir, p), encoding='utf-8') as f:
                parts.append(f.read())
        raw = ''.join(parts)
    else:
        with open(os.path.join(_dir, 'inject.js'), encoding='utf-8') as f:
            raw = f.read()
    return _Template(raw).safe_substitute(WS_URL=WS_URL)

INJECT_JS = _load_inject_js()

# ── Widgets extraídos para overlay_widgets.py ────────────────────────
from overlay.widgets import (
    SettingsDialog, InterceptPage, LoginPrompt,
    HotkeySignals, TitleBar,
    focus_game_window,
)

try:
    from server.main import (
        set_waypoints_focus_callbacks as _set_waypoints_focus_callbacks,
        set_focus_toggle_callback as _set_focus_toggle_callback,
    )
except Exception:
    _set_waypoints_focus_callbacks = None
    _set_focus_toggle_callback = None

# ── Resize ring overlay (modo circular) ──────────────────────────────

class _ResizeRingOverlay(QWidget):
    """Anel visual que aparece ao passar o mouse na borda redimensionável."""

    def __init__(self, parent, border):
        super().__init__(parent)
        self._active = False
        self._border = border
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)

    def set_active(self, active):
        if active != self._active:
            self._active = active
            self.update()

    def paintEvent(self, _event):
        if not self._active:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        b = self._border
        w, h = self.width(), self.height()
        pen = QPen(QColor(160, 200, 255, 110))
        pen.setWidth(b)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        # pen é centralizado na borda do ellipse; inset por b//2 para não sair da máscara
        half = b // 2
        p.drawEllipse(half, half, w - b, h - b)
        p.end()


# ── Janela principal ──────────────────────────────────────────────────

class OverlayWindow(QMainWindow):
    def __init__(self, cfg, screen_w, screen_h):
        super().__init__()

        w   = cfg.get('width',  DEFAULT_W)
        h   = cfg.get('height', DEFAULT_H)
        if cfg.get('roundWindow', SETTING_DEFAULTS.get('roundWindow', False)):
            h = w
        url = cfg.get('url', DEFAULT_URL)

        # Tenta posicionar no canto esquerdo da janela do jogo, centralizado
        game = find_game_window_rect()
        if game is not None:
            gw = game.right  - game.left
            gh = game.bottom - game.top
            x  = game.left + 50
            y  = game.top + (gh - h) // 2
            print(f"[*] Game found at ({game.left},{game.top}) {gw}×{gh}"
                  f" → overlay at ({x},{y})")
        else:
            x = cfg.get('x', screen_w - w - 20)
            y = cfg.get('y', screen_h - h - 60)
            print("[*] Game window not found — using saved position")

        # Offset relativo para follow mode
        if game is not None:
            self._game_rel_x     = x - game.left
            self._game_rel_y     = y - game.top
            self._last_game_left = game.left
            self._last_game_top  = game.top
        else:
            self._game_rel_x     = 50
            self._game_rel_y     = 0
            self._last_game_left = None   # sinaliza: ainda não viu o jogo
            self._last_game_top  = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.resize(w, h)
        self.move(x, y)
        transp = cfg.get('transparency', SETTING_DEFAULTS['transparency'])
        self.setWindowOpacity(1.0 - transp / 100)
        self._round_window = cfg.get('roundWindow', SETTING_DEFAULTS['roundWindow'])
        self._always_show_bar = cfg.get('alwaysShowTitleBar', SETTING_DEFAULTS['alwaysShowTitleBar'])
        self._is_maximized = False
        self._normal_geometry = None

        # ── Widget raiz (sem layout — posicionamento manual) ───────────
        root = QWidget()
        root.setStyleSheet(
            'QWidget#root { border:1px solid #2d2d44; background:#0f0f1a;'
            ' border-radius:8px; }')
        root.setObjectName('root')
        self.setCentralWidget(root)

        # ── WebView preenche tudo ──────────────────────────────────────
        self._view = QWebEngineView(root)
        self._view.setGeometry(0, 0, w, h)

        # ── Barra flutuante sobre o WebView (oculta por padrão) ────────
        self._bar = TitleBar(root)
        self._bar.hide()
        self._bar_visible = False
        if self._always_show_bar and not self._round_window:
            self._bar.show()
            self._bar.raise_()
            self._bar_visible = True

        # ── Botões flutuantes individuais (modo circular) ──────────────
        self._create_float_btns(root)

        # ── Anel de redimensionamento (modo circular) ──────────────────
        self._resize_ring = _ResizeRingOverlay(root, self._BORDER)
        self._resize_ring.setGeometry(0, 0, w, h)
        self._resize_ring.raise_()

        # Aplica máscara e posiciona barra
        self._apply_mask()
        self._update_bar_geometry()

        # ── Conectar barra ─────────────────────────────────────────────
        self._bar.btn_back.clicked.connect(self._view.back)
        self._bar.btn_settings.clicked.connect(self._open_settings)
        self._bar.btn_hide.clicked.connect(self.hide)
        self._bar.btn_maximize.clicked.connect(self.toggle_maximize)
        self._bar.btn_close.clicked.connect(self.close)

        # Página customizada para interceptar cdcompanion://login-needed
        page = InterceptPage(self._view)
        page.login_needed.connect(self._on_login_needed)
        self._view.setPage(page)
        self._mapgenie_patch_refs = None
        if os.environ.get('CD_PATCH_MAPGENIE_MAP_JS', '1') != '0':
            self._mapgenie_patch_refs = install_mapgenie_patch(page.profile(), self)

        self._view.loadFinished.connect(self._on_load_finished)
        self._view.load(QUrl(url))
        self._native_realtime_enabled = (
            cfg.get('realtimeTransport', SETTING_DEFAULTS['realtimeTransport']) == 'native'
        )
        self._last_native_realtime_seq = None
        self._native_realtime_timer = QTimer(self)
        self._native_realtime_timer.setInterval(16)
        self._native_realtime_timer.timeout.connect(self._push_native_realtime)

        # ── Timer de hover para mostrar/ocultar a barra ────────────────
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(40)
        self._hover_timer.timeout.connect(self._check_bar_hover)
        self._hover_timer.start()

        # ── Timer de debounce para salvar tamanho ao redimensionar ─────
        self._resize_save_timer = QTimer(self)
        self._resize_save_timer.setSingleShot(True)
        self._resize_save_timer.setInterval(500)
        self._resize_save_timer.timeout.connect(self._save_size)

        # ── Timer de follow da janela do jogo ──────────────────────────
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(32)
        self._follow_timer.timeout.connect(self._update_follow_pos)
        if cfg.get('followGameWindow', SETTING_DEFAULTS['followGameWindow']):
            self._follow_timer.start()

        # ── Hotkey ─────────────────────────────────────────────────────
        self._signals = HotkeySignals()
        self._signals.toggle.connect(self._toggle_visible)
        if not getattr(sys, 'frozen', False):
            self._signals.restart.connect(self._do_dev_restart)
        threading.Thread(target=self._hotkey_thread, daemon=True).start()

        if _set_waypoints_focus_callbacks:
            _set_waypoints_focus_callbacks(
                self._activate_for_waypoints,
                focus_game_window,
            )

        if _set_focus_toggle_callback:
            _set_focus_toggle_callback(self._toggle_focus)

    # ── Rounded corners ────────────────────────────────────────────────
    CORNER_RADIUS = 8

    def _apply_mask(self):
        bm = QBitmap(self.size())
        bm.fill(Qt.color0)
        p = QPainter(bm)
        p.setBrush(Qt.color1)
        p.setPen(Qt.NoPen)
        if getattr(self, '_round_window', False):
            p.drawEllipse(0, 0, self.width(), self.height())
        elif getattr(self, '_is_maximized', False):
            p.drawRect(0, 0, self.width(), self.height())
        else:
            p.drawRoundedRect(0, 0, self.width(), self.height(),
                              self.CORNER_RADIUS, self.CORNER_RADIUS)
        p.end()
        self.setMask(bm)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self._view.setGeometry(0, 0, w, h)
        self._apply_mask()
        self._update_bar_geometry()
        self._update_float_btn_geometry()
        if hasattr(self, '_resize_ring'):
            self._resize_ring.setGeometry(0, 0, w, h)
            self._resize_ring.raise_()
        if hasattr(self, '_resize_save_timer'):
            self._resize_save_timer.start()

    def _save_size(self):
        if self._is_maximized:
            return
        cfg = load_config()
        w = self.width()
        cfg['width']  = w
        cfg['height'] = w if self._round_window else self.height()
        save_config(cfg)

    def toggle_maximize(self):
        if self._is_maximized:
            if self._normal_geometry:
                self.setGeometry(self._normal_geometry)
            self._is_maximized = False
            self._bar.set_maximized(False)
        else:
            self._normal_geometry = self.geometry()
            screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
            self.setGeometry(screen.availableGeometry())
            self._is_maximized = True
            self._bar.set_maximized(True)

    # ── Barra: geometria e zonas de hover ─────────────────────────────
    _BAR_SHOW_ZONE = TitleBar.HEIGHT
    _BAR_HIDE_ZONE = TitleBar.HEIGHT + 20

    def _update_bar_geometry(self):
        """Posiciona a barra: só usada em modo normal (circular usa botões flutuantes)."""
        if self._round_window:
            return   # botões flutuantes individuais cuidam do modo circular
        w = self.width()
        self._bar.setGeometry(0, 0, w, TitleBar.HEIGHT)
        self._bar.set_compact(False)

    def _bar_zones(self):
        if self._round_window:
            r = min(self.width(), self.height()) / 2
            bar_y = max(8, int(r * 0.15))
            show = bar_y + TitleBar.HEIGHT + 10
            hide = bar_y + TitleBar.HEIGHT + 25
            return show, hide
        return self._BAR_SHOW_ZONE, self._BAR_HIDE_ZONE

    def _check_bar_hover(self):
        gp   = QCursor.pos()
        rect = self.geometry()
        lx   = gp.x() - rect.left()
        ly   = gp.y() - rect.top()
        w    = rect.width()

        if self._round_window:
            h = rect.height()
            # Botões flutuantes individuais: mostra no canto superior direito
            near = (0 <= lx <= w and 0 <= ly <= 50)
            if not self._float_btns_visible and near:
                for btn in self._float_btns:
                    btn.show()
                    btn.raise_()
                self._float_btns_visible = True
            elif self._float_btns_visible and not near:
                for btn in self._float_btns:
                    btn.hide()
                self._float_btns_visible = False
            # Anel de redimensionamento: cursor na faixa de borda
            b = self._BORDER
            inside = 0 <= lx <= w and 0 <= ly <= h
            in_resize = inside and (lx < b or lx > w - b or ly < b or ly > h - b)
            if hasattr(self, '_resize_ring'):
                self._resize_ring.set_active(in_resize)
            return

        inside_w = 0 <= lx <= w
        show_zone, hide_zone = self._bar_zones()
        if not self._bar_visible:
            if inside_w and 0 <= ly <= show_zone:
                self._bar.show()
                self._bar.raise_()
                self._bar_visible = True
        else:
            if not self._always_show_bar and (not inside_w or ly > hide_zone or ly < 0):
                self._bar.hide()
                self._bar_visible = False

    # ── Follow game window ────────────────────────────────────────────

    def _update_follow_pos(self):
        game = find_game_window_rect()
        if game is None:
            return
        gl, gt = int(game.left), int(game.top)
        if self._last_game_left is None:
            self._game_rel_x     = self.x() - gl
            self._game_rel_y     = self.y() - gt
            self._last_game_left = gl
            self._last_game_top  = gt
            return
        if gl != self._last_game_left or gt != self._last_game_top:
            self.move(gl + self._game_rel_x, gt + self._game_rel_y)
            self._last_game_left = gl
            self._last_game_top  = gt
        else:
            self._game_rel_x = self.x() - gl
            self._game_rel_y = self.y() - gt

    # ── Botões flutuantes (modo circular) ─────────────────────────────

    def _create_float_btns(self, parent):
        def _style(hover_color):
            return (
                'QPushButton{background:rgba(12,12,18,.85);'
                'border:1px solid rgba(255,255,255,.12);border-radius:13px;'
                'color:#555;font:14px;}'
                f'QPushButton:hover{{background:rgba(30,30,50,.95);color:{hover_color};}}'
            )
        specs = [
            ('⚙', _style('#ffd060'), self._open_settings),
            ('–', _style('#60b4ff'), self.hide),
            ('✕', _style('#ff6060'), self.close),
        ]
        self._float_btns = []
        self._float_btns_visible = False
        for text, style, slot in specs:
            btn = QPushButton(text, parent)
            btn.setFixedSize(26, 26)
            btn.setStyleSheet(style)
            btn.clicked.connect(slot)
            btn.hide()
            self._float_btns.append(btn)
        self._update_float_btn_geometry()

    def _update_float_btn_geometry(self):
        if not hasattr(self, '_float_btns'):
            return
        w, h = self.width(), self.height()
        s     = 26   # tamanho de cada botão
        gap   = 4
        y     = 10
        total = len(self._float_btns) * s + (len(self._float_btns) - 1) * gap
        x0    = (w - total) // 2
        for i, btn in enumerate(self._float_btns):
            btn.move(x0 + i * (s + gap), y)

    # ── All-edge resize via WM_NCHITTEST ───────────────────────────────
    _BORDER = 6   # px from edge that counts as resize zone

    def nativeEvent(self, event_type, message):
        if event_type == b'windows_generic_MSG':
            msg = ctypes.cast(int(message),
                              ctypes.POINTER(ctypes.wintypes.MSG)).contents

            if msg.message == 0x0214 and self._round_window:   # WM_SIZING
                rp   = ctypes.cast(msg.lParam, ctypes.POINTER(ctypes.wintypes.RECT))
                w    = rp.contents.right  - rp.contents.left
                h    = rp.contents.bottom - rp.contents.top
                wp   = msg.wParam
                # A borda arrastada determina o tamanho alvo (permite reduzir)
                if wp in (1, 2):       # LEFT / RIGHT  → largura lidera
                    size = max(w, 120)
                elif wp in (3, 6):     # TOP  / BOTTOM → altura lidera
                    size = max(h, 120)
                else:                  # cantos (improvável em janela circular)
                    size = max(w, h, 120)
                # Aplica direto no ponteiro (não em cópia)
                if wp in (1, 4, 7):    # borda esquerda fixa a direita
                    rp.contents.left   = rp.contents.right  - size
                else:
                    rp.contents.right  = rp.contents.left   + size
                if wp in (3, 4, 5):    # borda superior fixa o bottom
                    rp.contents.top    = rp.contents.bottom - size
                else:
                    rp.contents.bottom = rp.contents.top    + size
                return True, 1

            if msg.message == 0x0084:   # WM_NCHITTEST
                mx = ctypes.c_int16(msg.lParam & 0xFFFF).value
                my = ctypes.c_int16((msg.lParam >> 16) & 0xFFFF).value
                rect = self.frameGeometry()
                lx = mx - rect.left()
                ly = my - rect.top()
                w, h = rect.width(), rect.height()
                b = self._BORDER
                L = lx < b
                R = lx > w - b
                T = ly < b
                B = ly > h - b
                if T and L: return True, 13   # HTTOPLEFT
                if T and R: return True, 14   # HTTOPRIGHT
                if B and L: return True, 16   # HTBOTTOMLEFT
                if B and R: return True, 17   # HTBOTTOMRIGHT
                if L:       return True, 10   # HTLEFT
                if R:       return True, 11   # HTRIGHT
                if T:       return True, 12   # HTTOP
                if B:       return True, 15   # HTBOTTOM
                # Ctrl + qualquer área → arrasta a janela
                if QApplication.keyboardModifiers() & Qt.ControlModifier:
                    return True, 2            # HTCAPTION
        return super().nativeEvent(event_type, message)

    def _on_login_needed(self):
        dlg = LoginPrompt(self)
        if dlg.exec_() == QDialog.Accepted:
            self._view.load(QUrl('https://mapgenie.io/crimson-desert/login'))

    def _open_settings(self):
        cfg = load_config()
        dlg = SettingsDialog(cfg, self)
        if dlg.exec_() == QDialog.Accepted:
            new_settings = dlg.get_settings()
            cfg.update(new_settings)
            os.environ['CD_NEARBY_CONTROLS_ENABLED'] = '1' if new_settings.get('nearbyControlsEnabled') else '0'
            was_round = self._round_window
            self._round_window = new_settings.get('roundWindow', False)
            self._always_show_bar = new_settings.get('alwaysShowTitleBar', False)
            if not self._round_window:
                if self._always_show_bar and not self._bar_visible:
                    self._bar.show()
                    self._bar.raise_()
                    self._bar_visible = True
                elif not self._always_show_bar and self._bar_visible:
                    self._bar.hide()
                    self._bar_visible = False
            if self._round_window and not was_round:
                # Indo para circular: salva geometria quadrada e restaura tamanho circular
                cfg['squareWidth'] = self.width()
                cfg['squareHeight'] = self.height()
                self._bar.hide()
                self._bar_visible = False
                round_size = cfg.get('roundWidth', 240)
                self.resize(round_size, round_size)
            else:
                self._apply_mask()
                self._update_bar_geometry()
            if not self._round_window and was_round:
                # Saindo do modo circular: salva tamanho circular e restaura quadrado
                cfg['roundWidth'] = self.width()
                square_w = cfg.get('squareWidth', DEFAULT_W)
                square_h = cfg.get('squareHeight', DEFAULT_H)
                self.resize(square_w, square_h)
                for btn in self._float_btns:
                    btn.hide()
                self._float_btns_visible = False
            if new_settings.get('followGameWindow'):
                # Calcula offset relativo a partir da posição atual do overlay
                game = find_game_window_rect()
                if game is not None:
                    self._game_rel_x     = self.x() - game.left
                    self._game_rel_y     = self.y() - game.top
                    self._last_game_left = game.left
                    self._last_game_top  = game.top
                self._follow_timer.start()
            else:
                self._follow_timer.stop()
            save_config(cfg)
            self._apply_settings_js(new_settings)
            # Aplica zoom do navegador
            zoom = new_settings.get('browserZoom', 100)
            self._view.setZoomFactor(zoom / 100.0)

    def _apply_settings_js(self, settings):
        # Só atualiza a variável — os auto-hides de painel só devem rodar
        # no carregamento da página (via _on_load_finished), não ao salvar.
        self._view.page().runJavaScript(
            f'window.__cdSettings = {json.dumps(settings)};'
            f'window.__cdApplyRoundLayout && window.__cdApplyRoundLayout(window.__cdSettings);'
            f'window.__cdApplyRotationSettings && window.__cdApplyRotationSettings(window.__cdSettings);'
            f'window.__cdUpdateMapIconSize && window.__cdUpdateMapIconSize();'
            f'window.__cdUpdateNearbyControls && window.__cdUpdateNearbyControls();'
            f'window.__cdUpdateTeleportVisibility && window.__cdUpdateTeleportVisibility();')

    def _on_load_finished(self, ok):
        if ok:
            cfg = load_config()
            settings = {k: cfg.get(k, v) for k, v in SETTING_DEFAULTS.items()}
            self._native_realtime_enabled = (
                settings.get('realtimeTransport') == 'native')
            self._last_native_realtime_seq = None
            zoom = settings.get('browserZoom', 100)
            self._view.setZoomFactor(zoom / 100.0)
            self._view.page().runJavaScript(
                f'window.__cdSettings = {json.dumps(settings)};'
                f'window.__cdNativeRealtimeEnabled = '
                f'{json.dumps(self._native_realtime_enabled)};')
            self._view.page().runJavaScript(INJECT_JS)
            if self._native_realtime_enabled:
                self._native_realtime_timer.start()
            else:
                self._native_realtime_timer.stop()

    def _push_native_realtime(self):
        if not self._native_realtime_enabled:
            return
        try:
            from server.main import get_latest_realtime_frame
            frame = get_latest_realtime_frame()
        except Exception:
            return
        if not frame:
            return
        seq = frame.get("seq")
        if seq == self._last_native_realtime_seq:
            return
        self._last_native_realtime_seq = seq
        payload = json.dumps(frame, separators=(',', ':'))
        self._view.page().runJavaScript(
            f'window.__cdNativeRealtime && window.__cdNativeRealtime({payload});')

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _toggle_focus(self):
        """Alterna foco entre o jogo e o overlay (foca o WebView do MapGenie)."""
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        fg_hwnd = user32.GetForegroundWindow()
        overlay_hwnd = int(self.winId())
        # Se o overlay já está em foreground, devolve foco ao jogo
        if int(fg_hwnd) == overlay_hwnd:
            self._view.page().runJavaScript(
                'window.__cdOverlayFocused = false;'
                'window.__cdGamepadStop && window.__cdGamepadStop();')
            focus_game_window()
        else:
            # Traz o overlay para frente e foca o WebView
            self.show()
            self.raise_()
            self.activateWindow()
            self._activate_for_waypoints()
            self._view.setFocus()
            self._view.page().runJavaScript(
                'window.__cdOverlayFocused = true;'
                'window.__cdGamepadStart && window.__cdGamepadStart();')

    def _activate_for_waypoints(self):
        """Traz o overlay para frente quando o painel de waypoints abre."""
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
        user32.AttachThreadInput.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.BOOL]
        user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
        fg_hwnd = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
        my_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if fg_tid and fg_tid != my_tid:
            user32.AttachThreadInput(fg_tid, my_tid, True)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(fg_tid, my_tid, False)
        else:
            user32.SetForegroundWindow(hwnd)

    def _do_dev_restart(self):
        _PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.close()
        subprocess.Popen(
            [sys.executable, '-m', 'overlay.main'],
            cwd=_PROJECT_DIR,
            creationflags=0x00000010,  # CREATE_NEW_CONSOLE
        )
        # Fecha o console antigo para evitar "Pressione qualquer tecla..."
        kernel32 = ctypes.windll.kernel32
        console = kernel32.GetConsoleWindow()
        if console:
            ctypes.windll.user32.PostMessageW(console, 0x0010, 0, 0)  # WM_CLOSE
        os._exit(0)

    def _hotkey_thread(self):
        user32    = ctypes.windll.user32
        WM_HOTKEY = 0x0312
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT, HOTKEY_VK):
            print("[!] Failed to register Ctrl+Shift+M hotkey")
            return
        print("[*] Hotkey registered: Ctrl+Shift+M  →  show/hide")
        if not getattr(sys, 'frozen', False):
            if user32.RegisterHotKey(None, RESTART_HOTKEY_ID, MOD_CONTROL | MOD_SHIFT | MOD_ALT, RESTART_HOTKEY_VK):
                print("[*] Hotkey registered: Ctrl+Shift+Alt+R  →  restart (dev-only)")
            else:
                print("[!] Failed to register Ctrl+Shift+Alt+R hotkey (dev-only)")
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == HOTKEY_ID:
                    self._signals.toggle.emit()
                elif not getattr(sys, 'frozen', False) and msg.wParam == RESTART_HOTKEY_ID:
                    self._signals.restart.emit()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnregisterHotKey(None, HOTKEY_ID)
        if not getattr(sys, 'frozen', False):
            user32.UnregisterHotKey(None, RESTART_HOTKEY_ID)

    def closeEvent(self, event):
        cfg = load_config()
        w = self.width()
        current_url = self._view.url().toString()
        cfg.update({
            'width':  w,
            'height': w if self._round_window else self.height(),
            'x':      self.x(),
            'y':      self.y(),
        })
        if current_url and current_url not in ('about:blank', 'about:blank#blocked', ''):
            cfg['url'] = current_url
        save_config(cfg)
        print("[*] Config saved")
        super().closeEvent(event)

# ── Main ──────────────────────────────────────────────────────────────

def _start_server_thread(ready_event):
    """Inicia o servidor WebSocket como thread daemon.
    Roda asyncio.run(_main()) em background — quando o overlay fecha,
    a thread daemon morre junto."""
    from server.main import _main
    try:
        asyncio.run(_main(ready_event))
    except Exception as e:
        import logging
        logging.getLogger('cd_server').error("Server thread crashed: %s", e)
        ready_event.set()  # desbloqueia o overlay imediatamente

def main():
    # Habilitar DPI awareness antes de criar o app
    ctypes.windll.user32.SetProcessDPIAware()
    register_mapgenie_patch_scheme()

    # ── Log file (sem console, logs vão para arquivo) ─────────────────
    # Definido ANTES de importar server.main, pois o logging é configurado
    # no nível do módulo e lê CD_LOG_FILE no momento do import.
    app_dir = os.environ.get(
        'CD_APP_DIR',
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    os.environ.setdefault('CD_LOG_FILE', os.path.join(app_dir, 'cd_server.log'))

    # ── Teleport toggle ───────────────────────────────────────────────
    # Lê a config ANTES de iniciar o servidor para definir a env var
    # que controla se os hooks de teleport (hook_e, hook_c) são injetados.
    cfg = load_config()
    if not cfg.get('teleportEnabled', True):
        os.environ['CD_TELEPORT_ENABLED'] = '0'
    os.environ['CD_NEARBY_CONTROLS_ENABLED'] = '1' if cfg.get('nearbyControlsEnabled', False) else '0'
    if not cfg.get('useSharedMemoryEntity', True):
        os.environ['CD_USE_SHARED_MEMORY_ENTITY'] = '0'

    # ── Checagem de admin e dependências (antes de qualquer coisa) ────
    from server.main import assert_admin, _check_dependencies
    assert_admin()
    _check_dependencies()

    # ── Servidor WebSocket como thread daemon ─────────────────────────
    _server_ready = threading.Event()
    server_thread = threading.Thread(
        target=_start_server_thread, args=(_server_ready,), daemon=True, name="cd-server")
    server_thread.start()
    _server_ready.wait(timeout=10)

    if not getattr(sys, 'frozen', False):
        sys.argv += ['--remote-debugging-port=9222']

    if cfg.get('disableGpuVsync'):
        sys.argv += ['--disable-gpu-vsync']

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName('CD Map Overlay')
    screen_w, screen_h = get_screen_size()

    window = OverlayWindow(cfg, screen_w, screen_h)
    window.show()

    print("[*] Overlay started  —  hotkey: Ctrl+Shift+M")
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
