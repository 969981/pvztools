"""Regression tests for the executor review findings (scheduler ordering /
early refresh, input-backend lag, NotInLevel at level end, mid-wave start,
clock stall detection, fodder resolution, CLI flags, reactive debounce)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pvztools import game as game_mod
from pvztools.cli.__main__ import EXIT_ERROR, EXIT_OK, main
from pvztools.control.backends import BackendError, RemoteBackend, pick_fodder
from pvztools.control.executor import GameClock, PlannedExecutor
from pvztools.control.executor.clock import PAUSE_MIN_SPAN, RefreshPoint
from pvztools.control.executor.reactive import ReactiveRunner
from pvztools.control.executor.report import ExecutionReport
from pvztools.control.input_backend import InputBackend
from pvztools.game import Game
from pvztools.modifier import Safety
from pvztools.observe import NotInLevel
from pvztools.schema.enums import PlantType
from pvztools.schema.track.ast import KIND_FODDER, Op, Target
from pvztools.schema.track.expand import Action, Plan
from test_exec_integration import DOWN, UP, FakeObserver, FakeUser32, fake_game, ice_plan, plant, seed
from test_game_api import make_session


def fodder_plan(wave: int, cs: int, row: int = 1, col: int = 9, window: int = 0) -> Plan:
    win = (cs, cs + window) if window else None
    op = Op(kind=KIND_FODDER, symbol="C", target=Target(rows=(row,), col=float(col)), window_cs=win, effect_cs=cs)
    a = Action(wave=wave, effect_cs=cs, operation_cs=cs, kind=KIND_FODDER, symbol="C", plant=None,
               target=Target(rows=(row,), col=float(col)), cannon=None, source_op=op)
    return Plan(actions=(a,), issues=())


class ConsistentObserver(FakeObserver):
    """FakeObserver whose ``countdown_init`` is the real length of the current
    wave (what the game does: countdown is reset to countdown_start at refresh)."""

    def board(self):
        b = super().board()
        w = b.wave
        nxt = self.refresh.get(w + 1)
        cur = self.refresh.get(w)
        b.countdown_init = (nxt - cur) if (nxt is not None and cur is not None) else 2000
        return b


# --- fodder (plant=None) resolution ----------------------------------------------
def test_pick_fodder_order_and_imitater():
    seeds = [seed(0, int(PlantType.SUNFLOWER)), seed(1, int(PlantType.PUFF_SHROOM), usable=False),
             seed(2, int(PlantType.FLOWER_POT))]
    assert pick_fodder(seeds) == (int(PlantType.FLOWER_POT), False)  # puff on cooldown, pot before sunflower
    imit = SimpleNamespace(slot=3, type=48, imitater_type=int(PlantType.PUFF_SHROOM), usable=True)
    assert pick_fodder([seed(0, int(PlantType.SUNFLOWER)), imit]) == (int(PlantType.PUFF_SHROOM), True)
    with pytest.raises(BackendError):
        pick_fodder([seed(0, int(PlantType.PEASHOOTER))])


def test_fodder_action_resolved_by_input_backend():
    fake = FakeObserver({1: 0}, seeds=[seed(0, int(PlantType.PEASHOOTER)), seed(2, int(PlantType.PUFF_SHROOM))])
    u32 = FakeUser32()
    be = InputBackend(None, fake, None, Safety(allow_input=True, dry_run=False), hwnd=1, user32=u32, sleep=lambda s: None)
    s = be.plant_card(None, 1, 9)
    assert s.slot == 2
    assert u32.posted[0][2:] == (50 + 50 * 2, 70)
    clk = GameClock(fake, now=fake.now, sleep=fake.sleep, frame_ms=10)
    rep = PlannedExecutor(clk, be, fodder_plan(1, 50)).run()
    assert rep.rows[0].status == "ok" and rep.failed == 0


def test_fodder_action_resolved_by_remote_backend():
    fake = FakeObserver({1: 0}, seeds=[seed(0, int(PlantType.SUNFLOWER))])
    calls = []

    class FakeActions:
        observer = fake

        def put_plant(self, row, col, plant, imitater=False):
            calls.append((row, col, int(plant), imitater))

    RemoteBackend(FakeActions()).plant_card(None, 6, 9)
    assert calls == [(6, 9, int(PlantType.SUNFLOWER), False)]
    fake2 = FakeObserver({1: 0}, seeds=[seed(0, int(PlantType.PEASHOOTER))])
    FakeActions.observer = fake2
    with pytest.raises(BackendError):
        RemoteBackend(FakeActions()).plant_card(None, 6, 9)


# --- input backend: lag, reporting, cleanup, dry_run -----------------------------------
def test_input_backend_rows_not_late_with_real_frame_sleeps():
    """The click protocol (3+ frames) is not counted as scheduling lateness:
    the row reports dispatch + action_lag_cs, not the tick after the call."""
    cobs = [plant(4, 1, 4, 47, 37)]
    fake = FakeObserver({1: 1000, 2: 3000}, seeds=[seed(3, int(PlantType.ICE_SHROOM))], plants=cobs)
    g = fake_game(fake, input=True)
    u32 = FakeUser32()
    be = InputBackend(g.session, fake, g.profile, g.safety, hwnd=0x1234, user32=u32, sleep=fake.sleep)
    assert be.action_lag_cs == 2
    from pvztools.schema.track.ast import KIND_COB

    cob = Action(wave=1, effect_cs=573, operation_cs=200, kind=KIND_COB, symbol="P", plant=47,
                 target=Target(rows=(2,), col=9.0), cannon=(2, 5), source_op=Op(kind=KIND_COB, symbol="P"))
    plan = Plan((ice_plan(1, 200).actions[0], cob), ())
    rep = PlannedExecutor(g.game_clock, be, plan).run()
    assert [r.status for r in rep.rows] == ["ok", "ok"], rep.to_text()
    assert rep.late == 0
    assert all(-2 <= r.delta <= 2 for r in rep.rows)
    assert all(r.done_cs is not None and r.done_cs > r.observed_cs for r in rep.rows)  # the protocol took frames
    # first message posted at op - lead - lag: the second DOWN lands at ~op - lead
    assert [m for (_, m, _, _) in u32.posted][:4] == [DOWN, UP, DOWN, UP]


def test_fire_cobs_has_a_frame_between_shots():
    cobs = [plant(4, 1, 4, 47, 37), plant(5, 3, 4, 47, 37)]
    fake = FakeObserver({1: 0}, plants=cobs)
    sleeps = []
    be = InputBackend(None, fake, None, Safety(allow_input=True, dry_run=False), hwnd=1, user32=FakeUser32(), sleep=sleeps.append)
    be.fire_cobs([((2, 5), (2, 9.0)), ((4, 5), (5, 9.0))])
    # shot: down, gap, up... = 3 frames each; +1 frame between the shots
    assert len(sleeps) == 7


def test_click_posts_up_when_interrupted():
    u32 = FakeUser32()

    def boom(_s):
        raise KeyboardInterrupt

    be = InputBackend(None, FakeObserver({1: 0}), None, Safety(allow_input=True, dry_run=False), hwnd=1, user32=u32, sleep=boom)
    with pytest.raises(KeyboardInterrupt):
        be.click(5, 5)
    assert [m for (_, m, _, _) in u32.posted] == [DOWN, UP]


def test_input_backend_honours_safety_dry_run():
    u32 = FakeUser32()
    be = InputBackend(None, FakeObserver({1: 0}), None, Safety(allow_input=True), hwnd=1, user32=u32, sleep=lambda s: None)
    assert be.dry_run
    be.click(5, 5)
    assert u32.posted == [] and [m for (m, _, _) in be.history] == [DOWN, UP]
    s, p = make_session()
    assert Game(s, p, input=True).safety.dry_run is False  # attach(input=True) really clicks
    assert Game(s, p).safety.dry_run is True


# --- level end / mid-wave start through Game.run_track --------------------------------
def test_run_track_returns_report_when_level_ends():
    class Ending(FakeObserver):
        def board(self):
            b = super().board()
            if self.clock > 1500:
                raise NotInLevel("board freed")
            return b

    fake = Ending({1: 1000, 2: 1601, 3: 2202})
    g = fake_game(fake)
    rep = g.run_track("P6: PP|PP", cobs="25,45,26,46", dry_run=True, wave_count=2)
    assert isinstance(rep, ExecutionReport)
    assert rep.stop_reason == "level ended"
    assert [r.status for r in rep.rows] == ["dry-run", "dry-run", "skipped", "skipped"]


def test_run_reactive_returns_log_when_level_ends():
    class Ending(FakeObserver):
        def board(self):
            b = super().board()
            if self.clock > 40:
                raise NotInLevel("board freed")
            return b

    fake = Ending({1: 10, 2: 30, 3: 50, 4: 70}, step=5)
    g = fake_game(fake, input=True)
    be = InputBackend(g.session, fake, g.profile, g.safety, hwnd=1, user32=FakeUser32(), sleep=lambda s: None)
    g.backend = lambda mode="input": be
    log = g.run_reactive("p6", stop_wave=3)
    assert log.stop_reason == "level ended" and log.polls >= 1


def test_start_mid_wave_recovers_refresh_from_countdown_and_warns():
    # true refresh of w3 at 1000, attach at 3000: countdown_init 2500, countdown 500
    fake = ConsistentObserver({1: 0, 2: 500, 3: 1000, 4: 3500}, start=3000)
    clk = GameClock(fake, now=fake.now, sleep=fake.sleep, frame_ms=10)
    calls = []

    class BE:
        mode = "remote"

        def plant_card(self, p, r, c, imitater=False):
            calls.append(fake.clock)

    rep = PlannedExecutor(clk, BE(), ice_plan(3, 2300)).run()
    rp = clk.refresh_point(3)
    assert rp == RefreshPoint(3, 1000, None, "countdown")
    assert 3298 <= calls[0] <= 3302  # w3 +2300 = tick 3300, not attach + 2300
    assert rep.rows[0].status == "ok"
    assert rep.warnings and "countdown" in rep.warnings[0]
    assert any(e["event"] == "warning" for e in rep.events)


def test_start_mid_wave_without_countdown_keeps_first_and_warns():
    fake = FakeObserver({1: 0, 2: 500, 3: 1000, 4: 3500}, start=3000)  # countdown_init 2000 < elapsed: inconsistent
    fake.refresh[4] = 3001  # countdown reads 0 -> no estimate possible
    clk = GameClock(fake, now=fake.now, sleep=fake.sleep, frame_ms=10)
    s = clk.read()
    assert clk.refresh_point(s.wave).source == "first"


def test_clock_resets_when_wave_goes_backwards():
    fake = FakeObserver({1: 0, 2: 100})
    clk = GameClock(fake, now=fake.now, sleep=fake.sleep, frame_ms=10)
    fake.clock = 150
    clk.read()
    assert 2 in clk.refresh_points
    fake.refresh = {1: 200}  # new round: wave drops to 0, counter keeps running
    clk.read()
    assert 2 not in clk.refresh_points and clk.last.wave == 0


# --- clock stall detection while busy-polling ------------------------------------------
def test_stall_detected_despite_tiny_history():
    class Stalled:
        def __init__(self):
            self.t = 0.0
            self.reads = 0

        def now(self):
            return self.t

        def sleep(self, s):
            self.t += max(s, 0.00005)

        def game(self):
            return SimpleNamespace(ui=3, frame_duration=10)

        def board(self):
            self.reads += 1
            self.t += 0.00005  # 50 us per read: 64 samples span ~3 ms
            return SimpleNamespace(clock=500, wave=1, countdown=1000, countdown_init=2000,
                                   huge_wave_countdown=0, paused=False, total_waves=20)

    obs = Stalled()
    clk = GameClock(obs, now=obs.now, sleep=obs.sleep, frame_ms=10, history=8)
    for _ in range(2000):
        clk.read()
        if clk.paused():
            break
    assert clk.paused()
    assert obs.t < PAUSE_MIN_SPAN * 3  # noticed right after the span, not never
    assert len(clk.samples) <= 8


def test_scheduler_records_counter_stall_as_pause():
    """A frozen main_counter with Board.paused == False (focus loss / dialog)
    shows up as one pause, not as a burst of zero-length pause/resume pairs."""

    class FreezeObserver(FakeObserver):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.freeze = None  # (from_wall, to_wall)

        def board(self):
            if self.freeze and self.freeze[0] <= self.wall < self.freeze[1]:
                self.wall += 0.0001
                self.reads += 1
                return SimpleNamespace(scene=2, wave=1, total_waves=20, clock=self.clock, countdown=1000,
                                       countdown_init=2000, huge_wave_countdown=0, paused=False,
                                       hp_threshold=0, wave_hp=None, level_complete=False)
            return super().board()

        def sleep(self, s):
            if self.freeze and self.freeze[0] <= self.wall < self.freeze[1]:
                self.wall += s
                return
            super().sleep(s)

    fake = FreezeObserver({1: 0})
    fake.freeze = (1.0, 3.0)
    clk = GameClock(fake, now=fake.now, sleep=fake.sleep, frame_ms=10)
    rep = PlannedExecutor(clk, SimpleNamespace(mode="remote"), ice_plan(1, 1500), dry_run=True).run()
    assert rep.rows[0].status == "dry-run" and abs(rep.rows[0].delta) <= 2
    assert len(rep.pauses) == 1 and rep.pauses[0].wall_seconds > 1.0
    kinds = [e["event"] for e in rep.events]
    assert kinds.count("pause") == 1 and kinds.count("resume") == 1


# --- reactive debounce -------------------------------------------------------------------
def test_reactive_plant_decisions_are_debounced():
    from pvztools.control.executor import Decision

    class AlwaysPlant:
        def decide(self, view):
            return [Decision("plant", {"row": 1, "col": 5, "plant": 14}, "refill")]

    class BE:
        def __init__(self):
            self.calls = []

        def put_plant(self, row, col, plant):
            self.calls.append((row, col, plant))

    fake = FakeObserver({1: 10, 2: 3000}, step=5)
    be = BE()
    r = ReactiveRunner(None, be, fake, AlwaysPlant(), high_resolution=False, sleep=lambda s: None)
    log = r.run(max_polls=20)  # 20 polls x 5 ticks = 100 cs -> 2 plants at the default 50 cs debounce
    assert len(be.calls) == 2
    assert sum(1 for e in log.entries if e.error == "debounced") == 18
    r2 = ReactiveRunner(None, BE(), FakeObserver({1: 10, 2: 3000}, step=5), AlwaysPlant(),
                        high_resolution=False, sleep=lambda s: None, plant_debounce_cs=0)
    r2.run(max_polls=5)
    assert len(r2.backend.calls) == 5


# --- CLI ---------------------------------------------------------------------------------
@pytest.fixture
def cli_game(monkeypatch):
    s, p = make_session()
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    return s


def test_cli_track_run_passes_ice_fodder_and_lead(cli_game, monkeypatch, capsys):
    calls = {}

    def fake_run(self, track, **kw):
        calls.update(kw)
        rep = ExecutionReport(mode="键控", backend="input", dry_run=True)
        rep.stop_reason = "plan exhausted"
        return rep

    monkeypatch.setattr(Game, "run_track", fake_run)
    assert main(["track", "run", "P6: I PP|PP C", "--dry-run", "--ice", "11,61", "--fodder-rows", "1,6",
                 "--lead-cs", "2", "--quiet"]) == EXIT_OK
    assert calls["ice_slots"] == ((1, 1), (6, 1)) and calls["fodder_rows"] == (1, 6) and calls["lead_cs"] == 2
    assert "warning: 已在第 3 波" in capsys.readouterr().out  # make_session board is at wave 3


def test_cli_track_run_validates_flags_before_attaching(cli_game, monkeypatch, capsys):
    monkeypatch.setattr(Game, "run_track", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(SystemExit):
        main(["track", "run", "P6: PP|PP", "--dry-run", "--late-policy", "bogus"])
    with pytest.raises(SystemExit):
        main(["track", "run", "P6: PP|PP", "--dry-run", "--tolerance", "-1"])
    with pytest.raises(SystemExit):
        main(["reactive", "--input", "--yes", "--waves", "0"])
    assert main(["track", "run", "P6: PP|PP", "--dry-run", "--ice", "zz"]) == EXIT_ERROR
    assert "ice" in capsys.readouterr().err


def test_cli_track_run_dry_run_does_not_grant_remote_or_input(cli_game, monkeypatch):
    seen = {}

    def fake_run(self, track, **kw):
        seen["remote"] = self.remote
        seen["input"] = self.input
        seen["backend"] = kw["backend"]
        return ExecutionReport(mode="键控", backend=kw["backend"], dry_run=True)

    monkeypatch.setattr(Game, "run_track", fake_run)
    assert main(["track", "run", "P6: PP|PP", "--dry-run", "--remote", "--quiet"]) == EXIT_OK
    assert seen == {"remote": False, "input": False, "backend": "remote"}
    assert main(["track", "run", "P6: PP|PP", "--dry-run", "--input", "--quiet"]) == EXIT_OK
    assert seen["input"] is False and seen["backend"] == "input"
