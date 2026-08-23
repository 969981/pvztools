"""High-level in-game actions built on remote calls (DESIGN §6, parity §4).

:class:`Actions` turns "put a peashooter at (3,1)" into a per-family call
fragment (:mod:`pvztools.control.conv`), runs it through a
:class:`~pvztools.control.remote.RemoteExecutor` and verifies the result by
re-reading the game.  Every action is gated by

* ``Safety.allow_remote_call`` (level ``remote``) and ``Safety.allow_patch``
  (the ``main_loop_block`` patch wraps every injection),
* the capability grade of the profile (READ_ONLY / UNSUPPORTED refuse),
* ``game_ui in {2, 3}`` (seed chooser / in level) -- Toolkit's rule.

Rows and columns are **1-based** here (the user-facing / lineup convention:
row 1..6, col 1..9); the game's 0-based values are derived internally.

Batching (Toolkit's ``asm_init()`` pattern)::

    with actions.batch("布阵"):
        actions.put_plant(1, 2, "荷叶")
        actions.put_plant(1, 2, "玉米加农炮")
    # -> one remote call containing both fragments

Inside a batch the methods return ``None`` (the result cannot be verified
until the batch is flushed); :attr:`Actions.history` keeps every submitted
:class:`~pvztools.control.conv.Fragment` with its op sequence.

A real :class:`~pvztools.modifier.patches.PatchManager` refuses to toggle
``main_loop_block`` as a standalone feature; :class:`RemotePatchManager`
relaxes that rule *only* when ``Safety.allow_remote_call`` is set, so the
executor can block the main loop around an injection.
"""
from __future__ import annotations

import contextlib
import re
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from pvztools.modifier.patches import PatchManager, PermissionDenied, STRICT_ALLOWED_PATCHES
from pvztools.modifier.safety import Safety, SafetyError
from pvztools.observe.snapshot import NotInLevel
from pvztools.schema.enums import PLANT_ZH, ZOMBIE_CHARS, ZOMBIE_ZH, PlantType, ZombieType

from .conv import COB_CANNON, MAY_SLEEP, ZOMBOSS, Fragment, NotSupported, convention_for
from .conv.base import mov_reg_reg
from . import grid as _grid

__all__ = [
    "Actions", "ActionError", "RemotePatchManager", "BatchResult", "FireResult", "CobInfo",
    "COB_READY", "COB_FIRING", "COB_ARMING", "COB_LOADING", "COB_STATE_NAMES",
    "resolve_plant", "resolve_zombie", "plant_label",
    "CONTAINERS", "SHELL", "ADDON", "GROW_COUNTDOWN_OFFSET", "GRID_ITEM_TYPES",
    "COLS", "UI_ALLOWED",
]

#: Seed types that form the "container" layer (lily pad / flower pot).
CONTAINERS = (int(PlantType.LILY_PAD), int(PlantType.FLOWER_POT))
SHELL = int(PlantType.PUMPKIN)
ADDON = int(PlantType.COFFEE_BEAN)
COLS = 9
#: Toolkit: UI 2 (seed chooser) or 3 (in level).
UI_ALLOWED = (2, 3)
#: Potato mine / sun-shroom "grow up now": ``mov dword [plant+off], 1``
#: (Plant.mStateCountdown; Toolkit §3.14: 0x54, BETA 0x5c).
GROW_COUNTDOWN_OFFSET: Dict[str, int] = {
    "goty": 0x54, "goty_zh2010_ja": 0x54, "goty_zh2012": 0x54, "retail": 0x54, "beta": 0x5C,
}
GROW_PLANTS = (int(PlantType.POTATO_MINE), int(PlantType.SUN_SHROOM))
#: Grid item types (Toolkit §2.7).
GRID_ITEM_TYPES: Dict[str, int] = {
    "grave": 1, "tomb": 1, "crater": 2, "ladder": 3, "portal": 4, "portal2": 5,
    "vase": 7, "snail": 10, "rake": 11, "brain": 12,
}
#: Game modes "I, Zombie" (plants get the IZ style).
IZ_MODES = range(61, 71)
#: Day scenes (mushrooms fall asleep).
DAY_SCENES = (0, 2, 4)
#: Highest placeable plant type (47 = cob cannon); 48 (imitater) is a seed
#: id, never a plant on the board by itself.
MAX_PLANT = int(PlantType.COB_CANNON)
#: Plant.mState for cob cannons (source-map §6.2).
COB_ARMING, COB_LOADING, COB_READY, COB_FIRING = 35, 36, 37, 38
COB_STATE_NAMES: Dict[int, str] = {COB_ARMING: "arming", COB_LOADING: "loading",
                                   COB_READY: "ready", COB_FIRING: "firing"}
#: §6.3 / §7.2: ARMING -> ready = state_countdown + charge anim (~475 cs);
#: LOADING -> ready ~ 175 cs (charge animation, not readable precisely).
COB_CHARGE_CS = 475
COB_LOADING_CS = 175
#: ``mShootingCounter`` written by ``CobCannonFire`` (source-map §6); the
#: game decrements it once per tick -> ``fired_tick = now - (206 - counter)``
COB_SHOOTING_COUNTER = 206


class ActionError(SafetyError):
    """Bad argument / unverifiable result of an action."""


# ---------------------------------------------------------------------------
# name resolution
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s).lower()


_PLANT_EN: Dict[str, int] = {_norm(m.name): int(m) for m in PlantType}
_PLANT_ZH: Dict[str, int] = {v: int(k) for k, v in PLANT_ZH.items()}
_PLANT_ALIASES: Dict[str, int] = {
    "炮": int(PlantType.COB_CANNON), "玉米炮": int(PlantType.COB_CANNON), "cob": int(PlantType.COB_CANNON),
    "cobcannon": int(PlantType.COB_CANNON), "睡莲": int(PlantType.LILY_PAD), "lily": int(PlantType.LILY_PAD),
    "pot": int(PlantType.FLOWER_POT), "南瓜": int(PlantType.PUMPKIN), "咖啡": int(PlantType.COFFEE_BEAN),
    "坚果墙": int(PlantType.WALL_NUT), "冰": int(PlantType.ICE_SHROOM), "冰菇": int(PlantType.ICE_SHROOM),
    "核": int(PlantType.DOOM_SHROOM), "曾": int(PlantType.GLOOM_SHROOM), "曾哥": int(PlantType.GLOOM_SHROOM),
    "冰瓜": int(PlantType.WINTER_MELON), "西瓜": int(PlantType.MELON_PULT),
    "豌豆": int(PlantType.PEASHOOTER), "樱桃": int(PlantType.CHERRY_BOMB), "辣椒": int(PlantType.JALAPENO),
    "香蒲": int(PlantType.CATTAIL), "猫": int(PlantType.CATTAIL),
}
_ZOMBIE_EN: Dict[str, int] = {_norm(m.name): int(m) for m in ZombieType}
_ZOMBIE_ZH: Dict[str, int] = {v: int(k) for k, v in ZOMBIE_ZH.items()}
_ZOMBIE_ALIASES: Dict[str, int] = {
    "普僵": 0, "红眼": 32, "白眼": 23, "巨人": 23, "僵王": 25, "zomboss": 25, "giga": 32,
}


def resolve_plant(plant: Union[int, str, PlantType]) -> int:
    """Plant id from an int / ``PlantType`` / Chinese or English name / nickname.

    Delegates to :func:`pvztools.names.resolve_plant` (official zh / en names,
    community aliases such as 曾哥 / gloom / 春哥 / cob, enum names, ids).
    """
    from pvztools.names import UnknownName, resolve_plant as _resolve_plant
    if isinstance(plant, bool):
        raise ActionError("植物类型不能是 bool：%r" % (plant,))
    if isinstance(plant, int):
        pid = int(plant)
    else:
        try:
            pid = int(_resolve_plant(plant))
        except UnknownName as e:
            raise ActionError(str(e)) from None
    if pid == int(PlantType.IMITATER):
        # 48 is only ever pushed together with a real type (Toolkit: imitater
        # checkbox); AddPlant(48, -1) is an untested game state.
        raise ActionError("模仿者(48) 不是可直接放置的植物：请放置目标植物并加 imitater=True / --imitater")
    if not (0 <= pid <= MAX_PLANT):
        raise ActionError("植物编号 %d 超出 0..%d" % (pid, MAX_PLANT))
    return pid


def resolve_zombie(zombie: Union[int, str, ZombieType]) -> int:
    """Zombie id from an int / ``ZombieType`` / name / AvZ single character."""
    if isinstance(zombie, bool):
        raise ActionError("僵尸类型不能是 bool：%r" % (zombie,))
    if isinstance(zombie, int):
        zid = int(zombie)
    else:
        s = str(zombie).strip()
        if s.isdigit():
            zid = int(s)
        elif s in ZOMBIE_CHARS:
            zid = int(ZOMBIE_CHARS[s])
        else:
            key = _norm(s)
            zid = _ZOMBIE_ZH.get(s, _ZOMBIE_ALIASES.get(s, _ZOMBIE_EN.get(key, _ZOMBIE_ALIASES.get(key, -1))))
            if zid < 0:
                raise ActionError("不认识的僵尸 %r（接受中文名如 红眼巨人、单字如 红、英文名、或 0..32 的编号）" % (zombie,))
    if not (0 <= zid <= int(ZombieType.GIGA_GARGANTUAR)):
        raise ActionError("僵尸编号 %d 超出 0..%d" % (zid, int(ZombieType.GIGA_GARGANTUAR)))
    return zid


def plant_label(pid: int) -> str:
    try:
        return PLANT_ZH[PlantType(pid)]
    except (ValueError, KeyError):
        return "植物(%d)" % pid


# ---------------------------------------------------------------------------
# PatchManager that lets the executor toggle main_loop_block
# ---------------------------------------------------------------------------

class RemotePatchManager(PatchManager):
    """A :class:`PatchManager` whose *only* extra permission is toggling the
    internal ``main_loop_block`` group while ``Safety.allow_remote_call`` is
    set (the executor needs it around every injection).  Everything else --
    ``allow_patch``, capability, strict purity, writable session -- is still
    enforced exactly as in the base class."""

    _REMOTE_INTERNAL = ("main_loop_block",)

    def _authorize(self, key: str, action: str) -> None:
        if key in self._REMOTE_INTERNAL and self.safety.allow_remote_call:
            s = self.safety
            if s.strict and key not in STRICT_ALLOWED_PATCHES:
                raise PermissionDenied("purity", "%s %s" % (action, key))
            s.require_flag("allow_patch", "%s %s" % (action, key))
            s.require_capability("%s %s" % (action, key), self.profile)
            s.require_session_writable(self.session, "%s %s" % (action, key))
            return
        super()._authorize(key, action)


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    """What a flushed batch did: the merged fragment and the action labels."""
    names: List[str] = field(default_factory=list)
    fragment: Optional[Fragment] = None
    calls: int = 0
    result: Any = None

    @property
    def empty(self) -> bool:
        return self.calls == 0


@dataclass
class FireResult:
    """One ``cob_fire`` call: the plant fired, the click pixels passed to the
    game and the cob state before / after (expect 37 -> 38; ``None`` after a
    dry run or inside a batch).

    Frame-lock fields (DESIGN §6.3): ``frozen_tick`` is the ``main_counter``
    the game was parked on while ``CobCannonFire`` ran; ``requested_tick`` /
    ``late_by`` echo the ``at_tick`` lock (``late_by = frozen - requested``);
    ``fired_tick`` is read back from the game itself --
    ``CobCannonFire`` sets ``mShootingCounter = 206`` and the game
    decrements it once per tick, so ``fired_tick = main_counter_now -
    (206 - shooting_counter)`` (source-map §6).  ``shooting_counter`` is the
    raw value read.  All ``None`` in dry-run / batch or when the profile
    lacks the fields."""
    plant_index: int
    x: int
    y: int
    state_before: int
    state_after: Optional[int] = None
    target: Tuple[int, float] = (0, 0.0)
    frozen_tick: Optional[int] = None
    fired_tick: Optional[int] = None
    requested_tick: Optional[int] = None
    late_by: Optional[int] = None
    shooting_counter: Optional[int] = None
    #: block / release cycles the frame lock needed (``RemoteResult.attempts``)
    attempts: Optional[int] = None

    @property
    def fired(self) -> bool:
        return self.state_after == COB_FIRING


@dataclass
class CobInfo:
    """A cob cannon on the board (``row`` / ``col`` 1-based, ``col`` = left cell)."""
    index: int
    row: int
    col: int
    state: int
    countdown: Optional[int]

    @property
    def ready(self) -> bool:
        return self.state == COB_READY

    @property
    def state_name(self) -> str:
        return COB_STATE_NAMES.get(self.state, "state(%d)" % self.state)

    def __str__(self) -> str:
        cd = "" if self.countdown is None else " ~%d cs" % self.countdown
        return "#%d (%d,%d) %s%s" % (self.index, self.row, self.col, self.state_name, cd)


#: ``cob`` argument of :meth:`Actions.fire_cob`: array index or 1-based (row, col).
CobRef = Union[int, Tuple[int, int]]


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class Actions:
    """Remote-call actions on the attached game (see module doc)."""

    def __init__(self, session: Any, profile: Any, safety: Optional[Safety], observer: Any,
                 executor: Any, patches: Any, conv: Any = None) -> None:
        self.session = session
        self.profile = profile
        self.safety = safety if safety is not None else Safety()
        self.observer = observer
        self.executor = executor
        self.patches = patches
        self.conv = conv if conv is not None else convention_for(profile)
        self.family = getattr(profile, "conventions", None) or getattr(profile, "family", "")
        #: every fragment submitted (batched ones are merged into one entry)
        self.history: List[Fragment] = []
        self._batch: Optional[Fragment] = None
        self._batch_names: List[str] = []
        self._batch_calls = 0

    # -- gating -------------------------------------------------------------------
    def check(self, action: str = "远程调用", ui: Iterable[int] = UI_ALLOWED) -> int:
        """All preconditions; returns the current ``game_ui``."""
        self.safety.require_level("remote", action)
        self.safety.require_flag("allow_patch", action, "远程调用需要临时启用 main_loop_block 补丁")
        self.safety.require_capability(action, self.profile)
        self.safety.require_session_writable(self.session, action)
        return self.safety.require_ui(self.session, self.profile, ui, action)

    # -- game pointers ------------------------------------------------------------
    def board(self) -> int:
        board = self.observer.board_ptr()
        if not board:
            raise NotInLevel()
        return board

    def _board_field(self, name: str) -> int:
        off = self.profile.board.get(name)
        if off is None:
            raise NotSupported(name, str(self.family), "board.%s missing in profile" % name)
        return off

    def challenge(self, board: Optional[int] = None) -> int:
        board = self.board() if board is None else board
        return self.session.read_ptr(board + self._board_field("challenge"))

    def cutscene(self, board: Optional[int] = None) -> int:
        board = self.board() if board is None else board
        return self.session.read_ptr(board + self._board_field("cutscene"))

    def scene(self, board: Optional[int] = None) -> int:
        board = self.board() if board is None else board
        return self.session.read_i32(board + self._board_field("scene"))

    def game_mode(self) -> int:
        return self.safety.current_mode(self.session, self.profile)

    def rows(self, board: Optional[int] = None) -> int:
        return 6 if self.scene(board) in (2, 3) else 5

    def plant_addr(self, index: int, board: Optional[int] = None) -> int:
        board = self.board() if board is None else board
        base = self.session.read_ptr(board + self._board_field("plants"))
        count = self.session.read_i32(board + self._board_field("plant_count_max"))
        if not (0 <= index < count):
            raise ActionError("植物下标 %d 超出 0..%d" % (index, count - 1))
        return base + index * self.profile.objects["plant"].stride

    # -- geometry ------------------------------------------------------------------
    def _cell(self, row: int, col: int, board: int, cob: bool = False) -> Tuple[int, int]:
        """Validate 1-based (row, col); return 0-based."""
        rows = self.rows(board)
        if not isinstance(row, int) or not isinstance(col, int) or isinstance(row, bool) or isinstance(col, bool):
            raise ActionError("行列必须是整数：row=%r col=%r" % (row, col))
        if not (1 <= row <= rows):
            raise ActionError("行 %d 超出 1..%d（当前场景 %d）" % (row, rows, self.scene(board)))
        maxcol = COLS - 1 if cob else COLS
        if not (1 <= col <= maxcol):
            raise ActionError("列 %d 超出 1..%d%s" % (col, maxcol, "（玉米加农炮占两格）" if cob else ""))
        return row - 1, col - 1

    # -- submission ---------------------------------------------------------------
    def _asm(self) -> Any:
        return self._batch.asm if self._batch is not None else None

    def _submit(self, frag: Fragment, name: str) -> Any:
        """Run ``frag`` now, or fold it into the open batch."""
        if self._batch is not None:
            if frag.asm is not self._batch.asm:
                self._batch.extend(frag)
            else:
                self._batch.ops.extend(frag.ops)
                self._batch.required_patches = tuple(dict.fromkeys(self._batch.required_patches + frag.required_patches))
                self._batch.notes = tuple(dict.fromkeys(self._batch.notes + frag.notes))
            self._batch_names.append(name)
            self._batch_calls += 1
            return None
        return self._run(frag, name)

    def _run(self, frag: Fragment, name: str, at_tick: Optional[int] = None) -> Any:
        # required_patches (street_zombies_hack, mower_init_*) are enabled by
        # the executor *inside* the main-loop-blocked window, so the game
        # thread never runs a patched site outside the injection
        self.history.append(frag)
        kw: Dict[str, Any] = {}
        if at_tick is not None:
            kw["at_tick"] = int(at_tick)
        if not getattr(frag, "needs_main_loop", True):
            # UI transition (fade_out_level, puzzle_next_stage_clear,
            # sync_profile, show_seed_chooser ...): whole-call block like any
            # other call, but with the longer transition timeout; never
            # frame-locked
            kw["transition"] = True
            kw.pop("at_tick", None)
        return self.executor.run(frag.asm, name=name, extra_patches=tuple(frag.required_patches), **kw)

    def _last_remote_result(self) -> Any:
        """The executor's :class:`~pvztools.control.remote.RemoteResult` for
        the call just made (``None`` for executors that do not record it)."""
        return getattr(self.executor, "last_result", None)

    #: ``_fill_fire_timing``: re-read ``(shooting_counter, main_counter)``
    #: at most this many times until the counter is unchanged across the pair
    FIRE_READBACK_TRIES = 4

    def _fill_fire_timing(self, results: Sequence["FireResult"], board: int) -> None:
        """After the call: state_after, frozen/requested/late_by from the
        executor and ``fired_tick`` from ``mShootingCounter`` (206 at fire,
        -1 per tick).

        The game is running again by now, so ``main_counter`` and the
        plant's ``shooting_counter`` are read as a *pair* bracketed by two
        counter reads and retried until the counter did not move across
        the pair -- a tick landing between the two reads would otherwise
        yield a ``fired_tick`` one too early."""
        f = self.profile.objects["plant"].fields
        sc_off = f.get("shooting_counter")
        mc_off = self.profile.board.get("main_counter")
        rr = self._last_remote_result()
        for r in results:
            addr = self.plant_addr(r.plant_index, board)
            r.state_after = self.session.read_i32(addr + f.state)
            if rr is not None:
                r.frozen_tick = getattr(rr, "frozen_tick", None)
                r.requested_tick = getattr(rr, "requested_tick", None)
                r.late_by = getattr(rr, "late_by", None)
                r.attempts = getattr(rr, "attempts", None)
            if sc_off is not None and mc_off is not None and r.state_after == COB_FIRING:
                pair = self._read_fire_pair(addr + sc_off, board + mc_off)
                if pair is None:
                    continue
                sc, now = pair
                r.shooting_counter = sc
                if 0 < sc <= COB_SHOOTING_COUNTER:
                    r.fired_tick = now - (COB_SHOOTING_COUNTER - sc)

    def _read_fire_pair(self, sc_addr: int, mc_addr: int) -> Optional[Tuple[int, int]]:
        """``(shooting_counter, main_counter)`` read while the counter held
        still (``None`` when it kept moving for every try -- then the
        last pair is returned anyway since one tick of drift is the worst
        case)."""
        last: Optional[Tuple[int, int]] = None
        for _ in range(self.FIRE_READBACK_TRIES):
            before = self.session.read_i32(mc_addr)
            sc = self.session.read_i32(sc_addr)
            after = self.session.read_i32(mc_addr)
            last = (sc, after)
            if before == after:
                return last
        return last

    @contextlib.contextmanager
    def batch(self, name: str = "batch") -> Iterator[BatchResult]:
        """Merge every action issued inside into ONE remote call."""
        if self._batch is not None:
            raise ActionError("batch() 不能嵌套")
        self.check(name)
        result = BatchResult()
        self._batch = Fragment(self.conv.asm_factory(), [], (), name, ())
        self._batch_names = []
        self._batch_calls = 0
        ok = False
        try:
            yield result
            ok = True
        finally:
            frag, names, calls = self._batch, self._batch_names, self._batch_calls
            self._batch = None
            self._batch_names = []
            self._batch_calls = 0
        if ok and frag is not None and calls:
            self.check(name)
            result.names = list(names)
            result.fragment = frag
            result.calls = calls
            result.result = self._run(frag, name)

    # -- plants --------------------------------------------------------------------
    def put_plant(self, row: int, col: int, plant: Union[int, str, PlantType], imitater: bool = False,
                  wake: bool = True, grow: bool = True) -> Optional[int]:
        """Plant ``plant`` at 1-based ``(row, col)``; returns the new plant's
        array index (``None`` inside a batch).  ``wake`` wakes a mushroom that
        would sleep on a day scene; ``grow`` arms potato mines / sun-shrooms
        immediately.  Both default to ``True`` -- a deliberate deviation from
        Toolkit's single *Put Plant* button (§2.1), which only applies these
        extras in the 布置阵型 flow (§3.14); pass ``wake=False, grow=False``
        for the bare Toolkit behaviour.  IZ modes add the IZ style call.
        ``grow`` writes ``[eax+0x54] = 1`` trusting that ``Board::AddPlant``
        returned the plant in EAX (Toolkit does the same, no null check)."""
        ptype = resolve_plant(plant)
        action = "放植物 %s@(%d,%d)" % (plant_label(ptype), row, col)
        if self._batch is None:
            self.check(action)
        board = self.board()
        r, c = self._cell(row, col, board, cob=(ptype == COB_CANNON))
        challenge = self.challenge(board) if self.game_mode() in IZ_MODES else None
        before = self.session.read_i32(board + self._board_field("plant_next_index")) \
            if self._batch is None else None
        frag = self.conv.put_plant(board, r, c, ptype, bool(imitater), challenge,
                                   asm=self._asm(), terminate=False)
        if wake and ptype in MAY_SLEEP and self.scene(board) in DAY_SCENES:
            self._emit_wake_eax(frag)
        if grow and ptype in GROW_PLANTS:
            off = GROW_COUNTDOWN_OFFSET.get(str(self.family))
            if off is not None:
                frag.mov_regdisp_imm32("eax", off, 1)
        self._submit(frag, action)
        if self._batch is not None:
            return None
        if self.safety.dry_run:
            return before
        return self._find_new_plant(before, r, c, ptype)

    def _emit_wake_eax(self, frag: Fragment) -> None:
        """``push eax; mov <reg>, eax; push 0; call set_plant_sleeping; pop eax``
        -- wake the plant whose pointer put_plant left in EAX."""
        reg = self.conv._plant_sleep_reg()
        frag.push_reg("eax")
        mov_reg_reg(frag, reg, "eax")
        frag.push_imm32(0)
        frag.call_abs(self.conv._addr("set_plant_sleeping"))
        frag.pop_reg("eax")

    def _find_new_plant(self, expected: Optional[int], r: int, c: int, ptype: int) -> int:
        plants = self.observer.plants(include_dead=True)
        if expected is not None and 0 <= expected < len(plants):
            p = plants[expected]
            if not p.dead and p.row == r and p.col == c and p.type == ptype:
                return expected
        # fall back: the highest-index live plant of that type on the cell
        for p in reversed(plants):
            if not p.dead and p.row == r and p.col == c and p.type == ptype:
                return p.index
        raise ActionError("放植物后在 (%d,%d) 找不到新的 %s（远程调用可能未生效）"
                          % (r + 1, c + 1, plant_label(ptype)))

    def _plants_at(self, r: int, c: int, layer: str = "all") -> List[Any]:
        out = []
        for p in self.observer.plants(include_dead=True):
            if p.dead or p.squished:
                continue
            here = (p.row == r and p.col == c) or (p.type == COB_CANNON and p.row == r and p.col == c - 1)
            if not here:
                continue
            if layer == "container" and p.type not in CONTAINERS:
                continue
            if layer == "shell" and p.type != SHELL:
                continue
            if layer == "addon" and p.type != ADDON:
                continue
            if layer == "plant" and p.type in CONTAINERS + (SHELL, ADDON):
                continue
            out.append(p)
        return out

    def remove_plant(self, row: int, col: int, layer: str = "all") -> Optional[int]:
        """Delete the plants on 1-based ``(row, col)``: ``layer`` =
        ``all`` | ``plant`` | ``shell`` (pumpkin) | ``container`` (lily/pot)
        | ``addon`` (coffee).  Returns how many were deleted."""
        if layer not in ("all", "plant", "shell", "container", "addon"):
            raise ActionError("layer 必须是 all/plant/shell/container/addon，得到 %r" % (layer,))
        action = "铲植物 (%d,%d) %s" % (row, col, layer)
        if self._batch is None:
            self.check(action)
        board = self.board()
        r, c = self._cell(row, col, board)
        targets = self._plants_at(r, c, layer)
        if not targets:
            return 0
        frag = self._delete_many([self.plant_addr(p.index, board) for p in targets], "delete_plant")
        self._submit(frag, action)
        return None if self._batch is not None else len(targets)

    def remove_plant_index(self, index: int) -> Optional[int]:
        """Delete the single plant at array ``index`` (must be alive)."""
        action = "铲植物 #%d" % index
        if self._batch is None:
            self.check(action)
        board = self.board()
        plants = self.observer.plants(include_dead=True)
        if not (0 <= index < len(plants)) or plants[index].dead:
            raise ActionError("植物 #%d 不存在或已死亡" % index)
        frag = self._delete_many([self.plant_addr(index, board)], "delete_plant")
        self._submit(frag, action)
        return None if self._batch is not None else 1

    def clear_plants(self) -> Optional[int]:
        """Delete every live, non-squished plant (Toolkit ``ClearAllPlants``)."""
        action = "清除所有植物"
        if self._batch is None:
            self.check(action)
        board = self.board()
        targets = [p for p in self.observer.plants(include_dead=True) if not p.dead and not p.squished]
        if not targets:
            return 0
        frag = self._delete_many([self.plant_addr(p.index, board) for p in targets], "delete_plant")
        self._submit(frag, action)
        return None if self._batch is not None else len(targets)

    def _delete_many(self, addrs: Sequence[int], method: str) -> Fragment:
        asm = self._asm() if self._batch is not None else self.conv.asm_factory()
        frag = Fragment(asm, [], (), method, ())
        for a in addrs:
            inner = getattr(self.conv, method)(a, asm=asm, terminate=False)
            frag.ops.extend(inner.ops)
        return frag

    def set_sleeping(self, index: int, sleeping: bool) -> None:
        action = "设置植物 #%d %s" % (index, "睡眠" if sleeping else "唤醒")
        if self._batch is None:
            self.check(action)
        board = self.board()
        frag = self.conv.set_plant_sleeping(self.plant_addr(index, board), bool(sleeping),
                                            asm=self._asm(), terminate=False)
        self._submit(frag, action)

    # -- zombies ------------------------------------------------------------------------
    def put_zombie(self, row: int, col: int, zombie: Union[int, str, ZombieType]) -> None:
        ztype = resolve_zombie(zombie)
        action = "出僵尸 %d@(%d,%d)" % (ztype, row, col)
        if self._batch is None:
            self.check(action)
        board = self.board()
        if ztype == ZOMBOSS:
            frag = self.conv.put_zombie_in_row(board, ZOMBOSS, 0, asm=self._asm(), terminate=False)
        else:
            r, c = self._cell(row, col, board)
            frag = self.conv.put_zombie(self.challenge(board), r, c, ztype, asm=self._asm(), terminate=False)
        self._submit(frag, action)

    # -- grid items ---------------------------------------------------------------------
    def put_ladder(self, row: int, col: int) -> None:
        action = "搭梯子 (%d,%d)" % (row, col)
        if self._batch is None:
            self.check(action)
        board = self.board()
        r, c = self._cell(row, col, board)
        frag = self.conv.put_ladder(board, r, c, asm=self._asm(), terminate=False)
        self._submit(frag, action)

    def put_grave(self, row: int, col: int) -> None:
        action = "冒墓碑 (%d,%d)" % (row, col)
        if self._batch is None:
            self.check(action)
        board = self.board()
        r, c = self._cell(row, col, board)
        frag = self.conv.put_grave(self.challenge(board), r, c, asm=self._asm(), terminate=False)
        self._submit(frag, action)

    def grid_items(self) -> List[Tuple[int, int, int, int, int]]:
        """``[(index, addr, type, row, col), ...]`` of live grid items."""
        board = self.board()
        lay = self.profile.objects.get("grid_item")
        if lay is None or not lay.stride:
            raise NotSupported("grid_item", str(self.family), "object layout missing")
        base = self.session.read_ptr(board + self._board_field("grid_items"))
        count = self.session.read_i32(board + self._board_field("grid_item_count_max"))
        if base == 0 or count <= 0:
            return []
        if count > 4096:
            raise ActionError("grid_item_count_max=%d 不合理" % count)
        f = lay.fields
        data = self.session.read(base, count * lay.stride)
        out = []
        for i in range(count):
            b = i * lay.stride
            if data[b + f.dead]:
                continue
            t = int.from_bytes(data[b + f.type:b + f.type + 4], "little", signed=True)
            r = int.from_bytes(data[b + f.row:b + f.row + 4], "little", signed=True)
            c = int.from_bytes(data[b + f.col:b + f.col + 4], "little", signed=True)
            out.append((i, base + b, t, r, c))
        return out

    def clear_grid_items(self, types: Iterable[Union[int, str]] = (1, 2, 3, 11)) -> Optional[int]:
        """Delete grid items of the given types (ids or names: grave/crater/
        ladder/rake ...).  Toolkit ``ClearGridItems``."""
        wanted = set()
        for t in types:
            if isinstance(t, str) and not t.isdigit():
                if t not in GRID_ITEM_TYPES:
                    raise ActionError("未知场地物件 %r；可用：%s" % (t, ", ".join(GRID_ITEM_TYPES)))
                wanted.add(GRID_ITEM_TYPES[t])
            else:
                wanted.add(int(t))
        action = "清除场地物件 %s" % sorted(wanted)
        if self._batch is None:
            self.check(action)
        targets = [addr for (_, addr, t, _, _) in self.grid_items() if t in wanted]
        if not targets:
            return 0
        frag = self._delete_many(targets, "delete_grid_item")
        self._submit(frag, action)
        return None if self._batch is not None else len(targets)

    # -- cob cannons (source-map §6) -------------------------------------------------
    def cobs(self) -> List[CobInfo]:
        """Every live cob cannon: ``(index, row, col, state, countdown)`` with
        1-based row / col (col = the cannon's LEFT cell) and a remaining-time
        estimate in centiseconds (§6.3: ``state_countdown + 475`` while arming,
        ~175 while loading, 0 when ready, ``None`` otherwise)."""
        board = self.board()
        f = self.profile.objects["plant"].fields
        cd_off = f.get("state_countdown")
        out: List[CobInfo] = []
        for p in self.observer.plants(include_dead=True):
            if p.dead or p.squished or int(p.type) != COB_CANNON:
                continue
            cd: Optional[int]
            if p.state == COB_READY:
                cd = 0
            elif p.state == COB_ARMING and cd_off is not None:
                cd = self.session.read_i32(self.plant_addr(p.index, board) + cd_off) + COB_CHARGE_CS
            elif p.state == COB_LOADING:
                cd = COB_LOADING_CS
            else:
                cd = None
            out.append(CobInfo(p.index, p.row + 1, p.col + 1, int(p.state), cd))
        return out

    def _find_cob(self, cob: CobRef) -> Any:
        plants = self.observer.plants(include_dead=True)
        if isinstance(cob, bool):
            raise ActionError("cob 必须是植物下标或 (row, col)：%r" % (cob,))
        if isinstance(cob, int):
            if not (0 <= cob < len(plants)) or plants[cob].dead:
                raise ActionError("植物 #%d 不存在或已死亡" % cob)
            p = plants[cob]
            if int(p.type) != COB_CANNON:
                raise ActionError("植物 #%d 是 %s，不是玉米加农炮" % (cob, plant_label(int(p.type))))
            return p
        try:
            row, col = cob
        except (TypeError, ValueError):
            raise ActionError("cob 必须是植物下标或 (row, col)：%r" % (cob,))
        r, c = self._cell(int(row), int(col), self.board())
        for p in plants:
            if not p.dead and not p.squished and int(p.type) == COB_CANNON and p.row == r and p.col == c:
                return p
        for p in plants:  # user gave the right-hand cell
            if not p.dead and not p.squished and int(p.type) == COB_CANNON and p.row == r and p.col == c - 1:
                raise ActionError("(%d,%d) 是玉米炮 #%d 的右半格，请用左格 (%d,%d)" % (row, col, p.index, row, col - 1))
        raise ActionError("(%d,%d) 没有玉米加农炮" % (row, col))

    def _cob_frag(self, cob: CobRef, target_row: int, target_col: float, check_ready: bool,
                  board: int, asm: Any) -> Tuple[Fragment, Any, int, int]:
        p = self._find_cob(cob)
        if int(p.state) != COB_READY:
            msg = "玉米炮 #%d (%d,%d) 未就绪：state=%d (%s)，需要 37 (ready)" % (
                p.index, p.row + 1, p.col + 1, p.state, COB_STATE_NAMES.get(int(p.state), "?"))
            if check_ready:
                raise ActionError(msg)
            warnings.warn(msg + "；check_ready=False，仍然发射", RuntimeWarning, stacklevel=4)
        scene = self.scene(board)
        try:
            x, y = _grid.cob_target_pixels(scene, int(target_row), float(target_col))
        except ValueError as exc:
            raise ActionError("落点无效：%s" % exc)
        frag = self.conv.cob_fire(self.plant_addr(p.index, board), x, y, asm=asm, terminate=False)
        return frag, p, x, y

    def fire_cob(self, cob: CobRef, target_row: int, target_col: float, *,
                 check_ready: bool = True, at_tick: Optional[int] = None) -> Optional[FireResult]:
        """发炮: fire the cannon ``cob`` (array index or 1-based ``(row, col)``
        of its LEFT cell) at 1-based ``target_row`` / ``target_col`` (may be
        fractional, e.g. ``8.75``).  The cannon must be READY (state 37)
        unless ``check_ready=False`` (then a warning).  Emits one remote call
        (``Plant::CobCannonFire``); returns a :class:`FireResult` with the
        state re-read afterwards (38 = firing), ``None`` inside a batch.
        ``at_tick``: frame-lock the call to that ``main_counter`` (DESIGN
        §6.3; ignored inside a batch)."""
        action = "发炮 %s -> (%s,%s)" % (cob, target_row, target_col)
        if self._batch is None:
            self.check(action)
        board = self.board()
        frag, p, x, y = self._cob_frag(cob, target_row, target_col, check_ready, board, self._asm())
        if self._batch is not None:
            self._submit(frag, action)
            return None
        self._run(frag, action, at_tick)
        res = FireResult(p.index, x, y, int(p.state), None, (int(target_row), float(target_col)))
        if not self.safety.dry_run:
            self._fill_fire_timing([res], board)
        return res

    def fire_cobs(self, fires: Sequence[Tuple[CobRef, Tuple[int, float]]], *,
                  check_ready: bool = True, at_tick: Optional[int] = None) -> List[FireResult]:
        """瞬发多炮: ``[(cob, (row, col)), ...]`` -- every fire in ONE remote
        call.  All cannons are validated before anything is emitted.
        ``at_tick``: frame-lock the call (see :meth:`fire_cob`)."""
        fires = list(fires)
        if not fires:
            return []
        action = "发炮 x%d" % len(fires)
        if self._batch is not None:
            raise ActionError("fire_cobs() 不能在 batch() 内使用；直接在 batch 内多次调用 fire_cob()")
        self.check(action)
        board = self.board()
        asm = self.conv.asm_factory()
        merged = Fragment(asm, [], (), "cob_fire", ())
        results: List[FireResult] = []
        seen = set()
        for cob, target in fires:
            try:
                trow, tcol = target
            except (TypeError, ValueError):
                raise ActionError("落点必须是 (row, col)：%r" % (target,))
            frag, p, x, y = self._cob_frag(cob, trow, tcol, check_ready, board, asm)
            if p.index in seen:
                raise ActionError("玉米炮 #%d 在同一批里出现了两次" % p.index)
            seen.add(p.index)
            merged.ops.extend(frag.ops)
            merged.notes = tuple(dict.fromkeys(merged.notes + frag.notes))
            results.append(FireResult(p.index, x, y, int(p.state), None, (int(trow), float(tcol))))
        self._run(merged, action, at_tick)
        if not self.safety.dry_run:
            self._fill_fire_timing(results, board)
        return results

    def __repr__(self) -> str:
        return "Actions(family=%s, batch=%s, history=%d)" % (
            self.family, self._batch is not None, len(self.history))
