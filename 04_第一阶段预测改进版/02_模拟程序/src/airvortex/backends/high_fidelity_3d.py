"""Independent three-dimensional local-law high-fidelity solver.

This backend advances the variable-density incompressible one-fluid equations
for water and air on a masked Cartesian MAC grid.  A real horizontal capsule
bar is fixed in its own co-rotating frame; the cylindrical vessel then has the
exact opposite rigid-wall velocity and Coriolis, centrifugal and Euler terms
are retained.  No equation, state variable or closure from the reduced F1.0
forward theory is imported here.

The implementation is a research-grade finite-volume reference, not a claim
of production DNS.  Its numerical and physical validity gates are carried in
every result so a coarse preview cannot silently become an apparatus-level
prediction.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from types import MappingProxyType
from typing import Callable

import numpy as np
from numpy.typing import NDArray

from airvortex._hardware import NUMERICAL_THREAD_POLICY
from airvortex.core import (
    BackendMode,
    Control,
    Event,
    Progress,
    RunStatus,
    SimulationConfig,
    SimulationResult,
)
from airvortex.numerics.cartesian3d import (
    BAR_WALL,
    FLOOR_WALL,
    SIDE_WALL,
    FloatArray,
    ImmersedCartesianGrid,
    advect_bounded_scalar,
    advective_cfl,
)
from airvortex.numerics.interface3d import (
    Topology3DDiagnostics,
    azimuthal_average_3d,
    conservative_cls_3d,
    initialise_flat_phase_3d,
    mixture_property_3d,
    surface_geometry_3d,
    surface_tension_acceleration_3d,
    topology_diagnostics_3d,
)
from airvortex.numerics.momentum3d import (
    advect_momentum_3d,
    laboratory_cylindrical_velocity_3d,
    newtonian_stress_acceleration_3d,
    relax_no_slip_boundaries_3d,
    rotating_frame_acceleration_3d,
    rotating_to_laboratory_velocity_3d,
)
from airvortex.numerics.projection3d import VariableDensityProjection3D
from airvortex.numerics.projection3d_gpu import (
    CuPyVariableDensityProjection3D,
    GPUProjectionUnavailable,
    probe_gpu_projection,
)


ProgressCallback = Callable[[Progress], None]
PressureProjection3D = VariableDensityProjection3D | CuPyVariableDensityProjection3D
_LIVE_SURFACE_PREVIEW_INTERVAL_WALL_S = 1.0


MODEL_NAME = (
    "HF3D variable-density incompressible one-fluid Navier-Stokes + "
    "conservative colour interface + CSF + embedded capsule in a bar-co-rotating frame"
)


CAPABILITIES = MappingProxyType(
    {
        "fidelity_label": "HF3D-local-law",
        "evidence_level": (
            "independent numerical reference; quantitative only after frozen "
            "grid/time convergence and sealed experiment validation"
        ),
        "model": MODEL_NAME,
        "three_dimensional": True,
        "variable_density_two_phase_one_fluid": True,
        "local_newtonian_stress": True,
        "conservative_phase_transport": True,
        "variable_coefficient_pressure_projection": True,
        "open_atmospheric_pressure_top": True,
        "actual_horizontal_capsule_bar": True,
        "bar_tip_wake_representable_if_resolved": True,
        "rotating_frame_coriolis_centrifugal_euler": True,
        "geometric_equilibrium_contact_angle": True,
        "post_contact_continuation": True,
        "breakup_and_reconnection_representable_if_resolved": True,
        "azimuthal_cells_used": True,
        "imports_forward_theory_closures": False,
        "uses_forward_burgers_field": False,
        "uses_forward_interface_mobility": False,
        "uses_forward_effective_target": False,
        "quantitative_absolute_critical_rpm": False,
        "quantitative_post_contact_air_channel": False,
    }
)


LIMITATIONS = (
    "The cylindrical vessel and capsule are cell-centre voxel geometries; cut-cell "
    "fractions and body-fitted boundary layers are not implemented.",
    "The interface is a conservative diffuse colour field with projected "
    "colour-gradient CSF, not geometric PLIC VOF; parasitic currents and curvature "
    "error require frozen static-drop and refinement tests.",
    "No-slip is an explicitly reported immersed wall-layer relaxation. Its layer "
    "thickness and relaxation time are numerical controls and require refinement.",
    "Contact angle is a local equilibrium geometric condition. Dynamic contact-angle "
    "hysteresis, microscopic slip and contamination are not resolved.",
    "The prescribed bar trajectory assumes measured actual bar rpm; magnetic torque, "
    "motor dynamics, asynchronous slip and bar wobble are outside this backend.",
    "A resolution-gated gas path is a numerical topology observation, not an "
    "experimentally validated stable-channel or critical-rpm prediction.",
    "Only saved azimuthal averages and the final full 3D state are archived by the "
    "backend; this bounds memory but does not preserve every transient 3D voxel field.",
)


@dataclass(slots=True)
class HF3DState:
    time_s: float
    step: int
    bar_angle_rad: float
    u_x_face: FloatArray
    u_y_face: FloatArray
    u_z_face: FloatArray
    phase: FloatArray
    pressure_pa: FloatArray


@dataclass(frozen=True, slots=True)
class HF3DStepDiagnostics:
    dt_s: float
    actual_bar_rpm: float
    angular_velocity_rad_s: float
    angular_acceleration_rad_s2: float
    advective_cfl: float
    viscous_cfl: float
    capillary_cfl: float
    rotation_increment_rad: float
    divergence_before_s_inv: float
    divergence_after_s_inv: float
    pressure_relative_residual: float
    pressure_iterations: int
    liquid_volume_m3: float
    expected_liquid_volume_m3: float
    liquid_volume_balance_error_m3: float
    phase_top_outward_flux_m3_s: float
    phase_clip_absolute_volume_m3: float
    phase_repair_absolute_volume_m3: float
    kinetic_energy_lab_j: float
    kinetic_energy_rotating_j: float
    angular_momentum_z_kg_m2_s: float
    viscous_dissipation_w: float
    maximum_surface_tension_acceleration_m_s2: float
    maximum_viscous_acceleration_m_s2: float
    maximum_boundary_acceleration_m_s2: float
    boundary_rotating_energy_change_j: float
    topology: Topology3DDiagnostics
    phase_conservation_residual_m3: float = 0.0
    density_conservation_residual_kg: float = 0.0
    momentum_remap_x_kg_m_s: float = 0.0
    momentum_remap_y_kg_m_s: float = 0.0
    momentum_remap_z_kg_m_s: float = 0.0
    momentum_remap_absolute_kg_m_s: float = 0.0
    cls_phase_conservation_residual_m3: float = 0.0
    cls_density_conservation_residual_kg: float = 0.0
    cls_momentum_remap_x_kg_m_s: float = 0.0
    cls_momentum_remap_y_kg_m_s: float = 0.0
    cls_momentum_remap_z_kg_m_s: float = 0.0
    cls_momentum_remap_absolute_kg_m_s: float = 0.0


def _rpm_to_omega(rpm: float) -> float:
    return 2.0 * math.pi * rpm / 60.0


def _drive_rpm_and_slope(
    config: SimulationConfig, time_s: float
) -> tuple[float, float]:
    """Piecewise-linear actual signed bar rpm and its right-hand slope."""

    drive = config.drive
    t = float(np.clip(time_s, 0.0, config.run.duration_s))
    if drive.actual_speed_schedule_time_s:
        times = np.asarray(drive.actual_speed_schedule_time_s, dtype=np.float64)
        speeds = np.asarray(drive.actual_speed_schedule_rpm, dtype=np.float64)
        if t >= times[-1]:
            return float(speeds[-1]), 0.0
        index = int(np.searchsorted(times, t, side="right") - 1)
        index = max(0, min(index, times.size - 2))
        duration = times[index + 1] - times[index]
        slope = (speeds[index + 1] - speeds[index]) / duration
        return float(speeds[index] + slope * (t - times[index])), float(slope)
    direction = float(drive.rotation_direction)
    if drive.ramp_duration_s > 0.0 and t < drive.ramp_duration_s:
        slope = direction * (
            drive.target_rpm - drive.initial_rpm
        ) / drive.ramp_duration_s
        return float(direction * drive.initial_rpm + slope * t), float(slope)
    return float(direction * drive.target_rpm), 0.0


def _integrated_bar_angle_rad(config: SimulationConfig, time_s: float) -> float:
    """Exact integral of the configured piecewise-linear actual bar speed."""

    drive = config.drive
    t = max(0.0, float(time_s))
    rpm_seconds = 0.0
    if drive.actual_speed_schedule_time_s:
        times = drive.actual_speed_schedule_time_s
        speeds = drive.actual_speed_schedule_rpm
        for left, right, rpm_left, rpm_right in zip(
            times[:-1], times[1:], speeds[:-1], speeds[1:]
        ):
            if t <= left:
                break
            width = min(t, right) - left
            if width <= 0.0:
                continue
            slope = (rpm_right - rpm_left) / (right - left)
            rpm_seconds += rpm_left * width + 0.5 * slope * width * width
            if t <= right:
                break
        if t > times[-1]:
            rpm_seconds += speeds[-1] * (t - times[-1])
    else:
        signed_initial = drive.rotation_direction * drive.initial_rpm
        signed_target = drive.rotation_direction * drive.target_rpm
        ramp = drive.ramp_duration_s
        if ramp > 0.0:
            width = min(t, ramp)
            slope = (signed_target - signed_initial) / ramp
            rpm_seconds += signed_initial * width + 0.5 * slope * width * width
            if t > ramp:
                rpm_seconds += signed_target * (t - ramp)
        else:
            rpm_seconds = signed_target * t
    return rpm_seconds * (2.0 * math.pi / 60.0)


def _make_pressure_projection(
    config: SimulationConfig,
    grid: ImmersedCartesianGrid,
) -> tuple[PressureProjection3D, dict[str, object]]:
    """Select a reproducible pressure engine without changing its operator.

    ``auto`` falls back only during availability/construction probing.  Once a
    run has started on CUDA, a device failure remains fail-closed instead of
    silently switching floating-point reduction order partway through a run.
    """

    requested = config.high_fidelity.pressure_solver_device
    common = {
        "relative_tolerance": config.high_fidelity.pressure_relative_tolerance,
        "maximum_iterations": config.high_fidelity.pressure_max_iterations,
    }
    if requested == "cpu":
        return VariableDensityProjection3D(grid, **common), {
            "requested": requested,
            "selected": "cpu",
            "fallback": False,
            "detail": "CPU reference projection selected explicitly",
            "device_name": None,
            "compute_capability": None,
            "host_device_transfer_each_solve": False,
        }

    probe = probe_gpu_projection()
    if probe.available:
        try:
            projection = CuPyVariableDensityProjection3D(grid, **common)
        except (GPUProjectionUnavailable, RuntimeError, OSError) as exc:
            if requested == "cuda":
                raise RuntimeError(
                    "HF3D CUDA pressure solver was requested but construction failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            detail = (
                "CUDA probe passed but construction failed; using CPU: "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            return projection, {
                "requested": requested,
                "selected": "cuda",
                "fallback": False,
                "detail": probe.detail,
                "device_name": probe.device_name,
                "compute_capability": probe.compute_capability,
                "total_memory_bytes": probe.total_memory_bytes,
                # The other HF3D laws remain on the CPU in this lossless first
                # stage, so four velocity/pressure fields cross the bus here.
                "host_device_transfer_each_solve": True,
            }
    else:
        if requested == "cuda":
            raise RuntimeError(
                "HF3D CUDA pressure solver was requested but is unavailable: "
                f"{probe.detail}"
            )
        detail = f"CUDA unavailable; using CPU: {probe.detail}"

    return VariableDensityProjection3D(grid, **common), {
        "requested": requested,
        "selected": "cpu",
        "fallback": True,
        "detail": detail,
        "device_name": probe.device_name,
        "compute_capability": probe.compute_capability,
        "total_memory_bytes": probe.total_memory_bytes,
        "host_device_transfer_each_solve": False,
    }


class HighFidelityBackend:
    """HF3D variable-density, open-atmosphere, real-capsule backend."""

    mode = BackendMode.HIGH_FIDELITY
    backend_id = "high_fidelity_3d_local_law"
    display_name = "HF3D：三维变密度局部守恒律"
    capabilities = CAPABILITIES

    @staticmethod
    def _validate_config(config: SimulationConfig) -> None:
        if not isinstance(config, SimulationConfig):
            raise TypeError("config must be a SimulationConfig")
        if config.backend is not BackendMode.HIGH_FIDELITY:
            raise ValueError("HF3D backend requires backend='high_fidelity'")

    def make_grid(self, config: SimulationConfig) -> ImmersedCartesianGrid:
        self._validate_config(config)
        return ImmersedCartesianGrid(
            config.high_fidelity.radial_cells,
            config.high_fidelity.azimuthal_cells,
            config.high_fidelity.axial_cells,
            config.container.radius_m,
            config.container.liquid_depth_m
            + config.container.headspace_height_m,
            bar_half_length_m=config.stir_bar.half_length_m,
            bar_radius_m=config.stir_bar.radius_m,
            bar_centre_z_m=(
                config.stir_bar.bottom_clearance_m + config.stir_bar.radius_m
            ),
        )

    @staticmethod
    def _initial_liquid_volume(
        config: SimulationConfig, grid: ImmersedCartesianGrid
    ) -> float:
        return (
            math.pi
            * config.container.radius_m**2
            * config.container.liquid_depth_m
            - grid.exact_bar_volume_m3
        )

    def initialise_state(
        self,
        config: SimulationConfig,
        grid: ImmersedCartesianGrid | None = None,
    ) -> HF3DState:
        self._validate_config(config)
        g = self.make_grid(config) if grid is None else grid
        interface_width = (
            config.high_fidelity.interface_thickness_cells
            * g.minimum_spacing_m
        )
        phase = initialise_flat_phase_3d(
            g,
            config.container.liquid_depth_m,
            interface_width,
            target_liquid_volume_m3=self._initial_liquid_volume(config, g),
        )
        rpm, _ = _drive_rpm_and_slope(config, 0.0)
        omega = _rpm_to_omega(rpm)
        # A laboratory-quiescent initial fluid has -Omega x r velocity in the
        # bar frame.  The pressure projection in run() reconciles this field
        # with impermeable voxel faces if the schedule starts at nonzero rpm.
        ux_cell = np.where(g.active_mask, omega * g.y_cell, 0.0)
        uy_cell = np.where(g.active_mask, -omega * g.x_cell, 0.0)
        uz_cell = np.zeros(g.shape, dtype=np.float64)
        ux_face, uy_face, uz_face = g.cells_to_faces(ux_cell, uy_cell, uz_cell)
        return HF3DState(
            time_s=0.0,
            step=0,
            bar_angle_rad=0.0,
            u_x_face=ux_face,
            u_y_face=uy_face,
            u_z_face=uz_face,
            phase=phase,
            pressure_pa=np.zeros(g.shape, dtype=np.float64),
        )

    @staticmethod
    def _time_step_limits(
        state: HF3DState,
        config: SimulationConfig,
        grid: ImmersedCartesianGrid,
    ) -> tuple[float, dict[str, float]]:
        hf = config.high_fidelity
        fluid = config.fluid
        ux, uy, uz = grid.faces_to_cells(
            state.u_x_face, state.u_y_face, state.u_z_face
        )
        rate = np.abs(ux) / grid.dx + np.abs(uy) / grid.dy + np.abs(uz) / grid.dz
        advective_rate = float(np.max(rate[grid.active_mask]))
        rpm_now, slope_now = _drive_rpm_and_slope(config, state.time_s)
        rpm_trial, _ = _drive_rpm_and_slope(
            config, state.time_s + hf.time_step_s
        )
        omega_bound = max(abs(_rpm_to_omega(rpm_now)), abs(_rpm_to_omega(rpm_trial)))
        omega_dot = abs(_rpm_to_omega(slope_now))
        wall_rate = omega_bound * grid.radius_m * (
            1.0 / grid.dx + 1.0 / grid.dy
        )
        rate_bound = max(advective_rate, wall_rate)
        dt_advective = (
            math.inf if rate_bound <= np.finfo(float).tiny else hf.cfl_limit / rate_bound
        )
        nu_max = max(
            fluid.liquid_dynamic_viscosity_pa_s / fluid.liquid_density_kg_m3,
            fluid.gas_dynamic_viscosity_pa_s / fluid.gas_density_kg_m3,
        )
        diffusion_rate = 2.0 * nu_max * (
            1.0 / grid.dx**2 + 1.0 / grid.dy**2 + 1.0 / grid.dz**2
        )
        dt_viscous = 0.45 / diffusion_rate
        h = grid.minimum_spacing_m
        dt_capillary = 0.20 * math.sqrt(
            fluid.gas_density_kg_m3
            * h**3
            / (math.pi * fluid.surface_tension_n_m)
        )
        speed_max = float(
            np.max(np.sqrt(ux * ux + uy * uy + uz * uz)[grid.active_mask])
        )
        source_acceleration = (
            fluid.gravity_m_s2
            + omega_bound * omega_bound * grid.radius_m
            + omega_dot * grid.radius_m
            + 2.0 * omega_bound * speed_max
        )
        dt_source = math.sqrt(
            hf.cfl_limit * h / max(source_acceleration, np.finfo(float).tiny)
        )
        dt_rotation = (
            math.inf
            if omega_bound <= np.finfo(float).tiny
            else hf.maximum_rotation_increment_rad / omega_bound
        )
        stable = min(
            hf.time_step_s,
            dt_advective,
            dt_viscous,
            dt_capillary,
            dt_source,
            dt_rotation,
        )
        return stable, {
            "advective_rate_s_inv": rate_bound,
            "viscous_rate_s_inv": diffusion_rate,
            "capillary_dt_s": dt_capillary,
            "source_dt_s": dt_source,
            "rotation_dt_s": dt_rotation,
        }

    def advance_one_step(
        self,
        state: HF3DState,
        config: SimulationConfig,
        grid: ImmersedCartesianGrid,
        projection: PressureProjection3D,
        dt_s: float,
        *,
        expected_liquid_volume_m3: float,
    ) -> HF3DStepDiagnostics:
        """Advance one conservative local-law step and mutate ``state``."""

        hf = config.high_fidelity
        fluid = config.fluid
        if not math.isfinite(dt_s) or dt_s <= 0.0:
            raise ValueError("dt_s must be finite and positive")
        old_phase = state.phase
        old_density = mixture_property_3d(
            old_phase,
            fluid.liquid_density_kg_m3,
            fluid.gas_density_kg_m3,
        )
        old_ux, old_uy, old_uz = grid.faces_to_cells(
            state.u_x_face, state.u_y_face, state.u_z_face
        )

        updated_phase, phase_transport = advect_bounded_scalar(
            old_phase,
            state.u_x_face,
            state.u_y_face,
            state.u_z_face,
            grid,
            dt_s,
            top_inflow_value=0.0,
        )
        clip_absolute = phase_transport.clip_absolute_integral
        repair_absolute = phase_transport.repair_absolute_integral
        next_step = state.step + 1
        if (
            hf.interface_compression_interval > 0
            and next_step % hf.interface_compression_interval == 0
            and hf.interface_compression_pseudo_cfl > 0.0
        ):
            interface_width = hf.interface_thickness_cells * grid.minimum_spacing_m
            updated_phase, cls_diagnostics = conservative_cls_3d(
                updated_phase,
                grid,
                interface_width,
                pseudo_cfl=hf.interface_compression_pseudo_cfl,
                iterations=1,
            )
            clip_absolute += cls_diagnostics.clip_absolute_volume_m3
            repair_absolute += cls_diagnostics.repair_absolute_volume_m3

        new_density = mixture_property_3d(
            updated_phase,
            fluid.liquid_density_kg_m3,
            fluid.gas_density_kg_m3,
        )
        new_viscosity = mixture_property_3d(
            updated_phase,
            fluid.liquid_dynamic_viscosity_pa_s,
            fluid.gas_dynamic_viscosity_pa_s,
        )
        midpoint = state.time_s + 0.5 * dt_s
        rpm_mid, rpm_slope = _drive_rpm_and_slope(config, midpoint)
        omega_mid = _rpm_to_omega(rpm_mid)
        omega_dot = _rpm_to_omega(rpm_slope)
        top_x = omega_mid * grid.y_cell[:, :, -1]
        top_y = -omega_mid * grid.x_cell[:, :, -1]
        ux, uy, uz, _ = advect_momentum_3d(
            old_density,
            old_ux,
            old_uy,
            old_uz,
            state.u_x_face,
            state.u_y_face,
            state.u_z_face,
            new_density,
            grid,
            dt_s,
            top_inflow_density_kg_m3=fluid.gas_density_kg_m3,
            top_inflow_u_x_m_s=top_x,
            top_inflow_u_y_m_s=top_y,
            top_inflow_u_z_m_s=0.0,
        )

        viscous_x, viscous_y, viscous_z, viscous_diagnostics = (
            newtonian_stress_acceleration_3d(
                ux, uy, uz, new_viscosity, new_density, grid
            )
        )
        csf_x, csf_y, csf_z, _, csf_diagnostics = (
            surface_tension_acceleration_3d(
                updated_phase,
                new_density,
                grid,
                fluid.surface_tension_n_m,
                contact_angles_deg={
                    SIDE_WALL: hf.side_contact_angle_deg,
                    FLOOR_WALL: hf.floor_contact_angle_deg,
                    BAR_WALL: hf.bar_contact_angle_deg,
                },
                smoothing_cells=hf.surface_tension_smoothing_cells,
            )
        )
        # Enforce the immersed no-slip law on transported/viscous momentum
        # before adding conservative body-force increments.  Relaxing the
        # gravity or capillary impulse itself would create a spurious curl in
        # an otherwise hydrostatic state near the voxel wall.
        ux = ux + dt_s * viscous_x
        uy = uy + dt_s * viscous_y
        uz = uz + dt_s * viscous_z
        rpm_end, _ = _drive_rpm_and_slope(config, state.time_s + dt_s)
        omega_end = _rpm_to_omega(rpm_end)
        ux, uy, uz, boundary_diagnostics = relax_no_slip_boundaries_3d(
            ux,
            uy,
            uz,
            new_density,
            grid,
            omega_end,
            dt_s,
            relaxation_time_s=hf.boundary_relaxation_time_s,
            layer_cells=hf.boundary_layer_cells,
        )
        frame_x, frame_y, frame_z = rotating_frame_acceleration_3d(
            ux, uy, grid, omega_mid, omega_dot
        )
        ux = ux + dt_s * (csf_x + frame_x)
        uy = uy + dt_s * (csf_y + frame_y)
        uz = uz + dt_s * (csf_z + frame_z - fluid.gravity_m_s2)
        ux_face, uy_face, uz_face = grid.cells_to_faces(ux, uy, uz)
        ux_face, uy_face, uz_face, pressure, projection_diagnostics = (
            projection.project(
                ux_face,
                uy_face,
                uz_face,
                new_density,
                dt_s,
                initial_pressure_pa=state.pressure_pa,
            )
        )
        projected_ux, projected_uy, projected_uz = grid.faces_to_cells(
            ux_face, uy_face, uz_face
        )
        post_cfl = advective_cfl(
            ux_face, uy_face, uz_face, grid, dt_s
        )
        if post_cfl > hf.cfl_limit * (1.0 + 1.0e-9):
            raise FloatingPointError(
                f"HF3D post-source advective CFL {post_cfl:.3e} exceeds "
                f"configured limit {hf.cfl_limit:.3e}; reduce time_step_s"
            )
        if (
            projection_diagnostics.pressure_relative_residual
            > hf.pressure_relative_tolerance * 1.05
        ):
            raise FloatingPointError(
                "HF3D pressure residual exceeds configured tolerance"
            )
        arrays = (
            updated_phase,
            projected_ux,
            projected_uy,
            projected_uz,
            pressure,
        )
        if not all(np.isfinite(array).all() for array in arrays):
            raise FloatingPointError("HF3D produced a non-finite field")

        liquid_volume = grid.integrate(updated_phase)
        expected_after = expected_liquid_volume_m3 - (
            dt_s * phase_transport.prescribed_boundary_flux_m3_s
        )
        lab_x, lab_y, lab_z = rotating_to_laboratory_velocity_3d(
            projected_ux,
            projected_uy,
            projected_uz,
            grid,
            omega_end,
        )
        kinetic_lab = 0.5 * grid.integrate(
            new_density * (lab_x * lab_x + lab_y * lab_y + lab_z * lab_z)
        )
        kinetic_rotating = 0.5 * grid.integrate(
            new_density
            * (
                projected_ux * projected_ux
                + projected_uy * projected_uy
                + projected_uz * projected_uz
            )
        )
        angular_momentum = grid.integrate(
            new_density * (grid.x_cell * lab_y - grid.y_cell * lab_x)
        )
        interface_width = hf.interface_thickness_cells * grid.minimum_spacing_m
        topology = topology_diagnostics_3d(
            updated_phase,
            grid,
            interface_thickness_m=interface_width,
            minimum_resolved_throat_cells=hf.minimum_resolved_throat_cells,
        )

        state.time_s += dt_s
        state.step = next_step
        state.bar_angle_rad = _integrated_bar_angle_rad(config, state.time_s)
        state.u_x_face = ux_face
        state.u_y_face = uy_face
        state.u_z_face = uz_face
        state.phase = updated_phase
        state.pressure_pa = pressure

        nu_max = max(
            fluid.liquid_dynamic_viscosity_pa_s / fluid.liquid_density_kg_m3,
            fluid.gas_dynamic_viscosity_pa_s / fluid.gas_density_kg_m3,
        )
        viscous_cfl = 2.0 * nu_max * dt_s * (
            1.0 / grid.dx**2 + 1.0 / grid.dy**2 + 1.0 / grid.dz**2
        )
        capillary_dt = 0.20 * math.sqrt(
            fluid.gas_density_kg_m3
            * grid.minimum_spacing_m**3
            / (math.pi * fluid.surface_tension_n_m)
        )
        return HF3DStepDiagnostics(
            dt_s=dt_s,
            actual_bar_rpm=rpm_end,
            angular_velocity_rad_s=omega_end,
            angular_acceleration_rad_s2=omega_dot,
            advective_cfl=post_cfl,
            viscous_cfl=viscous_cfl,
            capillary_cfl=dt_s / capillary_dt,
            rotation_increment_rad=abs(omega_mid) * dt_s,
            divergence_before_s_inv=(
                projection_diagnostics.divergence_before_s_inv
            ),
            divergence_after_s_inv=projection_diagnostics.divergence_after_s_inv,
            pressure_relative_residual=(
                projection_diagnostics.pressure_relative_residual
            ),
            pressure_iterations=projection_diagnostics.iterations,
            liquid_volume_m3=liquid_volume,
            expected_liquid_volume_m3=expected_after,
            liquid_volume_balance_error_m3=liquid_volume - expected_after,
            phase_top_outward_flux_m3_s=(
                phase_transport.prescribed_boundary_flux_m3_s
            ),
            phase_clip_absolute_volume_m3=clip_absolute,
            phase_repair_absolute_volume_m3=repair_absolute,
            kinetic_energy_lab_j=kinetic_lab,
            kinetic_energy_rotating_j=kinetic_rotating,
            angular_momentum_z_kg_m2_s=angular_momentum,
            viscous_dissipation_w=viscous_diagnostics.viscous_dissipation_w,
            maximum_surface_tension_acceleration_m_s2=(
                csf_diagnostics.maximum_acceleration_m_s2
            ),
            maximum_viscous_acceleration_m_s2=(
                viscous_diagnostics.maximum_viscous_acceleration_m_s2
            ),
            maximum_boundary_acceleration_m_s2=(
                boundary_diagnostics.maximum_relaxation_acceleration_m_s2
            ),
            boundary_rotating_energy_change_j=(
                boundary_diagnostics.rotating_frame_kinetic_energy_change_j
            ),
            topology=topology,
        )

    @staticmethod
    def _zero_step_diagnostics(
        state: HF3DState,
        config: SimulationConfig,
        grid: ImmersedCartesianGrid,
        expected_liquid_volume_m3: float,
    ) -> HF3DStepDiagnostics:
        rpm, slope = _drive_rpm_and_slope(config, state.time_s)
        omega = _rpm_to_omega(rpm)
        density = mixture_property_3d(
            state.phase,
            config.fluid.liquid_density_kg_m3,
            config.fluid.gas_density_kg_m3,
        )
        ux, uy, uz = grid.faces_to_cells(
            state.u_x_face, state.u_y_face, state.u_z_face
        )
        lab_x, lab_y, lab_z = rotating_to_laboratory_velocity_3d(
            ux, uy, uz, grid, omega
        )
        topology = topology_diagnostics_3d(
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
        volume = grid.integrate(state.phase)
        return HF3DStepDiagnostics(
            dt_s=0.0,
            actual_bar_rpm=rpm,
            angular_velocity_rad_s=omega,
            angular_acceleration_rad_s2=_rpm_to_omega(slope),
            advective_cfl=0.0,
            viscous_cfl=0.0,
            capillary_cfl=0.0,
            rotation_increment_rad=0.0,
            divergence_before_s_inv=0.0,
            divergence_after_s_inv=float(
                np.max(
                    np.abs(
                        grid.divergence(
                            state.u_x_face, state.u_y_face, state.u_z_face
                        )[grid.active_mask]
                    )
                )
            ),
            pressure_relative_residual=0.0,
            pressure_iterations=0,
            liquid_volume_m3=volume,
            expected_liquid_volume_m3=expected_liquid_volume_m3,
            liquid_volume_balance_error_m3=volume - expected_liquid_volume_m3,
            phase_top_outward_flux_m3_s=0.0,
            phase_clip_absolute_volume_m3=0.0,
            phase_repair_absolute_volume_m3=0.0,
            kinetic_energy_lab_j=0.5
            * grid.integrate(
                density * (lab_x * lab_x + lab_y * lab_y + lab_z * lab_z)
            ),
            kinetic_energy_rotating_j=0.5
            * grid.integrate(density * (ux * ux + uy * uy + uz * uz)),
            angular_momentum_z_kg_m2_s=grid.integrate(
                density * (grid.x_cell * lab_y - grid.y_cell * lab_x)
            ),
            viscous_dissipation_w=0.0,
            maximum_surface_tension_acceleration_m_s2=0.0,
            maximum_viscous_acceleration_m_s2=0.0,
            maximum_boundary_acceleration_m_s2=0.0,
            boundary_rotating_energy_change_j=0.0,
            topology=topology,
        )

    def run(
        self,
        config: SimulationConfig,
        control: Control | None = None,
        progress: ProgressCallback | None = None,
    ) -> SimulationResult:
        self._validate_config(config)
        if control is not None and not isinstance(control, Control):
            raise TypeError("control must be Control or None")
        if progress is not None and not callable(progress):
            raise TypeError("progress must be callable or None")
        run_control = Control() if control is None else control
        grid = self.make_grid(config)
        projection, pressure_accelerator = _make_pressure_projection(config, grid)
        state = self.initialise_state(config, grid)
        initial_volume = self._initial_liquid_volume(config, grid)
        expected_volume = initial_volume
        # Reconcile a nonzero-speed laboratory-quiescent initial field with the
        # impermeable voxel boundary.  At the usual zero-rpm start this is a
        # no-op and incurs no pressure solve.
        initial_density = mixture_property_3d(
            state.phase,
            config.fluid.liquid_density_kg_m3,
            config.fluid.gas_density_kg_m3,
        )
        if np.max(
            np.abs(
                grid.divergence(
                    state.u_x_face, state.u_y_face, state.u_z_face
                )[grid.active_mask]
            )
        ) > 1.0e-14:
            (
                state.u_x_face,
                state.u_y_face,
                state.u_z_face,
                state.pressure_pa,
                _,
            ) = projection.project(
                state.u_x_face,
                state.u_y_face,
                state.u_z_face,
                initial_density,
                1.0,
            )

        times: list[float] = []
        observable_lists: dict[str, list[float | FloatArray]] = {}
        phase_frames: list[FloatArray] = []
        ur_frames: list[FloatArray] = []
        uz_frames: list[FloatArray] = []
        utheta_frames: list[FloatArray] = []
        pressure_frames: list[FloatArray] = []
        events: list[Event] = []
        step_time: list[float] = []
        step_dt: list[float] = []
        step_clip: list[float] = []
        step_repair: list[float] = []
        step_divergence: list[float] = []
        step_pressure_residual: list[float] = []
        step_pressure_iterations: list[float] = []
        step_phase_conservation: list[float] = []
        step_density_conservation: list[float] = []
        step_momentum_remap_x: list[float] = []
        step_momentum_remap_y: list[float] = []
        step_momentum_remap_z: list[float] = []
        step_momentum_remap_absolute: list[float] = []
        step_cls_phase_conservation: list[float] = []
        step_cls_density_conservation: list[float] = []
        step_cls_momentum_remap_x: list[float] = []
        step_cls_momentum_remap_y: list[float] = []
        step_cls_momentum_remap_z: list[float] = []
        step_cls_momentum_remap_absolute: list[float] = []
        cumulative_clip = 0.0
        cumulative_repair = 0.0
        maximum_single_clip = 0.0
        maximum_single_repair = 0.0
        maximum_divergence = 0.0
        maximum_pressure_residual = 0.0
        maximum_cfl = 0.0
        minimum_dt = math.inf
        previous_topology: Topology3DDiagnostics | None = None
        first_resolved_seen = False
        resolved_start_time: float | None = None
        sustained_reported = False
        last_diag = self._zero_step_diagnostics(
            state, config, grid, expected_volume
        )

        radial_bins = max(4, min(grid.nx, grid.ny) // 2)

        def append_observable(name: str, value: float | FloatArray) -> None:
            observable_lists.setdefault(name, []).append(value)

        def record_frame(
            diagnostics: HF3DStepDiagnostics,
        ) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
            rpm, _ = _drive_rpm_and_slope(config, state.time_s)
            omega = _rpm_to_omega(rpm)
            ux, uy, uz = grid.faces_to_cells(
                state.u_x_face, state.u_y_face, state.u_z_face
            )
            ur, utheta, uz_lab = laboratory_cylindrical_velocity_3d(
                ux, uy, uz, grid, omega
            )
            radial, phase_average = azimuthal_average_3d(
                state.phase, grid, radial_bins
            )
            _, ur_average = azimuthal_average_3d(ur, grid, radial_bins)
            _, uz_average = azimuthal_average_3d(uz_lab, grid, radial_bins)
            _, utheta_average = azimuthal_average_3d(
                utheta, grid, radial_bins
            )
            _, pressure_average = azimuthal_average_3d(
                state.pressure_pa, grid, radial_bins
            )
            geometry = surface_geometry_3d(
                state.phase,
                grid,
                liquid_height_m=config.container.liquid_depth_m,
                bar_top_m=(
                    config.stir_bar.bottom_clearance_m
                    + 2.0 * config.stir_bar.radius_m
                ),
                radial_bins=radial_bins,
            )
            times.append(state.time_s)
            phase_frames.append(phase_average)
            ur_frames.append(ur_average)
            uz_frames.append(uz_average)
            utheta_frames.append(utheta_average)
            pressure_frames.append(pressure_average)
            append_observable("surface_profile_m", geometry.mean_profile_m)
            append_observable("surface_profile_minimum_m", geometry.minimum_profile_m)
            append_observable("surface_profile_maximum_m", geometry.maximum_profile_m)
            append_observable("surface_depth", geometry.visible_depth_m)
            append_observable("surface_depth_m", geometry.visible_depth_m)
            append_observable("funnel_depth_m", geometry.funnel_depth_m)
            append_observable(
                "nominal_central_depression_m",
                geometry.nominal_central_depression_m,
            )
            append_observable("surface_profile_range_m", geometry.profile_range_m)
            append_observable(
                "surface_min_z_m", float(np.nanmin(geometry.column_height_m))
            )
            append_observable("min_gap_to_bar", geometry.minimum_gap_to_bar_m)
            append_observable("min_gap_to_bar_m", geometry.minimum_gap_to_bar_m)
            append_observable("minimum_gap_m", diagnostics.topology.minimum_gap_to_bar_m)
            append_observable("actual_bar_rpm", diagnostics.actual_bar_rpm)
            append_observable("equivalent_bar_rpm", diagnostics.actual_bar_rpm)
            append_observable("bar_angle_rad", state.bar_angle_rad)
            append_observable("liquid_volume_m3", diagnostics.liquid_volume_m3)
            append_observable(
                "liquid_volume_error_m3",
                diagnostics.liquid_volume_m3 - initial_volume,
            )
            append_observable(
                "liquid_volume_error",
                (diagnostics.liquid_volume_m3 - initial_volume) / initial_volume,
            )
            append_observable(
                "liquid_mass_error_relative",
                (diagnostics.liquid_volume_m3 - initial_volume) / initial_volume,
            )
            append_observable(
                "liquid_volume_balance_error_relative",
                diagnostics.liquid_volume_balance_error_m3 / initial_volume,
            )
            append_observable("max_divergence", diagnostics.divergence_after_s_inv)
            append_observable(
                "divergence_before_projection", diagnostics.divergence_before_s_inv
            )
            append_observable("pressure_residual", diagnostics.pressure_relative_residual)
            append_observable("pressure_iterations", float(diagnostics.pressure_iterations))
            append_observable("cfl_advective", diagnostics.advective_cfl)
            append_observable("cfl_viscous", diagnostics.viscous_cfl)
            append_observable("cfl_capillary", diagnostics.capillary_cfl)
            append_observable("rotation_increment_rad", diagnostics.rotation_increment_rad)
            append_observable(
                "phase_conservation_residual_m3",
                diagnostics.phase_conservation_residual_m3,
            )
            append_observable(
                "density_conservation_residual_kg",
                diagnostics.density_conservation_residual_kg,
            )
            append_observable(
                "momentum_remap_x_kg_m_s", diagnostics.momentum_remap_x_kg_m_s
            )
            append_observable(
                "momentum_remap_y_kg_m_s", diagnostics.momentum_remap_y_kg_m_s
            )
            append_observable(
                "momentum_remap_z_kg_m_s", diagnostics.momentum_remap_z_kg_m_s
            )
            append_observable(
                "momentum_remap_absolute_kg_m_s",
                diagnostics.momentum_remap_absolute_kg_m_s,
            )
            append_observable(
                "cls_phase_conservation_residual_m3",
                diagnostics.cls_phase_conservation_residual_m3,
            )
            append_observable(
                "cls_density_conservation_residual_kg",
                diagnostics.cls_density_conservation_residual_kg,
            )
            append_observable(
                "cls_momentum_remap_x_kg_m_s",
                diagnostics.cls_momentum_remap_x_kg_m_s,
            )
            append_observable(
                "cls_momentum_remap_y_kg_m_s",
                diagnostics.cls_momentum_remap_y_kg_m_s,
            )
            append_observable(
                "cls_momentum_remap_z_kg_m_s",
                diagnostics.cls_momentum_remap_z_kg_m_s,
            )
            append_observable(
                "cls_momentum_remap_absolute_kg_m_s",
                diagnostics.cls_momentum_remap_absolute_kg_m_s,
            )
            append_observable("kinetic_energy_j", diagnostics.kinetic_energy_lab_j)
            append_observable("kinetic_energy_lab_j", diagnostics.kinetic_energy_lab_j)
            append_observable(
                "kinetic_energy_rotating_j", diagnostics.kinetic_energy_rotating_j
            )
            append_observable(
                "angular_momentum_z_kg_m2_s",
                diagnostics.angular_momentum_z_kg_m2_s,
            )
            append_observable("viscous_dissipation_w", diagnostics.viscous_dissipation_w)
            append_observable("axisymmetry_error", geometry.axisymmetry_error)
            append_observable(
                "bar_gas_contact_area_m2",
                diagnostics.topology.bar_gas_contact_area_m2,
            )
            append_observable(
                "connection_minimum_throat_radius_m",
                diagnostics.topology.minimum_throat_radius_m,
            )
            append_observable(
                "connection_required_throat_radius_m",
                diagnostics.topology.required_resolved_radius_m,
            )
            append_observable(
                "first_contact_proxy", float(diagnostics.topology.touches_bar)
            )
            append_observable(
                "connection_candidate",
                float(diagnostics.topology.resolved_channel),
            )
            append_observable(
                "finite_channel_candidate",
                float(diagnostics.topology.finite_channel_candidate),
            )
            append_observable(
                "isolated_gas_volume_m3", diagnostics.topology.isolated_gas_volume_m3
            )
            return (
                radial.copy(),
                geometry.mean_profile_m.copy(),
                geometry.minimum_profile_m.copy(),
                geometry.maximum_profile_m.copy(),
            )

        def update_events(diagnostics: HF3DStepDiagnostics) -> None:
            nonlocal previous_topology, first_resolved_seen
            nonlocal resolved_start_time, sustained_reported
            topology = diagnostics.topology
            previous = previous_topology
            if topology.touches_bar and (previous is None or not previous.touches_bar):
                events.append(
                    Event(
                        state.time_s,
                        "gas_touches_bar",
                        "Top-connected gas first contacts a voxel face of the capsule.",
                        {"bar_gas_contact_area_m2": topology.bar_gas_contact_area_m2},
                    )
                )
            if topology.resolved_channel and (
                previous is None or not previous.resolved_channel
            ):
                kind = "resolved_channel" if not first_resolved_seen else "channel_reconnect"
                events.append(
                    Event(
                        state.time_s,
                        kind,
                        "Resolution-gated top-connected gas path observed; simulation continues.",
                        {
                            "minimum_throat_radius_m": topology.minimum_throat_radius_m,
                            "required_radius_m": topology.required_resolved_radius_m,
                        },
                    )
                )
                first_resolved_seen = True
                resolved_start_time = state.time_s
                sustained_reported = False
            if previous is not None and previous.resolved_channel and not topology.resolved_channel:
                events.append(
                    Event(
                        state.time_s,
                        "channel_break",
                        "Previously resolved gas path is no longer resolution-gated connected.",
                        {"minimum_throat_radius_m": topology.minimum_throat_radius_m},
                    )
                )
                resolved_start_time = None
                sustained_reported = False
            radius_ok = (
                topology.resolved_channel
                and topology.minimum_throat_radius_m
                >= config.high_fidelity.stable_channel_radius_m
            )
            if radius_ok:
                if resolved_start_time is None:
                    resolved_start_time = state.time_s
                period = (
                    math.inf
                    if abs(diagnostics.actual_bar_rpm) <= 1.0e-12
                    else 60.0 / abs(diagnostics.actual_bar_rpm)
                )
                required_hold = max(
                    config.high_fidelity.stable_channel_hold_time_s,
                    3.0 * period,
                )
                if (
                    not sustained_reported
                    and math.isfinite(required_hold)
                    and state.time_s - resolved_start_time >= required_hold
                ):
                    events.append(
                        Event(
                            state.time_s,
                            "sustained_channel_numerical",
                            "Numerical radius-and-hold convention satisfied; experimental stability is unverified.",
                            {
                                "hold_time_s": state.time_s - resolved_start_time,
                                "required_hold_time_s": required_hold,
                                "stable_radius_m": config.high_fidelity.stable_channel_radius_m,
                            },
                        )
                    )
                    sustained_reported = True
            else:
                resolved_start_time = None
                sustained_reported = False
            previous_topology = topology

        (
            live_surface_radius_m,
            live_surface_profile_m,
            live_surface_minimum_m,
            live_surface_maximum_m,
        ) = record_frame(last_diag)
        previous_topology = last_diag.topology
        if progress is not None:
            progress(
                Progress(
                    fraction=0.0,
                    simulated_time_s=0.0,
                    step=0,
                    stage="initialised",
                    message="HF3D local-law fields initialised",
                    metrics={
                        "active_cells": grid.active_cell_count,
                        "surface_radius_m": live_surface_radius_m.tolist(),
                        "surface_profile_m": live_surface_profile_m.tolist(),
                        "surface_profile_minimum_m": (
                            live_surface_minimum_m.tolist()
                        ),
                        "surface_profile_maximum_m": (
                            live_surface_maximum_m.tolist()
                        ),
                    },
                )
            )
        last_live_preview_wall_s = time.monotonic()

        end_time = config.run.duration_s
        output_interval = config.run.output_interval_s
        next_output = min(output_interval, end_time)
        time_tolerance = 128.0 * np.finfo(float).eps * max(end_time, 1.0)
        status = RunStatus.COMPLETED
        while state.time_s < end_time - time_tolerance:
            if not run_control.checkpoint():
                status = RunStatus.STOPPED
                events.append(
                    Event(
                        state.time_s,
                        "soft_stop",
                        run_control.stop_reason or "user requested stop",
                    )
                )
                break
            stable_dt, _ = self._time_step_limits(state, config, grid)
            target_time = min(next_output, end_time)
            dt_s = min(stable_dt, target_time - state.time_s)
            if not math.isfinite(dt_s) or dt_s <= 0.0:
                raise FloatingPointError("HF3D adaptive time step became non-positive")
            last_diag = self.advance_one_step(
                state,
                config,
                grid,
                projection,
                dt_s,
                expected_liquid_volume_m3=expected_volume,
            )
            expected_volume = last_diag.expected_liquid_volume_m3
            update_events(last_diag)
            step_time.append(state.time_s)
            step_dt.append(dt_s)
            step_clip.append(last_diag.phase_clip_absolute_volume_m3)
            step_repair.append(last_diag.phase_repair_absolute_volume_m3)
            step_divergence.append(last_diag.divergence_after_s_inv)
            step_pressure_residual.append(last_diag.pressure_relative_residual)
            step_pressure_iterations.append(float(last_diag.pressure_iterations))
            step_phase_conservation.append(
                last_diag.phase_conservation_residual_m3
            )
            step_density_conservation.append(
                last_diag.density_conservation_residual_kg
            )
            step_momentum_remap_x.append(last_diag.momentum_remap_x_kg_m_s)
            step_momentum_remap_y.append(last_diag.momentum_remap_y_kg_m_s)
            step_momentum_remap_z.append(last_diag.momentum_remap_z_kg_m_s)
            step_momentum_remap_absolute.append(
                last_diag.momentum_remap_absolute_kg_m_s
            )
            step_cls_phase_conservation.append(
                last_diag.cls_phase_conservation_residual_m3
            )
            step_cls_density_conservation.append(
                last_diag.cls_density_conservation_residual_kg
            )
            step_cls_momentum_remap_x.append(
                last_diag.cls_momentum_remap_x_kg_m_s
            )
            step_cls_momentum_remap_y.append(
                last_diag.cls_momentum_remap_y_kg_m_s
            )
            step_cls_momentum_remap_z.append(
                last_diag.cls_momentum_remap_z_kg_m_s
            )
            step_cls_momentum_remap_absolute.append(
                last_diag.cls_momentum_remap_absolute_kg_m_s
            )
            cumulative_clip += last_diag.phase_clip_absolute_volume_m3
            cumulative_repair += last_diag.phase_repair_absolute_volume_m3
            single_clip = last_diag.phase_clip_absolute_volume_m3 / initial_volume
            single_repair = last_diag.phase_repair_absolute_volume_m3 / initial_volume
            maximum_single_clip = max(maximum_single_clip, single_clip)
            maximum_single_repair = max(maximum_single_repair, single_repair)
            maximum_divergence = max(
                maximum_divergence, last_diag.divergence_after_s_inv
            )
            maximum_pressure_residual = max(
                maximum_pressure_residual, last_diag.pressure_relative_residual
            )
            maximum_cfl = max(maximum_cfl, last_diag.advective_cfl)
            minimum_dt = min(minimum_dt, dt_s)
            if (
                single_clip
                > config.high_fidelity.maximum_single_step_phase_clip_relative
                or cumulative_clip / initial_volume
                > config.high_fidelity.maximum_cumulative_phase_clip_relative
            ):
                raise FloatingPointError(
                    "HF3D phase-clipping numerical quality gate failed: "
                    f"single={single_clip:.3e}, cumulative={cumulative_clip / initial_volume:.3e}"
                )
            if (
                single_repair
                > config.high_fidelity.maximum_single_step_phase_repair_relative
                or cumulative_repair / initial_volume
                > config.high_fidelity.maximum_cumulative_phase_repair_relative
            ):
                raise FloatingPointError(
                    "HF3D phase-repair numerical quality gate failed: "
                    f"single={single_repair:.3e}, cumulative={cumulative_repair / initial_volume:.3e}"
                )
            reached_output = state.time_s >= next_output - time_tolerance
            reached_end = state.time_s >= end_time - time_tolerance
            if reached_output or reached_end:
                if reached_output and abs(state.time_s - next_output) <= time_tolerance:
                    state.time_s = next_output
                if reached_end and abs(state.time_s - end_time) <= time_tolerance:
                    state.time_s = end_time
                (
                    live_surface_radius_m,
                    live_surface_profile_m,
                    live_surface_minimum_m,
                    live_surface_maximum_m,
                ) = record_frame(last_diag)
                while next_output <= state.time_s + time_tolerance:
                    next_output += output_interval
                if progress is not None:
                    progress(
                        Progress(
                            fraction=min(state.time_s / end_time, 1.0),
                            simulated_time_s=state.time_s,
                            step=state.step,
                            stage="completed" if reached_end else "running",
                            message="HF3D variable-density local-law step",
                            metrics={
                                "active_cells": grid.active_cell_count,
                                "advective_cfl": last_diag.advective_cfl,
                                "max_divergence_s_inv": last_diag.divergence_after_s_inv,
                                "liquid_balance_error_relative": (
                                    last_diag.liquid_volume_balance_error_m3
                                    / initial_volume
                                ),
                                "surface_radius_m": live_surface_radius_m.tolist(),
                                "surface_profile_m": live_surface_profile_m.tolist(),
                                "surface_profile_minimum_m": (
                                    live_surface_minimum_m.tolist()
                                ),
                                "surface_profile_maximum_m": (
                                    live_surface_maximum_m.tolist()
                                ),
                            },
                        )
                    )
                    last_live_preview_wall_s = time.monotonic()
            elif progress is not None:
                current_wall_s = time.monotonic()
                if (
                    current_wall_s - last_live_preview_wall_s
                    >= _LIVE_SURFACE_PREVIEW_INTERVAL_WALL_S
                ):
                    live_geometry = surface_geometry_3d(
                        state.phase,
                        grid,
                        liquid_height_m=config.container.liquid_depth_m,
                        bar_top_m=(
                            config.stir_bar.bottom_clearance_m
                            + 2.0 * config.stir_bar.radius_m
                        ),
                        radial_bins=radial_bins,
                    )
                    progress(
                        Progress(
                            fraction=min(state.time_s / end_time, 1.0),
                            simulated_time_s=state.time_s,
                            step=state.step,
                            stage="running",
                            message=(
                                "HF3D variable-density local-law step; "
                                "read-only live surface preview"
                            ),
                            metrics={
                                "active_cells": grid.active_cell_count,
                                "advective_cfl": last_diag.advective_cfl,
                                "max_divergence_s_inv": (
                                    last_diag.divergence_after_s_inv
                                ),
                                "surface_radius_m": (
                                    live_geometry.radial_centres_m.tolist()
                                ),
                                "surface_profile_m": (
                                    live_geometry.mean_profile_m.tolist()
                                ),
                                "surface_profile_minimum_m": (
                                    live_geometry.minimum_profile_m.tolist()
                                ),
                                "surface_profile_maximum_m": (
                                    live_geometry.maximum_profile_m.tolist()
                                ),
                            },
                        )
                    )
                    last_live_preview_wall_s = current_wall_s

        if not times or state.time_s > times[-1] + time_tolerance:
            record_frame(last_diag)

        final_rpm, _ = _drive_rpm_and_slope(config, state.time_s)
        final_omega = _rpm_to_omega(final_rpm)
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
            "diagnostic_phase_conservation_residual_m3": np.asarray(
                step_phase_conservation, dtype=np.float64
            ),
            "diagnostic_density_conservation_residual_kg": np.asarray(
                step_density_conservation, dtype=np.float64
            ),
            "diagnostic_momentum_remap_x_kg_m_s": np.asarray(
                step_momentum_remap_x, dtype=np.float64
            ),
            "diagnostic_momentum_remap_y_kg_m_s": np.asarray(
                step_momentum_remap_y, dtype=np.float64
            ),
            "diagnostic_momentum_remap_z_kg_m_s": np.asarray(
                step_momentum_remap_z, dtype=np.float64
            ),
            "diagnostic_momentum_remap_absolute_kg_m_s": np.asarray(
                step_momentum_remap_absolute, dtype=np.float64
            ),
            "diagnostic_cls_phase_conservation_residual_m3": np.asarray(
                step_cls_phase_conservation, dtype=np.float64
            ),
            "diagnostic_cls_density_conservation_residual_kg": np.asarray(
                step_cls_density_conservation, dtype=np.float64
            ),
            "diagnostic_cls_momentum_remap_x_kg_m_s": np.asarray(
                step_cls_momentum_remap_x, dtype=np.float64
            ),
            "diagnostic_cls_momentum_remap_y_kg_m_s": np.asarray(
                step_cls_momentum_remap_y, dtype=np.float64
            ),
            "diagnostic_cls_momentum_remap_z_kg_m_s": np.asarray(
                step_cls_momentum_remap_z, dtype=np.float64
            ),
            "diagnostic_cls_momentum_remap_absolute_kg_m_s": np.asarray(
                step_cls_momentum_remap_absolute, dtype=np.float64
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
            "phase_conservation_residual_m3": "m^3",
            "density_conservation_residual_kg": "kg",
            "momentum_remap_x_kg_m_s": "kg*m/s",
            "momentum_remap_y_kg_m_s": "kg*m/s",
            "momentum_remap_z_kg_m_s": "kg*m/s",
            "momentum_remap_absolute_kg_m_s": "kg*m/s",
            "cls_phase_conservation_residual_m3": "m^3",
            "cls_density_conservation_residual_kg": "kg",
            "cls_momentum_remap_x_kg_m_s": "kg*m/s",
            "cls_momentum_remap_y_kg_m_s": "kg*m/s",
            "cls_momentum_remap_z_kg_m_s": "kg*m/s",
            "cls_momentum_remap_absolute_kg_m_s": "kg*m/s",
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
                else "m^3"
                if "volume_m3" in name
                else "s^-1"
                if "divergence" in name
                else "1"
                if "residual" in name or "iterations" in name
                else "m/s"
                if "u_" in name
                else "m"
            )
            for name in fields
        }
        field_units.update(
            {
                "diagnostic_phase_conservation_residual_m3": "m^3",
                "diagnostic_density_conservation_residual_kg": "kg",
                "diagnostic_momentum_remap_x_kg_m_s": "kg*m/s",
                "diagnostic_momentum_remap_y_kg_m_s": "kg*m/s",
                "diagnostic_momentum_remap_z_kg_m_s": "kg*m/s",
                "diagnostic_momentum_remap_absolute_kg_m_s": "kg*m/s",
                "diagnostic_cls_phase_conservation_residual_m3": "m^3",
                "diagnostic_cls_density_conservation_residual_kg": "kg",
                "diagnostic_cls_momentum_remap_x_kg_m_s": "kg*m/s",
                "diagnostic_cls_momentum_remap_y_kg_m_s": "kg*m/s",
                "diagnostic_cls_momentum_remap_z_kg_m_s": "kg*m/s",
                "diagnostic_cls_momentum_remap_absolute_kg_m_s": "kg*m/s",
            }
        )
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

        def diagnostic_summary(values: list[float]) -> dict[str, float]:
            array = np.asarray(values, dtype=np.float64)
            return {
                "maximum_absolute": (
                    0.0 if array.size == 0 else float(np.max(np.abs(array)))
                ),
                "cumulative_absolute": float(np.sum(np.abs(array))),
            }

        conservation_summary = {
            "internal_step_count": len(step_time),
            "phase_conservation_residual_m3": diagnostic_summary(
                step_phase_conservation
            ),
            "density_conservation_residual_kg": diagnostic_summary(
                step_density_conservation
            ),
            "momentum_remap_x_kg_m_s": diagnostic_summary(
                step_momentum_remap_x
            ),
            "momentum_remap_y_kg_m_s": diagnostic_summary(
                step_momentum_remap_y
            ),
            "momentum_remap_z_kg_m_s": diagnostic_summary(
                step_momentum_remap_z
            ),
            "momentum_remap_absolute_kg_m_s": diagnostic_summary(
                step_momentum_remap_absolute
            ),
            "cls_phase_conservation_residual_m3": diagnostic_summary(
                step_cls_phase_conservation
            ),
            "cls_density_conservation_residual_kg": diagnostic_summary(
                step_cls_density_conservation
            ),
            "cls_momentum_remap_x_kg_m_s": diagnostic_summary(
                step_cls_momentum_remap_x
            ),
            "cls_momentum_remap_y_kg_m_s": diagnostic_summary(
                step_cls_momentum_remap_y
            ),
            "cls_momentum_remap_z_kg_m_s": diagnostic_summary(
                step_cls_momentum_remap_z
            ),
            "cls_momentum_remap_absolute_kg_m_s": diagnostic_summary(
                step_cls_momentum_remap_absolute
            ),
            "phase_clip_absolute_volume_m3": diagnostic_summary(step_clip),
            "phase_repair_absolute_volume_m3": diagnostic_summary(step_repair),
        }
        metadata = {
            "backend_id": self.backend_id,
            "fidelity_label": "HF3D-local-law",
            "evidence_level": (
                "NUMERICAL-IMPLEMENTATION-ONLY until frozen refinement and experiment validation"
            ),
            "model": MODEL_NAME,
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
            "capabilities": dict(self.capabilities),
            "limitations": list(LIMITATIONS),
            "diagnostics": {
                "active_cells": grid.active_cell_count,
                "numerical_thread_policy": NUMERICAL_THREAD_POLICY.to_dict(),
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
                "conservation_and_momentum_remap": conservation_summary,
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


HF3DBackend = HighFidelityBackend


__all__ = [
    "CAPABILITIES",
    "HF3DBackend",
    "HF3DState",
    "HF3DStepDiagnostics",
    "HighFidelityBackend",
    "LIMITATIONS",
    "MODEL_NAME",
    "_drive_rpm_and_slope",
    "_integrated_bar_angle_rad",
]
