#!/usr/bin/env python3
"""Execute the preregistered D1 A1/A2/A3 static geometry audit."""

from __future__ import annotations

import argparse
import gc
import hashlib
import itertools
import json
import math
import subprocess
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from copy import deepcopy
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

from airvortex.io.archive import source_tree_identity
from airvortex.io.config_io import load_config_json, read_json_strict
from controlled_bar_geometry import (
    G1_CANDIDATE_ID,
    G2_CANDIDATE_ID,
    CandidateInfeasible,
    Capsule,
    GridSpec,
    adaptive_capsule_global_reference,
    adaptive_mask_capsule_intersection,
    assess_pairwise_jumps,
    build_g1_mask,
    canonicalize_capsule,
)

LEVELS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (4.0, (60, 60, 54)),
    (6.0, (90, 90, 80)),
    (9.0, (136, 136, 120)),
    (13.5, (204, 204, 180)),
)
PHASE_VALUES = (-0.49, 0.0, 0.49)
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


def _phase_token(value: float) -> str:
    if value == 0.0:
        return "0"
    return ("p" if value > 0.0 else "m") + str(abs(value)).replace(".", "p")


def _case_id(level: float, phase: tuple[float, float, float]) -> str:
    return "L{level}_dx_{x}_dy_{y}_dz_{z}".format(
        level=str(level).replace(".", "p"),
        x=_phase_token(phase[0]),
        y=_phase_token(phase[1]),
        z=_phase_token(phase[2]),
    )


def _capsule_from_phase(
    grid: GridSpec,
    half_length_m: float,
    radius_m: float,
    nominal_centre_z_m: float,
    phase: tuple[float, float, float],
) -> Capsule:
    return Capsule(
        half_length_m,
        radius_m,
        (
            phase[0] * grid.dx,
            phase[1] * grid.dy,
            nominal_centre_z_m + phase[2] * grid.dz,
        ),
    )


def _g2_for_centre(template: Mapping[str, Any], capsule: Capsule) -> dict[str, Any]:
    result = deepcopy(dict(template))
    canonical = canonicalize_capsule(capsule)
    result["centroid_interval_xyz_m"] = [
        [value, value] for value in canonical.centre_xyz_m
    ]
    result["shape_reference_reused_for_rigid_translation"] = True
    return result


def _invariance_summary(build: Any) -> dict[str, Any]:
    report = build.report
    return {
        "mask_sha256": report["mask_sha256"],
        "selection_problem_sha256": report["selection_problem_sha256"],
        "selection_cache_hit": report["selection_cache_hit"],
        "canonical_centre_xyz_m": report["canonical_capsule"]["centre_xyz_m"],
        "canonicalisation_maximum_absolute_change_m": report[
            "canonicalisation_maximum_absolute_change_m"
        ],
        "enumeration_mode": report["construction"]["enumeration_mode"],
    }


def audit_case(
    level: float,
    grid: GridSpec,
    capsule: Capsule,
    phase: tuple[float, float, float],
    global_reference_template: Mapping[str, Any],
    selection_cache: MutableMapping[str, tuple[Any, dict[str, Any]]],
) -> dict[str, Any]:
    case_id = _case_id(level, phase)
    try:
        base = build_g1_mask(
            grid,
            capsule,
            enumeration_mode="c",
            enumeration_seed=ENUMERATION_SEED,
            selection_cache=selection_cache,
        )
        centre = capsule.centre_xyz_m
        ulp_down = Capsule(
            capsule.half_length_m,
            capsule.radius_m,
            (centre[0], centre[1], float(np.nextafter(centre[2], -math.inf))),
        )
        ulp_up = Capsule(
            capsule.half_length_m,
            capsule.radius_m,
            (centre[0], centre[1], float(np.nextafter(centre[2], math.inf))),
        )
        variants = {
            "ulp_down_z": build_g1_mask(
                grid,
                ulp_down,
                enumeration_mode="c",
                enumeration_seed=ENUMERATION_SEED,
                selection_cache=selection_cache,
            ),
            "ulp_up_z": build_g1_mask(
                grid,
                ulp_up,
                enumeration_mode="c",
                enumeration_seed=ENUMERATION_SEED,
                selection_cache=selection_cache,
            ),
            "reversed_enumeration": build_g1_mask(
                grid,
                capsule,
                enumeration_mode="reversed",
                enumeration_seed=ENUMERATION_SEED,
                selection_cache=selection_cache,
            ),
            "seeded_enumeration": build_g1_mask(
                grid,
                capsule,
                enumeration_mode="seeded",
                enumeration_seed=ENUMERATION_SEED,
                selection_cache=selection_cache,
            ),
        }
        base_hash = base.report["mask_sha256"]
        variant_summaries = {
            name: _invariance_summary(build) for name, build in variants.items()
        }
        changed = [
            name
            for name, summary in variant_summaries.items()
            if summary["mask_sha256"] != base_hash
        ]
        problem_changes = [
            name
            for name, summary in variant_summaries.items()
            if summary["selection_problem_sha256"]
            != base.report["selection_problem_sha256"]
        ]
        g2_global = _g2_for_centre(global_reference_template, capsule)
        g2_local = adaptive_mask_capsule_intersection(
            grid, capsule, base.selected_indices
        )
        normalized = base.report["normalized_geometry_errors"]
        individual_gates = {
            "bar_volume": float(normalized["bar_volume_relative"]) <= 0.01,
            "centroid": float(normalized["centroid_over_bar_radius"]) <= 0.05,
            "second_moment": float(normalized["second_moment_relative_frobenius"])
            <= 0.05,
            "six_connected": base.report["solid_six_connected_component_count"] == 1,
            "ulp_and_enumeration_hash_invariant": len(changed) == 0,
            "selection_problem_invariant": len(problem_changes) == 0,
            "g2_global_uncertainty": all(g2_global["uncertainty_gates"].values()),
        }
        result = {
            "case_id": case_id,
            "cells_per_bar_diameter": level,
            "phase_offsets_in_cell_units_xyz": list(phase),
            "phase_offsets_m_xyz": [
                phase[0] * grid.dx,
                phase[1] * grid.dy,
                phase[2] * grid.dz,
            ],
            "execution_pass": True,
            "dynamic_solver_steps": 0,
            "g1": base.report,
            "g2_global_reference": g2_global,
            "g2_mask_comparison": g2_local,
            "invariance": {
                "base": _invariance_summary(base),
                "variants": variant_summaries,
                "mask_hash_change_count": len(changed),
                "mask_hash_changed_variants": changed,
                "selection_problem_change_count": len(problem_changes),
                "selection_problem_changed_variants": problem_changes,
                "pass": len(changed) == 0 and len(problem_changes) == 0,
            },
            "individual_gates": individual_gates,
            "individual_gate_pass": all(individual_gates.values()),
        }
        del variants
        del base
        gc.collect()
        return result
    except (CandidateInfeasible, ValueError, RuntimeError) as error:
        return {
            "case_id": case_id,
            "cells_per_bar_diameter": level,
            "phase_offsets_in_cell_units_xyz": list(phase),
            "execution_pass": False,
            "dynamic_solver_steps": 0,
            "error_type": type(error).__name__,
            "error": str(error),
            "individual_gate_pass": False,
        }


def run_audit(
    base_config_path: Path,
    preregistration_path: Path,
    a0_report_path: Path,
) -> dict[str, Any]:
    preregistration = read_json_strict(preregistration_path)
    a0_report = read_json_strict(a0_report_path)
    if not isinstance(preregistration, Mapping) or not isinstance(a0_report, Mapping):
        raise TypeError("D1 audit inputs must be JSON objects")
    if a0_report.get("gate_pass") is not True:
        raise RuntimeError("D1-A0 did not pass; A1/A2/A3 are fail-closed")
    porcelain_start = _git("status", "--porcelain", "--untracked-files=normal")
    git_start = {
        "head": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "worktree_clean": porcelain_start == "",
        "porcelain": porcelain_start.splitlines(),
    }
    if git_start["branch"] != "main" or not git_start["worktree_clean"]:
        raise RuntimeError("D1 static audit requires a clean main worktree")
    source_start = source_tree_identity(FORMAL_ROOT)
    config = load_config_json(base_config_path)
    vessel_radius = config.container.radius_m
    domain_height = (
        config.container.liquid_depth_m + config.container.headspace_height_m
    )
    bar_half_length = config.stir_bar.half_length_m
    bar_radius = config.stir_bar.radius_m
    nominal_centre_z = config.stir_bar.bottom_clearance_m + config.stir_bar.radius_m
    reference_template = adaptive_capsule_global_reference(
        Capsule(bar_half_length, bar_radius, (0.0, 0.0, nominal_centre_z))
    )
    nominal_cases: list[dict[str, Any]] = []
    phase_cases: list[dict[str, Any]] = []
    selection_cache: dict[str, tuple[Any, dict[str, Any]]] = {}
    for level, dimensions in LEVELS:
        grid = GridSpec(*dimensions, vessel_radius, domain_height)
        nominal_capsule = _capsule_from_phase(
            grid,
            bar_half_length,
            bar_radius,
            nominal_centre_z,
            (0.0, 0.0, 0.0),
        )
        nominal_cases.append(
            audit_case(
                level,
                grid,
                nominal_capsule,
                (0.0, 0.0, 0.0),
                reference_template,
                selection_cache,
            )
        )
        if level in (6.0, 9.0):
            for phase in itertools.product(PHASE_VALUES, repeat=3):
                phase_tuple = tuple(float(value) for value in phase)
                capsule = _capsule_from_phase(
                    grid,
                    bar_half_length,
                    bar_radius,
                    nominal_centre_z,
                    phase_tuple,
                )
                phase_cases.append(
                    audit_case(
                        level,
                        grid,
                        capsule,
                        phase_tuple,
                        reference_template,
                        selection_cache,
                    )
                )
        gc.collect()
    successful_nominal = [case for case in nominal_cases if case["execution_pass"]]
    pairwise = (
        assess_pairwise_jumps(successful_nominal)
        if len(successful_nominal) == len(nominal_cases)
        else {}
    )
    hard_nominal = [
        case
        for case in nominal_cases
        if float(case["cells_per_bar_diameter"]) in (6.0, 9.0, 13.5)
    ]
    gates = {
        "a0_reproduced": a0_report.get("gate_pass") is True,
        "nominal_hard_levels": all(
            case.get("individual_gate_pass") is True for case in hard_nominal
        ),
        "phase_sweep_6_and_9": len(phase_cases) == 54
        and all(case.get("individual_gate_pass") is True for case in phase_cases),
        "pairwise_error_jumps": len(pairwise) == 2
        and all(item["pass"] for item in pairwise.values()),
        "g2_global_uncertainty": all(reference_template["uncertainty_gates"].values()),
        "all_dynamic_steps_zero": all(
            case.get("dynamic_solver_steps") == 0
            for case in nominal_cases + phase_cases
        ),
    }
    source_end = source_tree_identity(FORMAL_ROOT)
    git_end_head = _git("rev-parse", "HEAD")
    identity_gates = {
        "main_branch": git_start["branch"] == "main",
        "clean_worktree_at_start": git_start["worktree_clean"],
        "git_head_stable": git_start["head"] == git_end_head,
        "formal_source_available": bool(source_start.get("available")),
        "formal_source_stable": source_start == source_end,
    }
    gate_pass = all(gates.values()) and all(identity_gates.values())
    status = (
        "D1_STATIC_G1_PASS__STOP_FOR_PRO_REVIEW"
        if gate_pass
        else "D1_STATIC_G1_FAIL__STOP_FOR_PRO_REVIEW"
    )
    return {
        "schema_version": 1,
        "stage": "D1_A1_A2_A3_STATIC",
        "status": status,
        "gate_pass": gate_pass,
        "stop_point": "STOP_FOR_PRO_REVIEW",
        "dynamic_solver_steps": 0,
        "candidate_ids": {
            "g1": G1_CANDIDATE_ID,
            "g2": G2_CANDIDATE_ID,
        },
        "inputs": {
            "base_config": str(base_config_path),
            "base_config_sha256": _sha256(base_config_path),
            "preregistration": str(preregistration_path),
            "preregistration_sha256": _sha256(preregistration_path),
            "a0_report": str(a0_report_path),
            "a0_report_sha256": _sha256(a0_report_path),
            "controlled_geometry_source_sha256": _sha256(
                HERE / "controlled_bar_geometry.py"
            ),
            "audit_source_sha256": _sha256(Path(__file__).resolve()),
        },
        "identity": {
            "git_start": git_start,
            "git_end_head": git_end_head,
            "formal_source_start": source_start,
            "formal_source_end": source_end,
            "gates": identity_gates,
        },
        "g2_shape_reference": reference_template,
        "nominal_cases": nominal_cases,
        "phase_sweep_cases": phase_cases,
        "pairwise_checks": pairwise,
        "gates": gates,
        "claim_boundary": {
            "static_candidate_geometry_only": True,
            "formal_solver_promotion_authorized": False,
            "dynamic_shadow_authorized": False,
            "d0_b_or_d0_c_authorized": False,
            "full_q1r4_authorized": False,
            "establishes_numerical_convergence": False,
            "validates_physics": False,
            "validates_experiment": False,
            "allows_apparatus_prediction": False,
        },
    }


def _phase_maximum(
    cases: Sequence[Mapping[str, Any]], level: float, metric: str
) -> tuple[float, str]:
    candidates = [
        case
        for case in cases
        if case.get("execution_pass") is True
        and float(case["cells_per_bar_diameter"]) == level
    ]
    if not candidates:
        return math.inf, "NONE"
    worst = max(
        candidates,
        key=lambda case: float(case["g1"]["normalized_geometry_errors"][metric]),
    )
    return (
        float(worst["g1"]["normalized_geometry_errors"][metric]),
        str(worst["case_id"]),
    )


def markdown(report: Mapping[str, Any]) -> str:
    nominal_rows: list[str] = []
    for case in report["nominal_cases"]:
        if not case["execution_pass"]:
            nominal_rows.append(
                f"| {case['cells_per_bar_diameter']:g} | ERROR | — | — | — | — |"
            )
            continue
        g1 = case["g1"]
        errors = g1["normalized_geometry_errors"]
        nominal_rows.append(
            "| {level:g} | {count} | {volume:.7g} | {centroid:.7g} | "
            "{moment:.7g} | {components} |".format(
                level=float(case["cells_per_bar_diameter"]),
                count=int(g1["bar_cell_count"]),
                volume=float(errors["bar_volume_relative"]),
                centroid=float(errors["centroid_over_bar_radius"]),
                moment=float(errors["second_moment_relative_frobenius"]),
                components=int(g1["solid_six_connected_component_count"]),
            )
        )
    phase_rows: list[str] = []
    for level in (6.0, 9.0):
        volume, volume_case = _phase_maximum(
            report["phase_sweep_cases"], level, "bar_volume_relative"
        )
        centroid, centroid_case = _phase_maximum(
            report["phase_sweep_cases"], level, "centroid_over_bar_radius"
        )
        moment, moment_case = _phase_maximum(
            report["phase_sweep_cases"],
            level,
            "second_moment_relative_frobenius",
        )
        cases = [
            case
            for case in report["phase_sweep_cases"]
            if float(case["cells_per_bar_diameter"]) == level
        ]
        phase_rows.append(
            "| {level:g} | {count} | {volume:.7g} (`{volume_case}`) | "
            "{centroid:.7g} (`{centroid_case}`) | {moment:.7g} "
            "(`{moment_case}`) | {passed} |".format(
                level=level,
                count=len(cases),
                volume=volume,
                volume_case=volume_case,
                centroid=centroid,
                centroid_case=centroid_case,
                moment=moment,
                moment_case=moment_case,
                passed=all(case.get("individual_gate_pass") is True for case in cases),
            )
        )
    reference = report["g2_shape_reference"]
    return f"""# D1 可控磁子几何静态审计

状态：`{report["status"]}`。

本审计只执行静态 G1 布尔掩膜与诊断用 G2 亚单元参考，动态求解步数为 `0`。报告生成后按上位裁决立即停止。

## 名义层

| 格/磁子直径 | 磁子单元数 | 体积相对误差 | 质心误差/半径 | 二阶矩相对误差 | 六连通分量 |
|---:|---:|---:|---:|---:|---:|
{chr(10).join(nominal_rows)}

## `6/9` 格相位扫描

| 格/磁子直径 | 算例数 | 最大体积误差（算例） | 最大质心误差（算例） | 最大二阶矩误差（算例） | 全部门通过 |
|---:|---:|---:|---:|---:|:---:|
{chr(10).join(phase_rows)}

## G2 区间与总门

- 全局体积相对区间宽度：`{reference["volume_relative_interval_width"]:.9g}`。
- 质心/半径区间宽度：`{reference["centroid_over_radius_interval_width"]:.9g}`。
- 二阶矩相对区间宽度：`{reference["second_moment_relative_interval_width"]:.9g}`。
- 名义硬层门：`{report["gates"]["nominal_hard_levels"]}`。
- 54 个相位算例门：`{report["gates"]["phase_sweep_6_and_9"]}`。
- 层间误差跳变门：`{report["gates"]["pairwise_error_jumps"]}`。
- D1 静态总门：`{report["gate_pass"]}`。

## 停止与结论边界

停止点为 `STOP_FOR_PRO_REVIEW`。无论静态总门结果如何，本报告都不授权动态影子对照、D0-B、D0-C、完整 Q1R4 或正式程序提升；它也不建立数值收敛、物理验证、实验验证或装置预测。
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
        "--a0-report",
        type=Path,
        default=HERE / "reports/a0_g0_conditioning.json",
    )
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    report = run_audit(
        args.base_config.resolve(),
        args.preregistration.resolve(),
        args.a0_report.resolve(),
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
