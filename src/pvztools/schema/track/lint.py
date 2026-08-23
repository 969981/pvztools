"""Track linter (docs/track-format.md §6).

``lint(track, scene=None)`` returns a list of :class:`Diagnostic` and never
raises. Every rule is a small function; which rules run depends on the
track kind:

* **shorthand** tracks carry no effect times and their ``selector`` is a
  cycle position, not a wave number, so only the structural checks apply:
  ``AL_BELOW_2``, ``LEN_RANGE``, ``RHYTHM_MISMATCH``, ``SIMPLIFIED_AMBIGUOUS``.
* **pvzt** tracks additionally get the timeline checks (``COB_CD``,
  ``CARD_CD``), the scene / target checks (``ICE_DAY_NEEDS_COFFEE``,
  ``POOL_NEEDS_LILY``, ``ROOF_NO_DOOM``, ``TARGET_GRID``) and
  ``WAVE_OUT_OF_RANGE`` for selectors beyond w20.

Beyond the §6 table: ``SCENE_UNKNOWN`` (error) replaces the exception an
unknown scene code would otherwise raise.

All numbers come from :mod:`pvztools.schema.timing`.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..enums import PlantType, Scene, parse_cards
from ..lineup import decode_lineup_code
from ..timing import (
    COB_CD,
    FINAL_WAVE_COUNTDOWN,
    FODDER_CD,
    HUGE_WAVE_MAX_LEN,
    ICE_ACTIVATE_DAY,
    WAVE_MIN_LEN,
    WAVE_NATURAL_MAX,
    card_cd,
)
from .ast import (
    KIND_ASH,
    KIND_COB,
    KIND_DOOM,
    KIND_FODDER,
    KIND_ICE,
    Diagnostic,
    Op,
    Track,
    Wave,
)

__all__ = ["lint", "wave_al", "ERROR", "WARNING", "INFO"]

ERROR = "error"
WARNING = "warning"
INFO = "info"

#: §1: 2 al trigger one refresh.
AL_REQUIRED = 2
#: §6 TARGET_GRID: columns are on a 1/80 grid.
COL_GRID = 80
COL_MIN, COL_MAX = 1, 9
#: §3.1 rule 1: only cob / ash ops are mirrored to the symmetric half.
MIRRORED_KINDS = (KIND_COB, KIND_ASH)
#: §1: one selection is 20 waves.
MAX_WAVE = 20

# --- timeline event: (abs_cs, wave, wave-relative cs, op, segment) -----------
_Event = Tuple[int, int, int, Op, int]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ops(wave: Wave) -> List[Op]:
    """All ops of a wave, top half then bottom half."""
    ops = list(wave.top.ops)
    if wave.bottom is not None:
        ops.extend(wave.bottom.ops)
    return ops


def wave_al(wave: Wave) -> int:
    """运算量 of a wave "写满后" (§3.1): mirrored 简写 waves count their
    cob / ash ops twice because they stand for both symmetric halves."""
    total = 0
    for op in _ops(wave):
        total += op.al
        if wave.mirrored and op.kind in MIRRORED_KINDS:
            total += op.al
    return total


def _is_ice_wave(wave: Wave) -> bool:
    return any(op.kind == KIND_ICE for op in _ops(wave))


def _wave_label(wave: Wave) -> Optional[int]:
    return wave.selector[0] if wave.selector else None


def _resolve_scene(track: Track, scene: Optional[str], out: List[Diagnostic]) -> Optional[Scene]:
    """Scene from the override or the track; an unknown code becomes an
    ``SCENE_UNKNOWN`` error (lint never raises) and disables scene rules."""
    code = scene if scene is not None else track.scene
    if code is None:
        return None
    try:
        return Scene.from_code(code)
    except ValueError:
        out.append(Diagnostic(ERROR, "SCENE_UNKNOWN", f"unknown scene code {code!r}; scene rules skipped"))
        return None


# ---------------------------------------------------------------------------
# structural checks (shorthand + pvzt)
# ---------------------------------------------------------------------------


def _check_al(track: Track) -> List[Diagnostic]:
    """``AL_BELOW_2`` (warning): a non-ice wave with al < 2 after mirror expansion.

    ``SIMPLIFIED_AMBIGUOUS`` (error) is raised instead for shorthand waves
    that were flagged ``mirrored`` but contain nothing mirrorable (e.g. a
    bare ``C`` wave): mirroring cannot make them reach 2 al, so the 简写
    split is unresolvable rather than merely thin.
    """
    out: List[Diagnostic] = []
    for wave in track.waves:
        if _is_ice_wave(wave):
            continue
        al = wave_al(wave)
        if al >= AL_REQUIRED:
            continue
        w = _wave_label(wave)
        if track.kind == "shorthand" and wave.mirrored and al == 0:
            out.append(Diagnostic(ERROR, "SIMPLIFIED_AMBIGUOUS",
                                  f"wave {wave.raw!r} has no cob/ash op to mirror; cannot reach {AL_REQUIRED} al", w))
            continue
        out.append(Diagnostic(WARNING, "AL_BELOW_2",
                              f"non-ice wave has {al} al after expansion (< {AL_REQUIRED})", w))
    return out


def _check_simplified_meta(track: Track) -> List[Diagnostic]:
    """Surface parser-recorded ``SIMPLIFIED_AMBIGUOUS`` warnings (``meta['warnings']``)."""
    out: List[Diagnostic] = []
    for item in track.meta.get("warnings", ()):
        if isinstance(item, dict) and item.get("code") == "SIMPLIFIED_AMBIGUOUS":
            out.append(Diagnostic(ERROR, "SIMPLIFIED_AMBIGUOUS", str(item.get("message", ""))))
    return out


def _len_bounds(wave_no: Optional[int]) -> Tuple[int, int]:
    """§1 wave-length interval for an actual wave number (None = unknown position)."""
    if wave_no == 20:
        return FINAL_WAVE_COUNTDOWN, FINAL_WAVE_COUNTDOWN
    if wave_no in (9, 19):
        return WAVE_MIN_LEN, HUGE_WAVE_MAX_LEN
    # w10 is a flag wave but its countdown to w11 is an ordinary one; the
    # lengthened limits belong to the waves *before* a flag (w9 / w19).
    return WAVE_MIN_LEN, WAVE_NATURAL_MAX


def _check_len(track: Track) -> List[Diagnostic]:
    """``LEN_RANGE`` (error): §1 / §6 wave-length limits.

    Shorthand selectors are cycle positions, so the ordinary interval is
    used; a shorthand length that would be legal for a closing wave
    (≤ 5500) is downgraded to a warning because the cycle may well be
    describing w9/w19/w20.

    Whole-second lengths (``6`` → 600cs) are the community's rounding of
    the 601cs minimum, so a value that is a multiple of 100cs may fall
    short of the lower bound by one tick.
    """
    out: List[Diagnostic] = []
    for wave in track.waves:
        if wave.length_cs is not None:
            lo = hi = wave.length_cs
        elif wave.length_range is not None:
            lo, hi = wave.length_range
        else:
            continue
        for n in (wave.selector if track.kind == "pvzt" else (None,)):
            bmin, bmax = _len_bounds(n)
            slack = 1 if lo % 100 == 0 else 0
            if bmin - slack <= lo and hi <= bmax:
                continue
            shown = f"{lo}" if lo == hi else f"{lo}~{hi}"
            level = ERROR
            if track.kind == "shorthand" and lo >= WAVE_MIN_LEN - slack and hi <= FINAL_WAVE_COUNTDOWN:
                level = WARNING
            if bmin == bmax:
                msg = f"w{n} length {shown}cs must be {bmin}"
            else:
                msg = f"length {shown}cs outside [{bmin}, {bmax}]"
            out.append(Diagnostic(level, "LEN_RANGE", msg, n if n is not None else _wave_label(wave)))
    return out


def _check_wave_numbers(track: Track) -> List[Diagnostic]:
    """``WAVE_OUT_OF_RANGE`` (warning): a pvzt selector beyond w20 (§1: one
    selection is 20 waves). Overlapping blocks are not reported: the spec's
    own example re-defines w1 in a later block (the later block wins, §7)."""
    out: List[Diagnostic] = []
    if track.kind != "pvzt":
        return out
    for wave in track.waves:
        bad = [n for n in wave.selector if n > MAX_WAVE]
        if bad:
            out.append(Diagnostic(WARNING, "WAVE_OUT_OF_RANGE",
                                  f"selector covers w{bad[0]} beyond w{MAX_WAVE}", bad[0]))
    return out


def _check_rhythm(track: Track) -> List[Diagnostic]:
    """``RHYTHM_MISMATCH`` (warning), §3.2.

    Interpretation (the spec only gives examples): ``P``/``S`` names carry
    the wave count as their parameter (``P6`` = 6 waves, ``P5u`` = 5);
    ``ch``/``I`` names must contain at least one ice wave; a ``-kI`` suffix
    asks for exactly ``k`` ice waves and ``-N`` for a doom op. ``C``/``Cu``
    parameters are cannon counts, not wave counts, so they are not checked.
    Unknown families only warn.
    """
    r = track.rhythm
    if r is None:
        return []
    out: List[Diagnostic] = []
    n_waves = len(track.waves)
    n_ice = sum(1 for w in track.waves if _is_ice_wave(w))
    has_doom = any(op.kind == KIND_DOOM for w in track.waves for op in _ops(w))
    if r.family == "?":
        out.append(Diagnostic(WARNING, "RHYTHM_MISMATCH", f"unknown rhythm name {r.raw!r}"))
        return out
    if r.family in ("P", "S") and r.param is not None and r.param != n_waves:
        out.append(Diagnostic(WARNING, "RHYTHM_MISMATCH",
                              f"{r.raw}: {r.family} rhythm expects {r.param} waves, track has {n_waves}"))
    if r.family in ("ch", "I") and n_ice == 0:
        out.append(Diagnostic(WARNING, "RHYTHM_MISMATCH",
                              f"{r.raw}: {r.family} rhythm expects an ice wave, track has none"))
    for suf in r.suffixes:
        if suf == "-N" and not has_doom:
            out.append(Diagnostic(WARNING, "RHYTHM_MISMATCH", f"{r.raw}: '-N' suffix but no doom op"))
        elif suf.startswith("-") and suf.endswith("I") and suf[1:-1].isdigit():
            k = int(suf[1:-1])
            if k != n_ice:
                out.append(Diagnostic(WARNING, "RHYTHM_MISMATCH",
                                      f"{r.raw}: '{suf}' suffix expects {k} ice waves, track has {n_ice}"))
    return out


# ---------------------------------------------------------------------------
# pvzt-only checks
# ---------------------------------------------------------------------------


def _check_targets(track: Track, scene: Optional[Scene]) -> List[Diagnostic]:
    """``TARGET_GRID`` (error): column on the 1/80 grid inside 1–9 and, when
    the scene is known, rows inside the field."""
    out: List[Diagnostic] = []
    for wave in track.waves:
        w = _wave_label(wave)
        for op in _ops(wave):
            t = op.target
            if t is None:
                continue
            col = t.col
            steps = col * COL_GRID
            if not (COL_MIN <= col <= COL_MAX):
                out.append(Diagnostic(ERROR, "TARGET_GRID",
                                      f"column {col} outside {COL_MIN}-{COL_MAX}", w, op.symbol))
            elif abs(steps - round(steps)) > 1e-6:
                out.append(Diagnostic(ERROR, "TARGET_GRID",
                                      f"column {col} is not a multiple of 1/{COL_GRID}", w, op.symbol))
            if scene is not None:
                bad = [r for r in t.rows if r < 1 or r > scene.rows]
                if bad:
                    out.append(Diagnostic(ERROR, "TARGET_GRID",
                                          f"row {bad[0]} outside 1-{scene.rows} on {scene.code}", w, op.symbol))
    return out


def _check_scene(track: Track, scene: Scene) -> List[Diagnostic]:
    """``ICE_DAY_NEEDS_COFFEE`` (info, once), ``ROOF_NO_DOOM`` (error),
    ``POOL_NEEDS_LILY`` (warning).

    The format has no lily marker, so a non-cob plant landing on a water
    row is considered "marked" when the ``cards`` header includes a lily pad.
    """
    out: List[Diagnostic] = []
    has_lily = any(pid == PlantType.LILY_PAD for pid, _ in _safe_cards(track))
    coffee_noted = False
    for wave in track.waves:
        w = _wave_label(wave)
        for op in _ops(wave):
            if op.kind in (KIND_ICE, KIND_DOOM) and scene.is_day and not coffee_noted:
                out.append(Diagnostic(INFO, "ICE_DAY_NEEDS_COFFEE",
                                      f"{scene.code} is a day scene: {op.symbol} needs a coffee bean "
                                      f"(effect offset {ICE_ACTIVATE_DAY}cs)", w, op.symbol))
                coffee_noted = True
            if op.kind == KIND_DOOM and scene.needs_pot:
                out.append(Diagnostic(ERROR, "ROOF_NO_DOOM",
                                      f"doom-shroom cannot be used on {scene.code}", w, op.symbol))
            if (op.kind != KIND_COB and op.target is not None and not has_lily
                    and any(r in scene.water_rows for r in op.target.rows)):
                out.append(Diagnostic(WARNING, "POOL_NEEDS_LILY",
                                      f"{op.symbol} lands on water row "
                                      f"{[r for r in op.target.rows if r in scene.water_rows][0]} "
                                      f"but no lily pad in cards", w, op.symbol))
    return out


def _safe_cards(track: Track) -> Tuple[Tuple[int, bool], ...]:
    """``parse_cards`` that tolerates a malformed header (returns ``()``)."""
    if track.cards is None:
        return ()
    try:
        return parse_cards(track.cards)
    except ValueError:
        return ()


# --- timeline -----------------------------------------------------------------


def _timeline(track: Track) -> Tuple[List[_Event], bool]:
    """Absolute effect times for every op of a pvzt track.

    Wave ``n`` starts at ``start[n-1] + length[n-1]``; the length used is
    ``length_cs`` or the low end of ``length_range`` (the conservative
    choice for CD checks). A wave with no length (or an undefined wave
    number) breaks the chain: later waves start a new *segment* and events
    from different segments are never compared. Returns the events and a
    flag telling whether any break happened.
    """
    by_wave: Dict[int, List[Wave]] = {}
    for wave in track.waves:
        for n in wave.selector:
            by_wave.setdefault(n, []).append(wave)
    events: List[_Event] = []
    broken = False
    segment = 0
    start = 0
    prev_known = True
    for n in sorted(by_wave):
        if not prev_known:
            segment += 1
            start = 0
            broken = True
        waves = by_wave[n]
        prev_effect = 0
        for wave in waves:
            for op in _ops(wave):
                if op.effect_cs is None:
                    continue
                t = prev_effect + op.effect_cs if op.relative else op.effect_cs
                prev_effect = t
                events.append((start + t, n, t, op, segment))
        length = None
        for wave in waves:
            if wave.length_cs is not None:
                length = wave.length_cs
            elif wave.length_range is not None:
                length = wave.length_range[0]
        if length is None or n + 1 not in by_wave:
            prev_known = False
        else:
            start += length
            prev_known = True
    events.sort(key=lambda e: (e[4], e[0]))
    return events, broken


def _window_violations(events: List[_Event], cd: int, available: int, code: str, what: str) -> List[Diagnostic]:
    """Sliding-window check: starting at any event, fewer than ``cd`` cs
    later at most ``available`` units may have been used (each event
    contributes ``op.count``). One diagnostic per offending window start,
    anchored at the op that overflows it."""
    out: List[Diagnostic] = []
    for i, (t0, w0, rel0, _op0, seg) in enumerate(events):
        used = 0
        for t, w, _rel, op, seg2 in events[i:]:
            if seg2 != seg or t - t0 >= cd:
                break
            used += op.count
            if used > available:
                out.append(Diagnostic(ERROR, code,
                                      f"{used} {what} within {cd}cs of w{w0} {rel0}cs "
                                      f"but only {available} available (CD {cd}cs)", w, op.symbol))
                break
    return out


def _cob_count(track: Track, scene: Optional[Scene]) -> Tuple[Optional[int], str]:
    """Number of cannons for ``COB_CD``: ``meta['cob_count']`` first, else the
    cobs of a decodable ``lineup`` header whose scene matches the track's.
    Returns ``(count, reason)`` with ``count=None`` when unknown."""
    count = track.meta.get("cob_count")
    if isinstance(count, int) and count >= 1:
        return count, "meta['cob_count']"
    if track.lineup_code is None:
        return None, "meta['cob_count'] not set and no lineup header"
    try:
        lineup = decode_lineup_code(track.lineup_code)
    except ValueError as exc:
        return None, f"lineup header does not decode ({exc})"
    if scene is not None and lineup.scene != scene.code:
        return None, f"lineup header is for {lineup.scene} but the track is {scene.code}"
    cobs = len(lineup.cobs())
    if cobs == 0:
        return None, "lineup header has no cob cannons"
    return cobs, "lineup header"


def _check_cob_cd(track: Track, events: List[_Event], scene: Optional[Scene]) -> List[Diagnostic]:
    """``COB_CD``: a cannon refires no sooner than ``COB_CD`` after it fired.

    Cannons are not named in the DSL, so the check is "cobs fired in any
    ``COB_CD`` window ≤ cannon count" with the count taken from
    :func:`_cob_count`. Without a count the check is skipped with an info.
    """
    cobs = [e for e in events if e[3].kind == KIND_COB]
    if not cobs:
        return []
    count, reason = _cob_count(track, scene)
    if count is None:
        return [Diagnostic(INFO, "COB_CD", f"{reason}; cannon CD ({COB_CD}cs) check skipped")]
    return _window_violations(cobs, COB_CD, count, "COB_CD", f"cobs ({reason})")


# card identity: (kind, variant) -> plant whose CD applies
_CARD_PLANT: Dict[Tuple[str, Optional[str]], PlantType] = {
    (KIND_ICE, None): PlantType.ICE_SHROOM,
    (KIND_DOOM, None): PlantType.DOOM_SHROOM,
    (KIND_ASH, "cherry"): PlantType.CHERRY_BOMB,
    (KIND_ASH, "jalapeno"): PlantType.JALAPENO,
    (KIND_ASH, "squash"): PlantType.SQUASH,
    (KIND_ASH, "light"): PlantType.POTATO_MINE,  # be2sio4 'a': 辣椒/窝瓜/土豆雷, shortest CD wins
}
#: §6 CARD_CD: "垫材 750" — every fodder symbol (hard or soft) uses the fodder CD.
_FODDER_KEY = "fodder"
# which card letters count as "one more copy" of each pooled symbol
_CARD_POOL: Dict[object, Tuple[PlantType, ...]] = {
    PlantType.POTATO_MINE: (PlantType.POTATO_MINE, PlantType.JALAPENO, PlantType.SQUASH),
    # 垫材: the auto-fodder letters (``C``/``c``, counted separately) plus
    # any card the community plants as fodder, cheap or "advanced" (§2.1)
    _FODDER_KEY: (
        PlantType.PUFF_SHROOM,
        PlantType.SUN_SHROOM,
        PlantType.SCAREDY_SHROOM,
        PlantType.SUNFLOWER,
        PlantType.PEASHOOTER,
        PlantType.FLOWER_POT,
        PlantType.PUMPKIN,
        PlantType.TALL_NUT,
        PlantType.SPIKEWEED,
        PlantType.WALL_NUT,
        PlantType.GARLIC,
        PlantType.UMBRELLA_LEAF,
    ),
}

# card group: (plant-or-fodder, imitater-only)
_CardKey = Tuple[object, bool]


def _card_key(op: Op) -> Optional[_CardKey]:
    """Card group of an op: ``(plant, imitater)``; ``I1..I4`` are still the
    ice card, every fodder variant is the ``"fodder"`` group. A plain ``I``
    / ``N`` names no particular copy, so it is grouped with ``imitater=False``
    and may draw from the imitater card too (see :func:`_card_available`);
    an explicit ``I'`` / ``N'`` is pinned to the imitater card. Returns None
    for ops that are not cards (cob, natural)."""
    if op.kind == KIND_ICE:
        return PlantType.ICE_SHROOM, op.variant == "imitater"
    if op.kind == KIND_DOOM:
        return PlantType.DOOM_SHROOM, op.variant == "imitater"
    if op.kind == KIND_FODDER:
        return _FODDER_KEY, False
    plant = _CARD_PLANT.get((op.kind, op.variant))
    if plant is None:
        return None
    return plant, False


def _card_cd_of(key: _CardKey) -> int:
    return FODDER_CD if key[0] == _FODDER_KEY else card_cd(key[0])


def _card_available(track: Track, key: _CardKey, used_imitater_symbol: bool) -> int:
    """How many distinct cards the group may draw from (≥ 1).

    Counted from the ``cards`` header: a generic symbol (``I``, ``A``, ``C``)
    takes every copy of the plant, real *or* imitater (§6 "同卡": a plain
    ``I`` with ``cards: II`` has two ice cards to rotate); an explicit
    imitater symbol (``I'``) only the imitater copy. Pooled symbols count
    their whole pool: fodder counts the auto letters ``C``/``c`` plus every
    fodder-capable card, be2sio4 ``a`` counts 辣椒/窝瓜/土豆雷. Without a
    header: 1, or 2 when the track spells both ``X`` and ``X'`` itself.
    """
    plant, imit = key
    pool = _CARD_POOL.get(plant, (plant,))
    cards = _safe_cards(track)
    if not cards:
        return 2 if (not imit and used_imitater_symbol) else 1
    n = 0
    for pid, is_imit in cards:
        if imit and not is_imit:
            continue
        if pid == -1:
            if plant == _FODDER_KEY:
                n += 1
        elif PlantType(pid) in pool:
            n += 1
    return max(1, n)


def _check_card_cd(track: Track, events: List[_Event]) -> List[Diagnostic]:
    """``CARD_CD``: same card reused sooner than its CD (``timing.card_cd``;
    §6: 冰/核/樱/辣 5000, 窝瓜 3000, 垫材 750)."""
    groups: Dict[_CardKey, List[_Event]] = {}
    for e in events:
        key = _card_key(e[3])
        if key is not None:
            groups.setdefault(key, []).append(e)
    out: List[Diagnostic] = []
    for key, evs in groups.items():
        plant, imit = key
        name = (plant if plant == _FODDER_KEY else plant.name.lower()) + (" (imitater)" if imit else "")
        has_imit_symbol = (plant, True) in groups
        out.extend(_window_violations(evs, _card_cd_of(key), _card_available(track, key, has_imit_symbol), "CARD_CD", name))
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def lint(track: Track, scene: Optional[str] = None) -> List[Diagnostic]:
    """Run every §6 rule that applies to ``track`` and return the diagnostics.

    ``scene`` (two-letter code) overrides ``track.scene``; scene-dependent
    rules are skipped when neither is known. Shorthand tracks only get the
    structural rules (see module docstring).
    """
    out: List[Diagnostic] = []
    out.extend(_check_simplified_meta(track))
    out.extend(_check_al(track))
    out.extend(_check_len(track))
    out.extend(_check_wave_numbers(track))
    out.extend(_check_rhythm(track))
    if track.kind != "pvzt":
        return out
    sc = _resolve_scene(track, scene, out)
    out.extend(_check_targets(track, sc))
    if sc is not None:
        out.extend(_check_scene(track, sc))
    events, broken = _timeline(track)
    if broken:
        out.append(Diagnostic(INFO, "COB_CD",
                              "some waves have no length; CD checks are not chained across them"))
    out.extend(_check_cob_cd(track, events, sc))
    out.extend(_check_card_cd(track, events))
    return out
