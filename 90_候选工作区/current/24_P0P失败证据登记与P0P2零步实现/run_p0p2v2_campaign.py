#!/usr/bin/env python3
"""Fail-closed orchestrator for the frozen HF3D P0-P campaign.

The committed authorization object is intentionally a denial.  Consequently
the only currently legal command is ``--zero-step-preflight``; ``--execute``
must fail before numerical imports, environment waiting, lock acquisition, or
creation of the performance raw root.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from p0p2v2_allocation import (
    audit_observer_payload,
    observer_environment,
    open_observer_fd,
)
from p0p2v2_authorization import (
    AUTHORIZED_STATUS,
    REQUIRED_IMPLEMENTATION_RELATIVE_PATHS,
)
from p0p2v2_consumption import (
    CAMPAIGN_LOCK_PATH,
    create_claim_and_ledger,
    mark_sample_pass,
    mark_sample_running,
    mark_schedule_failed,
    publish_execution_complete,
    publish_execution_failure,
)
from p0p2v2_contract import (
    EXPANDED_EXECUTION_SCHEDULE,
    EXPECTED_BENCHMARKS,
    GPU_PYTHON,
    HERE,
    RAW_ROOT,
    STATIC_ROOT,
    P0PerformanceError,
    assert_no_sample_output_root,
    atomic_json,
    evidence_identity,
    execution_binding,
    frozen_child_environment,
    load_authorization,
    load_contract,
    numerical_quality_gates,
    sample_identity,
    sha256_file,
    strict_json,
    strict_json_bytes,
    validate_schedule_prefix,
)
from p0p2v2_instrumentation import audit_host_semantic_evidence, parse_pstats
from p0p2v2_nsys import (
    REQUIRED_STATS,
    audit_nsys_evidence,
    export_commands,
    profile_command,
)
from p0p2v2_telemetry import (
    ancestor_pids,
    audit_monitor_records,
    evaluate_instantaneous_environment,
    full_environment_gate,
    instantaneous_environment,
    parse_monitor_jsonl,
)
from p0p2v2_transaction import ExclusiveCampaignLock, SampleTransaction

WORKER = HERE / "run_p0p2v2_sample.py"
MONITOR = HERE / "p0p2v2_monitor.py"
ZERO_STEP_PROBE = HERE / "p0p2v2_zero_step_probe.py"
PRECLAIM_FAILURE_ROOT = STATIC_ROOT / "preclaim_failures"
IMPLEMENTATION_PATHS = tuple(
    HERE.parents[2] / path for path in sorted(REQUIRED_IMPLEMENTATION_RELATIVE_PATHS)
)


def _runtime_side_effect_state() -> dict[str, Any]:
    def one(path: Path) -> dict[str, Any]:
        if path.is_symlink():
            return {"exists": path.exists(), "kind": "symlink"}
        if not path.exists():
            return {"exists": False, "kind": "absent"}
        if path.is_dir():
            return {
                "exists": True,
                "kind": "directory",
                "children": sorted(item.name for item in path.iterdir()),
            }
        if path.is_file():
            return {"exists": True, "kind": "file", "size_bytes": path.stat().st_size}
        return {"exists": True, "kind": "special"}

    return {
        "raw_root": one(RAW_ROOT),
        "lock": one(CAMPAIGN_LOCK_PATH),
        "claim": one(RAW_ROOT / "execution_claim.json"),
        "ledger": one(RAW_ROOT / "schedule_ledger.json"),
    }


def _run_zero_step_probe(benchmark: str) -> dict[str, Any]:
    environment = frozen_child_environment(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [str(GPU_PYTHON), str(ZERO_STEP_PROBE), "--benchmark", benchmark],
        cwd=HERE.parents[2],
        env=environment,
        check=False,
        capture_output=True,
        timeout=900.0,
    )
    try:
        report = strict_json_bytes(
            completed.stdout, label=f"P0-P2 V2 zero-step stdout {benchmark}"
        )
    except P0PerformanceError as exc:
        report = {
            "schema_version": 2,
            "status": "HF3D_P0P2_V2_ZERO_STEP_OUTPUT_INVALID",
            "gate_pass": False,
            "benchmark_id": benchmark,
            "message": str(exc),
            "hf3d_time_steps_advanced": 0,
        }
    return {
        "benchmark_id": benchmark,
        "returncode": completed.returncode,
        "stderr_empty": completed.stderr == b"",
        "stderr_utf8": completed.stderr.decode("utf-8", errors="replace"),
        "report": report,
        "gate_pass": completed.returncode == 0
        and completed.stderr == b""
        and report.get("gate_pass") is True
        and report.get("benchmark_id") == benchmark
        and report.get("hf3d_time_steps_advanced") == 0,
    }


def zero_step_preflight(
    *,
    background_duration_s: float = 30.0,
    initial_snapshot_runner: Any | None = None,
    probe_runner: Any | None = None,
    environment_runner: Any | None = None,
) -> dict[str, Any]:
    """Run the ordered non-consuming V2 preflight without creating runtime state."""

    initial_snapshot_runner = initial_snapshot_runner or instantaneous_environment
    probe_runner = probe_runner or _run_zero_step_probe
    environment_runner = environment_runner or full_environment_gate

    before = _runtime_side_effect_state()
    contract = load_contract()
    quality = numerical_quality_gates()
    frozen_evidence = evidence_identity()
    authorization = load_authorization(require_dynamic=False)
    authorization_is_dynamic = (
        authorization.get("status") == AUTHORIZED_STATUS
        and authorization.get("dynamic_samples_authorized") is True
    )
    output_root = assert_no_sample_output_root()
    initial_environment = evaluate_instantaneous_environment(
        initial_snapshot_runner(), excluded_pids=ancestor_pids()
    )
    probe_results: list[dict[str, Any]] = []
    if initial_environment["gate_pass"]:
        for benchmark in EXPECTED_BENCHMARKS:
            result = probe_runner(benchmark)
            probe_results.append(result)
            if result.get("gate_pass") is not True:
                break
    all_probes = len(probe_results) == len(EXPECTED_BENCHMARKS) and all(
        result.get("gate_pass") is True for result in probe_results
    )
    process_identities = [
        (
            result.get("report", {}).get("process_identity", {}).get("pid"),
            result.get("report", {})
            .get("process_identity", {})
            .get("process_start_ticks"),
        )
        for result in probe_results
    ]
    fresh_processes = all_probes and len(set(process_identities)) == 3 and all(
        pid is not None and ticks is not None for pid, ticks in process_identities
    )
    post_probe_environment = (
        environment_runner(background_duration_s=background_duration_s)
        if all_probes
        else {
            "gate_pass": False,
            "stage": "not_run_due_to_initial_or_zero_step_failure",
        }
    )
    after = _runtime_side_effect_state()
    gates = {
        "contract_frozen": contract.get("contract_id") is not None,
        "quality_gates_bound": bool(quality),
        "p0_d_and_pro_evidence_bound": frozen_evidence["gate_pass"],
        "authorization_schema_and_binding_valid": (
            authorization_is_dynamic
            or authorization.get("dynamic_samples_authorized") is False
        ),
        "raw_root_absent_or_empty": output_root["empty"] is True,
        "initial_instantaneous_environment_before_device_allocation": initial_environment[
            "gate_pass"
        ],
        "all_three_fresh_process_zero_step_probes": all_probes,
        "three_distinct_fresh_processes": fresh_processes,
        "post_probe_full_environment_30s": post_probe_environment.get("gate_pass")
        is True,
        "runtime_state_unchanged_by_preflight": before == after,
    }
    return {
        "schema_version": 2,
        "status": (
            "HF3D_P0P2_V2_PREFLIGHT_PASS_AUTHORIZED_NONCONSUMING"
            if all(gates.values()) and authorization_is_dynamic
            else (
                "HF3D_P0P2_V2_ZERO_STEP_STATIC_PASS_DYNAMIC_EXECUTION_DENIED"
                if all(gates.values())
                else "HF3D_P0P2_V2_PREFLIGHT_FAILED_STOPPED"
            )
        ),
        "gate_pass": all(gates.values()) and authorization_is_dynamic,
        "static_qualification": {
            "gate_pass": all(gates.values()),
            "gates": gates,
        },
        "gates": gates,
        "initial_environment": initial_environment,
        "probe_results": probe_results,
        "post_probe_environment": post_probe_environment,
        "authorization": authorization,
        "frozen_evidence": frozen_evidence,
        "runtime_state_before": before,
        "runtime_state_after": after,
        "counters": {
            "hf3d_time_steps_advanced": 0,
            "warmup_samples_started": 0,
            "scored_samples_started": 0,
            "profile_samples_started": 0,
            "performance_samples_started": 0,
        },
        "formal_source_unchanged": True,
        "p1_implemented": False,
        "p0p2v2_dynamic_authorization": (
            "AUTHORIZED_NONCONSUMING_PREFLIGHT"
            if authorization_is_dynamic
            else "NOT_AUTHORIZED"
        ),
    }


def _publish_preclaim_failure(record: Mapping[str, Any]) -> Path:
    parent = PRECLAIM_FAILURE_ROOT.parent
    if not parent.is_dir() or parent.is_symlink():
        raise P0PerformanceError("preclaim evidence parent is unavailable")
    if PRECLAIM_FAILURE_ROOT.is_symlink():
        raise P0PerformanceError("preclaim evidence root must not be a symlink")
    PRECLAIM_FAILURE_ROOT.mkdir(mode=0o700, exist_ok=True)
    path = PRECLAIM_FAILURE_ROOT / (
        f"environment_failure_{time.monotonic_ns()}_{os.getpid()}.json"
    )
    if path.exists() or path.is_symlink():
        raise P0PerformanceError("preclaim failure path collision")
    atomic_json(path, record)
    return path


def _base_worker_command(
    staging: Path,
    identity: Mapping[str, str],
    *,
    sequence_index: int,
    launch_token: str,
    orchestrator_pid: int,
) -> list[str]:
    return [
        str(GPU_PYTHON),
        str(WORKER),
        "--sample-id",
        identity["sample_id"],
        "--mode",
        identity["mode"],
        "--output-dir",
        str(staging),
        "--pid-file",
        str(staging / "worker_pid.json"),
        "--sequence-index",
        str(sequence_index),
        "--launch-token",
        launch_token,
        "--orchestrator-pid",
        str(orchestrator_pid),
        "--lock-path",
        str(CAMPAIGN_LOCK_PATH),
    ]


def sample_launch_spec(
    staging: Path,
    sample_id: str,
    source_environment: Mapping[str, str],
    *,
    sequence_index: int,
    launch_token: str,
    orchestrator_pid: int,
    observer_fd: int | None = None,
) -> dict[str, Any]:
    identity = sample_identity(sample_id)
    environment = frozen_child_environment(source_environment)
    for key in (
        "P0P2V2_CPROFILE_ACTIVE",
        "P0P2V2_NSYS_ACTIVE",
        "P0P2V2_ALLOC_OBSERVER_OUTPUT",
        "P0P2V2_ALLOC_OBSERVER_START_ENABLED",
        "P0P2V2_ALLOC_OBSERVER_ACTIVE",
    ):
        environment.pop(key, None)
    base = _base_worker_command(
        staging,
        identity,
        sequence_index=sequence_index,
        launch_token=launch_token,
        orchestrator_pid=orchestrator_pid,
    )
    mode = identity["mode"]
    if mode == "host":
        environment["P0P2V2_CPROFILE_ACTIVE"] = "1"
        command = [
            str(GPU_PYTHON),
            "-m",
            "cProfile",
            "-o",
            str(staging / "host_profile.pstats"),
            str(WORKER),
            *base[2:],
        ]
    elif mode == "cuda":
        environment["P0P2V2_NSYS_ACTIVE"] = "1"
        command = profile_command(
            output_base=staging / "cuda_profile",
            target_command=base,
        )
    elif mode == "memory":
        if observer_fd is None:
            raise P0PerformanceError("memory profile requires an observer descriptor")
        environment = observer_environment(
            environment,
            output_path=staging / "host_allocation_observer.json",
            start_enabled=True,
            observer_fd=observer_fd,
        )
        command = base
    else:
        command = base
    return {
        "identity": identity,
        "command": command,
        "environment": environment,
        "pid_file": staging / "worker_pid.json",
        "stdout": staging / "worker.stdout.jsonl",
        "stderr": staging / "worker.stderr.log",
        "pass_fds": (observer_fd,) if mode == "memory" else (),
    }


def _wait_for_worker_identity(
    pid_file: Path,
    process: subprocess.Popen[bytes],
    *,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pid_file.is_file() and not pid_file.is_symlink():
            payload = strict_json(pid_file)
            if not (
                isinstance(payload.get("pid"), int)
                and isinstance(payload.get("process_start_ticks"), int)
            ):
                raise P0PerformanceError("worker PID identity is invalid")
            return payload
        if process.poll() is not None:
            raise P0PerformanceError(
                "worker or profiler exited before publishing target PID identity"
            )
        time.sleep(0.05)
    raise P0PerformanceError("worker PID identity publication timed out")


def _open_new(path: Path, *, binary: bool) -> TextIO | Any:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    if binary:
        return os.fdopen(descriptor, "wb")
    return os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")


def _start_monitors(
    staging: Path, worker: Mapping[str, Any]
) -> list[subprocess.Popen[bytes]]:
    stop_file = staging / "monitors.stop"
    commands = []
    for kind, interval in (
        ("gpu", 0.5),
        ("tctl", 0.5),
        ("proc", 0.5),
        ("pidstat", 1.0),
    ):
        commands.append(
            [
                str(GPU_PYTHON),
                str(MONITOR),
                "--kind",
                kind,
                "--pid",
                str(worker["pid"]),
                "--start-ticks",
                str(worker["process_start_ticks"]),
                "--interval-s",
                str(interval),
                "--stop-file",
                str(stop_file),
                "--output",
                str(staging / f"monitor_{kind}.jsonl"),
            ]
        )
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for command in commands:
            processes.append(
                subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            )
    except BaseException:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.communicate()
        raise
    return processes


def _wait_for_monitor_starts(
    staging: Path,
    monitors: Sequence[subprocess.Popen[bytes]],
    worker: Mapping[str, Any],
    *,
    timeout_s: float = 30.0,
) -> list[dict[str, Any]]:
    kinds = ("gpu", "tctl", "proc", "pidstat")
    if len(monitors) != len(kinds):
        raise P0PerformanceError("monitor process set is incomplete")
    deadline = time.monotonic() + timeout_s
    starts: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        for kind, process in zip(kinds, monitors, strict=True):
            if kind in starts:
                continue
            if process.poll() is not None:
                raise P0PerformanceError(
                    f"{kind} monitor exited before its start record"
                )
            path = staging / f"monitor_{kind}.jsonl"
            if not path.is_file() or path.is_symlink():
                continue
            try:
                first_line = path.open("r", encoding="utf-8").readline()
                record = json.loads(first_line)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not (
                isinstance(record, Mapping)
                and record.get("record_type") == "monitor_start"
                and record.get("kind") == kind
                and record.get("monitor_pid") == process.pid
                and record.get("target_pid") == worker["pid"]
                and record.get("target_start_ticks") == worker["process_start_ticks"]
                and isinstance(record.get("monotonic_ns"), int)
            ):
                raise P0PerformanceError(f"{kind} monitor start identity is invalid")
            starts[kind] = dict(record)
        if len(starts) == len(kinds):
            break
        time.sleep(0.02)
    if len(starts) != len(kinds):
        raise P0PerformanceError("monitor start publication timed out")
    ready = {
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_ALL_EXTERNAL_MONITORS_STARTED",
        "target_pid": worker["pid"],
        "target_start_ticks": worker["process_start_ticks"],
        "published_monotonic_ns": time.monotonic_ns(),
        "monitor_starts": [starts[kind] for kind in kinds],
    }
    ready_path = staging / "monitors_ready.json"
    stream = _open_new(ready_path, binary=False)
    try:
        json.dump(ready, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    finally:
        stream.close()
    directory = os.open(
        staging,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return list(ready["monitor_starts"])


def _stop_monitors(
    staging: Path, monitors: Sequence[subprocess.Popen[bytes]]
) -> list[dict[str, Any]]:
    stop_file = staging / "monitors.stop"
    stop_preexisted = stop_file.exists() or stop_file.is_symlink()
    if not stop_preexisted:
        descriptor = os.open(
            stop_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        os.close(descriptor)
    outcomes: list[dict[str, Any]] = []
    for process in monitors:
        try:
            _stdout, stderr = process.communicate(timeout=10.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                _stdout, stderr = process.communicate(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                _stdout, stderr = process.communicate()
        outcomes.append(
            {
                "pid": process.pid,
                "exit_code": process.returncode,
                "stderr": stderr.decode("utf-8", errors="replace"),
            }
        )
    if stop_preexisted:
        raise P0PerformanceError("monitor stop token unexpectedly pre-existed")
    return outcomes


def _run_exports(staging: Path) -> dict[str, Path]:
    report = staging / "cuda_profile.nsys-rep"
    sqlite_path = staging / "cuda_profile.sqlite"
    bases = {name: staging / name for name in REQUIRED_STATS}
    bases["p0p2v2_cuda_mem_exact"] = staging / "p0p2v2_cuda_mem_exact"
    for command in export_commands(
        report_path=report,
        sqlite_path=sqlite_path,
        report_output_bases=bases,
    ):
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise P0PerformanceError(
                "Nsight export/stats failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
    discovered: dict[str, Path] = {"report": report, "sqlite": sqlite_path}
    for name, base in bases.items():
        matches = sorted(staging.glob(f"{base.name}_*.csv"))
        if len(matches) != 1:
            raise P0PerformanceError(f"Nsight report output is not unique: {name}")
        discovered[name] = matches[0]
    return discovered


def _audit_monitors(
    staging: Path,
    *,
    worker: Mapping[str, Any],
    target_start_ns: int,
    target_end_ns: int,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for kind in ("gpu", "tctl", "proc", "pidstat"):
        records = parse_monitor_jsonl(staging / f"monitor_{kind}.jsonl")
        output[kind] = audit_monitor_records(
            records,
            kind=kind,
            target_pid=int(worker["pid"]),
            target_start_ticks=int(worker["process_start_ticks"]),
            target_start_ns=target_start_ns,
            target_end_ns=target_end_ns,
        )
    output["gate_pass"] = all(value["gate_pass"] for value in output.values())
    return output


def audit_sample(
    staging: Path,
    *,
    launch: Mapping[str, Any],
    worker: Mapping[str, Any],
    worker_exit_code: int,
    target_end_ns: int,
    monitor_outcomes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if worker_exit_code != 0:
        raise P0PerformanceError(f"sample worker/profiler exited {worker_exit_code}")
    result = strict_json(staging / "sample_result.json")
    identity = launch["identity"]
    if not (
        result.get("gate_pass") is True
        and result.get("sample_id") == identity["sample_id"]
        and result.get("mode") == identity["mode"]
    ):
        raise P0PerformanceError("worker result identity or hard gate failed")
    monitors = _audit_monitors(
        staging,
        worker=worker,
        target_start_ns=int(worker["published_monotonic_ns"]),
        target_end_ns=target_end_ns,
    )
    audited_monitor_pids = {
        int(monitors[kind]["summary"]["monitor_pid"])
        for kind in ("gpu", "tctl", "proc", "pidstat")
        if isinstance(monitors[kind].get("summary"), Mapping)
        and isinstance(monitors[kind]["summary"].get("monitor_pid"), int)
    }
    outcome_pids = {
        int(outcome["pid"])
        for outcome in monitor_outcomes
        if isinstance(outcome.get("pid"), int)
    }
    monitor_process_gates = {
        "four_monitor_process_outcomes": len(monitor_outcomes) == 4,
        "monitor_process_pids_unique": len(outcome_pids) == 4,
        "monitor_processes_match_records": outcome_pids == audited_monitor_pids,
        "monitor_processes_exit_zero": all(
            outcome.get("exit_code") == 0 for outcome in monitor_outcomes
        ),
        "monitor_process_stderr_empty": all(
            not str(outcome.get("stderr", "")).strip() for outcome in monitor_outcomes
        ),
        "worker_completion_within_external_lifetime": int(
            result["completed_monotonic_ns"]
        )
        <= target_end_ns,
    }
    monitors["process_outcomes"] = {
        "gate_pass": all(monitor_process_gates.values()),
        "gates": monitor_process_gates,
        "outcomes": list(monitor_outcomes),
    }
    monitors["gate_pass"] = monitors["gate_pass"] and all(
        monitor_process_gates.values()
    )
    mode = identity["mode"]
    profile: dict[str, Any] | None = None
    if mode == "host":
        pstats_report = parse_pstats(
            staging / "host_profile.pstats",
            logical_path=(
                RAW_ROOT / str(identity["sample_id"]) / "host_profile.pstats"
            ),
        )
        child_wall_ns = result.get("child_observed_wall_time_ns")
        if type(child_wall_ns) is not int or int(child_wall_ns) <= 0:
            raise P0PerformanceError("host profile lacks a valid child wall time")
        host_coverage = audit_host_semantic_evidence(
            result["profile_instrumentation"]["semantic_ranges"],
            child_wall_ns=int(child_wall_ns),
        )
        if host_coverage != result["profile_instrumentation"]["host_coverage"]:
            raise P0PerformanceError(
                "worker host coverage does not independently recompute"
            )
        profile = {
            "gate_pass": bool(host_coverage["gate_pass"])
            and int(pstats_report["total_calls"]) > 0,
            "semantic_coverage": host_coverage,
            "pstats": pstats_report,
        }
        if not profile["gate_pass"]:
            raise P0PerformanceError("host semantic attribution coverage failed")
    elif mode == "cuda":
        outputs = _run_exports(staging)
        profile = audit_nsys_evidence(
            report_path=outputs["report"],
            sqlite_path=outputs["sqlite"],
            stats_paths={name: outputs[name] for name in REQUIRED_STATS},
            exact_mem_path=outputs["p0p2v2_cuda_mem_exact"],
            target_pid=int(worker["pid"]),
            expected_pressure_transfers=result["pressure"][
                "structural_transfer_expectations"
            ],
            logical_root=RAW_ROOT / str(identity["sample_id"]),
        )
        if not profile["gate_pass"]:
            raise P0PerformanceError("Nsight CUDA evidence audit failed")
    elif mode == "memory":
        profile = audit_observer_payload(
            staging / "host_allocation_observer.json",
            expected_pid=int(worker["pid"]),
            logical_path=(
                RAW_ROOT / str(identity["sample_id"]) / "host_allocation_observer.json"
            ),
        )
        if not profile["gate_pass"]:
            raise P0PerformanceError("host native allocation evidence failed")
    files = {
        path.relative_to(staging).as_posix(): {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(staging.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    manifest = {
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_SAMPLE_EVIDENCE_COMPLETE",
        "gate_pass": monitors["gate_pass"],
        **identity,
        "worker": worker,
        "worker_exit_code": worker_exit_code,
        "monitor_outcomes": list(monitor_outcomes),
        "monitor_audit": monitors,
        "worker_result": result,
        "profile_audit": profile,
        "canonical_numerical_payload_sha256": result["archive"][
            "canonical_numerical_payload"
        ]["sha256"],
        "files_before_manifest": files,
    }
    if not manifest["gate_pass"]:
        raise P0PerformanceError("sample telemetry evidence failed")
    return manifest


def execute_one_sample(
    sample_id: str,
    *,
    sequence_index: int,
    launch_token: str,
    orchestrator_pid: int,
) -> dict[str, Any]:
    transaction = SampleTransaction(RAW_ROOT, sample_id)
    staging = transaction.begin()
    observer_fd: int | None = None
    stdout_stream: TextIO | Any | None = None
    stderr_stream: TextIO | Any | None = None
    process: subprocess.Popen[bytes] | None = None
    monitors: list[subprocess.Popen[bytes]] = []
    try:
        identity = sample_identity(sample_id)
        observer_fd = open_observer_fd() if identity["mode"] == "memory" else None
        launch = sample_launch_spec(
            staging,
            sample_id,
            os.environ,
            sequence_index=sequence_index,
            launch_token=launch_token,
            orchestrator_pid=orchestrator_pid,
            observer_fd=observer_fd,
        )
        stdout_stream = _open_new(launch["stdout"], binary=True)
        stderr_stream = _open_new(launch["stderr"], binary=True)
        started_ns = time.monotonic_ns()
        process = subprocess.Popen(
            launch["command"],
            stdout=stdout_stream,
            stderr=stderr_stream,
            env=launch["environment"],
            pass_fds=launch["pass_fds"],
        )
        if observer_fd is not None:
            os.close(observer_fd)
            observer_fd = None
        worker = _wait_for_worker_identity(launch["pid_file"], process)
        monitors = _start_monitors(staging, worker)
        monitor_starts = _wait_for_monitor_starts(staging, monitors, worker)
        exit_code = process.wait()
        ended_ns = time.monotonic_ns()
        monitor_outcomes = _stop_monitors(staging, monitors)
        stdout_stream.close()
        stderr_stream.close()
        manifest = audit_sample(
            staging,
            launch=launch,
            worker=worker,
            worker_exit_code=exit_code,
            target_end_ns=ended_ns,
            monitor_outcomes=monitor_outcomes,
        )
        manifest["monitor_start_records"] = monitor_starts
        manifest["external_started_monotonic_ns"] = started_ns
        manifest["external_ended_monotonic_ns"] = ended_ns
        manifest["external_end_to_end_wall_time_s"] = (ended_ns - started_ns) / 1.0e9
        transaction.publish_success(manifest)
        return manifest
    except BaseException as exc:
        if observer_fd is not None:
            os.close(observer_fd)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if stdout_stream is not None and not stdout_stream.closed:
            stdout_stream.close()
        if stderr_stream is not None and not stderr_stream.closed:
            stderr_stream.close()
        if monitors and not (staging / "monitors.stop").exists():
            try:
                _stop_monitors(staging, monitors)
            except Exception:  # noqa: BLE001,S110 - preserve the original sample failure.
                # Monitor shutdown diagnostics are best-effort here; the primary
                # exception below remains the authoritative failed-stop cause.
                pass
        failure = {
            "schema_version": 2,
            "status": "HF3D_P0P2_V2_SAMPLE_FAILED_STOPPED_NO_RETRY",
            "gate_pass": False,
            "sample_id": sample_id,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        transaction.publish_failure(failure)
        raise


def execute_campaign() -> dict[str, Any]:
    """Execute once only after a future exact-snapshot Pro authorization."""

    # Denial/audit occurs before the lock, environment wait, raw creation, GPU,
    # profiler, monitor, worker, claim, or numerical side effect.
    authorization = load_authorization(require_dynamic=True)
    load_contract()
    evidence_identity()
    assert_no_sample_output_root()
    preflight = zero_step_preflight()
    if preflight.get("gate_pass") is not True:
        failure = {
            "schema_version": 2,
            "status": "HF3D_P0P2_V2_PREFLIGHT_FAILED_BEFORE_CLAIM",
            "gate_pass": False,
            "preflight": preflight,
        }
        _publish_preclaim_failure(failure)
        raise P0PerformanceError("P0-P2 V2 preclaim preflight failed")
    binding = execution_binding(IMPLEMENTATION_PATHS)
    with ExclusiveCampaignLock(CAMPAIGN_LOCK_PATH) as campaign_lock:
        completed: list[str] = []
        manifests: list[dict[str, Any]] = []
        claimed = False
        active_index: int | None = None
        active_sample: str | None = None
        try:
            post_probe_environment = preflight.get("post_probe_environment")
            if not isinstance(post_probe_environment, Mapping):
                raise P0PerformanceError("post-probe environment binding is absent")
            create_claim_and_ledger(
                authorization=authorization,
                execution_binding=binding,
                environment=post_probe_environment,
                lock_identity=campaign_lock.identity,
            )
            claimed = True
            for sequence_index, sample_id in enumerate(EXPANDED_EXECUTION_SCHEDULE):
                validate_schedule_prefix(completed, sample_id)
                environment = evaluate_instantaneous_environment(
                    instantaneous_environment(), excluded_pids=ancestor_pids()
                )
                if not environment["gate_pass"]:
                    failure = {
                        "schema_version": 2,
                        "status": "HF3D_P0P2_V2_ENVIRONMENT_FAILED_AFTER_CLAIM_STOPPED",
                        "gate_pass": False,
                        "next_sample": sample_id,
                        "completed_samples": completed,
                        "environment": environment,
                    }
                    mark_schedule_failed(
                        sequence_index=None,
                        sample_id=None,
                        failure=failure,
                    )
                    publish_execution_failure(failure)
                    raise P0PerformanceError("P0-P2 V2 environment gate failed")
                launch_token = mark_sample_running(sequence_index, sample_id)
                active_index = sequence_index
                active_sample = sample_id
                manifest = execute_one_sample(
                    sample_id,
                    sequence_index=sequence_index,
                    launch_token=launch_token,
                    orchestrator_pid=os.getpid(),
                )
                manifest_path = RAW_ROOT / sample_id / "sample_manifest.json"
                mark_sample_pass(
                    sequence_index,
                    sample_id,
                    launch_token=launch_token,
                    manifest_sha256=sha256_file(manifest_path),
                )
                active_index = None
                active_sample = None
                manifests.append(manifest)
                completed.append(sample_id)
            completion = {
                "schema_version": 2,
                "status": "HF3D_P0P2_V2_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS",
                "gate_pass": True,
                "authorization_child_commit": authorization[
                    "observed_parent_ticket_binding"
                ]["authorization_child_commit"],
                "completed_samples": completed,
                "completed_monotonic_ns": time.monotonic_ns(),
            }
            publish_execution_complete(completion)
        except BaseException as exc:
            if (
                claimed
                and not (RAW_ROOT / "execution_failure.json").exists()
                and not (RAW_ROOT / "execution_complete.json").exists()
            ):
                terminal_failure: dict[str, Any] = {
                    "schema_version": 2,
                    "status": "HF3D_P0P2_V2_CLAIM_CONSUMED_FAILED_STOPPED_NO_RESUME",
                    "gate_pass": False,
                    "active_sequence_index": active_index,
                    "active_sample": active_sample,
                    "completed_samples": completed,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                    "failed_monotonic_ns": time.monotonic_ns(),
                }
                try:
                    mark_schedule_failed(
                        sequence_index=active_index,
                        sample_id=active_sample,
                        failure=terminal_failure,
                    )
                finally:
                    publish_execution_failure(terminal_failure)
            raise
        return {
            "schema_version": 2,
            "status": "HF3D_P0P2_V2_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS",
            "gate_pass": True,
            "authorization": authorization,
            "execution_binding": binding,
            "completed_samples": completed,
            "sample_manifests": manifests,
        }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--zero-step-preflight", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.zero_step_preflight:
            report = zero_step_preflight()
        else:
            report = execute_campaign()
        if args.output is not None:
            output = args.output.resolve()
            if not output.is_relative_to(STATIC_ROOT.resolve()):
                raise P0PerformanceError(
                    "orchestrator report output escaped static root"
                )
            atomic_json(output, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if report["gate_pass"] else 2
    except BaseException as exc:  # noqa: BLE001 - retain interrupt evidence.
        failure = {
            "schema_version": 2,
            "status": "HF3D_P0P2_V2_ORCHESTRATOR_FAILED_STOPPED",
            "gate_pass": False,
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
