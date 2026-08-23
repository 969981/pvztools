"""Fix 2 -- never leave the game frozen.

* a timeout ALWAYS disables ``main_loop_block`` (watchdog retries, loud log,
  ``last_timeout`` record, buffer still leaked);
* ``RemoteExecutor.release()`` is idempotent;
* transition fragments (``Fragment.needs_main_loop == False``) keep
  ``main_loop_block`` on for the WHOLE call (Toolkit behaviour; the old
  release-after-call mode raced the game thread inside UI handlers) and only
  get the longer ``TRANSITION_TIMEOUT_MS``; a timeout releases and raises;
* ``MainMenu.award_continue`` / ``start_adventure`` and
  ``MemoryChooser.start`` refuse on the wrong ui / null pointer and submit
  their fragment as a transition.
"""
from __future__ import annotations

import struct
import warnings

import pytest

from pvztools.control import MAIN_LOOP_PATCH, RemoteCallFailed, RemoteCallTimeout, RemoteExecutor
from pvztools.control import remote as remote_mod
from pvztools.control.conv import convention_for
from pvztools.control.conv.base import TRANSITION_FRAGMENTS, Fragment
from pvztools.control.chooser_mem import (
    APP_AWARD_SCREEN, APP_SEED_CHOOSER, ChooserError, pointer_committed, require_object,
)
from pvztools.control.menu import MainMenu, MenuError, UI_AWARD
from pvztools.modifier import Safety

from test_remote import STAMP_1096, LAWN, FakePatches, FakeSession, FakeWin, make  # noqa: F401
from test_chooser_mem import AWARD, SELECTOR, FakeClock, make_chooser_world, fn_va


@pytest.fixture
def profile():
    from pvztools.addr import profile_for_stamp

    return profile_for_stamp(STAMP_1096)


@pytest.fixture
def sess(profile):
    s = FakeSession()
    s.poke(profile.globals.lawn_app, struct.pack("<I", LAWN))
    s.poke(LAWN + profile.lawn_app.frame_duration, struct.pack("<I", 10))
    return s


class FlakyPatches(FakePatches):
    """disable() fails ``fails`` times before succeeding."""

    def __init__(self, fails):
        super().__init__()
        self.fails = fails

    def disable(self, name):
        super().disable(name)
        if self.fails > 0:
            self.fails -= 1
            raise RuntimeError("bytes mismatch")


# ---------------------------------------------------------------------------
# a. timeout path releases the main loop
# ---------------------------------------------------------------------------

def test_timeout_disables_main_loop_and_records(profile, sess, monkeypatch):
    win = FakeWin(wait_result=FakeWin.WAIT_TIMEOUT)
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, dry_run=False)
    with pytest.raises(RemoteCallTimeout) as ei:
        ex.run(b"\x90", timeout_ms=50, name="award_start", extra_patches=("street_zombies_hack",))
    assert not ei.value.main_loop_blocked
    assert ex.patches.calls == [
        ("enable", MAIN_LOOP_PATCH), ("enable", "street_zombies_hack"),
        ("disable", "street_zombies_hack"), ("disable", MAIN_LOOP_PATCH),
    ]
    assert not ex.main_loop_blocked
    # buffer / thread record kept
    assert ex.leaked == [win.buf] and ex.leaked_threads == [0x77]
    assert ex.last_timeout == {
        "name": "award_start", "buffer": win.buf, "thread": 0x77, "timeout_ms": 50,
        "released": True, "release_error": None, "transition": False,
    }


def test_timeout_watchdog_retries_disable_and_warns(profile, sess, monkeypatch):
    win = FakeWin(wait_result=FakeWin.WAIT_TIMEOUT)
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, FlakyPatches(fails=2), dry_run=False)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        with pytest.raises(RemoteCallTimeout) as ei:
            ex.run(b"\x90", timeout_ms=50)
    disables = [c for c in ex.patches.calls if c == ("disable", MAIN_LOOP_PATCH)]
    assert len(disables) == 3            # 2 failures + the success
    assert not ei.value.main_loop_blocked and ex.last_timeout["released"]
    assert not ex.main_loop_blocked
    assert any("解除" in str(x.message) for x in w)
    assert [j.what for j in ex.safety.journal].count("remote_release_failed") == 2


def test_timeout_watchdog_gives_up_after_3_and_flags(profile, sess, monkeypatch):
    win = FakeWin(wait_result=FakeWin.WAIT_TIMEOUT)
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, FlakyPatches(fails=99), dry_run=False)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(RemoteCallTimeout) as ei:
            ex.run(b"\x90", timeout_ms=50)
    assert ei.value.main_loop_blocked and "release()" in str(ei.value)
    assert ex.last_timeout["released"] is False and "bytes mismatch" in ex.last_timeout["release_error"]
    assert ex.main_loop_blocked
    assert len([c for c in ex.patches.calls if c == ("disable", MAIN_LOOP_PATCH)]) == remote_mod.RELEASE_RETRIES
    # release() keeps trying once the patches co-operate again
    ex.patches.fails = 0
    assert ex.release() is True and not ex.main_loop_blocked


def test_running_thread_failure_releases(profile, sess, monkeypatch):
    win = FakeWin()

    def bad_wait(th, timeout_ms=None):
        raise OSError("WaitForSingleObject failed")

    win.wait_for_single_object = bad_wait
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, dry_run=False)
    with pytest.raises(RemoteCallFailed) as ei:
        ex.run(b"\x90", timeout_ms=50)
    assert ei.value.thread_running and not ei.value.main_loop_blocked
    assert ex.patches.calls[-1] == ("disable", MAIN_LOOP_PATCH)
    assert ex.last_timeout["released"]


# ---------------------------------------------------------------------------
# release()
# ---------------------------------------------------------------------------

def test_release_is_idempotent(profile, sess):
    ex = make(profile, sess)
    assert ex.release() is True and ex.patches.calls == []          # nothing to do
    ex.main_loop_blocked = True
    assert ex.release() is True
    assert ex.patches.calls == [("disable", MAIN_LOOP_PATCH)]
    assert ex.release() is True
    assert ex.patches.calls == [("disable", MAIN_LOOP_PATCH)]       # no second disable


def test_release_consults_patch_manager_state(profile, sess):
    class Knows(FakePatches):
        def __init__(self):
            super().__init__()
            self.on = True

        def is_enabled(self, name):
            return self.on and name == MAIN_LOOP_PATCH

        def disable(self, name):
            super().disable(name)
            self.on = False

    ex = make(profile, sess, Knows())
    assert ex.release() is True
    assert ex.patches.calls == [("disable", MAIN_LOOP_PATCH)]
    assert ex.release() is True and len(ex.patches.calls) == 1


def test_release_all_at_exit_covers_live_executors(profile, sess):
    ex = make(profile, sess)
    ex.main_loop_blocked = True
    assert ex in remote_mod._EXECUTORS
    remote_mod.release_all()
    assert ex.patches.calls == [("disable", MAIN_LOOP_PATCH)] and not ex.main_loop_blocked


# ---------------------------------------------------------------------------
# b. transition fragments
# ---------------------------------------------------------------------------

def test_transition_fragment_flags(profile):
    conv = convention_for(profile)
    assert conv.seed_chooser_start(0x1000).needs_main_loop is False
    assert conv.award_start(0x1000).needs_main_loop is False
    assert conv.game_selector_adventure(0x1000).needs_main_loop is False
    assert conv.fade_out_level(0x1000).needs_main_loop is False
    assert conv.sync_profile(0x1000).needs_main_loop is False
    assert conv.put_plant(0x1000, 0, 0, 1).needs_main_loop is True
    assert conv.delete_plant(0x1000).needs_main_loop is True
    assert {"show_seed_chooser", "puzzle_next_stage_clear"} <= TRANSITION_FRAGMENTS
    # a batch absorbing a transition fragment becomes one
    batch = Fragment(conv.asm_factory(), [], (), "batch", ())
    batch.extend(conv.put_plant(0x1000, 0, 0, 1))
    assert batch.needs_main_loop
    batch.extend(conv.award_start(0x1000))
    assert not batch.needs_main_loop


class OrderWin(FakeWin):
    """Records patch state at every wait so the release point is visible."""

    def __init__(self, patches, results):
        super().__init__()
        self.patches = patches
        self.results = list(results)
        self.waits = []

    def wait_for_single_object(self, th, timeout_ms=None):
        self.log.append(("wait", th, timeout_ms))
        self.waits.append((timeout_ms, list(self.patches.calls)))
        return self.results.pop(0) if self.results else 0


def test_transition_blocked_for_whole_call(profile, sess, monkeypatch):
    """One wait for the full transition timeout; the patch is still on when
    the thread is awaited and only released after the call returned."""
    patches = FakePatches()
    win = OrderWin(patches, [0])
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, patches, dry_run=False)
    conv = convention_for(profile)
    frag = conv.award_start(AWARD)
    rc = ex.run_fragment(frag)
    assert rc == 0
    assert len(win.waits) == 1
    t1, state1 = win.waits[0]
    assert t1 == remote_mod.TRANSITION_TIMEOUT_MS
    assert state1 == [("enable", MAIN_LOOP_PATCH)]                     # blocked during the wait
    assert patches.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]
    assert not ex.main_loop_blocked and ex.safety.journal[-1].what == "remote_call"


def test_transition_never_releases_early(profile, sess, monkeypatch):
    patches = FakePatches()
    win = OrderWin(patches, [0])
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, patches, dry_run=False)
    ex.run(b"\x90", transition=True, name="award_start")
    assert len(win.waits) == 1 and win.waits[0][0] == remote_mod.TRANSITION_TIMEOUT_MS
    assert not hasattr(remote_mod, "TRANSITION_RELEASE_MS")
    assert patches.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]


def test_transition_timeout_releases_and_records(profile, sess, monkeypatch):
    patches = FakePatches()
    win = OrderWin(patches, [FakeWin.WAIT_TIMEOUT])
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, patches, dry_run=False)
    with pytest.raises(RemoteCallTimeout) as ei:
        ex.run(b"\x90", transition=True, name="award_start")
    assert ei.value.timeout_ms == remote_mod.TRANSITION_TIMEOUT_MS
    assert not ei.value.main_loop_blocked
    assert len(win.waits) == 1 and win.waits[0][1] == [("enable", MAIN_LOOP_PATCH)]
    # released exactly once, by the timeout path
    assert patches.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]
    assert ex.last_timeout["transition"] and ex.last_timeout["released"]
    assert ex.leaked == [win.buf] and ex.leaked_threads == [0x77]


def test_transition_rejects_frame_lock_and_keeps_explicit_timeout(profile, sess):
    ex = make(profile, sess)
    with pytest.raises(ValueError):
        ex.run(b"\x90", transition=True, at_tick=5)
    ex.run(b"\x90", transition=True, timeout_ms=700)       # dry run; explicit timeout accepted


def test_actions_submit_transition_fragment(profile, sess, monkeypatch):
    """Actions._run passes transition=True for needs_main_loop=False fragments."""
    from pvztools.control import actions as actions_mod

    seen = {}

    class Ex:
        safety = Safety(allow_remote_call=True, allow_patch=True, dry_run=True)

        def run(self, code, **kw):
            seen.update(kw)
            return b""

    act = actions_mod.Actions.__new__(actions_mod.Actions)
    act.executor = Ex()
    act.history = []
    act._batch = None
    conv = convention_for(profile)
    act._run(conv.fade_out_level(0x1000), "fade_out_level", at_tick=10)
    assert seen.get("transition") is True and "at_tick" not in seen
    seen.clear()
    act._run(conv.put_plant(0x1000, 0, 0, 1), "put_plant")
    assert "transition" not in seen


# ---------------------------------------------------------------------------
# c. menu / chooser preconditions
# ---------------------------------------------------------------------------

def test_award_continue_refuses_on_wrong_ui():
    mc, s, p, ex, clock = make_chooser_world(ui=3)
    menu = MainMenu(s, p, ex, sleep=clock.sleep, clock=clock)
    with pytest.raises(MenuError) as ei:
        menu.award_continue()
    assert "game_ui=5" in str(ei.value) and ex.calls == []
    with pytest.raises(MenuError):
        menu.start_adventure()
    assert ex.calls == []


def test_award_continue_refuses_null_pointer():
    mc, s, p, ex, clock = make_chooser_world(ui=UI_AWARD)
    s.poke_u32(LAWN + APP_AWARD_SCREEN, 0)
    menu = MainMenu(s, p, ex, sleep=clock.sleep, clock=clock)
    with pytest.raises(MenuError):
        menu.award_continue()
    assert ex.calls == []


def test_award_continue_refuses_uncommitted_pointer(monkeypatch):
    mc, s, p, ex, clock = make_chooser_world(ui=UI_AWARD)
    menu = MainMenu(s, p, ex, sleep=clock.sleep, clock=clock)
    import pvztools.control.chooser_mem as cm

    monkeypatch.setattr(cm, "pointer_committed", lambda session, addr, size=4: False)
    import pvztools.control.menu as menu_mod

    monkeypatch.setattr(menu_mod, "require_object",
                        lambda session, ptr, what, size=4, error=ChooserError:
                        cm.require_object(session, ptr, what, size, error))
    with pytest.raises(MenuError) as ei:
        menu.award_continue()
    assert "已提交" in str(ei.value) and ex.calls == []


def test_award_continue_submits_transition_and_polls_with_loop_free():
    mc, s, p, ex, clock = make_chooser_world(ui=UI_AWARD)
    kws = []
    orig = ex.run

    def run(code, **kw):
        kws.append(kw)
        return orig(code, **kw)

    ex.run = run
    menu = MainMenu(s, p, ex, sleep=clock.sleep, clock=clock)
    assert menu.award_continue(wait=10.0) == 1
    assert kws[-1].get("transition") is True
    assert menu.history[-1].needs_main_loop is False


def test_start_adventure_submits_transition():
    mc, s, p, ex, clock = make_chooser_world(ui=1)
    kws = []
    orig = ex.run

    def run(code, **kw):
        kws.append(kw)
        return orig(code, **kw)

    ex.run = run
    menu = MainMenu(s, p, ex, sleep=clock.sleep, clock=clock)
    assert menu.start_adventure(wait=10.0) == 2
    assert kws[-1].get("transition") is True


def test_chooser_start_refuses_wrong_ui_and_submits_transition():
    mc, s, p, ex, clock = make_chooser_world(ui=2, bank=(1, 16, 3, 4, 5, 6))
    kws = []
    orig = ex.run

    def run(code, **kw):
        kws.append(kw)
        return orig(code, **kw)

    ex.run = run
    assert mc.start() is True
    assert kws[-1].get("transition") is True
    # now ui 3: refuse, no call
    n = len(ex.calls)
    with pytest.raises(ChooserError):
        mc.start()
    assert len(ex.calls) == n


def test_chooser_start_refuses_null_chooser():
    mc, s, p, ex, clock = make_chooser_world(ui=2, bank=(1, 16, 3, 4, 5, 6))
    s.poke_u32(LAWN + APP_SEED_CHOOSER, 0)
    with pytest.raises(ChooserError):
        mc.start()
    assert ex.calls == []


def test_pointer_committed_fallbacks():
    class S:
        handle = 0

        def read(self, a, n):
            if a == 0x1000:
                return b"\0" * n
            raise OSError("bad read")

    assert pointer_committed(S(), 0) is False
    assert pointer_committed(S(), 0x1000) is True
    assert pointer_committed(S(), 0x2000) is False
    assert require_object(S(), 0x1000, "x") == 0x1000
    with pytest.raises(ChooserError):
        require_object(S(), 0x2000, "x")
    with pytest.raises(MenuError):
        require_object(S(), 0, "x", error=MenuError)


# ---------------------------------------------------------------------------
# review fixes: a failed release is never reported as success
# ---------------------------------------------------------------------------

def test_transition_release_failure_is_not_reported_as_success(profile, sess, monkeypatch):
    """The thread finishes normally but the post-call release (transition)
    fails every attempt: the call must NOT return 0 with the flag cleared."""
    win = FakeWin(wait_result=FakeWin.WAIT_OBJECT_0)
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, FlakyPatches(fails=99), dry_run=False)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        with pytest.raises(RemoteCallFailed) as ei:
            ex.run(b"\x90", name="award_start", transition=True)
    assert not ei.value.thread_running and ei.value.main_loop_blocked
    assert "release()" in str(ei.value)
    assert ex.main_loop_blocked
    assert ex.last_release_failure["name"] == "award_start" and ex.last_release_failure["transition"]
    assert ex.last_timeout is None                     # the thread returned: no timeout record
    assert ex.leaked == [] and ex.leaked_threads == []  # buffer freed normally
    assert any("解除" in str(x.message) for x in w)
    # release() still recovers once the patch manager co-operates
    ex.patches.fails = 0
    assert ex.release() is True and not ex.main_loop_blocked


def test_transition_release_failure_then_recovery_by_watchdog(profile, sess, monkeypatch):
    """The post-call release fails once; the watchdog retry succeeds and the
    call completes normally."""
    win = FakeWin(wait_result=FakeWin.WAIT_OBJECT_0)
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, FlakyPatches(fails=1), dry_run=False)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        assert ex.run(b"\x90", name="award_start", transition=True) == 0
    assert not ex.main_loop_blocked and ex.last_release_failure is None
    assert ex.patches.calls.count(("disable", MAIN_LOOP_PATCH)) == 2


def _always_fail_patches():
    return FlakyPatches(fails=99)


def test_keyboard_interrupt_during_wait_releases_with_watchdog(profile, sess, monkeypatch):
    win = FakeWin()

    def interrupted(th, timeout_ms=None):
        raise KeyboardInterrupt

    win.wait_for_single_object = interrupted
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, _always_fail_patches(), dry_run=False)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        with pytest.raises(KeyboardInterrupt):
            ex.run(b"\x90", name="fire")
    assert ex.main_loop_blocked                      # the truth, not a guess
    assert ex.patches.calls.count(("disable", MAIN_LOOP_PATCH)) == remote_mod.RELEASE_RETRIES
    assert len(w) == remote_mod.RELEASE_RETRIES
    assert [j.what for j in ex.safety.journal].count("remote_release_failed") == remote_mod.RELEASE_RETRIES
    assert ex.last_release_failure["name"] == "fire" and ex.last_release_failure["main_loop_blocked"]
    # release() works from the executor's own flag (no is_enabled probe on FakePatches)
    ex.patches.fails = 0
    assert ex.release() is True and not ex.main_loop_blocked


def test_write_failure_before_thread_marks_exception_when_release_fails(profile, sess, monkeypatch):
    win = FakeWin()
    monkeypatch.setattr(remote_mod, "winremote", win)

    def bad_write(addr, data):
        raise OSError("WriteProcessMemory failed")

    sess.write = bad_write
    ex = make(profile, sess, _always_fail_patches(), dry_run=False)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(RemoteCallFailed) as ei:
            ex.run(b"\x90")
    assert not ei.value.thread_running
    assert ei.value.main_loop_blocked and "release()" in str(ei.value)
    assert ex.main_loop_blocked
    assert ex.patches.calls.count(("disable", MAIN_LOOP_PATCH)) == remote_mod.RELEASE_RETRIES


def test_frame_lock_error_release_failure_is_flagged(profile, sess, monkeypatch):
    from pvztools.control.remote import FrameLockError

    ex = make(profile, sess, _always_fail_patches(), dry_run=False)
    monkeypatch.setattr(ex, "_wait_frozen_status", lambda: (None, "capped"))
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(FrameLockError):
            ex.run(b"\x90")
    assert ex.main_loop_blocked
    assert ex.patches.calls.count(("disable", MAIN_LOOP_PATCH)) == remote_mod.RELEASE_RETRIES


def test_successful_release_on_exception_path_clears_flag(profile, sess, monkeypatch):
    win = FakeWin()

    def interrupted(th, timeout_ms=None):
        raise KeyboardInterrupt

    win.wait_for_single_object = interrupted
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, dry_run=False)
    with pytest.raises(KeyboardInterrupt):
        ex.run(b"\x90")
    assert not ex.main_loop_blocked and ex.last_release_failure is None
    assert ex.patches.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]
