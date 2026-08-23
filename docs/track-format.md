# pvztools 轨道格式规范（v0）

本文定义 `pvztools.schema.track` 接受的三种书写层级、统一 AST 与核心语义。目标：**不发明新记法**，兼容白菜《轨道理论》、Be2SiO4《三大变奏》、贴吧《轨道读本》与 Seml。

---

## 1. 术语与时间模型

- **cs**：厘秒，游戏逻辑 tick（默认 1cs = 10ms）。
- **波 (wave)**：一次选卡 20 波；w1–w9 上半场，w10 旗帜波，w11–w19 下半场，w20 最后一波。w9 / w19 / w20 为"收尾波"。
- **刷新点**：倒计时归零、下一波僵尸刷出的那一帧。每波的时间轴以**本波刷新点为 0**，负数表示刷新前（预判）。
- **效果轴 (effect)**：操作*生效*时刻（炮落地、冰生效）。**DSL 默认写效果轴**，与 AvZ DSL 一致。
- **操作轴 (operation)**：操作*发动*时刻。由执行器按 `schema.timing.effect_offset()` 从效果轴反推（炮 −373 / −378 / −387；冰核 −100 夜 / −299 昼；复制卡再 −320；樱辣瓜 +100）。
- **波长 (length)**：本波刷新点到下波刷新点的间隔。合法区间 601–3099cs（非旗帜波）；w9/w19 最长 4500+750；w20 5500。
- **运算量 (al)**：P = A = 1，N = 2，I = C = 0，IO = 1。**2 al = 1 re**（每波需两个运算量触发刷新）。

---

## 2. 符号表

### 2.1 操作符号

| 符号 | 名称 | kind | al | 说明 |
|---|---|---|---|---|
| `P` | 主炮 | cob | 1 | 单门炮 |
| `PP` | 双炮 | cob | 2 | 两门炮，默认 2/5 路（泳池）或对称两路 |
| `p` / `P'` | 铲种炮 | cob | 1 | variant = "replant" |
| `S` | 同步炮 | cob | 1 | variant = "sync" |
| `B` / `b` | 提前炮 前场 / 后场 | cob | 1 | variant = "before" / "before_back" |
| `D` / `d` | 延迟炮 前场 / 后场（尾炸） | cob | 1 | variant = "delay" / "delay_back" |
| `A` | 樱桃 | ash | 1 | |
| `A'` | 辣椒 | ash | 1 | variant = "jalapeno"；**方言 be2sio4：`a` 也可表辣椒** |
| `a` | 窝瓜 | ash | 1 | variant = "squash"；方言 be2sio4：窝瓜/辣椒/土豆雷统称 |
| `J` | 辣椒 | ash | 1 | 读本/AvZ 写法，同 `A'` |
| `W` | 窝瓜 | ash | 1 | AvZ 写法，同 `a` |
| `N` | 核（毁灭菇） | doom | 2 | |
| `N'` | 复制核 | doom | 2 | variant = "imitater" |
| `I` | 冰（寒冰菇） | ice | 0 | |
| `I'` | 复制冰 | ice | 0 | variant = "imitater" |
| `I3` | 冰三 (ICE3) | ice | 0 | variant = "ice3"；同理 `I1` `I2` `I4` |
| `C` | 高级垫材（地刺/高坚果/南瓜） | fodder | 0 | variant = "hard" |
| `c` | 普通垫材（小喷/阳光菇/胆小/花盆） | fodder | 0 | variant = "soft"；白菜方言中 `C` = 普通垫材 |
| `IO` | 自然输出 | natural | 1 | 仅用于运算量计算，不产生动作 |

方言（`dialect`）只影响冲突符号的解释：

| dialect | `a` | `C` | 说明 |
|---|---|---|---|
| `duben`（默认，轨道读本） | 窝瓜 | 高级垫材 | `c` 普通垫材，`A'` 辣椒 |
| `baicai` | 窝瓜 | 普通垫材 | `A'` 辣椒 |
| `be2sio4` | 辣椒/窝瓜/土豆雷（kind=ash, variant="light"） | 垫材 | `p` 铲种炮 |

### 2.2 逻辑符号

| 符号 | 名称 | 语义 |
|---|---|---|
| `\|` | 分波符 | 分隔相邻波 |
| `-` | 过渡符 / 刷新符 | 分隔"触发刷新前生效"与"触发刷新后生效"的操作；**触发刷新的那一炮归到 `-` 之后** |
| `/` | 分场符 | 同一波内上半场 / 下半场不同操作；对称时省略 |
| `~` | 波动符 | 波长或时间的范围，如 `539~619`、`17.5~19` |
| `( )` | 时间符 | 波长列表；也可不加括号尾随在操作串之后 |
| `,` `\|` 空白 | 波长分隔 | 波长列表内部的分隔，三者等价 |
| `:` `：` | 节奏名分隔 | `P6: ...`，全角冒号等价 |

### 2.3 波长单位推断

单个数值 `v`：
- `v ≤ 100` → 秒，`cs = round(v × 100)`（`6` → 600，`17.5` → 1750，`11.5` → 1150）
- `v > 100` → 厘秒（`869`、`1738`、`1976`）

整个列表独立逐项判断（社区确有 `(6|12|6)` 与 `1975|1975|750|750` 并存的写法）。

---

## 3. 层级 ①：速记 (shorthand)

一行文本，描述一个**循环**（通常对应一个炮 CD 周期）的节奏，不含具体落点。

```
[节奏名 ':'] 波串 [波长列表]
波串     := 波 ('|' 波)*
波       := 半场 ['/' 半场]
半场     := 操作串 ['-' 操作串]
操作串   := 操作*
操作     := 符号 ["'"] [数字后缀]
波长列表 := '(' 数值 (分隔 数值)* ')' | 数值 (分隔 数值)*
```

示例（全部来自社区原文）：

```
P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)
P4: PC|PC|PC|PC 869|869|869|869
ch4: IP-P|IP-P 1738|1738
ch6: P|I-P|P|I-P 601|1150|601|1150
ch5: P|I-P|I-P 601|1500|1500
邻C6u: I-P|I-P|N|P (1975|1975|750|750)
|PP|I-PP|N (6, 21, 9)
P5u: P|P|P|P|P 6|6|6|9|9
ICP-BPD/Pd
PIPIPN
```

### 3.1 复写与简写

- **复写**：每波显式写出全部操作（含对称的两门炮），每波 ≥ 2 al。`PP|I-PP|I-PP|N`
- **简写**：省略对称操作、`|`、`-`。`P|I-P|I-P|N`、`P|IP|IP|N`、`PIPIPN`

解析器判定：
1. 含 `|` 时按 `|` 分波；每波若 **al < 2 且不含 `/`** → 该波标 `mirrored=True`，展开时炮/灰烬操作镜像到对称半场（`P` → `PP`）。
2. 不含 `|` 且不含 `-` `/` 时视为纯简写串：按"累计 al 达到 1 即断波"贪心切分（`PIPIPN` → `P|IP|IP|N`；N 单独 2 al 自成一波）。切不出整数波时报错，不猜。
3. 含 `-` 或 `/` 而不含 `|` → 单波。

### 3.1b 容错规则（来自语料，`docs/research/track-corpus.md`）

解析器必须接受以下社区变体，并在 AST `meta["normalized_from"]` 里记录做过的规整：

| 变体 | 处理 |
|---|---|
| `+` 代替 `-`（`I+PP\|I+PP\|N`） | 等价于 `-` |
| 全角 `：（）｜，` | 映射为半角 |
| 首尾多余的 `\|`（`\|PP\|I-PP\|N\|`） | 去掉空波 |
| 波长修饰 `20+` `25-` `1361×3` `12s` `1560连续冰波` `(25\|9\|9\|9\|)` | 取数值；`×3` 展开为 3 项；尾部空槽忽略；`+`/`-` 后缀记入 `length_hint` |
| `Ice3` / `ice3` | 等价 `I3`；`I0` 记 variant `ice0` |
| `io` / `IO` | 自然输出 |
| 括号内非数值内容（`N(a)`、`PA（我就要先樱桃！）`、`c(二路)`） | 视为注释，剥离并告警 `ANNOTATION_DROPPED` |
| 节奏名小写 / 带前缀（`c5u:`、`邻c6u：`、`ch4-N：`） | 大小写不敏感解析 |
| `w1:…` 逐波前缀 | 速记串里出现 `wN:` 时，按逐波模式解析，selector = N |
| 未知字母（`i`、`A₁`、反引号 `` ` `` 代替 `'`） | 反引号/`’` 等价 `'`；`A₁` 等价 `A'`；其余报 `UNKNOWN_SYMBOL` 错误 |

### 3.2 节奏名校验

节奏名（`P6` `ch5` `邻C6u` `C6i` `S6` `I6i` `P5u`…）解析为 `RhythmName(family, param, prefix, suffixes)`，lint 时与波串做一致性校验（如 P 系波数 = 参数、ch 系含冰波数）。未知节奏名只告警。

---

## 4. 层级 ②/③：逐波 (`.pvzt`)

行式文本，无外部依赖。头部 `key: value`，随后若干波块。

```
# 注释以 # 开头
scene: PE
rhythm: P6  PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)
dialect: duben
cards: IINAW LPccc
zombies: 普杆舞车豚 丑气矿篮偷 红
protect: 18 28 58 68
lineup: LI43NMQIfe3BVVEc+sHIXUAPX7dvSCBUt1TS1OE8dn9XgraGkdQ1ZlSKiEUaVw==
timing: effect

w1~8 len 601
  PP 225 @ 25 9
  C 539~619 @ 1256 9
w9,19 len 4500
  PP 225 @ 25 9 ; PP 2000 @ 25 9
w20 len 5500
  PP 341 @ 25 9
  DD +107 @ 15 8.1875
  PP 2000 @ 25 9
w1%4, 2~9%2, 11~19 len 1976
  I 11 @ 3 5
  PP 1776 @ 25 8.75
w10
  PP 341
  A 401 @ 2 9 & W 401 @ 5 9
```

### 4.1 波块头

```
波块头 := 'w' 选择器 (',' 选择器)* ['len' 数值]
选择器 := N | A'~'B | A'~'B'%'k | N'%'k
```
- `A~B` 闭区间；`A~B%k` 步长 k；`N%k` 从 N 起每 k 波直到 20。
- `len` 用 §2.3 单位推断；省略则由 lint 按 §1 区间提示。

### 4.2 操作行

```
操作行 := 动作 (('&' | ';') 动作)*
动作   := 符号 时间 ['@' 落点]
时间   := 整数 | 整数'~'整数 | '+'整数 | '-'整数
落点   := 行集 列 | 行 列
行集   := 连续数字，如 25 = 2 路与 5 路；1256 = 1/2/5/6 路
列     := 整数或小数（1/80 格精度，如 8.75、8.1875）
```
- `&`：同时生效；`;`：顺序，与换行等价。
- `+N`：相对上一动作的生效时刻；`-N`：刷新前 N cs（预判）。
- 多行落点与多炮符号（`PP`、`DD`）按顺序配对：`PP 225 @ 25 9` = 2 路 9 列 + 5 路 9 列。
- 省略 `@` → 由展开器按场地默认（泳池 2/5 路、陆地 2/4 路…）补全，并在 AST 标 `position=None`。

### 4.3 头部键

| 键 | 值 |
|---|---|
| `scene` | `DE NE PE FE RE ME` |
| `rhythm` | 速记串（§3），可选 |
| `dialect` | `duben baicai be2sio4` |
| `cards` | AvZ 单字母选卡串（`A B F G I J K L M N P T U W _ C c`，重复字母 = 复制者） |
| `zombies` | AvZ 单字出怪串 |
| `protect` | 保护位列表，`行列` 两位数 |
| `lineup` | 阵型码 |
| `timing` | `effect`（默认）或 `operation` |

---

## 5. 统一 AST（`schema.track.ast`）

```python
@dataclass(frozen=True)
class Target:      rows: tuple[int, ...]; col: float
@dataclass(frozen=True)
class Op:
    symbol: str                 # 原始符号，如 "PP", "I3", "A'"
    kind: str                   # cob | ash | doom | ice | fodder | natural
    variant: str | None         # replant sync before delay delay_back jalapeno squash imitater ice3 hard soft light ...
    count: int                  # PP = 2
    al: int                     # 运算量
    phase: str                  # "pre" | "post"（相对 '-'；无 '-' 时 post）
    effect_cs: int | None       # 效果轴时刻；速记里为 None
    window_cs: tuple[int,int] | None
    relative: bool              # 时间是 '+N' 相对量
    target: Target | None
    simultaneous_with_prev: bool  # '&' 连接
@dataclass(frozen=True)
class HalfWave:    ops: tuple[Op, ...]
@dataclass(frozen=True)
class Wave:
    selector: tuple[int, ...]   # 速记：循环内序号 (1..n)；逐波：实际波号
    top: HalfWave
    bottom: HalfWave | None     # '/' 之后；None = 对称
    mirrored: bool              # 简写镜像标记
    length_cs: int | None
    length_range: tuple[int,int] | None   # '~'
    raw: str
@dataclass(frozen=True)
class RhythmName:  family: str; param: int | None; prefix: str; suffixes: tuple[str, ...]; raw: str
@dataclass(frozen=True)
class Track:
    waves: tuple[Wave, ...]
    rhythm: RhythmName | None
    scene: str | None
    dialect: str
    timing_base: str            # "effect" | "operation"
    cards: str | None
    zombies: str | None
    protect: tuple[int, ...]
    lineup_code: str | None
    kind: str                   # "shorthand" | "pvzt"
    meta: dict
```

`to_json(track)` / `from_json(data)` 往返无损；`track.normalize()` 返回复写形式（镜像展开、单位统一为 cs）。

---

## 6. lint 诊断（`schema.track.lint`）

每条 `Diagnostic(level, code, message, wave, op)`，level ∈ `error | warning | info`。

| code | level | 规则 |
|---|---|---|
| `AL_BELOW_2` | warning | 非冰波写满后 al < 2 |
| `LEN_RANGE` | error | 非旗帜波长 ∉ [601, 3099]；w9/19 > 5250；w20 ≠ 5500 |
| `COB_CD` | error | 同门炮两次生效间隔 < 3475 |
| `CARD_CD` | error | 同卡两次使用间隔 < 卡 CD（冰/核/樱/辣 5000，窝瓜 3000，垫材 750） |
| `ICE_DAY_NEEDS_COFFEE` | info | 白天场地用冰/核提示咖啡豆（生效偏移 299） |
| `POOL_NEEDS_LILY` | warning | 泳池 3/4 路落非炮植物未标荷叶 |
| `ROOF_NO_DOOM` | error | 屋顶用核 |
| `TARGET_GRID` | error | 列不是 1/80 的整数倍或超出 1–9 |
| `RHYTHM_MISMATCH` | warning | 节奏名与波数 / 冰波数不一致 |
| `SIMPLIFIED_AMBIGUOUS` | error | 简写串无法按 2 al 切分 |

---

## 7. 展开器（`schema.track.expand`）

输入：`Track`（速记或逐波）+ `ExpandContext(scene, cob_positions, ice_slots, fodder_rows, wave_count=20)`。
输出：`Plan`：20 波 × 有序 `Action(wave, effect_cs, operation_cs, kind, plant, target, source_op)`。

规则：
1. 速记循环按 `selector` 平铺到 1–20，w10/w20 为旗帜波可覆盖；w9/w19 默认延后收尾。
2. 炮位分配：就绪队列——按上次发射时间排序取最早可用；同一波内 `PP` 取两门；屋顶按列区分风炮 / 平炮。
3. 冰卡轮换：`ice_slots` 循环；昼场自动附加咖啡豆动作（−299）。
4. 垫材：`fodder_rows` 循环；无落点时取前场列 9。
5. `operation_cs = effect_cs − effect_offset(kind, scene, target)`。
6. 任何无法分配（炮不够、冰不够）→ `Plan.issues` 里记 `Diagnostic`，不抛异常。
7. **激活炮时机由节奏波长推导**（速记无时间的 post 炮）。机制：下一波在本波总血量降到阈值后 200cs 刷新，且该判定最早发生在波内 401cs → 最短波长 601。要让一波长 L，激活炮组（本波最后一个无时间的 post 炮符号，如 `PP` 或 `I-PP` 中 `-` 后的 `PP`）须在 `effect_cs = max(341, L − 200)` **落地**（`expand.rhythm_cob_effect`）：
   - P4 869 → 落地 669，陆地行 2/5 发射 296（669 − 373）；ch6 冰波 1150 → 落地 950（Be2SiO4：「ch6 激活炮落点 9 列，应于 950cs 生效」）。
   - L = 601 → 401（发射 28）。AvZ 的 P6 惯例为 −32 发射 / 341 落地，二者等价：判定不可能早于 401，落地 341 与 401 都得到 601 波。L < 601（如 `6` = 600）或未知 → 回退 341；未知时附 `info:RHYTHM_LENGTH_UNKNOWN`。
   - 同波其他 post 炮组相对激活组取默认：`S` 同刻，`B` 提前 `SYNC_BEFORE_CS`（100，TODO 占位），`D` 延后 `SYNC_DELAY_CS`（100，TODO 占位）。pre 段 `I-` 冰保持 effect 1（完美预判冰）。
   - 旗帜波 w10/w20 出怪更晚：effect 加 `HUGE_WAVE_DELAY = 341`（CD 检查时该波长也相应加 341）；收尾波 w9/w19 按上式计算不另行处理。
   - 逐波 `.pvzt` 的显式时间不受影响；`operation_cs` 仍按规则 5 由 effect 减去飞行时间（陆地 373 / 水路 378 / 屋顶 387）得出。

---

## 8. 导出

- `to_seml(track)` / `from_seml(text)`：与 Seml 互转（Seml 为逐波格式的子集）。
- `to_avz_dsl(track)`：生成 AvZ 2 DSL C++ 片段（单向）。
