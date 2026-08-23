"""control.backends / control.input_backend: fake user32, fake Actions."""
from __future__ import annotations

import pytest

from pvztools.control import backends as B
from pvztools.control import input_backend as IB
from pvztools.control.grid import cob_target_pixels
from pvztools.model.types import Seed
from pvztools.modifier.safety import PermissionDenied, Safety

DOWN, UP = 0x0201, 0x0202


class FakeUser32:
    WM_LBUTTONDOWN = DOWN
    WM_LBUTTONUP = UP

    def __init__(self, hwnd=0x1234, client=None):
        self.hwnd = hwnd
        self.posted = []
        self.lookups = []
        self.client = client

    def find_game_window(self, pid=None):
        self.lookups.append(pid)
        return self.hwnd

    def post_message(self, hwnd, msg, wparam=0, lparam=0):
        self.posted.append((hwnd, msg, wparam, lparam & 0xFFFF, lparam >> 16))

    def get_client_rect(self, hwnd):
        return self.client or (800, 600)


class FakeBoard:
    def __init__(self, scene=0):
        self.scene = scene


class FakePlant:
    def __init__(self, index, row0, col0, ptype, state):
        self.index, self.row, self.col, self.type, self.state = index, row0, col0, ptype, state


class FakeObserver:
    def __init__(self, seeds=(), scene=0, plants=()):
        self._seeds, self._scene, self._plants = tuple(seeds), scene, tuple(plants)

    def seeds(self):
        return self._seeds

    def board(self):
        return FakeBoard(self._scene)

    def plants(self, include_dead=True):
        return self._plants


class FakeSession:
    pid = 4242


def seed(slot, ptype, usable=True, imit=-1, cd=(0, 750)):
    return Seed(slot=slot, type=ptype, imitater_type=imit, cd_elapsed=cd[0], cd_total=cd[1], usable=usable)


def make(seeds=(), scene=0, plants=(), allow=True, **kw):
    u = FakeUser32()
    sleeps = []
    be = IB.InputBackend(FakeSession(), FakeObserver(seeds, scene, plants), None,
                         Safety(allow_input=allow, dry_run=False), user32=u, sleep=sleeps.append, **kw)
    return be, u, sleeps


def clicks(u):
    """[(x, y), ...] from a DOWN/UP pair sequence, asserting the pairing."""
    out = []
    assert len(u.posted) % 2 == 0
    for i in range(0, len(u.posted), 2):
        d, up = u.posted[i], u.posted[i + 1]
        assert d[1] == DOWN and up[1] == UP and d[3:] == up[3:] and d[0] == up[0] == 0x1234
        out.append(d[3:])
    return out


# -- helpers ---------------------------------------------------------------
def test_make_lparam():
    assert IB.make_lparam(100, 70) == (70 << 16) | 100
    assert IB.make_lparam(0xFFFF + 1, 1) == (1 << 16)


def test_is_slot():
    assert B.is_slot(0) and B.is_slot(9)
    assert not B.is_slot(10) and not B.is_slot(True) and not B.is_slot("豌豆射手")


def test_precise_sleep_accuracy():
    import time
    t = time.perf_counter()
    IB.precise_sleep(0.005)
    dt = time.perf_counter() - t
    assert 0.005 <= dt < 0.05
    IB.precise_sleep(-1)  # no-op
    calls = []
    IB.precise_sleep(0.001, until=lambda: calls.append(1) or True)


def test_timer_resolution_noop_safe():
    with IB.timer_resolution(1):
        pass


# -- safety gate -------------------------------------------------------------
def test_refused_without_allow_input():
    be, u, _ = make(seeds=[seed(0, 0)], allow=False)
    with pytest.raises(PermissionDenied) as ei:
        be.click(10, 10)
    assert ei.value.flag == "allow_input"
    with pytest.raises(PermissionDenied):
        be.plant_card(0, 1, 1)
    with pytest.raises(PermissionDenied):
        be.shovel(1, 1)
    with pytest.raises(PermissionDenied):
        be.fire_cob((1, 1), 2, 9)
    assert u.posted == []
    be2 = IB.InputBackend(FakeSession(), FakeObserver(), None, None, user32=u)
    with pytest.raises(B.BackendError):
        be2.click(1, 1)


def test_safety_default_off():
    assert Safety().allow_input is False
    assert Safety(allow_input=True).allow_input is True


# -- window lookup ----------------------------------------------------------
def test_hwnd_lookup_by_pid_and_missing():
    be, u, _ = make()
    be.click(5, 6)
    assert u.lookups == [4242]
    u.hwnd = 0
    be2 = IB.InputBackend(FakeSession(), FakeObserver(), None, Safety(allow_input=True, dry_run=False), user32=u)
    with pytest.raises(B.BackendError):
        be2.click(1, 1)


# -- click / plant_card -----------------------------------------------------
def test_click_sequence_and_frame_delay():
    be, u, sleeps = make()
    be.click(120, 345)
    assert u.posted == [(0x1234, DOWN, 0, 120, 345), (0x1234, UP, 0, 120, 345)]
    assert sleeps == [0.01]
    assert be.history == [(DOWN, 120, 345), (UP, 120, 345)]


def test_plant_card_by_slot_and_by_plant():
    seeds = [seed(0, 0), seed(1, 3, usable=True), seed(2, 47)]
    be, u, sleeps = make(seeds=seeds, scene=0)
    s = be.plant_card("豌豆射手", 3, 4)
    assert s.slot == 0
    # slot 0 centre (50, 70); cell (3,4) on day: x = 3*80+40+40 = 320, y = 2*100+80+40 = 320
    assert clicks(u) == [(50, 70), (320, 320)]
    assert sleeps == [0.01, 0.01, 0.01]  # down/up, between clicks, down/up

    u.posted.clear()
    be.plant_card(2, 1, 1)  # slot index
    assert clicks(u) == [(150, 70), (80, 120)]

    u.posted.clear()
    be.plant_card("wallnut", 5, 9)
    assert clicks(u)[0] == (100, 70)


def test_plant_card_scene_geometry():
    seeds = [seed(0, 0)]
    be, u, _ = make(seeds=seeds, scene=2)  # pool: 85 px rows
    be.plant_card(0, 6, 1)
    assert clicks(u)[1] == (80, 5 * 85 + 80 + 40)
    be, u, _ = make(seeds=seeds, scene=4)  # roof: slope on cols 1..5
    be.plant_card(0, 1, 1)
    assert clicks(u)[1] == (80, 0 + 5 * 20 + 80 - 10 + 40)
    with pytest.raises(B.BackendError):
        be.plant_card(0, 6, 1)  # roof has 5 rows


def test_plant_card_imitater_fallback_and_missing():
    seeds = [seed(0, 48, imit=2), seed(1, 2)]
    be, u, _ = make(seeds=seeds)
    assert be.find_slot("樱桃炸弹").slot == 1  # plain card preferred
    be2, u2, _ = make(seeds=[seed(0, 48, imit=2)])
    assert be2.find_slot("cherry bomb").slot == 0
    with pytest.raises(B.BackendError):
        be.find_slot("玉米加农炮")
    with pytest.raises(B.BackendError):
        be.find_slot(5)
    with pytest.raises(B.BackendError):
        make(seeds=[seed(0, -1)])[0].find_slot(0)


def test_plant_card_refuses_unusable():
    be, u, _ = make(seeds=[seed(0, 0, usable=False, cd=(100, 750))])
    with pytest.raises(B.BackendError) as ei:
        be.plant_card("豌豆射手", 1, 1)
    assert "不可用" in str(ei.value)
    assert u.posted == []


# -- shovel ------------------------------------------------------------------
def test_shovel():
    be, u, sleeps = make(scene=0)
    assert be.shovel(2, 3) == (240, 220)
    assert clicks(u) == [IB.SHOVEL_XY, (240, 220)]
    assert len(sleeps) == 3


# -- cobs ---------------------------------------------------------------------
def test_fire_cob_sequence():
    cob = FakePlant(7, 2, 0, 47, 37)  # row 3, cols 1-2, ready
    be, u, sleeps = make(scene=2, plants=[cob])
    src, dst = be.fire_cob((3, 1), 2, 8.75)
    assert src == (80, 2 * 85 + 80 + 40)
    assert dst == cob_target_pixels(2, 2, 8.75)
    assert clicks(u) == [src, dst]
    assert sleeps == [0.01, 0.01, 0.01]


def test_fire_cob_misclick_guard_waits():
    cob = FakePlant(7, 0, 0, 47, 37)
    be, u, sleeps = make(scene=0, plants=[cob])
    src, dst = be.fire_cob((1, 1), 1, 1)
    assert abs(dst[0] - src[0]) < 100 and abs(dst[1] - src[1]) < 100
    assert sleeps[1] == pytest.approx(0.31)


def test_fire_cob_refusals():
    be, u, _ = make(scene=0, plants=[FakePlant(1, 0, 0, 47, 35)])
    with pytest.raises(B.BackendError):
        be.fire_cob((1, 1), 2, 9)  # not ready
    with pytest.raises(B.BackendError):
        be.fire_cob((2, 1), 2, 9)  # no cannon there
    assert u.posted == []


def test_fire_cobs_sequential():
    plants = [FakePlant(1, 0, 0, 47, 37), FakePlant(2, 1, 0, 47, 37)]
    be, u, _ = make(scene=0, plants=plants)
    res = be.fire_cobs([((1, 1), (2, 9)), ((2, 1), (4, 9))])
    assert len(res) == 2 and len(clicks(u)) == 4


# -- auto scale ----------------------------------------------------------------
def test_auto_scale():
    be, u, _ = make(auto_scale=True)
    u.client = (1600, 1200)
    be.click(100, 70)
    assert u.posted[0][3:] == (200, 140)
    assert be.history[0] == (DOWN, 100, 70)
    u.client = (0, 0)
    u.posted.clear()
    be.click(100, 70)
    assert u.posted[0][3:] == (100, 70)


# -- RemoteBackend ----------------------------------------------------------------
class FakeActions:
    def __init__(self, seeds=()):
        self.calls = []
        self.observer = FakeObserver(seeds)

    def put_plant(self, row, col, plant, imitater=False, **kw):
        self.calls.append(("put_plant", row, col, plant, imitater))
        return "planted"

    def remove_plant(self, row, col, layer="all"):
        self.calls.append(("remove_plant", row, col))
        return 1

    def fire_cob(self, cob, r, c, **kw):
        self.calls.append(("fire_cob", cob, r, c))
        return "fired"

    def fire_cobs(self, fires, **kw):
        self.calls.append(("fire_cobs", fires))
        return ["ok"] * len(fires)


def test_remote_backend_delegation():
    a = FakeActions(seeds=[seed(0, 0), seed(1, 48, imit=3), seed(2, -1)])
    rb = B.RemoteBackend(a)
    assert rb.mode == "remote" and isinstance(rb, B.ActionBackend)
    assert rb.plant_card("豌豆射手", 1, 2) == "planted"
    assert rb.plant_card(0, 3, 4) == "planted"
    assert rb.plant_card(1, 5, 6) == "planted"  # imitater slot
    assert rb.shovel(1, 2) == 1
    assert rb.fire_cob([1, 1], 2, 8.75) == "fired"
    assert rb.fire_cobs([((1, 1), (2, 9)), ((3, 1), (4, 8.5))]) == ["ok", "ok"]
    assert a.calls == [
        ("put_plant", 1, 2, 0, False),
        ("put_plant", 3, 4, 0, False),
        ("put_plant", 5, 6, 3, True),
        ("remove_plant", 1, 2),
        ("fire_cob", (1, 1), 2, 8.75),
        ("fire_cobs", [((1, 1), (2, 9.0)), ((3, 1), (4, 8.5))]),
    ]
    with pytest.raises(B.BackendError):
        rb.plant_card(2, 1, 1)  # empty slot
    with pytest.raises(B.BackendError):
        rb.plant_card(7, 1, 1)  # no such slot
    with pytest.raises(B.BackendError):
        rb.click(1, 1)


def test_input_backend_is_protocol_instance():
    be, _, _ = make()
    assert isinstance(be, B.ActionBackend) and be.mode == "input"
