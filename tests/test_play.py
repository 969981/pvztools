"""PlaySession orchestrator (control/play.py), the Markdown report and the
``play`` CLI flags."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from pvztools import game as game_mod
from pvztools.cli.__main__ import EXIT_OK, main
from pvztools.control.executor.report import STATUS_CD_WAIT, ActionRow, ExecutionReport, status_label
from pvztools.control.play import DEFAULT_SEEDS, PlayError, PlaySession
from pvztools.game import Game
from pvztools.schema.track.expand import Plan
from test_exec_v2_integration import FakeClock, LockBackend, cob_action, run_plan
from test_game_api import make_session


# --- fakes ---------------------------------------------------------------------
class Wall:
    def __init__(self) -> None:
        self.t = 0.0

    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeWindow:
    def __init__(self, foreground: bool = False, can_focus: bool = True, patch: Optional[bool] = False) -> None:
        self.fg = foreground
        self.can_focus = can_focus
        self.patch = patch
        self.calls: List[str] = []

    def is_foreground(self) -> bool:
        return self.fg

    def bring_to_foreground(self, settle: float = 0.3) -> bool:
        self.calls.append("front")
        if self.can_focus:
            self.fg = True
        return self.fg

    def background_running(self) -> Optional[bool]:
        return self.patch

    def ensure_running(self, foreground: bool = False) -> str:
        self.calls.append("ensure(%s)" % foreground)
        if self.fg:
            return "foreground"
        self.patch = True
        return "patched"


class FakeMenu:
    """ui 1 -> 2 on start_adventure, ui 5 -> 1 on award_continue (scripted)."""

    def __init__(self, game: "FakeGame", fail: bool = False) -> None:
        self.game = game
        self.fail = fail
        self.calls: List[str] = []

    def start_adventure(self, wait: float = 10.0):
        self.calls.append("adventure")
        if self.fail:
            return None
        self.game.ui = 2
        return 2

    def award_continue(self, wait: float = 10.0):
        self.calls.append("award")
        if self.fail:
            return None
        self.game.ui = 1
        return 1


class FakeMemChooser:
    def __init__(self, game: "FakeGame", fail: Optional[Exception] = None) -> None:
        self.game = game
        self.fail = fail
        self.picks: List[Any] = []

    def select_and_start(self, types, wait: float = 5.0) -> bool:
        if self.fail is not None:
            raise self.fail
        self.picks = list(types)
        self.game.ui = 3
        return True

    def bank(self):
        return list(self.picks)


class FakeClickChooser:
    def __init__(self, game: "FakeGame") -> None:
        self.game = game
        self.picks: List[Any] = []

    def select_and_start(self, types, wait: float = 5.0) -> bool:
        self.picks = list(types)
        self.game.ui = 3
        return True


class FakeGame:
    """Only what PlaySession touches."""

    def __init__(self, ui: int = 1, *, remote: bool = True, input: bool = False, window: Optional[FakeWindow] = None,
                 complete_after_run: bool = True) -> None:
        self.ui = ui
        self.remote = remote
        self.input = input
        self.window = window or FakeWindow()
        self.version = "1.2.0.1096"
        self.profile = SimpleNamespace(id="goty_1_2_0_1096_en")
        self.session = object()
        self._executor = object()
        self._observer = object()
        self.level = 7
        self.board = SimpleNamespace(scene=2, total_waves=20)
        self.log: List[Any] = []
        self.chooser = FakeClickChooser(self)
        self.complete_after_run = complete_after_run
        self.run_kw: Dict[str, Any] = {}

    def place_lineup(self, name, clear=True):
        self.log.append(("place", name, clear))
        return SimpleNamespace(calls=3)

    def set_seed_slots(self, types):
        self.log.append(("slots", list(types)))
        return [(i, t) for i, t in enumerate(types)]

    def run_track(self, track, **kw):
        self.log.append(("run", track))
        self.run_kw = kw
        rep = ExecutionReport(mode="修改器", backend=kw.get("backend", "?"), dry_run=kw.get("dry_run", False),
                              plan_actions=1)
        rep.rows = [ActionRow(1, "P", "cob", 300, observed_cs=300, delta=0, status="ok", fired_cs=300, call_ms=24.0)]
        rep.stop_reason = "plan exhausted"
        if kw.get("collector") is not None:
            rep.collected = kw["collector"].stats.summary()
        if self.complete_after_run:
            self.ui = 5
        return rep


class FakeCollector:
    def __init__(self) -> None:
        self.stats = SimpleNamespace(summary=lambda: {"collected": 3, "calls": 1, "sun": 75, "money": 0,
                                                      "by_type": {"SUN": 3}, "errors": 0})


def session(game: FakeGame, **kw) -> PlaySession:
    wall = Wall()
    kw.setdefault("menu_factory", lambda g: FakeMenu(g))
    kw.setdefault("chooser_factory", lambda g: FakeMemChooser(g))
    kw.setdefault("collector_factory", lambda g: FakeCollector())
    kw.setdefault("settle_s", 0)
    kw.setdefault("wait_ui_s", 2.0)
    kw.setdefault("award_wait_s", 3.0)
    return PlaySession(game, sleep=wall.sleep, clock=wall.clock, **kw)


# --- orchestration -------------------------------------------------------------
def test_full_path_from_main_menu():
    g = FakeGame(ui=1, window=FakeWindow(foreground=False))
    ps = session(g, lineup="3C八炮", track="P4: PP|PP", cob_col=8, tolerance_cs=3)
    rep = ps.run()
    names = [s["name"] for s in ps.steps]
    assert names == ["window", "start_adventure", "chooser", "navigate", "place_lineup", "execute", "tail",
                     "award_continue"]
    assert ps.steps[0]["foreground_before"] is False and ps.steps[0]["foreground_after"] is True
    assert ps.steps[0]["brought_to_front"] is True and g.window.calls == ["front", "ensure(False)"]
    assert ps.steps[1]["result"] == 2 and ps.steps[2]["seeds"] == list(DEFAULT_SEEDS)
    assert ps.steps[3]["path"] == [1, 2, 3]
    assert g.log[0] == ("place", "3C八炮", True) and g.log[1] == ("run", "P4: PP|PP")
    assert g.run_kw["backend"] == "remote" and g.run_kw["default_cob_col"] == 8.0
    assert g.run_kw["tolerance_cs"] == 3 and isinstance(g.run_kw["collector"], FakeCollector)
    assert g.ui == 1  # award screen dismissed
    m = rep.meta
    assert m["lineup"] == "3C八炮" and m["track"] == "P4: PP|PP" and m["scene"] == "PE" and m["waves"] == 20
    assert m["level"] == 7 and m["version"] == "1.2.0.1096" and m["profile"] == "goty_1_2_0_1096_en"
    assert m["steps"][-1]["name"] == "award_continue" and rep.collected["sun"] == 75


def test_award_screen_first_then_menu():
    g = FakeGame(ui=5)
    ps = session(g)
    ps.run()
    names = [s["name"] for s in ps.steps]
    assert names[:4] == ["window", "award_continue", "start_adventure", "chooser"]
    assert ps.steps[-1]["name"] == "award_continue"


def test_in_level_skips_navigation_and_writes_slots():
    g = FakeGame(ui=3, complete_after_run=False)
    ps = session(g, slot_types=[1, 16], auto_collect=False)
    rep = ps.run()
    assert [s["name"] for s in ps.steps] == ["window", "navigate", "place_lineup", "seed_slots", "execute", "tail"]
    assert ("slots", [1, 16]) in g.log and g.run_kw["collector"] is None
    assert "level not complete" in ps.steps[-1]["result"]
    assert rep.meta["steps"][-1]["name"] == "tail"
    assert rep.meta["tail"]["award_continue"].startswith("not called")


def test_memory_chooser_failure_falls_back_to_click_chooser():
    g = FakeGame(ui=2, input=True)
    ps = session(g, chooser_factory=lambda game: FakeMemChooser(game, fail=RuntimeError("boom")), seeds=[1, 2])
    ps.run()
    assert g.chooser.picks == [1, 2]
    kinds = [s["result"] for s in ps.steps if s["name"] == "chooser"]
    assert kinds[0].startswith("memory failed") and kinds[1] == "input: started"


def test_memory_chooser_failure_without_input_raises():
    g = FakeGame(ui=2, input=False)
    ps = session(g, chooser_factory=lambda game: FakeMemChooser(game, fail=RuntimeError("boom")))
    with pytest.raises(PlayError) as ei:
        ps.run()
    assert isinstance(ei.value.__cause__, RuntimeError) and "steps: window=" in str(ei.value)
    ps2 = session(g, chooser="memory", chooser_factory=lambda game: FakeMemChooser(game, fail=RuntimeError("x")))
    with pytest.raises(PlayError):
        ps2.run()


def test_no_remote_uses_click_chooser_and_no_collector():
    g = FakeGame(ui=2, remote=False, input=True)
    ps = session(g, chooser_factory=None, collector_factory=None)
    ps.run()
    assert g.chooser.picks == list(DEFAULT_SEEDS)
    assert any("click chooser" in n for n in ps.notes) and g.run_kw["collector"] is None
    g2 = FakeGame(ui=2, remote=False, input=False)
    with pytest.raises(PlayError):
        session(g2, chooser_factory=None).run()


def test_menu_timeout_and_unknown_screen():
    g = FakeGame(ui=1)
    with pytest.raises(PlayError):
        session(g, menu_factory=lambda game: FakeMenu(game, fail=True)).run()
    g = FakeGame(ui=4)
    with pytest.raises(PlayError):
        session(g).run()


def test_dry_run_requires_level_and_calls_nothing():
    g = FakeGame(ui=2)
    with pytest.raises(PlayError):
        session(g, dry_run=True).run()
    g = FakeGame(ui=3, complete_after_run=False)
    ps = session(g, dry_run=True, slot_types=[1])
    ps.run()
    assert [x for x in g.log if x[0] in ("place", "slots")] == []
    assert g.run_kw["dry_run"] is True and g.run_kw["collector"] is None
    assert [s["name"] for s in ps.steps] == ["window", "navigate", "place_lineup", "execute"]


def test_foreground_false_patches_background():
    w = FakeWindow(foreground=False)
    g = FakeGame(ui=3, window=w, complete_after_run=False)
    ps = session(g, foreground=False)
    ps.run()
    assert w.calls == ["ensure(False)"] and ps.steps[0]["result"] == "patched"
    assert ps.steps[0]["background_running"] is True


def test_award_wait_timeout_records_and_continue_error_is_logged():
    g = FakeGame(ui=3, complete_after_run=True)
    ps = session(g, menu_factory=lambda game: FakeMenu(game, fail=True))
    ps.run()
    assert ps.steps[-1]["name"] == "award_continue" and ps.steps[-1]["result"] is None and g.ui == 5


def test_bad_arguments():
    with pytest.raises(ValueError):
        PlaySession(FakeGame(), backend="x")
    with pytest.raises(ValueError):
        PlaySession(FakeGame(), chooser="x")


def test_game_play_level_delegates(monkeypatch):
    s, p = make_session(ui=3)
    g = Game(s, p, write=True, remote=True, foreground=False)
    seen = {}

    class PS:
        def __init__(self, game, **kw):
            seen.update(kw)
            seen["game"] = game

        def run(self):
            return "REPORT"

    import pvztools.control.play as play_mod

    monkeypatch.setattr(play_mod, "PlaySession", PS)
    out = g.play_level("3C八炮", "P4: PP", [1, 2], cob_col=8, auto_collect=False, tolerance_cs=5)
    assert out == "REPORT" and seen["game"] is g
    assert seen["seeds"] == [1, 2] and seen["cob_col"] == 8 and seen["foreground"] is False
    assert seen["auto_collect"] is False and seen["tolerance_cs"] == 5 and seen["chooser"] == "auto"


# --- report: call_ms + markdown -----------------------------------------------
def test_scheduler_records_call_ms():
    clock = FakeClock({1: 1000})
    be = LockBackend(clock)
    rep = run_plan(Plan((cob_action(1, 300, (1, 1)),), ()), clock, be)
    assert rep.rows[0].call_ms is not None and rep.rows[0].call_ms >= 0
    assert rep.summary()["call_ms"]["count"] == 1
    dry = run_plan(Plan((cob_action(1, 300, (1, 1)),), ()), clock, be, dry_run=True)
    assert dry.rows[0].call_ms is None and dry.call_ms_stats.count == 0


def sample_report() -> ExecutionReport:
    rep = ExecutionReport(mode="修改器", backend="remote", plan_actions=4, tolerance_cs=2)
    rep.rows = [
        ActionRow(1, "P", "cob", 300, observed_cs=300, delta=0, status="ok", fired_cs=300, frozen_cs=300, call_ms=20.0),
        ActionRow(1, "P", "cob", 300, observed_cs=302, delta=2, status="ok", fired_cs=302, call_ms=30.0),
        ActionRow(2, "P", "cob", 300, observed_cs=420, delta=120, status=STATUS_CD_WAIT, fired_cs=420, cd_wait_cs=118,
                  call_ms=25.0, note="cannon not ready at planned tick: fired when ready (+120cs)"),
        ActionRow(3, "P", "cob", 300, status="skipped", note="not reached (level ended)"),
        ActionRow(3, "I", "ice", 100, observed_cs=104, delta=4, status="late", note="late by 4cs"),
    ]
    rep.skipped = 1
    rep.late = 1
    rep.refresh_points = {1: 1000, 2: 1869}
    rep.stop_reason = "level ended"
    rep.started_wall, rep.finished_wall = 100.0, 160.5
    rep.warnings = ["started inside wave 1"]
    rep.collected = {"collected": 12, "calls": 4, "sun": 250, "money": 5, "errors": 0,
                     "by_type": {"SUN": 10, "SILVER": 1, "TROPHY": 1}}
    rep.meta = {"version": "1.2.0.1096", "profile": "goty_1_2_0_1096_en", "lineup": "3C八炮",
                "track": "P4: PP|PP|PP|PP (869|869|869|869)", "scene": "PE", "waves": 20, "level": 7, "cob_col": 8.0,
                "steps": [{"name": "window", "result": "foreground"}, {"name": "execute", "result": "level ended"}],
                "notes": ["extra note"]}
    return rep


def test_to_markdown_sections_and_labels():
    md = sample_report().to_markdown()
    for h in ("# 打关报告 (Play report)", "## 概要 (Header)", "## 时序表 (Timing)", "## 统计 (Summary)",
              "## 自动收集 (Auto-collect)", "## 备注 (Notes)"):
        assert h in md
    assert "| 版本 / profile (version) | 1.2.0.1096 / goty_1_2_0_1096_en |" in md
    assert "| 模式 (mode) | 修改器 |" in md and "| 阵型 (lineup) | 3C八炮 |" in md
    assert "| 场景 (scene) | PE |" in md and "| 波数 (waves) | 20 |" in md
    assert "| 用时 (wall) | 60.5 s |" in md
    # timing rows: hit / cd-wait / skipped / late with Chinese status labels
    assert "| 1 | P | 300 | 300 | +0 | 命中 |" in md
    assert "| 2 | P | 300 | 420 | +120 | CD等待 | 等待 118 cs (waited); cannon not ready" in md
    assert "| 3 | P | 300 | - | - | 跳过 |" in md
    assert "| 3 | I | 100 | ~104 | +4 | 晚 |" in md
    # summary: hits only (0, 2) -> mean 1.00, max 2; one cd-wait; call ms over 3 rows
    assert "n=3 mean=2.00 p95=4 max=4 cs; 定帧 (frame-locked) 3" in md or "n=3 mean=2.00" in md
    assert "CD 等待 (cd-wait, fired when ready): 1 次 (count); 等待 mean=118 p95=118 max=118 cs" in md
    assert "远程调用耗时 (remote call ms): n=3 mean=25.0 max=30.0 ms" in md
    assert "刷新点 (refresh points, main_counter): w1@1000, w2@1869" in md
    assert "暂停 (pauses): 0" in md
    assert "- 阳光 (sun): 250" in md and "- 金币 (coins, money value): 5" in md and "- 其他 (others" in md and ": 1" in md
    assert "SILVER=1" in md
    assert "- window: foreground" in md and "- started inside wave 1" in md and "- extra note" in md
    assert status_label("ok") == "命中" and status_label("weird") == "weird"


def test_markdown_abs_error_uses_timed_rows_only():
    rep = sample_report()
    a = rep.abs_error
    assert a.count == 3 and a.max == 4  # ok, ok, late; cd-wait excluded
    md = rep.to_markdown()
    assert "n=3 mean=2.00 p95=4 max=4 cs" in md


def test_markdown_without_context_or_collector():
    rep = ExecutionReport(mode="键控", backend="input", dry_run=True)
    md = rep.to_markdown()
    assert "| 后端 (backend) | input (dry-run) |" in md and "- 未启用 (disabled)" in md and "- 无 (none)" in md
    assert "| 阵型 (lineup) | - |" in md


def test_json_carries_meta_and_call_ms():
    d = json.loads(sample_report().to_json())
    assert d["meta"]["lineup"] == "3C八炮" and d["summary"]["call_ms"]["max"] == 30.0
    assert d["rows"][0]["call_ms"] == 20.0


# --- CLI -----------------------------------------------------------------------
def test_cli_play_flags_and_markdown(monkeypatch, capsys, tmp_path):
    s, p = make_session(ui=1, in_level=False)
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    seen = {}

    def fake_play(self, lineup, track, seeds, **kw):
        seen.update(kw)
        rep = sample_report()
        return rep

    monkeypatch.setattr(Game, "play_level", fake_play)
    out = tmp_path / "r.json"
    md = tmp_path / "r.md"
    rc = main(["play", "--lineup", "3C八炮", "--track", "P4: PP|PP", "--cob-col", "8", "--remote", "--yes",
               "--no-collect", "--no-foreground", "--chooser", "memory", "--report", str(out), "--md", str(md)])
    assert rc == EXIT_OK, capsys.readouterr()
    text = capsys.readouterr().out
    assert seen["auto_collect"] is False and seen["foreground"] is False and seen["chooser"] == "memory"
    assert "step window" in text and "report written: %s" % md in text
    assert md.read_text(encoding="utf-8").startswith("# 打关报告")
    assert json.loads(out.read_text(encoding="utf-8"))["meta"]["scene"] == "PE"


def test_cli_play_accepts_main_menu_rejects_dry_run_there(monkeypatch, capsys):
    s, p = make_session(ui=1, in_level=False)
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    assert main(["play", "--dry-run"]) != EXIT_OK
    assert "--dry-run 需要已在关卡中" in capsys.readouterr().err


# --- transient ui / keep-running ------------------------------------------------
def test_navigate_waits_for_a_settled_ui():
    """A saved game loads as ui 3 for a moment before its chooser appears:
    navigate must not take the transient 3 for 'in a level'."""

    class G2(FakeGame):
        transient_reads = 0

        @property
        def ui(self):
            if self.transient_reads:
                self.transient_reads -= 1
                return 3  # still loading: reads as 3, then the chooser shows
            return self._ui

        @ui.setter
        def ui(self, v):
            self._ui = v

    class Menu2(FakeMenu):
        def start_adventure(self, wait=10.0):
            self.game.transient_reads = 1
            self.game.ui = 2
            return 3

    g = G2(ui=1, complete_after_run=False)
    ps = session(g, menu_factory=lambda game: Menu2(game), settle_s=0.1)
    ps.run()
    assert [s["name"] for s in ps.steps[:4]] == ["window", "start_adventure", "chooser", "navigate"]
    assert ps.steps[3]["path"] == [1, 2, 3]


def test_window_step_patches_background_running_even_when_foreground():
    class Patches:
        def __init__(self):
            self.on = []

        def enable(self, name):
            self.on.append(name)

    w = FakeWindow(foreground=True)
    w.patches = Patches()
    w.safety = SimpleNamespace(allow_patch=True)
    g = FakeGame(ui=3, window=w, complete_after_run=False)
    ps = session(g)
    ps.run()
    assert ps.steps[0]["result"] == "foreground+patched" and w.patches.on == ["background_running"]
    w2 = FakeWindow(foreground=True)  # no patch grant -> plain foreground
    ps2 = session(FakeGame(ui=3, window=w2, complete_after_run=False))
    ps2.run()
    assert ps2.steps[0]["result"] == "foreground"


def test_track_label_and_meta_override():
    from pvztools.control.play import _track_label
    from pvztools.game import Game as _G

    track = _G.load_track("P4: PP|PP (869|869)")
    assert _track_label(track) == "P4: PP|PP"
    assert _track_label("P4: PP") == "P4: PP"
    g = FakeGame(ui=3, complete_after_run=False)
    rep = session(g, track=track, track_label="P4: PP|PP (869|869)").run()
    assert rep.meta["track"] == "P4: PP|PP (869|869)"
    rep2 = session(FakeGame(ui=3, complete_after_run=False), track=track).run()
    assert rep2.meta["track"] == "P4: PP|PP"


# --- tail: keep playing until the level really ends ----------------------------
class TailClock(FakeClock):
    """FakeClock whose level completes at ``complete_at`` (counter): the
    sample flips ``level_complete`` and the game ui goes 3 -> 5."""

    def __init__(self, refresh, game, complete_at: int, **kw) -> None:
        super().__init__(refresh, **kw)
        self.game = game
        self.complete_at = complete_at

    def read(self):
        s = super().read()
        if self.counter >= self.complete_at:
            s.level_complete = True
            s.in_level = False
            if self.game.ui == 3:
                self.game.ui = 5
        return s


class FakePatches:
    def __init__(self, *names: str) -> None:
        self.enabled = set(names)
        self.disabled: List[str] = []

    def enable(self, name: str) -> None:
        self.enabled.add(name)

    def disable(self, name: str) -> None:
        self.enabled.discard(name)
        self.disabled.append(name)


class TailCollector:
    """Counts ticks; the level-end award coin (TROPHY) shows up once the
    clock passes ``award_at`` and is "collected" on the next tick."""

    def __init__(self, clock: FakeClock, award_at: int) -> None:
        self.clock = clock
        self.award_at = award_at
        self.ticks = 0
        self.collected = 0
        self.awards = 0
        self.stats = SimpleNamespace(summary=self.summary)

    def tick(self, sample=None) -> int:
        self.ticks += 1
        if self.clock.counter >= self.award_at and not self.awards:
            self.awards += 1
            self.collected += 1
            return 1
        return 0

    def summary(self):
        return {"collected": self.collected, "calls": self.collected, "sun": 0, "money": 0,
                "level_awards": self.awards, "by_type": {"trophy": self.awards} if self.awards else {},
                "errors": 0}


class TailGame(FakeGame):
    """FakeGame exposing game_clock / backend / plan_track, so PlaySession
    builds the PlannedExecutor itself (tail + release)."""

    def __init__(self, plan: Plan, refresh: Dict[int, int], complete_at: int, **kw) -> None:
        super().__init__(ui=3, **kw)
        self.plan = plan
        self.game_clock = TailClock(refresh, self, complete_at, start=950)
        self.be = LockBackend(self.game_clock)
        self.be.patches = FakePatches("main_loop_block", "street_zombies_hack")
        self._patches = FakePatches("background_running")
        self.window.patches = self._patches
        self.plan_kw: Dict[str, Any] = {}

    def plan_track(self, track, **kw):
        self.plan_kw = dict(kw)
        self.log.append(("plan", track))
        return self.plan

    def backend(self, mode):
        self.log.append(("backend", mode))
        return self.be

    def run_track(self, *a, **k):  # pragma: no cover - must not be used here
        raise AssertionError("PlaySession should build the executor itself")


def _tail_setup(complete_at: int = 5400, award_at: int = 4300, **kw):
    # plan ends at wave 20; the level continues for waves 21, 22, 23
    refresh = {19: 1000, 20: 2000, 21: 3000, 22: 4000, 23: 5000}
    plan = Plan((cob_action(19, 296, (1, 1)), cob_action(20, 296, (1, 1)), cob_action(20, 296, (2, 1), row=5)), ())
    g = TailGame(plan, refresh, complete_at)
    col = TailCollector(g.game_clock, award_at)
    kw.setdefault("quiet", True)
    ps = session(g, collector_factory=lambda game: col, tolerance_cs=3, **kw)
    return g, col, ps


def test_tail_keeps_collecting_until_the_level_ends_and_releases_patches(capsys):
    g, col, ps = _tail_setup(quiet=False)
    rep = ps.run()
    # executor: plan exhausted at w20 but the level ran on -> tail until level_complete
    assert rep.stop_reason == "level complete"
    t = rep.meta["tail"]
    assert t["policy"] is None and t["waves"] == [21, 22, 23] and t["fired_groups"] == 0
    assert t["reason"] == "level complete" and t["polls"] > 0 and t["seconds"] > 0
    # the collector ticked throughout the tail and picked up the award item
    assert col.ticks > 10 and col.awards == 1 and t["collected"] == 1
    assert t["award_item_collected"] is False  # collected by the executor tail, before the session tail
    assert t["level_awards"] == 1 and t["level_ended"] is True and t["ui_after"] == 5
    # award_continue exactly once, ui left 5
    assert ps.menu.calls == ["award"] and g.ui == 1 and t["award_continue"] == "ui=1"
    names = [s["name"] for s in ps.steps]
    assert names[-2:] == ["tail", "award_continue"]
    # every patch released: the executor's (main_loop_block + fragment patch) and play's background_running
    assert g.be.patches.enabled == set() and g._patches.enabled == set()
    assert g.be.patches.disabled[0] == "main_loop_block"
    assert set(ps.released) == {"main_loop_block", "street_zombies_hack", "background_running"}
    assert rep.meta["patches_released"] == ps.released
    # the executor was built by the session with the run_kw split correctly
    assert g.plan_kw == {"default_cob_col": 8.0} and ps.executor.tolerance_cs == 3
    assert ps.executor.tail_timeout_s == 600.0
    # one clear line
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("play finished: stop=level complete ui=1 ")
    assert "award_item=yes" in out[0] and "award_continue=ui=1" in out[0]
    assert "patches_released=main_loop_block" in out[0]


def test_tail_repeat_policy_refires_last_cycle_when_ready():
    g, col, ps = _tail_setup(tail_policy="repeat")
    rep = ps.run()
    fires = [c for c in g.be.calls if c[0] in ("fire_cob", "fire_cobs")]
    # plan: w19 (1 cannon), w20 (2 cannons); tail: w21, w22, w23 repeat the w20 pair
    assert len(fires) == 5 and [c[0] for c in fires[2:]] == ["fire_cobs"] * 3
    tail_rows = [r for r in rep.rows if r.wave > 20]
    assert [r.wave for r in tail_rows] == [21, 21, 22, 22, 23, 23]
    assert all(r.status == "ok" and r.planned_cs == 296 and r.detail.startswith("tail cob") for r in tail_rows)
    assert rep.meta["tail"]["fired_groups"] == 3 and rep.stop_reason == "level complete"
    assert ps.menu.calls == ["award"]


def test_tail_timeout_and_failed_award_continue_are_reported_once():
    from pvztools.control.executor.scheduler import PlannedExecutor

    # the level never completes: executor tail times out, session tail times out, no award_continue
    g, col, ps = _tail_setup(complete_at=10 ** 9, tail_timeout_s=0.5, award_wait_s=1.0)
    rep = ps.run()
    assert rep.stop_reason == "tail timeout" and rep.meta["tail"]["reason"] == "tail timeout"
    assert ps.menu.calls == [] and rep.meta["tail"]["award_continue"].startswith("not called")
    assert g.be.patches.enabled == set() and g._patches.enabled == set()

    # award_continue raising (e.g. a remote-call timeout that left the game frozen): reported, not retried
    class Frozen(RuntimeError):
        main_loop_blocked = True

    class BadMenu(FakeMenu):
        def award_continue(self, wait=10.0):
            self.calls.append("award")
            raise Frozen("timeout")

    g, col, ps = _tail_setup(menu_factory=lambda game: BadMenu(game))
    rep = ps.run()
    assert ps.menu.calls == ["award"] and g.ui == 5
    assert rep.meta["tail"]["award_continue"].startswith("failed: timeout")
    assert rep.meta["tail"]["award_continue_frozen"] is True
    assert ps.steps[-1]["name"] == "award_continue" and ps.steps[-1]["main_loop_blocked"] is True
    assert g.be.patches.enabled == set()  # release still unfreezes

    # bad tail policy is refused up front
    with pytest.raises(ValueError):
        PlannedExecutor(g.game_clock, g.be, g.plan, tail_policy="x")


def test_run_releases_patches_even_when_a_phase_fails(capsys):
    g, col, ps = _tail_setup()

    def boom(name, clear=True):
        raise RuntimeError("place failed")

    g.place_lineup = boom
    with pytest.raises(PlayError):
        ps.run()
    assert g._patches.enabled == set()
    assert capsys.readouterr().out == ""  # quiet=True
    ps.released = []
    line = ps._finish_line(None, RuntimeError("x"))
    assert line.startswith("play aborted (RuntimeError): stop=x ui=3 ") and line.endswith("patches_released=none")


def test_level_award_types_match_coin_is_level_award():
    from pvztools.control.autocollect import (AutoCollector, CoinType, DEFAULT_TYPES, LEVEL_AWARD_TYPES,
                                             CollectStats)

    # re-plants Coin::IsLevelAward, 15 ids
    assert LEVEL_AWARD_TYPES == frozenset({7, 8, 21, 22, 9, 11, 10, 12, 13, 14, 15, 18, 20, 19, 24})
    assert LEVEL_AWARD_TYPES <= DEFAULT_TYPES
    assert CoinType.USABLE_SEED_PACKET not in DEFAULT_TYPES and CoinType.NONE not in DEFAULT_TYPES
    st = CollectStats()
    st.add(CoinType.SUN)
    st.add(CoinType.TROPHY)
    assert st.level_awards == 1 and st.summary()["level_awards"] == 1
    prof = SimpleNamespace(objects={"coin": SimpleNamespace(stride=0xD8)})
    acts = SimpleNamespace(conv=object(), _run=lambda *a: None)
    assert AutoCollector(acts, None, prof).collects_level_awards is True
    assert AutoCollector(acts, None, prof, types=[CoinType.SUN]).collects_level_awards is False


# --- 2026-08-22 incident follow-ups: no clicks during a talk / after a leaked remote thread ---
class TalkWindow(FakeWindow):
    """FakeWindow with the cutscene signal (``cutscene_dialog_active``)."""

    def __init__(self, active: bool = True, ends_after: Optional[int] = None, **kw) -> None:
        super().__init__(**kw)
        self.active = active
        self.ends_after = ends_after
        self.reads = 0

    def cutscene_dialog_active(self) -> bool:
        self.reads += 1
        if self.ends_after is not None and self.reads > self.ends_after:
            self.active = False
        return self.active


class StepClickChooser(FakeClickChooser):
    """A click chooser with the step methods play uses (records every click)."""

    def __init__(self, game: "FakeGame") -> None:
        super().__init__(game)
        self.calls: List[str] = []

    def _require_chooser(self, what: str) -> None:
        self.calls.append("require")

    def bank_size(self) -> int:
        return 8

    def fill_types(self, picks, n):
        return list(picks)[:n]

    def clear_bank(self) -> None:
        self.calls.append("clear")

    def pick(self, ids) -> None:
        self.calls.append("pick")
        self.picks = list(ids)

    def bank_types(self):
        return list(self.picks)

    def start(self, wait: float = 5.0, retry: bool = True) -> bool:
        self.calls.append("start(retry=%s)" % retry)
        self.game.ui = 3
        return True


def test_input_only_play_refuses_to_click_during_a_dave_talk():
    win = TalkWindow(active=True)
    g = FakeGame(ui=2, remote=False, input=True, window=win)
    g.chooser = StepClickChooser(g)
    ps = session(g, chooser_factory=None, collector_factory=None, wait_ui_s=1.0)
    with pytest.raises(PlayError) as ei:
        ps.run()
    assert "戴夫对话" in str(ei.value)
    assert g.chooser.calls == [] and g.chooser.picks == []        # not one click
    assert any(s["name"] == "cutscene" and "refused" in s["result"] for s in ps.steps)
    # chooser='input' takes the same guarded path
    win2 = TalkWindow(active=True)
    g2 = FakeGame(ui=2, remote=True, input=True, window=win2)
    g2.chooser = StepClickChooser(g2)
    with pytest.raises(PlayError):
        session(g2, chooser="input", chooser_factory=None, cutscene_factory=lambda g: None, wait_ui_s=1.0).run()
    assert g2.chooser.calls == []


def test_input_only_play_waits_for_the_talk_to_end_then_clicks_without_retry():
    win = TalkWindow(active=True, ends_after=3)
    g = FakeGame(ui=2, remote=False, input=True, window=win)
    g.chooser = StepClickChooser(g)
    ps = session(g, chooser_factory=None, collector_factory=None, wait_ui_s=5.0, seeds=[1, 2])
    ps.run()
    assert g.chooser.calls == ["require", "clear", "pick", "start(retry=False)"]
    assert any(s["name"] == "cutscene" and "talk ended" in s["result"] for s in ps.steps)
    # no signal at all (plain FakeWindow): unchanged behaviour
    g3 = FakeGame(ui=2, remote=False, input=True)
    g3.chooser = StepClickChooser(g3)
    session(g3, chooser_factory=None, collector_factory=None).run()
    assert g3.chooser.calls[-1] == "start(retry=False)"


@pytest.mark.parametrize("make_exc", [
    lambda: __import__("pvztools.control.remote", fromlist=["x"]).RemoteCallTimeout("chooser_start", 5000, 0x1000, 7),
    lambda: __import__("pvztools.control.remote", fromlist=["x"]).RemoteCallFailed("WAIT_FAILED", thread_running=True),
    lambda: __import__("pvztools.control.remote", fromlist=["x"]).RemoteCallFailed("not started", thread_running=False),
    lambda: type("E", (RuntimeError,), {"thread_running": True})("duck"),
])
def test_remote_call_failure_in_memory_chooser_never_falls_back_to_clicks(make_exc):
    exc = make_exc()
    g = FakeGame(ui=2, input=True)
    g.chooser = StepClickChooser(g)
    ps = session(g, chooser_factory=lambda game: FakeMemChooser(game, fail=exc), seeds=[1, 2])
    with pytest.raises(PlayError) as ei:
        ps.run()
    assert ei.value.__cause__ is exc
    assert g.chooser.calls == [] and g.chooser.picks == []
    res = [s["result"] for s in ps.steps if s["name"] == "chooser"]
    assert len(res) == 1 and "remote thread may still run" in res[0]
    # an ordinary chooser error (no remote thread involved) still falls back
    g2 = FakeGame(ui=2, input=True)
    g2.chooser = StepClickChooser(g2)
    session(g2, chooser_factory=lambda game: FakeMemChooser(game, fail=RuntimeError("boom")), seeds=[1, 2]).run()
    assert g2.chooser.picks == [1, 2]
