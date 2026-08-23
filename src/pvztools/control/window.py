"""Game window: discovery, DPI-correct geometry, calibration, focus, dialogs.

:class:`GameWindow` is the single place that knows how a *logical* game
coordinate (the fixed 800x600 surface PvZ draws) maps onto the coordinate
that must be packed into a posted mouse message's ``lParam``.  Both
:class:`~pvztools.control.input_backend.InputBackend` and
:class:`~pvztools.control.chooser.SeedChooser` route every click through
:meth:`GameWindow.logical_to_physical`.

Why a mapping is needed at all
------------------------------
* **DPI virtualisation.**  Python is usually DPI-unaware while the game is
  (Steam GOTY, 125 % display scaling): ``GetClientRect`` reports 640x480
  for the real 800x600 client and ``PostMessage`` coordinates are
  virtualised the same way, so posting ``0.8 * logical`` is what works
  on that machine.  :meth:`client_size` rescales by
  ``GetDpiForWindow / GetDpiForSystem`` (see
  :func:`pvztools._win.user32.client_size`) so it returns the game's own
  800x600, and the *ratio* ``GetClientRect / client_size`` is the factor
  applied to logical coordinates.
* **Window resizing.**  A resized window stretches the 800x600 surface;
  the same ratio handles that.
* **Calibration.**  When a Board exists (seed chooser or in a level) the
  mapping can be *measured* instead of assumed: ``WM_MOUSEMOVE`` is posted
  at three physical points and ``Board.mPrevMouseX/Y`` (profile
  ``board.prev_mouse_x/y``; GOTY 0x5570/0x5574, 1051 0x5558/0x555C) is
  read back -- the game stores the logical coordinate it derived from the
  message.  A least-squares line ``logical = a * physical + b`` per axis
  gives scale **and** offset (a letterboxed / offset presentation rect
  would show up as ``b != 0``).  :meth:`calibrate` falls back to the
  client-size ratio when no Board is present, the field is unknown to the
  profile, or the readings are degenerate.

Focus and pausing
-----------------
The game pauses when it loses focus (``LawnApp::LostFocus`` ->
``DoPauseDialog``) unless the ``background_running`` patch is on.
:meth:`ensure_running` reports ``"foreground"`` / ``"patched"`` when input
will be processed; with ``foreground=True`` it first tries to bring the
window to the front (``AttachThreadInput`` trick,
:func:`pvztools._win.user32.focus_window`, followed by a settle sleep) --
callers (:class:`~pvztools.control.input_backend.InputBackend`,
:class:`~pvztools.control.chooser.SeedChooser`) do that **once** per
session, not before every action, since a successful steal costs ~0.3 s.
Otherwise it enables the patch when a :class:`PatchManager` is available
and the :class:`Safety` allows patching; else it only warns (once per
caller).

Dialogs
-------
``SexyAppBase::GetDialogCount()`` is ``mDialogMap.size()``.  The
SexyAppBase layout modelled from re-plants puts ``mDialogMap`` at +0x324
(``mWidgetManager`` +0x320, ``mDialogList`` +0x32C) but the same model
places ``mFrameTime`` at +0x444 while the verified ``frame_duration`` is
0x454 (1051) / 0x4B4 (GOTY): the model is **0x10 short before 0x454** and
the GOTY delta is unknown, so the offset is *not* derived here.  When a
profile gains a verified ``lawn_app.dialog_map`` key
:meth:`dialog_count` reads ``std::map::_Mysize`` at ``+4``.

:meth:`dialog_open` combines three signals, in order: the **Crazy Dave
intro talk** (``LawnApp::mCrazyDaveMessageIndex != -1`` while ``game_ui ==
2``, GOTY ``app+0x970``; see :mod:`pvztools.control.cutscene` -- the talk
is a "dialog" in every practical sense: the cutscene clock is stopped and
the chooser is not focused), :meth:`dialog_count` when available, and
otherwise the heuristic: after a click that should have changed
``game_ui``, an unchanged ``ui`` for ``wait`` seconds means a dialog (or a
disabled button) is in the way.

:meth:`close_dialog` posts **Escape only** (``LawnDialog::KeyDown`` treats
it as *No* / cancel).  The earlier "click a few candidate button
positions" fallback is gone: the positions were guesses (``AddDialog``
centres dialogs at ``y = 120`` but the LawnDialog height depends on image
assets), and during the 2026-08-22 incident blind clicks in the chooser
scene preceded a crash.  Nothing in :mod:`pvztools.control.play` calls
:meth:`close_dialog` automatically; a suspected dialog stops the run with
a report.
"""
from __future__ import annotations

import struct
import time
import warnings
from typing import Any, Callable, List, NamedTuple, Optional, Sequence, Tuple

from .._win import user32 as _user32

__all__ = [
    "GameWindow", "Calibration", "fit_axis", "CLIENT_W", "CLIENT_H",
    "UI_CHOOSER", "UI_LEVEL",
]

#: Logical client size of the game window (``BOARD_WIDTH`` / ``HEIGHT``).
CLIENT_W = 800
CLIENT_H = 600
UI_MENU = 1
UI_CHOOSER = 2
UI_LEVEL = 3
#: Physical fractions of the client used by :meth:`GameWindow.calibrate`.
CALIBRATION_FRACTIONS: Tuple[Tuple[float, float], ...] = ((0.2, 0.2), (0.5, 0.6), (0.8, 0.9))
#: :meth:`GameWindow.calibrate`: every sample must lie within this many
#: logical px of the fitted line, else the fit is rejected.
CALIBRATION_MAX_RESIDUAL = 3.0
#: :meth:`GameWindow.calibrate`: the measured scale must be within
#: ``[ratio / band, ratio * band]`` of the client-size ratio.
CALIBRATION_SCALE_BAND = 2.0
#: :meth:`GameWindow.calibrate`: extra ``settle`` waits while ``prev_mouse``
#: has not changed after a posted move (message pump lagging).
CALIBRATION_READ_TRIES = 4
#: Cursor-position field on the Board.
PREV_MOUSE_FIELDS = ("prev_mouse_x", "prev_mouse_y")


class Calibration(NamedTuple):
    """``physical = (logical - offset) / scale`` per axis; ``source`` is
    ``"measured"`` (prev-mouse fit), ``"ratio"`` (client size) or
    ``"identity"``."""
    scale_x: float
    scale_y: float
    offset_x: float = 0.0
    offset_y: float = 0.0
    source: str = "identity"

    def to_physical(self, x: float, y: float) -> Tuple[int, int]:
        return (int(round((x - self.offset_x) / self.scale_x)),
                int(round((y - self.offset_y) / self.scale_y)))

    def to_logical(self, px: float, py: float) -> Tuple[float, float]:
        return px * self.scale_x + self.offset_x, py * self.scale_y + self.offset_y


IDENTITY = Calibration(1.0, 1.0)


def fit_axis(physical: Sequence[float], logical: Sequence[float],
             max_residual: Optional[float] = None) -> Optional[Tuple[float, float]]:
    """Least-squares ``logical = a * physical + b``; ``None`` when the
    physical points are degenerate (fewer than two distinct values), the
    fitted scale is not positive or -- with ``max_residual`` -- any sample
    is further than that from the line (a stale / partially processed
    reading)."""
    n = len(physical)
    if n < 2 or n != len(logical):
        return None
    mx = sum(physical) / n
    my = sum(logical) / n
    sxx = sum((p - mx) ** 2 for p in physical)
    if sxx <= 1e-9:
        return None
    sxy = sum((p - mx) * (l - my) for p, l in zip(physical, logical))
    a = sxy / sxx
    if a <= 0:
        return None
    b = my - a * mx
    if max_residual is not None:
        if any(abs(a * p + b - l) > max_residual for p, l in zip(physical, logical)):
            return None
    return a, b


def _within_band(measured: float, reference: float, band: float) -> bool:
    """``reference / band <= measured <= reference * band`` (True when the
    reference is unknown / non-positive)."""
    if reference <= 0 or band <= 1.0:
        return True
    return reference / band <= measured <= reference * band


class GameWindow:
    """The game's main window (class ``MainWindow`` of ``session.pid``).

    ``session`` supplies ``pid`` and ``read``; ``observer`` supplies
    ``game().ui`` and ``board_ptr()``; ``profile`` supplies
    ``board.prev_mouse_x/y`` (optional); ``patches`` is a
    :class:`~pvztools.modifier.patches.PatchManager` (optional) used by
    :meth:`ensure_running`; ``safety`` gates posting (``allow_input``) and
    ``dry_run``.

    Test hooks: ``user32`` (fake module), ``hwnd`` (skip discovery),
    ``sleep`` / ``clock``.
    """

    def __init__(self, session: Any, observer: Any, profile: Any = None,
                 patches: Any = None, safety: Any = None, *,
                 hwnd: Optional[int] = None, user32: Any = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self.session = session
        self.observer = observer
        self.profile = profile
        self.patches = patches
        self.safety = safety
        self.user32 = user32 if user32 is not None else _user32
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._hwnd = int(hwnd) if hwnd else 0
        self.calibration: Calibration = IDENTITY
        self._calibrated = False
        #: ``(msg, wparam, x, y)`` of everything posted (or recorded in dry run).
        self.history: List[Tuple[int, int, int, int]] = []

    # -- discovery -------------------------------------------------------------
    @property
    def dry_run(self) -> bool:
        return bool(getattr(self.safety, "dry_run", False))

    @property
    def hwnd(self) -> int:
        if not self._hwnd:
            pid = getattr(self.session, "pid", None)
            self._hwnd = int(self.user32.find_game_window(pid) or 0)
            if not self._hwnd:
                from .backends import BackendError
                raise BackendError("找不到游戏窗口（pid=%s，class=MainWindow）" % (pid,))
        return self._hwnd

    def ui(self) -> int:
        return int(self.observer.game().ui)

    # -- geometry --------------------------------------------------------------
    def raw_client_size(self) -> Tuple[int, int]:
        """``GetClientRect`` in *our* DPI space (what ``lParam`` must use)."""
        fn = getattr(self.user32, "get_client_rect", None)
        if fn is None:
            return 0, 0
        w, h = fn(self.hwnd)
        return int(w), int(h)

    def client_size(self) -> Tuple[int, int]:
        """Client size in the window's own DPI space (800x600 for an
        unresized game); ``(0, 0)`` when unavailable."""
        fn = getattr(self.user32, "client_size", None)
        if fn is not None:
            w, h = fn(self.hwnd)
            return int(w), int(h)
        w, h = self.raw_client_size()
        if w <= 0 or h <= 0:
            return 0, 0
        try:
            win_dpi = int(self.user32.get_dpi_for_window(self.hwnd))
            my_dpi = int(self.user32.get_dpi_for_system())
        except Exception:
            return w, h
        if win_dpi > 0 and my_dpi > 0 and win_dpi != my_dpi:
            w = int(round(w * win_dpi / float(my_dpi)))
            h = int(round(h * win_dpi / float(my_dpi)))
        return w, h

    def ratio_calibration(self) -> Calibration:
        """Mapping from the client-rect ratio alone: logical 800x600 ->
        the raw client size ``PostMessage`` expects (identity when the
        sizes are unknown)."""
        rw, rh = self.raw_client_size()
        if rw <= 0 or rh <= 0:
            return IDENTITY
        return Calibration(CLIENT_W / float(rw), CLIENT_H / float(rh), 0.0, 0.0, "ratio")

    def logical_to_physical(self, x: float, y: float) -> Tuple[int, int]:
        """Logical 800x600 point -> ``lParam`` coordinates.  Uses the last
        :meth:`calibrate` result; before any calibration the client ratio
        is computed on the fly (so a resized window still works)."""
        cal = self.calibration if self._calibrated else self.ratio_calibration()
        return cal.to_physical(x, y)

    def physical_to_logical(self, px: float, py: float) -> Tuple[float, float]:
        cal = self.calibration if self._calibrated else self.ratio_calibration()
        return cal.to_logical(px, py)

    # -- calibration -----------------------------------------------------------
    def _prev_mouse_addr(self) -> Optional[int]:
        board_sec = getattr(self.profile, "board", None)
        if board_sec is None:
            return None
        getter = getattr(board_sec, "get", None)
        off_x = getter("prev_mouse_x") if getter else None
        off_y = getter("prev_mouse_y") if getter else None
        if off_x is None or off_y is None or off_y != off_x + 4:
            return None
        board = int(self.observer.board_ptr() or 0)
        if not board:
            return None
        return board + int(off_x)

    def read_prev_mouse(self) -> Optional[Tuple[int, int]]:
        """``Board.mPrevMouseX/Y`` or ``None`` (no Board / field unknown)."""
        addr = self._prev_mouse_addr()
        if addr is None:
            return None
        return struct.unpack("<ii", self.session.read(addr, 8))

    def _post(self, msg: int, wparam: int, px: int, py: int) -> None:
        self.history.append((int(msg), int(wparam), int(px), int(py)))
        if self.dry_run:
            return
        lparam = (int(py) & 0xFFFF) << 16 | (int(px) & 0xFFFF)
        self.user32.post_message(self.hwnd, msg, wparam, lparam)

    def calibrate(self, settle: float = 0.08) -> Calibration:
        """Measure the logical<->physical mapping (module docstring).
        Requires ``Safety(allow_input=True)`` to post; in ``dry_run`` or
        without a Board the client-size ratio is used.  The result is
        stored in :attr:`calibration` and used by all later clicks.

        The measured fit is accepted only when every sample lies within
        :data:`CALIBRATION_MAX_RESIDUAL` logical px of the line and the
        scale is within :data:`CALIBRATION_SCALE_BAND` of the client-size
        ratio; otherwise the ratio is used (with a warning) -- a stale
        ``prev_mouse`` reading (paused game / lagging message pump) must not
        be frozen into every later click.  Each reading is retried up to
        :data:`CALIBRATION_READ_TRIES` settle periods while ``prev_mouse``
        has not changed since the previous sample."""
        cal = self.ratio_calibration()
        addr = None if self.dry_run else self._prev_mouse_addr()
        if addr is not None and self.safety is not None:
            try:
                self.safety.require_input("校准坐标")
            except Exception:
                addr = None
        if addr is not None:
            rw, rh = self.raw_client_size()
            if rw > 0 and rh > 0:
                phys_x: List[float] = []
                phys_y: List[float] = []
                log_x: List[float] = []
                log_y: List[float] = []
                prev = struct.unpack("<ii", self.session.read(addr, 8))
                for fx, fy in CALIBRATION_FRACTIONS:
                    px, py = int(round(rw * fx)), int(round(rh * fy))
                    self._post(self.user32.WM_MOUSEMOVE, 0, px, py)
                    cur = prev
                    for _ in range(CALIBRATION_READ_TRIES):
                        self._sleep(settle)
                        cur = struct.unpack("<ii", self.session.read(addr, 8))
                        if cur != prev:
                            break
                    prev = cur
                    phys_x.append(px)
                    phys_y.append(py)
                    log_x.append(cur[0])
                    log_y.append(cur[1])
                fx_ = fit_axis(phys_x, log_x, CALIBRATION_MAX_RESIDUAL)
                fy_ = fit_axis(phys_y, log_y, CALIBRATION_MAX_RESIDUAL)
                if fx_ is None or fy_ is None:
                    warnings.warn("坐标校准读数退化或不自洽（prev_mouse 未随 WM_MOUSEMOVE 变化 / 残差 > %.0f px），"
                                  "改用客户区比例" % CALIBRATION_MAX_RESIDUAL)
                elif not (_within_band(fx_[0], cal.scale_x, CALIBRATION_SCALE_BAND)
                          and _within_band(fy_[0], cal.scale_y, CALIBRATION_SCALE_BAND)):
                    warnings.warn("坐标校准测得比例 (%.3f, %.3f) 与客户区比例 (%.3f, %.3f) 相差过大，改用客户区比例"
                                  % (fx_[0], fy_[0], cal.scale_x, cal.scale_y))
                else:
                    cal = Calibration(fx_[0], fy_[0], fx_[1], fy_[1], "measured")
        self.calibration = cal
        self._calibrated = True
        return cal

    # -- focus / running -------------------------------------------------------
    def is_foreground(self) -> bool:
        fn = getattr(self.user32, "is_foreground", None)
        if fn is None:
            return False
        try:
            return bool(fn(self.hwnd))
        except Exception:
            return False

    def bring_to_foreground(self, settle: float = 0.3) -> bool:
        """Safe foreground policy (fullscreen-safe): restore-if-minimised then
        raise + focus.  Delegates to :func:`pvztools._win.user32.focus_window`,
        which attaches to the current foreground thread's input queue
        (``AttachThreadInput``) so Windows accepts the steal, un-minimises the
        window with ``SW_RESTORE`` (``SW_SHOW`` otherwise -- never a resize /
        maximise that would disturb a fullscreen presentation), and calls
        ``SetForegroundWindow`` + ``BringWindowToTop``.  This is the same
        recipe the strict-CD level runner (:func:`play_one_level`) uses; the
        policy is *bring to front for viewing only* and enables **no**
        ``background_running`` patch (see :meth:`ensure_running`), so it does
        not touch a game that is legitimately fullscreen.  ``True`` when the
        game is the foreground window afterwards."""
        if self.is_foreground():
            return True
        fn = getattr(self.user32, "focus_window", None)
        if fn is None:
            return False
        try:
            ok = bool(fn(self.hwnd))
        except Exception:
            ok = False
        if ok:
            self._sleep(settle)
        return ok and self.is_foreground()

    # -- display mode (fullscreen / windowed) ---------------------------------
    #: LawnApp/SexyAppBase field offsets governing the fullscreen<->windowed
    #: switch.  Own reverse-engineering (not toolkit data), verified on the
    #: Steam GOTY build; keyed by profile id.  ``mIsWindowed`` (byte, 1=win),
    #: ``mForceFullscreen``/``mForceWindowed`` guards, ``mAllowAltEnter`` dword.
    _DISPLAY_OFFSETS = {
        "goty_1_2_0_1096_en": {
            "is_windowed": 0x367, "force_fullscreen": 0x36A,
            "force_windowed": 0x36B, "allow_alt_enter": 0x64C,
        },
    }

    def _lawn_field(self, name: str) -> Optional[int]:
        """Absolute VA of a LawnApp field, or ``None`` if unmapped.

        Prefers a profile-provided offset (future builds may carry it); falls
        back to the built-in per-version table above.
        """
        prof = self.profile
        if prof is None:
            return None
        off = getattr(getattr(prof, "lawn_app", None), name, None)
        if off is None:
            ver = str(getattr(prof, "id", "") or getattr(prof, "version_id", ""))
            off = self._DISPLAY_OFFSETS.get(ver, {}).get(name)
        if off is None:
            return None
        try:
            lawn = self.observer.lawn_ptr()
        except Exception:
            return None
        if not lawn:
            return None
        return lawn + int(off)

    def is_windowed(self) -> Optional[bool]:
        """``True`` windowed, ``False`` fullscreen, ``None`` if not readable.

        Reads ``LawnApp::mIsWindowed`` (byte, 1 = windowed).
        """
        addr = self._lawn_field("is_windowed")
        if addr is None:
            return None
        try:
            return bool(self.session.read(addr, 1)[0])
        except Exception:
            return None

    def can_switch_display(self) -> bool:
        """Whether an Alt+Enter switch is currently permitted by the game.

        Guarded by ``mForceFullscreen`` / ``mForceWindowed`` (both must be 0,
        set only on a DirectDraw fallback failure) and ``mAllowAltEnter`` != 0.
        """
        try:
            for name, want_zero in (("force_fullscreen", True), ("force_windowed", True)):
                a = self._lawn_field(name)
                if a is not None and bool(self.session.read(a, 1)[0]) is want_zero:
                    return False
            a = self._lawn_field("allow_alt_enter")
            if a is not None and self.session.read_u32(a) == 0:
                return False
        except Exception:
            return False
        return True

    def set_windowed(self, want: bool, *, timeout: float = 2.5,
                     poll: float = 0.05) -> Optional[bool]:
        """Switch the game to windowed / fullscreen the *safe* way.

        Never remote-calls ``SwitchScreenMode`` (it recreates the window +
        DDraw surfaces from a foreign thread → crash).  Instead posts the
        game's own Alt+Enter (``WM_SYSKEYDOWN`` ``VK_RETURN``); the game's main
        loop performs the switch at its natural point via
        ``ProcessDeferredMessages``.  Returns the resulting windowed state, or
        ``None`` when the mode is not readable / not switchable on this build.
        Requires the game's main loop to be running (do not hold
        ``main_loop_block`` while waiting).
        """
        current = self.is_windowed()
        if current is None:
            return None
        # 让游戏可见且在跑：最小化 / 失焦时主循环会暂停，延迟的 Alt+Enter
        # 就不会被处理，切换也就“没反应”。先恢复+置前再切。
        if bool(want):
            try:
                if not self.is_foreground():
                    self.bring_to_foreground(settle=0.2)
            except Exception:  # noqa: BLE001
                pass
        if current == bool(want):
            return current
        if not self.can_switch_display():
            return current
        if self.dry_run:
            self.history.append((int(self.user32.WM_SYSKEYDOWN),
                                 int(self.user32.VK_RETURN), 0, 0))
            return current
        hwnd = self.hwnd
        u = self.user32
        # Alt+Enter: SYSKEYDOWN carries the ALT context bit (lparam bit 29).
        u.post_message(hwnd, u.WM_SYSKEYDOWN, u.VK_RETURN, 0x20000001)
        u.post_message(hwnd, u.WM_SYSKEYUP, u.VK_RETURN, 0xE0000001)
        deadline = self._clock() + float(timeout)
        result = current
        while self._clock() < deadline:
            self._sleep(poll)
            now = self.is_windowed()
            if now is not None and now == bool(want):
                result = now
                break
        else:
            result = self.is_windowed()
        # 切到窗口后再恢复+置前一次（切换可能重建窗口）
        if bool(want):
            try:
                self.bring_to_foreground(settle=0.15)
            except Exception:  # noqa: BLE001
                pass
        return result

    def background_running(self) -> Optional[bool]:
        """State of the ``background_running`` patch (``None`` = unknown)."""
        pm = self.patches
        if pm is None:
            return None
        try:
            st = pm.status().get("background_running")
        except Exception:
            return None
        if st in ("on", "dry"):
            return True
        if st == "off":
            return False
        return None

    def ensure_running(self, foreground: bool = False) -> str:
        """Make sure the game will process posted input: returns
        ``"foreground"`` when the window has focus (or, with
        ``foreground=True``, was just brought to the front via
        :meth:`bring_to_foreground`), ``"patched"`` when
        ``background_running`` is (now) on, ``"unfocused"`` after warning
        that the game is probably paused, ``"unknown"`` when the focus
        state cannot be queried (no ``is_foreground`` in ``user32``).
        The foreground attempt is skipped when the patch is already on."""
        if getattr(self.user32, "is_foreground", None) is None:
            return "unknown"
        if self.is_foreground():
            return "foreground"
        if self.background_running():
            return "patched"
        if foreground and self.bring_to_foreground():
            return "foreground"
        pm = self.patches
        if pm is not None and getattr(self.safety, "allow_patch", False):
            try:
                pm.enable("background_running")
                return "patched"
            except Exception as exc:
                warnings.warn("无法启用 background_running：%s" % exc)
        warnings.warn("游戏窗口不在前台且 background_running 未开：失焦时游戏暂停，输入可能被忽略")
        return "unfocused"

    # -- dialogs ---------------------------------------------------------------
    def dialog_count(self) -> Optional[int]:
        """``SexyAppBase::mDialogMap.size()`` when the profile carries a
        verified ``lawn_app.dialog_map`` offset; ``None`` otherwise."""
        la = getattr(self.profile, "lawn_app", None)
        getter = getattr(la, "get", None) if la is not None else None
        off = getter("dialog_map") if getter else None
        if off is None:
            return None
        lawn = int(self.observer.lawn_ptr() or 0)
        if not lawn:
            return None
        n = struct.unpack("<I", self.session.read(lawn + int(off) + 4, 4))[0]
        return int(n) if n < 64 else None

    def cutscene_dialog_active(self) -> bool:
        """The Crazy Dave intro talk signal (module doc): ``True`` when
        ``game_ui == 2`` and ``LawnApp::mCrazyDaveMessageIndex != -1`` on a
        GOTY build; ``False`` when it cannot be read."""
        try:
            from .cutscene import crazy_dave_message_index

            if self.ui() != UI_CHOOSER:
                return False
            idx = crazy_dave_message_index(self.session, self.profile, int(self.observer.lawn_ptr() or 0))
        except Exception:  # noqa: BLE001 -- no lawn_ptr / unreadable
            return False
        return idx is not None and idx > 0

    def dialog_open(self, expected_ui: Optional[int] = None, wait: float = 1.0) -> bool:
        """Best effort: ``True`` when a dialog seems to be open.  Signals in
        order: :meth:`cutscene_dialog_active`, :meth:`dialog_count` when
        available; otherwise, given ``expected_ui`` (the ui a just-posted
        click should have produced), polls for ``wait`` seconds and reports
        ``True`` when the ui never changed.  Without any, ``False``."""
        if self.cutscene_dialog_active():
            return True
        n = self.dialog_count()
        if n is not None:
            return n > 0
        if expected_ui is None:
            return False
        deadline = self._clock() + max(0.0, wait)
        while True:
            if self.ui() == int(expected_ui):
                return False
            if self._clock() >= deadline:
                return True
            self._sleep(0.05)

    def close_dialog(self, click_gap: float = 0.15, expected_ui: Optional[int] = None) -> bool:
        """Try to dismiss a dialog with **Escape only** (no blind clicks;
        module doc).  Returns ``True`` when :meth:`dialog_count` reaches 0
        or ``expected_ui`` is reached; ``False`` when neither can be checked
        or it stayed open.  A Crazy Dave talk is not a LawnDialog: Escape
        does nothing to it, so the call is refused (``False``) -- use
        :class:`~pvztools.control.cutscene.Cutscene.skip_dialog`."""
        if self.safety is not None:
            self.safety.require_input("关闭对话框")
        if self.cutscene_dialog_active():
            warnings.warn("有戴夫对话（mCrazyDaveMessageIndex != -1）：Escape 不会关闭它，请用 Cutscene.skip_dialog()")
            return False

        def closed() -> Optional[bool]:
            n = self.dialog_count()
            if n is not None:
                return n == 0
            if expected_ui is not None:
                return self.ui() == int(expected_ui)
            return None

        self._key(self.user32.VK_ESCAPE)
        self._sleep(click_gap)
        return bool(closed())

    # -- raw input -------------------------------------------------------------
    def _key(self, vk: int) -> None:
        self.history.append((int(self.user32.WM_KEYDOWN), int(vk), 0, 0))
        if self.dry_run:
            return
        fn = getattr(self.user32, "post_key", None)
        if fn is not None:
            fn(self.hwnd, vk)
        else:
            self.user32.post_message(self.hwnd, self.user32.WM_KEYDOWN, vk, 1)
            self.user32.post_message(self.hwnd, self.user32.WM_KEYUP, vk, 1 | (1 << 30) | (1 << 31))

    def click_logical(self, x: float, y: float, hold: float = 0.01, move: bool = False) -> Tuple[int, int]:
        """DOWN, ``hold`` seconds, UP at a *logical* point (optionally
        preceded by ``WM_MOUSEMOVE``, which widget-based screens such as
        the seed chooser need to hit-test buttons).  Returns the physical
        point posted."""
        px, py = self.logical_to_physical(x, y)
        u = self.user32
        if move:
            self._post(u.WM_MOUSEMOVE, 0, px, py)
            self._sleep(hold)
        self._post(u.WM_LBUTTONDOWN, 1, px, py)
        try:
            self._sleep(hold)
        finally:
            self._post(u.WM_LBUTTONUP, 0, px, py)
        return px, py

    def __repr__(self) -> str:
        return "GameWindow(hwnd=0x%X, calibration=%r)" % (self._hwnd, self.calibration)
