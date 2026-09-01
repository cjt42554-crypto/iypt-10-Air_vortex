"""Fail-closed P0-P environment gates and telemetry parsers."""

from __future__ import annotations

import csv
import itertools
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from p0p2v2_contract import P0PerformanceError

EXPECTED_CPU_COUNT = 32
CPU_TCTL_MAX_C = 90.0
GPU_TEMPERATURE_MAX_C = 75.0
BACKGROUND_CPU_MAX_PERCENT = 25.0
TELEMETRY_COVERAGE_MIN = 0.95
TELEMETRY_MAX_GAP_S = 2.0
_COMPUTE_NAMES = re.compile(
    r"(?:python|node|julia|matlab|octave|blender|ffmpeg|vasp|lammps|"
    r"gromacs|namd|airvortex|cuda|simulation|solver|benchmark)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ProcessSample:
    pid: int
    start_ticks: int
    cpu_ticks: int
    command: str
    executable: str | None
    parent_pid: int = -1


def _strict_float(value: str) -> float:
    parsed = float(value.strip())
    if not (-1.0e300 < parsed < 1.0e300):
        raise ValueError("non-finite telemetry value")
    return parsed


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict").strip()


def process_identity(pid: int) -> ProcessSample:
    root = Path("/proc") / str(pid)
    raw = _read_text(root / "stat")
    close = raw.rfind(")")
    if close < 0:
        raise P0PerformanceError(f"invalid /proc stat for PID {pid}")
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        raise P0PerformanceError(f"short /proc stat for PID {pid}")
    command_bytes = (root / "cmdline").read_bytes().replace(b"\0", b" ").strip()
    command = command_bytes.decode("utf-8", errors="replace")
    executable: str | None
    try:
        executable = os.readlink(root / "exe")
    except OSError:
        executable = None
    return ProcessSample(
        pid=pid,
        start_ticks=int(fields[19]),
        cpu_ticks=int(fields[11]) + int(fields[12]),
        command=command,
        executable=executable,
        parent_pid=int(fields[1]),
    )


def process_snapshot() -> dict[int, ProcessSample]:
    samples: dict[int, ProcessSample] = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        pid = int(path.name)
        try:
            samples[pid] = process_identity(pid)
        except (OSError, UnicodeError, ValueError, P0PerformanceError):
            continue
    return samples


def process_snapshot_payload() -> list[dict[str, Any]]:
    """Return one JSON-safe, PID-sorted snapshot for the background monitor."""

    return [asdict(value) for _pid, value in sorted(process_snapshot().items())]


def process_snapshot_from_payload(
    value: object,
) -> dict[int, ProcessSample]:
    if not isinstance(value, list):
        raise P0PerformanceError("background process snapshot is not a list")
    output: dict[int, ProcessSample] = {}
    expected = {field.name for field in ProcessSample.__dataclass_fields__.values()}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != expected:
            raise P0PerformanceError(
                f"background process snapshot row schema differs: {index}"
            )
        try:
            sample = ProcessSample(
                pid=int(item["pid"]),
                start_ticks=int(item["start_ticks"]),
                cpu_ticks=int(item["cpu_ticks"]),
                command=str(item["command"]),
                executable=(
                    str(item["executable"])
                    if item["executable"] is not None
                    else None
                ),
                parent_pid=int(item["parent_pid"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise P0PerformanceError(
                f"background process snapshot row is invalid: {index}"
            ) from exc
        if sample.pid <= 0 or sample.pid in output:
            raise P0PerformanceError("background process snapshot PID is invalid")
        output[sample.pid] = sample
    return output


def system_cpu_snapshot() -> dict[str, int]:
    """Capture aggregate Linux CPU ticks, retaining busy and total identities."""

    line = _read_text(Path("/proc/stat")).splitlines()[0]
    fields = line.split()
    if not fields or fields[0] != "cpu" or len(fields) < 9:
        raise P0PerformanceError("aggregate /proc/stat CPU record is invalid")
    try:
        values = [int(value) for value in fields[1:]]
    except ValueError as exc:
        raise P0PerformanceError("aggregate /proc/stat CPU ticks are invalid") from exc
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return {"total_ticks": total, "busy_ticks": total - idle}


def background_snapshot() -> dict[str, Any]:
    """Capture process and aggregate CPU state in one monitor record."""

    return {
        "processes": process_snapshot_payload(),
        "system_cpu": system_cpu_snapshot(),
    }


def ancestor_pids(pid: int | None = None) -> set[int]:
    current = os.getpid() if pid is None else pid
    result: set[int] = set()
    while current > 1 and current not in result:
        result.add(current)
        try:
            raw = _read_text(Path("/proc") / str(current) / "stat")
            close = raw.rfind(")")
            fields = raw[close + 2 :].split()
            current = int(fields[1])
        except (OSError, ValueError, IndexError):
            break
    return result


def cpu_delta_report(
    start: Mapping[int, ProcessSample],
    end: Mapping[int, ProcessSample],
    *,
    elapsed_s: float,
    excluded_pids: Iterable[int],
) -> dict[str, Any]:
    if elapsed_s <= 0.0:
        raise ValueError("elapsed_s must be positive")
    excluded = set(excluded_pids)
    changed = True
    while changed:
        changed = False
        for sample in end.values():
            if sample.parent_pid in excluded and sample.pid not in excluded:
                excluded.add(sample.pid)
                changed = True
    clock_ticks = os.sysconf("SC_CLK_TCK")
    rows: list[dict[str, Any]] = []
    for pid, finish in end.items():
        begin = start.get(pid)
        if begin is None or begin.start_ticks != finish.start_ticks:
            continue
        delta = finish.cpu_ticks - begin.cpu_ticks
        if delta < 0:
            continue
        percent = 100.0 * delta / (clock_ticks * elapsed_s)
        rows.append(
            {
                **asdict(finish),
                "cpu_delta_ticks": delta,
                "cpu_percent_one_logical_cpu_basis": percent,
                "excluded": pid in excluded,
                "compute_name_match": bool(
                    _COMPUTE_NAMES.search(f"{finish.command} {finish.executable or ''}")
                ),
            }
        )
    rows.sort(key=lambda row: -float(row["cpu_percent_one_logical_cpu_basis"]))
    background = [row for row in rows if not row["excluded"]]
    background_percent = sum(
        float(row["cpu_percent_one_logical_cpu_basis"]) for row in background
    )
    other_compute = [
        row
        for row in background
        if row["compute_name_match"]
        and float(row["cpu_percent_one_logical_cpu_basis"]) >= 5.0
    ]
    return {
        "elapsed_s": elapsed_s,
        "clock_ticks_per_s": clock_ticks,
        "background_cpu_percent": background_percent,
        "other_compute_processes": other_compute,
        "top_processes": rows[:64],
        "excluded_pids_with_descendants": sorted(excluded),
        "excluded_cpu_delta_ticks": sum(
            int(row["cpu_delta_ticks"]) for row in rows if row["excluded"]
        ),
    }


def _system_background_cpu_percent(
    start: Mapping[str, Any],
    end: Mapping[str, Any],
    *,
    elapsed_s: float,
    excluded_process_cpu_ticks: int,
) -> float:
    try:
        busy_delta = int(end["busy_ticks"]) - int(start["busy_ticks"])
        total_delta = int(end["total_ticks"]) - int(start["total_ticks"])
    except (KeyError, TypeError, ValueError) as exc:
        raise P0PerformanceError("background system CPU snapshot is invalid") from exc
    if busy_delta < 0 or total_delta <= 0:
        raise P0PerformanceError("background system CPU ticks did not advance")
    clock_ticks = os.sysconf("SC_CLK_TCK")
    return 100.0 * max(0, busy_delta - excluded_process_cpu_ticks) / (
        clock_ticks * elapsed_s
    )


def online_cpu_count() -> int:
    text = _read_text(Path("/sys/devices/system/cpu/online"))
    count = 0
    for token in text.split(","):
        if "-" in token:
            lower, upper = (int(item) for item in token.split("-", 1))
            count += upper - lower + 1
        else:
            int(token)
            count += 1
    return count


def cpu_model() -> str:
    for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("model name") and ":" in line:
            return line.split(":", 1)[1].strip()
    raise P0PerformanceError("CPU model is unavailable")


def cpu_governors() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(
        Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor")
    ):
        result[path.parent.parent.name] = _read_text(path)
    return result


def ac_online_records() -> dict[str, int]:
    records: dict[str, int] = {}
    for path in sorted(Path("/sys/class/power_supply").glob("*/online")):
        try:
            records[path.parent.name] = int(_read_text(path))
        except (OSError, ValueError):
            continue
    return records


def tctl_sources() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            chip = _read_text(hwmon / "name")
        except OSError:
            chip = "unknown"
        for label_path in sorted(hwmon.glob("temp*_label")):
            try:
                label = _read_text(label_path)
            except OSError:
                continue
            if label.casefold() != "tctl":
                continue
            input_path = label_path.with_name(
                label_path.name.replace("_label", "_input")
            )
            try:
                temperature = float(_read_text(input_path)) / 1000.0
            except (OSError, ValueError):
                continue
            records.append(
                {
                    "chip": chip,
                    "label": label,
                    "temperature_c": temperature,
                    "label_path": str(label_path.resolve()),
                    "input_path": str(input_path.resolve()),
                }
            )
    return records


def _run_csv(command: Sequence[str]) -> list[list[str]]:
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise P0PerformanceError(f"telemetry command failed: {detail}")
    return [row for row in csv.reader(completed.stdout.splitlines()) if row]


GPU_QUERY_FIELDS = (
    "timestamp",
    "name",
    "uuid",
    "driver_version",
    "temperature.gpu",
    "utilization.gpu",
    "utilization.memory",
    "memory.total",
    "memory.used",
    "clocks.current.sm",
    "clocks.current.memory",
    "pstate",
    "power.draw",
    "power.limit",
    "clocks_throttle_reasons.active",
)


def gpu_snapshot() -> dict[str, Any]:
    rows = _run_csv(
        [
            "nvidia-smi",
            f"--query-gpu={','.join(GPU_QUERY_FIELDS)}",
            "--format=csv,noheader,nounits",
        ]
    )
    if len(rows) != 1 or len(rows[0]) != len(GPU_QUERY_FIELDS):
        raise P0PerformanceError("nvidia-smi GPU snapshot shape is invalid")
    values = {key: value.strip() for key, value in zip(GPU_QUERY_FIELDS, rows[0])}
    compute_rows = _run_csv(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    compute: list[dict[str, Any]] = []
    for row in compute_rows:
        if len(row) != 3:
            continue
        try:
            compute.append(
                {
                    "pid": int(row[0].strip()),
                    "process_name": row[1].strip(),
                    "used_gpu_memory_mib": _strict_float(row[2]),
                }
            )
        except ValueError:
            continue
    return {"gpu": values, "compute_processes": compute}


def instantaneous_environment() -> dict[str, Any]:
    governors = cpu_governors()
    tctl = tctl_sources()
    gpu = gpu_snapshot()
    ac = ac_online_records()
    return {
        "captured_monotonic_ns": time.monotonic_ns(),
        "online_logical_cpus": online_cpu_count(),
        "governors": governors,
        "ac_online": ac,
        "tctl_sources": tctl,
        "gpu": gpu,
    }


def evaluate_instantaneous_environment(
    snapshot: Mapping[str, Any],
    *,
    excluded_pids: Iterable[int] = (),
) -> dict[str, Any]:
    governors = dict(snapshot.get("governors", {}))
    tctl = list(snapshot.get("tctl_sources", []))
    gpu = dict(snapshot.get("gpu", {}))
    gpu_values = dict(gpu.get("gpu", {}))
    excluded = set(excluded_pids)
    other_gpu = [
        row
        for row in gpu.get("compute_processes", [])
        if int(row.get("pid", -1)) not in excluded
    ]
    try:
        gpu_temperature = _strict_float(str(gpu_values["temperature.gpu"]))
    except (KeyError, ValueError):
        gpu_temperature = float("inf")
    tctl_max = max((float(row["temperature_c"]) for row in tctl), default=float("inf"))
    ac = dict(snapshot.get("ac_online", {}))
    gates = {
        "online_logical_cpus_32": snapshot.get("online_logical_cpus")
        == EXPECTED_CPU_COUNT,
        "governor_records_complete": len(governors) == EXPECTED_CPU_COUNT,
        "governors_all_equal": len(set(governors.values())) == 1
        if governors
        else False,
        "ac_power_online": any(value == 1 for value in ac.values()),
        "reliable_tctl_available": len(tctl) == 1,
        "cpu_tctl_at_most_90_c": tctl_max <= CPU_TCTL_MAX_C,
        "gpu_temperature_at_most_75_c": gpu_temperature <= GPU_TEMPERATURE_MAX_C,
        "no_other_gpu_compute_process": not other_gpu,
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "cpu_tctl_max_c": tctl_max if tctl else None,
        "gpu_temperature_c": gpu_temperature
        if gpu_temperature != float("inf")
        else None,
        "other_gpu_compute_processes": other_gpu,
        "snapshot": dict(snapshot),
    }


def full_environment_gate(
    *,
    background_duration_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    environment_capture: Callable[[], dict[str, Any]] = instantaneous_environment,
    process_capture: Callable[[], dict[int, ProcessSample]] = process_snapshot,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> dict[str, Any]:
    """Evaluate frozen gates without creating the performance raw root."""

    excluded = ancestor_pids()
    first_environment = environment_capture()
    first_gate = evaluate_instantaneous_environment(
        first_environment, excluded_pids=excluded
    )
    if not first_gate["gate_pass"]:
        return {
            "gate_pass": False,
            "stage": "instantaneous_rejection",
            "instantaneous": first_gate,
            "background": None,
        }
    start = process_capture()
    start_ns = monotonic_ns()
    sleep(background_duration_s)
    end_ns = monotonic_ns()
    end = process_capture()
    elapsed = (end_ns - start_ns) / 1.0e9
    background = cpu_delta_report(
        start,
        end,
        elapsed_s=elapsed,
        excluded_pids=excluded,
    )
    second_gate = evaluate_instantaneous_environment(
        environment_capture(), excluded_pids=excluded
    )
    background_gates = {
        "background_window_at_least_30_s": elapsed >= 30.0,
        "background_cpu_at_most_25_percent": float(background["background_cpu_percent"])
        <= BACKGROUND_CPU_MAX_PERCENT,
        "no_other_compute_process": not background["other_compute_processes"],
    }
    return {
        "gate_pass": first_gate["gate_pass"]
        and second_gate["gate_pass"]
        and all(background_gates.values()),
        "stage": "complete",
        "instantaneous_start": first_gate,
        "instantaneous_end": second_gate,
        "background": background,
        "background_gates": background_gates,
    }


def nested_identity_mismatches(
    expected: object,
    observed: object,
    *,
    path: str = "$",
) -> list[dict[str, Any]]:
    """Return deterministic exact-identity differences without relaxing fields."""

    if isinstance(expected, Mapping) and isinstance(observed, Mapping):
        differences: list[dict[str, Any]] = []
        keys = sorted(set(expected) | set(observed), key=str)
        for key in keys:
            child = f"{path}.{key}"
            if key not in expected:
                differences.append(
                    {"path": child, "reason": "unexpected", "observed": observed[key]}
                )
            elif key not in observed:
                differences.append(
                    {"path": child, "reason": "missing", "expected": expected[key]}
                )
            else:
                differences.extend(
                    nested_identity_mismatches(expected[key], observed[key], path=child)
                )
        return differences
    if (
        isinstance(expected, Sequence)
        and not isinstance(expected, (str, bytes, bytearray))
        and isinstance(observed, Sequence)
        and not isinstance(observed, (str, bytes, bytearray))
    ):
        differences = []
        if len(expected) != len(observed):
            differences.append(
                {
                    "path": path,
                    "reason": "length",
                    "expected": len(expected),
                    "observed": len(observed),
                }
            )
        for index, (left, right) in enumerate(zip(expected, observed)):
            differences.extend(
                nested_identity_mismatches(left, right, path=f"{path}[{index}]")
            )
        return differences
    if type(expected) is not type(observed) or expected != observed:
        return [
            {
                "path": path,
                "reason": "value_or_type",
                "expected": expected,
                "observed": observed,
            }
        ]
    return []


def audit_ticket_machine_identity(
    expected: Mapping[str, Any], observed: Mapping[str, Any]
) -> dict[str, Any]:
    mismatches = nested_identity_mismatches(expected, observed)
    return {
        "schema_version": 2,
        "status": (
            "HF3D_P0P2_V2_TICKET_MACHINE_IDENTITY_EXACT"
            if not mismatches
            else "HF3D_P0P2_V2_TICKET_MACHINE_IDENTITY_DRIFT"
        ),
        "gate_pass": not mismatches,
        "mismatches": mismatches,
        "expected": dict(expected),
        "observed": dict(observed),
    }


def parse_nvidia_smi_csv(text: str) -> list[dict[str, str]]:
    rows = list(csv.DictReader(text.splitlines()))
    if not rows or rows[0] is None:
        raise P0PerformanceError("nvidia-smi telemetry CSV is empty")
    return [{str(key): str(value) for key, value in row.items()} for row in rows]


def parse_pidstat_blocks(text: str) -> list[dict[str, Any]]:
    """Parse -h pidstat sections while preserving each report's own columns."""

    header: list[str] | None = None
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith(("Linux ", "Average:")):
            continue
        parts = line.split()
        if "UID" in parts and any(token in parts for token in ("PID", "TGID")):
            header = parts
            continue
        if header is None or len(parts) < len(header):
            continue
        values = parts[: len(header) - 1] + [" ".join(parts[len(header) - 1 :])]
        row = dict(zip(header, values))
        pid_key = "TID" if "TID" in row else "PID"
        try:
            pid_value = int(row[pid_key])
        except (KeyError, ValueError):
            continue
        records.append(
            {
                "columns": row,
                "pid_or_tid": pid_value,
                "header": list(header),
                "source_line_number": line_number,
                "raw": raw_line,
            }
        )
    if not records:
        raise P0PerformanceError("pidstat telemetry contains no data records")
    return records


def telemetry_coverage(
    timestamps_ns: Sequence[int],
    *,
    target_start_ns: int,
    target_end_ns: int,
) -> dict[str, Any]:
    if target_end_ns <= target_start_ns:
        raise ValueError("target lifetime must be positive")
    ordered = sorted({int(value) for value in timestamps_ns})
    in_scope = [value for value in ordered if target_start_ns <= value <= target_end_ns]
    if len(in_scope) < 2:
        coverage = 0.0
        maximum_gap_s = (target_end_ns - target_start_ns) / 1.0e9
    else:
        covered_start = in_scope[0]
        covered_end = in_scope[-1]
        coverage = (covered_end - covered_start) / (target_end_ns - target_start_ns)
        boundaries = [target_start_ns, *in_scope, target_end_ns]
        maximum_gap_s = (
            max(right - left for left, right in itertools.pairwise(boundaries)) / 1.0e9
        )
    gates = {
        "coverage_at_least_95_percent": coverage >= TELEMETRY_COVERAGE_MIN,
        "maximum_gap_at_most_2_s": maximum_gap_s <= TELEMETRY_MAX_GAP_S,
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "coverage_fraction": coverage,
        "maximum_gap_s": maximum_gap_s,
        "sample_count": len(in_scope),
    }


def verify_pid_lifetime(
    expected: ProcessSample,
    observed: Sequence[ProcessSample],
) -> bool:
    return bool(observed) and all(
        item.pid == expected.pid and item.start_ticks == expected.start_ticks
        for item in observed
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def parse_monitor_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise P0PerformanceError("monitor JSONL must be a regular file")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line:
            raise P0PerformanceError(f"blank monitor JSONL line: {line_number}")
        try:
            value = json.loads(line, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            raise P0PerformanceError(
                f"invalid monitor JSONL at line {line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise P0PerformanceError("monitor JSONL record must be an object")
        records.append(value)
    if not records:
        raise P0PerformanceError("monitor JSONL is empty")
    return records


def audit_monitor_records(
    records: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    target_pid: int,
    target_start_ticks: int,
    target_start_ns: int,
    target_end_ns: int,
) -> dict[str, Any]:
    starts = [
        record for record in records if record.get("record_type") == "monitor_start"
    ]
    raw_samples = [
        record
        for record in records
        if record.get("record_type") in {"sample", "pidstat_line"}
    ]
    errors = [
        record for record in records if record.get("record_type") == "monitor_error"
    ]
    summaries = [
        record for record in records if record.get("record_type") == "monitor_summary"
    ]
    pidstat_rows: list[dict[str, Any]] = []
    if kind == "pidstat":
        pidstat_lines = [
            record for record in records if record.get("record_type") == "pidstat_line"
        ]
        pidstat_rows = parse_pidstat_blocks(pidstat_text_from_records(records))
        samples = [
            pidstat_lines[int(row["source_line_number"]) - 1] for row in pidstat_rows
        ]
    else:
        samples = [
            record for record in records if record.get("record_type") == "sample"
        ]
    identity_pass = all(
        record.get("target_pid") == target_pid
        and record.get("target_start_ticks") == target_start_ticks
        for record in records
    )
    timestamps = [
        int(record["monotonic_ns"])
        for record in samples
        if isinstance(record.get("monotonic_ns"), int)
    ]
    coverage = telemetry_coverage(
        timestamps,
        target_start_ns=target_start_ns,
        target_end_ns=target_end_ns,
    )
    start = starts[0] if len(starts) == 1 else None
    summary = summaries[0] if len(summaries) == 1 else None
    monitor_pid_consistent = (
        isinstance(start, Mapping)
        and isinstance(summary, Mapping)
        and isinstance(start.get("monitor_pid"), int)
        and start.get("monitor_pid") == summary.get("monitor_pid")
        and int(start["monitor_pid"]) > 0
        and int(start["monitor_pid"]) != target_pid
    )
    sample_count_consistent = False
    pidstat_target_rows = True
    pidstat_exit_clean = True
    if isinstance(summary, Mapping):
        if kind == "pidstat":
            sample_count_consistent = summary.get("line_count") == len(raw_samples)
            pidstat_target_rows = bool(pidstat_rows) and all(
                str(row["columns"].get("TGID", row["columns"].get("PID", "")))
                == str(target_pid)
                for row in pidstat_rows
            )
            pidstat_exit_clean = summary.get("pidstat_exit_code") in (0, -15)
        else:
            sample_count_consistent = summary.get("sample_count") == len(samples)
    timestamps_ordered = bool(timestamps) and timestamps == sorted(timestamps)
    start_precedes_samples = (
        isinstance(start, Mapping)
        and isinstance(start.get("monotonic_ns"), int)
        and bool(timestamps)
        and int(start["monotonic_ns"]) <= timestamps[0]
    )
    summary_follows_samples = (
        isinstance(summary, Mapping)
        and isinstance(summary.get("last_monotonic_ns"), int)
        and bool(timestamps)
        and int(summary["last_monotonic_ns"]) >= timestamps[-1]
    )
    derived: dict[str, Any] = {}
    kind_specific_gate = True
    if kind == "gpu":
        process_memory_mib: list[float] = []
        device_memory_mib: list[float] = []
        gpu_temperatures: list[float] = []
        other_compute: list[dict[str, Any]] = []
        throttle_states: list[str] = []
        complete_payloads = 0
        for record in samples:
            value = record.get("value")
            if not isinstance(value, Mapping):
                continue
            compute_processes = value.get("compute_processes")
            gpu_record = value.get("gpu")
            if not isinstance(compute_processes, list) or not isinstance(
                gpu_record, Mapping
            ):
                continue
            complete_payloads += 1
            for process_record in compute_processes:
                if (
                    isinstance(process_record, Mapping)
                    and process_record.get("pid") == target_pid
                ):
                    process_memory_mib.append(
                        float(process_record["used_gpu_memory_mib"])
                    )
                elif isinstance(process_record, Mapping):
                    other_compute.append(dict(process_record))
            try:
                device_memory_mib.append(_strict_float(str(gpu_record["memory.used"])))
                gpu_temperatures.append(
                    _strict_float(str(gpu_record["temperature.gpu"]))
                )
                throttle_states.append(
                    str(gpu_record["clocks_throttle_reasons.active"]).strip()
                )
            except (KeyError, ValueError):
                continue
        kind_specific_gate = (
            complete_payloads == len(samples)
            and bool(process_memory_mib)
            and len(device_memory_mib) == len(samples)
            and len(gpu_temperatures) == len(samples)
            and max(gpu_temperatures, default=float("inf")) <= GPU_TEMPERATURE_MAX_C
            and not other_compute
            and len(throttle_states) == len(samples)
        )
        derived = {
            "target_process_gpu_memory_sample_count": len(process_memory_mib),
            "target_process_gpu_memory_peak_mib": max(process_memory_mib, default=None),
            "target_process_gpu_memory_peak_bytes": int(
                max(process_memory_mib) * 1024 * 1024
            )
            if process_memory_mib
            else None,
            "device_memory_used_peak_mib": max(device_memory_mib, default=None),
            "gpu_temperature_peak_c": max(gpu_temperatures, default=None),
            "other_gpu_compute_processes": other_compute,
            "active_throttle_states": sorted(set(throttle_states)),
        }
    elif kind == "tctl":
        temperatures: list[float] = []
        complete_tctl_samples = 0
        for record in samples:
            value = record.get("value")
            if not (
                isinstance(value, list)
                and len(value) == 1
                and isinstance(value[0], Mapping)
                and str(value[0].get("label", "")).casefold() == "tctl"
                and isinstance(value[0].get("temperature_c"), (int, float))
            ):
                continue
            temperature = float(value[0]["temperature_c"])
            if not (-1.0e300 < temperature < 1.0e300):
                continue
            temperatures.append(temperature)
            complete_tctl_samples += 1
        kind_specific_gate = (
            complete_tctl_samples == len(samples)
            and bool(temperatures)
            and max(temperatures) <= CPU_TCTL_MAX_C
        )
        derived = {"cpu_tctl_peak_c": max(temperatures, default=None)}
    elif kind == "proc":
        vm_hwm = []
        for record in samples:
            value = record.get("value")
            if not isinstance(value, Mapping) or not isinstance(
                value.get("status"), str
            ):
                continue
            parsed = parse_proc_memory_text(value["status"])
            if "VmHWM" in parsed:
                vm_hwm.append(parsed["VmHWM"])
        kind_specific_gate = len(vm_hwm) == len(samples) and bool(vm_hwm)
        derived = {"process_vm_hwm_peak_bytes": max(vm_hwm, default=None)}
    gates = {
        "kind_consistent": all(record.get("kind") == kind for record in records),
        "target_identity_consistent": identity_pass,
        "no_monitor_error": not errors,
        "exactly_one_start": len(starts) == 1,
        "exactly_one_summary": len(summaries) == 1,
        "monitor_pid_consistent": monitor_pid_consistent,
        "sample_count_consistent": sample_count_consistent,
        "timestamps_monotonic": timestamps_ordered,
        "start_precedes_samples": start_precedes_samples,
        "summary_follows_samples": summary_follows_samples,
        "target_not_alive_when_monitor_stopped": isinstance(summary, Mapping)
        and summary.get("target_alive_at_stop") is False,
        "pidstat_rows_target_process": pidstat_target_rows,
        "pidstat_exit_clean": pidstat_exit_clean,
        "pidstat_stderr_empty": kind != "pidstat"
        or (
            isinstance(summary, Mapping)
            and not str(summary.get("pidstat_stderr", "")).strip()
        ),
        "kind_specific_payload_complete": kind_specific_gate,
        "telemetry_coverage": coverage["gate_pass"],
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "coverage": coverage,
        "sample_count": len(samples),
        "raw_sample_or_line_count": len(raw_samples),
        "pidstat_rows": pidstat_rows,
        "derived": derived,
        "errors": errors,
        "start": start,
        "summary": summary,
    }


def audit_background_records(
    records: Sequence[Mapping[str, Any]],
    *,
    target_pid: int,
    target_start_ticks: int,
    target_start_ns: int,
    target_end_ns: int,
    allowed_pids: Iterable[int],
) -> dict[str, Any]:
    """Independently reject process or aggregate CPU pollution for a sample."""

    base = audit_monitor_records(
        records,
        kind="background",
        target_pid=target_pid,
        target_start_ticks=target_start_ticks,
        target_start_ns=target_start_ns,
        target_end_ns=target_end_ns,
    )
    samples = [
        record for record in records if record.get("record_type") == "sample"
    ]
    start = base.get("start")
    monitor_pid = (
        int(start["monitor_pid"])
        if isinstance(start, Mapping) and isinstance(start.get("monitor_pid"), int)
        else -1
    )
    declared = (
        {int(value) for value in start.get("excluded_pids", [])}
        if isinstance(start, Mapping) and isinstance(start.get("excluded_pids"), list)
        else set()
    )
    expected_allowed = {int(value) for value in allowed_pids} | {monitor_pid}
    intervals: list[dict[str, Any]] = []
    for left, right in itertools.pairwise(samples):
        left_ns = left.get("monotonic_ns")
        right_ns = right.get("monotonic_ns")
        left_value = left.get("value")
        right_value = right.get("value")
        if not (
            isinstance(left_ns, int)
            and isinstance(right_ns, int)
            and right_ns > left_ns
            and isinstance(left_value, Mapping)
            and isinstance(right_value, Mapping)
        ):
            raise P0PerformanceError("background monitor interval is invalid")
        elapsed_s = (right_ns - left_ns) / 1.0e9
        first_processes = process_snapshot_from_payload(left_value.get("processes"))
        second_processes = process_snapshot_from_payload(right_value.get("processes"))
        process_report = cpu_delta_report(
            first_processes,
            second_processes,
            elapsed_s=elapsed_s,
            excluded_pids=expected_allowed,
        )
        excluded_ticks = int(process_report["excluded_cpu_delta_ticks"])
        system_percent = _system_background_cpu_percent(
            left_value.get("system_cpu", {}),
            right_value.get("system_cpu", {}),
            elapsed_s=elapsed_s,
            excluded_process_cpu_ticks=excluded_ticks,
        )
        intervals.append(
            {
                "start_monotonic_ns": left_ns,
                "end_monotonic_ns": right_ns,
                "elapsed_s": elapsed_s,
                "system_background_cpu_percent_one_logical_cpu_basis": system_percent,
                "process_report": process_report,
                "gate_pass": system_percent <= BACKGROUND_CPU_MAX_PERCENT
                and not process_report["other_compute_processes"],
            }
        )
    background_gates = {
        "declared_allowed_pids_exact": declared == expected_allowed - {monitor_pid},
        "at_least_two_intervals": len(intervals) >= 2,
        "all_intervals_background_cpu_at_most_25_percent": bool(intervals)
        and all(
            float(item["system_background_cpu_percent_one_logical_cpu_basis"])
            <= BACKGROUND_CPU_MAX_PERCENT
            for item in intervals
        ),
        "no_other_compute_process_during_sample": bool(intervals)
        and all(not item["process_report"]["other_compute_processes"] for item in intervals),
    }
    return {
        **base,
        "gate_pass": base["gate_pass"] and all(background_gates.values()),
        "background_gates": background_gates,
        "allowed_pids": sorted(expected_allowed),
        "intervals": intervals,
        "maximum_system_background_cpu_percent": max(
            (
                float(item["system_background_cpu_percent_one_logical_cpu_basis"])
                for item in intervals
            ),
            default=None,
        ),
        "other_compute_processes": [
            process
            for item in intervals
            for process in item["process_report"]["other_compute_processes"]
        ],
    }


def pidstat_text_from_records(records: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(
        str(record["raw"])
        for record in records
        if record.get("record_type") == "pidstat_line" and "raw" in record
    )


def parse_proc_memory_text(text: str) -> dict[str, int]:
    output: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        parts = raw.split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        if len(parts) > 1 and parts[1] == "kB":
            value *= 1024
        output[key] = value
    return output


__all__ = [
    "BACKGROUND_CPU_MAX_PERCENT",
    "CPU_TCTL_MAX_C",
    "EXPECTED_CPU_COUNT",
    "GPU_QUERY_FIELDS",
    "GPU_TEMPERATURE_MAX_C",
    "TELEMETRY_COVERAGE_MIN",
    "TELEMETRY_MAX_GAP_S",
    "ProcessSample",
    "ancestor_pids",
    "audit_background_records",
    "audit_monitor_records",
    "audit_ticket_machine_identity",
    "background_snapshot",
    "cpu_delta_report",
    "cpu_model",
    "evaluate_instantaneous_environment",
    "full_environment_gate",
    "gpu_snapshot",
    "instantaneous_environment",
    "nested_identity_mismatches",
    "parse_monitor_jsonl",
    "parse_nvidia_smi_csv",
    "parse_pidstat_blocks",
    "parse_proc_memory_text",
    "pidstat_text_from_records",
    "process_identity",
    "process_snapshot",
    "process_snapshot_from_payload",
    "process_snapshot_payload",
    "system_cpu_snapshot",
    "tctl_sources",
    "telemetry_coverage",
    "verify_pid_lifetime",
]
