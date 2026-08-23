"""Wall-clock helpers for the external executor (DESIGN §6, external backend).

* :func:`high_resolution` -- context manager that raises the Windows timer
  resolution to 1 ms (``winmm.timeBeginPeriod(1)`` / ``timeEndPeriod(1)``)
  for the duration of a run.  No-op off Windows or when winmm is missing.
* :func:`precise_sleep` -- hybrid sleep: coarse ``sleep`` until ~``spin``
  seconds before the deadline, then busy-wait on ``perf_counter``.

Both accept injectable ``now`` / ``sleep`` callables so tests can run with a
fake clock.
"""
from __future__ import annotations

import contextlib
import sys
import time
from typing import Callable, Iterator, Optional

__all__ = ["high_resolution", "precise_sleep", "SPIN_SECONDS", "timer_period_supported"]

#: default busy-wait window before a deadline (seconds)
SPIN_SECONDS = 0.002


def _winmm():  # pragma: no cover - Windows only
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        return ctypes.windll.winmm
    except (AttributeError, OSError):
        return None


def timer_period_supported() -> bool:
    """True when ``winmm.timeBeginPeriod`` is callable on this host."""
    return _winmm() is not None


@contextlib.contextmanager
def high_resolution(period_ms: int = 1) -> Iterator[bool]:
    """Raise the system timer resolution to ``period_ms`` while the block
    runs, restoring it afterwards.  Yields ``True`` when the period was
    actually applied (Windows with winmm), ``False`` otherwise (no-op)."""
    mm = _winmm()
    applied = False
    if mm is not None:
        try:
            applied = mm.timeBeginPeriod(int(period_ms)) == 0  # TIMERR_NOERROR
        except Exception:  # pragma: no cover - defensive
            applied = False
    try:
        yield applied
    finally:
        if applied:
            try:
                mm.timeEndPeriod(int(period_ms))
            except Exception:  # pragma: no cover - defensive
                pass


def precise_sleep(seconds: float,
                  *,
                  now: Callable[[], float] = time.perf_counter,
                  sleep: Callable[[float], None] = time.sleep,
                  spin: float = SPIN_SECONDS,
                  deadline: Optional[float] = None) -> float:
    """Block for ``seconds`` (or until absolute ``deadline`` on the ``now``
    clock).  Sleeps in chunks until ``spin`` seconds remain, then busy-waits.
    Returns the overshoot in seconds (``now() - deadline`` at return)."""
    if deadline is None:
        deadline = now() + max(0.0, seconds)
    while True:
        remaining = deadline - now()
        if remaining <= 0:
            break
        if remaining > spin:
            # leave the spin window untouched; cap chunks so a fake clock or
            # an interrupted sleep still converges
            sleep(min(remaining - spin, 0.05))
        else:
            # busy-wait: yield the GIL briefly without giving up the slice
            sleep(0)
    return now() - deadline
