"""Find the game process and attach to it.

Public API
----------
``find_game_processes(names=None, extra_names=None) -> list[ProcessInfo]``
    Enumerate processes whose image name is one of the known game names
    (``popcapgame1.exe``, ``PlantsVsZombies.exe``) or user supplied 改版 names.
    When both the Steam launcher (a PopCap DRM wrapper, no game window) and
    the inner game exist, the list is reduced to the real game: first by the
    presence of a top-level window of class ``MainWindow`` (user32
    ``EnumWindows``), then by name preference (``popcapgame1.exe`` wins).

``attach(pid=None, *, write=False, names=None) -> Session``
    Open the process, assert it is a 32-bit (WOW64) process, read the live PE
    header at the image base and return a :class:`Session`.

Errors are human readable (Chinese, matching DESIGN §3.6 ``doctor`` wording):
:class:`GameNotRunning`, :class:`MultipleGames`, :class:`AccessDenied`,
:class:`NotA32BitProcess`.

Win32 access goes through :mod:`pvztools._win.kernel32`.  That module is
resolved lazily via :func:`_kernel32` so tests can monkeypatch it; when it is
not importable (e.g. the package is being built concurrently) a tiny ctypes
fallback with the same interface is used so live attach still works.
"""
from __future__ import annotations

import struct
import sys
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

__all__ = [
    "DEFAULT_NAMES",
    "DEFAULT_IMAGE_BASE",
    "ProcessInfo",
    "Session",
    "ProcessError",
    "GameNotRunning",
    "MultipleGames",
    "AccessDenied",
    "NotA32BitProcess",
    "ControllerBusy",
    "find_game_processes",
    "attach",
]

#: Known image names.  Order matters: earlier = preferred when several match.
DEFAULT_NAMES: Tuple[str, ...] = ("popcapgame1.exe", "PlantsVsZombies.exe")
#: Where the game PE is mapped (no ASLR; verified for Steam GOTY 1.2.0.1096).
DEFAULT_IMAGE_BASE = 0x400000
#: Window class of the real game window (launcher has none).
GAME_WINDOW_CLASS = "MainWindow"

from .lock import ControllerBusy, ControllerLock, acquire as _acquire_lock  # noqa: E402

Reader = Callable[[int, int], bytes]
Writer = Callable[[int, bytes], None]


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
class ProcessError(Exception):
    """Base class for attach failures."""


class GameNotRunning(ProcessError):
    def __init__(self, msg: str = "游戏没有运行") -> None:
        super().__init__(msg)


class MultipleGames(ProcessError):
    """Several candidate processes; pass ``pid=`` to choose."""

    def __init__(self, candidates: Sequence["ProcessInfo"]) -> None:
        self.candidates = list(candidates)
        lines = ", ".join("%s (pid %d)" % (c.name, c.pid) for c in self.candidates)
        super().__init__("找到多个游戏进程，请用 pid= 指定：%s" % lines)


class AccessDenied(ProcessError):
    def __init__(self, msg: str = "无法打开游戏进程；游戏若以管理员运行，Python 也需要管理员") -> None:
        super().__init__(msg)


class NotA32BitProcess(ProcessError):
    def __init__(self, pid: int) -> None:
        self.pid = pid
        super().__init__("进程 %d 不是 32 位 (WOW64) 进程，不是支持的游戏" % pid)


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
class ProcessInfo(NamedTuple):
    pid: int
    name: str
    path: Optional[str]


# --------------------------------------------------------------------------- #
# kernel32 resolution (monkeypatch point)
# --------------------------------------------------------------------------- #
def _kernel32() -> Any:
    """Return an object with the ``pvztools._win.kernel32`` interface."""
    try:
        from pvztools._win import kernel32  # type: ignore
        return kernel32
    except Exception:  # pragma: no cover - depends on sibling module
        return _MiniKernel32


class _MiniKernel32:
    """Minimal ctypes fallback used only when ``pvztools._win`` is absent."""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010
    PROCESS_VM_WRITE = 0x0020
    PROCESS_VM_OPERATION = 0x0008
    PROCESS_CREATE_THREAD = 0x0002

    @staticmethod
    def _k():
        import ctypes
        return ctypes.WinDLL("kernel32", use_last_error=True)

    @classmethod
    def list_processes(cls) -> List[ProcessInfo]:
        import ctypes
        from ctypes import wintypes
        k = cls._k()

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
            ]

        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snap = k.CreateToolhelp32Snapshot(0x2, 0)
        out: List[ProcessInfo] = []
        if snap in (None, -1, 0xFFFFFFFFFFFFFFFF):
            return out
        try:
            e = PROCESSENTRY32W()
            e.dwSize = ctypes.sizeof(e)
            ok = k.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                out.append(ProcessInfo(int(e.th32ProcessID), e.szExeFile, None))
                ok = k.Process32NextW(snap, ctypes.byref(e))
        finally:
            k.CloseHandle(snap)
        return out

    @classmethod
    def open_process(cls, pid: int, write: bool = False, create_thread: bool = False) -> int:
        import ctypes
        from ctypes import wintypes
        k = cls._k()
        access = cls.PROCESS_QUERY_LIMITED_INFORMATION | cls.PROCESS_VM_READ
        if write:
            access |= cls.PROCESS_VM_WRITE | cls.PROCESS_VM_OPERATION
        if create_thread:
            access |= cls.PROCESS_CREATE_THREAD
        k.OpenProcess.restype = wintypes.HANDLE
        h = k.OpenProcess(access, False, int(pid))
        if not h:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        return int(h)

    @classmethod
    def close_handle(cls, h: int) -> None:
        import ctypes
        from ctypes import wintypes
        k = cls._k()
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle(h)

    @classmethod
    def is_wow64(cls, h: int) -> bool:
        import ctypes
        from ctypes import wintypes
        k = cls._k()
        flag = wintypes.BOOL(0)
        k.IsWow64Process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        if not k.IsWow64Process(h, ctypes.byref(flag)):
            raise OSError(ctypes.get_last_error(), "IsWow64Process failed")
        return bool(flag.value)

    @classmethod
    def process_image_path(cls, h: int) -> Optional[str]:
        import ctypes
        from ctypes import wintypes
        k = cls._k()
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                 ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)]
        if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return buf.value

    @classmethod
    def read(cls, h: int, addr: int, n: int) -> bytes:
        import ctypes
        from ctypes import wintypes
        k = cls._k()
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t(0)
        k.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        if not k.ReadProcessMemory(h, addr, buf, n, ctypes.byref(got)) or got.value != n:
            raise OSError(ctypes.get_last_error(), "ReadProcessMemory(0x%X, %d) failed" % (addr, n))
        return buf.raw

    @classmethod
    def write(cls, h: int, addr: int, data: bytes) -> None:
        import ctypes
        from ctypes import wintypes
        k = cls._k()
        n = len(data)
        put = ctypes.c_size_t(0)
        k.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_char_p,
                                         ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        if not k.WriteProcessMemory(h, addr, data, n, ctypes.byref(put)) or put.value != n:
            raise OSError(ctypes.get_last_error(), "WriteProcessMemory(0x%X, %d) failed" % (addr, n))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _entry_fields(e: Any) -> ProcessInfo:
    """Normalise a process entry (NamedTuple / dataclass / tuple) to ProcessInfo."""
    if isinstance(e, ProcessInfo):
        return e
    pid = getattr(e, "pid", None)
    if pid is None:
        pid = e[0]
    name = (getattr(e, "name", None) or getattr(e, "exe_name", None)
            or getattr(e, "exe", None) or getattr(e, "exe_file", None))
    if name is None:
        name = e[1]
    path = getattr(e, "path", None)
    return ProcessInfo(int(pid), str(name), path)


def _pids_with_window_class(cls_name: str = GAME_WINDOW_CLASS) -> Optional[set]:
    """PIDs owning a top-level window of class ``cls_name``.

    Returns ``None`` if the check could not be performed (non-Windows, or
    user32 failure) so callers can fall back to name preference.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        found = set()
        buf = ctypes.create_unicode_buffer(256)
        pid = wintypes.DWORD(0)
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _lp):
            n = u.GetClassNameW(hwnd, buf, 256)
            if n and buf.value == cls_name:
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.add(int(pid.value))
            return True

        u.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.EnumWindows.argtypes = [proto, wintypes.LPARAM]
        if not u.EnumWindows(proto(cb), 0):
            return None
        return found
    except Exception:
        return None


def _parse_pe_header(hdr: bytes) -> Dict[str, int]:
    """Parse DOS + NT headers from the first bytes of a mapped image."""
    if len(hdr) < 0x40 or hdr[:2] != b"MZ":
        raise ProcessError("进程映像基址处没有 MZ 头，不是期望的 PE 映像")
    e_lfanew = struct.unpack_from("<I", hdr, 0x3C)[0]
    if e_lfanew + 0x58 > len(hdr) or hdr[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise ProcessError("PE 签名无效（e_lfanew=0x%X）" % e_lfanew)
    fh = e_lfanew + 4
    machine, nsec, stamp = struct.unpack_from("<HHI", hdr, fh)
    opt = fh + 20
    magic = struct.unpack_from("<H", hdr, opt)[0]
    if magic == 0x10B:  # PE32
        image_base = struct.unpack_from("<I", hdr, opt + 28)[0]
    else:  # PE32+
        image_base = struct.unpack_from("<Q", hdr, opt + 24)[0]
    size_of_image = struct.unpack_from("<I", hdr, opt + 56)[0]
    return {"machine": machine, "stamp": stamp, "image_base": image_base,
            "size_of_image": size_of_image, "magic": magic, "sections": nsec}


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def find_game_processes(names: Optional[Iterable[str]] = None,
                        extra_names: Optional[Iterable[str]] = None,
                        *, prefer: bool = True) -> List[ProcessInfo]:
    """Return game candidates, most preferred first.

    ``names`` replaces :data:`DEFAULT_NAMES`; ``extra_names`` (改版 exe names)
    is appended.  With ``prefer=True`` (default) launcher/inner-game pairs are
    collapsed to the real game (window class, then name order).
    """
    wanted = [n.lower() for n in (names if names is not None else DEFAULT_NAMES)]
    wanted += [n.lower() for n in (extra_names or ())]
    rank = {n: i for i, n in enumerate(wanted)}
    k = _kernel32()
    cands: List[ProcessInfo] = []
    for e in k.list_processes():
        info = _entry_fields(e)
        if info.name.lower() in rank:
            cands.append(info)
    cands.sort(key=lambda c: (rank[c.name.lower()], c.pid))
    if prefer and len(cands) > 1:
        cands = _prefer_real_game(cands)
    return cands


def _prefer_real_game(cands: List[ProcessInfo]) -> List[ProcessInfo]:
    """Drop candidates that are evidently the DRM launcher."""
    with_window = _pids_with_window_class()
    if with_window is not None:
        windowed = [c for c in cands if c.pid in with_window]
        if windowed:
            return windowed
    # Name preference: the Steam launcher (PlantsVsZombies.exe) spawns the
    # real game as popcapgame1.exe; when the inner game is present drop the
    # launcher.  Other names (改版 exes, a second popcapgame1) are kept.
    inner, launcher = DEFAULT_NAMES[0].lower(), DEFAULT_NAMES[1].lower()
    if any(c.name.lower() == inner for c in cands):
        return [c for c in cands if c.name.lower() != launcher]
    return cands


# --------------------------------------------------------------------------- #
# session
# --------------------------------------------------------------------------- #
class Session:
    """An open handle to the game plus its identity (stamp / size_of_image).

    ``reader(addr, n) -> bytes`` and ``writer(addr, data)`` (``None`` when
    opened read-only) are the primitives the ``addr``/``observe``/``modifier``
    layers build on.
    """

    def __init__(self, pid: int, name: str, path: Optional[str], handle: int,
                 stamp: int, size_of_image: int, image_base: int,
                 reader: Reader, writer: Optional[Writer] = None,
                 _close: Optional[Callable[[int], None]] = None,
                 lock: Optional[ControllerLock] = None) -> None:
        self.pid = pid
        #: single-controller mutex (``attach(lock=True)``); released on close
        self.lock = lock
        self.name = name
        self.path = path
        self.handle = handle
        self.stamp = stamp
        self.size_of_image = size_of_image
        self.image_base = image_base
        self.reader = reader
        self.writer = writer
        self._close = _close
        self._closed = False

    # -- identity ---------------------------------------------------------
    @property
    def writable(self) -> bool:
        return self.writer is not None

    @property
    def closed(self) -> bool:
        return self._closed

    def __repr__(self) -> str:
        return ("Session(pid=%d, name=%r, stamp=0x%08x, size_of_image=0x%x, base=0x%x, %s)"
                % (self.pid, self.name, self.stamp, self.size_of_image, self.image_base,
                   "rw" if self.writable else "ro"))

    # -- memory ------------------------------------------------------------
    def read(self, addr: int, n: int) -> bytes:
        self._check_open()
        return self.reader(addr, n)

    def read_u32(self, addr: int) -> int:
        return struct.unpack("<I", self.read(addr, 4))[0]

    def read_i32(self, addr: int) -> int:
        return struct.unpack("<i", self.read(addr, 4))[0]

    def read_u16(self, addr: int) -> int:
        return struct.unpack("<H", self.read(addr, 2))[0]

    def read_u8(self, addr: int) -> int:
        return self.read(addr, 1)[0]

    def read_f32(self, addr: int) -> float:
        return struct.unpack("<f", self.read(addr, 4))[0]

    def read_ptr(self, addr: int) -> int:
        """Target pointers are 4 bytes (DESIGN §3.6)."""
        return self.read_u32(addr)

    def write(self, addr: int, data: bytes) -> None:
        self._check_open()
        if self.writer is None:
            raise ProcessError("会话为只读；attach(write=True) 才能写内存")
        self.writer(addr, bytes(data))

    def write_u32(self, addr: int, value: int) -> None:
        self.write(addr, struct.pack("<I", value & 0xFFFFFFFF))

    def write_i32(self, addr: int, value: int) -> None:
        self.write(addr, struct.pack("<i", value))

    # -- lifecycle ---------------------------------------------------------
    def _check_open(self) -> None:
        if self._closed:
            raise ProcessError("会话已关闭")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._close is not None and self.handle:
            try:
                self._close(self.handle)
            except Exception:
                pass
        if self.lock is not None:
            try:
                self.lock.release()
            except Exception:
                pass

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# attach
# --------------------------------------------------------------------------- #
def attach(pid: Optional[int] = None, *, write: bool = False,
           names: Optional[Iterable[str]] = None,
           extra_names: Optional[Iterable[str]] = None,
           image_base: int = DEFAULT_IMAGE_BASE,
           lock: Optional[bool] = None) -> Session:
    """Open the game process and return a :class:`Session`.

    - ``pid=None``: discover via :func:`find_game_processes`; raises
      :class:`GameNotRunning` / :class:`MultipleGames`.
    - ``write=True``: also request VM_WRITE|VM_OPERATION and install a writer.
    - ``names`` / ``extra_names``: see :func:`find_game_processes`.
    - ``lock`` (default: same as ``write``): take the single-controller
      mutex ``Local\\pvztools-controller-<pid>``; raises
      :class:`ControllerBusy` when another pvztools instance already
      controls the game.  Read-only sessions never take it.  Released by
      :meth:`Session.close` and automatically when this process exits.
    """
    k = _kernel32()
    if pid is None:
        cands = find_game_processes(names, extra_names)
        if not cands:
            raise GameNotRunning()
        if len(cands) > 1:
            raise MultipleGames(cands)
        info = cands[0]
    else:
        info = None
        for e in k.list_processes():
            fe = _entry_fields(e)
            if fe.pid == pid:
                info = fe
                break
        if info is None:
            raise GameNotRunning("没有 pid 为 %d 的进程" % pid)

    ctl_lock: Optional[ControllerLock] = None
    if (write if lock is None else lock):
        ctl_lock = _acquire_lock(info.pid)

    try:
        handle = k.open_process(info.pid, write=write, create_thread=False)
    except Exception as exc:  # OSError / WinApiError
        if ctl_lock is not None:
            ctl_lock.release()
        raise AccessDenied("无法打开游戏进程；游戏若以管理员运行，Python 也需要管理员 (pid %d: %s)"
                           % (info.pid, exc)) from exc
    if not handle:
        if ctl_lock is not None:
            ctl_lock.release()
        raise AccessDenied()

    closer = getattr(k, "close_handle", None)
    try:
        if not k.is_wow64(handle):
            raise NotA32BitProcess(info.pid)

        path = info.path
        if not path:
            fn = getattr(k, "process_image_path", None) or getattr(k, "image_path", None)
            if fn is not None:
                try:
                    path = fn(handle)
                except Exception:
                    path = None

        def reader(addr: int, n: int, _h=handle) -> bytes:
            return k.read(_h, addr, n)

        writer: Optional[Writer] = None
        if write:
            def writer(addr: int, data: bytes, _h=handle) -> None:  # type: ignore[misc]
                k.write(_h, addr, data)

        try:
            hdr = reader(image_base, 0x400)
        except Exception as exc:
            raise ProcessError("无法读取 0x%X 处的 PE 头：%s" % (image_base, exc)) from exc
        pe = _parse_pe_header(hdr)
        if pe["magic"] != 0x10B:
            raise NotA32BitProcess(info.pid)
    except Exception:
        if closer is not None:
            try:
                closer(handle)
            except Exception:
                pass
        if ctl_lock is not None:
            ctl_lock.release()
        raise

    return Session(info.pid, info.name, path, handle, pe["stamp"], pe["size_of_image"],
                   pe["image_base"] or image_base, reader, writer, _close=closer,
                   lock=ctl_lock)
