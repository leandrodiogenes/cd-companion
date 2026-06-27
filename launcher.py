"""
Crimson Desert — Launcher
=========================
Ponto de entrada único. Eleva UAC se necessário e inicia o overlay
(que por sua vez sobe o servidor WebSocket como thread daemon).

Resultado: uma única janela visível (o overlay PyQt5).
"""

import ctypes
import os
import sys
import time

# ── Detecta se está rodando como exe compilado (PyInstaller) ──────────

_FROZEN = getattr(sys, 'frozen', False)

if _FROZEN:
    SCRIPT_DIR = sys._MEIPASS
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Elevação UAC ──────────────────────────────────────────────────────

def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def _elevate():
    params = ' '.join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit(0)

if not _is_admin():
    _elevate()

# ── Instância única (named mutex) ─────────────────────────────────────

_MUTEX_NAME = "Global\\CDCompanion_SingleInstance"

def _acquire_single_instance_mutex():
    """Cria um named mutex global. Retorna o handle ou encerra o processo.

    Quando relançado pelo proprio app via "Restart" (flag --restarted), a
    instancia antiga ainda pode estar viva por um instante segurando o mutex.
    Nesse caso aguardamos ate ~12s o mutex ser liberado, em vez de mostrar
    "ja em execucao" e sair.
    """
    restarting = '--restarted' in sys.argv
    deadline = time.monotonic() + 12 if restarting else 0
    while True:
        handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            if restarting and time.monotonic() < deadline:
                ctypes.windll.kernel32.CloseHandle(handle)
                time.sleep(0.25)
                continue
            ctypes.windll.user32.MessageBoxW(
                None,
                "CD Companion já está em execução.",
                "CD Companion",
                0x00000030,  # MB_ICONWARNING
            )
            sys.exit(0)
        return handle  # mantém o handle vivo enquanto o processo rodar

_mutex_handle = _acquire_single_instance_mutex()

# ── Lançamento ────────────────────────────────────────────────────────

def main():
    # Garante que o diretório do script está no sys.path para imports
    if SCRIPT_DIR not in sys.path:
        sys.path.insert(0, SCRIPT_DIR)

    # Passa o diretório do exe para o overlay salvar config no lugar certo
    app_dir = os.path.dirname(sys.executable) if _FROZEN else SCRIPT_DIR
    os.environ['CD_APP_DIR'] = app_dir

    from overlay.main import main as overlay_main
    overlay_main()

if __name__ == '__main__':
    main()
