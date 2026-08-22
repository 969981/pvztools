"""pvztools — Plants vs. Zombies (PvZ1 & mods) automation framework.

Status: 0.0.1 is a name-reservation placeholder. The public API is not
defined yet. Planned layers:

- ``pvztools.memory``  process attach, signature (AOB) scan, version-adaptive addresses
- ``pvztools.game``    Board / Plant / Zombie / Seed / Wave / Clock object model
- ``pvztools.track``   轨道 DSL parser (e.g. ``P|I-P|I-P 601|1500|1500``)
- ``pvztools.runner``  track executor bound to the in-game clock
- ``pvztools.lineup``  endless-mode lineup generation
- ``pvztools.cheats``  opt-in modifier (sun, cooldown, ...)

Windows only. Does not ship any game files.
"""

__version__ = "0.0.1"
__all__ = ["__version__"]
