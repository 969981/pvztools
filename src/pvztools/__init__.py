"""pvztools — Plants vs. Zombies (PvZ1 & mods) automation framework.

Quick start (Windows, game running)::

    import pvztools as pvz

    game = pvz.attach()            # 找进程、识版本、校验；失败时说人话
    game.sun, game.wave, game.countdown
    for z in game.zombies:
        print(z.row, z.name, z.hp)

    game = pvz.attach(write=True)  # writes / patches need opting in
    game.sun = 999
    game.cheats.no_cooldown = True

    pvz.scan_exe(path)             # offline: signature-scan an exe (also mods)

Layers (see docs/DESIGN.md §2):

- ``pvztools.schema.track`` / ``.lineup``  轨道 DSL, lineup codecs (pure Python)
- ``pvztools.bridges``                      Seml / AvZ converters
- ``pvztools._win`` ``process`` ``addr``    kernel32 + PE, attach, address profiles / scan
- ``pvztools.observe`` ``model``            read-only snapshots
- ``pvztools.modifier``                     writes / patches behind ``Safety``
- ``pvztools.game``                         the beginner facade re-exported here
- ``pvztools.cli``                          ``python -m pvztools`` / ``pvztools``

The memory layers are Windows only; importing this package is cheap and
works anywhere (``attach`` and friends are imported lazily).  Zero
third-party dependencies; no game files are shipped.
"""
from __future__ import annotations

from typing import Any, Optional

__version__ = "0.1.0"
__all__ = [
    "__version__", "attach", "scan_exe", "Game",
    "GameNotRunning", "MultipleGames", "ProcessError", "NotInLevel", "PermissionDenied",
]


def attach(pid: Optional[int] = None, *, write: bool = False, profile: Any = None,
           patch: Optional[bool] = None, **flags: Any):
    """Attach to the running game and return a :class:`pvztools.game.Game`.
    Extra keyword flags (``remote=True``, ``input=True`` ...) are forwarded to
    :func:`pvztools.game.attach` unchanged."""
    from pvztools.game import attach as _attach

    return _attach(pid, write=write, profile=profile, patch=patch, **flags)


def scan_exe(path: Any):
    """Signature-scan a game exe (launcher wrappers are unpacked automatically).
    Returns ``(ScanResult, PEInfo)``; see :mod:`pvztools.addr.scan`."""
    from pvztools.addr.scan import scan_exe as _scan_exe

    return _scan_exe(path)


def __getattr__(name: str) -> Any:
    # lazy re-exports so that ``import pvztools`` stays light
    if name == "Game":
        from pvztools.game import Game

        return Game
    if name in ("GameNotRunning", "MultipleGames", "ProcessError"):
        import pvztools.process as _p

        return getattr(_p, name)
    if name == "NotInLevel":
        from pvztools.observe import NotInLevel

        return NotInLevel
    if name == "PermissionDenied":
        from pvztools.modifier import PermissionDenied

        return PermissionDenied
    raise AttributeError("module 'pvztools' has no attribute %r" % name)
