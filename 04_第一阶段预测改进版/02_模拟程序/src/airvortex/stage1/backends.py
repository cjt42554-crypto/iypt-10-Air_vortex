"""Isolated predictive wrappers for the forward and HF3D backends."""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from airvortex.backends import forward_topology as _forward
from airvortex.backends import high_fidelity_3d as _hf
from airvortex.core import BackendMode, SimulationConfig, SimulationResult
from airvortex.numerics.interface3d import (
    mixture_property_3d,
    surface_tension_acceleration_3d,
)
from airvortex.numerics.momentum3d import (
    newtonian_stress_acceleration_3d,
    relax_no_slip_boundaries_3d,
    rotating_frame_acceleration_3d,
    rotating_to_laboratory_velocity_3d,
)

from .cls3d import compress_phase_density_momentum_consistently
from .measurement import integrate_camera_measurement
from .observables import canonicalize_forward, canonicalize_hf3d
from .topology3d import topology_diagnostics_widest_path_3d
from .transport3d import (
    advect_phase_density_momentum_consistently,
    advective_cfl_face_flux,
    maximum_donor_cell_rate_s_inv,
)


def _output_targets(source_time_s: np.ndarray, interval_s: float) -> np.ndarray:
    if source_time_s.size == 0:
        return source_time_s.copy()
    end = float(source_time_s[-1])
    tolerance = 128.0 * np.finfo(float).eps * max(1.0, end)
    count = int(math.floor((end + tolerance) / interval_s))
    targets = np.arange(count + 1, dtype=np.float64) * interval_s
    targets = targets[targets <= end + tolerance]
    targets = np.clip(targets, 0.0, end)
    if targets.size == 0 or end - targets[-1] > tolerance:
        targets = np.append(targets, end)
    else:
        targets[-1] = end
    return np.unique(targets)


def _interpolate_first_axis(
    values: np.ndarray, source_time_s: np.ndarray, target_time_s: np.ndarray
) -> np.ndarray:
    array = np.asarray(values)
    if array.shape[0] != source_time_s.size:
        raise ValueError("time-aligned array has an inconsistent first dimension")
    flattened = np.asarray(array, dtype=np.float64).reshape(source_time_s.size, -1)
    interpolated = np.empty((target_time_s.size, flattened.shape[1]))
    for column in range(flattened.shape[1]):
        interpolated[:, column] = np.interp(
            target_time_s, source_time_s, flattened[:, column]
        )
    return interpolated.reshape((target_time_s.size, *array.shape[1:]))


def _restore_user_output_sampling(
    dense: SimulationResult, config: SimulationConfig
) -> SimulationResult:
    """Keep dense event evidence while returning ordinary arrays at user cadence."""

    source_time = np.asarray(dense.time_s, dtype=np.float64)
    target_time = _output_targets(source_time, config.run.output_interval_s)
    observables = {
        key: _interpolate_first_axis(value, source_time, target_time)
        for key, value in dense.observables.items()
    }
    fields = dict(dense.fields)
    frame_time = fields.get("frame_time_s")
    if frame_time is not None:
        frame_time_array = np.asarray(frame_time, dtype=np.float64)
        if frame_time_array.shape == source_time.shape and np.allclose(
            frame_time_array, source_time, rtol=0.0, atol=1.0e-14
        ):
            for key, value in tuple(fields.items()):
                array = np.asarray(value)
                if key != "frame_time_s" and array.ndim and array.shape[0] == source_time.size:
                    fields[key] = _interpolate_first_axis(
                        array, source_time, target_time
                    )
            fields["frame_time_s"] = target_time.copy()
    fields["event_evidence_time_s"] = source_time.copy()

    metadata = dict(dense.metadata)
    field_units_raw = metadata.get("field_units", {})
    field_units = (
        dict(field_units_raw) if isinstance(field_units_raw, Mapping) else {}
    )
    field_units["event_evidence_time_s"] = "s"
    metadata["field_units"] = field_units
    metadata["sampling_contract"] = {
        "event_evidence": "internal integration/output localisation grid",
        "event_evidence_samples": int(source_time.size),
        "event_evidence_nominal_interval_s": float(
            config.forward.integration_time_step_s
        ),
        "ordinary_output": "user requested output grid with linear state interpolation",
        "ordinary_output_samples": int(target_time.size),
        "ordinary_output_interval_s": float(config.run.output_interval_s),
    }
    return SimulationResult(
        dense.backend,
        target_time,
        observables,
        fields,
        dense.events,
        metadata,
        dense.status,
    )


class ForwardStage1Backend(_forward.ForwardTopologyBackend):
    mode = BackendMode.FORWARD
    backend_id = "forward_stage1_predictive"
    display_name = "F1.0 第一阶段预测合同版"

    def run(self, config, control=None, progress=None, **kwargs):
        if config.backend is not BackendMode.FORWARD:
            raise ValueError("ForwardStage1Backend requires forward backend")
        document = config.to_dict()
        document["run"]["output_interval_s"] = min(
            config.run.output_interval_s,
            config.forward.integration_time_step_s,
        )
        dense_config = SimulationConfig.from_dict(document)
        raw = super().run(
            dense_config,
            control=control,
            progress=progress,
            **kwargs,
        )
        canonical = canonicalize_forward(
            raw,
            config,
            event_evidence_time_s=np.asarray(raw.time_s, dtype=np.float64),
        )
        sampled = _restore_user_output_sampling(canonical, config)
        return integrate_camera_measurement(sampled, config)


class HighFidelityStage1Backend(_hf.HighFidelityBackend):
    mode = BackendMode.HIGH_FIDELITY
    backend_id = "high_fidelity_stage1_local_law"
    display_name = "HF3D 第一阶段一致输运局部律"

    @staticmethod
    def _time_step_limits(state, config, grid):
        hf = config.high_fidelity
        fluid = config.fluid
        face_rate = maximum_donor_cell_rate_s_inv(
            state.u_x_face, state.u_y_face, state.u_z_face, grid
        )
        rpm_now, slope_now = _hf._drive_rpm_and_slope(config, state.time_s)
        rpm_trial, _ = _hf._drive_rpm_and_slope(
            config, state.time_s + hf.time_step_s
        )
        omega_bound = max(
            abs(_hf._rpm_to_omega(rpm_now)),
            abs(_hf._rpm_to_omega(rpm_trial)),
        )
        omega_dot = abs(_hf._rpm_to_omega(slope_now))
        wall_rate = omega_bound * grid.radius_m * (1.0 / grid.dx + 1.0 / grid.dy)
        rate_bound = max(face_rate, wall_rate)
        dt_adv = (
            math.inf
            if rate_bound <= np.finfo(float).tiny
            else hf.cfl_limit / rate_bound
        )
        nu = max(
            fluid.liquid_dynamic_viscosity_pa_s / fluid.liquid_density_kg_m3,
            fluid.gas_dynamic_viscosity_pa_s / fluid.gas_density_kg_m3,
        )
        diffusive_rate = 2.0 * nu * (
            1.0 / grid.dx**2 + 1.0 / grid.dy**2 + 1.0 / grid.dz**2
        )
        dt_viscous = 0.45 / diffusive_rate
        spacing = grid.minimum_spacing_m
        dt_capillary = 0.20 * math.sqrt(
            fluid.gas_density_kg_m3
            * spacing**3
            / (math.pi * fluid.surface_tension_n_m)
        )
        ux, uy, uz = grid.faces_to_cells(
            state.u_x_face, state.u_y_face, state.u_z_face
        )
        speed = float(
            np.max(np.sqrt(ux * ux + uy * uy + uz * uz)[grid.active_mask])
        )
        source = (
            fluid.gravity_m_s2
            + omega_bound**2 * grid.radius_m
            + omega_dot * grid.radius_m
            + 2.0 * omega_bound * speed
        )
        dt_source = math.sqrt(
            hf.cfl_limit * spacing / max(source, np.finfo(float).tiny)
        )
        dt_rotation = (
            math.inf
            if omega_bound <= np.finfo(float).tiny
            else hf.maximum_rotation_increment_rad / omega_bound
        )
        stable = min(
            hf.time_step_s,
            dt_adv,
            dt_viscous,
            dt_capillary,
            dt_source,
            dt_rotation,
        )
        return stable, {
            "advective_face_outflow_rate_s_inv": rate_bound,
            "viscous_rate_s_inv": diffusive_rate,
            "capillary_dt_s": dt_capillary,
            "source_dt_s": dt_source,
            "rotation_dt_s": dt_rotation,
        }

    def advance_one_step(
        self,
        state,
        config,
        grid,
        projection,
        dt_s,
        *,
        expected_liquid_volume_m3,
    ):
        hf = config.high_fidelity
        fluid = config.fluid
        old_phase = state.phase
        old_ux, old_uy, old_uz = grid.faces_to_cells(
            state.u_x_face, state.u_y_face, state.u_z_face
        )
        midpoint = state.time_s + 0.5 * dt_s
        rpm_mid, rpm_slope = _hf._drive_rpm_and_slope(config, midpoint)
        omega_mid = _hf._rpm_to_omega(rpm_mid)
        omega_dot = _hf._rpm_to_omega(rpm_slope)
        top_x = omega_mid * grid.y_cell[:, :, -1]
        top_y = -omega_mid * grid.x_cell[:, :, -1]
        (
            phase,
            new_density,
            ux,
            uy,
            uz,
            transport,
        ) = advect_phase_density_momentum_consistently(
            old_phase,
            old_ux,
            old_uy,
            old_uz,
            state.u_x_face,
            state.u_y_face,
            state.u_z_face,
            grid,
            dt_s,
            liquid_density_kg_m3=fluid.liquid_density_kg_m3,
            gas_density_kg_m3=fluid.gas_density_kg_m3,
            top_inflow_phase=0.0,
            top_inflow_u_x_m_s=top_x,
            top_inflow_u_y_m_s=top_y,
            top_inflow_u_z_m_s=0.0,
        )
        clip_absolute = transport.phase_clip_absolute_volume_m3
        repair_absolute = transport.phase_repair_absolute_volume_m3
        cls_diagnostics: dict[str, float] = {
            "phase_conservation_residual_m3": 0.0,
            "density_conservation_residual_kg": 0.0,
            "momentum_remap_x_kg_m_s": 0.0,
            "momentum_remap_y_kg_m_s": 0.0,
            "momentum_remap_z_kg_m_s": 0.0,
            "momentum_remap_absolute_kg_m_s": 0.0,
        }
        next_step = state.step + 1
        if (
            hf.interface_compression_interval > 0
            and next_step % hf.interface_compression_interval == 0
            and hf.interface_compression_pseudo_cfl > 0.0
        ):
            (
                phase,
                new_density,
                ux,
                uy,
                uz,
                cls_diagnostics,
            ) = compress_phase_density_momentum_consistently(
                phase,
                new_density,
                ux,
                uy,
                uz,
                grid,
                hf.interface_thickness_cells * grid.minimum_spacing_m,
                pseudo_cfl=hf.interface_compression_pseudo_cfl,
                liquid_density_kg_m3=fluid.liquid_density_kg_m3,
                gas_density_kg_m3=fluid.gas_density_kg_m3,
            )
            clip_absolute += cls_diagnostics["clip_absolute_volume_m3"]
            repair_absolute += cls_diagnostics["repair_absolute_volume_m3"]

        viscosity = mixture_property_3d(
            phase,
            fluid.liquid_dynamic_viscosity_pa_s,
            fluid.gas_dynamic_viscosity_pa_s,
        )
        viscous_x, viscous_y, viscous_z, viscous_diagnostics = (
            newtonian_stress_acceleration_3d(
                ux, uy, uz, viscosity, new_density, grid
            )
        )
        surface_x, surface_y, surface_z, _, surface_diagnostics = (
            surface_tension_acceleration_3d(
                phase,
                new_density,
                grid,
                fluid.surface_tension_n_m,
                contact_angles_deg={
                    _hf.SIDE_WALL: hf.side_contact_angle_deg,
                    _hf.FLOOR_WALL: hf.floor_contact_angle_deg,
                    _hf.BAR_WALL: hf.bar_contact_angle_deg,
                },
                smoothing_cells=hf.surface_tension_smoothing_cells,
            )
        )
        ux += dt_s * viscous_x
        uy += dt_s * viscous_y
        uz += dt_s * viscous_z
        rpm_end, _ = _hf._drive_rpm_and_slope(config, state.time_s + dt_s)
        omega_end = _hf._rpm_to_omega(rpm_end)
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
        ux += dt_s * (surface_x + frame_x)
        uy += dt_s * (surface_y + frame_y)
        uz += dt_s * (surface_z + frame_z - fluid.gravity_m_s2)
        u_x_face, u_y_face, u_z_face = grid.cells_to_faces(ux, uy, uz)
        (
            u_x_face,
            u_y_face,
            u_z_face,
            pressure,
            projection_diagnostics,
        ) = projection.project(
            u_x_face,
            u_y_face,
            u_z_face,
            new_density,
            dt_s,
            initial_pressure_pa=state.pressure_pa,
        )
        projected_ux, projected_uy, projected_uz = grid.faces_to_cells(
            u_x_face, u_y_face, u_z_face
        )
        post_cfl = advective_cfl_face_flux(
            u_x_face, u_y_face, u_z_face, grid, dt_s
        )
        if post_cfl > hf.cfl_limit * (1.0 + 1.0e-9):
            raise FloatingPointError("HF3D face-flux CFL exceeds configured limit")
        for name, array in (
            ("phase", phase),
            ("density", new_density),
            ("u_x", projected_ux),
            ("u_y", projected_uy),
            ("u_z", projected_uz),
            ("pressure", pressure),
        ):
            if not np.isfinite(np.asarray(array)[grid.active_mask]).all():
                raise FloatingPointError(f"HF3D {name} became non-finite")

        liquid_volume = grid.integrate(phase)
        expected_after = (
            expected_liquid_volume_m3
            - dt_s * transport.phase_top_outward_flux_m3_s
        )
        lab_x, lab_y, lab_z = rotating_to_laboratory_velocity_3d(
            projected_ux, projected_uy, projected_uz, grid, omega_end
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
        topology = topology_diagnostics_widest_path_3d(
            phase,
            grid,
            interface_thickness_m=(
                hf.interface_thickness_cells * grid.minimum_spacing_m
            ),
            minimum_resolved_throat_cells=hf.minimum_resolved_throat_cells,
        )
        state.time_s += dt_s
        state.step = next_step
        state.bar_angle_rad = _hf._integrated_bar_angle_rad(config, state.time_s)
        state.u_x_face = u_x_face
        state.u_y_face = u_y_face
        state.u_z_face = u_z_face
        state.phase = phase
        state.pressure_pa = pressure

        nu = max(
            fluid.liquid_dynamic_viscosity_pa_s / fluid.liquid_density_kg_m3,
            fluid.gas_dynamic_viscosity_pa_s / fluid.gas_density_kg_m3,
        )
        viscous_cfl = 2.0 * nu * dt_s * (
            1.0 / grid.dx**2 + 1.0 / grid.dy**2 + 1.0 / grid.dz**2
        )
        capillary_dt = 0.20 * math.sqrt(
            fluid.gas_density_kg_m3
            * grid.minimum_spacing_m**3
            / (math.pi * fluid.surface_tension_n_m)
        )
        return _hf.HF3DStepDiagnostics(
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
            divergence_after_s_inv=(
                projection_diagnostics.divergence_after_s_inv
            ),
            pressure_relative_residual=(
                projection_diagnostics.pressure_relative_residual
            ),
            pressure_iterations=projection_diagnostics.iterations,
            liquid_volume_m3=liquid_volume,
            expected_liquid_volume_m3=expected_after,
            liquid_volume_balance_error_m3=liquid_volume - expected_after,
            phase_top_outward_flux_m3_s=(
                transport.phase_top_outward_flux_m3_s
            ),
            phase_clip_absolute_volume_m3=clip_absolute,
            phase_repair_absolute_volume_m3=repair_absolute,
            kinetic_energy_lab_j=kinetic_lab,
            kinetic_energy_rotating_j=kinetic_rotating,
            angular_momentum_z_kg_m2_s=angular_momentum,
            viscous_dissipation_w=viscous_diagnostics.viscous_dissipation_w,
            maximum_surface_tension_acceleration_m_s2=(
                surface_diagnostics.maximum_acceleration_m_s2
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
            phase_conservation_residual_m3=(
                transport.phase_conservation_residual_m3
            ),
            density_conservation_residual_kg=(
                transport.density_conservation_residual_kg
            ),
            momentum_remap_x_kg_m_s=transport.momentum_remap_x_kg_m_s,
            momentum_remap_y_kg_m_s=transport.momentum_remap_y_kg_m_s,
            momentum_remap_z_kg_m_s=transport.momentum_remap_z_kg_m_s,
            momentum_remap_absolute_kg_m_s=(
                transport.momentum_remap_absolute_kg_m_s
            ),
            cls_phase_conservation_residual_m3=cls_diagnostics[
                "phase_conservation_residual_m3"
            ],
            cls_density_conservation_residual_kg=cls_diagnostics[
                "density_conservation_residual_kg"
            ],
            cls_momentum_remap_x_kg_m_s=cls_diagnostics[
                "momentum_remap_x_kg_m_s"
            ],
            cls_momentum_remap_y_kg_m_s=cls_diagnostics[
                "momentum_remap_y_kg_m_s"
            ],
            cls_momentum_remap_z_kg_m_s=cls_diagnostics[
                "momentum_remap_z_kg_m_s"
            ],
            cls_momentum_remap_absolute_kg_m_s=cls_diagnostics[
                "momentum_remap_absolute_kg_m_s"
            ],
        )

    def run(self, config, control=None, progress=None):
        old_topology = _hf.topology_diagnostics_3d
        old_cfl = _hf.advective_cfl
        _hf.topology_diagnostics_3d = topology_diagnostics_widest_path_3d
        _hf.advective_cfl = advective_cfl_face_flux
        try:
            raw = super().run(config, control=control, progress=progress)
        finally:
            _hf.topology_diagnostics_3d = old_topology
            _hf.advective_cfl = old_cfl
        canonical = canonicalize_hf3d(
            raw,
            config,
            event_evidence_time_s=np.asarray(
                raw.fields.get("diagnostic_step_time_s", raw.time_s),
                dtype=np.float64,
            ),
        )
        measured = integrate_camera_measurement(canonical, config)
        metadata = dict(measured.metadata)
        metadata["stage1_numeric_changes"] = {
            "cfl": "donor-cell face outflow coefficient",
            "transport": "shared phase-density-momentum face flux",
            "transport_diagnostics": "persisted per step and summarized",
            "cls": "density and momentum remap persisted per compression step",
            "topology": "3D widest-path bottleneck",
            "strict_warning_policy": "inactive zero-density cells are never divided",
            "capillary_balance": (
                "STILL_REQUIRES_STATIC_DROP_AND_CAPILLARY_WAVE_VALIDATION"
            ),
        }
        return SimulationResult(
            measured.backend,
            measured.time_s,
            measured.observables,
            measured.fields,
            measured.events,
            metadata,
            measured.status,
        )


ForwardTheoryBackend = ForwardStage1Backend
HighFidelityBackend = HighFidelityStage1Backend


__all__ = [
    "ForwardStage1Backend",
    "ForwardTheoryBackend",
    "HighFidelityBackend",
    "HighFidelityStage1Backend",
]
