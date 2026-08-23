"""``pvztools`` command line (argparse, stdlib only).

Commands::

    pvztools track parse <file|-|inline> [--format F] [--dialect D] [--json]
    pvztools track lint <file|-|inline> [--scene XX]
    pvztools track expand <file|-|inline> --scene PE --cobs 25,25,... [--ice 11,..] [--fodder-rows 1,6]
    pvztools track to-seml <file|-|inline>
    pvztools track to-avz <file|-|inline>
    pvztools track run <file|-|inline> [--input | --remote] [--dry-run] [--report out.json] [--cobs 25,..] [--ice 11,..] [--fodder-rows 1,6] [--scene PE]
    pvztools reactive --policy p6 --cobs 29,59 [--waves 20] [--input | --remote] [--report out.json]
    pvztools bench-clock [--seconds 10] [--step 50] [--json]
    (every track command takes ``--format auto|shorthand|pvzt|seml`` and ``--dialect``)
    pvztools lineup decode <code>
    pvztools lineup encode <lineup-string>
    pvztools lineup show <name-or-index> [--db PATH]
    pvztools doctor [--pid N] [--json PATH]
    pvztools scan <exe> [--json]
    pvztools read [sun|board|plants|zombies|seeds|spawn|all] [--pid N]
    pvztools spawn show [--pid N]
    pvztools mod sun <n> | speed <frame_ms> | no-cooldown on|off | sun-limit on|off
                 | auto-collect on|off | background on|off | free-planting on|off
                 | kill-zombies      (flags: --write / --patch / --yes / --pid)
    pvztools plant <name> <row> <col> [--imitater]      --remote --yes
    pvztools shovel <row> <col>                         --remote --yes
    pvztools lineup place <name|index|code|string> [--clear/--no-clear]   --remote --yes
    pvztools spawn set <chars|ids>                      --write --yes
    pvztools spawn reroll [types]                       --remote --yes
    pvztools spawn stats N [types]                      --remote --yes
    pvztools clear plants|graves|ladders|rakes|items    --remote --yes
    pvztools cobs                                       (list cob cannons: ready / countdown)
    pvztools fire <cobRow> <cobCol> <row> <col> [--force]   --remote --yes
    pvztools play --lineup 3C八炮 --track "P4: ..." [--seeds ..] [--cob-col 8] [--no-collect]
                  [--no-foreground] [--report out.json] [--md out.md]   --remote --yes
    (``--remote`` implies ``--write`` and ``--patch``; remote commands inject
    code into the game, only on the seed chooser / in a level)

A track argument that is not an existing file (and not ``-``) is treated as
inline text, e.g. ``pvztools track parse "P6: PP|PP|PPSS (6|6|6)"``.
Track text is auto-detected (``--format auto``): Seml-only constructs
(``SET``, Seml setting keys, wave-line ice times, ``C_NUM``-style symbols,
``wN~M LEN`` loops without ``len``) select ``bridges.from_seml``; a ``wN``
block header or a ``key: value`` header line from docs/track-format.md §4.3
selects the ``.pvzt`` parser; anything else is shorthand (§3).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pvztools import __version__
from pvztools.i18n import _
from pvztools.schema.enums import PlantType, Scene
from pvztools.schema.track import (
    Diagnostic,
    ExpandContext,
    Track,
    dump_pvzt,
    dump_shorthand,
    expand,
    lint,
    parse_pvzt,
    parse_shorthand,
    to_json,
)
from pvztools.schema.track.pvzt import HEADER_KEYS
from pvztools.schema.track.symbols import DEFAULT_DIALECT, DIALECTS

FORMATS = ("auto", "shorthand", "pvzt", "seml")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_LINT = 2  # lint/validate reported at least one error-level diagnostic

from pvztools.data import lineups_path as _lineups_path

DEFAULT_DB = str(_lineups_path())  # bundled package data (pvztools/data/lineups.json)

# 2-character plant abbreviations for the ASCII grid (``lineup decode``).
PLANT_ABBR: Dict[int, str] = {
    PlantType.PEASHOOTER: "Pe",
    PlantType.SUNFLOWER: "Sf",
    PlantType.CHERRY_BOMB: "Ch",
    PlantType.WALL_NUT: "Wn",
    PlantType.POTATO_MINE: "Pm",
    PlantType.SNOW_PEA: "Sp",
    PlantType.CHOMPER: "Cp",
    PlantType.REPEATER: "Rp",
    PlantType.PUFF_SHROOM: "Pf",
    PlantType.SUN_SHROOM: "Ss",
    PlantType.FUME_SHROOM: "Fm",
    PlantType.GRAVE_BUSTER: "Gb",
    PlantType.HYPNO_SHROOM: "Hy",
    PlantType.SCAREDY_SHROOM: "Sc",
    PlantType.ICE_SHROOM: "Ic",
    PlantType.DOOM_SHROOM: "Dm",
    PlantType.LILY_PAD: "Lp",
    PlantType.SQUASH: "Sq",
    PlantType.THREEPEATER: "3p",
    PlantType.TANGLE_KELP: "Tk",
    PlantType.JALAPENO: "Ja",
    PlantType.SPIKEWEED: "Sw",
    PlantType.TORCHWOOD: "Tw",
    PlantType.TALL_NUT: "Tn",
    PlantType.SEA_SHROOM: "Se",
    PlantType.PLANTERN: "Pl",
    PlantType.CACTUS: "Ca",
    PlantType.BLOVER: "Bl",
    PlantType.SPLIT_PEA: "Sl",
    PlantType.STARFRUIT: "St",
    PlantType.PUMPKIN: "Pk",
    PlantType.MAGNET_SHROOM: "Mg",
    PlantType.CABBAGE_PULT: "Cb",
    PlantType.FLOWER_POT: "Fp",
    PlantType.KERNEL_PULT: "Kp",
    PlantType.COFFEE_BEAN: "Cf",
    PlantType.GARLIC: "Ga",
    PlantType.UMBRELLA_LEAF: "Ul",
    PlantType.MARIGOLD: "Mr",
    PlantType.MELON_PULT: "Mp",
    PlantType.GATLING_PEA: "Gp",
    PlantType.TWIN_SUNFLOWER: "Ts",
    PlantType.GLOOM_SHROOM: "Gl",
    PlantType.CATTAIL: "Ct",
    PlantType.WINTER_MELON: "Wm",
    PlantType.GOLD_MAGNET: "Gm",
    PlantType.SPIKEROCK: "Sr",
    PlantType.COB_CANNON: "CC",
    PlantType.IMITATER: "Im",
}
COB_RIGHT = "cc"  # right half of a cob cannon
EMPTY = ".."
TOMB = "##"
LADDER = "=="

# selector characters of a ``.pvzt`` / Seml wave header after the ``w``
_WAVE_SELECTOR_RE = re.compile(r"\s*([\d~%,\s]*)")
_PVZT_HEADER_RE = re.compile(r"^\s*(%s)\s*[:：]" % "|".join(HEADER_KEYS), re.IGNORECASE)
_SEML_SETTING_RE = re.compile(
    r"^\s*(repeat|require|ban|huge|activate|dance|natural|cobDelay|types|targetPos|avzTime)\s*:", re.IGNORECASE
)
_SEML_SMART_RE = re.compile(r"^\s*[A-Za-z']+_(NUM|POS)\b")
# Seml wave line: ``w 601``, ``w1~8 601``, ``w1 0 1200``, ``w 0 1200~1500`` (no ``len`` keyword)
_SEML_WAVE_RE = re.compile(r"^\s*w(?:\d+(?:~\d+)?)?\s+(?:\d+\s+)*\d+(?:~\d+)?\s*$")


# --------------------------------------------------------------------------- helpers
def _configure_stdout() -> None:
    """Force UTF-8 output (Windows consoles default to a legacy code page)."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if isinstance(stream, io.TextIOWrapper) and (stream.encoding or "").lower().replace("-", "") != "utf8":
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):  # pragma: no cover - exotic streams
                pass


def read_track_arg(arg: str) -> str:
    """Return track text from ``-`` (stdin), an existing file, or the argument itself."""
    if arg == "-":
        return sys.stdin.read()
    if os.path.isfile(arg):
        with open(arg, encoding="utf-8") as f:
            return f.read()
    return arg


def _is_wave_header(line: str) -> bool:
    """``w1``, ``w1~8 len 601``, ``w1%4, 2~9%2``, Seml ``w 601`` / ``w1~3 0 1200~1500``
    — but not the shorthand per-wave prefix ``w1:PSD/PDD`` (§3.1b), whose
    selector is followed by ``:``."""
    if line[:1].lower() != "w":
        return False
    m = _WAVE_SELECTOR_RE.match(line, 1)
    after = line[m.end():]
    return after == "" or after.lower().startswith("len")


def looks_like_seml(text: str) -> bool:
    """True when the text uses Seml-only constructs (``SET``, Seml setting
    keys, smart symbols, wave-line ice times / ``start~len``, or ``w...`` lines
    that all carry a bare length instead of ``len``)."""
    wave_lines = 0
    bare_length_waves = 0
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if stripped.split()[0] == "SET" or _SEML_SETTING_RE.match(stripped) or _SEML_SMART_RE.match(stripped):
            return True
        if _is_wave_header(stripped):
            wave_lines += 1
            tokens = stripped.split()
            if len(tokens) >= 3 and not any(t.lower() == "len" for t in tokens):
                return True  # ice times or "w1~3 0 1200~1500"
            if _SEML_WAVE_RE.match(stripped):
                bare_length_waves += 1
    return wave_lines > 0 and bare_length_waves == wave_lines


def looks_like_pvzt(text: str) -> bool:
    """True when the text carries a ``.pvzt`` wave header or a §4.3 header key."""
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if _is_wave_header(stripped) or _PVZT_HEADER_RE.match(stripped):
            return True
    return False


def detect_format(text: str) -> str:
    """``"seml"`` / ``"pvzt"`` / ``"shorthand"`` for ``--format auto``."""
    if looks_like_seml(text):
        return "seml"
    if looks_like_pvzt(text):
        return "pvzt"
    return "shorthand"


def parse_track_text(text: str, dialect: Optional[str] = None, fmt: str = "auto") -> Track:
    """Parse shorthand, ``.pvzt`` or Seml text (``fmt``; ``auto`` detects).

    ``dialect`` applies to shorthand and is the default for ``.pvzt`` text
    without a ``dialect:`` header (a header wins, with a warning on stderr
    when it differs from an explicitly requested dialect). Seml has a fixed
    dialect (``bridges.seml.SEML_DIALECT``).
    """
    if fmt not in FORMATS:
        raise ValueError(f"--format: expected one of {FORMATS}, got {fmt!r}")
    if fmt == "auto":
        fmt = detect_format(text)
    if fmt == "seml":
        from pvztools.bridges import from_seml

        return from_seml(text)
    if fmt == "pvzt":
        track = parse_pvzt(text, dialect=dialect)
        if dialect is not None and track.dialect != dialect:
            print(f"warning: --dialect {dialect} ignored; the dialect: header says {track.dialect}", file=sys.stderr)
        return track
    return parse_shorthand(text, dialect=dialect or DEFAULT_DIALECT)


def _parse_positions(text: Optional[str], what: str) -> Tuple[Tuple[int, int], ...]:
    """``"25,25,15"`` -> ``((2,5),(2,5),(1,5))``; each item is ``<row><col>``."""
    if not text:
        return ()
    out: List[Tuple[int, int]] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        if not (item.isdigit() and len(item) == 2):
            raise ValueError(f"--{what}: expected two-digit <row><col> items like 25, got {item!r}")
        out.append((int(item[0]), int(item[1])))
    return tuple(out)


def _parse_rows(text: Optional[str], what: str) -> Tuple[int, ...]:
    if not text:
        return ()
    rows: List[int] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit():
            raise ValueError(f"--{what}: expected row numbers like 1,6, got {item!r}")
        rows.append(int(item))
    return tuple(rows)


def _format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join("{:<%d}" % w for w in widths)
    lines = [fmt.format(*headers).rstrip(), "  ".join("-" * w for w in widths)]
    lines.extend(fmt.format(*row).rstrip() for row in rows)
    return "\n".join(lines)


def _print_diagnostics(diags: Sequence[Diagnostic], out) -> bool:
    """Print diagnostics; return True when any is error-level."""
    has_error = False
    for d in diags:
        print(str(d), file=out)
        has_error = has_error or d.level == "error"
    return has_error


def _track_summary(track: Track) -> str:
    if track.kind == "pvzt":
        return dump_pvzt(track)
    return dump_shorthand(track)


def _load_track(args: argparse.Namespace) -> Track:
    """Read and parse the track argument; parse errors name the file."""
    text = read_track_arg(args.source)
    try:
        return parse_track_text(text, args.dialect, args.format)
    except ValueError as exc:
        if args.source != "-" and os.path.isfile(args.source):
            raise ValueError(f"{args.source}: {exc}") from None
        raise


# --------------------------------------------------------------------------- track commands
def cmd_track_parse(args: argparse.Namespace) -> int:
    track = _load_track(args)
    if args.json:
        print(to_json(track))
        return EXIT_OK
    print(f"kind: {track.kind}  waves: {len(track.waves)}  dialect: {track.dialect}")
    if track.rhythm is not None:
        print(f"rhythm: {track.rhythm.raw or track.rhythm.family}")
    print(_track_summary(track))
    warnings = track.meta.get("warnings") or []
    for w in warnings:
        msg = w["message"] if isinstance(w, dict) else str(w)
        print(f"warning: {msg}", file=sys.stderr)
    return EXIT_OK


def cmd_track_lint(args: argparse.Namespace) -> int:
    track = _load_track(args)
    diags = lint(track, scene=args.scene)
    if not diags:
        print("no diagnostics")
        return EXIT_OK
    return EXIT_LINT if _print_diagnostics(diags, sys.stdout) else EXIT_OK


def _target_text(action) -> str:
    if action.target is None:
        return "-"
    rows = "".join(str(r) for r in action.target.rows)
    col = action.target.col
    col_text = str(int(col)) if float(col).is_integer() else ("%.4f" % col).rstrip("0").rstrip(".")
    return f"{rows}-{col_text}"


def cmd_track_expand(args: argparse.Namespace) -> int:
    track = _load_track(args)
    scene = Scene.from_code(args.scene).code
    ctx = ExpandContext(
        scene=scene,
        cob_positions=_parse_positions(args.cobs, "cobs"),
        ice_slots=_parse_positions(args.ice, "ice"),
        fodder_rows=_parse_rows(args.fodder_rows, "fodder-rows"),
        wave_count=args.waves,
    )
    plan = expand(track, ctx)
    rows = []
    for a in plan.actions:
        cannon = "-" if a.cannon is None else f"{a.cannon[0]}-{a.cannon[1]}"
        rows.append((str(a.wave), str(a.effect_cs), str(a.operation_cs), a.kind, a.symbol, _target_text(a), cannon))
    print(_format_table(("wave", "effect", "operation", "kind", "symbol", "target", "cannon"), rows))
    if plan.issues:
        print()
        _print_diagnostics(plan.issues, sys.stdout)
    return EXIT_OK


def cmd_track_to_seml(args: argparse.Namespace) -> int:
    from pvztools.bridges import to_seml

    track = _load_track(args)
    print(to_seml(track))
    return EXIT_OK


def cmd_track_to_avz(args: argparse.Namespace) -> int:
    from pvztools.bridges import to_avz_dsl

    track = _load_track(args)
    print(to_avz_dsl(track))
    return EXIT_OK


# --------------------------------------------------------------------------- lineup commands
def lineup_grid(lineup) -> str:
    """ASCII grid, one line per row, 2-char plant abbreviations per cell.

    Lower layers are not shown; a cob cannon shows as ``CC cc`` (two cells),
    ladder ``==``, gravestone ``##``, empty ``..``. A ``*`` after the row
    number marks the garden rake row.
    """
    lines = [" " * 3 + " ".join(f"{c:>2}" for c in range(1, 10))]
    cob_right = set((r, c + 1) for r, c in lineup.cobs())
    for row in range(1, lineup.rows + 1):
        mark = "*" if lineup.rake_row == row else " "
        cells = []
        for col in range(1, 10):
            cell = lineup.cell(row, col)
            if (row, col) in cob_right and (cell is None or cell.is_empty):
                cells.append(COB_RIGHT)
            elif cell is None or cell.is_empty:
                cells.append(EMPTY)
            elif cell.plant is not None:
                text = PLANT_ABBR.get(cell.plant, "%02X" % cell.plant)
                cells.append(text.lower() if cell.asleep else text)
            elif "tomb" in cell.props:
                cells.append(TOMB)
            elif "ladder" in cell.props:
                cells.append(LADDER)
            elif cell.container is not None:
                cells.append(PLANT_ABBR.get(cell.container, "%02X" % cell.container))
            else:
                cells.append(PLANT_ABBR.get(cell.shell or cell.addon or -1, "??"))
        lines.append(f"{row}{mark} " + " ".join(cells))
    return "\n".join(lines)


def _print_lineup(lineup, out=None) -> None:
    from pvztools.schema.lineup import dump_lineup_string

    out = out or sys.stdout
    head = f"scene: {lineup.scene}  cells: {len(lineup.cells)}  cobs: {len(lineup.cobs())}"
    if lineup.name:
        head = f"name: {lineup.name}  " + head
    print(head, file=out)
    print(lineup_grid(lineup), file=out)
    print("string: " + dump_lineup_string(lineup), file=out)


def cmd_lineup_decode(args: argparse.Namespace) -> int:
    from pvztools.schema.lineup import decode_lineup_code

    lineup = decode_lineup_code(args.code)
    _print_lineup(lineup)
    return EXIT_LINT if _print_diagnostics(lineup.validate(), sys.stdout) else EXIT_OK


def cmd_lineup_encode(args: argparse.Namespace) -> int:
    from pvztools.schema.lineup import encode_lineup_code, parse_lineup_string

    text = args.string
    if os.path.isfile(text):
        with open(text, encoding="utf-8") as f:
            text = f.read()
    lineup = parse_lineup_string(text)
    print(encode_lineup_code(lineup))
    return EXIT_OK


def load_lineup_db(path: str) -> List[Dict[str, Any]]:
    """Load the ArrayDesign lineup catalogue (default: bundled ``pvztools/data/lineups.json``)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data["lineups"] if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected a list of lineups under 'lineups'")
    return entries


def find_lineup_entries(entries: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    """Select by exact index, exact name, then case-insensitive substring of the name."""
    if key.isdigit():
        idx = int(key)
        return [e for e in entries if e.get("index") == idx] or ([entries[idx]] if idx < len(entries) else [])
    exact = [e for e in entries if e.get("name") == key]
    if exact:
        return exact
    low = key.lower()
    return [e for e in entries if low in str(e.get("name", "")).lower()]


def cmd_lineup_show(args: argparse.Namespace) -> int:
    from pvztools.schema.lineup import encode_lineup_code, parse_lineup_string

    path = args.db or DEFAULT_DB
    if not os.path.isfile(path):
        raise FileNotFoundError(f"lineup database not found: {path} (pass --db PATH)")
    entries = load_lineup_db(path)
    found = find_lineup_entries(entries, args.name)
    if not found:
        raise LookupError(f"no lineup matching {args.name!r} in {path}")
    if len(found) > 1 and not args.all:
        print(f"{len(found)} lineups match {args.name!r} (use --all to show every one):")
        for e in found:
            print(f"  [{e.get('index')}] {e.get('scene')} {e.get('name')}  ({e.get('map', '')})")
        return EXIT_OK
    for i, entry in enumerate(found):
        if i:
            print()
        lineup = parse_lineup_string(entry["raw"])
        print(f"[{entry.get('index')}] {entry.get('name')}  map: {entry.get('map', '')}")
        _print_lineup(lineup)
        print("code: " + encode_lineup_code(lineup))
    return EXIT_OK


# --------------------------------------------------------------------------- memory commands
EXIT_GAME = 3  # game not running / not in a level / version unsupported
ON_OFF = ("on", "off")


def _on(value: str) -> bool:
    v = value.lower()
    if v not in ON_OFF:
        raise ValueError("expected on|off, got %r" % value)
    return v == "on"


# argparse prints the type callable's __name__ in "invalid ... value" errors
_on.__name__ = "on|off"


def _memory_errors():
    """Exception classes that mean "talk to the user", imported lazily (Windows layers)."""
    from pvztools.control.conv import NotSupported
    from pvztools.game import NotImplementedAction
    from pvztools.modifier import SafetyError
    from pvztools.observe import ObserveError
    from pvztools.process import ProcessError

    return (ProcessError, ObserveError, SafetyError, NotSupported, NotImplementedAction)


def _controller_busy():
    from pvztools.process import ControllerBusy
    return ControllerBusy


def _open_game(args: argparse.Namespace, *, write: bool = False, patch: bool = False,
               remote: bool = False, input: bool = False):
    from pvztools.game import attach

    # write and patch are independent grants: --patch alone must not enable
    # memory writes (attach opens the process for writing in both cases);
    # remote implies both (attach does that itself); input is independent
    # (window messages only, no memory access beyond reads)
    extra = {"input": True} if input else {}  # only when asked (keeps attach fakes simple)
    if remote:
        return attach(getattr(args, "pid", None), write=write, patch=patch, remote=True, **extra)
    return attach(getattr(args, "pid", None), write=write, patch=patch, **extra)


def _run_memory(args: argparse.Namespace, body) -> int:
    """Run ``body(game)`` with the game attached; human-readable failures -> exit 3."""
    errors = _memory_errors()
    try:
        game = _open_game(args, write=getattr(args, "write", False), patch=getattr(args, "patch", False),
                          remote=getattr(args, "remote", False), input=getattr(args, "input", False))
    except _controller_busy() as exc:
        print("error: %s" % exc, file=sys.stderr)
        print("hint: 同一时间只能有一个 pvztools 实例控制游戏（写/补丁/远程/键控）；只读命令不受限制",
              file=sys.stderr)
        return EXIT_GAME
    except errors as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_GAME
    try:
        return body(game)
    except errors as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_GAME
    finally:
        game.close()


def _doctor_unsupported(args: argparse.Namespace) -> int:
    """doctor for a build the resolver graded UNSUPPORTED: still print the
    report (that is what doctor is for) and write the diagnostics bundle."""
    from pvztools.addr.resolve import doctor_report, export_diagnostics, resolve
    from pvztools.process import attach as process_attach

    with process_attach(getattr(args, "pid", None)) as session:
        res = resolve(session)
        print(doctor_report(res))
        if args.json:
            out = export_diagnostics(res, args.json)
            print("diagnostics written: %s" % out)
    core = [m for m in res.missing if not m.startswith(("functions.", "patches."))]
    print("error: 这个版本不受支持（capability %s）；缺少关键地址：%s（另有 %d 项函数/补丁未找到）"
          % (res.capability, ", ".join(core) or "?", len(res.missing) - len(core)), file=sys.stderr)
    return EXIT_GAME


def cmd_doctor(args: argparse.Namespace) -> int:
    def body(game) -> int:
        print(game.doctor())
        if args.json:
            from pvztools.addr.resolve import export_diagnostics

            out = export_diagnostics(game.resolution(), args.json)
            print("diagnostics written: %s" % out)
        return EXIT_OK

    from pvztools.game import UnknownBuild

    try:
        game = _open_game(args)
    except UnknownBuild:
        try:
            return _doctor_unsupported(args)
        except _memory_errors() as exc:
            print("error: %s" % exc, file=sys.stderr)
            return EXIT_GAME
    except _memory_errors() as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_GAME
    try:
        return body(game)
    except _memory_errors() as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_GAME
    finally:
        game.close()


def cmd_scan(args: argparse.Namespace) -> int:
    from pvztools.addr.scan import scan_exe

    result, pe = scan_exe(args.exe)
    if args.json:
        d = result.as_dict()
        d["found"] = {k: "0x%x" % v for k, v in result.found.items()}
        d["stamp"] = "0x%08x" % pe.stamp
        d["image_base"] = "0x%x" % pe.image_base
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return EXIT_OK
    print("exe: %s" % args.exe)
    print("stamp 0x%08x  image_base 0x%x  family %s  %.2fs" % (pe.stamp, pe.image_base, result.family, result.elapsed))
    rows = [(k, "0x%X" % v, "%.2f" % result.confidence.get(k, 0.0)) for k, v in sorted(result.found.items())]
    print(_format_table(("symbol", "value", "confidence"), rows))
    if result.missing:
        print()
        print("missing (%d): %s" % (len(result.missing), ", ".join(result.missing)))
    return EXIT_OK


def _print_board(game) -> None:
    b = game.board
    print("sun %d  wave %d/%d  countdown %d (start %d)  clock %d  scene %d  level %d  paused %s"
          % (b.sun, b.wave, b.total_waves, b.countdown, b.countdown_init, b.clock, b.scene, b.level, b.paused))


def _print_plants(game) -> None:
    plants = game.plants
    print("plants: %d" % len(plants))
    rows = [(str(p.index), p.name, "%d-%d" % p.cell, str(p.hp), str(p.max_hp), str(p.state)) for p in plants]
    if rows:
        print(_format_table(("#", "name", "cell", "hp", "max", "state"), rows))


def _print_zombies(game) -> None:
    zombies = game.zombies
    print("zombies: %d" % len(zombies))
    rows = [(str(z.index), z.name, str(z.row + 1), "%.0f" % z.x, str(z.hp), str(z.helmet_hp), str(z.shield_hp), str(z.status))
            for z in zombies]
    if rows:
        print(_format_table(("#", "name", "row", "x", "hp", "helmet", "shield", "status"), rows))


def _print_seeds(game) -> None:
    seeds = game.seeds
    print("seeds: %d" % len(seeds))
    rows = [(str(s.slot), s.name, "empty" if s.empty else ("ready" if s.ready else "cd %d" % s.cd_remaining))
            for s in seeds]
    if rows:
        print(_format_table(("slot", "card", "state"), rows))


def _print_spawn(game) -> None:
    print(game.describe_spawn())


READ_WHAT = ("sun", "board", "plants", "zombies", "seeds", "spawn", "all")


def cmd_read(args: argparse.Namespace) -> int:
    def body(game) -> int:
        what = args.what
        print("%s  pid %d  capability %s  ui %d (%s)" % (game.version, game.pid, game.capability, game.ui, game.ui_name))
        if not game.in_level:
            print("error: 请先进入关卡（现在在%s）" % game.ui_name, file=sys.stderr)
            return EXIT_GAME
        if what == "sun":
            print("sun %d" % game.sun)
            return EXIT_OK
        if what in ("board", "all"):
            _print_board(game)
        if what in ("plants", "all"):
            _print_plants(game)
        if what in ("zombies", "all"):
            _print_zombies(game)
        if what in ("seeds", "all"):
            _print_seeds(game)
        if what in ("spawn", "all"):
            _print_spawn(game)
        return EXIT_OK

    return _run_memory(args, body)


def cmd_spawn_show(args: argparse.Namespace) -> int:
    args.what = "spawn"
    return cmd_read(args)


MOD_NEEDS = {
    # action -> ("write" | "patch", description)
    "sun": ("write", "写入阳光"),
    "speed": ("write", "写入游戏速度"),
    "no-cooldown": ("write", "写入卡槽冷却"),
    "free-planting": ("write", "写入自由种植标志"),
    "kill-zombies": ("write", "写入僵尸状态"),
    "sun-limit": ("patch", "修改游戏代码（阳光上限）"),
    "auto-collect": ("patch", "修改游戏代码（自动收集）"),
    "background": ("patch", "修改游戏代码（后台运行）"),
}
PATCH_ACTIONS = ("sun-limit", "auto-collect", "background")


def cmd_mod(args: argparse.Namespace) -> int:
    need, desc = MOD_NEEDS[args.action]
    flag = "--write" if need == "write" else "--patch"
    if not getattr(args, need):
        print("error: %s 需要 %s（默认只读，什么都不会改）" % (desc, flag), file=sys.stderr)
        return EXIT_ERROR
    if not args.yes:
        print("error: 即将%s；确认请加 --yes" % desc, file=sys.stderr)
        return EXIT_ERROR

    def body(game) -> int:
        if args.action not in PATCH_ACTIONS and not game.in_level:
            print("error: 请先进入关卡（现在在%s）" % game.ui_name, file=sys.stderr)
            return EXIT_GAME
        c = game.cheats
        act = args.action
        if act == "sun":
            old = game.sun
            game.sun = args.value
            print("sun %d -> %d" % (old, game.sun))
        elif act == "speed":
            try:
                c.speed = args.value
            except (TypeError, ValueError) as exc:
                print("error: %s" % exc, file=sys.stderr)
                return EXIT_ERROR
            print("speed -> %gx (frame_duration %d ms)" % (c.speed, c.frame_duration))
        elif act == "no-cooldown":
            c.no_cooldown = args.on
            print("no-cooldown %s" % ("on" if args.on else "off"))
        elif act == "free-planting":
            c.free_planting = args.on
            print("free-planting %s" % ("on" if c.free_planting else "off"))
        elif act == "kill-zombies":
            print("kill-zombies: %d" % c.kill_zombies())
        else:
            attr = act.replace("-", "_")
            setattr(c, attr, args.on)
            print("%s %s" % (act, "on" if getattr(c, attr) else "off"))
            # the user asked for this code patch explicitly: keep it when the
            # CLI exits instead of auto-restoring on close
            game.keep_patches()
        return EXIT_OK

    return _run_memory(args, body)


# --------------------------------------------------------------------------- remote commands
def _remote_gate(args: argparse.Namespace, desc: str) -> Optional[int]:
    """Remote-call commands need ``--remote --yes``; returns an exit code to
    return early, or ``None`` to proceed."""
    if not getattr(args, "remote", False):
        print("error: %s 需要 --remote（向游戏注入代码；同时隐含 --write --patch；默认只读，什么都不会改）" % desc,
              file=sys.stderr)
        return EXIT_ERROR
    if not args.yes:
        print("error: 即将%s；确认请加 --yes" % desc, file=sys.stderr)
        return EXIT_ERROR
    return None


def _need_level(game) -> Optional[int]:
    if not game.in_level:
        print("error: 请先进入关卡或选卡界面（现在在%s）" % game.ui_name, file=sys.stderr)
        return EXIT_GAME
    return None


def _split_types(items: Sequence[str]) -> List[str]:
    """``["普杆舞红"]`` / ``["0,2,32"]`` / ``["2", "32"]`` -> flat list of tokens."""
    out: List[str] = []
    for it in items:
        for tok in re.split(r"[,\s]+", it.strip()):
            if tok:
                out.append(tok)
    return out


def cmd_plant(args: argparse.Namespace) -> int:
    rc = _remote_gate(args, "放植物 %s@(%d,%d)" % (args.name, args.row, args.col))
    if rc is not None:
        return rc

    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        from pvztools.control.actions import plant_label, resolve_plant

        idx = game.plant(args.name, args.row, args.col, imitater=args.imitater)
        print("已放置 %s 于 (%d,%d)%s，植物下标 %s"
              % (plant_label(resolve_plant(args.name)), args.row, args.col,
                 "（模仿者）" if args.imitater else "", idx))
        return EXIT_OK

    return _run_memory(args, body)


def cmd_shovel(args: argparse.Namespace) -> int:
    rc = _remote_gate(args, "铲植物 (%d,%d)" % (args.row, args.col))
    if rc is not None:
        return rc

    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        n = game.shovel(args.row, args.col, args.layer)
        if n == 0:
            print("(%d,%d) 没有植物" % (args.row, args.col))
        else:
            print("已铲除 (%d,%d) 的 %s 株植物" % (args.row, args.col, n))
        return EXIT_OK

    return _run_memory(args, body)


def cmd_lineup_place(args: argparse.Namespace) -> int:
    rc = _remote_gate(args, "布阵 %s" % args.what)
    if rc is not None:
        return rc

    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        summary = game.place_lineup(args.what, clear=args.clear, db_path=args.db)
        print("布阵完成：%s" % summary)
        return EXIT_OK

    return _run_memory(args, body)


def cmd_spawn_set(args: argparse.Namespace) -> int:
    if not args.write:
        print("error: 写出怪种类需要 --write（默认只读，什么都不会改）", file=sys.stderr)
        return EXIT_ERROR
    if not args.yes:
        print("error: 即将写出怪种类；确认请加 --yes", file=sys.stderr)
        return EXIT_ERROR
    types = _split_types(args.types)

    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        game.spawn_allowed = types
        from pvztools.observe.spawn import short_name

        print("出怪种类: " + " ".join(short_name(z) for z in game.spawn_allowed))
        return EXIT_OK

    return _run_memory(args, body)


def cmd_spawn_reroll(args: argparse.Namespace) -> int:
    rc = _remote_gate(args, "重新出怪")
    if rc is not None:
        return rc
    types = _split_types(args.types) if args.types else None

    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        sl = game.spawn_reroll(types)
        from pvztools.observe.spawn import describe

        print("已重新出怪")
        print(describe(sl) if sl is not None else game.describe_spawn())
        return EXIT_OK

    return _run_memory(args, body)


def cmd_spawn_stats(args: argparse.Namespace) -> int:
    rc = _remote_gate(args, "出怪统计 %d 次（结束后恢复原出怪表）" % args.n)
    if rc is not None:
        return rc
    if args.n <= 0:
        print("error: 次数必须是正整数", file=sys.stderr)
        return EXIT_ERROR
    types = _split_types(args.types) if args.types else None

    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        st = game.spawn_stats(args.n, types)
        print(st.describe())
        print("原出怪表与出怪种类已恢复")
        return EXIT_OK

    return _run_memory(args, body)


CLEAR_WHAT: Dict[str, Any] = {
    "plants": None,
    "graves": (1,),
    "ladders": (3,),
    "rakes": (11,),
    "craters": (2,),
    "items": (1, 2, 3, 11),
}


def cmd_clear(args: argparse.Namespace) -> int:
    rc = _remote_gate(args, "清除 %s" % args.what)
    if rc is not None:
        return rc

    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        types = CLEAR_WHAT[args.what]
        n = game.clear(args.what)
        if types is None:
            print("已清除植物 %s 株" % n)
        else:
            print("已清除场地物件 %s 个（类型 %s）" % (n, list(types)))
        return EXIT_OK

    return _run_memory(args, body)


def cmd_cobs(args: argparse.Namespace) -> int:
    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        cobs = game.cobs
        print("cobs: %d" % len(cobs))
        rows = [(str(c.index), "%d-%d" % (c.row, c.col), str(c.state), c.state_name,
                 "-" if c.countdown is None else str(c.countdown)) for c in cobs]
        if rows:
            print(_format_table(("#", "cell", "state", "status", "ready in (cs)"), rows))
        return EXIT_OK

    return _run_memory(args, body)


def cmd_fire(args: argparse.Namespace) -> int:
    rc = _remote_gate(args, "发炮 (%d,%d) -> (%d,%s)" % (args.cob_row, args.cob_col, args.row, args.col))
    if rc is not None:
        return rc

    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        res = game.fire_cob((args.cob_row, args.cob_col), args.row, args.col, check_ready=not args.force)
        print("已发炮 #%d (%d,%d) -> (%d,%g)  像素 (%d,%d)  state %d -> %s"
              % (res.plant_index, args.cob_row, args.cob_col, args.row, args.col, res.x, res.y,
                 res.state_before, "?" if res.state_after is None else res.state_after))
        return EXIT_OK

    return _run_memory(args, body)


# --------------------------------------------------------------------------- executor commands
def _non_negative_int(text: str) -> int:
    v = int(text)
    if v < 0:
        raise argparse.ArgumentTypeError("must be >= 0, got %d" % v)
    return v


def _positive_int(text: str) -> int:
    v = int(text)
    if v < 1:
        raise argparse.ArgumentTypeError("must be >= 1, got %d" % v)
    return v


def _backend_gate(args: argparse.Namespace, desc: str) -> Optional[str]:
    """Pick the backend from ``--input`` / ``--remote`` (+ ``--yes`` unless
    ``--dry-run``); prints an error and returns ``None`` to stop."""
    use_input, use_remote = getattr(args, "input", False), getattr(args, "remote", False)
    dry = getattr(args, "dry_run", False)
    if use_input and use_remote:
        print("error: --input 与 --remote 二选一", file=sys.stderr)
        return None
    mode = "remote" if use_remote else "input"
    if not (use_input or use_remote) and not dry:
        print("error: %s 需要 --input（键控：向游戏窗口投递鼠标消息，不写内存）或 --remote（修改器：注入代码调用游戏函数）；"
              "加 --dry-run 只等待不动作" % desc, file=sys.stderr)
        return None
    if not dry and not getattr(args, "yes", False):
        print("error: 即将%s（%s 模式）；确认请加 --yes" % (desc, mode), file=sys.stderr)
        return None
    return mode


def _write_report(path: Optional[str], payload: str) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)
        print("report written: %s" % path)


def _progress_event(ev: dict) -> None:
    """One line per executor event (``--quiet`` disables)."""
    kind = ev.get("event")
    if kind == "dispatch":
        fired = [f for f in (ev.get("fired_cs") or []) if f is not None]
        extra = ""
        if fired:
            extra += "  fired %s" % ",".join("%+d" % f for f in fired)
        if ev.get("cd_wait_cs") is not None:
            extra += "  CD-WAIT +%dcs" % ev["cd_wait_cs"]
        print("  w%-2d %+6dcs -> observed %+6s delta %+d  %s%s" % (
            ev.get("wave"), ev.get("operation_cs"), ev.get("observed_cs"), ev.get("delta"),
            ",".join(ev.get("symbols") or ()), extra), flush=True)
    elif kind in ("wave", "pause", "resume", "late", "skip", "error", "stop", "warning", "cd_wait"):
        print("  [%s] %s" % (kind, " ".join("%s=%s" % (k, v) for k, v in ev.items() if k != "event")), flush=True)


def _ctx_kwargs(args: argparse.Namespace) -> Dict[str, Any]:
    """ExpandContext overrides from ``--ice / --fodder-rows / --cob-col``."""
    kw: Dict[str, Any] = dict(ice_slots=_parse_positions(getattr(args, "ice", None), "ice"),
                              fodder_rows=_parse_rows(getattr(args, "fodder_rows", None), "fodder-rows"))
    col = getattr(args, "cob_col", None)
    if col is not None:
        if not (1.0 <= col <= 10.0):
            raise ValueError("--cob-col 必须在 1..10 之间，得到 %r" % col)
        kw["default_cob_col"] = float(col)
    return kw


def cmd_track_run(args: argparse.Namespace) -> int:
    mode = _backend_gate(args, "执行轨道 %s" % args.source)
    if mode is None:
        return EXIT_ERROR
    if args.dry_run:
        # a rehearsal never acts: attach read-only, grant neither input nor remote
        args.input = args.remote = False
    track = _load_track(args)
    try:
        ctx_kw = _ctx_kwargs(args)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    on_event = _progress_event

    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        print("%s: wave %d/%d clock %d  backend=%s%s" % (
            "dry-run" if args.dry_run else "run", game.wave, game.total_waves, game.clock, mode,
            " (dry-run)" if args.dry_run else ""))
        if game.wave > 0:
            print("warning: 已在第 %d 波中途开始；该波的刷新点只能由 countdown 估计（见报告 warning）" % game.wave)
        report = game.run_track(track, scene=args.scene, cobs=args.cobs, backend=mode,
                                dry_run=args.dry_run, tolerance_cs=args.tolerance,
                                late_policy=args.late_policy, lead_cs=args.lead_cs,
                                on_event=on_event if not args.quiet else None, **ctx_kw)
        print(report.to_text())
        _write_report(args.report, report.to_json())
        return EXIT_OK

    return _run_memory(args, body)


def _seed_list(text: Optional[str]) -> Optional[List[str]]:
    if text is None:
        return None
    return _split_types([text])


def cmd_play(args: argparse.Namespace) -> int:
    """``pvztools play --lineup X --track T [--seeds ...] --remote --yes``:
    chooser -> lineup -> track (frame-locked cobs) in one go."""
    mode = _backend_gate(args, "打一关 lineup=%s track=%s" % (args.lineup, args.track))
    if mode is None:
        return EXIT_ERROR
    if not args.yes and not args.dry_run:
        return EXIT_ERROR
    try:
        ctx_kw = _ctx_kwargs(args)
        track = parse_track_text(read_track_arg(args.track), args.dialect, args.format)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    seeds = _seed_list(args.seeds)
    slots = _seed_list(args.slots)
    # the chooser needs window input even in remote mode; the lineup needs
    # remote; a dry run attaches with whatever was asked (it acts on nothing)
    if not args.dry_run:
        args.input = True

    def body(game) -> int:
        ui = game.ui
        if ui not in (1, 2, 3, 5):
            print("error: play 需要在主菜单 / 选卡界面 / 关卡 / 奖励界面（现在在%s）" % game.ui_name, file=sys.stderr)
            return EXIT_GAME
        if args.dry_run and ui != 3:
            print("error: --dry-run 需要已在关卡中（不会选卡 / 布阵）", file=sys.stderr)
            return EXIT_GAME
        print("play: ui=%s lineup=%s track=%s seeds=%s backend=%s collect=%s foreground=%s" % (
            game.ui_name, args.lineup, getattr(track, "name", None) or args.track, seeds or "default",
            mode, not args.no_collect, not args.no_foreground), flush=True)
        report = game.play_level(args.lineup, track, seeds, backend=mode, cob_col=args.cob_col,
                                 slot_types=slots, place=not args.no_place and not args.dry_run,
                                 dry_run=args.dry_run, tolerance_cs=args.tolerance,
                                 late_policy=args.late_policy, auto_collect=not args.no_collect,
                                 foreground=not args.no_foreground, chooser=args.chooser, track_label=args.track,
                                 on_event=None if args.quiet else _progress_event, **ctx_kw)
        print(report.to_text())
        for st in (report.meta.get("steps") or []) if getattr(report, "meta", None) else []:
            print("  step %-16s %s" % (st.get("name"), st.get("result")))
        _write_report(args.report, report.to_json())
        _write_report(getattr(args, "md", None), report.to_markdown())
        return EXIT_OK

    return _run_memory(args, body)


def _campaign_event(ev: dict) -> None:
    """One line per campaign event (``--quiet`` disables)."""
    kind = ev.get("event")
    if kind == "level":
        r = ev.get("report") or {}
        mad = r.get("mean_abs_delta")
        print("  关卡 (level) %s [%s] 阵型 %s: 发炮 %s 组, 平均|Δ| %s cs, 收集 %s -> %s" % (
            r.get("level"), r.get("scene"), r.get("lineup"), r.get("fires"),
            "-" if mad is None else "%.1f" % mad, r.get("collected"), r.get("result")), flush=True)
    elif kind == "skip":
        print("  跳过 (skip): %s（场景 %s，关卡 %s）" % (ev.get("reason"), ev.get("scene"), ev.get("level")), flush=True)
    elif kind == "stop":
        print("  结束 (stop): %s（共 %s 关）" % (ev.get("reason"), ev.get("played")), flush=True)


def cmd_campaign(args: argparse.Namespace) -> int:
    """``pvztools campaign [N|auto] --remote --yes``: play several adventure
    levels back to back (chooser -> lineup -> strict-CD fires -> award)."""
    spec = str(args.count)
    auto = False
    n = 1
    if spec.lower() == "auto":
        auto = True
    else:
        try:
            n = int(spec)
        except ValueError:
            print("error: campaign 的参数必须是关卡数或 'auto'（得到 %r）" % spec, file=sys.stderr)
            return EXIT_ERROR
        if n < 1:
            print("error: 关卡数必须 >= 1", file=sys.stderr)
            return EXIT_ERROR
    desc = "自动打连关（直到出现非冒险关卡）" if auto else "打 %d 关" % n
    if not getattr(args, "remote", False):
        print("error: %s 需要 --remote（向游戏注入代码：布阵 / 发炮 / 选卡；隐含 --write --patch；默认只读）" % desc,
              file=sys.stderr)
        return EXIT_ERROR
    if not args.yes:
        print("error: 即将%s；确认请加 --yes" % desc, file=sys.stderr)
        return EXIT_ERROR
    if args.cycle < 1:
        print("error: --cycle 必须 >= 1", file=sys.stderr)
        return EXIT_ERROR
    # the seed chooser needs window input even in remote mode
    args.input = True

    def body(game) -> int:
        ui = game.ui
        if ui not in (1, 2, 3, 5):
            print("error: campaign 需要在主菜单 / 选卡界面 / 关卡 / 奖励界面（现在在%s）" % game.ui_name, file=sys.stderr)
            return EXIT_GAME
        print("campaign: %s lineup=%s cycle=%d collect=%s foreground=%s" % (
            "auto" if auto else "%d 关" % n, args.lineup or "场景默认 (scene-default)", args.cycle,
            not args.no_collect, not args.no_foreground), flush=True)
        rep = game.campaign(n, auto=auto, lineup=args.lineup, cycle=args.cycle,
                            collect=not args.no_collect, foreground=not args.no_foreground, quiet=True,
                            on_event=None if args.quiet else _campaign_event)
        print("已打 %d 关，结束原因：%s" % (rep.played, rep.stop))
        for l in rep.levels:
            print("  " + l.to_row().strip("| ").replace(" | ", "  "))
        if rep.skipped:
            print("跳过 (skipped): %s" % rep.skipped.get("reason"))
        print("合计 (total): 发炮 %d 组，收集 %d，平均|Δ| %s cs" % (
            rep.total_fires, rep.total_collected,
            "-" if rep.mean_abs_delta is None else "%.1f" % rep.mean_abs_delta))
        _write_report(args.report, rep.to_json())
        _write_report(getattr(args, "md", None), rep.to_markdown())
        return EXIT_OK

    return _run_memory(args, body)


def _input_gate(args: argparse.Namespace, desc: str) -> Optional[int]:
    if not getattr(args, "yes", False):
        print("error: 即将%s（向游戏窗口投递鼠标消息）；确认请加 --yes" % desc, file=sys.stderr)
        return EXIT_ERROR
    args.input = True
    return None


def cmd_chooser(args: argparse.Namespace) -> int:
    what = args.chooser_cmd
    rc = _input_gate(args, "操作选卡界面 (%s)" % what)
    if rc is not None:
        return rc

    def body(game) -> int:
        if game.ui != 2:
            print("error: 不在选卡界面（现在在%s）" % game.ui_name, file=sys.stderr)
            return EXIT_GAME
        ch = game.chooser
        if what == "clear":
            print("cleared %d" % ch.clear_bank())
        elif what == "pick":
            ids = ch.pick(_split_types(args.types))
            print("picked %s; bank now %s" % (ids, ch.bank_types()))
        elif what == "start":
            ok = ch.start(wait=args.wait)
            print("started" if ok else "not started (ui=%s)" % game.ui_name)
            return EXIT_OK if ok else EXIT_GAME
        elif what == "select":
            ok = ch.select_and_start(_split_types(args.types), wait=args.wait)
            print("started" if ok else "not started (ui=%s)" % game.ui_name)
            return EXIT_OK if ok else EXIT_GAME
        return EXIT_OK

    return _run_memory(args, body)


def cmd_window(args: argparse.Namespace) -> int:
    what = args.window_cmd
    if what in ("calibrate", "front"):
        rc = _input_gate(args, "窗口操作 (%s)" % what)
        if rc is not None:
            return rc

    def body(game) -> int:
        w = game.window
        if what == "info":
            print("hwnd=0x%x raw=%sx%s client=%sx%s foreground=%s background_running=%s ui=%s" % (
                w.hwnd, *w.raw_client_size(), *w.client_size(), w.is_foreground(), w.background_running(),
                game.ui_name))
            return EXIT_OK
        if what == "front":
            ok = w.bring_to_foreground()
            print("foreground=%s" % ok)
            return EXIT_OK if ok else EXIT_GAME
        cal = w.calibrate()
        print("calibration: source=%s scale=(%.4f, %.4f) offset=(%.2f, %.2f) client=%sx%s" % (
            cal.source, cal.scale_x, cal.scale_y, cal.offset_x, cal.offset_y, *w.client_size()))
        return EXIT_OK

    return _run_memory(args, body)


def cmd_reactive(args: argparse.Namespace) -> int:
    mode = _backend_gate(args, "反应式执行 policy=%s" % args.policy)
    if mode is None:
        return EXIT_ERROR
    if args.dry_run:
        print("error: reactive 没有 --dry-run（它按实时状态决策）；请用 --input/--remote --yes", file=sys.stderr)
        return EXIT_ERROR

    def body(game) -> int:
        rc = _need_level(game)
        if rc is not None:
            return rc
        print("reactive %s: wave %d/%d clock %d backend=%s" % (args.policy, game.wave, game.total_waves, game.clock, mode))
        log = game.run_reactive(args.policy, args.cobs, args.waves, backend=mode, poll_ms=args.poll_ms,
                                on_error=args.on_error)
        for e in log.actions():
            print("  clock %d w%d %+dcs %s %s%s" % (e.time, e.wave, e.wave_time, e.kind, e.reason,
                                                     (" ERROR " + e.error) if e.error else ""))
        print(log.summary())
        if args.report:
            _write_report(args.report, json.dumps(log.as_dicts(), ensure_ascii=False, indent=2, default=str))
        return EXIT_OK

    return _run_memory(args, body)


def cmd_bench_clock(args: argparse.Namespace) -> int:
    def body(game) -> int:
        from pvztools.control.executor.bench import format_bench

        if not game.in_level:
            print("bench-clock: 不在关卡中（现在在%s），跳过。" % game.ui_name)
            return EXIT_GAME
        res = game.bench_clock(args.seconds, step_cs=args.step)
        if args.json:
            print(json.dumps(res.summary(), ensure_ascii=False, indent=2))
        else:
            print(format_bench(res))
        return EXIT_OK if res.in_level else EXIT_GAME

    return _run_memory(args, body)


def _executor_flags(q: argparse.ArgumentParser) -> None:
    q.add_argument("--input", action="store_true", help=_("help_input"))
    q.add_argument("--remote", action="store_true", help=_("help_remote"))
    q.add_argument("--yes", "-y", action="store_true", help=_("help_yes"))
    q.add_argument("--pid", type=int, help=_("help_pid"))
    q.add_argument("--report", metavar="OUT.json", help=_("help_report"))


def _mod_like_flags(q: argparse.ArgumentParser) -> None:
    """Plain memory-write command: ``--write --yes`` (no --remote; the
    process is opened without thread rights)."""
    q.add_argument("--write", action="store_true", help=_("help_write"))
    q.add_argument("--patch", action="store_true", help=_("help_patch"))
    q.add_argument("--yes", "-y", action="store_true", help=_("help_yes"))
    q.add_argument("--pid", type=int, help=_("help_pid"))


def _remote_flags(q: argparse.ArgumentParser) -> None:
    q.add_argument("--remote", action="store_true",
                   help=_("help_remote_inject"))
    q.add_argument("--write", action="store_true", help=_("help_write"))
    q.add_argument("--patch", action="store_true", help=_("help_patch"))
    q.add_argument("--yes", "-y", action="store_true", help=_("help_yes"))
    q.add_argument("--pid", type=int, help=_("help_pid"))


# --------------------------------------------------------------------------- parser
def _add_track_source(p: argparse.ArgumentParser) -> None:
    p.add_argument("source", help="track file, '-' for stdin, or inline track text")
    p.add_argument("--format", default="auto", choices=FORMATS, help="input format (default: auto-detect)")
    p.add_argument(
        "--dialect",
        default=None,
        choices=DIALECTS,
        help=f"symbol dialect for shorthand, and for .pvzt text without a dialect: header (default: {DEFAULT_DIALECT})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pvztools", description=_("cli_description"), epilog=_("cli_epilog"))
    parser.add_argument("--version", action="version", version=f"pvztools {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    track = sub.add_parser("track", help=_("help_track"))
    tsub = track.add_subparsers(dest="subcommand", metavar="<subcommand>")
    tsub.required = True

    p = tsub.add_parser("parse", help="parse and print the canonical form")
    _add_track_source(p)
    p.add_argument("--json", action="store_true", help="print the AST as JSON")
    p.set_defaults(func=cmd_track_parse)

    p = tsub.add_parser("lint", help="run lint diagnostics (exit 2 on errors)")
    _add_track_source(p)
    p.add_argument("--scene", help="scene code override (DE NE PE FE RE ME)")
    p.set_defaults(func=cmd_track_lint)

    p = tsub.add_parser("expand", help="expand into concrete actions")
    _add_track_source(p)
    p.add_argument("--scene", required=True, help="scene code (DE NE PE FE RE ME)")
    p.add_argument("--cobs", help="cob cannon positions as <row><col> items, e.g. 15,25,45,55")
    p.add_argument("--ice", help="ice-shroom slots as <row><col> items, e.g. 11,61")
    p.add_argument("--fodder-rows", dest="fodder_rows", help="fodder rows, e.g. 1,6")
    p.add_argument("--waves", type=int, default=20, help="wave count (default 20)")
    p.set_defaults(func=cmd_track_expand)

    p = tsub.add_parser("to-seml", help="export as Seml text")
    _add_track_source(p)
    p.set_defaults(func=cmd_track_to_seml)

    p = tsub.add_parser("to-avz", help="export as AvZ DSL")
    _add_track_source(p)
    p.set_defaults(func=cmd_track_to_avz)

    p = tsub.add_parser("run", help=_("help_track_run"))
    _add_track_source(p)
    p.add_argument("--scene", default=None, help="scene code override (default: read from the board)")
    p.add_argument("--cobs", default=None, help="cannon positions <row><col>,... (default: read from the board)")
    p.add_argument("--ice", help="ice-shroom slots as <row><col> items, e.g. 11,61")
    p.add_argument("--fodder-rows", dest="fodder_rows", help="fodder rows, e.g. 1,6")
    p.add_argument("--dry-run", action="store_true", help=_("help_dry_run"))
    p.add_argument("--tolerance", type=_non_negative_int, default=2, help="late tolerance in cs (default 2)")
    p.add_argument("--late-policy", choices=("warn", "skip", "abort"), default="warn", help="warn|skip|abort (default warn)")
    p.add_argument("--lead-cs", dest="lead_cs", type=_non_negative_int, default=None,
                   help="dispatch this many ticks early (default 1; the input backend adds its 2-frame click lag)")
    p.add_argument("--cob-col", dest="cob_col", type=float, default=None,
                   help="landing column of bare P symbols (ExpandContext.default_cob_col; default 9)")
    p.add_argument("--quiet", action="store_true", help="no per-action progress lines")
    _executor_flags(p)
    p.set_defaults(func=cmd_track_run)

    lineup = sub.add_parser("lineup", help="阵型 (lineup) commands")
    lsub = lineup.add_subparsers(dest="subcommand", metavar="<subcommand>")
    lsub.required = True

    p = lsub.add_parser("decode", help="decode a lineup code and show the grid")
    p.add_argument("code")
    p.set_defaults(func=cmd_lineup_decode)

    p = lsub.add_parser("encode", help="encode a lineup string (or file) into a lineup code")
    p.add_argument("string")
    p.set_defaults(func=cmd_lineup_encode)

    p = lsub.add_parser("show", help="show a lineup from the ArrayDesign catalogue")
    p.add_argument("name", help="lineup name (or substring) or index")
    p.add_argument("--db", help=f"catalogue JSON path (default: {DEFAULT_DB})")
    p.add_argument("--all", action="store_true", help="show every match instead of listing them")
    p.set_defaults(func=cmd_lineup_show)

    p = lsub.add_parser("place", help="布阵：把阵型放进游戏（--remote --yes）")
    p.add_argument("what", help="catalogue name / index, lineup code, or lineup string")
    p.add_argument("--db", help=f"catalogue JSON path (default: {DEFAULT_DB})")
    p.add_argument("--clear", dest="clear", action="store_true", default=True,
                   help="clear plants and grid items first (default)")
    p.add_argument("--no-clear", dest="clear", action="store_false", help="keep what is on the field")
    _remote_flags(p)
    p.set_defaults(func=cmd_lineup_place)

    p = sub.add_parser("doctor", help="版本识别、能力分级、地址表（需要游戏运行）")
    p.add_argument("--pid", type=int, help=_("help_pid"))
    p.add_argument("--json", metavar="PATH", help="also write a diagnostics JSON bundle")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("scan", help="离线特征扫描一个游戏 exe（含改版）")
    p.add_argument("exe", help="path to PlantsVsZombies.exe / popcapgame1.exe")
    p.add_argument("--json", action="store_true", help="print the scan result as JSON")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("read", help="读取运行中的游戏（只读）")
    p.add_argument("what", nargs="?", default="all", choices=READ_WHAT)
    p.add_argument("--pid", type=int, help=_("help_pid"))
    p.set_defaults(func=cmd_read)

    spawn = sub.add_parser("spawn", help="出怪表")
    ssub = spawn.add_subparsers(dest="subcommand", metavar="<subcommand>")
    ssub.required = True
    p = ssub.add_parser("show", help="本关 20 波出怪")
    p.add_argument("--pid", type=int, help=_("help_pid"))
    p.set_defaults(func=cmd_spawn_show)

    p = ssub.add_parser("set", help="写出怪种类（--write --yes）：普杆舞红 或 0,2,32")
    p.add_argument("types", nargs="+", help="zombie chars / names / ids")
    _mod_like_flags(p)
    p.set_defaults(func=cmd_spawn_set)

    p = ssub.add_parser("reroll", help="让游戏重新生成出怪表（--remote --yes）")
    p.add_argument("types", nargs="*", help="optional allowed types (chars / names / ids)")
    _remote_flags(p)
    p.set_defaults(func=cmd_spawn_reroll)

    p = ssub.add_parser("stats", help="重新出怪 N 次统计每波僵尸（--remote --yes；结束后恢复）")
    p.add_argument("n", type=int)
    p.add_argument("types", nargs="*", help="optional allowed types (chars / names / ids)")
    _remote_flags(p)
    p.set_defaults(func=cmd_spawn_stats)

    p = sub.add_parser("plant", help="放植物（--remote --yes）")
    p.add_argument("name", help="植物名（中文 / 英文 / 编号）")
    p.add_argument("row", type=int, help="row 1..6")
    p.add_argument("col", type=int, help="col 1..9")
    p.add_argument("--imitater", action="store_true", help="模仿者版本")
    _remote_flags(p)
    p.set_defaults(func=cmd_plant)

    p = sub.add_parser("shovel", help="铲植物（--remote --yes）")
    p.add_argument("row", type=int)
    p.add_argument("col", type=int)
    p.add_argument("--layer", default="plant", choices=("all", "plant", "shell", "container", "addon"))
    _remote_flags(p)
    p.set_defaults(func=cmd_shovel)

    p = sub.add_parser("clear", help="清除植物 / 场地物件（--remote --yes）")
    p.add_argument("what", choices=sorted(CLEAR_WHAT))
    _remote_flags(p)
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("play", help="打一关：主菜单/选卡 -> 布阵 -> 按轨道定帧发炮 + 自动收集 -> 奖励界面（--remote --yes；--lineup 3C八炮 --track \"P4: ...\"）")
    p.add_argument("--lineup", default="3C八炮", help="lineup catalogue name / code (default 3C八炮)")
    p.add_argument("--track", default="P4: PP|PP|PP|PP (869|869|869|869)",
                   help="track text or .pvzt path (default P4: PP|PP|PP|PP (869|869|869|869))")
    p.add_argument("--format", default="auto", choices=FORMATS, help="track format (default: auto-detect)")
    p.add_argument("--dialect", default=None, choices=DIALECTS, help="symbol dialect")
    p.add_argument("--seeds", default=None, help="chooser cards (ids / names, comma separated; default first row + lily pad)")
    p.add_argument("--slots", default=None, help="rewrite the seed bank after starting (plant ids per slot; needs --remote/--write)")
    p.add_argument("--cob-col", dest="cob_col", type=float, default=None,
                   help="landing column of bare P symbols (default 9; use 8 for 3C八炮)")
    p.add_argument("--ice", help="ice-shroom slots as <row><col> items")
    p.add_argument("--fodder-rows", dest="fodder_rows", help="fodder rows, e.g. 1,6")
    p.add_argument("--no-place", dest="no_place", action="store_true", help="do not place the lineup")
    p.add_argument("--no-collect", dest="no_collect", action="store_true", help="不自动收集阳光 / 金币 (disable auto-collect)")
    p.add_argument("--no-foreground", dest="no_foreground", action="store_true",
                   help="不把游戏拉到前台（失焦时自动打 background_running）(do not bring the window to the front)")
    p.add_argument("--chooser", choices=("auto", "memory", "input"), default="auto",
                   help="选卡方式：auto（内存选卡，失败则点击）/ memory / input")
    p.add_argument("--md", metavar="OUT.md", help="同时写 Markdown 报告 (also write a Markdown report)")
    p.add_argument("--dry-run", action="store_true", help="in a level: wait for every action, call no backend")
    p.add_argument("--tolerance", type=_non_negative_int, default=2, help="late tolerance in cs (default 2)")
    p.add_argument("--late-policy", choices=("warn", "skip", "abort"), default="warn")
    p.add_argument("--quiet", action="store_true", help="no per-action progress lines")
    _executor_flags(p)
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("campaign", help="打连关：连续打若干冒险关卡（选卡 -> 布阵 -> 定帧发炮 + 自动收集 -> 奖励）"
                                        "（--remote --yes；campaign N 或 campaign auto）")
    p.add_argument("count", nargs="?", default="1", metavar="N|auto",
                   help="要打的关卡数（默认 1），或 'auto'（一直打到出现非冒险关卡）")
    p.add_argument("--lineup", default=None, help="覆盖每一关的场景默认阵型 (override the scene-default lineup)")
    p.add_argument("--cycle", type=int, default=869, help="固定发炮周期，单位 cs (fixed cob cycle; default 869)")
    p.add_argument("--no-collect", dest="no_collect", action="store_true", help="不自动收集阳光 / 金币")
    p.add_argument("--no-foreground", dest="no_foreground", action="store_true", help="不把游戏拉到前台")
    p.add_argument("--md", metavar="OUT.md", help="同时写 Markdown 报告 (also write a Markdown report)")
    p.add_argument("--quiet", action="store_true", help="no per-level progress lines")
    _executor_flags(p)
    p.set_defaults(func=cmd_campaign)

    chooser = sub.add_parser("chooser", help="选卡界面助手（--yes；投递鼠标消息）")
    csub = chooser.add_subparsers(dest="chooser_cmd", required=True)
    for name, hlp in (("clear", "clear the seed bank"), ("pick", "pick cards: pick 1 16 14 ..."),
                      ("start", "click Let's Rock (bank must be full)"), ("select", "clear + pick + start")):
        q = csub.add_parser(name, help=hlp)
        if name in ("pick", "select"):
            q.add_argument("types", nargs="+", help="plant ids / names")
        if name in ("start", "select"):
            q.add_argument("--wait", type=float, default=5.0, help="seconds to wait for the level (default 5)")
        q.add_argument("--yes", "-y", action="store_true", help=_("help_yes"))
        q.add_argument("--pid", type=int, help=_("help_pid"))
        q.set_defaults(func=cmd_chooser)

    window = sub.add_parser("window", help="游戏窗口：info | front（拉前台）| calibrate（鼠标标定）")
    wsub = window.add_subparsers(dest="window_cmd", required=True)
    for name, hlp in (("info", "client size / DPI / foreground state"), ("front", "bring the game window to the foreground"),
                      ("calibrate", "measure the click -> Board.prev_mouse mapping")):
        q = wsub.add_parser(name, help=hlp)
        q.add_argument("--yes", "-y", action="store_true", help=_("help_yes"))
        q.add_argument("--pid", type=int, help=_("help_pid"))
        q.set_defaults(func=cmd_window)

    p = sub.add_parser("reactive", help="反应式执行：读状态决定发炮（--policy p6 --cobs 29,59；--input | --remote，--yes）")
    p.add_argument("--policy", default="p6", help="p4|p5|p6|p7|p8 (PSeriesPolicy; default p6)")
    p.add_argument("--cobs", default=None, help="cob landing cells <row><col>,... e.g. 29,59 (default: rows 2/5 col 9)")
    p.add_argument("--waves", type=_positive_int, default=20, help="act on this many waves (default 20)")
    p.add_argument("--poll-ms", type=int, default=5, help="poll interval in ms (default 5)")
    p.add_argument("--on-error", default="warn", choices=("warn", "abort"))
    p.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    _executor_flags(p)
    p.set_defaults(func=cmd_reactive)

    p = sub.add_parser("bench-clock", help="实测帧钟：轮询延迟 + 命中目标 tick 的抖动（只读，需在关卡中）")
    p.add_argument("--seconds", type=float, default=10.0, help="wall seconds to measure (default 10)")
    p.add_argument("--step", type=int, default=50, help="target distance in cs (default 50)")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    p.add_argument("--pid", type=int, help=_("help_pid"))
    p.set_defaults(func=cmd_bench_clock)

    p = sub.add_parser("cobs", help="列出玉米加农炮（就绪 / 剩余冷却；只读）")
    p.add_argument("--pid", type=int, help=_("help_pid"))
    p.set_defaults(func=cmd_cobs)

    p = sub.add_parser("fire", help="发炮（--remote --yes）：fire <cobRow> <cobCol> <row> <col>")
    p.add_argument("cob_row", type=int, help="cannon row 1..6")
    p.add_argument("cob_col", type=int, help="cannon LEFT column 1..8")
    p.add_argument("row", type=int, help="target row 1..6")
    p.add_argument("col", type=float, help="target column (fractional ok, e.g. 8.75)")
    p.add_argument("--force", action="store_true", help="fire even if the cannon is not READY (state 37)")
    _remote_flags(p)
    p.set_defaults(func=cmd_fire)

    mod = sub.add_parser("mod", help="修改器（默认只读：需要 --write / --patch 和 --yes）")
    msub = mod.add_subparsers(dest="action", metavar="<action>")
    msub.required = True

    def _mod_flags(q: argparse.ArgumentParser) -> None:
        q.add_argument("--write", action="store_true", help=_("help_write"))
        q.add_argument("--patch", action="store_true", help=_("help_patch"))
        q.add_argument("--yes", "-y", action="store_true", help=_("help_yes"))
        q.add_argument("--pid", type=int, help=_("help_pid"))

    q = msub.add_parser("sun", help="set sun (0..9990)")
    q.add_argument("value", type=int)
    _mod_flags(q)
    q = msub.add_parser("speed", help="speed multiplier (1 = normal, 2 = 2x, 0.5 = half; same as cheats.speed)")
    q.add_argument("value", type=float)
    _mod_flags(q)
    for name, hlp in (("no-cooldown", "finish card cooldowns (one pass)"),
                      ("free-planting", "cards cost nothing"),
                      ("sun-limit", "lift the 9990 sun cap (code patch)"),
                      ("auto-collect", "auto collect sun/coins (code patch)"),
                      ("background", "keep running in background (code patch)")):
        q = msub.add_parser(name, help=hlp)
        q.add_argument("on", type=_on, help="on|off")
        _mod_flags(q)
    q = msub.add_parser("kill-zombies", help="mark every zombie as dying")
    _mod_flags(q)
    mod.set_defaults(func=cmd_mod)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point; returns the process exit code (0 ok, 1 error, 2 lint errors)."""
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, LookupError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except _memory_errors() as exc:  # NotSupported / NotImplementedAction outside _run_memory
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_GAME


if __name__ == "__main__":
    raise SystemExit(main())
