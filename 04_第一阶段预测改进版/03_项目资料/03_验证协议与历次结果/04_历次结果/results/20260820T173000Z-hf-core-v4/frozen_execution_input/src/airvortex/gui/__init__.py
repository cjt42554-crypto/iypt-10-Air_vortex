"""空气涡流统一图形界面。

界面模块只负责配置、运行编排和可视化；物理计算由 ``airvortex`` 的
core/runtime/analysis 模块提供。
"""

from .model import (
    BackendMode,
    ExperimentData,
    GuiViewModel,
    RunState,
    SimulationView,
)

__all__ = [
    "BackendMode",
    "ExperimentData",
    "GuiViewModel",
    "RunState",
    "SimulationView",
]


def launch() -> None:
    """延迟导入 Tk，便于在无显示环境中导入 ViewModel。"""

    from .app import launch as _launch

    _launch()

