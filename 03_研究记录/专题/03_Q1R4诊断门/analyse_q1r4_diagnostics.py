#!/usr/bin/env python3
"""Prepare, run, and analyse the preregistered Q1R4 D0 diagnostics."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

import numpy as np


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

from airvortex.io import load_run_archive
from airvortex.io.archive import source_tree_identity
from airvortex.io.config_io import (
    config_sha256,
    load_config_json,
    read_json_strict,
    save_config_json,
)

from audit_q1r4_geometry import markdown as geometry_markdown
from probe_q1r4_resource import markdown as resource_markdown


Q1R3_ROOT = HERE.parent / "01_HF3D数值资格"
Q1R3_SIX_CONFIG = Q1R3_ROOT / "generated_q1r3/space_L1_6cells.json"
Q1R3_NINE_CONFIG = Q1R3_ROOT / "generated_q1r3/space_L2_9cells.json"
Q1R3_SIX_ARCHIVE = Q1R3_ROOT / "runs/q1r3/space_L1_6cells"
Q1R3_NINE_ARCHIVE = Q1R3_ROOT / "runs/q1r3/space_L2_9cells"
REFACTOR_REPORT = (
    HERE.parent / "02_HF3D纯结构限界重构/completion_report.json"
)
EXPECTED_REFERENCE_MANIFESTS = {
    "six_cells": "794b0bc63f9bec688f32f0bc71e4ca05160d3b4ffce9d0af90636ef87f6705ae",
    "nine_cells": "99b7cf26cd5e2c50959e09af1fcfa998a34df63778297e78ee6e27d9abd78b4d",
}


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


def _child_rss_bytes(pid: int) -> int | None:
    path = Path(f"/proc/{pid}/status")
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("VmRSS:"):
            fields = line.split()
            if len(fields) >= 3 and fields[2] == "kB":
                return int(fields[1]) * 1024
    return None


def _nvidia_process_used_bytes(pid: int) -> int | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in completed.stdout.splitlines():
        fields = [item.strip() for item in line.split(",")]
        if len(fields) != 2:
            continue
        try:
            process_id = int(fields[0])
            used_mib = int(fields[1])
        except ValueError:
            continue
        if process_id == pid:
            return used_mib * 1024 * 1024
    return None


def _load_mapping(path: Path) -> Mapping[str, Any]:
    value = read_json_strict(path)
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain an object")
    return value


def prepare_cases(
    preregistration_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    preregistration = _load_mapping(preregistration_path)
    protocol = preregistration["d0_scope"]["C_source_separation"][
        "analysis_protocol"
    ]
    window = protocol["common_window_s"]
    duration_s = float(window[1]) - float(window[0])
    output_interval_s = float(protocol["common_output_interval_s"])
    if window != [0.0, 0.005] or duration_s != 0.005:
        raise ValueError("D0 source-separation window must remain 0 to 0.005 s")

    six = load_config_json(Q1R3_SIX_CONFIG)
    nine = load_config_json(Q1R3_NINE_CONFIG)
    common_run_six = replace(
        six,
        run=replace(
            six.run,
            duration_s=duration_s,
            output_interval_s=output_interval_s,
        ),
        high_fidelity=replace(
            six.high_fidelity,
            pressure_solver_device="cuda",
        ),
    )
    common_run_nine = replace(
        nine,
        run=replace(
            nine.run,
            duration_s=duration_s,
            output_interval_s=output_interval_s,
        ),
        high_fidelity=replace(
            nine.high_fidelity,
            pressure_solver_device="cuda",
        ),
    )
    resource = replace(
        common_run_nine,
        high_fidelity=replace(
            common_run_nine.high_fidelity,
            radial_cells=204,
            azimuthal_cells=204,
            axial_cells=180,
            time_step_s=3.75e-6,
            pressure_solver_device="cuda",
        ),
    )
    zero_six = replace(
        common_run_six,
        drive=replace(
            common_run_six.drive,
            initial_rpm=0.0,
            target_rpm=0.0,
            actual_speed_schedule_time_s=(),
            actual_speed_schedule_rpm=(),
        ),
    )
    zero_nine = replace(
        common_run_nine,
        drive=replace(
            common_run_nine.drive,
            initial_rpm=0.0,
            target_rpm=0.0,
            actual_speed_schedule_time_s=(),
            actual_speed_schedule_rpm=(),
        ),
    )
    controls = preregistration["d0_scope"]["C_source_separation"][
        "nine_cell_fixed_physical_controls"
    ]
    fixed_nine = replace(
        common_run_nine,
        high_fidelity=replace(
            common_run_nine.high_fidelity,
            interface_thickness_cells=float(controls["interface_thickness_cells"]),
            boundary_layer_cells=float(controls["boundary_layer_cells"]),
            surface_tension_smoothing_cells=float(
                controls["surface_tension_smoothing_cells"]
            ),
        ),
    )
    configs = {
        "resource_probe_13p5": resource,
        "zero_drive_6cells": zero_six,
        "zero_drive_9cells": zero_nine,
        "fixed_physical_9cells": fixed_nine,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    cases: dict[str, dict[str, object]] = {}
    for case_id, config in configs.items():
        path = output_dir / f"{case_id}.json"
        save_config_json(path, config, overwrite=True)
        cases[case_id] = {
            "config_file": path.name,
            "config_file_sha256": _sha256(path),
            "config_canonical_sha256": config_sha256(config),
            "grid": [
                config.high_fidelity.radial_cells,
                config.high_fidelity.azimuthal_cells,
                config.high_fidelity.axial_cells,
            ],
            "duration_s": config.run.duration_s,
            "output_interval_s": config.run.output_interval_s,
            "pressure_solver_device": config.high_fidelity.pressure_solver_device,
            "diagnostic_only": True,
        }
    manifest: dict[str, object] = {
        "schema_version": 1,
        "diagnostic_protocol_id": preregistration["diagnostic_protocol_id"],
        "decision_id": preregistration["decision_id"],
        "preregistration_file_sha256": _sha256(preregistration_path),
        "formal_source_tree_at_generation": source_tree_identity(FORMAL_ROOT),
        "cases": cases,
        "q1r3_forced_diagnostic_references": {
            "qualification_evidence_reuse_allowed": False,
            "six_cells": {
                "path": str(Q1R3_SIX_ARCHIVE.relative_to(REPOSITORY_ROOT)),
                "manifest_sha256": EXPECTED_REFERENCE_MANIFESTS["six_cells"],
            },
            "nine_cells": {
                "path": str(Q1R3_NINE_ARCHIVE.relative_to(REPOSITORY_ROOT)),
                "manifest_sha256": EXPECTED_REFERENCE_MANIFESTS["nine_cells"],
            },
        },
        "claim_boundary": {
            "is_campaign_q1r4": False,
            "freezes_q1r4": False,
            "allows_full_q1r4_run": False,
            "allows_apparatus_prediction": False,
        },
    }
    _write_json(output_dir / "cases_manifest.json", manifest)
    return manifest


def validate_generated_manifest(
    manifest_path: Path,
    preregistration_path: Path,
) -> dict[str, bool]:
    manifest = _load_mapping(manifest_path)
    preregistration = _load_mapping(preregistration_path)
    cases = manifest.get("cases")
    if not isinstance(cases, Mapping):
        return {"cases_mapping": False}
    expected = {
        "resource_probe_13p5",
        "zero_drive_6cells",
        "zero_drive_9cells",
        "fixed_physical_9cells",
    }
    config_root = manifest_path.parent
    hashes = True
    for case_id, raw in cases.items():
        if not isinstance(raw, Mapping) or not isinstance(raw.get("config_file"), str):
            hashes = False
            continue
        path = config_root / raw["config_file"]
        hashes = hashes and path.is_file() and _sha256(path) == raw.get(
            "config_file_sha256"
        )
    boundary = manifest.get("claim_boundary", {})
    return {
        "decision_id": manifest.get("decision_id")
        == preregistration.get("decision_id"),
        "protocol_id": manifest.get("diagnostic_protocol_id")
        == preregistration.get("diagnostic_protocol_id"),
        "preregistration_hash": manifest.get("preregistration_file_sha256")
        == _sha256(preregistration_path),
        "case_set": set(cases) == expected,
        "config_hashes": hashes,
        "not_q1r4_campaign": isinstance(boundary, Mapping)
        and boundary.get("is_campaign_q1r4") is False
        and boundary.get("freezes_q1r4") is False
        and boundary.get("allows_full_q1r4_run") is False,
    }


def run_source_separation_cases(
    manifest_path: Path,
    preregistration_path: Path,
    resource_probe_path: Path,
    output_root: Path,
    python_executable: str,
) -> dict[str, object]:
    gates = validate_generated_manifest(manifest_path, preregistration_path)
    resource = _load_mapping(resource_probe_path)
    gates["resource_probe_pass"] = resource.get("gate_pass") is True
    identity_start = git_identity()
    gates["clean_main_start"] = bool(
        identity_start["branch"] == "main"
        and identity_start["worktree_clean"] is True
    )
    if not all(gates.values()):
        raise RuntimeError(f"D0-C pre-run gates failed: {gates}")
    manifest = _load_mapping(manifest_path)
    selected = (
        "zero_drive_6cells",
        "zero_drive_9cells",
        "fixed_physical_9cells",
    )
    output_root.mkdir(parents=True, exist_ok=True)
    source_start = source_tree_identity(FORMAL_ROOT)
    records: list[dict[str, object]] = []
    for case_id in selected:
        raw = manifest["cases"][case_id]
        config_path = manifest_path.parent / raw["config_file"]
        archive_path = output_root / case_id
        log_path = output_root / f"{case_id}.log"
        if archive_path.exists() or log_path.exists():
            raise FileExistsError(f"D0-C output already exists for {case_id}")
        command = [
            python_executable,
            "-m",
            "airvortex",
            "run",
            "--backend",
            "high_fidelity",
            "--config",
            str(config_path.resolve()),
            "--output",
            str(archive_path.resolve()),
        ]
        started = time.monotonic()
        print(
            json.dumps(
                {"stage": "case_start", "case_id": case_id, "command": command},
                ensure_ascii=False,
            ),
            flush=True,
        )
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            while True:
                return_code = process.poll()
                if return_code is not None:
                    break
                time.sleep(30.0)
                print(
                    json.dumps(
                        {
                            "stage": "case_heartbeat",
                            "case_id": case_id,
                            "elapsed_s": time.monotonic() - started,
                            "child_rss_bytes": _child_rss_bytes(process.pid),
                            "child_gpu_bytes": _nvidia_process_used_bytes(process.pid),
                            "log_size_bytes": (
                                log_path.stat().st_size if log_path.exists() else 0
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        wall_s = time.monotonic() - started
        record = {
            "case_id": case_id,
            "return_code": return_code,
            "wall_s": wall_s,
            "archive": str(archive_path),
            "log": str(log_path),
        }
        records.append(record)
        print(
            json.dumps({"stage": "case_end", **record}, ensure_ascii=False),
            flush=True,
        )
        if return_code != 0:
            break
        loaded = load_run_archive(archive_path)
        if loaded.result.status.value != "completed":
            raise RuntimeError(f"{case_id} did not produce a completed archive")
    source_end = source_tree_identity(FORMAL_ROOT)
    identity_end = git_identity()
    summary = {
        "schema_version": 1,
        "status": (
            "D0_SOURCE_SEPARATION_RUNS_COMPLETE"
            if len(records) == len(selected)
            and all(item["return_code"] == 0 for item in records)
            else "D0_SOURCE_SEPARATION_RUNS_FAIL_CLOSED"
        ),
        "gate_pass": bool(
            len(records) == len(selected)
            and all(item["return_code"] == 0 for item in records)
            and source_start == source_end
            and identity_start["head"] == identity_end["head"]
            and identity_start["worktree_clean"] is True
            and identity_end["worktree_clean"] is True
        ),
        "pre_run_gates": gates,
        "identity_start": identity_start,
        "identity_end": identity_end,
        "formal_source_tree_start": source_start,
        "formal_source_tree_end": source_end,
        "cases": records,
    }
    _write_json(output_root / "run_summary.json", summary)
    return summary


def _archive_identity(archive: Any) -> dict[str, object]:
    source = archive.provenance.get("source_identity", {})
    diagnostics = archive.result.metadata.get("diagnostics", {})
    accelerator = diagnostics.get("pressure_accelerator", {})
    return {
        "status": archive.result.status.value,
        "execution_source_identity_valid": source.get(
            "execution_source_identity_valid"
        ),
        "source_tree_sha256_start": source.get("source_tree_sha256_start"),
        "source_tree_sha256_end": source.get("source_tree_sha256_end"),
        "pressure_accelerator": dict(accelerator),
        "completed_steps": diagnostics.get("completed_steps"),
        "simulated_duration_s": diagnostics.get("simulated_duration_s"),
        "numerical_quality_gate": diagnostics.get("numerical_quality_gate"),
    }


def _series_at_times(archive: Any, name: str, target_times: np.ndarray) -> np.ndarray:
    times = np.asarray(archive.result.time_s, dtype=np.float64)
    values = np.asarray(archive.result.observables[name], dtype=np.float64)
    if times.ndim != 1 or values.ndim != 1 or times.size != values.size:
        raise ValueError(f"{name} must be a scalar time series")
    if target_times[0] < times[0] or target_times[-1] > times[-1]:
        raise ValueError(f"{name} does not cover the preregistered window")
    return np.interp(target_times, times, values)


def _l2(value: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(value, dtype=np.float64)))


def effect_ratio(
    effect: Sequence[float],
    reference: Sequence[float],
    epsilon: float,
) -> float:
    return _l2(np.asarray(effect)) / max(_l2(np.asarray(reference)), epsilon)


def decide_d0(
    *,
    resource_pass: bool,
    geometry_bounded: bool,
    zero_drive_material_count: int,
    regularization_material_count: int,
    material_count_min: int,
    identity_complete: bool,
) -> dict[str, object]:
    if not resource_pass:
        status = "STOP_FULL_Q1R4__OPTIMISATION_OR_MODEL_REDESIGN_REQUIRED"
    elif (
        not geometry_bounded
        or zero_drive_material_count >= material_count_min
        or regularization_material_count >= material_count_min
    ):
        status = (
            "STOP_FULL_Q1R4__DEFINE_INTENDED_ASYMPTOTIC_MODEL_BEFORE_REFINEMENT"
        )
    elif not identity_complete:
        status = "STOP_FULL_Q1R4__D0_DATA_OR_SOURCE_IDENTITY_INCOMPLETE"
    else:
        status = (
            "DRAFT_NEW_6_9_13P5_PREREGISTRATION__DO_NOT_RUN_AUTOMATICALLY"
        )
    return {
        "status": status,
        "d0_complete": bool(resource_pass and identity_complete),
        "new_preregistration_draft_allowed": status.startswith("DRAFT_NEW_"),
        "full_q1r4_frozen": False,
        "full_q1r4_run_allowed": False,
        "campaign_q1r4_creation_allowed": False,
        "apparatus_prediction_allowed": False,
    }


def geometry_early_stop_report(
    preregistration_path: Path,
    geometry_path: Path,
) -> dict[str, object]:
    """Close D0 at A without inventing absent resource or dynamic evidence."""

    preregistration = _load_mapping(preregistration_path)
    geometry = _load_mapping(geometry_path)
    identity_gates = geometry.get("identity", {}).get("gates", {})
    identity_complete = bool(
        isinstance(identity_gates, Mapping)
        and all(identity_gates.values())
    )
    geometry_failed = geometry.get("gate_pass") is False
    if not geometry_failed:
        raise ValueError("geometry early-stop report requires a failed D0-A gate")
    return {
        "schema_version": 1,
        "diagnostic_protocol_id": preregistration["diagnostic_protocol_id"],
        "status": "Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY",
        "gate_pass": False,
        "inputs": {
            "preregistration_sha256": _sha256(preregistration_path),
            "geometry_report_sha256": _sha256(geometry_path),
        },
        "identity": {
            "complete": identity_complete,
            "geometry_execution_identity": geometry.get("identity"),
        },
        "static_geometry": {
            "executed": True,
            "dynamic_solver_steps": geometry.get("dynamic_solver_steps"),
            "gate_pass": False,
            "status": geometry.get("status"),
            "assessment": geometry.get("assessment"),
        },
        "resource_probe": {
            "executed": False,
            "reason": "D0-A preregistered geometry gate failed first",
        },
        "source_separation": {
            "executed": False,
            "reason": "D0-A preregistered geometry gate failed first",
        },
        "decision": {
            "status": (
                "STOP_FULL_Q1R4__DEFINE_INTENDED_ASYMPTOTIC_MODEL_BEFORE_REFINEMENT"
            ),
            "d0_complete": False,
            "new_preregistration_draft_allowed": False,
            "full_q1r4_frozen": False,
            "full_q1r4_run_allowed": False,
            "campaign_q1r4_creation_allowed": False,
            "requires_human_or_pro_ruling": True,
            "apparatus_prediction_allowed": False,
        },
        "interpretation": {
            "geometry_proven_as_cause_of_q1r3_failure": False,
            "geometry_attribution_bounded": False,
            "posthoc_threshold_change_allowed": False,
        },
        "claim_boundary": {
            "q1r4_frozen": False,
            "full_q1r4_run_allowed": False,
            "spatial_convergence_demonstrated": False,
            "temporal_convergence_demonstrated": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
    }


def geometry_early_stop_markdown(report: Mapping[str, Any]) -> str:
    assessment = report["static_geometry"]["assessment"]
    level_checks = assessment["individual_level_checks"]
    pairwise = assessment["pairwise_checks"]
    failed: list[str] = []
    for level, checks in level_checks.items():
        for name, passed in checks.items():
            if not passed:
                failed.append(f"`{level}` 层 `{name}`")
    return f"""# Q1R4 D0 诊断门早停裁决

状态：`{report['status']}`。

D0-A 静态几何审计在清洁 `main` 和稳定正式源码身份下完成，动态求解步数为 `0`。预注册几何门未通过，因此按失败闭锁顺序停止；D0-B `13.5` 格 CUDA 八步资源探针和 D0-C 来源解耦均未运行。

## 触发项

- 未通过的单层门：{', '.join(failed)}。
- `6→9` 最大归一化误差跳变：`{pairwise['6_to_9']['maximum_jump']:.8g}`；通过=`{pairwise['6_to_9']['pass']}`。
- `9→13.5` 最大归一化误差跳变：`{pairwise['9_to_13.5']['maximum_jump']:.8g}`；通过=`{pairwise['9_to_13.5']['pass']}`。
- 单层几何误差总门：`{assessment['gates']['individual_geometry_errors_bounded']}`。
- 层间跳变总门：`{assessment['gates']['pairwise_geometry_error_jumps_bounded']}`。

这只能说明预注册协议尚未把体素几何归因限制住，不能声称体素几何已经被证明是 Q1R3 空间失败的原因。阈值不能在看到结果后放宽；是否重新定义几何诊断门、采用更可控几何离散，或终止当前 HF3D 加密路径，需要人工或 Pro 另行裁决。

## 未执行与结论上限

- D0-B 资源探针：未执行；
- D0-C 两个零驱动算例和固定物理宽度对照：未执行；
- `campaign_q1r4.json`：未建立；
- 完整 Q1R4：未冻结、未运行；
- Q1R3 时间组：未继续；
- 数值资格、物理验证、实验验证：均未建立；
- 装置级预测：继续禁止。
"""


def _zero_drive_metrics(archive: Any) -> dict[str, object]:
    fields = archive.result.fields
    active = np.asarray(fields["active_fluid_mask_3d"], dtype=bool)
    velocity = np.sqrt(
        np.asarray(fields["final_u_x_lab_m_s_3d"]) ** 2
        + np.asarray(fields["final_u_y_lab_m_s_3d"]) ** 2
        + np.asarray(fields["final_u_z_lab_m_s_3d"]) ** 2
    )
    surface = np.asarray(archive.result.observables["surface_profile_m"])
    inventory = np.asarray(
        archive.result.observables["liquid_mass_error_relative"]
    )
    corrected = np.asarray(
        archive.result.observables["liquid_volume_balance_error_relative"]
    )
    diagnostics = archive.result.metadata["diagnostics"]
    return {
        "maximum_parasitic_velocity_m_s": float(np.max(velocity[active])),
        "maximum_surface_profile_change_from_initial_m": float(
            np.max(np.abs(surface - surface[0]))
        ),
        "maximum_absolute_liquid_inventory_change_relative": float(
            np.max(np.abs(inventory))
        ),
        "maximum_absolute_open_boundary_corrected_balance_relative": float(
            np.max(np.abs(corrected))
        ),
        "maximum_pressure_relative_residual": diagnostics.get(
            "maximum_pressure_relative_residual"
        ),
        "maximum_projected_divergence_s_inv": diagnostics.get(
            "maximum_projected_divergence_s_inv"
        ),
        "maximum_advective_cfl": diagnostics.get("maximum_advective_cfl"),
    }


def analyse(
    preregistration_path: Path,
    generated_manifest_path: Path,
    geometry_path: Path,
    resource_path: Path,
    runs_root: Path,
) -> dict[str, object]:
    preregistration = _load_mapping(preregistration_path)
    geometry = _load_mapping(geometry_path)
    resource = _load_mapping(resource_path)
    run_summary = _load_mapping(runs_root / "run_summary.json")
    generated_gates = validate_generated_manifest(
        generated_manifest_path,
        preregistration_path,
    )
    refactor = _load_mapping(REFACTOR_REPORT)
    refactor_equivalence = bool(
        refactor.get("status")
        == "PASS_HF3D_BOUNDED_STRUCTURAL_REFACTOR__CPU_CUDA_STRICT_EQUIVALENCE"
        and refactor["equivalence"]["cpu"]["status"]
        == "PASS_STRICT_ELEMENTWISE_EQUIVALENCE"
        and refactor["equivalence"]["cuda"]["status"]
        == "PASS_STRICT_ELEMENTWISE_EQUIVALENCE"
    )

    archives = {
        "forced_6cells_q1r3_diagnostic_reference": load_run_archive(
            Q1R3_SIX_ARCHIVE
        ),
        "forced_9cells_q1r3_diagnostic_reference": load_run_archive(
            Q1R3_NINE_ARCHIVE
        ),
        "zero_drive_6cells": load_run_archive(runs_root / "zero_drive_6cells"),
        "zero_drive_9cells": load_run_archive(runs_root / "zero_drive_9cells"),
        "fixed_physical_9cells": load_run_archive(
            runs_root / "fixed_physical_9cells"
        ),
    }
    archive_identities = {
        name: _archive_identity(archive) for name, archive in archives.items()
    }
    reference_hashes = {
        "six_cells": _sha256(Q1R3_SIX_ARCHIVE / "manifest.json"),
        "nine_cells": _sha256(Q1R3_NINE_ARCHIVE / "manifest.json"),
    }
    reference_identity = reference_hashes == EXPECTED_REFERENCE_MANIFESTS
    new_archive_identity = all(
        item["status"] == "completed"
        and item["execution_source_identity_valid"] is True
        and item["pressure_accelerator"].get("selected") == "cuda"
        and item["pressure_accelerator"].get("fallback") is False
        and item["numerical_quality_gate"].get("pass") is True
        for name, item in archive_identities.items()
        if "q1r3_diagnostic_reference" not in name
    )

    protocol = preregistration["d0_scope"]["C_source_separation"][
        "analysis_protocol"
    ]
    target_times = np.arange(
        float(protocol["common_window_s"][0]),
        float(protocol["common_window_s"][1])
        + 0.5 * float(protocol["common_output_interval_s"]),
        float(protocol["common_output_interval_s"]),
        dtype=np.float64,
    )
    epsilon = float(protocol["eta_denominator_epsilon_native_units"])
    eta_threshold = float(protocol["eta_reg_material_threshold"])
    zero_threshold = float(protocol["eta_zero_drive_material_threshold"])
    material_count_min = int(protocol["material_core_observable_count_min"])
    observables = list(protocol["core_smooth_observables"])

    regularization: dict[str, dict[str, object]] = {}
    zero_drive_resolution: dict[str, dict[str, object]] = {}
    for name in observables:
        forced_six = _series_at_times(archives[
            "forced_6cells_q1r3_diagnostic_reference"
        ], name, target_times)
        forced_nine = _series_at_times(archives[
            "forced_9cells_q1r3_diagnostic_reference"
        ], name, target_times)
        fixed_nine = _series_at_times(
            archives["fixed_physical_9cells"], name, target_times
        )
        zero_six = _series_at_times(
            archives["zero_drive_6cells"], name, target_times
        )
        zero_nine = _series_at_times(
            archives["zero_drive_9cells"], name, target_times
        )
        d_cell = forced_nine - forced_six
        d_reg = fixed_nine - forced_nine
        eta_reg = effect_ratio(d_reg, d_cell, epsilon)
        d_zero = zero_nine - zero_six
        eta_zero = effect_ratio(d_zero, d_cell, epsilon)
        regularization[name] = {
            "d_cell_l2_native_units": _l2(d_cell),
            "d_reg_l2_native_units": _l2(d_reg),
            "eta_reg": eta_reg,
            "material": eta_reg >= eta_threshold,
        }
        zero_drive_resolution[name] = {
            "zero_drive_6_to_9_l2_native_units": _l2(d_zero),
            "forced_6_to_9_l2_native_units": _l2(d_cell),
            "eta_zero_drive": eta_zero,
            "material": eta_zero >= zero_threshold,
        }
    regularization_material_count = sum(
        bool(item["material"]) for item in regularization.values()
    )
    zero_material_count = sum(
        bool(item["material"]) for item in zero_drive_resolution.values()
    )
    regularization_material = regularization_material_count >= material_count_min
    zero_drive_material = zero_material_count >= material_count_min
    zero_metrics = {
        "six_cells": _zero_drive_metrics(archives["zero_drive_6cells"]),
        "nine_cells": _zero_drive_metrics(archives["zero_drive_9cells"]),
    }
    identity_complete = bool(
        all(generated_gates.values())
        and reference_identity
        and refactor_equivalence
        and new_archive_identity
        and run_summary.get("gate_pass") is True
        and geometry.get("identity", {}).get("gates", {}).get(
            "formal_source_tree_stable"
        )
        is True
        and resource.get("identity", {}).get("formal_source_tree_stable") is True
    )
    decision = decide_d0(
        resource_pass=resource.get("gate_pass") is True,
        geometry_bounded=geometry.get("gate_pass") is True,
        zero_drive_material_count=zero_material_count,
        regularization_material_count=regularization_material_count,
        material_count_min=material_count_min,
        identity_complete=identity_complete,
    )
    return {
        "schema_version": 1,
        "diagnostic_protocol_id": preregistration["diagnostic_protocol_id"],
        "status": decision["status"],
        "gate_pass": bool(
            decision["d0_complete"]
            and resource.get("gate_pass") is True
            and run_summary.get("gate_pass") is True
        ),
        "inputs": {
            "preregistration_sha256": _sha256(preregistration_path),
            "generated_manifest_sha256": _sha256(generated_manifest_path),
            "geometry_report_sha256": _sha256(geometry_path),
            "resource_report_sha256": _sha256(resource_path),
            "q1r3_reference_manifest_sha256": reference_hashes,
            "hf3d_refactor_completion_report_sha256": _sha256(REFACTOR_REPORT),
        },
        "identity": {
            "complete": identity_complete,
            "generated_manifest_gates": generated_gates,
            "q1r3_reference_manifest_identity": reference_identity,
            "q1r3_reference_role": "DIAGNOSTIC_ONLY_NOT_Q1R4_QUALIFICATION_EVIDENCE",
            "strict_structural_refactor_equivalence": refactor_equivalence,
            "new_archive_identity": new_archive_identity,
            "archives": archive_identities,
        },
        "static_geometry": {
            "gate_pass": geometry.get("gate_pass"),
            "status": geometry.get("status"),
            "assessment": geometry.get("assessment"),
        },
        "resource_probe": {
            "gate_pass": resource.get("gate_pass"),
            "status": resource.get("status"),
            "execution": resource.get("execution"),
            "resources": {
                key: value
                for key, value in resource.get("resources", {}).items()
                if key != "samples"
            },
        },
        "source_separation": {
            "window_s": protocol["common_window_s"],
            "output_interval_s": protocol["common_output_interval_s"],
            "eta_material_threshold": eta_threshold,
            "material_core_observable_count_min": material_count_min,
            "regularization": {
                "diagnostic_only": True,
                "observables": regularization,
                "material_observable_count": regularization_material_count,
                "material": regularization_material,
            },
            "zero_drive_resolution": {
                "observables": zero_drive_resolution,
                "material_observable_count": zero_material_count,
                "material": zero_drive_material,
                "absolute_metrics": zero_metrics,
            },
            "inventory_and_conservation_are_separate": True,
        },
        "decision": decision,
        "claim_boundary": {
            "q1r4_frozen": False,
            "full_q1r4_run_allowed": False,
            "spatial_convergence_demonstrated": False,
            "temporal_convergence_demonstrated": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
    }


def markdown(report: Mapping[str, Any]) -> str:
    source = report["source_separation"]
    reg_rows = []
    for name, item in source["regularization"]["observables"].items():
        reg_rows.append(
            f"| `{name}` | {item['d_cell_l2_native_units']:.6g} | "
            f"{item['d_reg_l2_native_units']:.6g} | {item['eta_reg']:.6g} | "
            f"{item['material']} |"
        )
    zero_rows = []
    for name, item in source["zero_drive_resolution"]["observables"].items():
        zero_rows.append(
            f"| `{name}` | {item['zero_drive_6_to_9_l2_native_units']:.6g} | "
            f"{item['forced_6_to_9_l2_native_units']:.6g} | "
            f"{item['eta_zero_drive']:.6g} | {item['material']} |"
        )
    resource = report["resource_probe"]
    resource_values = resource["resources"]
    decision = report["decision"]
    return f"""# Q1R4 D0 诊断门综合裁决

状态：`{report['status']}`。

## 执行完整性

- D0-A 静态几何：`{report['static_geometry']['status']}`；通过=`{report['static_geometry']['gate_pass']}`。
- D0-B 八步资源探针：`{resource['status']}`；通过=`{resource['gate_pass']}`。
- D0-C 数据与源码身份完整：`{report['identity']['complete']}`。
- 既有 Q1R3 `6/9` 只作为强迫固定格数路径的诊断参照；资格证据复用被明确禁止。

## 资源探针摘要

- 进程 RSS 峰值：`{resource_values.get('process_rss_peak_bytes')}` byte。
- 主机最低可用内存：`{resource_values.get('host_available_memory_min_bytes')}` byte。
- CUDA 最低空闲：`{resource_values.get('cuda_free_memory_min_bytes')}` byte。
- `nvidia-smi` 本进程峰值：`{resource_values.get('nvidia_smi_process_peak_bytes')}` byte。

## 正则化路径敏感性

定义中的 `D_cell`、`D_reg` 和 `eta_reg` 均按已登记的 `0–0.005 s` 标量时间序列计算。`eta_reg ≥ 0.5` 为实质影响标记；至少两个核心量被标记即触发停止。

| 核心量 | `D_cell` L2 | `D_reg` L2 | `eta_reg` | 实质影响 |
|---|---:|---:|---:|---:|
{chr(10).join(reg_rows)}

实质影响核心量：`{source['regularization']['material_observable_count']}`；整体实质影响=`{source['regularization']['material']}`。固定物理宽度始终是 `DIAGNOSTIC_ONLY`，不能进入资格组。

## 零驱动相关分辨率诊断

`eta_zero_drive` 把零驱动 `6→9` 差异与同窗口强迫 `6→9` 差异比较。它是来源筛查比值，不是物理误差或实验偏差。

| 核心量 | 零驱动 `6→9` L2 | 强迫 `6→9` L2 | `eta_zero_drive` | 实质影响 |
|---|---:|---:|---:|---:|
{chr(10).join(zero_rows)}

实质影响核心量：`{source['zero_drive_resolution']['material_observable_count']}`；整体实质影响=`{source['zero_drive_resolution']['material']}`。

液体净存量变化与开放边界通量校正守恒已分开报告；后者是数值守恒门，前者是模型域与边界响应观测，二者没有被混写。

## D0 决策

- D0 完整：`{decision['d0_complete']}`。
- 允许起草新预注册：`{decision['new_preregistration_draft_allowed']}`。
- 完整 Q1R4 已冻结：`{decision['full_q1r4_frozen']}`。
- 完整 Q1R4 可运行：`{decision['full_q1r4_run_allowed']}`。

当前报告不建立空间或时间收敛，不执行物理或实验验证，也不允许装置级预测。Q1R3 的失败闭锁事实不变。
"""


def publish_reports(
    report_dir: Path,
    geometry_path: Path,
    resource_path: Path,
    combined: Mapping[str, Any],
) -> None:
    geometry = _load_mapping(geometry_path)
    resource = _load_mapping(resource_path)
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_json(report_dir / "static_geometry_audit.json", geometry)
    (report_dir / "static_geometry_audit.md").write_text(
        geometry_markdown(geometry), encoding="utf-8"
    )
    _write_json(report_dir / "resource_probe.json", resource)
    (report_dir / "resource_probe.md").write_text(
        resource_markdown(resource), encoding="utf-8"
    )
    _write_json(report_dir / "d0_diagnostic_decision.json", combined)
    (report_dir / "d0_diagnostic_decision.md").write_text(
        markdown(combined), encoding="utf-8"
    )


def publish_geometry_early_stop(
    report_dir: Path,
    geometry_path: Path,
    report: Mapping[str, Any],
) -> None:
    geometry = _load_mapping(geometry_path)
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_json(report_dir / "static_geometry_audit.json", geometry)
    (report_dir / "static_geometry_audit.md").write_text(
        geometry_markdown(geometry), encoding="utf-8"
    )
    _write_json(report_dir / "d0_diagnostic_decision.json", report)
    (report_dir / "d0_diagnostic_decision.md").write_text(
        geometry_early_stop_markdown(report), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument(
        "--preregistration",
        type=Path,
        default=HERE / "q1r4_diagnostic_preregistration.json",
    )
    prepare_parser.add_argument("--output", type=Path, default=HERE / "generated")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument(
        "--manifest", type=Path, default=HERE / "generated/cases_manifest.json"
    )
    run_parser.add_argument(
        "--preregistration",
        type=Path,
        default=HERE / "q1r4_diagnostic_preregistration.json",
    )
    run_parser.add_argument("--resource-probe", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--python", default=sys.executable)

    analyse_parser = subparsers.add_parser("analyse")
    analyse_parser.add_argument(
        "--preregistration",
        type=Path,
        default=HERE / "q1r4_diagnostic_preregistration.json",
    )
    analyse_parser.add_argument(
        "--manifest", type=Path, default=HERE / "generated/cases_manifest.json"
    )
    analyse_parser.add_argument("--geometry", type=Path, required=True)
    analyse_parser.add_argument("--resource-probe", type=Path, required=True)
    analyse_parser.add_argument("--runs", type=Path, required=True)
    analyse_parser.add_argument("--report-dir", type=Path, default=HERE / "reports")

    stop_parser = subparsers.add_parser("finalize-geometry-stop")
    stop_parser.add_argument(
        "--preregistration",
        type=Path,
        default=HERE / "q1r4_diagnostic_preregistration.json",
    )
    stop_parser.add_argument("--geometry", type=Path, required=True)
    stop_parser.add_argument("--report-dir", type=Path, default=HERE / "reports")

    args = parser.parse_args()
    if args.command == "prepare":
        manifest = prepare_cases(
            args.preregistration.resolve(), args.output.resolve()
        )
        print(
            json.dumps(
                {"status": "D0_CONFIGS_PREPARED", "cases": list(manifest["cases"])},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "run":
        summary = run_source_separation_cases(
            args.manifest.resolve(),
            args.preregistration.resolve(),
            args.resource_probe.resolve(),
            args.output.resolve(),
            args.python,
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["gate_pass"] else 1
    if args.command == "analyse":
        combined = analyse(
            args.preregistration.resolve(),
            args.manifest.resolve(),
            args.geometry.resolve(),
            args.resource_probe.resolve(),
            args.runs.resolve(),
        )
        publish_reports(
            args.report_dir.resolve(),
            args.geometry.resolve(),
            args.resource_probe.resolve(),
            combined,
        )
        print(
            json.dumps(
                {"status": combined["status"], "gate_pass": combined["gate_pass"]},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if combined["gate_pass"] else 1
    if args.command == "finalize-geometry-stop":
        report = geometry_early_stop_report(
            args.preregistration.resolve(),
            args.geometry.resolve(),
        )
        publish_geometry_early_stop(
            args.report_dir.resolve(),
            args.geometry.resolve(),
            report,
        )
        print(
            json.dumps(
                {"status": report["status"], "gate_pass": report["gate_pass"]},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
