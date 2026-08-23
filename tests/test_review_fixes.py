"""Regression tests for the review findings (patch rollback, GC-safe patches,
pointer whitelist, capability enforcement, scan cross-checks, CLI units,
UI names, model enum types, kernel32 helpers)."""
from __future__ import annotations

import gc
import struct
import sys

import pytest

from pvztools import game as game_mod
from pvztools.addr import anchors as A
from pvztools.addr import profile_for_stamp
from pvztools.addr import resolve as R
from pvztools.addr.scan import ScanResult, _family_candidates, _family_score, scan_image
from pvztools.game import CapabilityDenied, Game, NotImplementedAction, UnknownBuild, attach
from pvztools.model import Plant, Seed, Zombie
from pvztools.modifier import (NON_WRITABLE_FIELDS, UI_NAMES, PatchError, PatchManager, Safety,
                               WhitelistError, Writer, capability_of)
from pvztools.modifier import patches as patches_mod
from pvztools.schema.enums import PlantType, ZombieType
from test_game_api import BOARD, LAWN, STAMP_1096, make_session

TEXT_VA = 0x401000
DATA = (0x6A0000, 0x6B0000)


def u32(v: int) -> bytes:
    return struct.pack("<I", v)


def live_safety(**kw) -> Safety:
    kw.setdefault("allow_write", True)
    kw.setdefault("allow_patch", True)
    kw.setdefault("dry_run", False)
    return Safety(**kw)


def site_bytes(s, p, group, i=0, state="on"):
    site = p.patches[group].sites[i]
    want = site.on if state == "on" else site.off
    return s.read(site.va(p.image_base), len(want)) == bytes(want)


# ---------------------------------------------------------------------------
# 1. multi-site patch group is all-or-nothing
# ---------------------------------------------------------------------------

def test_partial_patch_group_rolls_back_on_write_failure():
    s, p = make_session()
    calls = {"n": 0}
    real_write = s.write

    def flaky_write(addr, data):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("write failed")
        real_write(addr, data)

    s.write = flaky_write
    pm = PatchManager(s, p, live_safety())
    assert len(p.patches["plant_invincible"].sites) >= 3
    with pytest.raises(OSError):
        pm.enable("plant_invincible")
    # the two sites written before the failure were rolled back
    assert pm.read_state("plant_invincible") == "off"
    assert pm.enabled == set()
    assert pm not in patches_mod._managers
    assert any(e.what == "patch_on_rollback" for e in pm.safety.journal)


def test_partial_patch_group_unrollbackable_site_stays_tracked():
    s, p = make_session()
    calls = {"n": 0}
    real_write = s.write

    def broken_write(addr, data):
        calls["n"] += 1
        if calls["n"] >= 3:   # 3rd write fails and every later write (rollback) too
            raise OSError("write failed")
        real_write(addr, data)

    s.write = broken_write
    pm = PatchManager(s, p, live_safety())
    with pytest.raises(OSError):
        pm.enable("plant_invincible")
    # rollback could not undo sites 0/1: the group stays on the books so
    # restore()/atexit keep trying
    assert pm.read_state("plant_invincible") == "mixed"
    assert pm.enabled == {"plant_invincible"}
    assert pm in patches_mod._managers
    s.write = real_write
    assert pm.restore() == ["plant_invincible"]
    assert pm.read_state("plant_invincible") == "off"
    assert pm not in patches_mod._managers


# ---------------------------------------------------------------------------
# 2. patches survive GC of the owner until restore_all()
# ---------------------------------------------------------------------------

def test_patches_restored_at_exit_after_game_is_garbage_collected():
    s, p = make_session()

    def f():
        g = Game(s, p, write=True)
        g.cheats.sun_limit = True
        g.cheats.auto_collect = True

    f()
    gc.collect()
    assert site_bytes(s, p, "sun_cap_unlock", state="on")
    patches_mod.restore_all()
    assert site_bytes(s, p, "sun_cap_unlock", state="off")
    assert site_bytes(s, p, "auto_collect", state="off")


def test_patch_manager_dropped_without_restore_is_still_tracked():
    s, p = make_session()

    def f():
        pm = PatchManager(s, p, live_safety())
        pm.enable("auto_collect")

    f()
    gc.collect()
    assert site_bytes(s, p, "auto_collect", state="on")
    patches_mod.restore_all()
    assert site_bytes(s, p, "auto_collect", state="off")
    # a manager with nothing enabled is not kept alive
    pm = PatchManager(s, p, live_safety())
    assert pm not in patches_mod._managers
    pm.enable("auto_collect")
    assert pm in patches_mod._managers
    pm.disable("auto_collect")
    assert pm not in patches_mod._managers


def test_keep_patches_forgets_without_restoring():
    s, p = make_session()
    g = Game(s, p, write=True)
    g.cheats.auto_collect = True
    g.keep_patches()
    assert g._patches_obj not in patches_mod._managers
    patches_mod.restore_all()
    assert site_bytes(s, p, "auto_collect", state="on")


# ---------------------------------------------------------------------------
# 3. pointer / array fields are never writable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("section,name", [
    ("lawn_app", "board"), ("lawn_app", "player_info"), ("lawn_app", "game_selector"),
    ("board", "zombies"), ("board", "plants"), ("board", "seed_bank"), ("board", "mowers"),
    ("board", "grid_items"), ("board", "cursor"), ("board", "challenge"),
    ("board", "zombie_count_max"), ("board", "spawn_list"),
])
def test_pointer_fields_rejected(section, name):
    s, p = make_session()
    w = Writer(s, p, live_safety())
    assert "%s.%s" % (section, name) not in w.safety.whitelist
    with pytest.raises(WhitelistError, match="指针"):
        if section == "board":
            w.set_board_field(name, 0)
        else:
            w.set_lawn_field(name, 0)
    assert s.read_u32(BOARD + p.board.zombies) != 0
    assert s.writes == []


def test_value_fields_still_writable():
    s, p = make_session()
    w = Writer(s, p, live_safety())
    assert w.set_sun(100).new == 100
    assert w.set_game_speed(5).new == 5
    assert "board.sun" in w.safety.whitelist and "lawn_app.frame_duration" in w.safety.whitelist
    assert "board.zombies" in NON_WRITABLE_FIELDS


# ---------------------------------------------------------------------------
# 7. capability grade enforced on writes / patches
# ---------------------------------------------------------------------------

class _Res:
    def __init__(self, cap):
        self.capability = cap


@pytest.mark.parametrize("cap", ["READ_ONLY", "UNSUPPORTED"])
def test_read_only_capability_blocks_facade_writes(cap):
    s, p = make_session()
    p._pvz_resolution = _Res(cap)
    try:
        g = Game(s, p, write=True)
        assert g.capability == cap
        assert g.sun == 75  # reads still fine
        with pytest.raises(CapabilityDenied, match=cap):
            g.sun = 5
        with pytest.raises(CapabilityDenied):
            g.cheats.kill_zombies()
        with pytest.raises(CapabilityDenied):
            g.cheats.sun_limit = True
        assert s.writes == []
    finally:
        del p._pvz_resolution


def test_read_only_capability_blocks_writer_and_patch_manager_directly():
    s, p = make_session()
    p._pvz_resolution = _Res("READ_ONLY")
    try:
        assert capability_of(p) == "READ_ONLY"
        w = Writer(s, p, live_safety())
        with pytest.raises(CapabilityDenied):
            w.set_sun(1)
        pm = PatchManager(s, p, live_safety())
        with pytest.raises(CapabilityDenied):
            pm.enable("auto_collect")
        assert s.writes == []
    finally:
        del p._pvz_resolution
    assert capability_of(p) == "FULL"
    # declared PARTIAL reads as READ_WRITE (writes allowed)
    class P:
        capability = "PARTIAL"
    assert capability_of(P()) == "READ_WRITE"


# ---------------------------------------------------------------------------
# 8 / 12. sun discovery: direct-access cross-check, raised-cap fallback, SIB
# ---------------------------------------------------------------------------

def _board_histogram(shift: int, n: int = 4):
    parts = []
    for off in A.BOARD_1051_HIGH_FIELDS.values():
        parts += [b"\x8B\x86" + u32(off + shift)] * n
    return parts


def test_sun_found_without_9990_clamp_via_histogram_and_direct_refs():
    # raised-cap mod: no 9990 anywhere, board at shift 0x18 with many direct refs
    text = b"\x90".join(_board_histogram(0x18))
    res = scan_image(text, TEXT_VA, DATA)
    assert res.found["board.sun"] == 0x5578
    assert res.confidence["board.sun"] <= 0.5
    assert "board.sun" not in res.missing
    assert any("direct" in e for e in res.evidence["board.sun"])


def test_sun_clamp_without_direct_refs_is_rejected():
    # a 9990 compare on a field nobody touches directly is not the sun
    parts = [b"\xC7\x80" + u32(0x7000) + u32(9990)]
    res = scan_image(b"\x90".join(parts), TEXT_VA, DATA)
    assert "board.sun" in res.missing
    assert "board.sun" not in res.found


def test_sun_immediate_in_register_shape():
    # mov eax,9990 ; cmp eax,[esi+0x5578] -- plus direct references
    parts = [b"\xB8" + u32(9990) + b"\x3B\x86" + u32(0x5578)]
    parts += [b"\x8B\x86" + u32(0x5578)] * 3
    res = scan_image(b"\x90".join(parts), TEXT_VA, DATA, A.SUN_CAP_ANCHORS)
    assert res.found["board.sun"] == 0x5578
    assert "sun_cap.imm_reg_cmp_mem" in res.evidence["board.sun"][-2]


def test_sib_modrm_does_not_vote():
    # 8B 84 (mod=10 rm=100 -> SIB) must not be read as [reg+disp32]
    bad = b"\x8B\x84\x24" + u32(0x5578)[:3] + b"\x00\x81\xF8" + u32(9990)
    res = scan_image(bad, TEXT_VA, DATA, A.SUN_CAP_ANCHORS)
    assert "board.sun" not in res.found
    for an in A.SUN_CAP_ANCHORS:
        if an.name in ("sun_cap.mov_cmp_reg", "sun_cap.imm_reg_cmp_mem"):
            assert an.compiled().search(bad) is None


# ---------------------------------------------------------------------------
# 9. goty vs goty_zh tie is reported, not silently broken
# ---------------------------------------------------------------------------

def test_family_tie_detected():
    assert [c[0] for c in _family_candidates({0x868: 2, 0x91C: 5})] == ["goty", "goty_zh"]
    assert [c[0] for c in _family_candidates({0x868: 2, 0x91C: 5, 0x4B4: 1})] == ["goty", "goty_zh"]
    assert _family_score({0x868: 2, 0x918: 5})[0] == "goty"
    assert [c[0] for c in _family_candidates({0x868: 2, 0x918: 5})] == ["goty"]
    assert [c[0] for c in _family_candidates({0x868: 2, 0x920: 1})] == ["goty_zh"]


def test_family_tie_lowers_confidence_and_marks_fields():
    lawn = 0x6A9EC0
    parts = []
    for disp, n in ((0x868, 2), (0x91C, 3)):
        parts += [b"\x8B\x0D" + u32(lawn) + b"\x8B\x81" + u32(disp) + b"\x90" * 8] * n
    res = scan_image(b"".join(parts), TEXT_VA, DATA)
    assert res.found["globals.lawn_app"] == lawn
    assert res.family_candidates == ["goty", "goty_zh"]
    assert res.confidence["globals.lawn_app"] < 0.6
    assert any("ambiguous family" in e for e in res.evidence["globals.lawn_app"])
    # game_mode (0x918 goty / 0x91C zh) was not observed and the families disagree
    assert "lawn_app.game_mode" in res.missing
    assert res.confidence["lawn_app.game_mode"] <= 0.3
    assert "lawn_app.board" not in res.missing  # agreed by both families
    d = res.as_dict()
    assert d["family_candidates"] == ["goty", "goty_zh"]
    # unambiguous build: no candidates beyond the family
    parts2 = [b"\x8B\x0D" + u32(lawn) + b"\x8B\x81" + u32(d_) + b"\x90" * 8 for d_ in (0x868, 0x918, 0x91C)]
    res2 = scan_image(b"".join(parts2), TEXT_VA, DATA)
    assert res2.family_candidates == ["goty"]


# ---------------------------------------------------------------------------
# 10 / 11 / 13 / 14. resolve: shifted template, PARTIAL, READ_ONLY, UNSUPPORTED
# ---------------------------------------------------------------------------

def _scan_result(shift, family="retail", **extra):
    found = {"globals.lawn_app": 0x6A9EC0, "lawn_app.board": 0x768, "lawn_app.game_mode": 0x7F8,
             "lawn_app.game_ui": 0x7FC, "board.shift": shift, "board.sun": 0x5560 + shift,
             "board.wave": 0x557C + shift}
    found.update(extra)
    conf = {k: 0.9 for k in found}
    return ScanResult(found=found, confidence=conf, family=family, family_candidates=[family])


def test_scan_profile_applies_shift_to_low_board_fields():
    scan = _scan_result(0x20)
    identity = {"stamp": 0x11111111}
    prof, srcs = R._profile_from_scan(scan, identity, 0x400000)
    from pvztools.addr import profile_for_id
    t1051 = profile_for_id("1_0_0_1051_en")
    assert prof.board.sun == 0x5580 and prof.board.wave == 0x559C
    for name in ("zombies", "plants", "seed_bank", "spawn_list", "main_counter"):
        assert prof.board[name] == t1051.board[name] + 0x20, name
        assert srcs["board." + name] == "template+shift"
    assert any("board shift" in w for w in prof.warnings)
    # shift 0 on the retail template: nothing moved
    prof0, srcs0 = R._profile_from_scan(_scan_result(0), identity, 0x400000)
    assert prof0.board.zombies == t1051.board.zombies and srcs0["board.zombies"] == "template"


def test_grade_partial_alias_and_shift_cross_check():
    from pvztools.addr import profile_for_id
    p = profile_for_id("goty_1_2_0_1096_en")

    class Prof:
        warnings = []
        functions = p.functions
        capability = "PARTIAL"

    res = R.Resolution(profile=p, source="table", capability="FULL", address_book={})
    assert R._grade(Prof(), R.FULL, res) == R.READ_WRITE
    # histogram-only shift (confidence 0.5) -> READ_ONLY per the module doc
    scan = _scan_result(0x18, "goty")
    scan.confidence["board.shift"] = 0.5
    res = R.Resolution(profile=p, source="scan", capability="FULL", address_book={}, scan=scan)
    Prof.capability = "FULL"
    assert R._grade(Prof(), R.FULL, res) == R.READ_ONLY
    assert any("cross-checked" in r for r in res.report)
    # ambiguous family -> READ_ONLY
    scan = _scan_result(0x18, "goty")
    scan.family_candidates = ["goty", "goty_zh"]
    res = R.Resolution(profile=p, source="scan", capability="FULL", address_book={}, scan=scan)
    assert R._grade(Prof(), R.FULL, res) == R.READ_ONLY
    # beta profile declares PARTIAL -> never FULL
    beta = profile_for_id("beta_0_1_1_1014_en")
    if beta is not None:
        res = R.Resolution(profile=beta, source="table", capability="FULL", address_book={})
        assert R._grade(beta, R.FULL, res) != R.FULL


def test_resolve_failed_scan_grades_unsupported(monkeypatch, tmp_path):
    monkeypatch.setenv("PVZTOOLS_CACHE_DIR", str(tmp_path))
    s, p = make_session()
    s.stamp = 0x48ECEE74
    empty = ScanResult(missing=["board.sun", "globals.lawn_app", "board.shift"])
    monkeypatch.setattr(R._Target, "do_scan", lambda self: empty)

    class FakePE:
        stamp = 0x48ECEE74
        size_of_image = 0x204000
        image_base = 0x400000
        text = None
        sections = []

        def section(self, name):
            return None

    monkeypatch.setattr(R._Target, "ensure_pe", lambda self: setattr(self, "pe", FakePE()))
    res = R.resolve(s)
    assert res.capability == R.UNSUPPORTED
    assert "globals.lawn_app" in res.missing
    assert any("UNSUPPORTED" in r for r in res.report)
    assert all(v == 0.0 for v in res.confidence.values())
    report = R.doctor_report(res)
    assert "UNSUPPORTED" in report and "missing (3)" in report
    # the facade turns it into UnknownBuild, naming the missing addresses
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    with pytest.raises(UnknownBuild, match="globals.lawn_app"):
        attach()
    assert s.closed


def test_cli_doctor_reports_unsupported_build(monkeypatch, capsys, tmp_path):
    from pvztools.cli.__main__ import EXIT_GAME, main
    import pvztools.process as proc

    s, p = make_session()

    class FakeRes:
        capability = "UNSUPPORTED"
        missing = ["board.sun", "globals.lawn_app", "functions.put_plant"]
        profile = p

    def fake_attach(pid, write=False):
        raise UnknownBuild("不认识这个游戏版本")

    monkeypatch.setattr(game_mod, "_process_attach", fake_attach)
    class CMSession:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __enter__(self):
            return self._inner

        def __exit__(self, *exc):
            self._inner.close()

    monkeypatch.setattr(proc, "attach", lambda pid=None, write=False: CMSession(s))
    monkeypatch.setattr(R, "resolve", lambda target: FakeRes())
    monkeypatch.setattr(R, "doctor_report", lambda res: "DOCTOR %s" % res.capability)
    monkeypatch.setattr(R, "export_diagnostics", lambda res, path: path)
    out = str(tmp_path / "d.json")
    assert main(["doctor", "--json", out]) == EXIT_GAME
    cap = capsys.readouterr()
    assert "DOCTOR UNSUPPORTED" in cap.out and "diagnostics written" in cap.out
    assert "board.sun, globals.lawn_app" in cap.err and "1 项" in cap.err


# ---------------------------------------------------------------------------
# 4 / 17 / 22. CLI: --patch alone, speed units, on|off error text
# ---------------------------------------------------------------------------

def test_patch_only_does_not_grant_allow_write(monkeypatch):
    s, p = make_session()
    seen = {}

    def fake_attach(pid, write=False):
        seen["write"] = write
        return s

    monkeypatch.setattr(game_mod, "_process_attach", fake_attach)
    g = attach(write=False, patch=True)
    assert seen["write"] is True           # process opened for writing (patches need it)
    assert g.safety.allow_write is False and g.safety.allow_patch is True
    assert not g.safety.dry_run
    from pvztools.modifier import PermissionDenied
    with pytest.raises(PermissionDenied):
        g.sun = 5
    g.cheats.auto_collect = True
    g.close()


def test_cli_open_game_passes_flags_separately(monkeypatch):
    from pvztools.cli.__main__ import _open_game
    import argparse
    seen = {}

    def fake_attach(pid=None, *, write=False, profile=None, patch=None):
        seen.update(write=write, patch=patch)
        return "game"

    import pvztools.game as G
    monkeypatch.setattr(G, "attach", fake_attach)
    _open_game(argparse.Namespace(pid=None), write=False, patch=True)
    assert seen == {"write": False, "patch": True}


def test_on_off_error_message_is_readable(capsys):
    from pvztools.cli.__main__ import main
    with pytest.raises(SystemExit):
        main(["mod", "no-cooldown", "yes", "--write", "--yes"])
    err = capsys.readouterr().err
    assert "invalid on|off value: 'yes'" in err and "_on" not in err


# ---------------------------------------------------------------------------
# 16. one UI name table
# ---------------------------------------------------------------------------

def test_ui_names_shared_and_chinese():
    from pvztools.modifier import GameStateError
    assert game_mod.UI_NAMES is UI_NAMES
    assert all(not any("a" <= ch.lower() <= "z" for ch in v) for v in UI_NAMES.values())
    s, p = make_session(ui=4)
    g = Game(s, p, write=True)
    name = g.ui_name
    with pytest.raises(GameStateError) as ei:
        g.sun = 5
    assert ("当前 4：%s" % name) in str(ei.value)
    assert "zen" not in str(ei.value)


# ---------------------------------------------------------------------------
# 18 / 20. model types
# ---------------------------------------------------------------------------

def test_zombie_plant_seed_types_are_enums_when_known():
    s, p = make_session()
    g = Game(s, p)
    z = g.zombies[0]
    assert isinstance(z.type, ZombieType) and z.type == ZombieType.ZOMBIE and z.type == 0
    pl = g.plants[0]
    assert isinstance(pl.type, PlantType) and pl.type is PlantType.SUNFLOWER
    sd = g.seeds[1]
    assert isinstance(sd.type, PlantType) and sd.plant_type is PlantType.SUNFLOWER and not sd.empty
    # unknown ids (mods) stay plain ints
    z2 = Zombie(index=0, row=0, type=999, name_zh="x", status=0, x=0.0, y=0.0, speed=0.0,
                hp=1, max_hp=1, helmet_hp=0, shield_hp=0, dead=False)
    assert type(z2.type) is int and z2.zombie_type is None
    p2 = Plant(index=0, row=0, col=0, type=999, name_zh="x", state=0, hp=1, max_hp=1, dead=False,
               squished=False, asleep=False, imitater=False)
    assert type(p2.type) is int


def test_empty_seed_slot():
    e = Seed(slot=0, type=-1, imitater_type=-1, cd_elapsed=0, cd_total=0, usable=True)
    assert e.empty and e.name == "（空）" and not e.ready and e.plant_type is None
    full = Seed(slot=0, type=0, imitater_type=0, cd_elapsed=0, cd_total=750, usable=True)
    assert not full.empty and full.ready and full.name == "豌豆射手"


def test_cli_read_seeds_marks_empty_slots(monkeypatch, capsys):
    from pvztools.cli.__main__ import EXIT_OK, main
    s, p = make_session()
    sbl, pk = p.objects["seed_bank"], p.objects["seed_packet"]
    from test_game_api import SEEDS
    s.poke_i32(SEEDS + sbl.packets + 2 * pk.stride + pk.type, -1)
    monkeypatch.setattr(game_mod, "_process_attach", lambda pid, write=False: s)
    assert main(["read", "seeds"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "（空）" in out and "empty" in out and "未知(-1)" not in out


# ---------------------------------------------------------------------------
# 19 / 21. facade: action stubs, speed setter validation
# ---------------------------------------------------------------------------

def test_unimplemented_actions_explain_themselves():
    s, p = make_session()
    g = Game(s, p, write=True)
    # plant()/shovel() are implemented now; without remote=True they refuse
    # with a PermissionDenied that names the missing flag.
    from pvztools.modifier.safety import PermissionDenied
    for call in (lambda: g.plant("豌豆射手", row=3, col=1), lambda: g.shovel(1, 1)):
        with pytest.raises(PermissionDenied, match="allow_remote_call"):
            call()
    with pytest.raises(PermissionDenied, match="allow_remote_call"):
        g.fire_cob((1, 1), 2, 7.5)
    assert issubclass(NotImplementedAction, NotImplementedError)


def test_speed_setter_validation():
    s, p = make_session()
    g = Game(s, p, write=True)
    with pytest.raises(TypeError, match="数字"):
        g.cheats.speed = "fast"
    with pytest.raises(ValueError, match="speed=100"):
        g.cheats.speed = 100
    with pytest.raises(ValueError, match="speed=0.001"):
        g.cheats.speed = 0.001
    with pytest.raises(ValueError):
        g.cheats.speed = 0
    g.cheats.speed = 10
    assert g.cheats.frame_duration == 1
    g.cheats.speed = 0.5
    assert g.cheats.frame_duration == 20


# ---------------------------------------------------------------------------
# 5 / 6. kernel32 helpers
# ---------------------------------------------------------------------------

def test_kernel32_write_helpers_reject_out_of_range(monkeypatch):
    from pvztools._win import kernel32 as k
    written = []
    monkeypatch.setattr(k, "write", lambda h, a, d: written.append(d))
    with pytest.raises(ValueError):
        k.write_u8(0, 0, 300)
    with pytest.raises(ValueError):
        k.write_u16(0, 0, -1)
    with pytest.raises(ValueError):
        k.write_u32(0, 0, 1 << 32)
    with pytest.raises(ValueError):
        k.write_i32(0, 0, 0x80000000)
    with pytest.raises(TypeError):
        k.write_u8(0, 0, "1")
    k.write_u8(0, 0, 255)
    k.write_i32(0, 0, -1)
    assert written == [b"\xff", b"\xff\xff\xff\xff"]


def test_main_module_error_does_not_use_stale_last_error(monkeypatch):
    from pvztools._win import kernel32 as k
    monkeypatch.setattr(k, "enum_modules", lambda h, flt: [])
    monkeypatch.setattr(k, "last_error", lambda: 299)
    with pytest.raises(k.WinApiError) as ei:
        k.main_module(1)
    assert ei.value.code == 0 and "299" not in str(ei.value) and "32-bit" in str(ei.value)
