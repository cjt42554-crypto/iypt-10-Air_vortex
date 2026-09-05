"""Fail-closed input contract for E0 measurement runs.

This module validates run identity, complete-run calibration/holdout assignment,
SI units, coordinate/time calibration metadata, and the measured bar channel.
It deliberately does not open image or surface-result files.  A synthetic
contract can exercise software paths, but can never pass a scientific-use gate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np

from airvortex.analysis._common import DataValidationError, RoleViolationError

from .config_io import StrictJSONError, read_json_strict


MEASUREMENT_CONTRACT_ID = "airvortex-e0-measurement-input-v1"
CALIBRATION = "calibration"
SEALED_VALIDATION = "sealed_validation"
SYNTHETIC = "SYNTHETIC"
REAL_MEASUREMENT = "REAL_MEASUREMENT"
SOFTWARE_TEST = "software_test"
MEASUREMENT_PREPROCESSING = "measurement_preprocessing"
CALIBRATION_FIT = "calibration_fit"
SEALED_VALIDATION_SCORE = "sealed_validation_score"

_EXPECTED_UNITS = {
    "time": "s",
    "length": "m",
    "angle": "rad",
    "angular_speed": "rad/s",
    "volume": "m^3",
    "gravity": "m/s^2",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(f"{name} must be a non-empty string")
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise DataValidationError(f"{name} must be a JSON boolean")
    return value


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DataValidationError(f"{name} must be a JSON object")
    if not all(isinstance(key, str) for key in value):
        raise DataValidationError(f"{name} keys must be strings")
    return value


def _finite_array(
    value: object,
    name: str,
    *,
    ndim: int | None = None,
    shape: tuple[int, ...] | None = None,
) -> np.ndarray:
    if isinstance(value, (str, bytes, Mapping)):
        raise DataValidationError(f"{name} must be a numeric JSON array")
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"{name} must be a numeric JSON array") from exc
    if ndim is not None and result.ndim != ndim:
        raise DataValidationError(f"{name} must have {ndim} dimensions")
    if shape is not None and result.shape != shape:
        raise DataValidationError(f"{name} must have shape {shape}")
    if result.size == 0 or not np.all(np.isfinite(result)):
        raise DataValidationError(f"{name} must be non-empty and finite")
    return result


def _positive_float(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise DataValidationError(f"{name} must be a positive finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise DataValidationError(f"{name} must be a positive finite number")
    return result


def _string_ids(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DataValidationError(f"{name} must be a JSON array of run IDs")
    result = tuple(_string(item, f"{name} entry") for item in value)
    if len(result) != len(set(result)):
        raise DataValidationError(f"{name} contains duplicate run IDs")
    return result


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _validate_units(document: Mapping[str, Any]) -> None:
    units = _mapping(document.get("units"), "units")
    if dict(units) != _EXPECTED_UNITS:
        raise DataValidationError(
            "units must be the exact E0 SI mapping; implicit conversion is forbidden"
        )


def _validate_split(
    document: Mapping[str, Any], run_id: str, role: str
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    split = _mapping(document.get("split"), "split")
    if split.get("unit") != "complete_run":
        raise DataValidationError("split.unit must be 'complete_run'")
    calibration_ids = _string_ids(
        split.get("calibration_run_ids"), "split.calibration_run_ids"
    )
    sealed_ids = _string_ids(
        split.get("sealed_validation_run_ids"),
        "split.sealed_validation_run_ids",
    )
    overlap = set(calibration_ids) & set(sealed_ids)
    if overlap:
        raise RoleViolationError(
            "complete runs cannot appear in both calibration and sealed validation: "
            + ", ".join(sorted(overlap))
        )
    expected = calibration_ids if role == CALIBRATION else sealed_ids
    other = sealed_ids if role == CALIBRATION else calibration_ids
    if run_id not in expected or run_id in other:
        raise RoleViolationError("run role disagrees with the complete-run split")
    frozen = _boolean(
        split.get("assignment_frozen_before_acquisition"),
        "split.assignment_frozen_before_acquisition",
    )
    return calibration_ids, sealed_ids, frozen


def _validate_coordinate_calibration(document: Mapping[str, Any]) -> None:
    section = _mapping(
        document.get("coordinate_calibration"), "coordinate_calibration"
    )
    _string(section.get("calibration_id"), "coordinate_calibration.calibration_id")
    status = _string(section.get("status"), "coordinate_calibration.status")
    if status not in {"QUALIFIED", "UNQUALIFIED", "SYNTHETIC_TEST_ONLY"}:
        raise DataValidationError("unsupported coordinate calibration status")
    _boolean(
        section.get("scientifically_qualified"),
        "coordinate_calibration.scientifically_qualified",
    )
    homography = _finite_array(
        section.get("homography_pixel_to_container_m"),
        "coordinate_calibration.homography_pixel_to_container_m",
        shape=(3, 3),
    )
    if abs(float(np.linalg.det(homography))) <= 1.0e-18:
        raise DataValidationError("pixel-to-container homography is singular")
    bounds = _finite_array(
        section.get("valid_pixel_bounds_uv"),
        "coordinate_calibration.valid_pixel_bounds_uv",
        shape=(4,),
    )
    if not bounds[0] < bounds[1] or not bounds[2] < bounds[3]:
        raise DataValidationError("valid pixel bounds must have positive extent")
    sign = section.get("expected_jacobian_sign")
    if isinstance(sign, bool) or sign not in {-1, 1}:
        raise DataValidationError("expected_jacobian_sign must be -1 or +1")


def _validate_time_and_exposure(document: Mapping[str, Any]) -> None:
    timebase = _mapping(document.get("timebase"), "timebase")
    if timebase.get("time_zero") != "shared_hardware_trigger_edge":
        raise DataValidationError(
            "timebase.time_zero must be 'shared_hardware_trigger_edge'"
        )
    _string(timebase.get("trigger_channel"), "timebase.trigger_channel")
    _string(timebase.get("camera_clock"), "timebase.camera_clock")
    _string(timebase.get("bar_clock"), "timebase.bar_clock")
    _boolean(timebase.get("synchronized"), "timebase.synchronized")
    _boolean(
        timebase.get("scientifically_qualified"),
        "timebase.scientifically_qualified",
    )

    exposure = _mapping(document.get("exposure"), "exposure")
    if exposure.get("shutter_type") != "global":
        raise DataValidationError(
            "only global shutter is supported; rolling shutter requires a row-time operator"
        )
    frame_time = _finite_array(
        exposure.get("frame_time_s"), "exposure.frame_time_s", ndim=1
    )
    starts = _finite_array(
        exposure.get("start_s"), "exposure.start_s", shape=frame_time.shape
    )
    ends = _finite_array(
        exposure.get("end_s"), "exposure.end_s", shape=frame_time.shape
    )
    if len(frame_time) > 1 and not np.all(np.diff(frame_time) > 0.0):
        raise DataValidationError("exposure frame times must be strictly increasing")
    if np.any(ends <= starts):
        raise DataValidationError("each exposure interval must have positive duration")
    if np.any(frame_time < starts) or np.any(frame_time > ends):
        raise DataValidationError("frame time must lie inside its exposure interval")
    fractions = _finite_array(
        exposure.get("response_fraction"),
        "exposure.response_fraction",
        ndim=1,
    )
    response = _finite_array(
        exposure.get("response_weight"),
        "exposure.response_weight",
        shape=fractions.shape,
    )
    if len(fractions) < 2 or not np.all(np.diff(fractions) > 0.0):
        raise DataValidationError("shutter response fractions must increase")
    if fractions[0] != 0.0 or fractions[-1] != 1.0:
        raise DataValidationError("shutter response must span fractions 0 through 1")
    if np.any(response < 0.0) or not np.any(response > 0.0):
        raise DataValidationError("shutter response must be nonnegative and nonzero")


def _validate_bar_kinematics(
    document: Mapping[str, Any], evidence_kind: str
) -> None:
    section = _mapping(document.get("bar_kinematics"), "bar_kinematics")
    if section.get("quantity") != "actual_bar":
        raise DataValidationError(
            "bar_kinematics.quantity must be 'actual_bar'; panel command is not a substitute"
        )
    actual = _boolean(
        section.get("is_actual_bar_measurement"),
        "bar_kinematics.is_actual_bar_measurement",
    )
    time = _finite_array(section.get("time_s"), "bar_kinematics.time_s", ndim=1)
    if len(time) < 2 or not np.all(np.diff(time) > 0.0):
        raise DataValidationError("bar kinematics time must be strictly increasing")
    for key in ("angular_speed_rad_s", "phase_rad", "tilt_rad"):
        _finite_array(section.get(key), f"bar_kinematics.{key}", shape=time.shape)
    _finite_array(
        section.get("centre_xyz_m"),
        "bar_kinematics.centre_xyz_m",
        shape=(len(time), 3),
    )
    if evidence_kind == REAL_MEASUREMENT and not actual:
        raise DataValidationError("a real run requires measured actual-bar kinematics")
    if evidence_kind == SYNTHETIC and actual:
        raise DataValidationError("synthetic kinematics cannot be marked as measured")


def _validate_geometry(document: Mapping[str, Any], evidence_kind: str) -> None:
    section = _mapping(document.get("geometry"), "geometry")
    for key in (
        "container_inner_radius_m",
        "liquid_depth_m",
        "headspace_height_m",
        "bar_half_length_m",
        "bar_radius_m",
        "bar_volume_m3",
        "bottom_clearance_m",
        "liquid_volume_m3",
        "liquid_volume_standard_uncertainty_m3",
    ):
        _positive_float(section.get(key), f"geometry.{key}")
    gravity = _finite_array(
        section.get("gravity_container_m_s2"),
        "geometry.gravity_container_m_s2",
        shape=(3,),
    )
    if float(np.linalg.norm(gravity)) <= 0.0:
        raise DataValidationError("container-frame gravity vector cannot be zero")
    qualified = _boolean(
        section.get("scientifically_qualified"), "geometry.scientifically_qualified"
    )
    if evidence_kind == REAL_MEASUREMENT and not qualified:
        raise DataValidationError("real measurement geometry must be qualified")
    if evidence_kind == SYNTHETIC and qualified:
        raise DataValidationError("synthetic geometry cannot be scientifically qualified")


def _validate_flow_field(document: Mapping[str, Any], evidence_kind: str) -> None:
    section = _mapping(document.get("flow_field"), "flow_field")
    if section.get("quantity") != "liquid_velocity_container":
        raise DataValidationError(
            "flow_field.quantity must be 'liquid_velocity_container'"
        )
    actual = _boolean(
        section.get("is_actual_flow_measurement"),
        "flow_field.is_actual_flow_measurement",
    )
    time = _finite_array(section.get("time_s"), "flow_field.time_s", ndim=1)
    if len(time) < 2 or not np.all(np.diff(time) > 0.0):
        raise DataValidationError("flow-field time must be strictly increasing")
    positions = _finite_array(
        section.get("sample_xyz_m"), "flow_field.sample_xyz_m", ndim=2
    )
    if positions.shape[1:] != (3,):
        raise DataValidationError("flow_field.sample_xyz_m must have shape (points, 3)")
    _finite_array(
        section.get("velocity_m_s"),
        "flow_field.velocity_m_s",
        shape=(len(time), len(positions), 3),
    )
    mask = np.asarray(section.get("valid_mask"))
    if mask.shape != (len(time), len(positions)):
        raise DataValidationError("flow_field.valid_mask has the wrong shape")
    if any(type(item) is not bool for item in mask.ravel().tolist()):
        raise DataValidationError("flow_field.valid_mask must contain JSON booleans")
    if evidence_kind == REAL_MEASUREMENT and not actual:
        raise DataValidationError("a real quantitative contract requires a measured flow field")
    if evidence_kind == SYNTHETIC and actual:
        raise DataValidationError("synthetic flow values cannot be marked as measured")


def _validate_surface_fixture(document: Mapping[str, Any]) -> None:
    if "surface_fixture" not in document:
        return
    section = _mapping(document["surface_fixture"], "surface_fixture")
    uv = _finite_array(section.get("pixel_uv"), "surface_fixture.pixel_uv", ndim=2)
    if uv.shape[1:] != (2,):
        raise DataValidationError("surface_fixture.pixel_uv must have shape (samples, 2)")
    time = _finite_array(
        section.get("source_time_s"), "surface_fixture.source_time_s", ndim=1
    )
    if len(time) < 2 or not np.all(np.diff(time) > 0.0):
        raise DataValidationError("surface fixture time must be strictly increasing")
    heights = _finite_array(
        section.get("height_m"),
        "surface_fixture.height_m",
        shape=(len(time), len(uv)),
    )
    del heights
    areas = _finite_array(
        section.get("pixel_footprint_area_px2"),
        "surface_fixture.pixel_footprint_area_px2",
        shape=(len(uv),),
    )
    if np.any(areas <= 0.0):
        raise DataValidationError("surface fixture pixel areas must be positive")
    mask = np.asarray(section.get("valid_mask"))
    if mask.shape != (len(time), len(uv)):
        raise DataValidationError("surface_fixture.valid_mask has the wrong shape")
    if any(type(item) is not bool for item in mask.ravel().tolist()):
        raise DataValidationError("surface_fixture.valid_mask must contain JSON booleans")


def _validate_document(document: Mapping[str, Any]) -> None:
    if document.get("schema_version") != 1:
        raise DataValidationError("unsupported or missing measurement schema_version")
    if document.get("contract_id") != MEASUREMENT_CONTRACT_ID:
        raise DataValidationError("unsupported or missing measurement contract_id")
    run_id = _string(document.get("run_id"), "run_id")
    evidence_kind = document.get("evidence_kind")
    if evidence_kind not in {SYNTHETIC, REAL_MEASUREMENT}:
        raise DataValidationError("evidence_kind must be SYNTHETIC or REAL_MEASUREMENT")
    role = document.get("role")
    if role not in {CALIBRATION, SEALED_VALIDATION}:
        raise DataValidationError("role must be calibration or sealed_validation")
    eligible = _boolean(
        document.get("calibration_validation_eligible"),
        "calibration_validation_eligible",
    )
    freeze_status = _string(document.get("freeze_status"), "freeze_status")
    _, sealed_ids, assignment_frozen = _validate_split(document, run_id, role)

    if evidence_kind == SYNTHETIC:
        if role != CALIBRATION:
            raise RoleViolationError("synthetic runs cannot carry sealed_validation role")
        if eligible:
            raise RoleViolationError("synthetic runs cannot be scientifically eligible")
        if freeze_status != "NOT_A_FROZEN_REAL_BATCH":
            raise RoleViolationError(
                "synthetic runs must declare NOT_A_FROZEN_REAL_BATCH"
            )
        if sealed_ids:
            raise RoleViolationError(
                "a synthetic software fixture cannot list sealed-validation runs"
            )
    else:
        if not eligible:
            raise RoleViolationError("real measurement input must declare eligibility")
        if role == SEALED_VALIDATION:
            if freeze_status != "FROZEN_BEFORE_TRIGGERED_SURFACE_UNSEAL":
                raise RoleViolationError(
                    "sealed validation requires a pre-unseal frozen assignment"
                )
            if not assignment_frozen:
                raise RoleViolationError(
                    "sealed validation split must be frozen before acquisition"
                )
            manifest = _string(
                document.get("prediction_manifest_sha256"),
                "prediction_manifest_sha256",
            ).lower()
            if _SHA256.fullmatch(manifest) is None:
                raise DataValidationError("prediction_manifest_sha256 is not SHA-256")
        elif freeze_status != "CALIBRATION_OPEN":
            raise RoleViolationError("real calibration runs must declare CALIBRATION_OPEN")

    provenance = _mapping(document.get("provenance"), "provenance")
    raw_present = _boolean(
        provenance.get("raw_measurement_files_present"),
        "provenance.raw_measurement_files_present",
    )
    if evidence_kind == SYNTHETIC and raw_present:
        raise DataValidationError("synthetic fixture cannot claim raw measurement files")
    _validate_units(document)
    _validate_coordinate_calibration(document)
    _validate_time_and_exposure(document)
    _validate_bar_kinematics(document, evidence_kind)
    _validate_geometry(document, evidence_kind)
    _validate_flow_field(document, evidence_kind)
    _validate_surface_fixture(document)


@dataclass(frozen=True, slots=True)
class MeasurementRunContract:
    """Immutable, validated E0 run contract."""

    document: Mapping[str, Any]
    source_path: str
    source_sha256: str

    @property
    def run_id(self) -> str:
        return str(self.document["run_id"])

    @property
    def evidence_kind(self) -> str:
        return str(self.document["evidence_kind"])

    @property
    def role(self) -> str:
        return str(self.document["role"])

    def section(self, name: str) -> Mapping[str, Any]:
        value = self.document.get(name)
        if not isinstance(value, Mapping):
            raise KeyError(name)
        return value


def load_measurement_contract(path: str | Path) -> MeasurementRunContract:
    """Load and validate a strict JSON measurement contract."""

    source = Path(path).expanduser().resolve()
    try:
        document = read_json_strict(source)
    except StrictJSONError as exc:
        raise DataValidationError(str(exc)) from exc
    if not isinstance(document, Mapping):
        raise DataValidationError("measurement contract root must be a JSON object")
    _validate_document(document)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return MeasurementRunContract(
        document=_freeze_json(document),
        source_path=str(source),
        source_sha256=digest,
    )


def ensure_measurement_use(
    contract: MeasurementRunContract, purpose: str
) -> None:
    """Gate software tests, calibration, preprocessing, and holdout scoring."""

    if not isinstance(contract, MeasurementRunContract):
        raise TypeError("contract must be a MeasurementRunContract")
    if purpose == SOFTWARE_TEST:
        if contract.evidence_kind != SYNTHETIC:
            raise RoleViolationError("software_test is reserved for synthetic fixtures")
        return
    if purpose not in {
        MEASUREMENT_PREPROCESSING,
        CALIBRATION_FIT,
        SEALED_VALIDATION_SCORE,
    }:
        raise ValueError("unsupported measurement-use purpose")
    if contract.evidence_kind != REAL_MEASUREMENT:
        raise RoleViolationError(
            "synthetic input cannot be used as calibration or validation evidence"
        )
    if purpose == CALIBRATION_FIT and contract.role != CALIBRATION:
        raise RoleViolationError("fitting requires a complete calibration run")
    if purpose == SEALED_VALIDATION_SCORE:
        if contract.role != SEALED_VALIDATION:
            raise RoleViolationError("scoring requires a complete sealed-validation run")
        if contract.document["freeze_status"] != (
            "FROZEN_BEFORE_TRIGGERED_SURFACE_UNSEAL"
        ):
            raise RoleViolationError("sealed validation was not frozen before unseal")


__all__ = [
    "CALIBRATION",
    "CALIBRATION_FIT",
    "MEASUREMENT_CONTRACT_ID",
    "MEASUREMENT_PREPROCESSING",
    "MeasurementRunContract",
    "REAL_MEASUREMENT",
    "SEALED_VALIDATION",
    "SEALED_VALIDATION_SCORE",
    "SOFTWARE_TEST",
    "SYNTHETIC",
    "ensure_measurement_use",
    "load_measurement_contract",
]
