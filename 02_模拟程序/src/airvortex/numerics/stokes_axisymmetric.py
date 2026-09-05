"""Axisymmetric incompressible Stokes operator for a flat reference cylinder.

The meridional velocity uses a MAC grid.  The axis and solid walls are
impermeable, the side/bottom walls are no slip, and the flat top accepts normal
and tangential traction.  This is the fixed-domain operator used by the
linear-moving-surface SH stage; it is not a nonlinear remeshed free boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csc_matrix, lil_matrix
from scipy.sparse.linalg import SuperLU, splu

from .axisymmetric import AxisymmetricGrid


FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class StokesSolveDiagnostics:
    relative_linear_residual: float
    maximum_divergence_s_inv: float
    top_volume_flux_m3_s: float
    pressure_min_pa: float
    pressure_max_pa: float


@dataclass(frozen=True, slots=True)
class AxisymmetricStrain:
    rr_s_inv: FloatArray
    tt_s_inv: FloatArray
    zz_s_inv: FloatArray
    rz_s_inv: FloatArray

    @property
    def double_contraction_s_inv2(self) -> FloatArray:
        return (
            np.square(self.rr_s_inv)
            + np.square(self.tt_s_inv)
            + np.square(self.zz_s_inv)
            + 2.0 * np.square(self.rz_s_inv)
        )


@dataclass(frozen=True, slots=True)
class AxisymmetricStokesSolution:
    radial_velocity_m_s: FloatArray
    axial_velocity_m_s: FloatArray
    pressure_pa: FloatArray
    strain: AxisymmetricStrain
    diagnostics: StokesSolveDiagnostics


@dataclass(frozen=True, slots=True)
class InertiaDiagnostics:
    meridional_reynolds: float
    unsteady_stokes: float
    inertia_residual_ratio: float
    velocity_scale_m_s: float
    length_scale_m: float
    time_scale_s: float | None


class AxisymmetricStokesOperator:
    """Factorised finite-volume/finite-difference Stokes saddle operator."""

    def __init__(
        self,
        grid: AxisymmetricGrid,
        viscosity_pa_s: float,
    ) -> None:
        if not math.isfinite(float(viscosity_pa_s)) or viscosity_pa_s <= 0.0:
            raise ValueError("viscosity_pa_s must be finite and positive")
        self.grid = grid
        self.viscosity_pa_s = float(viscosity_pa_s)
        self._radial_count = (grid.nr - 1) * grid.nz
        self._axial_count = grid.nr * grid.nz
        self._pressure_count = grid.nr * grid.nz
        self._size = self._radial_count + self._axial_count + self._pressure_count
        self._matrix = self._build_matrix()
        self._factor: SuperLU = splu(self._matrix)
        self.factorization_count = 1

    def _u_index(self, i: int, j: int) -> int:
        if not 1 <= i < self.grid.nr or not 0 <= j < self.grid.nz:
            raise IndexError("radial velocity index is outside the unknown set")
        return (i - 1) * self.grid.nz + j

    def _w_index(self, i: int, j: int) -> int:
        if not 0 <= i < self.grid.nr or not 1 <= j <= self.grid.nz:
            raise IndexError("axial velocity index is outside the unknown set")
        return self._radial_count + i * self.grid.nz + (j - 1)

    def _p_index(self, i: int, j: int) -> int:
        if not 0 <= i < self.grid.nr or not 0 <= j < self.grid.nz:
            raise IndexError("pressure index is outside the grid")
        return self._radial_count + self._axial_count + i * self.grid.nz + j

    def _add_u(self, matrix: lil_matrix, row: int, i: int, j: int, value: float) -> None:
        if 1 <= i < self.grid.nr:
            matrix[row, self._u_index(i, j)] += value

    def _add_w(self, matrix: lil_matrix, row: int, i: int, j: int, value: float) -> None:
        if 1 <= j <= self.grid.nz:
            matrix[row, self._w_index(i, j)] += value

    def _build_matrix(self) -> csc_matrix:
        grid = self.grid
        mu = self.viscosity_pa_s
        dr = grid.dr
        dz = grid.dz
        matrix = lil_matrix((self._size, self._size), dtype=np.float64)
        row = 0

        # Radial momentum at interior radial faces and axial cell centres.
        for i in range(1, grid.nr):
            radius = grid.r_faces[i]
            for j in range(grid.nz):
                centre = -2.0 / dr**2 - 1.0 / radius**2
                lower = 1.0 / dr**2 - 1.0 / (2.0 * radius * dr)
                upper = 1.0 / dr**2 + 1.0 / (2.0 * radius * dr)
                self._add_u(matrix, row, i - 1, j, mu * lower)
                self._add_u(matrix, row, i, j, mu * centre)
                self._add_u(matrix, row, i + 1, j, mu * upper)
                if j == 0:
                    # Odd ghost about the no-slip bottom at z=0.
                    self._add_u(matrix, row, i, j, -3.0 * mu / dz**2)
                    self._add_u(matrix, row, i, j + 1, mu / dz**2)
                elif j == grid.nz - 1:
                    # Top shear: mu*(du/dz+dw/dr)=tau_rz.  The known tau
                    # enters the RHS; dw/dr remains in the operator.
                    self._add_u(matrix, row, i, j - 1, mu / dz**2)
                    self._add_u(matrix, row, i, j, -mu / dz**2)
                    self._add_w(matrix, row, i, grid.nz, -mu / (dz * dr))
                    self._add_w(matrix, row, i - 1, grid.nz, mu / (dz * dr))
                else:
                    self._add_u(matrix, row, i, j - 1, mu / dz**2)
                    self._add_u(matrix, row, i, j, -2.0 * mu / dz**2)
                    self._add_u(matrix, row, i, j + 1, mu / dz**2)
                matrix[row, self._p_index(i, j)] += -1.0 / dr
                matrix[row, self._p_index(i - 1, j)] += 1.0 / dr
                row += 1

        # Axial momentum on interior horizontal faces.  Bottom is no penetration;
        # the top face is an unknown governed by normal traction below.
        for i, radius in enumerate(grid.r_centres):
            for j in range(1, grid.nz):
                radial_diagonal = 0.0
                if i > 0:
                    coefficient = grid.r_faces[i] / (radius * dr**2)
                    self._add_w(matrix, row, i - 1, j, mu * coefficient)
                    radial_diagonal -= coefficient
                if i < grid.nr - 1:
                    coefficient = grid.r_faces[i + 1] / (radius * dr**2)
                    self._add_w(matrix, row, i + 1, j, mu * coefficient)
                    radial_diagonal -= coefficient
                else:
                    # No-slip at r=R, a half cell away from the last centre.
                    radial_diagonal -= 2.0 * grid.r_faces[-1] / (radius * dr**2)
                self._add_w(matrix, row, i, j, mu * radial_diagonal)
                self._add_w(matrix, row, i, j - 1, mu / dz**2)
                self._add_w(matrix, row, i, j, -2.0 * mu / dz**2)
                self._add_w(matrix, row, i, j + 1, mu / dz**2)
                matrix[row, self._p_index(i, j)] += -1.0 / dz
                matrix[row, self._p_index(i, j - 1)] += 1.0 / dz
                row += 1

        # Normal traction at the flat top: -p + 2*mu*dw/dz = supplied traction.
        # A three-face backward derivative and second-order pressure
        # extrapolation retain consistency at the physical boundary.
        for i in range(grid.nr):
            matrix[row, self._p_index(i, grid.nz - 1)] = -1.5
            matrix[row, self._p_index(i, grid.nz - 2)] = 0.5
            self._add_w(matrix, row, i, grid.nz, 3.0 * mu / dz)
            self._add_w(matrix, row, i, grid.nz - 1, -4.0 * mu / dz)
            self._add_w(matrix, row, i, grid.nz - 2, mu / dz)
            row += 1

        # Conservative cell divergence.  Its area-weighted sum is exactly the
        # top volume flux because all other normal boundary velocities vanish.
        for i, radius in enumerate(grid.r_centres):
            for j in range(grid.nz):
                self._add_u(
                    matrix,
                    row,
                    i + 1,
                    j,
                    grid.r_faces[i + 1] / (radius * dr),
                )
                self._add_u(
                    matrix,
                    row,
                    i,
                    j,
                    -grid.r_faces[i] / (radius * dr),
                )
                self._add_w(matrix, row, i, j + 1, 1.0 / dz)
                self._add_w(matrix, row, i, j, -1.0 / dz)
                row += 1

        if row != self._size:
            raise RuntimeError(f"internal Stokes row count {row} != {self._size}")
        return csc_matrix(matrix)

    def solve(
        self,
        radial_force_n_m3: NDArray[np.floating],
        axial_force_n_m3: NDArray[np.floating],
        normal_traction_pa: NDArray[np.floating],
        tangential_traction_pa: NDArray[np.floating] | None = None,
    ) -> AxisymmetricStokesSolution:
        """Solve one instantaneous fixed-domain Stokes problem."""

        grid = self.grid
        radial_force = np.asarray(radial_force_n_m3, dtype=np.float64)
        axial_force = np.asarray(axial_force_n_m3, dtype=np.float64)
        normal_traction = np.asarray(normal_traction_pa, dtype=np.float64)
        tangential_traction = (
            np.zeros(grid.nr - 1, dtype=np.float64)
            if tangential_traction_pa is None
            else np.asarray(tangential_traction_pa, dtype=np.float64)
        )
        expected = {
            "radial_force_n_m3": ((grid.nr - 1, grid.nz), radial_force),
            "axial_force_n_m3": ((grid.nr, grid.nz - 1), axial_force),
            "normal_traction_pa": ((grid.nr,), normal_traction),
            "tangential_traction_pa": ((grid.nr - 1,), tangential_traction),
        }
        for name, (shape, value) in expected.items():
            if value.shape != shape:
                raise ValueError(f"{name} must have shape {shape}, got {value.shape}")
            if not np.isfinite(value).all():
                raise ValueError(f"{name} must be finite")

        rhs = np.zeros(self._size, dtype=np.float64)
        row = 0
        for i in range(grid.nr - 1):
            for j in range(grid.nz):
                rhs[row] = -radial_force[i, j]
                if j == grid.nz - 1:
                    rhs[row] -= tangential_traction[i] / grid.dz
                row += 1
        for i in range(grid.nr):
            for j in range(grid.nz - 1):
                rhs[row] = -axial_force[i, j]
                row += 1
        rhs[row : row + grid.nr] = normal_traction
        row += grid.nr
        # Divergence RHS is zero.
        row += grid.nr * grid.nz
        if row != self._size:
            raise RuntimeError("internal Stokes RHS layout mismatch")

        packed = self._factor.solve(rhs)
        radial_velocity = np.zeros((grid.nr + 1, grid.nz), dtype=np.float64)
        for i in range(1, grid.nr):
            for j in range(grid.nz):
                radial_velocity[i, j] = packed[self._u_index(i, j)]
        axial_velocity = np.zeros((grid.nr, grid.nz + 1), dtype=np.float64)
        for i in range(grid.nr):
            for j in range(1, grid.nz + 1):
                axial_velocity[i, j] = packed[self._w_index(i, j)]
        pressure = np.empty(grid.shape, dtype=np.float64)
        for i in range(grid.nr):
            for j in range(grid.nz):
                pressure[i, j] = packed[self._p_index(i, j)]
        divergence = grid.divergence(radial_velocity, axial_velocity)
        residual = self._matrix @ packed - rhs
        relative_residual = float(
            np.linalg.norm(residual) / max(np.linalg.norm(rhs), np.finfo(float).tiny)
        )
        top_flux = float(
            np.sum(axial_velocity[:, -1] * math.pi * (
                np.square(grid.r_faces[1:]) - np.square(grid.r_faces[:-1])
            ))
        )
        return AxisymmetricStokesSolution(
            radial_velocity_m_s=radial_velocity,
            axial_velocity_m_s=axial_velocity,
            pressure_pa=pressure,
            strain=axisymmetric_strain(grid, radial_velocity, axial_velocity),
            diagnostics=StokesSolveDiagnostics(
                relative_linear_residual=relative_residual,
                maximum_divergence_s_inv=float(np.max(np.abs(divergence))),
                top_volume_flux_m3_s=top_flux,
                pressure_min_pa=float(np.min(pressure)),
                pressure_max_pa=float(np.max(pressure)),
            ),
        )


def axisymmetric_strain(
    grid: AxisymmetricGrid,
    radial_velocity_m_s: NDArray[np.floating],
    axial_velocity_m_s: NDArray[np.floating],
) -> AxisymmetricStrain:
    """Return all nonzero meridional strain components, including ``D_tt``."""

    grid.require_face_velocity(radial_velocity_m_s, axial_velocity_m_s)
    ur = np.asarray(radial_velocity_m_s, dtype=np.float64)
    uz = np.asarray(axial_velocity_m_s, dtype=np.float64)
    ur_cell, uz_cell = grid.faces_to_cells(ur, uz)
    drr = (ur[1:, :] - ur[:-1, :]) / grid.dr
    dtt = ur_cell / grid.r_centres[:, None]
    dzz = (uz[:, 1:] - uz[:, :-1]) / grid.dz
    edge_order = 2 if min(grid.shape) >= 3 else 1
    dur_dz = np.gradient(ur_cell, grid.dz, axis=1, edge_order=edge_order)
    duz_dr = np.gradient(uz_cell, grid.dr, axis=0, edge_order=edge_order)
    drz = 0.5 * (dur_dz + duz_dr)
    return AxisymmetricStrain(drr, dtt, dzz, drz)


def viscous_dissipation_w(
    grid: AxisymmetricGrid,
    strain: AxisymmetricStrain,
    viscosity_pa_s: float,
) -> float:
    """Return ``2 mu integral D:D 2*pi*r dr dz`` in watts."""

    return 2.0 * float(viscosity_pa_s) * grid.integrate(
        strain.double_contraction_s_inv2
    )


def body_force_power_w(
    grid: AxisymmetricGrid,
    radial_force_n_m3: NDArray[np.floating],
    axial_force_n_m3: NDArray[np.floating],
    solution: AxisymmetricStokesSolution,
) -> float:
    """Integrate body-force work using the same physical ``2*pi*r`` weights."""

    radial_force = np.asarray(radial_force_n_m3, dtype=np.float64)
    axial_force = np.asarray(axial_force_n_m3, dtype=np.float64)
    if radial_force.shape != (grid.nr - 1, grid.nz):
        raise ValueError("radial force shape mismatch")
    if axial_force.shape != (grid.nr, grid.nz - 1):
        raise ValueError("axial force shape mismatch")
    ur_cell, uz_cell = grid.faces_to_cells(
        solution.radial_velocity_m_s, solution.axial_velocity_m_s
    )
    radial_cell = np.empty(grid.shape, dtype=np.float64)
    radial_cell[0] = radial_force[0]
    radial_cell[-1] = radial_force[-1]
    if grid.nr > 2:
        radial_cell[1:-1] = 0.5 * (radial_force[:-1] + radial_force[1:])
    axial_cell = np.empty(grid.shape, dtype=np.float64)
    axial_cell[:, 0] = axial_force[:, 0]
    axial_cell[:, -1] = axial_force[:, -1]
    if grid.nz > 2:
        axial_cell[:, 1:-1] = 0.5 * (axial_force[:, :-1] + axial_force[:, 1:])
    return grid.integrate(radial_cell * ur_cell + axial_cell * uz_cell)


def meridional_inertia_diagnostics(
    grid: AxisymmetricGrid,
    density_kg_m3: float,
    viscosity_pa_s: float,
    solution: AxisymmetricStokesSolution,
    *,
    previous_solution: AxisymmetricStokesSolution | None = None,
    elapsed_s: float | None = None,
) -> InertiaDiagnostics:
    """Evaluate S18 and the omitted meridional-inertia residual from the solution."""

    ur, uz = grid.faces_to_cells(
        solution.radial_velocity_m_s, solution.axial_velocity_m_s
    )
    speed = np.hypot(ur, uz)
    velocity_scale = float(np.max(speed))
    edge_order = 2 if min(grid.shape) >= 3 else 1
    gradients = (
        np.gradient(ur, grid.dr, axis=0, edge_order=edge_order),
        np.gradient(ur, grid.dz, axis=1, edge_order=edge_order),
        np.gradient(uz, grid.dr, axis=0, edge_order=edge_order),
        np.gradient(uz, grid.dz, axis=1, edge_order=edge_order),
    )
    gradient_scale = max(float(np.max(np.abs(item))) for item in gradients)
    minimum_length = min(grid.dr, grid.dz)
    domain_length = min(grid.radius_m, grid.height_m)
    if velocity_scale <= np.finfo(float).tiny:
        length_scale = domain_length
        reynolds = 0.0
    else:
        length_scale = min(
            domain_length,
            max(minimum_length, velocity_scale / max(gradient_scale, np.finfo(float).tiny)),
        )
        reynolds = density_kg_m3 * velocity_scale * length_scale / viscosity_pa_s

    dur_dt = np.zeros_like(ur)
    duz_dt = np.zeros_like(uz)
    time_scale: float | None = None
    unsteady_stokes = 0.0
    if previous_solution is not None:
        if elapsed_s is None or elapsed_s <= 0.0:
            raise ValueError("positive elapsed_s is required with previous_solution")
        previous_ur, previous_uz = grid.faces_to_cells(
            previous_solution.radial_velocity_m_s,
            previous_solution.axial_velocity_m_s,
        )
        dur_dt = (ur - previous_ur) / elapsed_s
        duz_dt = (uz - previous_uz) / elapsed_s
        derivative_scale = float(np.max(np.hypot(dur_dt, duz_dt)))
        if velocity_scale > np.finfo(float).tiny and derivative_scale > 0.0:
            time_scale = velocity_scale / derivative_scale
            unsteady_stokes = (
                density_kg_m3 * length_scale**2 / (viscosity_pa_s * time_scale)
            )

    convective_r = ur * gradients[0] + uz * gradients[1]
    convective_z = ur * gradients[2] + uz * gradients[3]
    inertia_r = density_kg_m3 * (dur_dt + convective_r)
    inertia_z = density_kg_m3 * (duz_dt + convective_z)
    pressure = solution.pressure_pa
    grad_p_r = np.gradient(pressure, grid.dr, axis=0, edge_order=edge_order)
    grad_p_z = np.gradient(pressure, grid.dz, axis=1, edge_order=edge_order)
    inertia_norm = math.sqrt(grid.integrate(inertia_r**2 + inertia_z**2))
    pressure_norm = math.sqrt(grid.integrate(grad_p_r**2 + grad_p_z**2))
    # The pressure-gradient scale remains meaningful in near-cancelling regions;
    # use machine scale only for the exact quiescent state.
    denominator = max(pressure_norm, np.finfo(float).tiny)
    residual_ratio = inertia_norm / denominator
    if velocity_scale <= np.finfo(float).tiny and pressure_norm <= np.finfo(float).tiny:
        residual_ratio = 0.0
    return InertiaDiagnostics(
        meridional_reynolds=float(reynolds),
        unsteady_stokes=float(unsteady_stokes),
        inertia_residual_ratio=float(residual_ratio),
        velocity_scale_m_s=velocity_scale,
        length_scale_m=float(length_scale),
        time_scale_s=time_scale,
    )


__all__ = [
    "AxisymmetricStokesOperator",
    "AxisymmetricStokesSolution",
    "AxisymmetricStrain",
    "InertiaDiagnostics",
    "StokesSolveDiagnostics",
    "axisymmetric_strain",
    "body_force_power_w",
    "meridional_inertia_diagnostics",
    "viscous_dissipation_w",
]
