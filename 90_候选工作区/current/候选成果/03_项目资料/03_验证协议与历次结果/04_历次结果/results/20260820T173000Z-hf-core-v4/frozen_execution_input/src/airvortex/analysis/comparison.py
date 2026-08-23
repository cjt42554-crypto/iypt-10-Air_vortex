"""Observable-level comparison for the reduced and high-fidelity backends."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping

import numpy as np

from ._common import DataValidationError, NoCommonIntervalError

if TYPE_CHECKING:
    from numpy.typing import NDArray
    from airvortex.core import SimulationResult
    from .experiment import ExperimentDataset


@dataclass(frozen=True)
class ObservableSeries:
    """One scalar observable on its unmodified source time grid."""

    time: "NDArray[np.float64]"
    values: "NDArray[np.float64]"
    unit: str
    source: str

    def __post_init__(self) -> None:
        time = np.asarray(self.time, dtype=float)
        values = np.asarray(self.values, dtype=float)
        if time.ndim != 1 or values.ndim != 1:
            raise DataValidationError("comparison supports one-dimensional observables")
        if len(time) != len(values) or len(time) < 2:
            raise DataValidationError("time and observable must have the same length >= 2")
        if not np.all(np.isfinite(time)) or not np.all(np.isfinite(values)):
            raise DataValidationError("time series contains a missing or non-finite value")
        if not np.all(np.diff(time) > 0.0):
            raise DataValidationError("time must be strictly increasing")
        if not isinstance(self.unit, str) or not self.unit.strip():
            raise DataValidationError("observable unit must be a non-empty string")
        # Defensive copies preserve the exact original grids against caller mutation.
        time = time.copy()
        values = values.copy()
        time.setflags(write=False)
        values.setflags(write=False)
        object.__setattr__(self, "time", time)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "unit", self.unit.strip())


@dataclass(frozen=True)
class ErrorMetrics:
    rmse: float
    mae: float
    nrmse: float | None
    max_absolute_error: float
    threshold_crossing_error: float | None
    normalization_scale: float
    normalization_defined: bool

    @property
    def max_error(self) -> float:
        return self.max_absolute_error

    @property
    def threshold_error(self) -> float | None:
        return self.threshold_crossing_error

    def as_dict(self) -> dict[str, float | None]:
        return {
            "rmse": self.rmse,
            "mae": self.mae,
            "nrmse": self.nrmse,
            "max_absolute_error": self.max_absolute_error,
            "threshold_crossing_error": self.threshold_crossing_error,
            "normalization_scale": self.normalization_scale,
            "normalization_defined": self.normalization_defined,
        }


@dataclass(frozen=True)
class ObservableComparison:
    """A comparison that retains both raw grids and the explicit aligned grid."""

    observable: str
    left: ObservableSeries
    right: ObservableSeries
    common_interval: tuple[float, float]
    common_time: "NDArray[np.float64]"
    left_interpolated: "NDArray[np.float64]"
    right_interpolated: "NDArray[np.float64]"
    residual: "NDArray[np.float64]"
    interpolation: str
    normalization: str
    metrics: ErrorMetrics


@dataclass(frozen=True)
class ThresholdComparison:
    """Direct model/reference comparison for a critical speed or other threshold."""

    predicted: float
    reference: float
    unit: str
    absolute_error: float
    relative_error: float | None


@dataclass(frozen=True)
class ComparisonBundle:
    """All requested common scalar observables plus honest skip diagnostics."""

    comparisons: Mapping[str, ObservableComparison]
    diagnostics: tuple[str, ...]


def _validated_explicit_unit(value: Any, observable: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(
            f"explicit unit for observable {observable!r} must be a non-empty string"
        )
    unit = value.strip()
    if unit.casefold() == "si":
        raise DataValidationError(
            f"explicit unit for observable {observable!r} cannot be the ambiguous "
            "placeholder 'SI'; provide a dimensional unit such as 'm' or the "
            "dimensionless unit '1'"
        )
    return unit


def _unit_from_result(result: Any, observable: str) -> str:
    """Read an observable unit without assigning an implicit unit."""

    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, Mapping):
        for key in ("observable_units", "units"):
            units = metadata.get(key)
            if isinstance(units, Mapping) and observable in units:
                return _validated_explicit_unit(units[observable], observable)
    units = getattr(result, "observable_units", None)
    if isinstance(units, Mapping) and observable in units:
        return _validated_explicit_unit(units[observable], observable)
    raise DataValidationError(
        f"result metadata has no explicit unit for observable {observable!r}; "
        "unitless fallback is forbidden because SI alone does not identify a "
        "physical dimension"
    )


def series_from_result(
    result: "SimulationResult",
    observable: str,
    *,
    source: str | None = None,
    unit: str | None = None,
) -> ObservableSeries:
    """Extract a common scalar observable from a core ``SimulationResult``."""

    if not hasattr(result, "time_s") or not hasattr(result, "observables"):
        raise DataValidationError(
            "expected SimulationResult with time_s and observables fields"
        )
    observables = result.observables
    if not isinstance(observables, Mapping) or observable not in observables:
        raise DataValidationError(f"result does not contain observable {observable!r}")
    values = np.asarray(observables[observable], dtype=float)
    if values.ndim != 1:
        raise DataValidationError(
            f"observable {observable!r} is not scalar over time (shape={values.shape})"
        )
    source_name = source
    if source_name is None:
        source_name = str(getattr(result, "backend", "unknown"))
    return ObservableSeries(
        time=np.asarray(result.time_s, dtype=float),
        values=values,
        unit=(
            _unit_from_result(result, observable)
            if unit is None
            else _validated_explicit_unit(unit, observable)
        ),
        source=source_name,
    )


def series_from_experiment(
    dataset: "ExperimentDataset",
    observable: str,
    *,
    source: str = "experiment",
) -> ObservableSeries:
    """Extract an experimental observable with an explicit missing-column error."""

    if observable not in dataset.observables:
        available = ", ".join(sorted(dataset.observables)) or "<none>"
        raise DataValidationError(
            f"experiment is missing required column {observable!r}; available: {available}"
        )
    unit = dataset.units.get(observable)
    if unit is None:
        raise DataValidationError(
            f"experiment metadata has no unit mapping for {observable!r}"
        )
    return ObservableSeries(
        time=dataset.time_s,
        values=dataset.observables[observable],
        unit=unit,
        source=source,
    )


def _threshold_crossing_time(
    time: "NDArray[np.float64]",
    values: "NDArray[np.float64]",
    threshold: float,
    direction: str,
) -> float | None:
    if direction == "rising":
        hits = values >= threshold
    elif direction == "falling":
        hits = values <= threshold
    elif direction == "either":
        centered = values - threshold
        indices = np.flatnonzero(centered[:-1] * centered[1:] <= 0.0)
        if values[0] == threshold:
            return float(time[0])
        if not len(indices):
            return None
        i = int(indices[0])
        dv = values[i + 1] - values[i]
        if dv == 0.0:
            return float(time[i])
        return float(time[i] + (threshold - values[i]) * (time[i + 1] - time[i]) / dv)
    else:
        raise DataValidationError("threshold_direction must be rising, falling, or either")

    indices = np.flatnonzero(hits)
    if not len(indices):
        return None
    i = int(indices[0])
    if i == 0:
        return float(time[0])
    dv = values[i] - values[i - 1]
    if dv == 0.0:
        return float(time[i])
    return float(
        time[i - 1]
        + (threshold - values[i - 1]) * (time[i] - time[i - 1]) / dv
    )


def compare_series(
    left: ObservableSeries,
    right: ObservableSeries,
    *,
    observable: str = "observable",
    threshold: float | None = None,
    threshold_direction: str = "rising",
    normalization: str = "right_range",
) -> ObservableComparison:
    """Linearly align two series over their closed common time interval.

    The common grid is the sorted union of both original grids clipped to the
    overlap, with interval endpoints inserted explicitly.  Consequently no
    backend is silently privileged as the reference sampling rate.
    """

    if left.unit != right.unit:
        raise DataValidationError(
            f"incompatible observable units: {left.unit!r} != {right.unit!r}"
        )
    start = max(float(left.time[0]), float(right.time[0]))
    stop = min(float(left.time[-1]), float(right.time[-1]))
    if stop <= start:
        raise NoCommonIntervalError(
            f"time series have no positive-duration common interval: [{start}, {stop}]"
        )

    left_points = left.time[(left.time >= start) & (left.time <= stop)]
    right_points = right.time[(right.time >= start) & (right.time <= stop)]
    common_time = np.unique(np.concatenate(([start], left_points, right_points, [stop])))
    if len(common_time) < 2:
        raise NoCommonIntervalError("common interval contains fewer than two points")
    left_i = np.interp(common_time, left.time, left.values)
    right_i = np.interp(common_time, right.time, right.values)
    residual = left_i - right_i

    duration = stop - start
    rmse = float(np.sqrt(np.trapz(residual * residual, common_time) / duration))
    mae = float(np.trapz(np.abs(residual), common_time) / duration)
    max_error = float(np.max(np.abs(residual)))
    if normalization == "right_range":
        scale = float(np.ptp(right_i))
    elif normalization == "left_range":
        scale = float(np.ptp(left_i))
    elif normalization == "pooled_range":
        scale = float(
            max(np.max(left_i), np.max(right_i))
            - min(np.min(left_i), np.min(right_i))
        )
    elif normalization == "right_rms":
        scale = float(np.sqrt(np.trapz(right_i * right_i, common_time) / duration))
    else:
        raise DataValidationError(
            "normalization must be right_range, left_range, pooled_range, or right_rms"
        )
    normalization_defined = scale > 0.0
    nrmse = rmse / scale if normalization_defined else None

    threshold_error: float | None = None
    if threshold is not None:
        threshold = float(threshold)
        if not math.isfinite(threshold):
            raise DataValidationError("threshold must be finite")
        # Crossing times use the common interval only.  Linear endpoint values
        # are inserted without altering the source arrays retained above.
        left_mask = (left.time > start) & (left.time < stop)
        right_mask = (right.time > start) & (right.time < stop)
        left_cross_time = np.concatenate(([start], left.time[left_mask], [stop]))
        right_cross_time = np.concatenate(([start], right.time[right_mask], [stop]))
        left_cross_values = np.interp(left_cross_time, left.time, left.values)
        right_cross_values = np.interp(right_cross_time, right.time, right.values)
        left_cross = _threshold_crossing_time(
            left_cross_time, left_cross_values, threshold, threshold_direction
        )
        right_cross = _threshold_crossing_time(
            right_cross_time, right_cross_values, threshold, threshold_direction
        )
        if left_cross is not None and right_cross is not None:
            threshold_error = abs(left_cross - right_cross)

    for array in (common_time, left_i, right_i, residual):
        array.setflags(write=False)
    return ObservableComparison(
        observable=observable,
        left=left,
        right=right,
        common_interval=(start, stop),
        common_time=common_time,
        left_interpolated=left_i,
        right_interpolated=right_i,
        residual=residual,
        interpolation="linear",
        normalization=normalization,
        metrics=ErrorMetrics(
            rmse=rmse,
            mae=mae,
            nrmse=nrmse,
            max_absolute_error=max_error,
            threshold_crossing_error=threshold_error,
            normalization_scale=scale,
            normalization_defined=normalization_defined,
        ),
    )


def compare_results(
    left: "SimulationResult",
    right: "SimulationResult",
    observable: str,
    *,
    threshold: float | None = None,
    threshold_direction: str = "rising",
    normalization: str = "right_range",
) -> ObservableComparison:
    """Compare one common observable from two core simulation results."""

    return compare_series(
        series_from_result(left, observable),
        series_from_result(right, observable),
        observable=observable,
        threshold=threshold,
        threshold_direction=threshold_direction,
        normalization=normalization,
    )


def compare_common_observables(
    left: "SimulationResult",
    right: "SimulationResult",
    observables: list[str] | tuple[str, ...] | None = None,
    *,
    thresholds: Mapping[str, float] | None = None,
    normalization: str = "right_range",
) -> ComparisonBundle:
    """Compare multiple common scalar outputs from the two backend results.

    Missing requested columns and non-scalar fields are diagnosed and omitted;
    an empty successful-looking bundle is never returned silently.
    """

    left_names = set(getattr(left, "observables", {}))
    right_names = set(getattr(right, "observables", {}))
    if observables is None:
        requested = sorted(left_names & right_names)
    else:
        requested = list(observables)
    if not requested:
        raise DataValidationError("results have no requested common observables")
    comparisons: dict[str, ObservableComparison] = {}
    diagnostics: list[str] = []
    for name in requested:
        absent: list[str] = []
        if name not in left_names:
            absent.append("left")
        if name not in right_names:
            absent.append("right")
        if absent:
            diagnostics.append(
                f"observable {name!r} unavailable in {' and '.join(absent)} result"
            )
            continue
        try:
            comparisons[name] = compare_results(
                left,
                right,
                name,
                threshold=None if thresholds is None else thresholds.get(name),
                normalization=normalization,
            )
        except DataValidationError as exc:
            diagnostics.append(f"observable {name!r} not compared: {exc}")
    if not comparisons:
        raise DataValidationError(
            "no requested observable could be compared; " + "; ".join(diagnostics)
        )
    return ComparisonBundle(MappingProxyType(comparisons), tuple(diagnostics))


def compare_thresholds(
    predicted: float,
    reference: float,
    *,
    unit: str = "rad/s",
) -> ThresholdComparison:
    """Return absolute and reference-relative error of a scalar threshold."""

    try:
        predicted_value = float(predicted)
        reference_value = float(reference)
    except (TypeError, ValueError) as exc:
        raise DataValidationError("thresholds must be real numbers") from exc
    if not math.isfinite(predicted_value) or not math.isfinite(reference_value):
        raise DataValidationError("thresholds must be finite")
    if not isinstance(unit, str) or not unit.strip():
        raise DataValidationError("threshold unit must be a non-empty string")
    absolute = abs(predicted_value - reference_value)
    relative = None if reference_value == 0.0 else absolute / abs(reference_value)
    return ThresholdComparison(
        predicted_value,
        reference_value,
        unit.strip(),
        absolute,
        relative,
    )
