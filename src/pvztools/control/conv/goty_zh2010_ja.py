"""GOTY 1.1.0.1056 zh 2010 ("粘度汗化") and ja remote-call convention.

Differences from the general GOTY branch (features doc):

* put_zombie: ``push type``, ``push challenge``, ``EAX=row``, ``ECX=col``
* pick_zombie_waves: ``EAX=board``
* mower_start: ``EBX=mower``
* fade_out_level: ``EAX=board``, ``push EAX``
* puzzle_next_stage_clear: ``push EDI`` before the call
* wisdom_tree_grow: ``EBX=challenge``, ``push EBX``
* sync_profile: extra ``mov esi, ecx``
"""
from __future__ import annotations

from typing import Any

from .base import Fragment, mov_reg_reg
from .goty import GotyConvention

__all__ = ["GotyZh2010JaConvention"]


class GotyZh2010JaConvention(GotyConvention):
    family = "goty_zh2010_ja"

    def put_zombie(self, challenge: int, row: int, col: int, ztype: int, asm: Any = None,
                   terminate: bool = False) -> Fragment:
        frag = self._new("put_zombie", asm)
        frag.push_imm32(ztype)
        frag.push_imm32(challenge)
        frag.mov_reg_imm32("eax", row)
        frag.mov_reg_imm32("ecx", col)
        frag.call_abs(self._addr("put_zombie"))
        return self._finish(frag, terminate)

    def _pick_waves_reg(self) -> str:
        return "eax"

    def _mower_start_reg(self) -> str:
        return "ebx"

    def fade_out_level(self, board: int, asm: Any = None, terminate: bool = False) -> Fragment:
        frag = self._new("fade_out_level", asm)
        frag.mov_reg_imm32("eax", board)
        frag.push_reg("eax")
        frag.call_abs(self._addr("fade_out_level"))
        return self._finish(frag, terminate)

    def _next_stage_extra(self, frag: Fragment) -> None:
        frag.push_reg("edi")

    def wisdom_tree_grow(self, challenge: int, lawn: int, asm: Any = None,
                         terminate: bool = False) -> Fragment:
        frag = self._new("wisdom_tree_grow", asm)
        frag.mov_reg_imm32("ebx", challenge)
        frag.push_reg("ebx")
        frag.call_abs(self._addr("wisdom_tree_grow"))
        return self._finish(frag, terminate)

    def _sync_profile_extra(self, frag: Fragment) -> None:
        mov_reg_reg(frag, "esi", "ecx")

    def cob_fire(self, cob_plant: int, x: int, y: int, asm: Any = None,
                 terminate: bool = False) -> Fragment:
        # source-map §6 covers retail 1051 and GOTY Steam only; the zh / ja
        # builds were not checked, so do not inherit the GOTY sequence.
        raise self._unsupported("cob_fire", "unverified")
