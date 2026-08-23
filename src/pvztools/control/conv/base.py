"""Per-family remote-call conventions (parity §0.4, DESIGN §3 / §6).

Every official PvZ build calls the same game functions, but the compiler
gave each a different register / stack contract, and the GOTY, zh2010+ja,
zh2012, retail and BETA families disagree.  A :class:`Convention` knows, for
one family, *which register receives the board / challenge pointer* and
*what is pushed in which order* for every function ``control.remote`` may
call.  Each method returns a :class:`Fragment`: an :class:`Asm` builder that
has been fed the call sequence, plus the list of builder operations that
were emitted (``Fragment.ops``) so the sequence can be inspected and unit
tested independently of the final byte encoding.

Facts about registers / push order come from ``docs/research/
pvztoolkit-features.md`` (Toolkit behaviour, GPL -- facts only, the Python is
ours).  Where that document is silent for a family the method raises
:class:`NotSupported` instead of guessing (rule from the task brief).

The caller is responsible for

* resolving absolute addresses (board / challenge / plant / cutscene values
  read from the game, function VAs via ``profile.function_va``),
* wrapping the injected code with the ``main_loop_block`` patch and, when
  ``Fragment.required_patches`` is non-empty, with those patch groups too,
* **not** appending ``ret``: ``terminate`` defaults to ``False`` because the
  remote executor's wrapper owns the epilogue (a user ``ret`` would pop
  EFLAGS / a cdecl leftover as the return address).  ``terminate=True`` is
  for standalone listings (``describe()``); the executor strips a trailing
  ``ret`` anyway.

Register names are lower-case strings (``"eax"``); that is the contract with
``control.asm.Asm``.
"""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

try:  # control/asm.py is written concurrently; tests supply a stub factory.
    from ..asm import Asm as _DefaultAsm  # type: ignore
except Exception:  # pragma: no cover - asm.py absent / broken
    _DefaultAsm = None

__all__ = ["Convention", "Fragment", "NotSupported", "Op", "IMITATER_SEED",
           "ZOMBOSS", "MAY_SLEEP", "COB_CANNON", "TRANSITION_FRAGMENTS",
           "mov_reg_reg", "dec_regdisp", "dec_regdisp32"]

#: Seed type of the imitater card (``slot_seed_type == 48``).
IMITATER_SEED = 48
#: Zombie type of Dr. Zomboss (spawned via ``put_zombie_in_row``).
ZOMBOSS = 25
#: Cob cannon plant type (occupies two columns).
COB_CANNON = 47
#: Plants that fall asleep in daytime scenes (Toolkit §3.14 ``may_sleep``).
MAY_SLEEP = frozenset({8, 9, 10, 12, 13, 14, 15, 24, 31, 42})

#: One recorded builder operation: ``(name, args)``.
Op = Tuple[str, Tuple[Any, ...]]

#: Fragments whose callee triggers a screen transition / level load
#: (``Fragment.needs_main_loop = False``).  The executor runs them like
#: every other call: the main loop stays blocked for the **whole** call
#: (``control.remote`` module doc item 7; Toolkit's ``asm_code_inject``
#: blocks for every call, ``sync_profile`` / ``fade_out_level`` included).
#: The only difference is a longer thread timeout (``transition=True`` ->
#: 5000 ms) because the handler does more work (screen switch, level
#: load).  There is **no** early release: releasing the loop while our
#: thread is still inside UI code races the game thread (2026-08-22
#: incident).  Never hold ``main_loop_block`` while *waiting for a UI
#: change* after the call returned -- that waiting happens unblocked.
TRANSITION_FRAGMENTS = frozenset({
    "seed_chooser_start",        # SeedChooserScreen::OnStartButton
    "award_start",               # AwardScreen::StartButtonPressed
    "game_selector_adventure",   # GameSelector::ClickedAdventure
    "show_seed_chooser",         # LawnApp::ShowSeedChooserScreen
    "fade_out_level",            # Board::FadeOutLevel (direct win)
    "puzzle_next_stage_clear",   # field reset (endless)
    "sync_profile",              # GameSelector::SyncProfile (loading screen)
})


class NotSupported(Exception):
    """The research notes do not describe ``name`` for ``family``."""

    def __init__(self, name: str, family: str, reason: str = ""):
        self.name = name
        self.family = family
        self.reason = reason
        msg = "%s: remote call convention unknown for family %r" % (name, family)
        if reason:
            msg += " (%s)" % reason
        super().__init__(msg)


@dataclass
class Fragment:
    """A generated call sequence.

    ``asm``              the builder (``control.asm.Asm`` or a stub) holding the code
    ``ops``              builder operations in emission order, e.g.
                         ``("push_imm32", (3,))``, ``("call_abs", (0x4105a0,))``
    ``required_patches`` profile patch groups that must be *enabled* around
                         the injection (besides ``main_loop_block`` which is
                         always required)
    ``name``             the convention method that produced it
    ``notes``            free-form caveats (e.g. "unverified live")
    ``needs_main_loop``  False for UI-transition calls
                         (:data:`TRANSITION_FRAGMENTS`): only selects the
                         longer transition timeout; the main loop is still
                         blocked for the whole call (no early release)
    """
    asm: Any
    ops: List[Op] = field(default_factory=list)
    required_patches: Tuple[str, ...] = ()
    name: str = ""
    notes: Tuple[str, ...] = ()
    needs_main_loop: bool = True

    # -- recording proxies ---------------------------------------------------
    def _emit(self, op: str, *args: Any) -> "Fragment":
        self.ops.append((op, tuple(args)))
        fn = getattr(self.asm, op)
        fn(*args)
        return self

    def mov_reg_imm32(self, reg: str, imm: int) -> "Fragment":
        return self._emit("mov_reg_imm32", reg, imm)

    def mov_reg_mem(self, reg: str, addr: int) -> "Fragment":
        return self._emit("mov_reg_mem", reg, addr)

    def mov_reg_regdisp(self, dst: str, src: str, disp: int) -> "Fragment":
        return self._emit("mov_reg_regdisp", dst, src, disp)

    def mov_regdisp_imm32(self, base: str, disp: int, imm: int) -> "Fragment":
        return self._emit("mov_regdisp_imm32", base, disp, imm)

    def push_imm32(self, imm: int) -> "Fragment":
        return self._emit("push_imm32", imm)

    def push_reg(self, reg: str) -> "Fragment":
        return self._emit("push_reg", reg)

    def pop_reg(self, reg: str) -> "Fragment":
        return self._emit("pop_reg", reg)

    def call_abs(self, addr: int) -> "Fragment":
        return self._emit("call_abs", addr)

    def ret(self) -> "Fragment":
        return self._emit("ret")

    def add_esp(self, n: int) -> "Fragment":
        return self._emit("add_esp", n)

    def raw(self, data: bytes) -> "Fragment":
        return self._emit("raw", data)

    # -- introspection -------------------------------------------------------
    def calls(self) -> List[int]:
        """Absolute targets of every ``call_abs`` in order."""
        return [a[0] for op, a in self.ops if op == "call_abs"]

    def pushes(self) -> List[Any]:
        """Pushed immediates / register names in push order."""
        return [a[0] for op, a in self.ops if op in ("push_imm32", "push_reg")]

    def reg_loads(self) -> List[Tuple[str, Any]]:
        """``(reg, value)`` for every ``mov_reg_imm32`` / ``mov_reg_mem``."""
        return [(a[0], a[1]) for op, a in self.ops if op in ("mov_reg_imm32", "mov_reg_mem")]

    def extend(self, other: "Fragment") -> "Fragment":
        """Replay ``other``'s ops into this fragment (batching helper)."""
        for op, args in other.ops:
            if op == "mov_reg_reg":
                mov_reg_reg(self, *args)
            elif op == "dec_regdisp":
                dec_regdisp(self, *args)
            else:
                self._emit(op, *args)
        self.required_patches = tuple(dict.fromkeys(self.required_patches + other.required_patches))
        self.notes = tuple(dict.fromkeys(self.notes + other.notes))
        # a batch holding any transition call is itself a transition
        self.needs_main_loop = bool(self.needs_main_loop and other.needs_main_loop)
        return self


# x86 encodings used where the Asm builder has no dedicated helper.
_REG_INDEX = {"eax": 0, "ecx": 1, "edx": 2, "ebx": 3, "esp": 4, "ebp": 5, "esi": 6, "edi": 7}


def dec_regdisp32(base: str, disp: int) -> bytes:
    """``dec dword [base+disp32]`` = ``FF /1`` with mod=10 (esp needs SIB)."""
    idx = _REG_INDEX[base]
    if idx == 4:
        raise ValueError("esp base needs SIB")
    return b"\xff" + bytes([0x80 | (1 << 3) | idx]) + int(disp).to_bytes(4, "little", signed=True)


def mov_reg_reg(frag: Fragment, dst: str, src: str) -> None:
    """``mov dst, src`` (``8B /r``); recorded as ``("mov_reg_reg", (dst, src))``."""
    frag.raw(bytes([0x8B, 0xC0 | (_REG_INDEX[dst] << 3) | _REG_INDEX[src]]))
    frag.ops[-1] = ("mov_reg_reg", (dst, src))


def dec_regdisp(frag: Fragment, base: str, disp: int) -> None:
    frag.raw(dec_regdisp32(base, disp))
    frag.ops[-1] = ("dec_regdisp", (base, disp))


class Convention(ABC):
    """Base class; subclasses set ``family`` and override per-family methods.

    ``fn``       name -> absolute VA of every known function (missing names
                 raise :class:`NotSupported` with reason ``"address missing"``)
    ``profile``  the address profile (struct offsets: ``board.scene``,
                 ``objects.plant.stride`` ...)
    ``asm_factory``  callable returning a fresh builder; defaults to
                 ``control.asm.Asm``
    """

    family: str = ""

    def __init__(self, fn: Mapping[str, Optional[int]], profile: Any,
                 asm_factory: Optional[Callable[[], Any]] = None):
        self.fn = {k: v for k, v in dict(fn).items() if v is not None}
        self.profile = profile
        if asm_factory is None:
            if _DefaultAsm is None:
                raise RuntimeError("pvztools.control.asm.Asm unavailable; pass asm_factory")
            asm_factory = _DefaultAsm
        self.asm_factory = asm_factory

    # -- helpers -----------------------------------------------------------
    def _addr(self, name: str) -> int:
        va = self.fn.get(name)
        if va is None:
            raise NotSupported(name, self.family, "address missing in profile")
        return va

    def _unsupported(self, name: str, reason: str = "") -> NotSupported:
        return NotSupported(name, self.family, reason or "not described in research notes")

    def _new(self, name: str, asm: Any = None, patches: Sequence[str] = (),
             notes: Sequence[str] = ()) -> Fragment:
        return Fragment(asm if asm is not None else self.asm_factory(), [],
                        tuple(patches), name, tuple(notes),
                        needs_main_loop=name not in TRANSITION_FRAGMENTS)

    @staticmethod
    def _finish(frag: Fragment, terminate: bool) -> Fragment:
        if terminate:
            frag.ret()
        return frag

    def _board_off(self, field_name: str) -> int:
        return getattr(self.profile.board, field_name)

    def _plant_stride(self) -> int:
        return self.profile.objects["plant"].stride

    # -- plants --------------------------------------------------------------
    def put_plant(self, board: int, row: int, col: int, ptype: int, imitater: bool = False,
                  challenge: Optional[int] = None, asm: Any = None,
                  terminate: bool = False) -> Fragment:
        """``Board::AddPlant`` (Toolkit §2.1).  GOTY / retail / zh families:
        ``push (imitater ? type : -1)``, ``push (imitater ? 48 : type)``,
        ``EAX=row``, ``push col``, ``EBP=board``, ``push EBP``, ``call``.
        The new plant pointer comes back in EAX.  ``imitater`` appends
        ``put_plant_imitater``; ``challenge`` (I, Zombie modes) appends
        ``put_plant_iz_style``."""
        # pre-check support / addresses BEFORE emitting anything, so a shared
        # (batch) builder is never left holding a dangling half sequence
        self._addr("put_plant")
        if imitater:
            self._precheck_put_plant_imitater()
        if challenge is not None:
            self._precheck_put_plant_iz_style()
        frag = self._new("put_plant", asm)
        self._emit_put_plant_core(frag, board, row, col, ptype, imitater)
        if imitater:
            self._emit_put_plant_imitater(frag)
        if challenge is not None:
            self._emit_put_plant_iz_style(frag, challenge)
        return self._finish(frag, terminate)

    def _emit_put_plant_core(self, frag: Fragment, board: int, row: int, col: int,
                             ptype: int, imitater: bool) -> None:
        frag.push_imm32(ptype if imitater else -1)
        frag.push_imm32(IMITATER_SEED if imitater else ptype)
        frag.mov_reg_imm32("eax", row)
        frag.push_imm32(col)
        frag.mov_reg_imm32("ebp", board)
        frag.push_reg("ebp")
        frag.call_abs(self._addr("put_plant"))

    def _precheck_put_plant_imitater(self) -> None:
        self._addr("put_plant_imitater")

    def _precheck_put_plant_iz_style(self) -> None:
        self._addr("put_plant_iz_style")

    #: Documented deviation (research notes §2.1): Toolkit recomputes the new
    #: plant address as ``[board+plants] + [board+plant_next_index] * stride``
    #: before the call; we take the pointer ``Board::AddPlant`` returns in EAX
    #: (the §3.14 wake / grow sequences rely on that same EAX), which avoids
    #: reading ``plant_next_index`` ahead of a call that advances it.  See
    #: :meth:`new_plant_addr_expr` for the documented inputs.
    IMITATER_NOTE = "put_plant_imitater: plant taken from EAX (AddPlant return), not next_index*stride"

    def _emit_put_plant_imitater(self, frag: Fragment) -> None:
        """Plays the imitater morph: ``ESI = plant (EAX returned by put_plant)``,
        ``call put_plant_imitater``; EAX (plant) is preserved via push/pop."""
        frag.push_reg("eax")
        mov_reg_reg(frag, "esi", "eax")
        frag.call_abs(self._addr("put_plant_imitater"))
        frag.pop_reg("eax")
        frag.notes = tuple(dict.fromkeys(frag.notes + (self.IMITATER_NOTE,)))

    def _emit_put_plant_iz_style(self, frag: Fragment, challenge: int) -> None:
        """``EAX = challenge``, ``call put_plant_iz_style`` (Toolkit §2.1).
        How the plant pointer reaches the callee is not recorded in the
        notes; Toolkit issues it right after put_plant, so the plant is
        pushed and restored around it here to keep EAX usable afterwards."""
        frag.push_reg("eax")
        frag.mov_reg_imm32("eax", challenge)
        frag.call_abs(self._addr("put_plant_iz_style"))
        frag.pop_reg("eax")
        frag.notes = tuple(dict.fromkeys(frag.notes + ("put_plant_iz_style: plant register unverified",)))

    def put_plant_iz_style(self, challenge: int, asm: Any = None, terminate: bool = False) -> Fragment:
        frag = self._new("put_plant_iz_style", asm)
        self._emit_put_plant_iz_style(frag, challenge)
        return self._finish(frag, terminate)

    def new_plant_addr_expr(self, board: int) -> Tuple[int, int, int]:
        """``(next_index_addr, stride, plants_ptr_addr)`` -- the three inputs of
        Toolkit's ``[board]+plants + [board]+plant_next_index * stride``
        formula, for callers that read the index before injecting."""
        return (board + self._board_off("plant_next_index"), self._plant_stride(),
                board + self._board_off("plants"))

    def delete_plant(self, plant: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``push plant; call delete_plant`` (GOTY / retail / zh)."""
        frag = self._new("delete_plant", asm)
        frag.push_imm32(plant)
        frag.call_abs(self._addr("delete_plant"))
        return self._finish(frag, terminate)

    def set_plant_sleeping(self, plant: int, sleeping: bool, asm: Any = None,
                           terminate: bool = False) -> Fragment:
        """GOTY: ``EDI=plant``; ``push sleeping``; ``call``.  Retail overrides EAX."""
        frag = self._new("set_plant_sleeping", asm)
        frag.mov_reg_imm32(self._plant_sleep_reg(), plant)
        frag.push_imm32(1 if sleeping else 0)
        frag.call_abs(self._addr("set_plant_sleeping"))
        return self._finish(frag, terminate)

    def _plant_sleep_reg(self) -> str:
        return "edi"

    # -- zombies -------------------------------------------------------------
    def put_zombie(self, challenge: int, row: int, col: int, ztype: int, asm: Any = None,
                   terminate: bool = False) -> Fragment:
        """GOTY / retail: ``push col``, ``push type``, ``EAX=row``,
        ``ECX=challenge``, ``call put_zombie``."""
        frag = self._new("put_zombie", asm)
        frag.push_imm32(col)
        frag.push_imm32(ztype)
        frag.mov_reg_imm32("eax", row)
        frag.mov_reg_imm32("ecx", challenge)
        frag.call_abs(self._addr("put_zombie"))
        return self._finish(frag, terminate)

    def put_zombie_in_row(self, board: int, ztype: int = ZOMBOSS, row: int = 0, asm: Any = None,
                          terminate: bool = False) -> Fragment:
        """Dr. Zomboss: ``EAX=board``, ``push row(0)``, ``push type(25)``."""
        frag = self._new("put_zombie_in_row", asm)
        frag.mov_reg_imm32("eax", board)
        frag.push_imm32(row)
        frag.push_imm32(ztype)
        frag.call_abs(self._addr("put_zombie_in_row"))
        return self._finish(frag, terminate)

    # -- grid items ------------------------------------------------------------
    def put_ladder(self, board: int, row: int, col: int, asm: Any = None,
                   terminate: bool = False) -> Fragment:
        """``EDI=row``, ``push col``, ``EAX=board``, ``call put_ladder``."""
        frag = self._new("put_ladder", asm)
        frag.mov_reg_imm32("edi", row)
        frag.push_imm32(col)
        frag.mov_reg_imm32("eax", board)
        frag.call_abs(self._addr("put_ladder"))
        return self._finish(frag, terminate)

    def put_grave(self, challenge: int, row: int, col: int, asm: Any = None,
                  terminate: bool = False) -> Fragment:
        """GOTY: ``ECX=challenge``, ``push ECX``, ``EDI=row``, ``EBX=col``.
        Retail uses EDX for the challenge."""
        reg = self._grave_reg()
        frag = self._new("put_grave", asm)
        frag.mov_reg_imm32(reg, challenge)
        frag.push_reg(reg)
        frag.mov_reg_imm32("edi", row)
        frag.mov_reg_imm32("ebx", col)
        frag.call_abs(self._addr("put_grave"))
        return self._finish(frag, terminate)

    def _grave_reg(self) -> str:
        return "ecx"

    def delete_grid_item(self, item: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``ESI=item``, ``call delete_grid_item``."""
        frag = self._new("delete_grid_item", asm)
        frag.mov_reg_imm32("esi", item)
        frag.call_abs(self._addr("delete_grid_item"))
        return self._finish(frag, terminate)

    # -- spawning ---------------------------------------------------------------
    def pick_zombie_waves(self, board: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """Regenerate the spawn list: ``EDI=board`` (zh2010/ja EAX, beta ECX)."""
        frag = self._new("pick_zombie_waves", asm)
        frag.mov_reg_imm32(self._pick_waves_reg(), board)
        frag.call_abs(self._addr("pick_zombie_waves"))
        return self._finish(frag, terminate)

    def _pick_waves_reg(self) -> str:
        return "edi"

    def remove_cutscene_zombies(self, board: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``EBX=board``, ``call remove_cutscene_zombies``."""
        frag = self._new("remove_cutscene_zombies", asm)
        frag.mov_reg_imm32("ebx", board)
        frag.call_abs(self._addr("remove_cutscene_zombies"))
        return self._finish(frag, terminate)

    def place_street_zombies(self, cutscene: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``push cutscene`` (the ``[board]+cutscene`` pointer value), ``call``.
        Must run with the ``street_zombies_hack`` patch enabled."""
        frag = self._new("place_street_zombies", asm, patches=("street_zombies_hack",))
        frag.push_imm32(cutscene)
        frag.call_abs(self._addr("place_street_zombies"))
        return self._finish(frag, terminate)

    def refresh_spawn_preview(self, board: int, cutscene: int, asm: Any = None,
                              terminate: bool = False) -> Fragment:
        """Toolkit ``update_spawn_preview``: remove cutscene zombies then place
        street zombies (seed-selection screen, ``game_ui == 2``); requires
        ``street_zombies_hack`` for the whole injection."""
        frag = self.remove_cutscene_zombies(board, asm, terminate=False)
        frag.name = "refresh_spawn_preview"
        inner = self.place_street_zombies(cutscene, frag.asm, terminate=False)
        frag.ops.extend(inner.ops)
        frag.required_patches = inner.required_patches
        return self._finish(frag, terminate)

    # -- scene / music / level --------------------------------------------------
    def pick_background(self, board: int, scene: int, asm: Any = None,
                        terminate: bool = False) -> Fragment:
        """``ESI=board``, ``mov [esi+scene], scene``, ``call pick_background``."""
        frag = self._new("pick_background", asm)
        frag.mov_reg_imm32("esi", board)
        frag.mov_regdisp_imm32("esi", self._board_off("scene"), scene)
        self._pick_background_extra(frag)
        frag.call_abs(self._addr("pick_background"))
        return self._finish(frag, terminate)

    def _pick_background_extra(self, frag: Fragment) -> None:
        return None

    def play_music(self, music: int, music_id: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``EDI=id``, ``EAX=music`` (the ``[lawn]+music`` object pointer)."""
        frag = self._new("play_music", asm)
        frag.mov_reg_imm32("edi", music_id)
        frag.mov_reg_imm32("eax", music)
        frag.call_abs(self._addr("play_music"))
        return self._finish(frag, terminate)

    def fade_out_level(self, board: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """Direct win: ``ECX=board``, ``call fade_out_level``."""
        frag = self._new("fade_out_level", asm)
        frag.mov_reg_imm32("ecx", board)
        frag.call_abs(self._addr("fade_out_level"))
        return self._finish(frag, terminate)

    def puzzle_next_stage_clear(self, challenge: int, asm: Any = None,
                                terminate: bool = False) -> Fragment:
        """Reset the field keeping the round count: ``EDI=challenge``,
        ``dec [edi+endless_round]``, ``call puzzle_next_stage_clear``."""
        frag = self._new("puzzle_next_stage_clear", asm)
        frag.mov_reg_imm32("edi", challenge)
        dec_regdisp(frag, "edi", self.profile.objects["challenge"].fields.endless_round)
        self._next_stage_extra(frag)
        frag.call_abs(self._addr("puzzle_next_stage_clear"))
        return self._finish(frag, terminate)

    def _next_stage_extra(self, frag: Fragment) -> None:
        return None

    def wisdom_tree_grow(self, challenge: int, lawn: int, asm: Any = None,
                         terminate: bool = False) -> Fragment:
        """GOTY: ``ESI=challenge``, ``call wisdom_tree_grow``."""
        frag = self._new("wisdom_tree_grow", asm)
        frag.mov_reg_imm32("esi", challenge)
        frag.call_abs(self._addr("wisdom_tree_grow"))
        return self._finish(frag, terminate)

    def sync_profile(self, game_selector: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``push 1`` (show loading), ``ECX=game_selector``, ``call sync_profile``."""
        frag = self._new("sync_profile", asm)
        frag.push_imm32(1)
        frag.mov_reg_imm32("ecx", game_selector)
        self._sync_profile_extra(frag)
        frag.call_abs(self._addr("sync_profile"))
        return self._finish(frag, terminate)

    def _sync_profile_extra(self, frag: Fragment) -> None:
        return None

    # -- mowers -----------------------------------------------------------------
    def mower_start(self, mower: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``ESI=mower`` (zh2010/ja EBX, beta ECX), ``call mower_start``."""
        frag = self._new("mower_start", asm)
        frag.mov_reg_imm32(self._mower_start_reg(), mower)
        frag.call_abs(self._addr("mower_start"))
        return self._finish(frag, terminate)

    def _mower_start_reg(self) -> str:
        return "esi"

    def mower_delete(self, mower: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``EAX=mower`` (beta ECX), ``call mower_delete``."""
        frag = self._new("mower_delete", asm)
        frag.mov_reg_imm32("eax", mower)
        frag.call_abs(self._addr("mower_delete"))
        return self._finish(frag, terminate)

    def mower_restore(self, board: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``push board``, ``call mower_restore``; caller must wrap with the
        ``mower_init_allow`` + ``mower_init_no_anim`` patches."""
        frag = self._new("mower_restore", asm, patches=("mower_init_allow", "mower_init_no_anim"))
        frag.push_imm32(board)
        frag.call_abs(self._addr("mower_restore"))
        return self._finish(frag, terminate)

    # -- cob cannon (source-map §6.6) ---------------------------------------------
    def cob_fire(self, cob_plant: int, x: int, y: int, asm: Any = None,
                 terminate: bool = False) -> Fragment:
        """``Plant::CobCannonFire(int x, int y)``: ``plant`` in **EAX**,
        ``push y; push x; call`` -- callee cleans (``ret 8``), so no
        ``add esp``.  ``x`` / ``y`` are click pixels (see ``control.grid``).
        Retail and GOTY implement it; other families raise NotSupported."""
        raise self._unsupported("cob_fire", "unverified")

    def _emit_cob_fire(self, name: str, cob_plant: int, x: int, y: int, asm: Any,
                       terminate: bool) -> Fragment:
        # Resolve the address BEFORE emitting anything so a shared (batch)
        # builder is never left holding a dangling push/push/mov when the
        # profile lacks the function (e.g. goty_1_2_0_1073_en).
        target = self._addr("cob_fire")
        frag = self._new(name, asm)
        frag.push_imm32(y)
        frag.push_imm32(x)
        frag.mov_reg_imm32("eax", cob_plant)
        frag.call_abs(target)
        return self._finish(frag, terminate)

    # -- coins (autocollect; GOTY 1096 ``Coin::Collect`` 0x435DC0) -------------------
    def coin_collect(self, coin: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``Coin::Collect()``: ``coin`` (this) in a register, no stack args,
        callee-cleans.  GOTY verified (``this`` in **ECX**: decompiled
        prologue ``in_ECX[0xe]`` = ``mDead`` +0x38); other families raise
        :class:`NotSupported` until their register is verified."""
        raise self._unsupported("coin_collect", "unverified")

    def _emit_coin_collect(self, reg: str, coin: int, asm: Any, terminate: bool) -> Fragment:
        # resolve first so a shared (batch) builder never holds a dangling mov
        target = self._addr("coin_collect")
        frag = self._new("coin_collect", asm)
        frag.mov_reg_imm32(reg, int(coin))
        frag.call_abs(target)
        return self._finish(frag, terminate)

    def coin_collect_many(self, coins: Sequence[int], asm: Any = None,
                          terminate: bool = False) -> Fragment:
        """One fragment collecting every coin in ``coins`` (``mov reg, coin;
        call`` pairs in order)."""
        asm = asm if asm is not None else self.asm_factory()
        frag = Fragment(asm, [], (), "coin_collect_many", ())
        for c in coins:
            inner = self.coin_collect(int(c), asm=asm, terminate=False)
            frag.ops.extend(inner.ops)
        return self._finish(frag, terminate)

    # -- menu / seed chooser navigation (control.chooser_mem, control.menu) ---------
    # Only GOTY (Steam 1096) is analysed; every other family raises
    # NotSupported("unverified").  The GOTY conventions (decompile + PE
    # prologue bytes, see control/chooser_mem.py):
    #   ClickedSeedInChooser / ClickedSeedInBank(ChosenSeed*): this=EAX,
    #       push seed, ret 4
    #   OnStartButton(): this=ESI, ret
    #   AwardScreen::StartButtonPressed(this): push this, ret 4
    #   GameSelector::ClickedAdventure(this):  push this, ret 4
    def seed_chooser_click_seed(self, chooser: int, seed: int, in_bank: bool = False,
                                asm: Any = None, terminate: bool = False) -> Fragment:
        """``SeedChooserScreen::ClickedSeedInChooser`` (``in_bank=False``) or
        ``ClickedSeedInBank`` (``in_bank=True``) for the ``ChosenSeed`` at
        absolute address ``seed`` (NOT an index)."""
        raise self._unsupported("seed_chooser_click_seed", "unverified")

    def seed_chooser_start(self, chooser: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``SeedChooserScreen::OnStartButton`` (*Let's Rock!*)."""
        raise self._unsupported("seed_chooser_start", "unverified")

    def award_start(self, award: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``AwardScreen::StartButtonPressed`` (continue after a level)."""
        raise self._unsupported("award_start", "unverified")

    def game_selector_adventure(self, selector: int, asm: Any = None,
                                terminate: bool = False) -> Fragment:
        """``GameSelector::ClickedAdventure`` (main menu *Adventure*)."""
        raise self._unsupported("game_selector_adventure", "unverified")

    def _emit_stack_this(self, name: str, fn_name: str, this: int, asm: Any,
                         terminate: bool) -> Fragment:
        """``push this; call fn`` -- callee does ``ret 4``."""
        target = self._addr(fn_name)
        frag = self._new(name, asm)
        frag.push_imm32(this)
        frag.call_abs(target)
        return self._finish(frag, terminate)
