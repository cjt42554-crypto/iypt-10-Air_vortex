"""Deterministic one-/two-dimensional sweep plans and phase-map aggregation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import hashlib
import itertools
import json
import math
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

import numpy as np

from ._common import DataValidationError
from .states import ConnectivityState, StateClassification

if TYPE_CHECKING:
    from airvortex.core import SimulationConfig, SimulationResult


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataValidationError("sweep config contains a non-finite float")
        return 0.0 if value == 0.0 else value  # canonicalize negative zero
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    raise DataValidationError(
        f"config value of type {type(value).__name__} is not serializable"
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _replace_dotted(snapshot: dict[str, Any], path: str, value: Any) -> None:
    components = path.split(".")
    current: dict[str, Any] = snapshot
    for component in components[:-1]:
        child = current.get(component)
        if not isinstance(child, dict):
            raise DataValidationError(f"sweep parameter path does not exist: {path!r}")
        current = child
    leaf = components[-1]
    if leaf not in current:
        raise DataValidationError(f"sweep parameter path does not exist: {path!r}")
    current[leaf] = _jsonable(value)


@dataclass(frozen=True)
class SweepAxis:
    parameter: str
    values: tuple[float, ...]

    def __init__(self, parameter: str, values: Sequence[float]):
        if not isinstance(parameter, str) or not parameter.strip() or ".." in parameter:
            raise DataValidationError("sweep parameter must be a non-empty dotted path")
        checked: list[float] = []
        for raw in values:
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise DataValidationError(
                    f"sweep value for {parameter!r} is not numeric"
                ) from exc
            if not math.isfinite(value):
                raise DataValidationError(
                    f"sweep value for {parameter!r} must be finite"
                )
            checked.append(0.0 if value == 0.0 else value)
        if not checked:
            raise DataValidationError("each sweep axis needs at least one value")
        if len(checked) != len(set(checked)):
            raise DataValidationError("duplicate values in a sweep axis are not allowed")
        object.__setattr__(self, "parameter", parameter.strip())
        object.__setattr__(self, "values", tuple(checked))


@dataclass(frozen=True)
class SweepJob:
    job_id: str
    index: int
    coordinates: Mapping[str, float]
    config_snapshot: Mapping[str, Any]


@dataclass(frozen=True)
class SweepPlan:
    axes: tuple[SweepAxis, ...]
    base_config_hash: str
    jobs: tuple[SweepJob, ...]

    @property
    def dimension(self) -> int:
        return len(self.axes)


def generate_sweep_plan(
    base_config: "SimulationConfig | Mapping[str, Any]",
    axes: Sequence[SweepAxis],
) -> SweepPlan:
    """Generate a stable, side-effect-free Cartesian plan for one or two axes."""

    axes = tuple(axes)
    if len(axes) not in {1, 2}:
        raise DataValidationError("a sweep plan must contain exactly one or two axes")
    names = [axis.parameter for axis in axes]
    if len(set(names)) != len(names):
        raise DataValidationError("sweep axes must use different parameter paths")
    base = _jsonable(base_config)
    if not isinstance(base, dict):
        raise DataValidationError("SimulationConfig must serialize to a JSON object")
    base_text = _canonical_json(base)
    base_hash = hashlib.sha256(base_text.encode("utf-8")).hexdigest()

    jobs: list[SweepJob] = []
    for index, combination in enumerate(
        itertools.product(*(axis.values for axis in axes))
    ):
        coordinates = {axis.parameter: value for axis, value in zip(axes, combination)}
        snapshot = json.loads(base_text)
        for path, value in coordinates.items():
            _replace_dotted(snapshot, path, value)
        identity = {
            "schema": "airvortex.sweep-job.v1",
            "base_config_hash": base_hash,
            "coordinates": coordinates,
        }
        digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
        jobs.append(
            SweepJob(
                job_id=f"job-{digest[:20]}",
                index=index,
                coordinates=MappingProxyType(dict(coordinates)),
                config_snapshot=MappingProxyType(snapshot),
            )
        )
    return SweepPlan(axes=axes, base_config_hash=base_hash, jobs=tuple(jobs))


@dataclass(frozen=True)
class PhasePoint:
    job_id: str
    coordinates: Mapping[str, float]
    state: ConnectivityState | None
    observables: Mapping[str, float]
    diagnostics: tuple[str, ...] = ()


def _state_from_result(result: "SimulationResult") -> tuple[ConnectivityState | None, str | None]:
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, Mapping):
        raw = metadata.get("connectivity_state", metadata.get("final_state"))
        if raw is not None:
            try:
                return ConnectivityState(str(raw)), None
            except ValueError:
                return None, f"invalid connectivity state in result metadata: {raw!r}"
    observables = getattr(result, "observables", None)
    if isinstance(observables, Mapping) and "connectivity_state_code" in observables:
        values = np.asarray(observables["connectivity_state_code"], dtype=float)
        if values.ndim == 1 and len(values) and np.isfinite(values[-1]):
            code = int(round(float(values[-1])))
            if 0 <= code <= 4:
                return ConnectivityState(f"S{code}"), None
        return None, "connectivity_state_code is missing, non-finite, or outside 0..4"
    return None, (
        "connectivity state unavailable: provide metadata.connectivity_state "
        "or observable connectivity_state_code"
    )


def aggregate_phase_data(
    plan: SweepPlan,
    results: Mapping[str, "SimulationResult"],
    *,
    observable_names: Sequence[str] = (
        "surface_depth",
        "min_gap_to_bar",
        "critical_rpm",
    ),
    classifier: (
        Callable[["SimulationResult"], StateClassification | ConnectivityState] | None
    ) = None,
) -> tuple[PhasePoint, ...]:
    """Aggregate completed sweep results without fabricating absent quantities."""

    points: list[PhasePoint] = []
    for job in plan.jobs:
        if job.job_id not in results:
            points.append(
                PhasePoint(
                    job.job_id,
                    job.coordinates,
                    None,
                    MappingProxyType({}),
                    ("result unavailable for this planned job",),
                )
            )
            continue
        result = results[job.job_id]
        diagnostics: list[str] = []
        if classifier is None:
            state, diagnostic = _state_from_result(result)
            if diagnostic:
                diagnostics.append(diagnostic)
        else:
            classified = classifier(result)
            state = classified.state if isinstance(classified, StateClassification) else classified
            if not isinstance(state, ConnectivityState):
                raise DataValidationError("phase classifier must return a connectivity state")

        final_values: dict[str, float] = {}
        observables = getattr(result, "observables", None)
        if not isinstance(observables, Mapping):
            diagnostics.append("result has no observable mapping")
        else:
            for name in observable_names:
                if name not in observables:
                    diagnostics.append(f"observable {name!r} unavailable")
                    continue
                values = np.asarray(observables[name], dtype=float)
                if values.ndim != 1 or not len(values) or not np.isfinite(values[-1]):
                    diagnostics.append(f"observable {name!r} has no finite scalar final value")
                    continue
                final_values[name] = float(values[-1])
        points.append(
            PhasePoint(
                job.job_id,
                job.coordinates,
                state,
                MappingProxyType(final_values),
                tuple(diagnostics),
            )
        )
    return tuple(points)
