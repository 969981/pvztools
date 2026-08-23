"""Campaign orchestrator (control/campaign.py), the strict level runner
(control/play.py play_one_level) and the ``campaign`` CLI command.  No live
game: everything is driven through fakes."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from pvztools import game as game_mod
from pvztools.cli.__main__ import EXIT_OK, build_parser, cmd_campaign, main
from pvztools.control.campaign import Campaign, CampaignReport
from pvztools.control.play import FALLBACK_LINEUPS, LevelReport, play_one_level


# --- wall clock ----------------------------------------------------------------
class Wall:
    def __init__(self) -> None:
        self.t = 0.0

    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += max(s, 0.001)


# =============================================================================
# play_one_level: strict fixed-cycle firing against a fake game
# =============================================================================
class Cob:
    def __init__(self, row: int, col: int, ready: bool = True) -> None:
        self.row = row
        self.col = col
        self.ready = ready


class FireRes:
    def __init__(self, tick: Optional[int]) -> None:
        self.fired_tick = tick
        self.state_after = 39  # firing


class _Obs:
    def __init__(self, game: "PGame") -> None:
        self.g = game

    def board(self):
        g = self.g
        return SimpleNamespace(scene=g.scene, clock=g.clock, level_complete=g.clock >= g.complete_at,
                               wave=min(20, g.clock // 200), total_waves=20, level=g.level)


class FakeStrictCollector:
    def __init__(self, award_at: int) -> None:
        self.award_at = award_at
        self.ticks = 0
        self.collected = 0
        self.awards = 0

    def tick(self, sample: Any = None) -> int:
        self.ticks += 1
        return 0

    @property
    def stats(self):
        return SimpleNamespace(summary=lambda: {"collected": self.collected, "calls": self.collected,
                                                "sun": 25 * self.collected, "money": 0,
                                                "level_awards": self.awards, "by_type": {}, "errors": 0})


class PGame:
    """Minimal game for play_one_level: 8 ready cobs, a clock advanced by the
    injected sleep, level completing (ui 3 -> 5) at ``complete_at``."""

    def __init__(self, scene: int = 2, complete_at: int = 1300, ncobs: int = 8) -> None:
        self.ui = 3
        self.scene = scene
        self.clock = 0
        self.complete_at = complete_at
        self.level = 7
        self.remote = True
        self._observer = _Obs(self)
        self.window = SimpleNamespace(bring_to_foreground=self._front)
        cells = [(1, 5), (2, 5), (3, 1), (3, 3), (4, 1), (4, 3), (5, 5), (6, 5)]
        self._cobs = [Cob(r, c) for r, c in cells[:ncobs]]
        self.fired: List[Any] = []
        self.placed: List[Any] = []
        self.actions = SimpleNamespace(fire_cobs=self._fire)
        self.fronted = 0

    def _front(self) -> bool:
        self.fronted += 1
        return True

    @property
    def cobs(self):
        return list(self._cobs)

    def _fire(self, fires, at_tick=None):
        self.fired.append((tuple(fires), at_tick))
        return [FireRes(None if at_tick is None else at_tick + 1) for _ in fires]

    def place_lineup(self, name, clear=True):
        self.placed.append((name, clear))
        return SimpleNamespace(calls=3)

    def make_wall(self) -> Wall:
        w = Wall()
        base_sleep = w.sleep

        def sleep(s: float) -> None:
            base_sleep(s)
            self.clock += 20
            if self.clock >= self.complete_at and self.ui == 3:
                self.ui = 5

        w.sleep = sleep  # type: ignore[assignment]
        return w


def test_play_one_level_fires_and_completes():
    g = PGame(scene=2, complete_at=1300)          # PE, 6-row pool
    w = g.make_wall()
    col = FakeStrictCollector(award_at=0)
    lr = play_one_level(g, cycle=200, collect=True, sleep=w.sleep, clock=w.clock,
                        collector_factory=lambda game: col, wait_ui_s=600.0)
    assert lr.result == "level_complete"
    assert lr.scene == "PE" and lr.lineup == "3C八炮" and lr.level == 7 and lr.waves == 20
    assert lr.fires >= 3
    assert g.fronted == 1 and g.placed == []       # already 8 cobs -> no placement
    # every fire is frame-locked at its target; the fake returns tick = target + 1
    assert lr.mean_abs_delta == 1.0
    # target rows on a 6-row scene are 2 and 5, landing column 8 (3C八炮 default)
    (first_fires, at_tick) = g.fired[0]
    targets = [tg for _, tg in first_fires]
    assert targets == [(2, 8), (5, 8)] and at_tick == 5
    assert col.ticks > 0


def test_play_one_level_places_lineup_when_few_cobs():
    g = PGame(scene=2, complete_at=200, ncobs=8)
    g._cobs = g._cobs[:2]                            # only 2 cobs -> needs placement
    w = g.make_wall()
    play_one_level(g, cycle=100, collect=False, sleep=w.sleep, clock=w.clock,
                   lineup="经典八炮", wait_ui_s=600.0)
    assert g.placed == [("经典八炮", True)]


def test_play_one_level_not_in_level_returns_early():
    g = PGame()
    g.ui = 1
    w = Wall()
    lr = play_one_level(g, sleep=w.sleep, clock=w.clock, wait_ui_s=0.2)
    assert lr.result == "not_in_level" and lr.fires == 0


# =============================================================================
# Campaign: full state machine with injected navigation fakes
# =============================================================================
class CMenu:
    """ui 1 -> 2 (start_adventure), ui 5 -> 1 (award_continue)."""

    def __init__(self, game: "CGame") -> None:
        self.game = game
        self.calls: List[str] = []

    def start_adventure(self, wait: float = 10.0):
        self.calls.append("adventure")
        self.game.ui = 2
        return 2

    def award_continue(self, wait: float = 10.0):
        self.calls.append("award")
        self.game.ui = 1
        return 1


class CChooser:
    def __init__(self, game: "CGame") -> None:
        self.game = game
        self.picks: List[Any] = []

    def select_and_start(self, types, wait: float = 10.0) -> bool:
        self.picks = list(types)
        self.game.ui = 3
        return True


class FakePatches:
    def __init__(self, *names: str) -> None:
        self.enabled = set(names)
        self.disabled: List[str] = []

    def disable(self, name: str) -> None:
        self.enabled.discard(name)
        self.disabled.append(name)


class CGame:
    def __init__(self, ui: int = 1) -> None:
        self.ui = ui
        self.idx = 0
        self.current_scene = "PE"
        self.mode = 0
        self.board = SimpleNamespace(scene=2, level=1)
        self.remote = True
        self.window = SimpleNamespace(bring_to_foreground=lambda: True)
        self._patches = FakePatches("background_running")


def make_campaign(g: CGame, *, scenes=("PE", "DE"), plantable_at=None, **kw) -> Campaign:
    w = Wall()

    def fake_classify(game):
        sc = scenes[min(game.idx, len(scenes) - 1)]
        game.current_scene = sc
        plantable = True if plantable_at is None else (game.idx < plantable_at)
        reason = "" if plantable else "传送带关卡 (conveyor)"
        return SimpleNamespace(scene=sc, level=game.idx + 1, is_adventure=plantable,
                               plantable=plantable, kind="adventure" if plantable else "conveyor",
                               reason=reason)

    def fake_play(game, *, lineup=None, **kwargs):
        game.ui = 5
        lr = LevelReport(scene=game.current_scene, lineup=lineup, level=game.idx + 1, waves=20,
                         fires=6, mean_abs_delta=1.0, collected=3, result="level_complete")
        game.idx += 1
        return lr

    kw.setdefault("classify", fake_classify)
    kw.setdefault("play_level", fake_play)
    kw.setdefault("menu_factory", lambda game: CMenu(game))
    kw.setdefault("chooser_factory", lambda game: CChooser(game))
    kw.setdefault("cutscene_factory", lambda game: None)
    kw.setdefault("sleep", w.sleep)
    kw.setdefault("clock", w.clock)
    kw.setdefault("quiet", True)
    return Campaign(g, **kw)


def test_campaign_plays_two_levels_with_scene_defaults():
    g = CGame(ui=1)
    c = make_campaign(g, count=2, scenes=("PE", "DE"))
    rep = c.run()
    assert rep.played == 2 and rep.stop == "count reached"
    assert [l.scene for l in rep.levels] == ["PE", "DE"]
    # the campaign picked the scene-default 八炮 lineup for each scene
    assert rep.levels[0].lineup == FALLBACK_LINEUPS["PE"] == "3C八炮"
    assert rep.levels[1].lineup == FALLBACK_LINEUPS["DE"] == "神之八炮"
    # award screen dismissed once per level; back on the menu at the end
    assert c.menu.calls == ["adventure", "award", "adventure", "award"] and g.ui == 1
    # aggregation
    assert rep.total_fires == 12 and rep.total_collected == 6 and rep.mean_abs_delta == 1.0
    # patches released in the finally
    assert rep.released == ["background_running"] and g._patches.enabled == set()


def test_campaign_lineup_override_applies_to_every_level():
    g = CGame(ui=1)
    c = make_campaign(g, count=2, scenes=("PE", "DE"), lineup="我的阵")
    rep = c.run()
    assert [l.lineup for l in rep.levels] == ["我的阵", "我的阵"]


def test_campaign_skips_non_plantable_level_and_stops():
    g = CGame(ui=1)
    # level 0 plays, level 1 classifies as non-plantable -> skip + stop
    c = make_campaign(g, count=5, scenes=("PE", "DE", "NE"), plantable_at=1)
    rep = c.run()
    assert rep.played == 1
    assert rep.skipped is not None and "conveyor" in rep.skipped["reason"]
    assert "non-adventure" in rep.stop
    assert g._patches.enabled == set()             # still released


def test_campaign_auto_stops_at_first_non_adventure():
    g = CGame(ui=1)
    c = make_campaign(g, count=1, auto=True, scenes=("PE", "DE"), plantable_at=2)
    rep = c.run()
    # auto ignores count: plays 2 levels, stops when level 2 is non-plantable
    assert rep.played == 2 and rep.skipped is not None


def test_campaign_starts_from_award_screen():
    g = CGame(ui=5)
    c = make_campaign(g, count=1, scenes=("PE",))
    rep = c.run()
    assert rep.played == 1 and c.menu.calls[0] == "award"


# =============================================================================
# The real LevelInfo shape (no is_adventure flag; enum kind; adventure_level):
# _plantable derives adventure from mode==0, _skip_info reads adventure_level
# and stringifies the LevelKind enum.
# =============================================================================
def test_plantable_try_then_stop():
    from pvztools.control.campaign import _plantable
    from pvztools.control.levels import LevelInfo, LevelKind

    def li(kind, plantable=True, mode=0):
        return LevelInfo(kind=kind, plantable=plantable, has_chooser=True, mode=mode,
                         scene="PE", adventure_level=None, reason="")

    # TRY: any plant-defense level is attempted (place lineup + fire), including
    # conveyor, survival, last-stand and the final boss.
    assert _plantable(li(LevelKind.ADVENTURE)) is True
    assert _plantable(li(LevelKind.CONVEYOR)) is True     # PutPlant bypasses the belt
    assert _plantable(li(LevelKind.SURVIVAL, mode=1)) is True
    assert _plantable(li(LevelKind.MINIGAME, mode=31)) is True   # Last Stand
    assert _plantable(li(LevelKind.BOSS)) is True

    # STOP only where planting is inherently meaningless.
    assert _plantable(li(LevelKind.I_ZOMBIE, mode=61)) is False
    assert _plantable(li(LevelKind.VASEBREAKER, mode=51)) is False
    # an explicit plantable=False from the classifier is always honoured.
    assert _plantable(li(LevelKind.MINIGAME, plantable=False)) is False


def test_skip_info_reads_adventure_level_and_stringifies_enum_kind():
    from pvztools.control.campaign import _skip_info
    from pvztools.control.levels import LevelInfo, LevelKind

    info = LevelInfo(kind=LevelKind.CONVEYOR, plantable=False, has_chooser=False, mode=0,
                     scene="PE", adventure_level=5, reason="冒险模式传送带关卡")
    sk = _skip_info(info, ui=3)
    assert sk["level"] == 5                 # from adventure_level, not None
    assert sk["kind"] == "conveyor"         # LevelKind enum -> its string value
    assert sk["scene"] == "PE" and sk["ui"] == 3
    assert sk["reason"] == "冒险模式传送带关卡"
    # and the record round-trips cleanly through the report JSON (no enum repr)
    d = json.loads(json.dumps(sk, ensure_ascii=False, default=str))
    assert d["kind"] == "conveyor"


def test_campaign_stops_on_i_zombie_via_real_levelinfo():
    """End-to-end: a classify that returns the real LevelInfo for an I,Zombie
    stage (planting is meaningless) makes the campaign stop, not play; a
    survival stage in between is still attempted (try-then-stop)."""
    from pvztools.control.levels import LevelInfo, LevelKind

    g = CGame(ui=1)

    def classify_levels(game):
        # level 0 adventure plays; level 1 i_zombie stops the run
        if game.idx == 0:
            return LevelInfo(kind=LevelKind.ADVENTURE, plantable=True, has_chooser=True,
                             mode=0, scene="PE", adventure_level=1, reason="冒险")
        return LevelInfo(kind=LevelKind.I_ZOMBIE, plantable=False, has_chooser=False,
                         mode=61, scene="DE", adventure_level=None, reason="I,Zombie")

    c = make_campaign(g, count=5, scenes=("PE", "DE"), classify=classify_levels)
    rep = c.run()
    assert rep.played == 1
    assert rep.skipped is not None and rep.skipped["kind"] == "i_zombie"
    assert g._patches.enabled == set()


# =============================================================================
# CampaignReport: aggregation, markdown, json
# =============================================================================
def sample_report() -> CampaignReport:
    rep = CampaignReport(target=2, auto=False, stop="count reached")
    rep.levels = [
        LevelReport(scene="PE", lineup="3C八炮", level=7, waves=20, fires=6, mean_abs_delta=1.2,
                    collected=40, result="level_complete"),
        LevelReport(scene="DE", lineup="神之八炮", level=8, waves=20, fires=8, mean_abs_delta=0.8,
                    collected=55, result="level_complete"),
    ]
    rep.released = ["background_running"]
    return rep


def test_campaign_report_markdown_and_json():
    rep = sample_report()
    md = rep.to_markdown()
    for h in ("# 连关报告 (Campaign report)", "## 概要 (Header)", "## 关卡 (Levels)", "## 跳过 (Skipped)"):
        assert h in md
    assert "| 已打关卡 (levels played) | 2 |" in md
    assert "| 总发炮组 (total fire pairs) | 14 |" in md
    assert "| 7 | PE | 3C八炮 | 20 | 6 | 1.2 | 40 | level_complete |" in md
    assert "| 8 | DE | 神之八炮 | 20 | 8 | 0.8 | 55 | level_complete |" in md
    assert "- 无 (none)" in md            # no skip record
    d = json.loads(rep.to_json())
    assert d["played"] == 2 and d["total_collected"] == 95 and d["mean_abs_delta"] == 1.0
    assert d["levels"][0]["lineup"] == "3C八炮" and d["stop"] == "count reached"


def test_campaign_report_markdown_with_skip():
    rep = CampaignReport(target=3, stop="non-adventure level")
    rep.skipped = {"scene": "PE", "level": 9, "kind": "conveyor", "reason": "传送带关卡 (conveyor)"}
    md = rep.to_markdown()
    assert "- 原因 (reason): 传送带关卡 (conveyor)" in md
    assert "| - | - | - | - | 0 | - | 0 | (无 none) |" in md


# =============================================================================
# Game.campaign delegation + CLI
# =============================================================================
def test_game_campaign_delegates(monkeypatch):
    from test_game_api import make_session
    from pvztools.game import Game
    import pvztools.control.campaign as cmod

    s, p = make_session(ui=3)
    g = Game(s, p, write=True, remote=True, foreground=False)
    seen: Dict[str, Any] = {}

    class C:
        def __init__(self, game, **kw):
            seen.update(kw)
            seen["game"] = game

        def run(self):
            return "CAMPAIGN_REPORT"

    monkeypatch.setattr(cmod, "Campaign", C)
    out = g.campaign(3, auto=True, lineup="X", cycle=900)
    assert out == "CAMPAIGN_REPORT" and seen["game"] is g
    assert seen["count"] == 3 and seen["auto"] is True and seen["lineup"] == "X"
    assert seen["cycle"] == 900 and seen["foreground"] is False


def test_cli_campaign_parsing():
    parser = build_parser()
    a = parser.parse_args(["campaign"])
    assert a.count == "1" and a.func is cmd_campaign
    assert parser.parse_args(["campaign", "3"]).count == "3"
    assert parser.parse_args(["campaign", "auto"]).count == "auto"
    a = parser.parse_args(["campaign", "5", "--lineup", "X", "--cycle", "800", "--remote", "--yes"])
    assert a.count == "5" and a.lineup == "X" and a.cycle == 800 and a.remote and a.yes


def test_cli_campaign_requires_remote_and_yes(capsys):
    assert main(["campaign", "2"]) != EXIT_OK
    assert "需要 --remote" in capsys.readouterr().err
    assert main(["campaign", "2", "--remote"]) != EXIT_OK
    assert "--yes" in capsys.readouterr().err
    assert main(["campaign", "bogus", "--remote", "--yes"]) != EXIT_OK
    assert "关卡数或 'auto'" in capsys.readouterr().err


def test_cli_campaign_runs_and_writes_reports(monkeypatch, capsys, tmp_path):
    from test_game_api import make_session
    from pvztools.game import Game

    s, p = make_session(ui=1, in_level=False)
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    monkeypatch.setattr(game_mod, "_grant_remote_handle", lambda session: None)
    monkeypatch.setattr(game_mod, "_acquire_lock", lambda pid: None)

    rep = sample_report()

    def fake_campaign(self, n=1, *, auto=False, lineup=None, cycle=869, on_event=None, **kw):
        return rep

    monkeypatch.setattr(Game, "campaign", fake_campaign)
    out = tmp_path / "c.json"
    md = tmp_path / "c.md"
    rc = main(["campaign", "2", "--remote", "--yes", "--report", str(out), "--md", str(md)])
    assert rc == EXIT_OK, capsys.readouterr()
    text = capsys.readouterr().out
    assert "已打 2 关" in text and "report written" in text
    assert md.read_text(encoding="utf-8").startswith("# 连关报告")
    assert json.loads(out.read_text(encoding="utf-8"))["played"] == 2
