"""Tests for pvztools.model / pvztools.observe (fake memory + optional live)."""
from __future__ import annotations

import struct
import sys
import time

import pytest

from pvztools.addr import profile_for_id
from pvztools.model import Board, Coin, GameState, Plant, Projectile, Seed, Snapshot, SpawnList, Zombie
from pvztools.observe import NotInLevel, Observer, decode_spawn_list, describe_spawn_list, invalid_reason
from pvztools.observe import spawn as spawn_mod
from pvztools.schema.enums import PlantType, ZombieType

PROFILE = profile_for_id("goty_1_2_0_1096_en")

LAWN_AT = 0x02000000
BOARD_AT = 0x1B000000
PLANTS_AT = 0x1C000000
ZOMBIES_AT = 0x1D000000
SEEDS_AT = 0x1E000000
PROJ_AT = 0x1F000000
COINS_AT = 0x20000000


# ---------------------------------------------------------------------------
# fake memory
# ---------------------------------------------------------------------------

class FakeMemory:
    """Sparse 32-bit address space backed by bytearrays; counts reads."""

    def __init__(self):
        self.regions = {}  # base -> bytearray
        self.reads = 0

    def alloc(self, base, size):
        self.regions[base] = bytearray(size)
        return base

    def _locate(self, addr, n):
        for base, buf in self.regions.items():
            if base <= addr and addr + n <= base + len(buf):
                return buf, addr - base
        raise OSError("unmapped read %#x+%d" % (addr, n))

    def read(self, addr, n):
        self.reads += 1
        buf, off = self._locate(addr, n)
        return bytes(buf[off:off + n])

    def put(self, addr, fmt, *values):
        buf, off = self._locate(addr, struct.calcsize(fmt))
        struct.pack_into(fmt, buf, off, *values)

    def put_i32(self, addr, v):
        self.put(addr, "<i", v)

    def put_u8(self, addr, v):
        self.put(addr, "<B", v)

    def put_f32(self, addr, v):
        self.put(addr, "<f", v)


class FakeSession:
    def __init__(self, mem):
        self.mem = mem
        self.stamp = PROFILE.stamp

    def read(self, addr, n):
        return self.mem.read(addr, n)


def build_world(p=PROFILE, *, in_level=True, sun=275, wave=7, total=20, clock=1234,
                paused=0, plants=(), zombies=(), seeds=(), spawn=None,
                projectiles=(), coins=()):
    """Lay out LawnApp / Board / arrays using only the profile's offsets.

    ``plants``: (row, col, type, hp, dead); ``zombies``: (row, type, x, hp, dead);
    ``seeds``: (type, imitater_type, cd_elapsed, cd_total, usable);
    ``projectiles``: (type, row, x, y, cob_target_x, cob_target_row, dead);
    ``coins``: (type, x, y, collecting, dead);
    ``spawn``: list of lists (per wave) or None (all -1) or "garbage".
    """
    mem = FakeMemory()
    la, bd = p.lawn_app, p.board
    mem.alloc(p.globals.lawn_app, 4)
    mem.put(p.globals.lawn_app, "<I", LAWN_AT)
    mem.alloc(LAWN_AT, 0x1000)
    mem.put(LAWN_AT + la.game_ui, "<i", 3 if in_level else 1)
    mem.put(LAWN_AT + la.game_mode, "<i", 13)
    mem.put(LAWN_AT + la.frame_duration, "<i", 10)
    if not in_level:
        mem.put(LAWN_AT + la.board, "<I", 0)
        return mem
    mem.put(LAWN_AT + la.board, "<I", BOARD_AT)

    mem.alloc(BOARD_AT, 0x6000)
    mem.put_i32(BOARD_AT + bd.sun, sun)
    mem.put_i32(BOARD_AT + bd.scene, 4)
    mem.put_i32(BOARD_AT + bd.adventure_level, 42)
    mem.put_u8(BOARD_AT + bd.paused, paused)
    mem.put_i32(BOARD_AT + bd.main_counter, clock)
    mem.put_i32(BOARD_AT + bd.global_counter, clock + 500)
    mem.put_i32(BOARD_AT + bd.wave, wave)
    mem.put_i32(BOARD_AT + bd.wave_total, total)
    mem.put_i32(BOARD_AT + bd.countdown, 1500)
    mem.put_i32(BOARD_AT + bd.countdown_start, 2500)
    mem.put_i32(BOARD_AT + bd.huge_wave_countdown, 750)
    mem.put_i32(BOARD_AT + bd.wave_hp_threshold, 341)
    for name, val in (("time_stop_counter", 120), ("board_rand_seed", 0x1234567),
                      ("total_spawned_waves", wave + 1), ("zombie_health_wave_start", 2700),
                      ("cob_cursor_delay", 9)):
        if bd.get(name) is not None:
            mem.put_i32(BOARD_AT + bd[name], val)
    if bd.get("level_complete") is not None:
        mem.put_u8(BOARD_AT + bd.level_complete, 1)
    if bd.get("ice_min_x") is not None:
        for r in range(6):
            mem.put_i32(BOARD_AT + bd.ice_min_x + 4 * r, 800 - 10 * r)

    pl = p.objects["plant"]
    mem.put(BOARD_AT + bd.plants, "<I", PLANTS_AT)
    mem.put_i32(BOARD_AT + bd.plant_count_max, len(plants))
    mem.alloc(PLANTS_AT, max(1, len(plants)) * pl.stride)
    for i, (row, col, typ, hp, dead) in enumerate(plants):
        b = PLANTS_AT + i * pl.stride
        mem.put_i32(b + pl.row, row)
        mem.put_i32(b + pl.col, col)
        mem.put_i32(b + pl.type, typ)
        mem.put_i32(b + pl.state, 37)
        mem.put_i32(b + pl.hp, hp)
        mem.put_i32(b + pl.max_hp, 300)
        mem.put_i32(b + pl.imitater_type, 48 if i == 1 else -1)
        mem.put_u8(b + pl.dead, dead)
        mem.put_u8(b + pl.asleep, 1 if i == 0 else 0)
        if pl.fields.get("state_countdown") is not None:
            mem.put_i32(b + pl.state_countdown, 3000 - i)
        if pl.fields.get("shooting_counter") is not None:
            mem.put_i32(b + pl.shooting_counter, 205)
            mem.put_i32(b + pl.target_x, 400 + i)
            mem.put_i32(b + pl.target_y, 300 + i)
            mem.put_i32(b + pl.disappear_countdown, 100)
            mem.put_i32(b + pl.do_special_countdown, 200)

    zl = p.objects["zombie"]
    mem.put(BOARD_AT + bd.zombies, "<I", ZOMBIES_AT)
    mem.put_i32(BOARD_AT + bd.zombie_count_max, len(zombies))
    mem.alloc(ZOMBIES_AT, max(1, len(zombies)) * zl.stride)
    for i, (row, typ, x, hp, dead) in enumerate(zombies):
        b = ZOMBIES_AT + i * zl.stride
        mem.put_i32(b + zl.row, row)
        mem.put_i32(b + zl.type, typ)
        mem.put_i32(b + zl.status, 6)
        mem.put_f32(b + zl.x, x)
        mem.put_f32(b + zl.y, 50.0 + row)
        mem.put_f32(b + zl.speed, 0.23)
        mem.put_i32(b + zl.hp, hp)
        mem.put_i32(b + zl.max_hp, 270)
        mem.put_i32(b + zl.helmet_hp, 370 if typ == 2 else 0)
        mem.put_i32(b + zl.shield_hp, 1100 if typ == 6 else 0)
        mem.put_u8(b + zl.dead, dead)
        if zl.fields.get("from_wave") is not None:
            mem.put_i32(b + zl.from_wave, -1 if i == 2 else wave)
            mem.put_i32(b + zl.chilled_counter, 1000 if i == 1 else 0)
            mem.put_i32(b + zl.buttered_counter, 400 if i == 0 else 0)
            mem.put_i32(b + zl.ice_trap_counter, 300 if i == 1 else 0)
            mem.put_u8(b + zl.mind_controlled, 1 if i == 0 else 0)
            mem.put_u8(b + zl.has_head, 1)
            mem.put_u8(b + zl.has_arm, 0 if i == 2 else 1)
            mem.put_u8(b + zl.has_object, 1 if typ == 6 else 0)
            mem.put_u8(b + zl.in_pool, 1 if row == 2 else 0)
            mem.put_i32(b + zl.helm_type, 1 if typ == 2 else 0)
            mem.put_i32(b + zl.helmet_max_hp, 370 if typ == 2 else 0)
            mem.put_i32(b + zl.shield_type, 2 if typ == 6 else 0)
            mem.put_i32(b + zl.shield_max_hp, 1100 if typ == 6 else 0)
            mem.put_i32(b + zl.target_col, 5)
            mem.put_i32(b + zl.target_row, row)
            mem.put_i32(b + zl.phase_counter, 77)
            mem.put_f32(b + zl.altitude, 12.5)

    pj = p.objects.get("projectile")
    if pj is not None and bd.get("projectiles") is not None and pj.fields.get("dead") is not None:
        mem.put(BOARD_AT + bd.projectiles, "<I", PROJ_AT)
        mem.put_i32(BOARD_AT + bd.projectile_count_max, len(projectiles))
        mem.alloc(PROJ_AT, max(1, len(projectiles)) * pj.stride)
        for i, (typ, row, x, y, ctx, ctr, dead) in enumerate(projectiles):
            b = PROJ_AT + i * pj.stride
            mem.put_i32(b + pj.type, typ)
            mem.put_i32(b + pj.row, row)
            mem.put_f32(b + pj.pos_x, x)
            mem.put_f32(b + pj.pos_y, y)
            mem.put_f32(b + pj.pos_z, 1.0)
            mem.put_f32(b + pj.vel_x, 3.3)
            mem.put_f32(b + pj.vel_y, -1.0)
            mem.put_f32(b + pj.vel_z, 0.5)
            mem.put_f32(b + pj.cob_target_x, ctx)
            mem.put_i32(b + pj.cob_target_row, ctr)
            mem.put_i32(b + pj.age, 10 + i)
            mem.put_i32(b + pj.motion_type, 1)
            mem.put_u8(b + pj.dead, dead)
    cl = p.objects.get("coin")
    if cl is not None and bd.get("coins") is not None and cl.fields.get("dead") is not None:
        mem.put(BOARD_AT + bd.coins, "<I", COINS_AT)
        mem.put_i32(BOARD_AT + bd.coin_count_max, len(coins))
        mem.alloc(COINS_AT, max(1, len(coins)) * cl.stride)
        for i, (typ, x, y, collecting, dead) in enumerate(coins):
            b = COINS_AT + i * cl.stride
            mem.put_i32(b + cl.type, typ)
            mem.put_f32(b + cl.pos_x, x)
            mem.put_f32(b + cl.pos_y, y)
            mem.put_u8(b + cl.is_being_collected, collecting)
            mem.put_i32(b + cl.coin_age, 50 * i)
            mem.put_i32(b + cl.motion, 2)
            mem.put_i32(b + cl.usable_seed_type, 14 if typ == 5 else -1)
            mem.put_u8(b + cl.dead, dead)

    sb, pk = p.objects["seed_bank"].fields, p.objects["seed_packet"]
    mem.put(BOARD_AT + bd.seed_bank, "<I", SEEDS_AT)
    mem.alloc(SEEDS_AT, sb.packets + 10 * pk.stride)
    mem.put_i32(SEEDS_AT + sb.count, len(seeds))
    for i, (typ, imit, el, tot, usable) in enumerate(seeds):
        b = SEEDS_AT + sb.packets + i * pk.stride
        mem.put_i32(b + pk.type, typ)
        mem.put_i32(b + pk.imitater_type, imit)
        mem.put_i32(b + pk.cd_elapsed, el)
        mem.put_i32(b + pk.cd_total, tot)
        mem.put_u8(b + pk.usable, usable)

    sl = BOARD_AT + bd.spawn_list
    if spawn == "garbage":
        for i in range(1000):
            mem.put_i32(sl + i * 4, 0x7F00 + i)
    else:
        for w in range(20):
            row = list(spawn[w]) if spawn and w < len(spawn) else []
            # the real game only writes the run + one -1 terminator; the rest of
            # the row keeps whatever was in memory (observed live on 1096)
            row += [-1] + [0x7F00 + w] * (49 - len(row))
            for k, v in enumerate(row[:50]):
                mem.put_i32(sl + (w * 50 + k) * 4, v)
    return mem


PLANTS = ((0, 0, PlantType.SUNFLOWER, 300, 0), (2, 3, PlantType.COB_CANNON, 250, 0), (4, 8, PlantType.WALL_NUT, 0, 1))
ZOMBIES = ((0, ZombieType.ZOMBIE, 800.0, 270, 0), (1, ZombieType.CONEHEAD, 700.5, 100, 0),
           (2, ZombieType.SCREEN_DOOR, 650.0, 270, 0), (3, ZombieType.GARGANTUAR, 0.0, 0, 1))
SEEDS = ((PlantType.PEASHOOTER, -1, 750, 750, 1), (PlantType.IMITATER, PlantType.CHERRY_BOMB, 100, 5000, 0))
SPAWN = [[0, 0, 2], [0, 2, 2, 6], [0] * 5] + [[0, 3]] * 6 + [[1] + [0] * 10 + [23]] + [[0]] * 10
PROJECTILES = ((0, 1, 300.0, 95.0, 0.0, -1, 0), (11, 2, 500.0, 40.0, 433.0, 3, 0), (0, 0, 0.0, 0.0, 0.0, 0, 1))
COINS = ((1, 120.0, 400.0, 0, 0), (5, 640.0, 300.0, 1, 0), (2, 0.0, 0.0, 0, 1))


@pytest.fixture
def world():
    return build_world(plants=PLANTS, zombies=ZOMBIES, seeds=SEEDS, spawn=SPAWN,
                       projectiles=PROJECTILES, coins=COINS)


@pytest.fixture
def obs(world):
    return Observer(FakeSession(world), PROFILE)


# ---------------------------------------------------------------------------
# observer on fake memory
# ---------------------------------------------------------------------------

def test_game_state(obs):
    g = obs.game()
    assert isinstance(g, GameState)
    assert g.in_level and g.board_ptr == BOARD_AT and g.lawn_ptr == LAWN_AT
    assert (g.ui, g.mode, g.frame_duration) == (3, 13, 10)
    assert g.version == PROFILE.id and g.capability == PROFILE.capability


def test_not_in_level():
    mem = build_world(in_level=False)
    o = Observer(FakeSession(mem), PROFILE)
    g = o.game()
    assert g.board_ptr == 0 and not g.in_level and g.ui == 1
    for fn in (o.board, o.plants, o.zombies, o.seeds, o.spawn_list, o.snapshot):
        with pytest.raises(NotInLevel) as ei:
            fn()
        assert "请先进入关卡" in str(ei.value)


def test_board_values(obs):
    b = obs.board()
    assert isinstance(b, Board)
    assert b.sun == 275 and b.scene == 4 and b.level == 42 and b.paused is False
    assert b.clock == 1234 and b.global_clock == 1734
    assert (b.wave, b.total_waves) == (7, 20)
    assert (b.countdown, b.countdown_init, b.huge_wave_countdown, b.hp_threshold) == (1500, 2500, 750, 341)
    assert b.wave_hp is None and b.cheats is None  # not in this profile
    assert b.is_flag_wave_next is False
    # source-map §4.1 fields
    assert (b.time_stop_counter, b.board_rand_seed, b.total_spawned_waves) == (120, 0x1234567, 8)
    assert b.zombie_health_wave_start == 2700 and b.level_complete is True and b.cob_cursor_delay == 9
    assert b.ice_min_x == (800, 790, 780, 770, 760, 750)
    with pytest.raises(Exception):
        b.sun = 1  # type: ignore[misc]  frozen


def test_paused_flag():
    mem = build_world(paused=1)
    assert Observer(FakeSession(mem), PROFILE).board().paused is True


def test_plants(obs):
    ps = obs.plants()
    assert len(ps) == 3 and all(isinstance(p, Plant) for p in ps)
    sf, cob, nut = ps
    assert (sf.row, sf.col, sf.type, sf.name_zh, sf.hp, sf.max_hp) == (0, 0, 1, "向日葵", 300, 300)
    assert sf.asleep and not sf.imitater and sf.cell == (1, 1)
    assert cob.name_zh == "玉米加农炮" and cob.state == 37 and cob.imitater
    assert nut.dead and not nut.alive and nut.plant_type is PlantType.WALL_NUT
    assert len(obs.plants(include_dead=False)) == 2
    # source-map §4.2 counters
    assert (cob.state_countdown, cob.shooting_counter, cob.target_x, cob.target_y) == (2999, 205, 401, 301)
    assert (sf.disappear_countdown, sf.do_special_countdown) == (100, 200)


def test_zombies(obs):
    zs = obs.zombies()
    assert len(zs) == 4 and all(isinstance(z, Zombie) for z in zs)
    z0, cone, door, garg = zs
    assert (z0.row, z0.type, z0.name_zh, z0.status) == (0, 0, "普通僵尸", 6)
    assert z0.x == 800.0 and abs(z0.speed - 0.23) < 1e-6 and z0.y == 50.0
    assert cone.helmet_hp == 370 and cone.total_hp == 470 and cone.zombie_type is ZombieType.CONEHEAD
    assert door.shield_hp == 1100
    assert garg.dead and garg.name_zh == "白眼巨人"
    assert len(obs.zombies(include_dead=False)) == 3
    # source-map §4.3 fields (community names are properties over them)
    assert z0.from_wave == 7 and z0.is_cutscene is False
    assert door.from_wave == -1 and door.is_cutscene is True
    assert (cone.chilled_counter, cone.ice_trap_counter, z0.buttered_counter) == (1000, 300, 400)
    assert cone.slowed_cd == 1000 and cone.frozen_cd == 300
    assert z0.mind_controlled is True and z0.hypnotized is True and cone.mind_controlled is False
    assert door.has_head is True and door.has_arm is False and door.has_object is True
    assert door.in_pool is True and door.in_water is True and z0.in_pool is False
    assert (cone.helm_type, cone.helmet_max_hp) == (1, 370)
    assert (door.shield_type, door.shield_max_hp) == (2, 1100)
    assert (z0.target_col, cone.target_row, z0.phase_counter) == (5, 1, 77)
    assert abs(z0.altitude - 12.5) < 1e-6


def test_projectiles(obs):
    ps = obs.projectiles()
    assert len(ps) == 3 and all(isinstance(p, Projectile) for p in ps)
    pea, cob, dead = ps
    assert pea.type == 0 and pea.row == 1 and pea.pos == (300.0, 95.0, 1.0) and pea.x == 300.0
    assert abs(pea.vel[0] - 3.3) < 1e-6 and pea.vel[1] == -1.0 and pea.vel[2] == 0.5
    assert pea.age == 10 and pea.motion_type == 1 and pea.alive
    assert cob.type == 11 and cob.cob_target_x == 433.0 and cob.cob_target_row == 3 and cob.y == 40.0
    assert dead.dead and not dead.alive
    assert len(obs.projectiles(include_dead=False)) == 2


def test_coins(obs):
    cs = obs.coins()
    assert len(cs) == 3 and all(isinstance(c, Coin) for c in cs)
    sun, packet, dead = cs
    assert sun.type == 1 and sun.pos == (120.0, 400.0) and sun.x == 120.0 and sun.y == 400.0
    assert sun.collecting is False and sun.age == 0 and sun.motion == 2 and sun.usable_seed_type == -1
    assert packet.type == 5 and packet.collecting is True and packet.age == 50 and packet.usable_seed_type == 14
    assert dead.dead and not dead.alive
    assert len(obs.coins(include_dead=False)) == 2


def test_projectiles_and_coins_on_1051_layout():
    p1051 = profile_for_id("1_0_0_1051_en")
    mem = build_world(p1051, projectiles=PROJECTILES, coins=COINS, zombies=ZOMBIES)
    o = Observer(FakeSession(mem), p1051)
    assert [p.type for p in o.projectiles(include_dead=False)] == [0, 11]
    assert o.projectiles()[1].cob_target_x == 433.0
    assert [c.type for c in o.coins()] == [1, 5, 2]
    assert o.zombies()[1].chilled_counter == 1000 and o.zombies()[2].is_cutscene
    assert o.board().board_rand_seed == 0x1234567


def test_projectiles_and_coins_absent_on_beta():
    beta = profile_for_id("beta_0_1_1_1014_en")
    mem = build_world(beta, zombies=ZOMBIES, plants=PLANTS)
    o = Observer(FakeSession(mem), beta)
    assert o.projectiles() == () and o.coins() == ()
    b = o.board()
    assert b.board_rand_seed is None and b.ice_min_x is None and b.level_complete is None
    z = o.zombies()[0]
    assert z.from_wave is None and z.is_cutscene is None and z.chilled_counter is None
    assert o.plants()[0].shooting_counter is None and o.plants()[0].target_x is None


def test_seeds(obs):
    ss = obs.seeds()
    assert len(ss) == 2 and all(isinstance(s, Seed) for s in ss)
    pea, imit = ss
    assert pea.type == 0 and pea.usable and pea.ready and pea.cd_remaining == 0 and pea.name_zh == "豌豆射手"
    assert Seed(0, 0, -1, 0, 750, True).cd_remaining == 0  # fresh packet at level start
    assert imit.type == 48 and imit.effective_type == PlantType.CHERRY_BOMB
    assert imit.name_zh == "模仿樱桃炸弹" and not imit.usable and imit.cd_remaining == 4900


def test_spawn_list(obs):
    obs.session.mem.reads = 0
    sl = obs.spawn_list()
    assert obs.session.mem.reads == 3  # lawn ptr + board ptr + board header (table included)
    assert isinstance(sl, SpawnList) and obs.last_spawn_note is None
    assert len(sl) == 20 and sl.wave(1) == (0, 0, 2) and sl.wave(2) == (0, 2, 2, 6)
    # rows beyond total_waves are stale leftovers from the previous level
    short = Observer(FakeSession(build_world(total=10, spawn=SPAWN)), PROFILE).spawn_list()
    assert len(short) == 10 and short.wave(10) == tuple(SPAWN[9])
    assert sl.per_wave_counts()[1] == {0: 1, 2: 2, 6: 1}
    assert sl.types_present() == (0, 1, 2, 3, 6, 23)
    assert sl.has(ZombieType.GARGANTUAR) and not sl.has(ZombieType.GIGA_GARGANTUAR)
    assert sl.total() == 3 + 4 + 5 + 12 + 12 + 10
    text = describe_spawn_list(sl)
    assert "w1 (3只): 普通x2 路障x1" in text
    assert "w10 (12只): 旗帜x1 普通x10 白眼巨人x1 <== 旗帜波" in text
    assert "w20 (1只): 普通x1 <== 旗帜波" in text
    assert obs.describe_spawn() == text


def test_spawn_list_empty_and_garbage():
    mem = build_world(spawn=None)
    o = Observer(FakeSession(mem), PROFILE)
    assert o.spawn_list() is None and "为空" in o.last_spawn_note
    mem = build_world(spawn="garbage")
    o = Observer(FakeSession(mem), PROFILE)
    assert o.spawn_list() is None and "教程" in o.last_spawn_note and "w1" in o.last_spawn_note
    assert o.describe_spawn().startswith("(无出怪表)")


def test_spawn_decode_helpers():
    raw = struct.pack("<1000i", *([-1] * 1000))
    assert decode_spawn_list(raw) is None and invalid_reason(raw)
    vals = [-1] * 1000
    vals[0], vals[1], vals[50] = 0, 2, 32
    sl = decode_spawn_list(struct.pack("<1000i", *vals))
    assert sl.wave(1) == (0, 2) and sl.wave(2) == (32,) and sl.non_empty_waves == 2
    assert spawn_mod.short_name(0) == "普通" and spawn_mod.short_name(25) == "僵王博士"
    assert spawn_mod.short_name(99) == "未知(99)"
    assert describe_spawn_list(None) == "(无出怪表)"
    with pytest.raises(ValueError):
        decode_spawn_list(b"\0" * 10)


def test_snapshot_bulk_reads(world):
    sess = FakeSession(world)
    o = Observer(sess, PROFILE)
    world.reads = 0
    snap = o.snapshot()
    assert isinstance(snap, Snapshot)
    # lawn ptr + lawn header + board header + plants + zombies + seed bank
    assert world.reads == 6
    assert snap.board.sun == 275 and snap.game.in_level
    assert len(snap.plants) == 3 and len(snap.zombies) == 4 and len(snap.seeds) == 2
    assert len(snap.alive_plants) == 2 and len(snap.alive_zombies) == 3


def test_empty_arrays():
    mem = build_world()
    o = Observer(FakeSession(mem), PROFILE)
    assert o.plants() == () and o.zombies() == () and o.seeds() == ()


def test_no_offsets_hardcoded_uses_profile(world):
    """Moving the profile's sun offset must move where the observer reads."""
    from pvztools.addr.profile import profile_from_dict
    import copy
    d = copy.deepcopy(PROFILE.raw)
    d["board"]["sun"] = "0x5000"
    p2 = profile_from_dict(d)
    world.put_i32(BOARD_AT + 0x5000, 4242)
    assert Observer(FakeSession(world), p2).board().sun == 4242


# ---------------------------------------------------------------------------
# live (read-only)
# ---------------------------------------------------------------------------

def _live():
    if sys.platform != "win32":
        pytest.skip("Windows only")
    from pvztools.process import attach, ProcessError
    from pvztools.addr import profile_for_stamp
    try:
        s = attach()
    except ProcessError as e:
        pytest.skip("no game: %s" % e)
    p = profile_for_stamp(s.stamp)
    if p is None:
        s.close()
        pytest.skip("unknown build %#x" % s.stamp)
    return s, p


def test_live_observe():
    s, p = _live()
    with s:
        o = Observer(s, p)
        g = o.game()
        assert g.ui in range(0, 10)
        if not g.in_level:
            with pytest.raises(NotInLevel):
                o.board()
            pytest.skip("game at menu (ui=%d)" % g.ui)
        b = o.board()
        assert 0 <= b.sun <= 9990
        assert 0 <= b.wave <= b.total_waves <= 50
        hdr = s.read(g.board_ptr, o._board_len)
        plant_n = struct.unpack_from("<i", hdr, p.board.plant_count_max)[0]
        zombie_n = struct.unpack_from("<i", hdr, p.board.zombie_count_max)[0]
        assert len(o.plants()) == plant_n
        assert len(o.zombies()) == zombie_n
        time.sleep(0.5)
        b2 = o.board()
        if not b.paused and g.ui == 3:
            assert b2.clock >= b.clock
            assert b2.clock > b.clock or b2.global_clock > b.global_clock
        snap = o.snapshot()
        assert snap.game.board_ptr == g.board_ptr
        for z in snap.alive_zombies:
            assert 0 <= z.row < 6 and -200.0 <= z.x <= 1200.0
