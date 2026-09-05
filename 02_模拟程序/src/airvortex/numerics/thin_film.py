"""Conservative axisymmetric thin-film numerics for AV-FWD-TF-v1.

The spatial discretisation is cell-centred finite volume with exact annular
weights.  Film thickness is never clipped.  The time integrator retries a
failed BDF segment with a smaller maximum step and stops at the last positive,
model-valid state if the configured validity thickness is crossed.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Literal, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_ivp


FloatArray = NDArray[np.float64]
BoundaryMode = Literal["closed", "open"]
SwirlClosure = Literal["none", "corotation", "prescribed_depth_profile"]


class ThinFilmError(RuntimeError):
    """Base class for a local-film numerical failure."""


class NonpositiveFilmError(ThinFilmError):
    """Raised when an attempted stage has nonpositive thickness."""


@dataclass(frozen=True, slots=True)
class ThinFilmBoundary:
    """Complete outer boundary data for a fourth-order local-film problem."""

    mode: BoundaryMode
    thickness: Callable[[float], float]
    thickness_rate: Callable[[float], float]
    pressure: Callable[[float], float]

    @classmethod
    def closed(cls) -> "ThinFilmBoundary":
        return cls(
            mode="closed",
            thickness=lambda _time: math.nan,
            thickness_rate=lambda _time: 0.0,
            pressure=lambda _time: math.nan,
        )

    @classmethod
    def open_constant(cls, thickness_m: float, pressure_pa: float) -> "ThinFilmBoundary":
        if thickness_m <= 0.0 or not math.isfinite(thickness_m):
            raise ValueError("open boundary thickness must be finite and positive")
        if not math.isfinite(pressure_pa):
            raise ValueError("open boundary pressure must be finite")
        return cls(
            mode="open",
            thickness=lambda _time: float(thickness_m),
            thickness_rate=lambda _time: 0.0,
            pressure=lambda _time: float(pressure_pa),
        )


@dataclass(frozen=True, slots=True)
class ThinFilmInstantaneous:
    """One semidiscrete state and its conservative rate ledger."""

    pressure_pa: FloatArray
    chemical_potential_pa: FloatArray
    radial_flux_m2_s: FloatArray
    thickness_rate_m_s: FloatArray
    energy_j: float
    volume_m3: float
    dissipation_power_w: float
    drive_power_w: float
    boundary_power_w: float
    boundary_outflow_m3_s: float
    weighted_swirl_power_w: float


@dataclass(frozen=True, slots=True)
class ThinFilmTrajectory:
    """Requested outputs plus a fail-closed completion identity."""

    time_s: FloatArray
    thickness_m: FloatArray
    cumulative_dissipation_j: FloatArray
    cumulative_drive_work_j: FloatArray
    cumulative_boundary_work_j: FloatArray
    cumulative_boundary_outflow_m3: FloatArray
    status: str
    step_rejections: int
    termination_interval_s: tuple[float, float] | None = None
    reason: str = ""


def solve_uniform_drainage(
    initial_thickness_m: float,
    output_times_s: object,
    *,
    density_kg_m3: float,
    viscosity_pa_s: float,
    slip_length_m: float,
    angular_speed_rad_s: Callable[[float], float],
    relative_tolerance: float = 1.0e-11,
    absolute_tolerance_m: float = 1.0e-14,
) -> FloatArray:
    """Solve the T11 spatially uniform open-drainage reference ODE.

    This is also the production-level reference used to distinguish the
    integral of ``Omega**2`` from the square of an averaged angular speed.
    It rejects nonpositive attempted states and performs no clipping.
    """

    times = np.asarray(output_times_s, dtype=np.float64)
    if (
        times.ndim != 1
        or times.size == 0
        or times[0] != 0.0
        or np.any(np.diff(times) <= 0.0)
    ):
        raise ValueError("drainage output times must increase strictly from zero")
    if initial_thickness_m <= 0.0:
        raise ValueError("initial drainage thickness must be positive")

    def rhs(time_s: float, state: FloatArray) -> FloatArray:
        thickness = float(state[0])
        if thickness <= 0.0:
            raise NonpositiveFilmError("nonpositive drainage stage; clipping is forbidden")
        mobility = float(
            film_mobility_m3_pa_s(thickness, viscosity_pa_s, slip_length_m)
        )
        omega = float(angular_speed_rad_s(float(time_s)))
        return np.asarray([-2.0 * density_kg_m3 * omega**2 * mobility])

    solution = solve_ivp(
        rhs,
        (0.0, float(times[-1])),
        np.asarray([initial_thickness_m], dtype=np.float64),
        method="DOP853",
        t_eval=times,
        rtol=relative_tolerance,
        atol=absolute_tolerance_m,
    )
    if not solution.success or np.any(solution.y[0] <= 0.0):
        raise ThinFilmError(f"uniform drainage integration failed: {solution.message}")
    return np.asarray(solution.y[0], dtype=np.float64)


def piecewise_linear_value_and_rate(
    times_s: Sequence[float], values: Sequence[float], time_s: float
) -> tuple[float, float]:
    """Evaluate a finite piecewise-linear history and its one-sided slope."""

    times = np.asarray(times_s, dtype=np.float64)
    data = np.asarray(values, dtype=np.float64)
    if times.ndim != 1 or data.ndim != 1 or times.size != data.size or times.size < 2:
        raise ValueError("piecewise-linear history requires equal 1D arrays of length >= 2")
    if not np.all(np.isfinite(times)) or not np.all(np.isfinite(data)):
        raise ValueError("piecewise-linear history must be finite")
    if np.any(np.diff(times) <= 0.0):
        raise ValueError("piecewise-linear times must be strictly increasing")
    time = float(time_s)
    if time <= times[0]:
        index = 0
    elif time >= times[-1]:
        return float(data[-1]), 0.0
    else:
        index = int(np.searchsorted(times, time, side="right") - 1)
    dt = float(times[index + 1] - times[index])
    slope = float((data[index + 1] - data[index]) / dt)
    value = float(data[index] + slope * (time - times[index]))
    return value, slope


def wetting_energy_j_m2(
    thickness_m: object, pressure_pa: float, precursor_m: float
) -> FloatArray:
    """T6 wetting energy density W(h), rejecting nonpositive thickness."""

    thickness = np.asarray(thickness_m, dtype=np.float64)
    if np.any(~np.isfinite(thickness)) or np.any(thickness <= 0.0):
        raise NonpositiveFilmError("nonpositive film thickness; clipping is forbidden")
    ratio = float(precursor_m) / thickness
    return np.asarray(
        float(pressure_pa) * float(precursor_m) * (ratio**5 / 5.0 - ratio**2 / 2.0),
        dtype=np.float64,
    )


def wetting_pressure_pa(
    thickness_m: object, pressure_pa: float, precursor_m: float
) -> FloatArray:
    """T7 derivative W'(h)."""

    thickness = np.asarray(thickness_m, dtype=np.float64)
    if np.any(~np.isfinite(thickness)) or np.any(thickness <= 0.0):
        raise NonpositiveFilmError("nonpositive film thickness; clipping is forbidden")
    ratio = float(precursor_m) / thickness
    return np.asarray(float(pressure_pa) * (ratio**3 - ratio**6), dtype=np.float64)


def wetting_pressure_derivative_pa_m(
    thickness_m: object, pressure_pa: float, precursor_m: float
) -> FloatArray:
    """Analytic W''(h), used by the T13 modal reference."""

    thickness = np.asarray(thickness_m, dtype=np.float64)
    if np.any(~np.isfinite(thickness)) or np.any(thickness <= 0.0):
        raise NonpositiveFilmError("nonpositive film thickness; clipping is forbidden")
    ratio = float(precursor_m) / thickness
    return np.asarray(
        float(pressure_pa) / float(precursor_m) * (-3.0 * ratio**4 + 6.0 * ratio**7),
        dtype=np.float64,
    )


def film_mobility_m3_pa_s(
    thickness_m: object, viscosity_pa_s: float, slip_length_m: float
) -> FloatArray:
    """T4 Navier-slip mobility h^3/(3 mu) + b h^2/mu."""

    thickness = np.asarray(thickness_m, dtype=np.float64)
    if np.any(~np.isfinite(thickness)) or np.any(thickness <= 0.0):
        raise NonpositiveFilmError("nonpositive film thickness; clipping is forbidden")
    if viscosity_pa_s <= 0.0 or slip_length_m < 0.0:
        raise ValueError("viscosity must be positive and slip length nonnegative")
    return np.asarray(
        thickness**3 / (3.0 * viscosity_pa_s)
        + slip_length_m * thickness**2 / viscosity_pa_s,
        dtype=np.float64,
    )


def polynomial_squared_moments(coefficients: Sequence[float]) -> tuple[float, float, float]:
    """Return integral f, integral f^2 and the T5 weighted f^2 moment.

    The last result is ``integral_0^1 (eta-eta^2/2) f(eta)^2 d eta``.
    """

    coefficients_array = np.asarray(coefficients, dtype=np.float64)
    if coefficients_array.ndim != 1 or coefficients_array.size == 0:
        raise ValueError("swirl polynomial coefficients must be a nonempty 1D array")
    if not np.all(np.isfinite(coefficients_array)):
        raise ValueError("swirl polynomial coefficients must be finite")
    powers = np.arange(coefficients_array.size, dtype=np.float64)
    mean = float(np.sum(coefficients_array / (powers + 1.0)))
    squared = np.convolve(coefficients_array, coefficients_array)
    square_powers = np.arange(squared.size, dtype=np.float64)
    square_mean = float(np.sum(squared / (square_powers + 1.0)))
    weighted = float(
        np.sum(
            squared
            * (
                1.0 / (square_powers + 2.0)
                - 0.5 / (square_powers + 3.0)
            )
        )
    )
    return mean, square_mean, weighted


def weighted_swirl_flux_m2_s(
    thickness_m: object,
    radius_m: object,
    angular_speed_rad_s: float,
    density_kg_m3: float,
    viscosity_pa_s: float,
    slip_length_m: float,
    profile_coefficients: Sequence[float],
) -> FloatArray:
    """Evaluate T5 exactly for U_theta=omega*r*f(y/h)."""

    thickness = np.asarray(thickness_m, dtype=np.float64)
    radius = np.asarray(radius_m, dtype=np.float64)
    thickness, radius = np.broadcast_arrays(thickness, radius)
    if np.any(thickness <= 0.0) or np.any(~np.isfinite(thickness)):
        raise NonpositiveFilmError("nonpositive film thickness; clipping is forbidden")
    if np.any(radius < 0.0) or np.any(~np.isfinite(radius)):
        raise ValueError("radius must be finite and nonnegative")
    _, square_mean, weighted = polynomial_squared_moments(profile_coefficients)
    omega_sq = float(angular_speed_rad_s) ** 2
    prefactor = float(density_kg_m3) * omega_sq * radius / float(viscosity_pa_s)
    return np.asarray(
        prefactor
        * (
            float(slip_length_m) * thickness**2 * square_mean
            + thickness**3 * weighted
        ),
        dtype=np.float64,
    )


def weighted_swirl_flux_from_samples_m2_s(
    thickness_m: object,
    radius_m: object,
    depth_fraction: Sequence[float],
    tangential_velocity_m_s: object,
    density_kg_m3: float,
    viscosity_pa_s: float,
    slip_length_m: float,
) -> FloatArray:
    """Direct T5 quadrature for an externally supplied film swirl profile.

    The last axis of ``tangential_velocity_m_s`` corresponds to
    ``depth_fraction``.  At the axis the regular T5 limit is returned as zero;
    supplied axis velocities must themselves be zero to satisfy U_theta=O(r).
    """

    thickness = np.asarray(thickness_m, dtype=np.float64)
    radius = np.asarray(radius_m, dtype=np.float64)
    eta = np.asarray(depth_fraction, dtype=np.float64)
    velocity = np.asarray(tangential_velocity_m_s, dtype=np.float64)
    if eta.ndim != 1 or eta.size < 2 or eta[0] != 0.0 or eta[-1] != 1.0:
        raise ValueError("depth fractions must be a 1D array spanning [0, 1]")
    if np.any(np.diff(eta) <= 0.0):
        raise ValueError("depth fractions must be strictly increasing")
    shape = np.broadcast_shapes(thickness.shape, radius.shape)
    thickness = np.broadcast_to(thickness, shape)
    radius = np.broadcast_to(radius, shape)
    if velocity.shape != shape + (eta.size,):
        raise ValueError("swirl velocity shape must equal broadcast film shape plus depth")
    if np.any(thickness <= 0.0) or np.any(~np.isfinite(velocity)):
        raise NonpositiveFilmError("film and supplied swirl must be finite with positive thickness")
    axis = radius == 0.0
    if np.any(np.abs(velocity[axis]) > 1.0e-14):
        raise ValueError("axis swirl samples must vanish as U_theta=O(r)")
    kernel = (
        float(slip_length_m) * thickness[..., None] ** 2
        + thickness[..., None] ** 3 * (eta - 0.5 * eta**2)
    )
    integral = np.trapz(kernel * velocity**2, eta, axis=-1)
    result = np.zeros(shape, dtype=np.float64)
    off_axis = ~axis
    result[off_axis] = (
        float(density_kg_m3)
        * integral[off_axis]
        / (float(viscosity_pa_s) * radius[off_axis])
    )
    return result


class AxisymmetricThinFilm:
    """Second-order radial finite-volume operator with exact annular weights."""

    def __init__(
        self,
        *,
        radius_m: float,
        radial_cells: int,
        density_kg_m3: float,
        viscosity_pa_s: float,
        surface_tension_n_m: float,
        density_difference_kg_m3: float,
        gravity_m_s2: float,
        slip_length_m: float,
        precursor_thickness_m: float,
        wetting_pressure_scale_pa: float,
        boundary: ThinFilmBoundary,
        swirl_closure: SwirlClosure,
        angular_speed: Callable[[float], tuple[float, float]],
        swirl_profile_coefficients: Sequence[float] = (1.0,),
    ) -> None:
        if radius_m <= 0.0 or radial_cells < 2:
            raise ValueError("film radius and radial cell count must be positive")
        for name, value in (
            ("density", density_kg_m3),
            ("viscosity", viscosity_pa_s),
            ("surface tension", surface_tension_n_m),
            ("density difference", density_difference_kg_m3),
            ("gravity", gravity_m_s2),
            ("precursor thickness", precursor_thickness_m),
        ):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        if slip_length_m < 0.0 or wetting_pressure_scale_pa < 0.0:
            raise ValueError("slip and wetting-pressure scales must be nonnegative")
        if swirl_closure not in {"none", "corotation", "prescribed_depth_profile"}:
            raise ValueError("unknown thin-film swirl closure")

        self.radius_m = float(radius_m)
        self.radial_cells = int(radial_cells)
        self.dr_m = self.radius_m / self.radial_cells
        self.radius_faces_m = np.linspace(
            0.0, self.radius_m, self.radial_cells + 1, dtype=np.float64
        )
        self.radius_centres_m = 0.5 * (
            self.radius_faces_m[:-1] + self.radius_faces_m[1:]
        )
        self.annular_weights_m2 = 0.5 * (
            self.radius_faces_m[1:] ** 2 - self.radius_faces_m[:-1] ** 2
        )
        self.density_kg_m3 = float(density_kg_m3)
        self.viscosity_pa_s = float(viscosity_pa_s)
        self.surface_tension_n_m = float(surface_tension_n_m)
        self.density_difference_kg_m3 = float(density_difference_kg_m3)
        self.gravity_m_s2 = float(gravity_m_s2)
        self.slip_length_m = float(slip_length_m)
        self.precursor_thickness_m = float(precursor_thickness_m)
        self.wetting_pressure_scale_pa = float(wetting_pressure_scale_pa)
        self.boundary = boundary
        self.swirl_closure = swirl_closure
        self.angular_speed = angular_speed
        self.swirl_profile_coefficients = tuple(float(value) for value in swirl_profile_coefficients)
        polynomial_squared_moments(self.swirl_profile_coefficients)

    def _positive(self, thickness_m: object) -> FloatArray:
        thickness = np.asarray(thickness_m, dtype=np.float64)
        if thickness.shape != (self.radial_cells,):
            raise ValueError(
                f"film thickness must have shape ({self.radial_cells},)"
            )
        if np.any(~np.isfinite(thickness)) or np.any(thickness <= 0.0):
            raise NonpositiveFilmError("nonpositive film thickness; clipping is forbidden")
        return thickness

    def _outer_values(self, time_s: float) -> tuple[float, float, float]:
        if self.boundary.mode == "closed":
            return math.nan, math.nan, 0.0
        thickness = float(self.boundary.thickness(time_s))
        pressure = float(self.boundary.pressure(time_s))
        thickness_rate = float(self.boundary.thickness_rate(time_s))
        if thickness <= 0.0 or not all(
            math.isfinite(value) for value in (thickness, pressure, thickness_rate)
        ):
            raise NonpositiveFilmError("open-boundary data must be finite and positive")
        return thickness, pressure, thickness_rate

    def radial_laplacian(self, thickness_m: object, time_s: float) -> FloatArray:
        thickness = self._positive(thickness_m)
        slopes = np.zeros(self.radial_cells + 1, dtype=np.float64)
        slopes[1:-1] = np.diff(thickness) / self.dr_m
        if self.boundary.mode == "open":
            outer_h, _, _ = self._outer_values(time_s)
            slopes[-1] = 2.0 * (outer_h - thickness[-1]) / self.dr_m
        return np.diff(self.radius_faces_m * slopes) / self.annular_weights_m2

    def pressure_potential_pa(self, thickness_m: object, time_s: float) -> FloatArray:
        thickness = self._positive(thickness_m)
        return np.asarray(
            -self.surface_tension_n_m * self.radial_laplacian(thickness, time_s)
            + self.density_difference_kg_m3 * self.gravity_m_s2 * thickness
            + wetting_pressure_pa(
                thickness,
                self.wetting_pressure_scale_pa,
                self.precursor_thickness_m,
            ),
            dtype=np.float64,
        )

    def _potential_pa(self, radius_m: FloatArray, time_s: float) -> tuple[FloatArray, FloatArray]:
        omega, omega_rate = self.angular_speed(float(time_s))
        potential = 0.5 * self.density_kg_m3 * omega**2 * radius_m**2
        potential_rate = self.density_kg_m3 * omega * omega_rate * radius_m**2
        return np.asarray(potential), np.asarray(potential_rate)

    def energy_j(self, thickness_m: object, time_s: float) -> float:
        thickness = self._positive(thickness_m)
        differences = np.diff(thickness)
        surface_without_2pi = 0.5 * self.surface_tension_n_m * float(
            np.sum(self.radius_faces_m[1:-1] * differences**2 / self.dr_m)
        )
        if self.boundary.mode == "open":
            outer_h, _, _ = self._outer_values(time_s)
            surface_without_2pi += (
                self.surface_tension_n_m
                * self.radius_m
                * (outer_h - thickness[-1]) ** 2
                / self.dr_m
            )
        density = (
            0.5
            * self.density_difference_kg_m3
            * self.gravity_m_s2
            * thickness**2
            + wetting_energy_j_m2(
                thickness,
                self.wetting_pressure_scale_pa,
                self.precursor_thickness_m,
            )
        )
        if self.swirl_closure == "corotation":
            potential, _ = self._potential_pa(self.radius_centres_m, time_s)
            density = density - potential * thickness
        return float(
            2.0
            * math.pi
            * (surface_without_2pi + np.dot(self.annular_weights_m2, density))
        )

    def volume_m3(self, thickness_m: object) -> float:
        thickness = self._positive(thickness_m)
        return float(2.0 * math.pi * np.dot(self.annular_weights_m2, thickness))

    def evaluate(self, thickness_m: object, time_s: float) -> ThinFilmInstantaneous:
        thickness = self._positive(thickness_m)
        pressure = self.pressure_potential_pa(thickness, time_s)
        omega, _ = self.angular_speed(float(time_s))
        potential_centres = np.zeros_like(thickness)
        potential_rate = np.zeros_like(thickness)
        if self.swirl_closure == "corotation":
            potential_centres, potential_rate = self._potential_pa(
                self.radius_centres_m, time_s
            )
        chemical = pressure - potential_centres

        face_mobility = np.zeros(self.radial_cells + 1, dtype=np.float64)
        cell_mobility = film_mobility_m3_pa_s(
            thickness, self.viscosity_pa_s, self.slip_length_m
        )
        face_mobility[1:-1] = 0.5 * (cell_mobility[:-1] + cell_mobility[1:])
        gradient = np.zeros(self.radial_cells + 1, dtype=np.float64)
        gradient[1:-1] = np.diff(chemical) / self.dr_m
        dual_width = np.zeros(self.radial_cells + 1, dtype=np.float64)
        dual_width[1:-1] = self.dr_m
        swirl_flux = np.zeros(self.radial_cells + 1, dtype=np.float64)

        outer_h = outer_pressure = outer_h_rate = math.nan
        if self.boundary.mode == "open":
            outer_h, outer_pressure, outer_h_rate = self._outer_values(time_s)
            face_mobility[-1] = float(
                film_mobility_m3_pa_s(
                    outer_h, self.viscosity_pa_s, self.slip_length_m
                )
            )
            outer_chemical = outer_pressure
            if self.swirl_closure == "corotation":
                outer_potential, _ = self._potential_pa(
                    np.asarray([self.radius_m]), time_s
                )
                outer_chemical -= float(outer_potential[0])
            gradient[-1] = 2.0 * (outer_chemical - chemical[-1]) / self.dr_m
            dual_width[-1] = 0.5 * self.dr_m

        if self.swirl_closure == "prescribed_depth_profile":
            face_h = np.zeros(self.radial_cells + 1, dtype=np.float64)
            face_h[1:-1] = 0.5 * (thickness[:-1] + thickness[1:])
            if self.boundary.mode == "open":
                face_h[-1] = outer_h
            else:
                # The closed-edge flux is imposed as zero below, but keeping a
                # positive trace here lets the constitutive law be evaluated
                # without introducing a fake zero-thickness boundary state.
                face_h[-1] = thickness[-1]
            swirl_flux = weighted_swirl_flux_m2_s(
                face_h[1:],
                self.radius_faces_m[1:],
                omega,
                self.density_kg_m3,
                self.viscosity_pa_s,
                self.slip_length_m,
                self.swirl_profile_coefficients,
            )
            swirl_flux = np.concatenate((np.asarray([0.0]), swirl_flux))

        flux = -face_mobility * gradient + swirl_flux
        flux[0] = 0.0
        if self.boundary.mode == "closed":
            flux[-1] = 0.0
        thickness_rate = -np.diff(self.radius_faces_m * flux) / self.annular_weights_m2

        active = slice(1, None) if self.boundary.mode == "open" else slice(1, -1)
        dissipation = float(
            2.0
            * math.pi
            * np.sum(
                self.radius_faces_m[active]
                * face_mobility[active]
                * gradient[active] ** 2
                * dual_width[active]
            )
        )
        weighted_swirl_power = 0.0
        if self.swirl_closure == "prescribed_depth_profile":
            weighted_swirl_power = float(
                2.0
                * math.pi
                * np.sum(
                    self.radius_faces_m[active]
                    * gradient[active]
                    * swirl_flux[active]
                    * dual_width[active]
                )
            )
        drive_power = weighted_swirl_power
        if self.swirl_closure == "corotation":
            drive_power = float(
                -2.0
                * math.pi
                * np.dot(self.annular_weights_m2, thickness * potential_rate)
            )

        boundary_power = 0.0
        if self.boundary.mode == "open":
            outer_slope = 2.0 * (outer_h - thickness[-1]) / self.dr_m
            outer_chemical = outer_pressure
            if self.swirl_closure == "corotation":
                outer_potential, _ = self._potential_pa(
                    np.asarray([self.radius_m]), time_s
                )
                outer_chemical -= float(outer_potential[0])
            boundary_power = float(
                2.0
                * math.pi
                * (
                    self.surface_tension_n_m
                    * self.radius_m
                    * outer_slope
                    * outer_h_rate
                    - self.radius_m * outer_chemical * flux[-1]
                )
            )

        return ThinFilmInstantaneous(
            pressure_pa=pressure,
            chemical_potential_pa=chemical,
            radial_flux_m2_s=flux,
            thickness_rate_m_s=thickness_rate,
            energy_j=self.energy_j(thickness, time_s),
            volume_m3=self.volume_m3(thickness),
            dissipation_power_w=dissipation,
            drive_power_w=drive_power,
            boundary_power_w=boundary_power,
            boundary_outflow_m3_s=float(2.0 * math.pi * self.radius_m * flux[-1]),
            weighted_swirl_power_w=weighted_swirl_power,
        )


def integrate_positive_thin_film(
    operator: AxisymmetricThinFilm,
    initial_thickness_m: object,
    output_times_s: object,
    *,
    minimum_valid_thickness_m: float,
    maximum_time_step_s: float,
    minimum_time_step_s: float,
    relative_tolerance: float,
    absolute_tolerance_m: float,
    energy_absolute_tolerance_j: float,
    volume_absolute_tolerance_m3: float,
    maximum_step_rejections: int,
    checkpoint: Callable[[], bool] | None = None,
    diagnostic_continuation: bool = False,
) -> ThinFilmTrajectory:
    """Integrate the semidiscrete film and fail closed without thickness clips."""

    initial = operator._positive(initial_thickness_m).copy()
    times = np.asarray(output_times_s, dtype=np.float64)
    if times.ndim != 1 or times.size == 0 or times[0] != 0.0 or np.any(np.diff(times) <= 0.0):
        raise ValueError("output times must be a strictly increasing 1D array starting at zero")
    if np.min(initial) <= minimum_valid_thickness_m:
        raise ValueError("initial film must exceed minimum_valid_thickness_m")

    state = np.concatenate((initial, np.zeros(4, dtype=np.float64)))
    atol = np.concatenate(
        (
            np.full(operator.radial_cells, absolute_tolerance_m, dtype=np.float64),
            np.full(3, energy_absolute_tolerance_j, dtype=np.float64),
            np.asarray([volume_absolute_tolerance_m3], dtype=np.float64),
        )
    )

    saved_times = [0.0]
    saved_states = [state.copy()]
    status = "completed"
    reason = ""
    termination_interval: tuple[float, float] | None = None
    total_rejections = 0

    def right_hand_side(time_s: float, vector: FloatArray) -> FloatArray:
        thickness = vector[: operator.radial_cells]
        instantaneous = operator.evaluate(thickness, time_s)
        return np.concatenate(
            (
                instantaneous.thickness_rate_m_s,
                np.asarray(
                    [
                        instantaneous.dissipation_power_w,
                        instantaneous.drive_power_w,
                        instantaneous.boundary_power_w,
                        instantaneous.boundary_outflow_m3_s,
                    ],
                    dtype=np.float64,
                ),
            )
        )

    current_time = 0.0
    for target_time in times[1:]:
        if checkpoint is not None and not checkpoint():
            status = "stopped"
            reason = "cooperative stop requested"
            break
        trial_max_step = min(maximum_time_step_s, float(target_time - current_time))
        rejections_for_segment = 0
        while True:
            try:
                solution = solve_ivp(
                    right_hand_side,
                    (current_time, float(target_time)),
                    state,
                    method="BDF",
                    rtol=relative_tolerance,
                    atol=atol,
                    max_step=trial_max_step,
                )
                success = bool(solution.success)
                failure_text = solution.message
            except (NonpositiveFilmError, FloatingPointError, ValueError) as error:
                success = False
                failure_text = f"{type(error).__name__}: {error}"
                solution = None
            if success and solution is not None:
                break
            total_rejections += 1
            rejections_for_segment += 1
            trial_max_step *= 0.5
            if (
                rejections_for_segment > maximum_step_rejections
                or trial_max_step < minimum_time_step_s
            ):
                status = "numerical_failure"
                reason = f"BDF segment rejected without clipping: {failure_text}"
                termination_interval = (current_time, float(target_time))
                break
        if status != "completed":
            break

        assert solution is not None
        internal_minimum = np.min(solution.y[: operator.radial_cells], axis=0)
        invalid = np.flatnonzero(internal_minimum <= minimum_valid_thickness_m)
        if invalid.size and termination_interval is None:
            invalid_index = int(invalid[0])
            safe_index = max(0, invalid_index - 1)
            safe_time = float(solution.t[safe_index])
            invalid_time = float(solution.t[invalid_index])
            termination_interval = (safe_time, invalid_time)
            if not diagnostic_continuation:
                if safe_time > current_time + 1.0e-15:
                    state = solution.y[:, safe_index].copy()
                    saved_times.append(safe_time)
                    saved_states.append(state.copy())
                status = "physical_exit"
                reason = (
                    "minimum valid positive film thickness reached; "
                    "no dry-contact continuation"
                )
                break

        state = solution.y[:, -1].copy()
        current_time = float(target_time)
        saved_times.append(current_time)
        saved_states.append(state.copy())

    if status == "completed" and termination_interval is not None:
        status = "diagnostic_continuation"
        reason = (
            "continued the positive mathematical film below the configured "
            "quantitative-validity thickness; no clipping or dry contact"
        )

    stacked = np.asarray(saved_states, dtype=np.float64)
    n = operator.radial_cells
    return ThinFilmTrajectory(
        time_s=np.asarray(saved_times, dtype=np.float64),
        thickness_m=stacked[:, :n],
        cumulative_dissipation_j=stacked[:, n],
        cumulative_drive_work_j=stacked[:, n + 1],
        cumulative_boundary_work_j=stacked[:, n + 2],
        cumulative_boundary_outflow_m3=stacked[:, n + 3],
        status=status,
        step_rejections=total_rejections,
        termination_interval_s=termination_interval,
        reason=reason,
    )


__all__ = [
    "AxisymmetricThinFilm",
    "NonpositiveFilmError",
    "ThinFilmBoundary",
    "ThinFilmError",
    "ThinFilmInstantaneous",
    "ThinFilmTrajectory",
    "film_mobility_m3_pa_s",
    "integrate_positive_thin_film",
    "piecewise_linear_value_and_rate",
    "polynomial_squared_moments",
    "solve_uniform_drainage",
    "weighted_swirl_flux_from_samples_m2_s",
    "weighted_swirl_flux_m2_s",
    "wetting_energy_j_m2",
    "wetting_pressure_derivative_pa_m",
    "wetting_pressure_pa",
]
