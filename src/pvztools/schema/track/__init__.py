"""轨道 DSL — see docs/track-format.md.

Public surface: the AST types (§5), the shorthand (§3) and ``.pvzt`` (§4)
parsers/dumpers, :func:`lint` (§6), :func:`expand` (§7), the JSON codec
(§5) and the rhythm-name parser (§3.2).
"""
from __future__ import annotations

from .ast import (
    KIND_ASH,
    KIND_COB,
    KIND_DOOM,
    KIND_FODDER,
    KIND_ICE,
    KIND_NATURAL,
    PHASE_POST,
    PHASE_PRE,
    Diagnostic,
    HalfWave,
    Op,
    RhythmName,
    Target,
    Track,
    Wave,
)
from .expand import Action, ExpandContext, Plan, expand
from .jsonio import from_dict, from_json, to_dict, to_json
from .lint import lint
from .pvzt import dump_pvzt, parse_pvzt
from .rhythm import parse_rhythm_name
from .shorthand import ShorthandError, dump_shorthand, parse_shorthand

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
    "parse_shorthand",
    "dump_shorthand",
    "ShorthandError",
    "parse_pvzt",
    "dump_pvzt",
    "lint",
    "expand",
    "ExpandContext",
    "Plan",
    "Action",
    "to_json",
    "from_json",
    "to_dict",
    "from_dict",
    "parse_rhythm_name",
]
