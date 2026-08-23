# pvztools

[![PyPI](https://img.shields.io/pypi/v/pvztools)](https://pypi.org/project/pvztools/)
[![Downloads](https://static.pepy.tech/badge/pvztools/month)](https://pepy.tech/project/pvztools)
[![Python](https://img.shields.io/pypi/pyversions/pvztools)](https://pypi.org/project/pvztools/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

Plants vs. Zombies（PvZ1 PC 版及各类改版）自动化框架 —— 纯 Python、零依赖，`pip install` 即用。
PvZ1 (PC) automation framework — pure Python, stdlib only, `pip install` and go.

> 与 AsmVsZombies、PvZ Toolkit 等既有工具不同，pvztools 是一个 **`pip install` 即用的 Python 框架**：多版本自适应、类型化只读对象模型、可编程的轨道执行器与修改器——别人的工具给你用，pvztools 给你写。
> Unlike existing tools (AsmVsZombies, PvZ Toolkit), pvztools is a **`pip install`-and-go Python framework**: version-adaptive, a typed read-only object model, a programmable track executor and modifier — other tools let you *use* them, pvztools lets you *build* with it.

[中文](#中文) · [English](#english)

> 本项目与 lmintlcx/pvztools、pvz.tools 无任何关系，仅是包名相同。
> Not affiliated with lmintlcx/pvztools or pvz.tools; the package name is a coincidence.

---

## 中文

### 这是什么

- **格式层**：社区轨道 DSL（`P6: PP|PP|PPSS (6|6|6)`、`.pvzt`）解析 / lint / 展开，阵型码编解码，Seml / AvZ 导出。
- **内存层**：附加运行中的游戏，读阳光、波数、倒计时、游戏时钟、植物 / 僵尸 / 卡槽 / 出怪表。默认只读。
- **修改器**：阳光、无冷却、自由种植、速度、自动收集等，全部显式打开、可回滚。
- **控制层**：键控（只向窗口投递鼠标消息）或远程调用（注入 shellcode 调游戏函数）种植 / 铲除 / 布阵 / 选卡 / 定帧发炮。
- **执行器**：以游戏内部帧钟（cs）调度轨道，给出 planned / observed / delta 报告。
- **名称 / 语言**：`pvztools.names` 统一解析中英官方名与社区昵称（曾哥 / 春哥 / gloom / cob）；`PVZTOOLS_LANG=zh|en` 切换提示语言。

### 安装

```bash
pip install pvztools        # 仅 Windows；Python >= 3.9；无第三方依赖
```

### 快速上手

```python
import pvztools as pvz

# 附加 / 读取（只读）
game = pvz.attach()                     # 找进程、识版本、校验
game.version, game.capability           # 'goty_1_2_0_1096_en', 'FULL'
game.sun, game.wave, game.countdown, game.clock, game.paused
for z in game.zombies: print(z.row, z.name, z.hp)
for p in game.plants:  print(p.cell, p.name, p.hp)
game.seeds, game.spawn                  # 卡槽 / 本关出怪表

# 修改（需显式 write=True；代码补丁在 close() 时自动恢复）
game = pvz.attach(write=True)
game.sun = 999
game.cheats.no_cooldown = True
game.cheats.sun_limit = True            # 代码补丁
print(game.doctor())

# 远程调用 / 键控
game = pvz.attach(write=True, remote=True)
game.plant("曾哥", 1, 1); game.shovel(1, 1); game.place_lineup("经典四炮")
game = pvz.attach(input=True)           # 键控：不写内存
report = game.run_track("P6: PP|PP", backend="input"); print(report.to_text())

# 一条龙：选卡 -> 布阵 -> 定帧发炮
game = pvz.attach(write=True, remote=True, input=True)
report = game.play_level("3C八炮", "P4: PP|PP|PP|PP (869|869|869|869)", cob_col=8)
```

名称解析：

```python
from pvztools.names import resolve_plant, plant_name, resolve_zombie
resolve_plant("曾哥") is resolve_plant("gloom") is resolve_plant("Gloom-shroom")   # PlantType.GLOOM_SHROOM
plant_name(42, "en")                    # 'Gloom-shroom'
plant_name(42, "zh", style="community") # '曾哥'
resolve_zombie("红")                    # ZombieType.GIGA_GARGANTUAR
```

### 命令行一览

```bash
pvztools doctor                                   # 版本识别、能力分级（FULL / READ_WRITE / READ_ONLY）
pvztools scan "C:\...\PlantsVsZombies.exe"        # 离线特征扫描（改版）
pvztools read [sun|board|plants|zombies|seeds|spawn|all]
pvztools spawn show | stats 200 | reroll --remote --yes | set <字符> --write --yes
pvztools mod sun 999 --write --yes                # 写内存
pvztools mod speed 5 | no-cooldown on | free-planting on --write --yes
pvztools mod sun-limit | auto-collect | background on --patch --yes     # 代码补丁
pvztools plant 豌豆射手 1 1 --remote --yes        # 远程调用（选卡界面 / 关卡中）
pvztools shovel 1 1 --remote --yes
pvztools lineup decode <阵型码> | show 3C八炮 | place 经典四炮 --remote --yes
pvztools track parse|lint|expand|to-seml|to-avz <轨道>
pvztools track run my.pvzt --input --yes          # --input 键控 / --remote 修改器 / --dry-run 只等待
pvztools reactive --policy p6 --cobs 29,59 --input --yes
pvztools play --lineup 3C八炮 --track "P4: PP|PP|PP|PP (869|869|869|869)" --cob-col 8 --remote --yes
pvztools campaign [N|auto] --remote --yes         # 连续自动推冒险关（掉落自动拾取、戴夫对话自动过）
pvztools chooser clear|pick 1 16 14|start|select 1,16,14 --yes
pvztools window info|front|calibrate
pvztools bench-clock --seconds 10
```

退出码：0 成功，1 参数错误，2 lint 错误，3 游戏没有运行 / 不在关卡 / 版本不支持。详见 `docs/track-format.md`、`docs/lineup-format.md`、`docs/DESIGN.md`。

### 安全模型

- **默认只读**。`pvz.attach()` / 任何 `pvztools read` 不写一个字节。
- 写内存需要 `--write` / `attach(write=True)`；代码补丁需要 `--patch`；远程调用（线程注入）需要 `--remote`（隐含 write + patch）；键控需要 `--input`。命令行里所有改动还要 `--yes` 确认。
- 注入类功能（远程调用、补丁）**默认关闭**，按会话授权，`game.close()` 时恢复补丁。
- 能力分级：READ_ONLY / UNSUPPORTED 的构建不接受任何修改。

### 兼容性

- **Steam GOTY 1.2.0.1096（英文）** 在真机上逐项验证（2026-08）。
- 内置 14 个官方构建的地址表（GOTY / 原版 / 中文版 / 日文版 / Beta 等）。
- 未知构建 / **改版**：走特征码（AOB）扫描并缓存（`pvztools scan` / `doctor`），能力分级随命中情况降级。

### 致谢

AsmVsZombies (AvZ)、lmintlcx/pvz、pvzclass、PvZ-Emulator、Seml、re-plants、LonelyStar / 囧丫乙 阵型库（ArrayDesign）、白菜 / Be2SiO4 / 轨道读本 等社区工作。本项目站在他们的肩膀上。

### 免责声明

- 本包**不包含任何游戏文件**、资源或地址表以外的游戏数据。
- 执行报告会明确标注后端：**键控**（只投递窗口消息）与 **修改器**（写内存 / 注入）分别标注，不混用。
- 输出**不适用于任何速通 / 排行榜提交**；请遵守社区规则。
- 使用修改器功能风险自负。

### 许可证

MIT

---

## English

### What it is

- **Format layer**: parse / lint / expand community track DSL (`P6: PP|PP|PPSS (6|6|6)`, `.pvzt`), encode / decode lineup codes, export to Seml / AvZ.
- **Memory layer**: attach to the running game and read sun, wave, countdown, game clock, plants / zombies / seed bank / spawn list. Read-only by default.
- **Modifier**: sun, no cooldown, free planting, speed, auto collect and more; every one is opt-in and reverted on close.
- **Control layer**: plant / shovel / place lineups / pick seeds / frame-locked cob fire through either the input backend (posts mouse messages only) or remote calls (shellcode calling game functions).
- **Executor**: schedules tracks on the game's own frame clock (cs) and reports planned / observed / delta.
- **Names / language**: `pvztools.names` resolves official zh / en names and community nicknames (曾哥 / 春哥 / gloom / cob); `PVZTOOLS_LANG=zh|en` switches messages.

### Install

```bash
pip install pvztools        # Windows only; Python >= 3.9; no third-party dependencies
```

### Quick start

```python
import pvztools as pvz

game = pvz.attach()                       # read-only: find process, identify build, verify
game.version, game.capability             # 'goty_1_2_0_1096_en', 'FULL'
game.sun, game.wave, game.countdown, game.clock, game.paused
for z in game.zombies: print(z.row, z.name, z.hp)
game.seeds, game.spawn

game = pvz.attach(write=True)             # modify (explicit); patches revert on close()
game.sun = 999
game.cheats.no_cooldown = True

game = pvz.attach(write=True, remote=True)
game.plant("gloom", 1, 1); game.shovel(1, 1); game.place_lineup("经典四炮")
game = pvz.attach(input=True)             # input backend: no memory writes
report = game.run_track("P6: PP|PP", backend="input"); print(report.to_text())

game = pvz.attach(write=True, remote=True, input=True)
report = game.play_level("3C八炮", "P4: PP|PP|PP|PP (869|869|869|869)", cob_col=8)
```

```python
from pvztools.names import resolve_plant, plant_name
resolve_plant("Cob Cannon") is resolve_plant("春哥") is resolve_plant("cob")   # PlantType.COB_CANNON
plant_name(47, "en"), plant_name(47, "zh", style="community")                # 'Cob Cannon', '春哥'
```

### CLI overview

`pvztools doctor | scan | read | spawn | mod | plant | shovel | lineup | track | reactive | play | campaign | chooser | window | bench-clock` — see the Chinese section above for the full list; `pvztools <cmd> --help` prints localized help (`PVZTOOLS_LANG=en`).

Exit codes: 0 ok, 1 bad arguments, 2 lint errors, 3 game not running / not in a level / unsupported build.

### Safety model

- **Read-only by default**: `pvz.attach()` and every `pvztools read` never write a byte.
- Memory writes need `--write` / `attach(write=True)`; code patches `--patch`; remote calls (thread injection) `--remote` (implies write + patch); the input backend `--input`. On the CLI every mutation also needs `--yes`.
- Injection (remote calls, patches) is **opt-in and off by default**, granted per session, patches reverted on `game.close()`.
- Capability grades: READ_ONLY / UNSUPPORTED builds refuse every mutation.

### Compatibility

- **Steam GOTY 1.2.0.1096 (en)** verified on a live game (2026-08).
- Built-in address tables for 14 official builds (GOTY / retail / Chinese / Japanese / beta ...).
- Unknown builds / **mods (改版)**: signature (AOB) scan with caching (`pvztools scan` / `doctor`); the capability grade degrades with what the scan finds.

### Credits

AsmVsZombies (AvZ), lmintlcx/pvz, pvzclass, PvZ-Emulator, Seml, re-plants, the LonelyStar / 囧丫乙 lineup database (ArrayDesign), 白菜 / Be2SiO4 / 轨道读本 and the wider community. This project stands on their work.

### Disclaimer

- Contains **no game files** or assets; only address tables and community data.
- Reports label the backend: **input** (window messages only) vs **modifier** (memory writes / injection) are never mixed.
- Output is **not suitable for speedrun / leaderboard submissions**; follow your community's rules.
- Use modifier features at your own risk.

### License

MIT
