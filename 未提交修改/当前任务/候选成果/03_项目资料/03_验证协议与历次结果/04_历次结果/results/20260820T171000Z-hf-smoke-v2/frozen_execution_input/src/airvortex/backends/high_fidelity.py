"""Executable H1 axisymmetric colour-fluid HF-preview backend.

This module intentionally follows a physically independent route from the
reduced theory.  It advances an axisymmetric ``r-z`` one-fluid surrogate with
swirl, a diffuse liquid colour function, a fixed-reference-density pressure
projection and an axisymmetrised swept-bar Brinkman forcing.

It is an **HF-preview**, not a production high-density-ratio two-phase CFD
solver.  In particular it cannot quantitatively predict the post-contact air
channel, wetting, breakup, real three-dimensional bar-tip wakes, or an absolute
critical rpm.  Those boundaries are repeated in ``capabilities`` and every
result's metadata so they survive archival/export.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Callable

import numpy as np
from numpy.typing import NDArray

from airvortex.core import (
    BackendMode,
    Control,
    Event,
    Progress,
    RunStatus,
    SimulationConfig,
    SimulationResult,
)
from airvortex.numerics import (
    AxisymmetricGrid,
    AxisymmetricProjection,
    conservative_advect_scalar,
    conservative_cls_compress,
    gas_connection_diagnostics,
    impose_cell_velocity_boundaries,
    initialise_flat_phase,
    mixture_property,
    projected_color_gradient_csf,
    semi_lagrangian_advect,
    surface_profile,
)


FloatArray = NDArray[np.float64]
ProgressCallback = Callable[[Progress], None]


MODEL_NAME = (
    "axisymmetric constant-inertia-coefficient Boussinesq-form/color-fluid "
    "diffuse-interface surrogate"
)

ROTATION_INCREMENT_LIMIT_RAD = 0.08
# Pre-registered numerical acceptance conventions, not physical constants.
MAX_SINGLE_STEP_MASS_REPAIR_RELATIVE = 1.0e-8
MAX_CUMULATIVE_MASS_REPAIR_RELATIVE = 1.0e-6


CAPABILITIES = MappingProxyType(
    {
        "fidelity_label": "HF-preview",
        "evidence_level": (
            "H1 qualitative; semiquantitative only after frozen validation"
        ),
        "model": MODEL_NAME,
        "axisymmetric_r_z_with_swirl": True,
        "transient_fields": True,
        "diffuse_interface": True,
        "projected_color_gradient_csf_option": True,
        "strict_balanced_force_csf": False,
        "fail_closed_mass_repair_quality_gate": True,
        "first_connection_candidate": True,
        "connection_candidate_requires_resolved_throat": True,
        "terminates_at_resolved_connection_candidate": True,
        "persistent_air_channel": False,
        "azimuthal_cells_used": False,
        "open_atmospheric_headspace": False,
        "quantitative_absolute_critical_rpm": False,
        "quantitative_post_contact_air_channel": False,
        "quantitative_air_channel_radius": False,
        "resolved_three_dimensional_bar_tip_wake": False,
        "resolved_contact_wetting_hysteresis": False,
    }
)


LIMITATIONS = (
    "HF-preview only: the air/water density ratio is represented by a "
    "constant-inertia-coefficient Boussinesq-form colour-fluid surrogate, not "
    "a controlled small-density-contrast Boussinesq limit.",
    "The real horizontal bar is replaced by an axisymmetric swept-volume "
    "Brinkman forcing; its three-dimensional bar-tip wake and slip require "
    "experimental calibration.",
    "A top-connected gas cell reaching the swept-bar neighbourhood is only a "
    "connection candidate. Post-contact channel dynamics, channel radius, "
    "wetting, breakup, entrainment and hysteresis are outside H1.",
    "Absolute critical rpm is not a quantitative prediction of this preview; "
    "grid/time-step convergence and frozen experimental validation are required.",
    "The computational lid is impermeable and the finite gas headspace is closed; "
    "H1 does not impose an open atmospheric reservoir or gas outflow boundary.",
    "Connection output is an instantaneous resolution-gated candidate only; "
    "H1 does not certify persistence for three bar periods or a stable channel.",
    "Surface tension uses a projected cell-centred colour-gradient CSF surrogate, "
    "not a strict identical-face-gradient balanced-force discretisation; no "
    "quantitative Laplace-pressure or static-drop claim is made.",
)


@dataclass(slots=True)
class HFState:
    """Mutable internal solver state; face and cell conventions are explicit."""

    time_s: float
    step: int
    u_r_face: FloatArray
    u_z_face: FloatArray
    u_theta: FloatArray
    phase: FloatArray
    pressure: FloatArray


@dataclass(frozen=True, slots=True)
class StepDiagnostics:
    dt_s: float
    rpm_equivalent_bar: float
    cfl_advective: float
    cfl_viscous: float
    cfl_capillary: float
    rotation_increment_rad: float
    divergence_before_projection: float
    max_divergence: float
    pressure_relative_residual: float
    pressure_removed_rhs_mean_pa_m2: float
    kinetic_energy_j: float
    phase_raw_mass_error_relative: float
    phase_clip_signed_volume_m3: float
    phase_clip_absolute_volume_m3: float
    phase_mass_repair_signed_volume_m3: float
    phase_mass_repair_absolute_volume_m3: float


class HighFidelityBackend:
    """H1 axisymmetric one-fluid/Boussinesq-form diffuse-interface preview."""

    mode = BackendMode.HIGH_FIDELITY
    backend_id = "axisymmetric_color_fluid_h1"
    display_name = "HF-preview：轴对称含旋流弥散界面"
    capabilities = CAPABILITIES

    def __init__(
        self,
        *,
        enable_surface_tension: bool = True,
        brinkman_relaxation_s: float = 0.020,
        cls_compression_interval: int = 1,
        cls_compression_speed_m_s: float = 0.040,
    ) -> None:
        if not isinstance(enable_surface_tension, bool):
            raise TypeError("enable_surface_tension must be bool")
        if not math.isfinite(brinkman_relaxation_s) or brinkman_relaxation_s <= 0.0:
            raise ValueError("brinkman_relaxation_s must be positive")
        if (
            isinstance(cls_compression_interval, bool)
            or cls_compression_interval < 0
        ):
            raise ValueError("cls_compression_interval must be an integer >= 0")
        if (
            not math.isfinite(cls_compression_speed_m_s)
            or cls_compression_speed_m_s <= 0.0
        ):
            raise ValueError("cls_compression_speed_m_s must be positive")
        self.enable_surface_tension = enable_surface_tension
        self.brinkman_relaxation_s = float(brinkman_relaxation_s)
        self.cls_compression_interval = int(cls_compression_interval)
        self.cls_compression_speed_m_s = float(cls_compression_speed_m_s)

    @staticmethod
    def _validate_config(config: SimulationConfig) -> None:
        if not isinstance(config, SimulationConfig):
            raise TypeError("config must be a SimulationConfig")
        if config.backend is not BackendMode.HIGH_FIDELITY:
            raise ValueError("HighFidelityBackend requires backend='high_fidelity'")

    @staticmethod
    def _domain_height(config: SimulationConfig) -> float:
        # A modest gas headspace lets the colour interface move while retaining
        # the user-facing axial-cell budget.
        return 1.25 * config.container.liquid_depth_m

    def make_grid(self, config: SimulationConfig) -> AxisymmetricGrid:
        self._validate_config(config)
        return AxisymmetricGrid(
            radial_cells=config.high_fidelity.radial_cells,
            axial_cells=config.high_fidelity.axial_cells,
            radius_m=config.container.radius_m,
            height_m=self._domain_height(config),
        )

    @staticmethod
    def _drive_rpm(config: SimulationConfig, time_s: float) -> float:
        drive = config.drive
        if drive.ramp_duration_s <= 0.0:
            unsigned = drive.target_rpm
        else:
            fraction = min(max(time_s / drive.ramp_duration_s, 0.0), 1.0)
            unsigned = drive.initial_rpm + fraction * (
                drive.target_rpm - drive.initial_rpm
            )
        return float(drive.rotation_direction * unsigned)

    @staticmethod
    def _bar_geometry(config: SimulationConfig) -> tuple[float, float]:
        centre = config.stir_bar.bottom_clearance_m + config.stir_bar.radius_m
        top = config.stir_bar.bottom_clearance_m + 2.0 * config.stir_bar.radius_m
        return centre, top

    @staticmethod
    def _brinkman_mask(
        config: SimulationConfig, grid: AxisymmetricGrid, phase: FloatArray
    ) -> FloatArray:
        centre_z, _ = HighFidelityBackend._bar_geometry(config)
        rr = grid.r_centres[:, None]
        zz = grid.z_centres[None, :]
        smoothing = 0.75 * min(grid.dr, grid.dz)
        radial = 0.5 * (
            1.0
            - np.tanh((rr - config.stir_bar.half_length_m) / smoothing)
        )
        axial = 0.5 * (
            1.0
            - np.tanh(
                (np.abs(zz - centre_z) - config.stir_bar.radius_m) / smoothing
            )
        )
        # The effective solid acts on liquid only.  This is one of the explicit
        # colour-fluid approximations, not a resolved gas/bar contact model.
        return radial * axial * np.clip(phase, 0.0, 1.0)

    def initialise_state(
        self, config: SimulationConfig, grid: AxisymmetricGrid | None = None
    ) -> HFState:
        self._validate_config(config)
        g = self.make_grid(config) if grid is None else grid
        thickness = config.high_fidelity.interface_thickness_cells * min(g.dr, g.dz)
        phase = initialise_flat_phase(
            g, config.container.liquid_depth_m, thickness
        )
        return HFState(
            time_s=0.0,
            step=0,
            u_r_face=np.zeros((g.nr + 1, g.nz), dtype=np.float64),
            u_z_face=np.zeros((g.nr, g.nz + 1), dtype=np.float64),
            u_theta=np.zeros(g.shape, dtype=np.float64),
            phase=phase,
            pressure=np.zeros(g.shape, dtype=np.float64),
        )

    @staticmethod
    def _time_step_limits(
        state: HFState, config: SimulationConfig, grid: AxisymmetricGrid
    ) -> tuple[float, float, float, float, float]:
        ur_c, uz_c = grid.faces_to_cells(state.u_r_face, state.u_z_face)
        current_advective_rate = float(
            np.max(np.abs(ur_c)) / grid.dr + np.max(np.abs(uz_c)) / grid.dz
        )
        max_kinematic_viscosity = max(
            config.fluid.liquid_dynamic_viscosity_pa_s,
            config.fluid.gas_dynamic_viscosity_pa_s,
        ) / config.fluid.liquid_density_kg_m3
        diffusion_rate = 2.0 * max_kinematic_viscosity * (
            1.0 / grid.dr**2 + 1.0 / grid.dz**2
        )
        dt_viscous = 0.45 / max(diffusion_rate, np.finfo(float).tiny)
        if config.fluid.surface_tension_n_m > 0.0:
            h = min(grid.dr, grid.dz)
            dt_capillary = 0.25 * math.sqrt(
                config.fluid.liquid_density_kg_m3
                * h**3
                / (math.pi * config.fluid.surface_tension_n_m)
            )
        else:
            dt_capillary = math.inf
        rpm_now = HighFidelityBackend._drive_rpm(config, state.time_s)
        rpm_end = HighFidelityBackend._drive_rpm(
            config, state.time_s + config.high_fidelity.time_step_s
        )
        omega = 2.0 * math.pi * max(abs(rpm_now), abs(rpm_end)) / 60.0
        current_centrifugal = float(
            np.max(
                np.clip(state.phase, 0.0, 1.0)
                * state.u_theta**2
                / grid.r_centres[:, None]
            )
        )
        target_centrifugal = omega**2 * min(
            config.stir_bar.half_length_m, grid.radius_m
        )
        centrifugal_acceleration_bound = max(
            current_centrifugal, target_centrifugal
        )
        # Source-aware predictor prevents a large swirl impulse from violating
        # the phase-transport CFL before the next adaptive step can react.
        advective_rate = current_advective_rate + (
            config.high_fidelity.time_step_s
            * centrifugal_acceleration_bound
            / grid.dr
        )
        dt_advective = (
            math.inf
            if advective_rate <= np.finfo(float).tiny
            else config.high_fidelity.cfl_limit / advective_rate
        )
        dt_rotation = (
            math.inf
            if omega <= np.finfo(float).tiny
            else ROTATION_INCREMENT_LIMIT_RAD / omega
        )
        stable = min(
            config.high_fidelity.time_step_s,
            dt_advective,
            dt_viscous,
            dt_capillary,
            dt_rotation,
        )
        return stable, advective_rate, diffusion_rate, dt_capillary, dt_rotation

    def advance_one_step(
        self,
        state: HFState,
        config: SimulationConfig,
        grid: AxisymmetricGrid,
        projection: AxisymmetricProjection,
        dt: float,
        *,
        initial_liquid_volume: float,
    ) -> StepDiagnostics:
        """Advance one bounded step and mutate ``state`` in-place."""

        fluid = config.fluid
        rho_ref = fluid.liquid_density_kg_m3
        ur_c, uz_c = grid.faces_to_cells(state.u_r_face, state.u_z_face)

        ur_star = semi_lagrangian_advect(
            ur_c, ur_c, uz_c, grid, dt, radial_parity="odd"
        )
        uz_star = semi_lagrangian_advect(uz_c, ur_c, uz_c, grid, dt)
        utheta_star = semi_lagrangian_advect(
            state.u_theta, ur_c, uz_c, grid, dt, radial_parity="odd"
        )

        liquid_weight = np.clip(state.phase, 0.0, 1.0)
        inverse_radius = 1.0 / grid.r_centres[:, None]
        # Axisymmetric swirl geometry.  The liquid weighting is explicit: this
        # H1 surrogate must not make an equal-density fictitious gas co-rotate.
        ur_star += dt * liquid_weight * state.u_theta**2 * inverse_radius
        utheta_star -= (
            dt * liquid_weight * ur_c * state.u_theta * inverse_radius
        )

        dynamic_viscosity = mixture_property(
            state.phase,
            fluid.liquid_dynamic_viscosity_pa_s,
            fluid.gas_dynamic_viscosity_pa_s,
        )
        kinematic_surrogate = dynamic_viscosity / rho_ref
        ur_star += dt * kinematic_surrogate * grid.scalar_laplacian(
            ur_c, vector_component=True
        )
        uz_star += dt * kinematic_surrogate * grid.scalar_laplacian(uz_c)
        utheta_star += dt * kinematic_surrogate * grid.scalar_laplacian(
            state.u_theta, vector_component=True
        )

        impose_cell_velocity_boundaries(ur_star, uz_star, utheta_star)

        # Relax transported momentum before adding hydrostatic/capillary body
        # forces.  Damping the gravity increment itself would create a spurious
        # circulation around a motionless immersed bar.
        rpm = self._drive_rpm(config, state.time_s + dt)
        omega = 2.0 * math.pi * rpm / 60.0
        mask = self._brinkman_mask(config, grid, state.phase)
        decay = np.exp(-mask * dt / self.brinkman_relaxation_s)
        ur_star *= decay
        uz_star *= decay
        target_swirl = omega * grid.r_centres[:, None]
        utheta_star = target_swirl + (utheta_star - target_swirl) * decay

        density_contrast = (
            fluid.liquid_density_kg_m3 - fluid.gas_density_kg_m3
        ) / rho_ref
        # Reference hydrostatic acceleration is absorbed into pressure; only
        # the colour-weighted density contrast appears explicitly.
        uz_star -= dt * density_contrast * liquid_weight * fluid.gravity_m_s2

        if self.enable_surface_tension:
            csf_r, csf_z, _ = projected_color_gradient_csf(
                state.phase,
                grid,
                fluid.surface_tension_n_m,
                rho_ref,
            )
            ur_star += dt * csf_r
            uz_star += dt * csf_z
        # Swirl is tangential and is not altered by pressure projection.
        utheta_star[-1, :] = 0.0
        utheta_star[:, 0] = 0.0
        utheta_star[:, -1] = 0.0

        ur_face, uz_face = grid.cells_to_faces(ur_star, uz_star)
        ur_face, uz_face, pressure, projection_diagnostics = projection.project(
            ur_face, uz_face, dt, rho_ref
        )
        if (
            projection_diagnostics.pressure_relative_residual
            > config.high_fidelity.pressure_relative_tolerance
        ):
            raise FloatingPointError(
                "pressure projection relative residual "
                f"{projection_diagnostics.pressure_relative_residual:.3e} exceeds "
                "configured tolerance "
                f"{config.high_fidelity.pressure_relative_tolerance:.3e}"
            )

        advective_cfl = dt * (
            float(np.max(np.abs(ur_face))) / grid.dr
            + float(np.max(np.abs(uz_face))) / grid.dz
        )
        if advective_cfl > config.high_fidelity.cfl_limit * (1.0 + 1.0e-10):
            raise FloatingPointError(
                f"post-source advective CFL {advective_cfl:.3e} exceeds "
                f"configured limit {config.high_fidelity.cfl_limit:.3e}"
            )
        updated_phase, advection_diagnostics = conservative_advect_scalar(
            state.phase,
            ur_face,
            uz_face,
            grid,
            dt,
            enforce_bounds=True,
            return_diagnostics=True,
        )
        next_step = state.step + 1
        clip_signed = advection_diagnostics.clip_signed_integral
        clip_absolute = advection_diagnostics.clip_absolute_integral
        repair_signed = advection_diagnostics.mass_repair_signed_integral
        repair_absolute = advection_diagnostics.mass_repair_absolute_integral
        raw_mass_errors = [
            (advection_diagnostics.raw_integral - initial_liquid_volume)
            / initial_liquid_volume
        ]
        if (
            self.cls_compression_interval > 0
            and next_step % self.cls_compression_interval == 0
            and advective_cfl > 1.0e-8
        ):
            thickness = (
                config.high_fidelity.interface_thickness_cells
                * min(grid.dr, grid.dz)
            )
            updated_phase, cls_diagnostics = conservative_cls_compress(
                updated_phase,
                grid,
                thickness,
                pseudo_cfl=min(
                    0.20,
                    self.cls_compression_speed_m_s
                    * dt
                    * self.cls_compression_interval
                    / min(grid.dr, grid.dz),
                ),
                iterations=1,
            )
            clip_signed += cls_diagnostics.clip_signed_integral
            clip_absolute += cls_diagnostics.clip_absolute_integral
            repair_signed += cls_diagnostics.mass_repair_signed_integral
            repair_absolute += cls_diagnostics.mass_repair_absolute_integral
            raw_mass_errors.append(
                (cls_diagnostics.raw_integral - initial_liquid_volume)
                / initial_liquid_volume
            )

        arrays = (ur_face, uz_face, utheta_star, updated_phase, pressure)
        if not all(np.isfinite(array).all() for array in arrays):
            raise FloatingPointError(
                "HF-preview produced a non-finite field; reduce time step or forcing"
            )
        state.time_s += dt
        state.step = next_step
        state.u_r_face = ur_face
        state.u_z_face = uz_face
        state.u_theta = utheta_star
        state.phase = updated_phase
        state.pressure = pressure

        ur_projected_c, uz_projected_c = grid.faces_to_cells(ur_face, uz_face)
        density_surrogate = mixture_property(
            updated_phase, fluid.liquid_density_kg_m3, fluid.gas_density_kg_m3
        )
        speed_squared = (
            ur_projected_c**2 + uz_projected_c**2 + utheta_star**2
        )
        kinetic_energy = 0.5 * grid.integrate(density_surrogate * speed_squared)
        max_kinematic_viscosity = max(
            fluid.liquid_dynamic_viscosity_pa_s,
            fluid.gas_dynamic_viscosity_pa_s,
        ) / rho_ref
        viscous_cfl = 2.0 * max_kinematic_viscosity * dt * (
            1.0 / grid.dr**2 + 1.0 / grid.dz**2
        )
        h = min(grid.dr, grid.dz)
        capillary_dt = 0.25 * math.sqrt(
            rho_ref * h**3 / (math.pi * fluid.surface_tension_n_m)
        )
        return StepDiagnostics(
            dt_s=dt,
            rpm_equivalent_bar=rpm,
            cfl_advective=advective_cfl,
            cfl_viscous=viscous_cfl,
            cfl_capillary=dt / capillary_dt if self.enable_surface_tension else 0.0,
            rotation_increment_rad=abs(omega) * dt,
            divergence_before_projection=projection_diagnostics.divergence_before,
            max_divergence=projection_diagnostics.divergence_after,
            pressure_relative_residual=projection_diagnostics.pressure_relative_residual,
            pressure_removed_rhs_mean_pa_m2=(
                projection_diagnostics.removed_rhs_mean
            ),
            kinetic_energy_j=kinetic_energy,
            phase_raw_mass_error_relative=max(raw_mass_errors, key=abs),
            phase_clip_signed_volume_m3=clip_signed,
            phase_clip_absolute_volume_m3=clip_absolute,
            phase_mass_repair_signed_volume_m3=repair_signed,
            phase_mass_repair_absolute_volume_m3=repair_absolute,
        )

    @staticmethod
    def _surface_observables(
        state: HFState,
        config: SimulationConfig,
        grid: AxisymmetricGrid,
        initial_volume: float,
    ) -> dict[str, float | FloatArray]:
        profile = surface_profile(state.phase, grid)
        outer_start = max(0, int(math.floor(0.8 * grid.nr)))
        far_surface = float(np.mean(profile[outer_start:]))
        centre_region = grid.r_centres <= config.stir_bar.half_length_m
        if not np.any(centre_region):
            centre_region[0] = True
        minimum_surface = float(np.min(profile[centre_region]))
        _, bar_top = HighFidelityBackend._bar_geometry(config)
        liquid_volume = grid.integrate(state.phase)
        return {
            "surface_profile_m": profile,
            "surface_depth": max(0.0, far_surface - minimum_surface),
            "funnel_depth_m": max(0.0, far_surface - minimum_surface),
            "surface_depth_m": max(0.0, far_surface - minimum_surface),
            "surface_min_z": minimum_surface,
            "surface_min_z_m": minimum_surface,
            "min_gap_to_bar": minimum_surface - bar_top,
            "min_gap_to_bar_m": minimum_surface - bar_top,
            "minimum_gap_m": minimum_surface - bar_top,
            "liquid_volume_error": (liquid_volume - initial_volume) / initial_volume,
            "liquid_mass_error_relative": (liquid_volume - initial_volume)
            / initial_volume,
            "liquid_volume_error_m3": liquid_volume - initial_volume,
            "liquid_volume_m3": liquid_volume,
        }

    def run(
        self,
        config: SimulationConfig,
        control: Control | None = None,
        progress: ProgressCallback | None = None,
    ) -> SimulationResult:
        """Run H1 until the configured time or a cooperative stop request."""

        self._validate_config(config)
        controller = control if control is not None else Control()
        grid = self.make_grid(config)
        state = self.initialise_state(config, grid)
        projection = AxisymmetricProjection(grid)
        initial_volume = grid.integrate(state.phase)
        output_interval = config.run.output_interval_s
        end_time = config.run.duration_s
        next_output = output_interval
        time_tolerance = max(
            128.0 * np.finfo(float).eps * max(1.0, end_time),
            1.0e-9 * config.high_fidelity.time_step_s,
        )

        times: list[float] = []
        observable_lists: dict[str, list[float | FloatArray]] = {}
        phase_frames: list[FloatArray] = []
        ur_frames: list[FloatArray] = []
        uz_frames: list[FloatArray] = []
        utheta_frames: list[FloatArray] = []
        pressure_frames: list[FloatArray] = []
        events: list[Event] = []
        connection_reported = False
        max_mass_error = 0.0
        max_divergence = 0.0
        max_advective_cfl = 0.0
        max_pressure_residual = 0.0
        max_removed_rhs_mean = 0.0
        max_rotation_increment = 0.0
        max_raw_mass_error = 0.0
        max_single_repair_relative = 0.0
        cumulative_clip_absolute = 0.0
        cumulative_repair_absolute = 0.0
        diagnostic_step_time: list[float] = []
        diagnostic_raw_mass_error: list[float] = []
        diagnostic_clip_signed: list[float] = []
        diagnostic_clip_absolute: list[float] = []
        diagnostic_repair_signed: list[float] = []
        diagnostic_repair_absolute: list[float] = []
        diagnostic_removed_rhs_mean: list[float] = []
        min_dt = math.inf
        last_diag = StepDiagnostics(
            dt_s=0.0,
            rpm_equivalent_bar=self._drive_rpm(config, 0.0),
            cfl_advective=0.0,
            cfl_viscous=0.0,
            cfl_capillary=0.0,
            rotation_increment_rad=0.0,
            divergence_before_projection=0.0,
            max_divergence=0.0,
            pressure_relative_residual=0.0,
            pressure_removed_rhs_mean_pa_m2=0.0,
            kinetic_energy_j=0.0,
            phase_raw_mass_error_relative=0.0,
            phase_clip_signed_volume_m3=0.0,
            phase_clip_absolute_volume_m3=0.0,
            phase_mass_repair_signed_volume_m3=0.0,
            phase_mass_repair_absolute_volume_m3=0.0,
        )

        def record_frame() -> None:
            nonlocal connection_reported, max_mass_error
            surface = self._surface_observables(
                state, config, grid, initial_volume
            )
            _, bar_top = self._bar_geometry(config)
            interface_thickness = (
                config.high_fidelity.interface_thickness_cells
                * min(grid.dr, grid.dz)
            )
            connection = gas_connection_diagnostics(
                state.phase,
                grid,
                config.stir_bar.half_length_m,
                bar_top,
                interface_thickness_m=interface_thickness,
            )
            connected = connection.resolved_candidate
            frame_values: dict[str, float | FloatArray] = {
                **surface,
                "max_divergence": last_diag.max_divergence,
                "divergence_before_projection": last_diag.divergence_before_projection,
                "cfl_advective": last_diag.cfl_advective,
                "cfl_viscous": last_diag.cfl_viscous,
                "cfl_capillary": last_diag.cfl_capillary,
                "rotation_increment_rad": last_diag.rotation_increment_rad,
                "pressure_residual": last_diag.pressure_relative_residual,
                "pressure_removed_rhs_mean_pa_m2": (
                    last_diag.pressure_removed_rhs_mean_pa_m2
                ),
                "kinetic_energy_j": last_diag.kinetic_energy_j,
                "equivalent_bar_rpm": last_diag.rpm_equivalent_bar,
                "connection_candidate": float(connected),
                "first_contact_proxy": float(connected),
                "connection_one_cell_precursor": float(
                    connection.one_cell_precursor
                ),
                "connection_minimum_throat_cells": float(
                    connection.minimum_throat_cells
                ),
                "connection_minimum_throat_radius_m": (
                    connection.minimum_throat_radius_m
                ),
                "connection_minimum_throat_diameter_m": (
                    connection.minimum_throat_diameter_m
                ),
                "connection_required_throat_cells": float(
                    connection.required_throat_cells
                ),
                "connection_required_throat_radius_m": (
                    connection.required_throat_radius_m
                ),
                "phase_raw_mass_error_relative": (
                    last_diag.phase_raw_mass_error_relative
                ),
                "phase_clip_signed_volume_m3": (
                    last_diag.phase_clip_signed_volume_m3
                ),
                "phase_clip_absolute_volume_m3": (
                    last_diag.phase_clip_absolute_volume_m3
                ),
                "phase_mass_repair_signed_volume_m3": (
                    last_diag.phase_mass_repair_signed_volume_m3
                ),
                "phase_mass_repair_absolute_volume_m3": (
                    last_diag.phase_mass_repair_absolute_volume_m3
                ),
            }
            times.append(state.time_s)
            for name, value in frame_values.items():
                observable_lists.setdefault(name, []).append(
                    value.copy() if isinstance(value, np.ndarray) else float(value)
                )
            phase_frames.append(state.phase.copy())
            ur_c, uz_c = grid.faces_to_cells(state.u_r_face, state.u_z_face)
            ur_frames.append(ur_c)
            uz_frames.append(uz_c)
            utheta_frames.append(state.u_theta.copy())
            pressure_frames.append(state.pressure.copy())
            max_mass_error = max(
                max_mass_error, abs(float(surface["liquid_volume_error"]))
            )
            if connected and not connection_reported:
                events.append(
                    Event(
                        time_s=state.time_s,
                        kind="air_connection_candidate",
                        message=(
                            "Top-connected gas reached the swept-bar neighbourhood; "
                            "HF-preview terminates at this domain boundary and does "
                            "not compute quantitative post-contact physics."
                        ),
                        data={
                            "min_gap_m": float(surface["min_gap_to_bar_m"]),
                            "minimum_throat_cells": (
                                connection.minimum_throat_cells
                            ),
                            "required_throat_cells": (
                                connection.required_throat_cells
                            ),
                            "minimum_throat_radius_m": (
                                connection.minimum_throat_radius_m
                            ),
                            "required_throat_radius_m": (
                                connection.required_throat_radius_m
                            ),
                            "interface_thickness_m": (
                                connection.interface_thickness_m
                            ),
                            "radial_resolution_m": connection.radial_resolution_m,
                            "axial_resolution_m": connection.axial_resolution_m,
                            "quantitative_post_contact_prediction": False,
                            "temporal_classification": (
                                "instantaneous_candidate_only"
                            ),
                            "persistent_for_three_bar_periods": False,
                        },
                    )
                )
                connection_reported = True

        record_frame()
        status = RunStatus.COMPLETED
        while (
            state.time_s < end_time - time_tolerance
            and not connection_reported
        ):
            if not controller.checkpoint():
                status = RunStatus.STOPPED
                events.append(
                    Event(
                        time_s=state.time_s,
                        kind="soft_stop",
                        message=controller.stop_reason,
                    )
                )
                break
            stable_dt, _, _, _, _ = self._time_step_limits(state, config, grid)
            remaining = end_time - state.time_s
            to_output = max(next_output - state.time_s, 0.0)
            dt = min(stable_dt, remaining)
            if to_output > time_tolerance:
                dt = min(dt, to_output)
            if not math.isfinite(dt) or dt <= np.finfo(float).tiny:
                raise FloatingPointError("HF-preview adaptive time step collapsed")
            last_diag = self.advance_one_step(
                state,
                config,
                grid,
                projection,
                dt,
                initial_liquid_volume=initial_volume,
            )
            min_dt = min(min_dt, dt)
            max_divergence = max(max_divergence, last_diag.max_divergence)
            max_advective_cfl = max(max_advective_cfl, last_diag.cfl_advective)
            max_rotation_increment = max(
                max_rotation_increment, last_diag.rotation_increment_rad
            )
            max_raw_mass_error = max(
                max_raw_mass_error,
                abs(last_diag.phase_raw_mass_error_relative),
            )
            cumulative_clip_absolute += (
                last_diag.phase_clip_absolute_volume_m3
            )
            cumulative_repair_absolute += (
                last_diag.phase_mass_repair_absolute_volume_m3
            )
            single_repair_relative = (
                last_diag.phase_mass_repair_absolute_volume_m3 / initial_volume
            )
            cumulative_repair_relative = (
                cumulative_repair_absolute / initial_volume
            )
            max_single_repair_relative = max(
                max_single_repair_relative, single_repair_relative
            )
            if (
                single_repair_relative
                > MAX_SINGLE_STEP_MASS_REPAIR_RELATIVE
                or cumulative_repair_relative
                > MAX_CUMULATIVE_MASS_REPAIR_RELATIVE
            ):
                raise FloatingPointError(
                    "phase mass-repair numerical quality gate failed: "
                    f"single={single_repair_relative:.3e} "
                    f"(limit {MAX_SINGLE_STEP_MASS_REPAIR_RELATIVE:.3e}), "
                    f"cumulative={cumulative_repair_relative:.3e} "
                    f"(limit {MAX_CUMULATIVE_MASS_REPAIR_RELATIVE:.3e})"
                )
            diagnostic_step_time.append(state.time_s)
            diagnostic_raw_mass_error.append(
                last_diag.phase_raw_mass_error_relative
            )
            diagnostic_clip_signed.append(
                last_diag.phase_clip_signed_volume_m3
            )
            diagnostic_clip_absolute.append(
                last_diag.phase_clip_absolute_volume_m3
            )
            diagnostic_repair_signed.append(
                last_diag.phase_mass_repair_signed_volume_m3
            )
            diagnostic_repair_absolute.append(
                last_diag.phase_mass_repair_absolute_volume_m3
            )
            diagnostic_removed_rhs_mean.append(
                last_diag.pressure_removed_rhs_mean_pa_m2
            )
            max_pressure_residual = max(
                max_pressure_residual, last_diag.pressure_relative_residual
            )
            max_removed_rhs_mean = max(
                max_removed_rhs_mean,
                abs(last_diag.pressure_removed_rhs_mean_pa_m2),
            )

            reached_output = state.time_s >= next_output - time_tolerance
            reached_end = state.time_s >= end_time - time_tolerance
            if reached_output and abs(state.time_s - next_output) <= time_tolerance:
                state.time_s = next_output
            if reached_end and abs(state.time_s - end_time) <= time_tolerance:
                state.time_s = end_time
            if reached_output or reached_end:
                record_frame()
                while next_output <= state.time_s + time_tolerance:
                    next_output += output_interval
                if progress is not None:
                    progress(
                        Progress(
                            fraction=min(state.time_s / end_time, 1.0),
                            simulated_time_s=state.time_s,
                            step=state.step,
                            stage=(
                                "domain_limit"
                                if connection_reported
                                else ("running" if not reached_end else "completed")
                            ),
                            message="HF-preview axisymmetric colour-fluid step",
                            metrics={
                                "max_divergence": last_diag.max_divergence,
                                "cfl_advective": last_diag.cfl_advective,
                                "liquid_volume_error": observable_lists[
                                    "liquid_volume_error"
                                ][-1],
                            },
                        )
                    )

        # A stop can occur between scheduled frames; preserve its final state.
        if not times or state.time_s > times[-1] + time_tolerance:
            record_frame()

        observables = {
            name: np.asarray(values, dtype=np.float64)
            for name, values in observable_lists.items()
        }
        fields = {
            "r_m": grid.r_centres,
            "radius_m": grid.r_centres,
            "z_m": grid.z_centres,
            "frame_time_s": np.asarray(times, dtype=np.float64),
            "phase_liquid_fraction": np.stack(phase_frames),
            "u_r_m_s": np.stack(ur_frames),
            "u_z_m_s": np.stack(uz_frames),
            "u_theta_m_s": np.stack(utheta_frames),
            "pressure_projection_pa": np.stack(pressure_frames),
            "diagnostic_step_time_s": np.asarray(
                diagnostic_step_time, dtype=np.float64
            ),
            "diagnostic_phase_raw_mass_error_relative": np.asarray(
                diagnostic_raw_mass_error, dtype=np.float64
            ),
            "diagnostic_phase_clip_signed_volume_m3": np.asarray(
                diagnostic_clip_signed, dtype=np.float64
            ),
            "diagnostic_phase_clip_absolute_volume_m3": np.asarray(
                diagnostic_clip_absolute, dtype=np.float64
            ),
            "diagnostic_phase_mass_repair_signed_volume_m3": np.asarray(
                diagnostic_repair_signed, dtype=np.float64
            ),
            "diagnostic_phase_mass_repair_absolute_volume_m3": np.asarray(
                diagnostic_repair_absolute, dtype=np.float64
            ),
            "diagnostic_pressure_removed_rhs_mean_pa_m2": np.asarray(
                diagnostic_removed_rhs_mean, dtype=np.float64
            ),
        }
        observable_units = {
            "surface_profile_m": "m",
            "surface_depth": "m",
            "funnel_depth_m": "m",
            "surface_depth_m": "m",
            "surface_min_z": "m",
            "surface_min_z_m": "m",
            "min_gap_to_bar": "m",
            "min_gap_to_bar_m": "m",
            "minimum_gap_m": "m",
            "liquid_volume_error": "1",
            "liquid_mass_error_relative": "1",
            "liquid_volume_error_m3": "m^3",
            "liquid_volume_m3": "m^3",
            "max_divergence": "s^-1",
            "divergence_before_projection": "s^-1",
            "cfl_advective": "1",
            "cfl_viscous": "1",
            "cfl_capillary": "1",
            "rotation_increment_rad": "rad",
            "pressure_residual": "1",
            "pressure_removed_rhs_mean_pa_m2": "Pa/m^2",
            "kinetic_energy_j": "J",
            "equivalent_bar_rpm": "rpm",
            "connection_candidate": "1",
            "first_contact_proxy": "1",
            "connection_one_cell_precursor": "1",
            "connection_minimum_throat_cells": "1",
            "connection_minimum_throat_radius_m": "m",
            "connection_minimum_throat_diameter_m": "m",
            "connection_required_throat_cells": "1",
            "connection_required_throat_radius_m": "m",
            "phase_raw_mass_error_relative": "1",
            "phase_clip_signed_volume_m3": "m^3",
            "phase_clip_absolute_volume_m3": "m^3",
            "phase_mass_repair_signed_volume_m3": "m^3",
            "phase_mass_repair_absolute_volume_m3": "m^3",
        }
        metadata = {
            "backend_id": self.backend_id,
            "fidelity_label": "HF-preview",
            "evidence_level": (
                "H1 qualitative; semiquantitative only after frozen validation"
            ),
            "model": MODEL_NAME,
            "observable_units": observable_units,
            "field_units": {
                "r_m": "m",
                "radius_m": "m",
                "z_m": "m",
                "frame_time_s": "s",
                "phase_liquid_fraction": "1",
                "u_r_m_s": "m/s",
                "u_z_m_s": "m/s",
                "u_theta_m_s": "m/s",
                "pressure_projection_pa": "Pa",
                "diagnostic_step_time_s": "s",
                "diagnostic_phase_raw_mass_error_relative": "1",
                "diagnostic_phase_clip_signed_volume_m3": "m^3",
                "diagnostic_phase_clip_absolute_volume_m3": "m^3",
                "diagnostic_phase_mass_repair_signed_volume_m3": "m^3",
                "diagnostic_phase_mass_repair_absolute_volume_m3": "m^3",
                "diagnostic_pressure_removed_rhs_mean_pa_m2": "Pa/m^2",
            },
            "capabilities": dict(self.capabilities),
            "limitations": list(LIMITATIONS),
            "boundary_conditions": {
                "axis": "axisymmetric parity",
                "outer_wall": "impermeable; preview no-slip damping",
                "bottom": "impermeable; swept-bar Brinkman region",
                "top": "impermeable closed lid",
                "headspace": "finite closed gas headspace; not an open atmosphere",
            },
            "diagnostics": {
                "pressure_solver": "fixed axisymmetric MAC Laplacian + scipy.sparse.splu",
                "pressure_factorizations": projection.factorization_count,
                "phase_transport": "conservative axisymmetric donor-cell finite volume",
                "interface_model": (
                    "conservative donor-cell liquid colour (CLS compression disabled)"
                    if self.cls_compression_interval == 0
                    else "conservative donor-cell liquid colour + conservative CLS compression/diffusion"
                ),
                "cls_compression_interval": self.cls_compression_interval,
                "cls_compression_speed_m_s": self.cls_compression_speed_m_s,
                "brinkman_forcing": "axisymmetric swept-bar azimuthal relaxation, liquid weighted",
                "brinkman_relaxation_s": self.brinkman_relaxation_s,
                "surface_tension_model": (
                    "projected colour-gradient CSF surrogate; not strict balanced-force"
                    if self.enable_surface_tension
                    else "disabled"
                ),
                "constant_reference_density_kg_m3": config.fluid.liquid_density_kg_m3,
                "configured_azimuthal_cells": config.high_fidelity.azimuthal_cells,
                "azimuthal_cells_used": False,
                "radial_cells_used": grid.nr,
                "axial_cells_used": grid.nz,
                "completed_steps": state.step,
                "simulated_duration_s": state.time_s,
                "minimum_time_step_s": 0.0 if not math.isfinite(min_dt) else min_dt,
                "maximum_liquid_volume_error": max_mass_error,
                "maximum_projected_divergence_s-1": max_divergence,
                "maximum_advective_cfl": max_advective_cfl,
                "maximum_rotation_increment_rad": max_rotation_increment,
                "rotation_increment_limit_rad": ROTATION_INCREMENT_LIMIT_RAD,
                "maximum_pressure_relative_residual": max_pressure_residual,
                "maximum_absolute_pressure_removed_rhs_mean_pa_m2": (
                    max_removed_rhs_mean
                ),
                "pressure_relative_tolerance_enforced": (
                    config.high_fidelity.pressure_relative_tolerance
                ),
                "pressure_max_iterations": {
                    "configured": config.high_fidelity.pressure_max_iterations,
                    "status": "reserved_not_applicable_to_direct_splu",
                },
                "maximum_raw_phase_mass_error_relative": max_raw_mass_error,
                "cumulative_phase_clip_absolute_volume_m3": (
                    cumulative_clip_absolute
                ),
                "cumulative_phase_mass_repair_absolute_volume_m3": (
                    cumulative_repair_absolute
                ),
                "maximum_single_step_mass_repair_relative": (
                    max_single_repair_relative
                ),
                "cumulative_mass_repair_relative": (
                    cumulative_repair_absolute / initial_volume
                ),
                "numerical_quality_gate": {
                    "pass": True,
                    "kind": "pre_registered_numerical_acceptance_convention",
                    "maximum_single_step_mass_repair_relative": (
                        MAX_SINGLE_STEP_MASS_REPAIR_RELATIVE
                    ),
                    "maximum_cumulative_mass_repair_relative": (
                        MAX_CUMULATIVE_MASS_REPAIR_RELATIVE
                    ),
                    "failure_policy": "fail_closed_with_FloatingPointError",
                },
                "quality_gate_pass": True,
                "connection_candidate_observed": connection_reported,
                "termination_reason": (
                    "resolved_instantaneous_connection_candidate_domain_limit"
                    if connection_reported
                    else (
                        "soft_stop"
                        if status is RunStatus.STOPPED
                        else "configured_duration_reached"
                    )
                ),
                "connection_candidate_criterion": (
                    "instantaneous top-connected central gas path with "
                    "r_throat >= max(2*interface_thickness, 4*max(dr,dz))"
                ),
                "persistent_for_three_bar_periods_assessed": False,
                "quantitative_post_contact_air_channel": False,
                "quantitative_absolute_critical_rpm": False,
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


# Names used by design notes and early integrations remain small aliases rather
# than duplicate implementations.
AxisymmetricFreeSurfaceBackend = HighFidelityBackend
AxisymFreeSurfaceNSBackend = HighFidelityBackend


__all__ = [
    "AxisymFreeSurfaceNSBackend",
    "AxisymmetricFreeSurfaceBackend",
    "CAPABILITIES",
    "HFState",
    "HighFidelityBackend",
    "LIMITATIONS",
    "MODEL_NAME",
    "StepDiagnostics",
]
