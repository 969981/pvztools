"""Input backend: act on the game by posting mouse messages (mode ``input``).

Unlike :class:`~pvztools.control.backends.RemoteBackend` this backend never
writes game memory.  It finds the game's main window
(:func:`pvztools._win.user32.find_game_window` with the session's pid) and
posts ``WM_LBUTTONDOWN`` / ``WM_LBUTTONUP`` with ``MAKELPARAM(x, y)`` -- the
game then does exactly what a player clicking there would do: the card must
be usable (cooldown done, enough sun), the shovel must be available, a cob
cannon must be ready.  The game reads the coordinates from the message
``lParam``; the real cursor is not moved and the window need not be in the
foreground (it must not be minimised, and ``background_running`` /
``Board.paused`` semantics still apply: a paused game ignores clicks).

Coordinate system
-----------------
All coordinates are **logical pixels of the 800x600 game surface** (PvZ
renders at a fixed 800x600).  Every message goes through
:meth:`InputBackend.click_logical` -> :meth:`GameWindow.logical_to_physical`
(:mod:`pvztools.control.window`), which applies the DPI-correct client
ratio (a DPI-unaware Python sees the 800x600 client as 640x480 at 125 %
and must post 0.8-scaled coordinates) or, after :meth:`calibrate`, the
measured scale + offset from ``Board.prev_mouse_x/y``.  ``auto_scale`` is
kept for compatibility and ignored: scaling is always on (identity when
the client size cannot be read).

Focus
-----
Before each action :meth:`GameWindow.ensure_running` is consulted: the game
pauses when it loses focus unless the ``background_running`` patch is on.
With ``foreground=True`` (default) the window is first brought to the
foreground (``AttachThreadInput`` trick); when that fails and a
:class:`PatchManager` (``patches=``) plus ``Safety(allow_patch=True)`` are
available the patch is enabled, otherwise a warning is issued once.

Geometry used (1-based rows / cols via :mod:`pvztools.control.grid`):

* seed-bank slot ``i`` (0-based) centre: ``(50 + 50*i, 70)``
  (TODO_VERIFY: constant borrowed from the community AHK/Python scripts'
  ``Card(n) = click(50+50*n, 70)``; the source's SeedPacket layout gives
  ``mX = 10 + i*SEED_PACKET_WIDTH`` with the first card starting at x≈25,
  so the centre of every 50-px-pitch slot is inside the packet on 1051 /
  GOTY; slot 10 (i=9) at x=500 was not measured),
* shovel button centre: ``(650, 40)`` (TODO_VERIFY: the shovel tray is drawn
  at x 600..680, y 0..70 in the default layout; 650/40 is the value used by
  pvztoolkit-style scripts),
* grid cell centre: ``grid_to_pixel_x(col) + 40``,
  ``grid_to_pixel_y(scene, col, row) + 40``,
* cob cannon target: :func:`pvztools.control.grid.cob_target_pixels`
  (explosion centre lands on ``(row, col)``; ``col`` may be fractional).

Click protocol
--------------
``click(x, y)`` = DOWN, wait one frame, UP.  Consecutive clicks are
separated by one frame as well so the game never sees a double click
(``theClickCount`` is derived by Windows from the timestamps of the posted
messages; ``WM_LBUTTONDBLCLK`` is never posted).  ``fire_cob`` additionally
honours ``Board::MouseDownCobcannonFire``'s misclick guard
(``mCobCannonCursorDelayCounter = 30`` ticks and ``Distance2D < 100`` px
cancels the shot): when the target is closer than 100 px to the cannon
click it waits 31 frames before the second click.

Waits use :func:`precise_sleep`: ``timeBeginPeriod(1)`` (winmm) is raised
for the duration of an action and restored afterwards; the thread sleeps
until ~2 ms before the target and busy-waits on ``perf_counter`` for the
rest.

Safety
------
Every action requires ``Safety(allow_input=True)``
(:meth:`pvztools.modifier.safety.Safety.require_input`).  ``Safety.dry_run``
is honoured as the global no-side-effects switch: with ``dry_run=True``
(the ``Safety`` default) the messages are only recorded in
:attr:`InputBackend.history` and nothing reaches the window.
``Game(..., input=True)`` / ``attach(input=True)`` build a Safety with
``dry_run=False`` so the CLI ``--input --yes`` path really clicks.

Timing
------
The game acts on the *second* ``WM_LBUTTONDOWN`` of a card/cell or
cannon/target pair, which is posted two frames after the first message
(:attr:`InputBackend.action_lag_cs`); a scheduler dispatching at tick *t*
gets the action at *t+2*.  Several cannons cannot fire in the same tick in
this mode (``fire_cobs`` is sequential, one frame between shots).
"""
from __future__ import annotations

import ctypes
import math
import sys
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, List, Optional, Sequence, Tuple

from .actions import resolve_plant
from .backends import BackendError, CobRowCol, SlotOrPlant, is_slot, pick_fodder
from .grid import cob_target_pixels, grid_to_pixel_x, grid_to_pixel_y, rows_of_scene
from .window import GameWindow
from .._win import user32 as _user32

__all__ = [
    "InputBackend", "precise_sleep", "timer_resolution", "make_lparam",
    "CLIENT_W", "CLIENT_H", "SLOT_X0", "SLOT_PITCH", "SLOT_Y", "SHOVEL_XY",
    "COB_MISCLICK_DIST", "COB_CURSOR_DELAY_TICKS",
]

#: Logical client size of the game window.
CLIENT_W = 800
CLIENT_H = 600
#: Seed-bank slot centres: x = SLOT_X0 + SLOT_PITCH * i, y = SLOT_Y (TODO_VERIFY).
SLOT_X0 = 50
SLOT_PITCH = 50
SLOT_Y = 70
#: Shovel button centre (TODO_VERIFY).
SHOVEL_XY = (650, 40)
#: ``Board::MouseDownCobcannonFire``: second click within this distance of the
#: cannon click while the delay counter runs is ignored.
COB_MISCLICK_DIST = 100
COB_CURSOR_DELAY_TICKS = 30
IMITATER = 48
COB_CANNON = 47
COB_READY = 37
#: The game's own debounce is per tick; one frame between messages is enough.
DEFAULT_FRAME_MS = 10.0
#: Sleep until this many ms before the target, then spin.
SPIN_MARGIN_S = 0.002

_winmm = None


def _get_winmm():
    global _winmm
    if _winmm is None and sys.platform == "win32":
        try:
            _winmm = ctypes.WinDLL("winmm")
        except OSError:  # pragma: no cover
            _winmm = False
    return _winmm or None


@contextmanager
def timer_resolution(ms: int = 1) -> Iterator[None]:
    """``timeBeginPeriod(ms)`` for the block (no-op off Windows)."""
    mm = _get_winmm()
    ok = False
    if mm is not None:
        try:
            ok = mm.timeBeginPeriod(int(ms)) == 0
        except Exception:  # pragma: no cover
            ok = False
    try:
        yield
    finally:
        if ok:
            mm.timeEndPeriod(int(ms))


def precise_sleep(seconds: float, until: Optional[Callable[[], bool]] = None) -> None:
    """Sleep ``seconds`` with ~sub-ms accuracy: ``time.sleep`` until
    ``SPIN_MARGIN_S`` before the deadline, then busy-wait on
    ``perf_counter``.  ``until()`` (optional) is polled while spinning and
    ends the wait early when it returns ``True``."""
    deadline = time.perf_counter() + max(0.0, float(seconds))
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if until is not None and until():
            return
        if remaining > SPIN_MARGIN_S:
            time.sleep(remaining - SPIN_MARGIN_S)


def make_lparam(x: int, y: int) -> int:
    """``MAKELPARAM``: low word = x, high word = y (client coordinates)."""
    return (int(y) & 0xFFFF) << 16 | (int(x) & 0xFFFF)


class InputBackend:
    """Post mouse clicks to the game window.  See the module docstring.

    ``session`` supplies ``pid`` (window lookup); ``observer`` supplies
    ``seeds()`` (slot lookup) and ``board().scene`` (row geometry);
    ``profile`` is kept for parity with the other backends (unused except
    for ``name`` in messages).  ``safety`` must carry ``allow_input=True``;
    ``None`` means *refuse everything*.

    ``patches`` (a :class:`~pvztools.modifier.patches.PatchManager`) lets
    :meth:`GameWindow.ensure_running` switch on ``background_running``;
    ``foreground=False`` skips the (one-off, first-action) bring-to-front
    attempt; ``window`` injects a ready :class:`GameWindow`.

    ``user32`` lets tests inject a fake module exposing ``post_message``,
    ``find_game_window`` and the ``WM_*`` constants; ``hwnd`` skips the
    lookup; ``sleep`` replaces :func:`precise_sleep`.
    """

    mode = "input"
    #: ticks between the first posted message and the one the game acts on
    #: (card DOWN, frame, UP, frame, cell DOWN): the planned executor
    #: dispatches this much earlier and reports the action at dispatch+lag.
    action_lag_cs = 2

    def __init__(self, session: Any, observer: Any, profile: Any = None,
                 safety: Any = None, *, hwnd: Optional[int] = None,
                 frame_ms: float = DEFAULT_FRAME_MS, auto_scale: bool = True,
                 user32: Any = None, sleep: Optional[Callable[[float], None]] = None,
                 patches: Any = None, foreground: bool = True,
                 window: Optional[GameWindow] = None) -> None:
        self.session = session
        self.observer = observer
        self.profile = profile
        self.safety = safety
        self.frame_ms = float(frame_ms)
        self.auto_scale = bool(auto_scale)  # compatibility; scaling is always on
        self.user32 = user32 if user32 is not None else _user32
        self._sleep = sleep or precise_sleep
        self.foreground = bool(foreground)
        self.window = window or GameWindow(session, observer, profile, patches, safety,
                                           hwnd=hwnd, user32=self.user32,
                                           sleep=lambda t: self._sleep(t))
        self._running_checked = False
        #: (msg, x, y) of every posted (or, in dry_run, recorded) message, in
        #: logical coordinates.
        self.history: List[Tuple[int, int, int]] = []

    @property
    def dry_run(self) -> bool:
        """``Safety.dry_run``: record messages without posting them."""
        return bool(getattr(self.safety, "dry_run", False))

    # -- plumbing ------------------------------------------------------------
    def _require(self, action: str) -> None:
        if self.safety is None:
            raise BackendError("%s：input 模式需要 Safety(allow_input=True)" % action)
        self.safety.require_input(action)
        self._ensure_running()

    def _ensure_running(self) -> Optional[str]:
        """Focus / ``background_running`` check before an action (skipped
        in dry run).  The bring-to-front attempt (``foreground=True``) is
        made **once** per backend -- a successful steal sleeps ~0.3 s,
        which must not land between a scheduled dispatch and its click --
        and never when ``background_running`` is already on.  The warning
        path is taken once per backend."""
        if self.dry_run:
            return None
        if self.window.is_foreground():
            self._running_checked = True
            return "foreground"
        if self.window.background_running():
            self._running_checked = True
            return "patched"
        if self._running_checked:
            return "unfocused"
        state = self.window.ensure_running(foreground=self.foreground)
        self._running_checked = True
        return state

    @property
    def hwnd(self) -> int:
        return self.window.hwnd

    @property
    def _hwnd(self) -> int:
        return self.window._hwnd

    def client_size(self) -> Tuple[int, int]:
        """DPI-correct client size (:meth:`GameWindow.client_size`)."""
        return self.window.client_size()

    def calibrate(self):
        """Measure scale + offset via ``Board.prev_mouse`` (needs a Board,
        i.e. seed chooser or level); see :meth:`GameWindow.calibrate`."""
        self._require("校准坐标")
        return self.window.calibrate()

    def _frame(self, n: float = 1.0) -> None:
        self._sleep(self.frame_ms * n / 1000.0)

    def _post(self, msg: int, x: int, y: int) -> None:
        self.history.append((msg, int(x), int(y)))
        if self.dry_run:
            return
        px, py = self.window.logical_to_physical(x, y)
        self.user32.post_message(self.hwnd, msg, 0, make_lparam(px, py))

    def click_logical(self, x: int, y: int) -> Tuple[int, int]:
        """DOWN, one frame, UP at a logical point (calibration applied);
        returns the physical point.  No safety gating -- callers gate."""
        self._click(x, y)
        return self.window.logical_to_physical(x, y)

    def _click(self, x: int, y: int) -> None:
        """DOWN, one frame, UP (no gating -- callers gate once per action).
        The UP is posted even when the frame wait is interrupted (Ctrl-C),
        so the game never keeps an unpaired button-down."""
        self._post(self.user32.WM_LBUTTONDOWN, x, y)
        try:
            self._frame()
        finally:
            self._post(self.user32.WM_LBUTTONUP, x, y)

    def _clicks(self, points: Sequence[Tuple[int, int]], gaps: Optional[Sequence[float]] = None) -> None:
        with timer_resolution(1):
            for i, (x, y) in enumerate(points):
                if i:
                    self._frame(gaps[i - 1] if gaps else 1.0)
                self._click(x, y)

    # -- geometry ------------------------------------------------------------
    def scene(self) -> int:
        return int(self.observer.board().scene)

    @staticmethod
    def slot_xy(slot: int) -> Tuple[int, int]:
        return SLOT_X0 + SLOT_PITCH * int(slot), SLOT_Y

    def cell_xy(self, row: int, col: float, scene: Optional[int] = None) -> Tuple[int, int]:
        """Centre of 1-based ``(row, col)``; the column may be fractional."""
        scene = self.scene() if scene is None else int(scene)
        rows = rows_of_scene(scene)
        if not (1 <= int(row) <= rows):
            raise BackendError("行 %s 超出 1..%d（场景 %d）" % (row, rows, scene))
        if not (1 <= float(col) <= 9):
            raise BackendError("列 %s 超出 1..9" % (col,))
        x = int(grid_to_pixel_x(col)) + 40
        y = grid_to_pixel_y(scene, int(math.floor(float(col))), int(row)) + 40
        return x, y

    def find_slot(self, plant: SlotOrPlant, imitater: bool = False) -> Any:
        """The :class:`~pvztools.model.types.Seed` for ``plant`` (slot index
        or plant name / id / PlantType).  An Imitater card mimicking the
        plant is used only when no plain card matches (``imitater=True``
        reverses the preference)."""
        seeds = list(self.observer.seeds())
        if plant is None:  # auto fodder (track ``C``): first usable fodder card
            pid, imit = pick_fodder(seeds)
            for s in seeds:
                if (not imit and int(s.type) == pid) or \
                        (imit and int(s.type) == IMITATER and int(s.imitater_type) == pid):
                    return s
        if is_slot(plant):
            for s in seeds:
                if int(s.slot) == int(plant):
                    if s.type < 0:
                        raise BackendError("卡槽 %d 为空" % int(plant))
                    return s
            raise BackendError("没有卡槽 %d" % int(plant))
        pid = resolve_plant(plant)
        plain = [s for s in seeds if int(s.type) == pid]
        mimic = [s for s in seeds if int(s.type) == IMITATER and int(s.imitater_type) == pid]
        for s in (mimic + plain) if imitater else (plain + mimic):
            return s
        raise BackendError("卡槽里没有 %r（现有：%s）" % (plant, ", ".join(
            "%d:%s" % (s.slot, s.type) for s in seeds if s.type >= 0) or "空"))

    # -- ActionBackend -------------------------------------------------------
    def click(self, x: int, y: int) -> Tuple[int, int]:
        self._require("点击 (%s,%s)" % (x, y))
        self._clicks([(int(x), int(y))])
        return int(x), int(y)

    def plant_card(self, slot_or_plant: SlotOrPlant, row: int, col: int, imitater: bool = False) -> Any:
        """Click the card then the cell.  Refuses when the card is not
        usable (cooldown / sun) -- the game would ignore the click and the
        cell click would become a stray click.  ``imitater=True`` prefers
        the Imitater copy of the card."""
        action = "种卡 %r -> (%s,%s)" % (slot_or_plant, row, col)
        self._require(action)
        seed = self.find_slot(slot_or_plant, imitater=imitater)
        if not seed.usable:
            raise BackendError("%s：卡槽 %d 不可用（冷却 %d/%d 或阳光不足）"
                               % (action, seed.slot, seed.cd_elapsed, seed.cd_total))
        sx, sy = self.slot_xy(seed.slot)
        cx, cy = self.cell_xy(row, col)
        self._clicks([(sx, sy), (cx, cy)])
        return seed

    def shovel(self, row: int, col: int) -> Tuple[int, int]:
        action = "铲 (%s,%s)" % (row, col)
        self._require(action)
        cx, cy = self.cell_xy(row, col)
        self._clicks([SHOVEL_XY, (cx, cy)])
        return cx, cy

    def _cob_click(self, cob_rowcol: CobRowCol, scene: int) -> Tuple[int, int]:
        r, c = int(cob_rowcol[0]), int(cob_rowcol[1])
        plants = getattr(self.observer, "plants", None)
        if plants is not None:
            for p in plants(include_dead=False):
                if int(p.type) == COB_CANNON and p.row + 1 == r and p.col + 1 == c:
                    if int(p.state) != COB_READY:
                        raise BackendError("炮 (%d,%d) 未就绪（state=%d）" % (r, c, int(p.state)))
                    break
            else:
                raise BackendError("(%d,%d) 没有炮（需给炮的左格）" % (r, c))
        return self.cell_xy(r, c, scene)

    def fire_cob(self, cob_rowcol: CobRowCol, row: int, col: float) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """Click the cannon (left cell) then the target; returns both points."""
        action = "发炮 %s -> (%s,%s)" % (tuple(cob_rowcol), row, col)
        self._require(action)
        scene = self.scene()
        src = self._cob_click(cob_rowcol, scene)
        dst = cob_target_pixels(scene, int(row), float(col))
        gap = 1.0
        if math.hypot(dst[0] - src[0], dst[1] - src[1]) < COB_MISCLICK_DIST:
            gap = COB_CURSOR_DELAY_TICKS + 1.0
        self._clicks([src, dst], [gap])
        return src, dst

    def fire_cobs(self, fires: Sequence[Tuple[CobRowCol, Tuple[int, float]]]) -> List[Any]:
        """Sequential ``fire_cob`` calls (4 frames per shot: one frame
        between shots, more when the misclick guard applies); input mode
        cannot fire several cannons in the same tick."""
        out = []
        for i, (cob, (r, c)) in enumerate(fires):
            if i:
                self._frame()
            out.append(self.fire_cob(cob, r, c))
        return out

    def __repr__(self) -> str:
        return "InputBackend(hwnd=0x%X, frame_ms=%s)" % (self.window._hwnd, self.frame_ms)
