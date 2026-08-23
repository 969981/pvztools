"""Frame-locked remote execution (DESIGN §6.3 external tier).

Fake session + patch manager with a scripted ``Board.main_counter`` that
advances on a fake wall clock while ``main_loop_block`` is off and stands
still while it is on.  No live game.
"""
from __future__ import annotations

import struct
import warnings
from typing import List, Optional, Tuple

import pytest

from pvztools.control.actions import COB_FIRING, COB_READY, COB_SHOOTING_COUNTER, FireResult

from pvztools.control.remote import (
    LOCK_MAX_WAIT_S,
    LOCK_STALL_S,
    MAIN_LOOP_PATCH,
    FrameLockError,
    RemoteCallFailed,
    RemoteExecutor,
    RemoteResult,
)
from pvztools.modifier.safety import Safety
from pvztools.schema.track.expand import Plan

from test_actions import BOARD, make_actions
from test_cob_fire import _add_cob
from test_exec_scheduler import THREE_WAVES, FakeBackend, FakeClock, cob, run as run_plan
from test_remote import LAWN, STAMP_1096, FakeSession, FakeWin
from pvztools.addr import profile_for_stamp

FRAME_S = 0.01


# ---------------------------------------------------------------------------
# fakes: a game whose main_counter runs on a fake clock
# ---------------------------------------------------------------------------
class FakeGame:
    """``counter(t)`` = ``base + floor((t - t0) / frame)`` while running; a
    ``main_loop_block`` enable freezes it (optionally ``freeze_lag`` frames
    late, the real patch takes effect on the next loop iteration)."""

    def __init__(self, start: int = 1000, frame_s: float = FRAME_S, freeze_lag: float = 0.0):
        self.t = 0.0
        self.frame_s = frame_s
        self.freeze_lag = freeze_lag
        self._base = start
        self._t0 = 0.0
        self._frozen_at: Optional[float] = None
        self.blocked = False
        self.calls: List[Tuple[str, str]] = []
        self.reads: List[int] = []

    # wall clock ----------------------------------------------------------
    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += max(0.0, s) + 1e-6  # a spin also costs a little

    # counter -------------------------------------------------------------
    def _counter_at(self, t: float) -> int:
        return self._base + int((t - self._t0) / self.frame_s + 1e-9)

    def counter(self) -> int:
        t = self.t if self._frozen_at is None else min(self.t, self._frozen_at)
        v = self._counter_at(t)
        self.reads.append(v)
        return v

    # patch manager interface ---------------------------------------------
    def enable(self, name: str) -> None:
        self.calls.append(("enable", name))
        if name == MAIN_LOOP_PATCH:
            self.blocked = True
            self._frozen_at = self.t + self.freeze_lag * self.frame_s

    def disable(self, name: str) -> None:
        self.calls.append(("disable", name))
        if name == MAIN_LOOP_PATCH and self.blocked:
            # resume from the frozen value: no catch-up
            frozen = self._counter_at(self._frozen_at)
            self._base, self._t0 = frozen, self.t
            self._frozen_at = None
            self.blocked = False

    @property
    def blocks(self) -> int:
        return sum(1 for c in self.calls if c == ("enable", MAIN_LOOP_PATCH))


@pytest.fixture
def profile():
    return profile_for_stamp(STAMP_1096)


def make_exec(profile, game: FakeGame, *, dry_run=True, read_counter="game", win=None, monkeypatch=None):
    sess = FakeSession()
    sess.poke(profile.globals.lawn_app, struct.pack("<I", LAWN))
    sess.poke(LAWN + profile.lawn_app.frame_duration, struct.pack("<I", 10))
    safety = Safety(allow_remote_call=True, allow_patch=True, dry_run=dry_run)
    rc = game.counter if read_counter == "game" else read_counter
    ex = RemoteExecutor(sess, profile, safety, game, sleep=game.sleep, read_counter=rc, now=game.now)
    if win is not None and monkeypatch is not None:
        from pvztools.control import remote as remote_mod
        monkeypatch.setattr(remote_mod, "winremote", win)
    return ex


# ---------------------------------------------------------------------------
# _wait_frozen
# ---------------------------------------------------------------------------
def test_wait_frozen_detects_the_freeze(profile):
    game = FakeGame(start=500, freeze_lag=1.0)  # patch bites one frame later
    ex = make_exec(profile, game)
    game.enable(MAIN_LOOP_PATCH)
    t0 = game.now()
    frozen = ex._wait_frozen()
    assert frozen == 501  # one more tick slipped in before the loop parked
    # returned once the value held still for 1.5 frames (well under the cap)
    assert FRAME_S * 1.5 <= game.now() - t0 < 0.2


def test_wait_frozen_cap_when_counter_never_settles(profile):
    game = FakeGame(start=0)
    ex = make_exec(profile, game)  # patch never enabled -> keeps running
    t0 = game.now()
    v = ex._wait_frozen()
    assert 0.2 <= game.now() - t0 < 0.25
    assert v == game.counter() or v == game.counter() - 1


def test_wait_frozen_falls_back_to_two_frame_sleep_without_counter(profile):
    game = FakeGame()
    sleeps: List[float] = []
    ex = make_exec(profile, game, read_counter=lambda: None)
    ex._sleep = sleeps.append
    assert ex._wait_frozen() is None
    assert sleeps == [0.02]


def test_default_reader_reads_lawn_board_main_counter(profile):
    sess = FakeSession()
    ex = RemoteExecutor(sess, profile, Safety(allow_remote_call=True, allow_patch=True, dry_run=True), FakeGame())
    assert ex.read_counter() is None  # lawn pointer 0
    sess.poke(profile.globals.lawn_app, struct.pack("<I", LAWN))
    assert ex.read_counter() is None  # board pointer 0
    sess.poke(LAWN + profile.lawn_app.board, struct.pack("<I", BOARD))
    sess.poke(BOARD + profile.board.main_counter, struct.pack("<i", 4242))
    assert ex.read_counter() == 4242


# ---------------------------------------------------------------------------
# run(): plain call uses _wait_frozen; RemoteResult
# ---------------------------------------------------------------------------
def test_plain_run_records_frozen_tick(profile):
    game = FakeGame(start=300)
    ex = make_exec(profile, game)
    out = ex.run(b"\x90")
    assert isinstance(out, bytes)  # dry-run: linked shellcode, as before
    r = ex.last_result
    assert isinstance(r, RemoteResult)
    assert r.frozen_tick == 300 and r.requested_tick is None and r.late_by is None
    assert r.attempts == 1 and len(r.durations) == 1
    assert game.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]
    assert not game.blocked


def test_run_locked_returns_result_and_real_execution(profile, monkeypatch):
    game = FakeGame(start=10)
    win = FakeWin(exit_code=0)
    ex = make_exec(profile, game, dry_run=False, win=win, monkeypatch=monkeypatch)
    res = ex.run_locked(b"\x90", at_tick=10)
    assert res.exit_code == 0 and res.frozen_tick == 10 and res.hit
    assert [e[0] for e in win.log] == ["alloc", "thread", "wait", "exit", "close", "free"]
    assert not game.blocked


# ---------------------------------------------------------------------------
# at_tick: early -> release -> retry -> hit; late_by; retries bound
# ---------------------------------------------------------------------------
def test_at_tick_hits_after_early_release_and_retry(profile):
    # a learned lead of 1 with a game whose patch bites at once: the first
    # block lands one tick early -> released -> re-blocked on the target
    game = FakeGame(start=1000)
    ex = make_exec(profile, game)
    ex.freeze_lead = 1
    res = ex.run_locked(b"\x90", at_tick=1002, max_early=3)
    assert res.frozen_tick == 1002 and res.late_by == 0 and res.hit
    assert res.attempts == 2 and res.history[0][0] == 1001
    assert res.history[-1][0] == 1002
    assert len(res.durations) == res.attempts
    assert res.freeze_status == ["stable", "stable"] and res.freeze_confirmed
    # every block was released; the final disable is the unwinding one
    enables = [c for c in game.calls if c == ("enable", MAIN_LOOP_PATCH)]
    disables = [c for c in game.calls if c == ("disable", MAIN_LOOP_PATCH)]
    assert len(enables) == res.attempts == len(disables)
    assert not game.blocked
    assert ex.freeze_lead == 0  # the lag of this game (0) was learned


def test_at_tick_close_ahead_blocks_once_on_the_target(profile):
    # the scheduler dispatches ~1 tick early: no early block / release cycle
    for ahead in (1, 2, 3):
        game = FakeGame(start=1000)
        ex = make_exec(profile, game)
        res = ex.run_locked(b"\x90", at_tick=1000 + ahead, max_early=3)
        assert res.hit and res.attempts == 1 and game.blocks == 1


def test_at_tick_far_away_waits_with_game_running_before_first_block(profile):
    game = FakeGame(start=100)
    ex = make_exec(profile, game)
    res = ex.run_locked(b"\x90", at_tick=150, max_early=3)
    assert res.frozen_tick == 150 and res.hit
    assert res.attempts == 1  # the hybrid wait landed the first block on the tick
    assert game.blocks == 1
    # it slept (not spun) most of the way: wall time ~ 50 frames
    assert 49 * FRAME_S <= game.now() <= 52 * FRAME_S + 0.02


def test_at_tick_already_past_executes_and_reports_late_by(profile):
    game = FakeGame(start=2005)
    ex = make_exec(profile, game)
    res = ex.run_locked(b"\x90", at_tick=2000)
    assert res.frozen_tick == 2005 and res.late_by == 5 and not res.hit
    assert res.attempts == 1
    assert not game.blocked


def test_at_tick_overshoot_by_patch_lag_is_late_by_one(profile):
    # the patch parks the loop one frame after enable: the first freeze
    # overshoots by one tick, no retry, late_by recorded
    game = FakeGame(start=40, freeze_lag=1.0)
    ex = make_exec(profile, game)
    res = ex.run_locked(b"\x90", at_tick=40, max_early=3)
    assert res.frozen_tick == 41 and res.late_by == 1
    assert ex.freeze_lead == 1  # ... and the lag is learned for the next lock


def test_learned_freeze_lead_turns_late_by_one_into_hits(profile):
    game = FakeGame(start=100, freeze_lag=1.0)
    ex = make_exec(profile, game)
    first = ex.run_locked(b"\x90", at_tick=105)
    assert first.late_by == 1 and first.freeze_lead == 0
    for at in (120, 130, 141):
        res = ex.run_locked(b"\x90", at_tick=at)
        assert res.hit and res.attempts == 1 and res.freeze_lead == 1, (at, res)
    assert ex.freeze_lead == 1


def test_at_tick_retries_are_bounded_when_counter_keeps_moving_but_never_hits(profile):
    game = FakeGame(start=0)
    ex = make_exec(profile, game)
    # the counter advances (no stall) but every freeze comes up early
    ex.freeze_lead = 1
    ex._learn_lead = lambda lag: None          # keep the bad lead
    orig = game.enable

    def enable(name):  # the freeze always lands one tick before the target
        orig(name)
        if name == MAIN_LOOP_PATCH:
            game._frozen_at = game.t - game.frame_s
    game.enable = enable
    res = ex.run_locked(b"\x90", at_tick=9, retries=3)
    assert res.attempts == 4  # first block + 3 retries
    assert res.late_by < 0 and not res.hit
    assert not game.blocked
    assert isinstance(res.exit_code, bytes)  # executed anyway (dry-run bytes)


def test_paused_game_raises_instead_of_executing_at_wrong_tick(profile):
    # main_counter never advances (pause dialog / unfocused game)
    game = FakeGame(start=1000, frame_s=1e9)
    ex = make_exec(profile, game)
    with pytest.raises(FrameLockError) as ei:
        ex.run_locked(b"\x90", at_tick=1005)
    assert "1005" in str(ei.value)
    assert ex.last_result.attempts == 0 and ex.last_result.exit_code is None
    assert game.blocks == 0 and not game.blocked
    assert game.now() < LOCK_STALL_S + 0.5  # gave up after a stall window, not minutes


def test_board_paused_flag_refuses_the_lock(profile):
    game = FakeGame(start=1000)
    ex = make_exec(profile, game)
    sess = ex.session
    sess.poke(LAWN + profile.lawn_app.board, struct.pack("<I", BOARD))
    sess.poke(BOARD + profile.board.paused, struct.pack("<i", 1))
    with pytest.raises(FrameLockError):
        ex.run_locked(b"\x90", at_tick=1003)
    assert game.blocks == 0
    sess.poke(BOARD + profile.board.paused, struct.pack("<i", 0))
    assert ex.run_locked(b"\x90", at_tick=1003).hit


def test_far_ahead_target_is_capped_not_waited_for_hours(profile):
    game = FakeGame(start=1000)
    ex = make_exec(profile, game)
    with pytest.raises(FrameLockError):
        ex.run_locked(b"\x90", at_tick=1000 + 100000)
    assert game.now() <= LOCK_MAX_WAIT_S + 1.0
    assert game.blocks == 0


def test_board_freed_mid_wait_raises_and_does_not_execute(profile):
    game = FakeGame(start=1000)
    ex = make_exec(profile, game)
    reads = [0]

    def counter():
        reads[0] += 1
        if game.now() > 0.03:  # the level ends 3 frames in
            return None
        return game.counter()
    ex._read_counter = counter
    with pytest.raises(FrameLockError):
        ex.run_locked(b"\x90", at_tick=1050)
    assert ex.last_result.exit_code is None and game.blocks == 0


def test_board_freed_during_freeze_raises(profile):
    game = FakeGame(start=1000)
    ex = make_exec(profile, game)
    n = [0]

    def counter():
        n[0] += 1
        if game.blocked:
            return None
        return game.counter()
    ex._read_counter = counter
    with pytest.raises(FrameLockError):
        ex.run_locked(b"\x90", at_tick=1002)
    assert ex.last_result.freeze_status == ["none"]
    assert not game.blocked  # unwound


def test_capped_freeze_with_moving_counter_is_refused(profile):
    # the patch never parks the loop: the counter keeps moving through the cap
    game = FakeGame(start=0)
    game.enable = lambda name: game.calls.append(("enable", name))
    ex = make_exec(profile, game)
    with pytest.raises(FrameLockError) as ei:
        ex.run_locked(b"\x90")
    assert ex.last_result.freeze_status == ["capped"] and not ex.last_result.freeze_confirmed
    assert "主循环未停住" in str(ei.value)
    assert ex.last_result.exit_code is None
    assert game.calls[-1] == ("disable", MAIN_LOOP_PATCH)


def test_plain_run_reports_stable_freeze(profile):
    game = FakeGame(start=7)
    ex = make_exec(profile, game)
    ex.run(b"\x90")
    assert ex.last_result.freeze_status == ["stable"] and ex.last_result.freeze_confirmed


def test_at_tick_without_counter_degrades_to_plain_call(profile):
    game = FakeGame()
    sleeps: List[float] = []
    ex = make_exec(profile, game, read_counter=lambda: None)
    ex._sleep = sleeps.append
    res = ex.run_locked(b"\x90", at_tick=123)
    assert res.frozen_tick is None and res.late_by is None and res.requested_tick == 123
    assert sleeps == [0.02] and res.attempts == 1


def test_at_tick_requires_block(profile):
    ex = make_exec(profile, FakeGame())
    with pytest.raises(ValueError):
        ex.run(b"\x90", at_tick=5, block_main_loop=False)


def test_disable_failure_mid_retry_is_not_masked(profile):

    class BadRelease(FakeGame):
        def disable(self, name):
            raise RuntimeError("stuck")

    bad = BadRelease(start=0)
    ex = make_exec(profile, bad)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(RemoteCallFailed) as ei:
            ex.run_locked(b"\x90", at_tick=2, max_early=5)
    # the release failure is surfaced (typed, flagged), never masked
    assert "stuck" in str(ei.value) and ei.value.main_loop_blocked
    assert ex.main_loop_blocked
    assert bad.calls == [("enable", MAIN_LOOP_PATCH)]  # BadRelease does not record the watchdog attempts


# ---------------------------------------------------------------------------
# Actions.fire_cob / fire_cobs: at_tick pass-through, fired_tick arithmetic
# ---------------------------------------------------------------------------
class LockingFiringExecutor:
    """Records ``at_tick``; flips cobs to FIRING with shooting_counter =
    206 - age, and bumps the board clock by ``age`` ticks afterwards (the
    game ran ``age`` frames between the fire and our read-back)."""

    def __init__(self, session, profile, frozen=5000, age=3):
        self.session, self.profile = session, profile
        self.frozen, self.age = frozen, age
        self.calls = []
        self.last_result = None

    def run(self, code, *, name="shellcode", extra_patches=(), at_tick=None, **kw):
        self.calls.append((name, at_tick))
        pl = self.profile.objects["plant"]
        for _, _, text in code._insns:
            if text.startswith("mov eax, "):
                addr = int(text.split(", ")[1], 16)
                self.session.poke_u32(addr + pl.state, COB_FIRING)
                self.session.poke_u32(addr + pl.shooting_counter, COB_SHOOTING_COUNTER - self.age)
        self.session.poke_u32(BOARD + self.profile.board.main_counter, self.frozen + self.age)
        late = None if at_tick is None else self.frozen - at_tick
        self.last_result = RemoteResult(exit_code=0, frozen_tick=self.frozen, requested_tick=at_tick,
                                        late_by=late, attempts=1, durations=[0.012])
        return 0


def make_fire_world(**kw):
    a, s, p, _ex, _patches = make_actions(scene=2)
    ex = LockingFiringExecutor(s, p, **kw)
    a.executor = ex
    _add_cob(s, p, 2, 2, 0, COB_READY)
    _add_cob(s, p, 3, 3, 0, COB_READY)
    return a, s, p, ex


def test_profiles_carry_shooting_counter():
    from pvztools.addr.profile import PROFILE_DIR, load_profile
    for name in ("goty_1_2_0_1096_en.json", "1_0_0_1051_en.json"):
        assert load_profile(PROFILE_DIR / name).objects["plant"].shooting_counter == 0x90


def test_fire_cob_passes_at_tick_and_reads_back_fired_tick():
    a, s, p, ex = make_fire_world(frozen=5000, age=3)
    r = a.fire_cob((3, 1), 2, 8.5, at_tick=4999)
    assert isinstance(r, FireResult) and r.fired
    assert ex.calls == [("发炮 (3, 1) -> (2,8.5)", 4999)]
    assert r.frozen_tick == 5000 and r.requested_tick == 4999 and r.late_by == 1
    assert r.shooting_counter == 203
    # now = 5003, counter 203 -> fired 3 ticks ago = 5000
    assert r.fired_tick == 5000 == r.frozen_tick


def test_fire_cobs_passes_at_tick_and_fills_every_result():
    a, s, p, ex = make_fire_world(frozen=7000, age=0)
    rs = a.fire_cobs([((3, 1), (2, 8.5)), ((4, 1), (5, 8.5))], at_tick=7000)
    assert [c[1] for c in ex.calls] == [7000]
    assert [r.fired_tick for r in rs] == [7000, 7000]
    assert all(r.late_by == 0 and r.frozen_tick == 7000 and r.shooting_counter == 206 for r in rs)


def test_fire_without_at_tick_still_reports_fired_tick():
    a, s, p, ex = make_fire_world(frozen=100, age=1)
    r = a.fire_cob((3, 1), 2, 8.5)
    assert ex.calls[0][1] is None
    assert r.requested_tick is None and r.late_by is None
    assert r.frozen_tick == 100 and r.fired_tick == 100


def test_fire_result_fields_none_when_not_fired_or_old_executor():
    a, s, p, ex = make_fire_world()

    class OldExecutor:  # returns an int, no last_result, does not fire
        def run(self, code, **kw):
            return 0

    a.executor = OldExecutor()
    r = a.fire_cob((3, 1), 2, 8.5)
    assert not r.fired
    assert r.frozen_tick is None and r.fired_tick is None and r.late_by is None


def test_fire_cob_dry_run_leaves_timing_none():
    a, s, p, _ex, _ = make_actions(scene=2, dry_run=True)
    _add_cob(s, p, 2, 2, 0, COB_READY)
    r = a.fire_cob((3, 1), 2, 8.5, at_tick=10)
    assert r.state_after is None and r.fired_tick is None and r.frozen_tick is None


# ---------------------------------------------------------------------------
# scheduler: remote cob groups are frame-locked, observed_cs from fired_tick
# ---------------------------------------------------------------------------
class LockingBackend(FakeBackend):
    """Remote backend whose fire_cob(s) accept ``at_tick`` and return
    FireResults with ``fired_tick = at_tick + lag``."""

    def __init__(self, clock, lag=0, mode="remote"):
        super().__init__(clock, mode)
        self.lag = lag
        self.at_ticks: List[Optional[int]] = []

    def _res(self, at_tick):
        ft = None if at_tick is None else at_tick + self.lag
        return FireResult(0, 0, 0, COB_READY, COB_FIRING, (0, 0.0), frozen_tick=ft, fired_tick=ft,
                          requested_tick=at_tick, late_by=self.lag if at_tick is not None else None)

    def fire_cob(self, cannon, row, col, at_tick=None):
        self._rec("fire_cob", cannon, row, col)
        self.at_ticks.append(at_tick)
        return self._res(at_tick)

    def fire_cobs(self, fires, at_tick=None):
        self._rec("fire_cobs", tuple(fires))
        self.at_ticks.append(at_tick)
        return [self._res(at_tick) for _ in fires]


def test_scheduler_passes_at_tick_for_remote_cob_groups():
    plan = Plan((cob(1, 300, (1, 1)), cob(2, 200, (1, 1), symbol="P"), cob(2, 200, (2, 1), symbol="P")), ())
    clock = FakeClock(THREE_WAVES)
    backend = LockingBackend(clock)
    rep, backend, events = run_plan(plan, clock, backend)
    # refresh_point(w1) = 0, (w2) = 1000 -> absolute ticks 300 and 1200
    assert backend.at_ticks == [300, 1200]
    assert [c[0] for c in backend.calls] == ["fire_cob", "fire_cobs"]
    rows = rep.rows
    assert [r.observed_cs for r in rows] == [300, 200, 200]  # from fired_tick, not the clock
    assert all(r.delta == 0 and r.status == "ok" for r in rows)


def test_scheduler_observed_cs_uses_fired_tick_when_late():
    plan = Plan((cob(1, 300, (1, 1)),), ())
    clock = FakeClock(THREE_WAVES)
    backend = LockingBackend(clock, lag=2)
    rep, _, _ = run_plan(plan, clock, backend, tolerance_cs=1)
    row = rep.rows[0]
    assert row.observed_cs == 302 and row.delta == 2 and row.status == "late"


def test_scheduler_input_backend_unchanged():
    plan = Plan((cob(1, 300, (1, 1)),), ())
    clock = FakeClock(THREE_WAVES)
    backend = LockingBackend(clock, mode="input")
    rep, _, _ = run_plan(plan, clock, backend)
    assert backend.at_ticks == [None]  # not frame-locked in input mode
    assert rep.rows[0].status == "ok"


def test_scheduler_tolerates_remote_backend_without_at_tick():
    plan = Plan((cob(1, 300, (1, 1)),), ())
    clock = FakeClock(THREE_WAVES)
    backend = FakeBackend(clock)  # remote, but fire_cob(cannon, row, col) only
    rep, backend, _ = run_plan(plan, clock, backend)
    assert backend.calls[0][0] == "fire_cob"
    assert rep.rows[0].status == "ok"


# ---------------------------------------------------------------------------
# fired_tick read-back: main_counter / shooting_counter read as a stable pair
# ---------------------------------------------------------------------------
def test_fired_tick_readback_survives_a_tick_between_the_reads():
    """The game runs again after the call: a tick landing between the
    ``main_counter`` read and the ``shooting_counter`` read used to give a
    fired_tick one too early."""
    a, s, p, ex = make_fire_world(frozen=5000, age=3)
    mc_addr = BOARD + p.board.main_counter
    sc_off = p.objects["plant"].shooting_counter
    orig = s.read_i32
    ticks = {"n": 0}

    def racy_read_i32(addr):
        v = orig(addr)
        if addr == mc_addr:
            ticks["n"] += 1
            if ticks["n"] == 1:
                # a game tick lands right after this read: counter +1 and
                # every firing cob's shooting_counter -1
                s.poke_u32(mc_addr, v + 1)
                for idx in range(4):
                    pa = a.plant_addr(idx, BOARD)
                    cur = orig(pa + sc_off)
                    if 0 < cur <= COB_SHOOTING_COUNTER:
                        s.poke_u32(pa + sc_off, cur - 1)
        return v
    s.read_i32 = racy_read_i32
    r = a.fire_cob((3, 1), 2, 8.5, at_tick=5000)
    assert r.fired and r.fired_tick == 5000 == r.frozen_tick
    assert ticks["n"] >= 3  # bracketed re-read happened


def test_fired_tick_readback_gives_up_after_bounded_tries():
    a, s, p, ex = make_fire_world(frozen=5000, age=0)
    mc_addr = BOARD + p.board.main_counter
    orig = s.read_i32
    n = {"mc": 0}

    def always_moving(addr):
        v = orig(addr)
        if addr == mc_addr:
            n["mc"] += 1
            s.poke_u32(mc_addr, v + 1)  # never stable
        return v
    s.read_i32 = always_moving
    r = a.fire_cob((3, 1), 2, 8.5)
    assert r.fired and r.fired_tick is not None
    assert n["mc"] <= 2 * a.FIRE_READBACK_TRIES + 1


def test_scheduler_fired_cs_stays_none_without_readback():
    """A result with only a frozen tick (no shooting_counter in the profile
    or the shot failed) must not be counted as 'read back from the game'."""

    class FrozenOnlyBackend(LockingBackend):
        def _res(self, at_tick):
            r = super()._res(at_tick)
            r.fired_tick = None
            return r

    plan = Plan((cob(1, 300, (1, 1)),), ())
    clock = FakeClock(THREE_WAVES)
    rep, _, _ = run_plan(plan, clock, FrozenOnlyBackend(clock))
    row = rep.rows[0]
    assert row.frozen_cs == 300 and row.fired_cs is None
    assert row.observed_cs == 300 and row.status == "ok"
    assert rep.frame_locked == 0 and rep.summary()["frame_locked"] == 0


def test_expanded_p4_rhythm_frame_locks_at_refresh_plus_296():
    """End to end: the rhythm-derived operation_cs (P4 869 -> land 669 ->
    fire 296) flows expand -> Action -> _Step -> at_tick = refresh + 296."""
    from pvztools.schema.track.expand import ExpandContext, expand
    from pvztools.schema.track.shorthand import parse_shorthand

    cobs = tuple((r, c) for r in (1, 2, 5, 6) for c in (1, 3))
    plan = expand(parse_shorthand("P4: PP|PP|PP|PP (869|869|869|869)"),
                  ExpandContext("PE", cob_positions=cobs, wave_count=3))
    assert {a.effect_cs for a in plan.actions if a.kind == "cob"} == {669}
    assert {a.operation_cs for a in plan.actions if a.kind == "cob"} == {296}
    refresh = {1: 1000, 2: 1869, 3: 2738}
    clock = FakeClock(refresh)
    backend = LockingBackend(clock)
    rep, backend, _ = run_plan(plan, clock, backend)
    assert backend.at_ticks == [1296, 2165, 3034]
    assert [r.observed_cs for r in rep.rows] == [296] * 6
    assert all(r.status == "ok" for r in rep.rows)
