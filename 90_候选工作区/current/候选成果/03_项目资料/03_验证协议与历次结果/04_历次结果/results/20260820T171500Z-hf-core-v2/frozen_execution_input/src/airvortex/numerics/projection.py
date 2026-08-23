"""A fixed, factorised axisymmetric pressure projection."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csc_matrix, lil_matrix
from scipy.sparse.linalg import SuperLU, splu

from .axisymmetric import AxisymmetricGrid, FloatArray


@dataclass(frozen=True, slots=True)
class ProjectionDiagnostics:
    divergence_before: float
    divergence_after: float
    pressure_relative_residual: float
    removed_rhs_mean: float


class AxisymmetricProjection:
    """MAC projection using one ``splu`` factorisation for the full run.

    Boundary conditions are homogeneous Neumann pressure conditions paired with
    impermeable velocity boundaries.  The volume-weighted mean of the Poisson
    right-hand side is removed and one pressure cell fixes the gauge.
    """

    def __init__(self, grid: AxisymmetricGrid) -> None:
        self.grid = grid
        physical = self._build_laplacian()
        gauge_fixed = physical.tolil(copy=True)
        gauge_fixed.rows[0] = [0]
        gauge_fixed.data[0] = [1.0]
        self._physical_matrix = physical
        self._matrix = csc_matrix(gauge_fixed)
        self._factor: SuperLU = splu(self._matrix)
        self.factorization_count = 1

    @property
    def matrix(self) -> csc_matrix:
        """Gauge-fixed pressure matrix (returned for diagnostics/tests)."""

        return self._matrix

    def _index(self, i: int, j: int) -> int:
        return i * self.grid.nz + j

    def _build_laplacian(self) -> csc_matrix:
        g = self.grid
        size = g.nr * g.nz
        matrix = lil_matrix((size, size), dtype=np.float64)
        for i, r_c in enumerate(g.r_centres):
            for j in range(g.nz):
                row = self._index(i, j)
                diagonal = 0.0
                if i > 0:
                    coefficient = g.r_faces[i] / (r_c * g.dr * g.dr)
                    matrix[row, self._index(i - 1, j)] = coefficient
                    diagonal -= coefficient
                if i < g.nr - 1:
                    coefficient = g.r_faces[i + 1] / (r_c * g.dr * g.dr)
                    matrix[row, self._index(i + 1, j)] = coefficient
                    diagonal -= coefficient
                if j > 0:
                    coefficient = 1.0 / (g.dz * g.dz)
                    matrix[row, self._index(i, j - 1)] = coefficient
                    diagonal -= coefficient
                if j < g.nz - 1:
                    coefficient = 1.0 / (g.dz * g.dz)
                    matrix[row, self._index(i, j + 1)] = coefficient
                    diagonal -= coefficient
                matrix[row, row] = diagonal
        return csc_matrix(matrix)

    def project(
        self,
        u_r: NDArray[np.floating],
        u_z: NDArray[np.floating],
        dt: float,
        reference_density: float = 1.0,
    ) -> tuple[FloatArray, FloatArray, FloatArray, ProjectionDiagnostics]:
        """Project tentative face velocities onto the discrete solenoidal space."""

        if not np.isfinite(dt) or dt <= 0.0:
            raise ValueError("dt must be finite and positive")
        if not np.isfinite(reference_density) or reference_density <= 0.0:
            raise ValueError("reference_density must be finite and positive")
        self.grid.require_face_velocity(u_r, u_z)
        ur = np.asarray(u_r, dtype=np.float64).copy()
        uz = np.asarray(u_z, dtype=np.float64).copy()
        # The projection boundary model is a closed cylindrical box.
        ur[0, :] = 0.0
        ur[-1, :] = 0.0
        uz[:, 0] = 0.0
        uz[:, -1] = 0.0
        divergence = self.grid.divergence(ur, uz)
        before = float(np.max(np.abs(divergence)))
        weights = self.grid.cell_volumes
        rhs = (reference_density / dt) * divergence
        rhs_mean = float(np.sum(rhs * weights) / np.sum(weights))
        rhs = rhs - rhs_mean
        rhs_flat = rhs.ravel().copy()
        rhs_flat[0] = 0.0
        pressure_flat = self._factor.solve(rhs_flat)
        pressure = pressure_flat.reshape(self.grid.shape)

        ur[1:-1, :] -= (dt / reference_density) * (
            pressure[1:, :] - pressure[:-1, :]
        ) / self.grid.dr
        uz[:, 1:-1] -= (dt / reference_density) * (
            pressure[:, 1:] - pressure[:, :-1]
        ) / self.grid.dz
        ur[0, :] = ur[-1, :] = 0.0
        uz[:, 0] = uz[:, -1] = 0.0
        after_field = self.grid.divergence(ur, uz)
        after = float(np.max(np.abs(after_field)))

        physical_rhs = rhs.ravel()
        residual = self._physical_matrix @ pressure_flat - physical_rhs
        residual_norm = float(np.linalg.norm(residual))
        rhs_norm = max(float(np.linalg.norm(physical_rhs)), np.finfo(float).tiny)
        diagnostics = ProjectionDiagnostics(
            divergence_before=before,
            divergence_after=after,
            pressure_relative_residual=residual_norm / rhs_norm,
            removed_rhs_mean=rhs_mean,
        )
        return ur, uz, pressure, diagnostics

