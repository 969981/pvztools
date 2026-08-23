"""Process discovery and attach (DESIGN §2 ``process/``).

    >>> from pvztools.process import attach
    >>> with attach() as s:            # finds popcapgame1.exe / PlantsVsZombies.exe
    ...     s.stamp, s.read_u32(0x731C50)

Only :mod:`pvztools.process.attach` contains logic; this package re-exports it.
"""
from __future__ import annotations

from .attach import (  # noqa: F401
    DEFAULT_NAMES,
    ProcessInfo,
    Session,
    ProcessError,
    GameNotRunning,
    MultipleGames,
    AccessDenied,
    NotA32BitProcess,
    ControllerBusy,
    find_game_processes,
    attach,
)

__all__ = [
    "DEFAULT_NAMES",
    "ProcessInfo",
    "Session",
    "ProcessError",
    "GameNotRunning",
    "MultipleGames",
    "AccessDenied",
    "NotA32BitProcess",
    "ControllerBusy",
    "find_game_processes",
    "attach",
]
