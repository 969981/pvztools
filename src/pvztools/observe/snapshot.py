"""Read-only observer: Profile + Session -> typed snapshots (DESIGN §4).

Every offset comes from the :class:`~pvztools.addr.Profile` (semantic names,
e.g. ``profile.board.sun``); nothing is hardcoded here.  Each accessor issues
as few ``ReadProcessMemory`` calls as possible:

* :meth:`Observer.game`      2 reads (LawnApp pointer, LawnApp header)
* :meth:`Observer.board`     1 read  (Board header block)
* :meth:`Observer.plants`    1 read  (whole plant array, ``count_max * stride``)
* :meth:`Observer.zombies`   1 read  (whole zombie array)
* :meth:`Observer.projectiles` / :meth:`Observer.coins`  1 read each
* :meth:`Observer.seeds`     1 read  (SeedBank header + all packets)
* :meth:`Observer.spawn_list` 1 read (the 20x50 table sits inside the header block)

:meth:`Observer.snapshot` therefore costs 6 reads (plus 1 when the board
pointer has to be resolved first).  The session only needs a
``read(addr, n) -> bytes`` method; ``pvztools.process.Session`` and any test
double qualify.
"""
from __future__ import annotations

import struct
from typing import Any, Callable, List, Optional, Tuple

from ..addr.profile import Profile, Section
from ..schema.enums import PlantType
from ..model.types import (
    Board,
    Cheats,
    Coin,
    GameState,
    Plant,
    Projectile,
    Seed,
    Snapshot,
    SpawnList,
    Zombie,
    plant_name_zh,
    zombie_name_zh,
)
from . import spawn as _spawn

__all__ = ["Observer", "ObserveError", "NotInLevel", "MAX_SEED_SLOTS", "MAX_ARRAY_COUNT"]

#: Seed bank never has more than 10 packets in official builds.
MAX_SEED_SLOTS = 10
#: Guard against a garbage count field (arrays are at most a few hundred).
MAX_ARRAY_COUNT = 4096

_CHEAT_NAMES = ("mustache", "trickedout", "future", "pinata", "dance", "daisies", "sukhbir")


class ObserveError(RuntimeError):
    """Base class for observation failures."""


class NotInLevel(ObserveError):
    """Raised by board-dependent reads when no Board exists."""

    def __init__(self, msg: str = "请先进入关卡") -> None:
        super().__init__(msg)


def _i32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<i", buf, off)[0]


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _f32(buf: bytes, off: int) -> float:
    return struct.unpack_from("<f", buf, off)[0]


def _u8(buf: bytes, off: int) -> int:
    return buf[off]


def _extent(section: Section, names: Tuple[str, ...], width: int = 4) -> int:
    """Bytes needed to cover every *available* field in ``names``."""
    top = 0
    for n in names:
        v = section.get(n)
        if v is not None:
            top = max(top, v + width)
    return top


def _opt_int(section: Section, name: str, buf: bytes, width: int = 4) -> Optional[int]:
    off = section.get(name)
    if off is None or off + width > len(buf):
        return None
    return _i32(buf, off) if width == 4 else _u8(buf, off)


def _opt_bool(section: Section, name: str, buf: bytes) -> Optional[bool]:
    v = _opt_int(section, name, buf, 1)
    return None if v is None else bool(v)


def _opt_f32(section: Section, name: str, buf: bytes) -> Optional[float]:
    off = section.get(name)
    if off is None or off + 4 > len(buf):
        return None
    return _f32(buf, off)


class _Slot:
    """One array element: ``Section`` offsets + the element's bytes."""

    __slots__ = ("fields", "buf")

    def __init__(self, fields: Section, buf: bytes) -> None:
        self.fields = fields
        self.buf = buf

    def i32(self, name: str) -> Optional[int]:
        return _opt_int(self.fields, name, self.buf)

    def u8(self, name: str) -> Optional[bool]:
        return _opt_bool(self.fields, name, self.buf)

    def f32(self, name: str) -> Optional[float]:
        return _opt_f32(self.fields, name, self.buf)


#: Board.mIceMinX is int[6] (one entry per lawn row).
ICE_MIN_X_ROWS = 6


class Observer:
    """Read-only view of a running game.

    ``session`` must provide ``read(addr, n) -> bytes`` (raising on failure);
    ``profile`` supplies every offset.  The observer keeps no cache: each
    call re-reads the game, so successive calls see fresh state.
    """

    def __init__(self, session: Any, profile: Profile) -> None:
        self.session = session
        self.profile = profile
        self.last_spawn_note: Optional[str] = None
        self._read: Callable[[int, int], bytes] = session.read

        la, bd = profile.lawn_app, profile.board
        self._lawn_names = ("board", "game_mode", "game_ui", "frame_duration")
        self._lawn_len = _extent(la, self._lawn_names)
        self._board_names = (
            "sun", "scene", "adventure_level", "paused", "main_counter", "global_counter",
            "wave", "wave_total", "countdown", "countdown_start", "huge_wave_countdown",
            "wave_hp_threshold", "wave_hp", "zombies", "zombie_count_max", "plants",
            "plant_count_max", "seed_bank", "spawn_list",
            "projectiles", "projectile_count_max", "coins", "coin_count_max",
            "time_stop_counter", "board_rand_seed", "total_spawned_waves",
            "zombie_health_wave_start", "level_complete", "cob_cursor_delay",
        ) + tuple("cheat_" + c for c in _CHEAT_NAMES)
        # the spawn table lives inside the Board, so one header read covers it
        self._board_len = max(_extent(bd, self._board_names),
                              _extent(bd, ("ice_min_x",), 4 * ICE_MIN_X_ROWS),
                              bd.spawn_list + _spawn.SPAWN_LIST_BYTES)

        self._plant = profile.objects["plant"]
        self._zombie = profile.objects["zombie"]
        self._projectile = profile.objects.get("projectile")
        self._coin = profile.objects.get("coin")
        self._seed_bank = profile.objects.get("seed_bank")
        self._seed_packet = profile.objects.get("seed_packet")

    # -- low level -----------------------------------------------------------
    def lawn_ptr(self) -> int:
        """Pointer to the LawnApp singleton (0 before the game initialises)."""
        return _u32(self._read(self.profile.globals.lawn_app, 4), 0)

    def board_ptr(self, lawn: Optional[int] = None) -> int:
        """Board pointer (0 when not in a level); 1 read given ``lawn``."""
        if lawn is None:
            lawn = self.lawn_ptr()
        if lawn == 0:
            return 0
        return _u32(self._read(lawn + self.profile.lawn_app.board, 4), 0)

    def _require_board(self, board: Optional[int] = None) -> int:
        if board is None:
            board = self.board_ptr()
        if board == 0:
            raise NotInLevel()
        return board

    # -- game -----------------------------------------------------------------
    def game(self) -> GameState:
        """LawnApp-level state.  Never raises for "not in level"
        (``board_ptr == 0``)."""
        p = self.profile
        lawn = self.lawn_ptr()
        if lawn == 0:
            return GameState(p.id, p.capability, 0, 0, 0, 0, 0)
        hdr = self._read(lawn, self._lawn_len)
        la = p.lawn_app
        fd = la.get("frame_duration")
        return GameState(
            version=p.id,
            capability=p.capability,
            ui=_i32(hdr, la.game_ui),
            mode=_i32(hdr, la.game_mode),
            frame_duration=_i32(hdr, fd) if fd is not None else 0,
            board_ptr=_u32(hdr, la.board),
            lawn_ptr=lawn,
        )

    # -- board ----------------------------------------------------------------
    def _board_header(self, board: Optional[int] = None) -> Tuple[int, bytes]:
        board = self._require_board(board)
        return board, self._read(board, self._board_len)

    def _parse_board(self, hdr: bytes) -> Board:
        bd = self.profile.board

        def req(name: str) -> int:
            off = bd.get(name)
            return _i32(hdr, off) if off is not None else 0

        cheats = None
        cheat_offs = [bd.get("cheat_" + c) for c in _CHEAT_NAMES]
        if all(o is not None for o in cheat_offs):
            cheats = Cheats(*[bool(_u8(hdr, o)) for o in cheat_offs])  # type: ignore[arg-type]

        paused_off = bd.get("paused")
        ice_off = bd.get("ice_min_x")
        ice_min_x = None
        if ice_off is not None and ice_off + 4 * ICE_MIN_X_ROWS <= len(hdr):
            ice_min_x = tuple(_i32(hdr, ice_off + 4 * r) for r in range(ICE_MIN_X_ROWS))
        return Board(
            sun=req("sun"),
            scene=req("scene"),
            level=req("adventure_level"),
            paused=bool(_u8(hdr, paused_off)) if paused_off is not None else False,
            clock=req("main_counter"),
            global_clock=req("global_counter"),
            wave=req("wave"),
            total_waves=req("wave_total"),
            countdown=req("countdown"),
            countdown_init=req("countdown_start"),
            huge_wave_countdown=req("huge_wave_countdown"),
            hp_threshold=req("wave_hp_threshold"),
            wave_hp=_opt_int(bd, "wave_hp", hdr),
            cheats=cheats,
            time_stop_counter=_opt_int(bd, "time_stop_counter", hdr),
            board_rand_seed=_opt_int(bd, "board_rand_seed", hdr),
            total_spawned_waves=_opt_int(bd, "total_spawned_waves", hdr),
            zombie_health_wave_start=_opt_int(bd, "zombie_health_wave_start", hdr),
            level_complete=_opt_bool(bd, "level_complete", hdr),
            cob_cursor_delay=_opt_int(bd, "cob_cursor_delay", hdr),
            ice_min_x=ice_min_x,
        )

    def board(self) -> Board:
        """Board header values; raises :class:`NotInLevel` outside a level."""
        _, hdr = self._board_header()
        return self._parse_board(hdr)

    # -- arrays ---------------------------------------------------------------
    def _array(self, hdr: bytes, ptr_name: str, count_name: str, stride: int) -> Tuple[int, int, bytes]:
        bd = self.profile.board
        ptr_off, count_off = bd.get(ptr_name), bd.get(count_name)
        if ptr_off is None or count_off is None:
            return 0, 0, b""
        base = _u32(hdr, ptr_off)
        count = _i32(hdr, count_off)   # DataArray.mMaxUsedCount (+4)
        if base == 0 or count <= 0:
            return base, 0, b""
        if count > MAX_ARRAY_COUNT:
            raise ObserveError("%s count %d 不合理（偏移表可能不匹配）" % (ptr_name, count))
        return base, count, self._read(base, count * stride)

    def _parse_plants(self, hdr: bytes, include_dead: bool) -> Tuple[Plant, ...]:
        lay = self._plant
        f = lay.fields
        stride = lay.stride
        _, count, data = self._array(hdr, "plants", "plant_count_max", stride)
        out: List[Plant] = []
        imit_off, serial_off = f.get("imitater_type"), f.get("serial")
        squished_off, asleep_off = f.get("squished"), f.get("asleep")
        for i in range(count):
            b = i * stride
            dead = bool(data[b + f.dead])
            if dead and not include_dead:
                continue
            t = _i32(data, b + f.type)
            slot = _Slot(f, data[b:b + stride])
            out.append(Plant(
                index=i,
                row=_i32(data, b + f.row),
                col=_i32(data, b + f.col),
                type=t,
                name_zh=plant_name_zh(t),
                state=_i32(data, b + f.state),
                hp=_i32(data, b + f.hp),
                max_hp=_i32(data, b + f.max_hp),
                dead=dead,
                squished=bool(data[b + squished_off]) if squished_off is not None else False,
                asleep=bool(data[b + asleep_off]) if asleep_off is not None else False,
                imitater=(_i32(data, b + imit_off) == int(PlantType.IMITATER)) if imit_off is not None else False,
                serial=_i32(data, b + serial_off) if serial_off is not None else None,
                state_countdown=slot.i32("state_countdown"),
                shooting_counter=slot.i32("shooting_counter"),
                target_x=slot.i32("target_x"),
                target_y=slot.i32("target_y"),
                disappear_countdown=slot.i32("disappear_countdown"),
                do_special_countdown=slot.i32("do_special_countdown"),
            ))
        return tuple(out)

    def _parse_zombies(self, hdr: bytes, include_dead: bool) -> Tuple[Zombie, ...]:
        lay = self._zombie
        f = lay.fields
        stride = lay.stride
        _, count, data = self._array(hdr, "zombies", "zombie_count_max", stride)
        out: List[Zombie] = []
        for i in range(count):
            b = i * stride
            dead = bool(data[b + f.dead])
            if dead and not include_dead:
                continue
            t = _i32(data, b + f.type)
            slot = _Slot(f, data[b:b + stride])
            out.append(Zombie(
                index=i,
                row=_i32(data, b + f.row),
                type=t,
                name_zh=zombie_name_zh(t),
                status=_i32(data, b + f.status),
                x=_f32(data, b + f.x),
                y=_f32(data, b + f.y),
                speed=_f32(data, b + f.speed),
                hp=_i32(data, b + f.hp),
                max_hp=_i32(data, b + f.max_hp),
                helmet_hp=_i32(data, b + f.helmet_hp),
                shield_hp=_i32(data, b + f.shield_hp),
                dead=dead,
                from_wave=slot.i32("from_wave"),
                chilled_counter=slot.i32("chilled_counter"),
                buttered_counter=slot.i32("buttered_counter"),
                ice_trap_counter=slot.i32("ice_trap_counter"),
                mind_controlled=slot.u8("mind_controlled"),
                has_head=slot.u8("has_head"),
                has_arm=slot.u8("has_arm"),
                has_object=slot.u8("has_object"),
                in_pool=slot.u8("in_pool"),
                helm_type=slot.i32("helm_type"),
                helmet_max_hp=slot.i32("helmet_max_hp"),
                shield_type=slot.i32("shield_type"),
                shield_max_hp=slot.i32("shield_max_hp"),
                target_col=slot.i32("target_col"),
                target_row=slot.i32("target_row"),
                phase_counter=slot.i32("phase_counter"),
                altitude=slot.f32("altitude"),
            ))
        return tuple(out)

    def _parse_projectiles(self, hdr: bytes, include_dead: bool) -> Tuple[Projectile, ...]:
        lay = self._projectile
        if lay is None or not lay.stride or lay.fields.get("dead") is None:
            return ()
        f, stride = lay.fields, lay.stride
        _, count, data = self._array(hdr, "projectiles", "projectile_count_max", stride)
        out: List[Projectile] = []
        for i in range(count):
            b = i * stride
            dead = bool(data[b + f.dead])
            if dead and not include_dead:
                continue
            slot = _Slot(f, data[b:b + stride])
            out.append(Projectile(
                index=i,
                type=slot.i32("type") or 0,
                row=slot.i32("row") or 0,
                pos=(slot.f32("pos_x") or 0.0, slot.f32("pos_y") or 0.0, slot.f32("pos_z") or 0.0),
                vel=(slot.f32("vel_x") or 0.0, slot.f32("vel_y") or 0.0, slot.f32("vel_z") or 0.0),
                dead=dead,
                cob_target_x=slot.f32("cob_target_x"),
                cob_target_row=slot.i32("cob_target_row"),
                age=slot.i32("age"),
                motion_type=slot.i32("motion_type"),
            ))
        return tuple(out)

    def _parse_coins(self, hdr: bytes, include_dead: bool) -> Tuple[Coin, ...]:
        lay = self._coin
        if lay is None or not lay.stride or lay.fields.get("dead") is None:
            return ()
        f, stride = lay.fields, lay.stride
        _, count, data = self._array(hdr, "coins", "coin_count_max", stride)
        out: List[Coin] = []
        for i in range(count):
            b = i * stride
            dead = bool(data[b + f.dead])
            if dead and not include_dead:
                continue
            slot = _Slot(f, data[b:b + stride])
            out.append(Coin(
                index=i,
                type=slot.i32("type") or 0,
                pos=(slot.f32("pos_x") or 0.0, slot.f32("pos_y") or 0.0),
                dead=dead,
                collecting=slot.u8("is_being_collected"),
                age=slot.i32("coin_age"),
                usable_seed_type=slot.i32("usable_seed_type"),
                motion=slot.i32("motion"),
            ))
        return tuple(out)

    def projectiles(self, include_dead: bool = True) -> Tuple[Projectile, ...]:
        """All projectile slots (``projectile_count_max`` of them) in one
        read; ``()`` when the build has no projectile layout."""
        _, hdr = self._board_header()
        return self._parse_projectiles(hdr, include_dead)

    def coins(self, include_dead: bool = True) -> Tuple[Coin, ...]:
        """All coin / item slots (``coin_count_max`` of them) in one read;
        ``()`` when the build has no coin layout."""
        _, hdr = self._board_header()
        return self._parse_coins(hdr, include_dead)

    def plants(self, include_dead: bool = True) -> Tuple[Plant, ...]:
        """All plant slots (``plant_count_max`` of them) in one read."""
        _, hdr = self._board_header()
        return self._parse_plants(hdr, include_dead)

    def zombies(self, include_dead: bool = True) -> Tuple[Zombie, ...]:
        """All zombie slots (``zombie_count_max`` of them) in one read."""
        _, hdr = self._board_header()
        return self._parse_zombies(hdr, include_dead)

    # -- seeds ----------------------------------------------------------------
    def _parse_seeds(self, hdr: bytes) -> Tuple[Seed, ...]:
        if self._seed_bank is None or self._seed_packet is None or not self._seed_packet.stride:
            return ()
        sb, pk = self._seed_bank.fields, self._seed_packet
        bank = _u32(hdr, self.profile.board.seed_bank)
        if bank == 0:
            return ()
        stride = pk.stride
        blob = self._read(bank, sb.packets + MAX_SEED_SLOTS * stride)
        count = _i32(blob, sb.count)
        if count < 0 or count > MAX_SEED_SLOTS:
            raise ObserveError("seed_bank.count %d 不合理（偏移表可能不匹配）" % count)
        f = pk.fields
        out: List[Seed] = []
        for i in range(count):
            b = sb.packets + i * stride
            out.append(Seed(
                slot=i,
                type=_i32(blob, b + f.type),
                imitater_type=_i32(blob, b + f.imitater_type),
                cd_elapsed=_i32(blob, b + f.cd_elapsed),
                cd_total=_i32(blob, b + f.cd_total),
                usable=bool(blob[b + f.usable]),
            ))
        return tuple(out)

    def seeds(self) -> Tuple[Seed, ...]:
        """Seed-bank packets (``()`` when the build has no seed layout)."""
        _, hdr = self._board_header()
        return self._parse_seeds(hdr)

    # -- spawn list -----------------------------------------------------------
    def spawn_list_raw(self) -> bytes:
        board = self._require_board()
        return self._read(board + self.profile.board.spawn_list, _spawn.SPAWN_LIST_BYTES)

    def _parse_spawn(self, hdr: bytes, total_waves: int) -> Optional[SpawnList]:
        off = self.profile.board.spawn_list
        raw = hdr[off:off + _spawn.SPAWN_LIST_BYTES]
        self.last_spawn_note = _spawn.invalid_reason(raw)
        if self.last_spawn_note:
            return None
        sl = _spawn.decode(raw)
        # the table is not cleared between levels: rows past wave_total are stale
        if sl is not None and 0 < total_waves < len(sl.waves):
            sl = SpawnList(sl.waves[:total_waves])
        return sl

    def spawn_list(self) -> Optional[SpawnList]:
        """Decoded spawn list trimmed to ``Board.total_waves``, or ``None``
        (tutorial / not generated); the reason is kept in
        :attr:`last_spawn_note`."""
        _, hdr = self._board_header()
        return self._parse_spawn(hdr, self._parse_board(hdr).total_waves)

    def describe_spawn(self) -> str:
        sl = self.spawn_list()
        return _spawn.describe(sl) if sl is not None else "(无出怪表) " + (self.last_spawn_note or "")

    # -- bundle ---------------------------------------------------------------
    def snapshot(self, include_dead: bool = True) -> Snapshot:
        """Game + Board + plants + zombies + seeds (+ spawn list) in 6 reads.
        Raises :class:`NotInLevel` when no Board exists."""
        game = self.game()
        if game.board_ptr == 0:
            raise NotInLevel()
        hdr = self._read(game.board_ptr, self._board_len)
        board = self._parse_board(hdr)
        return Snapshot(
            game=game,
            board=board,
            plants=self._parse_plants(hdr, include_dead),
            zombies=self._parse_zombies(hdr, include_dead),
            seeds=self._parse_seeds(hdr),
            spawn=self._parse_spawn(hdr, board.total_waves),
        )
