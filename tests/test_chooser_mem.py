"""control.chooser_mem / control.menu: fake session with a synthetic
SeedChooserScreen (49 ChosenSeed entries), fake executor capturing fragments."""
from __future__ import annotations

import struct

import pytest

import pvztools.schema.track  # noqa: F401
from pvztools.control.asm import Asm
from pvztools.control.chooser_mem import (
    APP_AWARD_SCREEN, APP_SEED_CHOOSER, CH_BOARD, CH_IN_BANK, CH_IN_FLIGHT, CHOSEN_SEEDS_OFF,
    CHOSEN_STRIDE, CS_BANK_INDEX, CS_CRAZY_DAVE, CS_STATE, CS_TYPE, BOARD_LEVEL, ChooserError,
    MemoryChooser, NUM_CHOSEN, SEED_FLYING_TO_BANK, SEED_FLYING_TO_CHOOSER, SEED_IN_BANK,
    SEED_IN_CHOOSER, SEED_PACKET_HIDDEN, seeds_available_for_level,
)
from pvztools.control.conv import NotSupported, convention_for
from pvztools.control.conv.retail import RetailConvention
from pvztools.control.menu import MainMenu, MenuError, UI_AWARD
from pvztools.observe import Observer
from test_game_api import BOARD, LAWN, make_session

CHOOSER = 0x2A000000
SELECTOR = 0x2B000000
AWARD = 0x2C000000
BANK = 0x2D000000
PLAYER = 0x2E000000


def fn_va(p, name):
    return p.functions[name].va(p.image_base)


def seed_addr(t):
    return CHOOSER + CHOSEN_SEEDS_OFF + t * CHOSEN_STRIDE


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class ChooserExecutor:
    """Records fragments and simulates ClickedSeedInChooser / InBank /
    OnStartButton on the fake session (seeds land immediately unless
    ``fly`` is set)."""

    def __init__(self, session, profile, fly=False, start_ui=3, dry_run=False):
        self.session, self.profile = session, profile
        self.calls = []
        self.checks = []
        self.fly = fly
        self.start_ui = start_ui
        self.safety = type("S", (), {"dry_run": dry_run})()

    def check(self, action, ui=None):
        self.checks.append((action, tuple(ui) if ui else None))

    def frame_ms(self):
        return 10

    def run(self, code, *, name="shellcode", extra_patches=(), **kw):
        assert isinstance(code, Asm)
        texts = [t for _, _, t in code._insns]
        self.calls.append((name, texts))
        if self.safety.dry_run:
            return b""
        s, p = self.session, self.profile
        va_ch = fn_va(p, "chooser_clicked_seed_in_chooser")
        va_bk = fn_va(p, "chooser_clicked_seed_in_bank")
        va_st = fn_va(p, "chooser_on_start")
        va_adv = fn_va(p, "game_selector_clicked_adventure")
        va_aw = fn_va(p, "award_start_button")
        for i, t in enumerate(texts):
            if not t.startswith("call "):
                continue
            target = int(t.split(" ")[1], 16)
            if target in (va_ch, va_bk):
                seed = int(texts[i - 2].split(" ")[1], 16)
                this = int(texts[i - 1].split(", ")[1], 16)
                assert this == CHOOSER
                assert texts[i - 1].startswith("mov eax, ")
                self._click(seed, target == va_bk)
            elif target == va_st:
                assert texts[i - 1] == "mov esi, 0x%X" % CHOOSER
                s.poke_u32(LAWN + p.lawn_app.game_ui, self.start_ui)
            elif target == va_adv:
                assert texts[i - 1] == "push 0x%X" % SELECTOR
                s.poke_u32(LAWN + p.lawn_app.game_ui, 2)
            elif target == va_aw:
                assert texts[i - 1] == "push 0x%X" % AWARD
                s.poke_u32(LAWN + p.lawn_app.game_ui, 1)
        return 0

    def _click(self, seed, in_bank):
        s = self.session
        n = s.read_i32(CHOOSER + CH_IN_BANK)
        if in_bank:
            idx = s.read_i32(seed + CS_BANK_INDEX)
            # shift later seeds left (mirrors the game)
            for t in range(NUM_CHOSEN):
                a = seed_addr(t)
                if s.read_i32(a + CS_STATE) == SEED_IN_BANK and s.read_i32(a + CS_BANK_INDEX) > idx:
                    s.poke_i32(a + CS_BANK_INDEX, s.read_i32(a + CS_BANK_INDEX) - 1)
            s.poke_i32(seed + CS_STATE, SEED_FLYING_TO_CHOOSER if self.fly else SEED_IN_CHOOSER)
            s.poke_i32(seed + CS_BANK_INDEX, 0)
            s.poke_i32(CHOOSER + CH_IN_BANK, n - 1)
        else:
            s.poke_i32(seed + CS_STATE, SEED_FLYING_TO_BANK if self.fly else SEED_IN_BANK)
            s.poke_i32(seed + CS_BANK_INDEX, n)
            s.poke_i32(CHOOSER + CH_IN_BANK, n + 1)
        if self.fly:
            s.poke_i32(CHOOSER + CH_IN_FLIGHT, s.read_i32(CHOOSER + CH_IN_FLIGHT) + 1)

    def land_all(self):
        s = self.session
        for t in range(NUM_CHOSEN):
            a = seed_addr(t)
            st = s.read_i32(a + CS_STATE)
            if st == SEED_FLYING_TO_BANK:
                s.poke_i32(a + CS_STATE, SEED_IN_BANK)
            elif st == SEED_FLYING_TO_CHOOSER:
                s.poke_i32(a + CS_STATE, SEED_IN_CHOOSER)
        s.poke_i32(CHOOSER + CH_IN_FLIGHT, 0)


def make_chooser_world(ui=2, slots=6, bank=(), scene=2, level=7, playthrough=0, mode=0,
                       purchases=(), fly=False, dry_run=False, start_ui=3):
    s, p = make_session(ui=ui)
    s.poke_u32(LAWN + APP_SEED_CHOOSER, CHOOSER)
    s.poke_u32(LAWN + p.lawn_app.game_selector, SELECTOR)
    s.poke_u32(LAWN + APP_AWARD_SCREEN, AWARD)
    s.poke_u32(LAWN + p.lawn_app.game_mode, mode)
    s.poke_u32(LAWN + p.lawn_app.player_info, PLAYER)
    s.poke_i32(PLAYER + p.player_info.level, level)
    s.poke_i32(PLAYER + p.player_info.playthrough, playthrough)
    for i, v in enumerate(purchases):
        s.poke_i32(PLAYER + p.player_info.purchases + 4 * i, v)
    s.poke_u32(CHOOSER + CH_BOARD, BOARD)
    s.poke_u32(BOARD + p.board.seed_bank, BANK)
    s.poke_i32(BANK + 0x24, slots)
    s.poke_u32(BOARD + p.board.scene, scene)
    s.poke_i32(BOARD + BOARD_LEVEL, 0)
    s.poke_i32(BOARD + p.board.wave_total, 0)
    for t in range(NUM_CHOSEN):
        a = seed_addr(t)
        s.poke_i32(a + CS_TYPE, t)
        s.poke_i32(a + CS_STATE, SEED_PACKET_HIDDEN if t == 48 else SEED_IN_CHOOSER)
        s.poke_i32(a + CS_BANK_INDEX, 0)
    for i, t in enumerate(bank):
        s.poke_i32(seed_addr(t) + CS_STATE, SEED_IN_BANK)
        s.poke_i32(seed_addr(t) + CS_BANK_INDEX, i)
    s.poke_i32(CHOOSER + CH_IN_BANK, len(bank))
    s.poke_i32(CHOOSER + CH_IN_FLIGHT, 0)
    ex = ChooserExecutor(s, p, fly=fly, dry_run=dry_run, start_ui=start_ui)
    clock = FakeClock()
    mc = MemoryChooser(s, p, ex, Observer(s, p), sleep=clock.sleep, clock=clock)
    return mc, s, p, ex, clock


# ---------------------------------------------------------------------------
# conventions
# ---------------------------------------------------------------------------

def test_goty_fragments():
    _, s, p, _, _ = make_chooser_world()
    conv = convention_for(p)
    f = conv.seed_chooser_click_seed(CHOOSER, seed_addr(1), terminate=True)
    assert f.ops == [("push_imm32", (seed_addr(1),)), ("mov_reg_imm32", ("eax", CHOOSER)),
                     ("call_abs", (fn_va(p, "chooser_clicked_seed_in_chooser"),)), ("ret", ())]
    f = conv.seed_chooser_click_seed(CHOOSER, seed_addr(1), in_bank=True)
    assert f.calls() == [fn_va(p, "chooser_clicked_seed_in_bank")]
    f = conv.seed_chooser_start(CHOOSER)
    assert f.ops == [("mov_reg_imm32", ("esi", CHOOSER)), ("call_abs", (fn_va(p, "chooser_on_start"),))]
    f = conv.award_start(AWARD)
    assert f.ops == [("push_imm32", (AWARD,)), ("call_abs", (fn_va(p, "award_start_button"),))]
    f = conv.game_selector_adventure(SELECTOR)
    assert f.ops == [("push_imm32", (SELECTOR,)),
                     ("call_abs", (fn_va(p, "game_selector_clicked_adventure"),))]


def test_other_families_not_supported():
    conv = RetailConvention({}, None, lambda: Asm())
    for name, args in (("seed_chooser_click_seed", (1, 2)), ("seed_chooser_start", (1,)),
                       ("award_start", (1,)), ("game_selector_adventure", (1,))):
        with pytest.raises(NotSupported) as e:
            getattr(conv, name)(*args)
        assert e.value.reason == "unverified"


def test_profile_functions_1096():
    _, _, p, _, _ = make_chooser_world()
    assert fn_va(p, "chooser_on_start") == 0x493C70
    assert fn_va(p, "chooser_clicked_seed_in_chooser") == 0x494690
    assert fn_va(p, "chooser_clicked_seed_in_bank") == 0x4944F0
    assert fn_va(p, "chooser_find_seed_in_bank") == 0x494490
    assert fn_va(p, "award_start_button") == 0x40A310
    assert fn_va(p, "game_selector_clicked_adventure") == 0x450B90
    assert fn_va(p, "show_seed_chooser") == 0x454B00


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def test_read_state():
    mc, s, p, ex, _ = make_chooser_world(bank=(1, 0, 16))
    assert mc.chooser_ptr() == CHOOSER
    assert mc.num_packets() == 6
    assert mc.seeds_in_bank() == 3 and mc.seeds_in_flight() == 0
    seeds = mc.chosen_seeds()
    assert len(seeds) == NUM_CHOSEN
    assert seeds[16] == (16, 16, SEED_IN_BANK, 2, False, 0)
    assert seeds[48].state == SEED_PACKET_HIDDEN
    assert mc.bank() == [1, 0, 16]
    assert mc.bank_landed()


def test_seeds_available():
    assert seeds_available_for_level(1, 0) == 1      # 1-1: peashooter only
    assert seeds_available_for_level(7, 0) == 6      # 1-7
    assert seeds_available_for_level(11, 0) == 9     # 2-1
    assert seeds_available_for_level(21, 0) == 17
    assert seeds_available_for_level(50, 0) == 40
    assert seeds_available_for_level(3, 1) == 49
    mc, *_ = make_chooser_world(level=7, purchases=(1, 0))
    assert mc.available(5) is True and mc.available(6) is False
    assert mc.available(40) is True and mc.available(41) is False


def test_requires_goty_1096(monkeypatch):
    _, s, p, ex, _ = make_chooser_world()
    from pvztools.addr.profile import profile_for_stamp
    from test_game_api import STAMP_1096
    bad = profile_for_stamp(STAMP_1096)
    monkeypatch.setattr(type(bad), "functions", property(lambda self: {}), raising=False)
    with pytest.raises(ChooserError):
        MemoryChooser(s, bad, ex, Observer(s, p))


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------

def test_pick_emits_calls_in_one_fragment():
    mc, s, p, ex, _ = make_chooser_world()
    got = mc.pick(["向日葵", 0, "wallnut"])
    assert got == [1, 0, 3]
    assert len(ex.calls) == 1
    name, texts = ex.calls[0]
    assert name == "chooser_pick"
    va = fn_va(p, "chooser_clicked_seed_in_chooser")
    assert texts.count("call 0x%X" % va) == 3
    assert texts[0] == "push 0x%X" % seed_addr(1)
    assert texts[1] == "mov eax, 0x%X" % CHOOSER
    assert texts[-1] == "ret"
    assert mc.bank() == [1, 0, 3]
    assert ex.checks == [("选卡", (2,))]
    assert len(mc.history) == 1


def test_pick_skips_bank_and_refuses_bad():
    mc, s, p, ex, _ = make_chooser_world(bank=(1,), level=7)
    assert mc.pick([1, 0]) == [0]          # sunflower already in bank
    with pytest.raises(ChooserError):      # level 1-7 has not unlocked 14
        mc.pick([14])
    with pytest.raises(ChooserError):      # imitater
        mc.pick([48])
    s.poke_i32(seed_addr(2) + CS_STATE, SEED_PACKET_HIDDEN)
    with pytest.raises(ChooserError):
        mc.pick([2])
    with pytest.warns(UserWarning):
        assert mc.pick([2, 3], strict=False) == [3]
    assert len(ex.calls) == 2


def test_pick_room():
    mc, s, p, ex, _ = make_chooser_world(slots=2, bank=(1,))
    with pytest.raises(ChooserError):
        mc.pick([0, 3])
    assert ex.calls == []
    assert mc.pick([0]) == [0]


def test_clear_bank_descending_one_call():
    mc, s, p, ex, _ = make_chooser_world(bank=(1, 0, 16, 3))
    s.poke(seed_addr(3) + CS_CRAZY_DAVE, b"\x01")   # crazy dave pick stays
    removed = mc.clear_bank()
    assert removed == [1, 0, 16]
    assert len(ex.calls) == 1
    name, texts = ex.calls[0]
    assert name == "chooser_clear_bank"
    pushes = [int(t.split(" ")[1], 16) for t in texts if t.startswith("push ")]
    assert pushes == [seed_addr(16), seed_addr(0), seed_addr(1)]
    va = fn_va(p, "chooser_clicked_seed_in_bank")
    assert texts.count("call 0x%X" % va) == 3
    assert mc.bank() == [3]
    assert mc.clear_bank() == [] and len(ex.calls) == 1


def test_wait_landed_polls_in_flight():
    mc, s, p, ex, clock = make_chooser_world(fly=True, level=50)
    mc.pick([1])
    assert s.read_i32(CHOOSER + CH_IN_FLIGHT) == 1
    assert mc.bank() == [1]               # flying-to-bank counts as bank
    assert not mc.bank_landed()
    assert mc.wait_landed(wait=0.3) is False
    assert clock.t >= 0.3
    ex.land_all()
    assert mc.wait_landed(wait=0.3) is True
    # start refuses while something is still flying
    mc.pick([0, 3, 16, 2, 4])
    with pytest.raises(ChooserError):
        mc.start(wait=0.1)
    assert not any(n == "chooser_start" for n, _ in ex.calls)


def test_start_polls_ui_and_requires_full_bank():
    mc, s, p, ex, clock = make_chooser_world(bank=(1, 16, 0, 3), slots=4)
    assert mc.start(wait=1.0) is True
    assert ex.calls[-1][0] == "chooser_start"
    assert ex.calls[-1][1] == ["mov esi, 0x%X" % CHOOSER, "call 0x%X" % fn_va(p, "chooser_on_start"), "ret"]
    assert mc.ui() == 3


def test_start_refuses_not_full():
    mc, s, p, ex, clock = make_chooser_world(bank=(1, 16, 0), slots=4)
    with pytest.raises(ChooserError):
        mc.start(auto_fix=False)
    assert ex.calls == []


def test_start_prevents_repick_dialog_by_adding_seeds():
    # pool scene, no lily, no sun producer, one slot spare -> lily + sunflower needed
    mc, s, p, ex, clock = make_chooser_world(bank=(0, 3, 2, 4), slots=6, scene=2, level=30)
    missing = mc.missing_for_start()
    assert [c for _, c in missing] == [(1, 41, 9), (16, 24, 19)]
    with pytest.raises(ChooserError):
        mc.start(auto_fix=False)
    with pytest.warns(UserWarning):
        assert mc.start(wait=1.0) is True
    assert mc.bank()[:4] == [0, 3, 2, 4] and set(mc.bank()[4:]) == {1, 16}
    assert [n for n, _ in ex.calls] == ["chooser_pick", "chooser_pick", "chooser_start"]


def test_start_replaces_when_bank_full():
    mc, s, p, ex, clock = make_chooser_world(bank=(0, 3, 2, 4), slots=4, scene=1, level=30)
    with pytest.warns(UserWarning):
        added = mc.ensure_start_safe()
    assert added == [1]
    assert mc.bank() == [0, 3, 2, 1]       # last seed (4) swapped for sunflower
    assert [n for n, _ in ex.calls] == ["chooser_unpick", "chooser_pick"]


def test_missing_for_start_rules():
    mc, s, p, ex, _ = make_chooser_world(bank=(1, 40), slots=6, scene=4, level=50)
    reasons = dict(mc.missing_for_start())
    assert (33,) in reasons.values()                 # roof: flower pot
    assert (7,) in reasons.values()                  # gatling needs repeater
    # balloon zombie in wave 0 -> flyer protection
    s.poke_i32(BOARD + p.board.wave_total, 1)
    s.poke_i32(BOARD + p.board.spawn_list, 16)
    s.poke_i32(BOARD + p.board.spawn_list + 4, -1)
    assert (43, 26, 27) in dict(mc.missing_for_start()).values()
    # survival: no sun / pool warnings
    s.poke_u32(LAWN + p.lawn_app.game_mode, 11)
    mc2, s2, p2, _, _ = make_chooser_world(bank=(0,), slots=6, scene=2, mode=11)
    assert mc2.missing_for_start() == []
    # last stand: no sun warning but pool still applies
    mc3, *_ = make_chooser_world(bank=(0,), slots=6, scene=2, mode=0x1F)
    assert [c for _, c in mc3.missing_for_start()] == [(16, 24, 19)]
    # first-time 4-1 puffshroom
    mc4, s4, p4, _, _ = make_chooser_world(bank=(1, 16), slots=6, scene=1, level=11, playthrough=0)
    s4.poke_i32(BOARD + BOARD_LEVEL, 11)
    assert (8,) in dict(mc4.missing_for_start()).values()


def test_start_no_ui_change_reports_dialog():
    class W:
        def dialog_open(self, expected_ui=None, wait=1.0):
            return True

    mc, s, p, ex, clock = make_chooser_world(bank=(1, 16, 0, 3), slots=4, start_ui=2)
    mc.window = W()
    with pytest.raises(ChooserError):
        mc.start(wait=0.3)
    mc.window = None
    assert mc.start(wait=0.3) is False


def test_select_and_start():
    mc, s, p, ex, clock = make_chooser_world(bank=(2, 4), slots=4, scene=1, level=30)
    assert mc.select_and_start([1, 0, 3, 5], wait=1.0) is True
    assert [n for n, _ in ex.calls] == ["chooser_clear_bank", "chooser_pick", "chooser_start"]
    assert mc.bank() == [1, 0, 3, 5]


def test_requires_chooser_ui():
    mc, s, p, ex, _ = make_chooser_world(ui=3)
    with pytest.raises(ChooserError):
        mc.pick([1])
    s.poke_u32(LAWN + p.lawn_app.game_ui, 2)
    s.poke_u32(LAWN + APP_SEED_CHOOSER, 0)
    with pytest.raises(ChooserError):
        mc.pick([1])


def test_dry_run_emits_without_polling():
    mc, s, p, ex, clock = make_chooser_world(bank=(1, 16, 0, 3), slots=4, dry_run=True)
    assert mc.pick([1, 0]) == []          # both already in bank
    assert mc.clear_bank() == [1, 16, 0, 3]
    assert ex.calls[-1][0] == "chooser_clear_bank"
    # memory untouched in dry run; start still refuses the (unchanged) state checks
    assert mc.bank() == [1, 16, 0, 3]
    assert mc.start(wait=1.0) is False
    assert ex.calls[-1][0] == "chooser_start"
    assert clock.t == 0.0


# ---------------------------------------------------------------------------
# menu
# ---------------------------------------------------------------------------

def test_menu_start_adventure():
    mc, s, p, ex, clock = make_chooser_world(ui=1)
    menu = MainMenu(s, p, ex, Observer(s, p), sleep=clock.sleep, clock=clock)
    assert menu.game_selector_ptr() == SELECTOR
    assert menu.start_adventure(wait=1.0) == 2
    name, texts = ex.calls[-1]
    assert name == "game_selector_adventure"
    assert texts == ["push 0x%X" % SELECTOR, "call 0x%X" % fn_va(p, "game_selector_clicked_adventure"), "ret"]
    assert ex.checks[-1] == ("开始冒险模式", (1,))
    with pytest.raises(MenuError):        # ui is 2 now
        menu.start_adventure()


def test_menu_award_continue_and_no_observer():
    mc, s, p, ex, clock = make_chooser_world(ui=UI_AWARD)
    menu = MainMenu(s, p, ex, sleep=clock.sleep, clock=clock)
    assert menu.ui() == 5 and menu.award_ptr() == AWARD
    assert menu.award_continue(wait=1.0) == 1
    name, texts = ex.calls[-1]
    assert name == "award_start"
    assert texts == ["push 0x%X" % AWARD, "call 0x%X" % fn_va(p, "award_start_button"), "ret"]
    with pytest.raises(MenuError):
        menu.award_continue()


def test_menu_missing_pointer_and_dry_run():
    mc, s, p, ex, clock = make_chooser_world(ui=1, dry_run=True)
    menu = MainMenu(s, p, ex, Observer(s, p), sleep=clock.sleep, clock=clock)
    assert menu.start_adventure(wait=1.0) is None
    assert ex.calls[-1][0] == "game_selector_adventure"
    s.poke_u32(LAWN + p.lawn_app.game_selector, 0)
    with pytest.raises(MenuError):
        menu.start_adventure()


def test_menu_requires_goty(monkeypatch):
    mc, s, p, ex, _ = make_chooser_world(ui=1)
    monkeypatch.setattr(type(p), "functions", property(lambda self: {}), raising=False)
    with pytest.raises(MenuError):
        MainMenu(s, p, ex)
