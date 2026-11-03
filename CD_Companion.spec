# -*- mode: python ; coding: utf-8 -*-
import os
import re
from pathlib import Path
import PySide6


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('server', 'server'), ('overlay', 'overlay'), ('shared', 'shared'), ('launcher.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Qt 6 uses the Windows ICU API. Do not shadow it with an unrelated ICU
# installation found on the build machine's PATH (for example Poppler).
# Keep the private MSVC runtime at least as recent as the selected Qt wheel.
qt_dir = Path(PySide6.__file__).parent
runtime_names = {'vcruntime140.dll', 'vcruntime140_1.dll'}
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in runtime_names
              and not re.fullmatch(r'icu(?:uc|dt\d+)\.dll', Path(entry[0]).name.lower())]
for name in sorted(runtime_names):
    source = qt_dir / name
    if os.name == 'nt' and source.is_file():
        a.binaries.append((name, str(source), 'BINARY'))

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CD_Companion',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['launcher.ico'],
)
