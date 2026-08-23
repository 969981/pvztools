# pvztools 设计构思（v0 草案，2026-08-22）

> 这是一份"只构思、不动手"的设计文档。依据：5 路调研（现有工具 / 内存结构 / 轨道记法 / 阵型生成 / 打包与社区规范）、3 套独立架构方案互评、完整性审查，以及知乎《pvz轨道理论 1/2》（白菜）与《三大变奏及轨道语言》（Be2SiO4）。
> 凡标 **【待核实】** 的，是调研里找不到一手来源或各方说法不一致的点，写代码前要实测。地址若未注明版本，均指 1.0.0.1051（ImageBase 0x400000），来源为 pvztoolkit `data.cpp`、AvZ `avz_asm.cpp` / `avz_pvz_struct.h`、pvzscripts `core.py` / `extra.py`。

---

## 0. 一句话定位

**pvztools**：MIT 许可、`pip install` 即用的 PvZ1（PC 原版各版本 + 各类改版）Python 自动化框架——
**格式层**（轨道 DSL、阵型码、常量表，纯 Python 跨平台）+ **自适应内存层**（特征码扫描定位基址、类型化只读对象模型、可选修改器）+ **可插拔执行后端**（默认纯 Python 轮询；可选注入 DLL 做帧精确）+ **阵容生成**（模板 + 参数化 + 静态约束）。

它是一个**框架**（执行器掌握主循环，用户填阵型与轨道），底层内存部分可以当**库**单独用。

### 关于包名

`pvztools` 已于 2026-08-22 发布 0.0.1 到 PyPI，名字归你。但要知道两个撞名：lmintlcx 的 C++ `pvztools`（已弃用，被 pvztoolkit 取代）和 `pvz.tools` 网站（植僵工具箱）。处理方式：README 首段明确"与 lmintlcx/pvztools、pvz.tools 无关，亦非其 Python 移植"，并在致谢里提到他们。

---

## 1. 与现有工具的关系（已核实，2026-08）

| 工具 | 现状 | pvztools 的关系 |
|---|---|---|
| **AvZ 2**（vector-wlc，C++，GPL-3.0，2.9.2 / 2026-06，仅 1.0.0.1051，注入 DLL + 主循环钩子，帧精确） | 键控事实标准，无 Python API | 不和它比 TAS 精度。轨道语义（生效时间基准、`ATime(wave, cs)`）与它对齐；提供 AvZ DSL 单向导出；注入后端借鉴其钩子模型，**不移植代码**（GPL） |
| **rpze**（ObeliskGate，Python ≥3.11，win64 wheel，1051 lcx 版，GPL-3.0，2026-08 活跃） | 最接近的"Python 帧精确"项目，偏 IZE | 后期可做 `[rpze]` 桥；我们面向无尽 + 修改器 + 多版本/改版 + py3.9+ |
| **PyPI `pvz`**（lmintlcx/pvzscripts，ctypes 外部轮询，1051 + 1065，2020 停更） | 唯一 Python 包，已死 | 我们的 external 后端是它的现代化替代 |
| **pvztoolkit**（lmintlcx，C++ GUI，GPL-3.0，14 个官方构建偏移表） | 唯一多版本修改器，无 API | 借鉴 TimeDateStamp 识版思路；偏移作为"事实"重整进自有 JSON profile 并注明来源 |
| **pvzclass**（Lazuplis-Mei → pvzclasses 维护分支，C++，MIT，1051） | 对象模型，无 Python | 我们提供 Python 版类型化只读模型 |
| **PvZ-Emulator / pvzemu**（dnartz/testla，pybind，不在 PyPI） | 唯一可用的无尽模拟器 | `[emu]` extras 评估桥 |
| **Seml**（Rottenham） | 唯一社区纯文本轨道格式 | 我们的 DSL 以 Seml 为基底做超集，双向转换 |
| **re-plants-vs-zombies**（Patoke，CC0 逆向重建 GOTY 源码）/ **PvZ-Portable** | 结构体 ground truth | 校对偏移表用 |

**填补的空白**：维护中的纯 Python 多版本库；Python 类型化只读模型（分析 / RL / bot）；宽松许可；阵型码 / 出怪表的库级 API；Steam/GOTY 与**改版**的 Python 支持（目前只有 C++ GUI 覆盖官方版，改版谁都没覆盖）。

---

## 2. 总体架构

```
pvztools/
├── schema/      【格式层 · 纯 Python · 跨平台 · 零依赖】
│   ├── track/       轨道 DSL 解析 → JSON AST → lint → 展开器 → Seml / AvZ 导出
│   ├── lineup/      阵型码 codec（pvz.tools base64 / 旧 lineup string / lineup.yml）
│   ├── timing/      权威常量表（炮 CD 3475、生效偏移、卡 CD、冰时长、波长区间…）
│   └── enums/       PlantType / ZombieType / Scene，对齐游戏 ID 与 pvzemu
├── model/       【对象层 · frozen dataclass】Board / Plant / Zombie / Seed / Wave / Clock，不绑定数据来源
├── _win/        【Win32 原语 · ctypes】OpenProcess / RPM / WPM / VirtualQueryEx / EnumProcessModulesEx /
│                  FindWindowW / PostMessageW / VirtualAllocEx / CreateRemoteThread
├── process/     附着、IsWow64Process 断言、主模块枚举、PE 头解析（stamp / SizeOfImage / 节表）
├── addr/        版本表 profiles/*.json → 缓存 → AOB 扫描 → 校验 → AddressBook(RVA)
├── observe/     内存快照 → model.Board（只读）
├── modifier/    可逆 Patch、写白名单、安全开关
├── control/     执行器 + 后端：external（默认）/ inject（可选 DLL）/ rpze 桥 / avz-export
├── generate/    阵容：模板召回 + 参数化 + 静态约束；可选 pvzemu 评估
├── bridges/     seml / avz / pvzemu
└── cli/         pvztools doctor | read | mod | run | lineup | track | bench-clock
```

**原则**
- 下层不依赖上层；`schema` + `model` 不依赖 Win32，Linux CI 可测。
- 所有写内存集中在 `modifier` / `control`，其他模块只读。
- `model` 只认语义名（`board.sun`），偏移全部来自 profile，代码里不出现魔数。

**extras 矩阵**
- `pvztools`：格式层，`py3-none-any`，任何平台可装。
- `pvztools[memory]`：Windows，仍是纯 ctypes，**不依赖 pymem**（避免其 64→32 注入 API 被误用）。
- `pvztools[inject]`：带 32 位 DLL 的 win32 / win_amd64 wheel。
- `pvztools[emu]`：文档说明如何 `pip install git+…PvZ-Emulator`。

---

## 3. 版本自适应与特征码（你最关心的部分）

### 3.1 为什么对你来说是必需品而不是加分项

官方版只有十几个构建，查表就够。但你的范围包括**改版**（95 版、杂交版、融合版等）——它们大多基于 1051 的 exe 打补丁或重编译，代码段位移、Board 结构增字段（新植物）、甚至 LawnApp 全局换位置都可能发生。硬编码 `0x6A9EC0 → +0x768 → +0x5560` 在改版上是随机炸的。所以：**版本表只是加速缓存，特征码扫描才是主路径**。

### 3.2 四级流水线

```
attach()
 ├ L0  确认是 PvZ：窗口类 "MainWindow"（Steam 版【待核实】）；IsWow64Process == TRUE（32 位进程）；
 │     PE 调试目录 CodeView 路径含 "\Lawn\"（pvztoolkit 做法）；主模块名允许
 │     PlantsVsZombies.exe / popcapgame1.exe（Steam 启动器释放的临时 exe）/ 改版自定义名
 ├ L1  版本表 O(1)：stamp = PE 头 TimeDateStamp → profiles/*.json（14 个官方构建，来源 pvztoolkit data.cpp）
 ├ L2  本地缓存：键 (stamp, SizeOfImage, exe SHA-256)；exe 不可读时退化为 (stamp, SizeOfImage) 并标 weak
 ├ L3  AOB 扫描（未知构建 / 改版）：只扫 .text（PAGE_EXECUTE_READ），VirtualQueryEx 分区一次性读入，
 │     bytes 上 re.finditer（通配符 → `.`，DOTALL）
 └ L4  校验 → 能力分级 FULL / READ_WRITE / READ_ONLY / UNSUPPORTED（单个地址缺失只禁用对应功能，不整体拒绝）
```
不用 PE CheckSum 作键（老构建常为 0）。

### 3.3 扫什么、怎么从指令里抠地址

**核心思路：不在数据段里"搜阳光值"，而是在代码段里搜"访问阳光的那条指令"，把指令里的立即数抠出来。** 这样拿到的是基址和偏移，与当前阳光是多少无关。

签名记录数据化，每条形如：
```json
{"name": "lawn_app", "section": ".text",
 "pattern": "8B 0D ?? ?? ?? ?? 8B 89 ?? ?? ?? ?? 8B 81 ?? ?? ?? ??",
 "extract": [["u32", 2, "lawn"], ["u32", 8, "board_off"], ["u32", 14, "field_off"]],
 "expect_hits": ">=3", "validators": ["lawn_in_data_section", "board_off_in {0x768,0x868}"]}
```
- **LawnApp 全局**：`mov ecx,[imm32]; mov ecx,[ecx+disp32]; mov eax,[ecx+disp32]` 及 `A1 imm32` 变体。多个锚点各自抠出 imm32，**取众数且要求一致**，不一致就降级。
- **Board 内偏移整体平移**：收集访问 Board 的上下文里 0x5000–0x6000 范围的 disp32，与 1051 已知偏移集合做整体平移匹配（官方 GOTY 是 +0x18；改版可能是别的值——这正是要扫的原因）。
- **阳光的独立直达签名**（审查建议）：直接找 `mov eax,[ecx+0x5560]`（GOTY 0x5578）形态的指令，作为与上面链式结果的交叉校验。
- **函数入口**（PutPlant 0x40D120 / RemovePlant 0x4679B0 / PlantCard 0x488590 / ShovelPlant 0x411060 / MouseDown 0x539390 / CobFire 0x466D50 / PickZombieWaves 0x4092E0 / KillZombie 0x5302F0…）：取 1051 入口前 16–32 字节，通配 rel32 / imm32，要求**恰好 1 次命中**。
- **补丁点**：上下文 12–20 字节，命中后读原字节必须等于预期值（如阳光上限 `0x7E`，后台运行 `0x2E74`），否则拒绝打补丁。
- **签名自动派生工具 `sig-derive`**：从 1051 exe + 已知地址自动生成模式，再在 1065 / 1096 / 各改版 exe 上回归。exe 作为本地 fixture，**不入仓库**。
- 立即数统一 `struct.unpack_from('<I')`；结果转 RVA 存 AddressBook（防未来出现重定位构建）。

### 3.4 校验（每一级都要有）
1. 静态：全局落在 .data/.bss；函数落在 .text；偏移 ≤ Board 尺寸 0x57B0（GOTY 增量【待核实】）。
2. 动态（每次 attach，便宜）：`[lawn]` 是堆内 MEM_COMMIT 指针；game_ui(+0x7FC) ∈ {1,2,3,7…}；frame_duration(+0x454) ∈ [5,100]。
3. 关卡内懒校验：sun ∈ [0, 9990]（打了上限补丁后放宽）；clock(+0x5568) 单调递增；plant count ≤ max。
4. 运行期动态确认（CE 思路，对改版特别有用）：读阳光 → 让用户点一个阳光 → 再读，差值 25/50 即确认。
5. 旧式指纹仅作辅助魔数：`[0x4140C5]==0x0019B337` ⇒ 1051，`[0x4140D5]==0x0019B827` ⇒ 1065（pvzscripts）【指令语义待核实】。

失败 → 降级 + `pvztools doctor` 导出诊断包（PE 头、节表、每条签名命中数，**不含游戏二进制**），社区按这个格式回流 profile（定义 JSON schema + PR 模板）。

### 3.5 已知官方版本表（节选，来源 pvztoolkit data.cpp）

| 版本 | stamp | lawn | board 偏移 |
|---|---|---|---|
| 1.0.0.1051 en | 0x49ecf563 | 0x6A9EC0 | 0x768 |
| 1.2.0.1065 en | 0x4a37d6af | 0x6A9EC0（数据段同 1051，代码小幅位移） | 0x768 |
| GOTY 1.2.0.1073 Origin | 0x4c2e3453 | 0x729670 | 0x868 |
| GOTY 1.2.0.1096 Steam | 0x4d02b058 | 0x731C50 | 0x868 |

**纠正一个常见误解**：0x731C50 是 Steam GOTY 1.2.0.1096，不是 1.2.0.1065。GOTY 位移规则（board +0x100；Board 内 ≥0x90 的偏移 +0x18；LawnApp ≥0x7F5 的偏移 +0x120；frame_duration 0x454→0x4B4）在 1.1.0.1056 zh/ja、1.2.0.1093 上**未逐条实测**，这些 profile 标 `verified: false`。

### 3.6 64 位 Python 控制 32 位游戏的坑（必须写进 `_win`）
- 目标内所有指针 4 字节；`ctypes.c_void_p` 在 64 位 Python 下是 8 字节，结构体要显式 `c_uint32`。
- `VirtualAllocEx` 的 lpAddress 留 NULL，分配自然落在 4GB 内。
- 远程线程入口在目标进程内的缓冲区，所以 64 位 Python 可以 `CreateRemoteThread` 32 位 shellcode；但要注入 DLL 时，`LoadLibraryW` 地址必须解析**目标内 32 位 kernel32** 的导出表，不能用自己进程的。
- shellcode 以标准 stdcall `ret 4` 结束。
- 最小权限 `PROCESS_VM_READ | VM_WRITE | VM_OPERATION | CREATE_THREAD | QUERY_INFORMATION`，不要 `PROCESS_ALL_ACCESS`（360 拦得更少）。
- 游戏以管理员启动则 Python 也需管理员；`doctor` 要能诊断 OpenProcess 失败原因。
- 多开时的进程选择策略（按 PID / 窗口标题）。
- ARM64 Win11 的 x86 模拟层：标不支持。

---

## 4. 对象模型

frozen dataclass 快照；一次批量 RPM 读 Board 头 + 植物 / 僵尸数组，降低撕裂（注明"一致性 = 单次 RPM 范围内"）；默认只读。偏移（1051/1065；GOTY 按规则位移；改版由扫描得到）：

- **Game**：version、capability、ui(+0x7FC)、mode(+0x7F8)、frame_duration(+0x454)、board(+0x768)
- **Board**：scene(+0x554C)、sun(+0x5560)、paused(+0x164)、grid(+0x168)、spawn_list(+0x6B4，20×50)、allowed_types(+0x54D4)
- **Clock**：main_counter(+0x5568，战斗 UI 计数、暂停停表)、global_counter(+0x556C)
- **Wave**：current(+0x557C)、total(+0x5564)、countdown(+0x559C)、countdown_start(+0x55A0)、huge_wave_countdown(+0x55A4)、**hp_threshold(+0x5594，直接可读，不用估算)**
- **Plant**（数组 +0xAC，stride 0x14C）：row(+0x1C) col(+0x28) type(+0x24) state(+0x3C；炮 37 就绪 / 38 发射) hp(+0x40) max_hp(+0x44) dead(+0x141) squished(+0x142) asleep(+0x143)
- **Zombie**（+0x90，stride 0x15C）：row(+0x1C) type(+0x24) status(+0x28) x/y/speed(+0x2C/0x30/0x34 float) hp(+0xC8) max(+0xCC) helmet(+0xD0) shield(+0xDC) dead(+0xEC)
- **Seed**（SeedBank +0x144；槽数 +0x24；packet 自 +0x28 起 stride 0x50）：cd_elapsed(+0x24) cd_total(+0x28) type(+0x34) imitater(+0x38) usable(+0x48)
- **GridItem**（+0x11C，stride 0xEC）：墓碑 / 弹坑 / 梯子 / 花瓶
- Projectile(+0xC8, 0x94)、Item(+0xE4, 0xD8)、Mower(+0x100)

注意：植物 / 僵尸数组的槽会被复用，跨快照跟踪对象要用 `(index, serial)` 作身份，不能只用 index。内部符号名（mMainCounter 等）是社区反编译命名——偏移可信、名字未核实，库内用自有英文名并在 docstring 注明偏移。

---

## 5. 轨道 DSL

### 5.1 立场：不发明新记法，做社区记法的超集

四个来源要统一：白菜《轨道理论》、Be2SiO4《三大变奏》、贴吧《轨道读本 v3.1》/《节奏命名体系修正方案》（wiki.pvz1.com）、Seml。

### 5.2 符号表（合并后）

| 符号 | 含义 | 运算量 al | 出处 |
|---|---|---|---|
| `P` / `p` / `P'` | 主炮 / 铲种炮（白菜、Be2SiO4 用 `p`，轨道读本用 `P'`） | 1 | 三方 |
| `PP` | 双炮（通常 2/5 路各一门） | 2 | 读本 |
| `S` `B` `b` `D` `d` | 同步炮 / 提前炮(前场) / 提前炮(后场) / 延迟炮(前场) / 延迟炮(后场, 尾炸) | 1 | 读本 + 白菜 |
| `A` / `A'` / `a` | 樱桃 / 辣椒 / 窝瓜（**Be2SiO4 方言：`a` = 辣椒/窝瓜/土豆雷**） | 1 | 白菜 / 读本 |
| `N` / `N'` | 核 / 复制核 | 2 | 三方 |
| `I` / `I'` / `I3` | 冰 / 复制冰 / 冰三（ICE3 用法） | 0 | 三方 |
| `C` / `c` | 垫材（读本：`c` 普通垫材，`C` 地刺/高坚果等高级垫材） | 0 | 三方 |
| `IO` | 自然输出 | 1 | 白菜 |

**逻辑符**：`|` 分波；`-` 分"生效于触发刷新前 / 后"（触发刷新的炮归后面）；`/` 分上下半场（对称可省）；`~` 波动；波长写法两种：尾随 `869|869|869|869`（Be2SiO4）或括号 `(6, 21, 9)`（白菜 / 读本）。**单位推断**：≤100 视为秒（`6` = 600cs），否则 cs；评论区 `P5u: P|P|P|P|P 6|6|6|9|9` 就是这种写法。

**约束常量**（`schema/timing`）：`2 al = 1 re`（每波两个运算量触发刷新，白菜）；炮 CD 3475cs、飞行 373cs（陆）/ 378（水路）/ 387（屋顶，AvZ；pvzscripts 按列 359–373【两套数值适用条件待核实】）；冰/核生效偏移 夜 100 / 昼 299，复制卡 +320，樱辣瓜 −100（AvZ DSL）；波长区间 [601, 2500]（Be2SiO4）/ 非旗帜波自然上限 3099（模拟器 `rand(600)+2500`）；w9/w19 倒计时 4500 + 红字 750；w20 5500；卡 CD 5000 / 3000 / 750（AvZ 注释 751【1 帧差待核实】）。

**节奏名**（《节奏命名体系修正方案》）作为元数据与校验预期：P 系 `P3/P4/P5/P5u/P6`；C 系（无炮）`C2/C3/C4`；S 系 `S3/S5/S6`；白昼双冰 `ch4/ch5/ch6/C6i`；夜间双冰 `C4u/C5u/邻C6u/对C6u/邻C7u`；I 系 `I4i/I6i`。

### 5.3 时间基准：效果轴 vs 操作轴

白菜第二篇区分**效果轴 EL（生效时刻）**和**操作轴 OL（发动时刻）**；Be2SiO4 的 `-` 也是按生效时刻划分。DSL **写效果轴**（与 AvZ DSL 一致），执行器编译成操作轴：`P 341` → 发炮于 341 − 373 = −32cs（即上一波）。JSON AST 里 `timing_base: "effect"` 时 `at_cs` 必须是生效时刻，不许混。

### 5.4 三种书写层级

**① 速记**（给人看，`rhythm` 一行）：
```yaml
scene: PE
rhythm: "P6  PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)"
```
**② 逐波**（Seml 兼容，可执行）：
```yaml
scene: PE
protect: [18, 28, 58, 68]        # 保护位，炮/冰瓜
w1~8 len 601:
  - PP 225 @ 25 9                # 生效 225cs，落 2-5 路 9 列
  - C  539~619 @ 1256 9          # 垫材，539–619 之间放，1/2/5/6 路 9 列
w9,19 len 4500:
  - PP 225 @ 25 9
  - PP 2000 @ 25 9
w20 len 5500:
  - PP 341 @ 25 9
  - DD +107 @ 15 8.1875          # 相对上一条 +107cs；落点 1/80 格
  - PP 2000 @ 25 9
```
**③ 完整**（AvZ 教程 [FE] 经典四炮，邻C6u，含选卡与出怪）：
```yaml
scene: FE
lineup_code: "LI43NMQIfe3BVVEc+sHIXUAPX7dvSCBUt1TS1OE8dn9XgraGkdQ1ZlSKiEUaVw=="
cards: "IINAW LPccc"             # AvZ 单字母选卡记法
zombies: "普杆舞车豚 丑气矿篮偷 红"
rhythm: "邻C6u  I-PP|I-PP|N|PP (1976|1976|750|749)"
w1%4, 2~9%2, 11~19:
  - I  11   @ 3 5
  - PP 1776 @ 25 8.75
  - len 1976
w10:
  - PP 341
  - A 401 @ 2 9 & W 401 @ 5 9
w20:
  - PP 341
  - I 395 @ 3 5
  - PP 2000
```

### 5.5 展开器（审查指出的缺口，必须设计）

速记 `P6: PP|PP|PPSS…` 到"每波用哪门炮、打哪格、哪张冰"之间有一个**展开器**：
- 炮位分配：AvZ `ACobManager` 风格就绪队列——按 `state==37` 和上次发射时间排序，取最早可用的；屋顶按列区分风炮 / 平炮。
- 冰卡轮换：存冰位列表 + 复制冰；昼间自动带咖啡豆。
- 垫材行选择：按出怪表（`+0x6B4`）里本波是否有巨人/舞王决定垫哪一路。
- 起手 / 收尾特判：w1 存冰、w9/w19 晚冰或不冰、w10/w20 出生点偏右的延迟——这些在 Be2SiO4 的脚本里是手写的 `W1/W9/W10/W19` 子程序，在我们这里是展开器规则 + DSL 可覆盖字段。
- 简写歧义消解：`PIPIPN` 这种省掉 `|` 和 `-` 的写法，用"每波恰好 2 al"约束回推波边界（白菜复写规则），无解时报错而不猜。
- 方言：`dialect: baicai | be2sio4 | dubben` 切换 `a`/`A'` 等冲突符号的解释。

### 5.6 校验器（lint，可机器化）
同门炮生效间隔 ≥ 3475；卡 CD；波长区间；白天冰/核需咖啡豆；水路需荷叶、屋顶需花盆且无核；落点 1/80 格；每波 al 总和 ≥ 2（非冰波）。输出结构化诊断。另提供 `to_seml / from_seml / to_avz_dsl`。

**明确不做**：从截图 / Excel 识别轨道（不做 OCR），只认文本。

---

## 6. 执行器：两档后端

| 档 | 机制 | 精度 | 阶段 |
|---|---|---|---|
| **external（默认，纯 Python）** | 唯一时间源 = 游戏帧钟 +0x5568 + wave/countdown（**不用墙钟 `Delay`**）。sleep 到截止前 1–2ms 后 `perf_counter` 忙等轮询（Py3.11+ 高分辨率定时器；<3.11 用 `timeBeginPeriod(1)`）。动作经 `PostMessage` 点击（落在下一消息泵）或远程线程 shellcode 直接调 PutPlant/CobFire（前后写 block_main_loop 再恢复，同 pvzscripts `asm_code_inject_safely`）。HIGH_PRIORITY_CLASS 不需要管理员 | 非确定，估计 ±1–2 tick，负载时更差。**无实测基准【待核实】**，库内置 `bench-clock` 实测，执行报告记录 planned / observed / delta | MVP |
| **inject（可选 DLL）** | 32 位 C DLL，**不含任何绝对地址**：vtable 槽 / 函数表 / 调用约定 thunk 全部由 Python 经共享内存传入（所以改版也能用，只要扫描拿到了地址）。每帧排空按 `(wave, cs)` 计时的命令堆；DLL 自己按 countdown 解析刷新点；整个计划在 w1,−599 前灌入；运行时决策用内置小策略 + 带超时保底的询问事件 | 帧级确定（与 AvZ 同级）。GOTY 调用约定未验证【待核实】 | 后期 |

取舍：external 定位观察 / 修改 / 宽容节奏（P 系）的自动化；轨道含 1cs 敏感操作且后端为 external 时**主动告警**，建议切 inject / rpze / avz-export。

**审查补充的坑**：
- 游戏 `UpdateFrames` 追帧逻辑：掉帧时一个渲染帧对应多个逻辑 tick，外部读到的 clock 可能跳 2–5——所以用"到达 t−N 提前触发"而不是"等相等"。
- 暂停（+0x164）时 +0x5568 停表：执行器要识别并挂起，不能当超时。
- 顶层对话框（"保存进度？"等）会吞 PostMessage：检测并处理。
- 窗口失焦自动暂停 → 必须先打"后台运行"补丁；这与"纯净键控"冲突，文档要明说键控模式也允许这一个补丁。
- 运行时容错：炮被啃 / 冰卡未就绪 / 巨人砸炮 → 策略枚举 `skip | substitute | warn | abort`，默认 substitute + warn。

### 6.1 端到端状态机（审查指出"运行起来"链条断裂）

```
主菜单 → 用户选择对话框 → 模式选择 → Survival → Endless
 → 选卡界面 (ui==2)：ChooseCard 0x486030（要求 ui==2）
 → Let's Rock → 战斗 (ui==3)：一键布阵 PutPlant（子层顺序：荷叶/花盆 → 本体 → 南瓜）
 → 灌入轨道 → 跑 20 波
 → 两面旗结束 → 重新选卡 → 读阵型损伤 → 补种 → 循环
```
每个状态有进入条件、超时、失败回退。`require_in_level` 守卫所有远程调用。

---

## 6.2 原则：从机制推导，而不是死记社区数值

社区轨道（"八炮 9 秒一对炮"）是**人类估算的近似值**：Be2SiO4 自己在文中就是这么算的——3475 ÷ 4 = 868.75 → 取 869。我们从逆向拿到的是精确机制，所以：

**(a) 节奏合成器（`generate/rhythm.py`）**：输入炮数 / 冰卡数 / 场地 / 约束，输出精确节奏，不查表。
- P 系：`pairs = cobs // 2`，`len = max(601, ceil(3475 / pairs))`。8 炮 → 869（P4）；12 炮 → 579 < 601 → 取 601（P6，这就是"P6 是理论最快"的原因）；16 炮 → 434 → 多出的炮变成同步炮 `PPSS`。
- 冰波：`冰波长 ≤ 2000`（减速持续），激活炮落点与生效时刻由 `timing.effect_offset` 反推，不再手算 "1538 − 373 = 1165"。
- 收尾 / 起手：w9、w19 用 `4500 + 750`，w20 用 `5500`，w10/w20 出生点偏右——全是常量表里的事实。
- 存冰预算：按 sqrt_7 时间计算器的输入输出定义（循环波长、用冰时机、收尾冰、存冰方式 → 剩余冰 / 产冰 / 耗冰 / 开局冰数）。

**(b) 反应式执行（`control/reactive.py`）**：有了内存，很多"计算"可以直接变成"读取"：
- 炮是否就绪：读 `Plant.state == 37`，不用数 3475。
- 该不该激活：读 `Board.hp_threshold(+0x5594)` 与本波血量（+0x5598），低于阈值就是可刷，不用"预判 341"。
- 下一波什么时候来：读 `countdown(+0x559C)` / `huge_wave_countdown(+0x55A4)`。
- 炮弹在飞：读 Projectile 数组。
所以执行器有两档：**计划式**（照 DSL 时间表走，用于复现/验证社区轨道）和**反应式**（读状态决定动作，用于挂机与改版适配——改版的数值变了也不用改轨道）。

**(c) 瞬发**：人要"点炮 → 点落点"两次点击、一次一门；我们直接调游戏的发炮函数（`CobFire`），同一帧发任意多门、任意落点，`PP`/`PPSS`/`PPDD` 全部零偏差。这也是为什么 AvZ 社区把键控成绩和手操成绩分开记——我们的产出物同样必须标注"键控"。

**(d) 边界**：能读到的是状态，读不到的是随机——冻结时长 400–600、刷新阈值 0.5–0.65、出怪表都带 RNG。反应式执行覆盖前两者（直接读结果而非预测），出怪表在关卡开始时已经生成（`+0x6B4`），开局读一次即可按波分支。


### 6.3 后端精度分级与注入开关（2026-08-22 决定）

| 后端 | 机制 | 精度 | 默认 |
|---|---|---|---|
| `external`（纯外部） | 轮询游戏时钟 + **定帧命中**（目标 tick 前半帧打 `main_loop_block`，读计数器确认停在哪一 tick，命中才执行，早了放开重来）+ 用 `mShootingCounter` 回读真实发射 tick | 0–1cs，偏差可知 | **开** |
| `inject`（注入 DLL 钩子，M6） | 主循环钩子按 (wave, cs) 队列原地执行 | 帧精确，零偏差 | **关**，需用户显式打开：`pvz.attach(inject=True)` / CLI `--inject` / `pvztools config inject on` |

真机数据（Steam GOTY，3C八炮，2026-08-22）：纯外部、未做定帧命中时，炮就绪情况下偏差 +1~+2cs（18 次发炮）；远程调用耗时约 24ms（其中含固定等待 2 帧，可优化）。
输入后端自适应要求：按 `GetClientRect` 缩放、鼠标自标定（发 `WM_MOUSEMOVE` 读回 `Board.mPrevMouseX/Y`）、操作前自动拉前台、失焦自动打 `background_running`、选卡界面助手（清空/选卡/开局按源码坐标公式；开局按钮要求选满）。

## 7. 阵容生成

### 7.0 "自动解题"的形态：合成 → 仿真 → 筛选

游戏逻辑是确定的（血量、速度、判定、炮范围、刷新条件），已由 PvZ-Emulator / re-plants-vs-zombies 重建，不必自己重新逆向；但出怪表、刷新阈值 0.5–0.65、冻结 400–600、巨人变速与出生点、投篮/小丑/矿工倒计时都带 RNG，**解法只能是统计意义上的**（社区本来就用"意外率"评价轨道）。因此：

1. **合成**：给定阵容，节奏合成器（§6.2a）按炮 CD / 波长区间 / 冰预算枚举候选节奏——纯算术，候选集很小。
2. **仿真**：每条候选进 `[emu]` 跑 N 千轮，统计砸率 / 炸率 / 漏怪率 / 阳光收支（Seml `repeat:10000` 的做法）。
3. **筛选输出**：最优者写成 `.pvzt` + 阵型码 + 统计指标（"10000 轮无伤率 99.6%"）。
4. **运行时**：剩余随机由反应式执行（§6.2b）当场吸收。

边界：**给定阵容找轨道**可做；**从零发明强阵**是 48^54 的搜索，只承诺"名阵变体生成 + 打分"，自动成阵列为长期研究。前置条件：模拟器保真度对照实验（同一轨道真机 vs 模拟器各 100 轮比分布，待核实第 9 条）。

社区无自动成阵先例；解空间 ~48^54，评估成本高（一面旗 2–4k tick × 数百面，且每阵需配节奏脚本）。所以：**模板 + 参数化 + 静态约束为主，搜索为辅**。

1. **模板库**：pvz.tools 无尽名阵选集 339 阵（`lineup.yml` / `lineup_string.json`，2020-12-04；DE45 / NE49 / PE143 / FE54 / RE28 / ME20）。包内只带 codec 与少量自制模板，`pvztools lineup fetch` 从官方 URL 下载到用户缓存【再分发许可待核实】。
2. **请求**：scene、炮数、节奏（反推炮数下界与冰卡数：P6 ⇒ 12 炮）、禁用集、经济、**本轮出怪集（从 +0x6B4 读）**。
3. **参数化**：镜像、冬瓜/曾哥替换、向日葵数、垫材列、荷叶/花盆/南瓜子层。
4. **静态约束**：前场空地列、水路 8–9 列留空、屋顶几何、阳光储备 ≥1500/3000、红眼分离炸规则。
5. **轨道配对**：模板若无配套轨道，按节奏名由展开器自动产出骨架轨道——否则"按操作方法运行"闭不上环。
6. **可选评估**：`[emu]` pvzemu 蒙特卡洛，结果标"模拟估计"【出怪 RNG 保真度待核实】。
7. **输出**：布阵码 + 配套 `.pvzt` + 报告。

---

## 8. 修改器功能清单 + 安全开关

功能清单以 docs/parity-pvztoolkit.md 的 v2 为准（v0 表保留如下供对照）

| 功能 | 机制（1051 / 1065 / Steam 1096） | 级别 |
|---|---|---|
| 阳光设定 / 锁定 | 写 Board+0x5560 | 低 |
| 阳光上限解锁 | 补丁 0x430A23 / 0x430A83 / 0x41F4E5（0x7E→0xEB） | 低 |
| 自动收集 | 补丁 0x43158F / 0x4315EF / 0x4352F2 | 低 |
| 后台运行 | 补丁 0x54EBA8 / 0x54EDD8 / 0x5D87C9（0x2E74→0x00EB） | 低 |
| 游戏速度 | 写 LawnApp+0x454（GOTY +0x4B4） | 低 |
| 卡槽无冷却 | 写 Seed.cd_elapsed = cd_total | 低 |
| 自由种植 | LawnApp+0x814 | 低 |
| 禁止刷新 / 冻结时间 | 写 countdown / frame_duration | 低 |
| 植物无敌 / HP | 写 Plant.hp | 低 |
| 出怪控制 | 写 +0x54D4 + 远程调用 0x425840 / 0x4092E0 | 中 |
| 放置 / 删除植物、放僵尸、一键布阵 | PutPlant 0x40D120 / RemovePlant 0x4679B0 / PutZombie 0x42A0F0 | 中 |
| 一键清僵 | KillZombie 0x5302F0 | 中 |
| 金币 / 关卡 / 智慧树 | PlayerInfo +0x82C（改内存后游戏自己 SaveData 才落盘） | 高（动存档，先备份） |

**安全设计**：默认只读；`allow_write / allow_patch / allow_remote_call` 三重开关；`dry_run` 默认开；写地址白名单（仅 profile 声明的偏移）；范围守卫；`require_in_level`；补丁前校验原字节、可逆 Patch 上下文管理器 + `atexit` 恢复；存档操作先备份；`purity="strict"` 纯净键控模式拒绝任何写内存 / 补丁（后台运行补丁除外）；执行报告与导出元数据标注 `mode: 键控 | 修改器 | 混合`。

---

## 9. 打包、法律、社区规范

- 基础包 `py3-none-any`，`Requires-Python >= 3.9`，零硬依赖。
- `[inject]`：DLL 恒 32 位；因 wheel 标签按解释器位数选择，必须发 `py3-none-win32` 和 `py3-none-win_amd64` **两个同内容 wheel**；DLL 放 package data，`importlib.resources` 定位；公开 CI 构建 + SHA-256 + VirusTotal 结果。
- 杀软：WPM + CreateRemoteThread + LoadLibrary 会触发 Defender `PUA:Win32/GameHack`，360 / 腾讯更严。不混淆、发源码、README 白名单指引、最小权限 OpenProcess。
- 测试：无游戏的 CI 用内存 dump fixture（不入库）+ mock RPM；格式层全量在 Linux 跑。
- **许可证 MIT**：不逐字移植 AvZ / pvz / pvztoolkit（GPL-3.0）代码与具名偏移头；偏移作为事实以自有 JSON profile 重整并注明来源；CONTRIBUTING 写明"合并任何 GPL 代码则整包转 GPL"。
- 不分发游戏二进制 / 素材 / 下载链接（包括改版）。
- EA 用户协议禁改 / 逆向、无单机豁免；措辞"无已知执法、非许可"。"Steam 版无 VAC"是社区经验，非官方说明。
- 社区规范：**键控 ≠ 修改器**，产出物必须标注；speedrun.com 禁外部工具——文档明示不得用于速通提交。
- 致谢：vector-wlc、lmintlcx、Lazuplis-Mei、dnartz/testla、Rottenham、白菜、Be2SiO4、轨道读本 / 节奏命名体系作者。

---

## 10. 路线图

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M0 占名** ✅ 2026-08-22 | PyPI `pvztools` 0.0.1，GitHub 仓库 | `pip install pvztools` 可用 |
| **M1 格式层（4–5 周）** | enums / timing；轨道 DSL 解析 + JSON AST + lint + 展开器 + Seml 双向 + AvZ 导出；阵型码四格式互转；Linux CI | 解析本文所有样本 + ≥30 条 wiki / 贴吧轨道；编解码往返一致 |
| **M2 只读内核（3–4 周）** | process / PE / 版本表 / 缓存 / observe；`pvztools read / doctor`；1051、1065 验证，Steam 1096 只读 | 三构建快照正确；未知构建输出诊断包 |
| **M3 自适应 + 修改器（3–4 周）** | `sig-derive`、AOB、四级降级；低 / 中级修改器 + 安全开关 + 一键布阵 | **删掉版本表后**三版本仍能解析 lawn / board / sun / 函数入口；**至少一个改版**跑通只读；补丁可逆 |
| **M4 external 执行器（3–4 周）** | 游戏帧钟计时、预判、状态机、容错、执行报告、`bench-clock` | PE 经典十二炮 P6 跑满 20 波；公布实测 delta 分布 |
| **M5 阵容生成（3 周）** | 模板召回 / 参数化 / 约束 / 轨道配对；`[emu]` 桥 | 生成变体通过静态校验并能在 M4 上跑 |
| **M6 inject 后端（可选，6–8 周，风险最高）** | 版本无关 DLL、64→32 注入、共享内存 (wave, cs) 队列 | 炮生效 tick 与计划零偏差 |

---

## 11. 主要风险

- external 精度不足，被社区拿 AvZ 标尺否定 → 诚实文档 + 桥接，不吹帧精确。
- 杀软隔离 DLL → `[inject]` 做成可选，核心功能不依赖它。
- GPL 传染 → 只借思路不抄码，CONTRIBUTING 写死。
- 改版碎片化（每个改版一套偏移）→ 这正是特征码 + 诊断包 + 社区回流 profile 要解决的。
- 单人维护 → 格式层先行，它不依赖游戏版本、最稳定、最容易有人贡献。
- DSL 碎片化 → Seml 超集，不发明第五种记法。

## 12. 待核实清单（写代码前实测）

1. 各构建 PE `DllCharacteristics` ASLR 位；改版 exe 是否有加壳。
2. Steam `popcapgame1.exe` 是否 SteamStub 包裹；运行期磁盘 exe 能否读取做 SHA-256。
3. 指纹常量 0x4140C5 / 0x4140D5 的指令语义。
4. 1065 上 MouseDown / PlantCard / ShovelPlant / CobFire 地址；GOTY 全部函数调用约定。
5. GOTY 位移规则在 1.1.0.1056 zh/ja、1.2.0.1093 的适用性；Board 尺寸 GOTY 增量。
6. 外部 Python 命中 10ms tick 的抖动分布、AOB 全量扫描耗时——无基准，需 `bench-clock` 自测。
7. 屋顶炮飞行 387（AvZ）vs 按列 359–373（pvzscripts）的适用条件。
8. 垫材 CD 750 vs 751；`plant.index == 数组下标` 是否恒成立。
9. pvzemu 许可证（BSD-2 vs NOASSERTION）、出怪 RNG 保真度；pvz.tools 339 名阵再分发许可；wiki 批量导出可行性。
10. `py3-none-win32.win_amd64` 多标签 wheel 是否被 PyPI 接受；纯 ctypes 包在 Defender / 360 下的误报情况。
11. 窗口类 "MainWindow" 在 Steam 版是否一致；PDB 路径含 `\Lawn\` 在全部构建是否成立。
12. 主流改版（95 版、杂交版、融合版…）各自基于哪个官方构建、是否重编译——决定签名库的覆盖策略。

### 已由源码分析解决（2026-08-22，详见 `docs/research/source-map.md`）

- 第 4 条（部分）：GOTY `CobFire` = `Plant::CobCannonFire` @ 0x46D1F0（retail 0x466D50），约定 EAX=Plant*、`push y; push x`、`ret 8`；release 不校验就绪态，调用者须检查 `state(+0x3C)==37`。retail 1051 的 `MouseDown/PlantCard/ShovelPlant` 已对应到 `Board::MouseDownWithTool` / `SeedPacket::MouseDown` 等源码函数；1065 地址与 `mouse_down` 0x539390 仍未解决。
- 第 5 条（部分）：Board 尺寸 GOTY 增量 = Widget 基类 +0x18（0x88→0xA0）+ 6 个成就统计字段（+0x10）；sizeof 1051 0x57B0 / GOTY 0x57D8。1051→GOTY 规则：Board 字段到 `mPrevBoardResult` 为 +0x18，之后 +0x28；Zombie stride 0x15C→0x168（插入 Zombatar 字段）；LawnApp 基 0x768→0x868 且 `mGameSelector` 之后再 +0x20。对 1056/1093 的适用性仍需实测。
- 第 7 条（部分）：玉米炮出膛 205 cs、落点行 = `PixelToGridYKeepOnBoard(x-47,y)`、爆心 = x−7 px、半径 115 已由源码确认；飞行时长（387 vs 359–373）是弹体侧，仍待核实。
- 第 8 条（后半）：`plant.index == 数组下标` 成立——`DataArray` 以 `mBlock + index*stride` 寻址，ID 低 16 位即下标；遍历上界用 `mMaxUsedCount`(+4)。
- 新确认：出怪列表为哨兵终止（`ZOMBIE_INVALID = -1`），非整行填充；`pick_background` 实为 `Board::LoadBackgroundImages`；`items` stride 0xD8 来自 Coin 8 字节对齐；PlayerInfo 1096 成就数 21。

---

## 附：本文依据的三篇轨道文章要点

- 白菜《pvz轨道理论 1. 基本概念》(zhuanlan.zhihu.com/p/379738079)：符号 I N A A' a C P p S B D d；逻辑符 `| - / ()`；运算量 P=A=1, N=2, I=C=0, IO=1；`2 al = 1 re`；习题 `ICP-BPD/Pd`、`|PP|I-PP|N (6, 21, 9)`。
- 白菜《2. 轨道展开式与轨道简式》(p/379924798)：效果轨 / 操作轨分类；效果轴 EL vs 操作轴 OL；复写 `PP|I-PP|I-PP|N` → 简写 `P|I-P|I-P|N` / `PIPIPN`。
- Be2SiO4《三大变奏及轨道语言》(p/45182429)：`P4: PC|PC|PC|PC 869|869|869|869`、`ch4: IP-P|IP-P 1738|1738`、`ch6: P|I-P|P|I-P 601|1150|601|1150`、`ch5: P|I-P|I-P 601|1500|1500`；炮 CD 3475、飞行 373、波长 [601, 2500]；按键精灵脚本（`CountDown` 轮询 `&H6a9ec0 → +0x768 → +0x559c`、`Delay` 硬等待、`W1/W9/W10/W19` 特判）即本框架要替代的对象。

---

## 实现状态（2026-08-22）

已实现（全部 stdlib + ctypes，测试 471 passed / 1 skipped）：`_win`（kernel32 + PE 解析，含 Steam 启动器内嵌 PE 剥离）、`process`（attach / 多开筛选 / WOW64 校验）、`addr`（14 个官方 profile、特征码扫描 L1→L4 流水线、doctor / 诊断导出）、`observe` + `model`（只读快照：Board / 植物 / 僵尸 / 卡槽 / 出怪表）、`modifier`（Safety 白名单 + 范围守卫 + 日志；写阳光 / 速度 / 冷却 / 自由种植 / 作弊标志 / 清僵；可逆代码补丁 + atexit 恢复）、`game`（§user-scenarios §2 初学者门面 `pvz.attach()`）、CLI `doctor / scan / read / spawn / mod`。
在 Steam GOTY 1.2.0.1096 实机验证（读全部；写仅阳光并回滚）。
未实现：远程调用（放植物 / 发炮 / 铲除 / 选卡等 §8 中级功能，`allow_remote_call` 仅为开关）、PlayerInfo 存档级写入与备份、锁定阳光循环、运行器 / 模拟器 / RL 接口、非 1096 构建的实机验证。
已知偏差：`board.seed_bank` 是指向 SeedBank 对象的指针（非内联，实机确认）；profile 1096 `userdata_readonly` 第 3 站点 on/off 疑似颠倒（未改动数据）。

**执行器（§6 external，2026-08-22 追加）**：已实现 `control/executor/`——`clock.GameClock`（帧钟采样、刷新点推断、`countdown` / `huge_wave_countdown` 刷新估计、暂停识别、sleep+忙等混合等待，`timeBeginPeriod(1)`）、`scheduler.PlannedExecutor` + `report.ExecutionReport`（计划式：按 `(wave, operation_cs)` 分组，同 tick 多炮一次 `fire_cobs`，迟到策略 warn/skip/abort，报告 planned / observed / delta / jitter / pauses）、`policies` + `reactive.ReactiveRunner`（反应式：P 系激活、冰卡补种、日间咖啡豆）、`bench.bench_clock`（`pvztools bench-clock`）；两档动作后端 `control/backends.RemoteBackend`（修改器语义）与 `control/input_backend.InputBackend`（键控：PostMessage 点击，`Safety.allow_input`）；门面 `pvz.attach(input=True)`、`Game.run_track / run_reactive / bench_clock / backend / expand_context`；CLI `track run`、`reactive`、`bench-clock`。
未实现（§6 审查坑）：顶层对话框吞消息的检测、运行时容错（炮被啃 / 卡未就绪 → substitute）、HP 触发提前刷新的预测（只能靠轮询下界）、inject DLL 后端、卡槽 / 铲子像素常量的实机核对（TODO_VERIFY）。

**执行器 v2 / 定帧命中与输入自适应（§6.3 external 档，2026-08-22 追加）**：`control/remote.RemoteExecutor.run(at_tick=)` / `run_locked` 定帧命中（打 `main_loop_block` 后轮询 `main_counter` 确认冻结 tick，早了放开、混合等待、重打；`RemoteResult.frozen_tick / late_by / attempts`）；`Actions.fire_cob(s)(at_tick=)` 回读 `mShootingCounter` 得真实发射 tick（`FireResult.fired_tick / frozen_tick / attempts`）；`RemoteBackend.fire_cob(s)(at_tick=)` + `cobs_ready()`；`scheduler.PlannedExecutor` 对 remote 炮组传 `at_tick = 刷新点 + operation_cs`，炮在计划 tick 未就绪时按反应式"就绪即发"回退（每 tick 复查，上限 `cd_wait_max_cs`=3600），行状态 `cd-wait`、`cd_wait_cs` 单独统计，不计入时序误差；`report.ExecutionReport` 新列 `frozen_cs / fired_cs / attempts / cd_wait_cs`，摘要 `abs_error`（|delta| mean/p95/max）、`cd_waits`、`frame_locked`。输入侧：`control/window.GameWindow`（`GetClientRect` × `GetDpiForWindow` 缩放、`WM_MOUSEMOVE` 读回 `Board.prev_mouse_x/y` 最小二乘标定、`AttachThreadInput` 拉前台、失焦自动 `background_running`、对话框启发式关闭）、`control/chooser.SeedChooser`（源码坐标公式清空 / 选卡 / Let's Rock，选满才开局）、`InputBackend` 经 `GameWindow` 投递；profile 新增 `board.prev_mouse_x/y`（GOTY 0x5570/0x5574，1051 0x5558/0x555C）。门面：`pvz.attach(input=True, foreground=True)`、`Game.window / chooser / set_seed_slots / play_level(lineup, track, seeds, backend, cob_col, slot_types)`；CLI `play`、`chooser clear|pick|start|select`、`window info|front|calibrate`、`track run --cob-col`。数据：`pvztools.data.lineups.json` 随包分发（`lineups_path()` / `load_lineups()`），`schema.lineup ↔ track` 循环导入已解。测试 844 passed / 4 skipped。
实机（Steam GOTY 1096，3C八炮，P4 col 8，未定帧）：18 次发炮偏差 +1~+2 cs，远程调用约 24 ms；定帧版本的整关实测待做（本次集成时游戏停在主菜单，未能跑 `pvztools play`）。inject 档仍未实现（默认关）。

**打关编排 / 自动收集 / 内存选卡（§6.3，2026-08-22 追加）**：`control/play.PlaySession(game, lineup, track, seeds, cob_col, backend, auto_collect, foreground, chooser)` 把整关串起来：窗口（`foreground=True` 只为观看，拉前台失败则 `ensure_running` 自动打 `background_running`）→ `ui 5` `MainMenu.award_continue` / `ui 1` `MainMenu.start_adventure`（`control/menu`，远程调用 `GameSelector::ClickedAdventure` / `AwardScreen::StartButtonPressed`）→ `ui 2` `MemoryChooser.select_and_start`（`control/chooser_mem`，远程调用 `ClickedSeedInChooser/Bank` + `OnStartButton`，先补齐 Let's Rock 会弹窗的卡；非 GOTY 1096 或失败时回退到点击选卡 `game.chooser`）→ `ui 3` `place_lineup` + 可选 `set_seed_slots` → `PlannedExecutor`（remote 定帧发炮，CD 未到"就绪即发"单独计 `cd-wait`）+ `AutoCollector`（`control/autocollect`，每次帧钟轮询按 `Coin::MouseDown` 守卫批量调 `Coin::Collect`）→ 关卡结束 `ui 5` 自动 `award_continue`。每阶段记录在 `report.meta["steps"]`；`ExecutionReport.meta`（版本 / profile / 阵型 / 轨道 / 场景 / 波数 / 关卡）、`ActionRow.call_ms`（后端调用耗时）、`summary()["call_ms"]` 新增；`ExecutionReport.to_markdown()` 输出中文标题 + 英文标签的报告（概要 / 时序表：命中·晚·CD等待·跳过 / 统计：|delta| mean·p95·max、CD 等待数、远程调用 ms、刷新点、暂停 / 自动收集：阳光·金币·其他 / 备注）。门面 `Game.play_level(..., auto_collect, foreground, chooser, finish_level)` 与 `Game.run_track(collector=)`；CLI `pvztools play --lineup --track --seeds --cob-col --remote --yes [--no-collect] [--no-foreground] [--chooser auto|memory|input] [--report out.json] [--md out.md]`（主菜单 / 选卡 / 关卡 / 奖励界面均可启动；`--dry-run` 仍需在关卡中）。测试 1000 passed / 2 skipped。

### 打连关（campaign）：定周期发炮的默认关卡运行器（2026-08-22 追加）

把实测验证过的"严格 CD 定周期发炮"脚本（原 `strict_front.py`）正式收进代码库，作为**默认关卡运行器**，并在其上做连关编排。

- **`control/play.play_one_level(game, *, lineup=None, cycle=869, target_col=8, collect=True, foreground=True, on_event=None) -> LevelReport`**：单关的严格发炮环——① 安全拉前台（`GameWindow.bring_to_foreground`，`SW_RESTORE`+`SetForegroundWindow`，只为观看、不强制 `background_running`，全屏安全）；② 等 `ui==3`；③ 场上炮 < 8 时按场景默认八炮布阵（`lineup` 覆盖 `lineups_default.default_lineup(scene)`）；④ 从**实时场地**按列后按行两两配对玉米炮，每对在 `T0 + k·cycle`（游戏时钟 cs）定帧发射（`fire_cobs(at_tick=)`，读回真实发射 tick），**忽略僵尸/波次**；⑤ 每次轮询 `AutoCollector.tick`；⑥ `ui != 3` 即返回 `LevelReport`（场景 / 阵型 / 关卡号 / 波数 / 发炮组数 / 平均 |Δ| / 收集 / 结果）。`PlaySession` 也暴露此档：`firing="strict"`（+`cob_cycle` / `ignore_zombies`），与计划式调度器互斥。菜单导航与奖励界面不归它管，交给连关编排。
- **`control/campaign.Campaign(game, *, count=1, auto=False, lineup=None, cycle=869, on_event=None).run() -> CampaignReport`**：连关状态机，逐帧读 `game.ui`——`ui 5` `award_continue`、`ui 1` `start_adventure`、`ui 2` 跳戴夫对话 + 内存选卡开局、`ui 3` 跑 `play_one_level`（关后收集通关奖励币 + 继续过奖励界面，等待下一界面）。每关前 `levels.classify(game)`：**不可布阵 / 非冒险关（传送带 / 小游戏）就记录跳过原因并停止**，不强行处理。`--lineup` 覆盖每关的场景默认阵型。产出 `CampaignReport`（聚合已打关数 / 总发炮组 / 总收集 / 平均 |Δ|，`to_markdown()` / `to_json()`；跳过记录）。`finally` 中总是释放遗留补丁，单控制器。
- 依赖并发编写的 `control/levels.py`（`classify` / `is_adventure` / `adventure_level`）与 `control/lineups_default.py`（`default_lineup` / `load_default` / `cob_pairs` / `target_col`）；缺失时 `play` / `campaign` 内置回退（场景默认八炮表 `FALLBACK_LINEUPS`：PE 3C八炮 / DE 神之八炮 / NE 美学八炮 / FE 经典八炮 / RE 方块八炮 / ME 对称八炮，落点列 8；`game_mode==0` 判冒险）。
- 门面 `Game.campaign(n=1, *, auto=False, lineup=None, cycle=869)`；CLI `pvztools campaign [N|auto] --remote --yes [--lineup NAME] [--cycle 869] [--no-collect] [--no-foreground] [--report out.json] [--md out.md]`（`--remote` 隐含 `--write --patch`，选卡另需窗口输入）。测试 1122 passed / 7 skipped。


