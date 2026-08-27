"""Transactional single-trial advancement for the formal HF3D backend."""

from __future__ import annotations

import math
from typing import Callable

import numpy as np

from airvortex.core import SimulationConfig
from airvortex.hf3d.drive import (
    drive_rpm_and_slope as _drive_rpm_and_slope,
    integrated_bar_angle_rad as _integrated_bar_angle_rad,
    rpm_to_omega as _rpm_to_omega,
)
from airvortex.hf3d.pressure import PressureProjection3D
from airvortex.hf3d.types import (
    HF3DPostProjectionCFLExceeded,
    HF3DRejectedStepDiagnostics,
    HF3DState,
    HF3DStepDiagnostics,
    TIME_STEP_LIMITER_CODES as _TIME_STEP_LIMITER_CODES,
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
    conservative_cls_3d,
    mixture_property_3d,
    surface_tension_acceleration_3d,
    topology_diagnostics_3d,
)
from airvortex.numerics.momentum3d import (
    advect_momentum_3d,
    newtonian_stress_acceleration_3d,
    relax_no_slip_boundaries_3d,
    rotating_frame_acceleration_3d,
    rotating_to_laboratory_velocity_3d,
)


def _maximum_vector_magnitude(
    grid: ImmersedCartesianGrid,
    x: FloatArray,
    y: FloatArray,
    z: FloatArray,
) -> float:
    magnitude = np.sqrt(x * x + y * y + z * z)
    return float(np.max(magnitude[grid.active_mask]))


def advance_one_step(
    state: HF3DState,
    config: SimulationConfig,
    grid: ImmersedCartesianGrid,
    projection: PressureProjection3D,
    dt_s: float,
    *,
    expected_liquid_volume_m3: float,
    time_step_limiter: str = "direct_call",
    cfl_retry_count: int = 0,
    initial_attempt_dt_s: float | None = None,
    maximum_rejected_post_projection_cfl: float = 0.0,
    advective_cfl_fn: Callable[..., float] = advective_cfl,
) -> HF3DStepDiagnostics:
    """Advance one conservative local-law step and commit only after all gates."""

    hf = config.high_fidelity
    fluid = config.fluid
    if not math.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError("dt_s must be finite and positive")
    if time_step_limiter not in _TIME_STEP_LIMITER_CODES:
        raise ValueError(f"unknown HF3D time-step limiter: {time_step_limiter}")
    if cfl_retry_count < 0:
        raise ValueError("cfl_retry_count must be non-negative")
    initial_dt = dt_s if initial_attempt_dt_s is None else initial_attempt_dt_s
    if not math.isfinite(initial_dt) or initial_dt < dt_s:
        raise ValueError("initial_attempt_dt_s must be finite and at least dt_s")
    if (
        not math.isfinite(maximum_rejected_post_projection_cfl)
        or maximum_rejected_post_projection_cfl < 0.0
    ):
        raise ValueError(
            "maximum_rejected_post_projection_cfl must be finite and non-negative"
        )
    starting_cfl = advective_cfl_fn(
        state.u_x_face, state.u_y_face, state.u_z_face, grid, dt_s
    )
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
    maximum_momentum_advection_acceleration = _maximum_vector_magnitude(
        grid,
        (ux - old_ux) / dt_s,
        (uy - old_uy) / dt_s,
        (uz - old_uz) / dt_s,
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
    pre_source_ux_face, pre_source_uy_face, pre_source_uz_face = (
        grid.cells_to_faces(ux, uy, uz)
    )
    pre_source_cfl = advective_cfl_fn(
        pre_source_ux_face,
        pre_source_uy_face,
        pre_source_uz_face,
        grid,
        dt_s,
    )
    frame_x, frame_y, frame_z = rotating_frame_acceleration_3d(
        ux, uy, grid, omega_mid, omega_dot
    )
    maximum_rotating_frame_acceleration = _maximum_vector_magnitude(
        grid, frame_x, frame_y, frame_z
    )
    ux = ux + dt_s * (csf_x + frame_x)
    uy = uy + dt_s * (csf_y + frame_y)
    uz = uz + dt_s * (csf_z + frame_z - fluid.gravity_m_s2)
    ux_face, uy_face, uz_face = grid.cells_to_faces(ux, uy, uz)
    pre_projection_cfl = advective_cfl_fn(
        ux_face, uy_face, uz_face, grid, dt_s
    )
    pressure_history_checkpoint = projection.checkpoint_history()
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
    maximum_pressure_projection_acceleration = _maximum_vector_magnitude(
        grid,
        (projected_ux - ux) / dt_s,
        (projected_uy - uy) / dt_s,
        (projected_uz - uz) / dt_s,
    )
    post_cfl = advective_cfl_fn(
        ux_face, uy_face, uz_face, grid, dt_s
    )
    if post_cfl > hf.cfl_limit * (1.0 + 1.0e-9):
        rejected = HF3DRejectedStepDiagnostics(
            step_one_based=next_step,
            state_time_s=state.time_s,
            attempted_dt_s=dt_s,
            starting_advective_cfl=starting_cfl,
            pre_source_advective_cfl=pre_source_cfl,
            pre_projection_advective_cfl=pre_projection_cfl,
            post_projection_advective_cfl=post_cfl,
            cfl_hard_limit=hf.cfl_limit,
            pressure_relative_residual=(
                projection_diagnostics.pressure_relative_residual
            ),
            pressure_iterations=projection_diagnostics.iterations,
            maximum_momentum_advection_acceleration_m_s2=(
                maximum_momentum_advection_acceleration
            ),
            maximum_viscous_acceleration_m_s2=(
                viscous_diagnostics.maximum_viscous_acceleration_m_s2
            ),
            maximum_boundary_acceleration_m_s2=(
                boundary_diagnostics.maximum_relaxation_acceleration_m_s2
            ),
            maximum_surface_tension_acceleration_m_s2=(
                csf_diagnostics.maximum_acceleration_m_s2
            ),
            maximum_rotating_frame_acceleration_m_s2=(
                maximum_rotating_frame_acceleration
            ),
            gravity_acceleration_m_s2=fluid.gravity_m_s2,
            maximum_pressure_projection_acceleration_m_s2=(
                maximum_pressure_projection_acceleration
            ),
        )
        projection.restore_history(pressure_history_checkpoint)
        raise HF3DPostProjectionCFLExceeded(rejected)
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
        initial_attempt_dt_s=initial_dt,
        time_step_limiter=time_step_limiter,
        cfl_retry_count=cfl_retry_count,
        starting_advective_cfl=starting_cfl,
        pre_source_advective_cfl=pre_source_cfl,
        pre_projection_advective_cfl=pre_projection_cfl,
        maximum_rejected_post_projection_cfl=(
            maximum_rejected_post_projection_cfl
        ),
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
        maximum_momentum_advection_acceleration_m_s2=(
            maximum_momentum_advection_acceleration
        ),
        maximum_viscous_acceleration_m_s2=(
            viscous_diagnostics.maximum_viscous_acceleration_m_s2
        ),
        maximum_boundary_acceleration_m_s2=(
            boundary_diagnostics.maximum_relaxation_acceleration_m_s2
        ),
        maximum_rotating_frame_acceleration_m_s2=(
            maximum_rotating_frame_acceleration
        ),
        gravity_acceleration_m_s2=fluid.gravity_m_s2,
        maximum_pressure_projection_acceleration_m_s2=(
            maximum_pressure_projection_acceleration
        ),
        boundary_rotating_energy_change_j=(
            boundary_diagnostics.rotating_frame_kinetic_energy_change_j
        ),
        topology=topology,
    )



__all__ = ["advance_one_step"]
