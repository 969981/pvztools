"""GOTY (en 1073 / 1096 / de-es-fr-it 1093) remote-call convention.

The base class already encodes the GOTY branch of every Toolkit call
(features doc §1-§5); this subclass only fixes the family name and marks the
calls whose GOTY convention is not verified.
"""
from __future__ import annotations

from typing import Any

from .base import Convention, Fragment

__all__ = ["GotyConvention"]


class GotyConvention(Convention):
    family = "goty"

    # set_plant_sleeping: EDI=plant; put_grave: ECX=challenge + push ECX;
    # pick_zombie_waves: EDI=board; mower_start: ESI; wisdom_tree: ESI
    # -- all base defaults.

    def cob_fire(self, cob_plant: int, x: int, y: int, asm: Any = None,
                 terminate: bool = False) -> Fragment:
        """``Plant::CobCannonFire`` (GOTY Steam ``0x46D1F0``, source-map §6.5):
        ``push y``, ``push x``, ``EAX=plant``, ``call``; the callee does
        ``ret 8``.  Verified against the disassembly (``mov esi, eax`` prologue,
        ``[esp+0x10]`` = x, ``[esp+0x14]`` = y) and the in-game caller at
        ``0x41323B``."""
        return self._emit_cob_fire("cob_fire", cob_plant, x, y, asm, terminate)

    # -- coins (autocollect) -------------------------------------------------------
    def coin_collect(self, coin: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """``Coin::Collect`` (GOTY Steam 1096 ``0x435DC0``): ``ECX=coin``,
        ``call``; no stack args.  Verified 2026-08-22 against the decompiled
        prologue (``in_ECX``; ``[ecx+0x38]`` = mDead checked first) and the
        in-game auto-collect caller at ``0x435D60``."""
        return self._emit_coin_collect("ecx", coin, asm, terminate)

    # -- menu / seed chooser (control.chooser_mem / control.menu) -----------------
    def seed_chooser_click_seed(self, chooser: int, seed: int, in_bank: bool = False,
                                asm: Any = None, terminate: bool = False) -> Fragment:
        """GOTY ``0x494690`` ClickedSeedInChooser / ``0x4944F0`` ClickedSeedInBank:
        prologue ``push ebp; mov ebp,[esp+8]; push esi; mov esi,eax`` --
        ``this`` in **EAX**, ``ChosenSeed*`` pushed, callee ``ret 4``."""
        fn_name = "chooser_clicked_seed_in_bank" if in_bank else "chooser_clicked_seed_in_chooser"
        target = self._addr(fn_name)
        frag = self._new("seed_chooser_click_seed", asm)
        frag.push_imm32(seed)
        frag.mov_reg_imm32("eax", chooser)
        frag.call_abs(target)
        return self._finish(frag, terminate)

    def seed_chooser_start(self, chooser: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """GOTY ``0x493C70`` OnStartButton: ``push ebx; mov ebx,[esi+0xD28]``
        -- ``this`` in **ESI**, no stack args, plain ``ret``."""
        target = self._addr("chooser_on_start")
        frag = self._new("seed_chooser_start", asm)
        frag.mov_reg_imm32("esi", chooser)
        frag.call_abs(target)
        return self._finish(frag, terminate)

    def award_start(self, award: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """GOTY ``0x40A310`` AwardScreen::StartButtonPressed: ``push ebp; mov
        ebp,[esp+8]`` -- ``this`` on the stack, ``ret 4``."""
        return self._emit_stack_this("award_start", "award_start_button", award, asm, terminate)

    def game_selector_adventure(self, selector: int, asm: Any = None,
                                terminate: bool = False) -> Fragment:
        """GOTY ``0x450B90`` GameSelector::ClickedAdventure: ``sub esp,0x1C; push
        ebx; push ebp; mov ebp,[esp+0x28]`` -- ``this`` on the stack, ``ret 4``."""
        return self._emit_stack_this("game_selector_adventure", "game_selector_clicked_adventure",
                                     selector, asm, terminate)
