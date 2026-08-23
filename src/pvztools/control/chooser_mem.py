"""Seed chooser driven through game calls (no screen clicks).

:class:`MemoryChooser` reads the ``SeedChooserScreen`` object and asks the
game to pick / unpick seeds and press *Let's Rock!* by remote-calling the
handlers the mouse code would call.  GOTY Steam 1.2.0.1096 only (the
profile must carry the ``chooser_*`` functions); everything else raises
:class:`ChooserError`.

Analysis (Ghidra decompile of popcapgame1 1.2.0.1096 + PE prologue bytes;
re-plants ``Lawn/Widget/SeedChooserScreen.cpp`` for semantics)
=====================================================================

(a) **Reaching the chooser object** -- ``LawnApp::ShowSeedChooserScreen``
    (``0x454B00``, ``this`` in ESI) does ``new SeedChooserScreen`` and stores
    the pointer at ``app+0x874`` (:data:`APP_SEED_CHOOSER`); the profile's
    ``lawn_app.board`` ``0x868`` / ``game_selector`` ``0x870`` sit right
    before it, matching the re-plants member order ``mBoard, mGameSelector,
    mSeedChooserScreen, mAwardScreen ...``.  ``LawnApp::ShowAwardScreen``
    (``0x454760``) sets ``game_ui = 5`` and stores the new ``AwardScreen`` at
    ``app+0x878`` (:data:`APP_AWARD_SCREEN`).

(b) **ClickedSeedInChooser** is ``0x494690`` (284 B).  Prologue bytes
    ``55 8B 6C 24 08 56 8B F0`` = ``push ebp; mov ebp,[esp+8]; push esi; mov
    esi,eax``: ``this`` in **EAX**, one stack argument = ``ChosenSeed*`` (not
    an index), epilogue ``ret 4`` (callee cleans).  Body: returns when
    ``mSeedsInBank == mBoard->mSeedBank->mNumPackets``; otherwise sets
    motion start/end (``mSeedChooserAge + 25`` ticks), target = bank slot
    ``mSeedsInBank``, ``mSeedState = SEED_FLYING_TO_BANK`` (0),
    ``mSeedIndexInBank = mSeedsInBank``, ``mSeedsInFlight++``,
    ``mSeedsInBank++``, enables the start button when the bank is full.
    The mouse handler additionally requires ``mSeedState == SEED_IN_CHOOSER``
    (3) and ``LawnApp::SeedTypeAvailable`` -- the callee does **not** check
    either, so :meth:`MemoryChooser.pick` checks them first.
    ``0x494020`` (504 B) is ``PickRandomSeeds`` (``this`` on the stack; fills
    the bank with random seeds and lands every flying seed) -- not the click
    handler.  ``0x4944F0`` = ``ClickedSeedInBank(ChosenSeed*)``: same
    convention as ClickedSeedInChooser (``this`` EAX, push seed, ``ret 4``);
    it re-targets every bank seed *after* the clicked one (found through
    ``FindSeedInBank`` ``0x494490`` = ``this`` EBX, push index, which only
    matches ``SEED_IN_BANK`` seeds) one slot to the left, sets the clicked
    seed ``SEED_FLYING_TO_CHOOSER`` (2), ``mSeedsInBank--``,
    ``mSeedsInFlight++`` and disables the start button.

(c) **OnStartButton** ``0x493C70``: ``53 8B 9E 28 0D 00 00`` = ``push ebx;
    mov ebx,[esi+0xD28]`` -- ``this`` in **ESI**, no stack arguments, plain
    ``ret``.  It runs the repick warnings (modal ``LawnApp::LawnMessageBox``
    = ``0x455530``!) and finally ``CloseSeedChooser`` (``0x495370``) which
    copies the bank through ``FindSeedInBank`` -- so the bank must be full
    **and landed** (``mSeedsInFlight == 0``, every bank seed state 1) before
    the call, or the copy loop dereferences ``FindSeedInBank() == -1``.

(d) **ChosenSeed** (stride ``0x3C``, 49 entries at ``chooser+0xBC`` indexed
    by seed type): ``+0x00 mX, +0x04 mY, +0x08 mTimeStartMotion, +0x0C
    mTimeEndMotion, +0x10 mStartX, +0x14 mStartY, +0x18 mEndX, +0x1C mEndY,
    +0x20 mSeedType, +0x24 mSeedState, +0x28 mSeedIndexInBank, +0x2C
    mRefreshing, +0x30 mRefreshCounter, +0x34 mImitaterType, +0x38
    mCrazyDavePicked`` (confirmed by the ``param_1[8..10]`` writes in the
    decompile).  States: 0 FLYING_TO_BANK, 1 IN_BANK, 2 FLYING_TO_CHOOSER,
    3 IN_CHOOSER, 4 PACKET_HIDDEN.  Chooser tail (re-plants + 0x18):
    ``+0xD28 mApp, +0xD2C mBoard, +0xD30 mNumSeedsToChoose, +0xD34
    mSeedChooserAge, +0xD38 mSeedsInFlight, +0xD3C mSeedsInBank, +0xD40
    mToolTip, +0xD44 mToolTipSeed, +0xD50 mChooseState``.  NOTE: the
    ``+0xD38`` counter incremented by ClickedSeedInBank is
    ``mSeedsInFlight`` (it is incremented for every re-targeted seed and
    decremented when a seed lands in ``0x494020``); ``mSeedsInBank`` is
    ``+0xD3C`` (decremented by ClickedSeedInBank, incremented by
    ClickedSeedInChooser).

(e) **GameSelector::ClickedAdventure** ``0x450B90``: ``83 EC 1C 53 55 8B 6C
    24 28`` -- ``this`` is the first *stack* argument (``[esp+0x28]`` after
    ``sub esp,0x1C`` + two pushes), epilogue ``ret 4``.  The object is
    ``LawnApp + lawn_app.game_selector`` (``0x870``).  It shows a modal
    "replay level?" box only for trial builds (``app+0x9E4`` / ``+0x998`` /
    ``+0x9E0`` trial gates); otherwise it stops the music, sets
    ``mStartingGame`` (``selector+0xE4``), disables the buttons and starts
    the zombie-hand reanimation -- the level / chooser is opened later from
    ``GameSelector::Update`` when the animation finishes, so the ui change
    (1 -> 2 / 3) must be polled.

(f) **AwardScreen::StartButtonPressed** ``0x40A310``: ``55 8B 6C 24 08`` --
    ``this`` on the stack, ``ret 4``; the object is ``app+0x878`` (see (a)).
    It returns immediately while any dialog is open (``GetDialog(4)``) and
    otherwise navigates (credits / game selector / challenge screen / next
    adventure level); the ui leaves 5.

Modal dialogs are the one thing a remote call must never trigger: the game
would run its nested message loop on the injected thread with the main loop
blocked.  :meth:`MemoryChooser.start` therefore mirrors every
``OnStartButton`` warning (:meth:`missing_for_start`) and refuses -- or,
with ``auto_fix``, adds the missing lily / sun producer / flower pot --
before calling.

(g) **Crazy Dave intro talk.**  ``game_ui == 2`` is the whole level-intro
    cutscene (:mod:`pvztools.control.cutscene`); while Dave is talking
    (``LawnApp::mCrazyDaveMessageIndex != -1``, GOTY ``app+0x970``) the
    chooser is not focused and ``OnStartButton`` must not be called (the
    2026-08-22 incident: start called during the talk, ui stayed 2, the
    process died after the click fallback).  :meth:`start` /
    :meth:`select_and_start` raise :class:`ChooserDialogError`
    (``dialog_suspected = True``) instead of calling; the caller
    (``play``) skips the talk first via :class:`Cutscene.skip_dialog` and
    never falls back to blind clicks on such an error.
"""
from __future__ import annotations

import struct
import time
import warnings
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

from .actions import plant_label, resolve_plant
from .backends import BackendError
from .conv import Fragment, NotSupported, convention_for
from .window import UI_CHOOSER, UI_LEVEL

__all__ = [
    "MemoryChooser", "ChooserError", "ChooserDialogError", "ChosenSeed", "APP_SEED_CHOOSER", "APP_AWARD_SCREEN",
    "CHOSEN_SEEDS_OFF", "CHOSEN_STRIDE", "NUM_CHOSEN", "SEED_FLYING_TO_BANK", "SEED_IN_BANK",
    "SEED_FLYING_TO_CHOOSER", "SEED_IN_CHOOSER", "SEED_PACKET_HIDDEN", "CH_APP", "CH_BOARD",
    "CH_AGE", "CH_IN_FLIGHT", "CH_IN_BANK", "seeds_available_for_level",
    "pointer_committed", "require_object",
]

# -- LawnApp (GOTY 1096) --------------------------------------------------------
APP_SEED_CHOOSER = 0x874   #: LawnApp::mSeedChooserScreen (ShowSeedChooserScreen store)
APP_AWARD_SCREEN = 0x878   #: LawnApp::mAwardScreen (ShowAwardScreen store)

# -- SeedChooserScreen (GOTY 1096) ---------------------------------------------
CHOSEN_SEEDS_OFF = 0xBC    #: ChosenSeed mChosenSeeds[49]
CHOSEN_STRIDE = 0x3C
NUM_CHOSEN = 49
CH_APP = 0xD28
CH_BOARD = 0xD2C
CH_AGE = 0xD34             #: mSeedChooserAge
CH_IN_FLIGHT = 0xD38       #: mSeedsInFlight
CH_IN_BANK = 0xD3C         #: mSeedsInBank
CH_CHOOSE_STATE = 0xD50    #: mChooseState (0 normal, 1 view lawn)

# -- ChosenSeed ----------------------------------------------------------------
CS_TYPE = 0x20
CS_STATE = 0x24
CS_BANK_INDEX = 0x28
CS_IMITATER_TYPE = 0x34
CS_CRAZY_DAVE = 0x38

SEED_FLYING_TO_BANK = 0
SEED_IN_BANK = 1
SEED_FLYING_TO_CHOOSER = 2
SEED_IN_CHOOSER = 3
SEED_PACKET_HIDDEN = 4

# -- Board / SeedBank ----------------------------------------------------------
BANK_NUM_PACKETS = 0x24    #: SeedBank::mNumPackets (profile objects.seed_bank.count)
BOARD_LEVEL = 0x5568       #: Board::mLevel (GOTY)
MAX_ZOMBIES_IN_WAVE = 50

# -- seeds / modes -------------------------------------------------------------
SUNFLOWER, WALLNUT, SUNSHROOM, LILY_PAD, TANGLE_KELP = 1, 3, 9, 16, 19
SEASHROOM, CACTUS, BLOVER, STARFRUIT, FLOWER_POT, UMBRELLA = 24, 26, 27, 29, 33, 37
PUFFSHROOM = 8
TWIN_SUNFLOWER, CATTAIL, IMITATER = 41, 43, 48
FIRST_UPGRADE = 40
SUN_PRODUCERS = (SUNFLOWER, TWIN_SUNFLOWER, SUNSHROOM)
POOL_PLANTS = (LILY_PAD, SEASHROOM, TANGLE_KELP)
FLYER_PLANTS = (CATTAIL, CACTUS, BLOVER)
#: upgrade -> base (OnStartButton CheckSeedUpgrade pairs)
UPGRADE_BASE: Dict[int, int] = {40: 7, 44: 39, 41: 1, 46: 21, 47: 34, 45: 31, 42: 10, 43: 16}
ZOMBIE_BALLOON = 16
MODE_ADVENTURE = 0
MODE_SURVIVAL_RANGE = range(1, 16)
MODE_SEEING_STARS = 0x16
MODE_LAST_STAND = 0x1F
MODE_ART_WALLNUT = 0x24
MODE_ART_SUNFLOWER = 0x28
SCENES_POOL = (2, 3)
SCENES_ROOF = (4, 5)
#: ticks a seed flies (ClickedSeedInChooser / ClickedSeedInBank: +25)
FLIGHT_TICKS = 25

SeedSpec = Union[int, str, Any]


def resolve_seed(seed: SeedSpec) -> int:
    """Seed type id 0..48 from an id / name / ``PlantType`` (the Imitater id
    48 is accepted here so :meth:`MemoryChooser.pick` can reject it with a
    chooser-specific message)."""
    if isinstance(seed, int) and not isinstance(seed, bool):
        pid = int(seed)
        if not (0 <= pid < NUM_CHOSEN):
            raise ChooserError("卡类型 %d 超出 0..48" % pid)
        return pid
    return resolve_plant(seed)


class ChooserError(BackendError):
    """Precondition / verification failure of the memory chooser."""

    #: True when the failure means a dialog (Crazy Dave talk / modal box)
    #: is up: callers must stop, never fall back to blind clicks
    dialog_suspected = False


class ChooserDialogError(ChooserError):
    """A Crazy Dave talk or a modal dialog is active in the chooser scene."""

    dialog_suspected = True


def pointer_committed(session: Any, addr: int, size: int = 4) -> Optional[bool]:
    """Is ``[addr, addr+size)`` inside a committed, readable region of the
    game?  Uses ``VirtualQueryEx`` through ``session.handle`` when the
    session has a real handle; otherwise (fakes, no handle) falls back to
    a read probe.  ``None`` when neither could be decided."""
    addr = int(addr)
    if addr <= 0 or addr + size > 0x1_0000_0000:
        return False
    h = getattr(session, "handle", None)
    if isinstance(h, int) and h and not getattr(session, "closed", False):
        try:
            from pvztools._win.kernel32 import iter_regions

            for reg in iter_regions(h, addr, addr + 1):
                return bool(reg.readable and reg.base <= addr and addr + size <= reg.end)
        except Exception:
            pass
    try:
        return len(session.read(addr, size)) == size
    except Exception:
        return False


def require_object(session: Any, ptr: int, what: str, size: int = 4,
                   error: Any = ChooserError) -> int:
    """``ptr`` must be non-null and live in a committed region, else raise
    ``error`` -- the gate before handing an object pointer to a remote call
    (a stale / freed screen pointer is a crash on the game thread)."""
    if not ptr:
        raise error("%s 为空" % what)
    ok = pointer_committed(session, ptr, size)
    if ok is False:
        raise error("%s 指针 0x%X 不在已提交的内存区域（对象已释放？）" % (what, ptr))
    return int(ptr)


class ChosenSeed(NamedTuple):
    index: int        #: position in mChosenSeeds == seed type
    seed_type: int    #: mSeedType (normally == index)
    state: int        #: ChosenSeedState
    bank_index: int   #: mSeedIndexInBank
    crazy_dave: bool = False
    imitater_type: int = -1

    @property
    def in_bank(self) -> bool:
        return self.state in (SEED_IN_BANK, SEED_FLYING_TO_BANK)


def seeds_available_for_level(level: int, finished_adventure: int) -> int:
    """``LawnApp::GetSeedsAvailable`` as decompiled in ``PickRandomSeeds``
    (``0x494020``): number of non-upgrade seed types unlocked (upper bound,
    exclusive; upgrades 40..48 are purchase-gated instead)."""
    if finished_adventure > 0 or level >= 0x33:
        return NUM_CHOSEN
    lvl = level - 1
    sub = lvl % 10 + 1
    n = sub + (lvl // 10) * 8
    if sub < 10:
        if sub > 4:
            n -= 1
    else:
        n -= 2
    return min(n, FIRST_UPGRADE)


class MemoryChooser:
    """Seed chooser through remote calls (see module doc).

    ``session`` / ``profile`` / ``executor`` as for :class:`Actions`;
    ``observer`` supplies ``game().ui``, ``lawn_ptr()``, ``board_ptr()`` and
    optionally ``plants()`` / ``spawn_list()``.  ``window`` (a
    :class:`~pvztools.control.window.GameWindow`) is optional and only used
    for the dialog heuristic after a start that did not switch the ui.
    ``gap`` is the pause between remote calls that must see one frame
    (default ``frame_ms``-based).
    """

    def __init__(self, session: Any, profile: Any, executor: Any, observer: Any, *,
                 conv: Any = None, window: Any = None, cutscene: Any = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 gap: Optional[float] = None) -> None:
        self.session = session
        self.profile = profile
        self.executor = executor
        self.observer = observer
        self.window = window
        #: :class:`~pvztools.control.cutscene.Cutscene` (built lazily; a
        #: test double with ``dialog_active()`` is accepted)
        self._cutscene = cutscene
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self.conv = conv if conv is not None else convention_for(profile)
        self.gap = float(gap) if gap is not None else self._frame_s() * 2
        #: every fragment injected, in order
        self.history: List[Fragment] = []
        self._require_supported()

    # -- support gate ------------------------------------------------------------
    def _require_supported(self) -> None:
        fns = getattr(self.profile, "functions", {}) or {}
        fam = getattr(self.profile, "conventions", None) or getattr(self.profile, "family", None)
        missing = [n for n in ("chooser_on_start", "chooser_clicked_seed_in_chooser",
                               "chooser_clicked_seed_in_bank") if n not in fns]
        if fam != "goty" or missing:
            raise ChooserError("内存选卡只支持 GOTY Steam 1.2.0.1096（profile %s 缺函数 %s）"
                               % (getattr(self.profile, "id", "?"), missing or fam))

    @property
    def dry_run(self) -> bool:
        safety = getattr(self.executor, "safety", None)
        return bool(getattr(safety, "dry_run", False) or getattr(self.executor, "dry_run", False))

    def _frame_s(self) -> float:
        fn = getattr(self.executor, "frame_ms", None)
        try:
            ms = int(fn()) if fn else 10
        except Exception:
            ms = 10
        return max(1, ms) / 1000.0

    # -- reads ---------------------------------------------------------------------
    def _u32(self, addr: int) -> int:
        return struct.unpack("<I", self.session.read(addr, 4))[0]

    def _i32(self, addr: int) -> int:
        return struct.unpack("<i", self.session.read(addr, 4))[0]

    def ui(self) -> int:
        return int(self.observer.game().ui)

    def lawn(self) -> int:
        return int(self.observer.lawn_ptr() or 0)

    def chooser_ptr(self) -> int:
        """``LawnApp::mSeedChooserScreen`` (0 when no chooser exists)."""
        lawn = self.lawn()
        return self._u32(lawn + APP_SEED_CHOOSER) if lawn else 0

    def _chooser(self) -> int:
        ch = self.chooser_ptr()
        if not ch:
            raise ChooserError("没有选卡界面对象（LawnApp+0x874 为空）")
        return ch

    def _chooser_live(self) -> int:
        """The chooser pointer, additionally verified to sit in committed
        memory (the whole object incl. ``mChooseState``)."""
        return require_object(self.session, self.chooser_ptr(),
                              "选卡界面对象（LawnApp+0x874）", CH_CHOOSE_STATE + 4)

    def app_ptr(self, chooser: Optional[int] = None) -> int:
        return self._u32((chooser or self._chooser()) + CH_APP)

    def board(self, chooser: Optional[int] = None) -> int:
        board = self._u32((chooser or self._chooser()) + CH_BOARD)
        if not board:
            raise ChooserError("选卡界面的 mBoard 为空")
        return board

    def num_packets(self, chooser: Optional[int] = None) -> int:
        """``mBoard->mSeedBank->mNumPackets`` (bank slot count)."""
        bank = self._u32(self.board(chooser) + int(self.profile.board.seed_bank))
        return self._i32(bank + BANK_NUM_PACKETS)

    def seeds_in_bank(self, chooser: Optional[int] = None) -> int:
        return self._i32((chooser or self._chooser()) + CH_IN_BANK)

    def seeds_in_flight(self, chooser: Optional[int] = None) -> int:
        return self._i32((chooser or self._chooser()) + CH_IN_FLIGHT)

    def seed_addr(self, seed_type: int, chooser: Optional[int] = None) -> int:
        """Absolute address of ``mChosenSeeds[seed_type]``."""
        if not (0 <= int(seed_type) < NUM_CHOSEN):
            raise ChooserError("卡类型 %d 超出 0..48" % seed_type)
        return (chooser or self._chooser()) + CHOSEN_SEEDS_OFF + int(seed_type) * CHOSEN_STRIDE

    def chosen_seeds(self, chooser: Optional[int] = None) -> List[ChosenSeed]:
        """All 49 ``ChosenSeed`` entries (one read)."""
        ch = chooser or self._chooser()
        raw = self.session.read(ch + CHOSEN_SEEDS_OFF, NUM_CHOSEN * CHOSEN_STRIDE)
        out = []
        for i in range(NUM_CHOSEN):
            base = i * CHOSEN_STRIDE
            st, state, bi = struct.unpack_from("<iii", raw, base + CS_TYPE)
            imi = struct.unpack_from("<i", raw, base + CS_IMITATER_TYPE)[0]
            out.append(ChosenSeed(i, st, state, bi, bool(raw[base + CS_CRAZY_DAVE]), imi))
        return out

    def bank(self, chooser: Optional[int] = None) -> List[int]:
        """Seed types in the bank (incl. seeds still flying to it), in slot order."""
        seeds = [s for s in self.chosen_seeds(chooser) if s.in_bank]
        seeds.sort(key=lambda s: s.bank_index)
        return [s.seed_type for s in seeds]

    def bank_landed(self, chooser: Optional[int] = None) -> bool:
        """Every bank seed has state ``SEED_IN_BANK`` and nothing is flying."""
        ch = chooser or self._chooser()
        if self.seeds_in_flight(ch):
            return False
        return all(s.state == SEED_IN_BANK for s in self.chosen_seeds(ch) if s.in_bank)

    # -- availability ----------------------------------------------------------------
    def _player_info(self) -> int:
        lawn = self.lawn()
        off = self.profile.lawn_app.get("player_info")
        return self._u32(lawn + off) if (lawn and off is not None) else 0

    def seeds_available(self) -> int:
        """``LawnApp::GetSeedsAvailable`` (see :func:`seeds_available_for_level`);
        49 when the player info is unreadable."""
        pi = self._player_info()
        if not pi:
            return NUM_CHOSEN
        p = self.profile.player_info
        return seeds_available_for_level(self._i32(pi + p.level), self._i32(pi + p.playthrough))

    def available(self, seed_type: int) -> Optional[bool]:
        """``LawnApp::SeedTypeAvailable`` approximation: level-gated for
        0..39, purchase-gated (``mPurchases[type-40] > 0``) for 40..48.
        ``None`` when unknown (no player info)."""
        t = int(seed_type)
        pi = self._player_info()
        if not pi:
            return None
        if t < FIRST_UPGRADE:
            return t < self.seeds_available()
        off = self.profile.player_info.get("purchases")
        if off is None:
            return None
        return self._i32(pi + off + (t - FIRST_UPGRADE) * 4) > 0

    # -- Crazy Dave talk (analysis (g)) -------------------------------------------------
    @property
    def cutscene(self) -> Any:
        """The :class:`~pvztools.control.cutscene.Cutscene` helper, or
        ``None`` when the build is not supported by it."""
        if self._cutscene is None:
            try:
                from .cutscene import Cutscene

                self._cutscene = Cutscene(self.session, self.profile, self.executor, self.observer,
                                          window=self.window, sleep=self._sleep, clock=self._clock)
            except Exception:  # noqa: BLE001 -- unsupported build: fall back to the raw read
                self._cutscene = False
        return self._cutscene or None

    def dialog_active(self) -> bool:
        """Is a Crazy Dave line up (``mCrazyDaveMessageIndex != -1`` in the
        intro scene)?  Uses :attr:`cutscene` when available, else the raw
        GOTY read; ``False`` when it cannot be decided."""
        cs = self.cutscene
        if cs is not None:
            try:
                return bool(cs.dialog_active())
            except Exception:  # noqa: BLE001
                pass
        from .cutscene import crazy_dave_message_index

        idx = crazy_dave_message_index(self.session, self.profile, self.lawn())
        return idx is not None and idx > 0 and self.ui() == UI_CHOOSER

    def _refuse_if_dialog(self, action: str) -> None:
        if self.dialog_active():
            raise ChooserDialogError("%s：戴夫对话未过（mCrazyDaveMessageIndex != -1），"
                                     "先用 Cutscene.skip_dialog() 跳过对话" % action)

    # -- remote plumbing -------------------------------------------------------------
    def _check(self, action: str) -> None:
        ui = self.ui()
        if ui != UI_CHOOSER:
            raise ChooserError("%s：需要在选卡界面（game_ui=2），现在 game_ui=%d" % (action, ui))
        self._chooser()
        chk = getattr(self.executor, "check", None)
        if chk is not None:
            chk(action, ui=(UI_CHOOSER,))

    def _run(self, frag: Fragment, name: str) -> Any:
        """Submit ``frag``; a transition fragment (``needs_main_loop`` False,
        i.e. ``seed_chooser_start``) runs with ``transition=True``: the main
        loop stays blocked for the whole call (Toolkit behaviour), only the
        timeout is longer."""
        self.history.append(frag)
        kw: Dict[str, Any] = {}
        if not getattr(frag, "needs_main_loop", True):
            kw["transition"] = True
        return self.executor.run(frag.asm, name=name, extra_patches=tuple(frag.required_patches), **kw)

    def _batch(self, name: str) -> Fragment:
        return Fragment(self.conv.asm_factory(), [], (), name, ())

    def _poll(self, cond: Callable[[], bool], wait: float, step: float = 0.05) -> bool:
        deadline = self._clock() + max(0.0, float(wait))
        while True:
            if cond():
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(step)

    def wait_landed(self, wait: Optional[float] = None) -> bool:
        """Wait until ``mSeedsInFlight == 0`` (seeds fly 25 ticks); returns
        whether it happened (immediately True in dry run)."""
        if self.dry_run:
            return True
        if wait is None:
            wait = max(1.0, self._frame_s() * FLIGHT_TICKS * 4)
        return self._poll(lambda: self.seeds_in_flight() == 0, wait)

    # -- actions ---------------------------------------------------------------------
    def clear_bank(self) -> List[int]:
        """Return every removable bank seed to the chooser with ONE remote
        call: ``ClickedSeedInBank`` per seed in *descending* bank order, so
        no other seed has to shift (``FindSeedInBank`` finds nothing to the
        right of the clicked slot).  Crazy-Dave picks cannot be removed and
        are kept.  Returns the seed types removed (slot order)."""
        self._check("清空卡槽")
        self.wait_landed()
        ch = self._chooser()
        seeds = [s for s in self.chosen_seeds(ch) if s.state == SEED_IN_BANK and not s.crazy_dave]
        if not seeds:
            return []
        seeds.sort(key=lambda s: s.bank_index, reverse=True)
        batch = self._batch("chooser_clear_bank")
        for s in seeds:
            frag = self.conv.seed_chooser_click_seed(ch, self.seed_addr(s.index, ch), in_bank=True,
                                                     asm=batch.asm)
            batch.ops.extend(frag.ops)
        batch.ret()
        self._run(batch, "chooser_clear_bank")
        removed = [s.seed_type for s in sorted(seeds, key=lambda s: s.bank_index)]
        if not self.dry_run:
            left = self.bank(ch)
            still = [t for t in removed if t in left]
            if still:
                raise ChooserError("清空卡槽后 %s 仍在卡槽" % still)
        return removed

    def pick(self, types: Sequence[SeedSpec], strict: bool = True) -> List[int]:
        """``ClickedSeedInChooser`` for every seed in ``types`` (ids / names /
        ``PlantType``), all in ONE remote call.  Seeds already in the bank are
        skipped; seeds not in the chooser (flying / hidden / unavailable) or
        beyond the free slots raise :class:`ChooserError` (``strict``) or
        are skipped with a warning.  Returns the ids picked."""
        self._check("选卡")
        ids: List[int] = []
        for t in types:
            pid = resolve_seed(t)
            if pid not in ids:
                ids.append(pid)
        self.wait_landed()
        ch = self._chooser()
        seeds = self.chosen_seeds(ch)
        room = self.num_packets(ch) - self.seeds_in_bank(ch)
        picked: List[int] = []
        for pid in ids:
            s = seeds[pid]
            problem = None
            if pid == IMITATER:
                problem = "模仿者要通过模仿者按钮选择，内存选卡不支持"
            elif s.in_bank:
                continue
            elif s.state != SEED_IN_CHOOSER:
                problem = "%s 不在选卡区（state=%d）" % (plant_label(pid), s.state)
            elif self.available(pid) is False:
                problem = "%s 尚未解锁 / 未购买" % plant_label(pid)
            elif len(picked) >= room:
                problem = "卡槽已满（%d 格），放不下 %s" % (self.num_packets(ch), plant_label(pid))
            if problem:
                if strict:
                    raise ChooserError("选卡：" + problem)
                warnings.warn("选卡：" + problem + "，跳过")
                continue
            picked.append(pid)
        if not picked:
            return []
        batch = self._batch("chooser_pick")
        for pid in picked:
            frag = self.conv.seed_chooser_click_seed(ch, self.seed_addr(pid, ch), in_bank=False,
                                                     asm=batch.asm)
            batch.ops.extend(frag.ops)
        batch.ret()
        self._run(batch, "chooser_pick")
        if not self.dry_run:
            have = self.bank(ch)
            missing = [p for p in picked if p not in have]
            if missing:
                raise ChooserError("选卡后 %s 不在卡槽（现有 %s）" % (missing, have))
        return picked

    # -- OnStartButton warnings ----------------------------------------------------------
    def game_mode(self) -> int:
        return int(self.observer.game().mode)

    def scene(self, chooser: Optional[int] = None) -> int:
        return self._i32(self.board(chooser) + int(self.profile.board.scene))

    def level(self, chooser: Optional[int] = None) -> int:
        return self._i32(self.board(chooser) + BOARD_LEVEL)

    def _first_time_adventure(self) -> bool:
        pi = self._player_info()
        return self.game_mode() == MODE_ADVENTURE and bool(pi) and \
            self._i32(pi + self.profile.player_info.playthrough) < 1

    def _flyers_coming(self, chooser: int) -> bool:
        """``FlyersAreComming``: a balloon zombie in any wave of the spawn list."""
        board = self.board(chooser)
        b = self.profile.board
        sl, wt = b.get("spawn_list"), b.get("wave_total")
        if sl is None or wt is None:
            return False
        waves = self._i32(board + wt)
        if not (0 < waves <= 100):
            return False
        raw = self.session.read(board + sl, waves * MAX_ZOMBIES_IN_WAVE * 4)
        for w in range(waves):
            for i in range(MAX_ZOMBIES_IN_WAVE):
                z = struct.unpack_from("<i", raw, (w * MAX_ZOMBIES_IN_WAVE + i) * 4)[0]
                if z == -1:
                    break
                if z == ZOMBIE_BALLOON:
                    return True
        return False

    def _fly_protection_planted(self) -> bool:
        fn = getattr(self.observer, "plants", None)
        if fn is None:
            return False
        try:
            return any(int(p.type) in (CATTAIL, CACTUS) for p in fn())
        except Exception:
            return False

    def missing_for_start(self, bank: Optional[Sequence[int]] = None) -> List[Tuple[str, Tuple[int, ...]]]:
        """Mirror of every ``OnStartButton`` repick warning: ``(reason,
        candidates)`` for each unmet requirement, where any one candidate
        in the bank satisfies it.  Empty list == *Let's Rock!* will not
        open a dialog."""
        ch = self._chooser()
        have = set(self.bank(ch) if bank is None else bank)
        mode = self.game_mode()
        out: List[Tuple[str, Tuple[int, ...]]] = []

        def need(reason: str, cands: Tuple[int, ...]) -> None:
            if not (have & set(cands)):
                out.append((reason, cands))

        if mode == MODE_SEEING_STARS:
            need("seeing-stars 需要杨桃", (STARFRUIT,))
        first = self._first_time_adventure()
        if first and self.level(ch) == 11:
            need("4-1 首通需要小喷菇", (PUFFSHROOM,))
        if mode != MODE_LAST_STAND and mode not in MODE_SURVIVAL_RANGE:
            need("缺少产阳光植物", SUN_PRODUCERS)
        if self.scene(ch) in SCENES_POOL and mode not in MODE_SURVIVAL_RANGE:
            need("泳池场景缺少荷叶 / 海蘑菇 / 缠绕海草", POOL_PLANTS)
        if self.scene(ch) in SCENES_ROOF and self.available(FLOWER_POT) is not False:
            need("屋顶场景缺少花盆", (FLOWER_POT,))
        if mode == MODE_ART_WALLNUT:
            need("坚果艺术需要坚果", (WALLNUT,))
        if mode == MODE_ART_SUNFLOWER:
            for t in (STARFRUIT, UMBRELLA, WALLNUT):
                need("向日葵艺术需要 %s" % plant_label(t), (t,))
        if self._flyers_coming(ch) and not self._fly_protection_planted():
            need("有气球僵尸：需要香蒲 / 仙人掌 / 三叶草", FLYER_PLANTS)
        if mode not in MODE_SURVIVAL_RANGE:
            for up, base in UPGRADE_BASE.items():
                if up in have and base not in have:
                    out.append(("升级植物 %s 需要 %s" % (plant_label(up), plant_label(base)), (base,)))
        return out

    def ensure_start_safe(self) -> List[int]:
        """Pick the first available candidate of every unmet
        :meth:`missing_for_start` requirement (prevention of the modal
        repick dialog).  When the bank is full the last removable non-
        required seed is returned to the chooser to make room.  Returns the
        seeds added."""
        added: List[int] = []
        missing = self.missing_for_start()
        if not missing:
            return added
        ch = self._chooser()
        size = self.num_packets(ch)
        required = {c for _, cands in missing for c in cands}
        for reason, cands in missing:
            cand = next((c for c in cands if self.available(c) is not False
                         and self.chosen_seeds(ch)[c].state == SEED_IN_CHOOSER), None)
            if cand is None:
                raise ChooserError("选卡：%s，但候选 %s 都不可选" % (reason, list(cands)))
            if self.seeds_in_bank(ch) >= size:
                victim = next((t for t in reversed(self.bank(ch)) if t not in required
                               and not self.chosen_seeds(ch)[t].crazy_dave), None)
                if victim is None:
                    raise ChooserError("选卡：%s，卡槽已满且没有可替换的卡" % reason)
                warnings.warn("选卡：%s，卡槽已满，用 %s 换下 %s" % (reason, plant_label(cand), plant_label(victim)))
                self.unpick([victim])
            else:
                warnings.warn("选卡：%s，自动补 %s" % (reason, plant_label(cand)))
            self.pick([cand])
            added.append(cand)
            required.add(cand)
        return added

    def unpick(self, types: Sequence[SeedSpec]) -> List[int]:
        """``ClickedSeedInBank`` for the given bank seeds (descending slot
        order, one call).  Returns the ids removed."""
        self._check("退卡")
        self.wait_landed()
        ch = self._chooser()
        seeds = self.chosen_seeds(ch)
        ids = [resolve_seed(t) for t in types]
        targets = [seeds[i] for i in ids if seeds[i].state == SEED_IN_BANK and not seeds[i].crazy_dave]
        if not targets:
            return []
        targets.sort(key=lambda s: s.bank_index, reverse=True)
        batch = self._batch("chooser_unpick")
        for s in targets:
            frag = self.conv.seed_chooser_click_seed(ch, self.seed_addr(s.index, ch), in_bank=True,
                                                     asm=batch.asm)
            batch.ops.extend(frag.ops)
        batch.ret()
        self._run(batch, "chooser_unpick")
        return [s.seed_type for s in targets]

    def bank_full(self, chooser: Optional[int] = None) -> bool:
        ch = chooser or self._chooser()
        return self.seeds_in_bank(ch) >= self.num_packets(ch)

    def start(self, wait: float = 10.0, auto_fix: bool = True) -> bool:
        """``OnStartButton`` then poll ``game_ui == 3`` (up to ``wait`` s,
        main loop free).  Refuses (no call
        made) when the bank is not full / not landed or a repick warning
        would pop up -- with ``auto_fix`` the missing seeds are added first
        (:meth:`ensure_start_safe`).  Refuses with
        :class:`ChooserDialogError` while a Crazy Dave line is up
        (:meth:`dialog_active`; analysis (g)).  If the ui still does not
        change and a dialog is detected (:meth:`dialog_active` /
        :meth:`GameWindow.dialog_open`), :class:`ChooserDialogError` is
        raised -- never a blind click.  Returns whether the level started
        (always False in dry run)."""
        self._check("开始关卡")
        self._refuse_if_dialog("开始关卡")
        if not self.wait_landed():
            raise ChooserError("开始关卡：卡片仍在飞行（mSeedsInFlight=%d）" % self.seeds_in_flight())
        if auto_fix:
            self.ensure_start_safe()
            self.wait_landed()
        ch = self._chooser()
        if not self.bank_full(ch):
            raise ChooserError("开始关卡：卡槽未满（%d/%d）；OnStartButton 会读到空槽"
                               % (self.seeds_in_bank(ch), self.num_packets(ch)))
        missing = self.missing_for_start()
        if missing:
            raise ChooserError("开始关卡会弹出重选对话框（远程线程里不能弹窗）：%s"
                               % "; ".join(r for r, _ in missing))
        if not self.dry_run and not self.bank_landed(ch):
            raise ChooserError("开始关卡：卡槽中有未落定的卡")
        # final precondition right before the call: still ui 2 and the
        # chooser object still committed (no stale pointer into the game)
        if self.ui() != UI_CHOOSER:
            raise ChooserError("开始关卡：需要在选卡界面（game_ui=2），现在 game_ui=%d" % self.ui())
        self._refuse_if_dialog("开始关卡")
        ch = self._chooser_live()
        frag = self.conv.seed_chooser_start(ch, terminate=True)
        # transition call: main_loop_block is held for the whole call and
        # released when it returns; the ui is polled below with the loop free
        self._run(frag, "chooser_start")
        if self.dry_run:
            return False
        if self._poll(lambda: self.ui() == UI_LEVEL, wait, step=0.1):
            return True
        if self.ui() == UI_CHOOSER:
            if self.dialog_active():
                raise ChooserDialogError("Let's Rock 后界面未切换：戴夫对话仍在进行")
            if self.window is not None:
                try:
                    open_ = self.window.dialog_open(expected_ui=UI_LEVEL, wait=0.5)
                except Exception:
                    open_ = False
                if open_:
                    raise ChooserDialogError("Let's Rock 后界面未切换且疑似有对话框（重选警告？）；不盲点")
        return self.ui() == UI_LEVEL

    #: fill order when the caller gives fewer seeds than the bank holds
    FILL_ORDER: Tuple[int, ...] = (1, 16, 0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                                   17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
                                   32, 33, 34, 35, 36, 37, 38, 39)

    def fill_bank(self) -> List[int]:
        """Pad the bank up to ``num_packets`` with the first *available* seeds
        of :data:`FILL_ORDER` that are not picked yet (*Let's Rock!* stays
        disabled while the bank is not full).  Returns the types added."""
        ch = self._chooser()
        need = self.num_packets(ch) - self.seeds_in_bank(ch)
        if need <= 0:
            return []
        have = set(self.bank(ch))
        added: List[int] = []
        for t in self.FILL_ORDER:
            if need <= 0:
                break
            if t in have or self.available(t) is False:
                continue
            try:
                self.pick([t], strict=True)
            except ChooserError:
                continue
            have.add(t)
            added.append(t)
            need -= 1
        if added:
            warnings.warn("卡槽未满，自动补了 %d 张：%s" % (len(added), added))
        return added

    def select_and_start(self, types: Sequence[SeedSpec], wait: float = 10.0) -> bool:
        """Clear the bank, pick ``types``, pad the bank to its size
        (:meth:`fill_bank`), add what *Let's Rock!* insists on, start."""
        self._check("选卡并开始")
        self._refuse_if_dialog("选卡并开始")
        self.clear_bank()
        self.pick(types)
        self.wait_landed()
        self.fill_bank()
        return self.start(wait=wait, auto_fix=True)

    def __repr__(self) -> str:
        return "MemoryChooser(profile=%s)" % getattr(self.profile, "id", "?")
