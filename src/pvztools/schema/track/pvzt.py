"""Parser / dumper for the per-wave ``.pvzt`` line format.

Specification: docs/track-format.md §4 (§4.1 wave block headers, §4.2
operation lines, §4.3 header keys). The parser additionally accepts the
Seml dialect (docs/research/seml-avz-grammar.md §1), which is a subset of
the same line format with a few spelling differences:

* ``w 601`` (auto-incrementing wave number) and ``w1~8 601`` (length
  without the ``len`` keyword); Seml ``start~length`` ranges and ice-time
  tokens are dropped with a warning.
* bare targets without ``@``: ``PP 225 25 9`` == ``PP 225 @ 25 9``.
* ``SET name expr`` variables (substituted textually, like Seml does).
* fodder shovel times: ``C t+N`` (shovel ``N`` cs after planting) and
  ``C t~T`` / ``C t~+N`` (absolute / relative shovel time) become
  ``window_cs = (t, shovel)`` (Seml "Fodder line").
* ``C_NUM`` / ``C_POS`` / ``A_NUM`` smart variants and ``P3`` roof-tail
  suffixes are read as their base symbol with a warning.
* unknown header keys (``repeat``, ``require``, ...) are warned about
  and ignored.

Everything that is dropped or normalised is recorded in
``Track.meta["warnings"]`` as ``"line N: ..."`` strings; hard errors raise
``ValueError`` with the 1-based line number.
"""
from __future__ import annotations

import ast as _pyast
import operator as _operator
import re
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from .ast import (
    KIND_FODDER,
    PHASE_POST,
    PHASE_PRE,
    HalfWave,
    Op,
    Target,
    Track,
    Wave,
)
from .symbols import DEFAULT_DIALECT, DIALECTS, lookup
from ..timing import seconds_or_cs

__all__ = ["parse_pvzt", "dump_pvzt", "parse_header", "strip_raw", "HEADER_KEYS", "SCENES"]

SCENES = ("DE", "NE", "PE", "FE", "RE", "ME")
TIMING_BASES = ("effect", "operation")
MAX_WAVE = 20  # ``N%k`` runs "until wave 20" (§4.1)

#: Header keys defined by §4.3, in canonical dump order.
HEADER_KEYS = ("scene", "rhythm", "dialect", "cards", "zombies", "protect", "lineup", "timing")

_FULLWIDTH = str.maketrans({"：": ":", "，": ",", "～": "~", "＠": "@", "＆": "&", "；": ";", "＃": "#"})
_HEADER_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
_WAVE_RE = re.compile(r"^[wW](?=\d|\s|$)")
_INT_RE = re.compile(r"^\d+$")
_ARITH_RE = re.compile(r"^[0-9+\-*/() ]+$")
_SMART_SUFFIX_RE = re.compile(r"^([A-Za-z']+)_(NUM|POS)$")
_COB_TAIL_RE = re.compile(r"^(PP|BB|DD|P|B|D)(\d)$")
_COL_RE = re.compile(r"^\d+(\.\d+)?$")

_BIN_OPS = {
    _pyast.Add: _operator.add,
    _pyast.Sub: _operator.sub,
    _pyast.Mult: _operator.mul,
    _pyast.Div: _operator.truediv,
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _err(lineno: int, msg: str) -> ValueError:
    return ValueError(f"line {lineno}: {msg}")


def _strip_comment(line: str) -> str:
    """``#`` (or fullwidth ``＃``) starts a comment anywhere on the line (§4,
    Seml ``expandLines``)."""
    return line.translate(_FULLWIDTH).split("#", 1)[0].strip()


def _eval_arith(text: str, lineno: int) -> int:
    """Evaluate a Seml ``SET`` style arithmetic expression (``776+266``).

    Only integer literals and ``+ - * /`` with parentheses are allowed; the
    result must be integral. Plain naturals (including ``08`` with a
    leading zero, which Seml's ``parseNatural`` accepts) are read directly.
    """
    if _INT_RE.match(text):
        return int(text)
    if not _ARITH_RE.match(text):
        raise _err(lineno, f"bad numeric expression {text!r}")
    try:
        tree = _pyast.parse(text, mode="eval")
    except SyntaxError:
        raise _err(lineno, f"bad numeric expression {text!r}")

    def walk(node: _pyast.AST) -> float:
        if isinstance(node, _pyast.Expression):
            return walk(node.body)
        if isinstance(node, _pyast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, _pyast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, _pyast.UnaryOp) and isinstance(node.op, (_pyast.USub, _pyast.UAdd)):
            val = walk(node.operand)
            return -val if isinstance(node.op, _pyast.USub) else val
        raise _err(lineno, f"bad numeric expression {text!r}")

    try:
        value = walk(tree)
    except ZeroDivisionError:
        raise _err(lineno, f"division by zero in {text!r}")
    if value != int(value):
        raise _err(lineno, f"expression {text!r} is not an integer")
    return int(value)


def _parse_length_value(tok: str, lineno: int) -> Tuple[Optional[int], Optional[Tuple[int, int]]]:
    """``601`` / ``6`` / ``539~619`` with §2.3 unit inference -> (length_cs, length_range)."""
    parts = tok.split("~", 1)
    try:
        values = [seconds_or_cs(float(part)) for part in parts]
    except ValueError:
        raise _err(lineno, f"bad wave length {tok!r}")
    if len(values) == 1:
        return values[0], None
    if values[0] > values[1]:
        raise _err(lineno, f"length range {tok!r} has lo > hi")
    return None, (values[0], values[1])


def _expand_selector(sel: str, lineno: int) -> Tuple[int, ...]:
    """§4.1: ``N`` | ``A~B`` | ``A~B%k`` | ``N%k`` -> actual wave numbers."""
    step = 1
    body = sel
    if "%" in sel:
        body, step_s = sel.split("%", 1)
        if not _INT_RE.match(step_s) or int(step_s) < 1:
            raise _err(lineno, f"bad selector step in {sel!r}")
        step = int(step_s)
    if "~" in body:
        lo_s, hi_s = body.split("~", 1)
        if not (_INT_RE.match(lo_s) and _INT_RE.match(hi_s)):
            raise _err(lineno, f"bad wave selector {sel!r}")
        lo, hi = int(lo_s), int(hi_s)
        if lo > hi:
            raise _err(lineno, f"wave selector {sel!r} has start > end")
    else:
        if not _INT_RE.match(body):
            raise _err(lineno, f"bad wave selector {sel!r}")
        lo = int(body)
        hi = MAX_WAVE if "%" in sel else lo
    if lo < 1:
        raise _err(lineno, f"wave numbers start at 1 in {sel!r}")
    return tuple(range(lo, hi + 1, step))


def _parse_rows(tok: str, lineno: int, warnings: List[str]) -> Tuple[int, ...]:
    """Row set ``25`` -> (2, 5); ``1256`` -> (1, 2, 5, 6). Seml ``1"25'`` marks are dropped."""
    if "'" in tok or '"' in tok:
        warnings.append(f"line {lineno}: Seml fodder type marks in rows {tok!r} ignored")
        tok = tok.replace("'", "").replace('"', "")
    if not _INT_RE.match(tok):
        raise _err(lineno, f"bad row set {tok!r}; expected digits like 25 or 1256")
    rows = tuple(int(ch) for ch in tok)
    if any(r < 1 or r > 6 for r in rows):
        raise _err(lineno, f"row out of range 1-6 in {tok!r}")
    return rows


def _parse_col(tok: str, lineno: int) -> float:
    if not _COL_RE.match(tok):
        raise _err(lineno, f"bad column {tok!r}")
    return float(tok)


def _resolve_symbol(sym: str, dialect: str, lineno: int, warnings: List[str]):
    """``symbols.lookup`` with Seml fallbacks (``C_NUM`` -> ``C``, ``P3`` -> ``P``)."""
    try:
        return sym, lookup(sym, dialect)
    except ValueError:
        pass
    m = _SMART_SUFFIX_RE.match(sym)
    if m:
        base = m.group(1)
        try:
            info = lookup(base, dialect)
        except ValueError:
            raise _err(lineno, f"unknown operation symbol {sym!r}")
        warnings.append(f"line {lineno}: Seml smart symbol {sym!r} read as {base!r}")
        return base, info
    m = _COB_TAIL_RE.match(sym)
    if m:
        base = m.group(1)
        warnings.append(f"line {lineno}: Seml roof tail column suffix in {sym!r} ignored; read as {base!r}")
        return base, lookup(base, dialect)
    raise _err(lineno, f"unknown operation symbol {sym!r}")


def _parse_time(tok: str, lineno: int, fodder: bool = False) -> Tuple[int, Optional[Tuple[int, int]], bool]:
    """§4.2 time: ``N`` | ``A~B`` | ``+N`` | ``-N`` -> (effect_cs, window_cs, relative).

    A window ``A~B`` sets ``effect_cs = A``. For fodder ops the Seml shovel
    grammar applies (``parseFodder``: a ``~`` or else the *last* ``+``, not
    at position 0, delimits the shovel part): ``t+N`` shovels ``N`` cs after planting,
    ``t~T`` at ``T``, ``t~+N`` at ``t+N`` — all become ``window_cs =
    (t, shovel)``. Other ops accept arithmetic (``776+266``) so that Seml
    ``SET`` substitutions still evaluate.
    """
    if fodder:
        cut = tok.find("~") if "~" in tok else tok.rfind("+")
        if cut > 0:
            plant_s, shovel_s = tok[:cut], tok[cut + 1:]
            plant = _eval_arith(plant_s, lineno)
            if tok[cut] == "+":
                shovel = plant + _eval_arith(shovel_s, lineno)
            elif shovel_s.startswith("+"):
                shovel = plant + _eval_arith(shovel_s[1:], lineno)
            else:
                shovel = _eval_arith(shovel_s, lineno)
            if shovel < plant:
                raise _err(lineno, f"shovel time in {tok!r} is before the planting time")
            return plant, (plant, shovel), False
    if tok.startswith("+"):
        return _eval_arith(tok[1:], lineno), None, True
    if "~" in tok:
        lo_s, hi_s = tok.split("~", 1)
        lo, hi = _eval_arith(lo_s, lineno), _eval_arith(hi_s, lineno)
        if lo > hi:
            raise _err(lineno, f"time window {tok!r} has lo > hi")
        return lo, (lo, hi), False
    return _eval_arith(tok, lineno), None, False


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def parse_header(text: str) -> Dict[str, str]:
    """Return the ``key: value`` header block (§4.3) as a dict of raw strings.

    Keys are lower-cased; scanning stops at the first wave block header.
    Unknown keys are returned as-is (``parse_pvzt`` warns about them).
    """
    out: Dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if not line:
            continue
        if _WAVE_RE.match(line):
            break
        m = _HEADER_RE.match(line)
        if not m:
            if line.split()[0] == "SET":
                continue
            raise _err(lineno, f"expected 'key: value' header or wave block, got {line!r}")
        key = m.group(1).lower()
        if key in out:
            raise _err(lineno, f"duplicate header key {key!r}")
        out[key] = m.group(2).strip()
    return out


class _Parser:
    def __init__(self, text: str, dialect: Optional[str] = None) -> None:
        self.lines = text.splitlines()
        self.warnings: List[str] = []
        self.variables: Dict[str, int] = {}
        self.header: Dict[str, str] = {}
        if dialect is not None and dialect not in DIALECTS:
            raise ValueError(f"unknown dialect {dialect!r}; expected one of {DIALECTS}")
        self.dialect = dialect or DEFAULT_DIALECT
        self.waves: List[Wave] = []
        # current wave block under construction
        self.cur_selector: Optional[Tuple[int, ...]] = None
        self.cur_ops: List[Op] = []
        self.cur_len: Optional[int] = None
        self.cur_range: Optional[Tuple[int, int]] = None
        self.cur_raw = ""
        self.next_auto_wave = 1

    # -- header ------------------------------------------------------------

    def _apply_header(self, lineno_of: Dict[str, int]) -> Dict[str, Any]:
        h = self.header
        fields: Dict[str, Any] = {}
        for key in h:
            if key not in HEADER_KEYS:
                self.warnings.append(f"line {lineno_of[key]}: unknown header key {key!r} ignored")
        if "dialect" in h:
            d = h["dialect"].lower()
            if d not in DIALECTS:
                raise _err(lineno_of["dialect"], f"unknown dialect {h['dialect']!r}; expected one of {DIALECTS}")
            self.dialect = d
        fields["dialect"] = self.dialect
        if "scene" in h:
            s = h["scene"].upper()
            if s not in SCENES:
                raise _err(lineno_of["scene"], f"unknown scene {h['scene']!r}; expected one of {SCENES}")
            fields["scene"] = s
        if "timing" in h:
            t = h["timing"].lower()
            if t not in TIMING_BASES:
                raise _err(lineno_of["timing"], f"timing must be one of {TIMING_BASES}, got {h['timing']!r}")
            fields["timing_base"] = t
        if "protect" in h:
            fields["protect"] = self._parse_protect(h["protect"], lineno_of["protect"])
        if "cards" in h:
            fields["cards"] = h["cards"]
        if "zombies" in h:
            fields["zombies"] = h["zombies"]
        if "lineup" in h:
            fields["lineup_code"] = h["lineup"]
        return fields

    def _parse_protect(self, value: str, lineno: int) -> Tuple[int, ...]:
        out = []
        for tok in value.split():
            if tok.endswith("'"):
                self.warnings.append(f"line {lineno}: Seml normal-plant mark in protect {tok!r} ignored")
                tok = tok[:-1]
            if not re.match(r"^[1-6][1-9]$", tok):
                raise _err(lineno, f"bad protect position {tok!r}; expected row+col digits like 28")
            out.append(int(tok))
        return tuple(out)

    # -- waves -------------------------------------------------------------

    def _flush_wave(self) -> None:
        if self.cur_selector is None:
            return
        self.waves.append(
            Wave(
                selector=self.cur_selector,
                top=HalfWave(tuple(self.cur_ops)),
                bottom=None,
                mirrored=False,
                length_cs=self.cur_len,
                length_range=self.cur_range,
                raw=self.cur_raw,
            )
        )
        self.cur_selector = None
        self.cur_ops = []
        self.cur_len = None
        self.cur_range = None
        self.cur_raw = ""

    def _parse_wave_header(self, line: str, lineno: int) -> None:
        """§4.1 ``w SEL(,SEL)* [len V]`` plus Seml ``w [ice...] LEN`` / ``w1~8 LEN``."""
        self._flush_wave()
        rest = line[1:]
        tokens = rest.split()
        sel_parts: List[str] = []
        if rest[:1].isdigit():
            # selectors may be written "1%4, 2~9%2, 11~19" or "1 ,3": keep joining
            # while a trailing or leading comma asks for more
            sel_parts.append(tokens.pop(0))
            while tokens and (sel_parts[-1].endswith(",") or tokens[0].startswith(",")):
                sel_parts.append(tokens.pop(0))
        sel_text = "".join(sel_parts)
        if sel_text:
            # Selectors may overlap ("w1%4, 2~9%2, 11~19" hits 13 and 17 twice in the
            # spec's own example); keep first occurrence, preserve order.
            selector: List[int] = []
            for sel in sel_text.split(","):
                if sel == "":
                    raise _err(lineno, f"empty wave selector in {line!r}")
                selector.extend(n for n in _expand_selector(sel, lineno) if n not in selector)
        else:
            selector = [self.next_auto_wave]  # Seml "w 601"
        self.cur_selector = tuple(selector)
        self.next_auto_wave = max(selector) + 1

        if tokens and tokens[0].lower() == "len":
            if len(tokens) != 2:
                raise _err(lineno, f"expected exactly one value after 'len' in {line!r}")
            self.cur_len, self.cur_range = _parse_length_value(tokens[1], lineno)
        elif tokens:
            # Seml: "w [iceTime ...] waveLength" or "... startTick~waveLength"
            wave_range = tokens[-1]
            if tokens[:-1]:
                self.warnings.append(
                    f"line {lineno}: Seml ice times {' '.join(tokens[:-1])!r} in wave header ignored"
                )
            if "~" in wave_range:
                start_s, len_s = wave_range.split("~", 1)
                if not (_INT_RE.match(start_s) and _INT_RE.match(len_s)):
                    raise _err(lineno, f"bad Seml wave range {wave_range!r}")
                self.warnings.append(
                    f"line {lineno}: Seml start tick {start_s} in {wave_range!r} ignored; length = {len_s}"
                )
                wave_range = len_s
            self.cur_len, self.cur_range = _parse_length_value(wave_range, lineno)
        self.cur_raw = line

    # -- operations --------------------------------------------------------

    def _substitute(self, tokens: List[str]) -> List[str]:
        """Seml variable substitution: textual replaceAll on every token after the symbol."""
        if not self.variables:
            return tokens
        out = tokens[:1]
        for tok in tokens[1:]:
            for name, value in self.variables.items():
                tok = tok.replace(name, str(value))
            out.append(tok)
        return out

    def _parse_set(self, tokens: List[str], lineno: int) -> None:
        if len(tokens) != 3 or not tokens[1] or _INT_RE.match(tokens[1]):
            raise _err(lineno, "SET expects 'SET name expr'")
        expr = self._substitute(["SET", tokens[2]])[1]
        self.variables[tokens[1]] = _eval_arith(expr, lineno)
        self.warnings.append(f"line {lineno}: Seml SET variable {tokens[1]!r} substituted once and dropped")

    def _parse_action(self, text: str, lineno: int, simultaneous: bool) -> Op:
        """§4.2 ``SYMBOL TIME [@ ROWS COL]`` or Seml ``SYMBOL TIME ROWS COL``."""
        tokens = self._substitute(text.split())
        if len(tokens) < 2:
            raise _err(lineno, f"action {text!r} needs at least a symbol and a time")
        symbol, info = _resolve_symbol(tokens[0], self.dialect, lineno, self.warnings)
        effect_cs, window, relative = _parse_time(tokens[1], lineno, fodder=info.kind == KIND_FODDER)
        target_toks = tokens[2:]
        if target_toks:
            if target_toks[0].startswith("@"):
                target_toks = ([target_toks[0][1:]] if target_toks[0] != "@" else []) + target_toks[1:]
            elif len(tokens) != 4:
                raise _err(lineno, f"expected '@ rows col' after time in {text!r}")
            if len(target_toks) != 2:
                raise _err(lineno, f"target must be 'rows col' in {text!r}")
            rows = _parse_rows(target_toks[0], lineno, self.warnings)
            target: Optional[Target] = Target(rows=rows, col=_parse_col(target_toks[1], lineno))
            # §4.2: multi-unit symbols pair with the row list ("PP 225 @ 25 9" = 2 cobs);
            # a longer row list (C @ 1256) means one unit per row.
            count = max(info.count, len(rows))
        else:
            target = None
            count = info.count
        return Op(
            symbol=symbol,
            kind=info.kind,
            variant=info.variant,
            count=count,
            al=info.al_each * count,
            phase=PHASE_PRE if effect_cs < 0 else PHASE_POST,
            effect_cs=effect_cs,
            window_cs=window,
            relative=relative,
            target=target,
            simultaneous_with_prev=simultaneous,
        )

    def _parse_op_line(self, line: str, lineno: int) -> None:
        if self.cur_selector is None:
            raise _err(lineno, f"operation line {line!r} before any wave block header")
        parts = re.split(r"\s*([&;])\s*", line)
        # parts = [action, sep, action, sep, action, ...]
        simultaneous = False
        for i, part in enumerate(parts):
            if i % 2 == 1:
                simultaneous = part == "&"
                continue
            if not part:
                # ``;`` equals a newline (§4.2), so an empty action is an empty line
                self.warnings.append(f"line {lineno}: empty action (stray separator) in {line!r} ignored")
                continue
            self.cur_ops.append(self._parse_action(part, lineno, simultaneous))

    # -- driver ------------------------------------------------------------

    def run(self) -> Track:
        lineno_of: Dict[str, int] = {}
        in_waves = False
        fields: Dict[str, Any] = {}
        for lineno, raw in enumerate(self.lines, 1):
            line = _strip_comment(raw)
            if not line:
                continue
            first = line.split()[0]
            if first == "SET":
                self._parse_set(line.split(), lineno)
                continue
            if _WAVE_RE.match(line):
                if not in_waves:
                    fields = self._apply_header(lineno_of)
                    in_waves = True
                self._parse_wave_header(line, lineno)
                continue
            m = _HEADER_RE.match(line)
            if m and not in_waves:
                key = m.group(1).lower()
                if key in self.header:
                    raise _err(lineno, f"duplicate header key {key!r}")
                self.header[key] = m.group(2).strip()
                lineno_of[key] = lineno
                continue
            if m and m.group(1).lower() in HEADER_KEYS:
                raise _err(lineno, f"header key {m.group(1)!r} after wave blocks")
            self._parse_op_line(line, lineno)
        if not in_waves:
            fields = self._apply_header(lineno_of)
        self._flush_wave()
        meta: Dict[str, Any] = {"warnings": self.warnings}
        if "rhythm" in self.header:
            meta["rhythm_raw"] = self.header["rhythm"]
        return Track(waves=tuple(self.waves), kind="pvzt", meta=meta, **fields)


def parse_pvzt(text: str, dialect: Optional[str] = None) -> Track:
    """Parse ``.pvzt`` text (docs/track-format.md §4) into a ``Track`` (kind ``"pvzt"``).

    ``dialect`` is the default when the text has no ``dialect:`` header
    (a header always wins); ``None`` means ``duben``.

    * ``Wave.selector`` holds the actual wave numbers the block applies to.
    * ``Op.effect_cs`` / ``window_cs`` / ``relative`` / ``target`` /
      ``simultaneous_with_prev`` are filled; ``phase`` is ``PHASE_PRE`` when
      ``effect_cs < 0`` (prediction before refresh) else ``PHASE_POST``.
    * the ``rhythm`` header is kept verbatim in ``meta["rhythm_raw"]``
      (shorthand parsing lives in the shorthand module).
    * non-fatal normalisations are listed in ``meta["warnings"]``.

    Raises ``ValueError("line N: ...")`` on malformed input.
    """
    return _Parser(text, dialect).run()


# ---------------------------------------------------------------------------
# dumper
# ---------------------------------------------------------------------------


def _format_selector(waves: Tuple[int, ...]) -> str:
    """Compress wave numbers into §4.1 selectors: runs of >=3 with a constant
    step become ``A~B%k`` (``A~B`` for step 1, also for pairs)."""
    parts: List[str] = []
    i = 0
    n = len(waves)
    while i < n:
        j = i + 1
        step = waves[j] - waves[i] if j < n else 0
        while j < n and waves[j] - waves[j - 1] == step and step > 0:
            j += 1
        run = j - i
        if run >= 2 and step == 1:
            parts.append(f"{waves[i]}~{waves[j - 1]}")
            i = j
        elif run >= 3:
            parts.append(f"{waves[i]}~{waves[j - 1]}%{step}")
            i = j
        else:
            parts.append(str(waves[i]))
            i += 1
    return ",".join(parts)


def _format_col(col: float) -> str:
    return str(int(col)) if float(col).is_integer() else repr(float(col))


def _format_length(cs: int) -> str:
    """Write a length so that §2.3 unit inference reads it back as ``cs``:
    values ≤ 100cs would be taken as seconds, so they are written as
    fractional seconds (``50`` -> ``0.5``)."""
    if cs <= 100:
        return repr(cs / 100)
    return str(cs)


def _format_op(op: Op) -> str:
    if op.effect_cs is None:
        raise ValueError(f"dump_pvzt: op {op.symbol!r} has no effect time (shorthand track?); .pvzt needs timed ops")
    if op.relative:
        time = f"+{op.effect_cs}"
    elif op.window_cs is not None:
        time = f"{op.window_cs[0]}~{op.window_cs[1]}"
    else:
        time = str(op.effect_cs)
    text = f"{op.symbol} {time}"
    if op.target is not None:
        rows = "".join(str(r) for r in op.target.rows)
        text += f" @ {rows} {_format_col(op.target.col)}"
    return text


def dump_pvzt(track: Track) -> str:
    """Serialise a ``Track`` to canonical ``.pvzt`` text (§4).

    Headers are emitted in ``HEADER_KEYS`` order, one wave block per
    ``Wave``, ``&``-joined ops on one line and all other ops on their own
    lines (``;`` is never emitted since it equals a newline). The output
    re-parses to an equal ``Track`` (ignoring ``Wave.raw`` and ``meta``).

    Raises ``ValueError`` for ops without an effect time (shorthand tracks).
    """
    lines: List[str] = []
    if track.scene is not None:
        lines.append(f"scene: {track.scene}")
    rhythm = track.meta.get("rhythm_raw") or (track.rhythm.raw if track.rhythm is not None else None)
    if rhythm:
        lines.append(f"rhythm: {rhythm}")
    lines.append(f"dialect: {track.dialect}")
    if track.cards is not None:
        lines.append(f"cards: {track.cards}")
    if track.zombies is not None:
        lines.append(f"zombies: {track.zombies}")
    if track.protect:
        lines.append("protect: " + " ".join(str(p) for p in track.protect))
    if track.lineup_code is not None:
        lines.append(f"lineup: {track.lineup_code}")
    lines.append(f"timing: {track.timing_base}")
    for wave in track.waves:
        lines.append("")
        header = "w" + _format_selector(tuple(wave.selector))
        if wave.length_range is not None:
            header += f" len {_format_length(wave.length_range[0])}~{_format_length(wave.length_range[1])}"
        elif wave.length_cs is not None:
            header += f" len {_format_length(wave.length_cs)}"
        lines.append(header)
        ops = list(wave.top.ops)
        if wave.bottom is not None:
            ops.extend(wave.bottom.ops)
        current = ""
        for op in ops:
            if op.simultaneous_with_prev and current:
                current += " & " + _format_op(op)
            else:
                if current:
                    lines.append("  " + current)
                current = _format_op(op)
        if current:
            lines.append("  " + current)
    return "\n".join(lines) + "\n"


def strip_raw(track: Track) -> Track:
    """Return ``track`` with ``Wave.raw`` cleared and ``meta`` emptied, for
    structural comparisons such as dump/parse round trips."""
    return replace(
        track,
        waves=tuple(replace(w, raw="") for w in track.waves),
        meta={},
    )
