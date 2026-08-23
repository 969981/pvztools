"""Typed, read-only snapshot objects (DESIGN §4).

Every class here is a frozen dataclass that carries *values only*: no
pointers into the game other than the informational ``board_ptr`` /
``lawn_ptr`` on :class:`GameState`.  They are produced by
:mod:`pvztools.observe` from an address :class:`~pvztools.addr.Profile` and
never know where their bytes came from, so they can equally be built from a
save, an emulator or a unit-test fixture.

Consistency note: a snapshot is only as consistent as the single
ReadProcessMemory call that produced each block (Board header, plant array,
zombie array ...).  Array slots are recycled by the game, so identify objects
across snapshots by ``(index, serial)`` when ``serial`` is available, never by
``index`` alone.

Fields whose offset is not known for a build are ``None`` (see the per-class
docstrings); the observer never guesses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, Optional, Tuple

from ..schema.enums import PLANT_ZH, ZOMBIE_ZH, PlantType, ZombieType

__all__ = [
    "GameState",
    "Cheats",
    "Board",
    "Plant",
    "Zombie",
    "Projectile",
    "Coin",
    "Seed",
    "SpawnList",
    "Snapshot",
    "plant_name_zh",
    "zombie_name_zh",
    "as_plant_type",
    "as_zombie_type",
    "SEED_EMPTY",
    "SEED_EMPTY_NAME",
    "SPAWN_WAVES",
    "SPAWN_PER_WAVE",
    "SPAWN_EMPTY",
    "ZOMBIE_WAVE_CUTSCENE",
]

#: Zombie.from_wave value of zombies placed by the level intro cut-scene
#: (re-plants ``ZOMBIE_WAVE_CUTSCENE``); they never belong to a wave.
ZOMBIE_WAVE_CUTSCENE = -1

#: Spawn list geometry: 20 waves x 50 int32 slots, -1 == empty.
SPAWN_WAVES = 20
SPAWN_PER_WAVE = 50
SPAWN_EMPTY = -1


#: Seed-bank slots that have not been chosen yet (seed chooser) hold this id.
SEED_EMPTY = -1
SEED_EMPTY_NAME = "（空）"


def plant_name_zh(type_id: int) -> str:
    """Chinese name for a plant id; ``未知(n)`` for ids outside the enum."""
    try:
        return PLANT_ZH[PlantType(type_id)]
    except (ValueError, KeyError):
        return "未知(%d)" % type_id


def as_plant_type(type_id: int) -> int:
    """``PlantType`` member when the id is known (it is still an ``int``),
    else the raw int (mods add ids)."""
    try:
        return PlantType(type_id)
    except ValueError:
        return int(type_id)


def as_zombie_type(type_id: int) -> int:
    try:
        return ZombieType(type_id)
    except ValueError:
        return int(type_id)


def zombie_name_zh(type_id: int) -> str:
    """Chinese name for a zombie id; ``未知(n)`` for ids outside the enum."""
    try:
        return ZOMBIE_ZH[ZombieType(type_id)]
    except (ValueError, KeyError):
        return "未知(%d)" % type_id


@dataclass(frozen=True)
class GameState:
    """LawnApp-level state; valid even when no level is loaded.

    ``board_ptr == 0`` means "not in a level" (menu, level select, ...).
    ``ui`` is LawnApp.game_ui (3 == in level), ``mode`` LawnApp.game_mode
    (0 adventure, 11-15 survival endless ... see parity §5 gates).
    """

    version: str
    capability: str
    ui: int
    mode: int
    frame_duration: int
    board_ptr: int
    lawn_ptr: int

    @property
    def in_level(self) -> bool:
        return self.board_ptr != 0


@dataclass(frozen=True)
class Cheats:
    """The seven built-in cheat-code booleans (Board +0x5761..+0x5767 on 1051).

    Only populated when the profile names them (``board.cheat_<name>``);
    otherwise :attr:`Board.cheats` is ``None``.
    """

    mustache: bool
    trickedout: bool
    future: bool
    pinata: bool
    dance: bool
    daisies: bool
    sukhbir: bool


@dataclass(frozen=True)
class Board:
    """Board header values (sun, clocks, wave bookkeeping).

    * ``clock`` — Board.main_counter: battle clock, stops while paused.
    * ``global_clock`` — Board.global_counter: keeps running while paused.
    * ``countdown`` — centiseconds until the next wave is forced;
      ``countdown_init`` is the value it was reset to.
    * ``huge_wave_countdown`` — the "A huge wave approaches" pre-flag timer.
    * ``hp_threshold`` — zombie HP at which the next wave triggers early
      (read directly; no need to estimate the community "341" rule).
    * ``wave_hp`` — current total zombie HP of the wave (``None`` if the
      profile has no offset for it).

    Fields from source-map §4.1 (``None`` when the profile lacks them):

    * ``time_stop_counter`` — Board.mTimeStopCounter (Ice-shroom time stop).
    * ``board_rand_seed`` — Board.mBoardRandSeed (replay / simulation).
    * ``total_spawned_waves`` — Board.mTotalSpawnedWaves.
    * ``zombie_health_wave_start`` — Board.mZombieHealthWaveStart.
    * ``level_complete`` — Board.mLevelComplete.
    * ``cob_cursor_delay`` — Board.mCobCannonCursorDelayCounter.
    * ``ice_min_x`` — Board.mIceMinX[6], ice-trail start x per row.
    """

    sun: int
    scene: int
    level: int
    paused: bool
    clock: int
    global_clock: int
    wave: int
    total_waves: int
    countdown: int
    countdown_init: int
    huge_wave_countdown: int
    hp_threshold: int
    wave_hp: Optional[int] = None
    cheats: Optional[Cheats] = None
    time_stop_counter: Optional[int] = None
    board_rand_seed: Optional[int] = None
    total_spawned_waves: Optional[int] = None
    zombie_health_wave_start: Optional[int] = None
    level_complete: Optional[bool] = None
    cob_cursor_delay: Optional[int] = None
    ice_min_x: Optional[Tuple[int, ...]] = None

    @property
    def is_flag_wave_next(self) -> bool:
        """True when the upcoming wave index (1-based ``wave + 1``) is a
        multiple of 10, i.e. a flag ("huge") wave."""
        nxt = self.wave + 1
        return nxt > 0 and nxt % 10 == 0


@dataclass(frozen=True)
class Plant:
    """One slot of the plant array.

    ``state`` — Plant.mState (cob cannon: 37 ready / 38 firing).
    ``imitater`` — planted from an Imitater card.  ``serial`` is ``None``
    unless the profile provides ``objects.plant.serial``.

    source-map §4.2 counters (``None`` when the profile lacks them):
    ``state_countdown`` (mStateCountdown: cob cannon arming, potato mine,
    sun-shroom growth), ``shooting_counter`` (mShootingCounter: 205 cs after
    a cob shot), ``target_x`` / ``target_y`` (cob cannon target pixel),
    ``disappear_countdown`` / ``do_special_countdown`` (instant plants).
    """

    index: int
    row: int
    col: int
    type: int
    name_zh: str
    state: int
    hp: int
    max_hp: int
    dead: bool
    squished: bool
    asleep: bool
    imitater: bool
    serial: Optional[int] = None
    state_countdown: Optional[int] = None
    shooting_counter: Optional[int] = None
    target_x: Optional[int] = None
    target_y: Optional[int] = None
    disappear_countdown: Optional[int] = None
    do_special_countdown: Optional[int] = None

    def __post_init__(self) -> None:
        # spec (user-scenarios §2): ``p.type`` is a PlantType when known
        object.__setattr__(self, "type", as_plant_type(self.type))

    @property
    def name(self) -> str:
        """Chinese name (alias of ``name_zh``)."""
        return self.name_zh

    @property
    def alive(self) -> bool:
        return not self.dead

    @property
    def plant_type(self) -> Optional[PlantType]:
        try:
            return PlantType(self.type)
        except ValueError:
            return None

    @property
    def cell(self) -> Tuple[int, int]:
        """1-based ``(row, col)`` as used by the 轨道 DSL."""
        return (self.row + 1, self.col + 1)


@dataclass(frozen=True)
class Zombie:
    """One slot of the zombie array.

    ``status`` — Zombie.mZombiePhase (community numbering).  ``x``/``y`` are
    pixel positions, ``speed`` the per-frame velocity.  ``helmet_hp`` is the
    cone/bucket/football helmet, ``shield_hp`` the newspaper/screen-door/ladder
    shield.

    source-map §4.3 fields (``None`` when the profile lacks them):
    ``from_wave`` (mFromWave, -1 for cut-scene zombies, see
    :attr:`is_cutscene`), ``chilled_counter`` / ``buttered_counter`` /
    ``ice_trap_counter`` (slow / butter / freeze remaining cs),
    ``mind_controlled`` (hypnotised), ``has_head`` / ``has_arm`` /
    ``has_object``, ``in_pool``, ``helm_type`` / ``helmet_max_hp``,
    ``shield_type`` / ``shield_max_hp``, ``target_col`` / ``target_row``
    (bungee / catapult), ``phase_counter`` (jack-in-the-box / jalapeno head
    countdown) and ``altitude`` (balloon / bungee height).  The community
    names ``slowed_cd`` / ``frozen_cd`` / ``hypnotized`` / ``in_water`` are
    read-only properties over those fields.
    """

    index: int
    row: int
    type: int
    name_zh: str
    status: int
    x: float
    y: float
    speed: float
    hp: int
    max_hp: int
    helmet_hp: int
    shield_hp: int
    dead: bool
    from_wave: Optional[int] = None
    chilled_counter: Optional[int] = None
    buttered_counter: Optional[int] = None
    ice_trap_counter: Optional[int] = None
    mind_controlled: Optional[bool] = None
    has_head: Optional[bool] = None
    has_arm: Optional[bool] = None
    has_object: Optional[bool] = None
    in_pool: Optional[bool] = None
    helm_type: Optional[int] = None
    helmet_max_hp: Optional[int] = None
    shield_type: Optional[int] = None
    shield_max_hp: Optional[int] = None
    target_col: Optional[int] = None
    target_row: Optional[int] = None
    phase_counter: Optional[int] = None
    altitude: Optional[float] = None

    def __post_init__(self) -> None:
        # spec (user-scenarios §2): ``z.type`` is a ZombieType when known
        object.__setattr__(self, "type", as_zombie_type(self.type))

    # community aliases (kept for callers written against the old names)
    @property
    def slowed_cd(self) -> Optional[int]:
        return self.chilled_counter

    @property
    def frozen_cd(self) -> Optional[int]:
        return self.ice_trap_counter

    @property
    def hypnotized(self) -> Optional[bool]:
        return self.mind_controlled

    @property
    def in_water(self) -> Optional[bool]:
        return self.in_pool

    @property
    def is_cutscene(self) -> Optional[bool]:
        """True for zombies placed by the level intro (``from_wave == -1``);
        ``None`` when the profile has no ``from_wave``."""
        return None if self.from_wave is None else self.from_wave == ZOMBIE_WAVE_CUTSCENE

    @property
    def name(self) -> str:
        """Chinese name (alias of ``name_zh``)."""
        return self.name_zh

    @property
    def alive(self) -> bool:
        return not self.dead

    @property
    def zombie_type(self) -> Optional[ZombieType]:
        try:
            return ZombieType(self.type)
        except ValueError:
            return None

    @property
    def total_hp(self) -> int:
        return self.hp + self.helmet_hp + self.shield_hp


@dataclass(frozen=True)
class Projectile:
    """One slot of the projectile array (source-map §4.6).

    ``type`` — Projectile.mProjectileType (0 pea, 1 snow pea, 2 cabbage,
    3 melon, 4 puff, 5 wintermelon, 6 fire pea, 7 star, 8 spike, 9 basketball,
    10 kernel, 11 cob cannon, 12 butter, 13 zombie pea).  ``pos`` / ``vel``
    are ``(x, y, z)`` pixel triples.  ``cob_target_x`` / ``cob_target_row``
    are only meaningful for cob-cannon shells (``None`` when the profile has
    no offset).  ``age`` is mProjectileAge in frames.
    """

    index: int
    type: int
    row: int
    pos: Tuple[float, float, float]
    vel: Tuple[float, float, float]
    dead: bool
    cob_target_x: Optional[float] = None
    cob_target_row: Optional[int] = None
    age: Optional[int] = None
    motion_type: Optional[int] = None

    @property
    def alive(self) -> bool:
        return not self.dead

    @property
    def x(self) -> float:
        return self.pos[0]

    @property
    def y(self) -> float:
        return self.pos[1]


@dataclass(frozen=True)
class Coin:
    """One slot of the coin / item array (source-map §4.6, Board.mCoins).

    ``type`` — Coin.mType (sun, coins, diamonds, seed packets, trophies,
    fertiliser ...).  ``collecting`` is mIsBeingCollected; ``age`` mCoinAge;
    ``usable_seed_type`` the seed id carried by a conveyor / vase packet
    (``None`` when the profile has no offset).
    """

    index: int
    type: int
    pos: Tuple[float, float]
    dead: bool
    collecting: Optional[bool] = None
    age: Optional[int] = None
    usable_seed_type: Optional[int] = None
    motion: Optional[int] = None

    @property
    def alive(self) -> bool:
        return not self.dead

    @property
    def x(self) -> float:
        return self.pos[0]

    @property
    def y(self) -> float:
        return self.pos[1]


@dataclass(frozen=True)
class Seed:
    """One seed-bank packet.

    ``type`` is the card id; for an Imitater card ``type == 48`` and
    ``imitater_type`` holds the mimicked plant.  ``usable`` is the game's own
    "can click" flag (cooldown done and enough sun).  ``cd_elapsed`` counts
    up from 0 after a use; a packet that has never been used (level start)
    shows ``cd_elapsed == 0`` while being usable, so :attr:`ready` and
    :attr:`cd_remaining` take ``usable`` into account.
    """

    slot: int
    type: int
    imitater_type: int
    cd_elapsed: int
    cd_total: int
    usable: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", as_plant_type(self.type))
        object.__setattr__(self, "imitater_type", as_plant_type(self.imitater_type))

    @property
    def empty(self) -> bool:
        """True for a slot that has not been chosen yet (seed chooser: id -1)."""
        return self.type < 0

    @property
    def effective_type(self) -> int:
        """The plant that would actually be placed."""
        return self.imitater_type if self.type == PlantType.IMITATER else self.type

    @property
    def plant_type(self) -> Optional[PlantType]:
        """``PlantType`` of :attr:`effective_type`, ``None`` when unknown / empty."""
        try:
            return PlantType(self.effective_type)
        except ValueError:
            return None

    @property
    def name_zh(self) -> str:
        if self.empty:
            return SEED_EMPTY_NAME
        base = plant_name_zh(self.effective_type)
        return ("模仿" + base) if self.type == PlantType.IMITATER else base

    @property
    def name(self) -> str:
        """Chinese name (alias of ``name_zh``)."""
        return self.name_zh

    @property
    def cd_remaining(self) -> int:
        """Centiseconds until the packet recharges (0 when usable)."""
        if self.usable:
            return 0
        return max(0, self.cd_total - self.cd_elapsed)

    @property
    def ready(self) -> bool:
        """Cooldown finished (sun not considered); never for an empty slot."""
        if self.empty:
            return False
        return self.usable or self.cd_total == 0 or self.cd_elapsed >= self.cd_total


@dataclass(frozen=True)
class SpawnList:
    """Decoded spawn list: ``waves[i]`` is the tuple of zombie ids of wave
    ``i + 1`` (empty slots already stripped)."""

    waves: Tuple[Tuple[int, ...], ...]

    def __len__(self) -> int:
        return len(self.waves)

    def __iter__(self) -> Iterator[Tuple[int, ...]]:
        return iter(self.waves)

    def wave(self, n: int) -> Tuple[int, ...]:
        """1-based wave accessor."""
        return self.waves[n - 1]

    def per_wave_counts(self) -> Tuple[Dict[int, int], ...]:
        """For each wave a ``{zombie_id: count}`` dict (insertion order ==
        first appearance in the list)."""
        out = []
        for w in self.waves:
            counts: Dict[int, int] = {}
            for z in w:
                counts[z] = counts.get(z, 0) + 1
            out.append(counts)
        return tuple(out)

    def types_present(self) -> Tuple[int, ...]:
        """Sorted distinct zombie ids appearing anywhere in the list."""
        return tuple(sorted({z for w in self.waves for z in w}))

    def has(self, zombie_type: int) -> bool:
        return any(int(zombie_type) in w for w in self.waves)

    def total(self) -> int:
        return sum(len(w) for w in self.waves)

    @property
    def non_empty_waves(self) -> int:
        return sum(1 for w in self.waves if w)


@dataclass(frozen=True)
class Snapshot:
    """Everything :meth:`Observer.snapshot` reads in one go."""

    game: GameState
    board: Board
    plants: Tuple[Plant, ...] = field(default_factory=tuple)
    zombies: Tuple[Zombie, ...] = field(default_factory=tuple)
    seeds: Tuple[Seed, ...] = field(default_factory=tuple)
    spawn: Optional[SpawnList] = None

    @property
    def alive_plants(self) -> Tuple[Plant, ...]:
        return tuple(p for p in self.plants if not p.dead)

    @property
    def alive_zombies(self) -> Tuple[Zombie, ...]:
        return tuple(z for z in self.zombies if not z.dead)
