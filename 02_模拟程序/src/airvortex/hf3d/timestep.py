"""Typed HF3D time-step proposal and post-projection retry controller."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from airvortex.core import SimulationConfig
from airvortex.hf3d.drive import drive_rpm_and_slope, rpm_to_omega
from airvortex.hf3d.types import HF3DState
from airvortex.numerics.cartesian3d import ImmersedCartesianGrid


@dataclass(frozen=True, slots=True)
class TimeStepProposal:
    """Stable proposed step with named limit identities and diagnostics."""

    stable_dt_s: float
    selected_limiter: str
    cfl_hard_limit: float
    cfl_controller_target: float
    advective_rate_s_inv: float
    known_source_rate_acceleration_s_inv2: float
    independent_advective_dt_s: float
    independent_source_dt_s: float
    combined_known_source_cfl_dt_s: float
    viscous_rate_s_inv: float
    capillary_dt_s: float
    rotation_dt_s: float

    def legacy_limits_dict(self) -> dict[str, float | str]:
        """Preserve the existing backend diagnostic mapping exactly."""

        return {
            "cfl_hard_limit": self.cfl_hard_limit,
            "cfl_controller_target": self.cfl_controller_target,
            "advective_rate_s_inv": self.advective_rate_s_inv,
            "known_source_rate_acceleration_s_inv2": (
                self.known_source_rate_acceleration_s_inv2
            ),
            "independent_advective_dt_s": self.independent_advective_dt_s,
            "independent_source_dt_s": self.independent_source_dt_s,
            "combined_known_source_cfl_dt_s": (
                self.combined_known_source_cfl_dt_s
            ),
            "viscous_rate_s_inv": self.viscous_rate_s_inv,
            "capillary_dt_s": self.capillary_dt_s,
            "rotation_dt_s": self.rotation_dt_s,
            "selected_limiter": self.selected_limiter,
        }


def propose_time_step(
    state: HF3DState,
    config: SimulationConfig,
    grid: ImmersedCartesianGrid,
) -> TimeStepProposal:
    hf = config.high_fidelity
    fluid = config.fluid
    ux, uy, uz = grid.faces_to_cells(
        state.u_x_face, state.u_y_face, state.u_z_face
    )
    rate = np.abs(ux) / grid.dx + np.abs(uy) / grid.dy + np.abs(uz) / grid.dz
    advective_rate = float(np.max(rate[grid.active_mask]))
    rpm_now, slope_now = drive_rpm_and_slope(config, state.time_s)
    rpm_trial, _ = drive_rpm_and_slope(config, state.time_s + hf.time_step_s)
    omega_bound = max(abs(rpm_to_omega(rpm_now)), abs(rpm_to_omega(rpm_trial)))
    omega_dot = abs(rpm_to_omega(slope_now))
    wall_rate = omega_bound * grid.radius_m * (1.0 / grid.dx + 1.0 / grid.dy)
    rate_bound = max(advective_rate, wall_rate)
    cfl_target = hf.cfl_controller_target_fraction * hf.cfl_limit
    dt_advective = (
        math.inf if rate_bound <= np.finfo(float).tiny else cfl_target / rate_bound
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
    speed_max = float(np.max(np.sqrt(ux * ux + uy * uy + uz * uz)[grid.active_mask]))
    horizontal_source_acceleration_bound = (
        omega_bound * omega_bound * grid.radius_m
        + omega_dot * grid.radius_m
        + 2.0 * omega_bound * speed_max
    )
    source_rate_acceleration = (
        horizontal_source_acceleration_bound * (1.0 / grid.dx + 1.0 / grid.dy)
        + fluid.gravity_m_s2 / grid.dz
    )
    dt_source = math.sqrt(
        cfl_target / max(source_rate_acceleration, np.finfo(float).tiny)
    )
    dt_combined_cfl = 2.0 * cfl_target / (
        rate_bound
        + math.sqrt(
            rate_bound * rate_bound
            + 4.0 * source_rate_acceleration * cfl_target
        )
    )
    dt_rotation = (
        math.inf
        if omega_bound <= np.finfo(float).tiny
        else hf.maximum_rotation_increment_rad / omega_bound
    )
    candidate_limits = {
        "registered_time_step": hf.time_step_s,
        "combined_known_source_cfl": dt_combined_cfl,
        "viscous_stability": dt_viscous,
        "capillary_stability": dt_capillary,
        "rotation_increment": dt_rotation,
    }
    selected_limiter = min(candidate_limits, key=candidate_limits.__getitem__)
    stable = candidate_limits[selected_limiter]
    return TimeStepProposal(
        stable_dt_s=stable,
        selected_limiter=selected_limiter,
        cfl_hard_limit=hf.cfl_limit,
        cfl_controller_target=cfl_target,
        advective_rate_s_inv=rate_bound,
        known_source_rate_acceleration_s_inv2=source_rate_acceleration,
        independent_advective_dt_s=dt_advective,
        independent_source_dt_s=dt_source,
        combined_known_source_cfl_dt_s=dt_combined_cfl,
        viscous_rate_s_inv=diffusion_rate,
        capillary_dt_s=dt_capillary,
        rotation_dt_s=dt_rotation,
    )


def retry_time_step_s(
    attempted_dt_s: float,
    observed_post_cfl: float,
    cfl_hard_limit: float,
    controller_target_fraction: float,
) -> float:
    target = controller_target_fraction * cfl_hard_limit
    proposed = attempted_dt_s * target / observed_post_cfl
    if not math.isfinite(proposed) or proposed <= np.finfo(float).tiny:
        raise FloatingPointError("HF3D CFL retry time step became non-positive")
    if proposed >= attempted_dt_s:
        proposed = math.nextafter(attempted_dt_s, 0.0)
    return proposed


__all__ = ["TimeStepProposal", "propose_time_step", "retry_time_step_s"]
