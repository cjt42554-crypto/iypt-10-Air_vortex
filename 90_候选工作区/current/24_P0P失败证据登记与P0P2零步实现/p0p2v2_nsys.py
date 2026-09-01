"""Nsight Systems launcher specification and version-introspective auditor."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from p0p2v2_contract import (
    HERE,
    NSYS_PATH,
    P0PerformanceError,
    atomic_text,
    sha256_file,
)

NSYS_VERSION = "2026.2.1"
NSYS_TRACE = "cuda,nvtx"
REQUIRED_STATS = (
    "cuda_api_sum",
    "cuda_gpu_kern_sum",
    "cuda_gpu_mem_time_sum",
    "cuda_gpu_mem_size_sum",
)
EXACT_MEM_REPORT = HERE / "p0p2v2_cuda_mem_exact.py"
REQUIRED_TABLES = (
    "CUPTI_ACTIVITY_KIND_KERNEL",
    "CUPTI_ACTIVITY_KIND_MEMCPY",
    "CUPTI_ACTIVITY_KIND_RUNTIME",
    "ENUM_CUDA_MEMCPY_OPER",
    "NVTX_EVENTS",
    "PROCESSES",
    "StringIds",
    "TARGET_INFO_CUDA_CONTEXT_INFO",
    "TARGET_INFO_CUDA_DEVICE",
)


def profile_command(
    *,
    output_base: Path,
    target_command: Sequence[str],
) -> list[str]:
    return [
        str(NSYS_PATH),
        "profile",
        "--trace=cuda,nvtx",
        "--sample=none",
        "--cpuctxsw=none",
        "--force-overwrite=true",
        f"--output={output_base}",
        *target_command,
    ]


def export_commands(
    *,
    report_path: Path,
    sqlite_path: Path,
    report_output_bases: Mapping[str, Path],
) -> list[list[str]]:
    commands = [
        [
            str(NSYS_PATH),
            "export",
            "--type=sqlite",
            "--force-overwrite=true",
            f"--output={sqlite_path}",
            str(report_path),
        ]
    ]
    for report in REQUIRED_STATS:
        commands.append(
            [
                str(NSYS_PATH),
                "stats",
                f"--report={report}",
                "--format=csv",
                "--force-export=true",
                f"--output={report_output_bases[report]}",
                str(report_path),
            ]
        )
    commands.append(
        [
            str(NSYS_PATH),
            "stats",
            f"--report={EXACT_MEM_REPORT}",
            "--format=csv",
            "--force-export=true",
            f"--output={report_output_bases['p0p2v2_cuda_mem_exact']}",
            str(report_path),
        ]
    )
    return commands


def _regular_nonempty(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.stat().st_size > 0


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    escaped = table.replace('"', '""')
    return [
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    ]


def sqlite_schema_manifest(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT name, type, sql FROM sqlite_master "
        "WHERE type IN ('table', 'view', 'index', 'trigger') "
        "ORDER BY type, name"
    ).fetchall()
    objects: list[dict[str, Any]] = []
    for name, object_type, sql in rows:
        record: dict[str, Any] = {
            "name": str(name),
            "type": str(object_type),
            "sql": str(sql) if sql is not None else None,
        }
        if object_type in {"table", "view"}:
            record["columns"] = _table_columns(connection, str(name))
        objects.append(record)
    return {"sqlite_objects": objects}


def _require_columns(
    connection: sqlite3.Connection,
    table: str,
    expected: Sequence[str],
) -> None:
    actual = set(_table_columns(connection, table))
    missing = sorted(set(expected) - actual)
    if missing:
        raise P0PerformanceError(
            f"Nsight table {table} lacks columns: {', '.join(missing)}"
        )


def _target_global_pid(connection: sqlite3.Connection, target_pid: int) -> int:
    rows = connection.execute(
        "SELECT globalPid FROM PROCESSES WHERE pid = ?", (target_pid,)
    ).fetchall()
    if len(rows) != 1:
        raise P0PerformanceError("target PID is not unique in Nsight PROCESSES")
    return int(rows[0][0])


def _string_map(connection: sqlite3.Connection) -> dict[int, str]:
    return {
        int(identifier): str(value)
        for identifier, value in connection.execute(
            "SELECT id, value FROM StringIds"
        ).fetchall()
    }


def _global_tid_process(global_tid: int) -> int:
    """Return Nsight's encoded global-process identifier for one global TID."""

    return global_tid & ~((1 << 24) - 1)


def _nvtx_rows(
    connection: sqlite3.Connection, *, target_global_pid: int
) -> list[dict[str, Any]]:
    strings = _string_map(connection)
    rows: list[dict[str, Any]] = []
    for start, end, text, text_id, global_tid in connection.execute(
        "SELECT start, end, text, textId, globalTid FROM NVTX_EVENTS"
    ).fetchall():
        resolved = text if text is not None else strings.get(int(text_id or -1))
        if resolved is None or global_tid is None:
            continue
        encoded_tid = int(global_tid)
        if _global_tid_process(encoded_tid) != target_global_pid:
            continue
        rows.append(
            {
                "start_ns": int(start),
                "end_ns": int(end) if end is not None else None,
                "text": str(resolved),
                "global_tid": encoded_tid,
            }
        )
    return rows


def _activity_rows(
    connection: sqlite3.Connection,
    *,
    target_global_pid: int,
) -> dict[str, Any]:
    strings = _string_map(connection)
    kernels = [
        {
            "start_ns": int(start),
            "end_ns": int(end),
            "device_id": int(device),
            "context_id": int(context),
            "global_pid": int(global_pid),
            "name": strings.get(int(name_id), f"StringIds:{name_id}"),
        }
        for start, end, device, context, global_pid, name_id in connection.execute(
            "SELECT start, end, deviceId, contextId, globalPid, demangledName "
            "FROM CUPTI_ACTIVITY_KIND_KERNEL WHERE globalPid = ? ORDER BY start",
            (target_global_pid,),
        ).fetchall()
    ]
    directions = {
        int(identifier): str(label)
        for identifier, label in connection.execute(
            "SELECT id, label FROM ENUM_CUDA_MEMCPY_OPER"
        ).fetchall()
    }
    memcopies = [
        {
            "start_ns": int(start),
            "end_ns": int(end),
            "duration_ns": int(end) - int(start),
            "device_id": int(device),
            "context_id": int(context),
            "global_pid": int(global_pid),
            "bytes": int(byte_count),
            "copy_kind": int(copy_kind),
            "direction": directions.get(int(copy_kind), f"UNKNOWN:{copy_kind}"),
        }
        for start, end, device, context, global_pid, byte_count, copy_kind in connection.execute(
            "SELECT start, end, deviceId, contextId, globalPid, bytes, copyKind "
            "FROM CUPTI_ACTIVITY_KIND_MEMCPY WHERE globalPid = ? ORDER BY start",
            (target_global_pid,),
        ).fetchall()
    ]
    runtimes = [
        {
            "start_ns": int(start),
            "end_ns": int(end),
            "global_tid": int(global_tid),
            "correlation_id": int(correlation) if correlation is not None else None,
            "name": strings.get(int(name_id), f"StringIds:{name_id}"),
            "return_value": int(return_value),
        }
        for start, end, global_tid, correlation, name_id, return_value in connection.execute(
            "SELECT start, end, globalTid, correlationId, nameId, returnValue "
            "FROM CUPTI_ACTIVITY_KIND_RUNTIME ORDER BY start"
        ).fetchall()
        if _global_tid_process(int(global_tid)) == target_global_pid
    ]
    return {"kernels": kernels, "memcopies": memcopies, "runtime_apis": runtimes}


def _csv_rows(path: Path) -> list[dict[str, str]]:
    if not _regular_nonempty(path):
        raise P0PerformanceError(f"Nsight stats report is absent or empty: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise P0PerformanceError(f"Nsight stats report has no rows: {path}")
    return [{str(key): str(value) for key, value in row.items()} for row in rows]


def _integer_sum(rows: Sequence[Mapping[str, str]], column: str) -> int:
    try:
        return sum(int(row[column]) for row in rows)
    except (KeyError, ValueError) as exc:
        raise P0PerformanceError(f"Nsight stats lacks integer column {column}") from exc


def _direction_summary(memcopies: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for direction in sorted({str(row["direction"]) for row in memcopies}):
        selected = [row for row in memcopies if row["direction"] == direction]
        sizes = Counter(int(row["bytes"]) for row in selected)
        output[direction] = {
            "count": len(selected),
            "bytes": sum(int(row["bytes"]) for row in selected),
            "time_ns": sum(int(row["duration_ns"]) for row in selected),
            "size_histogram": {
                str(size): count for size, count in sorted(sizes.items())
            },
        }
    return output


def _associate_pressure_ranges(
    memcopies: Sequence[Mapping[str, Any]],
    nvtx: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    pressure = [
        row
        for row in nvtx
        if str(row["text"]).startswith("pressure.project|call_id=")
        and row["end_ns"] is not None
    ]
    output: list[dict[str, Any]] = []
    for item in pressure:
        start = int(item["start_ns"])
        end = int(item["end_ns"])
        selected = [
            row
            for row in memcopies
            if int(row["start_ns"]) >= start and int(row["end_ns"]) <= end
        ]
        output.append(
            {
                "range": dict(item),
                "transfer_count": len(selected),
                "direction_summary": _direction_summary(selected),
            }
        )
    return output


def _expected_multiset_gate(
    associations: Sequence[Mapping[str, Any]],
    expected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    observed_by_call: dict[int, dict[str, Counter[int]]] = {}
    duplicate_call_ids: set[int] = set()
    for association in associations:
        text = str(association["range"]["text"])
        call_id = int(text.rsplit("=", 1)[1])
        direction_summary = association["direction_summary"]
        if call_id in observed_by_call:
            duplicate_call_ids.add(call_id)
        observed_by_call[call_id] = {
            direction: Counter(
                {
                    int(size): int(count)
                    for size, count in details["size_histogram"].items()
                }
            )
            for direction, details in direction_summary.items()
        }
    call_gates: list[dict[str, Any]] = []
    expected_call_ids: list[int] = []
    for record in expected:
        call_id = int(record["call_id"])
        expected_call_ids.append(call_id)
        observed = observed_by_call.get(call_id, {})
        h2d = observed.get("Host-to-Device", Counter())
        d2h = observed.get("Device-to-Host", Counter())
        expected_h2d = Counter(
            int(value) for value in record["expected_h2d_size_multiset"]
        )
        expected_d2h = Counter(
            int(value) for value in record["expected_d2h_size_multiset"]
        )
        h2d_pass = h2d == expected_h2d
        d2h_pass = d2h == expected_d2h
        call_gates.append(
            {
                "call_id": call_id,
                "gate_pass": h2d_pass and d2h_pass,
                "h2d_expected": dict(expected_h2d),
                "h2d_observed": dict(h2d),
                "d2h_expected": dict(expected_d2h),
                "d2h_observed": dict(d2h),
            }
        )
    return {
        "gate_pass": bool(call_gates)
        and not duplicate_call_ids
        and len(expected_call_ids) == len(set(expected_call_ids))
        and set(observed_by_call) == set(expected_call_ids)
        and all(row["gate_pass"] for row in call_gates),
        "expected_call_ids": expected_call_ids,
        "observed_call_ids": sorted(observed_by_call),
        "duplicate_observed_call_ids": sorted(duplicate_call_ids),
        "calls": call_gates,
    }


def audit_nsys_evidence(
    *,
    report_path: Path,
    sqlite_path: Path,
    stats_paths: Mapping[str, Path],
    exact_mem_path: Path,
    target_pid: int,
    expected_pressure_transfers: Sequence[Mapping[str, Any]],
    require_h2d_and_d2h: bool = True,
    logical_root: Path | None = None,
) -> dict[str, Any]:
    """Audit trace completeness using the actual exported SQLite schema."""

    files = {
        "nsys_report": report_path,
        "sqlite": sqlite_path,
        **{f"stats_{name}": stats_paths[name] for name in REQUIRED_STATS},
        "stats_p0p2v2_cuda_mem_exact": exact_mem_path,
    }
    file_gates = {name: _regular_nonempty(path) for name, path in files.items()}
    if not all(file_gates.values()):
        failed = [name for name, passed in file_gates.items() if not passed]
        raise P0PerformanceError(
            "Nsight output files missing/empty: " + ", ".join(failed)
        )
    connection = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise P0PerformanceError("Nsight SQLite integrity_check is not ok")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing_tables = sorted(set(REQUIRED_TABLES) - tables)
        if missing_tables:
            raise P0PerformanceError(
                "Nsight SQLite lacks required tables: " + ", ".join(missing_tables)
            )
        _require_columns(
            connection,
            "CUPTI_ACTIVITY_KIND_MEMCPY",
            ("start", "end", "contextId", "globalPid", "bytes", "copyKind"),
        )
        _require_columns(
            connection,
            "NVTX_EVENTS",
            ("start", "end", "text", "textId", "globalTid"),
        )
        schema = sqlite_schema_manifest(connection)
        global_pid = _target_global_pid(connection, target_pid)
        context_rows = connection.execute(
            "SELECT contextId, deviceId FROM TARGET_INFO_CUDA_CONTEXT_INFO "
            "WHERE processId = ?",
            (target_pid,),
        ).fetchall()
        if not context_rows:
            raise P0PerformanceError("target PID has no CUDA context in trace")
        contexts = {(int(context), int(device)) for context, device in context_rows}
        activities = _activity_rows(connection, target_global_pid=global_pid)
        nvtx = _nvtx_rows(connection, target_global_pid=global_pid)
    finally:
        connection.close()

    process_ranges = [
        row
        for row in nvtx
        if str(row["text"]).startswith("P0P2V2_PROCESS_START|call_id=")
    ]
    final_ranges = [
        row
        for row in nvtx
        if str(row["text"]).startswith("P0P2V2_FINAL_SYNC_END|call_id=")
    ]
    allocation_ranges = [
        row
        for row in nvtx
        if str(row["text"]).startswith("P0P2V2_DEVICE_ALLOCATION_SCOPE|call_id=")
    ]
    if len(process_ranges) != 1 or len(final_ranges) != 1:
        raise P0PerformanceError("Nsight trace lacks unique process/final NVTX ranges")
    if len(allocation_ranges) != 1 or allocation_ranges[0]["end_ns"] is None:
        raise P0PerformanceError(
            "Nsight trace lacks a unique complete device-allocation scope"
        )
    process_range = process_ranges[0]
    final_range = final_ranges[0]
    allocation_range = allocation_ranges[0]
    range_complete = (
        process_range["end_ns"] is not None
        and final_range["end_ns"] is not None
        and int(process_range["start_ns"]) <= int(final_range["start_ns"])
        and int(final_range["end_ns"]) <= int(process_range["end_ns"])
    )
    kernels = activities["kernels"]
    memcopies = activities["memcopies"]
    runtime_apis = activities["runtime_apis"]
    process_start_ns = int(process_range["start_ns"])
    process_end_ns = int(process_range["end_ns"] or -1)
    final_end_ns = int(final_range["end_ns"] or -1)
    gpu_activities = [*kernels, *memcopies]
    activity_inside_process = bool(gpu_activities) and all(
        int(row["start_ns"]) >= process_start_ns
        and int(row["end_ns"]) <= process_end_ns
        for row in gpu_activities
    )
    runtime_inside_process = bool(runtime_apis) and all(
        int(row["start_ns"]) >= process_start_ns
        and int(row["end_ns"]) <= process_end_ns
        for row in runtime_apis
    )
    gpu_tail_covered_by_final_sync = bool(gpu_activities) and all(
        int(row["end_ns"]) <= final_end_ns for row in gpu_activities
    )
    target_nvtx_inside_process = bool(nvtx) and all(
        int(row["start_ns"]) >= process_start_ns
        and row["end_ns"] is not None
        and int(row["end_ns"]) <= process_end_ns
        for row in nvtx
    )
    runtime_api_histogram = dict(
        sorted(Counter(str(row["name"]) for row in runtime_apis).items())
    )
    allocation_runtime_apis = [
        row
        for row in runtime_apis
        if int(row["start_ns"]) >= int(allocation_range["start_ns"])
        and int(row["end_ns"]) <= int(allocation_range["end_ns"])
    ]
    allocation_runtime_api_histogram = dict(
        sorted(Counter(str(row["name"]) for row in allocation_runtime_apis).items())
    )
    allocation_api_count = sum(
        count
        for name, count in allocation_runtime_api_histogram.items()
        if name.startswith(("cudaMalloc", "cuMemAlloc"))
    )
    allocation_scope_inside_process = (
        int(allocation_range["start_ns"]) >= process_start_ns
        and int(allocation_range["end_ns"]) <= process_end_ns
        and int(allocation_range["start_ns"]) <= int(final_range["start_ns"])
        and final_end_ns <= int(allocation_range["end_ns"])
    )
    activity_contexts = {
        (int(row["context_id"]), int(row["device_id"]))
        for row in [*kernels, *memcopies]
    }
    context_bound = bool(activity_contexts) and activity_contexts <= contexts

    stats = {name: _csv_rows(stats_paths[name]) for name in REQUIRED_STATS}
    exact_rows = _csv_rows(exact_mem_path)
    stats_counts = {
        "kernel": _integer_sum(stats["cuda_gpu_kern_sum"], "Instances"),
        "runtime_api": _integer_sum(stats["cuda_api_sum"], "Num Calls"),
        "mem_time": _integer_sum(stats["cuda_gpu_mem_time_sum"], "Count"),
        "mem_size": _integer_sum(stats["cuda_gpu_mem_size_sum"], "Count"),
        "mem_exact": _integer_sum(exact_rows, "Count"),
    }
    sqlite_counts = {
        "kernel": len(kernels),
        "runtime_api": len(runtime_apis),
        "memcpy": len(memcopies),
    }
    exact_summary = {
        row["Direction"]: {
            "count": int(row["Count"]),
            "bytes": int(row["Total Bytes"]),
            "time_ns": int(row["Total Time (ns)"]),
        }
        for row in exact_rows
    }
    sqlite_summary = _direction_summary(memcopies)
    exact_match = exact_summary == {
        direction: {
            "count": details["count"],
            "bytes": details["bytes"],
            "time_ns": details["time_ns"],
        }
        for direction, details in sqlite_summary.items()
    }
    associations = _associate_pressure_ranges(memcopies, nvtx)
    multiset = _expected_multiset_gate(associations, expected_pressure_transfers)
    directions = set(sqlite_summary)
    direction_gate = (
        {"Host-to-Device", "Device-to-Host"} <= directions
        if require_h2d_and_d2h
        else bool(directions)
    )
    gates = {
        **{f"file_{name}": passed for name, passed in file_gates.items()},
        "sqlite_integrity_ok": True,
        "required_tables_present": True,
        "kernel_nonempty": bool(kernels),
        "runtime_api_nonempty": bool(runtime_apis),
        "memcpy_nonempty": bool(memcopies),
        "required_transfer_directions_present": direction_gate,
        "unique_complete_nvtx_tail": range_complete,
        "target_nvtx_inside_process_range": target_nvtx_inside_process,
        "cuda_activity_inside_process_range": activity_inside_process,
        "cuda_runtime_inside_process_range": runtime_inside_process,
        "gpu_activity_covered_through_final_sync": gpu_tail_covered_by_final_sync,
        "device_allocation_scope_inside_process_and_final_sync": (
            allocation_scope_inside_process
        ),
        "device_allocation_api_observed_in_scope": allocation_api_count > 0,
        "target_pid_context_bound": context_bound,
        "kernel_stats_count_matches_sqlite": stats_counts["kernel"]
        == sqlite_counts["kernel"],
        "api_stats_count_matches_sqlite": stats_counts["runtime_api"]
        == sqlite_counts["runtime_api"],
        "mem_time_stats_count_matches_sqlite": stats_counts["mem_time"]
        == sqlite_counts["memcpy"],
        "mem_size_stats_count_matches_sqlite": stats_counts["mem_size"]
        == sqlite_counts["memcpy"],
        "exact_mem_stats_count_matches_sqlite": stats_counts["mem_exact"]
        == sqlite_counts["memcpy"],
        "exact_direction_count_bytes_time_matches_sqlite": exact_match,
        "pressure_transfer_multiset_covered": multiset["gate_pass"],
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "target": {
            "pid": target_pid,
            "global_pid": global_pid,
            "cuda_contexts": sorted(contexts),
        },
        "files": {
            name: {
                "path": str(
                    logical_root / path.name if logical_root is not None else path
                ),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for name, path in files.items()
        },
        "schema": schema,
        "counts": {"sqlite": sqlite_counts, "stats": stats_counts},
        "runtime_api_histogram": runtime_api_histogram,
        "device_allocation_scope": dict(allocation_range),
        "device_allocation_runtime_api_histogram": (allocation_runtime_api_histogram),
        "direction_summary": sqlite_summary,
        "nvtx_ranges": nvtx,
        "pressure_range_associations": associations,
        "pressure_transfer_multiset": multiset,
        "claim_ceiling": (
            "CUDA/NVTX activity, kernel/API/memcpy and exact transfer evidence only; "
            "CPU sampling, native call stacks, GPU metrics, and memory-usage tracing disabled"
        ),
    }


def write_schema(path: Path, schema: Mapping[str, Any]) -> None:
    atomic_text(
        path,
        json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


__all__ = [
    "EXACT_MEM_REPORT",
    "NSYS_TRACE",
    "NSYS_VERSION",
    "REQUIRED_STATS",
    "REQUIRED_TABLES",
    "audit_nsys_evidence",
    "export_commands",
    "profile_command",
    "sqlite_schema_manifest",
]
