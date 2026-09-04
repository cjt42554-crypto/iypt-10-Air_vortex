"""Bounded conservative CLS face exchanges and paired momentum transport.

Transfers are time-integrated phase fluxes (metres). Closed faces and domain
boundaries have zero transfer. This limiter is only for CLS corrections.
"""
from __future__ import annotations

import numpy as np

from .cartesian3d import FloatArray, ImmersedCartesianGrid

FaceTransfers = tuple[FloatArray, FloatArray, FloatArray]


def _cell_slices(axis: int) -> tuple[tuple[slice, ...], tuple[slice, ...], tuple[slice, ...]]:
    left = [slice(None)] * 3
    right = left.copy()
    face = left.copy()
    left[axis] = slice(None, -1)
    right[axis] = slice(1, None)
    face[axis] = slice(1, -1)
    return tuple(left), tuple(right), tuple(face)


def transfer_divergence(transfers: FaceTransfers, grid: ImmersedCartesianGrid) -> FloatArray:
    x, y, z = transfers
    return ((x[1:] - x[:-1]) / grid.dx
            + (y[:, 1:] - y[:, :-1]) / grid.dy
            + (z[:, :, 1:] - z[:, :, :-1]) / grid.dz)


def limit_phase_transfers(
    phase: FloatArray, transfers: FaceTransfers, grid: ImmersedCartesianGrid,
) -> tuple[FaceTransfers, float]:
    """Limit shared face exchanges by donor mass and receiver capacity.

    Incoming/outgoing budgets include all six faces simultaneously. One shared
    factor per face preserves conservation without a nonlocal clip-and-repair.
    """
    outgoing = np.zeros_like(phase)
    incoming = np.zeros_like(phase)
    spacings = (grid.dx, grid.dy, grid.dz)
    for axis, (flux, spacing) in enumerate(zip(transfers, spacings)):
        left, right, face = _cell_slices(axis)
        amount = flux[face] / spacing
        positive, negative = np.maximum(amount, 0), np.maximum(-amount, 0)
        outgoing[left] += positive
        incoming[right] += positive
        outgoing[right] += negative
        incoming[left] += negative
    loss_factor = np.minimum(1.0, np.divide(
        phase, outgoing, out=np.ones_like(phase), where=outgoing > 0))
    gain_factor = np.minimum(1.0, np.divide(
        1.0 - phase, incoming, out=np.ones_like(phase), where=incoming > 0))
    limited = []
    limited_volume = 0.0
    for axis, (flux, spacing) in enumerate(zip(transfers, spacings)):
        left, right, face = _cell_slices(axis)
        amount = flux[face]
        factor = np.where(amount >= 0,
                          np.minimum(loss_factor[left], gain_factor[right]),
                          np.minimum(loss_factor[right], gain_factor[left]))
        # Roundoff margin, not a subsequent non-conservative clipping step.
        factor = np.where(factor < 1.0, factor * (1.0 - 8 * np.finfo(float).eps), factor)
        result = np.zeros_like(flux)
        result[face] = amount * np.maximum(factor, 0.0)
        limited_volume += float(np.sum(np.abs(amount - result[face]))) * grid.cell_volume_m3 / spacing
        limited.append(result)
    return tuple(limited), limited_volume  # type: ignore[return-value]


def transport_momentum_with_phase_transfers(
    density: FloatArray, velocities: tuple[FloatArray, FloatArray, FloatArray],
    transfers: FaceTransfers, density_jump: float, grid: ImmersedCartesianGrid,
) -> tuple[FloatArray, tuple[FloatArray, FloatArray, FloatArray]]:
    """Apply the same CLS exchanges to rho and all three components of rho*u.

    Donor velocity follows the sign of the mass transfer, including a negative
    density jump. With bounded donor exchanges this conserves momentum, preserves
    uniform velocity and mixes (rather than creates) discrete kinetic energy.
    """
    mass_transfers = tuple(density_jump * value for value in transfers)
    new_density = density - transfer_divergence(mass_transfers, grid)
    if np.any(new_density[grid.active_mask] <= 0) or not np.isfinite(new_density[grid.active_mask]).all():
        raise FloatingPointError("CLS transfer produced non-positive or non-finite density")
    updated = []
    for velocity in velocities:
        momentum_transfers = []
        for axis, mass in enumerate(mass_transfers):
            left, right, face = _cell_slices(axis)
            flux = np.zeros_like(mass)
            flux[face] = mass[face] * np.where(mass[face] >= 0, velocity[left], velocity[right])
            momentum_transfers.append(flux)
        momentum = density * velocity - transfer_divergence(tuple(momentum_transfers), grid)
        updated.append(np.divide(momentum, new_density, out=np.zeros_like(momentum), where=grid.active_mask))
    return new_density, tuple(updated)  # type: ignore[return-value]
