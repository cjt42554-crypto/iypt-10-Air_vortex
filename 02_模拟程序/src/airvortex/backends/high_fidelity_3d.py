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
    ImmersedCartesianGrid,
    advective_cfl,
)
from airvortex.numerics.interface3d import (
    initialise_flat_phase_3d,
    mixture_property_3d,
    topology_diagnostics_3d,
)
from airvortex.numerics.momentum3d import rotating_to_laboratory_velocity_3d
from airvortex.hf3d.drive import (
    drive_rpm_and_slope as _drive_rpm_and_slope,
    integrated_bar_angle_rad as _integrated_bar_angle_rad,
    rpm_to_omega as _rpm_to_omega,
)
from airvortex.hf3d.events import TopologyEventTracker
from airvortex.hf3d.pressure import (
    PressureProjection3D,
    make_pressure_projection as _make_pressure_projection,
)
from airvortex.hf3d.recording import (
    FrameRecorder,
    StepDiagnosticsBuffer,
    assemble_result,
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

        recorder = FrameRecorder(config, grid, state, initial_volume)
        diagnostic_buffer = StepDiagnosticsBuffer(initial_volume)
        events: list[Event] = []
        event_tracker = TopologyEventTracker(config, events)
        last_diag = self._zero_step_diagnostics(
            state, config, grid, expected_volume
        )

        (
            live_surface_radius_m,
            live_surface_profile_m,
            live_surface_minimum_m,
            live_surface_maximum_m,
        ) = recorder.record_frame(last_diag)
        event_tracker.initialise(last_diag.topology)
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
            event_tracker.update(state.time_s, last_diag)
            diagnostic_buffer.append(state.time_s, last_diag, config)
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
                ) = recorder.record_frame(last_diag)
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
                    live_geometry = recorder.surface_geometry()
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

        if (
            not recorder.times
            or state.time_s > recorder.times[-1] + time_tolerance
        ):
            recorder.record_frame(last_diag)

        return assemble_result(
            config,
            state,
            grid,
            recorder,
            diagnostic_buffer,
            events,
            projection,
            pressure_accelerator,
            status,
            expected_volume,
            time_tolerance,
            backend_id=self.backend_id,
            model_name=MODEL_NAME,
            capabilities=self.capabilities,
            limitations=LIMITATIONS,
            numerical_thread_policy=NUMERICAL_THREAD_POLICY.to_dict(),
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
