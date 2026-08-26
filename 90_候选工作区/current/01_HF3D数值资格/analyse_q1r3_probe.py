"""Fail-closed analysis of the preregistered Q1R3 fine-grid CUDA probe."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from hfq_cli_common import LoadedCase, _explicit_config_differences, _quality_gate
from hfq_json import canonical_sha256, physical_signature


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
    config_path: Path,
    archive_path: Path,
    preregistration_path: Path,
    design_manifest_path: Path,
    expected_source_sha256: str,
) -> dict[str, Any]:
    from airvortex.io import load_run_archive

    config = _read_json(config_path)
    preregistration = _read_json(preregistration_path)
    design_manifest = _read_json(design_manifest_path)
    loaded = load_run_archive(archive_path, verify=True)
    archived_config = loaded.config.to_dict()

    diagnostics = _mapping(
        loaded.result.metadata.get("diagnostics"), "probe diagnostics"
    )
    source = _mapping(
        loaded.provenance.get("source_identity"), "probe source identity"
    )
    disk = _mapping(source.get("disk_content_tree"), "source disk content tree")
    source_start = _mapping(
        disk.get("execution_start"), "source execution start"
    ).get("value")
    source_end = _mapping(
        disk.get("execution_end"), "source execution end"
    ).get("value")
    accelerator = _mapping(
        diagnostics.get("pressure_accelerator"), "pressure accelerator"
    )
    quality_gates = _mapping(
        _mapping(design_manifest.get("gates"), "design gates").get(
            "numerical_quality"
        ),
        "design numerical quality gates",
    )

    probe_case = {
        "id": "q1r3_fine_probe",
        "group": "probe",
        "level": 0,
    }
    adapted = LoadedCase(
        case=probe_case,
        config=config,
        archived_config=archived_config,
        time_s=np.asarray(loaded.result.time_s, dtype=np.float64),
        observables={
            name: np.asarray(values, dtype=np.float64)
            for name, values in loaded.result.observables.items()
        },
        fields={
            name: np.asarray(values, dtype=np.float64)
            for name, values in loaded.result.fields.items()
        },
        metadata=dict(loaded.result.metadata),
        manifest=dict(loaded.manifest),
        provenance=dict(loaded.provenance),
        result_status=loaded.result.status.value,
    )
    quality = _quality_gate(adapted, quality_gates)
    quality_contract = _mapping(
        quality.get("quality_contract"), "probe quality contract"
    )
    design_quality_contract = _mapping(
        design_manifest.get("quality_contract_audit"),
        "design quality contract audit",
    )

    raw_steps = np.asarray(
        loaded.result.fields.get("diagnostic_dt_s", []), dtype=np.float64
    ).reshape(-1)
    configured_dt = float(
        _mapping(config.get("high_fidelity"), "high_fidelity")["time_step_s"]
    )
    tolerance = max(1.0e-15, configured_dt * 1.0e-9)
    valid_steps = bool(
        raw_steps.size > 0
        and raw_steps.size == int(diagnostics.get("completed_steps", -1))
        and np.all(np.isfinite(raw_steps))
        and np.all(raw_steps > 0.0)
    )
    active_fraction = (
        float(np.mean(np.abs(raw_steps - configured_dt) <= tolerance))
        if valid_steps
        else None
    )
    preregistered_probe = _mapping(
        preregistration.get("probe"), "preregistered probe"
    )
    minimum_fraction = float(
        preregistered_probe.get("minimum_actual_registered_dt_fraction")
    )
    requested_duration = float(_mapping(config.get("run"), "run")["duration_s"])
    runtime = _mapping(
        _mapping(loaded.provenance.get("extra"), "provenance extra").get(
            "runtime"
        ),
        "runtime",
    )
    limiter_counts = _mapping(
        diagnostics.get("time_step_limiter_counts"), "time-step limiter counts"
    )

    gates = {
        "preregistration_authorizes_only_this_probe": bool(
            preregistration.get("status")
            == "Q1R3_PREREGISTRATION_PASS__FINE_PROBE_REQUIRED"
            and preregistration.get("gate_pass") is True
            and preregistered_probe.get("must_execute_before_q1r3_freeze") is True
            and preregistered_probe.get("config_file_sha256")
            == _sha256(config_path)
        ),
        "archive_integrity": True,
        "generated_config_identity": bool(
            not _explicit_config_differences(config, archived_config)
            and canonical_sha256(config)
            == preregistered_probe.get("config_canonical_sha256")
        ),
        "physical_signature_identity": bool(
            canonical_sha256(physical_signature(config))
            == design_manifest.get("physical_signature_sha256")
        ),
        "formal_source_boundary_identity": bool(
            source.get("execution_source_identity_valid") is True
            and source_start == expected_source_sha256
            and source_end == expected_source_sha256
        ),
        "completed_target_duration": bool(
            loaded.result.status.value == "completed"
            and diagnostics.get("termination_reason")
            == "configured_duration_reached"
            and _close(diagnostics.get("simulated_duration_s"), requested_duration)
        ),
        "cuda_selected_without_fallback": bool(
            accelerator.get("selected") == "cuda"
            and accelerator.get("fallback") is False
        ),
        "dense_step_trajectory_complete": valid_steps,
        "actual_registered_dt_fraction": bool(
            active_fraction is not None and active_fraction >= minimum_fraction
        ),
        "open_boundary_numerical_quality_contract": bool(
            quality.get("pass") is True
            and quality_contract.get("pass") is True
            and design_quality_contract.get("pass") is True
            and quality_contract.get("contract_id")
            == design_quality_contract.get("contract_id")
            and _mapping(
                quality.get("inventory_observation"), "inventory observation"
            ).get("absolute_gate_enabled")
            is False
        ),
        "apparatus_prediction_remains_disallowed": bool(
            _mapping(
                preregistration.get("decision_boundary"), "decision boundary"
            ).get("apparatus_prediction_allowed")
            is False
        ),
    }
    gate_pass = all(gates.values())
    checks = _mapping(quality.get("checks"), "quality checks")
    inventory = _mapping(
        quality.get("inventory_observation"), "inventory observation"
    )
    return {
        "schema_version": 1,
        "status": (
            "Q1R3_FINE_PROBE_PASS__FREEZE_DECISION_READY"
            if gate_pass
            else "Q1R3_FINE_PROBE_FAIL__Q1R3_NOT_FROZEN"
        ),
        "gate_pass": gate_pass,
        "gates": gates,
        "inputs": {
            "config_path": str(config_path),
            "config_file_sha256": _sha256(config_path),
            "preregistration_sha256": _sha256(preregistration_path),
            "design_manifest_sha256": _sha256(design_manifest_path),
            "archive_path": str(archive_path),
            "archive_manifest_sha256": _sha256(archive_path / "manifest.json"),
        },
        "execution": {
            "run_id": loaded.manifest.get("run_id"),
            "completed_steps": diagnostics.get("completed_steps"),
            "simulated_duration_s": diagnostics.get("simulated_duration_s"),
            "runtime_wall_s": runtime.get("duration_s"),
            "source_tree_sha256_start": source_start,
            "source_tree_sha256_end": source_end,
            "pressure_accelerator": dict(accelerator),
            "configured_dt_s": configured_dt,
            "minimum_actual_dt_s": (
                float(np.min(raw_steps)) if valid_steps else None
            ),
            "maximum_actual_dt_s": (
                float(np.max(raw_steps)) if valid_steps else None
            ),
            "configured_dt_active_fraction": active_fraction,
            "minimum_configured_dt_active_fraction": minimum_fraction,
            "time_step_limiter_counts": dict(limiter_counts),
            "cfl_retry_attempts_total": diagnostics.get(
                "cfl_retry_attempts_total"
            ),
        },
        "numerical_quality": {
            "pass": quality.get("pass"),
            "checks": dict(checks),
            "liquid_inventory_change_relative": dict(inventory),
            "maximum_advective_cfl": diagnostics.get("maximum_advective_cfl"),
            "maximum_projected_divergence_s_inv": diagnostics.get(
                "maximum_projected_divergence_s_inv"
            ),
            "maximum_pressure_relative_residual": diagnostics.get(
                "maximum_pressure_relative_residual"
            ),
        },
        "decision_boundary": {
            "q1r3_freeze_allowed": gate_pass,
            "q1r3_frozen": False,
            "probe_establishes_spatial_convergence": False,
            "probe_establishes_temporal_convergence": False,
            "numerical_qualification_demonstrated": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
    }


def markdown(report: Mapping[str, Any]) -> str:
    execution = report["execution"]
    quality = report["numerical_quality"]
    checks = quality["checks"]
    inventory = quality["liquid_inventory_change_relative"]
    rows = []
    for name, item in checks.items():
        rows.append(
            "| `{}` | {:.12g} | {:.12g} | {} |".format(
                name,
                float(item["value"]),
                float(item["maximum"]),
                "PASS" if item["pass"] else "FAIL",
            )
        )
    return f"""# HF3D Q1R3 冻结前细网格探针报告

状态：`{report['status']}`。

本报告只判定预注册的 `136 × 136 × 120`、`0.02 s`、`5.5e-6 s` 正式 CUDA 探针。即使全部门通过，也只允许进入 Q1R3 冻结决策，不证明空间或时间收敛。

## 执行身份

- 完成步数：`{execution['completed_steps']}`；模拟时长：`{execution['simulated_duration_s']:.12g} s`；墙钟：`{execution['runtime_wall_s']:.6f} s`。
- 正式源码树前后均为 `{execution['source_tree_sha256_start']}`。
- 压力加速器实际选择 `{execution['pressure_accelerator'].get('selected')}`，回退={execution['pressure_accelerator'].get('fallback')}。
- 登记步长命中率：`{execution['configured_dt_active_fraction']:.12g}`；预注册下限：`{execution['minimum_configured_dt_active_fraction']:.12g}`。
- 实际最小/最大步长：`{execution['minimum_actual_dt_s']:.12g} s` / `{execution['maximum_actual_dt_s']:.12g} s`；事务重试总数：`{execution['cfl_retry_attempts_total']}`。

## 数值质量门

| 门 | 实测值 | 上限 | 结果 |
|---|---:|---:|---:|
{chr(10).join(rows)}

液体净存量变化观测为 `{inventory['value']:.12g}`。它没有未经标定的绝对通过阈值，不进入上表硬门；但必须作为 Q1R3 的平滑量参与空间和时间收敛。

## 结论边界

`q1r3_freeze_allowed={report['decision_boundary']['q1r3_freeze_allowed']}`。探针没有建立多网格或多时间步收敛，数值资格、物理验证、实验验证及装置级预测仍为 `NO-GO`。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--design-manifest", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    report = analyse(
        config_path=args.config.resolve(),
        archive_path=args.archive.resolve(),
        preregistration_path=args.preregistration.resolve(),
        design_manifest_path=args.design_manifest.resolve(),
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
