"""GameClock: refresh-point inference, wave_time, time_to_next_refresh,
pause detection, wait_until with a fake time source; precise_sleep /
high_resolution."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pvztools.control.executor import (
    GameClock, ClockSample, ClockTimeout, HUGE_WAVE_BANNER_AT,
    high_resolution, precise_sleep,
)
from pvztools.schema.timing import HUGE_WAVE_RED_TEXT


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
class FakeTime:
    """perf_counter + sleep on one fake clock."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += max(s, 0.0005)  # even sleep(0) costs a bit


def board(clock, wave=1, countdown=1000, huge=0, paused=False, total=20, start=2500):
    return SimpleNamespace(clock=clock, wave=wave, countdown=countdown,
                           countdown_init=start, huge_wave_countdown=huge,
                           paused=paused, total_waves=total)


class ScriptedObserver:
    """Returns boards from a script; each board() call consumes one entry
    (the last one repeats).  Optionally advances the fake clock per read."""

    def __init__(self, boards, ft: FakeTime, read_cost=0.001, ui=3, frame_duration=10):
        self.boards = list(boards)
        self.i = 0
        self.ft = ft
        self.read_cost = read_cost
        self.ui = ui
        self.frame_duration = frame_duration

    def game(self):
        return SimpleNamespace(ui=self.ui, frame_duration=self.frame_duration)

    def board(self):
        b = self.boards[min(self.i, len(self.boards) - 1)]
        self.i += 1
        self.ft.t += self.read_cost
        return b


class SimObserver:
    """A tiny game simulation driven by the fake wall clock: the battle
    clock advances ``frame_ms`` per ms of fake time unless paused."""

    def __init__(self, ft: FakeTime, frame_ms=10, wave=1, countdown=300, total=20):
        self.ft = ft
        self.frame_ms = frame_ms
        self.wave = wave
        self.countdown = countdown
        self.huge = 0
        self.total = total
        self.paused = False
        self.clock0 = 0
        self.t0 = ft.t
        self._last_tick = 0
        self.ui = 3

    def game(self):
        return SimpleNamespace(ui=self.ui, frame_duration=self.frame_ms)

    def _tick_to(self, tick):
        while self._last_tick < tick:
            self._last_tick += 1
            if self.huge > 0:
                self.huge -= 1
                if self.huge == 0:
                    self.wave += 1
                    self.countdown = 2500
            elif self.countdown > 0:
                self.countdown -= 1
                if self.countdown == HUGE_WAVE_BANNER_AT and (self.wave + 1) % 10 == 0:
                    self.huge = HUGE_WAVE_RED_TEXT
                elif self.countdown == 0:
                    self.wave += 1
                    self.countdown = 4500 if (self.wave + 1) % 10 == 0 else 2500

    def board(self):
        if not self.paused:
            tick = int((self.ft.t - self.t0) * 1000 / self.frame_ms)
            self._tick_to(tick)
        self.ft.t += 0.0005
        return board(self._last_tick, self.wave, self.countdown, self.huge,
                     self.paused, self.total)


# --------------------------------------------------------------------------
# samples / refresh points
# --------------------------------------------------------------------------
def test_read_builds_sample_and_first_refresh_point():
    ft = FakeTime()
    obs = ScriptedObserver([board(1234, wave=3, countdown=800)], ft)
    gc = GameClock(obs, now=ft.now, sleep=ft.sleep)
    s = gc.read()
    assert isinstance(s, ClockSample)
    assert (s.main_counter, s.wave, s.countdown, s.countdown_start, s.total_waves) == (1234, 3, 800, 2500, 20)
    assert s.game_ui == 3 and s.in_level
    rp = gc.refresh_point(3)
    assert rp is not None and rp.main_counter == 1234 and rp.late_by is None and rp.source == "first"
    assert gc.frame_ms == 10


def test_refresh_point_inferred_from_countdown_estimate():
    ft = FakeTime()
    # countdown 3 at clock 100 -> refresh at 103; next poll at 110 shows wave 2
    obs = ScriptedObserver([board(100, wave=1, countdown=3), board(110, wave=2, countdown=2800)], ft)
    gc = GameClock(obs, now=ft.now, sleep=ft.sleep)
    gc.read()
    s = gc.read()
    rp = gc.refresh_point(2)
    assert rp.main_counter == 103 and rp.source == "estimate" and rp.late_by == 10
    assert gc.wave_time(s, 2) == 7
    assert gc.wave_time(s, 1) == 10  # relative to first observation of w1


def test_refresh_point_falls_back_to_poll_counter():
    ft = FakeTime()
    # countdown says 900 but HP trigger refreshed early: estimate outside window
    obs = ScriptedObserver([board(100, wave=1, countdown=900), board(108, wave=2, countdown=2700)], ft)
    gc = GameClock(obs, now=ft.now, sleep=ft.sleep)
    gc.read()
    s = gc.read()
    rp = gc.refresh_point(2)
    assert rp.main_counter == 108 and rp.source == "poll" and rp.late_by == 8
    assert gc.wave_time(s, 2) == 0
    assert gc.wave_time(s, 5) is None


def test_skipped_waves_all_get_refresh_points_and_clock_reset():
    ft = FakeTime()
    obs = ScriptedObserver([board(100, wave=1, countdown=900), board(150, wave=3, countdown=2700),
                            board(5, wave=1, countdown=600)], ft)
    gc = GameClock(obs, now=ft.now, sleep=ft.sleep)
    gc.read(); gc.read()
    assert set(gc.refresh_points) == {1, 2, 3}
    assert gc.refresh_point(2).source == "poll" and gc.refresh_point(3).source == "poll"
    gc.read()  # clock went backwards -> new level
    assert set(gc.refresh_points) == {1} and gc.refresh_point(1).main_counter == 5


# --------------------------------------------------------------------------
# time_to_next_refresh
# --------------------------------------------------------------------------
def sample(**kw):
    base = dict(t=0.0, main_counter=0, wave=1, countdown=1000, countdown_start=2500,
                huge_wave_countdown=0, paused=False, total_waves=20, game_ui=3)
    base.update(kw)
    return ClockSample(**base)


def test_time_to_next_refresh_kinds():
    r = GameClock.time_to_next_refresh(sample(wave=3, countdown=420))
    assert (r.cs, r.kind, r.next_wave) == (420, "countdown", 4)

    r = GameClock.time_to_next_refresh(sample(wave=9, countdown=4000))
    assert r.kind == "huge_wave" and r.cs == 4000 - HUGE_WAVE_BANNER_AT + HUGE_WAVE_RED_TEXT

    r = GameClock.time_to_next_refresh(sample(wave=9, countdown=0, huge_wave_countdown=300))
    assert (r.cs, r.kind, r.next_wave) == (300, "huge_wave", 10)

    r = GameClock.time_to_next_refresh(sample(wave=19, countdown=3, huge_wave_countdown=0))
    assert r.kind == "huge_wave" and r.cs == HUGE_WAVE_RED_TEXT

    r = GameClock.time_to_next_refresh(sample(wave=20, countdown=0, total_waves=20))
    assert r.kind == "unknown" and r.cs is None and not r.known

    r = GameClock.time_to_next_refresh(sample(wave=4, countdown=0))
    assert r.kind == "unknown"


def test_wave_time_negative_for_upcoming_wave():
    ft = FakeTime()
    obs = ScriptedObserver([board(100, wave=1, countdown=250)], ft)
    gc = GameClock(obs, now=ft.now, sleep=ft.sleep)
    s = gc.read()
    assert gc.wave_time(s, 2) == -250
    assert gc.wave_time(s, 3) is None


# --------------------------------------------------------------------------
# pause detection
# --------------------------------------------------------------------------
def test_pause_detection_needs_three_polls_and_span():
    s = [sample(t=0.00, main_counter=500), sample(t=0.03, main_counter=500), sample(t=0.05, main_counter=500)]
    assert not GameClock.is_paused(s)  # span 50ms < 60ms
    s.append(sample(t=0.07, main_counter=500))
    assert GameClock.is_paused(s)
    assert not GameClock.is_paused(s[-2:])  # only two polls
    moving = s[:-1] + [sample(t=0.07, main_counter=501)]
    assert not GameClock.is_paused(moving)
    menu = [sample(t=t, main_counter=500, game_ui=1) for t in (0.0, 0.05, 0.1)]
    assert not GameClock.is_paused(menu)
    assert GameClock.is_paused([sample(paused=True)])


def test_clock_tracks_pause_from_reads():
    ft = FakeTime()
    obs = ScriptedObserver([board(700)] * 5, ft, read_cost=0.035)
    gc = GameClock(obs, now=ft.now, sleep=ft.sleep)
    gc.read(); gc.read()
    assert not gc.paused()
    gc.read()
    assert gc.paused()


# --------------------------------------------------------------------------
# waiting
# --------------------------------------------------------------------------
def test_wait_until_hybrid_sleeps_then_spins():
    ft = FakeTime()
    sim = SimObserver(ft, countdown=300)
    gc = GameClock(sim, now=ft.now, sleep=ft.sleep)
    s = gc.wait_ticks(100, timeout=5.0)
    assert 100 <= s.main_counter <= 102
    # coarse sleeps (capped at 50ms so the counter is re-read), then spins
    assert max(ft.sleeps) == pytest.approx(0.05)
    assert sum(x for x in ft.sleeps if x > 0.01) > 0.9
    assert sum(1 for x in ft.sleeps if x == 0) >= 1
    # never slept past the target: the spin window was honoured
    assert all(x <= 0.05 for x in ft.sleeps)


def test_wait_until_timeout_excludes_paused_time():
    ft = FakeTime()
    sim = SimObserver(ft, countdown=300)
    gc = GameClock(sim, now=ft.now, sleep=ft.sleep, poll_interval=0.01)
    sim.paused = True
    # paused time does not count toward ``timeout``; ``pause_timeout`` ends it
    with pytest.raises(ClockTimeout, match="paused"):
        gc.wait_until(lambda s: s.main_counter >= 10_000, timeout=0.2, pause_timeout=1.0)
    assert ft.t > 1.0
    # and an un-paused game does time out on ``timeout``
    ft2 = FakeTime()
    gc2 = GameClock(SimObserver(ft2, countdown=300), now=ft2.now, sleep=ft2.sleep, poll_interval=0.01)
    with pytest.raises(ClockTimeout, match="elapsed"):
        gc2.wait_until(lambda s: False, timeout=0.2)
    assert 0.2 < ft2.t < 0.3


def test_wait_until_resumes_after_pause():
    ft = FakeTime()
    sim = SimObserver(ft, countdown=300)
    gc = GameClock(sim, now=ft.now, sleep=ft.sleep, poll_interval=0.01)
    sim.paused = True
    calls = {"n": 0}

    def pred(s):
        calls["n"] += 1
        if calls["n"] == 12:
            sim.paused = False
            sim.t0 = ft.t  # resume: battle clock continues from 0
        return s.main_counter >= 50

    s = gc.wait_until(pred, timeout=1.0)
    assert s.main_counter >= 50


def test_wait_wave_time_positive_and_negative():
    ft = FakeTime()
    sim = SimObserver(ft, wave=1, countdown=300)
    gc = GameClock(sim, now=ft.now, sleep=ft.sleep)
    # w2 at -100: wait until countdown <= 100 while still in w1
    s = gc.wait_wave_time(2, -100, timeout=10)
    assert s.wave == 1 and s.countdown <= 100 and s.countdown > 90
    # w2 at +200
    s = gc.wait_wave_time(2, 200, timeout=10)
    assert s.wave == 2
    rp = gc.refresh_point(2)
    assert rp.source in ("estimate", "poll") and rp.late_by is not None
    wt = gc.wave_time(s, 2)
    assert 200 <= wt <= 200 + rp.late_by + 2
    # lead_cs fires early
    s2 = gc.wait_wave_time(2, 400, lead_cs=10, timeout=10)
    assert 390 <= gc.wave_time(s2, 2) < 400 + 3


def test_wait_wave_time_across_huge_wave():
    ft = FakeTime()
    sim = SimObserver(ft, wave=9, countdown=100)
    gc = GameClock(sim, now=ft.now, sleep=ft.sleep)
    s0 = gc.read()
    assert gc.time_to_next_refresh(s0).kind == "huge_wave"
    s = gc.wait_wave_time(10, 0, timeout=20)
    assert s.wave == 10
    assert 0 <= gc.wave_time(s, 10) <= 3
    rp = gc.refresh_point(10)
    assert rp.source == "estimate"


def test_wait_wave_time_already_past_returns_immediately():
    ft = FakeTime()
    obs = ScriptedObserver([board(100, wave=1, countdown=900), board(150, wave=3, countdown=2700)], ft)
    gc = GameClock(obs, now=ft.now, sleep=ft.sleep)
    gc.read()
    s = gc.wait_wave_time(2, 0, timeout=1)
    assert s.wave == 3 and len(ft.sleeps) == 0


# --------------------------------------------------------------------------
# timing_win
# --------------------------------------------------------------------------
def test_precise_sleep_fake_clock():
    ft = FakeTime()
    over = precise_sleep(0.5, now=ft.now, sleep=ft.sleep)
    assert 0 <= over < 0.002
    assert abs(ft.t - 0.5) < 0.002
    assert ft.sleeps[0] == pytest.approx(0.05)
    assert 0 in ft.sleeps


def test_precise_sleep_deadline_in_past():
    ft = FakeTime()
    ft.t = 5.0
    assert precise_sleep(0, now=ft.now, sleep=ft.sleep, deadline=4.0) == pytest.approx(1.0)
    assert ft.sleeps == []


def test_high_resolution_is_context_manager():
    with high_resolution() as applied:
        assert isinstance(applied, bool)
