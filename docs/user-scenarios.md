# pvztools 用户场景与入口设计

原则：**功能全部在库里，前端只是薄壳。** 我们的差异化是"可编程"，不是和 pvztoolkit 拼 GUI。

## 1. 五类用户

| 用户 | 诉求 | 入口 | 成功标准 |
|---|---|---|---|
| ① Python 初学者 | 拿 PvZ 当练手沙盒，改一行代码游戏里马上看见 | `import pvztools`，Jupyter 教程 | 10 分钟内读到阳光并改掉；一节课写出自动收阳光 |
| ② 普通玩家 | 修改器、一键布阵、看出怪 | CLI → 轻量 GUI | 不写代码完成 Toolkit 同等功能 |
| ③ 无尽 / 轨道玩家 | 写轨道、跑键控、算砸率 | CLI + `.pvzt` 文件 | lint 零错误的轨道能在真机跑满 20 波并出报告 |
| ④ 改版作者 / 逆向爱好者 | 让工具支持自己的改版 | `pvztools scan <exe>`、`doctor` | 未知构建离线解析出 profile 并回流仓库 |
| ⑤ AI / RL 研究者 | 只读观测 + 动作接口 | `pvztools.gym_env()` | 真游戏与模拟器同一接口 |

## 2. 初学者 API（`pvztools` 顶层，零门槛）

```python
import pvztools as pvz

game = pvz.attach()                 # 找进程、识版本、校验；失败时说人话
game.sun                            # -> 25
game.sun = 999
game.wave, game.clock, game.countdown
game.paused

for z in game.zombies:              # 快照，只读
    print(z.row, z.name, z.hp)      # z.name 中文；z.type 是 ZombieType
for p in game.plants:
    print(p.row, p.col, p.name, p.hp)

game.plant("豌豆射手", row=3, col=1) # 中文/英文/枚举都接受
game.shovel(3, 1)
game.fire_cob(cob=(3, 1), target=(2, 9))
game.cheats.no_cooldown = True      # 修改器都挂在 cheats 下，默认关闭
```

设计要求：
- `attach()` 一行搞定；无参数时自动找 `popcapgame1.exe` / `PlantsVsZombies.exe` / 改版名；多开时列出让选。
- 错误信息面向人："游戏没有运行""请先进入关卡（现在在主菜单）""这个版本还不支持写入，只能读取（能力：READ_ONLY）"。
- 中文植物/僵尸名一等公民；所有属性有类型提示和 docstring（含偏移）。
- 所有写操作在 `cheats` / 动作方法下，读属性永远安全。
- 教程包 `pvztools[tutorial]`：Jupyter Notebook 6 课——读阳光 → 数僵尸 → 自动收阳光 → 条件判断放樱桃 → 写第一个挂机循环 → 读出怪表按关分支。

## 3. 玩家 CLI（② ③ ④）

```bash
pvztools doctor                        # 版本、能力分级、哪些地址没找到
pvztools scan <exe>                    # 离线解析，输出 profile JSON
pvztools mod sun 9990 | no-cooldown on | speed 5 | background on
pvztools lineup show "PE-3C八炮" | place "PE-3C八炮" | decode <code>
pvztools spawn show                    # 本轮 20 波出怪
pvztools track parse|lint|expand|to-seml|to-avz <file>
pvztools track run <file> --lineup ... # 键控执行，出执行报告
pvztools track sim <file> --rounds N   # [emu] 仿真砸率
```

## 4. 前端路线

| 阶段 | 入口 | 说明 |
|---|---|---|
| M1–M4 | Python API + CLI | 现在 |
| M4 后 | Jupyter 教程包 | 面向① |
| 稳定后 | `pvztools-gui`（独立包：修改器面板 + 阵型/出怪查看器；tkinter 或 PySide） | 面向②，不进核心包 |
| 可选 | 本地网页面板（轨道时间轴、出怪表、仿真图表） | 面向③ |

## 5. RL 接口草案（⑤）

```python
env = pvz.gym_env(scene="PE", lineup="PE-经典八炮", backend="game" | "emu")
obs = env.reset()            # 只读快照：阳光、波、倒计时、植物/僵尸表、卡槽 CD
obs, reward, done, info = env.step(action)   # action: 放卡/发炮/铲/等待
```
真游戏后端用反应式执行器驱动；模拟器后端用 pvzemu。两者观测结构一致。
