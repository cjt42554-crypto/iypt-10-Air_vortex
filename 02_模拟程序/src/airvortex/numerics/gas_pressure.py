"""Fail-closed local gas-channel pressure diagnostics for F86--F88.

The restricted-channel calculation deliberately accepts a fixed-physical-space
area derivative rather than a net gas-volume difference.  It returns the full
area, flux and pressure profiles so callers cannot silently replace the along-
channel maximum by an endpoint pressure difference.

This module evaluates the one-dimensional circular-tube approximation.  A
successful F88 screen is only a conditional screen of that approximation; it
does not establish the remaining shape, event-time, inertia, compressibility
or experimental error budgets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray


FloatArray = NDArray[np.float64]


class GasPathRegime(str, Enum):
    """Geometry/topology class selected before evaluating a pressure model."""

    RESTRICTED_CHANNEL = "restricted_channel"
    OPEN_RESERVOIR = "open_reservoir"
    CLOSED_GAS = "closed_gas"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"


class GasPressureStatus(str, Enum):
    """Outcome without conflating calculation with full physical validation."""

    EVALUATED = "evaluated"
    ASSUMPTION_FAILED = "assumption_failed"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"
    INVALID_INPUT = "invalid_input"


class AreaRateFrame(str, Enum):
    """Coordinate frame attached to a supplied cross-sectional area rate."""

    FIXED_SPACE = "fixed_space"
    GRID_COORDINATE = "grid_coordinate"


@dataclass(frozen=True, slots=True)
class PressureScaleInput:
    """Inputs for the conditional reference pressure in F88."""

    surface_tension_n_m: float
    density_difference_kg_m3: float
    gravity_m_s2: float
    centrifugal_potential_range_m2_s2: float
    liquid_density_kg_m3: float
    epsilon_g_max: float


@dataclass(frozen=True, slots=True)
class RestrictedChannelInput:
    """Instantaneous inputs for a single atmospheric restricted channel.

    ``z_m``, ``area_m2`` and ``area_rate_fixed_m2_s`` are nodal profiles on
    the same strictly increasing fixed physical-space coordinate.  The lower
    flow is formed from the moving-section identity

    ``Q_- = A_- * lower_boundary_speed_m_s + lower_relative_flux_m3_s``.

    The optional upper flow is a consistency check, not a second independent
    boundary condition.  When it is supplied, its explicit absolute tolerance
    must also be supplied.  A pressure budget is assessed only when both
    ``pressure_error_budget_pa`` and an independently justified
    ``derivative_pressure_error_bound_pa`` are present.  Supplying neither
    still permits an F86--F87 calculation; supplying only one leaves the
    calculation explicitly unverified.
    """

    z_m: NDArray[np.floating] | None = None
    area_m2: NDArray[np.floating] | None = None
    area_rate_fixed_m2_s: NDArray[np.floating] | None = None
    gas_viscosity_pa_s: float | None = None
    lower_boundary_speed_m_s: float | None = None
    lower_relative_flux_m3_s: float | None = None
    pressure_error_budget_pa: float | None = None
    derivative_pressure_error_bound_pa: float | None = None
    area_rate_frame: AreaRateFrame | None = None
    area_rate_source: str | None = None
    lower_boundary_source: str | None = None
    upper_pressure_source: str | None = None
    geometry_evidence_source: str | None = None
    approximation_evidence_source: str | None = None
    target_error_budget_source: str | None = None
    prescribed_upper_flow_m3_s: float | None = None
    upper_flow_tolerance_m3_s: float | None = None
    single_channel: bool | None = None
    near_circular: bool | None = None
    slender: bool | None = None
    upper_connected_to_atmosphere: bool | None = None
    quasi_steady_viscous: bool | None = None
    gas_inertia_negligible: bool | None = None
    compressibility_negligible: bool | None = None
    wall_resistance_applicable: bool | None = None
    pressure_scale: PressureScaleInput | None = None


@dataclass(frozen=True, slots=True)
class OpenReservoirInput:
    """Independent pressure-error evidence for an open gas reservoir.

    A channel radius is intentionally absent: an open headspace must not fail
    merely because no restricted-channel radius exists.  Missing error evidence
    or a missing explicit pressure budget remains unverified rather than being
    converted to zero pressure error.
    """

    pressure_error_bound_pa: float | None = None
    pressure_reference_pa: float | None = None
    pressure_error_budget_pa: float | None = None
    epsilon_g_max: float | None = None
    evidence_source: str | None = None


@dataclass(frozen=True, slots=True)
class UnavailableGasPathInput:
    """Explicitly represent a closed, unsupported or data-poor gas path."""

    regime: GasPathRegime
    reason: str


GasPressureInput: TypeAlias = (
    RestrictedChannelInput | OpenReservoirInput | UnavailableGasPathInput
)


def _empty_profile() -> FloatArray:
    result = np.empty(0, dtype=np.float64)
    result.flags.writeable = False
    return result


@dataclass(frozen=True, slots=True)
class GasPressureDiagnostic:
    """Auditable result of the local gas-pressure calculation.

    Profile arrays are populated only when F86--F87 have actually been
    evaluated.  ``epsilon_limit_satisfied`` refers only to the supplied F88 or
    open-reservoir screen.  ``pressure_budget_satisfied`` separately uses the
    F87a model bound plus the supplied derivative-error bound.  Neither Boolean
    establishes whole-model validity.
    """

    regime: GasPathRegime
    status: GasPressureStatus
    reasons: tuple[str, ...]
    z_m: FloatArray = field(default_factory=_empty_profile)
    area_m2: FloatArray = field(default_factory=_empty_profile)
    radius_m: FloatArray = field(default_factory=_empty_profile)
    area_rate_fixed_m2_s: FloatArray = field(default_factory=_empty_profile)
    flow_m3_s: FloatArray = field(default_factory=_empty_profile)
    pressure_relative_pa: FloatArray = field(default_factory=_empty_profile)
    lower_flow_m3_s: float | None = None
    upper_flow_m3_s: float | None = None
    lower_minus_upper_pressure_pa: float | None = None
    max_abs_pressure_pa: float | None = None
    flow_upper_bound_m3_s: float | None = None
    pressure_upper_bound_pa: float | None = None
    derivative_pressure_error_bound_pa: float | None = None
    combined_pressure_bound_pa: float | None = None
    pressure_error_budget_pa: float | None = None
    pressure_budget_satisfied: bool | None = None
    pressure_reference_pa: float | None = None
    epsilon_g: float | None = None
    epsilon_g_max: float | None = None
    epsilon_limit_satisfied: bool | None = None
    area_rate_source: str | None = None
    lower_boundary_source: str | None = None
    upper_pressure_source: str | None = None
    geometry_evidence_source: str | None = None
    approximation_evidence_source: str | None = None
    target_error_budget_source: str | None = None


def _readonly(values: NDArray[np.floating]) -> FloatArray:
    result = np.array(values, dtype=np.float64, copy=True)
    result.flags.writeable = False
    return result


def _is_finite_real(value: object) -> bool:
    return (
        not isinstance(value, (bool, np.bool_))
        and isinstance(value, (int, float, np.integer, np.floating))
        and math.isfinite(float(value))
    )


def _coerce_profile(value: object, name: str) -> FloatArray:
    array = np.asarray(value)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if array.size < 2:
        raise ValueError(f"{name} must contain at least two samples")
    if not np.issubdtype(array.dtype, np.number) or np.issubdtype(
        array.dtype, np.complexfloating
    ):
        raise ValueError(f"{name} must be real-valued")
    result = np.asarray(array, dtype=np.float64)
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain only finite values")
    return result


def _invalid(
    regime: GasPathRegime,
    reason: str,
    data: RestrictedChannelInput | None = None,
) -> GasPressureDiagnostic:
    return GasPressureDiagnostic(
        regime=regime,
        status=GasPressureStatus.INVALID_INPUT,
        reasons=(reason,),
        area_rate_source=data.area_rate_source if data is not None else None,
        lower_boundary_source=(
            data.lower_boundary_source if data is not None else None
        ),
        upper_pressure_source=(
            data.upper_pressure_source if data is not None else None
        ),
        geometry_evidence_source=(
            data.geometry_evidence_source if data is not None else None
        ),
        approximation_evidence_source=(
            data.approximation_evidence_source if data is not None else None
        ),
        target_error_budget_source=(
            data.target_error_budget_source if data is not None else None
        ),
    )


def _unavailable(
    regime: GasPathRegime,
    status: GasPressureStatus,
    reason: str,
    data: RestrictedChannelInput | None = None,
) -> GasPressureDiagnostic:
    return GasPressureDiagnostic(
        regime=regime,
        status=status,
        reasons=(reason,),
        area_rate_source=data.area_rate_source if data is not None else None,
        lower_boundary_source=(
            data.lower_boundary_source if data is not None else None
        ),
        upper_pressure_source=(
            data.upper_pressure_source if data is not None else None
        ),
        geometry_evidence_source=(
            data.geometry_evidence_source if data is not None else None
        ),
        approximation_evidence_source=(
            data.approximation_evidence_source if data is not None else None
        ),
        target_error_budget_source=(
            data.target_error_budget_source if data is not None else None
        ),
    )


def _cumulative_trapezoid(values: FloatArray, coordinate: FloatArray) -> FloatArray:
    increments = (
        0.5 * (values[:-1] + values[1:]) * np.diff(coordinate)
    )
    return np.concatenate((np.zeros(1, dtype=np.float64), np.cumsum(increments)))


def convert_area_rate_to_fixed_space(
    z_m: NDArray[np.floating],
    area_m2: NDArray[np.floating],
    area_rate_grid_m2_s: NDArray[np.floating],
    grid_velocity_m_s: NDArray[np.floating],
) -> FloatArray:
    r"""Convert an ALE/grid-coordinate area rate to a fixed-space rate.

    This applies

    ``partial_t A|z = partial_t A|xi - z_t|xi * partial_z A``.

    The caller remains responsible for demonstrating convergence of the
    supplied grid-coordinate time derivative and of this spatial derivative.
    At least three samples are required so both endpoints use second-order
    one-sided differences.
    """

    try:
        z = _coerce_profile(z_m, "z_m")
        area = _coerce_profile(area_m2, "area_m2")
        grid_rate = _coerce_profile(area_rate_grid_m2_s, "area_rate_grid_m2_s")
        grid_velocity = _coerce_profile(grid_velocity_m_s, "grid_velocity_m_s")
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if z.size < 3:
        raise ValueError("fixed-space conversion requires at least three samples")
    if not (area.shape == grid_rate.shape == grid_velocity.shape == z.shape):
        raise ValueError("fixed-space conversion profiles must have equal shape")
    if np.any(np.diff(z) <= 0.0):
        raise ValueError("z_m must be strictly increasing")
    if np.any(area < 0.0):
        raise ValueError("area_m2 must be non-negative")
    spatial_gradient = np.gradient(area, z, edge_order=2)
    return _readonly(grid_rate - grid_velocity * spatial_gradient)


def estimate_area_rate_at_fixed_space(
    previous_z_m: NDArray[np.floating],
    previous_area_m2: NDArray[np.floating],
    next_z_m: NDArray[np.floating],
    next_area_m2: NDArray[np.floating],
    evaluation_z_m: NDArray[np.floating],
    elapsed_time_s: float,
) -> FloatArray:
    """Estimate a fixed-space area rate from two accepted-step snapshots.

    Each area profile is interpolated to the same explicit physical-space
    coordinates before differencing.  Thus a moving mesh is not mistaken for
    local area evolution.  ``evaluation_z_m`` must lie inside the common
    physical domain; extrapolation is rejected.  Centered-in-time use requires
    snapshots bracketing the reported time, while consecutive snapshots give
    the interval-average rate.  A first sample has no such derivative and must
    remain unverified at the caller.
    """

    try:
        previous_z = _coerce_profile(previous_z_m, "previous_z_m")
        previous_area = _coerce_profile(previous_area_m2, "previous_area_m2")
        next_z = _coerce_profile(next_z_m, "next_z_m")
        next_area = _coerce_profile(next_area_m2, "next_area_m2")
        evaluation_z = _coerce_profile(evaluation_z_m, "evaluation_z_m")
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if previous_z.shape != previous_area.shape:
        raise ValueError("previous coordinate and area profiles must have equal shape")
    if next_z.shape != next_area.shape:
        raise ValueError("next coordinate and area profiles must have equal shape")
    for name, coordinate in (
        ("previous_z_m", previous_z),
        ("next_z_m", next_z),
        ("evaluation_z_m", evaluation_z),
    ):
        if np.any(np.diff(coordinate) <= 0.0):
            raise ValueError(f"{name} must be strictly increasing")
    if np.any(previous_area < 0.0) or np.any(next_area < 0.0):
        raise ValueError("area profiles must be non-negative")
    if not _is_finite_real(elapsed_time_s) or float(elapsed_time_s) <= 0.0:
        raise ValueError("elapsed_time_s must be finite and positive")
    common_lower = max(float(previous_z[0]), float(next_z[0]))
    common_upper = min(float(previous_z[-1]), float(next_z[-1]))
    if (
        common_lower > common_upper
        or float(evaluation_z[0]) < common_lower
        or float(evaluation_z[-1]) > common_upper
    ):
        raise ValueError(
            "evaluation_z_m must lie inside the snapshots' common physical domain"
        )
    previous_fixed = np.interp(evaluation_z, previous_z, previous_area)
    next_fixed = np.interp(evaluation_z, next_z, next_area)
    return _readonly((next_fixed - previous_fixed) / float(elapsed_time_s))


def _validate_assumption_flags(
    data: RestrictedChannelInput,
) -> GasPressureDiagnostic | None:
    geometry_flags = (
        ("single_channel", data.single_channel, "branched_channel"),
        ("near_circular", data.near_circular, "non_circular_channel"),
        ("slender", data.slender, "non_slender_channel"),
    )
    physical_flags = (
        (
            "upper_connected_to_atmosphere",
            data.upper_connected_to_atmosphere,
            "closed_or_unknown_upper_boundary",
        ),
        (
            "quasi_steady_viscous",
            data.quasi_steady_viscous,
            "quasi_steady_viscous_assumption_failed",
        ),
        (
            "gas_inertia_negligible",
            data.gas_inertia_negligible,
            "gas_inertia_not_negligible",
        ),
        (
            "compressibility_negligible",
            data.compressibility_negligible,
            "gas_compressibility_not_negligible",
        ),
        (
            "wall_resistance_applicable",
            data.wall_resistance_applicable,
            "circular_wall_resistance_not_applicable",
        ),
    )
    for name, value, _ in geometry_flags + physical_flags:
        if value is not None and not isinstance(value, (bool, np.bool_)):
            return _invalid(
                GasPathRegime.RESTRICTED_CHANNEL,
                f"{name}_must_be_boolean_or_none",
                data,
            )
    for _, value, reason in geometry_flags:
        if value is False:
            return _unavailable(
                GasPathRegime.RESTRICTED_CHANNEL,
                GasPressureStatus.NOT_APPLICABLE,
                reason,
                data,
            )
    for _, value, reason in physical_flags:
        if value is False:
            return _unavailable(
                GasPathRegime.RESTRICTED_CHANNEL,
                GasPressureStatus.ASSUMPTION_FAILED,
                reason,
                data,
            )
    missing = [
        name
        for name, value, _ in geometry_flags + physical_flags
        if value is None
    ]
    if missing:
        return _unavailable(
            GasPathRegime.RESTRICTED_CHANNEL,
            GasPressureStatus.UNVERIFIED,
            "missing_assumption_evidence:" + ",".join(missing),
            data,
        )
    return None


def _validate_pressure_scale(scale: PressureScaleInput) -> str | None:
    checks = (
        ("surface_tension_n_m", scale.surface_tension_n_m, False),
        ("density_difference_kg_m3", scale.density_difference_kg_m3, False),
        ("gravity_m_s2", scale.gravity_m_s2, False),
        (
            "centrifugal_potential_range_m2_s2",
            scale.centrifugal_potential_range_m2_s2,
            False,
        ),
        ("liquid_density_kg_m3", scale.liquid_density_kg_m3, True),
        ("epsilon_g_max", scale.epsilon_g_max, False),
    )
    for name, value, strictly_positive in checks:
        if not _is_finite_real(value):
            return f"{name}_must_be_finite"
        if (strictly_positive and float(value) <= 0.0) or (
            not strictly_positive and float(value) < 0.0
        ):
            qualifier = "positive" if strictly_positive else "non_negative"
            return f"{name}_must_be_{qualifier}"
    return None


def _diagnose_restricted(data: RestrictedChannelInput) -> GasPressureDiagnostic:
    assumption_result = _validate_assumption_flags(data)
    if assumption_result is not None:
        return assumption_result

    required = (
        ("z_m", data.z_m),
        ("area_m2", data.area_m2),
        ("area_rate_fixed_m2_s", data.area_rate_fixed_m2_s),
        ("gas_viscosity_pa_s", data.gas_viscosity_pa_s),
        ("lower_boundary_speed_m_s", data.lower_boundary_speed_m_s),
        ("lower_relative_flux_m3_s", data.lower_relative_flux_m3_s),
        ("area_rate_frame", data.area_rate_frame),
        ("area_rate_source", data.area_rate_source),
        ("lower_boundary_source", data.lower_boundary_source),
        ("upper_pressure_source", data.upper_pressure_source),
        ("geometry_evidence_source", data.geometry_evidence_source),
        ("approximation_evidence_source", data.approximation_evidence_source),
        ("target_error_budget_source", data.target_error_budget_source),
    )
    missing = [name for name, value in required if value is None]
    if missing:
        return _unavailable(
            GasPathRegime.RESTRICTED_CHANNEL,
            GasPressureStatus.UNVERIFIED,
            "missing_required_data:" + ",".join(missing),
            data,
        )
    if not isinstance(data.area_rate_frame, AreaRateFrame):
        return _invalid(
            GasPathRegime.RESTRICTED_CHANNEL,
            "area_rate_frame_must_be_an_AreaRateFrame",
            data,
        )
    if data.area_rate_frame is not AreaRateFrame.FIXED_SPACE:
        return _unavailable(
            GasPathRegime.RESTRICTED_CHANNEL,
            GasPressureStatus.UNVERIFIED,
            "moving_grid_area_rate_not_converted_to_fixed_space",
            data,
        )
    for name, value in (
        ("area_rate_source", data.area_rate_source),
        ("lower_boundary_source", data.lower_boundary_source),
        ("upper_pressure_source", data.upper_pressure_source),
        ("geometry_evidence_source", data.geometry_evidence_source),
        ("approximation_evidence_source", data.approximation_evidence_source),
        ("target_error_budget_source", data.target_error_budget_source),
    ):
        if not isinstance(value, str) or not value.strip():
            return _invalid(
                GasPathRegime.RESTRICTED_CHANNEL,
                f"{name}_must_be_non_empty",
                data,
            )
    for name, value in (
        ("gas_viscosity_pa_s", data.gas_viscosity_pa_s),
        ("lower_boundary_speed_m_s", data.lower_boundary_speed_m_s),
        ("lower_relative_flux_m3_s", data.lower_relative_flux_m3_s),
    ):
        if not _is_finite_real(value):
            return _invalid(
                GasPathRegime.RESTRICTED_CHANNEL,
                f"{name}_must_be_finite",
                data,
            )
    if float(data.gas_viscosity_pa_s) <= 0.0:
        return _invalid(
            GasPathRegime.RESTRICTED_CHANNEL,
            "gas_viscosity_pa_s_must_be_positive",
            data,
        )
    if data.pressure_error_budget_pa is not None:
        if not _is_finite_real(data.pressure_error_budget_pa) or float(
            data.pressure_error_budget_pa
        ) <= 0.0:
            return _invalid(
                GasPathRegime.RESTRICTED_CHANNEL,
                "pressure_error_budget_pa_must_be_finite_and_positive",
                data,
            )
    if data.derivative_pressure_error_bound_pa is not None:
        if not _is_finite_real(data.derivative_pressure_error_bound_pa) or float(
            data.derivative_pressure_error_bound_pa
        ) < 0.0:
            return _invalid(
                GasPathRegime.RESTRICTED_CHANNEL,
                "derivative_pressure_error_bound_pa_must_be_finite_and_non_negative",
                data,
            )

    try:
        z = _coerce_profile(data.z_m, "z_m")
        area = _coerce_profile(data.area_m2, "area_m2")
        area_rate = _coerce_profile(
            data.area_rate_fixed_m2_s, "area_rate_fixed_m2_s"
        )
    except (TypeError, ValueError) as exc:
        return _invalid(GasPathRegime.RESTRICTED_CHANNEL, str(exc), data)
    if not (z.shape == area.shape == area_rate.shape):
        return _invalid(
            GasPathRegime.RESTRICTED_CHANNEL,
            "channel_profiles_must_have_equal_shape",
            data,
        )
    if np.any(np.diff(z) <= 0.0):
        return _invalid(
            GasPathRegime.RESTRICTED_CHANNEL,
            "z_m_must_be_strictly_increasing",
            data,
        )
    if np.any(area < 0.0):
        return _invalid(
            GasPathRegime.RESTRICTED_CHANNEL,
            "area_m2_must_be_non_negative",
            data,
        )
    if np.any(area == 0.0):
        return _unavailable(
            GasPathRegime.RESTRICTED_CHANNEL,
            GasPressureStatus.NOT_APPLICABLE,
            "zero_radius_channel",
            data,
        )

    if data.prescribed_upper_flow_m3_s is not None:
        if not _is_finite_real(data.prescribed_upper_flow_m3_s):
            return _invalid(
                GasPathRegime.RESTRICTED_CHANNEL,
                "prescribed_upper_flow_m3_s_must_be_finite",
                data,
            )
        if data.upper_flow_tolerance_m3_s is None:
            return _unavailable(
                GasPathRegime.RESTRICTED_CHANNEL,
                GasPressureStatus.UNVERIFIED,
                "upper_flow_tolerance_m3_s_required_with_prescribed_upper_flow",
                data,
            )
        if not _is_finite_real(data.upper_flow_tolerance_m3_s) or float(
            data.upper_flow_tolerance_m3_s
        ) < 0.0:
            return _invalid(
                GasPathRegime.RESTRICTED_CHANNEL,
                "upper_flow_tolerance_m3_s_must_be_finite_and_non_negative",
                data,
            )
    elif data.upper_flow_tolerance_m3_s is not None:
        return _invalid(
            GasPathRegime.RESTRICTED_CHANNEL,
            "upper_flow_tolerance_without_prescribed_upper_flow",
            data,
        )

    if data.pressure_scale is not None:
        scale_error = _validate_pressure_scale(data.pressure_scale)
        if scale_error is not None:
            return _invalid(GasPathRegime.RESTRICTED_CHANNEL, scale_error, data)

    lower_flow = (
        area[0] * float(data.lower_boundary_speed_m_s)
        + float(data.lower_relative_flux_m3_s)
    )
    area_rate_integral = _cumulative_trapezoid(area_rate, z)
    flow = lower_flow - area_rate_integral
    flow_upper_bound = abs(lower_flow) + float(
        _cumulative_trapezoid(np.abs(area_rate), z)[-1]
    )

    if data.prescribed_upper_flow_m3_s is not None:
        mismatch = abs(float(flow[-1]) - float(data.prescribed_upper_flow_m3_s))
        if mismatch > float(data.upper_flow_tolerance_m3_s):
            return _unavailable(
                GasPathRegime.RESTRICTED_CHANNEL,
                GasPressureStatus.ASSUMPTION_FAILED,
                "upper_flow_inconsistent_with_f86_continuity",
                data,
            )

    viscosity = float(data.gas_viscosity_pa_s)
    radius = np.sqrt(area / math.pi)
    pressure_gradient = 8.0 * math.pi * viscosity * flow / area**2
    pressure_antiderivative = _cumulative_trapezoid(pressure_gradient, z)
    pressure = pressure_antiderivative[-1] - pressure_antiderivative
    max_abs_pressure = float(np.max(np.abs(pressure)))
    length = float(z[-1] - z[0])
    minimum_radius = float(np.min(radius))
    pressure_upper_bound = (
        8.0
        * viscosity
        * length
        * flow_upper_bound
        / (math.pi * minimum_radius**4)
    )

    derivative_pressure_error = (
        None
        if data.derivative_pressure_error_bound_pa is None
        else float(data.derivative_pressure_error_bound_pa)
    )
    combined_pressure_bound = (
        None
        if derivative_pressure_error is None
        else pressure_upper_bound + derivative_pressure_error
    )
    pressure_error_budget = (
        None
        if data.pressure_error_budget_pa is None
        else float(data.pressure_error_budget_pa)
    )
    pressure_budget_satisfied: bool | None = None
    status = GasPressureStatus.EVALUATED
    reasons = ["f86_f87_evaluated"]
    if pressure_error_budget is None and derivative_pressure_error is None:
        reasons.append("pressure_budget_not_provided")
    elif pressure_error_budget is None or derivative_pressure_error is None:
        status = GasPressureStatus.UNVERIFIED
        reasons.append("pressure_budget_inputs_incomplete")
    else:
        pressure_budget_satisfied = combined_pressure_bound <= pressure_error_budget
        if pressure_budget_satisfied:
            reasons.append("combined_pressure_bound_within_budget")
        else:
            status = GasPressureStatus.ASSUMPTION_FAILED
            reasons.append("combined_pressure_bound_exceeds_budget")
    pressure_reference: float | None = None
    epsilon_g: float | None = None
    epsilon_max: float | None = None
    epsilon_satisfied: bool | None = None
    if data.pressure_scale is not None:
        scale = data.pressure_scale
        pressure_reference = max(
            float(scale.surface_tension_n_m) / minimum_radius,
            float(scale.density_difference_kg_m3)
            * float(scale.gravity_m_s2)
            * length,
            float(scale.liquid_density_kg_m3)
            * float(scale.centrifugal_potential_range_m2_s2),
        )
        epsilon_max = float(scale.epsilon_g_max)
        if pressure_reference == 0.0:
            if status is GasPressureStatus.EVALUATED:
                status = GasPressureStatus.UNVERIFIED
            reasons.append("f88_pressure_reference_is_zero")
        else:
            epsilon_g = pressure_upper_bound / pressure_reference
            epsilon_satisfied = epsilon_g <= epsilon_max
            if epsilon_satisfied:
                reasons.append("f88_conditional_screen_passed")
            else:
                status = GasPressureStatus.ASSUMPTION_FAILED
                reasons.append("f88_conditional_screen_exceeded")

    return GasPressureDiagnostic(
        regime=GasPathRegime.RESTRICTED_CHANNEL,
        status=status,
        reasons=tuple(reasons),
        z_m=_readonly(z),
        area_m2=_readonly(area),
        radius_m=_readonly(radius),
        area_rate_fixed_m2_s=_readonly(area_rate),
        flow_m3_s=_readonly(flow),
        pressure_relative_pa=_readonly(pressure),
        lower_flow_m3_s=float(flow[0]),
        upper_flow_m3_s=float(flow[-1]),
        lower_minus_upper_pressure_pa=float(pressure[0] - pressure[-1]),
        max_abs_pressure_pa=max_abs_pressure,
        flow_upper_bound_m3_s=flow_upper_bound,
        pressure_upper_bound_pa=float(pressure_upper_bound),
        derivative_pressure_error_bound_pa=derivative_pressure_error,
        combined_pressure_bound_pa=(
            None
            if combined_pressure_bound is None
            else float(combined_pressure_bound)
        ),
        pressure_error_budget_pa=pressure_error_budget,
        pressure_budget_satisfied=pressure_budget_satisfied,
        pressure_reference_pa=pressure_reference,
        epsilon_g=epsilon_g,
        epsilon_g_max=epsilon_max,
        epsilon_limit_satisfied=epsilon_satisfied,
        area_rate_source=data.area_rate_source,
        lower_boundary_source=data.lower_boundary_source,
        upper_pressure_source=data.upper_pressure_source,
        geometry_evidence_source=data.geometry_evidence_source,
        approximation_evidence_source=data.approximation_evidence_source,
        target_error_budget_source=data.target_error_budget_source,
    )


def _diagnose_open_reservoir(data: OpenReservoirInput) -> GasPressureDiagnostic:
    required = (
        ("pressure_error_bound_pa", data.pressure_error_bound_pa),
        ("pressure_reference_pa", data.pressure_reference_pa),
        ("pressure_error_budget_pa", data.pressure_error_budget_pa),
        ("epsilon_g_max", data.epsilon_g_max),
        ("evidence_source", data.evidence_source),
    )
    missing = [name for name, value in required if value is None]
    if missing:
        return _unavailable(
            GasPathRegime.OPEN_RESERVOIR,
            GasPressureStatus.UNVERIFIED,
            "missing_open_reservoir_evidence:" + ",".join(missing),
        )
    if not isinstance(data.evidence_source, str) or not data.evidence_source.strip():
        return _invalid(
            GasPathRegime.OPEN_RESERVOIR,
            "evidence_source_must_be_non_empty",
        )
    for name, value, strictly_positive in (
        ("pressure_error_bound_pa", data.pressure_error_bound_pa, False),
        ("pressure_reference_pa", data.pressure_reference_pa, True),
        ("pressure_error_budget_pa", data.pressure_error_budget_pa, True),
        ("epsilon_g_max", data.epsilon_g_max, False),
    ):
        if not _is_finite_real(value):
            return _invalid(
                GasPathRegime.OPEN_RESERVOIR, f"{name}_must_be_finite"
            )
        if (strictly_positive and float(value) <= 0.0) or (
            not strictly_positive and float(value) < 0.0
        ):
            qualifier = "positive" if strictly_positive else "non_negative"
            return _invalid(
                GasPathRegime.OPEN_RESERVOIR,
                f"{name}_must_be_{qualifier}",
            )
    bound = float(data.pressure_error_bound_pa)
    reference = float(data.pressure_reference_pa)
    pressure_error_budget = float(data.pressure_error_budget_pa)
    epsilon_max = float(data.epsilon_g_max)
    epsilon = bound / reference
    satisfied = epsilon <= epsilon_max
    pressure_budget_satisfied = bound <= pressure_error_budget
    overall_satisfied = satisfied and pressure_budget_satisfied
    return GasPressureDiagnostic(
        regime=GasPathRegime.OPEN_RESERVOIR,
        status=(
            GasPressureStatus.EVALUATED
            if overall_satisfied
            else GasPressureStatus.ASSUMPTION_FAILED
        ),
        reasons=(
            (
                "open_reservoir_pressure_bound_within_budget"
                if pressure_budget_satisfied
                else "open_reservoir_pressure_bound_exceeds_budget"
            ),
            (
                "open_reservoir_conditional_screen_passed"
                if satisfied
                else "open_reservoir_conditional_screen_exceeded"
            ),
        ),
        max_abs_pressure_pa=bound,
        pressure_upper_bound_pa=bound,
        combined_pressure_bound_pa=bound,
        pressure_error_budget_pa=pressure_error_budget,
        pressure_budget_satisfied=pressure_budget_satisfied,
        pressure_reference_pa=reference,
        epsilon_g=epsilon,
        epsilon_g_max=epsilon_max,
        epsilon_limit_satisfied=satisfied,
        upper_pressure_source=data.evidence_source,
    )


def _diagnose_unavailable(data: UnavailableGasPathInput) -> GasPressureDiagnostic:
    if not isinstance(data.regime, GasPathRegime):
        return _invalid(
            GasPathRegime.UNSUPPORTED_GEOMETRY,
            "regime_must_be_a_GasPathRegime",
        )
    if not isinstance(data.reason, str) or not data.reason.strip():
        return _invalid(data.regime, "unavailable_reason_must_be_non_empty")
    if data.regime is GasPathRegime.CLOSED_GAS:
        status = GasPressureStatus.ASSUMPTION_FAILED
    elif data.regime is GasPathRegime.UNSUPPORTED_GEOMETRY:
        status = GasPressureStatus.NOT_APPLICABLE
    else:
        status = GasPressureStatus.UNVERIFIED
    return _unavailable(data.regime, status, data.reason)


def diagnose_gas_pressure(data: GasPressureInput) -> GasPressureDiagnostic:
    """Evaluate the selected local gas-pressure path without implicit fallback."""

    if isinstance(data, RestrictedChannelInput):
        return _diagnose_restricted(data)
    if isinstance(data, OpenReservoirInput):
        return _diagnose_open_reservoir(data)
    if isinstance(data, UnavailableGasPathInput):
        return _diagnose_unavailable(data)
    return _invalid(
        GasPathRegime.UNSUPPORTED_GEOMETRY,
        "unsupported_gas_pressure_input_type",
    )


__all__ = [
    "AreaRateFrame",
    "GasPathRegime",
    "GasPressureDiagnostic",
    "GasPressureInput",
    "GasPressureStatus",
    "OpenReservoirInput",
    "PressureScaleInput",
    "RestrictedChannelInput",
    "UnavailableGasPathInput",
    "convert_area_rate_to_fixed_space",
    "diagnose_gas_pressure",
    "estimate_area_rate_at_fixed_space",
]
