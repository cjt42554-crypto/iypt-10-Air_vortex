"""Calibration/sealed-validation role gates with an auditable freeze point."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping, TypeVar

from ._common import DataValidationError, RoleViolationError
from .experiment import CALIBRATION, SEALED_VALIDATION, ExperimentDataset


Parameters = Mapping[str, float]
Prediction = TypeVar("Prediction")


def _ensure_scientifically_eligible(dataset: ExperimentDataset) -> None:
    """Reject demonstration/synthetic data before any fit or validation action."""

    metadata = dataset.metadata
    evidence_status = metadata.get("evidence_status")
    if "evidence_status" in metadata and (
        not isinstance(evidence_status, str) or not evidence_status.strip()
    ):
        raise RoleViolationError(
            "evidence_status must be a non-empty JSON string when present"
        )
    for flag_name in (
        "is_demonstration",
        "synthetic",
        "calibration_validation_eligible",
    ):
        if flag_name in metadata and type(metadata[flag_name]) is not bool:
            raise RoleViolationError(
                f"{flag_name} must be a JSON boolean when present"
            )
    is_demonstration = (
        isinstance(evidence_status, str)
        and evidence_status.strip().casefold() == "demonstration"
    ) or metadata.get("is_demonstration") is True
    is_synthetic = metadata.get("synthetic") is True
    declared_eligibility = metadata.get("calibration_validation_eligible")
    explicitly_ineligible = declared_eligibility is False
    if is_demonstration or is_synthetic or explicitly_ineligible:
        raise RoleViolationError(
            "demonstration, synthetic, or explicitly ineligible data may not be "
            "used for calibration or validation"
        )


def ensure_fit_allowed(
    dataset: ExperimentDataset,
    *,
    validation_started: bool = False,
    allow_refit: bool = False,
) -> None:
    """Enforce experiment roles and the validation-stage refit default."""

    _ensure_scientifically_eligible(dataset)
    if dataset.role != CALIBRATION:
        raise RoleViolationError(
            "fitting requires a calibration dataset; sealed validation data may never be fit"
        )
    if validation_started and not allow_refit:
        raise RoleViolationError(
            "sealed validation has started; refitting is rejected by default"
        )


def ensure_validation_allowed(dataset: ExperimentDataset) -> None:
    _ensure_scientifically_eligible(dataset)
    if dataset.role != SEALED_VALIDATION:
        raise RoleViolationError(
            "validation scoring requires a sealed_validation dataset"
        )


@dataclass(frozen=True)
class CalibrationAuditEvent:
    action: str
    timestamp_utc: str
    detail: str


@dataclass
class CalibrationGuard:
    """Stateful guard that freezes model parameters before blind validation.

    ``allow_refit=True`` is an explicit escape hatch for exploratory work.  It
    records an audit event and invalidates the old validation stage; data seen
    under that model version must no longer be described as sealed validation.
    """

    _stage: str = field(default=CALIBRATION, init=False, repr=False)
    _latest_parameters: dict[str, float] | None = field(default=None, init=False, repr=False)
    _frozen_parameters: Mapping[str, float] | None = field(default=None, init=False, repr=False)
    _audit: list[CalibrationAuditEvent] = field(default_factory=list, init=False, repr=False)

    @property
    def stage(self) -> str:
        return self._stage

    @property
    def validation_started(self) -> bool:
        return self._stage == SEALED_VALIDATION

    @property
    def frozen_parameters(self) -> Mapping[str, float]:
        if self._frozen_parameters is None:
            raise RoleViolationError("parameters have not been frozen")
        return self._frozen_parameters

    @property
    def audit_log(self) -> tuple[CalibrationAuditEvent, ...]:
        return tuple(self._audit)

    def _record(self, action: str, detail: str) -> None:
        self._audit.append(
            CalibrationAuditEvent(
                action=action,
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                detail=detail,
            )
        )

    @staticmethod
    def _validate_parameters(parameters: Mapping[str, Any]) -> dict[str, float]:
        if not isinstance(parameters, Mapping) or not parameters:
            raise DataValidationError("fitter must return a non-empty parameter mapping")
        checked: dict[str, float] = {}
        for name, raw in parameters.items():
            if not isinstance(name, str) or not name:
                raise DataValidationError("parameter names must be non-empty strings")
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise DataValidationError(f"parameter {name!r} is not numeric") from exc
            if not math.isfinite(value):
                raise DataValidationError(f"parameter {name!r} must be finite")
            checked[name] = value
        return checked

    def fit(
        self,
        dataset: ExperimentDataset,
        fitter: Callable[[ExperimentDataset], Parameters],
        *,
        allow_refit: bool = False,
    ) -> Mapping[str, float]:
        """Fit only calibration data, rejecting post-freeze refits by default."""

        ensure_fit_allowed(
            dataset,
            validation_started=self.validation_started,
            allow_refit=allow_refit,
        )
        if self.validation_started and allow_refit:
            self._record(
                "validation_invalidated",
                "explicit refit requested after validation started",
            )
            self._stage = CALIBRATION
            self._frozen_parameters = None
        parameters = self._validate_parameters(fitter(dataset))
        self._latest_parameters = deepcopy(parameters)
        self._record("fit", f"fit {len(parameters)} parameters on calibration data")
        return MappingProxyType(deepcopy(parameters))

    def freeze(self, parameters: Mapping[str, Any] | None = None) -> Mapping[str, float]:
        """Freeze parameters and enter the sealed-validation stage."""

        if parameters is None:
            if self._latest_parameters is None:
                raise RoleViolationError("cannot freeze before fitting or providing parameters")
            checked = deepcopy(self._latest_parameters)
        else:
            checked = self._validate_parameters(parameters)
        self._frozen_parameters = MappingProxyType(deepcopy(checked))
        self._stage = SEALED_VALIDATION
        self._record("freeze", f"froze {len(checked)} parameters")
        return self._frozen_parameters

    def validate(
        self,
        dataset: ExperimentDataset,
        predictor: Callable[[Mapping[str, float], ExperimentDataset], Prediction],
    ) -> Prediction:
        """Evaluate the frozen parameters on a sealed-validation dataset."""

        ensure_validation_allowed(dataset)
        if not self.validation_started:
            raise RoleViolationError("freeze parameters before opening sealed validation data")
        prediction = predictor(self.frozen_parameters, dataset)
        self._record("validate", "evaluated frozen parameters on sealed validation data")
        return prediction


CalibrationSession = CalibrationGuard
