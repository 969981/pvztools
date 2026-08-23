"""Safety model for the modifier (parity-pvztoolkit.md §0.3 / §4.7, DESIGN §8).

Everything that mutates the game goes through a :class:`Safety` object:

* **flags** — ``allow_write`` (single-field / batch memory writes),
  ``allow_patch`` (code patches), ``allow_remote_call`` (shellcode; not used
  by this package yet), ``allow_input`` (PostMessage mouse input via
  :mod:`pvztools.control.input_backend`), ``allow_savedata`` (anything that touches PlayerInfo,
  mode / level flags or the save file = "high" level).  ``dry_run`` is *on*
  by default: mutations are journaled but not written.
* **purity** — ``"strict"`` refuses every mutation except the
  ``background_running`` patch (pure key-controlled runs).
* **write whitelist** — a write is only accepted for a *semantic name* that
  the address profile declares (``board.sun``, ``lawn_app.frame_duration``,
  ``objects.zombie[3].status`` ...).  Raw addresses are never accepted.
* **range guards** — per-field value ranges (``FIELD_RANGES``); ``board.sun``
  is capped at 9990 unless the sun-limit patch is active.
* **state guards** — ``require_ui`` / ``require_mode`` mirror Toolkit's
  ``GameUI()`` / ``GameMode()`` checks.
* **journal** — every accepted mutation (including dry-run ones and reverts)
  is appended to ``Safety.journal``.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

__all__ = [
    "Safety",
    "SafetyError",
    "PermissionDenied",
    "WhitelistError",
    "RangeError",
    "GameStateError",
    "JournalEntry",
    "FIELD_RANGES",
    "FIELD_LEVELS",
    "FIELD_WIDTHS",
    "SUN_CAP",
    "SUN_CAP_UNLOCKED",
    "LEVEL_FLAGS",
    "CHEAT_FLAGS",
    "CHEAT_FLAGS_BASE",
    "NON_WRITABLE_FIELDS",
    "UI_NAMES",
    "CapabilityDenied",
    "capability_of",
    "semantic_base",
]

SUN_CAP = 9990
SUN_CAP_UNLOCKED = 999999

#: Cheat-command booleans behind Board (lonelystar catalog §1: seven bytes
#: Board+0x5761..0x5767 on 1051; GOTY builds shift Board fields >= 0x90 by
#: +0x18).  Order is the in-game order.  Unverified on GOTY (see report).
CHEAT_FLAGS: Tuple[str, ...] = ("mustache", "trickedout", "future", "pinata", "dance",
                                "daisies", "sukhbir")
CHEAT_FLAGS_BASE: Dict[str, int] = {
    "retail": 0x5761,
    "goty": 0x5779,
    "goty_zh2010_ja": 0x5779,
    "goty_zh2012": 0x5779,
}

#: Which flag a safety level needs (parity §0.3).
LEVEL_FLAGS: Dict[str, str] = {
    "low": "allow_write",
    "medium": "allow_write",
    "high": "allow_savedata",
    "patch": "allow_patch",
    "remote": "allow_remote_call",
}

#: (min, max) guards keyed by semantic base name (index stripped).  Fields
#: missing here fall back to the int32 / uint8 range of their width.
FIELD_RANGES: Dict[str, Tuple[int, int]] = {
    "board.sun": (0, SUN_CAP),
    "board.countdown": (0, 1_000_000),
    "board.countdown_start": (0, 1_000_000),
    "board.huge_wave_countdown": (0, 1_000_000),
    "board.wave": (0, 1000),
    "board.wave_total": (1, 1000),
    "board.wave_hp_threshold": (0, 1_000_000),
    "board.debug_mode": (0, 3),
    "board.paused": (0, 1),
    "board.scene": (0, 5),
    "board.adventure_level": (1, 50),
    "board.main_counter": (0, 0x7FFFFFFF),
    "board.global_counter": (0, 0x7FFFFFFF),
    "board.cheats": (0, 1),
    "lawn_app.frame_duration": (1, 1000),
    "lawn_app.free_planting": (0, 1),
    "lawn_app.game_mode": (0, 100),
    "lawn_app.tod_mode": (0, 1),
    "objects.plant.hp": (0, 100_000),
    "objects.plant.max_hp": (1, 100_000),
    "objects.plant.state": (0, 100),
    "objects.plant.asleep": (0, 1),
    "objects.zombie.status": (0, 100),
    "objects.zombie.hp": (0, 100_000),
    "objects.zombie.helmet_hp": (0, 100_000),
    "objects.zombie.shield_hp": (0, 100_000),
    "objects.seed_packet.cd_elapsed": (0, 1_000_000),
    "objects.seed_packet.cd_total": (0, 1_000_000),
    "objects.seed_packet.usable": (0, 1),
    "objects.seed_packet.type": (0, 48),
    "objects.seed_packet.imitater_type": (0, 48),
    "objects.cursor.grab": (0, 100),
    "objects.challenge.endless_round": (0, 100_000),
    "player_info.money": (0, 999_999),
    "player_info.level": (1, 50),
    "player_info.tree_height": (0, 100_000),
}

#: Byte width of fields that are not plain dwords.
FIELD_WIDTHS: Dict[str, int] = {
    "board.paused": 1,
    "board.cheats": 1,
    "lawn_app.free_planting": 1,
    "lawn_app.tod_mode": 1,
    "objects.plant.dead": 1,
    "objects.plant.squished": 1,
    "objects.plant.asleep": 1,
    "objects.zombie.dead": 1,
    "objects.lawn_mower.dead": 1,
    "objects.grid_item.dead": 1,
    "objects.seed_packet.usable": 1,
}

#: Safety level of a field (default "low").  "high" == touches save data or
#: mode / level flags (parity §4.2).
FIELD_LEVELS: Dict[str, str] = {
    "lawn_app.game_mode": "high",
    "lawn_app.game_selector": "high",
    "board.adventure_level": "high",
}

#: Profile fields that are pointers / array bases / array capacities /
#: sub-object handles.  They are *addresses*, not game values: writing them
#: crashes the game on the next frame, so they never enter the write
#: whitelist even though the profile declares them (parity §4.7 says a
#: declared semantic name is necessary, not sufficient).
NON_WRITABLE_FIELDS: frozenset = frozenset({
    "lawn_app.board", "lawn_app.player_info", "lawn_app.game_selector",
    "lawn_app.music", "lawn_app.effect_system",
    "board.zombies", "board.plants", "board.mowers", "board.grid_items",
    "board.projectiles", "board.items", "board.cursor", "board.seed_bank",
    "board.cutscene", "board.challenge", "board.spawn_list",
    "board.zombie_count_max", "board.plant_count_max", "board.mower_count_max",
    "board.grid_item_count_max", "board.plant_next_index", "board.mower_count",
    "board.particle_system_ref", "board.allowed_types", "board.grid_type",
    "board.row_type",
    "objects.seed_bank.packets", "objects.particle_system.particle_holder",
    "objects.particle_holder.particles", "objects.particle_holder.particle_count_max",
    "objects.challenge.challenge",
})
_NON_WRITABLE_SUFFIXES = ("_ptr", "_count_max", "_array", "_base", "_vtable")

#: LawnApp.game_ui values (PvZ ``GameScenes``: 0 loading, 1 menu, 2 level
#: intro / seed chooser, 3 playing, 4 zombies won, 5 award, 6 credits,
#: 7 challenge screen).  Shared by the facade and every error message.
UI_NAMES: Dict[int, str] = {
    0: "启动中", 1: "主菜单", 2: "选卡界面", 3: "关卡中", 4: "关卡失败（僵尸获胜）",
    5: "通关结算", 6: "制作人员", 7: "挑战模式选单",
}

#: Capability grades (DESIGN §3.2 L4) that still allow mutations.
_MUTABLE_CAPABILITIES = ("FULL", "READ_WRITE")
_CAPABILITY_ALIASES = {"PARTIAL": "READ_WRITE"}


def capability_of(profile: Any) -> str:
    """Effective capability grade of a profile: the resolver's grade when the
    profile came through :func:`pvztools.addr.resolve.resolve` (attached as
    ``_pvz_resolution``), else the profile's declared ``capability``."""
    res = getattr(profile, "_pvz_resolution", None)
    cap = getattr(res, "capability", None) or getattr(profile, "capability", "FULL") or "FULL"
    return _CAPABILITY_ALIASES.get(cap, cap)


def is_writable_field(name: str) -> bool:
    """False for pointer / array / capacity fields (see NON_WRITABLE_FIELDS)."""
    base = semantic_base(name)
    if base in NON_WRITABLE_FIELDS:
        return False
    return not base.endswith(_NON_WRITABLE_SUFFIXES)


_INDEX_RE = re.compile(r"\[\d+\]")


def semantic_base(name: str) -> str:
    """``objects.zombie[3].status`` -> ``objects.zombie.status``;
    ``board.cheats.dance`` -> ``board.cheats``."""
    base = _INDEX_RE.sub("", name)
    if base.startswith("board.cheats."):
        return "board.cheats"
    return base


class SafetyError(Exception):
    """Base class: the safety model refused an operation."""


class PermissionDenied(SafetyError):
    """A flag is missing.  ``flag`` names the attribute to set."""

    def __init__(self, flag: str, action: str, reason: str = "") -> None:
        self.flag = flag
        self.action = action
        if flag == "purity":
            msg = "%s：purity='strict' 纯净模式拒绝所有修改（仅允许 background_running 补丁）；" \
                  "需要时用 Safety(purity='normal')" % action
        elif flag == "session":
            msg = "%s：当前会话为只读；请用 attach(write=True) 打开进程后再关闭 dry_run" % action
        else:
            msg = "%s：需要 Safety(%s=True)" % (action, flag)
        if reason:
            msg += "（%s）" % reason
        super().__init__(msg)


class WhitelistError(SafetyError):
    """The write target is not a profile-declared semantic name."""


class CapabilityDenied(SafetyError):
    """The build's capability grade (READ_ONLY / UNSUPPORTED) forbids writes."""

    def __init__(self, capability: str, action: str) -> None:
        self.capability = capability
        self.action = action
        super().__init__("%s：当前版本能力分级为 %s，地址未通过校验，拒绝任何写入/补丁；"
                         "请运行 pvztools doctor 查看原因" % (action, capability))


class RangeError(SafetyError):
    """Value outside the per-field guard range."""

    def __init__(self, name: str, value: int, lo: int, hi: int) -> None:
        self.name, self.value, self.lo, self.hi = name, value, lo, hi
        super().__init__("%s=%r 超出允许范围 [%d, %d]" % (name, value, lo, hi))


class GameStateError(SafetyError):
    """Game is not in a state where the operation is allowed (ui / mode)."""


@dataclass
class JournalEntry:
    what: str            # "write" | "revert" | "patch_on" | "patch_off"
    where: str           # semantic name (or patch name / site)
    addr: int
    old: Any
    new: Any
    time: float = field(default_factory=time.time)
    dry_run: bool = False
    width: int = 4

    def __str__(self) -> str:
        tag = "[dry] " if self.dry_run else ""
        return "%s%s %s @0x%x: %r -> %r" % (tag, self.what, self.where, self.addr, self.old, self.new)


class Safety:
    """Holds the flags, the whitelist (bound from a profile) and the journal."""

    def __init__(self, allow_write: bool = False, allow_patch: bool = False,
                 allow_remote_call: bool = False, allow_savedata: bool = False,
                 dry_run: bool = True, purity: str = "normal",
                 allow_input: bool = False) -> None:
        if purity not in ("normal", "strict"):
            raise ValueError("purity must be 'normal' or 'strict', got %r" % (purity,))
        self.allow_write = bool(allow_write)
        self.allow_patch = bool(allow_patch)
        self.allow_remote_call = bool(allow_remote_call)
        self.allow_savedata = bool(allow_savedata)
        #: window-message input injection (control.input_backend); no memory
        #: writes, but it still acts on the user's game, so off by default.
        self.allow_input = bool(allow_input)
        self.dry_run = bool(dry_run)
        self.purity = purity
        self.journal: List[JournalEntry] = []
        #: set by PatchManager when ``sun_cap_unlock`` is active
        self.sun_limit_unlocked = False
        self._whitelist: Set[str] = set()
        self._whitelist_profile_id: Optional[str] = None
        #: capability grade of the bound profile (None until bind_profile)
        self.capability: Optional[str] = None

    # -- flags ---------------------------------------------------------------
    @property
    def strict(self) -> bool:
        return self.purity == "strict"

    def flag_for_level(self, level: str) -> str:
        try:
            return LEVEL_FLAGS[level]
        except KeyError:
            raise ValueError("unknown safety level %r" % (level,))

    def require_flag(self, flag: str, action: str, reason: str = "") -> None:
        if not getattr(self, flag, False):
            raise PermissionDenied(flag, action, reason)

    def require_level(self, level: str, action: str) -> None:
        """Raise PermissionDenied unless the flag for ``level`` is set.
        ``high`` also requires ``allow_write`` (it is still a memory write)."""
        if self.strict:
            raise PermissionDenied("purity", action)
        flag = self.flag_for_level(level)
        self.require_flag(flag, action, "安全级别 %s" % level)
        if level == "high":
            self.require_flag("allow_write", action, "安全级别 high 同时需要写内存")

    def require_input(self, action: str) -> None:
        """Raise PermissionDenied unless ``allow_input`` is set (input mode
        posts window messages; it does not write memory and ignores dry_run)."""
        self.require_flag("allow_input", action, "窗口消息输入模式")

    def require_session_writable(self, session: Any, action: str) -> None:
        """Real (non dry-run) mutations need a writable session."""
        if self.dry_run:
            return
        if not getattr(session, "writable", False):
            raise PermissionDenied("session", action)

    # -- whitelist -------------------------------------------------------------
    def bind_profile(self, profile: Any) -> None:
        """Derive the write whitelist from the profile's semantic names."""
        names: Set[str] = set()
        for sec_name, sec in (("lawn_app", profile.lawn_app), ("board", profile.board),
                              ("player_info", profile.player_info)):
            for k, v in sec.items():
                if v is not None:
                    names.add("%s.%s" % (sec_name, k))
        for oname, lay in profile.objects.items():
            for k, v in lay.fields.items():
                if v is not None:
                    names.add("objects.%s.%s" % (oname, k))
        if profile.board.get("cheats") is not None or profile.family in CHEAT_FLAGS_BASE:
            names.add("board.cheats")
        self._whitelist = {n for n in names if is_writable_field(n)}
        self._whitelist_profile_id = getattr(profile, "id", None)
        self.capability = capability_of(profile)

    # -- capability ------------------------------------------------------------
    def require_capability(self, action: str, profile: Any = None) -> None:
        """Refuse mutations when the build is graded READ_ONLY / UNSUPPORTED."""
        cap = capability_of(profile) if profile is not None else self.capability
        if cap is not None and cap not in _MUTABLE_CAPABILITIES:
            raise CapabilityDenied(cap, action)

    @property
    def whitelist(self) -> Set[str]:
        return set(self._whitelist)

    def check_whitelist(self, name: str) -> str:
        """Return the semantic base of ``name`` or raise WhitelistError."""
        if not self._whitelist:
            raise WhitelistError("写白名单为空：请先 Safety.bind_profile(profile)")
        base = semantic_base(name)
        if base not in self._whitelist:
            if not is_writable_field(base):
                raise WhitelistError("%r 是指针/数组基址/容量字段，不允许写入（会让游戏崩溃）" % (name,))
            raise WhitelistError("%r 不在 profile %s 的语义名白名单中，拒绝写入"
                                 % (name, self._whitelist_profile_id))
        return base

    # -- range guard -----------------------------------------------------------
    def width_of(self, name: str) -> int:
        return FIELD_WIDTHS.get(semantic_base(name), 4)

    def level_of(self, name: str) -> str:
        base = semantic_base(name)
        if base.startswith("player_info."):
            return "high"
        return FIELD_LEVELS.get(base, "low")

    def range_of(self, name: str, width: Optional[int] = None, signed: bool = True) -> Tuple[int, int]:
        base = semantic_base(name)
        if base == "board.sun":
            return (0, SUN_CAP_UNLOCKED if self.sun_limit_unlocked else SUN_CAP)
        if base in FIELD_RANGES:
            return FIELD_RANGES[base]
        w = width if width is not None else self.width_of(name)
        if w == 1:
            return (0, 0xFF)
        if w == 2:
            return (0, 0xFFFF)
        return (-0x80000000, 0x7FFFFFFF) if signed else (0, 0xFFFFFFFF)

    def check_range(self, name: str, value: int, width: Optional[int] = None,
                    signed: bool = True) -> None:
        if isinstance(value, bool):
            value = int(value)
        if not isinstance(value, int):
            raise RangeError(name, value, *self.range_of(name, width, signed))
        lo, hi = self.range_of(name, width, signed)
        if not (lo <= value <= hi):
            raise RangeError(name, value, lo, hi)

    # -- state guards ----------------------------------------------------------
    @staticmethod
    def _lawn(session: Any, profile: Any) -> int:
        lawn = session.read_ptr(profile.globals.lawn_app)
        if not lawn:
            raise GameStateError("LawnApp 指针为空：游戏尚未初始化")
        return lawn

    def current_ui(self, session: Any, profile: Any) -> int:
        return session.read_i32(self._lawn(session, profile) + profile.lawn_app.game_ui)

    def current_mode(self, session: Any, profile: Any) -> int:
        return session.read_i32(self._lawn(session, profile) + profile.lawn_app.game_mode)

    def require_ui(self, session: Any, profile: Any, allowed: Iterable[int] = (2, 3),
                   action: str = "操作") -> int:
        ui = self.current_ui(session, profile)
        allowed = set(allowed)
        if ui not in allowed:
            raise GameStateError("%s 需要 game_ui ∈ %s（当前 %d：%s）"
                                 % (action, sorted(allowed), ui, UI_NAMES.get(ui, "未知界面")))
        return ui

    def require_mode(self, session: Any, profile: Any,
                     allowed: Iterable[Sequence[int]], action: str = "操作") -> int:
        """``allowed`` = iterable of ``(lo, hi)`` inclusive ranges (profile gates)."""
        mode = self.current_mode(session, profile)
        for rng in allowed:
            lo, hi = (rng[0], rng[-1]) if len(rng) > 1 else (rng[0], rng[0])
            if lo <= mode <= hi:
                return mode
        raise GameStateError("%s 需要 game_mode ∈ %s（当前 %d）"
                             % (action, [list(r) for r in allowed], mode))

    # -- journal ---------------------------------------------------------------
    def log(self, what: str, where: str, addr: int, old: Any, new: Any,
            width: int = 4, dry_run: Optional[bool] = None) -> JournalEntry:
        entry = JournalEntry(what, where, addr, old, new,
                             dry_run=self.dry_run if dry_run is None else dry_run, width=width)
        self.journal.append(entry)
        return entry

    def clear_journal(self) -> None:
        self.journal.clear()

    def __repr__(self) -> str:
        return ("Safety(allow_write=%s, allow_patch=%s, allow_remote_call=%s, allow_savedata=%s,"
                " dry_run=%s, purity=%r, journal=%d)"
                % (self.allow_write, self.allow_patch, self.allow_remote_call,
                   self.allow_savedata, self.dry_run, self.purity, len(self.journal)))

