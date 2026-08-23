"""Integration of the executor into Game / CLI (run_track, run_reactive,
bench_clock, backends, gating) with fake sessions / observers."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from pvztools import game as game_mod
from pvztools.cli.__main__ import EXIT_ERROR, EXIT_GAME, EXIT_OK, main
from pvztools.control.executor import (
    GameClock, PlannedExecutor, PolicyBackend, bench_clock, format_bench, make_policy,
)
from pvztools.control.executor.bench import BenchResult
from pvztools.control.executor.report import ExecutionReport
from pvztools.control.input_backend import InputBackend
from pvztools.game import Game, PermissionDenied, _parse_cells
from pvztools.modifier import Safety
from pvztools.schema.enums import PlantType
from pvztools.schema.track.ast import KIND_ICE, Op, Target
from pvztools.schema.track.expand import Action, Plan
from test_game_api import make_session

DOWN, UP = 0x0201, 0x0202


# --- fakes ------------------------------------------------------------------
class FakeUser32:
    WM_LBUTTONDOWN = DOWN
    WM_LBUTTONUP = UP

    def __init__(self) -> None:
        self.posted: List[Any] = []

    def find_game_window(self, pid=None):
        return 0x1234

    def post_message(self, hwnd, msg, wparam=0, lparam=0):
        self.posted.append((hwnd, msg, lparam & 0xFFFF, lparam >> 16))


class FakeObserver:
    """Board whose clock advances ``step`` ticks per read; waves refresh at
    the counters in ``refresh``; ``now`` is a fake wall clock in step."""

    def __init__(self, refresh: Dict[int, int], *, scene: int = 2, step: int = 1,
                 seeds: Optional[List[Any]] = None, plants: Optional[List[Any]] = None,
                 ui: int = 3, total_waves: int = 20, start: int = 0) -> None:
        self.refresh = dict(refresh)
        self.scene = scene
        self.step = step
        self.clock = start
        self.wall = 0.0
        self.ui = ui
        self.total_waves = total_waves
        self._seeds = seeds or []
        self._plants = plants or []
        self.reads = 0

    # fake wall clock / sleep in lock step with the game clock
    def now(self) -> float:
        return self.wall

    def sleep(self, s: float) -> None:
        self.wall += s
        self.clock += int(round(s * 100))

    def game(self):
        return SimpleNamespace(ui=self.ui, frame_duration=10)

    def _wave(self) -> int:
        w = 0
        for k, c in self.refresh.items():
            if c <= self.clock and k > w:
                w = k
        return w

    def board(self):
        self.reads += 1
        self.clock += self.step
        self.wall += 0.0001
        w = self._wave()
        nxt = self.refresh.get(w + 1)
        cd = max(nxt - self.clock, 0) if nxt is not None else 0
        return SimpleNamespace(
            scene=self.scene, wave=w, total_waves=self.total_waves, clock=self.clock,
            countdown=cd, countdown_init=2000, huge_wave_countdown=0, paused=False,
            hp_threshold=0, wave_hp=None, level_complete=False, global_clock=self.clock,
        )

    def seeds(self):
        return tuple(self._seeds)

    def plants(self, include_dead: bool = True):
        return tuple(self._plants)

    def zombies(self, include_dead: bool = True):
        return ()


def seed(slot, typ, usable=True):
    return SimpleNamespace(slot=slot, type=typ, imitater_type=-1, cd_elapsed=0, cd_total=750, usable=usable)


def plant(index, row0, col0, typ, state=0):
    return SimpleNamespace(index=index, row=row0, col=col0, type=typ, state=state, dead=False,
                           squished=False, state_countdown=0)


def fake_game(fake: FakeObserver, **kw) -> Game:
    s, p = make_session()
    g = Game(s, p, **kw)
    g._observer = fake
    g._clock_obj = GameClock(fake, now=fake.now, sleep=fake.sleep, frame_ms=10)
    return g


def ice_plan(wave: int, cs: int, row: int = 1, col: int = 1) -> Plan:
    op = Op(kind=KIND_ICE, symbol="I", target=Target(rows=(row,), col=float(col)))
    a = Action(wave=wave, effect_cs=cs, operation_cs=cs, kind=KIND_ICE, symbol="I",
               plant=int(PlantType.ICE_SHROOM), target=Target(rows=(row,), col=float(col)),
               cannon=None, source_op=op)
    return Plan(actions=(a,), issues=())


# --- gating / construction ----------------------------------------------------
def test_attach_input_flag_sets_safety():
    s, p = make_session()
    g = Game(s, p, input=True)
    assert g.safety.allow_input and g.input and not g.writable
    assert isinstance(g.backend("input"), InputBackend)
    with pytest.raises(PermissionDenied):
        g.backend("remote")
    g2 = Game(s, p)
    assert not g2.input
    with pytest.raises(PermissionDenied) as exc:
        g2.backend("input")
    assert exc.value.flag == "allow_input"
    with pytest.raises(ValueError):
        g.backend("nope")


def test_attach_passes_input(monkeypatch):
    s, p = make_session()
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    g = game_mod.attach(input=True, profile=p)
    assert g.input and g.safety.allow_input


def test_parse_cells():
    assert _parse_cells("25,45") == [(2, 5), (4, 5)]
    assert _parse_cells(["2-8.75", (1, 9)]) == [(2, 8.75), (1, 9)]
    with pytest.raises(ValueError):
        _parse_cells(["x"])


def test_expand_context_from_board():
    s, p = make_session()
    g = Game(s, p)
    ctx = g.expand_context()
    assert ctx.scene == "PE" and ctx.wave_count == 20 and ctx.cob_positions == ()
    ctx = g.expand_context(scene="de", cobs="25,45", ice_slots=((1, 1),))
    assert ctx.scene == "DE" and ctx.cob_positions == ((2, 5), (4, 5)) and ctx.ice_slots == ((1, 1),)
    plan = g.plan_track("P6: PP|PP", cobs="25,45,26,46")
    assert len(plan.actions) == 40 and all(a.kind == "cob" for a in plan.actions)  # 2-wave cycle x 20
    assert len(g.plan_track("P6: PP|PP", cobs="25,45,26,46", wave_count=2).actions) == 4


# --- run_track -----------------------------------------------------------------
def test_run_track_dry_run_without_flags_waits_on_fake_clock():
    fake = FakeObserver({1: 1000, 2: 1601, 3: 2202})
    g = fake_game(fake)
    rep = g.run_track("P6: PP|PP", cobs="25,45,26,46", dry_run=True, wave_count=2)
    assert isinstance(rep, ExecutionReport)
    assert rep.stop_reason == "plan exhausted" and rep.dry_run and rep.backend == "input"
    assert [r.status for r in rep.rows] == ["dry-run"] * 4
    assert all(abs(r.delta) <= 2 for r in rep.rows)
    assert rep.refresh_points.get(1) == 1000  # w2 ops are pre-refresh (-32cs): w2 never observed


def test_run_track_input_backend_posts_clicks(monkeypatch):
    fake = FakeObserver({1: 1000, 2: 1601}, seeds=[seed(0, int(PlantType.SUNFLOWER)), seed(3, int(PlantType.ICE_SHROOM))])
    g = fake_game(fake, input=True)
    u32 = FakeUser32()
    be = InputBackend(g.session, fake, g.profile, g.safety, hwnd=0x1234, user32=u32, sleep=lambda s: None)
    monkeypatch.setattr(g, "backend", lambda mode="input": be)
    monkeypatch.setattr(g, "plan_track", lambda *a, **k: ice_plan(1, 200, row=1, col=1))
    rep = g.run_track("ignored", backend="input")
    row = rep.rows[0]
    assert row.status == "ok", row
    assert row.planned_cs == 200 and 0 <= row.delta <= 2
    # card (slot 3) then cell: DOWN/UP pairs with lParam = MAKELPARAM(x, y)
    msgs = [m for (_, m, _, _) in u32.posted]
    assert msgs == [DOWN, UP, DOWN, UP]
    assert u32.posted[0][2:] == (50 + 50 * 3, 70)
    assert rep.mode == "键控"


def test_run_track_remote_backend_uses_put_plant(monkeypatch):
    fake = FakeObserver({1: 1000, 2: 1601})
    g = fake_game(fake, write=True, remote=True)
    calls = []

    class FakeActions:
        observer = fake

        def put_plant(self, row, col, plant, imitater=False):
            calls.append(("put_plant", row, col, int(plant), imitater))
            return 7

    from pvztools.control.backends import RemoteBackend

    monkeypatch.setattr(g, "backend", lambda mode="input": RemoteBackend(FakeActions()))
    monkeypatch.setattr(g, "plan_track", lambda *a, **k: ice_plan(1, 100, row=2, col=3))
    rep = g.run_track("ignored", backend="remote")
    assert calls == [("put_plant", 2, 3, int(PlantType.ICE_SHROOM), False)]
    assert rep.rows[0].status == "ok" and rep.mode == "修改器"


def test_scheduler_passes_plant_type_not_slot():
    """Plant id 1 (sunflower) must not be read as seed slot 1."""
    from pvztools.control.executor.scheduler import PlannedExecutor

    fake = FakeObserver({1: 1000}, seeds=[seed(0, 1), seed(1, 14)])
    clk = GameClock(fake, now=fake.now, sleep=fake.sleep, frame_ms=10)
    u32 = FakeUser32()
    be = InputBackend(None, fake, None, Safety(allow_input=True, dry_run=False), hwnd=1, user32=u32, sleep=lambda s: None)
    op = Op(kind="card", symbol="S", target=Target(rows=(1,), col=9.0))
    a = Action(wave=1, effect_cs=50, operation_cs=50, kind="card", symbol="S", plant=1,
               target=Target(rows=(1,), col=9.0), cannon=None, source_op=op)
    rep = PlannedExecutor(clk, be, Plan((a,), ())).run()
    # the fake advances one tick per board read, so the backend's own reads
    # make the observed time a few ticks late; what matters is the slot
    assert rep.rows[0].status in ("ok", "late"), rep.rows[0]
    assert u32.posted[0][2:] == (50, 70)  # slot 0 (sunflower), not slot 1


def test_run_track_requires_level():
    s, p = make_session(in_level=False)
    g = Game(s, p, input=True)
    from pvztools.observe import NotInLevel

    with pytest.raises(NotInLevel):
        g.run_track("P6: PP|PP", dry_run=True)


# --- reactive -------------------------------------------------------------------
def test_policy_backend_adapter():
    fake = FakeObserver({1: 0}, plants=[plant(4, 1, 4, 47, 37), plant(5, 3, 4, 47, 37), plant(6, 0, 0, 1)])
    calls = []

    class BE:
        mode = "input"

        def fire_cob(self, cob, r, c):
            calls.append(("fire_cob", cob, r, c))

        def fire_cobs(self, fires):
            calls.append(("fire_cobs", fires))

        def plant_card(self, p, r, c, imitater=False):
            calls.append(("plant_card", p, r, c, imitater))

        def shovel(self, r, c):
            calls.append(("shovel", r, c))

    pb = PolicyBackend(BE(), fake)
    assert pb.mode == "input"
    pb.fire_cobs([(4, (2, 9))])
    pb.fire_cobs([(4, (2, 9)), (5, (5, 9.0))])
    pb.put_plant(1, 1, 1)
    pb.remove_plant(1, 1)
    assert calls[0] == ("fire_cob", (2, 5), 2, 9.0)
    assert calls[1] == ("fire_cobs", [((2, 5), (2, 9.0)), ((4, 5), (5, 9.0))])
    assert calls[2][1] is PlantType.SUNFLOWER and calls[2][2:] == (1, 1, False)
    assert calls[3] == ("shovel", 1, 1)
    with pytest.raises(ValueError):
        pb.fire_cobs([(6, (1, 9))])
    with pytest.raises(ValueError):
        pb.fire_cobs([(99, (1, 9))])


def test_make_policy_names():
    pol = make_policy("P6", [(2, 9), (5, 9)])
    assert pol.min_wave_len == 601 and pol.cob_targets == ((2, 9.0), (5, 9.0))
    assert make_policy("p4", [(2, 9), (5, 9)]).min_wave_len == 869
    with pytest.raises(ValueError):
        make_policy("zzz", [(2, 9), (5, 9)])


def test_run_reactive_stops_at_wave_and_logs(monkeypatch):
    fake = FakeObserver({1: 10, 2: 30, 3: 50, 4: 70}, step=5)
    g = fake_game(fake, input=True)
    u32 = FakeUser32()
    be = InputBackend(g.session, fake, g.profile, g.safety, hwnd=1, user32=u32, sleep=lambda s: None)
    monkeypatch.setattr(g, "backend", lambda mode="input": be)
    seen = []

    class Policy:
        def decide(self, view):
            seen.append((view.wave, view.wave_time))
            from pvztools.control.executor import Decision

            return [Decision.wait("noop")]

    # duration_waves=2 from wave 0: acts on w1 and w2, stops once w3 has refreshed
    log = g.run_reactive(Policy(), duration_waves=2)
    assert log.polls >= 2 and seen and seen[-1][0] == 3
    assert any(w == 2 and wt > 0 for w, wt in seen)  # the stop wave itself was acted on
    assert log.stop_reason == "wave 3 reached"
    assert u32.posted == []
    # default targets / named policy path
    pol_log = g.run_reactive("p6", stop_wave=3)
    assert pol_log.polls >= 1 and pol_log.stop_reason == "wave 4 reached"


# --- bench -----------------------------------------------------------------------
def test_bench_clock_fake_observer():
    fake = FakeObserver({1: 0}, step=1)  # a read advances one tick (the spin phase needs progress)
    res = bench_clock(fake, seconds=1.0, step_cs=20, clock=GameClock(fake, now=fake.now, sleep=fake.sleep, frame_ms=10),
                      now=fake.now, poll_samples=5, use_high_resolution=False)
    assert res.in_level and len(res.poll_ms) == 5 and res.deltas
    assert all(d >= 0 for d in res.deltas)
    s = res.summary()
    assert s["jitter_ticks"]["count"] == len(res.deltas) and s["histogram"]
    text = format_bench(res)
    assert "poll latency" in text and "histogram" in text


def test_bench_clock_not_in_level():
    fake = FakeObserver({1: 0}, ui=1)
    res = bench_clock(fake, seconds=1.0, now=fake.now, use_high_resolution=False)
    assert not res.in_level and not res.deltas
    assert "跳过" in format_bench(res)


def test_game_bench_clock_method(monkeypatch):
    fake = FakeObserver({1: 0})
    g = fake_game(fake)
    res = g.bench_clock(0.2, poll_samples=3, now=fake.now, use_high_resolution=False)
    assert isinstance(res, BenchResult) and res.in_level and len(res.poll_ms) == 3


# --- CLI -------------------------------------------------------------------------
@pytest.fixture
def cli_game(monkeypatch):
    s, p = make_session()
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    return s


def test_cli_track_run_gating(capsys):
    assert main(["track", "run", "P6: PP|PP"]) == EXIT_ERROR
    assert "--input" in capsys.readouterr().err
    assert main(["track", "run", "P6: PP|PP", "--input"]) == EXIT_ERROR
    assert "--yes" in capsys.readouterr().err
    assert main(["track", "run", "P6: PP|PP", "--input", "--remote", "--yes"]) == EXIT_ERROR
    assert "二选一" in capsys.readouterr().err
    assert main(["reactive", "--input"]) == EXIT_ERROR
    assert main(["reactive"]) == EXIT_ERROR


def test_cli_track_run_dry_run(cli_game, monkeypatch, capsys, tmp_path):
    calls = {}

    def fake_run(self, track, **kw):
        calls.update(kw)
        rep = ExecutionReport(mode="键控", backend="input", dry_run=True, plan_actions=1)
        rep.stop_reason = "plan exhausted"
        return rep

    monkeypatch.setattr(Game, "run_track", fake_run)
    out = tmp_path / "r.json"
    assert main(["track", "run", "P6: PP|PP", "--dry-run", "--cobs", "25,45", "--report", str(out)]) == EXIT_OK
    text = capsys.readouterr().out
    assert "dry-run" in text and "stop=plan exhausted" in text and "report written" in text
    assert calls["dry_run"] is True and calls["backend"] == "input" and calls["cobs"] == "25,45"
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["dry_run"] is True


def test_cli_track_run_input_flag_reaches_attach(cli_game, monkeypatch, capsys):
    seen = {}

    def fake_run(self, track, **kw):
        seen["input"] = self.input
        seen["backend"] = kw["backend"]
        return ExecutionReport(mode="键控", backend="input")

    monkeypatch.setattr(Game, "run_track", fake_run)
    assert main(["track", "run", "P6: PP|PP", "--input", "--yes", "--quiet"]) == EXIT_OK
    assert seen == {"input": True, "backend": "input"}


def test_cli_bench_clock(cli_game, monkeypatch, capsys):
    res = BenchResult(seconds=1.0, step_cs=50, frame_ms=10, poll_ms=[0.1, 0.2], deltas=[0, 0, 1])
    monkeypatch.setattr(Game, "bench_clock", lambda self, seconds, **kw: res)
    assert main(["bench-clock", "--seconds", "1"]) == EXIT_OK
    assert "target jitter n=3" in capsys.readouterr().out
    assert main(["bench-clock", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["jitter_ticks"]["count"] == 3


def test_cli_bench_clock_not_in_level(monkeypatch, capsys):
    s, p = make_session(in_level=False, ui=1)
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    assert main(["bench-clock"]) == EXIT_GAME
    assert "跳过" in capsys.readouterr().out


def test_cli_reactive(cli_game, monkeypatch, capsys):
    from pvztools.control.executor import ReactiveLog

    seen = {}

    def fake_run(self, policy, cobs, waves, **kw):
        seen.update(policy=policy, cobs=cobs, waves=waves, backend=kw["backend"])
        return ReactiveLog()

    monkeypatch.setattr(Game, "run_reactive", fake_run)
    assert main(["reactive", "--policy", "p6", "--cobs", "29,59", "--waves", "3", "--input", "--yes"]) == EXIT_OK
    assert seen == {"policy": "p6", "cobs": "29,59", "waves": 3, "backend": "input"}
    assert main(["reactive", "--input", "--yes", "--dry-run"]) == EXIT_ERROR


def test_help_lists_executor_commands(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "bench-clock" in out and "reactive" in out
