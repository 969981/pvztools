"""GUI 工作线程：所有 pvztools 调用都收拢到这一条线程上。

pvztools 库内部没有任何线程锁（单控制者模型），所以 GUI 采用
命令队列模式：UI 线程只读取 `worker.snapshot`（整体替换，读写均为
原子引用），一切 attach / 读内存 / 写内存 / 长任务都由本线程串行执行。

长任务（campaign / play_level 等）通过注入的协作式 sleep 支持
"停止"：stop_current() 置位事件后，下一次 sleep 即抛出 CancelledOp。
长任务开始前会先发布 busy 快照（UI 据此禁用按钮、显示停止键），
执行中的进度通过 _emit 直接刷新快照。
本模块不 import imgui，可在无 GUI 环境下单元测试。
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Deque, Dict, Optional, Tuple

__all__ = ["CancelledOp", "Snapshot", "GameWorker"]

_SCAN_INTERVAL_S = 2.0
_SCAN_BACKOFF_MAX_S = 30.0
_POLL_INTERVAL_S = 0.10
_LOG_MAX = 400

# 这些命令会阻塞工作线程较久：执行前发布 busy 快照
_LONG_OPS = frozenset({"place_lineup", "play_level", "campaign", "run_track",
                       "download_sprites", "import_lineup", "place_custom"})


class CancelledOp(Exception):
    """用户按下停止后，从协作式 sleep 里抛出以终止长任务。"""


@dataclass(frozen=True)
class Snapshot:
    """UI 线程每帧读取的游戏状态快照（不可变，整体替换）。"""

    attached: bool = False
    mode: str = "full"
    version: str = ""
    capability: str = ""
    pid: int = 0
    ui: int = 0
    ui_name: str = ""
    in_level: bool = False
    sun: int = 0
    wave: int = 0
    total_waves: int = 0
    clock: int = 0
    paused: bool = False
    cheats: Dict[str, Any] = field(default_factory=dict)
    busy: str = ""
    progress: str = ""
    candidates: Tuple[int, ...] = ()
    error: str = ""
    processes: Tuple[Tuple[int, str], ...] = ()
    addresses: Tuple[Tuple[str, str], ...] = ()


class GameWorker(threading.Thread):
    """单工作线程；UI 通过 submit() 投递命令，通过 .snapshot 读状态。"""

    def __init__(
        self,
        *,
        attach_fn: Optional[Callable[..., Any]] = None,
        find_fn: Optional[Callable[[], list]] = None,
        poll_interval: float = _POLL_INTERVAL_S,
        scan_interval: float = _SCAN_INTERVAL_S,
        auto_attach: bool = True,
        mode: str = "full",
    ) -> None:
        super().__init__(name="pvztools-gui-worker", daemon=True)
        self._attach_fn = attach_fn
        self._find_fn = find_fn
        self.poll_interval = poll_interval
        self.scan_interval = scan_interval
        self.auto_attach = auto_attach
        self.mode = mode
        #: GUI 悬浮模式下，全模式附加成功后自动把游戏切窗口（否则独占全屏盖不住）
        self.auto_windowize = False
        self.auto_background = False

        self.game: Any = None
        self.snapshot: Snapshot = Snapshot(mode=mode)
        self.log: Deque[Tuple[float, str]] = deque(maxlen=_LOG_MAX)

        self._cmds: "queue.Queue[Tuple[str, Dict[str, Any]]]" = queue.Queue()
        self._quit = threading.Event()
        self._stop_op = threading.Event()
        self._busy = ""
        self._progress = ""
        self._candidates: Tuple[int, ...] = ()
        self._processes: Tuple[Tuple[int, str], ...] = ()
        self._proc_scan_at = 0.0
        self._last_scan = 0.0
        self._scan_backoff = scan_interval
        self._last_error = ""
        self._attached_mode = mode
        # 会话内已设置的修改项（cheats.status() 在关卡外读不到部分键，
        # 用本地缓存兜底，避免勾选框在主菜单"自己弹回去"）
        self._cheat_cache: Dict[str, Any] = {}

    # ---------- UI 线程调用的接口 ----------

    def submit(self, cmd: str, /, **kwargs: Any) -> None:
        self._cmds.put((cmd, kwargs))

    def stop_current(self) -> None:
        """请求停止当前长任务（在下一个动作间隙生效）。"""
        self._stop_op.set()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop_op.set()
        self._quit.set()
        if self.is_alive():
            self.join(timeout)
        # 线程确认结束后补一次收尾：释放控制者互斥、回滚补丁
        if not self.is_alive() and self.game is not None:
            try:
                self.game.close()
            except Exception:  # noqa: BLE001
                pass
            self.game = None

    def log_lines(self) -> list:
        return list(self.log)

    def clear_log(self) -> None:
        self.log.clear()

    # ---------- 内部 ----------

    def _log(self, msg: str) -> None:
        self.log.append((time.time(), msg))

    def _publish_status(self) -> None:
        self.snapshot = replace(
            self.snapshot,
            busy=self._busy,
            progress=self._progress,
            error=self._last_error,
        )

    def _emit(self, event: Dict[str, Any]) -> None:
        kind = event.get("event", "?")
        parts = [f"{k}={v}" for k, v in event.items() if k != "event"]
        line = f"[{kind}] " + " ".join(str(p) for p in parts[:6])
        self._progress = line
        self._log(line)
        self._publish_status()  # 长任务执行中也让 UI 看到进度

    def _coop_sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            if self._stop_op.is_set() or self._quit.is_set():
                raise CancelledOp()
            remain = deadline - time.monotonic()
            if remain <= 0:
                return
            time.sleep(min(remain, 0.05))

    @staticmethod
    def _connection_lost(exc: BaseException) -> bool:
        """只有进程级错误才算断线；NotInLevel 等游戏状态错误不算。"""
        if isinstance(exc, OSError):
            return True
        try:
            from pvztools.process import ProcessError

            return isinstance(exc, ProcessError)
        except Exception:  # noqa: BLE001
            return False

    def _resolve_attach(self) -> Callable[..., Any]:
        if self._attach_fn is not None:
            return self._attach_fn
        import pvztools

        return pvztools.attach

    def _resolve_find(self) -> Callable[[], list]:
        if self._find_fn is not None:
            return self._find_fn
        from pvztools.process import find_game_processes

        return find_game_processes

    # ---------- 主循环 ----------

    def run(self) -> None:  # pragma: no cover - 线程壳，逻辑在 tick()
        while not self._quit.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - 工作线程绝不允许死掉
                self._log(f"内部错误 — {type(exc).__name__}: {exc}")
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._publish_status()
                time.sleep(0.2)

    def tick(self) -> None:
        """处理最多一条命令并刷新一次快照（拆出来便于测试）。"""
        try:
            cmd = self._cmds.get(timeout=self.poll_interval)
        except queue.Empty:
            cmd = None
        if cmd is not None:
            self._run_cmd(cmd[0], cmd[1])
        self._refresh()

    def _run_cmd(self, name: str, kwargs: Dict[str, Any]) -> None:
        handler = getattr(self, "_cmd_" + name, None)
        if handler is None:
            self._log(f"未知命令: {name}")
            return
        if name in _LONG_OPS:
            self._busy = name
            self._progress = ""
            self._publish_status()  # 先让 UI 进入 busy 态（停止键可见、按钮禁用）
        try:
            handler(**kwargs)
            self._last_error = ""
        except CancelledOp:
            self._log(f"{name}: 已停止")
        except Exception as exc:  # noqa: BLE001 - 所有错误都进日志而不是崩 GUI
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._log(f"{name} 失败 — {self._last_error}")
        finally:
            if self._busy:
                self._busy = ""
                self._publish_status()  # 长任务结束立即回落 busy 态
            self._stop_op.clear()

    def _scan_processes(self) -> None:
        """常驻维护游戏进程列表（节流），供设置页进程选择器用。"""
        now = time.monotonic()
        if now - self._proc_scan_at < 1.5:
            return
        self._proc_scan_at = now
        try:
            procs = self._resolve_find()()
        except Exception:  # noqa: BLE001
            return
        self._processes = tuple(
            (int(getattr(p, "pid", p)), str(getattr(p, "name", "") or ""))
            for p in procs
        )

    def _refresh(self) -> None:
        self._scan_processes()
        if self.game is None:
            self._maybe_scan()
        if self.game is None:  # 扫描后仍未附加才发布"未连接"快照
            self.snapshot = Snapshot(
                mode=self.mode,
                candidates=self._candidates,
                processes=self._processes,
                busy=self._busy,
                progress=self._progress,
                error=self._last_error,
            )
            return
        if self._busy:
            # 长任务期间不并发读内存（库无锁）；保留旧快照，只更新进度。
            self._publish_status()
            return
        g = self.game
        try:
            in_level = bool(g.in_level)
            snap = Snapshot(
                attached=True,
                mode=self._attached_mode,
                version=str(g.version),
                capability=str(g.capability),
                pid=int(g.pid),
                ui=int(g.ui),
                ui_name=str(g.ui_name),
                in_level=in_level,
                busy=self._busy,
                progress=self._progress,
                error=self._last_error,
                processes=self._processes,
            )
        except Exception as exc:  # noqa: BLE001
            if self._connection_lost(exc):
                self._log(f"连接丢失（{type(exc).__name__}），已断开")
                self._detach(silent=True)
            return
        snap = replace(snap, cheats=self._read_cheats(g),
                       addresses=self._collect_addresses(g, in_level))
        if in_level:
            try:
                snap = replace(
                    snap,
                    sun=int(g.sun),
                    wave=int(g.wave),
                    total_waves=int(g.total_waves),
                    clock=int(g.clock),
                    paused=bool(g.paused),
                )
            except Exception as exc:  # noqa: BLE001
                if self._connection_lost(exc):
                    self._log(f"连接丢失（{type(exc).__name__}），已断开")
                    self._detach(silent=True)
                    return
                # 关卡刚退出等瞬态：退化为"不在关卡"，不要断开
                snap = replace(snap, in_level=False)
        self.snapshot = snap

    def _collect_addresses(self, g: Any, in_level: bool) -> Tuple[Tuple[str, str, str], ...]:
        """解析出的地址：以模块偏移指针链呈现（如 [[mod+0x..]+0x868]+0x5578）。

        每行 (名称, 指针链, 解析值)。非 ASLR，解析值即绝对地址。
        """
        rows: list = []

        def hx(v: int) -> str:
            return f"0x{v:X}"

        try:
            prof = g.profile
            base = int(getattr(prof, "image_base", 0) or 0)
            mod = str(getattr(g.session, "name", "") or "game.exe")
            lawn_va = int(prof.globals.lawn_app)
            lawn_rva = lawn_va - base
            board_off = int(prof.lawn_app.board)
            sun_off = int(prof.board.sun)
        except Exception:  # noqa: BLE001
            return tuple(rows)

        gchain = f"{mod}+{hx(lawn_rva)}"        # LawnApp 全局(指针所在处)
        lawn_chain = f"[{gchain}]"               # LawnApp 实例
        board_chain = f"[{lawn_chain}+{hx(board_off)}]"
        sun_chain = f"{board_chain}+{hx(sun_off)}"

        rows.append(("进程 pid", str(g.pid), ""))
        rows.append(("映像基址", f"{mod}", hx(base)))
        rows.append(("LawnApp 全局", gchain, hx(lawn_va)))

        try:
            lawn = int(g.observer.lawn_ptr())
            rows.append(("LawnApp 实例", lawn_chain, hx(lawn)))
            iw_off = getattr(getattr(prof, "lawn_app", None), "is_windowed", None)
            if iw_off is None:
                # 代码常量兜底（见 window._DISPLAY_OFFSETS）
                from pvztools.control.window import GameWindow
                iw_off = GameWindow._DISPLAY_OFFSETS.get(
                    str(getattr(prof, "id", "")), {}).get("is_windowed")
            if iw_off is not None and lawn:
                rows.append(("mIsWindowed", f"{lawn_chain}+{hx(int(iw_off))}",
                             hx(lawn + int(iw_off))))
        except Exception:  # noqa: BLE001
            pass

        if in_level:
            try:
                board = int(g.observer.board_ptr())
                rows.append(("Board 指针", board_chain, hx(board)))
                sun_val = ""
                try:
                    sun_val = f"= {int(g.sun)}"
                except Exception:  # noqa: BLE001
                    pass
                rows.append(("阳光 sun", sun_chain, f"{hx(board + sun_off)} {sun_val}".strip()))
            except Exception:  # noqa: BLE001
                pass

        try:
            fns = prof.functions
            for name in ("put_plant", "put_zombie", "delete_plant"):
                fn = fns.get(name)
                if fn is not None and getattr(fn, "rva", None) is not None:
                    rva = int(fn.rva)
                    rows.append((f"fn {name}", f"{mod}+{hx(rva)}", hx(base + rva)))
        except Exception:  # noqa: BLE001
            pass
        return tuple(rows)

    def _read_cheats(self, g: Any) -> Dict[str, Any]:
        read: Dict[str, Any] = {}
        try:
            read = dict(g.cheats.status())
        except Exception:  # noqa: BLE001 - 关卡外部分键读不到，不致命
            pass
        return {**self._cheat_cache, **read}

    def _maybe_scan(self) -> None:
        if not self.auto_attach:
            return
        now = time.monotonic()
        if now - self._last_scan < self._scan_backoff:
            return
        self._last_scan = now
        try:
            procs = self._resolve_find()()
        except Exception:  # noqa: BLE001
            return
        pids = tuple(int(getattr(p, "pid", p)) for p in procs)
        self._candidates = pids if len(pids) > 1 else ()
        if len(pids) == 1:
            # 走 _run_cmd：附加失败（ControllerBusy / 版本不支持 / 进程
            # 刚退出）只进日志，绝不让工作线程死掉；失败后退避重试。
            self._run_cmd("attach", {"pid": pids[0]})
            if self.game is None:
                self._scan_backoff = min(
                    max(self._scan_backoff * 2, 1.0), _SCAN_BACKOFF_MAX_S
                )
            else:
                self._scan_backoff = self.scan_interval

    # ---------- 命令 ----------

    def _cmd_attach(self, pid: Optional[int] = None, mode: Optional[str] = None) -> None:
        if self.game is not None:
            self._detach(silent=True)
        if mode:
            self.mode = mode
        attach = self._resolve_attach()
        if self.mode == "readonly":
            self.game = attach(pid, foreground=False)
        else:
            self.game = attach(
                pid, write=True, remote=True, input=True, foreground=False
            )
        self._attached_mode = self.mode
        self._candidates = ()
        self._cheat_cache.clear()
        self.auto_attach = True  # 手动/自动附加成功后恢复自动扫描
        self._scan_backoff = self.scan_interval
        self._log(
            f"已附加 pid={self.game.pid} 版本={self.game.version}"
            f" 能力={self.game.capability} 模式={self.mode}"
        )
        if self.auto_windowize and self.mode == "full":
            try:
                result = self.game.window.set_windowed(True)
                if result is True:
                    self._log("已自动窗口化（悬浮窗需要）")
            except Exception:  # noqa: BLE001 - 窗口化失败不致命
                pass
        if self.auto_background and self.mode == "full":
            try:
                self.game.cheats.background = True   # 面板拿焦点时游戏不暂停
                self._cheat_cache["background"] = True
                self._log("已开启后台运行（面板操作时游戏不暂停）")
            except Exception:  # noqa: BLE001
                pass

    def _cmd_detach(self) -> None:
        # 用户主动断开：同时停掉自动扫描，避免 2 秒后又自己连回去
        self.auto_attach = False
        self._detach()

    def _cmd_set_mode(self, mode: str) -> None:
        if mode in ("full", "readonly"):
            self.mode = mode
            self._log(f"模式已选择 {mode}（重新附加后生效）")

    def _detach(self, silent: bool = False) -> None:
        g, self.game = self.game, None
        if g is not None:
            try:
                g.close()
            except Exception:  # noqa: BLE001
                pass
            if not silent:
                self._log("已断开")
        self._cheat_cache.clear()
        self.snapshot = Snapshot(mode=self.mode)

    def _need_game(self) -> Any:
        if self.game is None:
            raise RuntimeError("尚未连接游戏")
        return self.game

    def _cmd_set_sun(self, value: int) -> None:
        self._need_game().sun = int(value)
        self._log(f"阳光 -> {int(value)}")

    def _cmd_toggle(self, name: str, value: bool) -> None:
        setattr(self._need_game().cheats, name, bool(value))
        self._cheat_cache[name] = bool(value)
        self._log(f"{name} -> {'开' if value else '关'}")

    def _cmd_speed(self, value: float) -> None:
        self._need_game().cheats.speed = float(value)
        self._cheat_cache["speed"] = float(value)
        self._log(f"速度 -> {float(value):.1f}x")

    def _cmd_kill_zombies(self) -> None:
        n = self._need_game().cheats.kill_zombies()
        self._log(f"清场僵尸 x{n}")

    def _cmd_collect_now(self) -> None:
        n = self._need_game().collect_all()
        self._log(f"收取掉落 x{n}")

    def _cmd_to_front(self) -> None:
        ok = self._need_game().window.bring_to_foreground()
        self._log("游戏已置前" if ok else "置前失败")

    def _cmd_set_windowed(self, want: bool) -> None:
        """切换游戏全屏/窗口（安全的 Alt+Enter 路径，非注入）。"""
        result = self._need_game().window.set_windowed(bool(want))
        if result is None:
            self._log("显示模式切换不可用（该版本未映射或不可切）")
        elif result == bool(want):
            self._log("游戏已" + ("窗口化" if want else "全屏"))
        else:
            self._log("显示模式切换未生效（游戏主循环未运行？）")

    def _cmd_place_lineup(self, index: Optional[int] = None, name: Optional[str] = None,
                          lineup: Optional[Any] = None) -> None:
        target = lineup if lineup is not None else (index if index is not None else name)
        summary = self._need_game().place_lineup(target)
        self._log(f"放置阵型 {target}: {summary}")

    def _cmd_play_level(self, index: Optional[int] = None,
                        lineup: Optional[Any] = None) -> None:
        kwargs: Dict[str, Any] = {
            "on_event": self._emit,
            "quiet": True,
            "sleep": self._coop_sleep,
        }
        target = lineup if lineup is not None else index
        if target is not None:
            kwargs["lineup"] = target
        report = self._need_game().play_level(**kwargs)
        self._log(f"打关结束: {report.summary()}")

    def _cmd_campaign(self, n: int = 1, auto: bool = False) -> None:
        report = self._need_game().campaign(
            n=int(n),
            auto=bool(auto),
            on_event=self._emit,
            quiet=True,
            sleep=self._coop_sleep,
        )
        self._log(
            f"冒险结束: 打过 {report.played} 关, 停止原因 {report.stop}"
        )

    def _cmd_download_sprites(self) -> None:
        """下载全部植物图到本地缓存（工作线程，带进度）。"""
        from . import sprites

        self._busy = "download_sprites"

        def prog(i: int, n: int) -> None:
            self._progress = f"下载植物图 {i}/{n}"
            self._publish_status()

        ok = sprites.download_all(progress=prog)
        self._log(f"植物图下载完成：{ok}/{len(sprites.all_ids())}")

    def _cmd_import_lineup(self, text: str, scene: str = "PE") -> None:
        """粘贴导入：自动识别 1437 文本 / 阵容码 / 阵型字符串，然后布置。"""
        from pvztools.schema.lineup import (
            decode_lineup_code,
            parse_1437,
            parse_lineup_string,
        )

        raw = text.strip()
        lineup = None
        # 1) 阵容码（无空格、无换行、无中文的一段 base64-ish）
        if raw and "\n" not in raw and " " not in raw and raw.isascii() and len(raw) >= 16:
            try:
                lineup = decode_lineup_code(raw)
            except Exception:  # noqa: BLE001
                lineup = None
        # 2) ArrayDesign 阵型字符串（"<map>,<hex> r c ..."）
        if lineup is None and "," in raw and " " in raw and raw.isascii():
            try:
                lineup = parse_lineup_string(raw)
            except Exception:  # noqa: BLE001
                lineup = None
        # 3) 1437 人类可读文本
        if lineup is None:
            lineup = parse_1437(raw, scene=scene)
        summary = self._need_game().place_lineup(lineup)
        self._log(f"导入布置成功：{len(lineup.cells)} 格 {summary}")

    def _cmd_place_custom(self, scene: str, cells: list) -> None:
        """自定义布阵：cells = [{row,col,plant?,container?,shell?}, ...]。"""
        from pvztools.schema.lineup.model import Cell, Lineup

        objs = tuple(
            Cell(
                row=int(c["row"]),
                col=int(c["col"]),
                plant=c.get("plant"),
                container=c.get("container"),
                shell=c.get("shell"),
            )
            for c in cells
        )
        lineup = Lineup(scene=scene, cells=objs, name="自定义", source="gui")
        summary = self._need_game().place_lineup(lineup)
        self._log(f"自定义布阵 {scene} {len(objs)} 格: {summary}")

    def _cmd_run_track(
        self, text: str, backend: str = "input", dry_run: bool = False
    ) -> None:
        report = self._need_game().run_track(
            text, backend=backend, dry_run=dry_run, on_event=self._emit
        )
        label = "校验" if dry_run else "运行"
        self._log(f"轨道{label}完成: {report.summary()}")
