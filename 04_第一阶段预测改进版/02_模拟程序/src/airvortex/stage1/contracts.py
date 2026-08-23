"""Shared scientific identities for stage-one comparable observables."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping

OBSERVABLE_CONTRACT_VERSION = "airvortex-observable-v3"
EVENT_CONTRACT_VERSION = "airvortex-event-v2"
MEASUREMENT_CONTRACT_VERSION = "airvortex-measurement-v1"
TOPOLOGY_CONTRACT_VERSION = "airvortex-topology-v2-widest-path"

class ContractError(ValueError):
    pass

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

@dataclass(frozen=True, slots=True)
class ObservableDefinition:
    observable_id: str
    unit: str
    reference_surface: str
    spatial_aggregation: str
    measurement_operator_id: str
    description: str
    topology_definition_id: str = "not_applicable"
    comparable_across_backends: bool = True

    @property
    def identity(self) -> tuple[str, ...]:
        return (
            self.observable_id,
            OBSERVABLE_CONTRACT_VERSION,
            self.unit,
            self.reference_surface,
            self.spatial_aggregation,
            self.measurement_operator_id,
            self.topology_definition_id,
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["definition_version"] = OBSERVABLE_CONTRACT_VERSION
        value["identity"] = list(self.identity)
        return value

DEFINITIONS: dict[str, ObservableDefinition] = {
    "central_depression_from_static_m": ObservableDefinition(
        "central_depression_from_static_m", "m",
        "measured_static_free_surface_H0_with_bar_installed",
        "H0 minus central external-surface minimum",
        "physical_interface_alpha_0p5",
        "Central free-surface depression from the measured static reference.",
    ),
    "visible_outer_to_center_depth_m": ObservableDefinition(
        "visible_outer_to_center_depth_m", "m", "outer_annulus_mean",
        "fixed outer-annulus height minus central surface minimum",
        "physical_interface_alpha_0p5",
        "Visible geometric depth relative to a fixed outer reference annulus.",
    ),
    "surface_depth_m": ObservableDefinition(
        "visible_outer_to_center_depth_m", "m", "outer_annulus_mean",
        "fixed outer-annulus height minus central surface minimum",
        "physical_interface_alpha_0p5",
        "Compatibility key; exactly the visible outer-to-centre depth.",
    ),
    "surface_height_range_m": ObservableDefinition(
        "surface_height_range_m", "m", "laboratory_z_origin",
        "maximum minus minimum of the top-connected external surface",
        "physical_interface_alpha_0p5", "Full external-surface height range.",
    ),
    "connected_resolved": ObservableDefinition(
        "connected_resolved", "1", "top_atmosphere_to_bar_target",
        "widest-path bottleneck passes numerical resolution gate",
        "physical_topology", "Numerically resolved gas path.",
        TOPOLOGY_CONTRACT_VERSION, False,
    ),
    "channel_bottleneck_radius_m": ObservableDefinition(
        "channel_bottleneck_radius_m", "m", "top_atmosphere_to_bar_target",
        "maximum over paths of minimum local inscribed radius",
        "physical_topology", "Three-dimensional widest-path bottleneck radius.",
        TOPOLOGY_CONTRACT_VERSION, False,
    ),
}

def contract_document(keys: Any) -> dict[str, Any]:
    iterable = keys.keys() if isinstance(keys, Mapping) else keys
    return {
        "version": OBSERVABLE_CONTRACT_VERSION,
        "event_contract_version": EVENT_CONTRACT_VERSION,
        "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
        "topology_contract_version": TOPOLOGY_CONTRACT_VERSION,
        "definitions": {k: DEFINITIONS[k].to_dict() for k in iterable if k in DEFINITIONS},
    }

def _definition(metadata: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    contract = metadata.get("observable_contract")
    if not isinstance(contract, Mapping):
        raise ContractError("result lacks observable_contract metadata")
    definitions = contract.get("definitions")
    if not isinstance(definitions, Mapping) or key not in definitions:
        raise ContractError(f"result lacks definition for {key!r}")
    value = definitions[key]
    if not isinstance(value, Mapping):
        raise ContractError(f"invalid definition for {key!r}")
    return value

def assert_observables_compatible(left: Mapping[str, Any], right: Mapping[str, Any], key: str) -> None:
    a, b = _definition(left, key), _definition(right, key)
    if tuple(a.get("identity", ())) != tuple(b.get("identity", ())):
        raise ContractError(f"scientific identity mismatch for {key!r}")
    if not bool(a.get("comparable_across_backends", True)) or not bool(b.get("comparable_across_backends", True)):
        la = str(left.get("backend_id", ""))
        rb = str(right.get("backend_id", ""))
        if la and rb and la != rb:
            raise ContractError(f"{key!r} is model-specific and cannot be a cross-backend error metric")
