"""State, diagnostics, exceptions, and stable limiter identities for HF3D."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from airvortex.numerics.cartesian3d import FloatArray
from airvortex.numerics.interface3d import Topology3DDiagnostics


TIME_STEP_LIMITER_CODES = MappingProxyType(
    {
        "registered_time_step": 0,
        "combined_known_source_cfl": 1,
        "viscous_stability": 2,
        "capillary_stability": 3,
        "rotation_increment": 4,
        "output_alignment": 5,
        "post_projection_cfl_retry": 6,
        "direct_call": 7,
        "initial_state": 8,
    }
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
    initial_attempt_dt_s: float
    time_step_limiter: str
    cfl_retry_count: int
    starting_advective_cfl: float
    pre_source_advective_cfl: float
    pre_projection_advective_cfl: float
    maximum_rejected_post_projection_cfl: float
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
    maximum_momentum_advection_acceleration_m_s2: float
    maximum_viscous_acceleration_m_s2: float
    maximum_boundary_acceleration_m_s2: float
    maximum_rotating_frame_acceleration_m_s2: float
    gravity_acceleration_m_s2: float
    maximum_pressure_projection_acceleration_m_s2: float
    boundary_rotating_energy_change_j: float
    topology: Topology3DDiagnostics


@dataclass(frozen=True, slots=True)
class HF3DRejectedStepDiagnostics:
    step_one_based: int
    state_time_s: float
    attempted_dt_s: float
    starting_advective_cfl: float
    pre_source_advective_cfl: float
    pre_projection_advective_cfl: float
    post_projection_advective_cfl: float
    cfl_hard_limit: float
    pressure_relative_residual: float
    pressure_iterations: int
    maximum_momentum_advection_acceleration_m_s2: float
    maximum_viscous_acceleration_m_s2: float
    maximum_boundary_acceleration_m_s2: float
    maximum_surface_tension_acceleration_m_s2: float
    maximum_rotating_frame_acceleration_m_s2: float
    gravity_acceleration_m_s2: float
    maximum_pressure_projection_acceleration_m_s2: float


class HF3DPostProjectionCFLExceeded(FloatingPointError):
    """A complete trial step was rejected before its state was committed."""

    def __init__(self, diagnostics: HF3DRejectedStepDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__(
            "HF3D post-projection advective CFL "
            f"{diagnostics.post_projection_advective_cfl:.6e} exceeds hard limit "
            f"{diagnostics.cfl_hard_limit:.6e} at step "
            f"{diagnostics.step_one_based}; trial state and pressure history restored"
        )


_TIME_STEP_LIMITER_CODES = TIME_STEP_LIMITER_CODES


__all__ = [
    "HF3DPostProjectionCFLExceeded",
    "HF3DRejectedStepDiagnostics",
    "HF3DState",
    "HF3DStepDiagnostics",
    "TIME_STEP_LIMITER_CODES",
]
