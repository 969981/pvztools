"""pvztools 图形界面（Dear ImGui / imgui-bundle）。

渲染层只读 worker.snapshot、向 worker.submit() 投递命令，
自身绝不直接触碰 Game 对象（线程纪律见 worker.py 模块注释）。
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

import imgui_bundle
from imgui_bundle import hello_imgui, imgui

from pvztools import __version__
from pvztools.i18n import get_language, set_language

from . import catalog, overlay, sprites, theme
from .strings import tr
from .worker import GameWorker, Snapshot

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)

_ORANGE = (0.85, 0.45, 0.15, 1.0)
_GREEN = (0.13, 0.55, 0.13, 1.0)
_RED = (0.80, 0.20, 0.20, 1.0)

_PENDING_TTL_S = 0.6

_LAYER_ORDER = {"plant": 0, "container": 1, "shell": 2, "addon": 3}
_COB = 47       # 玉米加农炮：占两格、不能套南瓜、屋顶需两个花盆
_PUMPKIN = 30
_POT = 33       # 花盆
_LILY = 16      # 睡莲
# 场景水路（自动睡莲）/ 屋顶（自动花盆）
_WATER_ROWS = {"PE": (3, 4), "ME": (3, 4)}
_POT_SCENES = frozenset({"RE"})
_AQUATIC = frozenset({16, 43, 19, 24})  # 睡莲/香蒲/缠绕海草/水兵菇


def _build_palette():
    """全植物调色板：[(pid, 中文短名, 层), ...]，植物在前，底座/南瓜/咖啡在后。"""
    try:
        from pvztools.names import plant_name
    except Exception:  # noqa: BLE001
        plant_name = None
    out = []
    for pid, (_sprite, _c, _r, layer) in sprites.PLANT_SPRITES.items():
        zh = plant_name(pid, "zh") if plant_name else str(pid)
        out.append((pid, zh, layer))
    out.sort(key=lambda t: (_LAYER_ORDER.get(t[2], 0), t[0]))
    return out



class App:
    def __init__(self, worker: GameWorker) -> None:
        self.worker = worker
        self.sun_input = 9990
        self.catalog = catalog.load_catalog()
        self.scene_filter = 0  # 0=全部, 之后是 SCENES
        self.lineup_query = ""
        self.lineup_sel = 0    # 当前筛选结果里的下标
        self.campaign_n = 1
        self.campaign_auto = False
        # 自定义布阵状态
        self.build_scene = 0                      # catalog.SCENES 下标
        self.build_brush = 0                      # catalog.PALETTE 下标, -1=橡皮
        self.build_cells: dict = {}               # (row,col) -> {plant/container/shell: id}
        self.import_text = ""                     # 1437/阵容码导入框
        self.build_brush_pid = 1                  # 当前画笔植物 id（-1=橡皮）
        self._palette = _build_palette()          # [(pid, zh, layer), ...] 全植物
        self.track_text = "P4: PP|PP|PP|PP (869|869|869|869)"
        self.backend_idx = 0
        self.theme_name = theme.DEFAULT_THEME
        self.window_alpha = theme.DEFAULT_WINDOW_ALPHA
        self.os_opacity = 0.92
        # 产品固有行为，非选项：无边框悬浮 + 总在最前 + 附加时自动窗口化。
        # （PVZTOOLS_GUI_OVERLAY=0 仅供调试关闭悬浮外观。）
        self.overlay_mode = os.environ.get("PVZTOOLS_GUI_OVERLAY", "1") != "0"
        self.topmost = True
        self.worker.auto_windowize = True
        self.worker.auto_background = True
        self.collapsed = False
        self._toggle_prev = False              # 快捷键边沿检测
        self._drag_off = None                  # 拖动抓取偏移
        self._resize_start = None              # 缩放抓取起点
        self._expanded_size = None             # 收起前的尺寸
        self._style_dirty = True
        self._pending: dict = {}
        self.mode_choice: Optional[str] = None  # 用户改了模式但还没重新附加
        self._frames = 0
        # 测试钩子：PVZTOOLS_GUI_AUTOCLOSE_S=秒数 -> 到时自动退出
        try:
            secs = float(os.environ.get("PVZTOOLS_GUI_AUTOCLOSE_S", "0"))
        except ValueError:
            secs = 0.0
        self._deadline = time.monotonic() + secs if secs > 0 else 0.0

    # ---------- 小工具 ----------

    def _submit(self, name: str, **kw: object) -> None:
        self.worker.submit(name, **kw)

    def _mark_pending(self, key: str, value: object) -> None:
        self._pending[key] = (value, time.monotonic())

    def _effective(self, key: str, snapshot_value: object) -> object:
        entry = self._pending.get(key)
        if entry is not None:
            value, ts = entry
            if time.monotonic() - ts < _PENDING_TTL_S:
                return value
            del self._pending[key]
        return snapshot_value

    def _apply_window_effects(self) -> None:
        """小窗即面板：无边框 + 半透明 + 总在最前 + 不抢焦点。

        不用全屏宿主（那种一旦透明失效就整屏黑）。窗口本身就是面板，
        透明失效最坏也只是个不透明小方块。
        """
        if self.overlay_mode:
            overlay.set_decorated(False)
            overlay.set_topmost(self.topmost)
            # 不用 NOACTIVATE：面板要能拿键盘焦点（阳光值/轨道/导入等输入框）。
            # 游戏失焦暂停由"后台运行"补丁兜底（附加时自动开）。
            overlay.set_no_activate(False)
            overlay.apply(True, self.os_opacity,
                          colorkey=not overlay.has_transparent_framebuffer())
        else:
            overlay.set_decorated(True)
            overlay.set_topmost(self.topmost)
            overlay.apply(False, self.os_opacity)
            overlay.set_opacity(self.os_opacity)
        self._style_dirty = True

    def post_init(self) -> None:
        self._apply_window_effects()

    # ---------- 渲染 ----------

    def gui(self) -> None:
        self._frames += 1
        if self._frames == 5:
            # 窗口已真正显示后补一次置顶/透明(建窗早期设置可能被重排)
            self._apply_window_effects()
        elif self.topmost and self._frames % 30 == 0:
            overlay.set_topmost(True)  # 焦点变化后重申最前(学自独立 overlay)
        if self._deadline and time.monotonic() > self._deadline:
            hello_imgui.get_runner_params().app_shall_exit = True
        if self._style_dirty:
            # 面板半透明由不透明度滑条控制；抠色回退时面板必须不透明。
            alpha = self.os_opacity if self.overlay_mode else self.window_alpha
            if self.overlay_mode and not overlay.has_transparent_framebuffer():
                alpha = 1.0
            theme.apply(imgui, self.theme_name, window_alpha=alpha)
            try:
                # 清屏色：悬浮=逐像素透明(圆角外看不见)/抠色回退用幕布色；
                # 普通窗口=主题背景。绝不用不透明黑，避免透明失效时整片发黑。
                if self.overlay_mode:
                    bg = (0.0, 0.0, 0.0, 0.0) if overlay.has_transparent_framebuffer() \
                        else overlay.KEY_CLEAR
                else:
                    c = theme.THEMES[self.theme_name]["window_bg"]
                    bg = (c[0], c[1], c[2], 1.0)
                hello_imgui.get_runner_params().imgui_window_params.background_color = (
                    imgui.ImVec4(*bg)
                )
            except Exception:  # noqa: BLE001
                pass
            self._style_dirty = False
        snap = self.worker.snapshot
        # INSERT 收起/展开（边沿触发）
        down = overlay.key_pressed(overlay.TOGGLE_VK)
        if down and not self._toggle_prev:
            self._set_collapsed(not self.collapsed)
        self._toggle_prev = down

        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos((vp.pos.x, vp.pos.y))
        imgui.set_next_window_size((vp.size.x, vp.size.y))
        flags = (
            imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.no_resize
            | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_collapse
            | imgui.WindowFlags_.no_saved_settings
        )
        imgui.begin("##pvztools_main", None, flags)
        self._titlebar()
        if not self.collapsed:
            self._header(snap)
            imgui.separator()
            if imgui.begin_tab_bar("##tabs"):
                self._tab_mod(snap)
                self._tab_quick(snap)
                self._tab_build(snap)
                self._tab_track(snap)
                self._tab_info(snap)
                self._tab_log()
                self._tab_settings(snap)
                imgui.end_tab_bar()
            self._resize_grip()
        imgui.end()

    def _titlebar(self) -> None:
        """标题栏：抓取偏移拖动（无重影）+ 收起/关闭按钮（不与拖动区重叠）。"""
        avail = imgui.get_content_region_avail().x
        p0 = imgui.get_cursor_screen_pos()
        # 拖动区只占左侧，右侧留给按钮，避免点击被吃
        imgui.invisible_button("##titlebar", (max(avail - 56.0, 10.0), 22.0))
        if self.overlay_mode:
            if imgui.is_item_activated():
                cur, wpos = overlay.get_cursor_pos(), overlay.get_window_pos()
                self._drag_off = (
                    (wpos[0] - cur[0], wpos[1] - cur[1]) if cur and wpos else None
                )
            if imgui.is_item_active() and self._drag_off:
                cur = overlay.get_cursor_pos()
                if cur:
                    overlay.set_window_pos(cur[0] + self._drag_off[0],
                                           cur[1] + self._drag_off[1])
        # 标题文字叠在拖动区上
        imgui.set_cursor_screen_pos((p0.x + 2, p0.y + 3))
        imgui.text_disabled(f"pvztools v{__version__}")
        # 右侧按钮（在拖动区之外）
        imgui.set_cursor_screen_pos((p0.x + avail - 52.0, p0.y))
        if imgui.small_button(("v" if self.collapsed else "^") + "##collapse"):
            self._set_collapsed(not self.collapsed)
        imgui.same_line(0.0, 2.0)
        if imgui.small_button("X##close"):
            hello_imgui.get_runner_params().app_shall_exit = True

    def _resize_grip(self) -> None:
        """右下角缩放手柄：抓取偏移（无重影）。"""
        gw = 16.0
        wpos = imgui.get_window_pos()
        wsize = imgui.get_window_size()
        x, y = wpos.x + wsize.x - gw, wpos.y + wsize.y - gw
        imgui.set_cursor_screen_pos((x, y))
        imgui.invisible_button("##resize", (gw, gw))
        hovered = imgui.is_item_hovered()
        if imgui.is_item_activated():
            cur, wsz = overlay.get_cursor_pos(), overlay.get_window_size()
            self._resize_start = (cur, wsz) if cur and wsz else None
        if imgui.is_item_active() and self._resize_start:
            (cx, cy), (ow, oh) = self._resize_start
            cur = overlay.get_cursor_pos()
            if cur:
                overlay.set_window_size(ow + (cur[0] - cx), oh + (cur[1] - cy))
        dl = imgui.get_window_draw_list()
        col = imgui.get_color_u32((0.5, 0.5, 0.5, 0.9 if hovered else 0.5))
        for k in (4.0, 8.0, 12.0):
            dl.add_line((x + gw - k, y + gw - 2), (x + gw - 2, y + gw - k), col, 1.5)

    def _set_collapsed(self, value: bool) -> None:
        if value == self.collapsed:
            return
        self.collapsed = value
        if value:
            self._expanded_size = overlay.get_window_size()
            sz = overlay.get_window_size()
            overlay.set_window_size(int(sz[0]) if sz else 540, 40)
        elif self._expanded_size:
            overlay.set_window_size(*self._expanded_size)

    def _header(self, snap: Snapshot) -> None:
        if snap.attached:
            mode = tr("mode_full") if snap.mode == "full" else tr("mode_readonly")
            imgui.text_colored(
                _GREEN,
                f"{tr('status_attached')} · {snap.version} · {snap.capability} · {mode}",
            )
        elif snap.candidates:
            imgui.text_colored(_ORANGE, tr("status_multiple"))
            for pid in snap.candidates:
                if imgui.small_button(f"pid {pid}"):
                    self._submit("attach", pid=pid)
        else:
            imgui.text_colored(_ORANGE, tr("status_detached"))
        if snap.attached:
            if snap.in_level:
                pause = f" · {tr('paused')}" if snap.paused else ""
                imgui.text(
                    f"{tr('sun')} {snap.sun} · {tr('wave')} {snap.wave}/{snap.total_waves}"
                    f" · {tr('clock')} {snap.clock}cs{pause}"
                )
            else:
                imgui.text_disabled(f"{snap.ui_name} · {tr('not_in_level')}")
        if snap.busy:
            imgui.text_colored(_ORANGE, f"{tr('status_busy')}: {snap.busy}")
            if snap.progress:
                imgui.text_disabled(snap.progress)
            if imgui.button(tr("stop")):
                self.worker.stop_current()
            imgui.same_line()
            imgui.text_disabled(tr("stop_hint"))
        if snap.error:
            imgui.text_colored(_RED, snap.error)

    def _tab_mod(self, snap: Snapshot) -> None:
        selected, _ = imgui.begin_tab_item(tr("tab_mod") + "###tab_mod")
        if not selected:
            return
        writable = snap.attached and snap.mode == "full" and not snap.busy
        imgui.begin_disabled(not writable)
        _, self.sun_input = imgui.input_int(tr("sun"), self.sun_input, 50)
        imgui.same_line()
        if imgui.button(tr("write")):
            self._submit("set_sun", value=self.sun_input)
        for key in ("no_cooldown", "free_planting", "sun_limit", "auto_collect", "background"):
            current = bool(self._effective(key, snap.cheats.get(key, False)))
            changed, value = imgui.checkbox(tr(key), current)
            if changed:
                self._mark_pending(key, value)
                self._submit("toggle", name=key, value=value)
        speed = float(self._effective("speed", snap.cheats.get("speed", 1.0) or 1.0))
        changed, speed = imgui.slider_float(tr("speed"), speed, 0.2, 10.0, "%.1fx")
        if changed:
            self._mark_pending("speed", speed)
        if imgui.is_item_deactivated_after_edit():
            self._mark_pending("speed", speed)
            self._submit("speed", value=speed)
        if imgui.button(tr("kill_zombies")):
            self._submit("kill_zombies")
        imgui.same_line()
        if imgui.button(tr("collect_now")):
            self._submit("collect_now")
        imgui.end_disabled()
        if snap.attached and snap.mode != "full":
            imgui.text_disabled(tr("need_full"))
        elif not snap.attached:
            imgui.text_disabled(tr("need_attach"))
        imgui.end_tab_item()

    def _tab_quick(self, snap: Snapshot) -> None:
        selected, _ = imgui.begin_tab_item(tr("tab_quick") + "###tab_quick")
        if not selected:
            return
        ready = snap.attached and snap.mode == "full" and not snap.busy
        imgui.begin_disabled(not ready)
        # 场景筛选 + 搜索 + 全库阵型（363 个，按编号放置以避开重名）
        scene_opts = [tr("scene_all")] + [
            f"{s}·{catalog.SCENE_ZH.get(s, s)}" for s in catalog.SCENES
        ]
        changed, self.scene_filter = imgui.combo(
            tr("scene"), self.scene_filter, scene_opts
        )
        if changed:
            self.lineup_sel = 0
        changed, self.lineup_query = imgui.input_text(tr("search"), self.lineup_query)
        if changed:
            self.lineup_sel = 0
        scene = None if self.scene_filter == 0 else catalog.SCENES[self.scene_filter - 1]
        shown = catalog.filter_catalog(self.catalog, scene, self.lineup_query)
        labels = [it.label for it in shown] or [tr("lineup_none")]
        if self.lineup_sel >= len(shown):
            self.lineup_sel = 0
        imgui.set_next_item_width(-1)
        _, self.lineup_sel = imgui.combo(
            "##lineup", self.lineup_sel, labels, 20
        )
        imgui.text_disabled(tr("lineup_count").format(n=len(shown), total=len(self.catalog)))
        cur = shown[self.lineup_sel] if shown else None
        imgui.begin_disabled(cur is None)
        if imgui.button(tr("place_lineup"), (-1, 0)) and cur is not None:
            self._submit("place_lineup", index=cur.index)
        if imgui.button(tr("play_level"), (-1, 0)) and cur is not None:
            self._submit("play_level", index=cur.index)
        imgui.end_disabled()
        imgui.separator()
        _, self.campaign_n = imgui.input_int(tr("campaign_n"), self.campaign_n)
        self.campaign_n = max(1, self.campaign_n)
        _, self.campaign_auto = imgui.checkbox(tr("campaign_auto"), self.campaign_auto)
        if imgui.button(tr("campaign_run"), (-1, 0)):
            self._submit("campaign", n=self.campaign_n, auto=self.campaign_auto)
        imgui.end_disabled()
        if not ready and snap.attached and snap.busy:
            imgui.text_disabled(f"{tr('status_busy')}…")
        elif snap.attached and snap.mode != "full":
            imgui.text_disabled(tr("need_full"))
        elif not snap.attached:
            imgui.text_disabled(tr("need_attach"))
        imgui.end_tab_item()

    def _tab_build(self, snap: Snapshot) -> None:
        selected, _ = imgui.begin_tab_item(tr("tab_build") + "###tab_build")
        if not selected:
            return
        scene = catalog.SCENES[self.build_scene]
        _, self.build_scene = imgui.combo(
            tr("build_scene"),
            self.build_scene,
            [f"{s}·{catalog.SCENE_ZH.get(s, s)}" for s in catalog.SCENES],
        )
        scene = catalog.SCENES[self.build_scene]
        # 导入：粘贴 1437 文本 / 阵容码 → 直接复刻
        if imgui.collapsing_header(tr("import_title")):
            imgui.text_disabled(tr("import_hint"))
            _, self.import_text = imgui.input_text_multiline(
                "##import", self.import_text, (-1, 80)
            )
            ready = snap.attached and snap.mode == "full" and not snap.busy
            imgui.begin_disabled(not ready or not self.import_text.strip())
            if imgui.button(tr("import_place")):
                self._submit("import_lineup", text=self.import_text, scene=scene)
            imgui.end_disabled()
            imgui.separator()
        # 植物图未下载时给下载按钮
        cached = sprites.cached_count()
        total = len(sprites.all_ids())
        if cached < total:
            imgui.begin_disabled(snap.busy == "download_sprites")
            if imgui.button(tr("build_download").format(n=cached, total=total), (-1, 0)):
                self._submit("download_sprites")
            imgui.end_disabled()
        imgui.text_disabled(tr("build_hint"))
        # 画笔调色板（全植物图片）
        imgui.text(tr("build_brush") + ":")
        cols_pp = 8
        for i, (pid, zh, _layer) in enumerate(self._palette):
            if i % cols_pp != 0:
                imgui.same_line()
            self._palette_button(pid, zh)
        if imgui.button(tr("build_eraser") + "##eraser", (40, 40)):
            self.build_brush_pid = -1
        if self.build_brush_pid == -1:
            imgui.same_line()
            imgui.text_colored(_ORANGE, tr("build_eraser"))
        imgui.separator()
        # 格子网格：rows × 9
        rows = catalog.scene_rows(scene)
        for r in range(1, rows + 1):
            for c in range(1, 10):
                if c != 1:
                    imgui.same_line(0.0, 3.0)
                self._build_cell(r, c, scene)
        imgui.separator()
        n = sum(1 for v in self.build_cells.values() if not v.get("cob_right"))
        imgui.text_disabled(tr("build_count").format(n=n) if n else tr("build_empty"))
        ready = snap.attached and snap.mode == "full" and not snap.busy
        imgui.begin_disabled(not ready or n == 0)
        if imgui.button(tr("build_place"), (-1, 0)):
            self._submit("place_custom", scene=scene, cells=self._export_cells())
        imgui.end_disabled()
        if imgui.button(tr("build_clear")):
            self.build_cells.clear()
        if snap.attached and snap.mode != "full":
            imgui.text_disabled(tr("need_full"))
        elif not snap.attached:
            imgui.text_disabled(tr("need_attach"))
        imgui.end_tab_item()

    def _export_cells(self) -> list:
        """把 build_cells 导出为放阵用的格子列表。

        加农炮存在左格（模型自己处理占两格）；右格标记若带花盆(屋顶)则
        导出一个只有花盆的格子，否则跳过。
        """
        out = []
        for (r, c), layers in sorted(self.build_cells.items()):
            if layers.get("cob_right"):
                if layers.get("container") is not None:
                    out.append({"row": r, "col": c, "container": layers["container"]})
                continue
            cell = {k: v for k, v in layers.items() if k in ("plant", "container", "shell")}
            if cell:
                out.append({"row": r, "col": c, **cell})
        return out

    def _plant_tex(self, pid: int):
        """(tex_ref, size) 或 None（None=未缓存，用文字兜底）。"""
        t = sprites.texture(imgui_bundle, pid)
        if t is None:
            return None
        return imgui.ImTextureRef(t[0]), t[1]

    def _palette_button(self, pid: int, zh: str, sz: float = 40.0) -> None:
        imgui.push_id(f"pal{pid}")
        active = self.build_brush_pid == pid
        t = self._plant_tex(pid)
        clicked = False
        if active:
            imgui.push_style_color(imgui.Col_.button, _GREEN)
        if t is not None:
            clicked = imgui.image_button("##b", t[0], imgui.ImVec2(sz, sz))
        else:
            clicked = imgui.button(zh[:2], imgui.ImVec2(sz, sz))
        if active:
            imgui.pop_style_color()
        if imgui.is_item_hovered():
            imgui.set_tooltip(zh)
        if clicked:
            self.build_brush_pid = pid
        imgui.pop_id()

    def _build_cell(self, row: int, col: int, scene: str, sz: float = 46.0) -> None:
        key = (row, col)
        layers = self.build_cells.get(key)
        imgui.push_id(f"cell{row}_{col}")
        p0 = imgui.get_cursor_screen_pos()
        clicked = imgui.invisible_button("##c", imgui.ImVec2(sz, sz))
        dl = imgui.get_window_draw_list()
        dl.add_rect_filled(imgui.ImVec2(p0.x, p0.y), imgui.ImVec2(p0.x + sz, p0.y + sz),
                           imgui.get_color_u32((0.0, 0.0, 0.0, 0.15)), 4.0)
        dl.add_rect(imgui.ImVec2(p0.x, p0.y), imgui.ImVec2(p0.x + sz, p0.y + sz),
                    imgui.get_color_u32((0.4, 0.55, 0.42, 0.6)), 4.0)
        # 加农炮右半格：不单独画植物（左格的炮会横跨过来）
        is_cob_right = bool(layers and layers.get("cob_right"))
        if layers and not is_cob_right:
            main_pid = layers.get("plant") or layers.get("container") or layers.get("shell")
            # 加农炮横跨两格
            span = 2 * sz + 3 if layers.get("plant") == _COB else sz
            if main_pid is not None:
                t = self._plant_tex(main_pid)
                if t is not None:
                    dl.add_image(t[0], imgui.ImVec2(p0.x + 2, p0.y + 2),
                                 imgui.ImVec2(p0.x + span - 2, p0.y + sz - 2))
                else:
                    zh = next((z for pp, z, _l in self._palette if pp == main_pid), "?")
                    dl.add_text(imgui.ImVec2(p0.x + 3, p0.y + 14),
                                imgui.get_color_u32((0.9, 0.9, 0.9, 1.0)), zh[:2])
            b = sz * 0.42
            if layers.get("container") is not None and layers.get("plant") is not None:
                ct = self._plant_tex(layers["container"])
                if ct is not None:
                    dl.add_image(ct[0], imgui.ImVec2(p0.x + 1, p0.y + sz - b - 1),
                                 imgui.ImVec2(p0.x + 1 + b, p0.y + sz - 1))
            if layers.get("shell") is not None and layers.get("plant") is not None:
                st = self._plant_tex(layers["shell"])
                if st is not None:
                    dl.add_image(st[0], imgui.ImVec2(p0.x + sz - b - 1, p0.y + 1),
                                 imgui.ImVec2(p0.x + sz - 1, p0.y + 1 + b))
        if clicked:
            self._paint_cell(row, col, scene)
        if imgui.is_item_clicked(1):  # 右键清格
            self._erase_cell(row, col)
        imgui.pop_id()

    def _erase_cell(self, row: int, col: int) -> None:
        """清格；若该格是加农炮的一半，连带清另一半。"""
        cell = self.build_cells.get((row, col))
        if cell:
            if cell.get("plant") == _COB:
                self.build_cells.pop((row, col + 1), None)
            elif cell.get("cob_right"):
                self.build_cells.pop(cell["cob_right"], None)
        self.build_cells.pop((row, col), None)

    def _paint_cell(self, row: int, col: int, scene: str) -> None:
        brush = self.build_brush_pid
        if brush == -1:
            self._erase_cell(row, col)
            return
        layer = sprites.PLANT_SPRITES.get(brush, (None, 0, 0, "plant"))[3]
        water = row in _WATER_ROWS.get(scene, ())
        roof = scene in _POT_SCENES
        # 加农炮：占两格（左格存炮，右格标记）；屋顶两格各配花盆
        if brush == _COB:
            if col >= 9:
                return  # 最后一列放不下两格
            self._erase_cell(row, col)
            self._erase_cell(row, col + 1)
            left = {"plant": _COB}
            right = {"cob_right": (row, col)}
            if roof:
                left["container"] = _POT
                right["container"] = _POT
            self.build_cells[(row, col)] = left
            self.build_cells[(row, col + 1)] = right
            return
        # 南瓜：不能套加农炮
        if brush == _PUMPKIN:
            tgt = self.build_cells.get((row, col))
            if tgt and tgt.get("plant") == _COB:
                return
        # 落在加农炮的格子上：先清掉整门炮
        tgt = self.build_cells.get((row, col))
        if tgt and (tgt.get("plant") == _COB or tgt.get("cob_right")):
            self._erase_cell(row, col)
        cell = self.build_cells.setdefault((row, col), {})
        cell.pop("cob_right", None)
        cell[layer] = brush
        # 水路陆生自动睡莲 / 屋顶自动花盆
        if layer == "plant" and brush not in _AQUATIC:
            if water and "container" not in cell:
                cell["container"] = _LILY
            elif roof and "container" not in cell:
                cell["container"] = _POT

    def _tab_track(self, snap: Snapshot) -> None:
        selected, _ = imgui.begin_tab_item(tr("tab_track") + "###tab_track")
        if not selected:
            return
        imgui.text_disabled(tr("track_hint"))
        _, self.track_text = imgui.input_text_multiline(
            "##track", self.track_text, (-1, 90)
        )
        backends = ["input", "remote"]
        _, self.backend_idx = imgui.combo(tr("backend"), self.backend_idx, backends)
        can_check = snap.attached and not snap.busy
        can_run = can_check and snap.mode == "full"
        imgui.begin_disabled(not can_check)
        if imgui.button(tr("track_check")):
            self._submit(
                "run_track",
                text=self.track_text,
                backend=backends[self.backend_idx],
                dry_run=True,
            )
        imgui.end_disabled()
        imgui.same_line()
        imgui.begin_disabled(not can_run)
        if imgui.button(tr("track_run")):
            self._submit(
                "run_track",
                text=self.track_text,
                backend=backends[self.backend_idx],
                dry_run=False,
            )
        imgui.end_disabled()
        if snap.attached and snap.mode != "full":
            imgui.text_disabled(tr("need_full"))
        imgui.end_tab_item()

    def _tab_info(self, snap: Snapshot) -> None:
        selected, _ = imgui.begin_tab_item(tr("tab_info") + "###tab_info")
        if not selected:
            return
        imgui.text_disabled(tr("info_intro"))
        if not snap.addresses:
            imgui.text_disabled(tr("info_none"))
            imgui.end_tab_item()
            return
        flags = (
            imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.sizing_stretch_prop
        )
        if imgui.begin_table("##addrs", 3, flags):
            imgui.table_setup_column(tr("info_col_name"), imgui.TableColumnFlags_.width_fixed, 118.0)
            imgui.table_setup_column(tr("info_col_chain"))
            imgui.table_setup_column(tr("info_col_addr"), imgui.TableColumnFlags_.width_fixed, 120.0)
            imgui.table_headers_row()
            for name, chain, resolved in snap.addresses:
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text(name)
                imgui.table_next_column()
                imgui.text_wrapped(chain)
                imgui.table_next_column()
                imgui.text(resolved)
            imgui.end_table()
        imgui.end_tab_item()

    def _tab_log(self) -> None:
        selected, _ = imgui.begin_tab_item(tr("tab_log") + "###tab_log")
        if not selected:
            return
        if imgui.small_button(tr("clear_log")):
            self.worker.clear_log()
        imgui.begin_child("##log", (0, 0))
        lines = self.worker.log_lines()
        if not lines:
            imgui.text_disabled(tr("log_empty"))
        for ts, msg in lines[-200:]:
            imgui.text_disabled(time.strftime("%H:%M:%S", time.localtime(ts)))
            imgui.same_line()
            imgui.text_wrapped(msg)
        if imgui.get_scroll_y() >= imgui.get_scroll_max_y() - 1:
            imgui.set_scroll_here_y(1.0)
        imgui.end_child()
        imgui.end_tab_item()

    def _tab_settings(self, snap: Snapshot) -> None:
        selected, _ = imgui.begin_tab_item(tr("tab_settings") + "###tab_settings")
        if not selected:
            return
        # 第一行：进程选择（多个 PvZ 进程时挑一个附加）
        procs = list(snap.processes)
        if procs:
            labels = [f"{pid} · {name}" for pid, name in procs]
            cur_pid = snap.pid if snap.attached else 0
            sel = next((i for i, (pid, _n) in enumerate(procs) if pid == cur_pid), 0)
            changed, sel = imgui.combo(tr("process"), sel, labels)
            imgui.same_line()
            if imgui.button(tr("process_attach")):
                self._submit("attach", pid=procs[sel][0])
            elif changed and procs[sel][0] != cur_pid:
                self._submit("attach", pid=procs[sel][0])
        else:
            imgui.text_disabled(tr("process") + ": " + tr("process_none"))
        imgui.separator()
        modes = [tr("mode_full"), tr("mode_readonly")]
        current_mode = self.mode_choice or snap.mode
        mode_idx = 0 if current_mode == "full" else 1
        changed, mode_idx = imgui.combo(tr("mode_label"), mode_idx, modes)
        if changed:
            self.mode_choice = "full" if mode_idx == 0 else "readonly"
            self._submit("set_mode", mode=self.mode_choice)
        if self.mode_choice and snap.attached and self.mode_choice == snap.mode:
            self.mode_choice = None  # 已按所选模式附加,回归跟随快照
        if imgui.button(tr("reattach")):
            self._submit("attach")
        imgui.same_line()
        if imgui.button(tr("detach")):
            self._submit("detach")
        imgui.begin_disabled(not snap.attached or bool(snap.busy))
        if imgui.button(tr("to_front")):
            self._submit("to_front")
        imgui.end_disabled()
        imgui.separator()
        themes = [tr("theme_light"), tr("theme_dark")]
        theme_idx = 0 if self.theme_name == "light" else 1
        changed, theme_idx = imgui.combo(tr("theme"), theme_idx, themes)
        if changed:
            self.theme_name = "light" if theme_idx == 0 else "dark"
            self._style_dirty = True
        # 悬浮窗 / 总在最前 / 自动窗口化是产品固有行为，非可选项。
        imgui.text_disabled(tr("overlay_hint"))
        changed, self.os_opacity = imgui.slider_float(
            tr("opacity"), self.os_opacity, 0.5, 1.0, "%.2f"
        )
        if changed:
            self._apply_window_effects()
        langs = ["中文", "English"]
        lang_idx = 0 if get_language() == "zh" else 1
        changed, lang_idx = imgui.combo(tr("language"), lang_idx, langs)
        if changed:
            set_language("zh" if lang_idx == 0 else "en")
        imgui.separator()
        imgui.text_wrapped(tr("safety_note"))
        imgui.end_tab_item()


def _load_fonts() -> None:
    io = imgui.get_io()
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            io.fonts.add_font_from_file_ttf(path, 19.0)
            return


def run_gui(argv: Optional[List[str]] = None) -> int:
    # 单实例：先关掉已存在的 pvztools 窗口（优雅退出释放控制器互斥）
    try:
        overlay.kill_other_instances()
    except Exception:  # noqa: BLE001
        pass
    overlay.request_transparent_framebuffer()
    worker = GameWorker()
    worker.start()
    app = App(worker)
    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "pvztools"
    params.app_window_params.window_geometry.size = (560, 680)
    try:
        params.imgui_window_params.default_imgui_window_type = (
            hello_imgui.DefaultImGuiWindowType.no_default_window
        )
    except Exception:  # noqa: BLE001
        pass
    params.callbacks.show_gui = app.gui
    params.callbacks.load_additional_fonts = _load_fonts
    params.callbacks.post_init = app.post_init
    try:
        hello_imgui.run(params)
    finally:
        worker.shutdown()
    return 0
