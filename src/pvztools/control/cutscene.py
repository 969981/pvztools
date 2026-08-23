"""Level-intro cutscene: Crazy Dave dialog detection and advancement.

Why this module exists
======================
The seed chooser (``game_ui == 2``) is *not* one screen.  ``game_ui`` is
``LawnApp::mGameScene`` and value 2 is ``SCENE_LEVEL_INTRO``: the whole intro
cutscene -- the pan to the right, the optional **Crazy Dave talk**, the
seed-chooser slide-in, the pan back and "Ready / Set / Plant".  While Dave
is talking the cutscene clock is stopped and the ``SeedChooserScreen`` is
neither focused nor mouse-visible; calling ``SeedChooserScreen::OnStartButton``
then (what :class:`~pvztools.control.chooser_mem.MemoryChooser` did during
the 2026-08-22 incident: ui stayed 2, "dialog suspected", blind click
fallback, process died shortly after) drives the level start from a state
the game never reaches by itself.  Everything below is read from the
re-plants source (``Lawn/CutScene.cpp``, ``LawnApp.cpp``, ``Lawn/Board.cpp``)
and verified against the GOTY Steam 1.2.0.1096 decompile
(``pvz-private/decompiled_named/0044xxxx.c`` + ``symbols.csv``) and the PE
bytes of ``popcapgame1_1.2.0.1096.exe``.

How the intro talk works (re-plants ``CutScene.cpp``)
-----------------------------------------------------
* ``CutScene::StartLevelIntro`` picks ``mCrazyDaveDialogStart`` (+0x28) per
  level / mode (``201`` level 1-1, ``1401`` / ``1501`` / ``1551`` slot
  upgrades, ``2400`` / ``2411`` shovel tutorial, ``3200`` tree of wisdom,
  ...; ``-1`` = no talk).  When it is not ``-1``, ``mCrazyDaveTime`` (+0x20)
  becomes ``TimeEarlyDaveLeaveEnd - TimePanRightStart`` (4000 - 1500 =
  2500; +4000 on the adventure final boss) and every later cutscene
  timestamp is shifted by it.
* ``CutScene::AnimateBoard``: at ``mCutsceneTime == TimeEarlyDaveEnterStart``
  (2000) ``LawnApp::CrazyDaveEnter``; at ``TimeEarlyDaveEnterEnd`` (2750)
  ``LawnApp::CrazyDaveTalkIndex(mCrazyDaveDialogStart)`` and
  ``mCrazyDaveDialogStart = -1``.  ``CrazyDaveTalkIndex`` stores the index
  in ``LawnApp::mCrazyDaveMessageIndex``.
* ``CutScene::Update`` (GOTY ``0x440780``): ``aCutsceneTimeStop = mSeedChoosing
  || mApp->mCrazyDaveMessageIndex != -1 || IsInShovelTutorial()``; only when
  it is false does ``mCutsceneTime += 10`` and, at ``TimeSeedChoserSlideOnEnd
  + mCrazyDaveTime`` (4250 + dave), ``StartSeedChooser()`` (sets
  ``mSeedChooserScreen->mMouseVisible``, ``mSeedChoosing = true`` (+0x2C) and
  focuses the chooser widget).  **So the dialog-active condition is
  ``mCrazyDaveMessageIndex != -1`` and the chooser-ready condition is
  ``mSeedChoosing``.**
* The click that advances the talk: ``Board::MouseDown`` (GOTY ``this`` in
  ECX) -> ``if (mApp->mGameScene == SCENE_LEVEL_INTRO) mCutScene->MouseDown()``
  (GOTY ``0x441370``, decompile ``FUN_00441370(*(board+0x174))``) ->
  ``if (IsShowingCrazyDave()) AdvanceCrazyDaveDialog(false)`` where
  ``IsShowingCrazyDave = mGameScene == 2 && mCrazyDaveTime > 0 &&
  mCutsceneTime < TimePanRightEnd (3500) + mCrazyDaveTime``.
* ``CutScene::AdvanceCrazyDaveDialog(bool theJustSkipping)`` (GOTY
  ``0x440ED0``, 1183 B): returns at once in ``GAMEMODE_UPSELL`` (0x47) or
  when ``mCrazyDaveMessageIndex == -1``; ``LawnApp::AdvanceCrazyDaveText``
  (``0x459550``, ``this`` in ECX) looks up ``[CRAZY_DAVE_<n+1>]`` and either
  ``CrazyDaveTalkIndex(n+1)`` or -- no next line -- ``CrazyDaveLeave``
  (``0x459B80``: ``anim_leave``, state ``LEAVING``, ``mCrazyDaveMessageIndex =
  -1``).  Side effects on specific indices: 2406 (shovel tutorial; only
  with ``theJustSkipping == false``), 3200 (wisdom tree fertiliser), 405 /
  2411 (bowling line), 406 (grave stones) and **1503 / 1553: with
  ``theJustSkipping == false`` a modal ``DIALOG_PURCHASE_PACKET_SLOT`` is
  opened and ``Dialog::WaitForResult`` runs a nested message loop** -- the
  one thing a remote call must never do.
* ``CutScene::CancelIntro`` (GOTY ``0x43F950``) is the game's own "skip the
  talk" path: ``while (mCrazyDaveMessageIndex != -1)
  AdvanceCrazyDaveDialog(true)`` -- bytes at ``0x43FA30``: ``push 1; push
  ebp; call 0x440ED0`` (``ebp`` = the CutScene).  ``theJustSkipping = true``
  takes no modal branch and plays no sample.

Advancement strategy chosen: **(a) remote-call the handler**, but the
*skipping* variant the game itself uses -- ``AdvanceCrazyDaveDialog(this,
1)`` -- one line per call (never the whole loop at once, so the caller can
observe and bound it), with the main loop blocked for the whole call like
every non-transition call.  Rejected: (b) posting a click on the speech
bubble (needs input + focus, and at index 1503 / 1553 the click path opens
the purchase dialog), (c) writing ``mCrazyDaveMessageIndex = -1`` by hand
(skips ``CrazyDaveLeave``: Dave would stay on screen with the message text
cleared; the reanimation / sound state would be inconsistent).

GOTY 1.2.0.1096 conventions / offsets (verified)
-----------------------------------------------
``CutScene::AdvanceCrazyDaveDialog`` ``0x440ED0`` -- prologue ``6A FF 64 A1
00 00 00 00 68 70 20 6D 00 50 64 89 25 00 00 00 00 83 EC 70 53 55 8B AC 24
88 00 00 00`` (SEH frame; ``sub esp,0x70; push ebx; push ebp; mov
ebp,[esp+0x88]`` = first stack arg = ``this``; ``cmp byte [esp+0x94],0``
after two more pushes = second stack arg = ``theJustSkipping``), every exit
``ret 8`` (callee cleans) -- :data:`ADVANCE_DIALOG_VA`,
:data:`ADVANCE_DIALOG_PROLOGUE`.  ``CutScene::MouseDown`` ``0x441370``
(``ret 4``, ``this`` on the stack) is the click path that inlines the same
body with ``theJustSkipping = false``.

``CutScene`` layout (re-plants header, confirmed by the GOTY ``Update``
decompile ``in_EAX[2]`` time / ``[8]`` dave time / ``[0xb]`` seed choosing /
``+0x34`` preloaded / ``+0x35`` placed zombies / ``+0x48`` pre-updating and
by the 1051 catalog (``docs/research/lonelystar-catalog.md`` §1, Board+0x15C
"选卡界面属性": +0x2C 选卡结束开始游戏, +0x34 隐形, +0x35 生成僵尸)):
``+0x00 mApp, +0x04 mBoard, +0x08 mCutsceneTime, +0x0C mSodTime, +0x10
mGraveStoneTime, +0x14 mReadySetPlantTime, +0x18 mFogTime, +0x1C mBossTime,
+0x20 mCrazyDaveTime, +0x24 mLawnMowerTime, +0x28 mCrazyDaveDialogStart,
+0x2C mSeedChoosing, +0x30 mZombiesWonReanimID, +0x34 mPreloaded, +0x35
mPlacedZombies, +0x36 mPlacedLawnItems, +0x38 mCrazyDaveCountDown, +0x3C
mCrazyDaveLastTalkIndex, +0x40 mUpsellHideBoard, +0x44
mUpsellChallengeScreen, +0x48 mPreUpdatingBoard``.  The object hangs off
``Board + board.cutscene`` (GOTY ``0x174``, 1051 ``0x15C``).

``LawnApp`` (GOTY; re-plants offsets + 0x120, consistent with
``mGameMode`` 0x7F8 -> 0x918): ``+0x915 mTodCheatKeys, +0x918 mGameMode,
+0x91C mGameScene (profile game_ui), +0x960 mCrazyDaveReanimID, +0x964
mCrazyDaveState, +0x968 mCrazyDaveBlinkCounter, +0x96C
mCrazyDaveBlinkReanimID, +0x970 mCrazyDaveMessageIndex, +0x974
mCrazyDaveMessageText`` (``0x964`` / ``0x970`` read by the ``Update`` and
``AdvanceCrazyDaveDialog`` decompiles; ``0x960`` by ``CrazyDaveStopTalking``
``0x45A780``).  ``CrazyDaveState``: 0 OFF, 1 ENTERING, 2 LEAVING, 3 IDLING,
4 TALKING, 5 HANDING_TALKING, 6 HANDING_IDLING.

Cutscene constants (re-plants, GOTY data refs ``DAT_0072CAB8`` =
``TimePanRightEnd`` 3500, ``DAT_007320A8`` = ``TimeSeedChoserSlideOnEnd``
4250): :data:`TIME_PAN_RIGHT_END`, :data:`TIME_SEED_CHOOSER_SLIDE_ON_END`,
:data:`TIME_EARLY_DAVE_ENTER_END` 2750.

Only GOTY Steam 1.2.0.1096 is supported (the profile family must be
``goty`` and the function VA comes from ``profile.functions
["cutscene_advance_crazy_dave_dialog"]`` when present, else from
:data:`ADVANCE_DIALOG_VA` for profile ``goty_1_2_0_1096_en``); before the
first call the prologue bytes are read from the live process and must
match, so a different build / a patched function is refused rather than
called.
"""
from __future__ import annotations

import struct
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from .backends import BackendError
from .conv import Fragment, convention_for

__all__ = [
    "Cutscene", "CutsceneError", "CutsceneState", "ADVANCE_DIALOG_VA", "ADVANCE_DIALOG_PROLOGUE",
    "CS_TIME", "CS_DAVE_TIME", "CS_DAVE_DIALOG_START", "CS_SEED_CHOOSING", "CS_PRE_UPDATING",
    "APP_GAME_MODE", "APP_GAME_SCENE", "APP_DAVE_STATE", "APP_DAVE_MESSAGE", "SCENE_LEVEL_INTRO",
    "MODE_UPSELL", "TIME_PAN_RIGHT_END", "TIME_SEED_CHOOSER_SLIDE_ON_END", "TIME_EARLY_DAVE_ENTER_END",
    "crazy_dave_message_index",
]

# -- CutScene (GOTY == re-plants) ---------------------------------------------
CS_APP = 0x00
CS_BOARD = 0x04
CS_TIME = 0x08                 #: mCutsceneTime
CS_DAVE_TIME = 0x20            #: mCrazyDaveTime (0 = no talk on this level)
CS_DAVE_DIALOG_START = 0x28    #: mCrazyDaveDialogStart (-1 once the talk started / none)
CS_SEED_CHOOSING = 0x2C        #: mSeedChoosing (bool)
CS_PRELOADED = 0x34
CS_PLACED_ZOMBIES = 0x35
CS_DAVE_COUNTDOWN = 0x38
CS_DAVE_LAST_TALK = 0x3C
CS_PRE_UPDATING = 0x48         #: mPreUpdatingBoard (bool)
CS_SIZE = 0x4C

# -- LawnApp (GOTY 1096) --------------------------------------------------------
APP_TOD_CHEAT = 0x915
APP_GAME_MODE = 0x918
APP_GAME_SCENE = 0x91C
APP_DAVE_REANIM = 0x960
APP_DAVE_STATE = 0x964
APP_DAVE_MESSAGE = 0x970

SCENE_LEVEL_INTRO = 2
SCENE_PLAYING = 3
MODE_UPSELL = 0x47
MODE_INTRO = 0x48

DAVE_OFF, DAVE_ENTERING, DAVE_LEAVING, DAVE_IDLING, DAVE_TALKING = 0, 1, 2, 3, 4
DAVE_STATE_NAMES = {0: "off", 1: "entering", 2: "leaving", 3: "idling", 4: "talking",
                    5: "handing_talking", 6: "handing_idling"}

TIME_PAN_RIGHT_END = 3500
TIME_EARLY_DAVE_ENTER_END = 2750
TIME_SEED_CHOOSER_SLIDE_ON_END = 4250

#: message indices whose *click* path opens a modal dialog (purchase packet
#: slot) -- harmless with theJustSkipping=1 but listed for the report
DIALOG_MESSAGE_INDICES = (1503, 1553)

#: GOTY Steam 1.2.0.1096 ``CutScene::AdvanceCrazyDaveDialog``
ADVANCE_DIALOG_VA = 0x440ED0
ADVANCE_DIALOG_PROLOGUE = bytes.fromhex(
    "6a ff 64 a1 00 00 00 00 68 70 20 6d 00 50 64 89 25 00 00 00 00 83 ec 70 53 55 8b ac 24 88 00 00 00")
ADVANCE_DIALOG_FN = "cutscene_advance_crazy_dave_dialog"
SUPPORTED_PROFILE_IDS = ("goty_1_2_0_1096_en",)


class CutsceneError(BackendError):
    """The cutscene state could not be read / advanced safely."""

    dialog_suspected = False


class CutsceneDialogError(CutsceneError):
    """A Crazy Dave dialog is (still) active: the caller must not proceed
    with chooser / start calls."""

    dialog_suspected = True


class CutsceneState(NamedTuple):
    """Snapshot of the intro cutscene (all fields 0 / False when there is no
    board / cutscene object)."""
    active_dialog: bool          #: mCrazyDaveMessageIndex > 0 in SCENE_LEVEL_INTRO (see dialog_active)
    dave_state: int              #: LawnApp::mCrazyDaveState
    cutscene_time: int           #: CutScene::mCutsceneTime
    seed_chooser_ready: bool     #: mSeedChoosing and no dialog and Dave gone
    message_index: int = -1      #: LawnApp::mCrazyDaveMessageIndex
    dave_time: int = 0           #: CutScene::mCrazyDaveTime
    dialog_start: int = -1       #: CutScene::mCrazyDaveDialogStart (-1 = started / none)
    seed_choosing: bool = False  #: CutScene::mSeedChoosing
    showing_dave: bool = False   #: CutScene::IsShowingCrazyDave()
    scene: int = 0               #: LawnApp::mGameScene (game_ui)
    mode: int = 0                #: LawnApp::mGameMode
    cutscene_ptr: int = 0
    pre_updating: bool = False

    @property
    def dave_state_name(self) -> str:
        return DAVE_STATE_NAMES.get(self.dave_state, str(self.dave_state))

    @property
    def talk_pending(self) -> bool:
        """A talk is scheduled but Dave has not started yet (the dialog
        will become active at ``TimeEarlyDaveEnterEnd``)."""
        return self.scene == SCENE_LEVEL_INTRO and self.dialog_start != -1

    def describe(self) -> str:
        return ("scene=%d time=%d dave_time=%d dave=%s msg=%d dialog_start=%d seed_choosing=%s"
                % (self.scene, self.cutscene_time, self.dave_time, self.dave_state_name,
                   self.message_index, self.dialog_start, self.seed_choosing))


def _family(profile: Any) -> Any:
    return getattr(profile, "conventions", None) or getattr(profile, "family", None)


def _dave_message_offset(profile: Any) -> Optional[int]:
    """The ``mCrazyDaveMessageIndex`` offset for *profile*: the profile's
    ``lawn_app.crazy_dave_message_index`` when it carries one, else
    :data:`APP_DAVE_MESSAGE` for the builds it was verified on
    (:data:`SUPPORTED_PROFILE_IDS`); ``None`` otherwise.  The other GOTY
    family members (1073 / 1093) share ``game_ui`` 0x91c so 0x970 is
    plausible, but it was only read from the 1096 decompile -- not
    assumed."""
    la = getattr(profile, "lawn_app", None)
    getter = getattr(la, "get", None) if la is not None else None
    off = getter("crazy_dave_message_index") if getter else None
    if off is not None:
        return int(off)
    if _family(profile) == "goty" and getattr(profile, "id", None) in SUPPORTED_PROFILE_IDS:
        return APP_DAVE_MESSAGE
    return None


def crazy_dave_message_index(session: Any, profile: Any, lawn: int) -> Optional[int]:
    """``LawnApp::mCrazyDaveMessageIndex`` for a verified GOTY build
    (``None`` when the offset is unknown for this profile / no LawnApp) --
    the cheap dialog signal used by :meth:`GameWindow.dialog_open`."""
    off = _dave_message_offset(profile)
    if not lawn or off is None:
        return None
    try:
        return struct.unpack("<i", session.read(int(lawn) + off, 4))[0]
    except Exception:  # noqa: BLE001 -- unreadable: unknown
        return None


class Cutscene:
    """Read / advance the level-intro cutscene (see module doc).

    ``session`` / ``profile`` / ``executor`` / ``observer`` as for
    :class:`~pvztools.control.chooser_mem.MemoryChooser`; ``window`` is
    optional (``dialog_count`` is consulted before a call: a real modal
    dialog means the game thread is inside a nested loop -- refuse).

    **Caveat**: ``GameWindow.dialog_count`` needs a profile
    ``lawn_app.dialog_map`` offset and no shipped profile defines one, so
    on every current profile the check returns ``None`` and is a no-op: a
    real LawnDialog (the purchase dialog opened by a manual click at
    1503 / 1553, the pause dialog ...) is **not** detected here, and the
    executor's freeze check cannot tell either (the board counter is
    static while a modal is up -> "stable").  The skipping variant
    (``theJustSkipping = true``) itself opens no dialog; the residual risk
    is a call while the game thread sits in ``Dialog::WaitForResult``.
    Until ``SexyAppBase::mDialogMap`` (or ``mHasFocus`` / ``Board.paused``)
    is verified for a profile, keep the game free of dialogs before
    ``play``.
    """

    def __init__(self, session: Any, profile: Any, executor: Any, observer: Any,
                 window: Any = None, *, conv: Any = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 advance_va: Optional[int] = None, verify_prologue: bool = True) -> None:
        self.session = session
        self.profile = profile
        self.executor = executor
        self.observer = observer
        self.window = window
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self.conv = conv if conv is not None else convention_for(profile)
        self.verify_prologue = bool(verify_prologue)
        self.history: List[Fragment] = []
        self._verified = False
        self.advance_va = self._resolve_va(advance_va)

    # -- support gate ----------------------------------------------------------------
    def _resolve_va(self, given: Optional[int]) -> int:
        if _family(self.profile) != "goty":
            raise CutsceneError("过场对话处理只支持 GOTY Steam 1.2.0.1096（profile %s 族 %s）"
                                % (getattr(self.profile, "id", "?"), _family(self.profile)))
        if given:
            return int(given)
        fns = getattr(self.profile, "functions", {}) or {}
        if ADVANCE_DIALOG_FN in fns:
            fn = fns[ADVANCE_DIALOG_FN]
            va = getattr(fn, "va", None)
            try:
                return int(va(self.profile.image_base)) if callable(va) else int(fn)
            except Exception as exc:  # noqa: BLE001
                raise CutsceneError("profile 函数 %s 无法解析：%s" % (ADVANCE_DIALOG_FN, exc))
        if getattr(self.profile, "id", None) in SUPPORTED_PROFILE_IDS:
            return ADVANCE_DIALOG_VA
        raise CutsceneError("过场对话处理只支持 GOTY Steam 1.2.0.1096（profile %s 缺 %s）"
                            % (getattr(self.profile, "id", "?"), ADVANCE_DIALOG_FN))

    def verify_function(self) -> bool:
        """Read the prologue at :attr:`advance_va` from the live process and
        compare with :data:`ADVANCE_DIALOG_PROLOGUE`; raises
        :class:`CutsceneError` on mismatch (different build / patched)."""
        if self._verified:
            return True
        try:
            got = self.session.read(self.advance_va, len(ADVANCE_DIALOG_PROLOGUE))
        except Exception as exc:  # noqa: BLE001
            raise CutsceneError("无法读取 AdvanceCrazyDaveDialog 0x%X：%s" % (self.advance_va, exc))
        if bytes(got) != ADVANCE_DIALOG_PROLOGUE:
            raise CutsceneError("0x%X 处的代码与 CutScene::AdvanceCrazyDaveDialog 的已知序言不符"
                                "（不同版本 / 被修改），拒绝调用" % self.advance_va)
        self._verified = True
        return True

    @property
    def dry_run(self) -> bool:
        safety = getattr(self.executor, "safety", None)
        return bool(getattr(safety, "dry_run", False) or getattr(self.executor, "dry_run", False))

    def _frame_s(self) -> float:
        fn = getattr(self.executor, "frame_ms", None)
        try:
            ms = int(fn()) if fn else 10
        except Exception:  # noqa: BLE001
            ms = 10
        return max(1, ms) / 1000.0

    # -- reads -------------------------------------------------------------------------
    def _i32(self, addr: int) -> int:
        return struct.unpack("<i", self.session.read(addr, 4))[0]

    def _u32(self, addr: int) -> int:
        return struct.unpack("<I", self.session.read(addr, 4))[0]

    def _u8(self, addr: int) -> int:
        return self.session.read(addr, 1)[0]

    def lawn(self) -> int:
        return int(self.observer.lawn_ptr() or 0)

    def ui(self) -> int:
        return int(self.observer.game().ui)

    def board(self) -> int:
        return int(self.observer.board_ptr() or 0)

    def cutscene_ptr(self, board: Optional[int] = None) -> int:
        """``Board::mCutScene`` (0 without a board)."""
        b = self.board() if board is None else board
        if not b:
            return 0
        off = getattr(self.profile.board, "cutscene", None)
        if off is None:
            getter = getattr(self.profile.board, "get", None)
            off = getter("cutscene") if getter else None
        if off is None:
            raise CutsceneError("profile 缺 board.cutscene 偏移")
        return self._u32(b + int(off))

    def state(self) -> CutsceneState:
        lawn = self.lawn()
        if not lawn:
            return CutsceneState(False, 0, 0, False)
        scene = self._i32(lawn + APP_GAME_SCENE)
        mode = self._i32(lawn + APP_GAME_MODE)
        dave_state = self._i32(lawn + APP_DAVE_STATE)
        msg = self._i32(lawn + APP_DAVE_MESSAGE)
        cs = self.cutscene_ptr()
        if not cs:
            return CutsceneState(scene == SCENE_LEVEL_INTRO and msg > 0, dave_state, 0, False,
                                 message_index=msg, scene=scene, mode=mode)
        raw = self.session.read(cs, CS_SIZE)
        t = struct.unpack_from("<i", raw, CS_TIME)[0]
        dave_time = struct.unpack_from("<i", raw, CS_DAVE_TIME)[0]
        dialog_start = struct.unpack_from("<i", raw, CS_DAVE_DIALOG_START)[0]
        seed_choosing = bool(raw[CS_SEED_CHOOSING])
        pre_updating = bool(raw[CS_PRE_UPDATING])
        intro = scene == SCENE_LEVEL_INTRO
        showing = intro and dave_time > 0 and t < TIME_PAN_RIGHT_END + dave_time
        active = intro and msg > 0
        ready = intro and seed_choosing and not active and not showing and dialog_start == -1
        return CutsceneState(active, dave_state, t, ready, message_index=msg, dave_time=dave_time,
                             dialog_start=dialog_start, seed_choosing=seed_choosing,
                             showing_dave=showing, scene=scene, mode=mode, cutscene_ptr=cs,
                             pre_updating=pre_updating)

    def dialog_active(self) -> bool:
        """``True`` while Crazy Dave has a line up (the cutscene clock is
        stopped; chooser / start calls must wait).  "Up" is
        ``mCrazyDaveMessageIndex > 0``: the game sets ``-1`` for none and
        every real line index is >= 100 (``[CRAZY_DAVE_<n>]`` strings), so
        0 -- never produced by the game -- also counts as none."""
        try:
            return self.state().active_dialog
        except Exception:  # noqa: BLE001 -- unreadable board: not provably active
            return False

    # -- advancement ---------------------------------------------------------------
    def _fragment(self, cutscene: int) -> Fragment:
        """``push 1; push cutscene; call AdvanceCrazyDaveDialog`` (``ret 8``)."""
        frag = Fragment(self.conv.asm_factory(), [], (), "cutscene_advance_dialog", (),
                        needs_main_loop=True)
        frag.push_imm32(1)
        frag.push_imm32(int(cutscene))
        frag.call_abs(self.advance_va)
        return frag

    def _precheck(self, st: CutsceneState) -> None:
        if st.scene != SCENE_LEVEL_INTRO:
            raise CutsceneError("推进戴夫对话：需要在关卡开场过场（game_ui=2），现在 %d" % st.scene)
        if st.mode == MODE_UPSELL:
            raise CutsceneError("推进戴夫对话：UPSELL 模式下 AdvanceCrazyDaveDialog 直接返回，不调用")
        if not st.cutscene_ptr:
            raise CutsceneError("推进戴夫对话：Board.mCutScene 为空")
        if st.pre_updating:
            raise CutsceneError("推进戴夫对话：过场正在预更新（mPreUpdatingBoard），稍后再试")
        if self.window is not None:
            # no-op on every shipped profile (no lawn_app.dialog_map): see the class doc
            try:
                n = self.window.dialog_count()
            except Exception:  # noqa: BLE001
                n = None
            if n:
                raise CutsceneDialogError("推进戴夫对话：游戏里有 %d 个模态对话框（嵌套消息循环），不能远程调用" % n)
        chk = getattr(self.executor, "check", None)
        if chk is not None:
            chk("推进戴夫对话", ui=(SCENE_LEVEL_INTRO,))

    def advance_once(self) -> CutsceneState:
        """One ``AdvanceCrazyDaveDialog(this, true)`` call (refused when no
        dialog is active); returns the state after one frame."""
        st = self.state()
        if not st.active_dialog:
            raise CutsceneError("推进戴夫对话：当前没有对话（msg=%d）" % st.message_index)
        self._precheck(st)
        if self.verify_prologue and not self.dry_run:
            self.verify_function()
        frag = self._fragment(st.cutscene_ptr)
        self.history.append(frag)
        self.executor.run(frag.asm, name=frag.name, extra_patches=tuple(frag.required_patches))
        if self.dry_run:
            return st
        self._sleep(self._frame_s())
        return self.state()

    def skip_dialog(self, max_steps: int = 10) -> int:
        """Advance the talk one line per call until no dialog is active.
        Bounded by ``max_steps``; raises :class:`CutsceneDialogError` when a
        dialog is still active afterwards.  Returns the number of calls
        made (0 when nothing was active).  In dry run one fragment is
        recorded and 1 is returned."""
        steps = 0
        st = self.state()
        while st.active_dialog:
            if steps >= max(0, int(max_steps)):
                raise CutsceneDialogError("戴夫对话 %d 步后仍未结束（%s）" % (steps, st.describe()))
            st = self.advance_once()
            steps += 1
            if self.dry_run:
                return steps
        return steps

    def wait_ready_for_chooser(self, timeout: float = 10.0, skip: bool = True,
                               max_steps: int = 10, step: float = 0.05) -> bool:
        """Poll until ``seed_chooser_ready`` (``mSeedChoosing`` set, no
        dialog, Dave gone).  With ``skip`` a dialog that appears meanwhile
        (a talk scheduled for ``TimeEarlyDaveEnterEnd``) is skipped, at most
        ``max_steps`` lines in total.  Returns ``False`` on timeout or when
        the scene left the intro (``ui != 2``)."""
        deadline = self._clock() + max(0.0, float(timeout))
        total = 0
        while True:
            st = self.state()
            if st.scene != SCENE_LEVEL_INTRO:
                return False
            if st.seed_chooser_ready:
                return True
            if st.active_dialog:
                if not skip:
                    return False
                if total >= max_steps:
                    raise CutsceneDialogError("戴夫对话 %d 步后仍未结束（%s）" % (total, st.describe()))
                self.advance_once()
                total += 1
                if self.dry_run:
                    return False
                continue
            if self._clock() >= deadline:
                return False
            self._sleep(step)

    def report(self) -> Dict[str, Any]:
        try:
            st = self.state()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        d = st._asdict()
        d["dave_state_name"] = st.dave_state_name
        d["calls"] = len(self.history)
        return d

    def __repr__(self) -> str:
        return "Cutscene(profile=%s, advance=0x%X)" % (getattr(self.profile, "id", "?"), self.advance_va)
