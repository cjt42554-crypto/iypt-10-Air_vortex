"""Optional FP64 CUDA pressure projection for the HF3D discrete operator.

CuPy is imported lazily so the reference CPU backend and the ordinary test
suite remain usable without any CUDA packages.  The device entry point accepts
and returns GPU-resident arrays; the convenience ``project`` wrapper performs
host transfers only for parity tests and small standalone probes.  The HF3D
time integrator must not use that wrapper in its hot loop.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .cartesian3d import ImmersedCartesianGrid
from .projection3d import (
    Projection3DDiagnostics,
    _VERTICAL_LINE_SPACING_RATIO_MAX,
    _build_pressure_matrix_topology,
)


class GPUProjectionUnavailable(RuntimeError):
    """Raised when the optional CUDA projection cannot be constructed."""


@dataclass(frozen=True, slots=True)
class GPUProjectionProbe:
    available: bool
    detail: str
    device_name: str | None = None
    compute_capability: str | None = None
    total_memory_bytes: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "detail": self.detail,
            "device_name": self.device_name,
            "compute_capability": self.compute_capability,
            "total_memory_bytes": self.total_memory_bytes,
        }


def _load_cupy() -> tuple[Any, Any, Any]:
    try:
        import cupy as cp
        from cupyx.scipy import sparse as cupy_sparse
        from cupyx.scipy.sparse import linalg as cupy_sparse_linalg
    except (ImportError, OSError) as exc:
        raise GPUProjectionUnavailable(
            "CuPy CUDA runtime is unavailable; install the optional gpu dependencies "
            "in an isolated environment"
        ) from exc
    return cp, cupy_sparse, cupy_sparse_linalg


def probe_gpu_projection(device_id: int = 0) -> GPUProjectionProbe:
    """Return a non-throwing CUDA/CuPy availability record."""

    try:
        cp, _, _ = _load_cupy()
        count = int(cp.cuda.runtime.getDeviceCount())
        if count <= device_id:
            return GPUProjectionProbe(
                False,
                f"CUDA device {device_id} is unavailable; visible device count is {count}",
            )
        properties = cp.cuda.runtime.getDeviceProperties(device_id)
        raw_name = properties.get("name", b"unknown")
        name = raw_name.decode(errors="replace") if isinstance(raw_name, bytes) else str(raw_name)
        major = int(properties.get("major", 0))
        minor = int(properties.get("minor", 0))
        total_memory = int(properties.get("totalGlobalMem", 0))
        # Force creation of a context and one real device operation.  Merely
        # importing CuPy does not prove that the runtime and driver cooperate.
        with cp.cuda.Device(device_id):
            value = cp.asarray([1.0], dtype=cp.float64)
            if float(cp.sum(value).item()) != 1.0:
                raise RuntimeError("CUDA FP64 smoke reduction returned an invalid value")
        return GPUProjectionProbe(
            True,
            "CuPy FP64 device operation passed",
            device_name=name,
            compute_capability=f"{major}.{minor}",
            total_memory_bytes=total_memory,
        )
    except Exception as exc:  # availability probes must be fail-closed
        return GPUProjectionProbe(False, f"{type(exc).__name__}: {exc}")


def _vertical_active_segments(
    grid: ImmersedCartesianGrid,
) -> tuple[NDArray[np.int64], NDArray[np.int32]]:
    """Return contiguous active-vector segments for independent vertical lines."""

    starts: list[int] = []
    lengths: list[int] = []
    for i in range(grid.nx):
        for j in range(grid.ny):
            active = grid.active_mask[i, j]
            k = 0
            while k < grid.nz:
                while k < grid.nz and not active[k]:
                    k += 1
                if k >= grid.nz:
                    break
                first = k
                while k < grid.nz and active[k]:
                    k += 1
                start = int(grid.index[i, j, first])
                length = k - first
                if start < 0 or length < 1:
                    raise RuntimeError("invalid active vertical pressure segment")
                starts.append(start)
                lengths.append(length)
    starts_array = np.asarray(starts, dtype=np.int64)
    lengths_array = np.asarray(lengths, dtype=np.int32)
    if int(np.sum(lengths_array, dtype=np.int64)) != grid.active_cell_count:
        raise RuntimeError("vertical pressure segments do not cover every active cell")
    return starts_array, lengths_array


_LINE_KERNEL_SOURCE = r"""
extern "C" __global__
void factor_vertical_lines(
    const double* diagonal,
    const double* off_diagonal,
    const long long* starts,
    const int* lengths,
    double* factor_diagonal,
    double* factor_lower,
    const int segment_count)
{
    const int segment = blockDim.x * blockIdx.x + threadIdx.x;
    if (segment >= segment_count) return;
    const long long start = starts[segment];
    const int length = lengths[segment];
    factor_diagonal[start] = diagonal[start];
    for (int local = 1; local < length; ++local) {
        const long long left = start + local - 1;
        const double lower = off_diagonal[left] / factor_diagonal[left];
        factor_lower[left] = lower;
        factor_diagonal[left + 1] = diagonal[left + 1] - lower * off_diagonal[left];
    }
}

extern "C" __global__
void solve_vertical_lines(
    double* values,
    const double* factor_diagonal,
    const double* factor_lower,
    const long long* starts,
    const int* lengths,
    const int segment_count)
{
    const int segment = blockDim.x * blockIdx.x + threadIdx.x;
    if (segment >= segment_count) return;
    const long long start = starts[segment];
    const int length = lengths[segment];
    for (int local = 1; local < length; ++local) {
        const long long current = start + local;
        values[current] -= factor_lower[current - 1] * values[current - 1];
    }
    for (int local = 0; local < length; ++local) {
        const long long current = start + local;
        values[current] /= factor_diagonal[current];
    }
    for (int local = length - 2; local >= 0; --local) {
        const long long current = start + local;
        values[current] -= factor_lower[current] * values[current + 1];
    }
}
"""


class CuPyVariableDensityProjection3D:
    """GPU-resident FP64 implementation of the CPU HF3D pressure projection."""

    def __init__(
        self,
        grid: ImmersedCartesianGrid,
        *,
        relative_tolerance: float,
        maximum_iterations: int,
        device_id: int = 0,
    ) -> None:
        if not math.isfinite(relative_tolerance) or relative_tolerance <= 0.0:
            raise ValueError("relative_tolerance must be finite and positive")
        if (
            isinstance(maximum_iterations, bool)
            or not isinstance(maximum_iterations, int)
            or maximum_iterations < 1
        ):
            raise ValueError("maximum_iterations must be an integer >= 1")
        self.cp, cupy_sparse, cupy_sparse_linalg = _load_cupy()
        self.device_id = int(device_id)
        try:
            self._device = self.cp.cuda.Device(self.device_id)
            self._device.use()
        except Exception as exc:
            raise GPUProjectionUnavailable(
                f"CUDA device {self.device_id} could not be selected: {exc}"
            ) from exc
        self.grid = grid
        self.relative_tolerance = float(relative_tolerance)
        self.maximum_iterations = maximum_iterations
        self.solve_count = 0
        self.initial_guess_extrapolation_count = 0
        self.last_initial_guess_extrapolation_factor = 0.0
        self._previous_pressure_solution: Any | None = None
        self._last_pressure_solution: Any | None = None
        self._topology = _build_pressure_matrix_topology(grid)
        cp = self.cp
        topology = self._topology
        self._active_mask = cp.asarray(grid.active_mask)
        self._open_x = cp.asarray(grid.open_x)
        self._open_y = cp.asarray(grid.open_y)
        self._open_z = cp.asarray(grid.open_z)
        self._x_open = cp.asarray(topology.x_open)
        self._y_open = cp.asarray(topology.y_open)
        self._z_open = cp.asarray(topology.z_open)
        self._top_open = cp.asarray(topology.top_open)
        for name in (
            "x_left",
            "x_right",
            "y_left",
            "y_right",
            "z_left",
            "z_right",
            "top_index",
            "x_left_positions",
            "x_right_positions",
            "y_left_positions",
            "y_right_positions",
            "z_left_positions",
            "z_right_positions",
            "diagonal_positions",
        ):
            setattr(self, f"_{name}", cp.asarray(getattr(topology, name)))
        self._matrix_data = cp.empty(topology.nonzeros, dtype=cp.float64)
        self._matrix = cupy_sparse.csr_matrix(
            (
                self._matrix_data,
                cp.asarray(topology.csr_indices),
                cp.asarray(topology.csr_indptr),
            ),
            shape=(grid.active_cell_count, grid.active_cell_count),
            copy=False,
        )
        self._beta_x = cp.zeros(grid.face_shape_x, dtype=cp.float64)
        self._beta_y = cp.zeros(grid.face_shape_y, dtype=cp.float64)
        self._beta_z = cp.zeros(grid.face_shape_z, dtype=cp.float64)
        self._diagonal = cp.empty(grid.active_cell_count, dtype=cp.float64)
        self._line_off_diagonal = cp.zeros(
            max(0, grid.active_cell_count - 1), dtype=cp.float64
        )
        self._use_vertical_line_preconditioner = (
            grid.dz <= _VERTICAL_LINE_SPACING_RATIO_MAX * min(grid.dx, grid.dy)
        )
        self.preconditioner_name = (
            "CUDA batched vertical-line block-Jacobi"
            if self._use_vertical_line_preconditioner
            else "CUDA point-Jacobi"
        )
        self._line_factor_ready = False
        if self._use_vertical_line_preconditioner:
            starts, lengths = _vertical_active_segments(grid)
            self._segment_starts = cp.asarray(starts)
            self._segment_lengths = cp.asarray(lengths)
            self._segment_count = int(starts.size)
            self._factor_diagonal = cp.empty(
                grid.active_cell_count, dtype=cp.float64
            )
            self._factor_lower = cp.zeros(
                max(0, grid.active_cell_count - 1), dtype=cp.float64
            )
            self._factor_kernel = cp.RawKernel(
                _LINE_KERNEL_SOURCE, "factor_vertical_lines"
            )
            self._solve_kernel = cp.RawKernel(
                _LINE_KERNEL_SOURCE, "solve_vertical_lines"
            )
            preconditioner_matvec = self._apply_vertical_line_preconditioner
        else:
            self._segment_count = 0
            preconditioner_matvec = self._apply_point_jacobi_preconditioner
        self._preconditioner = cupy_sparse_linalg.LinearOperator(
            self._matrix.shape,
            matvec=preconditioner_matvec,
            dtype=cp.float64,
        )
        self._cupy_sparse_linalg = cupy_sparse_linalg

    def _require_shape(self, value: Any, expected: tuple[int, ...], name: str) -> None:
        if tuple(value.shape) != expected:
            raise ValueError(f"{name} must have shape {expected}, got {tuple(value.shape)}")

    def _divergence(self, u_x: Any, u_y: Any, u_z: Any) -> Any:
        cp = self.cp
        ux = cp.where(self._open_x, u_x, 0.0)
        uy = cp.where(self._open_y, u_y, 0.0)
        uz = cp.where(self._open_z, u_z, 0.0)
        result = (
            (ux[1:] - ux[:-1]) / self.grid.dx
            + (uy[:, 1:] - uy[:, :-1]) / self.grid.dy
            + (uz[:, :, 1:] - uz[:, :, :-1]) / self.grid.dz
        )
        return cp.where(self._active_mask, result, 0.0)

    def _matrix_and_face_beta_device(self, density_kg_m3: Any) -> tuple[Any, Any, Any, Any]:
        cp = self.cp
        g = self.grid
        density = cp.asarray(density_kg_m3, dtype=cp.float64)
        self._require_shape(density, g.shape, "density_kg_m3")
        active_density = density[self._active_mask]
        if not bool(cp.all(cp.isfinite(active_density)).item()) or bool(
            cp.any(active_density <= 0.0).item()
        ):
            raise ValueError("active-cell density must be finite and positive")
        beta_x = self._beta_x
        beta_y = self._beta_y
        beta_z = self._beta_z
        beta_x.fill(0.0)
        beta_y.fill(0.0)
        beta_z.fill(0.0)
        x_beta = 2.0 / (
            density[:-1][self._x_open] + density[1:][self._x_open]
        )
        y_beta = 2.0 / (
            density[:, :-1][self._y_open] + density[:, 1:][self._y_open]
        )
        z_beta = 2.0 / (
            density[:, :, :-1][self._z_open]
            + density[:, :, 1:][self._z_open]
        )
        beta_x[1:-1][self._x_open] = x_beta
        beta_y[:, 1:-1][self._y_open] = y_beta
        beta_z[:, :, 1:-1][self._z_open] = z_beta
        top_beta = 1.0 / density[:, :, -1][self._top_open]
        beta_z[:, :, -1][self._top_open] = top_beta

        x_coefficient = x_beta * (g.dy * g.dz / g.dx)
        y_coefficient = y_beta * (g.dx * g.dz / g.dy)
        z_coefficient = z_beta * (g.dx * g.dy / g.dz)
        top_coefficient = top_beta * (g.dx * g.dy / (0.5 * g.dz))
        diagonal = self._diagonal
        diagonal.fill(0.0)
        diagonal[self._x_left] += x_coefficient
        diagonal[self._x_right] += x_coefficient
        diagonal[self._y_left] += y_coefficient
        diagonal[self._y_right] += y_coefficient
        diagonal[self._z_left] += z_coefficient
        diagonal[self._z_right] += z_coefficient
        diagonal[self._top_index] += top_coefficient
        if bool(cp.any(diagonal <= 0.0).item()):
            raise FloatingPointError("pressure matrix has a non-positive diagonal")

        data = self._matrix_data
        data[self._x_left_positions] = -x_coefficient
        data[self._x_right_positions] = -x_coefficient
        data[self._y_left_positions] = -y_coefficient
        data[self._y_right_positions] = -y_coefficient
        data[self._z_left_positions] = -z_coefficient
        data[self._z_right_positions] = -z_coefficient
        data[self._diagonal_positions] = diagonal
        self._line_off_diagonal.fill(0.0)
        self._line_off_diagonal[self._z_left] = -z_coefficient
        self._line_factor_ready = False
        return self._matrix, beta_x, beta_y, beta_z

    def _factor_vertical_line_preconditioner(self) -> None:
        if not self._use_vertical_line_preconditioner:
            return
        threads = 128
        blocks = (self._segment_count + threads - 1) // threads
        self._factor_kernel(
            (blocks,),
            (threads,),
            (
                self._diagonal,
                self._line_off_diagonal,
                self._segment_starts,
                self._segment_lengths,
                self._factor_diagonal,
                self._factor_lower,
                np.int32(self._segment_count),
            ),
        )
        minimum = float(self.cp.min(self._factor_diagonal).item())
        if not math.isfinite(minimum) or minimum <= 0.0:
            self._line_factor_ready = False
            raise FloatingPointError(
                "CUDA vertical-line pressure preconditioner is not positive definite"
            )
        self._line_factor_ready = True

    def _apply_point_jacobi_preconditioner(self, value: Any) -> Any:
        return self.cp.asarray(value, dtype=self.cp.float64) / self._diagonal

    def _apply_vertical_line_preconditioner(self, value: Any) -> Any:
        if not self._line_factor_ready:
            raise RuntimeError("CUDA vertical-line pressure preconditioner is not factored")
        result = self.cp.asarray(value, dtype=self.cp.float64).reshape(-1).copy()
        threads = 128
        blocks = (self._segment_count + threads - 1) // threads
        self._solve_kernel(
            (blocks,),
            (threads,),
            (
                result,
                self._factor_diagonal,
                self._factor_lower,
                self._segment_starts,
                self._segment_lengths,
                np.int32(self._segment_count),
            ),
        )
        return result

    def _history_initial_guess(
        self,
        matrix: Any,
        rhs: Any,
        initial: Any,
        *,
        contiguous_history: bool,
    ) -> Any:
        cp = self.cp
        self.last_initial_guess_extrapolation_factor = 0.0
        previous = self._previous_pressure_solution
        latest = self._last_pressure_solution
        if not contiguous_history or previous is None or latest is None:
            return initial
        direction = latest - previous
        if not bool(cp.any(direction).item()):
            return initial
        base_residual = rhs - matrix @ initial
        matrix_direction = matrix @ direction
        denominator = float(cp.dot(matrix_direction, matrix_direction).item())
        if not math.isfinite(denominator) or denominator <= 0.0:
            return initial
        numerator = float(cp.dot(base_residual, matrix_direction).item())
        if not math.isfinite(numerator):
            return initial
        factor = float(np.clip(numerator / denominator, -1.0, 2.0))
        candidate_residual = base_residual - factor * matrix_direction
        base_norm_squared = float(cp.dot(base_residual, base_residual).item())
        candidate_norm_squared = float(
            cp.dot(candidate_residual, candidate_residual).item()
        )
        if (
            not math.isfinite(candidate_norm_squared)
            or candidate_norm_squared >= base_norm_squared
        ):
            return initial
        self.initial_guess_extrapolation_count += 1
        self.last_initial_guess_extrapolation_factor = factor
        return initial + factor * direction

    def _remember_pressure_solution(self, solution: Any, *, contiguous_history: bool) -> None:
        if contiguous_history:
            self._previous_pressure_solution = self._last_pressure_solution
        else:
            self._previous_pressure_solution = None
        self._last_pressure_solution = solution.copy()

    def project_device(
        self,
        u_x: Any,
        u_y: Any,
        u_z: Any,
        density_kg_m3: Any,
        dt_s: float,
        *,
        initial_pressure_pa: Any | None = None,
    ) -> tuple[Any, Any, Any, Any, Projection3DDiagnostics]:
        """Project GPU-resident face velocities without host field transfers."""

        cp = self.cp
        g = self.grid
        if not math.isfinite(dt_s) or dt_s <= 0.0:
            raise ValueError("dt_s must be finite and positive")
        ux_input = cp.asarray(u_x, dtype=cp.float64)
        uy_input = cp.asarray(u_y, dtype=cp.float64)
        uz_input = cp.asarray(u_z, dtype=cp.float64)
        self._require_shape(ux_input, g.face_shape_x, "u_x")
        self._require_shape(uy_input, g.face_shape_y, "u_y")
        self._require_shape(uz_input, g.face_shape_z, "u_z")
        matrix, beta_x, beta_y, beta_z = self._matrix_and_face_beta_device(
            density_kg_m3
        )
        ux = cp.where(self._open_x, ux_input, 0.0)
        uy = cp.where(self._open_y, uy_input, 0.0)
        uz = cp.where(self._open_z, uz_input, 0.0)
        divergence = self._divergence(ux, uy, uz)
        before = float(cp.max(cp.abs(divergence[self._active_mask])).item())
        rhs = -divergence[self._active_mask] * g.cell_volume_m3 / dt_s
        rhs_norm = float(cp.linalg.norm(rhs).item())
        pressure = cp.zeros(g.shape, dtype=cp.float64)
        contiguous_history = False
        if initial_pressure_pa is not None and self._last_pressure_solution is not None:
            supplied = cp.asarray(initial_pressure_pa, dtype=cp.float64)
            self._require_shape(supplied, g.shape, "initial_pressure_pa")
            supplied_active = supplied[self._active_mask]
            contiguous_history = bool(
                cp.array_equal(supplied_active, self._last_pressure_solution).item()
            )
        if rhs_norm <= 64.0 * np.finfo(float).tiny:
            self._remember_pressure_solution(
                pressure[self._active_mask], contiguous_history=contiguous_history
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
            initial = cp.zeros(g.active_cell_count, dtype=cp.float64)
        else:
            initial_full = cp.asarray(initial_pressure_pa, dtype=cp.float64)
            self._require_shape(initial_full, g.shape, "initial_pressure_pa")
            initial = initial_full[self._active_mask].copy()
            if not bool(cp.all(cp.isfinite(initial)).item()):
                raise ValueError("initial pressure must be finite")
        initial = self._history_initial_guess(
            matrix,
            rhs,
            initial,
            contiguous_history=contiguous_history,
        )
        self._factor_vertical_line_preconditioner()
        iterations = 0

        def callback(_: Any) -> None:
            nonlocal iterations
            iterations += 1

        solution, info = self._cupy_sparse_linalg.cg(
            matrix,
            rhs,
            x0=initial,
            rtol=self.relative_tolerance,
            atol=0.0,
            maxiter=self.maximum_iterations,
            M=self._preconditioner,
            callback=callback,
        )
        self.solve_count += 1
        residual = matrix @ solution - rhs
        relative_residual = float(cp.linalg.norm(residual).item() / rhs_norm)
        if int(info) != 0 or not bool(cp.all(cp.isfinite(solution)).item()):
            raise FloatingPointError(
                "CUDA variable-density pressure projection failed to converge: "
                f"info={int(info)}, iterations={iterations}, "
                f"relative_residual={relative_residual:.3e}"
            )
        self._remember_pressure_solution(
            solution, contiguous_history=contiguous_history
        )
        pressure[self._active_mask] = solution

        internal_x = ux[1:-1]
        mask_x = self._open_x[1:-1]
        gradient_x = (pressure[1:] - pressure[:-1]) / g.dx
        internal_x[mask_x] -= dt_s * beta_x[1:-1][mask_x] * gradient_x[mask_x]
        internal_y = uy[:, 1:-1]
        mask_y = self._open_y[:, 1:-1]
        gradient_y = (pressure[:, 1:] - pressure[:, :-1]) / g.dy
        internal_y[mask_y] -= (
            dt_s * beta_y[:, 1:-1][mask_y] * gradient_y[mask_y]
        )
        internal_z = uz[:, :, 1:-1]
        mask_z = self._open_z[:, :, 1:-1]
        gradient_z = (pressure[:, :, 1:] - pressure[:, :, :-1]) / g.dz
        internal_z[mask_z] -= (
            dt_s * beta_z[:, :, 1:-1][mask_z] * gradient_z[mask_z]
        )
        top = self._open_z[:, :, -1]
        top_gradient = -pressure[:, :, -1] / (0.5 * g.dz)
        uz_top = uz[:, :, -1]
        uz_top[top] -= dt_s * beta_z[:, :, -1][top] * top_gradient[top]
        ux *= self._open_x
        uy *= self._open_y
        uz *= self._open_z
        after_field = self._divergence(ux, uy, uz)
        after = float(cp.max(cp.abs(after_field[self._active_mask])).item())
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

    def project(
        self,
        u_x: NDArray[np.floating],
        u_y: NDArray[np.floating],
        u_z: NDArray[np.floating],
        density_kg_m3: NDArray[np.floating],
        dt_s: float,
        *,
        initial_pressure_pa: NDArray[np.floating] | None = None,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], Projection3DDiagnostics]:
        """Host-array parity wrapper; not suitable for an HF3D hot loop."""

        outputs = self.project_device(
            self.cp.asarray(u_x),
            self.cp.asarray(u_y),
            self.cp.asarray(u_z),
            self.cp.asarray(density_kg_m3),
            dt_s,
            initial_pressure_pa=(
                None
                if initial_pressure_pa is None
                else self.cp.asarray(initial_pressure_pa)
            ),
        )
        return (
            self.cp.asnumpy(outputs[0]),
            self.cp.asnumpy(outputs[1]),
            self.cp.asnumpy(outputs[2]),
            self.cp.asnumpy(outputs[3]),
            outputs[4],
        )

    def synchronize(self) -> None:
        self.cp.cuda.get_current_stream().synchronize()


__all__ = [
    "CuPyVariableDensityProjection3D",
    "GPUProjectionProbe",
    "GPUProjectionUnavailable",
    "probe_gpu_projection",
]
