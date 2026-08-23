"""Observation layer: typed read-only snapshots of a running game.

    >>> from pvztools.process import attach
    >>> from pvztools.addr import profile_for_stamp
    >>> from pvztools.observe import Observer
    >>> s = attach(); obs = Observer(s, profile_for_stamp(s.stamp))
    >>> obs.game().in_level, obs.board().sun
"""
from __future__ import annotations

from .snapshot import MAX_ARRAY_COUNT, MAX_SEED_SLOTS, NotInLevel, ObserveError, Observer  # noqa: F401
from .spawn import decode as decode_spawn_list, describe as describe_spawn_list, invalid_reason  # noqa: F401

__all__ = [
    "Observer", "ObserveError", "NotInLevel", "MAX_SEED_SLOTS", "MAX_ARRAY_COUNT",
    "decode_spawn_list", "describe_spawn_list", "invalid_reason",
]
