"""Track expander (docs/track-format.md §7).

Turns a :class:`Track` (shorthand cycle or per-wave ``.pvzt``) plus an
:class:`ExpandContext` (scene, cannon positions, ice slots, fodder rows)
into a :class:`Plan`: an ordered list of concrete :class:`Action` items,
one per cannon / card, each with an effect-axis *and* an operation-axis
time, plus a list of :class:`Diagnostic` issues. Nothing here raises for
"cannot allocate" situations (§7 rule 6); those become issues.

Rules implemented (§7):

1. Shorthand cycles are tiled over waves ``1..wave_count`` in selector
   order. Huge waves (w10/w20) get ``HUGE_WAVE_DELAY`` added to their
   untimed cob landings (rule 7); closing waves (w9/w19) are only
   *annotated* with ``info`` issues.
2. Cannon assignment by a ready queue keyed on last fire time. On the
   roof (RE/ME) cannons are split by column into 风炮 (slope, tail column
   ≤ ``ROOF_SLOPE_MAX_COL``) and 平炮 (flat); a shot prefers the class that
   matches its landing column and falls back to the other class with a
   ``ROOF_CANNON_CLASS`` warning.
3. Ice cards rotate over ``ice_slots``; day scenes get a coffee-bean action
   at ``effect - ICE_ACTIVATE_DAY`` (the mushroom itself is planted earlier
   when it is an imitater).
4. Fodder rotates over ``fodder_rows``; default column 9.
5. ``operation_cs = effect_cs - timing.effect_offset(kind, scene, row, imitater, variant)``.
6. Allocation failures are reported as issues, never exceptions.
7. Untimed shorthand cobs: the activating group of a wave of known length
   ``L`` lands at ``max(341, L - 200)`` (:func:`rhythm_cob_effect`); S/B/D
   companions are placed relative to it; huge waves add ``HUGE_WAVE_DELAY``;
   unknown length -> 341 plus an ``info:RHYTHM_LENGTH_UNKNOWN`` issue.

Time resolution for ``.pvzt`` ops: windows (``539~619``) take the window
start; relative times (``+N``) resolve against the previous action of the
same half-wave.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..enums import IMITATER_OFFSET, PlantType, Scene
from ..timing import (
    COB_CD,
    HUGE_WAVE_RED_TEXT,
    ICE_ACTIVATE_DAY,
    WAVE_NATURAL_MIN,
    card_cd,
    effect_offset,
    is_closing_wave,
    is_huge_wave,
)
from .ast import (
    KIND_ASH,
    KIND_COB,
    KIND_DOOM,
    KIND_FODDER,
    KIND_ICE,
    KIND_NATURAL,
    PHASE_PRE,
    Diagnostic,
    HalfWave,
    Op,
    Target,
    Track,
    Wave,
)

__all__ = [
    "Action",
    "ExpandContext",
    "Plan",
    "expand",
    "KIND_COFFEE",
    "DEFAULT_COB_EFFECT_CS",
    "DEFAULT_ICE_EFFECT_CS",
    "DEFAULT_FODDER_EFFECT_CS",
    "DEFAULT_ASH_EFFECT_CS",
    "DEFAULT_DOOM_EFFECT_CS",
    "DEFAULT_PRE_EFFECT_CS",
    "DEFAULT_FODDER_COL",
    "REFRESH_DELAY_CS",
    "ACTIVATE_EARLIEST_CS",
    "WAVE_MIN_CS",
    "HUGE_WAVE_DELAY",
    "SYNC_BEFORE_CS",
    "SYNC_DELAY_CS",
    "rhythm_cob_effect",
    "ROOF_SLOPE_MAX_COL",
    "mirror_row",
    "roof_cannon_class",
]

# --- default effect times for shorthand ops (HEURISTICS) --------------------
# Shorthand (§3) carries no times. These are community conventions used only
# to produce *some* operation axis; every plan built from them carries an
# ``info:DEFAULT_TIMING`` issue. They are not derived from game mechanics.
DEFAULT_COB_EFFECT_CS = 341  # post-phase cob: PE "PP 341" (cob lands 341 after refresh)
DEFAULT_ICE_EFFECT_CS = 1  # pre-phase ice: perfect predicted ice (effect 1cs after refresh)
DEFAULT_FODDER_EFFECT_CS = 134  # fodder planted at 134 (after the wave's zombies are placed)
DEFAULT_ASH_EFFECT_CS = 341  # cherry / jalapeno / squash: same slot as the activation cob
DEFAULT_DOOM_EFFECT_CS = 341  # doom-shroom: same slot as the activation cob
DEFAULT_PRE_EFFECT_CS = -1  # any other pre-phase op: "just before the refresh"
DEFAULT_FODDER_COL = 9.0  # §7 rule 4: 前场列 9

KIND_COFFEE = "coffee"  # synthetic action kind for day-scene mushrooms

# --- rhythm-derived activation timing (§7 rule 7) ----------------------------
# GAME MECHANICS (source-map §6 / Board::UpdateGameObjects): the next wave
# refreshes REFRESH_DELAY_CS after the current wave's total HP drops below
# the threshold, and that check cannot succeed before wave time
# ACTIVATE_EARLIEST_CS. So the shortest wave is WAVE_MIN_CS, and to make a
# wave last L the activating cob must *land* at L - REFRESH_DELAY_CS (never
# earlier than DEFAULT_COB_EFFECT_CS = 341, which is the landing that gives
# the 601 minimum: 341 -> trigger 401 -> refresh 601). P4 (869): land 669.
REFRESH_DELAY_CS = 200
ACTIVATE_EARLIEST_CS = 401
WAVE_MIN_CS = ACTIVATE_EARLIEST_CS + REFRESH_DELAY_CS  # 601
# COMMUNITY CONVENTION, NOT a derived game constant: huge waves (w10/w20)
# spawn their zombies later and AvZ / Be2SiO4 practice delays the activation
# by one more "341". Unverified against the binary (the source map has no
# flag-wave spawn timing); treat as conservative.
HUGE_WAVE_DELAY = 341
# Relative defaults of the companion cob groups (community convention):
# S (sync) lands on the same tick as the activating group, B (before) lands
# SYNC_BEFORE_CS earlier, D (delay) lands SYNC_DELAY_CS later. TODO: these
# two spacings are placeholders; real tracks write explicit times.
SYNC_BEFORE_CS = 100
SYNC_DELAY_CS = 100


def rhythm_cob_effect(length_cs: Optional[int]) -> Optional[int]:
    """Landing time of the activating cob for a wave of ``length_cs``.

    ``max(341, L - 200)`` for ``L >= WAVE_MIN_CS``; a shorter or unknown
    length yields ``None`` (caller falls back to ``DEFAULT_COB_EFFECT_CS``).
    Note ``L = 601`` gives 401 while the AvZ P6 convention fires at -32
    (landing 341): both produce a 601 wave because the trigger cannot happen
    before 401 anyway.
    """
    if length_cs is None or length_cs < WAVE_MIN_CS:
        return None
    return max(DEFAULT_COB_EFFECT_CS, length_cs - REFRESH_DELAY_CS)

# §7 rule 2 (屋顶按列区分风炮 / 平炮). Community convention, not a game
# constant: the roof slope spans columns 1-5 and the flat part 6-9. A cannon
# is classed by its tail column (the left cell of the two it occupies, as in
# Seml ``P3``), a shot by its landing column. The two classes differ in
# trajectory, so a shot is served by a cannon of the matching class first.
ROOF_SLOPE_MAX_COL = 5
ROOF_SLOPE = "slope"  # 风炮
ROOF_FLAT = "flat"  # 平炮

_PLANT_BY_KIND_VARIANT: Dict[Tuple[str, Optional[str]], Optional[PlantType]] = {
    (KIND_ASH, "cherry"): PlantType.CHERRY_BOMB,
    (KIND_ASH, "jalapeno"): PlantType.JALAPENO,
    (KIND_ASH, "squash"): PlantType.SQUASH,
    (KIND_ASH, "light"): None,  # be2sio4 'a': 辣椒/窝瓜/土豆雷 — undetermined
}


@dataclass(frozen=True)
class Action:
    """One concrete game operation (§7 output)."""

    wave: int
    effect_cs: int
    operation_cs: int
    kind: str  # cob | ash | doom | ice | fodder | coffee
    symbol: str
    plant: Optional[int]  # PlantType id (+IMITATER_OFFSET for imitater cards); None = auto
    target: Optional[Target]
    cannon: Optional[Tuple[int, int]]  # assigned cannon (row, col) for cob actions
    source_op: Op


@dataclass(frozen=True)
class ExpandContext:
    """Field information needed to turn symbols into concrete actions (§7)."""

    scene: str
    cob_positions: Tuple[Tuple[int, int], ...] = ()
    ice_slots: Tuple[Tuple[int, int], ...] = ()
    fodder_rows: Tuple[int, ...] = ()
    wave_count: int = 20
    default_cob_target_rows: Optional[Tuple[int, int]] = None
    default_cob_col: float = 9.0


@dataclass(frozen=True)
class Plan:
    actions: Tuple[Action, ...]
    issues: Tuple[Diagnostic, ...]


def mirror_row(row: int, scene: Scene) -> int:
    """Row symmetric about the field's middle (PE/FE 1<->6; 5-row 1<->5, 3<->3)."""
    return scene.rows + 1 - row


def roof_cannon_class(col: float) -> str:
    """``"slope"`` (风炮) for columns ≤ ``ROOF_SLOPE_MAX_COL``, else ``"flat"`` (平炮)."""
    return ROOF_SLOPE if col <= ROOF_SLOPE_MAX_COL else ROOF_FLAT


def _default_cob_rows(ctx: ExpandContext, scene: Scene) -> Tuple[int, int]:
    if ctx.default_cob_target_rows is not None:
        return ctx.default_cob_target_rows
    return (2, 5) if scene.rows == 6 else (2, 4)


def _plant_id(op: Op) -> Optional[int]:
    """PlantType id for an op; imitater variants get ``+IMITATER_OFFSET``."""
    if op.kind == KIND_COB:
        return int(PlantType.COB_CANNON)
    if op.kind == KIND_ICE:
        base = int(PlantType.ICE_SHROOM)
    elif op.kind == KIND_DOOM:
        base = int(PlantType.DOOM_SHROOM)
    elif op.kind == KIND_ASH:
        plant = _PLANT_BY_KIND_VARIANT.get((KIND_ASH, op.variant))
        return None if plant is None else int(plant)
    else:
        return None  # fodder: auto-picked card (enums.CARD_FODDER_LETTERS)
    return base + IMITATER_OFFSET if op.variant == "imitater" else base


def _default_effect(op: Op, base_cob: int = DEFAULT_COB_EFFECT_CS) -> int:
    """Heuristic effect time for a shorthand op (see module constants).

    ``base_cob`` is the landing time of the wave's activating cob group
    (:func:`rhythm_cob_effect`, or 341); companion groups are placed relative
    to it: S same tick, B ``SYNC_BEFORE_CS`` earlier, D ``SYNC_DELAY_CS`` later.
    """
    if op.kind == KIND_ICE:
        return DEFAULT_ICE_EFFECT_CS
    if op.phase == PHASE_PRE:
        return DEFAULT_PRE_EFFECT_CS
    if op.kind == KIND_COB:
        if op.variant in ("before", "before_back"):
            return base_cob - SYNC_BEFORE_CS
        if op.variant in ("delay", "delay_back"):
            return base_cob + SYNC_DELAY_CS
        return base_cob
    if op.kind == KIND_ASH:
        return DEFAULT_ASH_EFFECT_CS
    if op.kind == KIND_DOOM:
        return DEFAULT_DOOM_EFFECT_CS
    return DEFAULT_FODDER_EFFECT_CS


# --- wave layout -------------------------------------------------------------
def _layout_waves(track: Track, ctx: ExpandContext, issues: List[Diagnostic]) -> Dict[int, Wave]:
    """Map actual wave number -> Wave (§7 rule 1).

    Shorthand with selectors exactly ``1..n`` is a cycle tiled over
    ``1..wave_count``. Any other selector set (``wN:`` shorthand mode, pvzt)
    is taken as actual wave numbers; later blocks override earlier ones for
    the same wave (this is how w10/w20 flag-wave overrides are written).
    """
    waves = track.waves
    out: Dict[int, Wave] = {}
    selectors = sorted({n for w in waves for n in w.selector})
    is_cycle = track.kind == "shorthand" and selectors == list(range(1, len(waves) + 1))
    if is_cycle:
        by_index = {w.selector[0]: w for w in waves}
        n = len(waves)
        for wave_no in range(1, ctx.wave_count + 1):
            out[wave_no] = by_index[(wave_no - 1) % n + 1]
            if is_huge_wave(wave_no):
                issues.append(Diagnostic("info", "HUGE_WAVE", f"w{wave_no} is a huge wave; cycle entry {(wave_no - 1) % n + 1} applied with +{HUGE_WAVE_DELAY}cs on untimed cobs", wave_no))
            elif is_closing_wave(wave_no):
                issues.append(Diagnostic("info", "CLOSING_WAVE", f"w{wave_no} is a closing wave; cycle entry {(wave_no - 1) % n + 1} applied unchanged (closing usually delayed)", wave_no))
        return out
    for wave in waves:
        for wave_no in wave.selector:
            if wave_no < 1 or wave_no > ctx.wave_count:
                issues.append(Diagnostic("warning", "WAVE_OUT_OF_RANGE", f"selector w{wave_no} outside 1..{ctx.wave_count}; ignored", wave_no))
                continue
            if wave_no in out:
                issues.append(Diagnostic("info", "WAVE_OVERRIDDEN", f"w{wave_no} defined by several blocks; the later block wins", wave_no))
            out[wave_no] = wave
    for wave_no in range(1, ctx.wave_count + 1):
        if wave_no not in out:
            issues.append(Diagnostic("info", "WAVE_EMPTY", f"w{wave_no} has no operations", wave_no))
    return out


def _wave_length(wave: Optional[Wave]) -> Optional[int]:
    """Declared wave length (``len`` or the lower bound of a range), if any."""
    if wave is None:
        return None
    return wave.length_cs if wave.length_cs is not None else (wave.length_range[0] if wave.length_range else None)


def _untimed_cobs(half: Optional[HalfWave]) -> List[Op]:
    """Post-phase cob ops of a half-wave that carry no effect time (rule 7)."""
    if half is None:
        return []
    return [op for op in half.ops if op.kind == KIND_COB and op.phase != PHASE_PRE and op.effect_cs is None and op.window_cs is None]


def _has_untimed_cobs(wave: Optional[Wave]) -> bool:
    return wave is not None and bool(_untimed_cobs(wave.top) or _untimed_cobs(wave.bottom))


def _wave_starts(layout: Dict[int, Wave], ctx: ExpandContext, issues: List[Diagnostic]) -> Dict[int, int]:
    """Absolute refresh time (cs from w1 refresh) of each wave, for CD checks."""
    starts: Dict[int, int] = {}
    t = 0
    for wave_no in range(1, ctx.wave_count + 1):
        starts[wave_no] = t
        wave = layout.get(wave_no)
        length = _wave_length(wave)
        activated = _has_untimed_cobs(wave)
        if length is None:
            # Must agree with _resolve_half: an untimed activating cob lands
            # at DEFAULT_COB_EFFECT_CS (341) -> the wave lasts WAVE_MIN_CS
            # (601), so the CD timeline has to use 601 as well -- the natural
            # 2500 only applies when nothing activates the wave.
            length = WAVE_MIN_CS if activated else WAVE_NATURAL_MIN
            if wave_no < ctx.wave_count:
                what = "activated" if activated else "natural"
                issues.append(Diagnostic("info", "LEN_MISSING", f"w{wave_no} has no length; assuming {what} minimum {length}cs for CD checks", wave_no))
        if is_huge_wave(wave_no) and activated:
            length += HUGE_WAVE_DELAY  # rule 7: the activation is delayed, so is the refresh
        if is_huge_wave(wave_no + 1):
            # w9 / w19: the "A huge wave..." banner runs before w10 / w20
            # refreshes, whatever activated the closing wave
            length += HUGE_WAVE_RED_TEXT
        t += length
    return starts


# --- allocation state ---------------------------------------------------------
class _CobQueue:
    """Ready queue keyed by last fire time (§7 rule 2).

    ``take`` returns ``(cannon, class_matched)``; ``wanted`` restricts the
    first pass to cannons of one roof class (``"slope"`` / ``"flat"``) and
    the second pass takes any ready cannon with ``class_matched=False``.
    """

    def __init__(self, positions: Tuple[Tuple[int, int], ...]) -> None:
        self.last_fire: Dict[Tuple[int, int], Optional[int]] = {p: None for p in positions}

    def _earliest(self, fire_abs: int, wanted: Optional[str]) -> Optional[Tuple[int, int]]:
        best: Optional[Tuple[int, int]] = None
        best_last: Optional[int] = None
        for pos, last in self.last_fire.items():
            if wanted is not None and roof_cannon_class(pos[1]) != wanted:
                continue
            # never fired (None) sorts first; ties broken by position
            key = (last is not None, last if last is not None else 0, pos)
            if best is None or key < (best_last is not None, best_last if best_last is not None else 0, best):
                best, best_last = pos, last
        if best is None or (best_last is not None and best_last + COB_CD > fire_abs):
            return None
        return best

    def take(self, fire_abs: int, wanted: Optional[str] = None) -> Tuple[Optional[Tuple[int, int]], bool]:
        cannon = self._earliest(fire_abs, wanted)
        matched = True
        if cannon is None and wanted is not None:
            cannon = self._earliest(fire_abs, None)
            matched = False
        if cannon is not None:
            self.last_fire[cannon] = fire_abs
        return cannon, matched


class _Rotation:
    """Round-robin over slots/rows with an optional card CD check."""

    def __init__(self, items: Tuple, cd: int) -> None:
        self.items = items
        self.cd = cd
        self.idx = 0
        self.last_use: Dict[object, int] = {}

    def take(self, plant_abs: int) -> Tuple[object, bool]:
        """Return ``(item, ready)``; ``item`` is None when there are no slots."""
        if not self.items:
            return None, False
        item = self.items[self.idx % len(self.items)]
        self.idx += 1
        last = self.last_use.get(item)
        ready = last is None or last + self.cd <= plant_abs
        self.last_use[item] = plant_abs
        return item, ready


@dataclass
class _Unit:
    """One cannon/card of an op with its resolved effect time and row."""

    op: Op
    effect_cs: int
    row: Optional[int]
    col: Optional[float]
    mirrored_copy: bool = False


# --- per-wave expansion ----------------------------------------------------
def _resolve_half(
    half: HalfWave,
    wave_no: int,
    ctx: ExpandContext,
    scene: Scene,
    mirror: bool,
    issues: List[Diagnostic],
    defaults_used: List[bool],
    length_cs: Optional[int] = None,
) -> List[_Unit]:
    units: List[_Unit] = []
    prev_effect: Optional[int] = None
    # §7 rule 7: untimed post-phase cobs land relative to the rhythm length.
    base_cob = rhythm_cob_effect(length_cs)
    untimed_cobs = _untimed_cobs(half)
    if base_cob is None:
        base_cob = DEFAULT_COB_EFFECT_CS
        if untimed_cobs and length_cs is None:
            issues.append(Diagnostic("info", "RHYTHM_LENGTH_UNKNOWN", f"w{wave_no} has no length; activating cob defaults to landing {DEFAULT_COB_EFFECT_CS}cs (601 wave)", wave_no, untimed_cobs[-1].symbol))
    if is_huge_wave(wave_no):
        base_cob += HUGE_WAVE_DELAY
    for op in half.ops:
        if op.kind == KIND_NATURAL:
            continue
        # --- time (§4.2): window -> start; '+N' -> previous action; None -> heuristic
        if op.window_cs is not None:
            effect = op.window_cs[0]
        elif op.effect_cs is None:
            effect = _default_effect(op, base_cob)
            defaults_used[0] = True
        elif op.relative:
            if prev_effect is None:
                issues.append(Diagnostic("warning", "RELATIVE_NO_PREV", f"'+{op.effect_cs}' has no previous action; resolved against the refresh point", wave_no, op.symbol))
                effect = op.effect_cs
            else:
                effect = prev_effect + op.effect_cs
        else:
            effect = op.effect_cs
        prev_effect = effect
        # --- rows / cols
        rows: Tuple[Optional[int], ...]
        col: Optional[float]
        if op.target is not None:
            rows, col = op.target.rows, op.target.col
        elif op.kind in (KIND_COB, KIND_ASH):
            rows, col = _default_cob_rows(ctx, scene), ctx.default_cob_col
        elif op.kind == KIND_FODDER:
            rows, col = (None,), DEFAULT_FODDER_COL
        else:
            rows, col = (None,), None
        for i in range(op.count):
            row = rows[i % len(rows)]
            units.append(_Unit(op, effect, row, col))
            if mirror and op.kind in (KIND_COB, KIND_ASH) and row is not None:
                units.append(_Unit(op, effect, mirror_row(row, scene), col, mirrored_copy=True))
    return units


def expand(track: Track, ctx: ExpandContext) -> Plan:
    """Expand a track into a concrete :class:`Plan` (§7).

    Actions are ordered by ``(wave, effect_cs, operation_cs)``. Cannon
    assignment happens in firing (operation-axis) order so a later-landing
    but earlier-fired cob takes the earlier cannon.
    """
    issues: List[Diagnostic] = []
    scene = Scene.from_code(ctx.scene)
    if track.scene is not None and track.scene.upper() != scene.code:
        issues.append(Diagnostic("warning", "SCENE_MISMATCH", f"track says scene {track.scene}, context says {scene.code}; using {scene.code}"))
    layout = _layout_waves(track, ctx, issues)
    starts = _wave_starts(layout, ctx, issues)

    cobs = _CobQueue(ctx.cob_positions)
    ices = _Rotation(ctx.ice_slots, card_cd(PlantType.ICE_SHROOM))
    fodder = _Rotation(ctx.fodder_rows, 0)
    defaults_used = [False]
    actions: List[Action] = []

    for wave_no in range(1, ctx.wave_count + 1):
        wave = layout.get(wave_no)
        if wave is None:
            continue
        length = _wave_length(wave)
        units = _resolve_half(wave.top, wave_no, ctx, scene, wave.mirrored, issues, defaults_used, length)
        if wave.bottom is not None:
            units += _resolve_half(wave.bottom, wave_no, ctx, scene, False, issues, defaults_used, length)

        # compute operation times first, then allocate in firing order
        pending: List[Tuple[int, int, int, _Unit]] = []
        for order, u in enumerate(units):
            imit = u.op.variant == "imitater"
            if u.op.kind == KIND_FODDER:
                offset = 0
            else:
                offset = effect_offset(u.op.kind, scene, u.row, imit, u.op.variant)
            pending.append((u.effect_cs - offset, order, u.effect_cs, u))
        pending.sort(key=lambda p: (p[0], p[1]))

        wave_actions: List[Action] = []
        for operation_cs, _order, effect_cs, u in pending:
            op = u.op
            row, col = u.row, u.col
            cannon: Optional[Tuple[int, int]] = None
            abs_op = starts[wave_no] + operation_cs
            if op.kind == KIND_COB and op.variant != "replant":
                wanted = roof_cannon_class(col) if scene.needs_pot and col is not None else None
                cannon, matched = cobs.take(abs_op, wanted)
                if cannon is None:
                    issues.append(Diagnostic("warning", "COB_SHORTAGE", f"no cannon ready at {operation_cs}cs (effect {effect_cs}cs) for {op.symbol}", wave_no, op.symbol))
                elif not matched:
                    issues.append(Diagnostic("warning", "ROOF_CANNON_CLASS", f"no {wanted} cannon ready for {op.symbol} landing at column {col}; {roof_cannon_class(cannon[1])} cannon {cannon} used", wave_no, op.symbol))
            elif op.kind == KIND_ICE:
                slot, ready = ices.take(abs_op)
                if slot is None:
                    issues.append(Diagnostic("warning", "ICE_SHORTAGE", f"no ice slot configured for {op.symbol}", wave_no, op.symbol))
                else:
                    if row is None:
                        row, col = slot
                    if not ready:
                        issues.append(Diagnostic("warning", "ICE_SHORTAGE", f"ice slot {slot} reused within card CD", wave_no, op.symbol))
            elif op.kind == KIND_FODDER and row is None:
                frow, _ready = fodder.take(abs_op)
                if frow is None:
                    issues.append(Diagnostic("warning", "FODDER_NO_ROW", f"no fodder row configured for {op.symbol}; row left unassigned", wave_no, op.symbol))
                else:
                    row = frow
            target = Target((row,), col) if row is not None and col is not None else None
            action = Action(wave_no, effect_cs, operation_cs, op.kind, op.symbol, _plant_id(op), target, cannon, op)
            wave_actions.append(action)
            if op.kind in (KIND_ICE, KIND_DOOM) and scene.is_day:
                # §7 rule 3: day scenes need a coffee bean at effect - 299. For a
                # plain mushroom that is the planting slot too; an imitater is
                # planted IMITATER_EXTRA earlier and the coffee waits for it.
                wave_actions.append(Action(wave_no, effect_cs, effect_cs - ICE_ACTIVATE_DAY, KIND_COFFEE, "K", int(PlantType.COFFEE_BEAN), target, None, op))
        wave_actions.sort(key=lambda a: (a.effect_cs, a.operation_cs))
        actions.extend(wave_actions)

    if defaults_used[0]:
        issues.append(Diagnostic("info", "DEFAULT_TIMING", "shorthand ops carry no times; heuristic defaults used (cob %d, ice %d, fodder %d, pre %d)" % (DEFAULT_COB_EFFECT_CS, DEFAULT_ICE_EFFECT_CS, DEFAULT_FODDER_EFFECT_CS, DEFAULT_PRE_EFFECT_CS)))
    return Plan(tuple(actions), tuple(issues))
