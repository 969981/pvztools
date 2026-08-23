"""Remote-call executor: run shellcode inside the game (DESIGN §6 external
backend, parity §0.3 / §0.4).

Flow (mirrors the documented Toolkit mechanism, own implementation):

1. ``safety`` must grant ``allow_remote_call`` (level ``remote``), the build
   must be mutable (capability FULL / READ_WRITE) and the convention family
   must be known.
2. ``patches.enable("main_loop_block")`` stops the game loop, then
   :meth:`RemoteExecutor._wait_frozen` polls ``Board.main_counter`` until it
   has stopped changing for >= 1.5 frames (``lawn_app.frame_duration``,
   default 10 ms; 200 ms cap) so the main thread is known to be parked
   before foreign code touches Board -- and we know *which tick* it is
   parked on.  When no counter can be read (no board / no reader) the old
   fixed two-frame sleep is used instead.

   **Frame lock** (DESIGN §6.3, external tier): ``run(..., at_tick=N)``
   repeats *block -> read frozen tick -> compare*: early (frozen < N)
   releases the patch, waits (hybrid: sleep to ~1 tick before N, then spin
   on the counter) and blocks again, up to ``retries`` times; a hit
   (frozen == N) executes; an overshoot executes too but is reported as
   ``late_by``.  The whole attempt history is returned in a
   :class:`RemoteResult` (``frozen_tick`` / ``requested_tick`` / ``late_by``
   / ``attempts`` / ``durations``).

   *Guards*: while a lock waits with the game running the counter must keep
   advancing -- ``None`` (board freed: level won / lost) or no change for
   ``LOCK_STALL_S`` (game paused, modal loop, pause dialog) raises
   :class:`FrameLockError` instead of executing at a wrong tick; the whole
   wait is also capped at ``LOCK_MAX_WAIT_S`` wall seconds.  When every
   retry came up early although the counter *was* advancing the code still
   runs with a negative ``late_by``.

   *Freeze lead*: the real patch parks the loop on its next iteration, so a
   block issued on tick ``N`` often freezes on ``N+1``.  The executor learns
   that lag from each attempt (``freeze_lead``, bounded) and blocks that many
   ticks before ``at_tick`` on later calls; an early freeze is corrected by a
   release / re-block cycle, an overshoot only reported (``late_by``).

   *Freeze status*: ``RemoteResult.freeze_status`` records how each freeze
   was confirmed -- ``"stable"`` (counter held still for the stability
   window), ``"capped"`` (the counter was still moving when the cap expired
   -> the main thread is NOT parked; the call is refused with
   :class:`FrameLockError`), ``"unreadable"`` / ``"none"`` (no counter; the
   fixed-sleep fallback).  A static counter cannot tell a parked main thread
   from a paused game -- ``Board.paused`` is checked before a lock when the
   profile knows it.
3. The user's code is wrapped: ``pushad; pushfd; <save esp to a slot inside
   the buffer>`` ... ``<esp = slot>; popfd; popad; xor eax, eax; ret 4``.
   The slot is addressed position-independently (``call $+5; pop eax``), so
   the user code may clobber *every* register including EBP (GOTY
   ``put_plant`` loads the board into EBP) and leave the stack unbalanced
   (cdecl leftovers) -- every register is
   restored, a call that leaves arguments on the stack is tolerated, and the
   thread exits with code 0 (stdcall ``ret 4`` per DESIGN §3.6).
   The user code must **not** end with ``ret`` (that would pop EFLAGS or a
   cdecl leftover as the return address): a trailing ``ret`` / ``ret n`` is
   stripped by :meth:`RemoteExecutor.wrap` (the wrapper owns the epilogue).
4. RWX buffer (``len(code) + 0x100``) via ``VirtualAllocEx`` (lpAddress NULL),
   ``WriteProcessMemory``, ``CreateRemoteThread``, ``WaitForSingleObject``
   with a timeout, ``GetExitCodeThread``, ``VirtualFreeEx``.
5. ``extra_patches`` (a fragment's ``required_patches`` such as
   ``street_zombies_hack``) are enabled *inside* the blocked window -- after
   the freeze is confirmed -- and disabled again before the main loop resumes,
   so the game thread never executes a patched site outside the injection.
6. ``main_loop_block`` is disabled in a ``finally`` -- **always**, including
   after a timeout.  After a **timeout** (or a failure once the thread is
   running -- :class:`RemoteCallFailed` with ``thread_running=True``) the
   thread is *not* terminated (it may be inside the game's allocator), the
   buffer and thread handle are intentionally leaked (``leaked`` /
   ``leaked_threads``) so the thread never executes freed memory -- but the
   main loop is **released**: a game left frozen is a game that crashes
   (observed live: ``award_continue`` timed out under ``main_loop_block``,
   the executor left the patch on and the process died).  The old
   "leave blocked" policy is gone; ``RemoteExecutor.last_timeout`` records
   what happened (name / buffer / thread / whether the release succeeded)
   and the exception carries ``main_loop_blocked`` (True only when every
   release attempt failed).  The release itself is a watchdog
   (:meth:`RemoteExecutor._force_disable`): up to ``RELEASE_RETRIES``
   attempts, each failure logged loudly (``warnings`` + safety journal).
   :meth:`RemoteExecutor.release` force-disables the patch on demand
   (idempotent) and is also run at interpreter exit for every live executor.
7. **UI transitions** (``transition=True``; fragments with
   ``Fragment.needs_main_loop == False``: seed_chooser_start, award_start,
   game_selector_adventure, show_seed_chooser, fade_out_level,
   puzzle_next_stage_clear, sync_profile).  These handlers load levels /
   swap screens and may take many frames, so they get a longer timeout
   (``TRANSITION_TIMEOUT_MS``) -- but the main loop stays blocked for the
   **whole** remote call, exactly like every other call.  An earlier
   "release-after-call" mode (disable the patch 100 ms after the thread
   started, await the rest with the loop free) is gone: it let the game
   thread and our thread run *concurrently inside UI code* (observed live:
   ``SeedChooserScreen::OnStartButton`` injected while a Crazy Dave dialog
   was up, the loop released mid-handler, the process died shortly after).
   Toolkit's ``asm_code_inject`` keeps the loop blocked for every call,
   including ``call_sync_profile`` / ``call_fade_out_level``
   (``docs/research/pvztoolkit-features.md`` §0.1 / §1.17 / §1.18) -- that
   is the proven behaviour and we mirror it.  ``transition`` only selects
   the timeout and is reported in ``last_timeout["transition"]``.  The
   block is never held while waiting for a UI change: callers poll
   ``game_ui`` *after* the call returned (loop free).  On timeout the
   generic path applies: patches released, buffer / thread leaked,
   :class:`RemoteCallTimeout` raised.

``run()`` is the low-level, *unguarded* executor API: it checks the safety
level / capability / convention / writable session but not ``game_ui``
unless ``ui`` is passed -- :class:`~pvztools.control.actions.Actions` does the
UI gating for every user-facing action.  ``run()`` keeps returning the
thread exit code (or the linked bytes in dry-run) for compatibility;
:meth:`RemoteExecutor.run_locked` returns the full :class:`RemoteResult`
and ``RemoteExecutor.last_result`` always holds the most recent one.

``Safety.dry_run`` returns the linked shellcode instead of executing it
(patches are still toggled through the PatchManager, which itself honours
dry-run).
"""
from __future__ import annotations

import atexit
import sys
import time
import warnings
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple, Union

from .asm import Asm
from pvztools._win import remote as winremote
from pvztools.modifier.safety import Safety, SafetyError

__all__ = [
    "RemoteExecutor",
    "RemoteResult",
    "RemoteCallError",
    "RemoteCallTimeout",
    "RemoteCallFailed",
    "FrameLockError",
    "KNOWN_CONVENTIONS",
    "MAIN_LOOP_PATCH",
    "DEFAULT_FRAME_MS",
    "BUFFER_SLACK",
    "FREEZE_STABLE_FRAMES",
    "FREEZE_CAP_S",
    "DEFAULT_LOCK_RETRIES",
    "LOCK_STALL_S",
    "LOCK_MAX_WAIT_S",
    "MAX_FREEZE_LEAD",
    "RELEASE_RETRIES",
    "TRANSITION_TIMEOUT_MS",
    "release_all",
]

#: Convention families with documented register / push conventions
#: (parity §0.4).  Anything else (改版) is refused.
KNOWN_CONVENTIONS = frozenset({"retail", "goty", "goty_zh2010_ja", "goty_zh2012", "beta"})
#: ``call $+5`` (E8 rel32=0): pushes the address of the next instruction
CALL_NEXT = bytes([0xE8, 0, 0, 0, 0])
MAIN_LOOP_PATCH = "main_loop_block"
DEFAULT_FRAME_MS = 10
BUFFER_SLACK = 0x100
#: Thread exit code our wrapper always returns.
WRAPPER_EXIT_CODE = 0
#: ``_wait_frozen``: the counter must hold still for this many frames.
FREEZE_STABLE_FRAMES = 1.5
#: ``_wait_frozen``: give up (and report the last value) after this long.
FREEZE_CAP_S = 0.2
#: ``run(at_tick=...)``: block / release cycles before giving up on a hit.
DEFAULT_LOCK_RETRIES = 6
#: spin granularity while waiting for the counter (seconds)
_SPIN_S = 0.0002
#: frame lock: the counter must advance at least once per this many wall
#: seconds while we wait with the game running (else: paused / stalled)
LOCK_STALL_S = 0.5
#: frame lock: total wall seconds a single lock may wait for ``at_tick``
LOCK_MAX_WAIT_S = 10.0
#: upper bound of the learned ``freeze_lead`` (ticks)
MAX_FREEZE_LEAD = 3
#: watchdog: attempts to disable ``main_loop_block`` after a timeout
RELEASE_RETRIES = 3
#: transition calls: wait for the remote thread this long (loop blocked throughout)
TRANSITION_TIMEOUT_MS = 5000

#: every live executor; released at interpreter exit (see :func:`release_all`)
_EXECUTORS: "weakref.WeakSet[RemoteExecutor]" = weakref.WeakSet()
_atexit_registered = False


def release_all() -> None:
    """Force-disable ``main_loop_block`` on every live executor (atexit)."""
    for ex in list(_EXECUTORS):
        try:
            ex.release()
        except Exception:
            pass


def _ensure_atexit() -> None:
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(release_all)
        _atexit_registered = True


@dataclass
class RemoteResult:
    """Outcome of one :meth:`RemoteExecutor.run_locked` call.

    ``frozen_tick``: ``Board.main_counter`` while the main loop was blocked
    (``None`` when no counter could be read -- fixed-sleep fallback, or
    ``block_main_loop=False``).  ``requested_tick`` / ``late_by`` are set only
    for frame-locked calls (``late_by = frozen - requested``; negative means
    the lock gave up while still early and the code ran anyway).
    ``attempts`` counts block/release cycles; ``durations`` the wall seconds
    of each ``_wait_frozen`` (one per attempt)."""
    exit_code: Any = None
    frozen_tick: Optional[int] = None
    requested_tick: Optional[int] = None
    late_by: Optional[int] = None
    attempts: int = 0
    durations: List[float] = field(default_factory=list)
    #: ``(frozen_tick, duration)`` of every attempt, including early misses
    history: List[Tuple[Optional[int], float]] = field(default_factory=list)
    #: how each freeze was confirmed: ``"stable"`` / ``"capped"`` /
    #: ``"unreadable"`` / ``"none"`` (one entry per attempt)
    freeze_status: List[str] = field(default_factory=list)
    #: ticks before ``at_tick`` the (last) block was issued (learned lag)
    freeze_lead: int = 0
    dry_run: bool = False

    @property
    def freeze_confirmed(self) -> bool:
        """True when the last freeze met the stability criterion."""
        return bool(self.freeze_status) and self.freeze_status[-1] == "stable"

    @property
    def hit(self) -> bool:
        return self.requested_tick is not None and self.late_by == 0


class RemoteCallError(SafetyError):
    """Base class for remote-call failures."""


class RemoteCallFailed(RemoteCallError):
    """A Win32 step failed (alloc / write / thread).

    ``thread_running`` is True when the failure happened after
    ``CreateRemoteThread`` succeeded (the thread may still be inside the
    game): buffer / handle are leaked; like a timeout the main loop is
    released (``main_loop_blocked`` is True only when that release
    failed)."""

    def __init__(self, msg: str, thread_running: bool = False) -> None:
        super().__init__(msg)
        self.thread_running = bool(thread_running)
        self.main_loop_blocked = False

    def mark_blocked(self) -> None:
        """Release failed: the patch is (probably) still enabled (idempotent)."""
        if self.main_loop_blocked:
            return
        self.main_loop_blocked = True
        self.args = (self.args[0] + "；解除 main_loop_block 失败，游戏主循环可能仍暂停"
                     "（立即用 executor.release() / patches.disable('main_loop_block') 解除）",)


class FrameLockError(RemoteCallError):
    """A frame lock (``at_tick``) could not be honoured safely: the game is
    paused / the counter stalled, the board disappeared, the wait exceeded
    ``LOCK_MAX_WAIT_S`` or the main loop would not park.  The code was
    **not** executed."""


class RemoteCallTimeout(RemoteCallError):
    """The remote thread did not finish within ``timeout_ms``.

    The thread is left alone and the shellcode buffer leaked on purpose;
    the main loop is released (``main_loop_blocked`` True only when the
    release watchdog failed every attempt).
    """

    def __init__(self, name: str, timeout_ms: int, buffer: int, thread: int,
                 main_loop_blocked: bool = False) -> None:
        self.name = name
        self.timeout_ms = timeout_ms
        self.buffer = buffer
        #: thread handle, kept *open* (see ``RemoteExecutor.leaked_threads``)
        self.thread = thread
        #: True when ``main_loop_block`` could NOT be disabled (watchdog failed)
        self.main_loop_blocked = main_loop_blocked
        super().__init__("远程调用 %s 在 %d ms 内未返回（线程未终止，缓冲区 0x%X 保留）；"
                         "游戏可能已卡死或崩溃，请检查后再重试" % (name, timeout_ms, buffer))

    def mark_blocked(self) -> None:
        """Release failed: the patch is (probably) still enabled (idempotent)."""
        if self.main_loop_blocked:
            return
        self.main_loop_blocked = True
        self.args = (self.args[0] + "；解除 main_loop_block 失败（游戏主循环可能仍暂停），"
                     "立即用 executor.release() / patches.disable('main_loop_block') 解除",)


class RemoteExecutor:
    """Run shellcode in the attached game."""

    def __init__(self, session: Any, profile: Any, safety: Optional[Safety],
                 patches: Any, sleep: Any = time.sleep,
                 read_counter: Optional[Callable[[], Optional[int]]] = None,
                 now: Callable[[], float] = time.perf_counter) -> None:
        self.session = session
        self.profile = profile
        self.safety = safety if safety is not None else Safety()
        self.patches = patches
        self._sleep = sleep
        self._now = now
        #: ``() -> main_counter | None``; default reads lawn -> board ->
        #: ``board.main_counter`` through the session (None when not in a
        #: level or the profile lacks the field)
        self._read_counter: Callable[[], Optional[int]] = read_counter or self._read_main_counter
        #: most recent :class:`RemoteResult` (also for plain ``run`` calls)
        self.last_result: Optional[RemoteResult] = None
        #: buffers leaked after a timeout (for diagnostics)
        self.leaked: List[int] = []
        #: thread handles left open after a timeout (informational; the
        #: caller may wait on / close them once the game is known to be sane)
        self.leaked_threads: List[int] = []
        #: ticks the block is issued *before* ``at_tick`` (learned from the
        #: freeze lag of previous locked calls; ``0..MAX_FREEZE_LEAD``)
        self.freeze_lead: int = 0
        #: True while this executor believes ``main_loop_block`` is enabled
        self.main_loop_blocked: bool = False
        #: details of the most recent timeout / running-thread failure:
        #: ``{"name", "buffer", "thread", "timeout_ms", "released",
        #: "release_error", "transition"}`` (``None`` until one happens)
        self.last_timeout: Optional[dict] = None
        #: details of the most recent failed patch release on a non-timeout
        #: path (normal return, Ctrl-C ...):
        #: ``{"name", "error", "main_loop_blocked", "transition"}``
        self.last_release_failure: Optional[dict] = None
        _EXECUTORS.add(self)
        _ensure_atexit()

    # -- preconditions ------------------------------------------------------
    def check(self, action: str = "远程调用", ui: Optional[Iterable[int]] = None) -> None:
        """Raise a SafetyError unless a remote call is allowed right now.
        ``ui`` (e.g. ``(2, 3)``) additionally requires ``game_ui`` to be one
        of those values; ``None`` (default) skips the UI gate."""
        self.safety.require_level("remote", action)
        self.safety.require_capability(action, self.profile)
        conv = getattr(self.profile, "conventions", None) or getattr(self.profile, "family", None)
        if conv not in KNOWN_CONVENTIONS:
            raise RemoteCallError("%s：profile %s 的调用约定族 %r 未知（改版？），拒绝远程调用"
                                  % (action, getattr(self.profile, "id", "?"), conv))
        self.safety.require_session_writable(self.session, action)
        if ui is not None:
            self.safety.require_ui(self.session, self.profile, tuple(ui), action)

    def frame_ms(self) -> int:
        """``lawn_app.frame_duration`` (ms), clamped; DEFAULT_FRAME_MS on error."""
        try:
            lawn = self.session.read_ptr(self.profile.globals.lawn_app)
            off = self.profile.lawn_app.get("frame_duration")
            if not lawn or off is None:
                return DEFAULT_FRAME_MS
            fd = self.session.read_i32(lawn + off)
        except Exception:
            return DEFAULT_FRAME_MS
        if fd <= 0 or fd > 1000:
            return DEFAULT_FRAME_MS
        return fd

    def function_va(self, name: str) -> int:
        """VA of a profile function; raises when unknown / unavailable."""
        fn = self.profile.functions.get(name)
        if fn is None:
            raise RemoteCallError("profile %s 未声明函数 %r" % (getattr(self.profile, "id", "?"), name))
        va = fn.va(self.profile.image_base)
        if not va or not getattr(fn, "enabled", True):
            raise RemoteCallError("函数 %s 在 profile %s 中不可用（地址为 0 / 已禁用）"
                                  % (name, getattr(self.profile, "id", "?")))
        return va

    # -- game clock -----------------------------------------------------------
    def _read_main_counter(self) -> Optional[int]:
        """``Board.main_counter`` via lawn_app -> board; ``None`` when the
        board pointer is 0 (not in a level) or anything is unreadable."""
        try:
            lawn = self.session.read_ptr(self.profile.globals.lawn_app)
            if not lawn:
                return None
            board = self.session.read_ptr(lawn + self.profile.lawn_app.board)
            if not board:
                return None
            off = self.profile.board.get("main_counter")
            if off is None:
                return None
            return int(self.session.read_i32(board + off))
        except Exception:
            return None

    def read_counter(self) -> Optional[int]:
        """Current ``main_counter`` (``None`` when unavailable)."""
        try:
            v = self._read_counter()
        except Exception:
            return None
        return None if v is None else int(v)

    def _read_paused(self) -> Optional[bool]:
        """``Board.paused`` (``None`` when unknown / unreadable)."""
        try:
            off = self.profile.board.get("paused")
            if off is None:
                return None
            lawn = self.session.read_ptr(self.profile.globals.lawn_app)
            if not lawn:
                return None
            board = self.session.read_ptr(lawn + self.profile.lawn_app.board)
            if not board:
                return None
            # Board.paused is a one-byte bool (GOTY +0x17C); the neighbouring
            # bytes are other flags and are non-zero during play -> read u8
            return bool(self.session.read(board + off, 1)[0])
        except Exception:
            return None

    def _wait_frozen_status(self, cap_s: float = FREEZE_CAP_S,
                            stable_frames: float = FREEZE_STABLE_FRAMES) -> Tuple[Optional[int], str]:
        """After ``main_loop_block`` is enabled: poll the counter until it
        has not changed for ``stable_frames`` frames (or ``cap_s`` elapsed).
        Returns ``(tick, status)``: ``"stable"`` (criterion met),
        ``"capped"`` (cap expired while the counter was still moving: the
        main thread is **not** parked), ``"unreadable"`` (the counter went
        away mid-wait) or ``"none"`` (no counter at all -> fixed two-frame
        sleep)."""
        v = self.read_counter()
        if v is None:
            self._sleep(self.frame_ms() * 2 / 1000.0)
            return None, "none"
        stable_s = self.frame_ms() * stable_frames / 1000.0
        t0 = self._now()
        last, last_t = v, t0
        while True:
            self._sleep(_SPIN_S)
            t = self._now()
            cur = self.read_counter()
            if cur is None:
                return last, "unreadable"
            if cur != last:
                last, last_t = cur, t
            elif t - last_t >= stable_s:
                return last, "stable"
            if t - t0 >= cap_s:
                return last, "capped"

    def _wait_frozen(self, cap_s: float = FREEZE_CAP_S,
                     stable_frames: float = FREEZE_STABLE_FRAMES) -> Optional[int]:
        """:meth:`_wait_frozen_status` without the status (compatibility)."""
        return self._wait_frozen_status(cap_s, stable_frames)[0]

    def _wait_for_tick(self, at_tick: int, deadline_s: float,
                       stall_s: float = LOCK_STALL_S) -> Optional[int]:
        """Hybrid wait with the game running: sleep until ~1 tick before
        ``at_tick`` (by ``frame_ms``, in chunks so a stall is noticed), then
        spin on the counter until it reaches ``at_tick``.  Returns the last
        counter value seen.  Raises :class:`FrameLockError` when the counter
        becomes unreadable (board gone), stops advancing for ``stall_s``
        (game paused) or ``deadline_s`` wall seconds pass with the target
        still ahead."""
        frame_s = self.frame_ms() / 1000.0
        t0 = self._now()
        cur = self.read_counter()
        if cur is None:
            raise FrameLockError("帧锁定：等待 tick %d 时读不到 main_counter（关卡已结束 / 棋盘已释放），未执行" % at_tick)
        last, last_t = cur, t0
        while cur < at_tick:
            t = self._now()
            if t - t0 >= deadline_s:
                raise FrameLockError("帧锁定：等待 tick %d 超过 %.1f s（当前 %d），未执行"
                                     % (at_tick, deadline_s, cur))
            remaining = at_tick - cur
            if remaining > 1:
                # sleep towards the target, but never longer than a stall
                # window so a paused game is noticed
                self._sleep(max(0.0, min((remaining - 1) * frame_s - _SPIN_S, stall_s)))
            else:
                self._sleep(_SPIN_S)
            nxt = self.read_counter()
            if nxt is None:
                raise FrameLockError("帧锁定：等待 tick %d 时 main_counter 消失（关卡已结束 / 棋盘已释放），未执行"
                                     % at_tick)
            t = self._now()
            if nxt != last:
                last, last_t = nxt, t
            elif t - last_t >= stall_s:
                raise FrameLockError("帧锁定：main_counter 停在 %d 超过 %.2f s（游戏暂停 / 失焦 / 卡住？），"
                                     "目标 tick %d 未执行" % (nxt, stall_s, at_tick))
            cur = nxt
        return cur

    # -- shellcode ------------------------------------------------------------
    @staticmethod
    def wrap(code: Union[bytes, Asm]) -> Asm:
        """Prologue / epilogue around user code (see module doc)."""
        if isinstance(code, Asm):
            user = code.copy()
            user.strip_trailing_ret()
        else:
            raw = bytes(code)
            while raw.endswith(b"\xC3"):
                raw = raw[:-1]
            user = Asm().raw(raw, "user code")
        # layout (offsets): pushad pushfd | call $+5 | pop eax @7 | mov [eax+d1],esp
        # | user | call $+5 | pop eax @p2 | mov esp,[eax+d2] | popfd popad xor ret 4
        # | slot dd 0.  ``call $+5; pop eax`` -> eax = address of the pop.
        pop1 = 2 + 5
        pop2 = 14 + len(user) + 5
        slot = 14 + len(user) + 19
        body = Asm().pushad().pushfd()
        body.raw(CALL_NEXT, "call $+5").pop_reg("eax")
        body.mov_regdisp_reg("eax", slot - pop1, "esp")
        body.extend(user)
        body.raw(CALL_NEXT, "call $+5").pop_reg("eax")
        body.mov_reg_regdisp("esp", "eax", slot - pop2)
        body.popfd().popad().xor_reg_reg("eax", "eax").ret(4)
        assert len(body) == slot
        body.raw(bytes(4), "dd 0 ; saved esp")
        return body

    # -- execution --------------------------------------------------------------
    def run(self, code: Union[bytes, Asm], *, timeout_ms: int = 2000,
            block_main_loop: bool = True, name: str = "shellcode",
            extra_patches: Sequence[str] = (), ui: Optional[Iterable[int]] = None,
            at_tick: Optional[int] = None, max_early: int = 3,
            retries: int = DEFAULT_LOCK_RETRIES, transition: bool = False) -> Union[int, bytes]:
        """Execute ``code`` in the game.  Returns the thread exit code (0 from
        the wrapper) -- or, in dry-run, the linked shellcode bytes.  The full
        :class:`RemoteResult` is available as ``self.last_result`` (see
        :meth:`run_locked` for ``at_tick`` / ``max_early`` / ``retries``).

        ``extra_patches`` are enabled inside the blocked window (after the
        freeze is confirmed) and disabled before the main loop resumes.
        ``ui`` optionally gates on ``game_ui`` (see :meth:`check`).
        ``transition`` marks a UI transition: longer default timeout, same
        whole-call block (module doc item 7)."""
        res = self.run_locked(code, timeout_ms=timeout_ms, block_main_loop=block_main_loop,
                              name=name, extra_patches=extra_patches, ui=ui,
                              at_tick=at_tick, max_early=max_early, retries=retries,
                              transition=transition)
        return res.exit_code

    def run_fragment(self, frag: Any, *, name: Optional[str] = None, **kw: Any) -> Union[int, bytes]:
        """Run a :class:`~pvztools.control.conv.Fragment`: its
        ``required_patches`` become ``extra_patches`` and a fragment with
        ``needs_main_loop == False`` is run as a ``transition``
        (``TRANSITION_TIMEOUT_MS`` unless ``timeout_ms`` is given; the
        main loop stays blocked for the whole call)."""
        kw.setdefault("extra_patches", tuple(getattr(frag, "required_patches", ()) or ()))
        if not getattr(frag, "needs_main_loop", True):
            kw.setdefault("transition", True)
        return self.run(frag.asm, name=name or getattr(frag, "name", "") or "shellcode", **kw)

    def run_locked(self, code: Union[bytes, Asm], *, timeout_ms: int = 2000,
                   block_main_loop: bool = True, name: str = "shellcode",
                   extra_patches: Sequence[str] = (), ui: Optional[Iterable[int]] = None,
                   at_tick: Optional[int] = None, max_early: int = 3,
                   retries: int = DEFAULT_LOCK_RETRIES, transition: bool = False) -> RemoteResult:
        """Like :meth:`run` but returns a :class:`RemoteResult`.

        ``at_tick`` (frame lock): run the code while the game is parked on
        exactly that ``main_counter``.  If the counter is more than
        ``max_early`` ticks short of ``at_tick`` the executor first waits with
        the game *running* (hybrid sleep / spin), then blocks; an early freeze
        (``frozen < at_tick``) releases the patch, waits again and re-blocks,
        at most ``retries`` times.  A freeze on or past ``at_tick`` executes;
        ``late_by = frozen - at_tick`` records an overshoot.  When every retry
        came up early the code still runs (``late_by`` negative) so the caller
        never loses the shot -- it just knows how far off it was.
        ``at_tick`` needs ``block_main_loop``.

        ``transition`` (UI transition, module doc item 7): the main loop is
        blocked for the whole call like any other; ``timeout_ms`` defaults
        to ``TRANSITION_TIMEOUT_MS`` when it is the plain default and the
        flag is reported in ``last_timeout``.  Not combinable with
        ``at_tick``."""
        self.check("远程调用 " + name, ui)
        transition = bool(transition)
        if transition and timeout_ms == 2000:
            timeout_ms = TRANSITION_TIMEOUT_MS
        timeout_ms = int(timeout_ms)
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be > 0")
        if at_tick is not None and not block_main_loop:
            raise ValueError("at_tick requires block_main_loop=True")
        if at_tick is not None and transition:
            raise ValueError("transition calls cannot be frame-locked (at_tick)")
        shell = self.wrap(code)
        size = len(shell) + BUFFER_SLACK
        result = RemoteResult(requested_tick=None if at_tick is None else int(at_tick),
                              dry_run=bool(self.safety.dry_run))
        self.last_result = result
        #: [0] is True while main_loop_block is enabled (updated by _lock on
        #: every enable / disable so a failure mid-retry is still unwound)
        state = [False]
        enabled: List[str] = []

        try:
            if block_main_loop:
                self._lock(result, state, at_tick, int(max_early), int(retries))
                self.main_loop_blocked = state[0]
            for p in extra_patches:
                self.patches.enable(p)
                enabled.append(p)
            if self.safety.dry_run:
                linked = shell.link(0)
                self.safety.log("remote_call", name, 0, None, len(linked), width=len(linked))
                result.exit_code = linked
                return result
            try:
                # transition or not: the loop stays blocked until the
                # thread returns (or the timeout fires) -- see module doc 7
                result.exit_code = self._execute(shell, size, timeout_ms, name)
                return result
            except (RemoteCallTimeout, RemoteCallFailed) as exc:
                if isinstance(exc, RemoteCallFailed) and not exc.thread_running:
                    raise
                # the remote thread may still be running -- but a frozen
                # game crashes; ALWAYS release (watchdog) and record it
                self._release_after_failure(exc, name, enabled, state, timeout_ms, transition)
                raise
        finally:
            # Every exit (normal return, dry-run, KeyboardInterrupt during the
            # wait, RemoteCallFailed before the thread started, FrameLockError,
            # a refused extra patch ...) releases through the same watchdog
            # as the timeout path: retries, loud warnings, journal entries,
            # ``main_loop_blocked`` reflecting the real outcome and the
            # propagating exception marked when it can carry the flag.
            # (after a timeout ``_release_after_failure`` already cleared
            # ``state`` and set ``main_loop_blocked`` to the watchdog outcome)
            exc_val = sys.exc_info()[1]
            propagating = exc_val is not None
            err: Optional[str] = None
            for p in reversed(enabled):
                e = self._force_disable(p, name)
                err = err or e
            del enabled[:]
            if state[0]:
                e = self._force_disable(MAIN_LOOP_PATCH, name)
                err = err or e
                state[0] = False
                self.main_loop_blocked = e is not None
            if err is not None:
                self._record_release_failure(name, err, transition=transition)
                if propagating:
                    mark = getattr(exc_val, "mark_blocked", None)
                    if mark is not None and self.main_loop_blocked and not getattr(
                            exc_val, "main_loop_blocked", False):
                        mark()
                else:
                    fail = RemoteCallFailed("远程调用 %s 完成，但解除补丁失败：%s" % (name, err),
                                            thread_running=False)
                    if self.main_loop_blocked:
                        fail.mark_blocked()
                    raise fail

    def _record_release_failure(self, name: str, err: str, transition: bool) -> None:
        """Remember the most recent failed patch release (diagnostics)."""
        self.last_release_failure = {
            "name": name,
            "error": err,
            "main_loop_blocked": bool(self.main_loop_blocked),
            "transition": bool(transition),
        }

    def _release_after_failure(self, exc: Any, name: str, enabled: List[str], state: List[bool],
                               timeout_ms: int, transition: bool) -> None:
        """Timeout / running-thread failure: force-disable extra patches and
        ``main_loop_block`` (retrying), record ``last_timeout`` and flag the
        exception when the release failed."""
        err: Optional[str] = None
        for p in reversed(enabled):
            e = self._force_disable(p, name)
            err = err or e
        del enabled[:]
        if state[0]:
            e = self._force_disable(MAIN_LOOP_PATCH, name)
            err = err or e
            state[0] = False
        self.main_loop_blocked = bool(err)
        self.last_timeout = {
            "name": name,
            "buffer": getattr(exc, "buffer", None),
            "thread": getattr(exc, "thread", None),
            "timeout_ms": int(timeout_ms),
            "released": err is None,
            "release_error": err,
            "transition": bool(transition),
        }
        if err is not None:
            exc.mark_blocked()

    def _force_disable(self, patch: str, name: str = "") -> Optional[str]:
        """Watchdog: disable ``patch`` with up to ``RELEASE_RETRIES`` attempts;
        every failure is logged loudly (``warnings.warn`` + safety journal).
        Returns ``None`` on success, else the last error text."""
        last: Optional[str] = None
        for attempt in range(1, RELEASE_RETRIES + 1):
            try:
                self.patches.disable(patch)
                if last is not None:
                    warnings.warn("remote %s: %s 第 %d 次才解除成功" % (name, patch, attempt), RuntimeWarning)
                return None
            except Exception as e:  # watchdog must keep trying
                last = "%s: %s" % (type(e).__name__, e)
                warnings.warn("remote %s: 解除 %s 失败（第 %d/%d 次）：%s"
                              % (name, patch, attempt, RELEASE_RETRIES, last), RuntimeWarning)
                try:
                    self.safety.log("remote_release_failed", "%s:%s" % (name, patch), 0, None,
                                    attempt, width=0, dry_run=False)
                except Exception:
                    pass
        return last

    def release(self) -> bool:
        """Force-disable ``main_loop_block`` (idempotent: a no-op when this
        executor does not believe the patch is on and the PatchManager does
        not report it enabled).  Returns True when the patch is known to be
        off afterwards.  Run at interpreter exit for every executor; call it
        from ``Game.close()`` / after any :class:`RemoteCallTimeout`."""
        on = self.main_loop_blocked
        if not on:
            probe = getattr(self.patches, "is_enabled", None)
            if probe is not None:
                try:
                    on = bool(probe(MAIN_LOOP_PATCH))
                except Exception:
                    on = False
        if not on:
            return True
        err = self._force_disable(MAIN_LOOP_PATCH, "release")
        self.main_loop_blocked = err is not None
        return err is None

    def _block_and_freeze(self, result: RemoteResult, state: List[bool]) -> Optional[int]:
        """Enable main_loop_block, confirm the freeze, record the attempt."""
        self.patches.enable(MAIN_LOOP_PATCH)
        state[0] = True
        t0 = self._now()
        frozen, status = self._wait_frozen_status()
        dt = self._now() - t0
        result.attempts += 1
        result.durations.append(dt)
        result.history.append((frozen, dt))
        result.freeze_status.append(status)
        result.frozen_tick = frozen
        if status == "capped":
            # the counter kept moving for the whole cap: the patch did not
            # park the main thread -- injecting now would race it
            raise FrameLockError("main_loop_block 启用 %.2f s 后 main_counter 仍在变化（主循环未停住），拒绝注入"
                                 % FREEZE_CAP_S)
        return frozen

    def _lock(self, result: RemoteResult, state: List[bool], at_tick: Optional[int],
              max_early: int, retries: int) -> None:
        """Block the main loop (frame-locked when ``at_tick`` is given);
        ``state[0]`` mirrors whether the patch is currently enabled.

        Frame lock: let the game run until ``at_tick - freeze_lead`` (the
        hybrid wait; ``max_early`` is accepted for compatibility -- blocking
        early and releasing costs a frozen frame per cycle, so the first
        block is always issued at the target), block, confirm the freeze.
        An early freeze releases and re-blocks (``retries`` times); a stalled
        / vanished counter raises :class:`FrameLockError`."""
        if at_tick is None:
            self._block_and_freeze(result, state)
            return
        at_tick = int(at_tick)
        frame_s = self.frame_ms() / 1000.0
        cur = self.read_counter()
        if cur is None:
            # no counter at all (not in a level / profile lacks the field):
            # cannot lock, behave like a plain call
            self._block_and_freeze(result, state)
            result.late_by = None
            return
        if self._read_paused():
            raise FrameLockError("帧锁定：游戏处于暂停状态（Board.paused），目标 tick %d 未执行" % at_tick)
        lead = max(0, min(int(self.freeze_lead), MAX_FREEZE_LEAD))
        attempt = 0
        while True:
            target = at_tick - lead
            if cur < target:
                deadline = min(LOCK_MAX_WAIT_S, (target - cur + 5) * frame_s + LOCK_STALL_S)
                cur = self._wait_for_tick(target, deadline_s=deadline)
            result.freeze_lead = lead
            frozen = self._block_and_freeze(result, state)
            if frozen is None:
                # the board vanished between the wait and the freeze
                raise FrameLockError("帧锁定：冻结后读不到 main_counter（关卡已结束？），目标 tick %d 未执行" % at_tick)
            result.late_by = frozen - at_tick
            # learn the park latency: ticks that slipped in after the enable
            self._learn_lead(frozen - cur)
            if frozen >= at_tick or attempt >= retries:
                return
            attempt += 1
            # early: release, let the game advance, try again (with the lead
            # just learned -- usually 0 now, since the freeze was early)
            self.patches.disable(MAIN_LOOP_PATCH)
            state[0] = False
            lead = max(0, min(int(self.freeze_lead), at_tick - frozen - 1, MAX_FREEZE_LEAD))
            cur = frozen

    def _learn_lead(self, lag: int) -> None:
        """Adopt the observed freeze lag (``frozen - counter at enable``) as
        the lead for the next lock, clamped to ``0..MAX_FREEZE_LEAD``."""
        self.freeze_lead = max(0, min(int(lag), MAX_FREEZE_LEAD))

    def _execute(self, shell: Asm, size: int, timeout_ms: int, name: str) -> int:
        """Alloc / write / thread / wait (one ``WaitForSingleObject`` for the
        full ``timeout_ms``; patches stay as the caller set them) / exit
        code / free."""
        h = self.session.handle
        try:
            buf = winremote.virtual_alloc_ex(h, size)
        except Exception as e:
            raise RemoteCallFailed("远程调用 %s 失败：%s" % (name, e)) from e
        thread = 0
        #: once the thread runs, any failure keeps buffer + handle alive
        keep = False
        try:
            linked = shell.link(buf)
            self.session.write(buf, linked)
            thread = winremote.create_remote_thread(h, buf, 0)
            keep = True
            r = winremote.wait_for_single_object(thread, timeout_ms)
            if r == winremote.WAIT_TIMEOUT:
                self.leaked.append(buf)
                self.leaked_threads.append(thread)
                self.safety.log("remote_call_timeout", name, buf, None, timeout_ms,
                                width=len(linked), dry_run=False)
                raise RemoteCallTimeout(name, timeout_ms, buf, thread)
            code = winremote.get_exit_code_thread(thread)
            keep = False
            self.safety.log("remote_call", name, buf, None, code, width=len(linked), dry_run=False)
            return code
        except RemoteCallError:
            if keep and buf not in self.leaked:
                self.leaked.append(buf)
                self.leaked_threads.append(thread)
            raise
        except Exception as e:
            if keep:
                self.leaked.append(buf)
                self.leaked_threads.append(thread)
                raise RemoteCallFailed("远程调用 %s 失败（线程已启动，缓冲区 0x%X 保留）：%s" % (name, buf, e),
                                       thread_running=True) from e
            raise RemoteCallFailed("远程调用 %s 失败：%s" % (name, e)) from e
        finally:
            if not keep:
                if thread:
                    try:
                        winremote.close_handle(thread)
                    except Exception:
                        pass
                try:
                    winremote.virtual_free_ex(h, buf)
                except Exception:
                    pass

    def call_function(self, name: str, setup: Optional[Asm] = None, *,
                      cleanup: Optional[Asm] = None, timeout_ms: int = 2000,
                      block_main_loop: bool = True, ui: Optional[Iterable[int]] = None) -> Union[int, bytes]:
        """``setup`` (register loads / pushes for this build's convention) +
        ``call <functions[name]>`` + optional ``cleanup`` (e.g. ``add esp``
        for cdecl), executed through :meth:`run`."""
        va = self.function_va(name)
        code = Asm()
        if setup is not None:
            code.extend(setup)
        code.call_abs(va)
        if cleanup is not None:
            code.extend(cleanup)
        return self.run(code, timeout_ms=timeout_ms, block_main_loop=block_main_loop, name=name, ui=ui)

    def __repr__(self) -> str:
        return "RemoteExecutor(profile=%s, safety=%r)" % (getattr(self.profile, "id", "?"), self.safety)
