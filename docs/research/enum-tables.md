# Research: enum-tables (2026-08-22, agent-gathered; verify before relying on)

## Sources actually read (raw source, verbatim excerpts below)
- AvZ (vector-wlc/AsmVsZombies) `inc/avz_types.h` — `enum APlantType`, `enum AZombieType`, CN/EN name tables (`PLANT_DATA`, `ZOMBIE_DATA`).
- AvZ `inc/avz_pvz_struct.h` — scene ids comment, struct offsets.
- dnartz/PvZ-Emulator `object/plant.h`, `object/plant.cpp` (`COST_TABLE`, `CD_TABLE`), `object/zombie.h`, `object/scene.h`, `system/spawn.cpp` (`ZOMBIE_SPAWN_WEIGHT`), `system/zombie/*.cpp` (HP).
- Patoke/re-plants-vs-zombies (decompiled GOTY 1.2.0.1073) `ConstEnums.h` (`SeedType`, `ZombieType`, `GameMode`, `BackgroundType`), `Lawn/Plant.cpp` (`gPlantDefs` @0x69F2B0), `Lawn/Zombie.cpp` (`gZombieDefs` @0x69DA80, HP assignments in `ZombieInitialize`).
- pvzclass (Lazuplis-Mei/pvzclass) GitHub repo contains only a README (source not on GitHub) — NOT verified.

All three independent sources (AvZ, PvZ-Emulator, re-plants) agree on every ID below.

---

## 1. PlantType / SeedType 0..48

Cost = sun; CD = seed recharge in cs (centiseconds, game ticks at 100/s: 750=7.5s, 3000=30s, 5000=50s). Cost/CD columns are identical in PvZ-Emulator `plant::COST_TABLE`/`CD_TABLE` (48 entries, idx 0..47) and re-plants `gPlantDefs.mSeedCost/mRefreshTime`. Imitater (48) is only in `gPlantDefs` (cost 0, CD 750 — note the in-game imitater card is priced/recharged by the imitated plant, see unknowns). CN name = AvZ long name; short = AvZ short alias.

| ID | Original enum (SeedType) | AvZ enum | English | 中文 (AvZ) | 简称 | Cost | CD |
|---|---|---|---|---|---|---|---|
| 0 | SEED_PEASHOOTER | APEASHOOTER | Peashooter | 豌豆射手 | 豌豆 | 100 | 750 |
| 1 | SEED_SUNFLOWER | ASUNFLOWER | Sunflower | 向日葵 | 小向 | 50 | 750 |
| 2 | SEED_CHERRYBOMB | ACHERRY_BOMB | Cherry Bomb | 樱桃炸弹 | 樱桃 | 150 | 5000 |
| 3 | SEED_WALLNUT | AWALL_NUT | Wall-nut | 坚果 | 坚果 | 50 | 3000 |
| 4 | SEED_POTATOMINE | APOTATO_MINE | Potato Mine | 土豆地雷 | 土豆 | 25 | 3000 |
| 5 | SEED_SNOWPEA | ASNOW_PEA | Snow Pea | 寒冰射手 | 冰豆 | 175 | 750 |
| 6 | SEED_CHOMPER | ACHOMPER | Chomper | 大嘴花 | 大嘴 | 150 | 750 |
| 7 | SEED_REPEATER | AREPEATER | Repeater | 双发射手 (enum comment: 双重射手) | 双发 | 200 | 750 |
| 8 | SEED_PUFFSHROOM | APUFF_SHROOM | Puff-shroom | 小喷菇 | 小喷 | 0 | 750 |
| 9 | SEED_SUNSHROOM | ASUN_SHROOM | Sun-shroom | 阳光菇 | 阳光 | 25 | 750 |
| 10 | SEED_FUMESHROOM | AFUME_SHROOM | Fume-shroom | 大喷菇 | 大喷 | 75 | 750 |
| 11 | SEED_GRAVEBUSTER | AGRAVE_BUSTER | Grave Buster | 墓碑吞噬者 | 墓碑 | 75 | 750 |
| 12 | SEED_HYPNOSHROOM | AHYPNO_SHROOM | Hypno-shroom | 魅惑菇 | 魅惑 | 75 | 3000 |
| 13 | SEED_SCAREDYSHROOM | ASCAREDY_SHROOM | Scaredy-shroom | 胆小菇 | 胆小 | 25 | 750 |
| 14 | SEED_ICESHROOM | AICE_SHROOM | Ice-shroom | 寒冰菇 | 冰菇 | 75 | 5000 |
| 15 | SEED_DOOMSHROOM | ADOOM_SHROOM | Doom-shroom | 毁灭菇 | 核弹 | 125 | 5000 |
| 16 | SEED_LILYPAD | ALILY_PAD | Lily Pad | 荷叶 | 荷叶 | 25 | 750 |
| 17 | SEED_SQUASH | ASQUASH | Squash | 倭瓜 | 倭瓜 | 50 | 3000 |
| 18 | SEED_THREEPEATER | ATHREEPEATER | Threepeater | 三发射手 | 三发 | 325 | 750 |
| 19 | SEED_TANGLEKELP | ATANGLE_KELP | Tangle Kelp | 缠绕海藻 | 海藻 | 25 | 3000 |
| 20 | SEED_JALAPENO | AJALAPENO | Jalapeno | 火爆辣椒 | 辣椒 | 125 | 5000 |
| 21 | SEED_SPIKEWEED | ASPIKEWEED | Spikeweed | 地刺 | 地刺 | 100 | 750 |
| 22 | SEED_TORCHWOOD | ATORCHWOOD | Torchwood | 火炬树桩 | 火炬 | 175 | 750 |
| 23 | SEED_TALLNUT | ATALL_NUT | Tall-nut | 高坚果 | 高坚 | 125 | 3000 |
| 24 | SEED_SEASHROOM | ASEA_SHROOM | Sea-shroom | 水兵菇 | 水兵 | 0 | 3000 |
| 25 | SEED_PLANTERN | APLANTERN | Plantern | 路灯花 | 路灯 | 25 | 3000 |
| 26 | SEED_CACTUS | ACACTUS | Cactus | 仙人掌 | 仙人掌 | 125 | 750 |
| 27 | SEED_BLOVER | ABLOVER | Blover | 三叶草 | 三叶 | 100 | 750 |
| 28 | SEED_SPLITPEA | ASPLIT_PEA | Split Pea | 裂荚射手 | 裂荚 | 125 | 750 |
| 29 | SEED_STARFRUIT | ASTARFRUIT | Starfruit | 杨桃 | 杨桃 | 125 | 750 |
| 30 | SEED_PUMPKINSHELL | APUMPKIN | Pumpkin | 南瓜头 | 南瓜 | 125 | 3000 |
| 31 | SEED_MAGNETSHROOM | AMAGNET_SHROOM | Magnet-shroom | 磁力菇 | 磁力 | 100 | 750 |
| 32 | SEED_CABBAGEPULT | ACABBAGE_PULT | Cabbage-pult | 卷心菜投手 | 卷心菜 | 100 | 750 |
| 33 | SEED_FLOWERPOT | AFLOWER_POT | Flower Pot | 花盆 | 花盆 | 25 | 750 |
| 34 | SEED_KERNELPULT | AKERNEL_PULT | Kernel-pult | 玉米投手 | 玉米 | 100 | 750 |
| 35 | SEED_INSTANT_COFFEE | ACOFFEE_BEAN | Coffee Bean | 咖啡豆 | 咖啡 | 75 | 750 |
| 36 | SEED_GARLIC | AGARLIC | Garlic | 大蒜 | 大蒜 | 50 | 750 |
| 37 | SEED_UMBRELLA | AUMBRELLA_LEAF | Umbrella Leaf | 叶子保护伞 | 伞叶 | 100 | 750 |
| 38 | SEED_MARIGOLD | AMARIGOLD | Marigold | 金盏花 | 金盏花 | 50 | 3000 |
| 39 | SEED_MELONPULT | AMELON_PULT | Melon-pult | 西瓜投手 | 西瓜 | 300 | 750 |
| 40 | SEED_GATLINGPEA | AGATLING_PEA | Gatling Pea | 机枪射手 | 机枪 | 250 | 5000 |
| 41 | SEED_TWINSUNFLOWER | ATWIN_SUNFLOWER | Twin Sunflower | 双子向日葵 | 双花 | 150 | 5000 |
| 42 | SEED_GLOOMSHROOM | AGLOOM_SHROOM | Gloom-shroom | 忧郁菇 | 曾 | 150 | 5000 |
| 43 | SEED_CATTAIL | ACATTAIL | Cattail | 香蒲 | 猫 | 225 | 5000 |
| 44 | SEED_WINTERMELON | AWINTER_MELON | Winter Melon | 冰西瓜投手 | 冰瓜 | 200 | 5000 |
| 45 | SEED_GOLD_MAGNET | AGOLD_MAGNET | Gold Magnet | 吸金磁 | 吸金 | 50 | 5000 |
| 46 | SEED_SPIKEROCK | ASPIKEROCK | Spikerock | 地刺王 | 刺王 | 125 | 5000 |
| 47 | SEED_COBCANNON | ACOB_CANNON | Cob Cannon | 玉米加农炮 | 炮 | 500 | 5000 |
| 48 | SEED_IMITATER | AIMITATOR | Imitater | 模仿者 | 模仿 | 0* | 750* |

Extra SeedType values after 48 (re-plants `ConstEnums.h`): 49 SEED_EXPLODE_O_NUT, 50 SEED_GIANT_WALLNUT, 51 SEED_SPROUT, 52 SEED_LEFTPEATER, 53 NUM_SEED_TYPES; then SEED_BEGHOULED_BUTTON_SHUFFLE, SEED_BEGHOULED_BUTTON_CRATER, SEED_SLOT_MACHINE_SUN, ... (mini-game pseudo-seeds).

Upgrade-plant cost rule (PvZ-Emulator `plant_factory::get_cost`, matches game): for type >= 40 (gatling_pea) cost = base + 50 × (number of that plant type already on the lawn). Applies to 40..47 (in Survival/Endless modes).

AvZ convention: imitater cards are encoded as `AM_<PLANT> = AIMITATOR + id` (e.g. AM_PEASHOOTER = 49). Placed plant's `Type()` is always the base id (0..47); the seed slot has separate `Type()` (+0x5C) and `ImitatorType()` (+0x60).

Verbatim PvZ-Emulator tables (`object/plant.cpp`):
```
const std::array<unsigned int, 48> plant::COST_TABLE = {
    100, 50, 150, 50, 25, 175, 150, 200,
    0, 25, 75, 75, 75, 25, 75, 125,
    25, 50, 325, 25, 125, 100, 175, 125,
    0, 25, 125, 100, 125, 125, 125, 100,
    100, 25, 100, 75, 50, 100, 50, 300,
    250, 150, 150, 225, 200, 50, 125, 500 };
const std::array<unsigned int, 48> plant::CD_TABLE = {
    750, 750, 5000, 3000, 3000, 750, 750, 750,
    750, 750, 750, 750, 3000, 750, 5000, 5000,
    750, 3000, 750, 3000, 5000, 750, 750, 3000,
    3000, 3000, 750, 750, 750, 750, 3000, 750,
    750, 750, 750, 750, 750, 750, 3000, 750,
    5000, 5000, 5000, 5000, 5000, 5000, 5000, 5000 };
```

---

## 2. ZombieType 0..32 (+33)

HP columns from re-plants `Zombie::ZombieInitialize` (PvZ-Emulator agrees on every value it implements). Body = mBodyHealth (default 270), Helm = mHelmHealth (一类饰品, zombie+0xD0 in AvZ `OneHp`), Shield = mShieldHealth (二类饰品, +0xDC `TwoHp`). Weight = `gZombieDefs.mPickWeight` (== PvZ-Emulator `spawn::ZOMBIE_SPAWN_WEIGHT[33]`), plus `mZombieValue` (wave-point cost) and `mFirstAllowedWave`.

| ID | Original enum | AvZ enum | English (AvZ) | 中文 (AvZ) | 简称 | Body | Helm(1) | Shield(2) | Weight | Value | FirstWave |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | ZOMBIE_NORMAL | AZOMBIE | Zombie | 普通僵尸/普僵 | 普 | 270 | – | – | 4000 | 1 | 1 |
| 1 | ZOMBIE_FLAG | AFLAG_ZOMBIE | Flag Zombie | 旗帜僵尸 | 旗 | 270 | – | – | 0 | 1 | 1 |
| 2 | ZOMBIE_TRAFFIC_CONE | ACONEHEAD_ZOMBIE | Conehead Zombie | 路障僵尸 | 障 | 270 | 370 | – | 4000 | 2 | 1 |
| 3 | ZOMBIE_POLEVAULTER | APOLE_VAULTING_ZOMBIE | Pole Vaulting Zombie | 撑杆僵尸 | 杆 | 500 | – | – | 2000 | 2 | 5 |
| 4 | ZOMBIE_PAIL | ABUCKETHEAD_ZOMBIE | Buckethead Zombie | 铁桶僵尸 | 桶 | 270 | 1100 | – | 3000 | 4 | 1 |
| 5 | ZOMBIE_NEWSPAPER | ANEWSPAPER_ZOMBIE | Newspaper Zombie | 读报僵尸 | 报 | 270 | – | 150 | 1000 | 2 | 1 |
| 6 | ZOMBIE_DOOR | ASCREEN_DOOR_ZOMBIE | Screen Door Zombie | 铁门僵尸 | 门 | 270 | – | 1100 | 3500 | 4 | 5 |
| 7 | ZOMBIE_FOOTBALL | AFOOTBALL_ZOMBIE | Football Zombie | 橄榄球僵尸 | 橄 | 270 | 1400 | – | 2000 | 7 | 5 |
| 8 | ZOMBIE_DANCER | ADANCING_ZOMBIE | Dancing Zombie | 舞王僵尸 | 舞 | 500 | – | – | 1000 | 5 | 5 |
| 9 | ZOMBIE_BACKUP_DANCER | ABACKUP_DANCER | Backup Dancer | 伴舞僵尸 | 伴 | 270 | – | – | 0 | 1 | 1 |
| 10 | ZOMBIE_DUCKY_TUBE | ADUCKY_TUBE_ZOMBIE | Ducky Tube Zombie | 鸭子救生圈僵尸 | 鸭 | 270 | – | – | 0 | 1 | 5 |
| 11 | ZOMBIE_SNORKEL | ASNORKEL_ZOMBIE | Snorkel Zombie | 潜水僵尸 | 潜 | 270 | – | – | 2000 | 3 | 10 |
| 12 | ZOMBIE_ZAMBONI | AZOMBONI | Zomboni | 冰车僵尸 | 车 | 1350 | – | – | 2000 | 7 | 10 |
| 13 | ZOMBIE_BOBSLED | AZOMBIE_BOBSLED_TEAM | Zombie Bobsled Team | 雪橇僵尸小队 | 橇 | 270 | 300 (bobsled helm) | – | 2000 | 3 | 10 |
| 14 | ZOMBIE_DOLPHIN_RIDER | ADOLPHIN_RIDER_ZOMBIE | Dolphin Rider Zombie | 海豚僵尸 | 豚 | 500 | – | – | 1500 | 3 | 10 |
| 15 | ZOMBIE_JACK_IN_THE_BOX | AJACK_IN_THE_BOX_ZOMBIE | Jack-in-the-Box Zombie | 小丑僵尸 | 丑 | 500 | – | – | 1000 | 3 | 10 |
| 16 | ZOMBIE_BALLOON | ABALLOON_ZOMBIE | Balloon Zombie | 气球僵尸 | 气 | 270 | – | – | 2000 | 2 | 10 |
| 17 | ZOMBIE_DIGGER | ADIGGER_ZOMBIE | Digger Zombie | 矿工僵尸 | 矿 | 270 | 100 (miner hat) | – | 1000 | 4 | 10 |
| 18 | ZOMBIE_POGO | APOGO_ZOMBIE | Pogo Zombie | 跳跳僵尸 | 跳 | 500 | – | – | 1000 | 4 | 10 |
| 19 | ZOMBIE_YETI | AZOMBIE_YETI | Zombie Yeti | 雪人僵尸 | 雪 | 1350 | – | – | 1 | 4 | 1 |
| 20 | ZOMBIE_BUNGEE | ABUNGEE_ZOMBIE | Bungee Zombie | 蹦极僵尸 | 偷 | 450 | – | – | 1000 | 3 | 10 |
| 21 | ZOMBIE_LADDER | ALADDER_ZOMBIE | Ladder Zombie | 扶梯僵尸 (梯子) | 梯 | 500 | – | 500 (ladder) | 1000 | 4 | 10 |
| 22 | ZOMBIE_CATAPULT | ACATAPULT_ZOMBIE | Catapult Zombie | 投篮僵尸 | 篮 | 850 | – | – | 1500 | 5 | 10 |
| 23 | ZOMBIE_GARGANTUAR | AGARGANTUAR | Gargantuar | 巨人僵尸/白眼 | 白 | 3000 | – | – | 1500 | 10 | 15 |
| 24 | ZOMBIE_IMP | AIMP | Imp | 小鬼僵尸 | 鬼 | 270 (70 in I,Zombie) | – | – | 0 | 10 | 1 |
| 25 | ZOMBIE_BOSS | ADR_ZOMBOSS | Dr. Zomboss | 僵王博士/僵博 | 博 | 40000 adventure / 60000 otherwise | – | – | 0 | 10 | 1 |
| 26 | ZOMBIE_PEA_HEAD | APEASHOOTER_ZOMBIE | Peashooter Zombie | 豌豆僵尸 | 豌 | 270 | – | – | 4000 | 1 | 99 |
| 27 | ZOMBIE_WALLNUT_HEAD | AWALL_NUT_ZOMBIE | Wall-nut Zombie | 坚果僵尸 | 坚 | 270 | 1100 | – | 3000 | 4 | 99 |
| 28 | ZOMBIE_JALAPENO_HEAD | AJALAPENO_ZOMBIE | Jalapeno Zombie | 辣椒僵尸 | 辣 | 500 | – | – | 1000 | 3 | 99 |
| 29 | ZOMBIE_GATLING_HEAD | AGATLING_PEA_ZOMBIE | Gatling Pea Zombie | 机枪僵尸 | 机 | 270 | – | – | 2000 | 3 | 99 |
| 30 | ZOMBIE_SQUASH_HEAD | ASQUASH_ZOMBIE | Squash Zombie | 倭瓜僵尸 | 倭 | 270 | – | – | 2000 | 3 | 99 |
| 31 | ZOMBIE_TALLNUT_HEAD | ATALL_NUT_ZOMBIE | Tall-nut Zombie | 高坚果僵尸 | 高 | 270 | 2200 | – | 2000 | 4 | 99 |
| 32 | ZOMBIE_REDEYE_GARGANTUAR | AGIGA_GARGANTUAR | Giga-Gargantuar | 红眼巨人僵尸/红眼 | 红 | 6000 | – | – | 6000 | 10 | 15 |
| 33 | ZOMBIE_ZOMBATAR | (none) | Zombatar flag zombie | – | – | 270 | – | – | 0 | 1 | 1 |

Notes: Zombie 270 body = 270 default set at Zombie.cpp:181 before the switch. Zombiquarium zombies get 200 body. "Little Trouble" levels set mBodyMaxHealth=300. PvZ-Emulator's `zombie_type` enum skips 0xd (bobsled), 0x19..0x1f, 0x21 (not emulated). The original `ZombieType` also continues after ZOMBATAR with console/replanted-only values in a comment (ZOMBIE_TARGET, ZOMBIE_TRASH_CAN, ZOMBIE_GRAVESTONE...) — not in PC GOTY.

PvZ-Emulator endless spawn-weight adjustment (`spawn::get_spawn_weight`, emulator's reproduction of the game's endless weight scaling — verify against Board::PickZombieType if you need exactness): zombie → weight/10 (=400), conehead → weight/4 (=1000), giga → 1000, others unchanged.

Verbatim `spawn::ZOMBIE_SPAWN_WEIGHT[33]`:
```
4000,0,4000,2000,3000, 1000,3500,2000,1000,0, 0,2000,2000,2000,1500,
1000,2000,1000,1000,1, 1000,1000,1500,1500,0, 0,4000,3000,1000,2000, 2000,2000,6000
```

---

## 3. Scene ids (Board+0x554C in AvZ `AMainObject::Scene()`, PvZ-Emulator `scene_type`, original `BackgroundType`)

| ID | Original | AvZ comment | PvZ-Emulator | Community endless code |
|---|---|---|---|---|
| 0 | BACKGROUND_1_DAY | 白天 | day | DE (Day Endless) |
| 1 | BACKGROUND_2_NIGHT | 黑夜 | night | NE (Night Endless) |
| 2 | BACKGROUND_3_POOL | 泳池 | pool | PE (Pool Endless) |
| 3 | BACKGROUND_4_FOG | 雾夜 | fog | FE (Fog Endless) |
| 4 | BACKGROUND_5_ROOF | 天台 | roof | RE (Roof Endless) |
| 5 | BACKGROUND_6_BOSS | 月夜（僵王战场地） | moon_night | ME (Moon-night Endless; the night-roof boss scene, only reachable as endless via mods/AvZ) |
| 6 | BACKGROUND_MUSHROOM_GARDEN | – | – | – |
| 7+ | (further BackgroundType values follow in ConstEnums.h: greenhouse, zen garden, tree of wisdom etc. — not read in full) | | | |

Scene is NOT the mini-game selector. Mini-games / modes are `GameMode` (LawnApp+0x7F8, AvZ `LevelId()`), 0-based, verbatim order from re-plants `ConstEnums.h`:
0 ADVENTURE; 1-5 SURVIVAL_NORMAL_STAGE_1..5 (day,night,pool,fog,roof); 6-10 SURVIVAL_HARD_STAGE_1..5; 11-15 SURVIVAL_ENDLESS_STAGE_1..5 (11=DE,12=NE,13=PE,14=FE,15=RE; ME is not a separate official mode); 16 WAR_AND_PEAS; 17 WALLNUT_BOWLING; 18 SLOT_MACHINE; 19 RAINING_SEEDS; 20 BEGHOULED; 21 INVISIGHOUL; 22 SEEING_STARS; 23 ZOMBIQUARIUM; 24 BEGHOULED_TWIST; 25 LITTLE_TROUBLE; 26 PORTAL_COMBAT; 27 COLUMN; 28 BOBSLED_BONANZA; 29 SPEED; 30 WHACK_A_ZOMBIE; 31 LAST_STAND; 32 WAR_AND_PEAS_2; 33 WALLNUT_BOWLING_2; 34 POGO_PARTY; 35 FINAL_BOSS; 36 ART_CHALLENGE_WALLNUT; 37 SUNNY_DAY; 38 RESODDED; 39 BIG_TIME; 40 ART_CHALLENGE_SUNFLOWER; 41 AIR_RAID; 42 ICE; 43 ZEN_GARDEN; 44 HIGH_GRAVITY; 45 GRAVE_DANGER; 46 SHOVEL; 47 STORMY_NIGHT; 48 BUNGEE_BLITZ; 49 SQUIRREL; 50 TREE_OF_WISDOM; 51-59 SCARY_POTTER_1..9; 60 SCARY_POTTER_ENDLESS; 61-69 PUZZLE_I_ZOMBIE_1..9; 70 PUZZLE_I_ZOMBIE_ENDLESS; 71 UPSELL; 72 INTRO; 73 NUM_GAME_MODES.

Other AvZ-documented ids: `GameUi()` (+0x7FC): 1 main menu, 2 seed selection, 3 in battle. Plant `State()` for cob cannon: 35 empty, 36 loading, 37 ready, 38 firing. Zombie `State()`: 1 fallen, 2 ash, 3 lawnmower-killed, 70 Gargantuar hammer raised. Griditem types (AvZ): 1 GRAVESTONE, 2 CRATER, 3 LADDER, 4 PORTAL_CIRCLE, 5 PORTAL_SQUARE, 6 BRAIN, 7 SCARY_POT, 8 SQUIRREL, 9 ZEN_TOOL, 10 STINKY, 11 RAKE, 12 IZOMBIE_BRAIN.

Relevant AvZ offsets used for these tables: plant Type +0x24, plant Hp +0x40; zombie Type +0x24, Hp +0xC8, OneHp +0xD0, TwoHp +0xDC; seed Cd +0x4C, InitialCd +0x50, Type +0x5C, ImitatorType +0x60; Board Scene +0x554C, Sun +0x5560, GameClock +0x5568, ZombieRefreshHp +0x5594.

## Unknowns
- pvzclass: the GitHub repo Lazuplis-Mei/pvzclass contains only a README (sources hosted elsewhere), so its headers were not verified; the tables rest on AvZ + PvZ-Emulator + re-plants (decompiled GOTY), which all agree.
- Imitater (48) row: gPlantDefs lists cost 0 / refresh 750, but in-game the imitater seed packet derives cost and recharge from the imitated seed type (SeedPacket uses mImitaterType) — not re-verified in SeedPacket.cpp.
- The endless weight scaling in PvZ-Emulator (zombie/10, conehead/4, giga→1000) is the emulator author's reproduction; exact original logic lives in Board::PickZombieType / Board::PickZombieWaves and was not read.
- BackgroundType values beyond 6 (MUSHROOM_GARDEN) not enumerated.
- Chinese names: AvZ's are the canonical community ones; the user's "梯子" for Ladder Zombie is a common alternative to AvZ's "扶梯"; "双重射手" vs "双发射手" both appear in AvZ for Repeater.
- Dr. Zomboss HP is adventure 40000 / non-adventure 60000 (re-plants); PvZ-Emulator does not implement boss or bobsled.

## Sources
- AvZ avz_types.h (APlantType/AZombieType enums + CN/EN names): https://raw.githubusercontent.com/vector-wlc/AsmVsZombies/master/inc/avz_types.h
- AvZ avz_pvz_struct.h (scene ids, struct offsets): https://raw.githubusercontent.com/vector-wlc/AsmVsZombies/master/inc/avz_pvz_struct.h
- PvZ-Emulator object/plant.cpp (COST_TABLE, CD_TABLE): https://raw.githubusercontent.com/dnartz/PvZ-Emulator/master/object/plant.cpp
- PvZ-Emulator object/plant.h (plant_type enum): https://raw.githubusercontent.com/dnartz/PvZ-Emulator/master/object/plant.h
- PvZ-Emulator object/zombie.h (zombie_type enum): https://raw.githubusercontent.com/dnartz/PvZ-Emulator/master/object/zombie.h
- PvZ-Emulator object/scene.h (scene_type): https://raw.githubusercontent.com/dnartz/PvZ-Emulator/master/object/scene.h
- PvZ-Emulator system/spawn.cpp (ZOMBIE_SPAWN_WEIGHT): https://raw.githubusercontent.com/dnartz/PvZ-Emulator/master/system/spawn.cpp
- PvZ-Emulator system/plant/plant_factory.cpp (get_cost upgrade rule): https://raw.githubusercontent.com/dnartz/PvZ-Emulator/master/system/plant/plant_factory.cpp
- PvZ-Emulator system/zombie/common_zombie.cpp (HP): https://raw.githubusercontent.com/dnartz/PvZ-Emulator/master/system/zombie/common_zombie.cpp
- re-plants-vs-zombies ConstEnums.h (SeedType, ZombieType, GameMode, BackgroundType): https://raw.githubusercontent.com/Patoke/re-plants-vs-zombies/main/ConstEnums.h
- re-plants-vs-zombies Lawn/Plant.cpp (gPlantDefs): https://raw.githubusercontent.com/Patoke/re-plants-vs-zombies/main/Lawn/Plant.cpp
- re-plants-vs-zombies Lawn/Zombie.cpp (gZombieDefs, ZombieInitialize HP): https://raw.githubusercontent.com/Patoke/re-plants-vs-zombies/main/Lawn/Zombie.cpp
- re-plants-vs-zombies Lawn/Zombie.h (ZombieDefinition fields): https://raw.githubusercontent.com/Patoke/re-plants-vs-zombies/main/Lawn/Zombie.h