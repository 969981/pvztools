"""Win32 primitives (ctypes only).

Everything here is importable on any platform; the actual ``windll`` lookups
happen lazily inside the functions, so non-Windows hosts can import the
package (e.g. to use :mod:`pvztools._win.pe`, which is pure Python).

Design notes (DESIGN §3.6): the controlled process is a 32-bit WOW64 game,
driven from 64-bit Python.  All *target* pointers are 4 bytes and are passed
around as plain ``int``; only handles and local buffer pointers use native
pointer size.
"""
from __future__ import annotations

from .kernel32 import (  # noqa: F401
    WinApiError,
    Region,
    ModuleInfo,
    ProcessEntry,
    is_windows,
    open_process,
    close_handle,
    read,
    write,
    read_u8,
    read_u16,
    read_u32,
    read_i32,
    read_f32,
    read_ptr,
    write_u8,
    write_u16,
    write_u32,
    write_i32,
    write_f32,
    iter_regions,
    list_processes,
    find_processes,
    enum_modules,
    main_module,
    is_wow64,
    process_image_path,
    last_error,
)
from .pe import (  # noqa: F401
    PEInfo,
    Section,
    parse_pe,
    find_embedded_pes,
    extract_game_pe,
    read_live_pe,
    va_to_file_offset,
    section_of,
    rva_to_va,
    va_to_rva,
    sha256_hex,
)
