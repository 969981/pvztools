"""Lineup (阵型) model and codecs — docs/lineup-format.md.

``from_arraydesign`` / ``to_arraydesign`` (§3) are aliases of the lineup-string
parser/dumper: the lonelystar ArrayDesign export shares the item grammar.
"""
from __future__ import annotations

from .codec import (
    ID_LADDER,
    ID_RAKE,
    ID_TOMB,
    STRING_SCENE_ORDER,
    decode_lineup_code,
    dump_lineup_string,
    encode_lineup_code,
    parse_lineup_string,
)
from .model import COLS, MAY_SLEEP, PROP_NAMES, Cell, Lineup, is_day_scene

from_arraydesign = parse_lineup_string
to_arraydesign = dump_lineup_string

__all__ = [
    "Cell",
    "Lineup",
    "COLS",
    "MAY_SLEEP",
    "PROP_NAMES",
    "is_day_scene",
    "decode_lineup_code",
    "encode_lineup_code",
    "parse_lineup_string",
    "dump_lineup_string",
    "from_arraydesign",
    "to_arraydesign",
    "STRING_SCENE_ORDER",
    "ID_LADDER",
    "ID_RAKE",
    "ID_TOMB",
]
