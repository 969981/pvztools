"""control.cutscene: Crazy Dave intro talk on a fake session with a synthetic
CutScene object; MemoryChooser / GameWindow / play integration."""
from __future__ import annotations

import warnings
from typing import Any, List

import pytest

import pvztools.schema.track  # noqa: F401
from pvztools.control.asm import Asm
from pvztools.control.chooser_mem import ChooserDialogError, ChooserError
from pvztools.control.cutscene import (
    ADVANCE_DIALOG_PROLOGUE, ADVANCE_DIALOG_VA, APP_DAVE_MESSAGE, APP_DAVE_STATE, APP_GAME_MODE,
    APP_GAME_SCENE, CS_DAVE_DIALOG_START, CS_DAVE_TIME, CS_PRE_UPDATING, CS_SEED_CHOOSING, CS_TIME,
    MODE_UPSELL, TIME_SEED_CHOOSER_SLIDE_ON_END, Cutscene, CutsceneDialogError, CutsceneError,
    CutsceneState, crazy_dave_message_index,
)
from pvztools.control.play import PlayError
from pvztools.observe import Observer
from test_chooser_mem import ChooserExecutor, FakeClock, make_chooser_world
from test_game_api import BOARD, LAWN, make_session
from test_play import FakeGame, FakeMemChooser, session as play_session

CUTSCENE = 0x2F000000
DAVE_LINES = (201, 202, 203)     # level 1-1 talk: three lines, then leave


# --- fakes ---------------------------------------------------------------------
class CutsceneExecutor(ChooserExecutor):
    """Simulates ``AdvanceCrazyDaveDialog(this, 1)``: next line, or leave
    (``msg = -1``, state LEAVING) after the last one.  Every ``sleep``
    (one frame) then advances the cutscene clock like ``CutScene::Update``
    while no dialog is active, and sets ``mSeedChoosing`` at
    ``TimeSeedChoserSlideOnEnd + mCrazyDaveTime``."""

    def __init__(self, session, profile, lines=DAVE_LINES, endless=False, **kw):
        super().__init__(session, profile, **kw)
        self.lines = list(lines)
        self.endless = endless
        self.advance_calls: List[Any] = []

    def run(self, code, *, name="shellcode", extra_patches=(), **kw):
        assert isinstance(code, Asm)
        texts = [t for _, _, t in code._insns]
        self.calls.append((name, texts))
        if self.safety.dry_run:
            return b""
        for i, t in enumerate(texts):
            if t == "call 0x%X" % ADVANCE_DIALOG_VA:
                assert texts[i - 2] == "push 0x1" and texts[i - 1] == "push 0x%X" % CUTSCENE, texts
                assert "transition" not in kw or not kw["transition"]
                self.advance_calls.append(tuple(texts[i - 2:i + 1]))
                self._advance()
                break
        else:
            return super().run(code, name=name, extra_patches=extra_patches, **kw)
        return 0

    def _advance(self):
        s = self.session
        msg = s.read_i32(LAWN + APP_DAVE_MESSAGE)
        if msg == -1 or s.read_i32(LAWN + APP_GAME_MODE) == MODE_UPSELL:
            return
        if self.endless:
            s.poke_i32(LAWN + APP_DAVE_MESSAGE, msg + 1)
            return
        if msg in self.lines and self.lines.index(msg) + 1 < len(self.lines):
            s.poke_i32(LAWN + APP_DAVE_MESSAGE, self.lines[self.lines.index(msg) + 1])
        else:
            s.poke_i32(LAWN + APP_DAVE_MESSAGE, -1)
            s.poke_i32(LAWN + APP_DAVE_STATE, 2)   # LEAVING

    def tick(self):
        s = self.session
        if s.read_i32(LAWN + APP_DAVE_MESSAGE) != -1 or s.read_u8(CUTSCENE + CS_SEED_CHOOSING):
            return
        t = s.read_i32(CUTSCENE + CS_TIME) + 10
        s.poke_i32(CUTSCENE + CS_TIME, t)
        if t == TIME_SEED_CHOOSER_SLIDE_ON_END + s.read_i32(CUTSCENE + CS_DAVE_TIME):
            s.poke(CUTSCENE + CS_SEED_CHOOSING, b"\x01")
            s.poke_i32(LAWN + APP_DAVE_STATE, 0)


def make_world(msg=201, dave_time=2500, time=2750, seed_choosing=False, scene=2, mode=0, prologue=True,
               dialog_start=-1, **exkw):
    s, p = make_session(ui=scene)
    s.poke_u32(BOARD + p.board.cutscene, CUTSCENE)
    s.poke_i32(LAWN + APP_GAME_SCENE, scene)
    s.poke_i32(LAWN + APP_GAME_MODE, mode)
    s.poke_i32(LAWN + APP_DAVE_MESSAGE, msg)
    s.poke_i32(LAWN + APP_DAVE_STATE, 3 if msg != -1 else 0)
    s.poke_i32(CUTSCENE + CS_TIME, time)
    s.poke_i32(CUTSCENE + CS_DAVE_TIME, dave_time)
    s.poke_i32(CUTSCENE + CS_DAVE_DIALOG_START, dialog_start)
    s.poke(CUTSCENE + CS_SEED_CHOOSING, b"\x01" if seed_choosing else b"\x00")
    s.poke(CUTSCENE + CS_PRE_UPDATING, b"\x00")
    if prologue:
        s.poke(ADVANCE_DIALOG_VA, ADVANCE_DIALOG_PROLOGUE)
    ex = CutsceneExecutor(s, p, **exkw)
    clock = FakeClock()

    def sleep(dt):
        clock.sleep(dt)
        for _ in range(max(1, int(round(dt / 0.01)))):
            ex.tick()

    cs = Cutscene(s, p, ex, Observer(s, p), sleep=sleep, clock=clock)
    return cs, s, p, ex, clock


# --- state / detection -------------------------------------------------------------
def test_state_and_dialog_active():
    cs, s, p, ex, _ = make_world()
    st = cs.state()
    assert isinstance(st, CutsceneState)
    assert st.active_dialog and st.message_index == 201 and st.dave_state == 3
    assert st.dave_state_name == "idling" and st.cutscene_time == 2750 and st.dave_time == 2500
    assert st.showing_dave and not st.seed_chooser_ready and st.cutscene_ptr == CUTSCENE
    assert cs.dialog_active() is True
    assert "msg=201" in st.describe()
    # no talk: -1 (and 0, never produced by the game) are "none"
    s.poke_i32(LAWN + APP_DAVE_MESSAGE, -1)
    assert cs.dialog_active() is False
    s.poke_i32(LAWN + APP_DAVE_MESSAGE, 0)
    assert cs.dialog_active() is False
    # in the level (scene 3) a stale index is not a dialog
    s.poke_i32(LAWN + APP_DAVE_MESSAGE, 201)
    s.poke_i32(LAWN + APP_GAME_SCENE, 3)
    assert cs.dialog_active() is False
    assert crazy_dave_message_index(s, p, LAWN) == 201
    assert crazy_dave_message_index(s, p, 0) is None


def test_state_without_board_or_cutscene():
    cs, s, p, ex, _ = make_world()
    s.poke_u32(BOARD + p.board.cutscene, 0)
    st = cs.state()
    assert st.active_dialog and st.cutscene_ptr == 0 and st.cutscene_time == 0
    s.poke_u32(LAWN + p.lawn_app.board, 0)
    assert cs.state().active_dialog is True   # still the app-level signal
    s.poke_u32(p.globals.lawn_app, 0)
    assert cs.state() == CutsceneState(False, 0, 0, False)


def test_seed_chooser_ready_condition():
    cs, s, p, ex, _ = make_world(msg=-1, time=6750, seed_choosing=True)
    st = cs.state()
    assert st.seed_chooser_ready and not st.showing_dave and not st.active_dialog
    # a scheduled talk that has not started yet is not ready either
    s.poke_i32(CUTSCENE + CS_DAVE_DIALOG_START, 201)
    st = cs.state()
    assert st.talk_pending and not st.seed_chooser_ready


# --- advancement ------------------------------------------------------------------
def test_skip_dialog_emits_calls_and_stops_when_inactive():
    cs, s, p, ex, clock = make_world()
    n = cs.skip_dialog()
    assert n == 3
    assert [name for name, _ in ex.calls] == ["cutscene_advance_dialog"] * 3
    assert ex.advance_calls[0] == ("push 0x1", "push 0x%X" % CUTSCENE, "call 0x%X" % ADVANCE_DIALOG_VA)
    assert s.read_i32(LAWN + APP_DAVE_MESSAGE) == -1
    assert cs.dialog_active() is False
    assert cs.skip_dialog() == 0 and len(ex.calls) == 3
    assert [f.name for f in cs.history] == ["cutscene_advance_dialog"] * 3
    assert cs.history[0].ops == [("push_imm32", (1,)), ("push_imm32", (CUTSCENE,)),
                                 ("call_abs", (ADVANCE_DIALOG_VA,))]
    assert cs.history[0].needs_main_loop is True
    assert ("推进戴夫对话", (2,)) in ex.checks


def test_skip_dialog_is_bounded():
    cs, s, p, ex, _ = make_world(endless=True)
    with pytest.raises(CutsceneDialogError) as ei:
        cs.skip_dialog(max_steps=4)
    assert len(ex.advance_calls) == 4 and ei.value.dialog_suspected
    with pytest.raises(CutsceneDialogError):
        cs.skip_dialog(max_steps=0)
    assert len(ex.advance_calls) == 4


def test_prologue_mismatch_refuses_without_call():
    cs, s, p, ex, _ = make_world(prologue=False)
    with pytest.raises(CutsceneError) as ei:
        cs.skip_dialog()
    assert "序言" in str(ei.value) and ex.calls == [] and not ei.value.dialog_suspected
    s.poke(ADVANCE_DIALOG_VA, ADVANCE_DIALOG_PROLOGUE)
    assert cs.verify_function() is True and cs.skip_dialog() == 3


def test_preconditions_refuse():
    cs, s, p, ex, _ = make_world(mode=MODE_UPSELL)
    with pytest.raises(CutsceneError):
        cs.advance_once()
    cs, s, p, ex, _ = make_world()
    s.poke(CUTSCENE + CS_PRE_UPDATING, b"\x01")
    with pytest.raises(CutsceneError):
        cs.advance_once()
    s.poke(CUTSCENE + CS_PRE_UPDATING, b"\x00")
    s.poke_u32(BOARD + p.board.cutscene, 0)
    with pytest.raises(CutsceneError):
        cs.advance_once()
    assert ex.calls == []
    # a real modal dialog (window.dialog_count) -> refuse, dialog_suspected
    cs, s, p, ex, _ = make_world()

    class W:
        def dialog_count(self):
            return 1
    cs.window = W()
    with pytest.raises(CutsceneDialogError):
        cs.skip_dialog()
    assert ex.calls == []
    # no dialog at all: advance_once is refused
    cs, s, p, ex, _ = make_world(msg=-1)
    with pytest.raises(CutsceneError):
        cs.advance_once()


def test_unsupported_profile_and_va_resolution():
    s, p = make_session()
    bad = type("P", (), {"id": "1_0_0_1051_en", "conventions": "retail", "functions": {}, "board": p.board})()
    with pytest.raises(CutsceneError):
        Cutscene(s, bad, None, Observer(s, p))
    unknown = type("P", (), {"id": "goty_1_2_0_1073_en", "conventions": "goty", "functions": {},
                             "board": p.board, "image_base": 0x400000})()
    with pytest.raises(CutsceneError):
        Cutscene(s, unknown, None, Observer(s, p))
    cs = Cutscene(s, unknown, None, Observer(s, p), advance_va=0x441234, conv=type("C", (), {"asm_factory": Asm})())
    assert cs.advance_va == 0x441234
    cs2 = Cutscene(s, p, None, Observer(s, p))
    assert cs2.advance_va == ADVANCE_DIALOG_VA and "0x440ED0" in repr(cs2)


def test_dry_run_records_one_fragment():
    cs, s, p, ex, _ = make_world(dry_run=True)
    assert cs.skip_dialog() == 1
    assert [name for name, _ in ex.calls] == ["cutscene_advance_dialog"]
    assert s.read_i32(LAWN + APP_DAVE_MESSAGE) == 201


def test_wait_ready_for_chooser_skips_and_waits():
    cs, s, p, ex, clock = make_world()
    assert cs.wait_ready_for_chooser(timeout=30.0) is True
    assert len(ex.advance_calls) == 3
    st = cs.state()
    assert st.seed_choosing and st.seed_chooser_ready and st.cutscene_time == 6750
    # timeout when the clock never reaches the chooser
    cs, s, p, ex, clock = make_world(msg=-1, time=100, dave_time=0)
    ex.tick = lambda: None
    assert cs.wait_ready_for_chooser(timeout=0.5) is False
    # left the intro scene
    s.poke_i32(LAWN + APP_GAME_SCENE, 3)
    assert cs.wait_ready_for_chooser(timeout=0.5) is False
    # skip=False: a dialog is reported, not advanced
    cs, s, p, ex, clock = make_world()
    assert cs.wait_ready_for_chooser(timeout=1.0, skip=False) is False and ex.calls == []
    # bounded while skipping
    cs, s, p, ex, clock = make_world(endless=True)
    with pytest.raises(CutsceneDialogError):
        cs.wait_ready_for_chooser(timeout=5.0, max_steps=3)
    assert len(ex.advance_calls) == 3
    rep = cs.report()
    assert rep["calls"] == 3 and rep["active_dialog"] is True and rep["dave_state_name"] == "idling"


# --- MemoryChooser refuses during the talk ---------------------------------------------
def _dave(mc_session, msg):
    mc_session.poke_i32(LAWN + APP_GAME_SCENE, 2)
    mc_session.poke_i32(LAWN + APP_DAVE_MESSAGE, msg)
    mc_session.poke_u32(BOARD + 0x174, CUTSCENE)


def test_memory_chooser_start_refuses_during_dialog():
    mc, s, p, ex, clock = make_chooser_world(bank=(1, 16, 0, 3), slots=4)
    _dave(s, 201)
    assert mc.dialog_active() is True
    with pytest.raises(ChooserDialogError) as ei:
        mc.start(wait=0.3)
    assert ei.value.dialog_suspected and "戴夫对话未过" in str(ei.value)
    assert isinstance(ei.value, ChooserError)
    assert ex.calls == []          # OnStartButton never called
    with pytest.raises(ChooserDialogError):
        mc.select_and_start([1, 16, 0, 3], wait=0.3)
    assert ex.calls == []
    # talk over: start goes through
    s.poke_i32(LAWN + APP_DAVE_MESSAGE, -1)
    assert mc.dialog_active() is False
    assert mc.start(wait=0.3) is True
    assert [n for n, _ in ex.calls] == ["chooser_start"]


def test_memory_chooser_dialog_after_start_is_an_error_not_a_click():
    mc, s, p, ex, clock = make_chooser_world(bank=(1, 16, 0, 3), slots=4, start_ui=2)
    _dave(s, -1)

    class Dialog:
        def __init__(self):
            self.n = 0

        def dialog_active(self):
            self.n += 1
            return self.n > 2      # appears only after the start call

    mc._cutscene = Dialog()
    with pytest.raises(ChooserDialogError):
        mc.start(wait=0.3)
    assert [n for n, _ in ex.calls] == ["chooser_start"]
    # window heuristic path also raises the dialog error (no clicks anywhere)
    mc, s, p, ex, clock = make_chooser_world(bank=(1, 16, 0, 3), slots=4, start_ui=2)
    _dave(s, -1)

    class W:
        def dialog_open(self, expected_ui=None, wait=1.0):
            return True
    mc.window = W()
    with pytest.raises(ChooserDialogError):
        mc.start(wait=0.3)


def test_memory_chooser_accepts_cutscene_double():
    mc, s, p, ex, clock = make_chooser_world(bank=(1, 16, 0, 3), slots=4)
    mc._cutscene = type("D", (), {"dialog_active": lambda self: True})()
    with pytest.raises(ChooserDialogError):
        mc.start(wait=0.3)
    assert ex.calls == []


# --- GameWindow: cutscene signal, Escape only -------------------------------------------
def test_window_dialog_open_uses_cutscene_signal_and_close_is_escape_only():
    import pvztools.control.window as W
    from test_window_chooser import FakeObserver, FakeUser32, make_window

    cs, s, p, ex, _ = make_world()
    obs = FakeObserver(ui=2)
    obs.lawn_ptr = lambda: LAWN
    w, u, _ = make_window(obs=obs, session=s, profile=p)
    assert w.cutscene_dialog_active() is True
    assert w.dialog_open() is True
    assert w.dialog_open(expected_ui=3, wait=0.0) is True
    # Escape does nothing to a talk: close_dialog refuses, posts nothing
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        assert w.close_dialog(expected_ui=2) is False
    assert rec and u.keys == [] and u.posted == []
    # talk over: Escape only, never a click
    s.poke_i32(LAWN + APP_DAVE_MESSAGE, -1)
    assert w.cutscene_dialog_active() is False and w.dialog_open() is False
    obs.ui = 9
    assert w.close_dialog(expected_ui=2) is False
    assert u.keys == [0x1B] and not [m for m in u.posted if m[0] == FakeUser32.WM_LBUTTONDOWN]
    assert not hasattr(W, "DIALOG_BUTTON_CANDIDATES")
    # observer without lawn_ptr: signal off, no crash
    w2, u2, _ = make_window(obs=FakeObserver(ui=2), session=s, profile=p)
    assert w2.cutscene_dialog_active() is False


# --- play: cutscene step before the chooser, no fallback on a dialog ---------------------
class FakeCutscene:
    def __init__(self, game, lines=2, ready=True, fail=None, stuck=False):
        self.game = game
        self.lines = lines
        self.ready = ready
        self.fail = fail
        self.stuck = stuck
        self.log = game.log

    def state(self):
        return {"message_index": 201 if self.lines else -1}

    def dialog_active(self):
        return self.lines > 0

    def skip_dialog(self, max_steps=10):
        if self.fail is not None:
            raise self.fail
        n = self.lines
        if not self.stuck:
            self.lines = 0
        self.log.append(("skip", n))
        return n

    def wait_ready_for_chooser(self, timeout):
        self.log.append(("wait_ready", timeout))
        return self.ready

    def report(self):
        return {"dave_state_name": "idling" if self.lines else "off", "calls": 2}


class OrderedChooser(FakeMemChooser):
    def select_and_start(self, types, wait=5.0):
        self.game.log.append(("chooser", list(types)))
        return super().select_and_start(types, wait)


def test_play_navigate_skips_dialog_before_chooser():
    g = FakeGame(ui=2)
    ps = play_session(g, cutscene_factory=lambda game: FakeCutscene(game),
                      chooser_factory=lambda game: OrderedChooser(game), seeds=[1, 2])
    ps.run()
    kinds = [e[0] for e in g.log]
    assert kinds[:3] == ["skip", "wait_ready", "chooser"]
    assert g.log[0] == ("skip", 2)
    st = [s for s in ps.steps if s["name"] == "cutscene"][0]
    assert st["steps"] == 2 and st["ready"] is True and st["state"]["dave_state_name"] == "off"
    assert ps.report.meta["cutscene"]["dave_state_name"] == "off"
    # the step runs before the memory chooser is even built
    names = [s["name"] for s in ps.steps]
    assert names.index("cutscene") < names.index("chooser")


def test_play_stops_when_dialog_persists_no_click_fallback():
    g = FakeGame(ui=2, input=True)
    ps = play_session(g, cutscene_factory=lambda game: FakeCutscene(game, fail=CutsceneDialogError("stuck")),
                      chooser_factory=lambda game: OrderedChooser(game))
    with pytest.raises(PlayError) as ei:
        ps.run()
    assert "不盲点" in str(ei.value) and isinstance(ei.value.__cause__, CutsceneDialogError)
    assert g.chooser.picks == [] and not [e for e in g.log if e[0] == "chooser"]
    assert g.ui == 2
    # not ready / still active after the skip: stop as well
    g = FakeGame(ui=2, input=True)
    ps = play_session(g, cutscene_factory=lambda game: FakeCutscene(game, ready=False, stuck=True),
                      chooser_factory=lambda game: OrderedChooser(game))
    with pytest.raises(PlayError) as ei:
        ps.run()
    assert "疑似仍有戴夫对话" in str(ei.value) and g.chooser.picks == []


def test_play_memory_chooser_dialog_error_does_not_fall_back():
    g = FakeGame(ui=2, input=True)
    ps = play_session(g, cutscene_factory=lambda game: FakeCutscene(game, lines=0),
                      chooser_factory=lambda game: FakeMemChooser(game, fail=ChooserDialogError("戴夫对话未过")))
    with pytest.raises(PlayError) as ei:
        ps.run()
    assert "不回退点击选卡" in str(ei.value) and isinstance(ei.value.__cause__, ChooserDialogError)
    assert g.chooser.picks == []          # click chooser never used
    kinds = [s["result"] for s in ps.steps if s["name"] == "chooser"]
    assert len(kinds) == 1 and kinds[0].startswith("memory failed (dialog suspected)")
    # an ordinary chooser failure still falls back (unchanged behaviour)
    g = FakeGame(ui=2, input=True)
    ps = play_session(g, cutscene_factory=lambda game: FakeCutscene(game, lines=0),
                      chooser_factory=lambda game: FakeMemChooser(game, fail=RuntimeError("boom")), seeds=[1, 2])
    ps.run()
    assert g.chooser.picks == [1, 2]


def test_play_click_chooser_never_retries_with_close_dialog():
    g = FakeGame(ui=2, remote=False, input=True)
    calls = []

    class StepChooser:
        def _require_chooser(self, action):
            calls.append(("require", action))

        def bank_size(self):
            return 4

        def fill_types(self, types, size=None):
            return list(types)[:size]

        def clear_bank(self):
            calls.append("clear")

        def pick(self, ids):
            calls.append(("pick", list(ids)))

        def bank_types(self):
            return [1, 2, 3, 4]

        def start(self, wait=5.0, retry=True):
            calls.append(("start", retry))
            g.ui = 3
            return True

        def select_and_start(self, types, wait=5.0):
            raise AssertionError("play must drive the steps itself (retry=False)")

    g.chooser = StepChooser()
    ps = play_session(g, chooser_factory=None, collector_factory=None, seeds=[1, 2, 3, 4])
    ps.run()
    assert ("start", False) in calls and "clear" in calls


def test_play_without_cutscene_support_continues():
    g = FakeGame(ui=2)

    def broken(game):
        raise CutsceneError("unsupported")
    ps = play_session(g, cutscene_factory=broken, chooser_factory=lambda game: OrderedChooser(game))
    ps.run()
    assert [e[0] for e in g.log][0] == "chooser"
    assert any("cutscene helper unavailable" in n for n in ps.notes)
    assert not [s for s in ps.steps if s["name"] == "cutscene"]
    # state unreadable (fake observer): no dialog handling, chooser proceeds
    g = FakeGame(ui=2)

    class Unreadable(FakeCutscene):
        def state(self):
            raise AttributeError("no lawn_ptr")
    ps = play_session(g, cutscene_factory=lambda game: Unreadable(game), chooser_factory=lambda game: OrderedChooser(game))
    ps.run()
    assert [e[0] for e in g.log][0] == "chooser" and any("unreadable" in n for n in ps.notes)


def test_play_cutscene_reaching_level_skips_chooser():
    g = FakeGame(ui=2)

    class ToLevel(FakeCutscene):
        def wait_ready_for_chooser(self, timeout):
            self.game.ui = 3
            return False
    ps = play_session(g, cutscene_factory=lambda game: ToLevel(game, lines=0),
                      chooser_factory=lambda game: OrderedChooser(game))
    ps.run()
    assert not [e for e in g.log if e[0] == "chooser"]


def test_crazy_dave_message_index_only_on_verified_profiles_or_profile_offset():
    cs, s, p, ex, _ = make_world()
    assert crazy_dave_message_index(s, p, LAWN) == 201
    # same family, offset not verified on this build: unknown, not a guess
    other = type("P", (), {"id": "goty_1_2_0_1073_en", "conventions": "goty", "functions": {},
                           "board": p.board, "image_base": 0x400000})()
    assert crazy_dave_message_index(s, other, LAWN) is None
    # a profile that carries the offset is trusted
    s.poke_i32(LAWN + 0x123, 1503)
    withoff = type("P", (), {"id": "goty_1_2_0_1073_en", "conventions": "goty", "functions": {},
                             "board": p.board, "lawn_app": {"crazy_dave_message_index": 0x123}})()
    assert crazy_dave_message_index(s, withoff, LAWN) == 1503
    # zh / ja families excluded as before
    zh = type("P", (), {"id": "goty_zh2010_ja_x", "conventions": "goty_zh2010_ja", "functions": {}})()
    assert crazy_dave_message_index(s, zh, LAWN) is None
