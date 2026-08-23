"""Advection helpers for the axisymmetric HF-preview backend."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import map_coordinates

from .axisymmetric import AxisymmetricGrid, FloatArray


@dataclass(frozen=True, slots=True)
class ScalarTransportDiagnostics:
    """Audit trail for boundedness and global-volume intervention."""

    target_integral: float
    raw_integral: float
    clipped_integral: float
    final_integral: float
    clip_signed_integral: float
    clip_absolute_integral: float
    mass_repair_signed_integral: float
    mass_repair_absolute_integral: float
    raw_minimum: float
    raw_maximum: float


def _bounded_with_diagnostics(
    raw: FloatArray,
    grid: AxisymmetricGrid,
    target: float,
    *,
    lower: float = 0.0,
    upper: float = 1.0,
) -> tuple[FloatArray, ScalarTransportDiagnostics]:
    clipped = np.clip(raw, lower, upper)
    repaired = bounded_mass_correction(
        clipped, grid, target, lower=lower, upper=upper
    )
    raw_integral = grid.integrate(raw)
    clipped_integral = grid.integrate(clipped)
    final_integral = grid.integrate(repaired)
    diagnostics = ScalarTransportDiagnostics(
        target_integral=float(target),
        raw_integral=raw_integral,
        clipped_integral=clipped_integral,
        final_integral=final_integral,
        clip_signed_integral=clipped_integral - raw_integral,
        clip_absolute_integral=grid.integrate(np.abs(clipped - raw)),
        mass_repair_signed_integral=final_integral - clipped_integral,
        mass_repair_absolute_integral=grid.integrate(np.abs(repaired - clipped)),
        raw_minimum=float(np.min(raw)),
        raw_maximum=float(np.max(raw)),
    )
    return repaired, diagnostics


def bounded_mass_correction(
    scalar: NDArray[np.floating],
    grid: AxisymmetricGrid,
    target_integral: float,
    *,
    lower: float = 0.0,
    upper: float = 1.0,
) -> FloatArray:
    """Clip a scalar and recover its requested volume-weighted integral.

    Corrections are spread uniformly over cells which still have capacity.  It
    is not a physical transport step; it is a conservative boundedness repair
    for roundoff or a slightly over-CFL donor-cell update.
    """

    q = np.clip(np.asarray(scalar, dtype=np.float64), lower, upper).copy()
    weights = grid.cell_volumes
    min_mass = lower * float(np.sum(weights))
    max_mass = upper * float(np.sum(weights))
    target = float(np.clip(target_integral, min_mass, max_mass))
    scale = max(abs(target), float(np.sum(weights)), 1.0)
    tolerance = 32.0 * np.finfo(float).eps * scale
    for _ in range(12):
        residual = target - float(np.sum(q * weights))
        if abs(residual) <= tolerance:
            break
        if residual > 0.0:
            eligible = q < upper - 8.0 * np.finfo(float).eps
            if not np.any(eligible):
                break
            delta = residual / float(np.sum(weights[eligible]))
        else:
            eligible = q > lower + 8.0 * np.finfo(float).eps
            if not np.any(eligible):
                break
            delta = residual / float(np.sum(weights[eligible]))
        q[eligible] = np.clip(q[eligible] + delta, lower, upper)
    return q


def conservative_advect_scalar(
    scalar: NDArray[np.floating],
    u_r: NDArray[np.floating],
    u_z: NDArray[np.floating],
    grid: AxisymmetricGrid,
    dt: float,
    *,
    enforce_bounds: bool = True,
    return_diagnostics: bool = False,
) -> FloatArray | tuple[FloatArray, ScalarTransportDiagnostics]:
    """Advance a cell scalar with first-order, cylindrical donor-cell fluxes."""

    grid.require_cell_field(scalar)
    grid.require_face_velocity(u_r, u_z)
    if not np.isfinite(dt) or dt < 0.0:
        raise ValueError("dt must be finite and non-negative")
    q = np.asarray(scalar, dtype=np.float64)
    if dt == 0.0:
        copied = q.copy()
        diagnostics = ScalarTransportDiagnostics(
            target_integral=grid.integrate(q),
            raw_integral=grid.integrate(q),
            clipped_integral=grid.integrate(q),
            final_integral=grid.integrate(q),
            clip_signed_integral=0.0,
            clip_absolute_integral=0.0,
            mass_repair_signed_integral=0.0,
            mass_repair_absolute_integral=0.0,
            raw_minimum=float(np.min(q)),
            raw_maximum=float(np.max(q)),
        )
        return (copied, diagnostics) if return_diagnostics else copied
    ur = np.asarray(u_r, dtype=np.float64)
    uz = np.asarray(u_z, dtype=np.float64)
    target = grid.integrate(q)

    radial_flux = np.zeros((grid.nr + 1, grid.nz), dtype=np.float64)
    face_velocity_r = ur[1:-1, :]
    radial_flux[1:-1, :] = face_velocity_r * np.where(
        face_velocity_r >= 0.0, q[:-1, :], q[1:, :]
    )
    axial_flux = np.zeros((grid.nr, grid.nz + 1), dtype=np.float64)
    face_velocity_z = uz[:, 1:-1]
    axial_flux[:, 1:-1] = face_velocity_z * np.where(
        face_velocity_z >= 0.0, q[:, :-1], q[:, 1:]
    )

    flux_divergence = (
        grid.r_faces[1:, None] * radial_flux[1:, :]
        - grid.r_faces[:-1, None] * radial_flux[:-1, :]
    ) / (grid.r_centres[:, None] * grid.dr)
    flux_divergence += (axial_flux[:, 1:] - axial_flux[:, :-1]) / grid.dz
    updated = q - dt * flux_divergence
    if enforce_bounds:
        updated, diagnostics = _bounded_with_diagnostics(updated, grid, target)
    else:
        raw_integral = grid.integrate(updated)
        diagnostics = ScalarTransportDiagnostics(
            target_integral=target,
            raw_integral=raw_integral,
            clipped_integral=raw_integral,
            final_integral=raw_integral,
            clip_signed_integral=0.0,
            clip_absolute_integral=0.0,
            mass_repair_signed_integral=0.0,
            mass_repair_absolute_integral=0.0,
            raw_minimum=float(np.min(updated)),
            raw_maximum=float(np.max(updated)),
        )
    return (updated, diagnostics) if return_diagnostics else updated


def conservative_cls_compress(
    phase: NDArray[np.floating],
    grid: AxisymmetricGrid,
    thickness_m: float,
    *,
    pseudo_cfl: float = 0.04,
    iterations: int = 1,
) -> tuple[FloatArray, ScalarTransportDiagnostics]:
    """Conservative CLS compression/diffusion in pseudo-time.

    The face flux is

    ``c(1-c) n - epsilon grad(c)``,

    so a logistic interface of thickness ``epsilon`` is stationary while
    donor-cell broadening is compressed.  The pseudo-step is limited by
    ``pseudo_cfl * min(dr, dz)``.  Closed-boundary fluxes conserve the raw
    axisymmetric integral; any subsequent clipping and mass repair are returned
    explicitly in :class:`ScalarTransportDiagnostics` rather than hidden.
    """

    grid.require_cell_field(phase, "phase")
    if not np.isfinite(thickness_m) or thickness_m <= 0.0:
        raise ValueError("thickness_m must be finite and positive")
    if not np.isfinite(pseudo_cfl) or not 0.0 < pseudo_cfl <= 0.25:
        raise ValueError("pseudo_cfl must lie in (0, 0.25]")
    if isinstance(iterations, bool) or iterations < 1:
        raise ValueError("iterations must be an integer >= 1")
    c = np.asarray(phase, dtype=np.float64).copy()
    target = grid.integrate(c)
    aggregate_clip_signed = 0.0
    aggregate_clip_absolute = 0.0
    aggregate_repair_signed = 0.0
    aggregate_repair_absolute = 0.0
    raw_minimum = float(np.min(c))
    raw_maximum = float(np.max(c))
    last_raw_integral = target
    last_clipped_integral = target
    for _ in range(iterations):
        dc_dr, dc_dz = grid.cell_gradient(c)
        magnitude = np.sqrt(dc_dr**2 + dc_dz**2)
        normal_r_cell = np.divide(
            dc_dr,
            magnitude,
            out=np.zeros_like(dc_dr),
            where=magnitude > 1.0e-12 / min(grid.dr, grid.dz),
        )
        normal_z_cell = np.divide(
            dc_dz,
            magnitude,
            out=np.zeros_like(dc_dz),
            where=magnitude > 1.0e-12 / min(grid.dr, grid.dz),
        )

        flux_r = np.zeros((grid.nr + 1, grid.nz), dtype=np.float64)
        flux_z = np.zeros((grid.nr, grid.nz + 1), dtype=np.float64)
        c_r = 0.5 * (c[:-1, :] + c[1:, :])
        n_r = 0.5 * (normal_r_cell[:-1, :] + normal_r_cell[1:, :])
        grad_r_face = (c[1:, :] - c[:-1, :]) / grid.dr
        flux_r[1:-1, :] = c_r * (1.0 - c_r) * n_r - thickness_m * grad_r_face
        c_z = 0.5 * (c[:, :-1] + c[:, 1:])
        n_z = 0.5 * (normal_z_cell[:, :-1] + normal_z_cell[:, 1:])
        grad_z_face = (c[:, 1:] - c[:, :-1]) / grid.dz
        flux_z[:, 1:-1] = c_z * (1.0 - c_z) * n_z - thickness_m * grad_z_face

        divergence = (
            grid.r_faces[1:, None] * flux_r[1:, :]
            - grid.r_faces[:-1, None] * flux_r[:-1, :]
        ) / (grid.r_centres[:, None] * grid.dr)
        divergence += (flux_z[:, 1:] - flux_z[:, :-1]) / grid.dz
        pseudo_step_m = pseudo_cfl * min(grid.dr, grid.dz)
        raw = c - pseudo_step_m * divergence
        c, diagnostics = _bounded_with_diagnostics(raw, grid, target)
        last_raw_integral = diagnostics.raw_integral
        last_clipped_integral = diagnostics.clipped_integral
        aggregate_clip_signed += diagnostics.clip_signed_integral
        aggregate_clip_absolute += diagnostics.clip_absolute_integral
        aggregate_repair_signed += diagnostics.mass_repair_signed_integral
        aggregate_repair_absolute += diagnostics.mass_repair_absolute_integral
        raw_minimum = min(raw_minimum, diagnostics.raw_minimum)
        raw_maximum = max(raw_maximum, diagnostics.raw_maximum)

    aggregate = ScalarTransportDiagnostics(
        target_integral=target,
        raw_integral=last_raw_integral,
        clipped_integral=last_clipped_integral,
        final_integral=grid.integrate(c),
        clip_signed_integral=aggregate_clip_signed,
        clip_absolute_integral=aggregate_clip_absolute,
        mass_repair_signed_integral=aggregate_repair_signed,
        mass_repair_absolute_integral=aggregate_repair_absolute,
        raw_minimum=raw_minimum,
        raw_maximum=raw_maximum,
    )
    return c, aggregate


def semi_lagrangian_advect(
    field: NDArray[np.floating],
    u_r_cell: NDArray[np.floating],
    u_z_cell: NDArray[np.floating],
    grid: AxisymmetricGrid,
    dt: float,
    *,
    radial_parity: str = "even",
) -> FloatArray:
    """Backtrace and bilinearly sample a cell-centred field.

    ``radial_parity='odd'`` is used for a radial vector component when a
    characteristic crosses the symmetry axis.  The backend uses this stable,
    mildly diffusive method for momentum while phase is transported by the
    conservative finite-volume routine above.
    """

    for value, name in (
        (field, "field"), (u_r_cell, "u_r_cell"), (u_z_cell, "u_z_cell")
    ):
        grid.require_cell_field(value, name)
    if radial_parity not in {"even", "odd"}:
        raise ValueError("radial_parity must be 'even' or 'odd'")
    if not np.isfinite(dt) or dt < 0.0:
        raise ValueError("dt must be finite and non-negative")
    if dt == 0.0:
        return np.asarray(field, dtype=np.float64).copy()

    rr, zz = np.meshgrid(grid.r_centres, grid.z_centres, indexing="ij")
    departure_r = rr - dt * np.asarray(u_r_cell, dtype=np.float64)
    departure_z = zz - dt * np.asarray(u_z_cell, dtype=np.float64)
    reflected = departure_r < 0.0
    departure_r = np.abs(departure_r)
    departure_r = np.clip(departure_r, 0.5 * grid.dr, grid.radius_m - 0.5 * grid.dr)
    departure_z = np.clip(departure_z, 0.5 * grid.dz, grid.height_m - 0.5 * grid.dz)
    coordinates = np.array(
        [departure_r / grid.dr - 0.5, departure_z / grid.dz - 0.5]
    )
    sampled = map_coordinates(
        np.asarray(field, dtype=np.float64),
        coordinates,
        order=1,
        mode="nearest",
        prefilter=False,
    )
    if radial_parity == "odd":
        sampled = np.where(reflected, -sampled, sampled)
    return sampled.astype(np.float64, copy=False)
