r"""Closed reduced-order forward model for the magnetic-stirrer air vortex.

The backend deliberately stops its physical claims at first geometric contact.
Before contact it combines

* a first-order circulation state ``dC/dt = (A*Omega - C)/tau_C``;
* a regular Burgers/Oseen-like swirl profile;
* radial centrifugal balance; and
* an exact-curvature, axisymmetric Young--Laplace free-surface problem with an
  explicit volume constraint and guarded continuation.

The former linear-curvature solution is retained as an independent analytic
baseline.  It is never substituted for a failed nonlinear solve.

After contact the single-valued free-surface model is outside its domain.  We
still invert the computed surface as a *connected geometry proxy* for plotting,
but do not model wetting, topology change, bubble entrainment, or breakup.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
from typing import Final

import numpy as np
from numpy.typing import NDArray

from ..core import (
    BackendMode,
    Control,
    Event,
    Progress,
    ProgressCallback,
    RunStatus,
    SimulationConfig,
    SimulationResult,
)
from .nonlinear_surface import (
    NONLINEAR_SURFACE_MODEL_ID,
    NonlinearCriticalResult,
    NonlinearSurfaceSolution,
    NonlinearSurfaceSolver,
    solve_first_contact,
)


FloatArray = NDArray[np.float64]

MODEL_ID: Final = "closed-burgers-nonlinear-young-laplace-v2"
MODEL_VERSION: Final = 2
HALASZ_REFERENCE_A_M2: Final = 9.0e-5
HALASZ_REFERENCE_CORE_RADIUS_M: Final = 1.0e-2

_CRITICAL_CACHE_LOCK = threading.RLock()
_CRITICAL_CACHE: dict[tuple[object, ...], NonlinearCriticalResult] = {}
_CRITICAL_CACHE_MAX_SIZE: Final = 32


@dataclass(frozen=True, slots=True)
class UnitSurfaceResponse:
    """Spatial solution for one unit of :math:`C^2`.

    ``surface_per_circulation_sq`` has units such that multiplying it by
    ``C**2`` yields metres.  Its discrete radial-volume mean is zero.
    """

    radius_m: FloatArray
    potential_per_circulation_sq: FloatArray
    surface_per_circulation_sq: FloatArray
    volume_weights: FloatArray
    lagrange_multiplier_per_circulation_sq: float


def _radial_volume_weights(radius_m: FloatArray) -> FloatArray:
    """Trapezoidal weights for ``2/R**2 integral(eta*r dr)``."""

    if radius_m.ndim != 1 or radius_m.size < 2:
        raise ValueError("radius grid must be one-dimensional with at least two points")
    spacing = np.diff(radius_m)
    if np.any(spacing <= 0.0) or not np.allclose(spacing, spacing[0]):
        raise ValueError("radius grid must be strictly increasing and uniform")
    vessel_radius = float(radius_m[-1])
    if vessel_radius <= 0.0:
        raise ValueError("vessel radius must be positive")
    weights = radius_m.copy() * float(spacing[0])
    weights[0] *= 0.5
    weights[-1] *= 0.5
    weights *= 2.0 / vessel_radius**2
    # A linear radial coordinate is integrated exactly, apart from round-off.
    weights /= float(np.sum(weights))
    return weights


def burgers_tangential_velocity(
    radius_m: FloatArray,
    circulation_m2_s: float,
    core_radius_m: float,
) -> FloatArray:
    """Return ``u_theta=C/r*(1-exp(-r**2/c**2))`` with its axis limit."""

    radius = np.asarray(radius_m, dtype=np.float64)
    if radius.ndim != 1 or np.any(radius < 0.0):
        raise ValueError("radius_m must be a one-dimensional non-negative array")
    if not math.isfinite(float(circulation_m2_s)):
        raise ValueError("circulation_m2_s must be finite")
    if not math.isfinite(float(core_radius_m)) or core_radius_m <= 0.0:
        raise ValueError("core_radius_m must be finite and positive")
    velocity = np.zeros_like(radius)
    positive = radius > 0.0
    scaled_radius_sq = np.square(radius[positive] / core_radius_m)
    regularising_factor = -np.expm1(-scaled_radius_sq)
    velocity[positive] = circulation_m2_s * regularising_factor / radius[positive]
    return velocity


def centrifugal_potential_per_circulation_sq(
    radius_m: FloatArray,
    core_radius_m: float,
) -> FloatArray:
    """Compute ``integral_0^r (u_theta/C)^2/s ds`` by Gaussian quadrature.

    Direct evaluation at the axis would contain a removable ``0/0``.  Interior
    Gauss points avoid that singular representation while retaining the exact
    small-radius limit of the Burgers profile.
    """

    radius = np.asarray(radius_m, dtype=np.float64)
    if radius.ndim != 1 or radius.size < 2:
        raise ValueError("radius_m must have at least two points")
    if radius[0] != 0.0 or np.any(np.diff(radius) <= 0.0):
        raise ValueError("radius_m must increase strictly from zero")
    if not math.isfinite(float(core_radius_m)) or core_radius_m <= 0.0:
        raise ValueError("core_radius_m must be finite and positive")

    # Eight-point Gauss--Legendre is inexpensive here and much more accurate
    # than applying a trapezoid to the sharply changing core integrand.
    nodes, weights = np.polynomial.legendre.leggauss(8)
    left = radius[:-1, None]
    right = radius[1:, None]
    sample_radius = 0.5 * (right - left) * nodes[None, :] + 0.5 * (right + left)
    x = np.square(sample_radius / core_radius_m)
    numerator = np.square(-np.expm1(-x))
    integrand = numerator / np.power(sample_radius, 3)
    increments = 0.5 * (right[:, 0] - left[:, 0]) * (integrand @ weights)
    potential = np.empty_like(radius)
    potential[0] = 0.0
    potential[1:] = np.cumsum(increments)
    return potential


def _axisymmetric_laplacian_matrix(radius_m: FloatArray) -> FloatArray:
    """Second-order nodal matrix for ``eta'' + eta'/r`` with Neumann ends."""

    count = radius_m.size
    spacing = float(radius_m[1] - radius_m[0])
    inverse_h2 = 1.0 / spacing**2
    operator = np.zeros((count, count), dtype=np.float64)

    # For an even smooth axisymmetric field, laplacian(eta)(0)=2*eta''(0).
    operator[0, 0] = -4.0 * inverse_h2
    operator[0, 1] = 4.0 * inverse_h2
    for index in range(1, count - 1):
        inverse_2rh = 1.0 / (2.0 * radius_m[index] * spacing)
        operator[index, index - 1] = inverse_h2 - inverse_2rh
        operator[index, index] = -2.0 * inverse_h2
        operator[index, index + 1] = inverse_h2 + inverse_2rh

    # Mirror the wall ghost node to impose eta'(R)=0.
    operator[-1, -2] = 2.0 * inverse_h2
    operator[-1, -1] = -2.0 * inverse_h2
    return operator


def solve_unit_surface_response(config: SimulationConfig) -> UnitSurfaceResponse:
    """Solve the augmented linear Young--Laplace problem for ``C**2=1``.

    The extra row enforces the radial volume integral rather than fixing an
    arbitrary wall height.  The Lagrange multiplier therefore absorbs the
    unknown pressure datum while the mean liquid depth remains prescribed.
    """

    point_count = config.forward.surface_radial_points
    radius = np.linspace(
        0.0, config.container.radius_m, point_count, dtype=np.float64
    )
    potential = centrifugal_potential_per_circulation_sq(
        radius, config.forward.effective_core_radius_m
    )
    volume_weights = _radial_volume_weights(radius)
    laplacian = _axisymmetric_laplacian_matrix(radius)
    capillary_coefficient = (
        config.fluid.surface_tension_n_m / config.fluid.liquid_density_kg_m3
    )
    surface_operator = (
        config.fluid.gravity_m_s2 * np.eye(point_count, dtype=np.float64)
        - capillary_coefficient * laplacian
    )

    augmented = np.zeros((point_count + 1, point_count + 1), dtype=np.float64)
    augmented[:-1, :-1] = surface_operator
    augmented[:-1, -1] = -1.0
    augmented[-1, :-1] = volume_weights
    right_hand_side = np.zeros(point_count + 1, dtype=np.float64)
    right_hand_side[:-1] = potential
    solution = np.linalg.solve(augmented, right_hand_side)
    surface_response = solution[:-1]

    # Remove the last few ulps of volume drift.  A constant shift is exactly a
    # change of pressure datum, so this does not alter the surface gradients.
    surface_response = surface_response - np.dot(volume_weights, surface_response)
    if not np.isfinite(surface_response).all():
        raise RuntimeError("Young--Laplace solve produced non-finite values")
    return UnitSurfaceResponse(
        radius_m=radius,
        potential_per_circulation_sq=potential,
        surface_per_circulation_sq=surface_response,
        volume_weights=volume_weights,
        lagrange_multiplier_per_circulation_sq=float(solution[-1]),
    )


def stir_bar_top_height_m(config: SimulationConfig) -> float:
    """Highest point of the capsule stir bar measured from the vessel bottom."""

    return config.stir_bar.bottom_clearance_m + 2.0 * config.stir_bar.radius_m


def linear_critical_actual_rpm(
    config: SimulationConfig,
    response: UnitSurfaceResponse | None = None,
) -> float:
    """Linear-curvature analytic baseline for geometric first contact."""

    unit = response if response is not None else solve_unit_surface_response(config)
    central_response = float(np.min(unit.surface_per_circulation_sq))
    if central_response >= 0.0:
        raise RuntimeError("unit surface response cannot produce a central depression")
    static_gap = config.container.liquid_depth_m - stir_bar_top_height_m(config)
    if static_gap <= 0.0:
        return 0.0
    critical_circulation_sq = static_gap / -central_response
    critical_omega = (
        math.sqrt(critical_circulation_sq) / config.forward.circulation_coupling_m2
    )
    return critical_omega * 60.0 / (2.0 * math.pi)


def solve_nonlinear_critical_surface(
    config: SimulationConfig,
    response: UnitSurfaceResponse | None = None,
    solver: NonlinearSurfaceSolver | None = None,
) -> NonlinearCriticalResult:
    """Return the guarded exact-curvature first-contact root and diagnostics."""

    cache_key: tuple[object, ...] = (
        MODEL_VERSION,
        config.container.radius_m,
        config.container.liquid_depth_m,
        config.fluid.liquid_density_kg_m3,
        config.fluid.surface_tension_n_m,
        config.fluid.gravity_m_s2,
        config.stir_bar.radius_m,
        config.stir_bar.bottom_clearance_m,
        config.forward.circulation_coupling_m2,
        config.forward.effective_core_radius_m,
        config.forward.azimuthal_decay_power,
        config.forward.surface_radial_points,
        config.forward.relative_tolerance,
        config.forward.absolute_tolerance,
    )
    with _CRITICAL_CACHE_LOCK:
        cached = _CRITICAL_CACHE.get(cache_key)
    if cached is not None:
        return cached

    unit = response if response is not None else solve_unit_surface_response(config)
    central_response = float(np.min(unit.surface_per_circulation_sq))
    if central_response >= 0.0:
        raise RuntimeError("linear baseline cannot bracket a central depression")
    static_gap = config.container.liquid_depth_m - stir_bar_top_height_m(config)
    if static_gap <= 0.0:
        linear_critical_circulation_sq = 1.0
    else:
        linear_critical_circulation_sq = static_gap / -central_response
    nonlinear_solver = solver or NonlinearSurfaceSolver(
        config,
        linear_radius_m=unit.radius_m,
        linear_surface_per_circulation_sq=unit.surface_per_circulation_sq,
    )
    result = solve_first_contact(
        nonlinear_solver,
        static_gap_m=static_gap,
        circulation_coupling_m2=config.forward.circulation_coupling_m2,
        linear_critical_circulation_sq_m4_s2=linear_critical_circulation_sq,
    )
    with _CRITICAL_CACHE_LOCK:
        if len(_CRITICAL_CACHE) >= _CRITICAL_CACHE_MAX_SIZE:
            _CRITICAL_CACHE.pop(next(iter(_CRITICAL_CACHE)))
        _CRITICAL_CACHE.setdefault(cache_key, result)
        return _CRITICAL_CACHE[cache_key]


def critical_actual_rpm(
    config: SimulationConfig,
    response: UnitSurfaceResponse | None = None,
) -> float:
    """Exact-curvature steady actual-bar rpm at geometric first contact.

    This is a reduced-model prediction, not an experimentally validated
    threshold and not a claim that a stable post-contact air channel exists.
    """

    return solve_nonlinear_critical_surface(config, response).actual_rpm


def _drive_omega_rad_s(config: SimulationConfig, time_s: float) -> float:
    drive = config.drive
    if drive.ramp_duration_s > 0.0 and time_s < drive.ramp_duration_s:
        fraction = max(0.0, time_s) / drive.ramp_duration_s
        rpm = drive.initial_rpm + fraction * (drive.target_rpm - drive.initial_rpm)
    else:
        rpm = drive.target_rpm
    return drive.rotation_direction * rpm * (2.0 * math.pi / 60.0)


def _advance_first_order_linear_input(
    state: float,
    input_start: float,
    input_end: float,
    duration_s: float,
    response_time_s: float,
) -> float:
    """Exact first-order update when the forcing is linear over one step."""

    if duration_s <= 0.0:
        return state
    if response_time_s <= 0.0:
        return input_end
    decay = math.exp(-duration_s / response_time_s)
    slope = (input_end - input_start) / duration_s
    convolution = (
        input_start * (1.0 - decay)
        + slope * (duration_s - response_time_s * (1.0 - decay))
    )
    return decay * state + convolution


def _output_times(duration_s: float, interval_s: float) -> FloatArray:
    count = int(math.floor(duration_s / interval_s + 1.0e-12))
    values = np.arange(count + 1, dtype=np.float64) * interval_s
    if values[-1] < duration_s - 1.0e-12 * max(1.0, duration_s):
        values = np.append(values, duration_s)
    else:
        values[-1] = duration_s
    return values


def _half_width(radius_m: FloatArray, surface_m: FloatArray) -> float:
    minimum = float(np.min(surface_m))
    maximum = float(np.max(surface_m))
    depth = maximum - minimum
    if depth <= 64.0 * np.finfo(np.float64).eps * max(1.0, abs(maximum)):
        return 0.0
    centre_index = int(np.argmin(surface_m))
    target = minimum + 0.5 * depth
    outer_surface = np.maximum.accumulate(surface_m[centre_index:])
    outer_radius = radius_m[centre_index:]
    return float(np.interp(target, outer_surface, outer_radius))


def surface_depth_functional_m(
    radius_m: FloatArray,
    surface_m: FloatArray,
    central_roi_radius_m: float,
) -> float:
    """Shared Forward/H1 funnel-depth observable ``H[eta]``.

    ``H[eta]`` is the arithmetic mean of the sampled outer 20 percent in
    radius minus the minimum sample within the central stir-bar half-length.
    The definition deliberately matches ``HighFidelityBackend``.  It is not
    the full profile range; that independent quantity is reported as
    ``surface_profile_range_m``.
    """

    radius = np.asarray(radius_m, dtype=np.float64)
    surface = np.asarray(surface_m, dtype=np.float64)
    if (
        radius.ndim != 1
        or surface.shape != radius.shape
        or radius.size < 2
        or radius[0] < 0.0
        or np.any(np.diff(radius) <= 0.0)
        or not np.isfinite(surface).all()
    ):
        raise ValueError("radius and surface must be finite matching 1-D profiles")
    if not math.isfinite(central_roi_radius_m) or central_roi_radius_m <= 0.0:
        raise ValueError("central_roi_radius_m must be finite and positive")
    outer = radius >= 0.8 * float(radius[-1])
    centre = radius <= central_roi_radius_m
    if not np.any(outer):  # pragma: no cover - size/radius guards imply samples
        outer[-1] = True
    if not np.any(centre):
        centre[0] = True
    depth = float(np.mean(surface[outer]) - np.min(surface[centre]))
    if depth <= 64.0 * np.finfo(np.float64).eps * max(
        1.0, float(np.max(np.abs(surface)))
    ):
        return 0.0
    return max(0.0, depth)


def surface_profile_range_m(surface_m: FloatArray) -> float:
    """Full max-minus-min profile range, kept separate from ``H[eta]``."""

    surface = np.asarray(surface_m, dtype=np.float64)
    if surface.ndim != 1 or surface.size < 2 or not np.isfinite(surface).all():
        raise ValueError("surface_m must be a finite one-dimensional profile")
    return float(np.ptp(surface))


def _max_abs_surface_slope(radius_m: FloatArray, surface_m: FloatArray) -> float:
    """Maximum nodal ``|d eta/dr|`` respecting the two Neumann boundaries."""

    if radius_m.size < 3:
        return 0.0
    slope = np.zeros_like(surface_m)
    slope[1:-1] = (surface_m[2:] - surface_m[:-2]) / (
        radius_m[2:] - radius_m[:-2]
    )
    return float(np.max(np.abs(slope)))


def _channel_geometry(
    radius_m: FloatArray,
    surface_m: FloatArray,
    bar_top_m: float,
    levels: int = 128,
) -> tuple[FloatArray, FloatArray, float]:
    """Invert ``eta(r)=z`` as an explicitly out-of-domain geometry proxy."""

    centre_index = int(np.argmin(surface_m))
    monotone_surface = np.maximum.accumulate(surface_m[centre_index:])
    monotone_radius = radius_m[centre_index:]
    upper_z = float(monotone_surface[-1])
    lower_z = float(bar_top_m)
    if upper_z <= lower_z:
        z = np.array([lower_z], dtype=np.float64)
        r = np.array([float(monotone_radius[-1])], dtype=np.float64)
        return z, r, float(r[0])
    z = np.linspace(lower_z, upper_z, levels, dtype=np.float64)
    r = np.interp(z, monotone_surface, monotone_radius).astype(np.float64, copy=False)
    bottom_radius = float(r[0])
    return z, r, bottom_radius


def _crossing_time(
    time_a: float,
    time_b: float,
    value_a: float,
    value_b: float,
    threshold: float,
) -> float:
    if value_b == value_a:
        return time_b
    fraction = (threshold - value_a) / (value_b - value_a)
    return time_a + min(1.0, max(0.0, fraction)) * (time_b - time_a)


class ForwardBackend:
    """Numerical implementation of the closed reduced forward theory."""

    mode = BackendMode.FORWARD
    backend_id = "forward"
    display_name = "闭合降阶 Burgers–Young–Laplace 前向模型"

    def run(
        self,
        config: SimulationConfig,
        control: Control | None = None,
        progress: ProgressCallback | None = None,
    ) -> SimulationResult:
        if config.backend is not BackendMode.FORWARD:
            raise ValueError("ForwardBackend requires config.backend=BackendMode.FORWARD")
        if not math.isclose(
            config.forward.azimuthal_decay_power, 1.0, rel_tol=0.0, abs_tol=1.0e-12
        ):
            raise ValueError(
                "the closed Burgers profile fixes azimuthal_decay_power at 1.0"
            )

        response = solve_unit_surface_response(config)
        radius = response.radius_m
        unit_surface = response.surface_per_circulation_sq
        nonlinear_solver = NonlinearSurfaceSolver(
            config,
            linear_radius_m=radius,
            linear_surface_per_circulation_sq=unit_surface,
        )
        critical_result = solve_nonlinear_critical_surface(
            config, response, nonlinear_solver
        )
        critical_rpm = critical_result.actual_rpm
        linear_critical_rpm = linear_critical_actual_rpm(config, response)
        bar_top = stir_bar_top_height_m(config)
        contact_tolerance = max(config.forward.absolute_tolerance, 1.0e-12)
        requested_times = _output_times(
            config.run.duration_s, config.run.output_interval_s
        )

        coupling = config.forward.circulation_coupling_m2
        initial_omega = (
            config.drive.rotation_direction
            * config.drive.initial_rpm
            * (2.0 * math.pi / 60.0)
        )
        circulation = coupling * initial_omega
        surface_state = circulation**2
        current_time = 0.0
        status = RunStatus.COMPLETED

        times: list[float] = []
        omega_values: list[float] = []
        circulation_values: list[float] = []
        surface_state_values: list[float] = []
        surface_profiles: list[FloatArray] = []
        linear_surface_profiles: list[FloatArray] = []
        tangential_profiles: list[FloatArray] = []
        depths: list[float] = []
        profile_ranges: list[float] = []
        linear_depths: list[float] = []
        linear_profile_ranges: list[float] = []
        linear_gaps: list[float] = []
        half_widths: list[float] = []
        surface_minima: list[float] = []
        gaps: list[float] = []
        contacts: list[float] = []
        bottom_radii: list[float] = []
        volume_errors: list[float] = []
        maximum_slopes: list[float] = []
        maximum_normalised_slopes: list[float] = []
        nonlinear_rms_residuals: list[float] = []
        nonlinear_boundary_residuals: list[float] = []
        nonlinear_collocation_nodes: list[float] = []
        nonlinear_iterations: list[float] = []
        nonlinear_continuation_substeps: list[float] = []
        small_slope_validity: list[float] = []
        events: list[Event] = []
        channel_z = np.empty(0, dtype=np.float64)
        channel_radius = np.empty(0, dtype=np.float64)
        channel_time: float | None = None
        ever_contact = False
        nonlinear_state: NonlinearSurfaceSolution | None = critical_result.surface

        def append_snapshot(time_s: float) -> None:
            nonlocal channel_z, channel_radius, channel_time, ever_contact
            nonlocal nonlinear_state
            omega = _drive_omega_rad_s(config, time_s)
            nonlinear_state = nonlinear_solver.solve(
                surface_state, initial=nonlinear_state
            )
            surface = (
                config.container.liquid_depth_m + nonlinear_state.elevation_m
            )
            linear_surface = (
                config.container.liquid_depth_m + surface_state * unit_surface
            )
            tangential = burgers_tangential_velocity(
                radius, circulation, config.forward.effective_core_radius_m
            )
            surface_min = float(np.min(surface))
            depth = surface_depth_functional_m(
                radius,
                surface,
                config.stir_bar.half_length_m,
            )
            profile_range = surface_profile_range_m(surface)
            linear_depth = surface_depth_functional_m(
                radius,
                linear_surface,
                config.stir_bar.half_length_m,
            )
            linear_profile_range = surface_profile_range_m(linear_surface)
            gap = surface_min - bar_top
            linear_gap = float(np.min(linear_surface)) - bar_top
            connected = gap <= contact_tolerance
            bottom_radius = 0.0
            if connected:
                channel_z, channel_radius, bottom_radius = _channel_geometry(
                    radius, surface, bar_top
                )
                channel_time = time_s
                ever_contact = True
            volume = math.pi * config.container.radius_m**2 * float(
                np.dot(response.volume_weights, surface)
            )
            reference_volume = (
                math.pi
                * config.container.radius_m**2
                * config.container.liquid_depth_m
            )

            times.append(float(time_s))
            omega_values.append(float(omega))
            circulation_values.append(float(circulation))
            surface_state_values.append(float(surface_state))
            surface_profiles.append(surface)
            linear_surface_profiles.append(linear_surface)
            tangential_profiles.append(tangential)
            depths.append(float(depth))
            profile_ranges.append(profile_range)
            linear_depths.append(linear_depth)
            linear_profile_ranges.append(linear_profile_range)
            linear_gaps.append(linear_gap)
            half_widths.append(_half_width(radius, surface))
            surface_minima.append(surface_min)
            gaps.append(float(gap))
            contacts.append(1.0 if connected else 0.0)
            bottom_radii.append(bottom_radius)
            volume_errors.append(float(volume - reference_volume))
            maximum_slope = nonlinear_state.maximum_absolute_slope
            maximum_slopes.append(maximum_slope)
            maximum_normalised_slopes.append(
                nonlinear_state.maximum_absolute_normalised_slope
            )
            nonlinear_rms_residuals.append(
                nonlinear_state.maximum_collocation_rms_residual
            )
            nonlinear_boundary_residuals.append(
                nonlinear_state.boundary_residual_norm
            )
            nonlinear_collocation_nodes.append(
                float(nonlinear_state.collocation_nodes)
            )
            nonlinear_iterations.append(float(nonlinear_state.nonlinear_iterations))
            nonlinear_continuation_substeps.append(
                float(nonlinear_state.continuation_substeps)
            )
            # 0.3 is an engineering warning threshold, not a physical law or
            # a posteriori error bound.  It diagnoses the *linear baseline*,
            # not the exact-curvature main solution.
            linear_maximum_slope = _max_abs_surface_slope(radius, linear_surface)
            small_slope_validity.append(
                1.0 if linear_maximum_slope < 0.3 else 0.0
            )

        append_snapshot(0.0)
        if progress is not None:
            progress(
                Progress(
                    fraction=0.0,
                    simulated_time_s=0.0,
                    step=0,
                    stage="running",
                    message="forward model initialised",
                    metrics={"critical_actual_rpm": critical_rpm},
                )
            )

        previous_onset = depths[-1] >= config.forward.air_core_depth_threshold_m
        previous_contact = bool(contacts[-1])
        if previous_onset:
            events.append(
                Event(
                    0.0,
                    "funnel_depth_threshold",
                    "initial surface already exceeds the reporting-depth threshold",
                    {"threshold_m": config.forward.air_core_depth_threshold_m},
                )
            )
        if previous_contact:
            events.append(
                Event(
                    0.0,
                    "CONNECTED_PROXY",
                    "geometric contact proxy reached; subsequent shape is out of model domain",
                    {"bar_top_m": bar_top, "bottom_radius_m": bottom_radii[-1]},
                )
            )

        for output_index, output_time in enumerate(requested_times[1:], start=1):
            while current_time < output_time - 1.0e-15:
                if control is not None and not control.checkpoint():
                    status = RunStatus.STOPPED
                    break
                step = min(
                    config.forward.integration_time_step_s,
                    float(output_time - current_time),
                )
                ramp_end = config.drive.ramp_duration_s
                if current_time < ramp_end < current_time + step:
                    step = ramp_end - current_time
                omega_start = _drive_omega_rad_s(config, current_time)
                omega_end = _drive_omega_rad_s(config, current_time + step)
                circulation_start = circulation
                circulation = _advance_first_order_linear_input(
                    circulation,
                    coupling * omega_start,
                    coupling * omega_end,
                    step,
                    config.forward.spin_up_time_s,
                )
                if config.forward.surface_response_time_s <= 0.0:
                    surface_state = circulation**2
                else:
                    surface_state = _advance_first_order_linear_input(
                        surface_state,
                        circulation_start**2,
                        circulation**2,
                        step,
                        config.forward.surface_response_time_s,
                    )
                current_time += step
            if status is RunStatus.STOPPED:
                # Preserve partial progress instead of reporting the preceding
                # output boundary as the stop time.  ``current_time`` is always
                # a completed integration step and is therefore a safe state.
                if current_time > times[-1] + 1.0e-15:
                    append_snapshot(current_time)
                break

            append_snapshot(float(output_time))
            onset = depths[-1] >= config.forward.air_core_depth_threshold_m
            connected = bool(contacts[-1])
            if onset and not previous_onset:
                event_time = _crossing_time(
                    times[-2],
                    times[-1],
                    depths[-2],
                    depths[-1],
                    config.forward.air_core_depth_threshold_m,
                )
                events.append(
                    Event(
                        event_time,
                        "funnel_depth_threshold",
                        "funnel depth crossed the reporting threshold",
                        {"threshold_m": config.forward.air_core_depth_threshold_m},
                    )
                )
            if connected and not previous_contact:
                event_time = _crossing_time(
                    times[-2], times[-1], gaps[-2], gaps[-1], 0.0
                )
                events.append(
                    Event(
                        event_time,
                        "CONNECTED_PROXY",
                        "surface reached the stir-bar top; topology is out of model domain",
                        {
                            "bar_top_m": bar_top,
                            "bottom_radius_m": bottom_radii[-1],
                        },
                    )
                )
            elif previous_contact and not connected:
                events.append(
                    Event(
                        float(output_time),
                        "connected_proxy_exit",
                        "geometric contact proxy cleared during spin-down",
                        {"bar_top_m": bar_top},
                    )
                )
            previous_onset = onset
            previous_contact = connected

            if progress is not None:
                progress(
                    Progress(
                        fraction=min(1.0, float(output_time / config.run.duration_s)),
                        simulated_time_s=float(output_time),
                        step=output_index,
                        stage="running",
                        message="integrating closed reduced model",
                        metrics={
                            "funnel_depth_m": depths[-1],
                            "minimum_gap_m": gaps[-1],
                            "connected_proxy": connected,
                        },
                    )
                )

        time_array = np.asarray(times, dtype=np.float64)
        omega_array = np.asarray(omega_values, dtype=np.float64)
        critical_array = np.full(time_array.shape, critical_rpm, dtype=np.float64)
        linear_critical_array = np.full(
            time_array.shape, linear_critical_rpm, dtype=np.float64
        )
        final_connected = bool(contacts[-1])
        critical_maximum_slope = (
            critical_result.surface.maximum_absolute_slope
        )
        linear_critical_omega = linear_critical_rpm * (2.0 * math.pi / 60.0)
        linear_critical_surface = (
            config.container.liquid_depth_m
            + np.square(
                config.forward.circulation_coupling_m2 * linear_critical_omega
            )
            * unit_surface
        )
        linear_critical_maximum_slope = _max_abs_surface_slope(
            radius, linear_critical_surface
        )
        final_small_slope_valid = bool(small_slope_validity[-1])
        domain_status = "OUT_OF_MODEL_DOMAIN" if ever_contact else "IN_MODEL_DOMAIN"
        connection_status = "CONNECTED_PROXY" if final_connected else "NOT_CONNECTED_PROXY"
        metadata = {
            "model_id": MODEL_ID,
            "model_version": MODEL_VERSION,
            "model_class": "closed_reduced_forward_theory",
            "surface_model": "EXACT_CURVATURE_AXISYMMETRIC_YOUNG_LAPLACE",
            "surface_model_id": NONLINEAR_SURFACE_MODEL_ID,
            "surface_solution_regime": (
                nonlinear_state.solution_regime
                if nonlinear_state is not None
                else critical_result.solution_regime
            ),
            "linear_surface_baseline": "LINEARISED_SMALL_SLOPE_YOUNG_LAPLACE",
            "linear_baseline_is_main_solution": False,
            "silent_linear_fallback_permitted": False,
            "small_slope_engineering_threshold": 0.3,
            "small_slope_validity": (
                "LINEAR_BASELINE_WITHIN_ENGINEERING_WARNING_THRESHOLD"
                if final_small_slope_valid
                else "LINEAR_BASELINE_OUTSIDE_ENGINEERING_WARNING_THRESHOLD"
            ),
            "small_slope_warning": (
                "The 0.3 threshold diagnoses the independent linear-curvature "
                "baseline only; it is not a law or an a posteriori error bound."
            ),
            "surface_equation": (
                "g*eta-(sigma/rho)/r*d["
                "r*eta_r/sqrt(1+eta_r^2)]/dr=Phi+lambda"
            ),
            "curvature_sign_convention": (
                "normal liquid_to_gas; kappa=-div_r(eta_r/sqrt(1+eta_r^2))"
            ),
            "surface_boundary_conditions": (
                "axis regularity eta_r(0)=0; free 90deg wall eta_r(R)=0"
            ),
            "volume_constraint": "2/R^2*integral_0^R eta(r)*r dr=0",
            "circulation_equation": "dC/dt=(A*Omega-C)/tau_C",
            "swirl_profile": "u_theta=C/r*(1-exp(-r^2/c^2))",
            "drive_interpretation": "prescribed_actual_stir_bar_speed",
            "critical_actual_rpm": critical_rpm,
            "linear_baseline_critical_actual_rpm": linear_critical_rpm,
            "critical_method": (
                "exact_curvature_natural_parameter_continuation_and_bisection"
            ),
            "critical_interpretation": (
                "derived geometric first-contact proxy; calibration required; "
                "not experimental validation and not a stable-channel criterion"
            ),
            "circulation_naming": {
                "burgers_C_m2_s": "C in u_theta=C/r*(1-exp(-r^2/c^2))",
                "circulation_m2_s": "compatibility alias for Burgers C, not Gamma",
                "physical_circulation_gamma_m2_s": "Gamma=2*pi*C in the far field",
            },
            "bar_top_height_m": bar_top,
            "contact_tolerance_m": contact_tolerance,
            "domain_status": domain_status,
            "connection_status": connection_status,
            "connected_channel_interpretation": "CONNECTED_PROXY",
            "post_contact_interpretation": "OUT_OF_MODEL_DOMAIN",
            "bubble_entrainment_simulated": False,
            "topology_change_simulated": False,
            "wetting_and_contact_line_simulated": False,
            "uses_high_fidelity_equations": False,
            "channel_snapshot_time_s": channel_time,
            "diagnostics": {
                "empirical_reference": {
                    "is_empirical_reference": True,
                    "universal_constant": False,
                    "reference_context": "Halasz magnetic-stirrer apparatus",
                    "reference_A_m2": HALASZ_REFERENCE_A_M2,
                    "reference_core_radius_m": HALASZ_REFERENCE_CORE_RADIUS_M,
                    "configured_A_m2": config.forward.circulation_coupling_m2,
                    "configured_core_radius_m": config.forward.effective_core_radius_m,
                    "warning": "A and c require apparatus-specific calibration",
                },
                "maximum_absolute_volume_error_m3": float(
                    np.max(np.abs(np.asarray(volume_errors, dtype=np.float64)))
                ),
                "radial_points": int(radius.size),
                "linear_surface_lagrange_multiplier_per_circulation_sq": (
                    response.lagrange_multiplier_per_circulation_sq
                ),
                "critical_max_abs_surface_slope": critical_maximum_slope,
                "critical_max_abs_normalised_slope": (
                    critical_result.surface.maximum_absolute_normalised_slope
                ),
                "linear_critical_max_abs_surface_slope": (
                    linear_critical_maximum_slope
                ),
                "linear_critical_small_slope_valid": (
                    linear_critical_maximum_slope < 0.3
                ),
                "critical_dimensionless_forcing_W": (
                    critical_result.dimensionless_forcing
                ),
                "critical_circulation_sq_m4_s2": (
                    critical_result.circulation_sq_m4_s2
                ),
                "critical_height_residual_m": critical_result.height_residual_m,
                "critical_root_iterations": critical_result.root_iterations,
                "critical_forcing_bracket": list(
                    critical_result.bracket_dimensionless_forcing
                ),
                "nonlinear_to_linear_critical_rpm_ratio": (
                    critical_rpm / linear_critical_rpm
                    if linear_critical_rpm > 0.0
                    else 1.0
                ),
                "nonlinear_solver": {
                    "relative_tolerance": config.forward.relative_tolerance,
                    "absolute_tolerance_m": config.forward.absolute_tolerance,
                    "maximum_recorded_collocation_rms_residual": float(
                        np.max(
                            np.asarray(nonlinear_rms_residuals, dtype=np.float64)
                        )
                    ),
                    "maximum_recorded_boundary_residual": float(
                        np.max(
                            np.asarray(
                                nonlinear_boundary_residuals, dtype=np.float64
                            )
                        )
                    ),
                    "maximum_recorded_nodes": int(
                        np.max(
                            np.asarray(
                                nonlinear_collocation_nodes, dtype=np.float64
                            )
                        )
                    ),
                    "failure_policy": "FAIL_CLOSED_NO_LINEAR_FALLBACK",
                },
            },
            "observable_definitions": {
                "funnel_depth_m": (
                    "H[eta]=mean(surface at r>=0.8R)-min(surface at r<=a); "
                    "same sampled functional as HF-preview"
                ),
                "surface_depth_m": "exact alias of funnel_depth_m",
                "surface_profile_range_m": "max(surface)-min(surface)",
                "minimum_gap_m": "global min(surface)-stir_bar_top_height",
                "linear_funnel_depth_m": (
                    "same H[eta] applied to independent linear-curvature baseline"
                ),
            },
            "limitations": [
                "axisymmetric equivalent forcing for a non-axisymmetric bar",
                "single-valued graph ends at a vertical tangent",
                "90 degree freely sliding wall contact line is prescribed",
                "geometric contact is not proof of a stable air channel",
                "no bubble entrainment, breakup, hysteresis, or post-contact two-phase flow",
            ],
            "observable_units": {
                "actual_bar_rpm": "rpm",
                "actual_angular_velocity_rad_s": "rad s^-1",
                "burgers_C_m2_s": "m^2 s^-1",
                "circulation_m2_s": "m^2 s^-1",
                "physical_circulation_gamma_m2_s": "m^2 s^-1",
                "surface_state_m4_s2": "m^4 s^-2",
                "funnel_depth_m": "m",
                "surface_depth_m": "m",
                "surface_profile_range_m": "m",
                "linear_funnel_depth_m": "m",
                "linear_surface_depth_m": "m",
                "linear_surface_profile_range_m": "m",
                "linear_minimum_gap_m": "m",
                "half_width_m": "m",
                "surface_min_z_m": "m",
                "minimum_gap_m": "m",
                "min_gap_to_bar_m": "m",
                "connected_proxy": "1",
                "first_contact_proxy": "1",
                "air_core_bottom_radius_m": "m",
                "liquid_volume_error_m3": "m^3",
                "max_abs_surface_slope": "1",
                "max_abs_normalised_surface_slope": "1",
                "small_slope_validity": "1",
                "critical_actual_rpm": "rpm",
                "linear_critical_actual_rpm": "rpm",
                "surface_profile_m": "m",
                "linear_surface_profile_m": "m",
                "nonlinear_collocation_rms_residual": "1",
                "nonlinear_boundary_residual": "1",
                "nonlinear_collocation_nodes": "1",
                "nonlinear_iterations": "1",
                "nonlinear_continuation_substeps": "1",
                "tangential_velocity_profile_m_s": "m s^-1",
            },
            "field_units": {
                "radius_m": "m",
                "radial_volume_weights": "1",
                "centrifugal_potential_per_circulation_sq": "m^-2",
                "surface_per_circulation_sq": "s^2 m^-3",
                "nonlinear_final_normalised_slope": "1",
                "nonlinear_final_curvature_m_inv": "m^-1",
                "connected_proxy_channel_z_m": "m",
                "connected_proxy_channel_radius_m": "m",
            },
        }

        burgers_c_array = np.asarray(circulation_values, dtype=np.float64)
        observable_arrays: dict[str, FloatArray] = {
            "actual_bar_rpm": omega_array * (60.0 / (2.0 * math.pi)),
            "actual_angular_velocity_rad_s": omega_array,
            "burgers_C_m2_s": burgers_c_array,
            "circulation_m2_s": burgers_c_array,
            "physical_circulation_gamma_m2_s": 2.0 * math.pi * burgers_c_array,
            "surface_state_m4_s2": np.asarray(
                surface_state_values, dtype=np.float64
            ),
            "funnel_depth_m": np.asarray(depths, dtype=np.float64),
            "surface_depth_m": np.asarray(depths, dtype=np.float64),
            "surface_profile_range_m": np.asarray(
                profile_ranges, dtype=np.float64
            ),
            "linear_funnel_depth_m": np.asarray(
                linear_depths, dtype=np.float64
            ),
            "linear_surface_depth_m": np.asarray(
                linear_depths, dtype=np.float64
            ),
            "linear_surface_profile_range_m": np.asarray(
                linear_profile_ranges, dtype=np.float64
            ),
            "linear_minimum_gap_m": np.asarray(linear_gaps, dtype=np.float64),
            "half_width_m": np.asarray(half_widths, dtype=np.float64),
            "surface_min_z_m": np.asarray(surface_minima, dtype=np.float64),
            "minimum_gap_m": np.asarray(gaps, dtype=np.float64),
            "min_gap_to_bar_m": np.asarray(gaps, dtype=np.float64),
            "connected_proxy": np.asarray(contacts, dtype=np.float64),
            "first_contact_proxy": np.asarray(contacts, dtype=np.float64),
            "air_core_bottom_radius_m": np.asarray(bottom_radii, dtype=np.float64),
            "liquid_volume_error_m3": np.asarray(volume_errors, dtype=np.float64),
            "max_abs_surface_slope": np.asarray(maximum_slopes, dtype=np.float64),
            "max_abs_normalised_surface_slope": np.asarray(
                maximum_normalised_slopes, dtype=np.float64
            ),
            "small_slope_validity": np.asarray(
                small_slope_validity, dtype=np.float64
            ),
            "critical_actual_rpm": critical_array,
            "linear_critical_actual_rpm": linear_critical_array,
            "surface_profile_m": np.stack(surface_profiles),
            "linear_surface_profile_m": np.stack(linear_surface_profiles),
            "nonlinear_collocation_rms_residual": np.asarray(
                nonlinear_rms_residuals, dtype=np.float64
            ),
            "nonlinear_boundary_residual": np.asarray(
                nonlinear_boundary_residuals, dtype=np.float64
            ),
            "nonlinear_collocation_nodes": np.asarray(
                nonlinear_collocation_nodes, dtype=np.float64
            ),
            "nonlinear_iterations": np.asarray(
                nonlinear_iterations, dtype=np.float64
            ),
            "nonlinear_continuation_substeps": np.asarray(
                nonlinear_continuation_substeps, dtype=np.float64
            ),
            "tangential_velocity_profile_m_s": np.stack(tangential_profiles),
        }
        if nonlinear_state is None:  # pragma: no cover - snapshots guarantee state
            raise RuntimeError("nonlinear surface state missing after forward run")
        fields: dict[str, FloatArray] = {
            "radius_m": radius,
            "radial_volume_weights": response.volume_weights,
            "centrifugal_potential_per_circulation_sq": (
                response.potential_per_circulation_sq
            ),
            "surface_per_circulation_sq": response.surface_per_circulation_sq,
            "nonlinear_final_normalised_slope": nonlinear_state.normalised_slope,
            "nonlinear_final_curvature_m_inv": nonlinear_state.curvature_m_inv,
            "connected_proxy_channel_z_m": channel_z,
            "connected_proxy_channel_radius_m": channel_radius,
        }
        if status is RunStatus.STOPPED:
            events.append(
                Event(
                    float(time_array[-1]),
                    "stopped",
                    control.stop_reason if control is not None else "stopped",
                )
            )
        elif progress is not None:
            progress(
                Progress(
                    fraction=1.0,
                    simulated_time_s=float(time_array[-1]),
                    step=max(0, time_array.size - 1),
                    stage="completed",
                    message="forward model completed",
                    metrics={
                        "funnel_depth_m": depths[-1],
                        "minimum_gap_m": gaps[-1],
                        "connected_proxy": final_connected,
                    },
                )
            )

        return SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=time_array,
            observables=observable_arrays,
            fields=fields,
            events=tuple(events),
            metadata=metadata,
            status=status,
        )


# Descriptive alias retained for callers that name the model rather than mode.
ReducedForwardBackend = ForwardBackend
ForwardTheoryBackend = ForwardBackend


__all__ = [
    "HALASZ_REFERENCE_A_M2",
    "HALASZ_REFERENCE_CORE_RADIUS_M",
    "MODEL_ID",
    "ForwardBackend",
    "ForwardTheoryBackend",
    "ReducedForwardBackend",
    "UnitSurfaceResponse",
    "burgers_tangential_velocity",
    "centrifugal_potential_per_circulation_sq",
    "critical_actual_rpm",
    "linear_critical_actual_rpm",
    "solve_nonlinear_critical_surface",
    "solve_unit_surface_response",
    "stir_bar_top_height_m",
    "surface_depth_functional_m",
    "surface_profile_range_m",
]
