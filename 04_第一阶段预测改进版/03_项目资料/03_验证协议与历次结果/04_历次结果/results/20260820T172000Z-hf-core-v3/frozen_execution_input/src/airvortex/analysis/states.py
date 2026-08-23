"""Operational S0--S4 connectivity classification used by models and experiments."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import TYPE_CHECKING, Iterable

import numpy as np

from ._common import DataValidationError, finite_float

if TYPE_CHECKING:
    from .experiment import ExperimentDataset


class ConnectivityState(str, Enum):
    S0 = "S0"  # flat; no resolvable central depression
    S1 = "S1"  # funnel without contact
    S2 = "S2"  # intermittent contact, detached bubbles, or broken connection
    S3 = "S3"  # sustained atmosphere-to-bar connected air core
    S4 = "S4"  # violent/non-slender regime or bar loss of synchronism


@dataclass(frozen=True)
class StateClassification:
    state: ConnectivityState
    reason: str
    required_hold_s: float | None = None


def required_connection_hold_time(angular_speed: float | None) -> float:
    """Return ``max(5 s, 20 revolutions)`` for the measured bar speed."""

    if angular_speed is None:
        return 5.0
    omega = finite_float(angular_speed, "angular_speed")
    if omega <= 0.0:
        return 5.0
    return max(5.0, 20.0 * 2.0 * math.pi / omega)


def classify_connectivity(
    *,
    depression_depth: float,
    detection_limit: float,
    connectivity_fraction: float = 0.0,
    observation_duration: float = 0.0,
    angular_speed: float | None = None,
    intermittent_contact: bool = False,
    detached_bubbles: bool = False,
    unstable: bool = False,
    bar_desynchronized: bool = False,
) -> StateClassification:
    """Classify one observation window using preregistered operational rules.

    ``connectivity_fraction`` is the fraction of frames containing one
    continuous air-liquid interface from atmosphere to bar.  Detached bubbles
    never count as continuity.  S3 requires at least 95% connected frames and
    a window of ``max(5 s, 20 rotations)``.
    """

    depth = finite_float(depression_depth, "depression_depth")
    resolution = finite_float(detection_limit, "detection_limit", positive=True)
    fraction = finite_float(connectivity_fraction, "connectivity_fraction")
    duration = finite_float(observation_duration, "observation_duration")
    if depth < 0.0:
        raise DataValidationError("depression_depth must be non-negative")
    if not 0.0 <= fraction <= 1.0:
        raise DataValidationError("connectivity_fraction must lie in [0, 1]")
    if duration < 0.0:
        raise DataValidationError("observation_duration must be non-negative")

    hold = required_connection_hold_time(angular_speed)
    if unstable or bar_desynchronized:
        cause = "violent/non-slender flow" if unstable else "bar desynchronization"
        return StateClassification(ConnectivityState.S4, cause, hold)
    if fraction >= 0.95 and duration >= hold and not detached_bubbles:
        return StateClassification(
            ConnectivityState.S3,
            f"connected in {fraction:.1%} of frames for {duration:g} s",
            hold,
        )
    if fraction > 0.0 or intermittent_contact or detached_bubbles:
        if detached_bubbles and fraction == 0.0:
            reason = "detached bubbles without a continuous atmospheric path"
        else:
            reason = "intermittent or insufficiently sustained connection"
        return StateClassification(ConnectivityState.S2, reason, hold)
    if depth > resolution:
        return StateClassification(
            ConnectivityState.S1,
            "resolvable funnel without bar connection",
            hold,
        )
    return StateClassification(
        ConnectivityState.S0,
        "central depression does not exceed the detection limit",
        hold,
    )


def classify_experiment_window(
    dataset: "ExperimentDataset",
    indices: slice | Iterable[int] | None = None,
    *,
    detection_limit: float,
) -> StateClassification:
    """Classify an experiment window and diagnose every required missing column.

    Required canonical columns are ``surface_depth`` and
    ``connectivity_fraction``.  Optional binary columns are
    ``intermittent_contact``, ``detached_bubbles``, ``unstable`` and
    ``bar_desynchronized``.  Angular speed is taken from ``bar_speed`` when
    available.  Values are assumed to be in the SI units established by the
    experiment importer.
    """

    required = {"surface_depth", "connectivity_fraction"}
    missing = sorted(required.difference(dataset.observables))
    if missing:
        available = ", ".join(sorted(dataset.observables)) or "<none>"
        raise DataValidationError(
            "cannot classify experiment: missing required canonical columns "
            f"{', '.join(missing)}; available: {available}"
        )
    selected: slice | np.ndarray
    if indices is None:
        selected = slice(None)
    elif isinstance(indices, slice):
        selected = indices
    else:
        selected = np.asarray(list(indices), dtype=int)
    time = np.asarray(dataset.time_s[selected], dtype=float)
    if len(time) == 0:
        raise DataValidationError("classification window is empty")
    duration = float(time[-1] - time[0]) if len(time) > 1 else 0.0
    depth = float(np.max(dataset.observables["surface_depth"][selected]))
    connected = np.asarray(
        dataset.observables["connectivity_fraction"][selected], dtype=float
    )
    # Per-frame probability/score is treated conservatively: a frame is
    # connected only at >=0.95, then the frame fraction is used by S3.
    fraction = float(np.mean(connected >= 0.95))

    def any_flag(name: str) -> bool:
        values = dataset.observables.get(name)
        return bool(values is not None and np.any(np.asarray(values[selected]) >= 0.5))

    omega: float | None = None
    if "bar_speed" in dataset.observables:
        omega = float(np.mean(dataset.observables["bar_speed"][selected]))
    return classify_connectivity(
        depression_depth=depth,
        detection_limit=detection_limit,
        connectivity_fraction=fraction,
        observation_duration=duration,
        angular_speed=omega,
        intermittent_contact=any_flag("intermittent_contact"),
        detached_bubbles=any_flag("detached_bubbles"),
        unstable=any_flag("unstable"),
        bar_desynchronized=any_flag("bar_desynchronized"),
    )
