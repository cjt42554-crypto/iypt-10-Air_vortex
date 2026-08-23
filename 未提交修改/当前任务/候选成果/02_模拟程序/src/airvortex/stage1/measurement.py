"""Explicit model-to-camera measurement operators."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

import numpy as np
from scipy.ndimage import gaussian_filter, zoom

from airvortex.core import SimulationResult

from .contracts import MEASUREMENT_CONTRACT_VERSION, contract_document
from .observables import (
    SurfaceObservationOperator,
    canonical_surface_representation,
    surface_operator_from_config,
)


def _finite_positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return numeric


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CameraModel:
    """Calibratable ideal-camera parameters.

    Cylindrical-wall refraction deliberately remains disabled until a wall
    geometry and refractive-index calibration are supplied.
    """

    view_axis: str = "y"
    pixel_size_m: float = 1.0e-4
    psf_sigma_pixels: float = 0.0
    segmentation_threshold: float = 0.5
    projection_model_id: str = "ideal_orthographic_side_silhouette_v1"
    refraction_model_id: str = "not_applied_uncalibrated_cylindrical_wall_v1"

    def __post_init__(self) -> None:
        if self.view_axis not in {"x", "y"}:
            raise ValueError("view_axis must be x or y")
        pixel = _finite_positive(self.pixel_size_m, "pixel_size_m")
        psf = _finite(self.psf_sigma_pixels, "psf_sigma_pixels")
        threshold = _finite(
            self.segmentation_threshold, "segmentation_threshold"
        )
        if psf < 0.0:
            raise ValueError("psf_sigma_pixels must be non-negative")
        if not 0.0 < threshold < 1.0:
            raise ValueError("segmentation_threshold must be in (0, 1)")
        if self.projection_model_id != "ideal_orthographic_side_silhouette_v1":
            raise ValueError("unsupported projection_model_id")
        if self.refraction_model_id != "not_applied_uncalibrated_cylindrical_wall_v1":
            raise ValueError(
                "refraction remains unavailable until a calibrated wall model is implemented"
            )
        object.__setattr__(self, "pixel_size_m", pixel)
        object.__setattr__(self, "psf_sigma_pixels", psf)
        object.__setattr__(self, "segmentation_threshold", threshold)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "view_axis": self.view_axis,
            "pixel_size_m": self.pixel_size_m,
            "psf_sigma_pixels": self.psf_sigma_pixels,
            "segmentation_threshold": self.segmentation_threshold,
            "projection_model_id": self.projection_model_id,
            "refraction_model_id": self.refraction_model_id,
        }

    @property
    def operator_id(self) -> str:
        return f"camera-image-v2:{_digest(self.parameters)[:20]}"


def project_phase_to_camera(
    phase: np.ndarray,
    *,
    spacing_x_m: float,
    spacing_y_m: float,
    spacing_z_m: float,
    camera: CameraModel,
) -> dict[str, Any]:
    """Project a finite 3D liquid fraction through the ideal camera chain."""

    if not isinstance(camera, CameraModel):
        raise TypeError("camera must be CameraModel")
    spacing_x = _finite_positive(spacing_x_m, "spacing_x_m")
    spacing_y = _finite_positive(spacing_y_m, "spacing_y_m")
    spacing_z = _finite_positive(spacing_z_m, "spacing_z_m")
    phase_array = np.asarray(phase, dtype=np.float64)
    if phase_array.ndim != 3 or not np.isfinite(phase_array).all():
        raise ValueError("phase must be a finite 3D array")
    axis = 1 if camera.view_axis == "y" else 0
    horizontal_spacing = spacing_x if camera.view_axis == "y" else spacing_y
    # A ray is visibly gas wherever at least one voxel on that ray is gas.
    # The transpose presents rows as increasing laboratory z before the
    # optional blur, pixel resampling, and segmentation.
    gas_score = (1.0 - np.min(np.clip(phase_array, 0.0, 1.0), axis=axis)).T
    factors = (
        spacing_z / camera.pixel_size_m,
        horizontal_spacing / camera.pixel_size_m,
    )
    if any(abs(value - 1.0) > 1.0e-12 for value in factors):
        gas_score = zoom(
            gas_score,
            factors,
            order=1,
            mode="nearest",
            prefilter=False,
        )
    if camera.psf_sigma_pixels > 0.0:
        gas_score = gaussian_filter(
            gas_score, camera.psf_sigma_pixels, mode="nearest"
        )
    gas_score = np.clip(gas_score, 0.0, 1.0)
    segmented = gas_score >= camera.segmentation_threshold
    return {
        "gas_score": gas_score,
        "segmented_gas": segmented,
        "metadata": {
            "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
            "measurement_operator_id": camera.operator_id,
            **camera.parameters,
            "projection_rule": (
                "orthographic ray minimum of liquid fraction; gas score=1-min(alpha)"
            ),
            "cylindrical_refraction": (
                "NOT_APPLIED_REQUIRES_CALIBRATED_WALL_MODEL"
            ),
        },
    }


def project_surface_to_camera(
    radius_m: np.ndarray,
    surface_height_m: np.ndarray,
    *,
    container_radius_m: float,
    container_height_m: float,
    bar_top_m: float,
    camera: CameraModel,
    surface_operator: SurfaceObservationOperator,
) -> dict[str, Any]:
    """Project one canonical radial surface through pixel and segmentation space."""

    radius_limit = _finite_positive(container_radius_m, "container_radius_m")
    height_limit = _finite_positive(container_height_m, "container_height_m")
    bar_top = _finite(bar_top_m, "bar_top_m")
    if not 0.0 <= bar_top <= height_limit:
        raise ValueError("bar_top_m must lie inside the camera vertical extent")
    radius = np.asarray(radius_m, dtype=np.float64)
    profile = np.asarray(surface_height_m, dtype=np.float64)
    if (
        radius.ndim != 1
        or profile.shape != radius.shape
        or radius.size < 2
        or not np.isfinite(radius).all()
        or not np.isfinite(profile).all()
        or np.any(np.diff(radius) <= 0.0)
    ):
        raise ValueError("camera surface input must be finite ordered radial data")

    nx = max(2, int(math.ceil(2.0 * radius_limit / camera.pixel_size_m)))
    nz = max(2, int(math.ceil(height_limit / camera.pixel_size_m)))
    x_m = -radius_limit + (np.arange(nx) + 0.5) * (2.0 * radius_limit / nx)
    z_m = (np.arange(nz) + 0.5) * (height_limit / nz)
    projected_surface = np.interp(
        np.abs(x_m), radius, profile, left=profile[0], right=profile[-1]
    )
    gas_score = (z_m[:, None] >= projected_surface[None, :]).astype(np.float64)
    if camera.psf_sigma_pixels > 0.0:
        gas_score = gaussian_filter(
            gas_score, camera.psf_sigma_pixels, mode="nearest"
        )
    segmented = gas_score >= camera.segmentation_threshold
    any_gas = np.any(segmented, axis=0)
    first = np.argmax(segmented, axis=0)
    boundary_height = np.where(any_gas, z_m[first], height_limit)

    central = np.abs(x_m) <= surface_operator.central_observation_radius_m
    outer = (
        (np.abs(x_m) >= surface_operator.outer_annulus_inner_radius_m)
        & (np.abs(x_m) <= surface_operator.outer_annulus_outer_radius_m)
    )
    if not np.any(central) or not np.any(outer):
        raise ValueError("camera pixels do not resolve the configured observation ROIs")
    central_minimum = float(np.min(boundary_height[central]))
    outer_reference = float(np.mean(boundary_height[outer]))
    visible_depth = outer_reference - central_minimum

    bar_row = int(np.argmin(np.abs(z_m - bar_top)))
    row = segmented[bar_row]
    centre_index = int(np.argmin(np.abs(x_m)))
    if not row[centre_index]:
        channel_width = 0.0
    else:
        left = centre_index
        right = centre_index
        while left > 0 and row[left - 1]:
            left -= 1
        while right + 1 < row.size and row[right + 1]:
            right += 1
        channel_width = float((right - left + 1) * (2.0 * radius_limit / nx))

    return {
        "x_m": x_m,
        "z_m": z_m,
        "gas_score": np.clip(gas_score, 0.0, 1.0),
        "segmented_gas": segmented,
        "boundary_height_m": boundary_height,
        "camera_visible_depth_m": visible_depth,
        "camera_channel_width_m": channel_width,
        "camera_central_boundary_min_height_m": central_minimum,
        "camera_outer_boundary_reference_height_m": outer_reference,
        "bar_measurement_row_z_m": float(z_m[bar_row]),
    }


def camera_observable_operator_parameters(
    camera: CameraModel,
    surface_operator: SurfaceObservationOperator,
    *,
    container_radius_m: float,
    container_height_m: float,
    bar_top_m: float,
) -> dict[str, Any]:
    parameters = {
        **camera.parameters,
        "camera_image_operator_id": camera.operator_id,
        "central_observation_radius_m": surface_operator.central_observation_radius_m,
        "outer_annulus_inner_radius_m": surface_operator.outer_annulus_inner_radius_m,
        "outer_annulus_outer_radius_m": surface_operator.outer_annulus_outer_radius_m,
        "camera_central_aggregation": "minimum_segmented_boundary_height",
        "camera_outer_aggregation": "pixel_width_mean_segmented_boundary_height",
        "channel_width_rule": "central_contiguous_segmented_gas_at_bar_top_plane",
        "container_radius_m": float(container_radius_m),
        "container_height_m": float(container_height_m),
        "bar_top_m": float(bar_top_m),
        "calibration_status": "UNVALIDATED_SYNTHETIC_CAMERA_OPERATOR",
    }
    parameters["operator_id"] = f"camera-observable-v2:{_digest(parameters)[:20]}"
    return parameters


def integrate_camera_measurement(
    result: SimulationResult,
    config: Any,
    *,
    camera: CameraModel | None = None,
) -> SimulationResult:
    """Measure every output frame and persist recomputable camera primitives."""

    selected_camera = CameraModel() if camera is None else camera
    radius, profiles = canonical_surface_representation(result)
    surface_operator = surface_operator_from_config(config)
    container_radius = float(config.container.radius_m)
    container_height = float(
        config.container.liquid_depth_m + config.container.headspace_height_m
    )
    bar_top = float(
        config.stir_bar.bottom_clearance_m + 2.0 * config.stir_bar.radius_m
    )
    frames = [
        project_surface_to_camera(
            radius,
            profile,
            container_radius_m=container_radius,
            container_height_m=container_height,
            bar_top_m=bar_top,
            camera=selected_camera,
            surface_operator=surface_operator,
        )
        for profile in profiles
    ]
    if not frames:
        raise ValueError("camera integration requires at least one output frame")

    observables = dict(result.observables)
    observables["camera_visible_depth_m"] = np.asarray(
        [frame["camera_visible_depth_m"] for frame in frames],
        dtype=np.float64,
    )
    observables["camera_channel_width_m"] = np.asarray(
        [frame["camera_channel_width_m"] for frame in frames],
        dtype=np.float64,
    )
    fields = dict(result.fields)
    fields["camera_x_m"] = np.asarray(frames[0]["x_m"], dtype=np.float64)
    fields["camera_z_m"] = np.asarray(frames[0]["z_m"], dtype=np.float64)
    fields["camera_boundary_height_m"] = np.stack(
        [np.asarray(frame["boundary_height_m"], dtype=np.float64) for frame in frames]
    )
    fields["camera_segmented_gas_final"] = np.asarray(
        frames[-1]["segmented_gas"], dtype=np.uint8
    )

    parameters = camera_observable_operator_parameters(
        selected_camera,
        surface_operator,
        container_radius_m=container_radius,
        container_height_m=container_height,
        bar_top_m=bar_top,
    )
    metadata = dict(result.metadata)
    units_raw = metadata.get("observable_units", {})
    units = dict(units_raw) if isinstance(units_raw, Mapping) else {}
    units.update(
        {
            "camera_visible_depth_m": "m",
            "camera_channel_width_m": "m",
        }
    )
    metadata["observable_units"] = units
    field_units_raw = metadata.get("field_units", {})
    field_units = (
        dict(field_units_raw) if isinstance(field_units_raw, Mapping) else {}
    )
    field_units.update(
        {
            "camera_x_m": "m",
            "camera_z_m": "m",
            "camera_boundary_height_m": "m",
            "camera_segmented_gas_final": "1",
        }
    )
    metadata["field_units"] = field_units
    metadata["camera_measurement_operator"] = parameters
    metadata["camera_measurement_chain"] = {
        "contract_version": MEASUREMENT_CONTRACT_VERSION,
        "steps": [
            "canonical model interface",
            "ideal orthographic side projection",
            "point-spread function",
            "pixelisation",
            "threshold segmentation",
            "camera observables",
        ],
        "every_output_frame_measured": True,
        "output_frame_count": len(frames),
        "interface_source": metadata.get("canonical_surface_representation", {}),
        "cylindrical_refraction": (
            "NOT_APPLIED_REQUIRES_CALIBRATED_WALL_MODEL"
        ),
        "experimental_comparison_requirement": (
            "experiment metadata must carry the identical camera observable identity"
        ),
    }
    surface_parameters = metadata.get(
        "surface_observation_operator", surface_operator.parameters
    )
    if isinstance(surface_parameters, Mapping):
        surface_parameters = {
            key: value
            for key, value in surface_parameters.items()
            if key != "operator_id"
        }
    else:
        surface_parameters = surface_operator.parameters
    topology_parameters = metadata.get("topology_operator_parameters", {})
    metadata["observable_contract"] = contract_document(
        observables,
        surface_operator_parameters=surface_parameters,
        camera_operator_parameters=parameters,
        topology_operator_parameters=(
            topology_parameters if isinstance(topology_parameters, Mapping) else {}
        ),
    )
    return SimulationResult(
        result.backend,
        result.time_s,
        observables,
        fields,
        result.events,
        metadata,
        result.status,
    )


__all__ = [
    "CameraModel",
    "camera_observable_operator_parameters",
    "integrate_camera_measurement",
    "project_phase_to_camera",
    "project_surface_to_camera",
]
