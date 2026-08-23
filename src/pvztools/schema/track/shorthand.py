"""速记 (shorthand) track parser and dumper (docs/track-format.md §3).

Grammar (§3)::

    [节奏名 ':'] 波串 [波长列表]
    波串     := 波 ('|' 波)*
    波       := 半场 ['/' 半场]
    半场     := 操作串 ['-' 操作串]
    操作串   := 操作*
    波长列表 := '(' 数值 (分隔 数值)* ')' | 数值 (分隔 数值)*

Implemented here:

* §3.1 复写/简写 detection (``mirrored`` flag, pure-简写 split by al);
* §3.1b tolerance rules (fullwidth punctuation, ``+`` for ``-``, stray
  ``|``, length qualifiers, ``Ice3``/``I0``/``io``, annotation stripping,
  case-insensitive rhythm names, ``wN:`` per-wave prefix mode, backtick /
  ``’`` / ``₁`` as ``'``);
* §2.3 per-item length unit inference via :func:`timing.seconds_or_cs`.

Every normalisation is recorded in ``Track.meta["normalized_from"]``;
dropped annotations go to ``meta["annotations"]`` plus a warning with code
``ANNOTATION_DROPPED`` in ``meta["warnings"]``.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from ..timing import seconds_or_cs
from .ast import (
    KIND_ICE,
    PHASE_POST,
    PHASE_PRE,
    HalfWave,
    Op,
    RhythmName,
    Track,
    Wave,
)
from .rhythm import looks_like_rhythm_name, parse_rhythm_name
from .symbols import DEFAULT_DIALECT, DIALECTS, tokenize_ops

__all__ = ["parse_shorthand", "dump_shorthand", "ShorthandError"]


class ShorthandError(ValueError):
    """Parse error with a machine-readable ``code`` (§3.1b / §6)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# --- §3.1b text normalisation ---------------------------------------------
_FULLWIDTH = {
    "：": ":",
    "（": "(",
    "）": ")",
    "｜": "|",
    "，": ",",
    "、": ",",
    "～": "~",
    "＋": "+",
    "－": "-",
    "／": "/",
    "　": " ",
}
_APOSTROPHES = {"`": "'", "’": "'", "‘": "'", "′": "'", "₁": "'"}

# ``wN:`` may follow whitespace or a ``|`` ("w1:PP|w2:I-PP")
_WAVE_PREFIX_RE = re.compile(r"(?:^|[\s|])w(\d+)\s*:", re.IGNORECASE)
_PAREN_RE = re.compile(r"\(([^()]*)\)")
# A parenthesised length list: digits plus separators / qualifiers only.
_LENGTH_LIST_RE = re.compile(r"^[\d.,|~+\-×xXsS秒\s连续冰波]*\d[\d.,|~+\-×xXsS秒\s连续冰波]*$")
# Bare tail: "...ops 1976|1976|750|749" / "...ops 6|6|6|9|9" / "...ops 12s"
_BARE_TAIL_RE = re.compile(r"^(?P<ops>.*?[^\s,])[\s,]+(?P<lens>\d[\d.,|~+\-×xXsS秒\s连续冰波]*)$")
_LENGTH_ITEM_RE = re.compile(
    r"^(?P<a>\d+(?:\.\d+)?)(?:~(?P<b>\d+(?:\.\d+)?))?\s*(?P<unit>[sS秒])?\s*(?P<hint>[+\-])?"
    r"\s*(?:[×xX](?P<mult>\d+))?(?P<junk>.*)$"
)
_ICE_VARIANT_RE = re.compile(r"I(?:ce)?([0-4])", re.IGNORECASE)


def _normalize_text(text: str, notes: List[str]) -> str:
    """Apply the character-level rules of §3.1b and record them in ``notes``."""
    out = []
    seen_full = False
    seen_apos = False
    for ch in text:
        if ch in _FULLWIDTH:
            out.append(_FULLWIDTH[ch])
            seen_full = True
        elif ch in _APOSTROPHES:
            out.append("'")
            seen_apos = True
        elif ch in "\r\n\t":
            out.append(" ")
        else:
            out.append(ch)
    if seen_full:
        notes.append("fullwidth punctuation -> ASCII")
    if seen_apos:
        notes.append("backtick/’/₁ -> '")
    return "".join(out)


def _split_rhythm(text: str) -> Tuple[Optional[str], str]:
    """Split ``"P6: PP|PP"`` / ``"邻C6u I-PP|..."`` into (name, rest).

    The left part is taken as a rhythm name only when it looks like one
    (§3.1b "节奏名小写 / 带前缀"): short, no ``|``, family letter + digit.
    """
    if ":" in text:
        left, right = text.split(":", 1)
        if looks_like_rhythm_name(left):
            return left.strip(), right.strip()
    parts = text.split(None, 1)
    if (
        len(parts) == 2
        and looks_like_rhythm_name(parts[0])
        and not _WAVE_PREFIX_RE.search(text)
        # "I3PP 1976" is an op string with a bare length, not a rhythm name
        and not _LENGTH_LIST_RE.match(parts[1].strip())
    ):
        return parts[0], parts[1].strip()
    return None, text.strip()


# --- lengths ----------------------------------------------------------------
def _parse_length_item(item: str) -> Tuple[Optional[int], Optional[Tuple[int, int]], Optional[str], int]:
    """Parse one length item (§2.3 + §3.1b qualifiers).

    Returns ``(length_cs, length_range, hint, multiplicity)``. Unit is
    inferred per item (``<=100`` seconds, else cs) unless an explicit
    ``s``/``秒`` suffix is present.
    """
    m = _LENGTH_ITEM_RE.match(item.strip())
    if not m:
        raise ShorthandError("LENGTH_SYNTAX", f"cannot parse wave length {item!r}")
    junk = m.group("junk").strip()
    if junk and not re.fullmatch(r"[连续冰波\s]*", junk):
        raise ShorthandError("LENGTH_SYNTAX", f"unexpected {junk!r} in wave length {item!r}")

    def to_cs(v: str) -> int:
        if m.group("unit"):
            return int(round(float(v) * 100))
        return seconds_or_cs(float(v))

    a = to_cs(m.group("a"))
    mult = int(m.group("mult")) if m.group("mult") else 1
    if m.group("b"):
        return None, (a, to_cs(m.group("b"))), m.group("hint"), mult
    return a, None, m.group("hint"), mult


def _parse_length_list(text: str, notes: List[str]) -> List[Tuple[Optional[int], Optional[Tuple[int, int]], Optional[str]]]:
    items = [s for s in re.split(r"[,|\s]+", text.strip()) if s != ""]
    if len(items) != len(re.split(r"[,|\s]+", text.strip())):
        notes.append("empty length slot ignored")
    out = []
    for item in items:
        cs, rng, hint, mult = _parse_length_item(item)
        if mult != 1:
            notes.append(f"length {item!r} expanded x{mult}")
        if hint:
            notes.append(f"length qualifier {hint!r} on {item!r} kept as length_hint")
        out.extend([(cs, rng, hint)] * mult)
    return out


# --- operations ---------------------------------------------------------------
def _normalize_ops_text(text: str, notes: List[str]) -> str:
    """Symbol-level aliases: ``Ice3``→``I3``, ``io``→``IO``, ``+``→``-``."""
    s = text
    if "+" in s:
        s = s.replace("+", "-")
        notes.append("'+' -> '-'")
    s2 = re.sub(r"[Ii]ce([0-4])", r"I\1", s)
    if s2 != s:
        notes.append("Ice<n> -> I<n>")
        s = s2
    s2 = s.replace("io", "IO")  # §3.1b: ``io`` == ``IO``, also glued to other symbols ("ioPP")
    if s2 != s:
        notes.append("io -> IO")
        s = s2
    return s


def _tokenize_segment(text: str, dialect: str, phase: str) -> List[Op]:
    """Tokenise one '-'-free, '/'-free operation string into ``Op`` objects.

    ``I0`` (§3.1b, variant ``ice0``) is not in the symbol table, so it is
    cut out here and the rest goes through :func:`symbols.tokenize_ops`.
    """
    ops: List[Op] = []
    pos = 0
    for m in re.finditer(r"I0", text):
        ops.extend(_tokenize_plain(text[pos : m.start()], dialect, phase))
        ops.append(Op(symbol="I0", kind=KIND_ICE, variant="ice0", count=1, al=0, phase=phase))
        pos = m.end()
    ops.extend(_tokenize_plain(text[pos:], dialect, phase))
    return ops


def _tokenize_plain(text: str, dialect: str, phase: str) -> List[Op]:
    if not text.strip():
        return []
    try:
        toks = tokenize_ops(text, dialect)
    except ValueError as exc:
        raise ShorthandError("UNKNOWN_SYMBOL", f"{exc}") from None
    return [
        Op(symbol=sym, kind=info.kind, variant=info.variant, count=info.count,
           al=info.al_each * info.count, phase=phase)
        for sym, info in toks
    ]


def _parse_half(text: str, dialect: str) -> HalfWave:
    """半场 := 操作串 ['-' 操作串]. With several ``-`` (``BI-c-c-PPD``) the
    first segment is ``pre`` and everything after the first ``-`` is
    ``post`` — the refresh-triggering op always follows the first ``-``."""
    segs = text.split("-")
    if len(segs) == 1:
        return HalfWave(tuple(_tokenize_segment(segs[0], dialect, PHASE_POST)))
    ops = _tokenize_segment(segs[0], dialect, PHASE_PRE)
    for seg in segs[1:]:
        ops.extend(_tokenize_segment(seg, dialect, PHASE_POST))
    return HalfWave(tuple(ops))


def _wave_al(wave: Wave) -> int:
    return sum(op.al for op in wave.top.ops)


def _parse_wave(text: str, selector: Tuple[int, ...], dialect: str) -> Wave:
    raw = text.strip()
    if raw.count("/") > 1:
        raise ShorthandError("WAVE_SYNTAX", f"more than one '/' in wave {raw!r}")
    if "/" in raw:
        top_s, bot_s = raw.split("/")
        top = _parse_half(top_s, dialect)
        bottom: Optional[HalfWave] = _parse_half(bot_s, dialect)
    else:
        top = _parse_half(raw, dialect)
        bottom = None
    wave = Wave(selector=selector, top=top, bottom=bottom, raw=raw)
    # §3.1 rule 1: al < 2 and no '/' -> simplified, mirror to the other half
    mirrored = bottom is None and _wave_al(wave) < 2
    return replace(wave, mirrored=mirrored)


def _split_simplified(text: str, dialect: str) -> List[str]:
    """§3.1 rule 2: pure 简写 串 (no ``| - /``) — a wave closes as soon as the
    accumulated al reaches 1 (P/A = 1, N = 2, I/C = 0): ``PIPIPN`` →
    ``P|IP|IP|N``. Leftover ops that never reach 1 al are an error."""
    ops = _tokenize_segment(text, dialect, PHASE_POST)
    waves: List[str] = []
    cur: List[str] = []
    acc = 0
    for op in ops:
        cur.append(op.symbol)
        acc += op.al
        if acc >= 1:
            waves.append("".join(cur))
            cur = []
            acc = 0
    if cur:
        raise ShorthandError(
            "SIMPLIFIED_AMBIGUOUS",
            f"trailing {''.join(cur)!r} in {text!r} does not complete a wave (al < 1)",
        )
    return waves


# --- public API -----------------------------------------------------------------
def parse_shorthand(text: str, dialect: str = DEFAULT_DIALECT) -> Track:
    """Parse one shorthand line (§3) into ``Track(kind="shorthand")``.

    Raises :class:`ShorthandError` (a ``ValueError``) with ``code`` in
    ``UNKNOWN_SYMBOL``, ``SIMPLIFIED_AMBIGUOUS``, ``WAVE_SYNTAX``,
    ``LENGTH_SYNTAX``, ``EMPTY``.
    """
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}; expected one of {DIALECTS}")
    notes: List[str] = []
    warnings: List[Dict[str, Any]] = []
    annotations: List[str] = []

    s = _normalize_text(text, notes).strip()
    # trailing sentence punctuation ("...(1308|1308|1308)。")
    s2 = s.rstrip("。.;；,")
    if s2 != s:
        notes.append("trailing punctuation dropped")
        s = s2.strip()
    if not s:
        raise ShorthandError("EMPTY", "empty shorthand")

    name, body = _split_rhythm(s)
    rhythm: Optional[RhythmName] = parse_rhythm_name(name) if name else None

    # parentheses: the last numeric group is the length list, others are annotations
    length_text: Optional[str] = None
    tail_after_lengths = ""
    pieces: List[str] = []
    pos = 0
    for m in _PAREN_RE.finditer(body):
        inner = m.group(1)
        if _LENGTH_LIST_RE.match(inner.strip()):
            if length_text is not None:
                warnings.append({"code": "ANNOTATION_DROPPED", "message": f"extra length list ({length_text}) ignored"})
            pieces.append(body[pos : m.start()])
            length_text = inner
            pos = m.end()
            tail_after_lengths = body[pos:]
        else:
            annotations.append(inner.strip())
            warnings.append({"code": "ANNOTATION_DROPPED", "message": f"annotation ({inner.strip()}) dropped"})
            pieces.append(body[pos : m.start()])
            pos = m.end()
    if length_text is None:
        pieces.append(body[pos:])
        ops_text = "".join(pieces)
    else:
        ops_text = "".join(pieces)
        tail = tail_after_lengths.strip(" ,;")
        if tail:
            annotations.append(tail)
            warnings.append({"code": "ANNOTATION_DROPPED", "message": f"trailing text {tail!r} after length list dropped"})
    if annotations:
        notes.append("annotations stripped")
    ops_text = ops_text.strip().strip(",").strip()

    if length_text is None:
        m = _BARE_TAIL_RE.match(ops_text)
        if m:
            ops_text = m.group("ops")
            length_text = m.group("lens")
            notes.append("bare-tail length list")

    ops_text = _normalize_ops_text(ops_text, notes)

    # --- waves
    waves: List[Wave] = []
    prefix_hits = list(_WAVE_PREFIX_RE.finditer(ops_text))
    if prefix_hits:
        # §3.1b 'wN:' per-wave mode: selector = N
        notes.append("wN: per-wave prefix mode")
        for i, hit in enumerate(prefix_hits):
            end = prefix_hits[i + 1].start() if i + 1 < len(prefix_hits) else len(ops_text)
            chunk = ops_text[hit.end() : end].strip()
            n = int(hit.group(1))
            parts = [p for p in chunk.split("|") if p.strip()]
            if len(parts) > 1:
                warnings.append({"code": "WAVE_PREFIX_MULTI", "message": f"w{n}: contains '|'; all parts get selector ({n},)"})
            for part in parts:
                waves.append(_parse_wave(part, (n,), dialect))
    else:
        if "|" in ops_text:
            parts = [p for p in ops_text.split("|") if p.strip()]
            if len(parts) != ops_text.count("|") + 1:
                notes.append("empty waves from stray '|' dropped")
        elif "-" in ops_text or "/" in ops_text:
            parts = [ops_text]  # §3.1 rule 3: single wave
        else:
            parts = _split_simplified(ops_text, dialect)  # §3.1 rule 2
            if len(parts) > 1:
                notes.append("pure 简写 split by accumulated al")
        for i, part in enumerate(parts, 1):
            waves.append(_parse_wave(part, (i,), dialect))
    if not waves:
        raise ShorthandError("EMPTY", f"no waves in {text!r}")

    # --- lengths
    hints: List[Optional[str]] = [None] * len(waves)
    if length_text is not None:
        items = _parse_length_list(length_text, notes)
        if len(items) != len(waves):
            warnings.append({
                "code": "LENGTH_COUNT_MISMATCH",
                "message": f"{len(items)} lengths for {len(waves)} waves",
            })
        for i, (cs, rng, hint) in enumerate(items[: len(waves)]):
            waves[i] = replace(waves[i], length_cs=cs, length_range=rng)
            hints[i] = hint

    meta: Dict[str, Any] = {
        "raw": text,
        "normalized_from": notes,
        "annotations": annotations,
        "warnings": warnings,
        "length_hint": hints,
    }
    return Track(waves=tuple(waves), rhythm=rhythm, dialect=dialect, kind="shorthand", meta=meta)


def _dump_half(half: HalfWave) -> str:
    pre = "".join(op.symbol for op in half.ops if op.phase == PHASE_PRE)
    post = "".join(op.symbol for op in half.ops if op.phase == PHASE_POST)
    has_pre = any(op.phase == PHASE_PRE for op in half.ops)
    return f"{pre}-{post}" if has_pre else post


def _dump_length(wave: Wave) -> Optional[str]:
    def fmt(cs: int) -> str:
        """Shortest text that §2.3 reads back as ``cs``: whole seconds up to
        100s (``600`` -> ``6``), fractional seconds for ≤ 100cs (``50`` ->
        ``0.5``), otherwise cs (``12000`` stays ``12000``: ``120`` would be
        read as centiseconds)."""
        if cs <= 100:
            return repr(cs / 100)
        if cs % 100 == 0 and cs // 100 <= 100:
            return str(cs // 100)
        return str(cs)

    if wave.length_range is not None:
        return f"{fmt(wave.length_range[0])}~{fmt(wave.length_range[1])}"
    if wave.length_cs is not None:
        return fmt(wave.length_cs)
    return None


def dump_shorthand(track: Track) -> str:
    """Render a shorthand track back to canonical text, e.g.
    ``'P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)'``.

    Lengths that are whole seconds (≤ 100s) are written in seconds, others
    in cs (both re-parse to the same cs under §2.3). The length list is omitted
    unless every wave has one. Waves whose selector is not ``(i,)`` are
    written in ``wN:`` per-wave mode.
    """
    per_wave = any(w.selector != (i,) for i, w in enumerate(track.waves, 1))
    wave_texts = []
    for w in track.waves:
        t = _dump_half(w.top)
        if w.bottom is not None:
            t += "/" + _dump_half(w.bottom)
        if per_wave:
            t = f"w{w.selector[0]}:{t}"
        wave_texts.append(t)
    out = (" ".join if per_wave else "|".join)(wave_texts)
    if track.rhythm is not None:
        out = f"{track.rhythm.raw}: {out}"
    lengths = [_dump_length(w) for w in track.waves]
    if lengths and all(x is not None for x in lengths):
        out += " (" + "|".join(lengths) + ")"  # type: ignore[arg-type]
    return out
