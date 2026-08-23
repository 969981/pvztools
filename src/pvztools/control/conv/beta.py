"""Leaked BETA (0.1.1.1014 / 0.9.9.1029) remote-call convention: thiscall,
the object pointer travels in ECX and remaining arguments are pushed.

Only the calls the features doc spells out for BETA are implemented; the
rest raise :class:`NotSupported`.
"""
from __future__ import annotations

from typing import Any

from .base import Convention, Fragment, IMITATER_SEED, ZOMBOSS, mov_reg_reg

__all__ = ["BetaConvention"]


class BetaConvention(Convention):
    family = "beta"

    # -- plants ----------------------------------------------------------------
    def _emit_put_plant_core(self, frag: Fragment, board: int, row: int, col: int,
                             ptype: int, imitater: bool) -> None:
        """BETA: type pushes as usual, then ``push row``, ``push col``, ``ECX=board``."""
        frag.push_imm32(ptype if imitater else -1)
        frag.push_imm32(IMITATER_SEED if imitater else ptype)
        frag.push_imm32(row)
        frag.push_imm32(col)
        frag.mov_reg_imm32("ecx", board)
        frag.call_abs(self._addr("put_plant"))

    def _precheck_put_plant_imitater(self) -> None:
        raise self._unsupported("put_plant_imitater", "BETA register for the plant not documented")

    def _precheck_put_plant_iz_style(self) -> None:
        raise self._unsupported("put_plant_iz_style")

    def _emit_put_plant_imitater(self, frag: Fragment) -> None:
        raise self._unsupported("put_plant_imitater", "BETA register for the plant not documented")

    def _emit_put_plant_iz_style(self, frag: Fragment, challenge: int) -> None:
        raise self._unsupported("put_plant_iz_style")

    def delete_plant(self, plant: int, asm: Any = None, terminate: bool = False) -> Fragment:
        frag = self._new("delete_plant", asm)
        frag.mov_reg_imm32("ecx", plant)
        frag.call_abs(self._addr("delete_plant"))
        return self._finish(frag, terminate)

    def _plant_sleep_reg(self) -> str:
        return "ecx"

    # -- zombies ---------------------------------------------------------------
    def put_zombie(self, challenge: int, row: int, col: int, ztype: int, asm: Any = None,
                   terminate: bool = False) -> Fragment:
        """BETA: ``push row``, ``push col``, ``push type``, ``ECX=challenge``."""
        frag = self._new("put_zombie", asm)
        frag.push_imm32(row)
        frag.push_imm32(col)
        frag.push_imm32(ztype)
        frag.mov_reg_imm32("ecx", challenge)
        frag.call_abs(self._addr("put_zombie"))
        return self._finish(frag, terminate)

    def put_zombie_in_row(self, board: int, ztype: int = ZOMBOSS, row: int = 0, asm: Any = None,
                          terminate: bool = False) -> Fragment:
        raise self._unsupported("put_zombie_in_row")

    # -- grid items --------------------------------------------------------------
    def put_ladder(self, board: int, row: int, col: int, asm: Any = None,
                   terminate: bool = False) -> Fragment:
        """BETA: ``push row``, ``push col``, ``ECX=board``."""
        frag = self._new("put_ladder", asm)
        frag.push_imm32(row)
        frag.push_imm32(col)
        frag.mov_reg_imm32("ecx", board)
        frag.call_abs(self._addr("put_ladder"))
        return self._finish(frag, terminate)

    def put_grave(self, challenge: int, row: int, col: int, asm: Any = None,
                  terminate: bool = False) -> Fragment:
        """BETA: ``push row``, ``push col``, ``ECX=challenge`` (thiscall)."""
        frag = self._new("put_grave", asm)
        frag.push_imm32(row)
        frag.push_imm32(col)
        frag.mov_reg_imm32("ecx", challenge)
        frag.call_abs(self._addr("put_grave"))
        return self._finish(frag, terminate)

    def delete_grid_item(self, item: int, asm: Any = None, terminate: bool = False) -> Fragment:
        frag = self._new("delete_grid_item", asm)
        frag.mov_reg_imm32("ecx", item)
        frag.call_abs(self._addr("delete_grid_item"))
        return self._finish(frag, terminate)

    # -- spawning ----------------------------------------------------------------
    def _pick_waves_reg(self) -> str:
        return "ecx"

    def remove_cutscene_zombies(self, board: int, asm: Any = None, terminate: bool = False) -> Fragment:
        raise self._unsupported("remove_cutscene_zombies")

    def place_street_zombies(self, cutscene: int, asm: Any = None, terminate: bool = False) -> Fragment:
        raise self._unsupported("place_street_zombies")

    # -- scene / music / level ---------------------------------------------------
    def pick_background(self, board: int, scene: int, asm: Any = None,
                        terminate: bool = False) -> Fragment:
        # Toolkit also applies two temporary scene-id mapping patches whose
        # bytes are not in the notes, so the call is refused rather than half-done.
        raise self._unsupported("pick_background", "needs undocumented temporary scene-id patches")

    def play_music(self, music: int, music_id: int, asm: Any = None, terminate: bool = False) -> Fragment:
        """BETA: ``push id``, ``ECX=music``."""
        frag = self._new("play_music", asm)
        frag.push_imm32(music_id)
        frag.mov_reg_imm32("ecx", music)
        frag.call_abs(self._addr("play_music"))
        return self._finish(frag, terminate)

    def fade_out_level(self, board: int, asm: Any = None, terminate: bool = False) -> Fragment:
        raise self._unsupported("fade_out_level")

    def _next_stage_extra(self, frag: Fragment) -> None:
        # BETA: EDI=challenge, dec [edi+endless_round], ECX=EDI, call
        mov_reg_reg(frag, "ecx", "edi")

    def wisdom_tree_grow(self, challenge: int, lawn: int, asm: Any = None,
                         terminate: bool = False) -> Fragment:
        """BETA 0.9.9: ``ECX=challenge``; 0.1.1 has no wisdom tree (address None)."""
        frag = self._new("wisdom_tree_grow", asm)
        frag.mov_reg_imm32("ecx", challenge)
        frag.call_abs(self._addr("wisdom_tree_grow"))
        return self._finish(frag, terminate)

    def sync_profile(self, game_selector: int, asm: Any = None, terminate: bool = False) -> Fragment:
        raise self._unsupported("sync_profile")

    # -- mowers ------------------------------------------------------------------
    def _mower_start_reg(self) -> str:
        return "ecx"

    def mower_delete(self, mower: int, asm: Any = None, terminate: bool = False) -> Fragment:
        frag = self._new("mower_delete", asm)
        frag.mov_reg_imm32("ecx", mower)
        frag.call_abs(self._addr("mower_delete"))
        return self._finish(frag, terminate)

    def mower_restore(self, board: int, asm: Any = None, terminate: bool = False) -> Fragment:
        raise self._unsupported("mower_restore", "thiscall form not documented for BETA")
