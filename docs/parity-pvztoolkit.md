# pvztools × PvZ Toolkit 功能对等矩阵（parity v2，2026-08-22）

> 目标："PvZ Toolkit 能做的功能我们都能做"，再加上 Toolkit 没有的轨道 / 阵容 / 自动化。
> 依据：`docs/research/pvztoolkit-features.md`（Toolkit 1.22.0 全功能盘点，事实）、`docs/research/pvztoolkit-addresses.json`（14 构建 × 157 符号地址表，事实）、`docs/DESIGN.md`（§2 架构、§3 版本自适应、§4 对象模型、§6 执行器、§8 v0 清单）。
> 许可证提醒：Toolkit 为 GPL-3.0。本文只引用**事实**（地址、偏移、字节、流程描述），**不移植代码**；实现时按 DESIGN §9 自行用 Python 重写。
> 本文 §4 的"修改器功能清单 v2"**取代** DESIGN §8 的 v0 表。

---

## 0. 读法与约定

### 0.1 我方模块（对应 DESIGN §2）

| 模块 | 职责 | Toolkit 对应手段 |
|---|---|---|
| `addr` | profile JSON → AddressBook（RVA）；L1 版本表 / L3 AOB 扫描 | `data.cpp` 地址表 + `FindPvZ` |
| `observe` | 只读快照 → `model.*` | `ReadMemory` 遍历 |
| `modifier` | 内存写、可逆代码补丁、写白名单、安全开关 | `WriteMemory` + `enable_hack` |
| `control` | 远程调用（shellcode）、执行器、状态机 | `Code` + `asm_code_inject` |
| `schema.lineup` | 阵型码 codec（纯 Python） | `lineup.cpp` |
| `schema.spawn`（新增） | 出怪表生成 / 编解码（纯 Python） | `CustomizeSpawn` / `.zbl` |
| `formats.pak`（新增，可选） | PAK 解包 / 打包（纯 Python） | `pak.cpp` |
| `cli` | `pvztools mod / read / lineup / spawn / pak …` | FLTK 界面 |

### 0.2 状态

- **已设计**：DESIGN v0 已覆盖，本文只补实现细节。
- **补入**：DESIGN v0 缺失，本文纳入 v2 清单。
- **不做**：给出原因；通常是纯 GUI 事务，或由我方别的机制替代。

### 0.3 安全级别（细化 DESIGN §8，v2 起以此为准）

| 级别 | 定义 | 需要的开关 |
|---|---|---|
| **低** | 单字段内存写（值可随时改回）或单点 / 多点代码补丁（打前校验原字节，可逆，`atexit` 恢复） | `allow_write` 或 `allow_patch` |
| **中** | 远程调用（`block_main_loop` 补丁 + `CreateRemoteThread` 执行 shellcode），或批量遍历对象数组写入 | `allow_remote_call`（远程调用）/ `allow_write` |
| **高** | 触碰 PlayerInfo（存档，游戏随后自动落盘）、改变模式 / 关卡标志、禁止存档、直接过关 | `allow_savedata`（新增，高级操作独立开关）+ 先备份 `userdata` |
| **文件** | 不接触游戏进程，只读写本地文件 | 无（但不分发游戏素材） |

通用守卫：`require_ui(2|3)` / `require_ui(3)` / `require_mode(...)` 对应 Toolkit 的 `GameUI()` / `GameMode()` 检查；`purity="strict"` 时除 `background_running` 外全部拒绝。

### 0.4 远程调用约定分族（DESIGN §3 / §12 第 4 条）

Toolkit 的每个 `call_*` 都按 **retail（1051/1065/es/ru）/ goty / goty-zh2010+ja / goty-zh2012 / beta** 选择寄存器与压栈方式。我方把这一信息做成 profile 的 `conventions` 字段（见 §6），`control.remote` 按族生成 shellcode；族未知（改版）时 → 能力降级为 READ_WRITE，不允许远程调用。

---

## 1. 对等矩阵：通用机制（Toolkit §0）

| Toolkit | 我方模块 | 状态 | 实现要点 |
|---|---|---|---|
| 0.1 内存写 / 代码补丁 / 远程调用 / 文件 | `modifier` / `control` / `formats` | 已设计 | DESIGN §6 external 后端已有 `block_main_loop` 包裹的远程线程流程；补丁对象 `Patch(addr, on_bytes, off_bytes, width)` 与 Toolkit `HACK<T,N>` 同构，地址 0 ⇒ 该构建不支持、功能禁用 |
| 0.2 调用约定分支 | `addr` profile `conventions` | **补入** | 见 §6；每个函数标 `family` |
| 0.3 `GameOn` / `GameUI` / `GameMode` 前置 | `process` / `observe.Game` | 已设计 | `Game.ui`、`Game.mode`；进程消失自动重附着（DESIGN §3.2 L0） |
| 0.4 版本检测（窗口类 + PDB 路径 `\Lawn\` + TimeDateStamp） | `process` + `addr` L0/L1 | 已设计 | 窗口标题候选列表与 14 个 stamp 直接写入 `profiles/index.json`；`nullptr` 标题兜底 ⇒ 我方按进程枚举 + `IsWow64Process` 而非仅靠窗口 |
| 0.4-7 `IsDebuggerPresent` 自毁 | — | 不做 | 反调试与开源库无关 |
| 0.5 状态栏文案 / 粘度汗化警告 | `cli doctor` | 已设计 | 版本名 + `verified` 标记；zh2010 构建在 profile 里 `warnings: ["unstable"]` |
| 0.6 热键 / 缩放 / 主题 / 单实例 / 注册表持久化 | — | 不做 | 无 GUI；用户偏好走 `pvztools.toml`（可选），不进注册表 |
| 0.6 命令行 `/U` `/P` 打包解包 | `cli pak` | **补入** | `pvztools pak unpack <pak> <dir>` / `pack <dir> <pak>` |
| 0.6 失去游戏后重新应用已勾选功能（`keep_selected_feature`） | `modifier.Session` | **补入** | Session 记录已启用的 patch/写集合；重附着后按顺序重放（重放前重新校验原字节） |

---

## 2. 对等矩阵：逐功能

下表"符号"列为 Toolkit `data.h` 标识符（与 `pvztoolkit-addresses.json` 一致）；括号内是我方语义名（§6 映射表）。

### 2.1 资源（Toolkit §1）

| # | Toolkit 功能 | 我方 | 状态 | 符号 / 机制 | 级别 |
|---|---|---|---|---|---|
| 1.1 | 阳光无上限 | `modifier.patches.sun_cap_unlock` | 已设计 | `unlock_sun_limit`（1051 `0x430a23` 7E→EB） | 低 |
| 1.2 | 阳光设定 | `modifier.set_sun` | 已设计 | 写 `sun`（Board+0x5560）；`require_ui(2|3)`；范围守卫 0–9990（解锁上限后放宽） | 低 |
| 1.3 | 金币 | `modifier.player.set_money` | 已设计（v0"金币/关卡/智慧树"） | 写 `user_data.money`；需 `[lawn]+user_data != 0`；显示 ×10 | 高 |
| 1.4 | 自动收集 | `patches.auto_collect` | 已设计 | `auto_collected` | 低 |
| 1.5 | 不掉战利品 | `patches.no_loot_drop` | **补入** | `not_drop_loot` | 低 |
| 1.6 | 花肥无限 | `patches.unlimited_fertilizer` + 写 | **补入** | `fertilizer_unlimited` + 写 `twiddydinky[14] = 1020` | 低（补丁）+ 高（写存档项） |
| 1.7 | 杀虫剂无限 | `patches.unlimited_bug_spray` | **补入** | `bug_spray_unlimited` + `twiddydinky[15] = 1020` | 同上 |
| 1.8 | 树肥无限 | `patches.unlimited_tree_food` | **补入** | `tree_food_unlimited` + `twiddydinky[28] = 1010`；beta 0.1.1 地址 0 ⇒ 禁用 | 同上 |
| 1.9 | 巧克力无限 | `patches.unlimited_chocolate` | **补入** | `chocolate_unlimited`（2 点）+ `twiddydinky[26] = 1010` | 同上 |
| 1.10 | 树高 | `modifier.player.set_tree_height` | 已设计 | 写 `user_data.tree_height`；若 `mode==50` 则写 `height-1` 并远程调用 `call_wisdom_tree`（族：goty ESI=challenge / zh2010+ja push EBX / zh2012 EDI / retail EDI=lawn / beta0.9.9 ECX；beta0.1.1 地址 0） | 高 + 中 |
| 1.11 | 免费用卡（+清冷却） | `modifier.free_planting` + `modifier.reset_cooldowns` | 已设计（"自由种植""卡槽无冷却"） | 写 `free_planting`（LawnApp+0x814）；UI==3 时遍历 `slot` 每格 0x50，`slot_seed_cd_past = slot_seed_cd_total` | 低 |
| 1.12 | 随意放置 | `patches.place_anywhere` | **补入** | 三点：`placed_anywhere` / `_preview` / `_iz` 作为一个 PatchGroup | 低 |
| 1.13 | 无缝传送 | `patches.fast_conveyor` | **补入** | `fast_belt`（2 点） | 低 |
| 1.14 | 连续铲子 | `patches.sticky_shovel` | **补入** | `lock_shovel` + 开启时写 `cursor.cursor_grab = 6`，关闭时若 ==6 写 0 | 低 |
| 1.15 | 混乱关卡（改 game_mode / 冒险关卡号） | `modifier.set_game_mode` | **补入** | 写 `game_mode`；冒险时先写 `user_data.level` 与 `board.adventure_level`；`require_ui(2|3)`；枚举表 `GameMode` 0–72 进 `schema.enums` | 高（改模式标志 + 存档 level） |
| 1.16 | 无尽轮数 | `modifier.set_endless_round` | **补入** | 写 `challenge.endless_rounds`；`require_mode(11..15, 60, 70)` | 低 |
| 1.17 | 通关存档（金向日葵） | `modifier.player.unlock_all` | 已设计（v0"关卡"）→ 细化 | 纯写 PlayerInfo：`playthrough≥2`、`mini_games[...]`、`twiddydinky[...]`、GOTY 成就数组（1096 为 21 个，其余 20 个）；`playthrough` 原为 0 且 UI==1 时远程调用 `call_sync_profile`；**PlayerInfo 字段布局表**（Toolkit 的跳过序列）进 `schema.enums.PlayerInfoLayout`【待核实：beta 布局】 | 高（先备份 userdata） |
| 1.18 | 直接过关（含"炮最亮"对帧） | `control.finish_level` | **补入** | `require_ui(3)`；远程调用 `call_fade_out_level`（goty ECX=board / zh2010+ja push EAX）；`brightest_cob`: 读 `game_clock`，`wait = 75 - ((clock+500) % 75)` 帧，经 `PostMessage(VK_SPACE)` 暂停/恢复。我方执行器本就有帧钟轮询，可直接等到 `(clock+500) % 75 == 0` 而不用暂停 | 高 |

### 2.2 战场（Toolkit §2）

| # | Toolkit 功能 | 我方 | 状态 | 符号 / 机制 | 级别 |
|---|---|---|---|---|---|
| 2.1 | 种植物（行/列/类型/模仿者；IZ 风格） | `control.put_plant` | 已设计 | `call_put_plant`（retail: push im?type,48:-1,type; EAX=row; push col; EBP=board push EBP / beta: push row,col, ECX=board）；模仿者再 `call_put_plant_imitater`（ESI=植物 = `[board]+plant + plant_next_pos*0x14c`）；IZ 模式 `call_put_plant_iz_style`（EAX=challenge）；炮 47 列上限 8；多格合并为一次注入 | 中 |
| 2.2 | 出僵尸 | `control.put_zombie` | 已设计 | `call_put_zombie`（retail: push col,type; EAX=row; ECX=challenge / zh2010+ja: push type,challenge; EAX=row; ECX=col / beta push row,col,type）；僵王 25 用 `call_put_zombie_in_row`（EAX=board, push 0, push 25） | 中 |
| 2.3 | 搭梯子 | `control.put_ladder` | **补入** | `call_put_ladder`（EDI=row, push col, EAX=board / beta push row,col ECX=board） | 中 |
| 2.4 | 冒墓碑 | `control.put_grave` | **补入** | `call_put_grave`（goty ECX=challenge push; EDI=row; EBX=col / retail EDX=challenge push / beta push） | 中 |
| 2.5 | 放钉耙 | `control.put_rake` | **补入** | 临时补丁 `call_put_rake_row`(+1 写 row, 6/7 字节)、`call_put_rake_col`(+4 写 col, 8 字节) + `rake_unlimited`，调用 `call_put_rake`，之后**恢复备份字节**；每格单独注入。我方用 `with TempPatch(...)` 上下文保证恢复 | 中（含临时补丁） |
| 2.6 | 小推车 启动 / 删除 / 恢复 | `control.mowers.start/delete/restore` | **补入** | `require_ui(3)`；遍历 `lawn_mower`（stride 0x48，跳 `lawn_mower_dead`）：`call_start_lawn_mower`（ESI / zh EBX / beta ECX）、`call_delete_lawn_mower`（EAX / beta ECX）；恢复 = `init_lawn_mowers` + `lawn_mower_initialize`（X 常量指针 −160.0→−21.0，免入场动画）补丁包裹 `call_restore_lawn_mower`（push board） | 中 |
| 2.7a | 清除植物 | `control.clear_plants` | 已设计（"删除植物"） | 遍历 `plant`（0x14c）非 dead/squished → `call_delete_plant`（push addr / beta ECX） | 中 |
| 2.7b | 清除僵尸 | `modifier.kill_all_zombies` | 已设计（v0 用 KillZombie 0x5302F0）→ **改为 Toolkit 做法** | 纯内存写 `zombie_status = 3`（stride goty 0x168 / retail 0x15c / beta0.1.1 0x160）；无需远程调用，级别降为低；v0 的 `KillZombie` 调用保留为可选 | 低 |
| 2.7c | 清除梯子 / 墓碑 / 钉耙 | `control.clear_grid_items(types)` | **补入** | 遍历 `grid_item`（0xec / beta0.1.1 0x8c），类型 ∈ {3,1,11}（枚举 1 墓碑 2 弹坑 3 梯子 4/5 传送门 7 罐子 10 蜗牛 11 钉耙 12 脑子 进 `schema.enums.GridItemType`）→ `call_delete_grid_item`（ESI / beta ECX） | 中 |
| 2.8 | 植物无敌 | `patches.plant_invincible` | 已设计（v0 写 HP）→ **改为补丁组** | 9 点 PatchGroup：`plant_immune_{eat,radius,jalapeno,projectile,lob_motion,square,row_area,spike_rock,squish}`；与 2.9 互斥；beta 的 eat 点地址 0 ⇒ 功能标"部分"。v0 的"写 Plant.hp"保留为 `modifier.set_plant_hp` | 低 |
| 2.9 | 植物脆弱 | `patches.plant_fragile` | **补入** | `_plant_immune_{eat,projectile,lob_motion,row_area}` | 低 |
| 2.10 | 僵尸无敌 | `patches.zombie_invincible` | **补入** | 11 点 `zombie_immune_{body_damage,helm_damage,shield_damage,burn_crumble,radius,burn_row,chomper,mind_controll,blow_away,splash,lawn_mower}`；与 2.11 互斥 | 低 |
| 2.11 | 僵尸虚弱 | `patches.zombie_fragile` | **补入** | `_zombie_immune_{body_damage,helm_damage,shield_damage,burn_crumble}` | 低 |
| 2.12 | 立刻装填 | `patches.instant_reload` | **补入** | `reload_instantly` + `grow_up_quickly`(2) + `no_cooldown`(2) | 低 |
| 2.13 | 只投黄油 | `patches.always_butter` | **补入** | `lock_butter` | 低 |
| 2.14 | 暂停刷怪 | `patches.pause_spawning` | 已设计（"禁止刷新"v0 用写 countdown）→ **两种都保留** | 补丁 `stop_spawning`（跳过倒计时）为主；写 `countdown` 为辅（纯键控可接受的方式） | 低 |
| 2.15 | 停滞不前 | `patches.freeze_zombies` | **补入** | `stop_zombies`(2) | 低 |
| 2.16 | 蘑菇睡醒 | `patches.mushrooms_awake` + `control.wake_plants` | **补入** | `mushrooms_awake` + 遍历 `plant_asleep` 为真者 `call_set_plant_sleeping(addr, 0)`（goty EDI / beta ECX / retail EAX，push 0） | 低 + 中 |
| 2.17 | 核弹无坑 | `patches.no_crater` | **补入** | `no_crater` | 低 |
| 2.18 | 冰车无痕 | `patches.no_ice_trail` | **补入** | `no_ice_trail`(2) + 开启时写 `ice_trail_cd[0..5] = 1` | 低 |
| 2.19 | 丑椒不爆 | `patches.no_explode` | **补入** | `zombie_not_explode`(2) | 低 |

### 2.3 阵型（Toolkit §3）

| # | Toolkit 功能 | 我方 | 状态 | 符号 / 机制 | 级别 |
|---|---|---|---|---|---|
| 3.1 | 卡槽查看 | `observe.Board.seeds` | 已设计 | `slot_seed_type` / `slot_seed_type_im`，48 ⇒ 模仿者 | 只读 |
| 3.2 | 换卡片 | `modifier.set_seed(slot, type, imitater)` | **补入** | 模仿者：`type=48, im=type`；否则 `type, im=-1`；`require_ui(2|3)`；不改冷却。比 v0 的 `ChooseCard 0x486030`（选卡界面）更通用 | 低 |
| 3.3 | 快捷布阵模式（组合开关） | `modifier.Preset("lineup_mode")` | **补入** | 预设 = {auto_collect, free_planting, plant_invincible, instant_reload, pause_spawning, no_fog}；退出时恢复各开关原值（Session 记录） | 低 |
| 3.4 | 智能搭梯 | `control.auto_ladder(imitater_only=True)` | **补入** | 先 `clear_grid_items({3})`；遍历 `plant_type==30` 且（可选）`plant_imitater==48`、`col!=0`、`block_type[row][col]==1`（`block_type + 4*row + 0x18*col`）→ 合并 `put_ladder`。我方开放 `imitater_only=False` | 中 |
| 3.5 | 水路睡莲（列范围） | `control.fill_lily_pads(cols)` | **补入** | `block_type==3` 且无植物 → `put_plant(16)` 合并注入 | 中 |
| 3.6 | 屋顶花盆 | `control.fill_flower_pots(cols)` | **补入** | `require_scene(4|5)`；无植物格 → `put_plant(33)` | 中 |
| 3.7 | 重置场地 / 切换场景 | `control.set_scene(scene, reset=True)` | **补入** | 场景不同：删推车 → 远程 `mov [esi+scene],scene; call_pick_background`（beta 另需临时补丁 + ECX）→ 写 `block_type` 6×9 与 `row_type` 6 项 → 恢复推车 → 非泳池时删粒子系统（`anim→unnamed→particle_system` stride 0x2c，type 34 → `call_delete_particle_system`，`particle_systems_addr=0`）→ `play_music`。`reset`：`dec challenge.endless_rounds` 再 `call_puzzle_next_stage_clear`（EDI=challenge / zh2010+ja push EDI / beta ECX） | 中（多步；失败需回滚说明） |
| 3.8 | 阵型列表 `.yml`（`#! pvztoolkit` 头） | `schema.lineup.io.read_toolkit_yml` | 已设计（`lineup.yml` 在 §2 架构里）→ 细化 | 首行 `#! pvztoolkit`；`"名称": base64[url]`；权重排序公式（场景×1e16 + 炮×1e14 + 香蒲×1e12 + 双子×1e10 + 冰瓜×1e8 + 南瓜×1e6 + 睡莲×1e4 + 花盆×100 + 梯子）作为 `Lineup.sort_key()` | 文件 |
| 3.9–3.12 | 代码文本框 / 复制 / 粘贴 | `cli lineup` | 不做（GUI） | CLI：`pvztools lineup get`（读场上 → 码）/ `apply <code>`；剪贴板清洗规则（去空白、`, `→`,`）进 `Lineup.parse` 容错 | — |
| 3.10 | 获取代码（场上 → 阵型码） | `observe` + `schema.lineup.from_board` | 已设计 | 植物 16/33→base、30→pumpkin、35→coffee、其余 plant+1；`plant_awake=!asleep`；grid item 1→base=3、3→ladder、11→rake_row | 只读 |
| 3.13 | 阵型码格式（RFC1437） | `schema.lineup.codec` | 已设计（`docs/lineup-format.md` §2.2） | XOR 0x54 + zlib + RS 字节 + uint16 GRID 位域；兼容旧 `场景,hexType row col …` 字符串 | 文件 |
| 3.14 | 一键布阵 | `control.apply_lineup` | 已设计（DESIGN §6.1）→ **按 Toolkit 顺序细化** | 门控 `mode ∈ 11..15 ∪ 61..70`；清 grid items {1,2,3,11} + 清植物；场景不符 → `set_scene`；钉耙单独注入（`put_rake(rake_row-1, 7)`）；一次注入顺序 **睡莲/花盆 → 主植物（白天 0/2/4 且 `may_sleep` 且 awake ⇒ `call_set_plant_sleeping(0)`；土豆雷 4 / 阳光菇 9 ⇒ `[plant+0x54]=1`，beta +0x5c）→ 南瓜 → 咖啡豆 → 墓碑 → 梯子**；`may_sleep = {8,9,10,12,13,14,15,24,31,42}` 进 `schema.enums` | 中 |
| 3.15 | 截图到剪贴板 | `cli screenshot` | **补入**（低优先级） | `GetDC + BitBlt` 800×600；我方落文件（PNG）优先于剪贴板 | 文件 |

### 2.4 出怪（Toolkit §4）

| # | Toolkit 功能 | 我方 | 状态 | 符号 / 机制 | 级别 |
|---|---|---|---|---|---|
| 4.1 | 僵尸种类选择 + 规则（普僵必出、旗帜、11 种上限、路障/读报互斥） | `schema.spawn.SpawnRequest` | 已设计（v0"出怪控制"）→ 细化 | 规则做成 lint（可 `strict=False` 关闭）；20 可选种类表进 `schema.enums` | 纯 Python |
| 4.2 | 右键菜单（清空 / 取消限制 / 切换布局） | — | 不做（GUI） | `strict` 参数替代 | — |
| 4.3 | 出怪统计窗口 + `.zbl` 保存 / 加载 | `observe.spawn_list` + `schema.spawn.zbl` | **补入** | 读 `spawn_list` 1000 int（每波遇 −1 截断）；模式 1–5 只前 10 波；`.zbl` = `'RLCX'` + ver 1 + len 1000 + 1000×int32 + CRC32；统计表 `pvztools spawn stats`（表格输出） | 只读 / 文件 |
| 4.4 | 红眼权重 1000–6000 | `SpawnRequest.giga_weight` | **补入** | 模拟模式权重参数 | 纯 Python |
| 4.5 | 变速（11–19 波无红眼） | `SpawnRequest.no_giga_mid` | **补入** | 极限 / 模拟模式均生效 | 纯 Python |
| 4.6a | 自然出怪 | `control.respawn_natural(types)` | 已设计 | 写 `spawn_type`（33 bool，普僵强制）→ `spawn_list` 写 2000 个 −1 → 远程 `call_pick_zombie_waves`（EDI=board / zh2010+ja EAX / beta ECX）→ UI==2 时刷新预览 | 中 |
| 4.6b | 极限出怪（均匀填充） | `schema.spawn.generate(mode="uniform")` + `modifier.write_spawn_list` | **补入** | 本地生成 20×50；循环 `(type+1)%33`；旗帜波 450/950 首位旗帜；蹦极 459–462/959–962；雪人随机一格；`require_mode(1..15)` | 低 |
| 4.6c | 模拟出怪（加权随机） | `schema.spawn.generate(mode="weighted")` | **补入** | 原始权重表（普僵 400 … 红眼 6000，见 features §4.6）进 `schema.timing.SPAWN_WEIGHTS`；旗帜波 451–458 普僵 | 低 |
| 4.6d | 选卡界面刷新预览僵尸 | `control.refresh_spawn_preview` | **补入** | `hack_street_zombies` 补丁包裹 `call_remove_cutscene_zombies`（EBX=board）+ `call_place_street_zombies`（push `[board]+cut_scene`） | 中 |

### 2.5 杂项（Toolkit §5）

| # | Toolkit 功能 | 我方 | 状态 | 符号 / 机制 | 级别 |
|---|---|---|---|---|---|
| 5.1 | 背景音乐 | `control.play_music(id)` | **补入** | `call_play_music`（EDI=id, EAX=`[lawn]+music` / beta push id, ECX=music）；曲目 1–12 枚举 | 中 |
| 5.2 | 打开存档文件夹 | `cli userdata path` | **补入** | 候选：`[path]`+`\userdata`、`%ProgramData%\PopCap Games\{BloomAndDoom,PlantsVsZombies}\userdata`、`...\Steam\PlantsVsZombies\userdata`、`...\Zoo\...`、`...\Reflexive\...`、Steam `userdata\<id>\3590\remote`；我方**只打印路径**，并作为 `allow_savedata` 备份目标 | 文件 |
| 5.3 | 清除浓雾 | `patches.no_fog` | **补入** | `no_fog`（uint16） | 低 |
| 5.4 | 罐子透视 | `patches.see_vase` | **补入** | `see_vase`（uint32） | 低 |
| 5.5 | 后台运行 | `patches.background_running` | 已设计 | `background_running`（uint16；retail 2E74 / goty 4074 / beta 2D74 → EB）；`purity=strict` 唯一放行的补丁 | 低 |
| 5.6 | 禁止存档 / 删档 | `patches.userdata_readonly` | **补入** | `disable_delete_userdata` + `disable_save_userdata`；Steam 1096 额外 `0x498440: 6A FF 68 → C2 0C 00`（profile 内作第三点） | 高（影响存档） |
| 5.7 / 5.8 | PAK 解包 / 打包 | `formats.pak` | **补入**（低优先级） | 纯文件格式；错误码集合同 Toolkit；与游戏进程无关，Linux 可测 | 文件 |
| 5.9 | 调试显示模式 | `modifier.set_debug_mode(0..4)` | **补入** | 写 `debug_mode`（Board+0x55f8）；`require_ui(2|3)` | 低 |
| 5.10 | 速度 | `modifier.set_speed` | 已设计 | 写 `frame_duration` 1/2/5/10/20/50/100；执行器的帧等待同步读取该值 | 低 |
| 5.11 | 显示 Limbo 页 | `patches.show_limbo_page` | **补入** | `unlock_limbo_page`（3 字节） | 低 |
| 5.12–5.15 | 界面样式 / English tooltips / 文档 / 关于 | — | 不做（GUI） | `pvztools --version`、README | — |

### 2.6 覆盖统计

Toolkit 可计功能 60 项（含子项）：**已设计 16**、**补入 37**、**不做 7**（全部为 GUI/注册表/反调试事务，无游戏语义损失）。结论：游戏语义层面 100% 对等。

---

## 3. Toolkit 没有、pvztools 新增的能力

| 能力 | 模块 | 说明 |
|---|---|---|
| 轨道 DSL（速记 / 逐波 / 完整三层，Seml 超集，lint，展开器，AvZ 导出） | `schema.track` | DESIGN §5，`docs/track-format.md` |
| 执行器（external 帧钟轮询；可选 inject DLL；计划式 + 反应式） | `control` | DESIGN §6；Toolkit 只有"一次性操作"，没有随波次推进的自动化 |
| 阵容生成（模板召回 / 参数化 / 静态约束 / 模拟器评估） | `generate` | DESIGN §7 |
| 静态 exe 扫描 / 特征码（AOB）/ 四级降级 / 诊断包 | `addr` | DESIGN §3；Toolkit 仅 stamp 查表，未知构建直接"不支持" |
| 改版支持（95 版系列、汉化 1051、Steam `popcapgame1.exe`…） | `addr` + `process` | DESIGN §3.1、lonelystar 目录 §3；Board 整体平移匹配、能力分级 READ_ONLY 兜底 |
| 类型化只读对象模型（Plant / Zombie / Seed / Wave / Clock / Projectile…含 hp、countdown、hp_threshold） | `model` / `observe` | DESIGN §4；Toolkit 只读它要写的字段 |
| 出怪表 **读取 + 分析** API（按波分支、巨人/舞王检测供垫材行选择） | `observe` + `schema.spawn` | Toolkit 只有统计窗口 |
| 多格式阵型互转（ArrayDesign 文本 / pvz.tools 码 / 旧字符串 / 本库 JSON） | `schema.lineup` | `docs/lineup-format.md` |
| 安全模型（三重 + `allow_savedata` 开关、dry_run、写白名单、可逆补丁、存档备份、`purity=strict`、产出物标注键控/修改器） | `modifier` | DESIGN §8 安全设计 |
| 会话重放（重附着后按顺序重放已启用修改，先验原字节） | `modifier.Session` | 对应 Toolkit `keep_selected_feature`，但带校验 |
| Linux CI 可测的格式层（阵型码、出怪表、PAK、轨道） | `schema` / `formats` | Toolkit 全部绑 Win32 |
| Python API / pip 安装 / MIT | 全部 | Toolkit 是 C++ GUI、GPL |

---

## 4. 修改器功能清单 v2（取代 DESIGN §8 v0 表）

分组 = CLI 子命令 / API 命名空间。机制列给 1051 符号，其他构建查 profile。

### 4.1 资源 `mod res`

| 功能 | 机制 | 级别 | 门控 |
|---|---|---|---|
| 阳光设定 / 锁定 | 写 `board.sun` | 低 | ui 2/3 |
| 阳光上限解锁 | patch `sun_cap_unlock` | 低 | — |
| 自动收集 | patch `auto_collect` | 低 | — |
| 不掉战利品 | patch `no_loot_drop` | 低 | — |
| 花肥 / 杀虫剂 / 树肥 / 巧克力无限 | patch `unlimited_*` + 写 `player.purchases[i]` | 低 + 高 | user_data≠0 |
| 金币 | 写 `player.money` | 高 | user_data≠0 |
| 树高 | 写 `player.tree_height`（+ mode 50 时 `call wisdom_tree_grow`） | 高 / 中 | — |
| 免费用卡 + 清冷却 | 写 `lawn_app.free_planting`；写 `seed.cd_elapsed = cd_total` | 低 | ui 2/3 |
| 随意放置 | patch group `place_anywhere` ×3 | 低 | — |
| 无缝传送带 | patch `fast_conveyor` ×2 | 低 | — |
| 连续铲子 | patch `sticky_shovel` + 写 `cursor.grab` | 低 | ui 2/3 |
| 游戏速度 | 写 `lawn_app.frame_duration` | 低 | — |

### 4.2 关卡 / 存档 `mod level`

| 功能 | 机制 | 级别 | 门控 |
|---|---|---|---|
| 改模式（混乱关卡）/ 冒险关卡号 | 写 `lawn_app.game_mode`、`player.level`、`board.adventure_level` | 高 | ui 2/3 |
| 无尽轮数 | 写 `challenge.endless_round` | 低 | mode 11–15/60/70 |
| 通关存档（金向日葵 + 成就） | 写 PlayerInfo 多字段；ui 1 时 `call sync_profile` | 高 | 先备份 |
| 直接过关（可选炮最亮对帧） | `call fade_out_level`；帧钟等待 `(clock+500)%75==0` | 高 | ui 3 |
| 禁止存档 / 删档 | patch `userdata_readonly` ×2（1096 ×3） | 高 | — |
| 调试显示模式 | 写 `board.debug_mode` | 低 | ui 2/3 |
| 显示 Limbo 页 | patch `show_limbo_page` | 低 | — |
| 存档目录定位 / 备份 | 文件 | 文件 | — |

### 4.3 战场 `mod field`

| 功能 | 机制 | 级别 | 门控 |
|---|---|---|---|
| 放植物（含模仿者、IZ 风格、炮占两格） | `call put_plant` (+`put_plant_imitater`, `put_plant_iz_style`) | 中 | ui 2/3 |
| 放僵尸（含僵王） | `call put_zombie` / `put_zombie_in_row` | 中 | ui 2/3 |
| 放梯子 / 墓碑 / 钉耙 | `call put_ladder` / `put_grave` / `put_rake`（临时补丁 row/col + `rake_unlimited`） | 中 | ui 2/3 |
| 删植物 / 删 grid item（梯子 墓碑 钉耙 弹坑…） | `call delete_plant` / `delete_grid_item` | 中 | ui 2/3 |
| 一键清僵 | 写 `zombie.status = 3`（可选 `call kill_zombie`） | 低 | ui 2/3 |
| 小推车 启动 / 删除 / 恢复 | `call mower_start / mower_delete / mower_restore`（恢复时 2 个临时补丁） | 中 | ui 3 |
| 植物无敌 / 植物脆弱（互斥） | patch group ×9 / ×4；另有 `set_plant_hp` 写 HP | 低 | — |
| 僵尸无敌 / 僵尸虚弱（互斥） | patch group ×11 / ×4 | 低 | — |
| 立刻装填（炮 / 土豆雷 阳光菇 / 大嘴花 磁力菇） | patch ×5 | 低 | — |
| 只投黄油 | patch | 低 | — |
| 暂停刷怪 | patch `pause_spawning`（或写 `wave.countdown`） | 低 | — |
| 僵尸停滞 | patch ×2 | 低 | — |
| 蘑菇睡醒 | patch + `call set_plant_sleeping(0)` | 低 + 中 | — |
| 核弹无坑 / 冰车无痕 / 丑椒不爆 | patch（冰车另写 `board.ice_trail_cd[6]=1`） | 低 | — |
| 清除浓雾 / 罐子透视 | patch | 低 | — |

### 4.4 阵型 `lineup`

| 功能 | 机制 | 级别 | 门控 |
|---|---|---|---|
| 读场上 → 阵型码 / JSON | observe | 只读 | ui 2/3 |
| 换卡片 | 写 `seed.type` / `seed.imitater_type` | 低 | ui 2/3 |
| 一键布阵（清场 → 场景 → 钉耙 → 合并注入） | 组合远程调用 | 中 | mode 11–15 / 61–70 |
| 智能搭梯 / 水路睡莲 / 屋顶花盆 | 组合远程调用 | 中 | ui 2/3（花盆 scene 4/5） |
| 切换场景 / 重置本轮 | `call pick_background` + 写 `block_type`/`row_type` + 粒子清理 + `call puzzle_next_stage_clear` | 中 | ui 2/3 |
| 快捷布阵预设 | 组合开关 | 低 | — |
| 阵型列表 `.yml` / ArrayDesign / 旧字符串互转 | 纯 Python | 文件 | — |

### 4.5 出怪 `spawn`

| 功能 | 机制 | 级别 | 门控 |
|---|---|---|---|
| 读出怪表 / 统计 / `.zbl` 存取 | observe + 文件 | 只读 / 文件 | ui 2/3 |
| 自然出怪（指定种类让游戏重生成） | 写 `board.allowed_types` + `call pick_zombie_waves` | 中 | ui 2/3 |
| 极限 / 模拟出怪（本地生成后写入） | 写 `board.spawn_list` | 低 | mode 1–15 |
| 刷新选卡界面预览 | patch `street_zombies_hack` 包裹 2 个 call | 中 | ui 2 |

### 4.6 其他 `misc`

| 功能 | 机制 | 级别 |
|---|---|---|
| 后台运行 | patch（strict 模式唯一放行） | 低 |
| 背景音乐 | `call play_music` | 中 |
| 截图 | GDI 文件 | 文件 |
| PAK 解包 / 打包 | 纯 Python | 文件 |

### 4.7 安全设计（承接 DESIGN §8，增量）

- 开关：`allow_write` / `allow_patch` / `allow_remote_call` / **`allow_savedata`**（新增，覆盖所有"高"级）；`dry_run` 默认开。
- 互斥组：`plant_invincible ↔ plant_fragile`、`zombie_invincible ↔ zombie_fragile`；Session 自动关另一侧。
- 临时补丁（钉耙 row/col、推车初始化、beta 场景映射）一律 `with TempPatch()`，异常也恢复。
- 远程调用前必须：族已知（profile `conventions`）+ 函数地址非 0 + `block_main_loop` 地址非 0；否则 `CapabilityError`。
- 高级操作前自动 `userdata` 备份到 `~/.pvztools/backup/<stamp>/<时间戳>/`。

---

## 5. 版本门控汇总（从 Toolkit 事实推导，写入 profile `gates`）

| 功能 | 门控 |
|---|---|
| 极限 / 模拟出怪、出怪统计 | mode 1–15（1–5 仅前 10 波） |
| 一键布阵 | mode 11–15 或 61–70 |
| 无尽轮数 | mode 11–15 / 60 / 70 |
| 屋顶花盆 | scene 4 / 5 |
| 树高生长动画 | mode 50 |
| 小推车、直接过关 | ui 3 |
| 其余场上操作 | ui 2 / 3 |
| beta 0.1.1.1014 | 无树肥、无 wisdom_tree、无通用 eat/body_damage 补丁、结构体尺寸不同、PlayerInfo 布局不同 ⇒ 能力 PARTIAL |
| beta 0.9.9.1029 | 无通用 plant eat 补丁 |
| zh 2010（0x4c563de1） | profile `warnings: ["unstable"]`，doctor 提示换版本 |
| Steam 1096 | 成就 21 个；禁止存档第三点补丁 |

---

## 6. Profile JSON schema：把 Toolkit 表作为 L1 版本表导入

### 6.1 设计原则

1. 代码里只出现**语义名**（`board.sun`），永远不出现 Toolkit 标识符或魔数（DESIGN §2 原则）。
2. 偏移按**所属结构体**分组（Toolkit 是扁平 157 字段，靠注释缩进表达层级；我方显式分层）。
3. `pvztoolkit-addresses.json` 是**研究输入**，不是运行时文件；`tools/import_toolkit_profiles.py` 一次性转换成 `pvztools/addr/profiles/<id>.json`，并写 `source` 溯源。
4. 静态地址一律存 **RVA**（减 ImageBase 0x400000），防未来重定位构建。
5. 每个 profile 带 `verified`（实测过 / 仅由 Toolkit 表导入）与 `family`（调用约定族）。
6. Toolkit 没有、我方需要的字段（`wave.*`、`plant.hp`、`zombie.hp`、`projectile`、`cheats` 等，来源 lonelystar 内存图 / AvZ / re-pvz）放在同一结构体下，`source` 逐字段标注；Toolkit 导入只填它有的子集。

### 6.2 文件形状（示例：1.0.0.1051）

```json
{
  "schema_version": 1,
  "id": "1.0.0.1051-en",
  "display": "1.0.0.1051 英语原始版",
  "stamp": "0x49ecf563",
  "family": "retail",
  "lang": "en",
  "verified": false,
  "warnings": [],
  "source": "pvztoolkit 1.22.0 data.cpp (GPL-3.0) — facts only, re-expressed",
  "globals": {
    "lawn_app": {"rva": "0x2a9ec0", "source": "toolkit:lawn"},
    "exe_path_str": {"rva": "0x2a6cc8", "source": "toolkit:path"}
  },
  "structs": {
    "LawnApp":   {"frame_duration": "0x454", "board": "0x768", "game_selector": "0x770",
                  "tod_mode": "0x7f5", "game_mode": "0x7f8", "game_ui": "0x7fc",
                  "free_planting": "0x814", "effect_system": "0x820", "player_info": "0x82c", "music": "0x83c"},
    "Board":     {"zombies": "0x90", "zombie_count_max": "0x94", "plants": "0xac", "plant_count_max": "0xb0",
                  "plant_next_index": "0xb8", "mowers": "0x100", "mower_count_max": "0x104", "mower_count": "0x110",
                  "grid_items": "0x11c", "grid_item_count_max": "0x120", "cursor": "0x138", "seed_bank": "0x144",
                  "cutscene": "0x15c", "challenge": "0x160", "paused": "0x164", "grid_type": "0x168",
                  "row_type": "0x5d8", "ice_trail_cd": "0x624", "spawn_list": "0x6b4", "allowed_types": "0x54d4",
                  "scene": "0x554c", "adventure_level": "0x5550", "sun": "0x5560", "main_counter": "0x5568",
                  "debug_mode": "0x55f8", "particle_system_ref": "0x5620"},
    "Zombie":    {"stride": "0x15c", "status": "0x28", "dead": "0xec"},
    "Plant":     {"stride": "0x14c", "row": "0x1c", "type": "0x24", "col": "0x28",
                  "imitater_type": "0x138", "dead": "0x141", "squished": "0x142", "asleep": "0x143"},
    "LawnMower": {"stride": "0x48", "dead": "0x30"},
    "GridItem":  {"stride": "0xec", "type": "0x8", "col": "0x10", "row": "0x14", "dead": "0x20"},
    "Cursor":    {"grab": "0x30"},
    "SeedBank":  {"count": "0x24", "packets": "0x28"},
    "SeedPacket":{"stride": "0x50", "cd_elapsed": "0x24", "cd_total": "0x28", "type": "0x34", "imitater_type": "0x38"},
    "Challenge": {"endless_round": "0x6c"},
    "EffectSystem": {"particle_holder": "0x0"},
    "ParticleHolder": {"particles": "0x0", "particle_count_max": "0x4"},
    "ParticleSystem": {"stride": "0x2c", "type": "0x0", "dead": "0x1c"},
    "PlayerInfo":{"level": "0x24", "money": "0x28", "playthrough": "0x2c", "challenge_records": "0x30",
                  "tree_height": "0xf4", "purchases": "0x1c0", "achievements": null}
  },
  "patches": {
    "main_loop_block":   {"sites": [{"rva": "0x152014", "on": "fe", "off": "db"}], "source": "toolkit:block_main_loop"},
    "sun_cap_unlock":    {"sites": [{"rva": "0x030a23", "on": "eb", "off": "7e"}]},
    "plant_invincible":  {"sites": [ {"rva": "0x12fcf1", "on": "464000", "off": "4640fc"}, "…8 more" ], "excludes": ["plant_fragile"]},
    "userdata_readonly": {"sites": ["…"], "level": "high"}
  },
  "functions": {
    "put_plant":      {"rva": "0x00d120", "conv": "retail.put_plant"},
    "put_rake":       {"rva": "0x00b9c0", "conv": "retail.put_rake",
                       "temp_patch": {"row_site": "0x00bb25", "row_len": 7, "col_site": "0x00ba8e", "col_len": 8}},
    "wisdom_tree_grow": {"rva": "0x02d1f0", "conv": "retail.wisdom_tree"}
  },
  "conventions": "retail",
  "gates": {"apply_lineup": {"mode": [[11,15],[61,70]]}, "fill_flower_pots": {"scene": [4,5]}}
}
```

`conventions` 是族名，具体寄存器 / 压栈模板放在代码 `control/conv/<family>.py`（不是 profile 数据，因为那是行为不是事实）。族：`retail` / `goty` / `goty_zh2010_ja` / `goty_zh2012` / `beta`。

### 6.3 字段映射：Toolkit 标识符 → 我方语义名

**全局**

| Toolkit | 我方 | 备注 |
|---|---|---|
| `lawn` | `globals.lawn_app` | 存 RVA |
| `path` | `globals.exe_path_str` | 存档目录定位用 |

**LawnApp**（`[lawn_app] + off`）

| Toolkit | 我方 |
|---|---|
| `frame_duration` | `LawnApp.frame_duration` |
| `board` | `LawnApp.board` |
| `game_selector` | `LawnApp.game_selector` |
| `tod_mode` | `LawnApp.tod_mode` |
| `game_mode` | `LawnApp.game_mode` |
| `game_ui` | `LawnApp.game_ui` |
| `free_planting` | `LawnApp.free_planting` |
| `anim` | `LawnApp.effect_system` |
| `user_data` | `LawnApp.player_info` |
| `music` | `LawnApp.music` |

**Board**（`[board] + off`）

| Toolkit | 我方 | Toolkit | 我方 |
|---|---|---|---|
| `zombie` | `Board.zombies` | `zombie_count_max` | `Board.zombie_count_max` |
| `plant` | `Board.plants` | `plant_count_max` | `Board.plant_count_max` |
| `plant_next_pos` | `Board.plant_next_index` | `lawn_mower` | `Board.mowers` |
| `lawn_mower_count_max` | `Board.mower_count_max` | `lawn_mower_count` | `Board.mower_count` |
| `grid_item` | `Board.grid_items` | `grid_item_count_max` | `Board.grid_item_count_max` |
| `cursor` | `Board.cursor` | `slot` | `Board.seed_bank` |
| `cut_scene` | `Board.cutscene` | `challenge` | `Board.challenge` |
| `game_paused` | `Board.paused` | `block_type` | `Board.grid_type`（6×9，`+4*row+0x18*col`） |
| `row_type` | `Board.row_type` | `ice_trail_cd` | `Board.ice_trail_cd`（6×int） |
| `spawn_list` | `Board.spawn_list`（20×50 int） | `spawn_type` | `Board.allowed_types`（33 bool） |
| `scene` | `Board.scene` | `adventure_level` | `Board.adventure_level` |
| `sun` | `Board.sun` | `game_clock` | `Board.main_counter`（DESIGN §4 Clock.main_counter） |
| `debug_mode` | `Board.debug_mode` | `particle_systems_addr` | `Board.particle_system_ref` |

**对象内偏移**

| Toolkit | 我方 | 备注 |
|---|---|---|
| `zombie_status` / `zombie_dead` | `Zombie.status` / `Zombie.dead` | stride 由 family 决定：retail 0x15c / goty 0x168 / beta0.1.1 0x160（Toolkit 硬编码，我方进 profile `stride`） |
| `plant_row/type/col` | `Plant.row/type/col` | |
| `plant_imitater` | `Plant.imitater_type` | ==48 表示该株由模仿者种下 |
| `plant_dead/squished/asleep` | `Plant.dead/squished/asleep` | |
| `lawn_mower_dead` | `LawnMower.dead` | stride 0x48 |
| `grid_item_type/col/row/dead` | `GridItem.type/col/row/dead` | stride 0xec / beta0.1.1 0x8c |
| `cursor_grab` | `Cursor.grab` | 6 = 铲子 |
| `slot_count` | `SeedBank.count` | |
| `slot_seed_cd_past/total/type/type_im` | `SeedPacket.cd_elapsed/cd_total/type/imitater_type` | **换算**：Toolkit 相对 SeedBank 基址给第 0 格（0x4c/0x50/0x5c/0x60）；我方 `SeedBank.packets = 0x28` + `SeedPacket.* = 0x24/0x28/0x34/0x38`，与 DESIGN §4 一致（0x28+0x24 = 0x4c） |
| `endless_rounds` | `Challenge.endless_round` | |
| `unnamed` / `particle_system` / `particle_system_count_max` | `EffectSystem.particle_holder` / `ParticleHolder.particles` / `ParticleHolder.particle_count_max` | 链 `[lawn]+anim → +0 → +0`，stride 0x2c |
| `particle_system_type/dead` | `ParticleSystem.type/dead` | type 34 = 水波光 |
| `level/money/playthrough` | `PlayerInfo.level/money/playthrough` | |
| `mini_games` | `PlayerInfo.challenge_records` | 起点；子布局（生存 5 / 困难 5 / 无尽 5 / 小游戏 20 / 隐藏 14 / 树高 / 解谜 9+1 / IZ 9+1）进 `schema.enums.PlayerInfoLayout` |
| `tree_height` | `PlayerInfo.tree_height` | |
| `twiddydinky` | `PlayerInfo.purchases` | 起点；索引表（升级植物 0–7、模仿者 8、…花肥 14、杀虫剂 15、…巧克力 26、树肥 28…）进 `schema.enums.Purchase` |
| （Toolkit 硬编码 `userdata+0x24`） | `PlayerInfo.achievements` | 仅 GOTY；1096 为 21 项，其余 20 项 |

**补丁**（Toolkit `HACK` → `patches.<name>.sites[]`，`on`/`off` 为十六进制字节串，宽度由字节串长度表达，不再区分 `uint8/16/32`）

| Toolkit | 我方 | Toolkit | 我方 |
|---|---|---|---|
| `block_main_loop` | `main_loop_block` | `unlock_sun_limit` | `sun_cap_unlock` |
| `auto_collected` | `auto_collect` | `not_drop_loot` | `no_loot_drop` |
| `fertilizer_unlimited` / `bug_spray_unlimited` / `chocolate_unlimited` / `tree_food_unlimited` | `unlimited_fertilizer` / `unlimited_bug_spray` / `unlimited_chocolate` / `unlimited_tree_food` | `placed_anywhere` / `_preview` / `_iz` | `place_anywhere`（3 sites） |
| `fast_belt` | `fast_conveyor` | `lock_shovel` | `sticky_shovel` |
| `rake_unlimited` | `rake_unlimited`（临时） | `init_lawn_mowers` / `lawn_mower_initialize` | `mower_init_allow` / `mower_init_no_anim`（临时） |
| `plant_immune_*` ×9 | `plant_invincible`（9 sites） | `_plant_immune_*` ×4 | `plant_fragile`（4 sites） |
| `zombie_immune_*` ×11 | `zombie_invincible`（11 sites） | `_zombie_immune_*` ×4 | `zombie_fragile`（4 sites） |
| `reload_instantly` + `grow_up_quickly` + `no_cooldown` | `instant_reload`（5 sites） | `mushrooms_awake` | `mushrooms_awake` |
| `stop_spawning` | `pause_spawning` | `stop_zombies` | `freeze_zombies` |
| `lock_butter` | `always_butter` | `no_crater` | `no_crater` |
| `no_ice_trail` | `no_ice_trail` | `zombie_not_explode` | `no_explode` |
| `hack_street_zombies` | `street_zombies_hack`（临时） | `no_fog` | `no_fog` |
| `see_vase` | `see_vase` | `background_running` | `background_running` |
| `disable_delete_userdata` + `disable_save_userdata`（+1096 硬编码点） | `userdata_readonly`（2–3 sites） | `unlock_limbo_page` | `show_limbo_page` |

**函数**（`call_*` → `functions.<name>.rva` + `conv`）

| Toolkit | 我方 | Toolkit | 我方 |
|---|---|---|---|
| `call_put_plant` | `put_plant` | `call_put_plant_imitater` | `put_plant_imitater` |
| `call_put_plant_iz_style` | `put_plant_iz_style` | `call_put_zombie` | `put_zombie` |
| `call_put_zombie_in_row` | `put_zombie_in_row` | `call_put_grave` | `put_grave` |
| `call_put_ladder` | `put_ladder` | `call_put_rake` (+`_row`/`_col`) | `put_rake`（`temp_patch.row_site/col_site`） |
| `call_start/delete/restore_lawn_mower` | `mower_start` / `mower_delete` / `mower_restore` | `call_delete_plant` | `delete_plant` |
| `call_delete_grid_item` | `delete_grid_item` | `call_set_plant_sleeping` | `set_plant_sleeping` |
| `call_puzzle_next_stage_clear` | `puzzle_next_stage_clear` | `call_pick_background` | `pick_background` |
| `call_delete_particle_system` | `delete_particle_system` | `call_pick_zombie_waves` | `pick_zombie_waves` |
| `call_remove_cutscene_zombies` | `remove_cutscene_zombies` | `call_place_street_zombies` | `place_street_zombies` |
| `call_play_music` | `play_music` | `call_sync_profile` | `sync_profile` |
| `call_fade_out_level` | `fade_out_level` | `call_wisdom_tree` | `wisdom_tree_grow` |

DESIGN v0 已列、Toolkit 没有的函数（`ChooseCard 0x486030`、`ShovelPlant 0x411060`、`MouseDown 0x539390`、`CobFire 0x466D50`、`KillZombie 0x5302F0`、`PlantCard 0x488590`）同样进 `functions`，`source` 标 AvZ/pvzscripts，只在 1051 有值，其余构建由 L3 扫描或社区回流填。

### 6.4 导入脚本行为（`tools/import_toolkit_profiles.py`）

1. 读 `docs/research/pvztoolkit-addresses.json`；对每个 version 按 §6.3 映射重排。
2. 静态地址 → RVA；`0x0` → `null`（该功能禁用）。
3. 按 `goty` / `version_id` / 名称推导 `family`：901/902 → `beta`；1001–1005 → `retail`；2004/2005 → `goty_zh2010_ja`；2006/2007 → `goty_zh2012`；2001–2003 → `goty`。
4. 填 `stride`：zombie 0x15c（retail）/ 0x168（goty）/ 0x160（beta 0.1.1）；grid_item 0xec / 0x8c（beta 0.1.1）；plant 0x14c；mower 0x48；packet 0x50；particle 0x2c。
5. 1096 额外写 `userdata_readonly` 第三 site（`0x098440`: `6aff68` → `c20c00`）与 `achievements_count: 21`；其他 GOTY 20；retail/beta `achievements: null`。
6. `verified: false`；DESIGN §3.5 里 1051 / 1065 / 1096 待 M2 实测后改 true。
7. 输出时用 DESIGN §3.5 的 GOTY 位移规则做**自检**：goty 表应等于 retail 表 + 规则位移，否则打印差异（这正是 Toolkit 源码里 `base + delta` 写法的逆向验证）。
8. 生成 `profiles/index.json`：`stamp → id`，附 Toolkit 的窗口标题候选列表供 L0。

### 6.5 AddressBook 解析示例

```python
ab = AddressBook.load("1.0.0.1051-en")
ab.globals.lawn_app            # RVA → VA 由 process.image_base 决定
ab.Board.sun                   # 0x5560
ab.patches.plant_invincible    # PatchGroup(9 sites, excludes=["plant_fragile"])
ab.functions.put_plant         # Function(va, conv="retail.put_plant")
ab.gates.apply_lineup          # {"mode": [[11,15],[61,70]]}
```
代码中 `board.sun` 的读写只经 `ab.Board.sun`；改版扫描得到的结果也写入同一结构，所以上层不关心地址来源。

---

## 7. 待核实（在 DESIGN §12 基础上新增）

13. Toolkit 的 PlayerInfo "跳过序列"在 1051 上逐项核对（`challenge_records` 与 `purchases` 子布局），再定 `PlayerInfoLayout` 枚举。
14. `put_rake` 临时补丁的 row 指令长度（retail 7 / goty 6）在 zh2010/ja/zh2012 上是否一致。
15. `plant+0x54 = 1`（土豆雷/阳光菇长大）与 DESIGN §4 `Plant.state(+0x3c)` 的关系。
16. `zombie_status = 3` 秒杀在 GOTY 上的表现（Toolkit 对全部构建同样写法，未见门控）。
17. beta 两构建是否纳入支持范围——建议 `family: beta` 保留 profile 但 M2/M3 不验收。
