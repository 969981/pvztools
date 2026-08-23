"""control.conv: per-family calling-convention generators.

The tests check the *operation sequence* (which register gets the board,
what is pushed in which order, which function is called) with a recording
stub builder, so they do not depend on ``control.asm`` being present or on
its byte encoding.
"""
import pytest

from pvztools.addr.profile import PROFILE_DIR, load_profile
from pvztools.control.conv import (CALL_NAMES, FAMILIES, NotSupported, convention_for, describe,
                                   describe_all)
from pvztools.control.conv.base import Fragment, dec_regdisp32, mov_reg_reg


class StubAsm:
    """Records every builder call; stands in for ``control.asm.Asm``."""

    def __init__(self):
        self.log = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _rec(*args):
            self.log.append((name, args))
        return _rec


GOTY = load_profile(PROFILE_DIR / "goty_1_2_0_1096_en.json")
RETAIL = load_profile(PROFILE_DIR / "1_0_0_1051_en.json")
ZH2010 = load_profile(PROFILE_DIR / "goty_1_1_0_1056_zh.json")
BETA = load_profile(PROFILE_DIR / "beta_0_9_9_1029_en.json")
ZH2012 = load_profile(PROFILE_DIR / "goty_1_1_0_1056_zh_2012_06.json")

BOARD = 0x1A2B3C40
CHALLENGE = 0x2B3C4D50
PLANT = 0x3C4D5E60


def conv(profile):
    return convention_for(profile, asm_factory=StubAsm)


def fn(profile, name):
    return profile.function_va(name)


# ---------------------------------------------------------------------------
# put_plant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile", [GOTY, RETAIL, ZH2010])
def test_put_plant_sequence_matches_doc(profile):
    frag = conv(profile).put_plant(BOARD, row=2, col=5, ptype=47)
    assert frag.ops == [
        ("push_imm32", (-1,)),
        ("push_imm32", (47,)),
        ("mov_reg_imm32", ("eax", 2)),
        ("push_imm32", (5,)),
        ("mov_reg_imm32", ("ebp", BOARD)),
        ("push_reg", ("ebp",)),
        ("call_abs", (fn(profile, "put_plant"),)),
    ]
    # default terminate=False (review finding: the executor's wrapper owns the ret)
    assert ("ret", ()) not in frag.ops
    assert conv(profile).put_plant(BOARD, 2, 5, 47, terminate=True).ops[-1] == ("ret", ())
    # the stub builder saw exactly the same sequence
    assert frag.asm.log == frag.ops
    assert frag.required_patches == ()


@pytest.mark.parametrize("profile", [GOTY, RETAIL])
def test_put_plant_imitater_path(profile):
    frag = conv(profile).put_plant(BOARD, 0, 1, 30, imitater=True)
    assert frag.pushes()[:2] == [30, 48]  # push type, push 48
    assert frag.calls() == [fn(profile, "put_plant"), fn(profile, "put_plant_imitater")]
    i = frag.ops.index(("call_abs", (fn(profile, "put_plant"),)))
    assert frag.ops[i + 1:i + 5] == [
        ("push_reg", ("eax",)),
        ("mov_reg_reg", ("esi", "eax")),
        ("call_abs", (fn(profile, "put_plant_imitater"),)),
        ("pop_reg", ("eax",)),
    ]
    # the stub only knows the raw builder op for mov esi, eax (8B F0)
    assert ("raw", (b"\x8b\xf0",)) in frag.asm.log


def test_put_plant_iz_mode_appends_iz_style():
    frag = conv(GOTY).put_plant(BOARD, 0, 0, 1, challenge=CHALLENGE)
    assert frag.calls() == [fn(GOTY, "put_plant"), fn(GOTY, "put_plant_iz_style")]
    assert ("mov_reg_imm32", ("eax", CHALLENGE)) in frag.ops
    assert any("unverified" in n for n in frag.notes)


def test_put_plant_no_terminate_and_batching():
    c = conv(GOTY)
    a = c.put_plant(BOARD, 0, 0, 1, terminate=False)
    b = c.put_plant(BOARD, 1, 1, 2, terminate=False)
    assert ("ret", ()) not in a.ops
    a.extend(b)
    assert a.calls() == [fn(GOTY, "put_plant")] * 2
    # extending with one generated on the same builder object also works
    c2 = c.put_plant(BOARD, 2, 2, 3, asm=a.asm, terminate=True)
    assert c2.asm is a.asm and a.asm.log[-1] == ("ret", ())


def test_beta_put_plant_uses_ecx():
    frag = conv(BETA).put_plant(BOARD, row=3, col=4, ptype=7)
    assert frag.ops[:6] == [
        ("push_imm32", (-1,)),
        ("push_imm32", (7,)),
        ("push_imm32", (3,)),
        ("push_imm32", (4,)),
        ("mov_reg_imm32", ("ecx", BOARD)),
        ("call_abs", (fn(BETA, "put_plant"),)),
    ]
    with pytest.raises(NotSupported):
        conv(BETA).put_plant(BOARD, 0, 0, 7, imitater=True)


def test_put_plant_prechecks_before_emitting_into_shared_builder():
    """Review: BETA imitater / missing VA must not leave a dangling core
    sequence in a batch-shared builder."""
    shared = StubAsm()
    with pytest.raises(NotSupported):
        conv(BETA).put_plant(BOARD, 1, 2, 9, imitater=True, asm=shared)
    assert shared.log == []
    c = conv(GOTY)
    c.fn.pop("put_plant_imitater")
    with pytest.raises(NotSupported):
        c.put_plant(BOARD, 1, 2, 9, imitater=True, asm=shared)
    assert shared.log == []
    c.fn.pop("put_plant_iz_style")
    with pytest.raises(NotSupported):
        c.put_plant(BOARD, 1, 2, 9, challenge=CHALLENGE, asm=shared)
    assert shared.log == []


def test_put_plant_imitater_records_deviation_note():
    frag = conv(GOTY).put_plant(BOARD, 1, 2, 9, imitater=True)
    assert any("AddPlant return" in n for n in frag.notes)
    assert "[notes:" in describe("put_plant", "goty", imitater=True)


# ---------------------------------------------------------------------------
# put_zombie and friends
# ---------------------------------------------------------------------------

def test_put_zombie_goty_vs_zh2010():
    g = conv(GOTY).put_zombie(CHALLENGE, row=1, col=8, ztype=23)
    assert g.ops == [
        ("push_imm32", (8,)),
        ("push_imm32", (23,)),
        ("mov_reg_imm32", ("eax", 1)),
        ("mov_reg_imm32", ("ecx", CHALLENGE)),
        ("call_abs", (fn(GOTY, "put_zombie"),)),
    ]
    z = conv(ZH2010).put_zombie(CHALLENGE, row=1, col=8, ztype=23)
    assert z.ops == [
        ("push_imm32", (23,)),
        ("push_imm32", (CHALLENGE,)),
        ("mov_reg_imm32", ("eax", 1)),
        ("mov_reg_imm32", ("ecx", 8)),
        ("call_abs", (fn(ZH2010, "put_zombie"),)),
    ]
    assert ZH2010.family == "goty_zh2010_ja"


def test_put_zombie_beta_thiscall():
    b = conv(BETA).put_zombie(CHALLENGE, row=1, col=8, ztype=23)
    assert b.pushes() == [1, 8, 23]
    assert ("mov_reg_imm32", ("ecx", CHALLENGE)) in b.ops


def test_put_zombie_in_row_zomboss():
    frag = conv(GOTY).put_zombie_in_row(BOARD)
    assert frag.ops == [
        ("mov_reg_imm32", ("eax", BOARD)),
        ("push_imm32", (0,)),
        ("push_imm32", (25,)),
        ("call_abs", (fn(GOTY, "put_zombie_in_row"),)),
    ]
    with pytest.raises(NotSupported):
        conv(BETA).put_zombie_in_row(BOARD)


def test_put_ladder_and_grave():
    lad = conv(GOTY).put_ladder(BOARD, 2, 3)
    assert lad.ops == [
        ("mov_reg_imm32", ("edi", 2)), ("push_imm32", (3,)),
        ("mov_reg_imm32", ("eax", BOARD)), ("call_abs", (fn(GOTY, "put_ladder"),))]
    g = conv(GOTY).put_grave(CHALLENGE, 4, 6)
    assert g.ops == [
        ("mov_reg_imm32", ("ecx", CHALLENGE)), ("push_reg", ("ecx",)),
        ("mov_reg_imm32", ("edi", 4)), ("mov_reg_imm32", ("ebx", 6)),
        ("call_abs", (fn(GOTY, "put_grave"),))]
    r = conv(RETAIL).put_grave(CHALLENGE, 4, 6)
    assert r.ops[0] == ("mov_reg_imm32", ("edx", CHALLENGE)) and r.ops[1] == ("push_reg", ("edx",))
    b = conv(BETA).put_ladder(BOARD, 2, 3)
    assert b.pushes() == [2, 3] and ("mov_reg_imm32", ("ecx", BOARD)) in b.ops


def test_delete_and_sleep():
    assert conv(GOTY).delete_plant(PLANT).ops == [
        ("push_imm32", (PLANT,)), ("call_abs", (fn(GOTY, "delete_plant"),))]
    assert conv(BETA).delete_plant(PLANT).ops[0] == ("mov_reg_imm32", ("ecx", PLANT))
    assert conv(GOTY).set_plant_sleeping(PLANT, False).ops == [
        ("mov_reg_imm32", ("edi", PLANT)), ("push_imm32", (0,)),
        ("call_abs", (fn(GOTY, "set_plant_sleeping"),))]
    assert conv(RETAIL).set_plant_sleeping(PLANT, True).ops[:2] == [
        ("mov_reg_imm32", ("eax", PLANT)), ("push_imm32", (1,))]
    assert conv(BETA).set_plant_sleeping(PLANT, True).ops[0] == ("mov_reg_imm32", ("ecx", PLANT))
    assert conv(GOTY).delete_grid_item(0x77).ops[0] == ("mov_reg_imm32", ("esi", 0x77))
    assert conv(BETA).delete_grid_item(0x77).ops[0] == ("mov_reg_imm32", ("ecx", 0x77))


# ---------------------------------------------------------------------------
# spawning / scene / misc
# ---------------------------------------------------------------------------

def test_pick_zombie_waves_register_per_family():
    assert conv(GOTY).pick_zombie_waves(BOARD).ops[0] == ("mov_reg_imm32", ("edi", BOARD))
    assert conv(RETAIL).pick_zombie_waves(BOARD).ops[0] == ("mov_reg_imm32", ("edi", BOARD))
    assert conv(ZH2010).pick_zombie_waves(BOARD).ops[0] == ("mov_reg_imm32", ("eax", BOARD))
    assert conv(BETA).pick_zombie_waves(BOARD).ops[0] == ("mov_reg_imm32", ("ecx", BOARD))


def test_spawn_preview_requires_street_zombies_hack():
    cutscene = 0x5555
    frag = conv(GOTY).refresh_spawn_preview(BOARD, cutscene)
    assert frag.ops == [
        ("mov_reg_imm32", ("ebx", BOARD)),
        ("call_abs", (fn(GOTY, "remove_cutscene_zombies"),)),
        ("push_imm32", (cutscene,)),
        ("call_abs", (fn(GOTY, "place_street_zombies"),)),
    ]
    assert frag.required_patches == ("street_zombies_hack",)
    assert "street_zombies_hack" in GOTY.patches
    assert conv(GOTY).place_street_zombies(cutscene).required_patches == ("street_zombies_hack",)
    assert conv(GOTY).remove_cutscene_zombies(BOARD).required_patches == ()
    with pytest.raises(NotSupported):
        conv(BETA).refresh_spawn_preview(BOARD, cutscene)


def test_pick_background_writes_scene_field():
    frag = conv(GOTY).pick_background(BOARD, 2)
    assert frag.ops == [
        ("mov_reg_imm32", ("esi", BOARD)),
        ("mov_regdisp_imm32", ("esi", GOTY.board.scene, 2)),
        ("call_abs", (fn(GOTY, "pick_background"),)),
    ]
    assert GOTY.board.scene == 0x5564
    with pytest.raises(NotSupported):
        conv(BETA).pick_background(BOARD, 2)


def test_play_music_and_fade_out():
    assert conv(GOTY).play_music(0x99, 5).ops == [
        ("mov_reg_imm32", ("edi", 5)), ("mov_reg_imm32", ("eax", 0x99)),
        ("call_abs", (fn(GOTY, "play_music"),))]
    assert conv(BETA).play_music(0x99, 5).ops == [
        ("push_imm32", (5,)), ("mov_reg_imm32", ("ecx", 0x99)),
        ("call_abs", (fn(BETA, "play_music"),))]
    assert conv(GOTY).fade_out_level(BOARD).ops[0] == ("mov_reg_imm32", ("ecx", BOARD))
    assert conv(ZH2010).fade_out_level(BOARD).ops[:2] == [
        ("mov_reg_imm32", ("eax", BOARD)), ("push_reg", ("eax",))]
    with pytest.raises(NotSupported):
        conv(BETA).fade_out_level(BOARD)


def test_next_stage_clear_decrements_round():
    off = GOTY.objects["challenge"].fields.endless_round
    g = conv(GOTY).puzzle_next_stage_clear(CHALLENGE)
    assert g.ops == [
        ("mov_reg_imm32", ("edi", CHALLENGE)), ("dec_regdisp", ("edi", off)),
        ("call_abs", (fn(GOTY, "puzzle_next_stage_clear"),))]
    assert ("raw", (dec_regdisp32("edi", off),)) in g.asm.log
    assert dec_regdisp32("edi", 0x6C) == b"\xff\x8f\x6c\x00\x00\x00"
    z = conv(ZH2010).puzzle_next_stage_clear(CHALLENGE)
    assert z.ops[2] == ("push_reg", ("edi",))
    b = conv(BETA).puzzle_next_stage_clear(CHALLENGE)
    assert b.ops[2] == ("mov_reg_reg", ("ecx", "edi"))


def test_mowers():
    assert conv(GOTY).mower_start(0x10).ops[0] == ("mov_reg_imm32", ("esi", 0x10))
    assert conv(ZH2010).mower_start(0x10).ops[0] == ("mov_reg_imm32", ("ebx", 0x10))
    assert conv(BETA).mower_start(0x10).ops[0] == ("mov_reg_imm32", ("ecx", 0x10))
    assert conv(GOTY).mower_delete(0x10).ops[0] == ("mov_reg_imm32", ("eax", 0x10))
    assert conv(BETA).mower_delete(0x10).ops[0] == ("mov_reg_imm32", ("ecx", 0x10))
    r = conv(GOTY).mower_restore(BOARD)
    assert r.ops[0] == ("push_imm32", (BOARD,))
    assert r.required_patches == ("mower_init_allow", "mower_init_no_anim")
    with pytest.raises(NotSupported):
        conv(BETA).mower_restore(BOARD)


def test_wisdom_tree_and_sync_profile():
    assert conv(GOTY).wisdom_tree_grow(CHALLENGE, 0x1).ops[0] == ("mov_reg_imm32", ("esi", CHALLENGE))
    assert conv(RETAIL).wisdom_tree_grow(CHALLENGE, 0x1).ops[0] == ("mov_reg_imm32", ("edi", 0x1))
    assert conv(ZH2010).wisdom_tree_grow(CHALLENGE, 0x1).ops[:2] == [
        ("mov_reg_imm32", ("ebx", CHALLENGE)), ("push_reg", ("ebx",))]
    zh2012 = load_profile(PROFILE_DIR / "goty_1_1_0_1056_zh_2012_06.json")
    assert zh2012.family == "goty_zh2012"
    assert conv(zh2012).wisdom_tree_grow(CHALLENGE, 0x1).ops[0] == ("mov_reg_imm32", ("edi", CHALLENGE))
    # zh2012 otherwise follows GOTY
    strip = lambda ops: [(op, a) for op, a in ops if op != "call_abs"]
    assert strip(conv(zh2012).put_zombie(CHALLENGE, 0, 0, 2).ops) == strip(conv(GOTY).put_zombie(CHALLENGE, 0, 0, 2).ops)
    s = conv(ZH2010).sync_profile(0x42)
    assert s.ops[:3] == [("push_imm32", (1,)), ("mov_reg_imm32", ("ecx", 0x42)), ("mov_reg_reg", ("esi", "ecx"))]
    assert conv(GOTY).sync_profile(0x42).ops[2][0] == "call_abs"


# ---------------------------------------------------------------------------
# cob_fire
# ---------------------------------------------------------------------------

def test_cob_fire_retail_and_goty():
    frag = conv(RETAIL).cob_fire(PLANT, x=400, y=300)
    assert frag.ops == [
        ("push_imm32", (300,)),
        ("push_imm32", (400,)),
        ("mov_reg_imm32", ("eax", PLANT)),
        ("call_abs", (0x466D50,)),
    ]
    assert ("add_esp", (8,)) not in frag.ops  # callee cleans (ret 8)
    g = conv(GOTY).cob_fire(PLANT, x=400, y=300)
    assert g.ops[:3] == frag.ops[:3] and g.ops[3] == ("call_abs", (0x46D1F0,))
    for p in (ZH2010, ZH2012, BETA):
        with pytest.raises(NotSupported) as ei:
            conv(p).cob_fire(PLANT, 1, 2)
        assert ei.value.name == "cob_fire" and ei.value.family == p.family
        assert "unverified" in ei.value.reason


# ---------------------------------------------------------------------------
# NotSupported / convention_for / describe
# ---------------------------------------------------------------------------

def test_missing_address_raises_not_supported():
    c = FAMILIES["goty"]({"put_plant": None}, GOTY, StubAsm)
    with pytest.raises(NotSupported) as ei:
        c.put_plant(BOARD, 0, 0, 1)
    assert "address missing" in str(ei.value)
    with pytest.raises(NotSupported):
        c.delete_plant(PLANT)


def test_convention_for_family_and_unknown():
    assert convention_for(GOTY, asm_factory=StubAsm).family == "goty"
    assert convention_for(RETAIL, asm_factory=StubAsm).family == "retail"
    assert convention_for(ZH2010, asm_factory=StubAsm).family == "goty_zh2010_ja"
    assert convention_for(BETA, asm_factory=StubAsm).family == "beta"

    class Modded:
        family = "unknown"
        conventions = "unknown"
        functions = {}
        image_base = 0x400000
    with pytest.raises(NotSupported):
        convention_for(Modded(), asm_factory=StubAsm)


def test_mov_reg_reg_encoding():
    frag = Fragment(StubAsm())
    mov_reg_reg(frag, "esi", "eax")
    mov_reg_reg(frag, "ecx", "edi")
    assert frag.asm.log == [("raw", (b"\x8b\xf0",)), ("raw", (b"\x8b\xcf",))]
    assert frag.ops == [("mov_reg_reg", ("esi", "eax")), ("mov_reg_reg", ("ecx", "edi"))]


def test_describe_outputs():
    assert describe("put_zombie", "goty") == (
        "goty put_zombie: push col; push type; EAX = row; ECX = challenge; call put_zombie; ret")
    assert describe("put_zombie", "goty_zh2010_ja") == (
        "goty_zh2010_ja put_zombie: push type; push challenge; EAX = row; ECX = col; "
        "call put_zombie; ret")
    assert describe("put_plant", "beta").startswith(
        "beta put_plant: push -1; push type; push row; push col; ECX = board; call put_plant")
    im = describe("put_plant", "goty", imitater=True)
    assert "push type; push 48;" in im and "ESI = EAX; call put_plant_imitater" in im
    assert describe("cob_fire", "goty") == "goty cob_fire: push y; push x; EAX = cob_plant; call cob_fire; ret"
    assert describe("cob_fire", "goty_zh2012").startswith("goty_zh2012 cob_fire: not supported")
    assert "[requires patches: street_zombies_hack]" in describe("refresh_spawn_preview", "retail")
    with pytest.raises(NotSupported):
        describe("put_plant", "modded")
    with pytest.raises(NotSupported):
        describe("no_such_call", "goty")
    for fam in FAMILIES:
        text = describe_all(fam)
        assert len(text.splitlines()) == len(CALL_NAMES)
