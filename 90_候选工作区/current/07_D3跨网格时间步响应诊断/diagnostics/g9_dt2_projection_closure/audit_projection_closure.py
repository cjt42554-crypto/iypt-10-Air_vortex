"""Result-blind G9_DT2 failure-step projection-closure audit.

The archive reader is deliberately exact-whitelist-only.  This module does not
import or execute the formal solver and never reads D3 physical observables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import distance_transform_edt
from scipy.sparse import coo_matrix, csr_matrix

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
Index3 = tuple[int, int, int]

TERMINAL_ALGEBRAIC = "ALGEBRAIC_RESIDUAL_LOCALIZATION_HYPOTHESIS_SUPPORTED"
TERMINAL_OPERATOR = "DISCRETE_OPERATOR_OR_BOUNDARY_MISMATCH_HYPOTHESIS_SUPPORTED"
TERMINAL_UNKNOWN = "MECHANISM_NOT_IDENTIFIED__NO_FIX_AUTHORIZED"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def formal_source_tree_identity(formal_root: Path) -> tuple[str, int, int]:
    """Reproduce the formal source-tree v1 digest without importing it."""

    candidates: list[Path] = []
    for relative in ("pyproject.toml", "run.py", "run_gui.sh"):
        candidate = formal_root / relative
        if candidate.is_file() and not candidate.is_symlink():
            candidates.append(candidate)
    package_root = formal_root / "src" / "airvortex"
    if package_root.is_dir() and not package_root.is_symlink():
        candidates.extend(
            path
            for path in package_root.rglob("*.py")
            if path.is_file() and not path.is_symlink()
        )
    files = sorted(
        candidates, key=lambda item: item.relative_to(formal_root).as_posix()
    )
    if not files:
        raise RuntimeError("formal source allow-list is empty")
    digest = hashlib.sha256()
    digest.update(b"airvortex-source-tree-v1\0")
    total_bytes = 0
    for path in files:
        relative = path.relative_to(formal_root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        total_bytes += len(payload)
    return digest.hexdigest(), len(files), total_bytes


def build_open_masks(active: BoolArray) -> tuple[BoolArray, BoolArray, BoolArray]:
    if active.ndim != 3:
        raise ValueError("active mask must be three-dimensional")
    nx, ny, nz = active.shape
    open_x = np.zeros((nx + 1, ny, nz), dtype=bool)
    open_y = np.zeros((nx, ny + 1, nz), dtype=bool)
    open_z = np.zeros((nx, ny, nz + 1), dtype=bool)
    open_x[1:-1] = active[:-1] & active[1:]
    open_y[:, 1:-1] = active[:, :-1] & active[:, 1:]
    open_z[:, :, 1:-1] = active[:, :, :-1] & active[:, :, 1:]
    open_z[:, :, -1] = active[:, :, -1]
    return open_x, open_y, open_z


def masked_divergence(
    u_x: FloatArray,
    u_y: FloatArray,
    u_z: FloatArray,
    active: BoolArray,
    open_masks: tuple[BoolArray, BoolArray, BoolArray],
    spacing: tuple[float, float, float],
) -> FloatArray:
    open_x, open_y, open_z = open_masks
    dx, dy, dz = spacing
    expected = (open_x.shape, open_y.shape, open_z.shape)
    if (u_x.shape, u_y.shape, u_z.shape) != expected:
        raise ValueError(f"face-field shapes do not match open masks: {expected}")
    ux = np.where(open_x, np.asarray(u_x, dtype=np.float64), 0.0)
    uy = np.where(open_y, np.asarray(u_y, dtype=np.float64), 0.0)
    uz = np.where(open_z, np.asarray(u_z, dtype=np.float64), 0.0)
    result = (
        (ux[1:] - ux[:-1]) / dx
        + (uy[:, 1:] - uy[:, :-1]) / dy
        + (uz[:, :, 1:] - uz[:, :, :-1]) / dz
    )
    return np.where(active, result, 0.0)


def face_inverse_density(
    density: FloatArray,
    active: BoolArray,
    open_masks: tuple[BoolArray, BoolArray, BoolArray],
) -> tuple[FloatArray, FloatArray, FloatArray]:
    if density.shape != active.shape:
        raise ValueError("density and active mask shapes differ")
    if not np.isfinite(density[active]).all() or np.any(density[active] <= 0.0):
        raise ValueError("active density must be finite and positive")
    open_x, open_y, open_z = open_masks
    beta_x = np.zeros(open_x.shape, dtype=np.float64)
    beta_y = np.zeros(open_y.shape, dtype=np.float64)
    beta_z = np.zeros(open_z.shape, dtype=np.float64)
    x_open = open_x[1:-1]
    y_open = open_y[:, 1:-1]
    z_open = open_z[:, :, 1:-1]
    beta_x[1:-1][x_open] = 2.0 / (density[:-1][x_open] + density[1:][x_open])
    beta_y[:, 1:-1][y_open] = 2.0 / (density[:, :-1][y_open] + density[:, 1:][y_open])
    beta_z[:, :, 1:-1][z_open] = 2.0 / (
        density[:, :, :-1][z_open] + density[:, :, 1:][z_open]
    )
    top_open = open_z[:, :, -1]
    beta_z[:, :, -1][top_open] = 1.0 / density[:, :, -1][top_open]
    return beta_x, beta_y, beta_z


def pressure_gradient_faces(
    pressure: FloatArray,
    open_masks: tuple[BoolArray, BoolArray, BoolArray],
    spacing: tuple[float, float, float],
) -> tuple[FloatArray, FloatArray, FloatArray]:
    open_x, open_y, open_z = open_masks
    dx, dy, dz = spacing
    expected_pressure_shape = (
        open_x.shape[0] - 1,
        open_y.shape[1] - 1,
        open_z.shape[2] - 1,
    )
    if pressure.shape != expected_pressure_shape:
        raise ValueError("pressure shape does not match face masks")
    gradient_x = np.zeros(open_x.shape, dtype=np.float64)
    gradient_y = np.zeros(open_y.shape, dtype=np.float64)
    gradient_z = np.zeros(open_z.shape, dtype=np.float64)
    x_open = open_x[1:-1]
    y_open = open_y[:, 1:-1]
    z_open = open_z[:, :, 1:-1]
    raw_x = (pressure[1:] - pressure[:-1]) / dx
    raw_y = (pressure[:, 1:] - pressure[:, :-1]) / dy
    raw_z = (pressure[:, :, 1:] - pressure[:, :, :-1]) / dz
    gradient_x[1:-1][x_open] = raw_x[x_open]
    gradient_y[:, 1:-1][y_open] = raw_y[y_open]
    gradient_z[:, :, 1:-1][z_open] = raw_z[z_open]
    top_open = open_z[:, :, -1]
    top_gradient = -pressure[:, :, -1] / (0.5 * dz)
    gradient_z[:, :, -1][top_open] = top_gradient[top_open]
    return gradient_x, gradient_y, gradient_z


def reconstruct_predictor(
    u_plus: tuple[FloatArray, FloatArray, FloatArray],
    pressure: FloatArray,
    beta: tuple[FloatArray, FloatArray, FloatArray],
    open_masks: tuple[BoolArray, BoolArray, BoolArray],
    spacing: tuple[float, float, float],
    dt_s: float,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    gradients = pressure_gradient_faces(pressure, open_masks, spacing)
    reconstructed: list[FloatArray] = []
    for corrected, coefficient, gradient, open_face in zip(
        u_plus, beta, gradients, open_masks, strict=True
    ):
        value = np.where(open_face, corrected, 0.0) + dt_s * coefficient * gradient
        reconstructed.append(np.where(open_face, value, 0.0))
    return reconstructed[0], reconstructed[1], reconstructed[2]


def assemble_pressure_matrix(
    active: BoolArray,
    open_masks: tuple[BoolArray, BoolArray, BoolArray],
    beta: tuple[FloatArray, FloatArray, FloatArray],
    spacing: tuple[float, float, float],
) -> csr_matrix:
    """Independently assemble A = -V D beta G in active C-order."""

    open_x, open_y, open_z = open_masks
    beta_x, beta_y, beta_z = beta
    dx, dy, dz = spacing
    index = np.full(active.shape, -1, dtype=np.int64)
    count = int(np.sum(active))
    index[active] = np.arange(count, dtype=np.int64)

    x_open = open_x[1:-1]
    y_open = open_y[:, 1:-1]
    z_open = open_z[:, :, 1:-1]
    top_open = open_z[:, :, -1]
    x_left = index[:-1][x_open]
    x_right = index[1:][x_open]
    y_left = index[:, :-1][y_open]
    y_right = index[:, 1:][y_open]
    z_left = index[:, :, :-1][z_open]
    z_right = index[:, :, 1:][z_open]
    top_index = index[:, :, -1][top_open]

    x_coefficient = beta_x[1:-1][x_open] * (dy * dz / dx)
    y_coefficient = beta_y[:, 1:-1][y_open] * (dx * dz / dy)
    z_coefficient = beta_z[:, :, 1:-1][z_open] * (dx * dy / dz)
    top_coefficient = beta_z[:, :, -1][top_open] * (dx * dy / (0.5 * dz))
    diagonal = np.zeros(count, dtype=np.float64)
    diagonal[x_left] += x_coefficient
    diagonal[x_right] += x_coefficient
    diagonal[y_left] += y_coefficient
    diagonal[y_right] += y_coefficient
    diagonal[z_left] += z_coefficient
    diagonal[z_right] += z_coefficient
    diagonal[top_index] += top_coefficient
    if np.any(diagonal <= 0.0):
        raise FloatingPointError(
            "independent pressure matrix has non-positive diagonal"
        )

    identity = np.arange(count, dtype=np.int64)
    rows = np.concatenate((x_left, x_right, y_left, y_right, z_left, z_right, identity))
    columns = np.concatenate(
        (x_right, x_left, y_right, y_left, z_right, z_left, identity)
    )
    data = np.concatenate(
        (
            -x_coefficient,
            -x_coefficient,
            -y_coefficient,
            -y_coefficient,
            -z_coefficient,
            -z_coefficient,
            diagonal,
        )
    )
    matrix = coo_matrix((data, (rows, columns)), shape=(count, count)).tocsr()
    if matrix.nnz != data.size:
        raise RuntimeError("independent pressure topology contains duplicate entries")
    return matrix


def active_argmax_abs(field: FloatArray, active: BoolArray) -> Index3:
    flat_active = np.flatnonzero(active.ravel(order="C"))
    if flat_active.size == 0:
        raise ValueError("active mask is empty")
    local = int(np.argmax(np.abs(field.ravel(order="C")[flat_active])))
    return tuple(int(v) for v in np.unravel_index(flat_active[local], active.shape))  # type: ignore[return-value]


def connected_component(mask: BoolArray, seed: Index3) -> BoolArray:
    if not mask[seed]:
        raise ValueError("component seed is outside the mask")
    component = np.zeros(mask.shape, dtype=bool)
    component[seed] = True
    queue: deque[Index3] = deque([seed])
    nx, ny, nz = mask.shape
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
            a, b, c = neighbour
            if (
                0 <= a < nx
                and 0 <= b < ny
                and 0 <= c < nz
                and mask[neighbour]
                and not component[neighbour]
            ):
                component[neighbour] = True
                queue.append(neighbour)
    return component


def expanded_bbox(component: BoolArray, padding: int = 2) -> list[list[int]]:
    points = np.argwhere(component)
    if points.size == 0:
        raise ValueError("component is empty")
    lower = np.maximum(points.min(axis=0) - padding, 0)
    upper = np.minimum(points.max(axis=0) + padding, np.asarray(component.shape) - 1)
    return [[int(lower[axis]), int(upper[axis])] for axis in range(3)]


def bbox_mask(shape: tuple[int, int, int], bbox: list[list[int]]) -> BoolArray:
    result = np.zeros(shape, dtype=bool)
    result[
        bbox[0][0] : bbox[0][1] + 1,
        bbox[1][0] : bbox[1][1] + 1,
        bbox[2][0] : bbox[2][1] + 1,
    ] = True
    return result


def boundary_adjacency(
    region: BoolArray, active: BoolArray, bar: BoolArray
) -> dict[str, Any]:
    nx, ny, nz = active.shape
    points = np.argwhere(region)
    top_cells = 0
    bar_cells: set[Index3] = set()
    closed_cells: set[Index3] = set()
    for raw in points:
        cell = (int(raw[0]), int(raw[1]), int(raw[2]))
        i, j, k = cell
        if k == nz - 1 and active[cell]:
            top_cells += 1
        for a, b, c in (
            (i - 1, j, k),
            (i + 1, j, k),
            (i, j - 1, k),
            (i, j + 1, k),
            (i, j, k - 1),
            (i, j, k + 1),
        ):
            if not (0 <= a < nx and 0 <= b < ny and 0 <= c < nz):
                if c != nz:
                    closed_cells.add(cell)
                continue
            neighbour = (a, b, c)
            if bar[neighbour]:
                bar_cells.add(cell)
            elif not active[neighbour]:
                closed_cells.add(cell)
    return {
        "top_open_boundary_adjacent": top_cells > 0,
        "top_open_boundary_cell_count": top_cells,
        "bar_mask_adjacent": bool(bar_cells),
        "bar_adjacent_cell_count": len(bar_cells),
        "closed_container_or_domain_adjacent": bool(closed_cells),
        "closed_adjacent_cell_count": len(closed_cells),
    }


def index_list(index: Index3) -> list[int]:
    return [int(value) for value in index]


def compact_float(value: float) -> float:
    if not math.isfinite(value):
        raise FloatingPointError(f"non-finite audit result: {value}")
    return float(value)


def explain_field(
    d_plus: FloatArray,
    candidate: FloatArray,
    active: BoolArray,
    max_cell: Index3,
    hotspot_component: BoolArray,
) -> dict[str, Any]:
    denominator_cell = abs(float(d_plus[max_cell]))
    denominator_global = float(np.max(np.abs(d_plus[active])))
    candidate_max = active_argmax_abs(candidate, active)
    return {
        "signed_max_cell_explained_fraction": compact_float(
            1.0 - abs(float(d_plus[max_cell] - candidate[max_cell])) / denominator_cell
        ),
        "global_linf_explained_fraction": compact_float(
            1.0
            - float(np.max(np.abs((d_plus - candidate)[active]))) / denominator_global
        ),
        "candidate_argmax_index": index_list(candidate_max),
        "candidate_argmax_in_hot_component": bool(hotspot_component[candidate_max]),
        "candidate_max_abs_s_inv": compact_float(
            float(np.max(np.abs(candidate[active])))
        ),
        "candidate_at_divergence_max_s_inv": compact_float(float(candidate[max_cell])),
    }


def verify_archive_binding(path: Path, binding: dict[str, Any]) -> None:
    if path.stat().st_size != int(binding["size_bytes"]):
        raise RuntimeError("frozen partial size drifted")
    if sha256_file(path) != binding["sha256"]:
        raise RuntimeError("frozen partial SHA-256 drifted")


def load_exact_whitelist(
    archive_path: Path, permitted: Iterable[str]
) -> tuple[dict[str, NDArray[Any]], list[str], list[str]]:
    permitted_names = tuple(permitted)
    loaded: dict[str, NDArray[Any]] = {}
    accessed: list[str] = []
    with np.load(archive_path, allow_pickle=False) as archive:
        member_names = list(archive.files)
        missing = [name for name in permitted_names if name not in member_names]
        if missing:
            raise KeyError(f"frozen partial lacks permitted members: {missing}")
        for name in permitted_names:
            if name.startswith("observable_"):
                raise RuntimeError("observable member reached the audit whitelist")
            loaded[name] = np.asarray(archive[name])
            accessed.append(name)
    return loaded, accessed, member_names


def git_head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def require_clean_git_worktree(repo: Path) -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    if status:
        raise RuntimeError("audit requires a clean Git worktree")


def audit(contract_path: Path) -> dict[str, Any]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    repo = Path(__file__).resolve().parents[5]
    require_clean_git_worktree(repo)
    bindings = contract["bindings"]
    config_path = repo / bindings["config"]["path"]
    archive_path = repo / bindings["frozen_partial"]["path"]
    if sha256_file(config_path) != bindings["config"]["sha256"]:
        raise RuntimeError("G9_DT2 configuration SHA-256 drifted")
    source_hash, source_file_count, source_total_bytes = formal_source_tree_identity(
        repo / "02_模拟程序"
    )
    if source_hash != bindings["formal_source_tree_sha256"]:
        raise RuntimeError("formal source-tree SHA-256 drifted")
    verify_archive_binding(archive_path, bindings["frozen_partial"])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    arrays, accessed, member_names = load_exact_whitelist(
        archive_path, contract["archive_access"]["permitted_numeric_members"]
    )
    if any(name.startswith("observable_") for name in accessed):
        raise RuntimeError("physical observable was accessed")

    active_raw = np.asarray(arrays["grid_active_mask"])
    bar_raw = np.asarray(arrays["grid_bar_mask"])
    if not np.all((active_raw == 0) | (active_raw == 1)):
        raise ValueError("active mask is not binary")
    if not np.all((bar_raw == 0) | (bar_raw == 1)):
        raise ValueError("bar mask is not binary")
    active = active_raw.astype(bool)
    bar = bar_raw.astype(bool)
    if np.any(active & bar):
        raise ValueError("active and bar masks overlap")

    nx = int(config["high_fidelity"]["radial_cells"])
    ny = int(config["high_fidelity"]["azimuthal_cells"])
    nz = int(config["high_fidelity"]["axial_cells"])
    shape = (nx, ny, nz)
    if active.shape != shape or bar.shape != shape:
        raise ValueError("saved masks do not match configured grid shape")
    radius = float(config["container"]["radius_m"])
    height = float(config["container"]["liquid_depth_m"]) + float(
        config["container"]["headspace_height_m"]
    )
    spacing = (2.0 * radius / nx, 2.0 * radius / ny, height / nz)
    dx, dy, dz = spacing
    volume = dx * dy * dz

    u_plus = (
        np.asarray(arrays["state_u_x_face"], dtype=np.float64),
        np.asarray(arrays["state_u_y_face"], dtype=np.float64),
        np.asarray(arrays["state_u_z_face"], dtype=np.float64),
    )
    pressure = np.asarray(arrays["state_pressure_pa"], dtype=np.float64)
    phase = np.asarray(arrays["state_phase"], dtype=np.float64)
    if pressure.shape != shape or phase.shape != shape:
        raise ValueError("saved pressure or phase shape does not match configuration")
    if not np.isfinite(pressure[active]).all() or not np.isfinite(phase[active]).all():
        raise FloatingPointError("saved active pressure or phase is non-finite")

    diagnostics = {
        name: np.asarray(arrays[name], dtype=np.float64)
        for name in (
            "diagnostic_step_time",
            "diagnostic_step_dt",
            "diagnostic_step_divergence",
            "diagnostic_step_pressure_residual",
            "diagnostic_step_pressure_iterations",
        )
    }
    accepted_step = int(bindings["failure_reference"]["accepted_step"])
    lengths = {name: int(value.size) for name, value in diagnostics.items()}
    if set(lengths.values()) != {accepted_step}:
        raise ValueError(f"diagnostic lengths do not equal accepted step: {lengths}")
    for name, value in diagnostics.items():
        if value.ndim != 1 or not np.isfinite(value).all():
            raise FloatingPointError(f"invalid scalar diagnostic member: {name}")
    dt_s = float(diagnostics["diagnostic_step_dt"][-1])
    if not math.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError("final diagnostic dt is invalid")

    open_masks = build_open_masks(active)
    for field, mask, name in zip(
        u_plus, open_masks, ("u_x", "u_y", "u_z"), strict=True
    ):
        if field.shape != mask.shape or not np.isfinite(field[mask]).all():
            raise FloatingPointError(f"saved {name} face field is invalid")
    liquid_density = float(config["fluid"]["liquid_density_kg_m3"])
    gas_density = float(config["fluid"]["gas_density_kg_m3"])
    density = gas_density + (liquid_density - gas_density) * np.clip(phase, 0.0, 1.0)
    beta = face_inverse_density(density, active, open_masks)
    d_plus = masked_divergence(*u_plus, active, open_masks, spacing)
    u_star = reconstruct_predictor(u_plus, pressure, beta, open_masks, spacing, dt_s)
    d_star = masked_divergence(*u_star, active, open_masks, spacing)
    matrix = assemble_pressure_matrix(active, open_masks, beta, spacing)
    p_active = pressure[active]
    b = -(volume / dt_s) * d_star[active]
    a_p = np.asarray(matrix @ p_active, dtype=np.float64)
    residual = a_p - b
    d_alg = np.zeros(shape, dtype=np.float64)
    d_alg[active] = (dt_s / volume) * residual
    e_op = np.where(active, d_plus - d_alg, 0.0)

    gradients = pressure_gradient_faces(pressure, open_masks, spacing)
    beta_gradient = tuple(
        coefficient * gradient
        for coefficient, gradient in zip(beta, gradients, strict=True)
    )
    a_p_face = (
        -volume * masked_divergence(*beta_gradient, active, open_masks, spacing)[active]
    )
    operator_difference = np.asarray(a_p - a_p_face, dtype=np.float64)
    operator_scale = max(
        1.0,
        float(np.max(np.abs(a_p))),
        float(np.max(np.abs(a_p_face))),
    )
    operator_tolerance = 512.0 * np.finfo(float).eps * operator_scale
    operator_difference_max = float(np.max(np.abs(operator_difference)))
    if operator_difference_max > operator_tolerance:
        raise RuntimeError(
            "independent sparse and face pressure operators disagree: "
            f"{operator_difference_max:.17g} > {operator_tolerance:.17g}"
        )

    max_cell = active_argmax_abs(d_plus, active)
    max_divergence = float(np.max(np.abs(d_plus[active])))
    hot = active & (np.abs(d_plus) >= 0.5 * max_divergence)
    component = connected_component(hot, max_cell)
    bbox = expanded_bbox(component, padding=2)
    expanded = bbox_mask(shape, bbox) & active
    d_alg_explanation = explain_field(d_plus, d_alg, active, max_cell, component)
    e_op_explanation = explain_field(d_plus, e_op, active, max_cell, component)

    archived = float(
        bindings["failure_reference"]["archived_max_projected_divergence_s_inv"]
    )
    reproduction_tolerance = max(1.0e-12, 1.0e-8 * abs(archived))
    reproduction_error = abs(max_divergence - archived)
    archived_reproduced = reproduction_error <= reproduction_tolerance
    operator_rounding_tolerance = max(1.0e-12, 1.0e-8 * max_divergence)
    max_operator_defect = float(np.max(np.abs(e_op[active])))
    operator_rounding_scale = max_operator_defect <= operator_rounding_tolerance
    minimum_explained = float(contract["adjudication"]["minimum_explained_fraction"])
    h_a = bool(
        archived_reproduced
        and d_alg_explanation["signed_max_cell_explained_fraction"] >= minimum_explained
        and d_alg_explanation["global_linf_explained_fraction"] >= minimum_explained
        and d_alg_explanation["candidate_argmax_in_hot_component"]
        and operator_rounding_scale
    )
    h_b = bool(
        archived_reproduced
        and e_op_explanation["signed_max_cell_explained_fraction"] >= minimum_explained
        and e_op_explanation["global_linf_explained_fraction"] >= minimum_explained
        and e_op_explanation["candidate_argmax_in_hot_component"]
    )
    if h_a and not h_b:
        terminal = TERMINAL_ALGEBRAIC
    elif h_b and not h_a:
        terminal = TERMINAL_OPERATOR
    else:
        terminal = TERMINAL_UNKNOWN

    interface_band = active & (phase > 1.0e-4) & (phase < 1.0 - 1.0e-4)
    phase_gradients = np.gradient(phase, dx, dy, dz, edge_order=1)
    grad_phase = np.sqrt(
        sum(component_gradient**2 for component_gradient in phase_gradients)
    )
    grad_density_scale = abs(liquid_density - gas_density)
    if np.any(interface_band):
        distance_to_interface = distance_transform_edt(
            ~interface_band, sampling=spacing
        )
        max_cell_interface_distance = float(distance_to_interface[max_cell])
    else:
        max_cell_interface_distance = math.inf

    top_flux = float(np.sum(u_plus[2][:, :, -1][open_masks[2][:, :, -1]]) * dx * dy)
    integrated_divergence = float(np.sum(d_plus[active]) * volume)
    flux_closure = integrated_divergence - top_flux
    flux_scale = max(abs(integrated_divergence), abs(top_flux), np.finfo(float).tiny)
    relative_residual_l2 = float(np.linalg.norm(residual) / np.linalg.norm(b))
    relative_residual_linf = float(np.max(np.abs(residual)) / np.max(np.abs(b)))
    residual_field = np.zeros(shape, dtype=np.float64)
    residual_field[active] = residual
    residual_max_cell = active_argmax_abs(residual_field, active)
    operator_max_cell = active_argmax_abs(e_op, active)

    scalar_start = accepted_step - 12
    scalar_context: list[dict[str, Any]] = []
    for offset in range(-13, 0):
        scalar_context.append(
            {
                "accepted_step": accepted_step + offset + 1,
                "time_s": compact_float(
                    float(diagnostics["diagnostic_step_time"][offset])
                ),
                "dt_s": compact_float(float(diagnostics["diagnostic_step_dt"][offset])),
                "max_projected_divergence_s_inv": compact_float(
                    float(diagnostics["diagnostic_step_divergence"][offset])
                ),
                "pressure_relative_residual_l2": compact_float(
                    float(diagnostics["diagnostic_step_pressure_residual"][offset])
                ),
                "pressure_iterations": int(
                    diagnostics["diagnostic_step_pressure_iterations"][offset]
                ),
            }
        )
    if scalar_context[0]["accepted_step"] != scalar_start:
        raise RuntimeError("scalar context indexing drifted")

    component_adjacency = boundary_adjacency(component, active, bar)
    expanded_adjacency = boundary_adjacency(expanded, active, bar)
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "protocol_id": contract["protocol_id"],
        "terminal_status": terminal,
        "fix_authorized": False,
        "execution": {
            "audit_commit": git_head(repo),
            "contract_path": str(contract_path.relative_to(repo)),
            "contract_sha256": sha256_file(contract_path),
            "d3_execution_commit": bindings["d3_execution_commit"],
            "formal_source_tree_sha256": source_hash,
            "formal_source_tree_file_count": source_file_count,
            "formal_source_tree_total_bytes": source_total_bytes,
            "config_sha256": bindings["config"]["sha256"],
            "partial_size_bytes": int(bindings["frozen_partial"]["size_bytes"]),
            "partial_sha256_before": bindings["frozen_partial"]["sha256"],
            "partial_sha256_after": None,
            "numeric_members_accessed": accessed,
            "archive_member_name_count": len(member_names),
            "forbidden_observable_member_access_count": 0,
            "dynamic_steps_executed_by_audit": 0,
        },
        "grid": {
            "shape": [nx, ny, nz],
            "spacing_m": [compact_float(dx), compact_float(dy), compact_float(dz)],
            "cell_volume_m3": compact_float(volume),
            "active_cell_count": int(np.sum(active)),
            "bar_cell_count": int(np.sum(bar)),
            "pressure_matrix_unknowns": int(matrix.shape[0]),
            "pressure_matrix_nonzeros": int(matrix.nnz),
            "pressure_matrix_min_diagonal": compact_float(
                float(matrix.diagonal().min())
            ),
            "sparse_vs_face_operator_max_abs": compact_float(operator_difference_max),
            "sparse_vs_face_operator_tolerance": compact_float(operator_tolerance),
        },
        "failure_step": {
            "accepted_step": accepted_step,
            "time_s": compact_float(float(diagnostics["diagnostic_step_time"][-1])),
            "dt_s": compact_float(dt_s),
            "archived_max_projected_divergence_s_inv": compact_float(archived),
            "diagnostic_array_final_max_projected_divergence_s_inv": compact_float(
                float(diagnostics["diagnostic_step_divergence"][-1])
            ),
            "recomputed_max_projected_divergence_s_inv": compact_float(max_divergence),
            "reproduction_abs_error_s_inv": compact_float(reproduction_error),
            "reproduction_tolerance_s_inv": compact_float(reproduction_tolerance),
            "archived_scalar_reproduced": archived_reproduced,
            "recomputed_pressure_relative_residual_l2": compact_float(
                relative_residual_l2
            ),
            "diagnostic_pressure_relative_residual_l2": compact_float(
                float(diagnostics["diagnostic_step_pressure_residual"][-1])
            ),
            "recomputed_pressure_relative_residual_linf": compact_float(
                relative_residual_linf
            ),
            "pressure_iterations": int(
                diagnostics["diagnostic_step_pressure_iterations"][-1]
            ),
            "global_integrated_divergence_m3_s": compact_float(integrated_divergence),
            "top_open_flux_m3_s": compact_float(top_flux),
            "global_flux_closure_m3_s": compact_float(flux_closure),
            "global_flux_closure_relative": compact_float(
                abs(flux_closure) / flux_scale
            ),
        },
        "decomposition": {
            "max_cell_index": index_list(max_cell),
            "signed_divergence_at_max_cell_s_inv": compact_float(
                float(d_plus[max_cell])
            ),
            "algebraic_component": d_alg_explanation,
            "operator_defect": e_op_explanation,
            "max_operator_defect_s_inv": compact_float(max_operator_defect),
            "operator_rounding_tolerance_s_inv": compact_float(
                operator_rounding_tolerance
            ),
            "operator_defect_at_rounding_scale": operator_rounding_scale,
            "residual_argmax_index": index_list(residual_max_cell),
            "residual_argmax_in_hot_component": bool(component[residual_max_cell]),
            "operator_defect_argmax_index": index_list(operator_max_cell),
            "operator_defect_argmax_in_hot_component": bool(
                component[operator_max_cell]
            ),
        },
        "hotspot": {
            "threshold_fraction_of_global_max": 0.5,
            "all_hot_cell_count": int(np.sum(hot)),
            "max_component_cell_count": int(np.sum(component)),
            "max_component_bbox_inclusive": expanded_bbox(component, padding=0),
            "expanded_two_cell_bbox_inclusive": bbox,
            "expanded_active_cell_count": int(np.sum(expanded)),
            "component_boundary_adjacency": component_adjacency,
            "expanded_region_boundary_adjacency": expanded_adjacency,
            "interface_band_cell_count": int(np.sum(interface_band)),
            "interface_band_cells_in_component": int(
                np.sum(interface_band & component)
            ),
            "interface_band_cells_in_expanded_region": int(
                np.sum(interface_band & expanded)
            ),
            "max_cell_distance_to_interface_band_m": (
                compact_float(max_cell_interface_distance)
                if math.isfinite(max_cell_interface_distance)
                else None
            ),
            "max_grad_phase_global_active_m_inv": compact_float(
                float(np.max(grad_phase[active]))
            ),
            "max_grad_phase_in_component_m_inv": compact_float(
                float(np.max(grad_phase[component]))
            ),
            "max_grad_phase_in_expanded_region_m_inv": compact_float(
                float(np.max(grad_phase[expanded]))
            ),
            "max_grad_density_in_component_kg_m4": compact_float(
                float(np.max(grad_phase[component])) * grad_density_scale
            ),
            "max_grad_density_in_expanded_region_kg_m4": compact_float(
                float(np.max(grad_phase[expanded])) * grad_density_scale
            ),
        },
        "hypothesis_gates": {
            "minimum_explained_fraction": minimum_explained,
            "H_A_algebraic_residual_localization_supported": h_a,
            "H_B_discrete_operator_or_boundary_mismatch_supported": h_b,
            "H_C_archive_alignment_mismatch_supported": not archived_reproduced,
            "exclusive_supported_hypothesis": (
                "H_A"
                if terminal == TERMINAL_ALGEBRAIC
                else "H_B"
                if terminal == TERMINAL_OPERATOR
                else None
            ),
        },
        "scalar_context_steps_6683_through_6695": scalar_context,
        "claim_ceiling": contract["claim_ceiling"],
        "required_stop": "STOP_FOR_INDEPENDENT_REVIEW__NO_FIX_IN_THIS_ROUND",
    }
    verify_archive_binding(archive_path, bindings["frozen_partial"])
    result["execution"]["partial_sha256_after"] = sha256_file(archive_path)
    return result


def render_markdown(result: dict[str, Any]) -> str:
    failure = result["failure_step"]
    decomposition = result["decomposition"]
    hotspot = result["hotspot"]
    gates = result["hypothesis_gates"]
    lines = [
        "# G9_DT2 失败步离散投影闭合审计",
        "",
        f"终态：`{result['terminal_status']}`。",
        "",
        "本审计只读取预注册白名单中的数值状态和步级压力诊断；没有读取任何 `observable_*`，没有运行动态步，也不授权同轮修复。",
        "",
        "## 归档与离散闭合",
        "",
        f"- 失败步：`{failure['accepted_step']}`；时刻 `{failure['time_s']:.17g} s`；步长 `{failure['dt_s']:.17g} s`。",
        f"- 冻结最大散度：`{failure['archived_max_projected_divergence_s_inv']:.17g} s^-1`。",
        f"- 独立重算最大散度：`{failure['recomputed_max_projected_divergence_s_inv']:.17g} s^-1`；复现门：`{failure['archived_scalar_reproduced']}`。",
        rf"- 重算压力相对残差：$L_2={failure['recomputed_pressure_relative_residual_l2']:.17g}$，$L_\infty={failure['recomputed_pressure_relative_residual_linf']:.17g}$。",
        f"- 全局通量闭合误差：`{failure['global_flux_closure_m3_s']:.17g} m^3 s^-1`，相对值 `{failure['global_flux_closure_relative']:.17g}`。",
        "",
        "## 机制判别",
        "",
        f"- 最大散度格点：`{decomposition['max_cell_index']}`。",
        rf"- 代数残差分量在最大格点/全局 $L_\infty$ 的解释率：`{decomposition['algebraic_component']['signed_max_cell_explained_fraction']:.17g}` / `{decomposition['algebraic_component']['global_linf_explained_fraction']:.17g}`。",
        rf"- 算子缺陷在最大格点/全局 $L_\infty$ 的解释率：`{decomposition['operator_defect']['signed_max_cell_explained_fraction']:.17g}` / `{decomposition['operator_defect']['global_linf_explained_fraction']:.17g}`。",
        f"- 算子缺陷处于冻结舍入尺度：`{decomposition['operator_defect_at_rounding_scale']}`。",
        f"- H-A：`{gates['H_A_algebraic_residual_localization_supported']}`；H-B：`{gates['H_B_discrete_operator_or_boundary_mismatch_supported']}`；H-C：`{gates['H_C_archive_alignment_mismatch_supported']}`。",
        "",
        "## 空间热点",
        "",
        f"- 50% 热点总格数：`{hotspot['all_hot_cell_count']}`；包含最大值的六邻域分量：`{hotspot['max_component_cell_count']}`。",
        f"- 分量包围盒：`{hotspot['max_component_bbox_inclusive']}`；向各轴扩展两格：`{hotspot['expanded_two_cell_bbox_inclusive']}`。",
        f"- 分量边界标签：`{json.dumps(hotspot['component_boundary_adjacency'], ensure_ascii=False, sort_keys=True)}`。",
        f"- 分量中的界面带格数：`{hotspot['interface_band_cells_in_component']}`；扩展区中的界面带格数：`{hotspot['interface_band_cells_in_expanded_region']}`。",
        "",
        "## 结论边界",
        "",
        "数值资格仍未建立；物理验证和实验验证仍未执行；装置预测仍禁止。下一步必须是独立复核本审计，不得在本轮自动修改正式求解器。",
        "",
    ]
    return "\n".join(lines)


def write_outputs(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        output_dir / "projection_closure_audit.json": json.dumps(
            result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n",
        output_dir / "projection_closure_audit.md": render_markdown(result),
    }
    for path, text in payloads.items():
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)


def main() -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        type=Path,
        default=here / "projection_closure_preregistration.json",
    )
    parser.add_argument("--output-dir", type=Path, default=here / "artifacts")
    arguments = parser.parse_args()
    result = audit(arguments.contract.resolve())
    write_outputs(result, arguments.output_dir.resolve())
    print(result["terminal_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
