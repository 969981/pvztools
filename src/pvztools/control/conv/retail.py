"""Retail ("原版": 1.0.0.1051 / 1.2.0.1065 / es / ru) remote-call convention.

Differences from GOTY (features doc):

* put_grave: ``EDX=challenge``, ``push EDX`` (GOTY uses ECX)
* set_plant_sleeping: ``EAX=plant`` (GOTY EDI)
* wisdom_tree_grow: ``EDI=lawn`` then call
* cob_fire (AvZ / pvzscripts, 1051 ``0x466D50``): see :meth:`cob_fire`.
"""
from __future__ import annotations

from typing import Any

from .base import Convention, Fragment

__all__ = ["RetailConvention"]


class RetailConvention(Convention):
    family = "retail"

    def _grave_reg(self) -> str:
        return "edx"

    def _plant_sleep_reg(self) -> str:
        return "eax"

    def wisdom_tree_grow(self, challenge: int, lawn: int, asm: Any = None,
                         terminate: bool = False) -> Fragment:
        """Retail: ``EDI=lawn``, ``call wisdom_tree_grow``."""
        frag = self._new("wisdom_tree_grow", asm)
        frag.mov_reg_imm32("edi", lawn)
        frag.call_abs(self._addr("wisdom_tree_grow"))
        return self._finish(frag, terminate)

    def cob_fire(self, cob_plant: int, x: int, y: int, asm: Any = None,
                 terminate: bool = False) -> Fragment:
        """``Plant::CobCannonFire`` at 1051 ``0x466D50`` (AvZ ``AAsm::Fire``,
        source-map §6.5): ``push y``, ``push x``, ``EAX=plant``, ``call``;
        the callee does ``ret 8``.  ``x`` / ``y`` are click pixels
        (``control.grid.cob_target_pixels``)."""
        return self._emit_cob_fire("cob_fire", cob_plant, x, y, asm, terminate)
