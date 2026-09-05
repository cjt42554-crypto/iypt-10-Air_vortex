"""Deterministic one-/two-dimensional sweep plans and phase-map aggregation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
import hashlib
import itertools
import json
import math
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

import numpy as np

from airvortex.core.validity import (
    QuantitativeValidityError,
    QuantitativeValidityStatus,
    quantitative_valid_mask,
    quantitative_validity,
)

from ._common import DataValidationError
from .comparison import series_from_result
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
    observable_units: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    eligible_for_phase_map: bool = False
    termination_reason: str | None = None
    requested_end_s: float | None = None
    actual_end_s: float | None = None


_OBSERVABLE_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "funnel_depth_m": (
            "funnel_depth_m",
            "surface_depth_m",
            "surface_depth",
        ),
        "min_gap_to_bar_m": (
            "min_gap_to_bar_m",
            "minimum_gap_m",
            "min_gap_to_bar",
        ),
        "critical_actual_rpm": (
            "critical_actual_rpm",
            "critical_rpm",
        ),
    }
)


def _termination_summary(
    result: "SimulationResult", requested_end_s: float | None
) -> tuple[bool, str | None, float | None, tuple[str, ...]]:
    """Return fail-closed phase-map eligibility and termination diagnostics."""

    diagnostics: list[str] = []
    status = getattr(getattr(result, "status", None), "value", None)
    metadata = getattr(result, "metadata", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    nested = metadata.get("diagnostics")
    nested = nested if isinstance(nested, Mapping) else {}
    raw_reason = nested.get("termination_reason", metadata.get("termination_reason"))
    reason = str(raw_reason) if raw_reason is not None else None
    domain_status = str(metadata.get("domain_status", ""))

    raw_time = np.asarray(getattr(result, "time_s", ()), dtype=float)
    actual_end_s: float | None = None
    if raw_time.ndim == 1 and raw_time.size and np.isfinite(raw_time[-1]):
        actual_end_s = float(raw_time[-1])

    eligible = status == "completed"
    if not eligible:
        diagnostics.append(f"result status {status!r} is not completed")
    if reason is not None and "domain_limit" in reason.casefold():
        eligible = False
        diagnostics.append(f"model-domain termination excluded: {reason}")
    if domain_status.upper() == "OUT_OF_MODEL_DOMAIN":
        eligible = False
        diagnostics.append("post-contact mathematical proxy excluded from phase map")
    try:
        validity = quantitative_validity(result)
        validity_mask = quantitative_valid_mask(result)
    except QuantitativeValidityError as exc:
        eligible = False
        diagnostics.append(f"invalid quantitative-validity contract: {exc}")
    else:
        if validity.status is not QuantitativeValidityStatus.VALID:
            eligible = False
            diagnostics.append(
                "phase-map quantitative aggregation requires validity status 'valid'; "
                f"received {validity.status.value}: {validity.reason}"
            )
        if validity_mask.size == 0 or not bool(validity_mask[-1]):
            eligible = False
            diagnostics.append(
                "final recorded sample is not quantitatively valid; no terminal value inferred"
            )
    if requested_end_s is not None:
        if actual_end_s is None:
            eligible = False
            diagnostics.append("actual simulation end time unavailable")
        else:
            tolerance = max(1.0e-12, 1.0e-9 * max(1.0, requested_end_s))
            if actual_end_s < requested_end_s - tolerance:
                eligible = False
                diagnostics.append(
                    "result ended before requested duration: "
                    f"actual={actual_end_s:.12g}s, requested={requested_end_s:.12g}s"
                )
    return eligible, reason, actual_end_s, tuple(diagnostics)


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
        "funnel_depth_m",
        "min_gap_to_bar_m",
        "critical_actual_rpm",
    ),
    classifier: (
        Callable[["SimulationResult"], StateClassification | ConnectivityState] | None
    ) = None,
) -> tuple[PhasePoint, ...]:
    """Aggregate only phase-map-eligible results without fabricating quantities.

    Stopped/failed runs, unclassified early termination, H1 model-domain exits,
    and Forward post-contact mathematical proxies remain visible as ineligible
    :class:`PhasePoint` records but never receive an S0--S4 state.
    """

    points: list[PhasePoint] = []
    for job in plan.jobs:
        if job.job_id not in results:
            points.append(
                PhasePoint(
                    job_id=job.job_id,
                    coordinates=job.coordinates,
                    state=None,
                    observables=MappingProxyType({}),
                    diagnostics=("result unavailable for this planned job",),
                )
            )
            continue
        result = results[job.job_id]
        diagnostics: list[str] = []
        requested_end_s: float | None = None
        run_config = job.config_snapshot.get("run")
        if isinstance(run_config, Mapping):
            raw_requested = run_config.get("duration_s")
            if isinstance(raw_requested, (int, float)) and math.isfinite(
                float(raw_requested)
            ):
                requested_end_s = float(raw_requested)
        eligible, termination_reason, actual_end_s, termination_diagnostics = (
            _termination_summary(result, requested_end_s)
        )
        diagnostics.extend(termination_diagnostics)

        if not eligible:
            state = None
        elif classifier is None:
            state, diagnostic = _state_from_result(result)
            if diagnostic:
                diagnostics.append(diagnostic)
        else:
            classified = classifier(result)
            if isinstance(classified, StateClassification):
                state = classified.state
                if not classified.valid_for_flow_state_statistics:
                    eligible = False
                    diagnostics.append(
                        classified.invalid_reason or classified.reason
                    )
            else:
                state = classified
            if state is not None and not isinstance(state, ConnectivityState):
                raise DataValidationError(
                    "phase classifier must return ConnectivityState or a valid "
                    "StateClassification"
                )

        if eligible and state is None:
            eligible = False
            diagnostics.append(
                "phase-map ineligible: no valid S0--S4 state classification"
            )

        final_values: dict[str, float] = {}
        final_units: dict[str, str] = {}
        observables = getattr(result, "observables", None)
        if not eligible:
            diagnostics.append(
                "quantitative observables omitted because this result is phase-map ineligible"
            )
        elif not isinstance(observables, Mapping):
            diagnostics.append("result has no observable mapping")
        else:
            for requested_name in observable_names:
                aliases = _OBSERVABLE_ALIASES.get(
                    requested_name, (requested_name,)
                )
                present = [name for name in aliases if name in observables]
                if not present:
                    diagnostics.append(
                        f"observable {requested_name!r} unavailable"
                    )
                    continue
                extracted = []
                try:
                    for name in present:
                        extracted.append((name, series_from_result(result, name)))
                except DataValidationError as exc:
                    diagnostics.append(
                        f"observable {requested_name!r} rejected: {exc}"
                    )
                    continue
                units = {series.unit for _, series in extracted}
                final_samples = [float(series.values[-1]) for _, series in extracted]
                if len(units) != 1 or not np.allclose(
                    final_samples,
                    final_samples[0],
                    rtol=1.0e-12,
                    atol=1.0e-15,
                ):
                    diagnostics.append(
                        f"observable aliases for {requested_name!r} conflict: "
                        + ", ".join(name for name, _ in extracted)
                    )
                    continue
                final_values[requested_name] = final_samples[0]
                final_units[requested_name] = next(iter(units))
                if extracted[0][0] != requested_name:
                    diagnostics.append(
                        f"observable {requested_name!r} read through legacy alias "
                        f"{extracted[0][0]!r}"
                    )
        points.append(
            PhasePoint(
                job_id=job.job_id,
                coordinates=job.coordinates,
                state=state if eligible else None,
                observables=MappingProxyType(final_values),
                diagnostics=tuple(diagnostics),
                observable_units=MappingProxyType(final_units),
                eligible_for_phase_map=eligible,
                termination_reason=termination_reason,
                requested_end_s=requested_end_s,
                actual_end_s=actual_end_s,
            )
        )
    return tuple(points)
