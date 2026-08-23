"""GameWindow (scaling, calibration, focus, dialogs) and SeedChooser with
fake user32 / session / observer."""
import struct
import warnings

import pytest

from pvztools.control import chooser as CH
from pvztools.control import input_backend as IB
from pvztools.control import window as W
from pvztools.control.backends import BackendError
from pvztools.model.types import Seed
from pvztools.modifier.safety import Safety

DOWN, UP, MOVE = IB._user32.WM_LBUTTONDOWN, IB._user32.WM_LBUTTONUP, IB._user32.WM_MOUSEMOVE
KEYDOWN = IB._user32.WM_KEYDOWN


# -- fakes ----------------------------------------------------------------------
class FakeUser32:
    WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = MOVE, DOWN, UP
    WM_KEYDOWN, WM_KEYUP, VK_ESCAPE = KEYDOWN, IB._user32.WM_KEYUP, 0x1B

    def __init__(self, hwnd=0x5000, raw=(640, 480), win_dpi=120, sys_dpi=96, foreground=False,
                 focus_ok=True):
        self.hwnd, self.raw, self.win_dpi, self.sys_dpi = hwnd, raw, win_dpi, sys_dpi
        self.foreground, self.focus_ok = foreground, focus_ok
        self.posted, self.keys, self.focus_calls = [], [], 0
        self.on_move = None

    def find_game_window(self, pid=None):
        return self.hwnd

    def post_message(self, hwnd, msg, wparam=0, lparam=0):
        x, y = lparam & 0xFFFF, (lparam >> 16) & 0xFFFF
        self.posted.append((msg, wparam, x, y))
        if msg == MOVE and self.on_move:
            self.on_move(x, y)

    def post_key(self, hwnd, vk):
        self.keys.append(vk)

    def get_client_rect(self, hwnd):
        return self.raw

    def get_dpi_for_window(self, hwnd):
        return self.win_dpi

    def get_dpi_for_system(self):
        return self.sys_dpi

    def is_foreground(self, hwnd):
        return self.foreground

    def focus_window(self, hwnd):
        self.focus_calls += 1
        if self.focus_ok:
            self.foreground = True
        return self.foreground


class Sec(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


class FakeProfile:
    def __init__(self, prev_mouse=True, dialog_map=None):
        self.board = Sec(prev_mouse_x=0x5570, prev_mouse_y=0x5574) if prev_mouse else Sec()
        self.lawn_app = Sec(dialog_map=dialog_map) if dialog_map is not None else Sec()


class FakeSession:
    pid = 4242

    def __init__(self):
        self.mem = {}

    def read(self, addr, n):
        return bytes(self.mem.get(addr + i, 0) for i in range(n))

    def poke(self, addr, data):
        for i, b in enumerate(data):
            self.mem[addr + i] = b


class FakeGame:
    def __init__(self, ui):
        self.ui = ui


class FakeBoard:
    def __init__(self, scene):
        self.scene = scene


class FakeObserver:
    def __init__(self, ui=2, board=0x100000, lawn=0x200000, seeds=(), scene=0):
        self.ui, self._board, self._lawn, self._seeds, self._scene = ui, board, lawn, list(seeds), scene

    def game(self):
        return FakeGame(self.ui)

    def board_ptr(self):
        return self._board

    def lawn_ptr(self):
        return self._lawn

    def seeds(self):
        return tuple(self._seeds)

    def board(self):
        return FakeBoard(self._scene)


class FakePatches:
    def __init__(self, state="off", fail=False):
        self.state, self.fail, self.enabled = state, fail, []

    def status(self):
        return {"background_running": self.state}

    def enable(self, name):
        if self.fail:
            raise RuntimeError("denied")
        self.enabled.append(name)
        self.state = "on"


def seed(slot, ptype):
    return Seed(slot=slot, type=ptype, imitater_type=-1, cd_elapsed=0, cd_total=0, usable=True)


def make_window(u=None, obs=None, profile=None, session=None, patches=None, safety=None, **kw):
    u = u or FakeUser32()
    obs = obs or FakeObserver()
    profile = profile or FakeProfile()
    session = session or FakeSession()
    safety = safety or Safety(allow_input=True, dry_run=False)
    sleeps = []
    w = W.GameWindow(session, obs, profile, patches, safety, user32=u, sleep=sleeps.append, **kw)
    return w, u, sleeps


# -- scaling -------------------------------------------------------------------
def test_client_size_dpi_corrected_and_ratio_scaling():
    w, u, _ = make_window()
    assert w.raw_client_size() == (640, 480)
    assert w.client_size() == (800, 600)          # 640 * 120/96
    assert w.logical_to_physical(800, 600) == (640, 480)
    assert w.logical_to_physical(100, 70) == (80, 56)
    assert w.ratio_calibration().source == "ratio"
    lx, ly = w.physical_to_logical(80, 56)
    assert (round(lx), round(ly)) == (100, 70)


def test_identity_when_client_unknown_and_resized_window():
    w, u, _ = make_window(u=FakeUser32(raw=(0, 0)))
    assert w.client_size() == (0, 0)
    assert w.logical_to_physical(123, 45) == (123, 45)
    w, u, _ = make_window(u=FakeUser32(raw=(1600, 1200), win_dpi=96))
    assert w.client_size() == (1600, 1200)
    assert w.logical_to_physical(100, 70) == (200, 140)


def test_client_size_prefers_user32_client_size_helper():
    u = FakeUser32()
    u.client_size = lambda hwnd: (800, 600)
    w, _, _ = make_window(u=u)
    assert w.client_size() == (800, 600)


def test_window_discovery_failure():
    w, u, _ = make_window(u=FakeUser32(hwnd=0))
    with pytest.raises(BackendError):
        w.hwnd


# -- calibration ---------------------------------------------------------------
def test_fit_axis():
    a, b = W.fit_axis([128, 320, 512], [160 + 3, 400 + 3, 640 + 3])
    assert abs(a - 1.25) < 1e-9 and abs(b - 3) < 1e-9
    assert W.fit_axis([10, 10, 10], [1, 2, 3]) is None
    assert W.fit_axis([1, 2], [2, 1]) is None          # negative scale
    assert W.fit_axis([1], [1]) is None


def test_calibrate_fits_scale_and_offset_from_prev_mouse():
    u, session = FakeUser32(), FakeSession()
    board = 0x100000

    def game_maps(px, py):  # the game derives logical = 1.25*phys + (2, -1)
        session.poke(board + 0x5570, struct.pack("<ii", int(round(px * 1.25 + 2)), int(round(py * 1.25 - 1))))

    u.on_move = game_maps
    w, _, sleeps = make_window(u=u, session=session)
    cal = w.calibrate()
    assert cal.source == "measured"
    assert abs(cal.scale_x - 1.25) < 0.01 and abs(cal.scale_y - 1.25) < 0.01
    assert abs(cal.offset_x - 2) < 0.6 and abs(cal.offset_y + 1) < 0.6
    moves = [p for p in u.posted if p[0] == MOVE]
    assert len(moves) == 3 and moves[0][2:] == (128, 96)
    # later clicks use the measured mapping
    px, py = w.logical_to_physical(400, 300)
    assert abs(px - (400 - 2) / 1.25) <= 1 and abs(py - (300 + 1) / 1.25) <= 1
    assert w.read_prev_mouse() is not None


def test_fit_axis_rejects_large_residuals():
    # one stale reading (100 instead of ~160): slope looks fine, residual 50 px
    assert W.fit_axis([128, 320, 512], [100, 400, 400]) is not None          # legacy: no check
    assert W.fit_axis([128, 320, 512], [100, 400, 400], max_residual=3.0) is None
    assert W.fit_axis([128, 320, 512], [160, 400, 640], max_residual=3.0) == pytest.approx((1.25, 0.0))


def test_calibrate_rejects_a_stale_reading_and_falls_back_to_ratio():
    u, session = FakeUser32(), FakeSession()
    board = 0x100000
    n = {"moves": 0}

    def game_maps(px, py):
        n["moves"] += 1
        if n["moves"] == 2:
            return  # the second move is never processed (paused / lagging pump)
        session.poke(board + 0x5570, struct.pack("<ii", int(round(px * 1.25)), int(round(py * 1.25))))

    u.on_move = game_maps
    w, _, sleeps = make_window(u=u, session=session)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        cal = w.calibrate()
    assert cal.source == "ratio" and rec and "残差" in str(rec[-1].message)
    assert w.logical_to_physical(800, 600) == (640, 480)
    # the unchanged reading was retried (extra settle waits) before giving up
    assert len(sleeps) == 3 + (W.CALIBRATION_READ_TRIES - 1)


def test_calibrate_rejects_implausible_scale():
    u, session = FakeUser32(), FakeSession()  # ratio scale = 1.25
    # the game reports a consistent but wildly different line (scale 4.0)
    u.on_move = lambda px, py: session.poke(0x100000 + 0x5570, struct.pack("<ii", px * 4, py * 4))
    w, _, _ = make_window(u=u, session=session)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        cal = w.calibrate()
    assert cal.source == "ratio" and rec and "相差过大" in str(rec[-1].message)


def test_calibrate_waits_for_prev_mouse_to_change():
    u, session = FakeUser32(), FakeSession()
    pending = []

    def game_maps(px, py):  # processed only on the next read (one settle late)
        pending.append((px, py))

    u.on_move = game_maps
    w, _, sleeps = make_window(u=u, session=session)
    orig_read = session.read

    def lazy_read(addr, n):
        if pending and len(sleeps) % 2 == 0:  # the pump catches up every other poll
            px, py = pending.pop(0)
            session.poke(0x100000 + 0x5570, struct.pack("<ii", int(round(px * 1.25)), int(round(py * 1.25))))
        return orig_read(addr, n)
    session.read = lazy_read
    cal = w.calibrate()
    assert cal.source == "measured" and abs(cal.scale_x - 1.25) < 0.01


def test_calibrate_falls_back_without_board_or_field():
    w, u, _ = make_window(obs=FakeObserver(board=0))
    cal = w.calibrate()
    assert cal.source == "ratio" and not [p for p in u.posted if p[0] == MOVE]
    w, u, _ = make_window(profile=FakeProfile(prev_mouse=False))
    assert w.calibrate().source == "ratio"
    assert w.read_prev_mouse() is None


def test_calibrate_degenerate_readings_warn_and_fall_back():
    w, u, _ = make_window()      # prev_mouse never changes (all zeros)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        cal = w.calibrate()
    assert cal.source == "ratio" and rec
    assert w.logical_to_physical(800, 600) == (640, 480)


def test_calibrate_dry_run_posts_nothing():
    w, u, _ = make_window(safety=Safety(allow_input=True))
    assert w.calibrate().source == "ratio"
    assert u.posted == [] and len(w.history) == 0


# -- focus / running ------------------------------------------------------------
def test_bring_to_foreground_and_ensure_running():
    w, u, _ = make_window(u=FakeUser32(foreground=False))
    assert not w.is_foreground()
    assert w.bring_to_foreground() is True and u.focus_calls == 1
    assert w.ensure_running() == "foreground"
    assert w.bring_to_foreground() is True and u.focus_calls == 1   # already front


def test_ensure_running_enables_patch_or_warns():
    pm = FakePatches("off")
    w, u, _ = make_window(u=FakeUser32(foreground=False), patches=pm,
                          safety=Safety(allow_input=True, allow_patch=True, allow_write=True, dry_run=False))
    assert w.ensure_running() == "patched" and pm.enabled == ["background_running"]
    assert w.ensure_running() == "patched" and pm.enabled == ["background_running"]  # idempotent
    # no patch permission -> warn
    w, u, _ = make_window(u=FakeUser32(foreground=False), patches=FakePatches("off"))
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        assert w.ensure_running() == "unfocused"
    assert rec
    # enable failure -> warn
    w, u, _ = make_window(u=FakeUser32(foreground=False), patches=FakePatches("off", fail=True),
                          safety=Safety(allow_input=True, allow_patch=True, allow_write=True, dry_run=False))
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        assert w.ensure_running() == "unfocused"
    assert len(rec) == 2
    assert w.background_running() is False
    w, _, _ = make_window()
    assert w.background_running() is None


# -- dialogs ---------------------------------------------------------------------
def test_dialog_count_from_profile_and_heuristic():
    session = FakeSession()
    session.poke(0x200000 + 0x324 + 4, struct.pack("<I", 1))
    w, u, _ = make_window(session=session, profile=FakeProfile(dialog_map=0x324))
    assert w.dialog_count() == 1 and w.dialog_open() is True
    session.poke(0x200000 + 0x324 + 4, struct.pack("<I", 0))
    assert w.dialog_open() is False
    # no offset known: heuristic on ui
    t = [0.0]
    w, u, _ = make_window(obs=FakeObserver(ui=2), clock=lambda: t[0])
    w._sleep = lambda s: t.__setitem__(0, t[0] + s)
    assert w.dialog_count() is None
    assert w.dialog_open() is False
    assert w.dialog_open(expected_ui=3, wait=1.0) is True
    assert w.dialog_open(expected_ui=2, wait=1.0) is False


def test_close_dialog_is_escape_only_no_blind_clicks():
    obs = FakeObserver(ui=2)
    w, u, _ = make_window(obs=obs)
    assert not hasattr(W, "DIALOG_BUTTON_CANDIDATES")
    assert w.close_dialog(expected_ui=2) is True          # already in the expected ui
    assert u.keys == [0x1B] and not [p for p in u.posted if p[0] == DOWN]
    # stays "open": Escape once more, never a click, False
    obs.ui = 9
    u.keys.clear()
    u.posted.clear()
    assert w.close_dialog(expected_ui=2) is False
    assert u.keys == [0x1B] and not [p for p in u.posted if p[0] == DOWN]
    # a Crazy Dave talk is not a LawnDialog: refused with a warning, nothing posted
    u.keys.clear()
    w.cutscene_dialog_active = lambda: True
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        assert w.close_dialog(expected_ui=2) is False
    assert rec and u.keys == [] and u.posted == []


def test_click_logical_posts_scaled_down_up():
    w, u, _ = make_window()
    assert w.click_logical(100, 70, move=True) == (80, 56)
    assert [p[0] for p in u.posted] == [MOVE, DOWN, UP]
    assert u.posted[1][2:] == (80, 56) and u.posted[1][1] == 1


# -- InputBackend uses the window ------------------------------------------------
def test_input_backend_scales_through_window_and_brings_foreground():
    u = FakeUser32(foreground=False)
    obs = FakeObserver(ui=3, seeds=[seed(0, 1)])
    be = IB.InputBackend(FakeSession(), obs, FakeProfile(), Safety(allow_input=True, dry_run=False),
                         user32=u, sleep=lambda s: None)
    be.click(100, 70)
    assert u.focus_calls == 1 and u.foreground
    assert [p[:1] + p[2:] for p in u.posted] == [(DOWN, 80, 56), (UP, 80, 56)]
    assert be.history == [(DOWN, 100, 70), (UP, 100, 70)]
    assert be.client_size() == (800, 600)
    assert be.click_logical(0, 0) == (0, 0)
    assert be.hwnd == u.hwnd


def test_input_backend_foreground_false_uses_patch_and_warns_once():
    pm = FakePatches("off")
    u = FakeUser32(foreground=False)
    be = IB.InputBackend(FakeSession(), FakeObserver(ui=3), FakeProfile(),
                         Safety(allow_input=True, allow_patch=True, allow_write=True, dry_run=False),
                         user32=u, sleep=lambda s: None, patches=pm, foreground=False)
    be.click(1, 1)
    assert u.focus_calls == 0 and pm.enabled == ["background_running"]
    # without patches: one warning for several actions
    u = FakeUser32(foreground=False)
    be = IB.InputBackend(FakeSession(), FakeObserver(ui=3), FakeProfile(), Safety(allow_input=True, dry_run=False),
                         user32=u, sleep=lambda s: None, foreground=False)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        be.click(1, 1)
        be.click(2, 2)
    assert len(rec) == 1


def test_input_backend_calibrate_uses_measured_mapping():
    u, session = FakeUser32(), FakeSession()
    u.on_move = lambda px, py: session.poke(0x100000 + 0x5570, struct.pack("<ii", int(px * 1.25), int(py * 1.25)))
    be = IB.InputBackend(session, FakeObserver(ui=2), FakeProfile(), Safety(allow_input=True, dry_run=False),
                         user32=u, sleep=lambda s: None)
    assert be.calibrate().source == "measured"
    u.posted.clear()
    be.click(400, 300)
    assert u.posted[0][2:] == (320, 240)


# -- chooser geometry ------------------------------------------------------------
def test_chooser_card_and_bank_geometry():
    assert CH.chooser_card_xy(0) == (22 + 25, 128 + 35)
    assert CH.chooser_card_xy(9) == (1 * 53 + 22 + 25, 1 * 73 + 128 + 35)
    assert CH.chooser_card_xy(16, has7rows=True) == (22 + 25, 2 * 70 + 123 + 35)
    with pytest.raises(BackendError):
        CH.chooser_card_xy(48)
    # 8-slot bank: i*54+81
    assert [CH.bank_packet_xy(i, 8)[0] for i in range(8)] == [i * 54 + 81 + 25 for i in range(8)]
    assert CH.bank_packet_xy(0, 8) == (106, 43)
    assert CH.bank_packet_xy(2, 7) == (2 * 59 + 85 + 25, 43)
    assert CH.bank_packet_xy(1, 9) == (1 * 52 + 80 + 25, 43)
    assert CH.bank_packet_xy(1, 10) == (1 * 51 + 79 + 25, 43)
    assert CH.LETS_ROCK_XY == (232, 566)


def make_chooser(ui=2, seeds=None, scene=0, u=None, profile=None, session=None):
    u = u or FakeUser32(foreground=True)
    seeds = [seed(i, t) for i, t in enumerate(seeds if seeds is not None else [-1] * 8)]
    obs = FakeObserver(ui=ui, seeds=seeds, scene=scene)
    w, _, _ = make_window(u=u, obs=obs, profile=profile, session=session)
    t = [0.0]
    c = CH.SeedChooser(w, obs, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    return c, w, u, obs, t


FULL_BANK = [1, 2, 3, 4, 5, 6, 7, 8]


def test_clear_bank_clicks_slot0_n_times_with_gaps():
    c, w, u, obs, t = make_chooser(seeds=[1, 2, 3, -1, -1, -1, -1, -1])
    assert c.clear_bank() == 3
    assert c.clicks == [CH.bank_packet_xy(0, 8)] * 3
    assert abs(t[0] - 3 * 0.35) < 1e-6           # three 0.35 s gaps
    assert c.clear_bank(8) == 8


def test_pick_and_fill_types():
    c, w, u, obs, t = make_chooser(scene=2)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        ids = c.fill_types(["cob cannon", 47, "樱桃炸弹"])
    assert rec and ids[:2] == [47, 2] and len(ids) == 8
    assert 1 in ids and 16 in ids               # sunflower + lily pad on pool
    c2, *_ = make_chooser(scene=0)
    ids2 = c2.fill_types([0])
    assert 16 not in ids2 and ids2[0] == 0 and len(ids2) == 8
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        assert len(c2.fill_types(list(range(9)))) == 8
    assert rec
    assert c.pick(["peashooter", 16]) == [0, 16]
    assert c.clicks == [CH.chooser_card_xy(0), CH.chooser_card_xy(16)]
    phys = [p for p in u.posted if p[0] == DOWN]
    assert phys[0][2:] == w.logical_to_physical(*CH.chooser_card_xy(0))


def test_requires_chooser_ui():
    c, *_ = make_chooser(ui=3)
    with pytest.raises(BackendError):
        c.pick([0])
    with pytest.raises(BackendError):
        c.start()


def test_start_polls_ui_and_dialog_path():
    c, w, u, obs, t = make_chooser(seeds=FULL_BANK)
    # ui flips to 3 after 0.3 s
    obs.game = lambda: FakeGame(3 if t[0] >= 0.3 else 2)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        assert c.start(wait=5.0) is True and c.clicks == [CH.LETS_ROCK_XY]
    assert not rec  # full bank, started: nothing to warn about
    # stuck with a real dialog (dialog_map known, count 1): Escape, then retry once
    session = FakeSession()
    session.poke(0x200000 + 0x324 + 4, struct.pack("<I", 1))
    c, w, u, obs, t = make_chooser(seeds=FULL_BANK, profile=FakeProfile(dialog_map=0x324), session=session)
    state = {"stuck": True}

    def game():
        if state["stuck"]:
            return FakeGame(2)
        return FakeGame(3)
    obs.game = game

    def on_post(hwnd, msg, wparam=0, lparam=0):
        FakeUser32.post_message(u, hwnd, msg, wparam, lparam)
        if msg == DOWN and w.dialog_count() == 0:
            state["stuck"] = False      # the retry click (no dialog) "works"
    u.post_message = on_post
    orig_close = w.close_dialog

    def close(**kw):
        r = orig_close(**kw)
        session.poke(0x200000 + 0x324 + 4, struct.pack("<I", 0))  # dialog gone
        return r
    w.close_dialog = close
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        assert c.start(wait=0.5) is True
    assert rec and u.keys == [0x1B]
    assert c.clicks == [CH.LETS_ROCK_XY, CH.LETS_ROCK_XY]
    # hopeless: stays in the chooser
    c, w, u, obs, t = make_chooser(seeds=FULL_BANK)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        assert c.start(wait=0.2) is False
    assert rec and "dialog_map" in str(rec[-1].message)


def test_start_without_dialog_count_never_posts_a_blind_escape():
    """No ``lawn_app.dialog_map`` in the profile: an unchanged ui is not
    evidence of a dialog -- no Escape, no candidate clicks, one click only."""
    c, w, u, obs, t = make_chooser(seeds=FULL_BANK)  # FakeProfile: no dialog_map
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        assert c.start(wait=0.2) is False
    assert u.keys == [] and c.clicks == [CH.LETS_ROCK_XY]
    assert [p for p in u.posted if p[0] == DOWN] == [p for p in u.posted if p[0] == DOWN][:1]
    assert any("dialog_map" in str(r.message) for r in rec)


def test_start_with_a_partial_bank_warns_and_does_not_escape():
    c, w, u, obs, t = make_chooser(seeds=[1, 2, -1, -1, -1, -1, -1, -1])
    assert c.bank_full() is False
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        assert c.start(wait=0.1) is False
    assert u.keys == []
    assert any("2/8" in str(r.message) for r in rec)
    c2, *_ = make_chooser(seeds=FULL_BANK)
    assert c2.bank_full() is True


def test_start_with_dialog_count_zero_does_not_escape():
    session = FakeSession()  # dialog_map known, count 0
    c, w, u, obs, t = make_chooser(seeds=FULL_BANK, profile=FakeProfile(dialog_map=0x324), session=session)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        assert c.start(wait=0.1) is False
    assert u.keys == [] and c.clicks == [CH.LETS_ROCK_XY]


def test_select_and_start_end_to_end():
    c, w, u, obs, t = make_chooser(seeds=[5, 6, -1, -1, -1, -1, -1, -1], scene=2)
    picked = []

    def click_logical(x, y, **kw):
        picked.append((x, y))
        if (x, y) == CH.LETS_ROCK_XY:
            obs.ui = 3
        return w.logical_to_physical(x, y)
    w.click_logical = click_logical
    chosen = [1, 16, 47, 2]
    real_pick = c.pick

    def pick(types):
        ids = real_pick(types)
        obs._seeds = [seed(i, t) for i, t in enumerate(ids)]
        return ids
    c.pick = pick
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        assert c.select_and_start(chosen) is True
    assert picked[:2] == [CH.bank_packet_xy(0, 8)] * 2           # two filled slots cleared
    assert picked[2:6] == [CH.chooser_card_xy(i) for i in chosen]
    assert picked[-1] == CH.LETS_ROCK_XY and len(picked) == 2 + 8 + 1


def test_select_and_start_detects_missing_cards():
    c, w, u, obs, t = make_chooser()
    with pytest.raises(BackendError):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            c.select_and_start([0, 1])       # bank stays empty in the fake


# -- foreground handling: one steal per backend / chooser ------------------------
def test_input_backend_steals_focus_once_not_per_action():
    u = FakeUser32(foreground=False)
    sleeps = []
    be = IB.InputBackend(FakeSession(), FakeObserver(ui=3, seeds=[seed(0, 1)]), FakeProfile(),
                         Safety(allow_input=True, dry_run=False), user32=u, sleep=sleeps.append)
    be.click(1, 1)
    assert u.focus_calls == 1 and 0.3 in sleeps
    u.foreground = False            # the user alt-tabbed away again
    n = len(sleeps)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        be.click(2, 2)
        be.click(3, 3)
    assert u.focus_calls == 1        # no re-steal, no 0.3 s settle per action
    assert 0.3 not in sleeps[n:]


def test_input_backend_skips_focus_steal_when_patch_is_on():
    pm = FakePatches("on")
    u = FakeUser32(foreground=False)
    be = IB.InputBackend(FakeSession(), FakeObserver(ui=3), FakeProfile(),
                         Safety(allow_input=True, allow_patch=True, allow_write=True, dry_run=False),
                         user32=u, sleep=lambda s: None, patches=pm, foreground=True)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        be.click(1, 1)
    assert u.focus_calls == 0 and not rec


def test_ensure_running_foreground_flag():
    w, u, _ = make_window(u=FakeUser32(foreground=False))
    assert w.ensure_running(foreground=True) == "foreground" and u.focus_calls == 1
    w, u, _ = make_window(u=FakeUser32(foreground=False, focus_ok=False), patches=FakePatches("off"),
                          safety=Safety(allow_input=True, allow_patch=True, allow_write=True, dry_run=False))
    assert w.ensure_running(foreground=True) == "patched" and u.focus_calls == 1
    w, u, _ = make_window(u=FakeUser32(foreground=False), patches=FakePatches("on"))
    assert w.ensure_running(foreground=True) == "patched" and u.focus_calls == 0  # patch on: no steal


def test_chooser_brings_window_to_front_once_and_honours_foreground_false():
    u = FakeUser32(foreground=False)
    c, w, u, obs, t = make_chooser(seeds=[1, 2, -1, -1, -1, -1, -1, -1], u=u)
    c.clear_bank()
    c.clear_bank()
    assert u.focus_calls == 1
    u = FakeUser32(foreground=False)
    c, w, u, obs, t = make_chooser(seeds=[1, -1, -1, -1, -1, -1, -1, -1], u=u)
    c.foreground = False
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        c.clear_bank()
    assert u.focus_calls == 0 and rec


def test_game_chooser_passes_foreground_flag():
    from pvztools import game as G
    import inspect
    src = inspect.getsource(G.Game.chooser.fget)
    assert "foreground=self._foreground" in src
