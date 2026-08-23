"""Single-field / batch memory writes (parity §4.1–§4.3, "低" level).

:class:`Writer` resolves every target through the address profile
(``profile.globals.lawn_app`` → LawnApp → Board → field), asks
:class:`~pvztools.modifier.safety.Safety` for permission / whitelist /
range / ui checks, journals the mutation and returns a :class:`Change`
carrying the previous value with a ``revert()`` method.

Nothing here patches code or calls into the game; see ``patches.py`` for
code patches.  Remote calls (put_plant, ...) are out of scope.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .safety import (
    CHEAT_FLAGS,
    CHEAT_FLAGS_BASE,
    GameStateError,
    Safety,
    SafetyError,
    WhitelistError,
)

__all__ = ["Writer", "Change", "WriteError", "FREEZE_COUNTDOWN_VALUE", "ZOMBIE_STATUS_DYING"]

#: Toolkit's "kill all zombies" writes status 3 (dying) into live zombies.
ZOMBIE_STATUS_DYING = 3
#: Value written into ``board.countdown`` to hold the next wave back.
FREEZE_COUNTDOWN_VALUE = 999_999
#: Default frame duration (ms) — game speed 1x.
DEFAULT_FRAME_DURATION = 10


class WriteError(SafetyError):
    """A write could not be performed / verified."""


@dataclass
class Change:
    """Record of one field write.  ``int(change)`` / ``change.old`` is the
    previous value; ``revert()`` writes it back (through the same safety)."""
    name: str
    addr: int
    old: int
    new: int
    width: int = 4
    signed: bool = True
    dry_run: bool = False
    reverted: bool = False
    _writer: Optional["Writer"] = field(default=None, repr=False, compare=False)

    def __int__(self) -> int:
        return self.old

    def __index__(self) -> int:
        return self.old

    def revert(self) -> "Change":
        if self.reverted:
            return self
        if self._writer is None:
            raise WriteError("Change %s 没有关联 Writer，无法回滚" % self.name)
        self._writer._write(self.name, self.addr, self.old, self.width, self.signed,
                            level=None, kind="revert", check_range=False)
        self.reverted = True
        return self


class Writer:
    """Field writer bound to ``(session, profile, safety)``."""

    def __init__(self, session: Any, profile: Any, safety: Optional[Safety] = None) -> None:
        self.session = session
        self.profile = profile
        self.safety = safety if safety is not None else Safety()
        self.safety.bind_profile(profile)
        self.changes: List[Change] = []

    # -- pointer resolution ----------------------------------------------------
    def lawn(self) -> int:
        lawn = self.session.read_ptr(self.profile.globals.lawn_app)
        if not lawn:
            raise GameStateError("LawnApp 指针为空：游戏尚未初始化")
        return lawn

    def board(self) -> int:
        board = self.session.read_ptr(self.lawn() + self.profile.lawn_app.board)
        if not board:
            raise GameStateError("Board 指针为空：当前不在关卡中")
        return board

    def _field(self, section: str, name: str) -> int:
        sec = getattr(self.profile, section)
        off = sec.get(name)
        if off is None:
            raise WhitelistError("%s.%s 在 profile %s 中不可用" % (section, name, self.profile.id))
        return off

    def _obj(self, obj: str, fld: str) -> int:
        lay = self.profile.objects.get(obj)
        if lay is None or lay.fields.get(fld) is None:
            raise WhitelistError("objects.%s.%s 在 profile %s 中不可用" % (obj, fld, self.profile.id))
        return lay.fields[fld]

    def _stride(self, obj: str) -> int:
        lay = self.profile.objects.get(obj)
        if lay is None or not lay.stride:
            raise WhitelistError("objects.%s.stride 在 profile %s 中不可用" % (obj, self.profile.id))
        return lay.stride

    # -- primitive ---------------------------------------------------------------
    def _read(self, addr: int, width: int, signed: bool) -> int:
        raw = self.session.read(addr, width)
        fmt = {1: "b" if signed else "B", 2: "h" if signed else "H", 4: "i" if signed else "I"}[width]
        return struct.unpack("<" + fmt, raw)[0]

    def _pack(self, value: int, width: int, signed: bool) -> bytes:
        fmt = {1: "b" if signed else "B", 2: "h" if signed else "H", 4: "i" if signed else "I"}[width]
        return struct.pack("<" + fmt, value)

    def _write(self, name: str, addr: int, value: int, width: Optional[int] = None,
               signed: bool = True, level: Optional[str] = None, kind: str = "write",
               check_range: bool = True) -> Change:
        """The single choke point every mutation goes through."""
        s = self.safety
        if isinstance(value, bool):
            value = int(value)
        if not isinstance(value, int):
            raise WriteError("%s: 值必须是整数，得到 %r" % (name, value))
        base = s.check_whitelist(name)
        if width is None:
            width = s.width_of(base)
        if width == 1:
            signed = False
        if level is None:
            level = s.level_of(base)
        s.require_level(level, "%s %s" % (kind, name))
        s.require_capability("%s %s" % (kind, name), self.profile)
        s.require_session_writable(self.session, "%s %s" % (kind, name))
        if check_range:
            s.check_range(name, value, width, signed)
        else:
            # revert of a previously-read value: only the raw width limit applies
            lo, hi =((0, (1 << (8 * width)) - 1) if not signed
                      else (-(1 << (8 * width - 1)), (1 << (8 * width - 1)) - 1))
            if not (lo <= value <= hi):
                raise WriteError("%s: 回滚值 %r 超出 %d 字节宽度" % (name, value, width))
        old = self._read(addr, width, signed)
        if not s.dry_run:
            self.session.write(addr, self._pack(value, width, signed))
            back = self._read(addr, width, signed)
            if back != value:
                s.log(kind, name, addr, old, back, width)
                raise WriteError("%s @0x%x 写后回读 %r != %r" % (name, addr, back, value))
        s.log(kind, name, addr, old, value, width)
        ch = Change(name, addr, old, value, width, signed, dry_run=s.dry_run, _writer=self)
        if kind != "revert":
            self.changes.append(ch)
        return ch

    def revert_all(self) -> List[Change]:
        """Revert every non-reverted change made by this writer, newest first."""
        done = []
        for ch in reversed(self.changes):
            if not ch.reverted:
                ch.revert()
                done.append(ch)
        return done

    # -- board / lawn fields -------------------------------------------------------
    def set_board_field(self, name: str, value: int, *, require_ui: bool = True) -> Change:
        """Write a dword / byte field of Board by semantic name (``"sun"``,
        ``"countdown"``, ``"debug_mode"`` ...)."""
        off = self._field("board", name)
        if require_ui:
            self.safety.require_ui(self.session, self.profile, (2, 3), "写 board.%s" % name)
        return self._write("board." + name, self.board() + off, value)

    def set_lawn_field(self, name: str, value: int) -> Change:
        off = self._field("lawn_app", name)
        return self._write("lawn_app." + name, self.lawn() + off, value)

    def set_sun(self, value: int) -> Change:
        """Set sun (0..9990, or up to 999999 with ``sun_cap_unlock`` active)."""
        return self.set_board_field("sun", value)

    def set_game_speed(self, frame_duration: int = DEFAULT_FRAME_DURATION) -> Change:
        """``lawn_app.frame_duration`` in ms per frame (10 = 1x, 5 = 2x, 20 = 0.5x)."""
        return self.set_lawn_field("frame_duration", frame_duration)

    def free_planting(self, on: bool = True) -> Change:
        """LawnApp free-planting flag (cards cost nothing)."""
        self.safety.require_ui(self.session, self.profile, (2, 3), "free_planting")
        return self.set_lawn_field("free_planting", 1 if on else 0)

    def freeze_countdown(self, on: bool = True, value: int = FREEZE_COUNTDOWN_VALUE) -> Change:
        """Hold the next wave back by writing a large ``board.countdown``.

        This is a *delay* (the counter keeps ticking from ``value``); the
        real freeze is the ``pause_spawning`` patch.  ``on=False`` restores the
        value recorded by the last ``on`` call (or ``countdown_start``)."""
        off = self._field("board", "countdown")
        self.safety.require_ui(self.session, self.profile, (3,), "freeze_countdown")
        addr = self.board() + off
        if on:
            ch = self._write("board.countdown", addr, value)
            self._frozen = ch
            return ch
        prev = getattr(self, "_frozen", None)
        if prev is not None and not prev.reverted:
            self._frozen = None
            return prev.revert()
        start_off = self.profile.board.get("countdown_start")
        restore = self._read(self.board() + start_off, 4, True) if start_off is not None else 0
        return self._write("board.countdown", addr, max(0, restore))

    # -- cheat flags -----------------------------------------------------------------
    def cheat_flag_addr(self, name: str) -> int:
        if name not in CHEAT_FLAGS:
            raise WhitelistError("未知作弊标志 %r；可用：%s" % (name, ", ".join(CHEAT_FLAGS)))
        base = self.profile.board.get("cheats")
        if base is None:
            base = CHEAT_FLAGS_BASE.get(self.profile.family)
        if base is None:
            raise WhitelistError("profile %s（family %s）没有 board.cheats 偏移"
                                 % (self.profile.id, self.profile.family))
        return self.board() + base + CHEAT_FLAGS.index(name)

    def set_cheat_flag(self, name: str, on: bool = True) -> Change:
        """Board+0x5761.. cheat booleans (mustache / trickedout / future /
        pinata / dance / daisies / sukhbir)."""
        self.safety.require_ui(self.session, self.profile, (2, 3), "set_cheat_flag %s" % name)
        return self._write("board.cheats.%s" % name, self.cheat_flag_addr(name), 1 if on else 0, 1)

    # -- seed bank -------------------------------------------------------------------
    def seed_bank(self) -> int:
        """``board.seed_bank`` holds a *pointer* to the SeedBank object
        (verified live on 1096: Board+0x15C -> heap object with count at +0x24)."""
        bank = self.session.read_ptr(self.board() + self._field("board", "seed_bank"))
        if not bank:
            raise GameStateError("卡槽对象指针为空（尚未进入关卡？）")
        return bank

    def seed_count(self) -> int:
        return self._read(self.seed_bank() + self._obj("seed_bank", "count"), 4, True)

    def seed_packet(self, slot: int) -> int:
        count = self.seed_count()
        if not (0 <= slot < count):
            raise GameStateError("卡槽 %d 不存在（当前 %d 个卡槽）" % (slot, count))
        return self.seed_bank() + self._obj("seed_bank", "packets") + slot * self._stride("seed_packet")

    def set_seed_cd(self, slot: int, elapsed: Optional[int] = None) -> Change:
        """Set ``seed_packet.cd_elapsed`` of ``slot`` (default = ``cd_total``,
        i.e. cooldown finished)."""
        self.safety.require_ui(self.session, self.profile, (2, 3), "set_seed_cd")
        pkt = self.seed_packet(slot)
        if elapsed is None:
            elapsed = self._read(pkt + self._obj("seed_packet", "cd_total"), 4, True)
        return self._write("objects.seed_packet[%d].cd_elapsed" % slot,
                           pkt + self._obj("seed_packet", "cd_elapsed"), elapsed)

    def set_seed_type(self, slot: int, plant_type: int, imitater_type: Optional[int] = None) -> List[Change]:
        """Replace the card in ``slot`` (``seed_packet.type``; for the
        Imitater (48) also ``imitater_type``).  The game redraws the packet
        itself; cooldown / sun rules of the new card apply as usual."""
        self.safety.require_ui(self.session, self.profile, (2, 3), "set_seed_type")
        pkt = self.seed_packet(slot)
        out = [self._write("objects.seed_packet[%d].type" % slot, pkt + self._obj("seed_packet", "type"),
                           int(plant_type))]
        if imitater_type is not None:
            out.append(self._write("objects.seed_packet[%d].imitater_type" % slot,
                                   pkt + self._obj("seed_packet", "imitater_type"), int(imitater_type)))
        return out

    def no_cooldown(self, on: bool = True) -> List[Change]:
        """``on``: finish every slot's cooldown now (one pass; call again after
        each planting, or combine with ``free_planting``).  ``off``: revert the
        changes made by the last ``on`` call."""
        if not on:
            prev = getattr(self, "_no_cd", [])
            self._no_cd = []
            return [c.revert() for c in reversed(prev) if not c.reverted]
        self.safety.require_ui(self.session, self.profile, (2, 3), "no_cooldown")
        out = [self.set_seed_cd(i) for i in range(self.seed_count())]
        self._no_cd = out
        return out

    # -- plants / zombies --------------------------------------------------------------
    def plant_addr(self, index: int) -> int:
        board = self.board()
        count = self._read(board + self._field("board", "plant_count_max"), 4, True)
        if not (0 <= index < count):
            raise GameStateError("植物下标 %d 越界（plant_count_max=%d）" % (index, count))
        arr = self.session.read_ptr(board + self._field("board", "plants"))
        if not arr:
            raise GameStateError("植物数组指针为空")
        return arr + index * self._stride("plant")

    def set_plant_hp(self, index: int, hp: int) -> Change:
        self.safety.require_ui(self.session, self.profile, (2, 3), "set_plant_hp")
        p = self.plant_addr(index)
        if self._read(p + self._obj("plant", "dead"), 1, False):
            raise GameStateError("植物 %d 已死亡 / 槽位空闲" % index)
        return self._write("objects.plant[%d].hp" % index, p + self._obj("plant", "hp"), hp)

    def iter_zombies(self) -> List[int]:
        """Addresses of live zombie slots (``dead`` byte == 0)."""
        board = self.board()
        count = self._read(board + self._field("board", "zombie_count_max"), 4, True)
        arr = self.session.read_ptr(board + self._field("board", "zombies"))
        if not arr or count <= 0:
            return []
        stride, dead = self._stride("zombie"), self._obj("zombie", "dead")
        out = []
        for i in range(min(count, 1024)):
            z = arr + i * stride
            if self._read(z + dead, 1, False) == 0:
                out.append(z)
        return out

    def kill_all_zombies(self) -> List[Change]:
        """Toolkit way: write ``status = 3`` (dying) into every live zombie.
        Batch write → "medium" level (still ``allow_write``)."""
        self.safety.require_ui(self.session, self.profile, (3,), "kill_all_zombies")
        status = self._obj("zombie", "status")
        board = self.board()
        arr = self.session.read_ptr(board + self._field("board", "zombies"))
        stride = self._stride("zombie")
        out = []
        for z in self.iter_zombies():
            idx = (z - arr) // stride
            cur = self._read(z + status, 4, True)
            if cur == ZOMBIE_STATUS_DYING:
                continue
            out.append(self._write("objects.zombie[%d].status" % idx, z + status,
                                   ZOMBIE_STATUS_DYING, 4, True, level="medium"))
        return out

    def __repr__(self) -> str:
        return "Writer(profile=%s, changes=%d, %r)" % (self.profile.id, len(self.changes), self.safety)
