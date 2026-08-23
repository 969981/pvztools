"""Tests for schema.track.expand (docs/track-format.md §7)."""
from __future__ import annotations

import pytest

from pvztools.schema.enums import PlantType, Scene
from pvztools.schema.track.expand import (
    DEFAULT_COB_EFFECT_CS,
    DEFAULT_ICE_EFFECT_CS,
    KIND_COFFEE,
    Action,
    ExpandContext,
    Plan,
    expand,
    mirror_row,
)
from pvztools.schema.track.pvzt import parse_pvzt
from pvztools.schema.track.shorthand import parse_shorthand

COBS_16 = tuple((r, c) for r in (1, 2, 5, 6) for c in (1, 3, 5, 7))
COBS_12 = COBS_16[:12]
COBS_8 = COBS_16[:8]

PVZT_EXAMPLE = """
scene: PE
w1~8 len 601
  PP 225 @ 25 9
  C 539~619 @ 1256 9
w9,19 len 4500
  PP 225 @ 25 9 ; PP 2000 @ 25 9
w20 len 5500
  PP 341 @ 25 9
  DD +107 @ 15 8.1875
  PP 2000 @ 25 9
w1%4, 2~9%2, 11~19 len 1976
  I 11 @ 3 5
  PP 1776 @ 25 8.75
w10
  PP 341
  A 401 @ 2 9 & W 401 @ 5 9
"""


def codes(plan: Plan, code: str):
    return [d for d in plan.issues if d.code == code]


def test_p6_sixteen_cobs_no_shortage():
    # PP|PP|PPSS|PP|PP|PPSS = 16 cobs per 36s cycle; 16 cannons (CD 34.75s) suffice.
    track = parse_shorthand("P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)")
    plan = expand(track, ExpandContext("PE", cob_positions=COBS_16))
    assert isinstance(plan, Plan)
    assert sorted({a.wave for a in plan.actions}) == list(range(1, 21))
    cob_actions = [a for a in plan.actions if a.kind == "cob"]
    assert all(a.cannon is not None for a in cob_actions)
    assert not codes(plan, "COB_SHORTAGE")
    per_wave = {w: sum(1 for a in cob_actions if a.wave == w) for w in range(1, 21)}
    assert per_wave[1] == 2 and per_wave[3] == 4 and per_wave[6] == 4 and per_wave[9] == 4
    a = cob_actions[0]
    assert a.effect_cs == DEFAULT_COB_EFFECT_CS
    assert a.operation_cs == DEFAULT_COB_EFFECT_CS - 373
    assert a.plant == int(PlantType.COB_CANNON)
    assert a.target.rows in ((2,), (5,)) and a.target.col == 9.0
    assert codes(plan, "DEFAULT_TIMING")


def test_plain_p6_twelve_cobs_no_shortage():
    track = parse_shorthand("P6: PP|PP|PP|PP|PP|PP (6|6|6|6|6|6)")
    plan = expand(track, ExpandContext("PE", cob_positions=COBS_12))
    assert not codes(plan, "COB_SHORTAGE")
    assert all(a.cannon is not None for a in plan.actions if a.kind == "cob")


@pytest.mark.parametrize("cobs", [COBS_12, COBS_8])
def test_p6_too_few_cobs_shortage(cobs):
    # 16 cobs per cycle: 12 cannons already fall short (16 * 3475 / 3600 > 12).
    track = parse_shorthand("P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)")
    plan = expand(track, ExpandContext("PE", cob_positions=cobs))
    shortage = codes(plan, "COB_SHORTAGE")
    assert shortage and all(d.level == "warning" for d in shortage)
    unassigned = [a for a in plan.actions if a.kind == "cob" and a.cannon is None]
    assert len(unassigned) == len(shortage)
    # every action is still emitted: 20 waves x PP + 6 PPSS waves (3,6,..,18) x SS
    assert sum(1 for a in plan.actions if a.kind == "cob") == 20 * 2 + 6 * 2


def test_cob_queue_respects_cd():
    track = parse_shorthand("PP|PP (6|6)")
    plan = expand(track, ExpandContext("PE", cob_positions=((1, 1), (1, 3)), wave_count=2))
    cobs = [a for a in plan.actions if a.kind == "cob"]
    assert [a.cannon for a in cobs] == [(1, 1), (1, 3), None, None]
    assert [d.wave for d in codes(plan, "COB_SHORTAGE")] == [2, 2]
    # 35s later the first cannon is ready again
    plan = expand(parse_shorthand("P|P (35|35)"), ExpandContext("PE", cob_positions=((1, 1),), wave_count=2, default_cob_target_rows=(2, 2)))
    assert [a.cannon for a in plan.actions if a.kind == "cob"] == [(1, 1), None, (1, 1), None]


def test_pvzt_example_operation_offsets():
    plan = expand(parse_pvzt(PVZT_EXAMPLE), ExpandContext("PE", cob_positions=COBS_12, ice_slots=((3, 5),)))
    by_wave = {}
    for a in plan.actions:
        by_wave.setdefault(a.wave, []).append(a)
    # w1 is covered by the later "w1%4 ..." block (override); rows 2/5 are land -> 373
    w1 = by_wave[1]
    cobs = [a for a in w1 if a.kind == "cob"]
    assert len(cobs) == 2 and {a.target.rows[0] for a in cobs} == {2, 5}
    assert all(a.effect_cs == 1776 and a.operation_cs == 1776 - 373 for a in cobs)
    ice = [a for a in w1 if a.kind == "ice"]
    assert len(ice) == 1 and ice[0].effect_cs == 11 and ice[0].operation_cs == 11 - 299  # PE is day
    coffee = [a for a in w1 if a.kind == KIND_COFFEE]
    assert len(coffee) == 1 and coffee[0].effect_cs == 11 and coffee[0].plant == int(PlantType.COFFEE_BEAN)
    assert codes(plan, "WAVE_OVERRIDDEN")
    # w20: relative DD +107 -> 448; rows 1/5 land
    w20 = [a for a in by_wave[20] if a.symbol == "DD"]
    assert [a.effect_cs for a in w20] == [448, 448]
    assert {a.target.rows[0] for a in w20} == {1, 5}
    assert all(a.operation_cs == 448 - 373 for a in w20)
    # w10: default rows for PP 341 without '@'; A & W simultaneous
    w10 = by_wave[10]
    pp = [a for a in w10 if a.symbol == "PP"]
    assert {a.target.rows[0] for a in pp} == {2, 5}
    ash = [a for a in w10 if a.kind == "ash"]
    # cherry explodes 100cs after planting, squash lands after 182 (AvZ W = At(-182_cs))
    assert sorted((a.symbol, a.effect_cs, a.operation_cs) for a in ash) == [("A", 401, 301), ("W", 401, 401 - 182)]
    assert {a.plant for a in ash} == {int(PlantType.CHERRY_BOMB), int(PlantType.SQUASH)}
    # w3 keeps the w1~8 block: fodder window start 539, rows 1/2/5/6
    fod = [a for a in by_wave[3] if a.kind == "fodder"]
    assert len(fod) == 4 and all(a.effect_cs == 539 and a.operation_cs == 539 for a in fod)
    assert [a.target.rows[0] for a in fod] == [1, 2, 5, 6]
    assert [(a.wave, a.effect_cs) for a in plan.actions] == sorted((a.wave, a.effect_cs) for a in plan.actions)


def test_water_row_cob_offset_and_fe_ice():
    text = "scene: FE\nw1 len 1000\n  P 500 @ 3 9\n  P 500 @ 1 9\n  I 100 @ 4 5\n"
    plan = expand(parse_pvzt(text), ExpandContext("FE", cob_positions=COBS_12, wave_count=1))
    water = [a for a in plan.actions if a.kind == "cob" and a.target.rows == (3,)][0]
    land = [a for a in plan.actions if a.kind == "cob" and a.target.rows == (1,)][0]
    assert water.operation_cs == 500 - 378
    assert land.operation_cs == 500 - 373
    ice = [a for a in plan.actions if a.kind == "ice"][0]
    assert ice.operation_cs == 100 - 100
    assert not [a for a in plan.actions if a.kind == KIND_COFFEE]


def test_imitater_ice_offset():
    text = "scene: NE\nw1 len 1000\n  I' 500 @ 3 5\n"
    plan = expand(parse_pvzt(text), ExpandContext("NE", wave_count=1))
    ice = plan.actions[0]
    assert ice.operation_cs == 500 - 100 - 320
    assert ice.plant == int(PlantType.ICE_SHROOM) + 49
    assert codes(plan, "ICE_SHORTAGE")  # no ice slots configured


def test_mirrored_cycle_two_cobs_per_wave():
    track = parse_shorthand("P|I-P|I-P|N")
    plan = expand(track, ExpandContext("NE", cob_positions=COBS_12, ice_slots=((1, 1), (1, 2)), wave_count=8))
    for w in (1, 2, 3, 5, 6, 7):
        cobs = [a for a in plan.actions if a.wave == w and a.kind == "cob"]
        assert len(cobs) == 2, w
        assert {a.target.rows[0] for a in cobs} == {2, 4}  # 5-row scene mirrors 2<->4
        assert all(a.cannon is not None for a in cobs)
    ices = [a for a in plan.actions if a.kind == "ice"]
    assert [a.wave for a in ices] == [2, 3, 6, 7]
    assert all(a.effect_cs == DEFAULT_ICE_EFFECT_CS and a.operation_cs == DEFAULT_ICE_EFFECT_CS - 100 for a in ices)
    assert ices[0].target.rows == (1,) and ices[0].target.col == 1.0 and ices[1].target.col == 2.0
    dooms = [a for a in plan.actions if a.kind == "doom"]
    assert [a.wave for a in dooms] == [4, 8] and dooms[0].plant == int(PlantType.DOOM_SHROOM)
    assert not [a for a in plan.actions if a.kind == KIND_COFFEE]


def test_mirrored_on_pe_rows_and_huge_wave_info():
    plan = expand(parse_shorthand("P|P"), ExpandContext("PE", cob_positions=COBS_12))
    w10 = [a for a in plan.actions if a.wave == 10]
    assert {a.target.rows[0] for a in w10} == {2, 5}
    assert {d.wave for d in codes(plan, "HUGE_WAVE")} == {10, 20}
    assert {d.wave for d in codes(plan, "CLOSING_WAVE")} == {9, 19}


def test_mirror_row():
    assert mirror_row(1, Scene.POOL) == 6 and mirror_row(3, Scene.POOL) == 4
    assert mirror_row(1, Scene.DAY) == 5 and mirror_row(3, Scene.DAY) == 3


def test_fodder_rows_rotate_and_day_doom_coffee():
    track = parse_shorthand("PC|PC|N (6|6|6)")
    plan = expand(track, ExpandContext("DE", cob_positions=COBS_12, fodder_rows=(1, 5), wave_count=3))
    fod = [a for a in plan.actions if a.kind == "fodder"]
    assert [a.target.rows[0] for a in fod] == [1, 5]
    assert fod[0].target.col == 9.0 and fod[0].operation_cs == fod[0].effect_cs == 134
    doom = [a for a in plan.actions if a.kind == "doom"]
    coffee = [a for a in plan.actions if a.kind == KIND_COFFEE]
    assert len(doom) == 1 and len(coffee) == 1
    assert doom[0].operation_cs == doom[0].effect_cs - 299 and coffee[0].effect_cs == doom[0].effect_cs
    assert coffee[0].source_op is doom[0].source_op


def test_relative_without_prev_and_scene_mismatch():
    text = "scene: PE\nw1 len 1000\n  P +100 @ 1 9\n"
    plan = expand(parse_pvzt(text), ExpandContext("DE", cob_positions=COBS_12, wave_count=1))
    assert plan.actions[0].effect_cs == 100
    assert codes(plan, "RELATIVE_NO_PREV") and codes(plan, "SCENE_MISMATCH")


def test_wave_out_of_range_and_empty():
    text = "scene: PE\nw1 len 1000\n  P 300 @ 1 9\nw5\n  P 300 @ 1 9\n"
    plan = expand(parse_pvzt(text), ExpandContext("PE", cob_positions=COBS_12, wave_count=3))
    assert [d.wave for d in codes(plan, "WAVE_OUT_OF_RANGE")] == [5]
    assert {d.wave for d in codes(plan, "WAVE_EMPTY")} == {2, 3}
    assert [a.wave for a in plan.actions] == [1]


def test_split_wave_bottom_half_not_mirrored():
    plan = expand(parse_shorthand("P/I-P"), ExpandContext("PE", cob_positions=COBS_12, ice_slots=((1, 1),), wave_count=1))
    assert sorted(a.kind for a in plan.actions) == ["cob", "cob", "coffee", "ice"]


def test_action_is_frozen():
    plan = expand(parse_shorthand("P"), ExpandContext("PE", cob_positions=COBS_12, wave_count=1))
    assert isinstance(plan.actions[0], Action)
    with pytest.raises(Exception):
        plan.actions[0].wave = 2  # type: ignore[misc]



def test_imitater_coffee_waits_for_the_real_mushroom():
    # §7 rule 3: coffee at -299; the imitater ice itself is planted 320 earlier
    plan = expand(parse_pvzt("scene: PE\nw1 len 1976\n  I' 11 @ 3 5\n"), ExpandContext("PE", ice_slots=((3, 5),), wave_count=1))
    ice = [a for a in plan.actions if a.kind == "ice"][0]
    coffee = [a for a in plan.actions if a.kind == KIND_COFFEE][0]
    assert ice.operation_cs == 11 - 299 - 320
    assert coffee.operation_cs == 11 - 299 and coffee.effect_cs == 11
    plan = expand(parse_pvzt("scene: PE\nw1 len 1976\n  I 11 @ 3 5\n"), ExpandContext("PE", ice_slots=((3, 5),), wave_count=1))
    assert {a.operation_cs for a in plan.actions} == {11 - 299}


def test_squash_offset_182_and_jalapeno_100():
    plan = expand(parse_pvzt("scene: NE\nw1 len 601\n  a 341 @ 2 9\n  J 341 @ 3 9\n"), ExpandContext("NE", wave_count=1))
    assert {(a.symbol, a.operation_cs) for a in plan.actions} == {("a", 341 - 182), ("J", 341 - 100)}


def test_roof_cannon_class_by_column():
    from pvztools.schema.track.expand import roof_cannon_class

    assert roof_cannon_class(5) == "slope" and roof_cannon_class(6) == "flat"
    # slope cannons (cols 1-5) serve slope targets, flat cannons (cols 6+) serve flat targets
    text = "scene: RE\nw1 len 601\n  P 225 @ 2 9\n  P 225 @ 4 3\n"
    ctx = ExpandContext("RE", cob_positions=((2, 1), (2, 7)), wave_count=1)
    plan = expand(parse_pvzt(text), ctx)
    by_target = {a.target.col: a.cannon for a in plan.actions if a.kind == "cob"}
    assert by_target == {9.0: (2, 7), 3.0: (2, 1)}
    assert not codes(plan, "ROOF_CANNON_CLASS")
    # only flat cannons: a slope target falls back with a warning
    plan = expand(parse_pvzt("scene: RE\nw1 len 601\n  P 225 @ 4 3\n"), ExpandContext("RE", cob_positions=((2, 7),), wave_count=1))
    assert [a.cannon for a in plan.actions] == [(2, 7)] and codes(plan, "ROOF_CANNON_CLASS")
    # non-roof scenes ignore the classes
    plan = expand(parse_pvzt("scene: PE\nw1 len 601\n  P 225 @ 2 3\n"), ExpandContext("PE", cob_positions=((2, 7),), wave_count=1))
    assert [a.cannon for a in plan.actions] == [(2, 7)] and not codes(plan, "ROOF_CANNON_CLASS")


# --- §7 rule 7: activation timing derived from the rhythm length -------------
def _cobs(plan, wave):
    return [a for a in plan.actions if a.wave == wave and a.kind == "cob"]


def test_rhythm_cob_effect_function():
    from pvztools.schema.track.expand import HUGE_WAVE_DELAY, WAVE_MIN_CS, rhythm_cob_effect

    assert WAVE_MIN_CS == 601 and HUGE_WAVE_DELAY == 341
    assert rhythm_cob_effect(None) is None
    assert rhythm_cob_effect(600) is None  # shorter than the 601 minimum: caller uses 341
    assert rhythm_cob_effect(601) == 401
    assert rhythm_cob_effect(869) == 669
    assert rhythm_cob_effect(1150) == 950


def test_p4_activation_lands_at_length_minus_200():
    # P4 869: PP must land at 669 (= 869 - 200); land rows 2/5 -> fire at 296
    plan = expand(parse_shorthand("P4: PP|PP|PP|PP (869|869|869|869)"), ExpandContext("PE", cob_positions=COBS_12))
    for w in (1, 2, 3, 4, 5, 8, 9, 11):
        cobs = _cobs(plan, w)
        assert len(cobs) == 2 and all(a.effect_cs == 669 and a.operation_cs == 296 for a in cobs), w
    assert not codes(plan, "COB_SHORTAGE") and not codes(plan, "RHYTHM_LENGTH_UNKNOWN")
    # huge waves spawn later: +341
    for w in (10, 20):
        assert all(a.effect_cs == 669 + 341 and a.operation_cs == 296 + 341 for a in _cobs(plan, w)), w


def test_p6_601_lands_at_401_and_600_keeps_341():
    # L = 601 -> 401 (op 28); the AvZ P6 convention (-32 / land 341) is equivalent
    plan = expand(parse_shorthand("P6: PP|PP|PP|PP|PP|PP (601|601|601|601|601|601)"), ExpandContext("PE", cob_positions=COBS_12))
    assert all(a.effect_cs == 401 and a.operation_cs == 28 for a in _cobs(plan, 1))
    assert not codes(plan, "COB_SHORTAGE")
    # "6" parses as 600 (< 601 minimum): the 341 floor applies
    plan = expand(parse_shorthand("P6: PP|PP|PP|PP|PP|PP (6|6|6|6|6|6)"), ExpandContext("PE", cob_positions=COBS_12))
    assert all(a.effect_cs == 341 and a.operation_cs == -32 for a in _cobs(plan, 1))


def test_ch6_ice_wave_pp_lands_at_950():
    # Be2SiO4 ch6: the ice wave's activating cob lands at 950 (= 1150 - 200)
    plan = expand(parse_shorthand("ch6: P|I-P|P|I-P (601|1150|601|1150)"), ExpandContext("PE", cob_positions=COBS_12, ice_slots=((3, 5), (4, 5))))
    assert all(a.effect_cs == 401 for a in _cobs(plan, 1))
    w2 = _cobs(plan, 2)
    assert len(w2) == 2 and all(a.effect_cs == 950 and a.operation_cs == 950 - 373 for a in w2)
    ice = [a for a in plan.actions if a.wave == 2 and a.kind == "ice"]
    assert len(ice) == 1 and ice[0].effect_cs == DEFAULT_ICE_EFFECT_CS  # pre-phase ice unchanged
    assert all(a.effect_cs == 950 + 341 for a in _cobs(plan, 10))
    # closing waves keep the computed value
    assert all(a.effect_cs == 401 for a in _cobs(plan, 9))


def test_companion_groups_relative_to_activator():
    from pvztools.schema.track.expand import SYNC_BEFORE_CS, SYNC_DELAY_CS

    plan = expand(parse_shorthand("PPSS|PPBB|PPDD (869|869|869)"), ExpandContext("PE", cob_positions=COBS_16, wave_count=3))
    by = {w: {a.symbol: a.effect_cs for a in _cobs(plan, w)} for w in (1, 2, 3)}
    assert by[1] == {"PP": 669, "SS": 669}
    assert by[2] == {"PP": 669, "BB": 669 - SYNC_BEFORE_CS}
    assert by[3] == {"PP": 669, "DD": 669 + SYNC_DELAY_CS}


def test_unknown_length_falls_back_to_341_with_info():
    plan = expand(parse_shorthand("PP|PP"), ExpandContext("PE", cob_positions=COBS_12, wave_count=2))
    assert all(a.effect_cs == DEFAULT_COB_EFFECT_CS for a in plan.actions if a.kind == "cob")
    info = codes(plan, "RHYTHM_LENGTH_UNKNOWN")
    assert [d.wave for d in info] == [1, 2] and all(d.level == "info" for d in info)


def test_pvzt_explicit_times_not_rewritten():
    plan = expand(parse_pvzt("scene: PE\nw10 len 869\n  PP 225 @ 25 9\n"), ExpandContext("PE", cob_positions=COBS_12, wave_count=10))
    assert {a.effect_cs for a in _cobs(plan, 10)} == {225}
    assert not codes(plan, "RHYTHM_LENGTH_UNKNOWN")


# ---------------------------------------------------------------------------
# review fix: unknown-length waves use the same 601 assumption for the CD
# timeline as for the activation (previously 2500 -> COB_SHORTAGE never fired)
# ---------------------------------------------------------------------------
def test_unknown_length_cd_check_matches_601_activation():
    untimed = expand(parse_shorthand("P4: PP|PP|PP|PP"), ExpandContext("PE", cob_positions=COBS_8, wave_count=8))
    timed = expand(parse_shorthand("P4: PP|PP|PP|PP (601|601|601|601)"), ExpandContext("PE", cob_positions=COBS_8, wave_count=8))
    short = lambda plan: sorted((i.wave, i.code) for i in plan.issues if i.code == "COB_SHORTAGE")  # noqa: E731
    assert short(timed) == [(5, "COB_SHORTAGE")] * 2 + [(6, "COB_SHORTAGE")] * 2
    assert short(untimed) == short(timed)
    # both rhythms yield 601cs waves (landing 341 / 401 both trigger at 401)
    for w in range(1, 9):
        assert {a.effect_cs for a in untimed.actions if a.wave == w and a.kind == "cob"} == {341}
        assert {a.effect_cs for a in timed.actions if a.wave == w and a.kind == "cob"} == {401}
    missing = [i for i in untimed.issues if i.code == "LEN_MISSING"]
    assert len(missing) == 7 and all("activated minimum 601cs" in i.message for i in missing)


def test_unknown_length_without_activation_keeps_natural_minimum():
    from pvztools.schema.timing import WAVE_NATURAL_MIN

    plan = expand(parse_shorthand("P4: N|N|N|N"), ExpandContext("PE", cob_positions=COBS_8, wave_count=4))
    missing = [i for i in plan.issues if i.code == "LEN_MISSING"]
    assert len(missing) == 3 and all(f"natural minimum {WAVE_NATURAL_MIN}cs" in i.message for i in missing)


def test_wave_starts_add_huge_wave_banner_and_delay():
    from pvztools.schema.track.expand import HUGE_WAVE_DELAY, _layout_waves, _wave_starts
    from pvztools.schema.timing import HUGE_WAVE_RED_TEXT

    ctx = ExpandContext("PE", cob_positions=COBS_8, wave_count=20)
    issues: list = []
    layout = _layout_waves(parse_shorthand("P4: PP|PP|PP|PP (869|869|869|869)"), ctx, issues)
    starts = _wave_starts(layout, ctx, issues)
    assert starts[9] - starts[8] == 869
    assert starts[10] - starts[9] == 869 + HUGE_WAVE_RED_TEXT     # w9 closes with the banner
    assert starts[11] - starts[10] == 869 + HUGE_WAVE_DELAY       # w10 activation is delayed
    assert starts[20] - starts[19] == 869 + HUGE_WAVE_RED_TEXT
