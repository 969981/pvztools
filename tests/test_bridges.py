"""Tests for pvztools.bridges.seml / pvztools.bridges.avz
(docs/research/seml-avz-grammar.md §1 / §2, docs/DESIGN.md §5.4)."""
from __future__ import annotations

import re

import pytest

from pvztools.bridges.avz import format_wave_list, to_avz_dsl
from pvztools.bridges.seml import from_seml, to_seml
from pvztools.schema.track.ast import KIND_ICE, Target
from pvztools.schema.track.pvzt import parse_pvzt, strip_raw
from pvztools.schema.track.shorthand import parse_shorthand

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

# docs/DESIGN.md §5.4 example ③ ([FE] 经典四炮 邻C6u) written in .pvzt syntax
EXAMPLE_3 = """\
scene: FE
lineup: LI43NMQIfe3BVVEc+sHIXUAPX7dvSCBUt1TS1OE8dn9XgraGkdQ1ZlSKiEUaVw==
cards: IINAW LPccc
zombies: 普杆舞车豚 丑气矿篮偷 红
rhythm: 邻C6u  I-PP|I-PP|N|PP (1976|1976|750|749)

w1%4, 2~9%2, 11~19 len 1976
  I 11 @ 3 5
  PP 1776 @ 25 8.75
w10
  PP 341
  A 401 @ 2 9 & W 401 @ 5 9
w20
  PP 341
  I 395 @ 3 5
  PP 2000
"""


# ---------------------------------------------------------------------------
# Seml
# ---------------------------------------------------------------------------


def test_from_seml_smash():
    t = from_seml(SEML_SMASH)
    assert t.kind == "pvzt"
    assert t.scene == "PE"
    assert t.protect == (18, 28, 58, 68)
    assert [w.selector for w in t.waves] == [(1,), (2,)]
    assert [w.length_cs for w in t.waves] == [601, 601]
    w2 = t.waves[1].top.ops
    assert [o.symbol for o in w2] == ["PP", "DD", "C"]
    assert w2[1].relative and w2[1].effect_cs == 107 and w2[1].target == Target((1, 5), 8.1875)
    assert w2[2].window_cs == (539, 619) and w2[2].target == Target((1, 2, 5, 6), 9.0)
    assert t.meta["source"] == "seml"
    assert t.meta["seml"]["settings"] == {"repeat": "300"}
    assert t.meta["warnings"] == []


def test_smash_round_trip():
    t = from_seml(SEML_SMASH)
    text = to_seml(t)
    assert "scene:PE" in text and "protect:18 28 58 68" in text and "repeat:300" in text
    assert "w1 601" in text and "C 539~619 1265 9" in text and "DD +107 15 8.1875" in text
    again = from_seml(text)
    assert strip_raw(again) == strip_raw(t)
    assert again.meta["seml"] == t.meta["seml"]


def test_explode_loop_expansion_and_ice_times():
    t = from_seml(SEML_EXPLODE)
    assert [w.selector for w in t.waves] == [(1,), (2,), (3,)]
    assert all(w.length_cs == 1500 for w in t.waves)
    # SET x x+24 re-executes per expanded wave (Seml semantics); "x+266" = plant at x, shovel 266 later
    assert [w.top.ops[1].effect_cs for w in t.waves] == [776, 800, 824]
    assert [w.top.ops[1].window_cs for w in t.waves] == [(776, 1042), (800, 1066), (824, 1090)]
    for w in t.waves:
        ice = w.top.ops[0]
        assert ice.kind == KIND_ICE and ice.effect_cs == 0 and ice.target is None
    assert t.meta["seml"]["start_tick"] == {"0": 1200, "1": 1200, "2": 1200}
    # warnings point at Seml source lines and are deduplicated across iterations
    assert any(w.startswith("line 6: Seml smart symbol 'C_NUM'") for w in t.meta["warnings"])
    assert len(t.meta["warnings"]) == len(set(t.meta["warnings"]))
    out = to_seml(t)
    assert "w1 0 1200~1500" in out and "C 776~1042 12345 9" in out
    assert strip_raw(from_seml(out)) == strip_raw(t)


def test_refresh_settings_round_trip():
    t = from_seml(SEML_REFRESH)
    s = t.meta["seml"]["settings"]
    assert s["require"] == "红白" and s["huge"] == "false" and s["cobdelay"] == "true"
    assert t.meta["warnings"] == []
    out = to_seml(t)
    for line in ("require:红白", "huge:false", "cobdelay:true", "repeat:1000", "w2 601", "PP 260 25 9"):
        assert line in out
    assert strip_raw(from_seml(out)) == strip_raw(t)


def test_multiple_ice_times_sorted_and_wave_with_ice_and_ops():
    t = from_seml("scene:DE\nw1 300 10 1500\nP 700 2 9\n")
    ops = t.waves[0].top.ops
    assert [(o.symbol, o.effect_cs) for o in ops] == [("I", 300), ("I", 10), ("P", 700)]
    assert "w1 10 300 1500" in to_seml(t)


@pytest.mark.parametrize(
    "text, match",
    [
        ("w1\n", "line 1: .*wave length"),
        ("w1 abc 600\n", "line 1: bad ice time"),
        ("w1 700 600\n", "line 1: last ice time 700 exceeds"),
        ("w1 800~600\n", "line 1: start tick"),
        ("w3~1 600\n", "line 1: bad wave loop"),
        ("w1 601\nX 100 2 9\n", "line 2: unknown operation symbol"),
        ("scene:PE\n\nw1 601\n# c\nPP 225 25\n", "line 5: "),
    ],
)
def test_from_seml_errors(text, match):
    with pytest.raises(ValueError, match=match):
        from_seml(text)


def test_to_seml_from_pvzt_spec_example_fills_gaps_and_notes():
    t = parse_pvzt(EXAMPLE_3)
    out = to_seml(t)
    assert out.startswith("scene:FE\n")
    lines = out.splitlines()
    wave_lines = [l for l in lines if re.match(r"^w\d+ ", l)]
    assert [l.split()[0] for l in wave_lines] == [f"w{n}" for n in range(1, 21)]
    assert "w1 11 1976" in out  # ice target dropped into wave ice time
    assert "w3 9999" in out  # wave 3 not in the track
    assert "w10 9999" in out and "A 401 2 9" in out and "W 401 5 9" in out
    assert "PP 341 25 9" in out  # default rows on PP without target
    assert "# pvztools: w1 I: ice target dropped" in out
    assert from_seml(out).scene == "FE"


def test_to_seml_unsupported_ops_are_commented():
    t = parse_pvzt("scene: PE\nw1 len 601\n  P -32 @ 2 9\n  IO 0\n  N' 300 @ 3 5\n  A 300\n")
    out = to_seml(t)
    assert "# P -32" in out
    assert "# pvztools: w1 IO: natural output" in out
    assert "N 300 3 5" in out and "imitater doom" in out
    assert "# A 300" in out


def test_to_seml_rejects_shorthand():
    with pytest.raises(ValueError, match="shorthand"):
        to_seml(parse_shorthand("P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)"))


# ---------------------------------------------------------------------------
# AvZ DSL
# ---------------------------------------------------------------------------


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and text.count("(") == text.count(")")


def test_to_avz_dsl_example_3():
    t = parse_pvzt(EXAMPLE_3)
    cpp = to_avz_dsl(t)
    assert cpp.startswith("#include <avz.h>")
    assert "void AScript() {" in cpp
    assert _balanced(cpp)
    assert 'ASelectCards("IINAW LPccc");' in cpp
    assert 'ASetZombies("普杆舞车豚 丑气矿篮偷 红");' in cpp
    assert "OnWave(1_2, 4_6, 8_9, 11_19) {" in cpp
    assert "At(11_cs) I(3, 5)," in cpp
    assert "At(1776_cs) PP(8.75)," in cpp
    assert "At(1776_cs) Trig()," in cpp  # 1976 - 200
    assert "OnWave(10) {" in cpp and "At(341_cs) PP(9)," in cpp
    assert "At(401_cs) A(2, 9) & W(5, 9)," in cpp
    assert "OnWave(20) {" in cpp and "At(2000_cs) PP(9)," in cpp
    assert "// rhythm: 邻C6u" in cpp
    assert "// NOTE: PP: no target -> PP(9)" in cpp


def test_to_avz_dsl_smash_and_ctx():
    t = from_seml(SEML_SMASH)
    cpp = to_avz_dsl(t)
    assert _balanced(cpp)
    assert "OnWave(1) {" in cpp and "OnWave(2) {" in cpp
    assert "At(225_cs) PP(9)," in cpp
    assert "At(332_cs) DD(8.1875)," in cpp  # +107 resolved against 225
    assert "At(539_cs) C(80, 1265, 9)," in cpp  # window width as shovel delay
    assert "At(401_cs) Trig()," in cpp
    assert "Trig()" not in to_avz_dsl(t, ctx={"trig": False})


def test_to_avz_dsl_op_mapping():
    t = parse_pvzt(
        "scene: NE\nw1 len 1500\n  I 10\n  N' 300 @ 3 5\n  A' 400 @ 12 8\n  c 500 @ 1 9\n"
        "  P' 600 @ 2 9\n  D 700 @ 14 8\n  IO 0\n"
    )
    cpp = to_avz_dsl(t)
    assert _balanced(cpp)
    assert "At(10_cs) I()," in cpp
    assert "At(300_cs) N(3, 5, true)," in cpp
    assert "At(400_cs) J(1, 8) & J(2, 8)," in cpp
    assert "At(500_cs) C(134, 1, 9)," in cpp
    assert "At(600_cs) P<RECOVER_FIRE>(2, 9)," in cpp
    assert "At(700_cs) DD(8)," in cpp  # rows 1/4 = 5-row default delay pair
    assert "IO" not in cpp.split("void AScript")[1]
    cpp2 = to_avz_dsl(t, ctx={"ice_pos": (4, 1), "fodder_delay": 200})
    assert "I(4, 1)," in cpp2 and "C(200, 1, 9)," in cpp2


def test_to_avz_dsl_negative_time_and_errors():
    cpp = to_avz_dsl(parse_pvzt("scene: PE\nw2~9 len 601\n  P -32 @ 2 9\n"))
    assert "OnWave(2_9) {" in cpp and "At(-32_cs) P(2, 9)," in cpp
    with pytest.raises(ValueError, match="shorthand"):
        to_avz_dsl(parse_shorthand("PIPIPN"))
    with pytest.raises(ValueError, match="timing_base"):
        to_avz_dsl(parse_pvzt("timing: operation\nw1 len 601\n  PP 225 @ 25 9\n"))
    with pytest.raises(ValueError, match="relative"):
        to_avz_dsl(parse_pvzt("w1 len 601\n  PP +10 @ 25 9\n"))


@pytest.mark.parametrize(
    "waves, expected",
    [
        ((1,), "1"),
        ((1, 2), "1_2"),
        (tuple(range(1, 9)), "1_8"),
        ((1, 5, 9, 13, 17), "1_17_step4"),
        ((1, 5, 9, 13, 17, 2, 4, 6, 8, 11, 12, 14, 15, 16, 18, 19), "1_2, 4_6, 8_9, 11_19"),
        ((9, 19), "9, 19"),
        ((20, 10), "10, 20"),
    ],
)
def test_format_wave_list(waves, expected):
    assert format_wave_list(waves) == expected


def test_from_seml_reads_c_as_plain_fodder():
    from pvztools.schema.track.lint import lint

    t = from_seml("scene:PE\nw1~2 601\n C 300~434 12 9\n a 100 1 9\n")
    ops = t.waves[0].top.ops
    assert ops[0].symbol == "C" and ops[0].kind == "fodder" and ops[0].variant == "soft"
    assert ops[1].kind == "ash" and ops[1].variant == "squash"
    assert t.dialect == "baicai"
    # no spurious 3000cs pumpkin CD on per-wave fodder lines
    assert not [d for d in lint(t) if d.code == "CARD_CD" and d.op == "C" and "pumpkin" in d.message]
    # README "Explode" template: fodder shovel delay survives into the AvZ export
    assert "C(266" in to_avz_dsl(from_seml(SEML_EXPLODE))
