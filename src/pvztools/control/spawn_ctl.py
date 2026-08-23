"""Spawn-list control (Toolkit §4.6 "自然出怪", own implementation).

* :meth:`SpawnControl.current` / :meth:`raw` -- the live 20x50 table with
  ``-1`` sentinel semantics (each wave row ends at its first ``-1``; bytes
  after it are stale).
* :meth:`SpawnControl.allowed` / :meth:`set_allowed` -- the 33 ``bool``
  flags at ``board.allowed_types`` (普通僵尸 0 is always forced on, as the
  game requires it).
* :meth:`SpawnControl.reroll` -- write the allowed flags, fill the table with
  ``-1`` (``CLEAR_INTS`` dwords), remote-call ``pick_zombie_waves`` so the
  game regenerates its own list, and on the seed chooser (``game_ui == 2``)
  refresh the street preview (``street_zombies_hack`` patch +
  ``remove_cutscene_zombies`` + ``place_street_zombies``).
* :meth:`SpawnControl.stats` -- reroll ``n`` times, collecting per-wave type
  counts (红眼 probability research), then **restore** the original table
  and flags so the user's level is unchanged.

Writes go straight through the session (``board.allowed_types`` and
``board.spawn_list`` are inline arrays, deliberately outside the generic
write whitelist) after the same checks the modifier applies: level ``low``
(``allow_write``), capability, writable session; ``dry_run`` journals only.
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from pvztools.model.types import SPAWN_EMPTY, SPAWN_WAVES, SpawnList
from pvztools.modifier.safety import Safety
from pvztools.observe import spawn as _spawn
from pvztools.observe.snapshot import NotInLevel
from pvztools.schema.enums import ZOMBIE_CHARS, ZombieType

from .actions import UI_ALLOWED, ActionError, resolve_zombie
from .conv import convention_for

__all__ = ["SpawnControl", "SpawnStats", "ALLOWED_COUNT", "CLEAR_INTS", "CLEAR_BYTES", "GIGA", "NORMAL"]

#: ``board.allowed_types`` holds one bool per zombie id 0..32.
ALLOWED_COUNT = 33
#: Toolkit clears 2000 dwords of the table (inside the 100x50 Board array).
CLEAR_INTS = 2000
CLEAR_BYTES = CLEAR_INTS * 4
GIGA = int(ZombieType.GIGA_GARGANTUAR)
NORMAL = int(ZombieType.ZOMBIE)
_CHARS = frozenset(ZOMBIE_CHARS)


@dataclass
class SpawnStats:
    """Result of :meth:`SpawnControl.stats`."""
    runs: int = 0
    allowed: Tuple[int, ...] = ()
    #: per wave (index 0 == wave 1): ``{zombie_id: total count over runs}``
    per_wave: List[Dict[int, int]] = field(default_factory=list)
    #: per wave: number of runs in which the wave contained ``GIGA``
    giga_waves: List[int] = field(default_factory=list)
    #: every decoded list (for further analysis)
    lists: List[SpawnList] = field(default_factory=list)
    failed: int = 0

    def giga_probability(self) -> List[float]:
        """P(red-eye appears in wave i+1) over the runs."""
        if not self.runs:
            return []
        return [c / self.runs for c in self.giga_waves]

    def mean_counts(self, zombie_id: int = GIGA) -> List[float]:
        if not self.runs:
            return []
        return [w.get(zombie_id, 0) / self.runs for w in self.per_wave]

    def describe(self) -> str:
        lines = ["%d 次出怪（允许: %s）" % (self.runs, " ".join(_spawn.short_name(z) for z in self.allowed))]
        if self.failed:
            lines.append("无效出怪表 %d 次" % self.failed)
        probs = self.giga_probability()
        means = self.mean_counts(GIGA)
        for i, (p, m) in enumerate(zip(probs, means)):
            tot = sum(self.per_wave[i].values()) / self.runs if self.runs else 0
            lines.append("w%-2d 红眼概率 %5.1f%%  平均红眼 %.2f  平均总数 %.1f"
                         % (i + 1, 100 * p, m, tot))
        return "\n".join(lines)


class SpawnControl:
    """Read / write the spawn list and ask the game to regenerate it."""

    def __init__(self, session: Any, profile: Any, safety: Optional[Safety], observer: Any,
                 executor: Any, patches: Any, conv: Any = None) -> None:
        self.session = session
        self.profile = profile
        self.safety = safety if safety is not None else Safety()
        self.observer = observer
        self.executor = executor
        self.patches = patches
        self.conv = conv if conv is not None else convention_for(profile)
        #: every fragment injected (op sequences, for journal reconstruction)
        self.history: List[Any] = []

    # -- pointers -----------------------------------------------------------------------
    def board(self) -> int:
        board = self.observer.board_ptr()
        if not board:
            raise NotInLevel()
        return board

    def _off(self, name: str) -> int:
        off = self.profile.board.get(name)
        if off is None:
            raise ActionError("profile %s 没有 board.%s" % (getattr(self.profile, "id", "?"), name))
        return off

    # -- reading ------------------------------------------------------------------------
    def raw(self) -> bytes:
        """The first ``SPAWN_WAVES x SPAWN_PER_WAVE`` dwords of the table."""
        return self.observer.spawn_list_raw()

    def current(self) -> Optional[SpawnList]:
        """Decoded list (rows cut at the first -1), ``None`` when invalid."""
        return self.observer.spawn_list()

    def allowed(self) -> Tuple[int, ...]:
        """Zombie ids whose ``allowed_types`` flag is set."""
        flags = self.allowed_flags()
        return tuple(i for i, b in enumerate(flags) if b)

    def allowed_flags(self) -> Tuple[bool, ...]:
        data = self.session.read(self.board() + self._off("allowed_types"), ALLOWED_COUNT)
        return tuple(bool(b) for b in data)

    # -- writing ----------------------------------------------------------------------------
    def _check_write(self, action: str) -> None:
        self.safety.require_level("low", action)
        self.safety.require_capability(action, self.profile)
        self.safety.require_session_writable(self.session, action)
        self.safety.require_ui(self.session, self.profile, UI_ALLOWED, action)

    def _write(self, what: str, addr: int, data: bytes, old: Any = None, new: Any = None) -> None:
        if not self.safety.dry_run:
            self.session.write(addr, data)
        self.safety.log("write", what, addr, old, new, width=len(data))

    @staticmethod
    def normalize_types(types: Iterable[Union[int, str]]) -> Tuple[int, ...]:
        """ids / names / AvZ chars (``"普杆舞红"`` as one string is split)."""
        out: List[int] = []
        for t in types:
            if isinstance(t, str) and len(t) > 1 and not t.isdigit() and all(ch in _CHARS for ch in t.replace(" ", "")):
                for ch in t.replace(" ", ""):
                    out.append(resolve_zombie(ch))
            else:
                out.append(resolve_zombie(t))
        out.append(NORMAL)
        return tuple(sorted(set(out)))

    def set_allowed(self, types: Iterable[Union[int, str]]) -> Tuple[int, ...]:
        """Write the 33 flags; 普通僵尸 is always on.  Returns the ids set."""
        ids = self.normalize_types(types)
        action = "写出怪种类 %s" % list(ids)
        self._check_write(action)
        board = self.board()
        addr = board + self._off("allowed_types")
        old = self.session.read(addr, ALLOWED_COUNT)
        flags = bytes(1 if i in ids else 0 for i in range(ALLOWED_COUNT))
        self._write("board.allowed_types", addr, flags, old.hex(), flags.hex())
        return ids

    def raw_full(self) -> bytes:
        """The whole region :meth:`clear_list` overwrites (``CLEAR_BYTES``),
        i.e. what :meth:`stats` must back up to restore the level exactly."""
        return self.session.read(self.board() + self._off("spawn_list"), CLEAR_BYTES)

    def clear_list(self) -> None:
        """Fill the table with ``-1`` (``CLEAR_INTS`` dwords)."""
        action = "清空出怪表"
        self._check_write(action)
        addr = self.board() + self._off("spawn_list")
        data = struct.pack("<%di" % CLEAR_INTS, *([SPAWN_EMPTY] * CLEAR_INTS))
        old = self.session.read(addr, CLEAR_BYTES)
        self._write("board.spawn_list", addr, data, old, "-1 x %d" % CLEAR_INTS)

    def write_raw(self, raw: bytes) -> None:
        """Write a previously read table back (restore).  ``raw`` must be a
        multiple of 4 bytes and at most ``CLEAR_BYTES`` long -- the spawn
        table is written outside the field whitelist, so the length is the
        only thing keeping the write inside ``board.spawn_list``."""
        raw = bytes(raw)
        if not raw or len(raw) > CLEAR_BYTES or len(raw) % 4:
            raise ActionError("出怪表数据长度 %d 不合法（须为 4 的倍数且不超过 %d 字节）"
                              % (len(raw), CLEAR_BYTES))
        action = "恢复出怪表"
        self._check_write(action)
        addr = self.board() + self._off("spawn_list")
        old = self.session.read(addr, len(raw))
        self._write("board.spawn_list", addr, raw, old, raw)

    def write_allowed_flags(self, flags: Sequence[bool]) -> None:
        action = "恢复出怪种类"
        self._check_write(action)
        addr = self.board() + self._off("allowed_types")
        data = bytes(1 if b else 0 for b in flags)[:ALLOWED_COUNT]
        old = self.session.read(addr, len(data))
        self._write("board.allowed_types", addr, data, old.hex(), data.hex())

    # -- remote -------------------------------------------------------------------------------
    def _check_remote(self, action: str) -> int:
        self.safety.require_level("remote", action)
        self.safety.require_flag("allow_patch", action, "远程调用需要临时启用 main_loop_block 补丁")
        self.safety.require_capability(action, self.profile)
        self.safety.require_session_writable(self.session, action)
        return self.safety.require_ui(self.session, self.profile, UI_ALLOWED, action)

    def _run(self, frag: Any, name: str) -> Any:
        # patches are applied by the executor inside the blocked window
        self.history.append(frag)
        return self.executor.run(frag.asm, name=name, extra_patches=tuple(frag.required_patches))

    def regenerate(self) -> None:
        """Remote-call ``pick_zombie_waves`` (no list clearing / flags)."""
        self._check_remote("重新生成出怪")
        board = self.board()
        self._run(self.conv.pick_zombie_waves(board, terminate=False), "pick_zombie_waves")

    def refresh_preview(self) -> None:
        """Seed-chooser street preview: remove + place street zombies."""
        self._check_remote("刷新出怪预览")
        board = self.board()
        cut = self.session.read_ptr(board + self._off("cutscene"))
        self._run(self.conv.refresh_spawn_preview(board, cut, terminate=False), "refresh_spawn_preview")

    def reroll(self, types: Optional[Iterable[Union[int, str]]] = None) -> Optional[SpawnList]:
        """Toolkit 自然出怪: flags (when ``types`` given) -> clear -> regenerate
        -> preview refresh on the seed chooser.  Returns the new list."""
        ui = self._check_remote("重新出怪")
        if types is not None:
            self.set_allowed(types)
        self.clear_list()
        self.regenerate()
        if ui == 2:
            self.refresh_preview()
        if self.safety.dry_run:
            return None
        return self.current()

    def stats(self, n: int, types: Optional[Iterable[Union[int, str]]] = None) -> SpawnStats:
        """Reroll ``n`` times and tally per-wave type counts, then restore
        the original table and allowed flags."""
        if not isinstance(n, int) or n <= 0:
            raise ActionError("次数必须是正整数，得到 %r" % (n,))
        ui = self._check_remote("出怪统计")
        st = SpawnStats(per_wave=[{} for _ in range(SPAWN_WAVES)], giga_waves=[0] * SPAWN_WAVES)
        if types is not None:
            st.allowed = self.normalize_types(types)
        else:
            st.allowed = self.allowed()
        if self.safety.dry_run:
            # nothing is regenerated in dry-run, so re-reading the live table
            # n times would fake n "measurements"; journal the intent only
            self.safety.log("spawn_stats", "dry-run", 0, None, n)
            return st
        # back up exactly the region clear_list() overwrites, not just the
        # decoded 20x50 window, so the restore is byte-exact
        backup_raw = self.raw_full()
        backup_flags = self.allowed_flags()
        try:
            if types is not None:
                self.set_allowed(types)
            for _ in range(n):
                self.clear_list()
                self.regenerate()
                sl = self.current()
                if sl is None:
                    st.failed += 1
                    continue
                st.runs += 1
                st.lists.append(sl)
                for i, w in enumerate(sl.waves[:SPAWN_WAVES]):
                    d = st.per_wave[i]
                    for z in w:
                        d[z] = d.get(z, 0) + 1
                    if GIGA in w:
                        st.giga_waves[i] += 1
        finally:
            self._restore(backup_raw, backup_flags, ui)
        return st

    def _restore(self, backup_raw: bytes, backup_flags: Sequence[bool], ui: int) -> None:
        """Restore table + flags (+ street preview on the chooser); each step
        runs on its own so one failure (e.g. the user left the level) does
        not skip the others, and the first failure is re-raised only when no
        other exception is already propagating."""
        errors: List[BaseException] = []
        for step in (lambda: self.write_raw(backup_raw),
                     lambda: self.write_allowed_flags(backup_flags),
                     lambda: self.refresh_preview() if ui == 2 else None):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - collected, re-raised below
                errors.append(e)
        if errors and sys.exc_info()[0] is None:
            raise errors[0]

