"""Versioned numerical-quality contracts for open-boundary HF3D studies."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


LEGACY_QUALITY_CONTRACT_ID = "LEGACY_LIQUID_MASS_ERROR_GATE_V1"
OPEN_BOUNDARY_QUALITY_CONTRACT_ID = (
    "OPEN_BOUNDARY_INVENTORY_CONSERVATION_SPLIT_V1"
)
INVENTORY_ARCHIVE_OBSERVABLE = "liquid_mass_error_relative"
INVENTORY_CANONICAL_NAME = "liquid_inventory_change_relative"
INVENTORY_ROLE = "MODEL_DOMAIN_AND_BOUNDARY_INVENTORY_OBSERVABLE"
INVENTORY_ABSOLUTE_GATE_REASON = (
    "NO_CALIBRATED_PHYSICAL_OR_DOMAIN_ADEQUACY_THRESHOLD"
)


class QualityContractError(ValueError):
    """Raised when a quality contract is absent, ambiguous, or inconsistent."""


def quality_contract_id(gates: Mapping[str, Any]) -> str:
    raw = gates.get("contract_id", LEGACY_QUALITY_CONTRACT_ID)
    if not isinstance(raw, str) or not raw:
        raise QualityContractError("numerical-quality contract_id must be a string")
    if raw not in {
        LEGACY_QUALITY_CONTRACT_ID,
        OPEN_BOUNDARY_QUALITY_CONTRACT_ID,
    }:
        raise QualityContractError(f"unsupported numerical-quality contract: {raw}")
    return raw


def audit_quality_contract(
    gates: Mapping[str, Any],
    *,
    smooth_observables: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return a frozen audit of the selected numerical-quality semantics."""

    contract_id = quality_contract_id(gates)
    if contract_id == LEGACY_QUALITY_CONTRACT_ID:
        return {
            "schema_version": 1,
            "pass": True,
            "contract_id": contract_id,
            "inventory_absolute_gate_enabled": True,
            "inventory_archive_observable": INVENTORY_ARCHIVE_OBSERVABLE,
        }

    if "liquid_mass_error_relative_max" in gates:
        raise QualityContractError(
            "open-boundary split contract forbids the legacy "
            "liquid_mass_error_relative_max gate"
        )
    raw_inventory = gates.get("liquid_inventory_change")
    if not isinstance(raw_inventory, Mapping):
        raise QualityContractError(
            "open-boundary split contract requires liquid_inventory_change"
        )
    required = {
        "archive_observable": INVENTORY_ARCHIVE_OBSERVABLE,
        "canonical_name": INVENTORY_CANONICAL_NAME,
        "role": INVENTORY_ROLE,
        "absolute_gate_enabled": False,
        "required_smooth_observable": True,
        "absolute_gate_reason": INVENTORY_ABSOLUTE_GATE_REASON,
    }
    for key, expected in required.items():
        if raw_inventory.get(key) != expected:
            raise QualityContractError(
                f"liquid_inventory_change.{key} must equal {expected!r}"
            )

    smooth_registered: bool | None = None
    if smooth_observables is not None:
        smooth_registered = INVENTORY_ARCHIVE_OBSERVABLE in smooth_observables
        if not smooth_registered:
            raise QualityContractError(
                "open-boundary split contract requires "
                "liquid_mass_error_relative as a smooth observable"
            )

    return {
        "schema_version": 1,
        "pass": True,
        "contract_id": contract_id,
        "numerical_conservation": {
            "archive_observable": "liquid_volume_balance_error_relative",
            "role": "OPEN_BOUNDARY_FLUX_CORRECTED_NUMERICAL_CONSERVATION_GATE",
            "absolute_gate_enabled": True,
            "maximum_gate_key": "liquid_volume_balance_error_relative_max",
        },
        "inventory_observation": {
            **required,
            "smooth_observable_registered": smooth_registered,
        },
        "legacy_mass_error_gate_forbidden": True,
    }
