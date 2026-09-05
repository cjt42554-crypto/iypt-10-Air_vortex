"""Evidence and geometry bounds for the CP gas-pressure approximation.

An absent bound is unknown, never a zero-pressure assumption.  Explicit
synthetic bounds are useful for manufactured cases but confer no apparatus
qualification.  The source and its finite domain travel with every result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GasModelConfig:
    regime: str = "auto"
    evidence_kind: str = "unassessed"
    evidence_source: str = ""
    evidence_valid_until_s: float | None = None
    pressure_error_budget_pa: float | None = None
    reservoir_pressure_error_bound_pa: float | None = None
    reservoir_reference_pressure_pa: float | None = None
    reservoir_max_funnel_depth_m: float | None = None
    reservoir_max_surface_slope: float | None = None
    channel_lower_z_m: float | None = None
    channel_upper_z_m: float | None = None
    channel_lower_speed_m_s: float = 0.0
    channel_lower_relative_flux_m3_s: float | None = None
    channel_section_count: int = 65
    channel_max_radius_length_ratio: float = 0.1
    channel_max_wall_slope: float = 0.2
    channel_viscous_quasisteady: bool = False
    channel_incompressible: bool = False
    channel_wall_resistance_justified: bool = False
    channel_derivative_pressure_error_bound_pa: float | None = None
    channel_initial_area_rate_m2_s: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        from .config import ConfigError, _require_real, _require_int

        if self.regime not in {"auto", "open_reservoir", "restricted_channel"}:
            raise ConfigError("gas.regime must be auto, open_reservoir or restricted_channel")
        if self.evidence_kind not in {
            "unassessed", "synthetic_assumption", "independent_measurement", "external_model"
        }:
            raise ConfigError("gas.evidence_kind is not a supported evidence identity")
        if not isinstance(self.evidence_source, str):
            raise ConfigError("gas.evidence_source must be a string")
        if self.evidence_kind != "unassessed" and not self.evidence_source.strip():
            raise ConfigError("assessed gas evidence requires a non-empty source")
        for name in (
            "evidence_valid_until_s", "pressure_error_budget_pa",
            "reservoir_pressure_error_bound_pa", "reservoir_reference_pressure_pa",
            "reservoir_max_funnel_depth_m", "reservoir_max_surface_slope",
            "channel_lower_z_m", "channel_upper_z_m",
            "channel_derivative_pressure_error_bound_pa",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_real(f"gas.{name}", value, minimum=0.0)
                object.__setattr__(self, name, float(value))
        if self.reservoir_reference_pressure_pa == 0.0:
            raise ConfigError("gas.reservoir_reference_pressure_pa must be positive")
        if self.pressure_error_budget_pa == 0.0:
            raise ConfigError("gas.pressure_error_budget_pa must be positive")
        _require_real("gas.channel_lower_speed_m_s", self.channel_lower_speed_m_s)
        object.__setattr__(self, "channel_lower_speed_m_s", float(self.channel_lower_speed_m_s))
        if self.channel_lower_relative_flux_m3_s is not None:
            _require_real("gas.channel_lower_relative_flux_m3_s", self.channel_lower_relative_flux_m3_s)
            object.__setattr__(self, "channel_lower_relative_flux_m3_s", float(self.channel_lower_relative_flux_m3_s))
        _require_int("gas.channel_section_count", self.channel_section_count, minimum=3)
        if self.channel_section_count > 65537:
            raise ConfigError("gas.channel_section_count must be <= 65537")
        for name in ("channel_max_radius_length_ratio", "channel_max_wall_slope"):
            _require_real(f"gas.{name}", getattr(self, name), minimum=0.0, strict=True, maximum=1.0)
            object.__setattr__(self, name, float(getattr(self, name)))
        if self.channel_lower_z_m is not None and self.channel_upper_z_m is not None:
            if self.channel_lower_z_m >= self.channel_upper_z_m:
                raise ConfigError("gas channel lower endpoint must be below upper endpoint")
        for name in (
            "channel_viscous_quasisteady", "channel_incompressible",
            "channel_wall_resistance_justified",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ConfigError(f"gas.{name} must be boolean")
        if not isinstance(self.channel_initial_area_rate_m2_s, (tuple, list)):
            raise ConfigError("gas.channel_initial_area_rate_m2_s must be a sequence")
        if self.channel_initial_area_rate_m2_s and len(self.channel_initial_area_rate_m2_s) != self.channel_section_count:
            raise ConfigError("gas initial area-rate length must equal channel_section_count")
        for value in self.channel_initial_area_rate_m2_s:
            _require_real("gas.channel_initial_area_rate_m2_s[]", value)
        object.__setattr__(self, "channel_initial_area_rate_m2_s", tuple(float(v) for v in self.channel_initial_area_rate_m2_s))

    @classmethod
    def from_dict(cls, raw: object) -> "GasModelConfig":
        from .config import _decode_dataclass
        return _decode_dataclass(cls, raw, "gas")

    def to_dict(self) -> dict[str, Any]:
        from .config import _encode_dataclass
        return _encode_dataclass(self)
