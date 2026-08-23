"""control.autocollect: AutoCollector emits batched ``Coin::Collect`` calls."""
from dataclasses import dataclass
from typing import List, Optional

import pytest

from pvztools.addr.profile import PROFILE_DIR, load_profile
from pvztools.control.autocollect import (AWARD_TYPES, COIN_TYPES, DEFAULT_TYPES, AutoCollector,
                                          CoinType, CollectStats)
from pvztools.control.conv import NotSupported, convention_for
from pvztools.control.executor.report import ExecutionReport
from pvztools.control.executor.scheduler import PlannedExecutor
from pvztools.model.types import Coin
from pvztools.schema.enums import PlantType
from pvztools.schema.track.ast import KIND_ICE, Op, Target
from pvztools.schema.track.expand import Action, Plan

GOTY = load_profile(PROFILE_DIR / "goty_1_2_0_1096_en.json")
RETAIL = load_profile(PROFILE_DIR / "1_0_0_1051_en.json")
COLLECT_VA = 0x435DC0
BASE = 0x0C00_0000
STRIDE = GOTY.objects["coin"].stride


class StubAsm:
    def __init__(self):
        self.log = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _rec(*args):
            self.log.append((name, args))
        return _rec


@dataclass
class Game:
    ui: int = 3


@dataclass
class Board:
    paused: bool = False
    clock: int = 0


class FakeObserver:
    """Fake observer: coins list, ui, paused, clock; ``coins_base`` shortcut."""

    def __init__(self, coins, ui=3, paused=False, clock=0):
        self._coins = list(coins)
        self.ui = ui
        self.paused = paused
        self.clock = clock
        self.coin_reads = 0

    def game(self):
        return Game(self.ui)

    def board(self):
        return Board(self.paused, self.clock)

    def coins(self, include_dead=True):
        self.coin_reads += 1
        return tuple(c for c in self._coins if include_dead or not c.dead)

    def coins_base(self):
        return BASE


class FakeExecutor:
    def __init__(self):
        self.runs = []

    def run(self, asm, name="shellcode", extra_patches=(), **kw):
        self.runs.append((asm, name, extra_patches))
        return 0


def coin(i, ctype, dead=False, collecting=False):
    return Coin(index=i, type=ctype, pos=(0.0, 0.0), dead=dead, collecting=collecting)


def collector(observer, executor=None, **kw):
    executor = executor or FakeExecutor()
    conv = convention_for(GOTY, asm_factory=StubAsm)
    return AutoCollector(executor, observer, GOTY, conv=conv, **kw), executor


def pairs(frag):
    """``[(coin_addr, call_va), ...]`` from the emitted op list."""
    ops = frag.ops
    assert len(ops) % 2 == 0
    out = []
    for i in range(0, len(ops), 2):
        assert ops[i][0] == "mov_reg_imm32" and ops[i][1][0] == "ecx"
        assert ops[i + 1][0] == "call_abs"
        out.append((ops[i][1][1], ops[i + 1][1][0]))
    return out


# ---------------------------------------------------------------------------
# convention
# ---------------------------------------------------------------------------

def test_profile_has_coin_collect():
    assert GOTY.function_va("coin_collect") == COLLECT_VA


def test_goty_fragment_is_mov_ecx_call():
    frag = convention_for(GOTY, asm_factory=StubAsm).coin_collect(0x1234)
    assert frag.ops == [("mov_reg_imm32", ("ecx", 0x1234)), ("call_abs", (COLLECT_VA,))]
    assert frag.asm.log == frag.ops


def test_goty_fragment_real_asm_bytes():
    frag = convention_for(GOTY).coin_collect_many([0x1000, 0x2000])
    assert frag.calls() == [COLLECT_VA, COLLECT_VA]
    assert frag.reg_loads() == [("ecx", 0x1000), ("ecx", 0x2000)]
    assert len(frag.asm) == 2 * (5 + 5)  # mov ecx, imm32 + call rel32


def test_retail_unsupported():
    with pytest.raises(NotSupported):
        convention_for(RETAIL, asm_factory=StubAsm).coin_collect(1)


# ---------------------------------------------------------------------------
# collector
# ---------------------------------------------------------------------------

def test_emits_one_call_with_n_pairs():
    obs = FakeObserver([coin(0, CoinType.SUN), coin(3, CoinType.SILVER), coin(7, CoinType.LARGESUN)])
    col, ex = collector(obs)
    assert col.tick() == 3
    assert len(ex.runs) == 1
    asm, name, _ = ex.runs[0]
    assert name == "autocollect"
    frag = col.history[-1]
    assert frag.asm is asm
    assert pairs(frag) == [(BASE, COLLECT_VA), (BASE + 3 * STRIDE, COLLECT_VA), (BASE + 7 * STRIDE, COLLECT_VA)]


def test_skips_dead_and_collecting():
    obs = FakeObserver([coin(0, CoinType.SUN, dead=True), coin(1, CoinType.SUN, collecting=True),
                        coin(2, CoinType.SUN)])
    col, ex = collector(obs)
    assert col.tick() == 1
    assert pairs(col.history[-1]) == [(BASE + 2 * STRIDE, COLLECT_VA)]


def test_type_filter_default_and_custom():
    assert CoinType.SUN in DEFAULT_TYPES and CoinType.TROPHY in DEFAULT_TYPES
    assert CoinType.NONE not in DEFAULT_TYPES and CoinType.USABLE_SEED_PACKET not in DEFAULT_TYPES
    assert COIN_TYPES[7] == "final_seed_packet" and COIN_TYPES[8] == "trophy" and len(COIN_TYPES) == 28
    assert CoinType.AWARD_MONEY_BAG in AWARD_TYPES
    obs = FakeObserver([coin(0, CoinType.SUN), coin(1, CoinType.GOLD), coin(2, CoinType.NONE),
                        coin(3, CoinType.USABLE_SEED_PACKET)])
    col, ex = collector(obs)
    assert col.tick() == 2
    col2, ex2 = collector(obs, types=[CoinType.GOLD])
    assert col2.tick() == 1
    assert pairs(col2.history[-1]) == [(BASE + STRIDE, COLLECT_VA)]


def test_nothing_to_collect_emits_nothing():
    col, ex = collector(FakeObserver([]))
    assert col.tick() == 0 and ex.runs == [] and col.stats.calls == 0


def test_guards_paused_and_ui():
    coins = [coin(0, CoinType.SUN)]
    col, ex = collector(FakeObserver(coins, paused=True))
    assert col.tick() == 0 and col.collect_now() == 0 and ex.runs == []
    assert col.stats.skipped_paused == 2
    for ui in (0, 1, 2, 4, 7):
        col, ex = collector(FakeObserver(coins, ui=ui))
        assert col.tick() == 0 and ex.runs == []
        assert col.stats.skipped_ui == 1


def test_rate_limit_by_main_counter():
    obs = FakeObserver([coin(0, CoinType.SUN)], clock=100)
    col, ex = collector(obs, every_frames=10)
    assert col.tick() == 1
    obs.clock = 105
    assert col.tick() == 0          # too soon
    obs.clock = 109
    assert col.tick() == 0
    obs.clock = 110
    assert col.tick() == 1          # delta == every_frames
    obs.clock = 50                  # clock went backwards (new level): allowed
    assert col.tick() == 1
    assert len(ex.runs) == 3 and col.stats.ticks == 3
    # collect_now ignores the limit and resets it
    obs.clock = 51
    assert col.collect_now() == 1
    obs.clock = 55
    assert col.tick() == 0
    # no limit
    col0, ex0 = collector(obs, every_frames=0)
    assert col0.tick() == 1 and col0.tick() == 1


def test_rate_limit_does_not_read_coins():
    obs = FakeObserver([coin(0, CoinType.SUN)], clock=0)
    col, ex = collector(obs, every_frames=10)
    col.tick()
    n = obs.coin_reads
    obs.clock = 3
    col.tick()
    assert obs.coin_reads == n


def test_batch_cap():
    obs = FakeObserver([coin(i, CoinType.SUN) for i in range(25)])
    col, ex = collector(obs, every_frames=0)
    assert col.tick() == 20
    assert len(pairs(col.history[-1])) == 20
    col2, ex2 = collector(obs, every_frames=0, batch=5)
    assert col2.tick() == 5 and len(ex2.runs) == 1


def test_stats():
    obs = FakeObserver([coin(0, CoinType.SUN), coin(1, CoinType.SMALLSUN), coin(2, CoinType.LARGESUN),
                        coin(3, CoinType.SILVER), coin(4, CoinType.GOLD), coin(5, CoinType.DIAMOND),
                        coin(6, CoinType.TROPHY)])
    col, ex = collector(obs)
    col.tick()
    s = col.stats
    assert isinstance(s, CollectStats)
    assert s.collected == 7 and s.calls == 1 and s.ticks == 1
    assert s.sun == 25 + 15 + 50 and s.money == 1 + 5 + 100
    assert s.by_type == {4: 1, 5: 1, 6: 1, 1: 1, 2: 1, 3: 1, 8: 1}
    summ = s.summary()
    assert summ["by_type"]["sun"] == 1 and summ["by_type"]["trophy"] == 1 and summ["collected"] == 7
    col.reset()
    assert col.stats.collected == 0 and col.history == []


def test_disabled_and_error_recording():
    class Boom(FakeExecutor):
        def run(self, *a, **k):
            raise RuntimeError("nope")

    obs = FakeObserver([coin(0, CoinType.SUN)])
    col, ex = collector(obs, executor=Boom(), every_frames=0)
    assert col.tick() == 0                     # tick swallows
    assert col.stats.errors == 1 and "nope" in col.stats.last_error and col.stats.collected == 0
    with pytest.raises(RuntimeError):          # collect_now raises
        col.collect_now()
    col.enabled = False
    good, ex2 = collector(obs, every_frames=0)
    good.enabled = False
    assert good.tick() == 0 and ex2.runs == []


def test_actions_path_uses_conv_and_run():
    """An ``Actions``-like object: its conv + ``_run`` are used, session read_ptr for the base."""
    class Session:
        def read_ptr(self, addr):
            assert addr == 0x5000 + GOTY.board.coins
            return BASE

    class Obs(FakeObserver):
        coins_base = None  # force the board_ptr + session path

        def board_ptr(self):
            return 0x5000

    class FakeActions:
        def __init__(self):
            self.conv = convention_for(GOTY, asm_factory=StubAsm)
            self.session = Session()
            self.history = []
            self.ran = []

        def _run(self, frag, name, at_tick=None):
            self.history.append(frag)
            self.ran.append(name)
            return 0

    obs = Obs([coin(2, CoinType.SUN)])
    acts = FakeActions()
    col = AutoCollector(acts, obs, GOTY)
    assert col.conv is acts.conv and col.actions is acts
    assert col.collect_now() == 1
    assert acts.ran == ["autocollect"]
    assert pairs(acts.history[-1]) == [(BASE + 2 * STRIDE, COLLECT_VA)]


# ---------------------------------------------------------------------------
# executor hooks
# ---------------------------------------------------------------------------

@dataclass
class Sample:
    clock: int
    wave: int
    paused: bool = False
    level_complete: bool = False


class TickClock:
    """Minimal planned-executor clock: wave 1 refreshes at 0, level completes at ``end``."""

    def __init__(self, end=60, step=3):
        self.clock = -step
        self.end = end
        self.step = step

    def read(self):
        self.clock += self.step
        return Sample(self.clock, 1, level_complete=self.clock >= self.end)

    def wave_time(self, sample, wave):
        return sample.clock if wave == 1 else None

    def time_to_next_refresh(self, sample):
        return None

    def paused(self):
        return False

    def refresh_point(self, wave):
        return None

    def wait_until(self, pred, timeout, eta=None, pause_timeout=None):
        # short slices (like the real clock's timeout) so the scheduler loop polls often
        for _ in range(2):
            if pred(self.read()):
                return True
        return False


def ice_plan(op_cs):
    """One ice action at wave 1 ``op_cs`` so the executor polls until then."""
    a = Action(1, op_cs + 100, op_cs, KIND_ICE, "I", int(PlantType.ICE_SHROOM), Target((1,), 1.0), None,
               Op("I", KIND_ICE))
    return Plan((a,), ())


class FakeBackend:
    mode = "remote"

    def __init__(self):
        self.calls = []

    def plant_card(self, plant, row, col, imitater=False):
        self.calls.append(("plant_card", plant, row, col))


class SyncedObserver(FakeObserver):
    """Board clock follows the executor clock; a new sun appears every poll."""

    def __init__(self, clk):
        super().__init__([])
        self.clk = clk

    def board(self):
        return Board(False, self.clk.clock)

    def coins(self, include_dead=True):
        self.coin_reads += 1
        return (coin(self.clk.clock % 10, CoinType.SUN),)


def test_planned_executor_ticks_collector_and_reports():
    clk = TickClock(end=60, step=3)
    obs = SyncedObserver(clk)
    col, ex = collector(obs, every_frames=10)
    backend = FakeBackend()
    pe = PlannedExecutor(clk, backend, ice_plan(45), late_policy="warn", collector=col)
    report = pe.run()
    assert report.stop_reason == "plan exhausted" and len(backend.calls) == 1
    assert clk.clock >= 45
    # every poll (clock step 3) ticks the collector; rate limit 10 -> roughly one call per 12 ticks
    assert 3 <= col.stats.calls <= 6
    assert col.stats.collected == col.stats.calls and col.stats.sun == 25 * col.stats.calls
    assert report.collected["collected"] == col.stats.collected
    assert report.summary()["collected"]["sun"] == col.stats.sun
    assert "collected:" in report.to_text()
    assert report.to_dict()["summary"]["collected"]["calls"] == col.stats.calls


def test_planned_executor_without_collector_reports_none():
    clk = TickClock(end=60)
    report = PlannedExecutor(clk, FakeBackend(), ice_plan(6), late_policy="warn").run()
    assert report.collected is None and report.summary()["collected"] is None
    assert ExecutionReport("m", "b").collected is None


def test_planned_executor_dry_run_skips_collector():
    clk = TickClock(end=30)
    obs = SyncedObserver(clk)
    col, ex = collector(obs, every_frames=0)
    PlannedExecutor(clk, FakeBackend(), ice_plan(12), late_policy="warn", dry_run=True, collector=col).run()
    assert ex.runs == []


def test_reactive_runner_ticks_collector():
    from pvztools.control.executor.reactive import ReactiveRunner

    @dataclass
    class RBoard:
        clock: int = 0
        wave: int = 1
        total_waves: int = 10
        countdown: int = 500
        hp_threshold: int = 0
        paused: bool = False
        sun: int = 0

    class RObs(FakeObserver):
        def __init__(self):
            super().__init__([coin(0, CoinType.SUN)])
            self.t = 0

        def game(self):          # called once per collector tick
            self.t += 5
            return Game(3)

        def board(self):
            return RBoard(clock=self.t)

    class Policy:
        def decide(self, v):
            return []

    obs = RObs()
    col, ex = collector(obs, every_frames=10)
    runner = ReactiveRunner(None, object(), obs, Policy(), sleep=lambda s: None, high_resolution=False,
                            collector=col)
    runner.run(max_polls=6)
    assert runner.collector is col
    assert col.stats.calls == 3          # clocks 5,10,15,20,25,30 -> 5, 15, 25
    assert len(ex.runs) == 3
