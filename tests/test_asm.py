"""Encoding tests for pvztools.control.asm (checked against the Intel SDM)."""
from __future__ import annotations

import pytest

from pvztools.control.asm import Asm, AsmError


def enc(build) -> bytes:
    a = Asm()
    build(a)
    return a.link(0)


def hx(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


def test_mov_reg_imm32():
    assert enc(lambda a: a.mov_reg_imm32("eax", 0x12345678)) == hx("B8 78 56 34 12")
    assert enc(lambda a: a.mov_reg_imm32("edi", 1)) == hx("BF 01 00 00 00")
    assert enc(lambda a: a.mov_reg_imm32("ecx", -1)) == hx("B9 FF FF FF FF")


def test_mov_reg_mem():
    assert enc(lambda a: a.mov_reg_mem("eax", 0x731C50)) == hx("A1 50 1C 73 00")
    assert enc(lambda a: a.mov_reg_mem("ecx", 0x731C50)) == hx("8B 0D 50 1C 73 00")
    assert enc(lambda a: a.mov_reg_mem("esi", 0x731C50)) == hx("8B 35 50 1C 73 00")


def test_mov_mem_reg():
    assert enc(lambda a: a.mov_mem_reg(0x731C50, "eax")) == hx("A3 50 1C 73 00")
    assert enc(lambda a: a.mov_mem_reg(0x731C50, "edx")) == hx("89 15 50 1C 73 00")


def test_mov_reg_regdisp():
    assert enc(lambda a: a.mov_reg_regdisp("esi", "eax", 0x868)) == hx("8B B0 68 08 00 00")
    assert enc(lambda a: a.mov_reg_regdisp("eax", "esi", 0x5578)) == hx("8B 86 78 55 00 00")
    # esp base needs a SIB byte; ebp base is fine with mod=10
    assert enc(lambda a: a.mov_reg_regdisp("eax", "esp", 4)) == hx("8B 84 24 04 00 00 00")
    assert enc(lambda a: a.mov_reg_regdisp("eax", "ebp", 8)) == hx("8B 85 08 00 00 00")


def test_mov_regdisp_reg_and_lea():
    assert enc(lambda a: a.mov_regdisp_reg("esi", 0x5578, "eax")) == hx("89 86 78 55 00 00")
    assert enc(lambda a: a.lea_reg_regdisp("ecx", "esp", 0x10)) == hx("8D 8C 24 10 00 00 00")


def test_mov_mem_imm32():
    assert enc(lambda a: a.mov_mem_imm32(0x731C50, 0x270F)) == hx("C7 05 50 1C 73 00 0F 27 00 00")


def test_mov_regdisp_imm32():
    assert enc(lambda a: a.mov_regdisp_imm32("esi", 0x5578, 9990)) == hx("C7 86 78 55 00 00 06 27 00 00")
    assert enc(lambda a: a.mov_regdisp_imm32("esp", 0, 1)) == hx("C7 84 24 00 00 00 00 01 00 00 00")


def test_mov_reg_reg_xor():
    assert enc(lambda a: a.mov_reg_reg("ebp", "esp")) == hx("89 E5")
    assert enc(lambda a: a.mov_reg_reg("esp", "ebp")) == hx("89 EC")
    assert enc(lambda a: a.xor_reg_reg("eax", "eax")) == hx("31 C0")


def test_push_pop():
    assert enc(lambda a: a.push_imm32(5)) == hx("68 05 00 00 00")
    assert enc(lambda a: a.push_imm32(-1)) == hx("68 FF FF FF FF")
    assert enc(lambda a: a.push_reg("eax")) == b"\x50"
    assert enc(lambda a: a.push_reg("edi")) == b"\x57"
    assert enc(lambda a: a.pop_reg("ecx")) == b"\x59"
    assert enc(lambda a: a.push_mem(0x731C50)) == hx("FF 35 50 1C 73 00")
    assert enc(lambda a: a.push_regdisp("eax", 0x868)) == hx("FF B0 68 08 00 00")


def test_flags_and_all_regs():
    assert enc(lambda a: a.pushad().pushfd().popfd().popad()) == hx("60 9C 9D 61")


def test_esp_adjust():
    assert enc(lambda a: a.add_esp(8)) == hx("83 C4 08")
    assert enc(lambda a: a.sub_esp(0x10)) == hx("83 EC 10")
    # review finding: 83 /0 ib sign-extends, so 0x80..0xFF must use the id form
    assert enc(lambda a: a.add_esp(0x90)) == hx("81 C4 90 00 00 00")
    assert enc(lambda a: a.add_esp(0x1000)) == hx("81 C4 00 10 00 00")
    assert enc(lambda a: a.add_esp(-4)) == hx("83 C4 FC")
    assert enc(lambda a: a.sub_esp(0x80)) == hx("81 EC 80 00 00 00")
    assert enc(lambda a: a.add_esp(0x7F)) == hx("83 C4 7F")
    assert enc(lambda a: a.add_reg_imm32("esp", 0x100)) == hx("81 C4 00 01 00 00")


def test_misc():
    assert enc(lambda a: a.nop()) == b"\x90"
    assert enc(lambda a: a.int3()) == b"\xCC"
    assert enc(lambda a: a.raw(b"\x01\x02")) == b"\x01\x02"
    assert enc(lambda a: a.ret()) == b"\xC3"
    assert enc(lambda a: a.ret(4)) == hx("C2 04 00")
    assert enc(lambda a: a.call_reg("eax")) == hx("FF D0")


def test_call_rel32_link():
    a = Asm().call_abs(0x4105A0)
    # rel = target - (base + 5) = 0x4105A0 - 0x1005 = 0x40F59B
    assert a.link(0x1000) == hx("E8 9B F5 40 00")
    assert a.bytes_unlinked() == hx("E8 00 00 00 00")
    # backwards call (negative displacement) wraps to 32 bits
    b = Asm().call_abs(0x1000)
    assert b.link(0x2000) == hx("E8 FB EF FF FF")
    # preceding bytes shift the fixup offset
    c = Asm().push_imm32(1).call_abs(0x4105A0)
    assert c.link(0x1000) == hx("68 01 00 00 00") + hx("E8 96 F5 40 00")


def test_extend_relocates_fixups():
    inner = Asm().call_abs(0x4105A0)
    outer = Asm().pushad().extend(inner).popad()
    assert outer.fixups == [(2, 0x4105A0)]
    assert outer.link(0x1000) == b"\x60" + hx("E8 9A F5 40 00") + b"\x61"


def test_chaining_and_len():
    a = Asm().mov_reg_mem("eax", 0x731C50).mov_reg_regdisp("eax", "eax", 0x868).ret()
    assert len(a) == 5 + 6 + 1
    assert a.size == 12


def test_dump_lists_instructions():
    d = Asm().mov_reg_imm32("eax", 1).call_abs(0x4105A0).ret().dump(0x1000)
    lines = d.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("00001000  B8 01 00 00 00")
    assert "call 0x4105A0" in lines[1]
    assert "E8 96 F5 40 00" in lines[1]
    assert lines[2].endswith("ret")


def test_errors():
    with pytest.raises(AsmError):
        Asm().mov_reg_imm32("rax", 1)
    with pytest.raises(AsmError):
        Asm().push_imm32(1 << 32)
    with pytest.raises(AsmError):
        Asm().add_esp(1 << 32)
    with pytest.raises(AsmError):
        Asm().call_abs(0)
    with pytest.raises(AsmError):
        Asm().call_abs(1 << 32)
    with pytest.raises(AsmError):
        Asm().call_abs(0x1000).link(1 << 32)
    with pytest.raises(AsmError):
        Asm().push_imm32(True)
