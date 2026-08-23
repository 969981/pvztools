"""Tests for pvztools.addr (profile data layer) and tools/import_toolkit_profiles.py."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from pvztools.addr import (
    PROFILE_DIR,
    Profile,
    ProfileError,
    check_goty_delta,
    list_profiles,
    load_index,
    load_profile,
    profile_for_id,
    profile_for_stamp,
    profile_from_dict,
)

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "import_toolkit_profiles.py"
STEAM_STAMP = 0x4D02B058
V1051_STAMP = 0x49ECF563


def _load_tool():
    spec = importlib.util.spec_from_file_location("import_toolkit_profiles", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """Run the import tool into a temp dir; returns the output dir."""
    mod = _load_tool()
    out = tmp_path_factory.mktemp("profiles")
    mod.OUTPUT_DIR = out
    assert mod.main([]) == 0
    return out


# --- generator ---------------------------------------------------------------

def test_tool_generates_14_profiles_and_index(generated):
    files = sorted(p.name for p in generated.glob("*.json"))
    assert "index.json" in files
    assert len(files) - 1 == 14
    idx = json.loads((generated / "index.json").read_text(encoding="utf-8"))
    assert len(idx["by_stamp"]) == 14
    assert len(idx["profiles"]) == 14
    assert idx["by_stamp"]["0x4d02b058"] == "goty_1_2_0_1096_en.json"
    assert idx["by_stamp"]["0x49ecf563"] == "1_0_0_1051_en.json"


def test_generated_matches_shipped(generated):
    """Shipped profiles must be the tool's output (regenerate if this fails)."""
    for f in generated.glob("*.json"):
        shipped = PROFILE_DIR / f.name
        assert shipped.exists(), f.name
        assert json.loads(shipped.read_text(encoding="utf-8")) == json.loads(
            f.read_text(encoding="utf-8")), f.name


def test_generated_profiles_all_load_and_validate(generated):
    for f in generated.glob("*.json"):
        if f.name == "index.json":
            continue
        p = load_profile(f)
        assert isinstance(p, Profile)
        assert p.image_base == 0x400000


def test_hack_bytes_are_little_endian(generated):
    p = load_profile(generated / "1_0_0_1051_en.json")
    # toolkit rake_unlimited: uint16 0x800f -> bytes 0f 80
    site = p.patches["rake_unlimited"].sites[0]
    assert site.on == bytes.fromhex("0f80")
    assert site.off == bytes.fromhex("0f84")
    assert len(site.on) == len(site.off)


# --- shipped index / lookups ---------------------------------------------------

def test_index_maps_steam_stamp():
    p = profile_for_stamp(STEAM_STAMP)
    assert p is not None
    assert p.id == "goty_1_2_0_1096_en"
    assert p.family == "goty"
    assert p.stamp == STEAM_STAMP
    assert p.board.sun == 0x5578
    assert p.lawn_app.board == 0x868
    assert p.globals.lawn_app == 0x731C50
    assert p.globals_rva.lawn_app == 0x331C50
    assert p.board.spawn_list == 0x6CC
    assert p.lawn_app.game_ui == 0x91C
    assert p.lawn_app.game_mode == 0x918
    assert p.board.main_counter == 0x5580
    assert p.board.clock == 0x5580  # alias
    assert p.board.wave == 0x5594
    assert p.board.countdown == 0x55B4
    assert p.board.zombies == 0xA8
    assert p.board.plants == 0xC4
    assert p.objects["zombie"].stride == 0x15C + 0xC
    assert p.objects["plant"].stride == 0x14C
    assert p.verified is True
    assert p.achievements_count == 21
    assert len(p.patches["userdata_readonly"].sites) == 3
    assert profile_for_stamp("0x4d02b058").id == p.id


def test_1051_profile():
    p = profile_for_stamp(V1051_STAMP)
    assert p is not None
    assert p.id == "1_0_0_1051_en"
    assert p.family == "retail"
    assert p.board.sun == 0x5560
    assert p.lawn_app.board == 0x768
    assert p.globals.lawn_app == 0x6A9EC0
    assert p.board.spawn_list == 0x6B4
    assert p.board.wave == 0x557C
    assert p.board.countdown == 0x559C
    assert p.objects["zombie"].stride == 0x15C
    assert p.objects["zombie"].dead == 0xEC
    assert p.objects["zombie"].hp == 0xC8
    assert p.objects["plant"].hp == 0x40
    assert p.objects["seed_bank"].packets == 0x28
    assert p.objects["seed_packet"].cd_elapsed == 0x24
    assert p.objects["seed_packet"].type == 0x34
    assert p.objects["seed_packet"].stride == 0x50
    assert p.functions["put_plant"].va(p.image_base) == 0x40D120
    assert p.functions["cob_fire"].va(p.image_base) == 0x466D50
    assert p.patches["sun_cap_unlock"].sites[0].va(p.image_base) == 0x430A23
    assert p.patches["sun_cap_unlock"].sites[0].on == b"\xeb"
    assert p.patches["plant_invincible"].excludes == ("plant_fragile",)
    assert len(p.patches["plant_invincible"].sites) == 9
    assert p.verified is True
    assert p.player_info.get("achievements") is None


def test_unknown_stamp_and_listing():
    assert profile_for_stamp(0x12345678) is None
    entries = list_profiles()
    assert len(entries) == 14
    ids = {e["id"] for e in entries}
    assert "goty_1_2_0_1096_en" in ids and "1_0_0_1051_en" in ids
    assert profile_for_id("1_2_0_1065_en").globals.lawn_app == 0x6A9EC0
    idx = load_index()
    assert "popcapgame1.exe" in idx["process_names"]


def test_family_assignment():
    fam = {e["id"]: e["family"] for e in list_profiles()}
    assert fam["beta_0_1_1_1014_en"] == "beta"
    assert fam["1_0_7_3467_ru"] == "retail"
    assert fam["goty_1_2_0_1073_en"] == "goty"
    assert fam["goty_1_1_0_1056_zh"] == "goty_zh2010_ja"
    assert fam["goty_1_1_0_1056_zh_2012_07"] == "goty_zh2012"


def test_beta_disabled_features_and_warnings():
    p = profile_for_id("beta_0_1_1_1014_en")
    assert p.capability == "PARTIAL"
    assert p.patches["unlimited_tree_food"].enabled is False
    assert p.functions["wisdom_tree_grow"].enabled is False
    assert p.functions["wisdom_tree_grow"].rva is None
    assert "plant_immune_eat" in p.patches["plant_invincible"].disabled_sites
    assert p.objects["zombie"].stride == 0x160
    assert p.objects["grid_item"].stride == 0x8C
    assert profile_for_id("goty_1_1_0_1056_zh").warnings == ["unstable"]


def test_extra_keeps_unmapped_toolkit_names():
    p = profile_for_stamp(V1051_STAMP)
    # put_rake row/col sites are folded into functions.put_rake.temp_patch
    assert p.functions["put_rake"].temp_patch["row_site"] is not None
    # nothing mapped should leak into extra, and extra holds no semantic keys
    assert "sun" not in p.extra and "board" not in p.extra
    for k in p.extra:
        assert k in p.raw["extra"]


# --- round trip / address book ----------------------------------------------

def test_round_trip(tmp_path):
    p = profile_for_stamp(STEAM_STAMP)
    out = tmp_path / "copy.json"
    out.write_text(json.dumps(p.raw), encoding="utf-8")
    q = load_profile(out)
    assert q.as_address_book() == p.as_address_book()
    assert q.id == p.id and q.stamp == p.stamp


def test_address_book_flat():
    p = profile_for_stamp(STEAM_STAMP)
    book = p.as_address_book()
    assert book["globals.lawn_app"] == 0x731C50
    assert book["board.sun"] == 0x5578
    assert book["lawn_app.board"] == 0x868
    assert book["objects.zombie.stride"] == 0x168
    assert book["functions.put_plant"] == 0x4105A0
    assert all(isinstance(v, int) for v in book.values())
    assert p.lookup("board.sun") == 0x5578
    assert p.lookup("board.clock") == 0x5580
    assert p.lookup("objects.plant.stride") == 0x14C
    assert p.lookup("nope.nope") is None


def test_gates_present():
    p = profile_for_stamp(STEAM_STAMP)
    assert p.gates["apply_lineup"]["mode"] == [[11, 15], [61, 70]]
    assert p.gates["fill_flower_pots"]["scene"] == [4, 5]
    assert p.gates["mower_ops"]["ui"] == [3]


# --- validation ---------------------------------------------------------------

def test_validation_rejects_bad_hex():
    p = profile_for_stamp(V1051_STAMP)
    d = json.loads(json.dumps(p.raw))
    d["board"]["sun"] = "zz"
    with pytest.raises(ProfileError):
        profile_from_dict(d)
    d = json.loads(json.dumps(p.raw))
    del d["globals"]["lawn_app"]
    with pytest.raises(ProfileError):
        profile_from_dict(d)
    d = json.loads(json.dumps(p.raw))
    d["family"] = "mystery"
    with pytest.raises(ProfileError):
        profile_from_dict(d)
    d = json.loads(json.dumps(p.raw))
    d["patches"]["sun_cap_unlock"]["sites"][0]["on"] = "ebeb"
    with pytest.raises(ProfileError):
        profile_from_dict(d)


def test_section_attribute_errors():
    p = profile_for_stamp(V1051_STAMP)
    with pytest.raises(AttributeError):
        p.board.no_such_field
    with pytest.raises(AttributeError):
        p.player_info.achievements  # None in retail builds
    assert "clock" in p.board


# --- GOTY delta self-check ------------------------------------------------------

_KNOWN_GOTY_EXCEPTIONS = ("objects.zombie.stride", "objects.zombie.fireball_row",
                          "objects.zombie.is_fireball", "player_info.")

def test_goty_delta_rule_holds_for_steam_except_known():
    p1051 = profile_for_stamp(V1051_STAMP)
    steam = profile_for_stamp(STEAM_STAMP)
    diff = check_goty_delta(p1051, steam)
    assert any(d.startswith("objects.zombie.stride") for d in diff)
    assert any(d.startswith("player_info.level") for d in diff)
    # everything else follows the rule (the Zombie tail moves by the GOTY
    # mZombatarHeadReanimID insertion, source-map §4.3)
    for d in diff:
        assert d.startswith(_KNOWN_GOTY_EXCEPTIONS)
    assert not any(d.startswith("board.") or d.startswith("lawn_app.") for d in diff)


def test_goty_delta_zh_builds_shift_lawn_app_by_0x124():
    p1051 = profile_for_stamp(V1051_STAMP)
    zh = profile_for_id("goty_1_1_0_1056_zh")
    diff = check_goty_delta(p1051, zh)
    assert any(d.startswith("lawn_app.game_ui") and "got=0x920" in d for d in diff)
    # 1073 Origin follows the rule everywhere except stride / PlayerInfo
    origin = profile_for_id("goty_1_2_0_1073_en")
    assert all(d.startswith(_KNOWN_GOTY_EXCEPTIONS) for d in check_goty_delta(p1051, origin))


# --- source-map §4 / §7.1 fields (schema_version 2) ------------------------------

def _gen(generated, name):
    return json.loads((generated / (name + ".json")).read_text(encoding="utf-8"))


def test_schema_version_2(generated):
    idx = json.loads((generated / "index.json").read_text(encoding="utf-8"))
    assert idx["schema_version"] == 2
    for f in generated.glob("*.json"):
        assert json.loads(f.read_text(encoding="utf-8"))["schema_version"] == 2


@pytest.mark.parametrize("key, board, zombie, lawn", [
    # (profile, board fields, zombie fields, lawn_app fields) -- §4.1 / §4.3 / §4.4
    ("goty_1_2_0_1096_en",
     {"board_rand_seed": 0x5634, "time_stop_counter": 0x5760, "cob_cursor_delay": 0x576C,
      "cob_mouse_x": 0x5770, "cob_mouse_y": 0x5774, "ice_min_x": 0x624,
      "level_complete": 0x5614, "board_fade_out_counter": 0x5618,
      "next_survival_stage_counter": 0x561C, "total_spawned_waves": 0x5598,
      "zombie_health_wave_start": 0x55B0, "last_bungee_wave": 0x55A8,
      "enable_grave_stones": 0x5DC, "fog_offset": 0x5E8, "grid_cel_fog": 0x4E0,
      "mower_bonus_remaining": 0x620, "wave_row_got_mowered": 0x608,
      "tutorial_state": 0x559C, "killed_yeti": 0x5778, "mode_flags": 0x5779,
      "level_coins_collected": 0x5788, "gargantuar_cob_kills": 0x578C,
      "pool_sparkly_particle_id": 0x5638, "coins": 0xFC,
      "projectile_count_max": 0xE4, "coin_count_max": 0x100,
      "zombies_max_size": 0xB0, "zombies_size": 0xB8, "zombies_next_key": 0xBC,
      "plants_size": 0xD4, "coins_size": 0x10C, "mowers_size": 0x128,
      "grid_items_next_key": 0x148},
     {"chilled_counter": 0xAC, "buttered_counter": 0xB0, "ice_trap_counter": 0xB4,
      "from_wave": 0x6C, "phase_counter": 0x68, "altitude": 0x84, "target_col": 0x80,
      "target_row": 0x130, "mind_controlled": 0xB8, "has_head": 0xBA, "in_pool": 0xBD,
      "helm_type": 0xC4, "helmet_max_hp": 0xD4, "shield_type": 0xD8, "shield_max_hp": 0xE0,
      "fireball_row": 0x14C, "is_fireball": 0x150, "zombatar_head_reanim": 0x148},
     {"zen_garden": 0x93C, "app_counter": 0x958, "app_rand_seed": 0x990, "board_result": 0x9A8}),
    ("1_0_0_1051_en",
     {"board_rand_seed": 0x561C, "time_stop_counter": 0x5748, "cob_cursor_delay": 0x5754,
      "cob_mouse_x": 0x5758, "cob_mouse_y": 0x575C, "ice_min_x": 0x60C,
      "level_complete": 0x55FC, "total_spawned_waves": 0x5580,
      "zombie_health_wave_start": 0x5598, "enable_grave_stones": 0x5C4,
      "fog_offset": 0x5D0, "grid_cel_fog": 0x4C8, "mower_bonus_remaining": 0x608,
      "tutorial_state": 0x5584, "killed_yeti": 0x5760, "mode_flags": 0x5761,
      "pool_sparkly_particle_id": 0x5620, "coins": 0xE4,
      "projectile_count_max": 0xCC, "coin_count_max": 0xE8,
      "zombies_max_size": 0x98, "zombies_size": 0xA0, "plants_next_key": 0xC0},
     {"chilled_counter": 0xAC, "buttered_counter": 0xB0, "ice_trap_counter": 0xB4,
      "from_wave": 0x6C, "fireball_row": 0x148, "is_fireball": 0x14C},
     {"zen_garden": 0x81C, "app_counter": 0x838, "app_rand_seed": 0x870, "board_result": 0x888}),
])
def test_source_map_offsets(generated, key, board, zombie, lawn):
    d = _gen(generated, key)
    for name, off in board.items():
        assert int(d["board"][name], 16) == off, "board." + name
        if name not in ("coins", "pool_sparkly_particle_id"):
            assert d["field_sources"]["board." + name] == "source-map §4", name
    for name, off in zombie.items():
        assert int(d["objects"]["zombie"]["fields"][name], 16) == off, "zombie." + name
        assert d["field_sources"]["objects.zombie." + name] == "source-map §4"
    for name, off in lawn.items():
        assert int(d["lawn_app"][name], 16) == off, "lawn_app." + name
        assert d["field_sources"]["lawn_app." + name] == "source-map §4"
    # renamed keys are gone from the JSON
    assert "items" not in d["board"] and "particle_system_ref" not in d["board"]
    assert "item" not in d["objects"] and d["objects"]["coin"]["stride"] == "0xd8"


def test_source_map_goty_only_fields_absent_in_retail(generated):
    for key in ("1_0_0_1051_en", "1_2_0_1065_en", "1_0_7_3467_ru"):
        d = _gen(generated, key)
        assert "level_coins_collected" not in d["board"]
        assert "gargantuar_cob_kills" not in d["board"]
        assert "zombatar_head_reanim" not in d["objects"]["zombie"]["fields"]
        assert d["objects"]["zombie"]["stride"] == "0x15c"
        assert d["board"]["board_rand_seed"] == "0x561c"
    for key in ("goty_1_2_0_1073_en", "goty_1_1_0_1056_zh"):
        d = _gen(generated, key)
        assert d["board"]["board_rand_seed"] == "0x5634"
        assert d["objects"]["zombie"]["stride"] == "0x168"
        assert d["objects"]["zombie"]["fields"]["fireball_row"] == "0x14c"
    # zh builds: LawnApp extras follow the profile's own +0x124 shift
    zh = _gen(generated, "goty_1_1_0_1056_zh")
    assert zh["lawn_app"]["zen_garden"] == "0x940" and zh["lawn_app"]["music"] == "0x960"


def test_source_map_object_fields(generated):
    for key in ("goty_1_2_0_1096_en", "1_0_0_1051_en"):
        d = _gen(generated, key)
        o = d["objects"]
        plant = o["plant"]["fields"]
        assert (plant["state_countdown"], plant["shooting_counter"], plant["target_x"],
                plant["target_y"], plant["disappear_countdown"], plant["do_special_countdown"],
                plant["target_zombie_id"], plant["is_on_board"], plant["highlighted"]) == (
            "0x54", "0x90", "0x80", "0x84", "0x4c", "0x50", "0x12c", "0x144", "0x145")
        proj = o["projectile"]["fields"]
        assert proj["cob_target_x"] == "0x80" and proj["cob_target_row"] == "0x84"
        assert (proj["type"], proj["dead"], proj["pos_x"], proj["vel_z"], proj["age"],
                proj["motion_type"]) == ("0x5c", "0x50", "0x30", "0x44", "0x60", "0x58")
        assert o["projectile"]["stride"] == "0x94"
        coin = o["coin"]["fields"]
        assert (coin["type"], coin["pos_x"], coin["pos_y"], coin["dead"], coin["is_being_collected"],
                coin["coin_age"], coin["motion"], coin["usable_seed_type"]) == (
            "0x58", "0x24", "0x28", "0x38", "0x50", "0x4c", "0x5c", "0x68")
        mower = o["lawn_mower"]["fields"]
        assert (mower["row"], mower["pos_x"], mower["mower_state"], mower["mower_type"],
                mower["visible"], mower["dead"]) == ("0x14", "0x8", "0x2c", "0x34", "0x31", "0x30")
        gi = o["grid_item"]["fields"]
        assert (gi["state"], gi["counter"], gi["zombie_type"], gi["seed_type"],
                gi["scary_pot_type"], gi["transparent_counter"], gi["sun_count"]) == (
            "0xc", "0x18", "0x3c", "0x40", "0x44", "0x4c", "0x50")
        sp = o["seed_packet"]["fields"]
        assert (sp["index"], sp["offset_x"], sp["refreshing"], sp["times_used"],
                sp["slot_machine_countdown"]) == ("0x2c", "0x30", "0x49", "0x4c", "0x3c")
        sb = o["seed_bank"]["fields"]
        assert sb["conveyor_belt_counter"] == "0x34c" and sb["cutscene_darken"] == "0x348"
        cur = o["cursor"]["fields"]
        assert (cur["seed_bank_index"], cur["type"], cur["imitater_type"], cur["cob_cannon_plant_id"],
                cur["glove_plant_id"], cur["grab"]) == ("0x24", "0x28", "0x2c", "0x40", "0x38", "0x30")
        ch = o["challenge"]["fields"]
        assert (ch["challenge_state"], ch["challenge_state_counter"], ch["conveyor_belt_counter"],
                ch["challenge_score"], ch["scary_potter_pots"], ch["rain_counter"],
                ch["endless_round"]) == ("0x54", "0x58", "0x5c", "0x60", "0xb0", "0xb4", "0x6c")


def test_source_map_skipped_for_beta(generated):
    for key in ("beta_0_1_1_1014_en", "beta_0_9_9_1029_en"):
        d = _gen(generated, key)
        assert "board_rand_seed" not in d["board"] and "zombies_size" not in d["board"]
        assert "chilled_counter" not in d["objects"]["zombie"]["fields"]
        assert "zen_garden" not in d["lawn_app"]
        assert not any(v == "source-map §4" for v in d["field_sources"].values())
        # renames still apply
        assert "coins" in d["board"] and "pool_sparkly_particle_id" in d["board"]


def test_renamed_fields_resolve_through_aliases():
    p = profile_for_stamp(STEAM_STAMP)
    # ``Section.items`` is the mapping method, so the old name goes through
    # item / lookup access
    assert p.board.coins == 0xFC and p.board["items"] == 0xFC and p.board.item_array == 0xFC
    assert p.board.pool_sparkly_particle_id == 0x5638 and p.board.particle_system_ref == 0x5638
    assert p.lookup("board.items") == 0xFC and "items" in p.board
    z = p.objects["zombie"]
    assert z.slowed_cd == z.chilled_counter == 0xAC
    assert z.frozen_cd == 0xB4 and z.hypnotized == 0xB8 and z.in_water == 0xBD
    q = profile_for_stamp(V1051_STAMP)
    assert q.board["items"] == 0xE4 and q.board.board_rand_seed == 0x561C
    assert q.objects["zombie"].fireball_row == 0x148
    with pytest.raises(AttributeError):
        q.objects["zombie"].zombatar_head_reanim
