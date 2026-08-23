"""Seml <-> Track bridge.

Grammar: docs/research/seml-avz-grammar.md §1 (Rottenham/seml ``parser.ts``);
our side: docs/track-format.md §4 / §8. Seml is a per-wave format whose
operation lines are a subset of ``.pvzt`` (``SYMBOL TIME ROWS COL``), so
``from_seml`` does the Seml-only preprocessing here and hands the result
to ``pvzt.parse_pvzt``:

* ``#`` comments and whitespace collapsing (§1 "Preprocessing");
* ``w1~N`` loop expansion — the block is duplicated per wave number so
  that ``SET x x+24`` inside the block re-executes per iteration, exactly
  like the Seml parser (§1 "Loop expansion");
* wave-line ice times ``w [t ...] len`` become ``I t`` ice ops (no target)
  placed first in the wave; ``start~len`` ranges lose the start tick into
  ``meta["seml"]["start_tick"]`` and keep ``len``;
* Seml setting lines (``repeat:``, ``require:``, ``huge:``, ``avzTime:``,
  ...) are preserved verbatim in ``meta["seml"]["settings"]`` so that
  ``to_seml`` can write them back.

Going the other way, ``to_seml`` writes Seml text from a ``pvzt``-kind
``Track``; constructs Seml cannot express are commented out in the output
and listed in the returned text as ``# pvztools: ...`` lines.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from ..schema.track.ast import (
    KIND_ASH,
    KIND_COB,
    KIND_DOOM,
    KIND_FODDER,
    KIND_ICE,
    KIND_NATURAL,
    Op,
    Track,
    Wave,
)
from ..schema.track.pvzt import parse_header, parse_pvzt

__all__ = ["from_seml", "to_seml", "SEML_SETTING_KEYS", "SEML_DIALECT", "DEFAULT_WAVE_LENGTH"]

#: Seml symbols read with this dialect (docs/track-format.md §2.1): Seml ``C``
#: is the ordinary 垫材 card (``soft``, never "高级垫材") and ``a`` is squash.
SEML_DIALECT = "baicai"

#: Seml header keys that are not ``.pvzt`` headers (§1 "Header / setting lines").
SEML_SETTING_KEYS = (
    "repeat",
    "require",
    "ban",
    "huge",
    "activate",
    "dance",
    "natural",
    "cobdelay",
    "types",
    "targetpos",
    "avztime",
)
#: Wave length written for waves without a known length (AvZ ``ExportSeml`` uses 9999).
DEFAULT_WAVE_LENGTH = 9999

_NAT_RE = re.compile(r"^\d+$")
_LOOP_RE = re.compile(r"^w(\d+)~(\d+)$")
_LINE_MSG_RE = re.compile(r"^line (\d+):(.*)$", re.S)
# Seml cob symbols: B|P|D optionally doubled, optional roof tail digit
_COB_SYMBOL = {
    None: "P",
    "replant": "P",
    "sync": "P",
    "before": "B",
    "before_back": "B",
    "delay": "D",
    "delay_back": "D",
}
_ASH_SYMBOL = {"cherry": "A", "jalapeno": "J", "squash": "W", "light": "A"}


# ---------------------------------------------------------------------------
# from_seml
# ---------------------------------------------------------------------------


def _clean_lines(text: str) -> List[Tuple[int, str]]:
    """§1 preprocessing: strip ``#`` comments, collapse blanks, drop empty lines.
    Returns ``(lineno, line)`` pairs (1-based source line numbers)."""
    out: List[Tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"[ \t]+", " ", raw.split("#", 1)[0].strip())
        if line:
            out.append((lineno, line))
    return out


def _expand_loops(lines: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """§1 "Loop expansion": ``w1~4 ...`` + following non-``w`` lines are
    duplicated once per wave number, the header rewritten to ``wN ...``."""
    out: List[Tuple[int, str]] = []
    i = 0
    while i < len(lines):
        lineno, line = lines[i]
        first = line.split(" ", 1)[0]
        m = _LOOP_RE.match(first)
        if m is None:
            out.append((lineno, line))
            i += 1
            continue
        start, end = int(m.group(1)), int(m.group(2))
        if start < 1 or start > end:
            raise ValueError(f"line {lineno}: bad wave loop {first!r}; expected w<start>~<end> with start <= end")
        rest = line[len(first):]
        j = i + 1
        while j < len(lines) and not lines[j][1].startswith("w"):
            j += 1
        block = lines[i + 1 : j]
        for n in range(start, end + 1):
            out.append((lineno, f"w{n}{rest}"))
            out.extend(block)
        i = j
    return out


def _rewrite_wave_line(line: str, lineno: int, start_ticks: Dict[str, int], wave_index: int) -> List[str]:
    """Turn a Seml wave line into ``.pvzt`` lines: the header with ``len``
    and one ``I t`` line per ice time (§1 "Wave line")."""
    tokens = line.split(" ")
    head = tokens[0]
    if len(tokens) < 2:
        raise ValueError(f"line {lineno}: Seml wave line {line!r} needs a wave length")
    wave_range = tokens[-1]
    ice_times = tokens[1:-1]
    for t in ice_times:
        if not _NAT_RE.match(t):
            raise ValueError(f"line {lineno}: bad ice time {t!r} in {line!r}; expected a natural number")
    if "~" in wave_range:
        start_s, len_s = wave_range.split("~", 1)
        if not (_NAT_RE.match(start_s) and _NAT_RE.match(len_s)):
            raise ValueError(f"line {lineno}: bad wave range {wave_range!r}; expected <startTick>~<waveLength>")
        if int(start_s) > int(len_s):
            raise ValueError(f"line {lineno}: start tick {start_s} exceeds wave length {len_s}")
        start_ticks[str(wave_index)] = int(start_s)
        wave_range = len_s
    if not _NAT_RE.match(wave_range) or int(wave_range) < 1:
        raise ValueError(f"line {lineno}: bad wave length {wave_range!r}; expected a positive integer")
    if ice_times and int(ice_times[-1]) > int(wave_range):
        raise ValueError(f"line {lineno}: last ice time {ice_times[-1]} exceeds wave length {wave_range}")
    # ``w`` alone auto-increments in pvzt too; ``wN`` keeps the explicit number.
    out = [f"{head} len {wave_range}"]
    out.extend(f"I {t}" for t in ice_times)
    return out


def from_seml(text: str) -> Track:
    """Parse Seml text (grammar §1) into a ``Track`` of kind ``"pvzt"``.

    * wave ice times -> ``Op(kind="ice", effect_cs=t, target=None)``, placed
      before the wave's other ops (``"0"`` = perfectly predicted ice);
    * ``start~len`` -> ``Wave.length_cs = len``, start tick recorded in
      ``meta["seml"]["start_tick"][<wave index>]``;
    * Seml-only settings kept in ``meta["seml"]["settings"]`` (lower-cased
      keys, raw values); ``avzTime`` is *not* applied to the times;
    * ``C_NUM``/``C_POS``/``A_NUM`` smart symbols, roof tail digits, fodder
      type marks (``1"2'``) and ``protect`` ``'`` marks are accepted by the
      pvzt parser with ``meta["warnings"]`` entries;
    * symbols are read in the ``baicai`` dialect (``SEML_DIALECT``): Seml
      ``C`` is plain fodder (``variant="soft"``), ``a``/``W`` squash;
    * ``meta["source"] == "seml"``.

    Raises ``ValueError("line N: ...")`` on malformed input.
    """
    lines = _expand_loops(_clean_lines(text))
    start_ticks: Dict[str, int] = {}
    pvzt_lines: List[str] = []
    source_lineno: List[int] = []  # pvzt line index -> Seml source line
    wave_index = -1
    for lineno, line in lines:
        first = line.split(" ", 1)[0]
        if first.startswith("w") and (first == "w" or first[1:].isdigit()):
            wave_index += 1
            new_lines = _rewrite_wave_line(line, lineno, start_ticks, wave_index)
        else:
            new_lines = [line]
        pvzt_lines.extend(new_lines)
        source_lineno.extend([lineno] * len(new_lines))
    pvzt_text = "\n".join(pvzt_lines) + "\n"

    def remap(msg: str) -> str:
        """Rewrite ``line N`` of the preprocessed text to the Seml source line."""
        m = _LINE_MSG_RE.match(msg)
        if m is None:
            return msg
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(source_lineno):
            return f"line {source_lineno[idx]}:{m.group(2)}"
        return msg

    try:
        track = parse_pvzt(pvzt_text, dialect=SEML_DIALECT)
    except ValueError as exc:
        raise ValueError(remap(str(exc))) from None
    header = parse_header(pvzt_text)
    settings = {k: v for k, v in header.items() if k in SEML_SETTING_KEYS}
    # the pvzt parser already warned about these keys being unknown; we keep them
    warnings: List[str] = []
    for w in track.meta.get("warnings", []):
        w = remap(w)
        # loop expansion repeats the same source line; report it once
        if "unknown header key" not in w and w not in warnings:
            warnings.append(w)
    meta: Dict[str, Any] = dict(track.meta)
    meta["warnings"] = warnings
    meta["source"] = "seml"
    meta["seml"] = {"settings": settings, "start_tick": start_ticks}
    return replace(track, meta=meta)


# ---------------------------------------------------------------------------
# to_seml
# ---------------------------------------------------------------------------


def _rows_text(rows: Tuple[int, ...]) -> str:
    return "".join(str(r) for r in rows)


def _col_text(col: float) -> str:
    return str(int(col)) if float(col).is_integer() else repr(float(col))


def _default_rows(count: int, nrows: int) -> Tuple[int, ...]:
    """AvZ ``PP``/``DD`` defaults: rows 2/5 (6-row scenes) or 2/4, col 9."""
    pair = (2, 5) if nrows == 6 else (2, 4)
    return pair[:count] if count <= 2 else pair


def _time_text(op: Op) -> str:
    if op.relative:
        return f"+{op.effect_cs}"
    return str(op.effect_cs)


class _Writer:
    def __init__(self, track: Track) -> None:
        self.track = track
        self.nrows = 6 if track.scene in (None, "PE", "FE") else 5
        self.notes: List[str] = []

    def note(self, msg: str) -> None:
        self.notes.append(f"# pvztools: {msg}")

    def op_line(self, op: Op, wave_no: int) -> Optional[str]:
        """One Seml action line for ``op`` or None when it has no Seml form."""
        where = f"w{wave_no} {op.symbol}"
        if op.effect_cs is None:
            self.note(f"{where}: no time (shorthand op) -> skipped")
            return None
        if op.kind == KIND_NATURAL:
            self.note(f"{where}: natural output has no Seml form -> skipped")
            return None
        if op.kind == KIND_ICE:
            return None  # emitted on the wave line as an ice time
        if not op.relative and op.effect_cs < 0:
            self.note(f"{where} {op.effect_cs}: Seml forbids negative times -> commented out")
            return f"# {op.symbol} {op.effect_cs}"
        time = _time_text(op)
        if op.window_cs is not None and op.kind == KIND_FODDER:
            # Seml ``C t~T`` = plant at t, shovel at T (our window lo~hi)
            time = f"{op.window_cs[0]}~{op.window_cs[1]}"
        elif op.window_cs is not None:
            self.note(f"{where}: time window {op.window_cs[0]}~{op.window_cs[1]} written as {op.window_cs[0]}")
            time = str(op.window_cs[0])

        target = op.target
        if op.kind == KIND_COB:
            count = 2 if op.count >= 2 else 1
            if op.count > 2:
                self.note(f"{where}: {op.count} cobs in one op; Seml takes at most 2 -> first two rows")
            sym = _COB_SYMBOL.get(op.variant, "P") * count
            if op.variant in ("replant", "sync", "before_back", "delay_back"):
                self.note(f"{where}: variant {op.variant!r} written as {sym}")
            if target is None:
                rows = _default_rows(count, self.nrows)
                self.note(f"{where}: no target -> default rows {_rows_text(rows)} col 9")
                return f"{sym} {time} {_rows_text(rows)} 9"
            rows = target.rows[:count]
            if len(rows) < count:
                rows = rows + rows[-1:] * (count - len(rows))
            return f"{sym} {time} {_rows_text(rows)} {_col_text(target.col)}"
        if op.kind == KIND_ASH:
            sym = _ASH_SYMBOL.get(op.variant or "cherry", "A")
            if op.variant == "light":
                self.note(f"{where}: ambiguous 'light' ash written as cherry (A)")
            if target is None:
                self.note(f"{where}: ash card without target -> commented out")
                return f"# {sym} {time}"
            if len(target.rows) > 1:
                return f"{sym}_NUM {time} {_rows_text(target.rows)} {_col_text(target.col)}"
            return f"{sym} {time} {target.rows[0]} {_col_text(target.col)}"
        if op.kind == KIND_DOOM:
            if op.variant == "imitater":
                self.note(f"{where}: imitater doom written as plain N")
            if target is None:
                self.note(f"{where}: doom without target -> commented out")
                return f"# N {time}"
            return f"N {time} {target.rows[0]} {_col_text(target.col)}"
        if op.kind == KIND_FODDER:
            if target is None:
                rows = (1, 2, 5, 6) if self.nrows == 6 else (1, 2, 3, 4, 5)  # land rows
                self.note(f"{where}: fodder without target -> land rows {_rows_text(rows)} col 9")
                return f"C {time} {_rows_text(rows)} 9"
            return f"C {time} {_rows_text(target.rows)} {_col_text(target.col)}"
        self.note(f"{where}: unknown op kind {op.kind!r} -> skipped")
        return None

    def wave_block(self, wave: Optional[Wave], wave_no: int, start_tick: Optional[int]) -> List[str]:
        ops: List[Op] = []
        if wave is not None:
            ops = list(wave.top.ops) + (list(wave.bottom.ops) if wave.bottom is not None else [])
        ice_times: List[int] = []
        for op in ops:
            if op.kind == KIND_ICE and op.effect_cs is not None:
                if op.relative:
                    self.note(f"w{wave_no} {op.symbol}: relative ice time +{op.effect_cs} has no Seml form -> dropped")
                    continue
                if op.target is not None:
                    self.note(f"w{wave_no} {op.symbol}: ice target dropped (Seml ice times carry no position)")
                ice_times.append(op.effect_cs)
        if wave is None:
            length = DEFAULT_WAVE_LENGTH
            self.note(f"w{wave_no}: not covered by the track -> {DEFAULT_WAVE_LENGTH}")
        elif wave.length_cs is not None:
            length = wave.length_cs
        elif wave.length_range is not None:
            length = wave.length_range[0]
            self.note(f"w{wave_no}: length range {wave.length_range[0]}~{wave.length_range[1]} written as {length}")
        else:
            length = DEFAULT_WAVE_LENGTH
            self.note(f"w{wave_no}: no length -> {DEFAULT_WAVE_LENGTH}")
        tail = f"{start_tick}~{length}" if start_tick is not None else str(length)
        head = f"w{wave_no}"
        if ice_times:
            head += " " + " ".join(str(t) for t in sorted(ice_times))
        lines = [f"{head} {tail}"]
        for op in ops:
            line = self.op_line(op, wave_no)
            if line is not None:
                lines.append(line)
        return lines

    def run(self) -> str:
        t = self.track
        if t.kind != "pvzt":
            raise ValueError(
                f"to_seml needs a per-wave track (kind 'pvzt'), got {t.kind!r}; shorthand has no timings"
            )
        out: List[str] = []
        if t.scene is not None:
            out.append(f"scene:{t.scene}")
        if t.protect:
            out.append("protect:" + " ".join(str(p) for p in t.protect))
        seml_meta = t.meta.get("seml") or {}
        for key, value in (seml_meta.get("settings") or {}).items():
            out.append(f"{key}:{value}")
        start_ticks: Dict[str, int] = seml_meta.get("start_tick") or {}

        by_wave: Dict[int, Tuple[int, Wave]] = {}
        for idx, wave in enumerate(t.waves):
            for n in wave.selector:
                if n in by_wave:
                    self.note(f"w{n}: defined by more than one block -> first block kept")
                else:
                    by_wave[n] = (idx, wave)
        last = max(by_wave) if by_wave else 0
        for n in range(1, last + 1):
            entry = by_wave.get(n)
            wave = entry[1] if entry else None
            tick = start_ticks.get(str(entry[0])) if entry else None
            out.append("")
            out.extend(self.wave_block(wave, n, tick))
        if self.notes:
            out.append("")
            out.extend(self.notes)
        return "\n".join(out) + "\n"


def to_seml(track: Track) -> str:
    """Write Seml text (grammar §1) for a ``pvzt``-kind ``Track``.

    Waves are emitted sequentially ``w1..wN`` (Seml requires no gaps);
    ice ops become wave-line ice times; cob / ash / doom / fodder ops
    become ``P PP B BB D DD`` / ``A J W`` (``A_NUM`` for multi-row) /
    ``N`` / ``C`` lines. Anything without a Seml equivalent is commented
    out and explained in trailing ``# pvztools: ...`` lines.

    Raises ``ValueError`` for shorthand tracks.
    """
    return _Writer(track).run()
