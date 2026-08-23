"""Reactive policies (DESIGN §6.2b): pure functions ``WorldView -> [Decision]``.

A policy never touches the game.  It receives one :class:`WorldView`
(assembled by :class:`~pvztools.control.executor.reactive.ReactiveRunner`
from a board snapshot) and returns the list of :class:`Decision` items the
runner should execute *this poll*.  Everything a policy needs to remember
(pairs already fired in this wave, when the last pair was fired) is carried
by the view, so a policy can be exercised with scripted views in tests.

Decision kinds (``Decision.kind``):

``fire_cob``  ``args = {"fires": [(cob_index, (row, col)), ...]}``
``plant``     ``args = {"row": r, "col": c, "plant": PlantType id}``
``shovel``    ``args = {"row": r, "col": c}``
``wait``      no args; ``reason`` says why nothing was done.

Timing vocabulary (all centiseconds, 1 cs = one game tick):

* ``wave_time`` -- cs since the refresh point of ``wave`` (the tick at
  which ``Board.wave`` became that value).  Negative before the first wave.
* ``activate_at`` (341) -- the classic P-series activation point: a pair
  fired at 341 lands at 341 + 373 = 714, i.e. just after the 601 minimum
  wave length... the view tells us directly whether a wave is *already*
  activated, so this is only the earliest moment we are willing to fire.

How "activated" is detected (no community estimate needed): the game forces
``countdown = 200`` when the wave's zombie HP drops below
``Board.hp_threshold``; so a wave is "not yet activated" when
``countdown > 200`` (no huge-wave text running) **and** ``wave_hp`` is
still above ``hp_threshold * k`` (or unknown).  ``countdown <= 200`` always
means wait: the refresh is forced within 200 cs and a pair would land
(373 cs) after it.  When the
profile has no ``wave_hp`` field the view falls back to the summed HP of the
current wave's live zombies (see ``WorldView.wave_hp``).

Huge waves (w10 / w20): the zombies of a flag wave spawn further right
(§6.2 "出生点偏右"), so a pair fired at 341 lands ahead of the crowd.
:class:`PSeriesPolicy` delays activation on those waves to
``activate_at + HUGE_WAVE_EXTRA_DELAY`` (341 + 341 = 682), a conservative
community value rather than a derived one; tune ``huge_wave_extra``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pvztools.schema.enums import PlantType
from pvztools.schema.timing import COB_FLIGHT_LAND, WAVE_MIN_LEN, is_huge_wave

__all__ = [
    "Decision", "CobView", "SeedView", "ZombieView", "PlantView", "WorldView",
    "PSeriesPolicy", "IceRefillPolicy", "ComboPolicy",
    "KIND_FIRE", "KIND_PLANT", "KIND_SHOVEL", "KIND_WAIT",
    "DEFAULT_ACTIVATE_AT", "HUGE_WAVE_EXTRA_DELAY", "ACTIVATED_COUNTDOWN",
    "COB_CANNON", "COB_READY",
]

KIND_FIRE = "fire_cob"
KIND_PLANT = "plant"
KIND_SHOVEL = "shovel"
KIND_WAIT = "wait"

#: Plant.mState of a ready cob cannon (source-map §6.3).
COB_READY = 37
COB_CANNON = int(PlantType.COB_CANNON)

#: Classic P-series activation point (cs after refresh).
DEFAULT_ACTIVATE_AT = 341
#: Extra delay on w10 / w20 (zombies spawn further right).
HUGE_WAVE_EXTRA_DELAY = 341
#: The game clamps ``countdown`` to this value once the wave is activated.
ACTIVATED_COUNTDOWN = 200


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    kind: str
    args: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @property
    def is_wait(self) -> bool:
        return self.kind == KIND_WAIT

    @staticmethod
    def wait(reason: str) -> "Decision":
        return Decision(KIND_WAIT, {}, reason)

    @staticmethod
    def fire(fires: Sequence[Tuple[int, Tuple[int, float]]], reason: str) -> "Decision":
        return Decision(KIND_FIRE, {"fires": [(int(i), (int(r), float(c))) for i, (r, c) in fires]}, reason)

    @staticmethod
    def plant(row: int, col: int, plant: int, reason: str) -> "Decision":
        return Decision(KIND_PLANT, {"row": int(row), "col": int(col), "plant": int(plant)}, reason)

    @staticmethod
    def shovel(row: int, col: int, reason: str) -> "Decision":
        return Decision(KIND_SHOVEL, {"row": int(row), "col": int(col)}, reason)


@dataclass(frozen=True)
class CobView:
    """A cob cannon (1-based ``row`` / ``col`` = LEFT cell).  ``ready_in`` is
    the estimated cs until READY (0 when ready, ``None`` when unknown)."""
    index: int
    row: int
    col: int
    state: int
    ready_in: Optional[int] = None

    @property
    def ready(self) -> bool:
        return self.state == COB_READY


@dataclass(frozen=True)
class SeedView:
    slot: int
    type: int
    usable: bool
    cd_remaining: int = 0


@dataclass(frozen=True)
class PlantView:
    index: int
    row: int
    col: int
    type: int
    state: int = 0


@dataclass(frozen=True)
class ZombieView:
    index: int
    row: int
    x: float
    type: int
    hp: int
    from_wave: Optional[int] = None


@dataclass(frozen=True)
class WorldView:
    """Everything a policy may look at for one poll.

    ``wave_hp`` is ``Board.wave_hp`` when the profile has it, else the summed
    ``total_hp`` of live zombies whose ``from_wave == wave - 1`` (the game's
    ``mFromWave`` is 0-based), else ``None`` -> only ``countdown`` decides.
    ``pairs_fired`` / ``last_fire_wave_time`` are bookkeeping the runner
    keeps for the *current* wave so policies stay stateless.
    """
    time: int
    wave: int
    wave_time: int
    countdown: int
    hp_threshold: int
    wave_hp: Optional[int] = None
    huge_wave_countdown: int = 0
    total_waves: int = 20
    paused: bool = False
    scene: int = 0
    cobs: Tuple[CobView, ...] = ()
    seeds: Tuple[SeedView, ...] = ()
    plants: Tuple[PlantView, ...] = ()
    zombies: Tuple[ZombieView, ...] = ()
    pairs_fired: int = 0
    last_fire_wave_time: Optional[int] = None
    wave_time_exact: bool = True

    # -- derived -------------------------------------------------------------
    @property
    def ready_cobs(self) -> Tuple[CobView, ...]:
        return tuple(c for c in self.cobs if c.ready)

    @property
    def is_huge_wave(self) -> bool:
        return is_huge_wave(self.wave)

    @property
    def is_flag_wave_next(self) -> bool:
        nxt = self.wave + 1
        return nxt > 0 and nxt % 10 == 0

    @property
    def next_refresh_in(self) -> int:
        """Approximate cs until the next refresh point.  Normal waves:
        ``countdown``.  When the next wave is a flag wave and the red text is
        already running: ``huge_wave_countdown``; before the text starts the
        text itself (750 cs) is added to ``countdown`` -- an approximation
        (the text starts when countdown reaches ~1-5)."""
        if self.huge_wave_countdown > 0:
            return self.huge_wave_countdown
        if self.is_flag_wave_next:
            return self.countdown + 750
        return self.countdown

    @property
    def activated(self) -> bool:
        """True once the current wave's HP is below the threshold (the game
        has clamped ``countdown`` to 200 / started the red text)."""
        if self.huge_wave_countdown > 0:
            return True
        if self.countdown <= ACTIVATED_COUNTDOWN:
            return True
        if self.wave_hp is not None and self.hp_threshold > 0:
            return self.wave_hp <= self.hp_threshold
        return False

    def seed(self, plant: int) -> Optional[SeedView]:
        for s in self.seeds:
            if int(s.type) == int(plant):
                return s
        return None

    def plants_at(self, row: int, col: int) -> Tuple[PlantView, ...]:
        return tuple(p for p in self.plants if p.row == row and p.col == col)


# ---------------------------------------------------------------------------
# policies
# ---------------------------------------------------------------------------

class PSeriesPolicy:
    """P-series activation: one pair of cobs per wave, fired once the wave
    is old enough and not yet activated; a fallback pair when the first
    pair landed and the wave is *still* above threshold.

    ``cob_targets`` -- landing cells in firing order, consumed two at a
    time: pair 1 uses ``[0:2]``, the fallback pair ``[2:4]`` (wrapping back
    to ``[0:2]`` when only two are given).
    ``hp_factor`` (``k``) -- scale on ``hp_threshold``; ``> 1`` fires while
    the wave is still comfortably above threshold, ``< 1`` only when it is
    nearly refreshable anyway.
    ``min_wave_len`` is recorded for reporting / the runner's pacing; the
    decision itself is driven by ``activate_at`` and the activation check.
    """

    def __init__(self, cob_targets: Sequence[Tuple[int, float]], *,
                 min_wave_len: int = WAVE_MIN_LEN, activate_at: int = DEFAULT_ACTIVATE_AT,
                 max_wait: int = 2500, hp_factor: float = 1.0,
                 huge_wave_extra: int = HUGE_WAVE_EXTRA_DELAY,
                 flight: int = COB_FLIGHT_LAND) -> None:
        targets = [(int(r), float(c)) for r, c in cob_targets]
        if len(targets) < 2:
            raise ValueError("cob_targets 至少需要两个落点（一对炮）")
        self.cob_targets: Tuple[Tuple[int, float], ...] = tuple(targets)
        self.min_wave_len = int(min_wave_len)
        self.activate_at = int(activate_at)
        self.max_wait = int(max_wait)
        self.hp_factor = float(hp_factor)
        self.huge_wave_extra = int(huge_wave_extra)
        self.flight = int(flight)

    # -- helpers -------------------------------------------------------------
    def activation_time(self, wave: int) -> int:
        """Earliest ``wave_time`` at which a pair may be fired on ``wave``."""
        return self.activate_at + (self.huge_wave_extra if is_huge_wave(wave) else 0)

    def unactivated(self, view: WorldView) -> bool:
        if view.huge_wave_countdown > 0:
            return False
        if view.countdown <= ACTIVATED_COUNTDOWN:
            return False  # refresh forced within 200 cs: a pair would land after it
        if view.wave_hp is not None and view.hp_threshold > 0:
            return view.wave_hp > view.hp_threshold * self.hp_factor
        return True

    def pair_targets(self, pair_no: int) -> Tuple[Tuple[int, float], Tuple[int, float]]:
        i = 2 * int(pair_no)
        if i + 1 >= len(self.cob_targets):
            i = 0
        return self.cob_targets[i], self.cob_targets[i + 1]

    # -- decision ------------------------------------------------------------
    def decide(self, view: WorldView) -> List[Decision]:
        if view.paused:
            return [Decision.wait("paused")]
        if view.wave <= 0:
            return [Decision.wait("before first wave (wave=0)")]
        if view.wave >= view.total_waves:
            return [Decision.wait("final wave %d: nothing left to activate" % view.wave)]
        t0 = self.activation_time(view.wave)
        if view.wave_time < t0:
            return [Decision.wait("wave_time %d < activate_at %d" % (view.wave_time, t0))]
        if not self.unactivated(view):
            return [Decision.wait("wave %d already activated (countdown=%d hp=%s thr=%d)" % (
                view.wave, view.countdown, view.wave_hp, view.hp_threshold))]
        if view.pairs_fired >= 1:
            if view.pairs_fired >= 2:
                return [Decision.wait("two pairs already fired on wave %d" % view.wave)]
            if view.wave_time >= self.max_wait:
                return [Decision.wait("wave_time %d >= max_wait %d: no fallback" % (view.wave_time, self.max_wait))]
            last = view.last_fire_wave_time
            if last is not None and view.wave_time < last + self.flight:
                return [Decision.wait("pair in flight: lands at %d" % (last + self.flight))]
            label = "fallback pair"
        else:
            label = "activation pair"
        ready = view.ready_cobs
        if len(ready) < 2:
            eta = [c.ready_in for c in view.cobs if not c.ready and c.ready_in is not None]
            return [Decision.wait("%s: only %d ready cob(s)%s" % (
                label, len(ready), (", next in %d cs" % min(eta)) if eta else ""))]
        t1, t2 = self.pair_targets(view.pairs_fired)
        fires = [(ready[0].index, t1), (ready[1].index, t2)]
        return [Decision.fire(fires, "%s on wave %d at %d cs (countdown=%d hp=%s thr=%d)" % (
            label, view.wave, view.wave_time, view.countdown, view.wave_hp, view.hp_threshold))]


class IceRefillPolicy:
    """Plant an ice-shroom at ``cell`` when its card is usable and
    ``wave_time`` is inside ``window``; on day scenes follow with a coffee
    bean once the mushroom is on the cell.  Deliberately simple: one cell,
    no imitater handling, no shoveling."""

    def __init__(self, cell: Tuple[int, int], window: Tuple[int, int] = (0, 400), *,
                 ice: int = int(PlantType.ICE_SHROOM), coffee: Optional[bool] = None) -> None:
        self.cell = (int(cell[0]), int(cell[1]))
        self.window = (int(window[0]), int(window[1]))
        self.ice = int(ice)
        self.coffee = coffee  # None -> decide from scene (day needs coffee)

    def _needs_coffee(self, view: WorldView) -> bool:
        if self.coffee is not None:
            return bool(self.coffee)
        return view.scene in (0, 2, 4)  # DE / PE / RE

    def decide(self, view: WorldView) -> List[Decision]:
        if view.paused:
            return [Decision.wait("paused")]
        lo, hi = self.window
        if not (lo <= view.wave_time <= hi):
            return [Decision.wait("ice window %d~%d, wave_time %d" % (lo, hi, view.wave_time))]
        r, c = self.cell
        here = view.plants_at(r, c)
        has_ice = any(int(p.type) == self.ice for p in here)
        if not has_ice:
            seed = view.seed(self.ice)
            if seed is None or not seed.usable:
                return [Decision.wait("ice card not usable")]
            if here:
                return [Decision.wait("cell (%d,%d) occupied" % (r, c))]
            return [Decision.plant(r, c, self.ice, "ice at (%d,%d), wave_time %d" % (r, c, view.wave_time))]
        if self._needs_coffee(view):
            if any(int(p.type) == int(PlantType.COFFEE_BEAN) for p in here):
                return [Decision.wait("coffee already on ice")]
            seed = view.seed(int(PlantType.COFFEE_BEAN))
            if seed is None or not seed.usable:
                return [Decision.wait("coffee card not usable")]
            return [Decision.plant(r, c, int(PlantType.COFFEE_BEAN), "coffee on ice (%d,%d)" % (r, c))]
        return [Decision.wait("ice already planted")]


class ComboPolicy:
    """Run several policies on the same view; concatenates their decisions
    (``wait`` entries dropped when any policy acted)."""

    def __init__(self, *policies: Any) -> None:
        self.policies = tuple(policies)

    def decide(self, view: WorldView) -> List[Decision]:
        out: List[Decision] = []
        for p in self.policies:
            out.extend(p.decide(view))
        acted = [d for d in out if not d.is_wait]
        return acted if acted else out[:1] if out else [Decision.wait("no policies")]
