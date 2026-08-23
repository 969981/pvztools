from __future__ import annotations

import os
import struct
import sys

import pytest

from pvztools._win import kernel32 as k32
from pvztools._win.pe import (
    PEError, extract_game_pe, find_embedded_pes, parse_pe, read_live_pe,
    section_of, va_to_file_offset, file_offset_to_va, rva_to_va, va_to_rva,
    sha256_hex,
)

GAME_EXE = r"C:\Program Files (x86)\Steam\steamapps\common\Plants vs Zombies\PlantsVsZombies.exe"
IS_WIN = sys.platform == "win32"


# --------------------------------------------------------------------------
# Synthetic PE builder
# --------------------------------------------------------------------------
def build_pe(image_base=0x400000, stamp=0x12345678, text_size=0x1000,
             data_size=0x200, dll_chars=0, e_lfanew=0x80, entry_rva=0x1000,
             with_pad=True) -> bytes:
    """Minimal 32-bit PE: headers (0x400) + .text + .data."""
    size_of_headers = 0x400
    text_raw = size_of_headers
    data_raw = text_raw + text_size
    secs = [
        (b".text", 0x1000, text_size, text_raw, text_size, 0x60000020),
        (b".data", 0x1000 + ((text_size + 0xFFF) & ~0xFFF), data_size, data_raw, data_size, 0xC0000040),
    ]
    last = secs[-1]
    size_of_image = (last[1] + last[2] + 0xFFF) & ~0xFFF

    dos = bytearray(b"MZ" + b"\0" * 0x3E)
    struct.pack_into("<I", dos, 0x3C, e_lfanew)
    hdr = bytearray(dos) + b"\0" * (e_lfanew - len(dos))
    hdr += b"PE\0\0"
    opt_size = 0xE0
    hdr += struct.pack("<HHIIIHH", 0x14C, len(secs), stamp, 0, 0, opt_size, 0x102)
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, 0x10B)
    struct.pack_into("<I", opt, 16, entry_rva)
    struct.pack_into("<I", opt, 28, image_base)
    struct.pack_into("<I", opt, 32, 0x1000)  # SectionAlignment
    struct.pack_into("<I", opt, 36, 0x200)   # FileAlignment
    struct.pack_into("<I", opt, 56, size_of_image)
    struct.pack_into("<I", opt, 60, size_of_headers)
    struct.pack_into("<H", opt, 70, dll_chars)
    hdr += opt
    for name, va, vsize, raw_ptr, raw_size, chars in secs:
        hdr += struct.pack("<8sIIIIIIHHI", name.ljust(8, b"\0"), vsize, va, raw_size, raw_ptr,
                           0, 0, 0, 0, chars)
    assert len(hdr) <= size_of_headers
    img = bytearray(hdr) + b"\0" * (size_of_headers - len(hdr))
    img += bytes(range(256)) * (text_size // 256) + b"\xCC" * (text_size % 256)
    img += b"\xDD" * data_size
    return bytes(img)


# --------------------------------------------------------------------------
# parse_pe
# --------------------------------------------------------------------------
def test_parse_minimal_pe():
    data = build_pe()
    pe = parse_pe(data)
    assert pe.stamp == 0x12345678
    assert pe.image_base == 0x400000
    assert pe.machine == 0x14C
    assert pe.is_32bit
    assert not pe.is_aslr
    assert pe.entry == 0x401000
    assert pe.size_of_headers == 0x400
    assert [s.name for s in pe.sections] == [".text", ".data"]
    t = pe.text
    assert t is not None and t.va == 0x401000 and t.vsize == 0x1000 and t.raw_ptr == 0x400
    assert t.executable and not t.writable
    assert pe.section(".data").writable
    assert pe.file_size == len(data)
    assert pe.size_of_image == 0x3000


def test_aslr_flag():
    pe = parse_pe(build_pe(dll_chars=0x0140))
    assert pe.is_aslr
    assert pe.dll_characteristics == 0x0140


def test_parse_rejects_garbage():
    with pytest.raises(PEError):
        parse_pe(b"\0" * 0x200)
    bad = bytearray(build_pe())
    bad[0x80:0x84] = b"XX\0\0"
    with pytest.raises(PEError):
        parse_pe(bytes(bad))
    with pytest.raises(PEError):
        parse_pe(build_pe()[:0x90])


def test_address_helpers():
    pe = parse_pe(build_pe())
    assert rva_to_va(pe, 0x1000) == 0x401000
    assert va_to_rva(pe, 0x401234) == 0x1234
    assert section_of(pe, 0x401234).name == ".text"
    assert section_of(pe, 0x402010).name == ".data"
    assert section_of(pe, 0x409000) is None
    assert va_to_file_offset(pe, 0x401234) == 0x400 + 0x234
    assert va_to_file_offset(pe, 0x402010) == 0x1400 + 0x10
    assert va_to_file_offset(pe, 0x400040) == 0x40          # header bytes
    assert va_to_file_offset(pe, 0x402300) is None          # beyond raw_size
    assert va_to_file_offset(pe, 0x500000) is None
    assert file_offset_to_va(pe, 0x400 + 0x234) == 0x401234
    assert file_offset_to_va(pe, 0x10) == 0x400010
    assert file_offset_to_va(pe, 0x100000) is None


def test_read_live_pe_via_reader():
    data = build_pe(stamp=0xAABBCCDD)
    base = 0x400000
    calls = []

    def reader(addr, n):
        calls.append((addr, n))
        off = addr - base
        return data[off:off + n]

    pe = read_live_pe(reader, base)
    assert pe.stamp == 0xAABBCCDD
    assert pe.text.va == 0x401000
    assert all(a >= base for a, _ in calls)


# --------------------------------------------------------------------------
# embedded PEs
# --------------------------------------------------------------------------
def test_find_and_extract_embedded():
    outer = build_pe(stamp=0x1, text_size=0x400, image_base=0x10000000)
    inner = build_pe(stamp=0x2, text_size=0x3000, image_base=0x400000)
    junk = b"MZ" + b"\xFF" * 0x100          # MZ with bad e_lfanew, must be ignored
    blob = outer + junk + b"\0" * 0x37 + inner + b"\0" * 0x100
    inner_off = len(outer) + len(junk) + 0x37

    found = find_embedded_pes(blob)
    assert [off for off, _ in found] == [0, inner_off]
    assert found[1][1].stamp == 0x2

    off, pe, raw = extract_game_pe(blob)
    assert off == inner_off
    assert pe.stamp == 0x2
    assert raw == inner
    assert sha256_hex(raw) == sha256_hex(inner)
    assert len(sha256_hex(raw)) == 64


def test_extract_plain_exe():
    data = build_pe(stamp=0x7)
    off, pe, raw = extract_game_pe(data)
    assert off == 0 and pe.stamp == 0x7 and raw == data


def test_extract_nothing():
    with pytest.raises(PEError):
        extract_game_pe(b"\0" * 1000)


@pytest.mark.skipif(not os.path.exists(GAME_EXE), reason="Steam PvZ not installed")
def test_real_launcher_extracts_game_pe():
    with open(GAME_EXE, "rb") as f:
        data = f.read()
    off, pe, raw = extract_game_pe(data)
    assert off == 0x1A71A2
    assert pe.stamp == 0x4D02B058
    assert pe.image_base == 0x400000
    assert not pe.is_aslr
    assert pe.machine == 0x14C
    t = pe.text
    assert t.va == 0x401000
    assert t.va + t.vsize == 0x6D4E9C
    d = pe.section(".data")
    assert d.va == 0x71F000
    assert d.va + d.vsize == 0x7E61DC
    assert raw[:2] == b"MZ"
    assert len(raw) == pe.file_size
    # a .text VA must round-trip to a file offset inside the extracted blob
    fo = va_to_file_offset(pe, 0x401000)
    assert fo == t.raw_ptr and fo < len(raw)


# --------------------------------------------------------------------------
# kernel32
# --------------------------------------------------------------------------
def test_winapierror_hint():
    e = k32.WinApiError("OpenProcess", k32.ERROR_ACCESS_DENIED, "pid=1")
    assert e.code == 5
    if IS_WIN:
        assert e.winerror == 5
    assert "管理员" in e.hint
    assert "OpenProcess" in str(e) and "管理员" in str(e)
    assert isinstance(e, OSError)


def test_region_props():
    r = k32.Region(0x1000, 0x2000, k32.MEM_COMMIT, k32.PAGE_EXECUTE_READ, k32.MEM_IMAGE)
    assert r.end == 0x3000 and r.readable and r.executable and not r.writable
    g = k32.Region(0, 0x1000, k32.MEM_COMMIT, k32.PAGE_READWRITE | k32.PAGE_GUARD, 0)
    assert not g.readable
    f = k32.Region(0, 0x1000, k32.MEM_FREE, 0, 0)
    assert not f.committed and not f.readable


def test_open_process_rights():
    # Default rights never include ALL_ACCESS; check the computed masks.
    assert k32.PROCESS_QUERY_INFORMATION | k32.PROCESS_VM_READ == 0x410
    assert (k32.PROCESS_QUERY_INFORMATION | k32.PROCESS_VM_READ | k32.PROCESS_VM_WRITE
            | k32.PROCESS_VM_OPERATION | k32.PROCESS_CREATE_THREAD) == 0x43A


@pytest.mark.skipif(IS_WIN, reason="non-Windows behaviour")
def test_non_windows_raises_cleanly():
    with pytest.raises(k32.WinApiError):
        k32.list_processes()


@pytest.mark.skipif(not IS_WIN, reason="Windows only")
def test_list_processes_and_self_read():
    procs = k32.list_processes()
    assert any(p.pid == os.getpid() for p in procs)
    h = k32.open_process(os.getpid(), write=True)
    try:
        assert not k32.is_wow64(h) or struct.calcsize("P") == 4
        path = k32.process_image_path(h)
        assert path.lower().endswith(".exe")
        # read our own memory via RPM
        import ctypes
        buf = ctypes.create_string_buffer(b"hello-rpm")
        addr = ctypes.addressof(buf)
        assert k32.read(h, addr, 9) == b"hello-rpm"
        assert k32.read_u8(h, addr) == ord("h")
        k32.write(h, addr, b"HELLO")
        assert buf.raw[:5] == b"HELLO"
        v = ctypes.c_uint32(0)
        k32.write_u32(h, ctypes.addressof(v), 0xDEADBEEF)
        assert v.value == 0xDEADBEEF
        assert k32.read_u32(h, ctypes.addressof(v)) == 0xDEADBEEF
        assert k32.read_i32(h, ctypes.addressof(v)) == -559038737
        k32.write_f32(h, ctypes.addressof(v), 1.5)
        assert k32.read_f32(h, ctypes.addressof(v)) == 1.5
        regs = list(k32.iter_regions(h, 0, 0x1_0000_0000))
        assert regs and any(r.readable for r in regs)
        with pytest.raises(k32.WinApiError):
            k32.read(h, 0, 4)
    finally:
        k32.close_handle(h)


@pytest.mark.skipif(not IS_WIN, reason="Windows only")
def test_live_game_readonly():
    pids = k32.find_processes("popcapgame1.exe")
    if not pids:
        pytest.skip("game not running")
    h = k32.open_process(pids[0])
    try:
        assert k32.is_wow64(h)
        mod = k32.main_module(h)
        assert mod.base == 0x400000
        assert mod.name.lower() == "popcapgame1.exe"
        pe = read_live_pe(lambda a, n: k32.read(h, a, n), mod.base)
        assert pe.stamp == 0x4D02B058
        assert pe.size_of_image == mod.size
        board = k32.read_ptr(h, 0x731C50 + 0x868)
        assert board == 0 or board >= 0x10000
    finally:
        k32.close_handle(h)
