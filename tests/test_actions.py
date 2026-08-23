"""Tests for control.actions / lineup_place / spawn_ctl, the Game facade
remote API and the remote CLI commands -- fake session, fake executor, no
live game."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

import pvztools.schema.track  # noqa: F401  (import cycle: track before lineup)
from pvztools import game as game_mod
from pvztools.cli.__main__ import EXIT_ERROR, EXIT_GAME, EXIT_OK, main
from pvztools.control.actions import (Actions, ActionError, RemotePatchManager, resolve_plant,
                                      resolve_zombie)
from pvztools.control.asm import Asm
from pvztools.control.conv import NotSupported
from pvztools.control.lineup_place import PlaceSummary, SceneMismatch, load_lineup, place_lineup, place_named
from pvztools.control.spawn_ctl import ALLOWED_COUNT, CLEAR_INTS, SpawnControl
from pvztools.game import Game, PermissionDenied
from pvztools.modifier.patches import PatchError
from pvztools.modifier.safety import GameStateError, Safety
from pvztools.observe import Observer
from pvztools.schema.enums import PlantType
from test_game_api import BOARD, PLANTS, make_session

ROOT = Path(__file__).resolve().parents[1]
DB = str(ROOT / "docs" / "research" / "arraydesign-lineups.json")
CHALLENGE = 0x1F000000
CUTSCENE = 0x1F100000
GRID = 0x1F200000


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

def make_world(ui=3, scene=2, plant_slots=8):
    """Session with room for plants, a challenge / cutscene pointer, grid items."""
    s, p = make_session(ui=ui)
    s.poke_u32(BOARD + p.board.scene, scene)
    s.poke_u32(BOARD + p.board.challenge, CHALLENGE)
    s.poke_u32(BOARD + p.board.cutscene, CUTSCENE)
    s.poke_u32(BOARD + p.board.plant_count_max, plant_slots)
    s.poke_u32(BOARD + p.board.plant_next_index, 1)  # slot 0 = sunflower, slot 1 dead
    pl = p.objects["plant"]
    for i in range(1, plant_slots):
        s.poke(PLANTS + i * pl.stride + pl.dead, b"\x01")
    # grid items: a ladder at (0,1) and a grave at (2,3); slot 2 dead
    gl = p.objects["grid_item"]
    s.poke_u32(BOARD + p.board.grid_items, GRID)
    s.poke_u32(BOARD + p.board.grid_item_count_max, 3)
    for i, (t, r, c, dead) in enumerate(((3, 0, 1, 0), (1, 2, 3, 0), (3, 4, 4, 1))):
        g = GRID + i * gl.stride
        s.poke_u32(g + gl.type, t)
        s.poke_u32(g + gl.row, r)
        s.poke_u32(g + gl.col, c)
        s.poke(g + gl.dead, bytes([dead]))
    # allowed types: 0 and 2 on
    s.poke(BOARD + p.board.allowed_types, bytes([1, 0, 1] + [0] * 30))
    return s, p


class FakeExecutor:
    """Records every run(); ``simulate`` pokes plants so verification passes."""

    def __init__(self, session, profile, simulate=True):
        self.session, self.profile = session, profile
        self.calls = []  # (name, Asm)
        self.simulate = simulate
        self.patches_seen = []

    def run(self, code, *, name="shellcode", extra_patches=(), **kw):
        assert isinstance(code, Asm)
        self.calls.append((name, code))
        # mirror RemoteExecutor: required patches are toggled inside the window
        for p in extra_patches:
            self.patches.enable(p)
        if self.simulate:
            self._simulate(code)
        for p in reversed(tuple(extra_patches)):
            self.patches.disable(p)
        return 0

    def _simulate(self, code):
        """Replay the recorded text of the Asm to spot put_plant sequences."""
        s, p = self.session, self.profile
        pl = p.objects["plant"]
        va = p.functions["put_plant"].va(p.image_base)
        va_del = p.functions["delete_plant"].va(p.image_base)
        texts = [t for _, _, t in code._insns]
        # pattern: push type; mov eax,row; push col; mov ebp,board; push ebp; call put_plant
        for i, t in enumerate(texts):
            if t == "call 0x%X" % va_del:  # push addr; call delete_plant
                addr = int(texts[i - 1].split(" ")[1], 16)
                s.poke(addr + pl.dead, b"\x01")
            if t == "call 0x%X" % va:
                row = int(texts[i - 4].split(", ")[1], 16)
                col = int(texts[i - 3].split(" ")[1], 16)
                ptype = int(texts[i - 5].split(" ")[1], 16)
                if ptype == 48:  # imitater: real type pushed first
                    ptype = int(texts[i - 6].split(" ")[1], 16)
                idx = s.read_u32(BOARD + p.board.plant_next_index)
                a = PLANTS + idx * pl.stride
                s.poke(a + pl.dead, b"\x00")
                s.poke_u32(a + pl.row, row)
                s.poke_u32(a + pl.col, col)
                s.poke_u32(a + pl.type, ptype)
                s.poke_u32(BOARD + p.board.plant_next_index, idx + 1)


class FakePatches:
    def __init__(self):
        self.log = []

    def enable(self, name):
        self.log.append(("on", name))

    def disable(self, name):
        self.log.append(("off", name))


def make_actions(ui=3, scene=2, simulate=True, **safety_kw):
    s, p = make_world(ui=ui, scene=scene)
    kw = dict(allow_write=True, allow_patch=True, allow_remote_call=True, dry_run=False)
    kw.update(safety_kw)
    safety = Safety(**kw)
    ex = FakeExecutor(s, p, simulate=simulate)
    patches = FakePatches()
    ex.patches = patches
    a = Actions(s, p, safety, Observer(s, p), ex, patches)
    return a, s, p, ex, patches


def fn_va(p, name):
    return p.functions[name].va(p.image_base)


def calls_of(frag):
    return frag.calls()


# ---------------------------------------------------------------------------
# name resolution
# ---------------------------------------------------------------------------

def test_resolve_names():
    assert resolve_plant("豌豆射手") == 0
    assert resolve_plant("peashooter") == 0
    assert resolve_plant("Cob Cannon") == 47 and resolve_plant("炮") == 47
    assert resolve_plant(PlantType.ICE_SHROOM) == 14 and resolve_plant("14") == 14
    with pytest.raises(ActionError):
        resolve_plant("不存在的植物")
    with pytest.raises(ActionError):
        resolve_plant(49)
    assert resolve_zombie("红") == 32 and resolve_zombie("红眼巨人") == 32 and resolve_zombie("giga") == 32
    assert resolve_zombie(0) == 0 and resolve_zombie("普僵") == 0


# ---------------------------------------------------------------------------
# put_plant / remove_plant
# ---------------------------------------------------------------------------

def test_put_plant_one_remote_call_and_index():
    a, s, p, ex, patches = make_actions()
    idx = a.put_plant(3, 1, "豌豆射手")
    assert idx == 1
    assert len(ex.calls) == 1 and ex.calls[0][0].startswith("放植物")
    frag = a.history[-1]
    assert frag.calls() == [fn_va(p, "put_plant")]
    assert frag.pushes() == [-1, 0, 0, "ebp"]  # push -1; push type; (eax=row) push col; push ebp
    assert ("mov_reg_imm32", ("eax", 2)) in frag.ops  # row 3 -> 2
    assert ("mov_reg_imm32", ("ebp", BOARD)) in frag.ops
    assert ("ret", ()) not in frag.ops  # executor wrapper supplies the epilogue
    # the plant is now visible (simulated) at the predicted index
    pl = [x for x in Observer(s, p).plants(include_dead=False) if x.index == 1][0]
    assert (pl.row, pl.col, pl.type) == (2, 0, 0)
    assert patches.log == []  # put_plant needs no extra patch group


def test_put_plant_wake_and_grow():
    a, s, p, ex, _ = make_actions(scene=2)  # PE: day scene
    a.put_plant(1, 1, "寒冰菇")
    frag = a.history[-1]
    assert frag.calls() == [fn_va(p, "put_plant"), fn_va(p, "set_plant_sleeping")]
    i = frag.ops.index(("mov_reg_reg", ("edi", "eax")))  # GOTY: EDI = plant
    assert frag.ops[i - 1] == ("push_reg", ("eax",)) and frag.ops[i + 1] == ("push_imm32", (0,))
    assert frag.ops[-1] == ("pop_reg", ("eax",))
    a.put_plant(1, 2, "土豆雷")
    frag = a.history[-1]
    assert frag.calls() == [fn_va(p, "put_plant")]
    assert ("mov_regdisp_imm32", ("eax", 0x54, 1)) in frag.ops
    # night scene: no wake call
    a2, *_ = make_actions(scene=1)
    a2.put_plant(1, 1, "寒冰菇")
    assert a2.history[-1].calls() == [fn_va(p, "put_plant")]
    a2.put_plant(1, 1, "寒冰菇", wake=False)
    assert fn_va(p, "set_plant_sleeping") not in a2.history[-1].calls()
    # day scene with wake=False / grow=False: the bare Toolkit PutPlant sequence
    a3, *_ = make_actions(scene=2)
    a3.put_plant(1, 1, "土豆雷", wake=False, grow=False)
    assert a3.history[-1].calls() == [fn_va(p, "put_plant")]
    assert ("mov_regdisp_imm32", ("eax", 0x54, 1)) not in a3.history[-1].ops


def test_put_plant_imitater_and_cob_bounds():
    a, s, p, ex, _ = make_actions()
    a.put_plant(1, 1, "寒冰菇", imitater=True, wake=False)
    frag = a.history[-1]
    assert frag.pushes()[:2] == [14, 48]
    assert frag.calls() == [fn_va(p, "put_plant"), fn_va(p, "put_plant_imitater")]
    with pytest.raises(ActionError, match="两格"):
        a.put_plant(1, 9, "玉米加农炮")
    with pytest.raises(ActionError, match="行"):
        a.put_plant(7, 1, "豌豆射手")
    a3, *_ = make_actions(scene=0)
    with pytest.raises(ActionError, match="行 6"):
        a3.put_plant(6, 1, "豌豆射手")


def test_gating():
    a, s, p, ex, _ = make_actions(allow_remote_call=False)
    with pytest.raises(PermissionDenied, match="allow_remote_call"):
        a.put_plant(1, 1, 0)
    a, s, p, ex, _ = make_actions(allow_patch=False)
    with pytest.raises(PermissionDenied, match="allow_patch"):
        a.put_plant(1, 1, 0)
    a, s, p, ex, _ = make_actions(ui=1)
    with pytest.raises(GameStateError, match="game_ui"):
        a.put_plant(1, 1, 0)
    a, s, p, ex, _ = make_actions(purity="strict")
    with pytest.raises(PermissionDenied, match="purity"):
        a.put_plant(1, 1, 0)
    assert ex.calls == []


def test_remove_plant_layers():
    a, s, p, ex, _ = make_actions()
    pl = p.objects["plant"]
    # slot 0: sunflower at (0,0); add a pumpkin and a lily on the same cell
    for idx, t in ((1, 30), (2, 16)):
        addr = PLANTS + idx * pl.stride
        s.poke(addr + pl.dead, b"\x00")
        s.poke_u32(addr + pl.type, t)
    assert a.remove_plant(1, 1, "shell") == 1
    assert a.history[-1].pushes() == [PLANTS + 1 * pl.stride]
    assert a.history[-1].calls() == [fn_va(p, "delete_plant")]
    assert a.remove_plant(1, 1, "container") == 1
    assert a.history[-1].pushes() == [PLANTS + 2 * pl.stride]
    assert a.remove_plant(1, 1, "plant") == 1
    assert a.history[-1].pushes() == [PLANTS]
    assert a.remove_plant(1, 1) == 0  # nothing left: no call issued
    assert len(ex.calls) == 3
    with pytest.raises(ActionError):
        a.remove_plant(1, 1, "roof")


def test_remove_plant_index():
    a, s, p, ex, _ = make_actions()
    pl = p.objects["plant"]
    assert a.remove_plant_index(0) == 1
    assert a.history[-1].pushes() == [PLANTS]
    assert a.history[-1].calls() == [fn_va(p, "delete_plant")]
    with pytest.raises(ActionError):
        a.remove_plant_index(999)
    s.poke(PLANTS + 1 * pl.stride + pl.dead, b"\x01")
    with pytest.raises(ActionError):
        a.remove_plant_index(1)
    assert len(ex.calls) == 1


def test_clear_plants_and_grid_items():
    a, s, p, ex, _ = make_actions()
    assert a.clear_plants() == 1
    assert a.history[-1].calls() == [fn_va(p, "delete_plant")]
    items = a.grid_items()
    assert [(t, r, c) for (_, _, t, r, c) in items] == [(3, 0, 1), (1, 2, 3)]
    assert a.clear_grid_items(("ladder",)) == 1
    gl = p.objects["grid_item"]
    assert a.history[-1].reg_loads() == [("esi", GRID)]
    assert a.history[-1].calls() == [fn_va(p, "delete_grid_item")]
    assert a.clear_grid_items((1, 2, 3, 11)) == 2
    assert a.history[-1].reg_loads() == [("esi", GRID), ("esi", GRID + gl.stride)]
    with pytest.raises(ActionError):
        a.clear_grid_items(("unicorn",))


def test_zombie_ladder_grave():
    a, s, p, ex, patches = make_actions()
    a.put_zombie(2, 9, "红")
    frag = a.history[-1]
    assert frag.pushes() == [8, 32] and frag.reg_loads() == [("eax", 1), ("ecx", CHALLENGE)]
    assert frag.calls() == [fn_va(p, "put_zombie")]
    a.put_zombie(1, 1, 25)
    assert a.history[-1].calls() == [fn_va(p, "put_zombie_in_row")]
    a.put_ladder(1, 2)
    assert a.history[-1].reg_loads() == [("edi", 0), ("eax", BOARD)] and a.history[-1].pushes() == [1]
    a.put_grave(2, 3)
    assert a.history[-1].reg_loads() == [("ecx", CHALLENGE), ("edi", 1), ("ebx", 2)]
    a.set_sleeping(0, False)
    assert a.history[-1].reg_loads() == [("edi", PLANTS)] and a.history[-1].pushes() == [0]


def test_batch_is_one_call():
    a, s, p, ex, _ = make_actions()
    with a.batch("test") as b:
        assert a.put_plant(1, 1, "荷叶") is None
        assert a.put_plant(1, 1, "玉米加农炮", wake=False) is None
        a.put_ladder(1, 1)
    assert len(ex.calls) == 1 and ex.calls[0][0] == "test"
    assert b.calls == 3 and b.names[0].startswith("放植物")
    frag = a.history[-1]
    assert frag.calls() == [fn_va(p, "put_plant"), fn_va(p, "put_plant"), fn_va(p, "put_ladder")]
    # the executor got the very Asm the batch built
    assert ex.calls[0][1] is frag.asm
    assert len(ex.calls[0][1]) == len(frag.asm)
    # exception inside the batch: nothing is run
    with pytest.raises(ValueError):
        with a.batch("boom"):
            a.put_plant(1, 1, 0)
            raise ValueError("x")
    assert len(ex.calls) == 1
    with a.batch("empty"):
        pass
    assert len(ex.calls) == 1
    with a.batch("outer"):
        with pytest.raises(ActionError, match="嵌套"):
            with a.batch("inner"):
                pass


def test_required_patches_wrap_the_call():
    a, s, p, ex, patches = make_actions()
    frag = a.conv.refresh_spawn_preview(BOARD, CUTSCENE, terminate=False)
    a._run(frag, "preview")
    assert patches.log == [("on", "street_zombies_hack"), ("off", "street_zombies_hack")]


def test_remote_patch_manager_allows_main_loop_block_only_with_flag():
    s, p = make_world()
    pm = RemotePatchManager(s, p, Safety(allow_patch=True, allow_remote_call=True, dry_run=False))
    pm.enable("main_loop_block")
    assert pm.read_state("main_loop_block") == "on"
    pm.disable("main_loop_block")
    assert pm.read_state("main_loop_block") == "off"
    pm2 = RemotePatchManager(s, p, Safety(allow_patch=True, dry_run=False))
    with pytest.raises(PatchError, match="内部补丁"):
        pm2.enable("main_loop_block")
    pm3 = RemotePatchManager(s, p, Safety(allow_remote_call=True, dry_run=False))
    with pytest.raises(PermissionDenied, match="allow_patch"):
        pm3.enable("main_loop_block")


# ---------------------------------------------------------------------------
# lineup placement
# ---------------------------------------------------------------------------

def test_place_classic_four_cobs():
    a, s, p, ex, _ = make_actions(scene=2)
    lu = load_lineup("经典四炮", DB)
    assert lu.scene == "PE" and lu.name == "经典四炮"
    summary = place_lineup(a, lu, clear=True)
    assert isinstance(summary, PlaceSummary)
    assert summary.containers == 12 and summary.plants == 22 and summary.shells == 14
    assert summary.ladders == 10 and summary.graves == 0 and summary.addons == 0
    assert summary.cleared_plants == 1 and summary.cleared_items == 2
    # clear grid items, clear plants, then ONE batched injection
    assert [n for n, _ in ex.calls][:2] == ["清除场地物件 [1, 2, 3, 11]", "清除所有植物"]
    assert len(ex.calls) == 3 and summary.calls == 3
    frag = a.history[-1]
    calls = frag.calls()
    put, ladder, sleep = fn_va(p, "put_plant"), fn_va(p, "put_ladder"), fn_va(p, "set_plant_sleeping")
    assert calls.count(put) == 12 + 22 + 14
    assert calls.count(ladder) == 10 and calls[-10:] == [ladder] * 10
    # 6 gloom-shrooms are "awake" in the catalogue -> woken; the 3 ice
    # shrooms are marked asleep -> left alone
    assert calls.count(sleep) == 6 and summary.woken == 6
    # order: 12 lilies, then 22 plants (4 cobs among them), then 14 pumpkins
    # (10 of them imitater pumpkins: push type; push 48)
    seq = []
    for i, (op, args) in enumerate(frag.ops):
        if op == "call_abs" and args[0] == put:
            first, second = frag.ops[i - 6][1][0], frag.ops[i - 5][1][0]
            seq.append(first if second == 48 else second)
    assert len(seq) == 48
    assert seq[:12] == [16] * 12
    assert seq[12:34].count(47) == 4 and 30 not in seq[12:34] and 16 not in seq[12:34]
    assert seq[34:48] == [30] * 14
    imitater_pumpkins = sum(1 for i, (op, args) in enumerate(frag.ops)
                            if op == "call_abs" and args[0] == put and frag.ops[i - 5][1][0] == 48)
    assert imitater_pumpkins == 10
    assert summary.order == ["container"] * 12 + ["plant"] * 22 + ["shell"] * 14 + ["ladder"] * 10
    assert "远程调用" in str(summary)


def test_place_wakes_day_sleepers_and_skips():
    from pvztools.schema.lineup import Cell, Lineup

    a, s, p, ex, _ = make_actions(scene=0)
    lu = Lineup("DE", (Cell(1, 1, plant=14), Cell(1, 2, plant=9, asleep=True),
                       Cell(2, 2, plant=16), Cell(3, 3, props=("tomb",))), rake_row=2, name="x")
    summary = place_lineup(a, lu, clear=False)
    assert summary.plants == 2 and summary.woken == 1 and summary.graves == 1
    assert any("钉耙" in x for x in summary.skipped) and any("荷叶" in x for x in summary.skipped)
    frag = a.history[-1]
    assert frag.calls() == [fn_va(p, "put_plant"), fn_va(p, "set_plant_sleeping"),
                            fn_va(p, "put_plant"), fn_va(p, "put_grave")]
    assert ("mov_regdisp_imm32", ("eax", 0x54, 1)) in frag.ops  # sun-shroom grows
    assert len(ex.calls) == 1


def test_place_scene_mismatch_and_change_scene():
    a, s, p, ex, _ = make_actions(scene=0)
    lu = load_lineup("31", DB)  # 经典四炮 by index, PE
    with pytest.raises(SceneMismatch, match="PE"):
        place_lineup(a, lu)
    with pytest.raises(NotSupported):
        place_lineup(a, lu, change_scene=True)
    assert ex.calls == []


def test_place_lineup_prechecks_before_clearing():
    """Review: a build failure must not leave the field wiped."""
    from pvztools.schema.lineup import Cell, Lineup

    a, s, p, ex, _ = make_actions(scene=0)
    a.conv.fn.pop("put_ladder")
    lu = Lineup("DE", (Cell(1, 1, plant=0), Cell(2, 2, props=("ladder",))))
    with pytest.raises(NotSupported):
        place_lineup(a, lu, clear=True)
    assert ex.calls == []  # nothing cleared, nothing placed
    # bad plant id (hand-built cell): validate() rejects it before anything runs
    a2, s2, p2, ex2, _ = make_actions(scene=0)
    with pytest.raises(ActionError, match="不合法"):
        place_lineup(a2, Lineup("DE", (Cell(1, 1, plant=60),)), clear=True)
    assert ex2.calls == []
    # imitater 48 as a bare plant is rejected the same way
    with pytest.raises(ActionError, match="不合法"):
        place_lineup(a2, Lineup("DE", (Cell(1, 1, plant=48),)), clear=True)
    assert ex2.calls == []


def test_place_lineup_lenient_water_and_pot_diagnostics():
    """Review: 37 catalogue lineups (cattail on water, roof sun-shrooms
    without pots) must place unless strict=True."""
    from pvztools.schema.lineup import Cell, Lineup

    a, s, p, ex, _ = make_actions(scene=2)
    lu = Lineup("PE", (Cell(3, 4, plant=43), Cell(1, 1, plant=0)), name="猫")
    summary = place_lineup(a, lu, clear=False)
    assert summary.plants == 2 and len(ex.calls) == 1
    assert any("water" in w for w in summary.warnings) and "警告" in str(summary)
    assert summary.game_mode is not None
    with pytest.raises(ActionError, match="不合法"):
        place_lineup(a, lu, clear=False, strict=True)
    # the catalogue's 最简无炮 (cattail on water) now places
    assert place_named(a, "最简无炮", DB, clear=False).plants > 0
    # hard errors still abort: cob overlap / range
    with pytest.raises(ActionError, match="不合法"):
        place_lineup(a, Lineup("PE", (Cell(1, 9, plant=47),)), clear=False)


def test_load_lineup_reports_decode_error(tmp_path):
    bad = "A" * 24
    with pytest.raises(LookupError, match="无法解码"):
        load_lineup(bad, DB)
    with pytest.raises(LookupError, match="无法解码"):
        load_lineup(bad, str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        load_lineup("经典四炮", str(tmp_path / "missing.json"))


def test_resolve_plant_rejects_imitater_as_plant():
    for v in (48, "48", "模仿者", "imitater"):
        with pytest.raises(ActionError, match="imitater"):
            resolve_plant(v)
    a, *_ = make_actions()
    with pytest.raises(ActionError, match="imitater"):
        a.put_plant(1, 1, 48)


def test_shovel_on_cob_right_half():
    a, s, p, ex, _ = make_actions(scene=2)
    a.put_plant(1, 3, "玉米加农炮")
    assert a.remove_plant(1, 4) == 1  # right half maps back to the cob at (1,3)
    a.put_plant(1, 3, "玉米加农炮")
    assert a.remove_plant(1, 3) == 1


def test_load_lineup_forms():
    from pvztools.schema.lineup import encode_lineup_code

    lu = load_lineup("经典四炮", DB)
    code = encode_lineup_code(lu)
    assert load_lineup(code).cells == lu.cells
    assert load_lineup(lu) is lu
    raw = "0,2F 1 1 0 0 0,10 1 1 0 0 0"
    assert load_lineup(raw).cobs() == [(1, 1)]
    with pytest.raises(LookupError):
        load_lineup("绝对不存在的阵型", DB)
    with pytest.raises(LookupError, match="多个"):
        load_lineup("炮", DB)
    a, s, p, ex, _ = make_actions(scene=2)
    assert place_named(a, 31, DB, clear=False).plants == 22


# ---------------------------------------------------------------------------
# spawn control
# ---------------------------------------------------------------------------

def make_spawn(ui=3, **safety_kw):
    s, p = make_world(ui=ui)
    kw = dict(allow_write=True, allow_patch=True, allow_remote_call=True, dry_run=False)
    kw.update(safety_kw)
    ex = FakeExecutor(s, p, simulate=False)
    patches = FakePatches()
    ex.patches = patches
    sc = SpawnControl(s, p, Safety(**kw), Observer(s, p), ex, patches)
    return sc, s, p, ex, patches


def test_spawn_read_and_set_allowed():
    sc, s, p, ex, _ = make_spawn()
    assert sc.allowed() == (0, 2)
    assert sc.current().waves[0] == (0, 2)
    ids = sc.set_allowed(["普杆舞红"])
    assert ids == (0, 3, 8, 32)
    flags = s.read(BOARD + p.board.allowed_types, ALLOWED_COUNT)
    assert flags == bytes(1 if i in ids else 0 for i in range(33))
    assert sc.set_allowed([2, "32"]) == (0, 2, 32)  # normal zombie forced on
    assert sc.set_allowed([]) == (0,)


def test_spawn_reroll_clears_then_regenerates():
    sc, s, p, ex, patches = make_spawn()
    before_writes = len(s.writes)
    sl = sc.reroll(["红"])
    ws = s.writes[before_writes:]
    assert ws[0][0] == BOARD + p.board.allowed_types
    assert ws[1][0] == BOARD + p.board.spawn_list
    assert ws[1][1] == struct.pack("<%di" % CLEAR_INTS, *([-1] * CLEAR_INTS))
    assert [n for n, _ in ex.calls] == ["pick_zombie_waves"]
    assert patches.log == []
    # the fake did not regenerate -> decoded list is empty / None
    assert sl is None or sl.total() == 0
    # on the seed chooser the preview is refreshed under street_zombies_hack
    sc2, s2, p2, ex2, patches2 = make_spawn(ui=2)
    sc2.reroll()
    assert [n for n, _ in ex2.calls] == ["pick_zombie_waves", "refresh_spawn_preview"]
    assert patches2.log == [("on", "street_zombies_hack"), ("off", "street_zombies_hack")]


def test_spawn_gating():
    sc, s, p, ex, _ = make_spawn(allow_remote_call=False)
    with pytest.raises(PermissionDenied, match="allow_remote_call"):
        sc.reroll()
    sc, s, p, ex, _ = make_spawn(allow_write=False)
    with pytest.raises(PermissionDenied, match="allow_write"):
        sc.set_allowed([2])
    sc, s, p, ex, _ = make_spawn(ui=1)
    with pytest.raises(GameStateError):
        sc.set_allowed([2])


def test_spawn_stats_restores_original():
    sc, s, p, ex, _ = make_spawn()
    sl_off = BOARD + p.board.spawn_list

    def regen():
        # fake pick_zombie_waves: wave 1 = 0 0 32, wave 2 = 32 -1
        for i, v in enumerate((0, 0, 32, -1)):
            s.poke_i32(sl_off + 4 * i, v)
        for i, v in enumerate((32, -1)):
            s.poke_i32(sl_off + 200 + 4 * i, v)

    ex.run = lambda code, name="x", **kw: regen()
    orig_raw = sc.raw()
    orig_flags = sc.allowed_flags()
    st = sc.stats(3, ["红"])
    assert st.runs == 3 and st.allowed == (0, 32)
    assert st.per_wave[0] == {0: 6, 32: 3} and st.per_wave[1] == {32: 3}
    assert st.giga_probability()[:2] == [1.0, 1.0] and st.giga_probability()[2] == 0.0
    assert "红眼概率" in st.describe()
    assert sc.raw() == orig_raw and sc.allowed_flags() == orig_flags
    assert sc.current().waves[0] == (0, 2)
    with pytest.raises(ActionError):
        sc.stats(0)


def test_spawn_stats_restores_full_cleared_region_and_journals_old():
    """Review: clear_list wipes CLEAR_BYTES but only 4000 bytes were backed up."""
    from pvztools.control.spawn_ctl import CLEAR_BYTES
    sc, s, p, ex, _ = make_spawn()
    sl_off = BOARD + p.board.spawn_list
    marker = bytes(range(1, 17)) * (CLEAR_BYTES // 16)
    s.poke(sl_off, marker)  # non -1 bytes everywhere, incl. past dword 1000
    ex.run = lambda code, name="x", **kw: None
    sc.stats(2)
    assert s.read(sl_off, CLEAR_BYTES) == marker
    # journal captured the old bytes for the clear and the restore
    js = [j for j in sc.safety.journal if j.where == "board.spawn_list"]
    assert js and all(j.old is not None for j in js)


def test_spawn_stats_restore_steps_independent_and_preview_on_chooser():
    sc, s, p, ex, patches = make_spawn(ui=2)
    ex.run = lambda code, name="x", **kw: ex.calls.append((name, code))
    calls = []
    orig_write_raw = sc.write_raw

    def bad_write_raw(raw):
        calls.append("raw")
        raise GameStateError("left the level")

    sc.write_raw = bad_write_raw
    orig_flags = sc.allowed_flags()
    with pytest.raises(GameStateError):
        sc.stats(1, ["红"])
    # flags were still restored although the table restore failed first
    assert sc.allowed_flags() == orig_flags
    assert calls == ["raw"]
    # happy path on ui 2: preview refreshed after the restore
    sc.write_raw = orig_write_raw
    ex.calls.clear()
    sc.stats(1)
    assert [n for n, _ in ex.calls][-1] == "refresh_spawn_preview"


def test_spawn_stats_dry_run_short_circuits():
    sc, s, p, ex, _ = make_spawn(dry_run=True)
    before = list(s.writes)
    st = sc.stats(5, ["红"])
    assert st.runs == 0 and st.lists == [] and st.allowed == (0, 32)
    assert s.writes == before and ex.calls == []


def test_spawn_write_raw_length_checked():
    from pvztools.control.spawn_ctl import CLEAR_BYTES
    sc, s, p, ex, _ = make_spawn()
    with pytest.raises(ActionError, match="长度"):
        sc.write_raw(b"\xff" * (CLEAR_BYTES + 4))
    with pytest.raises(ActionError, match="长度"):
        sc.write_raw(b"\xff" * 3)
    with pytest.raises(ActionError, match="长度"):
        sc.write_raw(b"")
    sc.write_raw(b"\xff" * 8)  # small restores are fine
    assert s.read(BOARD + p.board.spawn_list, 8) == b"\xff" * 8


def test_spawn_history_keeps_fragments():
    sc, s, p, ex, _ = make_spawn()
    sc.regenerate()
    assert len(sc.history) == 1 and sc.history[0].name == "pick_zombie_waves"


# ---------------------------------------------------------------------------
# Game facade
# ---------------------------------------------------------------------------

def test_game_facade_requires_remote():
    s, p = make_world()
    g = Game(s, p, write=True)
    for call in (lambda: g.plant("豌豆射手", 3, 1), lambda: g.shovel(1, 1),
                 lambda: g.place_lineup("经典四炮"), lambda: g.spawn_reroll()):
        with pytest.raises(PermissionDenied) as ei:
            call()
        assert "allow_remote_call" in str(ei.value) and "remote=True" in str(ei.value)
    assert g.remote is False
    # spawn_allowed is a plain write
    assert g.spawn_allowed == (0, 2)
    g.spawn_allowed = "红"
    assert g.spawn_allowed == (0, 32)
    ro = Game(s, p)
    with pytest.raises(PermissionDenied, match="allow_write"):
        ro.spawn_allowed = [2]


def test_game_facade_remote_uses_remote_patch_manager(monkeypatch):
    s, p = make_world()
    g = Game(s, p, remote=True)
    assert g.remote and g.safety.allow_remote_call and g.safety.allow_patch and g.safety.allow_write
    assert not g.safety.dry_run
    assert isinstance(g._patches, RemotePatchManager)
    # plug a fake executor under the real Actions
    fake = FakeExecutor(s, p)
    g._executor_obj = fake
    idx = g.plant("豌豆射手", 3, 1)
    assert idx == 1 and len(fake.calls) == 1
    assert g.shovel(3, 1) == 1
    assert g.actions is g.actions and g.spawn_control is g.spawn_control


def test_attach_remote_flag(monkeypatch):
    s, p = make_world()
    seen = {}

    def fake_attach(pid, write=False):
        seen["write"] = write
        return s

    monkeypatch.setattr(game_mod, "_process_attach", fake_attach)
    g = game_mod.attach(remote=True)
    assert seen["write"] is True and g.remote and g.writable
    g2 = game_mod.attach()
    assert g2.remote is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_remote_game(monkeypatch):
    s, p = make_world()
    state = {"session": s, "profile": p, "executed": []}

    def fake_attach(pid, write=False):
        state["pid"], state["write"] = pid, write
        return s

    monkeypatch.setattr(game_mod, "_process_attach", fake_attach)
    from pvztools.control import remote as remote_mod

    def fake_execute(self, shell, size, timeout_ms, name):
        state["executed"].append(name)
        FakeExecutor(s, p)._simulate(shell)
        return 0

    monkeypatch.setattr(remote_mod.RemoteExecutor, "_execute", fake_execute)
    return state


def test_cli_remote_commands_need_flags(capsys):
    assert main(["plant", "豌豆射手", "3", "1"]) == EXIT_ERROR
    assert "--remote" in capsys.readouterr().err
    assert main(["plant", "豌豆射手", "3", "1", "--remote"]) == EXIT_ERROR
    assert "--yes" in capsys.readouterr().err
    assert main(["shovel", "3", "1", "--write", "--yes"]) == EXIT_ERROR
    assert main(["lineup", "place", "经典四炮"]) == EXIT_ERROR
    assert main(["spawn", "reroll"]) == EXIT_ERROR
    assert main(["spawn", "stats", "5", "--remote"]) == EXIT_ERROR
    assert main(["clear", "plants"]) == EXIT_ERROR
    assert main(["spawn", "set", "普杆舞红"]) == EXIT_ERROR
    assert "--write" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["clear", "zombies", "--remote", "--yes"])


def test_cli_plant_and_shovel(fake_remote_game, capsys):
    st = fake_remote_game
    assert main(["plant", "豌豆射手", "3", "1", "--remote", "--yes"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "已放置 豌豆射手 于 (3,1)" in out and "下标 1" in out
    assert st["write"] is True and st["executed"] == ["放植物 豌豆射手@(3,1)"]
    # main_loop_block was toggled around the injection and restored
    s, p = st["session"], st["profile"]
    site = p.patches["main_loop_block"].sites[0]
    assert s.read(site.va(p.image_base), len(site.off)) == bytes(site.off)
    assert main(["shovel", "3", "1", "--remote", "-y"]) == EXIT_OK
    assert "已铲除 (3,1) 的 1 株植物" in capsys.readouterr().out
    assert main(["shovel", "3", "1", "--remote", "-y"]) == EXIT_OK
    assert "没有植物" in capsys.readouterr().out
    assert main(["plant", "豌豆射手", "9", "1", "--remote", "--yes"]) == EXIT_GAME
    assert "行 9" in capsys.readouterr().err


def test_cli_lineup_place_and_clear(fake_remote_game, capsys):
    st = fake_remote_game
    assert main(["lineup", "place", "经典四炮", "--db", DB, "--remote", "--yes"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "布阵完成" in out and "容器 12" in out and "梯子 10" in out
    assert st["executed"][-1].startswith("布阵 经典四炮")
    assert main(["clear", "plants", "--remote", "--yes"]) == EXIT_OK
    assert "已清除植物" in capsys.readouterr().out
    assert main(["clear", "ladders", "--remote", "--yes"]) == EXIT_OK
    assert "类型 [3]" in capsys.readouterr().out


def test_cli_not_supported_is_an_error_line(fake_remote_game, capsys, monkeypatch):
    """Review: NotSupported / NotImplementedAction must not escape as tracebacks."""
    from pvztools.control import conv as conv_mod
    from pvztools.control import actions as actions_mod

    real = conv_mod.convention_for

    def no_ladder(profile, *a, **k):
        c = real(profile, *a, **k)
        c.fn.pop("put_ladder", None)
        return c

    monkeypatch.setattr(actions_mod, "convention_for", no_ladder)
    st = fake_remote_game
    assert main(["lineup", "place", "经典四炮", "--db", DB, "--remote", "--yes"]) == EXIT_GAME
    err = capsys.readouterr().err
    assert err.startswith("error:") and "put_ladder" in err
    assert st["executed"] == []  # field not cleared either


def test_cli_plant_echoes_resolved_label(fake_remote_game, capsys):
    assert main(["plant", "14", "1", "1", "--remote", "--yes"]) == EXIT_OK
    assert "已放置 寒冰菇 于 (1,1)" in capsys.readouterr().out
    assert main(["plant", "模仿者", "1", "2", "--remote", "--yes"]) == EXIT_GAME
    assert "imitater" in capsys.readouterr().err


def test_game_clear_facade(fake_remote_game):
    from pvztools.game import attach
    g = attach(remote=True)
    assert g.clear("ladders") == 1
    assert g.clear("plants") == 1
    with pytest.raises(ValueError):
        g.clear("zombies")


def test_cli_spawn_commands(fake_remote_game, capsys):
    st = fake_remote_game
    s, p = st["session"], st["profile"]
    assert main(["spawn", "set", "普杆舞红", "--write", "--yes"]) == EXIT_OK
    assert "出怪种类" in capsys.readouterr().out
    assert s.read(BOARD + p.board.allowed_types, 33)[32] == 1
    assert main(["spawn", "set", "2,32", "--write", "--yes"]) == EXIT_OK
    assert s.read(BOARD + p.board.allowed_types, 33)[3] == 0
    assert main(["spawn", "reroll", "红", "--remote", "--yes"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "已重新出怪" in out
    assert st["executed"][-1] == "pick_zombie_waves"
    assert main(["spawn", "stats", "2", "--remote", "--yes"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "已恢复" in out
    assert main(["spawn", "stats", "0", "--remote", "--yes"]) == EXIT_ERROR


def test_cli_not_in_level(monkeypatch, capsys):
    s, p = make_session(in_level=False)
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    assert main(["plant", "豌豆射手", "1", "1", "--remote", "--yes"]) == EXIT_GAME
    assert "请先进入关卡" in capsys.readouterr().err


def test_help_lists_remote_commands(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for name in ("plant", "shovel", "clear"):
        assert name in out
    with pytest.raises(SystemExit):
        main(["spawn", "--help"])
    out = capsys.readouterr().out
    assert "reroll" in out and "stats" in out and "set" in out
    with pytest.raises(SystemExit):
        main(["lineup", "--help"])
    assert "place" in capsys.readouterr().out
