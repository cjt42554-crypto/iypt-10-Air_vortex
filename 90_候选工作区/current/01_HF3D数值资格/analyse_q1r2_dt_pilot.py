"""Analyse promoted-source spatial pilot trajectories and design Q1R2 steps."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


EXPECTED_CASE_IDS = (
    "space_L0_4cells",
    "space_L1_6cells",
    "space_L2_9cells",
)


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


def _active_fraction(steps: np.ndarray, candidate_s: float) -> float:
    tolerance = max(1.0e-15, 1.0e-9 * candidate_s)
    projected = np.minimum(steps, candidate_s)
    return float(
        np.mean(
            np.isclose(
                projected,
                candidate_s,
                rtol=0.0,
                atol=tolerance,
            )
        )
    )


def select_common_dt(
    step_series: Mapping[str, np.ndarray],
    *,
    pilot_maximum_s: float,
    acceptance_fraction: float,
    design_fraction: float,
    quantum_s: float,
) -> dict[str, Any]:
    """Select the largest quantised step meeting a buffered trajectory gate."""

    for name, value in (
        ("pilot_maximum_s", pilot_maximum_s),
        ("acceptance_fraction", acceptance_fraction),
        ("design_fraction", design_fraction),
        ("quantum_s", quantum_s),
    ):
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and positive")
    if acceptance_fraction > 1.0 or design_fraction > 1.0:
        raise ValueError("active fractions must not exceed one")
    if design_fraction <= acceptance_fraction:
        raise ValueError("design_fraction must exceed acceptance_fraction")
    if not step_series:
        raise ValueError("at least one pilot trajectory is required")

    cleaned: dict[str, np.ndarray] = {}
    for case_id, raw in step_series.items():
        values = np.asarray(raw, dtype=np.float64).reshape(-1)
        if (
            values.size < 1
            or not np.all(np.isfinite(values))
            or not np.all(values > 0.0)
            or np.max(values) > pilot_maximum_s * (1.0 + 1.0e-9)
        ):
            raise ValueError(f"invalid pilot step series: {case_id}")
        cleaned[case_id] = values

    maximum_ticks = math.floor(
        pilot_maximum_s / quantum_s + 64.0 * np.finfo(float).eps
    )
    evaluated: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    for ticks in range(maximum_ticks, 0, -1):
        candidate_s = ticks * quantum_s
        fractions = {
            case_id: _active_fraction(values, candidate_s)
            for case_id, values in cleaned.items()
        }
        record = {
            "candidate_s": candidate_s,
            "predicted_active_fraction_by_case": fractions,
            "minimum_predicted_active_fraction": min(fractions.values()),
            "meets_design_fraction": min(fractions.values()) >= design_fraction,
        }
        evaluated.append(record)
        if record["meets_design_fraction"]:
            selected = record
            break
    if selected is None:
        raise ValueError("no positive quantised common step meets design_fraction")

    rejected_larger = None
    selected_ticks = round(float(selected["candidate_s"]) / quantum_s)
    if selected_ticks < maximum_ticks:
        larger_s = (selected_ticks + 1) * quantum_s
        fractions = {
            case_id: _active_fraction(values, larger_s)
            for case_id, values in cleaned.items()
        }
        rejected_larger = {
            "candidate_s": larger_s,
            "predicted_active_fraction_by_case": fractions,
            "minimum_predicted_active_fraction": min(fractions.values()),
            "meets_design_fraction": min(fractions.values()) >= design_fraction,
        }

    return {
        "method": "largest_quantised_step_meeting_buffered_active_fraction",
        "pilot_maximum_s": pilot_maximum_s,
        "acceptance_fraction": acceptance_fraction,
        "design_fraction": design_fraction,
        "design_margin_fraction": design_fraction - acceptance_fraction,
        "quantum_s": quantum_s,
        "selected": selected,
        "immediately_larger_rejected_candidate": rejected_larger,
        "evaluated_candidate_count_through_selection": len(evaluated),
    }


def _parse_archives(values: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for raw in values:
        case_id, separator, path = raw.partition("=")
        if not separator or not case_id or not path:
            raise ValueError("--archive must use CASE_ID=PATH")
        if case_id in result:
            raise ValueError(f"duplicate archive case id: {case_id}")
        result[case_id] = Path(path).resolve()
    if set(result) != set(EXPECTED_CASE_IDS):
        raise ValueError(
            f"archive ids must be exactly {list(EXPECTED_CASE_IDS)}"
        )
    return result


def analyse(
    manifest_path: Path,
    archive_paths: Mapping[str, Path],
    expected_source_sha256: str,
    *,
    acceptance_fraction: float = 0.95,
    design_fraction: float = 0.97,
    quantum_s: float = 1.0e-7,
) -> dict[str, Any]:
    # Keep the trajectory-selection primitive importable in a plain Python
    # environment; archive analysis itself still requires the formal package.
    from airvortex.io import load_run_archive

    manifest = _read_json(manifest_path)
    case_entries = {
        str(item["id"]): item
        for item in manifest.get("cases", [])
        if isinstance(item, dict) and item.get("group") == "spatial"
    }
    if set(case_entries) != set(EXPECTED_CASE_IDS):
        raise ValueError("manifest must contain exactly three spatial pilot cases")

    cases: dict[str, dict[str, Any]] = {}
    step_series: dict[str, np.ndarray] = {}
    pilot_steps: list[float] = []
    global_gates = {
        "manifest_static_refinement_design": bool(
            manifest.get("refinement_design_audit", {}).get("pass")
        ),
        "three_spatial_cases_present": True,
    }

    for case_id in EXPECTED_CASE_IDS:
        entry = case_entries[case_id]
        archive_path = archive_paths[case_id]
        loaded = load_run_archive(archive_path, verify=True)
        diagnostics = loaded.result.metadata["diagnostics"]
        source = loaded.provenance["source_identity"]
        disk = source["disk_content_tree"]
        runtime = loaded.provenance["extra"]["runtime"]
        generated_config_path = manifest_path.parent / str(entry["config_file"])
        input_match = _read_json(generated_config_path) == _read_json(
            archive_path / "config.json"
        )
        steps = np.asarray(
            loaded.result.fields.get("diagnostic_dt_s", []),
            dtype=np.float64,
        ).reshape(-1)
        completed_steps = int(diagnostics.get("completed_steps", -1))
        pilot_step = float(loaded.config.high_fidelity.time_step_s)
        pilot_steps.append(pilot_step)
        valid_steps = bool(
            steps.size == completed_steps
            and steps.size > 0
            and np.all(np.isfinite(steps))
            and np.all(steps > 0.0)
        )
        if valid_steps:
            step_series[case_id] = steps
        accelerator = diagnostics["pressure_accelerator"]
        case_gates = {
            "archive_integrity": True,
            "generated_input_identity": input_match,
            "completed_targeted_duration": bool(
                loaded.result.status.value == "completed"
                and math.isclose(
                    float(diagnostics["simulated_duration_s"]),
                    float(loaded.config.run.duration_s),
                    rel_tol=0.0,
                    abs_tol=5.0e-15,
                )
            ),
            "dense_step_trajectory_complete": valid_steps,
            "promoted_source_boundary_identity": bool(
                source["execution_source_identity_valid"]
                and disk["boundary_match"]
                and disk["execution_start"]["value"]
                == expected_source_sha256
                and disk["execution_end"]["value"]
                == expected_source_sha256
            ),
            "cuda_selected_without_fallback": bool(
                accelerator["selected"] == "cuda"
                and not accelerator["fallback"]
            ),
            "formal_numerical_quality_gate": bool(
                diagnostics["numerical_quality_gate"]["pass"]
            ),
            "registered_pressure_tolerance": math.isclose(
                float(loaded.config.high_fidelity.pressure_relative_tolerance),
                1.0e-7,
                rel_tol=0.0,
                abs_tol=0.0,
            ),
            "transactional_controller_identity": bool(
                math.isclose(
                    float(
                        loaded.config.high_fidelity.cfl_controller_target_fraction
                    ),
                    0.95,
                    rel_tol=0.0,
                    abs_tol=0.0,
                )
                and loaded.config.high_fidelity.maximum_cfl_step_retries == 4
            ),
        }
        limiter_counts = diagnostics["time_step_limiter_counts"]
        cases[case_id] = {
            "archive_path": str(archive_path),
            "manifest_config_sha256": entry["config_sha256"],
            "archive_manifest_sha256": _sha256(archive_path / "manifest.json"),
            "gates": case_gates,
            "gate_pass": all(case_gates.values()),
            "grid": {
                "radial_cells": loaded.config.high_fidelity.radial_cells,
                "azimuthal_cells": loaded.config.high_fidelity.azimuthal_cells,
                "axial_cells": loaded.config.high_fidelity.axial_cells,
            },
            "completed_steps": completed_steps,
            "simulated_duration_s": float(diagnostics["simulated_duration_s"]),
            "runtime_wall_s": float(runtime["duration_s"]),
            "pilot_registered_dt_s": pilot_step,
            "pilot_registered_dt_hit_fraction": (
                _active_fraction(steps, pilot_step) if valid_steps else None
            ),
            "minimum_actual_dt_s": (
                float(np.min(steps)) if valid_steps else None
            ),
            "limiter_counts": limiter_counts,
            "source_tree_sha256_start": disk["execution_start"]["value"],
            "source_tree_sha256_end": disk["execution_end"]["value"],
            "pressure_accelerator": accelerator,
        }

    common_pilot_step = bool(
        pilot_steps
        and all(
            math.isclose(value, pilot_steps[0], rel_tol=0.0, abs_tol=0.0)
            for value in pilot_steps[1:]
        )
    )
    global_gates["common_pilot_registered_step"] = common_pilot_step
    global_gates["all_case_gates"] = all(
        case["gate_pass"] for case in cases.values()
    )
    gate_pass = all(global_gates.values())
    selection = None
    if gate_pass:
        selection = select_common_dt(
            step_series,
            pilot_maximum_s=pilot_steps[0],
            acceptance_fraction=acceptance_fraction,
            design_fraction=design_fraction,
            quantum_s=quantum_s,
        )
        selected_s = float(selection["selected"]["candidate_s"])
        for case_id, fraction in selection["selected"][
            "predicted_active_fraction_by_case"
        ].items():
            cases[case_id][
                "selected_dt_predicted_active_fraction"
            ] = fraction
        temporal_steps = [selected_s, selected_s / 2.0, selected_s / 4.0]
    else:
        selected_s = None
        temporal_steps = None

    return {
        "schema_version": 1,
        "status": (
            "DT_DESIGN_PASS__Q1R2_FREEZE_INPUT_READY"
            if gate_pass and selection is not None
            else "DT_DESIGN_FAIL__Q1R2_NOT_FROZEN"
        ),
        "gate_pass": bool(gate_pass and selection is not None),
        "global_gates": global_gates,
        "manifest": {
            "path": str(manifest_path),
            "sha256": _sha256(manifest_path),
            "physical_signature_sha256": manifest[
                "physical_signature_sha256"
            ],
        },
        "expected_promoted_source_sha256": expected_source_sha256,
        "cases": cases,
        "selection": selection,
        "q1r2_freeze_inputs": {
            "spatial_common_registered_dt_s": selected_s,
            "temporal_registered_dt_s": temporal_steps,
            "pressure_relative_tolerance": 1.0e-7,
            "pressure_solver_device": "cuda",
            "cfl_controller_target_fraction": 0.95,
            "maximum_cfl_step_retries": 4,
        },
        "validation_boundary": {
            "selection_is_trajectory_projection_not_a_q1r2_run": True,
            "q1r2_actual_active_fraction_must_be_rechecked": acceptance_fraction,
            "q1r2_convergence_demonstrated": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
    }


def markdown(report: Mapping[str, Any]) -> str:
    cases = report["cases"]
    selection = report["selection"]
    freeze = report["q1r2_freeze_inputs"]
    rows = []
    for case_id in EXPECTED_CASE_IDS:
        case = cases[case_id]
        rows.append(
            "| `{}` | `{}×{}×{}` | {:.6f} | {:.6f} | {} |".format(
                case_id,
                case["grid"]["radial_cells"],
                case["grid"]["azimuthal_cells"],
                case["grid"]["axial_cells"],
                case["pilot_registered_dt_hit_fraction"],
                case.get("selected_dt_predicted_active_fraction", float("nan")),
                "PASS" if case["gate_pass"] else "FAIL",
            )
        )
    rejected = (
        selection["immediately_larger_rejected_candidate"]
        if selection is not None
        else None
    )
    selected = selection["selected"] if selection is not None else None
    temporal = freeze["temporal_registered_dt_s"]
    return f"""# HF3D Q1R2 共同登记步长正式轨迹设计

状态：`{report['status']}`。

本报告只用提升后正式源码的三层空间网格真实稠密步长轨迹设计 Q1R2 的登记步长。它不是 Q1R2 运行结果，不证明空间或时间收敛，也不是物理或实验验证。

## 轨迹门禁

| 算例 | 网格 | 原 `7.5e-6 s` 命中率 | 所选步长预测命中率 | 身份与质量门 |
|---|---:|---:|---:|---:|
{chr(10).join(rows)}

三层均完成 `0.013 s`，正式源码树前后均为 `{report['expected_promoted_source_sha256']}`，实际选择 CUDA 且无回退，正式数值质量门通过。

## 选择规则与结果

- 最终验收门仍为实际登记步长命中率至少 `{selection['acceptance_fraction'] if selection else 0.95:.3f}`。
- 设计阶段在 `0.1 µs` 量化网格上，选择三层预测命中率均至少 `{selection['design_fraction'] if selection else 0.97:.3f}` 的最大共同步长；这比最终门多留 `0.02` 的比例余量。
- 选中空间共同登记步长：`{freeze['spatial_common_registered_dt_s']:.12g} s`。
- 时间层登记步长：`{temporal[0]:.12g} s`、`{temporal[1]:.12g} s`、`{temporal[2]:.12g} s`，严格递减。
- 选中值的三层最小预测命中率：`{selected['minimum_predicted_active_fraction']:.12g}`。
- 紧邻的更大候选 `{rejected['candidate_s']:.12g} s` 只有 `{rejected['minimum_predicted_active_fraction']:.12g}`，未达到设计门，因此被拒绝。

## 结论边界

该选择不是把候选窗口的单个最小步长直接冻结，而是同时使用三层正式轨迹、输出对齐步和动态限步分布，并保留显式设计余量。由于改变登记步长会改变后续离散轨迹，Q1R2 完整运行后仍必须从实际 `diagnostic_dt_s` 重新检查空间组共同最大步长、时间组严格递减和每层至少 `0.95` 的命中率；失败时必须失败闭锁，不能用本报告替代。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--archive", action="append", default=[], required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--acceptance-fraction", type=float, default=0.95)
    parser.add_argument("--design-fraction", type=float, default=0.97)
    parser.add_argument("--quantum-s", type=float, default=1.0e-7)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    report = analyse(
        args.manifest.resolve(),
        _parse_archives(args.archive),
        args.expected_source_sha256,
        acceptance_fraction=args.acceptance_fraction,
        design_fraction=args.design_fraction,
        quantum_s=args.quantum_s,
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
