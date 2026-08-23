"""Thin ctypes wrappers around kernel32 / psapi for controlling a 32-bit
(WOW64) process from 64-bit Python.

All functions are importable on non-Windows; calling them there raises
:class:`WinApiError` (``winerror`` = 0).  ``windll`` is resolved lazily.

Target-side pointers are 4 bytes and travel as ``int``; structures read from
the target must use ``c_uint32`` for pointer fields, never ``c_void_p``.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import struct
import sys
from typing import Dict, Iterator, List, NamedTuple, Optional

__all__ = [
    "WinApiError", "Region", "ModuleInfo", "ProcessEntry",
    "is_windows", "last_error",
    "PROCESS_QUERY_INFORMATION", "PROCESS_QUERY_LIMITED_INFORMATION",
    "PROCESS_VM_READ", "PROCESS_VM_WRITE", "PROCESS_VM_OPERATION",
    "PROCESS_CREATE_THREAD",
    "MEM_COMMIT", "MEM_FREE", "MEM_RESERVE", "MEM_PRIVATE", "MEM_IMAGE", "MEM_MAPPED",
    "PAGE_NOACCESS", "PAGE_READONLY", "PAGE_READWRITE", "PAGE_WRITECOPY",
    "PAGE_EXECUTE", "PAGE_EXECUTE_READ", "PAGE_EXECUTE_READWRITE",
    "PAGE_EXECUTE_WRITECOPY", "PAGE_GUARD",
    "open_process", "close_handle",
    "read", "write",
    "read_u8", "read_u16", "read_u32", "read_i32", "read_f32", "read_ptr",
    "write_u8", "write_u16", "write_u32", "write_i32", "write_f32",
    "iter_regions", "list_processes", "find_processes",
    "enum_modules", "main_module", "is_wow64", "process_image_path",
]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
PROCESS_CREATE_THREAD = 0x0002
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_FREE = 0x10000
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000
MEM_IMAGE = 0x1000000

PAGE_NOACCESS = 0x01
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE = 0x10
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80
PAGE_GUARD = 0x100

_READABLE = (PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ
             | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)
_WRITABLE = (PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE
             | PAGE_EXECUTE_WRITECOPY)
_EXECUTABLE = (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE
               | PAGE_EXECUTE_WRITECOPY)

TH32CS_SNAPPROCESS = 0x00000002
LIST_MODULES_32BIT = 0x01
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260

ERROR_ACCESS_DENIED = 5
ERROR_INVALID_HANDLE = 6
ERROR_NO_MORE_FILES = 18
ERROR_INVALID_PARAMETER = 87
ERROR_PARTIAL_COPY = 299

_HINTS: Dict[int, str] = {
    ERROR_ACCESS_DENIED: "拒绝访问：需要以管理员身份运行（游戏若以管理员启动，Python 也必须是管理员；"
                         "也可能被安全软件拦截）",
    ERROR_INVALID_HANDLE: "无效句柄：进程句柄已关闭或目标进程已退出",
    ERROR_INVALID_PARAMETER: "参数无效：PID 不存在或进程已退出",
    ERROR_PARTIAL_COPY: "只复制了部分内存：地址不可读（未提交 / 保护页 / 超出 4GB）",
}


def is_windows() -> bool:
    return sys.platform == "win32"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class WinApiError(OSError):
    """A Win32 call failed.  ``code`` holds GetLastError() (also exposed as
    ``winerror`` on Windows); ``hint`` is a human-readable (Chinese)
    diagnosis when known."""

    def __init__(self, func: str, winerror: int = 0, detail: str = "") -> None:
        self.func = func
        self.code = winerror
        self.hint = _HINTS.get(winerror, "")
        msg = _format_message(winerror)
        text = "%s failed (error %d%s)" % (func, winerror, (": " + msg) if msg else "")
        if detail:
            text += " -- " + detail
        if self.hint:
            text += " [" + self.hint + "]"
        if is_windows():
            OSError.__init__(self, 0, text, None, winerror)
        else:
            OSError.__init__(self, winerror, text)
        self.strerror = text

    def __str__(self) -> str:
        return self.strerror


def _format_message(code: int) -> str:
    if not is_windows() or code == 0:
        return ""
    try:
        return ctypes.FormatError(code).strip()
    except Exception:  # pragma: no cover
        return ""


def last_error() -> int:
    if not is_windows():
        return 0
    return ctypes.get_last_error()


def _raise(func: str, detail: str = "") -> None:
    raise WinApiError(func, last_error(), detail)


# --------------------------------------------------------------------------
# Lazy DLL binding
# --------------------------------------------------------------------------
_k32 = None


def _kernel32():
    global _k32
    if _k32 is None:
        if not is_windows():
            raise WinApiError("kernel32", 0, "Win32 API is only available on Windows")
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        wt = ctypes.wintypes
        k.OpenProcess.restype = wt.HANDLE
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.CloseHandle.restype = wt.BOOL
        k.CloseHandle.argtypes = [wt.HANDLE]
        k.ReadProcessMemory.restype = wt.BOOL
        k.ReadProcessMemory.argtypes = [wt.HANDLE, wt.LPCVOID, wt.LPVOID, ctypes.c_size_t,
                                        ctypes.POINTER(ctypes.c_size_t)]
        k.WriteProcessMemory.restype = wt.BOOL
        k.WriteProcessMemory.argtypes = [wt.HANDLE, wt.LPVOID, wt.LPCVOID, ctypes.c_size_t,
                                         ctypes.POINTER(ctypes.c_size_t)]
        k.VirtualQueryEx.restype = ctypes.c_size_t
        k.VirtualQueryEx.argtypes = [wt.HANDLE, wt.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
        k.CreateToolhelp32Snapshot.restype = wt.HANDLE
        k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        k.Process32FirstW.restype = wt.BOOL
        k.Process32FirstW.argtypes = [wt.HANDLE, ctypes.c_void_p]
        k.Process32NextW.restype = wt.BOOL
        k.Process32NextW.argtypes = [wt.HANDLE, ctypes.c_void_p]
        k.IsWow64Process.restype = wt.BOOL
        k.IsWow64Process.argtypes = [wt.HANDLE, ctypes.POINTER(wt.BOOL)]
        k.QueryFullProcessImageNameW.restype = wt.BOOL
        k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                 ctypes.POINTER(wt.DWORD)]
        k.K32EnumProcessModulesEx.restype = wt.BOOL
        k.K32EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HMODULE), wt.DWORD,
                                              ctypes.POINTER(wt.DWORD), wt.DWORD]
        k.K32GetModuleFileNameExW.restype = wt.DWORD
        k.K32GetModuleFileNameExW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
        k.K32GetModuleInformation.restype = wt.BOOL
        k.K32GetModuleInformation.argtypes = [wt.HANDLE, wt.HMODULE, ctypes.c_void_p, wt.DWORD]
        _k32 = k
    return _k32


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    # Native (caller-side) layout: a 64-bit caller gets 64-bit fields even
    # for a WOW64 target.
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_uint32),
        ("PartitionId", ctypes.c_uint16),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_uint32),
        ("Protect", ctypes.c_uint32),
        ("Type", ctypes.c_uint32),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


class _MODULEINFO(ctypes.Structure):
    _fields_ = [
        ("lpBaseOfDll", ctypes.c_void_p),
        ("SizeOfImage", ctypes.c_uint32),
        ("EntryPoint", ctypes.c_void_p),
    ]


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------
class Region(NamedTuple):
    base: int
    size: int
    state: int
    protect: int
    type: int

    @property
    def end(self) -> int:
        return self.base + self.size

    @property
    def committed(self) -> bool:
        return self.state == MEM_COMMIT

    @property
    def readable(self) -> bool:
        return (self.state == MEM_COMMIT and bool(self.protect & _READABLE)
                and not (self.protect & PAGE_GUARD))

    @property
    def writable(self) -> bool:
        return self.readable and bool(self.protect & _WRITABLE)

    @property
    def executable(self) -> bool:
        return self.readable and bool(self.protect & _EXECUTABLE)


class ModuleInfo(NamedTuple):
    base: int
    size: int
    path: str

    @property
    def name(self) -> str:
        return self.path.replace("/", "\\").rsplit("\\", 1)[-1]


class ProcessEntry(NamedTuple):
    pid: int
    exe_name: str


# --------------------------------------------------------------------------
# Handles
# --------------------------------------------------------------------------
def open_process(pid: int, write: bool = False, create_thread: bool = False,
                 access: Optional[int] = None) -> int:
    """OpenProcess with minimal rights (never PROCESS_ALL_ACCESS).

    read-only:      QUERY_INFORMATION | VM_READ
    write:          + VM_WRITE | VM_OPERATION
    create_thread:  + CREATE_THREAD (implies write rights)
    """
    if access is None:
        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if write or create_thread:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        if create_thread:
            access |= PROCESS_CREATE_THREAD
    h = _kernel32().OpenProcess(access, False, int(pid))
    if not h:
        _raise("OpenProcess", "pid=%d access=0x%X" % (pid, access))
    return int(h)


def close_handle(h: int) -> None:
    if not h:
        return
    if not _kernel32().CloseHandle(h):
        _raise("CloseHandle")


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------
def read(h: int, addr: int, n: int) -> bytes:
    """ReadProcessMemory; raises WinApiError on failure or short read."""
    if n <= 0:
        return b""
    buf = ctypes.create_string_buffer(n)
    got = ctypes.c_size_t(0)
    ok = _kernel32().ReadProcessMemory(h, addr, buf, n, ctypes.byref(got))
    if not ok or got.value != n:
        _raise("ReadProcessMemory", "addr=0x%X n=%d got=%d" % (addr, n, got.value))
    return buf.raw


def write(h: int, addr: int, data: bytes) -> None:
    """WriteProcessMemory; raises WinApiError on failure or short write."""
    data = bytes(data)
    if not data:
        return
    buf = ctypes.create_string_buffer(data, len(data))
    done = ctypes.c_size_t(0)
    ok = _kernel32().WriteProcessMemory(h, addr, buf, len(data), ctypes.byref(done))
    if not ok or done.value != len(data):
        _raise("WriteProcessMemory", "addr=0x%X n=%d done=%d" % (addr, len(data), done.value))


def read_u8(h: int, addr: int) -> int:
    return read(h, addr, 1)[0]


def read_u16(h: int, addr: int) -> int:
    return struct.unpack("<H", read(h, addr, 2))[0]


def read_u32(h: int, addr: int) -> int:
    return struct.unpack("<I", read(h, addr, 4))[0]


def read_i32(h: int, addr: int) -> int:
    return struct.unpack("<i", read(h, addr, 4))[0]


def read_f32(h: int, addr: int) -> float:
    return struct.unpack("<f", read(h, addr, 4))[0]


def read_ptr(h: int, addr: int) -> int:
    """Read a 4-byte target pointer (WOW64)."""
    return read_u32(h, addr)


def _check_int(v: int, lo: int, hi: int, what: str) -> int:
    """Out-of-range values raise instead of being silently masked."""
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError("%s: expected int, got %r" % (what, v))
    if not (lo <= v <= hi):
        raise ValueError("%s: value %r out of range [%d, %d]" % (what, v, lo, hi))
    return v


def write_u8(h: int, addr: int, v: int) -> None:
    write(h, addr, struct.pack("<B", _check_int(v, 0, 0xFF, "write_u8")))


def write_u16(h: int, addr: int, v: int) -> None:
    write(h, addr, struct.pack("<H", _check_int(v, 0, 0xFFFF, "write_u16")))


def write_u32(h: int, addr: int, v: int) -> None:
    write(h, addr, struct.pack("<I", _check_int(v, 0, 0xFFFFFFFF, "write_u32")))


def write_i32(h: int, addr: int, v: int) -> None:
    write(h, addr, struct.pack("<i", _check_int(v, -0x80000000, 0x7FFFFFFF, "write_i32")))


def write_f32(h: int, addr: int, v: float) -> None:
    write(h, addr, struct.pack("<f", v))


def iter_regions(h: int, start: int = 0, end: int = 0x1_0000_0000) -> Iterator[Region]:
    """Walk VirtualQueryEx regions in [start, end).  The default range is the
    whole 4GB WOW64 address space."""
    k = _kernel32()
    mbi = _MEMORY_BASIC_INFORMATION()
    addr = start
    while addr < end:
        got = k.VirtualQueryEx(h, addr, ctypes.byref(mbi), ctypes.sizeof(mbi))
        if got == 0:
            if last_error() in (ERROR_INVALID_PARAMETER, 0):
                break  # past the end of the address space
            _raise("VirtualQueryEx", "addr=0x%X" % addr)
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        yield Region(base, size, mbi.State, mbi.Protect, mbi.Type)
        nxt = base + size
        if nxt <= addr:
            break
        addr = nxt


# --------------------------------------------------------------------------
# Processes / modules
# --------------------------------------------------------------------------
def list_processes() -> List[ProcessEntry]:
    """Toolhelp32 snapshot -> [(pid, exe_name)]."""
    k = _kernel32()
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        _raise("CreateToolhelp32Snapshot")
    out: List[ProcessEntry] = []
    try:
        pe = _PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        if not k.Process32FirstW(snap, ctypes.byref(pe)):
            if last_error() == ERROR_NO_MORE_FILES:
                return out
            _raise("Process32FirstW")
        while True:
            out.append(ProcessEntry(int(pe.th32ProcessID), pe.szExeFile))
            if not k.Process32NextW(snap, ctypes.byref(pe)):
                break
    finally:
        k.CloseHandle(snap)
    return out


def find_processes(exe_name: str) -> List[int]:
    """PIDs whose exe name matches (case-insensitive)."""
    name = exe_name.lower()
    return [p.pid for p in list_processes() if p.exe_name.lower() == name]


def enum_modules(h: int, flags: int = LIST_MODULES_32BIT) -> List[ModuleInfo]:
    """EnumProcessModulesEx (default: 32-bit modules only) with path + size."""
    k = _kernel32()
    wt = ctypes.wintypes
    count = 256
    while True:
        arr = (wt.HMODULE * count)()
        needed = wt.DWORD(0)
        if not k.K32EnumProcessModulesEx(h, arr, ctypes.sizeof(arr), ctypes.byref(needed), flags):
            _raise("EnumProcessModulesEx")
        n = needed.value // ctypes.sizeof(wt.HMODULE)
        if n <= count:
            break
        count = n
    out: List[ModuleInfo] = []
    for i in range(n):
        hmod = arr[i]
        buf = ctypes.create_unicode_buffer(1024)
        ln = k.K32GetModuleFileNameExW(h, hmod, buf, 1024)
        path = buf.value if ln else ""
        mi = _MODULEINFO()
        if not k.K32GetModuleInformation(h, hmod, ctypes.byref(mi), ctypes.sizeof(mi)):
            _raise("GetModuleInformation", "module=%s" % path)
        out.append(ModuleInfo(int(mi.lpBaseOfDll or 0), int(mi.SizeOfImage), path))
    return out


def main_module(h: int) -> ModuleInfo:
    """The process's main executable image (first 32-bit module)."""
    mods = enum_modules(h, LIST_MODULES_32BIT)
    if not mods:
        # the enumeration itself succeeded: GetLastError() would be stale
        raise WinApiError("EnumProcessModulesEx", 0,
                          "no 32-bit modules (not a 32-bit / WOW64 process, or not initialised yet)")
    return mods[0]


def is_wow64(h: int) -> bool:
    flag = ctypes.wintypes.BOOL(0)
    if not _kernel32().IsWow64Process(h, ctypes.byref(flag)):
        _raise("IsWow64Process")
    return bool(flag.value)


def process_image_path(h: int) -> str:
    size = ctypes.wintypes.DWORD(1024)
    buf = ctypes.create_unicode_buffer(size.value)
    if not _kernel32().QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
        _raise("QueryFullProcessImageNameW")
    return buf.value
