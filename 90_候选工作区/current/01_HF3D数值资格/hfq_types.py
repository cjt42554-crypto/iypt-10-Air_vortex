"""Shared types and immutable records for HF3D numerical qualification."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import numpy as np
from numpy.typing import NDArray

JsonObject = dict[str, Any]
FloatArray = NDArray[np.float64]

ALLOWED_DISCRETIZATION_PATHS: tuple[str, ...] = (
    "high_fidelity.radial_cells",
    "high_fidelity.azimuthal_cells",
    "high_fidelity.axial_cells",
    "high_fidelity.time_step_s",
)
DEFAULT_SMOOTH_OBSERVABLES: tuple[str, ...] = (
    "funnel_depth_m",
    "min_gap_to_bar_m",
    "kinetic_energy_lab_j",
    "angular_momentum_z_kg_m2_s",
    "axisymmetry_error",
)
DEFAULT_BINARY_OBSERVABLES: tuple[str, ...] = (
    "first_contact_proxy",
    "connection_candidate",
    "finite_channel_candidate",
)

class QualificationError(ValueError):
    """Raised for an invalid campaign or incomparable numerical evidence."""

@dataclass(frozen=True, slots=True)
class GridAudit:
    nx: int
    ny: int
    nz: int
    dx_m: float
    dy_m: float
    dz_m: float
    h_min_m: float
    h_max_m: float
    h_geometric_m: float
    aspect_ratio: float
    bar_diameter_m: float
    bar_diameter_cells_best_axis: float
    bar_diameter_cells_worst_axis: float
    exact_bar_volume_m3: float
    nominal_bar_voxel_count: float
    interface_width_m: float
    interface_width_cells_worst_axis: float
    boundary_layer_width_m: float
    boundary_layer_width_cells_worst_axis: float
    box_cell_count: int

    def to_dict(self) -> JsonObject:
        return {
            "nx": self.nx,
            "ny": self.ny,
            "nz": self.nz,
            "dx_m": self.dx_m,
            "dy_m": self.dy_m,
            "dz_m": self.dz_m,
            "h_min_m": self.h_min_m,
            "h_max_m": self.h_max_m,
            "h_geometric_m": self.h_geometric_m,
            "aspect_ratio": self.aspect_ratio,
            "bar_diameter_m": self.bar_diameter_m,
            "bar_diameter_cells_best_axis": self.bar_diameter_cells_best_axis,
            "bar_diameter_cells_worst_axis": self.bar_diameter_cells_worst_axis,
            "exact_bar_volume_m3": self.exact_bar_volume_m3,
            "nominal_bar_voxel_count": self.nominal_bar_voxel_count,
            "interface_width_m": self.interface_width_m,
            "interface_width_cells_worst_axis": (
                self.interface_width_cells_worst_axis
            ),
            "boundary_layer_width_m": self.boundary_layer_width_m,
            "boundary_layer_width_cells_worst_axis": (
                self.boundary_layer_width_cells_worst_axis
            ),
            "box_cell_count": self.box_cell_count,
        }

@dataclass(frozen=True, slots=True)
class SeriesData:
    time_s: FloatArray
    values: FloatArray

    def __post_init__(self) -> None:
        time_s = np.asarray(self.time_s, dtype=np.float64)
        values = np.asarray(self.values, dtype=np.float64)
        if time_s.ndim != 1 or time_s.size < 2:
            raise QualificationError("time_s must be a one-dimensional array with >= 2 samples")
        if values.shape[0] != time_s.size:
            raise QualificationError("values first axis must match time_s")
        if not np.all(np.isfinite(time_s)) or not np.all(np.diff(time_s) > 0.0):
            raise QualificationError("time_s must be finite and strictly increasing")
        if not np.all(np.isfinite(values)):
            raise QualificationError("series values must be finite")
        object.__setattr__(self, "time_s", time_s)
        object.__setattr__(self, "values", values)

@dataclass(frozen=True, slots=True)
class PairwiseError:
    relative_l2: float
    relative_linf: float
    absolute_l2: float
    absolute_linf: float
    reference_l2: float
    reference_linf: float
    common_start_s: float
    common_end_s: float
    sample_count: int

    def to_dict(self) -> JsonObject:
        return {
            "relative_l2": self.relative_l2,
            "relative_linf": self.relative_linf,
            "absolute_l2": self.absolute_l2,
            "absolute_linf": self.absolute_linf,
            "reference_l2": self.reference_l2,
            "reference_linf": self.reference_linf,
            "common_start_s": self.common_start_s,
            "common_end_s": self.common_end_s,
            "sample_count": self.sample_count,
        }

@dataclass(frozen=True, slots=True)
class ConvergenceEstimate:
    pair_fine_medium: PairwiseError
    pair_medium_coarse: PairwiseError
    observed_order: float | None
    refinement_ratio_fine_medium: float
    refinement_ratio_medium_coarse: float
    fine_grid_gci_l2: float | None
    asymptotic_trend: bool
    exact_agreement: bool
    reason: str

    def to_dict(self) -> JsonObject:
        return {
            "pair_fine_medium": self.pair_fine_medium.to_dict(),
            "pair_medium_coarse": self.pair_medium_coarse.to_dict(),
            "observed_order": self.observed_order,
            "refinement_ratio_fine_medium": self.refinement_ratio_fine_medium,
            "refinement_ratio_medium_coarse": self.refinement_ratio_medium_coarse,
            "fine_grid_gci_l2": self.fine_grid_gci_l2,
            "asymptotic_trend": self.asymptotic_trend,
            "exact_agreement": self.exact_agreement,
            "reason": self.reason,
        }

@dataclass(frozen=True, slots=True)
class EventSummary:
    occurred: bool
    first_on_s: float | None
    duty_fraction: float
    transition_count: int

    def to_dict(self) -> JsonObject:
        return {
            "occurred": self.occurred,
            "first_on_s": self.first_on_s,
            "duty_fraction": self.duty_fraction,
            "transition_count": self.transition_count,
        }
