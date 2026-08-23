"""Per-scene default cannon lineups (每场景默认炮阵).

Given a scene the automation is about to play, pick a proven 八炮 (eight-cob)
lineup from the bundled ArrayDesign catalogue (:mod:`pvztools.data`) and hand
back a decoded :class:`~pvztools.schema.lineup.Lineup` ready for
``place_lineup`` -- no live game needed.

The primary lineup per scene (verified this session to decode with exactly 8
cobs):

======  =======  ==============
scene   code     lineup name
======  =======  ==============
Pool    ``PE``   ``3C八炮``
Day     ``DE``   ``神之八炮``
Night   ``NE``   ``美学八炮``
Fog     ``FE``   ``经典八炮``
Roof    ``RE``   ``方块八炮``
Moon    ``ME``   ``对称八炮``
======  =======  ==============

Catalogue names are **not** unique on their own -- ``神之八炮`` for instance
exists in PE, FE and DE -- so every lookup here is filtered by ``(scene,
name)``, which *is* unique for the defaults.  ``lineup_place.load_lineup`` (by
name only) would raise on the ambiguous ones, so this module resolves against
the catalogue directly.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

__all__ = [
    "SCENES",
    "DEFAULT_LINEUPS",
    "FALLBACKS",
    "TARGET_COL_OVERRIDES",
    "default_lineup",
    "load_default",
    "validate_defaults",
    "cob_pairs",
    "target_col",
]

#: Canonical scene codes (community two-letter, matching
#: :class:`pvztools.schema.enums.Scene`).
SCENES: Tuple[str, ...] = ("PE", "DE", "NE", "FE", "RE", "ME")

#: Primary default lineup name per scene.
DEFAULT_LINEUPS: Dict[str, str] = {
    "PE": "3C八炮",
    "DE": "神之八炮",
    "NE": "美学八炮",
    "FE": "经典八炮",
    "RE": "方块八炮",
    "ME": "对称八炮",
}

#: Ordered fallback chain per scene (primary first).  Each name is tried in
#: order until one resolves to a decodable 8-cob lineup for that scene.  The
#: alternates are common catalogue 八炮 names that also exist for the scene.
FALLBACKS: Dict[str, List[str]] = {
    "PE": ["3C八炮", "神之八炮", "矩形八炮"],
    "DE": ["神之八炮", "简化八炮", "前置八炮"],
    "NE": ["美学八炮", "简化八炮", "前置八炮"],
    "FE": ["经典八炮", "神之八炮", "无保护八炮"],
    "RE": ["方块八炮", "斜方八炮", "理发八炮"],
    "ME": ["对称八炮", "奇怪的八炮", "田八炮"],
}

#: Per-scene cob-firing target column override hook.  Empty by default (all
#: scenes fire to column 8, the classic 八炮 landing column); populate to
#: retarget a scene without touching :func:`target_col` callers.
TARGET_COL_OVERRIDES: Dict[str, int] = {}

#: Default cob target column for every scene (八炮 fire to column 8).
DEFAULT_TARGET_COL = 8


def _norm(scene: str) -> str:
    """Normalise a scene argument to a two-letter upper code."""
    if scene is None:
        raise ValueError("scene 不能为空")
    code = str(scene).strip().upper()
    if code not in DEFAULT_LINEUPS:
        raise KeyError("未知场景 %r，应为 %s 之一" % (scene, "/".join(SCENES)))
    return code


def default_lineup(scene: str) -> str:
    """Bundled catalogue name of the primary default lineup for ``scene``.

    ``scene`` is a two-letter code (``"PE"`` .. ``"ME"``, case-insensitive).
    Raises ``KeyError`` for an unknown scene.
    """
    return DEFAULT_LINEUPS[_norm(scene)]


def _catalogue():
    """The bundled lineup entries (lazy import to keep import cost low)."""
    from pvztools.data import load_lineups

    return load_lineups()


def _resolve(scene: str, name: str):
    """Decode the catalogue entry matching ``(scene, name)`` -> ``Lineup``.

    Filters on both fields (names are not globally unique) and rebuilds a
    named :class:`Lineup` from the entry's ArrayDesign ``raw`` string.
    Returns ``None`` when no such entry exists or it fails to decode.
    """
    from pvztools.schema.lineup import Lineup, parse_lineup_string

    hits = [e for e in _catalogue() if e.get("name") == name and e.get("scene") == scene]
    if not hits:
        return None
    e = hits[0]
    try:
        lu = parse_lineup_string(e["raw"])
    except Exception:
        return None
    return Lineup(scene=lu.scene, cells=lu.cells, rake_row=lu.rake_row,
                  name=e.get("name"), source="arraydesign[%s]" % e.get("index"),
                  meta=dict(lu.meta))


def load_default(scene: str):
    """Decoded :class:`~pvztools.schema.lineup.Lineup` for ``scene``.

    Walks the scene's :data:`FALLBACKS` chain (primary first) and returns the
    first entry that decodes.  Raises ``LookupError`` if the whole chain is
    unresolvable (should not happen for the bundled catalogue).
    """
    code = _norm(scene)
    tried = []
    for name in FALLBACKS.get(code, [DEFAULT_LINEUPS[code]]):
        tried.append(name)
        lu = _resolve(code, name)
        if lu is not None:
            return lu
    raise LookupError("场景 %s 的默认炮阵均无法解码（尝试了 %s）" % (code, ", ".join(tried)))


def validate_defaults() -> Dict[str, bool]:
    """For each scene: does the **primary** lineup decode with exactly 8 cobs?

    Returns ``{scene_code: bool}`` for all six scenes -- a test asserts every
    value is ``True``.
    """
    out: Dict[str, bool] = {}
    for code in SCENES:
        lu = _resolve(code, DEFAULT_LINEUPS[code])
        out[code] = lu is not None and len(lu.cobs()) == 8
    return out


def cob_pairs(lineup) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """Group a lineup's cobs into symmetric pairs, column-major then row.

    ``lineup.cobs()`` returns the left cell ``(row, col)`` of every cob
    cannon.  They are sorted by ``(col, row)`` -- column-major, then row --
    and chunked two-by-two, so an eight-cob lineup yields four pairs (the two
    cobs sharing / neighbouring a firing column).  Raises ``ValueError`` on an
    odd cob count.
    """
    cobs = sorted(lineup.cobs(), key=lambda rc: (rc[1], rc[0]))
    if len(cobs) % 2 != 0:
        raise ValueError("炮数为奇数 (%d)，无法两两配对" % len(cobs))
    return [(cobs[i], cobs[i + 1]) for i in range(0, len(cobs), 2)]


def target_col(scene: str) -> int:
    """Cob firing target column for ``scene`` (8 unless overridden).

    Consult :data:`TARGET_COL_OVERRIDES` first (retarget hook), else the
    classic 八炮 landing column :data:`DEFAULT_TARGET_COL` (8).
    """
    code = _norm(scene)
    return TARGET_COL_OVERRIDES.get(code, DEFAULT_TARGET_COL)
