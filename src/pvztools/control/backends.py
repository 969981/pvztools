"""Action backends: one interface, two ways of acting on the game (DESIGN §6).

* ``mode == "remote"`` -- :class:`RemoteBackend` wraps
  :class:`~pvztools.control.actions.Actions` (shellcode remote calls).  These
  have *修改器* semantics: ``put_plant`` ignores sun and card cooldown, the
  seed bank is not touched, cobs fire regardless of the cursor.
* ``mode == "input"`` -- :class:`~pvztools.control.input_backend.InputBackend`
  posts ``WM_LBUTTONDOWN/UP`` to the game window and therefore plays by the
  game's rules (a card must be usable, the shovel must be shown, ...).

Both expose the :class:`ActionBackend` protocol so a track executor can be
written once:

    plant_card(slot_or_plant, row, col)   # seed slot (int 0-based) or plant
    shovel(row, col)
    fire_cob(cob_rowcol, row, col)        # cob = 1-based (row, col) of its LEFT cell
    fire_cobs([(cob, (row, col)), ...])
    click(x, y)                           # raw client-pixel click (input only)
    mode                                  # "remote" | "input"

Rows / columns are 1-based everywhere, like :mod:`pvztools.control.actions`.
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple, Union

from .actions import ActionError, resolve_plant

try:  # Python 3.8+ has typing.Protocol; keep a no-op fallback anyway.
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

    def runtime_checkable(cls):  # type: ignore
        return cls

__all__ = ["ActionBackend", "RemoteBackend", "BackendError", "CobRowCol", "SlotOrPlant",
           "FODDER_PLANTS", "pick_fodder"]

CobRowCol = Tuple[int, int]
SlotOrPlant = Union[int, str, Any]
MAX_SLOTS = 10
IMITATER_ID = 48
#: Auto fodder (track ``C`` / ``c``, ``Action.plant is None``): the AvZ
#: preference order 小喷菇 / 花盆 / 胆小菇 / 阳光菇 / 向日葵 (plant ids).
FODDER_PLANTS: Tuple[int, ...] = (8, 33, 13, 9, 1)


class BackendError(ActionError):
    """A backend refused or could not perform an action."""


@runtime_checkable
class ActionBackend(Protocol):
    """Common surface of :class:`RemoteBackend` and ``InputBackend``."""

    mode: str

    def plant_card(self, slot_or_plant: SlotOrPlant, row: int, col: int, imitater: bool = False) -> Any: ...

    def shovel(self, row: int, col: int) -> Any: ...

    def fire_cob(self, cob_rowcol: CobRowCol, row: int, col: float) -> Any: ...

    def fire_cobs(self, fires: Sequence[Tuple[CobRowCol, Tuple[int, float]]]) -> Any: ...

    def click(self, x: int, y: int) -> Any: ...


def is_slot(slot_or_plant: SlotOrPlant) -> bool:
    """``True`` when the argument names a seed-bank slot (small int, 0-based)
    rather than a plant (name / PlantType / plant id).  Plant ids and slot
    numbers overlap (0..9), so *plain ints below MAX_SLOTS are slots*; pass a
    plant name or a ``PlantType`` member to mean the plant."""
    if isinstance(slot_or_plant, bool):
        return False
    if type(slot_or_plant) is int:
        return 0 <= slot_or_plant < MAX_SLOTS
    return False


def pick_fodder(seeds: Sequence[Any], prefer: Sequence[int] = FODDER_PLANTS) -> Tuple[int, bool]:
    """Choose the fodder card for an *auto* fodder action
    (``plant_card(None, ...)``): the first plant of ``prefer`` that has a
    **usable** card in ``seeds`` (plain card first, then an Imitater copy).
    Returns ``(plant_id, imitater)``; raises :class:`BackendError` when no
    fodder card is usable."""
    seeds = list(seeds)
    for pid in prefer:
        for s in seeds:
            if int(s.type) == pid and getattr(s, "usable", True):
                return pid, False
        for s in seeds:
            if int(s.type) == IMITATER_ID and int(getattr(s, "imitater_type", -1)) == pid \
                    and getattr(s, "usable", True):
                return pid, True
    have = ", ".join("%d:%s" % (int(s.slot), int(s.type)) for s in seeds if int(s.type) >= 0) or "空"
    raise BackendError("没有可用的垫材卡（自动垫材按 小喷/花盆/胆小/阳光菇/向日葵 挑选；现有：%s）" % have)


class RemoteBackend:
    """Delegate to :class:`~pvztools.control.actions.Actions` (mode ``remote``).

    ``plant_card`` with a *slot* resolves the slot's plant through the
    observer's seed bank (``actions.observer.seeds()``) and then calls
    ``put_plant`` -- sun and cooldown are **not** consumed (修改器语义).
    """

    mode = "remote"

    def __init__(self, actions: Any) -> None:
        self.actions = actions

    # -- helpers -------------------------------------------------------------
    def _slot_plant(self, slot: int) -> Tuple[int, bool]:
        observer = getattr(self.actions, "observer", None)
        if observer is None:
            raise BackendError("remote 后端没有 observer，无法按卡槽编号取植物；请直接给植物名")
        for seed in observer.seeds():
            if int(seed.slot) == slot:
                if seed.type < 0:
                    raise BackendError("卡槽 %d 为空" % slot)
                if int(seed.type) == 48:
                    return int(seed.imitater_type), True
                return int(seed.type), False
        raise BackendError("没有卡槽 %d" % slot)

    # -- ActionBackend -------------------------------------------------------
    def plant_card(self, slot_or_plant: SlotOrPlant, row: int, col: int, imitater: bool = False) -> Any:
        """``imitater=True`` places the Imitater variant of ``slot_or_plant``
        (a slot argument carries its own imitater-ness)."""
        if slot_or_plant is None:  # auto fodder: pick from the seed bank
            observer = getattr(self.actions, "observer", None)
            if observer is None:
                raise BackendError("remote 后端没有 observer，无法自动挑选垫材卡；请直接给植物名")
            pid, imitater = pick_fodder(observer.seeds())
        elif is_slot(slot_or_plant):
            pid, imitater = self._slot_plant(int(slot_or_plant))
        else:
            pid = resolve_plant(slot_or_plant)
        return self.actions.put_plant(row, col, pid, imitater=bool(imitater))

    def shovel(self, row: int, col: int) -> Any:
        return self.actions.remove_plant(row, col)

    def fire_cob(self, cob_rowcol: CobRowCol, row: int, col: float, *, at_tick: Optional[int] = None) -> Any:
        """``at_tick``: frame-lock the call to that ``main_counter`` (DESIGN §6.3)."""
        kw = {} if at_tick is None else {"at_tick": int(at_tick)}
        return self.actions.fire_cob(tuple(cob_rowcol), int(row), float(col), **kw)

    def fire_cobs(self, fires: Sequence[Tuple[CobRowCol, Tuple[int, float]]], *,
                  at_tick: Optional[int] = None) -> Any:
        kw = {} if at_tick is None else {"at_tick": int(at_tick)}
        return self.actions.fire_cobs([(tuple(c), (int(r), float(k))) for c, (r, k) in fires], **kw)

    def cobs_ready(self, cannons: Sequence[CobRowCol]) -> List[bool]:
        """READY (state 37) flag per cannon ``(row, col)`` (left cell); a
        cannon that is not on the board counts as not ready.  Lets the
        scheduler wait for the cooldown instead of failing the shot."""
        cobs = getattr(self.actions, "cobs", None)
        if not callable(cobs):
            return [True] * len(cannons)
        ready = {(int(c.row), int(c.col)): bool(c.ready) for c in cobs()}
        return [ready.get((int(r), int(k)), False) for r, k in cannons]

    def click(self, x: int, y: int) -> Any:
        raise BackendError("remote 后端不支持原始点击 click(%s, %s)；请用 mode='input'" % (x, y))

    def __repr__(self) -> str:
        return "RemoteBackend(%r)" % (self.actions,)
