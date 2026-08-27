#!/usr/bin/env python3
"""Fail-closed eight-step CUDA resource probe for the Q1R4 D0 gate."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

from airvortex.backends.high_fidelity_3d import HighFidelityBackend
from airvortex.core.protocols import json_ready
from airvortex.hf3d.pressure import make_pressure_projection
from airvortex.hf3d.stepper import advance_one_step
from airvortex.hf3d.timestep import propose_time_step, retry_time_step_s
from airvortex.hf3d.types import HF3DPostProjectionCFLExceeded
from airvortex.io.archive import source_tree_identity
from airvortex.io.config_io import load_config_json, read_json_strict


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def git_identity() -> dict[str, object]:
    status = _git("status", "--porcelain", "--untracked-files=normal")
    return {
        "head": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "worktree_clean": status == "",
        "porcelain": status.splitlines(),
    }


def _proc_value_bytes(label: str) -> int | None:
    try:
        lines = Path("/proc/self/status").read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    prefix = f"{label}:"
    for line in lines:
        if not line.startswith(prefix):
            continue
        fields = line.split()
        if len(fields) >= 3 and fields[2] == "kB":
            return int(fields[1]) * 1024
    return None


def _host_available_bytes() -> int | None:
    try:
        lines = Path("/proc/meminfo").read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("MemAvailable:"):
            fields = line.split()
            if len(fields) >= 3 and fields[2] == "kB":
                return int(fields[1]) * 1024
    return None


def _nvidia_process_used_bytes(pid: int) -> int | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in completed.stdout.splitlines():
        fields = [item.strip() for item in line.split(",")]
        if len(fields) != 2:
            continue
        try:
            process_id = int(fields[0])
            used_mib = int(fields[1])
        except ValueError:
            continue
        if process_id == pid:
            return used_mib * 1024 * 1024
    return None


class ResourceMonitor:
    """Continuously sample host, CUDA-global, pool, and per-process memory."""

    def __init__(self, cupy_module: Any, interval_s: float = 0.25) -> None:
        self.cp = cupy_module
        self.interval_s = float(interval_s)
        self.started = time.monotonic()
        self.samples: list[dict[str, object]] = []
        self.errors: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._last_nvidia_sample_s = -math.inf
        self._last_process_gpu_bytes: int | None = None

    def _sample(self, label: str) -> dict[str, object]:
        elapsed = time.monotonic() - self.started
        try:
            with self.cp.cuda.Device(0):
                cuda_free, cuda_total = self.cp.cuda.runtime.memGetInfo()
                pool = self.cp.get_default_memory_pool()
                pool_used = int(pool.used_bytes())
                pool_total = int(pool.total_bytes())
        except Exception as exc:
            cuda_free = cuda_total = pool_used = pool_total = None
            with self._lock:
                self.errors.append(f"{type(exc).__name__}: {exc}")
        if elapsed - self._last_nvidia_sample_s >= 1.0:
            self._last_process_gpu_bytes = _nvidia_process_used_bytes(os.getpid())
            self._last_nvidia_sample_s = elapsed
        sample: dict[str, object] = {
            "elapsed_s": elapsed,
            "label": label,
            "process_rss_bytes": _proc_value_bytes("VmRSS"),
            "process_peak_rss_bytes": _proc_value_bytes("VmHWM"),
            "host_available_memory_bytes": _host_available_bytes(),
            "cuda_free_memory_bytes": (
                int(cuda_free) if cuda_free is not None else None
            ),
            "cuda_total_memory_bytes": (
                int(cuda_total) if cuda_total is not None else None
            ),
            "cupy_pool_used_bytes": pool_used,
            "cupy_pool_total_bytes": pool_total,
            "nvidia_smi_process_used_bytes": self._last_process_gpu_bytes,
        }
        with self._lock:
            self.samples.append(sample)
        return sample

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._sample("continuous")

    def start(self) -> None:
        self._sample("monitor_start")
        self._thread.start()

    def mark(self, label: str) -> dict[str, object]:
        try:
            self.cp.cuda.runtime.deviceSynchronize()
        except Exception as exc:
            with self._lock:
                self.errors.append(f"synchronize {type(exc).__name__}: {exc}")
        return self._sample(label)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(2.0, 4.0 * self.interval_s))
        self._sample("monitor_stop")

    def summary(self) -> dict[str, object]:
        with self._lock:
            samples = list(self.samples)
            errors = list(self.errors)
        numeric_fields = (
            "process_rss_bytes",
            "process_peak_rss_bytes",
            "host_available_memory_bytes",
            "cuda_free_memory_bytes",
            "cuda_total_memory_bytes",
            "cupy_pool_used_bytes",
            "cupy_pool_total_bytes",
            "nvidia_smi_process_used_bytes",
        )
        available: dict[str, list[int]] = {}
        for field in numeric_fields:
            available[field] = [
                int(item[field])
                for item in samples
                if isinstance(item.get(field), int)
            ]
        first = samples[0] if samples else {}
        return {
            "sampling_interval_s": self.interval_s,
            "sample_count": len(samples),
            "sampling_errors": errors,
            "baseline": first,
            "process_rss_peak_bytes": (
                max(available["process_rss_bytes"])
                if available["process_rss_bytes"]
                else None
            ),
            "process_peak_rss_max_bytes": (
                max(available["process_peak_rss_bytes"])
                if available["process_peak_rss_bytes"]
                else None
            ),
            "host_available_memory_min_bytes": (
                min(available["host_available_memory_bytes"])
                if available["host_available_memory_bytes"]
                else None
            ),
            "cuda_total_memory_bytes": (
                max(available["cuda_total_memory_bytes"])
                if available["cuda_total_memory_bytes"]
                else None
            ),
            "cuda_free_memory_min_bytes": (
                min(available["cuda_free_memory_bytes"])
                if available["cuda_free_memory_bytes"]
                else None
            ),
            "cuda_global_peak_delta_from_baseline_bytes": (
                int(first["cuda_free_memory_bytes"])
                - min(available["cuda_free_memory_bytes"])
                if isinstance(first.get("cuda_free_memory_bytes"), int)
                and available["cuda_free_memory_bytes"]
                else None
            ),
            "cupy_pool_used_peak_bytes": (
                max(available["cupy_pool_used_bytes"])
                if available["cupy_pool_used_bytes"]
                else None
            ),
            "cupy_pool_total_peak_bytes": (
                max(available["cupy_pool_total_bytes"])
                if available["cupy_pool_total_bytes"]
                else None
            ),
            "nvidia_smi_process_peak_bytes": (
                max(available["nvidia_smi_process_used_bytes"])
                if available["nvidia_smi_process_used_bytes"]
                else None
            ),
            "samples": samples,
        }


def validate_probe_contract(
    config: Any,
    preregistration: Mapping[str, Any],
) -> dict[str, bool]:
    contract = preregistration["d0_scope"]["B_13p5_cuda_resource_probe"]
    expected_grid = tuple(int(value) for value in contract["grid"])
    observed_grid = (
        config.high_fidelity.radial_cells,
        config.high_fidelity.azimuthal_cells,
        config.high_fidelity.axial_cells,
    )
    return {
        "decision_id": preregistration.get("decision_id")
        == "Q1R4_NOT_FROZEN__D0_DIAGNOSTIC_GATE_SELECTED",
        "grid": observed_grid == expected_grid == (204, 204, 180),
        "candidate_registered_dt": math.isclose(
            config.high_fidelity.time_step_s,
            float(contract["candidate_registered_dt_s"]),
            rel_tol=0.0,
            abs_tol=0.0,
        ),
        "cuda_explicit": config.high_fidelity.pressure_solver_device == "cuda",
        "accepted_steps": int(contract["maximum_accepted_steps"]) == 8,
        "candidate_dt_not_freeze": contract[
            "candidate_dt_is_qualification_freeze"
        ]
        is False,
        "full_q1r4_not_authorized": preregistration["decision"][
            "full_q1r4_campaign_run_allowed"
        ]
        is False,
    }


def assess_probe_report(report: Mapping[str, Any]) -> dict[str, object]:
    execution = report.get("execution", {})
    identity = report.get("identity", {})
    resources = report.get("resources", {})
    quality = report.get("numerical_quality", {})
    accelerator = execution.get("pressure_accelerator", {})
    steps = execution.get("accepted_step_diagnostics", [])
    gates = {
        "contract": all(report.get("contract_gates", {}).values()),
        "main_clean_stable_identity": bool(
            identity.get("main_branch") is True
            and identity.get("worktree_clean_start_and_end") is True
            and identity.get("git_head_stable") is True
            and identity.get("formal_source_tree_stable") is True
        ),
        "cuda_without_fallback": bool(
            accelerator.get("selected") == "cuda"
            and accelerator.get("fallback") is False
        ),
        "eight_accepted_steps": bool(
            execution.get("completed_accepted_steps") == 8
            and isinstance(steps, list)
            and len(steps) == 8
        ),
        "numerical_quality": quality.get("pass") is True,
        "resource_metrics_auditable": bool(
            resources.get("sample_count", 0) >= 2
            and resources.get("process_rss_peak_bytes") is not None
            and resources.get("host_available_memory_min_bytes") is not None
            and resources.get("cuda_free_memory_min_bytes") is not None
            and resources.get("cuda_global_peak_delta_from_baseline_bytes")
            is not None
            and resources.get("cupy_pool_used_peak_bytes") is not None
            and resources.get("cupy_pool_total_peak_bytes") is not None
            and resources.get("nvidia_smi_process_peak_bytes") is not None
            and not resources.get("sampling_errors")
        ),
        "no_execution_error": report.get("execution_error") is None,
    }
    return {"pass": all(gates.values()), "gates": gates}


def _quality(
    step_records: list[Mapping[str, Any]],
    config: Any,
    initial_volume_m3: float,
    thresholds: Mapping[str, Any],
) -> dict[str, object]:
    if not step_records:
        return {"pass": False, "checks": {}, "reason": "no accepted steps"}
    max_cfl = max(float(item["advective_cfl"]) for item in step_records)
    max_divergence = max(
        float(item["divergence_after_s_inv"]) for item in step_records
    )
    max_residual = max(
        float(item["pressure_relative_residual"]) for item in step_records
    )
    max_clip = max(
        float(item["phase_clip_absolute_volume_m3"]) for item in step_records
    ) / initial_volume_m3
    max_repair = max(
        float(item["phase_repair_absolute_volume_m3"]) for item in step_records
    ) / initial_volume_m3
    cumulative_clip = sum(
        float(item["phase_clip_absolute_volume_m3"]) for item in step_records
    ) / initial_volume_m3
    cumulative_repair = sum(
        float(item["phase_repair_absolute_volume_m3"])
        for item in step_records
    ) / initial_volume_m3
    checks = {
        "advective_cfl": {
            "value": max_cfl,
            "maximum": float(thresholds["advective_cfl_max"]),
            "pass": max_cfl
            <= float(thresholds["advective_cfl_max"]) * (1.0 + 1.0e-9),
        },
        "projected_divergence_s_inv": {
            "value": max_divergence,
            "maximum": float(thresholds["projected_divergence_max_s_inv"]),
            "pass": max_divergence
            <= float(thresholds["projected_divergence_max_s_inv"]),
        },
        "pressure_relative_residual": {
            "value": max_residual,
            "maximum": config.high_fidelity.pressure_relative_tolerance
            * float(
                thresholds[
                    "pressure_relative_residual_factor_over_configured_tolerance_max"
                ]
            ),
            "pass": max_residual
            <= config.high_fidelity.pressure_relative_tolerance
            * float(
                thresholds[
                    "pressure_relative_residual_factor_over_configured_tolerance_max"
                ]
            ),
        },
        "maximum_single_step_phase_clip_relative": {
            "value": max_clip,
            "maximum": config.high_fidelity.maximum_single_step_phase_clip_relative,
            "pass": max_clip
            <= config.high_fidelity.maximum_single_step_phase_clip_relative,
        },
        "maximum_single_step_phase_repair_relative": {
            "value": max_repair,
            "maximum": config.high_fidelity.maximum_single_step_phase_repair_relative,
            "pass": max_repair
            <= config.high_fidelity.maximum_single_step_phase_repair_relative,
        },
        "cumulative_phase_clip_relative": {
            "value": cumulative_clip,
            "maximum": config.high_fidelity.maximum_cumulative_phase_clip_relative,
            "pass": cumulative_clip
            <= config.high_fidelity.maximum_cumulative_phase_clip_relative,
        },
        "cumulative_phase_repair_relative": {
            "value": cumulative_repair,
            "maximum": config.high_fidelity.maximum_cumulative_phase_repair_relative,
            "pass": cumulative_repair
            <= config.high_fidelity.maximum_cumulative_phase_repair_relative,
        },
    }
    finite = all(
        math.isfinite(float(check["value"])) for check in checks.values()
    )
    return {
        "pass": finite and all(bool(check["pass"]) for check in checks.values()),
        "all_values_finite": finite,
        "checks": checks,
    }


def run_probe(
    config_path: Path,
    preregistration_path: Path,
) -> dict[str, object]:
    config = load_config_json(config_path)
    preregistration_raw = read_json_strict(preregistration_path)
    if not isinstance(preregistration_raw, Mapping):
        raise ValueError("preregistration must contain an object")
    contract_gates = validate_probe_contract(config, preregistration_raw)
    git_start = git_identity()
    source_start = source_tree_identity(FORMAL_ROOT)
    started = time.monotonic()
    execution_error: dict[str, str] | None = None
    stage_times: dict[str, float] = {}
    accelerator: dict[str, object] = {}
    step_records: list[dict[str, object]] = []
    monitor: ResourceMonitor | None = None
    initial_volume = math.nan
    resources: dict[str, object] = {}
    if not all(contract_gates.values()):
        execution_error = {
            "type": "ContractError",
            "message": "resource probe configuration does not match preregistration",
        }
    elif git_start["branch"] != "main" or git_start["worktree_clean"] is not True:
        execution_error = {
            "type": "IdentityError",
            "message": "resource probe requires a clean main worktree",
        }
    else:
        try:
            import cupy as cp

            cp.cuda.Device(0).use()
            monitor = ResourceMonitor(cp)
            monitor.start()
            monitor.mark("before_grid")
            backend = HighFidelityBackend()

            stage_started = time.monotonic()
            grid = backend.make_grid(config)
            stage_times["grid_construction_s"] = time.monotonic() - stage_started
            monitor.mark("after_grid")
            print(
                json.dumps(
                    {
                        "stage": "grid_constructed",
                        "elapsed_s": time.monotonic() - started,
                        "active_cells": grid.active_cell_count,
                    }
                ),
                flush=True,
            )

            stage_started = time.monotonic()
            projection, accelerator = make_pressure_projection(config, grid)
            stage_times["pressure_projection_construction_s"] = (
                time.monotonic() - stage_started
            )
            monitor.mark("after_projection")
            print(
                json.dumps(
                    {
                        "stage": "cuda_projection_constructed",
                        "elapsed_s": time.monotonic() - started,
                        "accelerator": accelerator,
                    },
                    default=str,
                ),
                flush=True,
            )

            stage_started = time.monotonic()
            state = backend.initialise_state(config, grid)
            stage_times["state_initialisation_s"] = time.monotonic() - stage_started
            initial_volume = backend._initial_liquid_volume(config, grid)
            expected_volume = initial_volume
            monitor.mark("after_state")
            print(
                json.dumps(
                    {
                        "stage": "state_initialised",
                        "elapsed_s": time.monotonic() - started,
                    }
                ),
                flush=True,
            )

            maximum_steps = int(
                preregistration_raw["d0_scope"]["B_13p5_cuda_resource_probe"][
                    "maximum_accepted_steps"
                ]
            )
            for accepted_index in range(maximum_steps):
                proposal = propose_time_step(state, config, grid)
                dt_s = min(proposal.stable_dt_s, config.high_fidelity.time_step_s)
                limiter = proposal.selected_limiter
                initial_attempt_dt_s = dt_s
                retry_count = 0
                maximum_rejected_cfl = 0.0
                step_started = time.monotonic()
                while True:
                    try:
                        diagnostics = advance_one_step(
                            state,
                            config,
                            grid,
                            projection,
                            dt_s,
                            expected_liquid_volume_m3=expected_volume,
                            time_step_limiter=limiter,
                            cfl_retry_count=retry_count,
                            initial_attempt_dt_s=initial_attempt_dt_s,
                            maximum_rejected_post_projection_cfl=(
                                maximum_rejected_cfl
                            ),
                        )
                        break
                    except HF3DPostProjectionCFLExceeded as exc:
                        maximum_rejected_cfl = max(
                            maximum_rejected_cfl,
                            exc.diagnostics.post_projection_advective_cfl,
                        )
                        if retry_count >= config.high_fidelity.maximum_cfl_step_retries:
                            raise RuntimeError(
                                "resource probe exhausted formal CFL retries"
                            ) from exc
                        retry_count += 1
                        dt_s = retry_time_step_s(
                            dt_s,
                            exc.diagnostics.post_projection_advective_cfl,
                            config.high_fidelity.cfl_limit,
                            config.high_fidelity.cfl_controller_target_fraction,
                        )
                        limiter = "post_projection_cfl_retry"
                step_wall = time.monotonic() - step_started
                expected_volume = diagnostics.expected_liquid_volume_m3
                record = asdict(diagnostics)
                record["accepted_step_wall_s"] = step_wall
                record["proposal"] = asdict(proposal)
                step_records.append(json_ready(record))
                monitor.mark(f"after_step_{accepted_index + 1}")
                print(
                    json.dumps(
                        {
                            "stage": "accepted_step",
                            "accepted_step": accepted_index + 1,
                            "step_wall_s": step_wall,
                            "dt_s": diagnostics.dt_s,
                            "pressure_iterations": diagnostics.pressure_iterations,
                            "pressure_residual": (
                                diagnostics.pressure_relative_residual
                            ),
                            "projected_divergence_s_inv": (
                                diagnostics.divergence_after_s_inv
                            ),
                            "advective_cfl": diagnostics.advective_cfl,
                            "retry_count": diagnostics.cfl_retry_count,
                        }
                    ),
                    flush=True,
                )
        except Exception as exc:
            execution_error = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
        finally:
            if monitor is not None:
                monitor.stop()
                resources = monitor.summary()

    source_end = source_tree_identity(FORMAL_ROOT)
    git_end = git_identity()
    identity = {
        "git_start": git_start,
        "git_end": git_end,
        "formal_source_tree_start": source_start,
        "formal_source_tree_end": source_end,
        "main_branch": git_start["branch"] == git_end["branch"] == "main",
        "worktree_clean_start_and_end": bool(
            git_start["worktree_clean"] and git_end["worktree_clean"]
        ),
        "git_head_stable": git_start["head"] == git_end["head"],
        "formal_source_tree_stable": bool(
            source_start.get("available") is True and source_start == source_end
        ),
    }
    thresholds = preregistration_raw["d0_scope"][
        "B_13p5_cuda_resource_probe"
    ]["numerical_quality_thresholds"]
    numerical_quality = (
        _quality(step_records, config, initial_volume, thresholds)
        if math.isfinite(initial_volume)
        else {"pass": False, "checks": {}, "reason": "state not initialised"}
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "diagnostic_protocol_id": preregistration_raw.get(
            "diagnostic_protocol_id"
        ),
        "status": "ASSESSMENT_PENDING",
        "gate_pass": False,
        "contract_gates": contract_gates,
        "inputs": {
            "config": str(config_path),
            "config_sha256": _sha256(config_path),
            "preregistration": str(preregistration_path),
            "preregistration_sha256": _sha256(preregistration_path),
        },
        "identity": identity,
        "execution": {
            "configured_grid": [
                config.high_fidelity.radial_cells,
                config.high_fidelity.azimuthal_cells,
                config.high_fidelity.axial_cells,
            ],
            "candidate_registered_dt_s": config.high_fidelity.time_step_s,
            "target_accepted_steps": 8,
            "completed_accepted_steps": len(step_records),
            "stage_wall_s": stage_times,
            "total_wall_s": time.monotonic() - started,
            "pressure_accelerator": accelerator,
            "accepted_step_diagnostics": step_records,
        },
        "resources": resources,
        "numerical_quality": numerical_quality,
        "execution_error": execution_error,
        "claim_boundary": {
            "probe_freezes_q1r4": False,
            "probe_authorizes_full_q1r4": False,
            "probe_establishes_numerical_convergence": False,
            "probe_validates_physics": False,
            "probe_allows_apparatus_prediction": False,
        },
    }
    assessment = assess_probe_report(report)
    report["assessment"] = assessment
    report["gate_pass"] = assessment["pass"]
    report["status"] = (
        "Q1R4_D0_RESOURCE_PROBE_PASS__SOURCE_SEPARATION_ALLOWED"
        if assessment["pass"]
        else "Q1R4_D0_RESOURCE_PROBE_FAIL_CLOSED__SOURCE_SEPARATION_NOT_AUTHORIZED"
    )
    return report


def markdown(report: Mapping[str, Any]) -> str:
    execution = report["execution"]
    resources = report["resources"]
    quality = report["numerical_quality"]
    step_rows: list[str] = []
    for index, item in enumerate(execution["accepted_step_diagnostics"], start=1):
        step_rows.append(
            "| {index} | {dt:.8g} | {wall:.6g} | {iterations} | {residual:.6g} | "
            "{divergence:.6g} | {cfl:.6g} | {retry} |".format(
                index=index,
                dt=float(item["dt_s"]),
                wall=float(item["accepted_step_wall_s"]),
                iterations=int(item["pressure_iterations"]),
                residual=float(item["pressure_relative_residual"]),
                divergence=float(item["divergence_after_s_inv"]),
                cfl=float(item["advective_cfl"]),
                retry=int(item["cfl_retry_count"]),
            )
        )
    check_rows: list[str] = []
    for name, check in quality.get("checks", {}).items():
        check_rows.append(
            f"| `{name}` | {float(check['value']):.8g} | "
            f"{float(check['maximum']):.8g} | {check['pass']} |"
        )
    error = report.get("execution_error")
    error_text = "无" if error is None else f"`{error['type']}: {error['message']}`"
    return f"""# Q1R4 D0-B `13.5` 格 CUDA 八步资源探针

状态：`{report['status']}`。

该探针只检查正式 `204 × 204 × 180` 构造和八个已接受步骤。候选登记步长 `3.75e-6 s` 不是 Q1R4 冻结值；探针通过也不授权完整资格组。

## 身份与执行

- 完成接受步：`{execution['completed_accepted_steps']}/8`。
- 压力加速器：`{execution['pressure_accelerator'].get('selected')}`；回退：`{execution['pressure_accelerator'].get('fallback')}`。
- 总墙钟：`{execution['total_wall_s']:.6g} s`。
- 执行错误：{error_text}。

## 资源峰值

- 进程 RSS 峰值：`{resources.get('process_rss_peak_bytes')}` byte。
- 主机最低可用内存：`{resources.get('host_available_memory_min_bytes')}` byte。
- CUDA 总量：`{resources.get('cuda_total_memory_bytes')}` byte；最低空闲量：`{resources.get('cuda_free_memory_min_bytes')}` byte。
- CUDA 全局空闲量相对基线的峰值下降：`{resources.get('cuda_global_peak_delta_from_baseline_bytes')}` byte。
- CuPy 内存池 used/total 峰值：`{resources.get('cupy_pool_used_peak_bytes')}` / `{resources.get('cupy_pool_total_peak_bytes')}` byte。
- `nvidia-smi` 本进程峰值：`{resources.get('nvidia_smi_process_peak_bytes')}` byte。
- 连续采样：`{resources.get('sample_count')}` 点，间隔 `{resources.get('sampling_interval_s')} s`。

## 八个接受步骤

| 步 | 实际步长 s | 墙钟 s | 压力迭代 | 压力残差 | 投影散度 1/s | CFL | 重试 |
|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(step_rows)}

## 数值质量门

| 门 | 实测 | 上限 | 通过 |
|---|---:|---:|---:|
{chr(10).join(check_rows)}

## 结论边界

D0-B 总门为 `{report['gate_pass']}`。无论结果如何，本探针不建立空间/时间收敛，不验证物理或实验，也不允许装置级预测。
"""


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--preregistration",
        type=Path,
        default=HERE / "q1r4_diagnostic_preregistration.json",
    )
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    if args.json_output.exists() or args.markdown_output.exists():
        raise FileExistsError("resource probe output already exists")
    report = run_probe(
        args.config.resolve(),
        args.preregistration.resolve(),
    )
    _write_json(args.json_output.resolve(), report)
    args.markdown_output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.resolve().write_text(markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {"status": report["status"], "gate_pass": report["gate_pass"]},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
