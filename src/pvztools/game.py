"""Beginner facade (docs/user-scenarios.md §2): one object, plain properties.

::

    import pvztools as pvz

    game = pvz.attach()            # find the game, identify the build, validate
    game.sun                       # -> 25      (read: always safe)
    game.wave, game.clock, game.countdown
    for z in game.zombies:         # snapshot, read only
        print(z.row, z.name, z.hp)

    game = pvz.attach(write=True)  # opt in to writes + code patches
    game.sun = 999
    game.cheats.no_cooldown = True
    game.cheats.sun_limit = True   # patch; restored when the Game is closed

    game = pvz.attach(write=True, remote=True)   # + remote calls (shellcode)
    game.plant("豌豆射手", row=3, col=1)           # 中文 / 英文 / PlantType / id
    game.shovel(3, 1)
    game.place_lineup("经典四炮")                  # catalogue name / code / Lineup
    game.spawn_allowed = "普杆舞红"               # 33 allowed_types flags
    game.spawn_reroll()                           # the game regenerates its list

    game = pvz.attach(write=True, remote=True, input=True)   # + window messages
    game.window.bring_to_foreground()             # AttachThreadInput trick
    game.chooser.select_and_start([1, 16, 14])    # seed chooser (ui 2) -> level
    game.play_level("3C八炮", "P4: PP|PP|PP|PP (869|869|869|869)", cob_col=8)

Every read property is a fresh read of the game (no caching).  Every write
goes through :mod:`pvztools.modifier` (whitelist, range guard, journal) and
is refused with a :class:`PermissionDenied` that says what to do when the
Game was not opened with ``write=True``.  Closing the Game restores the
patches it enabled.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pvztools.i18n import _
from pvztools.addr import Profile, profile_for_id, profile_for_stamp, load_profile
from pvztools.model import Board, GameState, Plant, Seed, SpawnList, Zombie
from pvztools.modifier import (CHEAT_FLAGS, UI_NAMES, CapabilityDenied, PatchManager,
                               PermissionDenied, Safety, Writer, capability_of)
from pvztools.observe import NotInLevel, Observer
from pvztools.process import GameNotRunning, MultipleGames, ProcessError, Session
from pvztools.process import attach as _process_attach
from pvztools.process.lock import acquire as _acquire_lock

__all__ = [
    "Game", "GameCheats", "CheatFlags", "attach", "DEFAULT_PLAY_SEEDS",
    "GameNotRunning", "MultipleGames", "ProcessError", "NotInLevel", "PermissionDenied",
    "UnknownBuild", "NotImplementedAction", "CapabilityDenied", "UI_NAMES", "describe_ui",
]

#: Speed multiplier bounds (frame_duration 1..1000 ms -> 10x .. 0.01x).
SPEED_MIN, SPEED_MAX = 0.01, 10.0

#: 1x speed: LawnApp.frame_duration in ms per game frame.
DEFAULT_FRAME_DURATION = 10

#: Chooser default when ``play_level(seeds=None)``: the first chooser row
#: (peashooter .. chomper, ids 0-6) + lily pad (16) -- what the live run used;
#: :meth:`SeedChooser.fill_types` pads / trims it to the bank size.
DEFAULT_PLAY_SEEDS: Tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 16)

# i18n catalog keys (pvztools.i18n); resolved with _() at raise time so the
# language can be switched after import.
_HINT_WRITE = "hint_write"
_HINT_REMOTE = "hint_remote"
_HINT_INPUT = "hint_input"


class UnknownBuild(ProcessError):
    """The build is not in the version table and no profile was supplied."""


class NotImplementedAction(NotImplementedError):
    """A spec'd action that needs remote calls (DESIGN: 未实现)."""

    def __init__(self, what: str) -> None:
        super().__init__("%s需要远程调用（注入线程），pvztools 尚未实现；"
                         "目前只支持读取和内存写入/补丁类修改" % what)


def describe_ui(ui: int) -> str:
    return UI_NAMES.get(ui, "未知界面(%d)" % ui)


def _load_profile_arg(profile: Any, session: Any) -> Profile:
    if isinstance(profile, Profile):
        return profile
    if isinstance(profile, str):
        p = profile_for_id(profile)
        if p is None:
            try:
                p = load_profile(profile)
            except OSError as exc:
                raise UnknownBuild(_("profile_not_found", profile=profile, err=exc)) from None
        return p
    if profile is not None:
        return load_profile(profile)
    p = profile_for_stamp(session.stamp)
    if p is None:
        # unknown build → L2/L3 (cache / signature scan) through the resolver
        from pvztools.addr.resolve import resolve, UNSUPPORTED

        res = resolve(session)
        if res.capability == UNSUPPORTED:
            missing = ", ".join(res.missing[:6]) or "?"
            raise UnknownBuild("不认识这个游戏版本（stamp 0x%08x，进程 %s），特征扫描也没有找到关键地址"
                               "（缺少：%s）；请运行 pvztools doctor --json 诊断.json 并把诊断文件反馈给我们"
                               % (session.stamp, getattr(session, "name", "?"), missing))
        p = res.profile
        p._pvz_resolution = res  # type: ignore[attr-defined]
    return p


class _ResolvedView:
    """Adapter so :func:`capability_of` sees a Resolution as a profile."""

    def __init__(self, res: Any) -> None:
        self._pvz_resolution = res


class CheatFlags:
    """The seven built-in cheat-code booleans (``mustache``, ``trickedout``,
    ``future``, ``pinata``, ``dance``, ``daisies``, ``sukhbir``) as settable
    attributes.  Reading needs a level; writing needs ``write=True``."""

    def __init__(self, game: "Game") -> None:
        object.__setattr__(self, "_game", game)

    def _get(self, name: str) -> bool:
        g = self._game
        w = g._writer_for_read()
        return bool(g.session.read(w.cheat_flag_addr(name), 1)[0])

    def _set(self, name: str, on: bool) -> None:
        g = self._game
        g._need_write("设置作弊标志 %s" % name)
        g._writer.set_cheat_flag(name, bool(on))

    def as_dict(self) -> Dict[str, bool]:
        return {n: self._get(n) for n in CHEAT_FLAGS}

    def __getattr__(self, name: str) -> bool:
        if name in CHEAT_FLAGS:
            return self._get(name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in CHEAT_FLAGS:
            self._set(name, value)
            return
        raise AttributeError(_("unknown_cheat", name=name, flags=", ".join(CHEAT_FLAGS)))

    def __repr__(self) -> str:
        try:
            return "CheatFlags(%s)" % ", ".join("%s=%s" % kv for kv in self.as_dict().items())
        except Exception:
            return "CheatFlags(<not in level>)"


class GameCheats:
    """Modifier switches, all off by default.  Each is a property:

    - ``no_cooldown`` — finish every card's cooldown now (write; one pass —
      set it again after planting, or combine with ``free_planting``)
    - ``free_planting`` — cards cost nothing (write LawnApp flag)
    - ``sun_limit`` — lift the 9990 sun cap (code patch ``sun_cap_unlock``)
    - ``auto_collect`` — sun / coins collect themselves (code patch)
    - ``background`` — keep running when the window loses focus (code patch)
    - ``speed`` — frame duration in ms (10 = 1x, 5 = 2x, 20 = 0.5x)
    - ``flags`` — :class:`CheatFlags` (mustache / dance / ...)

    Setting any of them needs ``pvz.attach(write=True)``; otherwise a
    :class:`PermissionDenied` explains what to do.
    """

    _PATCHES = {"sun_limit": "sun_cap_unlock", "auto_collect": "auto_collect",
                "background": "background_running"}

    def __init__(self, game: "Game") -> None:
        self._game = game
        self._no_cooldown = False
        self.flags = CheatFlags(game)

    # -- write-based --------------------------------------------------------------
    @property
    def no_cooldown(self) -> bool:
        """True after ``= True`` in this session (the game itself has no flag)."""
        return self._no_cooldown

    @no_cooldown.setter
    def no_cooldown(self, on: bool) -> None:
        g = self._game
        g._need_write("no_cooldown")
        g._writer.no_cooldown(bool(on))
        self._no_cooldown = bool(on)

    @property
    def free_planting(self) -> bool:
        g = self._game
        off = g.profile.lawn_app.get("free_planting")
        if off is None:
            return False
        lawn = g._observer.lawn_ptr()
        return bool(g.session.read(lawn + off, 1)[0]) if lawn else False

    @free_planting.setter
    def free_planting(self, on: bool) -> None:
        g = self._game
        g._need_write("free_planting")
        g._writer.free_planting(bool(on))

    @property
    def speed(self) -> float:
        """Speed multiplier derived from ``frame_duration`` (1.0 = normal)."""
        fd = self._game._observer.game().frame_duration
        return DEFAULT_FRAME_DURATION / fd if fd else 0.0

    @speed.setter
    def speed(self, multiplier: float) -> None:
        g = self._game
        g._need_write("speed")
        if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
            raise TypeError(_("speed_type", value=multiplier))
        m = float(multiplier)
        if not (SPEED_MIN <= m <= SPEED_MAX):
            raise ValueError(_("speed_range", value=multiplier, lo=SPEED_MIN, hi=SPEED_MAX))
        g._writer.set_game_speed(max(1, int(round(DEFAULT_FRAME_DURATION / m))))

    @property
    def frame_duration(self) -> int:
        return self._game._observer.game().frame_duration

    @frame_duration.setter
    def frame_duration(self, ms: int) -> None:
        g = self._game
        g._need_write("frame_duration")
        g._writer.set_game_speed(int(ms))

    # -- patch-based ----------------------------------------------------------------
    def _patch_state(self, key: str) -> bool:
        pm = self._game._patches_for_read()
        try:
            return pm.read_state(key) == "on"
        except Exception:
            return False

    def _patch_set(self, key: str, on: bool) -> None:
        g = self._game
        g._need_patch(key)
        if on:
            g._patches.enable(key)
        else:
            g._patches.disable(key)

    @property
    def sun_limit(self) -> bool:
        """True when the 9990 cap is lifted (``sun_cap_unlock`` patch on)."""
        return self._patch_state(self._PATCHES["sun_limit"])

    @sun_limit.setter
    def sun_limit(self, on: bool) -> None:
        self._patch_set(self._PATCHES["sun_limit"], bool(on))

    @property
    def auto_collect(self) -> bool:
        return self._patch_state(self._PATCHES["auto_collect"])

    @auto_collect.setter
    def auto_collect(self, on: bool) -> None:
        self._patch_set(self._PATCHES["auto_collect"], bool(on))

    @property
    def background(self) -> bool:
        return self._patch_state(self._PATCHES["background"])

    @background.setter
    def background(self, on: bool) -> None:
        self._patch_set(self._PATCHES["background"], bool(on))

    # -- actions --------------------------------------------------------------------
    def kill_zombies(self) -> int:
        """Mark every live zombie as dying; returns how many were hit."""
        g = self._game
        g._need_write("kill_zombies")
        return len(g._writer.kill_all_zombies())

    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"no_cooldown": self.no_cooldown}
        try:
            out["free_planting"] = self.free_planting
            out["speed"] = self.speed
        except Exception:
            pass
        for name in ("sun_limit", "auto_collect", "background"):
            out[name] = getattr(self, name)
        return out

    def __repr__(self) -> str:
        return "GameCheats(%s)" % ", ".join("%s=%s" % kv for kv in self.status().items())


class Game:
    """A running game.  Create it with :func:`attach`.

    Read properties never write anything; they raise :class:`NotInLevel`
    (message: 请先进入关卡) when there is no Board.  ``version`` /
    ``capability`` / ``ui`` / ``in_level`` work on any screen.
    """

    def __init__(self, session: Session, profile: Profile, *, write: bool = False,
                 patch: Optional[bool] = None, safety: Optional[Safety] = None,
                 remote: bool = False, input: bool = False, foreground: bool = True) -> None:
        self.session = session
        self.profile = profile
        self._remote = bool(remote)
        self._input = bool(input)
        #: input backend / chooser bring the game window to the front before
        #: posting (the game pauses when unfocused unless ``background_running``)
        self._foreground = bool(foreground)
        # remote calls need memory writes and the main_loop_block code patch
        self._write = bool(write) or self._remote
        self._patch = self._write if patch is None else (bool(patch) or self._remote)
        self.safety = safety if safety is not None else Safety(
            allow_write=self._write, allow_patch=self._patch, allow_remote_call=self._remote,
            # dry_run is the global no-side-effects switch (InputBackend honours it too)
            dry_run=not (self._write or self._patch or self._input), allow_input=self._input)
        self._observer = Observer(session, profile)
        self._writer_obj: Optional[Writer] = None
        self._patches_obj: Optional[PatchManager] = None
        self._executor_obj: Any = None
        self._actions_obj: Any = None
        self._spawn_ctl_obj: Any = None
        self._window_obj: Any = None
        self._chooser_obj: Any = None
        self._resolution = getattr(profile, "_pvz_resolution", None)
        self.cheats = GameCheats(self)

    # -- plumbing --------------------------------------------------------------------
    def _need_capability(self, action: str) -> None:
        """DESIGN §3.2 L4: READ_ONLY / UNSUPPORTED builds never accept mutations."""
        cap = self.capability
        if cap not in ("FULL", "READ_WRITE"):
            raise CapabilityDenied(cap, action)

    def _need_write(self, action: str) -> None:
        if not self._write:
            raise PermissionDenied("allow_write", action, _(_HINT_WRITE))
        if not getattr(self.session, "writable", False):
            raise PermissionDenied("session", action, _(_HINT_WRITE))
        self._need_capability(action)

    def _need_patch(self, action: str) -> None:
        if not self._patch:
            raise PermissionDenied("allow_patch", _("patch_action", action=action), _(_HINT_WRITE))
        if not getattr(self.session, "writable", False):
            raise PermissionDenied("session", _("patch_action", action=action), _(_HINT_WRITE))
        self._need_capability(_("patch_action", action=action))

    def _need_remote(self, action: str) -> None:
        """Remote calls need ``attach(write=True, remote=True)``: the flags
        ``allow_remote_call`` + ``allow_patch`` (+ ``allow_write``)."""
        if not self._remote or not self.safety.allow_remote_call:
            raise PermissionDenied("allow_remote_call", action,
                                   _(_HINT_REMOTE) + _("hint_remote_flags"))
        if not self.safety.allow_patch:
            raise PermissionDenied("allow_patch", action, _(_HINT_REMOTE))
        if not getattr(self.session, "writable", False):
            raise PermissionDenied("session", action, _(_HINT_REMOTE))
        self._need_capability(action)

    @property
    def _writer(self) -> Writer:
        if self._writer_obj is None:
            self._writer_obj = Writer(self.session, self.profile, self.safety)
        return self._writer_obj

    def _writer_for_read(self) -> Writer:
        """A Writer used only for its address arithmetic (no flags needed)."""
        return self._writer

    @property
    def _patches(self) -> PatchManager:
        if self._patches_obj is None:
            if self._remote:
                from pvztools.control.actions import RemotePatchManager

                self._patches_obj = RemotePatchManager(self.session, self.profile, self.safety)
            else:
                self._patches_obj = PatchManager(self.session, self.profile, self.safety)
        return self._patches_obj

    @property
    def _executor(self) -> Any:
        if self._executor_obj is None:
            from pvztools.control.remote import RemoteExecutor

            self._executor_obj = RemoteExecutor(self.session, self.profile, self.safety, self._patches)
        return self._executor_obj

    @property
    def actions(self) -> Any:
        """:class:`pvztools.control.actions.Actions` bound to this game
        (needs ``remote=True``; every call re-checks the flags)."""
        if self._actions_obj is None:
            from pvztools.control.actions import Actions

            self._actions_obj = Actions(self.session, self.profile, self.safety, self._observer,
                                        self._executor, self._patches)
        return self._actions_obj

    @property
    def spawn_control(self) -> Any:
        """:class:`pvztools.control.spawn_ctl.SpawnControl` bound to this game."""
        if self._spawn_ctl_obj is None:
            from pvztools.control.spawn_ctl import SpawnControl

            self._spawn_ctl_obj = SpawnControl(self.session, self.profile, self.safety, self._observer,
                                               self._executor, self._patches)
        return self._spawn_ctl_obj

    def _patches_for_read(self) -> PatchManager:
        return self._patches

    def _board(self) -> Board:
        try:
            return self._observer.board()
        except NotInLevel:
            raise NotInLevel(_("not_in_level", ui=describe_ui(self.ui))) from None

    # -- identity ------------------------------------------------------------------
    @property
    def version(self) -> str:
        """Profile id, e.g. ``goty_1_2_0_1096_en``."""
        return self.profile.id

    @property
    def capability(self) -> str:
        """``FULL`` / ``READ_WRITE`` / ``READ_ONLY`` / ``UNSUPPORTED`` (parity §0.3)."""
        if self._resolution is not None:
            return capability_of(_ResolvedView(self._resolution))
        return capability_of(self.profile)

    @property
    def pid(self) -> int:
        return self.session.pid

    @property
    def writable(self) -> bool:
        return self._write and bool(getattr(self.session, "writable", False))

    # -- LawnApp -------------------------------------------------------------------
    @property
    def state(self) -> GameState:
        return self._observer.game()

    @property
    def ui(self) -> int:
        """LawnApp.game_ui (3 = in a level, 2 = seed chooser, 1 = main menu)."""
        return self._observer.game().ui

    @property
    def ui_name(self) -> str:
        return describe_ui(self.ui)

    @property
    def mode(self) -> int:
        return self._observer.game().mode

    @property
    def in_level(self) -> bool:
        """True when a Board exists (seed chooser counts: the Board is created then)."""
        return self._observer.board_ptr() != 0

    # -- Board --------------------------------------------------------------------
    @property
    def board(self) -> Board:
        """Full :class:`pvztools.model.Board` snapshot."""
        return self._board()

    @property
    def sun(self) -> int:
        """Board.sun (offset ``board.sun`` of the profile)."""
        return self._board().sun

    @sun.setter
    def sun(self, value: int) -> None:
        self._need_write("设置阳光")
        self._writer.set_sun(int(value))

    @property
    def wave(self) -> int:
        return self._board().wave

    @property
    def total_waves(self) -> int:
        return self._board().total_waves

    @property
    def clock(self) -> int:
        """Board.main_counter — the in-level game clock (centiseconds)."""
        return self._board().clock

    @property
    def countdown(self) -> int:
        """Centiseconds until the next wave."""
        return self._board().countdown

    @property
    def paused(self) -> bool:
        return self._board().paused

    @property
    def scene(self) -> int:
        return self._board().scene

    @property
    def level(self) -> int:
        return self._board().level

    # -- arrays -------------------------------------------------------------------
    @property
    def zombies(self) -> List[Zombie]:
        """Live zombies (snapshot; ``z.name`` is Chinese, ``z.type`` the id)."""
        self._board()
        return list(self._observer.zombies(include_dead=False))

    @property
    def plants(self) -> List[Plant]:
        self._board()
        return list(self._observer.plants(include_dead=False))

    @property
    def seeds(self) -> List[Seed]:
        self._board()
        return list(self._observer.seeds())

    @property
    def spawn(self) -> Optional[SpawnList]:
        """This level's spawn list, or ``None`` when the game has none."""
        self._board()
        return self._observer.spawn_list()

    def describe_spawn(self) -> str:
        self._board()
        return self._observer.describe_spawn()

    def snapshot(self, include_dead: bool = False):
        self._board()
        return self._observer.snapshot(include_dead=include_dead)

    # -- actions (spec §2; remote calls — need attach(write=True, remote=True)) -------------
    @property
    def remote(self) -> bool:
        """True when this Game may inject remote calls."""
        return self._remote and self.safety.allow_remote_call

    def plant(self, what: Any, row: int, col: int, imitater: bool = False) -> Optional[int]:
        """放植物 at 1-based ``(row, col)``; ``what`` = 中文名 / English name /
        ``PlantType`` / id.  Returns the new plant's array index."""
        self._need_remote("放植物 plant()")
        return self.actions.put_plant(row, col, what, imitater=imitater)

    def shovel(self, row: int, col: int, layer: str = "all") -> Optional[int]:
        """铲植物 at 1-based ``(row, col)``; returns how many plants were removed."""
        self._need_remote("铲植物 shovel()")
        return self.actions.remove_plant(row, col, layer)

    def place_lineup(self, lineup_or_name: Any, *, clear: bool = True, db_path: Optional[str] = None,
                     strict: bool = False):
        """布阵: a :class:`~pvztools.schema.lineup.Lineup`, a lineup code, a
        lineup string or an ArrayDesign catalogue name / index.  One remote
        call places everything (``clear=True`` adds up to two more: grid
        items, then plants; ``PlaceSummary.calls`` counts them); ``strict``
        turns the lenient water / pot diagnostics into errors.  Returns a
        ``PlaceSummary``."""
        self._need_remote("布阵 place_lineup()")
        from pvztools.control.lineup_place import place_named

        return place_named(self.actions, lineup_or_name, db_path, clear=clear, strict=strict)

    def clear(self, what: str = "plants") -> Optional[int]:
        """清除: ``plants`` (every live plant) or grid items -- ``graves`` /
        ``ladders`` / ``rakes`` / ``craters`` / ``items`` (graves + craters +
        ladders + rakes).  Returns how many were deleted."""
        kinds = {"plants": None, "graves": (1,), "ladders": (3,), "rakes": (11,), "craters": (2,),
                 "items": (1, 2, 3, 11)}
        if what not in kinds:
            raise ValueError("clear(): what 必须是 %s，得到 %r" % ("/".join(kinds), what))
        self._need_remote("清除 %s" % what)
        types = kinds[what]
        if types is None:
            return self.actions.clear_plants()
        return self.actions.clear_grid_items(types)

    def collect_all(self, types: Any = None) -> int:
        """收集全部: one remote call issuing ``Coin::Collect`` for every
        live, not-yet-collecting sun / coin / award on the field (up to 20 per
        call; ``types`` = CoinType ids to restrict).  Returns how many were
        collected (0 when paused / not in a level)."""
        self._need_remote("收集 collect_all()")
        from pvztools.control.autocollect import AutoCollector

        return AutoCollector(self.actions, self._observer, self.profile, types=types).collect_now()

    def spawn_reroll(self, types: Any = None):
        """重新出怪: optionally set the allowed zombie types (ids / names /
        ``"普杆舞红"``), then let the game regenerate the spawn list.
        Returns the new :class:`SpawnList`."""
        self._need_remote("重新出怪 spawn_reroll()")
        if isinstance(types, str):
            types = [types]
        return self.spawn_control.reroll(types)

    def spawn_stats(self, n: int, types: Any = None):
        """Reroll ``n`` times for statistics, then restore the original list."""
        self._need_remote("出怪统计 spawn_stats()")
        if isinstance(types, str):
            types = [types]
        return self.spawn_control.stats(n, types)

    @property
    def spawn_allowed(self) -> Tuple[int, ...]:
        """Zombie ids allowed by ``board.allowed_types`` (read: always safe)."""
        self._board()
        return self.spawn_control.allowed()

    @spawn_allowed.setter
    def spawn_allowed(self, types: Any) -> None:
        self._need_write("写出怪种类")
        if isinstance(types, str):
            types = [types]
        self.spawn_control.set_allowed(types)

    # -- cob cannons (source-map §6) ------------------------------------------------
    @property
    def cobs(self) -> List[Any]:
        """Live cob cannons (read: always safe): ``CobInfo(index, row, col,
        state, countdown)`` with 1-based row / col (col = LEFT cell); ``ready``
        when state == 37, ``countdown`` an estimate in centiseconds."""
        self._board()
        return self.actions_readonly.cobs()

    @property
    def actions_readonly(self) -> Any:
        """An :class:`Actions` usable without ``remote=True`` for its read-only
        helpers (``cobs()``); every mutating call still re-checks the flags."""
        if self._remote:
            return self.actions
        if getattr(self, "_actions_ro", None) is None:
            from pvztools.control.actions import Actions

            self._actions_ro = Actions(self.session, self.profile, self.safety, self._observer,
                                       None, None)
        return self._actions_ro

    def fire_cob(self, cob: Any, target_row: int, target_col: float, *, check_ready: bool = True):
        """发炮: ``cob`` = plant index or 1-based ``(row, col)`` of the cannon's
        LEFT cell; target ``(row, col)`` 1-based, ``col`` may be fractional
        (``8.75``).  The cannon must be READY (state 37) unless
        ``check_ready=False``.  Returns a ``FireResult``."""
        self._need_remote("发炮 fire_cob()")
        return self.actions.fire_cob(cob, target_row, target_col, check_ready=check_ready)

    def fire_cobs(self, fires: Any, *, check_ready: bool = True):
        """瞬发多炮: ``[(cob, (row, col)), ...]`` in ONE remote call; returns a
        list of ``FireResult``."""
        self._need_remote("发炮 fire_cobs()")
        return self.actions.fire_cobs(fires, check_ready=check_ready)

    # -- executor (DESIGN §6 external backend) -------------------------------------
    @property
    def observer(self) -> Observer:
        """The read-only :class:`Observer` behind this Game."""
        return self._observer

    @property
    def input(self) -> bool:
        """``True`` when window-message input (``attach(input=True)``) is allowed."""
        return self._input and bool(getattr(self.safety, "allow_input", False))

    def _need_input(self, action: str) -> None:
        if not self.input:
            raise PermissionDenied("allow_input", action, _(_HINT_INPUT))

    @property
    def game_clock(self) -> Any:
        """A :class:`~pvztools.control.executor.GameClock` over this game (read only)."""
        if getattr(self, "_clock_obj", None) is None:
            from pvztools.control.executor.clock import GameClock

            self._clock_obj = GameClock(self._observer)
        return self._clock_obj

    def backend(self, mode: str = "input") -> Any:
        """An :class:`~pvztools.control.backends.ActionBackend`:
        ``'input'`` posts mouse messages (needs ``attach(input=True)``);
        ``'remote'`` calls game functions (needs ``attach(remote=True)``)."""
        if mode == "input":
            self._need_input("键控 input 后端")
            from pvztools.control.input_backend import InputBackend

            fd = 10
            try:
                fd = int(self.state.frame_duration or 10)
            except Exception:
                pass
            return InputBackend(self.session, self._observer, self.profile, self.safety, frame_ms=fd,
                                patches=self._patches if self._patch else None,
                                foreground=self._foreground, window=self.window)
        if mode == "remote":
            self._need_remote("remote 后端")
            from pvztools.control.backends import RemoteBackend

            return RemoteBackend(self.actions)
        raise ValueError("backend must be 'input' or 'remote', got %r" % (mode,))

    def expand_context(self, scene: Any = None, cobs: Any = None, **kw: Any) -> Any:
        """``ExpandContext`` from the live board: scene code from
        ``Board.scene``, cannon positions from :attr:`cobs` (left cell), wave
        count from ``total_waves``.  ``scene`` (code such as ``"PE"``) and
        ``cobs`` (``[(row, col), ...]`` or ``"25,45"``) override."""
        from pvztools.schema.enums import Scene
        from pvztools.schema.track.expand import ExpandContext

        b = self._board()
        code = Scene(int(b.scene)).code if scene is None else Scene.from_code(str(scene)).code
        if cobs is None:
            positions = tuple((c.row, c.col) for c in self.cobs)
        else:
            positions = tuple((r, int(c)) for r, c in _parse_cells(cobs))
        waves = int(b.total_waves) if b.total_waves and int(b.total_waves) > 0 else 20
        kw.setdefault("wave_count", waves)
        return ExpandContext(scene=code, cob_positions=positions, **kw)

    @staticmethod
    def load_track(track_or_path: Any, *, dialect: Optional[str] = None, fmt: str = "auto") -> Any:
        """A ``Track`` from a Track object, a file path or inline track text."""
        from pvztools.schema.track import Track

        if isinstance(track_or_path, Track):
            return track_or_path
        from pvztools.cli.__main__ import parse_track_text, read_track_arg

        return parse_track_text(read_track_arg(str(track_or_path)), dialect, fmt)

    def plan_track(self, track_or_path: Any, *, scene: Any = None, cobs: Any = None, **ctx_kw: Any) -> Any:
        """Expand a track into a ``Plan`` against the live board (read only)."""
        from pvztools.schema.track.expand import expand

        return expand(self.load_track(track_or_path), self.expand_context(scene, cobs, **ctx_kw))

    def run_track(self, track_or_path: Any, *, scene: Any = None, cobs: Any = None,
                  backend: str = "input", dry_run: bool = False, tolerance_cs: int = 2,
                  late_policy: str = "warn", lead_cs: Optional[int] = None,
                  on_event: Any = None, collector: Any = None, **ctx_kw: Any) -> Any:
        """Run a track on the live game clock; returns an ``ExecutionReport``.

        ``backend`` = ``'input'`` (键控: PostMessage clicks, ``attach(input=True)``)
        or ``'remote'`` (修改器: remote calls, ``attach(remote=True)``).
        ``dry_run=True`` waits for every action but calls no backend (no
        permission flag needed).  ``collector`` is an optional
        :class:`~pvztools.control.autocollect.AutoCollector` ticked on every
        clock poll.  Must be in a level.
        """
        from pvztools.control.executor.scheduler import PlannedExecutor

        self._board()
        plan = self.plan_track(track_or_path, scene=scene, cobs=cobs, **ctx_kw)
        if dry_run and not (self.input if backend == "input" else self.remote):
            be = _NullBackend(backend)  # rehearsal without the permission flag
        else:
            be = self.backend(backend)
        kw: Dict[str, Any] = dict(tolerance_cs=tolerance_cs, late_policy=late_policy, dry_run=dry_run)
        if lead_cs is not None:
            kw["lead_cs"] = lead_cs
        if collector is not None:
            kw["collector"] = collector
        ex = PlannedExecutor(self.game_clock, be, plan, **kw)
        if on_event is not None:
            ex.on_event(on_event)
        return ex.run()

    def run_reactive(self, policy: Any = "p6", cob_targets: Any = None, duration_waves: int = 20, *,
                     backend: str = "input", poll_ms: int = 5, on_error: str = "warn",
                     stop_wave: Optional[int] = None, **policy_kw: Any) -> Any:
        """Reactive execution (§6.2b): poll the board, let ``policy`` decide.

        ``policy`` is a name (``'p6'`` -> ``PSeriesPolicy``) or a policy
        object; ``cob_targets`` = landing cells ``[(row, col), ...]`` (or
        ``"29,59"``), default ``(2, 9) / (5, 9)`` on 6-row scenes and
        ``(2, 9) / (4, 9)`` otherwise.  Runs until ``Board.wave`` reaches
        ``stop_wave`` (default: current wave + ``duration_waves``, capped at
        ``total_waves``), i.e. it acts on the next ``duration_waves`` waves
        (the stop wave included).  Returns the ``ReactiveLog``; Ctrl-C / the
        level ending return the partial log (``log.stop_reason``).
        """
        from pvztools.control.executor import PolicyBackend, make_policy
        from pvztools.control.executor.reactive import ReactiveRunner
        from pvztools.schema.enums import Scene

        b = self._board()
        if cob_targets is None:
            rows = (2, 5) if Scene(int(b.scene)).rows == 6 else (2, 4)
            targets = [(rows[0], 9.0), (rows[1], 9.0)]
        else:
            targets = [(r, float(c)) for r, c in _parse_cells(cob_targets)]
        pol = make_policy(policy, targets, **policy_kw) if isinstance(policy, str) else policy
        be = PolicyBackend(self.backend(backend), self._observer)
        runner = ReactiveRunner(None, be, self._observer, pol, poll_ms=poll_ms, on_error=on_error)
        if stop_wave is None:
            stop_wave = int(b.wave) + int(duration_waves)
            if b.total_waves and int(b.total_waves) > 0:
                stop_wave = min(stop_wave, int(b.total_waves))
        return runner.run(stop_wave=stop_wave)  # acts on waves wave+1 .. stop_wave

    def bench_clock(self, seconds: float = 10.0, **kw: Any) -> Any:
        """Read-only clock benchmark (``pvztools bench-clock``); returns a
        ``BenchResult`` (``in_level=False`` when not in a level)."""
        from pvztools.control.executor.bench import bench_clock

        return bench_clock(self._observer, seconds, clock=self.game_clock, **kw)

    # -- window / seed chooser (input-v2) ----------------------------------------------
    @property
    def window(self) -> Any:
        """:class:`~pvztools.control.window.GameWindow` of this game: DPI-aware
        client size, click calibration, foreground / background-running
        handling, dialog closing.  Reading is always allowed; posting input
        needs ``attach(input=True)`` (``Safety.allow_input``)."""
        if self._window_obj is None:
            from pvztools.control.window import GameWindow

            self._window_obj = GameWindow(self.session, self._observer, self.profile,
                                          self._patches if self._patch else None, self.safety)
        return self._window_obj

    @property
    def chooser(self) -> Any:
        """:class:`~pvztools.control.chooser.SeedChooser` over :attr:`window`
        (seed chooser screen, ui 2): ``clear_bank`` / ``pick`` / ``start`` /
        ``select_and_start``.  Needs ``attach(input=True)``."""
        if self._chooser_obj is None:
            from pvztools.control.chooser import SeedChooser

            self._chooser_obj = SeedChooser(self.window, self._observer, foreground=self._foreground)
        return self._chooser_obj

    def set_seed_slots(self, types: Any) -> List[Tuple[int, int]]:
        """Rewrite the seed bank in memory: ``types`` = plant ids / names per
        slot (``[1, 16, 14, ...]``); extra entries beyond the bank size are
        ignored.  Needs ``write=True``.  Returns ``[(slot, id), ...]``."""
        self._need_write("写卡槽 set_seed_slots()")
        from pvztools.control.actions import resolve_plant

        ids = [resolve_plant(t) for t in types]
        n = self._writer.seed_count()
        out: List[Tuple[int, int]] = []
        for i, pid in enumerate(ids[:n]):
            self._writer.set_seed_type(i, pid)
            out.append((i, pid))
        return out

    def play_level(self, lineup: Any = "3C八炮", track: Any = "P4: PP|PP|PP|PP (869|869|869|869)",
                   seeds: Any = None, *, backend: str = "remote", cob_col: Optional[float] = None,
                   slot_types: Any = None, place: bool = True, dry_run: bool = False,
                   settle_s: float = 0.8, on_event: Any = None, auto_collect: bool = True,
                   foreground: Optional[bool] = None, chooser: str = "auto",
                   finish_level: bool = True, wait_ui_s: float = 20.0, award_wait_s: float = 120.0,
                   track_label: Optional[str] = None, **run_kw: Any) -> Any:
        """One call from wherever the game is to a finished level
        (:class:`~pvztools.control.play.PlaySession`, DESIGN §6.3):

        0. window: bring the game to the front (``foreground``, default the
           ``attach(foreground=)`` flag; viewing only) or enable
           ``background_running`` when it stays unfocused;
        1. main menu (ui 1) -> adventure; award screen (ui 5) -> continue;
           seed chooser (ui 2): memory chooser (``remote=True``, GOTY 1096)
           or the click chooser (``input=True``) picks ``seeds`` (default
           :data:`DEFAULT_PLAY_SEEDS`) and starts;
        2. in the level (ui 3): ``place_lineup(lineup)`` (``remote=True``;
           ``place=False`` keeps the field), then ``set_seed_slots(slot_types)``
           when given (``write=True``);
        3. :meth:`run_track` with ``backend`` -- ``'remote'`` fires cobs
           frame-locked (frozen / fired tick in the report; a cannon that is
           not ready at its planned tick is fired when it becomes ready and
           the row is labelled ``cd-wait``) -- with an auto-collector
           (``auto_collect``) picking up sun / coins on every clock poll;
        4. when the level completes, the award screen is dismissed
           (``finish_level``).

        ``cob_col`` sets ``ExpandContext.default_cob_col`` (landing column of
        bare ``P`` symbols); other ``run_kw`` go to :meth:`run_track`.
        Returns the ``ExecutionReport`` (``meta`` carries lineup / track /
        scene / waves / the phase log; ``to_markdown()`` renders it).
        ``dry_run=True`` needs ui 3 and calls nothing."""
        from pvztools.control.play import PlaySession

        picks = list(DEFAULT_PLAY_SEEDS) if seeds is None else list(seeds)
        session = PlaySession(self, lineup=lineup, track=track, seeds=picks, cob_col=cob_col,
                              backend=backend, auto_collect=auto_collect,
                              foreground=self._foreground if foreground is None else bool(foreground),
                              chooser=chooser, slot_types=slot_types, place=place, dry_run=dry_run,
                              settle_s=settle_s, on_event=on_event, finish_level=finish_level,
                              wait_ui_s=wait_ui_s, award_wait_s=award_wait_s, track_label=track_label, **run_kw)
        return session.run()

    def campaign(self, n: int = 1, *, auto: bool = False, lineup: Any = None,
                 cycle: float = 869, on_event: Any = None, foreground: Optional[bool] = None,
                 **kw: Any) -> Any:
        """打连关: play several adventure levels back to back with the strict
        fixed-cycle cob runner (:class:`~pvztools.control.campaign.Campaign`,
        DESIGN §6.3).

        Plays ``n`` levels (``auto=True`` ignores ``n`` and plays until a
        non-adventure / non-plantable level appears, at which point it stops
        without forcing it -- a conveyor / mini-game needs a different
        handler).  ``lineup`` overrides the scene-default 八炮 lineup for every
        level; ``cycle`` is the fixed cob cycle in cs (869 by default).  Needs
        ``attach(write=True, remote=True)`` (memory chooser, lineup placement,
        frame-locked fires) and the seed chooser needs window input, so also
        ``input=True``.  Returns a
        :class:`~pvztools.control.campaign.CampaignReport`
        (``to_markdown()`` / ``to_json()``).
        """
        from pvztools.control.campaign import Campaign

        fg = self._foreground if foreground is None else bool(foreground)
        return Campaign(self, count=n, auto=auto, on_event=on_event, lineup=lineup,
                        cycle=cycle, foreground=fg, **kw).run()

    # -- diagnostics ----------------------------------------------------------------
    def resolution(self):
        if self._resolution is None:
            from pvztools.addr.resolve import resolve

            self._resolution = resolve(self.session)
        return self._resolution

    def doctor(self) -> str:
        """Version, capability grade and the address table with confidence."""
        from pvztools.addr.resolve import doctor_report

        return doctor_report(self.resolution())

    # -- lifecycle ------------------------------------------------------------------
    @property
    def closed(self) -> bool:
        return bool(getattr(self.session, "closed", False))

    def keep_patches(self) -> None:
        """Leave enabled code patches in place when this Game closes
        (by default they are restored, and also at interpreter exit)."""
        self._keep_patches = True
        pm = self._patches_obj
        if pm is not None:
            pm.forget()

    def close(self) -> None:
        """Restore the patches this Game enabled and release the process."""
        if self._patches_obj is not None and not getattr(self, "_keep_patches", False):
            try:
                self._patches_obj.restore()
            except Exception:
                pass
        _close_session(self.session)  # also releases the single-controller mutex

    def __enter__(self) -> "Game":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        try:
            where = describe_ui(self.ui)
        except Exception:
            where = "?"
        return "Game(pid=%s, version=%s, capability=%s, %s, %s)" % (
            getattr(self.session, "pid", "?"), self.version, self.capability,
            "rw" if self.writable else "ro", where)


class _NullBackend:
    """Backend for ``run_track(dry_run=True)`` without a granted backend:
    the scheduler never calls it, but it carries the ``mode`` label."""

    def __init__(self, mode: str) -> None:
        if mode not in ("input", "remote"):
            raise ValueError("backend must be 'input' or 'remote', got %r" % (mode,))
        self.mode = mode

    def _refuse(self, *a: Any, **k: Any) -> None:
        raise NotImplementedAction("dry-run backend cannot act")

    plant_card = shovel = fire_cob = fire_cobs = click = _refuse


def _parse_cells(value: Any) -> List[Tuple[int, Any]]:
    """``"25,45"`` / ``["25", "2-8.75"]`` / ``[(2, 5), ...]`` -> ``[(row, col), ...]``
    (``col`` is an int unless fractional)."""
    if isinstance(value, str):
        value = [t for t in value.replace(";", ",").split(",") if t.strip()]
    out: List[Tuple[int, Any]] = []
    for item in value:
        if isinstance(item, (tuple, list)):
            r, c = item
        else:
            t = str(item).strip()
            if "-" in t:
                r, c = t.split("-", 1)
            elif len(t) >= 2 and t[0].isdigit():
                r, c = t[0], t[1:]
            else:
                raise ValueError("bad cell %r (expected <row><col> like 25 or 2-8.75)" % (item,))
        col = float(c)
        out.append((int(r), int(col) if col.is_integer() else col))
    return out


def attach(pid: Optional[int] = None, *, write: bool = False, profile: Any = None,
           patch: Optional[bool] = None, remote: bool = False, input: bool = False,
           foreground: bool = True) -> Game:
    """Find the game, open it and return a :class:`Game`.

    - ``pid=None`` looks for ``popcapgame1.exe`` / ``PlantsVsZombies.exe``;
      raises :class:`GameNotRunning` (游戏没有运行) or :class:`MultipleGames`.
    - ``write=True`` opens the process for writing and allows memory writes
      *and* code patches (``patch=False`` to allow writes only).
    - ``patch=True`` alone opens the process for writing but grants only code
      patches (``Safety.allow_write`` stays False).
    - ``remote=True`` additionally allows remote calls (shellcode injection:
      ``plant`` / ``shovel`` / ``place_lineup`` / ``spawn_reroll``); it
      implies ``write`` and ``patch`` (``allow_remote_call`` + ``allow_patch``
      + ``allow_write``) and opens the process with thread-creation rights.
    - ``input=True`` allows the 键控 input backend (``WM_LBUTTONDOWN/UP``
      posted to the game window: ``run_track(backend='input')``,
      ``backend('input')``); it writes no memory and needs no other flag.
    - ``foreground=False`` stops the input backend / chooser from pulling
      the game window to the front before posting (then the
      ``background_running`` patch is needed, or the game pauses).
    - Any of ``write`` / ``patch`` / ``remote`` / ``input`` takes the
      single-controller mutex; when another pvztools instance already
      controls this game :class:`~pvztools.process.ControllerBusy` is
      raised (已有另一个 pvztools 实例正在控制这个游戏).  The mutex is
      released by :meth:`Game.close` and when this process exits.
    - ``profile`` overrides version detection: a :class:`Profile`, a profile
      id such as ``"goty_1_2_0_1096_en"`` or a JSON path.  An unknown id /
      missing file raises :class:`UnknownBuild`.
    """
    # single-controller mutex: any control right (write / patch / remote /
    # input) takes Local\\pvztools-controller-<pid>; a second controlling
    # instance gets ControllerBusy.  Pure read-only attach never locks.
    controlling = bool(write or patch or remote or input)
    want_write = bool(write or patch or remote)
    session = _process_attach(pid, write=want_write)  # locks itself when write=True
    try:
        if controlling and hasattr(session, "lock") and session.lock is None:
            # input-only control: the read-only session did not lock; do it here
            # (raises ControllerBusy) and hand the lock to the session so
            # Session.close releases it.  Objects without a ``lock`` slot
            # (test fakes) are not real processes and are left alone.
            session.lock = _acquire_lock(int(session.pid))
        prof = _load_profile_arg(profile, session)
        if remote:
            _grant_remote_handle(session)
    except Exception:
        _close_session(session)
        raise
    return Game(session, prof, write=write or remote, patch=patch, remote=remote, input=input,
                foreground=foreground)


def _close_session(session: Any) -> None:
    """``session.close()`` plus releasing a lock attached to a fake session."""
    close = getattr(session, "close", None)
    if close is not None:
        try:
            close()
        except Exception:
            pass
    lk = getattr(session, "lock", None)
    if lk is not None and hasattr(lk, "release"):
        lk.release()


def _grant_remote_handle(session: Any) -> None:
    """Re-open the process with ``PROCESS_CREATE_THREAD`` for the executor.

    :func:`pvztools.process.attach` opens the game without thread-creation
    rights; the read / write closures keep their original handle, only
    ``session.handle`` (used by VirtualAllocEx / CreateRemoteThread) is
    replaced.  Both handles are closed by ``session.close()``.  Fake sessions
    (no ``_close``) are left alone."""
    if getattr(session, "_close", None) is None or not getattr(session, "pid", None):
        return
    from pvztools.process.attach import _kernel32

    k = _kernel32()
    new_h = k.open_process(session.pid, write=True, create_thread=True)
    old_h, old_close = session.handle, session._close

    def close_both(h: int, _old=old_h, _close=old_close) -> None:
        for hh in (h, _old):
            try:
                _close(hh)
            except Exception:
                pass

    session.handle = new_h
    session._close = close_both
