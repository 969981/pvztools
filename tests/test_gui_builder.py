"""自定义布阵器的放置逻辑测试（纯逻辑，不需要 imgui/GL）。"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("imgui_bundle")

from pvztools.gui import app as A


def _stub():
    class Stub:
        pass
    s = Stub()
    s.build_cells = {}
    s._palette = []
    for m in ("_paint_cell", "_erase_cell", "_export_cells"):
        setattr(s, m, types.MethodType(getattr(A.App, m), s))
    return s


def test_cob_occupies_two_cells():
    s = _stub()
    s.build_brush_pid = 47
    s._paint_cell(3, 3, "PE")
    assert s.build_cells[(3, 3)] == {"plant": 47}
    assert s.build_cells[(3, 4)]["cob_right"] == (3, 3)


def test_cob_cannot_be_pumpkined():
    s = _stub()
    s.build_brush_pid = 47
    s._paint_cell(3, 3, "PE")
    s.build_brush_pid = 30  # pumpkin
    s._paint_cell(3, 3, "PE")
    assert "shell" not in s.build_cells[(3, 3)]


def test_cob_last_column_rejected():
    s = _stub()
    s.build_brush_pid = 47
    s._paint_cell(3, 9, "PE")  # 第9列放不下两格
    assert (3, 9) not in s.build_cells


def test_click_cob_right_clears_whole_cob():
    s = _stub()
    s.build_brush_pid = 47
    s._paint_cell(3, 3, "PE")
    s.build_brush_pid = 1  # sunflower on the right half
    s._paint_cell(3, 4, "PE")
    assert (3, 3) not in s.build_cells        # 整门炮被清
    assert s.build_cells[(3, 4)]["plant"] == 1


def test_erase_cob_from_either_half():
    s = _stub()
    s.build_brush_pid = 47
    s._paint_cell(2, 5, "PE")
    s._erase_cell(2, 6)  # 从右半格擦
    assert (2, 5) not in s.build_cells and (2, 6) not in s.build_cells


def test_roof_cob_two_pots():
    s = _stub()
    s.build_brush_pid = 47
    s._paint_cell(2, 4, "RE")
    assert s.build_cells[(2, 4)]["container"] == 33
    assert s.build_cells[(2, 5)]["container"] == 33
    exported = s._export_cells()
    # 左格炮+花盆，右格只花盆
    left = next(c for c in exported if c["col"] == 4)
    right = next(c for c in exported if c["col"] == 5)
    assert left["plant"] == 47 and left["container"] == 33
    assert right.get("plant") is None and right["container"] == 33


def test_water_row_auto_lily():
    s = _stub()
    s.build_brush_pid = 29  # starfruit (land)
    s._paint_cell(3, 1, "PE")
    assert s.build_cells[(3, 1)]["container"] == 16


def test_aquatic_no_lily():
    s = _stub()
    s.build_brush_pid = 43  # cattail (aquatic)
    s._paint_cell(3, 1, "PE")
    assert "container" not in s.build_cells[(3, 1)]


def test_land_row_no_lily():
    s = _stub()
    s.build_brush_pid = 29
    s._paint_cell(1, 1, "PE")
    assert "container" not in s.build_cells[(1, 1)]


def test_roof_plant_gets_pot():
    s = _stub()
    s.build_brush_pid = 1  # sunflower on roof
    s._paint_cell(1, 1, "RE")
    assert s.build_cells[(1, 1)]["container"] == 33


def test_export_skips_bare_cob_right():
    s = _stub()
    s.build_brush_pid = 47
    s._paint_cell(3, 3, "PE")  # 泳池：右格无花盆
    out = s._export_cells()
    cols = {c["col"] for c in out}
    assert 3 in cols and 4 not in cols  # 右格标记不导出
