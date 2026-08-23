"""Synchronous CLI/GUI operations built on the background controller."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any

from airvortex.analysis.comparison import compare_results
from airvortex.core import BackendMode, RunStatus, SimulationConfig, SimulationResult
from airvortex.io import (
    capture_peak_rss,
    config_sha256,
    load_run_archive,
    save_config_json,
    save_run_archive,
)

from .controller import JobController, JobMessage, JobState
from .registry import BackendRegistry, discover_backends


MessageCallback = Callable[[JobMessage], None]


@dataclass(frozen=True, slots=True)
class RunOutcome:
    output: Path
    state: JobState
    result: SimulationResult | None
    controller: JobController[SimulationResult]

    @property
    def succeeded(self) -> bool:
        return self.state is JobState.COMPLETED


@dataclass(frozen=True, slots=True)
class SweepCase:
    index: int
    value: int | float
    config: SimulationConfig
    output: Path


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    output: Path
    total: int
    completed: int
    skipped: int
    failed: int

    @property
    def succeeded(self) -> bool:
        return self.failed == 0 and self.completed + self.skipped == self.total


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _failed_archive(
    target: Path,
    config: SimulationConfig,
    controller: JobController[SimulationResult],
) -> None:
    """Persist a terminal failed/cancelled manifest even without a result object."""

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    try:
        save_config_json(staging / "config.json", config)
        manifest = controller.manifest()
        manifest.update(
            {
                "archive_format": "airvortex-runtime-terminal-v1",
                "backend": config.backend.value,
                "config_sha256": config_sha256(config),
            }
        )
        _write_json(staging / "manifest.json", manifest)
        if controller.traceback_text:
            (staging / "traceback.txt").write_text(
                controller.traceback_text, encoding="utf-8"
            )
        os.rename(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def estimate_high_fidelity_work(config: SimulationConfig) -> dict[str, int | float]:
    effective_axisymmetric_cells = (
        config.high_fidelity.radial_cells * config.high_fidelity.axial_cells
    )
    configured_future_3d_cells = (
        config.high_fidelity.radial_cells
        * config.high_fidelity.azimuthal_cells
        * config.high_fidelity.axial_cells
    )
    steps = math.ceil(config.run.duration_s / config.high_fidelity.time_step_s)
    return {
        "effective_axisymmetric_cells": effective_axisymmetric_cells,
        "configured_future_3d_cells": configured_future_3d_cells,
        "steps_upper_bound": steps,
        "effective_axisymmetric_cell_steps_upper_bound": (
            effective_axisymmetric_cells * steps
        ),
        "configured_future_3d_cell_steps_upper_bound": (
            configured_future_3d_cells * steps
        ),
        "duration_s": config.run.duration_s,
        "time_step_s": config.high_fidelity.time_step_s,
        "azimuthal_cells_used_by_current_h1": 0,
    }


def run_once(
    config: SimulationConfig,
    output: str | Path,
    *,
    registry: BackendRegistry | None = None,
    on_message: MessageCallback | None = None,
    controller: JobController[SimulationResult] | None = None,
) -> RunOutcome:
    """Execute one backend and create exactly one terminal archive directory."""

    if not isinstance(config, SimulationConfig):
        raise TypeError("config must be a SimulationConfig")
    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(output_path)
    selected_registry = registry or discover_backends()
    backend = selected_registry.create(config.backend)
    job = controller or JobController[SimulationResult]()

    def target(control: Any, progress: Any) -> SimulationResult:
        result = backend.run(config, control=control, progress=progress)
        if not isinstance(result, SimulationResult):
            raise TypeError(
                f"backend returned {type(result).__name__}, expected SimulationResult"
            )
        return result

    process_cpu_started = time.process_time()
    job.start(target)
    try:
        while not job.join(0.05):
            if on_message is not None:
                for message in job.poll():
                    on_message(message)
    except KeyboardInterrupt:
        if job.state not in {JobState.CANCELLING, JobState.CANCELLED}:
            job.cancel("keyboard interrupt")
        while not job.join(0.05):
            if on_message is not None:
                for message in job.poll():
                    on_message(message)
    process_cpu_seconds = max(0.0, time.process_time() - process_cpu_started)
    peak_rss = {
        **capture_peak_rss(),
        "sample_point": (
            "after backend termination and before result archive serialization"
        ),
    }
    if on_message is not None:
        for message in job.poll():
            on_message(message)

    result = job.result
    invocation_summary = {
        "backend": config.backend.value,
        "config_sha256": config_sha256(config),
        "output_argument": {
            "provided": True,
            "stored": False,
            "reason": "absolute and user-relative filesystem paths are privacy-sensitive",
        },
        "backend_registry": "caller-supplied" if registry is not None else "built-in discovery",
    }
    if job.state is JobState.COMPLETED and result is not None:
        if result.status is not RunStatus.COMPLETED:
            _failed_archive(output_path, config, job)
            raise RuntimeError(
                f"completed job returned non-completed result status {result.status.value}"
            )
        save_run_archive(
            output_path,
            config,
            result,
            extra_provenance={"runtime": job.manifest()},
            process_cpu_seconds=process_cpu_seconds,
            peak_rss=peak_rss,
            invocation_entry_point="airvortex.runtime.run_once",
            invocation_argument_summary=invocation_summary,
        )
    elif job.state is JobState.CANCELLED and result is not None:
        if result.status is not RunStatus.STOPPED:
            result = replace(result, status=RunStatus.STOPPED)
        save_run_archive(
            output_path,
            config,
            result,
            extra_provenance={"runtime": job.manifest()},
            process_cpu_seconds=process_cpu_seconds,
            peak_rss=peak_rss,
            invocation_entry_point="airvortex.runtime.run_once",
            invocation_argument_summary=invocation_summary,
        )
    else:
        _failed_archive(output_path, config, job)
    return RunOutcome(output_path, job.state, result, job)


def compare_archives(
    left_path: str | Path,
    right_path: str | Path,
    output: str | Path,
) -> Path:
    """Compare every common scalar observable on an explicit aligned time grid."""

    left = load_run_archive(left_path)
    right = load_run_archive(right_path)
    target = Path(output)
    if target.exists():
        raise FileExistsError(target)
    common = sorted(set(left.result.observables) & set(right.result.observables))
    metrics: dict[str, Any] = {}
    rejected: dict[str, str] = {}
    for name in common:
        left_values = left.result.observables[name]
        right_values = right.result.observables[name]
        if left_values.ndim != 1 or right_values.ndim != 1:
            continue
        try:
            comparison = compare_results(left.result, right.result, name)
        except Exception as exc:
            rejected[name] = f"{type(exc).__name__}: {exc}"
            continue
        metric_values = comparison.metrics.as_dict()
        finite_metrics = {
            key: (
                value
                if value is None or not isinstance(value, float) or math.isfinite(value)
                else None
            )
            for key, value in metric_values.items()
        }
        metrics[name] = {
            **finite_metrics,
            "unit": comparison.left.unit,
            "common_interval_s": list(comparison.common_interval),
            "aligned_points": int(comparison.common_time.size),
        }
        if any(
            isinstance(value, float) and not math.isfinite(value)
            for value in metric_values.values()
        ):
            metrics[name]["nonfinite_metric_reason"] = (
                "normalization scale is zero while the absolute error is non-zero"
            )
    if not metrics:
        details = "; ".join(f"{key}: {value}" for key, value in rejected.items())
        raise ValueError(f"no comparable scalar observable found{': ' + details if details else ''}")
    target.mkdir(parents=True)
    payload = {
        "comparison_format_version": 1,
        "created_at_utc": _utc_now(),
        "left": str(Path(left_path).resolve()),
        "right": str(Path(right_path).resolve()),
        "left_backend": left.result.backend.value,
        "right_backend": right.result.backend.value,
        "metrics": metrics,
        "rejected": rejected,
    }
    _write_json(target / "comparison.json", payload)
    _write_json(
        target / "manifest.json",
        {
            "comparison_format_version": 1,
            "status": "completed",
            "observable_count": len(metrics),
            "comparison_sha256": hashlib.sha256(
                json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")
            ).hexdigest(),
        },
    )
    return target


def run_paired_compare(
    config: SimulationConfig,
    output: str | Path,
    *,
    registry: BackendRegistry | None = None,
    on_message: MessageCallback | None = None,
) -> Path:
    """Run both models with identical shared input and compare their observables."""

    target = Path(output)
    if target.exists():
        raise FileExistsError(target)
    target.mkdir(parents=True)
    selected_registry = registry or discover_backends()
    forward_config = replace(config, backend=BackendMode.FORWARD)
    high_fidelity_config = replace(config, backend=BackendMode.HIGH_FIDELITY)
    paired_manifest = target / "paired_manifest.json"
    _write_json(
        paired_manifest,
        {
            "paired_run_format_version": 1,
            "status": "running",
            "started_at_utc": _utc_now(),
            "base_config_sha256": config_sha256(config),
            "high_fidelity_work_upper_bound": estimate_high_fidelity_work(
                high_fidelity_config
            ),
        },
    )
    try:
        forward = run_once(
            forward_config,
            target / "forward",
            registry=selected_registry,
            on_message=on_message,
        )
        if not forward.succeeded:
            raise RuntimeError(f"paired forward run ended as {forward.state.value}")
        high_fidelity = run_once(
            high_fidelity_config,
            target / "high_fidelity",
            registry=selected_registry,
            on_message=on_message,
        )
        if not high_fidelity.succeeded:
            raise RuntimeError(
                f"paired high-fidelity run ended as {high_fidelity.state.value}"
            )
        comparison = compare_archives(
            target / "forward", target / "high_fidelity", target / "comparison"
        )
    except BaseException as exc:
        _write_json(
            paired_manifest,
            {
                "paired_run_format_version": 1,
                "status": "failed",
                "ended_at_utc": _utc_now(),
                "base_config_sha256": config_sha256(config),
                "error": {"type": type(exc).__name__, "message": str(exc)},
            },
        )
        raise
    _write_json(
        paired_manifest,
        {
            "paired_run_format_version": 1,
            "status": "completed",
            "ended_at_utc": _utc_now(),
            "base_config_sha256": config_sha256(config),
            "forward": "forward",
            "high_fidelity": "high_fidelity",
            "comparison": "comparison",
        },
    )
    return comparison


def _nested_set(raw: dict[str, Any], path: str, value: int | float) -> None:
    parts = path.split(".")
    if not parts or any(not item for item in parts):
        raise ValueError("sweep parameter must be a dotted configuration path")
    cursor: Any = raw
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            raise ValueError(f"unknown sweep parameter path {path!r}")
        cursor = cursor[part]
    leaf = parts[-1]
    if not isinstance(cursor, dict) or leaf not in cursor:
        raise ValueError(f"unknown sweep parameter path {path!r}")
    original = cursor[leaf]
    if isinstance(original, bool) or not isinstance(original, (int, float)):
        raise ValueError(f"sweep parameter {path!r} is not numeric")
    if isinstance(original, int):
        if not float(value).is_integer():
            raise ValueError(f"integer sweep parameter {path!r} received {value}")
        cursor[leaf] = int(value)
    else:
        cursor[leaf] = float(value)


def make_sweep_cases(
    base: SimulationConfig,
    parameter: str,
    values: Sequence[int | float],
    output: str | Path,
) -> tuple[SweepCase, ...]:
    if not values:
        raise ValueError("sweep requires at least one value")
    cases: list[SweepCase] = []
    target = Path(output)
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("sweep values must be real numbers")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("sweep values must be finite")
        raw = base.to_dict()
        _nested_set(raw, parameter, value)
        config = SimulationConfig.from_dict(raw)
        case_output = target / f"case_{index:04d}"
        cases.append(SweepCase(index, value, config, case_output))
    return tuple(cases)


def linear_values(start: float, stop: float, count: int) -> list[float]:
    if count < 1:
        raise ValueError("sweep count must be positive")
    if count == 1:
        return [float(start)]
    return [float(start + (stop - start) * index / (count - 1)) for index in range(count)]


def logarithmic_values(start: float, stop: float, count: int) -> list[float]:
    if start <= 0 or stop <= 0:
        raise ValueError("logarithmic sweep endpoints must be positive")
    logs = linear_values(math.log(start), math.log(stop), count)
    return [math.exp(value) for value in logs]


def run_sweep(
    base: SimulationConfig,
    parameter: str,
    values: Sequence[int | float],
    output: str | Path,
    *,
    workers: int = 1,
    registry: BackendRegistry | None = None,
    on_message: MessageCallback | None = None,
) -> SweepOutcome:
    """Run or resume a deterministic sweep without repeating valid completed cases."""

    if workers < 1:
        raise ValueError("workers must be positive")
    if base.backend is BackendMode.HIGH_FIDELITY and workers != 1:
        raise ValueError("high_fidelity sweeps require --workers 1 to bound memory use")
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    cases = make_sweep_cases(base, parameter, values, root)
    plan = {
        "sweep_format_version": 1,
        "backend": base.backend.value,
        "parameter": parameter,
        "values": list(values),
        "config_hashes": [config_sha256(case.config) for case in cases],
    }
    plan_hash = hashlib.sha256(
        json.dumps(plan, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()
    plan_path = root / "sweep_plan.json"
    if plan_path.exists():
        existing = json.loads(plan_path.read_text(encoding="utf-8"))
        existing_hash = hashlib.sha256(
            json.dumps(existing, sort_keys=True, allow_nan=False).encode("utf-8")
        ).hexdigest()
        if existing_hash != plan_hash:
            raise ValueError("existing sweep output belongs to a different sweep plan")
    else:
        _write_json(plan_path, plan)

    selected_registry = registry or discover_backends()
    pending: list[SweepCase] = []
    skipped = 0
    records: dict[int, dict[str, Any]] = {}
    for case in cases:
        if not case.output.exists():
            pending.append(case)
            continue
        try:
            archived = load_run_archive(case.output)
        except Exception as exc:
            raise ValueError(
                f"existing sweep case {case.index} is not a valid archive: {exc}"
            ) from exc
        if config_sha256(archived.config) != config_sha256(case.config):
            raise ValueError(f"existing sweep case {case.index} has a different config")
        if archived.result.status is not RunStatus.COMPLETED:
            raise ValueError(
                f"existing sweep case {case.index} is not completed; move it aside to retry"
            )
        skipped += 1
        records[case.index] = {
            "index": case.index,
            "value": case.value,
            "status": "skipped_existing_completed",
            "output": case.output.name,
        }

    record_lock = threading.Lock()

    def execute(case: SweepCase) -> tuple[SweepCase, RunOutcome]:
        outcome = run_once(
            case.config,
            case.output,
            registry=selected_registry,
            on_message=on_message,
        )
        return case, outcome

    failed = 0
    completed = 0
    if pending:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="airvortex-sweep") as pool:
            futures: list[Future[tuple[SweepCase, RunOutcome]]] = [
                pool.submit(execute, case) for case in pending
            ]
            for future in as_completed(futures):
                try:
                    case, outcome = future.result()
                    status = outcome.state.value
                    if outcome.succeeded:
                        completed += 1
                    else:
                        failed += 1
                except Exception as exc:
                    failed += 1
                    # Recover the case deterministically from the submitted index.
                    case = pending[futures.index(future)]
                    status = f"failed: {type(exc).__name__}: {exc}"
                with record_lock:
                    records[case.index] = {
                        "index": case.index,
                        "value": case.value,
                        "status": status,
                        "output": case.output.name,
                    }
                    _write_json(
                        root / "sweep_manifest.json",
                        {
                            "sweep_format_version": 1,
                            "plan_sha256": plan_hash,
                            "updated_at_utc": _utc_now(),
                            "status": "running",
                            "cases": [records[key] for key in sorted(records)],
                        },
                    )
    outcome = SweepOutcome(root, len(cases), completed, skipped, failed)
    _write_json(
        root / "sweep_manifest.json",
        {
            "sweep_format_version": 1,
            "plan_sha256": plan_hash,
            "updated_at_utc": _utc_now(),
            "status": "completed" if outcome.succeeded else "failed",
            "total": outcome.total,
            "completed": outcome.completed,
            "skipped": outcome.skipped,
            "failed": outcome.failed,
            "cases": [records[key] for key in sorted(records)],
        },
    )
    return outcome


__all__ = [
    "MessageCallback",
    "RunOutcome",
    "SweepCase",
    "SweepOutcome",
    "compare_archives",
    "estimate_high_fidelity_work",
    "linear_values",
    "logarithmic_values",
    "make_sweep_cases",
    "run_once",
    "run_paired_compare",
    "run_sweep",
]
