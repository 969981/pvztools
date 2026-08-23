"""Main-menu / award-screen navigation through game calls (no clicks).

GOTY Steam 1.2.0.1096 only (profile functions ``game_selector_clicked_adventure``
and ``award_start_button``).  Conventions and object paths are derived in
:mod:`pvztools.control.chooser_mem` (analysis (e) / (f)):

* ``GameSelector::ClickedAdventure`` ``0x450B90`` -- ``push this; call``
  (``ret 4``); the selector is ``LawnApp + lawn_app.game_selector``.  It only
  *starts* the zombie-hand animation; ``GameSelector::Update`` opens the
  level when it ends, so the ui (1 -> 2 chooser / 3 level) is polled.
* ``AwardScreen::StartButtonPressed`` ``0x40A310`` -- ``push this; call``
  (``ret 4``); the screen is ``LawnApp + 0x878`` and exists while
  ``game_ui == 5``.  It does nothing while a dialog is open.

Both are *transition* fragments (``Fragment.needs_main_loop == False``):
the executor keeps ``main_loop_block`` on for the whole remote call (as
Toolkit does for every call -- releasing it mid-handler let the game thread
race ours inside UI code and the process died) with the longer transition
timeout, and the ui is polled here *after* the call returned, with the loop
free -- never hold ``main_loop_block`` while waiting for a screen switch (a
timeout releases the patch and raises).  Preconditions before the
call: the exact ui (1 / 5), object pointer non-null and inside a committed
region (:func:`~pvztools.control.chooser_mem.require_object`).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from .backends import BackendError
from .chooser_mem import APP_AWARD_SCREEN, require_object
from .conv import Fragment, convention_for
from .window import UI_CHOOSER, UI_LEVEL, UI_MENU

__all__ = ["MainMenu", "MenuError", "UI_AWARD"]

UI_AWARD = 5


class MenuError(BackendError):
    """Precondition failure of a menu navigation call."""


class MainMenu:
    """Navigate main menu / award screen by remote calls.

    ``observer`` is optional (``game().ui`` / ``lawn_ptr()``); without it
    the ui and the LawnApp pointer are read through ``session``.
    """

    def __init__(self, session: Any, profile: Any, executor: Any, observer: Any = None, *,
                 conv: Any = None, sleep: Optional[Callable[[float], None]] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self.session = session
        self.profile = profile
        self.executor = executor
        self.observer = observer
        self.conv = conv if conv is not None else convention_for(profile)
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self.history: List[Fragment] = []
        fns = getattr(profile, "functions", {}) or {}
        fam = getattr(profile, "conventions", None) or getattr(profile, "family", None)
        missing = [n for n in ("game_selector_clicked_adventure", "award_start_button") if n not in fns]
        if fam != "goty" or missing:
            raise MenuError("菜单远程调用只支持 GOTY Steam 1.2.0.1096（profile %s 缺 %s）"
                            % (getattr(profile, "id", "?"), missing or fam))

    # -- state ----------------------------------------------------------------------
    @property
    def dry_run(self) -> bool:
        safety = getattr(self.executor, "safety", None)
        return bool(getattr(safety, "dry_run", False) or getattr(self.executor, "dry_run", False))

    def lawn(self) -> int:
        if self.observer is not None:
            return int(self.observer.lawn_ptr() or 0)
        return int(self.session.read_ptr(self.profile.globals.lawn_app))

    def ui(self) -> int:
        if self.observer is not None:
            return int(self.observer.game().ui)
        lawn = self.lawn()
        return self.session.read_i32(lawn + int(self.profile.lawn_app.game_ui)) if lawn else 0

    def game_selector_ptr(self) -> int:
        lawn = self.lawn()
        return self.session.read_ptr(lawn + int(self.profile.lawn_app.game_selector)) if lawn else 0

    def award_ptr(self) -> int:
        lawn = self.lawn()
        return self.session.read_ptr(lawn + APP_AWARD_SCREEN) if lawn else 0

    # -- plumbing ---------------------------------------------------------------------
    def _check(self, action: str, ui: int) -> None:
        cur = self.ui()
        if cur != ui:
            raise MenuError("%s：需要 game_ui=%d，现在 game_ui=%d" % (action, ui, cur))
        chk = getattr(self.executor, "check", None)
        if chk is not None:
            chk(action, ui=(ui,))

    def _object(self, ptr: int, what: str, size: int = 4) -> int:
        """Non-null + committed-region gate for a screen object pointer."""
        return require_object(self.session, ptr, what, size, error=MenuError)

    def _run(self, frag: Fragment, name: str) -> Any:
        """Submit ``frag``; transition fragments (``needs_main_loop`` False)
        run with ``transition=True`` (longer timeout; loop blocked for the whole call)."""
        self.history.append(frag)
        kw: Dict[str, Any] = {}
        if not getattr(frag, "needs_main_loop", True):
            kw["transition"] = True
        return self.executor.run(frag.asm, name=name, extra_patches=tuple(frag.required_patches), **kw)

    def _poll_ui(self, accept: Callable[[int], bool], wait: float) -> Optional[int]:
        if self.dry_run:
            return None
        deadline = self._clock() + max(0.0, float(wait))
        while True:
            ui = self.ui()
            if accept(ui):
                return ui
            if self._clock() >= deadline:
                return None
            self._sleep(0.1)

    # -- actions ----------------------------------------------------------------------
    def start_adventure(self, wait: float = 10.0) -> Optional[int]:
        """``GameSelector::ClickedAdventure`` from the main menu (``ui == 1``);
        waits up to ``wait`` s for ``ui`` 2 (seed chooser) or 3 (levels
        without a chooser).  Returns the new ui, ``None`` on timeout / dry run."""
        self._check("开始冒险模式", UI_MENU)
        sel = self._object(self.game_selector_ptr(), "开始冒险模式：LawnApp.game_selector", 0xE8)
        frag = self.conv.game_selector_adventure(sel, terminate=True)
        # transition: loop blocked for the whole call, released when it returns;
        # poll the ui afterwards with the loop free
        self._run(frag, "game_selector_adventure")
        return self._poll_ui(lambda u: u in (UI_CHOOSER, UI_LEVEL), wait)

    def award_continue(self, wait: float = 10.0) -> Optional[int]:
        """``AwardScreen::StartButtonPressed`` on the award screen (``ui == 5``);
        waits for the ui to leave 5.  Returns the new ui, ``None`` on
        timeout / dry run."""
        self._check("奖励界面继续", UI_AWARD)
        award = self._object(self.award_ptr(), "奖励界面继续：LawnApp+0x878 (AwardScreen)")
        frag = self.conv.award_start(award, terminate=True)
        # transition: loop blocked for the whole call, released when it returns;
        # poll the ui afterwards with the loop free
        self._run(frag, "award_start")
        return self._poll_ui(lambda u: u != UI_AWARD, wait)

    def __repr__(self) -> str:
        return "MainMenu(profile=%s)" % getattr(self.profile, "id", "?")
