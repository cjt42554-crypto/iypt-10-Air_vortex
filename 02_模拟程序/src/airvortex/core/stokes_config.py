"""Configuration owned by the AV-FWD-SH-v1 Stokes branch.

The module is deliberately independent of :mod:`airvortex.core.config` so it
can be imported by that module without a circular import.  ``SimulationConfig``
adds this dataclass as its ``stokes`` member at the integration boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
from typing import Any, Mapping


STOKES_MODEL_ID = "AV-FWD-SH-v1"


class StokesConfigError(ValueError):
    """A Stokes input is missing, ill typed, or outside the implemented model."""


def _real(
    name: str,
    value: object,
    *,
    minimum: float | None = None,
    strict: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StokesConfigError(f"stokes.{name} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise StokesConfigError(f"stokes.{name} must be finite")
    if minimum is not None:
        valid = result > minimum if strict else result >= minimum
        if not valid:
            relation = ">" if strict else ">="
            raise StokesConfigError(f"stokes.{name} must be {relation} {minimum}")
    return result


def _integer(name: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StokesConfigError(f"stokes.{name} must be an integer")
    if value < minimum:
        raise StokesConfigError(f"stokes.{name} must be >= {minimum}")
    return value


def _digest(name: str, value: str) -> str:
    digest = value.strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise StokesConfigError(
            f"stokes.{name} must contain exactly 64 hexadecimal characters"
        )
    return digest


@dataclass(frozen=True, slots=True)
class StokesModelConfig:
    """Inputs and numerical controls for the conditional SH implementation.

    ``linear_flat_cylinder`` advances a small-slope surface over a fixed flat
    reference cylinder.  It is a moving-interface Stokes calculation, but is
    not a nonlinear moving-domain/contact-line solver.  The only currently
    supported solid-wall condition is no slip with a fixed contact line;
    unsupported alternatives are rejected instead of being aliased.

    A nonzero Reynolds covariance is accepted only as an independently bound
    NPZ input.  The file must contain ``time_s``, ``radius_m``, ``z_m`` and the
    four SI covariance fields ``R_rr_m2_s2``, ``R_rz_m2_s2``,
    ``R_tt_m2_s2`` and ``R_zz_m2_s2``.
    """

    model_id: str = STOKES_MODEL_ID
    domain_model: str = "linear_flat_cylinder"
    radial_cells: int = 16
    axial_cells: int = 12
    time_step_s: float = 5.0e-3
    fixed_contact_line: bool = True
    side_slip_length_m: float = 0.0
    bottom_slip_length_m: float = 0.0
    initial_surface_mode: int = 0
    initial_surface_amplitude_m: float = 0.0
    initial_surface_npz_path: str = ""
    initial_surface_sha256: str = ""
    covariance_mode: str = "zero"
    covariance_npz_path: str = ""
    covariance_sha256: str = ""
    covariance_source: str = ""
    covariance_relative_uncertainty: float = 0.0
    maximum_surface_slope: float = 0.10
    maximum_amplitude_depth_ratio: float = 0.10
    maximum_meridional_reynolds: float = 0.10
    maximum_unsteady_stokes: float = 0.10
    maximum_inertia_residual_ratio: float = 0.10
    linear_solver_relative_tolerance: float = 1.0e-9

    def __post_init__(self) -> None:
        if self.model_id != STOKES_MODEL_ID:
            raise StokesConfigError(
                f"stokes.model_id must be {STOKES_MODEL_ID!r}"
            )
        if self.domain_model != "linear_flat_cylinder":
            raise StokesConfigError(
                "stokes.domain_model must be 'linear_flat_cylinder'; "
                "nonlinear moving-domain SH is not implemented"
            )
        _integer("radial_cells", self.radial_cells, minimum=6)
        _integer("axial_cells", self.axial_cells, minimum=6)
        _integer("initial_surface_mode", self.initial_surface_mode, minimum=0)
        if not isinstance(self.fixed_contact_line, bool):
            raise StokesConfigError("stokes.fixed_contact_line must be a boolean")
        if not self.fixed_contact_line:
            raise StokesConfigError(
                "stokes.fixed_contact_line=false is unsupported by the "
                "linear-flat-cylinder implementation"
            )
        for name in (
            "time_step_s",
            "maximum_surface_slope",
            "maximum_amplitude_depth_ratio",
            "maximum_meridional_reynolds",
            "maximum_unsteady_stokes",
            "maximum_inertia_residual_ratio",
            "linear_solver_relative_tolerance",
        ):
            object.__setattr__(
                self, name, _real(name, getattr(self, name), minimum=0.0, strict=True)
            )
        for name in (
            "side_slip_length_m",
            "bottom_slip_length_m",
            "covariance_relative_uncertainty",
        ):
            object.__setattr__(self, name, _real(name, getattr(self, name), minimum=0.0))
        object.__setattr__(
            self,
            "initial_surface_amplitude_m",
            _real("initial_surface_amplitude_m", self.initial_surface_amplitude_m),
        )
        if self.side_slip_length_m != 0.0 or self.bottom_slip_length_m != 0.0:
            raise StokesConfigError(
                "the present fixed-contact benchmark supports no-slip side and "
                "bottom walls only; nonzero physical slip is not yet implemented"
            )
        for name in (
            "initial_surface_npz_path",
            "initial_surface_sha256",
            "covariance_mode",
            "covariance_npz_path",
            "covariance_sha256",
            "covariance_source",
        ):
            if not isinstance(getattr(self, name), str):
                raise StokesConfigError(f"stokes.{name} must be a string")

        initial_path = self.initial_surface_npz_path.strip()
        initial_hash = self.initial_surface_sha256.strip()
        if bool(initial_path) != bool(initial_hash):
            raise StokesConfigError(
                "stokes initial-surface path and SHA-256 must be supplied together"
            )
        if initial_path:
            if self.initial_surface_mode != 0 or self.initial_surface_amplitude_m != 0.0:
                raise StokesConfigError(
                    "an initial-surface NPZ cannot be combined with a synthetic mode"
                )
            object.__setattr__(self, "initial_surface_npz_path", initial_path)
            object.__setattr__(
                self, "initial_surface_sha256", _digest("initial_surface_sha256", initial_hash)
            )
        elif self.initial_surface_mode == 0 and self.initial_surface_amplitude_m != 0.0:
            raise StokesConfigError(
                "stokes.initial_surface_amplitude_m must be zero when mode is zero"
            )

        covariance_mode = self.covariance_mode.strip().lower()
        if covariance_mode not in {"zero", "npz"}:
            raise StokesConfigError("stokes.covariance_mode must be 'zero' or 'npz'")
        object.__setattr__(self, "covariance_mode", covariance_mode)
        covariance_fields = (
            self.covariance_npz_path.strip(),
            self.covariance_sha256.strip(),
            self.covariance_source.strip(),
        )
        if covariance_mode == "zero":
            if any(covariance_fields) or self.covariance_relative_uncertainty != 0.0:
                raise StokesConfigError(
                    "zero covariance must not carry an NPZ, source, or uncertainty"
                )
        else:
            if not all(covariance_fields):
                raise StokesConfigError(
                    "nonzero covariance requires an independent NPZ path, SHA-256, "
                    "and source description"
                )
            object.__setattr__(self, "covariance_npz_path", covariance_fields[0])
            object.__setattr__(
                self, "covariance_sha256", _digest("covariance_sha256", covariance_fields[1])
            )
            object.__setattr__(self, "covariance_source", covariance_fields[2])

    def to_dict(self) -> dict[str, Any]:
        """Return a strict JSON-compatible mapping."""

        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_dict(cls, raw: object) -> "StokesModelConfig":
        """Decode a mapping with unknown-field and scalar-type rejection."""

        if not isinstance(raw, Mapping):
            raise StokesConfigError("stokes config must be an object")
        if any(not isinstance(key, str) for key in raw):
            raise StokesConfigError("stokes config keys must be strings")
        allowed = {field.name for field in fields(cls)}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise StokesConfigError(
                f"stokes config contains unknown field(s): {', '.join(unknown)}"
            )
        integer_fields = {"radial_cells", "axial_cells", "initial_surface_mode"}
        boolean_fields = {"fixed_contact_line"}
        string_fields = {
            "model_id",
            "domain_model",
            "initial_surface_npz_path",
            "initial_surface_sha256",
            "covariance_mode",
            "covariance_npz_path",
            "covariance_sha256",
            "covariance_source",
        }
        values: dict[str, object] = {}
        for name, value in raw.items():
            if name in integer_fields:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise StokesConfigError(f"stokes.{name} must be an integer")
            elif name in boolean_fields:
                if not isinstance(value, bool):
                    raise StokesConfigError(f"stokes.{name} must be a boolean")
            elif name in string_fields:
                if not isinstance(value, str):
                    raise StokesConfigError(f"stokes.{name} must be a string")
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                raise StokesConfigError(f"stokes.{name} must be a real number")
            values[name] = value
        return cls(**values)  # type: ignore[arg-type]


__all__ = ["STOKES_MODEL_ID", "StokesConfigError", "StokesModelConfig"]
