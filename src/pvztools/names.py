"""Plant / zombie name resolution and display (zh / en, official / community).

* :func:`resolve_plant` / :func:`resolve_zombie` accept an int, the enum,
  an enum member name (``GLOOM_SHROOM``), the official Chinese name
  (``忧郁菇``), the official English name (``Gloom-shroom``), community
  nicknames in either language (``曾哥``, ``gloom``) and -- for zombies --
  the AvZ single character (``红``).  Matching is case / whitespace /
  ``-`` / ``_`` insensitive.
* :func:`plant_name` / :func:`zombie_name` render a type in a language
  (``zh`` / ``en``, default: :func:`pvztools.i18n.get_language`) and style
  (``official`` / ``community``; the community style returns the primary
  nickname and falls back to the official name).

Community names follow 植吧 / AvZ / lonelystar usage.  The first alias in
each ``COMMUNITY_ALIASES`` tuple is the primary nickname.
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple, Union

from pvztools.i18n import _, get_language, normalize_lang
from pvztools.schema.enums import (PLANT_EN, PLANT_ZH, ZOMBIE_CHARS, ZOMBIE_EN, ZOMBIE_ZH,
                                   PlantType, ZombieType)

__all__ = [
    "COMMUNITY_ALIASES", "ZOMBIE_COMMUNITY_ALIASES", "PLANT_EN_ALIASES", "ZOMBIE_EN_ALIASES",
    "resolve_plant", "resolve_zombie", "plant_name", "zombie_name",
    "set_name_style", "get_name_style", "UnknownName", "NAME_STYLES",
]

NAME_STYLES = ("official", "community")


class UnknownName(ValueError):
    """The name / id could not be mapped to a plant or zombie."""


#: Chinese community nicknames, primary first.
COMMUNITY_ALIASES: Dict[PlantType, Tuple[str, ...]] = {
    PlantType.PEASHOOTER: ("豌豆",),
    PlantType.SUNFLOWER: ("花", "小向", "向日葵"),
    PlantType.CHERRY_BOMB: ("樱桃",),
    PlantType.WALL_NUT: ("坚果", "坚果墙", "小坚果"),
    PlantType.POTATO_MINE: ("土豆", "地雷"),
    PlantType.SNOW_PEA: ("冰豆", "寒冰豌豆"),
    PlantType.CHOMPER: ("大嘴",),
    PlantType.REPEATER: ("双发",),
    PlantType.PUFF_SHROOM: ("小喷",),
    PlantType.SUN_SHROOM: ("阳光菇", "阳菇"),
    PlantType.FUME_SHROOM: ("大喷",),
    PlantType.GRAVE_BUSTER: ("墓碑吞噬", "吞噬者"),
    PlantType.HYPNO_SHROOM: ("魅惑", "魅惑菇"),
    PlantType.SCAREDY_SHROOM: ("胆小", "胆小菇"),
    PlantType.ICE_SHROOM: ("冰", "冰菇"),
    PlantType.DOOM_SHROOM: ("核", "核武", "核菇"),
    PlantType.LILY_PAD: ("荷叶", "睡莲"),
    PlantType.SQUASH: ("窝瓜", "倭瓜"),
    PlantType.THREEPEATER: ("三线", "三发"),
    PlantType.TANGLE_KELP: ("海草", "水草"),
    PlantType.JALAPENO: ("辣椒",),
    PlantType.SPIKEWEED: ("地刺",),
    PlantType.TORCHWOOD: ("火炬", "树桩"),
    PlantType.TALL_NUT: ("高坚果",),
    PlantType.SEA_SHROOM: ("水兵", "水兵菇"),
    PlantType.PLANTERN: ("路灯", "灯"),
    PlantType.CACTUS: ("仙人掌",),
    PlantType.BLOVER: ("三叶草", "叶草"),
    PlantType.SPLIT_PEA: ("裂荚",),
    PlantType.STARFRUIT: ("杨桃", "五角星", "星星"),
    PlantType.PUMPKIN: ("南瓜",),
    PlantType.MAGNET_SHROOM: ("磁铁", "磁力菇"),
    PlantType.CABBAGE_PULT: ("卷心菜",),
    PlantType.FLOWER_POT: ("花盆",),
    PlantType.KERNEL_PULT: ("玉米", "玉米投手"),
    PlantType.COFFEE_BEAN: ("咖啡",),
    PlantType.GARLIC: ("大蒜",),
    PlantType.UMBRELLA_LEAF: ("伞", "叶子伞", "保护伞"),
    PlantType.MARIGOLD: ("金盏花",),
    PlantType.MELON_PULT: ("西瓜",),
    PlantType.GATLING_PEA: ("机枪",),
    PlantType.TWIN_SUNFLOWER: ("双子",),
    PlantType.GLOOM_SHROOM: ("曾哥", "曾", "忧郁"),
    PlantType.CATTAIL: ("香蒲", "猫"),
    PlantType.WINTER_MELON: ("冰瓜", "冰西瓜", "寒冰西瓜"),
    PlantType.GOLD_MAGNET: ("吸金", "吸金磁"),
    PlantType.SPIKEROCK: ("地刺王",),
    PlantType.COB_CANNON: ("春哥", "炮", "玉米炮", "加农炮"),
    PlantType.IMITATER: ("模仿", "模仿者"),
}

#: English community nicknames, primary first.
PLANT_EN_ALIASES: Dict[PlantType, Tuple[str, ...]] = {
    PlantType.PEASHOOTER: ("pea",),
    PlantType.SUNFLOWER: ("flower",),
    PlantType.CHERRY_BOMB: ("cherry",),
    PlantType.WALL_NUT: ("wallnut", "nut"),
    PlantType.POTATO_MINE: ("mine", "potato"),
    PlantType.SNOW_PEA: ("snow",),
    PlantType.PUFF_SHROOM: ("puff",),
    PlantType.SUN_SHROOM: ("sunshroom",),
    PlantType.FUME_SHROOM: ("fume",),
    PlantType.GRAVE_BUSTER: ("grave",),
    PlantType.HYPNO_SHROOM: ("hypno",),
    PlantType.SCAREDY_SHROOM: ("scaredy",),
    PlantType.ICE_SHROOM: ("ice",),
    PlantType.DOOM_SHROOM: ("doom", "nuke"),
    PlantType.LILY_PAD: ("lily", "pad"),
    PlantType.TANGLE_KELP: ("kelp",),
    PlantType.JALAPENO: ("jala", "pepper"),
    PlantType.TORCHWOOD: ("torch",),
    PlantType.TALL_NUT: ("tallnut",),
    PlantType.SEA_SHROOM: ("sea",),
    PlantType.SPLIT_PEA: ("split",),
    PlantType.STARFRUIT: ("star",),
    PlantType.MAGNET_SHROOM: ("magnet",),
    PlantType.CABBAGE_PULT: ("cabbage",),
    PlantType.FLOWER_POT: ("pot",),
    PlantType.KERNEL_PULT: ("kernel",),
    PlantType.COFFEE_BEAN: ("coffee",),
    PlantType.UMBRELLA_LEAF: ("umbrella",),
    PlantType.MELON_PULT: ("melon",),
    PlantType.GATLING_PEA: ("gatling",),
    PlantType.TWIN_SUNFLOWER: ("twin",),
    PlantType.GLOOM_SHROOM: ("gloom",),
    PlantType.WINTER_MELON: ("winter",),
    PlantType.GOLD_MAGNET: ("gold",),
    PlantType.COB_CANNON: ("cob", "cannon"),
    PlantType.IMITATER: ("imitator",),
}

ZOMBIE_COMMUNITY_ALIASES: Dict[ZombieType, Tuple[str, ...]] = {
    ZombieType.ZOMBIE: ("普僵", "普通"),
    ZombieType.FLAG: ("旗帜",),
    ZombieType.CONEHEAD: ("路障",),
    ZombieType.POLE_VAULTING: ("撑杆",),
    ZombieType.BUCKETHEAD: ("铁桶",),
    ZombieType.NEWSPAPER: ("报纸", "读报"),
    ZombieType.SCREEN_DOOR: ("铁门",),
    ZombieType.FOOTBALL: ("橄榄球", "橄榄"),
    ZombieType.DANCING: ("舞王",),
    ZombieType.BACKUP_DANCER: ("伴舞",),
    ZombieType.DUCKY_TUBE: ("鸭子",),
    ZombieType.SNORKEL: ("潜水",),
    ZombieType.ZOMBONI: ("冰车",),
    ZombieType.BOBSLED: ("雪橇",),
    ZombieType.DOLPHIN_RIDER: ("海豚",),
    ZombieType.JACK_IN_THE_BOX: ("小丑", "丑"),
    ZombieType.BALLOON: ("气球",),
    ZombieType.DIGGER: ("矿工",),
    ZombieType.POGO: ("跳跳",),
    ZombieType.YETI: ("雪人",),
    ZombieType.BUNGEE: ("蹦极", "偷"),
    ZombieType.LADDER: ("梯子",),
    ZombieType.CATAPULT: ("投篮", "篮"),
    ZombieType.GARGANTUAR: ("白眼", "巨人"),
    ZombieType.IMP: ("小鬼",),
    ZombieType.ZOMBOSS: ("僵王",),
    ZombieType.PEASHOOTER_ZOMBIE: ("豌豆僵",),
    ZombieType.WALL_NUT_ZOMBIE: ("坚果僵",),
    ZombieType.JALAPENO_ZOMBIE: ("辣椒僵",),
    ZombieType.GATLING_PEA_ZOMBIE: ("机枪僵",),
    ZombieType.SQUASH_ZOMBIE: ("窝瓜僵",),
    ZombieType.TALL_NUT_ZOMBIE: ("高坚果僵",),
    ZombieType.GIGA_GARGANTUAR: ("红眼", "红"),
}

ZOMBIE_EN_ALIASES: Dict[ZombieType, Tuple[str, ...]] = {
    ZombieType.ZOMBIE: ("normal", "regular", "basic"),
    ZombieType.FLAG: ("flag",),
    ZombieType.CONEHEAD: ("cone",),
    ZombieType.POLE_VAULTING: ("pole", "vaulter"),
    ZombieType.BUCKETHEAD: ("bucket",),
    ZombieType.NEWSPAPER: ("newspaper", "paper"),
    ZombieType.SCREEN_DOOR: ("screendoor", "door"),
    ZombieType.FOOTBALL: ("football",),
    ZombieType.DANCING: ("dancer", "mj"),
    ZombieType.BACKUP_DANCER: ("backup",),
    ZombieType.DUCKY_TUBE: ("ducky", "duck"),
    ZombieType.SNORKEL: ("snorkel",),
    ZombieType.ZOMBONI: ("zomboni",),
    ZombieType.BOBSLED: ("bobsled",),
    ZombieType.DOLPHIN_RIDER: ("dolphin",),
    ZombieType.JACK_IN_THE_BOX: ("jack", "jitb"),
    ZombieType.BALLOON: ("balloon",),
    ZombieType.DIGGER: ("digger", "miner"),
    ZombieType.POGO: ("pogo",),
    ZombieType.YETI: ("yeti",),
    ZombieType.BUNGEE: ("bungee",),
    ZombieType.LADDER: ("ladder",),
    ZombieType.CATAPULT: ("catapult",),
    ZombieType.GARGANTUAR: ("garg", "gargantuar"),
    ZombieType.IMP: ("imp",),
    ZombieType.ZOMBOSS: ("zomboss", "boss"),
    ZombieType.GIGA_GARGANTUAR: ("giga", "redeye", "gigagarg"),
}

_current_style = "official"


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-\.·]+", "", str(s)).lower()


def _build_index(enum_cls, *tables) -> Dict[str, int]:
    idx: Dict[str, int] = {}
    for m in enum_cls:
        idx.setdefault(_norm(m.name), int(m))
    for table in tables:
        for member, names in table.items():
            if isinstance(names, str):
                names = (names,)
            for n in names:
                idx.setdefault(_norm(n), int(member))
    return idx


_PLANT_INDEX = _build_index(PlantType, PLANT_ZH, PLANT_EN, COMMUNITY_ALIASES, PLANT_EN_ALIASES)
_ZOMBIE_INDEX = _build_index(ZombieType, ZOMBIE_ZH, ZOMBIE_EN, ZOMBIE_COMMUNITY_ALIASES,
                             ZOMBIE_EN_ALIASES, {v: k for k, v in ZOMBIE_CHARS.items()})
# Strip a trailing 僵尸 / "zombie" so "红眼巨人僵尸" / "conehead" both work.
_ZOMBIE_SUFFIXES = ("僵尸", "zombie")


def _resolve(value, enum_cls, index: Dict[str, int], key: str, suffixes=()):
    if isinstance(value, bool):
        raise UnknownName(_(key, name=value, max=max(int(m) for m in enum_cls)))
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, int):
        try:
            return enum_cls(value)
        except ValueError:
            raise UnknownName(_(key, name=value, max=max(int(m) for m in enum_cls))) from None
    s = str(value).strip()
    if re.fullmatch(r"[+-]?\d+", s):
        return _resolve(int(s), enum_cls, index, key, suffixes)
    k = _norm(s)
    if k in index:
        return enum_cls(index[k])
    for suf in suffixes:
        if k.endswith(suf) and len(k) > len(suf) and k[: -len(suf)] in index:
            return enum_cls(index[k[: -len(suf)]])
    raise UnknownName(_(key, name=value, max=max(int(m) for m in enum_cls)))


def resolve_plant(name_or_id: Union[int, str, PlantType]) -> PlantType:
    """``'曾哥'`` / ``'gloom'`` / ``'Gloom-shroom'`` / ``'GLOOM_SHROOM'`` / ``42`` -> ``PlantType.GLOOM_SHROOM``."""
    return _resolve(name_or_id, PlantType, _PLANT_INDEX, "unknown_plant")


def resolve_zombie(name_or_id: Union[int, str, ZombieType]) -> ZombieType:
    """``'红眼'`` / ``'红'`` / ``'giga'`` / ``'Giga-gargantuar'`` / ``32`` -> ``ZombieType.GIGA_GARGANTUAR``."""
    return _resolve(name_or_id, ZombieType, _ZOMBIE_INDEX, "unknown_zombie", _ZOMBIE_SUFFIXES)


def set_name_style(style: str) -> str:
    """Default style for :func:`plant_name` / :func:`zombie_name`."""
    global _current_style
    if style not in NAME_STYLES:
        raise ValueError("style must be one of %s, got %r" % (NAME_STYLES, style))
    _current_style = style
    return style


def get_name_style() -> str:
    return _current_style


def _lang(lang: Optional[str]) -> str:
    if lang is None:
        return get_language()
    norm = normalize_lang(lang)
    if norm is None:
        raise ValueError("lang must be 'zh' or 'en', got %r" % (lang,))
    return norm


def _name(member, official_zh, official_en, alias_zh, alias_en, lang, style) -> str:
    lang = _lang(lang)
    style = style or _current_style
    if style not in NAME_STYLES:
        raise ValueError("style must be one of %s, got %r" % (NAME_STYLES, style))
    if style == "community":
        table = alias_zh if lang == "zh" else alias_en
        aliases = table.get(member)
        if aliases:
            return aliases[0]
    return (official_zh if lang == "zh" else official_en)[member]


def plant_name(pt: Union[int, str, PlantType], lang: Optional[str] = None,
               style: Optional[str] = None) -> str:
    """Display name. ``lang``: ``'zh'``/``'en'`` (default: current i18n language);
    ``style``: ``'official'`` / ``'community'`` (default: :func:`set_name_style`)."""
    member = resolve_plant(pt)
    return _name(member, PLANT_ZH, PLANT_EN, COMMUNITY_ALIASES, PLANT_EN_ALIASES, lang, style)


def zombie_name(zt: Union[int, str, ZombieType], lang: Optional[str] = None,
                style: Optional[str] = None) -> str:
    member = resolve_zombie(zt)
    return _name(member, ZOMBIE_ZH, ZOMBIE_EN, ZOMBIE_COMMUNITY_ALIASES, ZOMBIE_EN_ALIASES, lang, style)
