"""
Widgets auxiliares do overlay — extraídos de overlay_app.py.
Contém: SettingsDialog, _PopupWebWindow, _InterceptPage, _LoginPrompt,
         HotkeySignals, TitleBar.
"""

import ctypes
import ctypes.wintypes
import json
import os

from PyQt5.QtCore import Qt, QPoint, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QCheckBox, QPushButton, QSlider,
                             QMainWindow, QShortcut, QWidget, QKeySequenceEdit,
                             QScrollArea, QFrame, QLineEdit, QTabWidget,
                             QComboBox)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

# Importa SETTING_DEFAULTS do módulo de configuração
from overlay.config_defaults import SETTING_DEFAULTS

try:
    from server.main import (
        set_nearby_hotkey_paused as _set_nearby_hotkey_paused,
        set_nearby_popup_hwnd as _set_nearby_popup_hwnd,
        set_controller_hotkey_paused as _set_controller_hotkey_paused,
        set_waypoints_popup_hwnd as _set_waypoints_popup_hwnd,
    )
except Exception:
    _set_nearby_hotkey_paused = None
    _set_nearby_popup_hwnd = None
    _set_controller_hotkey_paused = None
    _set_waypoints_popup_hwnd = None

try:
    from server.hotkeys import (
        _load_controller_hotkey_settings, _save_controller_hotkey_settings,
        _load_xinput_get_state, _controller_buttons,
        XINPUT_BUTTON_NAMES, mask_to_name,
        DEFAULT_CONTROLLER_HOTKEYS,
    )
    _HAS_CONTROLLER_HOTKEYS = True
except Exception:
    _HAS_CONTROLLER_HOTKEYS = False

# Callback chamado quando qualquer popup fecha e devolve foco ao jogo.
# O overlay main.py seta isso para parar o gamepad.
_on_popup_close_focus_game = None

# ── Hotkey helpers ────────────────────────────────────────────────────
_SAVE_DIR = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'CD_Teleport')
_HOTKEY_SETTINGS_FILE = os.path.join(_SAVE_DIR, 'cd_hotkeys.json')
_OPEN_NEARBY_DEFAULT = {'vk': 0x4E, 'mod': 0x10}  # Shift+N

_VK_MAP = {f'F{i}': 0x6F + i for i in range(1, 13)}
_VK_MAP.update({chr(c): c for c in range(0x41, 0x5B)})   # A–Z
_VK_MAP.update({str(d): 0x30 + d for d in range(10)})     # 0–9
_MOD_MAP  = {'Shift': 0x10, 'Ctrl': 0x11, 'Alt': 0x12}
_VK_DISP  = {v: k for k, v in _VK_MAP.items()}
_MOD_DISP = {0x10: 'Shift', 0x11: 'Ctrl', 0x12: 'Alt'}


def _vk_to_seq_str(vk, mod):
    key = _VK_DISP.get(vk, f'VK{vk:#04x}')
    prefix = f'{_MOD_DISP[mod]}+' if mod in _MOD_DISP else ''
    return f'{prefix}{key}'


def _seq_str_to_vk(seq_str):
    """'Shift+N' → (vk, mod). Returns None se inválido."""
    parts = [p.strip() for p in seq_str.split('+')]
    key = parts[-1]
    vk = _VK_MAP.get(key.upper() if len(key) == 1 else key)
    if vk is None:
        return None
    mod = 0
    for m in parts[:-1]:
        mod = _MOD_MAP.get(m, mod)
    return vk, mod


def _find_process_window(exe_name):
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    found = [None]
    pid = ctypes.wintypes.DWORD()
    target = exe_name.lower()

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool,
                                   ctypes.wintypes.HWND,
                                   ctypes.wintypes.LPARAM)

    @EnumProc
    def _cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hproc = kernel32.OpenProcess(0x1000, False, pid)
        if not hproc:
            return True
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.wintypes.DWORD(260)
        ok = kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size))
        kernel32.CloseHandle(hproc)
        if ok and buf.value.lower().endswith(target):
            rc = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            if rc.right - rc.left > 200:
                found[0] = hwnd
                return False
        return True

    user32.EnumWindows(_cb, 0)
    return found[0]


def focus_game_window():
    hwnd = _find_process_window('crimsondesert.exe')
    if not hwnd:
        return
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg_hwnd = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
    my_tid = kernel32.GetCurrentThreadId()
    if fg_tid and fg_tid != my_tid:
        user32.AttachThreadInput(fg_tid, my_tid, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(fg_tid, my_tid, False)
    else:
        user32.SetForegroundWindow(hwnd)

# ── Estilos ──────────────────────────────────────────────────────────

class NoWheelSlider(QSlider):
    def wheelEvent(self, event):
        event.ignore()


SETTINGS_STYLE = """
QDialog  { background:#0f0f1a; color:#e2e8f0; }
QLabel   { color:#e2e8f0; font:13px 'Segoe UI'; }
QCheckBox { color:#e2e8f0; font:13px 'Segoe UI'; spacing:8px; }
QCheckBox::indicator { width:18px; height:18px; border-radius:4px;
                       border:1px solid #334155; background:#1a1a2e; }
QCheckBox::indicator:checked { background:#1db954; border-color:#1db954; }
QPushButton { background:#1a1a2e; color:#e2e8f0; border:1px solid #2d2d44;
              border-radius:6px; padding:6px 18px; font:13px 'Segoe UI'; }
QPushButton:hover { background:#252540; }
QPushButton#save { background:#1db954; color:#fff; border:none; }
QPushButton#save:hover { background:#17a84a; }
QKeySequenceEdit, QComboBox {
    background:#e2e8f0; color:#111827; border:1px solid #64748b;
    border-radius:6px; padding:4px 8px; min-height:22px;
    selection-background-color:#ffd060; selection-color:#111827;
}
QKeySequenceEdit:focus, QComboBox:focus { border:1px solid #ffd060; }
QComboBox::drop-down {
    subcontrol-origin:padding; subcontrol-position:top right;
    width:24px; border-left:1px solid #94a3b8; border-top-right-radius:6px;
    border-bottom-right-radius:6px; background:#cbd5e1;
}
QComboBox::down-arrow {
    image:none; width:0; height:0; border-left:4px solid transparent;
    border-right:4px solid transparent; border-top:5px solid #1f2937;
}
QComboBox QAbstractItemView {
    background:#f8fafc; color:#111827; selection-background-color:#ffd060;
    selection-color:#111827; border:1px solid #475569;
}
QScrollArea { background:transparent; border:none; }
QScrollBar:vertical {
    background:#111122; width:10px; margin:2px 0 2px 0; border-radius:5px;
}
QScrollBar::handle:vertical {
    background:#475569; min-height:28px; border-radius:5px;
}
QScrollBar::handle:vertical:hover { background:#64748b; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QSlider::groove:horizontal { height:4px; background:#2d2d44; border-radius:2px; }
QSlider::handle:horizontal { width:14px; height:14px; margin:-5px 0;
    background:#ffd060; border-radius:7px; }
QSlider::sub-page:horizontal { background:#ffd060; border-radius:2px; }
"""


# ── Diálogo de configurações ─────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.setWindowFlags(
            (self.windowFlags() | Qt.WindowStaysOnTopHint)
            & ~Qt.WindowContextHelpButtonHint
        )
        self.setStyleSheet(SETTINGS_STYLE)
        self.setMinimumWidth(840)
        self.resize(840, 615)
        self._cfg = cfg
        self._original_opacity = parent.windowOpacity() if parent else 1.0

        outer_layout = QVBoxLayout(self)
        outer_layout.setSpacing(12)
        outer_layout.setContentsMargins(16, 16, 16, 16)

        # ── Tab widget ─────────────────────────────────────────────────
        tabs = QTabWidget()
        tabs.setStyleSheet(
            "QTabWidget::pane{border:1px solid #2d2d44;border-radius:6px;"
            "background:#0f0f1a;}"
            "QTabBar::tab{background:#1a1a2e;color:#94a3b8;border:1px solid #2d2d44;"
            "border-bottom:none;border-top-left-radius:6px;border-top-right-radius:6px;"
            "padding:6px 20px;margin-right:2px;font:12px 'Segoe UI';min-width:70px;}"
            "QTabBar::tab:selected{background:#151528;color:#ffd060;"
            "border-color:#ffd060;border-bottom:1px solid #151528;}"
            "QTabBar::tab:hover:!selected{background:#252540;color:#e2e8f0;}")
        outer_layout.addWidget(tabs, 1)

        self._checkboxes = {}

        # ── Helpers ────────────────────────────────────────────────────
        active_layout = [None]

        def _make_tab_scroll():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            content = QWidget()
            content.setStyleSheet('background:transparent;')
            lay = QVBoxLayout(content)
            lay.setSpacing(12)
            lay.setContentsMargins(12, 12, 12, 12)
            scroll.setWidget(content)
            return scroll, lay

        def section(text, layout):
            frame = QFrame()
            frame.setObjectName('settingsSection')
            frame.setStyleSheet(
                'QFrame#settingsSection{background:#151528;'
                'border:1px solid #2d2d44;border-radius:7px;}')
            frame_layout = QVBoxLayout(frame)
            frame_layout.setSpacing(7)
            frame_layout.setContentsMargins(12, 10, 12, 12)
            lbl = QLabel(text.upper())
            lbl.setStyleSheet(
                'color:#7c8db5; font:700 10px "Segoe UI"; '
                'margin-bottom:2px; letter-spacing:1px;')
            frame_layout.addWidget(lbl)
            layout.addWidget(frame)
            active_layout[0] = frame_layout
            return frame_layout

        def option(key, label, description):
            target = active_layout[0]
            cb = QCheckBox(label)
            cb.setChecked(cfg.get(key, SETTING_DEFAULTS[key]))
            cb.setToolTip(description)
            cb.setObjectName(key)
            target.addWidget(cb)
            if description:
                desc = QLabel(description)
                desc.setWordWrap(True)
                desc.setStyleSheet(
                    'color:#64748b; font:11px "Segoe UI"; margin-left:26px; '
                    'margin-top:-4px; margin-bottom:2px;')
                target.addWidget(desc)
            self._checkboxes[key] = cb

        def slider_block(title, value_label, slider, description=None):
            target = active_layout[0]
            row = QHBoxLayout()
            row.setSpacing(10)
            title_lbl = QLabel(title)
            row.addWidget(title_lbl, 1)
            row.addWidget(value_label)
            target.addLayout(row)
            target.addWidget(slider)
            if description:
                desc = QLabel(description)
                desc.setWordWrap(True)
                desc.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-bottom:2px;')
                target.addWidget(desc)


        # ══════════════════════════════════════════════════════════════
        # TAB: Map
        # ══════════════════════════════════════════════════════════════
        map_scroll, map_layout = _make_tab_scroll()
        tabs.addTab(map_scroll, 'Map')

        section('On map load', map_layout)
        option('restoreLastPosition', 'Restore last position',
               'Returns to the location and zoom from your last visit')
        option('autoHideFound', 'Hide Found Locations',
               'Automatically disables "Found Locations" when the map opens')
        option('autoHideLeftSidebar', 'Hide Left Panel',
               'Automatically closes the left sidebar')
        option('autoHideRightSidebar', 'Hide Right Panel',
               'Automatically closes the right sidebar')

        section('Map icons', map_layout)
        self._map_icon_scale_val = QLabel()
        self._map_icon_scale_val.setStyleSheet(
            "color:#ffd060; font:13px 'Consolas'; min-width:40px;")
        self._map_icon_scale = NoWheelSlider(Qt.Horizontal)
        self._map_icon_scale.setRange(5, 20)
        self._map_icon_scale.setSingleStep(1)
        raw_icon_scale = cfg.get('mapIconScale', SETTING_DEFAULTS['mapIconScale'])
        self._map_icon_scale.setValue(round(float(raw_icon_scale) * 10))

        def _on_icon_scale(v):
            self._map_icon_scale_val.setText(f'{v / 10:.1f}x')

        _on_icon_scale(self._map_icon_scale.value())
        self._map_icon_scale.valueChanged.connect(_on_icon_scale)
        slider_block('Icon scale', self._map_icon_scale_val, self._map_icon_scale,
                     'Scales location markers while keeping automatic zoom-based sizing.')

        map_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # TAB: Window
        # ══════════════════════════════════════════════════════════════
        win_scroll, win_layout = _make_tab_scroll()
        tabs.addTab(win_scroll, 'Window')

        section('Window', win_layout)
        option('roundWindow', 'Circular/oval window',
               'Applies an elliptical mask to the window')
        option('followGameWindow', 'Follow game window',
               'Moves the overlay automatically when the game window is moved')
        option('alwaysShowTitleBar', 'Always show title bar',
               'Keeps the title bar always visible instead of showing only on mouse hover (square mode only)')

        transp_val = QLabel(f'{cfg.get("transparency", SETTING_DEFAULTS["transparency"])}%')
        transp_val.setStyleSheet('color:#ffd060; font:12px "Segoe UI"; min-width:32px;')
        self._slider = NoWheelSlider(Qt.Horizontal)
        self._slider.setRange(0, 90)
        self._slider.setValue(cfg.get('transparency', SETTING_DEFAULTS['transparency']))
        self._slider.setTickInterval(10)

        def on_slider(v):
            transp_val.setText(f'{v}%')
            if parent:
                parent.setWindowOpacity(1.0 - v / 100)

        self._slider.valueChanged.connect(on_slider)
        slider_block('Transparency', transp_val, self._slider)

        # Browser zoom
        zoom_val = QLabel(f'{cfg.get("browserZoom", SETTING_DEFAULTS["browserZoom"])}%')
        zoom_val.setStyleSheet('color:#ffd060; font:12px "Segoe UI"; min-width:32px;')
        self._zoom_slider = NoWheelSlider(Qt.Horizontal)
        self._zoom_slider.setRange(70, 150)
        self._zoom_slider.setSingleStep(5)
        self._zoom_slider.setPageStep(10)
        self._zoom_slider.setValue(cfg.get('browserZoom', SETTING_DEFAULTS['browserZoom']))

        def on_zoom(v):
            # Snap to nearest 5
            snapped = round(v / 5) * 5
            if snapped != v:
                self._zoom_slider.setValue(snapped)
                return
            zoom_val.setText(f'{v}%')

        self._zoom_slider.valueChanged.connect(on_zoom)
        slider_block('Browser zoom', zoom_val, self._zoom_slider,
                     'Zoom level of the map page. Applied immediately on save.')

        # Focus toggle hotkey
        section('Focus toggle', win_layout)
        ft_hk_lbl = QLabel('Keyboard hotkey')
        self._focus_toggle_hk = QKeySequenceEdit()
        self._focus_toggle_hk.setFixedHeight(32)
        self._focus_toggle_hk.setAttribute(Qt.WA_StyledBackground, True)
        self._focus_toggle_hk.setStyleSheet(
            "QKeySequenceEdit{background:#e2e8f0;color:#111827;"
            "border:1px solid #64748b;border-radius:6px;padding:4px 8px;"
            "selection-background-color:#ffd060;selection-color:#111827;}"
            "QKeySequenceEdit:focus{border:1px solid #ffd060;}")
        ft_hk_line = self._focus_toggle_hk.findChild(QLineEdit)
        if ft_hk_line:
            ft_hk_line.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:none;"
                "selection-background-color:#ffd060;selection-color:#111827;}")
        self._focus_toggle_hk.setToolTip('Restart overlay for the new hotkey to take effect')
        self._focus_toggle_hk._hk_finalized = False

        def _ft_on_seq_changed(seq):
            first = seq.toString().split(', ')[0]
            if first != seq.toString():
                self._focus_toggle_hk.setKeySequence(QKeySequence(first))
            self._focus_toggle_hk._hk_finalized = True

        def _ft_hk_key_press(e):
            if self._focus_toggle_hk._hk_finalized:
                self._focus_toggle_hk.clear()
                self._focus_toggle_hk._hk_finalized = False
            QKeySequenceEdit.keyPressEvent(self._focus_toggle_hk, e)

        self._focus_toggle_hk.keySequenceChanged.connect(_ft_on_seq_changed)
        self._focus_toggle_hk.keyPressEvent = _ft_hk_key_press

        def _ft_hk_focus_in(e):
            if _set_nearby_hotkey_paused: _set_nearby_hotkey_paused(True)
            QKeySequenceEdit.focusInEvent(self._focus_toggle_hk, e)
        def _ft_hk_focus_out(e):
            if _set_nearby_hotkey_paused: _set_nearby_hotkey_paused(False)
            QKeySequenceEdit.focusOutEvent(self._focus_toggle_hk, e)
        self._focus_toggle_hk.focusInEvent = _ft_hk_focus_in
        self._focus_toggle_hk.focusOutEvent = _ft_hk_focus_out

        ft_hk_vk = 0
        ft_hk_mod = 0
        try:
            with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as _f:
                _ft_hk_data = json.load(_f).get('focus_toggle', {})
                ft_hk_vk = _ft_hk_data.get('vk', ft_hk_vk)
                ft_hk_mod = _ft_hk_data.get('mod', ft_hk_mod)
        except Exception:
            pass
        if ft_hk_vk:
            self._focus_toggle_hk.setKeySequence(QKeySequence(_vk_to_seq_str(ft_hk_vk, ft_hk_mod)))
        ft_hk_row = QWidget()
        ft_hk_row_layout = QHBoxLayout(ft_hk_row)
        ft_hk_row_layout.setContentsMargins(0, 0, 0, 0)
        ft_hk_row_layout.setSpacing(6)
        ft_hk_row_layout.addWidget(self._focus_toggle_hk)
        ft_hk_clear_btn = QPushButton('Clear')
        ft_hk_clear_btn.setFixedHeight(32)
        ft_hk_clear_btn.setStyleSheet(
            "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
            "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
            "QPushButton:hover{background:rgba(255,80,80,.3);}")
        ft_hk_clear_btn.clicked.connect(lambda: self._focus_toggle_hk.clear())
        ft_hk_row_layout.addWidget(ft_hk_clear_btn)
        active_layout[0].addWidget(ft_hk_lbl)
        active_layout[0].addWidget(ft_hk_row)

        # Focus toggle controller combo
        if _HAS_CONTROLLER_HOTKEYS:
            active_layout[0].addWidget(QLabel('Controller combo'))
            ft_ctrl_row = QWidget()
            ft_ctrl_row_layout = QHBoxLayout(ft_ctrl_row)
            ft_ctrl_row_layout.setContentsMargins(0, 0, 0, 0)
            ft_ctrl_row_layout.setSpacing(6)
            _ft_ctrl_settings = _load_controller_hotkey_settings()
            _ft_ctrl_mask = _ft_ctrl_settings.get("focus_toggle", 0x0300)
            self._ft_ctrl_display = QLineEdit(mask_to_name(_ft_ctrl_mask))
            self._ft_ctrl_display.setReadOnly(True)
            self._ft_ctrl_display.setFixedHeight(32)
            self._ft_ctrl_display.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:1px solid #64748b;"
                "border-radius:6px;padding:4px 8px;}")
            self._ft_ctrl_record_btn = QPushButton('Record')
            self._ft_ctrl_record_btn.setFixedHeight(32)
            self._ft_ctrl_record_btn.setStyleSheet(
                "QPushButton{background:rgba(255,208,96,.18);"
                "border:1px solid rgba(255,208,96,.5);"
                "color:#ffd060;border-radius:6px;padding:0 10px;}"
                "QPushButton:hover{background:rgba(255,208,96,.3);}"
                "QPushButton:disabled{color:#666;border-color:#444;background:#222;}")
            _ft_ctrl_clear_btn = QPushButton('Clear')
            _ft_ctrl_clear_btn.setFixedHeight(32)
            _ft_ctrl_clear_btn.setStyleSheet(
                "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
                "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
                "QPushButton:hover{background:rgba(255,80,80,.3);}")

            def _ft_ctrl_clear():
                self._ft_ctrl_display.setText('None')
                try:
                    existing = _load_controller_hotkey_settings()
                    existing["focus_toggle"] = 0
                    _save_controller_hotkey_settings(existing)
                except Exception:
                    pass

            _ft_ctrl_clear_btn.clicked.connect(_ft_ctrl_clear)
            ft_ctrl_row_layout.addWidget(self._ft_ctrl_display)
            ft_ctrl_row_layout.addWidget(self._ft_ctrl_record_btn)
            ft_ctrl_row_layout.addWidget(_ft_ctrl_clear_btn)
            active_layout[0].addWidget(ft_ctrl_row)

            ft_note = QLabel('Toggles focus between Crimson Desert and the overlay map. Restart required.')
            ft_note.setWordWrap(True)
            ft_note.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
            active_layout[0].addWidget(ft_note)

            self._ft_ctrl_recording = False
            self._ft_ctrl_peak_mask = 0
            self._ft_ctrl_timer = QTimer()
            self._ft_ctrl_timer.setInterval(50)
            _ft_get_xinput = _load_xinput_get_state()

            def _ft_ctrl_poll():
                btns = _controller_buttons(_ft_get_xinput)
                self._ft_ctrl_peak_mask |= btns
                if self._ft_ctrl_peak_mask and btns == 0:
                    saved_mask = self._ft_ctrl_peak_mask
                    self._ft_ctrl_timer.stop()
                    self._ft_ctrl_recording = False
                    self._ft_ctrl_record_btn.setText('Record')
                    self._ft_ctrl_record_btn.setEnabled(True)
                    self._ft_ctrl_display.setText(mask_to_name(saved_mask))
                    if _set_controller_hotkey_paused:
                        _set_controller_hotkey_paused(False)
                    try:
                        existing = _load_controller_hotkey_settings()
                        existing["focus_toggle"] = saved_mask
                        _save_controller_hotkey_settings(existing)
                    except Exception:
                        pass

            self._ft_ctrl_timer.timeout.connect(_ft_ctrl_poll)

            def _ft_start_record():
                if self._ft_ctrl_recording:
                    return
                self._ft_ctrl_recording = True
                self._ft_ctrl_peak_mask = 0
                self._ft_ctrl_display.setFocus()
                self._ft_ctrl_record_btn.setText('Recording...')
                self._ft_ctrl_record_btn.setEnabled(False)
                if _set_controller_hotkey_paused:
                    _set_controller_hotkey_paused(True)
                self._ft_ctrl_timer.start()

            self._ft_ctrl_record_btn.clicked.connect(_ft_start_record)

        win_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # TAB: Teleport
        # ══════════════════════════════════════════════════════════════
        tp_scroll, tp_layout = _make_tab_scroll()
        tabs.addTab(tp_scroll, 'Teleport')

        section('Teleport', tp_layout)
        option('teleportEnabled', 'Enable teleport (restart overlay and game)',
               'When disabled, the physics delta hook (hook_e) and invulnerability hook (hook_c) '
               'are not injected into the game. Useful to avoid conflicts with other mods.')
        option('useSharedMemoryEntity', 'Use Freedom Flyer shared memory',
               'Reads player entity base from Freedom Flyer shared memory instead of installing '
               'an entity hook (hook_a). Reduces hook conflicts when using both mods together. '
               'Disable if not using Freedom Flyer and experiencing entity detection issues.')

        self._center_y_value = QLabel()
        self._center_y_value.setStyleSheet(
            "color:#ffd060; font:13px 'Consolas'; min-width:48px;")
        self._center_y = NoWheelSlider(Qt.Horizontal)
        self._center_y.setRange(-5000, 5000)
        self._center_y.setSingleStep(10)
        self._center_y.setPageStep(100)
        self._center_y.setValue(int(float(cfg.get(
            'centerTeleportY', SETTING_DEFAULTS['centerTeleportY']))))
        self._center_y.setToolTip(
            'Absolute Y used when teleporting to the center of the screen')
        self._center_y_value.setText(str(self._center_y.value()))
        self._center_y.valueChanged.connect(
            lambda value: self._center_y_value.setText(str(value)))
        slider_block('Center TP Y', self._center_y_value, self._center_y,
                     'Absolute Y used when teleporting to the center of the screen')

        tp_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # TAB: Nearby
        # ══════════════════════════════════════════════════════════════
        nb_scroll, nb_layout = _make_tab_scroll()
        tabs.addTab(nb_scroll, 'Nearby')

        section('Nearby', nb_layout)
        option('nearbyControlsEnabled', 'Enable nearby popup shortcuts',
               'Shift+N or LB+Down opens the nearby popup. In the popup: Up/Down, W/S, or D-pad moves, '
               'Enter, Space, or A toggles found, Esc or B closes.')
        option('nearbyRespectMapVisibility', 'Respect MapGenie category visibility',
               'When enabled, the nearby popup only shows locations from categories '
               'currently visible on the MapGenie map.')
        nearby_help = QLabel(
            'LB+Down / Shift+N open. Up/Down, W/S, D-pad navigate. '
            'Enter/Space/A toggles. Esc/B closes.')
        nearby_help.setWordWrap(True)
        nearby_help.setStyleSheet(
            'color:#7c8db5; font:11px "Segoe UI"; margin-left:26px; margin-top:-4px;')
        active_layout[0].addWidget(nearby_help)

        self._nearby_radius_val = QLabel()
        self._nearby_radius_val.setStyleSheet(
            "color:#ffd060; font:13px 'Consolas'; min-width:40px;")
        self._nearby_radius = NoWheelSlider(Qt.Horizontal)
        self._nearby_radius.setRange(1, 8)
        self._nearby_radius.setSingleStep(1)
        raw = cfg.get('nearbyThreshold', SETTING_DEFAULTS['nearbyThreshold'])
        self._nearby_radius.setValue(round(float(raw) * 1000))
        self._nearby_radius_val.setText(str(self._nearby_radius.value()))
        self._nearby_radius.valueChanged.connect(
            lambda v: self._nearby_radius_val.setText(str(v)))
        slider_block('Scan radius', self._nearby_radius_val, self._nearby_radius)

        # Nearby hotkey
        section('Hotkey', nb_layout)
        hk_lbl = QLabel('Open hotkey')
        self._nearby_hk = QKeySequenceEdit()
        self._nearby_hk.setFixedHeight(32)
        self._nearby_hk.setAttribute(Qt.WA_StyledBackground, True)
        self._nearby_hk.setStyleSheet(
            "QKeySequenceEdit{background:#e2e8f0;color:#111827;"
            "border:1px solid #64748b;border-radius:6px;padding:4px 8px;"
            "selection-background-color:#ffd060;selection-color:#111827;}"
            "QKeySequenceEdit:focus{border:1px solid #ffd060;}")
        hk_line = self._nearby_hk.findChild(QLineEdit)
        if hk_line:
            hk_line.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:none;"
                "selection-background-color:#ffd060;selection-color:#111827;}")
        self._nearby_hk.setToolTip('Restart overlay for the new hotkey to take effect')
        self._nearby_hk._hk_finalized = False

        def _on_seq_changed(seq):
            first = seq.toString().split(', ')[0]
            if first != seq.toString():
                self._nearby_hk.setKeySequence(QKeySequence(first))
            self._nearby_hk._hk_finalized = True

        def _hk_key_press(e):
            if self._nearby_hk._hk_finalized:
                self._nearby_hk.clear()
                self._nearby_hk._hk_finalized = False
            QKeySequenceEdit.keyPressEvent(self._nearby_hk, e)

        self._nearby_hk.keySequenceChanged.connect(_on_seq_changed)
        self._nearby_hk.keyPressEvent = _hk_key_press

        def _on_hk_focus_in():
            if _set_nearby_hotkey_paused: _set_nearby_hotkey_paused(True)
        def _on_hk_focus_out():
            if _set_nearby_hotkey_paused: _set_nearby_hotkey_paused(False)
        def _hk_focus_in(e):
            _on_hk_focus_in()
            QKeySequenceEdit.focusInEvent(self._nearby_hk, e)
        def _hk_focus_out(e):
            _on_hk_focus_out()
            QKeySequenceEdit.focusOutEvent(self._nearby_hk, e)
        self._nearby_hk.focusInEvent = _hk_focus_in
        self._nearby_hk.focusOutEvent = _hk_focus_out

        hk_vk = _OPEN_NEARBY_DEFAULT['vk']
        hk_mod = _OPEN_NEARBY_DEFAULT['mod']
        try:
            with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as _f:
                _hk_data = json.load(_f).get('open_nearby', {})
                hk_vk = _hk_data.get('vk', hk_vk)
                hk_mod = _hk_data.get('mod', hk_mod)
        except Exception:
            pass
        self._nearby_hk.setKeySequence(QKeySequence(_vk_to_seq_str(hk_vk, hk_mod)))
        nb_hk_row = QWidget()
        nb_hk_row_layout = QHBoxLayout(nb_hk_row)
        nb_hk_row_layout.setContentsMargins(0, 0, 0, 0)
        nb_hk_row_layout.setSpacing(6)
        nb_hk_row_layout.addWidget(self._nearby_hk)
        nb_hk_clear_btn = QPushButton('Clear')
        nb_hk_clear_btn.setFixedHeight(32)
        nb_hk_clear_btn.setStyleSheet(
            "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
            "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
            "QPushButton:hover{background:rgba(255,80,80,.3);}")
        nb_hk_clear_btn.clicked.connect(lambda: self._nearby_hk.clear())
        nb_hk_row_layout.addWidget(nb_hk_clear_btn)
        active_layout[0].addWidget(hk_lbl)
        active_layout[0].addWidget(nb_hk_row)
        hk_note = QLabel('Restart required to apply hotkey change.')
        hk_note.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
        active_layout[0].addWidget(hk_note)

        # Nearby controller combo
        if _HAS_CONTROLLER_HOTKEYS:
            section('Controller', nb_layout)
            active_layout[0].addWidget(QLabel('Open combo'))
            nb_ctrl_row = QWidget()
            nb_ctrl_row_layout = QHBoxLayout(nb_ctrl_row)
            nb_ctrl_row_layout.setContentsMargins(0, 0, 0, 0)
            nb_ctrl_row_layout.setSpacing(6)
            _nb_ctrl_settings = _load_controller_hotkey_settings()
            _nb_ctrl_mask = _nb_ctrl_settings.get("open_nearby", 0x0102)
            self._nb_ctrl_display = QLineEdit(mask_to_name(_nb_ctrl_mask))
            self._nb_ctrl_display.setReadOnly(True)
            self._nb_ctrl_display.setFixedHeight(32)
            self._nb_ctrl_display.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:1px solid #64748b;"
                "border-radius:6px;padding:4px 8px;}")
            self._nb_ctrl_record_btn = QPushButton('Record')
            self._nb_ctrl_record_btn.setFixedHeight(32)
            self._nb_ctrl_record_btn.setStyleSheet(
                "QPushButton{background:rgba(255,208,96,.18);"
                "border:1px solid rgba(255,208,96,.5);"
                "color:#ffd060;border-radius:6px;padding:0 10px;}"
                "QPushButton:hover{background:rgba(255,208,96,.3);}"
                "QPushButton:disabled{color:#666;border-color:#444;background:#222;}")
            nb_ctrl_row_layout.addWidget(self._nb_ctrl_display)
            nb_ctrl_row_layout.addWidget(self._nb_ctrl_record_btn)
            _nb_ctrl_clear_btn = QPushButton('Clear')
            _nb_ctrl_clear_btn.setFixedHeight(32)
            _nb_ctrl_clear_btn.setStyleSheet(
                "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
                "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
                "QPushButton:hover{background:rgba(255,80,80,.3);}")

            def _nb_ctrl_clear():
                self._nb_ctrl_display.setText('None')
                try:
                    existing = _load_controller_hotkey_settings()
                    existing["open_nearby"] = 0
                    _save_controller_hotkey_settings(existing)
                except Exception:
                    pass

            _nb_ctrl_clear_btn.clicked.connect(_nb_ctrl_clear)
            nb_ctrl_row_layout.addWidget(_nb_ctrl_clear_btn)
            active_layout[0].addWidget(nb_ctrl_row)
            nb_ctrl_note = QLabel(
                'Hold button combo on controller, then release. Restart required.')
            nb_ctrl_note.setWordWrap(True)
            nb_ctrl_note.setStyleSheet(
                'color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
            active_layout[0].addWidget(nb_ctrl_note)

            self._nb_ctrl_recording = False
            self._nb_ctrl_peak_mask = 0
            self._nb_ctrl_timer = QTimer()
            self._nb_ctrl_timer.setInterval(50)
            _nb_get_xinput = _load_xinput_get_state()

            def _nb_ctrl_poll():
                btns = _controller_buttons(_nb_get_xinput)
                self._nb_ctrl_peak_mask |= btns
                if self._nb_ctrl_peak_mask and btns == 0:
                    saved_mask = self._nb_ctrl_peak_mask
                    self._nb_ctrl_timer.stop()
                    self._nb_ctrl_recording = False
                    self._nb_ctrl_record_btn.setText('Record')
                    self._nb_ctrl_record_btn.setEnabled(True)
                    self._nb_ctrl_display.setText(mask_to_name(saved_mask))
                    if _set_controller_hotkey_paused:
                        _set_controller_hotkey_paused(False)
                    try:
                        existing = _load_controller_hotkey_settings()
                        existing["open_nearby"] = saved_mask
                        _save_controller_hotkey_settings(existing)
                    except Exception:
                        pass

            self._nb_ctrl_timer.timeout.connect(_nb_ctrl_poll)

            def _nb_start_record():
                if self._nb_ctrl_recording:
                    return
                self._nb_ctrl_recording = True
                self._nb_ctrl_peak_mask = 0
                self._nb_ctrl_display.setFocus()
                self._nb_ctrl_record_btn.setText('Recording...')
                self._nb_ctrl_record_btn.setEnabled(False)
                if _set_controller_hotkey_paused:
                    _set_controller_hotkey_paused(True)
                self._nb_ctrl_timer.start()

            self._nb_ctrl_record_btn.clicked.connect(_nb_start_record)

        nb_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # TAB: Waypoints
        # ══════════════════════════════════════════════════════════════
        wp_scroll, wp_layout = _make_tab_scroll()
        tabs.addTab(wp_scroll, 'Waypoints')

        section('Keyboard hotkey', wp_layout)
        wp_hk_lbl = QLabel('Open hotkey')
        self._waypoints_hk = QKeySequenceEdit()
        self._waypoints_hk.setFixedHeight(32)
        self._waypoints_hk.setAttribute(Qt.WA_StyledBackground, True)
        self._waypoints_hk.setStyleSheet(
            "QKeySequenceEdit{background:#e2e8f0;color:#111827;"
            "border:1px solid #64748b;border-radius:6px;padding:4px 8px;"
            "selection-background-color:#ffd060;selection-color:#111827;}"
            "QKeySequenceEdit:focus{border:1px solid #ffd060;}")
        wp_hk_line = self._waypoints_hk.findChild(QLineEdit)
        if wp_hk_line:
            wp_hk_line.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:none;"
                "selection-background-color:#ffd060;selection-color:#111827;}")
        self._waypoints_hk.setToolTip('Restart overlay for the new hotkey to take effect')
        self._waypoints_hk._hk_finalized = False

        def _wp_on_seq_changed(seq):
            first = seq.toString().split(', ')[0]
            if first != seq.toString():
                self._waypoints_hk.setKeySequence(QKeySequence(first))
            self._waypoints_hk._hk_finalized = True

        def _wp_hk_key_press(e):
            if self._waypoints_hk._hk_finalized:
                self._waypoints_hk.clear()
                self._waypoints_hk._hk_finalized = False
            QKeySequenceEdit.keyPressEvent(self._waypoints_hk, e)

        def _wp_hk_focus_in(e):
            if _set_nearby_hotkey_paused: _set_nearby_hotkey_paused(True)
            QKeySequenceEdit.focusInEvent(self._waypoints_hk, e)
        def _wp_hk_focus_out(e):
            if _set_nearby_hotkey_paused: _set_nearby_hotkey_paused(False)
            QKeySequenceEdit.focusOutEvent(self._waypoints_hk, e)
        self._waypoints_hk.focusInEvent = _wp_hk_focus_in
        self._waypoints_hk.focusOutEvent = _wp_hk_focus_out
        self._waypoints_hk.keySequenceChanged.connect(_wp_on_seq_changed)
        self._waypoints_hk.keyPressEvent = _wp_hk_key_press

        wp_hk_vk = 0x59
        wp_hk_mod = 0x10
        try:
            with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as _f:
                _wp_hk_data = json.load(_f).get('open_waypoints', {})
                wp_hk_vk = _wp_hk_data.get('vk', wp_hk_vk)
                wp_hk_mod = _wp_hk_data.get('mod', wp_hk_mod)
        except Exception:
            pass
        self._waypoints_hk.setKeySequence(QKeySequence(_vk_to_seq_str(wp_hk_vk, wp_hk_mod)))
        wp_hk_row = QWidget()
        wp_hk_row_layout = QHBoxLayout(wp_hk_row)
        wp_hk_row_layout.setContentsMargins(0, 0, 0, 0)
        wp_hk_row_layout.setSpacing(6)
        wp_hk_row_layout.addWidget(self._waypoints_hk)
        wp_hk_clear_btn = QPushButton('Clear')
        wp_hk_clear_btn.setFixedHeight(32)
        wp_hk_clear_btn.setStyleSheet(
            "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
            "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
            "QPushButton:hover{background:rgba(255,80,80,.3);}")
        wp_hk_clear_btn.clicked.connect(lambda: self._waypoints_hk.clear())
        wp_hk_row_layout.addWidget(wp_hk_clear_btn)
        active_layout[0].addWidget(wp_hk_lbl)
        active_layout[0].addWidget(wp_hk_row)
        wp_hk_note = QLabel('Restart required to apply hotkey change.')
        wp_hk_note.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
        active_layout[0].addWidget(wp_hk_note)

        # Controller combo
        if _HAS_CONTROLLER_HOTKEYS:
            section('Controller', wp_layout)
            active_layout[0].addWidget(QLabel('Open combo'))
            ctrl_row = QWidget()
            ctrl_row_layout = QHBoxLayout(ctrl_row)
            ctrl_row_layout.setContentsMargins(0, 0, 0, 0)
            ctrl_row_layout.setSpacing(6)
            _ctrl_settings = _load_controller_hotkey_settings()
            _ctrl_mask = _ctrl_settings.get("open_waypoints", 0x1002)
            self._wp_ctrl_display = QLineEdit(mask_to_name(_ctrl_mask))
            self._wp_ctrl_display.setReadOnly(True)
            self._wp_ctrl_display.setFixedHeight(32)
            self._wp_ctrl_display.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:1px solid #64748b;"
                "border-radius:6px;padding:4px 8px;}")
            self._wp_ctrl_record_btn = QPushButton('Record')
            self._wp_ctrl_record_btn.setFixedHeight(32)
            self._wp_ctrl_record_btn.setStyleSheet(
                "QPushButton{background:rgba(255,208,96,.18);"
                "border:1px solid rgba(255,208,96,.5);"
                "color:#ffd060;border-radius:6px;padding:0 10px;}"
                "QPushButton:hover{background:rgba(255,208,96,.3);}"
                "QPushButton:disabled{color:#666;border-color:#444;background:#222;}")
            ctrl_row_layout.addWidget(self._wp_ctrl_display)
            ctrl_row_layout.addWidget(self._wp_ctrl_record_btn)
            _wp_ctrl_clear_btn = QPushButton('Clear')
            _wp_ctrl_clear_btn.setFixedHeight(32)
            _wp_ctrl_clear_btn.setStyleSheet(
                "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
                "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
                "QPushButton:hover{background:rgba(255,80,80,.3);}")

            def _wp_ctrl_clear():
                self._wp_ctrl_display.setText('None')
                try:
                    existing = _load_controller_hotkey_settings()
                    existing["open_waypoints"] = 0
                    _save_controller_hotkey_settings(existing)
                except Exception:
                    pass

            _wp_ctrl_clear_btn.clicked.connect(_wp_ctrl_clear)
            ctrl_row_layout.addWidget(_wp_ctrl_clear_btn)
            active_layout[0].addWidget(ctrl_row)
            ctrl_note = QLabel(
                'Hold button combo on controller, then release. Restart required.')
            ctrl_note.setWordWrap(True)
            ctrl_note.setStyleSheet(
                'color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
            active_layout[0].addWidget(ctrl_note)

            wp_nav_help = QLabel(
                'In popup: D-pad Up/Down navigate, A selects/teleports, '
                'Y deletes, B closes.')
            wp_nav_help.setWordWrap(True)
            wp_nav_help.setStyleSheet(
                'color:#7c8db5; font:11px "Segoe UI"; margin-top:4px;')
            active_layout[0].addWidget(wp_nav_help)

            self._wp_ctrl_recording = False
            self._wp_ctrl_peak_mask = 0
            self._wp_ctrl_timer = QTimer()
            self._wp_ctrl_timer.setInterval(50)
            _get_xinput = _load_xinput_get_state()

            def _ctrl_poll():
                btns = _controller_buttons(_get_xinput)
                self._wp_ctrl_peak_mask |= btns
                if self._wp_ctrl_peak_mask and btns == 0:
                    saved_mask = self._wp_ctrl_peak_mask
                    self._wp_ctrl_timer.stop()
                    self._wp_ctrl_recording = False
                    self._wp_ctrl_record_btn.setText('Record')
                    self._wp_ctrl_record_btn.setEnabled(True)
                    self._wp_ctrl_display.setText(mask_to_name(saved_mask))
                    if _set_controller_hotkey_paused:
                        _set_controller_hotkey_paused(False)
                    try:
                        existing = _load_controller_hotkey_settings()
                        existing["open_waypoints"] = saved_mask
                        _save_controller_hotkey_settings(existing)
                    except Exception:
                        pass

            self._wp_ctrl_timer.timeout.connect(_ctrl_poll)

            def _start_record():
                if self._wp_ctrl_recording:
                    return
                self._wp_ctrl_recording = True
                self._wp_ctrl_peak_mask = 0
                self._wp_ctrl_display.setFocus()
                self._wp_ctrl_record_btn.setText('Recording...')
                self._wp_ctrl_record_btn.setEnabled(False)
                if _set_controller_hotkey_paused:
                    _set_controller_hotkey_paused(True)
                self._wp_ctrl_timer.start()

            self._wp_ctrl_record_btn.clicked.connect(_start_record)

        wp_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # TAB: Direction
        # ══════════════════════════════════════════════════════════════
        arrow_scroll, arrow_layout = _make_tab_scroll()
        tabs.addTab(arrow_scroll, 'Direction')

        section('Direction arrow', arrow_layout)
        option('rotateWithPlayer', 'Rotate map with player',
               'The map rotates to always show the player\'s forward direction at the top')
        option('rotateWithCamera', 'Rotate map with camera',
               'The map rotates using the camera heading received via WebSocket')
        self._checkboxes['rotateWithPlayer'].toggled.connect(
            lambda checked: checked and self._checkboxes['rotateWithCamera'].setChecked(False))
        self._checkboxes['rotateWithCamera'].toggled.connect(
            lambda checked: checked and self._checkboxes['rotateWithPlayer'].setChecked(False))
        active_layout[0].addWidget(QLabel('Source'))
        self._heading_combo = QComboBox()
        self._heading_combo.setFixedHeight(32)
        self._heading_combo.setStyleSheet(
            "QComboBox{background:#e2e8f0;color:#111827;border:1px solid #64748b;"
            "border-radius:6px;padding:4px 30px 4px 8px;}"
            "QComboBox:focus{border:1px solid #ffd060;}"
            "QComboBox::drop-down{subcontrol-origin:padding;subcontrol-position:top right;"
            "width:24px;border-left:1px solid #94a3b8;background:#cbd5e1;"
            "border-top-right-radius:6px;border-bottom-right-radius:6px;}"
            "QComboBox::down-arrow{image:none;width:0;height:0;"
            "border-left:4px solid transparent;border-right:4px solid transparent;"
            "border-top:5px solid #1f2937;}"
            "QComboBox QAbstractItemView{background:#f8fafc;color:#111827;"
            "selection-background-color:#ffd060;selection-color:#111827;"
            "border:1px solid #475569;}")
        self._heading_combo.addItem('Auto (entity \u2192 delta)', 'auto')
        self._heading_combo.addItem('Forward vector (entity)', 'entity')
        self._heading_combo.addItem('Position delta', 'delta')
        current_src = cfg.get('headingSource', SETTING_DEFAULTS['headingSource'])
        idx = self._heading_combo.findData(current_src)
        if idx >= 0:
            self._heading_combo.setCurrentIndex(idx)
        self._heading_combo.setToolTip(
            'auto: uses forward vector when available, falls back to delta\n'
            'entity: uses entity+0x80/0x88 only (works while standing still)\n'
            'delta: always computes from position difference')
        active_layout[0].addWidget(self._heading_combo)

        arrow_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # TAB: Performance
        # ══════════════════════════════════════════════════════════════
        perf_scroll, perf_layout = _make_tab_scroll()
        tabs.addTab(perf_scroll, 'Performance')

        section('Performance', perf_layout)
        option('disableGpuVsync', 'Disable GPU vsync (multi-monitor fix)',
               'Fixes FPS cap when using the overlay on a secondary monitor with '
               'a different refresh rate. Requires restart.')

        active_layout[0].addWidget(QLabel('Realtime transport'))
        self._realtime_transport_combo = QComboBox()
        self._realtime_transport_combo.setFixedHeight(32)
        self._realtime_transport_combo.setStyleSheet(
            "QComboBox{background:#e2e8f0;color:#111827;border:1px solid #64748b;"
            "border-radius:6px;padding:4px 30px 4px 8px;}"
            "QComboBox:focus{border:1px solid #ffd060;}"
            "QComboBox::drop-down{subcontrol-origin:padding;subcontrol-position:top right;"
            "width:24px;border-left:1px solid #94a3b8;background:#cbd5e1;"
            "border-top-right-radius:6px;border-bottom-right-radius:6px;}"
            "QComboBox::down-arrow{image:none;width:0;height:0;"
            "border-left:4px solid transparent;border-right:4px solid transparent;"
            "border-top:5px solid #1f2937;}"
            "QComboBox QAbstractItemView{background:#f8fafc;color:#111827;"
            "selection-background-color:#ffd060;selection-color:#111827;"
            "border:1px solid #475569;}")
        self._realtime_transport_combo.addItem('WebSocket', 'websocket')
        self._realtime_transport_combo.addItem('Native bridge (experimental)', 'native')
        current_transport = cfg.get(
            'realtimeTransport', SETTING_DEFAULTS['realtimeTransport'])
        idx = self._realtime_transport_combo.findData(current_transport)
        if idx < 0:
            idx = self._realtime_transport_combo.findData(
                SETTING_DEFAULTS['realtimeTransport'])
        self._realtime_transport_combo.setCurrentIndex(idx)
        active_layout[0].addWidget(self._realtime_transport_combo)
        rt_note = QLabel('Restart overlay or reload the map to apply transport changes.')
        rt_note.setWordWrap(True)
        rt_note.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
        active_layout[0].addWidget(rt_note)

        perf_layout.addStretch(1)

        # ── Botões Save/Cancel ─────────────────────────────────────────
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton('Cancel')
        cancel_btn.clicked.connect(self._on_cancel)
        save_btn = QPushButton('Save')
        save_btn.setObjectName('save')
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        outer_layout.addLayout(btn_row)

    def _on_cancel(self):
        if self.parent():
            self.parent().setWindowOpacity(self._original_opacity)
        self.reject()

    def get_settings(self):
        result = {key: cb.isChecked() for key, cb in self._checkboxes.items()}
        result['transparency'] = self._slider.value()
        result['browserZoom'] = self._zoom_slider.value()
        result['centerTeleportY'] = float(self._center_y.value())
        result['headingSource'] = self._heading_combo.currentData()
        result['realtimeTransport'] = self._realtime_transport_combo.currentData()
        result['nearbyThreshold'] = self._nearby_radius.value() / 1000.0
        result['mapIconScale'] = self._map_icon_scale.value() / 10.0
        self._save_nearby_hotkey()
        return result

    def _save_nearby_hotkey(self):
        seq_str = self._nearby_hk.keySequence().toString()
        parsed = _seq_str_to_vk(seq_str)
        wp_seq_str = self._waypoints_hk.keySequence().toString()
        wp_parsed = _seq_str_to_vk(wp_seq_str)
        ft_seq_str = self._focus_toggle_hk.keySequence().toString()
        ft_parsed = _seq_str_to_vk(ft_seq_str)
        try:
            try:
                with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                data = {}
            if parsed is not None:
                vk, mod = parsed
                data['open_nearby'] = {'vk': vk, 'mod': mod, 'enabled': True}
            else:
                data['open_nearby'] = {'vk': 0, 'mod': 0, 'enabled': False}
            if wp_parsed is not None:
                wp_vk, wp_mod = wp_parsed
                data['open_waypoints'] = {'vk': wp_vk, 'mod': wp_mod, 'enabled': True}
            else:
                data['open_waypoints'] = {'vk': 0, 'mod': 0, 'enabled': False}
            if ft_parsed is not None:
                ft_vk, ft_mod = ft_parsed
                data['focus_toggle'] = {'vk': ft_vk, 'mod': ft_mod, 'enabled': True}
            else:
                data['focus_toggle'] = {'vk': 0, 'mod': 0, 'enabled': False}
            os.makedirs(os.path.dirname(_HOTKEY_SETTINGS_FILE), exist_ok=True)
            with open(_HOTKEY_SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


# ── Janelas WebEngine auxiliares ─────────────────────────────────────

class PopupWebWindow(QMainWindow):
    closed_with_pos = pyqtSignal(object)  # emits QPoint when window closes

    def __init__(self, parent=None, title='CD Overlay'):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._is_nearby_popup = False
        self._is_waypoints_popup = False
        self.resize(300, 560)
        self._view = QWebEngineView(self)
        self._page = QWebEnginePage(self._view)
        self._view.setPage(self._page)
        self.setCentralWidget(self._view)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)

    def mark_nearby_popup(self):
        self._is_nearby_popup = True
        if _set_nearby_popup_hwnd:
            _set_nearby_popup_hwnd(int(self.winId()))

    def mark_waypoints_popup(self):
        self._is_waypoints_popup = True
        if _set_waypoints_popup_hwnd:
            _set_waypoints_popup_hwnd(int(self.winId()))

    def closeEvent(self, event):
        if self._is_nearby_popup and _set_nearby_popup_hwnd:
            _set_nearby_popup_hwnd(None)
        if self._is_waypoints_popup and _set_waypoints_popup_hwnd:
            _set_waypoints_popup_hwnd(None)
        self.closed_with_pos.emit(self.pos())
        super().closeEvent(event)
        cb = _on_popup_close_focus_game or focus_game_window
        QTimer.singleShot(50, cb)

    def page(self):
        return self._page


class InterceptPage(QWebEnginePage):
    login_needed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_windows = []
        self._last_popup_pos = None   # persists popup position within the session

    def createWindow(self, window_type):
        overlay = self.view().window() if self.view() else None
        popup = PopupWebWindow(overlay, 'CD Overlay')
        self._popup_windows.append(popup)
        popup.destroyed.connect(lambda _=None, p=popup: self._forget_popup(p))
        popup.closed_with_pos.connect(lambda pos: setattr(self, '_last_popup_pos', pos))
        popup._page.windowCloseRequested.connect(popup.close)
        popup._page.geometryChangeRequested.connect(
            lambda rect, p=popup: p.resize(rect.width(), rect.height()))
        popup._page.titleChanged.connect(
            lambda title, p=popup: p.mark_nearby_popup() if title == 'Nearby Locations' else
            p.mark_waypoints_popup() if title == 'CD Waypoints' else None)
        self._position_popup(popup, overlay)
        popup.show()
        QTimer.singleShot(0, lambda: self._activate_popup(popup))
        return popup.page()

    def _activate_popup(self, popup):
        if not popup or not popup.isVisible():
            return
        popup.raise_()
        hwnd = int(popup.winId())
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        fg_tid = ctypes.windll.user32.GetWindowThreadProcessId(fg_hwnd, None)
        my_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if fg_tid and fg_tid != my_tid:
            ctypes.windll.user32.AttachThreadInput(fg_tid, my_tid, True)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            ctypes.windll.user32.AttachThreadInput(fg_tid, my_tid, False)
        else:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        popup._view.setFocus()

    def _position_popup(self, popup, overlay):
        # If user already moved the popup this session, reuse that position
        if self._last_popup_pos is not None:
            popup.move(self._last_popup_pos)
            return

        if overlay is None:
            return

        screen = QApplication.screenAt(overlay.geometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return

        sg = screen.availableGeometry()
        pw, ph = popup.width(), popup.height()
        ox, oy = overlay.x(), overlay.y()
        ow, oh = overlay.width(), overlay.height()
        margin = 8

        sr = sg.x() + sg.width()   # screen right
        sb = sg.y() + sg.height()  # screen bottom

        def cy(y):  # clamp y inside screen
            return max(sg.y(), min(y, sb - ph))

        def cx(x):  # clamp x inside screen
            return max(sg.x(), min(x, sr - pw))

        # Right of overlay
        if ox + ow + margin + pw <= sr:
            popup.move(QPoint(ox + ow + margin, cy(oy)))
            return
        # Left of overlay
        if ox - margin - pw >= sg.x():
            popup.move(QPoint(ox - margin - pw, cy(oy)))
            return
        # Below overlay
        if oy + oh + margin + ph <= sb:
            popup.move(QPoint(cx(ox), oy + oh + margin))
            return
        # Above overlay
        if oy - margin - ph >= sg.y():
            popup.move(QPoint(cx(ox), oy - margin - ph))
            return

        # No space on any side — use the screen corner nearest the overlay
        ocx = ox + ow // 2
        ocy = oy + oh // 2
        x = sr - pw if ocx > sg.x() + sg.width() // 2 else sg.x()
        y = sb - ph if ocy > sg.y() + sg.height() // 2 else sg.y()
        popup.move(QPoint(x, y))

    def _forget_popup(self, popup):
        try:
            self._popup_windows.remove(popup)
        except ValueError:
            pass
        self.runJavaScript('waypointPopup = null; nearbyPopup = null; nearbyInputHandler = null;')

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == 'cdcompanion' and url.host() == 'login-needed':
            self.login_needed.emit()
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class LoginPrompt(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Login required')
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setStyleSheet("""
            QDialog   { background:#0f0f1a; border:1px solid #2d2d44; border-radius:8px; }
            QLabel    { color:#e2e8f0; font:13px 'Segoe UI'; }
            QPushButton { border-radius:5px; padding:6px 18px; font:13px 'Segoe UI'; }
            QPushButton#yes { background:#1db954; color:#fff; border:none; }
            QPushButton#yes:hover { background:#17a84a; }
            QPushButton#no  { background:#1a1a2e; color:#aaa; border:1px solid #2d2d44; }
            QPushButton#no:hover  { background:#252540; }
        """)
        self.setFixedWidth(300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        icon = QLabel('\U0001f512')
        icon.setStyleSheet('font:28px; background:transparent;')
        icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)

        msg = QLabel('You need to log in\nto access the map.')
        msg.setAlignment(Qt.AlignCenter)
        msg.setStyleSheet('color:#e2e8f0; font:13px "Segoe UI"; background:transparent;')
        layout.addWidget(msg)

        row = QHBoxLayout()
        no_btn = QPushButton('Not now')
        no_btn.setObjectName('no')
        no_btn.clicked.connect(self.reject)
        yes_btn = QPushButton('Go to login')
        yes_btn.setObjectName('yes')
        yes_btn.clicked.connect(self.accept)
        row.addWidget(no_btn)
        row.addWidget(yes_btn)
        layout.addLayout(row)


# ── Sinal para toggle thread-safe ────────────────────────────────────

class HotkeySignals(QObject):
    toggle = pyqtSignal()
    restart = pyqtSignal()
    focus_toggle = pyqtSignal()


# ── Barra customizada (arrastável, sem decoração do Windows) ──────────

class TitleBar(QWidget):
    HEIGHT = 30

    def __init__(self, parent):
        super().__init__(parent)
        self._drag_pos = None
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget   { background:#0f0f1a; }
QPushButton { background:transparent; border:none;
                          color:#666; font:13px; padding:0 6px; min-width:24px; }
            QPushButton:hover#btn_back     { color:#aaa; }
            QPushButton:hover#btn_settings { color:#ffd060; }
            QPushButton:hover#btn_hide     { color:#60b4ff; }
            QPushButton:hover#btn_maximize { color:#aaffaa; }
            QPushButton:hover#btn_close    { color:#ff6060; }
        """)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 4, 0)
        row.setSpacing(2)

        self._lbl = QLabel('\U0001f5fa')
        self._lbl.setStyleSheet('font:14px; background:transparent;')
        row.addWidget(self._lbl)
        row.addStretch(1)

        def icon_btn(name, text, tip):
            b = QPushButton(text)
            b.setObjectName(name)
            b.setToolTip(tip)
            b.setFixedSize(26, 26)
            row.addWidget(b)
            return b

        self.btn_back     = icon_btn('btn_back',     '\u25c0', 'Back')
        self.btn_settings = icon_btn('btn_settings', '\u2699', 'Settings')
        self.btn_hide     = icon_btn('btn_hide',     '\u2013', 'Hide  (Ctrl+Shift+M)')
        self.btn_maximize = icon_btn('btn_maximize', '\u25a1', 'Maximize')
        self.btn_close    = icon_btn('btn_close',    '\u2715', 'Close')

    def set_compact(self, compact):
        self._lbl.setVisible(not compact)
        self.btn_back.setVisible(not compact)

    def set_maximized(self, maximized):
        if maximized:
            self.btn_maximize.setText('\u274e')
            self.btn_maximize.setToolTip('Restore')
        else:
            self.btn_maximize.setText('\u25a1')
            self.btn_maximize.setToolTip('Maximize')

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            win = self.window()
            if hasattr(win, 'toggle_maximize') and not getattr(win, '_round_window', True):
                win.toggle_maximize()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            import ctypes
            hwnd = int(self.window().winId())
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(hwnd, 0x0112, 0xF012, 0)  # WM_SYSCOMMAND, SC_MOVE|HTCAPTION
