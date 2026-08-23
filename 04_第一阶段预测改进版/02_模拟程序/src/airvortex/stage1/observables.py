"""Shared surface observation operator and event-state adapter."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

import numpy as np

from airvortex.core import Event, RunStatus, SimulationResult

from .contracts import (
    EVENT_CONTRACT_VERSION,
    PredictionState,
    contract_document,
    require_finite_parameter,
)


@dataclass(frozen=True, slots=True)
class SurfaceObservationOperator:
    """The single spatial operator used by both Stage1 predictors."""

    central_observation_radius_m: float
    outer_annulus_inner_radius_m: float
    outer_annulus_outer_radius_m: float
    interface_level: float
    static_reference_height_m: float
    static_reference_source: str
    central_aggregation: str = "minimum"
    outer_aggregation: str = "annular_area_weighted_mean"

    def __post_init__(self) -> None:
        central = require_finite_parameter(
            self.central_observation_radius_m,
            "central_observation_radius_m",
        )
        inner = require_finite_parameter(
            self.outer_annulus_inner_radius_m,
            "outer_annulus_inner_radius_m",
        )
        outer = require_finite_parameter(
            self.outer_annulus_outer_radius_m,
            "outer_annulus_outer_radius_m",
        )
        level = require_finite_parameter(self.interface_level, "interface_level")
        reference = require_finite_parameter(
            self.static_reference_height_m,
            "static_reference_height_m",
        )
        if central <= 0.0:
            raise ValueError("central_observation_radius_m must be positive")
        if not 0.0 < inner < outer:
            raise ValueError("outer annulus radii must satisfy 0 < inner < outer")
        if central >= inner:
            raise ValueError("central ROI must not overlap the outer annulus")
        if not 0.0 < level < 1.0:
            raise ValueError("interface_level must lie in (0, 1)")
        if reference <= 0.0:
            raise ValueError("static_reference_height_m must be positive")
        if not isinstance(self.static_reference_source, str) or not self.static_reference_source.strip():
            raise ValueError("static_reference_source must be non-empty text")
        if self.central_aggregation != "minimum":
            raise ValueError("central_aggregation must be 'minimum'")
        if self.outer_aggregation != "annular_area_weighted_mean":
            raise ValueError(
                "outer_aggregation must be 'annular_area_weighted_mean'"
            )
        object.__setattr__(self, "central_observation_radius_m", central)
        object.__setattr__(self, "outer_annulus_inner_radius_m", inner)
        object.__setattr__(self, "outer_annulus_outer_radius_m", outer)
        object.__setattr__(self, "interface_level", level)
        object.__setattr__(self, "static_reference_height_m", reference)
        object.__setattr__(
            self, "static_reference_source", self.static_reference_source.strip()
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "central_observation_radius_m": self.central_observation_radius_m,
            "outer_annulus_inner_radius_m": self.outer_annulus_inner_radius_m,
            "outer_annulus_outer_radius_m": self.outer_annulus_outer_radius_m,
            "interface_level": self.interface_level,
            "static_reference_height_m": self.static_reference_height_m,
            "static_reference_source": self.static_reference_source,
            "central_aggregation": self.central_aggregation,
            "outer_aggregation": self.outer_aggregation,
        }

    @property
    def operator_id(self) -> str:
        encoded = json.dumps(
            self.parameters,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return f"canonical-surface-v1:{hashlib.sha256(encoded).hexdigest()[:20]}"


def surface_operator_from_config(config: Any) -> SurfaceObservationOperator:
    """Derive the shared operator only from common, persisted run parameters."""

    radius = float(config.container.radius_m)
    return SurfaceObservationOperator(
        central_observation_radius_m=float(
            config.forward.effective_target_radius_m
        ),
        outer_annulus_inner_radius_m=0.8 * radius,
        outer_annulus_outer_radius_m=radius,
        interface_level=0.5,
        static_reference_height_m=float(config.container.liquid_depth_m),
        static_reference_source=(
            "nominal_configured_liquid_depth_not_measured_H0"
        ),
    )


def canonical_surface_representation(
    result: SimulationResult,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert either backend result to ``(radius, surface_height[time, radius])``."""

    radial_raw = result.fields.get("r_m", result.fields.get("radius_m"))
    profile_raw = result.observables.get("surface_profile_m")
    if radial_raw is None or profile_raw is None:
        raise ValueError(
            "backend result lacks r_m/radius_m and surface_profile_m required "
            "for the canonical surface representation"
        )
    radius = np.asarray(radial_raw, dtype=np.float64)
    profiles = np.asarray(profile_raw, dtype=np.float64)
    if radius.ndim != 1 or radius.size < 2:
        raise ValueError("canonical surface radius must be a 1D array with >=2 points")
    if profiles.ndim != 2 or profiles.shape != (result.time_s.size, radius.size):
        raise ValueError(
            "surface_profile_m must have shape (time, radius); got "
            f"{profiles.shape} for {(result.time_s.size, radius.size)}"
        )
    if (
        not np.isfinite(radius).all()
        or not np.isfinite(profiles).all()
        or radius[0] < 0.0
        or np.any(np.diff(radius) <= 0.0)
    ):
        raise ValueError("canonical surface representation must be finite and ordered")
    return radius, profiles


def _radial_cell_edges(radius_m: np.ndarray) -> np.ndarray:
    edges = np.empty(radius_m.size + 1, dtype=np.float64)
    edges[1:-1] = 0.5 * (radius_m[:-1] + radius_m[1:])
    edges[0] = max(0.0, radius_m[0] - 0.5 * (radius_m[1] - radius_m[0]))
    edges[-1] = radius_m[-1] + 0.5 * (radius_m[-1] - radius_m[-2])
    return edges


def apply_surface_observation_operator(
    radius_m: np.ndarray,
    surface_height_m: np.ndarray,
    operator: SurfaceObservationOperator,
) -> dict[str, np.ndarray]:
    """Apply the exact same primitive and derived calculation to either backend."""

    radius = np.asarray(radius_m, dtype=np.float64)
    profiles = np.asarray(surface_height_m, dtype=np.float64)
    if radius.ndim != 1 or profiles.ndim != 2 or profiles.shape[1] != radius.size:
        raise ValueError("surface observation requires radius and (time, radius) heights")
    if not np.isfinite(radius).all() or not np.isfinite(profiles).all():
        raise ValueError("surface observation inputs must be finite")
    if np.any(np.diff(radius) <= 0.0) or radius[0] < 0.0:
        raise ValueError("surface observation radius must be non-negative and increasing")

    central_mask = radius <= operator.central_observation_radius_m
    central_samples = profiles[:, central_mask]
    # Include interpolated r=0 and ROI-edge values so a coarse representation
    # cannot silently redefine the physical central radius.
    endpoints = np.stack(
        [
            np.interp(
                [0.0, operator.central_observation_radius_m],
                radius,
                profile,
            )
            for profile in profiles
        ]
    )
    if central_samples.size:
        central_samples = np.concatenate((central_samples, endpoints), axis=1)
    else:
        central_samples = endpoints
    central_minimum = np.min(central_samples, axis=1)

    edges = _radial_cell_edges(radius)
    lower = np.maximum(edges[:-1], operator.outer_annulus_inner_radius_m)
    upper = np.minimum(edges[1:], operator.outer_annulus_outer_radius_m)
    annular_weights = np.maximum(upper * upper - lower * lower, 0.0)
    weight_sum = float(np.sum(annular_weights))
    if weight_sum <= np.finfo(float).tiny:
        raise ValueError("surface radial grid does not cover the configured outer annulus")
    outer_reference = profiles @ annular_weights / weight_sum

    return {
        "central_surface_min_height_m": central_minimum,
        "outer_reference_height_m": outer_reference,
        "central_depression_from_static_m": (
            operator.static_reference_height_m - central_minimum
        ),
        "visible_outer_to_center_depth_m": outer_reference - central_minimum,
        "surface_height_range_m": np.ptp(profiles, axis=1),
    }


_EVENT_MAP: dict[str, tuple[str, PredictionState]] = {
    "first_target_touch": ("geometric_touch", PredictionState.GEOMETRIC_TOUCH),
    "gas_touches_bar": ("geometric_touch", PredictionState.GEOMETRIC_TOUCH),
    "finite_channel": (
        "connected_unresolved",
        PredictionState.CONNECTED_UNRESOLVED,
    ),
    "resolved_channel": ("connected_resolved", PredictionState.CONNECTED_RESOLVED),
    "channel_reconnect": ("connected_resolved", PredictionState.CONNECTED_RESOLVED),
    "channel_reconnection": (
        "connected_resolved",
        PredictionState.CONNECTED_RESOLVED,
    ),
    "channel_break": (
        "broken_after_connection",
        PredictionState.BROKEN_AFTER_CONNECTION,
    ),
    "sustained_channel": (
        "connected_sustained_numerical",
        PredictionState.CONNECTED_SUSTAINED_NUMERICAL,
    ),
    "sustained_channel_numerical": (
        "connected_sustained_numerical",
        PredictionState.CONNECTED_SUSTAINED_NUMERICAL,
    ),
    "domain_limit": ("domain_limit", PredictionState.DOMAIN_LIMIT),
    "model_domain_exit": (
        "invalid_model_domain",
        PredictionState.INVALID_MODEL_DOMAIN,
    ),
    "numerical_failure": (
        "failed_numerically",
        PredictionState.FAILED_NUMERICALLY,
    ),
}


def canonicalize_events(
    result: SimulationResult,
    *,
    evidence_time_s: np.ndarray | None = None,
) -> tuple[Event, ...]:
    """Map backend events while retaining dense localisation evidence."""

    evidence = np.asarray(
        result.time_s if evidence_time_s is None else evidence_time_s,
        dtype=np.float64,
    )
    if evidence.ndim != 1 or not np.isfinite(evidence).all():
        raise ValueError("event evidence times must be a finite 1D sequence")
    source_events = list(result.events)
    source_kinds = {event.kind.strip().casefold() for event in source_events}
    domain_status = str(result.metadata.get("domain_status", "")).strip().upper()
    if (
        domain_status == "OUT_OF_MODEL_DOMAIN"
        and not source_kinds.intersection({"domain_limit", "model_domain_exit"})
    ):
        source_events.append(
            Event(
                float(result.time_s[-1]) if result.time_s.size else 0.0,
                "model_domain_exit",
                "Backend reported OUT_OF_MODEL_DOMAIN.",
                {"source": "result.metadata.domain_status"},
            )
        )
    if result.status is RunStatus.FAILED and "numerical_failure" not in source_kinds:
        source_events.append(
            Event(
                float(result.time_s[-1]) if result.time_s.size else 0.0,
                "numerical_failure",
                "Backend returned failed run status.",
                {"source": "result.status"},
            )
        )

    output: list[Event] = []
    for event in source_events:
        source_kind = event.kind.strip().casefold()
        kind, state = _EVENT_MAP.get(
            source_kind,
            (source_kind, PredictionState.DISCONNECTED),
        )
        index = int(np.searchsorted(evidence, event.time_s, side="left"))
        lower = 0.0 if index <= 0 else float(evidence[min(index - 1, evidence.size - 1)])
        upper = (
            float(event.time_s)
            if evidence.size == 0 or index >= evidence.size
            else float(evidence[index])
        )
        data = dict(event.data)
        data.update(
            {
                "event_contract_version": EVENT_CONTRACT_VERSION,
                "source_event_kind": event.kind,
                "time_lower_s": lower,
                "time_upper_s": upper,
                "time_estimate_s": event.time_s,
                "bracket_width_s": max(0.0, upper - lower),
                "prediction_state": state.value,
            }
        )
        output.append(Event(event.time_s, kind, event.message, data))
    return tuple(output)


def _topology_parameters(config: Any, backend: str) -> dict[str, Any]:
    if backend == "high_fidelity":
        hf = config.high_fidelity
        height = config.container.liquid_depth_m + config.container.headspace_height_m
        spacing = max(
            2.0 * config.container.radius_m / hf.radial_cells,
            2.0 * config.container.radius_m / hf.azimuthal_cells,
            height / hf.axial_cells,
        )
        interface_thickness = hf.interface_thickness_cells * min(
            2.0 * config.container.radius_m / hf.radial_cells,
            2.0 * config.container.radius_m / hf.azimuthal_cells,
            height / hf.axial_cells,
        )
        return {
            "algorithm": "three_dimensional_max_min_widest_path",
            "gas_threshold": 0.5,
            "minimum_resolved_throat_cells": hf.minimum_resolved_throat_cells,
            "interface_thickness_m": interface_thickness,
            "required_resolved_radius_m": max(
                hf.minimum_resolved_throat_cells * spacing,
                1.5 * interface_thickness,
                2.0 * spacing,
            ),
        }
    return {
        "algorithm": "axisymmetric_forward_topology",
        "gas_threshold": 0.5,
        "minimum_resolved_throat_cells": config.forward.minimum_resolved_throat_cells,
        "interface_thickness_cells": config.forward.interface_thickness_cells,
    }


def _canonicalize(
    result: SimulationResult,
    config: Any,
    *,
    backend_id: str,
    backend_kind: str,
    evidence_time_s: np.ndarray | None,
) -> SimulationResult:
    radius, profiles = canonical_surface_representation(result)
    operator = surface_operator_from_config(config)
    shared = apply_surface_observation_operator(radius, profiles, operator)
    source = result.observables
    obsolete = {
        "funnel_depth_m",
        "surface_depth_m",
        "surface_depth",
        "visible_funnel_depth_m",
        "nominal_central_depression_m",
    }
    observables = {
        key: np.asarray(value)
        for key, value in source.items()
        if key not in obsolete
    }
    observables.update(shared)
    observables["surface_depth_m"] = shared[
        "visible_outer_to_center_depth_m"
    ].copy()
    if backend_kind == "high_fidelity":
        if "connection_candidate" in source:
            observables["connected_resolved"] = np.asarray(
                source["connection_candidate"], dtype=np.float64
            )
        if "connection_minimum_throat_radius_m" in source:
            observables["channel_bottleneck_radius_m"] = np.asarray(
                source["connection_minimum_throat_radius_m"], dtype=np.float64
            )

    metadata = dict(result.metadata)
    units_raw = metadata.get("observable_units", {})
    units = dict(units_raw) if isinstance(units_raw, Mapping) else {}
    for key in (
        "central_surface_min_height_m",
        "outer_reference_height_m",
        "central_depression_from_static_m",
        "visible_outer_to_center_depth_m",
        "surface_depth_m",
        "surface_height_range_m",
        "channel_bottleneck_radius_m",
    ):
        if key in observables:
            units[key] = "m"
    if "connected_resolved" in observables:
        units["connected_resolved"] = "1"
    metadata["observable_units"] = units
    metadata["backend_id"] = backend_id
    metadata["surface_observation_operator"] = {
        **operator.parameters,
        "operator_id": operator.operator_id,
    }
    metadata["canonical_surface_representation"] = {
        "schema": "radius-plus-external-surface-height-v1",
        "radius_field": "r_m",
        "height_observable": "surface_profile_m",
        "interface_level": operator.interface_level,
        "backend_conversion": (
            "axisymmetric_surface_profile"
            if backend_kind == "forward"
            else "azimuthal_mean_of_3d_alpha_interface"
        ),
    }
    topology_parameters = _topology_parameters(config, backend_kind)
    metadata["topology_operator_parameters"] = topology_parameters
    metadata["observable_contract"] = contract_document(
        observables,
        surface_operator_parameters=operator.parameters,
        topology_operator_parameters=topology_parameters,
    )
    metadata["event_state_machine"] = {
        "version": EVENT_CONTRACT_VERSION,
        "source_to_state": {
            key: state.value for key, (_kind, state) in _EVENT_MAP.items()
        },
        "event_evidence_sampling": "internal integration or event-localisation grid",
        "ordinary_output_sampling": "user configured output_interval_s",
    }
    return SimulationResult(
        result.backend,
        result.time_s,
        observables,
        result.fields,
        canonicalize_events(result, evidence_time_s=evidence_time_s),
        metadata,
        result.status,
    )


def canonicalize_forward(
    result: SimulationResult,
    config: Any,
    *,
    event_evidence_time_s: np.ndarray | None = None,
) -> SimulationResult:
    canonical = _canonicalize(
        result,
        config,
        backend_id="forward_stage1_predictive",
        backend_kind="forward",
        evidence_time_s=event_evidence_time_s,
    )
    metadata = dict(canonical.metadata)
    metadata["comparison_role"] = "predictor_after_independent_calibration"
    return SimulationResult(
        canonical.backend,
        canonical.time_s,
        canonical.observables,
        canonical.fields,
        canonical.events,
        metadata,
        canonical.status,
    )


def canonicalize_hf3d(
    result: SimulationResult,
    config: Any,
    *,
    event_evidence_time_s: np.ndarray | None = None,
) -> SimulationResult:
    canonical = _canonicalize(
        result,
        config,
        backend_id="high_fidelity_stage1_local_law",
        backend_kind="high_fidelity",
        evidence_time_s=event_evidence_time_s,
    )
    metadata = dict(canonical.metadata)
    metadata["comparison_role"] = "independent_predictor_after_physical_GO"
    metadata["physical_prediction_status"] = (
        "NO_GO_UNTIL_FROZEN_REFINEMENT_AND_EXPERIMENT_VALIDATION"
    )
    return SimulationResult(
        canonical.backend,
        canonical.time_s,
        canonical.observables,
        canonical.fields,
        canonical.events,
        metadata,
        canonical.status,
    )


__all__ = [
    "SurfaceObservationOperator",
    "apply_surface_observation_operator",
    "canonical_surface_representation",
    "canonicalize_events",
    "canonicalize_forward",
    "canonicalize_hf3d",
    "surface_operator_from_config",
]
