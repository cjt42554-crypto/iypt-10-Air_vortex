"""Read-only host and GPU environment gates for P0-P2 preflight."""

from __future__ import annotations

import csv
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from p0p2_contract import P0P2Error

EXPECTED_CPU_COUNT = 32
CPU_TCTL_MAX_C = 90.0
GPU_TEMPERATURE_MAX_C = 75.0
BACKGROUND_CPU_MAX_PERCENT = 25.0
BACKGROUND_OBSERVATION_S = 30.0

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


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict").strip()


def _strict_float(value: str) -> float:
    parsed = float(value.strip())
    if not (-1.0e300 < parsed < 1.0e300):
        raise ValueError("non-finite telemetry value")
    return parsed


def process_identity(pid: int) -> ProcessSample:
    root = Path("/proc") / str(pid)
    raw = _read_text(root / "stat")
    close = raw.rfind(")")
    if close < 0:
        raise P0P2Error(f"invalid /proc stat for PID {pid}")
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        raise P0P2Error(f"short /proc stat for PID {pid}")
    command = (
        (root / "cmdline")
        .read_bytes()
        .replace(b"\0", b" ")
        .strip()
        .decode("utf-8", errors="replace")
    )
    try:
        executable: str | None = os.readlink(root / "exe")
    except OSError:
        executable = None
    return ProcessSample(
        pid=pid,
        start_ticks=int(fields[19]),
        cpu_ticks=int(fields[11]) + int(fields[12]),
        command=command,
        executable=executable,
    )


def process_snapshot() -> dict[int, ProcessSample]:
    samples: dict[int, ProcessSample] = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        pid = int(path.name)
        try:
            samples[pid] = process_identity(pid)
        except (OSError, UnicodeError, ValueError, P0P2Error):
            continue
    return samples


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
    ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
    rows: list[dict[str, Any]] = []
    for pid, finish in end.items():
        begin = start.get(pid)
        if begin is None or begin.start_ticks != finish.start_ticks:
            continue
        delta = finish.cpu_ticks - begin.cpu_ticks
        if delta < 0:
            continue
        percent = 100.0 * delta / (ticks_per_second * elapsed_s)
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
    rows.sort(key=lambda item: -float(item["cpu_percent_one_logical_cpu_basis"]))
    background = [item for item in rows if not item["excluded"]]
    other_compute = [
        item
        for item in background
        if item["compute_name_match"]
        and float(item["cpu_percent_one_logical_cpu_basis"]) >= 5.0
    ]
    return {
        "elapsed_s": elapsed_s,
        "clock_ticks_per_s": ticks_per_second,
        "background_cpu_percent": sum(
            float(item["cpu_percent_one_logical_cpu_basis"]) for item in background
        ),
        "other_compute_processes": other_compute,
        "top_processes": rows[:64],
    }


def online_cpu_count() -> int:
    count = 0
    for token in _read_text(Path("/sys/devices/system/cpu/online")).split(","):
        if "-" in token:
            lower, upper = (int(value) for value in token.split("-", 1))
            count += upper - lower + 1
        else:
            int(token)
            count += 1
    return count


def cpu_governors() -> dict[str, str]:
    return {
        path.parent.parent.name: _read_text(path)
        for path in sorted(
            Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor")
        )
    }


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


def _run_csv(command: list[str]) -> list[list[str]]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise P0P2Error(f"telemetry command failed: {detail}")
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
        raise P0P2Error("nvidia-smi GPU snapshot shape is invalid")
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
    return {
        "captured_monotonic_ns": time.monotonic_ns(),
        "online_logical_cpus": online_cpu_count(),
        "governors": cpu_governors(),
        "ac_online": ac_online_records(),
        "tctl_sources": tctl_sources(),
        "gpu": gpu_snapshot(),
    }


def evaluate_instantaneous_environment(
    snapshot: Mapping[str, Any], *, excluded_pids: Iterable[int] = ()
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
    background_duration_s: float = BACKGROUND_OBSERVATION_S,
    sleep: Callable[[float], None] = time.sleep,
    snapshot_fn: Callable[[], dict[str, Any]] = instantaneous_environment,
    process_snapshot_fn: Callable[[], dict[int, ProcessSample]] = process_snapshot,
) -> dict[str, Any]:
    """Evaluate one unified environment gate without creating workspace output."""

    if background_duration_s < 0.0:
        raise ValueError("background_duration_s must be non-negative")
    excluded = ancestor_pids()
    first = evaluate_instantaneous_environment(snapshot_fn(), excluded_pids=excluded)
    if not first["gate_pass"]:
        return {
            "gate_pass": False,
            "stage": "instantaneous_rejection",
            "instantaneous_start": first,
            "instantaneous_end": None,
            "background": None,
            "background_gates": None,
        }
    start = process_snapshot_fn()
    start_ns = time.monotonic_ns()
    sleep(background_duration_s)
    end_ns = time.monotonic_ns()
    end = process_snapshot_fn()
    elapsed = (end_ns - start_ns) / 1.0e9
    if background_duration_s == 0.0 and elapsed <= 0.0:
        elapsed = 1.0e-9
    background = cpu_delta_report(start, end, elapsed_s=elapsed, excluded_pids=excluded)
    second = evaluate_instantaneous_environment(snapshot_fn(), excluded_pids=excluded)
    minimum = BACKGROUND_OBSERVATION_S if background_duration_s else 0.0
    background_gates = {
        "background_window_at_least_required_duration": elapsed >= minimum,
        "background_cpu_at_most_25_percent": float(background["background_cpu_percent"])
        <= BACKGROUND_CPU_MAX_PERCENT,
        "no_other_compute_process": not background["other_compute_processes"],
    }
    return {
        "gate_pass": first["gate_pass"]
        and second["gate_pass"]
        and all(background_gates.values()),
        "stage": "complete",
        "instantaneous_start": first,
        "instantaneous_end": second,
        "background": background,
        "background_gates": background_gates,
    }


__all__ = [
    "BACKGROUND_OBSERVATION_S",
    "ProcessSample",
    "ancestor_pids",
    "cpu_delta_report",
    "evaluate_instantaneous_environment",
    "full_environment_gate",
    "instantaneous_environment",
    "process_snapshot",
]
