"""L1 → L2 → L3 → L4 address resolution pipeline (DESIGN §3.2).

::

    from pvztools.addr.resolve import resolve, doctor_report
    r = resolve(session)            # or resolve("…/PlantsVsZombies.exe")
    r.profile.board.sun             # 0x5578
    r.capability                    # "FULL" | "READ_WRITE" | "READ_ONLY" | "UNSUPPORTED"
    print(doctor_report(r))

* **L1** ``profile_for_stamp(stamp)`` — the shipped version table.
* **L2** cache under ``%LOCALAPPDATA%/pvztools/cache`` keyed by
  ``stamp + size_of_image + sha256(game PE)``; falls back to
  ``stamp + size_of_image`` (marked *weak*) when the exe cannot be read.
* **L3** AOB scan (:mod:`pvztools.addr.scan`) → synthetic :class:`Profile`
  built on the closest official template (retail 1051 / goty 1096) so the
  upper layers see one shape regardless of source.
* **L4** validation: globals inside the data section, functions inside the
  code section, Board offsets ≤ Board size, and — live and in a level —
  ``sun ∈ [0, 9990]`` plus sane ``game_ui``.

Capability grading (DESIGN §3.2 / parity §0.4):

``FULL``        lawn/board/sun resolved + validated, calling-convention family
                known, and every :data:`anchors.CORE_FUNCTIONS` resolved
``READ_WRITE``  lawn/board/sun resolved + validated (patches / remote calls may
                be partially unavailable)
``READ_ONLY``   lawn/board/sun resolved but a validation failed, the Board
                shift could not be cross-checked (sun anchor and histogram do
                not both agree: ``board.shift`` confidence < 0.9), or the
                LawnApp offset family is ambiguous
``UNSUPPORTED`` any of lawn_app / board / sun missing (the synthetic profile
                then falls back to the template so ``doctor`` can still report)

A profile's own ``capability`` field caps the grade; the legacy value
``PARTIAL`` is read as ``READ_WRITE``.
"""
from __future__ import annotations

import copy
import json
import os
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from . import anchors as A
from . import profile as P
from .scan import ScanResult, read_live_text, scan_image, _data_range_of

__all__ = ["Resolution", "resolve", "doctor_report", "export_diagnostics",
           "cache_dir", "cache_key", "CAPABILITIES", "FULL", "READ_WRITE",
           "READ_ONLY", "UNSUPPORTED"]

FULL, READ_WRITE, READ_ONLY, UNSUPPORTED = "FULL", "READ_WRITE", "READ_ONLY", "UNSUPPORTED"
CAPABILITIES = (UNSUPPORTED, READ_ONLY, READ_WRITE, FULL)
CACHE_SCHEMA = 1
#: Legacy / alias spellings of profile ``capability`` values.
CAPABILITY_ALIASES = {"PARTIAL": READ_WRITE}
#: ``board.shift`` confidence below this means sun anchor and histogram did
#: not both confirm the shift (scan.py: 0.95 agree / 0.6 / 0.5).
SHIFT_CROSS_CHECKED = 0.9
_GAME_UI_OK = {0, 1, 2, 3, 4, 5, 6, 7}


def _cap_rank(c: str) -> int:
    return CAPABILITIES.index(c)


def _min_cap(a: str, b: str) -> str:
    return a if _cap_rank(a) <= _cap_rank(b) else b


@dataclass
class Resolution:
    profile: P.Profile
    source: str                      # "table" | "cache" | "scan" | "mixed"
    capability: str
    address_book: Dict[str, int]
    report: List[str] = field(default_factory=list)
    sources: Dict[str, str] = field(default_factory=dict)        # symbol -> source
    confidence: Dict[str, float] = field(default_factory=dict)   # symbol -> 0..1
    missing: List[str] = field(default_factory=list)
    identity: Dict[str, Any] = field(default_factory=dict)       # stamp/size/sha/weak
    scan: Optional[ScanResult] = None
    validation: List[Tuple[str, bool, str]] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.capability != UNSUPPORTED


# ---------------------------------------------------------------------------
# identity / cache
# ---------------------------------------------------------------------------
def cache_dir() -> Path:
    base = os.environ.get("PVZTOOLS_CACHE_DIR")
    if base:
        return Path(base)
    root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(root) / "pvztools" / "cache"


def cache_key(stamp: int, size_of_image: int, sha256: Optional[str]) -> Tuple[str, bool]:
    """``(filename, weak)``; weak when no sha256 is available."""
    if sha256:
        return "%08x_%x_%s.json" % (stamp, size_of_image, sha256[:16]), False
    return "%08x_%x.json" % (stamp, size_of_image), True


def _load_cache(key: str) -> Optional[Dict[str, Any]]:
    path = cache_dir() / key
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if d.get("schema") != CACHE_SCHEMA or "scan" not in d:
        return None
    return d


def _store_cache(key: str, identity: Dict[str, Any], scan: ScanResult) -> Optional[Path]:
    try:
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / key
        payload = {"schema": CACHE_SCHEMA, "identity": identity, "scan": scan.as_dict(),
                   "written": time.strftime("%Y-%m-%dT%H:%M:%S")}
        path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
        return path
    except OSError:
        return None


def _scan_from_cache(d: Dict[str, Any]) -> ScanResult:
    s = d["scan"]
    res = ScanResult(found={k: int(v) for k, v in s["found"].items()},
                     confidence={k: float(v) for k, v in s["confidence"].items()},
                     evidence={k: list(v) for k, v in s.get("evidence", {}).items()},
                     missing=list(s.get("missing", [])), family=s.get("family"),
                     family_candidates=list(s.get("family_candidates", [])),
                     text_range=tuple(s.get("text_range", (0, 0))),
                     data_range=tuple(s.get("data_range", (0, 0))),
                     elapsed=float(s.get("elapsed", 0.0)))
    return res


# ---------------------------------------------------------------------------
# target abstraction
# ---------------------------------------------------------------------------
class _Target:
    """Uniform view of an exe path or a live Session."""

    def __init__(self, obj: Any):
        from .._win.pe import extract_game_pe, sha256_hex
        self.session = None
        self.path: Optional[Path] = None
        self.text: Optional[bytes] = None
        self.text_va = 0
        self.pe = None
        self.sha256: Optional[str] = None
        self.live = False
        if isinstance(obj, (str, os.PathLike)):
            self.path = Path(obj)
            data = self.path.read_bytes()
            _off, pe, img = extract_game_pe(data)
            self.pe = pe
            self.sha256 = sha256_hex(img)
            t = pe.text
            self.text = img[t.raw_ptr:t.raw_ptr + t.raw_size]
            self.text_va = t.va
            self._img = img
        else:
            self.session = obj
            self.live = True
            p = getattr(obj, "path", None)
            if p:
                try:
                    data = Path(p).read_bytes()
                    _off, _pe, img = extract_game_pe(data)
                    self.sha256 = sha256_hex(img)
                    self.path = Path(p)
                except Exception:
                    self.sha256 = None

    @property
    def stamp(self) -> int:
        return self.session.stamp if self.live else self.pe.stamp

    @property
    def size_of_image(self) -> int:
        return self.session.size_of_image if self.live else self.pe.size_of_image

    @property
    def image_base(self) -> int:
        return self.session.image_base if self.live else self.pe.image_base

    def ensure_text(self) -> None:
        if self.text is None and self.live:
            self.text, self.text_va, self.pe = read_live_text(self.session)
        elif self.pe is None and self.live:
            from .._win.pe import read_live_pe
            self.pe = read_live_pe(self.session.read, self.session.image_base)

    def ensure_pe(self) -> None:
        if self.pe is None and self.live:
            from .._win.pe import read_live_pe
            self.pe = read_live_pe(self.session.read, self.session.image_base)

    def do_scan(self) -> ScanResult:
        self.ensure_text()
        return scan_image(self.text, self.text_va, _data_range_of(self.pe))

    def read_u32(self, addr: int) -> Optional[int]:
        if not self.live:
            return None
        try:
            return self.session.read_u32(addr)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# L3: synthesize a Profile from a ScanResult
# ---------------------------------------------------------------------------
def _template_for(scan: ScanResult) -> Tuple[Dict[str, Any], str]:
    shift = scan.found.get("board.shift")
    fam = scan.family
    if fam in ("goty", "goty_zh") and shift == 0x18:
        tid = "goty_1_2_0_1096_en" if fam == "goty" else "goty_1_1_0_1056_zh_2012_07"
        conv = "goty" if fam == "goty" else "goty_zh2012"
    elif fam == "retail" and shift == 0:
        tid, conv = "1_0_0_1051_en", "retail"
    else:
        tid, conv = ("goty_1_2_0_1096_en" if fam and fam.startswith("goty") else "1_0_0_1051_en"), "unknown"
    tp = P.profile_for_id(tid)
    if tp is None:  # pragma: no cover - shipped profiles always present
        raise P.ProfileError("template profile %s missing" % tid)
    return copy.deepcopy(tp.raw), conv


#: Board fields at or above this 1051 offset move with the GOTY shift
#: (DESIGN §3.5); the few below it (vtable / early members) do not.
BOARD_SHIFT_FLOOR = 0x90


def _template_board_shift(raw: Dict[str, Any]) -> int:
    """Shift the template itself carries (sun - 0x5560)."""
    sun = raw.get("board", {}).get("sun")
    return (int(str(sun), 16) - A.SUN_1051) if sun is not None else 0


def _fallback_profile(scan: ScanResult, identity: Dict[str, Any]) -> Tuple[P.Profile, Dict[str, str]]:
    """UNSUPPORTED builds: a template-shaped profile with every address the
    scan did not confirm disabled, so doctor / diagnostics still have a
    Profile to describe.  Never used for reads or writes (capability
    UNSUPPORTED is enforced by the modifier and the facade)."""
    raw, _conv = _template_for(scan)
    raw["id"] = "scan_%08x" % identity["stamp"]
    raw["name"] = "unknown build (stamp 0x%08x, scan failed)" % identity["stamp"]
    raw["stamp"] = "0x%08x" % identity["stamp"]
    raw["verified"] = False
    raw["verified_fields"] = []
    raw["warnings"] = list(raw.get("warnings", [])) + ["scanned", "scan incomplete: UNSUPPORTED"]
    raw["capability"] = UNSUPPORTED
    raw["source"] = "aob-scan (failed; template placeholder)"
    for fn in raw.get("functions", {}).values():
        fn["rva"] = None
        fn["enabled"] = False
    for grp in raw.get("patches", {}).values():
        grp["sites"] = []
        grp["enabled"] = False
    prof = P.profile_from_dict(raw)
    return prof, {k: "template" for k in prof.as_address_book()}


def _profile_from_scan(scan: ScanResult, identity: Dict[str, Any], image_base: int) -> Tuple[P.Profile, Dict[str, str]]:
    raw, conv = _template_for(scan)
    srcs: Dict[str, str] = {}
    template_id = raw.get("id", "")
    raw["id"] = "scan_%08x" % identity["stamp"]
    raw["name"] = "unknown build (stamp 0x%08x, scanned)" % identity["stamp"]
    raw["stamp"] = "0x%08x" % identity["stamp"]
    raw["verified"] = False
    raw["verified_fields"] = []
    raw["warnings"] = list(raw.get("warnings", [])) + ["scanned"]
    raw["conventions"] = conv if conv != "unknown" else raw.get("conventions", raw["family"])
    if conv == "unknown":
        raw["warnings"].append("unknown calling-convention family; remote calls disabled")
    raw["source"] = "aob-scan (template %s)" % template_id

    fs: Dict[str, str] = {}
    # globals
    g = scan.found.get("globals.lawn_app")
    raw["globals"] = {"lawn_app": {"rva": "0x%x" % (g - image_base), "source": "scan"}} if g else {"lawn_app": {"rva": None}}
    if g:
        srcs["globals.lawn_app"] = "scan"
    # lawn_app fields
    for k in list(raw["lawn_app"].keys()):
        v = scan.found.get("lawn_app." + k)
        if v is not None:
            raw["lawn_app"][k] = "0x%x" % v
            srcs["lawn_app." + k] = "scan"
            fs["lawn_app." + k] = "scan"
        else:
            srcs["lawn_app." + k] = "template"
    # display-mode fields discovered by field_off signatures (not in templates);
    # the SexyAppBase guards sit at fixed +3/+4 after mIsWindowed.
    iw = scan.found.get("lawn_app.is_windowed")
    if iw is not None:
        raw["lawn_app"]["is_windowed"] = "0x%x" % iw
        raw["lawn_app"]["force_fullscreen"] = "0x%x" % (iw + 3)
        raw["lawn_app"]["force_windowed"] = "0x%x" % (iw + 4)
        for k in ("is_windowed", "force_fullscreen", "force_windowed"):
            srcs["lawn_app." + k] = "scan"
            fs["lawn_app." + k] = "scan"
    aae = scan.found.get("lawn_app.allow_alt_enter")
    if aae is not None:
        raw["lawn_app"]["allow_alt_enter"] = "0x%x" % aae
        srcs["lawn_app.allow_alt_enter"] = "scan"
        fs["lawn_app.allow_alt_enter"] = "scan"
    # board fields: scanned high-range ones; the rest from the template,
    # moved by the detected shift when it differs from the template's own
    # (DESIGN §3.5: GOTY Board fields >= 0x90 move with the shift)
    template_shift = _template_board_shift(raw)
    shift = scan.found.get("board.shift")
    delta = None if shift is None else shift - template_shift
    for k in list(raw["board"].keys()):
        v = scan.found.get("board." + k)
        if v is not None:
            raw["board"][k] = "0x%x" % v
            srcs["board." + k] = "scan"
            continue
        tv = raw["board"][k]
        if delta and tv is not None:
            off = int(str(tv), 16)
            if off - template_shift >= BOARD_SHIFT_FLOOR:
                raw["board"][k] = "0x%x" % (off + delta)
                srcs["board." + k] = "template+shift"
                continue
        srcs["board." + k] = "template"
    if delta:
        raw["warnings"].append("board shift 0x%x differs from template (0x%x): low-range Board "
                               "fields moved by %+#x, unverified" % (shift, template_shift, delta))
    # functions: only scanned entries are trusted
    new_fns: Dict[str, Any] = {}
    for k, fn in raw["functions"].items():
        va = scan.found.get("functions." + k)
        if va is not None:
            d = dict(fn)
            d["rva"] = "0x%x" % (va - image_base)
            d["source"] = "scan"
            d["enabled"] = conv != "unknown"
            new_fns[k] = d
            srcs["functions." + k] = "scan"
        else:
            d = dict(fn)
            d["rva"] = None
            d["enabled"] = False
            new_fns[k] = d
    for name in ("cob_fire", "kill_zombie", "shovel_plant", "plant_card", "mouse_down", "choose_card"):
        va = scan.found.get("functions." + name)
        if va is not None and name not in new_fns:
            new_fns[name] = {"rva": "0x%x" % (va - image_base), "conv": "%s.%s" % (raw["conventions"], name),
                             "source": "scan", "enabled": conv != "unknown"}
            srcs["functions." + name] = "scan"
    raw["functions"] = new_fns
    # patches: keep a site only if the scan located it with matching off bytes
    new_patches: Dict[str, Any] = {}
    for gname, grp in raw["patches"].items():
        sites = []
        for i, s in enumerate(grp.get("sites", [])):
            va = scan.found.get("patches.%s.%d" % (gname, i))
            if va is None:
                continue
            d = dict(s)
            d["rva"] = "0x%x" % (va - image_base)
            d["source"] = "scan"
            sites.append(d)
            srcs["patches.%s.%d" % (gname, len(sites) - 1)] = "scan"
        d2 = dict(grp)
        d2["sites"] = sites
        d2["enabled"] = bool(sites) and len(sites) == len(grp.get("sites", []))
        new_patches[gname] = d2
    raw["patches"] = new_patches
    raw["field_sources"] = fs
    prof = P.profile_from_dict(raw)
    return prof, srcs


# ---------------------------------------------------------------------------
# L4: validation
# ---------------------------------------------------------------------------
def _validate(prof: P.Profile, target: _Target, res: Resolution) -> str:
    cap = FULL
    checks = res.validation
    target.ensure_pe()
    pe = target.pe
    text = pe.text
    data_lo, data_hi = _data_range_of(pe)
    text_lo, text_hi = (text.va, text.end) if text is not None else (0, 0)

    def check(name: str, ok: bool, detail: str, downgrade: str) -> None:
        nonlocal cap
        checks.append((name, ok, detail))
        if not ok:
            cap = _min_cap(cap, downgrade)

    lawn = prof.globals.get("lawn_app")
    check("globals.lawn_app in data section", lawn is not None and data_lo <= lawn < data_hi,
          "0x%X in [0x%X,0x%X)" % (lawn or 0, data_lo, data_hi), UNSUPPORTED)
    for name in ("board", "game_mode", "game_ui"):
        v = prof.lawn_app.get(name)
        check("lawn_app.%s present" % name, v is not None, hex(v) if v is not None else "missing", UNSUPPORTED)
    sun = prof.board.get("sun")
    check("board.sun present", sun is not None, hex(sun) if sun is not None else "missing", UNSUPPORTED)
    shift = (sun - A.SUN_1051) if sun is not None else 0
    board_size = A.BOARD_SIZE_1051 + max(0, shift)
    bad = [k for k, v in prof.board.items() if v is not None and v > board_size]
    check("board offsets <= board size", not bad, "size 0x%X bad=%s" % (board_size, bad), READ_ONLY)
    for fname, fn in prof.functions.items():
        va = fn.va(prof.image_base)
        if va is None:
            continue
        check("functions.%s in text" % fname, text_lo <= va < text_hi, hex(va), READ_WRITE)
    for pname, pg in prof.patches.items():
        for i, site in enumerate(pg.sites):
            va = site.va(prof.image_base)
            check("patches.%s.%d in text" % (pname, i), text_lo <= va < text_hi, hex(va), READ_WRITE)

    # dynamic (live only)
    if target.live and lawn is not None and prof.lawn_app.get("board") is not None:
        app = target.read_u32(lawn)
        check("[lawn_app] is a pointer", app is not None and 0x10000 <= app < 0x8000_0000,
              "0x%X" % (app or 0), READ_ONLY)
        if app:
            ui = target.read_u32(app + prof.lawn_app.game_ui)
            check("game_ui sane", ui is not None and ui in _GAME_UI_OK, str(ui), READ_ONLY)
            fd = prof.lawn_app.get("frame_duration")
            if fd is not None:
                v = target.read_u32(app + fd)
                check("frame_duration in [5,100]", v is not None and 5 <= v <= 100, str(v), READ_ONLY)
            board = target.read_u32(app + prof.lawn_app.board)
            res.identity["live"] = {"lawn_app": app, "game_ui": ui, "board": board}
            if board and ui in (2, 3) and sun is not None:
                s = target.read_u32(board + sun)
                check("sun in [0,9990] (in level)", s is not None and 0 <= s <= A.SUN_CAP, str(s), READ_ONLY)
                res.identity["live"]["sun"] = s
            else:
                checks.append(("sun range", True, "skipped (no Board / not in level)"))
    return cap


def _grade(prof: P.Profile, cap: str, res: Resolution) -> str:
    if cap == UNSUPPORTED:
        return cap
    if "unknown calling-convention family; remote calls disabled" in prof.warnings:
        cap = _min_cap(cap, READ_WRITE)
        res.report.append("calling-convention family unknown → remote calls disabled")
    missing_core = [f for f in A.CORE_FUNCTIONS
                    if f not in prof.functions or prof.functions[f].rva is None]
    if missing_core:
        cap = _min_cap(cap, READ_WRITE)
        res.report.append("core functions missing: %s → no FULL" % ", ".join(missing_core))
    pcap = CAPABILITY_ALIASES.get(prof.capability, prof.capability)
    if pcap in CAPABILITIES:
        if pcap != FULL:
            res.report.append("profile declares capability %s" % prof.capability)
        cap = _min_cap(cap, pcap)
    elif prof.capability:
        res.report.append("profile capability %r unknown → treated as READ_ONLY" % prof.capability)
        cap = _min_cap(cap, READ_ONLY)
    if res.scan is not None:
        if "board.shift" in res.scan.missing:
            cap = _min_cap(cap, READ_ONLY)
            res.report.append("board shift: sun anchor and histogram disagree → READ_ONLY")
        elif res.scan.confidence.get("board.shift", 1.0) < SHIFT_CROSS_CHECKED:
            cap = _min_cap(cap, READ_ONLY)
            res.report.append("board shift not cross-checked (confidence %.2f) → READ_ONLY"
                              % res.scan.confidence.get("board.shift", 0.0))
        if len(res.scan.family_candidates) > 1:
            cap = _min_cap(cap, READ_ONLY)
            res.report.append("LawnApp offset family ambiguous (%s) → READ_ONLY"
                              % "/".join(res.scan.family_candidates))
    return cap


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------
def resolve(target: Any, *, use_table: bool = True, use_cache: bool = True,
            force_scan: bool = False, augment: bool = False) -> Resolution:
    """Run the pipeline on a Session or an exe path.

    ``augment=True`` additionally scans when the table profile lacks any
    :data:`anchors.CORE_FUNCTIONS` entry and merges found symbols
    (``source="mixed"``).
    """
    t0 = time.perf_counter()
    tg = _Target(target)
    stamp, size = tg.stamp, tg.size_of_image
    key, weak = cache_key(stamp, size, tg.sha256)
    identity = {"stamp": stamp, "size_of_image": size, "sha256": tg.sha256, "weak_key": weak,
                "image_base": tg.image_base, "path": str(tg.path) if tg.path else None,
                "live": tg.live}
    report: List[str] = []
    prof: Optional[P.Profile] = None
    source = ""
    sources: Dict[str, str] = {}
    confidence: Dict[str, float] = {}
    scan: Optional[ScanResult] = None

    # L1
    if use_table and not force_scan:
        prof = P.profile_for_stamp(stamp)
        if prof is not None:
            source = "table"
            report.append("L1 table: stamp 0x%08x → %s (%s)" % (stamp, prof.id, prof.name))
            for k in prof.as_address_book():
                sources[k] = "table"
                confidence[k] = 1.0 if prof.verified else 0.9
            for w in prof.warnings:
                report.append("profile warning: %s" % w)
        else:
            report.append("L1 table: stamp 0x%08x unknown" % stamp)

    # L2
    if prof is None and use_cache and not force_scan:
        cached = _load_cache(key)
        if cached is not None:
            scan = _scan_from_cache(cached)
            source = "cache"
            report.append("L2 cache hit: %s%s" % (key, " (weak key)" if weak else ""))

    # L3
    if prof is None and scan is None:
        scan = tg.do_scan()
        source = "scan"
        report.append("L3 scan: %d symbols, %d missing, %.2fs, family=%s"
                      % (len(scan.found), len(scan.missing), scan.elapsed, scan.family))
        if use_cache:
            p = _store_cache(key, identity, scan)
            if p is not None:
                report.append("L2 cache written: %s" % p)

    unsupported = False
    if prof is None:
        assert scan is not None
        try:
            prof, sources = _profile_from_scan(scan, identity, tg.image_base)
        except P.ProfileError as exc:
            # the scan did not even find LawnApp: grade UNSUPPORTED instead of
            # crashing, and keep a placeholder profile for the report
            unsupported = True
            report.append("L3 scan incomplete (%s) → UNSUPPORTED" % exc)
            prof, sources = _fallback_profile(scan, identity)
            for k in prof.as_address_book():
                confidence[k] = 0.0   # placeholder template values, nothing confirmed
        for k, v in scan.confidence.items():
            confidence[k] = v
        for k in prof.as_address_book():
            if sources.get(k) == "template+shift":
                confidence.setdefault(k, 0.4)
            else:
                confidence.setdefault(k, 0.5 if sources.get(k, "template") == "template" else 0.9)
            sources.setdefault(k, "template")
    elif augment:
        need = [f for f in A.CORE_FUNCTIONS if f not in prof.functions or prof.functions[f].rva is None]
        if need:
            scan = tg.do_scan()
            raw = copy.deepcopy(prof.raw)
            added = []
            for f in need:
                va = scan.found.get("functions." + f)
                if va is not None:
                    raw["functions"][f] = {"rva": "0x%x" % (va - prof.image_base),
                                           "conv": "%s.%s" % (prof.conventions, f), "source": "scan"}
                    sources["functions." + f] = "scan"
                    confidence["functions." + f] = scan.confidence["functions." + f]
                    added.append(f)
            if added:
                prof = P.profile_from_dict(raw, prof.path)
                source = "mixed"
                report.append("L3 augment: added %s from scan" % ", ".join(added))

    res = Resolution(profile=prof, source=source, capability=UNSUPPORTED,
                     address_book=prof.as_address_book(), report=report, sources=sources,
                     confidence=confidence, identity=identity, scan=scan)
    res.missing = list(scan.missing) if scan is not None else []
    # L4
    try:
        cap = _validate(prof, tg, res)
    except Exception as e:  # pragma: no cover - defensive
        report.append("L4 validation error: %r" % (e,))
        cap = READ_ONLY
    if unsupported:
        cap = UNSUPPORTED
    res.capability = _grade(prof, cap, res)
    failed = [c for c in res.validation if not c[1]]
    report.append("L4 validation: %d checks, %d failed → %s" % (len(res.validation), len(failed), res.capability))
    for name, _ok, detail in failed:
        report.append("  FAILED %s: %s" % (name, detail))
    res.elapsed = time.perf_counter() - t0
    return res


def doctor_report(res: Resolution) -> str:
    """Human-readable listing of every symbol with its source and confidence."""
    lines = ["pvztools doctor — address resolution"]
    idt = res.identity
    lines.append("stamp 0x%08x  size_of_image 0x%x  sha256 %s%s" % (
        idt.get("stamp", 0), idt.get("size_of_image", 0),
        (idt.get("sha256") or "n/a")[:16], " (weak key)" if idt.get("weak_key") else ""))
    lines.append("profile %s  family %s  conventions %s  source %s  capability %s"
                 % (res.profile.id, res.profile.family, res.profile.conventions, res.source, res.capability))
    for r in res.report:
        lines.append("  " + r)
    lines.append("")
    lines.append("%-44s %-12s %-9s %s" % ("symbol", "value", "source", "confidence"))
    for k in sorted(res.address_book):
        v = res.address_book[k]
        lines.append("%-44s 0x%-10X %-9s %.2f" % (k, v, res.sources.get(k, "-"), res.confidence.get(k, 0.0)))
    if res.missing:
        lines.append("")
        lines.append("missing (%d):" % len(res.missing))
        for m in res.missing:
            ev = res.scan.evidence.get(m, []) if res.scan else []
            lines.append("  %s  %s" % (m, ev[0] if ev else ""))
    lines.append("")
    lines.append("validation:")
    for name, ok, detail in res.validation:
        lines.append("  [%s] %s: %s" % ("ok" if ok else "FAIL", name, detail))
    return "\n".join(lines)


def export_diagnostics(res: Resolution, path: Union[str, os.PathLike]) -> Path:
    """Write a JSON diagnostics bundle (no game binary bytes): identity, PE
    section table (if known), every symbol with source/confidence, scan
    evidence and hit counts, validation results, platform info."""
    out = Path(path)
    d: Dict[str, Any] = {
        "schema": 1,
        "tool": "pvztools",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "identity": {k: (("0x%x" % v) if isinstance(v, int) and k in ("stamp", "size_of_image", "image_base") else v)
                     for k, v in res.identity.items() if k != "live"},
        "live": res.identity.get("live"),
        "profile": {"id": res.profile.id, "name": res.profile.name, "family": res.profile.family,
                    "conventions": res.profile.conventions, "warnings": res.profile.warnings},
        "source": res.source,
        "capability": res.capability,
        "report": list(res.report),
        "symbols": {k: {"value": "0x%x" % v, "source": res.sources.get(k, "-"),
                        "confidence": round(res.confidence.get(k, 0.0), 3)}
                    for k, v in sorted(res.address_book.items())},
        "missing": list(res.missing),
        "validation": [{"check": n, "ok": ok, "detail": det} for n, ok, det in res.validation],
        "scan": res.scan.as_dict() if res.scan is not None else None,
        "anchors": [{"name": a.name, "kind": a.kind, "expect": a.expect} for a in A.DEFAULT_ANCHORS],
    }
    if res.scan is not None:
        d["scan"]["found"] = {k: "0x%x" % v for k, v in res.scan.found.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
