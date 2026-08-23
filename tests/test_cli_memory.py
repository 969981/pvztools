"""Tests for the memory-layer CLI commands (doctor / scan / read / spawn / mod)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from pvztools import game as game_mod
from pvztools.cli.__main__ import EXIT_ERROR, EXIT_GAME, EXIT_OK, main
from test_game_api import make_session

ROOT = Path(__file__).resolve().parents[1]
STEAM_EXE = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Plants vs Zombies\PlantsVsZombies.exe")


@pytest.fixture
def fake_game(monkeypatch):
    """Patch process attach so the CLI talks to a fake in-level session."""
    state = {}

    def make(**kw):
        s, p = make_session(**kw)
        state["session"] = s

        def fake_attach(pid, write=False):
            state["pid"], state["write"] = pid, write
            return s

        monkeypatch.setattr(game_mod, "_process_attach", fake_attach)
        return s, p

    state["make"] = make
    make()
    return state


def test_read_sun(fake_game, capsys):
    assert main(["read", "sun"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "sun 75" in out and "goty_1_2_0_1096_en" in out and "关卡中" in out
    assert fake_game["write"] is False


def test_read_all(fake_game, capsys):
    assert main(["read", "all", "--pid", "4242"]) == EXIT_OK
    out = capsys.readouterr().out
    assert fake_game["pid"] == 4242
    assert "wave 3/20" in out
    assert "plants: 1" in out and "向日葵" in out
    assert "zombies: 2" in out and "铁桶僵尸" in out
    assert "seeds: 3" in out and "豌豆射手" in out
    assert "w1" in out and "w2" in out
    assert fake_game["session"].closed


def test_read_default_is_all(fake_game, capsys):
    assert main(["read"]) == EXIT_OK
    out = capsys.readouterr().out
    for section in ("sun 75", "plants:", "zombies:", "seeds:", "w1 ("):
        assert section in out, section


def test_spawn_show(fake_game, capsys):
    assert main(["spawn", "show"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "w1 (2只)" in out and "合计 5 只" in out


def test_read_not_in_level(fake_game, capsys):
    fake_game["make"](ui=1, in_level=False)
    assert main(["read", "sun"]) == EXIT_GAME
    err = capsys.readouterr().err
    assert "请先进入关卡" in err and "主菜单" in err


def test_game_not_running(monkeypatch, capsys):
    from pvztools.process import GameNotRunning

    def fake_attach(pid, write=False):
        raise GameNotRunning()

    monkeypatch.setattr(game_mod, "_process_attach", fake_attach)
    assert main(["read", "sun"]) == EXIT_GAME
    assert "游戏没有运行" in capsys.readouterr().err
    assert main(["doctor"]) == EXIT_GAME
    assert main(["mod", "sun", "5", "--write", "--yes"]) == EXIT_GAME


def test_mod_requires_flags(fake_game, capsys):
    assert main(["mod", "sun", "999"]) == EXIT_ERROR
    assert "--write" in capsys.readouterr().err
    assert main(["mod", "sun", "999", "--write"]) == EXIT_ERROR
    assert "--yes" in capsys.readouterr().err
    assert main(["mod", "sun-limit", "on", "--write", "--yes"]) == EXIT_ERROR
    assert "--patch" in capsys.readouterr().err
    assert fake_game["session"].writes == []


def test_mod_sun(fake_game, capsys):
    assert main(["mod", "sun", "999", "--write", "--yes"]) == EXIT_OK
    assert "sun 75 -> 999" in capsys.readouterr().out
    assert fake_game["write"] is True
    assert main(["mod", "sun", "99999", "--write", "-y"]) == EXIT_GAME
    assert "超出允许范围" in capsys.readouterr().err


def test_mod_speed_and_no_cooldown(fake_game, capsys):
    # `speed N` is a multiplier, same unit as game.cheats.speed (spec §3 `speed 5`)
    assert main(["mod", "speed", "5", "--write", "--yes"]) == EXIT_OK
    assert "speed -> 5x (frame_duration 2 ms)" in capsys.readouterr().out
    assert main(["mod", "speed", "0.5", "--write", "--yes"]) == EXIT_OK
    assert "speed -> 0.5x (frame_duration 20 ms)" in capsys.readouterr().out
    assert main(["mod", "speed", "100", "--write", "--yes"]) == EXIT_ERROR
    assert "超出允许范围" in capsys.readouterr().err
    assert main(["mod", "no-cooldown", "on", "--write", "--yes"]) == EXIT_OK
    assert "no-cooldown on" in capsys.readouterr().out
    assert main(["mod", "free-planting", "on", "--write", "--yes"]) == EXIT_OK
    assert "free-planting on" in capsys.readouterr().out
    assert main(["mod", "kill-zombies", "--write", "--yes"]) == EXIT_OK
    assert "kill-zombies: 2" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["mod", "no-cooldown", "maybe", "--write", "--yes"])


def test_mod_patch_kept_after_exit(fake_game, capsys):
    s, p = fake_game["session"], None
    from pvztools.addr import profile_for_stamp
    p = profile_for_stamp(0x4D02B058)
    assert main(["mod", "auto-collect", "on", "--patch", "--yes"]) == EXIT_OK
    assert "auto-collect on" in capsys.readouterr().out
    site = p.patches["auto_collect"].sites[0]
    assert s.read(site.va(p.image_base), len(site.on)) == bytes(site.on)
    assert main(["mod", "auto-collect", "off", "--patch", "--yes"]) == EXIT_OK
    assert s.read(site.va(p.image_base), len(site.off)) == bytes(site.off)
    # patches do not need a level
    fake_game["make"](ui=1, in_level=False)
    assert main(["mod", "background", "on", "--patch", "--yes"]) == EXIT_OK


def test_mod_not_in_level(fake_game, capsys):
    fake_game["make"](ui=1, in_level=False)
    assert main(["mod", "sun", "1", "--write", "--yes"]) == EXIT_GAME
    assert "请先进入关卡" in capsys.readouterr().err


def test_doctor(fake_game, monkeypatch, capsys, tmp_path):
    import pvztools.addr.resolve as R
    s, p = fake_game["session"], None

    class FakeRes:
        capability = "FULL"

    monkeypatch.setattr(R, "resolve", lambda target: FakeRes())
    monkeypatch.setattr(R, "doctor_report", lambda res: "DOCTOR OK")
    monkeypatch.setattr(R, "export_diagnostics", lambda res, path: Path(path))
    assert main(["doctor"]) == EXIT_OK
    assert "DOCTOR OK" in capsys.readouterr().out
    out = tmp_path / "diag.json"
    assert main(["doctor", "--json", str(out)]) == EXIT_OK
    assert "diagnostics written" in capsys.readouterr().out


def test_scan_missing_file(capsys):
    assert main(["scan", str(ROOT / "no-such.exe")]) == EXIT_ERROR
    assert "error:" in capsys.readouterr().err


def test_help_lists_memory_commands(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for name in ("doctor", "scan", "read", "spawn", "mod"):
        assert name in out
    # existing commands untouched
    assert "track" in out and "lineup" in out


@pytest.mark.skipif(not STEAM_EXE.is_file(), reason="Steam exe not present")
def test_scan_steam_exe(capsys):
    assert main(["scan", str(STEAM_EXE), "--json"]) == EXIT_OK
    d = json.loads(capsys.readouterr().out)
    assert d["stamp"] == "0x4d02b058"
    assert d["found"]["globals.lawn_app"] == "0x731c50"
    assert d["found"]["board.sun"] == "0x5578"
    assert main(["scan", str(STEAM_EXE)]) == EXIT_OK
    assert "family goty" in capsys.readouterr().out
