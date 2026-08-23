"""Bundled lineup catalogue (pvztools.data) + schema.lineup import-order fix."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def test_load_lineups_count_and_lookup():
    from pvztools.data import lineups_path, load_lineups, load_lineups_raw
    from pvztools.schema.lineup import parse_lineup_string

    assert lineups_path().is_file()
    raw = load_lineups_raw()
    assert "license_note" in raw and "source" in raw
    entries = load_lineups()
    assert len(entries) == 363
    hit = [e for e in entries if e["name"] == "3C八炮"]
    assert len(hit) == 1
    assert len(parse_lineup_string(hit[0]["raw"]).cobs()) == 8


def test_load_lineup_default_db_is_package_data():
    from pvztools.control import lineup_place
    from pvztools.data import lineups_path

    assert Path(lineup_place.DEFAULT_DB) == lineups_path()
    lu = lineup_place.load_lineup("3C八炮")
    assert lu.name == "3C八炮" and len(lu.cobs()) == 8


def test_schema_lineup_imports_first_in_fresh_interpreter():
    code = ("import pvztools.schema.lineup as L; "
            "lu = L.decode_lineup_code(L.encode_lineup_code(L.parse_lineup_string('0'))); "
            "print(len(lu.validate()))")
    r = subprocess.run([sys.executable, "-c", code], env=_env(), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "0"


def test_cli_lineup_show_from_temp_cwd():
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run([sys.executable, "-m", "pvztools", "lineup", "show", "3C八炮"],
                           cwd=d, env=_env(), capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "3C八炮" in r.stdout and "cobs: 8" in r.stdout


def test_lineups_path_from_a_zipped_install_stays_valid_after_return(tmp_path, monkeypatch):
    """Zipped install: ``as_file`` extracts to a temp file; the path must
    still exist after ``lineups_path()`` returned (the CLI captures it at
    import time)."""
    import importlib.resources as ir
    import pathlib
    import zipfile
    from pvztools import data as D

    zpath = tmp_path / "pkg.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("zipped_pkg/__init__.py", "")
        z.writestr("zipped_pkg/lineups.json", '{"lineups": [{"index": 1}]}')
    monkeypatch.syspath_prepend(str(zpath))
    import zipped_pkg  # noqa: F401  (zipimport)
    res = ir.files("zipped_pkg") / "lineups.json"
    assert not pathlib.Path(str(res)).is_file()  # not a plain file: as_file must extract
    p = D._extracted(res, "test_zipped")
    assert p.is_file() and p.read_text(encoding="utf-8").startswith('{"lineups"')
    assert D._extracted(res, "test_zipped") == p  # memoised
    # ... and lineups_path() itself goes through the same keeper
    monkeypatch.setattr(D, "files", lambda name: ir.files("zipped_pkg"))
    q = D.lineups_path()
    assert q.is_file() and q.read_text(encoding="utf-8").startswith('{"lineups"')
    # the extraction is released only by the atexit stack
    D._EXTRACTED_PATHS.pop("test_zipped", None)
    D._EXTRACTED_PATHS.pop(D.LINEUPS_FILE, None)
