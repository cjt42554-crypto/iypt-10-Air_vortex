#!/usr/bin/env python3
"""Independent contract replay for the D1 static audit report."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PHASE_VALUES = (-0.49, 0.0, 0.49)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must contain a JSON object")
    return value


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


def _thresholds(preregistration: Mapping[str, Any]) -> Mapping[str, Any]:
    return preregistration["d1_scope"]["A1_boolean_candidate"]["hard_gates"]


def _case_checks(
    case: Mapping[str, Any], thresholds: Mapping[str, Any]
) -> dict[str, bool]:
    if case.get("execution_pass") is not True:
        return {"execution": False}
    g1 = case["g1"]
    errors = g1["normalized_geometry_errors"]
    g2 = case["g2_global_reference"]
    return {
        "execution": True,
        "dynamic_steps_zero": case.get("dynamic_solver_steps") == 0,
        "volume": float(errors["bar_volume_relative"])
        <= float(thresholds["rasterized_bar_volume_relative_error_max"]),
        "centroid": float(errors["centroid_over_bar_radius"])
        <= float(thresholds["bar_centroid_error_over_radius_max"]),
        "second_moment": float(errors["second_moment_relative_frobenius"])
        <= float(thresholds["bar_second_moment_relative_frobenius_error_max"]),
        "six_connected": int(g1["solid_six_connected_component_count"])
        == int(thresholds["solid_six_connected_component_count"]),
        "ulp_and_enumeration_invariant": int(
            case["invariance"]["mask_hash_change_count"]
        )
        <= int(thresholds["ulp_and_enumeration_mask_hash_changes_allowed"]),
        "selection_problem_invariant": int(
            case["invariance"]["selection_problem_change_count"]
        )
        == 0,
        "g2_volume_interval": float(g2["volume_relative_interval_width"]) <= 1.0e-5,
        "g2_centroid_interval": float(g2["centroid_over_radius_interval_width"])
        <= 1.0e-5,
        "g2_second_moment_interval": float(g2["second_moment_relative_interval_width"])
        <= 1.0e-4,
    }


def _pairwise(
    nominal_cases: Sequence[Mapping[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    by_level = {float(case["cells_per_bar_diameter"]): case for case in nominal_cases}
    result: dict[str, Any] = {}
    for left, right in ((6.0, 9.0), (9.0, 13.5)):
        if left not in by_level or right not in by_level:
            result[f"{left:g}_to_{right:g}"] = {
                "pass": False,
                "reason": "missing nominal level",
            }
            continue
        left_errors = by_level[left]["g1"]["normalized_geometry_errors"]
        right_errors = by_level[right]["g1"]["normalized_geometry_errors"]
        jumps = {
            key: abs(float(right_errors[key]) - float(left_errors[key]))
            for key in left_errors
        }
        maximum = max(jumps.values())
        result[f"{left:g}_to_{right:g}"] = {
            "normalized_error_jumps": jumps,
            "maximum_jump": maximum,
            "pass": maximum <= threshold,
        }
    return result


def _g2_diagnostics(
    nominal_cases: Sequence[Mapping[str, Any]],
    phase_cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    nominal_records: list[dict[str, Any]] = []
    tagged_cases = [
        *((True, case) for case in nominal_cases),
        *((False, case) for case in phase_cases),
    ]
    for is_nominal, case in tagged_cases:
        if case.get("execution_pass") is not True:
            continue
        interval = case["g2_mask_comparison"][
            "weighted_symmetric_difference_volume_interval_m3"
        ]
        exact_volume = float(case["g1"]["exact_bar_volume_m3"])
        record = {
            "case_id": str(case["case_id"]),
            "cells_per_bar_diameter": float(case["cells_per_bar_diameter"]),
            "weighted_symmetric_difference_volume_interval_m3": [
                float(interval[0]),
                float(interval[1]),
            ],
            "relative_to_exact_volume_interval": [
                float(interval[0]) / exact_volume,
                float(interval[1]) / exact_volume,
            ],
        }
        records.append(record)
        if is_nominal:
            nominal_records.append(record)
    if not records:
        return {
            "registered_as_gate": False,
            "maximum_relative_upper_bound": None,
            "maximum_case_id": "NONE",
            "nominal_by_level": [],
        }
    worst = max(
        records,
        key=lambda item: float(item["relative_to_exact_volume_interval"][1]),
    )
    return {
        "registered_as_gate": False,
        "maximum_relative_upper_bound": float(
            worst["relative_to_exact_volume_interval"][1]
        ),
        "maximum_case_id": worst["case_id"],
        "nominal_by_level": sorted(
            nominal_records,
            key=lambda item: float(item["cells_per_bar_diameter"]),
        ),
    }


def analyse(
    audit_path: Path,
    preregistration_path: Path,
    a0_path: Path,
) -> dict[str, Any]:
    audit = _read_json(audit_path)
    preregistration = _read_json(preregistration_path)
    a0 = _read_json(a0_path)
    thresholds = _thresholds(preregistration)
    nominal = audit.get("nominal_cases", [])
    phases = audit.get("phase_sweep_cases", [])
    if not isinstance(nominal, Sequence) or not isinstance(phases, Sequence):
        raise TypeError("audit case collections are malformed")
    nominal_levels = [float(case["cells_per_bar_diameter"]) for case in nominal]
    expected_phases = {
        (level, *phase)
        for level in (6.0, 9.0)
        for phase in itertools.product(PHASE_VALUES, repeat=3)
    }
    observed_phases = {
        (
            float(case["cells_per_bar_diameter"]),
            *tuple(float(value) for value in case["phase_offsets_in_cell_units_xyz"]),
        )
        for case in phases
    }
    nominal_checks = {
        str(float(case["cells_per_bar_diameter"])): _case_checks(case, thresholds)
        for case in nominal
    }
    phase_checks = {
        str(case["case_id"]): _case_checks(case, thresholds) for case in phases
    }
    pairwise = _pairwise(
        nominal,
        float(thresholds["six_to_nine_or_nine_to_13p5_normalized_error_jump_max"]),
    )
    g2_diagnostics = _g2_diagnostics(nominal, phases)
    hard_nominal_pass = all(
        all(nominal_checks[str(level)].values())
        for level in (6.0, 9.0, 13.5)
        if str(level) in nominal_checks
    ) and all(str(level) in nominal_checks for level in (6.0, 9.0, 13.5))
    phase_pass = len(phase_checks) == 54 and all(
        all(checks.values()) for checks in phase_checks.values()
    )
    source_identity = audit.get("identity", {})
    audit_identity_gates = source_identity.get("gates", {})
    gates = {
        "a0_pass": a0.get("gate_pass") is True,
        "audit_schema": audit.get("schema_version") == 1,
        "audit_static_only": audit.get("dynamic_solver_steps") == 0,
        "audit_stop_point": audit.get("stop_point") == "STOP_FOR_PRO_REVIEW",
        "candidate_ids": audit.get("candidate_ids")
        == {
            "g1": "G1_SYMMETRY_MOMENT_CONSTRAINED_BOOLEAN_MASK",
            "g2": "G2_ADAPTIVE_SUBCELL_CAPSULE_REFERENCE",
        },
        "nominal_level_identity": nominal_levels == [4.0, 6.0, 9.0, 13.5],
        "nominal_hard_gates": hard_nominal_pass,
        "phase_set_identity": observed_phases == expected_phases,
        "phase_hard_gates": phase_pass,
        "pairwise_jump_gates": all(item["pass"] for item in pairwise.values()),
        "audit_identity_gates": bool(audit_identity_gates)
        and all(bool(value) for value in audit_identity_gates.values()),
        "audit_report_self_consistency": audit.get("gate_pass")
        is (
            hard_nominal_pass
            and phase_pass
            and all(item["pass"] for item in pairwise.values())
            and a0.get("gate_pass") is True
            and bool(audit_identity_gates)
            and all(bool(value) for value in audit_identity_gates.values())
        ),
    }
    gate_pass = all(gates.values())
    status = (
        "D1_STATIC_G1_PASS_CONFIRMED__STOP_FOR_PRO_REVIEW"
        if gate_pass
        else "D1_STATIC_G1_FAIL_OR_UNRESOLVED__STOP_FOR_PRO_REVIEW"
    )
    return {
        "schema_version": 1,
        "status": status,
        "gate_pass": gate_pass,
        "stop_point": "STOP_FOR_PRO_REVIEW",
        "dynamic_solver_steps": 0,
        "inputs": {
            "audit_report": str(audit_path),
            "audit_report_sha256": _sha256(audit_path),
            "preregistration": str(preregistration_path),
            "preregistration_sha256": _sha256(preregistration_path),
            "a0_report": str(a0_path),
            "a0_report_sha256": _sha256(a0_path),
        },
        "thresholds": dict(thresholds),
        "nominal_checks": nominal_checks,
        "phase_checks": phase_checks,
        "pairwise_checks": pairwise,
        "g2_weighted_symmetric_difference_diagnostics": g2_diagnostics,
        "gates": gates,
        "conditional_next_request": (
            "REQUEST_PRO_REVIEW_FOR_CANDIDATE_ONLY_G0_VS_G1_DYNAMIC_SHADOW"
            if gate_pass
            else "STOP_MINIMAL_BOOLEAN_PATH_AND_PREPARE_ARCHITECTURE_DECISION"
        ),
        "claim_boundary": {
            "formal_solver_promotion_authorized": False,
            "dynamic_shadow_authorized": False,
            "d0_b_or_d0_c_authorized": False,
            "full_q1r4_authorized": False,
            "numerical_qualification_demonstrated": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
    }


def _worst(
    checks: Mapping[str, Any],
    audit: Mapping[str, Any],
    level: float,
    metric: str,
) -> tuple[float, str]:
    del checks
    cases = [
        case
        for case in audit["phase_sweep_cases"]
        if case.get("execution_pass") is True
        and float(case["cells_per_bar_diameter"]) == level
    ]
    if not cases:
        return float("inf"), "NONE"
    case = max(
        cases,
        key=lambda item: float(item["g1"]["normalized_geometry_errors"][metric]),
    )
    return (
        float(case["g1"]["normalized_geometry_errors"][metric]),
        str(case["case_id"]),
    )


def markdown(decision: Mapping[str, Any], audit: Mapping[str, Any]) -> str:
    rows: list[str] = []
    for level in (6.0, 9.0):
        volume, volume_case = _worst(
            decision["phase_checks"], audit, level, "bar_volume_relative"
        )
        centroid, centroid_case = _worst(
            decision["phase_checks"], audit, level, "centroid_over_bar_radius"
        )
        moment, moment_case = _worst(
            decision["phase_checks"],
            audit,
            level,
            "second_moment_relative_frobenius",
        )
        rows.append(
            f"| {level:g} | {volume:.7g} (`{volume_case}`) | {centroid:.7g} "
            f"(`{centroid_case}`) | {moment:.7g} (`{moment_case}`) |"
        )
    g2 = decision["g2_weighted_symmetric_difference_diagnostics"]
    g2_rows = [
        "| {level:g} | `[{lower:.9g}, {upper:.9g}]` | "
        "`[{lower_relative:.7g}, {upper_relative:.7g}]` |".format(
            level=float(item["cells_per_bar_diameter"]),
            lower=float(item["weighted_symmetric_difference_volume_interval_m3"][0]),
            upper=float(item["weighted_symmetric_difference_volume_interval_m3"][1]),
            lower_relative=float(item["relative_to_exact_volume_interval"][0]),
            upper_relative=float(item["relative_to_exact_volume_interval"][1]),
        )
        for item in g2["nominal_by_level"]
    ]
    g2_maximum = (
        "NONE"
        if g2["maximum_relative_upper_bound"] is None
        else f"{float(g2['maximum_relative_upper_bound']):.9g}"
    )
    return rf"""# D1 静态几何独立合同复核与停止裁决

状态：`{decision["status"]}`。

本文件从已落盘严格 JSON 独立重算全部门，不运行几何生成器或动态求解器。

## 相位扫描最坏值

| 格/磁子直径 | 最大体积误差（算例） | 最大质心误差（算例） | 最大二阶矩误差（算例） |
|---:|---:|---:|---:|
{chr(10).join(rows)}

## G1–G2 加权对称差诊断

该项在机器合同中要求记录，但没有登记 PASS 阈值，因此不参与上述静态总门。它仍是后续 Pro 复核必须查看的几何差异量。

| 名义格/磁子直径 | 对称差体积区间 ($\mathrm{{m}}^3$) | 相对解析体积区间 |
|---:|:---|:---|
{chr(10).join(g2_rows)}

全部 58 个静态算例中的最大相对上界为 `{g2_maximum}`，出现于 `{g2["maximum_case_id"]}`。

## 复核门

- A0 复现：`{decision["gates"]["a0_pass"]}`。
- 名义 `6/9/13.5` 层硬门：`{decision["gates"]["nominal_hard_gates"]}`。
- 登记的 54 个相位算例：`{decision["gates"]["phase_hard_gates"]}`。
- 层间误差跳变：`{decision["gates"]["pairwise_jump_gates"]}`。
- 报告身份与自洽性：`{decision["gates"]["audit_identity_gates"] and decision["gates"]["audit_report_self_consistency"]}`。
- 独立复核总门：`{decision["gate_pass"]}`。

## 停止点

当前必须停在 `STOP_FOR_PRO_REVIEW`。条件性下一请求为 `{decision["conditional_next_request"]}`，它只是提交给 Pro 的复核请求，不构成动态运行或正式提升授权。

## 结论上限

D1 仍是候选静态几何设计。它不证明 Q1R3/Q1R4 数值收敛，不构成物理或实验验证，不允许装置预测，也不授权修改正式理论或程序。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audit-report",
        type=Path,
        default=HERE / "reports/d1_static_audit.json",
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
    audit = _read_json(args.audit_report.resolve())
    decision = analyse(
        args.audit_report.resolve(),
        args.preregistration.resolve(),
        args.a0_report.resolve(),
    )
    _write_json(args.json_output.resolve(), decision)
    args.markdown_output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.resolve().write_text(
        markdown(decision, audit), encoding="utf-8"
    )
    print(
        json.dumps(
            {"status": decision["status"], "gate_pass": decision["gate_pass"]},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if decision["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
