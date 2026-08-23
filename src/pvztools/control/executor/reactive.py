"""Reactive executor (DESIGN §6.2b): read state, ask a policy, act, log.

::

    runner = ReactiveRunner(clock, game.actions, game.observer, PSeriesPolicy([(2, 9), (5, 9)]))
    log = runner.run(stop_wave=20)

Loop (every ``poll_ms``):

1. read ``observer.board()`` (+ plants / seeds / zombies) and assemble a
   :class:`~pvztools.control.executor.policies.WorldView`;
2. ``policy.decide(view)`` -> decisions;
3. execute them on ``backend`` (duck-typed :class:`~pvztools.control.actions.Actions`:
   ``fire_cobs`` / ``put_plant`` / ``remove_plant``);
4. append a :class:`LogEntry` to the :class:`ReactiveLog`.

Time base: the game's battle clock (``Board.clock`` = main_counter, cs).
``wave_time`` = ``clock - refresh_clock`` where the refresh point is the
first poll at which ``Board.wave`` changed.  When the runner starts mid-wave
the refresh point is *estimated* as ``clock - (countdown_init - countdown)``
(``wave_time_exact=False`` in the view); before the first wave (``wave == 0``)
``wave_time = -countdown``.  With a 5 ms poll the observed transition is at
most ~1 tick late (the game can also advance several ticks per rendered
frame when it catches up, so ``wave_time`` may jump by 2-5).

Pause handling: while ``Board.paused`` is set nothing is executed and the
entry is logged as ``paused``; it is not a timeout.  (A 5 ms poll against a
10 ms tick legitimately reads the same clock twice, so "clock did not
advance" is *not* used as a pause signal.)

Plant / shovel decisions are debounced: the same ``(kind, row, col)`` is not
executed again within ``plant_debounce_cs`` game ticks (default 50) -- the
game needs a few frames to process posted clicks and a stateless policy
would otherwise re-plant every poll until the plant shows up (the entry is
logged with ``executed=False`` and ``error='debounced'``).

``run`` returns the log also on Ctrl-C and when the level ends
(``NotInLevel`` from the observer): see :attr:`ReactiveLog.stop_reason`.

``clock`` is an optional object with ``now() -> int`` (game cs) and / or
``sleep(seconds)``; when absent the board's own ``clock`` field and
``time.sleep`` are used.  On Windows the run is wrapped in
``timing_win.high_resolution()`` (``timeBeginPeriod(1)``) so the 5 ms poll
sleeps really are 5 ms.
"""
from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from .policies import (
    COB_CANNON,
    COB_READY,
    KIND_FIRE,
    KIND_PLANT,
    KIND_SHOVEL,
    KIND_WAIT,
    CobView,
    Decision,
    PlantView,
    SeedView,
    WorldView,
    ZombieView,
)

try:  # sibling module written by the external executor; optional here
    from .timing_win import high_resolution as _high_resolution
except Exception:  # pragma: no cover - fallback when the module is absent
    @contextlib.contextmanager
    def _high_resolution(period_ms: int = 1) -> Iterator[bool]:
        yield False

try:
    from ...observe.snapshot import NotInLevel as _NotInLevel
except Exception:  # pragma: no cover
    _NotInLevel = None  # type: ignore

__all__ = ["ReactiveRunner", "ReactiveLog", "LogEntry", "build_view", "ReactiveError", "DEFAULT_PLANT_DEBOUNCE_CS"]

#: ticks within which an identical plant / shovel decision is not repeated
DEFAULT_PLANT_DEBOUNCE_CS = 50

#: Cob cannon arming: ``state_countdown`` + loading animation (source-map §6.3).
COB_ARMING, COB_LOADING, COB_FIRING = 35, 36, 38
COB_CHARGE_CS = 475
COB_LOADING_CS = 175


class ReactiveError(RuntimeError):
    """An action failed and ``on_error='abort'`` was requested."""


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

@dataclass
class LogEntry:
    time: int
    wave: int
    wave_time: int
    decision: Decision
    reason: str
    observed: Dict[str, Any]
    executed: bool = False
    result: Any = None
    error: Optional[str] = None

    @property
    def kind(self) -> str:
        return self.decision.kind

    def as_dict(self) -> Dict[str, Any]:
        return {
            "time": self.time, "wave": self.wave, "wave_time": self.wave_time,
            "kind": self.decision.kind, "args": dict(self.decision.args), "reason": self.reason,
            "observed": dict(self.observed), "executed": self.executed, "error": self.error,
        }


class ReactiveLog:
    """Ordered record of every decision the runner saw (including ``wait``)."""

    def __init__(self, keep_waits: bool = True) -> None:
        self.entries: List[LogEntry] = []
        self.keep_waits = keep_waits
        self.polls = 0
        self.paused_polls = 0
        self.stop_reason = ""

    def add(self, entry: LogEntry) -> None:
        if entry.decision.kind == KIND_WAIT and not self.keep_waits:
            return
        self.entries.append(entry)

    def actions(self) -> List[LogEntry]:
        return [e for e in self.entries if e.decision.kind != KIND_WAIT]

    def fires(self) -> List[LogEntry]:
        return [e for e in self.entries if e.decision.kind == KIND_FIRE]

    def errors(self) -> List[LogEntry]:
        return [e for e in self.entries if e.error]

    def as_dicts(self) -> List[Dict[str, Any]]:
        return [e.as_dict() for e in self.entries]

    def summary(self) -> str:
        acts = self.actions()
        lines = ["polls=%d actions=%d fires=%d errors=%d paused=%d%s" % (
            self.polls, len(acts), len(self.fires()), len(self.errors()), self.paused_polls,
            (" stop=%s" % self.stop_reason) if self.stop_reason else "")]
        for e in acts:
            lines.append("  w%-2d %5d cs  %-8s %s%s" % (
                e.wave, e.wave_time, e.decision.kind, e.reason, (" !! " + e.error) if e.error else ""))
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[LogEntry]:
        return iter(self.entries)


# ---------------------------------------------------------------------------
# view assembly
# ---------------------------------------------------------------------------

def _cob_ready_in(p: Any) -> Optional[int]:
    st = int(p.state)
    if st == COB_READY:
        return 0
    cd = getattr(p, "state_countdown", None)
    if st == COB_ARMING and cd is not None:
        return int(cd) + COB_CHARGE_CS
    if st == COB_LOADING:
        return COB_LOADING_CS
    if st == COB_FIRING:
        return None
    return None


def build_view(board: Any, plants: Any = (), seeds: Any = (), zombies: Any = (), *,
               wave_time: int = 0, wave_time_exact: bool = True, pairs_fired: int = 0,
               last_fire_wave_time: Optional[int] = None) -> WorldView:
    """Assemble a :class:`WorldView` from snapshot objects (model.types)."""
    cobs: List[CobView] = []
    pv: List[PlantView] = []
    for p in plants:
        if getattr(p, "dead", False) or getattr(p, "squished", False):
            continue
        row, col = int(p.row) + 1, int(p.col) + 1
        pv.append(PlantView(int(p.index), row, col, int(p.type), int(p.state)))
        if int(p.type) == COB_CANNON:
            cobs.append(CobView(int(p.index), row, col, int(p.state), _cob_ready_in(p)))
    sv = tuple(SeedView(int(s.slot), int(s.type), bool(s.usable), int(getattr(s, "cd_remaining", 0)))
               for s in seeds if int(s.type) >= 0)
    zv: List[ZombieView] = []
    wave_zombie_hp = 0
    have_from_wave = False
    for z in zombies:
        if getattr(z, "dead", False):
            continue
        fw = getattr(z, "from_wave", None)
        hp = int(getattr(z, "total_hp", z.hp))
        zv.append(ZombieView(int(z.index), int(z.row), float(z.x), int(z.type), hp, fw))
        if fw is not None:
            have_from_wave = True
            if fw == int(board.wave) - 1:
                wave_zombie_hp += hp
    wave_hp = getattr(board, "wave_hp", None)
    if wave_hp is None and have_from_wave:
        wave_hp = wave_zombie_hp
    return WorldView(
        time=int(board.clock), wave=int(board.wave), wave_time=int(wave_time),
        countdown=int(board.countdown), hp_threshold=int(board.hp_threshold), wave_hp=wave_hp,
        huge_wave_countdown=int(getattr(board, "huge_wave_countdown", 0) or 0),
        total_waves=int(getattr(board, "total_waves", 20) or 20), paused=bool(board.paused),
        scene=int(getattr(board, "scene", 0) or 0), cobs=tuple(cobs), seeds=sv, plants=tuple(pv),
        zombies=tuple(zv), pairs_fired=int(pairs_fired), last_fire_wave_time=last_fire_wave_time,
        wave_time_exact=bool(wave_time_exact),
    )


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

class ReactiveRunner:
    """Poll -> decide -> act loop (module doc).

    ``backend`` needs ``fire_cobs(fires)``, ``put_plant(row, col, plant)``,
    ``remove_plant(row, col)``; ``observer`` needs ``board()`` and optionally
    ``plants()`` / ``seeds()`` / ``zombies()``.  ``on_error``: ``'warn'``
    (log and continue, default) or ``'abort'`` (raise :class:`ReactiveError`).
    """

    def __init__(self, clock: Any, backend: Any, observer: Any, policy: Any, *,
                 poll_ms: int = 5, on_error: str = "warn", log: Optional[ReactiveLog] = None,
                 sleep: Optional[Callable[[float], None]] = None, high_resolution: bool = True,
                 plant_debounce_cs: int = DEFAULT_PLANT_DEBOUNCE_CS,
                 collector: Any = None) -> None:
        if on_error not in ("warn", "abort"):
            raise ValueError("on_error must be 'warn' or 'abort'")
        self.clock = clock
        self.backend = backend
        self.observer = observer
        self.policy = policy
        self.poll_ms = int(poll_ms)
        self.on_error = on_error
        self.log = log if log is not None else ReactiveLog()
        self._sleep = sleep
        self.high_resolution = high_resolution
        #: optional :class:`~pvztools.control.autocollect.AutoCollector`,
        #: ticked once per :meth:`step` (before the policy decides)
        self.collector = collector
        # wave bookkeeping
        self._last_wave: Optional[int] = None
        self._refresh_clock: Optional[int] = None
        self._refresh_exact = False
        self._pairs_fired = 0
        self._last_fire_wave_time: Optional[int] = None
        self.plant_debounce_cs = int(plant_debounce_cs)
        self._recent_plants: Dict[Tuple[str, int, int], int] = {}  # (kind,row,col) -> game time

    @staticmethod
    def _plant_key(d: Decision) -> Tuple[str, int, int]:
        return (d.kind, int(d.args.get("row", 0)), int(d.args.get("col", 0)))

    def _debounced(self, d: Decision, now: int) -> bool:
        """True when an identical plant / shovel decision ran too recently."""
        if d.kind not in (KIND_PLANT, KIND_SHOVEL) or self.plant_debounce_cs <= 0:
            return False
        last = self._recent_plants.get(self._plant_key(d))
        return last is not None and 0 <= now - last < self.plant_debounce_cs

    # -- time ----------------------------------------------------------------
    def _now(self, board: Any) -> int:
        c = self.clock
        if c is not None:
            fn = getattr(c, "now", None)
            if callable(fn):
                return int(fn())
            if callable(c):
                return int(c())
        return int(board.clock)

    def _sleep_poll(self) -> None:
        s = self.poll_ms / 1000.0
        if self._sleep is not None:
            self._sleep(s)
            return
        fn = getattr(self.clock, "sleep", None)
        if callable(fn):
            fn(s)
        else:
            time.sleep(s)

    # -- observe -------------------------------------------------------------
    def _read(self, name: str) -> Any:
        fn = getattr(self.observer, name, None)
        if not callable(fn):
            return ()
        return fn()

    def _wave_time(self, board: Any, now: int) -> Tuple[int, bool]:
        wave = int(board.wave)
        if wave != self._last_wave:
            if self._last_wave is None:
                # started mid-wave: estimate from the countdown that was reset at refresh
                if wave <= 0:
                    self._refresh_clock = now + int(board.countdown)
                else:
                    init = int(getattr(board, "countdown_init", 0) or 0)
                    self._refresh_clock = now - max(0, init - int(board.countdown))
                self._refresh_exact = False
            else:
                self._refresh_clock = now
                self._refresh_exact = True
            self._last_wave = wave
            self._pairs_fired = 0
            self._last_fire_wave_time = None
        assert self._refresh_clock is not None
        return now - self._refresh_clock, self._refresh_exact

    def view(self) -> WorldView:
        """Observe once and build the :class:`WorldView` (no decision)."""
        board = self.observer.board()
        now = self._now(board)
        wt, exact = self._wave_time(board, now)
        return build_view(board, self._read("plants"), self._read("seeds"), self._read("zombies"),
                          wave_time=wt, wave_time_exact=exact, pairs_fired=self._pairs_fired,
                          last_fire_wave_time=self._last_fire_wave_time)

    # -- act -----------------------------------------------------------------
    def execute(self, d: Decision) -> Any:
        a = d.args
        if d.kind == KIND_FIRE:
            return self.backend.fire_cobs(list(a["fires"]))
        if d.kind == KIND_PLANT:
            return self.backend.put_plant(a["row"], a["col"], a["plant"])
        if d.kind == KIND_SHOVEL:
            return self.backend.remove_plant(a["row"], a["col"])
        if d.kind == KIND_WAIT:
            return None
        raise ReactiveError("unknown decision kind %r" % (d.kind,))

    def _observed(self, v: WorldView) -> Dict[str, Any]:
        return {
            "countdown": v.countdown, "hp_threshold": v.hp_threshold, "wave_hp": v.wave_hp,
            "huge_wave_countdown": v.huge_wave_countdown, "ready_cobs": len(v.ready_cobs),
            "cobs": len(v.cobs), "zombies": len(v.zombies), "paused": v.paused,
            "pairs_fired": v.pairs_fired, "wave_time_exact": v.wave_time_exact,
        }

    def step(self) -> Tuple[WorldView, List[LogEntry]]:
        """One poll: observe, decide, execute, log.  Returns the view and the
        entries added."""
        v = self.view()
        self.log.polls += 1
        entries: List[LogEntry] = []
        if self.collector is not None and not v.paused:
            try:
                self.collector.tick(v)
            except Exception as exc:  # noqa: BLE001 - the collector records its own errors
                if self.on_error == "abort":
                    raise ReactiveError("collector: %s" % exc) from exc
        if v.paused:
            self.log.paused_polls += 1
            e = LogEntry(v.time, v.wave, v.wave_time, Decision.wait("paused"), "paused", self._observed(v))
            self.log.add(e)
            return v, [e]
        for d in self.policy.decide(v):
            e = LogEntry(v.time, v.wave, v.wave_time, d, d.reason, self._observed(v))
            if d.kind != KIND_WAIT and self._debounced(d, v.time):
                e.error = "debounced"
            elif d.kind != KIND_WAIT:
                try:
                    e.result = self.execute(d)
                    e.executed = True
                    if d.kind in (KIND_PLANT, KIND_SHOVEL):
                        self._recent_plants[self._plant_key(d)] = v.time
                except Exception as exc:  # noqa: BLE001 - logged, policy decides next poll
                    e.error = "%s: %s" % (type(exc).__name__, exc)
                    if self.on_error == "abort":
                        self.log.add(e)
                        raise ReactiveError(e.error) from exc
                if e.executed and d.kind == KIND_FIRE:
                    self._pairs_fired += 1
                    self._last_fire_wave_time = v.wave_time
            self.log.add(e)
            entries.append(e)
        return v, entries

    def run(self, *, stop_wave: Optional[int] = None, max_polls: Optional[int] = None,
            until: Optional[Callable[[WorldView], bool]] = None) -> ReactiveLog:
        """Loop until ``until(view)`` is true, wave ``stop_wave`` is over
        (``view.wave > stop_wave``, so the stop wave itself is acted on; when
        ``stop_wave`` is the level's last wave the loop ends at its refresh,
        there is nothing left to activate) or ``max_polls`` polls.  Ctrl-C
        and the level ending (``NotInLevel``) end the loop and the log is
        still returned (``log.stop_reason``)."""
        ctx = _high_resolution(1) if self.high_resolution else contextlib.nullcontext()
        n = 0
        with ctx:
            try:
                while True:
                    v, _ = self.step()
                    n += 1
                    if until is not None and until(v):
                        self.log.stop_reason = "until"
                        break
                    if stop_wave is not None and (v.wave > stop_wave or (
                            v.wave >= stop_wave and v.total_waves > 0 and v.wave >= v.total_waves)):
                        self.log.stop_reason = "wave %d reached" % v.wave
                        break
                    if max_polls is not None and n >= max_polls:
                        self.log.stop_reason = "max polls"
                        break
                    self._sleep_poll()
            except KeyboardInterrupt:
                self.log.stop_reason = "interrupted"
            except Exception as exc:
                if _NotInLevel is not None and isinstance(exc, _NotInLevel):
                    self.log.stop_reason = "level ended"
                else:
                    raise
        return self.log
