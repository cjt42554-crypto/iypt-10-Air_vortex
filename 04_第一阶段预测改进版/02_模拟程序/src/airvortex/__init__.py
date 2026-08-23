"""IYPT 2027 problem 10 magnetic-stirrer air-vortex research platform."""

# This must precede every import that can transitively load NumPy/SciPy.
from ._hardware import NUMERICAL_THREAD_POLICY, NumericalThreadPolicy

from .core import (
    CONFIG_SCHEMA_VERSION,
    PROBLEM_ID,
    Backend,
    BackendMode,
    ConfigError,
    ContainerConfig,
    Control,
    ControlSnapshot,
    ControlState,
    DriveConfig,
    Event,
    FluidConfig,
    ForwardModelConfig,
    HighFidelityConfig,
    Progress,
    ProgressCallback,
    ResultError,
    RunConfig,
    RunStatus,
    SimulationConfig,
    SimulationResult,
    StirBarConfig,
)
from .version import __version__

__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "PROBLEM_ID",
    "Backend",
    "BackendMode",
    "ConfigError",
    "ContainerConfig",
    "Control",
    "ControlSnapshot",
    "ControlState",
    "DriveConfig",
    "Event",
    "FluidConfig",
    "ForwardModelConfig",
    "HighFidelityConfig",
    "NUMERICAL_THREAD_POLICY",
    "NumericalThreadPolicy",
    "Progress",
    "ProgressCallback",
    "ResultError",
    "RunConfig",
    "RunStatus",
    "SimulationConfig",
    "SimulationResult",
    "StirBarConfig",
    "__version__",
]
