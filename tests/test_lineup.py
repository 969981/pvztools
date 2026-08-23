"""Tests for pvztools.schema.lineup (docs/lineup-format.md, research/lineup-code-format.md)."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from pvztools.schema.enums import PlantType
from pvztools.schema.lineup import (
    Cell,
    Lineup,
    decode_lineup_code,
    dump_lineup_string,
    encode_lineup_code,
    from_arraydesign,
    parse_lineup_string,
)

ROOT = Path(__file__).resolve().parent.parent
ARRAYDESIGN = ROOT / "docs" / "research" / "arraydesign-lineups.json"
FIXTURES = Path(
    r"C:\Users\mike2\AppData\Local\Temp\claude\C--MyAILab\9b6c7fa6-bf46-4013-a521-0bd4ad13cfc3\scratchpad\fixtures"
)

# research doc test vectors
CODE_A = "LI43NMQIfe3BVVEc+sHIXUAPX7dvSCBUt1TS1OE8dn9XgraGkdQ1ZlSKiEUaVw=="
CODE_B = "LI43NMQIfe3BldJVXRz6wchdQA9ft29IIFS3VM/U4Tx2f1eCtoaR1DVmVFO7RlZW"
CODE_C = "LI5HzH3tAibxsALS11gEdBRxRVTCikaVVA=="
CODE_D = "LI5H5DXMneRDXONmSDBUV9Xhxj9x4x6aMOTV3dQ8ncHGf5Wo1yTF4RRhvHYcPlTFqEd3BA=="
STRING_B = (
    "0,29 1 2 0 0 0,1E 1 2 0 0 1,30 1 2 0 0 0,2C 1 3 0 0 0,1E 1 3 0 0 1,30 1 3 0 0 0,E 1 4 0 0 0,"
    "2A 2 2 1 0 0,1E 2 2 0 0 1,30 2 2 0 0 0,25 2 3 0 0 0,1E 2 3 0 0 1,30 2 3 0 0 0,2C 2 4 0 0 0,"
    "1E 2 4 0 0 1,30 2 4 0 0 0,2A 2 5 1 0 0,10 3 1 0 0 0,2F 3 1 0 0 0,10 3 2 0 0 0,10 3 3 0 0 0,"
    "2F 3 3 0 0 0,10 3 4 0 0 0,10 3 5 0 0 0,E 3 5 0 0 0,1E 3 5 0 0 0,10 3 6 0 0 0,2A 3 6 1 0 0,"
    "1E 3 6 0 0 0,10 4 1 0 0 0,2F 4 1 0 0 0,10 4 2 0 0 0,10 4 3 0 0 0,2F 4 3 0 0 0,10 4 4 0 0 0,"
    "10 4 5 0 0 0,29 4 5 0 0 0,1E 4 5 0 0 0,10 4 6 0 0 0,2A 4 6 1 0 0,1E 4 6 0 0 0,2A 5 2 1 0 0,"
    "1E 5 2 0 0 1,30 5 2 0 0 0,25 5 3 0 0 0,1E 5 3 0 0 1,30 5 3 0 0 0,2C 5 4 0 0 0,1E 5 4 0 0 1,"
    "30 5 4 0 0 0,2A 5 5 1 0 0,29 6 2 0 0 0,1E 6 2 0 0 1,30 6 2 0 0 0,2C 6 3 0 0 0,1E 6 3 0 0 1,"
    "30 6 3 0 0 0,E 6 4 0 0 0"
)


def grid(lu: Lineup, keep_tomb_variant: bool = True):
    """Comparable view: scene, rake row and cells (name/source/meta ignored)."""
    cells = lu.cells
    if not keep_tomb_variant:
        cells = tuple(replace(c, tomb_variant=0) for c in cells)
    return (lu.scene, lu.rake_row, cells)


def test_vector_a_fog_four_cob():
    lu = decode_lineup_code(CODE_A)
    assert lu.scene == "FE" and lu.rake_row is None
    c = lu.cell(1, 2)
    assert c == Cell(1, 2, plant=41, shell=30, shell_imitater=True, props=("ladder",))
    assert lu.cell(1, 3).plant == PlantType.WINTER_MELON
    assert lu.cell(2, 2).plant == PlantType.GLOOM_SHROOM and not lu.cell(2, 2).asleep
    assert lu.cell(2, 3).plant == PlantType.UMBRELLA_LEAF
    assert lu.cell(2, 5) == Cell(2, 5, plant=42)
    assert lu.cell(3, 1) == Cell(3, 1, plant=47, container=16)
    assert lu.cell(3, 2) == Cell(3, 2, container=16)
    assert lu.cell(3, 5) == Cell(3, 5, container=16, shell=30)
    assert lu.cell(3, 6) == Cell(3, 6, plant=42, container=16, shell=30)
    assert lu.cell(4, 5) == Cell(4, 5, plant=41, container=16, shell=30)
    assert lu.cell(6, 2) == replace(lu.cell(1, 2), row=6)  # rows 6/5 mirror 1/2
    assert lu.cobs() == [(3, 1), (3, 3), (4, 1), (4, 3)]
    assert lu.occupied(3, 2) and lu.occupied(3, 4) and not lu.occupied(3, 7)
    assert lu.cell(1, 1) is None and lu.validate() == []


def test_vector_b_pool_four_cob_sleeping_ice():
    lu = decode_lineup_code(CODE_B)
    assert lu.scene == "PE"
    assert lu.cell(1, 4) == Cell(1, 4, plant=14, asleep=True)
    assert lu.cell(3, 5) == Cell(3, 5, plant=14, container=16, shell=30, asleep=True)
    assert lu.cell(6, 4) == Cell(6, 4, plant=14, asleep=True)
    assert lu.plants_of(14) == [(1, 4), (3, 5), (6, 4)]
    assert lu.plants_of(16) == [(r, c) for r in (3, 4) for c in range(1, 7)]
    # B == A + ice shrooms
    a = decode_lineup_code(CODE_A)
    rest = tuple(
        replace(c, plant=None, asleep=False) if c.plant == 14 else c for c in lu.cells
    )
    assert tuple(c for c in rest if not c.is_empty) == a.cells
    assert lu.validate() == []


def test_vector_c_day_pillar_five_cob():
    lu = decode_lineup_code(CODE_C)
    assert lu.scene == "DE" and lu.rake_row is None and len(lu.cells) == 25
    for r in range(1, 6):
        assert lu.cell(r, 1) == Cell(r, 1, plant=37, shell=30)
        assert lu.cell(r, 2) == Cell(r, 2, plant=42, shell=30, shell_imitater=True, props=("ladder",))
        assert lu.cell(r, 3) == Cell(r, 3, plant=41, shell=30, shell_imitater=True, props=("ladder",))
        assert lu.cell(r, 4) == Cell(r, 4, plant=44, shell=30, shell_imitater=True, props=("ladder",))
        assert lu.cell(r, 5) == Cell(r, 5, plant=47)
        for c in range(6, 10):
            assert lu.cell(r, c) is None
    assert len(lu.cobs()) == 5


def test_vector_d_rake_row():
    lu = decode_lineup_code(CODE_D)
    assert lu.scene == "DE" and lu.rake_row == 5
    assert lu.cell(1, 1) == Cell(1, 1, plant=14, shell=30, asleep=True)
    assert len(lu.cobs()) == 11
    assert lu.validate() == []


@pytest.mark.parametrize("code", [CODE_A, CODE_B, CODE_C, CODE_D])
def test_code_roundtrip(code):
    lu = decode_lineup_code(code)
    again = decode_lineup_code(encode_lineup_code(lu))
    assert grid(again) == grid(lu)
    # url-safe alphabet accepted
    assert grid(decode_lineup_code(code.replace("+", "-").replace("/", "_"))) == grid(lu)


def test_string_b_matches_code_b():
    assert grid(parse_lineup_string(STRING_B)) == grid(decode_lineup_code(CODE_B))


def test_string_c_with_trailing_comma():
    row = "25 {r} 1 0 0 0,1E {r} 1 0 0 0,2A {r} 2 1 0 0,1E {r} 2 0 0 1,30 {r} 2 0 0 0,29 {r} 3 0 0 0,1E {r} 3 0 0 1,30 {r} 3 0 0 0,2C {r} 4 0 0 0,1E {r} 4 0 0 1,30 {r} 4 0 0 0,2F {r} 5 0 0 0,"
    text = "2," + "".join(row.format(r=r) for r in range(1, 6))
    assert grid(parse_lineup_string(text)) == grid(decode_lineup_code(CODE_C))


def test_dump_parse_roundtrip_and_rake():
    lu = decode_lineup_code(CODE_D)
    text = dump_lineup_string(lu)
    assert text.startswith("2,") and "31 5 8 0 0 0" in text
    assert grid(parse_lineup_string(text)) == grid(lu)


def test_arraydesign_extra_fields_and_zombies_ignored():
    text = "3,2A 1 1 1 0 0,1E 1 1 0 0 1,30 1 1 0 0 0 0 80 75,32 2 3 1 2 0,55 1 5 0 0 0 0 1 300 75,2F 3 2 0 0 0"
    lu = from_arraydesign(text)
    assert lu.scene == "NE"
    assert lu.cell(1, 1) == Cell(1, 1, plant=42, shell=30, shell_imitater=True, props=("ladder",))
    assert lu.cell(2, 3) == Cell(2, 3, props=("tomb",), tomb_variant=2)
    assert lu.cell(1, 5) is None
    assert lu.cobs() == [(3, 2)]
    # gloom at night is never asleep regardless of state field
    assert not parse_lineup_string("3,2A 1 1 0 0 0").cell(1, 1).asleep
    assert parse_lineup_string("2,2A 1 1 0 0 0").cell(1, 1).asleep
    # binary keeps the gravestone (base=3)
    again = decode_lineup_code(encode_lineup_code(lu))
    assert again.cell(2, 3).props == ("tomb",)


@pytest.mark.parametrize(
    "bad",
    ["", "abc", "2,ZZ 1 1 0 0 0", "7,2F 1 1 0 0 0", "2,2F 1 1", "2,2F 7 1 0 0 0", "2,2F 1 1 0 0 0,2A 1 1 1 0 0"],
)
def test_parse_errors(bad):
    with pytest.raises(ValueError):
        parse_lineup_string(bad)


@pytest.mark.parametrize("bad", ["", "short", "!!!!" * 5, "A" * 20, CODE_C[:-4] + "AAAA"])
def test_decode_errors(bad):
    with pytest.raises(ValueError):
        decode_lineup_code(bad)


def test_validate_rules():
    pe = Lineup("PE", (Cell(3, 2, plant=42), Cell(1, 9, plant=47), Cell(2, 1, plant=47), Cell(2, 2, plant=0)), rake_row=3)
    codes = sorted(d.code for d in pe.validate())
    assert codes == ["lineup.cob-overlap", "lineup.rake", "lineup.range", "lineup.water"]
    re_ = Lineup("RE", (Cell(1, 1, plant=15), Cell(2, 2, plant=15, container=33), Cell(6, 1, plant=1)))
    diags = re_.validate()
    assert [d.code for d in diags if d.level == "error"] == ["lineup.pot", "lineup.range"]
    assert [d.code for d in diags if d.level == "warning"] == ["lineup.doom-roof", "lineup.doom-roof"]
    with pytest.raises(ValueError):
        Lineup("DE", (Cell(1, 1, plant=0), Cell(1, 1, plant=1)))
    with pytest.raises(ValueError):
        Lineup("XX")


def test_dict_roundtrip():
    lu = decode_lineup_code(CODE_B)
    d = json.loads(json.dumps(lu.to_dict()))
    assert Lineup.from_dict(d) == lu
    assert d["scene"] == "PE" and d["cells"][0] == {"row": 1, "col": 2, "plant": 41, "shell": 30, "shell_imitater": True, "props": ["ladder"]}


# --- corpus round trips ----------------------------------------------------
def _arraydesign():
    with open(ARRAYDESIGN, encoding="utf-8") as f:
        return json.load(f)["lineups"]


def test_arraydesign_corpus_roundtrips():
    lineups = _arraydesign()
    assert len(lineups) == 363
    for entry in lineups:
        lu = parse_lineup_string(entry["raw"])
        assert lu.scene == entry["scene"], entry["index"]
        assert grid(parse_lineup_string(dump_lineup_string(lu))) == grid(lu), entry["index"]
        # gravestone look is not carried by the binary code
        assert grid(decode_lineup_code(encode_lineup_code(lu)), False) == grid(lu, False), entry["index"]


def test_arraydesign_3c_eight_cob():
    entry = _arraydesign()[66]
    assert entry["name"] == "3C八炮"
    lu = parse_lineup_string(entry["raw"])
    assert len(lu.cobs()) == 8 and lu.validate() == []
    assert lu.occupied(1, 6) and lu.cell(1, 6) is None


def _fixtures():
    yml = FIXTURES / "lineup.yml"
    js = FIXTURES / "lineup_string.json"
    if not (yml.exists() and js.exists()):
        pytest.skip("pvztoolkit lineup fixtures not available")
    codes = {}
    for line in yml.read_text(encoding="utf-8").splitlines():
        m = re.match(r'^"(.*)":\s*(\S+)\s*$', line)
        if m:
            codes[m.group(1)] = m.group(2)
    with open(js, encoding="utf-8") as f:
        strings = {e["name"]: e["string"] for e in json.load(f)["lineup"]}
    return codes, strings


def test_pvztoolkit_fixture_code_vs_string():
    codes, strings = _fixtures()
    assert len(codes) == 339 and set(codes) == set(strings)
    for name, code in codes.items():
        from_code = decode_lineup_code(code)
        from_string = parse_lineup_string(strings[name])
        assert from_code.scene == name[1:3], name
        assert grid(from_code, False) == grid(from_string, False), name
        assert grid(decode_lineup_code(encode_lineup_code(from_string)), False) == grid(from_code, False), name


def test_parse_lineup_string_row_checked_against_scene():
    # scene field 2 = DE (5 rows): row 6 is rejected at parse time, not at encode time
    with pytest.raises(ValueError, match=r"item 1 .*\(6,1\) outside 5x9 field of DE"):
        parse_lineup_string("2,2F 6 1 0 0 0")
    assert parse_lineup_string("0,2F 6 1 0 0 0").cells[0].row == 6  # PE has 6 rows
