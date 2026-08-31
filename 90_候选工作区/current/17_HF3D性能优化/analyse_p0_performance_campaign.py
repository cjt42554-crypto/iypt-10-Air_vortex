#!/usr/bin/env python3
"""Independently recompute compact P0-P baseline/profile evidence."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from p0_performance_contract import (
    EXPANDED_EXECUTION_SCHEDULE,
    HERE,
    RAW_ROOT,
    P0PerformanceError,
    atomic_json,
    atomic_text,
    sample_identity,
    sha256_file,
    strict_json,
)
from p0p_allocation import audit_observer_payload, device_allocation_crosscheck
from p0p_consumption import (
    CLAIM_PATH,
    COMPLETE_PATH,
    LEDGER_PATH,
    validate_claim,
    validate_ledger,
)
from p0p_instrumentation import audit_host_semantic_evidence, parse_pstats
from p0p_nsys import REQUIRED_STATS, audit_nsys_evidence
from p0p_numerical import archive_numerical_payload
from p0p_telemetry import audit_monitor_records, parse_monitor_jsonl

OUTPUT_ROOT = HERE / "artifacts" / "p0"
MANIFEST_PATH = OUTPUT_ROOT / "performance_manifest.json"
BASELINE_JSON = OUTPUT_ROOT / "performance_baseline.json"
BASELINE_MD = OUTPUT_ROOT / "performance_baseline.md"
PROFILER_JSON = OUTPUT_ROOT / "profiler_summary.json"
PROFILER_MD = OUTPUT_ROOT / "profiler_summary.md"


def robust_statistics(values: Sequence[float]) -> dict[str, float]:
    if len(values) != 3:
        raise P0PerformanceError("each scored benchmark must contain exactly n=3")
    if any(not math.isfinite(value) or value <= 0.0 for value in values):
        raise P0PerformanceError(
            "scored performance values must be finite and positive"
        )
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mean = statistics.fmean(values)
    return {
        "n": 3,
        "minimum": min(values),
        "maximum": max(values),
        "median": median,
        "sample_standard_deviation": statistics.stdev(values),
        "median_absolute_deviation": statistics.median(deviations),
        "coefficient_of_variation": statistics.stdev(values) / mean,
    }


def _unique_report(root: Path, base: str) -> Path:
    matches = sorted(root.glob(f"{base}_*.csv"))
    if len(matches) != 1:
        raise P0PerformanceError(f"profile report is not unique: {base}")
    return matches[0]


def _recompute_monitor_audits(
    sample_root: Path, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    worker = manifest["worker"]
    recomputed: dict[str, Any] = {}
    for kind in ("gpu", "tctl", "proc", "pidstat"):
        recomputed[kind] = audit_monitor_records(
            parse_monitor_jsonl(sample_root / f"monitor_{kind}.jsonl"),
            kind=kind,
            target_pid=int(worker["pid"]),
            target_start_ticks=int(worker["process_start_ticks"]),
            target_start_ns=int(worker["published_monotonic_ns"]),
            target_end_ns=int(manifest["external_ended_monotonic_ns"]),
        )
        if recomputed[kind] != manifest["monitor_audit"][kind]:
            raise P0PerformanceError(
                f"monitor audit does not independently recompute: {manifest['sample_id']}:{kind}"
            )
    return recomputed


def _recompute_profile_audit(
    sample_root: Path, manifest: Mapping[str, Any]
) -> dict[str, Any] | None:
    mode = str(manifest["mode"])
    result = manifest["worker_result"]
    if mode == "host":
        semantic = audit_host_semantic_evidence(
            result["profile_instrumentation"]["semantic_ranges"],
            child_wall_ns=int(result["child_observed_wall_time_ns"]),
        )
        if semantic != result["profile_instrumentation"]["host_coverage"]:
            raise P0PerformanceError(
                f"worker host coverage changed: {manifest['sample_id']}"
            )
        pstats_report = parse_pstats(
            sample_root / "host_profile.pstats",
            logical_path=sample_root / "host_profile.pstats",
        )
        recomputed = {
            "gate_pass": semantic["gate_pass"]
            and int(pstats_report["total_calls"]) > 0,
            "semantic_coverage": semantic,
            "pstats": pstats_report,
        }
    elif mode == "cuda":
        stats = {name: _unique_report(sample_root, name) for name in REQUIRED_STATS}
        recomputed = audit_nsys_evidence(
            report_path=sample_root / "cuda_profile.nsys-rep",
            sqlite_path=sample_root / "cuda_profile.sqlite",
            stats_paths=stats,
            exact_mem_path=_unique_report(sample_root, "p0p_cuda_mem_exact"),
            target_pid=int(manifest["worker"]["pid"]),
            expected_pressure_transfers=result["pressure"][
                "structural_transfer_expectations"
            ],
            logical_root=sample_root,
        )
    elif mode == "memory":
        recomputed = audit_observer_payload(
            sample_root / "host_allocation_observer.json",
            expected_pid=int(manifest["worker"]["pid"]),
            logical_path=sample_root / "host_allocation_observer.json",
        )
    else:
        recomputed = None
    if recomputed != manifest.get("profile_audit"):
        raise P0PerformanceError(
            f"profile audit does not independently recompute: {manifest['sample_id']}"
        )
    return recomputed


def load_manifests() -> list[dict[str, Any]]:
    if not RAW_ROOT.is_dir() or RAW_ROOT.is_symlink():
        raise P0PerformanceError("performance raw root is unavailable")
    manifests: list[dict[str, Any]] = []
    control_names = {
        CLAIM_PATH.name,
        LEDGER_PATH.name,
        COMPLETE_PATH.name,
    }
    actual = {path.name for path in RAW_ROOT.iterdir()}
    expected_root = {*EXPANDED_EXECUTION_SCHEDULE, *control_names}
    if actual != expected_root:
        raise P0PerformanceError(
            "raw root differs from the frozen schedule plus completion controls"
        )
    claim = strict_json(CLAIM_PATH)
    validate_claim(claim)
    ledger = strict_json(LEDGER_PATH)
    validate_ledger(ledger)
    completion = strict_json(COMPLETE_PATH)
    if not (
        ledger.get("status") == "COMPLETE"
        and ledger.get("claim_sha256") == sha256_file(CLAIM_PATH)
        and set(completion)
        == {
            "schema_version",
            "status",
            "gate_pass",
            "authorization_child_commit",
            "completed_samples",
            "completed_monotonic_ns",
        }
        and completion.get("schema_version") == 1
        and completion.get("status")
        == "HF3D_P0P_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS"
        and completion.get("gate_pass") is True
        and completion.get("authorization_child_commit")
        == claim.get("authorization_child_commit")
        and completion.get("completed_samples") == list(EXPANDED_EXECUTION_SCHEDULE)
        and type(completion.get("completed_monotonic_ns")) is int
        and int(completion["completed_monotonic_ns"]) > 0
    ):
        raise P0PerformanceError("claim/ledger/completion controls are inconsistent")
    for sample_id in EXPANDED_EXECUTION_SCHEDULE:
        sample_root = RAW_ROOT / sample_id
        if not sample_root.is_dir() or sample_root.is_symlink():
            raise P0PerformanceError(f"invalid raw sample directory: {sample_id}")
        manifest = strict_json(sample_root / "sample_manifest.json")
        identity = sample_identity(sample_id)
        if not (
            manifest.get("gate_pass") is True
            and all(manifest.get(key) == value for key, value in identity.items())
        ):
            raise P0PerformanceError(f"sample manifest failed identity: {sample_id}")
        sequence_index = EXPANDED_EXECUTION_SCHEDULE.index(sample_id)
        ledger_entry = ledger["entries"][sequence_index]
        if not (
            ledger_entry["state"] == "PASS"
            and ledger_entry["manifest_sha256"]
            == sha256_file(sample_root / "sample_manifest.json")
        ):
            raise P0PerformanceError(
                f"sample manifest differs from the terminal ledger: {sample_id}"
            )
        if any(path.is_symlink() for path in sample_root.rglob("*")):
            raise P0PerformanceError(f"sample evidence contains a symlink: {sample_id}")
        observed_before_manifest = {
            path.relative_to(sample_root).as_posix(): {
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(sample_root.rglob("*"))
            if path.is_file() and path != sample_root / "sample_manifest.json"
        }
        if manifest.get("files_before_manifest") != observed_before_manifest:
            raise P0PerformanceError(
                f"sample raw files differ from the signed manifest: {sample_id}"
            )
        recomputed_numerical = archive_numerical_payload(sample_root / "archive")
        if (
            recomputed_numerical
            != manifest["worker_result"]["archive"]["canonical_numerical_payload"]
        ):
            raise P0PerformanceError(
                f"sample numerical payload does not independently recompute: {sample_id}"
            )
        _recompute_monitor_audits(sample_root, manifest)
        _recompute_profile_audit(sample_root, manifest)
        manifests.append(manifest)
    return manifests


def cross_mode_identity(manifests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_benchmark: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for manifest in manifests:
        by_benchmark[str(manifest["benchmark"])].append(manifest)
    result: dict[str, Any] = {}
    for benchmark, selected in sorted(by_benchmark.items()):
        hashes = [str(item["canonical_numerical_payload_sha256"]) for item in selected]
        result[benchmark] = {
            "sample_ids": [str(item["sample_id"]) for item in selected],
            "hashes": hashes,
            "gate_pass": len(set(hashes)) == 1,
        }
    return {
        "gate_pass": set(result) == {"B1", "B2", "B3"}
        and all(value["gate_pass"] for value in result.values()),
        "benchmarks": result,
    }


def baseline_summary(manifests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [item for item in manifests if item["disposition"] == "timed"]
    by_benchmark: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for manifest in scored:
        by_benchmark[str(manifest["benchmark"])].append(manifest)
    benchmarks: dict[str, Any] = {}
    for benchmark in ("B1", "B2", "B3"):
        selected = by_benchmark.get(benchmark, [])
        if len(selected) != 3:
            raise P0PerformanceError(f"{benchmark} scored sample count is not 3")
        wall = [float(item["external_end_to_end_wall_time_s"]) for item in selected]
        steps_per_second = [
            float(item["worker_result"]["outcome"]["accepted_steps"])
            / float(item["external_end_to_end_wall_time_s"])
            for item in selected
        ]
        benchmarks[benchmark] = {
            "sample_ids": [str(item["sample_id"]) for item in selected],
            "end_to_end_wall_time_s": robust_statistics(wall),
            "accepted_steps_per_end_to_end_second": robust_statistics(steps_per_second),
            "all_samples_retained_no_outlier_exclusion": True,
        }
    return {
        "schema_version": 1,
        "status": "HF3D_P0P_UNCHANGED_PRODUCTION_BASELINE_COMPLETE",
        "gate_pass": True,
        "benchmarks": benchmarks,
        "claim_ceiling": (
            "Unchanged production baseline only; no candidate speedup, P1 promotion, "
            "numerical qualification, physical validation, experimental validation, "
            "or apparatus prediction"
        ),
    }


def profiler_summary(manifests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    profiled = [item for item in manifests if item["disposition"] == "profiled"]
    expected = {
        f"PROFILED_{benchmark}_{mode}"
        for benchmark in ("B2", "B3")
        for mode in ("HOST", "CUDA", "MEMORY")
    }
    actual = {str(item["sample_id"]) for item in profiled}
    profile_gates = {
        str(item["sample_id"]): bool(item.get("profile_audit", {}).get("gate_pass"))
        for item in profiled
    }
    allocation_crosschecks: dict[str, Any] = {}
    for benchmark in ("B2", "B3"):
        cuda = next(
            item
            for item in profiled
            if item["benchmark"] == benchmark and item["mode"] == "cuda"
        )
        memory = next(
            item
            for item in profiled
            if item["benchmark"] == benchmark and item["mode"] == "memory"
        )
        recorder = memory["worker_result"]["device_memory"]["cupy_allocation_recorder"]
        nsys_counts = cuda["profile_audit"]["device_allocation_runtime_api_histogram"]
        nvidia_peak = memory["monitor_audit"]["gpu"]["derived"][
            "target_process_gpu_memory_peak_bytes"
        ]
        if not (
            isinstance(recorder, Mapping)
            and isinstance(nsys_counts, Mapping)
            and isinstance(nvidia_peak, int)
        ):
            raise P0PerformanceError(
                f"{benchmark} device-allocation triangulation inputs are incomplete"
            )
        allocation_crosschecks[benchmark] = device_allocation_crosscheck(
            recorder,
            nsys_allocation_api_counts={
                str(name): int(value) for name, value in nsys_counts.items()
            },
            nvidia_smi_peak_bytes=nvidia_peak,
        )
    allocation_gate = set(allocation_crosschecks) == {"B2", "B3"} and all(
        record["gate_pass"] for record in allocation_crosschecks.values()
    )
    return {
        "schema_version": 1,
        "status": "HF3D_P0P_PROFILE_ATTRIBUTION_COMPLETE",
        "gate_pass": actual == expected
        and all(profile_gates.values())
        and allocation_gate,
        "sample_set_exact": actual == expected,
        "profile_gates": profile_gates,
        "host": {
            str(item["sample_id"]): item["profile_audit"]
            for item in profiled
            if item["mode"] == "host"
        },
        "cuda": {
            str(item["sample_id"]): item["profile_audit"]
            for item in profiled
            if item["mode"] == "cuda"
        },
        "memory": {
            str(item["sample_id"]): item["profile_audit"]
            for item in profiled
            if item["mode"] == "memory"
        },
        "device_allocation_triangulation": allocation_crosschecks,
        "claim_ceiling": (
            "Operator-level host hotspots, CUDA kernel/API/memcpy, transfer, allocation, "
            "memory, utilization, and thermal attribution only; no native-symbol or "
            "microarchitectural claim"
        ),
    }


def raw_manifest(manifests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for manifest in manifests:
        sample_id = str(manifest["sample_id"])
        root = RAW_ROOT / sample_id
        files = {
            path.relative_to(RAW_ROOT).as_posix(): {
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }
        records[sample_id] = {
            "sample_manifest_sha256": sha256_file(root / "sample_manifest.json"),
            "file_count": len(files),
            "total_bytes": sum(item["size_bytes"] for item in files.values()),
            "files": files,
        }
    controls = {
        path.name: {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in (CLAIM_PATH, LEDGER_PATH, COMPLETE_PATH)
    }
    return {
        "schema_version": 1,
        "status": "HF3D_P0P_RAW_MANIFEST_COMPLETE",
        "gate_pass": len(records) == len(EXPANDED_EXECUTION_SCHEDULE)
        and len(controls) == 3,
        "schedule": list(EXPANDED_EXECUTION_SCHEDULE),
        "execution_controls": controls,
        "samples": records,
    }


def markdown_baseline(report: Mapping[str, Any]) -> str:
    lines = [
        "# HF3D P0-P 未修改生产路径基线",
        "",
        "状态：`HF3D_P0P_UNCHANGED_PRODUCTION_BASELINE_COMPLETE`",
        "",
        "| 基准 | n | 端到端中位数 / s | 最小 / s | 最大 / s | CV |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for benchmark, record in report["benchmarks"].items():
        wall = record["end_to_end_wall_time_s"]
        lines.append(
            f"| {benchmark} | {wall['n']} | {wall['median']:.9g} | "
            f"{wall['minimum']:.9g} | {wall['maximum']:.9g} | "
            f"{wall['coefficient_of_variation']:.6g} |"
        )
    lines.extend(
        [
            "",
            "所有三次 scored 样本均保留；没有异常值剔除、补样或替换。",
            "",
            f"结论上限：{report['claim_ceiling']}。",
            "",
        ]
    )
    return "\n".join(lines)


def markdown_profiler(report: Mapping[str, Any]) -> str:
    lines = [
        "# HF3D P0-P profiler 归因摘要",
        "",
        f"总体门：`{'PASS' if report['gate_pass'] else 'FAIL'}`。",
        "",
        "| 子运行 | 审计门 |",
        "|---|---:|",
    ]
    for sample_id, passed in sorted(report["profile_gates"].items()):
        lines.append(f"| {sample_id} | {'PASS' if passed else 'FAIL'} |")
    lines.extend(
        [
            "",
            "| 基准 | CuPy / nsys / nvidia-smi 分配三角核验 |",
            "|---|---:|",
        ]
    )
    for benchmark, record in sorted(report["device_allocation_triangulation"].items()):
        lines.append(f"| {benchmark} | {'PASS' if record['gate_pass'] else 'FAIL'} |")
    lines.extend(
        [
            "",
            "HOST、CUDA、MEMORY 为相互独立且不计分的进程，不能替代 scored 样本。",
            "",
            f"结论上限：{report['claim_ceiling']}。",
            "",
        ]
    )
    return "\n".join(lines)


def analyse() -> dict[str, Any]:
    manifests = load_manifests()
    identity = cross_mode_identity(manifests)
    baseline = baseline_summary(manifests)
    profiles = profiler_summary(manifests)
    raw = raw_manifest(manifests)
    gates = {
        "schedule_exact": [item["sample_id"] for item in manifests]
        == list(EXPANDED_EXECUTION_SCHEDULE),
        "cross_mode_numerical_identity": identity["gate_pass"],
        "baseline_statistics": baseline["gate_pass"],
        "profile_evidence": profiles["gate_pass"],
        "raw_manifest": raw["gate_pass"],
    }
    if not all(gates.values()):
        raise P0PerformanceError("P0-P compact evidence gates failed")
    atomic_json(MANIFEST_PATH, raw)
    atomic_json(BASELINE_JSON, {**baseline, "cross_mode_identity": identity})
    atomic_json(PROFILER_JSON, profiles)
    atomic_text(BASELINE_MD, markdown_baseline(baseline))
    atomic_text(PROFILER_MD, markdown_profiler(profiles))
    return {
        "status": "HF3D_P0P_COMPACT_EVIDENCE_PUBLISHED",
        "gate_pass": True,
        "gates": gates,
        "outputs": [
            str(path)
            for path in (
                MANIFEST_PATH,
                BASELINE_JSON,
                BASELINE_MD,
                PROFILER_JSON,
                PROFILER_MD,
            )
        ],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-analysis", action="store_true", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    parse_args(argv)
    try:
        report = analyse()
    except Exception as exc:  # noqa: BLE001 - analysis must fail closed at its CLI boundary.
        print(f"P0-P analysis failed: {type(exc).__name__}: {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
