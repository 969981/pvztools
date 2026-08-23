"""user32 wrappers: window lookup and message posting.

Used by the game finder (window class ``MainWindow``) and by the external
backend for key / mouse injection via ``PostMessageW`` (DESIGN §6).

:mod:`pvztools.process.attach` carries a private, self-contained copy of the
``EnumWindows`` + ``GetClassNameW`` walk (``_pids_with_window_class``) so that
it stays importable while this package is being built; the public surface
for the same operation is :func:`pids_with_window_class` here.

Importable on any platform; calls raise
:class:`~pvztools._win.kernel32.WinApiError` on non-Windows.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
from typing import Callable, List, NamedTuple, Optional, Set

from .kernel32 import WinApiError, is_windows, last_error

__all__ = [
    "GAME_WINDOW_CLASS",
    "WM_KEYDOWN", "WM_KEYUP", "WM_CHAR", "WM_LBUTTONDOWN", "WM_LBUTTONUP",
    "WM_RBUTTONDOWN", "WM_RBUTTONUP", "WM_MOUSEMOVE",
    "VK_SPACE", "VK_ESCAPE", "VK_RETURN",
    "WindowInfo",
    "find_window", "post_message", "send_message", "enum_windows",
    "get_window_thread_process_id", "get_class_name", "get_window_text",
    "windows_of_pid", "pids_with_window_class", "find_game_window",
    "SW_RESTORE", "SW_SHOW",
    "get_client_rect", "get_dpi_for_window", "get_dpi_for_system",
    "client_size", "get_foreground_window", "is_foreground",
    "focus_window", "post_key",
]

GAME_WINDOW_CLASS = "MainWindow"

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205

SW_SHOW = 5
SW_RESTORE = 9

VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_SPACE = 0x20

_u32 = None
_ENUM_PROTO = None


def _user32():
    global _u32, _ENUM_PROTO
    if _u32 is None:
        if not is_windows():
            raise WinApiError("user32", 0, "Win32 API is only available on Windows")
        u = ctypes.WinDLL("user32", use_last_error=True)
        wt = ctypes.wintypes
        _ENUM_PROTO = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        u.FindWindowW.restype = wt.HWND
        u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        u.PostMessageW.restype = wt.BOOL
        u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.SendMessageW.restype = wt.LPARAM  # LRESULT
        u.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.EnumWindows.restype = wt.BOOL
        u.EnumWindows.argtypes = [_ENUM_PROTO, wt.LPARAM]
        u.GetWindowThreadProcessId.restype = wt.DWORD
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetClassNameW.restype = ctypes.c_int
        u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        u.GetClientRect.restype = wt.BOOL
        u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        u.GetForegroundWindow.restype = wt.HWND
        u.GetForegroundWindow.argtypes = []
        u.SetForegroundWindow.restype = wt.BOOL
        u.SetForegroundWindow.argtypes = [wt.HWND]
        u.BringWindowToTop.restype = wt.BOOL
        u.BringWindowToTop.argtypes = [wt.HWND]
        u.ShowWindow.restype = wt.BOOL
        u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        u.AttachThreadInput.restype = wt.BOOL
        u.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
        u.IsIconic.restype = wt.BOOL
        u.IsIconic.argtypes = [wt.HWND]
        # Win10 1607+; absent on older user32 -- guarded at call time
        for name in ("GetDpiForWindow", "GetDpiForSystem"):
            fn = getattr(u, name, None)
            if fn is not None:
                fn.restype = wt.UINT
                fn.argtypes = [wt.HWND] if name == "GetDpiForWindow" else []
        _u32 = u
    return _u32


def _raise(func: str, detail: str = "") -> None:
    raise WinApiError(func, last_error(), detail)


class WindowInfo(NamedTuple):
    hwnd: int
    pid: int
    class_name: str
    title: str


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------
def find_window(class_name: Optional[str], title: Optional[str] = None) -> int:
    """FindWindowW; returns 0 when not found (no exception)."""
    h = _user32().FindWindowW(class_name, title)
    return int(h or 0)


def post_message(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> None:
    if not _user32().PostMessageW(hwnd, msg, wparam, lparam):
        _raise("PostMessageW", "hwnd=0x%X msg=0x%X" % (hwnd, msg))


def send_message(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> int:
    """SendMessageW (synchronous; blocks until the target handles it)."""
    return int(_user32().SendMessageW(hwnd, msg, wparam, lparam))


def get_window_thread_process_id(hwnd: int) -> int:
    pid = ctypes.wintypes.DWORD(0)
    tid = _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not tid:
        _raise("GetWindowThreadProcessId", "hwnd=0x%X" % hwnd)
    return int(pid.value)


def get_class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    n = _user32().GetClassNameW(hwnd, buf, 256)
    if not n:
        _raise("GetClassNameW", "hwnd=0x%X" % hwnd)
    return buf.value


def get_window_text(hwnd: int) -> str:
    """Window title ('' for windows without one; never raises)."""
    buf = ctypes.create_unicode_buffer(512)
    _user32().GetWindowTextW(hwnd, buf, 512)
    return buf.value


def enum_windows(callback: Optional[Callable[[int], bool]] = None) -> List[int]:
    """Top-level window handles.  ``callback(hwnd) -> bool`` may stop the
    enumeration early by returning False."""
    u = _user32()
    out: List[int] = []

    def cb(hwnd, _lp):
        h = int(hwnd)
        out.append(h)
        if callback is not None and callback(h) is False:
            return False
        return True

    fn = _ENUM_PROTO(cb)
    if not u.EnumWindows(fn, 0):
        # returning FALSE from the callback also makes EnumWindows return
        # FALSE with last_error 0 -- that is not a failure
        if last_error() not in (0, 1223):
            _raise("EnumWindows")
    return out


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def windows_of_pid(pid: int) -> List[WindowInfo]:
    """All top-level windows belonging to ``pid``."""
    found: List[WindowInfo] = []
    for h in enum_windows():
        try:
            owner = get_window_thread_process_id(h)
        except WinApiError:
            continue
        if owner != pid:
            continue
        try:
            cls = get_class_name(h)
        except WinApiError:
            cls = ""
        found.append(WindowInfo(h, owner, cls, get_window_text(h)))
    return found


def pids_with_window_class(class_name: str = GAME_WINDOW_CLASS) -> Optional[Set[int]]:
    """PIDs owning a top-level window of class ``class_name``; ``None`` when
    the check is impossible (non-Windows / user32 failure) so callers can
    fall back to name preference -- same contract as the private helper in
    :mod:`pvztools.process.attach`."""
    if not is_windows():
        return None
    try:
        found: Set[int] = set()
        for h in enum_windows():
            try:
                if get_class_name(h) == class_name:
                    found.add(get_window_thread_process_id(h))
            except WinApiError:
                continue
        return found
    except Exception:
        return None


def find_game_window(pid: Optional[int] = None,
                     class_name: str = GAME_WINDOW_CLASS) -> int:
    """HWND of the game's main window (0 if none).  With ``pid`` the window
    must belong to that process; otherwise the first ``class_name`` window
    wins (FindWindowW)."""
    if pid is None:
        return find_window(class_name, None)
    for w in windows_of_pid(pid):
        if w.class_name == class_name:
            return w.hwnd
    return 0


# --------------------------------------------------------------------------
# Client geometry / DPI / focus (used by pvztools.control.window)
# --------------------------------------------------------------------------
def get_client_rect(hwnd: int) -> tuple:
    """``GetClientRect`` as ``(width, height)`` in *this process's* DPI
    space; ``(0, 0)`` on failure."""
    rect = ctypes.wintypes.RECT()
    if not _user32().GetClientRect(hwnd, ctypes.byref(rect)):
        return 0, 0
    return int(rect.right - rect.left), int(rect.bottom - rect.top)


def get_dpi_for_window(hwnd: int) -> int:
    """``GetDpiForWindow`` (0 when the API is missing, pre-Win10 1607)."""
    fn = getattr(_user32(), "GetDpiForWindow", None)
    if fn is None:
        return 0
    try:
        return int(fn(hwnd))
    except Exception:
        return 0


def get_dpi_for_system() -> int:
    """``GetDpiForSystem`` -- the DPI *this* (possibly DPI-unaware, hence
    virtualised) process sees; 0 when the API is missing."""
    fn = getattr(_user32(), "GetDpiForSystem", None)
    if fn is None:
        return 0
    try:
        return int(fn())
    except Exception:
        return 0


def client_size(hwnd: int) -> tuple:
    """Client size in the *window's own* DPI space -- the space its
    ``WM_*BUTTON*`` ``lParam`` coordinates are interpreted in.

    A DPI-unaware Python sees a per-monitor-aware game window's 800x600
    client as 640x480 at 125 % scaling; ``GetClientRect`` is rescaled by
    ``GetDpiForWindow / GetDpiForSystem`` so the result is the 800x600 the
    game actually draws (and the coordinates we must post are then scaled
    *back* by the same ratio, see :class:`pvztools.control.window.GameWindow`).
    """
    w, h = get_client_rect(hwnd)
    if w <= 0 or h <= 0:
        return 0, 0
    win_dpi, my_dpi = get_dpi_for_window(hwnd), get_dpi_for_system()
    if win_dpi > 0 and my_dpi > 0 and win_dpi != my_dpi:
        w = int(round(w * win_dpi / float(my_dpi)))
        h = int(round(h * win_dpi / float(my_dpi)))
    return w, h


def get_foreground_window() -> int:
    return int(_user32().GetForegroundWindow() or 0)


def is_foreground(hwnd: int) -> bool:
    return get_foreground_window() == int(hwnd)


def focus_window(hwnd: int) -> bool:
    """Bring ``hwnd`` to the foreground.  Windows refuses
    ``SetForegroundWindow`` from a background process, so the calling
    thread's input queue is temporarily attached to the current foreground
    window's thread (``AttachThreadInput``), the window is restored
    (``SW_RESTORE`` also un-minimises it) and raised.  Returns whether the
    window is the foreground window afterwards."""
    u = _user32()
    hwnd = int(hwnd)
    if u.GetForegroundWindow() == hwnd:
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD
    me = k32.GetCurrentThreadId()
    fg = u.GetForegroundWindow()
    fg_tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_tid) and fg_tid != me and bool(u.AttachThreadInput(me, fg_tid, True))
    try:
        u.ShowWindow(hwnd, SW_RESTORE if u.IsIconic(hwnd) else SW_SHOW)
        u.SetForegroundWindow(hwnd)
        u.BringWindowToTop(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(me, fg_tid, False)
    return u.GetForegroundWindow() == hwnd


def post_key(hwnd: int, vk: int) -> None:
    """``WM_KEYDOWN`` + ``WM_KEYUP`` for virtual key ``vk``."""
    post_message(hwnd, WM_KEYDOWN, vk, 1)
    post_message(hwnd, WM_KEYUP, vk, 1 | (1 << 30) | (1 << 31))
