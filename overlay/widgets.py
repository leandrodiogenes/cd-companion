"""
Widgets auxiliares do overlay — extraídos de overlay_app.py.
Contém: SettingsDialog, _PopupWebWindow, _InterceptPage, _LoginPrompt,
         HotkeySignals, TitleBar.
"""

import ctypes
import ctypes.wintypes
import json
import os

from qtpy.QtCore import Qt, QPoint, QTimer, Signal, QObject
from qtpy.QtGui import QKeySequence
from qtpy.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QCheckBox, QPushButton, QSlider,
                             QMainWindow, QShortcut, QWidget, QKeySequenceEdit,
                             QScrollArea, QFrame, QLineEdit, QTabWidget,
                             QComboBox)
from qtpy.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

# Importa SETTING_DEFAULTS do módulo de configuração
from overlay.config_defaults import SETTING_DEFAULTS
import overlay.i18n as i18n

try:
    from server.main import (
        set_keyboard_hotkey_paused as _set_keyboard_hotkey_paused,
        set_nearby_hotkey_paused as _set_nearby_hotkey_paused,
        set_nearby_popup_hwnd as _set_nearby_popup_hwnd,
        set_controller_hotkey_paused as _set_controller_hotkey_paused,
        set_waypoints_popup_hwnd as _set_waypoints_popup_hwnd,
    )
except Exception:
    _set_keyboard_hotkey_paused = None
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

# ── Hotkey helpers ────────────────────────────────────────────────────
_SAVE_DIR = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'CD_Teleport')
_HOTKEY_SETTINGS_FILE = os.path.join(_SAVE_DIR, 'cd_hotkeys.json')
_TELEPORT_MARKER_DEFAULT = {'vk': 0x74, 'mod': 0}      # F5
_TELEPORT_ABORT_DEFAULT = {'vk': 0x74, 'mod': 0x10}    # Shift+F5
_OPEN_NEARBY_DEFAULT = {'vk': 0x4E, 'mod': 0x10}  # Shift+N

_VK_MAP = {f'F{i}': 0x6F + i for i in range(1, 25)}
_VK_MAP.update({chr(c): c for c in range(0x41, 0x5B)})   # A–Z
_VK_MAP.update({str(d): 0x30 + d for d in range(10)})     # 0–9
_VK_MAP.update({f'Num+{d}': 0x60 + d for d in range(10)})
_VK_MAP.update({f'Num{d}': 0x60 + d for d in range(10)})
_VK_MAP.update({
    'Home': 0x24, 'End': 0x23, 'Ins': 0x2D, 'Del': 0x2E,
    'PgUp': 0x21, 'PgDown': 0x22,
    'Up': 0x26, 'Down': 0x28, 'Left': 0x25, 'Right': 0x27,
    'Num+*': 0x6A, 'Num++': 0x6B, 'Num+-': 0x6D,
    'Num+.': 0x6E, 'Num+/': 0x6F,
})
_MOD_MAP  = {'Shift': 0x10, 'Ctrl': 0x11, 'Alt': 0x12}
_VK_DISP  = {v: k for k, v in _VK_MAP.items()}
_VK_DISP.update({0x60 + d: f'Num+{d}' for d in range(10)})
_VK_DISP.update({0x6A: 'Num+*', 0x6B: 'Num++', 0x6D: 'Num+-', 0x6E: 'Num+.', 0x6F: 'Num+/'})
_MOD_DISP = {0x10: 'Shift', 0x11: 'Ctrl', 0x12: 'Alt'}

# Modifier bitmask for RegisterHotKey (different from VK codes above)
_MOD_WIN_MAP = {'Ctrl': 0x0002, 'Shift': 0x0004, 'Alt': 0x0001}
_MOD_WIN_DISP_ORDER = [(0x0002, 'Ctrl'), (0x0004, 'Shift'), (0x0001, 'Alt')]


def _seq_parts(seq_str):
    seq_str = seq_str.split(',')[0].strip()
    parts = [p.strip() for p in seq_str.split('+')]
    if 'Num' in parts:
        num_idx = parts.index('Num')
        return 'Num+' + '+'.join(parts[num_idx + 1:]), parts[:num_idx]
    return parts[-1], parts[:-1]


def _seq_str_to_vk_win(seq_str):
    """'Ctrl+Shift+M' → (vk, mod_win) for RegisterHotKey. Supports multiple modifiers."""
    key, mod_parts = _seq_parts(seq_str)
    vk = _VK_MAP.get(key.upper() if len(key) == 1 else key)
    if vk is None:
        return None
    mod_win = 0
    for p in mod_parts:
        m = _MOD_WIN_MAP.get(p)
        if m:
            mod_win |= m
    return vk, mod_win


def _vk_win_to_seq_str(vk, mod_win):
    """(vk, mod_win) → 'Ctrl+Shift+M' string for QKeySequence."""
    key = _VK_DISP.get(vk, f'VK{vk:#04x}')
    parts = [name for bit, name in _MOD_WIN_DISP_ORDER if mod_win & bit]
    parts.append(key)
    return '+'.join(parts)


def _vk_to_seq_str(vk, mods):
    """(vk, mods) → 'Shift+N' or 'Ctrl+Shift+N' for QKeySequence.
    mods can be int (single VK mod) or list of VK mods."""
    key = _VK_DISP.get(vk, f'VK{vk:#04x}')
    if isinstance(mods, int):
        mods = [mods] if mods else []
    parts = [_MOD_DISP[m] for m in mods if m in _MOD_DISP]
    parts.append(key)
    return '+'.join(parts)


def _seq_str_to_vk(seq_str):
    """'Shift+N' or 'Ctrl+Shift+N' → (vk, mods_list). Returns None if invalid."""
    key, mod_parts = _seq_parts(seq_str)
    vk = _VK_MAP.get(key.upper() if len(key) == 1 else key)
    if vk is None:
        return None
    mods = [_MOD_MAP[m] for m in mod_parts if m in _MOD_MAP]
    return vk, mods


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
    font:13px 'Segoe UI';
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
        self.setWindowTitle(i18n.t('settings.title'))
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
        tabs.addTab(map_scroll, i18n.t('settings.tab_map'))

        section(i18n.t('settings.section_on_map_load'), map_layout)
        option('restoreLastPosition', i18n.t('settings.option_restore_last_position'),
               i18n.t('settings.option_restore_last_position_desc'))
        option('autoHideFound', i18n.t('settings.option_auto_hide_found'),
               i18n.t('settings.option_auto_hide_found_desc'))
        option('autoHideLeftSidebar', i18n.t('settings.option_auto_hide_left_sidebar'),
               i18n.t('settings.option_auto_hide_left_sidebar_desc'))
        option('autoHideRightSidebar', i18n.t('settings.option_auto_hide_right_sidebar'),
               i18n.t('settings.option_auto_hide_right_sidebar_desc'))

        section(i18n.t('settings.section_map_icons'), map_layout)
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
        slider_block(i18n.t('settings.slider_icon_scale'), self._map_icon_scale_val, self._map_icon_scale,
                     i18n.t('settings.slider_icon_scale_desc'))

        map_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # TAB: Window
        # ══════════════════════════════════════════════════════════════
        win_scroll, win_layout = _make_tab_scroll()
        tabs.addTab(win_scroll, i18n.t('settings.tab_window'))

        section(i18n.t('settings.section_language'), win_layout)
        active_layout[0].addWidget(QLabel(i18n.t('settings.language_label')))
        self._lang_combo = QComboBox()
        self._lang_combo.setFixedHeight(32)
        self._lang_combo.setStyleSheet(
            "QComboBox{background:#e2e8f0;color:#111827;border:1px solid #64748b;"
            "border-radius:6px;padding:4px 30px 4px 8px;font:13px 'Segoe UI';}"
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
        if i18n._instance is not None:
            lang_entries = i18n._instance.list_available()
        else:
            lang_entries = [{'code': 'en', 'name': 'English'}]
        for entry in lang_entries:
            self._lang_combo.addItem(entry['name'], entry['code'])
        current_lang = cfg.get('language', 'en')
        lang_idx = self._lang_combo.findData(current_lang)
        if lang_idx >= 0:
            self._lang_combo.setCurrentIndex(lang_idx)
        active_layout[0].addWidget(self._lang_combo)

        section(i18n.t('settings.section_window'), win_layout)
        option('roundWindow', i18n.t('settings.option_round_window'),
               i18n.t('settings.option_round_window_desc'))
        option('followGameWindow', i18n.t('settings.option_follow_game_window'),
               i18n.t('settings.option_follow_game_window_desc'))
        option('alwaysShowTitleBar', i18n.t('settings.option_always_show_title_bar'),
               i18n.t('settings.option_always_show_title_bar_desc'))

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
        slider_block(i18n.t('settings.slider_transparency'), transp_val, self._slider)

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
        slider_block(i18n.t('settings.slider_browser_zoom'), zoom_val, self._zoom_slider,
                     i18n.t('settings.slider_browser_zoom_desc'))

        # Show/hide overlay hotkey
        section(i18n.t('settings.section_show_hide_overlay'), win_layout)
        to_hk_lbl = QLabel(i18n.t('settings.keyboard_hotkey_label'))
        self._to_hk = QKeySequenceEdit()
        self._to_hk.setFixedHeight(32)
        self._to_hk.setAttribute(Qt.WA_StyledBackground, True)
        self._to_hk.setStyleSheet(
            "QKeySequenceEdit{background:#e2e8f0;color:#111827;"
            "border:1px solid #64748b;border-radius:6px;padding:4px 8px;"
            "font:13px 'Segoe UI';"
            "selection-background-color:#ffd060;selection-color:#111827;}"
            "QKeySequenceEdit:focus{border:1px solid #ffd060;}")
        _to_hk_line = self._to_hk.findChild(QLineEdit)
        if _to_hk_line:
            _to_hk_line.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:none;"
                "font:13px 'Segoe UI';"
                "selection-background-color:#ffd060;selection-color:#111827;}")
        self._to_hk.setToolTip(i18n.t('settings.hotkey_restart_tooltip'))
        self._to_hk._hk_finalized = False

        def _to_on_seq_changed(seq):
            first = seq.toString().split(', ')[0]
            if first != seq.toString():
                self._to_hk.setKeySequence(QKeySequence(first))
            self._to_hk._hk_finalized = True

        def _to_hk_key_press(e):
            key = e.key()
            mods = e.modifiers()
            if key not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
                if self._to_hk._hk_finalized:
                    self._to_hk.clear()
                    self._to_hk._hk_finalized = False
                if mods == Qt.NoModifier and key < 0x1000000:
                    seq_str = QKeySequence(key).toString()
                    if seq_str and _seq_str_to_vk(seq_str) is not None:
                        self._to_hk.setKeySequence(QKeySequence(key))
                        self._to_hk._hk_finalized = True
                        return
            QKeySequenceEdit.keyPressEvent(self._to_hk, e)

        self._to_hk.keySequenceChanged.connect(_to_on_seq_changed)
        self._to_hk.keyPressEvent = _to_hk_key_press

        def _to_hk_focus_in(e):
            if _set_nearby_hotkey_paused: _set_nearby_hotkey_paused(True)
            QKeySequenceEdit.focusInEvent(self._to_hk, e)
        def _to_hk_focus_out(e):
            if _set_nearby_hotkey_paused: _set_nearby_hotkey_paused(False)
            QKeySequenceEdit.focusOutEvent(self._to_hk, e)
        self._to_hk.focusInEvent = _to_hk_focus_in
        self._to_hk.focusOutEvent = _to_hk_focus_out

        to_hk_vk = 0x4D   # M
        to_hk_mod_win = 0x0006  # Ctrl+Shift
        try:
            with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as _f:
                _to_hk_data = json.load(_f).get('toggle_overlay', {})
                to_hk_vk = _to_hk_data.get('vk', to_hk_vk)
                to_hk_mod_win = _to_hk_data.get('mod_win', to_hk_mod_win)
        except Exception:
            pass
        self._to_hk.setKeySequence(QKeySequence(_vk_win_to_seq_str(to_hk_vk, to_hk_mod_win)))

        to_hk_row = QWidget()
        to_hk_row_layout = QHBoxLayout(to_hk_row)
        to_hk_row_layout.setContentsMargins(0, 0, 0, 0)
        to_hk_row_layout.setSpacing(6)
        to_hk_row_layout.addWidget(self._to_hk)
        to_hk_clear_btn = QPushButton(i18n.t('settings.btn_clear'))
        to_hk_clear_btn.setFixedHeight(32)
        to_hk_clear_btn.setStyleSheet(
            "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
            "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
            "QPushButton:hover{background:rgba(255,80,80,.3);}")
        to_hk_clear_btn.clicked.connect(lambda: self._to_hk.clear())
        to_hk_row_layout.addWidget(to_hk_clear_btn)
        active_layout[0].addWidget(to_hk_lbl)
        active_layout[0].addWidget(to_hk_row)

        if _HAS_CONTROLLER_HOTKEYS:
            active_layout[0].addWidget(QLabel(i18n.t('settings.controller_combo_label')))
            to_ctrl_row = QWidget()
            to_ctrl_row_layout = QHBoxLayout(to_ctrl_row)
            to_ctrl_row_layout.setContentsMargins(0, 0, 0, 0)
            to_ctrl_row_layout.setSpacing(6)
            _to_ctrl_settings = _load_controller_hotkey_settings()
            _to_ctrl_mask = _to_ctrl_settings.get("toggle_overlay", 0)
            self._to_ctrl_display = QLineEdit(mask_to_name(_to_ctrl_mask))
            self._to_ctrl_display.setReadOnly(True)
            self._to_ctrl_display.setFixedHeight(32)
            self._to_ctrl_display.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:1px solid #64748b;"
                "border-radius:6px;padding:4px 8px;font:13px 'Segoe UI';}")
            self._to_ctrl_record_btn = QPushButton(i18n.t('settings.btn_record'))
            self._to_ctrl_record_btn.setFixedHeight(32)
            self._to_ctrl_record_btn.setStyleSheet(
                "QPushButton{background:rgba(255,208,96,.18);"
                "border:1px solid rgba(255,208,96,.5);"
                "color:#ffd060;border-radius:6px;padding:0 10px;}"
                "QPushButton:hover{background:rgba(255,208,96,.3);}"
                "QPushButton:disabled{color:#666;border-color:#444;background:#222;}")
            _to_ctrl_clear_btn = QPushButton(i18n.t('settings.btn_clear'))
            _to_ctrl_clear_btn.setFixedHeight(32)
            _to_ctrl_clear_btn.setStyleSheet(
                "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
                "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
                "QPushButton:hover{background:rgba(255,80,80,.3);}")

            def _to_ctrl_clear():
                self._to_ctrl_display.setText(i18n.t('settings.display_none'))
                try:
                    existing = _load_controller_hotkey_settings()
                    existing["toggle_overlay"] = 0
                    _save_controller_hotkey_settings(existing)
                except Exception:
                    pass

            _to_ctrl_clear_btn.clicked.connect(_to_ctrl_clear)
            to_ctrl_row_layout.addWidget(self._to_ctrl_display)
            to_ctrl_row_layout.addWidget(self._to_ctrl_record_btn)
            to_ctrl_row_layout.addWidget(_to_ctrl_clear_btn)
            active_layout[0].addWidget(to_ctrl_row)

            self._to_ctrl_recording = False
            self._to_ctrl_peak_mask = 0
            self._to_ctrl_timer = QTimer()
            self._to_ctrl_timer.setInterval(50)
            _to_get_xinput = _load_xinput_get_state()

            def _to_ctrl_poll():
                btns = _controller_buttons(_to_get_xinput)
                self._to_ctrl_peak_mask |= btns
                if self._to_ctrl_peak_mask and btns == 0:
                    saved_mask = self._to_ctrl_peak_mask
                    self._to_ctrl_timer.stop()
                    self._to_ctrl_recording = False
                    self._to_ctrl_record_btn.setText(i18n.t('settings.btn_record'))
                    self._to_ctrl_record_btn.setEnabled(True)
                    self._to_ctrl_display.setText(mask_to_name(saved_mask))
                    if _set_controller_hotkey_paused:
                        _set_controller_hotkey_paused(False)
                    try:
                        existing = _load_controller_hotkey_settings()
                        existing["toggle_overlay"] = saved_mask
                        _save_controller_hotkey_settings(existing)
                    except Exception:
                        pass

            self._to_ctrl_timer.timeout.connect(_to_ctrl_poll)

            def _to_start_record():
                if self._to_ctrl_recording:
                    return
                self._to_ctrl_recording = True
                self._to_ctrl_peak_mask = 0
                self._to_ctrl_display.setFocus()
                self._to_ctrl_record_btn.setText(i18n.t('settings.btn_recording'))
                self._to_ctrl_record_btn.setEnabled(False)
                if _set_controller_hotkey_paused:
                    _set_controller_hotkey_paused(True)
                self._to_ctrl_timer.start()

            self._to_ctrl_record_btn.clicked.connect(_to_start_record)

        to_note = QLabel(i18n.t('settings.note_toggle_overlay'))
        to_note.setWordWrap(True)
        to_note.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
        active_layout[0].addWidget(to_note)

        # Focus toggle hotkey
        section(i18n.t('settings.section_focus_toggle'), win_layout)
        ft_hk_lbl = QLabel(i18n.t('settings.keyboard_hotkey_label'))
        self._focus_toggle_hk = QKeySequenceEdit()
        self._focus_toggle_hk.setFixedHeight(32)
        self._focus_toggle_hk.setAttribute(Qt.WA_StyledBackground, True)
        self._focus_toggle_hk.setStyleSheet(
            "QKeySequenceEdit{background:#e2e8f0;color:#111827;"
            "border:1px solid #64748b;border-radius:6px;padding:4px 8px;"
            "font:13px 'Segoe UI';"
            "selection-background-color:#ffd060;selection-color:#111827;}"
            "QKeySequenceEdit:focus{border:1px solid #ffd060;}")
        ft_hk_line = self._focus_toggle_hk.findChild(QLineEdit)
        if ft_hk_line:
            ft_hk_line.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:none;"
                "font:13px 'Segoe UI';"
                "selection-background-color:#ffd060;selection-color:#111827;}")
        self._focus_toggle_hk.setToolTip(i18n.t('settings.hotkey_restart_tooltip'))
        self._focus_toggle_hk._hk_finalized = False

        def _ft_on_seq_changed(seq):
            first = seq.toString().split(', ')[0]
            if first != seq.toString():
                self._focus_toggle_hk.setKeySequence(QKeySequence(first))
            self._focus_toggle_hk._hk_finalized = True

        def _ft_hk_key_press(e):
            key = e.key()
            mods = e.modifiers()
            if key not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
                if self._focus_toggle_hk._hk_finalized:
                    self._focus_toggle_hk.clear()
                    self._focus_toggle_hk._hk_finalized = False
                if mods == Qt.NoModifier and key < 0x1000000:
                    seq_str = QKeySequence(key).toString()
                    if seq_str and _seq_str_to_vk(seq_str) is not None:
                        self._focus_toggle_hk.setKeySequence(QKeySequence(key))
                        self._focus_toggle_hk._hk_finalized = True
                        return
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
        ft_hk_mods = []
        try:
            with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as _f:
                _ft_hk_data = json.load(_f).get('focus_toggle', {})
                ft_hk_vk = _ft_hk_data.get('vk', ft_hk_vk)
                if 'mods' in _ft_hk_data:
                    ft_hk_mods = _ft_hk_data['mods']
                elif _ft_hk_data.get('mod'):
                    ft_hk_mods = [_ft_hk_data['mod']]
        except Exception:
            pass
        if ft_hk_vk:
            self._focus_toggle_hk.setKeySequence(QKeySequence(_vk_to_seq_str(ft_hk_vk, ft_hk_mods)))
        ft_hk_row = QWidget()
        ft_hk_row_layout = QHBoxLayout(ft_hk_row)
        ft_hk_row_layout.setContentsMargins(0, 0, 0, 0)
        ft_hk_row_layout.setSpacing(6)
        ft_hk_row_layout.addWidget(self._focus_toggle_hk)
        ft_hk_clear_btn = QPushButton(i18n.t('settings.btn_clear'))
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
            active_layout[0].addWidget(QLabel(i18n.t('settings.controller_combo_label')))
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
                "border-radius:6px;padding:4px 8px;font:13px 'Segoe UI';}")
            self._ft_ctrl_record_btn = QPushButton(i18n.t('settings.btn_record'))
            self._ft_ctrl_record_btn.setFixedHeight(32)
            self._ft_ctrl_record_btn.setStyleSheet(
                "QPushButton{background:rgba(255,208,96,.18);"
                "border:1px solid rgba(255,208,96,.5);"
                "color:#ffd060;border-radius:6px;padding:0 10px;}"
                "QPushButton:hover{background:rgba(255,208,96,.3);}"
                "QPushButton:disabled{color:#666;border-color:#444;background:#222;}")
            _ft_ctrl_clear_btn = QPushButton(i18n.t('settings.btn_clear'))
            _ft_ctrl_clear_btn.setFixedHeight(32)
            _ft_ctrl_clear_btn.setStyleSheet(
                "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
                "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
                "QPushButton:hover{background:rgba(255,80,80,.3);}")

            def _ft_ctrl_clear():
                self._ft_ctrl_display.setText(i18n.t('settings.display_none'))
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

            ft_note = QLabel(i18n.t('settings.note_focus_toggle'))
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
                    self._ft_ctrl_record_btn.setText(i18n.t('settings.btn_record'))
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
                self._ft_ctrl_record_btn.setText(i18n.t('settings.btn_recording'))
                self._ft_ctrl_record_btn.setEnabled(False)
                if _set_controller_hotkey_paused:
                    _set_controller_hotkey_paused(True)
                self._ft_ctrl_timer.start()

            self._ft_ctrl_record_btn.clicked.connect(_ft_start_record)

        # Toggle window shape hotkey
        section(i18n.t('settings.section_toggle_shape'), win_layout)
        ts_hk_lbl = QLabel(i18n.t('settings.keyboard_hotkey_label'))
        self._ts_hk = QKeySequenceEdit()
        self._ts_hk.setFixedHeight(32)
        self._ts_hk.setAttribute(Qt.WA_StyledBackground, True)
        self._ts_hk.setStyleSheet(
            "QKeySequenceEdit{background:#e2e8f0;color:#111827;"
            "border:1px solid #64748b;border-radius:6px;padding:4px 8px;"
            "font:13px 'Segoe UI';"
            "selection-background-color:#ffd060;selection-color:#111827;}"
            "QKeySequenceEdit:focus{border:1px solid #ffd060;}")
        _ts_hk_line = self._ts_hk.findChild(QLineEdit)
        if _ts_hk_line:
            _ts_hk_line.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:none;"
                "font:13px 'Segoe UI';"
                "selection-background-color:#ffd060;selection-color:#111827;}")
        self._ts_hk.setToolTip(i18n.t('settings.hotkey_restart_tooltip'))
        self._ts_hk._hk_finalized = False

        def _ts_on_seq_changed(seq):
            first = seq.toString().split(', ')[0]
            if first != seq.toString():
                self._ts_hk.setKeySequence(QKeySequence(first))
            self._ts_hk._hk_finalized = True

        def _ts_hk_key_press(e):
            key = e.key()
            mods = e.modifiers()
            if key not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
                if self._ts_hk._hk_finalized:
                    self._ts_hk.clear()
                    self._ts_hk._hk_finalized = False
                if mods == Qt.NoModifier and key < 0x1000000:
                    seq_str = QKeySequence(key).toString()
                    if seq_str and _seq_str_to_vk(seq_str) is not None:
                        self._ts_hk.setKeySequence(QKeySequence(key))
                        self._ts_hk._hk_finalized = True
                        return
            QKeySequenceEdit.keyPressEvent(self._ts_hk, e)

        self._ts_hk.keySequenceChanged.connect(_ts_on_seq_changed)
        self._ts_hk.keyPressEvent = _ts_hk_key_press

        def _ts_hk_focus_in(e):
            if _set_nearby_hotkey_paused: _set_nearby_hotkey_paused(True)
            QKeySequenceEdit.focusInEvent(self._ts_hk, e)
        def _ts_hk_focus_out(e):
            if _set_nearby_hotkey_paused: _set_nearby_hotkey_paused(False)
            QKeySequenceEdit.focusOutEvent(self._ts_hk, e)
        self._ts_hk.focusInEvent = _ts_hk_focus_in
        self._ts_hk.focusOutEvent = _ts_hk_focus_out

        ts_hk_vk = 0
        ts_hk_mod_win = 0
        try:
            with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as _f:
                _ts_hk_data = json.load(_f).get('toggle_shape', {})
                ts_hk_vk = _ts_hk_data.get('vk', ts_hk_vk)
                ts_hk_mod_win = _ts_hk_data.get('mod_win', ts_hk_mod_win)
        except Exception:
            pass
        if ts_hk_vk:
            self._ts_hk.setKeySequence(QKeySequence(_vk_win_to_seq_str(ts_hk_vk, ts_hk_mod_win)))

        ts_hk_row = QWidget()
        ts_hk_row_layout = QHBoxLayout(ts_hk_row)
        ts_hk_row_layout.setContentsMargins(0, 0, 0, 0)
        ts_hk_row_layout.setSpacing(6)
        ts_hk_row_layout.addWidget(self._ts_hk)
        ts_hk_clear_btn = QPushButton(i18n.t('settings.btn_clear'))
        ts_hk_clear_btn.setFixedHeight(32)
        ts_hk_clear_btn.setStyleSheet(
            "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
            "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
            "QPushButton:hover{background:rgba(255,80,80,.3);}")
        ts_hk_clear_btn.clicked.connect(lambda: self._ts_hk.clear())
        ts_hk_row_layout.addWidget(ts_hk_clear_btn)
        active_layout[0].addWidget(ts_hk_lbl)
        active_layout[0].addWidget(ts_hk_row)

        if _HAS_CONTROLLER_HOTKEYS:
            active_layout[0].addWidget(QLabel(i18n.t('settings.controller_combo_label')))
            ts_ctrl_row = QWidget()
            ts_ctrl_row_layout = QHBoxLayout(ts_ctrl_row)
            ts_ctrl_row_layout.setContentsMargins(0, 0, 0, 0)
            ts_ctrl_row_layout.setSpacing(6)
            _ts_ctrl_settings = _load_controller_hotkey_settings()
            _ts_ctrl_mask = _ts_ctrl_settings.get("toggle_shape", 0)
            self._ts_ctrl_display = QLineEdit(mask_to_name(_ts_ctrl_mask))
            self._ts_ctrl_display.setReadOnly(True)
            self._ts_ctrl_display.setFixedHeight(32)
            self._ts_ctrl_display.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:1px solid #64748b;"
                "border-radius:6px;padding:4px 8px;font:13px 'Segoe UI';}")
            self._ts_ctrl_record_btn = QPushButton(i18n.t('settings.btn_record'))
            self._ts_ctrl_record_btn.setFixedHeight(32)
            self._ts_ctrl_record_btn.setStyleSheet(
                "QPushButton{background:rgba(255,208,96,.18);"
                "border:1px solid rgba(255,208,96,.5);"
                "color:#ffd060;border-radius:6px;padding:0 10px;}"
                "QPushButton:hover{background:rgba(255,208,96,.3);}"
                "QPushButton:disabled{color:#666;border-color:#444;background:#222;}")
            _ts_ctrl_clear_btn = QPushButton(i18n.t('settings.btn_clear'))
            _ts_ctrl_clear_btn.setFixedHeight(32)
            _ts_ctrl_clear_btn.setStyleSheet(
                "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
                "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
                "QPushButton:hover{background:rgba(255,80,80,.3);}")

            def _ts_ctrl_clear():
                self._ts_ctrl_display.setText(i18n.t('settings.display_none'))
                try:
                    existing = _load_controller_hotkey_settings()
                    existing["toggle_shape"] = 0
                    _save_controller_hotkey_settings(existing)
                except Exception:
                    pass

            _ts_ctrl_clear_btn.clicked.connect(_ts_ctrl_clear)
            ts_ctrl_row_layout.addWidget(self._ts_ctrl_display)
            ts_ctrl_row_layout.addWidget(self._ts_ctrl_record_btn)
            ts_ctrl_row_layout.addWidget(_ts_ctrl_clear_btn)
            active_layout[0].addWidget(ts_ctrl_row)

            self._ts_ctrl_recording = False
            self._ts_ctrl_peak_mask = 0
            self._ts_ctrl_timer = QTimer()
            self._ts_ctrl_timer.setInterval(50)
            _ts_get_xinput = _load_xinput_get_state()

            def _ts_ctrl_poll():
                btns = _controller_buttons(_ts_get_xinput)
                self._ts_ctrl_peak_mask |= btns
                if self._ts_ctrl_peak_mask and btns == 0:
                    saved_mask = self._ts_ctrl_peak_mask
                    self._ts_ctrl_timer.stop()
                    self._ts_ctrl_recording = False
                    self._ts_ctrl_record_btn.setText(i18n.t('settings.btn_record'))
                    self._ts_ctrl_record_btn.setEnabled(True)
                    self._ts_ctrl_display.setText(mask_to_name(saved_mask))
                    if _set_controller_hotkey_paused:
                        _set_controller_hotkey_paused(False)
                    try:
                        existing = _load_controller_hotkey_settings()
                        existing["toggle_shape"] = saved_mask
                        _save_controller_hotkey_settings(existing)
                    except Exception:
                        pass

            self._ts_ctrl_timer.timeout.connect(_ts_ctrl_poll)

            def _ts_start_record():
                if self._ts_ctrl_recording:
                    return
                self._ts_ctrl_recording = True
                self._ts_ctrl_peak_mask = 0
                self._ts_ctrl_display.setFocus()
                self._ts_ctrl_record_btn.setText(i18n.t('settings.btn_recording'))
                self._ts_ctrl_record_btn.setEnabled(False)
                if _set_controller_hotkey_paused:
                    _set_controller_hotkey_paused(True)
                self._ts_ctrl_timer.start()

            self._ts_ctrl_record_btn.clicked.connect(_ts_start_record)

        ts_note = QLabel(i18n.t('settings.note_toggle_shape'))
        ts_note.setWordWrap(True)
        ts_note.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
        active_layout[0].addWidget(ts_note)

        win_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # TAB: Teleport
        # ══════════════════════════════════════════════════════════════
        tp_scroll, tp_layout = _make_tab_scroll()
        tabs.addTab(tp_scroll, i18n.t('settings.tab_teleport'))

        section(i18n.t('settings.section_teleport'), tp_layout)
        option('teleportEnabled', i18n.t('settings.option_teleport_enabled'),
               i18n.t('settings.option_teleport_enabled_desc'))
        option('useSharedMemoryEntity', i18n.t('settings.option_shared_memory'),
               i18n.t('settings.option_shared_memory_desc'))

        section(i18n.t('settings.section_keyboard_hotkey'), tp_layout)
        tp_marker_hk_lbl = QLabel(i18n.t('settings.teleport_marker_hotkey_label'))
        self._tp_marker_hk = self._make_hotkey_editor()
        tp_marker_hk_vk = _TELEPORT_MARKER_DEFAULT['vk']
        tp_marker_hk_mods = []
        try:
            with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as _f:
                _tp_marker_data = json.load(_f).get('teleport_marker', {})
                tp_marker_hk_vk = _tp_marker_data.get('vk', tp_marker_hk_vk)
                if 'mods' in _tp_marker_data:
                    tp_marker_hk_mods = _tp_marker_data['mods']
                elif 'mod' in _tp_marker_data:
                    tp_marker_hk_mods = [_tp_marker_data['mod']] if _tp_marker_data['mod'] else []
        except Exception:
            pass
        self._tp_marker_hk.setKeySequence(QKeySequence(_vk_to_seq_str(
            tp_marker_hk_vk, tp_marker_hk_mods)))
        active_layout[0].addWidget(tp_marker_hk_lbl)
        active_layout[0].addWidget(self._make_hotkey_row(self._tp_marker_hk))

        tp_abort_hk_lbl = QLabel(i18n.t('settings.teleport_abort_hotkey_label'))
        self._tp_abort_hk = self._make_hotkey_editor()
        tp_abort_hk_vk = _TELEPORT_ABORT_DEFAULT['vk']
        tp_abort_hk_mods = [_TELEPORT_ABORT_DEFAULT['mod']]
        try:
            with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as _f:
                _tp_abort_data = json.load(_f).get('abort', {})
                tp_abort_hk_vk = _tp_abort_data.get('vk', tp_abort_hk_vk)
                if 'mods' in _tp_abort_data:
                    tp_abort_hk_mods = _tp_abort_data['mods']
                elif 'mod' in _tp_abort_data:
                    tp_abort_hk_mods = [_tp_abort_data['mod']] if _tp_abort_data['mod'] else []
        except Exception:
            pass
        self._tp_abort_hk.setKeySequence(QKeySequence(_vk_to_seq_str(
            tp_abort_hk_vk, tp_abort_hk_mods)))
        active_layout[0].addWidget(tp_abort_hk_lbl)
        active_layout[0].addWidget(self._make_hotkey_row(self._tp_abort_hk))

        tp_hk_note = QLabel(i18n.t('settings.note_hotkey_restart'))
        tp_hk_note.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
        active_layout[0].addWidget(tp_hk_note)

        section(i18n.t('settings.section_teleport'), tp_layout)
        self._center_y_value = QLabel()
        self._center_y_value.setStyleSheet(
            "color:#ffd060; font:13px 'Consolas'; min-width:48px;")
        self._center_y = NoWheelSlider(Qt.Horizontal)
        self._center_y.setRange(-5000, 5000)
        self._center_y.setSingleStep(10)
        self._center_y.setPageStep(100)
        self._center_y.setValue(int(float(cfg.get(
            'centerTeleportY', SETTING_DEFAULTS['centerTeleportY']))))
        self._center_y.setToolTip(i18n.t('settings.slider_center_tp_y_desc'))
        self._center_y_value.setText(str(self._center_y.value()))
        self._center_y.valueChanged.connect(
            lambda value: self._center_y_value.setText(str(value)))
        slider_block(i18n.t('settings.slider_center_tp_y'), self._center_y_value, self._center_y,
                     i18n.t('settings.slider_center_tp_y_desc'))

        tp_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # TAB: Nearby
        # ══════════════════════════════════════════════════════════════
        nb_scroll, nb_layout = _make_tab_scroll()
        tabs.addTab(nb_scroll, i18n.t('settings.tab_nearby'))

        section(i18n.t('settings.section_nearby'), nb_layout)
        option('nearbyControlsEnabled', i18n.t('settings.option_nearby_controls_enabled'),
               i18n.t('settings.option_nearby_controls_enabled_desc'))
        option('nearbyRespectMapVisibility', i18n.t('settings.option_nearby_respect_map_visibility'),
               i18n.t('settings.option_nearby_respect_map_visibility_desc'))
        nearby_help = QLabel(i18n.t('settings.help_nearby_controls'))
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
        slider_block(i18n.t('settings.slider_nearby_radius'), self._nearby_radius_val, self._nearby_radius)

        # Nearby hotkey
        section(i18n.t('settings.section_hotkey'), nb_layout)
        hk_lbl = QLabel(i18n.t('settings.open_hotkey_label'))
        self._nearby_hk = QKeySequenceEdit()
        self._nearby_hk.setFixedHeight(32)
        self._nearby_hk.setAttribute(Qt.WA_StyledBackground, True)
        self._nearby_hk.setStyleSheet(
            "QKeySequenceEdit{background:#e2e8f0;color:#111827;"
            "border:1px solid #64748b;border-radius:6px;padding:4px 8px;"
            "font:13px 'Segoe UI';"
            "selection-background-color:#ffd060;selection-color:#111827;}"
            "QKeySequenceEdit:focus{border:1px solid #ffd060;}")
        hk_line = self._nearby_hk.findChild(QLineEdit)
        if hk_line:
            hk_line.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:none;"
                "font:13px 'Segoe UI';"
                "selection-background-color:#ffd060;selection-color:#111827;}")
        self._nearby_hk.setToolTip(i18n.t('settings.hotkey_restart_tooltip'))
        self._nearby_hk._hk_finalized = False

        def _on_seq_changed(seq):
            first = seq.toString().split(', ')[0]
            if first != seq.toString():
                self._nearby_hk.setKeySequence(QKeySequence(first))
            self._nearby_hk._hk_finalized = True

        def _hk_key_press(e):
            key = e.key()
            mods = e.modifiers()
            if key not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
                if self._nearby_hk._hk_finalized:
                    self._nearby_hk.clear()
                    self._nearby_hk._hk_finalized = False
                if mods == Qt.NoModifier and key < 0x1000000:
                    seq_str = QKeySequence(key).toString()
                    if seq_str and _seq_str_to_vk(seq_str) is not None:
                        self._nearby_hk.setKeySequence(QKeySequence(key))
                        self._nearby_hk._hk_finalized = True
                        return
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
        hk_mods = [_OPEN_NEARBY_DEFAULT['mod']]
        try:
            with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as _f:
                _hk_data = json.load(_f).get('open_nearby', {})
                hk_vk = _hk_data.get('vk', hk_vk)
                if 'mods' in _hk_data:
                    hk_mods = _hk_data['mods']
                elif 'mod' in _hk_data:
                    hk_mods = [_hk_data['mod']] if _hk_data['mod'] else []
        except Exception:
            pass
        self._nearby_hk.setKeySequence(QKeySequence(_vk_to_seq_str(hk_vk, hk_mods)))
        nb_hk_row = QWidget()
        nb_hk_row_layout = QHBoxLayout(nb_hk_row)
        nb_hk_row_layout.setContentsMargins(0, 0, 0, 0)
        nb_hk_row_layout.setSpacing(6)
        nb_hk_row_layout.addWidget(self._nearby_hk)
        nb_hk_clear_btn = QPushButton(i18n.t('settings.btn_clear'))
        nb_hk_clear_btn.setFixedHeight(32)
        nb_hk_clear_btn.setStyleSheet(
            "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
            "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
            "QPushButton:hover{background:rgba(255,80,80,.3);}")
        nb_hk_clear_btn.clicked.connect(lambda: self._nearby_hk.clear())
        nb_hk_row_layout.addWidget(nb_hk_clear_btn)
        active_layout[0].addWidget(hk_lbl)
        active_layout[0].addWidget(nb_hk_row)
        hk_note = QLabel(i18n.t('settings.note_hotkey_restart'))
        hk_note.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
        active_layout[0].addWidget(hk_note)

        # Nearby controller combo
        if _HAS_CONTROLLER_HOTKEYS:
            section(i18n.t('settings.section_controller'), nb_layout)
            active_layout[0].addWidget(QLabel(i18n.t('settings.open_combo_label')))
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
                "border-radius:6px;padding:4px 8px;font:13px 'Segoe UI';}")
            self._nb_ctrl_record_btn = QPushButton(i18n.t('settings.btn_record'))
            self._nb_ctrl_record_btn.setFixedHeight(32)
            self._nb_ctrl_record_btn.setStyleSheet(
                "QPushButton{background:rgba(255,208,96,.18);"
                "border:1px solid rgba(255,208,96,.5);"
                "color:#ffd060;border-radius:6px;padding:0 10px;}"
                "QPushButton:hover{background:rgba(255,208,96,.3);}"
                "QPushButton:disabled{color:#666;border-color:#444;background:#222;}")
            nb_ctrl_row_layout.addWidget(self._nb_ctrl_display)
            nb_ctrl_row_layout.addWidget(self._nb_ctrl_record_btn)
            _nb_ctrl_clear_btn = QPushButton(i18n.t('settings.btn_clear'))
            _nb_ctrl_clear_btn.setFixedHeight(32)
            _nb_ctrl_clear_btn.setStyleSheet(
                "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
                "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
                "QPushButton:hover{background:rgba(255,80,80,.3);}")

            def _nb_ctrl_clear():
                self._nb_ctrl_display.setText(i18n.t('settings.display_none'))
                try:
                    existing = _load_controller_hotkey_settings()
                    existing["open_nearby"] = 0
                    _save_controller_hotkey_settings(existing)
                except Exception:
                    pass

            _nb_ctrl_clear_btn.clicked.connect(_nb_ctrl_clear)
            nb_ctrl_row_layout.addWidget(_nb_ctrl_clear_btn)
            active_layout[0].addWidget(nb_ctrl_row)
            nb_ctrl_note = QLabel(i18n.t('settings.note_controller_combo'))
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
                    self._nb_ctrl_record_btn.setText(i18n.t('settings.btn_record'))
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
                self._nb_ctrl_record_btn.setText(i18n.t('settings.btn_recording'))
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
        tabs.addTab(wp_scroll, i18n.t('settings.tab_waypoints'))

        section(i18n.t('settings.section_keyboard_hotkey'), wp_layout)
        wp_hk_lbl = QLabel(i18n.t('settings.open_hotkey_label'))
        self._waypoints_hk = QKeySequenceEdit()
        self._waypoints_hk.setFixedHeight(32)
        self._waypoints_hk.setAttribute(Qt.WA_StyledBackground, True)
        self._waypoints_hk.setStyleSheet(
            "QKeySequenceEdit{background:#e2e8f0;color:#111827;"
            "border:1px solid #64748b;border-radius:6px;padding:4px 8px;"
            "font:13px 'Segoe UI';"
            "selection-background-color:#ffd060;selection-color:#111827;}"
            "QKeySequenceEdit:focus{border:1px solid #ffd060;}")
        wp_hk_line = self._waypoints_hk.findChild(QLineEdit)
        if wp_hk_line:
            wp_hk_line.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:none;"
                "font:13px 'Segoe UI';"
                "selection-background-color:#ffd060;selection-color:#111827;}")
        self._waypoints_hk.setToolTip(i18n.t('settings.hotkey_restart_tooltip'))
        self._waypoints_hk._hk_finalized = False

        def _wp_on_seq_changed(seq):
            first = seq.toString().split(', ')[0]
            if first != seq.toString():
                self._waypoints_hk.setKeySequence(QKeySequence(first))
            self._waypoints_hk._hk_finalized = True

        def _wp_hk_key_press(e):
            key = e.key()
            mods = e.modifiers()
            if key not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
                if self._waypoints_hk._hk_finalized:
                    self._waypoints_hk.clear()
                    self._waypoints_hk._hk_finalized = False
                if mods == Qt.NoModifier and key < 0x1000000:
                    seq_str = QKeySequence(key).toString()
                    if seq_str and _seq_str_to_vk(seq_str) is not None:
                        self._waypoints_hk.setKeySequence(QKeySequence(key))
                        self._waypoints_hk._hk_finalized = True
                        return
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
        wp_hk_mods = [0x10]
        try:
            with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as _f:
                _wp_hk_data = json.load(_f).get('open_waypoints', {})
                wp_hk_vk = _wp_hk_data.get('vk', wp_hk_vk)
                if 'mods' in _wp_hk_data:
                    wp_hk_mods = _wp_hk_data['mods']
                elif 'mod' in _wp_hk_data:
                    wp_hk_mods = [_wp_hk_data['mod']] if _wp_hk_data['mod'] else []
        except Exception:
            pass
        self._waypoints_hk.setKeySequence(QKeySequence(_vk_to_seq_str(wp_hk_vk, wp_hk_mods)))
        wp_hk_row = QWidget()
        wp_hk_row_layout = QHBoxLayout(wp_hk_row)
        wp_hk_row_layout.setContentsMargins(0, 0, 0, 0)
        wp_hk_row_layout.setSpacing(6)
        wp_hk_row_layout.addWidget(self._waypoints_hk)
        wp_hk_clear_btn = QPushButton(i18n.t('settings.btn_clear'))
        wp_hk_clear_btn.setFixedHeight(32)
        wp_hk_clear_btn.setStyleSheet(
            "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
            "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
            "QPushButton:hover{background:rgba(255,80,80,.3);}")
        wp_hk_clear_btn.clicked.connect(lambda: self._waypoints_hk.clear())
        wp_hk_row_layout.addWidget(wp_hk_clear_btn)
        active_layout[0].addWidget(wp_hk_lbl)
        active_layout[0].addWidget(wp_hk_row)
        wp_hk_note = QLabel(i18n.t('settings.note_hotkey_restart'))
        wp_hk_note.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
        active_layout[0].addWidget(wp_hk_note)

        # Controller combo
        if _HAS_CONTROLLER_HOTKEYS:
            section(i18n.t('settings.section_controller'), wp_layout)
            active_layout[0].addWidget(QLabel(i18n.t('settings.open_combo_label')))
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
                "border-radius:6px;padding:4px 8px;font:13px 'Segoe UI';}")
            self._wp_ctrl_record_btn = QPushButton(i18n.t('settings.btn_record'))
            self._wp_ctrl_record_btn.setFixedHeight(32)
            self._wp_ctrl_record_btn.setStyleSheet(
                "QPushButton{background:rgba(255,208,96,.18);"
                "border:1px solid rgba(255,208,96,.5);"
                "color:#ffd060;border-radius:6px;padding:0 10px;}"
                "QPushButton:hover{background:rgba(255,208,96,.3);}"
                "QPushButton:disabled{color:#666;border-color:#444;background:#222;}")
            ctrl_row_layout.addWidget(self._wp_ctrl_display)
            ctrl_row_layout.addWidget(self._wp_ctrl_record_btn)
            _wp_ctrl_clear_btn = QPushButton(i18n.t('settings.btn_clear'))
            _wp_ctrl_clear_btn.setFixedHeight(32)
            _wp_ctrl_clear_btn.setStyleSheet(
                "QPushButton{background:rgba(255,80,80,.15);border:1px solid rgba(255,80,80,.4);"
                "color:#ff6060;border-radius:6px;padding:0 10px;font:11px 'Segoe UI';}"
                "QPushButton:hover{background:rgba(255,80,80,.3);}")

            def _wp_ctrl_clear():
                self._wp_ctrl_display.setText(i18n.t('settings.display_none'))
                try:
                    existing = _load_controller_hotkey_settings()
                    existing["open_waypoints"] = 0
                    _save_controller_hotkey_settings(existing)
                except Exception:
                    pass

            _wp_ctrl_clear_btn.clicked.connect(_wp_ctrl_clear)
            ctrl_row_layout.addWidget(_wp_ctrl_clear_btn)
            active_layout[0].addWidget(ctrl_row)
            ctrl_note = QLabel(i18n.t('settings.note_controller_combo'))
            ctrl_note.setWordWrap(True)
            ctrl_note.setStyleSheet(
                'color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
            active_layout[0].addWidget(ctrl_note)

            wp_nav_help = QLabel(i18n.t('settings.help_waypoints_controls'))
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
                    self._wp_ctrl_record_btn.setText(i18n.t('settings.btn_record'))
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
                self._wp_ctrl_record_btn.setText(i18n.t('settings.btn_recording'))
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
        tabs.addTab(arrow_scroll, i18n.t('settings.tab_direction'))

        section(i18n.t('settings.section_direction_arrow'), arrow_layout)
        option('rotateWithPlayer', i18n.t('settings.option_rotate_with_player'),
               i18n.t('settings.option_rotate_with_player_desc'))
        option('rotateWithCamera', i18n.t('settings.option_rotate_with_camera'),
               i18n.t('settings.option_rotate_with_camera_desc'))
        self._checkboxes['rotateWithPlayer'].toggled.connect(
            lambda checked: checked and self._checkboxes['rotateWithCamera'].setChecked(False))
        self._checkboxes['rotateWithCamera'].toggled.connect(
            lambda checked: checked and self._checkboxes['rotateWithPlayer'].setChecked(False))
        active_layout[0].addWidget(QLabel(i18n.t('settings.source_label')))
        self._heading_combo = QComboBox()
        self._heading_combo.setFixedHeight(32)
        self._heading_combo.setStyleSheet(
            "QComboBox{background:#e2e8f0;color:#111827;border:1px solid #64748b;"
            "border-radius:6px;padding:4px 30px 4px 8px;font:13px 'Segoe UI';}"
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
        self._heading_combo.addItem(i18n.t('settings.combo_heading_auto'), 'auto')
        self._heading_combo.addItem(i18n.t('settings.combo_heading_entity'), 'entity')
        self._heading_combo.addItem(i18n.t('settings.combo_heading_delta'), 'delta')
        current_src = cfg.get('headingSource', SETTING_DEFAULTS['headingSource'])
        idx = self._heading_combo.findData(current_src)
        if idx >= 0:
            self._heading_combo.setCurrentIndex(idx)
        self._heading_combo.setToolTip(i18n.t('settings.tooltip_heading_source'))
        active_layout[0].addWidget(self._heading_combo)

        arrow_layout.addStretch(1)

        # ══════════════════════════════════════════════════════════════
        # TAB: Performance
        # ══════════════════════════════════════════════════════════════
        perf_scroll, perf_layout = _make_tab_scroll()
        tabs.addTab(perf_scroll, i18n.t('settings.tab_performance'))

        section(i18n.t('settings.section_performance'), perf_layout)
        option('highDpiScaling', i18n.t('settings.option_high_dpi_scaling'),
               i18n.t('settings.option_high_dpi_scaling_desc'))
        option('disableGpuVsync', i18n.t('settings.option_disable_gpu_vsync'),
               i18n.t('settings.option_disable_gpu_vsync_desc'))

        active_layout[0].addWidget(QLabel(i18n.t('settings.realtime_transport_label')))
        self._realtime_transport_combo = QComboBox()
        self._realtime_transport_combo.setFixedHeight(32)
        self._realtime_transport_combo.setStyleSheet(
            "QComboBox{background:#e2e8f0;color:#111827;border:1px solid #64748b;"
            "border-radius:6px;padding:4px 30px 4px 8px;font:13px 'Segoe UI';}"
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
        self._realtime_transport_combo.addItem(i18n.t('settings.combo_realtime_websocket'), 'websocket')
        self._realtime_transport_combo.addItem(i18n.t('settings.combo_realtime_native'), 'native')
        current_transport = cfg.get(
            'realtimeTransport', SETTING_DEFAULTS['realtimeTransport'])
        idx = self._realtime_transport_combo.findData(current_transport)
        if idx < 0:
            idx = self._realtime_transport_combo.findData(
                SETTING_DEFAULTS['realtimeTransport'])
        self._realtime_transport_combo.setCurrentIndex(idx)
        active_layout[0].addWidget(self._realtime_transport_combo)
        rt_note = QLabel(i18n.t('settings.note_realtime_transport'))
        rt_note.setWordWrap(True)
        rt_note.setStyleSheet('color:#64748b; font:11px "Segoe UI"; margin-top:-4px;')
        active_layout[0].addWidget(rt_note)

        perf_layout.addStretch(1)

        # ── Botões Save/Cancel ─────────────────────────────────────────
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton(i18n.t('settings.cancel'))
        cancel_btn.clicked.connect(self._on_cancel)
        save_btn = QPushButton(i18n.t('settings.save'))
        save_btn.setObjectName('save')
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        outer_layout.addLayout(btn_row)

    def _make_hotkey_editor(self):
        editor = QKeySequenceEdit()
        editor.setFixedHeight(32)
        editor.setAttribute(Qt.WA_StyledBackground, True)
        editor.setStyleSheet(
            "QKeySequenceEdit{background:#e2e8f0;color:#111827;"
            "border:1px solid #64748b;border-radius:6px;padding:4px 8px;"
            "font:13px 'Segoe UI';"
            "selection-background-color:#ffd060;selection-color:#111827;}"
            "QKeySequenceEdit:focus{border:1px solid #ffd060;}")
        line = editor.findChild(QLineEdit)
        if line:
            line.setStyleSheet(
                "QLineEdit{background:#e2e8f0;color:#111827;border:none;"
                "font:13px 'Segoe UI';"
                "selection-background-color:#ffd060;selection-color:#111827;}")
        editor.setToolTip(i18n.t('settings.hotkey_restart_tooltip'))
        editor._hk_finalized = False

        def _on_seq_changed(seq):
            first = seq.toString().split(', ')[0]
            if first != seq.toString():
                editor.setKeySequence(QKeySequence(first))
            editor._hk_finalized = True

        def _key_press(e):
            key = e.key()
            mods = e.modifiers()
            if key not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
                if editor._hk_finalized:
                    editor.clear()
                    editor._hk_finalized = False
                if mods == Qt.NoModifier and key < 0x1000000:
                    seq_str = QKeySequence(key).toString()
                    if seq_str and _seq_str_to_vk(seq_str) is not None:
                        editor.setKeySequence(QKeySequence(key))
                        editor._hk_finalized = True
                        return
            QKeySequenceEdit.keyPressEvent(editor, e)

        def _focus_in(e):
            if _set_keyboard_hotkey_paused:
                _set_keyboard_hotkey_paused(True)
            if _set_nearby_hotkey_paused:
                _set_nearby_hotkey_paused(True)
            QKeySequenceEdit.focusInEvent(editor, e)

        def _focus_out(e):
            if _set_keyboard_hotkey_paused:
                _set_keyboard_hotkey_paused(False)
            if _set_nearby_hotkey_paused:
                _set_nearby_hotkey_paused(False)
            QKeySequenceEdit.focusOutEvent(editor, e)

        editor.keySequenceChanged.connect(_on_seq_changed)
        editor.keyPressEvent = _key_press
        editor.focusInEvent = _focus_in
        editor.focusOutEvent = _focus_out
        return editor

    def _make_hotkey_row(self, editor):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        row_layout.addWidget(editor)
        clear_btn = QPushButton(i18n.t('settings.btn_clear'))
        clear_btn.setFixedHeight(32)
        clear_btn.setStyleSheet(
            "QPushButton{background:rgba(255,80,80,.15);border:1px solid "
            "rgba(255,80,80,.4);color:#ff6060;border-radius:6px;"
            "padding:0 10px;font:11px 'Segoe UI';}"
            "QPushButton:hover{background:rgba(255,80,80,.3);}")
        clear_btn.clicked.connect(lambda: editor.clear())
        row_layout.addWidget(clear_btn)
        return row

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
        result['language'] = self._lang_combo.currentData()
        self._save_nearby_hotkey()
        return result

    def _save_nearby_hotkey(self):
        seq_str = self._nearby_hk.keySequence().toString()
        parsed = _seq_str_to_vk(seq_str)
        wp_seq_str = self._waypoints_hk.keySequence().toString()
        wp_parsed = _seq_str_to_vk(wp_seq_str)
        ft_seq_str = self._focus_toggle_hk.keySequence().toString()
        ft_parsed = _seq_str_to_vk(ft_seq_str)
        to_seq_str = self._to_hk.keySequence().toString()
        to_parsed = _seq_str_to_vk_win(to_seq_str)
        ts_seq_str = self._ts_hk.keySequence().toString()
        ts_parsed = _seq_str_to_vk_win(ts_seq_str)
        tp_marker_seq_str = self._tp_marker_hk.keySequence().toString()
        tp_marker_parsed = _seq_str_to_vk(tp_marker_seq_str)
        tp_abort_seq_str = self._tp_abort_hk.keySequence().toString()
        tp_abort_parsed = _seq_str_to_vk(tp_abort_seq_str)
        try:
            try:
                with open(_HOTKEY_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                data = {}
            if parsed is not None:
                vk, mods = parsed
                data['open_nearby'] = {'vk': vk, 'mods': mods, 'enabled': True}
            else:
                data['open_nearby'] = {'vk': 0, 'mods': [], 'enabled': False}
            if wp_parsed is not None:
                wp_vk, wp_mods = wp_parsed
                data['open_waypoints'] = {'vk': wp_vk, 'mods': wp_mods, 'enabled': True}
            else:
                data['open_waypoints'] = {'vk': 0, 'mods': [], 'enabled': False}
            if ft_parsed is not None:
                ft_vk, ft_mods = ft_parsed
                data['focus_toggle'] = {'vk': ft_vk, 'mods': ft_mods, 'enabled': True}
            else:
                data['focus_toggle'] = {'vk': 0, 'mods': [], 'enabled': False}
            if to_parsed is not None:
                to_vk, to_mod_win = to_parsed
                data['toggle_overlay'] = {'vk': to_vk, 'mod_win': to_mod_win}
            else:
                data['toggle_overlay'] = {'vk': 0, 'mod_win': 0}
            if tp_marker_parsed is not None:
                tp_marker_vk, tp_marker_mods = tp_marker_parsed
                data['teleport_marker'] = {
                    'vk': tp_marker_vk, 'mods': tp_marker_mods, 'enabled': True}
            else:
                data['teleport_marker'] = {'vk': 0, 'mods': [], 'enabled': False}
            if tp_abort_parsed is not None:
                tp_abort_vk, tp_abort_mods = tp_abort_parsed
                data['abort'] = {'vk': tp_abort_vk, 'mods': tp_abort_mods, 'enabled': True}
            else:
                data['abort'] = {'vk': 0, 'mods': [], 'enabled': False}
            if ts_parsed is not None:
                ts_vk, ts_mod_win = ts_parsed
                data['toggle_shape'] = {'vk': ts_vk, 'mod_win': ts_mod_win}
            else:
                data['toggle_shape'] = {'vk': 0, 'mod_win': 0}
            os.makedirs(os.path.dirname(_HOTKEY_SETTINGS_FILE), exist_ok=True)
            with open(_HOTKEY_SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


# ── Janelas WebEngine auxiliares ─────────────────────────────────────

class PopupWebWindow(QMainWindow):
    closed_with_pos = Signal(object)  # emits QPoint when window closes

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
        QTimer.singleShot(50, focus_game_window)

    def page(self):
        return self._page


class InterceptPage(QWebEnginePage):
    login_needed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_windows = []
        self._last_popup_pos = None   # persists popup position within the session

    def createWindow(self, window_type):
        view = self.parent()
        overlay = view.window() if isinstance(view, QWebEngineView) else None
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
        self.setWindowTitle(i18n.t('login.title'))
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

        msg = QLabel(i18n.t('login.message'))
        msg.setAlignment(Qt.AlignCenter)
        msg.setStyleSheet('color:#e2e8f0; font:13px "Segoe UI"; background:transparent;')
        layout.addWidget(msg)

        row = QHBoxLayout()
        no_btn = QPushButton(i18n.t('login.btn_not_now'))
        no_btn.setObjectName('no')
        no_btn.clicked.connect(self.reject)
        yes_btn = QPushButton(i18n.t('login.btn_go_to_login'))
        yes_btn.setObjectName('yes')
        yes_btn.clicked.connect(self.accept)
        row.addWidget(no_btn)
        row.addWidget(yes_btn)
        layout.addLayout(row)


# ── Sinal para toggle thread-safe ────────────────────────────────────

class HotkeySignals(QObject):
    toggle = Signal()
    restart = Signal()
    toggle_shape = Signal()


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

        self.btn_back     = icon_btn('btn_back',     '\u25c0', i18n.t('titlebar.btn_back'))
        self.btn_settings = icon_btn('btn_settings', '\u2699', i18n.t('titlebar.btn_settings'))
        self.btn_hide     = icon_btn('btn_hide',     '\u2013', i18n.t('titlebar.btn_hide'))
        self.btn_maximize = icon_btn('btn_maximize', '\u25a1', i18n.t('titlebar.btn_maximize'))
        self.btn_close    = icon_btn('btn_close',    '\u2715', i18n.t('titlebar.btn_close'))

    def set_compact(self, compact):
        self._lbl.setVisible(not compact)
        self.btn_back.setVisible(not compact)

    def set_maximized(self, maximized):
        if maximized:
            self.btn_maximize.setText('\u274e')
            self.btn_maximize.setToolTip(i18n.t('titlebar.btn_restore'))
        else:
            self.btn_maximize.setText('\u25a1')
            self.btn_maximize.setToolTip(i18n.t('titlebar.btn_maximize'))

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
