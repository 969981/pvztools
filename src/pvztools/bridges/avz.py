"""Track -> AvZ 2 DSL (C++) exporter.

Reference: docs/research/seml-avz-grammar.md §2 (``inc/dsl/main.h`` and
``inc/dsl/shorthand_240713.h``) and docs/track-format.md §8. One-way only.

Every DSL operation is effect-time based (the library subtracts the
effect offset itself), which is exactly our ``effect_cs`` axis, so times
are copied verbatim: ``At(225_cs) PP(9)``. Tracks with
``timing_base == "operation"`` are rejected because the DSL has no
operation-axis form.

Output shape::

    #include <avz.h>

    void AScript() {
        ASelectCards("IINAW LPccc");
        ASetZombies("...");

        OnWave(1_8) {
            At(225_cs) PP(9),
            At(539_cs) C(80, 1256, 9),
            At(401_cs) Trig(),
        };
    }
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..schema.track.ast import (
    KIND_ASH,
    KIND_COB,
    KIND_DOOM,
    KIND_FODDER,
    KIND_ICE,
    KIND_NATURAL,
    Op,
    Track,
    Wave,
)

__all__ = ["to_avz_dsl", "format_wave_list", "DEFAULT_CTX"]

#: Defaults for the optional ``ctx`` argument of ``to_avz_dsl``.
DEFAULT_CTX: Dict[str, Any] = {
    "cob_col": 9,  # column for cob ops without a target (AvZ ``PP(col=9)``)
    "fodder_delay": 134,  # ``C(delay)`` shovel delay for soft fodder without a window
    "ice_pos": None,  # (row, col) for ice ops without a target; None -> ``I()`` ice filler
    "trig": True,  # emit ``Trig()`` from ``Wave.length_cs``
}

_TRIG_LEAD = 200  # AvZ ``Trig()``: wavelength = max(time + 200, 601)
_ASH_FUNC = {"cherry": "A", "jalapeno": "J", "squash": "W", "light": "A"}


def format_wave_list(waves: Tuple[int, ...]) -> str:
    """Compress wave numbers into AvZ selector literals (§2 "Wave selector
    literals"): ``1_8`` for step-1 runs, ``1_17_step4`` for longer fixed-step
    runs, plain ints otherwise, joined with commas (``AWave(...)`` varargs)."""
    nums = sorted(set(waves))
    parts: List[str] = []
    i = 0
    n = len(nums)
    while i < n:
        j = i + 1
        step = nums[j] - nums[i] if j < n else 0
        while j < n and nums[j] - nums[j - 1] == step:
            j += 1
        run = j - i
        if run >= 2 and step == 1:
            parts.append(f"{nums[i]}_{nums[j - 1]}")
            i = j
        elif run >= 3:
            parts.append(f"{nums[i]}_{nums[j - 1]}_step{step}")
            i = j
        else:
            parts.append(str(nums[i]))
            i += 1
    return ", ".join(parts)


def _rows_int(rows: Tuple[int, ...]) -> str:
    return "".join(str(r) for r in rows)


def _col(col: float) -> str:
    return str(int(col)) if float(col).is_integer() else repr(float(col))


def _cs(t: int) -> str:
    return f"{t}_cs"


class _Writer:
    def __init__(self, track: Track, ctx: Dict[str, Any]) -> None:
        self.track = track
        self.ctx = ctx
        self.nrows = 6 if track.scene in (None, "PE", "FE") else 5
        self.is_roof = track.scene in ("RE", "ME")
        self.notes: List[str] = []

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    # -- single op -> DSL expression ----------------------------------------

    def cob(self, op: Op) -> str:
        default_pair = (2, 5) if self.nrows == 6 else (2, 4)
        col_default = self.ctx["cob_col"]
        delay_pair = (1, 5) if self.nrows == 6 else (1, 4)
        template = "P<RECOVER_FIRE>" if op.variant == "replant" else "P"
        if op.target is None:
            if op.count >= 2:
                self.note(f"{op.symbol}: no target -> PP({col_default}) (rows {_rows_int(default_pair)})")
                return f"PP({col_default})"
            self.note(f"{op.symbol}: no target -> row {default_pair[0]} col {col_default}")
            return f"{template}({default_pair[0]}, {col_default})"
        rows, col = op.target.rows, op.target.col
        if self.is_roof:
            self.note(f"{op.symbol}: roof scene; cob tail column left to ARoofCobManager")
        if op.variant == "delay" and rows == delay_pair and template == "P":
            return f"DD({_col(col)})" if len(rows) == 2 else f"D({_rows_int(rows)}, {_col(col)})"
        if rows == default_pair and template == "P" and op.count == 2:
            return f"PP({_col(col)})"
        return f"{template}({_rows_int(rows)}, {_col(col)})"

    def card(self, op: Op) -> str:
        """ash / doom / ice: one call per target row, joined with ``&``."""
        if op.kind == KIND_ASH:
            func = _ASH_FUNC.get(op.variant or "cherry", "A")
            if op.variant == "light":
                self.note(f"{op.symbol}: ambiguous 'light' ash exported as cherry A()")
            extra = ""
        elif op.kind == KIND_DOOM:
            func = "N"
            extra = ", true" if op.variant == "imitater" else ""
        else:
            func = "I"
            extra = ", true" if op.variant == "imitater" else ""
            if op.variant and op.variant.startswith("ice") and op.variant != "imitater":
                self.note(f"{op.symbol}: ice slot {op.variant!r} not modelled; plain I() used")
        if op.target is None:
            if op.kind == KIND_ICE:
                pos = self.ctx.get("ice_pos")
                if pos:
                    return f"I({pos[0]}, {pos[1]}{extra})"
                self.note(f"{op.symbol}: no target -> I() (needs an ice filler, day scenes only)")
                return "I()"
            self.note(f"{op.symbol}: no target; row/col must be filled in by hand")
            return f"{func}(/* row */ 0, /* col */ 0{extra})"
        col = _col(op.target.col)
        return " & ".join(f"{func}({row}, {col}{extra})" for row in op.target.rows)

    def fodder(self, op: Op) -> str:
        if op.window_cs is not None:
            delay = op.window_cs[1] - op.window_cs[0]
        elif op.variant == "hard":
            delay = -1  # keep hard fodder (no shovel)
        else:
            delay = self.ctx["fodder_delay"]
        if op.target is None:
            return f"C({delay})"
        return f"C({delay}, {_rows_int(op.target.rows)}, {_col(op.target.col)})"

    def op_expr(self, op: Op) -> Optional[str]:
        if op.kind == KIND_COB:
            return self.cob(op)
        if op.kind in (KIND_ASH, KIND_DOOM, KIND_ICE):
            return self.card(op)
        if op.kind == KIND_FODDER:
            return self.fodder(op)
        if op.kind == KIND_NATURAL:
            return None
        self.note(f"{op.symbol}: unknown op kind {op.kind!r} skipped")
        return None

    # -- wave --------------------------------------------------------------

    def wave_lines(self, wave: Wave, indent: str) -> List[str]:
        """Lines inside ``OnWave(...) { ... }``: one ``At(t) expr,`` per
        time group (``&``-joined ops share an ``At``)."""
        ops = list(wave.top.ops) + (list(wave.bottom.ops) if wave.bottom is not None else [])
        groups: List[Tuple[int, List[str]]] = []
        prev_time: Optional[int] = None
        for op in ops:
            if op.effect_cs is None:
                raise ValueError(f"op {op.symbol!r} in wave {wave.selector} has no time; expand the track first")
            if op.relative:
                if prev_time is None:
                    raise ValueError(f"relative time +{op.effect_cs} on {op.symbol!r} has no previous op")
                time = prev_time + op.effect_cs
            elif op.window_cs is not None:
                time = op.window_cs[0]
            else:
                time = op.effect_cs
            prev_time = time
            expr = self.op_expr(op)
            if expr is None:
                continue
            if op.simultaneous_with_prev and groups and groups[-1][0] == time:
                groups[-1][1].append(expr)
            else:
                groups.append((time, [expr]))
        lines = [f"{indent}At({_cs(t)}) {' & '.join(exprs)}," for t, exprs in groups]
        if self.ctx["trig"] and wave.length_cs is not None:
            lines.append(f"{indent}At({_cs(max(wave.length_cs - _TRIG_LEAD, 0))}) Trig(),")
        return lines

    def run(self) -> str:
        t = self.track
        if t.kind != "pvzt":
            raise ValueError(
                f"to_avz_dsl needs a per-wave track (kind 'pvzt'), got {t.kind!r}; shorthand has no timings"
            )
        if t.timing_base != "effect":
            raise ValueError("to_avz_dsl needs timing_base 'effect'; the AvZ DSL has no operation-axis form")
        body: List[str] = []
        ind = "    "
        if t.cards is not None:
            body.append(f'{ind}ASelectCards("{t.cards}");')
        if t.zombies is not None:
            body.append(f'{ind}ASetZombies("{t.zombies}");')
        if t.protect:
            body.append(f"{ind}// protect: {' '.join(str(p) for p in t.protect)}")
        if t.lineup_code is not None:
            body.append(f"{ind}// lineup: {t.lineup_code}")
        for wave in t.waves:
            body.append("")
            body.append(f"{ind}OnWave({format_wave_list(wave.selector)}) {{")
            body.extend(self.wave_lines(wave, ind * 2))
            body.append(f"{ind}}};")
        head = ["#include <avz.h>", ""]
        if t.scene is not None:
            head.append(f"// scene: {t.scene}")
        rhythm = t.meta.get("rhythm_raw") or (t.rhythm.raw if t.rhythm is not None else None)
        if rhythm:
            head.append(f"// rhythm: {rhythm}")
        for n in self.notes:
            head.append(f"// NOTE: {n}")
        head.append("")
        return "\n".join(head + ["void AScript() {"] + body + ["}"]) + "\n"


def to_avz_dsl(track: Track, ctx: Optional[Dict[str, Any]] = None) -> str:
    """Render a ``pvzt``-kind ``Track`` as an AvZ 2 DSL C++ script (§2).

    ``ctx`` overrides ``DEFAULT_CTX`` keys: ``cob_col``, ``fodder_delay``,
    ``ice_pos``, ``trig``. Mapping: cob -> ``PP(col)`` / ``DD(col)`` when
    the rows are the scene defaults, else ``P(rows, col)``; ash ->
    ``A/J/W(row, col)``; doom -> ``N(row, col[, true])``; ice ->
    ``I(row, col)`` or ``I()``; fodder -> ``C(delay, rows, col)`` with
    ``delay`` = window width, -1 for hard fodder; ``length_cs`` ->
    ``At(len-200_cs) Trig()``. Relative ``+N`` times are resolved against
    the previous op. Defaults chosen for missing targets are listed as
    ``// NOTE:`` lines. Braces are always balanced.

    Raises ``ValueError`` for shorthand tracks or ``timing_base != "effect"``.
    """
    merged = dict(DEFAULT_CTX)
    if ctx:
        merged.update(ctx)
    return _Writer(track, merged).run()
