# pvztools 阵型格式规范（v0）

`pvztools.schema.lineup` 的统一模型与各编码的对应关系。

## 1. 统一模型

```python
@dataclass(frozen=True)
class Cell:
    row: int                   # 1..6
    col: int                   # 1..9
    plant: Optional[int]       # PlantType id (0..47)，本体；48（模仿者）不是可放置的植物
    container: Optional[int]   # 16 荷叶 / 33 花盆
    shell: Optional[int]       # 30 南瓜
    addon: Optional[int]       # 35 咖啡豆
    props: Tuple[str, ...]     # "ladder" "tomb"（"rake" 仅解析时接受，归入 Lineup.rake_row）
    imitater: bool = False             # 本体为模仿者
    container_imitater: bool = False   # 容器为模仿者（模仿荷叶/花盆）
    shell_imitater: bool = False       # 南瓜为模仿者
    addon_imitater: bool = False       # 咖啡豆为模仿者
    asleep: bool = False               # 白天场景的睡眠蘑菇（布阵时不唤醒）
    tomb_variant: int = 0              # 墓碑外观 0..4（阵型串字段 F4）

@dataclass(frozen=True)
class Lineup:
    scene: str                 # DE NE PE FE RE ME
    cells: Tuple[Cell, ...]    # 每格最多一条，按 (row, col) 排序
    rake_row: Optional[int]    # 钉耙所在行（None = 无）
    name: Optional[str] = None
    source: Optional[str] = None
    meta: dict
```

`props` 只接受 `PROP_NAMES = ("ladder", "rake", "tomb")`；弹坑等其它场地物件不在模型内（`Cell.from_dict` 对未知 prop 抛 `ValueError`）。

规则（`Lineup.validate()` 的诊断码）：
- 玉米炮占 **2 格**，只在左格记录 `plant=47`，右格 `plant=None` 但视为被占（`Lineup.occupied(row, col)`）；右格若有植物/南瓜为 `lineup.cob-overlap`；泳池炮两格都需 `container=16`（`lineup.water`）。
- 各层 id 必须在 0..47（`lineup.plant`）。
- 泳池/浓雾 3、4 路为水路，非炮植物必须有 `container=16`（`lineup.water`）；屋顶/月夜所有格必须 `container=33`（炮除外，`lineup.pot`）。
  **布阵时**（`control.lineup_place`）这两条默认只作警告：阵型库里 37 个阵型把香蒲直接写在水路、屋顶阳光菇不带花盆，Toolkit 照放；`strict=True` 才视为错误。
- 同一格层次：container → plant → shell → addon。

## 2. 编码

### 2.1 ArrayDesign 文本（lonelystar.org 网页布阵器，已解码，见 `docs/research/arraydesign-lineups.json`）

```
<map_index>,<id> <row> <col> <spriteRow> <spriteCol> <gray> <opacity> [<flip>] [<x> <y>],...
```
- `id` 为十六进制字符串；`0x00–0x2F` 与游戏 PlantType 完全一致；`0x30` 梯子、`0x31` 钉耙、`0x32` 墓碑、`0x33` 条纹、`0x34` 弹坑、`0x35` 花瓶、`0x36` 雪花、`0x37` 蹦极靶、`0x38` 冰道；`0x50+` 僵尸。
- `map_index` 0 白天泳池(PE) / 1 夜晚泳池(FE) / 2 白天草地(DE) / 3 夜晚草地(NE) / 4 白天屋顶(RE) / 5 夜晚屋顶(ME)；6+ 为特殊地图。
- `spriteRow/spriteCol` 仅是外观选择，`gray/opacity/flip` 是绘图属性；导入时全部忽略，导出时填默认值。
- 物品类（梯子等）多出 `x y` 像素坐标，导入时按所在格归位。
- 数据集许可：站点注明非营利使用，**不打入 wheel**，仅作研究语料与测试样本。

### 2.2 pvz.tools / PvZ Toolkit 阵型码（base64）

【待研究工作流返回后补充：头部、场景字节、每格位域、校验】

### 2.3 本库 JSON

`Lineup.to_dict()` 直出；字段名即上面的模型。

## 3. API

```python
from pvztools.schema.lineup import Lineup, from_arraydesign, to_arraydesign
lu = from_arraydesign("0,2F 1 5 0 0 0,...")
lu.cobs()           # -> [(row, col), ...]
lu.plants_of(42)    # 曾哥位置
lu.validate()       # -> list[Diagnostic]（水路荷叶、屋顶花盆、炮占位重叠、越界）
```
