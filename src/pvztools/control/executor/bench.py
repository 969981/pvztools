"""``bench-clock``: measure how well the external executor can hit game ticks.

Two read-only measurements against a running level (DESIGN §6: "库内置
bench-clock 实测"):

(a) **poll latency** -- wall time of one ``observer.board()`` read (the cost
    of every sample the clock takes);
(b) **target jitter** -- repeatedly pick a target tick ``step_cs`` ahead of
    the current ``main_counter``, wait for it with the hybrid waiter
    (:meth:`GameClock.wait_until` with an ETA: coarse sleep, then
    busy-poll), and record ``observed_main_counter - target`` in ticks.
    ``0`` means the first poll after the boundary saw exactly the target
    tick; ``+n`` means the waiter woke ``n`` ticks late (frame catch-up or
    scheduling), negative cannot happen.

Nothing is written and no input is posted.  A paused game (or a wave-end
dialog) simply produces fewer samples: a wait that times out is counted in
``timeouts``.  When the observer is not in a level the result has
``in_level=False`` and no samples.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .clock import ClockTimeout, GameClock
from .timing_win import high_resolution

__all__ = ["BenchResult", "bench_clock", "format_bench", "percentile"]

GAME_UI_IN_LEVEL = 3
DEFAULT_STEP_CS = 50


def percentile(values: List[float], p: float) -> Optional[float]:
    """Nearest-rank percentile (``p`` in 0..100); ``None`` when empty."""
    if not values:
        return None
    s = sorted(values)
    rank = max(1, int(math.ceil(p / 100.0 * len(s))))
    return float(s[rank - 1])


@dataclass
class BenchResult:
    seconds: float
    step_cs: int
    frame_ms: int
    in_level: bool = True
    #: (a) poll latency in milliseconds, one entry per board read
    poll_ms: List[float] = field(default_factory=list)
    #: (b) observed - target in ticks, one entry per target
    deltas: List[int] = field(default_factory=list)
    #: wall overshoot of each wait relative to the ideal ``(ticks * frame_ms)``, ms
    wait_overshoot_ms: List[float] = field(default_factory=list)
    timeouts: int = 0
    start_clock: Optional[int] = None
    end_clock: Optional[int] = None
    wall_seconds: float = 0.0
    high_resolution: bool = False

    # -- derived ------------------------------------------------------------
    @property
    def histogram(self) -> Dict[int, int]:
        return dict(sorted(Counter(self.deltas).items()))

    def _stats(self, values: List[float]) -> Dict[str, Optional[float]]:
        if not values:
            return {"count": 0, "mean": None, "p50": None, "p95": None, "max": None, "min": None}
        return {
            "count": len(values),
            "mean": sum(values) / len(values),
            "p50": percentile(values, 50),
            "p95": percentile(values, 95),
            "max": max(values),
            "min": min(values),
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "in_level": self.in_level,
            "seconds": self.seconds,
            "wall_seconds": self.wall_seconds,
            "step_cs": self.step_cs,
            "frame_ms": self.frame_ms,
            "high_resolution": self.high_resolution,
            "start_clock": self.start_clock,
            "end_clock": self.end_clock,
            "timeouts": self.timeouts,
            "poll_ms": self._stats(self.poll_ms),
            "jitter_ticks": self._stats([float(d) for d in self.deltas]),
            "hit_rate": (sum(1 for d in self.deltas if d == 0) / len(self.deltas)) if self.deltas else None,
            "wait_overshoot_ms": self._stats(self.wait_overshoot_ms),
            "histogram": {str(k): v for k, v in self.histogram.items()},
        }


def bench_clock(observer: Any, seconds: float = 10.0, *, step_cs: int = DEFAULT_STEP_CS,
                clock: Optional[GameClock] = None, now: Callable[[], float] = time.perf_counter,
                poll_samples: int = 200, use_high_resolution: bool = True) -> BenchResult:
    """Run both measurements for about ``seconds`` wall seconds (read-only).

    ``observer`` needs ``board()`` and ``game()`` (``Observer`` or a fake);
    ``clock`` may be an existing :class:`GameClock` (one is built otherwise).
    """
    clk = clock if clock is not None else GameClock(observer, now=now)
    res = BenchResult(seconds=float(seconds), step_cs=int(step_cs), frame_ms=int(clk.frame_ms))
    try:
        ui = int(observer.game().ui)
    except Exception:
        ui = GAME_UI_IN_LEVEL
    if ui != GAME_UI_IN_LEVEL:
        res.in_level = False
        return res
    t0 = now()
    with high_resolution(1) as applied:
        res.high_resolution = bool(applied)
        # (a) poll latency
        for _ in range(max(1, int(poll_samples))):
            a = now()
            observer.board()
            res.poll_ms.append((now() - a) * 1000.0)
        # (b) target jitter
        first = clk.read()
        res.start_clock = first.main_counter
        deadline = t0 + float(seconds)
        frame_s = res.frame_ms / 1000.0
        while now() < deadline:
            s = clk.read()
            if s.paused:
                clk.sleep(0.02)
                continue
            target = s.main_counter + int(step_cs)
            ideal = (target - s.main_counter) * frame_s
            w0 = now()
            try:
                hit = clk.wait_until(
                    lambda x, t=target: x.main_counter >= t,
                    timeout=max(1.0, ideal * 3 + 0.5),
                    eta=lambda x, t=target: clk._eta_ticks(t - x.main_counter),
                )
            except ClockTimeout:
                res.timeouts += 1
                continue
            res.deltas.append(int(hit.main_counter - target))
            res.wait_overshoot_ms.append(((now() - w0) - ideal) * 1000.0)
            res.end_clock = hit.main_counter
    res.wall_seconds = now() - t0
    return res


def _fmt(v: Optional[float], nd: int = 2) -> str:
    return "-" if v is None else ("%.*f" % (nd, v))


def format_bench(res: BenchResult) -> str:
    """Human-readable stats + histogram."""
    s = res.summary()
    if not res.in_level:
        return "bench-clock: 不在关卡中（game_ui != 3），跳过。"
    pm, jt, ov = s["poll_ms"], s["jitter_ticks"], s["wait_overshoot_ms"]
    lines = [
        "bench-clock: %.1fs wall, frame=%dms, step=%dcs, clock %s -> %s, timeBeginPeriod(1)=%s"
        % (res.wall_seconds, res.frame_ms, res.step_cs, res.start_clock, res.end_clock,
           "on" if res.high_resolution else "off"),
        "(a) poll latency  n=%d  mean=%sms  p50=%sms  p95=%sms  max=%sms"
        % (pm["count"], _fmt(pm["mean"], 3), _fmt(pm["p50"], 3), _fmt(pm["p95"], 3), _fmt(pm["max"], 3)),
        "(b) target jitter n=%d  mean=%s  p95=%s  max=%s ticks  hit(0)=%s  timeouts=%d"
        % (jt["count"], _fmt(jt["mean"]), _fmt(jt["p95"], 0), _fmt(jt["max"], 0),
           "-" if s["hit_rate"] is None else "%.0f%%" % (100 * s["hit_rate"]), res.timeouts),
        "    wait overshoot vs ideal: mean=%sms  p95=%sms  max=%sms"
        % (_fmt(ov["mean"]), _fmt(ov["p95"]), _fmt(ov["max"])),
    ]
    hist = res.histogram
    if hist:
        top = max(hist.values())
        lines.append("    histogram (observed - target, ticks):")
        for k, v in hist.items():
            bar = "#" * max(1, int(round(40.0 * v / top)))
            lines.append("    %+3d | %-40s %d" % (k, bar, v))
    return "\n".join(lines)
