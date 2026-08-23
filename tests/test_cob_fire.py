"""Cob cannon firing: grid formulas (source-map §6.4), the cob_fire
convention (§6.5 / §6.6), Actions.fire_cob / fire_cobs / cobs with a fake
session, the Game facade and the CLI.  No live game."""
from __future__ import annotations

import warnings

import pytest

import pvztools.schema.track  # noqa: F401  (import cycle: track before lineup)
from pvztools.cli.__main__ import EXIT_ERROR, EXIT_GAME, EXIT_OK, build_parser, main
from pvztools.control import grid
from pvztools.control.actions import (COB_ARMING, COB_FIRING, COB_LOADING, COB_READY, ActionError,
                                      CobInfo, FireResult)
from pvztools.control.conv import NotSupported, convention_for, describe
from pvztools.addr.profile import PROFILE_DIR, load_profile
from pvztools.game import Game, PermissionDenied
from pvztools.modifier.safety import Safety
from pvztools.control.remote import RemoteExecutor
from test_actions import BOARD, PLANTS, make_actions, make_world
from test_game_api import make_session

GOTY_VA = 0x46D1F0
RETAIL_VA = 0x466D50


# ---------------------------------------------------------------------------
# grid formulas
# ---------------------------------------------------------------------------

def test_grid_x():
    assert grid.grid_to_pixel_x(1) == 40
    assert grid.grid_to_pixel_x(9) == 680
    assert grid.grid_to_pixel_x(8.75) == 660
    assert grid.pixel_to_grid_x(39) is None
    assert grid.pixel_to_grid_x(40) == 1
    assert grid.pixel_to_grid_x(119) == 1 and grid.pixel_to_grid_x(120) == 2
    assert grid.pixel_to_grid_x(5000) == 9  # clamped


def test_grid_y_day_pool_roof():
    # day (DE): row*100 + 80
    assert grid.grid_to_pixel_y(0, 1, 1) == 80
    assert grid.grid_to_pixel_y(0, 5, 5) == 480
    assert grid.grid_to_pixel_y(1, 3, 3) == 280
    # pool / fog: row*85 + 80
    assert grid.grid_to_pixel_y(2, 1, 6) == 5 * 85 + 80 == 505
    assert grid.grid_to_pixel_y(3, 9, 1) == 80
    # roof: row*85 + slope + 70, slope = (5-col0)*20 for col0 < 5
    assert grid.grid_to_pixel_y(4, 1, 1) == 0 + 100 + 70
    assert grid.grid_to_pixel_y(4, 5, 1) == 0 + 20 + 70
    assert grid.grid_to_pixel_y(4, 6, 1) == 70
    assert grid.grid_to_pixel_y(5, 9, 5) == 4 * 85 + 70
    # high ground: -30
    assert grid.grid_to_pixel_y(4, 9, 2, high_ground=True) == 85 + 70 - 30


def test_pixel_to_grid_y():
    assert grid.pixel_to_grid_y(0, 100, 79) is None
    assert grid.pixel_to_grid_y(0, 10, 200) is None  # left of lawn
    assert grid.pixel_to_grid_y(0, 100, 80) == 1
    assert grid.pixel_to_grid_y(0, 100, 179) == 1 and grid.pixel_to_grid_y(0, 100, 180) == 2
    assert grid.pixel_to_grid_y(0, 100, 9999) == 5
    assert grid.pixel_to_grid_y(2, 100, 80 + 5 * 85) == 6
    assert grid.pixel_to_grid_y(2, 100, 9999) == 6
    # roof: y -= (4-col0)*20 for col0 < 5
    assert grid.pixel_to_grid_y(4, 40, 80 + 80) == 1          # col0=0: y-80 -> 80 -> row 1
    assert grid.pixel_to_grid_y(4, 40, 80 + 80 + 85) == 2
    assert grid.pixel_to_grid_y(4, 700, 80 + 85) == 2         # col0=8: no slope
    assert grid.pixel_to_grid_y(4, 700, 9999) == 5
    assert grid.pixel_to_grid_y_keep_on_board(4, 0, 50) == 1


@pytest.mark.parametrize("scene", [0, 1, 2, 3, 4, 5])
def test_grid_roundtrip(scene):
    for row in range(1, grid.rows_of_scene(scene) + 1):
        for col in range(1, 10):
            y = grid.grid_to_pixel_y(scene, col, row) + 40
            x = grid.grid_to_pixel_x(col) + 40
            assert grid.pixel_to_grid_x(x) == col
            assert grid.pixel_to_grid_y(scene, x, y) == row, (scene, row, col)


def test_cob_target_pixels():
    # centre = col*80 ; click x = centre + 7 (§6.1: explosion at x-7)
    assert grid.cob_target_pixels(0, 1, 1) == (87, 120)
    assert grid.cob_target_pixels(0, 3, 5) == (407, 320)
    assert grid.cob_target_pixels(2, 6, 8.75) == (707, 5 * 85 + 80 + 40)
    x, y = grid.cob_target_pixels(4, 1, 1)
    assert x == 87
    # roof: landing column is derived from x-47 (kept on board -> 80 -> col 1)
    assert y == grid.grid_to_pixel_y(4, 1, 1) + 40 == 210
    x, y = grid.cob_target_pixels(4, 5, 9)
    assert (x, y) == (727, 4 * 85 + 70 + 40)
    # the game-side row derivation agrees for every target
    for scene in (0, 2, 4):
        for row in range(1, grid.rows_of_scene(scene) + 1):
            for col in (1, 2.5, 4, 5.25, 7.5, 8.75, 9):
                x, y = grid.cob_target_pixels(scene, row, col)
                assert grid.pixel_to_grid_y_keep_on_board(scene, x - 47, y) == row
                assert x - 7 == col * 80  # explosion centre on the 1/80 grid
    with pytest.raises(ValueError):
        grid.cob_target_pixels(0, 6, 1)
    with pytest.raises(ValueError):
        grid.cob_target_pixels(0, 1, 11)


# ---------------------------------------------------------------------------
# convention
# ---------------------------------------------------------------------------

def _conv(name):
    return convention_for(load_profile(PROFILE_DIR / name))


def test_cob_fire_convention_goty_and_retail():
    for fname, va in (("goty_1_2_0_1096_en.json", GOTY_VA), ("1_0_0_1051_en.json", RETAIL_VA)):
        frag = _conv(fname).cob_fire(0x12345678, 707, 545)
        assert frag.ops == [
            ("push_imm32", (545,)),
            ("push_imm32", (707,)),
            ("mov_reg_imm32", ("eax", 0x12345678)),
            ("call_abs", (va,)),
        ], fname
        assert ("add_esp", (8,)) not in frag.ops  # callee does ret 8
        assert ("ret", ()) not in frag.ops
    assert describe("cob_fire", "goty") == "goty cob_fire: push y; push x; EAX = cob_plant; call cob_fire; ret"
    assert describe("cob_fire", "retail") == "retail cob_fire: push y; push x; EAX = cob_plant; call cob_fire; ret"


def test_cob_fire_convention_unverified_families():
    for fname in ("goty_1_1_0_1056_zh.json", "goty_1_1_0_1056_ja.json",
                  "goty_1_1_0_1056_zh_2012_06.json", "beta_0_9_9_1029_en.json"):
        with pytest.raises(NotSupported) as ei:
            _conv(fname).cob_fire(1, 2, 3)
        assert "unverified" in ei.value.reason


def test_cob_fire_missing_address_emits_nothing_into_shared_builder():
    # goty_1_2_0_1073_en uses the goty convention but its profile lacks a
    # cob_fire VA: the lookup must fail BEFORE any push/mov is emitted so a
    # shared (batch) builder is never left with a dangling half sequence.
    conv = _conv("goty_1_2_0_1073_en.json")
    asm = conv.asm_factory()
    with pytest.raises(NotSupported):
        conv.cob_fire(0x1000, 707, 300, asm=asm)
    assert asm.link(0x10000000) == b""


def test_profiles_carry_cob_fire_and_state_countdown():
    g = load_profile(PROFILE_DIR / "goty_1_2_0_1096_en.json")
    r = load_profile(PROFILE_DIR / "1_0_0_1051_en.json")
    assert g.function_va("cob_fire") == GOTY_VA and r.function_va("cob_fire") == RETAIL_VA
    assert g.objects["plant"].state_countdown == 0x54 and r.objects["plant"].state_countdown == 0x54


# ---------------------------------------------------------------------------
# Actions.fire_cob with a fake session
# ---------------------------------------------------------------------------

def _add_cob(s, p, index, row0, col0, state, countdown=0):
    pl = p.objects["plant"]
    a = PLANTS + index * pl.stride
    s.poke(a + pl.dead, b"\x00")
    s.poke_u32(a + pl.row, row0)
    s.poke_u32(a + pl.col, col0)
    s.poke_u32(a + pl.type, 47)
    s.poke_u32(a + pl.state, state)
    s.poke_u32(a + pl.state_countdown, countdown)
    return a


class FiringExecutor:
    """Records runs; flips every plant whose VA was loaded into EAX to FIRING."""

    def __init__(self, session, profile):
        self.session, self.profile = session, profile
        self.calls = []

    def run(self, code, *, name="shellcode", extra_patches=(), **kw):
        self.calls.append((name, code))
        pl = self.profile.objects["plant"]
        for _, _, text in code._insns:
            if text.startswith("mov eax, "):
                self.session.poke_u32(int(text.split(", ")[1], 16) + pl.state, COB_FIRING)
        return 0


def make_cob_world(**safety_kw):
    a, s, p, ex, patches = make_actions(scene=2, **safety_kw)
    ex2 = FiringExecutor(s, p)
    a.executor = ex2
    # slot 2: ready cob at (3,1); slot 3: arming cob at (4,1); slot 4: loading at (5,3)
    _add_cob(s, p, 2, 2, 0, COB_READY)
    _add_cob(s, p, 3, 3, 0, COB_ARMING, countdown=1200)
    _add_cob(s, p, 4, 4, 2, COB_LOADING)
    return a, s, p, ex2


def test_cobs_listing():
    a, s, p, ex = make_cob_world()
    cobs = a.cobs()
    assert [(c.index, c.row, c.col, c.state) for c in cobs] == [
        (2, 3, 1, COB_READY), (3, 4, 1, COB_ARMING), (4, 5, 3, COB_LOADING)]
    assert cobs[0].ready and cobs[0].countdown == 0
    assert cobs[1].countdown == 1200 + 475 and cobs[1].state_name == "arming"
    assert cobs[2].countdown == 175
    assert str(cobs[1]) == "#3 (4,1) arming ~1675 cs"


def test_fire_cob_emits_push_y_push_x_call():
    a, s, p, ex = make_cob_world()
    res = a.fire_cob((3, 1), 2, 8.75)
    assert isinstance(res, FireResult)
    pl = p.objects["plant"]
    plant_va = PLANTS + 2 * pl.stride
    x, y = grid.cob_target_pixels(2, 2, 8.75)
    assert (res.plant_index, res.x, res.y) == (2, x, y) == (2, 707, 85 + 80 + 40)
    assert res.state_before == COB_READY and res.state_after == COB_FIRING and res.fired
    frag = a.history[-1]
    assert frag.ops == [
        ("push_imm32", (y,)),
        ("push_imm32", (x,)),
        ("mov_reg_imm32", ("eax", plant_va)),
        ("call_abs", (GOTY_VA,)),
    ]
    assert len(ex.calls) == 1 and ex.calls[0][0].startswith("发炮")
    # by index works too
    s.poke_u32(plant_va + pl.state, COB_READY)
    res2 = a.fire_cob(2, 1, 1)
    assert res2.plant_index == 2 and res2.x == 87


def test_fire_cob_refuses_unready():
    a, s, p, ex = make_cob_world()
    with pytest.raises(ActionError, match="未就绪"):
        a.fire_cob((4, 1), 2, 8)
    with pytest.raises(ActionError, match="未就绪"):
        a.fire_cob(4, 2, 8)
    assert ex.calls == []
    # check_ready=False warns and fires anyway
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        res = a.fire_cob((4, 1), 2, 8, check_ready=False)
    assert res.plant_index == 3 and len(ex.calls) == 1
    assert any("check_ready" in str(x.message) for x in w)


def test_fire_cob_bad_targets():
    a, s, p, ex = make_cob_world()
    with pytest.raises(ActionError, match="没有玉米加农炮"):
        a.fire_cob((1, 1), 2, 8)
    with pytest.raises(ActionError, match="右半格"):
        a.fire_cob((3, 2), 2, 8)
    with pytest.raises(ActionError, match="不是玉米加农炮"):
        a.fire_cob(0, 2, 8)  # slot 0 is a sunflower
    with pytest.raises(ActionError):
        a.fire_cob(1, 2, 8)  # dead slot
    with pytest.raises(ActionError, match="落点无效"):
        a.fire_cob((3, 1), 7, 8)  # pool has 6 rows
    assert ex.calls == []


def test_fire_cobs_batches_in_one_call():
    a, s, p, ex = make_cob_world()
    _add_cob(s, p, 5, 0, 5, COB_READY)
    pl = p.objects["plant"]
    results = a.fire_cobs([((3, 1), (2, 8.75)), (5, (4, 7.5))])
    assert len(ex.calls) == 1
    assert [r.plant_index for r in results] == [2, 5]
    assert all(r.fired for r in results)
    frag = a.history[-1]
    assert frag.calls() == [GOTY_VA, GOTY_VA]
    x1, y1 = grid.cob_target_pixels(2, 2, 8.75)
    x2, y2 = grid.cob_target_pixels(2, 4, 7.5)
    assert frag.pushes() == [y1, x1, y2, x2]
    assert frag.reg_loads() == [("eax", PLANTS + 2 * pl.stride), ("eax", PLANTS + 5 * pl.stride)]
    # one bad cannon -> nothing emitted
    s.poke_u32(PLANTS + 2 * pl.stride + pl.state, COB_READY)
    with pytest.raises(ActionError, match="未就绪"):
        a.fire_cobs([((3, 1), (2, 8)), ((4, 1), (2, 8))])
    assert len(ex.calls) == 1
    with pytest.raises(ActionError, match="两次"):
        a.fire_cobs([((3, 1), (2, 8)), (2, (2, 8))])
    assert a.fire_cobs([]) == []


def test_fire_cob_inside_batch():
    a, s, p, ex = make_cob_world()
    _add_cob(s, p, 5, 0, 5, COB_READY)
    with a.batch("双炮") as b:
        assert a.fire_cob((3, 1), 2, 8.75) is None
        assert a.fire_cob((1, 6), 4, 7.5) is None
    assert b.calls == 2 and len(ex.calls) == 1
    assert b.fragment.calls() == [GOTY_VA, GOTY_VA]


def test_fire_cob_gating_and_dry_run():
    a, s, p, ex = make_cob_world(allow_remote_call=False)
    with pytest.raises(PermissionDenied):
        a.fire_cob((3, 1), 2, 8)
    a, s, p, ex = make_cob_world(dry_run=True)
    res = a.fire_cob((3, 1), 2, 8)
    assert res.state_after is None and res.state_before == COB_READY


# ---------------------------------------------------------------------------
# Game facade
# ---------------------------------------------------------------------------

def test_game_cobs_and_fire(monkeypatch):
    s, p = make_world(scene=0)
    _add_cob(s, p, 2, 1, 3, COB_READY)
    g = Game(s, p, write=False)
    cobs = g.cobs
    assert len(cobs) == 1 and isinstance(cobs[0], CobInfo) and cobs[0].cell == (2, 4) if hasattr(cobs[0], "cell") \
        else (cobs[0].row, cobs[0].col) == (2, 4)
    with pytest.raises(PermissionDenied, match="allow_remote_call"):
        g.fire_cob((2, 4), 1, 8)
    g = Game(s, p, write=True, remote=True)
    g.safety.dry_run = False
    ex = FiringExecutor(s, p)
    monkeypatch.setattr(Game, "_executor", property(lambda self: ex))
    res = g.fire_cob((2, 4), 1, 8)
    assert res.fired and res.x == 647 and res.y == 120
    assert len(ex.calls) == 1


def test_dry_run_shellcode_through_real_executor():
    """Safety.dry_run returns the linked shellcode: the cob_fire sequence must
    be inside the wrapper (push y / push x / mov eax / call rel32)."""
    s, p = make_world(scene=2)
    _add_cob(s, p, 2, 2, 0, COB_READY)
    safety = Safety(allow_write=True, allow_patch=True, allow_remote_call=True, dry_run=True)
    from test_actions import FakePatches
    from pvztools.control.actions import Actions
    from pvztools.observe import Observer
    ex = RemoteExecutor(s, p, safety, FakePatches())
    a = Actions(s, p, safety, Observer(s, p), ex, ex.patches)
    res = a.fire_cob((3, 1), 2, 8.75)
    code = a.history[-1].asm
    linked = code.link(0)
    pl = p.objects["plant"]
    plant_va = PLANTS + 2 * pl.stride
    expect = (b"\x68" + (205).to_bytes(4, "little")      # push y (85+80+40)
              + b"\x68" + (707).to_bytes(4, "little")     # push x
              + b"\xb8" + plant_va.to_bytes(4, "little"))  # mov eax, plant
    assert expect in linked
    idx = linked.index(expect) + len(expect)
    assert linked[idx] == 0xE8  # call rel32 -> 0x46D1F0 (linked at base 0)
    rel = int.from_bytes(linked[idx + 1:idx + 5], "little", signed=True)
    assert idx + 5 + rel == GOTY_VA
    assert res.state_after is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_fire_parsing():
    ns = build_parser().parse_args(["fire", "3", "1", "2", "8.75", "--remote", "--yes"])
    assert (ns.cob_row, ns.cob_col, ns.row, ns.col) == (3, 1, 2, 8.75)
    assert ns.remote and ns.yes and not ns.force
    ns = build_parser().parse_args(["fire", "3", "1", "2", "8", "--force"])
    assert ns.force and ns.col == 8.0
    ns = build_parser().parse_args(["cobs"])
    assert ns.func.__name__ == "cmd_cobs"


def test_cli_fire_gates(capsys):
    assert main(["fire", "3", "1", "2", "8.75"]) == EXIT_ERROR
    assert "--remote" in capsys.readouterr().err
    assert main(["fire", "3", "1", "2", "8.75", "--remote"]) == EXIT_ERROR
    assert "--yes" in capsys.readouterr().err


def test_cli_cobs_and_fire_with_fake_game(monkeypatch, capsys):
    s, p = make_world(scene=2)
    _add_cob(s, p, 2, 2, 0, COB_READY)
    _add_cob(s, p, 3, 3, 0, COB_ARMING, countdown=100)
    ex = FiringExecutor(s, p)
    import pvztools.game as game_mod

    def fake_attach(pid=None, *, write=False, profile=None, patch=None, remote=False):
        g = Game(s, p, write=write, patch=patch, remote=remote)
        g.safety.dry_run = False
        monkeypatch.setattr(Game, "_executor", property(lambda self: ex))
        return g

    monkeypatch.setattr(game_mod, "attach", fake_attach)
    assert main(["cobs"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "cobs: 2" in out and "ready" in out and "575" in out
    assert main(["fire", "3", "1", "2", "8.75", "--remote", "--yes"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "已发炮 #2" in out and "707" in out and "37 -> 38" in out
    assert len(ex.calls) == 1
    # not ready now (38) -> refused, --force fires
    assert main(["fire", "3", "1", "2", "8.75", "--remote", "--yes"]) == EXIT_GAME
    assert "未就绪" in capsys.readouterr().err
    assert main(["fire", "3", "1", "2", "8.75", "--remote", "--yes", "--force"]) == EXIT_OK
    assert len(ex.calls) == 2
