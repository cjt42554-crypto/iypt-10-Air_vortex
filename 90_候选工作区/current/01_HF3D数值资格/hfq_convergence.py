"""Time-series convergence, GCI, event consistency, and numerical gates."""
from __future__ import annotations
import math
from typing import Any, Mapping
import numpy as np
from hfq_types import (
    ConvergenceEstimate,
    EventSummary,
    FloatArray,
    JsonObject,
    PairwiseError,
    QualificationError,
    SeriesData,
)
from hfq_json import _finite_positive, _require_mapping

def interpolate_time(series: SeriesData, target_time_s: FloatArray) -> FloatArray:
    target = np.asarray(target_time_s, dtype=np.float64)
    if target.ndim != 1 or target.size < 2 or not np.all(np.diff(target) > 0.0):
        raise QualificationError("target_time_s must be strictly increasing")
    if target[0] < series.time_s[0] - 1.0e-12 or target[-1] > series.time_s[-1] + 1.0e-12:
        raise QualificationError("target times lie outside source interval")
    flat = series.values.reshape(series.values.shape[0], -1)
    output = np.empty((target.size, flat.shape[1]), dtype=np.float64)
    for column in range(flat.shape[1]):
        output[:, column] = np.interp(target, series.time_s, flat[:, column])
    return output.reshape((target.size, *series.values.shape[1:]))

def _common_target(reference: SeriesData, other: SeriesData) -> FloatArray:
    start = max(reference.time_s[0], other.time_s[0])
    end = min(reference.time_s[-1], other.time_s[-1])
    target = reference.time_s[
        (reference.time_s >= start - 1.0e-15)
        & (reference.time_s <= end + 1.0e-15)
    ]
    if target.size < 2:
        target = np.linspace(start, end, 2, dtype=np.float64)
    return np.unique(target)

def _trapezoid(values: FloatArray, coordinates: FloatArray) -> float:
    function = getattr(np, "trapezoid", None)
    if function is not None:
        return float(function(values, coordinates))
    return float(np.trapz(values, coordinates))

def _time_weighted_l2(time_s: FloatArray, values: FloatArray) -> float:
    flattened = np.asarray(values, dtype=np.float64).reshape(time_s.size, -1)
    squared = np.mean(flattened * flattened, axis=1)
    duration = float(time_s[-1] - time_s[0])
    if duration <= 0.0:
        raise QualificationError("time interval must be positive")
    return math.sqrt(max(0.0, _trapezoid(squared, time_s) / duration))

def pairwise_error(
    reference: SeriesData,
    comparison: SeriesData,
    *,
    absolute_scale_floor: float = 0.0,
) -> PairwiseError:
    floor = float(absolute_scale_floor)
    if not math.isfinite(floor) or floor < 0.0:
        raise QualificationError("absolute_scale_floor must be finite and >= 0")
    target = _common_target(reference, comparison)
    ref = interpolate_time(reference, target)
    cmp = interpolate_time(comparison, target)
    if ref.shape != cmp.shape:
        raise QualificationError("series have incompatible trailing shapes")
    difference = cmp - ref
    absolute_l2 = _time_weighted_l2(target, difference)
    absolute_linf = float(np.max(np.abs(difference)))
    reference_l2 = _time_weighted_l2(target, ref)
    reference_linf = float(np.max(np.abs(ref)))
    scale_l2 = max(reference_l2, floor, np.finfo(float).tiny)
    scale_linf = max(reference_linf, floor, np.finfo(float).tiny)
    return PairwiseError(
        relative_l2=absolute_l2 / scale_l2,
        relative_linf=absolute_linf / scale_linf,
        absolute_l2=absolute_l2,
        absolute_linf=absolute_linf,
        reference_l2=reference_l2,
        reference_linf=reference_linf,
        common_start_s=float(target[0]),
        common_end_s=float(target[-1]),
        sample_count=int(target.size),
    )

def _generalized_ratio(p: float, h_fine: float, h_medium: float, h_coarse: float) -> float:
    numerator = h_coarse**p - h_medium**p
    denominator = h_medium**p - h_fine**p
    if denominator <= 0.0:
        return math.inf
    return numerator / denominator

def solve_observed_order(
    *,
    h_fine: float,
    h_medium: float,
    h_coarse: float,
    difference_fine_medium: float,
    difference_medium_coarse: float,
    maximum_order: float = 12.0,
) -> float | None:
    for name, value in (
        ("h_fine", h_fine),
        ("h_medium", h_medium),
        ("h_coarse", h_coarse),
        ("difference_fine_medium", difference_fine_medium),
        ("difference_medium_coarse", difference_medium_coarse),
    ):
        if not math.isfinite(value) or value <= 0.0:
            return None
    if not h_fine < h_medium < h_coarse:
        return None
    target = difference_medium_coarse / difference_fine_medium
    # Positive-order convergence requires the coarse-medium difference to exceed
    # the medium-fine difference.
    if target <= 1.0:
        return None
    low = 1.0e-6
    high = maximum_order
    f_low = _generalized_ratio(low, h_fine, h_medium, h_coarse) - target
    f_high = _generalized_ratio(high, h_fine, h_medium, h_coarse) - target
    if not math.isfinite(f_low) or not math.isfinite(f_high) or f_low * f_high > 0.0:
        return None
    for _ in range(100):
        mid = 0.5 * (low + high)
        f_mid = _generalized_ratio(mid, h_fine, h_medium, h_coarse) - target
        if abs(f_mid) < 1.0e-12:
            return mid
        if f_low * f_mid <= 0.0:
            high = mid
            f_high = f_mid
        else:
            low = mid
            f_low = f_mid
    return 0.5 * (low + high)

def convergence_estimate(
    *,
    fine: SeriesData,
    medium: SeriesData,
    coarse: SeriesData,
    h_fine: float,
    h_medium: float,
    h_coarse: float,
    absolute_scale_floor: float = 0.0,
    safety_factor: float = 1.25,
) -> ConvergenceEstimate:
    pair_fm = pairwise_error(fine, medium, absolute_scale_floor=absolute_scale_floor)
    pair_mc = pairwise_error(medium, coarse, absolute_scale_floor=absolute_scale_floor)
    zero_scale = max(
        pair_fm.reference_l2,
        pair_mc.reference_l2,
        float(absolute_scale_floor),
        1.0,
    )
    zero_tolerance = 64.0 * np.finfo(float).eps * zero_scale
    if (
        pair_fm.absolute_l2 <= zero_tolerance
        and pair_mc.absolute_l2 <= zero_tolerance
    ):
        return ConvergenceEstimate(
            pair_fine_medium=pair_fm,
            pair_medium_coarse=pair_mc,
            observed_order=None,
            refinement_ratio_fine_medium=h_medium / h_fine,
            refinement_ratio_medium_coarse=h_coarse / h_medium,
            fine_grid_gci_l2=0.0,
            asymptotic_trend=True,
            exact_agreement=True,
            reason=(
                "All three levels agree to floating-point tolerance; an observed order is not identifiable."
            ),
        )
    p = solve_observed_order(
        h_fine=h_fine,
        h_medium=h_medium,
        h_coarse=h_coarse,
        difference_fine_medium=pair_fm.absolute_l2,
        difference_medium_coarse=pair_mc.absolute_l2,
    )
    ratio_fm = h_medium / h_fine
    ratio_mc = h_coarse / h_medium
    if p is None:
        return ConvergenceEstimate(
            pair_fine_medium=pair_fm,
            pair_medium_coarse=pair_mc,
            observed_order=None,
            refinement_ratio_fine_medium=ratio_fm,
            refinement_ratio_medium_coarse=ratio_mc,
            fine_grid_gci_l2=None,
            asymptotic_trend=False,
            exact_agreement=False,
            reason=(
                "No positive observed order could be established from the three-level L2 differences."
            ),
        )
    denominator = ratio_fm**p - 1.0
    gci = None if denominator <= 0.0 else safety_factor * pair_fm.relative_l2 / denominator
    return ConvergenceEstimate(
        pair_fine_medium=pair_fm,
        pair_medium_coarse=pair_mc,
        observed_order=p,
        refinement_ratio_fine_medium=ratio_fm,
        refinement_ratio_medium_coarse=ratio_mc,
        fine_grid_gci_l2=gci,
        asymptotic_trend=pair_mc.absolute_l2 > pair_fm.absolute_l2,
        exact_agreement=False,
        reason="Three-level differences support a positive convergence trend.",
    )

def event_summary(series: SeriesData, *, threshold: float = 0.5) -> EventSummary:
    values = np.asarray(series.values, dtype=np.float64)
    if values.ndim != 1:
        raise QualificationError("binary event series must be one-dimensional")
    on = values >= threshold
    occurred = bool(np.any(on))
    first_on = float(series.time_s[int(np.argmax(on))]) if occurred else None
    transitions = int(np.count_nonzero(on[1:] != on[:-1]))
    duration = float(series.time_s[-1] - series.time_s[0])
    duty = (
        _trapezoid(on.astype(np.float64), series.time_s) / duration
        if duration > 0.0
        else float(on[-1])
    )
    return EventSummary(
        occurred=occurred,
        first_on_s=first_on,
        duty_fraction=duty,
        transition_count=transitions,
    )

def event_consistency(
    fine: EventSummary,
    medium: EventSummary,
    *,
    absolute_time_tolerance_s: float,
    period_s: float | None,
    period_fraction_tolerance: float,
    duty_fraction_tolerance: float,
    transition_count_tolerance: int,
) -> JsonObject:
    absolute_tolerance = _finite_positive(
        absolute_time_tolerance_s, "absolute event time tolerance"
    )
    fraction = _finite_positive(period_fraction_tolerance, "period fraction tolerance")
    duty_tolerance = _finite_positive(duty_fraction_tolerance, "duty tolerance")
    if transition_count_tolerance < 0:
        raise QualificationError("transition_count_tolerance must be >= 0")
    time_tolerance = absolute_tolerance
    if period_s is not None and math.isfinite(period_s) and period_s > 0.0:
        time_tolerance = max(time_tolerance, fraction * period_s)
    occurrence_match = fine.occurred == medium.occurred
    if fine.occurred and medium.occurred:
        assert fine.first_on_s is not None and medium.first_on_s is not None
        first_time_difference = abs(fine.first_on_s - medium.first_on_s)
        time_match = first_time_difference <= time_tolerance
    else:
        first_time_difference = None
        time_match = occurrence_match
    duty_difference = abs(fine.duty_fraction - medium.duty_fraction)
    transition_difference = abs(fine.transition_count - medium.transition_count)
    passed = (
        occurrence_match
        and time_match
        and duty_difference <= duty_tolerance
        and transition_difference <= transition_count_tolerance
    )
    return {
        "pass": passed,
        "occurrence_match": occurrence_match,
        "fine": fine.to_dict(),
        "medium": medium.to_dict(),
        "first_on_time_difference_s": first_time_difference,
        "allowed_first_on_time_difference_s": time_tolerance,
        "duty_fraction_difference": duty_difference,
        "allowed_duty_fraction_difference": duty_tolerance,
        "transition_count_difference": transition_difference,
        "allowed_transition_count_difference": transition_count_tolerance,
    }

def estimate_period_s(config: Mapping[str, Any]) -> float | None:
    drive = _require_mapping(config.get("drive"), "drive")
    times = drive.get("actual_speed_schedule_time_s")
    speeds = drive.get("actual_speed_schedule_rpm")
    rpm: float
    if isinstance(times, list) and isinstance(speeds, list) and speeds:
        rpm = abs(float(speeds[-1]))
    else:
        rpm = abs(float(drive.get("target_rpm", 0.0)))
    return None if rpm <= 1.0e-12 else 60.0 / rpm

def max_abs_observable(observables: Mapping[str, FloatArray], name: str) -> float | None:
    value = observables.get(name)
    if value is None:
        return None
    array = np.asarray(value, dtype=np.float64)
    if array.size == 0 or not np.all(np.isfinite(array)):
        return None
    return float(np.max(np.abs(array)))

def gate_convergence(
    estimate: ConvergenceEstimate,
    *,
    relative_l2_max: float,
    relative_linf_max: float,
    gci_l2_max: float,
    observed_order_min: float,
) -> JsonObject:
    thresholds = {
        "relative_l2_max": _finite_positive(relative_l2_max, "relative_l2_max"),
        "relative_linf_max": _finite_positive(relative_linf_max, "relative_linf_max"),
        "gci_l2_max": _finite_positive(gci_l2_max, "gci_l2_max"),
        "observed_order_min": _finite_positive(observed_order_min, "observed_order_min"),
    }
    order_pass = estimate.exact_agreement or (
        estimate.observed_order is not None
        and estimate.observed_order >= thresholds["observed_order_min"]
    )
    passed = (
        estimate.asymptotic_trend
        and order_pass
        and estimate.fine_grid_gci_l2 is not None
        and estimate.fine_grid_gci_l2 <= thresholds["gci_l2_max"]
        and estimate.pair_fine_medium.relative_l2 <= thresholds["relative_l2_max"]
        and estimate.pair_fine_medium.relative_linf <= thresholds["relative_linf_max"]
    )
    return {
        "pass": passed,
        "thresholds": thresholds,
        "estimate": estimate.to_dict(),
    }
