"""
TeleportEngine — leitura de memória, hooks e teleport.
Extraído de position_server.py para melhor organização.
"""

import ctypes
import ctypes.wintypes
import logging

import pymem
import pymem.process

from server.memory.constants import (
    PROCESS_NAME, SAVE_DIR, HOOK_OFFSETS_FILE, HEIGHT_BOOST,
    k32, MEM_COMMIT, MEM_RESERVE, MEM_RELEASE, PAGE_EXECUTE_READWRITE, _REDACT,
)
from server.memory.hook_cache import _load_hook_offsets, _save_hook_offsets
from server.memory.reader import ReaderMixin
from server.memory.teleport import TeleportMixin
from server.memory.shared_mem import SharedMemoryMixin
from server.memory.mem_alloc import MemAllocMixin
from server.memory.cave_builder import CaveBuilderMixin
from server.memory.aob_scanner import ScannerMixin

log = logging.getLogger('cd_server')

# Tipagem obrigatoria antes de chamar via ctypes: sem isto o handle de 64 bits
# seria truncado para 32 bits (crash SEH silencioso). Ver feedback_ctypes_argtypes.
k32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
k32.WaitForSingleObject.restype  = ctypes.wintypes.DWORD


# ── TeleportEngine (position reading only) ───────────────────────────

class TeleportEngine(SharedMemoryMixin, ScannerMixin, MemAllocMixin, CaveBuilderMixin, ReaderMixin, TeleportMixin):
    AOB_ENTITY = b'\x48\x83\xEC\x50\x48\x8B\xF9\x48\x8B\x91\x30\x11\x00\x00'
    AOB_POS    = b'\x0F\x11\x99\x90\x00\x00\x00'
    AOB_HEALTH = b'\x48\x8B\x46\x08\x48\x89\xF1'
    AOB_MAP    = b'\xC5\xFB\x10\x07\xC5\xFB\x11\x02\x8B\x47\x08\x89\x42\x08'
    # VEX encoding (2026-04 patch):
    #   C5 FB 10 07       vmovsd xmm0, [rdi]       ← carrega X/Y do marcador
    #   C5 FB 11 02       vmovsd [rdx], xmm0       ← escreve X/Y  ← hook em AOB+4
    #   8B 47 08          mov eax, [rdi+08]         ← carrega Z
    #   89 42 08          mov [rdx+08], eax         ← escreve Z
    AOB_MAP_HOOK_OFFSET = 4  # hook no vmovsd store, não no load
    AOB_WORLD  = b'\x0F\x5C\x1D'
    # vmovsd [rip+?],xmm0 ; mov eax,[rsp+28] ; mov [rip+?],eax
    # Localiza os 3 endereços estáticos de XYZ do player
    AOB_XYZ_PREFIX = b'\xC5\xFB\x11\x05'          # vmovsd [rip+disp32], xmm0
    AOB_XYZ_MID    = b'\x8B\x44\x24\x28\x89\x05'  # mov eax,[rsp+28] ; mov [rip+disp32],eax
    # Physics delta hook (OpenFlight technique):
    #   movaps xmm0, xmm6       ; 0F 28 C6   ← hook point (8 bytes total com próxima instrução)
    #   subss  xmm9, xmm8       ; F3 45 0F 5C C8
    # confirmado por +8: addps xmm0,[r13] + movups [r13],xmm0
    AOB_PHYS_DELTA_CONFIRM = b'\x41\x0F\x58\x45\x00\x41\x0F\x11\x45\x00'  # em hook_e+8
    AOB_PHYS_DELTA_HOOK    = b'\x0F\x28\xC6\xF3\x45\x0F\x5C\xC8'          # hook_e (8 bytes)
    # vmovss [r15+0x4CC], xmm2  (heading da camera em graus assinados -180..180)
    # instrução seguinte: vcomiss xmm9, xmm6
    AOB_CAM  = b'\xC4\xC1\x7A\x11\x97\xCC\x04\x00\x00\xC5\x78\x2F\xCE'
    HOOK_PATCH_SIZE = 7
    ORIG_HOOK_A   = b'\x48\x8B\x91\x30\x11\x00\x00'  # mov rdx,[rcx+0x1130]
    ORIG_HOOK_B   = AOB_POS
    ORIG_HOOK_C   = AOB_HEALTH
    ORIG_HOOK_D   = b'\xC5\xFB\x11\x02\x8B\x47\x08'  # vmovsd [rdx],xmm0 ; mov eax,[rdi+8]
    ORIG_HOOK_E   = b'\x0F\x28\xC6\xF3\x45\x0F\x5C'  # primeiros 7 bytes do hook_e
    ORIG_HOOK_CAM = b'\xC4\xC1\x7A\x11\x97\xCC\x04\x00\x00'  # vmovss [r15+0x4CC],xmm2  (9 bytes)

    OFF_TD      = 0x000
    OFF_INV     = 0x040
    OFF_MD      = 0x050
    OFF_TP      = 0x060  # teleport target: {tx,ty,tz,0.0f} + flag uint32 = 20 bytes
    OFF_CAM_YAW  = 0x090  # float: camera heading em graus assinados (salvo por cave_cam)
    OFF_PLYR_HDG = 0x094  # float: player heading em graus 0-360 (escrito por get_player_heading)
    OFF_CA   = 0x100
    OFF_CB   = 0x180
    OFF_CC   = 0x200
    OFF_CD   = 0x280
    OFF_CE   = 0x300  # cave E: physics delta hook para teleport
    OFF_CF   = 0x380  # cave F: camera yaw hook
    BLOCK_SZ = 0x1000

    def __init__(self, teleport_enabled: bool = True, use_shared_memory_entity: bool = True):
        self.teleport_enabled = teleport_enabled
        self.use_shared_memory_entity = use_shared_memory_entity
        self.pm = None
        self.module = None
        self.attached = False
        self.hooks_installed = False
        self.block = 0
        self.td = 0
        self.inv = 0
        self.md = 0
        self.tp = 0
        self.hook_a = 0
        self.hook_b = 0
        self.hook_c = 0
        self.hook_d = 0
        self.hook_e = 0
        self.hook_cam = 0
        self.orig_bytes = {}
        self.world_offset_addr = 0
        self.xyz_addr = (0, 0, 0)   # (x_addr, y_addr, z_addr) — estáticos globais
        self._trampolines = []
        self._far_mode = False
        self._hook_e_predecessor = 0  # trampoline de outro mod hookado no mesmo ponto (ex: OpenFlight)
        self._hook_e_predecessor_patch_size = 0  # 0, 5 (safetyhook), ou 7 (OpenFlight/companion)

    def attach(self):
        if self.pm or self.attached or self.orig_bytes or self.block:
            self.detach()
        self.pm = pymem.Pymem(PROCESS_NAME)
        self.module = pymem.process.module_from_name(
            self.pm.process_handle, PROCESS_NAME)
        self.attached = True

    def is_process_alive(self):
        """True se o processo do jogo ainda esta vivo.

        Necessario porque as leituras (get_player_pos etc.) engolem excecoes e
        retornam None quando o processo morre, em vez de lancar. Sem isto o
        engine ficaria preso ao handle de um processo morto e nunca re-anexaria
        ao novo processo quando o jogo fosse reaberto."""
        if not self.pm:
            return False
        try:
            WAIT_OBJECT_0 = 0x0  # processo sinalizado = ja terminou
            return k32.WaitForSingleObject(self.pm.process_handle, 0) != WAIT_OBJECT_0
        except Exception:
            return False

    @property
    def status(self):
        if not self.attached:
            return "disconnected"
        if not self.hooks_installed:
            return "scanning"
        return "attached"

    @property
    def teleport_available(self):
        """Retorna True se teleport está habilitado E o hook_e está instalado."""
        return self.teleport_enabled and bool(self.hook_e)

    def _reset_runtime_state(self):
        self.block = 0
        self.td = 0
        self.inv = 0
        self.md = 0
        self.tp = 0
        self.hook_a = 0
        self.hook_b = 0
        self.hook_c = 0
        self.hook_d = 0
        self.hook_e = 0
        self.hook_cam = 0
        self.world_offset_addr = 0
        self.xyz_addr = (0, 0, 0)
        self.orig_bytes.clear()
        self._trampolines.clear()
        self._far_mode = False
        self._hook_e_predecessor = 0
        self._hook_e_predecessor_patch_size = 0
        self.hooks_installed = False

    def detach(self):
        try:
            if self.orig_bytes:
                self.uninstall_hooks()
        except Exception:
            pass
        handle = self.pm.process_handle if self.pm else None
        if self.block and handle:
            k32.VirtualFreeEx(handle, self.block, 0, MEM_RELEASE)
            self.block = 0
        for tramp in self._trampolines:
            if handle:
                try:
                    k32.VirtualFreeEx(handle, tramp, 0, MEM_RELEASE)
                except Exception:
                    pass
        self._trampolines.clear()
        self._far_mode = False
        if self.pm:
            try:
                self.pm.close_process()
            except Exception:
                pass
        self.pm = None
        self.module = None
        self.attached = False
        self._reset_runtime_state()

