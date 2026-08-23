"""Game enumerations: plant / zombie / scene ids and community letter maps.

Ids are the in-game values (PvZ1 PC, all official builds). Chinese names
follow 植吧 / AvZ usage. Sources: AvZ ``avz_pvz_struct.h``, pvzclass,
PvZ-Emulator, lonelystar ArrayDesign ``NtS`` table (0x00–0x2F == these ids).
"""
from __future__ import annotations

from enum import IntEnum
from typing import Dict, Tuple

__all__ = [
    "PlantType",
    "ZombieType",
    "Scene",
    "PLANT_ZH",
    "PLANT_EN",
    "ZOMBIE_ZH",
    "ZOMBIE_EN",
    "CARD_LETTERS",
    "ZOMBIE_CHARS",
    "parse_cards",
    "parse_zombies",
    "IMITATER_OFFSET",
]


class PlantType(IntEnum):
    PEASHOOTER = 0
    SUNFLOWER = 1
    CHERRY_BOMB = 2
    WALL_NUT = 3
    POTATO_MINE = 4
    SNOW_PEA = 5
    CHOMPER = 6
    REPEATER = 7
    PUFF_SHROOM = 8
    SUN_SHROOM = 9
    FUME_SHROOM = 10
    GRAVE_BUSTER = 11
    HYPNO_SHROOM = 12
    SCAREDY_SHROOM = 13
    ICE_SHROOM = 14
    DOOM_SHROOM = 15
    LILY_PAD = 16
    SQUASH = 17
    THREEPEATER = 18
    TANGLE_KELP = 19
    JALAPENO = 20
    SPIKEWEED = 21
    TORCHWOOD = 22
    TALL_NUT = 23
    SEA_SHROOM = 24
    PLANTERN = 25
    CACTUS = 26
    BLOVER = 27
    SPLIT_PEA = 28
    STARFRUIT = 29
    PUMPKIN = 30
    MAGNET_SHROOM = 31
    CABBAGE_PULT = 32
    FLOWER_POT = 33
    KERNEL_PULT = 34
    COFFEE_BEAN = 35
    GARLIC = 36
    UMBRELLA_LEAF = 37
    MARIGOLD = 38
    MELON_PULT = 39
    GATLING_PEA = 40
    TWIN_SUNFLOWER = 41
    GLOOM_SHROOM = 42
    CATTAIL = 43
    WINTER_MELON = 44
    GOLD_MAGNET = 45
    SPIKEROCK = 46
    COB_CANNON = 47
    IMITATER = 48


IMITATER_OFFSET = 49  # AvZ: imitater version of card id x is x + 49

PLANT_ZH: Dict[PlantType, str] = {
    PlantType.PEASHOOTER: "豌豆射手",
    PlantType.SUNFLOWER: "向日葵",
    PlantType.CHERRY_BOMB: "樱桃炸弹",
    PlantType.WALL_NUT: "坚果",
    PlantType.POTATO_MINE: "土豆雷",
    PlantType.SNOW_PEA: "寒冰射手",
    PlantType.CHOMPER: "大嘴花",
    PlantType.REPEATER: "双发射手",
    PlantType.PUFF_SHROOM: "小喷菇",
    PlantType.SUN_SHROOM: "阳光菇",
    PlantType.FUME_SHROOM: "大喷菇",
    PlantType.GRAVE_BUSTER: "墓碑吞噬者",
    PlantType.HYPNO_SHROOM: "魅惑菇",
    PlantType.SCAREDY_SHROOM: "胆小菇",
    PlantType.ICE_SHROOM: "寒冰菇",
    PlantType.DOOM_SHROOM: "毁灭菇",
    PlantType.LILY_PAD: "荷叶",
    PlantType.SQUASH: "窝瓜",
    PlantType.THREEPEATER: "三线射手",
    PlantType.TANGLE_KELP: "缠绕海草",
    PlantType.JALAPENO: "火爆辣椒",
    PlantType.SPIKEWEED: "地刺",
    PlantType.TORCHWOOD: "火炬树桩",
    PlantType.TALL_NUT: "高坚果",
    PlantType.SEA_SHROOM: "水兵菇",
    PlantType.PLANTERN: "路灯花",
    PlantType.CACTUS: "仙人掌",
    PlantType.BLOVER: "三叶草",
    PlantType.SPLIT_PEA: "裂荚射手",
    PlantType.STARFRUIT: "杨桃",
    PlantType.PUMPKIN: "南瓜头",
    PlantType.MAGNET_SHROOM: "磁力菇",
    PlantType.CABBAGE_PULT: "卷心菜投手",
    PlantType.FLOWER_POT: "花盆",
    PlantType.KERNEL_PULT: "玉米投手",
    PlantType.COFFEE_BEAN: "咖啡豆",
    PlantType.GARLIC: "大蒜",
    PlantType.UMBRELLA_LEAF: "叶子保护伞",
    PlantType.MARIGOLD: "金盏花",
    PlantType.MELON_PULT: "西瓜投手",
    PlantType.GATLING_PEA: "机枪射手",
    PlantType.TWIN_SUNFLOWER: "双子向日葵",
    PlantType.GLOOM_SHROOM: "忧郁菇",
    PlantType.CATTAIL: "香蒲",
    PlantType.WINTER_MELON: "冰瓜投手",
    PlantType.GOLD_MAGNET: "吸金磁",
    PlantType.SPIKEROCK: "地刺王",
    PlantType.COB_CANNON: "玉米加农炮",
    PlantType.IMITATER: "模仿者",
}

#: Official English names (PC GOTY almanac spelling).
PLANT_EN: Dict[PlantType, str] = {
    PlantType.PEASHOOTER: "Peashooter",
    PlantType.SUNFLOWER: "Sunflower",
    PlantType.CHERRY_BOMB: "Cherry Bomb",
    PlantType.WALL_NUT: "Wall-nut",
    PlantType.POTATO_MINE: "Potato Mine",
    PlantType.SNOW_PEA: "Snow Pea",
    PlantType.CHOMPER: "Chomper",
    PlantType.REPEATER: "Repeater",
    PlantType.PUFF_SHROOM: "Puff-shroom",
    PlantType.SUN_SHROOM: "Sun-shroom",
    PlantType.FUME_SHROOM: "Fume-shroom",
    PlantType.GRAVE_BUSTER: "Grave Buster",
    PlantType.HYPNO_SHROOM: "Hypno-shroom",
    PlantType.SCAREDY_SHROOM: "Scaredy-shroom",
    PlantType.ICE_SHROOM: "Ice-shroom",
    PlantType.DOOM_SHROOM: "Doom-shroom",
    PlantType.LILY_PAD: "Lily Pad",
    PlantType.SQUASH: "Squash",
    PlantType.THREEPEATER: "Threepeater",
    PlantType.TANGLE_KELP: "Tangle Kelp",
    PlantType.JALAPENO: "Jalapeno",
    PlantType.SPIKEWEED: "Spikeweed",
    PlantType.TORCHWOOD: "Torchwood",
    PlantType.TALL_NUT: "Tall-nut",
    PlantType.SEA_SHROOM: "Sea-shroom",
    PlantType.PLANTERN: "Plantern",
    PlantType.CACTUS: "Cactus",
    PlantType.BLOVER: "Blover",
    PlantType.SPLIT_PEA: "Split Pea",
    PlantType.STARFRUIT: "Starfruit",
    PlantType.PUMPKIN: "Pumpkin",
    PlantType.MAGNET_SHROOM: "Magnet-shroom",
    PlantType.CABBAGE_PULT: "Cabbage-pult",
    PlantType.FLOWER_POT: "Flower Pot",
    PlantType.KERNEL_PULT: "Kernel-pult",
    PlantType.COFFEE_BEAN: "Coffee Bean",
    PlantType.GARLIC: "Garlic",
    PlantType.UMBRELLA_LEAF: "Umbrella Leaf",
    PlantType.MARIGOLD: "Marigold",
    PlantType.MELON_PULT: "Melon-pult",
    PlantType.GATLING_PEA: "Gatling Pea",
    PlantType.TWIN_SUNFLOWER: "Twin Sunflower",
    PlantType.GLOOM_SHROOM: "Gloom-shroom",
    PlantType.CATTAIL: "Cattail",
    PlantType.WINTER_MELON: "Winter Melon",
    PlantType.GOLD_MAGNET: "Gold Magnet",
    PlantType.SPIKEROCK: "Spikerock",
    PlantType.COB_CANNON: "Cob Cannon",
    PlantType.IMITATER: "Imitater",
}


class ZombieType(IntEnum):
    ZOMBIE = 0
    FLAG = 1
    CONEHEAD = 2
    POLE_VAULTING = 3
    BUCKETHEAD = 4
    NEWSPAPER = 5
    SCREEN_DOOR = 6
    FOOTBALL = 7
    DANCING = 8
    BACKUP_DANCER = 9
    DUCKY_TUBE = 10
    SNORKEL = 11
    ZOMBONI = 12
    BOBSLED = 13
    DOLPHIN_RIDER = 14
    JACK_IN_THE_BOX = 15
    BALLOON = 16
    DIGGER = 17
    POGO = 18
    YETI = 19
    BUNGEE = 20
    LADDER = 21
    CATAPULT = 22
    GARGANTUAR = 23
    IMP = 24
    ZOMBOSS = 25
    PEASHOOTER_ZOMBIE = 26
    WALL_NUT_ZOMBIE = 27
    JALAPENO_ZOMBIE = 28
    GATLING_PEA_ZOMBIE = 29
    SQUASH_ZOMBIE = 30
    TALL_NUT_ZOMBIE = 31
    GIGA_GARGANTUAR = 32


ZOMBIE_ZH: Dict[ZombieType, str] = {
    ZombieType.ZOMBIE: "普通僵尸",
    ZombieType.FLAG: "旗帜僵尸",
    ZombieType.CONEHEAD: "路障僵尸",
    ZombieType.POLE_VAULTING: "撑杆僵尸",
    ZombieType.BUCKETHEAD: "铁桶僵尸",
    ZombieType.NEWSPAPER: "读报僵尸",
    ZombieType.SCREEN_DOOR: "铁门僵尸",
    ZombieType.FOOTBALL: "橄榄球僵尸",
    ZombieType.DANCING: "舞王僵尸",
    ZombieType.BACKUP_DANCER: "伴舞僵尸",
    ZombieType.DUCKY_TUBE: "鸭子僵尸",
    ZombieType.SNORKEL: "潜水僵尸",
    ZombieType.ZOMBONI: "冰车僵尸",
    ZombieType.BOBSLED: "雪橇僵尸",
    ZombieType.DOLPHIN_RIDER: "海豚僵尸",
    ZombieType.JACK_IN_THE_BOX: "小丑僵尸",
    ZombieType.BALLOON: "气球僵尸",
    ZombieType.DIGGER: "矿工僵尸",
    ZombieType.POGO: "跳跳僵尸",
    ZombieType.YETI: "雪人僵尸",
    ZombieType.BUNGEE: "蹦极僵尸",
    ZombieType.LADDER: "梯子僵尸",
    ZombieType.CATAPULT: "投篮僵尸",
    ZombieType.GARGANTUAR: "白眼巨人",
    ZombieType.IMP: "小鬼僵尸",
    ZombieType.ZOMBOSS: "僵王博士",
    ZombieType.PEASHOOTER_ZOMBIE: "豌豆僵尸",
    ZombieType.WALL_NUT_ZOMBIE: "坚果僵尸",
    ZombieType.JALAPENO_ZOMBIE: "辣椒僵尸",
    ZombieType.GATLING_PEA_ZOMBIE: "机枪僵尸",
    ZombieType.SQUASH_ZOMBIE: "窝瓜僵尸",
    ZombieType.TALL_NUT_ZOMBIE: "高坚果僵尸",
    ZombieType.GIGA_GARGANTUAR: "红眼巨人",
}

#: Official English names (PC GOTY almanac spelling).
ZOMBIE_EN: Dict[ZombieType, str] = {
    ZombieType.ZOMBIE: "Zombie",
    ZombieType.FLAG: "Flag Zombie",
    ZombieType.CONEHEAD: "Conehead Zombie",
    ZombieType.POLE_VAULTING: "Pole Vaulting Zombie",
    ZombieType.BUCKETHEAD: "Buckethead Zombie",
    ZombieType.NEWSPAPER: "Newspaper Zombie",
    ZombieType.SCREEN_DOOR: "Screen Door Zombie",
    ZombieType.FOOTBALL: "Football Zombie",
    ZombieType.DANCING: "Dancing Zombie",
    ZombieType.BACKUP_DANCER: "Backup Dancer",
    ZombieType.DUCKY_TUBE: "Ducky Tube Zombie",
    ZombieType.SNORKEL: "Snorkel Zombie",
    ZombieType.ZOMBONI: "Zomboni",
    ZombieType.BOBSLED: "Zombie Bobsled Team",
    ZombieType.DOLPHIN_RIDER: "Dolphin Rider Zombie",
    ZombieType.JACK_IN_THE_BOX: "Jack-in-the-Box Zombie",
    ZombieType.BALLOON: "Balloon Zombie",
    ZombieType.DIGGER: "Digger Zombie",
    ZombieType.POGO: "Pogo Zombie",
    ZombieType.YETI: "Zombie Yeti",
    ZombieType.BUNGEE: "Bungee Zombie",
    ZombieType.LADDER: "Ladder Zombie",
    ZombieType.CATAPULT: "Catapult Zombie",
    ZombieType.GARGANTUAR: "Gargantuar",
    ZombieType.IMP: "Imp",
    ZombieType.ZOMBOSS: "Dr. Zomboss",
    ZombieType.PEASHOOTER_ZOMBIE: "Peashooter Zombie",
    ZombieType.WALL_NUT_ZOMBIE: "Wall-nut Zombie",
    ZombieType.JALAPENO_ZOMBIE: "Jalapeno Zombie",
    ZombieType.GATLING_PEA_ZOMBIE: "Gatling Pea Zombie",
    ZombieType.SQUASH_ZOMBIE: "Squash Zombie",
    ZombieType.TALL_NUT_ZOMBIE: "Tall-nut Zombie",
    ZombieType.GIGA_GARGANTUAR: "Giga-gargantuar",
}


class Scene(IntEnum):
    """In-game scene id with community two-letter codes."""

    DAY = 0  # DE
    NIGHT = 1  # NE
    POOL = 2  # PE
    FOG = 3  # FE
    ROOF = 4  # RE
    MOON = 5  # ME

    @property
    def code(self) -> str:
        return _SCENE_CODES[self]

    @classmethod
    def from_code(cls, code: str) -> "Scene":
        code = code.strip().upper()
        for scene, c in _SCENE_CODES.items():
            if c == code:
                return scene
        raise ValueError(f"unknown scene code: {code!r}")

    @property
    def rows(self) -> int:
        return 6 if self in (Scene.POOL, Scene.FOG) else 5

    @property
    def is_day(self) -> bool:
        return self in (Scene.DAY, Scene.POOL, Scene.ROOF)

    @property
    def water_rows(self) -> Tuple[int, ...]:
        return (3, 4) if self in (Scene.POOL, Scene.FOG) else ()

    @property
    def needs_pot(self) -> bool:
        return self in (Scene.ROOF, Scene.MOON)


_SCENE_CODES: Dict[Scene, str] = {
    Scene.DAY: "DE",
    Scene.NIGHT: "NE",
    Scene.POOL: "PE",
    Scene.FOG: "FE",
    Scene.ROOF: "RE",
    Scene.MOON: "ME",
}

# AvZ ASelectCards single-letter map. Repeated letter = imitater version.
CARD_LETTERS: Dict[str, PlantType] = {
    "A": PlantType.CHERRY_BOMB,
    "B": PlantType.BLOVER,
    "F": PlantType.FLOWER_POT,
    "G": PlantType.GRAVE_BUSTER,
    "I": PlantType.ICE_SHROOM,
    "J": PlantType.JALAPENO,
    "K": PlantType.COFFEE_BEAN,
    "L": PlantType.LILY_PAD,
    "M": PlantType.POTATO_MINE,
    "N": PlantType.DOOM_SHROOM,
    "P": PlantType.PUMPKIN,
    "T": PlantType.TALL_NUT,
    "U": PlantType.UMBRELLA_LEAF,
    "W": PlantType.SQUASH,
    "_": PlantType.SPIKEWEED,
}
# "C"/"c" = automatic fodder (AvZ picks 小喷/花盆/胆小/阳光菇/向日葵)
CARD_FODDER_LETTERS = ("C", "c")

# AvZ ASetZombies single-char map (普旗障杆桶报门橄舞潜车橇豚丑气矿跳雪偷梯篮白博豌坚辣枪窝高红).
ZOMBIE_CHARS: Dict[str, ZombieType] = {
    "普": ZombieType.ZOMBIE,
    "旗": ZombieType.FLAG,
    "障": ZombieType.CONEHEAD,
    "杆": ZombieType.POLE_VAULTING,
    "桶": ZombieType.BUCKETHEAD,
    "报": ZombieType.NEWSPAPER,
    "门": ZombieType.SCREEN_DOOR,
    "橄": ZombieType.FOOTBALL,
    "舞": ZombieType.DANCING,
    "潜": ZombieType.SNORKEL,
    "车": ZombieType.ZOMBONI,
    "橇": ZombieType.BOBSLED,
    "豚": ZombieType.DOLPHIN_RIDER,
    "丑": ZombieType.JACK_IN_THE_BOX,
    "气": ZombieType.BALLOON,
    "矿": ZombieType.DIGGER,
    "跳": ZombieType.POGO,
    "雪": ZombieType.YETI,
    "偷": ZombieType.BUNGEE,
    "梯": ZombieType.LADDER,
    "篮": ZombieType.CATAPULT,
    "白": ZombieType.GARGANTUAR,
    "博": ZombieType.ZOMBOSS,
    "豌": ZombieType.PEASHOOTER_ZOMBIE,
    "坚": ZombieType.WALL_NUT_ZOMBIE,
    "辣": ZombieType.JALAPENO_ZOMBIE,
    "枪": ZombieType.GATLING_PEA_ZOMBIE,
    "窝": ZombieType.SQUASH_ZOMBIE,
    "高": ZombieType.TALL_NUT_ZOMBIE,
    "红": ZombieType.GIGA_GARGANTUAR,
}


def parse_cards(text: str) -> Tuple[Tuple[int, bool], ...]:
    """Parse an AvZ card string like ``"IINAW LPccc"``.

    Returns ``((plant_id, is_imitater), ...)`` in slot order. A letter seen
    for the second time becomes the imitater version. ``C``/``c`` are
    returned as ``(-1, False)`` meaning "auto fodder". Whitespace ignored.
    """
    seen = set()
    out = []
    for ch in text:
        if ch.isspace():
            continue
        if ch in CARD_FODDER_LETTERS:
            out.append((-1, False))
            continue
        if ch not in CARD_LETTERS:
            raise ValueError(f"unknown card letter {ch!r}")
        pid = int(CARD_LETTERS[ch])
        imit = ch in seen
        seen.add(ch)
        out.append((pid, imit))
    return tuple(out)


def parse_zombies(text: str) -> Tuple[ZombieType, ...]:
    """Parse an AvZ zombie string like ``"普杆舞车豚 丑气矿篮偷 红"``."""
    out = []
    for ch in text:
        if ch.isspace():
            continue
        if ch not in ZOMBIE_CHARS:
            raise ValueError(f"unknown zombie char {ch!r}")
        out.append(ZOMBIE_CHARS[ch])
    return tuple(out)
