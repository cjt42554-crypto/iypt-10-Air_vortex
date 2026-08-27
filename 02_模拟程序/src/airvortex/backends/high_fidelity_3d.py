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

from dataclasses import asdict
import json
import math
import time
from types import MappingProxyType
from typing import Callable

import numpy as np

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
    FloatArray,
    ImmersedCartesianGrid,
    advective_cfl,
)
from airvortex.numerics.interface3d import (
    Topology3DDiagnostics,
    azimuthal_average_3d,
    initialise_flat_phase_3d,
    mixture_property_3d,
    surface_geometry_3d,
    topology_diagnostics_3d,
)
from airvortex.numerics.momentum3d import (
    laboratory_cylindrical_velocity_3d,
    rotating_to_laboratory_velocity_3d,
)
from airvortex.hf3d.drive import (
    drive_rpm_and_slope as _drive_rpm_and_slope,
    integrated_bar_angle_rad as _integrated_bar_angle_rad,
    rpm_to_omega as _rpm_to_omega,
)
from airvortex.hf3d.pressure import (
    PressureProjection3D,
    make_pressure_projection as _make_pressure_projection,
)
from airvortex.hf3d.stepper import advance_one_step as _advance_one_step
from airvortex.hf3d.timestep import (
    propose_time_step,
    retry_time_step_s as _retry_time_step_s,
)
from airvortex.hf3d.types import (
    HF3DPostProjectionCFLExceeded,
    HF3DRejectedStepDiagnostics,
    HF3DState,
    HF3DStepDiagnostics,
    TIME_STEP_LIMITER_CODES as _TIME_STEP_LIMITER_CODES,
)


ProgressCallback = Callable[[Progress], None]
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
    ) -> tuple[float, dict[str, float | str]]:
        proposal = propose_time_step(state, config, grid)
        return proposal.stable_dt_s, proposal.legacy_limits_dict()

    def advance_one_step(
        self,
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
    ) -> HF3DStepDiagnostics:
        return _advance_one_step(
            state,
            config,
            grid,
            projection,
            dt_s,
            expected_liquid_volume_m3=expected_liquid_volume_m3,
            time_step_limiter=time_step_limiter,
            cfl_retry_count=cfl_retry_count,
            initial_attempt_dt_s=initial_attempt_dt_s,
            maximum_rejected_post_projection_cfl=(
                maximum_rejected_post_projection_cfl
            ),
            advective_cfl_fn=advective_cfl,
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
            initial_attempt_dt_s=0.0,
            time_step_limiter="initial_state",
            cfl_retry_count=0,
            starting_advective_cfl=0.0,
            pre_source_advective_cfl=0.0,
            pre_projection_advective_cfl=0.0,
            maximum_rejected_post_projection_cfl=0.0,
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
            maximum_momentum_advection_acceleration_m_s2=0.0,
            maximum_viscous_acceleration_m_s2=0.0,
            maximum_boundary_acceleration_m_s2=0.0,
            maximum_rotating_frame_acceleration_m_s2=0.0,
            gravity_acceleration_m_s2=config.fluid.gravity_m_s2,
            maximum_pressure_projection_acceleration_m_s2=0.0,
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
        hf = config.high_fidelity
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
        step_initial_attempt_dt: list[float] = []
        step_time_limiter_code: list[float] = []
        step_cfl_retry_count: list[float] = []
        step_starting_cfl: list[float] = []
        step_pre_source_cfl: list[float] = []
        step_pre_projection_cfl: list[float] = []
        step_post_projection_cfl: list[float] = []
        step_maximum_rejected_cfl: list[float] = []
        step_momentum_advection_acceleration: list[float] = []
        step_viscous_acceleration: list[float] = []
        step_boundary_acceleration: list[float] = []
        step_surface_tension_acceleration: list[float] = []
        step_rotating_frame_acceleration: list[float] = []
        step_gravity_acceleration: list[float] = []
        step_pressure_projection_acceleration: list[float] = []
        step_clip: list[float] = []
        step_repair: list[float] = []
        step_divergence: list[float] = []
        step_pressure_residual: list[float] = []
        step_pressure_iterations: list[float] = []
        cumulative_clip = 0.0
        cumulative_repair = 0.0
        maximum_single_clip = 0.0
        maximum_single_repair = 0.0
        maximum_divergence = 0.0
        maximum_pressure_residual = 0.0
        maximum_cfl = 0.0
        maximum_rejected_cfl = 0.0
        cfl_retry_attempts_total = 0
        cfl_retried_accepted_steps = 0
        limiter_counts = {name: 0 for name in _TIME_STEP_LIMITER_CODES}
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
            stable_dt, time_step_limits = self._time_step_limits(
                state, config, grid
            )
            target_time = min(next_output, end_time)
            remaining_to_target = target_time - state.time_s
            dt_s = min(stable_dt, remaining_to_target)
            if not math.isfinite(dt_s) or dt_s <= 0.0:
                raise FloatingPointError("HF3D adaptive time step became non-positive")
            if remaining_to_target < stable_dt:
                limiter = "output_alignment"
            else:
                limiter = str(time_step_limits["selected_limiter"])
            initial_attempt_dt_s = dt_s
            retry_count = 0
            rejected_cfl_for_step = 0.0
            while True:
                try:
                    last_diag = self.advance_one_step(
                        state,
                        config,
                        grid,
                        projection,
                        dt_s,
                        expected_liquid_volume_m3=expected_volume,
                        time_step_limiter=limiter,
                        cfl_retry_count=retry_count,
                        initial_attempt_dt_s=initial_attempt_dt_s,
                        maximum_rejected_post_projection_cfl=(
                            rejected_cfl_for_step
                        ),
                    )
                    break
                except HF3DPostProjectionCFLExceeded as exc:
                    failure = exc.diagnostics
                    rejected_cfl_for_step = max(
                        rejected_cfl_for_step,
                        failure.post_projection_advective_cfl,
                    )
                    if retry_count >= hf.maximum_cfl_step_retries:
                        payload = {
                            "kind": "HF3D_CFL_RETRY_EXHAUSTED",
                            "attempt_count": retry_count + 1,
                            "retry_count": retry_count,
                            "maximum_cfl_step_retries": (
                                hf.maximum_cfl_step_retries
                            ),
                            "initial_attempt_dt_s": initial_attempt_dt_s,
                            "time_step_limiter": limiter,
                            "cfl_controller_target_fraction": (
                                hf.cfl_controller_target_fraction
                            ),
                            "maximum_rejected_post_projection_cfl": (
                                rejected_cfl_for_step
                            ),
                            "pressure_accelerator": pressure_accelerator,
                            "last_rejected_trial": asdict(failure),
                        }
                        raise FloatingPointError(
                            "HF3D CFL retry gate exhausted: "
                            + json.dumps(
                                payload,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                        ) from exc
                    retry_count += 1
                    next_dt_s = _retry_time_step_s(
                        dt_s,
                        failure.post_projection_advective_cfl,
                        hf.cfl_limit,
                        hf.cfl_controller_target_fraction,
                    )
                    events.append(
                        Event(
                            state.time_s,
                            "cfl_step_retry",
                            "Rejected trial was rolled back and retried below the independent CFL controller target.",
                            {
                                "retry_number": retry_count,
                                "previous_limiter": limiter,
                                "next_dt_s": next_dt_s,
                                "pressure_accelerator_selected": (
                                    pressure_accelerator["selected"]
                                ),
                                **asdict(failure),
                            },
                        )
                    )
                    dt_s = next_dt_s
                    limiter = "post_projection_cfl_retry"
            expected_volume = last_diag.expected_liquid_volume_m3
            update_events(last_diag)
            step_time.append(state.time_s)
            step_dt.append(last_diag.dt_s)
            step_initial_attempt_dt.append(last_diag.initial_attempt_dt_s)
            step_time_limiter_code.append(
                float(_TIME_STEP_LIMITER_CODES[last_diag.time_step_limiter])
            )
            step_cfl_retry_count.append(float(last_diag.cfl_retry_count))
            step_starting_cfl.append(last_diag.starting_advective_cfl)
            step_pre_source_cfl.append(last_diag.pre_source_advective_cfl)
            step_pre_projection_cfl.append(
                last_diag.pre_projection_advective_cfl
            )
            step_post_projection_cfl.append(last_diag.advective_cfl)
            step_maximum_rejected_cfl.append(
                last_diag.maximum_rejected_post_projection_cfl
            )
            step_momentum_advection_acceleration.append(
                last_diag.maximum_momentum_advection_acceleration_m_s2
            )
            step_viscous_acceleration.append(
                last_diag.maximum_viscous_acceleration_m_s2
            )
            step_boundary_acceleration.append(
                last_diag.maximum_boundary_acceleration_m_s2
            )
            step_surface_tension_acceleration.append(
                last_diag.maximum_surface_tension_acceleration_m_s2
            )
            step_rotating_frame_acceleration.append(
                last_diag.maximum_rotating_frame_acceleration_m_s2
            )
            step_gravity_acceleration.append(last_diag.gravity_acceleration_m_s2)
            step_pressure_projection_acceleration.append(
                last_diag.maximum_pressure_projection_acceleration_m_s2
            )
            cfl_retry_attempts_total += last_diag.cfl_retry_count
            if last_diag.cfl_retry_count:
                cfl_retried_accepted_steps += 1
            limiter_counts[last_diag.time_step_limiter] += 1
            step_clip.append(last_diag.phase_clip_absolute_volume_m3)
            step_repair.append(last_diag.phase_repair_absolute_volume_m3)
            step_divergence.append(last_diag.divergence_after_s_inv)
            step_pressure_residual.append(last_diag.pressure_relative_residual)
            step_pressure_iterations.append(float(last_diag.pressure_iterations))
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
            maximum_rejected_cfl = max(
                maximum_rejected_cfl,
                last_diag.maximum_rejected_post_projection_cfl,
            )
            minimum_dt = min(minimum_dt, last_diag.dt_s)
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
                "time_step_limiter_codes": dict(_TIME_STEP_LIMITER_CODES),
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


HF3DBackend = HighFidelityBackend


__all__ = [
    "CAPABILITIES",
    "HF3DBackend",
    "HF3DPostProjectionCFLExceeded",
    "HF3DRejectedStepDiagnostics",
    "HF3DState",
    "HF3DStepDiagnostics",
    "HighFidelityBackend",
    "LIMITATIONS",
    "MODEL_NAME",
    "_drive_rpm_and_slope",
    "_integrated_bar_angle_rad",
]
