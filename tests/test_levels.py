"""Level classification (control.levels) + per-scene defaults (control.lineups_default)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from pvztools.control import levels
from pvztools.control.levels import GameMode, LevelKind
from pvztools.control import lineups_default as ld


class FakeGame:
    """Duck-typed stand-in for pvztools.game.Game.

    ``mode`` is LawnApp.game_mode; ``scene`` is Board.scene (BackgroundType
    int) and ``level`` is Board.adventure_level.  A missing attribute (set to
    the sentinel ``RAISE``) raises on access, mimicking NotInLevel off-board.
    """

    RAISE = object()

    def __init__(self, mode=None, scene=None, level=None, conveyor=None):
        self._mode = mode
        self._scene = scene
        self._level = level
        if conveyor is not None:
            self.conveyor = conveyor

    def _get(self, v):
        if v is FakeGame.RAISE:
            raise RuntimeError("not in level")
        return v

    @property
    def mode(self):
        return self._get(self._mode)

    @property
    def scene(self):
        return self._get(self._scene)

    @property
    def level(self):
        return self._get(self._level)


# -- levels.classify -----------------------------------------------------------

def test_adventure_mode_0_is_plantable():
    g = FakeGame(mode=0, scene=2, level=12)  # scene 2 == PE (pool)
    info = levels.classify(g)
    assert info.kind is LevelKind.ADVENTURE
    assert info.plantable is True
    assert info.has_chooser is True
    assert info.mode == 0
    assert info.scene == "PE"
    assert info.adventure_level == 12
    assert levels.is_adventure(g) is True
    assert levels.adventure_level(g) == 12


def test_adventure_final_boss_is_boss():
    g = FakeGame(mode=0, scene=4, level=levels.ADVENTURE_FINAL_BOSS_LEVEL)
    info = levels.classify(g)
    assert info.kind is LevelKind.BOSS
    assert info.plantable is True and info.has_chooser is True


def test_conveyor_minigame_not_plantable_no_chooser():
    # Wall-nut Bowling mini-game (game_mode 17) -> conveyor belt.
    g = FakeGame(mode=int(GameMode.CHALLENGE_WALLNUT_BOWLING), scene=0, level=0)
    info = levels.classify(g)
    assert info.kind is LevelKind.CONVEYOR
    assert info.plantable is True   # try-then-stop: place plants directly
    assert info.has_chooser is False
    assert levels.is_adventure(g) is False


def test_conveyor_adventure_bowling_level():
    # Adventure 1-5 (level 5) is Wall-nut Bowling on a conveyor belt.
    g = FakeGame(mode=0, scene=0, level=5)
    info = levels.classify(g)
    assert info.kind is LevelKind.CONVEYOR
    # try-then-stop: conveyor levels are plantable (we PutPlant directly,
    # bypassing the belt); they just have no seed chooser.
    assert info.plantable is True and info.has_chooser is False


def test_live_conveyor_flag_overrides_adventure():
    g = FakeGame(mode=0, scene=0, level=3, conveyor=True)
    assert levels.classify(g).kind is LevelKind.CONVEYOR


def test_survival_is_plantable_with_chooser():
    g = FakeGame(mode=int(GameMode.SURVIVAL_ENDLESS_STAGE_1), scene=2, level=0)
    info = levels.classify(g)
    assert info.kind is LevelKind.SURVIVAL
    assert info.plantable is True and info.has_chooser is True


def test_vasebreaker_and_izombie_are_puzzles():
    vb = levels.classify(FakeGame(mode=int(GameMode.SCARY_POTTER_1)))
    iz = levels.classify(FakeGame(mode=int(GameMode.PUZZLE_I_ZOMBIE_1)))
    assert vb.kind is LevelKind.VASEBREAKER and not vb.plantable and not vb.has_chooser
    assert iz.kind is LevelKind.I_ZOMBIE and not iz.plantable and not iz.has_chooser


def test_special_minigame_kinds():
    assert levels.classify(FakeGame(mode=int(GameMode.CHALLENGE_SLOT_MACHINE))).kind is LevelKind.SLOTS
    assert levels.classify(FakeGame(mode=int(GameMode.CHALLENGE_WHACK_A_ZOMBIE))).kind is LevelKind.WHACK
    assert levels.classify(FakeGame(mode=int(GameMode.CHALLENGE_BEGHOULED))).kind is LevelKind.PUZZLE
    assert levels.classify(FakeGame(mode=int(GameMode.CHALLENGE_ZEN_GARDEN))).kind is LevelKind.ZEN_GARDEN
    assert levels.classify(FakeGame(mode=int(GameMode.TREE_OF_WISDOM))).kind is LevelKind.TREE_OF_WISDOM


def test_plantable_minigame_last_stand():
    info = levels.classify(FakeGame(mode=int(GameMode.CHALLENGE_LAST_STAND), scene=2))
    assert info.kind is LevelKind.MINIGAME
    assert info.plantable is True and info.has_chooser is True


def test_unknown_when_mode_unreadable():
    g = FakeGame(mode=FakeGame.RAISE)
    info = levels.classify(g)
    assert info.kind is LevelKind.UNKNOWN
    assert info.plantable is False
    assert info.mode is None


def test_scene_none_off_board():
    # In adventure mode but scene/level not readable (chooser transition).
    g = FakeGame(mode=0, scene=FakeGame.RAISE, level=FakeGame.RAISE)
    info = levels.classify(g)
    assert info.kind is LevelKind.ADVENTURE
    assert info.scene is None
    assert info.adventure_level is None


# -- levels.next_after_award ---------------------------------------------------

def test_next_after_award_adventure_chooser():
    g = FakeGame(mode=0, scene=2, level=12)
    assert levels.next_after_award(g, timeout=5.0) == levels.NEXT_CHOOSER


def test_next_after_award_adventure_boss_credits():
    g = FakeGame(mode=0, scene=4, level=levels.ADVENTURE_FINAL_BOSS_LEVEL)
    assert levels.next_after_award(g) == levels.NEXT_CREDITS


def test_next_after_award_minigame_challenge_screen():
    g = FakeGame(mode=int(GameMode.CHALLENGE_WHACK_A_ZOMBIE))
    assert levels.next_after_award(g) == levels.NEXT_CHALLENGE_SCREEN


def test_next_after_award_survival_challenge_screen():
    g = FakeGame(mode=int(GameMode.SURVIVAL_ENDLESS_STAGE_1), scene=2)
    assert levels.next_after_award(g) == levels.NEXT_CHALLENGE_SCREEN


# -- lineups_default -----------------------------------------------------------

def test_default_lineup_per_scene():
    assert ld.default_lineup("PE") == "3C八炮"
    assert ld.default_lineup("DE") == "神之八炮"
    assert ld.default_lineup("NE") == "美学八炮"
    assert ld.default_lineup("FE") == "经典八炮"
    assert ld.default_lineup("RE") == "方块八炮"
    assert ld.default_lineup("ME") == "对称八炮"
    # case-insensitive
    assert ld.default_lineup("pe") == "3C八炮"


def test_default_lineup_unknown_scene():
    with pytest.raises(KeyError):
        ld.default_lineup("XE")


def test_validate_defaults_all_primaries_decode_8_cobs():
    result = ld.validate_defaults()
    assert set(result) == set(ld.SCENES)
    assert all(result.values()), result


def test_load_default_returns_named_8_cob_lineup():
    for scene, name in ld.DEFAULT_LINEUPS.items():
        lu = ld.load_default(scene)
        assert lu.name == name
        assert len(lu.cobs()) == 8


def test_load_default_fallback_chain_resolves():
    # Every name in every scene's fallback chain must resolve for that scene.
    for scene, chain in ld.FALLBACKS.items():
        for name in chain:
            lu = ld._resolve(scene, name)
            assert lu is not None, (scene, name)
            assert len(lu.cobs()) == 8, (scene, name)


def test_cob_pairs_four_pairs_for_3c():
    lu = ld.load_default("PE")  # 3C八炮
    pairs = ld.cob_pairs(lu)
    assert len(pairs) == 4
    # every pair is two distinct (row, col) cells, sorted column-major
    flat = [c for p in pairs for c in p]
    assert len(set(flat)) == 8
    cols = [c[1] for c in flat]
    assert cols == sorted(cols)


def test_cob_pairs_all_scenes_four_pairs():
    for scene in ld.SCENES:
        assert len(ld.cob_pairs(ld.load_default(scene))) == 4


def test_cob_pairs_odd_raises():
    class _L:
        def cobs(self):
            return [(1, 1), (2, 2), (3, 3)]
    with pytest.raises(ValueError):
        ld.cob_pairs(_L())


def test_target_col_default_and_override():
    for scene in ld.SCENES:
        assert ld.target_col(scene) == 8
    ld.TARGET_COL_OVERRIDES["PE"] = 7
    try:
        assert ld.target_col("PE") == 7
        assert ld.target_col("DE") == 8
    finally:
        ld.TARGET_COL_OVERRIDES.clear()
