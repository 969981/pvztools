"""Grid <-> pixel conversions (source-map §6.4, ``Board.cpp``).

Pure functions, no game access.  Rows / columns are **1-based** in every
public signature (row 1..6, col 1..9, matching the lineup / CLI convention);
the game's 0-based values are derived internally.  Scenes are the
:class:`~pvztools.schema.enums.Scene` ids (0 DE, 1 NE, 2 PE, 3 FE, 4 RE, 5 ME).

Source formulas (``Board::GridToPixelX/Y``, ``Board::PixelToGridX/Y``)::

    GridToPixelX(col,row) = col*80 + 40
    GridToPixelY(col,row):
      roof      : row*85 + (col<5 ? (5-col)*20 : 0) + 80 - 10
      pool/fog  : row*85 + 80
      other     : row*100 + 80
      high ground (mGridSquareType == GRIDSQUARE_HIGH_GROUND): -30
    PixelToGridX(x,y) = x<40 ? -1 : clamp((x-40)/80, 0, 8)
    PixelToGridY(x,y):
      y<80 or col==-1 -> -1
      roof : if col<5: y -= (4-col)*20 ; clamp((y-80)/85, 0, 4)
      pool : clamp((y-80)/85, 0, 5)
      else : clamp((y-80)/100, 0, 4)
    PixelToGridYKeepOnBoard(x,y) = max(PixelToGridY(max(x,80), y), 0)

Cob cannon targeting (§6.1 / §6.6): ``Plant::CobCannonFire(x, y)`` stores
``mTargetX = x - 47``; the projectile lands at ``mCobTargetX = mTargetX - 40``
and explodes at ``mPosX + 80`` -- the explosion centre is therefore
``x - 7`` px, and the landing row is ``PixelToGridYKeepOnBoard(x - 47, y)``.
"""
from __future__ import annotations

from typing import Optional, Tuple

__all__ = [
    "LAWN_XMIN", "LAWN_YMIN", "HIGH_GROUND_HEIGHT", "CELL_W", "COB_CLICK_OFFSET", "COB_TARGET_OFFSET",
    "ROOF_SCENES", "POOL_SCENES",
    "grid_to_pixel_x", "grid_to_pixel_y", "pixel_to_grid_x", "pixel_to_grid_y",
    "pixel_to_grid_y_keep_on_board", "cob_target_pixels", "rows_of_scene",
]

LAWN_XMIN = 40
LAWN_YMIN = 80
HIGH_GROUND_HEIGHT = 30
CELL_W = 80
#: Explosion centre = click x - 7 (§6.1).
COB_CLICK_OFFSET = 7
#: ``mTargetX = x - 47`` (§6.6); the landing row is computed from that x.
COB_TARGET_OFFSET = 47
ROOF_SCENES = (4, 5)
POOL_SCENES = (2, 3)


def rows_of_scene(scene: int) -> int:
    return 6 if int(scene) in POOL_SCENES else 5


def _trunc_div(a: int, b: int) -> int:
    """C-style integer division (truncates toward zero)."""
    q = abs(a) // b
    return q if a >= 0 else -q


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def grid_to_pixel_x(col: float) -> float:
    """Left edge of 1-based ``col`` (fractional columns allowed): ``col0*80+40``."""
    return (float(col) - 1.0) * CELL_W + LAWN_XMIN


def grid_to_pixel_y(scene: int, col: int, row: int, high_ground: bool = False) -> int:
    """Top edge of 1-based ``(row, col)`` for ``scene``; roof scenes slope
    for columns 1..5, ``high_ground`` subtracts 30."""
    scene = int(scene)
    row0, col0 = int(row) - 1, int(col) - 1
    if scene in ROOF_SCENES:
        y = row0 * 85 + ((5 - col0) * 20 if col0 < 5 else 0) + LAWN_YMIN - 10
    elif scene in POOL_SCENES:
        y = row0 * 85 + LAWN_YMIN
    else:
        y = row0 * 100 + LAWN_YMIN
    if high_ground:
        y -= HIGH_GROUND_HEIGHT
    return y


def pixel_to_grid_x(x: float) -> Optional[int]:
    """1-based column under pixel ``x``; ``None`` left of the lawn (game: -1)."""
    x = int(x)
    if x < LAWN_XMIN:
        return None
    return _clamp(_trunc_div(x - LAWN_XMIN, CELL_W), 0, 8) + 1


def pixel_to_grid_y(scene: int, x: float, y: float) -> Optional[int]:
    """1-based row under pixel ``(x, y)``; ``None`` above the lawn / off the
    left edge (game: -1)."""
    scene = int(scene)
    y = int(y)
    col = pixel_to_grid_x(x)
    if y < LAWN_YMIN or col is None:
        return None
    col0 = col - 1
    if scene in ROOF_SCENES:
        if col0 < 5:
            y -= (4 - col0) * 20
        row0 = _clamp(_trunc_div(y - LAWN_YMIN, 85), 0, 4)
    elif scene in POOL_SCENES:
        row0 = _clamp(_trunc_div(y - LAWN_YMIN, 85), 0, 5)
    else:
        row0 = _clamp(_trunc_div(y - LAWN_YMIN, 100), 0, 4)
    return row0 + 1


def pixel_to_grid_y_keep_on_board(scene: int, x: float, y: float) -> int:
    """``max(PixelToGridY(max(x, 80), y), 0)`` -- 1-based, never ``None``."""
    r = pixel_to_grid_y(scene, max(int(x), 80), y)
    return 1 if r is None else r


def cob_target_pixels(scene: int, row: int, col: float) -> Tuple[int, int]:
    """Click ``(x, y)`` to pass to ``cob_fire`` so the explosion centre lands on
    1-based ``row`` and (possibly fractional) ``col`` -- e.g. ``8.75`` is the
    community's "(r, 8.75)" target on the 1/80 grid.

    centre_px = grid_to_pixel_x(col) + 40 (= col*80); x = centre + 7;
    y = grid_to_pixel_y(scene, col', row) + 40 with col' the column the game
    derives from ``x - 47`` (matters on the sloped roof)."""
    scene = int(scene)
    rows = rows_of_scene(scene)
    row = int(row)
    if not (1 <= row <= rows):
        raise ValueError("row %d outside 1..%d for scene %d" % (row, rows, scene))
    col = float(col)
    if not (0.0 <= col <= 10.0):
        raise ValueError("col %r outside 0..10" % (col,))
    centre = grid_to_pixel_x(col) + CELL_W / 2.0
    x = int(round(centre + COB_CLICK_OFFSET))
    col_land = pixel_to_grid_x(max(x - COB_TARGET_OFFSET, 80)) or 1
    y = grid_to_pixel_y(scene, col_land, row) + 40
    assert pixel_to_grid_y_keep_on_board(scene, x - COB_TARGET_OFFSET, y) == row
    return x, y
