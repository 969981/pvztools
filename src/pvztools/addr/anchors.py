"""Signature ("anchor") records for version-adaptive address discovery.

DESIGN §3.3: we do not search the *data* segment for a sun value; we search
the *code* segment for the instructions that touch the sun / LawnApp /
Board and pull the immediates out of those instructions.  Every anchor is a
data record (regex over ``.text`` bytes + extraction recipe + validators) so
that new builds / mods can be supported by adding records, not code.

Anchor kinds
------------
``sun_cap``
    The three codegen shapes that compare a Board field against the sun cap
    immediate 9990 (0x2706).  Each hit votes for ``board.sun``.
``lawn_global``
    Every ``mov r32, [imm32]`` whose imm32 lies in the data section.  The
    scanner groups the ``[reg+disp32]`` dereferences that follow each load and
    picks the global whose *offset family* matches LawnApp
    (``{board, game_mode, game_ui}`` = retail ``{0x768,0x7F8,0x7FC}`` or
    goty ``{0x868,0x918,0x91C}``).  Hit counts are deliberately **not** the
    criterion (the most-referenced global on Steam is 0x731C90, not LawnApp).
``null_check``
    ``mov eax,[imm32]; test eax,eax; jcc`` — secondary vote for the same
    global.
``board_shift``
    Histogram of ``[reg+disp32]`` with disp32 in ``0x5400..0x5700`` compared
    against the 1051 Board offset set under a sliding shift; must agree with
    ``sun - 0x5560``.
``function``
    Prologue signatures (rel32 / absolute imm32 / shiftable disp32
    wildcarded); expect exactly one hit.
``patch``
    Context signature around a patch site; the bytes at the site must equal
    the expected "off" bytes or the site is rejected.

Signature text format: ``"8B 0D ?? ?? ?? ?? 85 C9"`` — hex bytes, ``??`` is a
one-byte wildcard.  :func:`compile_sig` turns it into a ``re`` pattern with
``DOTALL`` so ``.`` matches any byte.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Anchor", "compile_sig", "sig_from_bytes", "generalize", "sig_to_text",
    "SUN_CAP_ANCHORS", "LAWN_GLOBAL_ANCHORS", "NULL_CHECK_ANCHORS",
    "BOARD_SHIFT_ANCHOR", "FUNCTION_ANCHORS", "PATCH_ANCHORS", "DEFAULT_ANCHORS",
    "LAWN_FAMILIES", "BOARD_1051_HIGH_FIELDS", "SUN_1051", "SUN_CAP",
    "MODRM_DISP32",
    "BOARD_SIZE_1051", "CORE_FUNCTIONS",
    "derive_function_signatures", "derive_patch_signatures",
    "anchors_from_profile",
]

SUN_CAP = 9990
SUN_1051 = 0x5560
BOARD_SIZE_1051 = 0x57B0          # DESIGN §3.4 (GOTY increment via shift)

#: LawnApp offset families (DESIGN §3.3 / parity §6).  ``core`` members are
#: required (at least two of them must be observed); ``extra`` members only
#: add confidence.  Values are disp32 used when dereferencing the global.
LAWN_FAMILIES: Dict[str, Dict[str, Dict[str, int]]] = {
    "retail": {
        "core": {"board": 0x768, "game_mode": 0x7F8, "game_ui": 0x7FC},
        "extra": {"tod_mode": 0x7F5, "player_info": 0x82C, "frame_duration": 0x454,
                  "game_selector": 0x770, "free_planting": 0x814, "music": 0x83C},
    },
    "goty": {
        "core": {"board": 0x868, "game_mode": 0x918, "game_ui": 0x91C},
        "extra": {"tod_mode": 0x915, "player_info": 0x94C, "frame_duration": 0x4B4,
                  "game_selector": 0x870, "free_planting": 0x934, "music": 0x95C},
    },
    # zh 2010/2012 GOTY: LawnApp fields >= 0x7F5 shift by +0x124 (profiles report)
    "goty_zh": {
        "core": {"board": 0x868, "game_mode": 0x91C, "game_ui": 0x920},
        "extra": {"tod_mode": 0x919, "player_info": 0x950, "frame_duration": 0x4B4,
                  "game_selector": 0x870, "free_planting": 0x938, "music": 0x960},
    },
}

#: 1051 Board fields in the 0x5000+ range (lonelystar map / DESIGN §4); the
#: board-shift histogram slides this set over the observed disp32 histogram.
BOARD_1051_HIGH_FIELDS: Dict[str, int] = {
    "allowed_types": 0x54D4, "scene": 0x554C, "adventure_level": 0x5550,
    "sun": 0x5560, "wave_total": 0x5564, "main_counter": 0x5568,
    "global_counter": 0x556C, "wave": 0x557C, "wave_hp_threshold": 0x5594,
    "countdown": 0x559C, "countdown_start": 0x55A0, "huge_wave_countdown": 0x55A4,
    "debug_mode": 0x55F8, "particle_system_ref": 0x5620,
}

#: Functions whose presence is required for capability FULL (all Toolkit-known,
#: so every official profile can reach FULL; DESIGN extras such as cob_fire /
#: kill_zombie only add features).
CORE_FUNCTIONS = ("put_plant", "delete_plant", "put_zombie", "pick_zombie_waves")


@dataclass(frozen=True)
class Anchor:
    """One signature record.

    ``pattern``   bytes regex over ``.text`` (compiled with DOTALL by the scanner)
    ``extract``   ``((fmt, offset, label), ...)`` — ``fmt`` in ``u8/u16/u32/i32``,
                  ``offset`` relative to the match start, label names the value
    ``validators``  free-form strings interpreted by the scanner
                  (``"in_data"``, ``"in_text"``, ``"sun_off"``, ``"eq_off"``)
    ``expect``    ``"==1"`` (unique), ``">=1"`` or ``"vote"`` (any number, each
                  hit is a vote)
    ``meta``      extra data (expected off bytes, site index, ...)
    """
    name: str
    kind: str
    pattern: bytes
    extract: Tuple[Tuple[str, int, str], ...] = ()
    validators: Tuple[str, ...] = ()
    expect: str = "vote"
    meta: Dict[str, Any] = field(default_factory=dict)

    def compiled(self) -> "re.Pattern[bytes]":
        return re.compile(self.pattern, re.DOTALL)


# ---------------------------------------------------------------------------
# signature text helpers
# ---------------------------------------------------------------------------
def compile_sig(text: str) -> bytes:
    """``"8B 0D ?? ?? ?? ?? 85 C9"`` → regex bytes (``??`` → ``.``)."""
    out = bytearray()
    for tok in text.split():
        if tok in ("??", "?"):
            out += b"."
        else:
            out += re.escape(bytes([int(tok, 16)]))
    return bytes(out)


def sig_from_bytes(raw: bytes, mask: Sequence[bool]) -> bytes:
    """Build a regex from ``raw`` where ``mask[i]`` False means wildcard."""
    out = bytearray()
    for b, keep in zip(raw, mask):
        out += re.escape(bytes([b])) if keep else b"."
    return bytes(out)


def sig_to_text(raw: bytes, mask: Sequence[bool]) -> str:
    return " ".join("%02X" % b if keep else "??" for b, keep in zip(raw, mask))


def generalize(raw: bytes, image_range: Tuple[int, int],
               shiftable: Tuple[int, int] = (0x100, 0x10000)) -> List[bool]:
    """Mask out bytes that vary between builds of the same source:

    * rel32 after ``E8``/``E9`` (call / jmp)
    * any little-endian u32 inside ``image_range`` (absolute addresses)
    * any u32 in ``shiftable`` (struct disp32 / immediates that move when a
      struct grows, e.g. Board fields 0x5560 → 0x5578)
    """
    n = len(raw)
    keep = [True] * n
    lo, hi = image_range
    slo, shi = shiftable
    i = 0
    while i < n:
        if raw[i] in (0xE8, 0xE9):
            # rel32 (possibly truncated at the window edge)
            for k in range(i + 1, min(i + 5, n)):
                keep[k] = False
            i += 5
            continue
        if i + 4 <= n:
            v = struct.unpack_from("<I", raw, i)[0]
            if lo <= v < hi or slo <= v < shi:
                for k in range(i, i + 4):
                    keep[k] = False
                i += 4
                continue
        i += 1
    return keep


# ---------------------------------------------------------------------------
# (1) SUN_CAP — three codegen shapes comparing a Board field with 9990
# ---------------------------------------------------------------------------
_IMM9990 = re.escape(struct.pack("<I", SUN_CAP))
#: modrm with mod=10 (disp32) and rm != 100 (no SIB byte), any reg field:
#: 0x80..0xBF minus 0x84,0x8C,...,0xBC -- the disp32 then always starts at +1.
MODRM_DISP32 = (rb"[\x80-\x83\x85-\x8B\x8D-\x93\x95-\x9B\x9D-\xA3"
                rb"\xA5-\xAB\xAD-\xB3\xB5-\xBB\xBD-\xBF]")

SUN_CAP_ANCHORS: Tuple[Anchor, ...] = (
    # cmp dword [reg+disp32], 9990          81 /7 (mod=10)  -> 81 B8..BF disp32 imm32
    Anchor("sun_cap.cmp_mem_imm", "sun_cap",
           rb"\x81[\xB8-\xBB\xBD-\xBF](....)" + _IMM9990,
           (("u32", 2, "sun"),), ("sun_off",), "vote"),
    # mov reg,[reg+disp32] ; (<=8 bytes) ; cmp reg,9990   8B /r(mod=10, no SIB) disp32 ; 81 F8..FF imm32
    Anchor("sun_cap.mov_cmp_reg", "sun_cap",
           rb"\x8B" + MODRM_DISP32 + rb"(....).{0,8}?\x81[\xF8-\xFF]" + _IMM9990,
           (("u32", 2, "sun"),), ("sun_off",), "vote"),
    # mov dword [reg+disp32], 9990          C7 /0(mod=10) disp32 imm32
    Anchor("sun_cap.mov_mem_imm", "sun_cap",
           rb"\xC7[\x80-\x83\x85-\x87](....)" + _IMM9990,
           (("u32", 2, "sun"),), ("sun_off",), "vote"),
    # add [reg+disp32], imm8/imm32 ... cmp ..., 9990 (sun += value; clamp)
    Anchor("sun_cap.add_then_cmp", "sun_cap",
           rb"(?:\x83[\x80-\x83\x85-\x87](....).|\x81[\x80-\x83\x85-\x87](....)....)"
           rb".{0,12}?\x81[\xF8-\xFF]" + _IMM9990,
           (("u32", 2, "sun"),), ("sun_off",), "vote",
           {"alt_groups": True}),
    # mov eax,[reg+disp32] ; cmp eax,9990 (short encoding 3D imm32; no SIB)
    Anchor("sun_cap.mov_cmp_eax", "sun_cap",
           rb"\x8B[\x80\x81\x82\x83\x85\x86\x87](....).{0,8}?\x3D" + _IMM9990,
           (("u32", 2, "sun"),), ("sun_off",), "vote"),
    # mov r32, 9990 ; (<=10 bytes) ; cmp r32,[reg+disp32] / cmp [reg+disp32],r32
    # (B8+r imm32 ... 3B /r | 39 /r) -- codegen that materialises the cap in a
    # register first; the disp32 is the capture group (alt_groups extraction)
    Anchor("sun_cap.imm_reg_cmp_mem", "sun_cap",
           rb"[\xB8-\xBF]" + _IMM9990 + rb".{0,10}?[\x39\x3B]" + MODRM_DISP32 + rb"(....)",
           (("u32", 0, "sun"),), ("sun_off",), "vote",
           {"alt_groups": True}),
)


# ---------------------------------------------------------------------------
# (2) LAWN_GLOBAL — global loads + null-check idiom
# ---------------------------------------------------------------------------
LAWN_GLOBAL_ANCHORS: Tuple[Anchor, ...] = (
    # A1 imm32 (mov eax,[imm32])  |  8B 05/0D/15/1D/35/3D imm32 (mov r32,[imm32])
    Anchor("lawn_global.load", "lawn_global",
           rb"(?:\xA1|\x8B[\x05\x0D\x15\x1D\x35\x3D])(....)",
           (("u32", -4, "global"),), ("in_data",), "vote",
           {"window": 24}),
)

NULL_CHECK_ANCHORS: Tuple[Anchor, ...] = (
    Anchor("lawn_global.null_check_eax", "null_check",
           rb"\xA1(....)\x85\xC0[\x74\x75\x0F]",
           (("u32", 1, "global"),), ("in_data",), "vote"),
    Anchor("lawn_global.null_check_ecx", "null_check",
           rb"\x8B\x0D(....)\x85\xC9[\x74\x75\x0F]",
           (("u32", 2, "global"),), ("in_data",), "vote"),
    Anchor("lawn_global.null_check_cmp0", "null_check",
           rb"\x83\x3D(....)\x00[\x74\x75\x0F]",
           (("u32", 2, "global"),), ("in_data",), "vote"),
)


# ---------------------------------------------------------------------------
# (3) BOARD_SHIFT — disp32 histogram in 0x5400..0x5700 (mod=10 modrm, rm!=100)
# ---------------------------------------------------------------------------
BOARD_SHIFT_ANCHOR = Anchor(
    "board.shift", "board_shift",
    rb"[\x80-\x83\x85-\xBB\xBD-\xBF](.[\x54-\x56]\x00\x00)",
    (("u32", 1, "disp"),), (), "vote",
    {"known": BOARD_1051_HIGH_FIELDS, "range": (-0x100, 0x100)})


# ---------------------------------------------------------------------------
# (4)/(5) FUNCTION / PATCH — derivation tools
# ---------------------------------------------------------------------------
def _load_profile_raw(profile: Any) -> Dict[str, Any]:
    if isinstance(profile, dict):
        return profile
    raw = getattr(profile, "raw", None)
    if raw:
        return raw
    return json.loads(Path(profile).read_text(encoding="utf-8"))


def _exe_text(exe_path: Any) -> Tuple[bytes, int, Tuple[int, int]]:
    from .._win.pe import extract_game_pe
    data = Path(exe_path).read_bytes()
    _off, pe, img = extract_game_pe(data)
    t = pe.text
    if t is None:
        raise ValueError("no .text section")
    text = img[t.raw_ptr:t.raw_ptr + t.raw_size]
    return text, t.va, (pe.image_base, pe.image_base + pe.size_of_image)


def _unique(text: bytes, pattern: bytes) -> int:
    return sum(1 for _ in re.finditer(pattern, text, re.DOTALL))


def derive_function_signatures(exe_path: Any, profile: Any, *, width: int = 32,
                               min_fixed: int = 12,
                               names: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
    """``sig-derive`` (DESIGN §3.3): for each function in ``profile`` (dict /
    Profile / json path) read ``width`` bytes at its entry in ``exe_path``,
    wildcard rel32 / absolute / shiftable immediates, and keep the pattern only
    if it hits exactly once in that exe.  Returns
    ``{name: {"sig": text, "rva": int, "hits": n, "ok": bool}}``.

    Works on any build that has a profile (1051 when the exe is available,
    Steam 1096 otherwise); the exe is a local fixture and never enters the repo.
    """
    raw = _load_profile_raw(profile)
    text, text_va, image_range = _exe_text(exe_path)
    image_base = int(str(raw["image_base"]), 16)
    out: Dict[str, Dict[str, Any]] = {}
    for name, fn in raw["functions"].items():
        if names is not None and name not in names:
            continue
        rva_s = fn.get("rva")
        if not rva_s:
            continue
        rva = int(str(rva_s), 16)
        start = image_base + rva - text_va
        chunk = text[start:start + width]
        if len(chunk) < width:
            continue
        mask = generalize(chunk, image_range)
        # never let the pattern depend on a trailing partial instruction
        fixed = sum(mask)
        pat = sig_from_bytes(chunk, mask)
        hits = _unique(text, pat)
        out[name] = {"sig": sig_to_text(chunk, mask), "rva": rva, "hits": hits,
                     "ok": hits == 1 and fixed >= min_fixed, "fixed": fixed}
    return out


def derive_patch_signatures(exe_path: Any, profile: Any, *, before: int = 10,
                            after: int = 10) -> Dict[str, List[Dict[str, Any]]]:
    """Context signature for each patch site: ``before`` bytes, the site's
    expected *off* bytes, ``after`` bytes; addresses wildcarded.  Returns
    ``{group: [{"sig", "site_offset", "off", "rva", "hits", "ok"}]}``."""
    raw = _load_profile_raw(profile)
    text, text_va, image_range = _exe_text(exe_path)
    image_base = int(str(raw["image_base"]), 16)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for gname, grp in raw["patches"].items():
        sites = []
        for s in grp.get("sites", []):
            rva = int(str(s["rva"]), 16)
            off = bytes.fromhex(s["off"])
            site = image_base + rva - text_va
            if text[site:site + len(off)] != off:
                sites.append({"rva": rva, "ok": False, "reason": "off bytes differ in exe"})
                continue
            best: Optional[Dict[str, Any]] = None
            # a site at a function entry has only padding before it: widen after
            for b, a in ((before, after), (2, before + after + 8), (before + 8, after + 8)):
                chunk = text[site - b:site + len(off) + a]
                if len(chunk) < b + len(off) + a:
                    continue
                mask = generalize(chunk, image_range)
                for k in range(b, b + len(off)):
                    mask[k] = True
                pat = sig_from_bytes(chunk, mask)
                hits = _unique(text, pat)
                cand = {"sig": sig_to_text(chunk, mask), "site_offset": b,
                        "off": s["off"], "on": s["on"], "rva": rva, "hits": hits,
                        "ok": hits == 1, "source": s.get("source", "")}
                if best is None or (cand["ok"] and not best["ok"]):
                    best = cand
                if best["ok"]:
                    break
            if best is not None:
                sites.append(best)
        out[gname] = sites
    return out


def anchors_from_profile(exe_path: Any, profile: Any) -> List[Anchor]:
    """Derive function + patch anchors from an exe/profile pair at runtime."""
    res: List[Anchor] = []
    for name, d in derive_function_signatures(exe_path, profile).items():
        if d["ok"]:
            res.append(Anchor("functions." + name, "function", compile_sig(d["sig"]),
                              (), ("in_text",), "==1", {"ref_rva": d["rva"]}))
    for gname, sites in derive_patch_signatures(exe_path, profile).items():
        for i, s in enumerate(sites):
            if s.get("ok"):
                res.append(Anchor("patches.%s.%d" % (gname, i), "patch", compile_sig(s["sig"]),
                                  (), ("in_text", "eq_off"), "==1",
                                  {"site_offset": s["site_offset"], "off": s["off"],
                                   "on": s["on"], "ref_rva": s["rva"]}))
    return res


# ---------------------------------------------------------------------------
# Shipped signatures.  Derived with derive_*_signatures() from the Steam GOTY
# 1.2.0.1096 image (stamp 0x4D02B058) at the Toolkit-known addresses and
# verified unique there; cob_fire / kill_zombie / shovel_plant / plant_card /
# mouse_down / choose_card come from the DESIGN §8 1051 list, located on Steam
# by their distinctive bodies (cob_fire: ``mov [esi+0x3C],0x26`` = cob state 38).
# Wildcards cover rel32, absolute addresses and shiftable disp32 so the same
# pattern is expected to hit retail 1051/1065 prologues; a miss simply marks
# the symbol missing (capability downgrade), never a wrong address.
# ---------------------------------------------------------------------------
_FUNCTION_SIGS: Dict[str, str] = {
    'put_plant': '51 53 55 8B 6C 24 10 56 57 8D ?? ?? ?? ?? 00 8B D8 E8 ?? ?? ?? ?? 8B 4C 24 20 8B 74 24 1C 8B F8',
    'put_plant_imitater': '53 56 57 8B F0 56 E8 ?? ?? ?? ?? 8B 86 ?? ?? ?? ?? 8B 4E 28 8B 56 04 6A 30 50 8B 46 1C 51 52 E8',
    'put_plant_iz_style': '53 55 8B 6C 24 0C 8B ?? ?? ?? ?? 00 56 8B 30 8B 86 ?? ?? ?? ?? 57 33 FF 3B C7 74 3B 8B 50 08 3B',
    'put_zombie': '53 55 8B 6C 24 0C 56 57 8B F0 8B F9 8B 47 04 56 55 33 DB E8 ?? ?? ?? ?? 8B D8 83 FD 14 0F 85 87',
    'put_zombie_in_row': '55 8B EC 83 E4 F8 83 EC 10 56 8B F0 8B ?? ?? ?? ?? 00 48 57 39 ?? ?? ?? ?? 00 72 0A 33 C0 5F 5E',
    'put_grave': '55 8B 6C 24 08 8B 55 04 56 8D 74 24 0C C7 44 ?? ?? ?? ?? 00 00 E8 ?? ?? ?? ?? 84 C0 74 26 8B FF',
    'put_ladder': '56 8B F0 81 C6 ?? ?? ?? ?? E8 ?? ?? ?? ?? 8B 54 24 08 8B CF 69 C9 ?? ?? ?? ?? 81 C1 D0 9E 04 00',
    'put_rake': '55 8B EC 83 E4 F8 83 EC 44 53 8B D8 56 8B ?? ?? ?? ?? 00 8B 86 ?? ?? ?? ?? 83 B8 ?? ?? ?? ?? 00',
    'mower_start': '83 EC 08 83 7E 2C 02 57 0F ?? ?? ?? ?? 00 8B 06 8B 88 ?? ?? ?? ?? 8B 46 1C 8B 51 08 25 FF FF 00',
    'mower_delete': '51 56 57 8B F8 8B 0F 8B 47 1C C6 47 30 01 8B 89 ?? ?? ?? ?? 85 C9 74 24 8B 51 08 85 C0 74 1D 0F',
    'mower_restore': '53 55 8B 6C 24 0C 56 57 33 FF 8D 9D ?? ?? ?? ?? 8B ?? ?? ?? ?? 00 8B 91 ?? ?? ?? ?? 83 FA 26 75',
    'delete_plant': '83 EC 10 53 55 8B 6C 24 1C 80 BD ?? ?? ?? ?? 00 56 57 74 38 83 7D 24 13 75 32 8B 4D 04 8B 95 2C',
    'delete_grid_item': '8B 0E 8B 46 34 C6 46 20 01 8B 89 ?? ?? ?? ?? 85 C9 74 2B 8B 51 08 85 C0 74 24 0F B7 C8 3B 4A 08',
    'set_plant_sleeping': '83 EC 08 53 8A 5C 24 10 56 38 9F ?? ?? ?? ?? 0F 84 ?? ?? ?? ?? 8B 4F 24 83 F9 11 75 1E 8B 47 3C',
    'puzzle_next_stage_clear': '51 8B 0F 8B 01 8B 15 ?? ?? ?? ?? 8B ?? ?? ?? ?? 00 53 56 52 FF D0 8B 4F 04 33 DB 89 ?? ?? ?? ??',
    'pick_background': '?? ?? ?? ?? 00 00 6A FF 68 ?? ?? ?? ?? 50 64 ?? ?? ?? ?? 00 00 8B ?? ?? ?? ?? 00 83 EC 38 53 33',
    'delete_particle_system': '53 8B 5C 24 08 55 56 8B 73 0C 33 ED 3B F5 74 6F 57 8B C6 8B 10 8B 43 08 8B 76 04 81 E2 FF FF 00',
    'pick_zombie_waves': '55 8B EC 83 E4 F8 8B ?? ?? ?? ?? 00 8B 91 ?? ?? ?? ?? 81 EC ?? ?? ?? ?? 53 56 ?? ?? ?? ?? 00 85',
    'remove_cutscene_zombies': '51 56 8D 74 24 04 8B D3 C7 44 ?? ?? ?? ?? 00 00 E8 ?? ?? ?? ?? 84 C0 74 25 8D ?? ?? ?? ?? 00 00',
    'place_street_zombies': '8B 54 24 04 81 ?? ?? ?? ?? 00 80 7A 35 00 53 55 56 57 0F 85 ?? ?? ?? ?? 8B 0A C6 42 35 01 83 B9',
    'play_music': '56 8B F0 39 7E 08 74 11 E8 ?? ?? ?? ?? 6A FF 83 C9 FF 8B C7 E8 ?? ?? ?? ?? 5E C3 CC CC CC CC CC',
    'sync_profile': '6A FF ?? ?? ?? ?? 00 00 68 ?? ?? ?? ?? 50 64 ?? ?? ?? ?? 00 00 83 EC 54 53 55 56 33 DB 57 8B F1',
    'fade_out_level': '6A FF ?? ?? ?? ?? 00 00 68 ?? ?? ?? ?? 50 64 ?? ?? ?? ?? 00 00 83 EC 58 53 55 56 57 8B F9 8B B7',
    'wisdom_tree_grow': '?? ?? ?? ?? 00 00 6A FF 68 ?? ?? ?? ?? 50 64 ?? ?? ?? ?? 00 00 8B 0E 8B 81 ?? ?? ?? ?? 8B 89 4C',
    'cob_fire': '51 D9 05 ?? ?? ?? ?? 53 56 51 D9 1C 24 8B F0 6A 03 68 ?? ?? ?? ?? ?? ?? ?? ?? 00 C7 46 3C 26 00',
}

_PATCH_SIGS: Dict[str, List[Dict[str, Any]]] = {
    'main_loop_block': [
        {"sig": 'FC 8B 90 ?? ?? ?? ?? FF D2 EB C8 8B E5 5D C3 CC CC CC CC CC CC',
         "site_offset": 10, "off": 'c8', "on": 'fe', "level": 'medium'},
    ],
    'sun_cap_unlock': [
        {"sig": '00 83 EC 0C 81 F9 ?? ?? ?? ?? 7E 0A C7 ?? ?? ?? ?? 00 06 27 00',
         "site_offset": 10, "off": '7e', "on": 'eb', "level": 'low'},
    ],
    'auto_collect': [
        {"sig": '14 F5 FF FF EB 16 ?? ?? ?? ?? 75 09 8B FB E8 ?? ?? ?? ?? EB 07',
         "site_offset": 10, "off": '75', "on": 'eb', "level": 'low'},
    ],
    'no_loot_drop': [
        {"sig": '10 83 7B 24 13 89 44 24 0C 75 5B 8B 03 ?? ?? ?? ?? 00 E8 ?? ??',
         "site_offset": 10, "off": '5b', "on": '66', "level": 'low'},
    ],
    'unlimited_fertilizer': [
        {"sig": '?? ?? ?? ?? 8B 82 ?? ?? ?? ?? FF 88 ?? ?? ?? ?? 8B 7D 04 E8 ??',
         "site_offset": 10, "off": 'ff', "on": '39', "level": 'low'},
    ],
    'unlimited_bug_spray': [
        {"sig": '?? ?? ?? ?? 8B 81 ?? ?? ?? ?? FF 88 ?? ?? ?? ?? 8B 7D 04 E8 ??',
         "site_offset": 10, "off": 'ff', "on": '39', "level": 'low'},
    ],
    'unlimited_chocolate': [
        {"sig": '?? ?? ?? ?? 8B 80 ?? ?? ?? ?? FF 88 ?? ?? ?? ?? 8B 45 00 83 C4',
         "site_offset": 10, "off": 'ff', "on": '39', "level": 'low'},
        {"sig": '?? ?? ?? ?? 8B 80 ?? ?? ?? ?? FF 88 ?? ?? ?? ?? 8B C7 E8 ?? ??',
         "site_offset": 10, "off": 'ff', "on": '39', "level": 'low'},
    ],
    'unlimited_tree_food': [
        {"sig": '0F 00 8B 03 8B 80 ?? ?? ?? ?? FF 88 ?? ?? ?? ?? 89 7B 54 8B 7B',
         "site_offset": 10, "off": 'ff', "on": '39', "level": 'low'},
    ],
    'place_anywhere': [
        {"sig": '53 55 E8 ?? ?? ?? ?? 85 C0 0F 84 ?? ?? ?? ?? 83 F8 02 75 50 68',
         "site_offset": 10, "off": '84', "on": '81', "level": 'low'},
        {"sig": '53 8B C2 E8 ?? ?? ?? ?? 85 C0 74 32 8B 77 04 8B 96 ?? ?? ?? ??',
         "site_offset": 10, "off": '74', "on": 'eb', "level": 'low'},
        {"sig": '8B CD E8 ?? ?? ?? ?? 85 C0 0F 84 ?? ?? ?? 00 83 C8 FF E8 ?? ??',
         "site_offset": 10, "off": '84', "on": '8d', "level": 'low'},
    ],
    'fast_conveyor': [
        {"sig": '07 00 FF 4B 5C ?? ?? ?? ?? 0F 8F ?? ?? ?? ?? D9 E8 ?? ?? ?? ??',
         "site_offset": 10, "off": '8f', "on": '80', "level": 'low'},
        {"sig": '58 8B 01 85 C0 7E 0D 48 33 D2 85 C0 0F 9E C2 4A 23 C2 89 01 47',
         "site_offset": 10, "off": '85', "on": '33', "level": 'low'},
    ],
    'sticky_shovel': [
        {"sig": 'FF 89 41 28 8B 97 ?? ?? ?? ?? 89 5A 30 8B 8F ?? ?? ?? ?? 89 41',
         "site_offset": 10, "off": '89', "on": '39', "level": 'low'},
    ],
    'rake_unlimited': [
        {"sig": '00 00 83 B8 ?? ?? ?? ?? 00 57 0F 84 ?? ?? ?? ?? 8B 86 ?? ?? ?? ??',
         "site_offset": 10, "off": '0f84', "on": '0f80', "level": 'low'},
    ],
    'mower_init_allow': [
        {"sig": '8B C7 E8 ?? ?? ?? ?? C6 46 31 00 47 83 C3 04 83 FF 06 0F 8C 5B',
         "site_offset": 10, "off": '00', "on": '01', "level": 'low'},
    ],
    'mower_init_no_anim': [
        {"sig": '08 00 CC CC CC CC CC CC D9 05 E8 68 70 00 ?? 55 8B 6C 24 0C 56 8B F0 A1',
         "site_offset": 10, "off": 'e8687000', "on": '04677000', "level": 'low'},
    ],
    'plant_invincible': [
        {"sig": '51 8B 4D 04 E8 ?? ?? ?? ?? 83 46 40 FC 8B 4E 40 C7 ?? ?? ?? ?? 00 32',
         "site_offset": 10, "off": '4640fc', "on": '464000', "level": 'low'},
        {"sig": 'E8 ?? ?? ?? ?? 83 C4 04 84 C0 74 0C FF ?? ?? ?? ?? 00 56 E8 ??',
         "site_offset": 10, "off": '74', "on": 'eb', "level": 'low'},
        {"sig": '8B 4F 1C 8B 44 24 0C 3B 48 1C 75 42 83 78 24 11 75 12 8B 48 3C',
         "site_offset": 10, "off": '75', "on": 'eb', "level": 'low'},
        {"sig": '?? ?? ?? ?? 8B ?? ?? ?? ?? 00 29 50 40 83 F9 19 7F 05 ?? ?? ?? ?? 00',
         "site_offset": 10, "off": '295040', "on": '909090', "level": 'low'},
        {"sig": '?? ?? ?? ?? 8B ?? ?? ?? ?? 00 29 4E 40 83 F8 19 7F 05 ?? ?? ?? ?? 00',
         "site_offset": 10, "off": '294e40', "on": '909090', "level": 'low'},
        {"sig": '00 00 00 E8 ?? ?? ?? ?? 84 C0 74 53 ?? ?? ?? ?? 00 8B 4C 24 0C',
         "site_offset": 10, "off": '74', "on": 'eb', "level": 'low'},
        {"sig": '1A 83 7D 24 2E BF ?? ?? ?? ?? 75 09 8B F5 E8 ?? ?? ?? ?? EB 06',
         "site_offset": 10, "off": '75', "on": '70', "level": 'low'},
        {"sig": 'C7 ?? ?? ?? ?? 00 00 83 46 40 CE 8B 46 40 3D ?? ?? ?? ?? 7F 0C',
         "site_offset": 10, "off": 'ce', "on": '00', "level": 'low'},
        {"sig": '16 25 01 00 C3 CC CC CC CC CC 53 55 8B 6C 24 0C 8B 45 24 56 57 83 F8',
         "site_offset": 10, "off": '53558b', "on": 'c20400', "level": 'low'},
    ],
    'plant_fragile': [
        {"sig": '51 8B 4D 04 E8 ?? ?? ?? ?? 83 46 40 FC 8B 4E 40 C7 ?? ?? ?? ?? 00 32',
         "site_offset": 10, "off": '4640fc', "on": '664000', "level": 'low'},
        {"sig": '?? ?? ?? ?? 8B ?? ?? ?? ?? 00 29 50 40 83 F9 19 7F 05 ?? ?? ?? ?? 00',
         "site_offset": 10, "off": '295040', "on": '294040', "level": 'low'},
        {"sig": '?? ?? ?? ?? 8B ?? ?? ?? ?? 00 29 4E 40 83 F8 19 7F 05 ?? ?? ?? ?? 00',
         "site_offset": 10, "off": '294e40', "on": '297640', "level": 'low'},
        {"sig": '1A 83 7D 24 2E BF ?? ?? ?? ?? 75 09 8B F5 E8 ?? ?? ?? ?? EB 06',
         "site_offset": 10, "off": '75', "on": 'eb', "level": 'low'},
    ],
    'zombie_invincible': [
        {"sig": 'C7 89 6C 24 18 E8 ?? ?? ?? ?? 2B 6C 24 20 89 44 24 10 8B C7 89 AF C8 00',
         "site_offset": 10, "off": '2b6c2420', "on": '90909090', "level": 'low'},
        {"sig": '3B CE 8B C1 7C 02 8B C6 2B F0 2B C8 89 74 24 0C 89 ?? ?? ?? ?? 00',
         "site_offset": 10, "off": '2bc8', "on": '2bc0', "level": 'low'},
        {"sig": '9C C3 8B 54 24 18 3B FA 8B C7 7C 02 8B C2 2B D0 29 ?? ?? ?? ?? 00 8B BE',
         "site_offset": 10, "off": '7c028bc2', "on": '33c033d2', "level": 'low'},
        {"sig": '?? ?? ?? ?? 00 ?? ?? ?? ?? 0F 8D ?? ?? ?? ?? 8B 47 24 83 F8 19',
         "site_offset": 10, "off": '8d', "on": '81', "level": 'low'},
        {"sig": '19 75 02 33 C0 8B 4D 1C 3B C1 7F 5D F7 D9 3B C1 7C 57 8B 4D 18',
         "site_offset": 10, "off": '7f', "on": 'eb', "level": 'low'},
        {"sig": '47 1C 83 7E 24 19 74 04 85 C0 75 39 53 E8 ?? ?? ?? ?? 84 C0 74',
         "site_offset": 10, "off": '75', "on": 'eb', "level": 'low'},
        {"sig": '85 F6 74 14 8B 46 24 83 F8 17 74 0A 83 F8 20 74 05 83 F8 19 75',
         "site_offset": 10, "off": '74', "on": 'eb', "level": 'low'},
        {"sig": 'D0 8B 46 24 C6 ?? ?? ?? ?? 00 01 C7 86 ?? ?? ?? ?? FF FF FF FF',
         "site_offset": 10, "off": '01', "on": '00', "level": 'low'},
        {"sig": '35 A1 0D 00 8B 43 28 83 F8 49 74 05 83 F8 4A 75 0C 83 F8 4A 74 07',
         "site_offset": 10, "off": '7405', "on": '9090', "level": 'low'},
        {"sig": '57 8B C6 E8 ?? ?? ?? ?? 84 C0 74 18 8B C6 E8 ?? ?? ?? ?? 8B CE',
         "site_offset": 10, "off": '74', "on": 'eb', "level": 'low'},
        {"sig": '00 00 8B 74 24 34 83 7E 24 19 74 69 8B 46 1C 2B 45 14 75 61 83',
         "site_offset": 10, "off": '74', "on": 'eb', "level": 'low'},
    ],
    'zombie_fragile': [
        {"sig": 'C7 89 6C 24 18 E8 ?? ?? ?? ?? 2B 6C 24 20 89 44 24 10 8B C7 89 AF C8 00',
         "site_offset": 10, "off": '2b6c2420', "on": '2bed9090', "level": 'low'},
        {"sig": '3B CE 8B C1 7C 02 8B C6 2B F0 2B C8 89 74 24 0C 89 ?? ?? ?? ?? 00',
         "site_offset": 10, "off": '2bc8', "on": '2bc9', "level": 'low'},
        {"sig": '9C C3 8B 54 24 18 3B FA 8B C7 7C 02 8B C2 2B D0 29 ?? ?? ?? ?? 00 8B BE',
         "site_offset": 10, "off": '7c028bc2', "on": '8bd08bc2', "level": 'low'},
        {"sig": '?? ?? ?? ?? 00 ?? ?? ?? ?? 0F 8D ?? ?? ?? ?? 8B 47 24 83 F8 19',
         "site_offset": 10, "off": '8d', "on": '80', "level": 'low'},
    ],
    'instant_reload': [
        {"sig": '83 F8 23 75 33 ?? ?? ?? ?? 0F 85 ?? ?? ?? ?? D9 05 ?? ?? ?? ??',
         "site_offset": 10, "off": '85', "on": '80', "level": 'low'},
        {"sig": '0F ?? ?? ?? ?? 00 39 47 54 0F 85 ?? ?? ?? ?? 8B 47 20 6A 31 50',
         "site_offset": 10, "off": '85', "on": '80', "level": 'low'},
        {"sig": '0F ?? ?? ?? ?? 00 ?? ?? ?? ?? 75 78 D9 05 ?? ?? ?? ?? 51 D9 1C',
         "site_offset": 10, "off": '75', "on": '70', "level": 'low'},
        {"sig": 'C3 83 F8 0D 75 3F ?? ?? ?? ?? 75 5F 6A 14 56 E8 ?? ?? ?? ?? D9',
         "site_offset": 10, "off": '75', "on": '70', "level": 'low'},
        {"sig": '0F ?? ?? ?? ?? 00 39 7B 54 0F 85 ?? ?? ?? ?? D9 05 ?? ?? ?? ??',
         "site_offset": 10, "off": '85', "on": '80', "level": 'low'},
    ],
    'mushrooms_awake': [
        {"sig": '2A 8B ?? ?? ?? ?? 00 83 F8 01 74 1F 83 F8 03 74 1A 83 F8 05 74',
         "site_offset": 10, "off": '74', "on": 'eb', "level": 'low'},
    ],
    'pause_spawning': [
        {"sig": 'D9 8B 91 ?? ?? ?? ?? 83 FA 2A 74 CE 83 FA 2B 74 C9 83 FA 32 74',
         "site_offset": 10, "off": '74', "on": 'eb', "level": 'low'},
    ],
    'freeze_zombies': [
        {"sig": '83 7E 28 28 74 09 D9 46 2C D8 64 24 08 EB 07 D9 46 2C D8 44 24',
         "site_offset": 10, "off": '64', "on": '54', "level": 'low'},
        {"sig": 'D8 64 24 08 EB 07 D9 46 2C D8 44 24 08 83 7E 24 07 D9 5E 2C 0F',
         "site_offset": 10, "off": '44', "on": '54', "level": 'low'},
    ],
    'always_butter': [
        {"sig": 'E8 ?? ?? ?? ?? 83 C4 04 85 C0 75 3F 8B 16 8B 82 ?? ?? ?? ?? 8B',
         "site_offset": 10, "off": '75', "on": '70', "level": 'low'},
    ],
    'no_crater': [
        {"sig": '00 7E 07 FF 4E 18 83 7E 18 00 75 05 E8 ?? ?? ?? ?? 8B C6 E8 ??',
         "site_offset": 10, "off": '75', "on": '70', "level": 'low'},
    ],
    'no_ice_trail': [
        {"sig": 'CC CC CC CC CC CC CC CC CC CC 51 D9 46 2C DC 1D ?? ?? ?? ?? DF',
         "site_offset": 10, "off": '51', "on": 'c3', "level": 'low'},
        {"sig": '?? ?? ?? ?? 1C ?? ?? ?? ?? 00 75 27 B8 ?? ?? ?? ?? BA FF FF FF',
         "site_offset": 10, "off": '75', "on": 'eb', "level": 'low'},
    ],
    'no_explode': [
        {"sig": '83 F8 0F 75 61 ?? ?? ?? ?? 0F 8F ?? ?? ?? ?? 80 ?? ?? ?? ?? 00',
         "site_offset": 10, "off": '8f', "on": '81', "level": 'low'},
        {"sig": '84 ?? ?? ?? ?? ?? ?? ?? ?? 0F 85 ?? ?? ?? ?? 8B 07 D9 EE 80 B8',
         "site_offset": 10, "off": '85', "on": '81', "level": 'low'},
    ],
    'street_zombies_hack': [
        {"sig": '00 80 7A 35 00 53 55 56 57 0F 85 ?? ?? ?? ?? 8B 0A C6 42 35 01',
         "site_offset": 10, "off": '85', "on": '80', "level": 'low'},
    ],
    'no_fog': [
        {"sig": '00 00 8B 31 8B 6C 24 10 03 F5 3B F2 7D 02 8B D6 89 11 83 C1 04 83',
         "site_offset": 10, "off": '3bf2', "on": '31d2', "level": 'low'},
    ],
    'see_vase': [
        {"sig": '40 89 47 4C 5E 59 C3 8B 47 4C 85 C0 7E 04 48 89 47 4C 5E 59 C3 CC CC CC',
         "site_offset": 10, "off": '85c07e04', "on": '66b83300', "level": 'low'},
    ],
    'background_running': [
        {"sig": 'F8 0F B6 91 ?? ?? ?? ?? 85 D2 74 40 8B 45 F8 0F B6 88 ?? ?? ?? ??',
         "site_offset": 10, "off": '7440', "on": 'eb00', "level": 'low'},
    ],
    'userdata_readonly': [
        {"sig": 'FC 0F B6 88 ?? ?? ?? ?? 85 C9 74 04 B0 01 EB 15 8B 4D 08 E8 ??',
         "site_offset": 10, "off": '74', "on": '70', "level": 'high'},
        {"sig": '7C 8D 44 24 3C 50 51 8B CD E8 42 2F 14 00 84 C0 75 42 83 7C 24 38 10 72',
         "site_offset": 10, "off": '422f1400', "on": '6a2f1400', "level": 'high'},
        {"sig": '88 46 14 C3 CC CC CC CC CC CC CC CC CC CC CC CC CC CC 6A FF 68 ?? ?? ?? ?? ?? ?? ?? ?? 00 00 50 64 ?? ?? ?? ?? 00 00',
         "site_offset": 18, "off": '6aff68', "on": 'c20c00', "level": 'high'},
    ],
    'show_limbo_page': [
        {"sig": '03 75 0A 8B 84 ?? ?? ?? ?? 00 88 58 64 47 83 FF 04 0F 8C 9F FD FF FF',
         "site_offset": 10, "off": '885864', "on": '385864', "level": 'low'},
    ],
}


def _build_function_anchors() -> Tuple[Anchor, ...]:
    return tuple(Anchor("functions." + name, "function", compile_sig(sig), (), ("in_text",), "==1")
                 for name, sig in _FUNCTION_SIGS.items())


def _build_patch_anchors() -> Tuple[Anchor, ...]:
    out = []
    for gname, sites in _PATCH_SIGS.items():
        for i, s in enumerate(sites):
            out.append(Anchor("patches.%s.%d" % (gname, i), "patch", compile_sig(s["sig"]),
                              (), ("in_text", "eq_off"), "==1",
                              {"site_offset": s["site_offset"], "off": s["off"], "on": s["on"],
                               "level": s.get("level", "low")}))
    return tuple(out)


FUNCTION_ANCHORS: Tuple[Anchor, ...] = _build_function_anchors()
PATCH_ANCHORS: Tuple[Anchor, ...] = _build_patch_anchors()

DEFAULT_ANCHORS: Tuple[Anchor, ...] = (
    SUN_CAP_ANCHORS + LAWN_GLOBAL_ANCHORS + NULL_CHECK_ANCHORS + (BOARD_SHIFT_ANCHOR,)
    + FUNCTION_ANCHORS + PATCH_ANCHORS
)
