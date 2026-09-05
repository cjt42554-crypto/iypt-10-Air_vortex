r"""Conditional AV-FWD-SH-v1 pressure--meridional-Stokes backend.

This implementation solves the instantaneous axisymmetric Stokes equations in
a finite flat reference cylinder and advances the free-surface height with the
computed top velocity.  Surface geometry enters through the linearised
gravity--capillary traction.  It is therefore the fixed-domain, small-slope
moving-interface substage of SH, not a nonlinear moving-domain/contact-line
completion and not an alias for the CP mobility model.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray
from scipy.special import j0, jn_zeros

from ..core import (
    BackendMode,
    Control,
    Event,
    Progress,
    ProgressCallback,
    QuantitativeClaimScope,
    QuantitativeValidityStatus,
    RunStatus,
    SimulationConfig,
    SimulationResult,
    make_quantitative_validity,
)
from ..core.stokes_config import STOKES_MODEL_ID
from ..numerics.axisymmetric import AxisymmetricGrid
from .forward_gas import assess_open_surface_gas
from ..numerics.stokes_axisymmetric import (
    AxisymmetricStokesOperator,
    AxisymmetricStokesSolution,
    InertiaDiagnostics,
    body_force_power_w,
    meridional_inertia_diagnostics,
    viscous_dissipation_w,
)


FloatArray = NDArray[np.float64]

IMPLEMENTATION_ID: Final = "axisymmetric-mac-linear-free-surface-sh-v1"
FROZEN_THEORY_SHA256: Final = (
    "4698b016a4a27110b8e147fe99d216a2b8589ffd96af26c4b5888a39c15a39b7"
)


def _theory_identity() -> tuple[str, str]:
    source = (
        Path(__file__).resolve().parents[4]
        / "01_最终理论"
        / "01_前向预测理论.md"
    )
    if source.is_file():
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return digest, "workspace_source"
    return FROZEN_THEORY_SHA256, "package_frozen_identity"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _drive_omega_rad_s(config: SimulationConfig, time_s: float) -> float:
    if config.drive.actual_speed_schedule_time_s:
        rpm = float(
            np.interp(
                time_s,
                np.asarray(config.drive.actual_speed_schedule_time_s, dtype=np.float64),
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
    return config.drive.rotation_direction * rpm * (2.0 * math.pi / 60.0)


def _output_times(duration_s: float, interval_s: float) -> FloatArray:
    count = int(math.floor(duration_s / interval_s + 1.0e-13))
    result = np.arange(count + 1, dtype=np.float64) * interval_s
    if result[-1] < duration_s - 1.0e-13:
        result = np.append(result, duration_s)
    else:
        result[-1] = duration_s
    return result


def _surface_laplacian_pinned(
    radius_m: FloatArray,
    surface_m: FloatArray,
    vessel_radius_m: float,
) -> FloatArray:
    """Axisymmetric FV Laplacian with axis regularity and a pinned wall height."""

    count = radius_m.size
    dr = vessel_radius_m / count
    faces = np.arange(count + 1, dtype=np.float64) * dr
    gradients = np.zeros(count + 1, dtype=np.float64)
    gradients[1:-1] = np.diff(surface_m) / dr
    # The wall value is the initial contact-line datum eta(R)=0.
    gradients[-1] = -2.0 * surface_m[-1] / dr
    return (
        faces[1:] * gradients[1:] - faces[:-1] * gradients[:-1]
    ) / (radius_m * dr)


def _surface_slope_pinned(surface_m: FloatArray, dr: float) -> float:
    if surface_m.size < 2:
        return 0.0
    interior = np.abs(np.diff(surface_m) / dr)
    wall = abs(2.0 * surface_m[-1] / dr)
    return max(float(np.max(interior)), wall)


def _surface_energy_j(
    radius_m: FloatArray,
    area_weights_m2: FloatArray,
    eta_m: FloatArray,
    vessel_radius_m: float,
    delta_density_kg_m3: float,
    gravity_m_s2: float,
    surface_tension_n_m: float,
) -> float:
    dr = vessel_radius_m / eta_m.size
    faces = np.arange(eta_m.size + 1, dtype=np.float64) * dr
    gradient = np.zeros(eta_m.size + 1, dtype=np.float64)
    gradient[1:-1] = np.diff(eta_m) / dr
    gradient[-1] = -2.0 * eta_m[-1] / dr
    dual_width = np.full(eta_m.size, dr)
    dual_width[-1] = 0.5 * dr
    capillary = 0.5 * surface_tension_n_m * float(
        np.sum(2.0 * math.pi * faces[1:] * np.square(gradient[1:]) * dual_width)
    )
    gravitational = 0.5 * delta_density_kg_m3 * gravity_m_s2 * float(
        np.sum(area_weights_m2 * np.square(eta_m))
    )
    return capillary + gravitational


@dataclass(frozen=True, slots=True)
class CovarianceSnapshot:
    rr_m2_s2: FloatArray
    rz_m2_s2: FloatArray
    tt_m2_s2: FloatArray
    zz_m2_s2: FloatArray


class PrescribedCovariance:
    """Hash-bound, positive-semidefinite axisymmetric covariance input."""

    _FIELD_NAMES = (
        "R_rr_m2_s2",
        "R_rz_m2_s2",
        "R_tt_m2_s2",
        "R_zz_m2_s2",
    )

    def __init__(self, config: SimulationConfig, grid: AxisymmetricGrid) -> None:
        self.mode = config.stokes.covariance_mode
        self.source = config.stokes.covariance_source
        self.relative_uncertainty = config.stokes.covariance_relative_uncertainty
        self.sha256 = config.stokes.covariance_sha256
        self._zero = np.zeros(grid.shape, dtype=np.float64)
        if self.mode == "zero":
            self._time = np.array([0.0], dtype=np.float64)
            self._fields = tuple(self._zero[None, ...] for _ in self._FIELD_NAMES)
            return
        path = Path(config.stokes.covariance_npz_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"stokes covariance NPZ does not exist: {path}")
        actual_digest = _sha256_file(path)
        if actual_digest != config.stokes.covariance_sha256:
            raise ValueError(
                "stokes covariance SHA-256 mismatch: "
                f"expected {config.stokes.covariance_sha256}, got {actual_digest}"
            )
        with np.load(path, allow_pickle=False) as archive:
            required = {"time_s", "radius_m", "z_m", *self._FIELD_NAMES}
            missing = sorted(required - set(archive.files))
            if missing:
                raise ValueError(
                    "stokes covariance NPZ is missing: " + ", ".join(missing)
                )
            time = np.asarray(archive["time_s"], dtype=np.float64)
            radius = np.asarray(archive["radius_m"], dtype=np.float64)
            axial = np.asarray(archive["z_m"], dtype=np.float64)
            fields = tuple(
                np.asarray(archive[name], dtype=np.float64) for name in self._FIELD_NAMES
            )
        if time.ndim != 1 or time.size < 1 or not np.isfinite(time).all():
            raise ValueError("covariance time_s must be a finite nonempty vector")
        if time[0] != 0.0 or np.any(np.diff(time) <= 0.0):
            raise ValueError("covariance time_s must start at zero and increase strictly")
        if time.size > 1 and time[-1] < config.run.duration_s:
            raise ValueError("covariance time_s must cover the complete run")
        np.testing.assert_allclose(
            radius,
            grid.r_centres,
            rtol=0.0,
            atol=1.0e-12 * max(1.0, grid.radius_m),
            err_msg="covariance radius_m does not match the Stokes grid",
        )
        np.testing.assert_allclose(
            axial,
            grid.z_centres,
            rtol=0.0,
            atol=1.0e-12 * max(1.0, grid.height_m),
            err_msg="covariance z_m does not match the Stokes grid",
        )
        normalised: list[FloatArray] = []
        for name, values in zip(self._FIELD_NAMES, fields):
            if values.shape == grid.shape and time.size == 1:
                values = values[None, ...]
            if values.shape != (time.size, *grid.shape):
                raise ValueError(
                    f"{name} must have shape {(time.size, *grid.shape)}, got {values.shape}"
                )
            if not np.isfinite(values).all():
                raise ValueError(f"{name} contains non-finite values")
            normalised.append(values)
        rr, rz, tt, zz = normalised
        scale = max(float(np.max(np.abs(np.stack(normalised)))), 1.0)
        tolerance = 1.0e-12 * scale
        if np.min(rr) < -tolerance or np.min(tt) < -tolerance or np.min(zz) < -tolerance:
            raise ValueError("covariance diagonal contains a negative variance")
        if np.min(rr * zz - rz * rz) < -tolerance * scale:
            raise ValueError("covariance meridional block is not positive semidefinite")
        self._time = time
        self._fields = tuple(normalised)

    def at(self, time_s: float) -> CovarianceSnapshot:
        if self._time.size == 1:
            values = tuple(field[0] for field in self._fields)
        else:
            right = int(np.searchsorted(self._time, time_s, side="right"))
            if right == 0:
                values = tuple(field[0] for field in self._fields)
            elif right >= self._time.size:
                values = tuple(field[-1] for field in self._fields)
            else:
                left = right - 1
                fraction = (time_s - self._time[left]) / (
                    self._time[right] - self._time[left]
                )
                values = tuple(
                    (1.0 - fraction) * field[left] + fraction * field[right]
                    for field in self._fields
                )
        return CovarianceSnapshot(*values)


@dataclass(frozen=True, slots=True)
class _Instantaneous:
    circulation_rate_m2_s2: float
    eta_rate_m_s: FloatArray
    solution: AxisymmetricStokesSolution
    inertia: InertiaDiagnostics
    swirl_power_w: float
    covariance_power_w: float
    viscous_dissipation_w: float


class ForwardStokesBackend:
    """Formal selectable backend for the linear moving-interface SH substage."""

    mode = BackendMode.FORWARD
    backend_id = "forward_stokes"
    display_name = "SH 轴对称经向 Stokes（线性平顶参考域）"

    def run(
        self,
        config: SimulationConfig,
        control: Control | None = None,
        progress: ProgressCallback | None = None,
    ) -> SimulationResult:
        if config.backend is not BackendMode.FORWARD:
            raise ValueError(
                "ForwardStokesBackend requires config.backend=BackendMode.FORWARD"
            )
        if config.forward.model_branch != STOKES_MODEL_ID:
            raise ValueError(
                f"ForwardStokesBackend requires forward.model_branch={STOKES_MODEL_ID!r}"
            )
        if config.stokes.model_id != STOKES_MODEL_ID:
            raise ValueError("config.stokes.model_id does not identify AV-FWD-SH-v1")
        if config.stokes.time_step_s > config.run.output_interval_s:
            raise ValueError("stokes.time_step_s cannot exceed run.output_interval_s")

        grid = AxisymmetricGrid(
            config.stokes.radial_cells,
            config.stokes.axial_cells,
            config.container.radius_m,
            config.container.liquid_depth_m,
        )
        operator = AxisymmetricStokesOperator(
            grid, config.fluid.liquid_dynamic_viscosity_pa_s
        )
        covariance = PrescribedCovariance(config, grid)
        area_weights = math.pi * (
            np.square(grid.r_faces[1:]) - np.square(grid.r_faces[:-1])
        )
        total_area = float(np.sum(area_weights))
        delta_density = (
            config.fluid.liquid_density_kg_m3 - config.fluid.gas_density_kg_m3
        )
        eta = self._initial_surface(config, grid, area_weights)
        circulation = float(config.forward.initial_circulation_m2_s)
        accumulated_swirl_work = 0.0
        accumulated_covariance_work = 0.0
        accumulated_dissipation = 0.0
        current_time = 0.0
        output_times = _output_times(config.run.duration_s, config.run.output_interval_s)
        initial_energy = _surface_energy_j(
            grid.r_centres,
            area_weights,
            eta,
            grid.radius_m,
            delta_density,
            config.fluid.gravity_m_s2,
            config.fluid.surface_tension_n_m,
        )

        times: list[float] = []
        omega_values: list[float] = []
        circulation_values: list[float] = []
        surfaces: list[FloatArray] = []
        volume_errors: list[float] = []
        maximum_slopes: list[float] = []
        amplitude_ratios: list[float] = []
        minimum_gaps: list[float] = []
        energies: list[float] = []
        swirl_powers: list[float] = []
        covariance_powers: list[float] = []
        dissipations: list[float] = []
        energy_balance_residuals: list[float] = []
        reynolds_values: list[float] = []
        stokes_values: list[float] = []
        inertia_ratios: list[float] = []
        divergence_values: list[float] = []
        linear_residuals: list[float] = []
        radial_velocity_fields: list[FloatArray] = []
        axial_velocity_fields: list[FloatArray] = []
        pressure_fields: list[FloatArray] = []
        strain_rr_fields: list[FloatArray] = []
        strain_tt_fields: list[FloatArray] = []
        strain_zz_fields: list[FloatArray] = []
        strain_rz_fields: list[FloatArray] = []
        validity_mask: list[float] = []
        events: list[Event] = []
        gas_snapshots: list[dict] = []

        previous_solution: AxisymmetricStokesSolution | None = None
        previous_solution_time: float | None = None
        first_invalid_time: float | None = None
        last_valid_time: float = 0.0
        invalid_reason: str | None = None
        stopped_by_user = False

        def solve_instant(time_s: float, c_value: float, surface: FloatArray) -> _Instantaneous:
            nonlocal previous_solution, previous_solution_time
            result = self._instantaneous(
                config,
                grid,
                operator,
                covariance,
                time_s,
                c_value,
                surface,
                area_weights,
                previous_solution=previous_solution,
                previous_elapsed=(
                    None
                    if previous_solution_time is None
                    else max(time_s - previous_solution_time, np.finfo(float).eps)
                ),
            )
            if (result.solution.diagnostics.relative_linear_residual
                    > config.stokes.linear_solver_relative_tolerance):
                raise FloatingPointError("SH Stokes linear solve residual limit exceeded")
            return result

        def assess(time_s: float, surface: FloatArray, instant: _Instantaneous) -> str | None:
            gas_valid, gas_reason, _ = assess_open_surface_gas(
                config, grid.height_m + surface, grid.dr, time_s)
            if not gas_valid:
                return gas_reason
            slope = _surface_slope_pinned(surface, grid.dr)
            amplitude_ratio = float(np.max(np.abs(surface))) / grid.height_m
            bar_top = (
                config.stir_bar.bottom_clearance_m + 2.0 * config.stir_bar.radius_m
            )
            gap = grid.height_m + float(np.min(surface)) - bar_top
            checks = (
                (
                    slope > config.stokes.maximum_surface_slope,
                    "linear free-surface slope limit exceeded",
                ),
                (
                    amplitude_ratio > config.stokes.maximum_amplitude_depth_ratio,
                    "linear free-surface amplitude/depth limit exceeded",
                ),
                (gap <= 0.0, "free surface reached the effective stir-bar top"),
                (
                    instant.inertia.meridional_reynolds
                    > config.stokes.maximum_meridional_reynolds,
                    "meridional Reynolds applicability limit exceeded",
                ),
                (
                    instant.inertia.unsteady_stokes
                    > config.stokes.maximum_unsteady_stokes,
                    "unsteady Stokes applicability limit exceeded",
                ),
                (
                    instant.inertia.inertia_residual_ratio
                    > config.stokes.maximum_inertia_residual_ratio,
                    "omitted meridional-inertia residual limit exceeded",
                ),
            )
            return next((message for failed, message in checks if failed), None)

        def append_snapshot(time_s: float, instant: _Instantaneous) -> None:
            _, _, gas_details = assess_open_surface_gas(
                config, grid.height_m + eta, grid.dr, time_s)
            gas_snapshots.append(gas_details)
            energy = _surface_energy_j(
                grid.r_centres,
                area_weights,
                eta,
                grid.radius_m,
                delta_density,
                config.fluid.gravity_m_s2,
                config.fluid.surface_tension_n_m,
            )
            bar_top = (
                config.stir_bar.bottom_clearance_m + 2.0 * config.stir_bar.radius_m
            )
            times.append(float(time_s))
            omega_values.append(_drive_omega_rad_s(config, time_s))
            circulation_values.append(float(circulation))
            surfaces.append((grid.height_m + eta).copy())
            volume_errors.append(float(np.sum(area_weights * eta)))
            maximum_slopes.append(_surface_slope_pinned(eta, grid.dr))
            amplitude_ratios.append(float(np.max(np.abs(eta))) / grid.height_m)
            minimum_gaps.append(grid.height_m + float(np.min(eta)) - bar_top)
            energies.append(energy)
            swirl_powers.append(instant.swirl_power_w)
            covariance_powers.append(instant.covariance_power_w)
            dissipations.append(instant.viscous_dissipation_w)
            energy_balance_residuals.append(
                (energy - initial_energy)
                - (
                    accumulated_swirl_work
                    + accumulated_covariance_work
                    - accumulated_dissipation
                )
            )
            reynolds_values.append(instant.inertia.meridional_reynolds)
            stokes_values.append(instant.inertia.unsteady_stokes)
            inertia_ratios.append(instant.inertia.inertia_residual_ratio)
            divergence_values.append(
                instant.solution.diagnostics.maximum_divergence_s_inv
            )
            linear_residuals.append(
                instant.solution.diagnostics.relative_linear_residual
            )
            radial_velocity_fields.append(instant.solution.radial_velocity_m_s.copy())
            axial_velocity_fields.append(instant.solution.axial_velocity_m_s.copy())
            pressure_fields.append(instant.solution.pressure_pa.copy())
            strain_rr_fields.append(instant.solution.strain.rr_s_inv.copy())
            strain_tt_fields.append(instant.solution.strain.tt_s_inv.copy())
            strain_zz_fields.append(instant.solution.strain.zz_s_inv.copy())
            strain_rz_fields.append(instant.solution.strain.rz_s_inv.copy())
            validity_mask.append(0.0 if first_invalid_time is not None else 1.0)

        instant = solve_instant(0.0, circulation, eta)
        initial_invalid = assess(0.0, eta, instant)
        if initial_invalid is not None:
            first_invalid_time = 0.0
            invalid_reason = initial_invalid
            events.append(Event(0.0, "SH_APPLICABILITY_EXIT", initial_invalid))
        append_snapshot(0.0, instant)
        previous_solution = instant.solution
        previous_solution_time = 0.0
        if progress is not None:
            progress(
                Progress(
                    fraction=0.0,
                    simulated_time_s=0.0,
                    step=0,
                    stage="running",
                    message="SH fixed-reference Stokes model initialised",
                )
            )

        strict_exit = initial_invalid is not None and config.forward.prediction_mode == "strict"
        for output_index, output_time in enumerate(output_times[1:], start=1):
            if strict_exit:
                break
            while current_time < output_time - 1.0e-15:
                if control is not None and not control.checkpoint():
                    stopped_by_user = True
                    break
                step = min(config.stokes.time_step_s, output_time - current_time)
                prior_time = current_time
                prior_surface = eta.copy()
                prior_circulation = circulation
                prior_work = np.array(
                    [
                        accumulated_swirl_work,
                        accumulated_covariance_work,
                        accumulated_dissipation,
                    ],
                    dtype=np.float64,
                )

                def stage(
                    stage_time: float,
                    stage_c: float,
                    stage_eta: FloatArray,
                ) -> tuple[float, FloatArray, FloatArray, _Instantaneous]:
                    value = solve_instant(stage_time, stage_c, stage_eta)
                    rates = np.array(
                        [
                            value.swirl_power_w,
                            value.covariance_power_w,
                            value.viscous_dissipation_w,
                        ],
                        dtype=np.float64,
                    )
                    return (
                        value.circulation_rate_m2_s2,
                        value.eta_rate_m_s,
                        rates,
                        value,
                    )

                k1_c, k1_eta, k1_work, _ = stage(prior_time, prior_circulation, prior_surface)
                k2_c, k2_eta, k2_work, _ = stage(
                    prior_time + 0.5 * step,
                    prior_circulation + 0.5 * step * k1_c,
                    prior_surface + 0.5 * step * k1_eta,
                )
                k3_c, k3_eta, k3_work, _ = stage(
                    prior_time + 0.5 * step,
                    prior_circulation + 0.5 * step * k2_c,
                    prior_surface + 0.5 * step * k2_eta,
                )
                k4_c, k4_eta, k4_work, _ = stage(
                    prior_time + step,
                    prior_circulation + step * k3_c,
                    prior_surface + step * k3_eta,
                )
                circulation = prior_circulation + step * (
                    k1_c + 2.0 * k2_c + 2.0 * k3_c + k4_c
                ) / 6.0
                eta = prior_surface + step * (
                    k1_eta + 2.0 * k2_eta + 2.0 * k3_eta + k4_eta
                ) / 6.0
                # Remove only accumulated roundoff in the exactly closed-volume
                # kinematic constraint; this is not a physical volume source.
                eta -= float(np.sum(area_weights * eta) / total_area)
                work = prior_work + step * (
                    k1_work + 2.0 * k2_work + 2.0 * k3_work + k4_work
                ) / 6.0
                accumulated_swirl_work = float(work[0])
                accumulated_covariance_work = float(work[1])
                accumulated_dissipation = float(work[2])
                current_time = prior_time + step
                end_instant = solve_instant(current_time, circulation, eta)
                previous_solution = end_instant.solution
                previous_solution_time = current_time
                if first_invalid_time is None:
                    reason = assess(current_time, eta, end_instant)
                    if reason is not None:
                        first_invalid_time = current_time
                        last_valid_time = prior_time
                        invalid_reason = reason
                        events.append(
                            Event(
                                current_time,
                                "SH_APPLICABILITY_EXIT",
                                reason,
                                {
                                    "last_confirmed_valid_s": prior_time,
                                    "first_confirmed_invalid_s": current_time,
                                },
                            )
                        )
                        if config.forward.prediction_mode == "strict":
                            strict_exit = True
                            break
                elif config.forward.prediction_mode == "diagnostic":
                    pass
            if stopped_by_user:
                break
            snapshot_time = current_time if strict_exit else float(output_time)
            snapshot_instant = end_instant
            append_snapshot(snapshot_time, snapshot_instant)
            if progress is not None:
                progress(
                    Progress(
                        fraction=min(1.0, snapshot_time / config.run.duration_s),
                        simulated_time_s=snapshot_time,
                        step=output_index,
                        stage="running" if not strict_exit else "physical_exit",
                        message=(
                            "SH applicability boundary reached"
                            if strict_exit
                            else "SH fixed-reference Stokes step complete"
                        ),
                        metrics={
                            "maximum_surface_slope": maximum_slopes[-1],
                            "meridional_reynolds": reynolds_values[-1],
                            "inertia_residual_ratio": inertia_ratios[-1],
                        },
                    )
                )
            if strict_exit:
                break

        time_array = np.asarray(times, dtype=np.float64)
        if stopped_by_user and first_invalid_time is None:
            validity_status = QuantitativeValidityStatus.USER_STOPPED
            validity_reason = control.stop_reason if control is not None else "user stopped"
            run_status = RunStatus.STOPPED
            valid_until = float(time_array[-1]) if time_array.size else None
            termination = None
        elif first_invalid_time is not None:
            diagnostic = config.forward.prediction_mode == "diagnostic" and not strict_exit
            validity_status = (
                QuantitativeValidityStatus.DIAGNOSTIC_CONTINUATION
                if diagnostic
                else QuantitativeValidityStatus.PHYSICAL_EXIT
            )
            validity_reason = invalid_reason or "SH applicability boundary reached"
            run_status = RunStatus.COMPLETED
            valid_until = float(last_valid_time)
            termination = [float(last_valid_time), float(first_invalid_time)]
            # Samples at and after the first invalid internal time are rejected.
            validity_mask = [
                1.0 if sample_time < first_invalid_time else 0.0
                for sample_time in times
            ]
        else:
            validity_status = QuantitativeValidityStatus.VALID
            validity_reason = (
                "completed within the conditional linear-flat-cylinder SH limits"
            )
            run_status = RunStatus.COMPLETED
            valid_until = float(time_array[-1]) if time_array.size else None
            termination = None

        theory_sha256, theory_identity_source = _theory_identity()
        validity = make_quantitative_validity(
            model_branch=STOKES_MODEL_ID,
            theory_sha256=theory_sha256,
            implementation_id=IMPLEMENTATION_ID,
            status=validity_status,
            valid_until_s=valid_until,
            first_invalid_time_s=first_invalid_time,
            termination_interval_s=termination,
            reason=validity_reason,
            diagnostic_continuation=(
                validity_status is QuantitativeValidityStatus.DIAGNOSTIC_CONTINUATION
            ),
            claim_scope=QuantitativeClaimScope.CONDITIONAL_MODEL_NUMERICS,
            apparatus_quantitative_prediction_qualified=False,
            calibrated_physical_inputs=False,
            experimental_validation=False,
        )
        metadata = {
            "model_id": STOKES_MODEL_ID,
            "implementation_id": IMPLEMENTATION_ID,
            "theory_sha256": theory_sha256,
            "theory_identity_source": theory_identity_source,
            "quantitative_validity": validity,
            "gas_assessments": gas_snapshots,
            "claim_boundary": (
                "finite-cylinder fixed flat reference domain with a linearised "
                "small-slope moving free surface and fixed contact line; nonlinear "
                "moving geometry/contact-line SH remains unimplemented"
            ),
            "nonlinear_moving_domain_implemented": False,
            "numerical_qualification_notice": (
                "SH 固定域算子已验证；移动面能量精度尚未达标，完整移动域未实现。"
                "运行完成和有效掩码均不能作为 10-C3 已通过的证据。"
            ),
            "uses_cp_interface_mobility": False,
            "pressure_zero_mode": "uniform traction is absorbed by pressure; K_S(1)=0",
            "wall_boundary": "no_slip_side_and_bottom",
            "surface_boundary": "linear_gravity_capillary_traction",
            "covariance": {
                "mode": covariance.mode,
                "sha256": covariance.sha256 or None,
                "source": covariance.source or None,
                "relative_uncertainty": covariance.relative_uncertainty,
            },
            "observable_units": {
                "actual_bar_omega_rad_s": "rad s^-1",
                "circulation_m2_s": "m^2 s^-1",
                "surface_profile_m": "m",
                "volume_error_m3": "m^3",
                "maximum_surface_slope": "1",
                "amplitude_depth_ratio": "1",
                "minimum_gap_m": "m",
                "surface_energy_j": "J",
                "swirl_input_power_w": "W",
                "covariance_input_power_w": "W",
                "viscous_dissipation_w": "W",
                "energy_balance_residual_j": "J",
                "meridional_reynolds": "1",
                "unsteady_stokes": "1",
                "inertia_residual_ratio": "1",
                "maximum_divergence_s_inv": "s^-1",
                "linear_relative_residual": "1",
                "quantitative_valid_mask": "1",
            },
            "field_units": {
                "radius_m": "m",
                "z_m": "m",
                "u_r_m_s": "m s^-1",
                "u_z_m_s": "m s^-1",
                "radius_faces_m": "m",
                "radius_centres_m": "m",
                "z_faces_m": "m",
                "z_centres_m": "m",
                "radial_velocity_m_s": "m s^-1",
                "axial_velocity_m_s": "m s^-1",
                "pressure_pa": "Pa",
                "strain_rr_s_inv": "s^-1",
                "strain_tt_s_inv": "s^-1",
                "strain_zz_s_inv": "s^-1",
                "strain_rz_s_inv": "s^-1",
            },
        }
        return SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=time_array,
            observables={
                "actual_bar_omega_rad_s": np.asarray(omega_values),
                "circulation_m2_s": np.asarray(circulation_values),
                "surface_profile_m": np.asarray(surfaces),
                "volume_error_m3": np.asarray(volume_errors),
                "maximum_surface_slope": np.asarray(maximum_slopes),
                "amplitude_depth_ratio": np.asarray(amplitude_ratios),
                "minimum_gap_m": np.asarray(minimum_gaps),
                "surface_energy_j": np.asarray(energies),
                "swirl_input_power_w": np.asarray(swirl_powers),
                "covariance_input_power_w": np.asarray(covariance_powers),
                "viscous_dissipation_w": np.asarray(dissipations),
                "energy_balance_residual_j": np.asarray(energy_balance_residuals),
                "meridional_reynolds": np.asarray(reynolds_values),
                "unsteady_stokes": np.asarray(stokes_values),
                "inertia_residual_ratio": np.asarray(inertia_ratios),
                "maximum_divergence_s_inv": np.asarray(divergence_values),
                "linear_relative_residual": np.asarray(linear_residuals),
                "quantitative_valid_mask": np.asarray(validity_mask, dtype=np.float64),
            },
            fields={
                "radius_m": grid.r_centres,
                "z_m": grid.z_centres,
                "u_r_m_s": .5 * (np.asarray(radial_velocity_fields)[:, 1:, :]
                                   + np.asarray(radial_velocity_fields)[:, :-1, :]),
                "u_z_m_s": .5 * (np.asarray(axial_velocity_fields)[:, :, 1:]
                                   + np.asarray(axial_velocity_fields)[:, :, :-1]),
                "radius_faces_m": grid.r_faces,
                "radius_centres_m": grid.r_centres,
                "z_faces_m": grid.z_faces,
                "z_centres_m": grid.z_centres,
                "radial_velocity_m_s": np.asarray(radial_velocity_fields),
                "axial_velocity_m_s": np.asarray(axial_velocity_fields),
                "pressure_pa": np.asarray(pressure_fields),
                "strain_rr_s_inv": np.asarray(strain_rr_fields),
                "strain_tt_s_inv": np.asarray(strain_tt_fields),
                "strain_zz_s_inv": np.asarray(strain_zz_fields),
                "strain_rz_s_inv": np.asarray(strain_rz_fields),
            },
            events=tuple(events),
            metadata=metadata,
            status=run_status,
        )

    @staticmethod
    def _initial_surface(
        config: SimulationConfig,
        grid: AxisymmetricGrid,
        area_weights_m2: FloatArray,
    ) -> FloatArray:
        stokes = config.stokes
        if stokes.initial_surface_npz_path:
            path = Path(stokes.initial_surface_npz_path).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f"initial-surface NPZ does not exist: {path}")
            digest = _sha256_file(path)
            if digest != stokes.initial_surface_sha256:
                raise ValueError(
                    "initial-surface SHA-256 mismatch: "
                    f"expected {stokes.initial_surface_sha256}, got {digest}"
                )
            with np.load(path, allow_pickle=False) as archive:
                if not {"radius_m", "eta_m"}.issubset(archive.files):
                    raise ValueError("initial-surface NPZ requires radius_m and eta_m")
                radius = np.asarray(archive["radius_m"], dtype=np.float64)
                elevation = np.asarray(archive["eta_m"], dtype=np.float64)
            if (
                radius.ndim != 1
                or elevation.shape != radius.shape
                or radius.size < 3
                or not np.isfinite(radius).all()
                or not np.isfinite(elevation).all()
                or np.any(np.diff(radius) <= 0.0)
                or radius[0] > grid.r_centres[0]
                or radius[-1] < grid.r_centres[-1]
            ):
                raise ValueError("initial-surface NPZ vectors are invalid or do not cover the grid")
            eta = np.interp(grid.r_centres, radius, elevation)
        elif stokes.initial_surface_mode > 0:
            # Two Dirichlet Bessel components enforce eta(R)=0 and the
            # independently required closed-volume constraint. This prescribed
            # shape is not claimed to be a Stokes decay eigenmode.
            roots = jn_zeros(0, stokes.initial_surface_mode + 1)[-2:]
            first = j0(roots[0] * grid.r_centres / grid.radius_m)
            second = j0(roots[1] * grid.r_centres / grid.radius_m)
            shape = first - second * (np.sum(area_weights_m2 * first)
                                      / np.sum(area_weights_m2 * second))
            shape /= float(np.max(np.abs(shape)))
            eta = stokes.initial_surface_amplitude_m * shape
        else:
            eta = np.zeros(grid.nr, dtype=np.float64)
        volume = float(np.sum(area_weights_m2 * eta))
        scale = max(
            float(np.sum(area_weights_m2)) * max(float(np.max(np.abs(eta))), 1.0e-12),
            np.finfo(float).tiny,
        )
        if abs(volume) > 1.0e-10 * scale:
            raise ValueError(
                "initial surface does not preserve the configured liquid volume; "
                "supply an independently volume-compatible profile"
            )
        eta = eta - volume / float(np.sum(area_weights_m2))
        return eta.astype(np.float64, copy=False)

    @staticmethod
    def _instantaneous(
        config: SimulationConfig,
        grid: AxisymmetricGrid,
        operator: AxisymmetricStokesOperator,
        covariance_input: PrescribedCovariance,
        time_s: float,
        circulation_m2_s: float,
        eta_m: FloatArray,
        area_weights_m2: FloatArray,
        *,
        previous_solution: AxisymmetricStokesSolution | None,
        previous_elapsed: float | None,
    ) -> _Instantaneous:
        rho = config.fluid.liquid_density_kg_m3
        mu = config.fluid.liquid_dynamic_viscosity_pa_s
        delta_density = rho - config.fluid.gas_density_kg_m3
        omega = _drive_omega_rad_s(config, time_s)
        circulation_rate = (
            config.forward.circulation_coupling_m2 * omega - circulation_m2_s
        ) / config.forward.spin_up_time_s

        radial_faces = grid.r_faces[1:-1]
        axial_centres = grid.z_centres
        core = config.forward.effective_core_radius_m
        z_bar = config.stir_bar.bottom_clearance_m + 2.0 * config.stir_bar.radius_m
        floor = config.forward.axial_swirl_floor
        decay_length = config.forward.axial_swirl_decay_length_m
        denominator = floor + (1.0 - floor) * math.exp(
            -(grid.height_m - z_bar) / decay_length
        )
        axial_shape = (
            floor
            + (1.0 - floor)
            * np.exp(-np.maximum(axial_centres - z_bar, 0.0) / decay_length)
        ) / denominator
        radial_regularisation = -np.expm1(-np.square(radial_faces / core))
        tangential_velocity = (
            circulation_m2_s
            * radial_regularisation[:, None]
            * axial_shape[None, :]
            / radial_faces[:, None]
        )
        swirl_radial_force = rho * np.square(tangential_velocity) / radial_faces[:, None]
        swirl_axial_force = np.zeros((grid.nr, grid.nz - 1), dtype=np.float64)

        covariance = covariance_input.at(time_s)
        rr, rz, tt, zz = (
            covariance.rr_m2_s2,
            covariance.rz_m2_s2,
            covariance.tt_m2_s2,
            covariance.zz_m2_s2,
        )
        isotropic = (
            np.allclose(rz, 0.0, rtol=0.0, atol=1.0e-14)
            and np.allclose(rr, tt, rtol=1.0e-12, atol=1.0e-14)
            and np.allclose(rr, zz, rtol=1.0e-12, atol=1.0e-14)
        )
        if isotropic:
            covariance_radial_force = -rho * np.diff(rr, axis=0) / grid.dr
            covariance_axial_force = -rho * np.diff(rr, axis=1) / grid.dz
        else:
            edge_order = 2 if min(grid.shape) >= 3 else 1
            div_r = (
                np.gradient(rr, grid.dr, axis=0, edge_order=edge_order)
                + (rr - tt) / grid.r_centres[:, None]
                + np.gradient(rz, grid.dz, axis=1, edge_order=edge_order)
            )
            div_z = (
                np.gradient(rz, grid.dr, axis=0, edge_order=edge_order)
                + rz / grid.r_centres[:, None]
                + np.gradient(zz, grid.dz, axis=1, edge_order=edge_order)
            )
            covariance_radial_force = -0.5 * rho * (div_r[:-1] + div_r[1:])
            covariance_axial_force = -0.5 * rho * (div_z[:, :-1] + div_z[:, 1:])

        radial_force = swirl_radial_force + covariance_radial_force
        # Solve for pressure relative to the exact hydrostatic equilibrium.
        # This keeps the quiescent state exactly stationary instead of seeding
        # the stiff free-surface equation with cancellation roundoff.
        axial_force = swirl_axial_force + covariance_axial_force
        laplacian_eta = _surface_laplacian_pinned(
            grid.r_centres, eta_m, grid.radius_m
        )
        surface_traction = (
            -delta_density * config.fluid.gravity_m_s2 * eta_m
            + config.fluid.surface_tension_n_m * laplacian_eta
        )
        covariance_zz_top = 1.5 * zz[:, -1] - 0.5 * zz[:, -2]
        covariance_rz_top_centres = 1.5 * rz[:, -1] - 0.5 * rz[:, -2]
        covariance_rz_top_faces = 0.5 * (
            covariance_rz_top_centres[:-1] + covariance_rz_top_centres[1:]
        )
        solution = operator.solve(
            radial_force,
            axial_force,
            surface_traction + rho * covariance_zz_top,
            rho * covariance_rz_top_faces,
        )
        solution = replace(solution, pressure_pa=solution.pressure_pa
            + delta_density * config.fluid.gravity_m_s2
            * (grid.height_m - grid.z_centres[None, :]))
        eta_rate = solution.axial_velocity_m_s[:, -1].copy()
        # Enforce only the roundoff-null constant component from the exactly
        # conservative MAC divergence constraint.
        eta_rate -= float(np.sum(area_weights_m2 * eta_rate) / np.sum(area_weights_m2))
        inertia = meridional_inertia_diagnostics(
            grid,
            rho,
            mu,
            solution,
            previous_solution=previous_solution,
            elapsed_s=previous_elapsed,
        )
        swirl_power = body_force_power_w(
            grid, swirl_radial_force, swirl_axial_force, solution
        )
        strain = solution.strain
        covariance_power = rho * grid.integrate(
            rr * strain.rr_s_inv
            + tt * strain.tt_s_inv
            + zz * strain.zz_s_inv
            + 2.0 * rz * strain.rz_s_inv
        )
        dissipation = viscous_dissipation_w(grid, strain, mu)
        return _Instantaneous(
            circulation_rate_m2_s2=float(circulation_rate),
            eta_rate_m_s=eta_rate,
            solution=solution,
            inertia=inertia,
            swirl_power_w=float(swirl_power),
            covariance_power_w=float(covariance_power),
            viscous_dissipation_w=float(dissipation),
        )


__all__ = [
    "FROZEN_THEORY_SHA256",
    "IMPLEMENTATION_ID",
    "ForwardStokesBackend",
    "PrescribedCovariance",
]
