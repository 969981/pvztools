"""Tests for pvztools.schema.track.lint (docs/track-format.md §6)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from pvztools.schema.timing import COB_CD, FINAL_WAVE_COUNTDOWN, HUGE_WAVE_MAX_LEN, WAVE_NATURAL_MAX
from pvztools.schema.track.ast import Diagnostic, Track
from pvztools.schema.track.lint import ERROR, INFO, WARNING, lint, wave_al
from pvztools.schema.track.pvzt import parse_pvzt
from pvztools.schema.track.shorthand import parse_shorthand


def codes(diags, code=None):
    return [d.code for d in diags if code is None or d.code == code]


def only(diags, code):
    found = [d for d in diags if d.code == code]
    assert len(found) == 1, found
    return found[0]


def pvzt(body: str, header: str = "scene: PE\n") -> Track:
    return parse_pvzt(header + "\n" + body)


# --- generic ------------------------------------------------------------------


def test_clean_shorthand_has_no_diagnostics():
    assert lint(parse_shorthand("P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)")) == []


def test_returns_diagnostic_objects():
    d = lint(parse_shorthand("P6: PP|PP (6|6)"))
    assert all(isinstance(x, Diagnostic) for x in d)


# --- AL_BELOW_2 ------------------------------------------------------------------


def test_al_below_2_warns_on_thin_wave():
    track = parse_shorthand("PP|P|PP")  # middle wave is mirrored (al 1 -> 2) so it's fine
    assert codes(lint(track), "AL_BELOW_2") == []
    # a fully written wave with a single cob is thin
    waves = list(track.waves)
    waves[1] = replace(waves[1], mirrored=False)
    d = only(lint(replace(track, waves=tuple(waves))), "AL_BELOW_2")
    assert d.level == WARNING and d.wave == 2


def test_al_ice_wave_exempt():
    track = parse_shorthand("PP|I|PP")
    waves = list(track.waves)
    waves[1] = replace(waves[1], mirrored=False)
    assert codes(lint(replace(track, waves=tuple(waves))), "AL_BELOW_2") == []


def test_wave_al_mirrors_cob_and_ash_only():
    track = parse_shorthand("P|IP|IP|N")
    assert [wave_al(w) for w in track.waves] == [2, 2, 2, 2]
    assert wave_al(parse_shorthand("ICP-BPD/Pd").waves[0]) == 6


def test_al_below_2_pvzt():
    d = lint(pvzt("w1 len 601\n  P 225 @ 2 9\n"))
    assert only(d, "AL_BELOW_2").wave == 1


# --- SIMPLIFIED_AMBIGUOUS -------------------------------------------------------


def test_simplified_ambiguous_for_unmirrorable_wave():
    track = parse_shorthand("PP|C|PP")
    d = only(lint(track), "SIMPLIFIED_AMBIGUOUS")
    assert d.level == ERROR and d.wave == 2
    assert codes(lint(track), "AL_BELOW_2") == []


def test_simplified_ambiguous_from_meta():
    track = Track(waves=(), meta={"warnings": [{"code": "SIMPLIFIED_AMBIGUOUS", "message": "x"}]})
    assert only(lint(track), "SIMPLIFIED_AMBIGUOUS").message == "x"


# --- LEN_RANGE --------------------------------------------------------------------


@pytest.mark.parametrize("text", ["w1 len 601", "w2 len 3099", "w9 len 5250", "w19 len 4500", "w20 len 5500", "w10 len 601"])
def test_len_ok(text):
    assert codes(lint(pvzt(text + "\n  PP 225 @ 25 9\n")), "LEN_RANGE") == []


@pytest.mark.parametrize("text,wave", [("w1 len 599", 1), ("w2 len 3100", 2), ("w9 len 5251", 9), ("w20 len 5000", 20), ("w10 len 4500", 10)])
def test_len_bad(text, wave):
    d = only(lint(pvzt(text + "\n  PP 225 @ 25 9\n")), "LEN_RANGE")
    assert d.level == ERROR and d.wave == wave


def test_len_range_checks_both_ends():
    assert codes(lint(pvzt("w1 len 601~3099\n  PP 225 @ 25 9\n")), "LEN_RANGE") == []
    assert only(lint(pvzt("w1 len 601~3100\n  PP 225 @ 25 9\n")), "LEN_RANGE").level == ERROR


def test_len_selector_reports_each_wave():
    d = codes(lint(pvzt("w1~3 len 500\n  PP 225 @ 25 9\n")), "LEN_RANGE")
    assert len(d) == 3


def test_len_shorthand_closing_length_is_warning():
    d = lint(parse_shorthand("PP|I-PP|N (6, 45, 9)"))
    assert only(d, "LEN_RANGE").level == WARNING
    assert only(lint(parse_shorthand("PP|PP (6, 60)")), "LEN_RANGE").level == ERROR
    assert only(lint(parse_shorthand("PP|PP (6, 5)")), "LEN_RANGE").level == ERROR


def test_len_whole_second_rounding_tolerated():
    assert codes(lint(parse_shorthand("P4: PC|PC|PC|PC (6|6|6|6)")), "LEN_RANGE") == []
    assert codes(lint(pvzt("w1 len 6\n  PP 225 @ 25 9\n")), "LEN_RANGE") == []
    assert only(lint(pvzt("w1 len 599\n  PP 225 @ 25 9\n")), "LEN_RANGE").level == ERROR
    assert only(lint(pvzt("w1 len 5\n  PP 225 @ 25 9\n")), "LEN_RANGE").level == ERROR


def test_len_constants_drive_bounds():
    assert FINAL_WAVE_COUNTDOWN == 5500 and HUGE_WAVE_MAX_LEN == 5250 and WAVE_NATURAL_MAX == 3099


# --- RHYTHM_MISMATCH --------------------------------------------------------------


def test_rhythm_p_wave_count():
    assert codes(lint(parse_shorthand("P5u: P|P|P|P|P 6|6|6|9|9")), "RHYTHM_MISMATCH") == []
    d = only(lint(parse_shorthand("P6: PP|PP|PP|PP (6|6|6|6)")), "RHYTHM_MISMATCH")
    assert d.level == WARNING and "6 waves" in d.message


def test_rhythm_ch_needs_ice():
    assert codes(lint(parse_shorthand("ch4: IP-P|IP-P 1738|1738")), "RHYTHM_MISMATCH") == []
    assert only(lint(parse_shorthand("ch4: PP|PP 1738|1738")), "RHYTHM_MISMATCH").level == WARNING


def test_rhythm_suffixes():
    assert codes(lint(parse_shorthand("P6-2I: PP|I-PP|PP|I-PP|PP|PP")), "RHYTHM_MISMATCH") == []
    assert "ice waves" in only(lint(parse_shorthand("P6-2I: PP|I-PP|PP|PP|PP|PP")), "RHYTHM_MISMATCH").message
    assert "-N" in only(lint(parse_shorthand("P4-N: PP|PP|PP|PP")), "RHYTHM_MISMATCH").message
    assert codes(lint(parse_shorthand("P4-N: PP|PP|PP|N")), "RHYTHM_MISMATCH") == []


def test_rhythm_unknown_family_only_warns():
    d = only(lint(parse_shorthand("D6: PP|PP|PP|PP|PP|PP")), "RHYTHM_MISMATCH")
    assert d.level == WARNING


def test_rhythm_c_family_not_checked():
    assert codes(lint(parse_shorthand("邻C6u: I-P|I-P|N|P (1975|1975|750|750)")), "RHYTHM_MISMATCH") == []


# --- shorthand gets no timeline / scene checks -------------------------------------


def test_shorthand_skips_pvzt_only_checks():
    track = parse_shorthand("PP|I-PP|N (6, 21, 9)")
    d = lint(replace(track, scene="RE"), scene="RE")
    assert set(codes(d)) <= {"AL_BELOW_2", "LEN_RANGE", "RHYTHM_MISMATCH", "SIMPLIFIED_AMBIGUOUS"}


# --- TARGET_GRID -----------------------------------------------------------------------


@pytest.mark.parametrize("col", ["9", "8.75", "8.1875", "1", "0.0125"])
def test_target_grid_ok(col):
    bad_expected = col == "0.0125"
    d = codes(lint(pvzt(f"w1 len 601\n  PP 225 @ 25 {col}\n")), "TARGET_GRID")
    assert bool(d) == bad_expected


@pytest.mark.parametrize("col", ["9.5", "10", "0", "8.01"])
def test_target_grid_bad(col):
    d = only(lint(pvzt(f"w1 len 601\n  PP 225 @ 25 {col}\n")), "TARGET_GRID")
    assert d.level == ERROR and d.wave == 1 and d.op == "PP"


def test_target_grid_row_outside_scene():
    d = only(lint(pvzt("w1 len 601\n  PP 225 @ 26 9\n", "scene: DE\n")), "TARGET_GRID")
    assert "row 6" in d.message
    assert codes(lint(pvzt("w1 len 601\n  PP 225 @ 26 9\n")), "TARGET_GRID") == []


def test_target_grid_row_unchecked_without_scene():
    assert codes(lint(pvzt("w1 len 601\n  PP 225 @ 26 9\n", "")), "TARGET_GRID") == []


# --- scene checks ----------------------------------------------------------------------


def test_ice_day_needs_coffee_once():
    body = "w1 len 601\n  I 11 @ 3 5\n  PP 225 @ 25 9\nw2 len 601\n  N 100 @ 3 5\n  PP 225 @ 25 9\n"
    d = [x for x in lint(pvzt(body, "scene: PE\ncards: ILN\n")) if x.code == "ICE_DAY_NEEDS_COFFEE"]
    assert len(d) == 1 and d[0].level == INFO and d[0].wave == 1 and d[0].op == "I"
    assert codes(lint(pvzt(body, "scene: NE\n")), "ICE_DAY_NEEDS_COFFEE") == []


def test_scene_argument_overrides_track():
    body = "w1 len 601\n  I 11 @ 2 5\n  PP 225 @ 25 9\n"
    assert codes(lint(pvzt(body, "scene: NE\n"), scene="DE"), "ICE_DAY_NEEDS_COFFEE") == ["ICE_DAY_NEEDS_COFFEE"]
    assert codes(lint(pvzt(body, "")), "ICE_DAY_NEEDS_COFFEE") == []


def test_roof_no_doom():
    body = "w1 len 601\n  N 100 @ 2 5\n"
    d = only(lint(pvzt(body, "scene: RE\n")), "ROOF_NO_DOOM")
    assert d.level == ERROR and d.op == "N"
    assert codes(lint(pvzt(body, "scene: ME\n")), "ROOF_NO_DOOM") == ["ROOF_NO_DOOM"]
    assert codes(lint(pvzt(body, "scene: DE\n")), "ROOF_NO_DOOM") == []


def test_pool_needs_lily():
    body = "w1 len 601\n  I 11 @ 3 5\n  PP 225 @ 25 9\n  C 539~619 @ 1256 9\n"
    d = lint(pvzt(body, "scene: PE\n"))
    lily = [x for x in d if x.code == "POOL_NEEDS_LILY"]
    assert [x.op for x in lily] == ["I"] and lily[0].level == WARNING  # cobs on 3/4 and C on 1256 are fine
    assert codes(lint(pvzt(body, "scene: PE\ncards: IL\n")), "POOL_NEEDS_LILY") == []
    assert codes(lint(pvzt(body, "scene: DE\n")), "POOL_NEEDS_LILY") == []
    # cob landing on a water row is exempt
    assert codes(lint(pvzt("w1 len 601\n  PP 225 @ 34 9\n", "scene: FE\n")), "POOL_NEEDS_LILY") == []


# --- COB_CD -------------------------------------------------------------------------------


P6 = "w1~6 len 601\n  PP 225 @ 25 9\n"


def test_cob_cd_skipped_without_cob_count():
    d = only(lint(pvzt(P6)), "COB_CD")
    assert d.level == INFO and "cob_count" in d.message


def test_cob_cd_no_cobs_no_info():
    assert codes(lint(pvzt("w1 len 601\n  N 100 @ 2 5\n")), "COB_CD") == []


def with_cobs(track: Track, n: int) -> Track:
    return replace(track, meta=dict(track.meta, cob_count=n))


def test_cob_cd_ok_with_enough_cannons():
    # 6 waves x 601 = 3606 > 3475: wave 1's two cannons are back for wave 7, but 12 cobs fall within 3475
    assert codes(lint(with_cobs(pvzt(P6), 12)), "COB_CD") == []


def test_cob_cd_violation_across_waves():
    d = [x for x in lint(with_cobs(pvzt(P6), 8)) if x.code == "COB_CD"]
    assert d and d[0].level == ERROR and d[0].wave == 5 and d[0].op == "PP"
    assert f"{COB_CD}" in d[0].message


def test_cob_cd_window_boundary_exact():
    # second shot exactly COB_CD later on one cannon is allowed
    body = f"w1 len {COB_CD}\n  P 225 @ 2 9\nw2 len 601\n  P 225 @ 2 9\n"
    assert codes(lint(with_cobs(pvzt(body), 1)), "COB_CD") == []
    body = f"w1 len {COB_CD - 1}\n  P 225 @ 2 9\nw2 len 601\n  P 225 @ 2 9\n"
    assert only(lint(with_cobs(pvzt(body), 1)), "COB_CD").level == ERROR


def test_cob_cd_same_wave_and_relative_times():
    body = "w1 len 601\n  PP 225 @ 25 9\n  DD +107 @ 15 8.1875\n"
    assert only(lint(with_cobs(pvzt(body), 3)), "COB_CD").op == "DD"
    assert codes(lint(with_cobs(pvzt(body), 4)), "COB_CD") == []


def test_cob_cd_chain_breaks_on_missing_length():
    body = "w1\n  PP 225 @ 25 9\nw2\n  PP 225 @ 25 9\n"
    d = lint(with_cobs(pvzt(body), 2))
    assert [x.level for x in d if x.code == "COB_CD"] == [INFO]  # not chained, so no error
    body = "w1 len 601\n  PP 225 @ 25 9\nw3 len 601\n  PP 225 @ 25 9\n"  # w2 undefined
    d = lint(with_cobs(pvzt(body), 2))
    assert [x.level for x in d if x.code == "COB_CD"] == [INFO]
    # a fully chained track with the same cobs is an error
    body = "w1 len 601\n  PP 225 @ 25 9\nw2 len 601\n  PP 225 @ 25 9\n"
    assert [x.level for x in lint(with_cobs(pvzt(body), 2)) if x.code == "COB_CD"] == [ERROR]


def test_cob_cd_uses_range_low_end():
    body = "w1 len 3000~3475\n  P 225 @ 2 9\nw2 len 601\n  P 225 @ 2 9\n"
    assert only(lint(with_cobs(pvzt(body), 1)), "COB_CD").level == ERROR


# --- CARD_CD ----------------------------------------------------------------------------


def test_card_cd_ice_violation_and_ok():
    bad = "w1 len 601\n  I 11 @ 2 5\n  PP 225 @ 25 9\nw2 len 601\n  I 11 @ 2 5\n  PP 225 @ 25 9\n"
    d = only(lint(pvzt(bad, "scene: NE\n")), "CARD_CD")
    assert d.level == ERROR and d.wave == 2 and d.op == "I" and "5000" in d.message
    ok = "w1 len 5000\n  I 11 @ 2 5\n  PP 225 @ 25 9\nw2 len 5000\n  I 11 @ 2 5\n  PP 225 @ 25 9\nw3 len 601\n  I 11 @ 2 5\n  PP 225 @ 25 9\n"
    assert codes(lint(pvzt(ok, "scene: NE\n")), "CARD_CD") == []


def test_card_cd_generic_symbol_may_use_imitater_copy():
    bad = "w1 len 601\n  I 11 @ 2 5\n  PP 225 @ 25 9\nw2 len 601\n  I 11 @ 2 5\n  PP 225 @ 25 9\n"
    # one ice card: two plain I within 5000cs is an error
    assert codes(lint(pvzt(bad, "scene: NE\ncards: INAW LPccc\n")), "CARD_CD") == ["CARD_CD"]
    # "II" = ice + imitater ice: a plain I does not name the copy, so both count
    assert codes(lint(pvzt(bad, "scene: NE\ncards: IINAW LPccc\n")), "CARD_CD") == []
    three = bad + "w3 len 601\n  I 11 @ 2 5\n  PP 225 @ 25 9\n"
    assert only(lint(pvzt(three, "scene: NE\ncards: IINAW LPccc\n")), "CARD_CD").wave == 3
    # imitater ice is a different card from the real one
    imit = "w1 len 601\n  I 11 @ 2 5\n  PP 225 @ 25 9\nw2 len 601\n  I' 11 @ 2 5\n  PP 225 @ 25 9\n"
    assert codes(lint(pvzt(imit, "scene: NE\n")), "CARD_CD") == []
    # an explicit I' is pinned to the single imitater card
    imit2 = imit + "w3 len 601\n  I' 11 @ 2 5\n  PP 225 @ 25 9\n"
    assert only(lint(pvzt(imit2, "scene: NE\ncards: IINAW\n")), "CARD_CD").op == "I'"


@pytest.mark.parametrize(
    "sym,gap,bad",
    [("A", 4999, True), ("A", 5000, False), ("a", 2999, True), ("a", 3000, False), ("c", 749, True), ("c", 750, False), ("C", 749, True), ("C", 750, False)],
)
def test_card_cd_per_kind(sym, gap, bad):
    # squash is written "a" here: "W" at line start is read as a wave header by the pvzt parser
    body = f"w1 len {gap}\n  {sym} 100 @ 2 9\n  PP 225 @ 25 9\nw2 len 601\n  {sym} 100 @ 2 9\n  PP 225 @ 25 9\n"
    assert bool(codes(lint(pvzt(body, "scene: NE\n")), "CARD_CD")) == bad


def test_card_cd_fodder_multi_row_counts_units_and_pool():
    body = "w1 len 601\n  C 539~619 @ 125 9\n  PP 225 @ 25 9\n"
    assert only(lint(pvzt(body, "scene: NE\n")), "CARD_CD").op == "C"
    assert codes(lint(pvzt(body, "scene: NE\ncards: PT_\n")), "CARD_CD") == []
    assert codes(lint(pvzt(body, "scene: NE\ncards: PT\n")), "CARD_CD") == ["CARD_CD"]
    soft = "w1 len 601\n  c 539~619 @ 125 9\n  PP 225 @ 25 9\n"
    assert codes(lint(pvzt(soft, "scene: NE\ncards: ccc\n")), "CARD_CD") == []
    assert codes(lint(pvzt(soft, "scene: NE\ncards: cc\n")), "CARD_CD") == ["CARD_CD"]


def test_card_cd_malformed_cards_header_ignored():
    body = "w1 len 601\n  I 11 @ 2 5\n  PP 225 @ 25 9\n"
    assert codes(lint(pvzt(body, "scene: NE\ncards: ZZZ\n")), "CARD_CD") == []


# --- spec example end to end ------------------------------------------------------------------


def test_spec_example_lints_sanely():
    from test_pvzt import SPEC_EXAMPLE

    d = lint(parse_pvzt(SPEC_EXAMPLE))
    levels = {x.code: x.level for x in d}
    assert "COB_CD" in levels and levels["COB_CD"] == INFO  # lineup header is FE, track is PE
    assert "ICE_DAY_NEEDS_COFFEE" in levels
    # the example fires ice on w1/w2/w4 (3 within 5000cs of the 2 ice cards) and plants 4 fodder per
    # 601cs wave from 4 cards (8 within 750cs): only genuine CARD_CD errors remain, nothing spurious
    assert {(x.code, x.op) for x in d if x.level == ERROR} == {("CARD_CD", "I"), ("CARD_CD", "C")}
    assert "TARGET_GRID" not in levels and "LEN_RANGE" not in levels and "ROOF_NO_DOOM" not in levels
    assert all(isinstance(x, Diagnostic) for x in d)


def test_cob_cd_from_lineup_header():
    code = "LI43NMQIfe3BVVEc+sHIXUAPX7dvSCBUt1TS1OE8dn9XgraGkdQ1ZlSKiEUaVw=="  # FE, 4 cobs
    body = "w1 len 601\n  PP 225 @ 25 9\nw2 len 601\n  PP 225 @ 25 9\nw3 len 601\n  PP 225 @ 25 9\n"
    d = [x for x in lint(pvzt(body, f"scene: FE\nlineup: {code}\n")) if x.code == "COB_CD"]
    assert d and d[0].level == ERROR and "lineup header" in d[0].message and d[0].wave == 3
    # no scene on the track: lineup is trusted
    assert [x.level for x in lint(pvzt(body, f"lineup: {code}\n")) if x.code == "COB_CD"] == [ERROR]
    # scene mismatch / undecodable code: skipped with an info
    d = only(lint(pvzt(body, f"scene: PE\nlineup: {code}\n")), "COB_CD")
    assert d.level == INFO and "FE" in d.message
    d = only(lint(pvzt(body, "scene: FE\nlineup: nope\n")), "COB_CD")
    assert d.level == INFO and "does not decode" in d.message
    # meta['cob_count'] wins over the header
    assert codes(lint(with_cobs(pvzt(body, f"scene: FE\nlineup: {code}\n"), 6)), "COB_CD") == []


def test_unknown_scene_is_a_diagnostic_not_an_exception():
    d = lint(parse_pvzt("w1 len 601\n  PP 225\n"), scene="XX")
    assert only(d, "SCENE_UNKNOWN").level == ERROR
    assert only(lint(parse_pvzt("scene: PE\nw1 len 601\n  N 100 @ 3 5\n"), scene="ZZ"), "SCENE_UNKNOWN")


def test_wave_out_of_range():
    assert only(lint(parse_pvzt("w21\n")), "WAVE_OUT_OF_RANGE").wave == 21
    assert only(lint(parse_pvzt("w1~25\n")), "WAVE_OUT_OF_RANGE").level == WARNING
    assert codes(lint(parse_pvzt("w1~20\n")), "WAVE_OUT_OF_RANGE") == []
    assert codes(lint(parse_shorthand("w21:PP")), "WAVE_OUT_OF_RANGE") == []  # shorthand selectors are not checked


def test_card_cd_hard_fodder_counts_every_fodder_card():
    # spec §4 example line: 4 fodder units per 601cs wave from P + ccc
    body = "w1 len 601\n  C 539~619 @ 1256 9\n  PP 225 @ 25 9\n"
    assert codes(lint(pvzt(body, "scene: PE\ncards: IINAW LPccc\n")), "CARD_CD") == []
    assert codes(lint(pvzt(body, "scene: PE\ncards: IINAW LPcc\n")), "CARD_CD") != []
