"""Single-controller mutex (one pvztools instance controls a game at a time).

``acquire(pid) -> ControllerLock`` creates the named Windows mutex
``Local\\pvztools-controller-<pid>`` (kernel32 ``CreateMutexW``).  When the
mutex already exists (``GetLastError() == ERROR_ALREADY_EXISTS``) another
pvztools process is controlling that game and :class:`ControllerBusy` is
raised.  The lock is released by :meth:`ControllerLock.release` (called from
``Session.close()``) and automatically by the kernel when the process dies,
so a crashed controller never leaves a stale lock.  There is deliberately no
"steal" option.

Off Windows (or when kernel32 is unavailable) the lock is a no-op.

The module-level callables :func:`_create_mutex`, :func:`_release_mutex` and
:func:`_close_handle` are the monkeypatch points used by the tests.
"""
from __future__ import annotations

import sys
from typing import Optional, Tuple

__all__ = ["ControllerBusy", "ControllerLock", "acquire", "mutex_name", "ERROR_ALREADY_EXISTS"]

ERROR_ALREADY_EXISTS = 183


class ControllerBusy(Exception):
    """Another pvztools instance already controls this game."""

    def __init__(self, pid: int) -> None:
        self.pid = int(pid)
        super().__init__("已有另一个 pvztools 实例正在控制这个游戏（pid %d）；"
                         "请先关闭它（或等待它退出）再试" % self.pid)


def mutex_name(pid: int) -> str:
    return "Local\\pvztools-controller-%d" % int(pid)


# --------------------------------------------------------------------------- #
# Win32 primitives (monkeypatch points)
# --------------------------------------------------------------------------- #
def _create_mutex(name: str) -> Tuple[Optional[int], int]:
    """Return ``(handle, last_error)``; ``(None, 0)`` when not on Windows."""
    if sys.platform != "win32":
        return None, 0
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.c_wchar_p]
    k.CreateMutexW.restype = wintypes.HANDLE
    h = k.CreateMutexW(None, True, name)
    err = ctypes.get_last_error()
    return (int(h) if h else None), err


def _release_mutex(handle: int) -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.ReleaseMutex.argtypes = [wintypes.HANDLE]
    k.ReleaseMutex(handle)


def _close_handle(handle: int) -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle(handle)


# --------------------------------------------------------------------------- #
# lock object
# --------------------------------------------------------------------------- #
class ControllerLock:
    def __init__(self, pid: int, handle: Optional[int]) -> None:
        self.pid = int(pid)
        self.handle = handle
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        h, self.handle = self.handle, None
        if h is None:
            return
        try:
            _release_mutex(h)
        except Exception:
            pass
        try:
            _close_handle(h)
        except Exception:
            pass

    def __enter__(self) -> "ControllerLock":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def __del__(self) -> None:  # pragma: no cover
        try:
            self.release()
        except Exception:
            pass

    def __repr__(self) -> str:
        return "ControllerLock(pid=%d, %s)" % (self.pid, "released" if self._released else "held")


def acquire(pid: int) -> ControllerLock:
    """Take the controller mutex for ``pid``; raise :class:`ControllerBusy` if held."""
    handle, err = _create_mutex(mutex_name(pid))
    if handle is not None and err == ERROR_ALREADY_EXISTS:
        try:
            _close_handle(handle)
        except Exception:
            pass
        raise ControllerBusy(pid)
    return ControllerLock(pid, handle)
