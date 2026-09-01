"""Build identities and fail-closed audits for P0-P allocation evidence."""

from __future__ import annotations

import ctypes
import os
import re
import stat
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from p0p2v2_contract import (
    HERE,
    P0PerformanceError,
    relative,
    sha256_file,
    strict_json,
)

OBSERVER_SOURCE = HERE / "p0p2v2_allocator_observer.c"
PROBE_SOURCE = HERE / "p0p2v2_allocator_probe.c"
OBSERVER_LIBRARY = HERE / "libp0p2v2_allocator_observer.so"
PROBE_EXECUTABLE = HERE / "p0p2v2_allocator_probe"
BUILD_FLAGS = (
    "-O2",
    "-std=c11",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-fno-omit-frame-pointer",
)
OBSERVED_APIS = (
    "malloc",
    "calloc",
    "realloc",
    "posix_memalign",
    "aligned_alloc",
    "memalign",
    "valloc",
    "pvalloc",
    "mmap",
    "free",
    "munmap",
)
OBSERVER_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "pid",
        "apis",
        "heap_live_bytes",
        "mmap_live_bytes",
        "heap_peak_live_bytes",
        "mmap_peak_live_bytes",
        "combined_peak_live_bytes",
        "overflow_events",
        "accounting_underflow_events",
        "hook_reentry_events",
        "tracking_table_overflow_events",
        "tracking_consistency_events",
        "untracked_free_events",
        "untracked_realloc_events",
        "untracked_munmap_events",
        "heap_live_allocations",
        "heap_peak_live_allocations",
        "mmap_live_mappings",
        "mmap_peak_live_mappings",
        "heap_tracking_capacity",
        "mmap_tracking_capacity",
        "page_size_bytes",
        "active_recorders_at_snapshot",
        "public_symbols_resolved",
        "capture_enabled_at_snapshot",
    }
)
_DEVICE_ALLOCATION_API = re.compile(
    r"^(?:cudaMalloc(?:Async)?|cuMemAlloc(?:Async)?)(?:_v[0-9]+)?$"
)


def build_commands() -> list[list[str]]:
    return [
        [
            "gcc",
            *BUILD_FLAGS,
            "-shared",
            "-fPIC",
            "-pthread",
            "-o",
            str(OBSERVER_LIBRARY),
            str(OBSERVER_SOURCE),
            "-ldl",
        ],
        [
            "gcc",
            *BUILD_FLAGS,
            "-fno-builtin",
            "-pthread",
            "-ldl",
            "-o",
            str(PROBE_EXECUTABLE),
            str(PROBE_SOURCE),
        ],
    ]


def build_native_artifacts() -> dict[str, Any]:
    completed_commands: list[dict[str, Any]] = []
    for command in build_commands():
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        completed_commands.append(
            {
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        if completed.returncode != 0:
            raise P0PerformanceError(
                "native allocation observer build failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
    compiler = subprocess.run(
        ["gcc", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    artifacts = {
        path.name: {
            "path": relative(path),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "executable": os.access(path, os.X_OK),
        }
        for path in (
            OBSERVER_SOURCE,
            PROBE_SOURCE,
            OBSERVER_LIBRARY,
            PROBE_EXECUTABLE,
        )
    }
    return {
        "gate_pass": True,
        "compiler_first_line": compiler.stdout.splitlines()[0],
        "build_flags": list(BUILD_FLAGS),
        "commands": completed_commands,
        "artifacts": artifacts,
    }


def observer_environment(
    source: Mapping[str, str],
    *,
    output_path: Path,
    start_enabled: bool,
    observer_fd: int,
) -> dict[str, str]:
    if not output_path.is_absolute():
        raise P0PerformanceError("allocation observer output path must be absolute")
    if output_path.exists() or output_path.is_symlink():
        raise P0PerformanceError("allocation observer output must not pre-exist")
    environment = dict(source)
    if environment.get("LD_PRELOAD"):
        raise P0PerformanceError("pre-existing LD_PRELOAD is prohibited")
    _validate_observer_fd(observer_fd)
    # This repository path contains whitespace, an LD_PRELOAD separator.  An
    # inherited read-only descriptor names the exact bound artifact without a
    # copy, an untrusted temporary file, or any loader quoting ambiguity.
    environment["LD_PRELOAD"] = f"/proc/self/fd/{observer_fd}"
    environment["P0P2V2_ALLOC_OBSERVER_ACTIVE"] = "1"
    environment["P0P2V2_ALLOC_OBSERVER_OUTPUT"] = str(output_path)
    environment["P0P2V2_ALLOC_OBSERVER_START_ENABLED"] = (
        "1" if start_enabled else "0"
    )
    return environment


def _validate_observer_fd(descriptor: int) -> None:
    opened = os.fstat(descriptor)
    source = os.stat(OBSERVER_LIBRARY, follow_symlinks=False)
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
        source.st_dev,
        source.st_ino,
    ):
        raise P0PerformanceError("allocation observer descriptor identity mismatch")


def open_observer_fd() -> int:
    descriptor = os.open(
        OBSERVER_LIBRARY,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        _validate_observer_fd(descriptor)
        os.set_inheritable(descriptor, True)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def load_observer_control() -> tuple[Any, Any]:
    process = ctypes.CDLL(None)
    start = process.p0p2v2_observer_reset_and_start
    stop = process.p0p2v2_observer_stop_and_write
    start.argtypes = []
    start.restype = None
    stop.argtypes = []
    stop.restype = None
    return start, stop


def audit_observer_payload(
    path: Path,
    *,
    expected_pid: int,
    expected_calls: Mapping[str, int] | None = None,
    expected_requested_bytes: Mapping[str, int] | None = None,
    allow_overflow: bool = False,
    logical_path: Path | None = None,
) -> dict[str, Any]:
    payload = strict_json(path)
    if not isinstance(payload, Mapping):
        raise P0PerformanceError("allocation observer payload is not an object")
    apis = payload.get("apis")
    if not isinstance(apis, Mapping):
        raise P0PerformanceError("allocation observer payload lacks APIs")
    integer_fields = OBSERVER_TOP_LEVEL_FIELDS - {
        "status",
        "apis",
        "public_symbols_resolved",
        "capture_enabled_at_snapshot",
    }
    integer_fields -= {"schema_version", "pid"}
    all_nonnegative_integers = all(
        type(payload.get(field)) is int and payload[field] >= 0
        for field in integer_fields
    )

    def integer_value(field: str) -> int:
        value = payload.get(field)
        return value if type(value) is int else -1

    regular_output = path.is_file() and not path.is_symlink()
    gates: dict[str, bool] = {
        "schema_version": payload.get("schema_version") == 2,
        "top_level_schema_exact": set(payload) == OBSERVER_TOP_LEVEL_FIELDS,
        "status": payload.get("status") == "P0P2V2_ALLOC_OBSERVER_COMPLETE",
        "pid": type(payload.get("pid")) is int and payload.get("pid") == expected_pid,
        "regular_nonsymlink_output": regular_output,
        "api_set_complete": set(apis) == set(OBSERVED_APIS),
        "top_level_counters_nonnegative_integers": all_nonnegative_integers,
        "capture_stopped_at_snapshot": payload.get("capture_enabled_at_snapshot")
        is False,
        "no_active_recorders_at_snapshot": payload.get("active_recorders_at_snapshot")
        == 0,
        "public_allocator_symbols_resolved": payload.get("public_symbols_resolved")
        is True,
        "no_accounting_underflow": payload.get("accounting_underflow_events") == 0,
        "no_hook_reentry": payload.get("hook_reentry_events") == 0,
        "no_tracking_table_overflow": payload.get("tracking_table_overflow_events")
        == 0,
        "no_tracking_consistency_error": payload.get("tracking_consistency_events")
        == 0,
        "overflow_policy": bool(payload.get("overflow_events"))
        if allow_overflow
        else payload.get("overflow_events") == 0,
        "page_size_bound": payload.get("page_size_bytes") == os.sysconf("SC_PAGESIZE"),
        "heap_tracking_within_capacity": 0
        <= integer_value("heap_live_allocations")
        <= integer_value("heap_peak_live_allocations")
        <= integer_value("heap_tracking_capacity"),
        "mmap_tracking_within_capacity": 0
        <= integer_value("mmap_live_mappings")
        <= integer_value("mmap_peak_live_mappings")
        <= integer_value("mmap_tracking_capacity"),
        "heap_peak_covers_live": 0
        <= integer_value("heap_live_bytes")
        <= integer_value("heap_peak_live_bytes"),
        "mmap_peak_covers_live": 0
        <= integer_value("mmap_live_bytes")
        <= integer_value("mmap_peak_live_bytes"),
        "combined_peak_covers_component_peaks": integer_value(
            "combined_peak_live_bytes"
        )
        >= max(
            integer_value("heap_peak_live_bytes"),
            integer_value("mmap_peak_live_bytes"),
        ),
    }
    for name in OBSERVED_APIS:
        record = apis.get(name)
        valid = (
            isinstance(record, Mapping)
            and set(record)
            == {
                "calls",
                "requested_bytes",
                "actual_bytes",
            }
            and all(
                type(record.get(field)) is int and record[field] >= 0
                for field in ("calls", "requested_bytes", "actual_bytes")
            )
        )
        gates[f"{name}_nonnegative_integer_record"] = valid
    for name, expected in (expected_calls or {}).items():
        gates[f"{name}_calls_exact"] = (
            isinstance(apis.get(name), Mapping) and apis[name].get("calls") == expected
        )
    for name, expected in (expected_requested_bytes or {}).items():
        gates[f"{name}_requested_bytes_exact"] = (
            isinstance(apis.get(name), Mapping)
            and apis[name].get("requested_bytes") == expected
        )
    failed = [name for name, passed in gates.items() if not passed]
    return {
        "gate_pass": not failed,
        "gates": gates,
        "failed_gates": failed,
        "path": (
            relative(logical_path)
            if logical_path is not None
            else (
                relative(path)
                if path.resolve().is_relative_to(HERE.parents[2])
                else str(path)
            )
        ),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "payload": payload,
        "claim_ceiling": (
            "Observed forwarded glibc heap/aligned/mmap API calls, requested and "
            "usable or page-rounded bytes, and tracked live/peak bytes within the "
            "drained capture boundary; not a replacement allocator"
        ),
    }


def audit_known_probe(
    path: Path,
    *,
    expected_pid: int,
    logical_path: Path | None = None,
) -> dict[str, Any]:
    page = os.sysconf("SC_PAGESIZE")
    expected_calls = {
        "malloc": 3,
        "calloc": 3,
        "realloc": 2,
        "posix_memalign": 2,
        "aligned_alloc": 1,
        "memalign": 1,
        "valloc": 1,
        "pvalloc": 1,
        "mmap": 2,
        "free": 13,
        "munmap": 4,
    }
    expected_bytes = {
        "malloc": 74,
        "calloc": 136,
        "realloc": 177,
        "posix_memalign": 145,
        "aligned_alloc": 128,
        "memalign": 64,
        "valloc": 33,
        "pvalloc": 65,
        "mmap": 4 * page,
        "munmap": 4 * page,
    }
    result = audit_observer_payload(
        path,
        expected_pid=expected_pid,
        expected_calls=expected_calls,
        expected_requested_bytes=expected_bytes,
        logical_path=logical_path,
    )
    payload = result["payload"]
    result["gates"].update(
        {
            "heap_live_returns_zero": payload.get("heap_live_bytes") == 0,
            "mmap_live_returns_zero": payload.get("mmap_live_bytes") == 0,
            "heap_live_allocation_count_returns_zero": payload.get(
                "heap_live_allocations"
            )
            == 0,
            "mmap_live_mapping_count_returns_zero": payload.get("mmap_live_mappings")
            == 0,
            "heap_peak_positive": int(payload.get("heap_peak_live_bytes", 0)) > 0,
            "mmap_peak_exact_four_pages": payload.get("mmap_peak_live_bytes")
            == 4 * page,
            "mmap_partial_unmap_split_observed": payload.get("mmap_peak_live_mappings")
            == 3,
            "pre_capture_free_disclosed": payload.get("untracked_free_events") == 1,
            "pre_capture_realloc_disclosed": payload.get("untracked_realloc_events")
            == 1,
            "no_untracked_munmap": payload.get("untracked_munmap_events") == 0,
            "combined_peak_at_least_component_peaks": int(
                payload.get("combined_peak_live_bytes", 0)
            )
            >= max(
                int(payload.get("heap_peak_live_bytes", 0)),
                int(payload.get("mmap_peak_live_bytes", 0)),
            ),
        }
    )
    result["failed_gates"] = [
        name for name, passed in result["gates"].items() if not passed
    ]
    result["gate_pass"] = not result["failed_gates"]
    return result


def device_allocation_crosscheck(
    recorder: Mapping[str, Any],
    *,
    nsys_allocation_api_counts: Mapping[str, int],
    nvidia_smi_peak_bytes: int,
) -> dict[str, Any]:
    cupy_allocations = int(recorder.get("underlying_device_allocation_count", -1))
    invalid_nsys_counts = {
        str(name): value
        for name, value in nsys_allocation_api_counts.items()
        if not isinstance(value, int) or value < 0
    }
    nsys_allocations = sum(
        int(value)
        for name, value in nsys_allocation_api_counts.items()
        if _DEVICE_ALLOCATION_API.fullmatch(str(name)) and isinstance(value, int)
    )
    recorder_peak = int(recorder.get("device_used_high_water_bytes", -1))
    pool_peak = int(recorder.get("pool_total_high_water_bytes", -1))
    unmatched = max(0, nsys_allocations - cupy_allocations)
    recorder_only = max(0, cupy_allocations - nsys_allocations)
    nvidia_resolution_bytes = 1024 * 1024
    gates = {
        "cupy_allocation_count_nonnegative": cupy_allocations >= 0,
        "nsys_allocation_counts_valid": not invalid_nsys_counts,
        "no_unmatched_device_allocation": unmatched == 0,
        "cupy_and_nsys_allocation_counts_equal": recorder_only == 0,
        "device_peak_at_least_pool_peak": recorder_peak >= pool_peak >= 0,
        "nvidia_smi_peak_nonnegative": nvidia_smi_peak_bytes >= 0,
        "nvidia_smi_process_peak_covers_pool_with_one_mib_resolution": (
            nvidia_smi_peak_bytes + nvidia_resolution_bytes >= pool_peak
        ),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "cupy_underlying_allocation_count": cupy_allocations,
        "nsys_allocation_api_count": nsys_allocations,
        "unmatched_allocation_count": unmatched,
        "recorder_only_allocation_count": recorder_only,
        "nsys_allocation_api_histogram": dict(nsys_allocation_api_counts),
        "invalid_nsys_allocation_api_counts": invalid_nsys_counts,
        "device_used_high_water_bytes": recorder_peak,
        "pool_total_high_water_bytes": pool_peak,
        "nvidia_smi_peak_bytes": nvidia_smi_peak_bytes,
        "nvidia_smi_resolution_allowance_bytes": nvidia_resolution_bytes,
    }


def run_probe(
    mode: str,
    *,
    output_path: Path,
) -> subprocess.CompletedProcess[str]:
    if mode not in {"known", "exit7", "overflow"}:
        raise ValueError("unknown allocation probe mode")
    descriptor = open_observer_fd()
    try:
        environment = observer_environment(
            os.environ,
            output_path=output_path,
            start_enabled=False,
            observer_fd=descriptor,
        )
        return subprocess.run(
            [str(PROBE_EXECUTABLE), mode],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            pass_fds=(descriptor,),
        )
    finally:
        os.close(descriptor)


__all__ = [
    "BUILD_FLAGS",
    "OBSERVED_APIS",
    "OBSERVER_LIBRARY",
    "OBSERVER_SOURCE",
    "PROBE_EXECUTABLE",
    "PROBE_SOURCE",
    "audit_known_probe",
    "audit_observer_payload",
    "build_commands",
    "build_native_artifacts",
    "device_allocation_crosscheck",
    "load_observer_control",
    "observer_environment",
    "open_observer_fd",
    "run_probe",
]
