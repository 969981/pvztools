"""External executor (DESIGN §6, "external" backend).

* :mod:`.clock` -- :class:`GameClock`: poll the observer, track refresh
  points per wave, estimate time to the next refresh, detect pauses, and
  block until a ``(wave, cs)`` point.
* :mod:`.timing_win` -- :func:`high_resolution` (winmm timeBeginPeriod) and
  :func:`precise_sleep` (sleep + busy-wait).
* :mod:`.scheduler` / :mod:`.report` -- :class:`PlannedExecutor` runs an
  :class:`~pvztools.schema.track.expand.Plan` on the game clock and returns
  an :class:`ExecutionReport` (planned / observed / delta).
* :mod:`.policies` / :mod:`.reactive` -- :class:`ReactiveRunner` polls the
  board and executes :class:`Decision` s from a policy (:class:`PSeriesPolicy`,
  :class:`IceRefillPolicy`, :class:`ComboPolicy`).
* :mod:`.bench` -- :func:`bench_clock`: poll latency + target-tick jitter of
  the hybrid waiter (``pvztools bench-clock``).

:class:`PolicyBackend` adapts an :class:`~pvztools.control.backends.ActionBackend`
(``plant_card`` / ``shovel`` / ``fire_cobs`` keyed by cannon *cell*) to the
``put_plant`` / ``remove_plant`` / ``fire_cobs`` (keyed by plant *index*)
surface the reactive runner expects.
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from .clock import (  # noqa: F401
    GameClock, ClockSample, RefreshPoint, Refresh, ClockError, ClockTimeout,
    DEFAULT_FRAME_MS, HUGE_WAVE_BANNER_AT, PAUSE_MIN_POLLS, PAUSE_MIN_SPAN,
)
from .timing_win import high_resolution, precise_sleep, SPIN_SECONDS  # noqa: F401
from .report import ActionRow, ExecutionReport, JitterStats, PauseRecord, jitter_stats, mode_label  # noqa: F401
from .scheduler import (  # noqa: F401
    PlannedExecutor, ExecutionAbort, LATE_POLICIES, DEFAULT_LEAD_CS,
    STATUS_OK, STATUS_LATE, STATUS_SKIPPED, STATUS_ABORTED, STATUS_PENDING, STATUS_DRY, STATUS_FAILED,
)
from .policies import (  # noqa: F401
    Decision, WorldView, CobView, SeedView, PlantView, ZombieView,
    PSeriesPolicy, IceRefillPolicy, ComboPolicy,
)
from .reactive import ReactiveRunner, ReactiveLog, LogEntry, ReactiveError, build_view  # noqa: F401
from .bench import BenchResult, bench_clock, format_bench  # noqa: F401

__all__ = [
    "GameClock", "ClockSample", "RefreshPoint", "Refresh", "ClockError", "ClockTimeout",
    "DEFAULT_FRAME_MS", "HUGE_WAVE_BANNER_AT", "PAUSE_MIN_POLLS", "PAUSE_MIN_SPAN",
    "high_resolution", "precise_sleep", "SPIN_SECONDS",
    "ActionRow", "ExecutionReport", "JitterStats", "PauseRecord", "jitter_stats", "mode_label",
    "PlannedExecutor", "ExecutionAbort", "LATE_POLICIES", "DEFAULT_LEAD_CS",
    "STATUS_OK", "STATUS_LATE", "STATUS_SKIPPED", "STATUS_ABORTED", "STATUS_PENDING", "STATUS_DRY", "STATUS_FAILED",
    "Decision", "WorldView", "CobView", "SeedView", "PlantView", "ZombieView",
    "PSeriesPolicy", "IceRefillPolicy", "ComboPolicy",
    "ReactiveRunner", "ReactiveLog", "LogEntry", "ReactiveError", "build_view",
    "BenchResult", "bench_clock", "format_bench",
    "PolicyBackend", "POLICIES", "make_policy",
]

#: Named policies for ``Game.run_reactive(policy=...)`` / ``pvztools reactive --policy``.
#: Every P-series name maps to :class:`PSeriesPolicy`; the number only sets
#: the nominal wave length (``min_wave_len``) recorded for reporting -- the
#: decision itself is driven by the activation check (§6.2b).
POLICIES = {
    "p4": 869, "p5": 695, "p6": 601, "p7": 601, "p8": 601,
}

COB_CANNON = 47


def make_policy(name: str, cob_targets: Sequence[Tuple[int, float]], **kw: Any) -> Any:
    """Build a policy object from a name (``'p6'``, ``'P4'``, ...)."""
    key = str(name).strip().lower()
    if key not in POLICIES:
        raise ValueError("unknown policy %r (known: %s)" % (name, ", ".join(sorted(POLICIES))))
    kw.setdefault("min_wave_len", POLICIES[key])
    return PSeriesPolicy(cob_targets, **kw)


class PolicyBackend:
    """Adapter: reactive :class:`Decision` calls -> :class:`ActionBackend`.

    ``fire_cobs`` receives ``[(cob_index, (row, col)), ...]`` (plant array
    index, as :class:`CobView.index`) and translates each index to the
    cannon's 1-based left cell through ``observer.plants()``.
    """

    def __init__(self, backend: Any, observer: Any) -> None:
        self.backend = backend
        self.observer = observer

    @property
    def mode(self) -> str:
        return str(getattr(self.backend, "mode", "unknown"))

    def _cob_cell(self, index: int) -> Tuple[int, int]:
        for p in self.observer.plants(include_dead=False):
            if int(p.index) == int(index):
                if int(p.type) != COB_CANNON:
                    raise ValueError("plant #%d is not a cob cannon (type %d)" % (index, int(p.type)))
                return int(p.row) + 1, int(p.col) + 1
        raise ValueError("no live plant #%d" % index)

    def fire_cobs(self, fires: Sequence[Tuple[Any, Tuple[int, float]]]) -> Any:
        out: List[Tuple[Tuple[int, int], Tuple[int, float]]] = []
        for cob, (r, c) in fires:
            cell = tuple(cob) if isinstance(cob, (tuple, list)) else self._cob_cell(int(cob))
            out.append(((int(cell[0]), int(cell[1])), (int(r), float(c))))
        if len(out) == 1:
            return self.backend.fire_cob(out[0][0], out[0][1][0], out[0][1][1])
        return self.backend.fire_cobs(out)

    def put_plant(self, row: int, col: int, plant: Any, imitater: bool = False) -> Any:
        from ...schema.enums import PlantType

        try:
            plant = PlantType(int(plant))  # never a bare int: that would mean a seed slot
        except (TypeError, ValueError):
            pass
        return self.backend.plant_card(plant, int(row), int(col), imitater=imitater)

    def remove_plant(self, row: int, col: int) -> Any:
        return self.backend.shovel(int(row), int(col))

    def __repr__(self) -> str:
        return "PolicyBackend(%r)" % (self.backend,)
