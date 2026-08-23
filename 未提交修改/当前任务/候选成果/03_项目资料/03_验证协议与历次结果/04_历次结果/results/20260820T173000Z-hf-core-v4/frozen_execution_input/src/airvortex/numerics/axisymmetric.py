"""Axisymmetric finite-volume grid utilities used by the HF-preview solver.

The meridional velocity is stored on a MAC grid: ``u_r`` lives on radial
faces and ``u_z`` on horizontal faces.  Scalars and swirl velocity live at
cell centres.  Keeping this convention in one small module makes the pressure
projection discretely compatible with the diagnostic divergence.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray


FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class AxisymmetricGrid:
    """Uniform cell-centred ``r-z`` grid for a cylindrical domain.

    Parameters are deliberately explicit and SI-only.  Cell centres never lie
    on ``r=0``; the first radial face does, avoiding direct evaluation of a
    singular ``1/r`` term.
    """

    radial_cells: int
    axial_cells: int
    radius_m: float
    height_m: float

    def __post_init__(self) -> None:
        if isinstance(self.radial_cells, bool) or self.radial_cells < 2:
            raise ValueError("radial_cells must be an integer >= 2")
        if isinstance(self.axial_cells, bool) or self.axial_cells < 2:
            raise ValueError("axial_cells must be an integer >= 2")
        for name, value in (("radius_m", self.radius_m), ("height_m", self.height_m)):
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
            if value <= 0.0:
                raise ValueError(f"{name} must be positive")

    @property
    def nr(self) -> int:
        return self.radial_cells

    @property
    def nz(self) -> int:
        return self.axial_cells

    @property
    def dr(self) -> float:
        return self.radius_m / self.radial_cells

    @property
    def dz(self) -> float:
        return self.height_m / self.axial_cells

    @property
    def r_centres(self) -> FloatArray:
        return (np.arange(self.nr, dtype=np.float64) + 0.5) * self.dr

    @property
    def z_centres(self) -> FloatArray:
        return (np.arange(self.nz, dtype=np.float64) + 0.5) * self.dz

    # American-spelling aliases make interactive use less surprising.
    @property
    def r_centers(self) -> FloatArray:
        return self.r_centres

    @property
    def z_centers(self) -> FloatArray:
        return self.z_centres

    @property
    def r_faces(self) -> FloatArray:
        return np.arange(self.nr + 1, dtype=np.float64) * self.dr

    @property
    def z_faces(self) -> FloatArray:
        return np.arange(self.nz + 1, dtype=np.float64) * self.dz

    @property
    def cell_volumes(self) -> FloatArray:
        """Physical volumes, including the full ``2*pi`` azimuthal sweep."""

        radial = 2.0 * np.pi * self.r_centres * self.dr * self.dz
        return np.broadcast_to(radial[:, None], (self.nr, self.nz)).copy()

    @property
    def shape(self) -> tuple[int, int]:
        return (self.nr, self.nz)

    def require_cell_field(self, field: NDArray[np.floating], name: str = "field") -> None:
        if np.shape(field) != self.shape:
            raise ValueError(f"{name} must have shape {self.shape}, got {np.shape(field)}")

    def require_face_velocity(
        self, u_r: NDArray[np.floating], u_z: NDArray[np.floating]
    ) -> None:
        expected_r = (self.nr + 1, self.nz)
        expected_z = (self.nr, self.nz + 1)
        if np.shape(u_r) != expected_r:
            raise ValueError(f"u_r must have shape {expected_r}, got {np.shape(u_r)}")
        if np.shape(u_z) != expected_z:
            raise ValueError(f"u_z must have shape {expected_z}, got {np.shape(u_z)}")

    def integrate(self, field: NDArray[np.floating]) -> float:
        self.require_cell_field(field)
        return float(np.sum(np.asarray(field, dtype=np.float64) * self.cell_volumes))

    def divergence(self, u_r: NDArray[np.floating], u_z: NDArray[np.floating]) -> FloatArray:
        """Finite-volume ``div(u)`` at cell centres."""

        self.require_face_velocity(u_r, u_z)
        ur = np.asarray(u_r, dtype=np.float64)
        uz = np.asarray(u_z, dtype=np.float64)
        r_f = self.r_faces
        r_c = self.r_centres
        radial = (
            r_f[1:, None] * ur[1:, :] - r_f[:-1, None] * ur[:-1, :]
        ) / (r_c[:, None] * self.dr)
        axial = (uz[:, 1:] - uz[:, :-1]) / self.dz
        return radial + axial

    def faces_to_cells(
        self, u_r: NDArray[np.floating], u_z: NDArray[np.floating]
    ) -> tuple[FloatArray, FloatArray]:
        self.require_face_velocity(u_r, u_z)
        ur_c = 0.5 * (np.asarray(u_r[:-1, :]) + np.asarray(u_r[1:, :]))
        uz_c = 0.5 * (np.asarray(u_z[:, :-1]) + np.asarray(u_z[:, 1:]))
        return ur_c.astype(np.float64, copy=False), uz_c.astype(np.float64, copy=False)

    def cells_to_faces(
        self, u_r_cell: NDArray[np.floating], u_z_cell: NDArray[np.floating]
    ) -> tuple[FloatArray, FloatArray]:
        """Interpolate cell velocities to faces with impermeable outer walls."""

        self.require_cell_field(u_r_cell, "u_r_cell")
        self.require_cell_field(u_z_cell, "u_z_cell")
        ur_f = np.zeros((self.nr + 1, self.nz), dtype=np.float64)
        uz_f = np.zeros((self.nr, self.nz + 1), dtype=np.float64)
        ur_f[1:-1, :] = 0.5 * (u_r_cell[:-1, :] + u_r_cell[1:, :])
        uz_f[:, 1:-1] = 0.5 * (u_z_cell[:, :-1] + u_z_cell[:, 1:])
        return ur_f, uz_f

    def scalar_laplacian(
        self, field: NDArray[np.floating], *, vector_component: bool = False
    ) -> FloatArray:
        """Return the axisymmetric Laplacian with homogeneous Neumann walls.

        For a radial or azimuthal vector component, ``-field/r**2`` is included.
        Wall no-slip is imposed separately after an explicit viscous update.
        """

        self.require_cell_field(field)
        q = np.asarray(field, dtype=np.float64)
        grad_r = np.zeros((self.nr + 1, self.nz), dtype=np.float64)
        grad_z = np.zeros((self.nr, self.nz + 1), dtype=np.float64)
        grad_r[1:-1, :] = (q[1:, :] - q[:-1, :]) / self.dr
        grad_z[:, 1:-1] = (q[:, 1:] - q[:, :-1]) / self.dz
        radial = (
            self.r_faces[1:, None] * grad_r[1:, :]
            - self.r_faces[:-1, None] * grad_r[:-1, :]
        ) / (self.r_centres[:, None] * self.dr)
        axial = (grad_z[:, 1:] - grad_z[:, :-1]) / self.dz
        result = radial + axial
        if vector_component:
            result = result - q / np.square(self.r_centres[:, None])
        return result

    def cell_gradient(self, field: NDArray[np.floating]) -> tuple[FloatArray, FloatArray]:
        """Second-order interior gradient used by diffuse-interface forces."""

        self.require_cell_field(field)
        q = np.asarray(field, dtype=np.float64)
        edge_order = 2 if min(self.shape) >= 3 else 1
        grad_r, grad_z = np.gradient(q, self.dr, self.dz, edge_order=edge_order)
        # Axisymmetry makes a scalar even in r, hence d(q)/dr = 0 at the axis.
        grad_r[0, :] = 0.0
        return grad_r, grad_z


def impose_cell_velocity_boundaries(
    u_r: FloatArray, u_z: FloatArray, u_theta: FloatArray
) -> None:
    """Apply inexpensive no-slip/axis parity constraints in-place.

    The pressure projection separately enforces zero normal face velocity.  This
    helper damps tangential cell velocities at the vessel wall and lid while
    retaining the near-axis ``u_theta ~ r`` behaviour.
    """

    if min(u_r.shape) < 2:
        return
    u_r[-1, :] = 0.0
    u_r[:, 0] = 0.0
    u_r[:, -1] = 0.0
    u_z[-1, :] = 0.0
    u_z[:, 0] = 0.0
    u_z[:, -1] = 0.0
    u_theta[-1, :] = 0.0
    u_theta[:, 0] = 0.0
    u_theta[:, -1] = 0.0

