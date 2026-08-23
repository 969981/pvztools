"""kernel32 wrappers for executing code inside a 32-bit (WOW64) target from
64-bit Python: remote memory allocation, protection changes, remote threads
and waiting (DESIGN §3.6).

All target addresses travel as ``int`` and are < 4GB; the ctypes signatures
use ``c_void_p`` so the values are passed as native-size pointers, which is
what the 64-bit kernel32 expects.  ``VirtualAllocEx`` is always called with
``lpAddress = NULL`` so the allocation naturally lands inside the WOW64
address space.

Importable on any platform; calling anything on non-Windows raises
:class:`~pvztools._win.kernel32.WinApiError` (``code`` = 0).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
from typing import Optional

from .kernel32 import (
    MEM_COMMIT,
    MEM_RESERVE,
    PAGE_EXECUTE_READWRITE,
    WinApiError,
    close_handle,
    is_windows,
    last_error,
)

__all__ = [
    "MEM_RELEASE", "MEM_DECOMMIT",
    "WAIT_OBJECT_0", "WAIT_TIMEOUT", "WAIT_ABANDONED", "WAIT_FAILED", "INFINITE",
    "STILL_ACTIVE",
    "virtual_alloc_ex", "virtual_free_ex", "virtual_protect_ex",
    "create_remote_thread", "wait_for_single_object", "get_exit_code_thread",
    "close_handle",
]

MEM_DECOMMIT = 0x4000
MEM_RELEASE = 0x8000

WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
STILL_ACTIVE = 259

_k32 = None


def _kernel32():
    global _k32
    if _k32 is None:
        if not is_windows():
            raise WinApiError("kernel32", 0, "Win32 API is only available on Windows")
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        wt = ctypes.wintypes
        k.VirtualAllocEx.restype = ctypes.c_void_p
        k.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                     wt.DWORD, wt.DWORD]
        k.VirtualFreeEx.restype = wt.BOOL
        k.VirtualFreeEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
        k.VirtualProtectEx.restype = wt.BOOL
        k.VirtualProtectEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                       wt.DWORD, ctypes.POINTER(wt.DWORD)]
        k.CreateRemoteThread.restype = wt.HANDLE
        k.CreateRemoteThread.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                         ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                                         ctypes.POINTER(wt.DWORD)]
        k.WaitForSingleObject.restype = wt.DWORD
        k.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        k.GetExitCodeThread.restype = wt.BOOL
        k.GetExitCodeThread.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        _k32 = k
    return _k32


def _raise(func: str, detail: str = "") -> None:
    raise WinApiError(func, last_error(), detail)


def _check_target_addr(addr: int, what: str) -> int:
    if not (0 <= addr < 0x1_0000_0000):
        raise ValueError("%s: target address 0x%X outside the 32-bit address space" % (what, addr))
    return addr


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------
def virtual_alloc_ex(h: int, size: int, protect: int = PAGE_EXECUTE_READWRITE,
                     alloc_type: int = MEM_COMMIT | MEM_RESERVE) -> int:
    """Allocate ``size`` bytes in the target (lpAddress = NULL).  Returns the
    target address (< 4GB for a WOW64 target)."""
    if size <= 0:
        raise ValueError("virtual_alloc_ex: size must be > 0")
    p = _kernel32().VirtualAllocEx(h, None, size, alloc_type, protect)
    if not p:
        _raise("VirtualAllocEx", "size=%d protect=0x%X" % (size, protect))
    addr = int(p)
    if addr >= 0x1_0000_0000:  # pragma: no cover - should not happen for WOW64
        _kernel32().VirtualFreeEx(h, addr, 0, MEM_RELEASE)
        raise WinApiError("VirtualAllocEx", 0,
                          "allocation at 0x%X is above 4GB (target is not a 32-bit process?)" % addr)
    return addr


def virtual_free_ex(h: int, addr: int, size: int = 0, free_type: int = MEM_RELEASE) -> None:
    """Release (default) or decommit a region allocated with VirtualAllocEx.
    ``size`` must be 0 for MEM_RELEASE."""
    _check_target_addr(addr, "virtual_free_ex")
    if free_type == MEM_RELEASE:
        size = 0
    if not _kernel32().VirtualFreeEx(h, addr, size, free_type):
        _raise("VirtualFreeEx", "addr=0x%X" % addr)


def virtual_protect_ex(h: int, addr: int, size: int, protect: int) -> int:
    """Change page protection; returns the previous protection."""
    _check_target_addr(addr, "virtual_protect_ex")
    old = ctypes.wintypes.DWORD(0)
    if not _kernel32().VirtualProtectEx(h, addr, size, protect, ctypes.byref(old)):
        _raise("VirtualProtectEx", "addr=0x%X size=%d protect=0x%X" % (addr, size, protect))
    return int(old.value)


# --------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------
def create_remote_thread(h: int, start_addr: int, param: int = 0,
                         stack_size: int = 0, flags: int = 0) -> int:
    """CreateRemoteThread with the start address inside the target (a buffer
    from :func:`virtual_alloc_ex`).  Returns the thread handle (caller must
    :func:`close_handle` it)."""
    _check_target_addr(start_addr, "create_remote_thread")
    tid = ctypes.wintypes.DWORD(0)
    th = _kernel32().CreateRemoteThread(h, None, stack_size, start_addr, param or None,
                                        flags, ctypes.byref(tid))
    if not th:
        _raise("CreateRemoteThread", "start=0x%X" % start_addr)
    return int(th)


def wait_for_single_object(handle: int, timeout_ms: Optional[int] = None) -> int:
    """Returns WAIT_OBJECT_0 / WAIT_TIMEOUT / WAIT_ABANDONED; raises on
    WAIT_FAILED.  ``timeout_ms=None`` waits forever (avoid for remote code)."""
    t = INFINITE if timeout_ms is None else int(timeout_ms)
    r = _kernel32().WaitForSingleObject(handle, t)
    if r == WAIT_FAILED:
        _raise("WaitForSingleObject")
    return int(r)


def get_exit_code_thread(handle: int) -> int:
    """Exit code of a finished thread (``STILL_ACTIVE`` if still running)."""
    code = ctypes.wintypes.DWORD(0)
    if not _kernel32().GetExitCodeThread(handle, ctypes.byref(code)):
        _raise("GetExitCodeThread")
    return int(code.value)
