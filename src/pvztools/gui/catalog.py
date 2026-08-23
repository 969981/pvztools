"""阵型目录：把打包的 LonelyStar 全库（363 阵）整理成 GUI 用的结构。

每个阵按全局唯一 index 选择（放置时按编号解析，天然避开重名），
可按场景（PE/DE/NE/FE/RE/ME）筛选、按名字搜索。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

# 场景顺序与中文名（与 lineups_default.SCENES 一致）
SCENES: Tuple[str, ...] = ("PE", "DE", "NE", "FE", "RE", "ME")
SCENE_ZH = {
    "PE": "泳池", "DE": "黑夜", "NE": "月夜", "FE": "前院", "RE": "屋顶", "ME": "雾夜",
}


@dataclass(frozen=True)
class LineupItem:
    index: int
    scene: str
    name: str

    @property
    def label(self) -> str:
        return f"[{self.index}] {self.scene}-{self.name}"


def load_catalog() -> List[LineupItem]:
    """全部阵型，按 (场景顺序, index) 排序。库缺失时返回空表。"""
    try:
        from pvztools.data import load_lineups
    except Exception:  # noqa: BLE001
        return []
    order = {s: i for i, s in enumerate(SCENES)}
    items = [
        LineupItem(int(e["index"]), str(e.get("scene", "")), str(e.get("name", "")))
        for e in load_lineups()
        if e.get("index") is not None
    ]
    items.sort(key=lambda it: (order.get(it.scene, 99), it.index))
    return items


def filter_catalog(
    items: List[LineupItem],
    scene: Optional[str] = None,
    query: str = "",
) -> List[LineupItem]:
    q = query.strip().lower()
    out = []
    for it in items:
        if scene and it.scene != scene:
            continue
        if q and q not in it.name.lower() and q not in str(it.index):
            continue
        out.append(it)
    return out


# 自定义布阵调色板：(层, 植物id, 中文短名)
# 层：plant=主植物 / container=底座(睡莲/花盆) / shell=保护(南瓜)
PALETTE: Tuple[Tuple[str, int, str], ...] = (
    ("plant", 1, "向日葵"),
    ("plant", 41, "双子"),
    ("plant", 42, "忧郁菇"),
    ("plant", 10, "大喷菇"),
    ("plant", 44, "寒冰瓜"),
    ("plant", 39, "西瓜"),
    ("plant", 47, "玉米炮"),
    ("plant", 43, "猫尾草"),
    ("plant", 31, "磁力菇"),
    ("plant", 23, "高坚果"),
    ("plant", 46, "地刺王"),
    ("plant", 14, "寒冰菇"),
    ("plant", 37, "叶子伞"),
    ("plant", 36, "大蒜"),
    ("container", 16, "睡莲"),
    ("container", 33, "花盆"),
    ("shell", 30, "南瓜"),
)

# 植物 id -> 短名（格子上显示）
PLANT_SHORT = {pid: short for _layer, pid, short in PALETTE}


def scene_rows(scene: str) -> int:
    """该场景的行数（泳池 6，其余 5）。"""
    try:
        from pvztools.schema.enums import Scene

        return int(Scene.from_code(scene).rows)
    except Exception:  # noqa: BLE001
        return 6 if scene in ("PE", "NE") else 5
