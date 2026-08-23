"""Reversible code patches (parity §0.3 "低" level, §4.7).

A profile declares patch *groups* (``profile.patches[name]``), each with one
or more sites ``(rva, on_bytes, off_bytes)``.  :class:`PatchManager`

* verifies the current bytes of **every** site before touching anything
  (refuses the whole group if a site matches neither ``on`` nor ``off``),
* honours mutual exclusion (``excludes`` — plant_invincible ↔ plant_fragile
  ...) by switching the other side off first,
* journals each site write through :class:`~pvztools.modifier.safety.Safety`,
* restores everything it enabled on ``__exit__`` / ``restore()`` and at
  interpreter exit (``atexit``), so a crash of the controlling script never
  leaves the game patched.

Code patches need ``Safety(allow_patch=True)``; ``purity="strict"`` allows
only ``background_running``; ``high`` groups (``userdata_readonly``) also
need ``allow_savedata``.  With ``dry_run`` (default) nothing is written.
"""
from __future__ import annotations

import atexit
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from .safety import PermissionDenied, Safety, SafetyError

__all__ = ["Patch", "PatchManager", "PatchError", "PATCH_ALIASES", "STRICT_ALLOWED_PATCHES",
           "restore_all"]

#: Friendly names (task brief / Toolkit wording) -> profile group names.
PATCH_ALIASES: Dict[str, str] = {
    "unlock_sun_limit": "sun_cap_unlock",
    "sun_limit": "sun_cap_unlock",
    "auto_collected": "auto_collect",
    "stop_spawning": "pause_spawning",
    "no_spawn": "pause_spawning",
    "background": "background_running",
    "block_main_loop": "main_loop_block",
}

#: The only patch group allowed under ``purity="strict"`` (parity §0.3).
STRICT_ALLOWED_PATCHES: Tuple[str, ...] = ("background_running",)

#: Groups that must never be toggled as a standalone "feature" (they are
#: internals of remote calls / temporary patches).
_INTERNAL_GROUPS: Tuple[str, ...] = ("main_loop_block",)


class PatchError(SafetyError):
    """Byte verification failed or the group cannot be applied."""


@dataclass(frozen=True)
class Patch:
    """One patch site with absolute address and on/off bytes."""
    name: str          # group name
    addr: int          # VA
    on_bytes: bytes
    off_bytes: bytes
    index: int = 0     # site index inside the group

    @property
    def size(self) -> int:
        return len(self.on_bytes)

    @property
    def label(self) -> str:
        return "%s[%d]" % (self.name, self.index)

    def state_of(self, current: bytes) -> str:
        if current == self.on_bytes:
            return "on"
        if current == self.off_bytes:
            return "off"
        return "unknown"


#: Managers that currently hold live (non dry-run) patches.  Strong
#: references on purpose: a manager (or the Game owning it) that goes out of
#: scope without ``restore()`` must still be restorable at interpreter exit.
#: A manager leaves the set as soon as it has nothing enabled any more.
_managers: "Set[PatchManager]" = set()
_atexit_registered = False


def _track(mgr: "PatchManager") -> None:
    if mgr.enabled:
        _managers.add(mgr)
    else:
        _managers.discard(mgr)


def _ensure_atexit() -> None:
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(restore_all)
        _atexit_registered = True


def restore_all() -> None:
    """Disable every patch enabled by any live PatchManager in this process."""
    for mgr in list(_managers):
        try:
            mgr.restore()
        except Exception:
            pass


class PatchManager:
    def __init__(self, session: Any, profile: Any, safety: Optional[Safety] = None) -> None:
        self.session = session
        self.profile = profile
        self.safety = safety if safety is not None else Safety()
        self.groups: Dict[str, List[Patch]] = {}
        self.levels: Dict[str, str] = {}
        self.excludes: Dict[str, Tuple[str, ...]] = {}
        self._build()
        #: names enabled through this manager and not yet disabled
        self.enabled: Set[str] = set()
        #: names "enabled" in dry-run mode (no bytes written)
        self.dry_enabled: Set[str] = set()
        _ensure_atexit()

    # -- construction ------------------------------------------------------------
    def _build(self) -> None:
        base = self.profile.image_base
        for name, pg in self.profile.patches.items():
            if not pg.enabled or not pg.sites:
                continue
            sites = [Patch(name, s.va(base), bytes(s.on), bytes(s.off), i)
                     for i, s in enumerate(pg.sites)]
            self.groups[name] = sites
            self.levels[name] = pg.level or "low"
            self.excludes[name] = tuple(pg.excludes)

    def resolve(self, name: str) -> str:
        key = PATCH_ALIASES.get(name, name)
        if key not in self.groups:
            if key in self.profile.patches:
                raise PatchError("补丁 %s 在 profile %s 中被禁用 / 无站点" % (key, self.profile.id))
            raise PatchError("未知补丁 %r；可用：%s" % (name, ", ".join(sorted(self.groups))))
        return key

    def names(self) -> List[str]:
        return sorted(self.groups)

    # -- inspection ------------------------------------------------------------------
    def read_state(self, name: str) -> str:
        """'on' | 'off' | 'mixed' | 'unknown' from the live bytes."""
        key = self.resolve(name)
        states = {p.state_of(self.session.read(p.addr, p.size)) for p in self.groups[key]}
        if len(states) == 1:
            return states.pop()
        if "unknown" in states:
            return "unknown"
        return "mixed"

    def status(self) -> Dict[str, str]:
        """Live state of every group; dry-run-enabled groups are reported as
        ``'dry'`` (bytes unchanged)."""
        out: Dict[str, str] = {}
        for key in sorted(self.groups):
            if key in self.dry_enabled:
                out[key] = "dry"
                continue
            try:
                out[key] = self.read_state(key)
            except Exception as exc:  # unreadable page etc.
                out[key] = "error:%s" % exc.__class__.__name__
        return out

    # -- permission ------------------------------------------------------------------
    def _authorize(self, key: str, action: str) -> None:
        s = self.safety
        if s.strict and key not in STRICT_ALLOWED_PATCHES:
            raise PermissionDenied("purity", "%s %s" % (action, key))
        if key in _INTERNAL_GROUPS:
            raise PatchError("%s 是远程调用内部补丁，不能单独启用" % key)
        s.require_flag("allow_patch", "%s %s" % (action, key))
        s.require_capability("%s %s" % (action, key), self.profile)
        if self.levels.get(key) == "high":
            s.require_flag("allow_savedata", "%s %s" % (action, key), "安全级别 high")
        s.require_session_writable(self.session, "%s %s" % (action, key))

    def _verify(self, key: str) -> Dict[int, bytes]:
        """Read every site; refuse if any site matches neither on nor off."""
        current: Dict[int, bytes] = {}
        for p in self.groups[key]:
            cur = self.session.read(p.addr, p.size)
            if p.state_of(cur) == "unknown":
                raise PatchError("%s @0x%x 当前字节 %s 既不是 on(%s) 也不是 off(%s)：拒绝修改"
                                 % (p.label, p.addr, cur.hex(), p.on_bytes.hex(), p.off_bytes.hex()))
            current[p.index] = cur
        return current

    # -- apply -----------------------------------------------------------------------
    def _apply(self, key: str, on: bool, what: str) -> List[Patch]:
        """Write every site of ``key``.  A multi-site group is applied
        all-or-nothing: when a site write (or its readback) fails, the sites
        already written are rolled back to their previous bytes before the
        error propagates.  Sites whose rollback fails too are left recorded
        in ``enabled`` so ``restore()`` / atexit keeps trying."""
        current = self._verify(key)
        written: List[Patch] = []
        try:
            for p in self.groups[key]:
                want = p.on_bytes if on else p.off_bytes
                cur = current[p.index]
                if cur == want:
                    continue
                if not self.safety.dry_run:
                    self.session.write(p.addr, want)
                    back = self.session.read(p.addr, p.size)
                    if back != want:
                        self.safety.log(what, p.label, p.addr, cur.hex(), back.hex(), p.size)
                        raise PatchError("%s @0x%x 写后回读 %s != %s" % (p.label, p.addr, back.hex(), want.hex()))
                self.safety.log(what, p.label, p.addr, cur.hex(), want.hex(), p.size)
                written.append(p)
        except Exception:
            if written and not self.safety.dry_run:
                self._rollback(key, written, current, what)
            raise
        return written

    def _rollback(self, key: str, written: List[Patch], current: Dict[int, bytes], what: str) -> None:
        """Best-effort undo of a partially applied group."""
        failed = False
        for p in written:
            old = current[p.index]
            try:
                self.session.write(p.addr, old)
                if self.session.read(p.addr, p.size) != old:
                    failed = True
                self.safety.log(what + "_rollback", p.label, p.addr,
                                (p.on_bytes if what == "patch_on" else p.off_bytes).hex(), old.hex(), p.size)
            except Exception:
                failed = True
        if failed:
            # some sites are still patched: keep the group on the books
            self.enabled.add(key)
            _track(self)

    def enable(self, name: str) -> List[Patch]:
        """Turn a group on.  Returns the sites actually written (empty when
        already on).  Mutually exclusive groups are switched off first."""
        key = self.resolve(name)
        self._authorize(key, "enable")
        for other in self.excludes.get(key, ()):
            if other in self.groups and (other in self.enabled or other in self.dry_enabled
                                         or self.read_state(other) == "on"):
                self.disable(other)
        self._verify(key)  # refuse before any side effect
        written = self._apply(key, True, "patch_on")
        if self.safety.dry_run:
            self.dry_enabled.add(key)
        else:
            self.enabled.add(key)
            _track(self)
        if key == "sun_cap_unlock":
            self.safety.sun_limit_unlocked = True
        return written

    def disable(self, name: str) -> List[Patch]:
        key = self.resolve(name)
        self._authorize(key, "disable")
        written = self._apply(key, False, "patch_off")
        self.enabled.discard(key)
        self.dry_enabled.discard(key)
        _track(self)
        if key == "sun_cap_unlock":
            self.safety.sun_limit_unlocked = False
        return written

    def is_enabled(self, name: str) -> bool:
        key = self.resolve(name)
        return key in self.enabled or key in self.dry_enabled

    def restore(self) -> List[str]:
        """Disable everything this manager enabled (best effort, never raises
        PermissionDenied for groups it was allowed to enable)."""
        done: List[str] = []
        for key in sorted(self.enabled):
            try:
                self._apply(key, False, "patch_off")
            except Exception:
                # the off-write failed: keep the group on the books so that
                # is_enabled() stays true and a later restore() / atexit
                # retries (see _apply's docstring)
                continue
            done.append(key)
            self.enabled.discard(key)
            if key == "sun_cap_unlock":
                self.safety.sun_limit_unlocked = False
        self.dry_enabled.clear()
        _track(self)
        return done

    def forget(self) -> None:
        """Keep the enabled patches in place and stop tracking them (used by
        ``Game.keep_patches``)."""
        self.enabled.clear()
        self.dry_enabled.clear()
        _track(self)

    def __del__(self) -> None:
        # a manager dropped without restore(): undo its patches now
        try:
            if self.enabled:
                self.restore()
        except Exception:
            pass

    # -- context ---------------------------------------------------------------------
    def __enter__(self) -> "PatchManager":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.restore()

    def __repr__(self) -> str:
        return "PatchManager(profile=%s, groups=%d, enabled=%s)" % (
            self.profile.id, len(self.groups), sorted(self.enabled | self.dry_enabled))
