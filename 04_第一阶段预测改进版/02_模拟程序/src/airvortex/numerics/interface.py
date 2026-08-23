"""Diffuse colour-function interface utilities for the HF-preview solver."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import binary_propagation, distance_transform_edt

from .axisymmetric import AxisymmetricGrid, FloatArray
from .transport import bounded_mass_correction


@dataclass(frozen=True, slots=True)
class ConnectionDiagnostics:
    """Grid-resolution gate for a top-connected central gas path."""

    one_cell_precursor: bool
    resolved_candidate: bool
    minimum_throat_cells: int
    required_throat_cells: int
    minimum_throat_radius_m: float
    minimum_throat_diameter_m: float
    required_throat_radius_m: float
    interface_thickness_m: float
    radial_resolution_m: float
    axial_resolution_m: float
    temporal_classification: str


def initialise_flat_phase(
    grid: AxisymmetricGrid, liquid_height_m: float, thickness_m: float
) -> FloatArray:
    """Return liquid colour ``c=1`` below a diffuse, initially flat surface."""

    if not 0.0 < liquid_height_m < grid.height_m:
        raise ValueError("liquid height must lie inside the computational domain")
    if not np.isfinite(thickness_m) or thickness_m <= 0.0:
        raise ValueError("interface thickness must be positive")
    signed_distance = liquid_height_m - grid.z_centres[None, :]
    column = 0.5 * (1.0 + np.tanh(signed_distance / (2.0 * thickness_m)))
    return np.broadcast_to(column, grid.shape).copy()


# American-spelling alias used by a few callers/tests.
initialize_flat_phase = initialise_flat_phase


def solid_body_surface_profile(
    grid: AxisymmetricGrid,
    mean_liquid_height_m: float,
    angular_velocity_rad_s: float,
    gravity_m_s2: float,
) -> FloatArray:
    """Small-slope rotating-bucket surface with discrete volume constraint.

    With surface tension neglected, radial equilibrium gives
    ``eta = constant + Omega**2 r**2/(2g)``.  The constant is chosen using the
    same annular cell weights as the numerical grid, making this a useful
    manufactured/grid-trend reference rather than a second dynamics model.
    """

    if not 0.0 < mean_liquid_height_m < grid.height_m:
        raise ValueError("mean_liquid_height_m must lie inside the domain")
    if not np.isfinite(angular_velocity_rad_s):
        raise ValueError("angular_velocity_rad_s must be finite")
    if not np.isfinite(gravity_m_s2) or gravity_m_s2 <= 0.0:
        raise ValueError("gravity_m_s2 must be positive")
    radial_weights = grid.r_centres.copy()
    mean_r_squared = float(
        np.sum(radial_weights * grid.r_centres**2) / np.sum(radial_weights)
    )
    profile = mean_liquid_height_m + (
        angular_velocity_rad_s**2
        / (2.0 * gravity_m_s2)
        * (grid.r_centres**2 - mean_r_squared)
    )
    if np.min(profile) <= 0.0 or np.max(profile) >= grid.height_m:
        raise ValueError("rotating-bucket reference surface leaves the domain")
    return profile


def initialise_rotating_bucket_phase(
    grid: AxisymmetricGrid,
    mean_liquid_height_m: float,
    angular_velocity_rad_s: float,
    gravity_m_s2: float,
    thickness_m: float,
) -> FloatArray:
    """Diffuse colour field associated with :func:`solid_body_surface_profile`."""

    if not np.isfinite(thickness_m) or thickness_m <= 0.0:
        raise ValueError("thickness_m must be positive")
    profile = solid_body_surface_profile(
        grid,
        mean_liquid_height_m,
        angular_velocity_rad_s,
        gravity_m_s2,
    )
    signed = profile[:, None] - grid.z_centres[None, :]
    return 0.5 * (1.0 + np.tanh(signed / (2.0 * thickness_m)))


def reinitialise_cls(
    phase: NDArray[np.floating],
    grid: AxisymmetricGrid,
    thickness_m: float,
    *,
    target_volume: float | None = None,
) -> FloatArray:
    """Rebuild a smooth conservative-level-set profile around ``c=0.5``.

    The signed distance is reconstructed geometrically and shifted by bisection
    so the axisymmetric liquid volume is retained.  This is intentionally a
    compact preview implementation, not a production contact-line method.
    """

    grid.require_cell_field(phase, "phase")
    if not np.isfinite(thickness_m) or thickness_m <= 0.0:
        raise ValueError("thickness_m must be positive")
    c = np.asarray(phase, dtype=np.float64)
    target = grid.integrate(c) if target_volume is None else float(target_volume)
    inside = c >= 0.5
    if np.all(inside) or not np.any(inside):
        return bounded_mass_correction(c, grid, target)
    distance_in = distance_transform_edt(inside, sampling=(grid.dr, grid.dz))
    distance_out = distance_transform_edt(~inside, sampling=(grid.dr, grid.dz))
    signed = distance_in - distance_out

    def reconstructed(offset: float) -> FloatArray:
        argument = np.clip((signed + offset) / (2.0 * thickness_m), -30.0, 30.0)
        return 0.5 * (1.0 + np.tanh(argument))

    span = 2.0 * max(grid.radius_m, grid.height_m)
    low, high = -span, span
    for _ in range(56):
        middle = 0.5 * (low + high)
        if grid.integrate(reconstructed(middle)) < target:
            low = middle
        else:
            high = middle
    result = reconstructed(0.5 * (low + high))
    return bounded_mass_correction(result, grid, target)


reinitialize_cls = reinitialise_cls


def mixture_property(
    phase: NDArray[np.floating], liquid_value: float, gas_value: float
) -> FloatArray:
    c = np.clip(np.asarray(phase, dtype=np.float64), 0.0, 1.0)
    return gas_value + (liquid_value - gas_value) * c


def projected_color_gradient_csf(
    phase: NDArray[np.floating],
    grid: AxisymmetricGrid,
    surface_tension_n_m: float,
    reference_density_kg_m3: float,
    *,
    regularisation: float = 1.0e-12,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Compute a projected colour-gradient CSF acceleration at cell centres.

    Curvature and force share a cell-centred colour gradient and the resulting
    force is subsequently projected by the backend.  This is a balanced-*form*
    preview surrogate, not a strict balanced-force scheme using an identical
    face gradient for capillarity and pressure.  Static-drop/Laplace validation
    is therefore required before any quantitative capillary claim.
    """

    grid.require_cell_field(phase, "phase")
    dc_dr, dc_dz = grid.cell_gradient(phase)
    magnitude = np.sqrt(dc_dr * dc_dr + dc_dz * dc_dz + regularisation**2)
    normal_r = dc_dr / magnitude
    normal_z = dc_dz / magnitude
    edge_order = 2 if min(grid.shape) >= 3 else 1
    dnr_dr = np.gradient(normal_r, grid.dr, axis=0, edge_order=edge_order)
    dnz_dz = np.gradient(normal_z, grid.dz, axis=1, edge_order=edge_order)
    curvature = -(dnr_dr + normal_r / grid.r_centres[:, None] + dnz_dz)
    coefficient = surface_tension_n_m / reference_density_kg_m3
    force_r = coefficient * curvature * dc_dr
    force_z = coefficient * curvature * dc_dz
    # Curvature away from the diffuse band is numerically meaningless.
    band = (phase > 1.0e-5) & (phase < 1.0 - 1.0e-5)
    force_r = np.where(band, force_r, 0.0)
    force_z = np.where(band, force_z, 0.0)
    curvature = np.where(band, curvature, 0.0)
    return force_r, force_z, curvature


# Backward-compatible descriptive alias.  The primary name above deliberately
# avoids overclaiming a strict balanced-force discretisation.
balanced_csf_force = projected_color_gradient_csf


def surface_profile(
    phase: NDArray[np.floating], grid: AxisymmetricGrid, *, level: float = 0.5
) -> FloatArray:
    """Extract the highest liquid-to-gas crossing in every radial column."""

    grid.require_cell_field(phase, "phase")
    if not 0.0 < level < 1.0:
        raise ValueError("level must lie in (0, 1)")
    c = np.asarray(phase, dtype=np.float64)
    profile = np.empty(grid.nr, dtype=np.float64)
    for i in range(grid.nr):
        column = c[i]
        transitions = np.flatnonzero((column[:-1] >= level) & (column[1:] < level))
        if transitions.size:
            j = int(transitions[-1])
            denominator = column[j + 1] - column[j]
            fraction = 0.5 if abs(denominator) < np.finfo(float).eps else (
                (level - column[j]) / denominator
            )
            profile[i] = grid.z_centres[j] + fraction * grid.dz
        elif np.all(column >= level):
            profile[i] = grid.height_m
        elif np.all(column < level):
            profile[i] = 0.0
        else:
            # A detached liquid pocket: use the topmost cell closest to level.
            j = int(np.argmin(np.abs(column - level)))
            profile[i] = grid.z_centres[j]
    return np.clip(profile, 0.0, grid.height_m)


def gas_connection_candidate(
    phase: NDArray[np.floating],
    grid: AxisymmetricGrid,
    bar_half_length_m: float,
    bar_top_m: float,
    *,
    gas_threshold: float = 0.5,
    minimum_throat_cells: int | None = None,
    interface_thickness_m: float | None = None,
) -> bool:
    """Return a resolution-gated connection candidate.

    A one-cell path is only a precursor.  The contiguous central throat must
    satisfy ``r_throat >= max(2*epsilon, 4*Delta)`` at every axial layer from
    the bar neighbourhood to the lid.  This is still an instantaneous topology
    candidate, not a persistence or wetting claim.
    """

    return gas_connection_diagnostics(
        phase,
        grid,
        bar_half_length_m,
        bar_top_m,
        gas_threshold=gas_threshold,
        minimum_throat_cells=minimum_throat_cells,
        interface_thickness_m=interface_thickness_m,
    ).resolved_candidate


def gas_connection_diagnostics(
    phase: NDArray[np.floating],
    grid: AxisymmetricGrid,
    bar_half_length_m: float,
    bar_top_m: float,
    *,
    gas_threshold: float = 0.5,
    minimum_throat_cells: int | None = None,
    interface_thickness_m: float | None = None,
) -> ConnectionDiagnostics:
    """Measure the narrowest resolved central throat of top-connected gas."""

    grid.require_cell_field(phase, "phase")
    if interface_thickness_m is None:
        thickness = 0.0
    elif not np.isfinite(interface_thickness_m) or interface_thickness_m <= 0.0:
        raise ValueError("interface_thickness_m must be positive when supplied")
    else:
        thickness = float(interface_thickness_m)
    # A binary path is not resolved unless its radius exceeds both twice the
    # diffuse thickness and four cells at the coarser meridional resolution.
    required_radius = max(2.0 * thickness, 4.0 * max(grid.dr, grid.dz))
    resolution_required = int(np.ceil(required_radius / grid.dr - 1.0e-14))
    if minimum_throat_cells is not None:
        if isinstance(minimum_throat_cells, bool) or minimum_throat_cells < 2:
            raise ValueError("minimum_throat_cells must be an integer >= 2")
        resolution_required = max(resolution_required, minimum_throat_cells)
    required = resolution_required
    gas = np.asarray(phase) < gas_threshold
    seed = np.zeros_like(gas, dtype=bool)
    seed[:, -1] = gas[:, -1]
    connected = binary_propagation(seed, mask=gas)
    radial_target = grid.r_centres <= bar_half_length_m + grid.dr
    axial_target = grid.z_centres <= bar_top_m + grid.dz
    target = radial_target[:, None] & axial_target[None, :]
    precursor = bool(np.any(connected & target))
    throat_cells = 0
    if precursor:
        target_indices = np.flatnonzero(axial_target)
        start_j = int(target_indices[-1]) if target_indices.size else 0
        counts: list[int] = []
        for j in range(start_j, grid.nz):
            row = connected[:, j]
            false_indices = np.flatnonzero(~row)
            count = int(false_indices[0]) if false_indices.size else grid.nr
            counts.append(count)
        throat_cells = min(counts) if counts else 0
    throat_radius = float(grid.r_faces[min(throat_cells, grid.nr)])
    resolved = precursor and throat_cells >= required
    return ConnectionDiagnostics(
        one_cell_precursor=precursor,
        resolved_candidate=resolved,
        minimum_throat_cells=throat_cells,
        required_throat_cells=required,
        minimum_throat_radius_m=throat_radius,
        minimum_throat_diameter_m=2.0 * throat_radius,
        required_throat_radius_m=required_radius,
        interface_thickness_m=thickness,
        radial_resolution_m=grid.dr,
        axial_resolution_m=grid.dz,
        temporal_classification="instantaneous_candidate_only",
    )
