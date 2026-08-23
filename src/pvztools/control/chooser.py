"""Seed chooser automation (``game_ui == 2``) by posting clicks.

Geometry (re-plants ``SeedChooserScreen`` / ``Board``):

* chooser card ``idx`` (= seed type id; the Imitater has its own button):
  ``x = (idx % 8) * 53 + 22``, ``y = (idx // 8) * 73 + 128``, or
  ``y = row * 70 + 123`` when ``Has7Rows()`` (adventure finished / Gatling
  Pea bought / any upgrade plant available) -- ``has7rows`` flag, default
  ``False``; cards are ``SEED_PACKET_WIDTH x HEIGHT = 50 x 70`` so the
  centre is ``(+25, +35)``.
* bank packet ``i`` of ``n`` (``Board::GetSeedPacketPositionX``):
  ``n <= 7: i*59+85``, ``n == 8: i*54+81``, ``n == 9: i*52+80``, else
  ``i*51+79``; ``y = 8`` (packet top in the bank), centre ``(+25, +35)``.
  Clicking a bank packet in the chooser returns it to the chooser and the
  remaining packets shift left -- so *clearing* the bank is ``n`` clicks on
  slot 0.
* *Let's Rock!* button: ``mStartButton->Resize(154, 545, 156, 42)``; it is
  disabled until the bank is full.

Every click goes through :meth:`GameWindow.click_logical` (calibrated,
DPI-correct).  The chooser's widgets hit-test on the cursor position, so a
``WM_MOUSEMOVE`` is posted before each click.
"""
from __future__ import annotations

import time
import warnings
from typing import Any, Callable, List, Optional, Sequence, Tuple, Union

from .actions import resolve_plant
from .backends import BackendError
from .grid import POOL_SCENES
from .window import UI_CHOOSER, UI_LEVEL

__all__ = [
    "SeedChooser", "chooser_card_xy", "bank_packet_xy", "LETS_ROCK_RECT",
    "LETS_ROCK_XY", "DEFAULT_FILL", "DEFAULT_FILL_POOL", "CARD_W", "CARD_H",
]

CARD_W = 50
CARD_H = 70
BANK_Y = 8
LETS_ROCK_RECT = (154, 545, 156, 42)
LETS_ROCK_XY = (LETS_ROCK_RECT[0] + LETS_ROCK_RECT[2] // 2,
                LETS_ROCK_RECT[1] + LETS_ROCK_RECT[3] // 2)
IMITATER = 48
SUNFLOWER, LILY_PAD = 1, 16
#: Auto-fill order when fewer cards than bank slots are requested.
DEFAULT_FILL: Tuple[int, ...] = (1, 0, 3, 2, 4, 5, 6, 7, 8, 9)          # 向日葵 豌豆 坚果 樱桃 土豆 寒冰 大嘴 双发 小喷 阳光菇
DEFAULT_FILL_POOL: Tuple[int, ...] = (1, 16, 0, 3, 2, 4, 5, 6, 7, 8)    # + 荷叶

SeedSpec = Union[int, str, Any]


def chooser_card_xy(idx: int, has7rows: bool = False) -> Tuple[int, int]:
    """Centre of chooser card ``idx`` (seed type id, not the Imitater)."""
    idx = int(idx)
    if not (0 <= idx < IMITATER):
        raise BackendError("选卡界面没有卡 %d（模仿者有自己的按钮，不能这样选）" % idx)
    row, col = divmod(idx, 8)
    x = col * 53 + 22
    y = row * 70 + 123 if has7rows else row * 73 + 128
    return x + CARD_W // 2, y + CARD_H // 2


def bank_packet_xy(i: int, n: int) -> Tuple[int, int]:
    """Centre of bank packet ``i`` when the bank holds ``n`` slots."""
    i, n = int(i), int(n)
    if n <= 7:
        x = i * 59 + 85
    elif n == 8:
        x = i * 54 + 81
    elif n == 9:
        x = i * 52 + 80
    else:
        x = i * 51 + 79
    return x + CARD_W // 2, BANK_Y + CARD_H // 2


class SeedChooser:
    """Drive the seed chooser through ``window`` (:class:`GameWindow`).

    ``observer.seeds()`` gives the bank (slot count = ``len(seeds)``, a
    slot with ``type < 0`` is empty); ``observer.game().ui`` is polled by
    :meth:`start`.  ``gap`` is the pause between clicks (the chooser
    animates packets flying in/out; 0.35 s worked on the live game).
    ``foreground`` (default True) brings the window to the front before
    the first action (see :meth:`GameWindow.ensure_running`).
    """

    def __init__(self, window: Any, observer: Any, *, has7rows: bool = False,
                 gap: float = 0.35, sleep: Optional[Callable[[float], None]] = None,
                 clock: Optional[Callable[[], float]] = None, foreground: bool = True) -> None:
        self.window = window
        self.observer = observer
        self.has7rows = bool(has7rows)
        self.gap = float(gap)
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        #: bring the window to the front before the first click
        #: (:meth:`GameWindow.ensure_running`); ``False`` relies on focus /
        #: the ``background_running`` patch
        self.foreground = bool(foreground)
        self._running_checked = False
        #: logical points clicked, in order
        self.clicks: List[Tuple[int, int]] = []

    # -- state -----------------------------------------------------------------
    def ui(self) -> int:
        return int(self.observer.game().ui)

    def bank(self) -> List[Any]:
        return list(self.observer.seeds())

    def bank_size(self) -> int:
        return len(self.bank())

    def bank_types(self) -> List[int]:
        return [int(s.type) for s in self.bank() if int(s.type) >= 0]

    def scene(self) -> Optional[int]:
        try:
            return int(self.observer.board().scene)
        except Exception:
            return None

    def _require_chooser(self, action: str) -> None:
        ui = self.ui()
        if ui != UI_CHOOSER:
            raise BackendError("%s：需要在选卡界面（game_ui=2），现在 game_ui=%d" % (action, ui))
        self._ensure_running()

    def _ensure_running(self) -> Optional[str]:
        """Once per chooser: focus (``foreground``) / ``background_running``
        via :meth:`GameWindow.ensure_running`; skipped in dry run."""
        if getattr(self.window, "dry_run", False) or self._running_checked:
            return None
        fn = getattr(self.window, "ensure_running", None)
        if fn is None:
            return None
        self._running_checked = True
        return fn(foreground=self.foreground)

    def _click(self, x: int, y: int) -> None:
        self.clicks.append((int(x), int(y)))
        self.window.click_logical(x, y, move=True)

    # -- actions ---------------------------------------------------------------
    def clear_bank(self, n: Optional[int] = None) -> int:
        """Click bank slot 0 ``n`` times (default: number of filled slots);
        each click returns the first packet to the chooser and the rest
        shift left.  Returns the number of clicks."""
        self._require_chooser("清空卡槽")
        size = self.bank_size()
        if n is None:
            n = len(self.bank_types())
        n = int(n)
        for k in range(n):
            if k:
                self._sleep(self.gap)
            self._click(*bank_packet_xy(0, size))
        if n:
            self._sleep(self.gap)
        return n

    def pick(self, types: Sequence[SeedSpec]) -> List[int]:
        """Click the chooser card of every seed in ``types`` (ids, names or
        ``PlantType``).  Returns the resolved ids."""
        self._require_chooser("选卡")
        ids = [resolve_plant(t) for t in types]
        for k, pid in enumerate(ids):
            if k:
                self._sleep(self.gap)
            self._click(*chooser_card_xy(pid, self.has7rows))
        if ids:
            self._sleep(self.gap)
        return ids

    def fill_types(self, types: Sequence[SeedSpec], size: Optional[int] = None) -> List[int]:
        """``types`` resolved and padded with :data:`DEFAULT_FILL`
        (:data:`DEFAULT_FILL_POOL` on pool / fog scenes) up to ``size``
        slots, skipping duplicates; warns when padding was needed (the
        *Let's Rock* button only enables with a full bank) and when more
        than ``size`` cards were requested (the extras are dropped)."""
        size = self.bank_size() if size is None else int(size)
        ids: List[int] = []
        for t in types:
            pid = resolve_plant(t)
            if pid not in ids:
                ids.append(pid)
        if len(ids) > size:
            warnings.warn("选卡 %d 张超过卡槽 %d 个，多余的忽略" % (len(ids), size))
            ids = ids[:size]
        if len(ids) < size:
            scene = self.scene()
            pool = scene in POOL_SCENES if scene is not None else False
            fill = DEFAULT_FILL_POOL if pool else DEFAULT_FILL
            missing = size - len(ids)
            for pid in fill:
                if len(ids) >= size:
                    break
                if pid not in ids:
                    ids.append(pid)
            warnings.warn("只给了 %d 张卡而卡槽有 %d 个（不满时 Let's Rock 不可用）：自动补 %d 张默认卡 %s"
                          % (size - missing, size, missing, ids[size - missing:]))
        return ids

    def bank_full(self) -> bool:
        """Every bank slot holds a card (*Let's Rock!* is disabled otherwise)."""
        bank = self.bank()
        return bool(bank) and all(int(s.type) >= 0 for s in bank)

    def start(self, wait: float = 5.0, retry: bool = True) -> bool:
        """Click *Let's Rock!* and poll for ``game_ui == 3``.  When the ui
        does not change and :meth:`GameWindow.dialog_count` reports an open
        dialog, it is closed and the click retried once.  Without a
        dialog count (profile lacks ``lawn_app.dialog_map``) an unchanged ui
        is *not* taken as a dialog -- nothing is posted blindly; the
        usual causes (bank not full -> button disabled, click off target)
        are reported in a warning instead.  Returns whether the level
        started."""
        self._require_chooser("开始关卡")
        if not self.bank_full():
            warnings.warn("卡槽未满（%d/%d）：Let's Rock 按钮不可用，点击无效"
                          % (len(self.bank_types()), self.bank_size()))
        self._click(*LETS_ROCK_XY)
        if self._poll_ui(UI_LEVEL, wait):
            return True
        if self.ui() == UI_LEVEL:
            return True
        n = self.window.dialog_count()
        if retry and n:
            warnings.warn("Let's Rock 后界面未切换且有 %d 个对话框：尝试关闭后重试" % n)
            self.window.close_dialog(expected_ui=UI_CHOOSER)
            if self.ui() == UI_LEVEL:
                return True
            if self.ui() == UI_CHOOSER and not self.window.dialog_count():
                return self.start(wait=wait, retry=False)
        elif n is None:
            warnings.warn("Let's Rock 后界面未切换（game_ui=%d）；无法检测对话框（profile 缺 lawn_app.dialog_map），"
                          "不盲发 Escape。检查卡槽是否已满 / 坐标校准" % self.ui())
        return self.ui() == UI_LEVEL

    def _poll_ui(self, target: int, wait: float) -> bool:
        deadline = self._clock() + max(0.0, float(wait))
        while True:
            if self.ui() == target:
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(0.1)

    def select_and_start(self, types: Sequence[SeedSpec], wait: float = 5.0) -> bool:
        """Clear the bank, pick ``types`` (auto-filled to the bank size),
        start.  Raises :class:`BackendError` when the bank does not hold
        the requested cards after picking (the card is not unlocked or the
        click missed)."""
        self._require_chooser("选卡并开始")
        size = self.bank_size()
        ids = self.fill_types(types, size)
        self.clear_bank()
        self.pick(ids)
        have = self.bank_types()
        missing = [pid for pid in ids if pid not in have]
        if missing:
            raise BackendError("选卡后卡槽缺少 %s（现有 %s）：卡未解锁或点击未命中（检查校准 / 7 行布局 has7rows）"
                               % (missing, have))
        return self.start(wait=wait)

    def __repr__(self) -> str:
        return "SeedChooser(has7rows=%s, gap=%s)" % (self.has7rows, self.gap)
