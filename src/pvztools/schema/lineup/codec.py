"""Lineup codecs (docs/lineup-format.md §2, docs/research/lineup-code-format.md).

Two community encodings are supported, both originating from pvztoolkit
(lmintlcx, GPL) and shared on pvz.tools / AvZ tutorials:

* **lineup code** (阵型代码, base64) — :func:`decode_lineup_code` /
  :func:`encode_lineup_code`. Binary layout: uint16 little-endian per cell,
  row-major ``rows*9`` cells, zlib level 9, trailer byte ``(rake_row<<4)|scene``
  (in-game scene id 0..5 = DE NE PE FE RE ME), whole buffer XOR 0x54, base64.
* **lineup string** (阵型字符串, text) — :func:`parse_lineup_string` /
  :func:`dump_lineup_string`. Grammar ``SCENE(,TYPEHEX ROW COL STATE F4 IM)*``
  with SCENE numbered 0=PE 1=FE 2=DE 3=NE 4=RE 5=ME. The lonelystar
  ArrayDesign export (§2.1) uses the same item layout with extra trailing
  draw fields, which are tolerated and ignored.
"""
from __future__ import annotations

import base64
import binascii
import re
import zlib
from typing import Dict, List, Optional, Tuple

from ..enums import PlantType, Scene
from .model import COLS, MAY_SLEEP, Cell, Lineup

__all__ = [
    "decode_lineup_code",
    "encode_lineup_code",
    "parse_lineup_string",
    "dump_lineup_string",
    "STRING_SCENE_ORDER",
    "ID_LADDER",
    "ID_RAKE",
    "ID_TOMB",
]

# Extra ids used by pvztoolkit / ArrayDesign beyond the 48 seed types.
ID_LADDER = 0x30
ID_RAKE = 0x31
ID_TOMB = 0x32

_COB = int(PlantType.COB_CANNON)
_LILY = int(PlantType.LILY_PAD)
_POT = int(PlantType.FLOWER_POT)
_PUMPKIN = int(PlantType.PUMPKIN)
_COFFEE = int(PlantType.COFFEE_BEAN)
_MAX_SEED = _COB  # 47

# Lineup-string scene numbering (differs from the in-game id!).
STRING_SCENE_ORDER = ("PE", "FE", "DE", "NE", "RE", "ME")

# ---------------------------------------------------------------------------
# binary lineup code
# ---------------------------------------------------------------------------
_XOR_KEY = 0x54
_CODE_RE = re.compile(r"^(?:[A-Za-z0-9+/=]{18,164}|[A-Za-z0-9\-_=]{18,164})$")

# bit layout of one uint16 cell (research doc table)
_PLANT_SHIFT = 10  # bits 15..10: seed type + 1, 0 = none
_BIT_PLANT_IM = 0x0200
_BIT_AWAKE = 0x0100
_BASE_SHIFT = 6  # bits 7..6: 0 none, 1 lily, 2 pot, 3 gravestone
_BIT_BASE_IM = 0x0020
_BIT_PUMPKIN = 0x0010
_BIT_PUMPKIN_IM = 0x0008
_BIT_COFFEE = 0x0004
_BIT_COFFEE_IM = 0x0002
_BIT_LADDER = 0x0001

_BASE_NONE, _BASE_LILY, _BASE_POT, _BASE_TOMB = 0, 1, 2, 3


def _is_asleep(scene: Scene, plant: Optional[int], awake: bool) -> bool:
    """Sleep rule: only sleep-capable mushrooms sleep, only in day scenes."""
    return (not awake) and scene.is_day and plant in MAY_SLEEP


def _cell_from_word(scene: Scene, row: int, col: int, word: int) -> Optional[Cell]:
    """Unpack one uint16 into a ``Cell`` (``None`` when the cell is empty)."""
    if word == 0:
        return None
    pos = f"({row},{col})"
    plant_field = word >> _PLANT_SHIFT
    plant: Optional[int] = None
    if plant_field:
        plant = plant_field - 1
        if plant > _MAX_SEED:
            raise ValueError(f"lineup code: invalid plant field {plant_field} at {pos}")
    base = (word >> _BASE_SHIFT) & 0x3
    container: Optional[int] = None
    props: List[str] = []
    if base == _BASE_LILY:
        container = _LILY
    elif base == _BASE_POT:
        container = _POT
    elif base == _BASE_TOMB:
        props.append("tomb")
    if word & _BIT_LADDER:
        props.append("ladder")
    return Cell(
        row=row,
        col=col,
        plant=plant,
        container=container,
        shell=_PUMPKIN if word & _BIT_PUMPKIN else None,
        addon=_COFFEE if word & _BIT_COFFEE else None,
        props=tuple(props),
        imitater=bool(plant is not None and word & _BIT_PLANT_IM),
        container_imitater=bool(container is not None and word & _BIT_BASE_IM),
        shell_imitater=bool(word & _BIT_PUMPKIN and word & _BIT_PUMPKIN_IM),
        addon_imitater=bool(word & _BIT_COFFEE and word & _BIT_COFFEE_IM),
        asleep=_is_asleep(scene, plant, bool(word & _BIT_AWAKE)),
    )


def _word_from_cell(c: Cell) -> int:
    """Pack a ``Cell`` into the uint16 layout."""
    pos = f"({c.row},{c.col})"
    word = 0
    if c.plant is not None:
        if not (0 <= c.plant <= _MAX_SEED):
            raise ValueError(f"lineup code: plant id {c.plant} at {pos} not encodable (0..47)")
        word |= (c.plant + 1) << _PLANT_SHIFT
        if c.imitater:
            word |= _BIT_PLANT_IM
        if not c.asleep:
            word |= _BIT_AWAKE
    if "tomb" in c.props:
        base = _BASE_TOMB
    elif c.container == _LILY:
        base = _BASE_LILY
    elif c.container == _POT:
        base = _BASE_POT
    elif c.container is None:
        base = _BASE_NONE
    else:
        raise ValueError(f"lineup code: container id {c.container} at {pos} not encodable (16/33)")
    word |= base << _BASE_SHIFT
    if c.container is not None and c.container_imitater:
        word |= _BIT_BASE_IM
    if c.shell is not None:
        if c.shell != _PUMPKIN:
            raise ValueError(f"lineup code: shell id {c.shell} at {pos} not encodable (30)")
        word |= _BIT_PUMPKIN
        if c.shell_imitater:
            word |= _BIT_PUMPKIN_IM
    if c.addon is not None:
        if c.addon != _COFFEE:
            raise ValueError(f"lineup code: addon id {c.addon} at {pos} not encodable (35)")
        word |= _BIT_COFFEE
        if c.addon_imitater:
            word |= _BIT_COFFEE_IM
    if "ladder" in c.props:
        word |= _BIT_LADDER
    return word


def decode_lineup_code(code: str) -> Lineup:
    """Decode a pvztoolkit / pvz.tools base64 lineup code into a :class:`Lineup`.

    Accepts the URL-safe alphabet (``-`` ``_``) as well as ``+`` ``/``.
    Raises ``ValueError`` on malformed input (bad base64, bad scene nibble,
    corrupt zlib stream, wrong payload size).
    """
    code = code.strip()
    if not _CODE_RE.match(code) or len(code) % 4 != 0 or code.count("=") > 2:
        raise ValueError("lineup code: not a base64 string of 18..164 chars (multiple of 4)")
    try:
        buf = base64.b64decode(code.replace("-", "+").replace("_", "/"), validate=True)
    except (ValueError, binascii.Error) as e:
        raise ValueError(f"lineup code: base64 decode failed: {e}") from None
    if len(buf) < 2 or len(buf) > 128:
        raise ValueError(f"lineup code: decoded size {len(buf)} outside 2..128 bytes")
    buf = bytes(b ^ _XOR_KEY for b in buf)
    trailer = buf[-1]
    rake_row = trailer >> 4
    scene_id = trailer & 0x0F
    if scene_id > 5:
        raise ValueError(f"lineup code: invalid scene id {scene_id} in trailer byte")
    scene = Scene(scene_id)
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(buf[:-1]) + inflater.flush()
    except zlib.error as e:
        raise ValueError(f"lineup code: zlib inflate failed: {e}") from None
    if not inflater.eof or inflater.unused_data:
        raise ValueError("lineup code: zlib stream truncated or followed by extra bytes")
    expected = scene.rows * COLS * 2
    if len(raw) != expected:
        raise ValueError(
            f"lineup code: payload is {len(raw)} bytes, expected {expected} for scene {scene.code}"
        )
    cells: List[Cell] = []
    for i in range(0, len(raw), 2):
        idx = i // 2
        row, col = idx // COLS + 1, idx % COLS + 1
        word = raw[i] | (raw[i + 1] << 8)
        c = _cell_from_word(scene, row, col, word)
        if c is not None:
            cells.append(c)
    return Lineup(
        scene=scene.code,
        cells=tuple(cells),
        rake_row=rake_row or None,
        source="lineup-code",
    )


def encode_lineup_code(lineup: Lineup) -> str:
    """Encode a :class:`Lineup` as a pvztoolkit base64 lineup code.

    Codes are not byte-canonical across zlib builds; compare decoded grids,
    not strings. Cells outside the scene's ``rows x 9`` field raise
    ``ValueError``.
    """
    scene = lineup.scene_id
    rows = scene.rows
    words = [0] * (rows * COLS)
    for c in lineup.cells:
        if not (1 <= c.row <= rows) or not (1 <= c.col <= COLS):
            raise ValueError(f"lineup code: cell ({c.row},{c.col}) outside {rows}x{COLS} field of {scene.code}")
        words[(c.row - 1) * COLS + (c.col - 1)] = _word_from_cell(c)
    rake = lineup.rake_row or 0
    if not (0 <= rake <= rows):
        raise ValueError(f"lineup code: rake row {rake} outside 1..{rows}")
    raw = b"".join(bytes((w & 0xFF, w >> 8)) for w in words)
    payload = zlib.compress(raw, 9) + bytes([(rake << 4) | int(scene)])
    return base64.b64encode(bytes(b ^ _XOR_KEY for b in payload)).decode("ascii")


# ---------------------------------------------------------------------------
# text lineup string (pvztoolkit) / ArrayDesign export
# ---------------------------------------------------------------------------
_HEX_RE = re.compile(r"^[0-9A-Fa-f]{1,2}$")


class _CellBuilder:
    """Mutable accumulator for the items sharing one grid cell."""

    def __init__(self, row: int, col: int) -> None:
        self.row, self.col = row, col
        self.plant: Optional[int] = None
        self.container: Optional[int] = None
        self.shell: Optional[int] = None
        self.addon: Optional[int] = None
        self.props: List[str] = []
        self.imitater = False
        self.container_imitater = False
        self.shell_imitater = False
        self.addon_imitater = False
        self.asleep = False
        self.tomb_variant = 0

    def build(self) -> Cell:
        return Cell(
            row=self.row,
            col=self.col,
            plant=self.plant,
            container=self.container,
            shell=self.shell,
            addon=self.addon,
            props=tuple(self.props),
            imitater=self.imitater,
            container_imitater=self.container_imitater,
            shell_imitater=self.shell_imitater,
            addon_imitater=self.addon_imitater,
            asleep=self.asleep,
            tomb_variant=self.tomb_variant,
        )


def parse_lineup_string(text: str) -> Lineup:
    """Parse a pvztoolkit lineup string or lonelystar ArrayDesign export.

    ``SCENE,TYPEHEX ROW COL STATE F4 IM[,...]`` — SCENE per
    :data:`STRING_SCENE_ORDER`; per item: hex id (``0x00-0x2F`` seed type,
    ``0x30`` ladder, ``0x31`` rake, ``0x32`` gravestone; anything larger —
    ArrayDesign decorations and zombies — is ignored), 1-based row/col,
    STATE (0 = sleeping for sleep-capable mushrooms in day scenes), F4
    (gravestone look; sun-shroom grow stage is dropped), IM (``1`` =
    imitater). Extra trailing fields (ArrayDesign opacity/flip/x/y) are
    ignored; empty items (trailing comma) are skipped.
    """
    parts = text.strip().split(",")
    head = parts[0].strip()
    if not head.isdigit() or int(head) >= len(STRING_SCENE_ORDER):
        raise ValueError(f"lineup string: scene field must be 0..5, got {head!r}")
    scene = Scene.from_code(STRING_SCENE_ORDER[int(head)])
    cells: Dict[Tuple[int, int], _CellBuilder] = {}
    rake_row: Optional[int] = None
    for i, item in enumerate(parts[1:], start=1):
        f = item.split()
        if not f:
            continue
        where = f"item {i} {item.strip()!r}"
        if len(f) < 6:
            raise ValueError(f"lineup string: {where}: expected 6 fields, got {len(f)}")
        if not _HEX_RE.match(f[0]):
            raise ValueError(f"lineup string: {where}: bad hex id {f[0]!r}")
        tid = int(f[0], 16)
        if tid > ID_TOMB:
            continue
        try:
            row, col, state, f4 = int(f[1]), int(f[2]), int(f[3]), int(f[4])
        except ValueError:
            raise ValueError(f"lineup string: {where}: row/col/state/f4 must be integers") from None
        if not (1 <= row <= scene.rows) or not (1 <= col <= COLS):
            raise ValueError(f"lineup string: {where}: position ({row},{col}) outside {scene.rows}x{COLS} field of {scene.code}")
        imit = f[5] == "1"
        if tid == ID_RAKE:
            if rake_row is not None and rake_row != row:
                raise ValueError(f"lineup string: {where}: second rake (already on row {rake_row})")
            rake_row = row
            continue
        b = cells.get((row, col))
        if b is None:
            b = cells[(row, col)] = _CellBuilder(row, col)
        if tid == ID_LADDER:
            if "ladder" not in b.props:
                b.props.append("ladder")
        elif tid == ID_TOMB:
            if "tomb" not in b.props:
                b.props.append("tomb")
            b.tomb_variant = f4
        elif tid in (_LILY, _POT):
            if b.container is not None:
                raise ValueError(f"lineup string: {where}: cell already has container {b.container}")
            b.container, b.container_imitater = tid, imit
        elif tid == _PUMPKIN:
            if b.shell is not None:
                raise ValueError(f"lineup string: {where}: cell already has a pumpkin")
            b.shell, b.shell_imitater = tid, imit
        elif tid == _COFFEE:
            if b.addon is not None:
                raise ValueError(f"lineup string: {where}: cell already has a coffee bean")
            b.addon, b.addon_imitater = tid, imit
        else:
            if b.plant is not None:
                raise ValueError(f"lineup string: {where}: cell already has plant {b.plant}")
            b.plant, b.imitater = tid, imit
            b.asleep = _is_asleep(scene, tid, state != 0)
    return Lineup(
        scene=scene.code,
        cells=tuple(b.build() for b in cells.values()),
        rake_row=rake_row,
        source="lineup-string",
    )


def _item(tid: int, row: int, col: int, state: int = 0, f4: int = 0, imit: bool = False) -> str:
    return f"{tid:X} {row} {col} {state} {f4} {1 if imit else 0}"


def dump_lineup_string(lineup: Lineup) -> str:
    """Serialise as a pvztoolkit lineup string (the inverse of
    :func:`parse_lineup_string`).

    Item order per cell follows pvztoolkit: container, plant, pumpkin, coffee,
    ladder/gravestone. The rake item is written as ``31 ROW 8 0 0 0`` (the
    published lineup_string.json places it in column 8; only the row matters).
    Sleep-capable mushrooms get STATE 1 when awake, 0 when asleep.
    """
    scene = lineup.scene_id
    out: List[str] = [str(STRING_SCENE_ORDER.index(scene.code))]
    for c in lineup.cells:
        r, k = c.row, c.col
        if c.container is not None:
            out.append(_item(c.container, r, k, imit=c.container_imitater))
        if c.plant is not None:
            state = 0
            if c.plant in MAY_SLEEP:
                state = 0 if c.asleep else 1
            out.append(_item(c.plant, r, k, state, imit=c.imitater))
        if c.shell is not None:
            out.append(_item(c.shell, r, k, imit=c.shell_imitater))
        if c.addon is not None:
            out.append(_item(c.addon, r, k, imit=c.addon_imitater))
        if "tomb" in c.props:
            out.append(_item(ID_TOMB, r, k, 0, c.tomb_variant))
        if "ladder" in c.props:
            out.append(_item(ID_LADDER, r, k))
    if lineup.rake_row is not None:
        out.append(_item(ID_RAKE, lineup.rake_row, 8))
    return ",".join(out)
