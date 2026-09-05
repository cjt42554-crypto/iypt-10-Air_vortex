"""E0 coordinate, shutter, radial-observation, and CP-initial-state bridge.

The functions here are intentionally small and fail closed.  They neither
reconstruct a free surface from camera intensity nor qualify a real optical
system.  They consume already calibrated inputs, retain projected-area weights,
and refuse unqualified coordinate, field-of-view, axisymmetry, or volume paths.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from airvortex.backends.forward_topology import ForwardState
from airvortex.numerics.interface import surface_profile
from airvortex.numerics.variational_interface import (
    VariationalGeometry,
    liquid_volume_m3,
    smooth_liquid_fraction,
)

from ._common import DataValidationError


FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
REAL_MEASUREMENT = "REAL_MEASUREMENT"
SYNTHETIC = "SYNTHETIC"
SCIENTIFIC = "SCIENTIFIC"
SYNTHETIC_TEST_ONLY = "SYNTHETIC_TEST_ONLY"


def _finite_array(
    value: Any,
    name: str,
    *,
    ndim: int | None = None,
    shape: tuple[int, ...] | None = None,
) -> FloatArray:
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"{name} must be numeric") from exc
    if ndim is not None and result.ndim != ndim:
        raise DataValidationError(f"{name} must have {ndim} dimensions")
    if shape is not None and result.shape != shape:
        raise DataValidationError(f"{name} must have shape {shape}")
    if result.size == 0 or not np.all(np.isfinite(result)):
        raise DataValidationError(f"{name} must be non-empty and finite")
    return result


def _readonly(value: NDArray[Any]) -> NDArray[Any]:
    result = np.asarray(value).copy()
    result.setflags(write=False)
    return result


@dataclass(frozen=True, slots=True)
class PlanarCoordinateCalibration:
    """Frozen planar pixel-to-container map for physical surface footprints."""

    calibration_id: str
    homography_pixel_to_container_m: FloatArray
    valid_pixel_bounds_uv: tuple[float, float, float, float]
    expected_jacobian_sign: int
    status: str
    evidence_kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.calibration_id, str) or not self.calibration_id.strip():
            raise DataValidationError("coordinate calibration_id cannot be empty")
        homography = _finite_array(
            self.homography_pixel_to_container_m,
            "homography_pixel_to_container_m",
            shape=(3, 3),
        )
        if abs(float(np.linalg.det(homography))) <= 1.0e-18:
            raise DataValidationError("pixel-to-container homography is singular")
        bounds = _finite_array(
            self.valid_pixel_bounds_uv, "valid_pixel_bounds_uv", shape=(4,)
        )
        if not bounds[0] < bounds[1] or not bounds[2] < bounds[3]:
            raise DataValidationError("valid pixel bounds must have positive extent")
        if self.expected_jacobian_sign not in {-1, 1}:
            raise DataValidationError("expected_jacobian_sign must be -1 or +1")
        if self.status not in {"QUALIFIED", "UNQUALIFIED", SYNTHETIC_TEST_ONLY}:
            raise DataValidationError("unsupported coordinate calibration status")
        if self.evidence_kind not in {REAL_MEASUREMENT, SYNTHETIC}:
            raise DataValidationError("unsupported coordinate evidence_kind")
        object.__setattr__(self, "homography_pixel_to_container_m", _readonly(homography))
        object.__setattr__(
            self, "valid_pixel_bounds_uv", tuple(float(item) for item in bounds)
        )


@dataclass(frozen=True, slots=True)
class MappedFootprints:
    container_xy_m: FloatArray
    projected_area_m2: FloatArray
    jacobian_m2_per_pixel2: FloatArray

    def __post_init__(self) -> None:
        xy = _finite_array(self.container_xy_m, "container_xy_m", ndim=2)
        if xy.shape[1:] != (2,):
            raise DataValidationError("container_xy_m must have shape (samples, 2)")
        area = _finite_array(
            self.projected_area_m2, "projected_area_m2", shape=(len(xy),)
        )
        jacobian = _finite_array(
            self.jacobian_m2_per_pixel2,
            "jacobian_m2_per_pixel2",
            shape=(len(xy),),
        )
        if np.any(area <= 0.0) or np.any(jacobian == 0.0):
            raise DataValidationError("mapped footprint areas and Jacobians must be nonzero")
        object.__setattr__(self, "container_xy_m", _readonly(xy))
        object.__setattr__(self, "projected_area_m2", _readonly(area))
        object.__setattr__(self, "jacobian_m2_per_pixel2", _readonly(jacobian))


def _require_coordinate_scope(
    calibration: PlanarCoordinateCalibration, allow_synthetic_test: bool
) -> None:
    if calibration.status == "UNQUALIFIED":
        raise DataValidationError("NO_GO_COORDINATE_CALIBRATION_UNQUALIFIED")
    if calibration.evidence_kind == REAL_MEASUREMENT:
        if calibration.status != "QUALIFIED":
            raise DataValidationError("NO_GO_COORDINATE_CALIBRATION_UNQUALIFIED")
        return
    if not allow_synthetic_test or calibration.status != SYNTHETIC_TEST_ONLY:
        raise DataValidationError("SYNTHETIC coordinate map is software-test-only")


def map_pixel_footprints_to_container(
    pixel_uv: Any,
    pixel_footprint_area_px2: Any,
    calibration: PlanarCoordinateCalibration,
    *,
    allow_synthetic_test: bool = False,
) -> MappedFootprints:
    """Map pixel centres and footprints to container ``x_c,y_c`` in metres."""

    if not isinstance(calibration, PlanarCoordinateCalibration):
        raise TypeError("calibration must be a PlanarCoordinateCalibration")
    _require_coordinate_scope(calibration, allow_synthetic_test)
    uv = _finite_array(pixel_uv, "pixel_uv", ndim=2)
    if uv.shape[1:] != (2,):
        raise DataValidationError("pixel_uv must have shape (samples, 2)")
    pixel_area = _finite_array(
        pixel_footprint_area_px2,
        "pixel_footprint_area_px2",
        shape=(len(uv),),
    )
    if np.any(pixel_area <= 0.0):
        raise DataValidationError("pixel footprint areas must be positive")
    u0, u1, v0, v1 = calibration.valid_pixel_bounds_uv
    if np.any((uv[:, 0] < u0) | (uv[:, 0] > u1)) or np.any(
        (uv[:, 1] < v0) | (uv[:, 1] > v1)
    ):
        raise DataValidationError("NO_GO_COORDINATE_SAMPLE_OUTSIDE_CALIBRATED_FOV")

    h = calibration.homography_pixel_to_container_m
    u = uv[:, 0]
    v = uv[:, 1]
    x_num = h[0, 0] * u + h[0, 1] * v + h[0, 2]
    y_num = h[1, 0] * u + h[1, 1] * v + h[1, 2]
    denominator = h[2, 0] * u + h[2, 1] * v + h[2, 2]
    if np.any(np.abs(denominator) <= 1.0e-14):
        raise DataValidationError("NO_GO_COORDINATE_HOMOGRAPHY_DENOMINATOR")
    x = x_num / denominator
    y = y_num / denominator

    denominator_sq = denominator**2
    dx_du = (h[0, 0] * denominator - h[2, 0] * x_num) / denominator_sq
    dx_dv = (h[0, 1] * denominator - h[2, 1] * x_num) / denominator_sq
    dy_du = (h[1, 0] * denominator - h[2, 0] * y_num) / denominator_sq
    dy_dv = (h[1, 1] * denominator - h[2, 1] * y_num) / denominator_sq
    jacobian = dx_du * dy_dv - dx_dv * dy_du
    if np.any(np.sign(jacobian) != calibration.expected_jacobian_sign):
        raise DataValidationError("NO_GO_COORDINATE_HANDEDNESS_OR_JACOBIAN_SIGN")
    if np.any(np.abs(jacobian) <= 1.0e-18):
        raise DataValidationError("NO_GO_COORDINATE_DEGENERATE_PIXEL_FOOTPRINT")
    return MappedFootprints(
        container_xy_m=np.column_stack((x, y)),
        projected_area_m2=np.abs(jacobian) * pixel_area,
        jacobian_m2_per_pixel2=jacobian,
    )


def solver_to_container_xy(solver_xy_m: Any, bar_angle_rad: float) -> FloatArray:
    """Apply ``x_c = R(+theta_b) x_b`` with the frozen sign convention."""

    points = _finite_array(solver_xy_m, "solver_xy_m", ndim=2)
    if points.shape[1:] != (2,):
        raise DataValidationError("solver_xy_m must have shape (samples, 2)")
    if not math.isfinite(float(bar_angle_rad)):
        raise DataValidationError("bar_angle_rad must be finite")
    cosine = math.cos(float(bar_angle_rad))
    sine = math.sin(float(bar_angle_rad))
    rotation = np.array([[cosine, -sine], [sine, cosine]], dtype=np.float64)
    return points @ rotation.T


def container_to_solver_xy(container_xy_m: Any, bar_angle_rad: float) -> FloatArray:
    """Apply ``x_b = R(-theta_b) x_c`` with the frozen sign convention."""

    return solver_to_container_xy(container_xy_m, -float(bar_angle_rad))


@dataclass(frozen=True, slots=True)
class GlobalShutterSchedule:
    """Global-shutter exposure windows and one normalized response shape."""

    exposure_start_s: FloatArray
    exposure_end_s: FloatArray
    response_fraction: FloatArray
    response_weight: FloatArray

    def __post_init__(self) -> None:
        starts = _finite_array(self.exposure_start_s, "exposure_start_s", ndim=1)
        ends = _finite_array(
            self.exposure_end_s, "exposure_end_s", shape=starts.shape
        )
        fractions = _finite_array(self.response_fraction, "response_fraction", ndim=1)
        weights = _finite_array(
            self.response_weight, "response_weight", shape=fractions.shape
        )
        if np.any(ends <= starts):
            raise DataValidationError("exposure windows must have positive duration")
        if len(starts) > 1 and not np.all(np.diff(starts) > 0.0):
            raise DataValidationError("exposure starts must be strictly increasing")
        if len(fractions) < 2 or not np.all(np.diff(fractions) > 0.0):
            raise DataValidationError("response fractions must be strictly increasing")
        if fractions[0] != 0.0 or fractions[-1] != 1.0:
            raise DataValidationError("response fractions must span 0 through 1")
        if np.any(weights < 0.0) or not np.any(weights > 0.0):
            raise DataValidationError("shutter response must be nonnegative and nonzero")
        object.__setattr__(self, "exposure_start_s", _readonly(starts))
        object.__setattr__(self, "exposure_end_s", _readonly(ends))
        object.__setattr__(self, "response_fraction", _readonly(fractions))
        object.__setattr__(self, "response_weight", _readonly(weights))


@dataclass(frozen=True, slots=True)
class ExposureAveraged:
    effective_time_s: FloatArray
    values: FloatArray

    def __post_init__(self) -> None:
        time = _finite_array(self.effective_time_s, "effective_time_s", ndim=1)
        values = _finite_array(self.values, "exposure-averaged values")
        if values.shape[0] != len(time):
            raise DataValidationError("exposure values first dimension must be frames")
        object.__setattr__(self, "effective_time_s", _readonly(time))
        object.__setattr__(self, "values", _readonly(values))


def _linear_sample(time_s: FloatArray, values: FloatArray, targets: FloatArray) -> FloatArray:
    if targets[0] < time_s[0] or targets[-1] > time_s[-1]:
        raise DataValidationError("NO_GO_EXPOSURE_WINDOW_OUTSIDE_SOURCE_TIME")
    right = np.searchsorted(time_s, targets, side="right")
    right = np.clip(right, 1, len(time_s) - 1)
    left = right - 1
    span = time_s[right] - time_s[left]
    fraction = (targets - time_s[left]) / span
    expansion = (len(targets),) + (1,) * (values.ndim - 1)
    fraction = fraction.reshape(expansion)
    return (1.0 - fraction) * values[left] + fraction * values[right]


def apply_global_shutter(
    source_time_s: Any,
    source_values: Any,
    schedule: GlobalShutterSchedule,
) -> ExposureAveraged:
    """Linearly sample a signal and integrate it against the shutter response.

    For camera data the signal should be intensity before a nonlinear optical
    reconstruction.  Height-domain use requires an independently qualified
    separable exposure approximation; this function does not grant that status.
    """

    time = _finite_array(source_time_s, "source_time_s", ndim=1)
    values = _finite_array(source_values, "source_values")
    if values.shape[0] != len(time):
        raise DataValidationError("source_values first dimension must match time")
    if len(time) < 2 or not np.all(np.diff(time) > 0.0):
        raise DataValidationError("source time must be strictly increasing")
    if not isinstance(schedule, GlobalShutterSchedule):
        raise TypeError("schedule must be a GlobalShutterSchedule")

    averaged: list[FloatArray] = []
    effective_time: list[float] = []
    for start, end in zip(schedule.exposure_start_s, schedule.exposure_end_s):
        response_time = start + schedule.response_fraction * (end - start)
        # Retain every source and shutter knot. Sampling only the shutter
        # endpoints can erase an entire oscillation inside one exposure.
        knots = np.unique(np.concatenate((response_time, time[(time > start) & (time < end)])))
        midpoints = .5 * (knots[:-1] + knots[1:])
        widths = np.diff(knots)
        sampled = _linear_sample(time, values, knots)
        midvalues = _linear_sample(time, values, midpoints)
        weights = np.interp(knots, response_time, schedule.response_weight)
        midweights = np.interp(midpoints, response_time, schedule.response_weight)
        norm = float(np.sum(widths * (weights[:-1] + weights[1:]) / 2.))
        if not math.isfinite(norm) or norm <= 0.0:
            raise DataValidationError("shutter response has zero physical-time integral")
        reshape = (len(knots),) + (1,) * (sampled.ndim - 1)
        weighted = sampled * weights.reshape(reshape)
        middle_shape = (len(midpoints),) + (1,) * (sampled.ndim - 1)
        middle = midvalues * midweights.reshape(middle_shape)
        # Signal and response are piecewise linear, so their product is
        # quadratic and Simpson integration is exact on every knot interval.
        averaged.append(np.sum(widths.reshape(middle_shape) *
            (weighted[:-1] + 4.*middle + weighted[1:]) / 6., axis=0) / norm)
        effective_time.append(
            float(
                np.sum(widths * (weights[:-1]*knots[:-1]
                    + 4.*midweights*midpoints + weights[1:]*knots[1:]) / 6.)
                / norm
            )
        )
    return ExposureAveraged(np.asarray(effective_time), np.stack(averaged))


@dataclass(frozen=True, slots=True)
class RadialObservation:
    time_s: FloatArray
    radial_edges_m: FloatArray
    surface_profile_m: FloatArray
    surface_profile_delta_m: FloatArray
    effective_area_m2: FloatArray
    valid_area_fraction: FloatArray
    common_sample_mask: BoolArray

    def __post_init__(self) -> None:
        for name in (
            "time_s",
            "radial_edges_m",
            "surface_profile_m",
            "surface_profile_delta_m",
            "effective_area_m2",
            "valid_area_fraction",
        ):
            object.__setattr__(self, name, _readonly(np.asarray(getattr(self, name))))
        object.__setattr__(
            self, "common_sample_mask", _readonly(np.asarray(self.common_sample_mask, dtype=bool))
        )


def aggregate_projected_radial_observation(
    exposure_values_m: ExposureAveraged,
    container_xy_m: Any,
    projected_area_m2: Any,
    radial_edges_m: Any,
    frame_valid_mask: Any,
    *,
    static_valid_mask: Any | None = None,
) -> RadialObservation:
    """Area-average a frozen common support into physical radial bins."""

    if not isinstance(exposure_values_m, ExposureAveraged):
        raise TypeError("exposure_values_m must be ExposureAveraged")
    heights = exposure_values_m.values
    if heights.ndim != 2:
        raise DataValidationError("surface observations must have shape (frames, samples)")
    xy = _finite_array(container_xy_m, "container_xy_m", shape=(heights.shape[1], 2))
    area = _finite_array(
        projected_area_m2, "projected_area_m2", shape=(heights.shape[1],)
    )
    if np.any(area <= 0.0):
        raise DataValidationError("projected areas must be positive")
    edges = _finite_array(radial_edges_m, "radial_edges_m", ndim=1)
    if len(edges) < 2 or edges[0] != 0.0 or not np.all(np.diff(edges) > 0.0):
        raise DataValidationError("radial edges must start at zero and strictly increase")
    valid = np.asarray(frame_valid_mask)
    if valid.shape != heights.shape or valid.dtype != np.bool_:
        raise DataValidationError("frame_valid_mask must be a boolean frame/sample array")
    if static_valid_mask is None:
        static = np.ones(heights.shape[1], dtype=bool)
    else:
        static = np.asarray(static_valid_mask)
        if static.shape != (heights.shape[1],) or static.dtype != np.bool_:
            raise DataValidationError("static_valid_mask must be a boolean sample array")
    common = static & np.all(valid, axis=0)
    radius = np.hypot(xy[:, 0], xy[:, 1])
    if np.any(radius < edges[0]) or np.any(radius >= edges[-1]):
        raise DataValidationError("NO_GO_OBSERVATION_OUTSIDE_FROZEN_RADIAL_DOMAIN")
    bins = np.searchsorted(edges, radius, side="right") - 1
    bin_count = len(edges) - 1
    profile = np.empty((heights.shape[0], bin_count), dtype=np.float64)
    effective_area = np.empty(bin_count, dtype=np.float64)
    valid_fraction = np.empty(bin_count, dtype=np.float64)
    for index in range(bin_count):
        in_bin = bins == index
        total_area = float(np.sum(area[in_bin]))
        selected = in_bin & common
        selected_area = float(np.sum(area[selected]))
        if total_area <= 0.0 or selected_area <= 0.0:
            raise DataValidationError(
                f"NO_GO_OBSERVATION_EMPTY_COMMON_SUPPORT_BIN_{index}"
            )
        effective_area[index] = selected_area
        valid_fraction[index] = selected_area / total_area
        profile[:, index] = np.sum(heights[:, selected] * area[selected], axis=1) / (
            selected_area
        )
    return RadialObservation(
        time_s=exposure_values_m.effective_time_s,
        radial_edges_m=edges,
        surface_profile_m=profile,
        surface_profile_delta_m=profile - profile[0],
        effective_area_m2=np.broadcast_to(effective_area, profile.shape).copy(),
        valid_area_fraction=np.broadcast_to(valid_fraction, profile.shape).copy(),
        common_sample_mask=common,
    )


@dataclass(frozen=True, slots=True)
class CPInitialStateQualification:
    """Result-blind gates required before using a radial measured initial surface."""

    scope: str
    evidence_kind: str
    coordinate_calibration_qualified: bool
    height_calibration_qualified: bool
    full_radial_field_of_view: bool
    axisymmetry_qualified: bool
    initial_rest_qualified: bool

    def __post_init__(self) -> None:
        if self.scope not in {SCIENTIFIC, SYNTHETIC_TEST_ONLY}:
            raise DataValidationError("unsupported initial-state qualification scope")
        if self.evidence_kind not in {REAL_MEASUREMENT, SYNTHETIC}:
            raise DataValidationError("unsupported initial-state evidence kind")
        for name in (
            "coordinate_calibration_qualified",
            "height_calibration_qualified",
            "full_radial_field_of_view",
            "axisymmetry_qualified",
            "initial_rest_qualified",
        ):
            if type(getattr(self, name)) is not bool:
                raise DataValidationError(f"{name} must be boolean")


@dataclass(frozen=True, slots=True)
class CPInitialStateMapping:
    state: ForwardState
    mapped_surface_profile_m: FloatArray
    measured_liquid_volume_m3: float
    mapped_liquid_volume_m3: float
    mapped_to_measured_volume_relative_error: float
    measured_to_configured_volume_relative_error: float
    maximum_profile_mapping_error_m: float
    volume_correction_applied_m3: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "mapped_surface_profile_m",
            _readonly(np.asarray(self.mapped_surface_profile_m, dtype=np.float64)),
        )


def _require_cp_qualification(
    qualification: CPInitialStateQualification, allow_synthetic_test: bool
) -> None:
    if qualification.evidence_kind == SYNTHETIC:
        if not allow_synthetic_test or qualification.scope != SYNTHETIC_TEST_ONLY:
            raise DataValidationError("SYNTHETIC CP mapping is software-test-only")
    elif qualification.scope != SCIENTIFIC:
        raise DataValidationError("real CP mapping requires SCIENTIFIC qualification")
    gates = {
        "COORDINATE_CALIBRATION": qualification.coordinate_calibration_qualified,
        "HEIGHT_CALIBRATION": qualification.height_calibration_qualified,
        "FULL_RADIAL_FOV": qualification.full_radial_field_of_view,
        "AXISYMMETRY": qualification.axisymmetry_qualified,
        "INITIAL_REST": qualification.initial_rest_qualified,
    }
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        raise DataValidationError("NO_GO_CP_INITIAL_STATE_" + "_AND_".join(failed))


def measured_profile_to_cp_initial_state(
    measured_radius_m: Any,
    measured_surface_height_m: Any,
    geometry: VariationalGeometry,
    *,
    interface_thickness_m: float,
    measured_liquid_volume_m3: float,
    mapped_volume_relative_tolerance: float,
    configured_volume_relative_tolerance: float,
    profile_mapping_tolerance_m: float,
    qualification: CPInitialStateQualification,
    initial_circulation_m2_s: float = 0.0,
    allow_synthetic_test: bool = False,
) -> CPInitialStateMapping:
    """Convert a qualified radial ``h0(r)`` into an uncorrected CP phase state.

    The measured profile is linearly sampled at CP radial cell centres.  The
    resulting order parameter is checked against both measured and configured
    liquid volumes.  No offset, clipping-based mass projection, or volume repair
    is applied: a mismatch is a rejection that must be resolved in the input,
    calibration, geometry, or uncertainty budget.
    """

    if not isinstance(geometry, VariationalGeometry):
        raise TypeError("geometry must be a VariationalGeometry")
    if not isinstance(qualification, CPInitialStateQualification):
        raise TypeError("qualification must be CPInitialStateQualification")
    _require_cp_qualification(qualification, allow_synthetic_test)
    radius = _finite_array(measured_radius_m, "measured_radius_m", ndim=1)
    height = _finite_array(
        measured_surface_height_m,
        "measured_surface_height_m",
        shape=radius.shape,
    )
    if len(radius) < 2 or radius[0] != 0.0 or not np.all(np.diff(radius) > 0.0):
        raise DataValidationError(
            "measured radius must start at zero and strictly increase"
        )
    grid = geometry.grid
    if radius[-1] < grid.r_centres[-1]:
        raise DataValidationError("NO_GO_CP_INITIAL_STATE_FULL_RADIAL_FOV")
    if not math.isfinite(interface_thickness_m) or interface_thickness_m <= 0.0:
        raise DataValidationError("interface_thickness_m must be finite and positive")
    positive_values = (
        ("measured_liquid_volume_m3", measured_liquid_volume_m3),
        ("mapped_volume_relative_tolerance", mapped_volume_relative_tolerance),
        ("configured_volume_relative_tolerance", configured_volume_relative_tolerance),
        ("profile_mapping_tolerance_m", profile_mapping_tolerance_m),
    )
    for name, value in positive_values:
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise DataValidationError(f"{name} must be finite and positive")
    if not math.isfinite(float(initial_circulation_m2_s)):
        raise DataValidationError("initial_circulation_m2_s must be finite")

    sampled_height = np.interp(grid.r_centres, radius, height)
    target_columns = grid.r_centres <= geometry.target_radius_m
    if np.any(sampled_height[target_columns] <= geometry.target_top_m):
        raise DataValidationError("NO_GO_CP_INITIAL_SURFACE_INTERSECTS_TARGET")
    top_rows = np.any(geometry.top_reservoir_mask, axis=0)
    if np.any(top_rows):
        top_reservoir_start = float(np.min(grid.z_centres[top_rows]))
        if np.any(sampled_height >= top_reservoir_start):
            raise DataValidationError("NO_GO_CP_INITIAL_SURFACE_INTERSECTS_TOP_RESERVOIR")

    signed = grid.z_centres[None, :] - sampled_height[:, None]
    order_parameter = -np.tanh(
        signed / (math.sqrt(2.0) * float(interface_thickness_m))
    )
    order_parameter[geometry.target_mask] = 1.0
    order_parameter[geometry.top_reservoir_mask] = -1.0
    mapped_volume = liquid_volume_m3(order_parameter, geometry)
    measured_volume = float(measured_liquid_volume_m3)
    mapped_error = abs(mapped_volume - measured_volume) / measured_volume
    configured_error = abs(
        measured_volume - geometry.target_liquid_volume_m3
    ) / geometry.target_liquid_volume_m3
    if configured_error > configured_volume_relative_tolerance:
        raise DataValidationError(
            "NO_GO_CP_INITIAL_VOLUME_INCOMPATIBLE_WITH_CONFIGURED_GEOMETRY: "
            f"relative_error={configured_error:.6e}"
        )
    if mapped_error > mapped_volume_relative_tolerance:
        raise DataValidationError(
            "NO_GO_CP_INITIAL_PHASE_VOLUME_MISMATCH_NO_CORRECTION_APPLIED: "
            f"relative_error={mapped_error:.6e}"
        )

    mapped_profile = surface_profile(
        smooth_liquid_fraction(order_parameter), grid, level=0.5
    )
    profile_error = float(np.max(np.abs(mapped_profile - sampled_height)))
    if profile_error > profile_mapping_tolerance_m:
        raise DataValidationError(
            "NO_GO_CP_INITIAL_PROFILE_DISCRETISATION: "
            f"maximum_error_m={profile_error:.6e}"
        )
    state = ForwardState(float(initial_circulation_m2_s), order_parameter)
    return CPInitialStateMapping(
        state=state,
        mapped_surface_profile_m=mapped_profile,
        measured_liquid_volume_m3=measured_volume,
        mapped_liquid_volume_m3=mapped_volume,
        mapped_to_measured_volume_relative_error=mapped_error,
        measured_to_configured_volume_relative_error=configured_error,
        maximum_profile_mapping_error_m=profile_error,
        volume_correction_applied_m3=0.0,
    )


__all__ = [
    "CPInitialStateMapping",
    "CPInitialStateQualification",
    "ExposureAveraged",
    "GlobalShutterSchedule",
    "MappedFootprints",
    "PlanarCoordinateCalibration",
    "RadialObservation",
    "aggregate_projected_radial_observation",
    "apply_global_shutter",
    "container_to_solver_xy",
    "map_pixel_footprints_to_container",
    "measured_profile_to_cp_initial_state",
    "solver_to_container_xy",
]
