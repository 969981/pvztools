"""1437 人类可读阵型格式（parse_1437 / to_1437）测试。"""

from __future__ import annotations

import pytest

from pvztools.schema.enums import PlantType
from pvztools.schema.lineup import (
    decode_lineup_code,
    encode_lineup_code,
    parse_1437,
    to_1437,
)
from pvztools.schema.lineup.human import Human1437Error

_SPIKEROCK = int(PlantType.SPIKEROCK)
_SUN = int(PlantType.SUNFLOWER)
_WMELON = int(PlantType.WINTER_MELON)
_UMBRELLA = int(PlantType.UMBRELLA_LEAF)
_STAR = int(PlantType.STARFRUIT)
_CATTAIL = int(PlantType.CATTAIL)
_GLOOM = int(PlantType.GLOOM_SHROOM)
_PUMPKIN = int(PlantType.PUMPKIN)
_LILY = int(PlantType.LILY_PAD)

# 吧友"完美阵型"贴的原文
PERFECT = """1 地刺王 （向日葵）（向日葵）（冰西瓜）地刺王，地刺王，地刺王。。。。
2 （向日葵）（保护伞）（冰瓜）（向日葵）（保护伞），地刺王，地刺王。。。。
3 五角星 五角星 香蒲 五角星 （五角星）（五角星）（五角星）（忧郁）（忧郁）
4 五角星 五角星 香蒲 五角星 （五角星）（五角星）（五角星）（忧郁）（忧郁）
5 （向日葵）（保护伞）（冰瓜）（向日葵）（保护伞），地刺王，地刺王。。。。
6 地刺王 （向日葵）（向日葵）（冰西瓜）地刺王，地刺王，地刺王。。。。"""


def _cell(lu, row, col):
    return next((c for c in lu.cells if c.row == row and c.col == col), None)


def test_parse_perfect_lineup_shape():
    lu = parse_1437(PERFECT, scene="PE")
    assert lu.scene_id.code == "PE"
    assert len(lu.cells) == 54  # 6 行 × 9 列全满


def test_pumpkin_shell():
    lu = parse_1437(PERFECT, scene="PE")
    # 行1 col2 = 向日葵 套南瓜
    c = _cell(lu, 1, 2)
    assert c.plant == _SUN and c.shell == _PUMPKIN
    # 行1 col1 = 地刺王 无南瓜
    c = _cell(lu, 1, 1)
    assert c.plant == _SPIKEROCK and c.shell is None


def test_ellipsis_repeats_last():
    lu = parse_1437(PERFECT, scene="PE")
    # 行1 "...地刺王。。。。" -> col7,8,9 都是地刺王
    for col in (7, 8, 9):
        assert _cell(lu, 1, col).plant == _SPIKEROCK


def test_water_row_auto_lily_land_plant():
    lu = parse_1437(PERFECT, scene="PE")
    # 行3(水路) 五角星(陆生) 自动补睡莲
    c = _cell(lu, 3, 1)
    assert c.plant == _STAR and c.container == _LILY


def test_water_row_aquatic_no_lily():
    lu = parse_1437(PERFECT, scene="PE")
    # 行3 col3 香蒲(水生) 不补睡莲
    c = _cell(lu, 3, 3)
    assert c.plant == _CATTAIL and c.container is None


def test_land_row_no_lily():
    lu = parse_1437(PERFECT, scene="PE")
    # 行1(陆地) 不补睡莲
    assert _cell(lu, 1, 1).container is None


def test_name_resolution_nicknames():
    lu = parse_1437(PERFECT, scene="PE")
    assert _cell(lu, 1, 4).plant == _WMELON     # 冰西瓜
    assert _cell(lu, 2, 2).plant == _UMBRELLA   # 保护伞
    assert _cell(lu, 3, 8).plant == _GLOOM      # 忧郁


def test_roundtrip_through_lineup_code():
    lu = parse_1437(PERFECT, scene="PE")
    code = encode_lineup_code(lu)
    back = decode_lineup_code(code)
    assert len(back.cells) == len(lu.cells)
    # 逐格植物一致
    orig = {(c.row, c.col): c.plant for c in lu.cells}
    got = {(c.row, c.col): c.plant for c in back.cells}
    assert orig == got


def test_to_1437_roundtrip_parses_back():
    lu = parse_1437(PERFECT, scene="PE")
    text = to_1437(lu)
    lu2 = parse_1437(text, scene="PE")
    a = {(c.row, c.col): (c.plant, c.shell) for c in lu.cells}
    b = {(c.row, c.col): (c.plant, c.shell) for c in lu2.cells}
    assert a == b


def test_ascii_parens_and_dots():
    lu = parse_1437("1 (向日葵) 地刺王 ...", scene="PE")
    assert _cell(lu, 1, 1).plant == _SUN and _cell(lu, 1, 1).shell == _PUMPKIN
    # ... 重复地刺王到 9 列
    assert _cell(lu, 1, 9).plant == _SPIKEROCK


def test_comma_and_dunhao_separators():
    lu = parse_1437("1 向日葵，地刺王、香蒲", scene="PE")
    assert _cell(lu, 1, 1).plant == _SUN
    assert _cell(lu, 1, 2).plant == _SPIKEROCK
    assert _cell(lu, 1, 3).plant == _CATTAIL


def test_unknown_plant_raises():
    with pytest.raises(Human1437Error):
        parse_1437("1 不存在的植物名xyz", scene="PE")


def test_no_rows_raises():
    with pytest.raises(Human1437Error):
        parse_1437("这里没有行号", scene="PE")


def test_non_pool_scene_no_water_lily():
    # 前院(FE) 无水路，五角星不补睡莲
    lu = parse_1437("3 五角星 五角星", scene="FE")
    assert _cell(lu, 3, 1).container is None


def test_ignores_prose_lines():
    text = "经过数次实验，我终于找到了完美阵型。\n1 向日葵 地刺王\n（备注：括号是南瓜）"
    lu = parse_1437(text, scene="PE")
    assert len(lu.cells) == 2
