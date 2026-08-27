#!/usr/bin/env python3
"""Static voxel-geometry audit for the preregistered Q1R4 D0 gate."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

import numpy as np


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

from airvortex.backends.high_fidelity_3d import HighFidelityBackend
from airvortex.core import SimulationConfig
from airvortex.io.archive import source_tree_identity
from airvortex.io.config_io import load_config_json, read_json_strict


LEVELS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (4.0, (60, 60, 54)),
    (6.0, (90, 90, 80)),
    (9.0, (136, 136, 120)),
    (13.5, (204, 204, 180)),
)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def git_identity() -> dict[str, object]:
    status = _git("status", "--porcelain", "--untracked-files=normal")
    return {
        "head": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "worktree_clean": status == "",
        "porcelain": status.splitlines(),
    }


def exact_capsule_second_moment_tensor(
    half_length_m: float,
    radius_m: float,
) -> np.ndarray:
    """Return integral r_i r_j dV for an x-aligned spherocylinder."""

    a = max(float(half_length_m) - float(radius_m), 0.0)
    r = float(radius_m)
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


def voxel_second_moment_tensor(grid: Any) -> tuple[np.ndarray, np.ndarray]:
    """Return voxel centroid and integral r_i r_j dV about the true centre."""

    mask = np.asarray(grid.bar_mask, dtype=bool)
    volume = float(np.sum(mask) * grid.cell_volume_m3)
    if volume <= 0.0:
        raise ValueError("voxel bar mask has no volume")
    coordinates = np.column_stack(
        (
            np.asarray(grid.x_cell)[mask],
            np.asarray(grid.y_cell)[mask],
            np.asarray(grid.z_cell)[mask],
        )
    )
    centroid = np.mean(coordinates, axis=0)
    true_centre = np.asarray([0.0, 0.0, grid.bar_centre_z_m], dtype=np.float64)
    offsets = coordinates - true_centre
    tensor = offsets.T @ offsets * grid.cell_volume_m3
    return centroid.astype(np.float64), np.asarray(tensor, dtype=np.float64)


def audit_one_level(
    backend: HighFidelityBackend,
    base_config: SimulationConfig,
    cells_per_bar_diameter: float,
    dimensions: tuple[int, int, int],
) -> dict[str, object]:
    nx, ny, nz = dimensions
    config = replace(
        base_config,
        high_fidelity=replace(
            base_config.high_fidelity,
            radial_cells=nx,
            azimuthal_cells=ny,
            axial_cells=nz,
        ),
    )
    grid = backend.make_grid(config)
    exact_volume = grid.exact_bar_volume_m3
    voxel_volume = grid.rasterized_bar_volume_m3
    centroid, voxel_tensor = voxel_second_moment_tensor(grid)
    true_centre = np.asarray([0.0, 0.0, grid.bar_centre_z_m], dtype=np.float64)
    centroid_error = centroid - true_centre
    exact_tensor = exact_capsule_second_moment_tensor(
        config.stir_bar.half_length_m,
        config.stir_bar.radius_m,
    )
    tensor_relative_error = float(
        np.linalg.norm(voxel_tensor - exact_tensor)
        / np.linalg.norm(exact_tensor)
    )
    active_fluid_volume = grid.active_cell_count * grid.cell_volume_m3
    exact_active_fluid_volume = grid.exact_vessel_volume_m3 - exact_volume
    normalized_errors = {
        "bar_volume_relative": abs(voxel_volume - exact_volume) / exact_volume,
        "centroid_over_bar_radius": float(
            np.linalg.norm(centroid_error) / config.stir_bar.radius_m
        ),
        "second_moment_relative_frobenius": tensor_relative_error,
    }
    return {
        "cells_per_bar_diameter": cells_per_bar_diameter,
        "grid": [nx, ny, nz],
        "grid_spacing_xyz_m": [grid.dx, grid.dy, grid.dz],
        "minimum_spacing_m": grid.minimum_spacing_m,
        "maximum_spacing_m": grid.maximum_spacing_m,
        "active_cell_count": grid.active_cell_count,
        "bar_cell_count": int(np.sum(grid.bar_mask)),
        "exact_bar_volume_m3": exact_volume,
        "rasterized_bar_volume_m3": voxel_volume,
        "rasterized_bar_volume_relative_error": (
            (voxel_volume - exact_volume) / exact_volume
        ),
        "bar_mask_centroid_xyz_m": centroid.tolist(),
        "true_bar_centroid_xyz_m": true_centre.tolist(),
        "bar_mask_centroid_error_xyz_m": centroid_error.tolist(),
        "bar_mask_centroid_error_norm_m": float(np.linalg.norm(centroid_error)),
        "bar_mask_second_moment_tensor_m5": voxel_tensor.tolist(),
        "exact_bar_second_moment_tensor_m5": exact_tensor.tolist(),
        "bar_second_moment_relative_frobenius_error": tensor_relative_error,
        "rasterized_active_fluid_volume_m3": active_fluid_volume,
        "exact_active_fluid_volume_m3": exact_active_fluid_volume,
        "active_fluid_volume_error_m3": (
            active_fluid_volume - exact_active_fluid_volume
        ),
        "active_fluid_volume_relative_error": (
            (active_fluid_volume - exact_active_fluid_volume)
            / exact_active_fluid_volume
        ),
        "normalized_geometry_errors": normalized_errors,
    }


def _thresholds(preregistration: Mapping[str, Any]) -> Mapping[str, Any]:
    return preregistration["d0_scope"]["A_static_geometry_audit"][
        "diagnostic_boundedness_thresholds"
    ]


def assess_geometry(
    levels: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> dict[str, object]:
    by_label = {
        float(item["cells_per_bar_diameter"]): item
        for item in levels
    }
    required = {4.0, 6.0, 9.0, 13.5}
    if set(by_label) != required:
        raise ValueError("geometry audit must contain exactly 4/6/9/13.5 levels")
    individual: dict[str, dict[str, bool]] = {}
    for label in (6.0, 9.0, 13.5):
        errors = by_label[label]["normalized_geometry_errors"]
        individual[str(label)] = {
            "bar_volume": bool(
                errors["bar_volume_relative"]
                <= thresholds["rasterized_bar_volume_relative_error_max"]
            ),
            "centroid": bool(
                errors["centroid_over_bar_radius"]
                <= thresholds["bar_centroid_error_over_radius_max"]
            ),
            "second_moment": bool(
                errors["second_moment_relative_frobenius"]
                <= thresholds["bar_second_moment_relative_frobenius_error_max"]
            ),
        }
    pairwise: dict[str, dict[str, object]] = {}
    jump_max = float(
        thresholds["six_to_nine_or_nine_to_13p5_normalized_error_jump_max"]
    )
    for left, right in ((6.0, 9.0), (9.0, 13.5)):
        left_errors = by_label[left]["normalized_geometry_errors"]
        right_errors = by_label[right]["normalized_geometry_errors"]
        jumps = {
            name: abs(float(right_errors[name]) - float(left_errors[name]))
            for name in left_errors
        }
        pairwise[f"{left:g}_to_{right:g}"] = {
            "normalized_error_jumps": jumps,
            "maximum_jump": max(jumps.values()),
            "pass": max(jumps.values()) <= jump_max,
        }
    gates = {
        "individual_geometry_errors_bounded": all(
            all(checks.values()) for checks in individual.values()
        ),
        "pairwise_geometry_error_jumps_bounded": all(
            bool(item["pass"]) for item in pairwise.values()
        ),
    }
    return {
        "pass": all(gates.values()),
        "gates": gates,
        "individual_level_checks": individual,
        "pairwise_checks": pairwise,
        "thresholds": dict(thresholds),
    }


def run_audit(
    base_config_path: Path,
    preregistration_path: Path,
) -> dict[str, object]:
    preregistration_raw = read_json_strict(preregistration_path)
    if not isinstance(preregistration_raw, Mapping):
        raise ValueError("preregistration must contain an object")
    git_start = git_identity()
    source_start = source_tree_identity(FORMAL_ROOT)
    if git_start["branch"] != "main" or git_start["worktree_clean"] is not True:
        raise RuntimeError("geometry audit requires a clean main worktree")
    backend = HighFidelityBackend()
    base_config = load_config_json(base_config_path)
    levels = [
        audit_one_level(backend, base_config, label, dimensions)
        for label, dimensions in LEVELS
    ]
    source_end = source_tree_identity(FORMAL_ROOT)
    git_end = git_identity()
    assessment = assess_geometry(levels, _thresholds(preregistration_raw))
    identity_gates = {
        "main_branch": git_start["branch"] == git_end["branch"] == "main",
        "worktree_clean_start_and_end": bool(
            git_start["worktree_clean"] and git_end["worktree_clean"]
        ),
        "git_head_stable": git_start["head"] == git_end["head"],
        "formal_source_tree_available": bool(source_start.get("available")),
        "formal_source_tree_stable": source_start == source_end,
    }
    gate_pass = bool(assessment["pass"] and all(identity_gates.values()))
    return {
        "schema_version": 1,
        "diagnostic_protocol_id": preregistration_raw.get(
            "diagnostic_protocol_id"
        ),
        "status": (
            "Q1R4_D0_STATIC_GEOMETRY_BOUNDED"
            if gate_pass
            else "Q1R4_D0_STATIC_GEOMETRY_MATERIAL_OR_UNRESOLVED"
        ),
        "gate_pass": gate_pass,
        "dynamic_solver_steps": 0,
        "inputs": {
            "base_config": str(base_config_path),
            "preregistration": str(preregistration_path),
        },
        "identity": {
            "git_start": git_start,
            "git_end": git_end,
            "formal_source_tree_start": source_start,
            "formal_source_tree_end": source_end,
            "gates": identity_gates,
        },
        "levels": levels,
        "assessment": assessment,
        "claim_boundary": {
            "geometry_audit_establishes_numerical_convergence": False,
            "geometry_audit_validates_physics": False,
            "geometry_audit_allows_apparatus_prediction": False,
        },
    }


def markdown(report: Mapping[str, Any]) -> str:
    rows: list[str] = []
    for item in report["levels"]:
        errors = item["normalized_geometry_errors"]
        rows.append(
            "| {label:g} | `{grid}` | {active} | {volume:.6g} | "
            "{centroid:.6g} | {moment:.6g} | {fluid:.6g} |".format(
                label=float(item["cells_per_bar_diameter"]),
                grid=" × ".join(str(value) for value in item["grid"]),
                active=int(item["active_cell_count"]),
                volume=float(errors["bar_volume_relative"]),
                centroid=float(errors["centroid_over_bar_radius"]),
                moment=float(errors["second_moment_relative_frobenius"]),
                fluid=float(item["active_fluid_volume_relative_error"]),
            )
        )
    assessment = report["assessment"]
    return f"""# Q1R4 D0-A 静态磁子体素几何审计

状态：`{report['status']}`。

本审计仅构造正式 `ImmersedCartesianGrid` 并计算静态几何量，动态求解步数为 `0`。几何误差受限不等于数值收敛、物理验证或装置预测通过。

## 四层结果

| 格/磁子直径 | 网格 | 活动单元 | 磁子体积相对误差绝对值 | 质心误差/磁子半径 | 二阶矩相对 Frobenius 误差 | 活动流体体积相对误差 |
|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

二阶矩是关于真实磁子中心的体积积分张量，单位为 `m^5`；完整张量保存在 JSON 报告中。

## 诊断门

- 单层几何误差受限：`{assessment['gates']['individual_geometry_errors_bounded']}`。
- `6→9` 与 `9→13.5` 归一化误差跳变受限：`{assessment['gates']['pairwise_geometry_error_jumps_bounded']}`。
- D0-A 总门：`{report['gate_pass']}`。

## 结论边界

本报告不运行动态求解器，也不建立空间或时间收敛。即使 D0-A 通过，也只能继续 D0-B/C；Q1R4 仍未冻结，装置预测仍被禁止。
"""


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-config",
        type=Path,
        default=(
            HERE.parent
            / "01_HF3D数值资格/generated_q1r3/space_L2_9cells.json"
        ),
    )
    parser.add_argument(
        "--preregistration",
        type=Path,
        default=HERE / "q1r4_diagnostic_preregistration.json",
    )
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    report = run_audit(
        args.base_config.resolve(),
        args.preregistration.resolve(),
    )
    _write_json(args.json_output.resolve(), report)
    args.markdown_output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.resolve().write_text(markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {"status": report["status"], "gate_pass": report["gate_pass"]},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
