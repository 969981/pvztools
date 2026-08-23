"""Tests for pvztools.addr.{anchors,scan,resolve}.

Synthetic-image tests run everywhere; the Steam-exe tests skip when the
launcher is absent; the live test skips when the game is not running.
"""
from __future__ import annotations

import json
import os
import struct
import time
from pathlib import Path

import pytest

from pvztools.addr import anchors as A
from pvztools.addr import profile as P
from pvztools.addr import resolve as R
from pvztools.addr.scan import ScanResult, scan_exe, scan_image

ROOT = Path(__file__).resolve().parents[1]
STEAM_EXE = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Plants vs Zombies\PlantsVsZombies.exe")
TOOLKIT_JSON = ROOT / "docs" / "research" / "pvztoolkit-addresses.json"

needs_steam = pytest.mark.skipif(not STEAM_EXE.exists(), reason="Steam exe not present")

TEXT_VA = 0x401000
DATA = (0x6A0000, 0x6B0000)
LAWN = 0x6A9EC0
OTHER = 0x6A9F00          # a "more popular" global that must NOT win


def u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _fill(sig: str, filler: int = 0x11) -> bytes:
    return bytes(filler if tok == "??" else int(tok, 16) for tok in sig.split())


def _synthetic_text() -> bytes:
    """A fake .text with: retail-family LawnApp derefs, a more-referenced
    decoy global, two sun-cap shapes, a board-offset histogram, a known
    function prologue and a patch context."""
    parts = []
    pad = b"\x90" * 8
    # LawnApp family: mov ecx,[LAWN]; mov eax,[ecx+0x768] / cmp [ecx+0x7F8],0 / mov eax,[ecx+0x7FC]
    for disp, n in ((0x768, 2), (0x7F8, 3), (0x7FC, 2), (0x82C, 1)):
        for _ in range(n):
            parts.append(b"\x8B\x0D" + u32(LAWN) + b"\x8B\x81" + u32(disp) + pad)
    # null-check idiom
    parts.append(b"\xA1" + u32(LAWN) + b"\x85\xC0\x74\x10" + pad)
    # decoy: 40 loads of OTHER with a random field (hit count must not matter)
    for _ in range(40):
        parts.append(b"\x8B\x0D" + u32(OTHER) + b"\x8B\x81" + u32(0x884) + pad)
    # sun cap shapes (sun = 0x5560): mov ecx,[eax+0x5560]; sub esp,0xc; cmp ecx,9990
    parts.append(b"\x8B\x88" + u32(0x5560) + b"\x83\xEC\x0C\x81\xF9" + u32(9990) + pad)
    #   mov dword [eax+0x5560],9990
    parts.append(b"\xC7\x80" + u32(0x5560) + u32(9990) + pad)
    #   cmp dword [esi+0x5560],9990
    parts.append(b"\x81\xBE" + u32(0x5560) + u32(9990) + pad)
    # board histogram: references to 1051 high fields at shift 0
    for off in A.BOARD_1051_HIGH_FIELDS.values():
        for _ in range(3):
            parts.append(b"\x8B\x86" + u32(off) + pad)
    # function prologue for "put_plant" (the shipped signature's fixed bytes)
    parts.append(b"\xCC" * 16)
    parts.append(_fill(A._FUNCTION_SIGS["put_plant"]) + pad)
    # patch context for sun_cap_unlock (wildcards filled with 0x11)
    parts.append(_fill(A._PATCH_SIGS["sun_cap_unlock"][0]["sig"]) + pad)
    return b"".join(parts)


@pytest.fixture(scope="module")
def synth():
    text = _synthetic_text()
    return text, scan_image(text, TEXT_VA, DATA)


# ---------------------------------------------------------------------------
# anchors helpers
# ---------------------------------------------------------------------------
def test_compile_sig_wildcards():
    pat = A.compile_sig("8B 0D ?? ?? ?? ?? 85 C9")
    import re
    assert re.search(pat, b"\x8B\x0D\x01\x02\x03\x04\x85\xC9", re.DOTALL)
    assert not re.search(pat, b"\x8B\x0D\x01\x02\x03\x04\x85\xC0", re.DOTALL)


def test_generalize_masks_rel32_abs_and_shiftable():
    raw = b"\xE8\x01\x02\x03\x04" + u32(0x6A9EC0) + b"\x8B\x81" + u32(0x168) + b"\x33\xC0"
    mask = A.generalize(raw, (0x400000, 0x800000))
    assert mask[0] is True and mask[1:5] == [False] * 4          # rel32
    assert mask[5:9] == [False] * 4                               # absolute
    assert mask[9:11] == [True, True] and mask[11:15] == [False] * 4  # shiftable disp
    assert mask[15:] == [True, True]
    # truncated rel32 at the edge is masked too
    assert A.generalize(b"\x33\xC0\xE8\x4D\x41", (0x400000, 0x800000))[3:] == [False, False]


def test_default_anchor_table_shape():
    kinds = {a.kind for a in A.DEFAULT_ANCHORS}
    assert {"sun_cap", "lawn_global", "null_check", "board_shift", "function", "patch"} <= kinds
    assert len(A.SUN_CAP_ANCHORS) >= 3
    names = {a.name for a in A.FUNCTION_ANCHORS}
    assert {"functions.put_plant", "functions.cob_fire", "functions.pick_zombie_waves"} <= names
    for a in A.DEFAULT_ANCHORS:
        a.compiled()  # every pattern must compile


# ---------------------------------------------------------------------------
# synthetic scan
# ---------------------------------------------------------------------------
def test_synthetic_sun_votes(synth):
    _, res = synth
    assert res.found["board.sun"] == 0x5560
    assert res.confidence["board.sun"] > 0.8
    assert "votes=3/3" in " ".join(res.evidence["board.sun"])


def test_synthetic_lawn_by_family_not_count(synth):
    _, res = synth
    assert res.found["globals.lawn_app"] == LAWN
    assert res.family == "retail"
    assert res.found["lawn_app.board"] == 0x768
    assert res.found["lawn_app.game_mode"] == 0x7F8
    assert res.found["lawn_app.game_ui"] == 0x7FC
    ev = " ".join(res.evidence["globals.lawn_app"])
    assert "most-referenced global was 0x6A9F00" in ev
    assert "null_checks=1" in ev


def test_synthetic_board_shift_and_derived(synth):
    _, res = synth
    assert res.found["board.shift"] == 0
    assert res.found["board.main_counter"] == 0x5568
    assert res.found["board.wave"] == 0x557C
    assert res.confidence["board.shift"] >= 0.9


def test_synthetic_function_and_patch(synth):
    text, res = synth
    pp = res.found["functions.put_plant"]
    assert text[pp - TEXT_VA:pp - TEXT_VA + 3] == b"\x51\x53\x55"
    site = res.found["patches.sun_cap_unlock.0"]
    assert text[site - TEXT_VA] == 0x7E
    assert "functions.cob_fire" in res.missing


def test_patch_off_bytes_mismatch_rejected():
    anchor = A.Anchor("patches.x.0", "patch", A.compile_sig("AA BB ?? DD"), (), ("in_text", "eq_off"), "==1",
                      {"site_offset": 2, "off": "7e", "on": "eb"})
    res = scan_image(b"\x00\xAA\xBB\xEB\xDD\x00", TEXT_VA, DATA, [anchor])
    assert "patches.x.0" in res.missing and "patches.x.0" not in res.found
    res2 = scan_image(b"\x00\xAA\xBB\x7E\xDD\x00", TEXT_VA, DATA, [anchor])
    assert res2.found["patches.x.0"] == TEXT_VA + 3


def test_function_requires_unique_hit():
    anchor = A.Anchor("functions.f", "function", A.compile_sig("51 53 55 56"), (), ("in_text",), "==1")
    res = scan_image(b"\x51\x53\x55\x56\x90\x51\x53\x55\x56", TEXT_VA, DATA, [anchor])
    assert "functions.f" in res.missing


def test_board_shift_disagreement_is_missing():
    # sun anchor says 0x5578 but the histogram is clearly at shift 0 → reject
    parts = [b"\xC7\x80" + u32(0x5578) + u32(9990)]
    parts += [b"\x8B\x86" + u32(0x5578)] * 3   # direct-access cross-check passes
    for off in A.BOARD_1051_HIGH_FIELDS.values():
        parts += [b"\x8B\x86" + u32(off)] * 4
    res = scan_image(b"\x90".join(parts), TEXT_VA, DATA)
    assert res.found["board.sun"] == 0x5578
    assert "board.shift" in res.missing


def test_lawn_missing_when_no_family():
    res = scan_image(b"\x8B\x0D" + u32(OTHER) + b"\x8B\x81" + u32(0x884), TEXT_VA, DATA)
    assert "globals.lawn_app" in res.missing


# ---------------------------------------------------------------------------
# Steam exe
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def steam():
    if not STEAM_EXE.exists():
        pytest.skip("Steam exe not present")
    t0 = time.perf_counter()
    res, pe = scan_exe(STEAM_EXE)
    return res, pe, time.perf_counter() - t0


@pytest.fixture(scope="module")
def toolkit_1096():
    d = json.loads(TOOLKIT_JSON.read_text(encoding="utf-8"))
    return d["versions"]["goty_1_2_0_1096_en"]["addresses"]


@needs_steam
def test_steam_timing(steam):
    res, _pe, wall = steam
    assert wall < 10.0, wall
    assert res.elapsed < 10.0


@needs_steam
def test_steam_core_addresses(steam):
    res, pe, _ = steam
    assert pe.stamp == 0x4D02B058
    assert res.found["board.sun"] == 0x5578
    assert res.found["globals.lawn_app"] == 0x731C50
    assert res.found["lawn_app.board"] == 0x868
    assert res.found["lawn_app.game_mode"] == 0x918
    assert res.found["lawn_app.game_ui"] == 0x91C
    assert res.found["board.shift"] == 0x18
    assert res.found["board.main_counter"] == 0x5580
    assert res.found["board.wave"] == 0x5594
    assert res.found["board.countdown"] == 0x55B4
    assert res.family == "goty"
    assert res.confidence["board.shift"] >= 0.9


@needs_steam
def test_steam_lawn_found_by_family_not_hit_count(steam):
    res, _, _ = steam
    ev = " ".join(res.evidence["globals.lawn_app"])
    assert "family=goty" in ev
    # the most-referenced global is not LawnApp; the naive pick 0x731C90 must not win
    assert "most-referenced global was 0x71FEE4" in ev
    assert res.found["globals.lawn_app"] != 0x731C90


@needs_steam
def test_steam_function_anchors(steam, toolkit_1096):
    res, _, _ = steam
    expect = {
        "put_plant": "call_put_plant", "pick_zombie_waves": "call_pick_zombie_waves",
        "put_zombie": "call_put_zombie", "delete_plant": "call_delete_plant",
        "put_plant_imitater": "call_put_plant_imitater", "mower_start": "call_start_lawn_mower",
        "sync_profile": "call_sync_profile", "wisdom_tree_grow": "call_wisdom_tree",
    }
    for ours, theirs in expect.items():
        assert res.found["functions." + ours] == int(toolkit_1096[theirs], 16), ours
    assert res.found["functions.cob_fire"] == 0x46D1F0
    assert not [m for m in res.missing if m.startswith("functions.")]


@needs_steam
def test_steam_patch_sites(steam):
    res, pe, _ = steam
    prof = P.profile_for_stamp(0x4D02B058)
    assert prof is not None
    text_bytes = None
    from pvztools._win.pe import extract_game_pe
    _off, pe2, img = extract_game_pe(STEAM_EXE.read_bytes())
    t = pe2.text
    text_bytes = img[t.raw_ptr:t.raw_ptr + t.raw_size]
    for gname, grp in prof.patches.items():
        for i, site in enumerate(grp.sites):
            if gname == "userdata_readonly" and i == 2:
                continue  # on/off swapped in the generated profile (reported)
            key = "patches.%s.%d" % (gname, i)
            assert res.found.get(key) == site.va(prof.image_base), key
            va = res.found[key]
            assert text_bytes[va - t.va:va - t.va + len(site.off)] == site.off, key
    # the Steam-only third userdata site: bytes in the exe are 6A FF 68 (off)
    va = res.found["patches.userdata_readonly.2"]
    assert va == 0x498440 and text_bytes[va - t.va:va - t.va + 3] == bytes.fromhex("6aff68")
    assert not [m for m in res.missing if m.startswith("patches.")]


@needs_steam
def test_steam_sun_patch_site_equals_cmp_site(steam):
    res, _, _ = steam
    assert res.found["patches.sun_cap_unlock.0"] == 0x41F4E5


@needs_steam
def test_derive_signatures_roundtrip():
    prof = P.profile_for_stamp(0x4D02B058)
    fs = A.derive_function_signatures(STEAM_EXE, prof, names=("put_plant", "pick_zombie_waves"))
    assert fs["put_plant"]["ok"] and fs["put_plant"]["hits"] == 1
    ps = A.derive_patch_signatures(STEAM_EXE, prof)
    assert ps["sun_cap_unlock"][0]["ok"]
    anchors = A.anchors_from_profile(STEAM_EXE, prof)
    assert any(a.name == "functions.put_plant" for a in anchors)


# ---------------------------------------------------------------------------
# resolve pipeline
# ---------------------------------------------------------------------------
@needs_steam
def test_resolve_table_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PVZTOOLS_CACHE_DIR", str(tmp_path))
    r = R.resolve(STEAM_EXE)
    assert r.source == "table" and r.capability == R.FULL
    assert r.profile.id == "goty_1_2_0_1096_en"
    assert r.address_book["board.sun"] == 0x5578
    assert not [v for v in r.validation if not v[1]]


@needs_steam
def test_resolve_without_table_uses_scan_then_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PVZTOOLS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(R.P, "profile_for_stamp", lambda stamp: None)
    t0 = time.perf_counter()
    r = R.resolve(STEAM_EXE)
    assert time.perf_counter() - t0 < 10.0
    assert r.source == "scan"
    assert R.CAPABILITIES.index(r.capability) >= R.CAPABILITIES.index(R.READ_WRITE)
    assert r.profile.board.sun == 0x5578
    assert r.profile.globals.lawn_app == 0x731C50
    assert r.profile.lawn_app.board == 0x868
    assert r.profile.lawn_app.game_ui == 0x91C
    assert r.profile.function_va("put_plant") == 0x4105A0
    assert r.profile.function_va("cob_fire") == 0x46D1F0
    assert r.profile.family == "goty" and r.profile.conventions == "goty"
    assert r.sources["board.sun"] == "scan" and r.sources["board.cursor"] == "template"
    assert list(tmp_path.glob("4d02b058_41e000_*.json"))
    # scanned book agrees with the shipped table except the extras
    table = P.profile_for_id("goty_1_2_0_1096_en").as_address_book()
    diff = {k for k in set(table) | set(r.address_book) if table.get(k) != r.address_book.get(k)}
    # extras = the design-note functions (cob_fire, coin_collect, chooser / menu
    # entries) that no signature finds, plus exe_path_str
    import importlib.util as _ilu
    from pathlib import Path as _Path

    spec = _ilu.spec_from_file_location("import_toolkit_profiles",
                                        _Path(__file__).resolve().parents[1] / "tools" / "import_toolkit_profiles.py")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    allowed = {"functions." + k for k in mod.DESIGN_FUNCTIONS_1096} | {"functions.cob_fire", "globals.exe_path_str"}
    assert diff <= allowed, diff
    # second call: cache hit, no rescan
    r2 = R.resolve(STEAM_EXE)
    assert r2.source == "cache" and r2.address_book["board.sun"] == 0x5578
    assert r2.scan is not None and r2.scan.found["globals.lawn_app"] == 0x731C50


@needs_steam
def test_doctor_and_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("PVZTOOLS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(R.P, "profile_for_stamp", lambda stamp: None)
    r = R.resolve(STEAM_EXE)
    rep = R.doctor_report(r)
    assert "board.sun" in rep and "scan" in rep and "capability" in rep
    out = R.export_diagnostics(r, tmp_path / "diag" / "bundle.json")
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["symbols"]["board.sun"]["value"] == "0x5578"
    assert d["capability"] == r.capability and d["scan"]["family"] == "goty"
    assert "text" not in d["scan"]  # no binary content
    assert out.stat().st_size < 2_000_000


def test_cache_key_weak():
    assert R.cache_key(1, 2, None) == ("00000001_2.json", True)
    assert R.cache_key(1, 2, "ab" * 32)[1] is False


def test_capability_grading_rules(tmp_path, monkeypatch):
    """Missing sun → UNSUPPORTED; missing core function → READ_WRITE."""
    monkeypatch.setenv("PVZTOOLS_CACHE_DIR", str(tmp_path))
    text = _synthetic_text()
    res = scan_image(text, TEXT_VA, DATA)
    identity = {"stamp": 0x12345678, "size_of_image": 0x300000, "sha256": None}
    prof, srcs = R._profile_from_scan(res, identity, 0x400000)
    assert prof.board.sun == 0x5560 and prof.globals.lawn_app == LAWN
    assert prof.function_va("put_plant") == TEXT_VA + text.index(b"\x51\x53\x55\x8B\x6C")
    assert prof.functions["pick_zombie_waves"].rva is None
    assert prof.patches["sun_cap_unlock"].enabled and not prof.patches["auto_collect"].enabled


# ---------------------------------------------------------------------------
# live (read-only)
# ---------------------------------------------------------------------------
def test_live_scan_and_resolve(tmp_path, monkeypatch):
    try:
        from pvztools.process import attach, GameNotRunning, ProcessError
    except Exception:
        pytest.skip("process module unavailable")
    try:
        s = attach()
    except Exception as e:  # GameNotRunning / AccessDenied / MultipleGames
        pytest.skip("game not available: %r" % (e,))
    with s:
        from pvztools.addr.scan import scan_live
        res = scan_live(s)
        assert res.elapsed < 10.0
        assert res.found["globals.lawn_app"] == 0x731C50 or res.family in ("retail", "goty", "goty_zh")
        monkeypatch.setenv("PVZTOOLS_CACHE_DIR", str(tmp_path))
        r = R.resolve(s)
        assert r.ok
        live = r.identity.get("live", {})
        assert live.get("lawn_app", 0) > 0x10000
        monkeypatch.setattr(R.P, "profile_for_stamp", lambda stamp: None)
        r2 = R.resolve(s, use_cache=False)
        assert r2.source == "scan" and R.CAPABILITIES.index(r2.capability) >= 2
        assert r2.profile.board.sun == r.profile.board.sun
