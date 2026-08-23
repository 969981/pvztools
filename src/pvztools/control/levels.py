"""Level identification & post-level navigation (关卡识别与导航).

This module turns the raw ``LawnApp.game_mode`` / ``Board`` reads that
:mod:`pvztools.game` already exposes into a *classification* the automation
can branch on: is this a normal plant-able lawn level (so the
:class:`~pvztools.control.play.PlaySession` chooser -> lineup -> place flow
applies), a conveyor-belt level, a puzzle (I,Zombie / Vasebreaker), a
survival stage, a mini-game, the final boss, the Zen Garden or the Tree of
Wisdom?

Sources
-------
* **GameMode enum** -- re-plants ``ConstEnums.h`` ``enum GameMode`` (verbatim
  order reproduced in :class:`GameMode` below).  The numeric values are the
  enumerator positions 0..72; the decompiled GOTY binary reads them from
  ``LawnApp + 0x918`` (retail ``+0x7F8``) -- the same field
  :attr:`pvztools.game.Game.mode` returns.
* **IsFinalBossLevel** (decompiled ``LawnApp::IsFinalBossLevel`` @ 0x459220):
  boss when ``game_mode == 0x23`` (35 = ``GAMEMODE_CHALLENGE_FINAL_BOSS``)
  **or** ``game_mode == 0`` (adventure) **and** ``board.adventure_level ==
  0x32`` (50 = world 5, stage 10).
* **IsPuzzleMode** (decompiled ``LawnApp::IsPuzzleMode`` @ 0x458f40): puzzle
  when ``game_mode`` in ``[0x33..0x3c]`` (51..60 = Scary Potter / Vasebreaker)
  **or** ``[0x3d..0x46]`` (61..70 = I,Zombie).  Reproduced here as the
  ``SCARY_POTTER`` / ``I_ZOMBIE`` ranges.
* **Board.adventure_level** -- Board field at ``scene + 4`` (1051 ``0x5550``,
  GOTY ``0x5568``); this is exactly what :attr:`Game.level` decodes
  (``observe/snapshot.py`` builds ``Board.level`` from the profile's
  ``board.adventure_level`` offset).  So :func:`adventure_level` reads
  ``game.level``.
* **Board.scene** -- Board field (1051 ``0x554C``, GOTY ``0x5564``) holding a
  ``BackgroundType`` (0 day .. 5 boss/moon); :attr:`Game.scene` returns it.
  Mapped to the community two-letter code via
  :class:`pvztools.schema.enums.Scene`.
* **game_ui** -- ``LawnApp.game_ui`` (``GameScenes``: 1 menu, 2 chooser,
  3 playing, 5 award, 6 credits, 7 challenge screen); used by
  :func:`next_after_award`.

Reading is defensive: every ``Board`` read is wrapped so that classifying a
``Game`` that is *not in a level* (menu / chooser transitions) yields
``scene=None`` / ``adventure_level=None`` instead of raising ``NotInLevel``.
The functions are equally happy with the real :class:`~pvztools.game.Game`
facade or any duck-typed object exposing ``mode`` / ``scene`` / ``level``.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    "GameMode",
    "LevelKind",
    "LevelInfo",
    "classify",
    "is_adventure",
    "adventure_level",
    "next_after_award",
    "ADVENTURE_FINAL_BOSS_LEVEL",
    "CONVEYOR_ADVENTURE_LEVELS",
]


class GameMode(enum.IntEnum):
    """``LawnApp.game_mode`` -- re-plants ``ConstEnums.h`` ``enum GameMode``.

    Values are the enumerator positions (0-based, no gaps), exactly as the
    game stores them.  Reproduced verbatim so range checks below carry their
    own documentation.
    """

    ADVENTURE = 0
    SURVIVAL_NORMAL_STAGE_1 = 1
    SURVIVAL_NORMAL_STAGE_2 = 2
    SURVIVAL_NORMAL_STAGE_3 = 3
    SURVIVAL_NORMAL_STAGE_4 = 4
    SURVIVAL_NORMAL_STAGE_5 = 5
    SURVIVAL_HARD_STAGE_1 = 6
    SURVIVAL_HARD_STAGE_2 = 7
    SURVIVAL_HARD_STAGE_3 = 8
    SURVIVAL_HARD_STAGE_4 = 9
    SURVIVAL_HARD_STAGE_5 = 10
    SURVIVAL_ENDLESS_STAGE_1 = 11
    SURVIVAL_ENDLESS_STAGE_2 = 12
    SURVIVAL_ENDLESS_STAGE_3 = 13
    SURVIVAL_ENDLESS_STAGE_4 = 14
    SURVIVAL_ENDLESS_STAGE_5 = 15
    CHALLENGE_WAR_AND_PEAS = 16
    CHALLENGE_WALLNUT_BOWLING = 17
    CHALLENGE_SLOT_MACHINE = 18
    CHALLENGE_RAINING_SEEDS = 19
    CHALLENGE_BEGHOULED = 20
    CHALLENGE_INVISIGHOUL = 21
    CHALLENGE_SEEING_STARS = 22
    CHALLENGE_ZOMBIQUARIUM = 23
    CHALLENGE_BEGHOULED_TWIST = 24
    CHALLENGE_LITTLE_TROUBLE = 25
    CHALLENGE_PORTAL_COMBAT = 26
    CHALLENGE_COLUMN = 27
    CHALLENGE_BOBSLED_BONANZA = 28
    CHALLENGE_SPEED = 29
    CHALLENGE_WHACK_A_ZOMBIE = 30
    CHALLENGE_LAST_STAND = 31
    CHALLENGE_WAR_AND_PEAS_2 = 32
    CHALLENGE_WALLNUT_BOWLING_2 = 33
    CHALLENGE_POGO_PARTY = 34
    CHALLENGE_FINAL_BOSS = 35  # 0x23 -- IsFinalBossLevel
    CHALLENGE_ART_CHALLENGE_WALLNUT = 36
    CHALLENGE_SUNNY_DAY = 37
    CHALLENGE_RESODDED = 38
    CHALLENGE_BIG_TIME = 39
    CHALLENGE_ART_CHALLENGE_SUNFLOWER = 40
    CHALLENGE_AIR_RAID = 41
    CHALLENGE_ICE = 42
    CHALLENGE_ZEN_GARDEN = 43
    CHALLENGE_HIGH_GRAVITY = 44
    CHALLENGE_GRAVE_DANGER = 45
    CHALLENGE_SHOVEL = 46
    CHALLENGE_STORMY_NIGHT = 47
    CHALLENGE_BUNGEE_BLITZ = 48
    CHALLENGE_SQUIRREL = 49
    TREE_OF_WISDOM = 50
    SCARY_POTTER_1 = 51  # 0x33 -- IsPuzzleMode range start
    SCARY_POTTER_2 = 52
    SCARY_POTTER_3 = 53
    SCARY_POTTER_4 = 54
    SCARY_POTTER_5 = 55
    SCARY_POTTER_6 = 56
    SCARY_POTTER_7 = 57
    SCARY_POTTER_8 = 58
    SCARY_POTTER_9 = 59
    SCARY_POTTER_ENDLESS = 60  # 0x3c
    PUZZLE_I_ZOMBIE_1 = 61  # 0x3d
    PUZZLE_I_ZOMBIE_2 = 62
    PUZZLE_I_ZOMBIE_3 = 63
    PUZZLE_I_ZOMBIE_4 = 64
    PUZZLE_I_ZOMBIE_5 = 65
    PUZZLE_I_ZOMBIE_6 = 66
    PUZZLE_I_ZOMBIE_7 = 67
    PUZZLE_I_ZOMBIE_8 = 68
    PUZZLE_I_ZOMBIE_9 = 69
    PUZZLE_I_ZOMBIE_ENDLESS = 70  # 0x46 -- IsPuzzleMode range end
    UPSELL = 71
    INTRO = 72


#: Board.adventure_level of world 5 stage 10 (the Zomboss fight); decompiled
#: ``IsFinalBossLevel`` compares ``board.adventure_level == 0x32``.
ADVENTURE_FINAL_BOSS_LEVEL = 50

#: Adventure levels (mode 0) whose seed bank is a conveyor belt rather than a
#: chooser: the two Wall-nut Bowling levels 1-5 (=5) and 4-5 (=35).  Advisory
#: only -- a live conveyor signal (:func:`_conveyor_live`) takes precedence.
#: ``level = (world - 1) * 10 + stage``.
CONVEYOR_ADVENTURE_LEVELS = frozenset({5, 35})

#: Challenge/mini-game modes that use a conveyor belt (no free planting).
_CONVEYOR_MODES = frozenset({
    GameMode.CHALLENGE_WALLNUT_BOWLING,
    GameMode.CHALLENGE_WALLNUT_BOWLING_2,
    GameMode.CHALLENGE_RAINING_SEEDS,
    GameMode.CHALLENGE_BOBSLED_BONANZA,
    GameMode.CHALLENGE_POGO_PARTY,
    GameMode.CHALLENGE_AIR_RAID,
    GameMode.CHALLENGE_BUNGEE_BLITZ,
})

#: Mini-games that DO use the standard seed-chooser + free-planting flow
#: (so the PlaySession lineup path applies).  Kept deliberately small and
#: conservative -- every other challenge mode is treated as non-plantable so
#: the automation never places a lineup where it does not belong.
_PLANTABLE_MINIGAMES = frozenset({
    GameMode.CHALLENGE_COLUMN,      # Column Like You See 'Em
    GameMode.CHALLENGE_LAST_STAND,  # Last Stand
})


class LevelKind(enum.Enum):
    """Coarse classification the automation branches on."""

    ADVENTURE = "adventure"          # mode 0, normal plant-able lawn level
    CONVEYOR = "conveyor"            # conveyor-belt seed bank (no chooser)
    I_ZOMBIE = "i_zombie"            # I,Zombie puzzle
    VASEBREAKER = "vasebreaker"      # Scary Potter / Vasebreaker puzzle
    SURVIVAL = "survival"            # survival normal / hard / endless
    MINIGAME = "minigame"            # other mini-game
    WHACK = "whack"                  # Whack a Zombie
    SLOTS = "slots"                  # Slot Machine
    PUZZLE = "puzzle"                # Beghouled / Beghouled Twist (bejeweled)
    BOSS = "boss"                    # final Zomboss fight
    ZEN_GARDEN = "zen_garden"        # Zen Garden
    TREE_OF_WISDOM = "tree_of_wisdom"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LevelInfo:
    """Result of :func:`classify`.

    * ``kind`` -- the :class:`LevelKind`.
    * ``plantable`` -- True when the standard "pick a lineup and place plants"
      flow applies (ADVENTURE / SURVIVAL / BOSS / a plant-able mini-game).
    * ``has_chooser`` -- True when the level opens the seed chooser (``ui 2``)
      so seeds are selectable before it starts.  Conveyor / puzzle / preset
      levels never do.
    * ``mode`` -- the raw ``LawnApp.game_mode`` int (``None`` if unreadable).
    * ``scene`` -- the two-letter scene code (``"PE"`` ...) or ``None`` when
      not in a level / scene out of range.
    * ``adventure_level`` -- ``Board.adventure_level`` (1..50) or ``None``.
    * ``reason`` -- a short human string documenting the decision.
    """

    kind: LevelKind
    plantable: bool
    has_chooser: bool
    mode: Optional[int]
    scene: Optional[str]
    adventure_level: Optional[int]
    reason: str


# -- defensive reads -----------------------------------------------------------

def _read(game: Any, name: str) -> Optional[int]:
    """``getattr(game, name)`` as an int, or ``None`` on any failure.

    Real :class:`Game` reads of ``scene`` / ``level`` raise ``NotInLevel``
    off a board; a plain attribute on a synthetic game just returns.  A
    callable attribute is called (tolerates method-style fakes)."""
    try:
        val = getattr(game, name)
    except Exception:
        return None
    if callable(val):
        try:
            val = val()
        except Exception:
            return None
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _scene_code(scene: Optional[int]) -> Optional[str]:
    """``BackgroundType`` int -> community code (``"PE"`` ...) or ``None``."""
    if scene is None:
        return None
    try:
        from pvztools.schema.enums import Scene

        return Scene(int(scene)).code
    except (ValueError, KeyError):
        return None


def _conveyor_live(game: Any) -> Optional[bool]:
    """Read a live conveyor-belt signal if the object exposes one.

    No verified boolean exists on the :class:`Game` facade yet, so this is a
    duck-typed hook: an explicit ``conveyor`` / ``is_conveyor`` attribute (or
    a ``seed_bank`` object carrying one) overrides the mode/level heuristics.
    Returns ``None`` when nothing conclusive is present."""
    for attr in ("conveyor", "is_conveyor", "seed_bank_conveyor"):
        if hasattr(game, attr):
            try:
                val = getattr(game, attr)
            except Exception:
                continue
            if callable(val):
                try:
                    val = val()
                except Exception:
                    continue
            if val is not None:
                return bool(val)
    bank = getattr(game, "seed_bank", None)
    if bank is not None:
        for attr in ("conveyor", "is_conveyor"):
            if hasattr(bank, attr):
                try:
                    return bool(getattr(bank, attr))
                except Exception:
                    pass
    return None


# -- public helpers ------------------------------------------------------------

def is_adventure(game: Any) -> bool:
    """True when ``LawnApp.game_mode == GAMEMODE_ADVENTURE`` (0)."""
    return _read(game, "mode") == int(GameMode.ADVENTURE)


def adventure_level(game: Any) -> Optional[int]:
    """``Board.adventure_level`` (1..50), or ``None`` when not in a level.

    Read from :attr:`Game.level` -- ``observe/snapshot.py`` builds
    ``Board.level`` from the profile's ``board.adventure_level`` offset
    (1051 ``0x5550`` / GOTY ``0x5568``), so ``game.level`` *is* the adventure
    level number.  Meaningful only in adventure mode; other modes reuse the
    field for their own stage index, so callers should gate on
    :func:`is_adventure`."""
    return _read(game, "level")


def classify(game: Any) -> LevelInfo:
    """Classify the level ``game`` is currently on.

    Uses ``LawnApp.game_mode`` first, refines adventure mode with the
    ``Board.adventure_level`` (final boss / conveyor bowling) and a live
    conveyor signal when available, and documents every branch in
    ``LevelInfo.reason``.
    """
    mode = _read(game, "mode")
    scene = _scene_code(_read(game, "scene"))
    adv = _read(game, "level")

    def info(kind: LevelKind, plantable: bool, has_chooser: bool, reason: str) -> LevelInfo:
        return LevelInfo(kind=kind, plantable=plantable, has_chooser=has_chooser,
                         mode=mode, scene=scene, adventure_level=adv, reason=reason)

    if mode is None:
        return info(LevelKind.UNKNOWN, False, False, "game_mode 不可读")

    conv = _conveyor_live(game)

    # --- Adventure (mode 0) ---------------------------------------------------
    if mode == int(GameMode.ADVENTURE):
        if adv == ADVENTURE_FINAL_BOSS_LEVEL:
            return info(LevelKind.BOSS, True, True,
                        "冒险模式 5-10 僵王 (adventure_level==50, IsFinalBossLevel)")
        if conv is True or (conv is None and adv in CONVEYOR_ADVENTURE_LEVELS):
            why = "传送带信号" if conv is True else "坚果保龄球关卡 (adventure_level in %s)" % sorted(CONVEYOR_ADVENTURE_LEVELS)
            return info(LevelKind.CONVEYOR, True, False, "冒险模式传送带关卡(可直接种植): %s" % why)
        return info(LevelKind.ADVENTURE, True, True,
                    "冒险模式普通种植关卡 (game_mode==GAMEMODE_ADVENTURE)")

    # --- Survival -------------------------------------------------------------
    if int(GameMode.SURVIVAL_NORMAL_STAGE_1) <= mode <= int(GameMode.SURVIVAL_ENDLESS_STAGE_5):
        return info(LevelKind.SURVIVAL, True, True,
                    "生存模式 (game_mode %d in 1..15)" % mode)

    # --- Tree of Wisdom -------------------------------------------------------
    if mode == int(GameMode.TREE_OF_WISDOM):
        return info(LevelKind.TREE_OF_WISDOM, False, False, "智慧之树 (game_mode==50)")

    # --- Scary Potter / Vasebreaker (IsPuzzleMode 0x33..0x3c) -----------------
    if int(GameMode.SCARY_POTTER_1) <= mode <= int(GameMode.SCARY_POTTER_ENDLESS):
        return info(LevelKind.VASEBREAKER, False, False,
                    "砸罐子/Vasebreaker (game_mode %d in 51..60, IsPuzzleMode)" % mode)

    # --- I,Zombie (IsPuzzleMode 0x3d..0x46) -----------------------------------
    if int(GameMode.PUZZLE_I_ZOMBIE_1) <= mode <= int(GameMode.PUZZLE_I_ZOMBIE_ENDLESS):
        return info(LevelKind.I_ZOMBIE, False, False,
                    "I,Zombie (game_mode %d in 61..70, IsPuzzleMode)" % mode)

    # --- Challenges / mini-games (16..49) -------------------------------------
    if int(GameMode.CHALLENGE_WAR_AND_PEAS) <= mode <= int(GameMode.CHALLENGE_SQUIRREL):
        gm = GameMode(mode)
        if gm == GameMode.CHALLENGE_FINAL_BOSS:
            return info(LevelKind.BOSS, True, True, "最终boss小游戏 (game_mode==35)")
        if gm == GameMode.CHALLENGE_ZEN_GARDEN:
            return info(LevelKind.ZEN_GARDEN, False, False, "禅境花园 (game_mode==43)")
        if gm == GameMode.CHALLENGE_SLOT_MACHINE:
            return info(LevelKind.SLOTS, False, False, "老虎机 Slot Machine (game_mode==18)")
        if gm == GameMode.CHALLENGE_WHACK_A_ZOMBIE:
            return info(LevelKind.WHACK, False, False, "打地鼠 Whack a Zombie (game_mode==30)")
        if gm in (GameMode.CHALLENGE_BEGHOULED, GameMode.CHALLENGE_BEGHOULED_TWIST):
            return info(LevelKind.PUZZLE, False, False, "宝石迷阵 Beghouled (game_mode %d)" % mode)
        if conv is True or gm in _CONVEYOR_MODES:
            return info(LevelKind.CONVEYOR, True, False,
                        "传送带小游戏 %s (可直接种植, game_mode==%d)" % (gm.name, mode))
        if gm in _PLANTABLE_MINIGAMES:
            return info(LevelKind.MINIGAME, True, True,
                        "可种植小游戏 %s (game_mode==%d)" % (gm.name, mode))
        return info(LevelKind.MINIGAME, False, False,
                    "小游戏 %s (game_mode==%d, 种植流程未验证按不可种植处理)" % (gm.name, mode))

    # --- Upsell / Intro / anything past the table -----------------------------
    return info(LevelKind.UNKNOWN, False, False, "未知/非关卡 game_mode==%d" % mode)


# -- post-award navigation -----------------------------------------------------

#: Predicted next state labels returned by :func:`next_after_award`.
NEXT_MAIN_MENU = "main_menu"          # game_ui 1
NEXT_CHOOSER = "next_chooser"         # game_ui 2 (next adventure level's chooser)
NEXT_CHALLENGE_SCREEN = "challenge_screen"  # game_ui 7 (survival/challenge/puzzle page)
NEXT_CREDITS = "credits"              # game_ui 6 (5-10 completion rolls credits)


def next_after_award(game: Any, timeout: float = 10.0) -> str:
    """Predict the ui/state reached after the award screen (``ui 5``) is
    dismissed (:meth:`MainMenu.award_continue`).

    The prediction is driven by :func:`classify` of the level that produced
    the award:

    * **Adventure**, final boss (5-10) -> ``"credits"`` (``game_ui 6``); the
      run then returns to the main menu.
    * **Adventure**, any other level -> ``"next_chooser"``: the next level
      loads and opens its seed chooser (``game_ui 2``); early preset-seed
      levels skip straight to ``game_ui 3`` -- the PlaySession loop polls the
      ui either way.
    * **Survival / mini-game / puzzle / Vasebreaker / I,Zombie** ->
      ``"challenge_screen"`` (``game_ui 7`` -- the survival/challenge/puzzle
      selector the level was launched from).
    * **Tree of Wisdom / unknown** -> ``"main_menu"``.

    ``timeout`` is the recommended budget (seconds) for the caller's ui poll
    after ``award_continue``; the value returned here is the *expected*
    landing so the caller knows which ui to wait for.
    """
    info = classify(game)
    _ = timeout  # advisory poll budget for the caller; prediction is pure
    if info.kind == LevelKind.BOSS and info.mode == int(GameMode.ADVENTURE):
        return NEXT_CREDITS
    if info.mode == int(GameMode.ADVENTURE):
        return NEXT_CHOOSER
    if info.kind in (LevelKind.SURVIVAL, LevelKind.MINIGAME, LevelKind.CONVEYOR,
                     LevelKind.WHACK, LevelKind.SLOTS, LevelKind.PUZZLE,
                     LevelKind.VASEBREAKER, LevelKind.I_ZOMBIE, LevelKind.BOSS):
        return NEXT_CHALLENGE_SCREEN
    return NEXT_MAIN_MENU
