"""Tests for pvztools.modifier (safety / writes / patches)."""
from __future__ import annotations

import os
import struct
import sys

import pytest

from pvztools.addr import profile_for_stamp
from pvztools.modifier import (
    GameStateError,
    PatchError,
    PatchManager,
    PermissionDenied,
    RangeError,
    Safety,
    WhitelistError,
    WriteError,
    Writer,
    ZOMBIE_STATUS_DYING,
    semantic_base,
)
from pvztools.modifier import patches as patches_mod

STAMP_1096 = 0x4D02B058
LAWN = 0x028F0000
BOARD = 0x1BA40000
ZOMBIES = 0x1C000000
PLANTS = 0x1D000000
SEEDS = 0x1E000000


class FakeSession:
    """Sparse dict-backed memory; mirrors the Session read/write surface."""

    def __init__(self, writable=True):
        self.mem = {}
        self._writable = writable
        self.closed = False
        self.writes = []

    @property
    def writable(self):
        return self._writable

    def poke(self, addr, data):
        for i, b in enumerate(bytes(data)):
            self.mem[addr + i] = b

    def poke_u32(self, addr, v):
        self.poke(addr, struct.pack("<I", v & 0xFFFFFFFF))

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


@pytest.fixture
def profile():
    return profile_for_stamp(STAMP_1096)


@pytest.fixture
def sess(profile):
    """Game in level (ui 3), sun 75, 2 zombies alive, 1 plant, 3 seed slots."""
    s = FakeSession()
    p = profile
    s.poke_u32(p.globals.lawn_app, LAWN)
    s.poke_u32(LAWN + p.lawn_app.board, BOARD)
    s.poke_u32(LAWN + p.lawn_app.game_ui, 3)
    s.poke_u32(LAWN + p.lawn_app.game_mode, 0)
    s.poke_u32(LAWN + p.lawn_app.frame_duration, 10)
    s.poke_u32(BOARD + p.board.sun, 75)
    s.poke_u32(BOARD + p.board.countdown, 500)
    s.poke_u32(BOARD + p.board.countdown_start, 600)
    # zombies: 3 slots, slot 1 dead
    zl = p.objects["zombie"]
    s.poke_u32(BOARD + p.board.zombies, ZOMBIES)
    s.poke_u32(BOARD + p.board.zombie_count_max, 3)
    for i, dead in enumerate((0, 1, 0)):
        z = ZOMBIES + i * zl.stride
        s.poke(z + zl.dead, bytes([dead]))
        s.poke_u32(z + zl.status, 0)
    # plants: 2 slots, slot 1 dead
    pl = p.objects["plant"]
    s.poke_u32(BOARD + p.board.plants, PLANTS)
    s.poke_u32(BOARD + p.board.plant_count_max, 2)
    s.poke_u32(PLANTS + pl.hp, 300)
    s.poke(PLANTS + pl.stride + pl.dead, b"\x01")
    # seed bank: 3 packets
    sb = SEEDS
    s.poke_u32(BOARD + p.board.seed_bank, SEEDS)  # board.seed_bank is a pointer
    sbl, pk = p.objects["seed_bank"], p.objects["seed_packet"]
    s.poke_u32(sb + sbl.count, 3)
    for i in range(3):
        pkt = sb + sbl.packets + i * pk.stride
        s.poke_u32(pkt + pk.cd_total, 750)
        s.poke_u32(pkt + pk.cd_elapsed, 100 * i)
    # patch sites in "off" state
    for pg in p.patches.values():
        for site in pg.sites:
            s.poke(site.va(p.image_base), site.off)
    return s


def live_safety(**kw):
    base = dict(allow_write=True, allow_patch=True, dry_run=False)
    base.update(kw)
    return Safety(**base)


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

def test_safety_defaults_are_safe():
    s = Safety()
    assert s.dry_run and not s.allow_write and not s.allow_patch
    assert not s.allow_remote_call and not s.allow_savedata
    assert s.purity == "normal" and s.journal == []
    with pytest.raises(ValueError):
        Safety(purity="weird")


def test_semantic_base():
    assert semantic_base("objects.zombie[3].status") == "objects.zombie.status"
    assert semantic_base("board.cheats.dance") == "board.cheats"
    assert semantic_base("board.sun") == "board.sun"


def test_permission_denied_message_names_flag(profile):
    s = Safety()
    with pytest.raises(PermissionDenied) as ei:
        s.require_level("low", "写 board.sun")
    assert ei.value.flag == "allow_write"
    assert "allow_write=True" in str(ei.value)
    with pytest.raises(PermissionDenied) as ei:
        Safety(allow_write=True).require_level("high", "写 player_info.money")
    assert ei.value.flag == "allow_savedata"
    with pytest.raises(PermissionDenied) as ei:
        Safety(allow_write=True, purity="strict").require_level("low", "x")
    assert ei.value.flag == "purity"


def test_whitelist_from_profile(profile):
    s = Safety()
    with pytest.raises(WhitelistError):
        s.check_whitelist("board.sun")  # not bound yet
    s.bind_profile(profile)
    assert s.check_whitelist("board.sun") == "board.sun"
    assert s.check_whitelist("objects.zombie[7].status") == "objects.zombie.status"
    assert s.check_whitelist("board.cheats.dance") == "board.cheats"
    for bad in ("board.nope", "raw.0x731c50", "objects.zombie.nonexistent", "0x5578"):
        with pytest.raises(WhitelistError):
            s.check_whitelist(bad)


def test_range_guard_sun_cap_and_unlock():
    s = Safety()
    s.check_range("board.sun", 9990)
    with pytest.raises(RangeError) as ei:
        s.check_range("board.sun", 9991)
    assert ei.value.hi == 9990
    with pytest.raises(RangeError):
        s.check_range("board.sun", -1)
    s.sun_limit_unlocked = True
    s.check_range("board.sun", 50000)
    # generic table / width fallbacks
    with pytest.raises(RangeError):
        s.check_range("lawn_app.frame_duration", 0)
    with pytest.raises(RangeError):
        s.check_range("board.paused", 2)
    s.check_range("board.projectiles", 0x7FFFFFFF)
    with pytest.raises(RangeError):
        s.check_range("board.projectiles", 0x80000000)
    with pytest.raises(RangeError):
        s.check_range("board.sun", "100")


def test_require_ui_and_mode(profile, sess):
    s = Safety()
    assert s.require_ui(sess, profile, (2, 3)) == 3
    with pytest.raises(GameStateError):
        s.require_ui(sess, profile, (2,), "x")
    assert s.require_mode(sess, profile, [(0, 0)]) == 0
    with pytest.raises(GameStateError):
        s.require_mode(sess, profile, [(11, 15)])
    sess.poke_u32(profile.globals.lawn_app, 0)
    with pytest.raises(GameStateError):
        s.require_ui(sess, profile)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def test_dry_run_journals_but_does_not_write(profile, sess):
    w = Writer(sess, profile, Safety(allow_write=True))  # dry_run default
    ch = w.set_sun(999)
    assert ch.old == 75 and ch.new == 999 and ch.dry_run
    assert int(ch) == 75
    assert sess.writes == []
    assert sess.read_u32(BOARD + profile.board.sun) == 75
    assert len(w.safety.journal) == 1
    e = w.safety.journal[0]
    assert e.what == "write" and e.where == "board.sun" and e.old == 75 and e.new == 999
    assert e.dry_run and e.addr == BOARD + profile.board.sun


def test_set_sun_write_and_revert(profile, sess):
    w = Writer(sess, profile, live_safety())
    ch = w.set_sun(999)
    assert sess.read_u32(BOARD + profile.board.sun) == 999
    assert ch.old == 75
    ch.revert()
    assert sess.read_u32(BOARD + profile.board.sun) == 75
    assert ch.reverted
    ch.revert()  # idempotent
    kinds = [e.what for e in w.safety.journal]
    assert kinds == ["write", "revert"]
    assert [e.dry_run for e in w.safety.journal] == [False, False]


def test_set_sun_refusals(profile, sess):
    # no flag
    with pytest.raises(PermissionDenied) as ei:
        Writer(sess, profile, Safety(dry_run=False)).set_sun(1)
    assert ei.value.flag == "allow_write"
    # strict purity
    with pytest.raises(PermissionDenied) as ei:
        Writer(sess, profile, live_safety(purity="strict")).set_sun(1)
    assert ei.value.flag == "purity"
    # range
    with pytest.raises(RangeError):
        Writer(sess, profile, live_safety()).set_sun(9991)
    # read-only session + real write
    ro = FakeSession(writable=False)
    ro.mem = dict(sess.mem)
    with pytest.raises(PermissionDenied) as ei:
        Writer(ro, profile, live_safety()).set_sun(1)
    assert ei.value.flag == "session"
    # but dry-run on a read-only session is fine
    Writer(ro, profile, Safety(allow_write=True)).set_sun(1)
    # ui gate
    sess.poke_u32(LAWN + profile.lawn_app.game_ui, 1)
    with pytest.raises(GameStateError):
        Writer(sess, profile, live_safety()).set_sun(1)
    # no board
    sess.poke_u32(LAWN + profile.lawn_app.game_ui, 3)
    sess.poke_u32(LAWN + profile.lawn_app.board, 0)
    with pytest.raises(GameStateError):
        Writer(sess, profile, live_safety()).set_sun(1)
    assert sess.writes == []


def test_sun_limit_unlock_via_patch_manager(profile, sess):
    safety = live_safety()
    w = Writer(sess, profile, safety)
    with pytest.raises(RangeError):
        w.set_sun(20000)
    pm = PatchManager(sess, profile, safety)
    pm.enable("unlock_sun_limit")
    assert safety.sun_limit_unlocked
    w.set_sun(20000)
    pm.disable("sun_cap_unlock")
    assert not safety.sun_limit_unlocked
    w.revert_all()
    assert sess.read_u32(BOARD + profile.board.sun) == 75


def test_set_board_field_and_whitelist(profile, sess):
    w = Writer(sess, profile, live_safety())
    ch = w.set_board_field("countdown", 42)
    assert ch.old == 500 and sess.read_u32(BOARD + profile.board.countdown) == 42
    with pytest.raises(WhitelistError):
        w.set_board_field("not_a_field", 1)
    with pytest.raises(RangeError):
        w.set_board_field("debug_mode", 9)
    ch = w.set_board_field("paused", 1)  # byte field
    assert ch.width == 1 and sess.read(BOARD + profile.board.paused, 4) == b"\x01\x00\x00\x00"
    # write of a non-int
    with pytest.raises(WriteError):
        w.set_board_field("countdown", 1.5)


def test_high_level_field_needs_savedata(profile, sess):
    w = Writer(sess, profile, live_safety())
    with pytest.raises(PermissionDenied) as ei:
        w.set_lawn_field("game_mode", 11)
    assert ei.value.flag == "allow_savedata"
    w2 = Writer(sess, profile, live_safety(allow_savedata=True))
    w2.set_lawn_field("game_mode", 11)
    assert sess.read_u32(LAWN + profile.lawn_app.game_mode) == 11


def test_game_speed_no_ui_gate(profile, sess):
    sess.poke_u32(LAWN + profile.lawn_app.game_ui, 1)
    w = Writer(sess, profile, live_safety())
    ch = w.set_game_speed(5)
    assert ch.old == 10 and sess.read_u32(LAWN + profile.lawn_app.frame_duration) == 5
    with pytest.raises(RangeError):
        w.set_game_speed(0)
    ch.revert()
    assert sess.read_u32(LAWN + profile.lawn_app.frame_duration) == 10


def test_seed_cd_and_no_cooldown(profile, sess):
    p = profile
    w = Writer(sess, p, live_safety())
    sb = SEEDS
    pk = p.objects["seed_packet"]
    pkt1 = sb + p.objects["seed_bank"].packets + pk.stride
    ch = w.set_seed_cd(1)
    assert ch.old == 100 and sess.read_u32(pkt1 + pk.cd_elapsed) == 750
    assert ch.name == "objects.seed_packet[1].cd_elapsed"
    with pytest.raises(GameStateError):
        w.set_seed_cd(3)
    ch.revert()
    changes = w.no_cooldown(True)
    assert len(changes) == 3
    assert all(sess.read_u32(sb + p.objects["seed_bank"].packets + i * pk.stride + pk.cd_elapsed) == 750
               for i in range(3))
    back = w.no_cooldown(False)
    assert len(back) == 3
    assert sess.read_u32(pkt1 + pk.cd_elapsed) == 100


def test_set_plant_hp(profile, sess):
    p = profile
    w = Writer(sess, p, live_safety())
    ch = w.set_plant_hp(0, 4000)
    assert ch.old == 300 and sess.read_u32(PLANTS + p.objects["plant"].hp) == 4000
    with pytest.raises(GameStateError):
        w.set_plant_hp(1, 1)  # dead slot
    with pytest.raises(GameStateError):
        w.set_plant_hp(2, 1)  # out of range
    with pytest.raises(RangeError):
        w.set_plant_hp(0, -5)


def test_kill_all_zombies(profile, sess):
    p = profile
    zl = p.objects["zombie"]
    w = Writer(sess, p, live_safety())
    changes = w.kill_all_zombies()
    assert [c.name for c in changes] == ["objects.zombie[0].status", "objects.zombie[2].status"]
    assert sess.read_u32(ZOMBIES + zl.status) == ZOMBIE_STATUS_DYING
    assert sess.read_u32(ZOMBIES + zl.stride + zl.status) == 0  # dead slot untouched
    assert sess.read_u32(ZOMBIES + 2 * zl.stride + zl.status) == ZOMBIE_STATUS_DYING
    assert w.kill_all_zombies() == []  # already dying
    w.revert_all()
    assert sess.read_u32(ZOMBIES + zl.status) == 0
    # needs ui 3
    sess.poke_u32(LAWN + p.lawn_app.game_ui, 2)
    with pytest.raises(GameStateError):
        w.kill_all_zombies()


def test_freeze_countdown_and_free_planting(profile, sess):
    p = profile
    w = Writer(sess, p, live_safety())
    ch = w.freeze_countdown(True)
    assert ch.old == 500 and sess.read_u32(BOARD + p.board.countdown) == 999_999
    w.freeze_countdown(False)
    assert sess.read_u32(BOARD + p.board.countdown) == 500
    # off without a prior on -> countdown_start
    sess.poke_u32(BOARD + p.board.countdown, 3)
    w.freeze_countdown(False)
    assert sess.read_u32(BOARD + p.board.countdown) == 600
    ch = w.free_planting(True)
    assert ch.width == 1 and sess.read(LAWN + p.lawn_app.free_planting, 1) == b"\x01"
    w.free_planting(False)
    assert sess.read(LAWN + p.lawn_app.free_planting, 1) == b"\x00"


def test_cheat_flags(profile, sess):
    w = Writer(sess, profile, live_safety())
    addr = w.cheat_flag_addr("dance")
    assert addr == BOARD + 0x5779 + 4
    ch = w.set_cheat_flag("dance", True)
    assert ch.width == 1 and sess.read(addr, 1) == b"\x01" and ch.name == "board.cheats.dance"
    with pytest.raises(WhitelistError):
        w.set_cheat_flag("godmode", True)
    ch.revert()
    assert sess.read(addr, 1) == b"\x00"


def test_write_verifies_readback(profile, sess):
    class Flaky(FakeSession):
        def write(self, addr, data):
            self.writes.append((addr, bytes(data)))  # silently dropped

    f = Flaky()
    f.mem = dict(sess.mem)
    w = Writer(f, profile, live_safety())
    with pytest.raises(WriteError):
        w.set_sun(123)
    assert w.safety.journal[-1].new == 75


def test_revert_all_lifo(profile, sess):
    w = Writer(sess, profile, live_safety())
    w.set_sun(100)
    w.set_sun(200)
    w.set_board_field("countdown", 7)
    done = w.revert_all()
    assert [c.name for c in done] == ["board.countdown", "board.sun", "board.sun"]
    assert sess.read_u32(BOARD + profile.board.sun) == 75
    assert sess.read_u32(BOARD + profile.board.countdown) == 500
    assert w.revert_all() == []


# ---------------------------------------------------------------------------
# PatchManager
# ---------------------------------------------------------------------------

def test_patch_groups_from_profile(profile, sess):
    pm = PatchManager(sess, profile, Safety())
    for name in ("sun_cap_unlock", "auto_collect", "background_running", "pause_spawning"):
        assert name in pm.groups
    site = pm.groups["sun_cap_unlock"][0]
    assert site.addr == 0x400000 + 0x1F4E5 and site.on_bytes == b"\xeb" and site.off_bytes == b"\x7e"
    assert pm.resolve("unlock_sun_limit") == "sun_cap_unlock"
    assert pm.resolve("stop_spawning") == "pause_spawning"
    with pytest.raises(PatchError):
        pm.resolve("no_such_patch")
    st = pm.status()
    assert st["auto_collect"] == "off" and all(v == "off" for v in st.values())


def test_patch_enable_disable_and_journal(profile, sess):
    pm = PatchManager(sess, profile, live_safety())
    site = pm.groups["auto_collect"][0]
    written = pm.enable("auto_collect")
    assert [p.label for p in written] == ["auto_collect[0]"]
    assert sess.read(site.addr, site.size) == site.on_bytes
    assert pm.status()["auto_collect"] == "on" and pm.is_enabled("auto_collect")
    assert pm.enable("auto_collect") == []  # already on, no rewrite
    pm.disable("auto_collect")
    assert sess.read(site.addr, site.size) == site.off_bytes
    assert not pm.is_enabled("auto_collect")
    j = pm.safety.journal
    assert [(e.what, e.where) for e in j] == [("patch_on", "auto_collect[0]"), ("patch_off", "auto_collect[0]")]
    assert j[0].old == site.off_bytes.hex() and j[0].new == site.on_bytes.hex()


def test_patch_multi_site_group(profile, sess):
    pm = PatchManager(sess, profile, live_safety())
    name = next(n for n, g in pm.groups.items() if len(g) > 2 and pm.levels[n] != "high")
    written = pm.enable(name)
    assert len(written) == len(pm.groups[name])
    assert pm.status()[name] == "on"
    # flip one site back by hand -> "mixed"
    p0 = pm.groups[name][0]
    sess.poke(p0.addr, p0.off_bytes)
    assert pm.read_state(name) == "mixed"
    pm.disable(name)
    assert pm.status()[name] == "off"


def test_patch_refuses_unknown_bytes(profile, sess):
    pm = PatchManager(sess, profile, live_safety())
    sites = pm.groups["place_anywhere"]
    assert len(sites) >= 2
    sess.poke(sites[-1].addr, b"\x90" * sites[-1].size)  # corrupt the last site
    with pytest.raises(PatchError) as ei:
        pm.enable("place_anywhere")
    assert "拒绝" in str(ei.value)
    # nothing was written, not even the first (valid) site
    assert sess.read(sites[0].addr, sites[0].size) == sites[0].off_bytes
    assert sess.writes == []
    assert pm.status()["place_anywhere"] == "unknown"
    assert not pm.is_enabled("place_anywhere")


def test_patch_refusals(profile, sess):
    with pytest.raises(PermissionDenied) as ei:
        PatchManager(sess, profile, Safety(allow_write=True, dry_run=False)).enable("auto_collect")
    assert ei.value.flag == "allow_patch"
    strict = PatchManager(sess, profile, live_safety(purity="strict"))
    with pytest.raises(PermissionDenied) as ei:
        strict.enable("auto_collect")
    assert ei.value.flag == "purity"
    strict.enable("background_running")  # the one exception
    assert strict.status()["background_running"] == "on"
    strict.restore()
    assert strict.status()["background_running"] == "off"
    # high-level group
    pm = PatchManager(sess, profile, live_safety())
    assert pm.levels["userdata_readonly"] == "high"
    with pytest.raises(PermissionDenied) as ei:
        pm.enable("userdata_readonly")
    assert ei.value.flag == "allow_savedata"
    # internal group
    with pytest.raises(PatchError):
        pm.enable("main_loop_block")
    # read-only session
    ro = FakeSession(writable=False)
    ro.mem = dict(sess.mem)
    with pytest.raises(PermissionDenied) as ei:
        PatchManager(ro, profile, live_safety()).enable("auto_collect")
    assert ei.value.flag == "session"


def test_patch_dry_run(profile, sess):
    pm = PatchManager(sess, profile, Safety(allow_patch=True))
    site = pm.groups["auto_collect"][0]
    pm.enable("auto_collect")
    assert sess.writes == [] and sess.read(site.addr, 1) == site.off_bytes
    assert pm.status()["auto_collect"] == "dry" and pm.is_enabled("auto_collect")
    assert pm.safety.journal[-1].dry_run
    pm.disable("auto_collect")
    assert pm.status()["auto_collect"] == "off"


def test_patch_mutual_exclusion(profile, sess):
    pm = PatchManager(sess, profile, live_safety())
    assert "plant_fragile" in pm.excludes["plant_invincible"]
    pm.enable("plant_invincible")
    assert pm.status()["plant_invincible"] == "on"
    pm.enable("plant_fragile")  # switches invincible off first, then applies fragile
    st = pm.status()
    assert st["plant_fragile"] == "on"
    # the two groups share sites with different on-bytes, so while fragile is
    # on the invincible group reads as 'unknown'
    assert st["plant_invincible"] == "unknown"
    assert pm.enabled == {"plant_fragile"}
    # switching back: fragile is disabled first, then invincible verifies as off
    pm.enable("plant_invincible")
    st = pm.status()
    assert st["plant_invincible"] == "on" and st["plant_fragile"] == "unknown"
    assert pm.enabled == {"plant_invincible"}
    pm.restore()
    st = pm.status()
    assert st["plant_invincible"] == "off" and st["plant_fragile"] == "off"


def test_patch_context_manager_and_atexit_restore(profile, sess):
    safety = live_safety()
    with PatchManager(sess, profile, safety) as pm:
        pm.enable("auto_collect")
        pm.enable("sun_cap_unlock")
        assert safety.sun_limit_unlocked
    assert pm.status()["auto_collect"] == "off" and pm.status()["sun_cap_unlock"] == "off"
    assert pm.enabled == set() and not safety.sun_limit_unlocked
    # atexit path
    pm2 = PatchManager(sess, profile, safety)
    pm2.enable("pause_spawning")
    assert pm2.status()["pause_spawning"] == "on"
    patches_mod.restore_all()
    assert pm2.status()["pause_spawning"] == "off"
    assert patches_mod._atexit_registered


def test_patch_restore_keeps_group_tracked_when_off_write_fails(profile, sess):
    """A failed restore() must not drop the group from ``enabled``: the
    next restore() / restore_all() has to retry (main_loop_block at
    Game.close time)."""
    safety = live_safety()
    pm = PatchManager(sess, profile, safety)
    pm.enable("auto_collect")
    pm.enable("sun_cap_unlock")
    real_write = sess.write

    def flaky_write(addr, data):
        if flaky_write.fail:
            raise RuntimeError("WriteProcessMemory failed")
        real_write(addr, data)

    flaky_write.fail = True
    sess.write = flaky_write
    assert pm.restore() == []
    assert pm.enabled == {"auto_collect", "sun_cap_unlock"}
    assert pm.is_enabled("auto_collect") and safety.sun_limit_unlocked
    assert pm in patches_mod._managers            # atexit will still retry
    assert pm.status()["auto_collect"] == "on"
    # once writes work again the retry succeeds and the books are cleared
    flaky_write.fail = False
    assert pm.restore() == ["auto_collect", "sun_cap_unlock"]
    assert pm.enabled == set() and not safety.sun_limit_unlocked
    assert pm.status()["auto_collect"] == "off" and pm not in patches_mod._managers


# ---------------------------------------------------------------------------
# live (read/write sun only; never patches)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
@pytest.mark.skipif(os.environ.get("PVZTOOLS_LIVE_WRITE") != "1",
                    reason="live write test: set PVZTOOLS_LIVE_WRITE=1 to opt in")
def test_live_set_sun_and_revert():
    from pvztools.process import GameNotRunning, attach, find_game_processes, ProcessError

    if not find_game_processes():
        pytest.skip("game not running")
    try:
        s = attach(write=True)
    except (GameNotRunning, ProcessError) as exc:
        pytest.skip("cannot attach for write: %s" % exc)
    with s:
        p = profile_for_stamp(s.stamp)
        if p is None:
            pytest.skip("unknown build 0x%08x" % s.stamp)
        safety = Safety(allow_write=True, dry_run=False)
        lawn = s.read_ptr(p.globals.lawn_app)
        ui = s.read_i32(lawn + p.lawn_app.game_ui) if lawn else -1
        if ui not in (2, 3) or not s.read_ptr(lawn + p.lawn_app.board):
            pytest.skip("game not in a level (ui=%d)" % ui)
        w = Writer(s, p, safety)
        addr = w.board() + p.board.sun
        before = s.read_i32(addr)
        ch = w.set_sun(999)
        try:
            assert ch.old == before
            assert s.read_i32(addr) == 999
        finally:
            ch.revert()
        assert s.read_i32(addr) == before
        assert [e.what for e in safety.journal] == ["write", "revert"]
        print("live: ui=%d sun %d -> 999 -> %d at 0x%x" % (ui, before, s.read_i32(addr), addr))
