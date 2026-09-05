"""Strict, SI-only configuration for the magnetic-stirrer air-vortex problem."""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
import math
from types import UnionType
from typing import Any, Mapping, TypeVar, Union, get_args, get_origin, get_type_hints


CONFIG_SCHEMA_VERSION = 2
PROBLEM_ID = "iypt-2027-10-air-vortex"


class ConfigError(ValueError):
    """Raised when a configuration is incomplete, ill-typed, or unphysical."""


class BackendMode(str, Enum):
    """The two calculation paths required by the common research platform."""

    FORWARD = "forward"
    HIGH_FIDELITY = "high_fidelity"


def _require_real(name: str, value: object, *, minimum: float | None = None,
                  strict: bool = False, maximum: float | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a real number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ConfigError(f"{name} must be finite")
    if minimum is not None:
        if strict and not numeric > minimum:
            raise ConfigError(f"{name} must be > {minimum}")
        if not strict and numeric < minimum:
            raise ConfigError(f"{name} must be >= {minimum}")
    if maximum is not None and numeric > maximum:
        raise ConfigError(f"{name} must be <= {maximum}")


def _require_int(name: str, value: object, *, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")


def _normalise_reals(instance: object, names: tuple[str, ...]) -> None:
    """Keep direct construction and JSON decoding byte-for-byte reproducible."""

    for name in names:
        object.__setattr__(instance, name, float(getattr(instance, name)))


@dataclass(frozen=True, slots=True)
class ContainerConfig:
    """Vertical cylindrical vessel dimensions, in metres."""

    radius_m: float = 0.045
    liquid_depth_m: float = 0.060
    headspace_height_m: float = 0.020

    def __post_init__(self) -> None:
        _require_real("container.radius_m", self.radius_m, minimum=0.0, strict=True)
        _require_real(
            "container.liquid_depth_m", self.liquid_depth_m, minimum=0.0, strict=True
        )
        _require_real(
            "container.headspace_height_m",
            self.headspace_height_m,
            minimum=0.0,
            strict=True,
        )
        _normalise_reals(
            self, ("radius_m", "liquid_depth_m", "headspace_height_m")
        )


@dataclass(frozen=True, slots=True)
class FluidConfig:
    """Newtonian liquid/gas properties, all expressed in SI units."""

    liquid_density_kg_m3: float = 998.2
    liquid_dynamic_viscosity_pa_s: float = 1.002e-3
    gas_density_kg_m3: float = 1.204
    gas_dynamic_viscosity_pa_s: float = 1.825e-5
    surface_tension_n_m: float = 0.0720
    gravity_m_s2: float = 9.80665

    def __post_init__(self) -> None:
        for name in (
            "liquid_density_kg_m3",
            "liquid_dynamic_viscosity_pa_s",
            "gas_density_kg_m3",
            "gas_dynamic_viscosity_pa_s",
            "surface_tension_n_m",
            "gravity_m_s2",
        ):
            _require_real(f"fluid.{name}", getattr(self, name), minimum=0.0, strict=True)
        if self.liquid_density_kg_m3 <= self.gas_density_kg_m3:
            raise ConfigError("fluid liquid density must exceed gas density")
        _normalise_reals(
            self,
            (
                "liquid_density_kg_m3",
                "liquid_dynamic_viscosity_pa_s",
                "gas_density_kg_m3",
                "gas_dynamic_viscosity_pa_s",
                "surface_tension_n_m",
                "gravity_m_s2",
            ),
        )


@dataclass(frozen=True, slots=True)
class StirBarConfig:
    """Half-length/radius approximation of the horizontal magnetic stir bar."""

    half_length_m: float = 0.015
    radius_m: float = 0.003
    bottom_clearance_m: float = 0.0015

    def __post_init__(self) -> None:
        _require_real("stir_bar.half_length_m", self.half_length_m, minimum=0.0, strict=True)
        _require_real("stir_bar.radius_m", self.radius_m, minimum=0.0, strict=True)
        _require_real(
            "stir_bar.bottom_clearance_m", self.bottom_clearance_m, minimum=0.0
        )
        _normalise_reals(self, ("half_length_m", "radius_m", "bottom_clearance_m"))


@dataclass(frozen=True, slots=True)
class DriveConfig:
    """Prescribed stir-bar speed; use measured bar rpm whenever magnetic slip occurs."""

    initial_rpm: float = 0.0
    target_rpm: float = 900.0
    ramp_duration_s: float = 2.0
    rotation_direction: int = 1
    actual_speed_schedule_time_s: tuple[float, ...] = ()
    actual_speed_schedule_rpm: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        _require_real("drive.initial_rpm", self.initial_rpm, minimum=0.0)
        _require_real("drive.target_rpm", self.target_rpm, minimum=0.0)
        _require_real("drive.ramp_duration_s", self.ramp_duration_s, minimum=0.0)
        if isinstance(self.rotation_direction, bool) or self.rotation_direction not in (-1, 1):
            raise ConfigError("drive.rotation_direction must be either -1 or 1")
        times = tuple(self.actual_speed_schedule_time_s)
        speeds = tuple(self.actual_speed_schedule_rpm)
        if bool(times) != bool(speeds):
            raise ConfigError(
                "drive actual-speed schedule times and rpm must either both be empty "
                "or both be supplied"
            )
        if times:
            if len(times) != len(speeds) or len(times) < 2:
                raise ConfigError(
                    "drive actual-speed schedule requires equal arrays with at least two points"
                )
            for index, value in enumerate(times):
                _require_real(
                    f"drive.actual_speed_schedule_time_s[{index}]", value, minimum=0.0
                )
            for index, value in enumerate(speeds):
                _require_real(f"drive.actual_speed_schedule_rpm[{index}]", value)
            if float(times[0]) != 0.0:
                raise ConfigError("drive actual-speed schedule must start at t=0")
            if any(float(right) <= float(left) for left, right in zip(times, times[1:])):
                raise ConfigError(
                    "drive actual-speed schedule times must be strictly increasing"
                )
            object.__setattr__(
                self, "actual_speed_schedule_time_s", tuple(float(item) for item in times)
            )
            object.__setattr__(
                self, "actual_speed_schedule_rpm", tuple(float(item) for item in speeds)
            )
        _normalise_reals(self, ("initial_rpm", "target_rpm", "ramp_duration_s"))


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Time horizon shared by both backends."""

    duration_s: float = 10.0
    output_interval_s: float = 0.02

    def __post_init__(self) -> None:
        _require_real("run.duration_s", self.duration_s, minimum=0.0, strict=True)
        _require_real(
            "run.output_interval_s", self.output_interval_s, minimum=0.0, strict=True
        )
        if self.output_interval_s > self.duration_s:
            raise ConfigError("run.output_interval_s cannot exceed run.duration_s")
        _normalise_reals(self, ("duration_s", "output_interval_s"))


@dataclass(frozen=True, slots=True)
class ForwardModelConfig:
    """Physical closures and discretisation controls for forward theory F1.0.

    The first nine fields are retained for the independently testable legacy
    graph solver.  The topology-capable reference implementation consumes the
    complete closure below; numerical controls are named explicitly so that
    they cannot be mistaken for physical fit parameters.
    """

    circulation_coupling_m2: float = 9.0e-5
    effective_core_radius_m: float = 0.010
    azimuthal_decay_power: float = 1.0
    spin_up_time_s: float = 1.2
    surface_response_time_s: float = 0.0
    air_core_depth_threshold_m: float = 0.001
    surface_radial_points: int = 256
    integration_time_step_s: float = 1.0e-3
    relative_tolerance: float = 1.0e-6
    absolute_tolerance: float = 1.0e-9
    initial_circulation_m2_s: float = 0.0
    initial_state_npz_path: str = ""
    initial_state_sha256: str = ""
    axial_swirl_floor: float = 1.0
    axial_swirl_decay_length_m: float = 0.020
    interface_mobility_m_pa_s: float = 2.0e-4
    effective_target_radius_m: float = 0.003
    wall_receding_angle_deg: float = 80.0
    wall_equilibrium_angle_deg: float = 90.0
    wall_advancing_angle_deg: float = 100.0
    wall_contact_friction_pa_s: float = 0.20
    floor_receding_angle_deg: float = 80.0
    floor_equilibrium_angle_deg: float = 90.0
    floor_advancing_angle_deg: float = 100.0
    floor_contact_friction_pa_s: float = 0.20
    target_receding_angle_deg: float = 70.0
    target_equilibrium_angle_deg: float = 85.0
    target_advancing_angle_deg: float = 100.0
    target_contact_friction_pa_s: float = 0.20
    stable_channel_radius_m: float = 0.0015
    stable_channel_hold_time_s: float = 0.50
    top_seed_depth_m: float = 0.004
    swirl_profile_error_max: float = 0.15
    axisymmetry_error_max: float = 0.10
    wave_envelope_error_max: float = 0.20
    gas_pressure_error_max: float = 0.10
    radial_cells: int = 64
    axial_cells: int = 112
    interface_thickness_cells: float = 1.5
    minimum_resolved_throat_cells: int = 4
    max_phase_change_per_step: float = 0.04
    volume_relative_tolerance: float = 5.0e-8
    energy_relative_tolerance: float = 2.0e-10
    maximum_step_rejections: int = 18
    model_branch: str = "AV-FWD-CP-v2"
    prediction_mode: str = "strict"
    validity_event_tolerance_s: float = 1.0e-5

    def __post_init__(self) -> None:
        if self.model_branch not in {
            "AV-FWD-CP-v2", "AV-FWD-SH-v1", "AV-FWD-TF-v1"
        }:
            raise ConfigError("forward.model_branch is not an implemented forward branch")
        if self.prediction_mode not in {"strict", "diagnostic"}:
            raise ConfigError("forward.prediction_mode must be strict or diagnostic")
        _require_real("forward.validity_event_tolerance_s", self.validity_event_tolerance_s,
                      minimum=0.0, strict=True)
        object.__setattr__(self, "validity_event_tolerance_s", float(self.validity_event_tolerance_s))
        _require_real(
            "forward.circulation_coupling_m2",
            self.circulation_coupling_m2,
            minimum=0.0,
            strict=True,
        )
        _require_real(
            "forward.effective_core_radius_m",
            self.effective_core_radius_m,
            minimum=0.0,
            strict=True,
        )
        _require_real(
            "forward.azimuthal_decay_power",
            self.azimuthal_decay_power,
            minimum=0.0,
            strict=True,
            maximum=4.0,
        )
        if float(self.azimuthal_decay_power) != 1.0:
            raise ConfigError(
                "forward.azimuthal_decay_power is fixed at 1.0 by the "
                "Burgers/Oseen forward-model closure"
            )
        _require_real(
            "forward.spin_up_time_s", self.spin_up_time_s, minimum=0.0, strict=True
        )
        _require_real(
            "forward.surface_response_time_s", self.surface_response_time_s, minimum=0.0
        )
        _require_real(
            "forward.air_core_depth_threshold_m",
            self.air_core_depth_threshold_m,
            minimum=0.0,
        )
        _require_int("forward.surface_radial_points", self.surface_radial_points, minimum=16)
        for name in (
            "integration_time_step_s",
            "relative_tolerance",
            "absolute_tolerance",
        ):
            _require_real(f"forward.{name}", getattr(self, name), minimum=0.0, strict=True)
        _require_real(
            "forward.initial_circulation_m2_s",
            self.initial_circulation_m2_s,
        )
        if not isinstance(self.initial_state_npz_path, str):
            raise ConfigError("forward.initial_state_npz_path must be a string")
        if not isinstance(self.initial_state_sha256, str):
            raise ConfigError("forward.initial_state_sha256 must be a string")
        if bool(self.initial_state_npz_path) != bool(self.initial_state_sha256):
            raise ConfigError(
                "forward initial-state path and SHA-256 must either both be empty "
                "or both be supplied"
            )
        if self.initial_state_sha256:
            digest = self.initial_state_sha256.lower()
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ConfigError(
                    "forward.initial_state_sha256 must be 64 hexadecimal characters"
                )
            object.__setattr__(self, "initial_state_sha256", digest)
        _require_real(
            "forward.axial_swirl_floor",
            self.axial_swirl_floor,
            minimum=0.0,
            strict=True,
            maximum=1.0,
        )
        for name in (
            "axial_swirl_decay_length_m",
            "interface_mobility_m_pa_s",
            "effective_target_radius_m",
            "wall_contact_friction_pa_s",
            "floor_contact_friction_pa_s",
            "target_contact_friction_pa_s",
            "stable_channel_radius_m",
            "stable_channel_hold_time_s",
            "top_seed_depth_m",
            "swirl_profile_error_max",
            "axisymmetry_error_max",
            "wave_envelope_error_max",
            "gas_pressure_error_max",
            "interface_thickness_cells",
            "max_phase_change_per_step",
            "volume_relative_tolerance",
            "energy_relative_tolerance",
        ):
            _require_real(
                f"forward.{name}", getattr(self, name), minimum=0.0, strict=True
            )
        if self.interface_thickness_cells > 4.0:
            raise ConfigError("forward.interface_thickness_cells must be <= 4")
        if self.max_phase_change_per_step > 0.25:
            raise ConfigError("forward.max_phase_change_per_step must be <= 0.25")
        for name in (
            "swirl_profile_error_max",
            "axisymmetry_error_max",
            "wave_envelope_error_max",
            "gas_pressure_error_max",
        ):
            if getattr(self, name) > 1.0:
                raise ConfigError(f"forward.{name} must be <= 1")
        for surface in ("wall", "floor", "target"):
            receding = getattr(self, f"{surface}_receding_angle_deg")
            equilibrium = getattr(self, f"{surface}_equilibrium_angle_deg")
            advancing = getattr(self, f"{surface}_advancing_angle_deg")
            for role, angle in (
                ("receding", receding),
                ("equilibrium", equilibrium),
                ("advancing", advancing),
            ):
                _require_real(
                    f"forward.{surface}_{role}_angle_deg",
                    angle,
                    minimum=0.0,
                    strict=True,
                    maximum=179.999999,
                )
            if not receding <= equilibrium <= advancing:
                raise ConfigError(
                    f"forward {surface} contact angles must satisfy "
                    "receding <= equilibrium <= advancing"
                )
        _require_int("forward.radial_cells", self.radial_cells, minimum=16)
        _require_int("forward.axial_cells", self.axial_cells, minimum=24)
        _require_int(
            "forward.minimum_resolved_throat_cells",
            self.minimum_resolved_throat_cells,
            minimum=2,
        )
        _require_int(
            "forward.maximum_step_rejections",
            self.maximum_step_rejections,
            minimum=1,
        )
        _normalise_reals(
            self,
            (
                "circulation_coupling_m2",
                "effective_core_radius_m",
                "azimuthal_decay_power",
                "spin_up_time_s",
                "surface_response_time_s",
                "air_core_depth_threshold_m",
                "integration_time_step_s",
                "relative_tolerance",
                "absolute_tolerance",
                "initial_circulation_m2_s",
                "axial_swirl_floor",
                "axial_swirl_decay_length_m",
                "interface_mobility_m_pa_s",
                "effective_target_radius_m",
                "wall_receding_angle_deg",
                "wall_equilibrium_angle_deg",
                "wall_advancing_angle_deg",
                "wall_contact_friction_pa_s",
                "floor_receding_angle_deg",
                "floor_equilibrium_angle_deg",
                "floor_advancing_angle_deg",
                "floor_contact_friction_pa_s",
                "target_receding_angle_deg",
                "target_equilibrium_angle_deg",
                "target_advancing_angle_deg",
                "target_contact_friction_pa_s",
                "stable_channel_radius_m",
                "stable_channel_hold_time_s",
                "top_seed_depth_m",
                "swirl_profile_error_max",
                "axisymmetry_error_max",
                "wave_envelope_error_max",
                "gas_pressure_error_max",
                "interface_thickness_cells",
                "max_phase_change_per_step",
                "volume_relative_tolerance",
                "energy_relative_tolerance",
            ),
        )


@dataclass(frozen=True, slots=True)
class HighFidelityConfig:
    """Independent three-dimensional two-phase finite-volume controls.

    ``radial_cells`` and ``azimuthal_cells`` are retained as schema-compatible
    names for the two Cartesian transverse counts ``N_x`` and ``N_y``.  Both
    are consumed by the HF3D backend; neither is a dormant axisymmetric field.
    """

    radial_cells: int = 48
    azimuthal_cells: int = 48
    axial_cells: int = 72
    time_step_s: float = 2.0e-4
    cfl_limit: float = 0.40
    cfl_controller_target_fraction: float = 0.95
    maximum_cfl_step_retries: int = 4
    interface_thickness_cells: float = 2.5
    pressure_solver_device: str = "auto"
    pressure_relative_tolerance: float = 1.0e-7
    pressure_max_iterations: int = 400
    maximum_projected_divergence_s_inv: float = 1.0e-4
    interface_compression_interval: int = 1
    interface_compression_pseudo_cfl: float = 0.04
    surface_tension_smoothing_cells: float = 0.75
    boundary_relaxation_time_s: float = 0.010
    boundary_layer_cells: float = 1.5
    side_contact_angle_deg: float = 90.0
    floor_contact_angle_deg: float = 90.0
    bar_contact_angle_deg: float = 85.0
    minimum_resolved_throat_cells: int = 4
    stable_channel_radius_m: float = 0.0015
    stable_channel_hold_time_s: float = 0.50
    maximum_rotation_increment_rad: float = 0.05
    maximum_single_step_phase_clip_relative: float = 1.0e-5
    maximum_cumulative_phase_clip_relative: float = 1.0e-3
    maximum_single_step_phase_repair_relative: float = 1.0e-5
    maximum_cumulative_phase_repair_relative: float = 1.0e-3

    def __post_init__(self) -> None:
        _require_int("high_fidelity.radial_cells", self.radial_cells, minimum=8)
        _require_int("high_fidelity.azimuthal_cells", self.azimuthal_cells, minimum=8)
        _require_int("high_fidelity.axial_cells", self.axial_cells, minimum=8)
        _require_real(
            "high_fidelity.time_step_s", self.time_step_s, minimum=0.0, strict=True
        )
        _require_real(
            "high_fidelity.cfl_limit",
            self.cfl_limit,
            minimum=0.0,
            strict=True,
            maximum=1.0,
        )
        _require_real(
            "high_fidelity.cfl_controller_target_fraction",
            self.cfl_controller_target_fraction,
            minimum=0.0,
            strict=True,
            maximum=1.0,
        )
        if self.cfl_controller_target_fraction >= 1.0:
            raise ConfigError(
                "high_fidelity.cfl_controller_target_fraction must be less than 1"
            )
        _require_int(
            "high_fidelity.maximum_cfl_step_retries",
            self.maximum_cfl_step_retries,
            minimum=0,
        )
        _require_real(
            "high_fidelity.interface_thickness_cells",
            self.interface_thickness_cells,
            minimum=1.0,
            maximum=8.0,
        )
        if not isinstance(self.pressure_solver_device, str):
            raise ConfigError("high_fidelity.pressure_solver_device must be a string")
        pressure_solver_device = self.pressure_solver_device.strip().lower()
        if pressure_solver_device not in {"auto", "cpu", "cuda"}:
            raise ConfigError(
                "high_fidelity.pressure_solver_device must be one of: "
                "'auto', 'cpu', 'cuda'"
            )
        object.__setattr__(self, "pressure_solver_device", pressure_solver_device)
        _require_real(
            "high_fidelity.pressure_relative_tolerance",
            self.pressure_relative_tolerance,
            minimum=0.0,
            strict=True,
        )
        _require_real(
            "high_fidelity.maximum_projected_divergence_s_inv",
            self.maximum_projected_divergence_s_inv,
            minimum=0.0,
            strict=True,
        )
        _require_int(
            "high_fidelity.pressure_max_iterations",
            self.pressure_max_iterations,
            minimum=1,
        )
        _require_int(
            "high_fidelity.interface_compression_interval",
            self.interface_compression_interval,
            minimum=0,
        )
        _require_int(
            "high_fidelity.minimum_resolved_throat_cells",
            self.minimum_resolved_throat_cells,
            minimum=2,
        )
        _require_real(
            "high_fidelity.interface_compression_pseudo_cfl",
            self.interface_compression_pseudo_cfl,
            minimum=0.0,
            maximum=0.20,
        )
        for name in (
            "surface_tension_smoothing_cells",
            "boundary_relaxation_time_s",
            "boundary_layer_cells",
            "stable_channel_radius_m",
            "stable_channel_hold_time_s",
            "maximum_rotation_increment_rad",
            "maximum_single_step_phase_clip_relative",
            "maximum_cumulative_phase_clip_relative",
            "maximum_single_step_phase_repair_relative",
            "maximum_cumulative_phase_repair_relative",
        ):
            _require_real(
                f"high_fidelity.{name}",
                getattr(self, name),
                minimum=0.0,
                strict=True,
            )
        for name in (
            "side_contact_angle_deg",
            "floor_contact_angle_deg",
            "bar_contact_angle_deg",
        ):
            _require_real(
                f"high_fidelity.{name}",
                getattr(self, name),
                minimum=0.0,
                strict=True,
                maximum=179.999999,
            )
        if (
            self.maximum_single_step_phase_clip_relative
            > self.maximum_cumulative_phase_clip_relative
        ):
            raise ConfigError(
                "high_fidelity single-step phase clip limit cannot exceed cumulative limit"
            )
        if (
            self.maximum_single_step_phase_repair_relative
            > self.maximum_cumulative_phase_repair_relative
        ):
            raise ConfigError(
                "high_fidelity single-step phase repair limit cannot exceed cumulative limit"
            )
        _normalise_reals(
            self,
            (
                "time_step_s",
                "cfl_limit",
                "cfl_controller_target_fraction",
                "interface_thickness_cells",
                "pressure_relative_tolerance",
                "maximum_projected_divergence_s_inv",
                "interface_compression_pseudo_cfl",
                "surface_tension_smoothing_cells",
                "boundary_relaxation_time_s",
                "boundary_layer_cells",
                "side_contact_angle_deg",
                "floor_contact_angle_deg",
                "bar_contact_angle_deg",
                "stable_channel_radius_m",
                "stable_channel_hold_time_s",
                "maximum_rotation_increment_rad",
                "maximum_single_step_phase_clip_relative",
                "maximum_cumulative_phase_clip_relative",
                "maximum_single_step_phase_repair_relative",
                "maximum_cumulative_phase_repair_relative",
            ),
        )


T = TypeVar("T")


def _decode_dataclass(cls: type[T], raw: object, path: str) -> T:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in raw):
        raise ConfigError(f"{path} keys must be strings")
    declared = {field.name: field for field in fields(cls)}
    unknown = sorted(set(raw) - set(declared))
    if unknown:
        raise ConfigError(f"{path} contains unknown field(s): {', '.join(unknown)}")
    hints = get_type_hints(cls)
    values: dict[str, Any] = {}
    for name, field in declared.items():
        if name in raw:
            values[name] = _decode_value(hints[name], raw[name], f"{path}.{name}")
        elif field.default is MISSING and field.default_factory is MISSING:
            raise ConfigError(f"{path} is missing required field: {name}")
    try:
        return cls(**values)
    except ConfigError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid {path}: {exc}") from exc


def _decode_value(annotation: Any, value: object, path: str) -> Any:
    if annotation is type(None):
        if value is not None:
            raise ConfigError(f"{path} must be null")
        return None
    if isinstance(annotation, type) and is_dataclass(annotation):
        return _decode_dataclass(annotation, value, path)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if not isinstance(value, str):
            raise ConfigError(f"{path} must be a string")
        try:
            return annotation(value)
        except ValueError as exc:
            choices = ", ".join(repr(item.value) for item in annotation)
            raise ConfigError(f"{path} must be one of: {choices}") from exc
    origin = get_origin(annotation)
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"{path} must be an array")
        arguments = get_args(annotation)
        if len(arguments) != 2 or arguments[1] is not Ellipsis:
            raise ConfigError(f"{path} has unsupported fixed-length tuple type")
        return tuple(
            _decode_value(arguments[0], item, f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if origin in (Union, UnionType):
        errors = []
        for choice in get_args(annotation):
            try:
                return _decode_value(choice, value, path)
            except ConfigError as exc:
                errors.append(str(exc))
        raise ConfigError(f"{path} does not match any allowed type: {'; '.join(errors)}")
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path} must be a real number")
        value = float(value)
        if not math.isfinite(value):
            raise ConfigError(f"{path} must be finite")
        return value
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{path} must be an integer")
        return value
    if annotation is str:
        if not isinstance(value, str):
            raise ConfigError(f"{path} must be a string")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{path} must be a boolean")
        return value
    raise ConfigError(f"{path} has unsupported declared type {annotation!r}")


def _encode_dataclass(instance: object) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields(instance):
        value = getattr(instance, field.name)
        if is_dataclass(value):
            result[field.name] = _encode_dataclass(value)
        elif isinstance(value, Enum):
            result[field.name] = value.value
        elif isinstance(value, tuple):
            result[field.name] = list(value)
        else:
            result[field.name] = value
    return result


from .gas_config import GasModelConfig
from .stokes_config import StokesModelConfig
from .film_config import FilmModelConfig


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Complete, validated input shared by both solver backends."""

    schema_version: int = CONFIG_SCHEMA_VERSION
    problem_id: str = PROBLEM_ID
    backend: BackendMode = BackendMode.FORWARD
    container: ContainerConfig = ContainerConfig()
    fluid: FluidConfig = FluidConfig()
    stir_bar: StirBarConfig = StirBarConfig()
    drive: DriveConfig = DriveConfig()
    run: RunConfig = RunConfig()
    forward: ForwardModelConfig = ForwardModelConfig()
    high_fidelity: HighFidelityConfig = HighFidelityConfig()
    gas: GasModelConfig = GasModelConfig()
    stokes: StokesModelConfig = StokesModelConfig()
    film: FilmModelConfig = FilmModelConfig()

    def __post_init__(self) -> None:
        _require_int("schema_version", self.schema_version, minimum=1)
        if self.schema_version != CONFIG_SCHEMA_VERSION:
            raise ConfigError(
                f"unsupported schema_version {self.schema_version}; "
                f"expected {CONFIG_SCHEMA_VERSION}"
            )
        if self.problem_id != PROBLEM_ID:
            raise ConfigError(f"problem_id must be {PROBLEM_ID!r}")
        if not isinstance(self.backend, BackendMode):
            raise ConfigError("backend must be a BackendMode")
        nested = {
            "container": ContainerConfig,
            "fluid": FluidConfig,
            "stir_bar": StirBarConfig,
            "drive": DriveConfig,
            "run": RunConfig,
            "forward": ForwardModelConfig,
            "high_fidelity": HighFidelityConfig,
            "gas": GasModelConfig,
            "stokes": StokesModelConfig,
            "film": FilmModelConfig,
        }
        for name, cls in nested.items():
            if not isinstance(getattr(self, name), cls):
                raise ConfigError(f"{name} must be {cls.__name__}")
        if self.stir_bar.half_length_m >= self.container.radius_m:
            raise ConfigError("stir bar must fit inside the cylindrical container")
        bar_top = self.stir_bar.bottom_clearance_m + 2.0 * self.stir_bar.radius_m
        if bar_top >= self.container.liquid_depth_m:
            raise ConfigError("submerged stir bar must fit below the initial free surface")
        if self.forward.effective_core_radius_m >= self.container.radius_m:
            raise ConfigError(
                "forward effective core radius must be smaller than container radius"
            )
        if self.forward.air_core_depth_threshold_m >= self.container.liquid_depth_m:
            raise ConfigError("air-core threshold must be smaller than the liquid depth")
        if self.forward.effective_target_radius_m > self.stir_bar.radius_m:
            raise ConfigError(
                "forward effective target radius cannot exceed stir-bar radius"
            )
        if self.forward.top_seed_depth_m >= self.container.headspace_height_m:
            raise ConfigError(
                "forward top seed depth must be smaller than the headspace height"
            )
        if self.forward.stable_channel_radius_m >= self.container.radius_m:
            raise ConfigError(
                "forward stable-channel radius must be smaller than container radius"
            )
        if (
            self.stir_bar.half_length_m + self.stir_bar.radius_m
            >= self.container.radius_m
        ):
            raise ConfigError(
                "stir-bar half-length plus radius must fit inside the container"
            )
        if self.forward.integration_time_step_s > self.run.output_interval_s:
            raise ConfigError("forward integration step cannot exceed output interval")
        if self.high_fidelity.time_step_s > self.run.output_interval_s:
            raise ConfigError("high-fidelity time step cannot exceed output interval")
        if self.high_fidelity.stable_channel_radius_m >= self.container.radius_m:
            raise ConfigError(
                "high-fidelity stable-channel radius must be smaller than container radius"
            )
        if (
            not self.drive.actual_speed_schedule_time_s
            and self.drive.ramp_duration_s > self.run.duration_s
        ):
            raise ConfigError("drive ramp duration cannot exceed run duration")
        if (
            self.drive.actual_speed_schedule_time_s
            and self.drive.actual_speed_schedule_time_s[-1] < self.run.duration_s
        ):
            raise ConfigError(
                "drive actual-speed schedule must cover the complete run duration"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dictionary without losing field names."""

        return _encode_dataclass(self)

    @classmethod
    def from_dict(cls, raw: object) -> "SimulationConfig":
        """Decode a mapping with recursive unknown-field and type rejection."""

        return _decode_dataclass(cls, raw, "config")
