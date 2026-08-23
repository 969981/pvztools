"""Tests for pvztools.schema.track.pvzt (docs/track-format.md §4 / §5.4, Seml examples)."""
from __future__ import annotations

import pytest

from pvztools.schema.track.ast import PHASE_POST, PHASE_PRE, Target
from pvztools.schema.track.pvzt import dump_pvzt, parse_header, parse_pvzt, strip_raw

SPEC_EXAMPLE = """\
# 注释以 # 开头
scene: PE
rhythm: P6  PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)
dialect: duben
cards: IINAW LPccc
zombies: 普杆舞车豚 丑气矿篮偷 红
protect: 18 28 58 68
lineup: LI43NMQIfe3BVVEc+sHIXUAPX7dvSCBUt1TS1OE8dn9XgraGkdQ1ZlSKiEUaVw==
timing: effect

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

SEML_SMASH = """\
# 测试 PE.垫材24 旧PDc解的砸率
scene:PE
protect:18 28 58 68  # 要保护的炮位
repeat:300

w 601
PP 225 25 9
C 539~619 1265 9

w 601
PP 225 25 9
DD +107 15 8.1875
C 539~619 1256 9
"""

SEML_EXPLODE = """\
scene:NE
protect:18 28 38 48 58
repeat:10000
SET x 776
w1~3 0 1200~1500
C_NUM x+266 1"234'5' 9
SET x x+24
"""

SEML_REFRESH = """\
scene:PE
require:红白
huge:false
activate:true
dance:true
natural:true
cobDelay:true
repeat:1000
w 601
PP 225 25 9
w 601
PP 260 25 9
"""


def test_parse_header():
    h = parse_header(SPEC_EXAMPLE)
    assert h["scene"] == "PE"
    assert h["rhythm"] == "P6  PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)"
    assert h["protect"] == "18 28 58 68"
    assert h["timing"] == "effect"
    assert "w1~8" not in h
    assert parse_header(SEML_SMASH)["repeat"] == "300"


def test_spec_example_headers():
    t = parse_pvzt(SPEC_EXAMPLE)
    assert t.kind == "pvzt"
    assert t.scene == "PE"
    assert t.dialect == "duben"
    assert t.timing_base == "effect"
    assert t.cards == "IINAW LPccc"
    assert t.zombies == "普杆舞车豚 丑气矿篮偷 红"
    assert t.protect == (18, 28, 58, 68)
    assert t.lineup_code.startswith("LI43NMQ")
    assert t.meta["rhythm_raw"].startswith("P6")
    assert t.meta["warnings"] == []


def test_spec_example_selectors_and_lengths():
    t = parse_pvzt(SPEC_EXAMPLE)
    assert [w.selector for w in t.waves] == [
        tuple(range(1, 9)),
        (9, 19),
        (20,),
        (1, 5, 9, 13, 17, 2, 4, 6, 8, 11, 12, 14, 15, 16, 18, 19),  # overlaps deduped
        (10,),
    ]
    assert [w.length_cs for w in t.waves] == [601, 4500, 5500, 1976, None]
    assert all(w.length_range is None for w in t.waves)
    assert t.waves[0].raw == "w1~8 len 601"


def test_selector_forms():
    assert parse_pvzt("w1%4\n").waves[0].selector == (1, 5, 9, 13, 17)
    assert parse_pvzt("w2~9%2\n").waves[0].selector == (2, 4, 6, 8)
    assert parse_pvzt("w11~19\n").waves[0].selector == tuple(range(11, 20))
    assert parse_pvzt("w7\n").waves[0].selector == (7,)
    assert parse_pvzt("w1~8 len 6\n").waves[0].length_cs == 600
    w = parse_pvzt("w1 len 539~619\n").waves[0]
    assert w.length_cs is None and w.length_range == (539, 619)


def test_spec_example_ops():
    t = parse_pvzt(SPEC_EXAMPLE)
    w1 = t.waves[0].top.ops
    pp, c = w1
    assert pp.symbol == "PP" and pp.kind == "cob" and pp.count == 2 and pp.al == 2
    assert pp.effect_cs == 225 and pp.window_cs is None and not pp.relative
    assert pp.target == Target(rows=(2, 5), col=9.0)
    assert pp.phase == PHASE_POST
    assert c.symbol == "C" and c.kind == "fodder" and c.variant == "hard"
    assert c.effect_cs == 539 and c.window_cs == (539, 619)
    assert c.target == Target(rows=(1, 2, 5, 6), col=9.0) and c.count == 4 and c.al == 0

    # ';' is sequencing, same as a newline
    w9 = t.waves[1].top.ops
    assert [o.effect_cs for o in w9] == [225, 2000]
    assert not any(o.simultaneous_with_prev for o in w9)

    # '+N' relative
    w20 = t.waves[2].top.ops
    dd = w20[1]
    assert dd.symbol == "DD" and dd.variant == "delay" and dd.count == 2
    assert dd.relative is True and dd.effect_cs == 107
    assert dd.target == Target(rows=(1, 5), col=8.1875)
    assert w20[0].relative is False and w20[2].relative is False

    ice = t.waves[3].top.ops[0]
    assert ice.kind == "ice" and ice.effect_cs == 11 and ice.target == Target((3,), 5.0)
    assert t.waves[3].top.ops[1].target.col == 8.75

    # '&' simultaneous, missing target
    w10 = t.waves[4].top.ops
    assert w10[0].target is None and w10[0].count == 2
    assert w10[1].symbol == "A" and w10[1].simultaneous_with_prev is False
    assert w10[2].symbol == "W" and w10[2].variant == "squash"
    assert w10[2].simultaneous_with_prev is True


def test_negative_time_is_pre_phase():
    t = parse_pvzt("w1\n  I -100 @ 3 5\n  PP 225 @ 25 9\n")
    ice, pp = t.waves[0].top.ops
    assert ice.effect_cs == -100 and ice.phase == PHASE_PRE and ice.relative is False
    assert pp.phase == PHASE_POST


def test_dialect_changes_symbol_reading():
    t = parse_pvzt("dialect: be2sio4\nw1\n  a 300 @ 2 9\n  C 400 @ 1 9\n")
    a, c = t.waves[0].top.ops
    assert a.variant == "light" and c.variant == "soft"


def test_round_trip_spec_example():
    t = parse_pvzt(SPEC_EXAMPLE)
    text = dump_pvzt(t)
    t2 = parse_pvzt(text)
    assert strip_raw(t2) == strip_raw(t)
    assert dump_pvzt(t2) == text
    assert "w1~17%4,2~8%2,11~12,14~16,18~19 len 1976" in text
    assert "  A 401 @ 2 9 & W 401 @ 5 9" in text
    assert "  DD +107 @ 15 8.1875" in text
    assert "  C 539~619 @ 1256 9" in text


def test_round_trip_seml_examples():
    for src in (SEML_SMASH, SEML_EXPLODE, SEML_REFRESH):
        t = parse_pvzt(src)
        t2 = parse_pvzt(dump_pvzt(t))
        assert strip_raw(t2) == strip_raw(t)


def test_seml_smash():
    t = parse_pvzt(SEML_SMASH)
    assert t.scene == "PE" and t.protect == (18, 28, 58, 68)
    assert any("repeat" in w for w in t.meta["warnings"])
    assert [w.selector for w in t.waves] == [(1,), (2,)]
    assert [w.length_cs for w in t.waves] == [601, 601]
    w2 = t.waves[1].top.ops
    assert w2[0].target == Target((2, 5), 9.0)
    assert w2[1].relative and w2[1].effect_cs == 107 and w2[1].target == Target((1, 5), 8.1875)
    assert w2[2].window_cs == (539, 619) and w2[2].target.rows == (1, 2, 5, 6)


def test_seml_explode():
    t = parse_pvzt(SEML_EXPLODE)
    assert t.scene == "NE"
    assert t.waves[0].selector == (1, 2, 3)
    assert t.waves[0].length_cs == 1500
    op = t.waves[0].top.ops[0]
    # Seml fodder "t+N": plant at t, shovel N later (not arithmetic)
    assert op.symbol == "C" and op.effect_cs == 776 and op.window_cs == (776, 776 + 266)
    assert op.target == Target((1, 2, 3, 4, 5), 9.0)
    warnings = "\n".join(t.meta["warnings"])
    assert "SET" in warnings and "C_NUM" in warnings and "start tick" in warnings
    assert "ice times" in warnings and "repeat" in warnings


def test_seml_refresh():
    t = parse_pvzt(SEML_REFRESH)
    assert [w.selector for w in t.waves] == [(1,), (2,)]
    assert [w.top.ops[0].effect_cs for w in t.waves] == [225, 260]
    warned_keys = {w.split("'")[1] for w in t.meta["warnings"] if "unknown header key" in w}
    assert warned_keys == {"require", "huge", "activate", "dance", "natural", "cobdelay", "repeat"}


def test_seml_explicit_wave_and_auto_increment():
    t = parse_pvzt("w3 601\nPP 225 25 9\nw 601\nw 0 1200~1800\n")
    assert [w.selector for w in t.waves] == [(3,), (4,), (5,)]
    assert t.waves[2].length_cs == 1800


def test_fullwidth_and_comments():
    t = parse_pvzt("scene： PE\nw1，3 len 601  # 注释\n  PP 225 ＠ 25 9 ＆ A 225 @ 3 9\n")
    assert t.scene == "PE"
    assert t.waves[0].selector == (1, 3)
    assert t.waves[0].top.ops[1].simultaneous_with_prev


def test_overlapping_selectors_dedupe():
    assert parse_pvzt("w1,1~3\n").waves[0].selector == (1, 2, 3)


def test_target_without_space_after_at():
    t = parse_pvzt("w1\n  PP 225 @25 9\n")
    assert t.waves[0].top.ops[0].target == Target((2, 5), 9.0)


@pytest.mark.parametrize(
    "text, lineno, fragment",
    [
        ("w1\n  PP 225 @ 25 9\n  XX 300 @ 2 9\n", 3, "unknown operation symbol"),
        ("PP 225 @ 25 9\n", 1, "before any wave block"),
        ("w9~1\n", 1, "start > end"),
        ("w0\n", 1, "start at 1"),
        ("w1 len\n", 1, "len"),
        ("w1\n  PP 225 @ 25\n", 2, "rows col"),
        ("w1\n  PP 225 @ 27 9\n", 2, "row out of range"),
        ("w1\n  PP 225 @ 25 x\n", 2, "bad column"),
        ("w1\n  PP 225 25 9 extra\n", 2, "@ rows col"),
        ("w1\n  PP\n", 2, "symbol and a time"),
        ("w1\n  PP 619~539 @ 25 9\n", 2, "lo > hi"),
        ("scene: XX\nw1\n", 1, "unknown scene"),
        ("dialect: foo\nw1\n", 1, "unknown dialect"),
        ("timing: other\nw1\n", 1, "timing must be"),
        ("scene: PE\nscene: DE\n", 2, "duplicate header"),
        ("w1\nscene: PE\n", 2, "after wave blocks"),
        ("protect: 1\nw1\n", 1, "bad protect"),
    ],
)
def test_errors_carry_line_numbers(text, lineno, fragment):
    with pytest.raises(ValueError) as exc:
        parse_pvzt(text)
    msg = str(exc.value)
    assert msg.startswith(f"line {lineno}:"), msg
    assert fragment in msg, msg


def test_dump_minimal_track():
    t = parse_pvzt("w1\n")
    assert dump_pvzt(t) == "dialect: duben\ntiming: effect\n\nw1\n"



def test_trailing_separator_is_ignored_with_warning():
    # §4.2: ';' equals a newline, so a stray trailing separator is harmless
    t = parse_pvzt("w1 len 601\n  PP 225 ;\n  P 300 &\n")
    assert [op.symbol for op in t.waves[0].top.ops] == ["PP", "P"]
    assert sum("stray separator" in w for w in t.meta["warnings"]) == 2


def test_fullwidth_comment_marker():
    t = parse_pvzt("w1 len 601 ＃ 注释\n  PP 225 @ 25 9 ＃ 注释\n")
    assert t.waves[0].length_cs == 601 and t.waves[0].top.ops[0].target == Target((2, 5), 9.0)


def test_leading_zero_time():
    t = parse_pvzt("w1 len 601\n  PP 08\n  P 0100\n")
    assert [op.effect_cs for op in t.waves[0].top.ops] == [8, 100]


def test_selector_space_before_comma():
    assert parse_pvzt("w1 ,3 len 601\n").waves[0].selector == (1, 3)
    assert parse_pvzt("w1 , 3 , 5 len 601\n").waves[0].selector == (1, 3, 5)


@pytest.mark.parametrize(
    "tok,expected",
    [("300+134", (300, (300, 434))), ("300~+134", (300, (300, 434))), ("300~434", (300, (300, 434))), ("776+266", (776, (776, 1042)))],
)
def test_fodder_shovel_times(tok, expected):
    op = parse_pvzt(f"w1 len 601\n  C {tok} @ 12 9\n").waves[0].top.ops[0]
    assert (op.effect_cs, op.window_cs) == expected and not op.relative


def test_fodder_shovel_before_plant_is_error():
    with pytest.raises(ValueError, match="line 2: shovel time"):
        parse_pvzt("w1 len 601\n  C 300~200 @ 12 9\n")


def test_dump_pvzt_rejects_shorthand():
    from pvztools.schema.track.shorthand import parse_shorthand

    with pytest.raises(ValueError, match="no effect time"):
        dump_pvzt(parse_shorthand("PP|I-PP|N (6|21|9)"))


def test_dump_pvzt_short_length_round_trips():
    t = parse_pvzt("w1 len 0.5\n  PP 225\n")
    assert t.waves[0].length_cs == 50
    assert "len 0.5" in dump_pvzt(t)
    assert parse_pvzt(dump_pvzt(t)).waves[0].length_cs == 50


def test_parse_pvzt_dialect_default_and_header_wins():
    assert parse_pvzt("w1\n  C 100 @ 1 9\n", dialect="baicai").waves[0].top.ops[0].variant == "soft"
    assert parse_pvzt("dialect: duben\nw1\n  C 100 @ 1 9\n", dialect="baicai").waves[0].top.ops[0].variant == "hard"
    with pytest.raises(ValueError, match="unknown dialect"):
        parse_pvzt("w1\n", dialect="nope")
