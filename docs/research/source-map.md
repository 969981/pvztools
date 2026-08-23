# 源码映射：re-plants / PvZ-Portable ↔ pvztools 地址档案

日期：2026-08-22。本文把三份分析（函数映射、结构布局、玉米炮发射）合并成一份权威参考，供 `src/pvztools/addr/profiles/*.json` 与 `DESIGN.md §4/§8` 对照使用。

- 分析用的源码快照：`<scratchpad>/re-plants/`（`Lawn/`、`Sexy.TodLib/`、`ConstEnums.h`）和 `<scratchpad>/pvz-portable/src/`。
- 对照档案：`src/pvztools/addr/profiles/goty_1_2_0_1096_en.json`、`1_0_0_1051_en.json`、`goty_1_2_0_1073_en.json`。
- 布局计算脚本与完整输出：`<scratchpad>/layout.py`、`<scratchpad>/layout_out.md`。

---

## §1 来源与许可，以及我们怎么用

| 项目 | 许可 | 地址注释 | 用途 |
|---|---|---|---|
| re-plants（PvZ 反编译重建，VS2005 风格源码） | **CC0-1.0**（仓库 LICENSE；需在引用处注明） | 每个函数带 `//0x4xxxxx` = **1.0.0.1051** VA；部分函数带 `// GOTY @Patoke: 0x...` = **1.2.0.1073** VA；成员带 `//+0x..` = 1051 偏移、`GOTY @Patoke` = 1096 偏移 | 主参考。所有函数/补丁/布局映射都在 re-plants 上做 |
| PvZ-Portable（re-plants 的跨平台移植） | **LGPL-2.1**（含 PvzpLib） | 无任何地址注释 | 仅作交叉验证：游戏对象头文件与 re-plants **逐字节一致**（Board/Plant/Zombie/SeedPacket/SeedBank/Projectile/Coin/LawnMower/GridItem/Challenge/CursorObject）；仅 LawnApp（去掉 `mBigArrowCursor`、`mDRM`，`mCloseRequest` 改 atomic）和 PlayerInfo（为便携存档重写）不同 |

使用原则：

1. 两个仓库都**只作参考**（reference only）。pvztools 不复制任何源码文件。
2. 若确需搬运片段（例如 `GridToPixelY` 的公式、枚举数值），只允许来自 CC0 的 re-plants，且在代码注释中写 `// derived from re-plants (CC0) Lawn/Board.cpp:NNN`。
3. 任何 LGPL 来源（PvZ-Portable / PvzpLib）的代码**一行都不进** pvztools；它只用来确认 re-plants 的布局没有被修改。
4. re-plants 是"现代化重建"，并非原始源码：部分函数被重写（`Board::InitLawnMowers` 有中文注释说明改动）、部分类型被改宽（`Challenge` 的 bool 数组改成 int）、SexyAppFramework 被替换成现代 STL 版本。因此**凡是地址和偏移，以它的注释为准，不以它的声明类型为准**（见 §4 的不一致项）。

---

## §2 档案函数 → 源码函数

方法：re-plants 没有 1096 地址。把 1096 档案的每个条目通过 `1_0_0_1051_en.json`（同名键）对到 1051 VA，再用 1073 注释与 `goty_1_2_0_1073_en.json` 交叉验证（8 个可验证项全部命中：AddPlant 0x40FA10、AddZombieInRow 0x410700、LoadBackgroundImages 0x40C9F0、PuzzleNextStageClear 0x42C9F0、TreeOfWisdomGrow 0x42FF00、MakeSureMusicIsPlaying 0x45EFA0、SyncProfile 0x44D270、AddSunMoney 0x41E6E0 对应补丁点 0x41E6F5）。

| profile key | 1096 RVA | 1051 VA | re-plants 函数（文件:行） | 签名 | 语义 / 前置条件 |
|---|---|---|---|---|---|
| put_plant | 0x105a0 | 0x40D120 | Lawn/Board.cpp:2178 | `Plant* Board::AddPlant(int theGridX, int theGridY, SeedType theSeedType, SeedType theImitaterType)` | 从 `mPlants` 分配、`PlantInitialize`、种植特效、`mChallenge->PlantAdded`、更新统计。thiscall on Board；需要活的 Board（game_ui 2/3）；**不检查 CanPlantAt、不扣阳光** |
| put_plant_imitater | 0x6d010 | 0x466B80 | Lawn/Plant.cpp:4473 | `void Plant::ImitaterMorph()` | 杀掉 `this`（必须是已写 `mImitaterType` 的模仿者），调 `AddPlant(mPlantCol,mRow,mImitaterType,SEED_IMITATER)`，加褪色滤镜。用法：put_plant(SEED_IMITATER) → 写 `imitater_type`(+0x138) → 调此函数 |
| put_plant_iz_style | 0x2e120 | 0x42A530 | Lawn/Challenge.cpp:4463 | `void Challenge::IZombieSetupPlant(Plant*)` | 冻结 body/head reanim（`mAnimRate=0`）、土豆雷立即就绪、设眨眼计数——把刚加的植物变成 I,Zombie 式静态植物。thiscall on `Board::mChallenge` |
| put_zombie | 0x2dce0 | 0x42A0F0 | Lawn/Challenge.cpp:4399 | `void Challenge::IZombiePlaceZombie(ZombieType, int theGridX, int theGridY)` | `AddZombieInRow(type,row,0)` 后定位到 `GridToPixelX(col,row)-30`（蹦极：设目标列）。只用 Board 成员，任何模式可用；thiscall on Challenge |
| put_zombie_in_row | 0x11290 | 0x40DDC0 | Lawn/Board.cpp:2750 | `Zombie* Board::AddZombieInRow(ZombieType, int theRow, int theFromWave)` | 分配僵尸、`ZombieInitialize`（1/5 概率变体）、雪橇车附带 3 个成员；`mSize >= mMaxSize-1` 返回 nullptr。GOTY 多一个 Zombologist 成就检查（雪人、关 40、`theFromWave>=0`） |
| put_grave | 0x2a170 | 0x426620 | Lawn/Challenge.cpp:2948 | `void Challenge::GraveDangerSpawnGraveAt(int, int)` | 杀格子上所有植物，`mBoard->mEnableGraveStones=true`，`AddAGraveStone` + 粒子。无格子合法性检查 |
| put_ladder | 0xc420 | 0x408F40 | Lawn/Board.cpp:474 | `GridItem* Board::AddALadder(int, int)` | 只分配一个 `GRIDITEM_LADDER` |
| put_rake | 0xee20 | 0x40B9C0 | Lawn/Board.cpp:1619 | `void Board::PlaceRake()` | 无 `mPurchases[STORE_ITEM_RAKE]` 则返回（:1621）；固定 `aGridX=7`（:1624）、随机普通行（:1657）、购买数 -1（:1658）。档案里 `temp_patch.row_site/col_site` 覆盖 :1657/:1624 的代码强制行列；`rake_unlimited` 干掉 :1621 |
| mower_start | 0x5efb0 | 0x458DA0 | Lawn/LawnMower.cpp:367 | `void LawnMower::StartMower()` | 已 TRIGGERED 则 no-op；记录 `mWaveRowGotLawnMowered[mRow]`、`mTriggeredLawnMowers++`。thiscall on LawnMower（board+0x118 数组，stride 0x48） |
| mower_delete | 0x5ef20 | 0x458D10 | Lawn/LawnMower.cpp:353 | `void LawnMower::Die()` | `mDead=true`；若 `mBonusLawnMowersRemaining>0` 且未掉奖励则生成替补 |
| mower_restore | 0xf0c0 | 0x40BC70 | Lawn/Board.cpp:1671 | `void Board::InitLawnMowers()` | 对 Beghouled/禅境/智慧树/Last Stand/水族馆/松鼠/I,Zombie、以及无屋顶清洁器的屋顶直接返回；否则每个合法行分配一辆（`mVisible=false`）。工具在关卡中重建时配合 `mower_init_allow` + `mower_init_no_anim`；gate ui=3 |
| delete_plant | 0x6de50 | 0x4679B0 | Lawn/Plant.cpp:5004 | `void Plant::Die()` | 纯移除，不退阳光；处理南瓜/花盆/荷叶叠放 |
| delete_grid_item | 0x51bd0 | 0x44D000 | Lawn/GridItem.cpp:45 | `void GridItem::GridItemDie()` | `mDead=true`，杀 reanim 与粒子 |
| set_plant_sleeping | 0x64b70 | 0x45E860 | Lawn/Plant.cpp:514 | `void Plant::SetSleeping(bool)` | 状态不变或 `NotOnGround()` 则 no-op；切 `mIsAsleep`(+0x143) 并增删 Zzz reanim |
| puzzle_next_stage_clear | 0x2da40 | 0x429E50 | Lawn/Challenge.cpp:4318 | `void Challenge::PuzzleNextStageClear()` | 播 HUGE_WAVE，清 `mNextSurvivalStageCounter`，所有僵尸 `DieNoLoot`、所有植物 `Die`。面向砸罐子/I,Zombie 无尽（模式 60/70 与 11–15） |
| pick_background | 0xd5a0 | 0x40A160 | Lawn/Board.cpp:841 | `void Board::LoadBackgroundImages()` | **不是** `Board::PickBackground`（那是 1051 0x40A550，Board.cpp:898）。按 `mBackground`(+0x5564 `scene`) `TodLoadResources("DelayLoad_BackgroundN")`。用法：先写 `scene` 再调 |
| delete_particle_system | 0x124840 | 0x5160C0 | Sexy.TodLib/TodParticle.cpp:303 | `void TodParticleSystem::ParticleSystemDie()` | 释放所有 emitter，`mDead=true`。thiscall on 粒子系统（effect_system→particle_holder→particles，stride 0x2c） |
| pick_zombie_waves | 0xc7d0 | 0x4092E0 | Lawn/Board.cpp:602 | `void Board::PickZombieWaves()` | 计算 `mNumWaves`，用 `PutZombieInWave` 填 `mZombiesInWave`(+0x6cc)。只在关卡开始前/时有意义 |
| remove_cutscene_zombies | 0x11470 | 0x40DF70 | Lawn/Board.cpp:2810 | `void Board::RemoveCutsceneZombies()` | `DieNoLoot` 所有 `mFromWave == ZOMBIE_WAVE_CUTSCENE` 的僵尸 |
| place_street_zombies | 0x3e690 | 0x43A140 | Lawn/CutScene.cpp:486 | `void CutScene::PlaceStreetZombies()` | `mPlacedZombies` 为真则返回（:488）；按 `mNumWaves` 统计可预览僵尸放到街上。thiscall on `Board::mCutScene`(+0x174)；开场后重调需 `street_zombies_hack` |
| play_music | 0x618f0 | 0x45B750 | Lawn/System/Music.cpp:680 | `void Music::MakeSureMusicIsPlaying(MusicTune)` | 曲目不同则 `StopAllMusic()` + `PlayMusic(tune,-1,-1)`。thiscall on `LawnApp::mMusic`(+0x95c GOTY) |
| sync_profile | 0x4eb90 | 0x44A320 | Lawn/Widget/GameSelector.cpp:510 | `void GameSelector::SyncProfile(bool theShowLoading)` | 可选 Loading 对话框 + `PreloadForUser`；按 `mPlayerInfo` 重算解锁标志。thiscall on `LawnApp::mGameSelector`(+0x870)；**仅主菜单有效** |
| fade_out_level | 0xf860 | 0x40C3E0 | Lawn/Board.cpp:1904 | `void Board::FadeOutLevel()` | `mLevelComplete=true`，停音乐、播胜利音、开始淡出。gate ui=3 |
| wisdom_tree_grow | 0x30f60 | 0x42D1F0 | Lawn/Challenge.cpp:5471 | `void Challenge::TreeOfWisdomGrow()` | `mChallengeRecords[当前挑战]++`，播 `anim_grow%d`；GOTY 在 100 处加成就。需 game_mode 50 且挑战 reanim 已加载 |
| cob_fire | 1096 档案缺；`anchors.py` 体特征 `mov [esi+0x3C],0x26` 解析 | 0x466D50 | Lawn/Plant.cpp:4529 | `void Plant::CobCannonFire(int theTargetX, int theTargetY)` | 见 §6 |

1051 档案独有、1096 缺的函数（供参考）：`kill_zombie` = `Zombie::DieWithLoot()`（Zombie.cpp:7380）；`shovel_plant` = `Board::MouseDownWithTool(int,int,int,CursorType)`（Board.cpp:4184）；`plant_card` = `SeedPacket::MouseDown(int,int,int)`（SeedPacket.cpp:705）；`choose_card` = `SeedChooserScreen::ClickedSeedInChooser(ChosenSeed&)`（SeedChooserScreen.cpp:846）。`mouse_down` 0x539390 超出最后一个带注释的 Lawn 函数，无法从 re-plants 定位。

---

## §3 补丁 → 源码行

偏移 = 1051 补丁 VA − 所在函数 1051 VA；字节语义由 on/off 对解码。

| patch | 1096 site | 函数（文件:行） | 被改的语句 | 语义 |
|---|---|---|---|---|
| sun_cap_unlock | 0x1f4e5 | `Board::AddSunMoney` Board.cpp:8756（1051 内联进 `Coin::ScoreCoin`；GOTY 独立，1073 站点 = AddSunMoney+0x15） | `if (mSunMoney > 9990) mSunMoney = 9990;` | `jle`→`jmp`，永远跳过钳制 |
| auto_collect | 0x352f2 | `Coin::Update` Coin.cpp:743 | `else if (!mIsBeingCollected) UpdateFall(); else UpdateCollected();` | `jne`→`jmp`，强制走 `UpdateCollected()` |
| no_loot_drop | 0x144d26 | `Zombie::DropLoot` Zombie.cpp:7347 | `if (mDroppedLoot \|\| HasLevelAwardDropped() \|\| !CanDropLoot()) return;` | `5b`→`66` 改 bool 测试使早返回成立；关卡奖励仍由上方 `TrySpawnLevelAward` 处理 |
| unlimited_fertilizer | 0x12da8b | `ZenGarden::MouseDownWithFeedingTool` ZenGarden.cpp:962 | `mPurchases[STORE_ITEM_FERTILIZER]--;` | `dec [eax+xx]`→`cmp` |
| unlimited_bug_spray | 0x12db83 | 同上 :973 | `mPurchases[STORE_ITEM_BUG_SPRAY]--;` | 同 |
| unlimited_chocolate | 0x12d6a5, 0x12d727 | 同上 :908（Stinky）/ :916（植物） | 两处 `mPurchases[STORE_ITEM_CHOCOLATE]--;` | 同 |
| unlimited_tree_food | 0x311ed | `Challenge::TreeOfWisdomFertilize` Challenge.cpp:5508 | `mPurchases[STORE_ITEM_TREE_FOOD]--;` | 同 |
| place_anywhere | 0x13350 / 0x3d100 / 0x2dec9 | `Board::MouseDownWithPlant` Board.cpp:3799-3800；`CursorPreview::Update` CursorObject.cpp:247；`Challenge::IZombieMouseDownWithZombie` Challenge.cpp:4427 | `CanPlantAt(...) != PLANTING_OK` 拒绝分支；预览测试；I,Zombie 测试 | `jz`→`jno` / `je`→`jmp` / `jz`→`jge`：忽略 CanPlantAt；仍扣阳光、消耗卡片 |
| fast_conveyor | 0x2684f / 0x9831e | `Challenge::UpdateConveyorBelt` Challenge.cpp:1633；`SeedBank::UpdateConveyorBelt` SeedPacket.cpp:1147 | `if (mConveyorBeltCounter > 0) return;`；`if (mConveyorBeltCounter % CONVEYOR_SPEED == 0)` | `jg`→`jo`（每 tick 出卡）；`test`→`xor`（每帧滑动） |
| sticky_shovel | 0x158b3 | `Board::ClearCursor` Board.cpp:4720-4721 | `mCursorObject->mType = SEED_NONE; mCursorType = CURSOR_TYPE_NORMAL;` | `mov`→`cmp`，一个复位存储失效 |
| rake_unlimited（temp） | 0xee41 | `Board::PlaceRake` Board.cpp:1621 | `if (!mPurchases[STORE_ITEM_RAKE]) return;` | `jz`→`jo`；:1658 仍会减购买数 |
| mower_init_allow（temp） | 0xf167 | `Board::InitLawnMowers` Board.cpp:1675-1684（+0xa7） | 模式/行合法性测试 | 立即数 `00`→`01`。re-plants 重写了此函数，精确操作数无法对应到语句 |
| mower_init_no_anim（temp） | 0x5e222 | `LawnMower::LawnMowerInitialize` LawnMower.cpp:10（+0x2） | 函数开头的一个 `call rel32` | 改写首个 call 使重建时无滚入/reanim 副作用。re-plants 函数体以成员赋值开头，无法确认是哪个 call |
| plant_invincible | 0x1447a1 | `Zombie::EatPlant` Zombie.cpp:7154 | `thePlant->mPlantHealth -= DAMAGE_PER_EAT;` | `add [esi+0x40],-4` 的立即数→0 |
| | 0x207df | `Board::KillAllPlantsInRadius` Board.cpp:9499 | overlap 分支 | `je`→`jmp`，爆炸不杀植物 |
| | 0x13beda | `Zombie::UpdateZombieJalapenoHead` Zombie.cpp:2447/2459 | `if (aPlant->mRow == mRow && !NotOnGround()) Die()` | `jne`→`jmp` |
| | 0x74ffb | `Projectile::CheckForCollision` Projectile.cpp:293 | `aPlant->mPlantHealth -= aProjectileDef.mDamage;` | `sub [eax+0x40],edx`→nop×3 |
| | 0x757b9 | `Projectile::UpdateLobMotion` Projectile.cpp:601 | `aPlant->mPlantHealth -= GetProjectileDef().mDamage;` | `sub [esi+0x40],ecx`→nop×3 |
| | 0x1433ab | `Zombie::SquishAllInSquare` Zombie.cpp:6519 | 行列匹配 | `je`→`jmp`，循环体不执行 |
| | 0x6511a | `Plant::DoRowAreaDamage` Plant.cpp:687 | `if (mSeedType == SEED_SPIKEROCK) SpikeRockTakeDamage(); else Die();` | `jne`→`jo`，永远走 SpikeRock 路径 |
| | 0x64f76 | `Plant::SpikeRockTakeDamage` Plant.cpp:637 | `mPlantHealth -= 50;` | 立即数 -50→0 |
| | 0x68f60 | `Plant::Squish` Plant.cpp:2345（入口） | 整个函数 | 入口改 `ret 4` |
| plant_fragile | 0x1447a1 / 0x74ffb / 0x757b9 / 0x6511a | 同上四处 | 同 | `and [esi+0x40],0`；`sub [eax+0x40],eax`；`sub [esi+0x40],esi`；`jne`→`jmp`（永远 `Die()`）。与 plant_invincible 互斥 |
| zombie_invincible | 0x145dfa | `Zombie::TakeBodyDamage` Zombie.cpp:7909 | `mBodyHealth -= theDamage;` | `sub ebp,[esp+0x20]`→nop×4 |
| | 0x145b14 | `Zombie::TakeHelmDamage` Zombie.cpp:7800 | `mHelmHealth -= aDamageActual;` | `sub ecx,eax`→`sub eax,eax` |
| | 0x14576b | `Zombie::TakeShieldDamage` Zombie.cpp:7689 | `aDamageActual = min(mShieldHealth, theDamage);` | `jl; mov eax,edx`→`xor eax,eax; xor edx,edx` |
| | 0x147691 | `Zombie::ApplyBurn` Zombie.cpp:8761 | `if (mBodyHealth >= 1800 \|\| BOSS) { TakeDamage(1800,18); return; }` | `jge`→`jno` 永远走 TakeDamage（已 nop） |
| | 0x214c3 | `Board::KillAllZombiesInRadius` Board.cpp:9853 | 行范围/overlap | `jg`→`jmp` |
| | 0x6c942 | `Plant::BurnRow` Plant.cpp:4297 | 火爆辣椒 ApplyBurn 条件 | `jne`→`jmp` |
| | 0x677e8 | `Plant::UpdateChomper` Plant.cpp:1781-1800 | `FindTargetZombie` 咬合检查 | `je`→`jmp` |
| | 0x144501 | `Zombie::StartMindControlled` Zombie.cpp:7041 | `mMindControlled = true;` | 立即数 1→0 |
| | 0x6ca51 | `Plant::BlowAwayFliers` Plant.cpp:4331 | `if (aZombie->IsFlying()) mBlowingAway = true;` | `je +5`→nop nop |
| | 0x75463 | `Projectile::DoSplashDamage` Projectile.cpp:476 | `if (IsZombieHitBySplash(aZombie)) TakeDamage(...)` | `je`→`jmp` |
| | 0x5ea45 | `LawnMower::Update` LawnMower.cpp:212 | `MowZombie(aZombie);` 守卫 | `je`→`jmp` |
| zombie_fragile | 0x145dfa / 0x145b14 / 0x14576b / 0x147691 | 同上前四处 | 同 | `sub ebp,ebp`；`sub ecx,ecx`；伤害=全部护盾血；`jo` 永不跳→必碎 |
| instant_reload | 0x673eb | `Plant::UpdateCobCannon` Plant.cpp:1686 | ARMING 中 `if (mStateCountdown == 0)` | `jne`→`jo`，立即进 LOADING |
| | 0x66204 / 0x65f53 | `Plant::UpdatePotato` :1149；`Plant::UpdateSunShroom` :1075 | `if (mStateCountdown == 0)` | 同技巧 |
| | 0x67905 / 0x681f7 | `Plant::UpdateChomper` :1842（DIGESTING）；`Plant::UpdateMagnetShroom` :2046（CHARGING） | `if (mStateCountdown == 0)` | 同 |
| mushrooms_awake | 0x641a2 | `Plant::PlantInitialize` Plant.cpp:166-167 | `if (IsNocturnal(mSeedType) && !StageIsNight()) SetSleeping(true);` | `je`→`jmp` 跳过 SetSleeping；已睡的不受影响，用 `set_plant_sleeping` |
| pause_spawning | 0x2a12c | `Challenge::UpdateZombieSpawning` Challenge.cpp:2928（+0x5c）；调用方 Board.cpp:5529 | 返回表达式中的某个模式测试 | `je`→`jmp`：总报"已处理"，`Board::UpdateZombieSpawning` 波计时不跑 |
| freeze_zombies | 0x13f533, 0x13f53c | `Zombie::UpdateZombieWalking` Zombie.cpp:4136/4140 | `mPosX += aSpeed;` / `mPosX -= aSpeed;` | 栈槽位移 `64`→`54`、`44`→`54`，FPU 读到零槽 |
| always_butter | 0x65522 | `Plant::FindTargetAndFire` Plant.cpp:786 | `if (Sexy::Rand(4) == 0) { ... KERNELPULT_BUTTER }` | `jne`→`jo` |
| no_crater | 0x2136d | `Board::UpdateGridItems` Board.cpp:9810 | `if (aGridItem->mGridItemCounter == 0) GridItemDie();` | `jne`→`jo` |
| no_ice_trail | 0x13f1b0 / 0x2333a | `Zombie::UpdateZamboni` Zombie.cpp:4011（入口 `51`→`c3`）；`Challenge::StartLevel` Challenge.cpp:424 | 整个 UpdateZamboni；Bobsled Bonanza 预铺冰 | 入口 `ret`；`jne`→`jmp` |
| no_explode | 0x13b2ec / 0x13bdcd | `Zombie::UpdateZombieJackInTheBox` Zombie.cpp:2052；`Zombie::UpdateZombieJalapenoHead` :2429 | `if (mPhaseCounter <= 0 && mHasHead)`；`if (mPhaseCounter == 0)` | →`jno` 永远跳过 |
| street_zombies_hack（temp） | 0x3e6a3 | `CutScene::PlaceStreetZombies` CutScene.cpp:488 | `if (mPlacedZombies) return;` | `jne`→`jo` |
| no_fog | 0x1df4d | `Board::UpdateFog` Board.cpp:7604 | `mGridCelFog[x][y] = min(... + aFogFadeInSpeed, aFogMax);` | `cmp esi,edx`→`xor edx,edx`，aFogMax=0 |
| see_vase | 0x531ca | `GridItem::UpdateScaryPot` GridItem.cpp:540-552 | `if (mTransparentCounter < 50) mTransparentCounter++;` | `test eax,eax; jle`→`mov ax,51` |
| background_running | 0x1d87c9 | SexyAppFramework 焦点处理（1051 0x54EBA8；`SexyAppBase::RehupFocus`/WM_ACTIVATE，SexyAppBase.cpp:3748-3776；游戏侧 `LawnApp::LostFocus` LawnApp.cpp:373 → `DoPauseDialog()`） | `jz +0x40`→nop | 失焦分支被抑制。re-plants 的框架文件无地址注释，精确语句不可恢复 |
| main_loop_block | 0x1dd25e | SexyAppFramework 主循环（1051 0x552014；`SexyAppBase::Process`/`UpdateAppStep` SexyAppBase.cpp:5186/5499） | 短回跳 `eb c8` | →`eb fe`（`jmp $`）自旋；必须恢复 |
| userdata_readonly | 0x1d3fd3 | `SexyAppBase::EraseFile` SexyAppBase.cpp:2113-2116（1051 0x54B267） | `if (mPlayingDemoBuffer) return true; return DeleteFileA(...)` | `jz`→`jo`，落入 `return true` |
| | 0x907ea | `LawnSaveGame` SaveGame.cpp:532（+0x79，:545 的 `WriteBufferToFile` 调用） | `return gSexyAppBase->WriteBufferToFile(theFilePath, &aContext.mBuffer);` | rel32 +0x28 改指向邻近函数，不写盘 |
| | 0x98440 | 1096 独有第三站点（`ret 0xC`→`push -1; push imm32`） | — | 无 1051/1073 对应，无法映射 |
| show_limbo_page | 0x31ce0 | `ChallengeScreen::ChallengeScreen(LawnApp*, ChallengePage)` ChallengeScreen.cpp:129-130 | `if (!ShowPageButtons() \|\| SURVIVAL \|\| PUZZLE) aPageButton->mVisible = false;` | `mov [eax+0x64],bl`→`cmp`（`Widget::mVisible` 在 +0x64） |

---

## §4 权威字段表

### 4.0 先决事实（与任务假设不同之处）

1. **`DataArray<T>`**（`Sexy.TodLib/DataArray.h`）：`mBlock +0; mMaxUsedCount +4; mMaxSize +8; mFreeListHead +0xC; mSize +0x10; mNextKey +0x14; mName +0x18`，共 0x1C。对应档案 `zombie_count_max = zombies+4`（mMaxUsedCount）、`plant_next_index = plants+0xC`（mFreeListHead）。`DataArrayItem = {T mItem; unsigned mID;}`，**stride = sizeof(T)+4 再按 alignof(T) 对齐**（Coin 因此是 0xD8）。
2. **GameObject**（Plant/Zombie/Projectile/Coin/SeedPacket/SeedBank/CursorObject 的基类，无 vtable）：`mApp 0x0, mBoard 0x4, mX 0x8, mY 0xC, mWidth 0x10, mHeight 0x14, mVisible 0x18, mRow 0x1C, mRenderOrder 0x20`，大小 0x24。
3. **Board** 继承 `Widget, ButtonListener`。Widget 大小无法从 re-plants 现代化框架算出，采用经验锚点：**1051 sizeof(Widget)=0x88、ButtonListener vptr @0x88、mApp @0x8C；GOTY 1096 sizeof(Widget)=0xA0、mApp @0xA4**。用此锚点，档案 29 个 Board 字段在两个版本上**全部精确命中**。
4. **LawnApp** 继承 SexyApp，锚点 `mBoard`：1051 0x768、GOTY 0x868。
5. Board 的 1051 偏移 = GOTY − 0x18（到 `mPrevBoardResult`）；从 `mTriggeredLawnMowers` 起 1051 = GOTY − 0x28（GOTY 插入了 6 个成就统计字段）。

### 4.1 Board（GOTY 1096）

| 字段 | 类型 | GOTY | 1051 | profile key |
|---|---|---|---|---|
| (Widget 基类) | | 0x00–0x9F | 0x00–0x87 | |
| (ButtonListener vptr) | | 0xA0 | 0x88 | |
| mApp | LawnApp* | 0xA4 | 0x8C | |
| mZombies | DataArray\<Zombie\> | 0xA8 | 0x90 | zombies ✓（+4 zombie_count_max） |
| mPlants | DataArray\<Plant\> | 0xC4 | 0xAC | plants ✓ |
| mProjectiles | DataArray\<Projectile\> | 0xE0 | 0xC8 | projectiles ✓ |
| mCoins | DataArray\<Coin\> | 0xFC | 0xE4 | items ✓ |
| mLawnMowers | DataArray\<LawnMower\> | 0x118 | 0x100 | mowers ✓ |
| mGridItems | DataArray\<GridItem\> | 0x134 | 0x11C | grid_items ✓ |
| mCursorObject | CursorObject* | 0x150 | 0x138 | cursor ✓ |
| mCursorPreview | CursorPreview* | 0x154 | 0x13C | |
| mAdvice | MessageWidget* | 0x158 | 0x140 | |
| mSeedBank | SeedBank* | 0x15C | 0x144 | seed_bank ✓ |
| mMenuButton / mStoreButton | GameButton* | 0x160 / 0x164 | 0x148 / 0x14C | |
| mIgnoreMouseUp | bool | 0x168 | 0x150 | |
| mToolTip | ToolTipWidget* | 0x16C | 0x154 | |
| mDebugFont | _Font* | 0x170 | 0x158 | |
| mCutScene | CutScene* | 0x174 | 0x15C | cutscene ✓ |
| mChallenge | Challenge* | 0x178 | 0x160 | challenge ✓ |
| mPaused | bool | 0x17C | 0x164 | paused ✓ |
| mGridSquareType | GridSquareType[9][6] | 0x180 | 0x168 | grid_type ✓ |
| mGridCelLook | int[9][6] | 0x258 | 0x240 | |
| mGridCelOffset | int[9][6][2] | 0x330 | 0x318 | |
| mGridCelFog | int[9][7] | 0x4E0 | 0x4C8 | |
| mEnableGraveStones | bool | 0x5DC | 0x5C4 | |
| mSpecialGraveStoneX/Y | int | 0x5E0 / 0x5E4 | 0x5C8 / 0x5CC | |
| mFogOffset | float | 0x5E8 | 0x5D0 | |
| mFogBlownCountDown | int | 0x5EC | 0x5D4 | |
| mPlantRow | PlantRowType[6] | 0x5F0 | 0x5D8 | row_type ✓ |
| mWaveRowGotLawnMowered | int[6] | 0x608 | 0x5F0 | |
| mBonusLawnMowersRemaining | int | 0x620 | 0x608 | |
| mIceMinX | int[6] | 0x624 | 0x60C | |
| mIceTimer | int[6] | 0x63C | 0x624 | ice_trail_cd ✓ |
| mIceParticleID | ParticleSystemID[6] | 0x654 | 0x63C | |
| mRowPickingArray | TodSmoothArray[6]（16B） | 0x66C | 0x654 | |
| mZombiesInWave | ZombieType[100][50] | 0x6CC | 0x6B4 | spawn_list ✓ |
| mZombieAllowed | bool[100] | 0x54EC | 0x54D4 | allowed_types ✓ |
| mSunCountDown | int | 0x5550 | 0x5538 | |
| mNumSunsFallen | int | 0x5554 | 0x553C | |
| mShakeCounter / X / Y | int | 0x5558 / 0x555C / 0x5560 | −0x18 | |
| mBackground | BackgroundType | 0x5564 | 0x554C | scene ✓ |
| mLevel | int | 0x5568 | 0x5550 | adventure_level ✓ |
| mSodPosition | int | 0x556C | 0x5554 | |
| mPrevMouseX/Y | int | 0x5570 / 0x5574 | −0x18 | |
| mSunMoney | int | 0x5578 | 0x5560 | sun ✓ |
| mNumWaves | int | 0x557C | 0x5564 | wave_total ✓ |
| mMainCounter | int | 0x5580 | 0x5568 | main_counter ✓ |
| mEffectCounter | int | 0x5584 | 0x556C | global_counter ✓ |
| mDrawCount | int | 0x5588 | 0x5570 | |
| mRiseFromGraveCounter | int | 0x558C | 0x5574 | |
| mOutOfMoneyCounter | int | 0x5590 | 0x5578 | |
| mCurrentWave | int | 0x5594 | 0x557C | wave ✓ |
| mTotalSpawnedWaves | int | 0x5598 | 0x5580 | |
| mTutorialState | TutorialState | 0x559C | 0x5584 | |
| mTutorialParticleID | ParticleSystemID | 0x55A0 | 0x5588 | |
| mTutorialTimer | int | 0x55A4 | 0x558C | |
| mLastBungeeWave | int | 0x55A8 | 0x5590 | |
| mZombieHealthToNextWave | int | 0x55AC | 0x5594 | wave_hp_threshold ✓ |
| mZombieHealthWaveStart | int | 0x55B0 | 0x5598 | |
| mZombieCountDown | int | 0x55B4 | 0x559C | countdown ✓ |
| mZombieCountDownStart | int | 0x55B8 | 0x55A0 | countdown_start ✓ |
| mHugeWaveCountDown | int | 0x55BC | 0x55A4 | huge_wave_countdown ✓ |
| mHelpDisplayed | bool[68] | 0x55C0 | 0x55A8 | |
| mHelpIndex | AdviceType | 0x5604 | 0x55EC | |
| mFinalBossKilled | bool | 0x5608 | 0x55F0 | |
| mShowShovel | bool | 0x5609 | 0x55F1 | |
| mCoinBankFadeCount | int | 0x560C | 0x55F4 | |
| mDebugTextMode | DebugTextMode | 0x5610 | 0x55F8 | debug_mode ✓ |
| mLevelComplete | bool | 0x5614 | 0x55FC | |
| mBoardFadeOutCounter | int | 0x5618 | 0x5600 | |
| mNextSurvivalStageCounter | int | 0x561C | 0x5604 | |
| mScoreNextMowerCounter | int | 0x5620 | 0x5608 | |
| mLevelAwardSpawned | bool | 0x5624 | 0x560C | |
| mProgressMeterWidth | int | 0x5628 | 0x5610 | |
| mFlagRaiseCounter | int | 0x562C | 0x5614 | |
| mIceTrapCounter | int | 0x5630 | 0x5618 | |
| mBoardRandSeed | int | 0x5634 | 0x561C | |
| mPoolSparklyParticleID | ParticleSystemID | 0x5638 | 0x5620 | particle_system_ref ✓ |
| mFwooshID | ReanimationID[6][12] | 0x563C | 0x5624 | |
| mFwooshCountDown | int | 0x575C | 0x5744 | |
| mTimeStopCounter | int | 0x5760 | 0x5748 | |
| mDroppedFirstCoin | bool | 0x5764 | 0x574C | |
| mFinalWaveSoundCounter | int | 0x5768 | 0x5750 | |
| mCobCannonCursorDelayCounter | int | 0x576C | 0x5754 | |
| mCobCannonMouseX / Y | int | 0x5770 / 0x5774 | 0x5758 / 0x575C | |
| mKilledYeti | bool | 0x5778 | 0x5760 | |
| mMustacheMode … mSukhbirMode | bool×7 | 0x5779–0x577F | 0x5761–0x5767 | |
| mPrevBoardResult | BoardResult | 0x5780 | 0x5768 | |
| mPeaShooterUsed / mCatapultPlantsUsed (GOTY) | bool | 0x5784 / 0x5785 | — | |
| mLevelCoinsCollected (GOTY) | int | 0x5788 | — | |
| mGargantuarsKillsByCornCob (GOTY) | int | 0x578C | — | |
| mMushroomAndCoffeeBeansOnly / mMushroomsUsed (GOTY) | bool | 0x5790 / 0x5791 | — | |
| mTriggeredLawnMowers | int | 0x5794 | 0x576C | |
| mPlayTimeActiveLevel / Inactive | int | 0x5798 / 0x579C | 0x5770 / 0x5774 | |
| mMaxSunPlants | int | 0x57A0 | 0x5778 | |
| mStartDrawTime / mIntervalDrawTime | DWORD | 0x57A4 / 0x57A8 | 0x577C / 0x5780 | |
| mIntervalDrawCountStart | int | 0x57AC | 0x5784 | |
| mMinFPS | float | 0x57B0 | 0x5788 | |
| mPreloadTime | int | 0x57B4 | 0x578C | |
| mGameID | intptr_t | 0x57B8 | 0x5790 | |
| mGravesCleared | int | 0x57BC | 0x5794 | |
| mPlantsEaten | int | 0x57C0 | 0x5798 | |
| mPlantsShoveled | int | 0x57C4 | 0x579C | |
| mCoinsCollected | int | 0x57C8 | 0x57A0 | |
| mDiamondsCollected | int | 0x57CC | 0x57A4 | |
| mPottedPlantsCollected | int | 0x57D0 | 0x57A8 | |
| mChocolateCollected | int | 0x57D4 | 0x57AC | |
| sizeof(Board) | | **0x57D8** | **0x57B0** | |

### 4.2 Plant（1051 与 GOTY 相同；stride 0x14C ✓）

| 字段 | 类型 | 偏移 | profile |
|---|---|---|---|
| GameObject | | 0x00–0x20 | row = 0x1C ✓ |
| mSeedType | SeedType | 0x24 | type ✓ |
| mPlantCol | int | 0x28 | col ✓ |
| mAnimCounter / mFrame / mFrameLength / mNumFrames | int | 0x2C / 0x30 / 0x34 / 0x38 | |
| mState | PlantState | 0x3C | state ✓ |
| mPlantHealth | int | 0x40 | hp ✓ |
| mPlantMaxHealth | int | 0x44 | max_hp ✓ |
| mSubclass | int | 0x48 | |
| mDisappearCountdown | int | 0x4C | |
| mDoSpecialCountdown | int | 0x50 | |
| mStateCountdown | int | 0x54 | （建议新增 `state_countdown`） |
| mLaunchCounter / mLaunchRate | int | 0x58 / 0x5C | |
| mPlantRect / mPlantAttackRect | Rect | 0x60 / 0x70 | |
| mTargetX / mTargetY | int | 0x80 / 0x84 | |
| mStartRow | int | 0x88 | |
| mParticleID | ParticleSystemID | 0x8C | |
| mShootingCounter | int | 0x90 | |
| mBodyReanimID | ReanimationID | 0x94 | |
| mHeadReanimID / 2 / 3 | ReanimationID | 0x98 / 0x9C / 0xA0 | |
| mBlinkReanimID / mLightReanimID / mSleepingReanimID | ReanimationID | 0xA4 / 0xA8 / 0xAC | |
| mBlinkCountdown | int | 0xB0 | |
| mRecentlyEatenCountdown | int | 0xB4 | |
| mEatenFlashCountdown | int | 0xB8 | |
| mBeghouledFlashCountdown | int | 0xBC | |
| mShakeOffsetX / Y | float | 0xC0 / 0xC4 | |
| mMagnetItems | MagnetItem[5]（20B） | 0xC8 | |
| mTargetZombieID | ZombieID | 0x12C | |
| mWakeUpCounter | int | 0x130 | |
| mOnBungeeState | PlantOnBungeeState | 0x134 | |
| mImitaterType | SeedType | 0x138 | imitater_type ✓ |
| mPottedPlantIndex | int | 0x13C | |
| mAnimPing | bool | 0x140 | |
| mDead | bool | 0x141 | dead ✓ |
| mSquished | bool | 0x142 | squished ✓ |
| mIsAsleep | bool | 0x143 | asleep ✓ |
| mIsOnBoard | bool | 0x144 | |
| mHighlighted | bool | 0x145 | |
| sizeof | | 0x148（+4 mID = 0x14C） | |

### 4.3 Zombie（GOTY 1096；1051 到 0x147 相同，之后无 `mZombatarHeadReanimID`：0x148 mFireballRow、0x14C mIsFireBall、0x150 mMoweredReanimID、0x154 mLastPortalX，sizeof 0x158，stride 0x15C）

| 字段 | 类型 | GOTY | profile |
|---|---|---|---|
| GameObject | | 0x00–0x20 | row = 0x1C ✓ |
| mZombieType | ZombieType | 0x24 | type ✓ |
| mZombiePhase | ZombiePhase | 0x28 | status ✓ |
| mPosX / mPosY | float | 0x2C / 0x30 | x ✓ / y ✓ |
| mVelX | float | 0x34 | speed ✓ |
| mAnimCounter / mGroanCounter | int | 0x38 / 0x3C | |
| mAnimTicksPerFrame / mAnimFrames / mFrame / mPrevFrame | int | 0x40 / 0x44 / 0x48 / 0x4C | |
| mVariant / mIsEating | bool | 0x50 / 0x51 | |
| mJustGotShotCounter | int | 0x54 | |
| mShieldJustGotShotCounter / mShieldRecoilCounter | int | 0x58 / 0x5C | |
| mZombieAge | int | 0x60 | |
| mZombieHeight | ZombieHeight | 0x64 | |
| mPhaseCounter | int | 0x68 | |
| mFromWave | int | 0x6C | |
| mDroppedLoot | bool | 0x70 | |
| mZombieFade | int | 0x74 | |
| mFlatTires | bool | 0x78 | |
| mUseLadderCol | int | 0x7C | |
| mTargetCol | int | 0x80 | |
| mAltitude | float | 0x84 | |
| mHitUmbrella | bool | 0x88 | |
| mZombieRect / mZombieAttackRect | Rect | 0x8C / 0x9C | |
| mChilledCounter / mButteredCounter / mIceTrapCounter | int | 0xAC / 0xB0 / 0xB4 | |
| mMindControlled / mBlowingAway / mHasHead / mHasArm / mHasObject / mInPool / mOnHighGround / mYuckyFace | bool | 0xB8–0xBF | |
| mYuckyFaceCounter | int | 0xC0 | |
| mHelmType | HelmType | 0xC4 | |
| mBodyHealth / mBodyMaxHealth | int | 0xC8 / 0xCC | hp ✓ / max_hp ✓ |
| mHelmHealth / mHelmMaxHealth | int | 0xD0 / 0xD4 | helmet_hp ✓ |
| mShieldType | ShieldType | 0xD8 | |
| mShieldHealth / mShieldMaxHealth | int | 0xDC / 0xE0 | shield_hp ✓ |
| mFlyingHealth / mFlyingMaxHealth | int | 0xE4 / 0xE8 | |
| mDead | bool | 0xEC | dead ✓ |
| mRelatedZombieID | ZombieID | 0xF0 | |
| mFollowerZombieID | ZombieID[4] | 0xF4 | |
| mPlayingSong | bool | 0x104 | |
| mParticleOffsetX / Y | int | 0x108 / 0x10C | |
| mAttachmentID | AttachmentID | 0x110 | |
| mSummonCounter | int | 0x114 | |
| mBodyReanimID | ReanimationID | 0x118 | |
| mScaleZombie / mVelZ / mOriginalAnimRate | float | 0x11C / 0x120 / 0x124 | |
| mTargetPlantID | PlantID | 0x128 | |
| mBossMode / mTargetRow / mBossBungeeCounter / mBossStompCounter / mBossHeadCounter | int | 0x12C–0x13C | |
| mBossFireBallReanimID / mSpecialHeadReanimID | ReanimationID | 0x140 / 0x144 | |
| mZombatarHeadReanimID (GOTY) | ReanimationID | 0x148 | |
| mFireballRow | int | 0x14C | |
| mIsFireBall | bool | 0x150 | |
| mMoweredReanimID | ReanimationID | 0x154 | |
| mLastPortalX | int | 0x158 | |
| mHasZombatar (GOTY) | bool | 0x15C | |
| mZombatarIndex (GOTY) | int | 0x160 | |
| sizeof | | 0x164（+4 mID = 0x168 ✓） | |

### 4.4 LawnApp（相对 `mBoard`；1051 基 0x768、GOTY 0x868）

| 字段 | 相对偏移 | 1051 | GOTY 计算 | GOTY 档案 | 备注 |
|---|---|---|---|---|---|
| mBoard | +0x0 | 0x768 ✓ | 0x868 | 0x868 ✓ | |
| mTitleScreen | +0x4 | 0x76C | 0x86C | | |
| mGameSelector | +0x8 | 0x770 ✓ | 0x870 | 0x870 ✓ | |
| mSeedChooserScreen / mAwardScreen / mCreditScreen / mChallengeScreen | +0xC–+0x18 | | | | |
| mSoundSystem | +0x1C | | | | |
| mControlButtonList (std::list) | +0x20 | | | | |
| mCreatedImageList | +0x2C | | | | |
| mReferId / mRegisterLink / mMod (std::string 0x1C) | +0x38 / +0x54 / +0x70 | | | | |
| mRegisterResourcesLoaded | +0x8C | | | | |
| mTodCheatKeys | +0x8D | 0x7F5 ✓ | 0x8F5 | **0x915** | **MISMATCH +0x20** |
| mGameMode | +0x90 | 0x7F8 ✓ | 0x8F8 | **0x918** | 同上 |
| mGameScene (game_ui) | +0x94 | 0x7FC ✓ | 0x8FC | **0x91C** | 同上 |
| mLoadingZombiesThreadCompleted / mFirstTimeGameSelector | +0x98 / +0x99 | | | | |
| mGamesPlayed / mMaxExecutions / mMaxPlays / mMaxTime | +0x9C–+0xA8 | | | | |
| mEasyPlantingCheat (free_planting) | +0xAC | 0x814 ✓ | 0x914 | **0x934** | 同上 |
| mPoolEffect / mZenGarden | +0xB0 / +0xB4 | | | | |
| mEffectSystem | +0xB8 | 0x820 ✓ | 0x920 | **0x940** | 同上 |
| mReanimatorCache / mProfileMgr | +0xBC / +0xC0 | | | | |
| mPlayerInfo | +0xC4 | 0x82C ✓ | 0x92C | **0x94C** | 同上 |
| mLastLevelStats / mCloseRequest / mAppCounter | +0xC8–+0xD0 | | | | |
| mMusic | +0xD4 | 0x83C ✓ | 0x93C | **0x95C** | 同上 |
| mCrazyDave* … mAppRandSeed | +0xD8–+0x108 | | | | |
| mBigArrowCursor / mDRM / mSessionID | +0x10C / +0x110 / +0x114 | | | | |
| mPlayTimeActiveSession / Inactive / mBoardResult / mSawYeti | +0x118–+0x124 | | | | |
| TypingCheck*×10 | +0x128–+0x14C | | | | |
| 7 个模式 bool | +0x150–+0x156 | | | | |
| mTrialType / mDebugTrialLocked / mMuteSoundsForCutscene | +0x158 / +0x15C / +0x15D | | | | |

结论：GOTY 在 `mGameSelector`(+0x8) 与 `mTodCheatKeys`(+0x8D) 之间多了 **0x20 字节**（最可能是新增的屏幕指针，如 Zombatar/成就界面），re-plants 未建模。**GOTY LawnApp 偏移以档案为准**。`frame_duration`（0x454 / 0x4B4）在 SexyAppBase 内（`mFrameTime`），re-plants 不可枚举。

### 4.5 SeedPacket / SeedBank

**SeedPacket**（sizeof 0x50 ✓，是 SeedBank 内的普通数组 `mSeedPackets[10]`，不是 DataArray）：

| 字段 | 类型 | 偏移 | profile |
|---|---|---|---|
| GameObject | | 0x00–0x20 | |
| mRefreshCounter | int | 0x24 | cd_elapsed ✓ |
| mRefreshTime | int | 0x28 | cd_total ✓ |
| mIndex | int | 0x2C | |
| mOffsetX | int | 0x30 | |
| mPacketType | SeedType | 0x34 | type ✓ |
| mImitaterType | SeedType | 0x38 | imitater_type ✓ |
| mSlotMachineCountDown | int | 0x3C | |
| mSlotMachiningNextSeed | SeedType | 0x40 | |
| mSlotMachiningPosition | float | 0x44 | |
| mActive | bool | 0x48 | usable ✓ |
| mRefreshing | bool | 0x49 | |
| mTimesUsed | int | 0x4C | |

**SeedBank**：GameObject；mNumPackets 0x24（count ✓）；mSeedPackets[10] 0x28（packets ✓）；mCutSceneDarken 0x348；mConveyorBeltCounter 0x34C；sizeof 0x350。

### 4.6 其他结构（1051/GOTY 相同）

- **Projectile**（sizeof 0x90，stride 0x94 ✓）：mFrame 0x24；mNumFrames 0x28；mAnimCounter 0x2C；mPosX/Y/Z 0x30/0x34/0x38；mVelX/Y/Z 0x3C/0x40/0x44；mAccZ 0x48；mShadowY 0x4C；mDead 0x50；mAnimTicksPerFrame 0x54；mMotionType 0x58；mProjectileType 0x5C；mProjectileAge 0x60；mClickBackoffCounter 0x64；mRotation 0x68；mRotationSpeed 0x6C；mOnHighGround 0x70；mDamageRangeFlags 0x74；mHitTorchwoodGridX 0x78；mAttachmentID 0x7C；mCobTargetX(float) 0x80；mCobTargetRow 0x84；mTargetZombieID 0x88；mLastPortalX 0x8C。
- **Coin**（sizeof 0xD0，align 8，stride 0xD8 ✓）：mPosX 0x24；mPosY 0x28；mVelX 0x2C；mVelY 0x30；mScale 0x34；mDead 0x38；mFadeCount 0x3C；mCollectX/Y 0x40/0x44；mGroundY 0x48；mCoinAge 0x4C；mIsBeingCollected 0x50；mDisappearCounter 0x54；mType 0x58；mCoinMotion 0x5C；mAttachmentID 0x60；mCollectionDistance 0x64；mUsableSeedType 0x68；mPottedPlantSpec 0x70（0x58）；mNeedsBouncyArrow 0xC8；mHasBouncyArrow 0xC9；mHitGround 0xCA；mTimesDropped 0xCC。
- **LawnMower**（无 GameObject 基类；sizeof 0x44，stride 0x48 ✓）：mApp 0x0；mBoard 0x4；mPosX 0x8；mPosY 0xC；mRenderOrder 0x10；mRow 0x14；mAnimTicksPerFrame 0x18；mReanimID 0x1C；mChompCounter 0x20；mRollingInCounter 0x24；mSquishedCounter 0x28；mMowerState 0x2C；mDead 0x30 ✓；mVisible 0x31；mMowerType 0x34；mAltitude 0x38；mMowerHeight 0x3C；mLastPortalX 0x40。
- **GridItem**（sizeof 0xE8，stride 0xEC ✓）：mGridItemType 0x8 ✓；mGridItemState 0xC；mGridX 0x10（col ✓）；mGridY 0x14（row ✓）；mGridItemCounter 0x18；mRenderOrder 0x1C；mDead 0x20 ✓；mPosX/Y 0x24/0x28；mGoalX/Y 0x2C/0x30；mGridItemReanimID 0x34；mGridItemParticleID 0x38；mZombieType 0x3C；mSeedType 0x40；mScaryPotType 0x44；mHighlighted 0x48；mTransparentCounter 0x4C；mSunCount 0x50；mMotionTrailFrames[12] 0x54；mMotionTrailCount 0xE4。
- **CursorObject**（sizeof 0x4C）：mSeedBankIndex 0x24；mType 0x28；mImitaterType 0x2C；mCursorType 0x30（grab ✓）；mCoinID 0x34；mGlovePlantID 0x38；mDuplicatorPlantID 0x3C；mCobCannonPlantID 0x40；mHammerDownCounter 0x44；mReanimCursorID 0x48。
- **Challenge**（按原始 bool 类型；re-plants 把 Beghouled 数组改成 int 是错的）：mBeghouledMouseCapture 0x8；mBeghouledMouseDownX/Y 0xC/0x10；mBeghouledEated bool[9][6] 0x14；mBeghouledPurcasedUpgrade bool[3] 0x4A；mBeghouledMatchesThisMove 0x50；mChallengeState 0x54；mChallengeStateCounter 0x58；mConveyorBeltCounter 0x5C；mChallengeScore 0x60；mShowBowlingLine 0x64；mLastConveyorSeedType 0x68；**mSurvivalStage 0x6C（endless_round ✓）**；mSlotMachineRollCount 0x70；mReanimChallenge 0x74；mReanimClouds[6] 0x78；mCloudsCounter[6] 0x90；mChallengeGridX/Y 0xA8/0xAC；mScaryPotterPots 0xB0；mRainCounter 0xB4；mTreeOfWisdomTalkIndex 0xB8。
- **PlayerInfo（GOTY）**：re-plants 声明 `bool mEarnedAchievements[20]`，但档案（achievements_count=21）要求 **[21]**，之后所有字段比 re-plants 的注释 +4：mName 0x0；mUseSeq 0x1C；mId 0x20；mEarnedAchievements[21] 0x24 ✓；mShownAchievements[21] 0x39；mLevel 0x50 ✓；mCoins 0x54 ✓；mFinishedAdventure 0x58 ✓；mChallengeRecords[100] 0x5C ✓；mPurchases[80] 0x1EC ✓；mPlayTimeActivePlayer 0x32C；… mNumPottedPlants 0x37C；mPottedPlant[200]（0x58，align 8）0x388；mNumZombatars 0x4848；mZombatars[100]（0x48）0x484C；mMiniGamesCompleted[20] 0x646C。`tree_height 0x120 = mChallengeRecords[49]` ✓。**1051 PlayerInfo** 无成就数组：mLevel 0x24、mCoins 0x28、mFinishedAdventure 0x2C、mChallengeRecords 0x30、mPurchases 0x1C0 ✓、tree_height 0xF4 ✓。

### 4.7 匹配/不匹配汇总

- **匹配**：Board 29/29（1051 + 1096）；Plant 10/10 + stride；Zombie 11/11 + stride（含 GOTY Zombatar 字段）；SeedPacket、SeedBank、LawnMower、GridItem、CursorObject；Projectile/Coin stride；LawnApp 1051 9/9；PlayerInfo（按 21 成就修正后）。
- **不匹配**：
  - (a) LawnApp GOTY：`mGameSelector`→`mTodCheatKeys` 之间 +0x20，re-plants 未建模；用档案值。
  - (b) PlayerInfo：re-plants 成就数 20，GOTY 需 21（+4 位移）。
  - (c) Challenge：re-plants 把 Beghouled bool 改 int；原始 bool 布局才与档案一致。
  - (d) Widget（0x88/0xA0）与 SexyApp（0x768/0x868）基类大小无法从现代化框架推出，属经验锚点。
  - (e) DataArray 成员顺序与此前设计假设不同（见 4.0）。

---

## §5 出怪列表语义（源码确认）

- `Board.h` 常量：`MAX_GRID_SIZE_X 9`、`MAX_GRID_SIZE_Y 6`、`MAX_ZOMBIES_IN_WAVE 50`、`MAX_ZOMBIE_WAVES 100`、`MAX_GRAVE_STONES 54`、`MAX_POOL_GRID_SIZE 10`、`NUM_ADVICE_TYPES 68`、`SEEDBANK_MAX 10`、`MAX_MAGNET_ITEMS 5`、`MAX_ZOMBIE_FOLLOWERS 4`、`NUM_MOTION_TRAIL_FRAMES 12`。
- `mZombiesInWave` 是 `ZombieType[100][50]`（4 字节枚举，共 0x4E20 字节；1051 0x6B4..0x54D4，GOTY 0x6CC..0x54EC）。`mZombieAllowed` 是 `bool[100]`（NUM_ZOMBIE_TYPES = 33，含 ZOMBIE_ZOMBATAR；数组过大）。
- 哨兵：`ZombieType::ZOMBIE_INVALID = -1`（ConstEnums.h:1349）。
- `Board::PickZombieWaves()`（Board.cpp ~620）：**只写终止符，不整行填充**。每波先 `mZombiesInWave[aWave][0] = ZOMBIE_INVALID`；每次 `PutZombieInWave()` 在 `[mZombieCount++]` 写类型，若 `mZombieCount < 50` 再在下一槽写 `-1`。终止符之后的槽保持原值（Board 用 `new` 分配、无 memset；重选关卡/生存阶段会残留旧波数据）。随机填充循环上界 `while (points > 0 && count < MAX_ZOMBIES_IN_WAVE)`。
- 所有读取方（`NumberZombiesInWave`、`SpawnZombieWave`、CutScene、Challenge）在首个 -1 处停止；50 槽内无 -1 则 `NumberZombiesInWave` 断言。`>= mNumWaves` 的波从不被触碰。
- 对 pvztools 的含义：读出怪列表必须在第一个 -1 处截断，不能信任其后的数据；写入时须在最后一个元素之后写 -1（且最多 49 个有效元素 + 1 哨兵）。

---

## §6 玉米炮发射规范（retail + GOTY）

### 6.1 调用链（玩家点击）

1. `Plant::MouseDown(int x,int y,int theClickCount)`（retail 0x466390）：`mState == STATE_COBCANNON_READY` 时 `ClearCursor()`，`mCursorObject->mCursorType = CURSOR_TYPE_COBCANNON_TARGET`（= **8**：NORMAL 0、PLANT_FROM_BANK 1、USABLE_COIN 2、GLOVE 3、DUPLICATOR 4、WHEEL_BARROW 5、SHOVEL 6、HAMMER 7、COBCANNON_TARGET 8），`mCobCannonPlantID`（CursorObject +0x40）= 植物 DataArray ID，`mBoard->mCobCannonCursorDelayCounter = 30`（Board GOTY +0x576C / 1051 +0x5754），`mCobCannonMouseX/Y = x,y`。
2. `Board::MouseDown` → 命中为 OBJECT_TYPE_NONE 且光标为 COBCANNON_TARGET → `Board::MouseDownCobcannonFire(int x,int y,int theClickCount)`（retail 0x40FC70）：要求 `theClickCount >= 0 && y >= 80`；误点保护 `mCobCannonCursorDelayCounter > 0 && Distance2D(...) < 100` 则返回；光标不能是 PLANT_FROM_DUPLICATOR；`Plant* p = mPlants.DataArrayTryToGet(mCobCannonPlantID); if (p) p->CobCannonFire(x, y); ClearCursor();`。
3. **`void Plant::CobCannonFire(int theTargetX, int theTargetY)`**（retail **0x466D50**，GOTY Steam **0x46D1F0**）：
   ```
   TOD_ASSERT(mState == STATE_COBCANNON_READY);   // 仅断言，release 不检查
   mState = STATE_COBCANNON_FIRING;               // [+0x3C] = 38
   mShootingCounter = 206;                        // [+0x90]
   PlayBodyReanim("anim_shooting", REANIM_PLAY_ONCE_AND_HOLD(=3), 20, 12.0f);
   mTargetX = theTargetX - 47;                    // [+0x80]（浮点减后 _ftol）
   mTargetY = theTargetY;                         // [+0x84]
   track "CobCannon_Cob" 颜色 = 白
   ```
   不经 `DoSpecial`。**release 下既不检查就绪状态也不检查光标**，远程调用者必须自己检查 `mState == 37`。
4. 弹体在 `Plant::UpdateShooting` 中 `mShootingCounter` 到 1 时（调用后 205 cs）生成：`Fire(nullptr,mRow,WEAPON_PRIMARY)` → `mCobTargetX = mTargetX - 40`、`mCobTargetRow = PixelToGridYKeepOnBoard(mTargetX, mTargetY)`。落地：`mPosX = mCobTargetX`，`mPosY = GridToPixelY(PixelToGridXKeepOnBoard(mCobTargetX,0), mCobTargetRow)`，爆炸 `KillAllZombiesInRadius(mRow, mPosX+80, mPosY+40, 115, 上下各 1 行)`。因此点击 x 决定爆心（≈ x − 87 + 80 = **x − 7 px**），点击 y 只决定行。

### 6.2 PlantState 枚举（Plant.h，0 起）

STATE_NOTREADY 0、READY 1、DOINGSPECIAL 2、SQUASH_LOOK 3 … TANGLEKELP_GRABBING 34、**COBCANNON_ARMING 35、COBCANNON_LOADING 36、COBCANNON_READY 37、COBCANNON_FIRING 38**、KERNELPULT_BUTTER 39 …（GOTY 二进制在 CobCannonFire 写 `0x26`=38 到 [plant+0x3C]，验证编号）。

### 6.3 冷却机制（3475 cs）

- 放置/升级（Plant 构造，`IsInPlay()`）：`mState = ARMING(35)`，`mStateCountdown(+0x54) = 500`。
- 发射后 "anim_shooting" 播完（`mLoopCount > 0`，Plant.cpp:3446）：`mState = ARMING`，`mStateCountdown = 3000`，播 "anim_unarmed_idle"。
- `Plant::Update`：`mStateCountdown > 0` 则每 cs −1。
- `Plant::UpdateCobCannon`（retail 0x461020，GOTY 0x4649F0）：ARMING 且 countdown==0 → LOADING(36) + "anim_charge"（once-and-hold，12 fps）；LOADING 且动画完 → READY(37)；READY 闪烁炮弹；FIRING 在动画 48% 处播 FOLEY_COB_LAUNCH。
- 发射→就绪 = 射击动画 + 3000 + 装填动画 = **3475**（初次放置 500 + 装填 ≈ 625）。工具侧就绪判定：`plant[+0x3C] == 37`；`== 35/36` 时可用 +0x54 估算剩余时间。弹体 205 cs 出膛；社区的 ~373 cs 飞行是弹体侧数值，不在此函数内。

### 6.4 格子 ↔ 像素（Board.cpp；LAWN_XMIN=40、LAWN_YMIN=80、HIGH_GROUND_HEIGHT=30）

```
GridToPixelX(col,row) = col*80 + 40                               // retail 0x41C680
GridToPixelY(col,row):                                            // retail 0x41C740
  roof      : row*85 + (col<5 ? (5-col)*20 : 0) + 80 - 10
  pool/fog  : row*85 + 80
  其他      : row*100 + 80
  若 col != -1 且 mGridSquareType[col][row] == GRIDSQUARE_HIGH_GROUND 再 -30
PixelToGridX(x,y) = x<40 ? -1 : clamp((x-40)/80, 0, 8)           // KeepOnBoard 钳到 >=0
PixelToGridY(x,y):
  y<80 或 col==-1 → -1
  roof : if col<5: y -= (4-col)*20 ; clamp((y-80)/85, 0, 4)
  pool : clamp((y-80)/85, 0, 5)
  else : clamp((y-80)/100, 0, 4)
PixelToGridYKeepOnBoard(x,y) = max(PixelToGridY(max(x,80), y), 0)
```

远程发炮的实用目标：x = 期望爆心像素 + 7；y = `GridToPixelY(col,row) + ~40`（行带内任意 y；`y >= 80` 只在鼠标路径要求，`CobCannonFire` 本身不要求）。

### 6.5 调用约定证据

**retail 1.0.0.1051（AvZ `avz_avz_asm.cpp` `AAsm::Fire(x,y,rank)`）：**
```
mov eax,[0x6A9EC0]; mov edi,[eax+0x768]; mov eax,[edi+0xAC]; imul ecx,rank,0x14C; add eax,ecx   ; eax = Plant*
push y ; push x ; mov edx,0x466D50 ; call edx                                                     ; 之后无栈修正
```
→ Plant* 在 **EAX**，参数 **先 push y 再 push x**（入口 x 在 [esp+4]），**被调方清栈（ret 8）**。

**GOTY（Steam）0x46D1F0 原始字节：**
```
51                      push ecx
D9 05 B0 60 70 00       fld dword [0x7060B0]   ; 12.0f
53 56                   push ebx ; push esi
51 D9 1C 24             push ecx ; fstp [esp]  ; float 12.0
8B F0                   mov esi, eax           ; *** Plant* 由 EAX 传入 ***
6A 03                   push 3
68 C4 29 6F 00          push "anim_shooting"   ; 0x6F29C4
B9 14 00 00 00          mov ecx, 20
C7 46 3C 26 00 00 00    mov [esi+0x3C], 38     ; mState = FIRING
C7 86 90 00 00 00 CE..  mov [esi+0x90], 206    ; mShootingCounter
E8 1F 8F FF FF          call PlayBodyReanim (0x466112)
DB 44 24 10             fild dword [esp+0x10]  ; = [entry_esp+4] = theTargetX
DC 25 58 62 70 00       fsub qword [0x706258]  ; 47.0
E8 F0 35 25 00          call _ftol
8B 0E                   mov ecx,[esi]
89 86 80 00 00 00       mov [esi+0x80], eax    ; mTargetX
8B 44 24 14             mov eax,[esp+0x14]     ; = [entry_esp+8] = theTargetY
89 86 84 00 00 00       mov [esi+0x84], eax    ; mTargetY
...                     GetTrackInstanceByName "CobCannon_Cob" → 白色
5E 89 50 0C 5B 59       pop esi ; mov [eax+0xC],edx ; pop ebx ; pop ecx
C2 08 00                ret 8                  ; @0x46D29C
```
.text 中唯一调用者 **0x41323B**（GOTY `Board::MouseDownCobcannonFire` 内）：`mov ecx,[eax+0x40]`(mCobCannonPlantID)；`lea edx,[edi+0xC4]`(mPlants)；call DataArrayTryToGet → eax=Plant*；`test eax,eax`；`mov ecx,[ebp+8]`；`push esi`(y)；`push ecx`(x)；`call 0x46D1F0`；调用者自身 `ret 0Ch`。与 retail 约定一致。

### 6.6 最终规范

```
cob_fire(Plant* plant, int x, int y)
  retail 1.0.0.1051 : VA 0x466D50
  GOTY (Steam)      : VA 0x46D1F0   （0x4126F0 不是——那是 Board::MouseDownWithPlant）
  plant  -> EAX（MSVC 自定义寄存器约定；ECX 被用作 blend-time，不是 this）
  args   -> push y ; push x（入口 x=[esp+4]，y=[esp+8]）
  stack  -> 被调方清栈 ret 8（调用者不得 add esp,8）
  clobbers eax, ecx, edx（及 FPU st0）；保留 ebx, esi, ebp, edi
  前置条件（调用者负责）：plant->mSeedType == 47 (SEED_COBCANNON)，plant->mState(+0x3C) == 37；release 不校验
  效果：mState=38，mShootingCounter(+0x90)=206，mTargetX(+0x80)=x-47，mTargetY(+0x84)=y；
        205 cs 后生成弹体，落在 row=PixelToGridYKeepOnBoard(x-47,y)，爆心 px=(x-87)+80，半径 115，上下各 1 行
  植物数组：stride 0x14C；Board 内 mPlants 在 +0xAC（1051）/ +0xC4（GOTY）；Board = [[0x6A9EC0]+0x768]（retail）
```

---

## §7 对 pvztools 的行动项

### 7.1 档案字段：新增 / 改名

| 结构 | 动作 | 字段 | 偏移 | 理由 |
|---|---|---|---|---|
| Plant | 新增 | `state_countdown` | 0x54 | 玉米炮/土豆雷/阳光菇剩余时间估算（§6.3） |
| Plant | 新增 | `shooting_counter` | 0x90 | 发射后 205 cs 出膛判定 |
| Plant | 新增 | `target_x` / `target_y` | 0x80 / 0x84 | 验证 cob_fire 写入 |
| Plant | 新增 | `disappear_countdown` / `do_special_countdown` | 0x4C / 0x50 | 樱桃/辣椒/毁灭菇即爆判定 |
| Plant | 新增 | `target_zombie_id` | 0x12C | 磁力菇/大嘴花/缠绕水草 |
| Plant | 新增 | `is_on_board` / `highlighted` | 0x144 / 0x145 | |
| Zombie | 新增 | `phase_counter` | 0x68 | 小丑/辣椒头爆炸倒计时 |
| Zombie | 新增 | `from_wave` | 0x6C | 区分过场僵尸（-1 = ZOMBIE_WAVE_CUTSCENE） |
| Zombie | 新增 | `altitude` / `vel_z` | 0x84 / 0x120 | 气球/蹦极 |
| Zombie | 新增 | `chilled_counter` / `buttered_counter` / `ice_trap_counter` | 0xAC / 0xB0 / 0xB4 | 减速/黄油/冰冻剩余 |
| Zombie | 新增 | `mind_controlled` / `has_head` / `has_arm` / `has_object` / `in_pool` | 0xB8 / 0xBA / 0xBB / 0xBC / 0xBD | |
| Zombie | 新增 | `helmet_max_hp` / `shield_max_hp` / `flying_hp` / `flying_max_hp` | 0xD4 / 0xE0 / 0xE4 / 0xE8 | |
| Zombie | 新增 | `helm_type` / `shield_type` | 0xC4 / 0xD8 | |
| Zombie | 新增 | `target_col` / `target_row` | 0x80 / 0x130 | 蹦极/投石车 |
| Zombie | 新增 | `related_zombie_id` / `follower_zombie_ids` | 0xF0 / 0xF4 | 雪橇队 |
| Zombie | 新增 | `fireball_row` / `is_fireball`（GOTY 0x14C/0x150；1051 0x148/0x14C）、`zombatar_head_reanim`（GOTY 0x148） | 版本相关 | |
| Board | 新增 | `cob_cursor_delay` / `cob_mouse_x` / `cob_mouse_y` | GOTY 0x576C/0x5770/0x5774；1051 0x5754/0x5758/0x575C | 玉米炮光标路径 |
| Board | 新增 | `ice_min_x` | GOTY 0x624 / 1051 0x60C | 冰道起点 |
| Board | 新增 | `level_complete` / `board_fade_out_counter` / `next_survival_stage_counter` | GOTY 0x5614/0x5618/0x561C；1051 −0x18 | 关卡结束检测 |
| Board | 新增 | `enable_grave_stones` / `special_grave_x` / `special_grave_y` | GOTY 0x5DC/0x5E0/0x5E4 | put_grave 配合 |
| Board | 新增 | `fog_offset` / `fog_blown_countdown` / `grid_cel_fog` | 0x5E8 / 0x5EC / 0x4E0（int[9][7]） | 雾关 |
| Board | 新增 | `time_stop_counter` | GOTY 0x5760 / 1051 0x5748 | 冰冻菇时停 |
| Board | 新增 | `total_spawned_waves` / `zombie_health_wave_start` / `last_bungee_wave` | GOTY 0x5598/0x55B0/0x55A8 | 波次统计 |
| Board | 新增 | `mower_bonus_remaining` / `wave_row_got_mowered` | GOTY 0x620 / 0x608 | |
| Board | 新增 | `board_rand_seed` | GOTY 0x5634 / 1051 0x561C | 仿真复现 |
| Board | 新增 | `tutorial_state` / `killed_yeti` / `mode_flags`（0x5779–0x577F） | 见 §4.1 | |
| Board | 新增（GOTY 专有） | `level_coins_collected` 0x5788、`gargantuar_cob_kills` 0x578C 等成就统计 | | 可选 |
| Board | 改名 | `particle_system_ref` → `pool_sparkly_particle_id` | 0x5638 | 语义更准（它是水池闪光粒子，不是通用粒子引用） |
| Board | 改名建议 | `items` → `coins` | 0xFC | 源码名为 mCoins |
| Board | 新增 | `grid_cel_look` / `grid_cel_offset` | 0x258 / 0x330 | 可选 |
| Plant / Zombie | 校对 | stride 分别 0x14C / 0x168（1051 0x15C）已正确 | | |
| SeedBank | 新增 | `conveyor_belt_counter` / `cutscene_darken` | 0x34C / 0x348 | 传送带关 |
| SeedPacket | 新增 | `index` / `offset_x` / `refreshing` / `times_used` / `slot_machine_countdown` | 0x2C / 0x30 / 0x49 / 0x4C / 0x3C | |
| CursorObject | 新增 | `seed_bank_index` / `type` / `imitater_type` / `cob_cannon_plant_id` / `glove_plant_id` | 0x24 / 0x28 / 0x2C / 0x40 / 0x38 | 手持状态判定 |
| Challenge | 新增 | `challenge_state` / `challenge_state_counter` / `conveyor_belt_counter` / `challenge_score` / `scary_potter_pots` / `rain_counter` | 0x54 / 0x58 / 0x5C / 0x60 / 0xB0 / 0xB4 | |
| Projectile | 新增 | `type` 0x5C、`pos_x/y/z` 0x30/0x34/0x38、`vel_x/y/z` 0x3C/0x40/0x44、`dead` 0x50、`cob_target_x` 0x80、`cob_target_row` 0x84、`motion_type` 0x58、`age` 0x60 | | 炮弹落点/飞行追踪 |
| Coin | 新增 | `type` 0x58、`pos_x/y` 0x24/0x28、`dead` 0x38、`is_being_collected` 0x50、`coin_age` 0x4C、`motion` 0x5C、`usable_seed_type` 0x68 | | 自动收集实现/统计 |
| LawnMower | 新增 | `row` 0x14、`pos_x` 0x8、`mower_state` 0x2C、`mower_type` 0x34、`visible` 0x31 | | |
| GridItem | 新增 | `state` 0xC、`counter` 0x18、`zombie_type` 0x3C、`seed_type` 0x40、`scary_pot_type` 0x44、`transparent_counter` 0x4C、`sun_count` 0x50 | | 砸罐子/坑洞/梯子 |
| PlayerInfo | 校对 | 1096 达到 21 成就 → 档案已正确；为 1073 也用 21 | | |
| LawnApp | 新增 | `zen_garden` (+0xB4 rel)、`app_counter` (+0xD0)、`board_result` (+0x120)、`app_rand_seed` (+0x108)、`easy_planting` 已有 | GOTY 需 +0x20 修正，以档案既有值推算 | |
| DataArray | 新增通用子键 | `max_size` +0x8、`size` +0x10、`next_key` +0x14 | 所有 DataArray | 遍历上界用 `mMaxUsedCount`(+4)，存活数用 `mSize`(+0x10) |

**已应用（2026-08-22，profile schema_version 2）**：上表的 Board / Plant / Zombie / Projectile / Coin / LawnMower / GridItem / SeedPacket / SeedBank / CursorObject / Challenge / LawnApp 字段与 DataArray 子键（`<array>_max_size` / `_size` / `_next_key`，以及 `projectile_count_max` / `coin_count_max`）已写入 `tools/import_toolkit_profiles.py`（表 `SOURCEMAP_*`，来源标记 `source-map §4`）并重新生成全部 14 份档案；retail 用 1051 列、GOTY 系用 GOTY 列（GOTY 专有字段 retail 省略；Zombie `fireball_row` / `is_fireball` 按版本取值），beta 两档不加新字段。改名 `items → coins`、`particle_system_ref → pool_sparkly_particle_id`（对象 `item → coin`），旧名经 `profile.ALIASES` 仍可用。对象模型 `Plant` / `Zombie` / `Board` 扩展并新增 `Projectile` / `Coin`（`Observer.projectiles()` / `coins()`）。未应用：`cheat_*` 单独键（仅给出 `mode_flags` 基址）、Board 的成就统计以外的 GOTY 字段、`grid_item_*`/`mower_*` 对象模型读取。

### 7.2 可由 NotSupported 转为实现的条目

| 功能 | 现状 | 现在可以怎么做 |
|---|---|---|
| `cob_fire`（GOTY） | 1096 档案缺函数，只靠 anchors 特征 | §6.6 规范完整：VA 0x46D1F0，EAX=Plant*，push y/push x，ret 8。把 VA 写进 1096 档案并在 `Remote.call` 增加 "eax-this + callee-cleans" 约定标志 |
| 玉米炮就绪判定 / 剩余 CD | 无 | `state(+0x3C)==37`；ARMING 时 `3000 − (3000 − state_countdown) + 475`（即 `state_countdown + 475`），LOADING 时以动画进度估算（≈ 175 cs） |
| 出怪列表读/写 | 读法不确定（整行还是哨兵） | §5：读到首个 -1 截断；写入时 ≤49 项 + 终止 -1；`wave_total` 之后的波不要读 |
| `pick_background` | 名字误导 | 改注释为 `Board::LoadBackgroundImages`；使用顺序 = 先写 `scene` 再调用 |
| 模仿者放置 | 流程不明 | put_plant(SEED_IMITATER) → 写 +0x138 → `ImitaterMorph`（§2） |
| 行/列 ↔ 像素换算（屋顶、高地、水池） | 按社区公式 | 用 §6.4 源码公式替换，含高地 −30 和屋顶列偏移 |
| 爆心计算（玉米炮落点） | 社区经验 x−7 | §6.1 推导确认：爆心 = x − 7，半径 115，±1 行 |
| `place_street_zombies` 重调 | 未知为何只能一次 | `mPlacedZombies` 守卫 + `street_zombies_hack` |
| `mower_restore` 的模式限制 | 未知 | `InitLawnMowers` 对禅境/智慧树/Last Stand/I,Zombie 等直接返回；需 `mower_init_allow` |
| `sync_profile` 的时机限制 | 未知 | 仅主菜单（thiscall on mGameSelector） |
| 僵尸过场标识 | 无 | `from_wave == -1`（ZOMBIE_WAVE_CUTSCENE） |
| `items` 数组 stride 0xD8 | 来源不明 | Coin 含 8 字节对齐 PottedPlant，sizeof 0xD0 + 4 → 0xD8；stride 计算规则可写进 `DataArray` 通用逻辑 |
| `allowed_types` 长度 | 不确定 | `bool[100]`，有效 33 种 |
| GOTY LawnApp 位移 | 未解释 | 仅 `mGameSelector`→`mTodCheatKeys` 之间 +0x20；档案值正确，不需改；用于 1073/1093 推断时保持同一修正 |
| `plant_invincible` / `zombie_invincible` 副作用 | 未知 | §3：植物无敌会让钉子盘路径始终被走、Squish 变 no-op；僵尸无敌同时关闭催眠、吹飞、溅射、割草机 — 在 UI 说明 |

### 7.3 仍无法从源码解决的

- `main_loop_block`、`background_running`、`userdata_readonly` 站点 1（框架内，无地址注释）；`userdata_readonly` 站点 3（1096 独有）。
- `mower_init_allow` 操作数、`mower_init_no_anim` 的目标 call。
- `mouse_down` 0x539390（超出带注释范围）。
- GOTY LawnApp 0x20 插入的具体字段；Widget/SexyApp 基类大小（需 IDA 在 1096 上核实）。
- `frame_duration`（SexyAppBase::mFrameTime）。
