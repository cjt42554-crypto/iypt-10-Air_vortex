"""Shared exceptions and small validation helpers for :mod:`airvortex.analysis`.

The analysis package deliberately has no GUI imports.  All public validation
errors derive from :class:`AnalysisError`, making them suitable for a CLI or a
desktop controller to translate into a user-facing message.
"""

from __future__ import annotations

import math
from typing import Any


class AnalysisError(ValueError):
    """Base class for invalid analysis inputs or incompatible results."""


class DataValidationError(AnalysisError):
    """Raised when an experiment or time series violates its data contract."""


class RoleViolationError(AnalysisError):
    """Raised when calibration and sealed-validation roles are mixed."""


class NoCommonIntervalError(AnalysisError):
    """Raised when two time series do not overlap in time."""


def finite_float(value: Any, name: str, *, positive: bool = False) -> float:
    """Return *value* as a finite float and enforce an optional positive bound."""

    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"{name} must be a real number") from exc
    if not math.isfinite(result):
        raise DataValidationError(f"{name} must be finite")
    if positive and result <= 0.0:
        raise DataValidationError(f"{name} must be greater than zero")
    return result
