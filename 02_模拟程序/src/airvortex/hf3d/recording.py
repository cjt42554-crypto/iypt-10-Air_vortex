"""HF3D frame recording, dense diagnostics, and result assembly."""

from __future__ import annotations

import math
from typing import Mapping

import numpy as np

from airvortex.core import (
    BackendMode,
    Event,
    RunStatus,
    SimulationConfig,
    SimulationResult,
)
from airvortex.hf3d.drive import drive_rpm_and_slope, rpm_to_omega
from airvortex.hf3d.pressure import PressureProjection3D
from airvortex.hf3d.types import (
    HF3DState,
    HF3DStepDiagnostics,
    TIME_STEP_LIMITER_CODES,
)
from airvortex.numerics.cartesian3d import FloatArray, ImmersedCartesianGrid
from airvortex.numerics.interface3d import (
    SurfaceGeometry3D,
    azimuthal_average_3d,
    surface_geometry_3d,
    topology_diagnostics_3d,
)
from airvortex.numerics.momentum3d import (
    laboratory_cylindrical_velocity_3d,
    rotating_to_laboratory_velocity_3d,
)


class FrameRecorder:
    """Own all sparse frame and observable series for one HF3D run."""

    def __init__(
        self,
        config: SimulationConfig,
        grid: ImmersedCartesianGrid,
        state: HF3DState,
        initial_liquid_volume_m3: float,
    ) -> None:
        self.config = config
        self.grid = grid
        self.state = state
        self.initial_liquid_volume_m3 = initial_liquid_volume_m3
        self.radial_bins = max(4, min(grid.nx, grid.ny) // 2)
        self.times: list[float] = []
        self.observable_lists: dict[str, list[float | FloatArray]] = {}
        self.phase_frames: list[FloatArray] = []
        self.ur_frames: list[FloatArray] = []
        self.uz_frames: list[FloatArray] = []
        self.utheta_frames: list[FloatArray] = []
        self.pressure_frames: list[FloatArray] = []

    def append_observable(self, name: str, value: float | FloatArray) -> None:
        self.observable_lists.setdefault(name, []).append(value)

    def surface_geometry(self) -> SurfaceGeometry3D:
        return surface_geometry_3d(
            self.state.phase,
            self.grid,
            liquid_height_m=self.config.container.liquid_depth_m,
            bar_top_m=(
                self.config.stir_bar.bottom_clearance_m
                + 2.0 * self.config.stir_bar.radius_m
            ),
            radial_bins=self.radial_bins,
        )

    def record_frame(
        self,
        diagnostics: HF3DStepDiagnostics,
    ) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
        config = self.config
        grid = self.grid
        state = self.state
        initial_volume = self.initial_liquid_volume_m3
        rpm, _ = drive_rpm_and_slope(config, state.time_s)
        omega = rpm_to_omega(rpm)
        ux, uy, uz = grid.faces_to_cells(
            state.u_x_face, state.u_y_face, state.u_z_face
        )
        ur, utheta, uz_lab = laboratory_cylindrical_velocity_3d(
            ux, uy, uz, grid, omega
        )
        radial, phase_average = azimuthal_average_3d(
            state.phase, grid, self.radial_bins
        )
        _, ur_average = azimuthal_average_3d(ur, grid, self.radial_bins)
        _, uz_average = azimuthal_average_3d(uz_lab, grid, self.radial_bins)
        _, utheta_average = azimuthal_average_3d(
            utheta, grid, self.radial_bins
        )
        _, pressure_average = azimuthal_average_3d(
            state.pressure_pa, grid, self.radial_bins
        )
        geometry = self.surface_geometry()
        self.times.append(state.time_s)
        self.phase_frames.append(phase_average)
        self.ur_frames.append(ur_average)
        self.uz_frames.append(uz_average)
        self.utheta_frames.append(utheta_average)
        self.pressure_frames.append(pressure_average)
        self.append_observable("surface_profile_m", geometry.mean_profile_m)
        self.append_observable(
            "surface_profile_minimum_m", geometry.minimum_profile_m
        )
        self.append_observable(
            "surface_profile_maximum_m", geometry.maximum_profile_m
        )
        self.append_observable("surface_depth", geometry.visible_depth_m)
        self.append_observable("surface_depth_m", geometry.visible_depth_m)
        self.append_observable("funnel_depth_m", geometry.funnel_depth_m)
        self.append_observable(
            "nominal_central_depression_m",
            geometry.nominal_central_depression_m,
        )
        self.append_observable(
            "surface_profile_range_m", geometry.profile_range_m
        )
        self.append_observable(
            "surface_min_z_m", float(np.nanmin(geometry.column_height_m))
        )
        self.append_observable("min_gap_to_bar", geometry.minimum_gap_to_bar_m)
        self.append_observable("min_gap_to_bar_m", geometry.minimum_gap_to_bar_m)
        self.append_observable(
            "minimum_gap_m", diagnostics.topology.minimum_gap_to_bar_m
        )
        self.append_observable("actual_bar_rpm", diagnostics.actual_bar_rpm)
        self.append_observable("equivalent_bar_rpm", diagnostics.actual_bar_rpm)
        self.append_observable("bar_angle_rad", state.bar_angle_rad)
        self.append_observable("liquid_volume_m3", diagnostics.liquid_volume_m3)
        self.append_observable(
            "liquid_volume_error_m3",
            diagnostics.liquid_volume_m3 - initial_volume,
        )
        self.append_observable(
            "liquid_volume_error",
            (diagnostics.liquid_volume_m3 - initial_volume) / initial_volume,
        )
        self.append_observable(
            "liquid_mass_error_relative",
            (diagnostics.liquid_volume_m3 - initial_volume) / initial_volume,
        )
        self.append_observable(
            "liquid_volume_balance_error_relative",
            diagnostics.liquid_volume_balance_error_m3 / initial_volume,
        )
        self.append_observable(
            "max_divergence", diagnostics.divergence_after_s_inv
        )
        self.append_observable(
            "divergence_before_projection", diagnostics.divergence_before_s_inv
        )
        self.append_observable(
            "pressure_residual", diagnostics.pressure_relative_residual
        )
        self.append_observable(
            "pressure_iterations", float(diagnostics.pressure_iterations)
        )
        self.append_observable("cfl_advective", diagnostics.advective_cfl)
        self.append_observable("cfl_viscous", diagnostics.viscous_cfl)
        self.append_observable("cfl_capillary", diagnostics.capillary_cfl)
        self.append_observable(
            "rotation_increment_rad", diagnostics.rotation_increment_rad
        )
        self.append_observable("kinetic_energy_j", diagnostics.kinetic_energy_lab_j)
        self.append_observable(
            "kinetic_energy_lab_j", diagnostics.kinetic_energy_lab_j
        )
        self.append_observable(
            "kinetic_energy_rotating_j", diagnostics.kinetic_energy_rotating_j
        )
        self.append_observable(
            "angular_momentum_z_kg_m2_s",
            diagnostics.angular_momentum_z_kg_m2_s,
        )
        self.append_observable(
            "viscous_dissipation_w", diagnostics.viscous_dissipation_w
        )
        self.append_observable("axisymmetry_error", geometry.axisymmetry_error)
        self.append_observable(
            "bar_gas_contact_area_m2",
            diagnostics.topology.bar_gas_contact_area_m2,
        )
        self.append_observable(
            "connection_minimum_throat_radius_m",
            diagnostics.topology.minimum_throat_radius_m,
        )
        self.append_observable(
            "connection_required_throat_radius_m",
            diagnostics.topology.required_resolved_radius_m,
        )
        self.append_observable(
            "first_contact_proxy", float(diagnostics.topology.touches_bar)
        )
        self.append_observable(
            "connection_candidate",
            float(diagnostics.topology.resolved_channel),
        )
        self.append_observable(
            "finite_channel_candidate",
            float(diagnostics.topology.finite_channel_candidate),
        )
        self.append_observable(
            "isolated_gas_volume_m3", diagnostics.topology.isolated_gas_volume_m3
        )
        return (
            radial.copy(),
            geometry.mean_profile_m.copy(),
            geometry.minimum_profile_m.copy(),
            geometry.maximum_profile_m.copy(),
        )


class StepDiagnosticsBuffer:
    """Own dense accepted-step diagnostics and their aggregate quality gates."""

    def __init__(self, initial_liquid_volume_m3: float) -> None:
        self.initial_liquid_volume_m3 = initial_liquid_volume_m3
        self.step_time: list[float] = []
        self.step_dt: list[float] = []
        self.step_initial_attempt_dt: list[float] = []
        self.step_time_limiter_code: list[float] = []
        self.step_cfl_retry_count: list[float] = []
        self.step_starting_cfl: list[float] = []
        self.step_pre_source_cfl: list[float] = []
        self.step_pre_projection_cfl: list[float] = []
        self.step_post_projection_cfl: list[float] = []
        self.step_maximum_rejected_cfl: list[float] = []
        self.step_momentum_advection_acceleration: list[float] = []
        self.step_viscous_acceleration: list[float] = []
        self.step_boundary_acceleration: list[float] = []
        self.step_surface_tension_acceleration: list[float] = []
        self.step_rotating_frame_acceleration: list[float] = []
        self.step_gravity_acceleration: list[float] = []
        self.step_pressure_projection_acceleration: list[float] = []
        self.step_clip: list[float] = []
        self.step_repair: list[float] = []
        self.step_divergence: list[float] = []
        self.step_pressure_residual: list[float] = []
        self.step_pressure_iterations: list[float] = []
        self.cumulative_clip = 0.0
        self.cumulative_repair = 0.0
        self.maximum_single_clip = 0.0
        self.maximum_single_repair = 0.0
        self.maximum_divergence = 0.0
        self.maximum_pressure_residual = 0.0
        self.maximum_cfl = 0.0
        self.maximum_rejected_cfl = 0.0
        self.cfl_retry_attempts_total = 0
        self.cfl_retried_accepted_steps = 0
        self.limiter_counts = {name: 0 for name in TIME_STEP_LIMITER_CODES}
        self.minimum_dt = math.inf

    def append(
        self,
        state_time_s: float,
        diagnostics: HF3DStepDiagnostics,
        config: SimulationConfig,
    ) -> None:
        initial_volume = self.initial_liquid_volume_m3
        self.step_time.append(state_time_s)
        self.step_dt.append(diagnostics.dt_s)
        self.step_initial_attempt_dt.append(diagnostics.initial_attempt_dt_s)
        self.step_time_limiter_code.append(
            float(TIME_STEP_LIMITER_CODES[diagnostics.time_step_limiter])
        )
        self.step_cfl_retry_count.append(float(diagnostics.cfl_retry_count))
        self.step_starting_cfl.append(diagnostics.starting_advective_cfl)
        self.step_pre_source_cfl.append(diagnostics.pre_source_advective_cfl)
        self.step_pre_projection_cfl.append(
            diagnostics.pre_projection_advective_cfl
        )
        self.step_post_projection_cfl.append(diagnostics.advective_cfl)
        self.step_maximum_rejected_cfl.append(
            diagnostics.maximum_rejected_post_projection_cfl
        )
        self.step_momentum_advection_acceleration.append(
            diagnostics.maximum_momentum_advection_acceleration_m_s2
        )
        self.step_viscous_acceleration.append(
            diagnostics.maximum_viscous_acceleration_m_s2
        )
        self.step_boundary_acceleration.append(
            diagnostics.maximum_boundary_acceleration_m_s2
        )
        self.step_surface_tension_acceleration.append(
            diagnostics.maximum_surface_tension_acceleration_m_s2
        )
        self.step_rotating_frame_acceleration.append(
            diagnostics.maximum_rotating_frame_acceleration_m_s2
        )
        self.step_gravity_acceleration.append(diagnostics.gravity_acceleration_m_s2)
        self.step_pressure_projection_acceleration.append(
            diagnostics.maximum_pressure_projection_acceleration_m_s2
        )
        self.cfl_retry_attempts_total += diagnostics.cfl_retry_count
        if diagnostics.cfl_retry_count:
            self.cfl_retried_accepted_steps += 1
        self.limiter_counts[diagnostics.time_step_limiter] += 1
        self.step_clip.append(diagnostics.phase_clip_absolute_volume_m3)
        self.step_repair.append(diagnostics.phase_repair_absolute_volume_m3)
        self.step_divergence.append(diagnostics.divergence_after_s_inv)
        self.step_pressure_residual.append(diagnostics.pressure_relative_residual)
        self.step_pressure_iterations.append(float(diagnostics.pressure_iterations))
        self.cumulative_clip += diagnostics.phase_clip_absolute_volume_m3
        self.cumulative_repair += diagnostics.phase_repair_absolute_volume_m3
        single_clip = diagnostics.phase_clip_absolute_volume_m3 / initial_volume
        single_repair = diagnostics.phase_repair_absolute_volume_m3 / initial_volume
        self.maximum_single_clip = max(self.maximum_single_clip, single_clip)
        self.maximum_single_repair = max(self.maximum_single_repair, single_repair)
        self.maximum_divergence = max(
            self.maximum_divergence, diagnostics.divergence_after_s_inv
        )
        self.maximum_pressure_residual = max(
            self.maximum_pressure_residual,
            diagnostics.pressure_relative_residual,
        )
        self.maximum_cfl = max(self.maximum_cfl, diagnostics.advective_cfl)
        self.maximum_rejected_cfl = max(
            self.maximum_rejected_cfl,
            diagnostics.maximum_rejected_post_projection_cfl,
        )
        self.minimum_dt = min(self.minimum_dt, diagnostics.dt_s)
        if (
            single_clip
            > config.high_fidelity.maximum_single_step_phase_clip_relative
            or self.cumulative_clip / initial_volume
            > config.high_fidelity.maximum_cumulative_phase_clip_relative
        ):
            raise FloatingPointError(
                "HF3D phase-clipping numerical quality gate failed: "
                f"single={single_clip:.3e}, cumulative={self.cumulative_clip / initial_volume:.3e}"
            )
        if (
            single_repair
            > config.high_fidelity.maximum_single_step_phase_repair_relative
            or self.cumulative_repair / initial_volume
            > config.high_fidelity.maximum_cumulative_phase_repair_relative
        ):
            raise FloatingPointError(
                "HF3D phase-repair numerical quality gate failed: "
                f"single={single_repair:.3e}, cumulative={self.cumulative_repair / initial_volume:.3e}"
            )




def assemble_result(
    config: SimulationConfig,
    state: HF3DState,
    grid: ImmersedCartesianGrid,
    recorder: FrameRecorder,
    diagnostics_buffer: StepDiagnosticsBuffer,
    events: list[Event],
    projection: PressureProjection3D,
    pressure_accelerator: dict[str, object],
    status: RunStatus,
    expected_liquid_volume_m3: float,
    time_tolerance: float,
    *,
    backend_id: str,
    model_name: str,
    capabilities: Mapping[str, object],
    limitations: tuple[str, ...],
    numerical_thread_policy: Mapping[str, object],
) -> SimulationResult:
    """Assemble the complete legacy-compatible HF3D result contract."""

    hf = config.high_fidelity
    initial_volume = recorder.initial_liquid_volume_m3
    expected_volume = expected_liquid_volume_m3
    radial_bins = recorder.radial_bins
    times = recorder.times
    observable_lists = recorder.observable_lists
    phase_frames = recorder.phase_frames
    ur_frames = recorder.ur_frames
    uz_frames = recorder.uz_frames
    utheta_frames = recorder.utheta_frames
    pressure_frames = recorder.pressure_frames
    step_time = diagnostics_buffer.step_time
    step_dt = diagnostics_buffer.step_dt
    step_initial_attempt_dt = diagnostics_buffer.step_initial_attempt_dt
    step_time_limiter_code = diagnostics_buffer.step_time_limiter_code
    step_cfl_retry_count = diagnostics_buffer.step_cfl_retry_count
    step_starting_cfl = diagnostics_buffer.step_starting_cfl
    step_pre_source_cfl = diagnostics_buffer.step_pre_source_cfl
    step_pre_projection_cfl = diagnostics_buffer.step_pre_projection_cfl
    step_post_projection_cfl = diagnostics_buffer.step_post_projection_cfl
    step_maximum_rejected_cfl = diagnostics_buffer.step_maximum_rejected_cfl
    step_momentum_advection_acceleration = (
        diagnostics_buffer.step_momentum_advection_acceleration
    )
    step_viscous_acceleration = diagnostics_buffer.step_viscous_acceleration
    step_boundary_acceleration = diagnostics_buffer.step_boundary_acceleration
    step_surface_tension_acceleration = (
        diagnostics_buffer.step_surface_tension_acceleration
    )
    step_rotating_frame_acceleration = (
        diagnostics_buffer.step_rotating_frame_acceleration
    )
    step_gravity_acceleration = diagnostics_buffer.step_gravity_acceleration
    step_pressure_projection_acceleration = (
        diagnostics_buffer.step_pressure_projection_acceleration
    )
    step_clip = diagnostics_buffer.step_clip
    step_repair = diagnostics_buffer.step_repair
    step_divergence = diagnostics_buffer.step_divergence
    step_pressure_residual = diagnostics_buffer.step_pressure_residual
    step_pressure_iterations = diagnostics_buffer.step_pressure_iterations
    cumulative_clip = diagnostics_buffer.cumulative_clip
    cumulative_repair = diagnostics_buffer.cumulative_repair
    maximum_single_clip = diagnostics_buffer.maximum_single_clip
    maximum_single_repair = diagnostics_buffer.maximum_single_repair
    maximum_divergence = diagnostics_buffer.maximum_divergence
    maximum_pressure_residual = diagnostics_buffer.maximum_pressure_residual
    maximum_cfl = diagnostics_buffer.maximum_cfl
    maximum_rejected_cfl = diagnostics_buffer.maximum_rejected_cfl
    cfl_retry_attempts_total = diagnostics_buffer.cfl_retry_attempts_total
    cfl_retried_accepted_steps = diagnostics_buffer.cfl_retried_accepted_steps
    limiter_counts = diagnostics_buffer.limiter_counts
    minimum_dt = diagnostics_buffer.minimum_dt

    final_rpm, _ = drive_rpm_and_slope(config, state.time_s)
    final_omega = rpm_to_omega(final_rpm)
    final_rot_x, final_rot_y, final_rot_z = grid.faces_to_cells(
        state.u_x_face, state.u_y_face, state.u_z_face
    )
    final_lab_x, final_lab_y, final_lab_z = rotating_to_laboratory_velocity_3d(
        final_rot_x,
        final_rot_y,
        final_rot_z,
        grid,
        final_omega,
    )
    final_topology = topology_diagnostics_3d(
        state.phase,
        grid,
        interface_thickness_m=(
            config.high_fidelity.interface_thickness_cells
            * grid.minimum_spacing_m
        ),
        minimum_resolved_throat_cells=(
            config.high_fidelity.minimum_resolved_throat_cells
        ),
    )
    final_geometry = surface_geometry_3d(
        state.phase,
        grid,
        liquid_height_m=config.container.liquid_depth_m,
        bar_top_m=(
            config.stir_bar.bottom_clearance_m
            + 2.0 * config.stir_bar.radius_m
        ),
        radial_bins=radial_bins,
    )
    observables = {
        name: np.asarray(values, dtype=np.float64)
        for name, values in observable_lists.items()
    }
    radial_centres = (
        np.arange(radial_bins, dtype=np.float64) + 0.5
    ) * grid.radius_m / radial_bins
    fields = {
        "r_m": radial_centres,
        "radius_m": radial_centres,
        "z_m": grid.z_centres,
        "frame_time_s": np.asarray(times, dtype=np.float64),
        "phase_liquid_fraction": np.stack(phase_frames),
        "u_r_m_s": np.stack(ur_frames),
        "u_z_m_s": np.stack(uz_frames),
        "u_theta_m_s": np.stack(utheta_frames),
        "pressure_projection_pa": np.stack(pressure_frames),
        "x_cell_m": grid.x_centres,
        "y_cell_m": grid.y_centres,
        "z_cell_m": grid.z_centres,
        "final_phase_liquid_fraction_3d": state.phase,
        "final_u_x_rotating_m_s_3d": final_rot_x,
        "final_u_y_rotating_m_s_3d": final_rot_y,
        "final_u_z_rotating_m_s_3d": final_rot_z,
        "final_u_x_lab_m_s_3d": final_lab_x,
        "final_u_y_lab_m_s_3d": final_lab_y,
        "final_u_z_lab_m_s_3d": final_lab_z,
        "final_pressure_gauge_pa_3d": state.pressure_pa,
        "active_fluid_mask_3d": grid.active_mask.astype(np.uint8),
        "stir_bar_mask_3d": grid.bar_mask.astype(np.uint8),
        "final_top_connected_gas_mask_3d": (
            final_topology.top_connected_gas_mask.astype(np.uint8)
        ),
        # Columns outside the cylindrical vessel are encoded as zero in
        # the archived numeric array; ``active_fluid_mask_3d`` is the
        # authoritative validity mask.  SimulationResult intentionally
        # rejects NaN payloads.
        "final_surface_column_height_m": np.nan_to_num(
            final_geometry.column_height_m, nan=0.0
        ),
        "diagnostic_step_time_s": np.asarray(step_time, dtype=np.float64),
        "diagnostic_dt_s": np.asarray(step_dt, dtype=np.float64),
        "diagnostic_initial_attempt_dt_s": np.asarray(
            step_initial_attempt_dt, dtype=np.float64
        ),
        "diagnostic_time_step_limiter_code": np.asarray(
            step_time_limiter_code, dtype=np.float64
        ),
        "diagnostic_cfl_retry_count": np.asarray(
            step_cfl_retry_count, dtype=np.float64
        ),
        "diagnostic_starting_advective_cfl": np.asarray(
            step_starting_cfl, dtype=np.float64
        ),
        "diagnostic_pre_source_advective_cfl": np.asarray(
            step_pre_source_cfl, dtype=np.float64
        ),
        "diagnostic_pre_projection_advective_cfl": np.asarray(
            step_pre_projection_cfl, dtype=np.float64
        ),
        "diagnostic_post_projection_advective_cfl": np.asarray(
            step_post_projection_cfl, dtype=np.float64
        ),
        "diagnostic_maximum_rejected_post_projection_cfl": np.asarray(
            step_maximum_rejected_cfl, dtype=np.float64
        ),
        "diagnostic_maximum_momentum_advection_acceleration_m_s2": np.asarray(
            step_momentum_advection_acceleration, dtype=np.float64
        ),
        "diagnostic_maximum_viscous_acceleration_m_s2": np.asarray(
            step_viscous_acceleration, dtype=np.float64
        ),
        "diagnostic_maximum_boundary_acceleration_m_s2": np.asarray(
            step_boundary_acceleration, dtype=np.float64
        ),
        "diagnostic_maximum_surface_tension_acceleration_m_s2": np.asarray(
            step_surface_tension_acceleration, dtype=np.float64
        ),
        "diagnostic_maximum_rotating_frame_acceleration_m_s2": np.asarray(
            step_rotating_frame_acceleration, dtype=np.float64
        ),
        "diagnostic_gravity_acceleration_m_s2": np.asarray(
            step_gravity_acceleration, dtype=np.float64
        ),
        "diagnostic_maximum_pressure_projection_acceleration_m_s2": np.asarray(
            step_pressure_projection_acceleration, dtype=np.float64
        ),
        "diagnostic_phase_clip_absolute_volume_m3": np.asarray(
            step_clip, dtype=np.float64
        ),
        "diagnostic_phase_repair_absolute_volume_m3": np.asarray(
            step_repair, dtype=np.float64
        ),
        "diagnostic_projected_divergence_s_inv": np.asarray(
            step_divergence, dtype=np.float64
        ),
        "diagnostic_pressure_relative_residual": np.asarray(
            step_pressure_residual, dtype=np.float64
        ),
        "diagnostic_pressure_iterations": np.asarray(
            step_pressure_iterations, dtype=np.float64
        ),
    }
    observable_units = {
        "surface_profile_m": "m",
        "surface_profile_minimum_m": "m",
        "surface_profile_maximum_m": "m",
        "surface_depth": "m",
        "surface_depth_m": "m",
        "funnel_depth_m": "m",
        "nominal_central_depression_m": "m",
        "surface_profile_range_m": "m",
        "surface_min_z_m": "m",
        "min_gap_to_bar": "m",
        "min_gap_to_bar_m": "m",
        "minimum_gap_m": "m",
        "actual_bar_rpm": "rpm",
        "equivalent_bar_rpm": "rpm",
        "bar_angle_rad": "rad",
        "liquid_volume_m3": "m^3",
        "liquid_volume_error_m3": "m^3",
        "liquid_volume_error": "1",
        "liquid_mass_error_relative": "1",
        "liquid_volume_balance_error_relative": "1",
        "max_divergence": "s^-1",
        "divergence_before_projection": "s^-1",
        "pressure_residual": "1",
        "pressure_iterations": "1",
        "cfl_advective": "1",
        "cfl_viscous": "1",
        "cfl_capillary": "1",
        "rotation_increment_rad": "rad",
        "kinetic_energy_j": "J",
        "kinetic_energy_lab_j": "J",
        "kinetic_energy_rotating_j": "J",
        "angular_momentum_z_kg_m2_s": "kg*m^2/s",
        "viscous_dissipation_w": "W",
        "axisymmetry_error": "1",
        "bar_gas_contact_area_m2": "m^2",
        "connection_minimum_throat_radius_m": "m",
        "connection_required_throat_radius_m": "m",
        "first_contact_proxy": "1",
        "connection_candidate": "1",
        "finite_channel_candidate": "1",
        "isolated_gas_volume_m3": "m^3",
    }
    field_units = {
        name: (
            "1"
            if "phase" in name or "mask" in name
            else "Pa"
            if "pressure" in name
            else "s"
            if name.endswith("time_s") or name.endswith("dt_s")
            else "m/s^2"
            if "acceleration_m_s2" in name
            else "m^3"
            if "volume_m3" in name
            else "s^-1"
            if "divergence" in name
            else "1"
            if (
                "residual" in name
                or "iterations" in name
                or "cfl" in name
                or "limiter_code" in name
                or "retry_count" in name
            )
            else "m/s"
            if "u_" in name
            else "m"
        )
        for name in fields
    }
    interface_width = (
        config.high_fidelity.interface_thickness_cells
        * grid.minimum_spacing_m
    )
    geometry_resolution = {
        "exact_vessel_volume_m3": grid.exact_vessel_volume_m3,
        "rasterized_vessel_volume_m3": grid.rasterized_vessel_volume_m3,
        "vessel_volume_relative_error": (
            grid.rasterized_vessel_volume_m3 / grid.exact_vessel_volume_m3 - 1.0
        ),
        "exact_bar_volume_m3": grid.exact_bar_volume_m3,
        "rasterized_bar_volume_m3": grid.rasterized_bar_volume_m3,
        "bar_volume_relative_error": (
            grid.rasterized_bar_volume_m3 / grid.exact_bar_volume_m3 - 1.0
        ),
        "bar_diameter_cells_min_spacing": (
            2.0 * config.stir_bar.radius_m / grid.minimum_spacing_m
        ),
        "interface_10_90_width_m": interface_width,
        "interface_10_90_width_cells_min_spacing": (
            interface_width / grid.minimum_spacing_m
        ),
    }
    physical_prediction_gate_pass = False
    resolved_event_times = [
        event.time_s for event in events if event.kind == "resolved_channel"
    ]
    metadata = {
        "backend_id": backend_id,
        "fidelity_label": "HF3D-local-law",
        "evidence_level": (
            "NUMERICAL-IMPLEMENTATION-ONLY until frozen refinement and experiment validation"
        ),
        "model": model_name,
        "coordinate_frame": {
            "solver": "bar-co-rotating Cartesian frame",
            "bar": "stationary horizontal x-aligned capsule",
            "container_velocity": "-Omega cross r in rotating frame",
            "reported_velocity_fields": (
                "saved r/theta/z averages are laboratory-frame; final full 3D includes both frames"
            ),
            "bar_angle": "exact integral of piecewise-linear actual signed rpm",
        },
        "boundary_conditions": {
            "side_and_floor": "impermeable voxel faces + local no-slip wall-layer relaxation",
            "bar": "impermeable capsule voxel faces + stationary no-slip wall-layer relaxation",
            "top_pressure": "atmospheric gauge p=0 Dirichlet at half-cell distance",
            "top_velocity_and_phase": (
                "open advective boundary; incoming material is pure gas stationary in laboratory frame"
            ),
            "contact_angle": {
                "side_deg": config.high_fidelity.side_contact_angle_deg,
                "floor_deg": config.high_fidelity.floor_contact_angle_deg,
                "bar_deg": config.high_fidelity.bar_contact_angle_deg,
                "law": "local geometric equilibrium angle; no forward hysteretic mobility",
            },
        },
        "observable_units": observable_units,
        "field_units": field_units,
        "observable_definitions": {
            "funnel_depth_m": "outer-annulus mean height minus central minimum height (shared shape observable)",
            "nominal_central_depression_m": "initial nominal liquid height minus central minimum; includes raster-volume offset",
            "surface_depth_m": "outer annulus azimuthal-mean height minus central minimum height",
            "axisymmetry_error": "within-annulus surface-height residual norm divided by resolved mean-deformation norm",
            "connection_candidate": "top-connected gas path whose minimum equivalent cross-section radius passes the interface/grid gate",
        },
        "capabilities": dict(capabilities),
        "limitations": list(limitations),
        "diagnostics": {
            "active_cells": grid.active_cell_count,
            "numerical_thread_policy": dict(numerical_thread_policy),
            "configured_cells_x_y_z": [grid.nx, grid.ny, grid.nz],
            "grid_spacing_m": [grid.dx, grid.dy, grid.dz],
            "geometry_resolution": geometry_resolution,
            "pressure_solver": (
                "variable-density SPD MAC projection with "
                f"{projection.preconditioner_name} PCG"
            ),
            "pressure_accelerator": pressure_accelerator,
            "pressure_solves": projection.solve_count,
            "pressure_initial_guess_extrapolations": (
                projection.initial_guess_extrapolation_count
            ),
            "pressure_last_initial_guess_extrapolation_factor": (
                projection.last_initial_guess_extrapolation_factor
            ),
            "cfl_controller": {
                "hard_limit": hf.cfl_limit,
                "target_fraction": hf.cfl_controller_target_fraction,
                "target": (
                    hf.cfl_limit * hf.cfl_controller_target_fraction
                ),
                "known_source_bound": (
                    "positive root of R*dt + A*dt^2 <= target for current/wall advection plus analytic gravity/rotating-frame bounds"
                ),
                "unknown_operator_policy": (
                    "complete-step post-projection gate with transactional retry"
                ),
                "maximum_step_retries": hf.maximum_cfl_step_retries,
            },
            "time_step_limiter_codes": dict(TIME_STEP_LIMITER_CODES),
            "time_step_limiter_counts": limiter_counts,
            "cfl_retry_attempts_total": cfl_retry_attempts_total,
            "cfl_retried_accepted_steps": cfl_retried_accepted_steps,
            "maximum_rejected_post_projection_cfl": maximum_rejected_cfl,
            "maximum_operator_acceleration_m_s2": {
                "momentum_advection": max(
                    step_momentum_advection_acceleration, default=0.0
                ),
                "viscous": max(step_viscous_acceleration, default=0.0),
                "boundary_relaxation": max(
                    step_boundary_acceleration, default=0.0
                ),
                "surface_tension": max(
                    step_surface_tension_acceleration, default=0.0
                ),
                "rotating_frame": max(
                    step_rotating_frame_acceleration, default=0.0
                ),
                "gravity": max(step_gravity_acceleration, default=0.0),
                "pressure_projection": max(
                    step_pressure_projection_acceleration, default=0.0
                ),
            },
            "completed_steps": state.step,
            "simulated_duration_s": state.time_s,
            "minimum_time_step_s": 0.0 if not math.isfinite(minimum_dt) else minimum_dt,
            "maximum_advective_cfl": maximum_cfl,
            "maximum_projected_divergence_s_inv": maximum_divergence,
            "maximum_pressure_relative_residual": maximum_pressure_residual,
            "maximum_single_step_phase_clip_relative": maximum_single_clip,
            "cumulative_phase_clip_relative": cumulative_clip / initial_volume,
            "maximum_single_step_phase_repair_relative": maximum_single_repair,
            "cumulative_phase_repair_relative": cumulative_repair / initial_volume,
            "liquid_open_boundary_balance_error_relative": (
                (grid.integrate(state.phase) - expected_volume) / initial_volume
            ),
            "numerical_quality_gate": {
                "pass": True,
                "kind": "configured fail-closed implementation gate",
                "physical_prediction_gate_pass": physical_prediction_gate_pass,
                "physical_prediction_status": "NO-GO_UNCALIBRATED_UNCONVERGED",
                "reason": (
                    "This run alone has no frozen multi-grid/time convergence or sealed experimental validation."
                ),
            },
            "final_topology": {
                "touches_bar": final_topology.touches_bar,
                "resolved_channel": final_topology.resolved_channel,
                "minimum_throat_radius_m": final_topology.minimum_throat_radius_m,
                "required_resolved_radius_m": final_topology.required_resolved_radius_m,
            },
            "post_contact_continued": bool(
                resolved_event_times
                and state.time_s > resolved_event_times[0] + time_tolerance
            ),
            "termination_reason": (
                "soft_stop"
                if status is RunStatus.STOPPED
                else "configured_duration_reached"
            ),
        },
    }
    return SimulationResult(
        backend=BackendMode.HIGH_FIDELITY,
        time_s=np.asarray(times, dtype=np.float64),
        observables=observables,
        fields=fields,
        events=tuple(events),
        metadata=metadata,
        status=status,
    )
__all__ = ["FrameRecorder", "StepDiagnosticsBuffer", "assemble_result"]
