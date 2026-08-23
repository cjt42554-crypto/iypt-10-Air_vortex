"""Scientific identities for stage-one observations and comparisons.

An observable name and a unit are not a scientific identity.  The helpers in
this module bind the name to the actual spatial operator, reference surface,
camera model, and topology thresholds that produced the archived values.
"""

from __future__ import annotations

from enum import Enum
import hashlib
import json
import math
from typing import Any, Iterable, Mapping


OBSERVABLE_CONTRACT_VERSION = "airvortex-observable-v4"
EVENT_CONTRACT_VERSION = "airvortex-event-v3"
MEASUREMENT_CONTRACT_VERSION = "airvortex-measurement-v2"
TOPOLOGY_CONTRACT_VERSION = "airvortex-topology-v3-widest-path"


class ContractError(ValueError):
    """Raised when scientific identities are absent, malformed, or different."""


class PredictionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    GEOMETRIC_TOUCH = "GEOMETRIC_TOUCH"
    CONNECTED_UNRESOLVED = "CONNECTED_UNRESOLVED"
    CONNECTED_RESOLVED = "CONNECTED_RESOLVED"
    CONNECTED_SUSTAINED_NUMERICAL = "CONNECTED_SUSTAINED_NUMERICAL"
    BROKEN_AFTER_CONNECTION = "BROKEN_AFTER_CONNECTION"
    DOMAIN_LIMIT = "DOMAIN_LIMIT"
    INVALID_MODEL_DOMAIN = "INVALID_MODEL_DOMAIN"
    FAILED_NUMERICALLY = "FAILED_NUMERICALLY"


DEFAULT_SURFACE_OPERATOR_PARAMETERS: dict[str, Any] = {
    "central_observation_radius_m": 0.003,
    "outer_annulus_inner_radius_m": 0.036,
    "outer_annulus_outer_radius_m": 0.045,
    "interface_level": 0.5,
    "static_reference_height_m": 0.06,
    "static_reference_source": "nominal_configured_liquid_depth_not_measured_H0",
    "central_aggregation": "minimum",
    "outer_aggregation": "annular_area_weighted_mean",
}


_TEMPLATES: dict[str, dict[str, Any]] = {
    "central_surface_min_height_m": {
        "observable_id": "central_surface_min_height_m",
        "unit": "m",
        "reference_surface": "laboratory_z_origin",
        "spatial_aggregation": "minimum inside configured central circular ROI",
        "operator_kind": "surface",
        "description": "Primitive central external-surface minimum height.",
    },
    "outer_reference_height_m": {
        "observable_id": "outer_reference_height_m",
        "unit": "m",
        "reference_surface": "laboratory_z_origin",
        "spatial_aggregation": "annular-area-weighted mean inside configured outer annulus",
        "operator_kind": "surface",
        "description": "Primitive outer-annulus reference height.",
    },
    "central_depression_from_static_m": {
        "observable_id": "central_depression_from_static_m",
        "unit": "m",
        "reference_surface": "configured_static_reference_with_explicit_provenance",
        "spatial_aggregation": "static reference height minus central surface minimum",
        "operator_kind": "surface",
        "description": "Signed central depression from the configured static reference.",
    },
    "visible_outer_to_center_depth_m": {
        "observable_id": "visible_outer_to_center_depth_m",
        "unit": "m",
        "reference_surface": "configured_outer_annulus",
        "spatial_aggregation": "outer reference height minus central surface minimum",
        "operator_kind": "surface",
        "description": "Signed visible outer-to-centre geometric depth.",
    },
    "surface_depth_m": {
        "observable_id": "visible_outer_to_center_depth_m",
        "unit": "m",
        "reference_surface": "configured_outer_annulus",
        "spatial_aggregation": "outer reference height minus central surface minimum",
        "operator_kind": "surface",
        "description": "Compatibility key identical to visible_outer_to_center_depth_m.",
    },
    "surface_height_range_m": {
        "observable_id": "surface_height_range_m",
        "unit": "m",
        "reference_surface": "laboratory_z_origin",
        "spatial_aggregation": "maximum minus minimum of canonical external-surface profile",
        "operator_kind": "surface",
        "description": "Full canonical surface-height range.",
    },
    "camera_visible_depth_m": {
        "observable_id": "camera_visible_depth_m",
        "unit": "m",
        "reference_surface": "segmented_camera_outer_annulus",
        "spatial_aggregation": "segmented outer boundary minus segmented central minimum",
        "operator_kind": "camera",
        "description": "Visible depth after projection, PSF, pixelisation, and segmentation.",
    },
    "camera_channel_width_m": {
        "observable_id": "camera_channel_width_m",
        "unit": "m",
        "reference_surface": "configured_bar_top_measurement_plane",
        "spatial_aggregation": "central contiguous segmented gas width at the bar-top plane",
        "operator_kind": "camera",
        "description": "Camera-space channel width at the configured bar-top plane.",
    },
    "connected_resolved": {
        "observable_id": "connected_resolved",
        "unit": "1",
        "reference_surface": "top_atmosphere_to_bar_target",
        "spatial_aggregation": "widest-path bottleneck passes numerical resolution gate",
        "operator_kind": "topology",
        "description": "Numerically resolved gas path; not an experimental stability claim.",
        "comparable_across_backends": False,
    },
    "channel_bottleneck_radius_m": {
        "observable_id": "channel_bottleneck_radius_m",
        "unit": "m",
        "reference_surface": "top_atmosphere_to_bar_target",
        "spatial_aggregation": "maximum over paths of minimum local inscribed radius",
        "operator_kind": "topology",
        "description": "Three-dimensional widest-path bottleneck radius.",
        "comparable_across_backends": False,
    },
}


_IDENTITY_FIELDS = (
    "observable_id",
    "definition_version",
    "unit",
    "reference_surface",
    "spatial_aggregation",
    "measurement_operator_id",
    "topology_definition_id",
    "operator_parameters",
    "comparable_across_backends",
)


def _json_copy(value: object, *, label: str) -> Any:
    """Return a deterministic JSON copy while rejecting NaN and non-JSON data."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{label} must be finite strict JSON: {exc}") from exc


def _short_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_parameters(
    value: Mapping[str, Any] | None,
    *,
    fallback: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    source = fallback if value is None else value
    if not isinstance(source, Mapping):
        raise ContractError(f"{label} must be a mapping")
    copied = _json_copy(dict(source), label=label)
    if not isinstance(copied, dict):  # pragma: no cover - guarded by dict(source)
        raise ContractError(f"{label} must be an object")
    return copied


def _definition(
    key: str,
    *,
    surface_parameters: Mapping[str, Any],
    camera_parameters: Mapping[str, Any],
    topology_parameters: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        template = _TEMPLATES[key]
    except KeyError as exc:
        raise ContractError(f"no registered scientific definition for {key!r}") from exc

    kind = str(template["operator_kind"])
    if kind == "surface":
        parameters = dict(surface_parameters)
        operator_id = f"canonical-surface-v1:{_short_digest(parameters)[:20]}"
        topology_id = "not_applicable"
    elif kind == "camera":
        parameters = dict(camera_parameters)
        operator_id = str(
            parameters.get(
                "operator_id", f"camera-v2:{_short_digest(parameters)[:20]}"
            )
        )
        topology_id = "not_applicable"
    elif kind == "topology":
        parameters = dict(topology_parameters)
        operator_id = "physical_topology"
        topology_id = f"{TOPOLOGY_CONTRACT_VERSION}:{_short_digest(parameters)[:20]}"
    else:  # pragma: no cover - templates are module constants
        raise ContractError(f"unsupported operator kind {kind!r}")

    definition: dict[str, Any] = {
        "observable_id": template["observable_id"],
        "definition_version": OBSERVABLE_CONTRACT_VERSION,
        "unit": template["unit"],
        "reference_surface": template["reference_surface"],
        "spatial_aggregation": template["spatial_aggregation"],
        "measurement_operator_id": operator_id,
        "topology_definition_id": topology_id,
        "operator_parameters": parameters,
        "comparable_across_backends": bool(
            template.get("comparable_across_backends", True)
        ),
        "description": template["description"],
    }
    identity_payload = {field: definition[field] for field in _IDENTITY_FIELDS}
    digest = _short_digest(identity_payload)
    definition["identity_digest"] = digest
    definition["identity"] = [
        definition["observable_id"],
        definition["definition_version"],
        definition["unit"],
        definition["reference_surface"],
        definition["spatial_aggregation"],
        definition["measurement_operator_id"],
        definition["topology_definition_id"],
        digest,
    ]
    return definition


def contract_document(
    keys: Iterable[str] | Mapping[str, Any],
    *,
    surface_operator_parameters: Mapping[str, Any] | None = None,
    camera_operator_parameters: Mapping[str, Any] | None = None,
    topology_operator_parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build definitions whose identity is derived from the actual operators."""

    iterable = keys.keys() if isinstance(keys, Mapping) else keys
    surface = _validated_parameters(
        surface_operator_parameters,
        fallback=DEFAULT_SURFACE_OPERATOR_PARAMETERS,
        label="surface operator parameters",
    )
    camera = _validated_parameters(
        camera_operator_parameters,
        fallback={
            "operator_id": "camera-unconfigured",
            "calibration_status": "not_configured",
        },
        label="camera operator parameters",
    )
    topology = _validated_parameters(
        topology_operator_parameters,
        fallback={
            "definition": "widest_path",
            "threshold_status": "not_configured",
        },
        label="topology operator parameters",
    )
    definitions = {
        key: _definition(
            key,
            surface_parameters=surface,
            camera_parameters=camera,
            topology_parameters=topology,
        )
        for key in iterable
        if key in _TEMPLATES
    }
    return {
        "version": OBSERVABLE_CONTRACT_VERSION,
        "event_contract_version": EVENT_CONTRACT_VERSION,
        "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
        "topology_contract_version": TOPOLOGY_CONTRACT_VERSION,
        "definitions": definitions,
    }


def _contract(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = metadata.get("observable_contract")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ContractError("observable_contract metadata must be a mapping")
    return value


def _raw_definition(metadata: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    contract = _contract(metadata)
    if contract is None:
        raise ContractError("result lacks observable_contract metadata")
    if contract.get("version") != OBSERVABLE_CONTRACT_VERSION:
        raise ContractError(
            f"unsupported observable contract version {contract.get('version')!r}"
        )
    definitions = contract.get("definitions")
    if not isinstance(definitions, Mapping) or key not in definitions:
        raise ContractError(f"result lacks definition for {key!r}")
    value = definitions[key]
    if not isinstance(value, Mapping):
        raise ContractError(f"invalid definition for {key!r}")
    return value


def _normalised_identity(definition: Mapping[str, Any], key: str) -> dict[str, Any]:
    missing = [field for field in _IDENTITY_FIELDS if field not in definition]
    if missing:
        raise ContractError(
            f"definition for {key!r} lacks identity fields: {', '.join(missing)}"
        )
    payload = {field: definition[field] for field in _IDENTITY_FIELDS}
    payload = _json_copy(payload, label=f"definition for {key!r}")
    if not isinstance(payload, dict):  # pragma: no cover
        raise ContractError(f"definition for {key!r} is invalid")
    for field in _IDENTITY_FIELDS[:7]:
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ContractError(f"definition field {field!r} for {key!r} must be text")
    if not isinstance(payload["operator_parameters"], dict):
        raise ContractError(f"operator_parameters for {key!r} must be an object")
    if not isinstance(payload["comparable_across_backends"], bool):
        raise ContractError(
            f"comparable_across_backends for {key!r} must be boolean"
        )
    expected_digest = _short_digest(payload)
    if definition.get("identity_digest") != expected_digest:
        raise ContractError(f"identity digest is stale or invalid for {key!r}")
    expected_identity = [
        payload["observable_id"],
        payload["definition_version"],
        payload["unit"],
        payload["reference_surface"],
        payload["spatial_aggregation"],
        payload["measurement_operator_id"],
        payload["topology_definition_id"],
        expected_digest,
    ]
    if list(definition.get("identity", ())) != expected_identity:
        raise ContractError(f"identity tuple is stale or invalid for {key!r}")
    payload["identity_digest"] = expected_digest
    return payload


def scientific_identity_from_metadata(
    metadata: Mapping[str, Any], key: str
) -> dict[str, Any] | None:
    """Return a validated identity, or ``None`` for a legacy uncontracted result."""

    if _contract(metadata) is None:
        return None
    return _normalised_identity(_raw_definition(metadata, key), key)


def assert_observables_compatible(
    left: Mapping[str, Any], right: Mapping[str, Any], key: str
) -> None:
    """Reject one-sided, malformed, or different scientific identities.

    Legacy comparisons remain available only when *neither* side claims a
    scientific contract.  Once either side carries a contract, dropping the
    other side's identity cannot be used to bypass this gate.
    """

    left_contract = _contract(left)
    right_contract = _contract(right)
    if left_contract is None and right_contract is None:
        return
    if left_contract is None or right_contract is None:
        raise ContractError(
            f"scientific identity missing on one side for {key!r}"
        )
    a = _normalised_identity(_raw_definition(left, key), key)
    b = _normalised_identity(_raw_definition(right, key), key)
    if a != b:
        raise ContractError(f"scientific identity mismatch for {key!r}")
    if not a["comparable_across_backends"]:
        left_backend = str(left.get("backend_id", ""))
        right_backend = str(right.get("backend_id", ""))
        if left_backend and right_backend and left_backend != right_backend:
            raise ContractError(
                f"{key!r} is model-specific and cannot be a cross-backend error metric"
            )


def require_finite_parameter(value: object, name: str) -> float:
    """Shared finite-number validation for operator dataclasses."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


__all__ = [
    "ContractError",
    "DEFAULT_SURFACE_OPERATOR_PARAMETERS",
    "EVENT_CONTRACT_VERSION",
    "MEASUREMENT_CONTRACT_VERSION",
    "OBSERVABLE_CONTRACT_VERSION",
    "PredictionState",
    "TOPOLOGY_CONTRACT_VERSION",
    "assert_observables_compatible",
    "contract_document",
    "require_finite_parameter",
    "scientific_identity_from_metadata",
]
