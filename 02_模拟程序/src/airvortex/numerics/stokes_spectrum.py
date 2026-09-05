"""Independent finite-depth checks and finite-cylinder surface modes for SH.

The planar fourth-order problem is retained as a verification problem.  The
finite-cylinder basis is built separately from the conservative radial
Neumann operator; no plane Fourier mode is relabelled as a vessel mode.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_bvp
from scipy.linalg import eigh


FloatArray = NDArray[np.float64]


def finite_depth_mobility(k_per_m: float, depth_m: float, viscosity_pa_s: float) -> float:
    """Return the stable closed form in (S15), including its exact zero mode."""

    for name, value in (
        ("k_per_m", k_per_m),
        ("depth_m", depth_m),
        ("viscosity_pa_s", viscosity_pa_s),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a real number")
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    k = float(k_per_m)
    depth = float(depth_m)
    viscosity = float(viscosity_pa_s)
    if k < 0.0 or depth <= 0.0 or viscosity <= 0.0:
        raise ValueError("require k >= 0, depth > 0 and viscosity > 0")
    if k == 0.0:
        return 0.0
    x = k * depth
    if x < 1.0e-3:
        # sinh(2x)-2x evaluated without subtracting nearly equal numbers.
        numerator = (
            (4.0 / 3.0) * x**3
            + (4.0 / 15.0) * x**5
            + (8.0 / 315.0) * x**7
            + (4.0 / 2835.0) * x**9
        )
        bracket = math.cosh(2.0 * x) + 2.0 * x * x + 1.0
        return numerator / (2.0 * viscosity * k * bracket)
    if x > 100.0:
        # Divide numerator and denominator by exp(2x)/2 to avoid overflow.
        decay = math.exp(-2.0 * x)
        return (
            1.0 - decay * decay - 4.0 * x * decay
        ) / (
            2.0
            * viscosity
            * k
            * (1.0 + decay * decay + (4.0 * x * x + 2.0) * decay)
        )
    return (math.sinh(2.0 * x) - 2.0 * x) / (
        2.0
        * viscosity
        * k
        * (math.cosh(2.0 * x) + 2.0 * x * x + 1.0)
    )


def independent_finite_depth_mobility_bvp(
    k_per_m: float,
    depth_m: float,
    viscosity_pa_s: float,
    *,
    nodes: int = 80,
    tolerance: float = 1.0e-10,
) -> float:
    """Solve the dimensional fourth-order boundary problem with ``solve_bvp``.

    This implementation does not evaluate the closed form and is kept as an
    independent acceptance reference.  It is intended for moderate ``kd``;
    the stable formula is the production path for extreme shallow/deep limits.
    """

    if k_per_m <= 0.0:
        if k_per_m == 0.0:
            return 0.0
        raise ValueError("k_per_m must be non-negative")
    if depth_m <= 0.0 or viscosity_pa_s <= 0.0:
        raise ValueError("depth and viscosity must be positive")
    if nodes < 12:
        raise ValueError("nodes must be >= 12")
    k = float(k_per_m)
    mu = float(viscosity_pa_s)
    # Nondimensionalise z by depth and velocity by depth/mu.  Otherwise a
    # tight absolute BVP tolerance is spent resolving derivatives in m^-3.
    kd = k * float(depth_m)
    z = np.linspace(0.0, 1.0, nodes, dtype=np.float64)

    def ode(_z: FloatArray, y: FloatArray) -> FloatArray:
        result = np.empty_like(y)
        result[0] = y[1]
        result[1] = y[2]
        result[2] = y[3]
        result[3] = 2.0 * kd * kd * y[2] - kd**4 * y[0]
        return result

    def boundary(left: FloatArray, right: FloatArray) -> FloatArray:
        return np.array(
            [
                left[0],
                left[1],
                right[2] + kd * kd * right[0],
                -right[3] / (kd * kd) + 3.0 * right[1] - 1.0,
            ],
            dtype=np.float64,
        )

    guess = np.zeros((4, nodes), dtype=np.float64)
    result = solve_bvp(
        ode,
        boundary,
        z,
        guess,
        tol=tolerance,
        max_nodes=max(1000, nodes * 20),
    )
    if not result.success:
        raise RuntimeError(f"independent Stokes BVP failed: {result.message}")
    return float(result.sol(1.0)[0]) * float(depth_m) / mu


def _finite_difference_weights(nodes: FloatArray, x0: float, derivative: int) -> FloatArray:
    """Return polynomial-exact finite-difference weights at ``x0``."""

    offsets = np.asarray(nodes, dtype=np.float64) - float(x0)
    count = offsets.size
    if derivative >= count:
        raise ValueError("derivative order must be smaller than stencil size")
    matrix = np.vstack([offsets**power for power in range(count)])
    rhs = np.zeros(count, dtype=np.float64)
    rhs[derivative] = math.factorial(derivative)
    return np.linalg.solve(matrix, rhs)


@dataclass(frozen=True, slots=True)
class PlanarModeDiagnostics:
    nodes: int
    relative_residual: float


class PlanarStokesModeOperator:
    """Uniform-grid fourth-order operator used by the production test suite.

    It represents the planar S15 boundary problem only.  Vessel evolution uses
    :class:`AxisymmetricStokesOperator` in ``stokes_axisymmetric.py``.
    """

    def __init__(self, nodes: int = 96) -> None:
        if isinstance(nodes, bool) or not isinstance(nodes, int) or nodes < 12:
            raise ValueError("nodes must be an integer >= 12")
        self.nodes = nodes

    def mobility(
        self, k_per_m: float, depth_m: float, viscosity_pa_s: float
    ) -> tuple[float, PlanarModeDiagnostics]:
        if k_per_m == 0.0:
            return 0.0, PlanarModeDiagnostics(self.nodes, 0.0)
        if k_per_m < 0.0 or depth_m <= 0.0 or viscosity_pa_s <= 0.0:
            raise ValueError("require k >= 0, depth > 0 and viscosity > 0")
        k = float(k_per_m)
        mu = float(viscosity_pa_s)
        z = np.linspace(0.0, float(depth_m), self.nodes, dtype=np.float64)
        dz = float(z[1] - z[0])
        matrix = np.zeros((self.nodes, self.nodes), dtype=np.float64)
        rhs = np.zeros(self.nodes, dtype=np.float64)
        row = 0
        matrix[row, 0] = 1.0
        row += 1
        matrix[row, :5] = _finite_difference_weights(z[:5], z[0], 1)
        row += 1
        for index in range(2, self.nodes - 2):
            matrix[row, index - 2 : index + 3] = np.array(
                [1.0, -4.0, 6.0, -4.0, 1.0], dtype=np.float64
            ) / dz**4
            matrix[row, index - 1 : index + 2] += (
                -2.0 * k * k * np.array([1.0, -2.0, 1.0]) / dz**2
            )
            matrix[row, index] += k**4
            row += 1
        top_nodes = z[-6:]
        matrix[row, -6:] = _finite_difference_weights(top_nodes, z[-1], 2)
        matrix[row, -1] += k * k
        row += 1
        matrix[row, -6:] = (
            -mu / (k * k) * _finite_difference_weights(top_nodes, z[-1], 3)
            + 3.0 * mu * _finite_difference_weights(top_nodes, z[-1], 1)
        )
        rhs[row] = 1.0
        scale = max(float(np.max(np.abs(matrix))), 1.0)
        solution = np.linalg.solve(matrix / scale, rhs / scale)
        residual = matrix @ solution - rhs
        relative = float(np.linalg.norm(residual) / max(np.linalg.norm(rhs), 1.0))
        return float(solution[-1]), PlanarModeDiagnostics(self.nodes, relative)


@dataclass(frozen=True, slots=True)
class RadialNeumannModes:
    """Weighted finite-volume eigenmodes of a finite circular free surface."""

    radius_m: FloatArray
    area_weights_m2: FloatArray
    wavenumber_per_m: FloatArray
    basis: FloatArray

    @classmethod
    def build(cls, radial_cells: int, vessel_radius_m: float) -> "RadialNeumannModes":
        if radial_cells < 3 or vessel_radius_m <= 0.0:
            raise ValueError("require at least three cells and positive radius")
        dr = vessel_radius_m / radial_cells
        radius = (np.arange(radial_cells, dtype=np.float64) + 0.5) * dr
        faces = np.arange(radial_cells + 1, dtype=np.float64) * dr
        annulus = math.pi * (np.square(faces[1:]) - np.square(faces[:-1]))
        operator = np.zeros((radial_cells, radial_cells), dtype=np.float64)
        for index in range(radial_cells - 1):
            conductance = 2.0 * math.pi * faces[index + 1] / dr
            operator[index, index] -= conductance / annulus[index]
            operator[index, index + 1] += conductance / annulus[index]
            operator[index + 1, index] += conductance / annulus[index + 1]
            operator[index + 1, index + 1] -= conductance / annulus[index + 1]
        root_weight = np.sqrt(annulus)
        symmetric = -root_weight[:, None] * operator / root_weight[None, :]
        eigenvalues, eigenvectors = eigh(symmetric)
        eigenvalues = np.maximum(eigenvalues, 0.0)
        basis = eigenvectors / root_weight[:, None]
        # Give the volume mode a stable positive orientation.
        if float(np.sum(basis[:, 0] * annulus)) < 0.0:
            basis[:, 0] *= -1.0
        return cls(
            radius_m=radius,
            area_weights_m2=annulus,
            wavenumber_per_m=np.sqrt(eigenvalues),
            basis=basis,
        )

    def project(self, values: NDArray[np.floating]) -> FloatArray:
        array = np.asarray(values, dtype=np.float64)
        if array.shape != self.radius_m.shape:
            raise ValueError(f"surface field must have shape {self.radius_m.shape}")
        return self.basis.T @ (self.area_weights_m2 * array)

    def reconstruct(self, coefficients: NDArray[np.floating]) -> FloatArray:
        array = np.asarray(coefficients, dtype=np.float64)
        if array.shape != self.wavenumber_per_m.shape:
            raise ValueError(
                f"modal coefficients must have shape {self.wavenumber_per_m.shape}"
            )
        return self.basis @ array

    def remove_volume_mode(self, values: NDArray[np.floating]) -> FloatArray:
        coefficients = self.project(values)
        coefficients[0] = 0.0
        return self.reconstruct(coefficients)


__all__ = [
    "PlanarModeDiagnostics",
    "PlanarStokesModeOperator",
    "RadialNeumannModes",
    "finite_depth_mobility",
    "independent_finite_depth_mobility_bvp",
]
