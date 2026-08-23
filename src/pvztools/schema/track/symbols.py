"""Operation symbol table shared by the shorthand and .pvzt parsers.

See docs/track-format.md §2. A symbol token is one of:
    P p P' PP S B b D d DD BB A A' a J W N N' I I' I1..I4 C c IO
Dialects only change how ambiguous letters are read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .ast import (
    KIND_ASH,
    KIND_COB,
    KIND_DOOM,
    KIND_FODDER,
    KIND_ICE,
    KIND_NATURAL,
)

__all__ = [
    "DIALECTS",
    "DEFAULT_DIALECT",
    "SymbolInfo",
    "lookup",
    "tokenize_ops",
    "SYMBOL_RE",
]

DIALECTS = ("duben", "baicai", "be2sio4")
DEFAULT_DIALECT = "duben"


@dataclass(frozen=True)
class SymbolInfo:
    kind: str
    variant: Optional[str]
    count: int  # number of cobs / cards this token stands for
    al_each: int  # 运算量 per unit


# base table, dialect-independent entries
_BASE: Dict[str, SymbolInfo] = {
    "P": SymbolInfo(KIND_COB, None, 1, 1),
    "PP": SymbolInfo(KIND_COB, None, 2, 1),
    "p": SymbolInfo(KIND_COB, "replant", 1, 1),
    "P'": SymbolInfo(KIND_COB, "replant", 1, 1),
    "S": SymbolInfo(KIND_COB, "sync", 1, 1),
    "SS": SymbolInfo(KIND_COB, "sync", 2, 1),
    "B": SymbolInfo(KIND_COB, "before", 1, 1),
    "BB": SymbolInfo(KIND_COB, "before", 2, 1),
    "b": SymbolInfo(KIND_COB, "before_back", 1, 1),
    "D": SymbolInfo(KIND_COB, "delay", 1, 1),
    "DD": SymbolInfo(KIND_COB, "delay", 2, 1),
    "d": SymbolInfo(KIND_COB, "delay_back", 1, 1),
    "A": SymbolInfo(KIND_ASH, "cherry", 1, 1),
    "A'": SymbolInfo(KIND_ASH, "jalapeno", 1, 1),
    "J": SymbolInfo(KIND_ASH, "jalapeno", 1, 1),
    "W": SymbolInfo(KIND_ASH, "squash", 1, 1),
    "N": SymbolInfo(KIND_DOOM, None, 1, 2),
    "N'": SymbolInfo(KIND_DOOM, "imitater", 1, 2),
    "I": SymbolInfo(KIND_ICE, None, 1, 0),
    "I'": SymbolInfo(KIND_ICE, "imitater", 1, 0),
    "I1": SymbolInfo(KIND_ICE, "ice1", 1, 0),
    "I2": SymbolInfo(KIND_ICE, "ice2", 1, 0),
    "I3": SymbolInfo(KIND_ICE, "ice3", 1, 0),
    "I4": SymbolInfo(KIND_ICE, "ice4", 1, 0),
    "IO": SymbolInfo(KIND_NATURAL, None, 1, 1),
}

# dialect-specific readings of 'a' and 'C' / 'c'
_DIALECT: Dict[str, Dict[str, SymbolInfo]] = {
    "duben": {
        "a": SymbolInfo(KIND_ASH, "squash", 1, 1),
        "C": SymbolInfo(KIND_FODDER, "hard", 1, 0),
        "c": SymbolInfo(KIND_FODDER, "soft", 1, 0),
    },
    "baicai": {
        "a": SymbolInfo(KIND_ASH, "squash", 1, 1),
        "C": SymbolInfo(KIND_FODDER, "soft", 1, 0),
        "c": SymbolInfo(KIND_FODDER, "soft", 1, 0),
    },
    "be2sio4": {
        "a": SymbolInfo(KIND_ASH, "light", 1, 1),  # 辣椒/窝瓜/土豆雷
        "C": SymbolInfo(KIND_FODDER, "soft", 1, 0),
        "c": SymbolInfo(KIND_FODDER, "soft", 1, 0),
    },
}

# Longest-match-first token regex. Order matters: PP before P, I3 before I, A' before A.
SYMBOL_RE = re.compile(
    r"IO|PP|SS|BB|DD|P'|N'|I'|A'|I[1-4]|[PpSBbDdAaJWNICc]"
)


def lookup(symbol: str, dialect: str = DEFAULT_DIALECT) -> SymbolInfo:
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}; expected one of {DIALECTS}")
    info = _DIALECT[dialect].get(symbol) or _BASE.get(symbol)
    if info is None:
        raise ValueError(f"unknown operation symbol {symbol!r}")
    return info


def tokenize_ops(text: str, dialect: str = DEFAULT_DIALECT) -> Tuple[Tuple[str, SymbolInfo], ...]:
    """Split an operation string like ``"IP-PP"`` *without* logic symbols
    (caller splits on ``| - /`` first) into ``((symbol, info), ...)``.

    Raises ValueError on any character that is not a known symbol.
    """
    out = []
    pos = 0
    s = text.strip()
    while pos < len(s):
        if s[pos].isspace():
            pos += 1
            continue
        m = SYMBOL_RE.match(s, pos)
        if not m:
            raise ValueError(f"unexpected character {s[pos]!r} at {pos} in {text!r}")
        sym = m.group(0)
        out.append((sym, lookup(sym, dialect)))
        pos = m.end()
    return tuple(out)
