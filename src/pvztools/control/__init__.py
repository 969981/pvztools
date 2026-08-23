"""Control layer: remote calls into the game (shellcode) -- DESIGN §6.

* :mod:`pvztools.control.asm` -- tiny x86-32 assembler (:class:`Asm`).
* :mod:`pvztools.control.remote` -- :class:`RemoteExecutor`: block the main
  loop, run shellcode on a remote thread, restore.
"""
from __future__ import annotations

from .asm import Asm, AsmError, REGS  # noqa: F401
from .remote import (  # noqa: F401
    RemoteExecutor,
    RemoteCallError,
    RemoteCallFailed,
    RemoteCallTimeout,
    KNOWN_CONVENTIONS,
    MAIN_LOOP_PATCH,
)

__all__ = [
    "Asm", "AsmError", "REGS",
    "RemoteExecutor", "RemoteCallError", "RemoteCallFailed", "RemoteCallTimeout",
    "KNOWN_CONVENTIONS", "MAIN_LOOP_PATCH",
]
