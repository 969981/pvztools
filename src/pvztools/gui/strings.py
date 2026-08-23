"""GUI 界面文案（中英双语）。

语言跟随 pvztools.i18n（PVZTOOLS_LANG=zh|en），GUI 里也可切换。
"""

from __future__ import annotations

from typing import Dict

from pvztools.i18n import get_language

GUI_TEXT: Dict[str, Dict[str, str]] = {
    "app_title": {"zh": "pvztools", "en": "pvztools"},
    "status": {"zh": "状态", "en": "Status"},
    "status_detached": {"zh": "未检测到游戏，自动搜索中…", "en": "Game not found, scanning…"},
    "status_attached": {"zh": "已连接", "en": "Attached"},
    "status_busy": {"zh": "执行中", "en": "Running"},
    "status_multiple": {"zh": "找到多个游戏进程，请选择", "en": "Multiple game processes, pick one"},
    "mode_readonly": {"zh": "只读观察", "en": "Read-only"},
    "mode_full": {"zh": "完整控制", "en": "Full control"},
    "mode_label": {"zh": "模式", "en": "Mode"},
    "process": {"zh": "进程", "en": "Process"},
    "process_none": {"zh": "（未检测到 PvZ 进程）", "en": "(no PvZ process found)"},
    "process_attach": {"zh": "附加所选进程", "en": "Attach selected"},
    "reattach": {"zh": "重新附加", "en": "Re-attach"},
    "detach": {"zh": "断开", "en": "Detach"},
    "attach_pid": {"zh": "附加此进程", "en": "Attach this pid"},
    "tab_mod": {"zh": "修改", "en": "Cheats"},
    "tab_quick": {"zh": "一键", "en": "Quick"},
    "tab_track": {"zh": "轨道", "en": "Track"},
    "tab_build": {"zh": "自定义", "en": "Build"},
    "tab_log": {"zh": "日志", "en": "Log"},
    "build_scene": {"zh": "场景", "en": "Scene"},
    "build_brush": {"zh": "画笔", "en": "Brush"},
    "build_eraser": {"zh": "橡皮", "en": "Eraser"},
    "build_hint": {"zh": "选画笔→点格子放置；右键格子清除该格", "en": "Pick a brush, click a cell to place; right-click clears a cell"},
    "build_download": {"zh": "下载植物图 ({n}/{total})", "en": "Download plant art ({n}/{total})"},
    "build_place": {"zh": "布到游戏", "en": "Place in game"},
    "build_clear": {"zh": "清空", "en": "Clear all"},
    "build_count": {"zh": "已放 {n} 格", "en": "{n} cells"},
    "build_empty": {"zh": "还没放任何植物", "en": "No plants placed yet"},
    "import_title": {"zh": "导入阵型（粘贴 1437 文本 / 阵容码 / 阵型串）", "en": "Import (paste 1437 text / lineup code / string)"},
    "import_hint": {"zh": "例：1 地刺王 （向日葵）...  或一段阵容码", "en": "e.g. a 1437 text block, or a lineup code"},
    "import_place": {"zh": "识别并布置", "en": "Parse and place"},
    "tab_info": {"zh": "解析", "en": "Info"},
    "info_intro": {"zh": "识别 / 扫描解出的地址，模块偏移指针链（只读）", "en": "Addresses resolved by identification / scan, as module pointer chains (read-only)"},
    "info_col_name": {"zh": "名称", "en": "Name"},
    "info_col_chain": {"zh": "指针链", "en": "Pointer chain"},
    "info_col_addr": {"zh": "解析", "en": "Resolved"},
    "info_none": {"zh": "连接游戏后显示", "en": "Shown once attached"},
    "auto_windowize": {"zh": "开悬浮时自动窗口化游戏", "en": "Auto-windowize game when overlay on"},
    "tab_settings": {"zh": "设置", "en": "Settings"},
    "sun": {"zh": "阳光", "en": "Sun"},
    "write": {"zh": "写入", "en": "Write"},
    "no_cooldown": {"zh": "无冷却", "en": "No cooldown"},
    "free_planting": {"zh": "免费种植", "en": "Free planting"},
    "sun_limit": {"zh": "阳光上限解锁", "en": "Sun cap unlock"},
    "auto_collect": {"zh": "自动收集", "en": "Auto collect"},
    "background": {"zh": "后台运行", "en": "Run in background"},
    "speed": {"zh": "加速", "en": "Speed"},
    "kill_zombies": {"zh": "清场僵尸", "en": "Kill zombies"},
    "collect_now": {"zh": "立即收取掉落", "en": "Collect drops now"},
    "lineup": {"zh": "阵型", "en": "Lineup"},
    "scene": {"zh": "场景", "en": "Scene"},
    "scene_all": {"zh": "全部场景", "en": "All scenes"},
    "search": {"zh": "搜索", "en": "Search"},
    "lineup_none": {"zh": "（无匹配阵型）", "en": "(no lineup matches)"},
    "lineup_count": {"zh": "共 {n} / {total} 个阵", "en": "{n} / {total} lineups"},
    "place_lineup": {"zh": "放置阵型", "en": "Place lineup"},
    "play_level": {"zh": "自动打这一关", "en": "Auto-play this level"},
    "campaign_n": {"zh": "推冒险关数", "en": "Campaign levels"},
    "campaign_auto": {"zh": "一直推到停", "en": "Until stopped"},
    "campaign_run": {"zh": "自动推冒险", "en": "Run campaign"},
    "stop": {"zh": "停止", "en": "Stop"},
    "stop_hint": {"zh": "在当前动作间隙生效", "en": "Takes effect between actions"},
    "track_hint": {"zh": "粘贴轨道，例：P6: PP|PP|PPSS (6|6|6)", "en": "Paste a track, e.g. P6: PP|PP|PPSS (6|6|6)"},
    "track_check": {"zh": "检查", "en": "Lint"},
    "track_run": {"zh": "运行", "en": "Run"},
    "backend": {"zh": "后端", "en": "Backend"},
    "wave": {"zh": "波次", "en": "Wave"},
    "clock": {"zh": "时钟", "en": "Clock"},
    "paused": {"zh": "已暂停", "en": "Paused"},
    "not_in_level": {"zh": "不在关卡中", "en": "Not in a level"},
    "theme": {"zh": "主题", "en": "Theme"},
    "theme_dark": {"zh": "森林绿（深）", "en": "Forest green (dark)"},
    "theme_light": {"zh": "淡绿（浅）", "en": "Pale green (light)"},
    "opacity": {"zh": "窗口不透明度", "en": "Window opacity"},
    "topmost": {"zh": "总在最前", "en": "Always on top"},
    "overlay_mode": {"zh": "悬浮窗模式（隐藏系统边框）", "en": "Overlay mode (hide window frame)"},
    "overlay_hint": {"zh": "按住空白处拖动移动面板", "en": "Drag an empty area to move the panel"},
    "language": {"zh": "语言", "en": "Language"},
    "to_front": {"zh": "游戏窗口置前", "en": "Bring game to front"},
    "need_full": {"zh": "此操作需要「完整控制」模式", "en": "This action needs full-control mode"},
    "need_attach": {"zh": "请先连接游戏", "en": "Attach to the game first"},
    "gui_missing_dep": {
        "zh": "GUI 需要额外依赖：pip install pvztools[gui]",
        "en": "The GUI needs an extra dependency: pip install pvztools[gui]",
    },
    "log_empty": {"zh": "（暂无日志）", "en": "(no log yet)"},
    "clear_log": {"zh": "清空", "en": "Clear"},
    "safety_note": {
        "zh": "默认只读；写入 / 注入仅在完整控制模式并点击对应按钮时发生",
        "en": "Read-only by default; writes/injection happen only in full-control mode on click",
    },
}


def tr(key: str) -> str:
    entry = GUI_TEXT.get(key)
    if entry is None:
        return key
    lang = get_language()
    return entry.get(lang) or entry.get("zh") or key
