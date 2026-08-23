"""Executor v2 integration: frame-locked cob columns in the report, the
"fire when ready" (cd-wait) fallback, RemoteBackend at_tick / cobs_ready,
Game.window / chooser / play_level / set_seed_slots and the ``play`` /
``chooser`` / ``window`` CLI commands."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from pvztools import game as game_mod
from pvztools.cli.__main__ import EXIT_ERROR, EXIT_GAME, EXIT_OK, main
from pvztools.control.backends import RemoteBackend
from pvztools.control.executor.report import STATUS_CD_WAIT, ActionRow, ExecutionReport
from pvztools.control.executor.scheduler import PlannedExecutor
from pvztools.game import DEFAULT_PLAY_SEEDS, Game, NotInLevel, PermissionDenied, attach
from pvztools.schema.track.ast import Op, Target
from pvztools.schema.track.expand import Action, Plan
from test_exec_integration import FakeObserver, fake_game
from test_game_api import make_session


# --- helpers ------------------------------------------------------------------
def cob_action(wave: int, cs: int, cannon, row: int = 2, col: float = 8.0, symbol: str = "P") -> Action:
    op = Op(kind="cob", symbol=symbol, target=Target(rows=(row,), col=col))
    return Action(wave=wave, effect_cs=cs + 373, operation_cs=cs, kind="cob", symbol=symbol, plant=None,
                  target=Target(rows=(row,), col=col), cannon=tuple(cannon), source_op=op)


class FakeClock:
    """Clock with an absolute counter; ``advance`` ticks per read."""

    def __init__(self, refresh: Dict[int, int], start: int = 0, advance: int = 1) -> None:
        self.refresh = dict(refresh)
        self.counter = start
        self.advance = advance
        self.wall = 0.0

    def now(self) -> float:
        return self.wall

    def frame_ms(self) -> int:
        return 10

    def _wave(self, c: int) -> int:
        return max([w for w, r in self.refresh.items() if r <= c] or [0])

    def read(self):
        self.counter += self.advance
        self.wall += 0.01 * self.advance
        c = self.counter
        w = self._wave(c)
        nxt = self.refresh.get(w + 1)
        return SimpleNamespace(main_counter=c, wave=w, paused=False, in_level=True, level_complete=False,
                               countdown=(nxt - c) if nxt else 0)

    def wave_time(self, sample, wave):
        r = self.refresh.get(wave)
        return None if r is None else sample.main_counter - r

    def time_to_next_refresh(self, sample):
        nxt = self.refresh.get(sample.wave + 1)
        return None if nxt is None else nxt - sample.main_counter

    def paused(self) -> bool:
        return False

    def wait_until(self, pred, timeout, eta=None, pause_timeout=None):
        for _ in range(int(timeout * 100) + 1):
            if pred(self.read()):
                return


class LockBackend:
    """Remote-like cob backend: fires return FireResult look-alikes with the
    frame-lock fields; readiness is scripted by ``ready_at`` (counter)."""

    mode = "remote"

    def __init__(self, clock: FakeClock, ready_at: Optional[int] = None, lock_lag: int = 0) -> None:
        self.clock = clock
        self.ready_at = ready_at
        self.lock_lag = lock_lag
        self.calls: List[Any] = []
        self.ready_queries = 0

    def cobs_ready(self, cannons):
        self.ready_queries += 1
        ok = self.ready_at is None or self.clock.counter >= self.ready_at
        return [ok] * len(cannons)

    def _result(self, at_tick):
        now = self.clock.counter
        frozen = now if at_tick is None else max(now, at_tick) + self.lock_lag
        return SimpleNamespace(fired_tick=frozen, frozen_tick=frozen, attempts=2 if at_tick is not None else 1,
                               state_after=38, requested_tick=at_tick,
                               late_by=None if at_tick is None else frozen - at_tick)

    def fire_cob(self, cannon, row, col, *, at_tick=None):
        self.calls.append(("fire_cob", tuple(cannon), row, col, at_tick))
        if at_tick is not None:
            self.clock.counter = max(self.clock.counter, at_tick)
        return self._result(at_tick)

    def fire_cobs(self, fires, *, at_tick=None):
        self.calls.append(("fire_cobs", [tuple(c) for c, _ in fires], at_tick))
        if at_tick is not None:
            self.clock.counter = max(self.clock.counter, at_tick)
        return [self._result(at_tick) for _ in fires]

    def plant_card(self, *a, **k):
        raise AssertionError("not used")

    shovel = plant_card


# --- report columns ------------------------------------------------------------
def test_report_columns_and_cd_wait_summary():
    rep = ExecutionReport(mode="键控", backend="remote")
    rep.rows = [
        ActionRow(1, "P", "cob", 300, observed_cs=301, delta=1, status="ok", frozen_cs=300, fired_cs=301, attempts=1),
        ActionRow(2, "P", "cob", 300, observed_cs=298, delta=-2, status="ok", frozen_cs=298, fired_cs=298, attempts=3),
        ActionRow(3, "P", "cob", 300, observed_cs=520, delta=220, status=STATUS_CD_WAIT, cd_wait_cs=220,
                  fired_cs=520, note="cannon not ready"),
        ActionRow(4, "P", "cob", 300, status="skipped"),
    ]
    s = rep.summary()
    assert s["dispatched"] == 3 and s["cd_waits"] == 1 and s["frame_locked"] == 3
    assert rep.deltas == [1, -2]  # the cd-wait is not a timing error
    assert s["abs_error"]["mean"] == 1.5 and s["abs_error"]["max"] == 2 and s["abs_error"]["p95"] == 2
    assert s["cd_wait"] == {"count": 1, "mean": 220.0, "p95": 220.0, "max": 220, "min": 220}
    text = rep.to_text()
    assert "cd-waits=1" in text and "abs: mean=1.50" in text and "frame-locked=3" in text
    assert "cd-wait: n=1" in text
    hdr = [ln for ln in text.splitlines() if ln.startswith("wave")][0]
    for col in ("planned", "frozen", "fired", "delta", "att", "status"):
        assert col in hdr
    row3 = [ln for ln in text.splitlines() if ln.startswith("   3 ")][0]
    assert "520" in row3 and "+220" in row3 and "cd-wait" in row3
    d = json.loads(rep.to_json())
    assert d["rows"][0]["frozen_cs"] == 300 and d["rows"][0]["attempts"] == 1
    assert d["rows"][2]["cd_wait_cs"] == 220 and d["summary"]["cd_waits"] == 1


# --- scheduler: frame-lock columns + cd-wait fallback ---------------------------------
def run_plan(plan: Plan, clock: FakeClock, be: Any, **kw) -> ExecutionReport:
    ex = PlannedExecutor(clock, be, plan, **kw)
    return ex.run()


def test_scheduler_fills_frozen_fired_attempts_from_fire_result():
    clock = FakeClock({1: 1000, 2: 2000})
    be = LockBackend(clock)
    plan = Plan((cob_action(1, 300, (1, 1)), cob_action(1, 300, (2, 1), row=5)), ())
    rep = run_plan(plan, clock, be)
    assert be.calls and be.calls[0][0] == "fire_cobs" and be.calls[0][2] == 1300
    for r in rep.rows:
        assert r.status == "ok" and r.frozen_cs == 300 and r.fired_cs == 300 and r.delta == 0 and r.attempts == 2
    assert rep.summary()["frame_locked"] == 2 and rep.cd_waits == 0
    ev = [e for e in rep.events if e["event"] == "dispatch"][0]
    assert ev["fired_cs"] == [300, 300] and ev["cd_wait_cs"] is None


def test_scheduler_lock_overshoot_is_timing_error_not_cd_wait():
    clock = FakeClock({1: 1000})
    be = LockBackend(clock, lock_lag=1)
    rep = run_plan(Plan((cob_action(1, 300, (1, 1)),), ()), clock, be)
    r = rep.rows[0]
    assert r.fired_cs == 301 and r.delta == 1 and r.status == "ok" and r.cd_wait_cs is None


def test_scheduler_waits_for_cannon_then_fires_and_labels_cd_wait():
    clock = FakeClock({1: 1000, 2: 2500})
    be = LockBackend(clock, ready_at=1450)  # ready 150 ticks after the planned 1300
    plan = Plan((cob_action(1, 300, (1, 1)),), ())
    rep = run_plan(plan, clock, be)
    r = rep.rows[0]
    assert r.status == STATUS_CD_WAIT and r.cd_wait_cs is not None and 145 <= r.cd_wait_cs <= 160
    assert r.fired_cs is not None and 445 <= r.fired_cs <= 460 and r.delta == r.fired_cs - 300
    assert "fired when ready" in r.note
    # fired unlocked (no at_tick in the past) and not counted as late
    assert be.calls[-1] == ("fire_cob", (1, 1), 2, 8.0, None)
    assert rep.late == 0 and rep.cd_waits == 1 and rep.deltas == []
    assert rep.summary()["cd_wait"]["count"] == 1
    kinds = [e["event"] for e in rep.events]
    assert "cd_wait" in kinds and "late" not in kinds
    assert "cd-waits=1" in rep.to_text()


def test_scheduler_cd_wait_bounded_then_fires_anyway():
    clock = FakeClock({1: 1000, 2: 9000}, advance=5)
    be = LockBackend(clock, ready_at=10**9)  # never ready
    rep = run_plan(Plan((cob_action(1, 300, (1, 1)),), ()), clock, be, cd_wait_max_cs=200)
    r = rep.rows[0]
    assert r.status == STATUS_CD_WAIT and 200 <= r.cd_wait_cs <= 215
    assert be.calls  # dispatched after the bound


def test_scheduler_without_cobs_ready_behaves_as_before():
    clock = FakeClock({1: 1000})
    be = LockBackend(clock, ready_at=10**9)
    del LockBackend.cobs_ready  # type: ignore[attr-defined]
    try:
        rep = run_plan(Plan((cob_action(1, 300, (1, 1)),), ()), clock, be)
    finally:
        LockBackend.cobs_ready = lambda self, cannons: [self.ready_at is None or self.clock.counter >= self.ready_at] * len(cannons)  # type: ignore[assignment]
    assert rep.rows[0].status == "ok" and rep.cd_waits == 0


def test_dry_run_never_queries_readiness():
    clock = FakeClock({1: 1000})
    be = LockBackend(clock, ready_at=10**9)
    rep = run_plan(Plan((cob_action(1, 300, (1, 1)),), ()), clock, be, dry_run=True)
    assert rep.rows[0].status == "dry-run" and be.ready_queries == 0 and not be.calls


# --- RemoteBackend ---------------------------------------------------------------
class FakeActions:
    def __init__(self) -> None:
        self.calls: List[Any] = []
        self._cobs = [SimpleNamespace(row=1, col=1, ready=True), SimpleNamespace(row=2, col=1, ready=False)]

    def fire_cob(self, cob, row, col, **kw):
        self.calls.append(("fire_cob", cob, row, col, kw))
        return "r1"

    def fire_cobs(self, fires, **kw):
        self.calls.append(("fire_cobs", fires, kw))
        return ["r"] * len(fires)

    def cobs(self):
        return list(self._cobs)


def test_remote_backend_passes_at_tick_and_reports_readiness():
    a = FakeActions()
    rb = RemoteBackend(a)
    assert PlannedExecutor._backend_accepts_at_tick(rb) is True
    rb.fire_cob((1, 1), 2, 8.0)
    assert a.calls[-1][4] == {}  # no at_tick -> old call shape
    rb.fire_cob((1, 1), 2, 8.0, at_tick=1300)
    assert a.calls[-1][4] == {"at_tick": 1300}
    rb.fire_cobs([((1, 1), (2, 8.0)), ((2, 1), (5, 8.0))], at_tick=1300)
    assert a.calls[-1][2] == {"at_tick": 1300}
    assert rb.cobs_ready([(1, 1), (2, 1), (3, 3)]) == [True, False, False]


# --- Game: window / chooser / seeds / play_level -------------------------------------
def test_attach_foreground_flag(monkeypatch):
    s, p = make_session()
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    g = attach(input=True, foreground=False)
    assert g._foreground is False and g.input
    g2 = attach(input=True)
    assert g2._foreground is True


def test_window_and_chooser_properties():
    s, p = make_session(ui=2)
    g = Game(s, p, input=True)
    from pvztools.control.chooser import SeedChooser
    from pvztools.control.window import GameWindow

    assert isinstance(g.window, GameWindow) and g.window is g.window
    assert isinstance(g.chooser, SeedChooser) and g.chooser.window is g.window
    assert g.window.profile is p and g.window.patches is None  # no patch grant -> no PatchManager
    g3 = Game(s, p, write=True, input=True)
    assert g3.window.patches is not None


def test_set_seed_slots_writes_types_and_needs_write():
    s, p = make_session()
    ro = Game(s, p)
    with pytest.raises(PermissionDenied):
        ro.set_seed_slots([1, 16, 14])
    g = Game(s, p, write=True)
    out = g.set_seed_slots(["向日葵", 16, 14, 35])  # 4 given, 3 slots
    assert out == [(0, 1), (1, 16), (2, 14)]
    assert [x.type for x in g.seeds] == [1, 16, 14]


def test_play_level_from_chooser(monkeypatch):
    fake = FakeObserver({1: 1000}, ui=2)
    g = fake_game(fake, write=True, remote=True, input=True)
    log: List[Any] = []

    class Chooser:
        def select_and_start(self, picks, wait=5.0):
            log.append(("select", list(picks)))
            fake.ui = 3
            return True

    g._chooser_obj = Chooser()
    monkeypatch.setattr(g, "place_lineup", lambda name, clear=True: log.append(("place", name, clear)))
    monkeypatch.setattr(g, "set_seed_slots", lambda types: log.append(("slots", list(types))))
    monkeypatch.setattr(g, "run_track", lambda track, **kw: log.append(("run", track, kw)) or "REPORT")
    rep = g.play_level("3C八炮", "P4: PP|PP", None, cob_col=8, slot_types=[1, 16], settle_s=0, tolerance_cs=3,
                       chooser="input", finish_level=False, foreground=False)
    assert rep == "REPORT"
    assert log[0] == ("select", list(DEFAULT_PLAY_SEEDS))
    assert log[1] == ("place", "3C八炮", True) and log[2] == ("slots", [1, 16])
    kind, track, kw = log[3]
    assert track == "P4: PP|PP" and kw["backend"] == "remote" and kw["default_cob_col"] == 8.0
    assert kw["tolerance_cs"] == 3 and kw["dry_run"] is False


def test_play_level_in_level_skips_chooser_and_honours_place_false(monkeypatch):
    fake = FakeObserver({1: 1000}, ui=3)
    g = fake_game(fake, write=True, remote=True)
    called = []
    monkeypatch.setattr(g, "place_lineup", lambda *a, **k: called.append("place"))
    monkeypatch.setattr(g, "run_track", lambda track, **kw: called.append(("run", kw["backend"])) or "R")
    assert g.play_level("x", "P4: PP", place=False, backend="remote", finish_level=False, foreground=False) == "R"
    assert called == [("run", "remote")]


def test_play_level_gating():
    from pvztools.control.play import PlayError

    fake = FakeObserver({1: 1000}, ui=2)
    g = fake_game(fake)  # neither remote (memory chooser) nor input (click chooser)
    with pytest.raises(PlayError):
        g.play_level("x", "P4: PP", foreground=False)
    fake.ui = 4  # no route from an unknown screen
    with pytest.raises(PlayError):
        g.play_level("x", "P4: PP", foreground=False)
    fake.ui = 1
    with pytest.raises(PlayError):  # dry run must start inside a level
        g.play_level("x", "P4: PP", foreground=False, dry_run=True)


def test_play_level_runs_frame_locked_track_end_to_end(monkeypatch):
    """Real PlannedExecutor over the fake clock with a remote-like backend:
    rows carry fired / frozen ticks from the backend results."""
    fake = FakeObserver({1: 1000, 2: 2000}, ui=3)
    g = fake_game(fake, write=True, remote=True)

    class FRBackend(LockBackend):
        pass

    clock = g.game_clock
    be = LockBackend.__new__(LockBackend)
    be.clock = SimpleNamespace()  # the scheduler supplies ticks; make fire results from the game clock
    be.ready_at = None
    be.lock_lag = 0
    be.calls = []
    be.ready_queries = 0

    def fire_cobs(fires, *, at_tick=None):
        be.calls.append(at_tick)
        return [SimpleNamespace(fired_tick=at_tick, frozen_tick=at_tick, attempts=1) for _ in fires]

    be.fire_cobs = fire_cobs
    be.fire_cob = lambda c, r, k, *, at_tick=None: fire_cobs([(c, (r, k))], at_tick=at_tick)[0]
    monkeypatch.setattr(g, "backend", lambda mode="input": be)
    rep = g.play_level(None, "P4: PP|PP", backend="remote", cob_col=8, wave_count=2, cobs="11,21,31,41", scene="PE",
                       place=False, finish_level=False, foreground=False, auto_collect=False)
    assert isinstance(rep, ExecutionReport)
    assert [r.status for r in rep.rows] == ["ok"] * 4
    assert all(r.fired_cs == r.planned_cs and r.delta == 0 and r.frozen_cs == r.planned_cs for r in rep.rows)
    assert be.calls == [1000 + rep.rows[0].planned_cs, 2000 + rep.rows[2].planned_cs]
    # --cob-col 8 reached the expander: every P lands in column 8
    assert all("8.0" in r.detail for r in rep.rows)


# --- CLI -----------------------------------------------------------------------------
@pytest.fixture
def cli_game(monkeypatch):
    s, p = make_session(ui=2)
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    return s


def test_cli_play_gating(capsys):
    assert main(["play"]) == EXIT_ERROR
    assert "--remote" in capsys.readouterr().err
    assert main(["play", "--remote"]) == EXIT_ERROR
    assert "--yes" in capsys.readouterr().err
    assert main(["play", "--remote", "--yes", "--cob-col", "11"]) == EXIT_ERROR
    assert "--cob-col" in capsys.readouterr().err


def test_cli_play_passes_options(cli_game, monkeypatch, capsys, tmp_path):
    seen = {}

    def fake_play(self, lineup, track, seeds, **kw):
        seen.update(lineup=lineup, track=track.name if hasattr(track, "name") else track, seeds=seeds, **kw)
        seen["input"] = self.input
        seen["remote"] = self.remote
        rep = ExecutionReport(mode="键控", backend="remote", plan_actions=2)
        rep.rows = [ActionRow(1, "P", "cob", 300, observed_cs=300, delta=0, status="ok", frozen_cs=300, fired_cs=300, attempts=1)]
        rep.stop_reason = "plan exhausted"
        return rep

    monkeypatch.setattr(Game, "play_level", fake_play)
    out = tmp_path / "play.json"
    rc = main(["play", "--lineup", "3C八炮", "--track", "P4: PP|PP|PP|PP (869|869|869|869)", "--seeds", "0,1,2,3,4,5,6,16",
               "--cob-col", "8", "--remote", "--yes", "--report", str(out)])
    assert rc == EXIT_OK, capsys.readouterr()
    text = capsys.readouterr().out
    assert "frame-locked=1" in text and "report written" in text
    assert seen["lineup"] == "3C八炮" and seen["seeds"] == ["0", "1", "2", "3", "4", "5", "6", "16"]
    assert seen["cob_col"] == 8.0 and seen["backend"] == "remote" and seen["input"] and seen["remote"]
    assert seen["place"] is True and seen["dry_run"] is False
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["frame_locked"] == 1


def test_cli_play_wrong_screen(monkeypatch, capsys):
    s, p = make_session(ui=4, in_level=False)  # ui 4 = options / no route
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    assert main(["play", "--remote", "--yes"]) == EXIT_GAME
    assert "主菜单 / 选卡界面 / 关卡" in capsys.readouterr().err


def test_cli_chooser_and_window(cli_game, monkeypatch, capsys):
    assert main(["chooser", "pick", "1", "16"]) == EXIT_ERROR
    assert "--yes" in capsys.readouterr().err
    calls = []

    class Chooser:
        def clear_bank(self):
            calls.append("clear")
            return 3

        def pick(self, types):
            calls.append(("pick", types))
            return [1, 16]

        def bank_types(self):
            return [1, 16, -1]

        def start(self, wait=5.0):
            calls.append(("start", wait))
            return True

        def select_and_start(self, types, wait=5.0):
            calls.append(("select", types))
            return True

    monkeypatch.setattr(Game, "chooser", property(lambda self: Chooser()))
    assert main(["chooser", "clear", "--yes"]) == EXIT_OK
    assert main(["chooser", "pick", "1", "16", "--yes"]) == EXIT_OK
    assert main(["chooser", "start", "--yes", "--wait", "2"]) == EXIT_OK
    assert main(["chooser", "select", "1,16", "14", "--yes"]) == EXIT_OK
    assert calls == ["clear", ("pick", ["1", "16"]), ("start", 2.0), ("select", ["1", "16", "14"])]
    out = capsys.readouterr().out
    assert "cleared 3" in out and "picked [1, 16]" in out and "started" in out

    class Win:
        hwnd = 0x10

        def raw_client_size(self):
            return (640, 480)

        def client_size(self):
            return (800, 600)

        def is_foreground(self):
            return False

        def background_running(self):
            return True

        def bring_to_foreground(self):
            return True

        def calibrate(self):
            from pvztools.control.window import Calibration
            return Calibration(0.8, 0.8, 0.0, 0.0, "ratio")  # the real NamedTuple

    monkeypatch.setattr(Game, "window", property(lambda self: Win()))
    assert main(["window", "info"]) == EXIT_OK
    assert "client=800x600" in capsys.readouterr().out
    assert main(["window", "front"]) == EXIT_ERROR  # needs --yes
    assert main(["window", "front", "--yes"]) == EXIT_OK
    assert main(["window", "calibrate", "--yes"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "source=ratio" in out and "scale=(0.8000, 0.8000)" in out and "offset=(0.00, 0.00)" in out


def test_cli_track_run_cob_col(cli_game, monkeypatch, capsys):
    s = cli_game
    seen = {}

    def fake_run(self, track, **kw):
        seen.update(kw)
        return ExecutionReport(mode="键控", backend="input")

    monkeypatch.setattr(Game, "run_track", fake_run)
    assert main(["track", "run", "P4: PP", "--dry-run", "--cob-col", "8", "--quiet"]) == EXIT_OK
    assert seen["default_cob_col"] == 8.0


def test_help_lists_play_commands(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for name in ("play", "chooser", "window"):
        assert name in out
