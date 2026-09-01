"""Independently inspect P0-Lite attempts and write compact baseline reports."""

from __future__ import annotations

import argparse
import math
import pstats
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from p0lite_common import (
    REPORTS_ROOT,
    RUNS_ROOT,
    P0LiteError,
    compare_payloads,
    load_schedule,
    strict_json,
    utc_now,
    validate_success_artifacts,
    write_json,
)


def inspect_attempt(attempt: Path) -> dict[str, Any]:
    if not attempt.is_dir() or attempt.is_symlink():
        raise P0LiteError(f"attempt is not a real directory: {attempt}")
    status_payload = strict_json(attempt / "status.json")
    manifest = strict_json(attempt / "run_manifest.json")
    if not isinstance(status_payload, dict) or not isinstance(manifest, dict):
        raise P0LiteError("attempt status or manifest is not an object")
    stored_status = status_payload.get("status")
    effective_status = "INTERRUPTED" if stored_status == "RUNNING" else stored_status
    if effective_status not in {
        "SUCCESS",
        "FAILED",
        "INTERRUPTED",
        "INVALID_ENVIRONMENT",
    }:
        raise P0LiteError(f"invalid attempt status: {stored_status}")
    record: dict[str, Any] = {
        "attempt": str(attempt),
        "attempt_id": attempt.name,
        "stored_status": stored_status,
        "effective_status": effective_status,
        "reason": status_payload.get("reason"),
        "slot": manifest.get("slot"),
        "environment_id": manifest.get("environment_id"),
        "execution": manifest.get("execution"),
        "valid_success": False,
    }
    if effective_status == "SUCCESS":
        validated = validate_success_artifacts(attempt)
        record["valid_success"] = True
        record["payload"] = validated["payload"]
        record["quality"] = validated["quality"]
    return record


def scan_attempts(runs_root: Path = RUNS_ROOT) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not runs_root.exists():
        return records
    if not runs_root.is_dir() or runs_root.is_symlink():
        raise P0LiteError("runs root is not a real directory")
    for attempt in sorted(runs_root.glob("*/*")):
        records.append(inspect_attempt(attempt))
    return records


def _selected_by_slot(records: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    selected: dict[str, Mapping[str, Any]] = {}
    for record in records:
        slot = record.get("slot")
        if not isinstance(slot, Mapping) or not isinstance(slot.get("slot_id"), str):
            raise P0LiteError("attempt does not identify its schedule slot")
        if record.get("valid_success") is True:
            slot_id = str(slot["slot_id"])
            if slot_id in selected:
                raise P0LiteError(f"multiple SUCCESS attempts exist for {slot_id}")
            selected[slot_id] = record
    return selected


def _geometric_median(values: Sequence[float]) -> float:
    if not values or any(value <= 0.0 or not math.isfinite(value) for value in values):
        raise P0LiteError("timings must be finite and positive")
    return math.exp(statistics.median([math.log(value) for value in values]))


def baseline_report(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    schedule = load_schedule()
    selected = _selected_by_slot(records)
    expected_slots = [str(slot["slot_id"]) for slot in schedule["slots"]]
    by_benchmark: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in selected.values():
        slot = record["slot"]
        by_benchmark[str(slot["benchmark"])].append(record)
    consistency = {
        benchmark: compare_payloads([record["payload"] for record in group])
        for benchmark, group in by_benchmark.items()
    }
    timed: dict[str, list[float]] = defaultdict(list)
    for record in selected.values():
        slot = record["slot"]
        execution = record.get("execution")
        if slot["kind"] == "TIMED" and isinstance(execution, Mapping):
            timed[str(slot["benchmark"])].append(float(execution["end_to_end_wall_s"]))
    timing_summary = {
        benchmark: {
            "samples_s": values,
            "median_s": statistics.median(values),
            "geometric_median_s": _geometric_median(values),
        }
        for benchmark, values in sorted(timed.items())
    }
    gates = {
        "all_16_slots_have_one_valid_success": set(selected) == set(expected_slots),
        "three_timed_samples_per_benchmark": all(len(timed.get(name, [])) == 3 for name in ("B1", "B2", "B3")),
        "all_benchmark_payloads_bitwise_identical": set(consistency) == {"B1", "B2", "B3"}
        and all(item["gate_pass"] for item in consistency.values()),
    }
    return {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "status": "P0LITE_REFERENCE_BASELINE_PASS" if all(gates.values()) else "P0LITE_REFERENCE_BASELINE_INCOMPLETE_OR_FAIL",
        "gate_pass": all(gates.values()),
        "gates": gates,
        "attempts": list(records),
        "selected_success_attempts": selected,
        "numerical_consistency": consistency,
        "timing": timing_summary,
        "environment_ids": sorted(
            {str(record["environment_id"]) for record in selected.values()}
        ),
    }


def _host_profile(path: Path, limit: int = 30) -> list[dict[str, Any]]:
    try:
        stats = pstats.Stats(str(path))
    except (OSError, EOFError, ValueError, TypeError) as exc:
        raise P0LiteError(f"cannot read cProfile evidence {path}: {exc}") from exc
    rows = []
    raw_stats: Any = stats.stats  # type: ignore[attr-defined]
    for (filename, line, function), values in raw_stats.items():
        primitive, calls, total, cumulative, _ = values
        rows.append(
            {
                "file": filename,
                "line": line,
                "function": function,
                "primitive_calls": primitive,
                "calls": calls,
                "total_s": total,
                "cumulative_s": cumulative,
            }
        )
    return sorted(rows, key=lambda item: item["cumulative_s"], reverse=True)[:limit]


def profile_report(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    selected = _selected_by_slot(records)
    profiles: dict[str, Any] = {}
    for slot_id in ("PROFILE_HOST_B2", "PROFILE_CUDA_B2", "PROFILE_HOST_B3", "PROFILE_CUDA_B3"):
        record = selected.get(slot_id)
        if record is None:
            continue
        attempt = Path(str(record["attempt"]))
        timing = strict_json(attempt / "worker_timing.json")
        execution = record.get("execution")
        wall = float(execution["end_to_end_wall_s"]) if isinstance(execution, Mapping) else None
        item: dict[str, Any] = {"attempt": str(attempt), "worker_timing": timing, "end_to_end_wall_s": wall}
        if "HOST" in slot_id:
            item["cprofile_top_cumulative"] = _host_profile(attempt / "host_profile.pstats")
        else:
            item["nsys"] = strict_json(attempt / "nsys_summary.json")
        profiles[slot_id] = item
    gates = {
        "all_four_profiles_available": len(profiles) == 4,
        "all_profile_payloads_in_baseline_consistency_gate": all(
            record.get("valid_success") is True
            for slot_id, record in selected.items()
            if slot_id.startswith("PROFILE_")
        )
        and len([slot_id for slot_id in selected if slot_id.startswith("PROFILE_")]) == 4,
        "host_profile_rows_available": all(
            profiles.get(slot_id, {}).get("cprofile_top_cumulative")
            for slot_id in ("PROFILE_HOST_B2", "PROFILE_HOST_B3")
        ),
        "cuda_kernel_and_memcpy_summaries_available": all(
            profiles.get(slot_id, {}).get("nsys", {}).get("kernels")
            and profiles.get(slot_id, {}).get("nsys", {}).get("memory")
            for slot_id in ("PROFILE_CUDA_B2", "PROFILE_CUDA_B3")
        ),
    }
    return {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "status": "P0LITE_PROFILE_PASS" if all(gates.values()) else "P0LITE_PROFILE_INCOMPLETE_OR_FAIL",
        "gate_pass": all(gates.values()),
        "gates": gates,
        "profiles": profiles,
    }


def _baseline_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# P0-Lite reference baseline",
        "",
        f"Status: `{report['status']}`.",
        "",
        "| Benchmark | Timed samples (s) | Median (s) |",
        "|---|---:|---:|",
    ]
    for benchmark, item in report["timing"].items():
        samples = ", ".join(f"{value:.6f}" for value in item["samples_s"])
        lines.append(f"| {benchmark} | {samples} | {item['median_s']:.6f} |")
    lines.extend(["", f"All recorded attempts: {len(report['attempts'])}.", ""])
    return "\n".join(lines)


def _profile_markdown(report: Mapping[str, Any]) -> str:
    lines = ["# P0-Lite profile", "", f"Status: `{report['status']}`.", ""]
    for slot_id, item in report["profiles"].items():
        lines.extend([f"## {slot_id}", "", f"End-to-end wall time: `{item['end_to_end_wall_s']:.6f} s`.", ""])
    return "\n".join(lines)


def write_reports(runs_root: Path = RUNS_ROOT, reports_root: Path = REPORTS_ROOT) -> tuple[dict[str, Any], dict[str, Any]]:
    records = scan_attempts(runs_root)
    baseline = baseline_report(records)
    profile = profile_report(records)
    reports_root.mkdir(parents=True, exist_ok=True)
    write_json(reports_root / "p0lite_baseline.json", baseline)
    write_json(reports_root / "p0lite_profile.json", profile)
    (reports_root / "p0lite_baseline.md").write_text(_baseline_markdown(baseline), encoding="utf-8")
    (reports_root / "p0lite_profile.md").write_text(_profile_markdown(profile), encoding="utf-8")
    return baseline, profile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    parser.add_argument("--reports-root", type=Path, default=REPORTS_ROOT)
    parser.add_argument("--write-reports", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        records = scan_attempts(args.runs_root.resolve())
        baseline = baseline_report(records)
        profile = profile_report(records)
        if args.write_reports:
            write_reports(args.runs_root.resolve(), args.reports_root.resolve())
        print(f"{baseline['status']} / {profile['status']}")
        return 0 if baseline["gate_pass"] and profile["gate_pass"] else 2
    except P0LiteError as exc:
        print(f"P0-Lite analysis failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
