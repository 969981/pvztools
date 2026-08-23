"""Place a whole :class:`~pvztools.schema.lineup.Lineup` in the game with one
remote call (Toolkit §3.14 "布置阵型", own implementation).

Order inside the single injection -- exactly the documented Toolkit order::

    containers (lily pad 16 / flower pot 33, incl. imitater)
    -> plants   (skips 16/33/30/35; wakes day-scene sleepers unless the cell
                 says ``asleep``; arms potato mine / sun-shroom)
    -> shells   (pumpkin 30)
    -> addons   (coffee bean 35)
    -> graves   (``props`` "tomb")
    -> ladders  (``props`` "ladder")

``clear=True`` first deletes every plant and every grave / crater / ladder /
rake grid item (a separate remote call, so the plant array is re-read
before placing).  The scene must match: a PE lineup on a DE board raises
:class:`SceneMismatch`; ``change_scene=True`` is not implemented (the
research notes describe ``pick_background`` but the block_type / row_type /
particle-system / music clean-up around it is only partially documented), so
it raises :class:`NotSupported`.

A garden rake (``rake_row``) is not placed (Toolkit needs a temporary code
patch dance for ``put_rake``; see conv notes) and is reported in the summary.

Deliberate deviations from the Toolkit flow (§3.14):

* **Game mode** -- Toolkit silently returns unless the mode is survival
  endless (11..15) or I, Zombie (61..70); pvztools places in *any* mode
  (adventure / mini-games included) as long as ``game_ui`` is 2 or 3.  The
  mode is recorded in ``PlaceSummary.game_mode``.
* **Validation** -- :meth:`Lineup.validate` flags cattail-on-water-without-
  lily-pad and roof-plant-without-pot as errors, but the ArrayDesign
  catalogue encodes 37 lineups that way (cattail sits on a lily in-game but
  is stored bare) and Toolkit plants whatever the code says.  By default
  (``strict=False``) those two diagnostics (``lineup.water`` / ``lineup.pot``)
  are reported in ``PlaceSummary.warnings`` and placement proceeds; range /
  cob-overlap / rake / plant-id errors always abort.  ``strict=True`` aborts
  on every error.
* **Failure ordering** -- every plant id and every function address the
  batch will need is checked *before* the field is cleared, so a build
  failure (missing VA, bad plant id) leaves the board untouched.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from pvztools.data import lineups_path
from pvztools.schema.enums import Scene
from pvztools.schema.lineup import Lineup, decode_lineup_code, parse_lineup_string

from .actions import ADDON, CONTAINERS, IZ_MODES, SHELL, Actions, ActionError, plant_label, resolve_plant
from .conv import MAY_SLEEP, NotSupported

__all__ = ["place_lineup", "place_named", "load_lineup", "PlaceSummary", "SceneMismatch", "DEFAULT_DB",
           "LENIENT_CODES"]

#: default catalogue = bundled package data (:mod:`pvztools.data`)
DEFAULT_DB = str(lineups_path())
_SKIP_AS_PLANT = set(CONTAINERS) | {SHELL, ADDON}
#: validate() error codes that only warn unless ``strict=True`` (see module doc)
LENIENT_CODES = frozenset({"lineup.water", "lineup.pot"})


class SceneMismatch(ActionError):
    def __init__(self, wanted: str, current: str) -> None:
        self.wanted, self.current = wanted, current
        super().__init__("阵型场景为 %s，当前关卡场景为 %s；请先进入对应场景的关卡"
                         "（自动换场景未实现）" % (wanted, current))


@dataclass
class PlaceSummary:
    """Counts of what was placed (plants by layer) and what was skipped."""
    scene: str = ""
    name: Optional[str] = None
    cleared_plants: int = 0
    cleared_items: int = 0
    containers: int = 0
    plants: int = 0
    shells: int = 0
    addons: int = 0
    graves: int = 0
    ladders: int = 0
    woken: int = 0
    skipped: List[str] = field(default_factory=list)
    #: lenient validate() diagnostics that did not stop placement
    warnings: List[str] = field(default_factory=list)
    calls: int = 0
    order: List[str] = field(default_factory=list)
    game_mode: Optional[int] = None

    @property
    def total(self) -> int:
        return self.containers + self.plants + self.shells + self.addons + self.graves + self.ladders

    def __str__(self) -> str:
        parts = ["场景 %s" % self.scene]
        if self.name:
            parts.insert(0, self.name)
        parts.append("容器 %d / 植物 %d / 南瓜 %d / 咖啡 %d / 墓碑 %d / 梯子 %d（共 %d，%d 次远程调用）"
                     % (self.containers, self.plants, self.shells, self.addons, self.graves,
                        self.ladders, self.total, self.calls))
        if self.cleared_plants or self.cleared_items:
            parts.append("先清除 植物 %d / 物件 %d" % (self.cleared_plants, self.cleared_items))
        if self.skipped:
            parts.append("跳过: " + "; ".join(self.skipped))
        if self.warnings:
            parts.append("警告: " + "; ".join(self.warnings))
        return "，".join(parts)


def _scene_code(scene_id: int) -> str:
    try:
        return Scene(scene_id).code
    except ValueError:
        return "?(%d)" % scene_id


def place_lineup(actions: Actions, lineup: Lineup, *, clear: bool = True,
                 change_scene: bool = False, strict: bool = False) -> PlaceSummary:
    """Place ``lineup`` on the current board.  See the module doc for the
    order, the gating and the deviations; returns a :class:`PlaceSummary`."""
    summary = PlaceSummary(scene=lineup.scene, name=lineup.name)
    errors = []
    for d in lineup.validate():
        if d.level != "error":
            continue
        if not strict and d.code in LENIENT_CODES:
            summary.warnings.append(d.message)
        else:
            errors.append(d)
    if errors:
        raise ActionError("阵型不合法：" + "; ".join(d.message for d in errors[:5]))
    actions.check("布阵 %s" % (lineup.name or lineup.scene))
    current = _scene_code(actions.scene())
    if current != lineup.scene:
        if change_scene:
            raise NotSupported("pick_background", str(actions.family),
                               "自动换场景需要 block_type/row_type/粒子/音乐 的完整重置流程，研究笔记不完整")
        raise SceneMismatch(lineup.scene, current)
    day = lineup.scene_id.is_day
    summary.game_mode = actions.game_mode()
    _precheck(actions, lineup, day, summary.game_mode)

    if clear:
        n = actions.clear_grid_items((1, 2, 3, 11))
        summary.cleared_items = n or 0
        n = actions.clear_plants()
        summary.cleared_plants = n or 0
        summary.calls += int(bool(summary.cleared_items)) + int(bool(summary.cleared_plants))

    if lineup.rake_row is not None:
        summary.skipped.append("钉耙（第 %d 行）未放置：put_rake 需要临时代码补丁，未实现" % lineup.rake_row)

    cells = lineup.cells
    with actions.batch("布阵 %s" % (lineup.name or lineup.scene)) as batch:
        for c in cells:
            if c.container is not None:
                actions.put_plant(c.row, c.col, c.container, imitater=c.container_imitater, wake=False)
                summary.containers += 1
                summary.order.append("container")
        for c in cells:
            if c.plant is None:
                continue
            if c.plant in _SKIP_AS_PLANT:
                summary.skipped.append("(%d,%d) %s 放在主植物层，已忽略" % (c.row, c.col, plant_label(c.plant)))
                continue
            wake = day and not c.asleep
            actions.put_plant(c.row, c.col, c.plant, imitater=c.imitater, wake=wake)
            summary.plants += 1
            summary.order.append("plant")
            if wake and c.plant in _may_sleep():
                summary.woken += 1
        for c in cells:
            if c.shell is not None:
                actions.put_plant(c.row, c.col, c.shell, imitater=c.shell_imitater, wake=False)
                summary.shells += 1
                summary.order.append("shell")
        for c in cells:
            if c.addon is not None:
                actions.put_plant(c.row, c.col, c.addon, imitater=c.addon_imitater, wake=False)
                summary.addons += 1
                summary.order.append("addon")
        for c in cells:
            if "tomb" in c.props:
                actions.put_grave(c.row, c.col)
                summary.graves += 1
                summary.order.append("grave")
        for c in cells:
            if "ladder" in c.props:
                actions.put_ladder(c.row, c.col)
                summary.ladders += 1
                summary.order.append("ladder")
    if not batch.empty:
        summary.calls += 1
    return summary


def _precheck(actions: Actions, lineup: Lineup, day: bool, mode: int) -> None:
    """Resolve every plant id and every function VA the batch needs, so a
    build failure raises *before* the field is cleared."""
    needed = set()
    imitater = False
    for c in lineup.cells:
        for pid, imi in ((c.container, c.container_imitater), (c.plant, c.imitater),
                         (c.shell, c.shell_imitater), (c.addon, c.addon_imitater)):
            if pid is None:
                continue
            resolve_plant(pid)
            needed.add("put_plant")
            imitater = imitater or bool(imi)
        if day and c.plant is not None and c.plant in MAY_SLEEP and not c.asleep:
            needed.add("set_plant_sleeping")
        if "tomb" in c.props:
            needed.add("put_grave")
        if "ladder" in c.props:
            needed.add("put_ladder")
    conv = actions.conv
    for name in sorted(needed):
        conv._addr(name)
    if imitater:
        conv._precheck_put_plant_imitater()
    if "put_plant" in needed and mode in IZ_MODES:
        conv._precheck_put_plant_iz_style()


def _may_sleep() -> Any:
    return MAY_SLEEP


# ---------------------------------------------------------------------------
# catalogue / free-form lookup
# ---------------------------------------------------------------------------

def _load_db(path: Optional[str]) -> List[Dict[str, Any]]:
    path = path or DEFAULT_DB
    if not os.path.isfile(path):
        raise FileNotFoundError("阵型库不存在：%s（用 db_path 指定）" % path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data["lineups"] if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("%s: 期望 'lineups' 列表" % path)
    return entries


def _find(entries: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    if key.isdigit():
        idx = int(key)
        hit = [e for e in entries if e.get("index") == idx]
        return hit or ([entries[idx]] if idx < len(entries) else [])
    exact = [e for e in entries if e.get("name") == key]
    if exact:
        return exact
    low = key.lower()
    return [e for e in entries if low in str(e.get("name", "")).lower()]


def load_lineup(name_or_index: Union[str, int, Lineup], db_path: Optional[str] = None) -> Lineup:
    """Resolve a Lineup from: a :class:`Lineup`, a lineup *code* (base64),
    a lineup *string* (``"0,29 1 2 ..."``), or a catalogue name / index."""
    if isinstance(name_or_index, Lineup):
        return name_or_index
    key = str(name_or_index).strip()
    if not key:
        raise ActionError("阵型名 / 编号 / 代码不能为空")
    # free-form lineup string ("<map>,<hex> r c ...")
    if "," in key and " " in key:
        lu = parse_lineup_string(key)
        return lu
    # lineup code (base64 / base64url, no spaces, not a pure number)
    decode_error: Optional[str] = None
    if not key.isdigit() and " " not in key and len(key) >= 18 and all(
            ch.isalnum() or ch in "+/=-_" for ch in key):
        try:
            return decode_lineup_code(key)
        except Exception as e:  # fall back to the catalogue, but keep the reason
            decode_error = "%s: %s" % (type(e).__name__, e)
    try:
        entries = _load_db(db_path)
    except FileNotFoundError as e:
        if decode_error:
            raise LookupError("阵型代码无法解码（%s），也无法在阵型库中查找（%s）" % (decode_error, e))
        raise
    found = _find(entries, key)
    if not found:
        if decode_error:
            raise LookupError("阵型代码无法解码（%s），也不是阵型库中的名字：%r" % (decode_error, key))
        raise LookupError("阵型库中找不到 %r" % key)
    if len(found) > 1:
        names = ", ".join("[%s] %s %s" % (e.get("index"), e.get("scene"), e.get("name")) for e in found[:8])
        raise LookupError("%r 匹配多个阵型，请用编号：%s" % (key, names))
    e = found[0]
    lu = parse_lineup_string(e["raw"])
    return Lineup(scene=lu.scene, cells=lu.cells, rake_row=lu.rake_row,
                  name=e.get("name"), source="arraydesign[%s]" % e.get("index"), meta=dict(lu.meta))


def place_named(actions: Actions, name_or_index: Union[str, int, Lineup], db_path: Optional[str] = None,
                *, clear: bool = True, change_scene: bool = False, strict: bool = False) -> PlaceSummary:
    """``place_lineup(actions, load_lineup(name_or_index, db_path))``."""
    return place_lineup(actions, load_lineup(name_or_index, db_path), clear=clear,
                        change_scene=change_scene, strict=strict)
