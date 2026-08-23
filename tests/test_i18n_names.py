import os

import pytest

from pvztools import i18n
from pvztools.i18n import _, get_language, set_language
from pvztools.names import (UnknownName, plant_name, resolve_plant, resolve_zombie,
                            set_name_style, zombie_name)
from pvztools.schema.enums import PLANT_EN, PLANT_ZH, ZOMBIE_EN, ZOMBIE_ZH, PlantType, ZombieType


@pytest.fixture(autouse=True)
def _reset_lang():
    yield
    set_language(None)
    set_name_style("official")


def test_official_tables_complete():
    assert set(PLANT_EN) == set(PlantType) and set(PLANT_ZH) == set(PlantType)
    assert set(ZOMBIE_EN) == set(ZombieType) and set(ZOMBIE_ZH) == set(ZombieType)
    assert PLANT_EN[PlantType.WALL_NUT] == "Wall-nut"
    assert ZOMBIE_EN[ZombieType.GIGA_GARGANTUAR] == "Giga-gargantuar"


@pytest.mark.parametrize("name, expected", [
    ("曾哥", PlantType.GLOOM_SHROOM), ("曾", PlantType.GLOOM_SHROOM), ("忧郁菇", PlantType.GLOOM_SHROOM),
    ("gloom", PlantType.GLOOM_SHROOM), ("Gloom-shroom", PlantType.GLOOM_SHROOM),
    ("gloom shroom", PlantType.GLOOM_SHROOM), ("GLOOM_SHROOM", PlantType.GLOOM_SHROOM),
    ("春哥", PlantType.COB_CANNON), ("炮", PlantType.COB_CANNON), ("玉米炮", PlantType.COB_CANNON),
    ("Cob Cannon", PlantType.COB_CANNON), ("cob", PlantType.COB_CANNON), (" COB  CANNON ", PlantType.COB_CANNON),
    ("冰瓜", PlantType.WINTER_MELON), ("winter", PlantType.WINTER_MELON),
    ("双子", PlantType.TWIN_SUNFLOWER), ("twin", PlantType.TWIN_SUNFLOWER),
    ("花", PlantType.SUNFLOWER), ("坚果", PlantType.WALL_NUT), ("高坚果", PlantType.TALL_NUT),
    ("南瓜", PlantType.PUMPKIN), ("冰", PlantType.ICE_SHROOM), ("核", PlantType.DOOM_SHROOM),
    ("樱桃", PlantType.CHERRY_BOMB), ("辣椒", PlantType.JALAPENO), ("咖啡", PlantType.COFFEE_BEAN),
    ("磁铁", PlantType.MAGNET_SHROOM), ("大喷", PlantType.FUME_SHROOM), ("小喷", PlantType.PUFF_SHROOM),
    ("胆小", PlantType.SCAREDY_SHROOM), ("地刺王", PlantType.SPIKEROCK),
    (42, PlantType.GLOOM_SHROOM), ("42", PlantType.GLOOM_SHROOM), (PlantType.CATTAIL, PlantType.CATTAIL),
])
def test_resolve_plant(name, expected):
    assert resolve_plant(name) is expected


@pytest.mark.parametrize("name, expected", [
    ("红眼", ZombieType.GIGA_GARGANTUAR), ("红", ZombieType.GIGA_GARGANTUAR), ("giga", ZombieType.GIGA_GARGANTUAR),
    ("Giga-gargantuar", ZombieType.GIGA_GARGANTUAR), ("白眼", ZombieType.GARGANTUAR),
    ("丑", ZombieType.JACK_IN_THE_BOX), ("小丑僵尸", ZombieType.JACK_IN_THE_BOX),
    ("舞王", ZombieType.DANCING), ("海豚", ZombieType.DOLPHIN_RIDER), ("冰车", ZombieType.ZOMBONI),
    ("矿工", ZombieType.DIGGER), ("气球", ZombieType.BALLOON), ("篮", ZombieType.CATAPULT),
    ("梯子", ZombieType.LADDER), ("偷", ZombieType.BUNGEE), ("撑杆", ZombieType.POLE_VAULTING),
    ("铁桶", ZombieType.BUCKETHEAD), ("路障", ZombieType.CONEHEAD), ("报纸", ZombieType.NEWSPAPER),
    ("铁门", ZombieType.SCREEN_DOOR), ("橄榄球", ZombieType.FOOTBALL),
    ("Conehead Zombie", ZombieType.CONEHEAD), ("conehead", ZombieType.CONEHEAD),
    ("Dr. Zomboss", ZombieType.ZOMBOSS), (32, ZombieType.GIGA_GARGANTUAR),
])
def test_resolve_zombie(name, expected):
    assert resolve_zombie(name) is expected


def test_resolve_errors():
    for bad in ("不存在的植物", 49, -1, True, ""):
        with pytest.raises(UnknownName):
            resolve_plant(bad)
    with pytest.raises(UnknownName):
        resolve_zombie("nope")
    set_language("en")
    with pytest.raises(UnknownName, match="unknown plant"):
        resolve_plant("zzz")


def test_plant_name_styles():
    assert plant_name(PlantType.GLOOM_SHROOM, "en") == "Gloom-shroom"
    assert plant_name(PlantType.GLOOM_SHROOM, "zh") == "忧郁菇"
    assert plant_name(PlantType.GLOOM_SHROOM, "zh", style="community") == "曾哥"
    assert plant_name(PlantType.COB_CANNON, "zh", style="community") == "春哥"
    assert plant_name(PlantType.GLOOM_SHROOM, "en", style="community") == "gloom"
    # community falls back to the official name when no nickname exists
    assert plant_name(PlantType.CHOMPER, "en", style="community") == "Chomper"
    assert plant_name("曾哥", "en") == "Gloom-shroom"
    assert zombie_name(ZombieType.GIGA_GARGANTUAR, "en") == "Giga-gargantuar"
    assert zombie_name(32, "zh", style="community") == "红眼"
    set_name_style("community")
    assert plant_name(PlantType.GLOOM_SHROOM, "zh") == "曾哥"
    with pytest.raises(ValueError):
        set_name_style("weird")


def test_name_follows_i18n_language():
    set_language("zh")
    assert plant_name(PlantType.GLOOM_SHROOM) == "忧郁菇"
    set_language("en")
    assert plant_name(PlantType.GLOOM_SHROOM) == "Gloom-shroom"


def test_i18n_switching(monkeypatch):
    set_language("zh")
    assert get_language() == "zh"
    assert _("hint_write").startswith("请用")
    set_language("en-US")
    assert get_language() == "en"
    assert _("hint_write").startswith("re-attach")
    assert _("not_in_level", ui="X") == "enter a level first (currently at X)"
    assert _("no_such_key") == "no_such_key"
    assert _("no_such_key {x}", x=1) == "no_such_key 1"
    with pytest.raises(ValueError):
        set_language("fr")
    # env var drives auto-detection
    set_language(None)
    monkeypatch.setenv("PVZTOOLS_LANG", "zh_CN")
    assert get_language() == "zh"
    monkeypatch.setenv("PVZTOOLS_LANG", "en")
    assert get_language() == "en"
    monkeypatch.delenv("PVZTOOLS_LANG")
    assert get_language() in i18n.LANGUAGES


def test_actions_resolve_plant_delegates():
    from pvztools.control.actions import ActionError, resolve_plant as act_resolve
    assert act_resolve("曾哥") == 42 and act_resolve("春哥") == 47 and act_resolve("gloom") == 42
    with pytest.raises(ActionError):
        act_resolve("模仿者")
    with pytest.raises(ActionError):
        act_resolve("不存在的植物")


def test_game_hints_are_translated():
    from pvztools import game
    set_language("en")
    assert "attach(write=True)" in _(game._HINT_WRITE)
