"""悬浮窗模式：隐藏 OS 窗口外框，只留半透明圆角面板。

实现：Windows 分层窗口（WS_EX_LAYERED）。
- LWA_COLORKEY 把"幕布色"抠成完全透明（且鼠标穿透）；
  清屏色被设成幕布色后，圆角外与边距区域就消失了。
- LWA_ALPHA 同时提供整体半透明。
仅 Windows；其他平台 apply() 返回 False 静默降级。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys

__all__ = ["KEY_RGB", "KEY_CLEAR", "apply", "set_decorated", "move_window_by"]

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002

# 幕布色：刻意取不会在界面里出现的洋红
KEY_RGB = (255, 0, 254)
KEY_CLEAR = (KEY_RGB[0] / 255.0, KEY_RGB[1] / 255.0, KEY_RGB[2] / 255.0, 1.0)

_WINDOW_TITLE = "pvztools"


def _glfw_window():
    try:
        from imgui_bundle.glfw_utils import glfw_window_hello_imgui

        return glfw_window_hello_imgui()
    except Exception:  # noqa: BLE001
        return None


def _hwnd() -> int:
    win = _glfw_window()
    if win is not None:
        try:
            import glfw

            if hasattr(glfw, "get_win32_window"):
                return int(glfw.get_win32_window(win))
        except Exception:  # noqa: BLE001
            pass
    try:
        return int(ctypes.windll.user32.FindWindowW(None, _WINDOW_TITLE))
    except Exception:  # noqa: BLE001
        return 0


def kill_other_instances(title: str = _WINDOW_TITLE, grace: float = 2.0) -> int:
    """关掉其它 pvztools GUI 实例（单实例）。先 WM_CLOSE 优雅退出，
    没退再强杀。返回处理掉的实例数。仅 Windows。"""
    if sys.platform != "win32":
        return 0
    import os
    import time as _time

    u = ctypes.windll.user32
    me = os.getpid()
    WM_CLOSE = 0x0010
    victims: set = set()

    @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(64)
        u.GetWindowTextW(hwnd, buf, 64)
        if buf.value == title:
            pid = ctypes.wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and pid.value != me:
                victims.add((int(hwnd), pid.value))
        return True

    try:
        u.EnumWindows(_cb, 0)
    except Exception:  # noqa: BLE001
        return 0
    if not victims:
        return 0
    for hwnd, _pid in victims:
        u.PostMessageW(hwnd, WM_CLOSE, 0, 0)   # 优雅退出：触发 close()，释放互斥/回滚补丁
    # 等它们退出
    pids = {p for _h, p in victims}
    k32 = ctypes.windll.kernel32
    deadline = _time.monotonic() + max(0.0, grace)
    while _time.monotonic() < deadline and pids:
        _time.sleep(0.1)
        alive = set()
        for pid in pids:
            h = k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if h:
                code = ctypes.wintypes.DWORD()
                if k32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value == 259:
                    alive.add(pid)  # STILL_ACTIVE
                k32.CloseHandle(h)
        pids = alive
    # 还活着的强杀
    for pid in pids:
        h = k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if h:
            k32.TerminateProcess(h, 0)
            k32.CloseHandle(h)
    return len(victims)


def request_transparent_framebuffer() -> None:
    """在 hello_imgui 建窗前请求逐像素透明帧缓冲（须在 run 之前调用）。"""
    try:
        import glfw

        if glfw.init():
            glfw.window_hint(glfw.TRANSPARENT_FRAMEBUFFER, glfw.TRUE)
    except Exception:  # noqa: BLE001
        pass


def has_transparent_framebuffer() -> bool:
    win = _glfw_window()
    if win is None:
        return False
    try:
        import glfw

        return bool(glfw.get_window_attrib(win, glfw.TRANSPARENT_FRAMEBUFFER))
    except Exception:  # noqa: BLE001
        return False


def apply(enabled: bool, opacity: float, *, colorkey: bool = True) -> bool:
    """开/关悬浮透明；opacity 为整体不透明度 0..1。

    有逐像素透明帧缓冲时用 colorkey=False（清屏 alpha=0 即透明，
    边缘无锯齿）；否则退回抠色（LWA_COLORKEY）。
    """
    if sys.platform != "win32":
        return False
    hwnd = _hwnd()
    if not hwnd:
        return False
    u = ctypes.windll.user32
    style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    alpha = max(40, min(255, int(round(float(opacity) * 255))))
    if enabled:
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        if colorkey:
            key = KEY_RGB[0] | (KEY_RGB[1] << 8) | (KEY_RGB[2] << 16)
            u.SetLayeredWindowAttributes(hwnd, key, alpha, LWA_COLORKEY | LWA_ALPHA)
        else:
            u.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
    else:
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_LAYERED)
    return True


def set_decorated(decorated: bool) -> bool:
    """显示/隐藏系统标题栏和边框（运行时切换）。"""
    win = _glfw_window()
    if win is None:
        return False
    try:
        import glfw

        glfw.set_window_attrib(win, glfw.DECORATED, 1 if decorated else 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def set_opacity(opacity: float) -> bool:
    """普通模式下的整窗不透明度（glfw 实现）。"""
    win = _glfw_window()
    if win is None:
        return False
    try:
        import glfw

        glfw.set_window_opacity(win, float(opacity))
        return True
    except Exception:  # noqa: BLE001
        return False


def move_window_by(dx: float, dy: float) -> bool:
    """按增量移动 OS 窗口（悬浮模式下拖动标题栏用）。"""
    win = _glfw_window()
    if win is None:
        return False
    try:
        import glfw

        x, y = glfw.get_window_pos(win)
        glfw.set_window_pos(win, int(x + dx), int(y + dy))
        return True
    except Exception:  # noqa: BLE001
        return False


#: 收起/展开快捷键（VK code）。INSERT 从不与游戏冲突。
TOGGLE_VK = 0x2D  # VK_INSERT


def key_pressed(vk: int) -> bool:
    """全局按键当前是否按下（GetAsyncKeyState 高位）。仅 Windows。"""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(int(vk)) & 0x8000)
    except Exception:  # noqa: BLE001
        return False


def get_cursor_pos():
    """屏幕光标坐标 (x, y)，失败返回 None。仅 Windows。"""
    if sys.platform != "win32":
        return None
    try:
        pt = ctypes.wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return (pt.x, pt.y)
    except Exception:  # noqa: BLE001
        pass
    return None


def get_window_pos():
    win = _glfw_window()
    if win is None:
        return None
    try:
        import glfw

        return tuple(glfw.get_window_pos(win))
    except Exception:  # noqa: BLE001
        return None


def set_window_pos(x: int, y: int) -> bool:
    win = _glfw_window()
    if win is None:
        return False
    try:
        import glfw

        glfw.set_window_pos(win, int(x), int(y))
        return True
    except Exception:  # noqa: BLE001
        return False


def get_window_size():
    """返回 (w, h) 或 None。"""
    win = _glfw_window()
    if win is None:
        return None
    try:
        import glfw

        return tuple(glfw.get_window_size(win))
    except Exception:  # noqa: BLE001
        return None


def set_window_size(w: int, h: int) -> bool:
    win = _glfw_window()
    if win is None:
        return False
    try:
        import glfw

        glfw.set_window_size(win, int(w), int(h))
        return True
    except Exception:  # noqa: BLE001
        return False


def resize_window_by(dw: float, dh: float, min_w: int = 340, min_h: int = 260) -> bool:
    """按增量缩放 OS 窗口（右下角手柄用）。"""
    win = _glfw_window()
    if win is None:
        return False
    try:
        import glfw

        w, h = glfw.get_window_size(win)
        glfw.set_window_size(win, max(min_w, int(w + dw)), max(min_h, int(h + dh)))
        return True
    except Exception:  # noqa: BLE001
        return False


def set_topmost(topmost: bool) -> bool:
    """总在最前（游戏全屏时 GUI 仍可见；Win11 下老游戏走 DWM 合成）。

    glfw 与 SetWindowPos 双保险：两条路径都执行。
    """
    ok = False
    win = _glfw_window()
    if win is not None:
        try:
            import glfw

            glfw.set_window_attrib(win, glfw.FLOATING, 1 if topmost else 0)
            ok = True
        except Exception:  # noqa: BLE001
            pass
    if sys.platform != "win32":
        return ok
    hwnd = _hwnd()
    if not hwnd:
        return ok
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x2, 0x1, 0x10
    ctypes.windll.user32.SetWindowPos(
        hwnd,
        HWND_TOPMOST if topmost else HWND_NOTOPMOST,
        0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    )
    return True


WS_EX_TRANSPARENT = 0x00000020


def set_click_through(enabled: bool) -> bool:
    """开/关鼠标穿透（WS_EX_TRANSPARENT）：不在面板上时点击落到游戏。

    这是交互式悬浮窗的关键——宿主全屏透明，按 want_capture_mouse
    动态切换：鼠标在面板上→可点，不在→穿透到下面的游戏。仅 Windows。
    """
    if sys.platform != "win32":
        return False
    hwnd = _hwnd()
    if not hwnd:
        return False
    u = ctypes.windll.user32
    ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    new = (ex | WS_EX_TRANSPARENT) if enabled else (ex & ~WS_EX_TRANSPARENT)
    if new != ex:
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, new)
    return True


def cover_screen() -> bool:
    """把宿主窗口铺满整个（虚拟）屏幕，作为透明覆盖层。"""
    win = _glfw_window()
    if win is None:
        return False
    try:
        import glfw

        if sys.platform == "win32":
            u = ctypes.windll.user32
            SM_XVIRTUAL, SM_YVIRTUAL, SM_CXVIRTUAL, SM_CYVIRTUAL = 76, 77, 78, 79
            x = u.GetSystemMetrics(SM_XVIRTUAL)
            y = u.GetSystemMetrics(SM_YVIRTUAL)
            w = u.GetSystemMetrics(SM_CXVIRTUAL) or u.GetSystemMetrics(0)
            h = u.GetSystemMetrics(SM_CYVIRTUAL) or u.GetSystemMetrics(1)
        else:
            mon = glfw.get_primary_monitor()
            mode = glfw.get_video_mode(mon)
            x, y, w, h = 0, 0, mode.size.width, mode.size.height
        glfw.set_window_pos(win, int(x), int(y))
        glfw.set_window_size(win, int(w), int(h))
        return True
    except Exception:  # noqa: BLE001
        return False


def set_no_activate(enabled: bool) -> bool:
    """点击悬浮窗不抢占焦点：游戏保持前台，不会因失焦自动暂停。

    学自独立 overlay 常用做法（WS_EX_NOACTIVATE）。仅 Windows。
    """
    if sys.platform != "win32":
        return False
    hwnd = _hwnd()
    if not hwnd:
        return False
    u = ctypes.windll.user32
    ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    new = (ex | WS_EX_NOACTIVATE) if enabled else (ex & ~WS_EX_NOACTIVATE)
    if new != ex:
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, new)
    return True
