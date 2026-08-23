"""Tests for control.executor.scheduler / report with a fake clock + backend."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pytest

from pvztools.control.executor.report import ExecutionReport, jitter_stats
from pvztools.control.executor.scheduler import (
    STATUS_LATE,
    STATUS_OK,
    STATUS_SKIPPED,
    ExecutionAbort,
    PlannedExecutor,
)
from pvztools.schema.enums import PlantType
from pvztools.schema.track.ast import KIND_COB, KIND_FODDER, KIND_ICE, Diagnostic, Op, Target
from pvztools.schema.track.expand import KIND_COFFEE, Action, Plan


# --- fakes ------------------------------------------------------------------
@dataclass
class Sample:
    clock: int
    wave: int
    paused: bool = False
    level_complete: bool = False


class FakeClock:
    """Deterministic clock: every read() advances ``step`` ticks unless paused.

    ``refresh``: wave -> main_counter at which that wave refreshes.
    ``pause_at``: (clock, polls) -> when the clock reaches ``clock`` it stays
    paused (frozen) for ``polls`` reads.
    """

    def __init__(self, refresh: Dict[int, int], step: int = 1, pause_at: Optional[Tuple[int, int]] = None, complete_at: Optional[int] = None):
        self.refresh = dict(refresh)
        self.step = step
        self.clock = -1
        self.pause_at = pause_at
        self.pause_left = 0
        self.pause_done = False
        self.complete_at = complete_at
        self.reads = 0
        self.waits = 0

    def _wave(self) -> int:
        w = 0
        for k, c in self.refresh.items():
            if c <= self.clock and k > w:
                w = k
        return w

    def read(self) -> Sample:
        self.reads += 1
        if self.pause_left > 0:
            self.pause_left -= 1
            return Sample(self.clock, self._wave(), paused=True)
        self.clock += self.step
        if self.pause_at and not self.pause_done and self.clock >= self.pause_at[0]:
            self.pause_done = True
            self.pause_left = self.pause_at[1]
            return Sample(self.clock, self._wave(), paused=True)
        done = self.complete_at is not None and self.clock >= self.complete_at
        return Sample(self.clock, self._wave(), level_complete=done)

    def wave_time(self, sample: Sample, wave: int) -> Optional[int]:
        if wave not in self.refresh:
            return None
        return sample.clock - self.refresh[wave]

    def time_to_next_refresh(self, sample: Sample) -> Optional[int]:
        nxt = sample.wave + 1
        if nxt not in self.refresh:
            return None
        return self.refresh[nxt] - sample.clock

    def paused(self) -> bool:  # same name as GameClock.paused()
        return self.pause_left > 0

    def refresh_point(self, wave):
        return None

    def wait_until(self, pred, timeout: float, eta=None) -> bool:
        self.waits += 1
        for _ in range(100000):
            if pred(self.read()):
                return True
        raise RuntimeError("fake clock ran away")


class FakeBackend:
    def __init__(self, clock: FakeClock, mode: str = "remote"):
        self.clock = clock
        self.mode = mode
        self.calls: List[Tuple[str, tuple, int]] = []

    def _rec(self, name, *args):
        self.calls.append((name, args, self.clock.clock))

    def plant_card(self, plant, row, col, imitater=False):
        self._rec("plant_card", plant, row, col, imitater)

    def shovel(self, row, col):
        self._rec("shovel", row, col)

    def fire_cob(self, cannon, row, col):
        self._rec("fire_cob", cannon, row, col)

    def fire_cobs(self, fires):
        self._rec("fire_cobs", tuple(fires))


def cob(wave, op_cs, cannon, row=2, col=9.0, symbol="P"):
    return Action(wave, op_cs + 373, op_cs, KIND_COB, symbol, int(PlantType.COB_CANNON), Target((row,), col), cannon, Op(symbol, KIND_COB))


def ice(wave, op_cs, row=1, col=1):
    return Action(wave, op_cs + 100, op_cs, KIND_ICE, "I", int(PlantType.ICE_SHROOM), Target((row,), float(col)), None, Op("I", KIND_ICE))


def fodder(wave, op_cs, row=1, col=9, shovel_after=None):
    win = (op_cs, op_cs + shovel_after) if shovel_after else None
    op = Op("C", KIND_FODDER, window_cs=win, effect_cs=op_cs)
    return Action(wave, op_cs, op_cs, KIND_FODDER, "C", None, Target((row,), float(col)), None, op)


def coffee(wave, op_cs, row=1, col=1):
    return Action(wave, op_cs + 299, op_cs, KIND_COFFEE, "K", int(PlantType.COFFEE_BEAN), Target((row,), float(col)), None, Op("I", KIND_ICE))


THREE_WAVES = {1: 0, 2: 1000, 3: 2000, 4: 3000}


def run(plan, clock, backend=None, **kw):
    backend = backend or FakeBackend(clock)
    ex = PlannedExecutor(clock, backend, plan, **kw)
    events = []
    ex.on_event(events.append)
    rep = ex.run()
    return rep, backend, events


# --- tests ------------------------------------------------------------------
def test_three_wave_plan_in_order_within_tolerance():
    plan = Plan((cob(1, 300, (1, 1)), fodder(2, 134, shovel_after=200), cob(3, 300, (2, 1))), ())
    clock = FakeClock(THREE_WAVES, complete_at=3500)
    rep, be, events = run(plan, clock)
    names = [c[0] for c in be.calls]
    assert names == ["fire_cob", "plant_card", "shovel", "fire_cob"]
    ticks = [c[2] for c in be.calls]
    assert ticks == sorted(ticks)
    assert [r.status for r in rep.rows] == [STATUS_OK] * 4
    assert all(abs(r.delta) <= 2 for r in rep.rows)
    # dispatch never earlier than planned - lead
    planned = [300, 1134, 1334, 2300]
    for tick, p in zip(ticks, planned):
        assert p - 1 <= tick <= p + 2
    assert rep.refresh_points == {2: 1000, 3: 2000}
    assert rep.stop_reason == "plan exhausted"
    assert rep.skipped == 0 and rep.late == 0
    assert [e["event"] for e in events][0] == "start" and events[-1]["event"] == "stop"


def test_same_tick_pp_is_one_fire_cobs_call():
    plan = Plan((cob(1, 300, (1, 1), row=2), cob(1, 300, (3, 1), row=5), cob(1, 600, (5, 1))), ())
    clock = FakeClock(THREE_WAVES)
    rep, be, _ = run(plan, clock)
    assert [c[0] for c in be.calls] == ["fire_cobs", "fire_cob"]
    fires = be.calls[0][1][0]
    assert fires == (((1, 1), (2, 9.0)), ((3, 1), (5, 9.0)))
    assert rep.rows[0].observed_cs == rep.rows[1].observed_cs


def test_pre_refresh_ice_waits_on_time_to_next_refresh():
    plan = Plan((ice(2, -89),), ())
    clock = FakeClock(THREE_WAVES)
    rep, be, _ = run(plan, clock)
    assert be.calls[0][0] == "plant_card"
    tick = be.calls[0][2]
    # dispatched when time_to_next_refresh <= 89 (+lead), i.e. clock ~ 911, still in wave 1
    assert 909 <= tick <= 913
    assert tick < THREE_WAVES[2]
    assert rep.rows[0].status == STATUS_OK
    assert rep.rows[0].observed_cs is not None and -91 <= rep.rows[0].observed_cs <= -87


def test_pause_suspends_without_late_flags():
    plan = Plan((cob(1, 500, (1, 1)),), ())
    clock = FakeClock(THREE_WAVES, pause_at=(450, 300))
    rep, be, events = run(plan, clock)
    assert rep.rows[0].status == STATUS_OK
    assert rep.late == 0
    assert len(rep.pauses) == 1 and rep.pauses[0].clock == 450
    kinds = [e["event"] for e in events]
    assert "pause" in kinds and "resume" in kinds
    assert be.calls[0][2] in (499, 500, 501)


def _late_plan():
    # clock jumps 50 ticks per poll: op at 300 will be seen first at >= 302
    return Plan((cob(1, 300, (1, 1)), cob(1, 800, (2, 1))), ())


def test_late_policy_warn_dispatches_and_flags():
    rep, be, _ = run(_late_plan(), FakeClock(THREE_WAVES, step=50))
    assert len(be.calls) == 2
    assert rep.rows[0].status == STATUS_LATE and rep.late >= 1


def test_late_policy_skip_skips():
    rep, be, events = run(_late_plan(), FakeClock(THREE_WAVES, step=50), late_policy="skip")
    assert [r.status for r in rep.rows] == [STATUS_SKIPPED, STATUS_SKIPPED]  # both seen late at step 50
    assert rep.skipped == 2 and rep.late == 2 and be.calls == []
    assert "skip" in [e["event"] for e in events]


def test_late_policy_abort_stops_run():
    rep, be, _ = run(_late_plan(), FakeClock(THREE_WAVES, step=50), late_policy="abort")
    assert be.calls == []
    assert rep.stop_reason.startswith("abort")
    assert rep.rows[0].status == "aborted" and rep.rows[1].status == STATUS_SKIPPED


def test_wave_time_axis_stays_valid_after_early_refresh():
    """w2 refreshes at 1000 and w3 already at 1001 (activation): the w2 +500
    step is still dispatched on w2's axis (clock 1500), then the w3 step."""
    plan = Plan((cob(2, 500, (1, 1)), cob(3, 100, (2, 1))), ())
    clock = FakeClock({1: 0, 2: 1000, 3: 1001, 4: 3000})
    rep, be, _ = run(plan, clock)
    assert [r.status for r in rep.rows] == [STATUS_OK, STATUS_OK]
    # chronological: w3 +100 (tick ~1101) comes before w2 +500 (tick ~1500)
    assert [c[1][0] for c in be.calls] == [(2, 1), (1, 1)]
    assert 1100 <= be.calls[0][2] <= 1102 and 1498 <= be.calls[1][2] <= 1501
    assert rep.skipped == 0


def test_early_refresh_keeps_late_cobs_and_fodder_shovel():
    """Plan built for a 1000 cs wave; w2 refreshes at 900 (activation).  The
    w1 +950 PP and the fodder shovel (w1 +700 + 400 window) are dispatched,
    not skipped; the PP is *late* only against its own planned tick."""
    plan = Plan((cob(1, 950, (1, 1)), cob(1, 950, (2, 1)), fodder(1, 700, shovel_after=400), cob(2, 300, (1, 1))), ())
    clock = FakeClock({1: 0, 2: 900, 3: 2000, 4: 3000})
    rep, be, _ = run(plan, clock)
    names = [c[0] for c in be.calls]
    assert names == ["plant_card", "fire_cobs", "shovel", "fire_cob"]
    ticks = [c[2] for c in be.calls]
    assert ticks == sorted(ticks)
    assert abs(ticks[1] - 950) <= 2 and abs(ticks[2] - 1100) <= 2 and abs(ticks[3] - 1200) <= 2
    assert rep.skipped == 0 and rep.late == 0
    assert all(r.status == STATUS_OK for r in rep.rows)


def test_unknown_refresh_point_skips():
    # the clock never observes w2 (w1 -> w3 in one poll): w2's axis is unknown
    plan = Plan((cob(2, 500, (1, 1)), cob(3, 100, (2, 1))), ())
    clock = FakeClock({1: 0, 3: 1001, 4: 3000})
    rep, be, _ = run(plan, clock)
    assert rep.rows[0].status == STATUS_SKIPPED and "unknown" in rep.rows[0].note
    assert rep.rows[1].status == STATUS_OK
    assert [c[0] for c in be.calls] == ["fire_cob"]


def test_pre_refresh_step_of_next_wave_precedes_late_step_of_this_wave():
    """(w1 fodder 1700 + shovel 400 -> 2100) sorts before (w2 -200) but the ice
    is due first (tick 1800 on a 2000 cs wave)."""
    plan = Plan((fodder(1, 1700, shovel_after=400), ice(2, -200)), ())
    clock = FakeClock({1: 0, 2: 2000, 3: 4000, 4: 6000})
    rep, be, _ = run(plan, clock)
    names = [c[0] for c in be.calls]
    assert names == ["plant_card", "plant_card", "shovel"]
    ticks = [c[2] for c in be.calls]
    assert abs(ticks[0] - 1700) <= 2 and abs(ticks[1] - 1800) <= 2 and abs(ticks[2] - 2100) <= 2
    assert all(r.status == STATUS_OK for r in rep.rows), rep.to_text()
    assert rep.late == 0 and rep.skipped == 0


def test_eta_hint_is_one_tick_short():
    """The ETA handed to the clock stops one frame before the target so the
    last tick is busy-polled (DESIGN §6), never a full-frame sleep."""
    seen = []

    class EtaClock(FakeClock):
        def wait_until(self, pred, timeout, eta=None):
            s = self.read()
            if eta is not None:
                seen.append((s.clock, eta(s)))
            return super().wait_until(pred, timeout, eta)

    clock = EtaClock(THREE_WAVES)
    run(Plan((cob(1, 500, (1, 1)),), ()), clock)
    tick, secs = seen[0]
    # target tick = 500 - lead 1 = 499; hint = (499 - tick - 1) frames
    assert abs(secs - (499 - tick - 1) * 0.01) < 1e-9


def test_backend_action_lag_dispatches_early_and_reports_action_tick():
    class LagBackend(FakeBackend):
        action_lag_cs = 2

    clock = FakeClock(THREE_WAVES)
    be = LagBackend(clock, mode="input")
    rep, _, _ = run(Plan((cob(1, 500, (1, 1)),), ()), clock, backend=be)
    # dispatched at 500 - lead 1 - lag 2 = 497 (+1: the fake advances per read);
    # the action is reported at dispatch + lag, not after the click protocol
    assert 497 <= be.calls[0][2] <= 498
    assert rep.rows[0].observed_cs == be.calls[0][2] + 2 and -1 <= rep.rows[0].delta <= 0
    assert rep.rows[0].status == STATUS_OK and rep.late == 0


def test_level_ended_not_in_level_returns_report():
    from pvztools.observe import NotInLevel

    class EndingClock(FakeClock):
        def read(self):
            s = super().read()
            if s.clock >= 1500:
                raise NotInLevel("board freed")
            return s

    rep, be, _ = run(Plan((cob(1, 300, (1, 1)), cob(2, 800, (2, 1))), ()), EndingClock(THREE_WAVES))
    assert rep.stop_reason == "level ended"
    assert [r.status for r in rep.rows] == [STATUS_OK, STATUS_SKIPPED]
    assert len(be.calls) == 1


def test_start_mid_wave_warns():
    class EstimatingClock(FakeClock):
        def refresh_point(self, wave):
            from pvztools.control.executor.clock import RefreshPoint
            return RefreshPoint(wave, self.refresh[wave], None, "countdown") if wave == 1 else None

    clock = EstimatingClock(THREE_WAVES)
    rep, _, events = run(Plan((cob(1, 300, (1, 1)),), ()), clock)
    assert len(rep.warnings) == 1 and "wave 1" in rep.warnings[0]
    assert any(e["event"] == "warning" for e in events)
    assert "warning:" in rep.to_text()


def test_remote_shovel_counts_as_modifier():
    class NoPlantBackend(FakeBackend):
        def plant_card(self, *a, **k):
            raise RuntimeError("no card")

    clock = FakeClock(THREE_WAVES)
    rep, be, _ = run(Plan((fodder(1, 200, shovel_after=100),), ()), clock, backend=NoPlantBackend(clock, mode="remote"))
    assert [r.status for r in rep.rows] == ["failed", STATUS_OK]
    assert rep.mode == "修改器"


def test_dry_run_mode_label_follows_backend():
    clock = FakeClock(THREE_WAVES)
    rep, _, _ = run(Plan((ice(1, 300),), ()), clock, backend=FakeBackend(clock, mode="remote"), dry_run=True)
    assert rep.mode == "修改器"
    clock = FakeClock(THREE_WAVES)
    rep, _, _ = run(Plan((cob(1, 300, (1, 1)),), ()), clock, backend=FakeBackend(clock, mode="remote"), dry_run=True)
    assert rep.mode == "键控"


def test_level_complete_stops():
    plan = Plan((cob(1, 300, (1, 1)), cob(3, 300, (2, 1))), ())
    rep, be, _ = run(plan, FakeClock(THREE_WAVES, complete_at=1500))
    assert rep.stop_reason == "level complete"
    assert len(be.calls) == 1
    assert rep.rows[1].status == STATUS_SKIPPED


def test_keyboard_interrupt_is_clean_stop():
    class InterruptingClock(FakeClock):
        def wait_until(self, pred, timeout, eta=None):
            raise KeyboardInterrupt

    rep, be, _ = run(Plan((cob(1, 300, (1, 1)),), ()), InterruptingClock(THREE_WAVES))
    assert rep.stop_reason == "interrupted"
    assert be.calls == []


def test_dry_run_calls_nothing():
    rep, be, _ = run(Plan((cob(1, 300, (1, 1)), coffee(1, 400)), ()), FakeClock(THREE_WAVES), dry_run=True)
    assert be.calls == []
    assert [r.status for r in rep.rows] == ["dry-run", "dry-run"]


def test_coffee_and_imitater_cards():
    imit_ice = Action(1, 400, 100, KIND_ICE, "I'", int(PlantType.ICE_SHROOM) + 49, Target((1,), 1.0), None, Op("I'", KIND_ICE, variant="imitater"))
    rep, be, _ = run(Plan((imit_ice, coffee(1, 300)), ()), FakeClock(THREE_WAVES))
    assert be.calls[0][:2] == ("plant_card", (int(PlantType.ICE_SHROOM), 1, 1, True))
    assert be.calls[1][:2] == ("plant_card", (int(PlantType.COFFEE_BEAN), 1, 1, False))


def test_bad_policy_rejected():
    with pytest.raises(ValueError):
        PlannedExecutor(FakeClock(THREE_WAVES), FakeBackend(FakeClock(THREE_WAVES)), Plan((), ()), late_policy="ignore")


# --- report ---------------------------------------------------------------
def test_report_json_structure_and_mode():
    plan = Plan((cob(1, 300, (1, 1)), ice(2, -89), cob(2, 300, (2, 1))), (Diagnostic("info", "DEFAULT_TIMING", "x"),))
    clock = FakeClock(THREE_WAVES)
    rep, be, _ = run(plan, clock)
    d = json.loads(rep.to_json())
    assert set(d) == {"summary", "rows", "refresh_points", "pauses", "events", "meta"}
    s = d["summary"]
    for key in ("mode", "backend", "dry_run", "tolerance_cs", "late_policy", "plan_actions", "plan_issues", "dispatched", "skipped", "late", "failed", "pauses", "stop_reason", "jitter"):
        assert key in s
    assert s["plan_issues"] == ["DEFAULT_TIMING"]
    assert s["mode"] == "修改器"  # remote plant_card used
    assert set(s["jitter"]) == {"count", "mean", "p95", "max", "min"}
    assert s["jitter"]["count"] == 3
    row = d["rows"][0]
    assert set(row) == {"wave", "symbol", "kind", "planned_cs", "detail", "observed_cs", "delta", "status", "note", "done_cs",
                        "frozen_cs", "fired_cs", "attempts", "cd_wait_cs", "call_ms"}
    assert d["refresh_points"] == {"2": 1000}
    text = rep.to_text()
    assert "mode=修改器" in text and "jitter:" in text and " P " in text


def test_mode_labels():
    clock = FakeClock(THREE_WAVES)
    rep, _, _ = run(Plan((cob(1, 300, (1, 1)),), ()), clock)  # remote cob only
    assert rep.mode == "键控"
    clock = FakeClock(THREE_WAVES)
    rep, _, _ = run(Plan((ice(1, 300),), ()), clock, backend=FakeBackend(clock, mode="input"))
    assert rep.mode == "键控"
    clock = FakeClock(THREE_WAVES)
    be = FakeBackend(clock, mode="remote")
    be.cob_mode = "input"
    rep, _, _ = run(Plan((cob(1, 300, (1, 1)), ice(1, 500)), ()), clock, backend=be)
    assert rep.mode == "混合"


def test_jitter_stats():
    j = jitter_stats([0, 1, 1, 2, 5])
    assert j.count == 5 and j.max == 5 and j.min == 0 and j.p95 == 5.0
    assert abs(j.mean - 1.8) < 1e-9
    assert jitter_stats([]).count == 0 and jitter_stats([]).mean is None


def test_real_clock_shape_main_counter_refresh_object_and_timeout():
    """Samples with ``main_counter``/``in_level``, ``time_to_next_refresh`` returning
    an object with ``.cs`` and ``wait_until`` raising a *Timeout* all work."""
    from pvztools.control.executor.clock import ClockTimeout, Refresh

    @dataclass
    class RealishSample:
        main_counter: int
        wave: int
        paused: bool = False
        in_level: bool = True

    class RealishClock(FakeClock):
        def read(self):
            s = super().read()
            return RealishSample(s.clock, s.wave, s.paused, not s.level_complete)

        def wave_time(self, sample, wave):
            return sample.main_counter - self.refresh[wave] if wave in self.refresh else None

        def time_to_next_refresh(self, sample):
            nxt = sample.wave + 1
            return Refresh(self.refresh[nxt] - sample.main_counter if nxt in self.refresh else None, "countdown", nxt)

        def paused(self):
            return self.pause_left > 0

        def wait_until(self, pred, timeout, eta=None):
            for _ in range(20):  # short slice, then "timeout"
                if pred(self.read()):
                    return True
            raise ClockTimeout("slice")

    clock = RealishClock(THREE_WAVES, complete_at=2200)
    rep, be, _ = run(Plan((ice(2, -89), cob(2, 300, (1, 1)), cob(3, 300, (2, 1))), ()), clock)
    assert [c[0] for c in be.calls] == ["plant_card", "fire_cob"]
    assert rep.rows[0].status == STATUS_OK and rep.rows[1].status == STATUS_OK
    assert rep.rows[2].status == STATUS_SKIPPED and rep.stop_reason == "level complete"
    assert rep.refresh_points == {2: 1000, 3: 2000}


# --- tail edge cases (review fixes) -------------------------------------------
class ReadyBackend(FakeBackend):
    """FakeBackend with scripted cannon readiness: ``ready(cannon, clock)``."""

    def __init__(self, clock, ready):
        super().__init__(clock)
        self.ready = ready

    def cobs_ready(self, cannons):
        return [self.ready(tuple(c), self.clock.clock) for c in cannons]


def test_tail_entry_after_cd_wait_spanning_waves_is_missed_not_aborted():
    """The last planned group ends inside a cd-wait that spans several waves:
    the first tail poll is far past the template offset.  The tail must not
    dispatch late (or abort the run under late_policy='abort'); it skips the
    missed wave and fires on the next one."""
    refresh = {1: 0, 2: 1000, 3: 2000, 4: 3000, 5: 4000, 6: 5000}
    plan = Plan((cob(1, 296, (1, 1)),), ())
    clock = FakeClock(refresh, complete_at=5900)
    be = ReadyBackend(clock, lambda c, t: t < 300 or t >= 3800)
    rep, _, events = run(plan, clock, be, tail_timeout_s=30, tail_policy="repeat", late_policy="abort")
    assert rep.stop_reason == "level complete"
    assert [r.wave for r in rep.rows if r.status == "aborted"] == []
    tail = rep.meta["tail"]
    assert [m["wave"] for m in tail["missed"]] == [4] and tail["missed"][0]["late_by"] > 500
    fired = [r for r in rep.rows if r.detail.startswith("tail")]
    # w2: cd-wait until the cannon recharges (3800 = w3 +1800); w4 is then
    # already +800 -> missed (not 'late'); w5 / w6 fire on time
    assert [(r.wave, r.status) for r in fired] == [(2, "cd-wait"), (5, "ok"), (6, "ok")]
    assert all(abs(r.observed_cs - 296) <= 5 for r in fired[1:])
    assert tail["waves"] == [2, 4, 5, 6] and tail["fired_groups"] == 3
    assert [(e["wave"], e["operation_cs"]) for e in events if e["event"] == "tail_miss"] == [(4, 296)]


def test_tail_repeat_substitutes_any_ready_cannon():
    """P2-of-4 rhythm: the last planned wave used (1,1); on the tail waves
    (1,1) is still on CD but (2,1) / (5,1) are ready -> fire them instead of
    cd-waiting ~3500cs."""
    refresh = {1: 0, 2: 1000, 3: 2000, 4: 3000}
    plan = Plan((cob(1, 296, (2, 1)), cob(1, 296, (5, 1)), cob(2, 296, (1, 1))), ())
    clock = FakeClock(refresh, complete_at=3900)
    # (1,1) fired at ~1296 and is never ready again; the others recharge at 1900
    be = ReadyBackend(clock, lambda c, t: (c != (1, 1) and (t < 400 or t >= 1900)) or (c == (1, 1) and t < 1400))
    rep, _, _ = run(plan, clock, be, tail_timeout_s=30, tail_policy="repeat")
    tail_rows = [r for r in rep.rows if r.detail.startswith("tail")]
    assert [(r.wave, r.status) for r in tail_rows] == [(3, "ok"), (4, "ok")]
    assert all(abs(r.observed_cs - 296) <= 5 for r in tail_rows)
    fires = [c for c in be.calls if c[0] == "fire_cob"]
    assert [f[1][0] for f in fires[-2:]] == [(2, 1), (2, 1)]   # substituted, not (1,1)
    assert all("tail cob (2, 1)" in r.detail for r in tail_rows)


def test_tail_template_step_without_cannon_fails_without_backend_call():
    """A COB_SHORTAGE action (cannon None) in the last planned wave becomes a
    'failed' tail row; the backend is never asked to fire."""
    refresh = {1: 0, 2: 1000, 3: 2000}
    a = Action(2, 669, 296, KIND_COB, "P", int(PlantType.COB_CANNON), Target((2,), 9.0), None, Op("P", KIND_COB))
    plan = Plan((cob(1, 296, (1, 1)), a), ())
    clock = FakeClock(refresh, complete_at=2900)
    rep, be, _ = run(plan, clock, tail_timeout_s=30, tail_policy="repeat")
    fires = [c for c in be.calls if c[0].startswith("fire")]
    assert len(fires) == 1
    tail_rows = [r for r in rep.rows if r.detail.startswith("tail")]
    assert [(r.wave, r.status) for r in tail_rows] == [(3, "failed")]
    assert "no cannon" in tail_rows[0].note
