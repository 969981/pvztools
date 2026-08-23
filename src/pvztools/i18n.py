"""Tiny zh / en message catalog (stdlib only).

Usage::

    from pvztools.i18n import _
    raise ValueError(_("hint_write"))
    _("unknown_plant", name="foo", max=47)

Language selection (first hit wins): :func:`set_language`, the
``PVZTOOLS_LANG`` environment variable (``zh`` / ``en``, prefixes such as
``zh_CN`` accepted), then ``LC_ALL`` / ``LC_MESSAGES`` / ``LANG``, then
``zh`` (the project's historical default; an English OS locale alone does
not switch it).  Unknown keys fall back to the key text itself, so ``_()`` is
always safe to call.
"""
from __future__ import annotations

import locale
import os
from typing import Dict, Optional

__all__ = ["MESSAGES", "LANGUAGES", "set_language", "get_language", "_", "normalize_lang"]

LANGUAGES = ("zh", "en")

MESSAGES: Dict[str, Dict[str, str]] = {
    # --- game.py hints / errors -------------------------------------------------
    "hint_write": {
        "zh": "请用 pvz.attach(write=True) 重新附加",
        "en": "re-attach with pvz.attach(write=True)",
    },
    "hint_remote": {
        "zh": "请用 pvz.attach(write=True, remote=True) 重新附加",
        "en": "re-attach with pvz.attach(write=True, remote=True)",
    },
    "hint_remote_flags": {
        "zh": "，即 allow_remote_call + allow_patch",
        "en": ", i.e. allow_remote_call + allow_patch",
    },
    "hint_input": {
        "zh": "请用 pvz.attach(input=True) 重新附加（键控：只向窗口投递鼠标消息，不写内存）",
        "en": "re-attach with pvz.attach(input=True) (input mode: only posts mouse messages to the window, no memory writes)",
    },
    "patch_action": {"zh": "补丁 {action}", "en": "patch {action}"},
    "not_in_level": {
        "zh": "请先进入关卡（现在在{ui}）",
        "en": "enter a level first (currently at {ui})",
    },
    "not_in_level_after_rock": {
        "zh": "Let's Rock 后没有进入关卡（现在在{ui}）",
        "en": "did not enter the level after Let's Rock (currently at {ui})",
    },
    "play_level_needs_ui": {
        "zh": "play_level 需要在选卡界面或关卡中（现在在{ui}）",
        "en": "play_level needs the seed chooser or a level (currently at {ui})",
    },
    "unknown_cheat": {
        "zh": "未知作弊标志 {name!r}；可用：{flags}",
        "en": "unknown cheat flag {name!r}; available: {flags}",
    },
    "speed_type": {
        "zh": "speed 必须是数字（1 = 正常，2 = 两倍速），得到 {value!r}",
        "en": "speed must be a number (1 = normal, 2 = double speed), got {value!r}",
    },
    "speed_range": {
        "zh": "speed={value!r} 超出允许范围 [{lo:g}, {hi:g}]（1 = 正常，2 = 两倍速，10 = 最快）",
        "en": "speed={value!r} outside the allowed range [{lo:g}, {hi:g}] (1 = normal, 2 = double, 10 = fastest)",
    },
    "unknown_build": {
        "zh": "不认识这个游戏版本（stamp 0x{stamp:08x}，进程 {proc}），特征扫描也没有找到关键地址",
        "en": "unrecognised game build (stamp 0x{stamp:08x}, process {proc}); the signature scan found no key addresses either",
    },
    "profile_not_found": {
        "zh": "找不到 profile {profile!r}：既不是内置 profile id，也不是可读的 JSON 文件（{err}）",
        "en": "profile {profile!r} not found: neither a built-in profile id nor a readable JSON file ({err})",
    },
    # --- names.py ---------------------------------------------------------------
    "unknown_plant": {
        "zh": "不认识的植物 {name!r}（接受中文名如 豌豆射手、英文名如 Peashooter、昵称如 曾哥、或 0..{max} 的编号）",
        "en": "unknown plant {name!r} (accepts Chinese names like 豌豆射手, English names like Peashooter, nicknames like gloom, or ids 0..{max})",
    },
    "unknown_zombie": {
        "zh": "不认识的僵尸 {name!r}（接受中文名如 红眼巨人、单字如 红、英文名如 Gargantuar、或 0..{max} 的编号）",
        "en": "unknown zombie {name!r} (accepts Chinese names like 红眼巨人, single chars like 红, English names like Gargantuar, or ids 0..{max})",
    },
    # --- CLI ----------------------------------------------------------------------
    "cli_description": {
        "zh": "PvZ 轨道 / 阵型格式工具 + 内存读取 / 修改器（默认只读）",
        "en": "PvZ track / lineup format tools + memory reader / modifier (read-only by default)",
    },
    "cli_epilog": {
        "zh": "安全模型：默认只读；写内存需 --write，代码补丁需 --patch，远程调用需 --remote，均需 --yes 确认。语言：PVZTOOLS_LANG=zh|en",
        "en": "Safety model: read-only by default; --write for memory writes, --patch for code patches, --remote for remote calls, all require --yes. Language: PVZTOOLS_LANG=zh|en",
    },
    "help_track": {"zh": "轨道 (track) 命令", "en": "track commands"},
    "help_track_run": {
        "zh": "在运行中的游戏上执行轨道（--input | --remote，--yes；--dry-run 只等待）",
        "en": "run a track on the live game (--input | --remote, --yes; --dry-run only waits)",
    },
    "help_input": {
        "zh": "键控：PostMessage 鼠标消息（Safety.allow_input；不写内存）",
        "en": "input mode: PostMessage mouse messages (Safety.allow_input; no memory writes)",
    },
    "help_remote": {
        "zh": "修改器：远程调用（隐含 --write --patch）",
        "en": "modifier: remote calls (implies --write --patch)",
    },
    "help_remote_inject": {
        "zh": "允许远程调用 / 代码注入（隐含 --write --patch）",
        "en": "allow remote calls / code injection (implies --write --patch)",
    },
    "help_write": {"zh": "允许写内存（Safety.allow_write）", "en": "allow memory writes (Safety.allow_write)"},
    "help_patch": {"zh": "允许代码补丁（Safety.allow_patch）", "en": "allow code patches (Safety.allow_patch)"},
    "help_yes": {"zh": "确认执行", "en": "confirm the change"},
    "help_pid": {"zh": "游戏进程 id（默认自动）", "en": "game process id (default: auto)"},
    "help_report": {"zh": "把执行报告写成 JSON", "en": "write the execution report as JSON"},
    "help_dry_run": {"zh": "只等待每个动作，不调用任何后端", "en": "wait for every action, call no backend"},
}

_current: Optional[str] = None


def normalize_lang(lang: Optional[str]) -> Optional[str]:
    """``'zh_CN.UTF-8'`` -> ``'zh'``, ``'en-US'`` -> ``'en'``, other -> ``None``."""
    if not lang:
        return None
    s = str(lang).strip().lower().replace("-", "_")
    if s.startswith("zh"):
        return "zh"
    if s.startswith("en"):
        return "en"
    return None


def _detect() -> str:
    """``PVZTOOLS_LANG`` > ``LC_ALL`` / ``LC_MESSAGES`` / ``LANG`` > process
    locale (only a ``zh*`` locale switches to Chinese) > ``zh``.

    The project's historical default is Chinese; an English-language OS
    locale alone does not flip it, set ``PVZTOOLS_LANG=en`` (or ``LANG``)."""
    env = normalize_lang(os.environ.get("PVZTOOLS_LANG"))
    if env:
        return env
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = normalize_lang(os.environ.get(var))
        if v:
            return v
    try:
        loc = locale.getlocale()[0]
    except (ValueError, AttributeError):
        loc = None
    return normalize_lang(loc) if normalize_lang(loc) == "zh" else "zh"


def set_language(lang: Optional[str]) -> str:
    """Force the language (``'zh'`` / ``'en'``); ``None`` re-enables auto-detection."""
    global _current
    if lang is None:
        _current = None
        return get_language()
    norm = normalize_lang(lang)
    if norm is None:
        raise ValueError("unsupported language %r (use 'zh' or 'en')" % (lang,))
    _current = norm
    return norm


def get_language() -> str:
    return _current or _detect()


def _(key: str, **fmt: object) -> str:
    """Translate *key*; ``**fmt`` is applied with :meth:`str.format`."""
    entry = MESSAGES.get(key)
    if entry is None:
        text = key
    else:
        text = entry.get(get_language()) or entry.get("en") or entry.get("zh") or key
    if fmt:
        try:
            text = text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            pass
    return text
