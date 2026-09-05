"""CP pressure-potential interface with accepted-step quantitative validity.

The independent graph solver remains in ``airvortex.backends.forward``.
Strict CP prediction stops at its first unsupported event; explicit diagnostic
continuation preserves the same regularised equations and an irreversible
invalid-sample mask.  SH and TF dispatch to their own equations and states.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
from typing import Mapping

import numpy as np
from numpy.typing import NDArray
from scipy.special import expi

from airvortex.core import (
    BackendMode,
    Control,
    Event,
    Progress,
    ProgressCallback,
    RunStatus,
    SimulationConfig,
    SimulationResult,
)
from airvortex.core.validity import make_quantitative_validity
from airvortex.backends.forward_gas import assess_forward_gas
from airvortex.numerics.axisymmetric import AxisymmetricGrid, FloatArray
from airvortex.numerics.interface import surface_profile
from airvortex.numerics.variational_interface import (
    FLOOR,
    TARGET,
    WALL,
    ContactLaw,
    TopologyDiagnostics,
    VariationalGeometry,
    VariationalStepError,
    advance_variational_phase,
    build_variational_geometry,
    discrete_energy_and_gradient,
    initialise_flat_order_parameter,
    liquid_volume_m3,
    phase_curvature_m_inv,
    smooth_liquid_fraction,
    topology_diagnostics,
)


MODEL_ID = "forward-cp2-axisymmetric-variational-fv-v2"
THEORY_ID = "AV-FWD-CP-v2"
NUMERICAL_METHOD_ID = "PF-FV2D-v1"
FORWARD_STATE_FORMAT = "airvortex-forward-state-v1"


@dataclass(frozen=True, slots=True)
class ForwardState:
    """Programmatic initial/restart state for arbitrary admissible L0."""

    circulation_m2_s: float
    phase_order_parameter: NDArray[np.floating]

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.circulation_m2_s)):
            raise ValueError("initial circulation must be finite")
        phase = np.asarray(self.phase_order_parameter, dtype=np.float64)
        if phase.ndim != 2 or not np.isfinite(phase).all():
            raise ValueError("phase_order_parameter must be a finite 2D array")
        if np.any(phase < -1.0) or np.any(phase > 1.0):
            raise ValueError("phase_order_parameter must lie in [-1, 1]")
        object.__setattr__(self, "circulation_m2_s", float(self.circulation_m2_s))
        object.__setattr__(self, "phase_order_parameter", phase.copy())

    @classmethod
    def from_result(cls, result: SimulationResult) -> "ForwardState":
        """Extract the final restart state from a compatible forward result."""

        if result.backend is not BackendMode.FORWARD:
            raise ValueError("restart state requires a forward-backend result")
        try:
            circulation = float(result.observables["burgers_C_m2_s"][-1])
            phase = result.fields["final_phase_order_parameter"]
        except KeyError as exc:
            raise ValueError("result does not contain an F1.0 restart state") from exc
        return cls(circulation, phase)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_forward_state_geometry(
    state: ForwardState,
    geometry: VariationalGeometry,
    *,
    volume_relative_tolerance: float,
) -> None:
    """Reject restart states that violate the frozen geometry/volume contract."""

    phase = state.phase_order_parameter
    if phase.shape != geometry.grid.shape:
        raise ValueError("restart phase shape does not match configured grid")
    if np.any(phase[geometry.top_reservoir_mask] != -1.0):
        raise ValueError("forward restart violates fixed top gas reservoir")
    if np.any(phase[geometry.target_mask] != 1.0):
        raise ValueError("forward restart penetrates the effective solid target")
    volume = liquid_volume_m3(phase, geometry)
    relative_error = abs(volume - geometry.target_liquid_volume_m3) / (
        geometry.target_liquid_volume_m3
    )
    if relative_error > volume_relative_tolerance:
        raise ValueError(
            "forward restart liquid volume differs from configured target: "
            f"relative error={relative_error:.6e}"
        )


def save_forward_state_npz(
    path: str | Path,
    state: ForwardState,
    config: SimulationConfig,
    *,
    overwrite: bool = False,
) -> tuple[Path, str]:
    """Save an allow-pickle-free, geometry-bound restart and return its hash."""

    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    geometry, _ = _build_geometry(config)
    _validate_forward_state_geometry(
        state,
        geometry,
        volume_relative_tolerance=config.forward.volume_relative_tolerance,
    )
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as stream:
            np.savez_compressed(
                stream,
                format_version=np.asarray(FORWARD_STATE_FORMAT),
                circulation_m2_s=np.asarray(state.circulation_m2_s),
                phase_order_parameter=state.phase_order_parameter,
                radial_cells=np.asarray(geometry.grid.nr),
                axial_cells=np.asarray(geometry.grid.nz),
                radius_m=np.asarray(geometry.grid.radius_m),
                height_m=np.asarray(geometry.grid.height_m),
                liquid_height_m=np.asarray(geometry.liquid_height_m),
                target_bottom_m=np.asarray(geometry.target_bottom_m),
                target_top_m=np.asarray(geometry.target_top_m),
                target_radius_m=np.asarray(geometry.target_radius_m),
                target_liquid_volume_m3=np.asarray(geometry.target_liquid_volume_m3),
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination, _sha256_file(destination)


def load_forward_state_npz(
    path: str | Path,
    expected_sha256: str,
    config: SimulationConfig,
) -> ForwardState:
    """Verify hash, schema, geometry and volume before accepting a restart."""

    source = Path(path).expanduser().resolve()
    digest = _sha256_file(source)
    if digest != expected_sha256.lower():
        raise ValueError(
            "forward restart SHA-256 mismatch: "
            f"observed {digest}, expected {expected_sha256.lower()}"
        )
    geometry, _ = _build_geometry(config)
    expected_keys = {
        "format_version",
        "circulation_m2_s",
        "phase_order_parameter",
        "radial_cells",
        "axial_cells",
        "radius_m",
        "height_m",
        "liquid_height_m",
        "target_bottom_m",
        "target_top_m",
        "target_radius_m",
        "target_liquid_volume_m3",
    }
    with np.load(source, allow_pickle=False) as archive:
        if set(archive.files) != expected_keys:
            raise ValueError("forward restart contains missing or unknown fields")
        if str(archive["format_version"].item()) != FORWARD_STATE_FORMAT:
            raise ValueError("unsupported forward restart format")
        exact_values = {
            "radial_cells": geometry.grid.nr,
            "axial_cells": geometry.grid.nz,
        }
        for key, expected in exact_values.items():
            if int(archive[key].item()) != expected:
                raise ValueError(f"forward restart geometry mismatch at {key}")
        real_values = {
            "radius_m": geometry.grid.radius_m,
            "height_m": geometry.grid.height_m,
            "liquid_height_m": geometry.liquid_height_m,
            "target_bottom_m": geometry.target_bottom_m,
            "target_top_m": geometry.target_top_m,
            "target_radius_m": geometry.target_radius_m,
            "target_liquid_volume_m3": geometry.target_liquid_volume_m3,
        }
        for key, expected in real_values.items():
            if not math.isclose(
                float(archive[key].item()), expected, rel_tol=2.0e-14, abs_tol=1.0e-16
            ):
                raise ValueError(f"forward restart geometry mismatch at {key}")
        state = ForwardState(
            float(archive["circulation_m2_s"].item()),
            np.asarray(archive["phase_order_parameter"], dtype=np.float64),
        )
    _validate_forward_state_geometry(
        state,
        geometry,
        volume_relative_tolerance=config.forward.volume_relative_tolerance,
    )
    return state


def _output_times(duration_s: float, interval_s: float) -> FloatArray:
    count = int(math.floor(duration_s / interval_s + 1.0e-13))
    values = np.arange(count + 1, dtype=np.float64) * interval_s
    if values[-1] < duration_s - 1.0e-13:
        values = np.append(values, duration_s)
    else:
        values[-1] = duration_s
    return values


def actual_bar_omega_rad_s(config: SimulationConfig, time_s: float) -> float:
    """Piecewise-linear prescribed *actual* bar speed from the input contract."""

    schedule_time = config.drive.actual_speed_schedule_time_s
    if schedule_time:
        rpm = float(
            np.interp(
                time_s,
                np.asarray(schedule_time, dtype=np.float64),
                np.asarray(config.drive.actual_speed_schedule_rpm, dtype=np.float64),
            )
        )
        return rpm * (2.0 * math.pi / 60.0)
    ramp = config.drive.ramp_duration_s
    if ramp <= 0.0 or time_s >= ramp:
        rpm = config.drive.target_rpm
    else:
        fraction = max(0.0, time_s) / ramp
        rpm = config.drive.initial_rpm + fraction * (
            config.drive.target_rpm - config.drive.initial_rpm
        )
    return (
        config.drive.rotation_direction
        * float(rpm)
        * (2.0 * math.pi / 60.0)
    )


def _advance_circulation_on_linear_segment(
    circulation_m2_s: float,
    omega_start_rad_s: float,
    omega_slope_rad_s2: float,
    dt_s: float,
    coupling_m2: float,
    spin_up_time_s: float,
) -> float:
    decay = math.exp(-dt_s / spin_up_time_s)
    steady_offset_start = coupling_m2 * (
        omega_start_rad_s - omega_slope_rad_s2 * spin_up_time_s
    )
    omega_end = omega_start_rad_s + omega_slope_rad_s2 * dt_s
    steady_offset_end = coupling_m2 * (
        omega_end - omega_slope_rad_s2 * spin_up_time_s
    )
    return steady_offset_end + (circulation_m2_s - steady_offset_start) * decay


def advance_circulation_exact(
    circulation_m2_s: float,
    config: SimulationConfig,
    time_s: float,
    dt_s: float,
) -> float:
    """Exact F21 update for constant/linear drive, including a ramp crossing."""

    if dt_s < 0.0 or not math.isfinite(dt_s):
        raise ValueError("circulation step must be finite and non-negative")
    if dt_s == 0.0:
        return float(circulation_m2_s)
    result = float(circulation_m2_s)
    current = float(time_s)
    remaining = float(dt_s)
    schedule_time = config.drive.actual_speed_schedule_time_s
    ramp_end = config.drive.ramp_duration_s
    while remaining > 1.0e-15:
        if schedule_time:
            knots = np.asarray(schedule_time, dtype=np.float64)
            speeds = np.asarray(
                config.drive.actual_speed_schedule_rpm, dtype=np.float64
            ) * (2.0 * math.pi / 60.0)
            index = int(np.searchsorted(knots, current, side="right") - 1)
            index = max(0, min(index, knots.size - 1))
            if index < knots.size - 1:
                next_knot = float(knots[index + 1])
                step = min(remaining, max(next_knot - current, 0.0))
                if step <= 1.0e-15:
                    current = next_knot
                    continue
                slope = float(
                    (speeds[index + 1] - speeds[index])
                    / (knots[index + 1] - knots[index])
                )
            else:
                step = remaining
                slope = 0.0
        elif current < ramp_end:
            step = min(remaining, ramp_end - current)
            slope_rpm_s = (
                (config.drive.target_rpm - config.drive.initial_rpm) / ramp_end
                if ramp_end > 0.0
                else 0.0
            )
            slope = (
                config.drive.rotation_direction
                * slope_rpm_s
                * (2.0 * math.pi / 60.0)
            )
        else:
            step = remaining
            slope = 0.0
        result = _advance_circulation_on_linear_segment(
            result,
            actual_bar_omega_rad_s(config, current),
            slope,
            step,
            config.forward.circulation_coupling_m2,
            config.forward.spin_up_time_s,
        )
        current += step
        remaining -= step
    return result


def axial_swirl_shape(config: SimulationConfig, z_m: NDArray[np.floating]) -> FloatArray:
    z = np.asarray(z_m, dtype=np.float64)
    floor = config.forward.axial_swirl_floor
    decay_length = config.forward.axial_swirl_decay_length_m
    bar_top = config.stir_bar.bottom_clearance_m + 2.0 * config.stir_bar.radius_m
    height = config.container.liquid_depth_m
    numerator = floor + (1.0 - floor) * np.exp(
        -np.maximum(z - bar_top, 0.0) / decay_length
    )
    denominator = floor + (1.0 - floor) * math.exp(
        -(height - bar_top) / decay_length
    )
    return np.asarray(numerator / denominator, dtype=np.float64)


def burgers_potential_shape(dimensionless_radius: NDArray[np.floating]) -> FloatArray:
    """Stable evaluation of F(x) in frozen equations F28--F30."""

    x = np.asarray(dimensionless_radius, dtype=np.float64)
    if np.any(x < 0.0) or not np.isfinite(x).all():
        raise ValueError("dimensionless radius must be finite and non-negative")
    result = np.empty_like(x)
    small = x < 0.08
    xs = x[small]
    result[small] = (
        0.5 * xs**2 - 0.25 * xs**4 + (7.0 / 72.0) * xs**6
        - (1.0 / 32.0) * xs**8
    )
    xl = x[~small]
    if xl.size:
        square = xl * xl
        result[~small] = (
            math.log(2.0)
            + expi(-square)
            - expi(-2.0 * square)
            - np.square(1.0 - np.exp(-square)) / (2.0 * square)
        )
    return result


def burgers_tangential_velocity_field(
    config: SimulationConfig,
    grid: AxisymmetricGrid,
    circulation_m2_s: float,
) -> FloatArray:
    r = grid.r_centres[:, None]
    z_shape = axial_swirl_shape(config, grid.z_centres)[None, :]
    core = config.forward.effective_core_radius_m
    return (
        circulation_m2_s
        * z_shape
        / r
        * (1.0 - np.exp(-np.square(r / core)))
    )


def centrifugal_potential_field(
    config: SimulationConfig,
    grid: AxisymmetricGrid,
    circulation_m2_s: float,
) -> FloatArray:
    core = config.forward.effective_core_radius_m
    radial = burgers_potential_shape(grid.r_centres / core)[:, None]
    axial = axial_swirl_shape(config, grid.z_centres)[None, :]
    return circulation_m2_s**2 / core**2 * radial * axial**2


def bulk_liquid_potential_pa(
    config: SimulationConfig,
    grid: AxisymmetricGrid,
    circulation_m2_s: float,
) -> FloatArray:
    delta_density = (
        config.fluid.liquid_density_kg_m3 - config.fluid.gas_density_kg_m3
    )
    gravity = (
        delta_density
        * config.fluid.gravity_m_s2
        * (grid.z_centres[None, :] - config.container.liquid_depth_m)
    )
    swirl = (
        config.fluid.liquid_density_kg_m3
        * centrifugal_potential_field(config, grid, circulation_m2_s)
    )
    return np.broadcast_to(gravity, grid.shape) - swirl


def _contact_laws(config: SimulationConfig) -> Mapping[int, ContactLaw]:
    f = config.forward

    def law(prefix: str) -> ContactLaw:
        return ContactLaw(
            math.radians(getattr(f, f"{prefix}_receding_angle_deg")),
            math.radians(getattr(f, f"{prefix}_equilibrium_angle_deg")),
            math.radians(getattr(f, f"{prefix}_advancing_angle_deg")),
            getattr(f, f"{prefix}_contact_friction_pa_s"),
        )

    return {WALL: law("wall"), FLOOR: law("floor"), TARGET: law("target")}


def _build_geometry(config: SimulationConfig) -> tuple[VariationalGeometry, float]:
    height = config.container.liquid_depth_m + config.container.headspace_height_m
    grid = AxisymmetricGrid(
        config.forward.radial_cells,
        config.forward.axial_cells,
        config.container.radius_m,
        height,
    )
    target_bottom = config.stir_bar.bottom_clearance_m
    target_top = target_bottom + 2.0 * config.stir_bar.radius_m
    geometry = build_variational_geometry(
        grid,
        liquid_height_m=config.container.liquid_depth_m,
        target_bottom_m=target_bottom,
        target_top_m=target_top,
        target_radius_m=config.forward.effective_target_radius_m,
        top_seed_depth_m=config.forward.top_seed_depth_m,
    )
    thickness = config.forward.interface_thickness_cells * max(grid.dr, grid.dz)
    return geometry, thickness


def _surface_profile_m(q: FloatArray, geometry: VariationalGeometry) -> FloatArray:
    liquid = smooth_liquid_fraction(q)
    # Solid cells are assigned liquid-side value solely for extracting the
    # highest q=0 crossing; they remain excluded from all volume/energy sums.
    liquid = liquid.copy()
    liquid[geometry.target_mask] = 1.0
    return surface_profile(liquid, geometry.grid, level=0.5)


def _surface_functionals(
    profile_m: FloatArray, config: SimulationConfig, geometry: VariationalGeometry
) -> tuple[float, float, float, float]:
    radius = geometry.grid.r_centres
    target_columns = radius <= (
        config.forward.effective_target_radius_m + geometry.grid.dr
    )
    centre_min = float(np.min(profile_m[target_columns]))
    outer = radius >= 0.8 * config.container.radius_m
    outer_mean = float(np.mean(profile_m[outer]))
    depth_from_static = max(0.0, config.container.liquid_depth_m - centre_min)
    visible_depth = max(0.0, outer_mean - centre_min)
    profile_range = float(np.max(profile_m) - np.min(profile_m))
    gap = centre_min - geometry.target_top_m
    return depth_from_static, visible_depth, profile_range, gap


def _crossing_time(
    time_a: float, time_b: float, value_a: float, value_b: float, threshold: float
) -> float:
    if value_b == value_a:
        return float(time_b)
    fraction = (threshold - value_a) / (value_b - value_a)
    return float(time_a + min(1.0, max(0.0, fraction)) * (time_b - time_a))


class ForwardTopologyBackend:
    """Reference solver for F1.0, including topology and dynamic wetting."""

    mode = BackendMode.FORWARD
    backend_id = "forward"
    display_name = "F1.0 体积约束变分界面前向求解器"

    def run(
        self,
        config: SimulationConfig,
        control: Control | None = None,
        progress: ProgressCallback | None = None,
        *,
        initial_state: ForwardState | None = None,
    ) -> SimulationResult:
        if config.backend is not BackendMode.FORWARD:
            raise ValueError(
                "ForwardTopologyBackend requires config.backend=BackendMode.FORWARD"
            )
        if config.forward.model_branch == "AV-FWD-SH-v1":
            if initial_state is not None:
                raise ValueError("SH requires its own initial surface/flow state, not a CP phase state")
            from .forward_stokes import ForwardStokesBackend
            return ForwardStokesBackend().run(config, control, progress)
        if config.forward.model_branch == "AV-FWD-TF-v1":
            if initial_state is not None:
                raise ValueError("TF requires its own local film state, not a CP phase state")
            from .forward_film import ForwardFilmBackend
            return ForwardFilmBackend().run(config, control, progress)
        if config.forward.model_branch != THEORY_ID:
            raise ValueError("unknown forward model branch; implicit fallback is forbidden")
        geometry, thickness = _build_geometry(config)
        grid = geometry.grid
        laws = _contact_laws(config)
        configured_restart = bool(config.forward.initial_state_npz_path)
        if initial_state is not None and configured_restart:
            raise ValueError(
                "supply the initial state either through config NPZ or the Python API, not both"
            )
        if configured_restart:
            initial_state = load_forward_state_npz(
                config.forward.initial_state_npz_path,
                config.forward.initial_state_sha256,
                config,
            )
            initial_state_kind = "verified_npz_forward_state"
        if initial_state is None:
            q = initialise_flat_order_parameter(
                geometry,
                thickness,
                volume_relative_tolerance=config.forward.volume_relative_tolerance,
            )
            circulation = config.forward.initial_circulation_m2_s
            initial_state_kind = "flat_volume_projected"
        else:
            if initial_state.phase_order_parameter.shape != grid.shape:
                raise ValueError(
                    "initial phase shape does not match configured grid: "
                    f"{initial_state.phase_order_parameter.shape} != {grid.shape}"
                )
            q = initial_state.phase_order_parameter.copy()
            if np.any(q[geometry.top_reservoir_mask] != -1.0):
                raise ValueError("initial state violates fixed top gas reservoir")
            if np.any(q[geometry.target_mask] != 1.0):
                raise ValueError("initial state penetrates the effective solid target")
            initial_volume = liquid_volume_m3(q, geometry)
            relative_error = abs(
                initial_volume - geometry.target_liquid_volume_m3
            ) / geometry.target_liquid_volume_m3
            if relative_error > config.forward.volume_relative_tolerance:
                raise ValueError(
                    "initial state liquid volume differs from configured target: "
                    f"relative error={relative_error:.6e}"
                )
            circulation = initial_state.circulation_m2_s
            if not configured_restart:
                initial_state_kind = "explicit_python_forward_state"
        initial_phase = q.copy()
        initial_circulation = float(circulation)

        requested_times = _output_times(
            config.run.duration_s, config.run.output_interval_s
        )
        current_time = 0.0
        status = RunStatus.COMPLETED
        events: list[Event] = []
        maximum_rejections = 0
        accepted_internal_steps = 0
        maximum_phase_change = 0.0
        minimum_accepted_dt = math.inf
        maximum_abs_volume_error = 0.0
        maximum_energy_increase = 0.0
        physical_refinements = 0
        first_invalid_time: float | None = None
        last_valid_time: float | None = None
        validity_interval: tuple[float, float] | None = None
        validity_reason = "DECLARED_CONDITIONAL_MODEL_CHECKS_PASSED"
        validity_transition_phase: FloatArray | None = None
        validity_previous_phase: FloatArray | None = None
        numerical_failure_interval: tuple[float, float] | None = None
        numerical_failure_reason: str | None = None
        validity_mask_values: list[float] = []
        gas_summary_values: list[dict] = []
        gas_pressure_evaluated_values: list[float] = []
        gas_pressure_max_values: list[float] = []
        gas_section_z_values: list[FloatArray] = []
        gas_area_profile_values: list[FloatArray] = []
        gas_area_rate_profile_values: list[FloatArray] = []
        gas_flow_profile_values: list[FloatArray] = []
        gas_pressure_profile_values: list[FloatArray] = []
        gas_section_evaluated_values: list[float] = []

        def inspect_state(phase, state_circulation, time_s, previous_sections):
            topology = topology_diagnostics(
                phase, geometry, interface_thickness_m=thickness,
                minimum_resolved_throat_cells=config.forward.minimum_resolved_throat_cells,
            )
            phi = centrifugal_potential_field(config, grid, state_circulation)
            gas_check = assess_forward_gas(
                config, phase, geometry, topology, time_s=time_s,
                previous_sections=previous_sections,
                surface_profile_m=_surface_profile_m(phase, geometry),
                centrifugal_potential_range_m2_s2=float(np.max(phi) - np.min(phi)),
                interface_thickness_m=thickness,
            )
            reason = (
                "ISOLATED_GAS_COMPONENT_WHOLE_INTERFACE_INVALID"
                if topology.isolated_gas_volume_m3 > 0.0 else
                "GEOMETRIC_TARGET_TOUCH_CONTINUATION_UNESTABLISHED"
                if topology.touches_target else
                gas_check.reason if not gas_check.valid else None
            )
            return topology, gas_check, reason

        current_topology, current_gas, initial_invalid_reason = inspect_state(q, circulation, 0.0, None)
        if initial_invalid_reason is None:
            last_valid_time = 0.0
        else:
            first_invalid_time = 0.0
            validity_interval = (0.0, 0.0)
            validity_reason = initial_invalid_reason
            validity_transition_phase = q.copy()
            events.append(Event(0.0, "quantitative_validity_exit", validity_reason,
                                {"bracket_start_s": 0.0, "bracket_end_s": 0.0,
                                 "whole_interface_invalid": True}))

        times: list[float] = []
        omega_values: list[float] = []
        circulation_values: list[float] = []
        profiles: list[FloatArray] = []
        depth_static_values: list[float] = []
        visible_depth_values: list[float] = []
        profile_range_values: list[float] = []
        gap_values: list[float] = []
        discrete_gap_values: list[float] = []
        touch_values: list[float] = []
        channel_values: list[float] = []
        resolved_channel_values: list[float] = []
        sustained_values: list[float] = []
        target_gas_area_values: list[float] = []
        throat_values: list[float] = []
        required_throat_values: list[float] = []
        isolated_gas_values: list[float] = []
        connected_gas_values: list[float] = []
        volume_values: list[float] = []
        volume_error_values: list[float] = []
        energy_values: list[float] = []
        surface_energy_values: list[float] = []
        bulk_energy_values: list[float] = []
        wall_energy_values: list[float] = []
        lagrange_values: list[float] = []
        pinned_values: list[float] = []
        advancing_values: list[float] = []
        receding_values: list[float] = []
        throat_profiles: list[FloatArray] = []
        gas_pressure_error_values: list[float] = []
        tangential_surface_profiles: list[FloatArray] = []

        last_lagrange = 0.0
        last_contact = None
        channel_hold_start: float | None = None
        sustained_announced = False
        ever_finite_channel = False
        isolated_announced = False
        last_topology: TopologyDiagnostics | None = None

        def snapshot(time_s: float) -> None:
            nonlocal channel_hold_start, sustained_announced
            nonlocal ever_finite_channel, isolated_announced, last_topology
            potential = bulk_liquid_potential_pa(config, grid, circulation)
            energy, _, _, _, contact = discrete_energy_and_gradient(
                q,
                geometry,
                potential,
                surface_tension_n_m=config.fluid.surface_tension_n_m,
                interface_thickness_m=thickness,
                contact_laws=laws,
                apply_hysteresis=False,
            )
            topology = current_topology
            profile_m = _surface_profile_m(q, geometry)
            depth_static, visible_depth, profile_range, profile_gap = (
                _surface_functionals(profile_m, config, geometry)
            )
            # The profile gap is continuous enough for sub-output interpolation
            # before first contact.  The binary connected-gas distance is kept
            # separately as a conservative topology-resolution diagnostic.
            gap = profile_gap
            volume = liquid_volume_m3(q, geometry)
            volume_error = volume - geometry.target_liquid_volume_m3
            physical_channel = (
                topology.finite_channel_candidate
                and topology.target_gas_area_m2 > 0.0
                and topology.minimum_throat_radius_m
                >= config.forward.stable_channel_radius_m
            )
            numerically_eligible = physical_channel and topology.resolved_channel
            depth_threshold = config.forward.air_core_depth_threshold_m
            if depth_static >= depth_threshold and (
                not depth_static_values or depth_static_values[-1] < depth_threshold
            ):
                event_time = (
                    0.0
                    if not depth_static_values
                    else _crossing_time(
                        times[-1],
                        time_s,
                        depth_static_values[-1],
                        depth_static,
                        depth_threshold,
                    )
                )
                events.append(
                    Event(
                        event_time,
                        "funnel_depth_threshold",
                        "surface depth crossed the configured reporting threshold",
                        {"threshold_m": depth_threshold},
                    )
                )
            if numerically_eligible:
                if channel_hold_start is None:
                    channel_hold_start = time_s
            else:
                channel_hold_start = None
                sustained_announced = False
            sustained = (
                channel_hold_start is not None
                and time_s - channel_hold_start
                >= config.forward.stable_channel_hold_time_s - 1.0e-14
            )

            previous = last_topology
            if topology.touches_target and (
                previous is None or not previous.touches_target
            ):
                touch_time = (
                    _crossing_time(
                        times[-1], time_s, gap_values[-1], gap, 0.0
                    )
                    if gap_values and gap_values[-1] > 0.0 and gap <= 0.0
                    else time_s
                )
                events.append(
                    Event(
                        touch_time,
                        "first_target_touch",
                        "top-connected gas first touches the effective target",
                        {
                            "bracket_start_s": times[-1] if times else time_s,
                            "bracket_end_s": time_s,
                            "event_time_bracket_s": (
                                time_s - times[-1] if times else 0.0
                            ),
                        },
                    )
                )
            if topology.finite_channel_candidate and (
                previous is None or not previous.finite_channel_candidate
            ):
                kind = "channel_reconnection" if ever_finite_channel else "finite_channel"
                events.append(
                    Event(
                        time_s,
                        kind,
                        "finite top-connected channel candidate appeared",
                        {
                            "minimum_throat_radius_m": (
                                topology.minimum_throat_radius_m
                            ),
                            "resolved": topology.resolved_channel,
                        },
                    )
                )
                ever_finite_channel = True
            if (
                previous is not None
                and previous.finite_channel_candidate
                and not topology.finite_channel_candidate
            ):
                events.append(
                    Event(time_s, "channel_break", "top-connected channel disconnected")
                )
            if sustained and not sustained_announced:
                events.append(
                    Event(
                        time_s,
                        "sustained_channel",
                        "resolved channel met the pre-registered radius/hold contract",
                        {
                            "radius_threshold_m": config.forward.stable_channel_radius_m,
                            "hold_time_s": config.forward.stable_channel_hold_time_s,
                        },
                    )
                )
                sustained_announced = True
            if topology.isolated_gas_volume_m3 > 0.0 and not isolated_announced:
                events.append(
                    Event(
                        time_s,
                        "isolated_gas_component",
                        "isolated gas appeared; the whole interface is quantitatively invalid",
                    )
                )
                isolated_announced = True

            gas_diagnostic = current_gas.diagnostic
            gas_error = gas_diagnostic.epsilon_g if gas_diagnostic.epsilon_g is not None else 0.0
            # Dense numeric arrays use finite placeholders only alongside an
            # explicit availability mask.  Missing evidence is null in JSON
            # and invalid in the scientific contract, never a pressure PASS.
            gas_pressure_evaluated_values.append(float(gas_diagnostic.epsilon_g is not None))
            gas_pressure_max_values.append(gas_diagnostic.max_abs_pressure_pa or 0.0)
            gas_summary_values.append(current_gas.summary())
            validity_mask_values.append(float(first_invalid_time is None))
            count = config.gas.channel_section_count
            section_available = gas_diagnostic.z_m.size == count
            gas_section_evaluated_values.append(float(section_available))
            for values, source in (
                (gas_section_z_values, gas_diagnostic.z_m),
                (gas_area_profile_values, gas_diagnostic.area_m2),
                (gas_area_rate_profile_values, gas_diagnostic.area_rate_fixed_m2_s),
                (gas_flow_profile_values, gas_diagnostic.flow_m3_s),
                (gas_pressure_profile_values, gas_diagnostic.pressure_relative_pa),
            ):
                values.append(np.array(source, copy=True) if section_available else np.zeros(count))

            omega = actual_bar_omega_rad_s(config, time_s)
            velocity = burgers_tangential_velocity_field(config, grid, circulation)
            surface_j = int(
                np.argmin(np.abs(grid.z_centres - config.container.liquid_depth_m))
            )
            times.append(float(time_s))
            omega_values.append(omega)
            circulation_values.append(float(circulation))
            profiles.append(profile_m)
            depth_static_values.append(depth_static)
            visible_depth_values.append(visible_depth)
            profile_range_values.append(profile_range)
            gap_values.append(gap)
            discrete_gap_values.append(topology.minimum_gap_to_target_m)
            touch_values.append(float(topology.touches_target))
            channel_values.append(float(topology.finite_channel_candidate))
            resolved_channel_values.append(float(topology.resolved_channel))
            sustained_values.append(float(sustained))
            target_gas_area_values.append(topology.target_gas_area_m2)
            throat_values.append(topology.minimum_throat_radius_m)
            required_throat_values.append(topology.required_resolved_radius_m)
            isolated_gas_values.append(topology.isolated_gas_volume_m3)
            connected_gas_values.append(topology.top_connected_gas_volume_m3)
            volume_values.append(volume)
            volume_error_values.append(volume_error)
            energy_values.append(energy.total_j)
            surface_energy_values.append(energy.surface_j)
            bulk_energy_values.append(energy.bulk_potential_j)
            wall_energy_values.append(energy.wall_j)
            lagrange_values.append(last_lagrange)
            pinned_values.append(float(contact.pinned_faces))
            advancing_values.append(float(contact.advancing_faces))
            receding_values.append(float(contact.receding_faces))
            throat_profiles.append(topology.throat_radius_by_height_m)
            gas_pressure_error_values.append(gas_error)
            tangential_surface_profiles.append(velocity[:, surface_j])
            last_topology = topology

        snapshot(0.0)
        if progress is not None:
            progress(
                Progress(
                    fraction=0.0,
                    simulated_time_s=0.0,
                    step=0,
                    stage="running",
                    message="F1.0 variational interface initialised",
                    metrics={
                        "radial_cells": grid.nr,
                        "axial_cells": grid.nz,
                        "interface_thickness_m": thickness,
                    },
                )
            )

        for output_index, output_time in enumerate(requested_times[1:], start=1):
            if first_invalid_time is not None and config.forward.prediction_mode == "strict":
                break
            while current_time < output_time - 1.0e-14:
                if control is not None and not control.checkpoint():
                    status = RunStatus.STOPPED
                    break
                proposed_dt = min(
                    config.forward.integration_time_step_s,
                    float(output_time - current_time),
                )
                # A phase step may reduce dt for the maximum-change or energy
                # gate.  Recompute C and Phi at that accepted time so no future
                # forcing leaks into a shortened interface step.
                while True:
                    trial_circulation = advance_circulation_exact(
                        circulation, config, current_time, proposed_dt
                    )
                    potential = bulk_liquid_potential_pa(
                        config, grid, trial_circulation
                    )
                    try:
                        trial_q, step_diagnostics = advance_variational_phase(
                            q,
                            geometry,
                            potential,
                            dt_s=proposed_dt,
                            surface_tension_n_m=config.fluid.surface_tension_n_m,
                            interface_thickness_m=thickness,
                            interface_mobility_m_pa_s=(
                                config.forward.interface_mobility_m_pa_s
                            ),
                            contact_laws=laws,
                            target_volume_m3=geometry.target_liquid_volume_m3,
                            max_phase_change=config.forward.max_phase_change_per_step,
                            volume_relative_tolerance=(
                                config.forward.volume_relative_tolerance
                            ),
                            energy_relative_tolerance=(
                                config.forward.energy_relative_tolerance
                            ),
                            maximum_rejections=(
                                config.forward.maximum_step_rejections
                            ),
                        )
                    except VariationalStepError as exc:
                        events.append(
                            Event(
                                current_time,
                                "numerical_failure",
                                str(exc),
                                {"proposed_dt_s": proposed_dt},
                            )
                        )
                        status = RunStatus.FAILED
                        numerical_failure_interval = (current_time, current_time + proposed_dt)
                        numerical_failure_reason = str(exc)
                        break
                    accepted_dt = step_diagnostics.accepted_dt_s
                    if accepted_dt < proposed_dt * (1.0 - 1.0e-12):
                        proposed_dt = accepted_dt
                        continue
                    trial_topology, trial_gas, invalid_reason = inspect_state(
                        trial_q, trial_circulation, current_time + accepted_dt, current_gas.sections,
                    )
                    # Refine a newly detected physical transition using the
                    # same evolution from the last accepted state.  The final
                    # bracket is evidence, not an invented root precision.
                    if (
                        first_invalid_time is None and invalid_reason is not None
                        and accepted_dt > config.forward.validity_event_tolerance_s
                    ):
                        proposed_dt = 0.5 * accepted_dt
                        physical_refinements += 1
                        continue
                    if first_invalid_time is None:
                        if invalid_reason is None:
                            last_valid_time = current_time + accepted_dt
                        else:
                            first_invalid_time = current_time + accepted_dt
                            validity_interval = (current_time, first_invalid_time)
                            validity_reason = invalid_reason
                            validity_previous_phase = q.copy()
                            validity_transition_phase = trial_q.copy()
                            events.append(Event(
                                first_invalid_time, "quantitative_validity_exit", validity_reason,
                                {"bracket_start_s": current_time,
                                 "bracket_end_s": first_invalid_time,
                                 "whole_interface_invalid": True},
                            ))
                    q = trial_q
                    circulation = trial_circulation
                    current_time += accepted_dt
                    current_topology = trial_topology
                    current_gas = trial_gas
                    accepted_internal_steps += 1
                    maximum_rejections = max(
                        maximum_rejections, step_diagnostics.rejected_attempts
                    )
                    maximum_phase_change = max(
                        maximum_phase_change, step_diagnostics.maximum_phase_change
                    )
                    minimum_accepted_dt = min(minimum_accepted_dt, accepted_dt)
                    last_lagrange = step_diagnostics.lagrange_pressure_pa
                    last_contact = step_diagnostics.contact
                    volume_error = (
                        step_diagnostics.volume_after_m3
                        - geometry.target_liquid_volume_m3
                    )
                    maximum_abs_volume_error = max(
                        maximum_abs_volume_error, abs(volume_error)
                    )
                    maximum_energy_increase = max(
                        maximum_energy_increase,
                        step_diagnostics.energy_after_j
                        - step_diagnostics.energy_before_j,
                    )
                    break
                if status is RunStatus.FAILED:
                    break
                if first_invalid_time is not None and config.forward.prediction_mode == "strict":
                    break
            if status in {RunStatus.STOPPED, RunStatus.FAILED} or (
                first_invalid_time is not None and config.forward.prediction_mode == "strict"
            ):
                if current_time > times[-1] + 1.0e-14:
                    snapshot(current_time)
                break
            snapshot(float(output_time))
            if progress is not None:
                progress(
                    Progress(
                        fraction=float(output_time / config.run.duration_s),
                        simulated_time_s=float(output_time),
                        step=output_index,
                        stage="running",
                        message="F1.0 interface evolution",
                        metrics={
                            "minimum_gap_m": gap_values[-1],
                            "minimum_throat_radius_m": throat_values[-1],
                            "liquid_volume_error_m3": volume_error_values[-1],
                        },
                    )
                )

        if status is RunStatus.STOPPED:
            events.append(
                Event(
                    float(times[-1]),
                    "soft_stop",
                    control.stop_reason if control is not None else "stop requested",
                )
            )
        elif status is RunStatus.COMPLETED and first_invalid_time is None and progress is not None:
            progress(
                Progress(
                    fraction=1.0,
                    simulated_time_s=config.run.duration_s,
                    step=len(times) - 1,
                    stage="completed",
                    message="F1.0 forward run completed",
                )
            )

        time_array = np.asarray(times, dtype=np.float64)
        circulation_array = np.asarray(circulation_values, dtype=np.float64)
        final_phi = centrifugal_potential_field(config, grid, circulation)
        final_velocity = burgers_tangential_velocity_field(config, grid, circulation)
        final_curvature = phase_curvature_m_inv(q, geometry)
        final_liquid = smooth_liquid_fraction(q)
        final_liquid[geometry.target_mask] = 0.0
        if last_topology is None:  # pragma: no cover - initial snapshot sets it
            raise RuntimeError("topology diagnostics missing")
        any_isolated = bool(np.any(np.asarray(isolated_gas_values) > 0.0))
        gas_invalid = any(not record["valid"] for record in gas_summary_values)
        scientific_status = (
            "NUMERICAL_FAILURE" if status is RunStatus.FAILED else
            "QUANTITATIVE_VALIDITY_EXIT" if first_invalid_time is not None else
            "GAS_PRESSURE_CLOSURE_INVALID"
            if gas_invalid
            else "QUALITATIVE_AXISYMMETRIC_ONLY"
            if any_isolated
            else "GENERALIZED_TOPOLOGY_SOLUTION"
            if ever_finite_channel
            else "CLASSICAL_SOLUTION"
        )
        termination_reason = (
            "numerical_failure" if status is RunStatus.FAILED else
            "soft_stop" if status is RunStatus.STOPPED else
            "quantitative_validity_exit" if first_invalid_time is not None and config.forward.prediction_mode == "strict" else
            "requested_duration_reached_diagnostic" if first_invalid_time is not None else
            "requested_duration_reached"
        )
        envelope_length = min(
            config.container.radius_m, config.container.liquid_depth_m
        )
        fast_wave_time = max(
            math.sqrt(envelope_length / config.fluid.gravity_m_s2),
            math.sqrt(
                config.fluid.liquid_density_kg_m3
                * envelope_length**3
                / config.fluid.surface_tension_n_m
            ),
        )
        if config.drive.actual_speed_schedule_time_s:
            schedule_times = config.drive.actual_speed_schedule_time_s
            schedule_speeds = config.drive.actual_speed_schedule_rpm
            speed_change = max(schedule_speeds) - min(schedule_speeds)
            positive_durations = [
                right - left
                for left, right in zip(schedule_times, schedule_times[1:])
                if right > left
            ]
            drive_change_time = min(positive_durations) if positive_durations else 0.0
        else:
            speed_change = abs(config.drive.target_rpm - config.drive.initial_rpm)
            drive_change_time = config.drive.ramp_duration_s
        if speed_change == 0.0:
            wave_error = 0.0
            wave_error_status = "NO_DRIVE_CHANGE"
        elif drive_change_time > 0.0:
            wave_error = fast_wave_time / drive_change_time
            wave_error_status = (
                "PASS"
                if wave_error <= config.forward.wave_envelope_error_max
                else "INTERFACE_KINETICS_WARNING"
            )
        else:
            wave_error = 0.0
            wave_error_status = "UNBOUNDED_FOR_IDEAL_SPEED_STEP"
        theory_path = Path(__file__).resolve().parents[4] / "01_最终理论" / "01_前向预测理论.md"
        theory_sha256 = _sha256_file(theory_path)
        if status is RunStatus.FAILED:
            quantitative_status = "numerical_failure"
            validity_mask_values = [0.0 for _ in times]
            contract_valid_until = None
            contract_first_invalid = numerical_failure_interval[1]
            contract_interval = numerical_failure_interval
            contract_reason = numerical_failure_reason or "NUMERICAL_STEP_FAILED"
        else:
            quantitative_status = (
                "physical_exit" if first_invalid_time is not None and config.forward.prediction_mode == "strict" else
                "diagnostic_continuation" if first_invalid_time is not None else
                "user_stopped" if status is RunStatus.STOPPED else "valid"
            )
            contract_valid_until = last_valid_time
            contract_first_invalid = first_invalid_time
            contract_interval = validity_interval
            contract_reason = validity_reason
        quantitative_contract = make_quantitative_validity(
            model_branch=THEORY_ID, theory_sha256=theory_sha256,
            implementation_id=MODEL_ID + "/" + NUMERICAL_METHOD_ID,
            status=quantitative_status,
            valid_until_s=contract_valid_until,
            first_invalid_time_s=contract_first_invalid,
            termination_interval_s=contract_interval,
            reason=contract_reason,
            diagnostic_continuation=quantitative_status == "diagnostic_continuation",
            claim_scope="none" if status is RunStatus.FAILED else "conditional_model_numerics",
        )
        metadata = {
            "model_id": MODEL_ID,
            "theory_id": THEORY_ID,
            "theory_sha256": theory_sha256,
            "quantitative_validity": quantitative_contract,
            "numerical_method_id": NUMERICAL_METHOD_ID,
            "model_role": "forward_theory_numerical_implementation",
            "initial_state_kind": initial_state_kind,
            "initial_circulation_m2_s": initial_circulation,
            "initial_state_sha256": (
                config.forward.initial_state_sha256 if configured_restart else ""
            ),
            "initial_state_file_name": (
                Path(config.forward.initial_state_npz_path).name
                if configured_restart
                else ""
            ),
            "drive_interpretation": "prescribed_actual_stir_bar_speed",
            "drive_input_form": (
                "signed_piecewise_linear_actual_rpm_schedule"
                if config.drive.actual_speed_schedule_time_s
                else "initial_target_linear_ramp_actual_rpm"
            ),
            "domain_status": (
                "IN_MODEL_DOMAIN"
                if scientific_status
                in {"CLASSICAL_SOLUTION", "GENERALIZED_TOPOLOGY_SOLUTION"}
                else scientific_status
            ),
            "solution_class": scientific_status,
            "termination_reason": termination_reason,
            "topology_change_simulated": True,
            "wetting_and_contact_line_simulated": True,
            "post_contact_interpretation": "regularised continuation is diagnostic only",
            "uses_high_fidelity_equations": False,
            "bubble_quantification": (
                "QUALITATIVE_AXISYMMETRIC_ONLY" if any_isolated else "not_triggered"
            ),
            "unassessed_validity_gates": [
                "SWIRL_CLOSURE_REQUIRES_PIV",
                "AXISYMMETRY_REQUIRES_3D_OBSERVATION",
                "INTERFACE_MOBILITY_REQUIRES_MULTI_SCALE_STEP_TESTS",
            ],
            "validity_thresholds": {
                "swirl_profile_error_max": config.forward.swirl_profile_error_max,
                "axisymmetry_error_max": config.forward.axisymmetry_error_max,
                "wave_envelope_error_max": config.forward.wave_envelope_error_max,
                "gas_pressure_error_max": config.forward.gas_pressure_error_max,
            },
            "wave_envelope_check": {
                "fast_wave_time_s": fast_wave_time,
                "drive_change_time_s": drive_change_time,
                "epsilon_wave": wave_error,
                "status": wave_error_status,
                "ideal_step_interpretation": (
                    "epsilon_wave is formally unbounded when speed changes at zero ramp"
                ),
            },
            "parameter_roles_required": (
                "MEASURED/CALIBRATED_FLOW/CALIBRATED_INTERFACE/"
                "CALIBRATED_WETTING/FIXED_REFERENCE"
            ),
            "event_time_localisation": (
                "quantitative exit checked at each accepted internal step and refined; "
                "the termination interval bounds the last confirmed valid and first "
                "confirmed invalid state; legacy diagnostic topology labels remain snapshots"
            ),
            "event_time_nominal_resolution_s": config.forward.validity_event_tolerance_s,
            "gas_pressure_diagnostics": gas_summary_values,
            "dense_gas_array_contract": (
                "zero placeholders are unavailable when gas_pressure_evaluated_mask or "
                "gas_section_evaluated_mask is zero; use nullable JSON diagnostics for "
                "open-reservoir bounds and absent pressure maxima"
            ),
            "geometry": {
                "domain_height_m": grid.height_m,
                "effective_target_bottom_m": geometry.target_bottom_m,
                "effective_target_top_m": geometry.target_top_m,
                "effective_target_radius_m": geometry.target_radius_m,
                "grid_aligned_target_cell_count": int(np.sum(geometry.target_mask)),
                "target_liquid_volume_m3": geometry.target_liquid_volume_m3,
                "derived_initial_liquid_mass_kg": (
                    geometry.target_liquid_volume_m3
                    * config.fluid.liquid_density_kg_m3
                ),
                "exact_effective_target_volume_m3": (
                    geometry.exact_target_solid_volume_m3
                ),
                "rasterized_effective_target_volume_m3": (
                    geometry.rasterized_target_solid_volume_m3
                ),
                "target_rasterization_volume_error_m3": (
                    geometry.rasterized_target_solid_volume_m3
                    - geometry.exact_target_solid_volume_m3
                ),
            },
            "discretisation": {
                "radial_cells": grid.nr,
                "axial_cells": grid.nz,
                "dr_m": grid.dr,
                "dz_m": grid.dz,
                "interface_thickness_m": thickness,
                "interface_thickness_cells": config.forward.interface_thickness_cells,
                "minimum_resolved_throat_cells": (
                    config.forward.minimum_resolved_throat_cells
                ),
                "accepted_internal_steps": accepted_internal_steps,
                "physical_transition_refinement_attempts": physical_refinements,
                "validity_checked_at_each_accepted_step": True,
                "minimum_accepted_dt_s": (
                    minimum_accepted_dt if math.isfinite(minimum_accepted_dt) else 0.0
                ),
                "maximum_backtracking_rejections": maximum_rejections,
                "maximum_phase_change": maximum_phase_change,
                "maximum_abs_liquid_volume_error_m3": maximum_abs_volume_error,
                "maximum_fixed_forcing_energy_increase_j": maximum_energy_increase,
                "volume_gate_passed": maximum_abs_volume_error
                <= config.forward.volume_relative_tolerance
                * geometry.target_liquid_volume_m3,
                "energy_gate_passed": maximum_energy_increase
                <= config.forward.energy_relative_tolerance
                * max(abs(energy_values[-1]), 1.0e-18),
            },
            "contact_law": {
                "pinned_advancing_receding_discretised": True,
                "last_pinned_faces": (
                    last_contact.pinned_faces if last_contact is not None else 0
                ),
                "last_advancing_faces": (
                    last_contact.advancing_faces if last_contact is not None else 0
                ),
                "last_receding_faces": (
                    last_contact.receding_faces if last_contact is not None else 0
                ),
            },
            "channel_resolution": {
                "continuous_definition": "F74/F77",
                "one_cell_path_is_evidence": False,
                "final_required_resolved_radius_m": (
                    last_topology.required_resolved_radius_m
                ),
                "final_resolved": last_topology.resolved_channel,
            },
            "observable_units": {
                "quantitative_valid_mask": "1",
                "gas_pressure_evaluated_mask": "1",
                "gas_section_evaluated_mask": "1",
                "gas_pressure_max_abs_pa": "Pa",
                "gas_section_z_m": "m",
                "gas_section_area_m2": "m^2",
                "gas_section_area_rate_m2_s": "m^2 s^-1",
                "gas_section_flow_m3_s": "m^3 s^-1",
                "gas_section_pressure_relative_pa": "Pa",
                "actual_bar_rpm": "rpm",
                "actual_angular_velocity_rad_s": "rad s^-1",
                "burgers_C_m2_s": "m^2 s^-1",
                "circulation_m2_s": "m^2 s^-1",
                "physical_circulation_gamma_m2_s": "m^2 s^-1",
                "surface_depth_m": "m",
                "funnel_depth_m": "m",
                "visible_funnel_depth_m": "m",
                "surface_profile_range_m": "m",
                "surface_profile_m": "m",
                "minimum_gap_m": "m",
                "min_gap_to_bar_m": "m",
                "discrete_top_connected_gap_m": "m",
                "target_touch": "1",
                "finite_channel": "1",
                "resolved_channel": "1",
                "sustained_channel": "1",
                "target_gas_area_m2": "m^2",
                "minimum_throat_radius_m": "m",
                "required_resolved_throat_radius_m": "m",
                "isolated_gas_volume_m3": "m^3",
                "top_connected_gas_volume_m3": "m^3",
                "liquid_volume_m3": "m^3",
                "liquid_volume_error_m3": "m^3",
                "forward_effective_energy_j": "J",
                "surface_energy_j": "J",
                "bulk_potential_energy_j": "J",
                "wall_energy_j": "J",
                "volume_lagrange_pressure_pa": "Pa",
                "pinned_contact_faces": "1",
                "advancing_contact_faces": "1",
                "receding_contact_faces": "1",
                "gas_pressure_closure_error": "1",
                "channel_throat_profile_m": "m",
                "tangential_velocity_profile_m_s": "m s^-1",
            },
            "field_units": {
                "r_m": "m",
                "z_m": "m",
                "final_phase_order_parameter": "1",
                "initial_phase_order_parameter": "1",
                "final_liquid_fraction": "1",
                "final_top_connected_gas_mask": "1",
                "final_centrifugal_potential_m2_s2": "m^2 s^-2",
                "final_tangential_velocity_m_s": "m s^-1",
                "final_curvature_m_inv": "m^-1",
                "fluid_mask": "1",
                "effective_target_mask": "1",
                "frame_time_s": "s",
                "phase_liquid_fraction": "1",
                "u_r_m_s": "m s^-1",
                "u_z_m_s": "m s^-1",
                "u_theta_m_s": "m s^-1",
            },
            "limitations": [
                "axisymmetric averaged interface, not instantaneous 3D flow",
                "diffuse-interface result requires epsilon/grid/time convergence",
                "effective central target replaces the horizontal bar geometry",
                "gas pressure is uniform only while the reported closure check passes",
                "an isolated gas component invalidates the entire interface prediction",
                "gas derivative and tube-resistance error budgets require independent evidence",
                "conditional numerical validity does not establish calibration or apparatus accuracy",
            ],
        }
        observable_arrays = {
            "quantitative_valid_mask": np.asarray(validity_mask_values, dtype=float),
            "gas_pressure_evaluated_mask": np.asarray(gas_pressure_evaluated_values),
            "gas_section_evaluated_mask": np.asarray(gas_section_evaluated_values),
            "gas_pressure_max_abs_pa": np.asarray(gas_pressure_max_values),
            "gas_section_z_m": np.stack(gas_section_z_values),
            "gas_section_area_m2": np.stack(gas_area_profile_values),
            "gas_section_area_rate_m2_s": np.stack(gas_area_rate_profile_values),
            "gas_section_flow_m3_s": np.stack(gas_flow_profile_values),
            "gas_section_pressure_relative_pa": np.stack(gas_pressure_profile_values),
            "actual_bar_rpm": np.asarray(omega_values) * 60.0 / (2.0 * math.pi),
            "actual_angular_velocity_rad_s": np.asarray(omega_values),
            "burgers_C_m2_s": circulation_array,
            "circulation_m2_s": circulation_array,
            "physical_circulation_gamma_m2_s": 2.0 * math.pi * circulation_array,
            "surface_depth_m": np.asarray(depth_static_values),
            "funnel_depth_m": np.asarray(depth_static_values),
            "visible_funnel_depth_m": np.asarray(visible_depth_values),
            "surface_profile_range_m": np.asarray(profile_range_values),
            "surface_profile_m": np.stack(profiles),
            "minimum_gap_m": np.asarray(gap_values),
            "min_gap_to_bar_m": np.asarray(gap_values),
            "discrete_top_connected_gap_m": np.asarray(discrete_gap_values),
            "target_touch": np.asarray(touch_values),
            "finite_channel": np.asarray(channel_values),
            "resolved_channel": np.asarray(resolved_channel_values),
            "sustained_channel": np.asarray(sustained_values),
            "target_gas_area_m2": np.asarray(target_gas_area_values),
            "minimum_throat_radius_m": np.asarray(throat_values),
            "required_resolved_throat_radius_m": np.asarray(required_throat_values),
            "isolated_gas_volume_m3": np.asarray(isolated_gas_values),
            "top_connected_gas_volume_m3": np.asarray(connected_gas_values),
            "liquid_volume_m3": np.asarray(volume_values),
            "liquid_volume_error_m3": np.asarray(volume_error_values),
            "forward_effective_energy_j": np.asarray(energy_values),
            "surface_energy_j": np.asarray(surface_energy_values),
            "bulk_potential_energy_j": np.asarray(bulk_energy_values),
            "wall_energy_j": np.asarray(wall_energy_values),
            "volume_lagrange_pressure_pa": np.asarray(lagrange_values),
            "pinned_contact_faces": np.asarray(pinned_values),
            "advancing_contact_faces": np.asarray(advancing_values),
            "receding_contact_faces": np.asarray(receding_values),
            "gas_pressure_closure_error": np.asarray(gas_pressure_error_values),
            "channel_throat_profile_m": np.stack(throat_profiles),
            "tangential_velocity_profile_m_s": np.stack(
                tangential_surface_profiles
            ),
        }
        fields = {
            "r_m": grid.r_centres,
            "z_m": grid.z_centres,
            "final_phase_order_parameter": q,
            "initial_phase_order_parameter": initial_phase,
            "final_liquid_fraction": final_liquid,
            "final_top_connected_gas_mask": (
                last_topology.top_connected_gas_mask.astype(np.float64)
            ),
            "final_centrifugal_potential_m2_s2": final_phi,
            "final_tangential_velocity_m_s": final_velocity,
            "final_curvature_m_inv": final_curvature,
            "fluid_mask": geometry.fluid_mask.astype(np.float64),
            "effective_target_mask": geometry.target_mask.astype(np.float64),
            # Backend-neutral GUI aliases for one final meridional frame.  The
            # zero r/z velocities are part of the reduced forward closure, not
            # a claim that the high-fidelity liquid flow has no meridional motion.
            "frame_time_s": np.asarray([time_array[-1]], dtype=np.float64),
            "phase_liquid_fraction": final_liquid,
            "u_r_m_s": np.zeros(grid.shape, dtype=np.float64),
            "u_z_m_s": np.zeros(grid.shape, dtype=np.float64),
            "u_theta_m_s": final_velocity,
        }
        if validity_previous_phase is not None:
            fields["validity_last_confirmed_phase"] = validity_previous_phase
            metadata["field_units"]["validity_last_confirmed_phase"] = "1"
        if validity_transition_phase is not None:
            fields["validity_first_invalid_phase"] = validity_transition_phase
            metadata["field_units"]["validity_first_invalid_phase"] = "1"
        return SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=time_array,
            observables=observable_arrays,
            fields=fields,
            events=tuple(events),
            metadata=metadata,
            status=status,
        )


# Public names make the normative role explicit while preserving the runtime's
# historical class-name lookup contract.
ForwardTheoryBackend = ForwardTopologyBackend
ForwardBackend = ForwardTopologyBackend


__all__ = [
    "MODEL_ID",
    "NUMERICAL_METHOD_ID",
    "THEORY_ID",
    "FORWARD_STATE_FORMAT",
    "ForwardBackend",
    "ForwardState",
    "ForwardTheoryBackend",
    "ForwardTopologyBackend",
    "actual_bar_omega_rad_s",
    "advance_circulation_exact",
    "axial_swirl_shape",
    "bulk_liquid_potential_pa",
    "burgers_potential_shape",
    "burgers_tangential_velocity_field",
    "centrifugal_potential_field",
    "load_forward_state_npz",
    "save_forward_state_npz",
]
