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

from p0p2v2_allocation import audit_observer_payload, device_allocation_crosscheck
from p0p2v2_consumption import (
    CLAIM_PATH,
    COMPLETE_PATH,
    LEDGER_PATH,
    validate_claim,
    validate_ledger,
)
from p0p2v2_contract import (
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
from p0p2v2_instrumentation import audit_host_semantic_evidence, parse_pstats
from p0p2v2_nsys import REQUIRED_STATS, audit_nsys_evidence
from p0p2v2_numerical import archive_numerical_payload, compare_payloads
from p0p2v2_telemetry import audit_monitor_records, parse_monitor_jsonl
from p0p2v2_transaction import DirectoryPublicationTransaction

OUTPUT_PARENT = HERE / "artifacts" / "p0p2v2"
OUTPUT_ROOT = OUTPUT_PARENT / "compact_evidence"
MANIFEST_NAME = "performance_manifest.json"
BASELINE_JSON_NAME = "performance_baseline.json"
BASELINE_MD_NAME = "performance_baseline.md"
PROFILER_JSON_NAME = "profiler_summary.json"
PROFILER_MD_NAME = "profiler_summary.md"
TERMINAL_NAME = "publication_complete.json"


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
            exact_mem_path=_unique_report(sample_root, "p0p2v2_cuda_mem_exact"),
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
        and completion.get("schema_version") == 2
        and completion.get("status")
        == "HF3D_P0P2_V2_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS"
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
        recomputed_numerical = archive_numerical_payload(
            sample_root / "archive",
            execution_sequence=manifest["worker_result"]["outcome"][
                "trial_sequence"
            ],
        )
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
        payloads = [
            item["worker_result"]["archive"]["canonical_numerical_payload"]
            for item in selected
        ]
        comparison = compare_payloads(payloads)
        result[benchmark] = {
            "sample_ids": [str(item["sample_id"]) for item in selected],
            "comparison": comparison,
            "gate_pass": comparison["gate_pass"],
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
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_UNCHANGED_PRODUCTION_BASELINE_COMPLETE",
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
        f"P0P2V2_PROFILED_{benchmark}_{mode}"
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
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_PROFILE_ATTRIBUTION_COMPLETE",
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


def _distribution(values: Sequence[float]) -> dict[str, float | int]:
    if not values or any(not math.isfinite(value) for value in values):
        raise P0PerformanceError("utilization distribution is empty or non-finite")
    ordered = sorted(values)
    p90_index = max(0, math.ceil(0.90 * len(ordered)) - 1)
    return {
        "n": len(ordered),
        "minimum": ordered[0],
        "maximum": ordered[-1],
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p90": ordered[p90_index],
    }


def resource_summary(manifests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Recompute CPU/GPU utilization and archive/solver costs from raw evidence."""

    samples: dict[str, Any] = {}
    for manifest in manifests:
        sample_id = str(manifest["sample_id"])
        sample_root = RAW_ROOT / sample_id
        gpu_records = parse_monitor_jsonl(sample_root / "monitor_gpu.jsonl")
        gpu_utilization: list[float] = []
        memory_utilization: list[float] = []
        sm_clock_mhz: list[float] = []
        power_w: list[float] = []
        for record in gpu_records:
            if record.get("record_type") != "sample":
                continue
            value = record.get("value")
            gpu = value.get("gpu") if isinstance(value, Mapping) else None
            if not isinstance(gpu, Mapping):
                continue
            try:
                gpu_utilization.append(float(gpu["utilization.gpu"]))
                memory_utilization.append(float(gpu["utilization.memory"]))
                sm_clock_mhz.append(float(gpu["clocks.current.sm"]))
                power_w.append(float(gpu["power.draw"]))
            except (KeyError, TypeError, ValueError):
                continue
        result = manifest["worker_result"]
        wall_s = float(manifest["external_end_to_end_wall_time_s"])
        cpu_s = float(result["child_process_cpu_time_s"])
        phases = result["phase_wall_time_s"]
        if wall_s <= 0.0 or not gpu_utilization:
            raise P0PerformanceError(
                f"resource evidence is incomplete for {sample_id}"
            )
        samples[sample_id] = {
            "benchmark": manifest["benchmark"],
            "disposition": manifest["disposition"],
            "mode": manifest["mode"],
            "external_end_to_end_wall_time_s": wall_s,
            "child_process_cpu_time_s": cpu_s,
            "average_cpu_percent_one_logical_cpu_basis": 100.0 * cpu_s / wall_s,
            "average_logical_cpu_cores": cpu_s / wall_s,
            "gpu_compute_utilization_percent": _distribution(gpu_utilization),
            "gpu_memory_utilization_percent": _distribution(memory_utilization),
            "gpu_sm_clock_mhz": _distribution(sm_clock_mhz),
            "gpu_power_w": _distribution(power_w),
            "gpu_memory_peak_bytes": manifest["monitor_audit"]["gpu"]["derived"][
                "target_process_gpu_memory_peak_bytes"
            ],
            "process_vm_hwm_peak_bytes": manifest["monitor_audit"]["proc"][
                "derived"
            ]["process_vm_hwm_peak_bytes"],
            "archive_write_s": float(phases.get("archive_write", 0.0)),
            "strict_archive_reload_s": float(
                phases.get("strict_archive_reload", 0.0)
            ),
            "pressure_solve_semantic_time_s": float(
                result["pressure"]["total_semantic_inclusive_time_s"]
            ),
            "accepted_steps": int(result["outcome"]["accepted_steps"]),
        }
    scored = [
        record for record in samples.values() if record["disposition"] == "timed"
    ]
    if len(samples) != 18 or len(scored) != 9:
        raise P0PerformanceError("resource summary sample cardinality differs from 18/9")
    return {
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_RESOURCE_UTILIZATION_AND_COST_SUMMARY_COMPLETE",
        "gate_pass": True,
        "samples": samples,
        "scored_aggregate": {
            "average_cpu_percent_one_logical_cpu_basis": _distribution(
                [
                    float(record["average_cpu_percent_one_logical_cpu_basis"])
                    for record in scored
                ]
            ),
            "gpu_compute_utilization_median_percent": _distribution(
                [
                    float(record["gpu_compute_utilization_percent"]["median"])
                    for record in scored
                ]
            ),
            "gpu_compute_utilization_p90_percent": _distribution(
                [
                    float(record["gpu_compute_utilization_percent"]["p90"])
                    for record in scored
                ]
            ),
            "archive_write_s": _distribution(
                [float(record["archive_write_s"]) for record in scored]
            ),
            "strict_archive_reload_s": _distribution(
                [float(record["strict_archive_reload_s"]) for record in scored]
            ),
        },
        "claim_ceiling": (
            "Measured baseline resource utilization and attribution only; thread "
            "settings or utilization alone do not establish acceleration"
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
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_RAW_MANIFEST_COMPLETE",
        "gate_pass": len(records) == len(EXPANDED_EXECUTION_SCHEDULE)
        and len(controls) == 3,
        "schedule": list(EXPANDED_EXECUTION_SCHEDULE),
        "execution_controls": controls,
        "samples": records,
    }


def markdown_baseline(
    report: Mapping[str, Any], resources: Mapping[str, Any]
) -> str:
    aggregate = resources["scored_aggregate"]
    lines = [
        "# HF3D P0-P 未修改生产路径基线",
        "",
        "状态：`HF3D_P0P2_V2_UNCHANGED_PRODUCTION_BASELINE_COMPLETE`",
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
            "CPU、GPU 利用率以及 archive 写入和严格重载开销已经从原始遥测独立汇总，机器可读明细位于 performance_baseline.json。",
            "",
            (
                "计分样本的跨样本 CPU 占用中位数（单逻辑核 100% 口径）为 "
                f"{aggregate['average_cpu_percent_one_logical_cpu_basis']['median']:.6g}%；"
                "GPU 计算利用率样本中位数的跨样本中位数为 "
                f"{aggregate['gpu_compute_utilization_median_percent']['median']:.6g}%。"
            ),
            "",
            "这些利用率只描述基线瓶颈，不能单独证明任何加速。",
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
    resources = resource_summary(manifests)
    raw = raw_manifest(manifests)
    gates = {
        "schedule_exact": [item["sample_id"] for item in manifests]
        == list(EXPANDED_EXECUTION_SCHEDULE),
        "cross_mode_numerical_identity": identity["gate_pass"],
        "baseline_statistics": baseline["gate_pass"],
        "profile_evidence": profiles["gate_pass"],
        "resource_utilization_and_costs": resources["gate_pass"],
        "raw_manifest": raw["gate_pass"],
    }
    if not all(gates.values()):
        raise P0PerformanceError("P0-P compact evidence gates failed")
    transaction = DirectoryPublicationTransaction(OUTPUT_PARENT, OUTPUT_ROOT.name)
    staging = transaction.begin()
    atomic_json(staging / MANIFEST_NAME, raw)
    atomic_json(
        staging / BASELINE_JSON_NAME,
        {
            **baseline,
            "cross_mode_identity": identity,
            "resource_utilization_and_costs": resources,
        },
    )
    atomic_json(staging / PROFILER_JSON_NAME, profiles)
    atomic_text(staging / BASELINE_MD_NAME, markdown_baseline(baseline, resources))
    atomic_text(staging / PROFILER_MD_NAME, markdown_profiler(profiles))
    for name in (MANIFEST_NAME, BASELINE_JSON_NAME, PROFILER_JSON_NAME):
        strict_json(staging / name)
    files_before_terminal = {
        path.name: {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(staging.iterdir())
        if path.is_file() and not path.is_symlink()
    }
    if set(files_before_terminal) != {
        MANIFEST_NAME,
        BASELINE_JSON_NAME,
        BASELINE_MD_NAME,
        PROFILER_JSON_NAME,
        PROFILER_MD_NAME,
    }:
        raise P0PerformanceError("compact evidence staging file set is incomplete")
    terminal = {
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_COMPACT_EVIDENCE_PUBLISHED",
        "gate_pass": True,
        "gates": gates,
        "files_before_terminal": files_before_terminal,
        "terminal_record_written_last": True,
    }
    published = transaction.publish_terminal(TERMINAL_NAME, terminal)
    reloaded_terminal = strict_json(published / TERMINAL_NAME)
    observed = {
        name: {
            "sha256": sha256_file(published / name),
            "size_bytes": (published / name).stat().st_size,
        }
        for name in files_before_terminal
    }
    if reloaded_terminal != terminal or observed != files_before_terminal:
        raise P0PerformanceError("published compact evidence failed hash/size reload")
    return {
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_COMPACT_EVIDENCE_PUBLISHED",
        "gate_pass": True,
        "gates": gates,
        "output_root": str(published),
        "outputs": sorted(path.name for path in published.iterdir()),
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
