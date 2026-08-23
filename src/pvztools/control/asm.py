"""A minimal x86-32 assembler for remote-call shellcode (DESIGN §6,
parity §0.4).

Only the handful of encodings the remote calls need are provided; every
method appends bytes to the builder and returns ``self`` so calls chain.
``call_abs(addr)`` emits ``E8 rel32`` with a fixup that :meth:`Asm.link`
resolves once the remote buffer address is known -- ``eax``/``ecx``/``edx``
are often argument registers, so an indirect ``mov eax, addr; call eax``
would clobber them.

Written from the Intel SDM encodings; nothing here is derived from any
third-party tool's source.

Example::

    code = (Asm()
            .mov_reg_mem("eax", 0x731C50)          # lawn
            .mov_reg_regdisp("eax", "eax", 0x868)  # board
            .push_imm32(3).push_imm32(2)
            .call_abs(0x4105A0))
    shell = code.link(remote_buffer)

Do **not** end shellcode meant for :class:`~pvztools.control.remote.RemoteExecutor`
with ``ret``: the executor's wrapper supplies the epilogue / thread exit and
strips a trailing ``ret`` (see :meth:`Asm.strip_trailing_ret`).
"""
from __future__ import annotations

import struct
from typing import List, Tuple

__all__ = ["Asm", "AsmError", "REGS"]

#: Register numbers in ModRM / opcode+r order.
REGS = {"eax": 0, "ecx": 1, "edx": 2, "ebx": 3, "esp": 4, "ebp": 5, "esi": 6, "edi": 7}


class AsmError(ValueError):
    """Bad operand (unknown register, value out of range ...)."""


def _reg(name: str) -> int:
    try:
        return REGS[name.lower()]
    except (KeyError, AttributeError):
        raise AsmError("unknown register %r (expected one of %s)" % (name, ", ".join(REGS)))


def _imm32(v: int, what: str = "imm32") -> bytes:
    if isinstance(v, bool) or not isinstance(v, int):
        raise AsmError("%s: expected int, got %r" % (what, v))
    if not (-0x80000000 <= v <= 0xFFFFFFFF):
        raise AsmError("%s: %r does not fit in 32 bits" % (what, v))
    return struct.pack("<I", v & 0xFFFFFFFF)


def _imm8(v: int, what: str = "imm8") -> bytes:
    if isinstance(v, bool) or not isinstance(v, int):
        raise AsmError("%s: expected int, got %r" % (what, v))
    if not (-0x80 <= v <= 0xFF):
        raise AsmError("%s: %r does not fit in 8 bits" % (what, v))
    return struct.pack("<B", v & 0xFF)


def _imm16(v: int, what: str = "imm16") -> bytes:
    if isinstance(v, bool) or not isinstance(v, int) or not (0 <= v <= 0xFFFF):
        raise AsmError("%s: %r does not fit in 16 bits" % (what, v))
    return struct.pack("<H", v)


def _modrm(mod: int, reg: int, rm: int) -> int:
    return (mod << 6) | (reg << 3) | rm


def _mem_regdisp(reg_field: int, base: int, disp: int) -> bytes:
    """ModRM (+SIB for esp) for ``[base + disp32]`` (always disp32 form, so
    ebp works without special casing)."""
    out = bytes([_modrm(0b10, reg_field, base)])
    if base == REGS["esp"]:
        out += b"\x24"  # SIB: scale=0 index=none base=esp
    return out + _imm32(disp, "disp32")


def _mem_abs(reg_field: int, addr: int) -> bytes:
    """ModRM for ``[disp32]`` (mod=00 rm=101)."""
    return bytes([_modrm(0b00, reg_field, 0b101)]) + _imm32(addr, "addr")


class Asm:
    """Byte builder with ``call rel32`` fixups."""

    def __init__(self) -> None:
        self._buf = bytearray()
        #: (offset_of_rel32_field, absolute_target)
        self._call_fixups: List[Tuple[int, int]] = []
        #: (offset, length, text) for :meth:`dump`
        self._insns: List[Tuple[int, int, str]] = []

    # -- bookkeeping ---------------------------------------------------------
    def _emit(self, data: bytes, text: str) -> "Asm":
        self._insns.append((len(self._buf), len(data), text))
        self._buf += data
        return self

    def __len__(self) -> int:
        return len(self._buf)

    @property
    def size(self) -> int:
        return len(self._buf)

    @property
    def fixups(self) -> List[Tuple[int, int]]:
        return list(self._call_fixups)

    def bytes_unlinked(self) -> bytes:
        """Raw bytes with call rel32 fields still zero."""
        return bytes(self._buf)

    def extend(self, other: "Asm") -> "Asm":
        """Append another builder's code (fixups are relocated)."""
        off = len(self._buf)
        for fo, target in other._call_fixups:
            self._call_fixups.append((off + fo, target))
        for io, ln, text in other._insns:
            self._insns.append((off + io, ln, text))
        self._buf += other._buf
        return self

    def link(self, remote_base: int) -> bytes:
        """Resolve ``call rel32`` fixups for code loaded at ``remote_base``."""
        if not (0 <= remote_base <= 0xFFFFFFFF):
            raise AsmError("remote_base 0x%X outside the 32-bit address space" % remote_base)
        out = bytearray(self._buf)
        for off, target in self._call_fixups:
            next_ip = remote_base + off + 4
            rel = (target - next_ip) & 0xFFFFFFFF
            out[off:off + 4] = struct.pack("<I", rel)
        return bytes(out)

    def dump(self, base: int = 0) -> str:
        """Hex listing: ``offset  bytes  mnemonic`` per instruction."""
        data = self.link(base) if self._call_fixups else bytes(self._buf)
        lines = []
        for off, ln, text in self._insns:
            hexs = " ".join("%02X" % b for b in data[off:off + ln])
            lines.append("%08X  %-24s %s" % (base + off, hexs, text))
        return "\n".join(lines)

    # -- moves ---------------------------------------------------------------
    def mov_reg_imm32(self, reg: str, imm: int) -> "Asm":
        """``mov r32, imm32`` (B8+r id)."""
        r = _reg(reg)
        return self._emit(bytes([0xB8 + r]) + _imm32(imm), "mov %s, 0x%X" % (reg, imm & 0xFFFFFFFF))

    def mov_reg_reg(self, dst: str, src: str) -> "Asm":
        """``mov r32, r32`` (89 /r)."""
        d, s = _reg(dst), _reg(src)
        return self._emit(bytes([0x89, _modrm(0b11, s, d)]), "mov %s, %s" % (dst, src))

    def mov_reg_mem(self, reg: str, addr: int) -> "Asm":
        """``mov r32, [addr]`` -- A1 moffs32 for eax, 8B /r [disp32] otherwise."""
        r = _reg(reg)
        if r == REGS["eax"]:
            code = b"\xA1" + _imm32(addr, "addr")
        else:
            code = b"\x8B" + _mem_abs(r, addr)
        return self._emit(code, "mov %s, [0x%X]" % (reg, addr))

    def mov_mem_reg(self, addr: int, reg: str) -> "Asm":
        """``mov [addr], r32`` -- A3 for eax, 89 /r [disp32] otherwise."""
        r = _reg(reg)
        if r == REGS["eax"]:
            code = b"\xA3" + _imm32(addr, "addr")
        else:
            code = b"\x89" + _mem_abs(r, addr)
        return self._emit(code, "mov [0x%X], %s" % (addr, reg))

    def mov_reg_regdisp(self, dst: str, src: str, disp: int) -> "Asm":
        """``mov r32, [base + disp32]`` (8B /r)."""
        d, s = _reg(dst), _reg(src)
        return self._emit(b"\x8B" + _mem_regdisp(d, s, disp),
                          "mov %s, [%s+0x%X]" % (dst, src, disp & 0xFFFFFFFF))

    def mov_regdisp_reg(self, base: str, disp: int, src: str) -> "Asm":
        """``mov [base + disp32], r32`` (89 /r)."""
        b, s = _reg(base), _reg(src)
        return self._emit(b"\x89" + _mem_regdisp(s, b, disp),
                          "mov [%s+0x%X], %s" % (base, disp & 0xFFFFFFFF, src))

    def mov_mem_imm32(self, addr: int, imm: int) -> "Asm":
        """``mov dword [addr], imm32`` (C7 05 disp32 imm32)."""
        return self._emit(b"\xC7" + _mem_abs(0, addr) + _imm32(imm),
                          "mov dword [0x%X], 0x%X" % (addr, imm & 0xFFFFFFFF))

    def mov_regdisp_imm32(self, base: str, disp: int, imm: int) -> "Asm":
        """``mov dword [base + disp32], imm32`` (C7 /0 disp32 imm32)."""
        b = _reg(base)
        return self._emit(b"\xC7" + _mem_regdisp(0, b, disp) + _imm32(imm),
                          "mov dword [%s+0x%X], 0x%X" % (base, disp & 0xFFFFFFFF, imm & 0xFFFFFFFF))

    def lea_reg_regdisp(self, dst: str, src: str, disp: int) -> "Asm":
        """``lea r32, [base + disp32]`` (8D /r)."""
        d, s = _reg(dst), _reg(src)
        return self._emit(b"\x8D" + _mem_regdisp(d, s, disp),
                          "lea %s, [%s+0x%X]" % (dst, src, disp & 0xFFFFFFFF))

    # -- stack ---------------------------------------------------------------
    def push_imm32(self, imm: int) -> "Asm":
        """``push imm32`` (68 id)."""
        return self._emit(b"\x68" + _imm32(imm), "push 0x%X" % (imm & 0xFFFFFFFF))

    def push_reg(self, reg: str) -> "Asm":
        return self._emit(bytes([0x50 + _reg(reg)]), "push %s" % reg)

    def pop_reg(self, reg: str) -> "Asm":
        return self._emit(bytes([0x58 + _reg(reg)]), "pop %s" % reg)

    def push_mem(self, addr: int) -> "Asm":
        """``push dword [addr]`` (FF /6)."""
        return self._emit(b"\xFF" + _mem_abs(6, addr), "push dword [0x%X]" % addr)

    def push_regdisp(self, base: str, disp: int) -> "Asm":
        """``push dword [base + disp32]`` (FF /6)."""
        b = _reg(base)
        return self._emit(b"\xFF" + _mem_regdisp(6, b, disp),
                          "push dword [%s+0x%X]" % (base, disp & 0xFFFFFFFF))

    def pushad(self) -> "Asm":
        return self._emit(b"\x60", "pushad")

    def popad(self) -> "Asm":
        return self._emit(b"\x61", "popad")

    def pushfd(self) -> "Asm":
        return self._emit(b"\x9C", "pushfd")

    def popfd(self) -> "Asm":
        return self._emit(b"\x9D", "popfd")

    def add_esp(self, imm: int) -> "Asm":
        """``add esp, imm`` -- ``83 C4 ib`` (sign-extended imm8) when the value
        fits in -0x80..0x7F, otherwise ``81 C4 id``."""
        return self._esp_arith(0xC4, imm, "add")

    def sub_esp(self, imm: int) -> "Asm":
        """``sub esp, imm`` -- ``83 EC ib`` (sign-extended imm8) or ``81 EC id``."""
        return self._esp_arith(0xEC, imm, "sub")

    def _esp_arith(self, modrm: int, imm: int, mn: str) -> "Asm":
        if isinstance(imm, bool) or not isinstance(imm, int):
            raise AsmError("%s esp: expected int, got %r" % (mn, imm))
        text = "%s esp, 0x%X" % (mn, imm & 0xFFFFFFFF)
        if -0x80 <= imm <= 0x7F:
            return self._emit(bytes([0x83, modrm]) + _imm8(imm), text)
        return self._emit(bytes([0x81, modrm]) + _imm32(imm), text)

    # -- arithmetic / misc ---------------------------------------------------
    def xor_reg_reg(self, dst: str, src: str) -> "Asm":
        """``xor r32, r32`` (31 /r)."""
        d, s = _reg(dst), _reg(src)
        return self._emit(bytes([0x31, _modrm(0b11, s, d)]), "xor %s, %s" % (dst, src))

    def add_reg_imm32(self, reg: str, imm: int) -> "Asm":
        """``add r32, imm32`` (81 /0 id)."""
        r = _reg(reg)
        return self._emit(bytes([0x81, _modrm(0b11, 0, r)]) + _imm32(imm),
                          "add %s, 0x%X" % (reg, imm & 0xFFFFFFFF))

    def nop(self) -> "Asm":
        return self._emit(b"\x90", "nop")

    def int3(self) -> "Asm":
        """Debug only -- never ship in a remote call."""
        return self._emit(b"\xCC", "int3")

    def raw(self, data: bytes, text: str = "db") -> "Asm":
        return self._emit(bytes(data), text)

    # -- control flow --------------------------------------------------------
    def call_abs(self, addr: int) -> "Asm":
        """``call rel32`` (E8 cd) to absolute ``addr``; the displacement is
        filled in by :meth:`link`."""
        if not (0 <= addr <= 0xFFFFFFFF):
            raise AsmError("call target 0x%X outside the 32-bit address space" % addr)
        if addr == 0:
            raise AsmError("call target is 0 (function not available in this build)")
        self._call_fixups.append((len(self._buf) + 1, addr))
        return self._emit(b"\xE8\x00\x00\x00\x00", "call 0x%X" % addr)

    def call_reg(self, reg: str) -> "Asm":
        """``call r32`` (FF /2)."""
        r = _reg(reg)
        return self._emit(bytes([0xFF, _modrm(0b11, 2, r)]), "call %s" % reg)

    def ret(self, n: int = 0) -> "Asm":
        """``ret`` (C3) or ``ret imm16`` (C2 iw)."""
        if n == 0:
            return self._emit(b"\xC3", "ret")
        return self._emit(b"\xC2" + _imm16(n), "ret 0x%X" % n)

    def ends_with_ret(self) -> bool:
        """True when the last emitted instruction is ``ret`` / ``ret imm16``."""
        if not self._insns:
            return False
        off, ln, _ = self._insns[-1]
        last = bytes(self._buf[off:off + ln])
        return last == b"\xC3" or (len(last) == 3 and last[0] == 0xC2)

    def strip_trailing_ret(self) -> int:
        """Remove every trailing ``ret`` / ``ret imm16`` instruction (in place);
        returns how many were removed.  Used by the remote executor, whose
        wrapper owns the epilogue."""
        n = 0
        while self.ends_with_ret():
            off, ln, _ = self._insns.pop()
            del self._buf[off:off + ln]
            self._call_fixups = [(fo, t) for fo, t in self._call_fixups if fo < off]
            n += 1
        return n

    def copy(self) -> "Asm":
        out = Asm()
        out._buf = bytearray(self._buf)
        out._call_fixups = list(self._call_fixups)
        out._insns = list(self._insns)
        return out

    def __repr__(self) -> str:
        return "Asm(%d bytes, %d call fixups)" % (len(self._buf), len(self._call_fixups))
