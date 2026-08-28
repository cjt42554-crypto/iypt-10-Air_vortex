#!/usr/bin/env python3
"""Fail-closed A0 reproduction of the formal G0 rasterizer conditioning."""

from __future__ import annotations

import argparse
import gc
import hashlib
import inspect
import json
import math
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from airvortex.core import SimulationConfig
from airvortex.io.archive import source_tree_identity
from airvortex.io.config_io import load_config_json, read_json_strict
from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from controlled_bar_geometry import (
    Capsule,
    exact_capsule_second_moment_tensor_m5,
    mask_sha256,
)

LEVELS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (4.0, (60, 60, 54)),
    (6.0, (90, 90, 80)),
    (9.0, (136, 136, 120)),
    (13.5, (204, 204, 180)),
)
ENUMERATION_SEED = 20260828


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _grid(
    config: SimulationConfig, dimensions: tuple[int, int, int]
) -> ImmersedCartesianGrid:
    nx, ny, nz = dimensions
    return ImmersedCartesianGrid(
        nx,
        ny,
        nz,
        config.container.radius_m,
        config.container.liquid_depth_m + config.container.headspace_height_m,
        bar_half_length_m=config.stir_bar.half_length_m,
        bar_radius_m=config.stir_bar.radius_m,
        bar_centre_z_m=(config.stir_bar.bottom_clearance_m + config.stir_bar.radius_m),
    )


def _grid_with_centre(
    config: SimulationConfig,
    dimensions: tuple[int, int, int],
    centre_z_m: float,
) -> ImmersedCartesianGrid:
    nx, ny, nz = dimensions
    return ImmersedCartesianGrid(
        nx,
        ny,
        nz,
        config.container.radius_m,
        config.container.liquid_depth_m + config.container.headspace_height_m,
        bar_half_length_m=config.stir_bar.half_length_m,
        bar_radius_m=config.stir_bar.radius_m,
        bar_centre_z_m=centre_z_m,
    )


def _metrics(grid: ImmersedCartesianGrid, mask: np.ndarray) -> dict[str, Any]:
    mask = np.asarray(mask, dtype=bool)
    count = int(np.sum(mask))
    coordinates = np.column_stack(
        (
            np.asarray(grid.x_cell)[mask],
            np.asarray(grid.y_cell)[mask],
            np.asarray(grid.z_cell)[mask],
        )
    )
    centroid = np.mean(coordinates, axis=0)
    true_centre = np.asarray([0.0, 0.0, grid.bar_centre_z_m])
    offsets = coordinates - true_centre
    tensor = offsets.T @ offsets * grid.cell_volume_m3
    capsule = Capsule(
        grid.bar_half_length_m,
        grid.bar_radius_m,
        (0.0, 0.0, grid.bar_centre_z_m),
    )
    exact_tensor = exact_capsule_second_moment_tensor_m5(capsule)
    centroid_error = centroid - true_centre
    exact_volume = grid.exact_bar_volume_m3
    voxel_volume = count * grid.cell_volume_m3
    return {
        "grid": list(grid.shape),
        "bar_cell_count": count,
        "bar_centre_z_m": grid.bar_centre_z_m,
        "mask_sha256": mask_sha256(mask),
        "exact_bar_volume_m3": exact_volume,
        "rasterized_bar_volume_m3": voxel_volume,
        "bar_mask_centroid_xyz_m": centroid.tolist(),
        "bar_mask_centroid_error_xyz_m": centroid_error.tolist(),
        "bar_mask_second_moment_tensor_m5": tensor.tolist(),
        "normalized_geometry_errors": {
            "bar_volume_relative": abs(voxel_volume - exact_volume) / exact_volume,
            "centroid_over_bar_radius": float(
                np.linalg.norm(centroid_error) / grid.bar_radius_m
            ),
            "second_moment_relative_frobenius": float(
                np.linalg.norm(tensor - exact_tensor) / np.linalg.norm(exact_tensor)
            ),
        },
    }


def _same_float_sequence(
    observed: Sequence[float], expected: Sequence[float], *, atol: float = 2.0e-18
) -> bool:
    return bool(
        np.allclose(
            np.asarray(observed, dtype=np.float64),
            np.asarray(expected, dtype=np.float64),
            rtol=0.0,
            atol=atol,
        )
    )


def _compare_level(
    observed: Mapping[str, Any], expected: Mapping[str, Any]
) -> dict[str, bool]:
    observed_errors = observed["normalized_geometry_errors"]
    expected_errors = expected["normalized_geometry_errors"]
    return {
        "grid": list(observed["grid"]) == list(expected["grid"]),
        "bar_cell_count": int(observed["bar_cell_count"])
        == int(expected["bar_cell_count"]),
        "centroid_error": _same_float_sequence(
            observed["bar_mask_centroid_error_xyz_m"],
            expected["bar_mask_centroid_error_xyz_m"],
        ),
        "second_moment_tensor": bool(
            np.allclose(
                np.asarray(observed["bar_mask_second_moment_tensor_m5"]),
                np.asarray(expected["bar_mask_second_moment_tensor_m5"]),
                rtol=0.0,
                atol=2.0e-25,
            )
        ),
        "normalized_errors": _same_float_sequence(
            [observed_errors[key] for key in sorted(observed_errors)],
            [expected_errors[key] for key in sorted(expected_errors)],
            atol=2.0e-15,
        ),
    }


def _rerasterize(
    grid: ImmersedCartesianGrid,
    enumeration: np.ndarray,
) -> np.ndarray:
    desired = int(np.sum(grid.bar_mask))
    flat_distance = np.asarray(grid.bar_signed_distance_m).ravel()
    order = np.argsort(flat_distance[enumeration], kind="stable")
    chosen = enumeration[order[:desired]]
    mask = np.zeros(grid.shape, dtype=bool)
    mask.ravel()[chosen] = True
    return mask


def _conditioning_variants(
    config: SimulationConfig,
    nominal: ImmersedCartesianGrid,
    preregistration: Mapping[str, Any],
) -> dict[str, Any]:
    expected = preregistration["source_level_finding"][
        "independent_recalculation_to_reproduce_before_changes"
    ]
    nominal_mask = np.asarray(nominal.bar_mask, dtype=bool)
    nominal_metrics = _metrics(nominal, nominal_mask)
    vessel_flat = np.flatnonzero(np.asarray(nominal.vessel_mask).ravel())
    distances = np.asarray(nominal.bar_signed_distance_m).ravel()[vessel_flat]
    sorted_distances = np.sort(distances, kind="stable")
    desired = int(np.sum(nominal_mask))
    cutoff = float(sorted_distances[desired - 1])
    exact_ties = int(np.sum(distances == cutoff))
    slots = desired - int(np.sum(distances < cutoff))
    c_mask = _rerasterize(nominal, vessel_flat)
    reversed_mask = _rerasterize(nominal, vessel_flat[::-1].copy())
    rng = np.random.default_rng(ENUMERATION_SEED)
    seeded_mask = _rerasterize(nominal, rng.permutation(vessel_flat))
    raw_centre = nominal.bar_centre_z_m
    down_centre = float(np.nextafter(raw_centre, -math.inf))
    up_centre = float(np.nextafter(raw_centre, math.inf))
    down_grid = _grid_with_centre(config, (90, 90, 80), down_centre)
    down_metrics = _metrics(down_grid, down_grid.bar_mask)
    down_difference = int(np.count_nonzero(nominal_mask ^ down_grid.bar_mask))
    del down_grid
    gc.collect()
    up_grid = _grid_with_centre(config, (90, 90, 80), up_centre)
    up_metrics = _metrics(up_grid, up_grid.bar_mask)
    up_difference = int(np.count_nonzero(nominal_mask ^ up_grid.bar_mask))
    del up_grid
    gc.collect()
    variants = {
        "nominal": nominal_metrics,
        "ulp_down": {
            "centre_z_m": down_centre,
            "centre_delta_m": down_centre - raw_centre,
            "symmetric_difference_cells_vs_nominal": down_difference,
            "metrics": down_metrics,
        },
        "ulp_up": {
            "centre_z_m": up_centre,
            "centre_delta_m": up_centre - raw_centre,
            "symmetric_difference_cells_vs_nominal": up_difference,
            "metrics": up_metrics,
        },
        "enumeration": {
            "c": {
                "symmetric_difference_cells_vs_formal": int(
                    np.count_nonzero(c_mask ^ nominal_mask)
                ),
                "metrics": _metrics(nominal, c_mask),
            },
            "reversed": {
                "symmetric_difference_cells_vs_formal": int(
                    np.count_nonzero(reversed_mask ^ nominal_mask)
                ),
                "metrics": _metrics(nominal, reversed_mask),
            },
            "seeded": {
                "seed": ENUMERATION_SEED,
                "symmetric_difference_cells_vs_formal": int(
                    np.count_nonzero(seeded_mask ^ nominal_mask)
                ),
                "metrics": _metrics(nominal, seeded_mask),
            },
        },
        "cutoff": {
            "signed_distance_m": cutoff,
            "exact_tie_cell_count": exact_ties,
            "slots_selected_from_exact_tie": slots,
        },
    }
    gates = {
        "nominal_cell_count": nominal_metrics["bar_cell_count"]
        == expected["bar_cell_count"],
        "nominal_centroid_error": _same_float_sequence(
            nominal_metrics["bar_mask_centroid_error_xyz_m"],
            expected["reported_centroid_error_xyz_m"],
        ),
        "cutoff_exact_tie_count": exact_ties == expected["cutoff_exact_tie_cell_count"],
        "cutoff_slots": slots == expected["cutoff_slots_selected_from_exact_tie"],
        "ulp_down_z_centroid": math.isclose(
            float(down_metrics["bar_mask_centroid_error_xyz_m"][2]),
            float(expected["one_ulp_down_bar_centre_z_expected_z_centroid_error_m"]),
            rel_tol=0.0,
            abs_tol=2.0e-18,
        ),
        "ulp_down_symmetric_difference": down_difference
        == expected["one_ulp_down_expected_mask_symmetric_difference_cells"],
        "formal_c_enumeration_identity": bool(
            variants["enumeration"]["c"]["symmetric_difference_cells_vs_formal"] == 0
        ),
        "reversed_x_centroid": math.isclose(
            float(
                variants["enumeration"]["reversed"]["metrics"][
                    "bar_mask_centroid_error_xyz_m"
                ][0]
            ),
            float(
                expected[
                    "reversed_equal_distance_enumeration_expected_x_centroid_error_m"
                ]
            ),
            rel_tol=0.0,
            abs_tol=2.0e-18,
        ),
        "reversed_symmetric_difference": variants["enumeration"]["reversed"][
            "symmetric_difference_cells_vs_formal"
        ]
        == expected[
            "reversed_equal_distance_enumeration_expected_mask_symmetric_difference_cells"
        ],
    }
    variants["gates"] = gates
    variants["pass"] = all(gates.values())
    return variants


def run_reproduction(
    base_config_path: Path,
    preregistration_path: Path,
    archived_report_path: Path,
) -> dict[str, Any]:
    preregistration = read_json_strict(preregistration_path)
    archived_report = read_json_strict(archived_report_path)
    if not isinstance(preregistration, Mapping) or not isinstance(
        archived_report, Mapping
    ):
        raise TypeError("A0 inputs must be JSON objects")
    porcelain_start = _git("status", "--porcelain", "--untracked-files=normal")
    git_identity = {
        "head": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "worktree_clean_at_start": porcelain_start == "",
        "porcelain_at_start": porcelain_start.splitlines(),
    }
    source_start = source_tree_identity(FORMAL_ROOT)
    config = load_config_json(base_config_path)
    archived_by_level = {
        float(item["cells_per_bar_diameter"]): item
        for item in archived_report["levels"]
    }
    levels: list[dict[str, Any]] = []
    comparisons: dict[str, dict[str, bool]] = {}
    conditioning: dict[str, Any] | None = None
    for label, dimensions in LEVELS:
        grid = _grid(config, dimensions)
        observed = _metrics(grid, grid.bar_mask)
        observed["cells_per_bar_diameter"] = label
        levels.append(observed)
        comparisons[str(label)] = _compare_level(observed, archived_by_level[label])
        if label == 6.0:
            conditioning = _conditioning_variants(config, grid, preregistration)
        del grid
        gc.collect()
    if conditioning is None:
        raise RuntimeError("A0 did not construct the registered 6-cell level")
    source_end = source_tree_identity(FORMAL_ROOT)
    identity_gates = {
        "main_branch": git_identity["branch"] == "main",
        "clean_worktree_at_start": git_identity["worktree_clean_at_start"],
        "formal_source_available": bool(source_start.get("available")),
        "formal_source_stable": source_start == source_end,
        "direct_formal_class_import": Path(
            inspect.getsourcefile(ImmersedCartesianGrid) or ""
        ).resolve()
        == (FORMAL_SOURCE / "airvortex/numerics/cartesian3d.py").resolve(),
    }
    archive_gates = {
        label: all(checks.values()) for label, checks in comparisons.items()
    }
    gate_pass = bool(
        all(identity_gates.values())
        and all(archive_gates.values())
        and conditioning["pass"]
    )
    return {
        "schema_version": 1,
        "stage": "D1_A0",
        "status": "D1_A0_REPRODUCED" if gate_pass else "D1_A0_FAIL_CLOSED",
        "gate_pass": gate_pass,
        "dynamic_solver_steps": 0,
        "inputs": {
            "base_config": str(base_config_path),
            "base_config_sha256": _sha256(base_config_path),
            "preregistration": str(preregistration_path),
            "preregistration_sha256": _sha256(preregistration_path),
            "archived_d0_a_report": str(archived_report_path),
            "archived_d0_a_report_sha256": _sha256(archived_report_path),
        },
        "identity": {
            "git": git_identity,
            "formal_source_start": source_start,
            "formal_source_end": source_end,
            "gates": identity_gates,
        },
        "levels": levels,
        "archived_report_comparisons": comparisons,
        "archived_report_level_gates": archive_gates,
        "conditioning_6_cells": conditioning,
        "claim_boundary": {
            "static_source_conditioning_only": True,
            "proves_g0_caused_q1r3_dynamic_failure": False,
            "establishes_numerical_convergence": False,
            "validates_physics": False,
            "validates_experiment": False,
            "allows_apparatus_prediction": False,
        },
    }


def markdown(report: Mapping[str, Any]) -> str:
    rows: list[str] = []
    for item in report["levels"]:
        errors = item["normalized_geometry_errors"]
        comparisons = report["archived_report_comparisons"][
            str(float(item["cells_per_bar_diameter"]))
        ]
        rows.append(
            "| {level:g} | `{grid}` | {count} | {centroid:.9g} | {moment:.9g} | {match} |".format(
                level=float(item["cells_per_bar_diameter"]),
                grid=" × ".join(str(value) for value in item["grid"]),
                count=int(item["bar_cell_count"]),
                centroid=float(errors["centroid_over_bar_radius"]),
                moment=float(errors["second_moment_relative_frobenius"]),
                match=all(comparisons.values()),
            )
        )
    conditioning = report["conditioning_6_cells"]
    down = conditioning["ulp_down"]
    reverse = conditioning["enumeration"]["reversed"]
    return f"""# D1-A0 正式 G0 几何病态性只读复现

状态：`{report["status"]}`。

本步骤直接导入正式 `ImmersedCartesianGrid`，动态求解步数为 `0`，没有复制或修改正式栅格算法。

## D0-A 四层复现

| 格/磁子直径 | 网格 | 磁子单元数 | 质心误差/半径 | 二阶矩相对误差 | 与归档一致 |
|---:|---:|---:|---:|---:|:---:|
{chr(10).join(rows)}

## `6` 格层条件数证据

- 截止处精确并列单元：`{conditioning["cutoff"]["exact_tie_cell_count"]}`；剩余名额：`{conditioning["cutoff"]["slots_selected_from_exact_tie"]}`。
- 磁子中心向下移动一 ULP 后，掩膜对称差为 `{down["symmetric_difference_cells_vs_nominal"]}` 个单元，轴向质心误差为 `{down["metrics"]["bar_mask_centroid_error_xyz_m"][2]:.16g} m`。
- 反向枚举后，掩膜对称差为 `{reverse["symmetric_difference_cells_vs_formal"]}` 个单元，横向质心误差为 `{reverse["metrics"]["bar_mask_centroid_error_xyz_m"][0]:.16g} m`。
- A0 总门：`{report["gate_pass"]}`。

## 结论边界

该结果只复现静态源码病态性；它不证明 G0 是 Q1R3 动态不收敛的唯一原因，不建立数值收敛、物理验证、实验验证或装置预测。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-config",
        type=Path,
        default=(HERE.parent / "01_HF3D数值资格/generated_q1r3/space_L2_9cells.json"),
    )
    parser.add_argument(
        "--preregistration",
        type=Path,
        default=HERE / "d1_geometry_preregistration.json",
    )
    parser.add_argument(
        "--archived-report",
        type=Path,
        default=(HERE.parent / "03_Q1R4诊断门/reports/static_geometry_audit.json"),
    )
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    report = run_reproduction(
        args.base_config.resolve(),
        args.preregistration.resolve(),
        args.archived_report.resolve(),
    )
    _write_json(args.json_output.resolve(), report)
    args.markdown_output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.resolve().write_text(markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {"status": report["status"], "gate_pass": report["gate_pass"]},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
