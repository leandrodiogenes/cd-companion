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
import sys
import threading
import http.server
import socketserver

from PyQt5.QtCore import Qt, QUrl, QTimer, pyqtSignal, QObject
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QCursor
from PyQt5.QtGui import QBitmap, QPainter
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget,
                             QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QCheckBox, QPushButton, QSlider)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

from overlay.config_defaults import SETTING_DEFAULTS

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
HOTKEY_VK   = ord('M')

# SETTING_DEFAULTS importado de overlay_config_defaults.py

# ── Config ────────────────────────────────────────────────────────────
def start_lang_server():
    lang_dir = os.path.join(os.path.dirname(__file__), "lang")

    class CORSRequestHandler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()
        def do_OPTIONS(self):
            self.send_response(204)
            self.end_headers()
    os.chdir(lang_dir)
    with socketserver.TCPServer(("127.0.0.1", 8765), CORSRequestHandler) as httpd:
        print("[*] Language server running at http://127.0.0.1:8765")
        httpd.serve_forever()

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

from string import Template as _Template

def _load_inject_js():
    js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'inject.js')
    with open(js_path, 'r', encoding='utf-8') as f:
        raw = f.read()
    return _Template(raw).safe_substitute(WS_URL=WS_URL)

INJECT_JS = _load_inject_js()

# ── Widgets extraídos para overlay_widgets.py ────────────────────────
from overlay.widgets import (
    SettingsDialog, InterceptPage, LoginPrompt,
    HotkeySignals, TitleBar,
)

# ── Janela principal ──────────────────────────────────────────────────

class OverlayWindow(QMainWindow):
    def __init__(self, cfg, screen_w, screen_h):
        super().__init__()

        w   = cfg.get('width',  DEFAULT_W)
        h   = cfg.get('height', DEFAULT_H)
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

        # ── Botões flutuantes individuais (modo circular) ──────────────
        self._create_float_btns(root)

        # Aplica máscara e posiciona barra
        self._apply_mask()
        self._update_bar_geometry()

        # ── Conectar barra ─────────────────────────────────────────────
        self._bar.btn_back.clicked.connect(self._view.back)
        self._bar.btn_settings.clicked.connect(self._open_settings)
        self._bar.btn_hide.clicked.connect(self.hide)
        self._bar.btn_close.clicked.connect(self.close)

        # Página customizada para interceptar cdcompanion://login-needed
        page = InterceptPage(self._view)
        page.login_needed.connect(self._on_login_needed)
        self._view.setPage(page)

        self._view.loadFinished.connect(self._on_load_finished)
        self._view.load(QUrl(url))

        # ── Timer de hover para mostrar/ocultar a barra ────────────────
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(40)
        self._hover_timer.timeout.connect(self._check_bar_hover)
        self._hover_timer.start()

        # ── Timer de follow da janela do jogo ──────────────────────────
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(32)
        self._follow_timer.timeout.connect(self._update_follow_pos)
        if cfg.get('followGameWindow', SETTING_DEFAULTS['followGameWindow']):
            self._follow_timer.start()

        # ── Hotkey ─────────────────────────────────────────────────────
        self._signals = HotkeySignals()
        self._signals.toggle.connect(self._toggle_visible)
        threading.Thread(target=self._hotkey_thread, daemon=True).start()

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
        else:
            p.drawRoundedRect(0, 0, self.width(), self.height(),
                              self.CORNER_RADIUS, self.CORNER_RADIUS)
        p.end()
        self.setMask(bm)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._view.setGeometry(0, 0, self.width(), self.height())
        self._apply_mask()
        self._update_bar_geometry()
        self._update_float_btn_geometry()

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
            return

        inside_w = 0 <= lx <= w
        show_zone, hide_zone = self._bar_zones()
        if not self._bar_visible:
            if inside_w and 0 <= ly <= show_zone:
                self._bar.show()
                self._bar.raise_()
                self._bar_visible = True
        else:
            if not inside_w or ly > hide_zone or ly < 0:
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
            save_config(cfg)
            was_round = self._round_window
            self._round_window = new_settings.get('roundWindow', False)
            if self._round_window and not was_round:
                self.resize(240, 240)   # resizeEvent aplica máscara e barra
            else:
                self._apply_mask()
                self._update_bar_geometry()
            if not self._round_window and was_round:
                # Saindo do modo circular: oculta botões flutuantes
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
            self._apply_settings_js(new_settings)

    def _apply_settings_js(self, settings):
        # Só atualiza a variável — os auto-hides de painel só devem rodar
        # no carregamento da página (via _on_load_finished), não ao salvar.
        self._view.page().runJavaScript(
            f'window.__cdSettings = {json.dumps(settings)};'
            f'window.__cdApplyRoundLayout && window.__cdApplyRoundLayout(window.__cdSettings);'
            f'window.__cdApplyRotationSettings && window.__cdApplyRotationSettings(window.__cdSettings);')

    def _on_load_finished(self, ok):
        if ok:
            cfg = load_config()
            settings = {k: cfg.get(k, v) for k, v in SETTING_DEFAULTS.items()}
            self._view.page().runJavaScript(
                f'window.__cdSettings = {json.dumps(settings)};')
            self._view.page().runJavaScript(INJECT_JS)

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _hotkey_thread(self):
        user32    = ctypes.windll.user32
        WM_HOTKEY = 0x0312
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT, HOTKEY_VK):
            print("[!] Failed to register Ctrl+Shift+M hotkey")
            return
        print("[*] Hotkey registered: Ctrl+Shift+M  →  show/hide")
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self._signals.toggle.emit()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnregisterHotKey(None, HOTKEY_ID)

    def closeEvent(self, event):
        cfg = load_config()
        cfg.update({
            'width':  self.width(),
            'height': self.height(),
            'x':      self.x(),
            'y':      self.y(),
            'url':    self._view.url().toString(),
        })
        save_config(cfg)
        print("[*] Config saved")
        super().closeEvent(event)

# ── Main ──────────────────────────────────────────────────────────────

def _start_server_thread():
    """Inicia o servidor WebSocket como thread daemon.
    Roda asyncio.run(_main()) em background — quando o overlay fecha,
    a thread daemon morre junto."""
    from server.main import _main
    try:
        asyncio.run(_main())
    except Exception as e:
        import logging
        logging.getLogger('cd_server').error("Server thread crashed: %s", e)

def main():
    # Habilitar DPI awareness antes de criar o app
    ctypes.windll.user32.SetProcessDPIAware()

    # ── Log file (sem console, logs vão para arquivo) ─────────────────
    # Definido ANTES de importar server.main, pois o logging é configurado
    # no nível do módulo e lê CD_LOG_FILE no momento do import.
    app_dir = os.environ.get(
        'CD_APP_DIR',
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    os.environ.setdefault('CD_LOG_FILE', os.path.join(app_dir, 'cd_server.log'))

    # ── Checagem de admin e dependências (antes de qualquer coisa) ────
    from server.main import assert_admin, _check_dependencies
    assert_admin()
    _check_dependencies()

    # ── Servidor WebSocket como thread daemon ─────────────────────────
    server_thread = threading.Thread(
        target=_start_server_thread, daemon=True, name="cd-server")
    server_thread.start()

    # Pequena espera para o servidor subir antes do overlay conectar
    import time, socket
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(('localhost', 7891), timeout=0.5):
                break
        except Exception:
            time.sleep(0.2)
    lang_thread = threading.Thread(
        target=start_lang_server,
        daemon=True,
        name="cd-lang-server"
    )
    lang_thread.start()

    app = QApplication(sys.argv)
    app.setApplicationName('CD Map Overlay')

    cfg = load_config()
    screen_w, screen_h = get_screen_size()

    window = OverlayWindow(cfg, screen_w, screen_h)
    window.show()

    print("[*] Overlay started  —  hotkey: Ctrl+Shift+M")
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
