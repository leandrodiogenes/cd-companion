import struct
import logging

from server.memory.constants import k32, HEIGHT_BOOST

log = logging.getLogger('cd_server')


class CaveBuilderMixin:
    """Construcao de code caves, patches e gerenciamento de hooks.

    Acessa self.pm, self.block, self.td, self.inv, self.md, self.tp,
    self.OFF_TD, self.OFF_INV, self.OFF_MD, self.OFF_TP, self.OFF_CA,
    self.OFF_CB, self.OFF_CC, self.OFF_CD, self.OFF_CE, self.OFF_CF,
    self.OFF_CAM_YAW, self.BLOCK_SZ, self.ORIG_HOOK_A, self.ORIG_HOOK_B,
    self.ORIG_HOOK_C, self.ORIG_HOOK_D, self.ORIG_HOOK_E, self.ORIG_HOOK_CAM,
    self.HOOK_PATCH_SIZE, self.hook_a, self.hook_b, self.hook_c, self.hook_d,
    self.hook_e, self.hook_cam, self.xyz_addr, self._hook_e_predecessor,
    self._hook_e_predecessor_patch_size, self._far_mode, self.orig_bytes,
    self._trampolines, self.hooks_installed, self.use_shared_memory_entity,
    self._alloc_near, self._jmp_patch, self._jmp_patch5, self._jmp_patch9,
    self._abs_jmp — todos definidos em TeleportEngine.__init__ ou mixins.
    """

    def _canonical_orig_bytes(self, hook_addr):
        if hook_addr == self.hook_a:
            return self.ORIG_HOOK_A
        if hook_addr == self.hook_b:
            return self.ORIG_HOOK_B
        if hook_addr == self.hook_c:
            return self.ORIG_HOOK_C
        if hook_addr == self.hook_d:
            return self.ORIG_HOOK_D
        if hook_addr == self.hook_e:
            return self.ORIG_HOOK_E
        if hook_addr == self.hook_cam:
            return self.ORIG_HOOK_CAM
        return b''

    def _remember_orig_bytes(self, hook_addr):
        patch_size = self.HOOK_PATCH_SIZE
        # Predecessor de 5 bytes (safetyhook/Freedom Flyer): salva e restaura 5 bytes.
        if hook_addr == self.hook_e and self._hook_e_predecessor_patch_size == 5:
            patch_size = 5
        current = self.pm.read_bytes(hook_addr, patch_size)
        if hook_addr == self.hook_e and self._hook_e_predecessor:
            # Predecessor detectado: salva o JMP do outro mod para restaurar corretamente no detach.
            self.orig_bytes[hook_addr] = current
        elif current[:1] == b'\xE9' and current[-2:] == b'\x90\x90':
            self.orig_bytes[hook_addr] = self._canonical_orig_bytes(hook_addr)
        else:
            self.orig_bytes[hook_addr] = current

    def _rel32(self, from_addr, to_addr):
        rel = to_addr - (from_addr + 5)
        if not (-0x80000000 <= rel <= 0x7FFFFFFF):
            raise RuntimeError("Cave too far for rel32 jump — try restarting the game")
        return rel

    def _jmp_patch5(self, from_addr, to_addr):
        """Patch de 5 bytes (só E9 + rel32, sem NOPs) — para instruções de 5 bytes."""
        rel = to_addr - (from_addr + 5)
        if not (-0x80000000 <= rel <= 0x7FFFFFFF):
            raise RuntimeError("Cave too far for rel32 jump — try restarting the game")
        return b'\xE9' + struct.pack('<i', rel)

    def _jmp_patch9(self, from_addr, to_addr):
        return b'\xE9' + struct.pack('<i', self._rel32(from_addr, to_addr)) + b'\x90' * 4

    def _jmp_patch(self, from_addr, to_addr):
        return b'\xE9' + struct.pack('<i', self._rel32(from_addr, to_addr)) + b'\x90\x90'

    def _abs_jmp(self, target):
        return b'\xFF\x25\x00\x00\x00\x00' + struct.pack('<Q', target)

    def _build_cave_a(self):
        # Hook em "mov rdx,[rcx+0x1130]" — rcx = entity base
        td, ret = self.td, self.hook_a + 7
        c = bytearray()
        c += b'\x50'                               # push rax  (scratch)
        c += b'\x48\xB8' + struct.pack('<Q', td)   # mov rax, td
        c += b'\x48\x89\x48\x18'                   # mov [rax+0x18], rcx  (entity base)
        c += b'\x58'                               # pop rax
        c += b'\x48\x8B\x91\x30\x11\x00\x00'      # mov rdx,[rcx+0x1130]  (original)
        c += self._abs_jmp(ret)
        return bytes(c)

    def _build_cave_b(self):
        td, ret = self.td, self.hook_b + 7
        c = bytearray()
        c += b'\x50'
        c += b'\x48\xB8' + struct.pack('<Q', td)
        c += b'\x83\x78\x10\x00'
        c += b'\x7E\x15'
        c += b'\x48\x3B\x48\x18'
        c += b'\x75\x0F'
        c += b'\x58'
        c += self._abs_jmp(ret)
        c += b'\x58'
        c += b'\x0F\x11\x99\x90\x00\x00\x00'
        c += self._abs_jmp(ret)
        return bytes(c)

    def _build_cave_c(self):
        inv, ret = self.inv, self.hook_c + 7
        c = bytearray()
        c += b'\x53'
        c += b'\x48\xBB' + struct.pack('<Q', inv)
        c += b'\x80\x3B\x01'
        c += b'\x5B'
        c += b'\x75\x0F'
        c += b'\x80\x3E\x00'
        c += b'\x75\x0A'
        c += b'\x53'
        c += b'\x48\x8B\x5E\x18'
        c += b'\x48\x89\x5E\x08'
        c += b'\x5B'
        c += b'\x48\x8B\x46\x08'
        c += b'\x48\x89\xF1'
        c += self._abs_jmp(ret)
        return bytes(c)

    def _build_cave_d(self):
        """Map destination capture cave (VEX encoding, 2026-04 patch).
        Hook em vmovsd [rdx],xmm0 — captura X/Y/Z do marcador do mapa."""
        md, ret = self.md, self.hook_d + 7
        c = bytearray()
        # Instruções originais (7 bytes substituídos pelo JMP)
        c += b'\xC5\xFB\x11\x02'                       # vmovsd [rdx], xmm0  (VEX)
        c += b'\x8B\x47\x08'                            # mov eax, [rdi+08]
        # Salva no bloco md
        c += b'\x51'                                    # push rcx
        c += b'\x48\xB9' + struct.pack('<Q', md)        # mov rcx, mapDestData
        c += b'\xC5\xFB\x11\x01'                        # vmovsd [rcx], xmm0  (VEX)
        c += b'\x89\x41\x08'                            # mov [rcx+08], eax
        c += b'\xC7\x41\x0C\x01\x00\x00\x00'           # mov dword [rcx+0C], 1  (flag)
        c += b'\x59'                                    # pop rcx
        c += self._abs_jmp(ret)
        return bytes(c)

    def _build_cave_e(self):
        """Hook no physics delta — dois modos via flag em tp_block:

        flag=1  TELEPORT: xmm0 = target_static - current_static
                addps → [r13]_new = [r13] + delta = target_r13  ✓
                (corrige o offset entre espaço [r13] e static globals)

        flag=2  MOVE: xmm0 = delta directo do tp_block
                addps → [r13]_new = [r13] + delta  (deltas são invariantes à translação)
                Usado para movimento direcional contínuo.

        tp_block layout (em self.tp):
          +0  float x    ┐ target (flag=1) ou delta (flag=2)
          +4  float y    │
          +8  float z    │
          +12 float 0.0  ┘ padding W
          +16 uint32 flag   (1=teleport, 2=move, 0=nenhum — zerado pelo hook)
          +24 16 bytes      área save/restore xmm1 (flag=1 apenas)
        """
        tp  = self.tp
        xyz = self.xyz_addr[0]   # static global X (Y=+4, Z=+8, consecutivos)

        # Predecessor com patch de 5 bytes (safetyhook / Freedom Flyer):
        # trampoline preserva só 5 bytes originais → flag=0 pula bytes originais
        # e faz JMP direto para o predecessor; flag=1/2 executa todos os 8 bytes
        # originais na cave e faz JMP para hook_e+8 (skip do predecessor por 1 frame).
        if self._hook_e_predecessor and self._hook_e_predecessor_patch_size == 5:
            return self._build_cave_e_pred5(tp, xyz)

        # Sem predecessor, ou predecessor com patch de 7 bytes (OpenFlight):
        # cave executa os 8 bytes originais e retorna para predecessor+8 (se houver)
        # ou hook_e+8 (sem predecessor).
        if self._hook_e_predecessor:
            ret = self._hook_e_predecessor + 8
        else:
            ret = self.hook_e + 8

        c = bytearray()

        # Instruções originais (8 bytes sobrescritos pelo JMP)
        c += b'\x0F\x28\xC6'                          # movaps xmm0, xmm6
        c += b'\xF3\x45\x0F\x5C\xC8'                  # subss  xmm9, xmm8

        # Salvar RBX (physics entity) em tp+0x28 — usado para ler forward vector
        c += b'\x50'                                   # push rax
        c += b'\x48\xB8' + struct.pack('<Q', tp)        # mov rax, tp_addr
        c += b'\x48\x89\x58\x28'                       # mov [rax+0x28], rbx  (save physics entity)

        # Verificar flag != 0
        c += b'\x83\x78\x10\x00'                       # cmp dword [rax+16], 0
        je_done_pos = len(c)
        c += b'\x74\x00'                               # je .done (placeholder)

        # Verificar flag=2 (move mode — delta directo)
        c += b'\x83\x78\x10\x02'                       # cmp dword [rax+16], 2
        je_move_pos = len(c)
        c += b'\x74\x00'                               # je .move (placeholder)

        # ── TELEPORT (flag=1): xmm0 = target_static - current_static ──
        c += b'\x0F\x11\x48\x18'                       # movups [rax+24], xmm1  (save)
        c += b'\x0F\x10\x00'                           # movups xmm0, [rax]     target
        c += b'\x51'                                   # push rcx
        c += b'\x48\xB9' + struct.pack('<Q', xyz)       # mov rcx, xyz_addr
        c += b'\x0F\x10\x09'                           # movups xmm1, [rcx]     current static
        c += b'\x59'                                   # pop rcx
        c += b'\x0F\x5C\xC1'                           # subps  xmm0, xmm1
        c += b'\x0F\x10\x48\x18'                       # movups xmm1, [rax+24]  (restore)
        jmp_clear_pos = len(c)
        c += b'\xEB\x00'                               # jmp .clear (placeholder)

        # ── MOVE (flag=2): xmm0 = delta directo ────────────────────────
        c[je_move_pos + 1] = len(c) - (je_move_pos + 2)
        c += b'\x0F\x10\x00'                           # movups xmm0, [rax]

        # ── CLEAR FLAG ──────────────────────────────────────────────────
        c[jmp_clear_pos + 1] = len(c) - (jmp_clear_pos + 2)
        c += b'\xC7\x40\x10\x00\x00\x00\x00'           # mov dword [rax+16], 0

        # ── .done ───────────────────────────────────────────────────────
        c[je_done_pos + 1] = len(c) - (je_done_pos + 2)
        c += b'\x58'                                   # pop rax
        c += self._abs_jmp(ret)
        return bytes(c)

    def _build_cave_e_pred5(self, tp, xyz):
        """Cave para predecessor com patch de 5 bytes (safetyhook / Freedom Flyer).

        Diferente do path normal:
        - flag=0 → NÃO executa bytes originais, faz JMP direto para o predecessor.
          O safetyhook executa os 5 bytes originais + handler do FF.
        - flag=1/2 → Executa TODOS os 8 bytes originais na cave, aplica delta,
          e faz JMP para hook_e+8 (pula o predecessor por 1 frame — imperceptível).
        """
        ret = self.hook_e + 8
        c = bytearray()

        # Salvar RBX (physics entity) em tp+0x28
        c += b'\x50'                                   # push rax
        c += b'\x48\xB8' + struct.pack('<Q', tp)        # mov rax, tp_addr
        c += b'\x48\x89\x58\x28'                       # mov [rax+0x28], rbx  (save physics entity)

        # Verificar flag != 0
        c += b'\x83\x78\x10\x00'                       # cmp dword [rax+16], 0
        je_done_no_orig_pos = len(c)
        c += b'\x74\x00'                               # je .done_no_orig (placeholder)

        # ── flag != 0: executar bytes originais (não executados pelo pred-5) ──
        c += b'\x0F\x28\xC6'                           # movaps xmm0, xmm6
        c += b'\xF3\x45\x0F\x5C\xC8'                   # subss  xmm9, xmm8

        # Verificar flag=2 (move mode)
        c += b'\x83\x78\x10\x02'                       # cmp dword [rax+16], 2
        je_move_pos = len(c)
        c += b'\x74\x00'                               # je .move (placeholder)

        # ── TELEPORT (flag=1): xmm0 = target_static - current_static ──
        c += b'\x0F\x11\x48\x18'                       # movups [rax+24], xmm1  (save)
        c += b'\x0F\x10\x00'                           # movups xmm0, [rax]     target
        c += b'\x51'                                   # push rcx
        c += b'\x48\xB9' + struct.pack('<Q', xyz)       # mov rcx, xyz_addr
        c += b'\x0F\x10\x09'                           # movups xmm1, [rcx]     current static
        c += b'\x59'                                   # pop rcx
        c += b'\x0F\x5C\xC1'                           # subps  xmm0, xmm1
        c += b'\x0F\x10\x48\x18'                       # movups xmm1, [rax+24]  (restore)
        jmp_clear_pos = len(c)
        c += b'\xEB\x00'                               # jmp .clear_skip_pred (placeholder)

        # ── MOVE (flag=2): xmm0 = delta directo ────────────────────────
        c[je_move_pos + 1] = len(c) - (je_move_pos + 2)
        c += b'\x0F\x10\x00'                           # movups xmm0, [rax]

        # ── CLEAR FLAG + return to hook_e+8 (skip predecessor) ──────────
        c[jmp_clear_pos + 1] = len(c) - (jmp_clear_pos + 2)
        c += b'\xC7\x40\x10\x00\x00\x00\x00'           # mov dword [rax+16], 0
        c += b'\x58'                                   # pop rax
        c += self._abs_jmp(ret)                        # JMP hook_e+8

        # ── .done_no_orig (flag=0): pop rax, JMP predecessor ────────────
        c[je_done_no_orig_pos + 1] = len(c) - (je_done_no_orig_pos + 2)
        c += b'\x58'                                   # pop rax
        c += self._abs_jmp(self._hook_e_predecessor)    # JMP predecessor (FF trampoline)

        return bytes(c)

    def _build_cave_cam(self):
        """Hook em vmovss [r15+0x4CC],xmm2 (heading da câmera em graus assinados).
        Salva xmm2 em OFF_CAM_YAW e executa instrução original.
        Patch de 9 bytes — retorna para hook_cam+9 (próxima instrução intacta)."""
        cam_yaw = self.block + self.OFF_CAM_YAW
        ret = self.hook_cam + 9
        c = bytearray()
        c += b'\x50'                                   # push rax
        c += b'\x48\xB8' + struct.pack('<Q', cam_yaw)  # mov rax, cam_yaw_addr
        c += b'\xC5\xFA\x11\x10'                       # vmovss [rax], xmm2
        c += b'\x58'                                   # pop rax
        c += self.ORIG_HOOK_CAM                        # vmovss [r15+0x4CC], xmm2
        c += self._abs_jmp(ret)
        return bytes(c)

    def _install_hooks(self):
        handle = self.pm.process_handle
        init = bytearray(64)
        struct.pack_into('<f', init, 0x30, HEIGHT_BOOST)
        self.pm.write_bytes(self.td, bytes(init), len(init))
        self.pm.write_bytes(self.inv, b'\x00', 1)
        self.pm.write_bytes(self.md, bytes(16), 16)

        # Inicializar tp_block com zeros (flag=0, sem teleport pendente)
        self.pm.write_bytes(self.tp, bytes(20), 20)

        # Se shared memory mode, popular td+0x18 ANTES dos hooks começarem.
        # hook_a escreve td+0x18 via cave_a; sem hook_a, preenchemos aqui.
        if not self.hook_a and self.use_shared_memory_entity:
            if self.refresh_entity_base():
                entity = self._read_ff_shared_entity()
                from server.memory.constants import _REDACT
                if not _REDACT and entity:
                    log.info(
                        "Entity base populated from Freedom Flyer shared memory: entity=%#x  pos=%#x",
                        entity, entity + 0x90,
                    )
                else:
                    log.info("Entity base populated from Freedom Flyer shared memory")
            else:
                log.warning("Failed to read Freedom Flyer shared memory — entity detection may fail")

        caves = [
            (self.OFF_CA, self.hook_a,   self._build_cave_a),
            (self.OFF_CB, self.hook_b,   self._build_cave_b),
            (self.OFF_CC, self.hook_c,   self._build_cave_c),
            (self.OFF_CD, self.hook_d,   self._build_cave_d),
            (self.OFF_CE, self.hook_e,   self._build_cave_e),
            (self.OFF_CF, self.hook_cam, self._build_cave_cam),
        ]
        for off, hook_addr, builder in caves:
            if hook_addr:
                code = builder()
                self.pm.write_bytes(self.block + off, code, len(code))

        # Hooks padrão — patch de 7 bytes (hook_e usa 5 bytes quando predecessor é safetyhook)
        hooks7 = [
            (self.hook_a, self.block + self.OFF_CA),
            (self.hook_b, self.block + self.OFF_CB),
            (self.hook_c, self.block + self.OFF_CC),
            (self.hook_d, self.block + self.OFF_CD),
        ]
        if not self._hook_e_predecessor or self._hook_e_predecessor_patch_size != 5:
            hooks7.append((self.hook_e, self.block + self.OFF_CE))

        if not self._far_mode:
            for hook_addr, cave_addr in hooks7:
                if not hook_addr:
                    continue
                self._remember_orig_bytes(hook_addr)
                self.pm.write_bytes(hook_addr, self._jmp_patch(hook_addr, cave_addr), 7)
            # hook_e com predecessor de 5 bytes (safetyhook / Freedom Flyer)
            if self.hook_e and self._hook_e_predecessor and self._hook_e_predecessor_patch_size == 5:
                self._remember_orig_bytes(self.hook_e)
                self.pm.write_bytes(self.hook_e,
                    self._jmp_patch5(self.hook_e, self.block + self.OFF_CE), 5)
            # Camera hook — patch de 9 bytes (instrução vmovss [r15+0x4CC],xmm2)
            if self.hook_cam:
                orig = self.pm.read_bytes(self.hook_cam, 9)
                if orig[:1] == b'\xE9':
                    orig = self.ORIG_HOOK_CAM
                self.orig_bytes[self.hook_cam] = orig
                self.pm.write_bytes(self.hook_cam,
                    self._jmp_patch9(self.hook_cam, self.block + self.OFF_CF), 9)
        else:
            for hook_addr, cave_addr in hooks7:
                if not hook_addr:
                    continue
                tramp = self._alloc_near(handle, hook_addr, 64)
                if not tramp:
                    raise RuntimeError("Could not allocate trampoline. Close other hooking tools.")
                self._trampolines.append(tramp)
                self.pm.write_bytes(tramp, self._abs_jmp(cave_addr), 14)
                self._remember_orig_bytes(hook_addr)
                self.pm.write_bytes(hook_addr, self._jmp_patch(hook_addr, tramp), 7)
            # hook_e far mode com predecessor de 5 bytes (safetyhook / Freedom Flyer)
            if self.hook_e and self._hook_e_predecessor and self._hook_e_predecessor_patch_size == 5:
                tramp = self._alloc_near(handle, self.hook_e, 64)
                if tramp:
                    self._trampolines.append(tramp)
                    self.pm.write_bytes(tramp, self._abs_jmp(self.block + self.OFF_CE), 14)
                    self._remember_orig_bytes(self.hook_e)
                    self.pm.write_bytes(self.hook_e,
                        self._jmp_patch5(self.hook_e, tramp), 5)
            # Camera hook far mode — 9-byte jmp via trampoline
            if self.hook_cam:
                tramp = self._alloc_near(handle, self.hook_cam, 64)
                if tramp:
                    self._trampolines.append(tramp)
                    self.pm.write_bytes(tramp, self._abs_jmp(self.block + self.OFF_CF), 14)
                    orig = self.pm.read_bytes(self.hook_cam, 9)
                    if orig[:1] == b'\xE9':
                        orig = self.ORIG_HOOK_CAM
                    self.orig_bytes[self.hook_cam] = orig
                    self.pm.write_bytes(self.hook_cam,
                        self._jmp_patch9(self.hook_cam, tramp), 9)
                else:
                    log.warning("Camera hook trampoline alloc failed — camera_heading unavailable")

        self.hooks_installed = True

    def uninstall_hooks(self):
        if not self.pm or not self.orig_bytes:
            return
        for addr, orig in self.orig_bytes.items():
            try:
                self.pm.write_bytes(addr, orig, len(orig))
            except Exception:
                pass
        self.orig_bytes.clear()
        self.hooks_installed = False
