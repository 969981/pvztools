# PvZ Toolkit 1.22.0 功能清单 (完整盘点)

> **来源**：lmintlcx/pvztoolkit（GitHub），本地快照 `scratchpad/pvztoolkit`，版本 1.22.0（`src/window.cpp` 关于弹窗 `1.22.0 (65536)`）；**许可证 GPL-3.0**。
> **性质**：纯事实盘点（控件、机制、地址符号、版本门控），供 `docs/parity-pvztoolkit.md` 做对等矩阵。本文件只记录"它做了什么、怎么做"，不含我方设计决定；偏移/地址是事实，不构成代码移植。
> 配套：`docs/research/pvztoolkit-addresses.json`（14 个构建的完整地址表，字段名沿用 `inc/data.h` 标识符）。

源码: `C:\Users\mike2\AppData\Local\Temp\claude\C--MyAILab\9b6c7fa6-bf46-4013-a521-0bd4ad13cfc3\scratchpad\pvztoolkit`
关键文件: `src/window.cpp` (界面/标签/提示), `src/toolkit.cpp` (按钮回调), `src/pvz.cpp` + `inc/pvz.h` (修改实现), `src/data.cpp` + `inc/data.h` (各版本地址表), `src/code.cpp` (远程汇编注入), `src/lineup.cpp` (阵型编码), `src/pak.cpp` (PAK 打包解包), `src/main.cpp` (启动/热键/定时查找)。

---

## 0. 通用机制 (所有功能共用)

### 0.1 四种实现手段
| 手段 | 说明 | 代码位置 |
|---|---|---|
| **内存写 (memory write)** | `Process::WriteMemory<T>(value, {base, off1, off2...})` 多级指针写入; `ReadMemory` 同理。地址链形如 `[[lawn]+board]+sun`。 | `inc/process.h` |
| **代码补丁 (code patch / HACK)** | `HACK<T,size>{mem_addr, hack_value, reset_value}`; `enable_hack(hack, on)` 为 on 时写 hack_value, off 时写回 reset_value。地址为 `0x00000000` / `0xffffffff` 时跳过 (表示该版本不支持)。有些功能是 `std::vector<HACK>` 一次打多处。 | `inc/pvz.h` 模板 |
| **远程调用 (remote call)** | `Code` 类手工拼 x86 机器码 (`asm_mov_exx`, `asm_push_dword`, `asm_call`, `asm_ret` 等), `asm_code_inject()` 流程: 先 `enable_hack(block_main_loop,true)` 阻塞游戏主循环, `Sleep(帧时长*2)`, `VirtualAllocEx` + `WriteProcessMemory` + `CreateRemoteThread` 执行, `WaitForSingleObject`, `VirtualFreeEx`, 再 `enable_hack(block_main_loop,false)` 恢复。`asm_call` 的目标地址在注入前被修正为相对偏移。 | `src/code.cpp`, `PvZ::asm_code_inject` |
| **存档/文件编辑** | 实际上**没有**直接改存档文件; "通关存档" 是改内存中的 userdata 结构然后让游戏自己保存。唯一的文件级操作是 PAK 打包/解包、出怪列表 `.zbl` 保存/加载、阵型 `.yml` 列表读取。 | `src/pak.cpp`, `src/toolkit.cpp` |

### 0.2 调用约定 (version gating 的主要来源)
几乎所有远程调用都按三类分支: **原版 (1.0.x/1.2.0.1065/es/ru)**、**年度版 isGOTY()**、**泄露测试版 isBETA()** (thiscall 用 ECX)。此外 **粘度汗化 2010 (ZH) 与日语 (JA) 年度版** 经常再单独分支 (寄存器/压栈方式不同)。数据表中所有正式版本地址均完整, 只有两个测试版有 `0x00000000` 空项 (见 §7)。

### 0.3 前置检查
每个修改前都调 `GameOn()` (找到受支持版本且进程仍存活, 否则重新 `FindPvZ()`)。许多功能还要求 `GameUI()` 为 2 (选卡界面) 或 3 (战斗中), 个别要求 ==3。`GameMode()` = `[lawn]+game_mode`, `GameUI()` = `[lawn]+game_ui`。

### 0.4 版本检测 (FindPvZ, 精确流程)
1. 依次用 `FindWindowW(L"MainWindow", title)` 找窗口, 标题候选: `Bloom & Doom BETA 0.1.1.1014`, `Plants vs. Zombies BETA 0.9.9.1029` (仅 `_PVZ_BETA_LEAK_SUPPORT`), `Plants vs. Zombies`, `Plants vs. Zombies 1.2.0.1073`, `Plants vs. Zombies 1.2.0.1073 RELEASE`, `Plants vs. Zombies GOTY`, `Pflanzen gegen Zombies 1.2.0.1093`, `Plantas contra Zombis 1.2.0.1093`, `Plantes contre Zombies 1.2.0.1093`, `Piante contro zombi 1.2.0.1093`; 最后一轮标题传 `nullptr` (任意标题的 MainWindow 类窗口, 兼容改标题的版本)。
2. `OpenProcess(PROCESS_ALL_ACCESS)`; 失败 → `PVZ_OPEN_ERROR` (提示以管理员运行)。
3. 读 PE 头: `nth = 0x400000 + [0x400000+0x3c]`; 读 Debug Directory `lcd = 0x400000 + [nth+0xc8]`, 再读 CodeView 记录里的 PDB 路径字符串 `[lcd + [lcd] + 0x18]`。若 pdb 为空/不含 `.pdb`/不含 `\Lawn\` 或 `\lawn\` → 判定为"其他宝开游戏" → `PVZ_NOT_FOUND`; 否则先置 `PVZ_UNSUPPORTED`。
4. 读 PE `TimeDateStamp` = `[nth+0x08]`, 与表匹配:
   - `0x49359c21` BETA 0.1.1.1014 (en) / `0x499a6204` BETA 0.9.9.1029 (en)
   - `0x49ecf563` 1.0.0.1051 en / `0x4a37d6af` 1.2.0.1065 en
   - `0x4a5b7963` 1.0.4.7924 es / `0x4c237519` 1.0.7.3556 es / `0x4ce4c3d6` 1.0.7.3467 ru
   - `0x4c2e3453` GOTY 1.2.0.1073 en (Origin) / `0x4d02b058` GOTY 1.2.0.1096 en (Steam) / `0x4ca31baa` GOTY 1.2.0.1093 de/es/fr/it
   - `0x4c563de1` GOTY 1.1.0.1056 zh 2010 粘度汗化 / `0x4cc8e5f8` GOTY 1.1.0.1056 ja / `0x4fcd7be2` zh 2012-06 / `0x5003d437` zh 2012-07
5. 找到 NOT_FOUND/OPEN_ERROR 继续下一个标题; 找到支持或不支持版本即停。不支持时关闭句柄。结果通过回调刷新状态栏。
6. `main.cpp` 用 `Fl::repeat_timeout` 定时轮询 `GameOn()` (找到时 0.4s, 未找到 0.2s)。重新找到游戏后 `keep_selected_feature()` 会把所有已勾选的复选框重新应用一遍。
7. 注意: 非 `_DEBUG` 构建里 `IsDebuggerPresent()` 为真则 `exit(-42)`。

### 0.5 状态栏文字 (窗口顶部 `game_status`)
`0.1.1.1014 英语测试版` / `0.9.9.1029 英语测试版` / `1.0.0.1051 英语原始版` / `1.2.0.1065 英语修复版` / `1.0.4.7924 西班牙语版` / `1.0.7.3556 西班牙语版` / `1.0.7.3467 俄罗斯语版` / `1.2.0.1073 英语年度版` / `1.2.0.1096 英语年度版` / `1.2.0.1093 多国语言年度版` / `1.1.0.1056 粘度汗化版` / `1.1.0.1056 日语年度版` / `1.1.0.1056 年度加强版` (两个 2012) / `不支持的游戏版本` / `打开游戏进程出错` / `没有找到游戏窗口`。旁边 `🛈` 提示: 未找到→"请先打开植物大战僵尸游戏。", 打开出错→"请尝试用管理员权限运行修改器。", 不支持→"联系作者给这个版本添加支持。", 测试版→"测试版内存布局与正式版差别较大, 对测试版本仅提供有限的功能支持。", 粘度汗化→"建议换掉这个有严重问题的游戏版本。"(并弹窗一次建议去 pvz.lmintlcx.com/download 下载)。每年 5 月 5 日显示 🎂 (PvZ 生日)。

### 0.6 热键 / 全局交互
- **Ctrl + `+` / Ctrl + `-`**: FLTK 内置界面缩放 (screen_scale)。
- **Esc**: 不退出, 而是把缩放恢复为 `MinScale()` (按 DPI 取整的倍数)。
- 关闭窗口时同时关闭 "出怪数量统计" 子窗口。
- 下拉框 (`Fl_Choice_`) 支持鼠标滚轮切换选项并触发回调。
- 文件路径输入框支持拖拽文件 (拖入前先清空)。
- 首次启动: 非中文区域 (LangID 非 2052/1028/3076/5124/4100) 自动勾选 English Tooltips; 按系统版本自动选主题 (Win10/11→gtk+, Win8/8.1→oxy, Vista/7→gleam, XP→plastic, 2000→base); 询问是否打开在线文档; 高 DPI 时提示缩放快捷键。
- 单实例: 互斥量 `PvZ Toolkit`。启动画面 `splash.jpg/png/bmp` (同目录) 显示 1.2s (官方 splash.png 0.3s)。
- **设置持久化** (注册表 `HKCU\Software\Cube Studio\PvZ Toolkit\v1`): `ToolTips`, `ScaleFactor`, `Scheme`, `SpawnTypes` (20 位 '0'/'1' 字符串), `SpawnMode`, `ValueSun`, `ValueMoney`, `ValueLevel`, `CurrentPage`。`misc/uninstall.reg` 用于删除。
- **命令行模式**: `pvztoolkit.exe /U <file.pak> <dir>` 解包, `/P <file.pak> <dir>` 打包 (argc==4, 无界面)。
- Win8+ 按钮文字带 Emoji (`EMOJI()` 宏), 否则用 `▢`。
- 默认打开 "出怪" 选项卡; 默认焦点在 "阳光" 按钮。

---

## 1. 选项卡「资源」(Resources)

### 1.1 阳光无上限 — No Upper Limit to Sunlight
- 控件: 圆形单选钮 `check_unlock_sun_limit` (无文字, tooltip "阳光无上限")。
- 机制: 代码补丁 `enable_hack(data().unlock_sun_limit, on)`; 1.0.0.1051 为 `0x00430a23: 0x7e→0xeb` (跳过 9990 上限判断)。
- 备注: 不勾时阳光输入框上限仍是 9990 (UI bounds 0~9990)。

### 1.2 阳光 — Sun (按钮 `阳光`, 输入框 0~9990, 默认 8000)
- 机制: 内存写 `WriteMemory<int>(sun, {lawn, board, sun})`。要求 GameUI 为 2 或 3。
- 值会保存到注册表 `ValueSun`。

### 1.3 金币 — Coins (标签 `钱包`/💰, 按钮 `金币`, 0~99999, 默认 80000)
- 机制: 内存写 `{lawn, user_data, money}`; 需 `[lawn]+user_data != 0` (已建立用户)。游戏内显示 ×10。
- 保存到注册表 `ValueMoney`。

### 1.4 自动收集 — Auto Collected
- 代码补丁 `auto_collected` (1051: `0x0043158f 0x75→0xeb`)。阳光/金币自动收取。

### 1.5 不掉战利品 — Don't Drop Loot
- 代码补丁 `not_drop_loot` (1051: `0x00530276 0x5b→0x66`)。僵尸不掉钱/不掉卡片等。

### 1.6 花肥无限 — Infinite Fertilizer
- 代码补丁 `fertilizer_unlimited` (使用后不扣) + 开启时写 `{lawn, user_data, twiddydinky + 14*4} = 1020` (花肥数量, 实际显示 1020-1000=20)。

### 1.7 杀虫剂无限 — Infinite Bug Spray
- 代码补丁 `bug_spray_unlimited` + 开启时写 `twiddydinky + 15*4 = 1020`。

### 1.8 树肥无限 — Infinite Tree Food
- 代码补丁 `tree_food_unlimited` + 开启时写 `twiddydinky + 28*4 = 1010`。
- 版本: BETA 0.1.1.1014 无智慧树, 直接返回且该控件被禁用 (`cb_find_result` 中 deactivate)。

### 1.9 巧克力无限 — Infinite Chocolate
- 代码补丁 `chocolate_unlimited` (vector, 两处) + 开启时写 `twiddydinky + 26*4 = 1010`。

### 1.10 树高 — Wisdom Tree (标签 `树高`/🌳, 按钮 `英尺`, 0~1000, 默认 1000)
- 机制: 若当前 `GameMode()==50` (禅境花园/智慧树): 写 `{lawn, user_data, tree_height} = height-1`, 再远程调用 `call_wisdom_tree` 让树长一次 (把高度补到 height 并刷新画面)。寄存器分支: GOTY 一般版 ESI=challenge; ZH2010/JA 年度版 push EBX(challenge); ZH2012 用 EDI; 原版 `mov edi, lawn` 直接 call; BETA 0.9.9 ECX。
- 否则直接写 `tree_height = height`。
- 版本: BETA 0.1.1.1014 直接返回。

### 1.11 免费用卡 — Free Planting
- 机制: 内存写 `{lawn, free_planting} = on` (游戏内置的免费种植标志, 即调试用 bool)。如果在战斗中 (GameUI==3) 还遍历卡槽 (`slot`, 每格结构 0x50, `slot_count`), 把每张卡 `slot_seed_cd_past = slot_seed_cd_total` 即**清空冷却**。

### 1.12 随意放置 — Place Anywhere
- 代码补丁三处: `placed_anywhere` (种植合法性判断), `placed_anywhere_preview` (预览), `placed_anywhere_iz` (我是僵尸模式)。

### 1.13 无缝传送 — Seamless Conveyor Belt
- 代码补丁 `fast_belt` (vector 两处)。传送带关卡卡片立即到达。

### 1.14 连续铲子 — Shovel Continuously
- 代码补丁 `lock_shovel` (1051 `0x004123a3 0x89→0x39`, 使铲子用完不放回)。同时若在 UI 2/3, 开启时写 `[cursor]+cursor_grab = 6` (光标立即变铲子), 关闭时若当前为 6 则写 0。

### 1.15 混乱关卡 — Modify Mode (下拉 `choice_mode` 73 项 `[0] 冒险模式`…`[72] 介绍`, 下拉 `choice_adventure` `1 - 1`…`5 - 10` 仅冒险模式时可用, 按钮 `混乱关卡`)
- 机制: `MixMode(mode, level)`: 需 UI 2/3。mode 下标即游戏 `game_mode` 值 (0 冒险, 1~5 生存, 6~10 困难, 11~15 无尽, 16~35 小游戏, 36~50 隐藏小游戏(含 50 智慧树), 51~60 解谜, 61~70 我是僵尸, 71 Upsell, 72 Intro)。冒险模式时 level = 下拉索引+1 (1~50), 写 `{lawn,user_data,level}` 和 `{lawn,board,adventure_level}`; 之后写 `{lawn, game_mode} = mode`。
- 效果: 在当前关卡里把模式改成别的 (例如在无尽里切换成冒险 4-10 出僵王), 即"混乱关卡"。
- 备注: 不重新加载关卡, 只改模式标志, 需要用户自行理解后果。

### 1.16 无尽轮数 — Endless Rounds (输入框 0~999999, 默认 1010, 按钮 `无尽轮数`)
- 机制: 仅当 `GameMode()` 为 11~15 (生存无尽), 60 (破罐者无尽), 70 (我是僵尸无尽) 时写 `[[lawn]+board]+challenge +endless_rounds = level`。默认 1010 → 显示 2022 旗帜。
- 保存到注册表 `ValueLevel`。

### 1.17 通关存档 — Get Gold Sunflower Trophy (按钮 `🏆 通关存档`, tooltip "解锁黄金向日葵奖杯")
- 机制 (`UnlockTrophy`): 纯内存写 userdata 结构 (`[lawn]+user_data`, 为 0 则返回), 之后游戏自己保存到 user*.dat:
  1. `playthrough` (冒险通关次数) < 2 则写 2。
  2. `mini_games` 起: 生存 5 关写 5, 困难 5 关写 10 (跳过无尽 5 个); 20 个小游戏写 1 (BETA 另有特殊位置); 跳过 13+1 隐藏小游戏和松鼠, 跳过智慧树高度 (BETA 0.1.1 没有); 破罐者 9 关写 1, 跳无尽; 我是僵尸 9 关写 1, 跳无尽。
  3. `twiddydinky` 起 (商店购买项): 8 个升级植物 (机枪/双子/忧郁/香蒲/冰瓜/吸金/地刺王/玉米炮) 写 1; 模仿者写 1; 跳过 未知/金盏花苗x3/金浇水壶/花肥/杀虫剂/留声机/手套/蘑菇园/手推车/蜗牛; 卡槽数写 4 (6+4=10 格); 泳池清洁器 1; 屋顶清洁器 1; 跳过钉耙/水族馆/巧克力; 智慧树 1, 跳过树肥, 坚果急救 1 (BETA 0.1.1 无后三项)。
  4. 年度版: `userdata+0x24` 起成就 bool 数组全写 true (Steam 1096 为 21 个, 其他 GOTY 20 个)。
  5. 若原本 playthrough==0 且 `GameUI()==1` (主菜单): 远程调用 `call_sync_profile` (push 1 显示 Loading, ECX = `[lawn]+game_selector`; ZH2010/JA 额外 `mov esi,ecx`) 刷新主界面。
- 备注: 没有解锁"隐藏小游戏"与"松鼠", 没有给金币。

### 1.18 直接过关 — Level Complete (按钮 `直接过关` + 左侧小复选框 `check_brightest_cob_cannon` tooltip "结束时炮最亮", 默认勾选)
- 机制 (`DirectWin(brightest)`): 需 `GameUI()==3`。在单独线程执行 (UI 按钮禁用直到完成)。
  - 若勾选"炮最亮"且 GameMode 1~15 (生存): 读 `game_clock`, 计算 `frame_to_wait = 75 - ((clock+500)%75)`; 通过 `PostMessage(hwnd, WM_KEYDOWN/UP, VK_SPACE)` 控制暂停/恢复, `Sleep(frame_to_wait*帧时长)` 使关卡结束时玉米炮处于最亮状态, 然后暂停。
  - 远程调用 `call_fade_out_level` (ECX=board; ZH2010/JA 用 push EAX)。
  - 若 light_cob 则再发空格解除暂停。
- 备注: 原理是调用游戏的关卡淡出函数, 等同正常胜利。

---

## 2. 选项卡「战场」(Battlefield)

公共选择器: `choice_row` (`所有行`, `第 1 行`…`第 6 行`, 默认第 1 行), `choice_col` (`所有列`, `第 1 列`…`第 9 列`, 默认第 1 列), `choice_plant` (`[0] 豌豆射手`…`[47] 玉米加农炮`), `choice_zombie` (`[0] 普僵`…`[32] 红眼`)。"所有行/列" 传 -1 表示遍历; 行数由 `GetRowCount()` (场景 2/3 泳池/雾夜为 6, 其他 5) 决定。

### 2.1 种植物 — Put Plant (+ 复选框 `模仿者` Imitater)
- 机制 `PutPlant(row,col,type,imitater)`: 需 UI 2/3。一次 `asm_init()` 把所有格子的调用拼成一段代码再注入一次。每格 `asm_put_plant`: push (imitater? type,48 : -1,type); EAX=row, push col, EBP=board, push EBP; call `call_put_plant`。BETA 改为 push row, push col, ECX=board。模仿者时额外: 计算新植物地址 = `[board]+plant + [board]+plant_next_pos * 0x14c`, ESI=EAX(植物), call `call_put_plant_imitater` (播放模仿者变身), 结果保持 EAX=植物地址。若 GameMode 61~70 (我是僵尸) 还 call `call_put_plant_iz_style` (EAX=challenge) 让植物成为 IZ 风格。
- 玉米加农炮 (47): 列数上限 8 且步长 2 (占两格)。
- 不检查格子是否已有植物/是否水路, 直接叠加。

### 2.2 出僵尸 — Put Zombie
- `PutZombie(row,col,type)`: 需 UI 2/3。type==25 僵王博士: 远程调用 `call_put_zombie_in_row` (EAX=board, push 0, push 25) 一次。其他: 每格 `asm_put_zombie`: push col, push type, EAX=row, ECX=challenge, call `call_put_zombie`; ZH2010/JA: push type, push challenge, EAX=row, ECX=col; BETA: push row,col,type, ECX=challenge。

### 2.3 搭梯子 — Put Ladder
- `PutLadder`: 每格 EDI=row, push col, EAX=board, call `call_put_ladder` (BETA: push row,col, ECX=board)。

### 2.4 冒墓碑 — Put Grave
- `PutGrave`: 每格 GOTY: ECX=challenge, push ECX, EDI=row, EBX=col, call `call_put_grave`; 原版用 EDX=challenge push; BETA push row,col。

### 2.5 放钉耙 — Put Rake
- `PutRake`: 需 UI 2/3。由于游戏的放钉耙函数随机选行且只放一次, 先**临时打补丁**: 读出并备份 `call_put_rake_col` (8 字节) 和 `call_put_rake_row` (原版 7 / GOTY 6 字节), 把行选择代码改为 `mov edx/edi/ebp, imm32; nop`, 打开 `rake_unlimited` 补丁 (忽略钉耙库存判断); 每格 `asm_put_rake` 直接把 row 写到 `call_put_rake_row+1`, col 写到 `call_put_rake_col+4`, 然后远程调用 `call_put_rake` (EAX/EDX/ECX=board 各分支, ZH2010/JA 用 push ECX)。每格单独注入一次。完成后关闭 `rake_unlimited` 并把备份字节写回。

### 2.6 小推车 — Lawn Mower (菜单按钮: `➡️ [启动]`, `🆑 [删除]`, `🔄 [恢复]`)
- `SetLawnMowers(option)`: 需 `GameUI()==3`。遍历 `[board]+lawn_mower` (结构 0x48, `lawn_mower_count_max`, 跳过 `lawn_mower_dead`):
  - 0 启动: 每辆 ESI=addr (ZH2010/JA 用 EBX, BETA ECX) call `call_start_lawn_mower`。
  - 1 删除: EAX=addr (BETA ECX) call `call_delete_lawn_mower`。
  - 2 恢复: 先打开补丁 `init_lawn_mowers` (允许初始化) 和 `lawn_mower_initialize` (把初始化 X 坐标常量指针改为 -21.0 而不是 -160.0, 即不播放入场动画), 删除所有现有推车, 再 push board call `call_restore_lawn_mower`, 最后关闭两个补丁。

### 2.7 清除 — Clear All (下拉 `choice_item`: `植物` / `僵尸` / `梯子` / `墓碑` / `钉耙`, 默认墓碑; 按钮 `清除`)
- 植物 `ClearAllPlants()`: 遍历 `[board]+plant` (0x14c 每个, `plant_count_max`), 非 dead 且非 squished 的 push addr call `call_delete_plant` (BETA ECX)。
- 僵尸 `KillAllZombies()`: 纯内存写: 遍历 `[board]+zombie` (结构 GOTY 0x168 / 原版 0x15c / BETA0.1.1 0x160), 非 `zombie_dead` 的写 `zombie_status = 3` (秒杀状态)。
- 梯子/墓碑/钉耙 `ClearGridItems({3}/{1}/{11})`: 遍历 `[board]+grid_item` (0xec, BETA0.1.1 0x8c), 类型匹配且未 dead 的 ESI=addr (BETA ECX) call `call_delete_grid_item`。grid item 类型: 1 墓碑, 2 弹坑, 3 梯子, 4/5 传送门, 7 罐子, 10 蜗牛, 11 钉耙, 12 脑子。

### 2.8 植物无敌 — Plant Invincible (与"植物脆弱"互斥, 勾一个自动取消另一个)
- 代码补丁 9 处: `plant_immune_eat`, `plant_immune_radius`, `plant_immune_jalapeno`, `plant_immune_projectile`, `plant_immune_lob_motion`, `plant_immune_square`, `plant_immune_row_area`, `plant_immune_spike_rock`, `plant_immune_squish`。BETA 版另有硬编码的 eat 补丁。

### 2.9 植物脆弱 — Plant Weak
- 代码补丁 `_plant_immune_eat`, `_plant_immune_projectile`, `_plant_immune_lob_motion`, `_plant_immune_row_area` (把减血改成归零/一击死)。

### 2.10 僵尸无敌 — Zombie Invincible (与"僵尸虚弱"互斥)
- 代码补丁 11 处: `zombie_immune_body_damage`, `zombie_immune_helm_damage`, `zombie_immune_shield_damage`, `zombie_immune_burn_crumble`, `zombie_immune_radius`, `zombie_immune_burn_row`, `zombie_immune_chomper`, `zombie_immune_mind_controll`, `zombie_immune_blow_away`, `zombie_immune_splash`, `zombie_immune_lawn_mower`。

### 2.11 僵尸虚弱 — Zombie Weak
- 代码补丁 `_zombie_immune_body_damage`, `_zombie_immune_helm_damage`, `_zombie_immune_shield_damage`, `_zombie_immune_burn_crumble` (一击死)。

### 2.12 立刻装填 — Reload Instantly
- 代码补丁 `reload_instantly` (玉米炮装填), `grow_up_quickly` (vector: 土豆雷/阳光菇立即长大), `no_cooldown` (vector: 大嘴花/磁力菇无冷却)。

### 2.13 只投黄油 — Always Butter
- 代码补丁 `lock_butter` (玉米投手只投黄油)。

### 2.14 暂停刷怪 — Pause Spawning
- 代码补丁 `stop_spawning` (跳过刷怪倒计时)。

### 2.15 停滞不前 — Stay In Place
- 代码补丁 `stop_zombies` (vector 两处, 僵尸位置不更新)。

### 2.16 蘑菇睡醒 — Mushroom Awake
- 代码补丁 `mushrooms_awake` (白天种蘑菇不睡) + 若 UI 2/3 且开启: 遍历所有植物, `plant_asleep` 为真的: (GOTY EDI=addr / BETA ECX / 原版 EAX), push 0, call `call_set_plant_sleeping` 即唤醒。

### 2.17 核弹无坑 — No Crater
- 代码补丁 `no_crater` (毁灭菇不留弹坑)。

### 2.18 冰车无痕 — No Ice Trail
- 代码补丁 `no_ice_trail` (vector 两处) + 开启且 UI 2/3 时把 6 行 `[board]+ice_trail_cd + i*4` 写 1 (立即消除现有冰道)。

### 2.19 丑椒不爆 — Never Explode
- 代码补丁 `zombie_not_explode` (vector 两处: 小丑不爆, 辣椒僵尸不爆)。

---

## 3. 选项卡「阵型」(Setups)

### 3.1 卡槽查看 (下拉 `choice_slot`: `第 1 格`…`第 10 格`)
- 切换时回调 `cb_get_seed`: `GetSlotSeed(index)` 读 `[board]+slot`, 每格 0x50, `slot_seed_type`, `slot_seed_type_im`; 若类型为 48 (模仿者) 返回 `im+48`。结果填到 `choice_seed` 和 `模仿者` 复选框。

### 3.2 换卡片 — Replace Seed (下拉 `choice_seed` 48 种植物 + 复选框 `模仿者` + 按钮 `换卡片`)
- `SetSlotSeed(index,type,im)`: 内存写; 模仿者: `slot_seed_type=48, slot_seed_type_im=type`; 否则 `slot_seed_type=type, im=-1`。需 UI 2/3。
- 备注: 不改变冷却, 不检查是否已解锁该植物。

### 3.3 快捷布阵模式 — Lineup Mode (复选框 `快捷布阵模式`)
- 纯 UI 组合功能: 勾选时把 `自动收集`, `免费用卡`, `植物无敌`, `立刻装填`, `暂停刷怪`, `清除浓雾` 以"值=1"临时触发回调(开启), 然后恢复它们原本的勾选显示状态并禁用这些复选框 (`植物脆弱` 只禁用不触发); 取消时重新启用并按各自当前勾选状态重新回调 (即恢复用户原设置)。
- 在 `keep_selected_feature()` 中最后处理, 重新找到游戏时会再次应用。

### 3.4 智能搭梯 — Smart Laddering (按钮)
- `AutoLadder(true)`: 先 `ClearGridItems({3})` 清空梯子; 遍历植物, 类型 30 (南瓜) 且 `plant_imitater==48` (**只给模仿南瓜**), 列 != 0 且该格 `block_type`==1 (草地, 读 `[board]+block_type + 4*row + 0x18*col`) 的格子上 `asm_put_ladder`; 合并注入。
- 备注: 函数参数 `imitater_pumpkin_only` 默认 true, UI 没有开放"所有南瓜"选项。

### 3.5 水路睡莲 — Pool's Lily Pad (菜单按钮, 8 项用 ■□ 表示列范围: 1-2, 1-3, 1-4, 1-5, 1-6, 1-7, 1-8, 1-9)
- `LilyPadOnPool(from,to)`: 扫描现有植物建 `has_plant[6][9]`; 遍历所有行列, `block_type==3` (泳池) 且无植物且列在范围内的格子 `asm_put_plant(r,c,16,false,false)`; 一次注入。

### 3.6 屋顶花盆 — Roof's Flower Pot (菜单 7 项: 1-3, 4-4, 4-5, 4-6, 4-7, 4-8, 4-9)
- `FlowerPotOnRoof`: 需场景 4/5 (屋顶/月夜); 5 行 × 9 列无植物且列在范围内的格子种花盆 (33)。

### 3.7 重置场地 — Reset Scene (按钮 `⏳ 重置场地` + 下拉 `choice_scene`: `[0] 白天`, `[1] 黑夜`, `[2] 泳池`, `[3] 雾夜`, `[4] 屋顶`, `[5] 月夜`, 默认泳池)
- `SetScene(scene, reset=true)`: 需 UI 2/3。
  1. 若目标场景 != 当前 `[board]+scene`: 有推车先 `SetLawnMowers(1)` 删除; 远程调用: ESI=board, `mov [esi+scene], scene`, call `call_pick_background` (BETA 额外 ECX=ESI, 并临时补丁 `0x004103e1`/`0x00416e31` 处 scene_id 映射); 然后直接内存写 `block_type` 6×9 表 (非泳池: 前 5 行全 1 草地, 第 6 行 2 裸地; 泳池/雾夜: 第 3,4 行 3 泳池) 和 `row_type` 6 项 (1 陆地 / 2 水路 / 0 无); 有推车则 `SetLawnMowers(2)` 恢复; 非泳池/雾夜时遍历粒子系统 (`[lawn]+anim+unnamed+particle_system`, 0x2c 每个), 类型 34 (水波光) 的 call `call_delete_particle_system`, 再 `mov [board+particle_systems_addr],0`; 最后 `SetMusic(scene+1, 月夜→2)`。
  2. reset 为真: EDI=challenge, `dec [edi+endless_rounds]` (轮数减 1), call `call_puzzle_next_stage_clear` (ZH2010/JA 先 push EDI, BETA ECX=EDI) → 清场并重新开始本轮 (轮数不变)。
- 备注: 也被 `SetLineup` 在场景不符时自动调用。

### 3.8 加载阵型列表文件 / 阵型选择 (按钮 `加载阵型列表文件 (***.yml)`, 加载后隐藏并换成 6 个按场景的下拉 `choice_lineup_name[scene]`)
- 启动时自动扫描**当前工作目录**下所有 `*.yml` 并导入 (`import_lineup_list(true)`); 按钮则弹出文件对话框选单个文件。
- 文件格式 (`misc/setups.yml`): 首行必须 `#! pvztoolkit`; `#` 注释; 每行 `"名称": 代码` (正则 `".*": [base64|base64url]`); 解析为 `Lineup(name, code)`, 失败行收集后弹窗 "阵型列表文件格式错误" (最多显示 12 行)。
- 阵型按 `weight` 排序 (场景×1e16 + 玉米炮×1e14 + 香蒲×1e12 + 双子×1e10 + 冰瓜×1e8 + 南瓜×1e6 + 睡莲×1e4 + 花盆×100 + 梯子), 同名后补空格去重。切换场景下拉时显示对应列表, 选择阵型后把代码填入文本框。

### 3.9 阵型代码文本框 (`editor_lineup_string`, 只读, Consolas 字体, tooltip "(阵型代码)")

### 3.10 获取代码 — Get Lineup Code
- `GetLineup()`: 需 UI 2/3。`scene=GetScene()`; 遍历植物: 16/33 → `base`=1/2 (+`base_im`), 30 → `pumpkin`(+im), 35 → `coffee`(+im), 其他 → `plant=type+1`, `plant_im`, `plant_awake=!asleep`; 遍历 grid item: 1 墓碑 → `base=3`, 3 梯子 → `ladder`, 11 钉耙 → `rake_row=row+1`。然后 `Generate()` 编码 (见 3.13) 显示。

### 3.11 复制导出 — Copy (Export)
- `Fl::copy` 文本框内容到剪贴板 (UI-only)。

### 3.12 粘贴导入 — Paste (Import)
- 从剪贴板粘贴, 去除 `\r\n\t\v\f`, 首尾空格, 多余空格, `, `→`,`; 然后 `Lineup(str)` 解析 (同时兼容旧版 PvZ Tools 的**阵型字符串**格式 `场景,hexType row col state x im ...` 和新版 Base64 **阵型代码**), 统一重新 `Generate()` 成新格式显示。

### 3.13 阵型代码格式 (docs/rfc1437.txt)
- Base64 / Base64URL, 长度 18~164; 解码后每字节 XOR 0x54; 结构 = zlib(Z_BEST_COMPRESSION) 压缩流 + 1 字节 RS (高 4 位钉耙行 R, 低 4 位场景 S)。解压后为 54 (泳池/雾夜) 或 45 个 uint16 GRID: bit10-15 P 主植物(0 无, 1~48), bit9 PI 模仿, bit8 PA 醒着, bit6-7 B (0 无, 1 睡莲, 2 花盆, 3 墓碑), bit5 BI, bit4 K 南瓜, bit3 KI, bit2 C 咖啡, bit1 CI, bit0 L 梯子。
- 校验: 场景 <6; 钉耙行不超过行数且泳池/雾夜时不在 3/4 行; 解压长度必须匹配。

### 3.14 布置阵型 — Apply Current Setup (一键布阵)
- `cb_set_lineup`: 先把 Base64URL 转标准 Base64 并补 `=`, `Lineup(str)`, `SetLineup(lineup)`。
- `SetLineup` 精确流程:
  1. `lineup.OK()`, `GameOn()`, UI 2/3; **仅允许 GameMode 11~15 (生存无尽) 或 61~70 (我是僵尸)**, 否则静默返回。
  2. `ClearGridItems({1,2,3,11})` (墓碑/弹坑/梯子/钉耙) + `ClearAllPlants()`。
  3. 若 `GetScene() != lineup.scene` → `SetScene(lineup.scene, true)` (自动切场景并重置)。
  4. 钉耙: 若 `rake_row != 0` → `PutRake(rake_row-1, 7)` (固定第 8 列, 单独注入)。
  5. 一次 `asm_init()` 合并以下全部调用后注入一次, 顺序为: **睡莲/花盆 (base 1/2, 含模仿者) → 主要植物 (跳过 16/33/30/35 和越界类型; 每株后: 若场景为 0/2/4 白天且该植物 `may_sleep` 且 awake, 则 push EAX, (GOTY EDI=EAX / BETA ECX=EAX), push 0, call `call_set_plant_sleeping`, pop EAX 唤醒; 若为土豆雷 4 或阳光菇 9, 写 `mov [eax+0x54],1` (BETA +0x5c) 使其直接长大) → 南瓜壳 (30) → 咖啡豆 (35) → 墓碑 (`asm_put_grave`) → 梯子 (`asm_put_ladder`)**。IZ 模式下每株植物附带 `call_put_plant_iz_style`。
  6. `Sleep(帧时长)`。
- `may_sleep` 表 (会睡的植物): 8 小喷, 9 阳光菇, 10 大喷, 12 魅惑, 13 胆小, 14 寒冰菇, 15 毁灭, 24 海蘑菇, 31 磁力菇, 42 忧郁菇。
- 备注/坑: 不会在非无尽/非 IZ 模式下工作 (按钮无反应); 咖啡豆会被种下但不会自动"生效"(由游戏自己处理); 墓碑需要场景支持 (夜晚)。

### 3.15 截图 — Screenshot (按钮 `📷`/`截图`, tooltip "截图到剪贴板")
- `Screenshot()`: `GetDC(hwnd)` + `BitBlt` 固定 800×600 客户区到位图, `SetClipboardData(CF_BITMAP)`。不保存文件。

---

## 4. 选项卡「出怪」(Spawning)

### 4.1 僵尸种类复选框 (20 个, 默认布局顺序: 路障 2, 撑杆 3, 铁桶 4, 读报 5, 铁门 6 / 橄榄 7, 舞王 8, 潜水 11, 冰车 12, 海豚 14 / 小丑 15, 气球 16, 矿工 17, 跳跳 18, 雪人 19 / 蹦极 20, 扶梯 21, 投篮 22, 白眼 23, 红眼 32)
- UI 规则: 普僵 0 总是出; 旗帜 1 在极限/模拟模式中总是加上; **种类总数限制** (`limit_species`): 含普僵、不含雪人/蹦极, 选满 11 种后其他未选项灰掉 (游戏出怪种类上限); 路障 (index 0) 与读报 (index 3) 互斥 (勾一个自动取消另一个, 模拟游戏规则)。勾选状态保存到注册表 `SpawnTypes`。

### 4.2 右键菜单 (`button_spawn_extra`, 覆盖复选框区域, 右键弹出): `❌ [清空已选]`, `❎ [取消限制]` / `✅ [启用限制]` (切换 limit_species, 同时解除互斥), `🔀 [切换布局]` (仅中文 UI; 在默认布局与"小王子出怪器" xwz 布局之间切换复选框位置)。

### 4.3 查看详情 / 隐藏详情 — Show/Hide Details (按钮 `📈 查看详情` ↔ `📉 隐藏详情`)
- 打开子窗口 "出怪数量统计" (`SpawnWindow`): 33 行 (仅显示出现的种类) × 20 波 + 总数列的表格, 每格数量, 颜色随数量加深, 第 10/20 波边框高亮; 左上 `···` 按钮刷新。右键菜单: `🔄 [刷新]`, `💾 [保存]`, `🔖 [加载]`。
- 读取逻辑 `cb_zombies_list`: 需 UI 2/3; GameMode 1~5 (普通生存) 只取前 10 波 (后 500 项置 -1); 6~15 取全部 20 波; 其他模式不支持 (显示空)。
- **保存**: 写 `zombies\YYYY.M.D_H.M.S.zbl` (在修改器所在目录, `system("mkdir zombies")`), 格式: int32 header `0x58434c52` ('RLCX'), version 1, length 1000, 1000 个 int32 出怪列表, CRC32 (zlib) 校验。
- **加载**: 选 `.zbl` (初始目录 `zombies`), 校验大小 1004×4、头、CRC, 然后 `SetSpawnList(zl)` 写入游戏并刷新, 提示 "出怪列表已经导入到游戏中."。

### 4.4 红眼权重下拉 (`choice_giga_weight`: 1000~6000 步长 100, 仅模拟模式可用; tooltip "非旗帜波的红眼僵尸随机权重。原始值: 普通波 1000, 旗帜波 6000。")

### 4.5 变速 — No GigaGargantuar in 11th~19th waves (复选框 `变速`, tooltip "第 11 ~ 19 波不出红眼僵尸。", 自然模式下禁用)

### 4.6 出怪按钮 (标签随模式变: `自然出怪 (游戏生成)` / `极限出怪 (均匀填充)` / `模拟出怪 (加权随机)`, 右键弹出 `button_spawn_mode` 菜单 "选择刷怪模式：" 切换三种, 默认极限; tooltip "右键切换出怪列表生成方法。"; 模式保存到注册表 `SpawnMode`)
- 公共前置: GameOn, UI 2/3; 极限/模拟**仅限 GameMode 1~15 生存模式**; 自然模式任何模式均可。
- **自然出怪** `InternalSpawn(zombies)`: 内存写 `[board]+spawn_type` (33 个 bool, 普僵强制 true); 把 `[board]+spawn_list` 写 2000 个 -1 (清空); 远程调用 `call_pick_zombie_waves` (EDI=board; ZH2010/JA EAX; BETA ECX) 让游戏自己重新生成; 若在选卡界面 (UI==2) 再 `update_spawn_preview()`。
- **极限出怪** `CustomizeSpawn(zombies, limit_giga, simulate=false, 1000)`: 本地生成 1000 项列表 (20 波 × 50): 按已选种类循环 `type=(type+1)%33` 均匀填满每个位置 (跳过未选、旗帜、雪人、蹦极, 以及变速时第 11~19 波的红眼); 然后固定位置: 旗帜波 (第 10/20 波, 索引 450/950) 首位放旗帜 1; 选了蹦极时 459~462 / 959~962 放蹦极 20; 选了雪人则随机一个位置放雪人 19。`WriteMemory` 写 `[board]+spawn_list`; UI==2 时刷新预览。
- **模拟出怪** `simulate=true`: 用 `std::discrete_distribution` 按游戏原始权重随机 (普僵 400, 路障 1000, 撑杆 2000, 铁桶 3000, 读报 1000, 铁门 3500, 橄榄 2000, 舞王 1000, 潜水 2000, 冰车 2000, 雪橇 2000, 海豚 1500, 小丑 1000, 气球 2000, 矿工 1000, 跳跳 1000, 雪人 1, 蹦极 1000, 扶梯 1000, 投篮 1500, 白眼 1500, 豌豆 4000, 坚果 3000, 辣椒 1000, 机枪 2000, 倭瓜 2000, 高墙 2000, 红眼 6000; 旗帜波使用红眼 6000, 普通波使用用户选的 giga_weight); 旗帜波额外固定 450/950 旗帜, 451~458/951~958 普僵; 蹦极/雪人处理同上。
- `update_spawn_preview()` (选卡界面刷新预览僵尸): 打开补丁 `hack_street_zombies`, 远程调用 `call_remove_cutscene_zombies` (EBX=board) + `call_place_street_zombies` (push `[board]+cut_scene`), 关闭补丁。
- `GetSpawnList()` 读 `[board]+spawn_list` 1000 个 int, 每波遇到 -1 后其余置 -1。
- 备注: 出怪列表写入后若处于战斗中, 当前波之后的波按新列表出。

---

## 5. 选项卡「杂项」(Others)

### 5.1 背景音乐 — Background Music (下拉 `[1] Grasswalk` … `[12] Brainiac Maniac`, 默认 [5] Graze the Roof; 按钮 `🎵 背景音乐`)
- `SetMusic(id)`: 远程调用 `call_play_music` (EDI=id, EAX=`[lawn]+music`; BETA push id, ECX=music)。不需要在战斗中。

### 5.2 存档文件夹 — Open Userdata Folder (按钮 `💾 存档文件夹`)
- 依次尝试用 `ShellExecute` 打开存在的目录: `<游戏exe目录>\userdata` (读 `[path]` 字符串, 2000/XP 存档位置); `%ProgramData%\PopCap Games\BloomAndDoom\userdata`, `...\PopCap Games\PlantsVsZombies\userdata`, `...\Steam\PlantsVsZombies\userdata`, `...\Zoo\PlantsVsZombies\userdata`, `...\Reflexive\PlantsVsZombies\userdata`; 以及注册表 `HKLM\SOFTWARE\Valve\Steam\InstallPath` + `\userdata\<每个账号>\3590\remote` (Steam 云存档)。所有存在的都打开 (可能弹多个资源管理器)。

### 5.3 清除浓雾 — Clear Fog
- 代码补丁 `no_fog` (uint16)。

### 5.4 罐子透视 — Transparent Vase
- 代码补丁 `see_vase` (uint32, 破罐者罐子显示内容)。

### 5.5 允许后台运行 — Enable PvZ to Run in Background (默认勾选)
- 代码补丁 `background_running` (uint16, 失去焦点不暂停)。

### 5.6 禁止存档删档 — Userdata Readonly
- 代码补丁 `disable_delete_userdata` + `disable_save_userdata` (uint32, 跳过保存调用); Steam 1096 额外硬编码补丁 `0x00498440: {0x6a,0xff,0x68}→{0xc2,0x0c,0x00}` (直接 ret 保存函数, 因 Steam 云同步自带不能删档)。

### 5.7 解包 — Extract (按钮 `选择文件` 选 `*.pak`, 路径框 (可拖拽), 按钮 `解包`)
- `PAK::Unpack(src, dir)` 在工作线程中执行 (按钮禁用), 选择文件后若目录为空自动填 `<exe目录>\extract_<时间戳>`。结果弹窗 "解包完成/解包出错" + 具体原因 (打开源文件失败 / 获取大小失败 / 读取失败 / 文件头格式不正确 / 文件数据已经损坏 / 路径创建失败 / 文件创建失败 / 写入失败)。文件级操作, 与游戏进程无关。

### 5.8 打包 — Pack (按钮 `选择目录`, 路径框, 按钮 `打包`)
- `PAK::Pack(dir, <dir父目录>\main_<时间戳>.pak)`; 错误: 打开源文件夹失败 / 源文件夹为空 / 打包路径创建失败 / 打包文件创建失败 / 写入失败 / 源文件读取失败。

### 5.9 调试 — Debug (下拉 `关闭` / `出怪` / `音乐` / `内存` / `碰撞`, 按钮 `调试`)
- `DebugMode(mode)`: 内存写 `[board]+debug_mode = 0..4` (游戏内置调试显示模式)。需 UI 2/3。

### 5.10 速度 — Speed (下拉 `10x` `5x` `2x` `1x` `0.5x` `0.2x` `0.1x`, 默认 1x, 按钮 `速度`)
- `SetFrameDuration(ms)`: 内存写 `[lawn]+frame_duration` = 1/2/5/10/20/50/100 毫秒每帧 (游戏默认 10)。注入时的等待时间也基于此值。

### 5.11 显示隐藏页面 — Show Limbo Page (默认勾选)
- 代码补丁 `unlock_limbo_page` (3 字节, 让小游戏选择界面显示 Limbo 隐藏关卡页)。

### 5.12 界面样式 (下拉 `base` / `plastic` / `gtk+` / `gleam` / `oxy`)
- `Fl::scheme()` UI-only, 保存到注册表 `Scheme`。

### 5.13 English Tooltips (复选框, 仅中文 UI 构建)
- 开启后每个控件显示英文 tooltip, 窗口标题改为 "PvZ Toolkit - Plants vs. Zombies Tool Kit", 子窗口标题 "Spawning Counting"。保存到注册表 `ToolTips`。

### 5.14 文档 — Document (按钮 `🌏 文档`): 打开 https://pvz.lmintlcx.com/toolkit/。

### 5.15 关于 … — About: 弹窗显示版本 (1.22.0 (65536)), 编译时间, 官网, 版权 "© 2020 ~ 年份 L.Mint. LCX", 参考项目 "PVZ Helper 1.8.7" (zhumxiang), 依赖 FLTK+zlib, 鸣谢。

---

## 6. 各功能用到的 data.cpp 符号速查

| 符号 | 用途 |
|---|---|
| `path` | 游戏 exe 路径字符串 (存档文件夹) |
| `lawn`, `board`, `challenge`, `game_selector`, `cut_scene`, `music`, `anim/unnamed/particle_system*` | 基础指针链 |
| `frame_duration` | 速度 |
| `game_mode`, `game_ui`, `scene`, `adventure_level`, `level` | 模式/界面/场景/关卡 |
| `sun`, `money`, `tree_height`, `twiddydinky`(+14/15/26/28 ×4), `playthrough`, `mini_games`, `user_data` | 资源/通关存档 |
| `free_planting`, `slot`, `slot_count`, `slot_seed_cd_past/total`, `slot_seed_type(_im)` | 免费用卡/换卡片 |
| `cursor`, `cursor_grab` | 连续铲子 |
| `endless_rounds` | 无尽轮数/重置场地 |
| `game_paused`, `game_clock` | 直接过关 (炮最亮) |
| `block_type`, `row_type`, `particle_systems_addr` | 重置/切换场地, 智能搭梯, 水路睡莲 |
| `ice_trail_cd` | 冰车无痕 |
| `spawn_list` (1000 int), `spawn_type` (33 bool) | 出怪 |
| `debug_mode` | 调试 |
| `zombie*`, `plant*`, `lawn_mower*`, `grid_item*` | 遍历对象数组 (清除/搭梯/阵型/推车) |
| `tod_mode` | 仅 DEBUG 构建写 true |
| HACK: `block_main_loop` | 注入时阻塞主循环 |
| HACK: `unlock_sun_limit`, `auto_collected`, `not_drop_loot`, `fertilizer_unlimited`, `bug_spray_unlimited`, `chocolate_unlimited`[], `tree_food_unlimited`, `placed_anywhere(_preview/_iz)`, `fast_belt`[], `lock_shovel`, `rake_unlimited`, `init_lawn_mowers`, `lawn_mower_initialize`, `plant_immune_*` ×9, `_plant_immune_*` ×4, `zombie_immune_*` ×11, `_zombie_immune_*` ×4, `reload_instantly`, `grow_up_quickly`[], `no_cooldown`[], `mushrooms_awake`, `stop_spawning`, `stop_zombies`[], `lock_butter`, `no_crater`, `no_ice_trail`[], `zombie_not_explode`[], `hack_street_zombies`, `no_fog`, `see_vase`, `background_running`, `disable_delete_userdata`, `disable_save_userdata`, `unlock_limbo_page` | 对应各复选框 |
| call: `call_sync_profile`, `call_fade_out_level`, `call_wisdom_tree`, `call_put_plant`, `call_put_plant_imitater`, `call_put_plant_iz_style`, `call_put_zombie`, `call_put_zombie_in_row`, `call_put_grave`, `call_put_ladder`, `call_put_rake`(+`_row`/`_col` 补丁点), `call_start/delete/restore_lawn_mower`, `call_delete_plant`, `call_delete_grid_item`, `call_set_plant_sleeping`, `call_puzzle_next_stage_clear`, `call_pick_background`, `call_delete_particle_system`, `call_pick_zombie_waves`, `call_remove_cutscene_zombies`, `call_place_street_zombies`, `call_play_music` | 远程调用目标 |

---

## 7. 版本支持与限制汇总

- 14 个版本全部在 `ver_map` 中; `PVZ_NOT_FOUND/OPEN_ERROR/UNSUPPORTED` 映射到 1.0.0.1051 的表但不会真正写入 (GameOn 为假)。
- **所有正式版本 (12 个) 的地址表均无空项**, 即所有功能理论上都支持; 差异只在调用约定分支 (GOTY / ZH2010+JA / ZH2012 / 原版)。
- **BETA 0.1.1.1014**: 无 `tree_food_unlimited`, 无 `call_wisdom_tree` (树肥/树高禁用), 无通用 `plant_immune_eat`/`zombie_immune_body_damage` (改用代码内硬编码补丁), 结构体大小不同 (zombie 0x160, grid_item 0x8c), 通关存档布局不同。
- **BETA 0.9.9.1029**: 无通用 `plant_immune_eat` (硬编码补丁)。
- **粘度汗化 2010 (ZH)**: 启动时警告版本有严重问题随时崩溃。
- **Steam 1096**: 成就 21 个; 禁止存档时额外 ret 补丁。
- 极限/模拟出怪、出怪统计: 仅生存模式 (1~15); 布置阵型: 仅生存无尽 (11~15) 与我是僵尸 (61~70); 无尽轮数: 仅 11~15/60/70; 屋顶花盆: 仅场景 4/5; 树高生长动画: 仅 GameMode 50; 小推车/直接过关: 仅战斗中 (UI 3); 其余场上操作: UI 2 或 3。
- 已知问题 (README 自述): 修改功能照搬旧修改器 (PVZ Helper), 留有一些 bug 未修复; FLTK 无无障碍支持。
