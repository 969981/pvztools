"""Tests for pvztools.process (discovery + attach)."""
from __future__ import annotations

import struct
import sys

import pytest

from pvztools.process import (
    AccessDenied,
    GameNotRunning,
    MultipleGames,
    NotA32BitProcess,
    ProcessInfo,
    Session,
    attach,
    find_game_processes,
)
import importlib

attach_mod = importlib.import_module("pvztools.process.attach")

STAMP = 0x4D02B058
SIZE_OF_IMAGE = 0x3EB000
BASE = 0x400000


def make_pe_header(stamp=STAMP, size_of_image=SIZE_OF_IMAGE, image_base=BASE, magic=0x10B):
    """Synthetic MZ/PE header laid out like a mapped image."""
    e_lfanew = 0x80
    hdr = bytearray(0x400)
    hdr[:2] = b"MZ"
    struct.pack_into("<I", hdr, 0x3C, e_lfanew)
    hdr[e_lfanew:e_lfanew + 4] = b"PE\0\0"
    fh = e_lfanew + 4
    struct.pack_into("<HHI", hdr, fh, 0x14C, 5, stamp)  # machine, nsec, stamp
    opt = fh + 20
    struct.pack_into("<H", hdr, opt, magic)
    if magic == 0x10B:
        struct.pack_into("<I", hdr, opt + 28, image_base)
    else:
        struct.pack_into("<Q", hdr, opt + 24, image_base)
    struct.pack_into("<I", hdr, opt + 56, size_of_image)
    return bytes(hdr)


class FakeK32:
    """Stand-in for pvztools._win.kernel32."""

    def __init__(self, procs, wow64=True, deny=False, mem=None):
        self.procs = procs
        self.wow64 = wow64
        self.deny = deny
        self.mem = dict(mem or {BASE: make_pe_header()})
        self.opened = []
        self.closed = []
        self.writes = []

    def list_processes(self):
        return list(self.procs)

    def open_process(self, pid, write=False, create_thread=False):
        if self.deny:
            raise OSError(5, "Access is denied")
        self.opened.append((pid, write, create_thread))
        return 0x1234

    def close_handle(self, h):
        self.closed.append(h)

    def is_wow64(self, h):
        return self.wow64

    def process_image_path(self, h):
        return r"C:\fake\popcapgame1.exe"

    def read(self, h, addr, n):
        for start, blob in self.mem.items():
            if start <= addr and addr + n <= start + len(blob):
                off = addr - start
                return blob[off:off + n]
        raise OSError(299, "ReadProcessMemory")

    def write(self, h, addr, data):
        self.writes.append((addr, bytes(data)))


def install(monkeypatch, k32, windowed=None):
    monkeypatch.setattr(attach_mod, "_kernel32", lambda: k32)
    monkeypatch.setattr(attach_mod, "_pids_with_window_class", lambda cls_name="MainWindow": windowed)


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def test_find_none(monkeypatch):
    install(monkeypatch, FakeK32([ProcessInfo(1, "explorer.exe", None)]))
    assert find_game_processes() == []


def test_find_matches_case_insensitively_and_accepts_tuples(monkeypatch):
    install(monkeypatch, FakeK32([(7, "PopCapGame1.EXE"), ProcessInfo(2, "notepad.exe", None)]))
    found = find_game_processes()
    assert found == [ProcessInfo(7, "PopCapGame1.EXE", None)]


def test_prefer_window_owner_over_launcher(monkeypatch):
    procs = [ProcessInfo(10, "PlantsVsZombies.exe", None), ProcessInfo(20, "popcapgame1.exe", None)]
    install(monkeypatch, FakeK32(procs), windowed={20})
    assert [p.pid for p in find_game_processes()] == [20]


def test_prefer_by_name_when_window_check_unavailable(monkeypatch):
    procs = [ProcessInfo(10, "PlantsVsZombies.exe", None), ProcessInfo(20, "popcapgame1.exe", None)]
    install(monkeypatch, FakeK32(procs), windowed=None)
    assert [p.pid for p in find_game_processes()] == [20]


def test_window_owner_can_be_launcher_name(monkeypatch):
    # A non-Steam install: PlantsVsZombies.exe *is* the game and owns the window.
    procs = [ProcessInfo(10, "PlantsVsZombies.exe", None), ProcessInfo(20, "popcapgame1.exe", None)]
    install(monkeypatch, FakeK32(procs), windowed={10})
    assert [p.pid for p in find_game_processes()] == [10]


def test_two_real_games_kept(monkeypatch):
    procs = [ProcessInfo(20, "popcapgame1.exe", None), ProcessInfo(21, "popcapgame1.exe", None)]
    install(monkeypatch, FakeK32(procs), windowed={20, 21})
    assert [p.pid for p in find_game_processes()] == [20, 21]


def test_extra_and_custom_names(monkeypatch):
    procs = [ProcessInfo(5, "pvz_mod.exe", None), ProcessInfo(6, "popcapgame1.exe", None)]
    install(monkeypatch, FakeK32(procs), windowed=None)
    assert [p.pid for p in find_game_processes(extra_names=["PVZ_MOD.exe"])] == [6, 5]
    assert [p.pid for p in find_game_processes(names=["pvz_mod.exe"])] == [5]
    assert find_game_processes(extra_names=["PVZ_MOD.exe"], prefer=False)[0].pid == 6


# --------------------------------------------------------------------------- #
# attach
# --------------------------------------------------------------------------- #
def test_attach_not_running(monkeypatch):
    install(monkeypatch, FakeK32([]))
    with pytest.raises(GameNotRunning, match="游戏没有运行"):
        attach()


def test_attach_multiple(monkeypatch):
    procs = [ProcessInfo(20, "popcapgame1.exe", None), ProcessInfo(21, "popcapgame1.exe", None)]
    install(monkeypatch, FakeK32(procs), windowed=None)
    with pytest.raises(MultipleGames) as ei:
        attach()
    assert [c.pid for c in ei.value.candidates] == [20, 21]
    assert "pid=" in str(ei.value)


def test_attach_explicit_pid_bypasses_multi(monkeypatch):
    procs = [ProcessInfo(20, "popcapgame1.exe", None), ProcessInfo(21, "popcapgame1.exe", None)]
    k = FakeK32(procs)
    install(monkeypatch, k, windowed=None)
    with attach(pid=21) as s:
        assert s.pid == 21
    assert k.opened == [(21, False, False)]


def test_attach_unknown_pid(monkeypatch):
    install(monkeypatch, FakeK32([]))
    with pytest.raises(GameNotRunning):
        attach(pid=999)


def test_attach_access_denied(monkeypatch):
    install(monkeypatch, FakeK32([ProcessInfo(20, "popcapgame1.exe", None)], deny=True))
    with pytest.raises(AccessDenied, match="管理员"):
        attach()


def test_attach_not_wow64_closes_handle(monkeypatch):
    k = FakeK32([ProcessInfo(20, "popcapgame1.exe", None)], wow64=False)
    install(monkeypatch, k)
    with pytest.raises(NotA32BitProcess):
        attach()
    assert k.closed == [0x1234]


def test_attach_pe32plus_rejected(monkeypatch):
    k = FakeK32([ProcessInfo(20, "popcapgame1.exe", None)],
                mem={BASE: make_pe_header(magic=0x20B, image_base=0x140000000)})
    install(monkeypatch, k)
    with pytest.raises(NotA32BitProcess):
        attach()


def test_attach_bad_header(monkeypatch):
    k = FakeK32([ProcessInfo(20, "popcapgame1.exe", None)], mem={BASE: b"\0" * 0x400})
    install(monkeypatch, k)
    with pytest.raises(attach_mod.ProcessError, match="MZ"):
        attach()
    assert k.closed == [0x1234]


def test_attach_session_fields_and_reads(monkeypatch):
    mem = {BASE: make_pe_header(), 0x731C50: struct.pack("<I", 0xDEADBEEF) + struct.pack("<i", -7)}
    k = FakeK32([ProcessInfo(20, "popcapgame1.exe", None)], mem=mem)
    install(monkeypatch, k)
    s = attach()
    assert isinstance(s, Session)
    assert (s.pid, s.name, s.path) == (20, "popcapgame1.exe", r"C:\fake\popcapgame1.exe")
    assert s.handle == 0x1234
    assert s.stamp == STAMP
    assert s.size_of_image == SIZE_OF_IMAGE
    assert s.image_base == BASE
    assert s.read_u32(0x731C50) == 0xDEADBEEF
    assert s.read_i32(0x731C54) == -7
    assert s.read(0x731C50, 2) == b"\xef\xbe"
    assert s.read_ptr(0x731C50) == 0xDEADBEEF
    assert not s.writable and s.writer is None
    with pytest.raises(attach_mod.ProcessError, match="只读"):
        s.write_u32(0x731C50, 1)
    assert "0x4d02b058" in repr(s)
    s.close()
    assert s.closed and k.closed == [0x1234]
    with pytest.raises(attach_mod.ProcessError):
        s.read_u32(BASE)
    s.close()  # idempotent


def test_attach_write_passthrough(monkeypatch):
    k = FakeK32([ProcessInfo(20, "popcapgame1.exe", None)])
    install(monkeypatch, k)
    with attach(write=True) as s:
        assert s.writable
        s.write_u32(0x731C50 + 0x5578, 9990)
        s.write_i32(0x10, -1)
    assert k.opened == [(20, True, False)]
    assert k.writes == [(0x731C50 + 0x5578, struct.pack("<I", 9990)), (0x10, b"\xff\xff\xff\xff")]
    assert k.closed == [0x1234]


def test_attach_keeps_list_entry_path_when_present(monkeypatch):
    k = FakeK32([ProcessInfo(20, "popcapgame1.exe", r"D:\g\popcapgame1.exe")])
    install(monkeypatch, k)
    with attach() as s:
        assert s.path == r"D:\g\popcapgame1.exe"


# --------------------------------------------------------------------------- #
# live (skipped when no game is running)
# --------------------------------------------------------------------------- #
def _live_candidates():
    if sys.platform != "win32":
        return []
    try:
        return find_game_processes()
    except Exception:
        return []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_live_attach():
    cands = _live_candidates()
    if not cands:
        pytest.skip("game not running")
    if len(cands) > 1:
        pytest.skip("several game processes running")
    try:
        s = attach()
    except AccessDenied as exc:
        pytest.skip(str(exc))
    with s:
        assert s.name.lower() in ("popcapgame1.exe", "plantsvszombies.exe")
        assert s.image_base == BASE
        assert s.stamp == 0x4D02B058, hex(s.stamp)
        assert s.size_of_image > 0x300000
        assert s.path and s.path.lower().endswith(".exe")
        # lawn pointer: either NULL (not yet created) or inside the process.
        lawn = s.read_u32(0x731C50)
        assert lawn == 0 or lawn > 0x10000
