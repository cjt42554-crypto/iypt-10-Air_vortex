"""Variable-density pressure projection on the masked three-dimensional grid."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import math

import numpy as np
from numpy.typing import NDArray
from scipy.linalg.lapack import dpttrf, dpttrs
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.linalg import LinearOperator, cg

from .cartesian3d import FloatArray, ImmersedCartesianGrid


_CG_USES_RTOL = "rtol" in inspect.signature(cg).parameters
_VERTICAL_LINE_SPACING_RATIO_MAX = 0.8


@dataclass(frozen=True, slots=True)
class Projection3DDiagnostics:
    divergence_before_s_inv: float
    divergence_after_s_inv: float
    pressure_relative_residual: float
    iterations: int
    active_unknowns: int
    matrix_nonzeros: int
    converged: bool


@dataclass(frozen=True, slots=True)
class _PressureMatrixTopology3D:
    """Density-independent CSR topology and coefficient destinations.

    The immersed geometry is fixed for the lifetime of a projection object.
    Consequently, pressure-matrix row/column connectivity is fixed too; only
    its density-weighted values change.  Keeping the COO-to-CSR permutation
    here avoids rebuilding and sorting almost nine hundred thousand entries on
    every default-grid time step.
    """

    x_open: NDArray[np.bool_]
    y_open: NDArray[np.bool_]
    z_open: NDArray[np.bool_]
    top_open: NDArray[np.bool_]
    x_left: NDArray[np.int64]
    x_right: NDArray[np.int64]
    y_left: NDArray[np.int64]
    y_right: NDArray[np.int64]
    z_left: NDArray[np.int64]
    z_right: NDArray[np.int64]
    top_index: NDArray[np.int64]
    csr_indices: NDArray[np.integer]
    csr_indptr: NDArray[np.integer]
    x_left_positions: NDArray[np.int64]
    x_right_positions: NDArray[np.int64]
    y_left_positions: NDArray[np.int64]
    y_right_positions: NDArray[np.int64]
    z_left_positions: NDArray[np.int64]
    z_right_positions: NDArray[np.int64]
    diagonal_positions: NDArray[np.int64]

    @property
    def nonzeros(self) -> int:
        return int(self.csr_indices.size)


def _build_pressure_matrix_topology(
    grid: ImmersedCartesianGrid,
) -> _PressureMatrixTopology3D:
    """Build the immutable pressure graph and its COO-to-CSR permutation."""

    x_open = grid.open_x[1:-1]
    y_open = grid.open_y[:, 1:-1]
    z_open = grid.open_z[:, :, 1:-1]
    top_open = grid.open_z[:, :, -1]
    x_left = grid.index[:-1][x_open]
    x_right = grid.index[1:][x_open]
    y_left = grid.index[:, :-1][y_open]
    y_right = grid.index[:, 1:][y_open]
    z_left = grid.index[:, :, :-1][z_open]
    z_right = grid.index[:, :, 1:][z_open]
    top_index = grid.index[:, :, -1][top_open]
    identity = np.arange(grid.active_cell_count, dtype=np.int64)

    rows = np.concatenate(
        (x_left, x_right, y_left, y_right, z_left, z_right, identity)
    )
    cols = np.concatenate(
        (x_right, x_left, y_right, y_left, z_right, z_left, identity)
    )
    entry_markers = np.arange(1, rows.size + 1, dtype=np.int64)
    template = coo_matrix(
        (entry_markers, (rows, cols)),
        shape=(grid.active_cell_count, grid.active_cell_count),
    ).tocsr()
    if template.nnz != rows.size:
        raise RuntimeError("pressure topology unexpectedly contains duplicate entries")
    markers_in_csr_order = np.asarray(template.data, dtype=np.int64)
    if not np.array_equal(
        np.sort(markers_in_csr_order), entry_markers
    ):
        raise RuntimeError("pressure topology lost entries during CSR conversion")
    entry_to_csr = np.empty(rows.size, dtype=np.int64)
    entry_to_csr[markers_in_csr_order - 1] = np.arange(
        rows.size, dtype=np.int64
    )

    offset = 0

    def take(count: int) -> NDArray[np.int64]:
        nonlocal offset
        positions = entry_to_csr[offset : offset + count]
        offset += count
        return positions

    x_left_positions = take(x_left.size)
    x_right_positions = take(x_right.size)
    y_left_positions = take(y_left.size)
    y_right_positions = take(y_right.size)
    z_left_positions = take(z_left.size)
    z_right_positions = take(z_right.size)
    diagonal_positions = take(identity.size)
    if offset != rows.size:
        raise RuntimeError("pressure topology coefficient map is incomplete")
    # Active-cell indices follow C-order, with z as the contiguous dimension.
    # Every open vertical face must therefore connect consecutive unknowns.
    if not np.array_equal(z_right, z_left + 1):
        raise RuntimeError("active-cell ordering does not support vertical line blocks")
    # Direct indexed accumulation is valid only because one geometric side can
    # contribute at most once to a cell.  Prove that topology invariant once,
    # rather than paying ``np.add.at`` repeated-index handling every step.
    for name, indices in (
        ("x_left", x_left),
        ("x_right", x_right),
        ("y_left", y_left),
        ("y_right", y_right),
        ("z_left", z_left),
        ("z_right", z_right),
        ("top_index", top_index),
    ):
        if np.unique(indices).size != indices.size:
            raise RuntimeError(f"pressure topology {name} contains duplicate cells")

    return _PressureMatrixTopology3D(
        x_open=x_open,
        y_open=y_open,
        z_open=z_open,
        top_open=top_open,
        x_left=x_left,
        x_right=x_right,
        y_left=y_left,
        y_right=y_right,
        z_left=z_left,
        z_right=z_right,
        top_index=top_index,
        csr_indices=template.indices.copy(),
        csr_indptr=template.indptr.copy(),
        x_left_positions=x_left_positions,
        x_right_positions=x_right_positions,
        y_left_positions=y_left_positions,
        y_right_positions=y_right_positions,
        z_left_positions=z_left_positions,
        z_right_positions=z_right_positions,
        diagonal_positions=diagonal_positions,
    )


def _harmonic_inverse_density(left: FloatArray, right: FloatArray) -> FloatArray:
    """Return 2/(rho_left+rho_right), the harmonic mean of 1/rho."""

    return 2.0 / (left + right)


class VariableDensityProjection3D:
    """MAC projection for ``div(u)=0`` with an atmospheric pressure top.

    Cylinder, floor and bar faces are impermeable.  The top uses a zero-gauge
    Dirichlet pressure at half a cell from the top cell centre, so the Poisson
    operator is symmetric positive definite without an arbitrary gauge row.
    Density is cell centred and face inverse density is harmonic.
    """

    def __init__(
        self,
        grid: ImmersedCartesianGrid,
        *,
        relative_tolerance: float,
        maximum_iterations: int,
    ) -> None:
        if not math.isfinite(relative_tolerance) or relative_tolerance <= 0.0:
            raise ValueError("relative_tolerance must be finite and positive")
        if (
            isinstance(maximum_iterations, bool)
            or not isinstance(maximum_iterations, int)
            or maximum_iterations < 1
        ):
            raise ValueError("maximum_iterations must be an integer >= 1")
        self.grid = grid
        self.relative_tolerance = float(relative_tolerance)
        self.maximum_iterations = maximum_iterations
        self.solve_count = 0
        self.initial_guess_extrapolation_count = 0
        self.last_initial_guess_extrapolation_factor = 0.0
        self._previous_pressure_solution: FloatArray | None = None
        self._last_pressure_solution: FloatArray | None = None
        self._topology = _build_pressure_matrix_topology(grid)
        matrix_data = np.empty(self._topology.nonzeros, dtype=np.float64)
        self._matrix = csr_matrix(
            (
                matrix_data,
                self._topology.csr_indices,
                self._topology.csr_indptr,
            ),
            shape=(grid.active_cell_count, grid.active_cell_count),
            copy=False,
        )
        # The topology came from a canonical CSR template.  Setting these
        # flags prevents redundant sortedness checks inside sparse matvecs.
        self._matrix.has_sorted_indices = True
        self._matrix.has_canonical_format = True
        self._line_diagonal = np.empty(grid.active_cell_count, dtype=np.float64)
        self._line_off_diagonal = np.zeros(
            max(0, grid.active_cell_count - 1), dtype=np.float64
        )
        # These arrays have fixed shapes and are overwritten before every use.
        # Reusing them removes four full-grid allocations per pressure solve.
        self._beta_x = np.zeros(grid.face_shape_x, dtype=np.float64)
        self._beta_y = np.zeros(grid.face_shape_y, dtype=np.float64)
        self._beta_z = np.zeros(grid.face_shape_z, dtype=np.float64)
        self._diagonal = np.empty(grid.active_cell_count, dtype=np.float64)
        self._use_vertical_line_preconditioner = (
            grid.dz
            <= _VERTICAL_LINE_SPACING_RATIO_MAX * min(grid.dx, grid.dy)
        )
        self.preconditioner_name = (
            "vertical-line block-Jacobi"
            if self._use_vertical_line_preconditioner
            else "point-Jacobi"
        )
        self._line_factor_ready = False
        self._preconditioner = LinearOperator(
            self._matrix.shape,
            matvec=(
                self._apply_vertical_line_preconditioner
                if self._use_vertical_line_preconditioner
                else self._apply_point_jacobi_preconditioner
            ),
            dtype=np.float64,
        )

    def _factor_vertical_line_preconditioner(self) -> None:
        """Factor the SPD block-Jacobi matrix made of vertical grid lines."""

        if not self._use_vertical_line_preconditioner:
            return
        diagonal, off_diagonal, info = dpttrf(
            self._line_diagonal,
            self._line_off_diagonal,
            overwrite_d=True,
            overwrite_e=True,
        )
        if info != 0:
            self._line_factor_ready = False
            raise FloatingPointError(
                "vertical-line pressure preconditioner is not positive definite: "
                f"leading_minor={info}"
            )
        self._line_diagonal = diagonal
        self._line_off_diagonal = off_diagonal
        self._line_factor_ready = True

    def _apply_point_jacobi_preconditioner(
        self, value: NDArray[np.floating]
    ) -> FloatArray:
        return np.asarray(value, dtype=np.float64) / self._line_diagonal

    def _apply_vertical_line_preconditioner(
        self, value: NDArray[np.floating]
    ) -> FloatArray:
        if not self._line_factor_ready:
            raise RuntimeError("vertical-line pressure preconditioner is not factored")
        rhs = np.asarray(value, dtype=np.float64).reshape(-1, 1)
        solution, info = dpttrs(
            self._line_diagonal,
            self._line_off_diagonal,
            rhs,
            overwrite_b=False,
        )
        if info != 0:
            raise FloatingPointError(
                "vertical-line pressure preconditioner solve failed: "
                f"info={info}"
            )
        return solution[:, 0]

    def _history_initial_guess(
        self,
        matrix: csr_matrix,
        rhs: FloatArray,
        initial: FloatArray,
        *,
        contiguous_history: bool,
    ) -> FloatArray:
        """Return a residual-minimising pressure-history extrapolation.

        The search is restricted to ``initial + alpha * (p_n-p_{n-1})`` and
        minimises the *current, unchanged* linear-system residual.  The clipped
        interval contains alpha=0, and the candidate is accepted only after an
        explicit residual reduction, so an abrupt forcing or time-step change
        cannot force use of a worse history guess.
        """

        self.last_initial_guess_extrapolation_factor = 0.0
        previous = self._previous_pressure_solution
        latest = self._last_pressure_solution
        if not contiguous_history or previous is None or latest is None:
            return initial
        direction = latest - previous
        if not np.any(direction):
            return initial
        base_residual = rhs - matrix @ initial
        matrix_direction = matrix @ direction
        denominator = float(np.dot(matrix_direction, matrix_direction))
        if not math.isfinite(denominator) or denominator <= 0.0:
            return initial
        numerator = float(np.dot(base_residual, matrix_direction))
        if not math.isfinite(numerator):
            return initial
        factor = float(np.clip(numerator / denominator, -1.0, 2.0))
        candidate_residual = base_residual - factor * matrix_direction
        base_norm_squared = float(np.dot(base_residual, base_residual))
        candidate_norm_squared = float(
            np.dot(candidate_residual, candidate_residual)
        )
        if (
            not math.isfinite(candidate_norm_squared)
            or candidate_norm_squared >= base_norm_squared
        ):
            return initial
        self.initial_guess_extrapolation_count += 1
        self.last_initial_guess_extrapolation_factor = factor
        return initial + factor * direction

    def _remember_pressure_solution(
        self, solution: FloatArray, *, contiguous_history: bool
    ) -> None:
        if contiguous_history:
            self._previous_pressure_solution = self._last_pressure_solution
        else:
            self._previous_pressure_solution = None
        self._last_pressure_solution = np.asarray(
            solution, dtype=np.float64
        ).copy()

    def _matrix_and_face_beta(
        self, density_kg_m3: NDArray[np.floating]
    ) -> tuple[csr_matrix, FloatArray, FloatArray, FloatArray]:
        g = self.grid
        g.require_cell_field(density_kg_m3, "density_kg_m3")
        density = np.asarray(density_kg_m3, dtype=np.float64)
        if not np.isfinite(density[g.active_mask]).all() or np.any(
            density[g.active_mask] <= 0.0
        ):
            raise ValueError("active-cell density must be finite and positive")
        topology = self._topology
        beta_x = self._beta_x
        beta_y = self._beta_y
        beta_z = self._beta_z
        beta_x.fill(0.0)
        beta_y.fill(0.0)
        beta_z.fill(0.0)
        x_beta = _harmonic_inverse_density(
            density[:-1][topology.x_open], density[1:][topology.x_open]
        )
        y_beta = _harmonic_inverse_density(
            density[:, :-1][topology.y_open],
            density[:, 1:][topology.y_open],
        )
        z_beta = _harmonic_inverse_density(
            density[:, :, :-1][topology.z_open],
            density[:, :, 1:][topology.z_open],
        )
        beta_x[1:-1][topology.x_open] = x_beta
        beta_y[:, 1:-1][topology.y_open] = y_beta
        beta_z[:, :, 1:-1][topology.z_open] = z_beta
        # The external headspace is the configured gas; using the top-cell
        # density is the consistent one-sided limit when that cell is pure gas.
        top_beta = 1.0 / density[:, :, -1][topology.top_open]
        beta_z[:, :, -1][topology.top_open] = top_beta

        x_coefficient = x_beta * (g.dy * g.dz / g.dx)
        y_coefficient = y_beta * (g.dx * g.dz / g.dy)
        z_coefficient = z_beta * (g.dx * g.dy / g.dz)
        top_coefficient = top_beta * (g.dx * g.dy / (0.5 * g.dz))
        diagonal = self._diagonal
        diagonal.fill(0.0)
        # Each list represents one geometric face direction, so an active cell
        # occurs at most once in each list.  Direct indexed addition is exactly
        # equivalent to ``np.add.at`` here and avoids its repeated-index
        # synchronisation machinery.
        diagonal[topology.x_left] += x_coefficient
        diagonal[topology.x_right] += x_coefficient
        diagonal[topology.y_left] += y_coefficient
        diagonal[topology.y_right] += y_coefficient
        diagonal[topology.z_left] += z_coefficient
        diagonal[topology.z_right] += z_coefficient
        diagonal[topology.top_index] += top_coefficient
        if np.any(diagonal <= 0.0):
            raise FloatingPointError("pressure matrix has a non-positive diagonal")

        data = self._matrix.data
        data[topology.x_left_positions] = -x_coefficient
        data[topology.x_right_positions] = -x_coefficient
        data[topology.y_left_positions] = -y_coefficient
        data[topology.y_right_positions] = -y_coefficient
        data[topology.z_left_positions] = -z_coefficient
        data[topology.z_right_positions] = -z_coefficient
        data[topology.diagonal_positions] = diagonal

        # The vertical-line block diagonal is a principal block of the SPD
        # pressure matrix, hence is itself SPD and valid for preconditioned CG.
        # In active C-order each vertical neighbour occupies the first matrix
        # off-diagonal; zeros naturally separate different columns and bar gaps.
        self._line_diagonal[:] = diagonal
        self._line_off_diagonal.fill(0.0)
        self._line_off_diagonal[topology.z_left] = -z_coefficient
        self._line_factor_ready = False
        return self._matrix, beta_x, beta_y, beta_z

    def project(
        self,
        u_x: NDArray[np.floating],
        u_y: NDArray[np.floating],
        u_z: NDArray[np.floating],
        density_kg_m3: NDArray[np.floating],
        dt_s: float,
        *,
        initial_pressure_pa: NDArray[np.floating] | None = None,
    ) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, Projection3DDiagnostics]:
        g = self.grid
        g.require_face_velocity(u_x, u_y, u_z)
        if not math.isfinite(dt_s) or dt_s <= 0.0:
            raise ValueError("dt_s must be finite and positive")
        matrix, beta_x, beta_y, beta_z = self._matrix_and_face_beta(
            density_kg_m3
        )
        ux = np.where(g.open_x, np.asarray(u_x, dtype=np.float64), 0.0)
        uy = np.where(g.open_y, np.asarray(u_y, dtype=np.float64), 0.0)
        uz = np.where(g.open_z, np.asarray(u_z, dtype=np.float64), 0.0)
        divergence = g.divergence(ux, uy, uz)
        before = float(np.max(np.abs(divergence[g.active_mask])))
        rhs = -divergence[g.active_mask] * g.cell_volume_m3 / dt_s
        rhs_norm = float(np.linalg.norm(rhs))
        pressure = np.zeros(g.shape, dtype=np.float64)
        contiguous_history = False
        if (
            initial_pressure_pa is not None
            and self._last_pressure_solution is not None
            and np.shape(initial_pressure_pa) == g.shape
        ):
            supplied_initial = np.asarray(initial_pressure_pa, dtype=np.float64)[
                g.active_mask
            ]
            contiguous_history = np.array_equal(
                supplied_initial, self._last_pressure_solution
            )
        if rhs_norm <= 64.0 * np.finfo(float).tiny:
            self._remember_pressure_solution(
                pressure[g.active_mask], contiguous_history=contiguous_history
            )
            diagnostics = Projection3DDiagnostics(
                divergence_before_s_inv=before,
                divergence_after_s_inv=before,
                pressure_relative_residual=0.0,
                iterations=0,
                active_unknowns=g.active_cell_count,
                matrix_nonzeros=int(matrix.nnz),
                converged=True,
            )
            return ux, uy, uz, pressure, diagnostics

        if initial_pressure_pa is None:
            initial = np.zeros(g.active_cell_count, dtype=np.float64)
        else:
            g.require_cell_field(initial_pressure_pa, "initial_pressure_pa")
            initial = np.asarray(initial_pressure_pa, dtype=np.float64)[
                g.active_mask
            ].copy()
            if not np.isfinite(initial).all():
                raise ValueError("initial pressure must be finite")
        initial = self._history_initial_guess(
            matrix,
            rhs,
            initial,
            contiguous_history=contiguous_history,
        )
        self._factor_vertical_line_preconditioner()
        iterations = 0

        def callback(_: NDArray[np.floating]) -> None:
            nonlocal iterations
            iterations += 1

        cg_arguments = {
            "x0": initial,
            "maxiter": self.maximum_iterations,
            "M": self._preconditioner,
            "callback": callback,
            "atol": 0.0,
        }
        if _CG_USES_RTOL:
            solution, info = cg(
                matrix,
                rhs,
                rtol=self.relative_tolerance,
                **cg_arguments,
            )
        else:
            solution, info = cg(
                matrix,
                rhs,
                tol=self.relative_tolerance,
                **cg_arguments,
            )
        self.solve_count += 1
        residual = matrix @ solution - rhs
        relative_residual = float(np.linalg.norm(residual) / rhs_norm)
        if info != 0 or not np.isfinite(solution).all():
            raise FloatingPointError(
                "variable-density pressure projection failed to converge: "
                f"info={info}, iterations={iterations}, "
                f"relative_residual={relative_residual:.3e}"
            )
        self._remember_pressure_solution(
            solution, contiguous_history=contiguous_history
        )
        pressure[g.active_mask] = solution

        internal = g.open_x[1:-1]
        gradient = (pressure[1:] - pressure[:-1]) / g.dx
        ux[1:-1][internal] -= dt_s * beta_x[1:-1][internal] * gradient[internal]
        internal = g.open_y[:, 1:-1]
        gradient = (pressure[:, 1:] - pressure[:, :-1]) / g.dy
        uy[:, 1:-1][internal] -= (
            dt_s * beta_y[:, 1:-1][internal] * gradient[internal]
        )
        internal = g.open_z[:, :, 1:-1]
        gradient = (pressure[:, :, 1:] - pressure[:, :, :-1]) / g.dz
        uz[:, :, 1:-1][internal] -= (
            dt_s * beta_z[:, :, 1:-1][internal] * gradient[internal]
        )
        top = g.open_z[:, :, -1]
        top_gradient = -pressure[:, :, -1] / (0.5 * g.dz)
        uz[:, :, -1][top] -= (
            dt_s * beta_z[:, :, -1][top] * top_gradient[top]
        )
        ux *= g.open_x
        uy *= g.open_y
        uz *= g.open_z
        after_field = g.divergence(ux, uy, uz)
        after = float(np.max(np.abs(after_field[g.active_mask])))
        diagnostics = Projection3DDiagnostics(
            divergence_before_s_inv=before,
            divergence_after_s_inv=after,
            pressure_relative_residual=relative_residual,
            iterations=iterations,
            active_unknowns=g.active_cell_count,
            matrix_nonzeros=int(matrix.nnz),
            converged=True,
        )
        return ux, uy, uz, pressure, diagnostics


__all__ = ["Projection3DDiagnostics", "VariableDensityProjection3D"]
