"""Tests for the ``pvztools`` command line (src/pvztools/cli/__main__.py)."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pvztools import __version__
from pvztools.cli.__main__ import (
    EXIT_ERROR,
    EXIT_LINT,
    EXIT_OK,
    find_lineup_entries,
    lineup_grid,
    looks_like_pvzt,
    main,
)
from pvztools.schema.lineup import decode_lineup_code, parse_lineup_string

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "docs" / "research" / "arraydesign-lineups.json"
SRC = ROOT / "src"

SHORTHAND = "P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)"
PVZT = """scene: PE
timing: effect

w1~8 len 601
  PP 225 @ 25 9
w9,19 len 4500
  PP 225 @ 25 9
"""
CODE = "LI43NMQIfe3BVVEc+sHIXUAPX7dvSCBUt1TS1OE8dn9XgraGkdQ1ZlSKiEUaVw=="
STRING = "0,2F 1 1 0 0 0,2F 1 3 0 0 0,1E 2 2 0 0 0,31 2 8 0 0 0"


def run(argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr, sys.stdin
    sys.stdout, sys.stderr = out, err
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        rc = main(argv)
    finally:
        sys.stdout, sys.stderr, sys.stdin = old
    return rc, out.getvalue(), err.getvalue()


# --- detection -------------------------------------------------------------
def test_looks_like_pvzt():
    assert looks_like_pvzt(PVZT)
    assert looks_like_pvzt("# c\nw1 len 601\n PP 225")
    assert not looks_like_pvzt(SHORTHAND)
    assert not looks_like_pvzt("P|I-P|P|I-P 601|1150|601|1150")
    # §3.1b shorthand per-wave prefix is not a .pvzt wave header
    assert not looks_like_pvzt("w1:PSD/PDD w19:PSD/PSD")
    assert not looks_like_pvzt("w1:c(二路)PSD/PDD\nw3:PDD/PDcD")
    assert looks_like_pvzt("w1%4, 2~9%2, 11~19 len 1976\n  I 11 @ 3 5")
    assert looks_like_pvzt("w 601\n PP 225 25 9")


SEML_EXPLODE = """scene:NE
SET x 776
w1~3 0 1200~1500
C_NUM x+266 12345 9
SET x x+24
"""


def test_detect_format():
    from pvztools.cli.__main__ import detect_format, looks_like_seml

    assert detect_format(SHORTHAND) == "shorthand"
    assert detect_format(PVZT) == "pvzt"
    assert detect_format("w1:PSD/PDD w19:PSD/PSD") == "shorthand"
    assert detect_format(SEML_EXPLODE) == "seml"
    assert looks_like_seml("scene:PE\nprotect:18 28\nrepeat:300\nw 601\n PP 225 25 9\n")
    assert looks_like_seml("w1 0 1200\n PP 300 25 9\n")  # ice time on the wave line
    assert looks_like_seml("w1~8 601\n PP 225 25 9\n")  # bare lengths everywhere, no 'len'
    assert not looks_like_seml("w1~8 len 601\n PP 225 @ 25 9\nw10\n PP 341\n")


def test_track_parse_wave_prefix_shorthand():
    rc, out, _ = run(["track", "parse", "w1:PSD/PDD w19:PSD/PSD"])
    assert rc == EXIT_OK and "kind: shorthand  waves: 2" in out


def test_track_parse_seml_uses_bridge():
    rc, out, _ = run(["track", "parse", SEML_EXPLODE])
    assert rc == EXIT_OK
    assert "w1 len 1500" in out and "w2 len 1500" in out and "w3 len 1500" in out
    assert "I 0" in out  # wave-line ice time kept
    assert "C 776~1042" in out and "C 800~1066" in out  # SET re-executed per loop iteration
    rc, out, _ = run(["track", "parse", "--format", "pvzt", "w1~8 601\n PP 225 25 9\n"])
    assert rc == EXIT_OK and "w1~8 len 601" in out
    rc, out, _ = run(["track", "to-avz", SEML_EXPLODE])
    assert rc == EXIT_OK and "C(266" in out


def test_track_dialect_flag(tmp_path):
    rc, out, _ = run(["track", "parse", "--dialect", "baicai", "w1\n  C 100 @ 1 9"])
    assert rc == EXIT_OK and "dialect: baicai" in out
    rc, out, err = run(["track", "parse", "--dialect", "be2sio4", "dialect: duben\nw1\n  C 100 @ 1 9"])
    assert rc == EXIT_OK and "dialect: duben" in out and "--dialect be2sio4 ignored" in err
    with pytest.raises(SystemExit):
        run(["track", "parse", "--dialect", "nope", SHORTHAND])


def test_track_parse_error_names_the_file(tmp_path):
    f = tmp_path / "t.pvzt"
    f.write_text("w1" + chr(10) + "  QQ 225" + chr(10), encoding="utf-8")
    rc, _, err = run(["track", "parse", str(f)])
    assert rc == EXIT_ERROR and err.startswith(f"error: {f}: line 2:")


def test_track_lint_bad_scene_is_a_diagnostic():
    rc, out, _ = run(["track", "lint", "--scene", "XX", PVZT])
    assert rc == EXIT_LINT and "SCENE_UNKNOWN" in out


# --- track -----------------------------------------------------------------
def test_track_parse_inline_shorthand():
    rc, out, _ = run(["track", "parse", SHORTHAND])
    assert rc == EXIT_OK
    assert "kind: shorthand  waves: 6" in out
    assert SHORTHAND in out


def test_track_parse_json_and_stdin():
    rc, out, _ = run(["track", "parse", "-", "--json"], stdin="P|I-P|P|I-P 601|1150|601|1150")
    assert rc == EXIT_OK
    data = json.loads(out)
    assert data["pvztools_track"] == 1
    assert len(data["waves"]) == 4


def test_track_parse_pvzt_file(tmp_path):
    f = tmp_path / "t.pvzt"
    f.write_text(PVZT, encoding="utf-8")
    rc, out, _ = run(["track", "parse", str(f)])
    assert rc == EXIT_OK
    assert "kind: pvzt  waves: 2" in out
    assert "w1~8 len 601" in out


def test_track_parse_error_exit_code():
    rc, out, err = run(["track", "parse", "P6: PP|XX"])
    assert rc == EXIT_ERROR
    assert "error:" in err and "UNKNOWN_SYMBOL" in err


def test_track_lint_clean_and_errors():
    rc, out, _ = run(["track", "lint", SHORTHAND])
    assert rc == EXIT_OK and "no diagnostics" in out
    rc, out, _ = run(["track", "lint", "P2: PP|PP (6|60)"])
    assert rc == EXIT_LINT
    assert "LEN_RANGE" in out


def test_track_expand_table():
    rc, out, _ = run(["track", "expand", SHORTHAND, "--scene", "PE", "--cobs", "15,25,45,55", "--waves", "2"])
    assert rc == EXIT_OK
    lines = out.splitlines()
    assert lines[0].split() == ["wave", "effect", "operation", "kind", "symbol", "target", "cannon"]
    body = [ln for ln in lines[2:] if ln and ln[0].isdigit()]
    assert len(body) == 4  # PP mirrored x 2 waves
    assert body[0].split()[3:5] == ["cob", "PP"]
    assert "1-5" in body[0]


def test_track_expand_bad_cobs():
    rc, _, err = run(["track", "expand", SHORTHAND, "--scene", "PE", "--cobs", "x"])
    assert rc == EXIT_ERROR and "--cobs" in err


def test_track_to_seml_and_avz(tmp_path):
    f = tmp_path / "t.pvzt"
    f.write_text(PVZT, encoding="utf-8")
    rc, out, _ = run(["track", "to-seml", str(f)])
    assert rc == EXIT_OK and "w1 601" in out and "PP 225 25 9" in out
    rc, out, _ = run(["track", "to-avz", str(f)])
    assert rc == EXIT_OK and "#include <avz.h>" in out and "OnWave(" in out


def test_track_to_avz_rejects_shorthand():
    rc, _, err = run(["track", "to-avz", SHORTHAND])
    assert rc == EXIT_ERROR and "error:" in err


# --- lineup ----------------------------------------------------------------
def test_lineup_decode_grid_and_string():
    rc, out, _ = run(["lineup", "decode", CODE])
    assert rc == EXIT_OK
    lines = out.splitlines()
    assert lines[0].startswith("scene: FE")
    grid = lines[2:8]
    assert len(grid) == 6
    assert grid[2].startswith("3  CC Lp CC Lp")
    assert any(ln.startswith("string: 1,") for ln in lines)


def test_lineup_grid_rake_and_empty():
    lu = parse_lineup_string(STRING)
    grid = lineup_grid(lu).splitlines()
    assert grid[1] == "1  CC cc CC cc .. .. .. .. .."  # 0x2F = cob cannon
    assert grid[2].startswith("2* .. Pk")  # 0x1E = lone pumpkin shell, rake row marked


def test_lineup_decode_bad_code():
    rc, _, err = run(["lineup", "decode", "nope"])
    assert rc == EXIT_ERROR and "error:" in err


def test_lineup_encode_roundtrip():
    rc, out, _ = run(["lineup", "encode", STRING])
    assert rc == EXIT_OK
    code = out.strip()
    lu = decode_lineup_code(code)
    assert lu.scene == "PE" and lu.rake_row == 2 and lu.cobs() == [(1, 1), (1, 3)]


@pytest.mark.skipif(not DB.exists(), reason="ArrayDesign catalogue not present")
def test_lineup_show_by_index_and_name():
    rc, out, _ = run(["lineup", "show", "66", "--db", str(DB)])
    assert rc == EXIT_OK
    assert "[66] 3C八炮" in out and "cobs: 8" in out and "code: " in out
    rc, out2, _ = run(["lineup", "show", "3C八炮", "--db", str(DB)])
    assert rc == EXIT_OK and out2 == out
    rc, out, _ = run(["lineup", "show", "八炮", "--db", str(DB)])
    assert rc == EXIT_OK and "lineups match" in out
    rc, _, err = run(["lineup", "show", "no-such-lineup-xyz", "--db", str(DB)])
    assert rc == EXIT_ERROR and "no lineup matching" in err


def test_find_lineup_entries():
    entries = [{"index": 0, "name": "a"}, {"index": 5, "name": "Ab"}]
    assert find_lineup_entries(entries, "5") == [entries[1]]
    assert find_lineup_entries(entries, "a") == [entries[0]]
    assert find_lineup_entries(entries, "AB") == [entries[1]]
    assert find_lineup_entries(entries, "zz") == []


def test_lineup_show_missing_db(tmp_path):
    rc, _, err = run(["lineup", "show", "1", "--db", str(tmp_path / "none.json")])
    assert rc == EXIT_ERROR and "not found" in err


# --- process-level entry points ---------------------------------------------
def test_python_m_pvztools_version():
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    res = subprocess.run(
        [sys.executable, "-m", "pvztools", "--version"],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )
    assert res.returncode == 0
    assert f"pvztools {__version__}" in res.stdout


def test_python_m_pvztools_utf8_output():
    env = dict(os.environ, PYTHONPATH=str(SRC))
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    res = subprocess.run(
        [sys.executable, "-m", "pvztools", "track", "parse", "P2: PP|PP (6|6)"],
        capture_output=True, env=env, cwd=str(ROOT),
    )
    assert res.returncode == 0
    assert "kind: shorthand".encode() in res.stdout
    res = subprocess.run(
        [sys.executable, "-m", "pvztools", "lineup", "show", "66"],
        capture_output=True, env=env, cwd=str(ROOT),
    )
    assert res.returncode == 0
    assert "3C八炮".encode("utf-8") in res.stdout
