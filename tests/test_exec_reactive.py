"""Reactive executor + policies (DESIGN §6.2b) with scripted WorldViews."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, List, Optional

import pytest

from pvztools.control.executor.policies import (
    COB_READY,
    KIND_FIRE,
    KIND_PLANT,
    KIND_WAIT,
    CobView,
    IceRefillPolicy,
    PlantView,
    PSeriesPolicy,
    SeedView,
    WorldView,
)
from pvztools.control.executor.reactive import ReactiveError, ReactiveLog, ReactiveRunner, build_view

ICE, COFFEE, COB = 14, 35, 47


def cobs(n_ready: int, n_arming: int = 0) -> tuple:
    out = []
    for i in range(n_ready):
        out.append(CobView(i, 1 + i, 1, COB_READY, 0))
    for j in range(n_arming):
        out.append(CobView(10 + j, 1 + j, 3, 35, 900))
    return tuple(out)


def view(wave: int = 3, wave_time: int = 400, countdown: int = 2700, hp: Optional[int] = 5000,
         thr: int = 3000, ready: int = 4, arming: int = 0, **kw: Any) -> WorldView:
    return WorldView(time=10000 + wave_time, wave=wave, wave_time=wave_time, countdown=countdown,
                     hp_threshold=thr, wave_hp=hp, cobs=cobs(ready, arming), **kw)


@pytest.fixture
def policy() -> PSeriesPolicy:
    return PSeriesPolicy([(2, 9), (5, 9), (2, 8.5), (5, 8.5)])


# ---------------------------------------------------------------------------
# PSeriesPolicy
# ---------------------------------------------------------------------------

def test_fires_exactly_one_pair_when_ready_and_unactivated(policy):
    ds = policy.decide(view())
    assert len(ds) == 1 and ds[0].kind == KIND_FIRE
    fires = ds[0].args["fires"]
    assert fires == [(0, (2, 9.0)), (1, (5, 9.0))]  # two cobs, first two targets
    assert "activation pair" in ds[0].reason


def test_waits_before_activate_at(policy):
    ds = policy.decide(view(wave_time=340))
    assert ds[0].kind == KIND_WAIT and "activate_at 341" in ds[0].reason
    assert policy.decide(view(wave_time=341))[0].kind == KIND_FIRE


def test_waits_when_cobs_not_ready(policy):
    ds = policy.decide(view(ready=1, arming=3))
    assert ds[0].kind == KIND_WAIT
    assert "only 1 ready" in ds[0].reason and "next in 900" in ds[0].reason


def test_waits_when_already_activated(policy):
    # countdown clamped to 200 by the game
    assert policy.decide(view(countdown=200))[0].kind == KIND_WAIT
    # hp below threshold even though countdown not yet clamped in this sample
    assert policy.decide(view(hp=2000, countdown=2700))[0].kind == KIND_WAIT
    # countdown clamped but hp sample still high (natural wave end): still wait
    assert policy.decide(view(hp=5000, countdown=150))[0].kind == KIND_WAIT
    # hp unknown (profile lacks wave_hp): countdown alone decides
    assert policy.decide(view(hp=None, countdown=2700))[0].kind == KIND_FIRE
    assert policy.decide(view(hp=None, countdown=150))[0].kind == KIND_WAIT
    # huge-wave red text running -> activated
    assert policy.decide(view(wave=9, huge_wave_countdown=500))[0].kind == KIND_WAIT


def test_hp_factor_gates_on_scaled_threshold():
    p = PSeriesPolicy([(2, 9), (5, 9)], hp_factor=2.0)
    # hp 5000 < 2 * 3000 -> treated as activated once countdown is clamped
    assert p.decide(view(hp=5000, countdown=200))[0].kind == KIND_WAIT
    assert p.decide(view(hp=7000, countdown=2700))[0].kind == KIND_FIRE


def test_fallback_pair_when_hp_stays_above_threshold(policy):
    # first pair fired at 341, still in flight at 600
    v = view(wave_time=600, pairs_fired=1, last_fire_wave_time=341)
    d = policy.decide(v)[0]
    assert d.kind == KIND_WAIT and "in flight" in d.reason and "714" in d.reason
    # landed (341 + 373 = 714), hp still above threshold -> fallback pair, next two targets
    v = replace(v, wave_time=714)
    d = policy.decide(v)[0]
    assert d.kind == KIND_FIRE and "fallback" in d.reason
    assert d.args["fires"] == [(0, (2, 8.5)), (1, (5, 8.5))]
    # landed and activated -> nothing
    assert policy.decide(replace(v, countdown=200, wave_hp=100))[0].kind == KIND_WAIT
    # never a third pair
    assert policy.decide(replace(v, pairs_fired=2))[0].kind == KIND_WAIT


def test_fallback_targets_wrap_when_only_two_targets():
    p = PSeriesPolicy([(2, 9), (5, 9)])
    d = p.decide(view(wave_time=800, pairs_fired=1, last_fire_wave_time=341))[0]
    assert d.kind == KIND_FIRE and d.args["fires"] == [(0, (2, 9.0)), (1, (5, 9.0))]


def test_respects_max_wait(policy):
    v = view(wave_time=2500, pairs_fired=1, last_fire_wave_time=341)
    d = policy.decide(v)[0]
    assert d.kind == KIND_WAIT and "max_wait" in d.reason
    assert policy.decide(replace(v, wave_time=2499))[0].kind == KIND_FIRE


def test_huge_wave_delay(policy):
    assert policy.activation_time(10) == 682 and policy.activation_time(20) == 682
    assert policy.activation_time(9) == 341
    assert policy.decide(view(wave=10, wave_time=600, countdown=2700))[0].kind == KIND_WAIT
    assert policy.decide(view(wave=10, wave_time=682, countdown=2700))[0].kind == KIND_FIRE
    # wave 20 is the final wave: nothing to activate (total_waves=20)
    assert policy.decide(view(wave=20, wave_time=900))[0].kind == KIND_WAIT


def test_wave_zero_and_paused_wait(policy):
    assert policy.decide(view(wave=0, wave_time=-300))[0].kind == KIND_WAIT
    assert policy.decide(view(paused=True))[0].reason == "paused"


def test_policy_needs_two_targets():
    with pytest.raises(ValueError):
        PSeriesPolicy([(2, 9)])


def test_view_helpers():
    v = view(wave=9, countdown=10, huge_wave_countdown=0)
    assert v.is_flag_wave_next and v.next_refresh_in == 760
    assert replace(v, huge_wave_countdown=300).next_refresh_in == 300
    assert view(wave=3, countdown=500).next_refresh_in == 500
    assert view(countdown=2700, hp=5000, thr=3000).activated is False
    assert view(countdown=2700, hp=2000, thr=3000).activated is True


# ---------------------------------------------------------------------------
# IceRefillPolicy
# ---------------------------------------------------------------------------

def test_ice_refill_plants_ice_then_coffee_on_day():
    p = IceRefillPolicy((1, 5), window=(0, 400))
    seeds = (SeedView(0, ICE, True), SeedView(1, COFFEE, True))
    v = view(wave_time=100, scene=2, seeds=seeds)  # PE (day)
    d = p.decide(v)[0]
    assert d.kind == KIND_PLANT and d.args == {"row": 1, "col": 5, "plant": ICE}
    v2 = replace(v, plants=(PlantView(0, 1, 5, ICE),))
    d = p.decide(v2)[0]
    assert d.kind == KIND_PLANT and d.args["plant"] == COFFEE
    v3 = replace(v2, plants=v2.plants + (PlantView(1, 1, 5, COFFEE),))
    assert p.decide(v3)[0].kind == KIND_WAIT
    # night: no coffee
    assert p.decide(replace(v2, scene=1))[0].kind == KIND_WAIT
    # outside window / card cooling
    assert p.decide(replace(v, wave_time=500))[0].kind == KIND_WAIT
    assert p.decide(replace(v, seeds=(SeedView(0, ICE, False, 1200),)))[0].reason == "ice card not usable"


# ---------------------------------------------------------------------------
# ReactiveRunner with fake clock / backend / observer
# ---------------------------------------------------------------------------

@dataclass
class FakeBoard:
    clock: int
    wave: int
    countdown: int
    countdown_init: int = 2700
    hp_threshold: int = 3000
    wave_hp: Optional[int] = 5000
    huge_wave_countdown: int = 0
    total_waves: int = 20
    paused: bool = False
    scene: int = 2


@dataclass
class FakePlant:
    index: int
    row: int
    col: int
    type: int
    state: int
    state_countdown: Optional[int] = None
    dead: bool = False
    squished: bool = False


class FakeGame:
    """A scripted world: the clock advances ``tick`` cs per poll; cobs fire
    (37 -> 38) when the backend is told to; wave refreshes at given clocks."""

    def __init__(self, refresh_at: dict, tick: int = 1) -> None:
        self.t = 0
        self.tick = tick
        self.refresh_at = refresh_at  # clock -> wave
        self.board = FakeBoard(clock=0, wave=0, countdown=600)
        self.plants = [FakePlant(i, i, 0, COB, COB_READY) for i in range(4)]
        self.fired: List[Any] = []
        self.fail_next = False

    # observer
    def observe_board(self) -> FakeBoard:
        return self.board

    def observe_plants(self):
        return list(self.plants)

    # clock
    def now(self) -> int:
        return self.t

    def sleep(self, _s: float) -> None:
        self.t += self.tick
        w = self.refresh_at.get(self.t)
        if w is not None:
            self.board.wave = w
            self.board.countdown = self.board.countdown_init
            self.board.wave_hp = 5000
        elif self.board.wave > 0:
            self.board.countdown = max(0, self.board.countdown - self.tick)
        self.board.clock = self.t

    # backend
    def fire_cobs(self, fires):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom")
        for idx, _target in fires:
            self.plants[idx].state = 38
        self.fired.append((self.t, list(fires)))
        return fires


class Obs:
    def __init__(self, g: FakeGame) -> None:
        self.g = g

    def board(self):
        return self.g.observe_board()

    def plants(self):
        return self.g.observe_plants()


def test_runner_executes_and_logs():
    g = FakeGame({10: 1, 1000: 2})
    policy = PSeriesPolicy([(2, 9), (5, 9)], max_wait=400)
    r = ReactiveRunner(g, g, Obs(g), policy, poll_ms=5, high_resolution=False)
    log = r.run(stop_wave=1)  # act on w1, stop once w2 has refreshed
    # exactly one pair on wave 1, fired at wave_time 341 (clock 10 + 341)
    assert len(g.fired) == 1
    t, fires = g.fired[0]
    assert t == 351 and [i for i, _ in fires] == [0, 1]
    fire_entries = log.fires()
    assert len(fire_entries) == 1
    e = fire_entries[0]
    assert e.wave == 1 and e.wave_time == 341 and e.executed and e.error is None
    assert e.observed["ready_cobs"] == 4 and e.observed["pairs_fired"] == 0
    assert e.decision.args["fires"] == [(0, (2, 9.0)), (1, (5, 9.0))]
    # after the pair only two cobs are ready and the hp stays up -> fallback blocked by max_wait 400
    assert log.polls == 1001 and log.entries[-1].wave == 2
    waits = [x for x in log.entries if x.wave == 1 and x.wave_time == 714]
    assert waits and "fallback" in waits[0].reason
    assert "fires=1" in log.summary()
    d = log.as_dicts()[0]
    assert d["kind"] == KIND_WAIT and d["wave"] == 0 and d["wave_time"] == -600


def test_runner_wave_time_estimate_when_started_mid_wave():
    g = FakeGame({})
    g.t = 5000
    g.board = FakeBoard(clock=5000, wave=4, countdown=2000, countdown_init=2700)
    r = ReactiveRunner(g, g, Obs(g), PSeriesPolicy([(2, 9), (5, 9)]), high_resolution=False)
    v = r.view()
    assert v.wave_time == 700 and v.wave_time_exact is False
    # subsequent observed transition is exact
    g.sleep(0)
    g.board.wave = 5
    g.board.countdown = 2700
    v = r.view()
    assert v.wave_time == 0 and v.wave_time_exact is True and v.pairs_fired == 0


def test_runner_paused_skips_execution():
    g = FakeGame({1: 1})
    g.board.paused = True
    r = ReactiveRunner(g, g, Obs(g), PSeriesPolicy([(2, 9), (5, 9)]), high_resolution=False)
    log = r.run(max_polls=400)
    assert not g.fired and log.paused_polls == 400
    assert all(e.reason == "paused" for e in log.entries)


def test_runner_error_policy():
    g = FakeGame({1: 1})
    g.fail_next = True
    r = ReactiveRunner(g, g, Obs(g), PSeriesPolicy([(2, 9), (5, 9)]), high_resolution=False)
    log = r.run(until=lambda v: v.wave_time >= 345)
    errs = log.errors()
    assert len(errs) == 1 and "boom" in errs[0].error and not errs[0].executed
    # the failed fire did not count as a pair: the next poll fired for real
    assert len(g.fired) == 1 and g.fired[0][0] == 343

    g2 = FakeGame({1: 1})
    g2.fail_next = True
    r2 = ReactiveRunner(g2, g2, Obs(g2), PSeriesPolicy([(2, 9), (5, 9)]), on_error="abort",
                        high_resolution=False)
    with pytest.raises(ReactiveError):
        r2.run(max_polls=1000)


def test_build_view_fallback_wave_hp_from_zombies():
    @dataclass
    class Z:
        index: int
        row: int
        x: float
        type: int
        hp: int
        total_hp: int
        from_wave: Optional[int]
        dead: bool = False

    board = FakeBoard(clock=1, wave=3, countdown=2000, wave_hp=None)
    zs = [Z(0, 1, 700.0, 2, 100, 370, 2), Z(1, 2, 700.0, 2, 100, 270, 1), Z(2, 2, 700.0, 2, 50, 50, 2, dead=True)]
    v = build_view(board, [FakePlant(0, 0, 0, COB, 35, 100)], [], zs, wave_time=50)
    assert v.wave_hp == 370 and len(v.zombies) == 2
    assert v.cobs[0].ready_in == 575 and v.cobs[0].row == 1 and not v.cobs[0].ready


def test_reactive_log_drops_waits_when_asked():
    log = ReactiveLog(keep_waits=False)
    g = FakeGame({1: 1})
    r = ReactiveRunner(g, g, Obs(g), PSeriesPolicy([(2, 9), (5, 9)]), log=log, high_resolution=False)
    r.run(max_polls=400)
    assert len(log) == 1 and log.entries[0].kind == KIND_FIRE and log.polls == 400
