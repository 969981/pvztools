# lonelystar.org 资源目录（爬取于 2026-08-22）

站点：http://lonelystar.org/ （LonelyStar / 植吧"囧丫乙"整理；资源站更新至 2026-05-25）。
本文只记录**对 pvztools 有用的条目与页面 URL**，不收录游戏本体/改版的网盘、蓝奏直链（版权物，仓库不分发）。

## 1. 对 pvztools 最直接有用的条目

| 条目 | 用途 | 页面 |
|---|---|---|
| **《植物大战僵尸1代(非年度版)内存地址》**（崇明人家123 整理） | 1.0.0.1051 完整结构图：LawnApp(0x6A9EC0) → Board(+0x768) 内几乎全部字段；僵尸/植物/子弹/物品/小推车/场地物品/卡槽/存档结构与 stride。**是 `profiles/1.0.0.1051.json` 的主要依据之一** | http://lonelystar.org/ResDownload/1_PC_Memory.htm（原帖 https://tieba.baidu.com/p/5575921846） |
| 贴吧轨道读本提要 V3.0 离线第叁版（QNYXJ 提供） | 轨道记法权威参考 | 下载站"一代技术文档"分类（蓝奏云离线包） |
| 【Kaleidoscope of Endless Build Culture】九章阵华录 | 无尽阵型文化总览，阵型命名与分类语料 | https://tieba.baidu.com/p/5272254427 |
| IZE 算血公式 | IZE 相关（暂不在范围） | https://tieba.baidu.com/p/5104607724 |
| SteamPvZReadme 手册（英文） | Steam 版说明 | 下载站"一代技术文档" |
| JSPVZ v12.10.25 源码（LonelyStar，开源、非商用） | JS/HTML5 重写版，游戏逻辑参考（非官方，数值需与 PvZ-Emulator 交叉核对） | http://lonelystar.org/ 首页"前端代码练习" |
| PVZ 网页布阵器 ArrayDesign（363 个阵型已导出为 `docs/research/arraydesign-lineups.json`；**规范副本为包数据 `src/pvztools/data/lineups.json`**，经 `pvztools.data.load_lineups()` 读取，作者 LonelyStar/囧丫乙 已授权随包分发） | 网页布阵/截图工具，有"载入/导出"功能【其导出格式待核实，可能与 pvz.tools 阵型码不同】 | http://lonelystar.org/ArrayDesign |

## 2. 官方 PC 构建（版本表校验用）

### 英文原版（http://lonelystar.org/ResDownload/1_PC_E.htm）
- 1.0.0.1051（站点推荐；AvZ / rpze / pvztoolkit 的参考构建）
- 1.2.0.1065（修正裂荚 BUG；"据说"修正屋顶上界之风炮落点偏移）
- 1.2.0.1073 年度版（GOTY）；另有 1.2.0.1073 美版试玩版
- **Steam 1.2.0.1096**：解压后运行 **`popcapgame1.exe`**；存档位于 `C:\ProgramData\Steam\PlantsVsZombies\userdata`；站点另提供"Steam 正版可用七项修改器"→ 证实 Steam 版可被外部修改器读写
- 英文年度加强版（五场地无尽）
- 内测版 BLOOM&DOOM（早期构建，地址未知）

### 中文版（http://lonelystar.org/ResDownload/1_PC_C.htm）
- 1.0.0.1051 汉化 1 版 / 汉化 2 版（站点推荐，社区主流）/ 汉化 2 安装版 / 图鉴加强版 — 同一 exe 构建，仅资源汉化 → **地址应与英文 1051 一致**【待实测】
- 1.2.0.1065 汉化版 — 站点注明"很多修改器无效（v1.8.7、v3.1、九项属性修改器均不可用）" → 证实 1065 代码地址与 1051 不同
- 1.2.0.1073 年度版植吧汉化版 / 年度加强版（站点推荐）
- steam 汉化版（六三enjoy，基于 Steam 1096 换资源）
- 最后一战（官方街机版汉化，竖屏，2.07G，不在范围）

### 多国语言版（http://lonelystar.org/ResDownload/1_PC_Mu.htm）
俄 / 德 / 西 / 法 / 意 / 日 / 韩(1051) / 荷(1093) / 瑞典(1093) / 葡(1093) 等——其中德/西/法/意由 lmintlcx 提供，对应 pvztoolkit 偏移表里的构建。

## 3. 改版清单（http://lonelystar.org/ResDownload/1_PC_C2.htm）— 待核实第 12 条的答案

| 改版 | 作者 | 语言 | 与原版关系（站点描述推断，**基于哪个构建待实测**） | 备注 |
|---|---|---|---|---|
| DLC 版 (PvZ: DLC Edition) | Samen 及制作组（海外） | 英文 | 新大关、新植物新僵尸、本地对战 → 深度修改/可能重编译 | 2024-03 首发，持续更新；腾讯文档分发 |
| 精华版 | 萌新迎风听雨 | 中文 | 集成各平台独占内容；多平台（Win/Android/iOS/macOS/Linux）| 2025 年 1.0，至 2025-08 为 1.5.3 |
| **95 版 / 0–95 主程序包** | 崇明人家123 | 中文 | 基于 1.0 汉化的 **96 个 exe 变体**（0–95），"铁桶铁门、隐藏关卡、后台运行、无限制出怪等几十项改变"；已知 0/16/38/42/46/52/58/59/62/68/74/80/82/84/90/95 可玩 | **特征码回归测试的理想语料**：同源渐进补丁 |
| 强化版 v96 | 崇明人家123 → king3唐三 | 中文 | 95 版续作 | |
| 胆小菇之梦 | 北窗遥望（B 站） | 中文 | 新增玉米投手；配套修改器（ctrl+1~6 开关）→ 靠外挂修改器实现，exe 可能为原版 | |
| β 版 | 牛二龙疼 | 中文 | **Gitee 开源** | https://pvz-beta.site/ |
| TAT 时空环游之旅 | 江南游戏社 × 玩窝头 | 中文 | 独立字库、改版主程序、全新植物僵尸 → 重编译级别 | https://tat.pvzhome.com/ |
| 随机版系列（终极随机版 / 随机卡片 / 随机僵尸 / 随机礼盒 / 随机模仿者） | 叔丁基_锂（B 站） | 中文 | 带"右下角控制台" → 注入式改造 | |
| GHtr 版 1.2.2 | GHTR绮想馆 | 中文 | 二代植物僵尸设定移植 | pvzghtr.top |
| 原？版 | — | 中文 | "常见改版" | |
| 鉴版 | — | 中文 | "常见改版" | |
| 1.0 汉化 3.1 / 3.1 最终版 | 玩窝头网络工作室 | 中文 | 优化版 = 资源级修改；加强版 = 替换为崇明人家123 强化版主程序 | |

**95 版作弊指令**（与原版共用机制，对应内存 Board+0x5761~0x5767 七个布尔：mustache / trickedout / future / pinata / dance / daisies / sukhbir）：
`dance` 坚果→爆炸坚果；`daisies` 关卡不出墓碑；`future` 切 BGM；`pinata` 僵尸进家不死；`trickedout` 触发小推车；`sukhbir` 无视阳光/无冷却/紫卡直种。

## 4. PC 辅助工具（http://lonelystar.org/ResDownload/Modifier.htm，35 项，节选与本项目相关者）

| 工具 | 作者 | 对本项目的意义 |
|---|---|---|
| A-TAS 2.3 / A-TAS-MORE / A-TAS 小工具 | sin20、一望碧水 / SKOSKX | 基于 AvZ 的 TAS 插件，附 .h 源文件；仅保证英文原版；有"修复上界之风"功能 |
| 崇明人家123 终极修改器 / v2.5.1.4 修改器 | 崇明人家123 | 同一作者写了内存地址文档，修改器功能清单可对照我们的 §8 |
| v1.9 + v1.8.7(188) 修改器 | — | 社区最常用；注意 1.8.7 曾导致"地刺掉血"误判 BUG（站点澄清） |
| PvZ Toolkit | lmintlcx | 外链 https://pvz.lmintlcx.com/ |
| Steam 正版可用七项修改器 | — | 证实 Steam 1096 外部修改可行 |
| pvz 随机数修改器 v2.1/v3.0 | 失控的指令 | 锁定冻结时间、激活血量比例、自然刷新上限、出生点等 → 说明这些 RNG 点都有明确地址，可作为校验/测试用 |
| 零度小工具 TSV 修改器 | 失控的指令 | TAS 修改器 |
| 时间计算器（Excel 宏） | sqrt_7 | **存冰/冰透支计算逻辑**（循环波长、用冰时机、收尾冰、存冰方式 → 剩余冰/产冰/耗冰/开局冰数），可作为阵容生成里冰位计算的参考规格；作者注明未考虑咖啡豆 2.98s |
| 植物和僵尸生命值显示工具 | 不在宝前 | 适用 1.0 / 1.2.0.1065 / 大部分年度版 → 多版本外部读内存先例 |
| 血量显示器 | 六三enjoy | 仅原版 |
| IZE 系列（全随机插件、矿工小助手、一键算血） | 多人 | 暂不在范围；矿工小助手源码基于 pvzclass |
| 梯子量产机 / 小王子出怪修改器 / BT 修改器 | — | 出怪控制先例 |
| PVZ Studio 2022 | 萌新迎风听雨 | PAK 解包/打包、reanim/particles 转换，.NET；资源层工具，不在范围 |

## 5. 其他相关站点（来自首页友链）
- 植僵工具箱 pvz.tools / https://pvz.lmintlcx.com/
- PVZ 之家 http://pvzchina.anyplace.cn/
- 老豆荚 Oldpods https://oldpods.cn/ ；PvZ 同人创作者论坛 http://www.pvz.moe/
- β 版 https://pvz-beta.site/ ；TAT https://tat.pvzhome.com/ ；江南游戏 http://jiangnangame.com/
- 直链镜像 https://pvz.10001000.xyz/

## 6. 对设计的影响
1. **改版覆盖策略**：先以"95 版 0–95 系列"做特征码回归（同源渐进补丁，理想的 fuzz 语料）；DLC / TAT / GHtr 这类重编译级改版放到后期，能力分级允许 READ_ONLY。
2. **Steam**：`popcapgame1.exe` 进程名、`ProgramData\Steam\PlantsVsZombies\userdata` 存档路径写入 profile。
3. **1051 汉化版**与英文 1051 同构建，profile 共用，但 L0 识别不能依赖窗口标题文字（汉化后不同）。
4. **冰位计算**：sqrt_7 时间计算器的输入/输出定义直接作为 `generate/` 里 `IceBudget` 的规格。
5. 作弊指令布尔（Board+0x5761~0x5767）进对象模型 `Board.cheats`。
