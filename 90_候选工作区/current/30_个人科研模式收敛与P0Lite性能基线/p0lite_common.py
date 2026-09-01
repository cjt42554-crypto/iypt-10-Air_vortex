"""Small shared primitives for the P0-Lite HF3D baseline harness."""

from __future__ import annotations

import copy
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import sqlite3
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
FORMAL_ROOT = REPO_ROOT / "02_模拟程序"
SOURCE_CONFIG_ROOT = (
    REPO_ROOT
    / "90_候选工作区"
    / "current"
    / "07_D3跨网格时间步响应诊断"
    / "generated_configs"
)
GPU_PYTHON = (
    REPO_ROOT
    / "90_候选工作区"
    / "current"
    / "01_HF3D数值资格"
    / ".venv-gpu"
    / "bin"
    / "python"
)
NSYS = Path("/opt/nvidia/hpc_sdk/Linux_x86_64/26.5/compilers/bin/nsys")
SCHEDULE_PATH = HERE / "p0lite_schedule.json"
RUNS_ROOT = HERE / "runs"
REPORTS_ROOT = HERE / "reports"

BENCHMARK_SOURCES = {"B1": "G6_DT0", "B2": "G9_DT0", "B3": "G9_DT2"}
EXPECTED_REGISTERED_TIMES = [0.0, 0.001, 0.002, 0.003, 0.004, 0.005]
STATUSES = {
    "RUNNING",
    "SUCCESS",
    "FAILED",
    "INTERRUPTED",
    "INVALID_ENVIRONMENT",
}
NSYS_REPORTS = (
    "cuda_api_sum",
    "cuda_gpu_kern_sum",
    "cuda_gpu_mem_time_sum",
    "cuda_gpu_mem_size_sum",
)
NUMERICAL_HASH_DOMAIN = b"hf3d-p0lite-canonical-numerical-payload-v1\0"
THREAD_ENV_KEYS = (
    "AIRVORTEX_BLAS_THREADS",
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OMP_DYNAMIC",
    "CUDA_VISIBLE_DEVICES",
)


class P0LiteError(RuntimeError):
    """Fail-closed P0-Lite contract violation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise P0LiteError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                P0LiteError(f"non-finite JSON number: {value}")
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P0LiteError(f"cannot read strict JSON {path}: {exc}") from exc


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise P0LiteError(f"value is not strict JSON: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        raise P0LiteError(f"temporary JSON path already exists: {temporary}")
    try:
        temporary.write_bytes(canonical_json_bytes(value))
        os.replace(temporary, path)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise P0LiteError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def git_output(*arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise P0LiteError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def git_commit() -> str:
    value = git_output("rev-parse", "HEAD")
    if len(value) != 40:
        raise P0LiteError("Git HEAD is not a 40-character object ID")
    return value


def git_clean() -> bool:
    return not git_output("status", "--porcelain=v1", "--untracked-files=all")


def load_schedule() -> dict[str, Any]:
    raw = strict_json(SCHEDULE_PATH)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise P0LiteError("unsupported P0-Lite schedule")
    slots = raw.get("slots")
    if not isinstance(slots, list) or len(slots) != 16:
        raise P0LiteError("P0-Lite schedule must contain exactly 16 slots")
    identities: set[str] = set()
    for slot in slots:
        if not isinstance(slot, dict) or set(slot) != {"slot_id", "kind", "benchmark"}:
            raise P0LiteError("invalid P0-Lite slot record")
        slot_id = slot["slot_id"]
        if not isinstance(slot_id, str) or slot_id in identities:
            raise P0LiteError("P0-Lite slot IDs must be unique strings")
        identities.add(slot_id)
        if slot["kind"] not in {"WARMUP", "TIMED", "PROFILE_HOST", "PROFILE_CUDA"}:
            raise P0LiteError(f"invalid slot kind: {slot['kind']}")
        if slot["benchmark"] not in BENCHMARK_SOURCES:
            raise P0LiteError(f"invalid benchmark: {slot['benchmark']}")
    return raw


def slot_record(slot_id: str) -> dict[str, str]:
    matches = [slot for slot in load_schedule()["slots"] if slot["slot_id"] == slot_id]
    if len(matches) != 1:
        raise P0LiteError(f"unknown slot: {slot_id}")
    return {str(key): str(value) for key, value in matches[0].items()}


def effective_config(benchmark: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive and formally round-trip one benchmark by changing duration only."""

    if benchmark not in BENCHMARK_SOURCES:
        raise P0LiteError(f"unknown benchmark: {benchmark}")
    source_path = SOURCE_CONFIG_ROOT / f"{BENCHMARK_SOURCES[benchmark]}.json"
    source = strict_json(source_path)
    if not isinstance(source, dict):
        raise P0LiteError("source configuration is not a JSON object")
    derived = copy.deepcopy(source)
    run = derived.get("run")
    if not isinstance(run, dict) or run.get("duration_s") != 0.01:
        raise P0LiteError("source duration is not the frozen 0.010 s")
    run["duration_s"] = 0.005
    expected = copy.deepcopy(source)
    expected["run"]["duration_s"] = 0.005
    if derived != expected:
        raise P0LiteError("derived configuration changed more than run.duration_s")
    if derived.get("drive", {}).get("ramp_duration_s") != 0.005:
        raise P0LiteError("drive.ramp_duration_s is not frozen at 0.005 s")
    if run.get("output_interval_s") != 0.001:
        raise P0LiteError("run.output_interval_s is not frozen at 0.001 s")
    try:
        from airvortex.core import SimulationConfig

        config = SimulationConfig.from_dict(derived)
    except Exception as exc:
        raise P0LiteError(f"formal configuration construction failed: {exc}") from exc
    roundtrip = config.to_dict()
    if roundtrip != derived:
        raise P0LiteError("formal SimulationConfig round-trip is not exact")
    identity = {
        "benchmark": benchmark,
        "source_case": BENCHMARK_SOURCES[benchmark],
        "source_sha256": sha256_file(source_path),
        "effective_sha256": hashlib.sha256(canonical_json_bytes(derived)).hexdigest(),
        "only_change": "run.duration_s: 0.010 -> 0.005",
        "registered_times_s": EXPECTED_REGISTERED_TIMES,
    }
    return roundtrip, identity


def new_attempt_directory(
    slot_id: str,
    *,
    commit: str,
    runs_root: Path = RUNS_ROOT,
) -> Path:
    slot = slot_record(slot_id)
    slot_root = runs_root / slot_id
    existing = sorted(path for path in slot_root.iterdir()) if slot_root.is_dir() else []
    if any(path.is_symlink() or not path.is_dir() for path in existing):
        raise P0LiteError(f"invalid attempt entry below {slot_root}")
    if len(existing) >= 3:
        raise P0LiteError(f"slot {slot_id} already has three attempts")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    attempt = slot_root / f"{stamp}_{commit[:8]}_{os.getpid()}"
    attempt.mkdir(parents=True, exist_ok=False)
    (attempt / "telemetry").mkdir()
    (attempt / "stdout.log").touch(exist_ok=False)
    (attempt / "stderr.log").touch(exist_ok=False)
    write_json(
        attempt / "status.json",
        {"status": "RUNNING", "updated_at_utc": utc_now(), "reason": None},
    )
    write_json(
        attempt / "quality.json",
        {"evaluated": False, "gate_pass": False, "reason": "worker not completed"},
    )
    write_json(
        attempt / "run_manifest.json",
        {
            "schema_version": 1,
            "schedule_id": load_schedule()["schedule_id"],
            "slot": slot,
            "attempt_id": attempt.name,
            "baseline_commit": commit,
            "launcher_pid": os.getpid(),
            "created_at_utc": utc_now(),
            "execution": None,
        },
    )
    return attempt


def write_status(path: Path, status: str, reason: str | None = None) -> None:
    if status not in STATUSES:
        raise P0LiteError(f"invalid attempt status: {status}")
    write_json(
        path / "status.json",
        {"status": status, "updated_at_utc": utc_now(), "reason": reason},
    )


def _run_text(command: Sequence[str], timeout: float = 15.0) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            list(command),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"
    return completed.returncode, completed.stdout, completed.stderr


def _online_cpu_count() -> int:
    try:
        raw = Path("/sys/devices/system/cpu/online").read_text(encoding="ascii").strip()
        count = 0
        for item in raw.split(","):
            ends = item.split("-", 1)
            count += int(ends[-1]) - int(ends[0]) + 1
        return count
    except (OSError, ValueError):
        return int(os.cpu_count() or 0)


def _governors() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor")):
        try:
            values[path.parts[-3]] = path.read_text(encoding="ascii").strip()
        except OSError:
            values[path.parts[-3]] = "unavailable"
    return values


def _ac_online() -> dict[str, int]:
    values: dict[str, int] = {}
    for path in sorted(Path("/sys/class/power_supply").glob("*")):
        try:
            supply_type = (path / "type").read_text(encoding="ascii").strip()
            if supply_type == "Mains":
                values[path.name] = int((path / "online").read_text(encoding="ascii"))
        except (OSError, ValueError):
            continue
    return values


def _tctl_c() -> float | None:
    code, stdout, _ = _run_text(["sensors", "-j"])
    if code != 0:
        return None
    try:
        payload = json.loads(stdout)
        values = [
            float(group["Tctl"][key])
            for name, group in payload.items()
            if name.startswith("k10temp") and isinstance(group, dict) and "Tctl" in group
            for key in group["Tctl"]
            if key.endswith("_input")
        ]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return max(values) if values else None


def _gpu_snapshot() -> dict[str, Any]:
    fields = (
        "index",
        "name",
        "uuid",
        "driver_version",
        "temperature.gpu",
        "utilization.gpu",
        "utilization.memory",
        "clocks.sm",
        "clocks.mem",
        "memory.used",
        "memory.total",
        "power.draw",
    )
    code, stdout, stderr = _run_text(
        ["nvidia-smi", f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"]
    )
    if code != 0:
        return {"available": False, "error": stderr.strip(), "gpus": [], "compute_processes": []}
    rows = list(csv.reader(stdout.splitlines()))
    gpus = [
        {field: value.strip() for field, value in zip(fields, row, strict=True)}
        for row in rows
        if len(row) == len(fields)
    ]
    process_fields = ("pid", "process_name", "gpu_uuid", "used_gpu_memory")
    pcode, pout, perr = _run_text(
        [
            "nvidia-smi",
            f"--query-compute-apps={','.join(process_fields)}",
            "--format=csv,noheader,nounits",
        ]
    )
    processes = []
    if pcode == 0:
        for row in csv.reader(pout.splitlines()):
            if len(row) == len(process_fields):
                processes.append(
                    {field: value.strip() for field, value in zip(process_fields, row, strict=True)}
                )
    return {
        "available": True,
        "error": None if pcode == 0 else perr.strip(),
        "gpus": gpus,
        "compute_processes": processes,
    }


def _cpu_jiffies() -> tuple[int, int]:
    fields = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()[1:]
    values = [int(value) for value in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def runtime_sensor_snapshot() -> dict[str, Any]:
    """Capture the inexpensive host/GPU fields used during one live attempt."""

    return {"cpu_jiffies": _cpu_jiffies(), "cpu_tctl_c": _tctl_c(), "gpu": _gpu_snapshot()}


def sample_background_cpu(duration_s: float = 30.0, interval_s: float = 1.0) -> list[float]:
    if duration_s <= 0.0 or interval_s <= 0.0:
        raise P0LiteError("background CPU duration and interval must be positive")
    samples: list[float] = []
    total_before, idle_before = _cpu_jiffies()
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        time.sleep(min(interval_s, max(0.0, deadline - time.monotonic())))
        total_after, idle_after = _cpu_jiffies()
        delta = total_after - total_before
        idle = idle_after - idle_before
        samples.append(0.0 if delta <= 0 else 100.0 * (delta - idle) / delta)
        total_before, idle_before = total_after, idle_after
    return samples


def capture_environment(baseline_commit: str) -> dict[str, Any]:
    return {
        "captured_at_utc": utc_now(),
        "online_logical_cpus": _online_cpu_count(),
        "governors": _governors(),
        "ac_online": _ac_online(),
        "cpu_tctl_c": _tctl_c(),
        "gpu": _gpu_snapshot(),
        "git_head": git_commit(),
        "git_clean": git_clean(),
        "baseline_commit": baseline_commit,
    }


def evaluate_environment(
    snapshot: Mapping[str, Any],
    *,
    background_cpu_percent: Sequence[float] | None,
) -> dict[str, Any]:
    governors = snapshot.get("governors")
    governor_values = set(governors.values()) if isinstance(governors, Mapping) else set()
    ac = snapshot.get("ac_online")
    gpu = snapshot.get("gpu")
    gpus = gpu.get("gpus", []) if isinstance(gpu, Mapping) else []
    processes = gpu.get("compute_processes", []) if isinstance(gpu, Mapping) else []
    try:
        gpu_temperature = max(float(item["temperature.gpu"]) for item in gpus)
    except (KeyError, TypeError, ValueError):
        gpu_temperature = math.inf
    cpu_tctl = snapshot.get("cpu_tctl_c")
    background = list(background_cpu_percent) if background_cpu_percent is not None else []
    gates = {
        "background_cpu_continuous_le_25_percent": (
            background_cpu_percent is None
            or (bool(background) and max(background) <= 25.0)
        ),
        "no_other_gpu_compute_process": isinstance(processes, list) and not processes,
        "cpu_tctl_le_90_c": isinstance(cpu_tctl, (int, float)) and float(cpu_tctl) <= 90.0,
        "gpu_temperature_le_75_c": gpu_temperature <= 75.0,
        "ac_online": isinstance(ac, Mapping) and any(int(value) == 1 for value in ac.values()),
        "online_logical_cpus_eq_32": snapshot.get("online_logical_cpus") == 32,
        "all_governors_identical": bool(governor_values) and len(governor_values) == 1,
        "git_clean": snapshot.get("git_clean") is True,
        "git_commit_matches_baseline": snapshot.get("git_head") == snapshot.get("baseline_commit"),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "background_cpu_percent": background,
        "background_cpu_max_percent": max(background) if background else None,
        "gpu_temperature_c": None if not math.isfinite(gpu_temperature) else gpu_temperature,
        "snapshot": dict(snapshot),
    }


def software_environment() -> dict[str, Any]:
    versions: dict[str, str] = {}
    for name in ("numpy", "scipy", "cupy-cuda13x"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    return {
        "python": sys.version,
        "python_executable": str(Path(sys.executable).resolve()),
        "packages": versions,
        "platform": platform.platform(),
        "kernel": platform.release(),
        "thread_environment": {key: os.environ.get(key) for key in THREAD_ENV_KEYS},
        "affinity": sorted(os.sched_getaffinity(0)),
    }


def environment_id(snapshot: Mapping[str, Any], software: Mapping[str, Any]) -> str:
    stable = {
        "online_logical_cpus": snapshot.get("online_logical_cpus"),
        "governors": snapshot.get("governors"),
        "gpu": snapshot.get("gpu", {}).get("gpus") if isinstance(snapshot.get("gpu"), Mapping) else None,
        "software": software,
    }
    return hashlib.sha256(canonical_json_bytes(stable)).hexdigest()


def _framed(digest: Any, label: bytes, payload: bytes) -> None:
    digest.update(len(label).to_bytes(8, "big"))
    digest.update(label)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def canonical_arrays_identity(arrays: Mapping[str, Any]) -> dict[str, Any]:
    import numpy as np

    digest = hashlib.sha256(NUMERICAL_HASH_DOMAIN)
    records: list[dict[str, Any]] = []
    for name in sorted(arrays):
        array = np.asarray(arrays[name])
        if array.dtype.hasobject:
            raise P0LiteError(f"object array is prohibited: {name}")
        if np.issubdtype(array.dtype, np.number) and not bool(np.isfinite(array).all()):
            raise P0LiteError(f"non-finite numerical array: {name}")
        contiguous = np.ascontiguousarray(array)
        metadata = {
            "name": name,
            "dtype": contiguous.dtype.str,
            "shape": list(contiguous.shape),
            "nbytes": int(contiguous.nbytes),
        }
        payload = contiguous.tobytes(order="C")
        _framed(digest, f"metadata:{name}".encode(), canonical_json_bytes(metadata))
        _framed(digest, f"bytes:{name}".encode(), payload)
        records.append({**metadata, "bytes_sha256": hashlib.sha256(payload).hexdigest()})
    return {"algorithm": "sha256", "sha256": digest.hexdigest(), "arrays": records}


def archive_numerical_payload(archive: Path) -> dict[str, Any]:
    import numpy as np

    if not archive.is_dir() or archive.is_symlink():
        raise P0LiteError("archive must be a real directory")
    config_path = archive / "config.json"
    result_path = archive / "result.json"
    arrays_path = archive / "arrays.npz"
    config = strict_json(config_path)
    result = strict_json(result_path)
    try:
        with np.load(arrays_path, allow_pickle=False) as loaded:
            arrays = {name: loaded[name] for name in loaded.files}
    except (OSError, ValueError, KeyError) as exc:
        raise P0LiteError(f"cannot read numerical archive arrays: {exc}") from exc
    arrays_identity = canonical_arrays_identity(arrays)
    digest = hashlib.sha256(NUMERICAL_HASH_DOMAIN)
    _framed(digest, b"config.json", canonical_json_bytes(config))
    _framed(digest, b"result.json", canonical_json_bytes(result))
    _framed(digest, b"arrays.identity", arrays_identity["sha256"].encode("ascii"))
    frame = arrays.get("field__frame_time_s", arrays.get("time_s"))
    registered = (
        np.asarray(frame, dtype=np.float64).view(np.uint64).tolist()
        if frame is not None
        else None
    )
    return {
        "schema_version": 1,
        "algorithm": "sha256",
        "sha256": digest.hexdigest(),
        "config_canonical_sha256": hashlib.sha256(canonical_json_bytes(config)).hexdigest(),
        "result_canonical_sha256": hashlib.sha256(canonical_json_bytes(result)).hexdigest(),
        "arrays": arrays_identity,
        "registered_time_uint64": registered,
        "excluded_performance_metadata": ["manifest.json", "provenance.json", "timeseries.csv"],
        "source_files": {
            "config.json": sha256_file(config_path),
            "result.json": sha256_file(result_path),
            "arrays.npz": sha256_file(arrays_path),
        },
    }


def compare_payloads(payloads: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not payloads:
        raise P0LiteError("at least one numerical payload is required")
    hashes = [str(item.get("sha256")) for item in payloads]
    configs = [str(item.get("config_canonical_sha256")) for item in payloads]
    times = [item.get("registered_time_uint64") for item in payloads]
    gates = {
        "numerical_payload_hash_identical": len(set(hashes)) == 1,
        "configuration_hash_identical": len(set(configs)) == 1,
        "registered_times_bitwise_identical": all(value == times[0] for value in times),
    }
    return {"gate_pass": all(gates.values()), "gates": gates, "hashes": hashes}


def validate_success_artifacts(attempt: Path) -> dict[str, Any]:
    required = (
        "status.json",
        "run_manifest.json",
        "effective_config.json",
        "stdout.log",
        "stderr.log",
        "quality.json",
        "numerical_payload.json",
        "worker_timing.json",
        "memory.json",
        "worker_identity.json",
    )
    missing = [name for name in required if not (attempt / name).is_file()]
    telemetry = attempt / "telemetry"
    if (
        missing
        or not telemetry.is_dir()
        or not (telemetry / "runtime.jsonl").is_file()
        or (telemetry / "runtime.jsonl").stat().st_size == 0
        or not (telemetry / "pidstat.log").is_file()
        or not (attempt / "archive").is_dir()
    ):
        raise P0LiteError(f"attempt artifacts are incomplete: {missing}")
    quality = strict_json(attempt / "quality.json")
    if not isinstance(quality, dict) or quality.get("gate_pass") is not True:
        raise P0LiteError("quality gate did not pass")
    required_gates = {
        "formal_config_roundtrip_exact",
        "result_completed",
        "numerical_quality_gate_pass",
        "cuda_selected",
        "cuda_no_fallback",
        "registered_times_exact",
        "archive_strict_reload",
        "canonical_payload_complete",
    }
    gates = quality.get("gates")
    if not isinstance(gates, dict) or not required_gates.issubset(gates) or not all(
        gates[name] is True for name in required_gates
    ):
        raise P0LiteError("SUCCESS gates are incomplete or failed")
    try:
        import numpy as np
        from airvortex.core import RunStatus
        from airvortex.io import load_run_archive

        archived = load_run_archive(attempt / "archive", verify=True)
    except Exception as exc:
        raise P0LiteError(f"strict archive reload failed: {exc}") from exc
    effective = strict_json(attempt / "effective_config.json")
    diagnostics = archived.result.metadata.get("diagnostics")
    accelerator = diagnostics.get("pressure_accelerator") if isinstance(diagnostics, Mapping) else None
    numerical_gate = diagnostics.get("numerical_quality_gate") if isinstance(diagnostics, Mapping) else None
    if (
        archived.config.to_dict() != effective
        or archived.result.status is not RunStatus.COMPLETED
        or not isinstance(accelerator, Mapping)
        or accelerator.get("selected") != "cuda"
        or accelerator.get("fallback") is not False
        or not isinstance(numerical_gate, Mapping)
        or numerical_gate.get("pass") is not True
        or not np.array_equal(
            archived.result.time_s, np.asarray(EXPECTED_REGISTERED_TIMES, dtype=np.float64)
        )
    ):
        raise P0LiteError("formal archive semantics do not satisfy SUCCESS")
    payload = strict_json(attempt / "numerical_payload.json")
    recomputed = archive_numerical_payload(attempt / "archive")
    if payload != recomputed:
        raise P0LiteError("stored canonical numerical payload does not recompute")
    return {
        "backend": archived.result.backend.value,
        "status": archived.result.status.value,
        "payload": payload,
        "quality": quality,
    }


def nsys_profile_command(output_base: Path, target: Sequence[str]) -> list[str]:
    return [
        str(NSYS),
        "profile",
        "--trace=cuda,nvtx",
        "--sample=none",
        "--cpuctxsw=none",
        "--force-overwrite=true",
        f"--output={output_base}",
        *target,
    ]


def _csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        raise P0LiteError(f"cannot parse Nsight CSV {path}: {exc}") from exc
    if not rows:
        raise P0LiteError(f"Nsight CSV is empty: {path}")
    return [{str(key): str(value) for key, value in row.items()} for row in rows]


def parse_nsys_csvs(paths: Mapping[str, Path]) -> dict[str, Any]:
    if set(paths) != set(NSYS_REPORTS):
        raise P0LiteError("Nsight report set is incomplete")
    reports = {name: _csv_rows(paths[name]) for name in NSYS_REPORTS}
    api = reports["cuda_api_sum"]
    kernels = reports["cuda_gpu_kern_sum"]
    mem_time = reports["cuda_gpu_mem_time_sum"]
    mem_size = reports["cuda_gpu_mem_size_sum"]
    size_by_operation = {row["Operation"]: row for row in mem_size}
    memory: list[dict[str, Any]] = []
    for row in mem_time:
        operation = row["Operation"]
        size = size_by_operation.get(operation, {})
        memory.append(
            {
                "operation": operation,
                "count": int(row["Count"]),
                "total_time_ns": int(row["Total Time (ns)"]),
                "total_bytes": round(float(size.get("Total (MB)", "0")) * 1_000_000.0),
            }
        )
    return {
        "cuda_api": [
            {"name": row["Name"], "calls": int(row["Num Calls"]), "total_time_ns": int(row["Total Time (ns)"])}
            for row in api
        ],
        "kernels": [
            {"name": row["Name"], "instances": int(row["Instances"]), "total_time_ns": int(row["Total Time (ns)"])}
            for row in kernels
        ],
        "memory": memory,
        "allocation_api_calls": sum(
            int(row["Num Calls"])
            for row in api
            if "alloc" in row["Name"].lower() or "malloc" in row["Name"].lower()
        ),
        "h2d": next((row for row in memory if "Host-to-Device" in row["operation"]), None),
        "d2h": next((row for row in memory if "Device-to-Host" in row["operation"]), None),
    }


def _exact_memcpy(sqlite_path: Path) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT e.label, COUNT(*), SUM(m.bytes), SUM(m.end-m.start) "
                "FROM CUPTI_ACTIVITY_KIND_MEMCPY m "
                "JOIN ENUM_CUDA_MEMCPY_OPER e ON e.id=m.copyKind "
                "GROUP BY e.label ORDER BY e.label"
            ).fetchall()
    except sqlite3.Error as exc:
        raise P0LiteError(f"cannot read exact Nsight memcpy rows: {exc}") from exc
    return [
        {
            "operation": str(label),
            "count": int(count),
            "total_bytes": int(total_bytes),
            "total_time_ns": int(total_time),
        }
        for label, count, total_bytes, total_time in rows
    ]


def export_nsys_reports(attempt: Path, report: Path) -> dict[str, Any]:
    telemetry = attempt / "telemetry"
    sqlite_path = attempt / "cuda_profile.sqlite"
    commands = [
        [
            str(NSYS),
            "export",
            "--type=sqlite",
            "--force-overwrite=true",
            f"--output={sqlite_path}",
            str(report),
        ]
    ]
    bases: dict[str, Path] = {}
    for name in NSYS_REPORTS:
        base = telemetry / name
        bases[name] = base
        commands.append(
            [
                str(NSYS),
                "stats",
                f"--report={name}",
                "--format=csv",
                "--force-export=true",
                f"--output={base}",
                str(report),
            ]
        )
    command_records = []
    for command in commands:
        code, stdout, stderr = _run_text(command, timeout=300.0)
        command_records.append(
            {"command": command, "exit_code": code, "stdout": stdout, "stderr": stderr}
        )
        if code != 0:
            raise P0LiteError(f"Nsight export failed: {stderr.strip()}")
    discovered: dict[str, Path] = {}
    for name, base in bases.items():
        matches = sorted(telemetry.glob(f"{base.name}_*.csv"))
        if len(matches) != 1:
            raise P0LiteError(f"Nsight CSV output is not unique for {name}")
        discovered[name] = matches[0]
    summary = parse_nsys_csvs(discovered)
    exact_memory = _exact_memcpy(sqlite_path)
    summary["memory_exact"] = exact_memory
    summary["h2d"] = next(
        (row for row in exact_memory if row["operation"] == "Host-to-Device"), None
    )
    summary["d2h"] = next(
        (row for row in exact_memory if row["operation"] == "Device-to-Host"), None
    )
    summary.update(
        {
            "report_sha256": sha256_file(report),
            "sqlite_sha256": sha256_file(sqlite_path),
            "csv_sha256": {name: sha256_file(path) for name, path in discovered.items()},
            "commands": command_records,
        }
    )
    write_json(attempt / "nsys_summary.json", summary)
    return summary
