"""1437 格式：社区常用的人类可读阵型文本，可读可写。

约定（源自玩家分享习惯，见吧友"完美阵型"贴）：

* 每行以行号 ``1``–``6`` 开头，对应六条行。
* 一行内按 **空格 / 逗号（, ，）/ 顿号（、）** 分隔各列植物。
* ``（X）`` 或 ``(X)``：该格植物 **套南瓜**。
* 行尾 ``。。。。`` / ``...`` / ``…``（省略号）表示 **如前**——
  剩余列全部重复该行最后一个已写的格子，直到填满。
* 植物用中文名 / 昵称（``地刺王``、``冰西瓜``、``五角星``、``忧郁`` …），
  由 :mod:`pvztools.names` 解析。
* 泳池水路（第 3、4 行）上的陆生植物会 **自动补睡莲底座**。

``parse_1437(text)`` → :class:`~pvztools.schema.lineup.model.Lineup`；
``to_1437(lineup)`` → 文本。
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..enums import PlantType, Scene
from .model import Cell, Lineup

__all__ = ["parse_1437", "to_1437", "Human1437Error"]

_PUMPKIN = int(PlantType.PUMPKIN)
_LILY = int(PlantType.LILY_PAD)
_POT = int(PlantType.FLOWER_POT)
#: 水生植物（在水路无需睡莲）
_AQUATIC = frozenset(int(p) for p in (
    PlantType.LILY_PAD, PlantType.CATTAIL, PlantType.TANGLE_KELP,
    PlantType.SEA_SHROOM,
))
#: 各场景的水路行（1-indexed）。泳池 / 雾夜第 3、4 行是水。
_WATER_ROWS = {"PE": (3, 4), "ME": (3, 4)}

_ELLIPSIS = re.compile(r"(?:。\s*){2,}|\.{2,}|…+")
_SEP = re.compile(r"[\s,，、]+")


class Human1437Error(ValueError):
    """1437 文本无法解析。"""


def _strip_row_prefix(line: str) -> Optional[Tuple[int, str]]:
    """行首的 ``1``–``6`` 行号 + 其余内容；非阵型行返回 None。"""
    m = re.match(r"^\s*([1-6])\s*[.、,，:：)）]?\s*(.*)$", line)
    if not m:
        return None
    return int(m.group(1)), m.group(2).strip()


def _tokenize_row(body: str) -> Tuple[List[Tuple[str, bool]], bool]:
    """把一行拆成 [(植物名, 是否套南瓜), ...] 和 是否以省略号结尾（如前）。"""
    repeat = bool(_ELLIPSIS.search(body))
    body = _ELLIPSIS.sub(" ", body)
    tokens: List[Tuple[str, bool]] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch in "（(":
            close = body.find("）", i)
            if close == -1:
                close = body.find(")", i)
            if close == -1:
                name = body[i + 1:].strip()
                i = n
            else:
                name = body[i + 1:close].strip()
                i = close + 1
            if name:
                tokens.append((name, True))
        elif _SEP.match(ch):
            i += 1
        else:
            m = _SEP.search(body, i)
            end = m.start() if m else n
            # 括号也是分隔
            for br in "（(":
                b = body.find(br, i, end)
                if b != -1:
                    end = min(end, b)
            name = body[i:end].strip()
            if name:
                tokens.append((name, False))
            i = end
    return tokens, repeat


def parse_1437(text: str, scene: str = "PE", *, name: Optional[str] = None,
               auto_lily: bool = True, cols: int = 9) -> Lineup:
    """解析 1437 文本为 Lineup。``scene`` 决定水路补睡莲。"""
    from ...names import resolve_plant  # 延迟导入避免环

    scene_code = Scene.from_code(scene).code
    water = set(_WATER_ROWS.get(scene_code, ()))
    cells: List[Cell] = []
    any_row = False
    for raw in text.splitlines():
        parsed = _strip_row_prefix(raw)
        if parsed is None:
            continue
        row, body = parsed
        if not body:
            continue
        any_row = True
        tokens, repeat = _tokenize_row(body)
        if not tokens:
            continue
        if repeat and len(tokens) < cols:
            tokens = tokens + [tokens[-1]] * (cols - len(tokens))
        for col, (nm, pumpkin) in enumerate(tokens[:cols], start=1):
            try:
                pt = int(resolve_plant(nm))
            except Exception as exc:  # noqa: BLE001
                raise Human1437Error(
                    "第 %d 行第 %d 列 '%s' 无法识别：%s" % (row, col, nm, exc)
                ) from exc
            container = None
            if auto_lily and row in water and pt not in _AQUATIC:
                container = _LILY
            cells.append(Cell(row=row, col=col, plant=pt,
                              shell=_PUMPKIN if pumpkin else None,
                              container=container))
    if not any_row:
        raise Human1437Error("没有找到任何以 1–6 行号开头的阵型行")
    return Lineup(scene=scene_code, cells=tuple(cells), name=name, source="1437")


def to_1437(lineup: Lineup, *, lang: str = "zh", collapse: bool = True) -> str:
    """把 Lineup 序列化为 1437 文本。``collapse`` 时把行尾连续重复写成省略号。"""
    from ...names import plant_name

    rows = lineup.rows
    by_row = {r: {} for r in range(1, rows + 1)}
    for c in lineup.cells:
        if c.plant is not None:
            by_row.setdefault(c.row, {})[c.col] = c
    lines: List[str] = []
    for r in range(1, rows + 1):
        cols = by_row.get(r) or {}
        if not cols:
            continue
        maxc = max(cols)
        parts: List[str] = []
        for col in range(1, maxc + 1):
            cell = cols.get(col)
            if cell is None or cell.plant is None:
                parts.append("_")
                continue
            nm = plant_name(int(cell.plant), lang)
            parts.append(f"（{nm}）" if cell.shell == _PUMPKIN else nm)
        if collapse and len(parts) >= 2:
            last = parts[-1]
            k = len(parts)
            while k > 1 and parts[k - 1] == last:
                k -= 1
            if maxc - k >= 1:  # 至少省略两格才用省略号
                parts = parts[:k + 1] + ["。。。。"]
        lines.append(f"{r} " + " ".join(parts))
    return "\n".join(lines)
