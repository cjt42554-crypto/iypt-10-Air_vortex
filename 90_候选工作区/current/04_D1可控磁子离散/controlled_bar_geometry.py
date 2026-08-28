#!/usr/bin/env python3
"""Candidate-only static geometry tools for the preregistered D1 audit.

Nothing in this module is imported by the formal solver.  G1 constructs a
Boolean mask subject to the preregistered static constraints.  G2 supplies an
independent, interval-bearing axial subcell reference for the same analytic
x-aligned capsule.
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import math
from collections import deque
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.spatial import cKDTree

G1_CANDIDATE_ID = "G1_SYMMETRY_MOMENT_CONSTRAINED_BOOLEAN_MASK"
G2_CANDIDATE_ID = "G2_ADAPTIVE_SUBCELL_CAPSULE_REFERENCE"
REPRESENTATION_PROTOCOL_ID = "D1_GRID_INDEPENDENT_1E-15_M_CANONICAL_SCALAR_V1"
REPRESENTATION_QUANTUM_M = 1.0e-15
COST_QUANTUM_M2 = 1.0e-24
SOC_CUT_TOLERANCE = 1.0e-8
LEXICOGRAPHIC_OBJECTIVE_TOLERANCE = 2.0e-6
COST_OBJECTIVE_TOLERANCE = 1.0e-9
OBJECTIVE_BISECTION_TOLERANCE = 2.0e-6
ALIGNED_CENTROID_COMPONENT_TOLERANCE = 1.0e-12
CANONICAL_BLOCK_BITS = 12
MIP_TIME_LIMIT_S = 300.0
MIP_OBJECTIVE_RELATIVE_GAP = 2.0e-5
EPIGRAPH_TIE_BREAK_L1_BOUND = 2.0e-7
G2_LOCAL_RELATIVE_WIDTH_TARGET = 5.0e-5
G2_MAX_AXIAL_SUBCELLS = 500_000
AFFINE_COMPRESSION_RELATIVE_TOLERANCE = 1.0e-12

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
Index3 = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class GridSpec:
    nx: int
    ny: int
    nz: int
    radius_m: float
    height_m: float

    def __post_init__(self) -> None:
        if min(self.nx, self.ny, self.nz) < 6:
            raise ValueError("grid dimensions must be at least 6")
        if not (self.radius_m > 0.0 and self.height_m > 0.0):
            raise ValueError("grid dimensions must be positive")

    @property
    def shape(self) -> tuple[int, int, int]:
        return (self.nx, self.ny, self.nz)

    @property
    def dx(self) -> float:
        return 2.0 * self.radius_m / self.nx

    @property
    def dy(self) -> float:
        return 2.0 * self.radius_m / self.ny

    @property
    def dz(self) -> float:
        return self.height_m / self.nz

    @property
    def cell_volume_m3(self) -> float:
        return self.dx * self.dy * self.dz

    @property
    def cell_half_diagonal_m(self) -> float:
        return 0.5 * math.sqrt(self.dx**2 + self.dy**2 + self.dz**2)

    def x_centres(self) -> FloatArray:
        # This form gives exactly opposite floating values at reflected x indices.
        numerators = 2 * np.arange(self.nx, dtype=np.int64) + 1 - self.nx
        return numerators.astype(np.float64) * (self.radius_m / self.nx)

    def y_centres(self) -> FloatArray:
        numerators = 2 * np.arange(self.ny, dtype=np.int64) + 1 - self.ny
        return numerators.astype(np.float64) * (self.radius_m / self.ny)

    def z_centres(self) -> FloatArray:
        numerators = 2 * np.arange(self.nz, dtype=np.int64) + 1
        return numerators.astype(np.float64) * (self.height_m / (2 * self.nz))

    def flat_index(self, index: Index3) -> int:
        i, j, k = index
        return (i * self.ny + j) * self.nz + k


@dataclass(frozen=True, slots=True)
class Capsule:
    half_length_m: float
    radius_m: float
    centre_xyz_m: tuple[float, float, float]

    def __post_init__(self) -> None:
        if self.half_length_m <= 0.0 or self.radius_m <= 0.0:
            raise ValueError("capsule dimensions must be positive")
        if self.half_length_m < self.radius_m:
            raise ValueError("capsule half length must be at least its radius")
        values = (self.half_length_m, self.radius_m, *self.centre_xyz_m)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("capsule parameters must be finite")

    @property
    def segment_half_length_m(self) -> float:
        return self.half_length_m - self.radius_m


@dataclass(frozen=True, slots=True)
class CellGroup:
    members: tuple[Index3, ...]
    signed_distances_m: tuple[float, ...]
    cost_delta_m2: float
    cost_key: int
    offset_sum_m: tuple[float, float, float]
    offset_outer_sum_m2: tuple[tuple[float, float, float], ...]

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass(slots=True)
class G1Build:
    mask: BoolArray
    selected_indices: tuple[Index3, ...]
    report: dict[str, Any]


class CandidateInfeasible(RuntimeError):
    """Raised when no mask satisfies the frozen D1 hard geometry constraints."""


def canonicalize_scalar_m(value: float) -> float:
    """Remove sub-physical IEEE-754 representation noise without grid snapping."""

    scaled = float(value) / REPRESENTATION_QUANTUM_M
    if abs(scaled) >= 2**52:
        raise ValueError("value is too large for the fixed D1 scalar quantum")
    return float(round(scaled) * REPRESENTATION_QUANTUM_M)


def canonicalize_capsule(capsule: Capsule) -> Capsule:
    return Capsule(
        half_length_m=canonicalize_scalar_m(capsule.half_length_m),
        radius_m=canonicalize_scalar_m(capsule.radius_m),
        centre_xyz_m=tuple(
            canonicalize_scalar_m(value) for value in capsule.centre_xyz_m
        ),
    )


def exact_capsule_volume_m3(capsule: Capsule) -> float:
    a = capsule.segment_half_length_m
    r = capsule.radius_m
    return math.pi * r**2 * (2.0 * a) + 4.0 * math.pi * r**3 / 3.0


def exact_capsule_second_moment_tensor_m5(capsule: Capsule) -> FloatArray:
    """Return the central integral of r_i r_j dV for an x-aligned capsule."""

    a = capsule.segment_half_length_m
    r = capsule.radius_m
    cylinder_xx = (2.0 / 3.0) * math.pi * r**2 * a**3
    cylinder_yy = 0.5 * math.pi * a * r**4
    sphere_volume = (4.0 / 3.0) * math.pi * r**3
    sphere_local_moment = (4.0 / 15.0) * math.pi * r**5
    caps_xx = a**2 * sphere_volume + math.pi * a * r**4 + sphere_local_moment
    caps_yy = sphere_local_moment
    return np.diag(
        [
            cylinder_xx + caps_xx,
            cylinder_yy + caps_yy,
            cylinder_yy + caps_yy,
        ]
    ).astype(np.float64)


def capsule_signed_distance_m(
    x_m: float,
    y_m: float,
    z_m: float,
    capsule: Capsule,
) -> float:
    cx, cy, cz = capsule.centre_xyz_m
    qx = max(abs(x_m - cx) - capsule.segment_half_length_m, 0.0)
    return math.sqrt(qx**2 + (y_m - cy) ** 2 + (z_m - cz) ** 2) - capsule.radius_m


def mask_sha256(mask: BoolArray) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(list(mask.shape), separators=(",", ":")).encode("ascii"))
    digest.update(b"\0")
    digest.update(np.asarray(mask, dtype=np.uint8).tobytes(order="C"))
    return digest.hexdigest()


def six_connected_component_count(indices: Iterable[Index3]) -> int:
    remaining = set(indices)
    components = 0
    while remaining:
        components += 1
        seed = remaining.pop()
        queue: deque[Index3] = deque([seed])
        while queue:
            i, j, k = queue.popleft()
            for neighbour in (
                (i - 1, j, k),
                (i + 1, j, k),
                (i, j - 1, k),
                (i, j + 1, k),
                (i, j, k - 1),
                (i, j, k + 1),
            ):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    queue.append(neighbour)
    return components


def _index_coordinates(
    grid: GridSpec,
    x_centres: FloatArray,
    y_centres: FloatArray,
    z_centres: FloatArray,
    index: Index3,
) -> FloatArray:
    i, j, k = index
    return np.asarray([x_centres[i], y_centres[j], z_centres[k]], dtype=np.float64)


def _orbit(
    grid: GridSpec,
    index: Index3,
    reflect_x: bool,
    reflect_y: bool,
) -> tuple[Index3, ...]:
    i, j, k = index
    x_indices = {i, grid.nx - 1 - i} if reflect_x else {i}
    y_indices = {j, grid.ny - 1 - j} if reflect_y else {j}
    return tuple(sorted((ii, jj, k) for ii in x_indices for jj in y_indices))


def _enumeration(
    indices: Sequence[Index3],
    mode: str,
    seed: int,
) -> list[Index3]:
    values = list(indices)
    if mode == "c":
        return values
    if mode == "reversed":
        return list(reversed(values))
    if mode == "seeded":
        rng = np.random.default_rng(seed)
        return [values[int(position)] for position in rng.permutation(len(values))]
    raise ValueError(f"unsupported enumeration mode: {mode}")


def _make_group(
    members: tuple[Index3, ...],
    signed_distances: Sequence[float],
    coordinates: Sequence[FloatArray],
    centre: FloatArray,
) -> CellGroup:
    offsets = np.asarray(coordinates, dtype=np.float64) - centre[None, :]
    cost_delta = float(
        math.fsum(
            distance**2 if distance >= 0.0 else -(distance**2)
            for distance in signed_distances
        )
    )
    outer = offsets.T @ offsets
    return CellGroup(
        members=members,
        signed_distances_m=tuple(float(value) for value in signed_distances),
        cost_delta_m2=cost_delta,
        cost_key=round(cost_delta / COST_QUANTUM_M2),
        offset_sum_m=tuple(float(value) for value in np.sum(offsets, axis=0)),
        offset_outer_sum_m2=tuple(
            tuple(float(value) for value in row) for row in outer
        ),
    )


def _candidate_groups(
    grid: GridSpec,
    capsule: Capsule,
    *,
    enumeration_mode: str,
    enumeration_seed: int,
) -> tuple[list[CellGroup], list[CellGroup], dict[str, Any]]:
    x = grid.x_centres()
    y = grid.y_centres()
    z = grid.z_centres()
    cx, cy, cz = capsule.centre_xyz_m
    half_diagonal = grid.cell_half_diagonal_m
    ix = np.flatnonzero(np.abs(x - cx) < capsule.half_length_m + half_diagonal)
    iy = np.flatnonzero(np.abs(y - cy) < capsule.radius_m + half_diagonal)
    iz = np.flatnonzero(np.abs(z - cz) < capsule.radius_m + half_diagonal)
    local_indices = [(int(i), int(j), int(k)) for i in ix for j in iy for k in iz]
    reflect_x = cx == 0.0
    reflect_y = cy == 0.0
    mandatory: list[CellGroup] = []
    boundary: list[CellGroup] = []
    seen: set[tuple[Index3, ...]] = set()
    for index in _enumeration(local_indices, enumeration_mode, enumeration_seed):
        members = _orbit(grid, index, reflect_x, reflect_y)
        if members in seen:
            continue
        seen.add(members)
        coordinates = [_index_coordinates(grid, x, y, z, member) for member in members]
        distances = [
            capsule_signed_distance_m(*coordinate, capsule)
            for coordinate in coordinates
        ]
        group = _make_group(
            members,
            distances,
            coordinates,
            np.asarray(capsule.centre_xyz_m, dtype=np.float64),
        )
        if max(distances) <= -half_diagonal:
            mandatory.append(group)
        elif min(distances) >= half_diagonal:
            continue
        else:
            boundary.append(group)
    mandatory.sort(key=lambda group: group.members)
    boundary.sort(key=lambda group: (group.cost_key, group.members))
    orbit_sizes = {group.size for group in mandatory + boundary}
    if len(orbit_sizes) != 1:
        raise CandidateInfeasible(
            f"registered even grid unexpectedly produced orbit sizes {orbit_sizes}"
        )
    return (
        mandatory,
        boundary,
        {
            "local_candidate_cell_count": len(local_indices),
            "reflection_constraints": {
                "x_shared": reflect_x,
                "y_shared": reflect_y,
            },
            "orbit_size": next(iter(orbit_sizes)),
            "enumeration_mode": enumeration_mode,
            "enumeration_seed": enumeration_seed,
        },
    )


def _sum_group_offsets(groups: Sequence[CellGroup]) -> FloatArray:
    if not groups:
        return np.zeros(3, dtype=np.float64)
    return np.sum(
        np.asarray([group.offset_sum_m for group in groups], dtype=np.float64),
        axis=0,
    )


def _sum_group_outer(groups: Sequence[CellGroup]) -> FloatArray:
    if not groups:
        return np.zeros((3, 3), dtype=np.float64)
    return np.sum(
        np.asarray([group.offset_outer_sum_m2 for group in groups], dtype=np.float64),
        axis=0,
    )


def _compress_affine_norm(
    matrix: FloatArray, offset: FloatArray
) -> tuple[FloatArray, FloatArray, dict[str, Any]]:
    """Compress ``||offset + matrix*x||`` to its deterministic affine span.

    The moment residual is stored as a flattened symmetric 3x3 tensor, but a
    particular boundary problem normally spans only three or four independent
    directions.  Carrying all nine rows makes every outer-approximation MILP
    needlessly degenerate.  Deterministic modified Gram--Schmidt preserves the
    Euclidean norm while exposing only the actually reachable affine span.
    """

    values = np.column_stack((offset, matrix))
    reference_norm = max(
        float(np.max(np.linalg.norm(values, axis=0), initial=0.0)),
        np.finfo(float).tiny,
    )
    basis: list[FloatArray] = []
    for column in range(values.shape[1]):
        residual = np.array(values[:, column], dtype=np.float64, copy=True)
        # Reorthogonalisation is important for the nearly diagonal nominal
        # cases and is deterministic because columns and basis vectors are in
        # canonical order.
        for _ in range(2):
            for vector in basis:
                residual -= float(vector @ residual) * vector
        norm = float(np.linalg.norm(residual))
        if norm > AFFINE_COMPRESSION_RELATIVE_TOLERANCE * reference_norm:
            basis.append(residual / norm)
    if not basis:
        compressed_matrix = np.zeros((0, matrix.shape[1]), dtype=np.float64)
        compressed_offset = np.zeros(0, dtype=np.float64)
        reconstruction_error = float(np.max(np.abs(values), initial=0.0))
    else:
        basis_matrix = np.asarray(basis, dtype=np.float64)
        compressed = basis_matrix @ values
        reconstructed = basis_matrix.T @ compressed
        reconstruction_error = float(
            np.max(np.abs(values - reconstructed), initial=0.0)
        )
        compressed_offset = compressed[:, 0]
        compressed_matrix = compressed[:, 1:]
    allowed_error = 20.0 * AFFINE_COMPRESSION_RELATIVE_TOLERANCE * reference_norm
    if reconstruction_error > allowed_error:
        raise CandidateInfeasible(
            "affine norm compression residual exceeds its fail-closed limit"
        )
    return (
        compressed_matrix,
        compressed_offset,
        {
            "source_dimension": int(matrix.shape[0]),
            "compressed_dimension": int(compressed_matrix.shape[0]),
            "maximum_absolute_reconstruction_error": reconstruction_error,
            "relative_tolerance": AFFINE_COMPRESSION_RELATIVE_TOLERANCE,
        },
    )


def _soc_directions(dimension: int) -> list[FloatArray]:
    result: list[FloatArray] = []
    if dimension <= 6:
        for values in itertools.product((-1.0, 0.0, 1.0), repeat=dimension):
            vector = np.asarray(values, dtype=np.float64)
            norm = float(np.linalg.norm(vector))
            if norm > 0.0:
                result.append(vector / norm)
        return result
    if dimension == 9:
        # Moment residuals are symmetric 3x3 tensors.  Seed the outer
        # approximation with the complete {-1,0,1} direction stencil in its
        # six-dimensional independent subspace; adaptive cuts then close the
        # remaining angular gap.
        for values in itertools.product((-1.0, 0.0, 1.0), repeat=6):
            xx, yy, zz, xy, xz, yz = values
            matrix = np.asarray(
                [[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]],
                dtype=np.float64,
            )
            vector = matrix.reshape(-1)
            norm = float(np.linalg.norm(vector))
            if norm > 0.0:
                result.append(vector / norm)
        return result
    for axis in range(dimension):
        direction = np.zeros(dimension, dtype=np.float64)
        direction[axis] = 1.0
        result.extend((direction, -direction))
    return result


def _append_soc_cut(directions: list[FloatArray], value: FloatArray) -> None:
    norm = float(np.linalg.norm(value))
    if norm <= np.finfo(float).tiny:
        return
    direction = value / norm
    if not any(
        np.linalg.norm(direction - existing) <= 1.0e-12 for existing in directions
    ):
        directions.append(direction)


def _constraints(
    n: int,
    required: int,
    fixed: Mapping[int, int],
    hard_socs: Sequence[tuple[FloatArray, FloatArray, float, Sequence[FloatArray]]],
    *,
    epigraph: tuple[FloatArray, FloatArray, Sequence[FloatArray]] | None = None,
    extra_linear: Sequence[tuple[FloatArray, float, float]] = (),
) -> tuple[Bounds, LinearConstraint]:
    variable_count = n + (1 if epigraph is not None else 0)
    lower_bounds = np.zeros(variable_count, dtype=np.float64)
    upper_bounds = np.ones(variable_count, dtype=np.float64)
    if epigraph is not None:
        upper_bounds[-1] = 1.0
    for index, value in fixed.items():
        lower_bounds[index] = float(value)
        upper_bounds[index] = float(value)
    rows: list[FloatArray] = []
    lows: list[float] = []
    highs: list[float] = []
    count_row = np.zeros(variable_count, dtype=np.float64)
    count_row[:n] = 1.0
    rows.append(count_row)
    lows.append(float(required))
    highs.append(float(required))
    for coefficients, lower, upper in extra_linear:
        row = np.zeros(variable_count, dtype=np.float64)
        row[:n] = coefficients
        rows.append(row)
        lows.append(float(lower))
        highs.append(float(upper))
    for matrix, offset, limit, directions in hard_socs:
        for direction in directions:
            row = np.zeros(variable_count, dtype=np.float64)
            row[:n] = direction @ matrix
            rows.append(row)
            lows.append(-np.inf)
            highs.append(float(limit - direction @ offset))
    if epigraph is not None:
        matrix, offset, directions = epigraph
        for direction in directions:
            row = np.zeros(variable_count, dtype=np.float64)
            row[:n] = direction @ matrix
            row[-1] = -1.0
            rows.append(row)
            lows.append(-np.inf)
            highs.append(float(-(direction @ offset)))
    return Bounds(lower_bounds, upper_bounds), LinearConstraint(
        np.vstack(rows), np.asarray(lows), np.asarray(highs)
    )


def _solve_linear_with_socs(
    required: int,
    objective: FloatArray,
    centroid_matrix: FloatArray,
    centroid_offset: FloatArray,
    centroid_limit: float,
    centroid_directions: list[FloatArray],
    moment_matrix: FloatArray,
    moment_offset: FloatArray,
    moment_limit: float,
    moment_directions: list[FloatArray],
    *,
    extra_linear: Sequence[tuple[FloatArray, float, float]] = (),
    fixed: Mapping[int, int] | None = None,
    acceptance_tolerance: float = SOC_CUT_TOLERANCE,
) -> tuple[FloatArray, dict[str, Any]]:
    """Globally minimise a linear objective over two binary SOC constraints."""

    n = objective.size
    fixed_values = {} if fixed is None else dict(fixed)
    for iteration in range(256):
        bounds, constraints = _constraints(
            n,
            required,
            fixed_values,
            [
                (
                    centroid_matrix,
                    centroid_offset,
                    centroid_limit,
                    centroid_directions,
                ),
                (moment_matrix, moment_offset, moment_limit, moment_directions),
            ],
            extra_linear=extra_linear,
        )
        result = milp(
            objective,
            integrality=np.ones(n, dtype=np.int8),
            bounds=bounds,
            constraints=constraints,
            options={"mip_rel_gap": 0.0, "time_limit": MIP_TIME_LIMIT_S},
        )
        if not result.success or result.x is None:
            raise CandidateInfeasible(str(result.message))
        selection = np.rint(result.x).astype(np.float64)
        centroid_value = centroid_offset + centroid_matrix @ selection
        moment_value = moment_offset + moment_matrix @ selection
        centroid_norm = float(np.linalg.norm(centroid_value))
        moment_norm = float(np.linalg.norm(moment_value))
        added = False
        if centroid_norm > centroid_limit + acceptance_tolerance:
            _append_soc_cut(centroid_directions, centroid_value)
            added = True
        if moment_norm > moment_limit + acceptance_tolerance:
            _append_soc_cut(moment_directions, moment_value)
            added = True
        if not added:
            return selection, {
                "outer_approximation_iterations": iteration + 1,
                "linear_objective": float(objective @ selection),
                "centroid_norm": centroid_norm,
                "moment_norm": moment_norm,
            }
    raise CandidateInfeasible("linear/SOC outer approximation did not converge")


def _solve_epigraph_soc(
    required: int,
    objective_matrix: FloatArray,
    objective_offset: FloatArray,
    objective_limit: float,
    objective_directions: list[FloatArray],
    hard_matrix: FloatArray,
    hard_offset: FloatArray,
    hard_limit: float,
    hard_directions: list[FloatArray],
    *,
    fixed: Mapping[int, int] | None = None,
    extra_linear: Sequence[tuple[FloatArray, float, float]] = (),
) -> tuple[FloatArray, dict[str, Any]]:
    n = objective_matrix.shape[1]
    fixed_values = {} if fixed is None else dict(fixed)
    # A bounded deterministic perturbation removes vast families of masks
    # that are identical in the low-rank epigraph relaxation.  Its complete
    # L1 range is kept one order of magnitude below the registered objective
    # bracket and is included explicitly in the global lower-bound audit.
    tie_raw = np.asarray(
        [
            int.from_bytes(
                hashlib.sha256(f"D1-EPIGRAPH-{index}".encode("ascii")).digest()[:8],
                "big",
            )
            / float(2**64 - 1)
            - 0.5
            for index in range(n)
        ],
        dtype=np.float64,
    )
    tie_norm = float(np.sum(np.abs(tie_raw)))
    tie_break = (
        tie_raw * (EPIGRAPH_TIE_BREAK_L1_BOUND / tie_norm)
        if tie_norm > 0.0
        else tie_raw
    )
    last_diagnostics: dict[str, float | int] = {}
    for iteration in range(256):
        bounds, constraints = _constraints(
            n,
            required,
            fixed_values,
            [(hard_matrix, hard_offset, hard_limit, hard_directions)],
            epigraph=(
                objective_matrix,
                objective_offset,
                objective_directions,
            ),
            extra_linear=extra_linear,
        )
        upper_bounds = np.array(bounds.ub, dtype=np.float64, copy=True)
        upper_bounds[-1] = objective_limit
        bounds = Bounds(np.array(bounds.lb, dtype=np.float64, copy=True), upper_bounds)
        objective = np.zeros(n + 1, dtype=np.float64)
        objective[:n] = tie_break
        objective[-1] = 1.0
        result = milp(
            objective,
            integrality=np.r_[np.ones(n, dtype=np.int8), 0],
            bounds=bounds,
            constraints=constraints,
            # The registered lexicographic objective already admits a 2e-6
            # normalized bracket.  Requiring a zero MIP gap here spends
            # minutes proving distinctions far below that contract.  The
            # relative gap below is tighter than the registered absolute
            # bracket for every accepted objective in [0, 0.05].
            options={
                "mip_rel_gap": MIP_OBJECTIVE_RELATIVE_GAP,
                "time_limit": MIP_TIME_LIMIT_S,
            },
        )
        if not result.success or result.x is None:
            raise CandidateInfeasible(str(result.message))
        selection = np.rint(result.x[:n]).astype(np.float64)
        objective_value = objective_offset + objective_matrix @ selection
        hard_value = hard_offset + hard_matrix @ selection
        objective_norm = float(np.linalg.norm(objective_value))
        hard_norm = float(np.linalg.norm(hard_value))
        epigraph_value = float(result.x[-1])
        mip_dual_bound = float(getattr(result, "mip_dual_bound", result.fun))
        objective_global_lower_bound = mip_dual_bound - EPIGRAPH_TIE_BREAK_L1_BOUND
        objective_global_bracket = objective_norm - objective_global_lower_bound
        last_diagnostics = {
            "iteration": iteration + 1,
            "epigraph": epigraph_value,
            "objective_norm": objective_norm,
            "outer_gap": objective_norm - epigraph_value,
            "hard_norm": hard_norm,
            "objective_direction_count": len(objective_directions),
            "hard_direction_count": len(hard_directions),
            "objective_global_lower_bound": objective_global_lower_bound,
            "objective_global_bracket": objective_global_bracket,
        }
        added = False
        if objective_norm > epigraph_value + SOC_CUT_TOLERANCE:
            _append_soc_cut(objective_directions, objective_value)
            added = True
        if hard_norm > hard_limit + SOC_CUT_TOLERANCE:
            _append_soc_cut(hard_directions, hard_value)
            added = True
        if not added:
            if objective_norm > objective_limit + SOC_CUT_TOLERANCE:
                raise CandidateInfeasible("SOC objective exceeds its hard limit")
            if objective_global_bracket > LEXICOGRAPHIC_OBJECTIVE_TOLERANCE:
                raise CandidateInfeasible(
                    "SOC objective global bracket exceeds the registered "
                    "lexicographic tolerance: "
                    + json.dumps(last_diagnostics, sort_keys=True)
                )
            return selection, {
                "outer_approximation_iterations": iteration + 1,
                "objective_lower_bound": epigraph_value,
                "objective_upper_bound": objective_norm,
                "objective_outer_gap": objective_norm - epigraph_value,
                "objective_global_lower_bound": objective_global_lower_bound,
                "objective_global_bracket": objective_global_bracket,
                "epigraph_tie_break_l1_bound": (EPIGRAPH_TIE_BREAK_L1_BOUND),
                "mip_dual_bound_with_tie_break": mip_dual_bound,
                "other_hard_norm": hard_norm,
            }
    raise CandidateInfeasible(
        "SOC outer approximation did not converge: "
        + json.dumps(last_diagnostics, sort_keys=True)
    )


def _soc_feasible(
    required: int,
    centroid_matrix: FloatArray,
    centroid_offset: FloatArray,
    centroid_limit: float,
    centroid_directions: list[FloatArray],
    moment_matrix: FloatArray,
    moment_offset: FloatArray,
    moment_limit: float,
    moment_directions: list[FloatArray],
    fixed: Mapping[int, int],
    *,
    extra_linear: Sequence[tuple[FloatArray, float, float]] = (),
) -> FloatArray | None:
    n = centroid_matrix.shape[1]
    for _ in range(256):
        bounds, constraints = _constraints(
            n,
            required,
            fixed,
            [
                (
                    centroid_matrix,
                    centroid_offset,
                    centroid_limit,
                    centroid_directions,
                ),
                (moment_matrix, moment_offset, moment_limit, moment_directions),
            ],
            extra_linear=extra_linear,
        )
        result = milp(
            np.zeros(n, dtype=np.float64),
            integrality=np.ones(n, dtype=np.int8),
            bounds=bounds,
            constraints=constraints,
            options={"mip_rel_gap": 0.0, "time_limit": MIP_TIME_LIMIT_S},
        )
        if result.x is not None:
            selection = np.rint(result.x).astype(np.float64)
            centroid_value = centroid_offset + centroid_matrix @ selection
            moment_value = moment_offset + moment_matrix @ selection
            added = False
            if np.linalg.norm(centroid_value) > centroid_limit + SOC_CUT_TOLERANCE:
                _append_soc_cut(centroid_directions, centroid_value)
                added = True
            if np.linalg.norm(moment_value) > moment_limit + SOC_CUT_TOLERANCE:
                _append_soc_cut(moment_directions, moment_value)
                added = True
            if not added:
                # A feasible incumbent proves feasibility even if HiGHS hit a
                # time limit before completing an irrelevant zero-objective
                # search.
                return selection
        if result.status == 2:
            # Infeasibility of the outer relaxation is a valid proof that the
            # original SOC-constrained binary problem is infeasible.
            return None
        if not result.success:
            raise CandidateInfeasible(
                "SOC feasibility was not proved feasible or infeasible: "
                + str(result.message)
            )
    raise CandidateInfeasible("SOC feasibility outer approximation did not converge")


def _minimize_soc_by_bisection(
    required: int,
    objective_matrix: FloatArray,
    objective_offset: FloatArray,
    objective_hard_limit: float,
    objective_directions: list[FloatArray],
    other_matrix: FloatArray,
    other_offset: FloatArray,
    other_limit: float,
    other_directions: list[FloatArray],
    *,
    extra_linear: Sequence[tuple[FloatArray, float, float]] = (),
    initial_feasible: FloatArray | None = None,
) -> tuple[FloatArray, dict[str, Any]]:
    """Bracket the global discrete SOC optimum with fail-closed feasibility."""

    if initial_feasible is None:
        feasible = _soc_feasible(
            required,
            objective_matrix,
            objective_offset,
            objective_hard_limit,
            objective_directions,
            other_matrix,
            other_offset,
            other_limit,
            other_directions,
            {},
            extra_linear=extra_linear,
        )
        if feasible is None:
            raise CandidateInfeasible("no feasible point at the registered SOC gate")
    else:
        feasible = np.asarray(initial_feasible, dtype=np.float64)
        if int(np.rint(feasible).sum()) != required:
            raise CandidateInfeasible("initial SOC point has the wrong cardinality")
        if (
            np.linalg.norm(objective_offset + objective_matrix @ feasible)
            > objective_hard_limit + SOC_CUT_TOLERANCE
            or np.linalg.norm(other_offset + other_matrix @ feasible)
            > other_limit + SOC_CUT_TOLERANCE
        ):
            raise CandidateInfeasible("initial SOC point violates a hard gate")
    upper = float(np.linalg.norm(objective_offset + objective_matrix @ feasible))
    lower = 0.0
    best = feasible
    iterations = 0
    while upper - lower > OBJECTIVE_BISECTION_TOLERANCE and iterations < 64:
        iterations += 1
        midpoint = 0.5 * (lower + upper)
        candidate = _soc_feasible(
            required,
            objective_matrix,
            objective_offset,
            midpoint,
            objective_directions,
            other_matrix,
            other_offset,
            other_limit,
            other_directions,
            {},
            extra_linear=extra_linear,
        )
        if candidate is None:
            lower = midpoint
            continue
        candidate_norm = float(
            np.linalg.norm(objective_offset + objective_matrix @ candidate)
        )
        if candidate_norm > midpoint + SOC_CUT_TOLERANCE:
            raise CandidateInfeasible("SOC feasibility returned an invalid candidate")
        best = candidate
        upper = min(upper, candidate_norm)
    return best, {
        "method": "FAIL_CLOSED_MILP_SOC_FEASIBILITY_BISECTION",
        "iterations": iterations,
        "objective_lower_bound": lower,
        "objective_upper_bound": upper,
        "objective_bracket_width": upper - lower,
        "objective_bisection_tolerance": OBJECTIVE_BISECTION_TOLERANCE,
        "other_hard_norm": float(np.linalg.norm(other_offset + other_matrix @ best)),
    }


def _fit_layer_polynomial(
    abscissae: FloatArray,
    values: FloatArray,
    degree: int,
) -> tuple[FloatArray, float]:
    """Fit and fail-closed validate a layer-wise coordinate polynomial."""

    design = np.column_stack([abscissae**power for power in range(degree, -1, -1)])
    coefficients, _, _, _ = np.linalg.lstsq(design, values.T, rcond=None)
    reconstructed = design @ coefficients
    error = float(np.max(np.abs(reconstructed - values.T), initial=0.0))
    scale = max(float(np.max(np.abs(values), initial=0.0)), 1.0)
    if error > 5.0e-11 * scale:
        raise CandidateInfeasible(
            "layer polynomial reduction exceeds its fail-closed residual"
        )
    return coefficients.T, error


def _layer_reachable_states(
    positions: Sequence[int],
    abscissae: Sequence[int],
    canonical_bits: Mapping[int, int],
    maximum_count: int | None = None,
) -> list[dict[tuple[int, int], int]]:
    """Enumerate reachable (sum x, sum x^2) states by cardinality."""

    limit = len(positions) if maximum_count is None else maximum_count
    if not 0 <= limit <= len(positions):
        raise ValueError("reachable-state count limit is invalid")
    states: list[dict[tuple[int, int], int]] = [{} for _ in range(limit + 1)]
    states[0][(0, 0)] = 0
    for ordinal, (position, abscissa) in enumerate(
        zip(positions, abscissae, strict=True)
    ):
        bit = canonical_bits[position]
        for count in range(min(ordinal + 1, limit), 0, -1):
            destination = states[count]
            for (linear_sum, square_sum), score in states[count - 1].items():
                key = (
                    linear_sum + int(abscissa),
                    square_sum + int(abscissa) ** 2,
                )
                candidate_score = score | bit
                previous = destination.get(key)
                if previous is None or candidate_score > previous:
                    destination[key] = candidate_score
    return states


def _solve_two_layer_x_polynomial_exact(
    grid: GridSpec,
    active_groups: Sequence[CellGroup],
    required: int,
    centroid_matrix: FloatArray,
    centroid_offset: FloatArray,
    centroid_limit: float,
    moment_matrix: FloatArray,
    moment_offset: FloatArray,
    moment_limit: float,
    extra_linear: Sequence[tuple[FloatArray, float, float]],
) -> tuple[FloatArray, dict[str, Any]] | None:
    """Exactly enumerate the generic two-z-layer, x-polynomial tie family.

    The coarse 6-cell phase cases expose a 48-variable cost tie: two axial
    rows of 24 y-reflected groups.  Their first moments are affine in the x
    index and their second moments are quadratic.  Dynamic programming over
    the reachable integer sums proves the remaining lexicographic optimum
    without a grid-specific mask or an unbounded MISOCP search.
    """

    n = len(active_groups)
    if n == 0 or n > 63:
        return None
    layer_positions: dict[int, list[int]] = {}
    x_indices: dict[int, int] = {}
    for position, group in enumerate(active_groups):
        member_i = {member[0] for member in group.members}
        member_k = {member[2] for member in group.members}
        if len(member_i) != 1 or len(member_k) != 1:
            return None
        x_indices[position] = next(iter(member_i))
        layer_positions.setdefault(next(iter(member_k)), []).append(position)
    if len(layer_positions) != 2:
        return None
    layers = [
        sorted(positions, key=lambda position: x_indices[position])
        for _, positions in sorted(layer_positions.items())
    ]
    if min(len(layer) for layer in layers) < 3:
        return None
    if any(
        len({x_indices[position] for position in layer}) != len(layer)
        for layer in layers
    ):
        return None

    canonical_order = sorted(
        range(n),
        key=lambda position: min(
            grid.flat_index(index) for index in active_groups[position].members
        ),
    )
    canonical_bits = {
        position: 1 << (n - 1 - rank) for rank, position in enumerate(canonical_order)
    }
    layer_abscissae: list[list[int]] = []
    centroid_fits: list[FloatArray] = []
    moment_fits: list[FloatArray] = []
    constraint_constants: list[list[float]] = [[] for _ in extra_linear]
    maximum_fit_error = 0.0
    for layer in layers:
        origin = min(x_indices[position] for position in layer)
        integer_x = [x_indices[position] - origin for position in layer]
        layer_abscissae.append(integer_x)
        coordinates = np.asarray(integer_x, dtype=np.float64)
        centroid_fit, error = _fit_layer_polynomial(
            coordinates, centroid_matrix[:, layer], 1
        )
        maximum_fit_error = max(maximum_fit_error, error)
        centroid_fits.append(centroid_fit)
        moment_fit, error = _fit_layer_polynomial(
            coordinates, moment_matrix[:, layer], 2
        )
        maximum_fit_error = max(maximum_fit_error, error)
        moment_fits.append(moment_fit)
        for constraint_index, (coefficients, _, _) in enumerate(extra_linear):
            layer_values = coefficients[np.asarray(layer, dtype=np.int64)]
            constant = float(np.mean(layer_values))
            error = float(np.max(np.abs(layer_values - constant), initial=0.0))
            maximum_fit_error = max(maximum_fit_error, error)
            if error > 5.0e-11 * max(abs(constant), 1.0):
                return None
            constraint_constants[constraint_index].append(constant)

    reachable = [
        _layer_reachable_states(layer, abscissae, canonical_bits)
        for layer, abscissae in zip(layers, layer_abscissae, strict=True)
    ]
    states_by_sum: list[list[dict[int, list[tuple[int, int]]]]] = []
    for layer_states in reachable:
        layer_by_count: list[dict[int, list[tuple[int, int]]]] = []
        for count_states in layer_states:
            grouped: dict[int, list[tuple[int, int]]] = {}
            for (linear_sum, square_sum), score in count_states.items():
                grouped.setdefault(linear_sum, []).append((square_sum, score))
            for values in grouped.values():
                values.sort(key=lambda item: item[0])
            layer_by_count.append(grouped)
        states_by_sum.append(layer_by_count)

    feasible_blocks: list[
        tuple[list[tuple[int, int]], list[tuple[int, int]], FloatArray]
    ] = []
    evaluated_state_pairs = 0
    best_moment = math.inf
    minimum_count_0 = max(0, required - len(layers[1]))
    maximum_count_0 = min(required, len(layers[0]))
    for count_0 in range(minimum_count_0, maximum_count_0 + 1):
        count_1 = required - count_0
        constraint_pass = True
        for constraint_index, (_, lower, upper) in enumerate(extra_linear):
            value = (
                constraint_constants[constraint_index][0] * count_0
                + constraint_constants[constraint_index][1] * count_1
            )
            if value < lower - 1.0e-10 or value > upper + 1.0e-10:
                constraint_pass = False
                break
        if not constraint_pass:
            continue
        groups_0 = states_by_sum[0][count_0]
        groups_1 = states_by_sum[1][count_1]
        for linear_0, values_0 in groups_0.items():
            centroid_0 = (
                centroid_fits[0][:, 0] * linear_0 + centroid_fits[0][:, 1] * count_0
            )
            for linear_1, values_1 in groups_1.items():
                centroid = (
                    centroid_offset
                    + centroid_0
                    + centroid_fits[1][:, 0] * linear_1
                    + centroid_fits[1][:, 1] * count_1
                )
                if np.linalg.norm(centroid) > centroid_limit + 1.0e-12:
                    continue
                square_0 = np.asarray(
                    [value[0] for value in values_0], dtype=np.float64
                )
                square_1 = np.asarray(
                    [value[0] for value in values_1], dtype=np.float64
                )
                fixed_moment = (
                    moment_offset
                    + moment_fits[0][:, 1] * linear_0
                    + moment_fits[0][:, 2] * count_0
                    + moment_fits[1][:, 1] * linear_1
                    + moment_fits[1][:, 2] * count_1
                )
                residuals = (
                    fixed_moment[:, None, None]
                    + moment_fits[0][:, 0, None, None] * square_0[None, :, None]
                    + moment_fits[1][:, 0, None, None] * square_1[None, None, :]
                )
                norms = np.linalg.norm(residuals, axis=0)
                valid = norms <= moment_limit + SOC_CUT_TOLERANCE
                if not np.any(valid):
                    continue
                evaluated_state_pairs += int(norms.size)
                local_minimum = float(np.min(norms[valid]))
                best_moment = min(best_moment, local_minimum)
                feasible_blocks.append((values_0, values_1, norms))
    if not math.isfinite(best_moment):
        raise CandidateInfeasible(
            "two-layer exact enumeration found no hard-gate-feasible mask"
        )

    canonical_score = -1
    accepted_state_pairs = 0
    for values_0, values_1, norms in feasible_blocks:
        scores_0 = [value[1] for value in values_0]
        scores_1 = np.asarray([value[1] for value in values_1], dtype=np.uint64)
        accepted = norms <= (best_moment + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-12)
        accepted_state_pairs += int(np.count_nonzero(accepted))
        for row, score_0 in enumerate(scores_0):
            columns = accepted[row]
            if not np.any(columns):
                continue
            score_1 = int(np.max(scores_1[columns]))
            canonical_score = max(canonical_score, int(score_0) | score_1)
    if canonical_score < 0:
        raise CandidateInfeasible("two-layer canonical selection did not close")
    selection = np.zeros(n, dtype=np.float64)
    for position, bit in canonical_bits.items():
        selection[position] = float(bool(canonical_score & bit))
    centroid_final = float(
        np.linalg.norm(centroid_offset + centroid_matrix @ selection)
    )
    moment_final = float(np.linalg.norm(moment_offset + moment_matrix @ selection))
    if int(np.rint(selection.sum())) != required:
        raise CandidateInfeasible("two-layer exact selection has wrong cardinality")
    if centroid_final > centroid_limit + 1.0e-10:
        raise CandidateInfeasible("two-layer exact selection violates centroid prefix")
    if moment_final > best_moment + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-10:
        raise CandidateInfeasible("two-layer exact selection violates moment prefix")
    return selection, {
        "method": "EXACT_TWO_LAYER_X_POLYNOMIAL_REACHABLE_STATE_ENUMERATION",
        "layer_sizes": [len(layer) for layer in layers],
        "reachable_state_counts": [
            [len(states) for states in layer_states] for layer_states in reachable
        ],
        "maximum_polynomial_reconstruction_error": maximum_fit_error,
        "evaluated_state_pair_count": evaluated_state_pairs,
        "accepted_canonical_state_pair_count": accepted_state_pairs,
        "objective_lower_bound": best_moment,
        "objective_upper_bound": best_moment,
        "objective_bracket_width": 0.0,
        "objective_bisection_tolerance": 0.0,
        "final_selected_objective": moment_final,
        "centroid_prefix_norm": centroid_final,
    }


def _solve_two_layer_combined_exact(
    grid: GridSpec,
    active_groups: Sequence[CellGroup],
    required: int,
    centroid_matrix: FloatArray,
    centroid_offset: FloatArray,
    moment_matrix: FloatArray,
    moment_offset: FloatArray,
    moment_limit: float,
    extra_linear: Sequence[tuple[FloatArray, float, float]],
) -> tuple[FloatArray, dict[str, Any], FloatArray, dict[str, Any]] | None:
    """Bracket centroid exactly, then close moment/canonical on two x rows."""

    layer_positions: dict[int, list[int]] = {}
    x_indices: dict[int, int] = {}
    for position, group in enumerate(active_groups):
        member_i = {member[0] for member in group.members}
        member_k = {member[2] for member in group.members}
        if len(member_i) != 1 or len(member_k) != 1:
            return None
        x_indices[position] = next(iter(member_i))
        layer_positions.setdefault(next(iter(member_k)), []).append(position)
    if len(layer_positions) != 2:
        return None
    layers = [
        sorted(positions, key=lambda position: x_indices[position])
        for _, positions in sorted(layer_positions.items())
    ]
    if min(len(layer) for layer in layers) < 3:
        return None
    centroid_fits: list[FloatArray] = []
    abscissae_by_layer: list[list[int]] = []
    constraint_constants: list[list[float]] = [[] for _ in extra_linear]
    for layer in layers:
        if len({x_indices[position] for position in layer}) != len(layer):
            return None
        origin = min(x_indices[position] for position in layer)
        abscissae = [x_indices[position] - origin for position in layer]
        abscissae_by_layer.append(abscissae)
        fit, _ = _fit_layer_polynomial(
            np.asarray(abscissae, dtype=np.float64),
            centroid_matrix[:, layer],
            1,
        )
        centroid_fits.append(fit)
        for constraint_index, (coefficients, _, _) in enumerate(extra_linear):
            values = coefficients[np.asarray(layer, dtype=np.int64)]
            constant = float(np.mean(values))
            if np.max(np.abs(values - constant), initial=0.0) > 5.0e-11 * max(
                abs(constant), 1.0
            ):
                return None
            constraint_constants[constraint_index].append(constant)

    reachable_sums: list[list[set[int]]] = []
    for layer, abscissae in zip(layers, abscissae_by_layer, strict=True):
        states = [set() for _ in range(len(layer) + 1)]
        states[0].add(0)
        for ordinal, abscissa in enumerate(abscissae):
            for count in range(ordinal + 1, 0, -1):
                states[count].update(value + abscissa for value in states[count - 1])
        reachable_sums.append(states)
    centroid_lower = math.inf
    minimum_count_0 = max(0, required - len(layers[1]))
    maximum_count_0 = min(required, len(layers[0]))
    for count_0 in range(minimum_count_0, maximum_count_0 + 1):
        count_1 = required - count_0
        constraint_pass = True
        for constraint_index, (_, lower, upper) in enumerate(extra_linear):
            value = (
                constraint_constants[constraint_index][0] * count_0
                + constraint_constants[constraint_index][1] * count_1
            )
            if value < lower - 1.0e-10 or value > upper + 1.0e-10:
                constraint_pass = False
                break
        if not constraint_pass:
            continue
        for linear_0 in reachable_sums[0][count_0]:
            partial = (
                centroid_offset
                + centroid_fits[0][:, 0] * linear_0
                + centroid_fits[0][:, 1] * count_0
                + centroid_fits[1][:, 1] * count_1
            )
            for linear_1 in reachable_sums[1][count_1]:
                centroid_lower = min(
                    centroid_lower,
                    float(np.linalg.norm(partial + centroid_fits[1][:, 0] * linear_1)),
                )
    if not math.isfinite(centroid_lower) or centroid_lower > 0.05:
        return None
    centroid_prefix = centroid_lower + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE
    try:
        stage4_result = _solve_two_layer_x_polynomial_exact(
            grid,
            active_groups,
            required,
            centroid_matrix,
            centroid_offset,
            centroid_prefix,
            moment_matrix,
            moment_offset,
            moment_limit,
            extra_linear,
        )
    except CandidateInfeasible:
        # The unconstrained centroid lower bound may be attainable only by a
        # mask that violates the moment hard gate.  Let the general joint
        # solver handle that uncommon case without weakening either gate.
        return None
    if stage4_result is None:
        return None
    final_selection, stage4_report = stage4_result
    centroid_upper = float(
        np.linalg.norm(centroid_offset + centroid_matrix @ final_selection)
    )
    if centroid_upper - centroid_lower > LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-12:
        raise CandidateInfeasible(
            "two-layer centroid bracket exceeds the registered tolerance"
        )
    stage3_report = {
        "method": "EXACT_TWO_LAYER_X_SUM_REACHABILITY_WITH_FEASIBLE_WITNESS",
        "objective": "CENTROID_NORM",
        "objective_lower_bound": centroid_lower,
        "objective_upper_bound": centroid_upper,
        "objective_bracket_width": centroid_upper - centroid_lower,
        "objective_bisection_tolerance": 0.0,
        "other_hard_norm": float(
            np.linalg.norm(moment_offset + moment_matrix @ final_selection)
        ),
        "layer_sizes": [len(layer) for layer in layers],
    }
    return final_selection, stage3_report, final_selection, stage4_report


def _solve_one_line_combined_exact(
    grid: GridSpec,
    active_groups: Sequence[CellGroup],
    required: int,
    centroid_matrix: FloatArray,
    centroid_offset: FloatArray,
    moment_matrix: FloatArray,
    moment_offset: FloatArray,
    moment_limit: float,
    extra_linear: Sequence[tuple[FloatArray, float, float]],
) -> tuple[FloatArray, dict[str, Any], FloatArray, dict[str, Any]] | None:
    """Solve centroid, moment and canonical stages for one x-index line."""

    n = len(active_groups)
    if n < 3 or n > 63:
        return None
    x_indices: list[int] = []
    transverse_signature: tuple[tuple[int, int], ...] | None = None
    for group in active_groups:
        member_i = {member[0] for member in group.members}
        if len(member_i) != 1:
            return None
        signature = tuple(sorted((member[1], member[2]) for member in group.members))
        if transverse_signature is None:
            transverse_signature = signature
        elif signature != transverse_signature:
            return None
        x_indices.append(next(iter(member_i)))
    if len(set(x_indices)) != n:
        return None
    positions = sorted(range(n), key=lambda position: x_indices[position])
    origin = min(x_indices)
    abscissae = [x_indices[position] - origin for position in positions]
    coordinates = np.asarray(abscissae, dtype=np.float64)
    centroid_fit, centroid_fit_error = _fit_layer_polynomial(
        coordinates, centroid_matrix[:, positions], 1
    )
    moment_fit, moment_fit_error = _fit_layer_polynomial(
        coordinates, moment_matrix[:, positions], 2
    )
    constraint_constants: list[float] = []
    maximum_fit_error = max(centroid_fit_error, moment_fit_error)
    for coefficients, _, _ in extra_linear:
        values = coefficients[np.asarray(positions, dtype=np.int64)]
        constant = float(np.mean(values))
        error = float(np.max(np.abs(values - constant), initial=0.0))
        maximum_fit_error = max(maximum_fit_error, error)
        if error > 5.0e-11 * max(abs(constant), 1.0):
            return None
        constraint_constants.append(constant)
    for constraint_index, (_, lower, upper) in enumerate(extra_linear):
        value = constraint_constants[constraint_index] * required
        if value < lower - 1.0e-10 or value > upper + 1.0e-10:
            raise CandidateInfeasible(
                "one-line exact family violates a lexicographic prefix"
            )

    canonical_order = sorted(
        range(n),
        key=lambda position: min(
            grid.flat_index(index) for index in active_groups[position].members
        ),
    )
    canonical_bits = {
        position: 1 << (n - 1 - rank) for rank, position in enumerate(canonical_order)
    }
    reachable = _layer_reachable_states(
        positions,
        abscissae,
        canonical_bits,
        maximum_count=required,
    )
    final_states = reachable[required]
    if not final_states:
        raise CandidateInfeasible("one-line reachable-state family is empty")

    def metrics(linear_sum: int, square_sum: int) -> tuple[float, float]:
        centroid = (
            centroid_offset
            + centroid_fit[:, 0] * linear_sum
            + centroid_fit[:, 1] * required
        )
        moment = (
            moment_offset
            + moment_fit[:, 0] * square_sum
            + moment_fit[:, 1] * linear_sum
            + moment_fit[:, 2] * required
        )
        return float(np.linalg.norm(centroid)), float(np.linalg.norm(moment))

    state_metrics: dict[tuple[int, int], tuple[float, float]] = {}
    centroid_optimum = math.inf
    centroid_score = -1
    for state, score in final_states.items():
        centroid_norm, moment_norm = metrics(*state)
        if moment_norm > moment_limit + SOC_CUT_TOLERANCE:
            continue
        state_metrics[state] = (centroid_norm, moment_norm)
        if centroid_norm < centroid_optimum - 1.0e-14:
            centroid_optimum = centroid_norm
            centroid_score = score
        elif abs(centroid_norm - centroid_optimum) <= 1.0e-14:
            centroid_score = max(centroid_score, score)
    if not math.isfinite(centroid_optimum) or centroid_optimum > 0.05:
        raise CandidateInfeasible(
            "one-line exact family violates the centroid hard gate"
        )
    centroid_prefix = centroid_optimum + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE
    moment_optimum = min(
        moment_norm
        for centroid_norm, moment_norm in state_metrics.values()
        if centroid_norm <= centroid_prefix + 1.0e-12
    )
    final_score = max(
        final_states[state]
        for state, (centroid_norm, moment_norm) in state_metrics.items()
        if centroid_norm <= centroid_prefix + 1.0e-12
        and moment_norm <= moment_optimum + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-12
    )

    def selection_from_score(score: int) -> FloatArray:
        return np.asarray(
            [float(bool(score & canonical_bits[position])) for position in range(n)],
            dtype=np.float64,
        )

    stage3_selection = selection_from_score(centroid_score)
    final_selection = selection_from_score(final_score)
    centroid_final = float(
        np.linalg.norm(centroid_offset + centroid_matrix @ final_selection)
    )
    moment_final = float(
        np.linalg.norm(moment_offset + moment_matrix @ final_selection)
    )
    if int(np.rint(final_selection.sum())) != required:
        raise CandidateInfeasible("one-line exact selection has wrong cardinality")
    if centroid_final > centroid_prefix + 1.0e-10:
        raise CandidateInfeasible("one-line exact selection violates centroid prefix")
    if moment_final > moment_optimum + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-10:
        raise CandidateInfeasible("one-line exact selection violates moment prefix")
    common = {
        "method": "EXACT_ONE_LINE_X_POLYNOMIAL_REACHABLE_STATE_ENUMERATION",
        "variable_count": n,
        "required_count": required,
        "reachable_state_count": len(final_states),
        "hard_moment_feasible_state_count": len(state_metrics),
        "maximum_polynomial_reconstruction_error": maximum_fit_error,
    }
    stage3_report = {
        **common,
        "objective": "CENTROID_NORM",
        "objective_lower_bound": centroid_optimum,
        "objective_upper_bound": centroid_optimum,
        "objective_bracket_width": 0.0,
        "objective_bisection_tolerance": 0.0,
        "other_hard_norm": float(
            np.linalg.norm(moment_offset + moment_matrix @ stage3_selection)
        ),
    }
    stage4_report = {
        **common,
        "objective": "SECOND_MOMENT_RELATIVE_FROBENIUS",
        "centroid_prefix_norm": centroid_final,
        "objective_lower_bound": moment_optimum,
        "objective_upper_bound": moment_optimum,
        "objective_bracket_width": 0.0,
        "objective_bisection_tolerance": 0.0,
        "final_selected_objective": moment_final,
    }
    return stage3_selection, stage3_report, final_selection, stage4_report


def _combination_batches(
    n: int, required: int, batch_size: int = 50_000
) -> Iterable[NDArray[np.int64]]:
    iterator = itertools.combinations(range(n), required)
    while True:
        batch = list(itertools.islice(iterator, batch_size))
        if not batch:
            return
        yield np.asarray(batch, dtype=np.int64)


def _solve_small_exact_enumeration(
    grid: GridSpec,
    active_groups: Sequence[CellGroup],
    required: int,
    centroid_matrix: FloatArray,
    centroid_offset: FloatArray,
    centroid_limit: float,
    moment_matrix: FloatArray,
    moment_offset: FloatArray,
    moment_limit: float,
    extra_linear: Sequence[tuple[FloatArray, float, float]],
) -> tuple[FloatArray, dict[str, Any]] | None:
    """Exhaust the complete fixed-cardinality family for at most 24 groups."""

    n = len(active_groups)
    if n == 0 or n > 24:
        return None
    combination_count = math.comb(n, required)
    best_moment = math.inf
    hard_feasible_count = 0
    for combinations in _combination_batches(n, required):
        centroid = centroid_offset[:, None] + np.sum(
            centroid_matrix[:, combinations], axis=2
        )
        valid = np.linalg.norm(centroid, axis=0) <= centroid_limit + 1.0e-12
        for coefficients, lower, upper in extra_linear:
            values = np.sum(coefficients[combinations], axis=1)
            valid &= values >= lower - 1.0e-10
            valid &= values <= upper + 1.0e-10
        if not np.any(valid):
            continue
        moment = moment_offset[:, None] + np.sum(moment_matrix[:, combinations], axis=2)
        norms = np.linalg.norm(moment, axis=0)
        valid &= norms <= moment_limit + SOC_CUT_TOLERANCE
        if not np.any(valid):
            continue
        hard_feasible_count += int(np.count_nonzero(valid))
        best_moment = min(best_moment, float(np.min(norms[valid])))
    if not math.isfinite(best_moment):
        raise CandidateInfeasible(
            "small exact enumeration found no hard-gate-feasible mask"
        )

    canonical_order = sorted(
        range(n),
        key=lambda position: min(
            grid.flat_index(index) for index in active_groups[position].members
        ),
    )
    canonical_rank = {position: rank for rank, position in enumerate(canonical_order)}
    bit_weights = np.asarray(
        [1 << (n - 1 - canonical_rank[position]) for position in range(n)],
        dtype=np.uint64,
    )
    canonical_score = -1
    accepted_count = 0
    for combinations in _combination_batches(n, required):
        centroid = centroid_offset[:, None] + np.sum(
            centroid_matrix[:, combinations], axis=2
        )
        valid = np.linalg.norm(centroid, axis=0) <= centroid_limit + 1.0e-12
        for coefficients, lower, upper in extra_linear:
            values = np.sum(coefficients[combinations], axis=1)
            valid &= values >= lower - 1.0e-10
            valid &= values <= upper + 1.0e-10
        if not np.any(valid):
            continue
        moment = moment_offset[:, None] + np.sum(moment_matrix[:, combinations], axis=2)
        norms = np.linalg.norm(moment, axis=0)
        valid &= norms <= (best_moment + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-12)
        if not np.any(valid):
            continue
        accepted_count += int(np.count_nonzero(valid))
        scores = np.sum(bit_weights[combinations], axis=1, dtype=np.uint64)
        canonical_score = max(canonical_score, int(np.max(scores[valid])))
    if canonical_score < 0:
        raise CandidateInfeasible("small exact canonical selection did not close")
    selection = np.asarray(
        [
            float(bool(canonical_score & (1 << (n - 1 - canonical_rank[position]))))
            for position in range(n)
        ],
        dtype=np.float64,
    )
    centroid_final = float(
        np.linalg.norm(centroid_offset + centroid_matrix @ selection)
    )
    moment_final = float(np.linalg.norm(moment_offset + moment_matrix @ selection))
    if int(np.rint(selection.sum())) != required:
        raise CandidateInfeasible("small exact selection has wrong cardinality")
    if centroid_final > centroid_limit + 1.0e-10:
        raise CandidateInfeasible("small exact selection violates centroid prefix")
    if moment_final > best_moment + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-10:
        raise CandidateInfeasible("small exact selection violates moment prefix")
    return selection, {
        "method": "EXACT_FIXED_CARDINALITY_COMPLETE_ENUMERATION",
        "variable_count": n,
        "required_count": required,
        "combination_count": combination_count,
        "hard_feasible_combination_count": hard_feasible_count,
        "accepted_canonical_combination_count": accepted_count,
        "objective_lower_bound": best_moment,
        "objective_upper_bound": best_moment,
        "objective_bracket_width": 0.0,
        "objective_bisection_tolerance": 0.0,
        "final_selected_objective": moment_final,
        "centroid_prefix_norm": centroid_final,
    }


def _solve_meet_in_middle_exact(
    grid: GridSpec,
    active_groups: Sequence[CellGroup],
    required: int,
    centroid_matrix: FloatArray,
    centroid_offset: FloatArray,
    centroid_limit: float,
    moment_matrix: FloatArray,
    moment_offset: FloatArray,
    moment_limit: float,
    extra_linear: Sequence[tuple[FloatArray, float, float]],
) -> tuple[FloatArray, dict[str, Any]] | None:
    """Exhaust 25--32 binary groups with a centroid-radius join."""

    n = len(active_groups)
    if n <= 24 or n > 32:
        return None
    canonical_order = sorted(
        range(n),
        key=lambda position: min(
            grid.flat_index(index) for index in active_groups[position].members
        ),
    )
    canonical_rank = {position: rank for rank, position in enumerate(canonical_order)}
    canonical_bits = np.asarray(
        [1 << (n - 1 - canonical_rank[position]) for position in range(n)],
        dtype=np.uint64,
    )
    split = n // 2
    halves = [
        np.arange(0, split, dtype=np.int64),
        np.arange(split, n, dtype=np.int64),
    ]

    def subset_table(positions: NDArray[np.int64]) -> dict[str, Any]:
        counts = np.zeros(1, dtype=np.uint8)
        centroid = np.zeros((centroid_matrix.shape[0], 1), dtype=np.float64)
        moment = np.zeros((moment_matrix.shape[0], 1), dtype=np.float64)
        extra = np.zeros((len(extra_linear), 1), dtype=np.float64)
        scores = np.zeros(1, dtype=np.uint64)
        for position in positions:
            counts = np.concatenate((counts, counts + 1))
            centroid = np.concatenate(
                (
                    centroid,
                    centroid + centroid_matrix[:, int(position), None],
                ),
                axis=1,
            )
            moment = np.concatenate(
                (
                    moment,
                    moment + moment_matrix[:, int(position), None],
                ),
                axis=1,
            )
            if extra_linear:
                column = np.asarray(
                    [
                        coefficients[int(position)]
                        for coefficients, _, _ in extra_linear
                    ],
                    dtype=np.float64,
                )
                extra = np.concatenate((extra, extra + column[:, None]), axis=1)
            scores = np.concatenate((scores, scores | canonical_bits[int(position)]))
        return {
            "counts": counts,
            "centroid": centroid,
            "moment": moment,
            "extra": extra,
            "scores": scores,
        }

    tables = [subset_table(positions) for positions in halves]
    candidate_records: list[tuple[FloatArray, NDArray[np.uint64]]] = []
    best_moment = math.inf
    centroid_join_pair_count = 0
    hard_feasible_pair_count = 0
    right_chunk_size = 20_000
    minimum_left_count = max(0, required - len(halves[1]))
    maximum_left_count = min(required, len(halves[0]))
    for left_count in range(minimum_left_count, maximum_left_count + 1):
        right_count = required - left_count
        left_indices = np.flatnonzero(tables[0]["counts"] == left_count)
        right_indices = np.flatnonzero(tables[1]["counts"] == right_count)
        if left_indices.size == 0 or right_indices.size == 0:
            continue
        left_centroid = tables[0]["centroid"][:, left_indices]
        left_tree = cKDTree(left_centroid.T)
        for start in range(0, right_indices.size, right_chunk_size):
            right_chunk = right_indices[start : start + right_chunk_size]
            right_targets = (
                -centroid_offset[:, None] - tables[1]["centroid"][:, right_chunk]
            )
            right_tree = cKDTree(right_targets.T)
            pairs = left_tree.sparse_distance_matrix(
                right_tree,
                centroid_limit + 1.0e-12,
                output_type="coo_matrix",
            )
            if pairs.nnz == 0:
                continue
            left = left_indices[np.asarray(pairs.row, dtype=np.int64)]
            right = right_chunk[np.asarray(pairs.col, dtype=np.int64)]
            centroid_join_pair_count += int(left.size)
            centroid = (
                centroid_offset[:, None]
                + tables[0]["centroid"][:, left]
                + tables[1]["centroid"][:, right]
            )
            valid = np.linalg.norm(centroid, axis=0) <= centroid_limit + 1.0e-12
            for constraint_index, (_, lower, upper) in enumerate(extra_linear):
                values = (
                    tables[0]["extra"][constraint_index, left]
                    + tables[1]["extra"][constraint_index, right]
                )
                valid &= values >= lower - 1.0e-10
                valid &= values <= upper + 1.0e-10
            if not np.any(valid):
                continue
            left = left[valid]
            right = right[valid]
            moment = (
                moment_offset[:, None]
                + tables[0]["moment"][:, left]
                + tables[1]["moment"][:, right]
            )
            norms = np.linalg.norm(moment, axis=0)
            valid_moment = norms <= moment_limit + SOC_CUT_TOLERANCE
            if not np.any(valid_moment):
                continue
            norms = norms[valid_moment]
            left = left[valid_moment]
            right = right[valid_moment]
            hard_feasible_pair_count += int(norms.size)
            chunk_minimum = float(np.min(norms))
            if chunk_minimum < best_moment:
                best_moment = chunk_minimum
                retained: list[tuple[FloatArray, NDArray[np.uint64]]] = []
                for old_norms, old_scores in candidate_records:
                    keep = old_norms <= (
                        best_moment + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-12
                    )
                    if np.any(keep):
                        retained.append((old_norms[keep], old_scores[keep]))
                candidate_records = retained
            keep = norms <= (best_moment + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-12)
            if np.any(keep):
                scores = np.bitwise_or(
                    tables[0]["scores"][left[keep]],
                    tables[1]["scores"][right[keep]],
                )
                candidate_records.append((norms[keep], scores))
    if not math.isfinite(best_moment):
        raise CandidateInfeasible(
            "meet-in-the-middle enumeration found no hard-gate-feasible mask"
        )
    canonical_score = max(
        int(
            np.max(
                scores[
                    norms <= best_moment + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-12
                ]
            )
        )
        for norms, scores in candidate_records
        if np.any(norms <= best_moment + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-12)
    )
    selection = np.asarray(
        [
            float(bool(canonical_score & int(canonical_bits[position])))
            for position in range(n)
        ],
        dtype=np.float64,
    )
    centroid_final = float(
        np.linalg.norm(centroid_offset + centroid_matrix @ selection)
    )
    moment_final = float(np.linalg.norm(moment_offset + moment_matrix @ selection))
    if int(np.rint(selection.sum())) != required:
        raise CandidateInfeasible("meet-in-the-middle selection has wrong cardinality")
    if centroid_final > centroid_limit + 1.0e-10:
        raise CandidateInfeasible(
            "meet-in-the-middle selection violates centroid prefix"
        )
    if moment_final > best_moment + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE + 1.0e-10:
        raise CandidateInfeasible("meet-in-the-middle selection violates moment prefix")
    return selection, {
        "method": "EXACT_MEET_IN_THE_MIDDLE_CENTROID_RADIUS_ENUMERATION",
        "variable_count": n,
        "required_count": required,
        "half_variable_counts": [len(half) for half in halves],
        "half_subset_counts": [int(table["counts"].size) for table in tables],
        "centroid_join_pair_count": centroid_join_pair_count,
        "hard_feasible_pair_count": hard_feasible_pair_count,
        "retained_canonical_candidate_count": int(
            sum(len(norms) for norms, _ in candidate_records)
        ),
        "objective_lower_bound": best_moment,
        "objective_upper_bound": best_moment,
        "objective_bracket_width": 0.0,
        "objective_bisection_tolerance": 0.0,
        "final_selected_objective": moment_final,
        "centroid_prefix_norm": centroid_final,
    }


def _select_boundary_groups(
    grid: GridSpec,
    capsule: Capsule,
    mandatory_groups: Sequence[CellGroup],
    boundary_groups: Sequence[CellGroup],
    required: int,
    total_cell_count: int,
) -> tuple[list[CellGroup], dict[str, Any]]:
    """Apply hard gates first, then the four non-volume lexicographic stages."""

    n = len(boundary_groups)
    radius = capsule.radius_m
    exact_tensor = exact_capsule_second_moment_tensor_m5(capsule)
    tensor_norm = float(np.linalg.norm(exact_tensor))
    base_offset = _sum_group_offsets(mandatory_groups) / (total_cell_count * radius)
    centroid_matrix = np.asarray(
        [group.offset_sum_m for group in boundary_groups], dtype=np.float64
    ).T / (total_cell_count * radius)
    base_outer = _sum_group_outer(mandatory_groups)
    moment_offset_full = (base_outer * grid.cell_volume_m3 - exact_tensor).reshape(
        -1
    ) / tensor_norm
    moment_matrix_full = np.asarray(
        [
            np.asarray(group.offset_outer_sum_m2).reshape(-1)
            * grid.cell_volume_m3
            / tensor_norm
            for group in boundary_groups
        ],
        dtype=np.float64,
    ).T
    moment_matrix, moment_offset, initial_moment_compression = _compress_affine_norm(
        moment_matrix_full, moment_offset_full
    )
    raw_cost = np.asarray(
        [group.cost_delta_m2 for group in boundary_groups], dtype=np.float64
    )
    shifted_cost = raw_cost - float(np.min(raw_cost))
    cost_scale = max(float(np.max(shifted_cost)), np.finfo(float).tiny)
    normalized_cost = shifted_cost / cost_scale
    centroid_directions = _soc_directions(3)
    moment_directions = _soc_directions(moment_matrix.shape[0])
    z_reflection_constant = 2.0 * capsule.centre_xyz_m[2] / grid.dz - 1.0
    z_lattice_reflection_aligned = (
        abs(z_reflection_constant - round(z_reflection_constant)) <= 1.0e-10
    )
    full_alignment_constraints: list[tuple[FloatArray, float, float]] = []
    if z_lattice_reflection_aligned:
        target = float(-base_offset[2])
        full_alignment_constraints.append(
            (
                centroid_matrix[2],
                target - ALIGNED_CENTROID_COMPONENT_TOLERANCE,
                target + ALIGNED_CENTROID_COMPONENT_TOLERANCE,
            )
        )

    if required == 0 or required == n:
        selection = np.full(n, float(required == n), dtype=np.float64)
        centroid_norm = float(np.linalg.norm(base_offset + centroid_matrix @ selection))
        moment_norm = float(np.linalg.norm(moment_offset + moment_matrix @ selection))
        if centroid_norm > 0.05 or moment_norm > 0.05:
            raise CandidateInfeasible("volume-optimal fixed mask violates hard gates")
        chosen = [
            group
            for group, value in zip(boundary_groups, selection, strict=True)
            if value > 0.5
        ]
        return chosen, {
            "boundary_group_count": n,
            "boundary_groups_selected": required,
            "selection_method": "FIXED_BY_VOLUME_OBJECTIVE",
            "final_centroid_objective_normalized": centroid_norm,
            "final_second_moment_objective_normalized": moment_norm,
        }

    stage2_selection, stage2 = _solve_linear_with_socs(
        required,
        normalized_cost,
        centroid_matrix,
        base_offset,
        0.05,
        centroid_directions,
        moment_matrix,
        moment_offset,
        0.05,
        moment_directions,
        extra_linear=full_alignment_constraints,
    )
    cost_optimum = float(normalized_cost @ stage2_selection)
    unconstrained = np.zeros(n, dtype=np.float64)
    unconstrained[:required] = 1.0
    changed_positions = np.flatnonzero(stage2_selection != unconstrained)
    cutoff_key = boundary_groups[required - 1].cost_key
    active_keys = {cutoff_key}
    active_keys.update(
        boundary_groups[int(index)].cost_key for index in changed_positions
    )
    full_cost_constraint = (
        normalized_cost,
        -np.inf,
        cost_optimum + COST_OBJECTIVE_TOLERANCE,
    )
    full_prefix_constraints = [
        full_cost_constraint,
        *full_alignment_constraints,
    ]
    outside_certification_iterations: list[dict[str, Any]] = []
    for certification_iteration in range(1, 33):
        tentative_active = {
            position
            for position, group in enumerate(boundary_groups)
            if group.cost_key in active_keys
        }
        outside = [
            position for position in range(n) if position not in tentative_active
        ]
        if not outside:
            outside_certification_iterations.append(
                {
                    "iteration": certification_iteration,
                    "outside_group_count": 0,
                    "changed_outside_group_count": 0,
                }
            )
            break
        difference_objective = np.zeros(n, dtype=np.float64)
        for position in outside:
            difference_objective[position] = (
                1.0 if stage2_selection[position] > 0.5 else -1.0
            )
        alternative, _ = _solve_linear_with_socs(
            required,
            difference_objective,
            centroid_matrix,
            base_offset,
            0.05,
            centroid_directions,
            moment_matrix,
            moment_offset,
            0.05,
            moment_directions,
            extra_linear=full_prefix_constraints,
        )
        changed_outside = [
            position
            for position in outside
            if alternative[position] != stage2_selection[position]
        ]
        outside_certification_iterations.append(
            {
                "iteration": certification_iteration,
                "outside_group_count": len(outside),
                "changed_outside_group_count": len(changed_outside),
            }
        )
        if not changed_outside:
            break
        active_keys.update(
            boundary_groups[position].cost_key for position in changed_outside
        )
    else:
        raise CandidateInfeasible("cost-optimal active-key discovery did not close")
    candidate_active_positions = [
        position
        for position, group in enumerate(boundary_groups)
        if group.cost_key in active_keys
    ]
    # Retain every group in a cost level shown capable of changing.  Proving
    # each variable individually mutable was only a performance reduction; in
    # the general shifted phase it repeated the same large SOC feasibility
    # problem dozens of times.  Conservatively keeping the whole discovered
    # level preserves (and can only enlarge) the exact cost-optimal search
    # family, while the prefix constraint still forbids higher cost.
    active_positions = list(candidate_active_positions)
    active_position_set = set(active_positions)
    fixed_selected_positions = [
        position
        for position, value in enumerate(stage2_selection)
        if value > 0.5 and position not in active_position_set
    ]
    active_groups = [boundary_groups[position] for position in active_positions]
    fixed_selected_groups = [
        boundary_groups[position] for position in fixed_selected_positions
    ]
    required_active = required - len(fixed_selected_groups)
    if not 0 <= required_active <= len(active_groups):
        raise CandidateInfeasible("active cost-level reduction is inconsistent")

    # Replay the globally optimal cost on every variable in a discovered
    # active cost level.  The global maximum-difference solves certify groups
    # outside those levels fixed; groups inside are retained conservatively.
    n = len(active_groups)
    base_groups = [*mandatory_groups, *fixed_selected_groups]
    base_offset = _sum_group_offsets(base_groups) / (total_cell_count * radius)
    centroid_matrix = np.asarray(
        [group.offset_sum_m for group in active_groups], dtype=np.float64
    ).T / (total_cell_count * radius)
    base_outer = _sum_group_outer(base_groups)
    moment_offset_full = (base_outer * grid.cell_volume_m3 - exact_tensor).reshape(
        -1
    ) / tensor_norm
    moment_matrix_full = np.asarray(
        [
            np.asarray(group.offset_outer_sum_m2).reshape(-1)
            * grid.cell_volume_m3
            / tensor_norm
            for group in active_groups
        ],
        dtype=np.float64,
    ).T
    moment_matrix, moment_offset, active_moment_compression = _compress_affine_norm(
        moment_matrix_full, moment_offset_full
    )
    moment_directions = _soc_directions(moment_matrix.shape[0])
    active_cost = normalized_cost[np.asarray(active_positions, dtype=np.int64)]
    fixed_cost = float(
        normalized_cost[np.asarray(fixed_selected_positions, dtype=np.int64)].sum()
    )
    cost_constraint = (
        active_cost,
        -np.inf,
        cost_optimum - fixed_cost + COST_OBJECTIVE_TOLERANCE,
    )
    active_alignment_constraints: list[tuple[FloatArray, float, float]] = []
    if z_lattice_reflection_aligned:
        target = float(-base_offset[2])
        active_alignment_constraints.append(
            (
                centroid_matrix[2],
                target - ALIGNED_CENTROID_COMPONENT_TOLERANCE,
                target + ALIGNED_CENTROID_COMPONENT_TOLERANCE,
            )
        )
    lex_prefix_constraints = [cost_constraint, *active_alignment_constraints]
    if n == 0:
        centroid_final = float(np.linalg.norm(base_offset))
        moment_final = float(np.linalg.norm(moment_offset))
        if centroid_final > 0.05 + SOC_CUT_TOLERANCE:
            raise CandidateInfeasible(
                "cost-optimal fixed mask violates the centroid gate"
            )
        if moment_final > 0.05 + SOC_CUT_TOLERANCE:
            raise CandidateInfeasible(
                "cost-optimal fixed mask violates the second-moment gate"
            )
        return fixed_selected_groups, {
            "boundary_group_count": len(boundary_groups),
            "boundary_groups_selected": required,
            "selection_method": "FIXED_AFTER_GLOBAL_COST_OPTIMUM_CERTIFICATION",
            "stage2_misclassification_cost": stage2,
            "stage2_normalized_cost_optimum": cost_optimum,
            "stage2_raw_cost_delta_m2": float(raw_cost @ stage2_selection),
            "cost_objective_tolerance": COST_OBJECTIVE_TOLERANCE,
            "moment_affine_compression": {
                "global_stage2": initial_moment_compression,
                "active_lexicographic_problem": active_moment_compression,
            },
            "aligned_centroid_component_constraints": {
                "z_lattice_reflection_aligned": z_lattice_reflection_aligned,
                "z_reflection_index_constant": z_reflection_constant,
                "z_centroid_component_fixed_to_zero": (z_lattice_reflection_aligned),
                "component_tolerance": ALIGNED_CENTROID_COMPONENT_TOLERANCE,
            },
            "active_cost_level_reduction": {
                "globally_solved_boundary_group_count": len(boundary_groups),
                "global_stage2_changed_group_count": int(changed_positions.size),
                "candidate_active_cost_level_group_count": len(
                    candidate_active_positions
                ),
                "certified_mutable_group_count": 0,
                "active_group_count": 0,
                "fixed_selected_group_count": len(fixed_selected_groups),
                "active_groups_required": 0,
                "active_cost_keys": sorted(active_keys),
                "outside_cost_level_certification": (outside_certification_iterations),
                "outside_groups_certified_fixed": True,
                "candidate_active_groups_tested_individually": len(
                    candidate_active_positions
                ),
            },
            "stage3_centroid": {"method": "FIXED_SELECTION"},
            "stage3_centroid_optimum_normalized": centroid_final,
            "stage3_fixed_centroid_axes": [0, 1, 2],
            "stage3_free_centroid_axes": [],
            "stage4_second_moment": {"method": "FIXED_SELECTION"},
            "stage4_second_moment_optimum_normalized": moment_final,
            "final_centroid_objective_normalized": centroid_final,
            "final_second_moment_objective_normalized": moment_final,
            "lexicographic_objective_tolerance": (LEXICOGRAPHIC_OBJECTIVE_TOLERANCE),
            "soc_cut_tolerance": SOC_CUT_TOLERANCE,
            "canonical_block_bits": CANONICAL_BLOCK_BITS,
            "canonical_block_reports": [],
        }
    one_line_exact = _solve_one_line_combined_exact(
        grid,
        active_groups,
        required_active,
        centroid_matrix,
        base_offset,
        moment_matrix,
        moment_offset,
        0.05,
        lex_prefix_constraints,
    )
    combined_exact = one_line_exact
    if combined_exact is None:
        combined_exact = _solve_two_layer_combined_exact(
            grid,
            active_groups,
            required_active,
            centroid_matrix,
            base_offset,
            moment_matrix,
            moment_offset,
            0.05,
            lex_prefix_constraints,
        )
    if combined_exact is None:
        try:
            stage3_selection, stage3 = _minimize_soc_by_bisection(
                required_active,
                centroid_matrix,
                base_offset,
                0.05,
                centroid_directions,
                moment_matrix,
                moment_offset,
                0.05,
                moment_directions,
                extra_linear=lex_prefix_constraints,
            )
        except CandidateInfeasible as error:
            raise CandidateInfeasible(f"stage3 centroid objective: {error}") from error
    else:
        (
            stage3_selection,
            stage3,
            one_line_stage4_selection,
            one_line_stage4,
        ) = combined_exact
    centroid_optimum = float(
        np.linalg.norm(base_offset + centroid_matrix @ stage3_selection)
    )
    stage4_centroid_limit = centroid_optimum + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE
    fixed_centroid_axes = {
        axis
        for axis, fixed in enumerate(
            (
                capsule.centre_xyz_m[0] == 0.0,
                capsule.centre_xyz_m[1] == 0.0,
                z_lattice_reflection_aligned,
            )
        )
        if fixed
    }
    free_centroid_axes = [axis for axis in range(3) if axis not in fixed_centroid_axes]
    try:
        if combined_exact is not None:
            stage4_selection = one_line_stage4_selection
            stage4 = one_line_stage4
        else:
            layered_exact = _solve_two_layer_x_polynomial_exact(
                grid,
                active_groups,
                required_active,
                centroid_matrix,
                base_offset,
                stage4_centroid_limit,
                moment_matrix,
                moment_offset,
                0.05,
                lex_prefix_constraints,
            )
            if layered_exact is not None:
                stage4_selection, stage4 = layered_exact
            else:
                small_exact = _solve_small_exact_enumeration(
                    grid,
                    active_groups,
                    required_active,
                    centroid_matrix,
                    base_offset,
                    stage4_centroid_limit,
                    moment_matrix,
                    moment_offset,
                    0.05,
                    lex_prefix_constraints,
                )
                if small_exact is not None:
                    stage4_selection, stage4 = small_exact
                else:
                    meet_in_middle = _solve_meet_in_middle_exact(
                        grid,
                        active_groups,
                        required_active,
                        centroid_matrix,
                        base_offset,
                        stage4_centroid_limit,
                        moment_matrix,
                        moment_offset,
                        0.05,
                        lex_prefix_constraints,
                    )
                    if meet_in_middle is not None:
                        stage4_selection, stage4 = meet_in_middle
                    else:
                        stage4_selection, stage4 = _minimize_soc_by_bisection(
                            required_active,
                            moment_matrix,
                            moment_offset,
                            0.05,
                            moment_directions,
                            centroid_matrix,
                            base_offset,
                            stage4_centroid_limit,
                            centroid_directions,
                            extra_linear=lex_prefix_constraints,
                            initial_feasible=stage3_selection,
                        )
                        stage4["objective"] = "SECOND_MOMENT_RELATIVE_FROBENIUS"
    except CandidateInfeasible as error:
        context = {
            "active_group_count": n,
            "active_groups_required": required_active,
            "centroid_optimum": centroid_optimum,
            "stage3": stage3,
        }
        raise CandidateInfeasible(
            "stage4 moment objective: "
            f"{error}; context={json.dumps(context, sort_keys=True)}"
        ) from error
    moment_optimum = float(
        np.linalg.norm(moment_offset + moment_matrix @ stage4_selection)
    )
    stage4_includes_canonical_selection = str(stage4.get("method", "")).startswith(
        "EXACT_"
    )
    canonical_order = (
        []
        if stage4_includes_canonical_selection
        else sorted(
            range(n),
            key=lambda position: min(
                grid.flat_index(index) for index in active_groups[position].members
            ),
        )
    )
    centroid_constraint_limit = stage4_centroid_limit
    moment_constraint_limit = moment_optimum + LEXICOGRAPHIC_OBJECTIVE_TOLERANCE
    lex_constraints: list[tuple[FloatArray, float, float]] = list(
        lex_prefix_constraints
    )
    canonical_fixed: dict[int, int] = {}
    selection = stage4_selection
    block_reports: list[dict[str, Any]] = (
        [
            {
                "method": "CANONICAL_SELECTION_COMPLETED_INSIDE_EXACT_STAGE4",
                "stage4_method": stage4["method"],
            }
        ]
        if stage4_includes_canonical_selection
        else []
    )
    for block_number, start in enumerate(
        range(0, len(canonical_order), CANONICAL_BLOCK_BITS), start=1
    ):
        positions = canonical_order[start : start + CANONICAL_BLOCK_BITS]
        block_objective = np.zeros(n, dtype=np.float64)
        block_score = np.zeros(n, dtype=np.float64)
        for rank, position in enumerate(positions):
            weight = float(2 ** (len(positions) - 1 - rank))
            block_objective[position] = -weight
            block_score[position] = weight
        try:
            selection, block_report = _solve_linear_with_socs(
                required_active,
                block_objective,
                centroid_matrix,
                base_offset,
                centroid_constraint_limit,
                centroid_directions,
                moment_matrix,
                moment_offset,
                moment_constraint_limit,
                moment_directions,
                extra_linear=lex_constraints,
                fixed=canonical_fixed,
                acceptance_tolerance=1.0e-12,
            )
        except CandidateInfeasible as error:
            raise CandidateInfeasible(
                f"canonical block {block_number} infeasible: {error}"
            ) from error
        score = float(block_score @ selection)
        rounded_score = float(round(score))
        if abs(score - rounded_score) > 1.0e-6:
            raise CandidateInfeasible("canonical block score is not integral")
        for position in positions:
            canonical_fixed[position] = int(selection[position] > 0.5)
        block_reports.append(
            {
                "block": block_number,
                "variable_count": len(positions),
                "binary_score": int(rounded_score),
                "solver": block_report,
            }
        )
    centroid_final = float(np.linalg.norm(base_offset + centroid_matrix @ selection))
    moment_final = float(np.linalg.norm(moment_offset + moment_matrix @ selection))
    if centroid_final > 0.05 + SOC_CUT_TOLERANCE:
        raise CandidateInfeasible("canonical tie selection violates centroid gate")
    if moment_final > 0.05 + SOC_CUT_TOLERANCE:
        raise CandidateInfeasible("canonical tie selection violates moment gate")
    chosen = [
        group
        for group, value in zip(active_groups, selection, strict=True)
        if value > 0.5
    ]
    chosen = [*fixed_selected_groups, *chosen]
    return chosen, {
        "boundary_group_count": len(boundary_groups),
        "boundary_groups_selected": required,
        "selection_method": "MISOCP_OUTER_APPROXIMATION_WITH_BLOCK_EXACT_CANONICAL_BITS",
        "stage2_misclassification_cost": stage2,
        "stage2_normalized_cost_optimum": cost_optimum,
        "stage2_raw_cost_delta_m2": float(raw_cost @ stage2_selection),
        "moment_affine_compression": {
            "global_stage2": initial_moment_compression,
            "active_lexicographic_problem": active_moment_compression,
        },
        "cost_objective_tolerance": COST_OBJECTIVE_TOLERANCE,
        "aligned_centroid_component_constraints": {
            "z_lattice_reflection_aligned": z_lattice_reflection_aligned,
            "z_reflection_index_constant": z_reflection_constant,
            "z_centroid_component_fixed_to_zero": z_lattice_reflection_aligned,
            "component_tolerance": ALIGNED_CENTROID_COMPONENT_TOLERANCE,
        },
        "active_cost_level_reduction": {
            "globally_solved_boundary_group_count": len(boundary_groups),
            "global_stage2_changed_group_count": int(changed_positions.size),
            "candidate_active_cost_level_group_count": len(candidate_active_positions),
            "certified_mutable_group_count": None,
            "conservatively_retained_active_group_count": len(active_groups),
            "active_group_count": len(active_groups),
            "fixed_selected_group_count": len(fixed_selected_groups),
            "active_groups_required": required_active,
            "active_cost_keys": sorted(active_keys),
            "outside_cost_level_certification": outside_certification_iterations,
            "outside_groups_certified_fixed": True,
            "candidate_active_groups_tested_individually": 0,
        },
        "stage3_centroid": stage3,
        "stage3_centroid_optimum_normalized": centroid_optimum,
        "stage3_fixed_centroid_axes": sorted(fixed_centroid_axes),
        "stage3_free_centroid_axes": free_centroid_axes,
        "stage4_second_moment": stage4,
        "stage4_second_moment_optimum_normalized": moment_optimum,
        "final_centroid_objective_normalized": centroid_final,
        "final_second_moment_objective_normalized": moment_final,
        "lexicographic_objective_tolerance": LEXICOGRAPHIC_OBJECTIVE_TOLERANCE,
        "soc_cut_tolerance": SOC_CUT_TOLERANCE,
        "canonical_block_bits": CANONICAL_BLOCK_BITS,
        "stage4_includes_canonical_selection": (stage4_includes_canonical_selection),
        "canonical_block_reports": block_reports,
    }


def _selection_problem_sha256(
    grid: GridSpec,
    capsule: Capsule,
    mandatory: Sequence[CellGroup],
    boundary: Sequence[CellGroup],
) -> str:
    payload = {
        "grid": [*grid.shape, grid.radius_m, grid.height_m],
        "capsule": [
            capsule.half_length_m,
            capsule.radius_m,
            *capsule.centre_xyz_m,
        ],
        "mandatory": [group.members for group in mandatory],
        "boundary": [
            {"members": group.members, "cost_key": group.cost_key} for group in boundary
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_g1_mask(
    grid: GridSpec,
    capsule: Capsule,
    *,
    enumeration_mode: str = "c",
    enumeration_seed: int = 20260828,
    selection_cache: MutableMapping[str, tuple[tuple[Index3, ...], dict[str, Any]]]
    | None = None,
) -> G1Build:
    """Build one G1 mask without changing or importing the dynamic solver."""

    canonical = canonicalize_capsule(capsule)
    mandatory, boundary, construction = _candidate_groups(
        grid,
        canonical,
        enumeration_mode=enumeration_mode,
        enumeration_seed=enumeration_seed,
    )
    problem_sha = _selection_problem_sha256(grid, canonical, mandatory, boundary)
    exact_volume = exact_capsule_volume_m3(canonical)
    orbit_size = int(construction["orbit_size"])
    mandatory_cells = sum(group.size for group in mandatory)
    feasible_k = range(len(boundary) + 1)
    selected_boundary_group_count = min(
        feasible_k,
        key=lambda count: (
            abs(
                (mandatory_cells + orbit_size * count) * grid.cell_volume_m3
                - exact_volume
            ),
            count,
        ),
    )
    total_cell_count = mandatory_cells + orbit_size * selected_boundary_group_count
    if selected_boundary_group_count < 0 or selected_boundary_group_count > len(
        boundary
    ):
        raise CandidateInfeasible("volume-optimal boundary count is unavailable")
    selected: list[CellGroup]
    optimisation: dict[str, Any]
    cache_hit = False
    if selection_cache is not None and problem_sha in selection_cache:
        cached_members, cached_optimisation = selection_cache[problem_sha]
        by_members = {group.members: group for group in mandatory + boundary}
        try:
            selected = [by_members[members] for members in cached_members]
        except KeyError as error:
            raise CandidateInfeasible(
                "cached selection does not match candidates"
            ) from error
        optimisation = dict(cached_optimisation)
        cache_hit = True
    else:
        chosen_boundary, staged_optimisation = _select_boundary_groups(
            grid,
            canonical,
            mandatory,
            boundary,
            selected_boundary_group_count,
            total_cell_count,
        )
        selected = [*mandatory, *chosen_boundary]
        optimisation = {
            "volume_objective": {
                "mandatory_cell_count": mandatory_cells,
                "selected_boundary_group_count": selected_boundary_group_count,
                "total_cell_count": total_cell_count,
                "absolute_volume_error_m3": abs(
                    total_cell_count * grid.cell_volume_m3 - exact_volume
                ),
            },
            "misclassification_cost_objective": {
                "cost_quantum_m2": COST_QUANTUM_M2,
                "selected_cost_delta_m2": float(
                    math.fsum(group.cost_delta_m2 for group in selected)
                ),
            },
            "staged_selection": staged_optimisation,
        }
        if selection_cache is not None:
            selection_cache[problem_sha] = (
                tuple(group.members for group in selected),
                optimisation,
            )
    selected_indices = tuple(
        sorted(index for group in selected for index in group.members)
    )
    if len(selected_indices) != total_cell_count:
        raise CandidateInfeasible("selected mask cell count is inconsistent")
    mask = np.zeros(grid.shape, dtype=bool)
    if selected_indices:
        array_indices = np.asarray(selected_indices, dtype=np.int64)
        mask[array_indices[:, 0], array_indices[:, 1], array_indices[:, 2]] = True
    x = grid.x_centres()
    y = grid.y_centres()
    z = grid.z_centres()
    coordinates = np.asarray(
        [(x[i], y[j], z[k]) for i, j, k in selected_indices],
        dtype=np.float64,
    )
    centre = np.asarray(canonical.centre_xyz_m, dtype=np.float64)
    offsets = coordinates - centre[None, :]
    centroid = np.mean(coordinates, axis=0)
    centroid_error = centroid - centre
    voxel_tensor = offsets.T @ offsets * grid.cell_volume_m3
    exact_tensor = exact_capsule_second_moment_tensor_m5(canonical)
    volume = total_cell_count * grid.cell_volume_m3
    normalized = {
        "bar_volume_relative": abs(volume - exact_volume) / exact_volume,
        "centroid_over_bar_radius": float(
            np.linalg.norm(centroid_error) / canonical.radius_m
        ),
        "second_moment_relative_frobenius": float(
            np.linalg.norm(voxel_tensor - exact_tensor) / np.linalg.norm(exact_tensor)
        ),
    }
    component_count = six_connected_component_count(selected_indices)
    canonical_selected_hash = hashlib.sha256(
        json.dumps(selected_indices, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    report: dict[str, Any] = {
        "candidate_id": G1_CANDIDATE_ID,
        "grid": list(grid.shape),
        "grid_spacing_xyz_m": [grid.dx, grid.dy, grid.dz],
        "cell_half_diagonal_m": grid.cell_half_diagonal_m,
        "input_capsule": {
            "half_length_m": capsule.half_length_m,
            "radius_m": capsule.radius_m,
            "centre_xyz_m": list(capsule.centre_xyz_m),
        },
        "canonical_capsule": {
            "half_length_m": canonical.half_length_m,
            "radius_m": canonical.radius_m,
            "centre_xyz_m": list(canonical.centre_xyz_m),
        },
        "canonicalisation_maximum_absolute_change_m": max(
            abs(capsule.half_length_m - canonical.half_length_m),
            abs(capsule.radius_m - canonical.radius_m),
            *(
                abs(left - right)
                for left, right in zip(
                    capsule.centre_xyz_m,
                    canonical.centre_xyz_m,
                    strict=True,
                )
            ),
        ),
        "representation_protocol_id": REPRESENTATION_PROTOCOL_ID,
        "representation_quantum_m": REPRESENTATION_QUANTUM_M,
        "construction": construction,
        "selection_problem_sha256": problem_sha,
        "selection_cache_hit": cache_hit,
        "optimisation": optimisation,
        "mask_sha256": mask_sha256(mask),
        "canonical_selected_index_sha256": canonical_selected_hash,
        "bar_cell_count": total_cell_count,
        "exact_bar_volume_m3": exact_volume,
        "rasterized_bar_volume_m3": volume,
        "rasterized_bar_volume_relative_error": (volume - exact_volume) / exact_volume,
        "bar_mask_centroid_xyz_m": centroid.tolist(),
        "true_bar_centroid_xyz_m": centre.tolist(),
        "bar_mask_centroid_error_xyz_m": centroid_error.tolist(),
        "bar_mask_second_moment_tensor_m5": voxel_tensor.tolist(),
        "exact_bar_second_moment_tensor_m5": exact_tensor.tolist(),
        "normalized_geometry_errors": normalized,
        "solid_six_connected_component_count": component_count,
        "hard_geometry_gates": {
            "bar_volume": normalized["bar_volume_relative"] <= 0.01,
            "centroid": normalized["centroid_over_bar_radius"] <= 0.05,
            "second_moment": normalized["second_moment_relative_frobenius"] <= 0.05,
            "six_connected": component_count == 1,
        },
    }
    report["hard_geometry_gate_pass"] = all(report["hard_geometry_gates"].values())
    return G1Build(mask=mask, selected_indices=selected_indices, report=report)


def _quarter_disk_rectangle_area(radius: float, a: float, b: float) -> float:
    """Area of a radius-r disk inside [0,a] x [0,b]."""

    if radius <= 0.0 or a <= 0.0 or b <= 0.0:
        return 0.0
    a = min(float(a), radius)
    b = min(float(b), radius)
    corner = a * a + b * b
    if corner <= radius * radius:
        return a * b
    x0 = math.sqrt(max(radius * radius - b * b, 0.0))

    def primitive(x: float) -> float:
        root = math.sqrt(max(radius * radius - x * x, 0.0))
        return 0.5 * (
            x * root + radius * radius * math.asin(min(max(x / radius, -1.0), 1.0))
        )

    return b * x0 + primitive(a) - primitive(x0)


def _positive_segments(lower: float, upper: float) -> list[tuple[float, float]]:
    if upper <= lower:
        return []
    if upper <= 0.0:
        return [(-upper, -lower)]
    if lower >= 0.0:
        return [(lower, upper)]
    return [(0.0, -lower), (0.0, upper)]


def circle_rectangle_area(
    radius: float,
    y_lower: float,
    y_upper: float,
    z_lower: float,
    z_upper: float,
) -> float:
    """Exact-form disk/axis-aligned-rectangle intersection area."""

    result = 0.0
    for ya, yb in _positive_segments(y_lower, y_upper):
        for za, zb in _positive_segments(z_lower, z_upper):
            result += (
                _quarter_disk_rectangle_area(radius, yb, zb)
                - _quarter_disk_rectangle_area(radius, ya, zb)
                - _quarter_disk_rectangle_area(radius, yb, za)
                + _quarter_disk_rectangle_area(radius, ya, za)
            )
    rectangle_area = max(y_upper - y_lower, 0.0) * max(z_upper - z_lower, 0.0)
    return min(max(result, 0.0), rectangle_area)


def _global_reference_node_bounds(
    s_lower: float,
    s_upper: float,
    capsule: Capsule,
) -> tuple[float, ...]:
    """Bounds for both capsule caps over one axial subinterval."""

    a = capsule.segment_half_length_m
    r = capsule.radius_m

    def volume_integrand(s: float) -> float:
        return math.pi * max(r * r - s * s, 0.0)

    def axial_integrand(s: float) -> float:
        return (a + s) ** 2 * volume_integrand(s)

    def transverse_integrand(s: float) -> float:
        rho2 = max(r * r - s * s, 0.0)
        return 0.25 * math.pi * rho2**2

    critical = (-a + math.sqrt(a * a + 8.0 * r * r)) / 4.0
    sample_points = [s_lower, s_upper]
    if s_lower < critical < s_upper:
        sample_points.append(critical)
    v_values = [volume_integrand(value) for value in sample_points]
    x_values = [axial_integrand(value) for value in sample_points]
    t_values = [transverse_integrand(value) for value in sample_points]
    width = s_upper - s_lower
    values = (
        2.0 * width * min(v_values),
        2.0 * width * max(v_values),
        2.0 * width * min(x_values),
        2.0 * width * max(x_values),
        2.0 * width * min(t_values),
        2.0 * width * max(t_values),
    )
    return tuple(float(value) for value in values)


def adaptive_capsule_global_reference(
    capsule: Capsule,
    *,
    volume_relative_width_target: float = 1.0e-5,
    second_moment_relative_width_target: float = 1.0e-4,
) -> dict[str, Any]:
    """Bound whole-capsule volume and central moments by axial subdivision."""

    canonical = canonicalize_capsule(capsule)
    a = canonical.segment_half_length_m
    r = canonical.radius_m
    exact_volume = exact_capsule_volume_m3(canonical)
    exact_tensor = exact_capsule_second_moment_tensor_m5(canonical)
    tensor_norm = float(np.linalg.norm(exact_tensor))
    cylinder_volume = 2.0 * a * math.pi * r**2
    cylinder_xx = (2.0 / 3.0) * math.pi * r**2 * a**3
    cylinder_transverse = 0.5 * math.pi * a * r**4
    root_bounds = _global_reference_node_bounds(0.0, r, canonical)
    counter = itertools.count()

    def priority(bounds: Sequence[float]) -> float:
        moment_width = math.sqrt(
            (bounds[3] - bounds[2]) ** 2 + 2.0 * (bounds[5] - bounds[4]) ** 2
        )
        return max(
            (bounds[1] - bounds[0]) / exact_volume,
            moment_width / tensor_norm,
        )

    heap: list[tuple[float, int, float, float, tuple[float, ...]]] = [
        (-priority(root_bounds), next(counter), 0.0, r, root_bounds)
    ]
    running = np.asarray(root_bounds, dtype=np.float64)
    created = 1
    while True:
        volume_width = (running[1] - running[0]) / exact_volume
        moment_width = (
            math.sqrt(
                (running[3] - running[2]) ** 2 + 2.0 * (running[5] - running[4]) ** 2
            )
            / tensor_norm
        )
        if (
            volume_width <= volume_relative_width_target
            and moment_width <= second_moment_relative_width_target
        ):
            break
        if created >= G2_MAX_AXIAL_SUBCELLS:
            raise CandidateInfeasible("G2 global interval budget was not reached")
        _, _, lower, upper, old_bounds = heapq.heappop(heap)
        running -= np.asarray(old_bounds)
        midpoint = 0.5 * (lower + upper)
        for child_lower, child_upper in ((lower, midpoint), (midpoint, upper)):
            child_bounds = _global_reference_node_bounds(
                child_lower, child_upper, canonical
            )
            running += np.asarray(child_bounds)
            heapq.heappush(
                heap,
                (
                    -priority(child_bounds),
                    next(counter),
                    child_lower,
                    child_upper,
                    child_bounds,
                ),
            )
            created += 1
    leaf_bounds = [item[4] for item in heap]
    cap_sums = [
        math.fsum(bounds[index] for bounds in leaf_bounds) for index in range(6)
    ]
    volume_interval = [
        math.nextafter(cylinder_volume + cap_sums[0], -math.inf),
        math.nextafter(cylinder_volume + cap_sums[1], math.inf),
    ]
    xx_interval = [
        math.nextafter(cylinder_xx + cap_sums[2], -math.inf),
        math.nextafter(cylinder_xx + cap_sums[3], math.inf),
    ]
    transverse_interval = [
        math.nextafter(cylinder_transverse + cap_sums[4], -math.inf),
        math.nextafter(cylinder_transverse + cap_sums[5], math.inf),
    ]
    if not volume_interval[0] <= exact_volume <= volume_interval[1]:
        raise CandidateInfeasible(
            "G2 volume interval does not contain analytic identity"
        )
    if not xx_interval[0] <= exact_tensor[0, 0] <= xx_interval[1]:
        raise CandidateInfeasible("G2 axial-moment interval misses analytic identity")
    if not transverse_interval[0] <= exact_tensor[1, 1] <= transverse_interval[1]:
        raise CandidateInfeasible(
            "G2 transverse-moment interval misses analytic identity"
        )
    volume_relative_width = (volume_interval[1] - volume_interval[0]) / exact_volume
    moment_relative_width = (
        math.sqrt(
            (xx_interval[1] - xx_interval[0]) ** 2
            + 2.0 * (transverse_interval[1] - transverse_interval[0]) ** 2
        )
        / tensor_norm
    )
    cx, cy, cz = canonical.centre_xyz_m
    return {
        "candidate_id": G2_CANDIDATE_ID,
        "method": "ADAPTIVE_AXIAL_SUBCELLS_WITH_SIGNED_DISTANCE_CROSS_SECTION_BOUNDS",
        "diagnostic_only": True,
        "formal_solver_integration": False,
        "adaptive_leaf_count": len(heap),
        "adaptive_subcells_created": created,
        "volume_interval_m3": volume_interval,
        "volume_relative_interval_width": volume_relative_width,
        "centroid_interval_xyz_m": [[cx, cx], [cy, cy], [cz, cz]],
        "centroid_over_radius_interval_width": 0.0,
        "central_second_moment_interval_m5": [
            [xx_interval, [0.0, 0.0], [0.0, 0.0]],
            [[0.0, 0.0], transverse_interval, [0.0, 0.0]],
            [[0.0, 0.0], [0.0, 0.0], transverse_interval],
        ],
        "second_moment_relative_interval_width": moment_relative_width,
        "analytic_identity_containment": {
            "volume": True,
            "axial_second_moment": True,
            "transverse_second_moment": True,
            "centroid_and_cross_moments_by_capsule_reflection_symmetry": True,
        },
        "uncertainty_gates": {
            "volume": volume_relative_width <= 1.0e-5,
            "centroid": True,
            "second_moment": moment_relative_width <= 1.0e-4,
        },
    }


def _cell_axial_pieces(
    grid: GridSpec,
    capsule: Capsule,
    index: Index3,
) -> list[tuple[float, float, float, float, bool]]:
    """Return x pieces as (lo,hi,area_lo,area_hi,is_exact)."""

    i, j, k = index
    cx, cy, cz = capsule.centre_xyz_m
    x_lower = -grid.radius_m + i * grid.dx
    x_upper = x_lower + grid.dx
    y_lower = -grid.radius_m + j * grid.dy - cy
    y_upper = y_lower + grid.dy
    z_lower = k * grid.dz - cz
    z_upper = z_lower + grid.dz
    a = capsule.segment_half_length_m
    r = capsule.radius_m
    cuts = [
        x_lower,
        x_upper,
        cx - a - r,
        cx - a,
        cx + a,
        cx + a + r,
    ]
    cuts = sorted({value for value in cuts if x_lower <= value <= x_upper})
    if cuts[0] != x_lower:
        cuts.insert(0, x_lower)
    if cuts[-1] != x_upper:
        cuts.append(x_upper)
    pieces: list[tuple[float, float, float, float, bool]] = []
    for lower, upper in itertools.pairwise(cuts):
        if upper <= lower:
            continue
        midpoint = 0.5 * (lower + upper)
        axial = abs(midpoint - cx)
        if axial >= a + r:
            continue
        if axial <= a:
            area = circle_rectangle_area(r, y_lower, y_upper, z_lower, z_upper)
            pieces.append((lower, upper, area, area, True))
            continue

        def cross_radius(value: float) -> float:
            s = max(abs(value - cx) - a, 0.0)
            return math.sqrt(max(r * r - s * s, 0.0))

        radii = [cross_radius(lower), cross_radius(upper)]
        radius_lower = min(radii)
        radius_upper = max(radii)
        area_lower = circle_rectangle_area(
            radius_lower, y_lower, y_upper, z_lower, z_upper
        )
        area_upper = circle_rectangle_area(
            radius_upper, y_lower, y_upper, z_lower, z_upper
        )
        pieces.append((lower, upper, area_lower, area_upper, False))
    return pieces


def adaptive_mask_capsule_intersection(
    grid: GridSpec,
    capsule: Capsule,
    selected_indices: Sequence[Index3],
    *,
    relative_width_target: float = G2_LOCAL_RELATIVE_WIDTH_TARGET,
) -> dict[str, Any]:
    """Bound mask/capsule overlap by recursively bisecting cap-cell x slabs."""

    canonical = canonicalize_capsule(capsule)
    exact_volume = exact_capsule_volume_m3(canonical)
    half_diagonal = grid.cell_half_diagonal_m
    x = grid.x_centres()
    y = grid.y_centres()
    z = grid.z_centres()
    exact_contributions: list[float] = []
    counter = itertools.count()
    heap: list[tuple[float, int, Index3, float, float, float, float]] = []
    for index in selected_indices:
        i, j, k = index
        distance = capsule_signed_distance_m(x[i], y[j], z[k], canonical)
        if distance <= -half_diagonal:
            exact_contributions.append(grid.cell_volume_m3)
            continue
        for lower, upper, area_lower, area_upper, is_exact in _cell_axial_pieces(
            grid, canonical, index
        ):
            width = upper - lower
            volume_lower = width * area_lower
            volume_upper = width * area_upper
            if is_exact or volume_upper - volume_lower <= np.finfo(float).tiny:
                exact_contributions.append(0.5 * (volume_lower + volume_upper))
            else:
                heapq.heappush(
                    heap,
                    (
                        -(volume_upper - volume_lower),
                        next(counter),
                        index,
                        lower,
                        upper,
                        volume_lower,
                        volume_upper,
                    ),
                )
    running_lower = math.fsum(exact_contributions) + math.fsum(item[5] for item in heap)
    running_upper = math.fsum(exact_contributions) + math.fsum(item[6] for item in heap)
    created = len(heap)
    while running_upper - running_lower > relative_width_target * exact_volume:
        if not heap or created >= G2_MAX_AXIAL_SUBCELLS:
            raise CandidateInfeasible("G2 local intersection interval did not converge")
        _, _, index, lower, upper, old_lower, old_upper = heapq.heappop(heap)
        running_lower -= old_lower
        running_upper -= old_upper
        midpoint = 0.5 * (lower + upper)
        for child_lower, child_upper in ((lower, midpoint), (midpoint, upper)):
            # Each parent lies wholly in one cap, so endpoint radii are monotone.
            i, j, k = index
            cx, cy, cz = canonical.centre_xyz_m
            y_lower = -grid.radius_m + j * grid.dy - cy
            y_upper = y_lower + grid.dy
            z_lower = k * grid.dz - cz
            z_upper = z_lower + grid.dz
            a = canonical.segment_half_length_m
            r = canonical.radius_m

            def radius_at(
                value: float,
                centre_x: float = cx,
                segment_half_length: float = a,
                radius: float = r,
            ) -> float:
                s = max(abs(value - centre_x) - segment_half_length, 0.0)
                return math.sqrt(max(radius * radius - s * s, 0.0))

            radii = [radius_at(child_lower), radius_at(child_upper)]
            area_lower = circle_rectangle_area(
                min(radii), y_lower, y_upper, z_lower, z_upper
            )
            area_upper = circle_rectangle_area(
                max(radii), y_lower, y_upper, z_lower, z_upper
            )
            child_width = child_upper - child_lower
            volume_lower = child_width * area_lower
            volume_upper = child_width * area_upper
            running_lower += volume_lower
            running_upper += volume_upper
            heapq.heappush(
                heap,
                (
                    -(volume_upper - volume_lower),
                    next(counter),
                    index,
                    child_lower,
                    child_upper,
                    volume_lower,
                    volume_upper,
                ),
            )
            created += 1
    intersection_interval = [
        math.nextafter(running_lower, -math.inf),
        math.nextafter(running_upper, math.inf),
    ]
    mask_volume = len(selected_indices) * grid.cell_volume_m3
    symmetric_difference_interval = [
        max(mask_volume + exact_volume - 2.0 * intersection_interval[1], 0.0),
        max(mask_volume + exact_volume - 2.0 * intersection_interval[0], 0.0),
    ]
    return {
        "method": "CELL_AXIAL_BISECTION_WITH_EXACT_DISK_RECTANGLE_CROSS_SECTIONS",
        "selected_mask_cell_count": len(selected_indices),
        "adaptive_cap_leaf_count": len(heap),
        "adaptive_subcells_created": created,
        "mask_capsule_intersection_volume_interval_m3": intersection_interval,
        "intersection_relative_interval_width": (
            intersection_interval[1] - intersection_interval[0]
        )
        / exact_volume,
        "weighted_symmetric_difference_volume_interval_m3": (
            symmetric_difference_interval
        ),
        "weighted_symmetric_difference_volume_midpoint_m3": 0.5
        * sum(symmetric_difference_interval),
        "weighted_symmetric_difference_over_exact_volume": 0.5
        * sum(symmetric_difference_interval)
        / exact_volume,
    }


def assess_pairwise_jumps(
    nominal_reports: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_level = {
        float(report["cells_per_bar_diameter"]): report for report in nominal_reports
    }
    result: dict[str, Any] = {}
    for left, right in ((6.0, 9.0), (9.0, 13.5)):
        left_errors = by_level[left]["g1"]["normalized_geometry_errors"]
        right_errors = by_level[right]["g1"]["normalized_geometry_errors"]
        jumps = {
            name: abs(float(right_errors[name]) - float(left_errors[name]))
            for name in left_errors
        }
        maximum = max(jumps.values())
        result[f"{left:g}_to_{right:g}"] = {
            "normalized_error_jumps": jumps,
            "maximum_jump": maximum,
            "pass": maximum <= 0.05,
        }
    return result
