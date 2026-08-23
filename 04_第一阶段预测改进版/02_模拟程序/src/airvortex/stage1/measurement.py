"""Explicit model-to-camera measurement operators."""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from scipy.ndimage import gaussian_filter, zoom
from .contracts import MEASUREMENT_CONTRACT_VERSION

@dataclass(frozen=True, slots=True)
class CameraModel:
    view_axis: str = "y"
    pixel_size_m: float = 1e-4
    psf_sigma_pixels: float = 0.0
    segmentation_threshold: float = 0.5
    def __post_init__(self):
        if self.view_axis not in {"x", "y"}: raise ValueError("view_axis must be x or y")
        if not math.isfinite(self.pixel_size_m) or self.pixel_size_m <= 0: raise ValueError("pixel_size_m must be positive")
        if self.psf_sigma_pixels < 0: raise ValueError("psf_sigma_pixels must be non-negative")
        if not 0 < self.segmentation_threshold < 1: raise ValueError("segmentation_threshold must be in (0,1)")
    @property
    def operator_id(self) -> str:
        return f"ideal_orthographic_{self.view_axis}_psf{self.psf_sigma_pixels:g}_threshold{self.segmentation_threshold:g}"

def project_phase_to_camera(phase, *, spacing_x_m, spacing_y_m, spacing_z_m, camera: CameraModel):
    q = np.asarray(phase, dtype=float)
    if q.ndim != 3 or not np.isfinite(q).all(): raise ValueError("phase must be a finite 3D array")
    axis = 1 if camera.view_axis == "y" else 0
    horizontal_spacing = spacing_x_m if camera.view_axis == "y" else spacing_y_m
    image = np.flipud((1.0 - np.min(np.clip(q,0,1), axis=axis)).T)
    factors = (spacing_z_m/camera.pixel_size_m, horizontal_spacing/camera.pixel_size_m)
    if any(abs(v-1)>1e-12 for v in factors): image = zoom(image, factors, order=1, mode="nearest", prefilter=False)
    if camera.psf_sigma_pixels > 0: image = gaussian_filter(image, camera.psf_sigma_pixels, mode="nearest")
    segmented = image >= camera.segmentation_threshold
    return {
        "gas_score": np.clip(image,0,1),
        "segmented_gas": segmented,
        "metadata": {
            "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
            "measurement_operator_id": camera.operator_id,
            "view_axis": camera.view_axis,
            "pixel_size_m": camera.pixel_size_m,
            "psf_sigma_pixels": camera.psf_sigma_pixels,
            "segmentation_threshold": camera.segmentation_threshold,
            "cylindrical_refraction": "NOT_APPLIED_REQUIRES_CALIBRATED_WALL_MODEL",
        },
    }
