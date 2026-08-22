"""Synchronous CLI/GUI operations built on the background controller."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
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

import numpy as np

from airvortex.analysis.comparison import compare_results
from airvortex.core import (
    BackendMode,
    RunStatus,
    SimulationConfig,
    SimulationResult,
    sha256_file,
)
from airvortex.io import (
    archive_integrity_model,
    capture_peak_rss,
    collect_provenance,
    config_sha256,
    git_revision_identity,
    load_run_archive,
    save_config_json,
    save_run_archive,
    source_tree_identity,
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
    termination_reason: str
    requested_end_s: float
    actual_end_s: float | None
    domain_limited: bool
    full_duration: bool
    completion_class: str
    execution_source_identity_valid: bool

    @property
    def succeeded(self) -> bool:
        return self.state is JobState.COMPLETED and self.completion_class in {
            "completed_full",
            "completed_domain_limited",
        } and self.execution_source_identity_valid

    @property
    def domain_status(self) -> str:
        if self.result is None:
            return "NOT_AVAILABLE"
        return str(_result_domain_summary(self.result)["domain_status"])

    @property
    def out_of_model_domain(self) -> bool:
        if self.result is None:
            return False
        return bool(_result_domain_summary(self.result)["out_of_model_domain"])

    @property
    def first_out_of_domain_s(self) -> float | None:
        if self.result is None:
            return None
        value = _result_domain_summary(self.result)["first_out_of_domain_s"]
        return None if value is None else float(value)

    @property
    def scientific_completion_class(self) -> str:
        return _scientific_completion_class(
            self.completion_class,
            self.out_of_model_domain,
        )


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
    completed_full: int
    completed_domain_limited: int
    completed_early_unclassified: int
    stopped: int
    skipped_full: int
    skipped_domain_limited: int
    failed: int
    completed_with_out_of_model_domain: int = 0
    skipped_with_out_of_model_domain: int = 0

    @property
    def completed(self) -> int:
        return self.completed_full + self.completed_domain_limited

    @property
    def skipped(self) -> int:
        return self.skipped_full + self.skipped_domain_limited

    @property
    def succeeded(self) -> bool:
        accepted = self.completed + self.skipped
        return (
            self.failed == 0
            and self.stopped == 0
            and self.completed_early_unclassified == 0
            and accepted == self.total
        )

    @property
    def out_of_model_domain(self) -> bool:
        return bool(
            self.completed_with_out_of_model_domain
            or self.skipped_with_out_of_model_domain
        )


def _termination_summary(
    config: SimulationConfig,
    result: SimulationResult | None,
    state: JobState,
) -> dict[str, Any]:
    requested_end_s = float(config.run.duration_s)
    actual_end_s = (
        float(result.time_s[-1])
        if result is not None and result.time_s.size > 0
        else None
    )
    diagnostics: Mapping[str, object] = {}
    if result is not None:
        raw_diagnostics = result.metadata.get("diagnostics")
        if isinstance(raw_diagnostics, Mapping):
            diagnostics = raw_diagnostics
    raw_reason = diagnostics.get("termination_reason")
    explicit_reason = raw_reason if isinstance(raw_reason, str) and raw_reason else None
    domain_limited = bool(
        explicit_reason is not None and "domain_limit" in explicit_reason.lower()
    )
    tolerance = max(1.0e-12, 1.0e-9 * max(1.0, requested_end_s))
    reached_requested_end = bool(
        actual_end_s is not None and actual_end_s + tolerance >= requested_end_s
    )
    full_duration = bool(
        result is not None
        and result.status is RunStatus.COMPLETED
        and reached_requested_end
        and not domain_limited
    )
    if explicit_reason is not None:
        termination_reason = explicit_reason
    elif result is not None and result.status is RunStatus.STOPPED:
        termination_reason = "soft_stop"
    elif full_duration:
        termination_reason = "configured_duration_reached"
    elif state is JobState.FAILED:
        termination_reason = "runtime_failure"
    elif state is JobState.CANCELLED:
        termination_reason = "runtime_cancelled_without_completed_result"
    else:
        termination_reason = "completed_before_requested_end_without_domain_reason"
    if state is JobState.FAILED:
        completion_class = "failed"
    elif state is JobState.CANCELLED or (
        result is not None and result.status is RunStatus.STOPPED
    ):
        completion_class = "stopped"
    elif state is JobState.COMPLETED and domain_limited:
        completion_class = "completed_domain_limited"
    elif state is JobState.COMPLETED and full_duration:
        completion_class = "completed_full"
    elif state is JobState.COMPLETED:
        completion_class = "completed_early_unclassified"
    else:
        completion_class = "failed"
    return {
        "termination_reason": termination_reason,
        "requested_end_s": requested_end_s,
        "actual_end_s": actual_end_s,
        "domain_limited": domain_limited,
        "full_duration": full_duration,
        "reached_requested_end": reached_requested_end,
        "completion_class": completion_class,
    }


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
    *,
    process_cpu_seconds: float,
    peak_rss: dict[str, Any],
    invocation_summary: dict[str, Any],
    source_tree_execution_start: dict[str, Any],
    source_tree_execution_end: dict[str, Any],
    git_revision_execution_start: dict[str, Any],
    git_revision_execution_end: dict[str, Any],
    termination_summary: dict[str, Any],
) -> None:
    """Persist a terminal failed/cancelled manifest even without a result object."""

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    try:
        save_config_json(staging / "config.json", config)
        provenance = collect_provenance(
            config,
            {"runtime": {**controller.manifest(), "termination": termination_summary}},
            process_cpu_seconds=process_cpu_seconds,
            peak_rss=peak_rss,
            invocation_entry_point="airvortex.runtime.run_once",
            invocation_argument_summary=invocation_summary,
            source_tree_execution_start=source_tree_execution_start,
            source_tree_execution_end=source_tree_execution_end,
            git_revision_execution_start=git_revision_execution_start,
            git_revision_execution_end=git_revision_execution_end,
        )
        _write_json(staging / "provenance.json", provenance)
        payload_names = ["config.json", "provenance.json"]
        if controller.traceback_text:
            (staging / "traceback.txt").write_text(
                controller.traceback_text, encoding="utf-8"
            )
            payload_names.append("traceback.txt")
        files = {
            name: {
                "sha256": sha256_file(staging / name),
                "size_bytes": (staging / name).stat().st_size,
            }
            for name in sorted(payload_names)
        }
        manifest = controller.manifest()
        manifest.update(
            {
                "archive_format": "airvortex-runtime-terminal-v2",
                "artifact_class": "terminal_diagnostic_without_simulation_result",
                "complete_run_archive": False,
                "backend": config.backend.value,
                "config_sha256": config_sha256(config),
                "files": files,
                "integrity_model": archive_integrity_model(),
                "provenance_reproducibility_status": provenance["source_identity"][
                    "reproducibility_status"
                ],
                "execution_source_identity_valid": provenance["source_identity"][
                    "execution_source_identity_valid"
                ],
                "termination": termination_summary,
            }
        )
        _write_json(staging / "manifest.json", manifest)
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
    source_tree_provider: Callable[[], dict[str, Any]] | None = None,
) -> RunOutcome:
    """Execute one backend and create exactly one terminal archive directory."""

    if not isinstance(config, SimulationConfig):
        raise TypeError("config must be a SimulationConfig")
    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(output_path)
    selected_source_tree_provider = (
        source_tree_identity if source_tree_provider is None else source_tree_provider
    )
    source_tree_execution_start = {
        **selected_source_tree_provider(),
        "sample_point": (
            "run_once entry before backend discovery and backend construction"
        ),
    }
    git_revision_execution_start = {
        **git_revision_identity(),
        "sample_point": (
            "run_once entry before backend discovery and backend construction"
        ),
    }
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
    source_tree_execution_end = {
        **selected_source_tree_provider(),
        "sample_point": (
            "archive boundary after backend termination and immediately before "
            "archive serialization"
        ),
    }
    git_revision_execution_end = {
        **git_revision_identity(),
        "sample_point": "after backend termination and before archive serialization",
    }
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
    termination_summary = _termination_summary(config, result, job.state)
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
    terminal_provenance = {
        "process_cpu_seconds": process_cpu_seconds,
        "peak_rss": peak_rss,
        "invocation_summary": invocation_summary,
        "source_tree_execution_start": source_tree_execution_start,
        "source_tree_execution_end": source_tree_execution_end,
        "git_revision_execution_start": git_revision_execution_start,
        "git_revision_execution_end": git_revision_execution_end,
        "termination_summary": termination_summary,
    }
    if job.state is JobState.COMPLETED and result is not None:
        if result.status is not RunStatus.COMPLETED:
            _failed_archive(output_path, config, job, **terminal_provenance)
            raise RuntimeError(
                f"completed job returned non-completed result status {result.status.value}"
            )
        save_run_archive(
            output_path,
            config,
            result,
            extra_provenance={
                "runtime": {**job.manifest(), "termination": termination_summary}
            },
            process_cpu_seconds=process_cpu_seconds,
            peak_rss=peak_rss,
            invocation_entry_point="airvortex.runtime.run_once",
            invocation_argument_summary=invocation_summary,
            source_tree_execution_start=source_tree_execution_start,
            source_tree_execution_end=source_tree_execution_end,
            git_revision_execution_start=git_revision_execution_start,
            git_revision_execution_end=git_revision_execution_end,
        )
    elif job.state is JobState.CANCELLED and result is not None:
        if result.status is not RunStatus.STOPPED:
            result = replace(result, status=RunStatus.STOPPED)
        save_run_archive(
            output_path,
            config,
            result,
            extra_provenance={
                "runtime": {**job.manifest(), "termination": termination_summary}
            },
            process_cpu_seconds=process_cpu_seconds,
            peak_rss=peak_rss,
            invocation_entry_point="airvortex.runtime.run_once",
            invocation_argument_summary=invocation_summary,
            source_tree_execution_start=source_tree_execution_start,
            source_tree_execution_end=source_tree_execution_end,
            git_revision_execution_start=git_revision_execution_start,
            git_revision_execution_end=git_revision_execution_end,
        )
    else:
        _failed_archive(output_path, config, job, **terminal_provenance)
    return RunOutcome(
        output_path,
        job.state,
        result,
        job,
        termination_summary["termination_reason"],
        termination_summary["requested_end_s"],
        termination_summary["actual_end_s"],
        termination_summary["domain_limited"],
        termination_summary["full_duration"],
        termination_summary["completion_class"],
        bool(
            source_tree_execution_start.get("available")
            and source_tree_execution_end.get("available")
            and source_tree_execution_start.get("value")
            == source_tree_execution_end.get("value")
        ),
    )


def _result_domain_summary(result: SimulationResult) -> dict[str, Any]:
    """Describe recorded model-domain validity without equating it to termination."""

    raw_status = result.metadata.get("domain_status")
    reported_status = (
        raw_status.strip().upper()
        if isinstance(raw_status, str) and raw_status.strip()
        else None
    )
    boundary_event_kinds = {
        "connected_proxy",
        "out_of_model_domain",
        "out-of-model-domain",
    }
    boundary_times = [
        float(event.time_s)
        for event in result.events
        if event.kind.strip().casefold() in boundary_event_kinds
    ]
    first_out_of_domain_s = min(boundary_times) if boundary_times else None

    diagnostics = result.metadata.get("diagnostics")
    raw_reason = (
        diagnostics.get("termination_reason")
        if isinstance(diagnostics, Mapping)
        else None
    )
    model_domain_limit = bool(
        isinstance(raw_reason, str) and "domain_limit" in raw_reason.casefold()
    )
    out_of_model_domain = bool(
        reported_status == "OUT_OF_MODEL_DOMAIN"
        or first_out_of_domain_s is not None
    )
    if out_of_model_domain:
        domain_status = "OUT_OF_MODEL_DOMAIN"
    elif reported_status is not None:
        domain_status = reported_status
    elif model_domain_limit:
        # A high-fidelity run that stops at its boundary has no post-boundary
        # samples.  Keep that distinct from Forward's continued proxy output.
        domain_status = "MODEL_DOMAIN_LIMIT_REACHED"
    else:
        domain_status = "NOT_REPORTED"
    return {
        "domain_status": domain_status,
        "first_out_of_domain_s": first_out_of_domain_s,
        "out_of_model_domain": out_of_model_domain,
        "model_domain_limit": model_domain_limit,
    }


def _scientific_completion_class(
    duration_completion_class: str,
    out_of_model_domain: bool,
) -> str:
    if out_of_model_domain and duration_completion_class in {
        "completed_full",
        "completed_domain_limited",
    }:
        return "completed_with_out_of_model_domain"
    return duration_completion_class


def _comparison_domain_window(
    left: SimulationResult,
    right: SimulationResult,
) -> dict[str, Any]:
    left_domain = _result_domain_summary(left)
    right_domain = _result_domain_summary(right)
    domains = {"left": left_domain, "right": right_domain}
    any_out_of_model_domain = any(
        bool(item["out_of_model_domain"]) for item in domains.values()
    )
    any_model_domain_limit = any(
        bool(item["model_domain_limit"]) for item in domains.values()
    )

    valid_time_start: float | None = None
    recorded_common_end: float | None = None
    if left.time_s.size and right.time_s.size:
        valid_time_start = max(float(left.time_s[0]), float(right.time_s[0]))
        recorded_common_end = min(float(left.time_s[-1]), float(right.time_s[-1]))

    missing_boundary = any(
        bool(item["out_of_model_domain"])
        and item["first_out_of_domain_s"] is None
        for item in domains.values()
    )
    if recorded_common_end is None or missing_boundary:
        valid_time_end: float | None = None
    else:
        valid_time_end = recorded_common_end
        for item in domains.values():
            boundary = item["first_out_of_domain_s"]
            if boundary is not None:
                valid_time_end = min(valid_time_end, float(boundary))

    tolerance = 1.0e-12
    sufficient = bool(
        valid_time_start is not None
        and valid_time_end is not None
        and valid_time_end > valid_time_start + tolerance
    )
    truncated = bool(
        valid_time_end is not None
        and recorded_common_end is not None
        and valid_time_end < recorded_common_end - tolerance
    )
    if missing_boundary:
        insufficient_reason = (
            "OUT_OF_MODEL_DOMAIN was reported without a CONNECTED_PROXY/"
            "out-of-domain event time; a safe comparison window cannot be reconstructed"
        )
    elif not sufficient:
        insufficient_reason = (
            "common model-valid interval has no positive duration or fewer than "
            "two distinct aligned times"
        )
    else:
        insufficient_reason = None

    if any_out_of_model_domain:
        completion_class = "completed_with_out_of_model_domain"
    elif any_model_domain_limit:
        completion_class = "completed_with_domain_limit"
    else:
        completion_class = "completed"
    return {
        "domains": domains,
        "valid_time_start": valid_time_start,
        "valid_time_end": valid_time_end,
        "recorded_common_end": recorded_common_end,
        "truncated_to_common_valid_domain": truncated,
        "common_valid_domain_sufficient": sufficient,
        "insufficient_reason": insufficient_reason,
        "out_of_model_domain": any_out_of_model_domain,
        "model_domain_limit": any_model_domain_limit,
        "completion_class": completion_class,
    }


def _observable_comparison_view(
    result: SimulationResult,
    observable: str,
    valid_time_end: float,
) -> SimulationResult:
    """Return one observable clipped at an interpolated closed domain boundary."""

    source_time = result.time_s
    clipped_time = np.unique(
        np.concatenate((source_time[source_time < valid_time_end], [valid_time_end]))
    )
    clipped_values = np.interp(
        clipped_time,
        source_time,
        result.observables[observable],
    )
    return SimulationResult(
        backend=result.backend,
        time_s=clipped_time,
        observables={observable: clipped_values},
        metadata=result.metadata,
        status=result.status,
    )


def compare_archives(
    left_path: str | Path,
    right_path: str | Path,
    output: str | Path,
) -> Path:
    """Compare scalar observables only over the common model-valid time window."""

    left = load_run_archive(left_path)
    right = load_run_archive(right_path)
    target = Path(output)
    if target.exists():
        raise FileExistsError(target)
    common = sorted(set(left.result.observables) & set(right.result.observables))
    scalar_common = [
        name
        for name in common
        if left.result.observables[name].ndim == 1
        and right.result.observables[name].ndim == 1
    ]
    if not scalar_common:
        raise ValueError("no common scalar observable found")

    domain_window = _comparison_domain_window(left.result, right.result)
    metrics: dict[str, Any] = {}
    rejected: dict[str, str] = {}
    valid_time_end = domain_window["valid_time_end"]
    if not domain_window["common_valid_domain_sufficient"]:
        reason = str(domain_window["insufficient_reason"])
        rejected.update(
            (name, f"insufficient_common_valid_domain: {reason}")
            for name in scalar_common
        )
    else:
        assert isinstance(valid_time_end, float)
        for name in scalar_common:
            try:
                left_view = _observable_comparison_view(
                    left.result, name, valid_time_end
                )
                right_view = _observable_comparison_view(
                    right.result, name, valid_time_end
                )
                comparison = compare_results(left_view, right_view, name)
            except Exception as exc:
                rejected[name] = f"{type(exc).__name__}: {exc}"
                continue
            metric_values = comparison.metrics.as_dict()
            finite_metrics = {
                key: (
                    value
                    if value is None
                    or not isinstance(value, float)
                    or math.isfinite(value)
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
    if not metrics and domain_window["common_valid_domain_sufficient"]:
        details = "; ".join(f"{key}: {value}" for key, value in rejected.items())
        raise ValueError(
            f"no comparable scalar observable found{': ' + details if details else ''}"
        )

    comparison_status = (
        str(domain_window["completion_class"])
        if metrics
        else "rejected_insufficient_common_valid_domain"
    )
    target.mkdir(parents=True)
    payload = {
        "comparison_format_version": 2,
        "created_at_utc": _utc_now(),
        "status": comparison_status,
        "completion_class": domain_window["completion_class"],
        "left": str(Path(left_path).resolve()),
        "right": str(Path(right_path).resolve()),
        "left_backend": left.result.backend.value,
        "right_backend": right.result.backend.value,
        "domains": domain_window["domains"],
        "valid_time_start": domain_window["valid_time_start"],
        "valid_time_end": valid_time_end,
        "truncated_to_common_valid_domain": domain_window[
            "truncated_to_common_valid_domain"
        ],
        "common_valid_domain_sufficient": domain_window[
            "common_valid_domain_sufficient"
        ],
        "insufficient_reason": domain_window["insufficient_reason"],
        "metrics": metrics,
        "rejected": rejected,
    }
    _write_json(target / "comparison.json", payload)
    _write_json(
        target / "manifest.json",
        {
            "comparison_format_version": 2,
            "status": comparison_status,
            "completion_class": domain_window["completion_class"],
            "observable_count": len(metrics),
            "domains": domain_window["domains"],
            "valid_time_start": domain_window["valid_time_start"],
            "valid_time_end": valid_time_end,
            "truncated_to_common_valid_domain": domain_window[
                "truncated_to_common_valid_domain"
            ],
            "common_valid_domain_sufficient": domain_window[
                "common_valid_domain_sufficient"
            ],
            "insufficient_reason": domain_window["insufficient_reason"],
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
            "paired_run_format_version": 2,
            "status": "running",
            "started_at_utc": _utc_now(),
            "base_config_sha256": config_sha256(config),
            "requested_end_s": config.run.duration_s,
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
                "paired_run_format_version": 2,
                "status": "failed",
                "ended_at_utc": _utc_now(),
                "base_config_sha256": config_sha256(config),
                "requested_end_s": config.run.duration_s,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            },
        )
        raise
    assert forward.result is not None
    assert high_fidelity.result is not None
    domain_window = _comparison_domain_window(
        forward.result,
        high_fidelity.result,
    )
    forward_domain = domain_window["domains"]["left"]
    high_fidelity_domain = domain_window["domains"]["right"]

    def paired_completion_class(
        outcome: RunOutcome,
        domain: Mapping[str, Any],
    ) -> str:
        if domain["out_of_model_domain"]:
            return "completed_with_out_of_model_domain"
        return outcome.completion_class

    backend_terminations = {
        "forward": {
            "termination_reason": forward.termination_reason,
            "requested_end_s": forward.requested_end_s,
            "actual_end_s": forward.actual_end_s,
            "domain_limited": forward.domain_limited,
            "full_duration": forward.full_duration,
            "completion_class": paired_completion_class(
                forward, forward_domain
            ),
            "domain_status": forward_domain["domain_status"],
            "first_out_of_domain_s": forward_domain["first_out_of_domain_s"],
            "execution_source_identity_valid": (
                forward.execution_source_identity_valid
            ),
        },
        "high_fidelity": {
            "termination_reason": high_fidelity.termination_reason,
            "requested_end_s": high_fidelity.requested_end_s,
            "actual_end_s": high_fidelity.actual_end_s,
            "domain_limited": high_fidelity.domain_limited,
            "full_duration": high_fidelity.full_duration,
            "completion_class": paired_completion_class(
                high_fidelity, high_fidelity_domain
            ),
            "domain_status": high_fidelity_domain["domain_status"],
            "first_out_of_domain_s": high_fidelity_domain[
                "first_out_of_domain_s"
            ],
            "execution_source_identity_valid": (
                high_fidelity.execution_source_identity_valid
            ),
        },
    }
    any_domain_limit = forward.domain_limited or high_fidelity.domain_limited
    any_out_of_model_domain = bool(domain_window["out_of_model_domain"])
    paired_status = (
        "completed_with_out_of_model_domain"
        if any_out_of_model_domain
        else "completed_with_domain_limit"
        if any_domain_limit
        else "completed"
    )
    _write_json(
        paired_manifest,
        {
            "paired_run_format_version": 2,
            "status": paired_status,
            "completion_class": paired_status,
            "ended_at_utc": _utc_now(),
            "base_config_sha256": config_sha256(config),
            "requested_end_s": config.run.duration_s,
            "domain_limited": any_domain_limit,
            "out_of_model_domain": any_out_of_model_domain,
            "backend_domains": {
                "forward": forward_domain,
                "high_fidelity": high_fidelity_domain,
            },
            "valid_time_end": domain_window["valid_time_end"],
            "truncated_to_common_valid_domain": domain_window[
                "truncated_to_common_valid_domain"
            ],
            "comparison_metrics_available": domain_window[
                "common_valid_domain_sufficient"
            ],
            "backend_terminations": backend_terminations,
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
    skipped_full = 0
    skipped_domain_limited = 0
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
        source_identity = archived.provenance.get("source_identity")
        if isinstance(source_identity, Mapping) and source_identity.get(
            "execution_source_identity_valid"
        ) is False:
            raise ValueError(
                f"existing sweep case {case.index} has invalid execution-source identity; "
                "move it aside to retry"
            )
        termination = _termination_summary(
            case.config, archived.result, JobState.COMPLETED
        )
        completion_class = termination["completion_class"]
        if completion_class == "completed_full":
            skipped_full += 1
            record_status = "skipped_existing_completed_full"
        elif completion_class == "completed_domain_limited":
            skipped_domain_limited += 1
            record_status = "skipped_existing_completed_domain_limited"
        else:
            raise ValueError(
                f"existing sweep case {case.index} is {completion_class}; "
                "move it aside to retry"
            )
        records[case.index] = {
            "index": case.index,
            "value": case.value,
            "status": record_status,
            "output": case.output.name,
            **termination,
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
    stopped = 0
    completed_full = 0
    completed_domain_limited = 0
    completed_early_unclassified = 0
    if pending:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="airvortex-sweep") as pool:
            futures: list[Future[tuple[SweepCase, RunOutcome]]] = [
                pool.submit(execute, case) for case in pending
            ]
            for future in as_completed(futures):
                try:
                    case, outcome = future.result()
                    if not outcome.execution_source_identity_valid:
                        failed += 1
                        status = "invalid_execution_source_identity"
                    elif outcome.completion_class == "completed_full":
                        completed_full += 1
                        status = "completed_full"
                    elif outcome.completion_class == "completed_domain_limited":
                        completed_domain_limited += 1
                        status = "completed_domain_limited"
                    elif outcome.completion_class == "stopped":
                        stopped += 1
                        status = "stopped"
                    elif outcome.completion_class == "completed_early_unclassified":
                        completed_early_unclassified += 1
                        status = "completed_early_unclassified"
                    else:
                        failed += 1
                        status = "failed"
                    termination = {
                        "termination_reason": outcome.termination_reason,
                        "requested_end_s": outcome.requested_end_s,
                        "actual_end_s": outcome.actual_end_s,
                        "domain_limited": outcome.domain_limited,
                        "full_duration": outcome.full_duration,
                        "completion_class": outcome.completion_class,
                        "execution_source_identity_valid": (
                            outcome.execution_source_identity_valid
                        ),
                    }
                except Exception as exc:
                    failed += 1
                    # Recover the case deterministically from the submitted index.
                    case = pending[futures.index(future)]
                    status = f"failed: {type(exc).__name__}: {exc}"
                    termination = {
                        "termination_reason": "runtime_exception",
                        "requested_end_s": case.config.run.duration_s,
                        "actual_end_s": None,
                        "domain_limited": False,
                        "full_duration": False,
                        "completion_class": "failed",
                        "execution_source_identity_valid": False,
                    }
                with record_lock:
                    records[case.index] = {
                        "index": case.index,
                        "value": case.value,
                        "status": status,
                        "output": case.output.name,
                        **termination,
                    }
                    _write_json(
                        root / "sweep_manifest.json",
                        {
                            "sweep_format_version": 2,
                            "plan_sha256": plan_hash,
                            "updated_at_utc": _utc_now(),
                            "status": "running",
                            "cases": [records[key] for key in sorted(records)],
                        },
                    )
    outcome = SweepOutcome(
        root,
        len(cases),
        completed_full,
        completed_domain_limited,
        completed_early_unclassified,
        stopped,
        skipped_full,
        skipped_domain_limited,
        failed,
    )
    any_domain_limit = bool(
        outcome.completed_domain_limited or outcome.skipped_domain_limited
    )
    _write_json(
        root / "sweep_manifest.json",
        {
            "sweep_format_version": 2,
            "plan_sha256": plan_hash,
            "updated_at_utc": _utc_now(),
            "status": (
                "completed_with_domain_limit"
                if outcome.succeeded and any_domain_limit
                else "completed"
                if outcome.succeeded
                else "failed"
            ),
            "total": outcome.total,
            "completed": outcome.completed,
            "completed_full": outcome.completed_full,
            "completed_domain_limited": outcome.completed_domain_limited,
            "completed_early_unclassified": outcome.completed_early_unclassified,
            "stopped": outcome.stopped,
            "skipped": outcome.skipped,
            "skipped_full": outcome.skipped_full,
            "skipped_domain_limited": outcome.skipped_domain_limited,
            "failed": outcome.failed,
            "domain_limited": any_domain_limit,
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
