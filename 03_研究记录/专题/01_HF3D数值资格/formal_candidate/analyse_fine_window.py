"""Verify the isolated fine-grid CFL candidate replay and write review evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from airvortex.io import config_sha256, load_run_archive


OLD_FAILURE_STATE_TIME_S = 0.0121425
DIVERGENCE_GATE_S_INV = 1.0e-4


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


def _differences(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(left, dict) and isinstance(right, dict):
        result: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else key
            if key not in left:
                result.append({"path": child, "reference": None, "candidate": right[key]})
            elif key not in right:
                result.append({"path": child, "reference": left[key], "candidate": None})
            else:
                result.extend(_differences(left[key], right[key], child))
        return result
    if left != right:
        return [{"path": path, "reference": left, "candidate": right}]
    return []


def _source_identity(provenance: Any) -> dict[str, Any]:
    source = provenance["source_identity"]
    disk = source["disk_content_tree"]
    return {
        "execution_source_identity_valid": bool(
            source["execution_source_identity_valid"]
        ),
        "boundary_match": bool(disk["boundary_match"]),
        "source_tree_sha256_start": disk["execution_start"]["value"],
        "source_tree_sha256_end": disk["execution_end"]["value"],
        "loaded_module_bytes_verified": bool(
            source["loaded_module_bytes_verified"]
        ),
    }


def analyse(
    archive_path: Path,
    reference_config_path: Path,
    patch_path: Path,
    expected_source_sha256: str,
) -> dict[str, Any]:
    archived = load_run_archive(archive_path, verify=True)
    result = archived.result
    config = archived.config
    diagnostics = result.metadata["diagnostics"]
    fields = result.fields
    completed_steps = int(diagnostics["completed_steps"])
    required_fields = (
        "diagnostic_step_time_s",
        "diagnostic_dt_s",
        "diagnostic_initial_attempt_dt_s",
        "diagnostic_time_step_limiter_code",
        "diagnostic_cfl_retry_count",
        "diagnostic_starting_advective_cfl",
        "diagnostic_pre_source_advective_cfl",
        "diagnostic_pre_projection_advective_cfl",
        "diagnostic_post_projection_advective_cfl",
        "diagnostic_maximum_rejected_post_projection_cfl",
        "diagnostic_maximum_momentum_advection_acceleration_m_s2",
        "diagnostic_maximum_viscous_acceleration_m_s2",
        "diagnostic_maximum_boundary_acceleration_m_s2",
        "diagnostic_maximum_surface_tension_acceleration_m_s2",
        "diagnostic_maximum_rotating_frame_acceleration_m_s2",
        "diagnostic_gravity_acceleration_m_s2",
        "diagnostic_maximum_pressure_projection_acceleration_m_s2",
        "diagnostic_projected_divergence_s_inv",
        "diagnostic_pressure_relative_residual",
    )
    missing_fields = sorted(set(required_fields) - set(fields))
    wrong_length_fields = sorted(
        name
        for name in required_fields
        if name in fields and fields[name].size != completed_steps
    )
    if missing_fields or wrong_length_fields or completed_steps < 1:
        raise ValueError(
            f"incomplete dense diagnostics: missing={missing_fields}, "
            f"wrong_length={wrong_length_fields}, steps={completed_steps}"
        )

    step_time = fields["diagnostic_step_time_s"]
    post_cfl = fields["diagnostic_post_projection_advective_cfl"]
    divergence = fields["diagnostic_projected_divergence_s_inv"]
    pressure_residual = fields["diagnostic_pressure_relative_residual"]
    peak_cfl_index = int(np.argmax(post_cfl))
    old_window = np.flatnonzero(step_time >= OLD_FAILURE_STATE_TIME_S)
    source = _source_identity(archived.provenance)
    accelerator = diagnostics["pressure_accelerator"]
    runtime = archived.provenance["extra"]["runtime"]
    limiter_codes = diagnostics["time_step_limiter_codes"]
    limiter_values = fields["diagnostic_time_step_limiter_code"]
    limiter_statistics: dict[str, dict[str, float | int]] = {}
    for name, code in limiter_codes.items():
        mask = limiter_values == code
        if np.any(mask):
            selected_dt = fields["diagnostic_dt_s"][mask]
            limiter_statistics[name] = {
                "accepted_step_count": int(np.sum(mask)),
                "minimum_dt_s": float(np.min(selected_dt)),
                "maximum_dt_s": float(np.max(selected_dt)),
                "median_dt_s": float(np.median(selected_dt)),
            }

    reference_raw = _read_json(reference_config_path)
    candidate_raw = _read_json(archive_path / "config.json")
    input_differences = _differences(reference_raw, candidate_raw)
    allowed_differences = {
        "high_fidelity.cfl_controller_target_fraction",
        "high_fidelity.maximum_cfl_step_retries",
        "high_fidelity.pressure_relative_tolerance",
        "high_fidelity.pressure_solver_device",
        "run.duration_s",
    }
    observed_difference_paths = {item["path"] for item in input_differences}
    input_scope_pass = observed_difference_paths == allowed_differences

    gates = {
        "archive_integrity": True,
        "completed_targeted_duration": bool(
            result.status.value == "completed"
            and math.isclose(
                float(diagnostics["simulated_duration_s"]),
                config.run.duration_s,
                rel_tol=0.0,
                abs_tol=5.0e-15,
            )
        ),
        "crossed_old_failure_state_time": bool(old_window.size > 0),
        "input_change_scope": input_scope_pass,
        "candidate_disk_source_boundary_identity": bool(
            source["execution_source_identity_valid"]
            and source["boundary_match"]
            and source["source_tree_sha256_start"] == expected_source_sha256
            and source["source_tree_sha256_end"] == expected_source_sha256
        ),
        "cuda_selected_without_fallback": bool(
            accelerator["selected"] == "cuda" and not accelerator["fallback"]
        ),
        "accepted_post_projection_cfl": bool(
            float(np.max(post_cfl))
            <= config.high_fidelity.cfl_limit * (1.0 + 1.0e-9)
        ),
        "absolute_projected_divergence": bool(
            float(np.max(divergence)) <= DIVERGENCE_GATE_S_INV
        ),
        "pressure_relative_residual": bool(
            float(np.max(pressure_residual))
            <= config.high_fidelity.pressure_relative_tolerance * 1.05
        ),
        "formal_numerical_quality_gate": bool(
            diagnostics["numerical_quality_gate"]["pass"]
        ),
    }
    gate_pass = all(gates.values())
    peak_operator_acceleration = {
        name.removeprefix("diagnostic_").removeprefix("maximum_"): float(
            fields[name][peak_cfl_index]
        )
        for name in required_fields
        if "acceleration_m_s2" in name
    }
    retry_events = [
        event.to_dict() for event in result.events if event.kind == "cfl_step_retry"
    ]
    old_window_index = int(old_window[0]) if old_window.size else None
    registered_dt_hit_fraction = float(
        np.mean(
            np.isclose(
                fields["diagnostic_dt_s"],
                config.high_fidelity.time_step_s,
                rtol=0.0,
                atol=64.0
                * np.finfo(float).eps
                * config.high_fidelity.time_step_s,
            )
        )
    )
    return {
        "schema_version": 1,
        "status": (
            "FINE_WINDOW_CANDIDATE_PASS__PROMOTION_DECISION_REQUIRED"
            if gate_pass
            else "FINE_WINDOW_CANDIDATE_FAIL"
        ),
        "gate_pass": gate_pass,
        "gates": gates,
        "archive": {
            "path": str(archive_path),
            "config_sha256": config_sha256(config),
            "manifest_sha256": _sha256(archive_path / "manifest.json"),
            "manifest_size_bytes": (archive_path / "manifest.json").stat().st_size,
            "payload_files": archived.manifest["files"],
            "completed_steps": completed_steps,
            "simulated_duration_s": float(diagnostics["simulated_duration_s"]),
            "runtime_wall_s": float(runtime["duration_s"]),
            "source_identity": source,
            "pressure_accelerator": accelerator,
        },
        "candidate_patch": {
            "path": str(patch_path),
            "sha256": _sha256(patch_path),
            "expected_source_tree_sha256": expected_source_sha256,
        },
        "input_differences_from_original_fine_case": input_differences,
        "allowed_input_difference_paths": sorted(allowed_differences),
        "cfl": {
            "hard_limit": config.high_fidelity.cfl_limit,
            "controller_target_fraction": (
                config.high_fidelity.cfl_controller_target_fraction
            ),
            "maximum_accepted_post_projection_cfl": float(np.max(post_cfl)),
            "maximum_starting_advective_cfl": float(
                np.max(fields["diagnostic_starting_advective_cfl"])
            ),
            "maximum_pre_source_advective_cfl": float(
                np.max(fields["diagnostic_pre_source_advective_cfl"])
            ),
            "maximum_pre_projection_advective_cfl": float(
                np.max(fields["diagnostic_pre_projection_advective_cfl"])
            ),
            "maximum_accepted_step_one_based": peak_cfl_index + 1,
            "maximum_accepted_step_time_s": float(step_time[peak_cfl_index]),
            "maximum_rejected_post_projection_cfl": float(
                diagnostics["maximum_rejected_post_projection_cfl"]
            ),
            "retry_attempts_total": int(
                diagnostics["cfl_retry_attempts_total"]
            ),
            "retried_accepted_steps": int(
                diagnostics["cfl_retried_accepted_steps"]
            ),
            "minimum_accepted_dt_s": float(
                np.min(fields["diagnostic_dt_s"])
            ),
            "minimum_combined_known_source_limited_dt_s": (
                limiter_statistics.get("combined_known_source_cfl", {}).get(
                    "minimum_dt_s"
                )
            ),
            "registered_dt_hit_fraction": registered_dt_hit_fraction,
            "time_step_limiter_counts": diagnostics[
                "time_step_limiter_counts"
            ],
            "time_step_limiter_statistics": limiter_statistics,
            "retry_events": retry_events,
            "peak_step_operator_acceleration_m_s2": peak_operator_acceleration,
        },
        "old_failure_window": {
            "old_last_completed_state_time_s": OLD_FAILURE_STATE_TIME_S,
            "first_candidate_step_at_or_after_time_s": (
                None if old_window_index is None else float(step_time[old_window_index])
            ),
            "candidate_post_projection_cfl": (
                None if old_window_index is None else float(post_cfl[old_window_index])
            ),
            "candidate_dt_s": (
                None
                if old_window_index is None
                else float(fields["diagnostic_dt_s"][old_window_index])
            ),
            "candidate_starting_advective_cfl": (
                None
                if old_window_index is None
                else float(
                    fields["diagnostic_starting_advective_cfl"][old_window_index]
                )
            ),
            "candidate_pre_source_advective_cfl": (
                None
                if old_window_index is None
                else float(
                    fields["diagnostic_pre_source_advective_cfl"][old_window_index]
                )
            ),
            "candidate_pre_projection_advective_cfl": (
                None
                if old_window_index is None
                else float(
                    fields["diagnostic_pre_projection_advective_cfl"][old_window_index]
                )
            ),
            "candidate_time_step_limiter": (
                None
                if old_window_index is None
                else next(
                    name
                    for name, code in limiter_codes.items()
                    if code == int(limiter_values[old_window_index])
                )
            ),
        },
        "q1r2_design_implication": {
            "current_registered_dt_s": config.high_fidelity.time_step_s,
            "registered_dt_hit_fraction": registered_dt_hit_fraction,
            "required_hit_fraction": 0.95,
            "current_registered_dt_reusable_without_refreeze": bool(
                registered_dt_hit_fraction >= 0.95
            ),
            "common_dt_frozen": False,
            "reason": (
                "The candidate controller actively limited the fine grid; a new common spatial dt must be selected from promoted-source trajectories."
            ),
        },
        "pressure": {
            "relative_tolerance": config.high_fidelity.pressure_relative_tolerance,
            "maximum_relative_residual": float(np.max(pressure_residual)),
            "maximum_projected_divergence_s_inv": float(np.max(divergence)),
            "absolute_divergence_gate_s_inv": DIVERGENCE_GATE_S_INV,
        },
        "claim_ceiling": {
            "formal_program_modified": False,
            "loaded_module_bytes_verified": source[
                "loaded_module_bytes_verified"
            ],
            "q1r2_frozen_or_passed": False,
            "numerical_convergence_demonstrated": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
    }


def markdown(report: dict[str, Any]) -> str:
    gates = report["gates"]
    cfl = report["cfl"]
    pressure = report["pressure"]
    archive = report["archive"]
    source = archive["source_identity"]
    rows = "\n".join(
        f"| `{name}` | {'PASS' if passed else 'FAIL'} |"
        for name, passed in gates.items()
    )
    return f"""# HF3D 细网格候选 CFL 窗口重放

状态：`{report['status']}`。

本报告只验证隔离候选补丁能否越过原 Q1R 细网格 CFL 失败窗口。正式程序没有修改；这不是 Q1R2、空间/时间收敛、物理验证或实验验证。

## 门禁

| 门禁 | 结果 |
|---|---:|
{rows}

## 核心结果

- 模拟时长：`{archive['simulated_duration_s']:.9g} s`；完成步数：`{archive['completed_steps']}`。
- 墙钟：`{archive['runtime_wall_s']:.6g} s`；最小接受步长：`{cfl['minimum_accepted_dt_s']:.12g} s`；登记步长命中比例：`{cfl['registered_dt_hit_fraction']:.6g}`。
- 联合已知源项限步的最小步长：`{cfl['minimum_combined_known_source_limited_dt_s']:.12g} s`。登记步长命中比例低于资格工具要求的 0.95，因此原 `7.5e-6 s` 不得直接沿用为 Q1R2 空间共同步长。
- 投影后最大接受 CFL：`{cfl['maximum_accepted_post_projection_cfl']:.12g}`；硬门：`{cfl['hard_limit']:.12g}`。
- 被拒绝试步最大 CFL：`{cfl['maximum_rejected_post_projection_cfl']:.12g}`；总重试次数：`{cfl['retry_attempts_total']}`。
- 最大投影散度：`{pressure['maximum_projected_divergence_s_inv']:.12g} s^-1`；候选绝对门：`{pressure['absolute_divergence_gate_s_inv']:.12g} s^-1`。
- 最大压力相对残差：`{pressure['maximum_relative_residual']:.12g}`；登记容差：`{pressure['relative_tolerance']:.12g}`。
- 实际压力加速器：`{archive['pressure_accelerator']['selected']}`；回退：`{archive['pressure_accelerator']['fallback']}`。
- 执行源码 SHA-256：`{source['source_tree_sha256_start']}`；前后边界一致：`{source['boundary_match']}`。
- 已加载模块字节核验：`{source['loaded_module_bytes_verified']}`。正式归档模型固定记录为 `false`；本报告只声称磁盘白名单源码树执行前后边界一致，不把它扩大为已导入模块字节证明。

## 结论边界

若全部门禁通过，只能说明该候选补丁及目标压力容差在一条 0.013 s 细网格窗口轨迹上通过实现级和单例数值门，足以提交“是否提升到正式程序”的人工裁决。它不冻结共同时间步，不替代完整 Q1R2，也不允许装置级预测。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("reference_config", type=Path)
    parser.add_argument("patch", type=Path)
    parser.add_argument("expected_source_sha256")
    parser.add_argument("json_output", type=Path)
    parser.add_argument("markdown_output", type=Path)
    args = parser.parse_args()
    report = analyse(
        args.archive.resolve(),
        args.reference_config.resolve(),
        args.patch.resolve(),
        args.expected_source_sha256,
    )
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "gate_pass": report["gate_pass"]}))
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
