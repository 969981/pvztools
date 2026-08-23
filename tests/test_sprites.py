"""植物贴图模块的纯逻辑测试（不联网、不建贴图）。"""

from __future__ import annotations

from pvztools.gui import sprites
from pvztools.schema.enums import PlantType


def test_all_plant_types_covered():
    # 0..48 每个植物都有精灵映射
    for pt in PlantType:
        assert int(pt) in sprites.PLANT_SPRITES, pt.name


def test_layers_classified():
    assert sprites.PLANT_SPRITES[16][3] == "container"   # 睡莲
    assert sprites.PLANT_SPRITES[33][3] == "container"   # 花盆
    assert sprites.PLANT_SPRITES[30][3] == "shell"       # 南瓜
    assert sprites.PLANT_SPRITES[35][3] == "addon"       # 咖啡豆
    assert sprites.PLANT_SPRITES[1][3] == "plant"        # 向日葵


def test_sprite_name():
    assert sprites.sprite_name(42) == "Gloomshroom"
    assert sprites.sprite_name(47) == "Cobcannon"
    assert sprites.sprite_name(999) is None


def test_frame0_box_horizontal_strip():
    # 399x133 三帧横条 -> 第0帧 133 宽
    assert sprites._frame0_box(399, 133) == (0, 0, 133, 133)
    # 340x170 两帧 -> 170
    assert sprites._frame0_box(340, 170) == (0, 0, 170, 170)


def test_frame0_box_vertical_strip():
    # 160x440 竖三帧 -> 高 146
    box = sprites._frame0_box(160, 440)
    assert box[0] == 0 and box[1] == 0 and box[2] == 160
    assert 140 <= box[3] <= 160


def test_frame0_box_single_frame():
    # 近方形单帧原样
    assert sprites._frame0_box(266, 266) == (0, 0, 266, 266)
    assert sprites._frame0_box(250, 190) == (0, 0, 250, 190)


def test_crop_frame0_no_pillow_passthrough(monkeypatch):
    # 无 PIL 时原样返回，不崩
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no pillow")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    data = b"not-a-real-png"
    assert sprites._crop_frame0(data) == data


def test_all_ids_sorted_and_complete():
    ids = sprites.all_ids()
    assert list(ids) == sorted(ids)
    assert len(ids) == len(sprites.PLANT_SPRITES)
