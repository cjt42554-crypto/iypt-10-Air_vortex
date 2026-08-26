"""Freeze the evidence logic required before a Q1R3 fine-grid probe."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from hfq_cli_common import _explicit_config_differences
from hfq_convergence import convergence_estimate, gate_convergence
from hfq_grid import grid_audit
from hfq_json import canonical_sha256, physical_signature
from hfq_quality_contract import (
    OPEN_BOUNDARY_QUALITY_CONTRACT_ID,
    audit_quality_contract,
)
from hfq_types import SeriesData


SPATIAL_CASE_IDS = (
    "space_L0_4cells",
    "space_L1_6cells",
    "space_L2_9cells",
)
FINE_CASE_ID = "space_L2_9cells"
PROBE_DT_S = 5.5e-6
REJECTED_LARGER_DT_S = 5.6e-6


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


def _close(left: Any, right: float) -> bool:
    try:
        value = float(left)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and math.isclose(
        value,
        right,
        rel_tol=0.0,
        abs_tol=max(1.0e-15, abs(right) * 1.0e-12),
    )


def analyse(
    *,
    q1r2_manifest_path: Path,
    q1r2_runs: Path,
    q1r2_qualification_path: Path,
    next_dt_path: Path,
    q1r3_manifest_path: Path,
    expected_source_sha256: str,
) -> dict[str, Any]:
    from airvortex.io import load_run_archive

    q1r2_manifest = _read_json(q1r2_manifest_path)
    q1r2_qualification = _read_json(q1r2_qualification_path)
    next_dt = _read_json(next_dt_path)
    q1r3_manifest = _read_json(q1r3_manifest_path)

    q1r2_cases = {
        str(item.get("id")): item
        for item in q1r2_manifest.get("cases", [])
        if isinstance(item, Mapping) and item.get("group") == "spatial"
    }
    q1r3_cases = {
        str(item.get("id")): item
        for item in q1r3_manifest.get("cases", [])
        if isinstance(item, Mapping) and item.get("group") == "spatial"
    }
    if set(q1r2_cases) != set(SPATIAL_CASE_IDS):
        raise ValueError("Q1R2 manifest must contain exactly three spatial cases")
    if set(q1r3_cases) != set(SPATIAL_CASE_IDS):
        raise ValueError("Q1R3 design must contain exactly three spatial cases")

    loaded_cases: dict[str, Any] = {}
    archive_records: dict[str, Any] = {}
    for case_id in SPATIAL_CASE_IDS:
        entry = q1r2_cases[case_id]
        generated_config = _read_json(
            q1r2_manifest_path.parent / str(entry["config_file"])
        )
        archive_path = q1r2_runs / case_id
        loaded = load_run_archive(archive_path, verify=True)
        loaded_cases[case_id] = loaded
        archived_config = loaded.config.to_dict()
        source = _mapping(
            loaded.provenance.get("source_identity"),
            f"{case_id}.source_identity",
        )
        disk = _mapping(
            source.get("disk_content_tree"),
            f"{case_id}.disk_content_tree",
        )
        start = _mapping(disk.get("execution_start"), "execution_start").get(
            "value"
        )
        end = _mapping(disk.get("execution_end"), "execution_end").get("value")
        diagnostics = _mapping(
            loaded.result.metadata.get("diagnostics"),
            f"{case_id}.diagnostics",
        )
        accelerator = _mapping(
            diagnostics.get("pressure_accelerator"),
            f"{case_id}.pressure_accelerator",
        )
        archive_records[case_id] = {
            "archive_path": str(archive_path.resolve()),
            "archive_manifest_sha256": _sha256(archive_path / "manifest.json"),
            "completed": bool(
                loaded.result.status.value == "completed"
                and _close(
                    diagnostics.get("simulated_duration_s"),
                    float(loaded.config.run.duration_s),
                )
            ),
            "generated_config_identity": bool(
                canonical_sha256(generated_config) == entry.get("config_sha256")
                and not _explicit_config_differences(
                    generated_config,
                    archived_config,
                )
            ),
            "source_identity": bool(
                source.get("execution_source_identity_valid") is True
                and start == expected_source_sha256
                and end == expected_source_sha256
            ),
            "source_tree_sha256_start": start,
            "source_tree_sha256_end": end,
            "cuda_without_fallback": bool(
                accelerator.get("selected") == "cuda"
                and accelerator.get("fallback") is False
            ),
            "completed_steps": diagnostics.get("completed_steps"),
        }

    quality_gates = _mapping(
        _mapping(q1r3_manifest.get("gates"), "q1r3.gates").get(
            "numerical_quality"
        ),
        "q1r3.gates.numerical_quality",
    )
    smooth_observables = _mapping(
        q1r3_manifest.get("observables"), "q1r3.observables"
    ).get("smooth")
    if not isinstance(smooth_observables, list):
        raise ValueError("Q1R3 smooth observables must be an array")
    contract_audit = audit_quality_contract(
        quality_gates,
        smooth_observables=[str(item) for item in smooth_observables],
    )

    smooth_gate = _mapping(
        _mapping(q1r3_manifest.get("gates"), "q1r3.gates").get(
            "smooth_convergence"
        ),
        "q1r3.gates.smooth_convergence",
    )
    floors = _mapping(
        smooth_gate.get("absolute_scale_floors"),
        "smooth absolute scale floors",
    )
    grids = {
        case_id: grid_audit(loaded_cases[case_id].config.to_dict()).h_max_m
        for case_id in SPATIAL_CASE_IDS
    }
    prior_smooth: dict[str, Any] = {}
    for name in smooth_observables:
        series = {}
        for case_id in SPATIAL_CASE_IDS:
            loaded = loaded_cases[case_id]
            series[case_id] = SeriesData(
                np.asarray(loaded.result.time_s, dtype=np.float64),
                np.asarray(loaded.result.observables[str(name)], dtype=np.float64),
            )
        estimate = convergence_estimate(
            coarse=series[SPATIAL_CASE_IDS[0]],
            medium=series[SPATIAL_CASE_IDS[1]],
            fine=series[SPATIAL_CASE_IDS[2]],
            h_coarse=grids[SPATIAL_CASE_IDS[0]],
            h_medium=grids[SPATIAL_CASE_IDS[1]],
            h_fine=grids[SPATIAL_CASE_IDS[2]],
            absolute_scale_floor=float(floors.get(str(name), 0.0)),
            safety_factor=float(smooth_gate.get("gci_safety_factor", 1.25)),
        )
        prior_smooth[str(name)] = gate_convergence(
            estimate,
            relative_l2_max=float(smooth_gate["relative_l2_max"]),
            relative_linf_max=float(smooth_gate["relative_linf_max"]),
            gci_l2_max=float(smooth_gate["gci_l2_max"]),
            observed_order_min=float(smooth_gate["observed_order_min"]),
        )

    selected = _mapping(
        next_dt.get("selected_projection"), "selected_projection"
    )
    rejected = _mapping(
        next_dt.get("immediately_larger_rejected_projection"),
        "immediately_larger_rejected_projection",
    )
    selection_contract = _mapping(
        next_dt.get("selection_contract"), "selection_contract"
    )
    q1r3_fine_entry = q1r3_cases[FINE_CASE_ID]
    q1r3_fine_config_path = (
        q1r3_manifest_path.parent / str(q1r3_fine_entry["config_file"])
    )
    q1r3_fine_config = _read_json(q1r3_fine_config_path)
    high_fidelity = _mapping(
        q1r3_fine_config.get("high_fidelity"), "q1r3 fine high_fidelity"
    )
    run = _mapping(q1r3_fine_config.get("run"), "q1r3 fine run")

    q1r2_quality = _mapping(
        q1r2_qualification.get("numerical_quality"),
        "q1r2 numerical_quality",
    )
    q1r2_fine_quality = _mapping(
        q1r2_quality.get(FINE_CASE_ID), "q1r2 fine quality"
    )
    q1r2_fine_checks = _mapping(
        q1r2_fine_quality.get("checks"), "q1r2 fine checks"
    )
    mass_check = _mapping(
        q1r2_fine_checks.get("liquid_mass_error_relative"),
        "q1r2 fine mass check",
    )
    balance_check = _mapping(
        q1r2_fine_checks.get("liquid_volume_balance_error_relative"),
        "q1r2 fine balance check",
    )

    design_fraction = float(selection_contract.get("design_fraction"))
    gates = {
        "q1r2_failure_evidence_preserved": bool(
            q1r2_qualification.get("status")
            == "FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE"
            and mass_check.get("pass") is False
            and balance_check.get("pass") is True
        ),
        "three_q1r2_spatial_archives_complete_and_bound": all(
            record["completed"]
            and record["generated_config_identity"]
            and record["source_identity"]
            and record["cuda_without_fallback"]
            for record in archive_records.values()
        ),
        "corrected_next_dt_source_identity": bool(
            _mapping(next_dt.get("input"), "next_dt.input").get(
                "formal_source_tree_sha256"
            )
            == expected_source_sha256
        ),
        "buffered_dt_projection_selects_5_5us": bool(
            next_dt.get("status")
            == "DIAGNOSTIC_ONLY__Q1R3_COMMON_DT_NOT_FROZEN"
            and _close(selected.get("candidate_s"), PROBE_DT_S)
            and float(selected.get("minimum_predicted_active_fraction"))
            >= design_fraction
            and _close(rejected.get("candidate_s"), REJECTED_LARGER_DT_S)
            and float(rejected.get("minimum_predicted_active_fraction"))
            < design_fraction
        ),
        "open_boundary_quality_contract_frozen_in_design": bool(
            contract_audit.get("pass") is True
            and contract_audit.get("contract_id")
            == OPEN_BOUNDARY_QUALITY_CONTRACT_ID
            and q1r3_manifest.get("quality_contract_audit") == contract_audit
        ),
        "draft_static_refinement_design_pass": bool(
            _mapping(
                q1r3_manifest.get("refinement_design_audit"),
                "q1r3 refinement audit",
            ).get("pass")
            is True
        ),
        "physical_signature_unchanged": bool(
            q1r3_manifest.get("physical_signature_sha256")
            == q1r2_manifest.get("physical_signature_sha256")
            and canonical_sha256(physical_signature(q1r3_fine_config))
            == q1r3_manifest.get("physical_signature_sha256")
        ),
        "fine_probe_input_exact": bool(
            _close(high_fidelity.get("time_step_s"), PROBE_DT_S)
            and _close(run.get("duration_s"), 0.02)
            and high_fidelity.get("pressure_solver_device") == "cuda"
            and _close(high_fidelity.get("pressure_relative_tolerance"), 1.0e-7)
            and _close(
                high_fidelity.get("cfl_controller_target_fraction"), 0.95
            )
            and high_fidelity.get("maximum_cfl_step_retries") == 4
        ),
    }
    gate_pass = all(gates.values())
    return {
        "schema_version": 1,
        "status": (
            "Q1R3_PREREGISTRATION_PASS__FINE_PROBE_REQUIRED"
            if gate_pass
            else "Q1R3_PREREGISTRATION_FAIL__PROBE_NOT_AUTHORIZED"
        ),
        "gate_pass": gate_pass,
        "gates": gates,
        "expected_formal_source_tree_sha256": expected_source_sha256,
        "inputs": {
            "q1r2_manifest_sha256": _sha256(q1r2_manifest_path),
            "q1r2_qualification_sha256": _sha256(q1r2_qualification_path),
            "corrected_next_dt_diagnostic_sha256": _sha256(next_dt_path),
            "q1r3_probe_design_manifest_sha256": _sha256(q1r3_manifest_path),
        },
        "quality_contract": contract_audit,
        "q1r2_semantic_counterexample": {
            "fine_legacy_inventory_change_value": mass_check.get("value"),
            "fine_legacy_inventory_threshold": mass_check.get("maximum"),
            "fine_flux_corrected_balance_value": balance_check.get("value"),
            "fine_flux_corrected_balance_threshold": balance_check.get("maximum"),
        },
        "probe": {
            "config_path": str(q1r3_fine_config_path.resolve()),
            "config_file_sha256": _sha256(q1r3_fine_config_path),
            "config_canonical_sha256": canonical_sha256(q1r3_fine_config),
            "registered_dt_s": PROBE_DT_S,
            "duration_s": 0.02,
            "minimum_actual_registered_dt_fraction": float(
                selection_contract.get("acceptance_fraction")
            ),
            "must_execute_before_q1r3_freeze": True,
        },
        "prior_q1r2_spatial_smooth_diagnostic": {
            "evidence_role": (
                "RISK_DIAGNOSTIC_ONLY__Q1R2_EFFECTIVE_STEP_GATE_FAILED"
            ),
            "all_observables_pass": all(
                result.get("pass") is True for result in prior_smooth.values()
            ),
            "observables": prior_smooth,
        },
        "archive_identity": archive_records,
        "decision_boundary": {
            "q1r3_frozen": False,
            "fine_probe_executed": False,
            "prior_q1r2_smooth_diagnostic_is_not_q1r3_result": True,
            "probe_pass_does_not_establish_convergence": True,
            "numerical_qualification_demonstrated": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
    }


def markdown(report: Mapping[str, Any]) -> str:
    counterexample = report["q1r2_semantic_counterexample"]
    projection = report["probe"]
    prior = report["prior_q1r2_spatial_smooth_diagnostic"]
    rows = []
    for name, result in prior["observables"].items():
        estimate = result.get("estimate", {})
        pair = estimate.get("pair_fine_medium", {})
        rows.append(
            "| `{}` | {} | {:.6g} | {} | {} |".format(
                name,
                "PASS" if result.get("pass") else "FAIL",
                float(pair.get("relative_l2", math.nan)),
                estimate.get("observed_order"),
                estimate.get("fine_grid_gci_l2"),
            )
        )
    return f"""# HF3D Q1R3 探针前预注册裁决

状态：`{report['status']}`。

本裁决在任何 Q1R3 运行前冻结质量语义、步长来源和探针输入。它不改写 Q1R2 的失败结论，也不把探针设计称为 Q1R3 冻结或数值资格。

## 开边界质量语义

- 数值守恒硬门继续使用 `liquid_volume_balance_error_relative`，即实际液体体积与按规定顶边界通量推算的期望体积之差；上限保持 `1e-3`。
- 旧名 `liquid_mass_error_relative` 的正式定义只是相对初始体积的净存量变化。Q1R2 精细层该值为 `{counterexample['fine_legacy_inventory_change_value']:.12g}`，超过历史冻结阈值 `{counterexample['fine_legacy_inventory_threshold']:.12g}`；该历史失败保持不变。
- 同一归档的通量修正守恒残差只有 `{counterexample['fine_flux_corrected_balance_value']:.12g}`，低于 `{counterexample['fine_flux_corrected_balance_threshold']:.12g}`。Q1R3 不再把库存变化误称为数值守恒误差。
- 库存变化没有经物理或实验标定的绝对充分性阈值，因此不事后抬高阈值；它被重分类为必需、非绝对门控的模型域/边界观测量，并加入空间与时间平滑量收敛。

## 步长与探针输入

- 修正后的 Q1R2 完整轨迹诊断选择 `5.5e-6 s`，设计命中率门为 `0.97`、最终实际验收门仍为 `0.95`；相邻 `5.6e-6 s` 未达到设计门。
- 探针固定为精细网格 `136 × 136 × 120`、`0.02 s`、登记步长 `{projection['registered_dt_s']:.12g} s`、压力相对容差 `1e-7`、CUDA 无回退、CFL 控制目标比例 `0.95` 和最多 4 次事务重试。
- 探针配置逐字节 SHA-256：`{projection['config_file_sha256']}`。
- 只有探针完整结束、正式源码身份一致、实际登记步长命中率至少 `0.95`，并通过 CFL、散度、压力残差、相场修复和通量修正守恒门后，才允许冻结 Q1R3。

## 先验风险诊断

下面只把 Q1R2 已完成空间轨迹投到新的收敛观测合同；由于 Q1R2 的实际步长身份门已经失败，它不是 Q1R3 结果，也不替代新运行。

| 观测量 | 旧轨迹诊断 | 两最细层相对 $L^2$ | 观测阶 | 细网格 GCI |
|---|---:|---:|---:|---:|
{chr(10).join(rows)}

旧轨迹下全部六个平滑量均未通过，其中库存变化也没有正阶收敛。这表明即使探针修复步长身份，Q1R3 空间收敛仍有很高失败风险；但改变登记步长会改变离散轨迹，因此最终裁决必须来自冻结后的新空间组，而不能由本风险诊断替代。

## 结论边界

本报告只授权执行冻结前细网格探针。Q1R3 尚未冻结，空间与时间收敛尚未建立；数值资格、物理验证、实验验证和装置级定量预测继续为 `NO-GO`。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--q1r2-manifest", type=Path, required=True)
    parser.add_argument("--q1r2-runs", type=Path, required=True)
    parser.add_argument("--q1r2-qualification", type=Path, required=True)
    parser.add_argument("--next-dt", type=Path, required=True)
    parser.add_argument("--q1r3-manifest", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    report = analyse(
        q1r2_manifest_path=args.q1r2_manifest.resolve(),
        q1r2_runs=args.q1r2_runs.resolve(),
        q1r2_qualification_path=args.q1r2_qualification.resolve(),
        next_dt_path=args.next_dt.resolve(),
        q1r3_manifest_path=args.q1r3_manifest.resolve(),
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
