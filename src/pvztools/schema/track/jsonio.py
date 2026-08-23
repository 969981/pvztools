"""Lossless JSON (de)serialisation of the track AST.

Specification: docs/track-format.md §5 (``to_json(track)`` / ``from_json``
round-trip without loss). The document is the dataclass tree of
``ast.Track`` with every tuple written as a JSON array, plus a schema
version key ``"pvztools_track": 1`` at the top level. ``from_dict``
rebuilds the frozen dataclasses and restores the tuple types so that
``from_json(to_json(t)) == t`` holds for any parser output whose ``meta``
is JSON-representable (lists / dicts / scalars).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .ast import HalfWave, Op, RhythmName, Target, Track, Wave

__all__ = ["SCHEMA_KEY", "SCHEMA_VERSION", "to_dict", "from_dict", "to_json", "from_json"]

SCHEMA_KEY = "pvztools_track"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------


def _target_to_dict(t: Target) -> Dict[str, Any]:
    return {"rows": list(t.rows), "col": t.col}


def _op_to_dict(op: Op) -> Dict[str, Any]:
    return {
        "symbol": op.symbol,
        "kind": op.kind,
        "variant": op.variant,
        "count": op.count,
        "al": op.al,
        "phase": op.phase,
        "effect_cs": op.effect_cs,
        "window_cs": list(op.window_cs) if op.window_cs is not None else None,
        "relative": op.relative,
        "target": _target_to_dict(op.target) if op.target is not None else None,
        "simultaneous_with_prev": op.simultaneous_with_prev,
    }


def _half_to_dict(h: HalfWave) -> Dict[str, Any]:
    return {"ops": [_op_to_dict(op) for op in h.ops]}


def _wave_to_dict(w: Wave) -> Dict[str, Any]:
    return {
        "selector": list(w.selector),
        "top": _half_to_dict(w.top),
        "bottom": _half_to_dict(w.bottom) if w.bottom is not None else None,
        "mirrored": w.mirrored,
        "length_cs": w.length_cs,
        "length_range": list(w.length_range) if w.length_range is not None else None,
        "raw": w.raw,
    }


def _rhythm_to_dict(r: RhythmName) -> Dict[str, Any]:
    return {
        "family": r.family,
        "param": r.param,
        "prefix": r.prefix,
        "suffixes": list(r.suffixes),
        "raw": r.raw,
    }


def to_dict(track: Track) -> Dict[str, Any]:
    """Convert a ``Track`` into a JSON-compatible dict (§5).

    Tuples become lists; nested dataclasses become nested dicts. The
    ``meta`` dict is copied as-is (it must already be JSON-compatible,
    which holds for every parser in this package).
    """
    return {
        SCHEMA_KEY: SCHEMA_VERSION,
        "waves": [_wave_to_dict(w) for w in track.waves],
        "rhythm": _rhythm_to_dict(track.rhythm) if track.rhythm is not None else None,
        "scene": track.scene,
        "dialect": track.dialect,
        "timing_base": track.timing_base,
        "cards": track.cards,
        "zombies": track.zombies,
        "protect": list(track.protect),
        "lineup_code": track.lineup_code,
        "kind": track.kind,
        "meta": dict(track.meta),
    }


def to_json(track: Track, indent: Optional[int] = 2) -> str:
    """Serialise ``track`` to a JSON string (``ensure_ascii=False`` so
    Chinese rhythm prefixes and zombie strings stay readable)."""
    return json.dumps(to_dict(track), ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------


def _expect(data: Any, typ: type, where: str) -> Any:
    if not isinstance(data, typ):
        raise ValueError(f"{where}: expected {typ.__name__}, got {type(data).__name__}")
    return data


def _int_tuple(data: Any, where: str) -> Tuple[int, ...]:
    items = _expect(data, list, where)
    out: List[int] = []
    for i, v in enumerate(items):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{where}[{i}]: expected int, got {v!r}")
        out.append(v)
    return tuple(out)


def _opt_pair(data: Any, where: str) -> Optional[Tuple[int, int]]:
    if data is None:
        return None
    pair = _int_tuple(data, where)
    if len(pair) != 2:
        raise ValueError(f"{where}: expected 2 items, got {len(pair)}")
    return (pair[0], pair[1])


def _target_from_dict(data: Any, where: str) -> Target:
    d = _expect(data, dict, where)
    col = d.get("col")
    if isinstance(col, bool) or not isinstance(col, (int, float)):
        raise ValueError(f"{where}.col: expected number, got {col!r}")
    return Target(rows=_int_tuple(d.get("rows"), where + ".rows"), col=float(col))


def _op_from_dict(data: Any, where: str) -> Op:
    d = _expect(data, dict, where)
    for key in ("symbol", "kind"):
        if not isinstance(d.get(key), str):
            raise ValueError(f"{where}.{key}: expected string, got {d.get(key)!r}")
    target = d.get("target")
    return Op(
        symbol=d["symbol"],
        kind=d["kind"],
        variant=d.get("variant"),
        count=int(d.get("count", 1)),
        al=int(d.get("al", 0)),
        phase=str(d.get("phase", "post")),
        effect_cs=d.get("effect_cs"),
        window_cs=_opt_pair(d.get("window_cs"), where + ".window_cs"),
        relative=bool(d.get("relative", False)),
        target=_target_from_dict(target, where + ".target") if target is not None else None,
        simultaneous_with_prev=bool(d.get("simultaneous_with_prev", False)),
    )


def _half_from_dict(data: Any, where: str) -> HalfWave:
    d = _expect(data, dict, where)
    ops = _expect(d.get("ops", []), list, where + ".ops")
    return HalfWave(ops=tuple(_op_from_dict(op, f"{where}.ops[{i}]") for i, op in enumerate(ops)))


def _wave_from_dict(data: Any, where: str) -> Wave:
    d = _expect(data, dict, where)
    bottom = d.get("bottom")
    return Wave(
        selector=_int_tuple(d.get("selector"), where + ".selector"),
        top=_half_from_dict(d.get("top", {}), where + ".top"),
        bottom=_half_from_dict(bottom, where + ".bottom") if bottom is not None else None,
        mirrored=bool(d.get("mirrored", False)),
        length_cs=d.get("length_cs"),
        length_range=_opt_pair(d.get("length_range"), where + ".length_range"),
        raw=str(d.get("raw", "")),
    )


def _rhythm_from_dict(data: Any, where: str) -> RhythmName:
    d = _expect(data, dict, where)
    suffixes = _expect(d.get("suffixes", []), list, where + ".suffixes")
    return RhythmName(
        family=str(d.get("family", "?")),
        param=d.get("param"),
        prefix=str(d.get("prefix", "")),
        suffixes=tuple(str(s) for s in suffixes),
        raw=str(d.get("raw", "")),
    )


def from_dict(data: Dict[str, Any]) -> Track:
    """Rebuild a ``Track`` from ``to_dict`` output (§5).

    Raises ``ValueError`` naming the offending JSON path when the schema
    version is missing/unsupported or a field has the wrong shape.
    """
    d = _expect(data, dict, "track")
    version = d.get(SCHEMA_KEY)
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"track: missing or unsupported {SCHEMA_KEY!r} version {version!r}; expected {SCHEMA_VERSION}"
        )
    waves = _expect(d.get("waves"), list, "track.waves")
    rhythm = d.get("rhythm")
    meta = d.get("meta") or {}
    return Track(
        waves=tuple(_wave_from_dict(w, f"track.waves[{i}]") for i, w in enumerate(waves)),
        rhythm=_rhythm_from_dict(rhythm, "track.rhythm") if rhythm is not None else None,
        scene=d.get("scene"),
        dialect=str(d.get("dialect", "duben")),
        timing_base=str(d.get("timing_base", "effect")),
        cards=d.get("cards"),
        zombies=d.get("zombies"),
        protect=_int_tuple(d.get("protect", []), "track.protect"),
        lineup_code=d.get("lineup_code"),
        kind=str(d.get("kind", "shorthand")),
        meta=dict(_expect(meta, dict, "track.meta")),
    )


def from_json(text: str) -> Track:
    """Parse a JSON document produced by ``to_json`` back into a ``Track``."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid track JSON at line {exc.lineno} col {exc.colno}: {exc.msg}") from None
    return from_dict(data)
