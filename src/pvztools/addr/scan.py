"""AOB scanning (DESIGN §3.2 L3): run the anchor records over ``.text`` and
turn the immediates found in instructions into an address/offset set.

Entry points
------------
``scan_image(text, text_va, data_range, anchors=DEFAULT_ANCHORS)``
    Pure function over bytes; ``found`` keys use the same dotted names as
    ``Profile.as_address_book()`` (``globals.lawn_app`` VA, ``lawn_app.board``,
    ``board.sun``, ``board.shift``, ``functions.put_plant`` VA,
    ``patches.<group>.<i>`` VA).
``scan_exe(path)``
    Reads a game exe (plain or PopCap DRM launcher) via ``_win.pe``.
``scan_live(session)``
    Reads ``.text`` out of the running process through ``VirtualQueryEx``
    regions (never the file) and scans that.

A full Steam exe scan takes well under a second: everything is ``re`` over
``bytes``; the only Python loop over hits is the (few hundred) global loads.
"""
from __future__ import annotations

import collections
import re
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import anchors as A

__all__ = ["ScanResult", "scan_image", "scan_exe", "scan_live", "read_live_text"]

Reader = Callable[[int, int], bytes]


@dataclass
class ScanResult:
    found: Dict[str, int] = field(default_factory=dict)
    confidence: Dict[str, float] = field(default_factory=dict)
    evidence: Dict[str, List[str]] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    family: Optional[str] = None          # LawnApp offset family ("retail"/"goty"/"goty_zh")
    #: every family that scored the same as ``family`` (len > 1 == ambiguous)
    family_candidates: List[str] = field(default_factory=list)
    text_range: Tuple[int, int] = (0, 0)
    data_range: Tuple[int, int] = (0, 0)
    elapsed: float = 0.0

    def _add(self, name: str, value: int, conf: float, *why: str) -> None:
        self.found[name] = value
        self.confidence[name] = max(0.0, min(1.0, conf))
        self.evidence.setdefault(name, []).extend(why)

    def _miss(self, name: str, *why: str) -> None:
        if name not in self.missing:
            self.missing.append(name)
        self.evidence.setdefault(name, []).extend(why)

    def get(self, name: str, default: Optional[int] = None) -> Optional[int]:
        return self.found.get(name, default)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "found": {k: v for k, v in self.found.items()},
            "confidence": dict(self.confidence),
            "evidence": {k: list(v) for k, v in self.evidence.items()},
            "missing": list(self.missing),
            "family": self.family,
            "family_candidates": list(self.family_candidates),
            "text_range": list(self.text_range),
            "data_range": list(self.data_range),
            "elapsed": self.elapsed,
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
_FMT = {"u8": "<B", "u16": "<H", "u32": "<I", "i32": "<i"}


def _extract(text: bytes, pos: int, fmt: str, off: int) -> Optional[int]:
    p = pos + off
    if p < 0:
        return None
    try:
        return struct.unpack_from(_FMT[fmt], text, p)[0]
    except struct.error:
        return None


def _mode(votes: Dict[int, int]) -> Tuple[Optional[int], int, int]:
    """(winner, winner_votes, total_votes)."""
    if not votes:
        return None, 0, 0
    total = sum(votes.values())
    winner = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))
    return winner[0], winner[1], total


# ---------------------------------------------------------------------------
# (1) sun
# ---------------------------------------------------------------------------
#: A Board field referenced fewer times than this by ``[reg+disp32]``
#: instructions is not believable as the sun (the real one has dozens).
SUN_MIN_DIRECT_REFS = 3


def _direct_refs(text: bytes, disp: int) -> int:
    """DESIGN §3.3 cross-check: count ``op [reg+disp32]`` (mod=10, no SIB)
    instructions that touch ``disp`` directly -- ``mov eax,[ecx+sun]`` and
    friends -- independent of the 9990 clamp constant."""
    pat = A.MODRM_DISP32 + re.escape(struct.pack("<I", disp))
    return sum(1 for _ in re.finditer(pat, text, re.DOTALL))


def _scan_sun(text: bytes, text_va: int, anchors: Sequence[A.Anchor], res: ScanResult) -> Optional[int]:
    votes: Dict[int, int] = collections.Counter()
    shapes: Dict[int, set] = collections.defaultdict(set)
    why: List[str] = []
    for an in anchors:
        for m in an.compiled().finditer(text):
            if an.meta.get("alt_groups"):
                raw = next((g for g in m.groups() if g is not None), None)
                disp = struct.unpack("<I", raw)[0] if raw else None
            else:
                disp = _extract(text, m.start(), "u32", an.extract[0][1])
            if disp is None or not (0x1000 <= disp < 0x8000):
                continue
            votes[disp] += 1
            shapes[disp].add(an.name)
            why.append("%s @0x%X -> 0x%X" % (an.name, text_va + m.start(), disp))
    sun, n, total = _mode(votes)
    if sun is None:
        res._miss("board.sun", "no 9990 (0x2706) comparison found in .text")
        return None
    agree = n / float(total)
    if agree < 0.5:
        res._miss("board.sun", "sun votes disagree: %s" % {hex(k): v for k, v in votes.items()})
        return None
    conf = 0.5 + 0.3 * min(n, 3) / 3.0 + 0.2 * agree
    refs = _direct_refs(text, sun)
    if refs < SUN_MIN_DIRECT_REFS:
        # the clamp points at a field nobody else reads: not the sun
        res._miss("board.sun", "0x%X wins the 9990 vote (%d/%d) but only %d direct [reg+0x%X] "
                  "references (need >= %d)" % (sun, n, total, refs, sun, SUN_MIN_DIRECT_REFS), *why)
        return None
    conf = min(1.0, conf + 0.05 * min(refs, 4) / 4.0)
    res._add("board.sun", sun, conf, *why)
    res.evidence["board.sun"].append("votes=%d/%d shapes=%s" % (n, total, sorted(shapes[sun])))
    res.evidence["board.sun"].append("cross-check: %d direct [reg+0x%X] references" % (refs, sun))
    return sun


# ---------------------------------------------------------------------------
# (2) LawnApp global by offset family
# ---------------------------------------------------------------------------
def _family_score(disps: Dict[int, int]) -> Tuple[Optional[str], int, int, Dict[str, int]]:
    """Return (family, core_hits, extra_hits, matched_fields) for the best family.
    Ties (same core and extra counts) are kept in declaration order; use
    :func:`_family_candidates` to know whether the pick was ambiguous."""
    cands = _family_candidates(disps)
    if not cands:
        return (None, 0, 0, {})
    return cands[0]


def _family_candidates(disps: Dict[int, int]) -> List[Tuple[str, int, int, Dict[str, int]]]:
    """Every family whose (core, extra) score equals the best one."""
    scored = []
    for fam, spec in A.LAWN_FAMILIES.items():
        core = {k: v for k, v in spec["core"].items() if disps.get(v, 0) > 0}
        extra = {k: v for k, v in spec["extra"].items() if disps.get(v, 0) > 0}
        if not core:
            continue
        matched = dict(core)
        matched.update(extra)
        scored.append((fam, len(core), len(extra), matched))
    if not scored:
        return []
    best = max((c, e) for _f, c, e, _m in scored)
    return [t for t in scored if (t[1], t[2]) == best]


def _scan_lawn(text: bytes, text_va: int, data_range: Tuple[int, int],
               load_anchors: Sequence[A.Anchor], null_anchors: Sequence[A.Anchor],
               res: ScanResult) -> Optional[int]:
    lo, hi = data_range
    per_global: Dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    loads: collections.Counter = collections.Counter()
    for an in load_anchors:
        window = int(an.meta.get("window", 24))
        for m in an.compiled().finditer(text):
            g = struct.unpack("<I", m.group(1))[0]
            if not (lo <= g < hi):
                continue
            loads[g] += 1
            first = text[m.start()]
            reg = 0 if first == 0xA1 else (text[m.start() + 1] >> 3) & 7
            s = m.end()
            win = text[s:s + window]
            seen = set()
            want = 0x80 | reg
            for i in range(1, len(win) - 4):
                if (win[i] & 0xC7) == want:
                    d = struct.unpack_from("<I", win, i + 1)[0]
                    if d < 0x2000 and d not in seen:
                        seen.add(d)
                        per_global[g][d] += 1
    null_votes: collections.Counter = collections.Counter()
    for an in null_anchors:
        for m in an.compiled().finditer(text):
            g = struct.unpack("<I", m.group(1))[0]
            if lo <= g < hi:
                null_votes[g] += 1

    ranked = []
    for g, disps in per_global.items():
        fam, core, extra, matched = _family_score(disps)
        if core >= 2:
            ranked.append((core, extra, null_votes.get(g, 0), loads[g], g, fam, matched))
    ranked.sort(reverse=True)
    if not ranked:
        top = [hex(g) for g, _ in loads.most_common(5)]
        res._miss("globals.lawn_app",
                  "no global matched a LawnApp offset family (top loads by count: %s)" % top)
        return None
    core, extra, nulls, nloads, g, fam, matched = ranked[0]
    conf = 0.4 + 0.15 * core + 0.03 * min(extra, 4) + (0.05 if nulls else 0.0)
    if len(ranked) > 1 and ranked[1][0] == core and ranked[1][1] == extra:
        conf -= 0.2
        res.evidence.setdefault("globals.lawn_app", []).append(
            "ambiguous: 0x%X also matches family %s" % (ranked[1][4], ranked[1][5]))
    cands = [c[0] for c in _family_candidates(per_global[g])]
    res.family = fam
    res.family_candidates = cands
    ambiguous_fields: set = set()
    if len(cands) > 1:
        # same global, several families with the same score (e.g. goty vs
        # goty_zh when only board + one of game_mode/game_ui was observed):
        # the offsets the families disagree on cannot be trusted
        conf -= 0.2
        for other in cands[1:]:
            for fld, off in list(A.LAWN_FAMILIES[other]["core"].items()) + list(A.LAWN_FAMILIES[other]["extra"].items()):
                mine = A.LAWN_FAMILIES[fam]["core"].get(fld, A.LAWN_FAMILIES[fam]["extra"].get(fld))
                if mine != off:
                    ambiguous_fields.add(fld)
        res.evidence.setdefault("globals.lawn_app", []).append(
            "ambiguous family: %s score the same (core=%d extra=%d); picked %s, fields %s unresolved"
            % ("/".join(cands), core, extra, fam, sorted(ambiguous_fields)))
    res._add("globals.lawn_app", g, conf,
             "family=%s core=%d/3 extra=%d null_checks=%d loads=%d" % (fam, core, extra, nulls, nloads),
             "matched " + ", ".join("%s=0x%X(%d)" % (k, v, per_global[g][v]) for k, v in sorted(matched.items())),
             "most-referenced global was 0x%X (%d loads) — hit count is not the criterion"
             % (loads.most_common(1)[0][0], loads.most_common(1)[0][1]))
    # LawnApp fields seen for the chosen global
    spec = A.LAWN_FAMILIES[fam]
    for name, off in list(spec["core"].items()) + list(spec["extra"].items()):
        n = per_global[g].get(off, 0)
        if n > 0:
            res._add("lawn_app." + name, off, min(1.0, 0.6 + 0.1 * n),
                     "disp32 0x%X dereferenced %d× after loading [0x%X]" % (off, n, g))
        elif name in ambiguous_fields:
            # the tied families disagree here and nothing was observed
            res._add("lawn_app." + name, off, 0.3,
                     "inferred from family %s but families %s disagree (not observed)" % (fam, "/".join(cands)))
            res._miss("lawn_app." + name, "ambiguous between families %s" % "/".join(cands))
        else:
            # family member not observed directly: inferred from the family
            res._add("lawn_app." + name, off, 0.5, "inferred from family %s (not observed)" % fam)
    return g


# ---------------------------------------------------------------------------
# (3) Board shift
# ---------------------------------------------------------------------------
def _scan_board_shift(text: bytes, sun: Optional[int], anchor: A.Anchor, res: ScanResult) -> Optional[int]:
    hist: collections.Counter = collections.Counter()
    for m in anchor.compiled().finditer(text):
        hist[struct.unpack("<I", m.group(1))[0]] += 1
    known: Dict[str, int] = anchor.meta["known"]
    lo, hi = anchor.meta["range"]
    best: List[Tuple[int, int, int]] = []
    for s in range(lo, hi + 1):
        pres = sum(1 for k in known.values() if hist.get(k + s, 0) > 0)
        wt = sum(min(hist.get(k + s, 0), 20) for k in known.values())
        best.append((wt, pres, s))
    best.sort(reverse=True)
    hist_shift = best[0][2] if best and best[0][1] >= len(known) // 2 else None
    sun_shift = None if sun is None else sun - A.SUN_1051
    why = ["histogram best shifts: %s" % [(hex(s & 0xFFFFFFFF) if s < 0 else hex(s), w, p) for w, p, s in best[:3]],
           "sun-derived shift: %s" % (None if sun_shift is None else hex(sun_shift))]
    if sun_shift is not None and hist_shift == sun_shift:
        res._add("board.shift", sun_shift, 0.95, *why)
        shift = sun_shift
    elif sun_shift is not None and hist_shift is None:
        res._add("board.shift", sun_shift, 0.6, "histogram inconclusive", *why)
        shift = sun_shift
    elif sun_shift is None and hist_shift is not None:
        res._add("board.shift", hist_shift, 0.5, "no sun anchor; histogram only", *why)
        shift = hist_shift
        # no 9990 clamp (raised-cap mods): derive the sun from the shift and
        # require the DESIGN §3.3 direct-access evidence before believing it
        cand = A.SUN_1051 + shift
        refs = _direct_refs(text, cand)
        if refs >= SUN_MIN_DIRECT_REFS:
            res.missing = [m for m in res.missing if m != "board.sun"]
            res._add("board.sun", cand, 0.5,
                     "no 9990 clamp found; 1051 0x%X + histogram shift 0x%X, %d direct [reg+0x%X] references"
                     % (A.SUN_1051, shift, refs, cand))
        else:
            res._miss("board.sun", "histogram shift 0x%X suggests 0x%X but only %d direct references"
                      % (shift, cand, refs))
    else:
        res._miss("board.shift", "sun-derived and histogram shifts disagree", *why)
        return None
    for name, off in known.items():
        if name == "sun":
            continue
        res._add("board." + name, off + shift, 0.75 if hist.get(off + shift, 0) else 0.55,
                 "1051 0x%X + shift 0x%X (%d refs)" % (off, shift, hist.get(off + shift, 0)))
    return shift


# ---------------------------------------------------------------------------
# (4)/(5) functions and patches
# ---------------------------------------------------------------------------
def _scan_unique(text: bytes, text_va: int, anchors: Sequence[A.Anchor], res: ScanResult) -> None:
    for an in anchors:
        hits = [m.start() for m in an.compiled().finditer(text)]
        if len(hits) != 1:
            res._miss(an.name, "%d hits (expected exactly 1)" % len(hits))
            continue
        pos = hits[0]
        if an.kind == "patch":
            so = int(an.meta["site_offset"])
            off = bytes.fromhex(an.meta["off"])
            cur = text[pos + so:pos + so + len(off)]
            if cur != off:
                res._miss(an.name, "site found @0x%X but bytes %s != expected off %s"
                          % (text_va + pos + so, cur.hex(), off.hex()))
                continue
            res._add(an.name, text_va + pos + so, 0.9,
                     "unique context hit, off bytes verified (%s)" % off.hex())
        else:
            res._add(an.name, text_va + pos, 0.9, "unique prologue hit @0x%X" % (text_va + pos))


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------
def scan_image(text: bytes, text_va: int, data_range: Tuple[int, int],
               anchors: Optional[Iterable[A.Anchor]] = None) -> ScanResult:
    """Scan a ``.text`` image.  ``data_range`` is the VA range of the
    writable data section(s) (globals must fall inside it)."""
    t0 = time.perf_counter()
    anc = list(anchors) if anchors is not None else list(A.DEFAULT_ANCHORS)
    by_kind: Dict[str, List[A.Anchor]] = collections.defaultdict(list)
    for an in anc:
        by_kind[an.kind].append(an)
    res = ScanResult(text_range=(text_va, text_va + len(text)), data_range=tuple(data_range))

    sun = _scan_sun(text, text_va, by_kind.get("sun_cap", ()), res)
    _scan_lawn(text, text_va, data_range, by_kind.get("lawn_global", ()),
               by_kind.get("null_check", ()), res)
    shift_anchor = by_kind.get("board_shift", [None])[0]
    if shift_anchor is not None:
        _scan_board_shift(text, sun, shift_anchor, res)
    _scan_unique(text, text_va, by_kind.get("function", ()), res)
    _scan_unique(text, text_va, by_kind.get("patch", ()), res)
    res.elapsed = time.perf_counter() - t0
    return res


def _data_range_of(pe: Any) -> Tuple[int, int]:
    """Union of writable, non-code sections (``.data``/``.bss``)."""
    lo, hi = 0, 0
    for s in pe.sections:
        if s.chars & A_SCN_WRITE and not (s.chars & A_SCN_CODE):
            if lo == 0 or s.va < lo:
                lo = s.va
            hi = max(hi, s.end)
    if lo == 0:
        d = pe.section(".data")
        if d is not None:
            lo, hi = d.va, d.end
    return lo, hi


A_SCN_WRITE = 0x80000000
A_SCN_CODE = 0x00000020


def scan_exe(path: Any, anchors: Optional[Iterable[A.Anchor]] = None) -> Tuple[ScanResult, Any]:
    """Scan an exe on disk (plain image or DRM launcher).  Returns
    ``(ScanResult, PEInfo)``."""
    from .._win.pe import extract_game_pe
    data = Path(path).read_bytes()
    _off, pe, img = extract_game_pe(data)
    t = pe.text
    if t is None:
        raise ValueError("%s: no code section" % path)
    text = img[t.raw_ptr:t.raw_ptr + t.raw_size]
    res = scan_image(text, t.va, _data_range_of(pe), anchors)
    return res, pe


def read_live_text(session: Any, regions: Optional[Iterable[Any]] = None) -> Tuple[bytes, int, Any]:
    """Read the code section of the live process.  Uses the PE header in
    memory for the section table and ``VirtualQueryEx`` regions (readable,
    committed) intersected with the section for the bytes.  Returns
    ``(text_bytes, text_va, PEInfo)``."""
    from .._win.pe import read_live_pe
    pe = read_live_pe(session.read, session.image_base)
    t = pe.text
    if t is None:
        raise ValueError("live image has no code section")
    start, end = t.va, t.va + t.vsize
    if regions is None:
        from .._win import kernel32
        regions = kernel32.iter_regions(session.handle, start, end)
    buf = bytearray(end - start)
    got = 0
    for r in regions:
        if not getattr(r, "readable", True):
            continue
        r_lo = max(r.base, start)
        r_hi = min(r.base + r.size, end)
        if r_hi <= r_lo:
            continue
        pos = r_lo
        while pos < r_hi:
            n = min(0x40000, r_hi - pos)
            chunk = session.read(pos, n)
            buf[pos - start:pos - start + len(chunk)] = chunk
            got += len(chunk)
            pos += n
    if got == 0:
        raise ValueError("no readable region overlaps the code section")
    return bytes(buf), start, pe


def scan_live(session: Any, anchors: Optional[Iterable[A.Anchor]] = None,
              regions: Optional[Iterable[Any]] = None) -> ScanResult:
    """Scan the running game's code section (read from process memory)."""
    text, text_va, pe = read_live_text(session, regions)
    return scan_image(text, text_va, _data_range_of(pe), anchors)
