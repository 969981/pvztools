"""Tests for the beginner facade (src/pvztools/game.py) and the top-level package."""
from __future__ import annotations

import os
import struct
import sys

import pytest

import pvztools
from pvztools import game as game_mod
from pvztools.addr import profile_for_stamp
from pvztools.game import Game, GameCheats, CheatFlags, NotInLevel, PermissionDenied, attach

STAMP_1096 = 0x4D02B058
LAWN = 0x028F0000
BOARD = 0x1BA40000
ZOMBIES = 0x1C000000
PLANTS = 0x1D000000
SEEDS = 0x1E000000


class FakeSession:
    """Sparse dict-backed memory mirroring pvztools.process.Session."""

    def __init__(self, writable=True, stamp=STAMP_1096):
        self.mem = {}
        self._writable = writable
        self.closed = False
        self.writes = []
        self.stamp = stamp
        self.size_of_image = 0x41E000
        self.image_base = 0x400000
        self.pid = 4242
        self.name = "popcapgame1.exe"
        self.path = None

    @property
    def writable(self):
        return self._writable

    def poke(self, addr, data):
        for i, b in enumerate(bytes(data)):
            self.mem[addr + i] = b

    def poke_u32(self, addr, v):
        self.poke(addr, struct.pack("<I", v & 0xFFFFFFFF))

    def poke_i32(self, addr, v):
        self.poke(addr, struct.pack("<i", v))

    def read(self, addr, n):
        return bytes(self.mem.get(addr + i, 0) for i in range(n))

    def read_u32(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]

    def read_i32(self, addr):
        return struct.unpack("<i", self.read(addr, 4))[0]

    def read_u8(self, addr):
        return self.read(addr, 1)[0]

    read_ptr = read_u32

    def write(self, addr, data):
        if not self._writable:
            raise RuntimeError("read-only")
        self.writes.append((addr, bytes(data)))
        self.poke(addr, data)

    def write_u32(self, addr, v):
        self.write(addr, struct.pack("<I", v))

    def close(self):
        self.closed = True


def make_session(writable=True, ui=3, in_level=True):
    p = profile_for_stamp(STAMP_1096)
    s = FakeSession(writable=writable)
    s.poke_u32(p.globals.lawn_app, LAWN)
    s.poke_u32(LAWN + p.lawn_app.board, BOARD if in_level else 0)
    s.poke_u32(LAWN + p.lawn_app.game_ui, ui)
    s.poke_u32(LAWN + p.lawn_app.game_mode, 0)
    s.poke_u32(LAWN + p.lawn_app.frame_duration, 10)
    s.poke_u32(BOARD + p.board.sun, 75)
    s.poke_u32(BOARD + p.board.wave, 3)
    s.poke_u32(BOARD + p.board.wave_total, 20)
    s.poke_u32(BOARD + p.board.countdown, 500)
    s.poke_u32(BOARD + p.board.countdown_start, 600)
    s.poke_u32(BOARD + p.board.main_counter, 1234)
    s.poke_u32(BOARD + p.board.scene, 2)
    s.poke_u32(BOARD + p.board.adventure_level, 7)
    # spawn list: two waves, -1 terminated
    sl = BOARD + p.board.spawn_list
    for i, v in enumerate((0, 2, -1)):
        s.poke_i32(sl + 4 * i, v)
    for i, v in enumerate((0, 0, 4, -1)):
        s.poke_i32(sl + 200 + 4 * i, v)
    for w in range(2, 20):  # remaining waves empty (-1 terminated)
        s.poke_i32(sl + 200 * w, -1)
    # zombies: 3 slots, slot 1 dead
    zl = p.objects["zombie"]
    s.poke_u32(BOARD + p.board.zombies, ZOMBIES)
    s.poke_u32(BOARD + p.board.zombie_count_max, 3)
    for i, (dead, typ) in enumerate(((0, 0), (1, 2), (0, 4))):
        z = ZOMBIES + i * zl.stride
        s.poke(z + zl.dead, bytes([dead]))
        s.poke_u32(z + zl.type, typ)
        s.poke_u32(z + zl.hp, 270)
        s.poke_u32(z + zl.row, i)
    # plants: 2 slots, slot 1 dead
    pl = p.objects["plant"]
    s.poke_u32(BOARD + p.board.plants, PLANTS)
    s.poke_u32(BOARD + p.board.plant_count_max, 2)
    s.poke_u32(PLANTS + pl.hp, 300)
    s.poke_u32(PLANTS + pl.type, 1)  # sunflower
    s.poke(PLANTS + pl.stride + pl.dead, b"\x01")
    # seed bank: 3 packets
    sb = SEEDS
    s.poke_u32(BOARD + p.board.seed_bank, SEEDS)  # pointer to the SeedBank object
    sbl, pk = p.objects["seed_bank"], p.objects["seed_packet"]
    s.poke_u32(sb + sbl.count, 3)
    for i in range(3):
        pkt = sb + sbl.packets + i * pk.stride
        s.poke_u32(pkt + pk.cd_total, 750)
        s.poke_u32(pkt + pk.cd_elapsed, 100 * i)
        s.poke_u32(pkt + pk.type, i)
    # patch sites in "off" state
    for pg in p.patches.values():
        for site in pg.sites:
            s.poke(site.va(p.image_base), site.off)
    return s, p


@pytest.fixture
def rw():
    s, p = make_session()
    return Game(s, p, write=True)


@pytest.fixture
def ro():
    s, p = make_session(writable=False)
    return Game(s, p)


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------

def test_top_level_exports():
    import re, pathlib
    pyproject = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    m = re.search(r'^version = "([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
    assert pvztools.__version__ == m.group(1)
    assert callable(pvztools.attach) and callable(pvztools.scan_exe)
    assert pvztools.Game is Game
    assert pvztools.PermissionDenied is PermissionDenied
    assert pvztools.NotInLevel is NotInLevel
    from pvztools.process import GameNotRunning
    assert pvztools.GameNotRunning is GameNotRunning
    with pytest.raises(AttributeError):
        pvztools.nope


def test_import_is_light():
    # ``import pvztools`` must not drag in the Windows layers
    import subprocess
    code = "import sys, pvztools; print('pvztools.process' in sys.modules, 'pvztools.game' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**__import__('os').environ, "PYTHONPATH": "src"})
    assert out.stdout.split() == ["False", "False"], out


# ---------------------------------------------------------------------------
# attach
# ---------------------------------------------------------------------------

def test_attach_uses_process_attach_and_profile(monkeypatch):
    s, _p = make_session()
    seen = {}

    def fake_attach(pid, write=False):
        seen["pid"], seen["write"] = pid, write
        return s

    monkeypatch.setattr(game_mod, "_process_attach", fake_attach)
    g = attach(write=True)
    assert seen == {"pid": None, "write": True}
    assert g.version == "goty_1_2_0_1096_en"
    assert g.capability == "FULL"
    assert g.writable
    g.close()
    assert s.closed


def test_attach_game_not_running(monkeypatch):
    from pvztools.process import GameNotRunning

    def fake_attach(pid, write=False):
        raise GameNotRunning()

    monkeypatch.setattr(game_mod, "_process_attach", fake_attach)
    with pytest.raises(GameNotRunning, match="游戏没有运行"):
        pvztools.attach()


def test_attach_profile_by_id(monkeypatch):
    s, _p = make_session()
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    g = attach(profile="goty_1_2_0_1096_en")
    assert g.profile.id == "goty_1_2_0_1096_en"
    from pvztools.game import UnknownBuild
    with pytest.raises(UnknownBuild, match="no_such_profile_xyz"):
        attach(profile="no_such_profile_xyz")
    assert s.closed  # closed on failure


def test_attach_unknown_build_falls_back_to_resolver(monkeypatch):
    s, p = make_session()
    s.stamp = 0x12345678
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)

    class FakeRes:
        capability = "READ_ONLY"
        profile = p

    import pvztools.addr.resolve as R
    monkeypatch.setattr(R, "resolve", lambda target: FakeRes())
    g = attach()
    assert g.capability == "READ_ONLY"
    assert "READ_ONLY" in repr(g)


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

def test_read_properties(ro):
    g = ro
    assert g.ui == 3 and g.ui_name == "关卡中" and g.in_level
    assert g.sun == 75
    assert (g.wave, g.total_waves, g.clock, g.countdown) == (3, 20, 1234, 500)
    assert g.paused is False and g.scene == 2 and g.level == 7
    assert g.board.countdown_init == 600
    assert g.state.frame_duration == 10


def test_arrays_and_names(ro):
    zs = ro.zombies
    assert [z.index for z in zs] == [0, 2]
    assert zs[0].name == zs[0].name_zh == "普通僵尸"
    assert zs[1].name == "铁桶僵尸" and zs[1].row == 2
    ps = ro.plants
    assert len(ps) == 1 and ps[0].name == "向日葵" and ps[0].hp == 300
    seeds = ro.seeds
    assert [s.slot for s in seeds] == [0, 1, 2]
    assert seeds[0].name == "豌豆射手"
    spawn = ro.spawn
    assert spawn is not None
    assert list(spawn.wave(1)) == [0, 2] and list(spawn.wave(2)) == [0, 0, 4]
    text = ro.describe_spawn()
    assert "w1" in text and "w2" in text
    snap = ro.snapshot()
    assert len(snap.zombies) == 2


def test_not_in_level_message():
    s, p = make_session(ui=1, in_level=False)
    g = Game(s, p)
    assert not g.in_level and g.ui == 1
    with pytest.raises(NotInLevel) as ei:
        g.sun
    assert "请先进入关卡" in str(ei.value) and "主菜单" in str(ei.value)
    with pytest.raises(NotInLevel):
        g.zombies
    with pytest.raises(NotInLevel):
        g.spawn
    assert "主菜单" in repr(g)


# ---------------------------------------------------------------------------
# writes / permission
# ---------------------------------------------------------------------------

def test_read_only_game_refuses_writes(ro):
    with pytest.raises(PermissionDenied) as ei:
        ro.sun = 999
    msg = str(ei.value)
    assert "attach(write=True)" in msg and "Safety(allow_write=True)" in msg
    with pytest.raises(PermissionDenied):
        ro.cheats.no_cooldown = True
    with pytest.raises(PermissionDenied):
        ro.cheats.free_planting = True
    with pytest.raises(PermissionDenied) as ei:
        ro.cheats.sun_limit = True
    assert "allow_patch" in str(ei.value)
    with pytest.raises(PermissionDenied):
        ro.cheats.auto_collect = True
    with pytest.raises(PermissionDenied):
        ro.cheats.background = True
    with pytest.raises(PermissionDenied):
        ro.cheats.flags.dance = True
    with pytest.raises(PermissionDenied):
        ro.cheats.kill_zombies()
    assert ro.session.writes == []
    # reading cheat state is always allowed
    assert ro.cheats.sun_limit is False and ro.cheats.free_planting is False
    assert ro.cheats.no_cooldown is False
    assert ro.cheats.speed == 1.0


def test_write_flag_but_session_read_only():
    s, p = make_session(writable=False)
    g = Game(s, p, write=True)
    with pytest.raises(PermissionDenied, match="只读"):
        g.sun = 1


def test_set_sun_and_journal(rw):
    rw.sun = 999
    assert rw.sun == 999
    assert [e.what for e in rw.safety.journal] == ["write"]
    from pvztools.modifier import RangeError
    with pytest.raises(RangeError):
        rw.sun = 99999


def test_cheats_writes(rw):
    c = rw.cheats
    c.no_cooldown = True
    assert c.no_cooldown is True
    assert all(s.ready for s in rw.seeds)
    c.free_planting = True
    assert c.free_planting is True
    c.free_planting = False
    assert c.free_planting is False
    c.speed = 2
    assert c.frame_duration == 5 and c.speed == 2.0
    c.frame_duration = 10
    assert c.speed == 1.0
    with pytest.raises(ValueError):
        c.speed = 0
    assert c.kill_zombies() == 2
    # both live slots (0 and 2) now carry the dying status; the dead slot untouched
    zl, zombies = rw.profile.objects["zombie"], rw.session.read_u32(BOARD + rw.profile.board.zombies)
    from pvztools.modifier import ZOMBIE_STATUS_DYING
    assert [rw.session.read_u32(zombies + i * zl.stride + zl.status) for i in range(3)] ==         [ZOMBIE_STATUS_DYING, 0, ZOMBIE_STATUS_DYING]
    c.flags.dance = True
    assert c.flags.dance is True and c.flags.as_dict()["mustache"] is False
    with pytest.raises(AttributeError):
        c.flags.bogus = True
    st = c.status()
    assert st["no_cooldown"] is True and st["sun_limit"] is False


def test_patches_enable_and_restore_on_close(rw):
    c = rw.cheats
    c.sun_limit = True
    assert c.sun_limit is True
    assert rw.safety.sun_limit_unlocked
    rw.sun = 99999  # cap lifted
    c.auto_collect = True
    c.background = True
    assert c.auto_collect and c.background
    c.background = False
    assert not c.background
    rw.close()
    assert rw.session.closed
    # patches restored
    s, p = rw.session, rw.profile
    for name in ("sun_cap_unlock", "auto_collect"):
        for site in p.patches[name].sites:
            assert s.read(site.va(p.image_base), len(site.off)) == bytes(site.off)


def test_keep_patches(rw):
    rw.cheats.auto_collect = True
    rw.keep_patches()
    rw.close()
    s, p = rw.session, rw.profile
    site = p.patches["auto_collect"].sites[0]
    assert s.read(site.va(p.image_base), len(site.on)) == bytes(site.on)


def test_context_manager_and_doctor(monkeypatch):
    s, p = make_session()

    class FakeRes:
        capability = "FULL"
        profile = p

    import pvztools.addr.resolve as R
    monkeypatch.setattr(R, "resolve", lambda target: FakeRes())
    monkeypatch.setattr(R, "doctor_report", lambda res: "doctor %s" % res.capability)
    with Game(s, p) as g:
        assert g.doctor() == "doctor FULL"
        assert isinstance(g.cheats, GameCheats) and isinstance(g.cheats.flags, CheatFlags)
    assert s.closed and g.closed


def test_write_only_no_patch():
    s, p = make_session()
    g = Game(s, p, write=True, patch=False)
    g.sun = 10
    with pytest.raises(PermissionDenied):
        g.cheats.sun_limit = True


# ---------------------------------------------------------------------------
# live (read-only; sun write + revert only)
# ---------------------------------------------------------------------------

#: Live *write* tests (sun=999 then revert) only run with this opt-in: a
#: plain ``pytest`` while playing must never touch the game or open a
#: VM_WRITE handle.
LIVE_WRITE = os.environ.get("PVZTOOLS_LIVE_WRITE") == "1"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_live_facade():
    from pvztools.process import ProcessError, find_game_processes

    if not find_game_processes():
        pytest.skip("game not running")
    try:
        g = pvztools.attach(write=LIVE_WRITE)
    except ProcessError as exc:
        pytest.skip("cannot attach: %s" % exc)
    with g:
        print(repr(g))
        assert g.capability in ("FULL", "READ_WRITE", "READ_ONLY")
        if not g.in_level or g.ui not in (2, 3):
            pytest.skip("not in a level (ui=%d)" % g.ui)
        before = g.sun
        assert isinstance(before, int)
        if LIVE_WRITE:
            g.sun = 999
            try:
                assert g.sun == 999
            finally:
                g.sun = before
            assert g.sun == before
        else:
            with pytest.raises(PermissionDenied):
                g.sun = 999
        zs = g.zombies
        for z in zs:
            assert isinstance(z.type, int)
        print("live: sun %d wave %d/%d zombies %d plants %d seeds %d" %
              (before, g.wave, g.total_waves, len(zs), len(g.plants), len(g.seeds)))
        for z in zs[:3]:
            print("  ", z.row, z.name, z.hp)
