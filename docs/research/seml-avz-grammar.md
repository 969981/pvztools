# Research: seml-avz-grammar (2026-08-22, agent-gathered; verify before relying on)

## Sources (verified by cloning repos, 2026-08-22)
- Seml: `https://github.com/Rottenham/seml` (VS Code extension "Crescendo.seml"; TS parser `src/parser.ts`, 1008 lines; README v1.16.0 2026-02-20). Local clone: `C:\Users\mike2\AppData\Local\Temp\claude\C--MyAILab\9b6c7fa6-bf46-4013-a521-0bd4ad13cfc3\scratchpad\seml`
- AvZ2: `https://github.com/vector-wlc/AsmVsZombies` @ commit `c42676c269b5b482a1eb9203a5b979e9d8a2a5c7` (2026-06-26). `inc/dsl/shorthand.h` is a one-liner `#include "shorthand_240713.h"` — all line citations below are to `inc/dsl/shorthand_240713.h` (`__DSL_SHORTHAND_VERSION__ 240713`).

---

# 1. SEML grammar (from `src/parser.ts` — authoritative)

## Preprocessing (`expandLines`, parser.ts L1151-1201)
- Split on `\r?\n`. Per line: `line.split("#")[0].trim().replace(/[ \t]+/g,' ')` → **`#` starts a comment anywhere on the line**; runs of spaces/tabs collapse to a single space. Empty lines ignored.
- Tokens are space-separated; first token = `symbol`.
- **Loop expansion**: a line whose first token starts with `w` AND contains `~` (e.g. `w1~4 0 1672`) — `startWave = natural between 'w' and '~'`, `endWave = after '~'`; error if either non-natural or start > end. The block = that line plus all following lines until the next line starting with `w` (note: checks `line.startsWith("w")`, any line starting with w). The block is duplicated once per wave number; the `w` line is rewritten to `w{N} <rest>`. Other lines copied verbatim (so `SET x x+24` inside the block re-executes each iteration).

## Variables (`SET`, L837-868; substitution L1203-1216)
- `SET <name> <expr>` : name not empty, not `/^\d+$/`; expr (after variable substitution) must match `/^[0-9+\-*/()]+$/`, evaluated with JS `eval`, must be finite. Stored in `out.setting.variables`.
- Substitution: for every subsequent line, the first token (first 2 tokens for `SET`) is kept; the remainder has every variable name textually `replaceAll`'d by its value (no word boundaries!). Applied before symbol dispatch. Variables are deleted from output JSON.

## Header / setting lines (dispatch L1051-1112; a `scene:` pass runs first L870-899)
| Line | Grammar | Notes |
|---|---|---|
| `scene:XX` | `scene:` + one of DE NE PE FE RE ME (case-insensitive) | duplicate → error. Maps to internal scene: DE/NE→"NE", PE/FE→"FE", RE/ME→"ME". Default "FE" if absent. Max rows: 6 for FE (and undefined), 5 for NE/ME. |
| `protect:` | `protect:` then space-separated tokens `RC` or `RC'` (row digit, col digit; trailing `'` = normal plant, else cob) | cob col 2..9 (cob occupies col-1..col), normal col 1..9; overlap → error "保护位置重叠". |
| `repeat:N` | positive int | → binary arg `-r N` |
| `require:` / `ban:` | zombie types: if any non-`[A-Za-z\s]` char present → string is split per Chinese char; else split by spaces into 4-letter EN abbrs (lowercased) | → `-req a,b,c` / `-ban ...`; must be in acceptableZombieTypes; not banned per scene; no dup across require/ban |
| `huge:`, `activate:`, `dance:`, `natural:`, `cobDelay:` | `true`/`false` (case-insens) | flags `-h`, `-a`, `-d`, `-n`, `-cd`; emitted only when true |
| `types:` | zombie types (same parsing, no acceptable check) | `-z` |
| `targetPos:N` | positive int | `-x N` |
| `avzTime:` | bool | not passed to binary; post-processes times (see below) |

Unknown first token → error "未知符号".

## Wave line (`parseWave`, L310-400)
```
<wtoken> [<iceTime> ...] <waveRange>
wtoken    := "w"            -- auto: previous wave number + 1
           | "w" <1..99>    -- must equal prev+1 (sequential, no gaps, no dups)
iceTime   := natural (>=0), zero or more; "0" = perfect-predicted ice, "10" = Ice3 (README); in cob-equivalent time
waveRange := <waveLength> | <startTick> "~" <waveLength>
waveLength := natural > 0;  startTick natural <= waveLength
```
- At least 2 tokens required. Last ice time must be ≤ waveLength.
- JSON: `{ iceTimes: number[], waveLength: number, actions: Action[], startTick?: number }` pushed to `out.waves`.
- After `w1~N` expansion, `w` lines appear as `w1`, `w2`... so sequential checks still apply.

## Time token (`parseTime`, L402-419)
`time := natural | "+" natural` ; `+N` = N cs after the **time of the last action in the current wave** (error if no previous action). No negative numbers allowed anywhere (parseNatural = `/^\d+$/`).

## Cob line (`parseCob`, L421-506; dispatch regex L1086-1089)
```
symbol  := /^(B|P|D)\d?$/i   -> 1 cob ; /^(BB|PP|DD)\d?$/i -> 2 cobs
line    := symbol <time> <rows> <col>           (exactly 4 tokens, extra -> error)
rows    := string of exactly cobNum digits, each 1..maxRows (sorted ascending)
col     := /^\d+(\.\d+)?$/ in [0.0, 10.0]
```
- Trailing digit on symbol (e.g. `P3`) = cob tail column 1..8, **only allowed when scene is ME/RE** (roof), and **required** on roof.
- B/D/P are semantically identical in the parser (`symbol` is stored). JSON: `{op:"Cob", symbol, time, positions:[{row,col}...], cobCol?}`.

## Fodder line (`parseFodder`, L558-714)
```
symbol := "C" | "C_POS" | "C_NUM"       (case-sensitive)
line   := symbol <times> <rows> <col> [<arg>...]
times  := <time>                    -- plant only
        | <time> "+" <natural>      -- shovel N cs after planting
        | <time> "~" <time>         -- shovel at absolute (or +relative) time
rows   := ( digit [ "'" | '"' ] )+   ; "'" = Puff (小喷/阳光菇), '"' = Pot (花盆), none = Normal; digits 1..maxRows, no duplicates
col    := natural 1..9
arg    := "choose:" <1..nRows>  | "waves:" <n>(,<n>)*   (waves 1..currentWaveNum, no dup)
```
- Shovel-time parsing (L527-556): delimiter = max(lastIndexOf('+'), lastIndexOf('~')); if index ≤ 0 there's no shovel part (so a leading `+` is the delay prefix). Part after `~` is parsed via parseTime relative to the card time (so `~+N` also works); shovelTime < cardTime → error.
- `C` → `{op:"FixedFodder", symbol, time, shovelTime?, fodders:Fodder[], positions}`.
- `C_POS`/`C_NUM` need ≥2 rows → `{op:"SmartFodder", ..., choose, waves}`; `choose` required for `C_POS`, defaults to nRows for `C_NUM`; `waves` defaults `[]` (= all waves). Only `choose`/`waves` keys accepted.

## Card lines (`parseFixedCard` L716-770, `parseSmartCard` L772-835)
| symbol | plant | PlantType hex |
|---|---|---|
| `A` | cherryBomb | 0x2 |
| `J` | jalapeno | 0x14 |
| `a` or `W` | squash | 0x11 |
| `N` | doomshroom | 0xF |
| `G` | garlic | 0x24 (supports `+N`/`~T` shovel time like C) |
| `A_NUM`, `J_NUM`, `a_NUM`/`W_NUM` | smart versions, rows ≥2 digits, no dup |
- Fixed: `symbol <time> <row> <col>` exactly; row 1..maxRows, col 1..9 (integers). JSON `{op:"FixedCard", symbol, time, shovelTime?, plantType, position:{row,col}}`.
- Smart: `symbol <time> <rows> <col>` → `{op:"SmartCard", symbol, time, plantType, positions}`.
- Times are "effect time" on the cob-equivalent basis (README: 用卡 基于炮等效时间).

## Post-processing (`parse`, L1120-1142)
- If `avzTime:true`: FixedCard A/J/a/N/W and SmartCard times `-= 1`; Fixed/SmartFodder times `+= 1` (and shovelTime `+= 1`). (README: AvZ basis = predicted ice at 1, fodder fast at 4n-1.)
- Each wave's actions are sorted by `time` (stable).
- Output: `{ out: {setting:{protect?,originalScene?,scene}, waves:[...]}, args }` — written as `<basename>.json` and passed to native test binary as `[...args flat, "-f", jsonPath]`.

## Zombie type tables (`src/zombie_types.ts`)
CN single char → ID: 普0 旗1 障2 杆3 桶4 报4(!) 门6 橄7 舞8 伴9 鸭0xa 潜0xb 车0xc 豚0xe 丑0xf 气0x10 矿0x11 跳0x12 雪0x13 偷0x14 梯0x15 篮0x16 白0x17 鬼0x18 红0x20.
EN 4-letter: regu flag cone pole buck news(=4) scre foot danc back duck snor zomb dolp jack ball digg pogo yeti bung ladd cata garg imp giga.
Note: seml maps 报/news to buckethead (0x4) — likely a bug/intentional alias; PvZ's newspaper is 0x5. Acceptable for require/ban: pole, buck, scre, foot, danc, snor, zomb, dolp, jack, ball, digg, pogo, bung, ladd, cata, garg, giga. Banned: DE {snor,dolp}; NE {snor,zomb,dolp}; RE/ME {danc,snor,dolp,digg}.

## Complete example .seml files (README/templates.ts, verbatim)
Smash:
```
# 测试 PE.垫材24 旧PDc解的砸率
scene:PE
protect:18 28 58 68  # 要保护的炮位
repeat:300

w 601
PP 225 25 9
C 539~619 1265 9

w 601
PP 225 25 9
DD +107 15 8.1875
C 539~619 1256 9
```
Explode:
```
scene:NE
protect:18 28 38 48 58
repeat:10000
SET x 776
w1~3 0 1200~1500
C_NUM x+266 1"234'5' 9
SET x x+24
```
Refresh:
```
scene:PE
require:红白
huge:false
activate:true
dance:true
natural:true
cobDelay:true
repeat:1000
w 601
PP 225 25 9
w 601
PP 260 25 9
```
Pos: `scene:PE / types:红 / repeat:20000 / w 601 900 / C 445+200 1256 9`. Pogo: `protect:15 17 / w 0 1200~1800` (on roof add e.g. `P3 300 2 9`).
Markdown inline blocks: ```` ```seml type=smash|explode|refresh|pogo name=... ```` (README "内联测试").

---

# 2. AvZ 2 DSL (`inc/dsl/main.h`, `inc/dsl/shorthand_240713.h`)

## Core types (`inc/avz_types.h`)
- `struct ATime { int time; int wave; ATime(int wave, int time); }` (L99-107). `struct ATimeOffset { int wave=0; int time=0; }` with +,-,*,unary- (L119-177). `ATime ± ATimeOffset` defined. `operator""_cs(x)` → `ATimeOffset(x)` (L196). `prev_wave{-1,0}`, `next_wave{1,0}`.
- `ATimeline` (avz_timeline.h): `&` merges timelines (L178), `timeline + offset` / `offset + timeline` / `timeline - offset` shift (L187-195), `{a, b, c}` initializer-list merges.

## Macros (`main.h` L335-338)
```cpp
#define At(...)      (__VA_ARGS__) <<= (__ADSLCastHelper)
#define OnWave(...)  At(AWave(__VA_ARGS__))
#define Do           (ATimeline)[=]() mutable
#define CoDo         (ATimeline)[=]() mutable->ACoroutine
```
- `At(offset) timeline` → `timeline + offset` (ATimeOffset <<= ATimeline, main.h L276). `At(ATime) timeline` → `AConnect(time, timeline)` (L280-284). `At(AWave) timeline|sequence` → connects to `ATime(wave,0)+_offset` for each wave (L127-135). `At(x) {a, b}` works via `__ADSLCastHelper(initializer_list)`. Absolute time must be outermost; `At` binds looser than `&`; `+` binds tighter than `&` (tutorial dsl/2).
- `now` (shorthand L10-15) = `ANowTime(true)` (ATime); `At(now + delay) Do {...}`.

## Wave selector literals (`main.h` L157-274)
- `N_wave` → `AWave(N)`; `a_b` → `std::vector<int>` {a..b step 1}; `a_b_wave` → AWave; `a_b_stepS` / `a_b_stepS_wave` for S=1..41; `b` (last) is 0..41 (generated by `__DeclareRange(0..41)`), so `1_8`, `10_18`, `10_19_step3`, `2_9` all exist. `AWave(args...)` accepts ints, vectors, AWaves; `AWave, AWave` via `operator,`.
- Phase: `waves % p` (AWave::operator%, L105-112; also `vector<int> % int` L153) inserts a negative marker `-p` that resets the phase counter to p-1 (0-based internally; tutorial says phase numbers from 1). `ASequence` = `a | b | c` (main.h L6-32); `wave <<= sequence` picks `sequence[phase % size]`, phase increments per connected wave regardless of skipped waves. Warning from tutorial: `1 % 4` is integer arithmetic — write `1_1 % 4`.

## Shorthand operations and effect offsets
All DSL ops are **effect-time based**: they internally do `At(-GetEffectOffset()) op`.

| Op | Signature | Effect offset / semantics | Lines |
|---|---|---|---|
| `P(row, col)`, `P(cm, row, col, ...)`, `P<RECOVER_FIRE>` | rows given as digit-string int (`1256` → rows 1,2,5,6 via `__ParseRow`, L144-160); varargs pairs `P(15,9,26,8)`; `slope`/`flat` are `ARoofCobManager(1..8)`/`(8..1)` (L93) | Cob effect offset `GetEffectOffset()`: roof → **387**; target row is POOL → **378**; else **373** (L120-127; comment says 376, code says 378). Fires `At(-offset)`. | L175-203 |
| `PP(col=9)` | `P(nRows==5 ? 24 : 25, col)` | L210-213 |
| `D<delay>(args...)` | `P(args...) + delay`; no RECOVER_FIRE | L219-222 |
| `DD<delay>(col)` | `P(nRows==5 ? 14 : 15, col) + delay` | L227-230 |
| `RP(cobRow, cobCol, row, col)` | roof fire with explicit cob tail grid | L134-142 |
| `Card<delay>(seed,row,col)`, `Card(vector<ACardName>)`, `Card(seeds,row,col)`, `Card(seed,positions)` | plants first valid position; auto lily pad/flower pot; `delay>0` → shovel at `now+delay`; effectOffset 0 | L233-314 |
| `Shovel(row,col[,type|pumpkin])`, `Shovel(vector<AShovelPosition>)` | `Do` | L320-340 |
| `A(row,col)` | cherry: `At(-100_cs)`, effectOffset 100 | L345-352 |
| `J(row,col)` | jalapeno: `At(-100_cs)`, effectOffset 100 | L357-364 |
| `W(row,col)` | squash: `At(-182_cs)`, effectOffset **182** (older 240205 named it `a(...)`, L267-268, also -182) | L369-376 |
| `N(row,col,tryImitator=false)`, `N(positions,bool)` | doom via `UseMushroom`: effect offset = `(tryImitator?320:0) + (night?100:299)` (L439-441). Day: plants shroom (imitater if usable, else waits 320 when tryImitator) then coffee bean with effectOffset 299; calls `ASetPlantActiveTime`. | L463-479 |
| `I(row,col,tryImitator=true)`, `I(if_=aIceFiller)` | ice via same UseMushroom; `I()` day-only, `CallIceFiller` effectOffset 299 | L444-455, L486-502 |
| `C(removalDelay[, rows|pos|positions])` | `AFodder` global instance `C` (L735). `C(134)` all LAND rows col 9, shovel after 134; `C(-1, 56)` no shovel; `C(1,{1256,8})`; `C(266,{{1,9},{256,8}})`; `C.TriggerBy(constraints...)(delay, ...)`; constraints `CURR_WAVE`, `PREV_WAVES`, `WaveIn(...)`, `AbscIn(l,r)`, zombie type, `&`. Seeds default: all 750-cd cards sorted by cost (L656-673). effectOffset 0 (L798-800). | L517-822 |
| `Trig(force=false)` | ignored if `wave%10==9` or wave==TotalWave; `time = max(time+200, 601)`; `force ? ASetWavelength : AAssumeWavelength` | L22-29 |
| `TrigAt(t)` | `#define TrigAt(...) (__VA_ARGS__) <<= Trig() & (__ADSLCastHelper)` — i.e. `At(t) Trig() & <following op>` | L31 |
| `Repeat(op, times, interval)` | `&=` of `op + i*interval` | L35-40 |
| `IfWave(waves|wave, then, else)` | L42-53 |
| `EnsureExist/EnsureAbsent(types, rows)`, `AAverageSpawn(types)` | zombie row manipulation | L872-956 |

Tutorial (dsl/2) example: `At(360_cs) P(2, 9) & A(5, 9) & (DD(7.8125) + 107_cs)`, `At(401_cs) Do { AMaidCheats::Dancing(); }`.

## Wave length bounds (`src/avz_time_queue.cpp` L160-166; `inc/avz_time_queue.h` L90; tutorial 09_time.md)
- last wave (totalWave, 20): `{500, 5999}`; `wave % 10 == 9`: `{1346, 5245}`; others `{601, 3100}`. Below min → Error & reject; above max → Warning only.
- Rationale (09_time.md): w9/19 refresh check starts 401cs after spawn, +945 → min 1346; upper 5245.

## Seml export (`inc/dsl/verifier.h`, registered via `AOnAfterScript` L148-152)
- `CheckWaveLength` (L5-11): warns for waves in `AWave(1_8, 10_18)` with `waveLength <= 0`.
- `CheckCobCD` (L13-38): warns if same-cob reuse interval < 3475 cs.
- `ExportSeml()` (L46-145): for wave 1..20 collects queued `AFunctors::Cob` (non-recover), `Card`, `UseMushroom` (ice → iceTimes; doom → op), `Fodder`, `CallIceFiller` (→ iceTimes), each stamped with `(time + op.GetEffectOffset()).time`. **Wave length**: `wave%10==9 → 5245`; `wave==20 → 5999`; else queue's waveLength if >0 else `9999` (L78-85). Writes `<script>.seml`:
  - `scene:{DE|NE|PE|FE|RE|ME}` by `Scene()` index; `protect:` = per row, max cob col (`p.Col()+2`) joined as `RC` tokens; `avzTime:true`; `repeat:1000`.
  - Per wave: `w{N} [ice times sorted...] {waveLength}`, then lines: Cob → `P {t} {row} {col}`; Card seed DOOM/CHERRY/JALAPENO/SQUASH → `N/A/J/W {t} {row} {col}` (first position only); UseMushroom doom → `N ...`; Fodder → grouped by col: `C {t}+{removalDelay} {rows digits} {col}`.
  - Known limits (comment L40-45): no blocking scripts, dynamic inserts, non-DSL ops; multi-position cards export first position. Note: exported `C {t}+{-1}` when removalDelay=-1 would be invalid seml (unverified whether handled).

## `ASetZombies(string)` char map (`src/avz_memory.cpp` L140-176, `AParseZombieTypeString`)
Separators ignored: `' ' ',' ';' '　' '，' '；'`. Unknown char → logger error.
普0 旗1 障2 杆3 桶4 报5 门6 橄7 舞8 潜11 车12 橇13 豚14 丑15 气16 矿17 跳18 雪19 偷20 梯21 篮22 白23 博25 豌26 坚27 辣28 枪29 窝30 高31 红32
(IDs from `enum AZombieType` avz_types.h L553-587: 0 AZOMBIE,1 FLAG,2 CONEHEAD,3 POLE_VAULTING,4 BUCKETHEAD,5 NEWSPAPER,6 SCREEN_DOOR,7 FOOTBALL,8 DANCING,9 BACKUP_DANCER,10 DUCKY_TUBE,11 SNORKEL,12 ZOMBONI,13 BOBSLED,14 DOLPHIN_RIDER,15 JACK_IN_THE_BOX,16 BALLOON,17 DIGGER,18 POGO,19 YETI,20 BUNGEE,21 LADDER,22 CATAPULT,23 GARGANTUAR,24 IMP,25 DR_ZOMBOSS,26 PEASHOOTER_Z,27 WALL_NUT_Z,28 JALAPENO_Z,29 GATLING_PEA_Z,30 SQUASH_Z,31 TALL_NUT_Z,32 GIGA_GARGANTUAR.) No chars for 伴(9), 鸭(10), 鬼(24). Tutorial usage: `ASetZombies("普杆舞车豚 丑气矿篮偷 红");` Signature: `ASetZombies(std::string_view, ASetZombieMode mode = AVERAGE)` (avz_memory.h L134).

## `ASelectCards(string, lst={}, selectInterval=17)` (shorthand L958-1013)
Separators same set. Map: A→ACHERRY_BOMB, B→ABLOVER, F→AFLOWER_POT, G→AGRAVE_BUSTER, I→AICE_SHROOM, J→AJALAPENO, K→ACOFFEE_BEAN, L→ALILY_PAD, M→APOTATO_MINE, N→ADOOM_SHROOM, P→APUMPKIN, T→ATALL_NUT, U→AUMBRELLA_LEAF, W→ASQUASH, `_`→ASPIKEWEED (unused letters noted: DEHOQRSVXYZ). Repeated letter already selected → `card += 49` (imitater variant `AM_*`). `C`/`c` → next unused fodder from `{APUFF_SHROOM, AFLOWER_POT, ASCAREDY_SHROOM, ASUN_SHROOM, ASUNFLOWER}` (max 5, error beyond). Extra ints from `lst` appended; then `ASelectCards(vector,int)`. Tutorial: `ASelectCards("IINAW LPccc");`.

## Unknowns
- Seml's `w` token branch (`symbol.startsWith("w")`) catches any line beginning with "w" — behaviour for lines like `waves:` outside a fodder line not tested. The `~` in wave header (`startTick~waveLength`) semantic (record window for explode/pogo/pos tests) is inferred from README comments; binary-side meaning not verified.
- Seml `报`/`news` → buckethead (0x4) rather than newspaper (0x5): present in source; whether intentional is unknown.
- The native test binaries (smash/explode/refresh/pogo/pos) are not in the repo clone inspected (only `out/` and `src/`); their JSON schema consumption is inferred solely from parser output types.
- AvZ squash effect offset: request mentioned "100" but current shorthand uses 182 for `W` (and older `a`); cherry/jalapeno are 100. Comment at shorthand L119 says "376" while code returns 378 for pool rows.
- `ExportSeml` with `removalDelay = -1` (no shovel) would emit `C t+-1 ...`, which seml would reject; not verified whether any guard exists.
- Seml README mentions wave numbers 1..99 accepted but AvZ export only covers 1..20.
- I did not fetch the forum post on zombie naming (forum.crescb.com/postid/5885) nor AvZ tutorial/dsl "1 时间轴是什么.md" in full.

## Sources
- Rottenham/seml (GitHub): https://github.com/Rottenham/seml
- seml parser.ts: https://raw.githubusercontent.com/Rottenham/seml/master/src/parser.ts
- seml zombie_types.ts: https://raw.githubusercontent.com/Rottenham/seml/master/src/zombie_types.ts
- seml templates.ts: https://raw.githubusercontent.com/Rottenham/seml/master/src/templates.ts
- vector-wlc/AsmVsZombies (GitHub): https://github.com/vector-wlc/AsmVsZombies
- AvZ inc/dsl/shorthand_240713.h: https://raw.githubusercontent.com/vector-wlc/AsmVsZombies/master/inc/dsl/shorthand_240713.h
- AvZ inc/dsl/main.h: https://raw.githubusercontent.com/vector-wlc/AsmVsZombies/master/inc/dsl/main.h
- AvZ inc/dsl/verifier.h: https://raw.githubusercontent.com/vector-wlc/AsmVsZombies/master/inc/dsl/verifier.h
- AvZ src/avz_memory.cpp (ASetZombies): https://raw.githubusercontent.com/vector-wlc/AsmVsZombies/master/src/avz_memory.cpp
- AvZ src/avz_time_queue.cpp (wavelength bounds): https://raw.githubusercontent.com/vector-wlc/AsmVsZombies/master/src/avz_time_queue.cpp
- AvZ tutorial dsl/2 构建时间轴.md: https://github.com/vector-wlc/AsmVsZombies/blob/master/tutorial/dsl/2%20构建时间轴.md
- AvZ tutorial dsl/3 连接时间轴.md: https://github.com/vector-wlc/AsmVsZombies/blob/master/tutorial/dsl/3%20连接时间轴.md
- AvZ tutorial 09_time.md: https://github.com/vector-wlc/AsmVsZombies/blob/master/tutorial/09_time.md