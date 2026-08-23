"""Unified lineup (阵型) model — docs/lineup-format.md §1.

A :class:`Lineup` is a scene code plus one :class:`Cell` per occupied grid
cell. A cob cannon (PlantType 47) is stored on its *left* cell only; the
right cell is reported as occupied by :meth:`Lineup.occupied` but carries no
``Cell`` of its own unless it holds a container (e.g. the second lily pad
under a pool cob).

Layer order inside one cell: ``container`` (lily pad 16 / flower pot 33) →
``plant`` → ``shell`` (pumpkin 30) → ``addon`` (coffee bean 35). ``props``
holds non-plant items: ``"ladder"`` and ``"tomb"`` (a gravestone occupies the
container layer in the binary code, hence ``tomb_variant`` for its look).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ..enums import PlantType, Scene

if TYPE_CHECKING:  # pragma: no cover
    from ..track.ast import Diagnostic


def _diagnostic_cls():  # type: ignore[no-untyped-def]
    """Lazy: ``schema.track`` (via ``track.lint``) imports ``schema.lineup``,
    so a module-level import here is a cycle when ``schema.lineup`` is
    imported first (tests/test_lineup.py run alone)."""
    from ..track.ast import Diagnostic
    return Diagnostic

__all__ = [
    "Cell",
    "Lineup",
    "COLS",
    "PROP_NAMES",
    "MAY_SLEEP",
    "is_day_scene",
]

COLS = 9
PROP_NAMES = ("ladder", "rake", "tomb")

# Seed types that sleep in day scenes unless woken by a coffee bean
# (pvztoolkit lineup.cpp may_sleep table): Puff, Sun, Fume, Hypno, Scaredy,
# Ice, Doom, Sea, Magnet, Gloom.
MAY_SLEEP = frozenset((8, 9, 10, 12, 13, 14, 15, 24, 31, 42))

_COB = int(PlantType.COB_CANNON)
_LILY = int(PlantType.LILY_PAD)
_POT = int(PlantType.FLOWER_POT)
_MAX_PLANT = int(PlantType.COB_CANNON)  # 48 (imitater) is never a plant on the board


def is_day_scene(scene: str) -> bool:
    """True for DE / PE / RE (mushrooms sleep without coffee)."""
    return Scene.from_code(scene).is_day


@dataclass(frozen=True)
class Cell:
    """One grid cell (docs/lineup-format.md §1). Ids are ``PlantType`` ints."""

    row: int  # 1..6
    col: int  # 1..9
    plant: Optional[int] = None
    container: Optional[int] = None  # 16 lily pad / 33 flower pot
    shell: Optional[int] = None  # 30 pumpkin
    addon: Optional[int] = None  # 35 coffee bean
    props: Tuple[str, ...] = ()  # "ladder" "tomb" ("rake" accepted, see Lineup.rake_row)
    imitater: bool = False
    container_imitater: bool = False
    shell_imitater: bool = False
    addon_imitater: bool = False
    asleep: bool = False  # sleeping mushroom (day scene, no coffee)
    tomb_variant: int = 0  # gravestone look 0..4 (lineup-string field F4)

    @property
    def is_cob(self) -> bool:
        return self.plant == _COB

    @property
    def is_empty(self) -> bool:
        return (
            self.plant is None
            and self.container is None
            and self.shell is None
            and self.addon is None
            and not self.props
        )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"row": self.row, "col": self.col}
        for name in ("plant", "container", "shell", "addon"):
            v = getattr(self, name)
            if v is not None:
                d[name] = int(v)
        if self.props:
            d["props"] = list(self.props)
        for name in (
            "imitater",
            "container_imitater",
            "shell_imitater",
            "addon_imitater",
            "asleep",
        ):
            if getattr(self, name):
                d[name] = True
        if self.tomb_variant:
            d["tomb_variant"] = self.tomb_variant
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Cell":
        props = tuple(str(p) for p in d.get("props", ()))
        for p in props:
            if p not in PROP_NAMES:
                raise ValueError(f"unknown cell prop {p!r} at ({d.get('row')},{d.get('col')})")
        return cls(
            row=int(d["row"]),
            col=int(d["col"]),
            plant=_opt_int(d.get("plant")),
            container=_opt_int(d.get("container")),
            shell=_opt_int(d.get("shell")),
            addon=_opt_int(d.get("addon")),
            props=props,
            imitater=bool(d.get("imitater", False)),
            container_imitater=bool(d.get("container_imitater", False)),
            shell_imitater=bool(d.get("shell_imitater", False)),
            addon_imitater=bool(d.get("addon_imitater", False)),
            asleep=bool(d.get("asleep", False)),
            tomb_variant=int(d.get("tomb_variant", 0)),
        )


def _opt_int(v: Any) -> Optional[int]:
    return None if v is None else int(v)


@dataclass(frozen=True)
class Lineup:
    """A whole-field lineup (docs/lineup-format.md §1).

    ``cells`` is kept sorted by ``(row, col)`` with at most one entry per
    cell; ``rake_row`` is the row of the garden rake (``None`` = no rake).
    """

    scene: str  # DE NE PE FE RE ME
    cells: Tuple[Cell, ...] = ()
    rake_row: Optional[int] = None
    name: Optional[str] = None
    source: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        scene = Scene.from_code(self.scene).code
        cells = tuple(sorted(self.cells, key=lambda c: (c.row, c.col)))
        seen = set()
        for c in cells:
            key = (c.row, c.col)
            if key in seen:
                raise ValueError(f"duplicate cell at ({c.row},{c.col})")
            seen.add(key)
        object.__setattr__(self, "scene", scene)
        object.__setattr__(self, "cells", cells)

    # --- queries -----------------------------------------------------------
    @property
    def scene_id(self) -> Scene:
        return Scene.from_code(self.scene)

    @property
    def rows(self) -> int:
        return self.scene_id.rows

    def cell(self, row: int, col: int) -> Optional[Cell]:
        """The ``Cell`` stored at ``(row, col)`` or ``None``."""
        for c in self.cells:
            if c.row == row and c.col == col:
                return c
        return None

    def occupied(self, row: int, col: int) -> bool:
        """True if something sits on the cell, counting a cob's right half."""
        c = self.cell(row, col)
        if c is not None and not c.is_empty:
            return True
        left = self.cell(row, col - 1)
        return left is not None and left.is_cob

    def cobs(self) -> List[Tuple[int, int]]:
        """``[(row, left_col), ...]`` of every cob cannon."""
        return [(c.row, c.col) for c in self.cells if c.is_cob]

    def plants_of(self, pid: int) -> List[Tuple[int, int]]:
        """Positions where ``pid`` appears in any layer (container/plant/shell/addon)."""
        pid = int(pid)
        return [
            (c.row, c.col)
            for c in self.cells
            if pid in (c.plant, c.container, c.shell, c.addon)
        ]

    # --- validation --------------------------------------------------------
    def validate(self) -> List[Diagnostic]:
        """Static checks from docs/lineup-format.md §1 / §3.

        Errors: out-of-range cells, cob overlap, water row without lily pad
        (non-cob plants, PE/FE), roof/moon plant without flower pot (non-cob),
        rake on a water row or outside the field.
        Warnings: doom-shroom on roof scenes.
        """
        Diagnostic = _diagnostic_cls()
        out: List[Diagnostic] = []
        scene = self.scene_id
        rows = scene.rows
        water = scene.water_rows

        def err(code: str, msg: str) -> None:
            out.append(Diagnostic("error", code, msg))

        for c in self.cells:
            pos = f"({c.row},{c.col})"
            if not (1 <= c.row <= rows) or not (1 <= c.col <= COLS):
                err("lineup.range", f"cell {pos} outside {rows}x{COLS} field of {self.scene}")
                continue
            for layer, pid in (("plant", c.plant), ("container", c.container),
                               ("shell", c.shell), ("addon", c.addon)):
                if pid is not None and not (0 <= int(pid) <= _MAX_PLANT):
                    err("lineup.plant", f"{layer} {pid} at {pos} is not a plant id (0..{_MAX_PLANT})")
            if c.is_cob:
                if c.col >= COLS:
                    err("lineup.range", f"cob at {pos} needs column {c.col + 1} (field has {COLS})")
                else:
                    right = self.cell(c.row, c.col + 1)
                    if right is not None and (right.plant is not None or right.shell is not None):
                        err("lineup.cob-overlap", f"cob at {pos} overlaps plant at ({c.row},{c.col + 1})")
                    if c.row in water:
                        # a pool cob needs a lily pad under both halves
                        if c.container != _LILY or right is None or right.container != _LILY:
                            err("lineup.water", f"cob at {pos} on water row without lily pads under both cells")
                continue
            if c.plant is None:
                continue
            if c.row in water and c.container != _LILY:
                err("lineup.water", f"plant {c.plant} at {pos} on water row without lily pad")
            if scene.needs_pot and c.container != _POT:
                err("lineup.pot", f"plant {c.plant} at {pos} on {self.scene} without flower pot")
            if scene.needs_pot and c.plant == int(PlantType.DOOM_SHROOM):
                out.append(
                    Diagnostic("warning", "lineup.doom-roof", f"doom-shroom at {pos} on roof scene {self.scene}")
                )
        if self.rake_row is not None:
            if not (1 <= self.rake_row <= rows):
                err("lineup.rake", f"rake row {self.rake_row} outside 1..{rows}")
            elif self.rake_row in water:
                err("lineup.rake", f"rake cannot be on water row {self.rake_row}")
        return out

    # --- serialisation (docs/lineup-format.md §2.3) ------------------------
    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "scene": self.scene,
            "cells": [c.to_dict() for c in self.cells],
            "rake_row": self.rake_row,
            "name": self.name,
            "source": self.source,
            "meta": dict(self.meta),
        }
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Lineup":
        return cls(
            scene=str(d["scene"]),
            cells=tuple(Cell.from_dict(c) for c in d.get("cells", ())),
            rake_row=_opt_int(d.get("rake_row")),
            name=d.get("name"),
            source=d.get("source"),
            meta=dict(d.get("meta", {})),
        )

    def with_cells(self, cells: Tuple[Cell, ...]) -> "Lineup":
        """Copy with a different cell tuple (sorted/validated by ``__post_init__``)."""
        return replace(self, cells=cells)
