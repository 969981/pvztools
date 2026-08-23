"""GUI 工作线程（pvztools.gui.worker）的无界面测试。

worker 不依赖 imgui-bundle，可在任何环境跑；用 FakeGame 验证
快照轮询、命令派发、协作式停止与断线自愈。
"""

from __future__ import annotations

import threading
import time

import pytest

from pvztools.gui.worker import CancelledOp, GameWorker, Snapshot


class FakeCheats:
    def __init__(self) -> None:
        self.no_cooldown = False
        self.free_planting = False
        self.speed = 1.0
        self.calls = []

    def status(self):
        return {
            "no_cooldown": self.no_cooldown,
            "free_planting": self.free_planting,
            "speed": self.speed,
        }

    def kill_zombies(self):
        self.calls.append("kill")
        return 7


class FakeGame:
    def __init__(self) -> None:
        self.pid = 1234
        self.version = "goty_1_2_0_1096_en"
        self.capability = "FULL"
        self.ui = 3
        self.ui_name = "level"
        self.in_level = True
        self.sun = 50
        self.wave = 2
        self.total_waves = 20
        self.clock = 1000
        self.paused = False
        self.cheats = FakeCheats()
        self.closed = False
        self.dead = False

    def _check(self):
        if self.dead:
            raise OSError("process gone")

    def __getattribute__(self, name):
        # 模拟读内存失败:游戏退出后任何属性读都抛 OSError
        if name not in ("dead", "_check", "close", "closed") and not name.startswith("__"):
            if object.__getattribute__(self, "dead"):
                raise OSError("process gone")
        return object.__getattribute__(self, name)

    def close(self):
        self.closed = True

    def collect_all(self, types=None):
        return 3


def make_worker(game=None, **kw):
    game = game if game is not None else FakeGame()
    w = GameWorker(
        attach_fn=lambda pid=None, **flags: game,
        find_fn=lambda: [],
        auto_attach=False,
        **kw,
    )
    return w, game


def drain(w, n=3):
    for _ in range(n):
        w.tick()


def test_attach_and_snapshot():
    w, g = make_worker()
    w.submit("attach")
    drain(w)
    snap = w.snapshot
    assert snap.attached is True
    assert snap.version == "goty_1_2_0_1096_en"
    assert snap.sun == 50
    assert snap.wave == 2
    assert snap.cheats["no_cooldown"] is False


def test_set_sun_and_toggle():
    w, g = make_worker()
    w.submit("attach")
    w.submit("set_sun", value=9990)
    w.submit("toggle", name="no_cooldown", value=True)
    drain(w, 5)
    assert g.sun == 9990
    assert g.cheats.no_cooldown is True
    assert w.snapshot.cheats["no_cooldown"] is True


def test_speed_and_kill():
    w, g = make_worker()
    w.submit("attach")
    w.submit("speed", value=5.0)
    w.submit("kill_zombies")
    drain(w, 5)
    assert g.cheats.speed == 5.0
    assert "kill" in g.cheats.calls


def test_detach_on_read_failure():
    w, g = make_worker()
    w.submit("attach")
    drain(w)
    assert w.snapshot.attached
    g.dead = True
    drain(w)
    assert w.snapshot.attached is False
    assert w.game is None


def test_unknown_command_logged_not_fatal():
    w, g = make_worker()
    w.submit("does_not_exist")
    drain(w)
    assert any("未知命令" in msg for _, msg in w.log)


def test_command_error_goes_to_log():
    w, g = make_worker()
    # 未 attach 就写阳光 -> RuntimeError 进日志,不炸
    w.submit("set_sun", value=1)
    drain(w)
    assert any("set_sun 失败" in msg for _, msg in w.log)
    assert w.snapshot.error != ""


def test_coop_sleep_cancels():
    w, _ = make_worker()
    w._stop_op.set()
    with pytest.raises(CancelledOp):
        w._coop_sleep(1.0)


def test_coop_sleep_normal_wait():
    w, _ = make_worker()
    t0 = time.monotonic()
    w._coop_sleep(0.05)
    assert time.monotonic() - t0 >= 0.04


def test_long_op_stop_via_injected_sleep():
    """campaign 收到注入的 coop sleep;停止事件让它在间隙抛 CancelledOp。"""
    w, g = make_worker()

    def fake_campaign(n=1, auto=False, on_event=None, quiet=True, sleep=None, **kw):
        for _ in range(100):
            sleep(0.02)
        raise AssertionError("should have been cancelled")

    g.campaign = fake_campaign
    w.submit("attach")
    drain(w)

    done = threading.Event()

    def run_cmd():
        w._run_cmd("campaign", {"n": 1})
        done.set()

    t = threading.Thread(target=run_cmd, daemon=True)
    t.start()
    time.sleep(0.05)
    w.stop_current()
    assert done.wait(2.0), "campaign 未能被停止"
    assert any("已停止" in msg for _, msg in w.log)
    assert w._busy == ""


def test_busy_published_during_long_op_and_memory_not_polled():
    """生产路径:长任务开始即发布 busy 快照;期间 _refresh 不读内存。"""
    w, g = make_worker()
    observed = {}

    def fake_campaign(n=1, auto=False, on_event=None, quiet=True, sleep=None, **kw):
        observed["busy_during"] = w.snapshot.busy
        g.dead = True  # 若此后 _refresh 读内存就会断开
        w._refresh()
        observed["attached_after_refresh"] = w.snapshot.attached
        g.dead = False
        on_event({"event": "level", "n": 1})
        observed["progress_during"] = w.snapshot.progress

    g.campaign = fake_campaign
    w.submit("attach")
    drain(w)
    w._run_cmd("campaign", {"n": 1})
    assert observed["busy_during"] == "campaign"
    assert observed["attached_after_refresh"] is True
    assert "[level]" in observed["progress_during"]
    assert w.snapshot.busy == ""  # 结束后回落


def test_multiple_candidates_no_auto_attach():
    class P:
        def __init__(self, pid):
            self.pid = pid

    attach_calls = []
    w = GameWorker(
        attach_fn=lambda pid=None, **flags: attach_calls.append(pid),
        find_fn=lambda: [P(11), P(22)],
        auto_attach=True,
        scan_interval=0.0,
    )
    w.tick()
    assert w.snapshot.candidates == (11, 22)
    assert attach_calls == []


def test_single_candidate_auto_attaches():
    class P:
        def __init__(self, pid):
            self.pid = pid

    g = FakeGame()
    seen = []

    def attach(pid=None, **flags):
        seen.append((pid, flags))
        return g

    w = GameWorker(attach_fn=attach, find_fn=lambda: [P(42)], scan_interval=0.0)
    w.tick()
    w.tick()
    assert seen and seen[0][0] == 42
    assert w.snapshot.attached


def test_readonly_mode_attach_flags():
    seen = {}

    def attach(pid=None, **flags):
        seen.update(flags)
        return FakeGame()

    w = GameWorker(attach_fn=attach, find_fn=lambda: [], auto_attach=False, mode="readonly")
    w.submit("attach")
    w.tick()
    assert seen.get("write") is None or "write" not in seen
    assert seen.get("foreground") is False


def test_full_mode_attach_flags():
    seen = {}

    def attach(pid=None, **flags):
        seen.update(flags)
        return FakeGame()

    w = GameWorker(attach_fn=attach, find_fn=lambda: [], auto_attach=False, mode="full")
    w.submit("attach")
    w.tick()
    assert seen.get("write") is True
    assert seen.get("remote") is True
    assert seen.get("input") is True
    assert seen.get("foreground") is False


def test_shutdown_joins():
    w, _ = make_worker()
    w.start()
    w.shutdown(timeout=2.0)
    assert not w.is_alive()


def test_strings_bilingual():
    from pvztools.gui.strings import GUI_TEXT, tr

    for key, entry in GUI_TEXT.items():
        assert "zh" in entry and "en" in entry, key
    assert isinstance(tr("sun"), str)
    assert tr("no_such_key") == "no_such_key"


def test_theme_palettes():
    from pvztools.gui import theme

    for pal in (theme.FOREST_DARK, theme.FOREST_LIGHT):
        for name, rgba in pal.items():
            assert len(rgba) == 4, name
            assert all(0.0 <= c <= 1.0 for c in rgba), name
    assert theme.DEFAULT_THEME in theme.THEMES


def test_auto_scan_attach_failure_does_not_kill_worker():
    """自动附加抛 ControllerBusy 等异常:进日志、退避重试,线程不死。"""
    class P:
        def __init__(self, pid):
            self.pid = pid

    calls = []

    def attach(pid=None, **flags):
        calls.append(pid)
        raise RuntimeError("ControllerBusy: another controller")

    w = GameWorker(attach_fn=attach, find_fn=lambda: [P(7)], scan_interval=0.0)
    for _ in range(5):
        w.tick()  # 不应抛出
    assert calls, "应尝试过附加"
    assert any("attach 失败" in msg for _, msg in w.log)
    assert w.snapshot.attached is False
    assert w._scan_backoff > w.scan_interval  # 失败后退避


def test_detach_disables_auto_attach():
    class P:
        def __init__(self, pid):
            self.pid = pid

    g = FakeGame()
    w = GameWorker(attach_fn=lambda pid=None, **f: g, find_fn=lambda: [P(1)], scan_interval=0.0)
    w.tick()
    assert w.snapshot.attached
    w.submit("detach")
    drain(w)
    assert w.snapshot.attached is False
    assert w.auto_attach is False  # 不会 2 秒后自己连回去
    w.submit("attach")
    drain(w)
    assert w.snapshot.attached and w.auto_attach is True


def test_set_mode_via_command_and_snapshot_shows_attached_mode():
    w, g = make_worker()
    w.submit("attach")
    drain(w)
    assert w.snapshot.mode == "full"
    w.submit("set_mode", mode="readonly")
    drain(w)
    # 还没重新附加:快照仍显示实际附加时的模式
    assert w.snapshot.mode == "full"
    assert w.mode == "readonly"
    w.submit("attach")
    drain(w)
    assert w.snapshot.mode == "readonly"


def test_transient_level_exit_does_not_detach():
    """读关卡数据抛非进程级异常(如 NotInLevel):退化为不在关卡,不断开。"""
    w, g = make_worker()
    w.submit("attach")
    drain(w)

    def boom(self):
        raise ValueError("board vanished mid-read")

    type(g).sun = property(boom)
    del g.__dict__["sun"]
    try:
        w._refresh()
    finally:
        del type(g).sun
        g.sun = 50
    assert w.snapshot.attached is True
    assert w.snapshot.in_level is False


def test_cheat_cache_survives_outside_level():
    """关卡外 status() 读不到的键由会话缓存兜底,勾选不回跳。"""
    w, g = make_worker()
    w.submit("attach")
    w.submit("toggle", name="background", value=True)
    drain(w, 5)
    g.in_level = False
    g.cheats.status = lambda: {}  # 关卡外读不到任何键
    w._refresh()
    assert w.snapshot.cheats.get("background") is True


def test_play_level_gets_coop_sleep():
    w, g = make_worker()
    seen = {}

    def fake_play(**kw):
        seen.update(kw)
        class R:
            def summary(self):
                return "ok"
        return R()

    g.play_level = fake_play
    w.submit("attach")
    w.submit("play_level")
    drain(w, 5)
    assert seen.get("sleep") == w._coop_sleep
    assert seen.get("quiet") is True


def test_shutdown_closes_game():
    w, g = make_worker()
    w.submit("attach")
    drain(w)
    w.start()
    w.shutdown(timeout=3.0)
    assert not w.is_alive()
    assert g.closed is True  # 互斥/补丁随 close 释放


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _game_with_profile():
    g = FakeGame()
    g.place_calls = []
    g.place_lineup = lambda target, **kw: (g.place_calls.append(target) or f"placed {target}")
    prof = _NS(
        image_base=0x400000,
        id="goty_1_2_0_1096_en",
        globals=_NS(lawn_app=0x731C50),
        lawn_app={"board": 0x868},
        board={"sun": 0x5578},
        functions={"put_plant": _NS(rva=0x10560), "put_zombie": _NS(rva=0x1a2b0),
                   "delete_plant": _NS(rva=0x10800)},
    )
    # profile.lawn_app / board need attribute+get access
    prof.lawn_app = _NS(board=0x868)
    prof.board = _NS(sun=0x5578)
    prof.functions = {"put_plant": _NS(rva=0x10560), "put_zombie": _NS(rva=0x1a2b0),
                      "delete_plant": _NS(rva=0x10800)}
    prof.functions_get = prof.functions.get
    g.profile = prof
    g.session = _NS(name="PlantsVsZombies.exe")
    g.observer = _NS(lawn_ptr=lambda: 0x0AAA0000, board_ptr=lambda: 0x0BBB0000)
    g.window = _NS(_lawn_field=lambda name: 0x0AAA0000 + 0x367 if name == "is_windowed" else None)
    return g


def test_place_lineup_by_index():
    g = _game_with_profile()
    w, _ = make_worker(game=g)
    w.submit("attach")
    w.submit("place_lineup", index=61)
    drain(w, 4)
    assert 61 in g.place_calls


def test_place_custom_builds_lineup():
    g = _game_with_profile()
    captured = {}

    def place(target, **kw):
        captured["target"] = target
        return "ok"

    g.place_lineup = place
    w, _ = make_worker(game=g)
    w.submit("attach")
    w.submit("place_custom", scene="PE",
             cells=[{"row": 1, "col": 1, "plant": 42},
                    {"row": 3, "col": 1, "container": 16, "plant": 41}])
    drain(w, 4)
    lu = captured.get("target")
    from pvztools.schema.lineup.model import Lineup
    assert isinstance(lu, Lineup)
    assert lu.scene_id.code == "PE"
    assert len(lu.cells) == 2


def test_addresses_are_pointer_chains():
    g = _game_with_profile()
    w, _ = make_worker(game=g)
    w.submit("attach")
    drain(w, 3)
    addr = dict((name, (chain, resolved)) for name, chain, resolved in w.snapshot.addresses)
    # 全局:模块+rva
    assert addr["LawnApp 全局"][0] == "PlantsVsZombies.exe+0x331C50"
    # 实例:一层解引用
    assert addr["LawnApp 实例"][0] == "[PlantsVsZombies.exe+0x331C50]"
    # 阳光:双层解引用 + 两个偏移
    assert "阳光 sun" in addr
    assert addr["阳光 sun"][0] == "[[PlantsVsZombies.exe+0x331C50]+0x868]+0x5578"
    # 函数:模块+rva
    assert addr["fn put_plant"][0] == "PlantsVsZombies.exe+0x10560"


def test_addresses_empty_without_profile():
    g = FakeGame()  # 无 profile/session/observer
    w, _ = make_worker(game=g)
    w.submit("attach")
    drain(w, 3)
    # 不应崩;地址表为空或不含链
    assert isinstance(w.snapshot.addresses, tuple)


def test_auto_windowize_on_full_attach():
    calls = []
    g = FakeGame()
    g.window = _NS(set_windowed=lambda want: calls.append(want) or True)
    w = GameWorker(attach_fn=lambda pid=None, **f: g, find_fn=lambda: [],
                   auto_attach=False, mode="full")
    w.auto_windowize = True
    w.submit("attach")
    drain(w, 3)
    assert calls == [True]  # 全模式附加后自动窗口化


def test_no_windowize_in_readonly():
    calls = []
    g = FakeGame()
    g.window = _NS(set_windowed=lambda want: calls.append(want) or True)
    w = GameWorker(attach_fn=lambda pid=None, **f: g, find_fn=lambda: [],
                   auto_attach=False, mode="readonly")
    w.auto_windowize = True
    w.submit("attach")
    drain(w, 3)
    assert calls == []  # 只读模式不动显示模式


def test_processes_in_snapshot():
    class P:
        def __init__(self, pid, name):
            self.pid, self.name = pid, name
    w = GameWorker(attach_fn=lambda *a, **k: None,
                   find_fn=lambda: [P(8068, "popcapgame1.exe"), P(1234, "PlantsVsZombies.exe")],
                   auto_attach=False)
    w.tick()
    assert w.snapshot.processes == ((8068, "popcapgame1.exe"), (1234, "PlantsVsZombies.exe"))


def test_processes_present_while_attached():
    class P:
        def __init__(self, pid, name):
            self.pid, self.name = pid, name
    g = FakeGame()
    w = GameWorker(attach_fn=lambda pid=None, **k: g,
                   find_fn=lambda: [P(1234, "popcapgame1.exe")], auto_attach=False)
    w.submit("attach")
    drain(w, 3)
    assert w.snapshot.attached
    assert (1234, "popcapgame1.exe") in w.snapshot.processes


def test_auto_background_on_full_attach():
    calls = []
    g = FakeGame()
    class Ch(FakeCheats):
        @property
        def background(self): return self._bg
        @background.setter
        def background(self, v): self._bg = v; calls.append(v)
    g.cheats = Ch(); g.cheats._bg = False
    g.window = _NS(set_windowed=lambda want: True)
    w = GameWorker(attach_fn=lambda pid=None, **f: g, find_fn=lambda: [],
                   auto_attach=False, mode="full")
    w.auto_background = True
    w.submit("attach")
    drain(w, 3)
    assert calls == [True]  # 全模式附加后自动开后台运行


def test_no_background_in_readonly():
    calls = []
    g = FakeGame()
    class Ch(FakeCheats):
        @property
        def background(self): return False
        @background.setter
        def background(self, v): calls.append(v)
    g.cheats = Ch()
    w = GameWorker(attach_fn=lambda pid=None, **f: g, find_fn=lambda: [],
                   auto_attach=False, mode="readonly")
    w.auto_background = True
    w.submit("attach")
    drain(w, 3)
    assert calls == []
