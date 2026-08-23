"""GOTY 1.1.0.1056 zh 2012-06 / 2012-07 remote-call convention.

Identical to the general GOTY branch except ``wisdom_tree_grow`` (EDI).
Toolkit only special-cases zh2010/ja elsewhere, so this family inherits the
GOTY sequences for every other call.
"""
from __future__ import annotations

from typing import Any

from .base import Fragment
from .goty import GotyConvention

__all__ = ["GotyZh2012Convention"]


class GotyZh2012Convention(GotyConvention):
    family = "goty_zh2012"

    def wisdom_tree_grow(self, challenge: int, lawn: int, asm: Any = None,
                         terminate: bool = False) -> Fragment:
        """zh2012: ``EDI=challenge``, ``call wisdom_tree_grow``."""
        frag = self._new("wisdom_tree_grow", asm)
        frag.mov_reg_imm32("edi", challenge)
        frag.call_abs(self._addr("wisdom_tree_grow"))
        return self._finish(frag, terminate)

    def cob_fire(self, cob_plant: int, x: int, y: int, asm: Any = None,
                 terminate: bool = False) -> Fragment:
        # source-map §6 covers retail 1051 and GOTY Steam only; the zh / ja
        # builds were not checked, so do not inherit the GOTY sequence.
        raise self._unsupported("cob_fire", "unverified")
