"""Pure-Python PE (Portable Executable) header parser.

Works over ``bytes`` (a file image) or over a reader callback
``reader(addr, n) -> bytes`` (a live in-memory image).  Only the headers and
section table are parsed; no imports/exports/relocations.

Handles the PopCap DRM launcher case: ``PlantsVsZombies.exe`` is a wrapper
with the real game PE embedded in plaintext; :func:`extract_game_pe` picks
the embedded PE with the largest ``.text`` section.
"""
from __future__ import annotations

import hashlib
import struct
from typing import Callable, List, NamedTuple, Optional, Tuple

__all__ = [
    "PEError", "PEInfo", "Section",
    "parse_pe", "find_embedded_pes", "extract_game_pe", "read_live_pe",
    "va_to_file_offset", "file_offset_to_va", "section_of", "rva_to_va",
    "va_to_rva", "sha256_hex",
    "IMAGE_FILE_MACHINE_I386", "IMAGE_FILE_MACHINE_AMD64",
    "IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE",
    "IMAGE_SCN_CNT_CODE", "IMAGE_SCN_MEM_EXECUTE", "IMAGE_SCN_MEM_READ",
    "IMAGE_SCN_MEM_WRITE",
]

IMAGE_FILE_MACHINE_I386 = 0x014C
IMAGE_FILE_MACHINE_AMD64 = 0x8664
IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE = 0x0040
IMAGE_NT_OPTIONAL_HDR32_MAGIC = 0x10B
IMAGE_NT_OPTIONAL_HDR64_MAGIC = 0x20B

IMAGE_SCN_CNT_CODE = 0x00000020
IMAGE_SCN_CNT_INITIALIZED_DATA = 0x00000040
IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_READ = 0x40000000
IMAGE_SCN_MEM_WRITE = 0x80000000

_DOS_HEADER_SIZE = 0x40
_FILE_HEADER_SIZE = 20
_SECTION_SIZE = 40


class PEError(ValueError):
    """Malformed or unsupported PE image."""


class Section(NamedTuple):
    name: str
    va: int        # absolute virtual address (image_base + VirtualAddress)
    vsize: int     # Misc.VirtualSize
    raw_ptr: int   # PointerToRawData (relative to the PE's own start)
    raw_size: int  # SizeOfRawData
    chars: int     # Characteristics

    @property
    def end(self) -> int:
        return self.va + max(self.vsize, self.raw_size)

    @property
    def executable(self) -> bool:
        return bool(self.chars & IMAGE_SCN_MEM_EXECUTE)

    @property
    def writable(self) -> bool:
        return bool(self.chars & IMAGE_SCN_MEM_WRITE)

    def contains_va(self, va: int) -> bool:
        return self.va <= va < self.end


class PEInfo(NamedTuple):
    stamp: int               # TimeDateStamp
    image_base: int
    size_of_image: int
    entry: int               # absolute VA of AddressOfEntryPoint
    sections: List[Section]
    dll_characteristics: int
    machine: int
    is_aslr: bool
    size_of_headers: int = 0
    magic: int = IMAGE_NT_OPTIONAL_HDR32_MAGIC
    checksum: int = 0

    @property
    def is_32bit(self) -> bool:
        return self.magic == IMAGE_NT_OPTIONAL_HDR32_MAGIC

    @property
    def end(self) -> int:
        return self.image_base + self.size_of_image

    def section(self, name: str) -> Optional[Section]:
        for s in self.sections:
            if s.name == name:
                return s
        return None

    @property
    def text(self) -> Optional[Section]:
        s = self.section(".text")
        if s is not None:
            return s
        for sec in self.sections:
            if sec.chars & IMAGE_SCN_CNT_CODE:
                return sec
        return None

    @property
    def file_size(self) -> int:
        """Size of the on-disk image implied by the section table."""
        end = self.size_of_headers
        for s in self.sections:
            end = max(end, s.raw_ptr + s.raw_size)
        return end


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _u16(b: bytes, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def _u64(b: bytes, off: int) -> int:
    return struct.unpack_from("<Q", b, off)[0]


def _e_lfanew(data: bytes, off: int = 0) -> int:
    if len(data) < off + _DOS_HEADER_SIZE or data[off:off + 2] != b"MZ":
        raise PEError("not an MZ image at offset 0x%X" % off)
    e_lfanew = _u32(data, off + 0x3C)
    if e_lfanew < _DOS_HEADER_SIZE or e_lfanew > 0x1000:
        raise PEError("bad e_lfanew 0x%X" % e_lfanew)
    return e_lfanew


def _parse_headers(get: Callable[[int, int], bytes]) -> PEInfo:
    """``get(off, n)`` returns bytes at image-relative offset ``off``."""
    dos = get(0, _DOS_HEADER_SIZE)
    e_lfanew = _e_lfanew(dos)
    nt = get(e_lfanew, 4 + _FILE_HEADER_SIZE)
    if len(nt) < 4 + _FILE_HEADER_SIZE or nt[:4] != b"PE\0\0":
        raise PEError("missing PE signature at 0x%X" % e_lfanew)
    fh = nt[4:]
    machine = _u16(fh, 0)
    nsec = _u16(fh, 2)
    stamp = _u32(fh, 4)
    opt_size = _u16(fh, 16)
    if nsec == 0 or nsec > 96:
        raise PEError("implausible section count %d" % nsec)
    opt_off = e_lfanew + 4 + _FILE_HEADER_SIZE
    opt = get(opt_off, opt_size)
    if len(opt) < 2:
        raise PEError("missing optional header")
    magic = _u16(opt, 0)
    if magic == IMAGE_NT_OPTIONAL_HDR32_MAGIC:
        if len(opt) < 0x60:
            raise PEError("optional header too short")
        entry_rva = _u32(opt, 16)
        image_base = _u32(opt, 28)
        size_of_image = _u32(opt, 56)
        size_of_headers = _u32(opt, 60)
        checksum = _u32(opt, 64)
        dll_chars = _u16(opt, 70)
    elif magic == IMAGE_NT_OPTIONAL_HDR64_MAGIC:
        if len(opt) < 0x70:
            raise PEError("optional header too short")
        entry_rva = _u32(opt, 16)
        image_base = _u64(opt, 24)
        size_of_image = _u32(opt, 56)
        size_of_headers = _u32(opt, 60)
        checksum = _u32(opt, 64)
        dll_chars = _u16(opt, 70)
    else:
        raise PEError("unknown optional header magic 0x%X" % magic)
    sec_off = opt_off + opt_size
    table = get(sec_off, nsec * _SECTION_SIZE)
    if len(table) < nsec * _SECTION_SIZE:
        raise PEError("truncated section table")
    sections: List[Section] = []
    for i in range(nsec):
        s = table[i * _SECTION_SIZE:(i + 1) * _SECTION_SIZE]
        name = s[:8].split(b"\0", 1)[0].decode("latin-1")
        sections.append(Section(
            name=name,
            va=image_base + _u32(s, 12),
            vsize=_u32(s, 8),
            raw_ptr=_u32(s, 20),
            raw_size=_u32(s, 16),
            chars=_u32(s, 36),
        ))
    return PEInfo(
        stamp=stamp,
        image_base=image_base,
        size_of_image=size_of_image,
        entry=image_base + entry_rva,
        sections=sections,
        dll_characteristics=dll_chars,
        machine=machine,
        is_aslr=bool(dll_chars & IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE),
        size_of_headers=size_of_headers,
        magic=magic,
        checksum=checksum,
    )


def parse_pe(data: bytes, offset: int = 0) -> PEInfo:
    """Parse the PE headers of the image starting at ``data[offset:]``."""
    mv = memoryview(data)

    def get(off: int, n: int) -> bytes:
        return bytes(mv[offset + off:offset + off + n])

    return _parse_headers(get)


def read_live_pe(reader: Callable[[int, int], bytes], base: int) -> PEInfo:
    """Parse the headers of an image mapped at ``base`` in a live process.
    ``reader(addr, n) -> bytes`` (e.g. ``functools.partial(kernel32.read, h)``)."""
    return _parse_headers(lambda off, n: reader(base + off, n))


def find_embedded_pes(data: bytes, start: int = 0) -> List[Tuple[int, PEInfo]]:
    """Scan for every parseable PE image inside ``data`` (``MZ`` + valid
    ``e_lfanew`` + ``PE\\0\\0``).  Returns ``[(offset, PEInfo)]`` in file order,
    including the outer image at offset 0 if present."""
    out: List[Tuple[int, PEInfo]] = []
    n = len(data)
    pos = data.find(b"MZ", start)
    while pos != -1 and pos + _DOS_HEADER_SIZE <= n:
        try:
            e_lfanew = _e_lfanew(data, pos)
            if data[pos + e_lfanew:pos + e_lfanew + 4] == b"PE\0\0":
                out.append((pos, parse_pe(data, pos)))
        except PEError:
            pass
        pos = data.find(b"MZ", pos + 1)
    return out


def extract_game_pe(data: bytes) -> Tuple[int, PEInfo, bytes]:
    """Pick the PE inside ``data`` with the largest ``.text`` (the real game
    when ``data`` is a DRM launcher; the image itself when it is a plain exe).
    Returns ``(offset, PEInfo, pe_bytes)``, where ``pe_bytes`` is the slice
    sized from the section table (``PEInfo.file_size``)."""
    found = find_embedded_pes(data)
    if not found:
        raise PEError("no PE image found")

    def text_size(item: Tuple[int, PEInfo]) -> int:
        t = item[1].text
        return max(t.vsize, t.raw_size) if t is not None else 0

    best = max(found, key=text_size)
    off, pe = best
    end = min(len(data), off + pe.file_size)
    return off, pe, bytes(data[off:end])


# --------------------------------------------------------------------------
# Address helpers
# --------------------------------------------------------------------------
def rva_to_va(pe: PEInfo, rva: int) -> int:
    return pe.image_base + rva


def va_to_rva(pe: PEInfo, va: int) -> int:
    return va - pe.image_base


def section_of(pe: PEInfo, va: int) -> Optional[Section]:
    for s in pe.sections:
        if s.contains_va(va):
            return s
    return None


def va_to_file_offset(pe: PEInfo, va: int) -> Optional[int]:
    """Map an absolute VA to an offset relative to the PE's own start.
    Returns ``None`` for VAs without file backing (uninitialised data or
    outside any section).  Header bytes map 1:1."""
    rva = va - pe.image_base
    if 0 <= rva < pe.size_of_headers:
        return rva
    s = section_of(pe, va)
    if s is None:
        return None
    delta = va - s.va
    if delta >= s.raw_size:
        return None
    return s.raw_ptr + delta


def file_offset_to_va(pe: PEInfo, off: int) -> Optional[int]:
    if 0 <= off < pe.size_of_headers:
        return pe.image_base + off
    for s in pe.sections:
        if s.raw_ptr <= off < s.raw_ptr + s.raw_size:
            return s.va + (off - s.raw_ptr)
    return None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
