#!/usr/bin/env python3
"""Execute the preregistered D1 G0/G1 lockstep dynamic shadow stages.

Raw run products live below ``artifacts/runs`` (ignored by repository policy).
Only the independent analyser publishes compact tracked reports after all
authorised stages have stopped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
import traceback
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
STATIC_ROOT = HERE.parent / "04_D1可控磁子离散"
Q1R3_ROOT = HERE.parent / "01_HF3D数值资格"
PREREGISTRATION_PATH = HERE / "d1_dynamic_shadow_preregistration.json"
RAW_ROOT = HERE / "artifacts" / "runs"
VERIFY_PATH = RAW_ROOT / "g0_self_identity.json"
SMOKE_PATH = RAW_ROOT / "g0_g1_smoke.json"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from airvortex._hardware import NUMERICAL_THREAD_POLICY
from airvortex.backends.high_fidelity_3d import (
    CAPABILITIES,
    LIMITATIONS,
    MODEL_NAME,
    HighFidelityBackend,
)
from airvortex.core import Event, RunStatus, SimulationConfig
from airvortex.hf3d.events import TopologyEventTracker
from airvortex.hf3d.pressure import (
    PressureProjection3D,
    make_pressure_projection,
)
from airvortex.hf3d.recording import (
    FrameRecorder,
    StepDiagnosticsBuffer,
    assemble_result,
)
from airvortex.hf3d.timestep import (
    propose_time_step,
    retry_time_step_s,
)
from airvortex.hf3d.types import (
    HF3DPostProjectionCFLExceeded,
    HF3DState,
    HF3DStepDiagnostics,
)
from airvortex.io import load_run_archive, save_run_archive
from airvortex.io.archive import (
    git_revision_identity,
    source_tree_identity,
)
from airvortex.io.config_io import (
    canonical_json_bytes,
    read_json_strict,
)
from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from controlled_shadow_grid import (
    REBUILT_FIELDS,
    BoundG1Mask,
    array_sha256,
    build_bound_nominal_g1_mask,
    compare_rebuilt_fields,
    install_shadow_bar_mask,
    snapshot_rebuilt_fields,
)

PROTOCOL_ID = "D1_G0_G1_LOCKSTEP_DYNAMIC_SHADOW_V1"
FAIL_CLOSED = "D1_DYNAMIC_SHADOW_INCONCLUSIVE_FAIL_CLOSED"
G0_FAIL_CLOSED = "D1_DYNAMIC_SHADOW_FAIL_CLOSED_AT_G0_SELF_IDENTITY"
LEVEL_CONFIGS: dict[int, Path] = {
    4: Q1R3_ROOT / "generated_q1r3" / "space_L0_4cells.json",
    6: Q1R3_ROOT / "generated_q1r3" / "space_L1_6cells.json",
    9: Q1R3_ROOT / "generated_q1r3" / "space_L2_9cells.json",
}


class ShadowExecutionError(RuntimeError):
    """Raised when a preregistered execution or identity gate fails."""


@dataclass(slots=True)
class VariantRuntime:
    label: str
    grid: ImmersedCartesianGrid
    state: HF3DState
    projection: PressureProjection3D
    pressure_accelerator: dict[str, object]
    initial_volume_m3: float
    expected_volume_m3: float
    recorder: FrameRecorder
    diagnostics: StepDiagnosticsBuffer
    events: list[Event]
    event_tracker: TopologyEventTracker
    accepted_times_s: list[float]
    liquid_balance_relative: list[float]
    last_diagnostics: HF3DStepDiagnostics
    geometry_installation: dict[str, Any]


@dataclass(slots=True)
class PairOutcome:
    variants: tuple[VariantRuntime, VariantRuntime]
    completed: bool
    target_time_s: float | None
    target_steps: int | None
    failure: dict[str, Any] | None
    retry_attempts: int
    wall_time_s: float
    process_cpu_time_s: float


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    value = read_json_strict(path)
    if not isinstance(value, dict):
        raise ShadowExecutionError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    path.write_text(text + "\n", encoding="utf-8")


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()


def _blob_sha(path: Path, revision: str = "HEAD") -> str:
    return _git("rev-parse", f"{revision}:{_relative(path)}")


def _capture_source(sample_point: str) -> dict[str, Any]:
    identity = dict(source_tree_identity(FORMAL_ROOT))
    identity["sample_point"] = sample_point
    return identity


def _capture_revision(sample_point: str) -> dict[str, Any]:
    identity = dict(git_revision_identity(REPOSITORY_ROOT))
    identity["sample_point"] = sample_point
    return identity


def audit_execution_identity() -> dict[str, Any]:
    """Execute S0 against every path/blob/source binding in the preregistration."""

    prereg = _json_object(PREREGISTRATION_PATH)
    bindings = prereg.get("evidence_bindings")
    if not isinstance(bindings, Mapping):
        raise ShadowExecutionError("preregistration evidence_bindings is missing")
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    source = _capture_source("D1 S0 execution boundary")

    gates: dict[str, bool] = {
        "main_branch": branch == "main",
        "clean_worktree": not porcelain,
        "formal_source_available": source.get("available") is True,
        "formal_source_tree_matches_binding": (
            source.get("value") == bindings.get("formal_source_tree_sha256")
        ),
        "decision_id_matches": (
            prereg.get("decision_id")
            == "D1_CANDIDATE_ONLY_LOCKSTEP_DYNAMIC_SHADOW_AUTHORIZED__NOT_EXECUTED"
        ),
        "protocol_id_matches": prereg.get("protocol_id") == PROTOCOL_ID,
    }
    bound_files: dict[str, Any] = {}
    for name in (
        "d1_geometry_preregistration",
        "controlled_bar_geometry",
        "static_audit",
        "static_analysis",
        "q1r3_campaign",
        "q1r3_spatial_decision",
    ):
        binding = bindings.get(name)
        if not isinstance(binding, Mapping) or not isinstance(binding.get("path"), str):
            gates[f"{name}_binding"] = False
            continue
        path = REPOSITORY_ROOT / str(binding["path"])
        observed_blob = _blob_sha(path)
        expected_blob = binding.get("git_blob_sha")
        observed_content = _sha256(path)
        expected_content = binding.get("content_sha256_from_static_audit")
        blob_match = observed_blob == expected_blob
        content_match = expected_content is None or observed_content == expected_content
        gates[f"{name}_blob"] = blob_match
        gates[f"{name}_content"] = content_match
        bound_files[name] = {
            "path": _relative(path),
            "observed_git_blob_sha": observed_blob,
            "expected_git_blob_sha": expected_blob,
            "observed_content_sha256": observed_content,
            "expected_content_sha256": expected_content,
        }

    baseline_bindings = bindings.get("baseline_configs")
    if not isinstance(baseline_bindings, Mapping):
        gates["baseline_config_bindings"] = False
    else:
        for name, binding in baseline_bindings.items():
            if not isinstance(binding, Mapping) or not isinstance(
                binding.get("path"), str
            ):
                gates[f"baseline_{name}_blob"] = False
                continue
            path = REPOSITORY_ROOT / str(binding["path"])
            observed_blob = _blob_sha(path)
            gates[f"baseline_{name}_blob"] = observed_blob == binding.get(
                "git_blob_sha"
            )
            bound_files[f"baseline_{name}"] = {
                "path": _relative(path),
                "observed_git_blob_sha": observed_blob,
                "expected_git_blob_sha": binding.get("git_blob_sha"),
                "observed_content_sha256": _sha256(path),
            }

    static_audit = _json_object(REPOSITORY_ROOT / str(bindings["static_audit"]["path"]))
    static_analysis = _json_object(
        REPOSITORY_ROOT / str(bindings["static_analysis"]["path"])
    )
    spatial_decision = _json_object(
        REPOSITORY_ROOT / str(bindings["q1r3_spatial_decision"]["path"])
    )
    gates["static_audit_gate_pass"] = static_audit.get("gate_pass") is True
    gates["static_analysis_decision"] = static_analysis.get("status") == bindings[
        "static_analysis"
    ].get("decision_required")
    gates["q1r3_failure_preserved"] = spatial_decision.get("status") == bindings[
        "q1r3_spatial_decision"
    ].get("decision_required")
    gate_pass = all(gates.values())
    result = {
        "stage": "S0",
        "gate_pass": gate_pass,
        "gates": gates,
        "branch": branch,
        "execution_commit": head,
        "reviewed_head": prereg.get("reviewed_head"),
        "worktree_porcelain": porcelain,
        "formal_source_tree": source,
        "bound_files": bound_files,
    }
    if not gate_pass:
        raise ShadowExecutionError(
            "D1 S0 evidence/source identity audit failed: "
            + json.dumps(
                [name for name, passed in gates.items() if not passed],
                ensure_ascii=False,
            )
        )
    return result


def _state_arrays(state: HF3DState) -> dict[str, NDArray[Any]]:
    return {
        "u_x_face": np.asarray(state.u_x_face),
        "u_y_face": np.asarray(state.u_y_face),
        "u_z_face": np.asarray(state.u_z_face),
        "phase": np.asarray(state.phase),
        "pressure_pa": np.asarray(state.pressure_pa),
        "time_s": np.asarray([state.time_s], dtype=np.float64),
        "step": np.asarray([state.step], dtype=np.int64),
        "bar_angle_rad": np.asarray([state.bar_angle_rad], dtype=np.float64),
    }


def _clone_state(state: HF3DState) -> HF3DState:
    return HF3DState(
        time_s=state.time_s,
        step=state.step,
        bar_angle_rad=state.bar_angle_rad,
        u_x_face=state.u_x_face.copy(),
        u_y_face=state.u_y_face.copy(),
        u_z_face=state.u_z_face.copy(),
        phase=state.phase.copy(),
        pressure_pa=state.pressure_pa.copy(),
    )


def _arrays_identity(
    left: Mapping[str, NDArray[Any]],
    right: Mapping[str, NDArray[Any]],
) -> dict[str, Any]:
    names_equal = set(left) == set(right)
    names = sorted(set(left) | set(right))
    detail: dict[str, Any] = {}
    for name in names:
        if name not in left or name not in right:
            detail[name] = {"present_both": False, "equal": False}
            continue
        a = np.asarray(left[name])
        b = np.asarray(right[name])
        detail[name] = {
            "present_both": True,
            "dtype_equal": a.dtype == b.dtype,
            "shape_equal": a.shape == b.shape,
            "equal": bool(
                a.dtype == b.dtype
                and a.shape == b.shape
                and np.array_equal(a, b, equal_nan=True)
            ),
            "left_sha256": array_sha256(a),
            "right_sha256": array_sha256(b),
        }
    return {
        "pass": names_equal
        and all(item.get("equal") is True for item in detail.values()),
        "names_equal": names_equal,
        "arrays": detail,
    }


def _all_finite(value: object) -> bool:
    if value is None or isinstance(value, (bool, str, int, np.integer)):
        return True
    if isinstance(value, (float, np.floating)):
        return math.isfinite(float(value))
    if isinstance(value, np.ndarray):
        return bool(np.isfinite(value).all())
    if is_dataclass(value):
        return all(_all_finite(getattr(value, item.name)) for item in fields(value))
    if isinstance(value, Mapping):
        return all(_all_finite(item) for item in value.values())
    if isinstance(value, Sequence):
        return all(_all_finite(item) for item in value)
    return False


def _config_raw(level: int, *, duration_s: float | None, device: str) -> dict[str, Any]:
    if level not in LEVEL_CONFIGS:
        raise ShadowExecutionError(f"unsupported D1 level: {level}")
    raw = _json_object(LEVEL_CONFIGS[level])
    result = deepcopy(raw)
    high_fidelity = result.get("high_fidelity")
    run = result.get("run")
    if not isinstance(high_fidelity, dict) or not isinstance(run, dict):
        raise ShadowExecutionError("baseline config is missing run/high_fidelity")
    high_fidelity["pressure_solver_device"] = device
    if duration_s is not None:
        run["duration_s"] = duration_s
    return result


def _effective_config(
    level: int,
    *,
    duration_s: float | None = None,
    device: str = "cuda",
) -> SimulationConfig:
    return SimulationConfig.from_dict(
        _config_raw(level, duration_s=duration_s, device=device)
    )


def _full_window_config_identity(level: int) -> dict[str, Any]:
    baseline = _json_object(LEVEL_CONFIGS[level])
    effective = _config_raw(level, duration_s=0.010, device="cuda")
    expected = deepcopy(baseline)
    expected["run"]["duration_s"] = 0.010
    prereg = _json_object(PREREGISTRATION_PATH)
    protocol = prereg.get("execution_protocol")
    if not isinstance(protocol, Mapping):
        raise ShadowExecutionError("execution_protocol is missing")
    required = protocol.get("required_unchanged_values")
    if not isinstance(required, Mapping):
        raise ShadowExecutionError("required_unchanged_values is missing")

    def deep_get(root: Mapping[str, Any], dotted: str) -> Any:
        value: Any = root
        for token in dotted.split("."):
            if not isinstance(value, Mapping):
                raise ShadowExecutionError(f"config path is not a mapping: {dotted}")
            value = value[token]
        return value

    unchanged = {
        name: deep_get(effective, name) == frozen for name, frozen in required.items()
    }
    gates = {
        "only_duration_override": effective == expected,
        "required_values_unchanged": all(unchanged.values()),
        "baseline_file_blob_is_committed": bool(_blob_sha(LEVEL_CONFIGS[level])),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "required_values": unchanged,
        "baseline_path": _relative(LEVEL_CONFIGS[level]),
        "baseline_content_sha256": _sha256(LEVEL_CONFIGS[level]),
        "baseline_canonical_sha256": _canonical_sha256(baseline),
        "effective_canonical_sha256": _canonical_sha256(effective),
        "allowed_override": {"run.duration_s": 0.010},
    }


def _prepare_variant(
    config: SimulationConfig,
    *,
    label: str,
    candidate_mask: NDArray[np.bool_] | None,
    rebuild: bool,
) -> VariantRuntime:
    backend = HighFidelityBackend()
    grid = backend.make_grid(config)
    formal_mask = grid.bar_mask.copy()
    geometry_installation: dict[str, Any]
    if rebuild:
        geometry_installation = install_shadow_bar_mask(
            grid,
            formal_mask if candidate_mask is None else candidate_mask,
        )
    elif candidate_mask is not None:
        raise ShadowExecutionError(
            "formal comparison grid cannot accept a candidate mask"
        )
    else:
        geometry_installation = {
            "formal_constructor_without_candidate_rebuild": True,
            "mask_sha256": array_sha256(grid.bar_mask.astype(np.uint8)),
            "bar_cell_count": int(np.sum(grid.bar_mask)),
            "rebuilt_fields": {},
        }

    # The installation boundary is intentionally above all three constructors.
    projection, pressure_accelerator = make_pressure_projection(config, grid)
    state = backend.initialise_state(config, grid)
    initial_volume = backend._initial_liquid_volume(config, grid)
    expected_volume = initial_volume
    recorder = FrameRecorder(config, grid, state, initial_volume)
    diagnostics_buffer = StepDiagnosticsBuffer(initial_volume)
    events: list[Event] = []
    event_tracker = TopologyEventTracker(config, events)
    zero = backend._zero_step_diagnostics(state, config, grid, expected_volume)
    recorder.record_frame(zero)
    event_tracker.initialise(zero.topology)
    return VariantRuntime(
        label=label,
        grid=grid,
        state=state,
        projection=projection,
        pressure_accelerator=dict(pressure_accelerator),
        initial_volume_m3=initial_volume,
        expected_volume_m3=expected_volume,
        recorder=recorder,
        diagnostics=diagnostics_buffer,
        events=events,
        event_tracker=event_tracker,
        accepted_times_s=[],
        liquid_balance_relative=[],
        last_diagnostics=zero,
        geometry_installation=geometry_installation,
    )


def _limiter_for_pair(
    stable_g0: float,
    limiter_g0: str,
    stable_g1: float,
    limiter_g1: str,
    remaining: float,
) -> str:
    stable = min(stable_g0, stable_g1)
    if remaining < stable:
        return "output_alignment"
    return limiter_g0 if stable_g0 <= stable_g1 else limiter_g1


def _step_hard_gate(
    runtime: VariantRuntime,
    diagnostics: HF3DStepDiagnostics,
    config: SimulationConfig,
) -> tuple[bool, list[str]]:
    failed: list[str] = []
    if not _all_finite(runtime.state) or not _all_finite(diagnostics):
        failed.append("all_state_and_diagnostics_finite")
    if diagnostics.pressure_relative_residual > 1.0e-7:
        failed.append("pressure_relative_residual")
    if diagnostics.divergence_after_s_inv > 1.0e-4:
        failed.append("projected_divergence")
    if diagnostics.advective_cfl > 0.35 * (1.0 + 1.0e-12):
        failed.append("advective_cfl")
    balance = (
        abs(diagnostics.liquid_volume_balance_error_m3) / runtime.initial_volume_m3
    )
    if balance > 0.001:
        failed.append("liquid_volume_balance_error_relative")
    if config.high_fidelity.pressure_solver_device == "cuda" and not (
        runtime.pressure_accelerator.get("selected") == "cuda"
        and runtime.pressure_accelerator.get("fallback") is False
    ):
        failed.append("cuda_selected_without_fallback")
    return not failed, failed


def run_lockstep_pair(
    variants: tuple[VariantRuntime, VariantRuntime],
    config: SimulationConfig,
    *,
    target_time_s: float | None,
    target_steps: int | None,
) -> PairOutcome:
    """Advance two variants transactionally on one accepted time grid."""

    if (target_time_s is None) == (target_steps is None):
        raise ValueError("exactly one of target_time_s/target_steps is required")
    backend = HighFidelityBackend()
    start_wall = time.monotonic()
    start_cpu = time.process_time()
    total_retry_attempts = 0
    failure: dict[str, Any] | None = None
    end_time = config.run.duration_s if target_time_s is None else target_time_s
    output_interval = config.run.output_interval_s
    next_output = min(output_interval, end_time)
    tolerance = 128.0 * np.finfo(float).eps * max(end_time, 1.0)

    try:
        while True:
            completed_steps = variants[0].state.step
            if target_steps is not None and completed_steps >= target_steps:
                break
            if (
                target_time_s is not None
                and variants[0].state.time_s >= end_time - tolerance
            ):
                break
            if variants[0].state.time_s != variants[1].state.time_s:
                raise ShadowExecutionError("pair state times diverged before a trial")

            proposals = tuple(
                propose_time_step(runtime.state, config, runtime.grid)
                for runtime in variants
            )
            target = (
                min(next_output, end_time) if target_time_s is not None else math.inf
            )
            remaining = (
                target - variants[0].state.time_s if math.isfinite(target) else math.inf
            )
            dt_s = min(
                proposals[0].stable_dt_s,
                proposals[1].stable_dt_s,
                remaining,
            )
            if not math.isfinite(dt_s) or dt_s <= 0.0:
                raise FloatingPointError(
                    "paired adaptive time step became non-positive"
                )
            limiter = _limiter_for_pair(
                proposals[0].stable_dt_s,
                proposals[0].selected_limiter,
                proposals[1].stable_dt_s,
                proposals[1].selected_limiter,
                remaining,
            )
            initial_attempt_dt_s = dt_s
            retry_count = 0
            maximum_rejected = [0.0, 0.0]

            while True:
                checkpoints = tuple(
                    runtime.projection.checkpoint_history() for runtime in variants
                )
                trial_states = tuple(
                    _clone_state(runtime.state) for runtime in variants
                )
                trial_diagnostics: list[HF3DStepDiagnostics | None] = [None, None]
                rejections: list[HF3DPostProjectionCFLExceeded | None] = [None, None]
                unexpected: Exception | None = None
                for index, runtime in enumerate(variants):
                    try:
                        trial_diagnostics[index] = backend.advance_one_step(
                            trial_states[index],
                            config,
                            runtime.grid,
                            runtime.projection,
                            dt_s,
                            expected_liquid_volume_m3=runtime.expected_volume_m3,
                            time_step_limiter=limiter,
                            cfl_retry_count=retry_count,
                            initial_attempt_dt_s=initial_attempt_dt_s,
                            maximum_rejected_post_projection_cfl=maximum_rejected[
                                index
                            ],
                        )
                    except HF3DPostProjectionCFLExceeded as exc:
                        rejections[index] = exc
                    except Exception as exc:  # noqa: BLE001 - preserve partial evidence fail-closed
                        unexpected = exc
                        break

                if unexpected is not None:
                    for runtime, checkpoint in zip(variants, checkpoints):
                        runtime.projection.restore_history(checkpoint)
                    raise unexpected

                if any(item is not None for item in rejections):
                    for runtime, checkpoint in zip(variants, checkpoints):
                        runtime.projection.restore_history(checkpoint)
                    if retry_count >= config.high_fidelity.maximum_cfl_step_retries:
                        raise FloatingPointError(
                            "paired CFL retry gate exhausted at accepted step "
                            f"{variants[0].state.step + 1}"
                        )
                    next_steps = [dt_s]
                    for index, rejection in enumerate(rejections):
                        if rejection is None:
                            continue
                        observed = rejection.diagnostics.post_projection_advective_cfl
                        maximum_rejected[index] = max(maximum_rejected[index], observed)
                        next_steps.append(
                            retry_time_step_s(
                                dt_s,
                                observed,
                                config.high_fidelity.cfl_limit,
                                config.high_fidelity.cfl_controller_target_fraction,
                            )
                        )
                    dt_s = min(next_steps)
                    retry_count += 1
                    total_retry_attempts += 1
                    limiter = "post_projection_cfl_retry"
                    for runtime in variants:
                        runtime.events.append(
                            Event(
                                runtime.state.time_s,
                                "paired_cfl_step_retry",
                                "Both D1 trial states were discarded and retried at the stricter paired step.",
                                {
                                    "retry_number": retry_count,
                                    "next_dt_s": dt_s,
                                },
                            )
                        )
                    continue

                if any(item is None for item in trial_diagnostics):
                    raise ShadowExecutionError(
                        "paired trial produced incomplete diagnostics"
                    )
                for index, runtime in enumerate(variants):
                    runtime.state = trial_states[index]
                    runtime.recorder.state = runtime.state
                    diagnostics = trial_diagnostics[index]
                    assert diagnostics is not None
                    runtime.last_diagnostics = diagnostics
                    runtime.expected_volume_m3 = diagnostics.expected_liquid_volume_m3
                    runtime.event_tracker.update(runtime.state.time_s, diagnostics)
                    runtime.diagnostics.append(
                        runtime.state.time_s, diagnostics, config
                    )
                    runtime.accepted_times_s.append(runtime.state.time_s)
                    runtime.liquid_balance_relative.append(
                        diagnostics.liquid_volume_balance_error_m3
                        / runtime.initial_volume_m3
                    )
                    passed, failed = _step_hard_gate(runtime, diagnostics, config)
                    if not passed:
                        raise FloatingPointError(
                            f"{runtime.label} hard gate failed: {', '.join(failed)}"
                        )
                times0 = np.asarray(variants[0].accepted_times_s, dtype=np.float64)
                times1 = np.asarray(variants[1].accepted_times_s, dtype=np.float64)
                if times0.tobytes() != times1.tobytes():
                    raise ShadowExecutionError(
                        "accepted time arrays are not bitwise equal"
                    )
                break

            if target_time_s is not None:
                reached_output = variants[0].state.time_s >= next_output - tolerance
                reached_end = variants[0].state.time_s >= end_time - tolerance
                if reached_output or reached_end:
                    snap = end_time if reached_end else next_output
                    for runtime in variants:
                        if abs(runtime.state.time_s - snap) <= tolerance:
                            runtime.state.time_s = snap
                        runtime.recorder.record_frame(runtime.last_diagnostics)
                    while next_output <= variants[0].state.time_s + tolerance:
                        next_output += output_interval
                    print(
                        json.dumps(
                            {
                                "stage": "lockstep_progress",
                                "time_s": variants[0].state.time_s,
                                "accepted_steps": variants[0].state.step,
                                "retry_attempts": total_retry_attempts,
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
    except Exception as exc:  # noqa: BLE001 - archive unexpected numerical failure
        failure = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "accepted_steps": [runtime.state.step for runtime in variants],
            "accepted_times_bitwise_equal": (
                np.asarray(variants[0].accepted_times_s, dtype=np.float64).tobytes()
                == np.asarray(variants[1].accepted_times_s, dtype=np.float64).tobytes()
            ),
        }

    completed = failure is None
    if target_steps is not None:
        completed = completed and all(
            runtime.state.step == target_steps for runtime in variants
        )
    if target_time_s is not None:
        completed = completed and all(
            abs(runtime.state.time_s - target_time_s) <= tolerance
            for runtime in variants
        )
    if target_steps is not None and completed:
        for runtime in variants:
            if (
                not runtime.recorder.times
                or runtime.recorder.times[-1] != runtime.state.time_s
            ):
                runtime.recorder.record_frame(runtime.last_diagnostics)
    return PairOutcome(
        variants=variants,
        completed=completed,
        target_time_s=target_time_s,
        target_steps=target_steps,
        failure=failure,
        retry_attempts=total_retry_attempts,
        wall_time_s=time.monotonic() - start_wall,
        process_cpu_time_s=time.process_time() - start_cpu,
    )


def _runtime_arrays(runtime: VariantRuntime) -> dict[str, NDArray[Any]]:
    arrays = {
        f"state_{name}": value.copy()
        for name, value in _state_arrays(runtime.state).items()
    }
    arrays["accepted_time_s"] = np.asarray(runtime.accepted_times_s, dtype=np.float64)
    arrays["liquid_balance_relative"] = np.asarray(
        runtime.liquid_balance_relative, dtype=np.float64
    )
    for name, value in vars(runtime.diagnostics).items():
        if isinstance(value, list):
            arrays[f"diagnostic_{name}"] = np.asarray(value, dtype=np.float64)
    for name, value in runtime.recorder.observable_lists.items():
        arrays[f"observable_{name}"] = np.asarray(value, dtype=np.float64)
    for name in REBUILT_FIELDS:
        arrays[f"grid_{name}"] = np.asarray(getattr(runtime.grid, name)).copy()
    return arrays


def _save_npz(path: Path, arrays: Mapping[str, NDArray[Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    np.savez_compressed(path, **arrays)
    return {
        "path": _relative(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "array_count": len(arrays),
    }


def _runtime_quality(
    runtime: VariantRuntime, config: SimulationConfig
) -> dict[str, Any]:
    dt = np.asarray(runtime.diagnostics.step_dt, dtype=np.float64)
    tolerance = max(1.0e-15, config.high_fidelity.time_step_s * 1.0e-9)
    fraction = (
        float(np.mean(np.abs(dt - config.high_fidelity.time_step_s) <= tolerance))
        if dt.size
        else 0.0
    )
    pressure = np.asarray(runtime.diagnostics.step_pressure_residual, dtype=np.float64)
    divergence = np.asarray(runtime.diagnostics.step_divergence, dtype=np.float64)
    cfl = np.asarray(runtime.diagnostics.step_post_projection_cfl, dtype=np.float64)
    balance = np.asarray(runtime.liquid_balance_relative, dtype=np.float64)
    clip = runtime.diagnostics.cumulative_clip / runtime.initial_volume_m3
    repair = runtime.diagnostics.cumulative_repair / runtime.initial_volume_m3
    gates = {
        "all_state_and_diagnostics_finite": bool(
            _all_finite(runtime.state)
            and np.isfinite(dt).all()
            and np.isfinite(pressure).all()
            and np.isfinite(divergence).all()
            and np.isfinite(cfl).all()
            and np.isfinite(balance).all()
        ),
        "configured_time_step_active_fraction": fraction >= 0.95,
        "pressure_relative_residual": bool(
            pressure.size and float(np.max(pressure)) <= 1.0e-7
        ),
        "projected_divergence": bool(
            divergence.size and float(np.max(divergence)) <= 1.0e-4
        ),
        "advective_cfl": bool(
            cfl.size and float(np.max(cfl)) <= 0.35 * (1.0 + 1.0e-12)
        ),
        "cumulative_phase_clip_relative": clip <= 0.001,
        "cumulative_phase_repair_relative": repair <= 0.001,
        "liquid_volume_balance_error_relative": bool(
            balance.size and float(np.max(np.abs(balance))) <= 0.001
        ),
        "cuda_selected_without_fallback": (
            config.high_fidelity.pressure_solver_device != "cuda"
            or (
                runtime.pressure_accelerator.get("selected") == "cuda"
                and runtime.pressure_accelerator.get("fallback") is False
            )
        ),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "metrics": {
            "accepted_steps": runtime.state.step,
            "simulated_time_s": runtime.state.time_s,
            "configured_time_step_active_fraction": fraction,
            "maximum_pressure_relative_residual": float(np.max(pressure))
            if pressure.size
            else None,
            "maximum_projected_divergence_s_inv": float(np.max(divergence))
            if divergence.size
            else None,
            "maximum_advective_cfl": float(np.max(cfl)) if cfl.size else None,
            "cumulative_phase_clip_relative": clip,
            "cumulative_phase_repair_relative": repair,
            "maximum_liquid_volume_balance_error_relative": (
                float(np.max(np.abs(balance))) if balance.size else None
            ),
        },
    }


def _pair_summary(
    outcome: PairOutcome,
    config: SimulationConfig,
) -> dict[str, Any]:
    time0 = np.asarray(outcome.variants[0].accepted_times_s, dtype=np.float64)
    time1 = np.asarray(outcome.variants[1].accepted_times_s, dtype=np.float64)
    quality = {
        runtime.label: _runtime_quality(runtime, config) for runtime in outcome.variants
    }
    common_gates = {
        "pair_completed": outcome.completed,
        "both_variants_complete": all(
            runtime.state.step == outcome.variants[0].state.step
            for runtime in outcome.variants
        ),
        "accepted_time_arrays_bitwise_equal": time0.tobytes() == time1.tobytes(),
        "all_variant_quality_gates": all(
            item["gate_pass"] for item in quality.values()
        ),
    }
    return {
        "protocol_id": PROTOCOL_ID,
        "status": (
            "D1_LOCKSTEP_PAIR_PASS" if all(common_gates.values()) else FAIL_CLOSED
        ),
        "gate_pass": all(common_gates.values()),
        "gates": common_gates,
        "failure": outcome.failure,
        "target_time_s": outcome.target_time_s,
        "target_steps": outcome.target_steps,
        "accepted_time_count": int(time0.size),
        "accepted_time_sha256": array_sha256(time0),
        "accepted_times_s": time0.tolist(),
        "retry_attempts": outcome.retry_attempts,
        "wall_time_s": outcome.wall_time_s,
        "process_cpu_time_s": outcome.process_cpu_time_s,
        "quality": quality,
        "variants": {
            runtime.label: {
                "pressure_accelerator": runtime.pressure_accelerator,
                "geometry_installation": runtime.geometry_installation,
                "bar_mask_sha256": array_sha256(runtime.grid.bar_mask.astype(np.uint8)),
                "bar_cell_count": int(np.sum(runtime.grid.bar_mask)),
            }
            for runtime in outcome.variants
        },
    }


def _perturb_mask(
    mask: NDArray[np.bool_], grid: ImmersedCartesianGrid
) -> NDArray[np.bool_]:
    result = mask.copy()
    candidates = np.argwhere(grid.vessel_mask & ~mask)
    candidates = candidates[candidates[:, 2] < grid.nz - 1]
    if candidates.size == 0:
        raise ShadowExecutionError(
            "no valid fluid cell is available for round-trip perturbation"
        )
    i, j, k = (int(value) for value in candidates[0])
    result[i, j, k] = True
    return result


def _verify_static_g0_level(level: int) -> dict[str, Any]:
    config = _effective_config(level)
    backend = HighFidelityBackend()
    formal = backend.make_grid(config)
    rebuilt = backend.make_grid(config)
    expected = snapshot_rebuilt_fields(formal)
    original_mask = rebuilt.bar_mask.copy()
    perturbed = _perturb_mask(original_mask, rebuilt)
    perturb_report = install_shadow_bar_mask(rebuilt, perturbed)
    perturb_changed = not np.array_equal(rebuilt.bar_mask, original_mask)
    restore_report = install_shadow_bar_mask(rebuilt, original_mask)
    field_identity = compare_rebuilt_fields(expected, rebuilt)
    formal_state = backend.initialise_state(config, formal)
    rebuilt_state = backend.initialise_state(config, rebuilt)
    state_identity = _arrays_identity(
        _state_arrays(formal_state), _state_arrays(rebuilt_state)
    )
    gates = {
        "perturbation_was_material": perturb_changed,
        "no_g0_noop_special_case": (
            perturb_report.get("installer_has_g0_noop_special_case") is False
            and restore_report.get("installer_has_g0_noop_special_case") is False
        ),
        "all_rebuilt_arrays_bitwise_equal": field_identity["pass"],
        "initial_state_arrays_bitwise_equal": state_identity["pass"],
    }
    return {
        "level_cells_per_bar_diameter": level,
        "grid_shape": list(formal.shape),
        "gate_pass": all(gates.values()),
        "gates": gates,
        "field_identity": field_identity,
        "initial_state_identity": state_identity,
        "perturbed_mask_sha256": perturb_report["mask_sha256"],
        "restored_mask_sha256": restore_report["mask_sha256"],
    }


def _g0_replay(device: str) -> dict[str, Any]:
    config = _effective_config(4, device=device)
    formal = _prepare_variant(
        config,
        label="formal_g0",
        candidate_mask=None,
        rebuild=False,
    )
    rebuilt = _prepare_variant(
        config,
        label="rebuilt_g0",
        candidate_mask=None,
        rebuild=True,
    )
    outcome = run_lockstep_pair(
        (formal, rebuilt), config, target_time_s=None, target_steps=8
    )
    arrays_formal = _runtime_arrays(formal)
    arrays_rebuilt = _runtime_arrays(rebuilt)
    archive_identity = _arrays_identity(arrays_formal, arrays_rebuilt)
    raw_dir = RAW_ROOT / "g0_replay" / device
    formal_archive = _save_npz(raw_dir / "formal_g0.npz", arrays_formal)
    rebuilt_archive = _save_npz(raw_dir / "rebuilt_g0.npz", arrays_rebuilt)
    pair = _pair_summary(outcome, config)
    gates = {
        "pair_gate": pair["gate_pass"],
        "all_comparable_archive_arrays_bitwise_equal": archive_identity["pass"],
        "accepted_steps_exactly_eight": formal.state.step == rebuilt.state.step == 8,
        "requested_device_selected": (
            formal.pressure_accelerator.get("selected") == device
            and rebuilt.pressure_accelerator.get("selected") == device
        ),
        "cuda_without_fallback": (
            device != "cuda"
            or (
                formal.pressure_accelerator.get("fallback") is False
                and rebuilt.pressure_accelerator.get("fallback") is False
            )
        ),
    }
    return {
        "device": device,
        "gate_pass": all(gates.values()),
        "gates": gates,
        "pair": pair,
        "archive_identity": archive_identity,
        "archives": {
            "formal_g0": formal_archive,
            "rebuilt_g0": rebuilt_archive,
        },
    }


def command_verify_g0() -> int:
    if VERIFY_PATH.exists():
        raise FileExistsError(VERIFY_PATH)
    identity = audit_execution_identity()
    static_levels = [_verify_static_g0_level(level) for level in (4, 6, 9)]
    cpu = _g0_replay("cpu")
    cuda = _g0_replay("cuda")
    gates = {
        "s0_identity": identity["gate_pass"],
        "static_4_6_9": all(item["gate_pass"] for item in static_levels),
        "cpu_eight_step_identity": cpu["gate_pass"],
        "cuda_eight_step_identity": cuda["gate_pass"],
    }
    report = {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "stage": "S1",
        "status": (
            "D1_G0_SELF_IDENTITY_PASS" if all(gates.values()) else G0_FAIL_CLOSED
        ),
        "gate_pass": all(gates.values()),
        "gates": gates,
        "identity": identity,
        "static_levels": static_levels,
        "short_replay": {"cpu": cpu, "cuda": cuda},
    }
    _write_json(VERIFY_PATH, report)
    print(
        json.dumps(
            {"status": report["status"], "path": str(VERIFY_PATH)}, ensure_ascii=False
        )
    )
    return 0 if report["gate_pass"] else 2


def _required_passing_report(
    path: Path, expected_status: str | None = None
) -> dict[str, Any]:
    if not path.is_file():
        raise ShadowExecutionError(f"required prior-stage report is missing: {path}")
    report = _json_object(path)
    if report.get("gate_pass") is not True:
        raise ShadowExecutionError(f"required prior-stage report did not pass: {path}")
    if expected_status is not None and report.get("status") != expected_status:
        raise ShadowExecutionError(
            f"required prior-stage status mismatch: {report.get('status')}"
        )
    return report


def command_smoke() -> int:
    if SMOKE_PATH.exists():
        raise FileExistsError(SMOKE_PATH)
    identity = audit_execution_identity()
    verify = _required_passing_report(VERIFY_PATH, "D1_G0_SELF_IDENTITY_PASS")
    config = _effective_config(4, device="cuda")
    template = HighFidelityBackend().make_grid(config)
    g1_binding = build_bound_nominal_g1_mask(template)
    g0 = _prepare_variant(config, label="g0", candidate_mask=None, rebuild=True)
    g1 = _prepare_variant(
        config, label="g1", candidate_mask=g1_binding.mask, rebuild=True
    )
    outcome = run_lockstep_pair((g0, g1), config, target_time_s=None, target_steps=8)
    pair = _pair_summary(outcome, config)
    raw_dir = RAW_ROOT / "g0_g1_smoke"
    archives = {
        "g0": _save_npz(raw_dir / "g0.npz", _runtime_arrays(g0)),
        "g1": _save_npz(raw_dir / "g1.npz", _runtime_arrays(g1)),
    }
    gates = {
        "s0_identity": identity["gate_pass"],
        "g0_self_identity_prior_stage": verify["gate_pass"],
        "g1_mask_hash": (
            pair["variants"]["g1"]["geometry_installation"]["mask_sha256"]
            == g1_binding.expected_mask_sha256
        ),
        "g1_mask_cell_count": (
            pair["variants"]["g1"]["geometry_installation"]["bar_cell_count"]
            == g1_binding.expected_bar_cell_count
        ),
        "lockstep_cuda_eight_step_pair": pair["gate_pass"],
    }
    report = {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "stage": "S2",
        "status": "D1_G0_G1_CUDA_SMOKE_PASS" if all(gates.values()) else FAIL_CLOSED,
        "gate_pass": all(gates.values()),
        "gates": gates,
        "identity": identity,
        "g1_static_binding": {
            "cells_per_bar_diameter": g1_binding.cells_per_bar_diameter,
            "mask_sha256": g1_binding.expected_mask_sha256,
            "bar_cell_count": g1_binding.expected_bar_cell_count,
        },
        "pair": pair,
        "archives": archives,
    }
    _write_json(SMOKE_PATH, report)
    print(
        json.dumps(
            {"status": report["status"], "path": str(SMOKE_PATH)}, ensure_ascii=False
        )
    )
    return 0 if report["gate_pass"] else 2


def _archive_payloads(path: Path) -> dict[str, dict[str, Any]]:
    return {
        child.name: {"sha256": _sha256(child), "size_bytes": child.stat().st_size}
        for child in sorted(path.iterdir())
        if child.is_file()
    }


def _assemble_and_archive(
    runtime: VariantRuntime,
    config: SimulationConfig,
    target: Path,
    *,
    source_start: Mapping[str, object],
    source_end: Mapping[str, object],
    revision_start: Mapping[str, object],
    revision_end: Mapping[str, object],
    pair_process_cpu_time_s: float,
) -> dict[str, Any]:
    tolerance = 128.0 * np.finfo(float).eps * max(config.run.duration_s, 1.0)
    result = assemble_result(
        config,
        runtime.state,
        runtime.grid,
        runtime.recorder,
        runtime.diagnostics,
        runtime.events,
        runtime.projection,
        runtime.pressure_accelerator,
        RunStatus.COMPLETED,
        runtime.expected_volume_m3,
        tolerance,
        backend_id=f"d1_dynamic_shadow_{runtime.label}",
        model_name=MODEL_NAME,
        capabilities=CAPABILITIES,
        limitations=LIMITATIONS,
        numerical_thread_policy=NUMERICAL_THREAD_POLICY.to_dict(),
    )
    save_run_archive(
        target,
        config,
        result,
        extra_provenance={
            "candidate_protocol_id": PROTOCOL_ID,
            "geometry_variant": runtime.label,
            "bar_mask_sha256": array_sha256(runtime.grid.bar_mask.astype(np.uint8)),
            "bar_cell_count": int(np.sum(runtime.grid.bar_mask)),
            "pair_process_cpu_time_scope": (
                "process-wide interval for both lockstep variants"
            ),
        },
        process_cpu_seconds=pair_process_cpu_time_s,
        invocation_entry_point="run_d1_dynamic_shadow.py run",
        invocation_argument_summary={
            "protocol_id": PROTOCOL_ID,
            "geometry_variant": runtime.label,
            "config_canonical_sha256": _canonical_sha256(config.to_dict()),
        },
        source_tree_execution_start=source_start,
        source_tree_execution_end=source_end,
        git_revision_execution_start=revision_start,
        git_revision_execution_end=revision_end,
    )
    loaded = load_run_archive(target)
    if loaded.result.status is not RunStatus.COMPLETED:
        raise ShadowExecutionError(
            f"saved archive did not reload as completed: {target}"
        )
    return {
        "path": _relative(target),
        "payloads": _archive_payloads(target),
        "result_status": loaded.result.status.value,
        "frame_time_sha256": array_sha256(loaded.result.time_s),
    }


def _level_summary_path(level: int) -> Path:
    return RAW_ROOT / f"level_{level}" / "pair_summary.json"


def command_run_level(level: int) -> int:
    if level not in (6, 9):
        raise ShadowExecutionError("D1 full-window level must be 6 or 9")
    summary_path = _level_summary_path(level)
    if summary_path.exists():
        raise FileExistsError(summary_path)
    identity = audit_execution_identity()
    _required_passing_report(VERIFY_PATH, "D1_G0_SELF_IDENTITY_PASS")
    _required_passing_report(SMOKE_PATH, "D1_G0_G1_CUDA_SMOKE_PASS")
    prior: dict[str, Any] | None = None
    if level == 9:
        prior = _required_passing_report(
            _level_summary_path(6), "D1_LOCKSTEP_PAIR_PASS"
        )
    config_identity = _full_window_config_identity(level)
    if not config_identity["gate_pass"]:
        raise ShadowExecutionError("full-window config identity gate failed")
    config = _effective_config(level, duration_s=0.010, device="cuda")
    template = HighFidelityBackend().make_grid(config)
    g1_binding: BoundG1Mask = build_bound_nominal_g1_mask(template)
    g0 = _prepare_variant(config, label="g0", candidate_mask=None, rebuild=True)
    g1 = _prepare_variant(
        config, label="g1", candidate_mask=g1_binding.mask, rebuild=True
    )
    source_start = _capture_source(f"D1 level {level} lockstep start")
    revision_start = _capture_revision(f"D1 level {level} lockstep start")
    outcome = run_lockstep_pair(
        (g0, g1), config, target_time_s=0.010, target_steps=None
    )
    source_end = _capture_source(f"D1 level {level} lockstep end")
    revision_end = _capture_revision(f"D1 level {level} lockstep end")
    pair = _pair_summary(outcome, config)
    pair["schema_version"] = 1
    pair["stage"] = "S3" if level == 6 else "S4"
    pair["level_cells_per_bar_diameter"] = level
    pair["identity"] = identity
    pair["config_identity"] = config_identity
    pair["source_tree_execution_start"] = source_start
    pair["source_tree_execution_end"] = source_end
    pair["git_revision_execution_start"] = revision_start
    pair["git_revision_execution_end"] = revision_end
    pair["g1_static_binding"] = {
        "mask_sha256": g1_binding.expected_mask_sha256,
        "bar_cell_count": g1_binding.expected_bar_cell_count,
        "cells_per_bar_diameter": g1_binding.cells_per_bar_diameter,
    }
    pair["prior_level_6_gate_pass"] = None if prior is None else prior["gate_pass"]

    raw_dir = summary_path.parent
    if outcome.completed and pair["gate_pass"]:
        archives = {
            runtime.label: _assemble_and_archive(
                runtime,
                config,
                raw_dir / runtime.label,
                source_start=source_start,
                source_end=source_end,
                revision_start=revision_start,
                revision_end=revision_end,
                pair_process_cpu_time_s=outcome.process_cpu_time_s,
            )
            for runtime in outcome.variants
        }
        pair["archives"] = archives
    else:
        pair["status"] = FAIL_CLOSED
        pair["gate_pass"] = False
        pair["partial_archives"] = {
            runtime.label: _save_npz(
                raw_dir / f"{runtime.label}_partial.npz",
                _runtime_arrays(runtime),
            )
            for runtime in outcome.variants
        }
    _write_json(summary_path, pair)
    print(
        json.dumps(
            {"status": pair["status"], "path": str(summary_path)}, ensure_ascii=False
        )
    )
    return 0 if pair["gate_pass"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify-g0", help="execute S0/S1 G0 identity gates")
    subparsers.add_parser("smoke", help="execute S2 four-cell CUDA smoke")
    run_parser = subparsers.add_parser("run", help="execute one full lockstep window")
    run_parser.add_argument("--level", type=int, choices=(6, 9), required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "verify-g0":
        return command_verify_g0()
    if args.command == "smoke":
        return command_smoke()
    if args.command == "run":
        return command_run_level(args.level)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
