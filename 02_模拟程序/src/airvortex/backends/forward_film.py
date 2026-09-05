"""Formal local thin-film backend for the AV-FWD-TF-v1 theory branch."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Callable

import numpy as np
from numpy.typing import NDArray
from scipy.special import jn_zeros, j0

from airvortex.core.config import BackendMode, SimulationConfig
from airvortex.core.film_config import FilmModelConfig, THEORY_ID
from airvortex.core.protocols import (
    Control,
    Event,
    Progress,
    ProgressCallback,
    RunStatus,
    SimulationResult,
)
from airvortex.core.validity import (
    QuantitativeClaimScope,
    QuantitativeValidityStatus,
    make_quantitative_validity,
)
from airvortex.numerics.thin_film import (
    AxisymmetricThinFilm,
    ThinFilmBoundary,
    integrate_positive_thin_film,
    piecewise_linear_value_and_rate,
)


FloatArray = NDArray[np.float64]
MODEL_ID = THEORY_ID
IMPLEMENTATION_ID = "airvortex.forward_film.axisymmetric-fv-bdf.v1"
MODEL_VERSION = 1


def _output_times(duration_s: float, interval_s: float) -> FloatArray:
    count = int(math.floor(duration_s / interval_s + 1.0e-13))
    values = np.arange(count + 1, dtype=np.float64) * interval_s
    if values[-1] < duration_s - 1.0e-13:
        values = np.append(values, duration_s)
    else:
        values[-1] = duration_s
    return values


def _drive_omega_and_rate(config: SimulationConfig, time_s: float) -> tuple[float, float]:
    drive = config.drive
    time = min(max(0.0, float(time_s)), config.run.duration_s)
    conversion = 2.0 * math.pi / 60.0
    if drive.actual_speed_schedule_time_s:
        rpm, slope = piecewise_linear_value_and_rate(
            drive.actual_speed_schedule_time_s,
            drive.actual_speed_schedule_rpm,
            time,
        )
        return rpm * conversion, slope * conversion
    direction = float(drive.rotation_direction)
    if drive.ramp_duration_s > 0.0 and time < drive.ramp_duration_s:
        slope_rpm_s = direction * (
            drive.target_rpm - drive.initial_rpm
        ) / drive.ramp_duration_s
        rpm = direction * drive.initial_rpm + slope_rpm_s * time
        return rpm * conversion, slope_rpm_s * conversion
    return direction * drive.target_rpm * conversion, 0.0


def _film_omega_provider(
    config: SimulationConfig, film: FilmModelConfig
) -> Callable[[float], tuple[float, float]]:
    if film.prescribed_swirl_time_s:
        return lambda time_s: piecewise_linear_value_and_rate(
            film.prescribed_swirl_time_s,
            film.prescribed_swirl_angular_speed_rad_s,
            time_s,
        )
    return lambda time_s: _drive_omega_and_rate(config, time_s)


def _boundary_provider(film: FilmModelConfig) -> ThinFilmBoundary:
    if film.boundary_condition == "closed":
        return ThinFilmBoundary.closed()
    if film.outer_boundary_time_s:
        def thickness(time_s: float) -> float:
            return piecewise_linear_value_and_rate(
                film.outer_boundary_time_s,
                film.outer_thickness_schedule_m,
                time_s,
            )[0]

        def thickness_rate(time_s: float) -> float:
            return piecewise_linear_value_and_rate(
                film.outer_boundary_time_s,
                film.outer_thickness_schedule_m,
                time_s,
            )[1]

        def pressure(time_s: float) -> float:
            return piecewise_linear_value_and_rate(
                film.outer_boundary_time_s,
                film.outer_pressure_schedule_pa,
                time_s,
            )[0]

        return ThinFilmBoundary(
            mode="open",
            thickness=thickness,
            thickness_rate=thickness_rate,
            pressure=pressure,
        )
    return ThinFilmBoundary.open_constant(
        film.outer_thickness_m, film.outer_pressure_pa
    )


def _initial_profile(film: FilmModelConfig, radius_m: FloatArray) -> FloatArray:
    if film.initial_profile_radius_m:
        return np.interp(
            radius_m,
            np.asarray(film.initial_profile_radius_m, dtype=np.float64),
            np.asarray(film.initial_profile_thickness_m, dtype=np.float64),
        )
    profile = np.full(radius_m.shape, film.initial_thickness_m, dtype=np.float64)
    if film.initial_perturbation_amplitude_m:
        wave_number = float(jn_zeros(1, film.initial_perturbation_mode)[-1]) / film.domain_radius_m
        profile += film.initial_perturbation_amplitude_m * j0(wave_number * radius_m)
    return profile


def _footprint_radius_m(
    radius_m: FloatArray, thickness_m: FloatArray, threshold_m: float, domain_radius_m: float
) -> float:
    if thickness_m[0] > threshold_m:
        return 0.0
    outside = np.flatnonzero(thickness_m > threshold_m)
    if outside.size == 0:
        return float(domain_radius_m)
    first = int(outside[0])
    if first == 0:
        return 0.0
    left_r = float(radius_m[first - 1])
    right_r = float(radius_m[first])
    left_h = float(thickness_m[first - 1])
    right_h = float(thickness_m[first])
    if right_h == left_h:
        return left_r
    return float(left_r + (threshold_m - left_h) * (right_r - left_r) / (right_h - left_h))


def _source_identities() -> tuple[str, dict[str, str]]:
    repository_root = Path(__file__).resolve().parents[4]
    theory_path = repository_root / "01_最终理论" / "01_前向预测理论.md"
    theory_sha = (hashlib.sha256(theory_path.read_bytes()).hexdigest()
                  if theory_path.is_file() else
                  "4698b016a4a27110b8e147fe99d216a2b8589ffd96af26c4b5888a39c15a39b7")
    source_paths = {
        "backend": Path(__file__).resolve(),
        "operator": Path(__file__).resolve().parents[1]
        / "numerics"
        / "thin_film.py",
        "configuration": Path(__file__).resolve().parents[1]
        / "core"
        / "film_config.py",
    }
    hashes = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in source_paths.items()
    }
    return theory_sha, hashes


class ForwardFilmBackend:
    """Conditional local-film prediction on a prescribed circular flat top."""

    mode = BackendMode.FORWARD
    backend_id = "forward_film"
    display_name = "局部轴对称正厚度薄膜模型"

    def run(
        self,
        config: SimulationConfig,
        control: Control | None = None,
        progress: ProgressCallback | None = None,
    ) -> SimulationResult:
        if config.backend is not BackendMode.FORWARD:
            raise ValueError("ForwardFilmBackend requires config.backend=BackendMode.FORWARD")
        if config.forward.model_branch != THEORY_ID:
            raise ValueError(f"ForwardFilmBackend requires forward.model_branch={THEORY_ID}")
        film = config.film
        if not isinstance(film, FilmModelConfig):
            raise TypeError("config.film must be a FilmModelConfig")
        if film.domain_radius_m > config.forward.effective_target_radius_m:
            raise ValueError(
                "local film radius must not exceed the configured effective flat-top radius"
            )
        if film.outer_boundary_time_s and film.outer_boundary_time_s[-1] < config.run.duration_s:
            raise ValueError("film outer-boundary schedule must cover the run duration")
        if film.prescribed_swirl_time_s and film.prescribed_swirl_time_s[-1] < config.run.duration_s:
            raise ValueError("film prescribed swirl schedule must cover the run duration")

        density_difference = (
            config.fluid.liquid_density_kg_m3 - config.fluid.gas_density_kg_m3
        )
        omega_provider = _film_omega_provider(config, film)
        operator = AxisymmetricThinFilm(
            radius_m=film.domain_radius_m,
            radial_cells=film.radial_cells,
            density_kg_m3=config.fluid.liquid_density_kg_m3,
            viscosity_pa_s=config.fluid.liquid_dynamic_viscosity_pa_s,
            surface_tension_n_m=config.fluid.surface_tension_n_m,
            density_difference_kg_m3=density_difference,
            gravity_m_s2=config.fluid.gravity_m_s2,
            slip_length_m=film.slip_length_m,
            precursor_thickness_m=film.precursor_thickness_m,
            wetting_pressure_scale_pa=film.wetting_pressure_pa,
            boundary=_boundary_provider(film),
            swirl_closure=film.swirl_closure,  # type: ignore[arg-type]
            angular_speed=omega_provider,
            swirl_profile_coefficients=film.swirl_profile_coefficients,
        )
        initial = _initial_profile(film, operator.radius_centres_m)
        if np.min(initial) <= film.minimum_valid_thickness_m:
            raise ValueError("configured initial film is not above its validity thickness")
        if film.boundary_condition == "open":
            boundary_initial = float(operator.boundary.thickness(0.0))
            profile_edge = (
                film.initial_profile_thickness_m[-1]
                if film.initial_profile_thickness_m
                else film.initial_thickness_m
            )
            tolerance = max(film.absolute_tolerance_m, 1.0e-10 * abs(boundary_initial))
            if abs(profile_edge - boundary_initial) > tolerance:
                raise ValueError("open-film initial thickness is incompatible with h_out(0)")

        requested_times = _output_times(
            config.run.duration_s, config.run.output_interval_s
        )
        trajectory = integrate_positive_thin_film(
            operator,
            initial,
            requested_times,
            minimum_valid_thickness_m=film.minimum_valid_thickness_m,
            maximum_time_step_s=film.maximum_time_step_s,
            minimum_time_step_s=film.minimum_time_step_s,
            relative_tolerance=film.relative_tolerance,
            absolute_tolerance_m=film.absolute_tolerance_m,
            energy_absolute_tolerance_j=film.energy_absolute_tolerance_j,
            volume_absolute_tolerance_m3=film.volume_absolute_tolerance_m3,
            maximum_step_rejections=film.maximum_step_rejections,
            checkpoint=control.checkpoint if control is not None else None,
            diagnostic_continuation=config.forward.prediction_mode == "diagnostic",
        )

        snapshots = [
            operator.evaluate(thickness, float(time_s))
            for time_s, thickness in zip(trajectory.time_s, trajectory.thickness_m)
        ]
        energy = np.asarray([item.energy_j for item in snapshots], dtype=np.float64)
        volume = np.asarray([item.volume_m3 for item in snapshots], dtype=np.float64)
        minimum = np.min(trajectory.thickness_m, axis=1)
        centre = trajectory.thickness_m[:, 0]
        footprint = np.asarray(
            [
                _footprint_radius_m(
                    operator.radius_centres_m,
                    thickness,
                    film.footprint_threshold_m,
                    film.domain_radius_m,
                )
                for thickness in trajectory.thickness_m
            ],
            dtype=np.float64,
        )
        energy_balance = (
            energy
            - energy[0]
            + trajectory.cumulative_dissipation_j
            - trajectory.cumulative_drive_work_j
            - trajectory.cumulative_boundary_work_j
        )
        volume_balance = (
            volume - volume[0] + trajectory.cumulative_boundary_outflow_m3
        )
        omega = np.asarray(
            [omega_provider(float(time_s))[0] for time_s in trajectory.time_s],
            dtype=np.float64,
        )
        pressure = np.asarray([item.pressure_pa for item in snapshots])
        chemical = np.asarray([item.chemical_potential_pa for item in snapshots])
        flux = np.asarray([item.radial_flux_m2_s for item in snapshots])

        if trajectory.status == "numerical_failure":
            run_status = RunStatus.FAILED
            validity_status = QuantitativeValidityStatus.NUMERICAL_FAILURE
            claim_scope = QuantitativeClaimScope.NONE
            valid_until: float | None = None
            assert trajectory.termination_interval_s is not None
            first_invalid: float | None = trajectory.termination_interval_s[1]
            validity_interval = trajectory.termination_interval_s
            validity_mask = np.zeros(trajectory.time_s.shape, dtype=np.float64)
        elif trajectory.status == "stopped" and trajectory.termination_interval_s is None:
            run_status = RunStatus.STOPPED
            validity_status = QuantitativeValidityStatus.USER_STOPPED
            claim_scope = QuantitativeClaimScope.CONDITIONAL_MODEL_NUMERICS
            valid_until = float(trajectory.time_s[-1])
            first_invalid = None
            validity_interval = None
            validity_mask = np.ones(trajectory.time_s.shape, dtype=np.float64)
        elif trajectory.status == "physical_exit":
            run_status = RunStatus.COMPLETED
            validity_status = QuantitativeValidityStatus.PHYSICAL_EXIT
            claim_scope = QuantitativeClaimScope.CONDITIONAL_MODEL_NUMERICS
            assert trajectory.termination_interval_s is not None
            valid_until, first_invalid = trajectory.termination_interval_s
            validity_interval = trajectory.termination_interval_s
            validity_mask = np.ones(trajectory.time_s.shape, dtype=np.float64)
        elif trajectory.status in {"diagnostic_continuation", "stopped"}:
            run_status = RunStatus.STOPPED if trajectory.status == "stopped" else RunStatus.COMPLETED
            validity_status = QuantitativeValidityStatus.DIAGNOSTIC_CONTINUATION
            claim_scope = QuantitativeClaimScope.CONDITIONAL_MODEL_NUMERICS
            assert trajectory.termination_interval_s is not None
            valid_until, first_invalid = trajectory.termination_interval_s
            validity_interval = trajectory.termination_interval_s
            validity_mask = np.asarray(
                trajectory.time_s < first_invalid, dtype=np.float64
            )
        else:
            run_status = RunStatus.COMPLETED
            validity_status = QuantitativeValidityStatus.VALID
            claim_scope = QuantitativeClaimScope.CONDITIONAL_MODEL_NUMERICS
            valid_until = float(trajectory.time_s[-1])
            first_invalid = None
            validity_interval = None
            validity_mask = np.ones(trajectory.time_s.shape, dtype=np.float64)

        theory_sha, source_hashes = _source_identities()
        validity = make_quantitative_validity(
            model_branch=THEORY_ID,
            theory_sha256=theory_sha,
            implementation_id=IMPLEMENTATION_ID,
            status=validity_status,
            valid_until_s=valid_until,
            first_invalid_time_s=first_invalid,
            termination_interval_s=validity_interval,
            reason=(
                trajectory.reason
                or "local positive-thickness thin-film integration completed"
            ),
            diagnostic_continuation=(
                validity_status is QuantitativeValidityStatus.DIAGNOSTIC_CONTINUATION
            ),
            claim_scope=claim_scope,
            apparatus_quantitative_prediction_qualified=False,
            calibrated_physical_inputs=False,
            experimental_validation=False,
        )

        observables = {
            "film_minimum_thickness_m": minimum,
            "film_centre_thickness_m": centre,
            "film_volume_m3": volume,
            "film_volume_balance_error_m3": volume_balance,
            "film_energy_j": energy,
            "film_energy_balance_error_j": energy_balance,
            "film_dissipation_power_w": np.asarray(
                [item.dissipation_power_w for item in snapshots]
            ),
            "film_drive_power_w": np.asarray(
                [item.drive_power_w for item in snapshots]
            ),
            "film_boundary_power_w": np.asarray(
                [item.boundary_power_w for item in snapshots]
            ),
            "film_outer_flux_m2_s": flux[:, -1],
            "film_footprint_radius_m": footprint,
            "film_actual_angular_speed_rad_s": omega,
            "quantitative_valid_mask": validity_mask,
        }
        fields = {
            "film_radius_m": operator.radius_centres_m,
            "film_radius_faces_m": operator.radius_faces_m,
            "film_annular_weights_m2": operator.annular_weights_m2,
            "film_thickness_m": trajectory.thickness_m,
            "film_pressure_potential_pa": pressure,
            "film_chemical_potential_pa": chemical,
            "film_radial_flux_m2_s": flux,
            "film_cumulative_dissipation_j": trajectory.cumulative_dissipation_j,
            "film_cumulative_drive_work_j": trajectory.cumulative_drive_work_j,
            "film_cumulative_boundary_work_j": trajectory.cumulative_boundary_work_j,
            "film_cumulative_boundary_outflow_m3": (
                trajectory.cumulative_boundary_outflow_m3
            ),
        }
        metadata = {
            "model_id": MODEL_ID,
            "model_version": MODEL_VERSION,
            "implementation_id": IMPLEMENTATION_ID,
            "theory_sha256": theory_sha,
            "implementation_source_sha256": source_hashes,
            "model_class": "local_axisymmetric_positive_thickness_film",
            "scope": (
                "prescribed local initial-boundary-value film on a flat circular top; "
                "not a whole-vessel channel, bare-solid contact, or dry-rupture prediction"
            ),
            "boundary_condition": film.boundary_condition,
            "swirl_closure": film.swirl_closure,
            "weighted_swirl_transport": film.swirl_closure == "prescribed_depth_profile",
            "spatial_method": (
                "cell-centred conservative finite volume with exact annular 2*pi*r weights"
            ),
            "time_method": "scipy BDF with fail-closed positive-thickness segment retries",
            "negative_thickness_policy": "reject_without_clipping",
            "step_rejections": trajectory.step_rejections,
            "minimum_valid_thickness_m": film.minimum_valid_thickness_m,
            "film_config": film.to_dict(),
            "physical_parameters": {
                "liquid_density_kg_m3": config.fluid.liquid_density_kg_m3,
                "gas_density_kg_m3": config.fluid.gas_density_kg_m3,
                "density_difference_kg_m3": density_difference,
                "liquid_dynamic_viscosity_pa_s": (
                    config.fluid.liquid_dynamic_viscosity_pa_s
                ),
                "surface_tension_n_m": config.fluid.surface_tension_n_m,
                "gravity_m_s2": config.fluid.gravity_m_s2,
                "slip_length_m": film.slip_length_m,
                "precursor_thickness_m": film.precursor_thickness_m,
                "wetting_pressure_pa": film.wetting_pressure_pa,
            },
            "volume_balance": "V(t)-V(0)+integral(2*pi*L*Q_out dt)",
            "energy_balance": (
                "E(t)-E(0)+integral(D-P_drive-P_boundary)dt; open boundary includes "
                "chemical-potential transport and capillary boundary work"
            ),
            "footprint_interpretation": (
                "connected centre region below observation thickness; covered film remains, "
                "so this is not bare-solid contact or a qualified gas channel"
            ),
            "quantitative_validity": validity,
            "observable_units": {
                "film_minimum_thickness_m": "m",
                "film_centre_thickness_m": "m",
                "film_volume_m3": "m^3",
                "film_volume_balance_error_m3": "m^3",
                "film_energy_j": "J",
                "film_energy_balance_error_j": "J",
                "film_dissipation_power_w": "W",
                "film_drive_power_w": "W",
                "film_boundary_power_w": "W",
                "film_outer_flux_m2_s": "m^2/s",
                "film_footprint_radius_m": "m",
                "film_actual_angular_speed_rad_s": "rad/s",
                "quantitative_valid_mask": "1",
            },
        }

        events: list[Event] = []
        if validity_interval is not None and trajectory.status != "numerical_failure":
            events.append(
                Event(
                    time_s=float(first_invalid),
                    kind="film_validity_boundary",
                    message=trajectory.reason,
                    data={
                        "last_confirmed_valid_s": float(valid_until),
                        "first_confirmed_invalid_s": float(first_invalid),
                        "minimum_valid_thickness_m": film.minimum_valid_thickness_m,
                    },
                )
            )
        if trajectory.status == "numerical_failure":
            events.append(
                Event(
                    time_s=float(trajectory.time_s[-1]),
                    kind="thin_film_numerical_failure",
                    message=trajectory.reason,
                    data={"step_rejections": trajectory.step_rejections},
                )
            )
        elif trajectory.status == "stopped":
            events.append(
                Event(
                    time_s=float(trajectory.time_s[-1]),
                    kind="stopped",
                    message=control.stop_reason if control is not None else trajectory.reason,
                )
            )

        if progress is not None:
            progress(
                Progress(
                    fraction=float(trajectory.time_s[-1] / config.run.duration_s),
                    simulated_time_s=float(trajectory.time_s[-1]),
                    step=max(0, trajectory.time_s.size - 1),
                    stage=("completed" if run_status is RunStatus.COMPLETED else run_status.value),
                    message=(trajectory.reason or "local thin-film model completed"),
                    metrics={
                        "minimum_thickness_m": float(minimum[-1]),
                        "volume_balance_error_m3": float(volume_balance[-1]),
                        "energy_balance_error_j": float(energy_balance[-1]),
                    },
                )
            )

        return SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=trajectory.time_s,
            observables=observables,
            fields=fields,
            events=tuple(events),
            metadata=metadata,
            status=run_status,
        )


__all__ = ["IMPLEMENTATION_ID", "MODEL_ID", "ForwardFilmBackend"]
