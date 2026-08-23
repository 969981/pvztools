"""pvztools 图形界面（可选组件）。

依赖 imgui-bundle（Dear ImGui 官方生态的 Python 绑定）：

    pip install pvztools[gui]
    pvztools gui

worker/strings/theme 不依赖 imgui，可独立导入；只有真正启动
界面（run_gui）才需要 imgui-bundle。
"""

from __future__ import annotations

from typing import List, Optional

__all__ = ["run_gui", "gui_available"]


def gui_available() -> bool:
    try:
        import imgui_bundle  # noqa: F401
    except ImportError:
        return False
    return True


def run_gui(argv: Optional[List[str]] = None) -> int:
    from .app import run_gui as _run

    return _run(argv)
