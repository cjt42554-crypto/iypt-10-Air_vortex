#!/usr/bin/env python3
"""Independently analyse D1 lockstep archives and publish the frozen decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
PREREGISTRATION_PATH = HERE / "d1_dynamic_shadow_preregistration.json"
RAW_ROOT = HERE / "artifacts" / "runs"
REPORT_JSON = HERE / "reports" / "d1_dynamic_shadow_report.json"
REPORT_MD = HERE / "reports" / "d1_dynamic_shadow_report.md"
ARTIFACT_MANIFEST = HERE / "artifacts" / "d1_dynamic_shadow_manifest.json"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

from airvortex.io import load_run_archive
from airvortex.io.archive import source_tree_identity
from airvortex.io.config_io import read_json_strict

PROTOCOL_ID = "D1_G0_G1_LOCKSTEP_DYNAMIC_SHADOW_V1"
FAIL_CLOSED = "D1_DYNAMIC_SHADOW_INCONCLUSIVE_FAIL_CLOSED"
PRIMARY = (
    "funnel_depth_m",
    "kinetic_energy_lab_j",
    "angular_momentum_z_kg_m2_s",
    "axisymmetry_error",
)
SECONDARY = (
    "min_gap_to_bar_m",
    "liquid_inventory_change_relative",
)
ARCHIVE_ALIAS = {
    "liquid_inventory_change_relative": "liquid_mass_error_relative",
}


class ShadowAnalysisError(RuntimeError):
    """Raised when evidence cannot support a preregistered analysis."""


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


def _json_object(path: Path) -> dict[str, Any]:
    value = read_json_strict(path)
    if not isinstance(value, dict):
        raise ShadowAnalysisError(f"JSON root must be an object: {path}")
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


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()


def rms(values: NDArray[np.floating[Any]]) -> float:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0 or not np.isfinite(array).all():
        raise ShadowAnalysisError("RMS input must be a nonempty finite array")
    return float(math.sqrt(float(np.mean(array * array))))


def _delta(values: NDArray[np.floating[Any]]) -> NDArray[np.float64]:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size == 0 or not np.isfinite(array).all():
        raise ShadowAnalysisError("observable trajectory must be nonempty and finite")
    return array - array[0]


def compute_observable_metrics(
    g0_6: NDArray[np.floating[Any]],
    g1_6: NDArray[np.floating[Any]],
    g0_9: NDArray[np.floating[Any]],
    g1_9: NDArray[np.floating[Any]],
    epsilon_q: float,
) -> dict[str, float]:
    """Compute all frozen D1 native, relative, eta, and rho metrics."""

    if not math.isfinite(epsilon_q) or epsilon_q <= 0.0:
        raise ValueError("epsilon_q must be finite and positive")
    d06, d16, d09, d19 = tuple(_delta(value) for value in (g0_6, g1_6, g0_9, g1_9))
    sizes = {value.size for value in (d06, d16, d09, d19)}
    if len(sizes) != 1:
        raise ShadowAnalysisError("D1 compared trajectories must have equal lengths")
    difference6 = d16 - d06
    difference9 = d19 - d09
    n6 = rms(difference6)
    n9 = rms(difference9)
    e0 = rms(d09 - d06)
    e1 = rms(d19 - d16)
    linf6 = float(np.max(np.abs(difference6)))
    linf9 = float(np.max(np.abs(difference9)))
    d6 = n6 / max(rms(d06), rms(d16), epsilon_q)
    d9 = n9 / max(rms(d09), rms(d19), epsilon_q)
    d6_linf = linf6 / max(
        float(np.max(np.abs(d06))),
        float(np.max(np.abs(d16))),
        epsilon_q,
    )
    d9_linf = linf9 / max(
        float(np.max(np.abs(d09))),
        float(np.max(np.abs(d19))),
        epsilon_q,
    )
    return {
        "epsilon_q": epsilon_q,
        "N_6": n6,
        "N_9": n9,
        "native_Linf_6": linf6,
        "native_Linf_9": linf9,
        "D_6_relative_L2": d6,
        "D_9_relative_L2": d9,
        "D_6_relative_Linf": d6_linf,
        "D_9_relative_Linf": d9_linf,
        "E_0": e0,
        "E_1": e1,
        "eta_6": n6 / max(e0, epsilon_q),
        "eta_9": n9 / max(e0, epsilon_q),
        "rho": e1 / max(e0, epsilon_q),
    }


def classify_materiality(
    primary_metrics: Mapping[str, Mapping[str, float]],
    thresholds: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Apply the four frozen labels without adding post-hoc thresholds."""

    direct_min = float(thresholds["direct_relative_l2_material"])
    eta_min = float(thresholds["eta_material"])
    rho_reduction = float(thresholds["rho_reduction_max"])
    rho_increase = float(thresholds["rho_increase_min"])
    count_min = int(thresholds["material_primary_observable_count_min"])
    small_eta = float(thresholds["small_eta_max"])
    small_rho = tuple(float(value) for value in thresholds["small_rho_interval"])

    flags: dict[str, dict[str, bool]] = {}
    for name, metrics in primary_metrics.items():
        max_d = max(
            float(metrics["D_6_relative_L2"]),
            float(metrics["D_9_relative_L2"]),
        )
        max_eta = max(float(metrics["eta_6"]), float(metrics["eta_9"]))
        rho = float(metrics["rho"])
        direct_material = max_d >= direct_min and max_eta >= eta_min
        flags[name] = {
            "direct_material": direct_material,
            "reduction_signal": direct_material and rho <= rho_reduction,
            "extreme_change_signal": direct_material
            and (rho <= rho_reduction or rho >= rho_increase),
            "small_window_effect": (
                max_d < direct_min
                and max_eta < small_eta
                and small_rho[0] <= rho <= small_rho[1]
            ),
        }
    reduction_count = sum(item["reduction_signal"] for item in flags.values())
    direct_count = sum(item["direct_material"] for item in flags.values())
    extreme_count = sum(item["extreme_change_signal"] for item in flags.values())
    if reduction_count >= count_min:
        label = "GEOMETRY_CONTRIBUTOR_WITH_REDUCTION_SIGNAL"
    elif (
        direct_count >= count_min
        and extreme_count >= count_min
        and reduction_count < count_min
    ):
        label = "GEOMETRY_CONTRIBUTOR_SIGNAL_WITHOUT_REDUCTION"
    elif all(item["small_window_effect"] for item in flags.values()):
        label = "GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW"
    else:
        label = "MIXED_OR_INDETERMINATE_GEOMETRY_EFFECT"
    return label, {
        "per_observable": flags,
        "direct_material_count": direct_count,
        "reduction_signal_count": reduction_count,
        "extreme_change_signal_count": extreme_count,
        "minimum_count": count_min,
    }


def _pair_path(level: int) -> Path:
    return RAW_ROOT / f"level_{level}" / "pair_summary.json"


def _archive_from_summary(summary: Mapping[str, Any], variant: str):
    archives = summary.get("archives")
    if not isinstance(archives, Mapping):
        raise ShadowAnalysisError("passing pair summary has no archives")
    record = archives.get(variant)
    if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
        raise ShadowAnalysisError(f"pair summary archive is missing for {variant}")
    return load_run_archive(REPOSITORY_ROOT / str(record["path"]))


def _observable(run: Any, name: str) -> NDArray[np.float64]:
    archive_name = ARCHIVE_ALIAS.get(name, name)
    if archive_name not in run.result.observables:
        raise ShadowAnalysisError(f"archive observable is missing: {archive_name}")
    return np.asarray(run.result.observables[archive_name], dtype=np.float64).reshape(
        -1
    )


def _trajectory_record(
    time_s: NDArray[np.float64],
    values: Mapping[str, NDArray[np.float64]],
) -> dict[str, Any]:
    return {
        "time_s": time_s.tolist(),
        "raw": {name: value.tolist() for name, value in values.items()},
        "initial_value_removed": {
            name: _delta(value).tolist() for name, value in values.items()
        },
    }


def _window_indices(
    time_s: NDArray[np.float64], start: float, end: float
) -> NDArray[np.int64]:
    tolerance = 256.0 * np.finfo(float).eps * max(end, 1.0)
    indices = np.flatnonzero(
        (time_s >= start - tolerance) & (time_s <= end + tolerance)
    )
    if indices.size < 2:
        raise ShadowAnalysisError(
            f"registered window [{start}, {end}] has too few frames"
        )
    return indices


def _materiality_analysis(
    prereg: Mapping[str, Any],
    level6: Mapping[str, Any],
    level9: Mapping[str, Any],
) -> dict[str, Any]:
    runs = {
        "g0_6": _archive_from_summary(level6, "g0"),
        "g1_6": _archive_from_summary(level6, "g1"),
        "g0_9": _archive_from_summary(level9, "g0"),
        "g1_9": _archive_from_summary(level9, "g1"),
    }
    times = {
        name: np.asarray(run.result.time_s, dtype=np.float64)
        for name, run in runs.items()
    }
    reference_time = times["g0_6"]
    time_identity = {
        name: bool(np.array_equal(reference_time, value))
        for name, value in times.items()
    }
    if not all(time_identity.values()):
        raise ShadowAnalysisError(
            "output frame times are not identical across D1 archives"
        )
    expected_time = np.linspace(0.0, 0.010, 11, dtype=np.float64)
    if not np.allclose(reference_time, expected_time, rtol=0.0, atol=2.0e-15):
        raise ShadowAnalysisError(
            "D1 output frames do not match the registered 0.001 s grid"
        )

    observables = prereg.get("observables")
    if not isinstance(observables, Mapping):
        raise ShadowAnalysisError("preregistration observables are missing")
    floors = observables.get("absolute_scale_floors")
    if not isinstance(floors, Mapping):
        raise ShadowAnalysisError("observable scale floors are missing")
    materiality = prereg.get("materiality_analysis")
    if not isinstance(materiality, Mapping) or not isinstance(
        materiality.get("thresholds"), Mapping
    ):
        raise ShadowAnalysisError("materiality thresholds are missing")

    series: dict[str, dict[str, NDArray[np.float64]]] = {}
    metrics: dict[str, dict[str, float]] = {}
    trajectories: dict[str, Any] = {}
    for observable in (*PRIMARY, *SECONDARY):
        values = {name: _observable(run, observable) for name, run in runs.items()}
        series[observable] = values
        epsilon_q = float(floors[observable])
        metrics[observable] = compute_observable_metrics(
            values["g0_6"],
            values["g1_6"],
            values["g0_9"],
            values["g1_9"],
            epsilon_q,
        )
        trajectories[observable] = _trajectory_record(reference_time, values)

    windows = prereg["execution_protocol"]["registered_windows_s"]
    window_metrics: dict[str, Any] = {}
    for window_name, bounds in windows.items():
        start, end = (float(value) for value in bounds)
        indices = _window_indices(reference_time, start, end)
        window_metrics[window_name] = {
            observable: compute_observable_metrics(
                series[observable]["g0_6"][indices],
                series[observable]["g1_6"][indices],
                series[observable]["g0_9"][indices],
                series[observable]["g1_9"][indices],
                float(floors[observable]),
            )
            for observable in (*PRIMARY, *SECONDARY)
        }
    primary_metrics = {name: metrics[name] for name in PRIMARY}
    label, classification = classify_materiality(
        primary_metrics, materiality["thresholds"]
    )
    return {
        "materiality_label": label,
        "classification": classification,
        "thresholds": dict(materiality["thresholds"]),
        "time_identity": time_identity,
        "output_time_s": reference_time.tolist(),
        "metrics": metrics,
        "registered_window_metrics": window_metrics,
        "trajectories": trajectories,
        "archive_integrity_verified_by_loader": True,
    }


def _raw_manifest() -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    if RAW_ROOT.is_dir():
        for path in sorted(item for item in RAW_ROOT.rglob("*") if item.is_file()):
            files.append(
                {
                    "path": _relative(path),
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "integrity_scope": "local ignored raw D1 products available at analysis time",
        "file_count": len(files),
        "total_size_bytes": sum(item["size_bytes"] for item in files),
        "files": files,
        "authenticity_limit": (
            "SHA-256 detects accidental mutation against this tracked manifest; it is not an external signature."
        ),
    }


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# D1 G0/G1 锁步动态影子对照报告",
        "",
        "## 裁决",
        "",
        "```text",
        str(report["status"]),
        "```",
        "",
        f"执行提交：`{report['identity']['execution_commit']}`。",
        "",
    ]
    if report.get("materiality_label") is None:
        lines.extend(
            [
                "由于至少一个硬门失败，本报告不生成几何物质性四分类标签。原始诊断保留，任务按预注册失败闭锁。",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "几何故障归因标签为：",
                "",
                "```text",
                str(report["materiality_label"]),
                "```",
                "",
                "该标签只解释登记短窗口内 G0/G1 布尔几何差异对既有 Q1R3 失败形态的贡献，不证明 G1 更接近连续方程或实验。",
                "",
                "## 主要观测量指标",
                "",
                "| 观测量 | max D | max eta | rho |",
                "|---|---:|---:|---:|",
            ]
        )
        metrics = report["analysis"]["metrics"]
        for name in PRIMARY:
            item = metrics[name]
            lines.append(
                "| {name} | {d:.6g} | {eta:.6g} | {rho:.6g} |".format(
                    name=name,
                    d=max(item["D_6_relative_L2"], item["D_9_relative_L2"]),
                    eta=max(item["eta_6"], item["eta_9"]),
                    rho=item["rho"],
                )
            )
        lines.append("")
    lines.extend(
        [
            "## 阶段门",
            "",
            "| 阶段 | 结果 |",
            "|---|---|",
        ]
    )
    for name, passed in report["stage_gates"].items():
        lines.append(f"| {name} | {'PASS' if passed else 'FAIL'} |")
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- Q1R3 空间阶段既有失败闭锁没有被撤销；",
            "- 本轮不是空间或时间收敛研究；",
            "- G1 没有提升到正式求解器；",
            "- 没有执行物理验证或实验验证；",
            "- 不允许临界转速、长期稳态、空气通道稳定性或装置级预测；",
            "- 报告完成后停止，等待 Pro 复核。",
            "",
            "## 原始证据",
            "",
            f"本地原始证据清单：`{_relative(ARTIFACT_MANIFEST)}`。",
            "",
        ]
    )
    return "\n".join(lines)


def analyse() -> dict[str, Any]:
    if REPORT_JSON.exists() or REPORT_MD.exists() or ARTIFACT_MANIFEST.exists():
        raise FileExistsError("D1 tracked report or manifest already exists")
    prereg = _json_object(PREREGISTRATION_PATH)
    verify_path = RAW_ROOT / "g0_self_identity.json"
    smoke_path = RAW_ROOT / "g0_g1_smoke.json"
    verify = _json_object(verify_path) if verify_path.is_file() else {}
    smoke = _json_object(smoke_path) if smoke_path.is_file() else {}
    level6 = _json_object(_pair_path(6)) if _pair_path(6).is_file() else {}
    level9 = _json_object(_pair_path(9)) if _pair_path(9).is_file() else {}
    source = source_tree_identity(FORMAL_ROOT)
    head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    porcelain = _git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    bound_source = prereg["evidence_bindings"]["formal_source_tree_sha256"]
    stage_gates = {
        "clean_main_at_analysis_start": branch == "main" and not porcelain,
        "formal_source_identity": source.get("value") == bound_source,
        "S1_G0_self_identity": verify.get("gate_pass") is True,
        "S2_CUDA_smoke": smoke.get("gate_pass") is True,
        "S3_level_6": level6.get("gate_pass") is True,
        "S4_level_9": level9.get("gate_pass") is True,
        "all_execution_commits_match": bool(
            verify
            and smoke
            and level6
            and level9
            and verify.get("identity", {}).get("execution_commit") == head
            and smoke.get("identity", {}).get("execution_commit") == head
            and level6.get("identity", {}).get("execution_commit") == head
            and level9.get("identity", {}).get("execution_commit") == head
        ),
        "source_stable_across_full_windows": bool(
            level6
            and level9
            and level6.get("source_tree_execution_start", {}).get("value")
            == bound_source
            and level6.get("source_tree_execution_end", {}).get("value") == bound_source
            and level9.get("source_tree_execution_start", {}).get("value")
            == bound_source
            and level9.get("source_tree_execution_end", {}).get("value") == bound_source
        ),
    }
    hard_gate_pass = all(stage_gates.values())
    analysis: dict[str, Any] | None = None
    materiality_label: str | None = None
    analysis_failure: dict[str, str] | None = None
    if hard_gate_pass:
        try:
            analysis = _materiality_analysis(prereg, level6, level9)
            materiality_label = str(analysis["materiality_label"])
        except Exception as exc:  # noqa: BLE001 - convert evidence failure to closed report
            hard_gate_pass = False
            stage_gates["independent_materiality_analysis"] = False
            analysis_failure = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
    else:
        stage_gates["independent_materiality_analysis"] = False
    if analysis is not None:
        stage_gates["independent_materiality_analysis"] = True

    status = (
        f"D1_DYNAMIC_SHADOW_COMPLETE__{materiality_label}__STOP_FOR_PRO_REVIEW"
        if hard_gate_pass and materiality_label is not None
        else FAIL_CLOSED
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "status": status,
        "gate_pass": hard_gate_pass,
        "materiality_label": materiality_label,
        "stage_gates": stage_gates,
        "analysis_failure": analysis_failure,
        "analysis": analysis,
        "identity": {
            "branch": branch,
            "execution_commit": head,
            "worktree_porcelain_at_analysis_start": porcelain,
            "formal_source_tree": source,
            "bound_formal_source_tree_sha256": bound_source,
        },
        "stage_reports": {
            "g0_self_identity": _relative(verify_path),
            "g0_g1_smoke": _relative(smoke_path),
            "level_6": _relative(_pair_path(6)),
            "level_9": _relative(_pair_path(9)),
        },
        "claim_boundary": {
            "purpose": "FAILURE_MECHANISM_ATTRIBUTION_ONLY",
            "q1r3_failure_overturned": False,
            "numerical_convergence_established": False,
            "temporal_convergence_established": False,
            "g1_formally_promoted": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
        "conclusion_ceiling": list(prereg["conclusion_ceiling"]),
        "stop_point": "REPORT_THEN_STOP_FOR_PRO_REVIEW",
    }
    manifest = _raw_manifest()
    _write_json(ARTIFACT_MANIFEST, manifest)
    report["artifact_manifest"] = {
        "path": _relative(ARTIFACT_MANIFEST),
        "raw_file_count": manifest["file_count"],
        "raw_total_size_bytes": manifest["total_size_bytes"],
    }
    _write_json(REPORT_JSON, report)
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(_markdown(report), encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    report = analyse()
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(REPORT_JSON),
                "markdown": str(REPORT_MD),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
