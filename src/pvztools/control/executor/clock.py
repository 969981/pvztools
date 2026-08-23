"""Game-clock tracking for the external executor (DESIGN §6, §6.2b).

The only time source for scheduling is the game's own battle clock
(``Board.main_counter``, 1 tick = 1 cs = ``frame_duration`` ms, default
10 ms) plus the wave bookkeeping (``wave`` / ``countdown`` /
``huge_wave_countdown``).  Wall-clock time (``perf_counter``) is used only to
decide how long to sleep between polls.

Time model (docs/track-format.md §1):

* The *refresh point* of wave ``w`` is the tick at which ``Board.wave``
  becomes ``w`` (its zombies spawn).  ``wave_time(sample, w)`` is
  ``main_counter - refresh_point[w]``; negative values lie before the refresh
  and are only available as an estimate through :meth:`time_to_next_refresh`.

* Time-to-refresh approximation:

  - normal wave next: ``countdown`` (cs) -- kind ``'countdown'``.  This is
    the *forced* refresh; an HP-triggered early refresh shortens it, so the
    value is an upper bound.
  - flag wave next (w10 / w20, ``Board.is_flag_wave_next``): the game counts
    ``countdown`` down to 5, then runs the 750 cs "A huge wave approaches"
    banner (``huge_wave_countdown``) and refreshes when it reaches 0.  So
    ``max(countdown - 5, 0) + 750`` while the banner has not started and
    ``huge_wave_countdown`` once it has -- kind ``'huge_wave'``.
  - no next wave (``wave >= total_waves``) or nothing counting -> kind
    ``'unknown'``, ``cs=None``.

* Refresh-point inference: the first sample seen for a wave (attaching
  mid-wave) records ``main_counter - (countdown_start - countdown)`` with
  ``source='countdown'`` when that is consistent (wave > 0, both counters
  positive, the result non-negative): the game resets ``countdown`` to
  ``countdown_start`` at the refresh, so the difference is the time elapsed
  since -- exact for a normal wave, too large after an HP-threshold clamp
  (``countdown`` jumped to 200) or inside the huge-wave banner.  Otherwise
  the first sample's ``main_counter`` is used with ``source='first'``.  Both
  carry ``late_by=None`` (the estimate is unbounded).  When
  two consecutive polls straddle an increment, the refresh happened inside
  ``(prev.main_counter, cur.main_counter]``: if the previous sample's
  estimate (``prev.main_counter + time_to_next_refresh(prev)``) falls in that
  window it is used (``late_by`` = window size, ``source='estimate'``);
  otherwise the new counter is used with ``late_by`` = window size
  (``source='poll'``).  Callers that need tighter bounds poll faster.

* Pause: ``main_counter`` stops advancing (``Board.paused``, a top-level
  dialog, or focus loss without the background patch).  :meth:`paused`
  detects it from the trailing run of equal counters (tracked across reads,
  so a busy-poll phase with thousands of samples per second still notices a
  stall after ``PAUSE_MIN_SPAN``); :meth:`wait_until` excludes paused time
  from its timeout instead of failing and drops to a slow poll while paused.

* New level / new Survival round: the clock resets when ``main_counter``
  goes backwards or ``wave`` goes backwards.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from ...schema.timing import HUGE_WAVE_RED_TEXT
from .timing_win import SPIN_SECONDS

__all__ = [
    "ClockSample", "RefreshPoint", "Refresh", "GameClock",
    "ClockTimeout", "ClockError",
    "DEFAULT_FRAME_MS", "HUGE_WAVE_BANNER_AT", "PAUSE_MIN_POLLS", "PAUSE_MIN_SPAN",
]

DEFAULT_FRAME_MS = 10
#: countdown value at which the flag-wave banner (huge_wave_countdown=750) starts
HUGE_WAVE_BANNER_AT = 5
#: pause detection thresholds
PAUSE_MIN_POLLS = 3
PAUSE_MIN_SPAN = 0.060  # seconds
GAME_UI_IN_LEVEL = 3


class ClockError(RuntimeError):
    """Base class for clock failures."""


class ClockTimeout(ClockError):
    """``wait_until`` ran out of (un-paused) wall time."""


@dataclass(frozen=True)
class ClockSample:
    """One poll of the game clock.  ``t`` is the ``now()`` reading taken
    right before the board was read."""

    t: float
    main_counter: int
    wave: int
    countdown: int
    countdown_start: int
    huge_wave_countdown: int
    paused: bool
    total_waves: int
    game_ui: int = GAME_UI_IN_LEVEL

    @property
    def in_level(self) -> bool:
        return self.game_ui == GAME_UI_IN_LEVEL

    @property
    def is_flag_wave_next(self) -> bool:
        nxt = self.wave + 1
        return nxt > 0 and nxt % 10 == 0

    @property
    def has_next_wave(self) -> bool:
        return self.total_waves <= 0 or self.wave < self.total_waves


@dataclass(frozen=True)
class RefreshPoint:
    """``main_counter`` at which ``wave`` (is believed to have) started.

    ``late_by`` bounds how many ticks *after* the true refresh the recorded
    counter may be (``None`` = unbounded: the wave was already running when
    first observed).  ``source`` is ``'first'`` / ``'poll'`` / ``'estimate'``.
    """

    wave: int
    main_counter: int
    late_by: Optional[int]
    source: str


@dataclass(frozen=True)
class Refresh:
    """Estimated time to the next refresh point (see module doc)."""

    cs: Optional[int]
    kind: str  # 'countdown' | 'huge_wave' | 'unknown'
    next_wave: Optional[int] = None

    @property
    def known(self) -> bool:
        return self.cs is not None


class GameClock:
    """Poll a :class:`pvztools.observe.snapshot.Observer` (anything with
    ``board()`` and ``game()``) and keep wave / refresh bookkeeping.

    ``now`` / ``sleep`` are injectable for tests; ``frame_ms`` overrides the
    game's ``frame_duration`` (read from ``observer.game()`` when ``None``).
    """

    def __init__(self,
                 observer: Any,
                 *,
                 now: Callable[[], float] = time.perf_counter,
                 sleep: Callable[[float], None] = time.sleep,
                 frame_ms: Optional[int] = None,
                 spin: float = SPIN_SECONDS,
                 poll_interval: float = 0.004,
                 history: int = 64) -> None:
        self.observer = observer
        self.now = now
        self.sleep = sleep
        self._frame_ms = frame_ms
        self.spin = spin
        self.poll_interval = poll_interval
        self.refresh_points: Dict[int, RefreshPoint] = {}
        self.samples: List[ClockSample] = []
        self._history = max(PAUSE_MIN_POLLS, history)
        self.last: Optional[ClockSample] = None
        # trailing run of samples sharing one main_counter (stall detection)
        self._stall_n = 0
        self._stall_t0 = 0.0

    # -- sampling -------------------------------------------------------------
    @property
    def frame_ms(self) -> int:
        if self._frame_ms is None:
            fd = 0
            game = getattr(self.observer, "game", None)
            if callable(game):
                try:
                    fd = int(getattr(game(), "frame_duration", 0) or 0)
                except Exception:
                    fd = 0
            self._frame_ms = fd if fd > 0 else DEFAULT_FRAME_MS
        return self._frame_ms

    def read(self) -> ClockSample:
        """Poll the game once, update refresh points / history, return the
        sample.  Raises whatever ``observer.board()`` raises (NotInLevel)."""
        t = self.now()
        ui = GAME_UI_IN_LEVEL
        game = getattr(self.observer, "game", None)
        if callable(game):
            try:
                ui = int(game().ui)
            except Exception:
                ui = GAME_UI_IN_LEVEL
        b = self.observer.board()
        s = ClockSample(
            t=t,
            main_counter=int(b.clock),
            wave=int(b.wave),
            countdown=int(b.countdown),
            countdown_start=int(getattr(b, "countdown_init", 0)),
            huge_wave_countdown=int(b.huge_wave_countdown),
            paused=bool(b.paused),
            total_waves=int(b.total_waves),
            game_ui=ui,
        )
        self._track(s)
        return s

    def reset(self) -> None:
        """Forget refresh points and history (new level / new selection)."""
        self.refresh_points.clear()
        self.samples.clear()
        self.last = None
        self._stall_n = 0

    @staticmethod
    def _first_refresh_point(s: ClockSample) -> RefreshPoint:
        """Refresh point for a wave already running when first observed."""
        if s.wave > 0 and s.countdown_start > 0 and 0 < s.countdown <= s.countdown_start                 and s.huge_wave_countdown <= 0:
            elapsed = s.countdown_start - s.countdown
            if 0 <= elapsed <= s.main_counter:
                return RefreshPoint(s.wave, s.main_counter - elapsed, None, "countdown")
        return RefreshPoint(s.wave, s.main_counter, None, "first")

    def _track(self, s: ClockSample) -> None:
        prev = self.last
        if prev is not None and (s.main_counter < prev.main_counter or s.wave < prev.wave):
            # clock / wave went backwards: new level or Survival round started
            self.reset()
            prev = None
        if prev is not None and s.main_counter == prev.main_counter and s.in_level:
            self._stall_n += 1
        else:
            self._stall_n, self._stall_t0 = 1, s.t
        if prev is None:
            if s.wave not in self.refresh_points:
                self.refresh_points[s.wave] = self._first_refresh_point(s)
        elif s.wave != prev.wave:
            window = s.main_counter - prev.main_counter
            est = self.time_to_next_refresh(prev)
            for w in range(prev.wave + 1, s.wave + 1):
                if w in self.refresh_points:
                    continue
                if w == prev.wave + 1 and est.cs is not None \
                        and prev.main_counter < prev.main_counter + est.cs <= s.main_counter:
                    self.refresh_points[w] = RefreshPoint(
                        w, prev.main_counter + est.cs, window, "estimate")
                else:
                    self.refresh_points[w] = RefreshPoint(w, s.main_counter, window, "poll")
        self.last = s
        self.samples.append(s)
        if len(self.samples) > self._history:
            del self.samples[: len(self.samples) - self._history]

    # -- derived times --------------------------------------------------------
    def refresh_point(self, wave: int) -> Optional[RefreshPoint]:
        return self.refresh_points.get(wave)

    def wave_time(self, sample: ClockSample, wave: int) -> Optional[int]:
        """cs since the refresh point of ``wave`` (negative = before it, only
        for ``wave == sample.wave + 1`` via the refresh estimate); ``None``
        when unknown."""
        rp = self.refresh_points.get(wave)
        if rp is not None:
            return sample.main_counter - rp.main_counter
        if wave == sample.wave + 1:
            r = self.time_to_next_refresh(sample)
            return None if r.cs is None else -r.cs
        return None

    @staticmethod
    def time_to_next_refresh(sample: ClockSample) -> Refresh:
        """Estimated cs until the next refresh point (see module doc)."""
        if not sample.has_next_wave:
            return Refresh(None, "unknown", None)
        nxt = sample.wave + 1
        if sample.is_flag_wave_next:
            if sample.huge_wave_countdown > 0:
                return Refresh(sample.huge_wave_countdown, "huge_wave", nxt)
            if sample.countdown > 0:
                cs = max(sample.countdown - HUGE_WAVE_BANNER_AT, 0) + HUGE_WAVE_RED_TEXT
                return Refresh(cs, "huge_wave", nxt)
            return Refresh(None, "unknown", nxt)
        if sample.countdown > 0:
            return Refresh(sample.countdown, "countdown", nxt)
        return Refresh(None, "unknown", nxt)

    # -- pause ----------------------------------------------------------------
    @staticmethod
    def is_paused(samples: Sequence[ClockSample],
                  min_polls: int = PAUSE_MIN_POLLS,
                  min_span: float = PAUSE_MIN_SPAN) -> bool:
        """True when the game clock is not advancing: the last sample has the
        ``paused`` flag, or the last ``min_polls`` samples (spanning at least
        ``min_span`` seconds) share one ``main_counter`` while in a level
        (``game_ui == 3``).  The run may be longer than ``min_polls``: any
        trailing stretch of unchanged counters counts."""
        if not samples:
            return False
        last = samples[-1]
        if last.paused:
            return True
        # longest trailing run with an unchanged counter, all in-level
        n = 0
        for s in reversed(samples):
            if s.main_counter != last.main_counter or not s.in_level:
                break
            n += 1
        if n < min_polls:
            return False
        return last.t - samples[-n].t >= min_span

    def paused(self) -> bool:
        """:meth:`is_paused` over the tracked trailing run (not limited by
        the sample history, so busy-polling cannot hide a stall)."""
        last = self.last
        if last is None:
            return False
        if last.paused:
            return True
        return self._stall_n >= PAUSE_MIN_POLLS and last.t - self._stall_t0 >= PAUSE_MIN_SPAN

    # -- waiting --------------------------------------------------------------
    def wait_until(self,
                   predicate: Callable[[ClockSample], bool],
                   timeout: Optional[float] = None,
                   *,
                   eta: Optional[Callable[[ClockSample], Optional[float]]] = None,
                   pause_timeout: Optional[float] = None) -> ClockSample:
        """Poll until ``predicate(sample)`` is true and return that sample.

        ``eta(sample)`` may return the estimated seconds remaining; when it is
        larger than the spin window the clock sleeps (hybrid: coarse sleep,
        then busy-poll re-reading ``main_counter``).  Without ``eta`` it polls
        every ``poll_interval``.  ``timeout`` counts un-paused wall seconds;
        ``pause_timeout`` caps one continuous pause (``None`` = wait
        forever).  Expiring either raises :class:`ClockTimeout`.
        """
        start = self.now()
        paused_accum = 0.0
        pause_since: Optional[float] = None
        while True:
            s = self.read()
            if predicate(s):
                return s
            paused = self.paused()
            if paused:
                if pause_since is None:
                    pause_since = s.t
                elif pause_timeout is not None and self.now() - pause_since > pause_timeout:
                    raise ClockTimeout("wait_until: game paused for more than %.1fs" % pause_timeout)
            elif pause_since is not None:
                paused_accum += s.t - pause_since
                pause_since = None
            if timeout is not None:
                active = self.now() - start - paused_accum
                if pause_since is not None:
                    active -= self.now() - pause_since
                if active > timeout:
                    raise ClockTimeout("wait_until: %.3fs elapsed (wave %d, clock %d)"
                                       % (active, s.wave, s.main_counter))
            if paused:
                self.sleep(max(self.poll_interval, 0.02))
                continue
            remaining = eta(s) if eta is not None else None
            if remaining is not None and remaining > self.spin:
                self.sleep(min(remaining - self.spin, 0.05))
            elif remaining is not None:
                self.sleep(0)  # busy-poll; re-read main_counter immediately
            else:
                self.sleep(self.poll_interval)

    def _eta_ticks(self, ticks: Optional[int]) -> Optional[float]:
        """Seconds to sleep for ``ticks`` remaining game ticks.  The last
        tick is always spent busy-polling (its boundary lies anywhere within
        one frame), so the estimate is ``(ticks - 1) * frame_ms``."""
        if ticks is None:
            return None
        return max(ticks - 1, 0) * self.frame_ms / 1000.0

    def wait_wave_time(self, wave: int, cs: int, lead_cs: int = 0,
                       timeout: Optional[float] = None) -> ClockSample:
        """Block until ``wave_time(sample, wave) >= cs - lead_cs``.

        Negative ``cs`` (before the refresh of ``wave``) is resolved from the
        previous wave's context: waits until ``time_to_next_refresh <= -cs``
        while ``sample.wave == wave - 1`` (or until ``wave`` has already
        refreshed, which is "late" but satisfied).  ``lead_cs`` fires early
        to absorb frame catch-up jumps (DESIGN §6: trigger at t-N, never wait
        for equality).
        """
        target = cs - lead_cs

        def remaining_ticks(s: ClockSample) -> Optional[int]:
            wt = self.wave_time(s, wave)
            if wt is None:
                return None
            return target - wt

        def done(s: ClockSample) -> bool:
            if s.wave >= wave and wave in self.refresh_points:
                wt = self.wave_time(s, wave)
                return wt is not None and wt >= target
            if target < 0 and s.wave == wave - 1:
                r = self.time_to_next_refresh(s)
                return r.cs is not None and r.cs <= -target
            return s.wave > wave

        return self.wait_until(done, timeout,
                               eta=lambda s: self._eta_ticks(remaining_ticks(s)))

    def wait_ticks(self, ticks: int, timeout: Optional[float] = None) -> ClockSample:
        """Block for ``ticks`` game ticks from the next read."""
        base = self.read().main_counter + ticks
        return self.wait_until(lambda s: s.main_counter >= base, timeout,
                               eta=lambda s: self._eta_ticks(base - s.main_counter))
