"""Tests for pvztools.schema.track.jsonio (docs/track-format.md §5 round trip)."""
from __future__ import annotations

import json

import pytest

from pvztools.schema.track.ast import HalfWave, Op, RhythmName, Target, Track, Wave
from pvztools.schema.track.jsonio import SCHEMA_KEY, from_dict, from_json, to_dict, to_json
from pvztools.schema.track.pvzt import parse_pvzt
from pvztools.schema.track.shorthand import parse_shorthand

SHORTHAND_EXAMPLES = [
    "P6: PP|PP|PPSS|PP|PP|PPSS (6|6|6|6|6|6)",
    "P4: PC|PC|PC|PC 869|869|869|869",
    "ch4: IP-P|IP-P 1738|1738",
    "ch6: P|I-P|P|I-P 601|1150|601|1150",
    "邻C6u: I-P|I-P|N|P (1975|1975|750|750)",
    "|PP|I-PP|N (6, 21, 9)",
    "ICP-BPD/Pd",
    "PIPIPN",
    "w1:PP w2:I-PP (6|6)",
]

PVZT_EXAMPLE = """\
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


@pytest.mark.parametrize("text", SHORTHAND_EXAMPLES)
def test_shorthand_round_trip(text):
    track = parse_shorthand(text)
    assert from_json(to_json(track)) == track


def test_pvzt_round_trip():
    track = parse_pvzt(PVZT_EXAMPLE)
    restored = from_json(to_json(track))
    assert restored == track
    # tuple types restored, not lists
    assert isinstance(restored.waves[0].selector, tuple)
    assert isinstance(restored.waves[0].top.ops[1].window_cs, tuple)
    assert isinstance(restored.waves[0].top.ops[0].target.rows, tuple)
    assert isinstance(restored.protect, tuple)


def test_schema_key_and_json_shape():
    track = parse_shorthand("ch4: IP-P|IP-P 1738|1738")
    data = json.loads(to_json(track))
    assert data[SCHEMA_KEY] == 1
    assert data["kind"] == "shorthand"
    assert data["rhythm"]["family"] == "ch"
    assert data["rhythm"]["suffixes"] == []
    assert data["waves"][0]["selector"] == [1]
    assert data["waves"][0]["top"]["ops"][0]["symbol"] == "I"
    assert "邻" in to_json(parse_shorthand("邻C6u: I-P|I-P|N|P"))  # ensure_ascii=False


def test_to_dict_is_json_compatible_and_stable():
    track = parse_pvzt(PVZT_EXAMPLE)
    d = to_dict(track)
    assert json.loads(json.dumps(d)) == d
    assert from_dict(d) == track
    assert to_dict(from_dict(d)) == d


def test_rhythm_name_and_bottom_half_round_trip():
    op = Op(symbol="P", kind="cob", phase="post", effect_cs=300, target=Target((2,), 8.75))
    wave = Wave(selector=(1, 2), top=HalfWave((op,)), bottom=HalfWave((op,)), mirrored=True,
                length_range=(1800, 1900), raw="x")
    track = Track(
        waves=(wave,),
        rhythm=RhythmName("Cu", 6, "邻", ("i", "-N"), "邻C6ui-N"),
        scene="FE",
        protect=(18, 28),
        lineup_code="abc",
        meta={"warnings": [{"code": "X", "message": "m"}]},
    )
    assert from_json(to_json(track)) == track
    assert from_json(to_json(track, indent=None)) == track


def test_missing_or_wrong_version():
    track = parse_shorthand("PIPIPN")
    data = to_dict(track)
    del data[SCHEMA_KEY]
    with pytest.raises(ValueError, match="pvztools_track"):
        from_dict(data)
    data[SCHEMA_KEY] = 99
    with pytest.raises(ValueError, match="unsupported"):
        from_dict(data)


def test_bad_shapes_report_path():
    track = parse_pvzt("w1 len 601\n  PP 225 @ 25 9\n")
    data = to_dict(track)
    data["waves"][0]["top"]["ops"][0]["target"]["rows"] = [2, "5"]
    with pytest.raises(ValueError, match=r"track.waves\[0\].top.ops\[0\].target.rows\[1\]"):
        from_dict(data)
    data = to_dict(track)
    data["waves"][0]["length_range"] = [1, 2, 3]
    with pytest.raises(ValueError, match=r"length_range"):
        from_dict(data)
    with pytest.raises(ValueError, match="invalid track JSON"):
        from_json("{not json")
