"""ImGui 主题：森林绿两套配色。

深色版取自 Dear ImGui 官方样式分享帖 ocornut/imgui#707 中
TheAncientOwl 的 "ForestGreen" 主题（该帖为公开样式分享帖）；
浅色"淡绿"版为本项目自制变体。半透明与圆角在 apply() 中统一处理。
"""

from __future__ import annotations

from typing import Dict, Tuple

Color = Tuple[float, float, float, float]

# ocornut/imgui#707 "ForestGreen"（深绿）
FOREST_DARK: Dict[str, Color] = {
    "text": (0.85, 0.9, 0.85, 1.0),
    "text_disabled": (0.5, 0.55, 0.5, 1.0),
    "window_bg": (0.06, 0.09, 0.06, 1.0),
    "child_bg": (0.08, 0.11, 0.08, 1.0),
    "popup_bg": (0.07, 0.1, 0.07, 0.96),
    "border": (0.18, 0.28, 0.18, 0.8),
    "border_shadow": (0.0, 0.0, 0.0, 0.0),
    "frame_bg": (0.12, 0.18, 0.12, 1.0),
    "frame_bg_hovered": (0.18, 0.3, 0.18, 1.0),
    "frame_bg_active": (0.24, 0.42, 0.24, 1.0),
    "title_bg": (0.09, 0.14, 0.09, 1.0),
    "title_bg_active": (0.14, 0.26, 0.14, 1.0),
    "title_bg_collapsed": (0.05, 0.08, 0.05, 1.0),
    "menu_bar_bg": (0.09, 0.14, 0.09, 1.0),
    "scrollbar_bg": (0.05, 0.08, 0.05, 1.0),
    "scrollbar_grab": (0.18, 0.28, 0.18, 1.0),
    "scrollbar_grab_hovered": (0.25, 0.38, 0.25, 1.0),
    "scrollbar_grab_active": (0.32, 0.48, 0.32, 1.0),
    "check_mark": (0.45, 0.75, 0.45, 1.0),
    "slider_grab": (0.35, 0.55, 0.35, 1.0),
    "slider_grab_active": (0.45, 0.7, 0.45, 1.0),
    "button": (0.18, 0.35, 0.18, 1.0),
    "button_hovered": (0.25, 0.45, 0.25, 1.0),
    "button_active": (0.32, 0.55, 0.32, 1.0),
    "header": (0.18, 0.35, 0.18, 1.0),
    "header_hovered": (0.25, 0.45, 0.25, 1.0),
    "header_active": (0.32, 0.55, 0.32, 1.0),
    "separator": (0.18, 0.28, 0.18, 1.0),
    "separator_hovered": (0.25, 0.45, 0.25, 1.0),
    "separator_active": (0.32, 0.55, 0.32, 1.0),
    "resize_grip": (0.18, 0.35, 0.18, 0.8),
    "resize_grip_hovered": (0.25, 0.45, 0.25, 1.0),
    "resize_grip_active": (0.32, 0.55, 0.32, 1.0),
    "tab": (0.12, 0.22, 0.12, 1.0),
    "tab_hovered": (0.25, 0.45, 0.25, 1.0),
    "tab_selected": (0.2, 0.38, 0.2, 1.0),
    "tab_dimmed": (0.08, 0.15, 0.08, 1.0),
    "tab_dimmed_selected": (0.12, 0.22, 0.12, 1.0),
    "plot_lines": (0.4, 0.7, 0.4, 1.0),
    "plot_lines_hovered": (0.5, 0.85, 0.5, 1.0),
    "plot_histogram": (0.4, 0.7, 0.4, 1.0),
    "plot_histogram_hovered": (0.5, 0.85, 0.5, 1.0),
    "table_header_bg": (0.12, 0.22, 0.12, 1.0),
    "table_border_strong": (0.2, 0.35, 0.2, 1.0),
    "table_border_light": (0.15, 0.25, 0.15, 1.0),
    "table_row_bg": (0.0, 0.0, 0.0, 0.0),
    "table_row_bg_alt": (0.08, 0.14, 0.08, 0.5),
    "text_selected_bg": (0.25, 0.55, 0.25, 0.5),
    "drag_drop_target": (0.6, 0.9, 0.6, 1.0),
    "nav_cursor": (0.4, 0.8, 0.4, 1.0),
    "nav_windowing_highlight": (0.85, 0.9, 0.85, 0.7),
    "nav_windowing_dim_bg": (0.1, 0.15, 0.1, 0.5),
    "modal_window_dim_bg": (0.05, 0.08, 0.05, 0.6),
    "docking_preview": (0.25, 0.55, 0.25, 0.5),
    "docking_empty_bg": (0.06, 0.09, 0.06, 1.0),
}

# 淡绿浅色变体（本项目自制）；点缀色为 forest green #228B22
_FG = (0.13, 0.55, 0.13)

FOREST_LIGHT: Dict[str, Color] = {
    "text": (0.09, 0.17, 0.11, 1.0),
    "text_disabled": (0.45, 0.55, 0.47, 1.0),
    "window_bg": (0.93, 0.97, 0.93, 1.0),
    "child_bg": (0.90, 0.95, 0.90, 0.60),
    "popup_bg": (0.95, 0.98, 0.95, 0.98),
    "border": (0.55, 0.72, 0.57, 0.60),
    "border_shadow": (0.0, 0.0, 0.0, 0.0),
    "frame_bg": (0.82, 0.91, 0.83, 1.0),
    "frame_bg_hovered": (0.74, 0.88, 0.76, 1.0),
    "frame_bg_active": (0.67, 0.84, 0.70, 1.0),
    "title_bg": (0.78, 0.90, 0.80, 1.0),
    "title_bg_active": (0.70, 0.86, 0.73, 1.0),
    "title_bg_collapsed": (0.82, 0.91, 0.83, 0.75),
    "menu_bar_bg": (0.86, 0.93, 0.87, 1.0),
    "scrollbar_bg": (0.88, 0.94, 0.89, 0.6),
    "scrollbar_grab": (0.60, 0.78, 0.63, 1.0),
    "scrollbar_grab_hovered": (0.48, 0.70, 0.52, 1.0),
    "scrollbar_grab_active": _FG + (1.0,),
    "check_mark": _FG + (1.0,),
    "slider_grab": _FG + (0.9,),
    "slider_grab_active": _FG + (1.0,),
    "button": (0.76, 0.89, 0.78, 1.0),
    "button_hovered": (0.62, 0.82, 0.66, 1.0),
    "button_active": _FG + (0.85,),
    "header": (0.72, 0.87, 0.75, 0.85),
    "header_hovered": (0.62, 0.82, 0.66, 1.0),
    "header_active": (0.55, 0.78, 0.60, 1.0),
    "separator": (0.60, 0.76, 0.62, 0.7),
    "separator_hovered": _FG + (0.8,),
    "separator_active": _FG + (1.0,),
    "resize_grip": (0.60, 0.78, 0.63, 0.6),
    "resize_grip_hovered": _FG + (0.8,),
    "resize_grip_active": _FG + (1.0,),
    "tab": (0.80, 0.90, 0.81, 1.0),
    "tab_hovered": (0.66, 0.84, 0.70, 1.0),
    "tab_selected": (0.93, 0.97, 0.93, 1.0),
    "table_header_bg": (0.82, 0.91, 0.83, 1.0),
    "table_border_strong": (0.60, 0.76, 0.62, 1.0),
    "table_border_light": (0.72, 0.85, 0.74, 1.0),
    "table_row_bg_alt": (0.88, 0.94, 0.89, 0.5),
    "text_selected_bg": _FG + (0.30,),
    "plot_lines": _FG + (0.9,),
    "plot_lines_hovered": _FG + (1.0,),
    "plot_histogram": _FG + (0.9,),
    "plot_histogram_hovered": _FG + (1.0,),
    "drag_drop_target": _FG + (0.9,),
    "nav_cursor": _FG + (0.8,),
}

THEMES: Dict[str, Dict[str, Color]] = {
    "dark": FOREST_DARK,
    "light": FOREST_LIGHT,
}

DEFAULT_THEME = "light"
DEFAULT_WINDOW_ALPHA = 0.92


def apply(imgui, name: str = DEFAULT_THEME, *, window_alpha: float = DEFAULT_WINDOW_ALPHA) -> None:
    """把主题应用到当前 ImGui 上下文（圆角 + 半透明统一在这里）。"""
    style = imgui.get_style()
    # 先重置到内置基线再覆盖:两套调色板键集不同,避免切换主题时旧色残留
    try:
        if name == "dark":
            imgui.style_colors_dark(style)
        else:
            imgui.style_colors_light(style)
    except Exception:  # noqa: BLE001
        pass
    style.window_padding = (12, 12)
    style.frame_padding = (8, 5)
    style.cell_padding = (6, 4)
    style.item_spacing = (8, 7)
    style.item_inner_spacing = (6, 4)
    style.scrollbar_size = 14.0
    style.grab_min_size = 12.0
    style.window_rounding = 10.0
    style.child_rounding = 6.0
    style.frame_rounding = 6.0
    style.popup_rounding = 6.0
    style.scrollbar_rounding = 8.0
    style.grab_rounding = 6.0
    style.tab_rounding = 6.0
    style.window_border_size = 1.0
    style.frame_border_size = 1.0
    palette = THEMES.get(name, FOREST_LIGHT)
    for attr, rgba in palette.items():
        try:
            idx = getattr(imgui.Col_, attr)
        except AttributeError:
            continue
        if attr == "window_bg":
            rgba = (rgba[0], rgba[1], rgba[2], window_alpha)
        style.set_color_(idx, imgui.ImVec4(*rgba))
