"""Execution report for the planned executor (DESIGN §6: planned / observed / delta).

Mode labels follow the AvZ community convention of separating 键控 (key
control: inputs or direct cob firing only) from 修改器 (modifier: free
planting via remote ``PutPlant``); a run that used both remote planting and
input-driven actions is 混合.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["ActionRow", "PauseRecord", "JitterStats", "ExecutionReport", "mode_label", "jitter_stats",
           "abs_stats", "STATUS_CD_WAIT", "STATUS_LABELS", "status_label"]

#: row status: the cannon was not ready at the planned tick; fired when ready
STATUS_CD_WAIT = "cd-wait"

#: row status -> Chinese label used by :meth:`ExecutionReport.to_markdown`
STATUS_LABELS = {
    "ok": "命中", "late": "晚", STATUS_CD_WAIT: "CD等待", "skipped": "跳过", "failed": "失败",
    "aborted": "中止", "dry-run": "演练", "pending": "未到",
}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


MODE_INPUT = "键控"
MODE_MODIFIER = "修改器"
MODE_MIXED = "混合"


def mode_label(backend: Any, remote_plant_used: bool, input_used: bool) -> str:
    """'键控' for input / remote-cob-only, '修改器' for remote plant_card, '混合' for both."""
    if remote_plant_used and input_used:
        return MODE_MIXED
    if remote_plant_used:
        return MODE_MODIFIER
    return MODE_INPUT


@dataclass
class ActionRow:
    wave: int
    symbol: str
    kind: str
    planned_cs: int
    detail: str = ""
    observed_cs: Optional[int] = None  # estimated action tick (dispatch + backend lag)
    delta: Optional[int] = None
    status: str = "pending"
    note: str = ""
    done_cs: Optional[int] = None  # wave time when the backend call returned
    # frame lock (DESIGN §6.3, remote cob groups): wave time the game was
    # parked on while the call ran, the true fire tick read back from
    # ``mShootingCounter`` (delta is computed from it when present) and the
    # number of block / release cycles the lock needed
    frozen_cs: Optional[int] = None
    fired_cs: Optional[int] = None
    attempts: Optional[int] = None
    # cannon not READY at the planned tick: the executor waited ``cd_wait_cs``
    # ticks for it (status ``cd-wait``) -- reported apart from timing error
    cd_wait_cs: Optional[int] = None
    #: wall milliseconds the backend call(s) of this row's group took
    #: (remote call / frame lock round trip); ``None`` in dry run
    call_ms: Optional[float] = None


@dataclass
class PauseRecord:
    clock: Optional[int]
    wave: Optional[int]
    wall_seconds: float


@dataclass
class JitterStats:
    count: int = 0
    mean: Optional[float] = None
    p95: Optional[float] = None
    max: Optional[int] = None
    min: Optional[int] = None


def jitter_stats(deltas: List[int]) -> JitterStats:
    if not deltas:
        return JitterStats()
    s = sorted(deltas)
    n = len(s)
    # nearest-rank p95
    rank = max(1, int(math.ceil(0.95 * n)))
    return JitterStats(n, sum(s) / n, float(s[rank - 1]), s[-1], s[0])


def abs_stats(deltas: List[int]) -> JitterStats:
    """Statistics of ``|delta|`` (timing error magnitude)."""
    return jitter_stats([abs(int(d)) for d in deltas])


@dataclass
class ExecutionReport:
    mode: str
    backend: str
    dry_run: bool = False
    tolerance_cs: int = 2
    late_policy: str = "warn"
    plan_actions: int = 0
    plan_issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)  # e.g. mid-wave start
    rows: List[ActionRow] = field(default_factory=list)
    refresh_points: Dict[int, int] = field(default_factory=dict)  # wave -> main_counter at refresh
    pauses: List[PauseRecord] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    skipped: int = 0
    late: int = 0
    stop_reason: str = ""
    started_wall: Optional[float] = None
    finished_wall: Optional[float] = None
    #: autocollect summary (``AutoCollector.stats.summary()``) when a
    #: collector ran alongside the plan: collected / calls / sun / by_type ...
    collected: Optional[Dict[str, Any]] = None
    #: free-form context set by the orchestrator (``PlaySession``): version,
    #: profile, lineup, track, scene, waves, level, steps (phase log) ...
    meta: Dict[str, Any] = field(default_factory=dict)

    # --- derived -------------------------------------------------------------
    @property
    def deltas(self) -> List[int]:
        return [r.delta for r in self.rows if r.delta is not None and r.status in ("ok", "late", "dry-run")]

    @property
    def jitter(self) -> JitterStats:
        return jitter_stats(self.deltas)

    @property
    def abs_error(self) -> JitterStats:
        """mean / p95 / max of ``|delta|`` over the timed rows (cd-waits excluded)."""
        return abs_stats(self.deltas)

    @property
    def cd_wait_rows(self) -> List[ActionRow]:
        return [r for r in self.rows if r.status == STATUS_CD_WAIT]

    @property
    def cd_waits(self) -> int:
        """Rows whose cannon was not ready at the planned tick (fired later)."""
        return len(self.cd_wait_rows)

    @property
    def cd_wait_stats(self) -> JitterStats:
        return jitter_stats([int(r.cd_wait_cs) for r in self.cd_wait_rows if r.cd_wait_cs is not None])

    @property
    def frame_locked(self) -> int:
        """Rows with a read-back fire tick (``fired_cs``)."""
        return sum(1 for r in self.rows if r.fired_cs is not None)

    @property
    def call_ms_stats(self) -> JitterStats:
        """mean / p95 / max of the backend call wall time (ms) per row."""
        vals = [float(r.call_ms) for r in self.rows if r.call_ms is not None]
        if not vals:
            return JitterStats()
        s = sorted(vals)
        n = len(s)
        rank = max(1, int(math.ceil(0.95 * n)))
        return JitterStats(n, sum(s) / n, s[rank - 1], s[-1], s[0])

    @property
    def dispatched(self) -> int:
        return sum(1 for r in self.rows if r.status in ("ok", "late", "dry-run", STATUS_CD_WAIT))

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r.status == "failed")

    def summary(self) -> Dict[str, Any]:
        j = self.jitter
        return {
            "mode": self.mode,
            "backend": self.backend,
            "dry_run": self.dry_run,
            "tolerance_cs": self.tolerance_cs,
            "late_policy": self.late_policy,
            "plan_actions": self.plan_actions,
            "plan_issues": list(self.plan_issues),
            "warnings": list(self.warnings),
            "dispatched": self.dispatched,
            "skipped": self.skipped,
            "late": self.late,
            "failed": self.failed,
            "pauses": len(self.pauses),
            "stop_reason": self.stop_reason,
            "wall_seconds": (self.finished_wall - self.started_wall) if self.started_wall and self.finished_wall else None,
            "jitter": asdict(j),
            "abs_error": asdict(self.abs_error),
            "cd_waits": self.cd_waits,
            "cd_wait": asdict(self.cd_wait_stats),
            "frame_locked": self.frame_locked,
            "collected": dict(self.collected) if self.collected else None,
            "call_ms": asdict(self.call_ms_stats),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary(),
            "rows": [asdict(r) for r in self.rows],
            "refresh_points": {str(k): v for k, v in sorted(self.refresh_points.items())},
            "pauses": [asdict(p) for p in self.pauses],
            "events": list(self.events),
            "meta": dict(self.meta),
        }

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def to_text(self) -> str:
        """Compact table: wave, symbol, kind, planned, frozen, fired, delta,
        attempts, status (``observed`` = the estimated action tick when no
        fire tick could be read back)."""
        s = self.summary()
        j = s["jitter"]
        lines = [
            "mode=%s backend=%s%s policy=%s tol=%dcs" % (s["mode"], s["backend"], " (dry-run)" if self.dry_run else "", s["late_policy"], s["tolerance_cs"]),
            "actions=%d dispatched=%d late=%d cd-waits=%d skipped=%d failed=%d pauses=%d stop=%s" % (
                s["plan_actions"], s["dispatched"], s["late"], s["cd_waits"], s["skipped"], s["failed"], s["pauses"], s["stop_reason"]),
        ]
        for w in self.warnings:
            lines.append("warning: %s" % w)
        if j["count"]:
            a = s["abs_error"]
            lines.append("jitter: n=%d mean=%.2f p95=%.0f max=%d min=%d | abs: mean=%.2f p95=%.0f max=%d | frame-locked=%d"
                         % (j["count"], j["mean"], j["p95"], j["max"], j["min"], a["mean"], a["p95"], a["max"], s["frame_locked"]))
        if s["cd_waits"]:
            c = s["cd_wait"]
            lines.append("cd-wait: n=%d (cannon not ready at the planned tick) wait mean=%.0f p95=%.0f max=%dcs"
                         % (c["count"], c["mean"] or 0, c["p95"] or 0, c["max"] or 0))
        if self.collected:
            c = self.collected
            lines.append("collected: %d coin(s) in %d call(s), sun~%d money~%d %s" % (
                c.get("collected", 0), c.get("calls", 0), c.get("sun", 0), c.get("money", 0),
                " ".join("%s=%d" % kv for kv in (c.get("by_type") or {}).items())))
        if self.refresh_points:
            lines.append("refresh: " + " ".join("w%d@%d" % (w, c) for w, c in sorted(self.refresh_points.items())))
        hdr = "%4s %-6s %-7s %8s %8s %8s %6s %3s %-8s %s" % ("wave", "sym", "kind", "planned", "frozen", "fired", "delta", "att", "status", "note")
        lines.append(hdr)

        def cell(v: Optional[int]) -> str:
            return "-" if v is None else str(v)

        for r in self.rows:
            # ``fired``: read back from the game; ``~N`` = estimated
            # (dispatch / frozen tick) when no read-back exists
            fired = cell(r.fired_cs) if r.fired_cs is not None else (
                "-" if r.observed_cs is None else "~%d" % r.observed_cs)
            lines.append(
                "%4d %-6s %-7s %8d %8s %8s %6s %3s %-8s %s"
                % (r.wave, r.symbol, r.kind, r.planned_cs, cell(r.frozen_cs), fired,
                   "-" if r.delta is None else "%+d" % r.delta, cell(r.attempts), r.status, r.note)
            )
        return "\n".join(lines)

    # --- Markdown (pvztools play --md) --------------------------------------
    def to_markdown(self) -> str:
        """Chinese headings with English labels: header, timing table,
        summary, auto-collect stats, notes.  Context (version / lineup /
        track / scene / waves / steps) comes from :attr:`meta`."""
        m = self.meta or {}
        s = self.summary()

        def g(key: str, default: Any = "-") -> Any:
            v = m.get(key)
            return default if v in (None, "") else v

        def num(v: Optional[float], fmt: str = "%.2f") -> str:
            return "-" if v is None else fmt % v

        def cell(v: Optional[Any]) -> str:
            return "-" if v is None else str(v)

        lines: List[str] = []
        lines.append("# 打关报告 (Play report)")
        lines.append("")
        lines.append("## 概要 (Header)")
        lines.append("")
        lines.append("| 项目 (item) | 值 (value) |")
        lines.append("|---|---|")
        lines.append("| 版本 / profile (version) | %s / %s |" % (g("version"), g("profile")))
        lines.append("| 后端 (backend) | %s%s |" % (self.backend, " (dry-run)" if self.dry_run else ""))
        lines.append("| 模式 (mode) | %s |" % (self.mode or "-"))
        lines.append("| 阵型 (lineup) | %s |" % g("lineup"))
        lines.append("| 轨道 (track) | %s |" % g("track"))
        lines.append("| 场景 (scene) | %s |" % g("scene"))
        lines.append("| 关卡 (level) | %s |" % g("level"))
        lines.append("| 波数 (waves) | %s |" % g("waves"))
        lines.append("| 炮落点列 (cob col) | %s |" % g("cob_col"))
        lines.append("| 容差 / 迟到策略 (tolerance / late policy) | %d cs / %s |" % (self.tolerance_cs, self.late_policy))
        lines.append("| 结束原因 (stop reason) | %s |" % (self.stop_reason or "-"))
        ws = s.get("wall_seconds")
        lines.append("| 用时 (wall) | %s |" % ("-" if ws is None else "%.1f s" % ws))
        lines.append("")
        lines.append("## 时序表 (Timing)")
        lines.append("")
        lines.append("| 波 (wave) | 符号 (symbol) | 计划 cs (planned) | 实际 cs (fired, true tick) | 偏差 (delta) | 状态 (status) | 备注 (note) |")
        lines.append("|---:|---|---:|---:|---:|---|---|")
        for r in self.rows:
            if r.fired_cs is not None:
                fired = str(r.fired_cs)
            elif r.observed_cs is not None:
                fired = "~%d" % r.observed_cs
            else:
                fired = "-"
            delta = "-" if r.delta is None else "%+d" % r.delta
            note = r.note or ""
            if r.status == STATUS_CD_WAIT and r.cd_wait_cs is not None:
                note = "等待 %d cs (waited)" % r.cd_wait_cs + ("; " + note if note else "")
            lines.append("| %d | %s | %d | %s | %s | %s | %s |" % (
                r.wave, r.symbol, r.planned_cs, fired, delta, status_label(r.status), note.replace("|", "/")))
        lines.append("")
        lines.append("## 统计 (Summary)")
        lines.append("")
        a = self.abs_error
        hits = sum(1 for r in self.rows if r.status == "ok")
        fires = sum(1 for r in self.rows if r.kind == "cob" and r.status in ("ok", "late", STATUS_CD_WAIT))
        lines.append("- 发炮 / 动作 (fires / actions): %d 炮 (fires)，%d 行 (rows)，计划 (planned) %d，派发 (dispatched) %d，"
                     "命中 (hit) %d，晚 (late) %d，跳过 (skipped) %d，失败 (failed) %d"
                     % (fires, len(self.rows), self.plan_actions, self.dispatched, hits, self.late, self.skipped, self.failed))
        lines.append("- 时序误差 |delta| 命中行 (timing error over hit rows): n=%d mean=%s p95=%s max=%s cs; 定帧 (frame-locked) %d"
                     % (a.count, num(a.mean), num(a.p95, "%.0f"), cell(a.max), self.frame_locked))
        c = self.cd_wait_stats
        lines.append("- CD 等待 (cd-wait, fired when ready): %d 次 (count); 等待 mean=%s p95=%s max=%s cs"
                     % (self.cd_waits, num(c.mean, "%.0f"), num(c.p95, "%.0f"), cell(c.max)))
        cm = self.call_ms_stats
        lines.append("- 远程调用耗时 (remote call ms): n=%d mean=%s max=%s ms" % (cm.count, num(cm.mean, "%.1f"), num(cm.max, "%.1f")))
        if self.refresh_points:
            lines.append("- 刷新点 (refresh points, main_counter): " + ", ".join(
                "w%d@%d" % (w, t) for w, t in sorted(self.refresh_points.items())))
        else:
            lines.append("- 刷新点 (refresh points): -")
        if self.pauses:
            lines.append("- 暂停 (pauses): %d 次, 共 %.1f s" % (len(self.pauses), sum(p.wall_seconds for p in self.pauses)))
        else:
            lines.append("- 暂停 (pauses): 0")
        lines.append("")
        lines.append("## 自动收集 (Auto-collect)")
        lines.append("")
        if self.collected:
            cd = self.collected
            by = cd.get("by_type") or {}
            others = sum(int(v) for k, v in by.items()
                         if str(k) not in ("SUN", "SMALL_SUN", "LARGE_SUN", "SILVER", "GOLD", "DIAMOND"))
            lines.append("- 阳光 (sun): %s" % cd.get("sun", 0))
            lines.append("- 金币 (coins, money value): %s" % cd.get("money", 0))
            lines.append("- 其他 (others: awards / packets / chocolate ...): %d" % others)
            lines.append("- 收集次数 / 调用 (collected / calls): %s / %s; 错误 (errors) %s" % (
                cd.get("collected", 0), cd.get("calls", 0), cd.get("errors", 0)))
            if by:
                lines.append("- 按类型 (by type): " + ", ".join("%s=%s" % kv for kv in sorted(by.items())))
        else:
            lines.append("- 未启用 (disabled)")
        lines.append("")
        lines.append("## 备注 (Notes)")
        lines.append("")
        notes: List[str] = []
        for st in m.get("steps") or []:
            if isinstance(st, dict):
                notes.append("%s: %s" % (st.get("name"), st.get("result")))
        notes.extend(str(w) for w in self.warnings)
        notes.extend(str(i) for i in self.plan_issues)
        for ev in self.events:
            if isinstance(ev, dict) and ev.get("event") == "error":
                notes.append("error (%s): %s" % (ev.get("kind"), ev.get("error")))
        for n in m.get("notes") or []:
            notes.append(str(n))
        if notes:
            lines.extend("- " + n for n in notes)
        else:
            lines.append("- 无 (none)")
        lines.append("")
        return "\n".join(lines)

    __str__ = to_text
