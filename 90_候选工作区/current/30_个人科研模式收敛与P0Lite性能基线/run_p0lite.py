"""Launch, monitor, and preserve the fixed P0-Lite HF3D baseline attempts."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from p0lite_common import (
    BENCHMARK_SOURCES,
    GPU_PYTHON,
    HERE,
    NSYS,
    RUNS_ROOT,
    P0LiteError,
    capture_environment,
    compare_payloads,
    effective_config,
    environment_id,
    evaluate_environment,
    export_nsys_reports,
    git_commit,
    load_schedule,
    new_attempt_directory,
    nsys_profile_command,
    runtime_sensor_snapshot,
    sample_background_cpu,
    slot_record,
    software_environment,
    strict_json,
    utc_now,
    validate_success_artifacts,
    write_json,
    write_status,
)

WORKER = HERE / "p0lite_worker.py"


def _thread_snapshot(pid: int) -> dict[str, Any]:
    process = Path("/proc") / str(pid)
    status: dict[str, Any] = {}
    try:
        for line in (process / "status").read_text(encoding="ascii").splitlines():
            key, separator, value = line.partition(":")
            if separator and key in {"VmRSS", "VmHWM", "Threads"}:
                parts = value.strip().split()
                status[key] = int(parts[0]) * (1024 if key.startswith("Vm") else 1)
    except (OSError, ValueError, IndexError):
        status["unavailable"] = True
    threads = []
    for stat_path in sorted(process.glob("task/[0-9]*/stat")):
        try:
            raw = stat_path.read_text(encoding="ascii")
            closing = raw.rfind(")")
            fields = raw[closing + 2 :].split()
            threads.append(
                {
                    "tid": int(stat_path.parent.name),
                    "name": raw[raw.find("(") + 1 : closing],
                    "user_jiffies": int(fields[11]),
                    "system_jiffies": int(fields[12]),
                }
            )
        except (OSError, ValueError, IndexError):
            continue
    return {"process": status, "threads": threads}


def _process_snapshot() -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            raw = stat_path.read_text(encoding="ascii")
            closing = raw.rfind(")")
            fields = raw[closing + 2 :].split()
            pid = int(stat_path.parent.name)
            cmdline = (stat_path.parent / "cmdline").read_bytes().replace(b"\0", b" ")
            rows[pid] = {
                "pid": pid,
                "ppid": int(fields[1]),
                "name": raw[raw.find("(") + 1 : closing],
                "ticks": int(fields[11]) + int(fields[12]),
                "command": cmdline[:300].decode("utf-8", errors="replace"),
            }
        except (OSError, ValueError, IndexError):
            continue
    return rows


def _instant_pollution_reasons(
    other_processes: Sequence[Mapping[str, Any]], gpu: Mapping[str, Any], cpu_count: int
) -> list[str]:
    busy = [item for item in other_processes if float(item["cpu_percent"]) >= 50.0]
    aggregate = sum(float(item["cpu_percent"]) for item in other_processes)
    gpu_others = [
        item for item in gpu.get("compute_processes", []) if not item.get("is_target_worker")
    ]
    reasons = []
    if len(busy) >= 4:
        reasons.append("four_or_more_external_processes_each_use_at_least_half_a_core")
    if cpu_count > 0 and aggregate / cpu_count > 25.0:
        reasons.append("external_cpu_usage_exceeds_25_percent_of_logical_capacity")
    if gpu_others:
        reasons.append("other_gpu_compute_process_detected")
    return reasons


def _monitor(
    outer_pid: int,
    attempt: Path,
    stop: threading.Event,
    pollution: threading.Event,
    pollution_details: list[dict[str, Any]],
    output: Path,
) -> None:
    previous: tuple[int, int] | None = None
    previous_threads: dict[int, int] = {}
    previous_processes: dict[int, int] = {}
    previous_monotonic: float | None = None
    pollution_streak = 0
    ticks_per_second = float(os.sysconf("SC_CLK_TCK"))
    with output.open("x", encoding="utf-8") as stream:
        while not stop.is_set():
            pid = outer_pid
            identity_path = attempt / "worker_identity.json"
            if identity_path.is_file():
                try:
                    pid = int(strict_json(identity_path)["pid"])
                except (P0LiteError, KeyError, TypeError, ValueError):
                    pid = outer_pid
            captured = runtime_sensor_snapshot()
            current = tuple(captured.pop("cpu_jiffies"))
            monotonic = time.monotonic()
            total_cpu = None
            if previous is not None:
                delta = current[0] - previous[0]
                idle = current[1] - previous[1]
                total_cpu = 0.0 if delta <= 0 else 100.0 * (delta - idle) / delta
            previous = current
            process = _thread_snapshot(pid)
            elapsed = monotonic - previous_monotonic if previous_monotonic is not None else None
            current_threads: dict[int, int] = {}
            for thread in process["threads"]:
                tid = int(thread["tid"])
                ticks = int(thread["user_jiffies"]) + int(thread["system_jiffies"])
                current_threads[tid] = ticks
                before = previous_threads.get(tid)
                thread["cpu_percent"] = (
                    None
                    if before is None or elapsed is None or elapsed <= 0.0
                    else 100.0 * (ticks - before) / ticks_per_second / elapsed
                )
            previous_threads = current_threads
            gpu = captured.get("gpu")
            if isinstance(gpu, dict):
                for item in gpu.get("compute_processes", []):
                    try:
                        item["is_target_worker"] = int(item["pid"]) == pid
                    except (KeyError, TypeError, ValueError):
                        item["is_target_worker"] = False
            process_rows = _process_snapshot()
            excluded = {os.getpid(), outer_pid, pid}
            for _ in range(2):
                excluded.update(
                    item_pid
                    for item_pid, item in process_rows.items()
                    if int(item["ppid"]) in excluded
                )
            other_processes = []
            if elapsed is not None and elapsed > 0.0:
                for item_pid, item in process_rows.items():
                    before = previous_processes.get(item_pid)
                    if before is None or item_pid in excluded:
                        continue
                    cpu_percent = (
                        100.0 * (int(item["ticks"]) - before) / ticks_per_second / elapsed
                    )
                    if cpu_percent >= 10.0:
                        other_processes.append({**item, "cpu_percent": cpu_percent})
            previous_processes = {
                item_pid: int(item["ticks"]) for item_pid, item in process_rows.items()
            }
            reasons = _instant_pollution_reasons(
                other_processes,
                gpu if isinstance(gpu, Mapping) else {},
                int(os.cpu_count() or 0),
            )
            pollution_streak = pollution_streak + 1 if reasons else 0
            triggered = pollution_streak >= 5
            if triggered and not pollution.is_set():
                pollution_details.append(
                    {
                        "detected_at_utc": utc_now(),
                        "reasons": reasons,
                        "other_processes": other_processes,
                    }
                )
                pollution.set()
            previous_monotonic = monotonic
            record = {
                "captured_at_utc": utc_now(),
                "monotonic_s": monotonic,
                "outer_pid": outer_pid,
                "observed_worker_pid": pid,
                "system_cpu_percent": total_cpu,
                "other_cpu_processes": other_processes,
                "runtime_pollution_reasons": reasons,
                "runtime_pollution_triggered": triggered,
                **captured,
                **process,
            }
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            stop.wait(0.75)


def _wait_worker_pid(attempt: Path, process: subprocess.Popen[str]) -> int:
    identity = attempt / "worker_identity.json"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and process.poll() is None:
        if identity.is_file():
            try:
                return int(strict_json(identity)["pid"])
            except (P0LiteError, KeyError, TypeError, ValueError):
                pass
        time.sleep(0.05)
    return process.pid


def _start_pidstat(pid: int, output: Path) -> tuple[subprocess.Popen[str] | None, Any]:
    stream = output.open("x", encoding="utf-8")
    try:
        process = subprocess.Popen(
            ["pidstat", "-h", "-u", "-r", "-t", "-p", str(pid), "1"],
            text=True,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    except OSError:
        stream.write("pidstat unavailable\n")
        stream.flush()
        return None, stream
    return process, stream


def _stop_pidstat(process: subprocess.Popen[str] | None, stream: Any) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
    stream.close()


def _worker_command(kind: str, attempt: Path) -> list[str]:
    base = [str(GPU_PYTHON), str(WORKER), "--mode", "run", "--attempt", str(attempt)]
    if kind == "PROFILE_HOST":
        return [
            str(GPU_PYTHON),
            "-m",
            "cProfile",
            "-o",
            str(attempt / "host_profile.pstats"),
            str(WORKER),
            "--mode",
            "run",
            "--attempt",
            str(attempt),
        ]
    if kind == "PROFILE_CUDA":
        return nsys_profile_command(attempt / "cuda_profile", base)
    return base


def _successful_payloads(benchmark: str, runs_root: Path, current: Path) -> list[Mapping[str, Any]]:
    payloads: list[Mapping[str, Any]] = []
    if runs_root.is_dir():
        for attempt in sorted(runs_root.glob("*/*")):
            if attempt == current or not attempt.is_dir() or attempt.is_symlink():
                continue
            status = strict_json(attempt / "status.json")
            manifest = strict_json(attempt / "run_manifest.json")
            if (
                status.get("status") == "SUCCESS"
                and manifest.get("slot", {}).get("benchmark") == benchmark
            ):
                validated = validate_success_artifacts(attempt)
                payloads.append(validated["payload"])
    current_payload = strict_json(current / "numerical_payload.json")
    if not isinstance(current_payload, Mapping):
        raise P0LiteError("current numerical payload is not an object")
    return [*payloads, current_payload]


def _has_success(slot_id: str, runs_root: Path) -> bool:
    root = runs_root / slot_id
    if not root.is_dir():
        return False
    attempts = sorted(root.iterdir())
    for attempt in attempts:
        status = strict_json(attempt / "status.json")
        if status.get("status") == "SUCCESS":
            try:
                validate_success_artifacts(attempt)
            except P0LiteError as exc:
                raise P0LiteError(f"existing SUCCESS attempt is corrupt: {attempt}: {exc}") from exc
            return True
    if attempts:
        latest_status = strict_json(attempts[-1] / "status.json")
        if latest_status.get("status") == "FAILED" and not str(
            latest_status.get("reason", "")
        ).startswith("PROFILER_FAILURE:"):
            raise P0LiteError(
                f"slot {slot_id} has a non-retryable failed attempt; fix the cause before continuing"
            )
    return False


def execute_slot(
    slot_id: str,
    *,
    baseline_commit: str,
    runs_root: Path = RUNS_ROOT,
    environment_override: Mapping[str, Any] | None = None,
    background_cpu_override: Sequence[float] | None = None,
) -> tuple[str, Path]:
    """Execute one immutable attempt; test overrides never bypass gate evaluation."""

    if _has_success(slot_id, runs_root):
        raise P0LiteError(f"slot {slot_id} already has a verified SUCCESS attempt")
    slot = slot_record(slot_id)
    attempt = new_attempt_directory(slot_id, commit=baseline_commit, runs_root=runs_root)
    config, config_identity = effective_config(slot["benchmark"])
    write_json(attempt / "effective_config.json", config)

    background = (
        list(background_cpu_override)
        if background_cpu_override is not None
        else sample_background_cpu()
        if slot["kind"] == "TIMED"
        else None
    )
    snapshot = (
        dict(environment_override)
        if environment_override is not None
        else capture_environment(baseline_commit)
    )
    audit = evaluate_environment(snapshot, background_cpu_percent=background)
    write_json(attempt / "telemetry" / "preflight.json", audit)
    software = software_environment()
    manifest = strict_json(attempt / "run_manifest.json")
    manifest.update(
        {
            "effective_config_identity": config_identity,
            "software_environment": software,
            "environment_id": environment_id(snapshot, software),
            "preflight_gate_pass": audit["gate_pass"],
        }
    )
    write_json(attempt / "run_manifest.json", manifest)
    if not audit["gate_pass"]:
        (attempt / "archive").mkdir(exist_ok=False)
        invalid_reason = "environment gate failed: " + ", ".join(
            name for name, passed in audit["gates"].items() if not passed
        )
        write_json(
            attempt / "quality.json",
            {"evaluated": False, "gate_pass": False, "reason": invalid_reason},
        )
        write_status(attempt, "INVALID_ENVIRONMENT", invalid_reason)
        manifest["execution"] = {"status": "INVALID_ENVIRONMENT", "ended_at_utc": utc_now()}
        write_json(attempt / "run_manifest.json", manifest)
        return "INVALID_ENVIRONMENT", attempt

    command = _worker_command(slot["kind"], attempt)
    child_environment = dict(os.environ)
    child_environment["PYTHONUNBUFFERED"] = "1"
    child_environment["P0LITE_PROFILE_KIND"] = slot["kind"]
    started_utc = utc_now()
    started = time.perf_counter()
    interrupted = False
    runtime_invalid = False
    pollution = threading.Event()
    pollution_details: list[dict[str, Any]] = []
    with (attempt / "stdout.log").open("w", encoding="utf-8") as stdout, (
        attempt / "stderr.log"
    ).open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            command,
            cwd=HERE,
            env=child_environment,
            text=True,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        stop = threading.Event()
        monitor = threading.Thread(
            target=_monitor,
            args=(
                process.pid,
                attempt,
                stop,
                pollution,
                pollution_details,
                attempt / "telemetry" / "runtime.jsonl",
            ),
            daemon=True,
        )
        monitor.start()
        worker_pid = _wait_worker_pid(attempt, process)
        pidstat, pidstat_stream = _start_pidstat(
            worker_pid, attempt / "telemetry" / "pidstat.log"
        )
        try:
            while True:
                try:
                    exit_code = process.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if pollution.is_set():
                        runtime_invalid = True
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            exit_code = process.wait(timeout=30.0)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            exit_code = process.wait(timeout=10.0)
                        break
        except KeyboardInterrupt:
            interrupted = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                exit_code = process.wait(timeout=30.0)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                exit_code = process.wait(timeout=10.0)
        finally:
            stop.set()
            monitor.join(timeout=5.0)
            _stop_pidstat(pidstat, pidstat_stream)
    wall = time.perf_counter() - started
    write_json(
        attempt / "telemetry" / "runtime_environment.json",
        {
            "gate_pass": not runtime_invalid,
            "pollution_detected": runtime_invalid,
            "details": pollution_details,
        },
    )

    profiler_error: str | None = None
    if exit_code == 0 and slot["kind"] == "PROFILE_CUDA":
        report = attempt / "cuda_profile.nsys-rep"
        try:
            if not report.is_file():
                raise P0LiteError("Nsight report was not created")
            export_nsys_reports(attempt, report)
        except P0LiteError as exc:
            profiler_error = str(exc)
    if exit_code == 0 and slot["kind"] == "PROFILE_HOST":
        profile = attempt / "host_profile.pstats"
        if not profile.is_file() or profile.stat().st_size == 0:
            profiler_error = "cProfile output was not created"

    status = "FAILED"
    reason: str | None = None
    if runtime_invalid:
        status = "INVALID_ENVIRONMENT"
        reason = "runtime background pollution detected"
        write_json(
            attempt / "quality.json",
            {"evaluated": False, "gate_pass": False, "reason": reason},
        )
    elif interrupted or exit_code == 130:
        status = "INTERRUPTED"
        reason = "launcher or worker was interrupted"
    elif exit_code != 0:
        reason = f"worker exit code {exit_code}"
        error_path = attempt / "worker_error.json"
        if error_path.is_file():
            error = strict_json(error_path)
            reason = f"{error.get('category')}: {error.get('message')}"
    elif profiler_error is not None:
        reason = f"PROFILER_FAILURE: {profiler_error}"
    else:
        try:
            validate_success_artifacts(attempt)
            comparison = compare_payloads(
                _successful_payloads(slot["benchmark"], runs_root, attempt)
            )
            write_json(attempt / "numerical_consistency.json", comparison)
            if not comparison["gate_pass"]:
                raise P0LiteError("NONDETERMINISTIC_PAYLOAD: benchmark payload differs")
        except P0LiteError as exc:
            reason = str(exc)
        else:
            status = "SUCCESS"
    if not (attempt / "archive").exists():
        (attempt / "archive").mkdir()
    write_status(attempt, status, reason)
    manifest = strict_json(attempt / "run_manifest.json")
    manifest["execution"] = {
        "status": status,
        "command": command,
        "outer_pid": process.pid,
        "worker_pid": worker_pid,
        "started_at_utc": started_utc,
        "ended_at_utc": utc_now(),
        "end_to_end_wall_s": wall,
        "exit_code": exit_code,
        "reason": reason,
    }
    write_json(attempt / "run_manifest.json", manifest)
    return status, attempt


def run_schedule(*, baseline_commit: str, runs_root: Path = RUNS_ROOT) -> int:
    for slot in load_schedule()["slots"]:
        slot_id = str(slot["slot_id"])
        if _has_success(slot_id, runs_root):
            continue
        status, attempt = execute_slot(
            slot_id, baseline_commit=baseline_commit, runs_root=runs_root
        )
        print(f"{slot_id}: {status} ({attempt})", flush=True)
        if status != "SUCCESS":
            return 2
    return 0


def zero_step_validation(output_root: Path) -> dict[str, Any]:
    if output_root.exists() or output_root.is_symlink():
        raise P0LiteError("zero-step validation output must not pre-exist")
    output_root.mkdir(parents=True)
    records: dict[str, Any] = {}
    for benchmark in BENCHMARK_SOURCES:
        root = output_root / benchmark
        root.mkdir()
        config, identity = effective_config(benchmark)
        config_path = root / "effective_config.json"
        result_path = root / "zero_step.json"
        write_json(config_path, config)
        command = [
            str(GPU_PYTHON),
            str(WORKER),
            "--mode",
            "zero-step",
            "--config",
            str(config_path),
            "--output",
            str(result_path),
        ]
        completed = subprocess.run(command, cwd=HERE, text=True, capture_output=True, check=False)
        (root / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (root / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise P0LiteError(f"{benchmark} zero-step worker failed: {completed.stderr}")
        result = strict_json(result_path)
        if (
            result.get("hf3d_time_steps_advanced") != 0
            or result.get("cuda_selected") != "cuda"
            or result.get("cuda_fallback") is not False
            or result.get("formal_roundtrip_exact") is not True
        ):
            raise P0LiteError(f"{benchmark} zero-step gate failed")
        records[benchmark] = {"identity": identity, "result": result, "command": command}

    probe = output_root / "nsys_probe"
    probe.mkdir()
    (probe / "telemetry").mkdir()
    probe_result = probe / "probe.json"
    command = nsys_profile_command(
        probe / "minimal",
        [
            str(GPU_PYTHON),
            str(WORKER),
            "--mode",
            "nsys-probe",
            "--output",
            str(probe_result),
        ],
    )
    completed = subprocess.run(command, cwd=HERE, text=True, capture_output=True, check=False)
    (probe / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (probe / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    report = probe / "minimal.nsys-rep"
    if completed.returncode != 0 or not report.is_file():
        raise P0LiteError(f"minimal Nsight probe failed: {completed.stderr}")
    nsys_summary = export_nsys_reports(probe, report)
    probe_payload = strict_json(probe_result)
    if probe_payload.get("completed") is not True or not nsys_summary["kernels"]:
        raise P0LiteError("minimal Nsight probe did not capture CUDA work")
    report_payload = {
        "schema_version": 1,
        "status": "P0LITE_STAGE1_ZERO_STEP_PASS",
        "git_commit": git_commit(),
        "benchmarks": records,
        "nsys_version": subprocess.run(
            [str(NSYS), "--version"], text=True, capture_output=True, check=False
        ).stdout.strip(),
        "nsys_probe": {"payload": probe_payload, "summary": nsys_summary, "command": command},
        "hf3d_time_steps_advanced": 0,
        "dynamic_attempts_started": 0,
    }
    write_json(output_root / "validation.json", report_payload)
    return report_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--slot")
    group.add_argument("--run-schedule", action="store_true")
    group.add_argument("--zero-step-all", type=Path)
    parser.add_argument("--baseline-commit")
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.zero_step_all is not None:
            zero_step_validation(args.zero_step_all.resolve())
            print(f"zero-step validation PASS: {args.zero_step_all}")
            return 0
        baseline = args.baseline_commit or git_commit()
        if len(baseline) != 40:
            raise P0LiteError("baseline commit must be a full 40-character object ID")
        if args.slot is not None:
            status, attempt = execute_slot(
                args.slot, baseline_commit=baseline, runs_root=args.runs_root.resolve()
            )
            print(f"{args.slot}: {status} ({attempt})")
            return 0 if status == "SUCCESS" else 2
        return run_schedule(baseline_commit=baseline, runs_root=args.runs_root.resolve())
    except P0LiteError as exc:
        print(f"P0-Lite launcher failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
