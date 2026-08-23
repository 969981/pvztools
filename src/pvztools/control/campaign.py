"""Play a run of adventure levels back to back (the *campaign* orchestrator).

:class:`Campaign` loops the proven single-level pieces -- the main menu
(:class:`~pvztools.control.menu.MainMenu`), the intro cutscene
(:class:`~pvztools.control.cutscene.Cutscene`), the memory seed chooser
(:class:`~pvztools.control.chooser_mem.MemoryChooser`) and the strict
fixed-cycle level runner (:func:`pvztools.control.play.play_one_level`) --
so one call drives several levels:

    Campaign(game, count=3).run()          # play the next 3 adventure levels
    Campaign(game, auto=True).run()         # play until a non-adventure level

Each iteration reads ``game.ui`` and advances the state machine:

* ``ui 5`` (award screen)  -> :meth:`MainMenu.award_continue`;
* ``ui 1`` (main menu)     -> :meth:`MainMenu.start_adventure`;
* ``ui 2`` (seed chooser)  -> :func:`classify` the level (informational);
  the campaign **tries** every plant-defense level -- conveyor levels and the
  final boss included, because plants are placed with a direct ``PutPlant``
  call that bypasses the seed bank -- and only **stops** on a level where
  planting is meaningless (I,Zombie / Vasebreaker) or the classifier reports
  ``plantable=False``; otherwise skip the Crazy Dave talk and memory-choose +
  start;
* ``ui 3`` (in a level)    -> :func:`classify`; stop only on a non-plantable
  level, else :func:`play_one_level` with the scene-default
  八炮 lineup (``lineup=`` overrides it for every level), then collect the
  level-end award and continue past the award screen.

Every level becomes a :class:`~pvztools.control.play.LevelReport`; the run
returns a :class:`CampaignReport` that aggregates them
(:meth:`CampaignReport.to_markdown` / :meth:`CampaignReport.to_json`).  A
``finally`` always releases every code patch that was left enabled (the
single controller mutex itself is released by :meth:`Game.close`).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence

from .play import (DEFAULT_SEEDS, LevelReport, UI_AWARD, UI_CHOOSER, UI_LEVEL, UI_MENU,
                   default_lineup, play_one_level)

__all__ = ["Campaign", "CampaignReport", "classify"]


# --------------------------------------------------------------------------- classify
def _fallback_classify(game: Any) -> SimpleNamespace:
    """Best-effort classification when :mod:`pvztools.control.levels` is not
    present: adventure == ``game_mode 0``; scene / level from the board when
    one exists."""
    from ..schema.enums import Scene

    mode = int(getattr(game, "mode", 0) or 0)
    scene: Optional[str] = None
    level: Optional[int] = None
    try:
        b = game.board
        scene = Scene(int(b.scene)).code
        level = int(getattr(b, "level", 0) or 0)
    except Exception:  # noqa: BLE001 -- no board (menu / award): unknown
        pass
    is_adv = mode == 0
    return SimpleNamespace(scene=scene, level=level, mode=mode, is_adventure=is_adv,
                           plantable=is_adv, kind="adventure" if is_adv else "minigame",
                           reason="" if is_adv else "非冒险模式 (game_mode=%d)" % mode)


def classify(game: Any) -> Any:
    """Classify the current level.  Delegates to
    :func:`pvztools.control.levels.classify` when that (concurrently written)
    module is available, else :func:`_fallback_classify`.  The result carries
    at least ``scene`` (code), ``level``, ``is_adventure``, ``plantable``,
    ``kind`` and ``reason``."""
    try:
        from .levels import classify as _classify

        return _classify(game)
    except Exception:  # noqa: BLE001 -- module not present yet
        return _fallback_classify(game)


def _is_adventure(cls: Any) -> bool:
    """Whether the classification is a mode-0 *adventure* level.

    The fallback classifier (:func:`_fallback_classify`) sets an explicit
    ``is_adventure`` flag; the real :func:`pvztools.control.levels.classify`
    (a :class:`~pvztools.control.levels.LevelInfo`) does not -- it carries the
    raw ``game_mode`` int as ``mode`` instead, and adventure is ``mode == 0``.
    Prefer the explicit flag when present, else derive it from ``mode``; an
    unreadable mode (``None``) passes (unknowns are not blocked here)."""
    flag = getattr(cls, "is_adventure", None)
    if flag is not None:
        return bool(flag)
    mode = getattr(cls, "mode", None)
    if mode is None:
        return True
    try:
        return int(mode) == 0
    except (TypeError, ValueError):
        return True


def _kind_name(cls: Any) -> Any:
    """The ``kind`` as a string: the real classifier's ``kind`` is a
    :class:`~pvztools.control.levels.LevelKind` enum (whose ``value`` is the
    community string, e.g. ``"conveyor"``); the fallback stores a plain
    string.  ``None`` stays ``None``."""
    kind = getattr(cls, "kind", None)
    return getattr(kind, "value", kind)


#: Level kinds where placing plants + firing cobs is meaningless, so the
#: campaign must not attempt them (you are the zombie side, or the goal is
#: smashing pots rather than growing plants).  Everything else -- including
#: conveyor-belt levels and the final boss -- is TRIED: we place the lineup
#: with PutPlant directly (bypassing the seed bank), so a conveyor bank does
#: not matter, and the user verified conveyor levels hard-play fine.
_NON_PLANTABLE_KINDS = frozenset({"i_zombie", "vasebreaker"})


def _plantable(cls: Any) -> bool:
    """TRY-THEN-STOP gating: attempt to play (place lineup + fire) on ANY
    level except the ones where planting is inherently impossible.

    We place plants via a direct ``PutPlant`` remote call, not through the
    seed bank, so the bank's shape (conveyor / normal) is irrelevant and
    conveyor levels play normally (user-verified 2026-08-22).  The boss
    level is attempted too (it may or may not fall to the death-flow -- the
    report says which).  Only ``i_zombie`` / ``vasebreaker`` -- where there
    is no plant-growing at all -- are refused.  An explicit
    ``plantable=False`` from the classifier is still honoured."""
    if getattr(cls, "plantable", True) is False:
        return False
    kind = _kind_name(cls)
    if kind is not None and str(kind).lower() in _NON_PLANTABLE_KINDS:
        return False
    return True


def _skip_info(cls: Any, ui: int) -> Dict[str, Any]:
    # the real LevelInfo names the field ``adventure_level``; the fallback
    # classifier uses ``level`` -- accept either so the skip record is filled
    level = getattr(cls, "adventure_level", None)
    if level is None:
        level = getattr(cls, "level", None)
    return {
        "scene": getattr(cls, "scene", None),
        "level": level,
        "kind": _kind_name(cls),
        "reason": getattr(cls, "reason", None) or "不是可布阵的冒险关卡",
        "ui": ui,
    }


# --------------------------------------------------------------------------- report
@dataclass
class CampaignReport:
    """The outcome of a :class:`Campaign` run: the levels played, why it
    stopped, and a skip record when a non-adventure level ended the run."""
    target: int = 1
    auto: bool = False
    levels: List[LevelReport] = field(default_factory=list)
    skipped: Optional[Dict[str, Any]] = None
    stop: str = ""
    notes: List[str] = field(default_factory=list)
    released: List[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.levels)

    @property
    def played(self) -> int:
        return len(self.levels)

    @property
    def total_fires(self) -> int:
        return sum(int(l.fires) for l in self.levels)

    @property
    def total_collected(self) -> int:
        return sum(int(l.collected) for l in self.levels)

    @property
    def mean_abs_delta(self) -> Optional[float]:
        vals = [l.mean_abs_delta for l in self.levels if l.mean_abs_delta is not None]
        return (sum(vals) / len(vals)) if vals else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target, "auto": self.auto, "played": self.played,
            "total_fires": self.total_fires, "total_collected": self.total_collected,
            "mean_abs_delta": self.mean_abs_delta, "stop": self.stop, "skipped": self.skipped,
            "levels": [l.to_dict() for l in self.levels], "notes": list(self.notes),
            "released": list(self.released),
        }

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def to_markdown(self) -> str:
        """Chinese headings with English labels: header, per-level table,
        skip record."""
        mad = "-" if self.mean_abs_delta is None else "%.1f" % self.mean_abs_delta
        lines: List[str] = []
        lines.append("# 连关报告 (Campaign report)")
        lines.append("")
        lines.append("## 概要 (Header)")
        lines.append("")
        lines.append("| 项目 (item) | 值 (value) |")
        lines.append("|---|---|")
        lines.append("| 目标 (target) | %s |" % ("自动 (auto)" if self.auto else self.target))
        lines.append("| 已打关卡 (levels played) | %d |" % self.played)
        lines.append("| 总发炮组 (total fire pairs) | %d |" % self.total_fires)
        lines.append("| 总收集 (total collected) | %d |" % self.total_collected)
        lines.append("| 平均 |Δ| (mean abs delta) | %s cs |" % mad)
        lines.append("| 结束原因 (stop reason) | %s |" % (self.stop or "-"))
        lines.append("")
        lines.append("## 关卡 (Levels)")
        lines.append("")
        lines.append("| 关卡 (level) | 场景 (scene) | 阵型 (lineup) | 波数 (waves) | 发炮组 (fires) | "
                     "平均|Δ| (mean) | 收集 (collected) | 结果 (result) |")
        lines.append("|---:|---|---|---:|---:|---:|---:|---|")
        if self.levels:
            for l in self.levels:
                lines.append(l.to_row())
        else:
            lines.append("| - | - | - | - | 0 | - | 0 | (无 none) |")
        lines.append("")
        lines.append("## 跳过 (Skipped)")
        lines.append("")
        if self.skipped:
            sk = self.skipped
            lines.append("- 场景 / 关卡 (scene / level): %s / %s" % (sk.get("scene"), sk.get("level")))
            lines.append("- 类型 (kind): %s" % sk.get("kind"))
            lines.append("- 原因 (reason): %s" % sk.get("reason"))
        else:
            lines.append("- 无 (none)")
        if self.notes:
            lines.append("")
            lines.append("## 备注 (Notes)")
            lines.append("")
            lines.extend("- %s" % n for n in self.notes)
        lines.append("")
        return "\n".join(lines)


# --------------------------------------------------------------------------- campaign
class Campaign:
    """Drive the game through several adventure levels (module doc).

    ``count`` levels are played (``auto=True`` ignores it and plays until a
    non-adventure level / no progress).  ``lineup`` overrides the scene
    default for **every** level.  ``on_event`` receives ``{"event": ...}``
    dicts (``"level"`` after each level, ``"fire"`` from the firing loop,
    ``"skip"`` / ``"stop"``).

    Test hooks (each receives the game): ``play_level`` replaces
    :func:`play_one_level`; ``menu_factory`` / ``chooser_factory`` /
    ``cutscene_factory`` / ``collector_factory`` replace the default
    constructors; ``classify`` replaces :func:`classify`; ``sleep`` / ``clock``
    the wall clock.
    """

    def __init__(self, game: Any, *, count: int = 1, auto: bool = False, on_event: Any = None,
                 lineup: Any = None, cycle: float = 869, seeds: Optional[Sequence[Any]] = None,
                 foreground: bool = True, collect: bool = True, quiet: bool = False,
                 wait_ui_s: float = 20.0, settle_s: float = 0.5, max_iters: Optional[int] = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 classify: Optional[Callable[[Any], Any]] = None,
                 play_level: Optional[Callable[..., LevelReport]] = None,
                 menu_factory: Optional[Callable[[Any], Any]] = None,
                 chooser_factory: Optional[Callable[[Any], Any]] = None,
                 cutscene_factory: Optional[Callable[[Any], Any]] = None,
                 collector_factory: Optional[Callable[[Any], Any]] = None) -> None:
        self.game = game
        self.count = max(1, int(count))
        self.auto = bool(auto)
        self.on_event = on_event
        self.lineup = lineup
        self.cycle = float(cycle)
        self.seeds = list(seeds) if seeds is not None else list(DEFAULT_SEEDS)
        self.foreground = bool(foreground)
        self.collect = bool(collect)
        self.quiet = bool(quiet)
        self.wait_ui_s = float(wait_ui_s)
        self.settle_s = float(settle_s)
        self.max_iters = max_iters
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._classify_fn = classify
        self._play_level_fn = play_level or play_one_level
        self._menu_factory = menu_factory
        self._chooser_factory = chooser_factory
        self._cutscene_factory = cutscene_factory
        self._collector_factory = collector_factory
        self._menu: Any = None
        self.report = CampaignReport(target=self.count, auto=self.auto)

    # -- helpers ---------------------------------------------------------------------
    def _note(self, msg: str) -> None:
        self.report.notes.append(msg)

    def _emit(self, event: Dict[str, Any]) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 -- a bad listener must not break the run
                pass

    def _classify(self) -> Any:
        if self._classify_fn is not None:
            return self._classify_fn(self.game)
        return classify(self.game)

    def _scene_code(self) -> Optional[str]:
        from ..schema.enums import Scene

        try:
            return Scene(int(self.game.board.scene)).code
        except Exception:  # noqa: BLE001
            return None

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

    def _chooser(self) -> Any:
        if self._chooser_factory is not None:
            return self._chooser_factory(self.game)
        from .chooser_mem import MemoryChooser

        g = self.game
        return MemoryChooser(g.session, g.profile, g._executor, g._observer, window=g.window)

    def _cutscene(self) -> Any:
        if self._cutscene_factory is not None:
            try:
                return self._cutscene_factory(self.game)
            except Exception:  # noqa: BLE001
                return None
        if not getattr(self.game, "remote", False):
            return None
        try:
            from .cutscene import Cutscene

            g = self.game
            return Cutscene(g.session, g.profile, g._executor, g._observer, window=g.window)
        except Exception:  # noqa: BLE001 -- unsupported build
            return None

    def _ui(self) -> int:
        return int(self.game.ui)

    def _wait_leave(self, ui: int, wait: Optional[float] = None) -> int:
        deadline = self._clock() + (self.wait_ui_s if wait is None else wait)
        while self._ui() == ui and self._clock() < deadline:
            self._sleep(0.1)
        return self._ui()

    def _wait_next_state(self) -> int:
        """After a level: wait for the ui to settle on a map / menu / chooser
        (anything but the level / award transition)."""
        deadline = self._clock() + self.wait_ui_s
        while self._clock() < deadline:
            ui = self._ui()
            if ui in (UI_MENU, UI_CHOOSER, UI_LEVEL):
                return ui
            self._sleep(0.1)
        return self._ui()

    # -- phases ----------------------------------------------------------------------
    def _prepare_window(self) -> None:
        if self.foreground:
            try:
                self.game.window.bring_to_foreground()
            except Exception as exc:  # noqa: BLE001 -- cosmetic
                self._note("bring_to_foreground: %s" % exc)

    def _choose(self) -> bool:
        """``ui 2`` -> ``ui 3``: skip the Crazy Dave talk, then memory-choose
        and start."""
        cs = self._cutscene()
        if cs is not None:
            try:
                cs.state()
                if cs.dialog_active():
                    cs.skip_dialog()
                cs.wait_ready_for_chooser(self.wait_ui_s)
            except Exception as exc:  # noqa: BLE001 -- best effort; the chooser re-checks
                self._note("cutscene: %s" % exc)
        ch = self._chooser()
        return bool(ch.select_and_start(self.seeds, wait=self.wait_ui_s))

    def _play_level(self, scene: Optional[str]) -> LevelReport:
        name = self.lineup if self.lineup is not None else default_lineup(scene or "")
        lr = self._play_level_fn(self.game, lineup=name, cycle=self.cycle, collect=self.collect,
                                 foreground=self.foreground, on_event=self.on_event,
                                 sleep=self._sleep, clock=self._clock,
                                 collector_factory=self._collector_factory)
        return lr

    def _post_level(self) -> None:
        """Collect the level-end award (the strict loop already grabs it while
        ``ui == 3``), then continue past the award screen."""
        if self._ui() == UI_AWARD:
            try:
                self.menu.award_continue(wait=self.wait_ui_s)
            except Exception as exc:  # noqa: BLE001 -- report, never retry
                self._note("award_continue: %s" % exc)

    def _release(self) -> List[str]:
        done: List[str] = []
        for obj in (getattr(self.game, "window", None), self.game):
            pm = getattr(obj, "patches", None) or getattr(obj, "_patches", None)
            if pm is not None and callable(getattr(pm, "disable", None)):
                for name in list(getattr(pm, "enabled", ()) or ()):
                    try:
                        pm.disable(name)
                        done.append(name)
                    except Exception as exc:  # noqa: BLE001
                        self._note("patch %s not released: %s" % (name, exc))
        self.report.released = done
        return done

    # -- run -------------------------------------------------------------------------
    def run(self) -> CampaignReport:
        r = self.report
        self._prepare_window()
        it = 0
        max_iters = int(self.max_iters) if self.max_iters is not None else self.count * 12 + 40
        try:
            while True:
                if not self.auto and len(r.levels) >= self.count:
                    r.stop = "count reached"
                    break
                it += 1
                if it > max_iters:
                    r.stop = "max iterations (%d)" % max_iters
                    break
                ui = self._ui()
                if ui == UI_AWARD:
                    try:
                        self.menu.award_continue(wait=self.wait_ui_s)
                    except Exception as exc:  # noqa: BLE001
                        self._note("award_continue: %s" % exc)
                        r.stop = "award_continue failed"
                        break
                    self._wait_leave(UI_AWARD)
                    continue
                if ui == UI_MENU:
                    new = self.menu.start_adventure(wait=self.wait_ui_s)
                    if new is None:
                        r.stop = "start_adventure failed"
                        break
                    continue
                if ui in (UI_CHOOSER, UI_LEVEL):
                    cls = self._classify()
                    if not _plantable(cls):
                        r.skipped = _skip_info(cls, ui)
                        r.stop = "非冒险 / 不可布阵关卡 (non-adventure level)"
                        self._emit({"event": "skip", **r.skipped})
                        break
                    scene = getattr(cls, "scene", None) or self._scene_code()
                    if ui == UI_CHOOSER:
                        ok = self._choose()
                        if not ok or self._ui() != UI_LEVEL:
                            r.stop = "选卡后未进入关卡 (chooser did not reach a level, ui=%d)" % self._ui()
                            break
                        continue
                    # ui == UI_LEVEL: play it
                    lr = self._play_level(scene)
                    r.levels.append(lr)
                    self._emit({"event": "level", "report": lr.to_dict()})
                    if not lr.ok:
                        r.stop = "关卡未完成 (level result: %s)" % lr.result
                        break
                    self._post_level()
                    self._wait_next_state()
                    continue
                r.skipped = {"reason": "未知界面 (unknown ui %d)" % ui, "ui": ui}
                r.stop = "未知界面 (unknown ui %d)" % ui
                break
            if not r.stop:
                r.stop = "count reached" if len(r.levels) >= self.count else "done"
        finally:
            self._release()
            self._emit({"event": "stop", "reason": r.stop, "played": r.played})
            if not self.quiet:
                print(self._finish_line(), flush=True)
        return r

    def _finish_line(self) -> str:
        r = self.report
        mad = "-" if r.mean_abs_delta is None else "%.1f" % r.mean_abs_delta
        return ("campaign finished: played=%d/%s stop=%s fires=%d collected=%d mean|Δ|=%s cs "
                "patches_released=%s"
                % (r.played, "auto" if self.auto else self.count, r.stop, r.total_fires,
                   r.total_collected, mad, ",".join(r.released) if r.released else "none"))

    def __repr__(self) -> str:
        return "Campaign(count=%s, auto=%s, played=%d)" % (self.count, self.auto, self.report.played)
