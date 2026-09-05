"""Configuration for the local positive-thickness thin-film branch.

The configuration lives in its own module so that ``SimulationConfig`` can
embed it without mixing the AV-FWD-TF-v1 physical parameters with the older
macroscopic forward closures.  Every length and pressure below is an SI
quantity.  Tuple fields serialise as JSON arrays.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
from typing import Any, Mapping

from .config import ConfigError


THEORY_ID = "AV-FWD-TF-v1"


def _real(
    name: str,
    value: object,
    *,
    minimum: float | None = None,
    strict: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError(f"{name} must be finite")
    if minimum is not None:
        if strict and not result > minimum:
            raise ConfigError(f"{name} must be > {minimum}")
        if not strict and result < minimum:
            raise ConfigError(f"{name} must be >= {minimum}")
    return result


def _integer(name: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _real_tuple(name: str, value: object) -> tuple[float, ...]:
    if not isinstance(value, (tuple, list)):
        raise ConfigError(f"{name} must be an array")
    return tuple(_real(f"{name}[{index}]", item) for index, item in enumerate(value))


def _strictly_increasing(name: str, values: tuple[float, ...]) -> None:
    if any(right <= left for left, right in zip(values, values[1:])):
        raise ConfigError(f"{name} must be strictly increasing")


@dataclass(frozen=True, slots=True)
class FilmModelConfig:
    """Physical initial-boundary data and numerical controls for AV-FWD-TF-v1.

    ``boundary_condition`` is either ``"closed"`` (zero outer flux and slope)
    or ``"open"`` (prescribed outer thickness and pressure-potential history).
    The optional initial profile is sampled at the supplied physical radii and
    interpolated to cell centres.  Otherwise a uniform thickness plus an
    allowed closed-disk J0 mode is used.

    ``prescribed_depth_profile`` represents
    ``U_theta = omega(t) * r * f(y/h)`` with ``f`` supplied as polynomial
    coefficients in ascending powers.  The backend evaluates the T5 weighted
    integral of ``f**2``; it never substitutes the square of its depth mean.
    """

    domain_radius_m: float = 0.003
    radial_cells: int = 64
    boundary_condition: str = "closed"

    initial_thickness_m: float = 1.0e-4
    initial_perturbation_amplitude_m: float = 0.0
    initial_perturbation_mode: int = 1
    initial_profile_radius_m: tuple[float, ...] = ()
    initial_profile_thickness_m: tuple[float, ...] = ()

    outer_thickness_m: float = 1.0e-4
    outer_pressure_pa: float = 0.0
    outer_boundary_time_s: tuple[float, ...] = ()
    outer_thickness_schedule_m: tuple[float, ...] = ()
    outer_pressure_schedule_pa: tuple[float, ...] = ()

    slip_length_m: float = 0.0
    precursor_thickness_m: float = 1.0e-5
    wetting_pressure_pa: float = 1.0
    minimum_valid_thickness_m: float = 1.0e-9
    footprint_threshold_m: float = 2.0e-5

    swirl_closure: str = "corotation"
    swirl_profile_coefficients: tuple[float, ...] = (1.0,)
    prescribed_swirl_time_s: tuple[float, ...] = ()
    prescribed_swirl_angular_speed_rad_s: tuple[float, ...] = ()

    maximum_time_step_s: float = 5.0e-3
    minimum_time_step_s: float = 1.0e-9
    relative_tolerance: float = 1.0e-7
    absolute_tolerance_m: float = 1.0e-11
    energy_absolute_tolerance_j: float = 1.0e-14
    volume_absolute_tolerance_m3: float = 1.0e-16
    maximum_step_rejections: int = 12

    def __post_init__(self) -> None:
        for name in (
            "domain_radius_m",
            "initial_thickness_m",
            "precursor_thickness_m",
            "minimum_valid_thickness_m",
            "footprint_threshold_m",
            "maximum_time_step_s",
            "minimum_time_step_s",
            "relative_tolerance",
            "absolute_tolerance_m",
            "energy_absolute_tolerance_j",
            "volume_absolute_tolerance_m3",
        ):
            object.__setattr__(
                self, name, _real(f"film.{name}", getattr(self, name), minimum=0.0, strict=True)
            )
        for name in (
            "initial_perturbation_amplitude_m",
            "outer_pressure_pa",
        ):
            object.__setattr__(self, name, _real(f"film.{name}", getattr(self, name)))
        for name in (
            "outer_thickness_m",
            "slip_length_m",
            "wetting_pressure_pa",
        ):
            object.__setattr__(
                self, name, _real(f"film.{name}", getattr(self, name), minimum=0.0)
            )

        _integer("film.radial_cells", self.radial_cells, minimum=8)
        _integer("film.initial_perturbation_mode", self.initial_perturbation_mode, minimum=1)
        _integer("film.maximum_step_rejections", self.maximum_step_rejections, minimum=0)
        if self.minimum_time_step_s > self.maximum_time_step_s:
            raise ConfigError("film.minimum_time_step_s cannot exceed maximum_time_step_s")
        if self.minimum_valid_thickness_m >= self.initial_thickness_m and not self.initial_profile_thickness_m:
            raise ConfigError(
                "film.minimum_valid_thickness_m must be below the nominal initial thickness"
            )
        if self.footprint_threshold_m <= self.precursor_thickness_m:
            raise ConfigError(
                "film.footprint_threshold_m must exceed precursor_thickness_m"
            )

        boundary = self.boundary_condition.strip().lower() if isinstance(self.boundary_condition, str) else ""
        if boundary not in {"closed", "open"}:
            raise ConfigError("film.boundary_condition must be 'closed' or 'open'")
        object.__setattr__(self, "boundary_condition", boundary)

        profile_r = _real_tuple("film.initial_profile_radius_m", self.initial_profile_radius_m)
        profile_h = _real_tuple("film.initial_profile_thickness_m", self.initial_profile_thickness_m)
        if bool(profile_r) != bool(profile_h):
            raise ConfigError("film initial-profile radius and thickness arrays must both be supplied")
        if profile_r:
            if len(profile_r) != len(profile_h) or len(profile_r) < 2:
                raise ConfigError("film initial profile requires equal arrays with at least two points")
            _strictly_increasing("film.initial_profile_radius_m", profile_r)
            if not math.isclose(profile_r[0], 0.0, rel_tol=0.0, abs_tol=1.0e-15):
                raise ConfigError("film initial-profile radius must start at zero")
            if not math.isclose(
                profile_r[-1], self.domain_radius_m, rel_tol=0.0, abs_tol=1.0e-12 * self.domain_radius_m
            ):
                raise ConfigError("film initial-profile radius must end at domain_radius_m")
            if any(value <= self.minimum_valid_thickness_m for value in profile_h):
                raise ConfigError("film initial profile must exceed minimum_valid_thickness_m")
            if self.initial_perturbation_amplitude_m != 0.0:
                raise ConfigError("film explicit initial profile cannot be combined with perturbation amplitude")
        elif abs(self.initial_perturbation_amplitude_m) >= (
            self.initial_thickness_m - self.minimum_valid_thickness_m
        ):
            raise ConfigError("film initial perturbation does not preserve positive valid thickness")
        if boundary == "open" and self.initial_perturbation_amplitude_m != 0.0:
            raise ConfigError("film J0 perturbation shortcut is only defined for the closed boundary")
        object.__setattr__(self, "initial_profile_radius_m", profile_r)
        object.__setattr__(self, "initial_profile_thickness_m", profile_h)

        times = _real_tuple("film.outer_boundary_time_s", self.outer_boundary_time_s)
        outer_h = _real_tuple("film.outer_thickness_schedule_m", self.outer_thickness_schedule_m)
        outer_p = _real_tuple("film.outer_pressure_schedule_pa", self.outer_pressure_schedule_pa)
        if any((times, outer_h, outer_p)):
            if not times or len(times) < 2 or len(times) != len(outer_h) or len(times) != len(outer_p):
                raise ConfigError(
                    "film open-boundary schedules require equal time, thickness and pressure arrays"
                )
            if times[0] != 0.0:
                raise ConfigError("film outer-boundary schedule must start at t=0")
            _strictly_increasing("film.outer_boundary_time_s", times)
            if any(value <= self.minimum_valid_thickness_m for value in outer_h):
                raise ConfigError("film outer thickness schedule must remain above minimum_valid_thickness_m")
        elif self.outer_thickness_m <= self.minimum_valid_thickness_m:
            raise ConfigError("film.outer_thickness_m must exceed minimum_valid_thickness_m")
        object.__setattr__(self, "outer_boundary_time_s", times)
        object.__setattr__(self, "outer_thickness_schedule_m", outer_h)
        object.__setattr__(self, "outer_pressure_schedule_pa", outer_p)

        closure = self.swirl_closure.strip().lower() if isinstance(self.swirl_closure, str) else ""
        if closure not in {"none", "corotation", "prescribed_depth_profile"}:
            raise ConfigError(
                "film.swirl_closure must be 'none', 'corotation' or 'prescribed_depth_profile'"
            )
        object.__setattr__(self, "swirl_closure", closure)
        coefficients = _real_tuple(
            "film.swirl_profile_coefficients", self.swirl_profile_coefficients
        )
        if not coefficients:
            raise ConfigError("film.swirl_profile_coefficients cannot be empty")
        if closure == "prescribed_depth_profile":
            surface_derivative = sum(
                power * coefficient for power, coefficient in enumerate(coefficients)
            )
            scale = max(1.0, sum(abs(value) for value in coefficients))
            if abs(surface_derivative) > 1.0e-12 * scale:
                raise ConfigError(
                    "film prescribed swirl polynomial must have zero derivative at y/h=1"
                )
        object.__setattr__(self, "swirl_profile_coefficients", coefficients)

        swirl_t = _real_tuple("film.prescribed_swirl_time_s", self.prescribed_swirl_time_s)
        swirl_w = _real_tuple(
            "film.prescribed_swirl_angular_speed_rad_s",
            self.prescribed_swirl_angular_speed_rad_s,
        )
        if bool(swirl_t) != bool(swirl_w):
            raise ConfigError("film prescribed swirl time and angular-speed arrays must both be supplied")
        if swirl_t:
            if closure != "prescribed_depth_profile":
                raise ConfigError(
                    "film prescribed swirl schedule requires prescribed_depth_profile closure"
                )
            if len(swirl_t) != len(swirl_w) or len(swirl_t) < 2:
                raise ConfigError("film prescribed swirl schedule requires at least two points")
            if swirl_t[0] != 0.0:
                raise ConfigError("film prescribed swirl schedule must start at t=0")
            _strictly_increasing("film.prescribed_swirl_time_s", swirl_t)
        object.__setattr__(self, "prescribed_swirl_time_s", swirl_t)
        object.__setattr__(self, "prescribed_swirl_angular_speed_rad_s", swirl_w)

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-ready representation."""

        result: dict[str, Any] = {}
        for descriptor in fields(self):
            value = getattr(self, descriptor.name)
            result[descriptor.name] = list(value) if isinstance(value, tuple) else value
        return result

    @classmethod
    def from_dict(cls, raw: object) -> "FilmModelConfig":
        """Strictly decode a JSON object, rejecting misspelled fields."""

        if not isinstance(raw, Mapping):
            raise ConfigError("film must be an object")
        allowed = {descriptor.name for descriptor in fields(cls)}
        unknown = set(raw) - allowed
        if unknown:
            raise ConfigError(f"film has unknown fields: {', '.join(sorted(unknown))}")
        tuple_names = {
            descriptor.name
            for descriptor in fields(cls)
            if descriptor.name.endswith(("_m", "_pa", "_s", "_rad_s", "coefficients"))
            and isinstance(descriptor.default, tuple)
        }
        values: dict[str, Any] = {}
        for key, value in raw.items():
            values[key] = tuple(value) if key in tuple_names and isinstance(value, list) else value
        return cls(**values)


__all__ = ["FilmModelConfig", "THEORY_ID"]
