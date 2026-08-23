"""Planned executor (DESIGN §6 / §6.2(b) "计划式"): run an :class:`expand.Plan`
against the live game clock.

Time model
----------
All times are game ticks (1 cs = one ``Board.main_counter`` increment = one
``frame_duration`` ms, default 10). Every planned time is AvZ ``ATime(w, cs)``:
``cs`` relative to the *refresh point* of wave ``w`` (the tick at which
``Board.wave`` becomes ``w``); negative = before it.

The scheduler never sleeps on the wall clock by itself. It only asks the
:class:`GameClock` to wait for a predicate (``clock.wait_until``) and reads
samples from it; the clock owns the hybrid sleep/busy-wait strategy and
``timeBeginPeriod(1)``. The ETA hint handed to the clock is one tick short
(the last frame is spent busy-polling ``main_counter``, DESIGN §6).

**Backend lag / observed tick.** A backend may declare ``action_lag_cs``:
the number of ticks between the dispatch call and the tick the game acts
on (``InputBackend``: 2 -- card DOWN, frame, UP, frame, cell DOWN). The
scheduler dispatches ``lead_cs + action_lag_cs`` ticks early and reports
``observed_cs`` = dispatch tick + lag (the estimated *action* tick); the
tick at which the backend call returned is ``ActionRow.done_cs``. For a
backend without lag (remote calls are synchronous) ``observed_cs`` is the
tick read right after the call -- except for **remote cob groups**, which
are *frame-locked* (DESIGN §6.3): the scheduler passes
``at_tick = refresh_point(wave) + operation_cs`` (absolute ``main_counter``)
to ``fire_cob`` / ``fire_cobs`` when the backend accepts it, and
``observed_cs`` is then ``fired_tick - refresh_point`` where ``fired_tick``
is read back from the cannon's ``mShootingCounter`` (``FireResult``);
``done_cs`` keeps the post-call clock read. Input mode cannot fire two cannons in the
same tick (each shot is a 3-frame click sequence): the second cannon of a
``PP`` acts ~4 ticks after the first; the rows share the dispatch tick.

**Mid-wave start.** When the run starts inside a wave whose refresh point
the clock could only estimate (``RefreshPoint.source`` ``'first'`` /
``'countdown'``) a ``warning`` event is emitted and listed in
``report.warnings``: every step of that wave is timed against the estimate.

**Tail (end of plan).** With ``tail_timeout_s > 0`` an exhausted plan does
not end the run while the level is still going: the executor keeps polling
(collector tick on every poll, pauses tracked) until ``level_complete`` /
``NotInLevel`` or the tail timeout.  ``tail_policy='repeat'`` additionally
re-fires the cob groups of the last planned wave on every later wave
(targets / offsets of the last cycle, negative offsets clamped to 0, *fire
when ready* through the cd-wait path); ``None`` (default) only polls.  The
tail is summarised in ``report.meta['tail']``.  :meth:`release` (called at
the end of every run) disables whatever patch group the backend left on
(``main_loop_block`` after a remote-call timeout) so the game is never left
frozen.

**Level end.** ``NotInLevel`` from the clock (board freed: level won / lost,
back to the menu) ends the run with ``stop_reason='level ended'`` and the
report is still returned. Position inside a wave
is ``clock.wave_time(sample, wave)``; time to the next refresh is
``clock.time_to_next_refresh(sample)``.

**Ordering.** Steps are dispatched in chronological order on the live
clock, not in ``(wave, operation_cs)`` sort order: while wave ``w-1`` is
running, its remaining steps *and* the pre-refresh steps of wave ``w``
(``operation_cs < 0``) are candidates and whichever is due first goes first
(a ``w -200`` ice may well precede a ``w-1 +1700`` fodder shovel).

**Early refresh.** A wave's time axis stays valid after the next wave has
refreshed (AvZ ``ATime`` semantics): when wave ``w+1`` arrives early
(HP-threshold activation, zombies dying to ice / doom) the steps of ``w``
that are still pending are dispatched as soon as they are due on ``w``'s
axis -- they are *late* (handled by ``late_policy``) only when their planned
tick is already more than ``tolerance_cs`` in the past. A step is skipped
only when its wave's refresh point is unknown (the clock never observed the
wave, e.g. several waves passed inside one poll).

**Approximation for pre-refresh actions** (``operation_cs < 0``): the
wait condition is ``sample.wave == wave-1 and time_to_next_refresh(sample)
<= -operation_cs``. For normal waves ``time_to_next_refresh`` is the board
``countdown``; for the wave after a huge wave (w10 / w20) it is
``huge_wave_countdown`` (red text, ~750 cs) while the normal countdown has
dropped to its floor. Both are *forced*-refresh estimates: a wave can refresh
earlier when the zombie HP falls under ``hp_threshold`` (activation), which
no countdown predicts. A pre-refresh action therefore has a true error equal
to (activation earliness); the report shows it as the delta.

Interfaces assumed (written concurrently, duck-typed here)
---------------------------------------------------------
``GameClock`` (:mod:`.clock` or any look-alike)
    ``read() -> sample`` (attributes ``main_counter`` (or ``clock``),
    ``wave``, ``paused``, optional ``in_level`` / ``level_complete``);
    ``wave_time(sample, wave) -> int | None``;
    ``time_to_next_refresh(sample) -> Refresh | int | None`` (``.cs`` used
    when present);
    ``paused()`` / ``is_paused() -> bool``;
    ``wait_until(pred, timeout, eta=...)`` (``pred(sample)``; timeout in
    wall seconds; may raise a timeout error, which the scheduler treats as
    "slice over, poll again"). ``frame_ms()`` is optional (ETA hint).
``ActionBackend``
    ``plant_card(plant, row, col, imitater=False)``, ``shovel(row, col)``,
    ``fire_cob(cannon, row, col)``, ``fire_cobs([(cannon, (row, col)), ...])``,
    ``mode`` (``"input"`` | ``"remote"`` | ...; optional ``plant_mode`` /
    ``cob_mode`` when the two differ).
"""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ...schema.enums import IMITATER_OFFSET, PlantType
from ...schema.track.expand import KIND_COFFEE, Action, Plan
from .report import STATUS_CD_WAIT, ActionRow, ExecutionReport, PauseRecord, mode_label

try:  # optional: real clock's timeout type / winmm helper
    from .clock import ClockTimeout as _ClockTimeout
except Exception:  # pragma: no cover
    _ClockTimeout = None  # type: ignore
try:  # the observer's "board pointer is 0" error: level ended
    from ...observe.snapshot import NotInLevel as _NotInLevel
except Exception:  # pragma: no cover
    _NotInLevel = None  # type: ignore
try:
    from .timing_win import high_resolution as _high_resolution
except Exception:  # pragma: no cover
    _high_resolution = None  # type: ignore

__all__ = [
    "PlannedExecutor",
    "ExecutionAbort",
    "LATE_POLICIES",
    "DEFAULT_LEAD_CS",
    "STATUS_OK",
    "STATUS_LATE",
    "STATUS_SKIPPED",
    "STATUS_ABORTED",
    "STATUS_PENDING",
    "STATUS_DRY",
    "STATUS_FAILED",
    "STATUS_CD_WAIT",
    "DEFAULT_CD_WAIT_MAX_CS",
    "DEFAULT_TAIL_TIMEOUT_S",
    "TAIL_POLICIES",
    "MAIN_LOOP_PATCH",
]

LATE_POLICIES = ("warn", "skip", "abort")
DEFAULT_LEAD_CS = 1  # dispatch one tick early: a PostMessage click lands on the next pump

STATUS_PENDING = "pending"
STATUS_OK = "ok"
STATUS_LATE = "late"
STATUS_SKIPPED = "skipped"
STATUS_ABORTED = "aborted"
STATUS_DRY = "dry-run"
STATUS_FAILED = "failed"

#: how long a cob group may wait for a cannon that is not READY at its
#: planned tick before it is dispatched anyway (a full reload is 3475 cs)
DEFAULT_CD_WAIT_MAX_CS = 3600

#: default tail timeout used by ``PlaySession`` (wall seconds); 0 = no tail
DEFAULT_TAIL_TIMEOUT_S = 600.0
TAIL_POLICIES = (None, "repeat")
MAIN_LOOP_PATCH = "main_loop_block"

KIND_SHOVEL = "shovel"  # synthetic step: fodder removal (pvzt ``C t+N`` / ``t~T``)

# wait_until timeouts (seconds): short slices so pauses / wave jumps are noticed
_WAIT_SLICE_S = 0.25
_DEFAULT_FRAME_MS = 10


def _tick(sample: Any) -> Optional[int]:
    v = getattr(sample, "main_counter", None)
    if v is None:
        v = getattr(sample, "clock", None)
    return None if v is None else int(v)


def _attr_int(obj: Any, name: str) -> Optional[int]:
    v = getattr(obj, name, None)
    return None if v is None else int(v)


def _refresh_cs(value: Any) -> Optional[int]:
    """Unwrap ``Refresh.cs`` / plain int / None."""
    if value is None:
        return None
    cs = getattr(value, "cs", value)
    return None if cs is None else int(cs)


class ExecutionAbort(RuntimeError):
    """Raised internally to unwind the main loop on ``late_policy='abort'``."""


@dataclass
class _Step:
    """One dispatchable unit derived from an :class:`Action`."""

    wave: int
    operation_cs: int
    kind: str
    symbol: str
    plant: Optional[int]
    row: Optional[int]
    col: Optional[float]
    cannon: Optional[Tuple[int, int]]
    action: Action
    row_index: int = -1  # index into report rows


@dataclass
class _Group:
    wave: int
    operation_cs: int
    steps: List[_Step] = field(default_factory=list)

    @property
    def key(self) -> Tuple[int, int]:
        return (self.wave, self.operation_cs)


def _steps_from_plan(plan: Plan) -> List[_Step]:
    steps: List[_Step] = []
    for a in plan.actions:
        row = a.target.rows[0] if a.target is not None and a.target.rows else None
        col = a.target.col if a.target is not None else None
        steps.append(_Step(a.wave, a.operation_cs, a.kind, a.symbol, a.plant, row, col, a.cannon, a))
        if a.kind == "fodder":
            win = getattr(a.source_op, "window_cs", None)
            if win is not None and len(win) == 2 and win[1] is not None and win[1] > win[0]:
                shovel_cs = a.operation_cs + (win[1] - win[0])
                steps.append(_Step(a.wave, shovel_cs, KIND_SHOVEL, a.symbol, None, row, col, None, a))
    steps.sort(key=lambda s: (s.wave, s.operation_cs))
    return steps


def _group(steps: Sequence[_Step]) -> List[_Group]:
    groups: List[_Group] = []
    for s in steps:
        if groups and groups[-1].key == (s.wave, s.operation_cs):
            groups[-1].steps.append(s)
        else:
            groups.append(_Group(s.wave, s.operation_cs, [s]))
    return groups


class PlannedExecutor:
    """Dispatch a :class:`Plan` in ``(wave, operation_cs)`` order on the game clock.

    ``tolerance_cs``: a dispatch whose observed tick exceeds the planned tick
    by more than this is *late* and handled by ``late_policy``:
    ``'warn'`` (dispatch anyway, flag), ``'skip'`` (do not dispatch),
    ``'abort'`` (stop the run). ``dry_run=True`` does all the waiting but
    calls no backend method.
    """

    def __init__(
        self,
        clock: Any,
        backend: Any,
        plan: Plan,
        *,
        tolerance_cs: int = 2,
        late_policy: str = "warn",
        dry_run: bool = False,
        lead_cs: int = DEFAULT_LEAD_CS,
        cd_wait_max_cs: int = DEFAULT_CD_WAIT_MAX_CS,
        collector: Any = None,
        tail_timeout_s: float = 0.0,
        tail_policy: Optional[str] = None,
    ) -> None:
        if late_policy not in LATE_POLICIES:
            raise ValueError("late_policy must be one of %s" % (LATE_POLICIES,))
        if tail_policy not in TAIL_POLICIES:
            raise ValueError("tail_policy must be one of %s" % (TAIL_POLICIES,))
        #: keep polling after the plan while the level runs (0 = return at once)
        self.tail_timeout_s = max(0.0, float(tail_timeout_s or 0.0))
        self.tail_policy = tail_policy
        #: filled by :meth:`_run_tail` (also copied to ``report.meta['tail']``)
        self.tail: Dict[str, Any] = {}
        #: patch groups disabled by :meth:`release`
        self.released: List[str] = []
        self.clock = clock
        self.backend = backend
        self.plan = plan
        self.tolerance_cs = int(tolerance_cs)
        self.late_policy = late_policy
        self.dry_run = bool(dry_run)
        self.lead_cs = int(lead_cs)
        self.cd_wait_max_cs = max(0, int(cd_wait_max_cs))
        #: optional :class:`~pvztools.control.autocollect.AutoCollector`:
        #: ``tick(sample)`` runs on every clock poll (it rate-limits itself)
        self.collector = collector
        try:
            self.backend_lag_cs = max(0, int(getattr(backend, "action_lag_cs", 0) or 0))
        except (TypeError, ValueError):
            self.backend_lag_cs = 0
        self._steps = _steps_from_plan(plan)
        self._groups = _group(self._steps)
        self._callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self.report = ExecutionReport(
            mode="",
            backend=str(getattr(backend, "mode", "unknown")),
            dry_run=self.dry_run,
            tolerance_cs=self.tolerance_cs,
            late_policy=self.late_policy,
            plan_actions=len(plan.actions),
            plan_issues=[getattr(i, "code", str(i)) for i in plan.issues],
        )
        for s in self._steps:
            s.row_index = len(self.report.rows)
            self.report.rows.append(ActionRow(s.wave, s.symbol, s.kind, s.operation_cs, self._describe(s)))
        self._last_wave: Optional[int] = None
        self._pause_start: Optional[float] = None
        self._pause_tick: Optional[int] = None
        self._remote_modify_used = False  # remote plant_card / shovel (修改器)
        self._input_used = False
        self._stop_reason: Optional[str] = None
        self._last_wait_key: Optional[Tuple[int, int]] = None
        self._hires: Any = None
        #: remote cob backend accepts ``at_tick`` (frame lock, §6.3)
        self._cob_at_tick = self._backend_accepts_at_tick(backend)
        #: backend can tell whether a cannon is READY (``cobs_ready``):
        #: enables the "fire when ready" fallback for late cannons
        fn = getattr(backend, "cobs_ready", None)
        self._cobs_ready: Optional[Callable[[Sequence[Tuple[int, int]]], Sequence[bool]]] = fn if callable(fn) else None
        now = getattr(clock, "now", None)  # the clock's wall source (injectable in tests)
        self._now: Callable[[], float] = now if callable(now) else time.perf_counter
        try:
            fm = getattr(clock, "frame_ms", None)
            if callable(fm):  # method on fakes; a property on GameClock
                fm = fm()
            self._frame_s = (int(fm) if fm else _DEFAULT_FRAME_MS) / 1000.0
        except Exception:
            self._frame_s = _DEFAULT_FRAME_MS / 1000.0

    @staticmethod
    def _backend_accepts_at_tick(backend: Any) -> bool:
        """True when the backend fires cobs remotely *and* both
        ``fire_cob`` / ``fire_cobs`` take an ``at_tick`` keyword."""
        mode = getattr(backend, "cob_mode", getattr(backend, "mode", "input"))
        if mode != "remote":
            return False
        for name in ("fire_cob", "fire_cobs"):
            fn = getattr(backend, name, None)
            if not callable(fn):
                return False
            try:
                params = inspect.signature(fn).parameters
            except (TypeError, ValueError):
                return False
            if "at_tick" not in params and not any(
                    p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
                return False
        return True

    @staticmethod
    def _counter(sample: Any) -> Optional[int]:
        return _tick(sample)

    # --- hooks -------------------------------------------------------------
    def on_event(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register ``callback(event_dict)``; ``event_dict['event']`` is one of
        start, wave, wait, dispatch, late, skip, pause, resume, stop, error."""
        self._callbacks.append(callback)

    def _emit(self, event: str, **data: Any) -> None:
        ev = dict(data)
        ev["event"] = event
        self.report.events.append(ev)
        for cb in self._callbacks:
            cb(ev)

    # --- helpers -----------------------------------------------------------
    @staticmethod
    def _describe(s: _Step) -> str:
        if s.kind == "cob":
            return "cob %s -> (%s,%s)" % (s.cannon, s.row, s.col)
        if s.kind == KIND_SHOVEL:
            return "shovel (%s,%s)" % (s.row, s.col)
        return "%s plant=%s @(%s,%s)" % (s.kind, s.plant, s.row, s.col)

    def _wave_time(self, sample: Any, wave: int) -> Optional[int]:
        """AvZ-style time of ``sample`` relative to ``wave``'s refresh point."""
        sw = getattr(sample, "wave", None)
        if sw is None:
            return None
        if sw >= wave:
            return self.clock.wave_time(sample, wave)
        if sw == wave - 1:
            ttr = _refresh_cs(self.clock.time_to_next_refresh(sample))
            return None if ttr is None else -ttr
        return None

    def _clock_paused(self) -> bool:
        fn = getattr(self.clock, "paused", None)
        if callable(fn):
            try:
                return bool(fn())
            except TypeError:
                pass
        fn = getattr(self.clock, "is_paused", None)
        if callable(fn):
            try:
                return bool(fn())
            except TypeError:
                return False
        return False

    def _wait(self, pred: Callable[[Any], bool], eta: Optional[Callable[[Any], Optional[float]]] = None) -> None:
        """One bounded wait slice on the clock; timeouts just return."""
        try:
            try:
                # pause_timeout: a counter stall also ends the slice so the
                # scheduler can record it (the clock excludes paused time
                # from ``timeout`` and would otherwise wait silently)
                self.clock.wait_until(pred, _WAIT_SLICE_S, eta=eta, pause_timeout=_WAIT_SLICE_S)
            except TypeError:
                try:
                    self.clock.wait_until(pred, _WAIT_SLICE_S, eta=eta)
                except TypeError:
                    self.clock.wait_until(pred, _WAIT_SLICE_S)
        except Exception as e:
            if _ClockTimeout is not None and isinstance(e, _ClockTimeout):
                return
            if type(e).__name__.endswith("Timeout"):
                return
            raise

    def _collect_tick(self, sample: Any) -> None:
        """Give the autocollector one poll (errors are its own business;
        an exception here must not end the plan)."""
        if self.collector is None or self.dry_run:
            return
        try:
            self.collector.tick(sample)
        except Exception as e:  # noqa: BLE001
            self._emit("error", kind="collector", error=str(e))

    def _note_wave(self, sample: Any) -> None:
        w = getattr(sample, "wave", None)
        if w is None:
            return
        if self._last_wave is None or w != self._last_wave:
            if self._last_wave is not None and w > self._last_wave:
                tick = _tick(sample) or 0
                fn = getattr(self.clock, "refresh_point", None)  # the clock's (estimated) refresh tick
                rp = fn(w) if callable(fn) else None
                if rp is not None and getattr(rp, "main_counter", None) is not None:
                    tick = int(rp.main_counter)
                self.report.refresh_points[w] = tick
                self._emit("wave", wave=w, clock=tick, source=getattr(rp, "source", None))
            self._last_wave = w

    def _handle_pause(self, sample: Any) -> bool:
        """Track pause state; returns True while paused (caller keeps waiting)."""
        paused = bool(getattr(sample, "paused", False)) or self._clock_paused()
        if paused and self._pause_start is None:
            self._pause_start = self._now()
            self._pause_tick = _tick(sample)
            self._emit("pause", clock=self._pause_tick, wave=getattr(sample, "wave", None))
        elif not paused and self._pause_start is not None:
            dur = self._now() - self._pause_start
            self.report.pauses.append(PauseRecord(self._pause_tick, getattr(sample, "wave", None), dur))
            self._emit("resume", clock=_tick(sample), wall_seconds=dur)
            self._pause_start = None
            self._pause_tick = None
        return paused

    @staticmethod
    def _level_complete(sample: Any) -> bool:
        if getattr(sample, "level_complete", False):
            return True
        return not getattr(sample, "in_level", True)

    # --- main loop ---------------------------------------------------------
    def run(self) -> ExecutionReport:
        """Execute the plan; always returns the report (also on Ctrl-C)."""
        self._begin_timer_period()
        self.report.started_wall = time.time()
        self._emit("start", groups=len(self._groups), actions=len(self._steps), dry_run=self.dry_run)
        try:
            self._run_groups(list(self._groups))
            if not self._stop_reason and self.tail_timeout_s > 0 and not self.dry_run:
                self._run_tail()
            if not self._stop_reason:
                self._stop_reason = "plan exhausted"
        except KeyboardInterrupt:
            self._stop_reason = "interrupted"
        except ExecutionAbort as e:
            self._stop_reason = "abort: %s" % e
        except Exception as e:
            if _NotInLevel is not None and isinstance(e, _NotInLevel):
                self._stop_reason = "level ended"
                self._emit("error", kind="clock", error=str(e))
            else:
                self._stop_reason = "error: %s" % e
                self._emit("error", kind="run", error=str(e))
                raise
        finally:
            self._end_timer_period()
            if self._pause_start is not None:
                self.report.pauses.append(PauseRecord(self._pause_tick, self._last_wave, self._now() - self._pause_start))
                self._pause_start = None
            for s in self._steps:
                row = self.report.rows[s.row_index]
                if row.status == STATUS_PENDING:
                    row.status = STATUS_SKIPPED
                    row.note = row.note or "not reached (%s)" % self._stop_reason
            self.report.stop_reason = self._stop_reason or "unknown"
            self.report.finished_wall = time.time()
            if self.dry_run:  # nothing dispatched: label by what *would* be used
                self._mark_dry_modes()
            self.report.mode = mode_label(self.backend, self._remote_modify_used, self._input_used)
            if self.collector is not None:
                self.report.collected = self._collector_summary()
            if self.tail:
                self.report.meta["tail"] = dict(self.tail)
            self._emit("stop", reason=self.report.stop_reason)
            # never leave the game frozen: a remote-call timeout keeps
            # main_loop_block on purpose *during* the run; the run is over now
            try:
                self.release()
            except Exception as e:  # noqa: BLE001
                self._emit("error", kind="release", error=str(e))
        return self.report

    # --- tail / release ----------------------------------------------------
    def _patch_manager(self) -> Any:
        """The PatchManager behind the backend (``backend.patches`` /
        ``backend.actions.patches`` / ``...executor.patches``), or None."""
        be = self.backend
        acts = getattr(be, "actions", None)
        for obj in (be, acts, getattr(acts, "executor", None), getattr(be, "executor", None)):
            pm = getattr(obj, "patches", None)
            if pm is not None and callable(getattr(pm, "disable", None)):
                return pm
        return None

    def release(self, keep: Sequence[str] = ("background_running",)) -> List[str]:
        """Disable every patch group still enabled through the backend's
        PatchManager (``main_loop_block`` first, then fragment patches left
        on after a remote-call timeout) except ``keep``; also ends the timer
        period.  Idempotent; returns the names disabled now."""
        self._end_timer_period()
        pm = self._patch_manager()
        if pm is None:
            return []
        enabled = getattr(pm, "enabled", None)
        if enabled is None:
            names = [MAIN_LOOP_PATCH]
        else:
            names = [n for n in list(enabled) if n not in keep]
            if MAIN_LOOP_PATCH in names:
                names.remove(MAIN_LOOP_PATCH)
                names.insert(0, MAIN_LOOP_PATCH)
        done: List[str] = []
        for n in names:
            try:
                pm.disable(n)
            except Exception as e:  # noqa: BLE001
                self._emit("error", kind="release", error="%s: %s" % (n, e))
                continue
            done.append(n)
        self.released.extend(done)
        return done

    def _tail_template(self) -> List[_Group]:
        """Cob groups of the last planned wave (the "last cycle"), offsets
        clamped to >= 0 -- what ``tail_policy='repeat'`` fires on later waves."""
        if not self._groups:
            return []
        last = max(g.wave for g in self._groups)
        out: List[_Group] = []
        for g in self._groups:
            if g.wave != last:
                continue
            cobs = [s for s in g.steps if s.kind == "cob"]
            if cobs:
                out.append(_Group(last, max(0, g.operation_cs), cobs))
        return out

    def _plan_cannons(self) -> List[Tuple[int, int]]:
        """Every cannon the plan assigns, in first-use order."""
        out: List[Tuple[int, int]] = []
        for g in self._groups:
            for st in g.steps:
                if st.kind == "cob" and st.cannon is not None:
                    c = (int(st.cannon[0]), int(st.cannon[1]))
                    if c not in out:
                        out.append(c)
        return out

    def _tail_cannons(self, template: Sequence[_Group]) -> Dict[Tuple[int, int], Tuple[int, int]]:
        """Substitutions for the template cannons of one tail wave: a template
        cannon that is still on CD is replaced by *any* ready cannon of the
        plan (the template only knows the cannons of the last planned wave;
        on rhythms with more cannons the others are the ones that are
        actually ready).  Without readiness info nothing is substituted."""
        subs: Dict[Tuple[int, int], Tuple[int, int]] = {}
        if self._cobs_ready is None:
            return subs
        wanted: List[Tuple[int, int]] = []
        for tg in template:
            for st in tg.steps:
                if st.cannon is not None:
                    wanted.append((int(st.cannon[0]), int(st.cannon[1])))
        if not wanted:
            return subs
        pool = self._plan_cannons()
        not_ready = set(self._not_ready(pool))
        taken = set(c for c in wanted if c not in not_ready)
        spare = [c for c in pool if c not in not_ready and c not in taken]
        for c in wanted:
            if c in not_ready and spare:
                subs[c] = spare.pop(0)
        return subs

    def _tail_groups(self, template: Sequence[_Group], wave: int) -> List[_Group]:
        groups: List[_Group] = []
        subs = self._tail_cannons(template)
        for tg in template:
            steps: List[_Step] = []
            for t in tg.steps:
                cannon = t.cannon
                if cannon is not None:
                    cannon = subs.get((int(cannon[0]), int(cannon[1])), cannon)
                s = _Step(wave, tg.operation_cs, t.kind, t.symbol, t.plant, t.row, t.col, cannon, t.action)
                s.row_index = len(self.report.rows)
                self.report.rows.append(ActionRow(wave, s.symbol, s.kind, s.operation_cs, "tail " + self._describe(s)))
                steps.append(s)
            groups.append(_Group(wave, tg.operation_cs, steps))
        self._steps.extend(s for g in groups for s in g.steps)
        return groups

    def _tail_wave_missed(self, template: Sequence[_Group], sample: Any, wave: int, info: Dict[str, Any]) -> bool:
        """True when the wave is already past the template's earliest
        offset (beyond ``tolerance_cs``); recorded in ``info["missed"]``."""
        t = self._wave_time(sample, wave)
        if t is None:
            return False
        earliest = min(g.operation_cs for g in template)
        late_by = t + self.backend_lag_cs - earliest
        if late_by <= self.tolerance_cs:
            return False
        info.setdefault("missed", []).append({"wave": wave, "wave_cs": int(t), "late_by": int(late_by)})
        self._emit("tail_miss", wave=wave, operation_cs=earliest, late_by=late_by)
        return True

    def _run_tail(self) -> None:
        """Plan exhausted, level still running: keep polling (collector,
        pauses, optional ``tail_policy``) until the level ends or
        ``tail_timeout_s`` of wall time passed."""
        t0 = self._now()
        before = self._collector_summary() or {}
        info: Dict[str, Any] = {"policy": self.tail_policy, "timeout_s": self.tail_timeout_s, "polls": 0,
                                "waves": [], "fired_groups": 0, "start_wave": self._last_wave,
                                "collected_before": int(before.get("collected", 0) or 0)}
        self.tail = info
        self._emit("tail", wave=self._last_wave, policy=self.tail_policy, timeout_s=self.tail_timeout_s)
        template = self._tail_template() if self.tail_policy == "repeat" else []
        done_wave = max((g.wave for g in self._groups), default=0)
        reason: Optional[str] = None
        while True:
            sample = self.clock.read()
            info["polls"] += 1
            self._collect_tick(sample)
            self._note_wave(sample)
            if self._level_complete(sample):
                reason = "level complete"
                break
            if self._now() - t0 >= self.tail_timeout_s:
                reason = "tail timeout"
                break
            if self._handle_pause(sample):
                self._wait(lambda s: not getattr(s, "paused", False) and not self._clock_paused())
                continue
            sw = getattr(sample, "wave", None)
            if sw is not None and sw > done_wave:
                info["waves"].append(int(sw))
                done_wave = sw
                if template and self._tail_wave_missed(template, sample, int(sw), info):
                    # entered this wave too late for the template (the last
                    # planned group ended inside a cd-wait spanning waves,
                    # or a tail cd-wait ran long): a tail shot is
                    # opportunistic, never dispatched late / aborted
                    continue
                if template:
                    groups = self._tail_groups(template, int(sw))
                    info["fired_groups"] += len(groups)
                    self._run_groups(groups)
                    if self._stop_reason:
                        reason = self._stop_reason
                        break
                    continue
            self._wait(lambda s, sw=sw: getattr(s, "wave", None) != sw or getattr(s, "paused", False)
                       or self._level_complete(s))
        after = self._collector_summary() or {}
        info["seconds"] = round(self._now() - t0, 3)
        info["collected"] = int(after.get("collected", 0) or 0) - info["collected_before"]
        info["reason"] = reason
        self._stop_reason = reason or self._stop_reason
        self._emit("tail_end", reason=reason, seconds=info["seconds"], collected=info["collected"],
                   waves=list(info["waves"]))

    def _collector_summary(self) -> Optional[Dict[str, Any]]:
        st = getattr(self.collector, "stats", None)
        fn = getattr(st, "summary", None)
        try:
            return dict(fn()) if callable(fn) else None
        except Exception:  # noqa: BLE001
            return None

    def _mark_dry_modes(self) -> None:
        kinds = set(s.kind for s in self._steps)
        if "cob" in kinds:
            self._mark_mode("cob")
        if kinds - {"cob", KIND_SHOVEL}:
            self._mark_mode("plant")
        if KIND_SHOVEL in kinds:
            self._mark_mode("shovel")

    def _check_start(self, sample: Any) -> None:
        """Warn when the current wave's refresh point is only an estimate."""
        w = getattr(sample, "wave", None)
        fn = getattr(self.clock, "refresh_point", None)
        if not w or not callable(fn):
            return
        rp = fn(w)
        src = getattr(rp, "source", None)
        if rp is None or src not in ("first", "countdown"):
            return
        if src == "countdown":
            msg = ("started inside wave %d: its refresh point (clock %s) is estimated from countdown_start - countdown "
                   "(exact for a normal wave, too early after an HP-threshold clamp or inside the huge-wave banner)"
                   % (w, getattr(rp, "main_counter", "?")))
        else:
            msg = ("started inside wave %d at clock %s with no way to recover its refresh point: wave %d steps are "
                   "timed from the first poll (up to a whole wave late)" % (w, getattr(rp, "main_counter", "?"), w))
        self.report.warnings.append(msg)
        self._emit("warning", wave=w, source=src, message=msg)

    def _run_groups(self, pending: List[_Group]) -> None:
        """Dispatch ``pending`` (sorted by ``(wave, operation_cs)``) in live
        chronological order; see the module doc (*Ordering*)."""
        first = True
        while pending and not self._stop_reason:
            sample = self.clock.read()
            self._collect_tick(sample)
            self._note_wave(sample)
            if first:
                self._check_start(sample)
                first = False
            if self._level_complete(sample):
                self._stop_reason = "level complete"
                return
            if self._handle_pause(sample):
                self._wait(lambda s: not getattr(s, "paused", False) and not self._clock_paused())
                continue
            sw = getattr(sample, "wave", None)
            if sw is None:
                self._wait(lambda s: getattr(s, "wave", None) is not None)
                continue
            # candidates: groups whose wave time is resolvable now (<= sw + 1)
            cands: List[_Group] = []
            due: Optional[_Group] = None
            due_t = 0
            replan = False
            for g in pending:
                if g.wave > sw + 1:
                    break
                t = self._wave_time(sample, g.wave)
                if t is None:
                    if g.wave < sw:
                        self._skip_group(g, "wave %d already passed (now w%d), refresh point unknown" % (g.wave, sw))
                        pending.remove(g)
                        replan = True  # list changed: re-read and re-plan
                        break
                    continue
                if t >= self._target_cs(g):
                    due, due_t = g, t
                    break
                cands.append(g)
            if replan:
                continue
            if due is not None:
                pending.remove(due)
                self._dispatch_group(due, sample, due_t)
                continue
            if not cands:
                # nothing resolvable yet (e.g. waiting for wave sw+1 to refresh)
                self._emit_wait(pending[0])
                self._wait(lambda s, sw=sw: getattr(s, "wave", None) != sw or getattr(s, "paused", False)
                           or self._level_complete(s))
                continue
            self._emit_wait(cands[0])
            self._wait(self._any_due_pred(cands, sw), self._min_eta(cands))

    def _emit_wait(self, group: _Group) -> None:
        """One ``wait`` event per group (the loop re-plans every slice)."""
        if self._last_wait_key != group.key:
            self._last_wait_key = group.key
            self._emit("wait", wave=group.wave, operation_cs=group.operation_cs, symbols=[s.symbol for s in group.steps])

    def _target_cs(self, group: _Group) -> int:
        return group.operation_cs - self.lead_cs - self.backend_lag_cs

    def _eta(self, wave: int, target_cs: int) -> Callable[[Any], Optional[float]]:
        """Seconds to sleep before the target tick (hint for the clock's
        hybrid sleep): one tick short, so the last frame is busy-polled."""

        def eta(s: Any) -> Optional[float]:
            t = self._wave_time(s, wave)
            if t is None:
                return None
            return max(0.0, (target_cs - t - 1) * self._frame_s)

        return eta

    def _min_eta(self, cands: Sequence[_Group]) -> Callable[[Any], Optional[float]]:
        etas = [self._eta(g.wave, self._target_cs(g)) for g in cands]

        def eta(s: Any) -> Optional[float]:
            vals = [v for v in (e(s) for e in etas) if v is not None]
            return min(vals) if vals else None

        return eta

    def _due_pred(self, wave: int, target_cs: int) -> Callable[[Any], bool]:
        def pred(s: Any) -> bool:
            self._note_wave(s)  # samples seen only inside the clock's wait still count
            if getattr(s, "paused", False) or self._level_complete(s):
                return True
            sw = getattr(s, "wave", None)
            if sw is None:
                return False
            if sw > wave:
                return True
            t = self._wave_time(s, wave)
            return t is not None and t >= target_cs

        return pred

    def _any_due_pred(self, cands: Sequence[_Group], sw: int) -> Callable[[Any], bool]:
        preds = [self._due_pred(g.wave, self._target_cs(g)) for g in cands]

        def pred(s: Any) -> bool:
            if getattr(s, "wave", None) != sw:  # wave moved: re-plan the candidates
                self._note_wave(s)
                return True
            return any(p(s) for p in preds)

        return pred

    def _skip_group(self, group: _Group, reason: str, observed: Optional[int] = None) -> None:
        for s in group.steps:
            row = self.report.rows[s.row_index]
            row.status = STATUS_SKIPPED
            row.observed_cs = observed
            row.note = reason
        self.report.skipped += len(group.steps)
        self._emit("skip", wave=group.wave, operation_cs=group.operation_cs, reason=reason, symbols=[s.symbol for s in group.steps])

    def _cannons_of(self, group: _Group) -> List[Tuple[int, int]]:
        return [(int(s.cannon[0]), int(s.cannon[1])) for s in group.steps if s.kind == "cob" and s.cannon is not None]

    def _not_ready(self, cannons: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
        if self._cobs_ready is None or not cannons:
            return []
        try:
            flags = list(self._cobs_ready(cannons))
        except Exception as e:  # readiness unknown: behave as before
            self._emit("error", kind="cobs_ready", error=str(e))
            return []
        return [c for c, ok in zip(cannons, flags) if not ok]

    def _wait_cobs_ready(self, group: _Group, sample: Any, t_before: int) -> Tuple[Any, int, Optional[int]]:
        """*Fire when ready* fallback (reactive, DESIGN §6.2b): when a cannon
        of a cob group is not READY at its planned tick, wait (bounded by
        ``cd_wait_max_cs`` ticks on the wave axis, the level ending or a
        pause) until every cannon of the group is ready.  Returns the new
        ``(sample, t_before, cd_wait_cs)``; ``cd_wait_cs`` is ``None`` when
        no wait was needed."""
        cannons = self._cannons_of(group)
        missing = self._not_ready(cannons)
        if not missing:
            return sample, t_before, None
        self._emit("cd_wait", wave=group.wave, operation_cs=group.operation_cs, cannons=list(missing),
                   symbols=[s.symbol for s in group.steps])
        t_start = t_before
        limit = t_start + self.cd_wait_max_cs
        t_now = t_before
        while True:
            # readiness is re-checked on every clock sample (one plant-array
            # read per tick, like the reactive runner) so the shot goes out
            # on the tick the cannon turns READY
            self._wait(lambda s, w=group.wave, lim=limit, c=cannons: self._level_complete(s)
                       or getattr(s, "paused", False) or (self._wave_time(s, w) or 0) >= lim
                       or not self._not_ready(c))
            sample = self.clock.read()
            self._collect_tick(sample)
            self._note_wave(sample)
            t = self._wave_time(sample, group.wave)
            t_now = t if t is not None else t_now
            missing = self._not_ready(cannons)
            if not missing or self._level_complete(sample) or t_now >= limit:
                break
        return sample, t_now, max(0, t_now - t_start)

    def _dispatch_group(self, group: _Group, sample: Any, t_before: int) -> None:
        cd_wait: Optional[int] = None
        if not self.dry_run and any(s.kind == "cob" for s in group.steps):
            sample, t_before, cd_wait = self._wait_cobs_ready(group, sample, t_before)
        late_by = t_before + self.backend_lag_cs - group.operation_cs
        is_late = late_by > self.tolerance_cs and cd_wait is None
        if is_late:
            self._emit("late", wave=group.wave, operation_cs=group.operation_cs, late_by=late_by, policy=self.late_policy)
            if self.late_policy == "skip":
                self._skip_group(group, "late by %dcs (policy skip)" % late_by, observed=t_before)
                self.report.late += len(group.steps)
                return
            if self.late_policy == "abort":
                for s in group.steps:
                    row = self.report.rows[s.row_index]
                    row.status = STATUS_ABORTED
                    row.observed_cs = t_before
                    row.note = "late by %dcs (policy abort)" % late_by
                self.report.late += len(group.steps)
                raise ExecutionAbort("w%d %+dcs late by %dcs" % (group.wave, group.operation_cs, late_by))
        errors: Dict[int, str] = {}
        # absolute main_counter of the wave's refresh point (sample tick -
        # wave time); lets a remote cob group be frame-locked to
        # refresh + operation_cs and its fired_tick be mapped back to wave time
        counter = self._counter(sample)
        refresh_abs = None if counter is None else counter - t_before
        fired: Dict[int, Any] = {}
        call_ms: Optional[float] = None
        if not self.dry_run:
            # a cd-waited group is fired at once (no lock target in the past)
            t0 = self._now()
            errors = self._do_dispatch(group, refresh_abs, fired, lock=cd_wait is None)
            call_ms = (self._now() - t0) * 1000.0
        after = self.clock.read()
        self._note_wave(after)
        done = self._wave_time(after, group.wave)
        if self.backend_lag_cs or self.dry_run:
            # multi-frame click protocol: the game acts ``lag`` ticks after
            # the first message; the call's return time measures the protocol
            observed = t_before + self.backend_lag_cs
        else:
            observed = done if done is not None else t_before
        for s in group.steps:
            row = self.report.rows[s.row_index]
            row.observed_cs = observed
            res = fired.get(s.row_index)
            if res is not None and refresh_abs is not None:
                ft = _attr_int(res, "fired_tick")
                fz = _attr_int(res, "frozen_tick")
                if ft is not None:
                    # read back from the game (mShootingCounter arithmetic)
                    row.observed_cs = ft - refresh_abs
                    row.fired_cs = ft - refresh_abs
                elif fz is not None:
                    # no read-back (profile lacks shooting_counter / the
                    # cannon did not fire): the frozen tick is the best
                    # estimate of the action tick, but it is NOT a fire tick
                    row.observed_cs = fz - refresh_abs
                if fz is not None:
                    row.frozen_cs = fz - refresh_abs
                row.attempts = _attr_int(res, "attempts")
            row.done_cs = done
            row.call_ms = call_ms
            row.delta = row.observed_cs - s.operation_cs
            if s.row_index in errors:
                row.status = STATUS_FAILED
                row.note = errors[s.row_index]
            elif self.dry_run:
                row.status = STATUS_DRY
            elif cd_wait is not None and s.kind == "cob":
                row.status = STATUS_CD_WAIT
                row.cd_wait_cs = cd_wait
                row.note = "cannon not ready at planned tick: fired when ready (+%dcs)" % row.delta
            elif is_late or row.delta > self.tolerance_cs:
                row.status = STATUS_LATE
                row.note = "late by %dcs" % row.delta
            else:
                row.status = STATUS_OK
        if any(self.report.rows[s.row_index].status == STATUS_LATE for s in group.steps):
            self.report.late += sum(1 for s in group.steps if self.report.rows[s.row_index].status == STATUS_LATE)
        self._emit(
            "dispatch",
            wave=group.wave,
            operation_cs=group.operation_cs,
            observed_cs=observed,
            delta=observed - group.operation_cs,
            symbols=[s.symbol for s in group.steps],
            dry_run=self.dry_run,
            cd_wait_cs=cd_wait,
            fired_cs=[self.report.rows[s.row_index].fired_cs for s in group.steps],
        )

    def _do_dispatch(self, group: _Group, refresh_abs: Optional[int] = None,
                     fired: Optional[Dict[int, Any]] = None, lock: bool = True) -> Dict[int, str]:
        """Call the backend for every step of the group; cobs in ONE call.

        ``refresh_abs``: absolute ``main_counter`` of the group's wave refresh
        point; with a remote cob backend (and ``lock``) the fires are
        frame-locked to ``refresh_abs + operation_cs`` and ``fired``
        (row_index -> the returned ``FireResult``) is filled."""
        errors: Dict[int, str] = {}
        cobs = [s for s in group.steps if s.kind == "cob"]
        others = [s for s in group.steps if s.kind != "cob"]
        if cobs:
            fires = []
            fire_steps = []
            for s in cobs:
                if s.cannon is None or s.row is None or s.col is None:
                    errors[s.row_index] = "no cannon / target assigned"
                    continue
                fires.append((s.cannon, (s.row, s.col)))
                fire_steps.append(s)
            if fires:
                kw: Dict[str, Any] = {}
                if lock and self._cob_at_tick and refresh_abs is not None:
                    kw["at_tick"] = refresh_abs + group.operation_cs
                try:
                    if len(fires) == 1:
                        res = self.backend.fire_cob(fires[0][0], fires[0][1][0], fires[0][1][1], **kw)
                        results = [res]
                    else:
                        res = self.backend.fire_cobs(fires, **kw)
                        results = list(res) if isinstance(res, (list, tuple)) else []
                    self._mark_mode("cob")
                    if fired is not None:
                        for s, r in zip(fire_steps, results):
                            if r is not None:
                                fired[s.row_index] = r
                except Exception as e:  # backend failure must not kill the run
                    for s in cobs:
                        errors.setdefault(s.row_index, "fire failed: %s" % e)
                    self._emit("error", kind="cob", error=str(e), wave=group.wave, operation_cs=group.operation_cs)
        for s in others:
            try:
                self._dispatch_one(s)
            except Exception as e:
                errors[s.row_index] = "%s failed: %s" % (s.kind, e)
                self._emit("error", kind=s.kind, error=str(e), wave=group.wave, operation_cs=group.operation_cs)
        return errors

    def _dispatch_one(self, s: _Step) -> None:
        if s.row is None or s.col is None:
            raise ValueError("no target position")
        col = int(round(s.col))
        if s.kind == KIND_SHOVEL:
            self.backend.shovel(s.row, col)
            self._mark_mode("shovel")
            return
        plant = s.plant
        imitater = False
        if s.kind == KIND_COFFEE:
            plant = int(PlantType.COFFEE_BEAN)
        elif plant is not None and plant >= IMITATER_OFFSET:
            plant, imitater = plant - IMITATER_OFFSET, True
        # pass a PlantType member, never a bare int: backends read small ints
        # (0..9) as seed-bank *slots* (backends.is_slot). ``None`` (fodder
        # "auto") is passed through for the backend to resolve.
        if plant is not None:
            try:
                plant = PlantType(int(plant))
            except ValueError:
                pass
        self.backend.plant_card(plant, s.row, col, imitater=imitater)
        self._mark_mode("plant")

    def _mark_mode(self, what: str) -> None:
        mode = getattr(self.backend, "mode", "input")
        if what == "cob":
            mode = getattr(self.backend, "cob_mode", mode)
        elif what in ("plant", "shovel"):
            mode = getattr(self.backend, "plant_mode", mode)
        if mode == "remote" and what in ("plant", "shovel"):
            self._remote_modify_used = True  # PutPlant / delete_plant: 修改器
        elif mode != "remote":
            self._input_used = True

    # --- timer resolution (winmm timeBeginPeriod(1) for the run) -----------
    def _begin_timer_period(self) -> None:
        if _high_resolution is None:
            return
        try:
            self._hires = _high_resolution(1)
            self._hires.__enter__()
        except Exception:
            self._hires = None

    def _end_timer_period(self) -> None:
        if self._hires is not None:
            try:
                self._hires.__exit__(None, None, None)
            except Exception:
                pass
            self._hires = None
