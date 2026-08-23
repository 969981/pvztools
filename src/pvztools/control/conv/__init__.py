"""Per-family remote-call conventions (parity §0.4).

``convention_for(profile)`` picks the generator for ``profile.family``;
``describe(name, family)`` renders the register / push summary for docs.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Type

from .base import (COB_CANNON, IMITATER_SEED, MAY_SLEEP, ZOMBOSS, Convention, Fragment,
                   NotSupported)
from .beta import BetaConvention
from .goty import GotyConvention
from .goty_zh2010_ja import GotyZh2010JaConvention
from .goty_zh2012 import GotyZh2012Convention
from .retail import RetailConvention

__all__ = ["Convention", "Fragment", "NotSupported", "convention_for", "describe",
           "FAMILIES", "CALL_NAMES", "IMITATER_SEED", "ZOMBOSS", "COB_CANNON", "MAY_SLEEP",
           "GotyConvention", "GotyZh2010JaConvention", "GotyZh2012Convention",
           "RetailConvention", "BetaConvention"]

FAMILIES: Dict[str, Type[Convention]] = {
    "retail": RetailConvention,
    "goty": GotyConvention,
    "goty_zh2010_ja": GotyZh2010JaConvention,
    "goty_zh2012": GotyZh2012Convention,
    "beta": BetaConvention,
}

#: Public generator methods, in docs order.
CALL_NAMES = (
    "put_plant", "put_plant_iz_style", "delete_plant", "set_plant_sleeping",
    "put_zombie", "put_zombie_in_row", "put_ladder", "put_grave", "delete_grid_item",
    "pick_zombie_waves", "remove_cutscene_zombies", "place_street_zombies",
    "refresh_spawn_preview", "pick_background", "play_music", "fade_out_level",
    "puzzle_next_stage_clear", "wisdom_tree_grow", "sync_profile",
    "mower_start", "mower_delete", "mower_restore", "cob_fire",
)


def convention_for(profile: Any, fn: Optional[Dict[str, Optional[int]]] = None,
                   asm_factory: Optional[Callable[[], Any]] = None) -> Convention:
    """Instantiate the convention for ``profile.family`` (or the string
    ``profile.conventions`` when it names a family).  ``fn`` defaults to every
    function VA of the profile."""
    family = getattr(profile, "conventions", None) or getattr(profile, "family", None)
    if family not in FAMILIES:
        family = getattr(profile, "family", family)
    cls = FAMILIES.get(family)
    if cls is None:
        raise NotSupported("*", str(family), "unknown family")
    if fn is None:
        fn = {name: f.va(profile.image_base) for name, f in profile.functions.items()
              if getattr(f, "enabled", True)}
    return cls(fn, profile, asm_factory)


# ---------------------------------------------------------------------------
# describe(): symbolic dry run
# ---------------------------------------------------------------------------

class _Sym:
    """Placeholder for an absolute value in a symbolic description."""

    def __init__(self, label: str):
        self.label = label

    def __repr__(self) -> str:
        return self.label

    __str__ = __repr__

    def __index__(self) -> int:
        return 0

    def __int__(self) -> int:
        return 0


class _NullAsm:
    def __getattr__(self, item: str) -> Callable[..., None]:
        return lambda *a, **k: None


class _SymBoard:
    """Stand-in profile exposing struct offsets symbolically."""

    class _Sec:
        def __getattr__(self, item: str) -> Any:
            if item.startswith("_"):
                raise AttributeError(item)
            return _Sym("+%s" % item)

    class _Obj:
        stride = _Sym("stride")

        def __init__(self) -> None:
            self.fields = _SymBoard._Sec()

    board = _Sec()
    objects: Dict[str, Any] = {}

    def __init__(self) -> None:
        self.objects = {"plant": self._Obj(), "challenge": self._Obj()}


_SYM_ARGS: Dict[str, Dict[str, Any]] = {
    "put_plant": {"board": "board", "row": "row", "col": "col", "ptype": "type"},
    "put_plant_iz_style": {"challenge": "challenge"},
    "delete_plant": {"plant": "plant"},
    "set_plant_sleeping": {"plant": "plant", "sleeping": "sleeping"},
    "put_zombie": {"challenge": "challenge", "row": "row", "col": "col", "ztype": "type"},
    "put_zombie_in_row": {"board": "board", "ztype": "type", "row": "row"},
    "put_ladder": {"board": "board", "row": "row", "col": "col"},
    "put_grave": {"challenge": "challenge", "row": "row", "col": "col"},
    "delete_grid_item": {"item": "grid_item"},
    "pick_zombie_waves": {"board": "board"},
    "remove_cutscene_zombies": {"board": "board"},
    "place_street_zombies": {"cutscene": "cutscene"},
    "refresh_spawn_preview": {"board": "board", "cutscene": "cutscene"},
    "pick_background": {"board": "board", "scene": "scene"},
    "play_music": {"music": "music", "music_id": "id"},
    "fade_out_level": {"board": "board"},
    "puzzle_next_stage_clear": {"challenge": "challenge"},
    "wisdom_tree_grow": {"challenge": "challenge", "lawn": "lawn"},
    "sync_profile": {"game_selector": "game_selector"},
    "mower_start": {"mower": "mower"},
    "mower_delete": {"mower": "mower"},
    "mower_restore": {"board": "board"},
    "cob_fire": {"cob_plant": "cob_plant", "x": "x", "y": "y"},
}


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v) if -256 < v < 256 else "0x%X" % (v & 0xFFFFFFFF)
    if isinstance(v, bytes):
        return v.hex()
    return str(v)


def _op_text(op: str, args: tuple) -> str:
    if op == "mov_reg_imm32":
        return "%s = %s" % (args[0].upper(), _fmt(args[1]))
    if op == "mov_reg_mem":
        return "%s = [%s]" % (args[0].upper(), _fmt(args[1]))
    if op == "mov_reg_reg":
        return "%s = %s" % (args[0].upper(), args[1].upper())
    if op == "mov_reg_regdisp":
        return "%s = [%s%s]" % (args[0].upper(), args[1].upper(), _fmt(args[2]))
    if op == "mov_regdisp_imm32":
        return "[%s%s] = %s" % (args[0].upper(), _fmt(args[1]), _fmt(args[2]))
    if op == "dec_regdisp":
        return "dec [%s%s]" % (args[0].upper(), _fmt(args[1]))
    if op == "push_imm32":
        return "push %s" % _fmt(args[0])
    if op == "push_reg":
        return "push %s" % args[0].upper()
    if op == "pop_reg":
        return "pop %s" % args[0].upper()
    if op == "call_abs":
        return "call %s" % _fmt(args[0])
    if op == "ret":
        return "ret"
    if op == "add_esp":
        return "add esp, %s" % _fmt(args[0])
    return "%s %s" % (op, " ".join(_fmt(a) for a in args))


def describe(name: str, family: str, **kwargs: Any) -> str:
    """Human-readable register / push summary of call ``name`` for ``family``,
    e.g. ``"goty put_zombie: push col; push type; EAX = row; ECX = challenge;
    call put_zombie; ret"``.  Unsupported combinations yield
    ``"<family> <name>: not supported (<reason>)"``.  ``kwargs`` (``imitater=True``,
    ``challenge=...``) are forwarded to the generator."""
    cls = FAMILIES.get(family)
    if cls is None:
        raise NotSupported(name, family, "unknown family")
    if name not in _SYM_ARGS:
        raise NotSupported(name, family, "unknown call")
    fn = {n: _Sym(n) for n in (
        "put_plant", "put_plant_imitater", "put_plant_iz_style", "put_zombie", "put_zombie_in_row",
        "put_grave", "put_ladder", "mower_start", "mower_delete", "mower_restore", "delete_plant",
        "delete_grid_item", "set_plant_sleeping", "puzzle_next_stage_clear", "pick_background",
        "pick_zombie_waves", "remove_cutscene_zombies", "place_street_zombies", "play_music",
        "sync_profile", "fade_out_level", "wisdom_tree_grow", "cob_fire")}
    conv = cls(fn, _SymBoard(), _NullAsm)  # type: ignore[arg-type]
    args = {k: _Sym(v) for k, v in _SYM_ARGS[name].items()}
    if name == "set_plant_sleeping":
        args["sleeping"] = kwargs.pop("sleeping", False)
    args.update(kwargs)
    args.setdefault("terminate", True)  # standalone listing ends with ret
    try:
        frag = getattr(conv, name)(**args)
    except NotSupported as e:
        return "%s %s: not supported (%s)" % (family, name, e.reason or "no convention")
    text = "; ".join(_op_text(op, a) for op, a in frag.ops)
    out = "%s %s: %s" % (family, name, text)
    if frag.required_patches:
        out += "  [requires patches: %s]" % ", ".join(frag.required_patches)
    if frag.notes:
        out += "  [notes: %s]" % "; ".join(frag.notes)
    return out


def describe_all(family: str) -> str:
    """All calls of one family, one per line."""
    return "\n".join(describe(n, family) for n in CALL_NAMES)
