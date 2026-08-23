"""Backend-neutral runtime contracts and validated solver results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import threading
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from .config import BackendMode, SimulationConfig


JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
Array = NDArray[np.number]


class ResultError(ValueError):
    """Raised when a backend returns an inconsistent result."""


def json_ready(value: object, path: str = "value") -> JSONValue:
    """Validate and normalise a value for deterministic JSON persistence."""

    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (float, np.floating)):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ResultError(f"{path} must not contain NaN or infinity")
        return numeric
    if isinstance(value, Mapping):
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ResultError(f"{path} contains a non-string mapping key")
            result[key] = json_ready(item, f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [json_ready(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise ResultError(f"{path} contains non-JSON value of type {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class Event:
    """A physical, numerical, or user-control event on the simulation timeline."""

    time_s: float
    kind: str
    message: str = ""
    data: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.time_s, bool) or not isinstance(self.time_s, (int, float)):
            raise ResultError("event time_s must be a real number")
        if not math.isfinite(float(self.time_s)) or self.time_s < 0.0:
            raise ResultError("event time_s must be finite and non-negative")
        if not isinstance(self.kind, str) or not self.kind:
            raise ResultError("event kind must be a non-empty string")
        if not isinstance(self.message, str):
            raise ResultError("event message must be a string")
        normalised = json_ready(self.data, "event.data")
        if not isinstance(normalised, dict):
            raise ResultError("event data must be a mapping")
        object.__setattr__(self, "time_s", float(self.time_s))
        object.__setattr__(self, "data", MappingProxyType(normalised))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "time_s": self.time_s,
            "kind": self.kind,
            "message": self.message,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "Event":
        if not isinstance(raw, Mapping):
            raise ResultError("event must be an object")
        allowed = {"time_s", "kind", "message", "data"}
        unknown = set(raw) - allowed
        if unknown:
            raise ResultError(f"event has unknown fields: {', '.join(sorted(unknown))}")
        if "time_s" not in raw or "kind" not in raw:
            raise ResultError("event requires time_s and kind")
        return cls(
            time_s=raw["time_s"],  # type: ignore[arg-type]
            kind=raw["kind"],  # type: ignore[arg-type]
            message=raw.get("message", ""),  # type: ignore[arg-type]
            data=raw.get("data", {}),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class Progress:
    """A small immutable progress message suitable for GUI dispatch."""

    fraction: float
    simulated_time_s: float
    step: int
    stage: str = "running"
    message: str = ""
    metrics: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("fraction", self.fraction),
            ("simulated_time_s", self.simulated_time_s),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ResultError(f"progress {name} must be a real number")
            if not math.isfinite(float(value)):
                raise ResultError(f"progress {name} must be finite")
        if not 0.0 <= self.fraction <= 1.0:
            raise ResultError("progress fraction must lie in [0, 1]")
        if self.simulated_time_s < 0.0:
            raise ResultError("progress simulated_time_s must be non-negative")
        if isinstance(self.step, bool) or not isinstance(self.step, int) or self.step < 0:
            raise ResultError("progress step must be a non-negative integer")
        if not isinstance(self.stage, str) or not self.stage:
            raise ResultError("progress stage must be a non-empty string")
        if not isinstance(self.message, str):
            raise ResultError("progress message must be a string")
        metrics = json_ready(self.metrics, "progress.metrics")
        if not isinstance(metrics, dict):
            raise ResultError("progress metrics must be a mapping")
        object.__setattr__(self, "fraction", float(self.fraction))
        object.__setattr__(self, "simulated_time_s", float(self.simulated_time_s))
        object.__setattr__(self, "metrics", MappingProxyType(metrics))


class RunStatus(str, Enum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


def _numeric_array(value: object, path: str, *, one_dimensional: bool = False) -> Array:
    array = np.asarray(value)
    if not np.issubdtype(array.dtype, np.number):
        raise ResultError(f"{path} must be a numeric array")
    if np.issubdtype(array.dtype, np.complexfloating):
        raise ResultError(f"{path} must be real-valued")
    if one_dimensional and array.ndim != 1:
        raise ResultError(f"{path} must be one-dimensional")
    finite = np.isfinite(array).all()
    if not finite:
        raise ResultError(f"{path} must contain only finite values")
    return array


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Backend-neutral result with dense arrays kept out of JSON metadata."""

    backend: BackendMode
    time_s: Array
    observables: Mapping[str, Array]
    fields: Mapping[str, Array] = field(default_factory=dict)
    events: tuple[Event, ...] = ()
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)
    status: RunStatus = RunStatus.COMPLETED

    def __post_init__(self) -> None:
        if not isinstance(self.backend, BackendMode):
            raise ResultError("result backend must be a BackendMode")
        if not isinstance(self.status, RunStatus):
            raise ResultError("result status must be a RunStatus")
        time_s = _numeric_array(self.time_s, "result.time_s", one_dimensional=True).astype(
            np.float64, copy=False
        )
        if time_s.size and (time_s[0] < 0.0 or np.any(np.diff(time_s) < 0.0)):
            raise ResultError("result.time_s must be non-negative and non-decreasing")
        observables: dict[str, Array] = {}
        for name, value in self.observables.items():
            if not isinstance(name, str) or not name:
                raise ResultError("observable names must be non-empty strings")
            array = _numeric_array(value, f"result.observables.{name}")
            if array.ndim == 0 or array.shape[0] != time_s.size:
                raise ResultError(
                    f"observable {name!r} first dimension must equal time_s length"
                )
            observables[name] = array
        fields: dict[str, Array] = {}
        for name, value in self.fields.items():
            if not isinstance(name, str) or not name:
                raise ResultError("field names must be non-empty strings")
            fields[name] = _numeric_array(value, f"result.fields.{name}")
        events = tuple(self.events)
        if any(not isinstance(event, Event) for event in events):
            raise ResultError("result events must contain Event objects")
        metadata = json_ready(self.metadata, "result.metadata")
        if not isinstance(metadata, dict):
            raise ResultError("result metadata must be a mapping")
        object.__setattr__(self, "time_s", time_s)
        object.__setattr__(self, "observables", MappingProxyType(observables))
        object.__setattr__(self, "fields", MappingProxyType(fields))
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "metadata", MappingProxyType(metadata))


class ControlState(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    STOP_REQUESTED = "stop_requested"


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    state: ControlState
    stop_reason: str


class Control:
    """Thread-safe cooperative pause/resume and soft-stop controller.

    A backend calls :meth:`checkpoint` at bounded intervals.  The call blocks
    while paused and returns ``False`` once a stop is requested.  A stop also
    wakes a paused worker, which gives pause and soft stop real runtime meaning.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._state = ControlState.RUNNING
        self._stop_reason = ""

    @property
    def state(self) -> ControlState:
        with self._condition:
            return self._state

    @property
    def stop_requested(self) -> bool:
        with self._condition:
            return self._state is ControlState.STOP_REQUESTED

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._state is ControlState.PAUSED

    @property
    def stop_reason(self) -> str:
        with self._condition:
            return self._stop_reason

    def snapshot(self) -> ControlSnapshot:
        with self._condition:
            return ControlSnapshot(self._state, self._stop_reason)

    def pause(self) -> bool:
        """Pause future checkpoints; return whether the state changed."""

        with self._condition:
            if self._state is not ControlState.RUNNING:
                return False
            self._state = ControlState.PAUSED
            return True

    def resume(self) -> bool:
        """Resume a paused worker; return whether the state changed."""

        with self._condition:
            if self._state is not ControlState.PAUSED:
                return False
            self._state = ControlState.RUNNING
            self._condition.notify_all()
            return True

    def request_stop(self, reason: str = "user requested stop") -> bool:
        """Request cooperative termination and wake all paused workers."""

        if not isinstance(reason, str):
            raise TypeError("stop reason must be a string")
        with self._condition:
            if self._state is ControlState.STOP_REQUESTED:
                return False
            self._state = ControlState.STOP_REQUESTED
            self._stop_reason = reason
            self._condition.notify_all()
            return True

    def stop(self, reason: str = "user requested stop") -> bool:
        """Alias used by GUI adapters."""

        return self.request_stop(reason)

    def checkpoint(self) -> bool:
        """Block during pause; return ``False`` iff soft stop was requested."""

        with self._condition:
            while self._state is ControlState.PAUSED:
                self._condition.wait()
            return self._state is not ControlState.STOP_REQUESTED


ProgressCallback = Callable[[Progress], None]


@runtime_checkable
class Backend(Protocol):
    """Common interface implemented by forward and high-fidelity solvers."""

    mode: BackendMode

    def run(
        self,
        config: SimulationConfig,
        control: Control | None = None,
        progress: ProgressCallback | None = None,
    ) -> SimulationResult:
        """Run the solver and return all data accumulated before completion/stop."""
