"""Play one level end to end (DESIGN §6.3 *play* orchestrator).

:class:`PlaySession` strings the pieces together::

    PlaySession(game, lineup="3C八炮", track="P4: PP|PP|PP|PP (869|869|869|869)",
                seeds=None, cob_col=8, backend="remote", auto_collect=True).run()

1. **window** -- ``foreground=True`` brings the game to the front *for
   viewing only*; when it stays unfocused :meth:`GameWindow.ensure_running`
   enables the ``background_running`` patch so the game keeps ticking.
2. **navigate** -- ``ui 5`` (award screen): :meth:`MainMenu.award_continue`;
   ``ui 1`` (main menu): :meth:`MainMenu.start_adventure` then wait for
   ``ui 2``; ``ui 2`` (level intro cutscene incl. the seed chooser): first
   the **cutscene step** -- :meth:`Cutscene.skip_dialog` advances a Crazy
   Dave talk line by line (the game's own ``AdvanceCrazyDaveDialog(true)``
   path) and :meth:`Cutscene.wait_ready_for_chooser` waits for
   ``mSeedChoosing`` -- then :meth:`MemoryChooser.select_and_start` with
   ``seeds`` (default :data:`DEFAULT_SEEDS`); when the memory chooser is
   not available for this build / grant it falls back to the click chooser
   (``game.chooser``, needs ``input=True``, started with ``retry=False`` so
   no dialog is ever closed automatically); then wait for ``ui 3``.
   **Never on a suspected dialog**: an error carrying
   ``dialog_suspected`` (:class:`~pvztools.control.chooser_mem.ChooserDialogError`,
   :class:`~pvztools.control.cutscene.CutsceneDialogError`) stops play
   with the cutscene state in the report -- no click-chooser fallback, no
   Escape, no blind clicks (2026-08-22 incident: ``OnStartButton`` during
   the talk + click fallback, process died).  **Never after a remote-call
   failure that may have left our thread inside the game** (a
   :class:`~pvztools.control.remote.RemoteCallError` -- timeout or
   ``thread_running``): the leaked thread may still be executing
   ``SeedChooserScreen`` code while the main loop has just been released,
   so posting clicks into the same screen is the very race the whole-call
   block removes -- play stops instead.
   **The click chooser never clicks during a Crazy Dave talk**: before any
   click (also in the input-only path, where no ``Cutscene`` helper exists)
   play reads :meth:`GameWindow.cutscene_dialog_active` (memory only, no
   remote grant needed) and waits up to ``wait_ui_s`` for the talk to end
   (the user may click through it); if it is still active play stops.
   ``game_ui == 2`` alone is not a chooser: a click during the talk is
   ``Board::MouseDown`` -> ``CutScene::MouseDown`` ->
   ``AdvanceCrazyDaveDialog(false)``, which at 1503 / 1553 opens the modal
   purchase dialog that the remaining seed / *Let's Rock* clicks would hit.
3. **level** -- ``place_lineup(lineup)`` (remote) and the optional seed slot
   rewrite (``slot_types``).
4. **execute** -- :class:`PlannedExecutor` with frame-locked cob fires (a
   cannon that is not ready at its planned tick is fired when it becomes
   ready and counted apart as ``cd-wait``) plus an :class:`AutoCollector`
   ticking on every clock poll.
   The executor is built here (``game.game_clock`` / ``game.backend`` /
   ``game.plan_track``) so the session owns it: with ``tail_timeout_s > 0``
   the executor does not return when the plan is exhausted while the level
   is still running (``ui 3``) -- it keeps polling / collecting (and, with
   ``tail_policy='repeat'``, re-fires the last cycle on later waves) until
   the level completes or the tail timeout.
5. **finish (tail)** -- after the executor returns the collector keeps
   ticking while ``ui == 3`` (the level-end award coin --
   ``AutoCollector.LEVEL_AWARD_TYPES`` -- must be picked up before the game
   moves on); on ``ui 5`` :meth:`award_continue` is called **once** (a
   failure is reported, never retried).  ``report.meta["tail"]`` carries
   the executor's tail plus ``session_seconds`` / ``session_collected`` /
   ``award_item_collected`` / ``award_continue``.
6. **release** -- in a ``finally``: ``executor.release()`` and the
   ``background_running`` patch are disabled, so no patch stays enabled
   after ``play`` (the game is never left frozen), and one line
   ``play finished: ...`` is printed (``quiet=True`` silences it).

The :class:`ExecutionReport` (``meta`` filled with version / lineup / track
/ scene / waves / phase log / tail) is returned.

Every phase is logged in :attr:`PlaySession.steps` and copied to
``report.meta["steps"]``; :meth:`ExecutionReport.to_markdown` renders it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .backends import BackendError

__all__ = ["PlaySession", "PlayError", "DEFAULT_SEEDS", "DEFAULT_LINEUP", "DEFAULT_TRACK",
           "UI_MENU", "UI_CHOOSER", "UI_LEVEL", "UI_AWARD",
           "play_one_level", "LevelReport", "FireLog", "default_lineup", "default_target_col",
           "FALLBACK_LINEUPS"]

UI_MENU = 1
UI_CHOOSER = 2
UI_LEVEL = 3
UI_AWARD = 5

DEFAULT_LINEUP = "3C八炮"
DEFAULT_TRACK = "P4: PP|PP|PP|PP (869|869|869|869)"
#: chooser default: first chooser row (peashooter .. chomper) + lily pad;
#: the memory chooser's ``ensure_start_safe`` adds whatever *Let's Rock!*
#: insists on (sun producer / pool plants ...) and trims to the bank size
DEFAULT_SEEDS: Tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 16)


class PlayError(BackendError):
    """The orchestrator could not reach / finish the level."""


class PlaySession:
    """See the module doc.  ``game`` is a :class:`pvztools.game.Game`.

    ``chooser``: ``"auto"`` (memory chooser, click fallback), ``"memory"``
    or ``"input"``.  ``backend``: ``"remote"`` (修改器 frame-locked fires) or
    ``"input"`` (键控).  ``run_kw`` go to ``Game.run_track`` (``tolerance_cs``,
    ``late_policy``, ``lead_cs``, ExpandContext overrides ...).

    Test hooks: ``sleep`` / ``clock`` (wall time), ``menu_factory`` /
    ``chooser_factory`` / ``cutscene_factory`` / ``collector_factory``
    replace the default constructors (each receives the game).
    """

    def __init__(self, game: Any, *, lineup: Any = DEFAULT_LINEUP, track: Any = DEFAULT_TRACK,
                 seeds: Optional[Sequence[Any]] = None, cob_col: Optional[float] = 8,
                 backend: str = "remote", auto_collect: bool = True, foreground: bool = True,
                 chooser: str = "auto", slot_types: Any = None, place: bool = True,
                 dry_run: bool = False, settle_s: float = 0.8, wait_ui_s: float = 20.0,
                 award_wait_s: float = 120.0, finish_level: bool = True,
                 track_label: Optional[str] = None, on_event: Any = None,
                 firing: str = "plan", cob_cycle: float = 869, ignore_zombies: bool = True,
                 tail_timeout_s: float = 600.0, tail_policy: Optional[str] = None, quiet: bool = False,
                 sleep: Optional[Callable[[float], None]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 menu_factory: Optional[Callable[[Any], Any]] = None,
                 chooser_factory: Optional[Callable[[Any], Any]] = None,
                 cutscene_factory: Optional[Callable[[Any], Any]] = None,
                 collector_factory: Optional[Callable[[Any], Any]] = None,
                 **run_kw: Any) -> None:
        if backend not in ("remote", "input"):
            raise ValueError("backend must be 'remote' or 'input', got %r" % (backend,))
        if chooser not in ("auto", "memory", "input"):
            raise ValueError("chooser must be 'auto', 'memory' or 'input', got %r" % (chooser,))
        if firing not in ("plan", "strict"):
            raise ValueError("firing must be 'plan' or 'strict', got %r" % (firing,))
        self.game = game
        #: ``"plan"`` -> the :class:`PlannedExecutor` scheduler (track based);
        #: ``"strict"`` -> the fixed-cycle strict-CD firing loop
        #: (:func:`play_one_level`), which ignores zombies and fires every
        #: ``cob_cycle`` cs of game clock (``strict_front`` recipe).
        self.firing = firing
        self.cob_cycle = float(cob_cycle)
        self.ignore_zombies = bool(ignore_zombies)
        self.lineup = lineup
        self.track = track
        self.track_label = track_label
        self.seeds = list(seeds) if seeds is not None else list(DEFAULT_SEEDS)
        self.cob_col = None if cob_col is None else float(cob_col)
        self.backend = backend
        self.auto_collect = bool(auto_collect)
        self.foreground = bool(foreground)
        self.chooser_mode = chooser
        self.slot_types = slot_types
        self.place = bool(place)
        self.dry_run = bool(dry_run)
        self.settle_s = float(settle_s)
        self.wait_ui_s = float(wait_ui_s)
        self.award_wait_s = float(award_wait_s)
        self.finish_level = bool(finish_level)
        self.on_event = on_event
        self.tail_timeout_s = max(0.0, float(tail_timeout_s or 0.0))
        self.tail_policy = tail_policy
        self.quiet = bool(quiet)
        self.run_kw = dict(run_kw)
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._menu_factory = menu_factory
        self._chooser_factory = chooser_factory
        self._cutscene_factory = cutscene_factory
        self._collector_factory = collector_factory
        #: phase log: ``{"name", "result", "seconds", ...}`` per phase
        self.steps: List[Dict[str, Any]] = []
        self.notes: List[str] = []
        self.collector: Any = None
        self.report: Any = None
        self.executor: Any = None
        #: tail summary (executor tail + session tail), also ``report.meta["tail"]``
        self.tail: Dict[str, Any] = {}
        self.released: List[str] = []
        self._menu: Any = None
        self._cutscene: Any = None
        #: last cutscene state seen (``report.meta["cutscene"]``)
        self.cutscene_state: Any = None

    # -- helpers ---------------------------------------------------------------------
    def _step(self, name: str, result: Any, t0: Optional[float] = None, **extra: Any) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": name, "result": result}
        if t0 is not None:
            d["seconds"] = round(self._clock() - t0, 3)
        d.update(extra)
        self.steps.append(d)
        return d

    def ui(self) -> int:
        return int(self.game.ui)

    def wait_ui(self, accept: Any, wait: Optional[float] = None, step: float = 0.1) -> Optional[int]:
        """Poll ``game.ui`` until ``accept(ui)`` (or ``ui in accept``); the ui
        reached, ``None`` on timeout."""
        if not callable(accept):
            wanted = set(int(u) for u in accept)
            accept = lambda u: u in wanted  # noqa: E731
        deadline = self._clock() + (self.wait_ui_s if wait is None else max(0.0, float(wait)))
        while True:
            ui = self.ui()
            if accept(ui):
                return ui
            if self._clock() >= deadline:
                return None
            self._sleep(step)

    def _settled_ui(self) -> int:
        """The ui once it has held for ``settle_s`` (screens pass through
        transient values: a saved game loads as ui 3 before its chooser)."""
        ui = self.ui()
        if self.settle_s <= 0:
            return ui
        deadline = self._clock() + max(self.settle_s * 6, 2.0)
        while self._clock() < deadline:
            self._sleep(self.settle_s)
            nxt = self.ui()
            if nxt == ui:
                return ui
            ui = nxt
        return ui

    @property
    def menu(self) -> Any:
        if self._menu is None:
            if self._menu_factory is not None:
                self._menu = self._menu_factory(self.game)
            else:
                from .menu import MainMenu

                g = self.game
                self._menu = MainMenu(g.session, g.profile, g._executor, g._observer)
        return self._menu

    def _memory_chooser(self) -> Any:
        if self._chooser_factory is not None:
            return self._chooser_factory(self.game)
        from .chooser_mem import MemoryChooser

        g = self.game
        return MemoryChooser(g.session, g.profile, g._executor, g._observer, window=g.window,
                             cutscene=self._cutscene or None)

    def _cutscene_helper(self) -> Any:
        """:class:`~pvztools.control.cutscene.Cutscene` for this game, or
        ``None`` when the build has no cutscene support (then nothing can
        be detected and the chooser is tried as before).  Built once."""
        if self._cutscene is None:
            if self._cutscene_factory is not None:
                try:
                    self._cutscene = self._cutscene_factory(self.game)
                except Exception as exc:  # noqa: BLE001
                    self.notes.append("cutscene helper unavailable: %s" % exc)
                    self._cutscene = False
            elif not getattr(self.game, "remote", False):
                self._cutscene = False
            else:
                try:
                    from .cutscene import Cutscene

                    g = self.game
                    self._cutscene = Cutscene(g.session, g.profile, g._executor, g._observer, window=g.window)
                except Exception as exc:  # noqa: BLE001 -- unsupported build / grant
                    self.notes.append("cutscene helper unavailable: %s" % exc)
                    self._cutscene = False
        return self._cutscene or None

    def _cutscene_report(self, cs: Any) -> Any:
        fn = getattr(cs, "report", None)
        st = _safe(fn) if callable(fn) else None
        if st is not None:
            self.cutscene_state = st
        return st

    def _cutscene_talk_active(self) -> Optional[bool]:
        """The cheap talk signal (``game_ui == 2`` and
        ``mCrazyDaveMessageIndex != -1``) through the window -- memory
        reads only, so it works without the remote grant.  ``None`` when
        the window cannot tell (no such method / unreadable)."""
        win = getattr(self.game, "window", None)
        fn = getattr(win, "cutscene_dialog_active", None)
        if not callable(fn):
            return None
        try:
            return bool(fn())
        except Exception as exc:  # noqa: BLE001 -- unreadable: unknown
            self.notes.append("cutscene signal unreadable: %s" % exc)
            return None

    def _wait_no_cutscene_talk(self, what: str) -> None:
        """Refuse to click while Dave is talking: wait up to ``wait_ui_s``
        for the talk to end (or the ui to leave 2), then raise
        :class:`PlayError` when it is still active.  No-op when the
        signal is unavailable."""
        if not self._cutscene_talk_active():
            return
        t0 = self._clock()
        deadline = t0 + self.wait_ui_s
        while self._clock() < deadline:
            self._sleep(0.2)
            if not self._cutscene_talk_active():
                self._step("cutscene", "talk ended (waited, no clicks)", t0)
                return
        self._step("cutscene", "talk still active: %s refused" % what, t0)
        raise PlayError("戴夫对话进行中（mCrazyDaveMessageIndex != -1），不点击选卡 / 不盲点：%s" % what)

    def _skip_cutscene_dialog(self) -> Optional[Dict[str, Any]]:
        """The cutscene step of ``navigate`` (ui 2): skip a Crazy Dave talk
        and wait until the chooser is ready.  Raises :class:`PlayError`
        (no fallback) when a dialog is still suspected afterwards."""
        cs = self._cutscene_helper()
        if cs is None:
            return None
        t0 = self._clock()
        try:
            cs.state()
        except Exception as exc:  # noqa: BLE001 -- state unreadable (no board / fake): nothing to detect
            self.notes.append("cutscene state unreadable (%s); no dialog handling" % exc)
            self._cutscene = False
            return None
        try:
            steps = cs.skip_dialog()
            ready = cs.wait_ready_for_chooser(self.wait_ui_s)
        except Exception as exc:  # noqa: BLE001 -- CutsceneDialogError / remote failure
            st = self._cutscene_report(cs)
            self._step("cutscene", "failed: %s" % exc, t0, state=st)
            raise PlayError("过场对话处理失败，停止（不盲点）：%s" % exc) from exc
        st = self._cutscene_report(cs)
        step = self._step("cutscene", "skipped %d line(s), chooser %s" % (steps, "ready" if ready else "not ready"),
                          t0, steps=steps, ready=ready, state=st)
        if self.ui() == UI_LEVEL:
            return step
        if not ready or (callable(getattr(cs, "dialog_active", None)) and cs.dialog_active()):
            raise PlayError("疑似仍有戴夫对话 / 选卡界面未就绪，停止（不盲点）：%s"
                            % (st.get("dave_state_name") if isinstance(st, dict) else st))
        return step

    # -- phases ----------------------------------------------------------------------
    def prepare_window(self) -> Dict[str, Any]:
        """Foreground (viewing only) and keep-running guarantee."""
        t0 = self._clock()
        win = self.game.window
        before = win.is_foreground()
        brought: Optional[bool] = None
        if self.foreground and not before:
            try:
                brought = bool(win.bring_to_foreground())
            except Exception as exc:  # noqa: BLE001 -- cosmetic step
                brought = False
                self.notes.append("bring_to_foreground failed: %s" % exc)
        after = win.is_foreground()
        # the game pauses whenever it loses focus (and focus can move at any
        # moment during a long run): keep it ticking with background_running
        # when the patch grant is there, whatever the focus state is now
        state = win.ensure_running(foreground=False)
        if state == "foreground" and not self.dry_run:
            pm = getattr(win, "patches", None)
            if pm is not None and getattr(getattr(win, "safety", None), "allow_patch", False):
                try:
                    pm.enable("background_running")
                    state = "foreground+patched"
                except Exception as exc:  # noqa: BLE001
                    self.notes.append("background_running not enabled: %s" % exc)
        return self._step("window", state, t0, foreground_before=before, foreground_after=after,
                          brought_to_front=brought, background_running=win.background_running())

    def _choose_seeds(self) -> bool:
        """``ui 2`` -> ``ui 3``.  Memory chooser first, click chooser as
        fallback -- except after an error that says a dialog is up
        (``dialog_suspected``): then play stops."""
        picks = list(self.seeds)
        mode = self.chooser_mode
        err: Optional[Exception] = None
        if mode in ("auto", "memory") and self._chooser_factory is None and not getattr(self.game, "remote", False):
            err = PlayError("内存选卡需要 attach(remote=True)")
            if mode == "memory":
                raise err
            self.notes.append("memory chooser needs remote=True; falling back to the click chooser")
        elif mode in ("auto", "memory"):
            try:
                ch = self._memory_chooser()
            except Exception as exc:  # noqa: BLE001 -- unsupported build / grant
                err = exc
                ch = None
            if ch is not None:
                try:
                    ok = ch.select_and_start(picks, wait=self.wait_ui_s)
                except PlayError:
                    raise
                except Exception as exc:  # noqa: BLE001 -- ChooserError / NotSupported
                    if getattr(exc, "dialog_suspected", False):
                        self._step("chooser", "memory failed (dialog suspected): %s" % exc, seeds=picks,
                                   state=self._cutscene_report(self._cutscene) if self._cutscene else None)
                        raise PlayError("选卡时疑似有对话框，停止（不回退点击选卡 / 不盲点）：%s" % exc) from exc
                    if _remote_thread_may_be_inside(exc):
                        self._step("chooser", "memory failed (remote thread may still run): %s" % exc, seeds=picks)
                        raise PlayError("内存选卡远程调用超时 / 线程未结束，停止（不回退点击选卡：注入线程可能仍在选卡界面代码里）：%s"
                                        % exc) from exc
                    if mode == "memory" or not getattr(self.game, "input", False):
                        raise
                    err = exc
                    ok = False
                    self._step("chooser", "memory failed: %s" % exc, seeds=picks)
                else:
                    self._step("chooser", "memory: %s" % ("started" if ok else "not started"),
                               seeds=picks, bank=_safe(ch.bank))
                    if ok:
                        return True
                    raise PlayError("内存选卡后未进入关卡（game_ui=%d）" % self.ui())
            elif mode == "memory":
                raise PlayError("内存选卡不可用：%s" % err)
            self.notes.append("memory chooser unavailable (%s); falling back to the click chooser" % err)
        if not getattr(self.game, "input", False):
            raise PlayError("选卡需要内存选卡（GOTY 1096 + --remote）或窗口输入（attach(input=True)）：%s" % (err or "input 未授权"))
        ch = self.game.chooser
        self._wait_no_cutscene_talk("点击选卡")
        ok = self._click_select_and_start(ch, picks)
        self._step("chooser", "input: %s" % ("started" if ok else "not started"), seeds=picks)
        if not ok:
            raise PlayError("点击选卡后未进入关卡（game_ui=%d）" % self.ui())
        return True

    def _click_select_and_start(self, ch: Any, picks: List[Any]) -> bool:
        """The click chooser's ``select_and_start`` but with
        ``start(retry=False)``: play never lets the chooser close a
        dialog automatically (Escape / clicks).  Falls back to
        ``select_and_start`` for choosers without the step methods."""
        names = ("_require_chooser", "bank_size", "fill_types", "clear_bank", "pick", "bank_types", "start")
        if not all(callable(getattr(ch, n, None)) for n in names):
            return bool(ch.select_and_start(picks, wait=self.wait_ui_s))
        ch._require_chooser("选卡并开始")
        self._wait_no_cutscene_talk("点击选卡")   # checked again right before the first click
        ids = ch.fill_types(picks, ch.bank_size())
        ch.clear_bank()
        ch.pick(ids)
        have = ch.bank_types()
        missing = [pid for pid in ids if pid not in have]
        if missing:
            raise PlayError("选卡后卡槽缺少 %s（现有 %s）：卡未解锁或点击未命中" % (missing, have))
        return bool(ch.start(wait=self.wait_ui_s, retry=False))

    def navigate(self) -> int:
        """Drive the ui to 3 (in a level) from 5 / 1 / 2; returns the ui.
        On ui 2 the cutscene step (:meth:`_skip_cutscene_dialog`) runs
        before any chooser action."""
        t0 = self._clock()
        seen: List[int] = []
        if self.dry_run and self.ui() != UI_LEVEL:
            raise PlayError("dry-run 需要已在关卡中（不会选卡 / 布阵）；现在 game_ui=%d" % self.ui())
        for _ in range(8):
            ui = self._settled_ui()
            seen.append(ui)
            if ui == UI_LEVEL:
                break
            if ui == UI_AWARD:
                new = self.menu.award_continue(wait=self.wait_ui_s)
                self._step("award_continue", new)
                if new is None:
                    raise PlayError("奖励界面继续后 game_ui 仍为 5")
                continue
            if ui == UI_MENU:
                new = self.menu.start_adventure(wait=self.wait_ui_s)
                self._step("start_adventure", new)
                if new is None:
                    raise PlayError("主菜单开始冒险后 game_ui 未变（仍为 1）")
                continue
            if ui == UI_CHOOSER:
                self._skip_cutscene_dialog()
                if self.ui() == UI_LEVEL:
                    continue
                self._choose_seeds()
                if self.wait_ui((UI_LEVEL,)) is None:
                    raise PlayError("选卡后等待进入关卡超时（game_ui=%d）" % self.ui())
                continue
            raise PlayError("play 无法从当前界面继续（game_ui=%d）" % ui)
        self._step("navigate", "ui=%d" % self.ui(), t0, path=seen)
        return self.ui()

    def setup_level(self) -> None:
        t0 = self._clock()
        g = self.game
        if self.place and self.lineup is not None and not self.dry_run:
            res = g.place_lineup(self.lineup, clear=True)
            self._step("place_lineup", str(self.lineup), t0, detail=_safe(lambda: repr(res)))
        else:
            self._step("place_lineup", "skipped", t0)
        if self.slot_types and not self.dry_run:
            out = g.set_seed_slots(self.slot_types)
            self._step("seed_slots", out)

    def _make_collector(self) -> Any:
        if not self.auto_collect or self.dry_run:
            return None
        if self._collector_factory is not None:
            return self._collector_factory(self.game)
        if not getattr(self.game, "remote", False):
            self.notes.append("auto-collect needs remote=True; disabled")
            return None
        from .autocollect import AutoCollector

        g = self.game
        return AutoCollector(g.actions, g._observer, g.profile)

    def _meta(self) -> Dict[str, Any]:
        g = self.game
        meta: Dict[str, Any] = {
            "lineup": None if self.lineup is None else str(self.lineup),
            "track": self.track_label or _track_label(self.track),
            "cob_col": self.cob_col,
            "backend": self.backend,
            "auto_collect": self.auto_collect,
            "version": _safe(lambda: g.version),
            "profile": _safe(lambda: getattr(g.profile, "id", None)),
        }
        try:
            from ..schema.enums import Scene

            b = g.board
            meta["scene"] = Scene(int(b.scene)).code
            meta["waves"] = int(b.total_waves)
            meta["level"] = _safe(lambda: g.level)
        except Exception:  # noqa: BLE001 -- not in a level (dry run on a menu)
            pass
        return meta

    _EXECUTOR_KW = ("tolerance_cs", "late_policy", "lead_cs", "cd_wait_max_cs")

    def _build_executor(self, kw: Dict[str, Any]) -> Any:
        """A :class:`PlannedExecutor` over the live game (what
        ``Game.run_track`` does, but the instance is kept: tail + release).
        ``None`` when the game does not expose the parts (fakes / dry run)."""
        g = self.game
        if self.dry_run or not all(callable(getattr(g, n, None)) for n in ("plan_track", "backend")) \
                or getattr(g, "game_clock", None) is None:
            return None
        if "run_track" in getattr(g, "__dict__", {}):
            return None  # run_track replaced on the instance (custom driver / test double): honour it
        from .executor.scheduler import PlannedExecutor

        ex_kw: Dict[str, Any] = {n: kw.pop(n) for n in self._EXECUTOR_KW if n in kw}
        plan = g.plan_track(self.track, **kw)
        be = g.backend(self.backend)
        ex = PlannedExecutor(g.game_clock, be, plan, collector=self.collector,
                             tail_timeout_s=self.tail_timeout_s, tail_policy=self.tail_policy, **ex_kw)
        if self.on_event is not None:
            ex.on_event(self.on_event)
        return ex

    def execute(self) -> Any:
        t0 = self._clock()
        self.collector = self._make_collector()
        if self.firing == "strict":
            return self._execute_strict(t0)
        kw = dict(self.run_kw)
        if self.cob_col is not None:
            kw["default_cob_col"] = self.cob_col
        self.executor = self._build_executor(kw)
        if self.executor is not None:
            report = self.executor.run()
        else:
            report = self.game.run_track(self.track, backend=self.backend, dry_run=self.dry_run,
                                         on_event=self.on_event, collector=self.collector, **kw)
        self._step("execute", getattr(report, "stop_reason", None), t0)
        self.report = report
        return report

    def _execute_strict(self, t0: float) -> Any:
        """The strict fixed-cycle firing loop (``firing='strict'``): fire every
        pair of cannons every :attr:`cob_cycle` cs, ignoring zombies
        (:func:`_strict_fire_loop`), collecting on every poll.  Wrapped in an
        :class:`ExecutionReport` so the rest of :class:`PlaySession` (tail,
        award, meta, release) works unchanged."""
        from .executor.report import ExecutionReport

        tcol = int(self.cob_col) if self.cob_col is not None else None
        log = _strict_fire_loop(self.game, cycle=self.cob_cycle, target_col=tcol,
                                collector=self.collector, sleep=self._sleep, clock=self._clock,
                                on_event=self.on_event, deadline_s=self.tail_timeout_s,
                                ignore_zombies=self.ignore_zombies)
        rep = ExecutionReport(mode="键控", backend=self.backend, plan_actions=log.count)
        rep.rows = log.to_rows()
        rep.stop_reason = "level complete" if log.stop_reason == "level_complete" else (log.stop_reason or "")
        if self.collector is not None:
            rep.collected = _collector_summary(self.collector)
        self._step("execute", rep.stop_reason, t0, fires=log.count)
        self.report = rep
        return rep

    def _collected(self) -> Tuple[int, int]:
        """``(collected, level_awards)`` from the collector stats (0, 0 without)."""
        st = getattr(self.collector, "stats", None)
        fn = getattr(st, "summary", None)
        try:
            d = dict(fn()) if callable(fn) else {}
        except Exception:  # noqa: BLE001
            d = {}
        return int(d.get("collected", 0) or 0), int(d.get("level_awards", 0) or 0)

    def _collector_tick(self) -> None:
        col = self.collector
        if col is None:
            return
        try:
            col.tick(None)
        except Exception as exc:  # noqa: BLE001 -- one failed call must not end the tail
            self.notes.append("collector tick failed in tail: %s" % exc)

    def finish(self, report: Any) -> Optional[int]:
        """Session tail: keep the collector ticking while ``ui == 3`` (the
        level-end award coin), then on ``ui 5`` call ``award_continue`` once.
        A failed continue (including a remote-call timeout that keeps the
        game frozen) is recorded in ``tail["award_continue"]`` and the
        release in :meth:`run` unfreezes the game; nothing is retried."""
        tail: Dict[str, Any] = dict(getattr(self.executor, "tail", None) or {})
        self.tail = tail
        if not self.finish_level or self.dry_run:
            return None
        t0 = self._clock()
        c0, a0 = self._collected()
        deadline = t0 + self.award_wait_s
        ticks = 0
        ui = self.ui()
        while ui == UI_LEVEL and self._clock() < deadline:
            self._collector_tick()
            ticks += 1
            self._sleep(0.1)
            ui = self.ui()
        c1, a1 = self._collected()
        tail.update({"session_seconds": round(self._clock() - t0, 3), "session_ticks": ticks,
                     "session_collected": c1 - c0, "award_item_collected": (a1 - a0) > 0,
                     "level_awards": a1, "ui_after": ui,
                     "level_ended": ui != UI_LEVEL})
        if ui == UI_LEVEL:
            tail["award_continue"] = "not called (level still running after %.0fs)" % self.award_wait_s
            self._step("tail", "ui=3 (level not complete)", t0, **_tail_brief(tail))
            return ui
        if ui != UI_AWARD:
            tail["award_continue"] = "not needed (ui=%d)" % ui
            self._step("tail", "ui=%d" % ui, t0, **_tail_brief(tail))
            return ui
        self._step("tail", "ui=5", t0, **_tail_brief(tail))
        t1 = self._clock()
        try:
            new = self.menu.award_continue(wait=self.wait_ui_s)
        except Exception as exc:  # noqa: BLE001 -- report only, never retry
            blocked = bool(getattr(exc, "main_loop_blocked", False))
            tail["award_continue"] = "failed: %s" % exc
            tail["award_continue_frozen"] = blocked
            self._step("award_continue", "failed: %s" % exc, t1, main_loop_blocked=blocked)
            return UI_AWARD
        tail["award_continue"] = "ui=%s" % new if new is not None else "timeout (ui still 5)"
        self._step("award_continue", new, t1)
        return new

    def release(self) -> List[str]:
        """Disable every patch play / the executor enabled (``main_loop_block``
        left on by a timed-out remote call, ``background_running``).  Safe
        to call more than once; returns the names disabled now."""
        done: List[str] = []
        ex = self.executor
        if ex is not None and callable(getattr(ex, "release", None)):
            try:
                ex.release(keep=())
            except Exception as exc:  # noqa: BLE001
                self.notes.append("executor.release failed: %s" % exc)
            # the executor also releases at the end of its own run(): report those too
            done.extend(n for n in getattr(ex, "released", ()) if n not in self.released)
        if not self.dry_run:
            pms = []
            for obj in (getattr(self.game, "window", None), self.game):
                pm = getattr(obj, "patches", None) or getattr(obj, "_patches", None)
                if pm is not None and callable(getattr(pm, "disable", None)) and pm not in pms:
                    pms.append(pm)
            for pm in pms:
                for name in list(getattr(pm, "enabled", ()) or ()):
                    try:
                        pm.disable(name)
                        done.append(name)
                    except Exception as exc:  # noqa: BLE001
                        self.notes.append("patch %s not released: %s" % (name, exc))
        self.released.extend(done)
        return done

    def _finish_line(self, report: Any, error: Optional[BaseException] = None) -> str:
        t = self.tail or {}
        col, awards = self._collected()
        parts = ["play finished:" if error is None else "play aborted (%s):" % type(error).__name__,
                 "stop=%s" % getattr(report, "stop_reason", None if error is None else str(error)[:80]),
                 "ui=%s" % _safe(self.ui)]
        if t:
            parts.append("tail=%.1fs+%.1fs" % (float(t.get("seconds", 0) or 0), float(t.get("session_seconds", 0) or 0)))
        parts.append("collected=%d" % col)
        parts.append("award_item=%s" % ("yes" if awards else "no"))
        if "award_continue" in t:
            parts.append("award_continue=%s" % t["award_continue"])
        parts.append("patches_released=%s" % (",".join(self.released) if self.released else "none"))
        return " ".join(parts)

    def run(self) -> Any:
        """All phases; returns the :class:`ExecutionReport`.  Whatever
        happens, :meth:`release` runs and one ``play finished`` line is
        printed (unless ``quiet``)."""
        t0 = self._clock()
        report: Any = None
        error: Optional[BaseException] = None
        try:
            try:
                self.prepare_window()
                ui = self.navigate()
                if ui != UI_LEVEL:
                    raise PlayError("未能进入关卡（game_ui=%d）" % ui)
                self.setup_level()
                report = self.execute()
            except PlayError as exc:
                exc.args = (self.describe_steps(str(exc)),)
                raise
            except Exception as exc:  # noqa: BLE001 -- keep the phase log with the error
                err = PlayError(self.describe_steps("%s: %s" % (type(exc).__name__, exc)))
                err.__cause__ = exc
                raise err
            try:
                self.finish(report)
            finally:
                meta = self._meta()
                meta["steps"] = list(self.steps)
                meta["notes"] = list(self.notes)
                meta["play_seconds"] = round(self._clock() - t0, 3)
                if self.cutscene_state is not None:
                    meta["cutscene"] = self.cutscene_state
                if self.tail:
                    meta["tail"] = dict(self.tail)
                try:
                    report.meta.update(meta)
                except AttributeError:
                    pass
        except BaseException as exc:  # noqa: BLE001 -- re-raised after the release
            error = exc
            raise
        finally:
            self.release()
            if report is not None:
                try:
                    report.meta["patches_released"] = list(self.released)
                    if self.tail:
                        report.meta["tail"] = dict(self.tail)
                except (AttributeError, TypeError):
                    pass
            if not self.quiet:
                print(self._finish_line(report, error), flush=True)
        return report

    def describe_steps(self, head: str = "") -> str:
        """``head`` followed by one ``name=result`` per phase (error messages)."""
        parts = ["%s=%s" % (st.get("name"), st.get("result")) for st in self.steps]
        tail = " | steps: " + "; ".join(parts) if parts else ""
        if self.notes:
            tail += " | notes: " + "; ".join(self.notes)
        return head + tail

    def __repr__(self) -> str:
        return "PlaySession(lineup=%r, backend=%s, steps=%d)" % (self.lineup, self.backend, len(self.steps))


def _track_label(track: Any) -> str:
    """A short name for a track: text as given, a Track's name / rhythm +
    wave summary otherwise."""
    if isinstance(track, str):
        return track
    name = getattr(track, "name", None) or (getattr(track, "meta", None) or {}).get("name")
    if name:
        return str(name)
    waves = getattr(track, "waves", None)
    if waves is not None:
        rhythm = getattr(track, "rhythm", None)
        raws = [getattr(w, "raw", None) or "?" for w in waves]
        label = "|".join(raws)
        if rhythm is not None and getattr(rhythm, "family", None):
            label = "%s%s: %s" % (rhythm.family, getattr(rhythm, "param", "") or "", label)
        return label or type(track).__name__
    return type(track).__name__


def _tail_brief(tail: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("seconds", "reason", "waves", "fired_groups", "missed", "collected", "session_seconds",
            "session_collected", "award_item_collected")
    return {k: tail[k] for k in keys if k in tail}


def _remote_thread_may_be_inside(exc: BaseException) -> bool:
    """True for a remote-call failure after which the injected thread may
    still be executing game code (timeout / ``thread_running``) -- or any
    :class:`~pvztools.control.remote.RemoteCallError` at all: after one the
    main loop was force-released and the screen state is unknown, so no
    clicks may follow."""
    if getattr(exc, "thread_running", False):
        return True
    try:
        from .remote import RemoteCallError, RemoteCallTimeout
    except Exception:  # noqa: BLE001
        return False
    return isinstance(exc, (RemoteCallError, RemoteCallTimeout))


def _safe(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


# ===========================================================================
# Strict fixed-cycle level runner (the ``strict_front`` recipe, promoted from
# an ad-hoc script into the codebase as the default level runner).
# ===========================================================================

#: Scene-default八炮 lineups when :mod:`pvztools.control.lineups_default` is
#: unavailable (each has 8 cob cannons); scene codes DE/NE/PE/FE/RE/ME.
FALLBACK_LINEUPS: Dict[str, str] = {
    "PE": "3C八炮", "DE": "神之八炮", "NE": "美学八炮",
    "FE": "经典八炮", "RE": "方块八炮", "ME": "对称八炮",
}


def default_lineup(scene: str) -> Optional[str]:
    """The scene's default八炮 lineup name.  Delegates to
    :func:`pvztools.control.lineups_default.default_lineup` when that module
    (written alongside this one) is present, else :data:`FALLBACK_LINEUPS`."""
    try:
        from .lineups_default import default_lineup as _dl

        val = _dl(scene)
        if val:
            return val
    except Exception:  # noqa: BLE001 -- module not present yet / different shape
        pass
    return FALLBACK_LINEUPS.get(scene)


def default_target_col(scene: str) -> int:
    """The cob landing column for a scene (``lineups_default.target_col`` when
    available, else 8 -- the 3C八炮 landing column)."""
    try:
        from .lineups_default import target_col as _tc

        val = _tc(scene)
        if val:
            return int(val)
    except Exception:  # noqa: BLE001
        pass
    return 8


def _adventure_level(game: Any) -> Optional[int]:
    try:
        from .levels import adventure_level

        return int(adventure_level(game))
    except Exception:  # noqa: BLE001 -- module not present / not in a level
        try:
            return int(game.level)
        except Exception:  # noqa: BLE001
            return None


def _make_collector(game: Any) -> Any:
    """An :class:`~pvztools.control.autocollect.AutoCollector` over the live
    game (``None`` without remote)."""
    if not getattr(game, "remote", False):
        return None
    from .autocollect import AutoCollector

    return AutoCollector(game.actions, game._observer, game.profile)


def _collector_summary(collector: Any) -> Dict[str, Any]:
    if collector is None:
        return {}
    st = getattr(collector, "stats", None)
    fn = getattr(st, "summary", None)
    try:
        return dict(fn()) if callable(fn) else {}
    except Exception:  # noqa: BLE001
        return {}


@dataclass
class _Fire:
    """One frame-locked pair fire: the planned tick, the true fire ticks read
    back from the game (``FireResult.fired_tick``) and their deltas."""
    k: int
    target: int
    fired: List[Optional[int]]
    deltas: List[int]
    wave: int = 0


@dataclass
class FireLog:
    """What :func:`_strict_fire_loop` did: the pairs it drove and every fire."""
    scene: str = ""
    target_col: int = 8
    pairs: List[Any] = field(default_factory=list)
    fires: List[_Fire] = field(default_factory=list)
    stop_reason: str = ""

    @property
    def count(self) -> int:
        return len(self.fires)

    @property
    def deltas(self) -> List[int]:
        out: List[int] = []
        for f in self.fires:
            out.extend(f.deltas)
        return out

    @property
    def mean_abs_delta(self) -> Optional[float]:
        ds = [abs(int(d)) for d in self.deltas]
        return (sum(ds) / len(ds)) if ds else None

    def to_rows(self) -> List[Any]:
        from .executor.report import ActionRow

        rows: List[Any] = []
        for f in self.fires:
            fired = f.fired[0] if f.fired else None
            d = f.deltas[0] if f.deltas else None
            rows.append(ActionRow(wave=int(f.wave), symbol="P", kind="cob", planned_cs=int(f.target),
                                  observed_cs=fired, fired_cs=fired, delta=d, status="ok"))
        return rows


@dataclass
class LevelReport:
    """One level played by :func:`play_one_level` (see :mod:`pvztools.control.campaign`).

    ``result`` is ``"level_complete"`` on success, else the stop reason
    (``"not_in_level"``, ``"place_failed: ..."``, ``"timeout"``, ``skipped:
    ...`` from the campaign, ...).
    """
    scene: str = ""
    lineup: Optional[str] = None
    level: Optional[int] = None
    waves: int = 0
    fires: int = 0
    mean_abs_delta: Optional[float] = None
    collected: int = 0
    sun: int = 0
    result: str = ""
    placed_lineup: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result == "level_complete"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scene": self.scene, "lineup": self.lineup, "level": self.level, "waves": self.waves,
            "fires": self.fires, "mean_abs_delta": self.mean_abs_delta, "collected": self.collected,
            "sun": self.sun, "result": self.result, "placed_lineup": self.placed_lineup,
            "detail": dict(self.detail),
        }

    def to_row(self) -> str:
        """One Markdown table row (see :meth:`CampaignReport.to_markdown`)."""
        mad = "-" if self.mean_abs_delta is None else "%.1f" % self.mean_abs_delta
        return "| %s | %s | %s | %s | %d | %s | %d | %s |" % (
            "-" if self.level is None else self.level, self.scene, self.lineup or "-", self.waves,
            self.fires, mad, self.collected, self.result)


def _strict_fire_loop(game: Any, *, cycle: float, target_col: Optional[int] = None,
                      collector: Any = None, sleep: Optional[Callable[[float], None]] = None,
                      clock: Optional[Callable[[], float]] = None, on_event: Any = None,
                      deadline_s: float = 600.0, ignore_zombies: bool = True) -> FireLog:
    """Strict CD-cycle firing (the ``strict_front`` recipe): once the first
    pair is ready, fire one pair every ``cycle`` cs of game clock, rotating the
    pairs, **ignoring waves / zombies**.  Cannons are paired from the live
    board (symmetric八炮 lineups: chunk by column then row); each fire is
    frame-locked (``fire_cobs(..., at_tick=target)``) so the true fire tick is
    read back and the delta recorded.  Auto-collects on every poll.  Returns
    when ``game.ui`` leaves 3 (or the board reports ``level_complete``)."""
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    from ..schema.enums import Scene

    obs = game._observer

    def ui() -> int:
        return int(game.ui)

    def board() -> Any:
        return obs.board()

    b = board()
    scene = Scene(int(b.scene)).code
    log = FireLog(scene=scene)
    tcol = int(target_col) if target_col is not None else default_target_col(scene)
    log.target_col = tcol
    six_rows = Scene.from_code(scene).rows == 6
    bottom_row = 5 if six_rows else 4

    cl = sorted([(c.row, c.col) for c in game.cobs], key=lambda rc: (rc[1], rc[0]))
    n = min(8, len(cl))
    pairs = [(cl[i], cl[i + 1]) for i in range(0, n - 1, 2)]
    log.pairs = pairs
    if not pairs:
        log.stop_reason = "no cannons"
        return log
    targets = [((2, tcol), (bottom_row, tcol))] * len(pairs)

    def cobs_by_cell() -> Dict[Tuple[int, int], Any]:
        return {(c.row, c.col): c for c in game.cobs}

    def pair_ready(k: int) -> bool:
        cb = cobs_by_cell()
        return all((rc in cb) and cb[rc].ready for rc in pairs[k])

    def poll() -> None:
        if collector is not None:
            try:
                collector.tick()
            except Exception:  # noqa: BLE001 -- one failed collect must not end the loop
                pass

    t_start = clock()
    # T0 = when pair 0 becomes ready
    while not pair_ready(0):
        poll()
        sleep(0.02)
        if ui() != UI_LEVEL:
            log.stop_reason = "level ended before first fire"
            return log
        if clock() - t_start > deadline_s:
            log.stop_reason = "timeout waiting for first pair"
            return log
    T0 = int(board().clock) + 5
    k = 0
    while True:
        poll()
        if ui() != UI_LEVEL:
            log.stop_reason = "level_complete"
            break
        b = board()
        if getattr(b, "level_complete", False):
            log.stop_reason = "level_complete"
            break
        if clock() - t_start > deadline_s:
            log.stop_reason = "timeout"
            break
        target = T0 + k * cycle
        kk = k % len(pairs)
        if not pair_ready(kk):
            sleep(0.01)
            continue
        if int(b.clock) < target - 30:
            sleep(0.004)
            continue
        pair = pairs[kk]
        tg = targets[kk]
        try:
            res = game.actions.fire_cobs([(pair[0], tg[0]), (pair[1], tg[1])], at_tick=int(target))
        except Exception as exc:  # noqa: BLE001 -- record and stop, never crash the campaign
            log.stop_reason = "fire error: %s" % exc
            if on_event is not None:
                on_event({"event": "error", "error": str(exc)})
            break
        fired = [getattr(r, "fired_tick", None) for r in res]
        deltas = [int(f - target) for f in fired if f is not None]
        log.fires.append(_Fire(k=k, target=int(target), fired=fired, deltas=deltas,
                               wave=int(getattr(b, "wave", 0) or 0)))
        if on_event is not None:
            on_event({"event": "fire", "k": k, "pair": pair, "target": int(target),
                      "fired": fired, "deltas": deltas, "wave": int(getattr(b, "wave", 0) or 0)})
        k += 1
    return log


def play_one_level(game: Any, *, lineup: Any = None, cycle: float = 869,
                   target_col: Optional[int] = None, collect: bool = True,
                   foreground: bool = True, on_event: Any = None,
                   sleep: Optional[Callable[[float], None]] = None,
                   clock: Optional[Callable[[], float]] = None, place: bool = True,
                   collector_factory: Optional[Callable[[Any], Any]] = None,
                   wait_ui_s: float = 600.0, quiet: bool = True) -> LevelReport:
    """Play one level with the strict fixed-cycle cob loop -- the default
    level runner used by :class:`pvztools.control.campaign.Campaign`.

    Steps (the ``strict_front`` recipe, DESIGN §6.3):

    1. **safe-foreground** (viewing only, fullscreen-safe):
       :meth:`GameWindow.bring_to_foreground` (``SW_RESTORE`` +
       ``SetForegroundWindow``);
    2. wait for ``game.ui == 3``;
    3. place the scene-default八炮 lineup (``lineup`` overrides
       :func:`default_lineup`) when the board has fewer than 8 cob cannons;
    4. pair cannons from the live board and fire each pair frame-locked at
       ``T0 + k*cycle`` (:func:`_strict_fire_loop`), **ignoring zombies**;
    5. auto-collect on every poll;
    6. return a :class:`LevelReport` when ``game.ui`` leaves 3
       (``result == "level_complete"``).

    Menu navigation and the award screen are the campaign's job, not this
    function's.
    """
    _sleep = sleep or time.sleep
    _clock = clock or time.monotonic
    from ..schema.enums import Scene

    if foreground:
        try:
            game.window.bring_to_foreground()
        except Exception:  # noqa: BLE001 -- cosmetic
            pass

    deadline = _clock() + wait_ui_s
    while int(game.ui) != UI_LEVEL and _clock() < deadline:
        _sleep(0.1)
    if int(game.ui) != UI_LEVEL:
        return LevelReport(result="not_in_level")

    obs = game._observer
    b = obs.board()
    scene = Scene(int(b.scene)).code
    level = _adventure_level(game)
    waves = int(getattr(b, "total_waves", 0) or 0)
    name = lineup if lineup is not None else default_lineup(scene)

    placed = False
    try:
        ncobs = len(game.cobs)
    except Exception:  # noqa: BLE001
        ncobs = 0
    if place and ncobs < 8 and name is not None:
        try:
            game.place_lineup(name, clear=True)
            placed = True
        except Exception as exc:  # noqa: BLE001
            return LevelReport(scene=scene, lineup=name, level=level, waves=waves,
                               result="place_failed: %s" % exc)

    tcol = int(target_col) if target_col is not None else default_target_col(scene)
    collector = None
    if collect:
        collector = collector_factory(game) if collector_factory is not None else _make_collector(game)

    log = _strict_fire_loop(game, cycle=cycle, target_col=tcol, collector=collector,
                            sleep=_sleep, clock=_clock, on_event=on_event, deadline_s=wait_ui_s)
    coll = _collector_summary(collector)
    result = "level_complete" if log.stop_reason == "level_complete" else (log.stop_reason or "incomplete")
    return LevelReport(scene=scene, lineup=name, level=level, waves=waves, fires=log.count,
                       mean_abs_delta=log.mean_abs_delta, collected=int(coll.get("collected", 0) or 0),
                       sun=int(coll.get("sun", 0) or 0), result=result, placed_lineup=placed,
                       detail={"pairs": log.pairs, "target_col": tcol, "collected": coll,
                               "stop_reason": log.stop_reason})
