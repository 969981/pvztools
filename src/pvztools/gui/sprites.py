"""植物贴图：从 lonelystar 布阵器下载原版美术到本地缓存，供 GUI 显示。

设计取舍（见与用户的讨论）：
* 原版游戏 main.pak 里植物是 reanim 骨骼动画，没有现成整株图；
  lonelystar 站长已把它们离线渲染成整株精灵图（多帧横条）。
* 这些图 **不打包进 pip 包**（pvztools 不含游戏美术），而是运行时
  下载到用户本地缓存目录，加载显示——既有原版美术又不涉及分发。
* 每张图是 CMax×RMax 的帧网格；显示时裁到第 0 帧（左上格）。

下载在工作线程做（网络 IO）；贴图创建必须在 UI/GL 线程做。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

# 植物 id -> (精灵图名, CMax 列, RMax 行, 层)   层: plant/container/shell/addon
PLANT_SPRITES: Dict[int, Tuple[str, int, int, str]] = {
    0: ("Peashooter", 2, 1, "plant"),
    1: ("Sunflower", 2, 1, "plant"),
    2: ("Cherrybomb", 1, 1, "plant"),
    3: ("Wallnut", 3, 1, "plant"),
    4: ("Potatomine", 2, 1, "plant"),
    5: ("Snowpea", 2, 1, "plant"),
    6: ("Chomper", 2, 3, "plant"),
    7: ("Repeater", 2, 1, "plant"),
    8: ("Puffshroom", 1, 1, "plant"),
    9: ("Sunshroom", 3, 1, "plant"),
    10: ("Fumeshroom", 1, 2, "plant"),
    11: ("Gravebuster", 3, 1, "plant"),
    12: ("Hypnoshroom", 2, 1, "plant"),
    13: ("Scaredyshroom", 1, 3, "plant"),
    14: ("Iceshroom", 1, 1, "plant"),
    15: ("Doomshroom", 2, 1, "plant"),
    16: ("Lilypad", 2, 1, "container"),
    17: ("Squash", 1, 1, "plant"),
    18: ("Threepeater", 1, 1, "plant"),
    19: ("Tanglekelp", 1, 1, "plant"),
    20: ("Jalapeno", 3, 1, "plant"),
    21: ("Caltrop", 1, 1, "plant"),
    22: ("Torchwood", 2, 1, "plant"),
    23: ("Tallnut", 2, 1, "plant"),
    24: ("Seashroom", 2, 1, "plant"),
    25: ("Plantern", 1, 1, "plant"),
    26: ("Cactus", 1, 3, "plant"),
    27: ("Blover", 2, 1, "plant"),
    28: ("Splitpea", 1, 1, "plant"),
    29: ("Starfruit", 2, 1, "plant"),
    30: ("Pumpkin", 2, 2, "shell"),
    31: ("Magnetshroom", 2, 3, "plant"),
    32: ("Cabbagepult", 2, 1, "plant"),
    33: ("Pot", 1, 2, "container"),
    34: ("Cornpult", 1, 2, "plant"),
    35: ("Coffeebean", 1, 1, "addon"),
    36: ("Garlic", 2, 1, "plant"),
    37: ("Umbrellaleaf", 1, 1, "plant"),
    38: ("Marigold", 2, 1, "plant"),
    39: ("Melonpult", 2, 1, "plant"),
    40: ("Gatlingpea", 2, 1, "plant"),
    41: ("Twinsunflower", 2, 1, "plant"),
    42: ("Gloomshroom", 2, 2, "plant"),
    43: ("Cattail", 2, 1, "plant"),
    44: ("Wintermelon", 2, 1, "plant"),
    45: ("GoldMagnet", 2, 1, "plant"),
    46: ("Spikerock", 2, 1, "plant"),
    47: ("Cobcannon", 2, 1, "plant"),
    48: ("Imitater", 1, 1, "plant"),
}

#: 用"选卡"卡片图（单张干净图，无动画帧），而非场上动画精灵条。
_BASE_URL = "http://lonelystar.org/ArrayDesign/image/Card/"

_tex_cache: Dict[int, object] = {}   # pid -> ImageAndSize（UI 线程）
_missing: set = set()                # 加载失败的，避免反复重试


def cache_dir() -> Path:
    root = os.environ.get("PVZTOOLS_SPRITE_DIR")
    if root:
        p = Path(root)
    elif os.name == "nt":
        p = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "pvztools" / "sprites"
    else:
        p = Path.home() / ".cache" / "pvztools" / "sprites"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sprite_name(pid: int) -> Optional[str]:
    e = PLANT_SPRITES.get(int(pid))
    return e[0] if e else None


def sprite_path(pid: int) -> Optional[Path]:
    name = sprite_name(pid)
    return cache_dir() / f"{name}.png" if name else None


def is_cached(pid: int) -> bool:
    p = sprite_path(pid)
    return bool(p and p.exists() and p.stat().st_size > 0)


def all_ids() -> Tuple[int, ...]:
    return tuple(sorted(PLANT_SPRITES))


def cached_count() -> int:
    return sum(1 for pid in PLANT_SPRITES if is_cached(pid))


def _frame0_box(w: int, h: int):
    """按宽高比推断第 0 帧的裁剪框（方形帧，横/竖条或单帧）。"""
    ratio = w / h if h else 1.0
    if ratio >= 1.5:            # 横向动画条
        cols = max(1, round(ratio))
        return (0, 0, w // cols, h)
    if ratio <= 0.67:           # 纵向动画条
        rows = max(1, round(h / w))
        return (0, 0, w, h // rows)
    return (0, 0, w, h)         # 近方形：单帧


def _crop_frame0(data: bytes) -> bytes:
    """把精灵条裁到第 0 帧（需要 Pillow；没有则原样返回）。"""
    try:
        import io as _io

        from PIL import Image
    except Exception:  # noqa: BLE001
        return data
    try:
        im = Image.open(_io.BytesIO(data)).convert("RGBA")
        im = im.crop(_frame0_box(im.width, im.height))
        buf = _io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return data


def download(pid: int, timeout: float = 20.0) -> bool:
    """下载单个精灵图、裁到第 0 帧、存缓存（工作线程调用）。"""
    name = sprite_name(pid)
    if not name:
        return False
    dst = cache_dir() / f"{name}.png"
    if dst.exists() and dst.stat().st_size > 0:
        return True
    import urllib.request

    try:
        with urllib.request.urlopen(_BASE_URL + name + ".png", timeout=timeout) as r:
            data = r.read()
        if data:
            dst.write_bytes(data)  # 卡片是单张图，无需裁帧
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


def download_all(progress: Optional[Callable[[int, int], None]] = None) -> int:
    """下载全部缺失的精灵图；返回成功数（工作线程调用）。"""
    ids = all_ids()
    ok = 0
    for i, pid in enumerate(ids, 1):
        if download(pid):
            ok += 1
        if progress:
            progress(i, len(ids))
    return ok


def texture(imgui_bundle, pid: int):
    """返回 (tex_ref, size) 或 None。必须在 UI/GL 线程调用（会创建贴图）。"""
    pid = int(pid)
    if pid in _tex_cache:
        img = _tex_cache[pid]
        return (img.texture_id, img.size)
    if pid in _missing or not is_cached(pid):
        return None
    try:
        data = sprite_path(pid).read_bytes()
        img = imgui_bundle.hello_imgui.image_and_size_from_encoded_data(
            data, f"pvz_{pid}"
        )
        _tex_cache[pid] = img
        return (img.texture_id, img.size)
    except Exception:  # noqa: BLE001
        _missing.add(pid)
        return None
