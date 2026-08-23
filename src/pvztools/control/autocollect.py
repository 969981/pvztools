"""Auto-collect sun / coins / awards by calling ``Coin::Collect`` remotely.

The game's own ``Coin::MouseDown`` (GOTY 1096 ``0x436950``) collects a coin
only when ``board.paused == 0``, ``app.game_ui == 3``, ``coin.dead == 0``
(+0x38) and ``coin.is_being_collected == 0`` (+0x50); :class:`AutoCollector`
applies exactly the same guards in Python, then emits **one** remote call
holding a ``mov ecx, coin; call Coin::Collect`` pair per coin (at most
``batch`` coins per call; GOTY ``Coin::Collect`` = ``0x435DC0``, ``this`` in
ECX, no stack args, callee-cleans).

Usage::

    col = AutoCollector(game.actions, game.observer, game.profile, every_frames=10)
    col.tick()          # rate-limited by the board clock (main_counter)
    col.collect_now()   # one-shot, ignores the rate limit
    col.stats           # CollectStats: per-type counts, sun / money estimates

``tick()`` is what :class:`~pvztools.control.executor.PlannedExecutor` and
:class:`~pvztools.control.executor.ReactiveRunner` call on every poll when
given a ``collector=``.

Coin types (re-plants ``ConstEnums.h`` ``CoinType``) are listed in
:data:`COIN_TYPES`; ``types=None`` collects every *collectable* kind
(:data:`DEFAULT_TYPES`), which mirrors the game's own auto-collect filter at
``0x435D60`` plus the level awards (final seed packet, trophy, money bags,
presents ...).  The sun estimate uses ``Coin::GetSunValue`` (25 / 15 / 50)
and the money estimate ``Coin::GetCoinValue`` (1 / 5 / 100, in the game's
x10 coin units).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = ["AutoCollector", "CollectStats", "CoinType", "COIN_TYPES", "DEFAULT_TYPES",
           "SUN_TYPES", "MONEY_TYPES", "AWARD_TYPES", "LEVEL_AWARD_TYPES", "SUN_VALUE", "COIN_VALUE",
           "UI_IN_LEVEL", "DEFAULT_BATCH"]

#: ``LawnApp.game_ui`` value for "playing a level" (Coin::MouseDown guard).
UI_IN_LEVEL = 3
#: Max ``Coin::Collect`` calls folded into one remote call.
DEFAULT_BATCH = 20


class CoinType:
    """``enum CoinType`` (re-plants ``ConstEnums.h`` 163-193), as plain ints.

    Which ids are **level-end items** (``Coin::IsLevelAward``, re-plants
    ``Coin.cpp`` 1473): the one coin the last zombie drops
    (``Zombie::DropLoot`` 7238ff) that must be picked up before the level
    fades to the award screen (``ui 5``):

    * adventure (level <= 50): ``FINAL_SEED_PACKET`` (new plant), ``NOTE``
      (levels 9/19/29/39/49, Dave's note), ``SHOVEL`` (4), ``ALMANAC`` (14),
      ``CARKEYS`` (24), ``TACO`` (34), ``WATERING_CAN`` (44),
      ``AWARD_SILVER_SUNFLOWER`` (50, first time) and ``AWARD_MONEY_BAG``
      (every level once adventure is finished);
    * mini-games / puzzle / survival: ``TROPHY`` (first win),
      ``AWARD_GOLD_SUNFLOWER`` (last trophy) or ``AWARD_MONEY_BAG`` (replay);
    * ``VASE`` (vase breaker), ``AWARD_BAG_DIAMOND``, ``AWARD_PRESENT`` /
      ``AWARD_CHOCOLATE`` (zen garden loot) complete the ``IsLevelAward`` set.

    Everything in that set is in :data:`LEVEL_AWARD_TYPES` (and therefore
    in :data:`DEFAULT_TYPES`).  ``PRESENT_MINIGAMES`` / ``PRESENT_PUZZLE_MODE``
    / ``PRESENT_SURVIVAL_MODE`` and ``PRESENT_PLANT`` / ``CHOCOLATE`` are
    presents (*not* ``IsLevelAward`` but collectable: :data:`AWARD_TYPES`).
    ``USABLE_SEED_PACKET`` (conveyor) and ``NONE`` are never collected.
    """
    NONE = 0
    SILVER = 1
    GOLD = 2
    DIAMOND = 3
    SUN = 4
    SMALLSUN = 5
    LARGESUN = 6
    FINAL_SEED_PACKET = 7
    TROPHY = 8
    SHOVEL = 9
    ALMANAC = 10
    CARKEYS = 11
    VASE = 12
    WATERING_CAN = 13
    TACO = 14
    NOTE = 15
    USABLE_SEED_PACKET = 16
    PRESENT_PLANT = 17
    AWARD_MONEY_BAG = 18
    AWARD_PRESENT = 19
    AWARD_BAG_DIAMOND = 20
    AWARD_SILVER_SUNFLOWER = 21
    AWARD_GOLD_SUNFLOWER = 22
    CHOCOLATE = 23
    AWARD_CHOCOLATE = 24
    PRESENT_MINIGAMES = 25
    PRESENT_PUZZLE_MODE = 26
    PRESENT_SURVIVAL_MODE = 27


#: id -> name for every CoinType.
COIN_TYPES: Dict[int, str] = {
    v: k.lower() for k, v in vars(CoinType).items() if isinstance(v, int) and not k.startswith("_")
}

SUN_TYPES = frozenset({CoinType.SUN, CoinType.SMALLSUN, CoinType.LARGESUN})
MONEY_TYPES = frozenset({CoinType.SILVER, CoinType.GOLD, CoinType.DIAMOND})
#: Level awards / presents (AwardScreen items, zen-garden presents ...).
AWARD_TYPES = frozenset({
    CoinType.FINAL_SEED_PACKET, CoinType.TROPHY, CoinType.SHOVEL, CoinType.ALMANAC,
    CoinType.CARKEYS, CoinType.VASE, CoinType.WATERING_CAN, CoinType.TACO, CoinType.NOTE,
    CoinType.PRESENT_PLANT, CoinType.AWARD_MONEY_BAG, CoinType.AWARD_PRESENT,
    CoinType.AWARD_BAG_DIAMOND, CoinType.AWARD_SILVER_SUNFLOWER, CoinType.AWARD_GOLD_SUNFLOWER,
    CoinType.CHOCOLATE, CoinType.AWARD_CHOCOLATE, CoinType.PRESENT_MINIGAMES,
    CoinType.PRESENT_PUZZLE_MODE, CoinType.PRESENT_SURVIVAL_MODE,
})
#: ``Coin::IsLevelAward`` (re-plants Coin.cpp 0x432F..): the item the last
#: zombie of a level drops; the level does not end until it is collected.
LEVEL_AWARD_TYPES = frozenset({
    CoinType.FINAL_SEED_PACKET, CoinType.TROPHY, CoinType.AWARD_SILVER_SUNFLOWER,
    CoinType.AWARD_GOLD_SUNFLOWER, CoinType.SHOVEL, CoinType.CARKEYS, CoinType.ALMANAC,
    CoinType.VASE, CoinType.WATERING_CAN, CoinType.TACO, CoinType.NOTE, CoinType.AWARD_MONEY_BAG,
    CoinType.AWARD_BAG_DIAMOND, CoinType.AWARD_PRESENT, CoinType.AWARD_CHOCOLATE,
})
assert LEVEL_AWARD_TYPES <= AWARD_TYPES
#: Everything but NONE and USABLE_SEED_PACKET (conveyor / vase-breaker
#: packets are picked up into the cursor, not "collected").
DEFAULT_TYPES = frozenset(SUN_TYPES | MONEY_TYPES | AWARD_TYPES)

#: ``Coin::GetSunValue`` / ``Coin::GetCoinValue`` (re-plants Coin.cpp).
SUN_VALUE: Dict[int, int] = {CoinType.SUN: 25, CoinType.SMALLSUN: 15, CoinType.LARGESUN: 50}
COIN_VALUE: Dict[int, int] = {CoinType.SILVER: 1, CoinType.GOLD: 5, CoinType.DIAMOND: 100}


@dataclass
class CollectStats:
    """What the collector did so far (estimates: the game adds the value when
    the coin *arrives*, we count it when ``Collect`` is issued)."""
    ticks: int = 0            # tick() calls that passed the rate limit
    calls: int = 0            # remote calls issued
    collected: int = 0        # coins passed to Coin::Collect
    sun: int = 0              # estimated sun (25 / 15 / 50)
    money: int = 0            # estimated money (1 / 5 / 100, game units)
    by_type: Dict[int, int] = field(default_factory=dict)
    skipped_paused: int = 0
    skipped_ui: int = 0
    errors: int = 0
    last_error: str = ""

    def add(self, ctype: int) -> None:
        self.collected += 1
        self.by_type[ctype] = self.by_type.get(ctype, 0) + 1
        self.sun += SUN_VALUE.get(ctype, 0)
        self.money += COIN_VALUE.get(ctype, 0)

    @property
    def level_awards(self) -> int:
        """Level-end items (:data:`LEVEL_AWARD_TYPES`) passed to ``Collect``."""
        return sum(n for t, n in self.by_type.items() if t in LEVEL_AWARD_TYPES)

    def summary(self) -> Dict[str, Any]:
        return {
            "collected": self.collected, "calls": self.calls, "sun": self.sun, "money": self.money,
            "level_awards": self.level_awards,
            "by_type": {COIN_TYPES.get(k, str(k)): v for k, v in sorted(self.by_type.items())},
            "skipped_paused": self.skipped_paused, "skipped_ui": self.skipped_ui,
            "errors": self.errors,
        }


class AutoCollector:
    """Collect coins via remote ``Coin::Collect`` calls (module doc).

    ``actions_or_executor``  an :class:`~pvztools.control.actions.Actions`
        (its ``conv`` and ``_run`` are used: the call runs immediately and is
        appended to ``actions.history``) **or** a bare remote executor with ``run(asm, name=...)``
        (a convention is built from ``profile``; pass ``conv=`` to override).
    ``observer``   needs ``game()`` (``.ui``), ``board()`` (``.paused``,
        ``.clock``), ``coins()`` and either ``coins_base()`` or
        ``board_ptr()`` + ``session.read_ptr`` for the array base.
    ``every_frames``  minimum ``main_counter`` delta between two collections.
    ``types``  iterable of CoinType ids to collect (default: all collectable).
    ``batch``  cap on coins per remote call; the rest wait for the next tick.
    """

    def __init__(self, actions_or_executor: Any, observer: Any, profile: Any, *,
                 every_frames: int = 10, types: Optional[Iterable[int]] = None,
                 batch: int = DEFAULT_BATCH, conv: Any = None) -> None:
        self.observer = observer
        self.profile = profile
        self.every_frames = max(0, int(every_frames))
        self.batch = max(1, int(batch))
        self.types = frozenset(int(t) for t in types) if types is not None else DEFAULT_TYPES
        #: True when every level-end item type is in ``types`` (the level
        #: cannot end while its award coin lies on the lawn)
        self.collects_level_awards = LEVEL_AWARD_TYPES <= self.types
        self.stats = CollectStats()
        self.enabled = True
        self._last_clock: Optional[int] = None
        self.history: List[Any] = []  # fragments emitted (newest last)
        if hasattr(actions_or_executor, "conv") and hasattr(actions_or_executor, "_run"):
            self.actions = actions_or_executor
            self.executor = getattr(actions_or_executor, "executor", None)
            self.conv = conv if conv is not None else actions_or_executor.conv
        else:
            self.actions = None
            self.executor = actions_or_executor
            if conv is None:
                from .conv import convention_for

                conv = convention_for(profile)
            self.conv = conv
        self._stride = int(profile.objects["coin"].stride)

    # -- observation -----------------------------------------------------------
    def _coin_base(self) -> int:
        fn = getattr(self.observer, "coins_base", None)
        if callable(fn):
            return int(fn())
        board = int(self.observer.board_ptr())
        session = getattr(self.observer, "session", None)
        if session is None and self.actions is not None:
            session = getattr(self.actions, "session", None)
        if session is None:
            raise RuntimeError("AutoCollector: no session to read the coin array base")
        return int(session.read_ptr(board + self.profile.board.coins))

    def coin_addr(self, index: int, base: Optional[int] = None) -> int:
        base = self._coin_base() if base is None else base
        return base + int(index) * self._stride

    def select(self, coins: Iterable[Any]) -> List[Any]:
        """Coins passing the MouseDown guards + the type filter (all of them;
        batching is done by the caller)."""
        out = []
        for c in coins:
            if getattr(c, "dead", False) or getattr(c, "collecting", False):
                continue
            if int(getattr(c, "type", 0)) not in self.types:
                continue
            out.append(c)
        return out

    def _guards(self) -> Optional[Tuple[Any, int]]:
        """``(board, clock)`` when the game state allows collecting, else None."""
        game = self.observer.game()
        if int(getattr(game, "ui", 0)) != UI_IN_LEVEL:
            self.stats.skipped_ui += 1
            return None
        board = self.observer.board()
        if getattr(board, "paused", False):
            self.stats.skipped_paused += 1
            return None
        return board, int(getattr(board, "clock", 0))

    # -- emission --------------------------------------------------------------
    def _emit(self, coins: Sequence[Any]) -> Any:
        base = self._coin_base()
        addrs = [self.coin_addr(c.index, base) for c in coins]
        frag = self.conv.coin_collect_many(addrs)
        self.history.append(frag)
        if self.actions is not None:
            res = self.actions._run(frag, "autocollect")
        else:
            res = self.executor.run(frag.asm, name="autocollect",
                                    extra_patches=tuple(frag.required_patches))
        self.stats.calls += 1
        for c in coins:
            self.stats.add(int(c.type))
        return res

    def _collect(self, raise_errors: bool) -> int:
        coins = self.select(self.observer.coins())
        if not coins:
            return 0
        coins = list(coins)[: self.batch]
        try:
            self._emit(coins)
        except Exception as e:  # noqa: BLE001 - executor loops must survive one failed call
            self.stats.errors += 1
            self.stats.last_error = "%s: %s" % (type(e).__name__, e)
            if raise_errors:
                raise
            return 0
        return len(coins)

    # -- public ----------------------------------------------------------------
    def tick(self, sample: Any = None) -> int:
        """Collect if the board clock moved ``every_frames`` since the last
        collection (``sample`` is ignored; kept for loop hooks that pass their
        clock sample).  Returns the number of coins collected; a failed
        remote call is recorded in ``stats.errors`` and returns 0."""
        if not self.enabled:
            return 0
        g = self._guards()
        if g is None:
            return 0
        _, clock = g
        if self._last_clock is not None and self.every_frames:
            delta = clock - self._last_clock
            if 0 <= delta < self.every_frames:
                return 0
        self._last_clock = clock
        self.stats.ticks += 1
        return self._collect(raise_errors=False)

    def collect_now(self) -> int:
        """One shot: collect everything selectable right now (guards still
        apply; the rate limit does not).  Returns the number collected."""
        g = self._guards()
        if g is None:
            return 0
        self._last_clock = g[1]
        self.stats.ticks += 1
        return self._collect(raise_errors=True)

    def reset(self) -> None:
        self.stats = CollectStats()
        self._last_clock = None
        self.history = []

    def __repr__(self) -> str:
        return "AutoCollector(every=%d, types=%d, %s)" % (
            self.every_frames, len(self.types), self.stats.summary())
