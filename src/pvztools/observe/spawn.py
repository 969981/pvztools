"""Spawn-list decoding and pretty printing.

The Board keeps a ``20 x 50`` int32 table (``board.spawn_list``): row *i* is
wave *i + 1*, a run of zombie ids terminated by ``-1``.  Only the run up to
the first ``-1`` is meaningful — the game does not clear the rest of a row,
so slots after the terminator hold leftovers (verified live on Steam GOTY
1096).  Tutorial levels and some mini-games never fill the table at all; the
leading values then fall outside ``0..32`` — :func:`decode` returns ``None``
in that case and :func:`invalid_reason` explains why.
"""
from __future__ import annotations

import struct
from typing import List, Optional, Sequence, Tuple

from ..model.types import SPAWN_EMPTY, SPAWN_PER_WAVE, SPAWN_WAVES, SpawnList, zombie_name_zh
from ..schema.enums import ZombieType

__all__ = [
    "SPAWN_LIST_BYTES",
    "MAX_ZOMBIE_ID",
    "decode",
    "invalid_reason",
    "short_name",
    "describe",
    "describe_wave",
]

#: Size of the raw table in bytes.
SPAWN_LIST_BYTES = SPAWN_WAVES * SPAWN_PER_WAVE * 4
#: Largest zombie id in an official build (红眼巨人).
MAX_ZOMBIE_ID = int(max(ZombieType))


def _unpack(data: bytes, waves: int, per_wave: int) -> List[Tuple[int, ...]]:
    """Rows truncated at their first ``-1`` terminator."""
    need = waves * per_wave * 4
    if len(data) < need:
        raise ValueError("spawn list needs %d bytes, got %d" % (need, len(data)))
    vals = struct.unpack("<%di" % (waves * per_wave), data[:need])
    rows = []
    for i in range(waves):
        row = vals[i * per_wave:(i + 1) * per_wave]
        if SPAWN_EMPTY in row:
            row = row[:row.index(SPAWN_EMPTY)]
        rows.append(tuple(row))
    return rows


def invalid_reason(data: bytes, waves: int = SPAWN_WAVES, per_wave: int = SPAWN_PER_WAVE) -> Optional[str]:
    """``None`` when the raw table looks like a real spawn list, else a note
    (tutorial / no-list case, or garbage)."""
    rows = _unpack(data, waves, per_wave)
    bad = [(i + 1, v) for i, row in enumerate(rows) for v in row if not (0 <= v <= MAX_ZOMBIE_ID)]
    if bad:
        w, v = bad[0]
        return "出怪表无效：w%d 含超出 -1..%d 的值 %d（教程关或本模式不生成出怪表）" % (w, MAX_ZOMBIE_ID, v)
    if not any(rows):
        return "出怪表为空（尚未生成出怪，或本模式不使用出怪表）"
    return None


def decode(data: bytes, waves: int = SPAWN_WAVES, per_wave: int = SPAWN_PER_WAVE) -> Optional[SpawnList]:
    """Decode the raw table; ``None`` when :func:`invalid_reason` is not None.

    Each row is cut at its ``-1`` terminator; ordering inside a wave is kept.
    """
    if invalid_reason(data, waves, per_wave) is not None:
        return None
    return SpawnList(tuple(_unpack(data, waves, per_wave)))


def short_name(zombie_id: int) -> str:
    """Compact Chinese label: ``普通`` / ``路障`` / ``白眼巨人`` ..."""
    name = zombie_name_zh(zombie_id)
    if name.endswith("僵尸") and len(name) > 2:
        return name[:-2]
    return name


def describe_wave(n: int, zombies: Sequence[int], total_waves: int = SPAWN_WAVES) -> str:
    """``'w10 (50只): 普通x14 路障x3 ... <== 旗帜波'``"""
    counts = {}
    for z in zombies:
        counts[z] = counts.get(z, 0) + 1
    parts = " ".join("%sx%d" % (short_name(z), c) for z, c in counts.items())
    line = "w%d (%d只): %s" % (n, len(zombies), parts or "-")
    if n % 10 == 0 or ZombieType.FLAG in zombies:
        line += " <== 旗帜波"
    return line


def describe(spawn: Optional[SpawnList], skip_empty: bool = True) -> str:
    """Multi-line table, one wave per line; ``'(无出怪表)'`` for ``None``."""
    if spawn is None:
        return "(无出怪表)"
    lines = []
    for i, w in enumerate(spawn.waves):
        if skip_empty and not w:
            continue
        lines.append(describe_wave(i + 1, w, len(spawn.waves)))
    if not lines:
        return "(出怪表为空)"
    present = ", ".join(short_name(z) for z in spawn.types_present())
    lines.append("类型: %s；合计 %d 只" % (present, spawn.total()))
    return "\n".join(lines)
