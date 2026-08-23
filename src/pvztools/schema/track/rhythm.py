"""Rhythm-name parser (docs/track-format.md §3.2).

A rhythm name such as ``P6`` ``ch5`` ``邻C6u`` ``C6i*`` ``P6-2I`` ``C4-io`` is
decomposed into :class:`RhythmName(family, param, prefix, suffixes)`.

Families (``docs/research/track-corpus.md`` "Rhythm-name grammar"):

* ``P`` / ``S`` / ``C`` / ``ch`` / ``I`` — as written (case-insensitive);
* ``Cu`` — a ``C`` name whose first suffix letter is ``u`` (night double-ice
  series, the ``u`` is part of the class name and is *not* repeated in
  ``suffixes``);
* ``T`` is the legacy spelling of ``P`` and is mapped to ``P``;
* anything else (``D4``, ``N6``, free text) → family ``"?"`` with ``raw`` kept.

Parsing never raises.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .ast import RhythmName

__all__ = ["parse_rhythm_name", "looks_like_rhythm_name", "RHYTHM_RE"]

# prefix = everything before the first ASCII letter (Chinese words, spaces)
# family = ch | one of P C S I T D N ; param = digits ; rest = suffix soup
RHYTHM_RE = re.compile(
    r"^(?P<prefix>[^A-Za-z]*?)\s*(?P<family>ch|[PCSITDN])(?P<param>\d+)?(?P<rest>.*)$",
    re.IGNORECASE,
)

_KNOWN_FAMILIES = ("P", "C", "S", "ch", "I")
_LEGACY_FAMILIES = {"T": "P"}


def looks_like_rhythm_name(text: str) -> bool:
    """True when ``text`` is plausibly a rhythm name (used by the shorthand
    parser to decide whether the part before ``:`` / the first word is a name
    rather than operations). Requires a family letter followed by a digit and
    forbids wave separators."""
    s = text.strip()
    if not s or len(s) > 16 or "|" in s or "/" in s:
        return False
    m = RHYTHM_RE.match(s)
    if not m or m.group("param") is None:
        return False
    rest = m.group("rest")
    # suffix soup may only contain letters, '*', '-', digits and spaces
    return re.fullmatch(r"[A-Za-z0-9*\-\s]*", rest) is not None


def _split_suffixes(rest: str) -> Tuple[List[str], bool]:
    """Split the suffix soup after the param into tokens.

    Returns ``(suffixes, leading_u)`` where ``leading_u`` is True when the
    first letter-suffix is ``u`` (needed for the ``Cu`` family decision).
    """
    parts = rest.strip().split("-")
    head = parts[0].strip()
    suffixes: List[str] = []
    pos = 0
    while pos < len(head):
        ch = head[pos]
        if head[pos : pos + 2].lower() == "io":
            suffixes.append("io")
            pos += 2
        elif ch.isspace():
            pos += 1
        else:
            suffixes.append(ch.lower() if ch.isalpha() else ch)
            pos += 1
    leading_u = bool(suffixes) and suffixes[0] == "u"
    for seg in parts[1:]:
        seg = seg.strip()
        if not seg:
            continue
        low = seg.lower()
        if low == "io":
            suffixes.append("-io")
        elif re.fullmatch(r"\d*[in]", low):
            # -N / -2I / -3I : card-count suffixes are upper-cased
            suffixes.append("-" + low.upper())
        else:
            suffixes.append("-" + seg)
    return suffixes, leading_u


def parse_rhythm_name(text: str) -> RhythmName:
    """Parse a rhythm name (§3.2). Case-insensitive; never raises.

    >>> parse_rhythm_name("邻C6u")
    RhythmName(family='Cu', param=6, prefix='邻', suffixes=(), raw='邻C6u')
    >>> parse_rhythm_name("ch4-N").suffixes
    ('-N',)
    """
    raw = text.strip()
    m = RHYTHM_RE.match(raw)
    if not m:
        return RhythmName(family="?", param=None, prefix="", suffixes=(), raw=raw)
    prefix = "".join(m.group("prefix").split())
    fam = m.group("family")
    family = "ch" if fam.lower() == "ch" else fam.upper()
    family = _LEGACY_FAMILIES.get(family, family)
    param: Optional[int] = int(m.group("param")) if m.group("param") else None
    suffixes, leading_u = _split_suffixes(m.group("rest"))
    if family == "C" and leading_u:
        family = "Cu"
        suffixes = suffixes[1:]
    if family not in _KNOWN_FAMILIES and family != "Cu":
        family = "?"
    return RhythmName(
        family=family,
        param=param,
        prefix=prefix,
        suffixes=tuple(suffixes),
        raw=raw,
    )
