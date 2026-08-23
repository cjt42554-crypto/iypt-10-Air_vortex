"""Local momentum operators for the three-dimensional HF solver.

The routines in this module know nothing about the reduced forward theory.
They implement conservative donor-cell momentum transport, the local
Newtonian constitutive law, non-inertial accelerations in the bar-co-rotating
frame, and a voxel-wall no-slip penalisation whose work is explicitly
reported.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray

from .cartesian3d import (
    FloatArray,
    ImmersedCartesianGrid,
    advect_conserved_cell_quantity,
)
from .interface3d import masked_gradient_3d, masked_gradient_axis_3d


@dataclass(frozen=True, slots=True)
class MomentumAdvection3DDiagnostics:
    top_outward_x_momentum_flux_n: float
    top_outward_y_momentum_flux_n: float
    top_outward_z_momentum_flux_n: float
    maximum_speed_before_m_s: float
    maximum_speed_after_m_s: float


@dataclass(frozen=True, slots=True)
class NewtonianStress3DDiagnostics:
    viscous_dissipation_w: float
    maximum_von_mises_stress_pa: float
    maximum_viscous_acceleration_m_s2: float


@dataclass(frozen=True, slots=True)
class BoundaryRelaxation3DDiagnostics:
    rotating_frame_kinetic_energy_change_j: float
    maximum_relaxation_acceleration_m_s2: float
    active_boundary_layer_cells: int
    maximum_layer_weight: float


def _speed_maximum(
    u_x: FloatArray,
    u_y: FloatArray,
    u_z: FloatArray,
    grid: ImmersedCartesianGrid,
) -> float:
    speed = np.sqrt(u_x * u_x + u_y * u_y + u_z * u_z)
    return float(np.max(speed[grid.active_mask]))


def advect_momentum_3d(
    density_kg_m3: NDArray[np.floating],
    u_x_cell: NDArray[np.floating],
    u_y_cell: NDArray[np.floating],
    u_z_cell: NDArray[np.floating],
    advecting_u_x_face: NDArray[np.floating],
    advecting_u_y_face: NDArray[np.floating],
    advecting_u_z_face: NDArray[np.floating],
    new_density_kg_m3: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    dt_s: float,
    *,
    top_inflow_density_kg_m3: float,
    top_inflow_u_x_m_s: NDArray[np.floating] | float,
    top_inflow_u_y_m_s: NDArray[np.floating] | float,
    top_inflow_u_z_m_s: NDArray[np.floating] | float = 0.0,
) -> tuple[FloatArray, FloatArray, FloatArray, MomentumAdvection3DDiagnostics]:
    """Transport ``rho*u`` with the same face volume flux as liquid colour.

    Since the two material densities are constant and density is affine in the
    colour function, using the identical donor-cell volume flux is the
    conservative consistent-transport counterpart of colour advection.  The
    atmospheric top admits prescribed gas momentum on inflow and reports all
    three outward momentum fluxes.
    """

    for value, name in (
        (density_kg_m3, "density_kg_m3"),
        (u_x_cell, "u_x_cell"),
        (u_y_cell, "u_y_cell"),
        (u_z_cell, "u_z_cell"),
        (new_density_kg_m3, "new_density_kg_m3"),
    ):
        grid.require_cell_field(value, name)
    density = np.asarray(density_kg_m3, dtype=np.float64)
    new_density = np.asarray(new_density_kg_m3, dtype=np.float64)
    if (
        not np.isfinite(density[grid.active_mask]).all()
        or not np.isfinite(new_density[grid.active_mask]).all()
        or np.any(density[grid.active_mask] <= 0.0)
        or np.any(new_density[grid.active_mask] <= 0.0)
    ):
        raise ValueError("old and new density must be finite and positive")
    if (
        not math.isfinite(top_inflow_density_kg_m3)
        or top_inflow_density_kg_m3 <= 0.0
    ):
        raise ValueError("top inflow density must be finite and positive")

    old_velocity = tuple(
        np.asarray(value, dtype=np.float64)
        for value in (u_x_cell, u_y_cell, u_z_cell)
    )
    top_values = tuple(
        top_inflow_density_kg_m3 * np.asarray(value, dtype=np.float64)
        for value in (
            top_inflow_u_x_m_s,
            top_inflow_u_y_m_s,
            top_inflow_u_z_m_s,
        )
    )
    transported: list[FloatArray] = []
    top_fluxes: list[float] = []
    for velocity, top_value in zip(old_velocity, top_values):
        momentum, flux = advect_conserved_cell_quantity(
            density * velocity,
            advecting_u_x_face,
            advecting_u_y_face,
            advecting_u_z_face,
            grid,
            dt_s,
            top_inflow_value=top_value,
        )
        transported.append(momentum)
        top_fluxes.append(flux)
    new_velocity = tuple(
        np.divide(
            momentum,
            new_density,
            out=np.zeros_like(momentum),
            where=grid.active_mask,
        )
        for momentum in transported
    )
    diagnostics = MomentumAdvection3DDiagnostics(
        top_outward_x_momentum_flux_n=top_fluxes[0],
        top_outward_y_momentum_flux_n=top_fluxes[1],
        top_outward_z_momentum_flux_n=top_fluxes[2],
        maximum_speed_before_m_s=_speed_maximum(*old_velocity, grid),
        maximum_speed_after_m_s=_speed_maximum(*new_velocity, grid),
    )
    return (*new_velocity, diagnostics)


def rotating_frame_acceleration_3d(
    u_x_rotating_m_s: NDArray[np.floating],
    u_y_rotating_m_s: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    angular_velocity_rad_s: float,
    angular_acceleration_rad_s2: float,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Return Coriolis, centrifugal and Euler acceleration in the bar frame."""

    grid.require_cell_field(u_x_rotating_m_s, "u_x_rotating_m_s")
    grid.require_cell_field(u_y_rotating_m_s, "u_y_rotating_m_s")
    if not math.isfinite(angular_velocity_rad_s):
        raise ValueError("angular velocity must be finite")
    if not math.isfinite(angular_acceleration_rad_s2):
        raise ValueError("angular acceleration must be finite")
    ux = np.asarray(u_x_rotating_m_s, dtype=np.float64)
    uy = np.asarray(u_y_rotating_m_s, dtype=np.float64)
    omega = float(angular_velocity_rad_s)
    omega_dot = float(angular_acceleration_rad_s2)
    ax = (
        2.0 * omega * uy
        + omega * omega * grid.x_cell
        + omega_dot * grid.y_cell
    )
    ay = (
        -2.0 * omega * ux
        + omega * omega * grid.y_cell
        - omega_dot * grid.x_cell
    )
    zero = np.zeros(grid.shape, dtype=np.float64)
    return (
        np.where(grid.active_mask, ax, 0.0),
        np.where(grid.active_mask, ay, 0.0),
        zero,
    )


def rotating_to_laboratory_velocity_3d(
    u_x_rotating_m_s: NDArray[np.floating],
    u_y_rotating_m_s: NDArray[np.floating],
    u_z_rotating_m_s: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    angular_velocity_rad_s: float,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Transform cell velocity from rotating coordinates to laboratory velocity."""

    for value, name in (
        (u_x_rotating_m_s, "u_x_rotating_m_s"),
        (u_y_rotating_m_s, "u_y_rotating_m_s"),
        (u_z_rotating_m_s, "u_z_rotating_m_s"),
    ):
        grid.require_cell_field(value, name)
    if not math.isfinite(angular_velocity_rad_s):
        raise ValueError("angular velocity must be finite")
    omega = float(angular_velocity_rad_s)
    lab_x = np.asarray(u_x_rotating_m_s, dtype=np.float64) - omega * grid.y_cell
    lab_y = np.asarray(u_y_rotating_m_s, dtype=np.float64) + omega * grid.x_cell
    lab_z = np.asarray(u_z_rotating_m_s, dtype=np.float64).copy()
    return tuple(
        np.where(grid.active_mask, value, 0.0)
        for value in (lab_x, lab_y, lab_z)
    )  # type: ignore[return-value]


def laboratory_cylindrical_velocity_3d(
    u_x_rotating_m_s: NDArray[np.floating],
    u_y_rotating_m_s: NDArray[np.floating],
    u_z_rotating_m_s: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    angular_velocity_rad_s: float,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Return laboratory-frame ``(u_r,u_theta,u_z)`` at Cartesian cells."""

    lab_x, lab_y, lab_z = rotating_to_laboratory_velocity_3d(
        u_x_rotating_m_s,
        u_y_rotating_m_s,
        u_z_rotating_m_s,
        grid,
        angular_velocity_rad_s,
    )
    radius = np.hypot(grid.x_cell, grid.y_cell)
    inverse = np.divide(
        1.0,
        radius,
        out=np.zeros_like(radius),
        where=radius > np.finfo(float).tiny,
    )
    radial = (grid.x_cell * lab_x + grid.y_cell * lab_y) * inverse
    azimuthal = (-grid.y_cell * lab_x + grid.x_cell * lab_y) * inverse
    return tuple(
        np.where(grid.active_mask, value, 0.0)
        for value in (radial, azimuthal, lab_z)
    )  # type: ignore[return-value]


def newtonian_stress_acceleration_3d(
    u_x_m_s: NDArray[np.floating],
    u_y_m_s: NDArray[np.floating],
    u_z_m_s: NDArray[np.floating],
    dynamic_viscosity_pa_s: NDArray[np.floating],
    density_kg_m3: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
) -> tuple[FloatArray, FloatArray, FloatArray, NewtonianStress3DDiagnostics]:
    """Evaluate ``rho^-1 div(mu*(grad u + grad u^T))`` locally.

    The voxel-wall no-slip condition is enforced by the separately audited
    relaxation operator.  Consequently the stress gradients here use
    zero-normal solid extensions, avoiding an undocumented second wall model.
    """

    for value, name in (
        (u_x_m_s, "u_x_m_s"),
        (u_y_m_s, "u_y_m_s"),
        (u_z_m_s, "u_z_m_s"),
        (dynamic_viscosity_pa_s, "dynamic_viscosity_pa_s"),
        (density_kg_m3, "density_kg_m3"),
    ):
        grid.require_cell_field(value, name)
    ux = np.asarray(u_x_m_s, dtype=np.float64)
    uy = np.asarray(u_y_m_s, dtype=np.float64)
    uz = np.asarray(u_z_m_s, dtype=np.float64)
    mu = np.asarray(dynamic_viscosity_pa_s, dtype=np.float64)
    rho = np.asarray(density_kg_m3, dtype=np.float64)
    if (
        not np.isfinite(mu[grid.active_mask]).all()
        or not np.isfinite(rho[grid.active_mask]).all()
        or np.any(mu[grid.active_mask] <= 0.0)
        or np.any(rho[grid.active_mask] <= 0.0)
    ):
        raise ValueError("active viscosity and density must be finite and positive")

    dux_dx, dux_dy, dux_dz = masked_gradient_3d(ux, grid)
    duy_dx, duy_dy, duy_dz = masked_gradient_3d(uy, grid)
    duz_dx, duz_dy, duz_dz = masked_gradient_3d(uz, grid)
    dxx = dux_dx
    dyy = duy_dy
    dzz = duz_dz
    dxy = 0.5 * (dux_dy + duy_dx)
    dxz = 0.5 * (dux_dz + duz_dx)
    dyz = 0.5 * (duy_dz + duz_dy)
    tau_xx = 2.0 * mu * dxx
    tau_yy = 2.0 * mu * dyy
    tau_zz = 2.0 * mu * dzz
    tau_xy = 2.0 * mu * dxy
    tau_xz = 2.0 * mu * dxz
    tau_yz = 2.0 * mu * dyz
    # Each stress-divergence term needs one directional derivative.  Calling
    # ``masked_gradient_3d`` here used to calculate 27 components and discard
    # 18 of them.  The directional helper executes the exact same stencil for
    # the nine components that are actually consumed.
    dtau_xx_dx = masked_gradient_axis_3d(tau_xx, grid, 0)
    dtau_xy_dy = masked_gradient_axis_3d(tau_xy, grid, 1)
    dtau_xz_dz = masked_gradient_axis_3d(tau_xz, grid, 2)
    dtau_xy_dx = masked_gradient_axis_3d(tau_xy, grid, 0)
    dtau_yy_dy = masked_gradient_axis_3d(tau_yy, grid, 1)
    dtau_yz_dz = masked_gradient_axis_3d(tau_yz, grid, 2)
    dtau_xz_dx = masked_gradient_axis_3d(tau_xz, grid, 0)
    dtau_yz_dy = masked_gradient_axis_3d(tau_yz, grid, 1)
    dtau_zz_dz = masked_gradient_axis_3d(tau_zz, grid, 2)
    # Inactive voxels deliberately carry zero density.  Dividing the complete
    # array and masking afterwards emits invalid-value warnings and fails the
    # strict warning gate even though those cells are outside the PDE domain.
    # Restrict the division itself to the positive-density active set.
    active_density = grid.active_mask & (rho > 0.0)
    ax = np.divide(
        dtau_xx_dx + dtau_xy_dy + dtau_xz_dz,
        rho,
        out=np.zeros_like(rho),
        where=active_density,
    )
    ay = np.divide(
        dtau_xy_dx + dtau_yy_dy + dtau_yz_dz,
        rho,
        out=np.zeros_like(rho),
        where=active_density,
    )
    az = np.divide(
        dtau_xz_dx + dtau_yz_dy + dtau_zz_dz,
        rho,
        out=np.zeros_like(rho),
        where=active_density,
    )
    dissipation_density = 2.0 * mu * (
        dxx * dxx
        + dyy * dyy
        + dzz * dzz
        + 2.0 * (dxy * dxy + dxz * dxz + dyz * dyz)
    )
    mean_stress = (tau_xx + tau_yy + tau_zz) / 3.0
    von_mises = np.sqrt(
        1.5
        * (
            (tau_xx - mean_stress) ** 2
            + (tau_yy - mean_stress) ** 2
            + (tau_zz - mean_stress) ** 2
            + 2.0 * (tau_xy * tau_xy + tau_xz * tau_xz + tau_yz * tau_yz)
        )
    )
    acceleration = np.sqrt(ax * ax + ay * ay + az * az)
    diagnostics = NewtonianStress3DDiagnostics(
        viscous_dissipation_w=grid.integrate(dissipation_density),
        maximum_von_mises_stress_pa=float(
            np.max(von_mises[grid.active_mask])
        ),
        maximum_viscous_acceleration_m_s2=float(
            np.max(acceleration[grid.active_mask])
        ),
    )
    return ax, ay, az, diagnostics


def relax_no_slip_boundaries_3d(
    u_x_m_s: NDArray[np.floating],
    u_y_m_s: NDArray[np.floating],
    u_z_m_s: NDArray[np.floating],
    density_kg_m3: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    angular_velocity_rad_s: float,
    dt_s: float,
    *,
    relaxation_time_s: float,
    layer_cells: float,
) -> tuple[FloatArray, FloatArray, FloatArray, BoundaryRelaxation3DDiagnostics]:
    """Apply an exact-exponential immersed no-slip wall relaxation."""

    for value, name in (
        (u_x_m_s, "u_x_m_s"),
        (u_y_m_s, "u_y_m_s"),
        (u_z_m_s, "u_z_m_s"),
        (density_kg_m3, "density_kg_m3"),
    ):
        grid.require_cell_field(value, name)
    if not math.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError("dt_s must be finite and positive")
    if not math.isfinite(relaxation_time_s) or relaxation_time_s <= 0.0:
        raise ValueError("relaxation_time_s must be finite and positive")
    rho = np.asarray(density_kg_m3, dtype=np.float64)
    if not np.isfinite(rho[grid.active_mask]).all() or np.any(
        rho[grid.active_mask] <= 0.0
    ):
        raise ValueError("density must be finite and positive")
    weight, target_x, target_y, target_z = grid.boundary_relaxation_targets(
        angular_velocity_rad_s, layer_cells
    )
    decay = np.exp(-dt_s * weight / relaxation_time_s)
    old = tuple(
        np.asarray(value, dtype=np.float64)
        for value in (u_x_m_s, u_y_m_s, u_z_m_s)
    )
    targets = (target_x, target_y, target_z)
    new = tuple(
        np.where(grid.active_mask, target + (value - target) * decay, 0.0)
        for value, target in zip(old, targets)
    )
    old_energy = 0.5 * grid.integrate(
        rho * (old[0] ** 2 + old[1] ** 2 + old[2] ** 2)
    )
    new_energy = 0.5 * grid.integrate(
        rho * (new[0] ** 2 + new[1] ** 2 + new[2] ** 2)
    )
    acceleration = np.sqrt(
        ((new[0] - old[0]) / dt_s) ** 2
        + ((new[1] - old[1]) / dt_s) ** 2
        + ((new[2] - old[2]) / dt_s) ** 2
    )
    diagnostics = BoundaryRelaxation3DDiagnostics(
        rotating_frame_kinetic_energy_change_j=new_energy - old_energy,
        maximum_relaxation_acceleration_m_s2=float(
            np.max(acceleration[grid.active_mask])
        ),
        active_boundary_layer_cells=int(np.sum(weight > 0.0)),
        maximum_layer_weight=float(np.max(weight[grid.active_mask])),
    )
    return (*new, diagnostics)


__all__ = [
    "BoundaryRelaxation3DDiagnostics",
    "MomentumAdvection3DDiagnostics",
    "NewtonianStress3DDiagnostics",
    "advect_momentum_3d",
    "laboratory_cylindrical_velocity_3d",
    "newtonian_stress_acceleration_3d",
    "relax_no_slip_boundaries_3d",
    "rotating_frame_acceleration_3d",
    "rotating_to_laboratory_velocity_3d",
]
