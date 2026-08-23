"""pvztools.modifier — memory writes and code patches behind a safety model.

::

    from pvztools.process import attach
    from pvztools.addr import profile_for_stamp
    from pvztools.modifier import Safety, Writer, PatchManager

    with attach(write=True) as s:
        p = profile_for_stamp(s.stamp)
        safety = Safety(allow_write=True, dry_run=False)
        w = Writer(s, p, safety)
        ch = w.set_sun(999)          # ch.old == previous sun
        ch.revert()
        with PatchManager(s, p, Safety(allow_patch=True, dry_run=False)) as pm:
            pm.enable("auto_collect")   # restored on exit / atexit

Defaults are safe: ``Safety()`` is dry-run with every flag off, so nothing is
written until the caller opts in.  See docs/parity-pvztoolkit.md §0.3 / §4.7.
"""
from __future__ import annotations

from .safety import (  # noqa: F401
    CHEAT_FLAGS,
    FIELD_RANGES,
    FIELD_WIDTHS,
    GameStateError,
    JournalEntry,
    PermissionDenied,
    RangeError,
    SUN_CAP,
    Safety,
    SafetyError,
    WhitelistError,
    CapabilityDenied,
    NON_WRITABLE_FIELDS,
    UI_NAMES,
    capability_of,
    semantic_base,
)
from .writes import Change, Writer, WriteError, ZOMBIE_STATUS_DYING  # noqa: F401
from .patches import PATCH_ALIASES, Patch, PatchError, PatchManager, restore_all  # noqa: F401

__all__ = [
    "Safety", "SafetyError", "PermissionDenied", "WhitelistError", "RangeError",
    "GameStateError", "CapabilityDenied", "NON_WRITABLE_FIELDS", "UI_NAMES", "capability_of",
    "JournalEntry", "FIELD_RANGES", "FIELD_WIDTHS", "CHEAT_FLAGS",
    "SUN_CAP", "semantic_base",
    "Writer", "Change", "WriteError", "ZOMBIE_STATUS_DYING",
    "PatchManager", "Patch", "PatchError", "PATCH_ALIASES", "restore_all",
]
