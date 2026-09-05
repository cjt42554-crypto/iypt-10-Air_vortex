"""Verify that Q1R2 was already failed by completed spatial evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


SPATIAL_CASE_IDS = (
    "space_L0_4cells",
    "space_L1_6cells",
    "space_L2_9cells",
)
FINE_CASE_ID = "space_L2_9cells"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"missing mapping: {label}")
    return value


def assess(
    qualification: Mapping[str, Any],
    *,
    qualification_report_sha256: str,
    expected_source_sha256: str,
) -> dict[str, Any]:
    run_audits = _mapping(qualification.get("run_audits"), "run_audits")
    quality = _mapping(
        qualification.get("numerical_quality"), "numerical_quality"
    )
    groups = _mapping(qualification.get("groups"), "groups")
    spatial = _mapping(groups.get("spatial"), "groups.spatial")
    temporal = _mapping(groups.get("temporal"), "groups.temporal")
    terminal_failures = _mapping(
        qualification.get("terminal_failures"), "terminal_failures"
    )
    missing_cases = qualification.get("missing_cases")
    if not isinstance(missing_cases, list):
        raise ValueError("missing list: missing_cases")
    interrupted = _mapping(
        terminal_failures.get("time_L1_3.000e-06s"),
        "terminal_failures.time_L1_3.000e-06s",
    )
    effective = _mapping(
        spatial.get("effective_time_step_gate"),
        "groups.spatial.effective_time_step_gate",
    )
    effective_cases = effective.get("cases")
    if not isinstance(effective_cases, list):
        raise ValueError("missing effective time-step case records")
    by_id = {
        str(item.get("id")): item
        for item in effective_cases
        if isinstance(item, Mapping)
    }
    if set(by_id) != set(SPATIAL_CASE_IDS):
        raise ValueError("effective time-step records must be the three spatial cases")

    identity_by_case: dict[str, bool] = {}
    for case_id in SPATIAL_CASE_IDS:
        audit = _mapping(run_audits.get(case_id), f"run_audits.{case_id}")
        diagnostics = _mapping(
            audit.get("diagnostics"), f"run_audits.{case_id}.diagnostics"
        )
        identity_by_case[case_id] = bool(
            audit.get("completed") is True
            and audit.get("execution_source_identity_valid") is True
            and audit.get("physical_signature_match") is True
            and audit.get("source_tree_sha256_start") == expected_source_sha256
            and diagnostics.get("pressure_accelerator_selected") == "cuda"
            and diagnostics.get("pressure_accelerator_fallback") is False
        )

    fine_step = _mapping(by_id[FINE_CASE_ID], "fine effective step")
    active_fraction = float(fine_step["configured_step_active_fraction"])
    active_minimum = float(
        fine_step["minimum_configured_step_active_fraction"]
    )
    active_failure = bool(
        math.isfinite(active_fraction)
        and math.isfinite(active_minimum)
        and active_fraction < active_minimum
        and fine_step.get("pass") is False
        and effective.get("pass") is False
        and spatial.get("status") == "FAIL_EFFECTIVE_TIME_STEP_GATE"
    )

    fine_quality = _mapping(quality.get(FINE_CASE_ID), "fine quality")
    fine_checks = _mapping(fine_quality.get("checks"), "fine quality checks")
    mass = _mapping(
        fine_checks.get("liquid_mass_error_relative"),
        "fine liquid mass error",
    )
    mass_value = float(mass["value"])
    mass_maximum = float(mass["maximum"])
    mass_failure = bool(
        math.isfinite(mass_value)
        and math.isfinite(mass_maximum)
        and mass_value > mass_maximum
        and mass.get("pass") is False
        and fine_quality.get("pass") is False
    )
    balance = _mapping(
        fine_checks.get("liquid_volume_balance_error_relative"),
        "fine liquid volume balance error",
    )
    balance_value = float(balance["value"])
    balance_maximum = float(balance["maximum"])
    balance_pass = bool(
        math.isfinite(balance_value)
        and math.isfinite(balance_maximum)
        and balance_value <= balance_maximum
        and balance.get("pass") is True
    )

    gates = {
        "qualification_report_fail_closed_status": (
            qualification.get("status")
            == "FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE"
        ),
        "three_completed_spatial_archives_with_formal_cuda_identity": all(
            identity_by_case.values()
        ),
        "spatial_effective_time_step_gate_already_failed": active_failure,
        "fine_spatial_numerical_quality_gate_already_failed": mass_failure,
        "fine_open_boundary_conservation_balance_metric_passed": balance_pass,
        "temporal_group_not_completed": temporal.get("status") == "INCOMPLETE",
        "time_L2_is_missing_not_started": (
            "time_L2_1.500e-06s" in missing_cases
        ),
        "time_L1_interruption_diagnostic_integrity": bool(
            interrupted.get("complete_run_archive") is False
            and interrupted.get("terminal_archive_payload_integrity_valid") is True
            and interrupted.get("terminal_config_identity_valid") is True
            and interrupted.get("state") in {"cancelling", "cancelled"}
            and _mapping(
                interrupted.get("error"), "interrupted error"
            ).get("message")
            is None
        ),
        "apparatus_prediction_remains_disallowed": _mapping(
            qualification.get("claim_boundary"), "claim_boundary"
        ).get("apparatus_prediction_allowed")
        is False,
    }
    failure_established_before_temporal_completion = bool(
        gates["three_completed_spatial_archives_with_formal_cuda_identity"]
        and gates["spatial_effective_time_step_gate_already_failed"]
        and gates["fine_spatial_numerical_quality_gate_already_failed"]
    )
    gate_pass = bool(
        failure_established_before_temporal_completion
        and gates["qualification_report_fail_closed_status"]
        and gates["fine_open_boundary_conservation_balance_metric_passed"]
        and gates["temporal_group_not_completed"]
        and gates["time_L2_is_missing_not_started"]
        and gates["time_L1_interruption_diagnostic_integrity"]
        and gates["apparatus_prediction_remains_disallowed"]
    )
    return {
        "schema_version": 1,
        "status": (
            "Q1R2_FAIL_CLOSED_AFTER_COMPLETED_SPATIAL_GATES__TEMPORAL_NOT_COMPLETED"
            if gate_pass
            else "Q1R2_EARLY_STOP_DECISION_NOT_ESTABLISHED"
        ),
        "gate_pass": gate_pass,
        "qualification_report_sha256": qualification_report_sha256,
        "qualification_report_status": qualification.get("status"),
        "campaign_manifest_sha256": qualification.get(
            "campaign_manifest_sha256"
        ),
        "expected_formal_source_tree_sha256": expected_source_sha256,
        "gates": gates,
        "spatial_identity_by_case": identity_by_case,
        "decisive_failures": {
            "fine_registered_dt_active_fraction": active_fraction,
            "registered_dt_active_fraction_minimum": active_minimum,
            "fine_liquid_mass_error_relative": mass_value,
            "liquid_mass_error_relative_maximum": mass_maximum,
            "fine_liquid_volume_balance_error_relative": balance_value,
            "liquid_volume_balance_error_relative_maximum": balance_maximum,
        },
        "mass_gate_semantics": {
            "liquid_mass_error_relative_definition": (
                "(current_liquid_volume - initial_liquid_volume) / "
                "initial_liquid_volume"
            ),
            "liquid_volume_balance_error_relative_definition": (
                "(current_liquid_volume - "
                "expected_liquid_volume_after_prescribed_boundary_flux) / "
                "initial_liquid_volume"
            ),
            "registered_mass_gate_failure_is_a_frozen_contract_failure": True,
            "registered_mass_gate_failure_alone_proves_numerical_nonconservation": (
                False
            ),
            "formal_source_reference": (
                "02_模拟程序/src/airvortex/backends/high_fidelity_3d.py"
            ),
        },
        "execution_decision": {
            "failure_established_before_temporal_completion": (
                failure_established_before_temporal_completion
            ),
            "remaining_temporal_level_required_to_establish_q1r2_failure": False,
            "time_L1_intentionally_interrupted_after_spatial_decision": True,
            "time_L1_diagnostic_state": interrupted.get("state"),
            "time_L1_complete_run_archive": interrupted.get(
                "complete_run_archive"
            ),
            "time_L1_payload_integrity_valid": interrupted.get(
                "terminal_archive_payload_integrity_valid"
            ),
            "time_L2_not_started": gates["time_L2_is_missing_not_started"],
            "temporal_convergence_status": "NOT_EVALUATED",
            "resuming_temporal_levels_would_be_a_separate_diagnostic_task": True,
        },
        "claim_boundary": {
            "q1r2_passed": False,
            "spatial_convergence_evaluated": False,
            "temporal_convergence_evaluated": False,
            "numerical_qualification_demonstrated": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
    }


def markdown(report: Mapping[str, Any]) -> str:
    failures = report["decisive_failures"]
    decision = report["execution_decision"]
    return f"""# HF3D Q1R2 空间门失败闭锁裁决

状态：`{report['status']}`。

冻结分析器的顶层状态为 `{report['qualification_report_status']}`，因为时间中层在空间组已经给出不可补救失败后被主动停止，时间细层未启动。本报告不把该人为停止伪装成新的数值故障，而是核验停止前已经完整归档的三层空间证据。

## 已完成空间证据

- 三层空间算例均完整到 `0.02 s`，正式源码树身份一致，实际选择 CUDA 且无回退。
- 精细空间层登记步长命中率为 `{failures['fine_registered_dt_active_fraction']:.12g}`，低于冻结下限 `{failures['registered_dt_active_fraction_minimum']:.12g}`，因此空间实际时间步身份门已经失败。
- 精细空间层液体质量相对误差为 `{failures['fine_liquid_mass_error_relative']:.12g}`，超过冻结上限 `{failures['liquid_mass_error_relative_maximum']:.12g}`，因此单例数值质量门也已经失败。
- 但正式实现中的该量是相对初始体积的净变化，包含规定的开边界通量；真正按开边界通量修正的体积平衡残差为 `{failures['fine_liquid_volume_balance_error_relative']:.12g}`，低于上限 `{failures['liquid_volume_balance_error_relative_maximum']:.12g}`。因此这仍是不可事后删除的冻结门失败，却不能单独解释为求解器数值不守恒。
- 上述两项都来自时间中层启动前已完成的 `space_L2_9cells` 归档，后续时间层不能改变空间组的失败事实。

## 执行裁决

时间中层在 110 步后主动中断，运行进程已经退出；留下的诊断目录载荷哈希与配置身份完整，但控制器状态为 `{decision['time_L1_diagnostic_state']}` 且不是完整运行归档，因此不用于数值结论。时间细层未启动。继续两层只会成为失败后的额外诊断，不能使冻结 Q1R2 通过；是否恢复应作为下一次明确任务决定。当前时间收敛状态为 `{decision['temporal_convergence_status']}`，空间平滑量收敛也未进入评估，因为实际步长身份门先失败。

## 结论边界

Q1R2 没有通过数值筛查，也没有建立空间或时间收敛。正式事务性 CFL 控制器的 CUDA 实现级证据仍成立，但这不升级为数值资格、物理验证或实验验证；装置级定量预测继续禁止。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qualification_report", type=Path)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    qualification_path = args.qualification_report.resolve()
    report = assess(
        _read_json(qualification_path),
        qualification_report_sha256=_sha256(qualification_path),
        expected_source_sha256=args.expected_source_sha256,
    )
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "gate_pass": report["gate_pass"]}))
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
