import struct
import logging

from server.memory.constants import _REDACT
from server.memory.hook_cache import _load_hook_offsets, _save_hook_offsets

log = logging.getLogger('cd_server')


class ScannerMixin:
    """Digitalizacao de AOBs, resolucao de enderecos e instalacao inicial de hooks.

    Acessa self.module, self.pm, self.AOB_*, self.ORIG_HOOK_*, self.xyz_addr,
    self.hook_a~e, self.hook_cam, self.world_offset_addr, self.teleport_enabled,
    self.use_shared_memory_entity, self._hook_e_predecessor,
    self._hook_e_predecessor_patch_size, self._alloc_block, self._install_hooks,
    self._read_ff_shared_entity, self._find_phys_delta_hook, self._find_xyz_static
    — definidos em TeleportEngine.__init__ ou mixins.
    """

    def _read_module(self):
        base = self.module.lpBaseOfDll
        size = self.module.SizeOfImage
        data = bytearray(size)
        CHUNK = 0x10000
        for off in range(0, size, CHUNK):
            sz = min(CHUNK, size - off)
            try:
                data[off:off + sz] = self.pm.read_bytes(base + off, sz)
            except Exception:
                pass
        return bytes(data), base

    def _find_phys_delta_hook(self, data, base):
        """Localiza o ponto de hook no physics delta (movaps xmm0,xmm6 / subss xmm9,xmm8)
        confirmado por addps xmm0,[r13] + movups [r13],xmm0 nos 8 bytes seguintes.
        Retorna endereço absoluto ou 0.
        Também detecta quando o ponto já foi hookado por outro mod (bytes = E9 JMP) —
        nesse caso retorna o endereço assim mesmo para que o caller detecte o predecessor."""
        confirm = self.AOB_PHYS_DELTA_CONFIRM
        hook    = self.AOB_PHYS_DELTA_HOOK
        pos = 0
        while pos < len(data) - 18:
            i = data.find(confirm, pos)
            if i == -1:
                break
            if i >= 8 and data[i - 8:i] == hook:
                return base + i - 8
            if i >= 8 and data[i - 8] == 0xE9:
                # Outro mod (ex: OpenFlight) já hookou este ponto — retorna o endereço
                # para que scan_and_hook detecte e configure o chaining.
                return base + i - 8
            pos = i + 1
        return 0

    def _find_xyz_static(self, data, base):
        """Localiza endereços estáticos de XYZ via padrão vmovsd+mov.
        Retorna (x_addr, y_addr, z_addr) ou (0,0,0) se não encontrado."""
        prefix = self.AOB_XYZ_PREFIX
        mid    = self.AOB_XYZ_MID
        pos = 0
        while pos < len(data) - 20:
            i = data.find(prefix, pos)
            if i == -1:
                break
            if data[i + 8:i + 14] == mid:
                disp_xy = struct.unpack_from('<i', data, i + 4)[0]
                xy_addr = base + i + 8 + disp_xy
                disp_z  = struct.unpack_from('<i', data, i + 14)[0]
                z_addr  = base + i + 18 + disp_z
                return xy_addr, xy_addr + 4, z_addr
            pos = i + 1
        return 0, 0, 0

    def _cached_hook_addr(self, saved, key, orig, data, base):
        """Resolve um hook a partir do cache validando os bytes no RVA salvo.

        Retorna o endereço absoluto se os bytes baterem com `orig` (instrução
        original intacta) ou com um JMP nosso (E9) de uma sessão anterior sem
        detach limpo. Caso contrário limpa a entrada do cache e retorna 0.

        Isto evita instalar o trampolim de 7 bytes num endereço stale depois de
        um patch do jogo — o que sobrescreveria código deslocado e crasharia o
        processo ao carregar o save."""
        if key not in saved:
            return 0
        rva = int(saved[key])
        if 0 <= rva <= len(data) - len(orig):
            chunk = data[rva:rva + len(orig)]
            if chunk == orig or chunk[:1] == b'\xE9':
                return base + rva
        saved.pop(key, None)
        _save_hook_offsets(saved)
        log.warning("Cached %s stale or out of range, cache cleared", key)
        return 0

    def scan_and_hook(self):
        data, base = self._read_module()
        saved = _load_hook_offsets()

        # Fingerprint do modulo do jogo. Quando o jogo atualiza, o SizeOfImage
        # quase sempre muda; se nao bater com o que foi salvo, todo o cache de
        # RVAs e considerado stale e descartado, forcando um AOB scan limpo.
        # Invalidacao proativa: complementa a validacao de bytes por-hook abaixo,
        # evitando ate a tentativa de reusar offsets de uma versao anterior.
        module_size = self.module.SizeOfImage
        if saved and saved.get("module_size") != module_size:
            log.warning(
                "Game module changed (image size %s != cached %s) — discarding hook cache",
                module_size, saved.get("module_size"))
            saved = {}

        # Busca endereços estáticos XYZ (não requer hook de entidade)
        self.xyz_addr = self._find_xyz_static(data, base)
        if any(self.xyz_addr):
            if _REDACT:
                log.info("Static XYZ addresses found")
            else:
                log.info("Static XYZ found: X=%#x Y=%#x Z=%#x", *self.xyz_addr)
        else:
            log.warning("Static XYZ AOB not found — position reading will use hook fallback")

        # Tenta shared memory do Freedom Flyer para entity base.
        # Se disponível, hook_a é desnecessário — o entity_base vem da shared memory.
        # Fallback: AOB scan normal para hook_a.
        if self.use_shared_memory_entity and self._read_ff_shared_entity():
            self.hook_a = 0
            log.info("Entity base via Freedom Flyer shared memory — hook_a not needed")
        else:
            if self.use_shared_memory_entity:
                log.info("Freedom Flyer shared memory not available — falling back to hook_a")
            idx = data.find(self.AOB_ENTITY)
            if idx != -1:
                self.hook_a = base + idx + 7  # pula "sub rsp,50; mov rdi,rcx" (7 bytes)
            else:
                self.hook_a = self._cached_hook_addr(saved, "hook_a_rva", self.ORIG_HOOK_A, data, base)
                if self.hook_a:
                    log.info("Entity hook loaded from cache")
                elif not any(self.xyz_addr):
                    raise RuntimeError("Entity hook AOB not found and no static XYZ — update required")
                else:
                    log.warning("Entity hook AOB not found — teleport unavailable, position via static globals")

        idx = data.find(self.AOB_POS)
        if idx != -1:
            self.hook_b = base + idx
        else:
            self.hook_b = self._cached_hook_addr(saved, "hook_b_rva", self.ORIG_HOOK_B, data, base)
            if self.hook_b:
                log.info("Position hook loaded from cache")
            else:
                log.warning("Position hook AOB not found — skipping hook_b")

        idx = data.find(self.AOB_HEALTH)
        if not self.teleport_enabled:
            self.hook_c = 0
            log.info("Teleport disabled — skipping health/invuln hook (hook_c)")
        elif idx != -1:
            self.hook_c = base + idx
        else:
            self.hook_c = self._cached_hook_addr(saved, "hook_c_rva", self.ORIG_HOOK_C, data, base)
            if self.hook_c:
                log.info("Health hook loaded from cache")
            else:
                log.warning("Health hook AOB not found — invuln unavailable")

        # Cache tem prioridade sobre AOB scan: o módulo pode ter múltiplas ocorrências
        # do padrão, e quando o JMP da sessão anterior ainda está instalado, find()
        # pula o endereço correto e acerta uma ocorrência errada.
        if "hook_d_rva" in saved:
            rva = int(saved["hook_d_rva"])
            if 0 <= rva <= len(data) - len(self.ORIG_HOOK_D):
                chunk = data[rva:rva + len(self.ORIG_HOOK_D)]
                if chunk == self.ORIG_HOOK_D:
                    self.hook_d = base + rva
                    log.info("Map dest hook loaded from cache")
                elif chunk[:1] == b'\xE9':
                    self.hook_d = base + rva
                    log.info("Map dest hook recovered (JMP still installed from previous session)")
                else:
                    self.hook_d = 0
                    saved.pop("hook_d_rva", None)
                    _save_hook_offsets(saved)
                    log.warning("Cached map dest hook stale, cache cleared")
            else:
                self.hook_d = 0
                saved.pop("hook_d_rva", None)
                _save_hook_offsets(saved)
                log.warning("Cached map dest hook RVA out of range, cache cleared")
        else:
            idx = data.find(self.AOB_MAP)
            if idx != -1:
                self.hook_d = base + idx + self.AOB_MAP_HOOK_OFFSET
                log.info("Map dest hook found")
            else:
                # Sem cache e JMP stale: busca pelos bytes não-patchados + opcode JMP
                stale_prefix = self.AOB_MAP[:self.AOB_MAP_HOOK_OFFSET] + b'\xE9'
                idx2 = data.find(stale_prefix)
                if idx2 != -1:
                    self.hook_d = base + idx2 + self.AOB_MAP_HOOK_OFFSET
                    log.info("Map dest hook recovered via stale prefix scan")
                else:
                    self.hook_d = 0
                    log.warning("Map dest AOB not found — map marker unavailable")

        hook_e_addr = self._find_phys_delta_hook(data, base)
        if not self.teleport_enabled:
            self.hook_e = 0
            log.info("Teleport disabled — skipping physics delta hook (hook_e)")
        elif hook_e_addr:
            self.hook_e = hook_e_addr
            log.info("Physics delta hook found — teleport via delta injection enabled")
        elif "hook_e_rva" in saved:
            rva = int(saved["hook_e_rva"])
            confirm = self.AOB_PHYS_DELTA_CONFIRM
            if 0 <= rva + 8 + len(confirm) <= len(data):
                chunk = data[rva:rva + len(self.ORIG_HOOK_E)]
                confirm_at_8 = data[rva + 8:rva + 8 + len(confirm)]
                if chunk == self.ORIG_HOOK_E:
                    self.hook_e = base + rva
                    log.info("Physics delta hook loaded from cache")
                elif chunk[:1] == b'\xE9' and confirm_at_8 == confirm:
                    self.hook_e = base + rva
                    log.info("Physics delta hook loaded from cache (E9 at hook point, confirm OK)")
                else:
                    self.hook_e = 0
                    saved.pop("hook_e_rva", None)
                    _save_hook_offsets(saved)
                    log.warning("Cached physics delta hook stale — cache cleared")
            else:
                self.hook_e = 0
                saved.pop("hook_e_rva", None)
                _save_hook_offsets(saved)
                log.warning("Cached physics delta hook RVA out of range — cache cleared")
        else:
            self.hook_e = 0
            log.warning("Physics delta hook not found — teleport fallback to direct write")

        # Detectar se outro mod (ex: OpenFlight) já hookou hook_e antes do companion.
        # Lemos os bytes diretamente via pm.read_bytes (não via data[] do _read_module,
        # que pode ter zeros nessa região se o chunk falhou na leitura em massa).
        # Se o primeiro byte for E9, pode ser: (a) JMP de outro mod ativo, ou
        # (b) JMP stale do companion numa sessão anterior que crashou sem fazer detach.
        # Distinguimos tentando ler 1 byte do destino do JMP: se a página foi liberada
        # (VirtualFreeEx do detach anterior), pymem lança exceção → stale, sobreescrever.
        # Se a leitura funciona → trampoline de outro mod ativo → encadear.
        if self.hook_e and self.teleport_enabled:
            try:
                live_bytes = self.pm.read_bytes(self.hook_e, 5)
                if live_bytes[0] == 0xE9:
                    rel32 = struct.unpack_from('<i', live_bytes, 1)[0]
                    candidate = self.hook_e + 5 + rel32
                    try:
                        self.pm.read_bytes(candidate, 1)
                        self._hook_e_predecessor = candidate
                        # Detectar tamanho do patch do predecessor lendo o trampoline.
                        # safetyhook (Freedom Flyer) usa patch de 5 bytes → trampoline[0:5] == orig[0:5].
                        # OpenFlight / companion stale usam patch de 7 bytes → trampoline[0:7] == orig.
                        try:
                            tramp_header = self.pm.read_bytes(candidate, 7)
                            if tramp_header[:5] == self.ORIG_HOOK_E[:5]:
                                if tramp_header == self.ORIG_HOOK_E:
                                    self._hook_e_predecessor_patch_size = 7
                                    log.info("Existing hook detected at hook_e — will chain through predecessor trampoline (+8)")
                                else:
                                    self._hook_e_predecessor_patch_size = 5
                                    log.info("Existing hook detected at hook_e (5-byte patch, safetyhook) — will chain through predecessor trampoline (+5)")
                            else:
                                self._hook_e_predecessor_patch_size = 7
                                log.info("Existing hook detected at hook_e — will chain through predecessor trampoline (+8)")
                        except Exception:
                            self._hook_e_predecessor_patch_size = 7
                            log.info("Existing hook detected at hook_e — will chain through predecessor trampoline (+8)")
                    except Exception:
                        self._hook_e_predecessor = 0
                        log.info("Stale JMP at hook_e (previous companion session without clean detach) — will overwrite")
                else:
                    self._hook_e_predecessor = 0
            except Exception:
                self._hook_e_predecessor = 0

        idx = data.find(self.AOB_CAM)
        if idx != -1:
            self.hook_cam = base + idx
            log.info("Camera heading hook found")
        elif "hook_cam_rva" in saved:
            rva = int(saved["hook_cam_rva"])
            # Valida que os bytes no endereço cacheado ainda batem com o padrão original,
            # OU que o nosso próprio JMP patch ainda está lá (shutdown não-limpo anterior).
            # Num shutdown abrupto, uninstall_hooks() não roda e o JMP permanece em memória.
            # Na reabertura, os bytes começam com \xE9 — isso é o nosso hook, o endereço é válido.
            # Se os bytes não forem nenhum dos dois casos, o jogo foi reiniciado com layout diferente:
            # limpa o cache e força AOB scan limpo no próximo attach.
            if 0 <= rva <= len(data) - len(self.ORIG_HOOK_CAM):
                chunk = data[rva:rva + len(self.ORIG_HOOK_CAM)]
                if chunk == self.ORIG_HOOK_CAM:
                    self.hook_cam = base + rva
                    log.info("Camera heading hook loaded from cache")
                elif chunk[:1] == b'\xE9':
                    self.hook_cam = base + rva
                    log.info("Camera heading hook recovered (JMP still installed from previous session)")
                else:
                    self.hook_cam = 0
                    saved.pop("hook_cam_rva", None)
                    _save_hook_offsets(saved)
                    log.warning("Cached camera hook stale, cache cleared, will re-scan on next attach")
            else:
                self.hook_cam = 0
                saved.pop("hook_cam_rva", None)
                _save_hook_offsets(saved)
                log.warning("Cached camera hook stale, cache cleared, will re-scan on next attach")
        else:
            self.hook_cam = 0
            log.warning("Camera heading AOB not found — camera_heading unavailable")

        suffix = b'\x0F\x11\x99\x90\x00\x00\x00'
        pos = 0
        while pos < len(data) - 14:
            i = data.find(self.AOB_WORLD, pos)
            if i == -1:
                break
            if data[i + 7:i + 14] == suffix:
                disp = struct.unpack_from('<i', data, i + 3)[0]
                self.world_offset_addr = base + i + 7 + disp
                break
            pos = i + 1
        if not self.world_offset_addr and "world_offset_rva" in saved:
            self.world_offset_addr = base + int(saved["world_offset_rva"])

        self._alloc_block()
        self._install_hooks()
        if not _REDACT:
            if self.hook_cam and self.block:
                log.info("Camera heading: yaw=%#x", self.block + self.OFF_CAM_YAW)
            if self.hook_e and self.block:
                log.info("Player heading: hdg=%#x", self.block + self.OFF_PLYR_HDG)
        save_data = {k: v for k, v in {
            "hook_a_rva":       self.hook_a   - base if self.hook_a   else None,
            "hook_b_rva":       self.hook_b   - base if self.hook_b   else None,
            "hook_c_rva":       self.hook_c   - base if self.hook_c   else None,
            "hook_d_rva":       self.hook_d   - base if self.hook_d   else None,
            "hook_e_rva":       self.hook_e   - base if self.hook_e   else None,
            "hook_cam_rva":     self.hook_cam - base if self.hook_cam else None,
            "world_offset_rva": self.world_offset_addr - base if self.world_offset_addr else None,
        }.items() if v is not None}
        save_data["module_size"] = module_size
        _save_hook_offsets(save_data)
