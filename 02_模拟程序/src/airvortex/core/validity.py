"""Quantitative-validity contracts shared by producers and consumers.

The validity mask is deliberately narrower than run completion.  A value of
one says that the recorded sample may be used for quantitative calculations
within the explicitly declared model and claim scope.  It does not establish
apparatus calibration or experimental validation.  Missing legacy metadata is
therefore interpreted as no established quantitative samples, never as an
all-valid record.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from .protocols import SimulationResult


QUANTITATIVE_VALIDITY_VERSION = 1
QUANTITATIVE_VALIDITY_KEY = "quantitative_validity"
QUANTITATIVE_VALID_MASK = "quantitative_valid_mask"


class QuantitativeValidityError(ValueError):
    """Raised when a producer declares an inconsistent validity contract."""


class QuantitativeValidityStatus(str, Enum):
    """Scientific identity of a run, independent of its execution status."""

    VALID = "valid"
    PHYSICAL_EXIT = "physical_exit"
    DIAGNOSTIC_CONTINUATION = "diagnostic_continuation"
    USER_STOPPED = "user_stopped"
    NUMERICAL_FAILURE = "numerical_failure"
    INPUT_REJECTED = "input_rejected"
    LEGACY_UNASSESSED = "legacy_unassessed"


class QuantitativeClaimScope(str, Enum):
    """Highest type of claim represented by the validity mask."""

    CONDITIONAL_MODEL_NUMERICS = "conditional_model_numerics"
    APPARATUS_PREDICTION = "apparatus_prediction"
    DIAGNOSTIC_ONLY = "diagnostic_only"
    NONE = "none"


_CONTRACT_FIELDS = frozenset(
    {
        "version",
        "model_branch",
        "theory_sha256",
        "implementation_id",
        "status",
        "valid_until_s",
        "first_invalid_time_s",
        "termination_interval_s",
        "reason",
        "diagnostic_continuation",
        "claim_scope",
        "apparatus_quantitative_prediction_qualified",
        "calibrated_physical_inputs",
        "experimental_validation",
    }
)
_IDENTIFIED_RESULT_STATUSES = frozenset(
    {
        QuantitativeValidityStatus.VALID,
        QuantitativeValidityStatus.PHYSICAL_EXIT,
        QuantitativeValidityStatus.DIAGNOSTIC_CONTINUATION,
    }
)
_NO_QUANTITATIVE_CLAIM_STATUSES = frozenset(
    {
        QuantitativeValidityStatus.INPUT_REJECTED,
        QuantitativeValidityStatus.LEGACY_UNASSESSED,
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _optional_time(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise QuantitativeValidityError(f"{label} must be a real number or null")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise QuantitativeValidityError(f"{label} must be finite and non-negative")
    return result


def _optional_identity(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise QuantitativeValidityError(f"{label} must be a non-empty string or null")
    return value.strip()


@dataclass(frozen=True, slots=True)
class QuantitativeValidity:
    """Parsed immutable view of ``metadata.quantitative_validity``."""

    version: int
    model_branch: str | None
    theory_sha256: str | None
    implementation_id: str | None
    status: QuantitativeValidityStatus
    valid_until_s: float | None
    first_invalid_time_s: float | None
    termination_interval_s: tuple[float, float] | None
    reason: str
    diagnostic_continuation: bool
    claim_scope: QuantitativeClaimScope
    apparatus_quantitative_prediction_qualified: bool
    calibrated_physical_inputs: bool
    experimental_validation: bool

    @property
    def legacy_or_unassessed(self) -> bool:
        return self.status is QuantitativeValidityStatus.LEGACY_UNASSESSED

    @property
    def has_quantitative_claim(self) -> bool:
        return self.claim_scope not in {
            QuantitativeClaimScope.NONE,
            QuantitativeClaimScope.DIAGNOSTIC_ONLY,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "model_branch": self.model_branch,
            "theory_sha256": self.theory_sha256,
            "implementation_id": self.implementation_id,
            "status": self.status.value,
            "valid_until_s": self.valid_until_s,
            "first_invalid_time_s": self.first_invalid_time_s,
            "termination_interval_s": (
                list(self.termination_interval_s)
                if self.termination_interval_s is not None
                else None
            ),
            "reason": self.reason,
            "diagnostic_continuation": self.diagnostic_continuation,
            "claim_scope": self.claim_scope.value,
            "apparatus_quantitative_prediction_qualified": (
                self.apparatus_quantitative_prediction_qualified
            ),
            "calibrated_physical_inputs": self.calibrated_physical_inputs,
            "experimental_validation": self.experimental_validation,
        }


def validate_quantitative_validity_payload(raw: object) -> QuantitativeValidity:
    """Parse one exact version-1 validity object and reject ambiguous fields."""

    if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
        raise QuantitativeValidityError("quantitative_validity must be an object")
    if set(raw) != _CONTRACT_FIELDS:
        missing = sorted(_CONTRACT_FIELDS - set(raw))
        unknown = sorted(set(raw) - _CONTRACT_FIELDS)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if unknown:
            detail.append("unknown " + ", ".join(unknown))
        raise QuantitativeValidityError(
            "quantitative_validity fields are incomplete or unknown: " + "; ".join(detail)
        )
    version = raw["version"]
    if isinstance(version, bool) or version != QUANTITATIVE_VALIDITY_VERSION:
        raise QuantitativeValidityError("unsupported quantitative_validity version")
    try:
        status = QuantitativeValidityStatus(raw["status"])
    except (TypeError, ValueError) as exc:
        raise QuantitativeValidityError("invalid quantitative_validity status") from exc
    try:
        claim_scope = QuantitativeClaimScope(raw["claim_scope"])
    except (TypeError, ValueError) as exc:
        raise QuantitativeValidityError("invalid quantitative claim_scope") from exc

    model_branch = _optional_identity(raw["model_branch"], "model_branch")
    theory_sha256 = _optional_identity(raw["theory_sha256"], "theory_sha256")
    implementation_id = _optional_identity(raw["implementation_id"], "implementation_id")
    if theory_sha256 is not None and _SHA256.fullmatch(theory_sha256) is None:
        raise QuantitativeValidityError(
            "theory_sha256 must be a lowercase 64-character SHA-256 or null"
        )
    if status in _IDENTIFIED_RESULT_STATUSES and (
        model_branch is None or theory_sha256 is None or implementation_id is None
    ):
        raise QuantitativeValidityError(
            f"{status.value} requires model_branch, theory_sha256, and implementation_id"
        )

    valid_until_s = _optional_time(raw["valid_until_s"], "valid_until_s")
    first_invalid_time_s = _optional_time(
        raw["first_invalid_time_s"], "first_invalid_time_s"
    )
    raw_interval = raw["termination_interval_s"]
    termination_interval_s: tuple[float, float] | None
    if raw_interval is None:
        termination_interval_s = None
    else:
        if (
            not isinstance(raw_interval, Sequence)
            or isinstance(raw_interval, (str, bytes))
            or len(raw_interval) != 2
        ):
            raise QuantitativeValidityError(
                "termination_interval_s must be [last_confirmed_valid, first_confirmed_invalid] or null"
            )
        lower = _optional_time(raw_interval[0], "termination_interval_s[0]")
        upper = _optional_time(raw_interval[1], "termination_interval_s[1]")
        assert lower is not None and upper is not None
        if lower > upper:
            raise QuantitativeValidityError("termination_interval_s must be ordered")
        termination_interval_s = (lower, upper)

    reason = raw["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise QuantitativeValidityError("quantitative_validity reason must be non-empty")
    diagnostic_continuation = raw["diagnostic_continuation"]
    apparatus_qualified = raw["apparatus_quantitative_prediction_qualified"]
    calibrated_inputs = raw["calibrated_physical_inputs"]
    experimental_validation = raw["experimental_validation"]
    for label, value in (
        ("diagnostic_continuation", diagnostic_continuation),
        ("apparatus_quantitative_prediction_qualified", apparatus_qualified),
        ("calibrated_physical_inputs", calibrated_inputs),
        ("experimental_validation", experimental_validation),
    ):
        if type(value) is not bool:
            raise QuantitativeValidityError(f"{label} must be a JSON boolean")
    if diagnostic_continuation is not (
        status is QuantitativeValidityStatus.DIAGNOSTIC_CONTINUATION
    ):
        raise QuantitativeValidityError(
            "diagnostic_continuation must be true exactly for diagnostic_continuation status"
        )
    if status in {
        QuantitativeValidityStatus.PHYSICAL_EXIT,
        QuantitativeValidityStatus.DIAGNOSTIC_CONTINUATION,
    }:
        if first_invalid_time_s is None or termination_interval_s is None:
            raise QuantitativeValidityError(
                f"{status.value} requires first_invalid_time_s and termination_interval_s"
            )
        lower, upper = termination_interval_s
        if valid_until_s is not None and not math.isclose(
            valid_until_s, lower, rel_tol=0.0, abs_tol=1.0e-15
        ):
            raise QuantitativeValidityError(
                "valid_until_s must equal the lower termination-interval bound"
            )
        if not math.isclose(
            first_invalid_time_s, upper, rel_tol=0.0, abs_tol=1.0e-15
        ):
            raise QuantitativeValidityError(
                "first_invalid_time_s must equal the upper termination-interval bound"
            )
    elif status is QuantitativeValidityStatus.NUMERICAL_FAILURE:
        if (termination_interval_s is None) is not (first_invalid_time_s is None):
            raise QuantitativeValidityError(
                "numerical_failure must declare both termination bounds or neither"
            )
        if termination_interval_s is not None:
            lower, upper = termination_interval_s
            if valid_until_s is not None and not math.isclose(
                valid_until_s, lower, rel_tol=0.0, abs_tol=1.0e-15
            ):
                raise QuantitativeValidityError(
                    "valid_until_s must equal the lower termination-interval bound"
                )
            assert first_invalid_time_s is not None
            if not math.isclose(
                first_invalid_time_s, upper, rel_tol=0.0, abs_tol=1.0e-15
            ):
                raise QuantitativeValidityError(
                    "first_invalid_time_s must equal the upper termination-interval bound"
                )
    elif termination_interval_s is not None or first_invalid_time_s is not None:
        raise QuantitativeValidityError(
            f"{status.value} cannot declare a physical termination interval"
        )

    if status in _NO_QUANTITATIVE_CLAIM_STATUSES and claim_scope is not QuantitativeClaimScope.NONE:
        raise QuantitativeValidityError(f"{status.value} requires claim_scope='none'")
    if status in _NO_QUANTITATIVE_CLAIM_STATUSES and valid_until_s is not None:
        raise QuantitativeValidityError(f"{status.value} cannot declare valid_until_s")
    if claim_scope is QuantitativeClaimScope.APPARATUS_PREDICTION:
        if not (apparatus_qualified and calibrated_inputs and experimental_validation):
            raise QuantitativeValidityError(
                "apparatus_prediction scope requires explicit calibration and experimental-validation flags"
            )
    elif apparatus_qualified:
        raise QuantitativeValidityError(
            "apparatus qualification is inconsistent with the declared claim_scope"
        )

    return QuantitativeValidity(
        version=QUANTITATIVE_VALIDITY_VERSION,
        model_branch=model_branch,
        theory_sha256=theory_sha256,
        implementation_id=implementation_id,
        status=status,
        valid_until_s=valid_until_s,
        first_invalid_time_s=first_invalid_time_s,
        termination_interval_s=termination_interval_s,
        reason=reason.strip(),
        diagnostic_continuation=diagnostic_continuation,
        claim_scope=claim_scope,
        apparatus_quantitative_prediction_qualified=apparatus_qualified,
        calibrated_physical_inputs=calibrated_inputs,
        experimental_validation=experimental_validation,
    )


def make_quantitative_validity(
    *,
    model_branch: str | None,
    theory_sha256: str | None,
    implementation_id: str | None,
    status: QuantitativeValidityStatus | str,
    valid_until_s: float | None,
    first_invalid_time_s: float | None = None,
    termination_interval_s: Sequence[float] | None = None,
    reason: str,
    diagnostic_continuation: bool = False,
    claim_scope: QuantitativeClaimScope | str = (
        QuantitativeClaimScope.CONDITIONAL_MODEL_NUMERICS
    ),
    apparatus_quantitative_prediction_qualified: bool = False,
    calibrated_physical_inputs: bool = False,
    experimental_validation: bool = False,
) -> dict[str, object]:
    """Build and validate the JSON-ready metadata object used by producers."""

    raw = {
        "version": QUANTITATIVE_VALIDITY_VERSION,
        "model_branch": model_branch,
        "theory_sha256": theory_sha256,
        "implementation_id": implementation_id,
        "status": getattr(status, "value", status),
        "valid_until_s": valid_until_s,
        "first_invalid_time_s": first_invalid_time_s,
        "termination_interval_s": (
            list(termination_interval_s) if termination_interval_s is not None else None
        ),
        "reason": reason,
        "diagnostic_continuation": diagnostic_continuation,
        "claim_scope": getattr(claim_scope, "value", claim_scope),
        "apparatus_quantitative_prediction_qualified": (
            apparatus_quantitative_prediction_qualified
        ),
        "calibrated_physical_inputs": calibrated_physical_inputs,
        "experimental_validation": experimental_validation,
    }
    return validate_quantitative_validity_payload(raw).to_dict()


def legacy_unassessed_validity(reason: str) -> dict[str, object]:
    """Return the only safe interpretation of a record without this contract."""

    return make_quantitative_validity(
        model_branch=None,
        theory_sha256=None,
        implementation_id=None,
        status=QuantitativeValidityStatus.LEGACY_UNASSESSED,
        valid_until_s=None,
        reason=reason,
        claim_scope=QuantitativeClaimScope.NONE,
    )


def validate_result_quantitative_validity(
    time_s: object,
    observables: Mapping[str, object],
    metadata: Mapping[str, object],
    *,
    require_contract: bool = False,
) -> QuantitativeValidity | None:
    """Validate the joint metadata/mask contract for one result.

    Completely absent legacy information is accepted only when
    ``require_contract`` is false.  A partial declaration is always rejected.
    """

    has_metadata = QUANTITATIVE_VALIDITY_KEY in metadata
    has_mask = QUANTITATIVE_VALID_MASK in observables
    if not has_metadata and not has_mask:
        if require_contract:
            raise QuantitativeValidityError(
                "result has no quantitative_validity metadata or quantitative_valid_mask"
            )
        return None
    if has_metadata is not has_mask:
        raise QuantitativeValidityError(
            "quantitative_validity metadata and quantitative_valid_mask must be declared together"
        )
    contract = validate_quantitative_validity_payload(metadata[QUANTITATIVE_VALIDITY_KEY])
    time = np.asarray(time_s, dtype=np.float64)
    mask = np.asarray(observables[QUANTITATIVE_VALID_MASK])
    if time.ndim != 1:
        raise QuantitativeValidityError("validity time axis must be one-dimensional")
    if mask.ndim != 1 or mask.shape != time.shape:
        raise QuantitativeValidityError(
            "quantitative_valid_mask must be one-dimensional and match time_s"
        )
    if not np.issubdtype(mask.dtype, np.floating):
        raise QuantitativeValidityError("quantitative_valid_mask must use a float dtype")
    if not np.isfinite(mask).all() or not np.isin(mask, (0.0, 1.0)).all():
        raise QuantitativeValidityError(
            "quantitative_valid_mask values must be finite and exactly 0.0 or 1.0"
        )
    if mask.size > 1 and np.any(np.diff(mask.astype(np.float64)) > 0.0):
        raise QuantitativeValidityError(
            "quantitative_valid_mask must be monotone non-increasing"
        )

    valid = mask == 1.0
    invalid = ~valid
    if time.size and contract.status not in _NO_QUANTITATIVE_CLAIM_STATUSES and (
        contract.model_branch is None
        or contract.theory_sha256 is None
        or contract.implementation_id is None
    ):
        raise QuantitativeValidityError(
            f"{contract.status.value} result samples require model, theory, and implementation identity"
        )
    if contract.status in {
        QuantitativeValidityStatus.VALID,
        QuantitativeValidityStatus.USER_STOPPED,
    } and invalid.any():
        raise QuantitativeValidityError(
            f"{contract.status.value} cannot contain quantitatively invalid samples"
        )
    if contract.status is QuantitativeValidityStatus.DIAGNOSTIC_CONTINUATION and not invalid.any():
        raise QuantitativeValidityError(
            "diagnostic_continuation requires at least one zero validity-mask sample"
        )
    if contract.status in _NO_QUANTITATIVE_CLAIM_STATUSES and valid.any():
        raise QuantitativeValidityError(
            f"{contract.status.value} cannot contain quantitatively valid samples"
        )
    if valid.any() and not contract.has_quantitative_claim:
        raise QuantitativeValidityError(
            "a no-claim or diagnostic-only scope cannot contain quantitatively valid samples"
        )
    if (
        contract.status is QuantitativeValidityStatus.NUMERICAL_FAILURE
        and time.size
        and (contract.first_invalid_time_s is None or contract.termination_interval_s is None)
    ):
        raise QuantitativeValidityError(
            "a numerical_failure result with recorded samples requires a termination interval"
        )
    if time.size and valid.any():
        if contract.valid_until_s is None:
            raise QuantitativeValidityError(
                "a result with valid samples must declare valid_until_s"
            )
        tolerance = max(1.0e-15, 1.0e-12 * max(1.0, contract.valid_until_s))
        if float(np.max(time[valid])) > contract.valid_until_s + tolerance:
            raise QuantitativeValidityError("valid mask extends beyond valid_until_s")
    if time.size and contract.valid_until_s is not None:
        tolerance = max(1.0e-15, 1.0e-12 * max(1.0, contract.valid_until_s))
        if np.any(invalid & (time < contract.valid_until_s - tolerance)):
            raise QuantitativeValidityError(
                "validity mask rejects a sample before the declared valid interval ends"
            )
    if time.size and contract.first_invalid_time_s is not None:
        tolerance = max(
            1.0e-15, 1.0e-12 * max(1.0, contract.first_invalid_time_s)
        )
        if np.any(valid & (time >= contract.first_invalid_time_s - tolerance)):
            raise QuantitativeValidityError(
                "validity mask accepts a sample at or after first_invalid_time_s"
            )
    return contract


def quantitative_validity(result: Any) -> QuantitativeValidity:
    """Return an explicit contract, synthesizing fail-closed legacy identity."""

    metadata = getattr(result, "metadata", None)
    observables = getattr(result, "observables", None)
    time_s = getattr(result, "time_s", ())
    if not isinstance(metadata, Mapping) or not isinstance(observables, Mapping):
        return validate_quantitative_validity_payload(
            legacy_unassessed_validity("result does not expose metadata and observables mappings")
        )
    contract = validate_result_quantitative_validity(time_s, observables, metadata)
    if contract is not None:
        return contract
    return validate_quantitative_validity_payload(
        legacy_unassessed_validity(
            "legacy result has no quantitative-validity contract; no sample qualification inferred"
        )
    )


def quantitative_valid_mask(result: Any) -> NDArray[np.bool_]:
    """Return a defensive Boolean mask; missing legacy contracts yield all false."""

    time = np.asarray(getattr(result, "time_s", ()), dtype=np.float64)
    metadata = getattr(result, "metadata", None)
    observables = getattr(result, "observables", None)
    if not isinstance(metadata, Mapping) or not isinstance(observables, Mapping):
        return np.zeros(time.shape, dtype=np.bool_)
    contract = validate_result_quantitative_validity(time, observables, metadata)
    if contract is None:
        return np.zeros(time.shape, dtype=np.bool_)
    return np.asarray(observables[QUANTITATIVE_VALID_MASK], dtype=np.float64) == 1.0


def with_legacy_unassessed_validity(
    result: "SimulationResult",
    *,
    reason: str = (
        "result producer omitted the quantitative-validity contract; archive preserved "
        "without inferring sample qualification"
    ),
) -> "SimulationResult":
    """Add an explicit zero mask to an otherwise complete legacy result."""

    metadata = getattr(result, "metadata", None)
    observables = getattr(result, "observables", None)
    if not isinstance(metadata, Mapping) or not isinstance(observables, Mapping):
        raise TypeError("result must expose metadata and observables mappings")
    present = validate_result_quantitative_validity(result.time_s, observables, metadata)
    if present is not None:
        return result
    from .protocols import SimulationResult

    enriched_metadata = dict(metadata)
    enriched_metadata[QUANTITATIVE_VALIDITY_KEY] = legacy_unassessed_validity(reason)
    enriched_observables = dict(observables)
    enriched_observables[QUANTITATIVE_VALID_MASK] = np.zeros(
        np.asarray(result.time_s).shape, dtype=np.float64
    )
    units = enriched_metadata.get("observable_units")
    if isinstance(units, Mapping):
        enriched_units = dict(units)
        enriched_units[QUANTITATIVE_VALID_MASK] = "1"
        enriched_metadata["observable_units"] = enriched_units
    return SimulationResult(
        backend=result.backend,
        time_s=result.time_s,
        observables=enriched_observables,
        fields=result.fields,
        events=result.events,
        metadata=enriched_metadata,
        status=result.status,
    )


def quantitative_series(
    result: Any,
    observable: str,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Extract only mask-qualified scalar samples from one result."""

    if observable == QUANTITATIVE_VALID_MASK:
        raise QuantitativeValidityError("the validity mask is not a physical observable")
    observables = getattr(result, "observables", None)
    if not isinstance(observables, Mapping) or observable not in observables:
        raise QuantitativeValidityError(f"result does not contain observable {observable!r}")
    time = np.asarray(getattr(result, "time_s", ()), dtype=np.float64)
    values = np.asarray(observables[observable], dtype=np.float64)
    if values.ndim != 1 or values.shape != time.shape:
        raise QuantitativeValidityError(
            f"observable {observable!r} must be scalar over the result time axis"
        )
    mask = quantitative_valid_mask(result)
    return time[mask].copy(), values[mask].copy()


__all__ = [
    "QUANTITATIVE_VALIDITY_KEY",
    "QUANTITATIVE_VALIDITY_VERSION",
    "QUANTITATIVE_VALID_MASK",
    "QuantitativeClaimScope",
    "QuantitativeValidity",
    "QuantitativeValidityError",
    "QuantitativeValidityStatus",
    "legacy_unassessed_validity",
    "make_quantitative_validity",
    "quantitative_series",
    "quantitative_valid_mask",
    "quantitative_validity",
    "validate_quantitative_validity_payload",
    "validate_result_quantitative_validity",
    "with_legacy_unassessed_validity",
]
