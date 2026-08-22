"""Public background runtime and backend discovery API."""

from .controller import (
    InvalidStateTransition,
    JobController,
    JobMessage,
    JobState,
    StateTransition,
    TERMINAL_STATES,
)
from .registry import (
    BackendDescriptor,
    BackendFactory,
    BackendRegistry,
    BackendRegistryError,
    discover_backends,
)
from .operations import (
    MessageCallback,
    RunOutcome,
    SweepCase,
    SweepOutcome,
    compare_archives,
    estimate_high_fidelity_work,
    linear_values,
    logarithmic_values,
    make_sweep_cases,
    run_once,
    run_paired_compare,
    run_sweep,
)

__all__ = [
    "BackendDescriptor",
    "BackendFactory",
    "BackendRegistry",
    "BackendRegistryError",
    "InvalidStateTransition",
    "JobController",
    "JobMessage",
    "JobState",
    "MessageCallback",
    "RunOutcome",
    "StateTransition",
    "TERMINAL_STATES",
    "SweepCase",
    "SweepOutcome",
    "compare_archives",
    "discover_backends",
    "estimate_high_fidelity_work",
    "linear_values",
    "logarithmic_values",
    "make_sweep_cases",
    "run_once",
    "run_paired_compare",
    "run_sweep",
]
