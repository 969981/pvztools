"""Unified track AST (see docs/track-format.md §5).

Everything here is a frozen dataclass: parsers build it, lint/expand/export
consume it. No Win32, no game-version knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Tuple

__all__ = [
    "Target",
    "Op",
    "HalfWave",
    "Wave",
    "RhythmName",
    "Track",
    "Diagnostic",
    "KIND_COB",
    "KIND_ASH",
    "KIND_DOOM",
    "KIND_ICE",
    "KIND_FODDER",
    "KIND_NATURAL",
    "PHASE_PRE",
    "PHASE_POST",
]

KIND_COB = "cob"
KIND_ASH = "ash"
KIND_DOOM = "doom"
KIND_ICE = "ice"
KIND_FODDER = "fodder"
KIND_NATURAL = "natural"

PHASE_PRE = "pre"  # effective before the refresh-triggering operation ('-' left side)
PHASE_POST = "post"  # effective at/after the refresh trigger ('-' right side, or no '-')


@dataclass(frozen=True)
class Target:
    """Landing position. ``rows`` is 1-based; ``col`` is 1-based and may be
    fractional in 1/80-grid steps (8.75, 8.1875)."""

    rows: Tuple[int, ...]
    col: float


@dataclass(frozen=True)
class Op:
    symbol: str  # raw symbol as written: "PP", "I3", "A'", "p"
    kind: str  # KIND_*
    variant: Optional[str] = None  # replant sync before before_back delay delay_back jalapeno squash light imitater ice1..ice4 hard soft
    count: int = 1  # PP -> 2, DD -> 2
    al: int = 0  # 运算量 for the whole op (PP -> 2)
    phase: str = PHASE_POST
    effect_cs: Optional[int] = None  # effect-axis time; None in shorthand
    window_cs: Optional[Tuple[int, int]] = None  # "539~619"
    relative: bool = False  # effect_cs is "+N" relative to previous op
    target: Optional[Target] = None
    simultaneous_with_prev: bool = False  # joined with '&'


@dataclass(frozen=True)
class HalfWave:
    ops: Tuple[Op, ...] = ()


@dataclass(frozen=True)
class Wave:
    selector: Tuple[int, ...]  # shorthand: 1..n position in cycle; pvzt: actual wave numbers
    top: HalfWave = HalfWave()
    bottom: Optional[HalfWave] = None  # after '/'; None == symmetric
    mirrored: bool = False  # simplified notation: cob/ash ops are mirrored to the other half
    length_cs: Optional[int] = None
    length_range: Optional[Tuple[int, int]] = None
    raw: str = ""


@dataclass(frozen=True)
class RhythmName:
    family: str  # "P" "C" "S" "ch" "Cu" "I" or "?" when unknown
    param: Optional[int]
    prefix: str = ""  # 邻 / 间 / 对 / 精准 / 双冰 ...
    suffixes: Tuple[str, ...] = ()  # "u" "i" "*" "-N" "-2I" "-io" ...
    raw: str = ""


@dataclass(frozen=True)
class Track:
    waves: Tuple[Wave, ...]
    rhythm: Optional[RhythmName] = None
    scene: Optional[str] = None  # DE NE PE FE RE ME
    dialect: str = "duben"
    timing_base: str = "effect"  # "effect" | "operation"
    cards: Optional[str] = None  # AvZ single-letter card string
    zombies: Optional[str] = None  # AvZ single-char zombie string
    protect: Tuple[int, ...] = ()
    lineup_code: Optional[str] = None
    kind: str = "shorthand"  # "shorthand" | "pvzt"
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-compatible dict (§5); identical to :func:`jsonio.to_dict`, so
        the result is accepted by :func:`jsonio.from_dict`."""
        from .jsonio import to_dict  # local import: jsonio depends on this module

        return to_dict(self)

    def normalize(self) -> "Track":
        """Return the 复写 form of the track (§5 ``track.normalize()``).

        * every ``mirrored`` 简写 wave (§3.1 rule 1) gets its cob / ash ops
          expanded to both symmetric halves: the unit count (and al) is
          doubled and single-cannon symbols ``P S B D`` become ``PP SS BB DD``;
          ``mirrored`` is cleared;
        * lengths are already stored in cs, so they are returned unchanged.

        Everything else (timings, targets, meta) is preserved.
        """
        waves = []
        for wave in self.waves:
            if not wave.mirrored:
                waves.append(wave)
                continue
            ops = tuple(_mirror_op(op) for op in wave.top.ops)
            waves.append(replace(wave, top=HalfWave(ops), mirrored=False))
        return replace(self, waves=tuple(waves))


_DOUBLED_SYMBOL = {"P": "PP", "S": "SS", "B": "BB", "D": "DD"}


def _mirror_op(op: Op) -> Op:
    """Expand one op of a mirrored wave to both halves (cob / ash only)."""
    if op.kind not in (KIND_COB, KIND_ASH):
        return op
    return replace(
        op,
        symbol=_DOUBLED_SYMBOL.get(op.symbol, op.symbol),
        count=op.count * 2,
        al=op.al * 2,
    )


@dataclass(frozen=True)
class Diagnostic:
    level: str  # "error" | "warning" | "info"
    code: str  # see docs/track-format.md §6
    message: str
    wave: Optional[int] = None
    op: Optional[str] = None

    def __str__(self) -> str:
        where = ""
        if self.wave is not None:
            where = f" [w{self.wave}{(' ' + self.op) if self.op else ''}]"
        return f"{self.level}:{self.code}{where}: {self.message}"
