"""Authoritative timing constants (unit: cs = centisecond = 1 game tick).

Sources (see docs/DESIGN.md §5.2 and docs/track-format.md §1):
- cob CD / flight: AvZ ``dsl`` GetEffectOffset (373 land / 378 pool water rows /
  387 roof), PvZ-Emulator cob state machine (3475 total CD)
- ice / doom activation: AvZ (night 100, day 299 incl. coffee bean), imitater +320
- cherry / jalapeno: AvZ DSL effect offset -100 (i.e. effect 100cs after plant);
  squash: -182 (AvZ ``shorthand_240713.h`` ``W(row,col)``)
- card CDs: PvZ-Emulator ``plant::CD_TABLE``
- wave mechanics: PvZ-Emulator ``system/spawn.cpp`` + pvzscripts extra.py
- 运算量: 白菜《pvz轨道理论1》
Values marked TODO_VERIFY are community numbers not yet cross-checked in code.
"""
from __future__ import annotations

from typing import Optional

from .enums import PlantType, Scene

__all__ = [
    "COB_CD",
    "COB_FLIGHT_LAND",
    "COB_FLIGHT_WATER",
    "COB_FLIGHT_ROOF",
    "cob_flight",
    "ICE_ACTIVATE_NIGHT",
    "ICE_ACTIVATE_DAY",
    "IMITATER_EXTRA",
    "ASH_EFFECT",
    "SQUASH_EFFECT",
    "FODDER_CD",
    "ICE_SLOW_DURATION",
    "CARD_CD",
    "card_cd",
    "WAVE_MIN_LEN",
    "WAVE_NATURAL_MIN",
    "WAVE_NATURAL_MAX",
    "HUGE_WAVE_COUNTDOWN",
    "HUGE_WAVE_RED_TEXT",
    "FINAL_WAVE_COUNTDOWN",
    "FIRST_WAVE_COUNTDOWN",
    "AL_PER_REFRESH",
    "effect_offset",
    "seconds_or_cs",
]

# --- cob cannon -------------------------------------------------------------
COB_CD = 3475
COB_FLIGHT_LAND = 373
COB_FLIGHT_WATER = 378  # pool/fog rows 3-4 (AvZ)
COB_FLIGHT_ROOF = 387  # AvZ; pvzscripts uses per-column 359..373 TODO_VERIFY
COB_CARD_CD = 5000


def cob_flight(scene: Scene, row: Optional[int] = None) -> int:
    """Flight time from firing to landing, by landing row/scene."""
    if scene in (Scene.ROOF, Scene.MOON):
        return COB_FLIGHT_ROOF
    if row is not None and row in scene.water_rows:
        return COB_FLIGHT_WATER
    return COB_FLIGHT_LAND


# --- cards ------------------------------------------------------------------
ICE_ACTIVATE_NIGHT = 100  # mushroom awake: plant -> effect
ICE_ACTIVATE_DAY = 299  # plant + coffee bean
IMITATER_EXTRA = 320  # imitater card: extra delay before the real card appears
ASH_EFFECT = 100  # cherry / jalapeno: plant -> explode
SQUASH_EFFECT = 182  # squash: plant -> land (AvZ ``W(row,col)`` = At(-182_cs))
ICE_SLOW_DURATION = 2000  # whole-field slow (emulator: max 2000)
ICE_FREEZE_MIN, ICE_FREEZE_MAX = 400, 600  # unslowed zombie freeze 400+rand(201)

CARD_CD = {
    # 5000: ash & big mushrooms & upgrades
    PlantType.CHERRY_BOMB: 5000,
    PlantType.JALAPENO: 5000,
    PlantType.ICE_SHROOM: 5000,
    PlantType.DOOM_SHROOM: 5000,
    PlantType.COB_CANNON: 5000,
    PlantType.WINTER_MELON: 5000,
    PlantType.TWIN_SUNFLOWER: 5000,
    PlantType.GLOOM_SHROOM: 5000,
    PlantType.CATTAIL: 5000,
    PlantType.GOLD_MAGNET: 5000,
    PlantType.SPIKEROCK: 5000,
    PlantType.GATLING_PEA: 5000,
    PlantType.IMITATER: 5000,
    # 3000
    PlantType.SQUASH: 3000,
    PlantType.WALL_NUT: 3000,
    PlantType.TALL_NUT: 3000,
    PlantType.PUMPKIN: 3000,
    PlantType.POTATO_MINE: 3000,
    PlantType.TANGLE_KELP: 3000,
    PlantType.GRAVE_BUSTER: 3000,
    PlantType.HYPNO_SHROOM: 3000,
    PlantType.MAGNET_SHROOM: 3000,
    PlantType.CHOMPER: 3000,
    PlantType.BLOVER: 3000,
    PlantType.SPIKEWEED: 3000,
    PlantType.PLANTERN: 3000,
    PlantType.UMBRELLA_LEAF: 3000,
    PlantType.GARLIC: 3000,
    PlantType.STARFRUIT: 3000,
    PlantType.TORCHWOOD: 3000,
    PlantType.SEA_SHROOM: 3000,
    PlantType.FUME_SHROOM: 3000,
    PlantType.SCAREDY_SHROOM: 3000,
    PlantType.CABBAGE_PULT: 3000,
    PlantType.KERNEL_PULT: 3000,
    PlantType.MELON_PULT: 3000,
    PlantType.THREEPEATER: 3000,
    PlantType.SPLIT_PEA: 3000,
    PlantType.REPEATER: 3000,
    PlantType.SNOW_PEA: 3000,
    PlantType.CACTUS: 3000,
    PlantType.MARIGOLD: 3000,
    PlantType.COFFEE_BEAN: 3000,
}
# 750: cheap fodder (AvZ comment says 751; 1-tick difference TODO_VERIFY)
FODDER_CD = 750
for _p in (
    PlantType.PEASHOOTER,
    PlantType.SUNFLOWER,
    PlantType.PUFF_SHROOM,
    PlantType.SUN_SHROOM,
    PlantType.LILY_PAD,
    PlantType.FLOWER_POT,
):
    CARD_CD[_p] = FODDER_CD


def card_cd(plant: PlantType) -> int:
    return CARD_CD.get(plant, 3000)


# --- waves ------------------------------------------------------------------
WAVE_MIN_LEN = 601  # activation earliest at 401 + 200 countdown
WAVE_NATURAL_MIN = 2500  # next_wave = rand(600) + 2500 when not activated
WAVE_NATURAL_MAX = 3099
HUGE_WAVE_COUNTDOWN = 4500  # w9 / w19 countdown after refresh
HUGE_WAVE_RED_TEXT = 750  # "A huge wave..." fade before w10 / w20
FINAL_WAVE_COUNTDOWN = 5500  # w20
FIRST_WAVE_COUNTDOWN = 600  # level start; first wave earliest -599
HUGE_WAVE_MAX_LEN = HUGE_WAVE_COUNTDOWN + HUGE_WAVE_RED_TEXT  # 5250 (AvZ seml export uses 5245)

AL_PER_REFRESH = 2  # 白菜: 2 al = 1 re


def is_huge_wave(wave: int) -> bool:
    return wave in (10, 20)


def is_closing_wave(wave: int) -> bool:
    return wave in (9, 19, 20)


# --- effect <-> operation axis ---------------------------------------------
def effect_offset(
    kind: str,
    scene: Scene,
    row: Optional[int] = None,
    imitater: bool = False,
    variant: Optional[str] = None,
) -> int:
    """How many cs *before* the effect time the operation must be issued.

    ``operation_cs = effect_cs - effect_offset(...)``.
    kind: "cob" | "ice" | "doom" | "ash" | "fodder"; ``variant`` only
    matters for ash (``"squash"`` lands 182cs after planting, cherry /
    jalapeno explode after 100cs).
    """
    if kind == "cob":
        return cob_flight(scene, row)
    if kind in ("ice", "doom"):
        base = ICE_ACTIVATE_DAY if scene.is_day else ICE_ACTIVATE_NIGHT
        return base + (IMITATER_EXTRA if imitater else 0)
    if kind == "ash":
        base = SQUASH_EFFECT if variant == "squash" else ASH_EFFECT
        return base + (IMITATER_EXTRA if imitater else 0)
    return 0


def seconds_or_cs(value: float) -> int:
    """Community length convention: <=100 means seconds, else cs."""
    if value <= 100:
        return int(round(value * 100))
    return int(round(value))
