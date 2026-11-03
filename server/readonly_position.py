"""Read-only position support for CD Companion on exact Steam build 26361232.

Signature provenance: CrimsonDesertTelemetry v2.2.6, MIT licensed,
definitions/build-26361232.json. No game allocation, hook, or write operations.
"""
import ctypes as c
import ctypes.wintypes as w
import hashlib
import math
import re
import struct

EXPECTED_SHA256 = 'DF531A585BF84CEB47779304CC8B3CAAF976AEA88424C32605885623CD4519C7'
PROCESS_NAME = 'CrimsonDesert.exe'
k = c.WinDLL('kernel32', use_last_error=True)
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
k.CreateToolhelp32Snapshot.restype = w.HANDLE
k.CloseHandle.argtypes = [w.HANDLE]
k.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
k.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]

class ProcessEntry(c.Structure):
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                ('th32DefaultHeapID', c.c_size_t), ('th32ModuleID', w.DWORD),
                ('cntThreads', w.DWORD), ('th32ParentProcessID', w.DWORD),
                ('pcPriClassBase', w.LONG), ('dwFlags', w.DWORD), ('szExeFile', w.WCHAR * 260)]

class ModuleEntry(c.Structure):
    _fields_ = [('dwSize', w.DWORD), ('th32ModuleID', w.DWORD), ('th32ProcessID', w.DWORD),
                ('GlblcntUsage', w.DWORD), ('ProccntUsage', w.DWORD),
                ('modBaseAddr', c.c_void_p), ('modBaseSize', w.DWORD), ('hModule', w.HMODULE),
                ('szModule', w.WCHAR * 256), ('szExePath', w.WCHAR * 260)]

k.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
k.Process32NextW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
k.Module32FirstW.argtypes = [w.HANDLE, c.POINTER(ModuleEntry)]

class ReadOnlyPositionEngine:
    read_only = True
    teleport_enabled = False
    teleport_available = False
    hook_cam = hook_e = tp = 0
    attached = hooks_installed = False
    handle = None

    def __init__(self, **kwargs):
        self.addresses = None

    def attach(self):
        self.detach()
        snap = k.CreateToolhelp32Snapshot(2, 0)
        try:
            entry = ProcessEntry(); entry.dwSize = c.sizeof(entry)
            found = k.Process32FirstW(snap, c.byref(entry))
            while found and entry.szExeFile.lower() != PROCESS_NAME.lower():
                found = k.Process32NextW(snap, c.byref(entry))
            if not found:
                raise RuntimeError('Crimson Desert is not running')
            pid = entry.th32ProcessID
        finally:
            k.CloseHandle(snap)
        snap = k.CreateToolhelp32Snapshot(0x18, pid)
        try:
            module = ModuleEntry(); module.dwSize = c.sizeof(module)
            if not k.Module32FirstW(snap, c.byref(module)):
                raise OSError(c.get_last_error(), 'Cannot inspect game module')
            with open(module.szExePath, 'rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest().upper()
            if digest != EXPECTED_SHA256:
                raise RuntimeError('Game executable changed; this repair requires a matching build')
            self.base, self.size = module.modBaseAddr, module.modBaseSize
        finally:
            k.CloseHandle(snap)
        self.handle = k.OpenProcess(0x410, False, pid)  # QUERY_INFORMATION | VM_READ only
        if not self.handle:
            raise OSError(c.get_last_error(), 'Cannot read game process')
        self.attached = True

    def read(self, address, size):
        buffer = c.create_string_buffer(size); count = c.c_size_t()
        if not k.ReadProcessMemory(self.handle, address, buffer, size, c.byref(count)) or count.value != size:
            raise OSError(c.get_last_error(), 'Game memory unavailable')
        return buffer.raw

    def scan_and_hook(self):
        # Retain the upstream server interface; this implementation installs no hooks.
        data = bytearray(self.size)
        for offset in range(0, self.size, 65536):
            size = min(65536, self.size - offset)
            try:
                data[offset:offset + size] = self.read(self.base + offset, size)
            except OSError:
                pass
        matches = list(re.finditer(rb'\xc5\xfb\x11\x3d....\x44\x89\x3d....', data, re.S))
        if len(matches) != 1:
            raise RuntimeError('Expected one validated position signature; refusing ambiguous match')
        i = matches[0].start()
        xy = self.base + i + 8 + struct.unpack_from('<i', data, i + 4)[0]
        z = self.base + i + 15 + struct.unpack_from('<i', data, i + 11)[0]
        if not all(self.base <= address < self.base + self.size - 8 for address in (xy, z)):
            raise RuntimeError('Position targets outside validated game module')
        self.addresses = xy, z
        self.hooks_installed = True

    def get_player_abs(self):
        xy, za = self.addresses
        x, y = struct.unpack('<ff', self.read(xy, 8))
        z, = struct.unpack('<f', self.read(za, 4))
        if not all(math.isfinite(v) and abs(v) < 100_000_000 for v in (x, y, z)):
            return None
        if x*x + z*z < .01 and abs(y) < .1:
            return None
        if x*x + z*z < 10000 and abs(y - 1000.15) < 1:
            return None
        return x, y, z

    def is_process_alive(self):
        code = w.DWORD()
        return bool(self.handle and k.GetExitCodeProcess(self.handle, c.byref(code)) and code.value == 259)

    def detach(self):
        if self.handle:
            k.CloseHandle(self.handle)
        self.handle = None
        self.attached = self.hooks_installed = False

    def refresh_entity_base(self): pass
    def get_player_heading(self): return None
    def get_map_dest(self): return None
    def teleport_to_abs(self, *args): return False, 'Teleport disabled in read-only repair'
    def set_invuln(self, *args): pass
