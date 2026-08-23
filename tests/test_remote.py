"""Tests for pvztools.control.remote (no live game; fake session / patches)."""
from __future__ import annotations

import struct
import warnings

import pytest

from pvztools.addr import profile_for_stamp
from pvztools.control import (
    Asm,
    MAIN_LOOP_PATCH,
    RemoteCallError,
    RemoteCallFailed,
    RemoteCallTimeout,
    RemoteExecutor,
)
from pvztools.control import remote as remote_mod
from pvztools.modifier import PermissionDenied, Safety, SafetyError

STAMP_1096 = 0x4D02B058
LAWN = 0x028F0000


class FakeSession:
    def __init__(self, writable=True):
        self.mem = {}
        self._writable = writable
        self.handle = 0x1234
        self.writes = []

    @property
    def writable(self):
        return self._writable

    def poke(self, addr, data):
        for i, b in enumerate(bytes(data)):
            self.mem[addr + i] = b

    def read(self, addr, n):
        return bytes(self.mem.get(addr + i, 0) for i in range(n))

    def read_u32(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]

    def read_i32(self, addr):
        return struct.unpack("<i", self.read(addr, 4))[0]

    read_ptr = read_u32

    def write(self, addr, data):
        if not self._writable:
            raise RuntimeError("read-only")
        self.writes.append((addr, bytes(data)))
        self.poke(addr, data)


class FakePatches:
    def __init__(self, fail_enable=False):
        self.calls = []
        self.fail_enable = fail_enable

    def enable(self, name):
        self.calls.append(("enable", name))
        if self.fail_enable:
            raise RuntimeError("patch refused")

    def disable(self, name):
        self.calls.append(("disable", name))


class FakeWin:
    """Stand-in for pvztools._win.remote with scripted behaviour."""
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 0x102

    def __init__(self, wait_result=0, exit_code=0, fail_alloc=False):
        self.log = []
        self.wait_result = wait_result
        self.exit_code = exit_code
        self.fail_alloc = fail_alloc
        self.buf = 0x00A00000

    def virtual_alloc_ex(self, h, size, *a, **k):
        self.log.append(("alloc", h, size))
        if self.fail_alloc:
            raise OSError("VirtualAllocEx failed")
        return self.buf

    def virtual_free_ex(self, h, addr, *a, **k):
        self.log.append(("free", h, addr))

    def create_remote_thread(self, h, start, param=0, *a, **k):
        self.log.append(("thread", h, start, param))
        return 0x77

    def wait_for_single_object(self, th, timeout_ms=None):
        self.log.append(("wait", th, timeout_ms))
        return self.wait_result

    def get_exit_code_thread(self, th):
        self.log.append(("exit", th))
        return self.exit_code

    def close_handle(self, h):
        self.log.append(("close", h))


@pytest.fixture
def profile():
    return profile_for_stamp(STAMP_1096)


@pytest.fixture
def sess(profile):
    s = FakeSession()
    s.poke(profile.globals.lawn_app, struct.pack("<I", LAWN))
    s.poke(LAWN + profile.lawn_app.frame_duration, struct.pack("<I", 10))
    return s


def make(profile, sess, patches=None, **safety_kw):
    kw = dict(allow_remote_call=True, allow_patch=True, dry_run=True)
    kw.update(safety_kw)
    safety = Safety(**kw)
    sleeps = []
    ex = RemoteExecutor(sess, profile, safety, patches or FakePatches(), sleep=sleeps.append)
    ex._sleeps = sleeps
    return ex


PROLOGUE_LEN = 14   # pushad; pushfd; call $+5; pop eax; mov [eax+d1], esp
EPILOGUE_LEN = 19   # call $+5; pop eax; mov esp, [eax+d2]; popfd; popad; xor eax,eax; ret 4
SLOT_LEN = 4


def prologue(user_len: int) -> bytes:
    slot = PROLOGUE_LEN + user_len + EPILOGUE_LEN
    return bytes.fromhex("609C" "E800000000" "58" "89A0") + struct.pack("<I", slot - 7)


def epilogue(user_len: int) -> bytes:
    slot = PROLOGUE_LEN + user_len + EPILOGUE_LEN
    pop2 = PROLOGUE_LEN + user_len + 5
    return (bytes.fromhex("E800000000" "58" "8BA0") + struct.pack("<I", slot - pop2)
            + bytes.fromhex("9D6131C0C20400") + bytes(4))


def test_wrap_restores_esp_even_if_ebp_clobbered():
    """GOTY put_plant sets EBP = board; the epilogue must not depend on it."""
    user = Asm().mov_reg_imm32("ebp", 0x11223344).push_reg("ebp")
    out = RemoteExecutor.wrap(user).link(0x1000)
    assert out == prologue(len(user)) + user.link(0x1000 + PROLOGUE_LEN) + epilogue(len(user))
    # the saved-esp slot is the last dword and both displacements point at it
    slot = len(out) - 4
    assert struct.unpack_from("<I", out, 10)[0] == slot - 7
    pop2 = PROLOGUE_LEN + len(user) + 5
    assert struct.unpack_from("<I", out, pop2 + 3)[0] == slot - pop2
    assert bytes([0x89, 0xEC]) not in out  # no ``mov esp, ebp``


# ---------------------------------------------------------------------------
def test_dry_run_layout_and_patch_order(profile, sess):
    ex = make(profile, sess)
    user = Asm().push_imm32(5).call_abs(0x4105A0)
    out = ex.run(user, name="t")
    assert isinstance(out, bytes)
    assert out.startswith(prologue(len(user)))
    assert out.endswith(epilogue(len(user)))
    body = out[PROLOGUE_LEN:-EPILOGUE_LEN - SLOT_LEN]
    # call linked relative to base 0: E8 sits at PROLOGUE_LEN+5
    rel = (0x4105A0 - (PROLOGUE_LEN + 5 + 5)) & 0xFFFFFFFF
    assert body == bytes.fromhex("6805000000") + b"\xE8" + struct.pack("<I", rel)
    assert ex.patches.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]
    assert ex._sleeps == [0.02]  # 2 frames of 10 ms
    assert sess.writes == []
    assert [e.what for e in ex.safety.journal] == ["remote_call"]
    assert ex.safety.journal[0].dry_run


def test_raw_bytes_and_no_block(profile, sess):
    ex = make(profile, sess)
    out = ex.run(b"\x90\x90", block_main_loop=False)
    assert out == prologue(2) + b"\x90\x90" + epilogue(2)
    assert ex.patches.calls == []
    assert ex._sleeps == []


def test_frame_duration_default_when_unreadable(profile):
    s = FakeSession()  # lawn pointer 0
    ex = make(profile, s)
    assert ex.frame_ms() == remote_mod.DEFAULT_FRAME_MS
    s.poke(profile.globals.lawn_app, struct.pack("<I", LAWN))
    s.poke(LAWN + profile.lawn_app.frame_duration, struct.pack("<I", 33))
    assert ex.frame_ms() == 33
    s.poke(LAWN + profile.lawn_app.frame_duration, struct.pack("<i", -5))
    assert ex.frame_ms() == remote_mod.DEFAULT_FRAME_MS


def test_refusals(profile, sess):
    with pytest.raises(PermissionDenied):
        make(profile, sess, allow_remote_call=False).run(b"\x90")
    with pytest.raises(PermissionDenied):
        make(profile, sess, purity="strict").run(b"\x90")

    class Modded:
        id = "mod"
        conventions = "mystery"
        family = "mystery"
        capability = "FULL"

    with pytest.raises(RemoteCallError):
        make(Modded(), sess).run(b"\x90")
    # live run needs a writable session
    with pytest.raises(PermissionDenied):
        make(profile, FakeSession(writable=False), dry_run=False).run(b"\x90")
    with pytest.raises(ValueError):
        make(profile, sess).run(b"\x90", timeout_ms=0)
    # nothing was toggled on refusal
    ex = make(profile, sess, allow_remote_call=False)
    with pytest.raises(PermissionDenied):
        ex.run(b"\x90")
    assert ex.patches.calls == []


def test_no_disable_when_enable_itself_raises(profile, sess):
    patches = FakePatches(fail_enable=True)
    ex = make(profile, sess, patches=patches)
    with pytest.raises(RuntimeError):
        ex.run(b"\x90")
    assert patches.calls == [("enable", MAIN_LOOP_PATCH)]


def test_call_function_uses_profile_va(profile, sess):
    ex = make(profile, sess)
    setup = Asm().mov_reg_mem("eax", profile.globals.lawn_app)
    out = ex.call_function("put_plant", setup)
    va = profile.function_va("put_plant")
    off = PROLOGUE_LEN + 5  # after mov eax, [..]
    rel = (va - (off + 5)) & 0xFFFFFFFF
    assert out[off:off + 5] == b"\xE8" + struct.pack("<I", rel)
    with pytest.raises(RemoteCallError):
        ex.call_function("no_such_function")


def test_live_path_with_fake_win(profile, sess, monkeypatch):
    win = FakeWin(exit_code=0)
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, dry_run=False)
    code = Asm().push_imm32(1).call_abs(0x4105A0)
    rc = ex.run(code, timeout_ms=500, name="x")
    assert rc == 0
    # buffer written with code linked at the buffer address
    assert len(sess.writes) == 1
    addr, data = sess.writes[0]
    assert addr == win.buf
    assert data == RemoteExecutor.wrap(code).link(win.buf)
    kinds = [e[0] for e in win.log]
    assert kinds == ["alloc", "thread", "wait", "exit", "close", "free"]
    assert win.log[0][2] == len(data) + remote_mod.BUFFER_SLACK
    assert win.log[1][2] == win.buf and win.log[1][3] == 0
    assert win.log[2][2] == 500
    assert ex.patches.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]
    assert ex.safety.journal[-1].what == "remote_call"
    assert not ex.safety.journal[-1].dry_run


def test_timeout_keeps_buffer_thread_but_releases_main_loop(profile, sess, monkeypatch):
    """After a timeout the foreign thread may still be inside Board, so the
    buffer / handle stay leaked -- but the main loop is ALWAYS released (a
    frozen game crashes; see tests/test_remote_safety.py)."""
    win = FakeWin(wait_result=FakeWin.WAIT_TIMEOUT)
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, dry_run=False)
    with pytest.raises(RemoteCallTimeout) as ei:
        ex.run(b"\x90", timeout_ms=50)
    assert ei.value.buffer == win.buf
    assert ei.value.thread == 0x77 and not ei.value.main_loop_blocked
    kinds = [e[0] for e in win.log]
    assert "free" not in kinds and "close" not in kinds
    assert ex.leaked == [win.buf] and ex.leaked_threads == [0x77]
    assert ex.patches.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]
    assert ex.safety.journal[-1].what == "remote_call_timeout"
    assert ex.last_timeout["released"] and ex.last_timeout["name"] == "shellcode"


def test_failure_after_thread_started_keeps_buffer(profile, sess, monkeypatch):
    """WAIT_FAILED / GetExitCodeThread error once the thread runs: the RWX
    buffer must not be freed under a possibly-running thread."""
    win = FakeWin()

    def bad_wait(th, timeout_ms=None):
        win.log.append(("wait", th, timeout_ms))
        raise OSError("WaitForSingleObject failed")

    win.wait_for_single_object = bad_wait
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, dry_run=False)
    with pytest.raises(RemoteCallFailed) as ei:
        ex.run(b"\x90", timeout_ms=50, extra_patches=("street_zombies_hack",))
    kinds = [e[0] for e in win.log]
    assert "free" not in kinds and "close" not in kinds
    assert ex.leaked == [win.buf] and ex.leaked_threads == [0x77]
    # like a timeout: buffer leaked, but the extra patches and the main loop
    # are released (module doc item 6)
    assert ei.value.thread_running and not ei.value.main_loop_blocked
    assert ex.patches.calls == [("enable", MAIN_LOOP_PATCH), ("enable", "street_zombies_hack"),
                                ("disable", "street_zombies_hack"), ("disable", MAIN_LOOP_PATCH)]


def test_failure_before_thread_started_releases_main_loop(profile, sess, monkeypatch):
    win = FakeWin()

    def bad_thread(h, start, param=0, *a, **k):
        raise OSError("CreateRemoteThread failed")

    win.create_remote_thread = bad_thread
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, dry_run=False)
    with pytest.raises(RemoteCallFailed) as ei:
        ex.run(b"\x90", timeout_ms=50)
    assert not ei.value.thread_running and not ei.value.main_loop_blocked
    assert ex.leaked == [] and ex.patches.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]


def test_disable_failure_does_not_mask_original_error(profile, sess, monkeypatch):
    win = FakeWin(fail_alloc=True)
    monkeypatch.setattr(remote_mod, "winremote", win)

    class BadDisable(FakePatches):
        def disable(self, name):
            super().disable(name)
            raise RuntimeError("unknown bytes")

    ex = make(profile, sess, BadDisable(), dry_run=False)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(RemoteCallFailed) as ei:
            ex.run(b"\x90")
    assert "VirtualAllocEx" in str(ei.value)
    # the original error propagates, but it is marked and the executor
    # keeps reporting the loop as blocked (the watchdog tried 3 times)
    assert ei.value.main_loop_blocked and ex.main_loop_blocked
    assert ex.patches.calls.count(("disable", MAIN_LOOP_PATCH)) == remote_mod.RELEASE_RETRIES
    # ... and a disable failure on the success path is reported (typed)
    ex2 = make(profile, sess, BadDisable(), dry_run=True)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        with pytest.raises(RemoteCallFailed) as ei2:
            ex2.run(b"\x90")
    assert "unknown bytes" in str(ei2.value) and ex2.main_loop_blocked


def test_extra_patches_enabled_inside_blocked_window(profile, sess):
    ex = make(profile, sess)
    ex.run(b"\x90", extra_patches=("street_zombies_hack",))
    assert ex.patches.calls == [("enable", MAIN_LOOP_PATCH), ("enable", "street_zombies_hack"),
                                ("disable", "street_zombies_hack"), ("disable", MAIN_LOOP_PATCH)]
    assert len(ex._sleeps) == 1


def test_run_ui_gate_optional(profile, sess):
    ex = make(profile, sess)
    ex.run(b"\x90")  # unguarded by default
    with pytest.raises(SafetyError):
        ex.run(b"\x90", ui=(2, 3))  # fake session: game_ui unreadable / not 2,3


def test_wrap_strips_trailing_ret():
    """A user ``ret`` would pop EFLAGS / a cdecl leftover as the return address."""
    user = Asm().push_imm32(0x3000).call_abs(0x46DE50).ret()
    out = RemoteExecutor.wrap(user).link(0x1000)
    bare = Asm().push_imm32(0x3000).call_abs(0x46DE50)
    assert out == RemoteExecutor.wrap(bare).link(0x1000)
    assert user.ends_with_ret()  # caller's builder untouched
    assert RemoteExecutor.wrap(b"\x90\xC3").link(0) == RemoteExecutor.wrap(b"\x90").link(0)
    # ret imm16 and stacked rets
    u2 = Asm().nop().ret(4).ret()
    assert u2.strip_trailing_ret() == 2 and u2.bytes_unlinked() == b"\x90"
    # fixups inside a stripped ret are impossible, but those before it survive
    u3 = Asm().call_abs(0x401000).ret()
    u3.strip_trailing_ret()
    assert u3.fixups == [(1, 0x401000)]


def test_win32_failure_unblocks(profile, sess, monkeypatch):
    win = FakeWin(fail_alloc=True)
    monkeypatch.setattr(remote_mod, "winremote", win)
    ex = make(profile, sess, dry_run=False)
    with pytest.raises(RemoteCallFailed):
        ex.run(b"\x90")
    assert ex.patches.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]

    # failure after alloc (write raises) -> buffer freed, patch disabled
    win2 = FakeWin()
    monkeypatch.setattr(remote_mod, "winremote", win2)
    bad = FakeSession()
    bad.poke(profile.globals.lawn_app, struct.pack("<I", LAWN))

    def boom(addr, data):
        raise OSError("WriteProcessMemory failed")

    bad.write = boom
    ex2 = make(profile, bad, dry_run=False)
    with pytest.raises(RemoteCallFailed):
        ex2.run(b"\x90")
    assert [e[0] for e in win2.log] == ["alloc", "free"]
    assert ex2.patches.calls == [("enable", MAIN_LOOP_PATCH), ("disable", MAIN_LOOP_PATCH)]


def test_win_modules_importable():
    from pvztools._win import remote as w
    from pvztools._win import user32 as u
    assert w.MEM_RELEASE == 0x8000 and w.WAIT_TIMEOUT == 0x102
    assert u.GAME_WINDOW_CLASS == "MainWindow"
