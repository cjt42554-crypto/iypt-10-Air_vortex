#!/usr/bin/env python3
"""Execute the preregistered D2 V2 numerical-source attribution stages.

The candidate driver imports the formal HF3D implementation directly.  Raw
archives are written below artifacts/runs, which repository policy ignores.
Tracked compact evidence is published only by the independent analyser.
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
Q1R3_ROOT = HERE.parent / "01_HF3D数值资格"
PREREGISTRATION_PATH = HERE / "d2_source_attribution_preregistration_v2.json"
PLAN_PATH = HERE.parent / "06_D2数值误差来源归因科学裁决与执行规划.md"
RAW_ROOT = HERE / "artifacts" / "runs"
S0_PATH = RAW_ROOT / "s0_identity_implementation.json"
STAGE_LEDGER_PATH = RAW_ROOT / "stage_ledger.json"
FAILURE_PATH = RAW_ROOT / "execution_failure.json"
PRELIMINARY_R_PATH = RAW_ROOT / "preliminary_r.json"
REPORT_JSON = HERE / "reports" / "d2_source_attribution_report.json"
REPORT_MD = HERE / "reports" / "d2_source_attribution_report.md"
TRACKED_MANIFEST = HERE / "artifacts" / "d2_source_attribution_manifest.json"
TRACKED_EVIDENCE = HERE / "artifacts" / "evidence"

if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

from airvortex._hardware import NUMERICAL_THREAD_POLICY
from airvortex.backends.high_fidelity_3d import (
    CAPABILITIES,
    LIMITATIONS,
    MODEL_NAME,
    HighFidelityBackend,
)
from airvortex.core import Event, RunStatus, SimulationConfig
from airvortex.hf3d.drive import drive_rpm_and_slope, rpm_to_omega
from airvortex.hf3d.events import TopologyEventTracker
from airvortex.hf3d.pressure import PressureProjection3D, make_pressure_projection
from airvortex.hf3d.recording import (
    FrameRecorder,
    StepDiagnosticsBuffer,
    assemble_result,
)
from airvortex.hf3d.timestep import propose_time_step, retry_time_step_s
from airvortex.hf3d.types import (
    HF3DPostProjectionCFLExceeded,
    HF3DState,
    HF3DStepDiagnostics,
)
from airvortex.io import load_run_archive, save_run_archive
from airvortex.io.archive import git_revision_identity, source_tree_identity
from airvortex.io.config_io import canonical_json_bytes, read_json_strict
from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from airvortex.numerics.momentum3d import rotating_to_laboratory_velocity_3d

PROTOCOL_ID = "D2_HF3D_NUMERICAL_SOURCE_ATTRIBUTION_V2"
DECISION_ID = (
    "D1_DYNAMIC_SHADOW_REVIEW_ACCEPTED__"
    "D2_SOURCE_ATTRIBUTION_V2_AUTHORIZED_NOT_EXECUTED"
)
S0_PASS = "D2_SOURCE_ATTRIBUTION_S0_IDENTITY_IMPLEMENTATION_PASS"
S0_FAIL = "D2_SOURCE_ATTRIBUTION_FAIL_CLOSED_AT_IDENTITY_OR_IMPLEMENTATION"
FAIL_CLOSED = "D2_SOURCE_ATTRIBUTION_INCONCLUSIVE_FAIL_CLOSED"
GROUP_PASS = "D2_TRANSACTIONAL_GROUP_PASS"
EXPECTED_OUTPUT_TIME = np.linspace(0.0, 0.010, 11, dtype=np.float64)
BASELINE_CONFIGS: dict[str, Path] = {
    "F6": Q1R3_ROOT / "generated_q1r3" / "space_L1_6cells.json",
    "F9": Q1R3_ROOT / "generated_q1r3" / "space_L2_9cells.json",
}
SINGLE_CASES = ("F6", "F9", "Z6", "Z9", "T9")
R_PAIR_CASES = ("R9_BASE", "R9_ALL")
R_MULTIWAY_CASES = ("R9_BASE", "R9_ALL", "R9_IF", "R9_BL", "R9_ST")


class D2ExecutionError(RuntimeError):
    """Raised when a frozen D2 execution gate fails."""


@dataclass(slots=True)
class CaseRuntime:
    label: str
    config: SimulationConfig
    config_identity: dict[str, Any]
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
    expected_volume_dense_m3: list[float]
    top_outward_flux_dense_m3_s: list[float]
    last_diagnostics: HF3DStepDiagnostics


@dataclass(slots=True)
class GroupOutcome:
    variants: tuple[CaseRuntime, ...]
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


def _array_sha256(value: NDArray[Any]) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(b"\0")
    digest.update(json.dumps(list(array.shape)).encode("ascii"))
    digest.update(b"\0")
    digest.update(array.view(np.uint8))
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    value = read_json_strict(path)
    if not isinstance(value, dict):
        raise D2ExecutionError(f"JSON root must be an object: {path}")
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
    result = dict(source_tree_identity(FORMAL_ROOT))
    result["sample_point"] = sample_point
    return result


def _capture_revision(sample_point: str) -> dict[str, Any]:
    result = dict(git_revision_identity(REPOSITORY_ROOT))
    result["sample_point"] = sample_point
    return result


def _deep_get(root: Mapping[str, Any], dotted: str) -> Any:
    value: Any = root
    for token in dotted.split("."):
        if not isinstance(value, Mapping) or token not in value:
            raise D2ExecutionError(f"configuration path is missing: {dotted}")
        value = value[token]
    return value


def _deep_set(root: dict[str, Any], dotted: str, value: Any) -> None:
    tokens = dotted.split(".")
    cursor: dict[str, Any] = root
    for token in tokens[:-1]:
        child = cursor.get(token)
        if not isinstance(child, dict):
            raise D2ExecutionError(f"configuration path is not an object: {dotted}")
        cursor = child
    if tokens[-1] not in cursor:
        raise D2ExecutionError(f"configuration path is missing: {dotted}")
    cursor[tokens[-1]] = value


def _leaf_differences(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    prefix: str = "",
) -> dict[str, dict[str, Any]]:
    differences: dict[str, dict[str, Any]] = {}
    keys = set(before) | set(after)
    for key in sorted(keys):
        path = f"{prefix}.{key}" if prefix else key
        if key not in before or key not in after:
            differences[path] = {
                "baseline": before.get(key),
                "effective": after.get(key),
            }
            continue
        left, right = before[key], after[key]
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            differences.update(_leaf_differences(left, right, path))
        elif left != right:
            differences[path] = {"baseline": left, "effective": right}
    return differences


def _case_contract(label: str) -> dict[str, Any]:
    prereg = _json_object(PREREGISTRATION_PATH)
    cases = prereg.get("cases")
    if not isinstance(cases, Mapping) or not isinstance(cases.get(label), Mapping):
        raise D2ExecutionError(f"unknown D2 case: {label}")
    return dict(cases[label])


def effective_case_config(label: str) -> tuple[SimulationConfig, dict[str, Any]]:
    """Build one case and prove its complete diff equals the frozen allowlist."""

    contract = _case_contract(label)
    base_name = contract.get("base_config")
    if base_name not in BASELINE_CONFIGS:
        raise D2ExecutionError(f"invalid base configuration for {label}")
    baseline_path = BASELINE_CONFIGS[str(base_name)]
    baseline = _json_object(baseline_path)
    effective = deepcopy(baseline)
    overrides = contract.get("allowed_overrides")
    if not isinstance(overrides, Mapping):
        raise D2ExecutionError(f"allowed_overrides is missing for {label}")
    for dotted, value in overrides.items():
        if not isinstance(dotted, str):
            raise D2ExecutionError(f"non-string override path for {label}")
        _deep_set(effective, dotted, value)

    differences = _leaf_differences(baseline, effective)
    expected_differences = {
        str(dotted): {
            "baseline": _deep_get(baseline, str(dotted)),
            "effective": value,
        }
        for dotted, value in overrides.items()
        if _deep_get(baseline, str(dotted)) != value
    }
    prereg = _json_object(PREREGISTRATION_PATH)
    common = prereg["common_execution"]
    required_values = {
        "run.duration_s": common["duration_s"],
        "run.output_interval_s": common["output_interval_s"],
        "high_fidelity.pressure_solver_device": common["pressure_solver_device"],
        "high_fidelity.pressure_relative_tolerance": common[
            "pressure_relative_tolerance"
        ],
        "high_fidelity.cfl_controller_target_fraction": common[
            "cfl_controller_target_fraction"
        ],
        "high_fidelity.maximum_cfl_step_retries": common["maximum_cfl_step_retries"],
    }
    common_checks = {
        path: _deep_get(effective, path) == value
        for path, value in required_values.items()
    }
    identity_gates = {
        "changed_paths_equal_allowlist": set(differences) == set(overrides),
        "changed_values_equal_allowlist": differences == expected_differences,
        "common_execution_values_match": all(common_checks.values()),
        "baseline_blob_matches_binding": (
            _blob_sha(baseline_path)
            == prereg["evidence_bindings"]["baseline_configs"][str(base_name)][
                "git_blob_sha"
            ]
        ),
    }
    identity = {
        "case": label,
        "base_config": base_name,
        "baseline_path": _relative(baseline_path),
        "baseline_git_blob_sha": _blob_sha(baseline_path),
        "baseline_content_sha256": _sha256(baseline_path),
        "baseline_canonical_sha256": _canonical_sha256(baseline),
        "effective_canonical_sha256": _canonical_sha256(effective),
        "allowed_overrides": dict(overrides),
        "observed_differences": differences,
        "common_execution_checks": common_checks,
        "gates": identity_gates,
        "gate_pass": all(identity_gates.values()),
    }
    if not identity["gate_pass"]:
        raise D2ExecutionError(f"configuration identity failed for {label}")
    return SimulationConfig.from_dict(effective), identity


def regularisation_scale_audit() -> dict[str, Any]:
    """Audit operator-specific physical widths on the actual formal grids."""

    config6, _ = effective_case_config("F6")
    config9, _ = effective_case_config("R9_ALL")
    backend = HighFidelityBackend()
    grid6 = backend.make_grid(config6)
    grid9 = backend.make_grid(config9)
    contract = _json_object(PREREGISTRATION_PATH)["source_scale_audit"]
    observed_grids = {
        "F6": {
            "dx_m": grid6.dx,
            "dy_m": grid6.dy,
            "dz_m": grid6.dz,
            "minimum_spacing_m": grid6.minimum_spacing_m,
            "maximum_spacing_m": grid6.maximum_spacing_m,
        },
        "F9": {
            "dx_m": grid9.dx,
            "dy_m": grid9.dy,
            "dz_m": grid9.dz,
            "minimum_spacing_m": grid9.minimum_spacing_m,
            "maximum_spacing_m": grid9.maximum_spacing_m,
        },
    }
    grid_checks = {
        level: all(
            math.isclose(
                float(observed_grids[level][name]),
                float(expected),
                rel_tol=0.0,
                abs_tol=2.0e-15,
            )
            for name, expected in contract["grids"][level].items()
        )
        for level in ("F6", "F9")
    }
    hf6, hf9 = config6.high_fidelity, config9.high_fidelity
    widths = {
        "interface_thickness": {
            "F6_m": hf6.interface_thickness_cells * grid6.minimum_spacing_m,
            "F9_m": hf9.interface_thickness_cells * grid9.minimum_spacing_m,
            "spacing_identity": "minimum_spacing_m",
        },
        "boundary_layer": {
            "F6_m": hf6.boundary_layer_cells * grid6.maximum_spacing_m,
            "F9_m": hf9.boundary_layer_cells * grid9.maximum_spacing_m,
            "spacing_identity": "maximum_spacing_m",
        },
        "surface_tension_smoothing": {
            "F6_m": (hf6.surface_tension_smoothing_cells * grid6.maximum_spacing_m),
            "F9_m": (hf9.surface_tension_smoothing_cells * grid9.maximum_spacing_m),
            "spacing_identity": "maximum_spacing_m",
        },
    }
    width_checks = {
        name: math.isclose(
            float(item["F6_m"]),
            float(item["F9_m"]),
            rel_tol=0.0,
            abs_tol=2.0e-15,
        )
        for name, item in widths.items()
    }
    targets = contract["operators"]
    target_checks = {
        "interface_target": math.isclose(
            hf9.interface_thickness_cells,
            float(
                targets["interface_thickness"]["F9_cells_for_equal_F6_physical_width"]
            ),
            rel_tol=0.0,
            abs_tol=1.0e-15,
        ),
        "boundary_target": math.isclose(
            hf9.boundary_layer_cells,
            float(targets["boundary_layer"]["F9_cells_for_equal_F6_physical_width"]),
            rel_tol=0.0,
            abs_tol=1.0e-15,
        ),
        "surface_target": math.isclose(
            hf9.surface_tension_smoothing_cells,
            float(
                targets["surface_tension_smoothing"][
                    "F9_cells_for_equal_F6_physical_width"
                ]
            ),
            rel_tol=0.0,
            abs_tol=1.0e-15,
        ),
    }
    source_evidence = {
        "interface": {
            "path": "02_模拟程序/src/airvortex/hf3d/stepper.py",
            "expression": "interface_thickness_cells * grid.minimum_spacing_m",
        },
        "boundary": {
            "path": "02_模拟程序/src/airvortex/numerics/cartesian3d.py",
            "expression": "layer_cells * grid.maximum_spacing_m",
        },
        "surface": {
            "path": "02_模拟程序/src/airvortex/numerics/interface3d.py",
            "expression": "smoothing_cells * grid.maximum_spacing_m",
        },
        "contact_angle_band": {
            "path": "02_模拟程序/src/airvortex/numerics/interface3d.py",
            "expression": "1.5 * grid.maximum_spacing_m",
            "status": "UNRESOLVED_GRID_SCALED_WETTING_CONFOUND",
            "covered_by_R": False,
        },
    }
    gates = {
        "formal_grid_spacings_match_contract": all(grid_checks.values()),
        "operator_specific_physical_widths_equal": all(width_checks.values()),
        "registered_R9_targets_match": all(target_checks.values()),
        "contact_angle_band_preserved_as_unresolved": True,
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "grid_checks": grid_checks,
        "target_checks": target_checks,
        "observed_grids": observed_grids,
        "physical_widths": widths,
        "source_evidence": source_evidence,
    }


def audit_execution_identity(*, require_no_prior_outputs: bool) -> dict[str, Any]:
    """Bind the authorisation, source, implementation, and execution commit."""

    prereg = _json_object(PREREGISTRATION_PATH)
    bindings = prereg.get("evidence_bindings")
    if not isinstance(bindings, Mapping):
        raise D2ExecutionError("evidence bindings are missing")
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    source = _capture_source("D2 execution identity boundary")
    gates: dict[str, bool] = {
        "main_branch": branch == "main",
        "clean_worktree": not porcelain,
        "protocol_id": prereg.get("protocol_id") == PROTOCOL_ID,
        "decision_id": prereg.get("decision_id") == DECISION_ID,
        "candidate_execution_authorised": (
            prereg.get("decision", {}).get("candidate_dynamic_execution_allowed")
            is True
        ),
        "formal_source_tree_matches": (
            source.get("available") is True
            and source.get("value") == bindings.get("formal_source_tree_sha256")
        ),
        "authorisation_plan_content": _sha256(PLAN_PATH)
        == bindings["authorisation_plan"]["content_sha256"],
        "authorisation_plan_blob": _blob_sha(PLAN_PATH)
        == bindings["authorisation_plan"]["git_blob_sha"],
        "contract_is_committed": bool(_blob_sha(PREREGISTRATION_PATH)),
    }
    bound_files: dict[str, Any] = {}
    for name in ("d1_report_json", "q1r3_spatial_decision"):
        binding = bindings[name]
        path = REPOSITORY_ROOT / str(binding["path"])
        observed_blob = _blob_sha(path)
        gates[f"{name}_blob"] = observed_blob == binding["git_blob_sha"]
        record = _json_object(path)
        if name == "d1_report_json":
            gates["d1_materiality_label"] = (
                record.get("materiality_label") == binding["materiality_label_required"]
            )
        else:
            gates["q1r3_failure_status"] = (
                record.get("status") == binding["status_required"]
            )
        bound_files[name] = {
            "path": _relative(path),
            "observed_git_blob_sha": observed_blob,
            "expected_git_blob_sha": binding["git_blob_sha"],
            "content_sha256": _sha256(path),
        }
    for name, binding in bindings["baseline_configs"].items():
        path = REPOSITORY_ROOT / str(binding["path"])
        observed_blob = _blob_sha(path)
        gates[f"baseline_{name}_blob"] = observed_blob == binding["git_blob_sha"]
        bound_files[f"baseline_{name}"] = {
            "path": _relative(path),
            "observed_git_blob_sha": observed_blob,
            "expected_git_blob_sha": binding["git_blob_sha"],
            "content_sha256": _sha256(path),
        }
    implementation_paths = [
        HERE / "README.md",
        HERE / "run_d2_source_attribution.py",
        HERE / "analyse_d2_source_attribution.py",
        HERE / "tests" / "test_d2_source_attribution.py",
        PREREGISTRATION_PATH,
    ]
    implementation = {}
    for path in implementation_paths:
        try:
            blob = _blob_sha(path)
        except subprocess.CalledProcessError:
            blob = ""
        gates[f"implementation_committed_{path.name}"] = bool(blob)
        implementation[path.name] = {
            "path": _relative(path),
            "git_blob_sha": blob,
            "content_sha256": _sha256(path) if path.is_file() else None,
        }
    prior_paths = [
        RAW_ROOT,
        REPORT_JSON,
        REPORT_MD,
        TRACKED_MANIFEST,
        TRACKED_EVIDENCE,
    ]
    no_prior = not any(path.exists() for path in prior_paths)
    if require_no_prior_outputs:
        gates["no_prior_d2_dynamic_or_postexecution_outputs"] = no_prior
    else:
        gates["s0_prior_gate_available"] = (
            S0_PATH.is_file() and _json_object(S0_PATH).get("gate_pass") is True
        )
        gates["no_prior_failure_marker"] = not FAILURE_PATH.exists()
    result = {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "branch": branch,
        "execution_commit": head,
        "worktree_porcelain": porcelain,
        "formal_source_tree": source,
        "contract": {
            "path": _relative(PREREGISTRATION_PATH),
            "git_blob_sha": _blob_sha(PREREGISTRATION_PATH),
            "content_sha256": _sha256(PREREGISTRATION_PATH),
        },
        "bound_files": bound_files,
        "implementation": implementation,
        "no_prior_output_observation": no_prior,
    }
    if not result["gate_pass"]:
        failed = [name for name, passed in gates.items() if not passed]
        raise D2ExecutionError(
            "D2 execution identity failed: " + json.dumps(failed, ensure_ascii=False)
        )
    return result


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


def _laboratory_speed_metrics(runtime: CaseRuntime) -> tuple[float, float]:
    ux, uy, uz = runtime.grid.faces_to_cells(
        runtime.state.u_x_face,
        runtime.state.u_y_face,
        runtime.state.u_z_face,
    )
    rpm, _ = drive_rpm_and_slope(runtime.config, runtime.state.time_s)
    lab = rotating_to_laboratory_velocity_3d(
        ux,
        uy,
        uz,
        runtime.grid,
        rpm_to_omega(rpm),
    )
    speed_squared = lab[0] * lab[0] + lab[1] * lab[1] + lab[2] * lab[2]
    active = speed_squared[runtime.grid.active_mask]
    return float(np.sqrt(np.max(active))), float(np.sqrt(np.mean(active)))


def _record_frame(runtime: CaseRuntime) -> None:
    runtime.recorder.record_frame(runtime.last_diagnostics)
    maximum_speed, rms_speed = _laboratory_speed_metrics(runtime)
    initial = runtime.initial_volume_m3
    diagnostics = runtime.last_diagnostics
    runtime.recorder.append_observable(
        "expected_liquid_volume_m3",
        diagnostics.expected_liquid_volume_m3,
    )
    runtime.recorder.append_observable(
        "expected_liquid_volume_change_relative",
        (diagnostics.expected_liquid_volume_m3 - initial) / initial,
    )
    runtime.recorder.append_observable(
        "phase_top_outward_flux_m3_s",
        diagnostics.phase_top_outward_flux_m3_s,
    )
    runtime.recorder.append_observable("maximum_laboratory_speed_m_s", maximum_speed)
    runtime.recorder.append_observable("rms_laboratory_speed_m_s", rms_speed)
    runtime.recorder.append_observable(
        "maximum_surface_tension_acceleration_m_s2",
        diagnostics.maximum_surface_tension_acceleration_m_s2,
    )
    runtime.recorder.append_observable(
        "maximum_boundary_acceleration_m_s2",
        diagnostics.maximum_boundary_acceleration_m_s2,
    )


def _prepare_runtime(
    label: str,
    *,
    runtime_label: str | None = None,
) -> CaseRuntime:
    config, identity = effective_case_config(label)
    backend = HighFidelityBackend()
    grid = backend.make_grid(config)
    projection, accelerator = make_pressure_projection(config, grid)
    state = backend.initialise_state(config, grid)
    initial = backend._initial_liquid_volume(config, grid)
    recorder = FrameRecorder(config, grid, state, initial)
    diagnostics = StepDiagnosticsBuffer(initial)
    events: list[Event] = []
    tracker = TopologyEventTracker(config, events)
    zero = backend._zero_step_diagnostics(state, config, grid, initial)
    runtime = CaseRuntime(
        label=label if runtime_label is None else runtime_label,
        config=config,
        config_identity=identity,
        grid=grid,
        state=state,
        projection=projection,
        pressure_accelerator=dict(accelerator),
        initial_volume_m3=initial,
        expected_volume_m3=initial,
        recorder=recorder,
        diagnostics=diagnostics,
        events=events,
        event_tracker=tracker,
        accepted_times_s=[],
        liquid_balance_relative=[],
        expected_volume_dense_m3=[],
        top_outward_flux_dense_m3_s=[],
        last_diagnostics=zero,
    )
    _record_frame(runtime)
    tracker.initialise(zero.topology)
    return runtime


def select_group_limiter(
    proposals: Sequence[Any],
    remaining_s: float,
) -> str:
    """Return output alignment or the strictest variant limiter."""

    if not proposals:
        raise ValueError("at least one proposal is required")
    index = min(range(len(proposals)), key=lambda item: proposals[item].stable_dt_s)
    if remaining_s < proposals[index].stable_dt_s:
        return "output_alignment"
    return str(proposals[index].selected_limiter)


def accepted_times_bitwise_equal(variants: Sequence[CaseRuntime]) -> bool:
    if not variants:
        return False
    reference = np.asarray(variants[0].accepted_times_s, dtype=np.float64).tobytes()
    return all(
        np.asarray(runtime.accepted_times_s, dtype=np.float64).tobytes() == reference
        for runtime in variants[1:]
    )


def _step_hard_gate(
    runtime: CaseRuntime,
    diagnostics: HF3DStepDiagnostics,
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
    if not (
        runtime.pressure_accelerator.get("selected") == "cuda"
        and runtime.pressure_accelerator.get("fallback") is False
    ):
        failed.append("cuda_selected_without_fallback")
    return not failed, failed


def run_transactional_group(
    variants: Sequence[CaseRuntime],
    *,
    target_time_s: float | None,
    target_steps: int | None,
) -> GroupOutcome:
    """Advance one or more independently configured cases transactionally."""

    group = tuple(variants)
    if not group:
        raise ValueError("transactional group cannot be empty")
    if (target_time_s is None) == (target_steps is None):
        raise ValueError("exactly one target is required")
    durations = {runtime.config.run.duration_s for runtime in group}
    intervals = {runtime.config.run.output_interval_s for runtime in group}
    if len(durations) != 1 or len(intervals) != 1:
        raise D2ExecutionError("group duration/output interval mismatch")
    end_time = float(next(iter(durations))) if target_time_s is None else target_time_s
    output_interval = float(next(iter(intervals)))
    next_output = min(output_interval, end_time)
    tolerance = 128.0 * np.finfo(float).eps * max(end_time, 1.0)
    backend = HighFidelityBackend()
    start_wall = time.monotonic()
    start_cpu = time.process_time()
    total_retries = 0
    failure: dict[str, Any] | None = None

    try:
        while True:
            if target_steps is not None and group[0].state.step >= target_steps:
                break
            if (
                target_time_s is not None
                and group[0].state.time_s >= end_time - tolerance
            ):
                break
            state_times = [runtime.state.time_s for runtime in group]
            state_steps = [runtime.state.step for runtime in group]
            if len(set(state_times)) != 1 or len(set(state_steps)) != 1:
                raise D2ExecutionError("group states diverged before a trial")

            proposals = [
                propose_time_step(runtime.state, runtime.config, runtime.grid)
                for runtime in group
            ]
            target = (
                min(next_output, end_time) if target_time_s is not None else math.inf
            )
            remaining = (
                target - group[0].state.time_s if math.isfinite(target) else math.inf
            )
            dt_s = min(
                *(proposal.stable_dt_s for proposal in proposals),
                remaining,
            )
            if not math.isfinite(dt_s) or dt_s <= 0.0:
                raise FloatingPointError("shared trial time step became non-positive")
            limiter = select_group_limiter(proposals, remaining)
            initial_attempt = dt_s
            retry_count = 0
            maximum_rejected = [0.0 for _ in group]

            while True:
                checkpoints = [
                    runtime.projection.checkpoint_history() for runtime in group
                ]
                trial_states = [_clone_state(runtime.state) for runtime in group]
                trial_diagnostics: list[HF3DStepDiagnostics | None] = [
                    None for _ in group
                ]
                rejections: list[HF3DPostProjectionCFLExceeded | None] = [
                    None for _ in group
                ]
                unexpected: Exception | None = None
                for index, runtime in enumerate(group):
                    try:
                        trial_diagnostics[index] = backend.advance_one_step(
                            trial_states[index],
                            runtime.config,
                            runtime.grid,
                            runtime.projection,
                            dt_s,
                            expected_liquid_volume_m3=runtime.expected_volume_m3,
                            time_step_limiter=limiter,
                            cfl_retry_count=retry_count,
                            initial_attempt_dt_s=initial_attempt,
                            maximum_rejected_post_projection_cfl=maximum_rejected[
                                index
                            ],
                        )
                    except HF3DPostProjectionCFLExceeded as exc:
                        rejections[index] = exc
                    except Exception as exc:  # noqa: BLE001 - preserve partial evidence
                        unexpected = exc
                        break
                if unexpected is not None:
                    for runtime, checkpoint in zip(group, checkpoints):
                        runtime.projection.restore_history(checkpoint)
                    raise unexpected
                if any(item is not None for item in rejections):
                    for runtime, checkpoint in zip(group, checkpoints):
                        runtime.projection.restore_history(checkpoint)
                    maximum_retry_count = min(
                        runtime.config.high_fidelity.maximum_cfl_step_retries
                        for runtime in group
                    )
                    if retry_count >= maximum_retry_count:
                        raise FloatingPointError(
                            "shared CFL retry gate exhausted at accepted step "
                            f"{group[0].state.step + 1}"
                        )
                    next_steps = [dt_s]
                    for index, rejection in enumerate(rejections):
                        if rejection is None:
                            continue
                        observed = rejection.diagnostics.post_projection_advective_cfl
                        maximum_rejected[index] = max(maximum_rejected[index], observed)
                        hf = group[index].config.high_fidelity
                        next_steps.append(
                            retry_time_step_s(
                                dt_s,
                                observed,
                                hf.cfl_limit,
                                hf.cfl_controller_target_fraction,
                            )
                        )
                    dt_s = min(next_steps)
                    retry_count += 1
                    total_retries += 1
                    limiter = "post_projection_cfl_retry"
                    for runtime in group:
                        runtime.events.append(
                            Event(
                                runtime.state.time_s,
                                "d2_group_cfl_step_retry",
                                "All D2 trial states were discarded before a stricter shared retry.",
                                {
                                    "retry_number": retry_count,
                                    "next_dt_s": dt_s,
                                    "group_size": len(group),
                                },
                            )
                        )
                    continue
                if any(item is None for item in trial_diagnostics):
                    raise D2ExecutionError("group trial diagnostics are incomplete")

                for index, runtime in enumerate(group):
                    runtime.state = trial_states[index]
                    runtime.recorder.state = runtime.state
                    diagnostics = trial_diagnostics[index]
                    assert diagnostics is not None
                    runtime.last_diagnostics = diagnostics
                    runtime.expected_volume_m3 = diagnostics.expected_liquid_volume_m3
                    runtime.event_tracker.update(runtime.state.time_s, diagnostics)
                    runtime.diagnostics.append(
                        runtime.state.time_s,
                        diagnostics,
                        runtime.config,
                    )
                    runtime.accepted_times_s.append(runtime.state.time_s)
                    runtime.liquid_balance_relative.append(
                        diagnostics.liquid_volume_balance_error_m3
                        / runtime.initial_volume_m3
                    )
                    runtime.expected_volume_dense_m3.append(
                        diagnostics.expected_liquid_volume_m3
                    )
                    runtime.top_outward_flux_dense_m3_s.append(
                        diagnostics.phase_top_outward_flux_m3_s
                    )
                    passed, failed = _step_hard_gate(runtime, diagnostics)
                    if not passed:
                        raise FloatingPointError(
                            f"{runtime.label} hard gate failed: {', '.join(failed)}"
                        )
                if not accepted_times_bitwise_equal(group):
                    raise D2ExecutionError(
                        "accepted time arrays are not bitwise identical"
                    )
                break

            if target_time_s is not None:
                reached_output = group[0].state.time_s >= next_output - tolerance
                reached_end = group[0].state.time_s >= end_time - tolerance
                if reached_output or reached_end:
                    snap = end_time if reached_end else next_output
                    for runtime in group:
                        if abs(runtime.state.time_s - snap) <= tolerance:
                            runtime.state.time_s = snap
                        _record_frame(runtime)
                    while next_output <= group[0].state.time_s + tolerance:
                        next_output += output_interval
                    print(
                        json.dumps(
                            {
                                "stage": "d2_group_progress",
                                "variants": [runtime.label for runtime in group],
                                "time_s": group[0].state.time_s,
                                "accepted_steps": group[0].state.step,
                                "retry_attempts": total_retries,
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
    except Exception as exc:  # noqa: BLE001 - archive numerical failure
        failure = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "accepted_steps": [runtime.state.step for runtime in group],
            "accepted_times_bitwise_equal": accepted_times_bitwise_equal(group),
        }

    completed = failure is None
    if target_steps is not None:
        completed = completed and all(
            runtime.state.step == target_steps for runtime in group
        )
    if target_time_s is not None:
        completed = completed and all(
            abs(runtime.state.time_s - target_time_s) <= tolerance for runtime in group
        )
    if target_steps is not None and completed:
        for runtime in group:
            if (
                not runtime.recorder.times
                or runtime.recorder.times[-1] != runtime.state.time_s
            ):
                _record_frame(runtime)
    return GroupOutcome(
        variants=group,
        completed=completed,
        target_time_s=target_time_s,
        target_steps=target_steps,
        failure=failure,
        retry_attempts=total_retries,
        wall_time_s=time.monotonic() - start_wall,
        process_cpu_time_s=time.process_time() - start_cpu,
    )


def _runtime_arrays(runtime: CaseRuntime) -> dict[str, NDArray[Any]]:
    arrays = {
        f"state_{name}": value.copy()
        for name, value in _state_arrays(runtime.state).items()
    }
    arrays["accepted_time_s"] = np.asarray(runtime.accepted_times_s, dtype=np.float64)
    arrays["liquid_balance_relative"] = np.asarray(
        runtime.liquid_balance_relative, dtype=np.float64
    )
    arrays["expected_volume_dense_m3"] = np.asarray(
        runtime.expected_volume_dense_m3, dtype=np.float64
    )
    arrays["top_outward_flux_dense_m3_s"] = np.asarray(
        runtime.top_outward_flux_dense_m3_s, dtype=np.float64
    )
    for name, value in vars(runtime.diagnostics).items():
        if isinstance(value, list):
            arrays[f"diagnostic_{name}"] = np.asarray(value, dtype=np.float64)
    for name, value in runtime.recorder.observable_lists.items():
        arrays[f"observable_{name}"] = np.asarray(value, dtype=np.float64)
    arrays["frame_time_s"] = np.asarray(runtime.recorder.times, dtype=np.float64)
    arrays["grid_bar_mask"] = runtime.grid.bar_mask.astype(np.uint8)
    arrays["grid_active_mask"] = runtime.grid.active_mask.astype(np.uint8)
    return arrays


def _arrays_identity(
    left: Mapping[str, NDArray[Any]],
    right: Mapping[str, NDArray[Any]],
) -> dict[str, Any]:
    names = sorted(set(left) | set(right))
    detail: dict[str, Any] = {}
    for name in names:
        if name not in left or name not in right:
            detail[name] = {"equal": False, "present_both": False}
            continue
        a, b = np.asarray(left[name]), np.asarray(right[name])
        equal = (
            a.dtype == b.dtype
            and a.shape == b.shape
            and np.array_equal(a, b, equal_nan=True)
        )
        detail[name] = {
            "equal": bool(equal),
            "present_both": True,
            "dtype_equal": a.dtype == b.dtype,
            "shape_equal": a.shape == b.shape,
            "left_sha256": _array_sha256(a),
            "right_sha256": _array_sha256(b),
        }
    return {
        "pass": set(left) == set(right)
        and all(item["equal"] for item in detail.values()),
        "names_equal": set(left) == set(right),
        "arrays": detail,
    }


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
    runtime: CaseRuntime,
    *,
    require_full_window: bool,
) -> dict[str, Any]:
    config = runtime.config
    dt = np.asarray(runtime.diagnostics.step_dt, dtype=np.float64)
    tolerance = max(1.0e-15, config.high_fidelity.time_step_s * 1.0e-9)
    active_fraction = (
        float(np.mean(np.abs(dt - config.high_fidelity.time_step_s) <= tolerance))
        if dt.size
        else 0.0
    )
    pressure = np.asarray(runtime.diagnostics.step_pressure_residual, dtype=np.float64)
    divergence = np.asarray(runtime.diagnostics.step_divergence, dtype=np.float64)
    cfl = np.asarray(runtime.diagnostics.step_post_projection_cfl, dtype=np.float64)
    balance = np.asarray(runtime.liquid_balance_relative, dtype=np.float64)
    top_flux = np.asarray(runtime.top_outward_flux_dense_m3_s, dtype=np.float64)
    expected = np.asarray(runtime.expected_volume_dense_m3, dtype=np.float64)
    clip = runtime.diagnostics.cumulative_clip / runtime.initial_volume_m3
    repair = runtime.diagnostics.cumulative_repair / runtime.initial_volume_m3
    frame_time = np.asarray(runtime.recorder.times, dtype=np.float64)
    zero_drive = runtime.config.drive.target_rpm == 0.0
    actual_rpm = np.asarray(
        runtime.recorder.observable_lists["actual_bar_rpm"], dtype=np.float64
    )
    bar_angle = np.asarray(
        runtime.recorder.observable_lists["bar_angle_rad"], dtype=np.float64
    )
    gates = {
        "all_state_and_diagnostics_finite": bool(
            _all_finite(runtime.state)
            and all(
                np.isfinite(value).all()
                for value in (
                    dt,
                    pressure,
                    divergence,
                    cfl,
                    balance,
                    top_flux,
                    expected,
                )
            )
        ),
        "configured_time_step_active_fraction": active_fraction >= 0.95,
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
            runtime.pressure_accelerator.get("selected") == "cuda"
            and runtime.pressure_accelerator.get("fallback") is False
        ),
        "complete_to_registered_duration": (
            not require_full_window
            or math.isclose(
                runtime.state.time_s,
                0.010,
                rel_tol=0.0,
                abs_tol=2.0e-15,
            )
        ),
        "output_time_grid_matches_registration": (
            (not require_full_window and frame_time.size == 2)
            or bool(np.array_equal(frame_time, EXPECTED_OUTPUT_TIME))
        ),
        "zero_drive_speed_and_angle_remain_zero": (
            not zero_drive
            or (
                np.array_equal(actual_rpm, np.zeros_like(actual_rpm))
                and np.array_equal(bar_angle, np.zeros_like(bar_angle))
            )
        ),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "metrics": {
            "accepted_steps": runtime.state.step,
            "simulated_time_s": runtime.state.time_s,
            "configured_time_step_active_fraction": active_fraction,
            "maximum_pressure_relative_residual": (
                float(np.max(pressure)) if pressure.size else None
            ),
            "maximum_projected_divergence_s_inv": (
                float(np.max(divergence)) if divergence.size else None
            ),
            "maximum_advective_cfl": (float(np.max(cfl)) if cfl.size else None),
            "cumulative_phase_clip_relative": clip,
            "cumulative_phase_repair_relative": repair,
            "maximum_liquid_volume_balance_error_relative": (
                float(np.max(np.abs(balance))) if balance.size else None
            ),
            "maximum_surface_tension_acceleration_m_s2": (
                float(
                    np.max(
                        np.asarray(
                            runtime.diagnostics.step_surface_tension_acceleration,
                            dtype=np.float64,
                        )
                    )
                )
                if runtime.diagnostics.step_surface_tension_acceleration
                else None
            ),
            "maximum_boundary_acceleration_m_s2": (
                float(
                    np.max(
                        np.asarray(
                            runtime.diagnostics.step_boundary_acceleration,
                            dtype=np.float64,
                        )
                    )
                )
                if runtime.diagnostics.step_boundary_acceleration
                else None
            ),
            "maximum_absolute_top_outward_flux_m3_s": (
                float(np.max(np.abs(top_flux))) if top_flux.size else None
            ),
        },
    }


def _group_summary(
    outcome: GroupOutcome,
    *,
    stage: str,
    identity: Mapping[str, Any],
    source_start: Mapping[str, Any],
    source_end: Mapping[str, Any],
    revision_start: Mapping[str, Any],
    revision_end: Mapping[str, Any],
) -> dict[str, Any]:
    quality = {
        runtime.label: _runtime_quality(
            runtime,
            require_full_window=outcome.target_time_s is not None,
        )
        for runtime in outcome.variants
    }
    accepted_time_evidence = {
        runtime.label: {
            "accepted_time_count": int(
                np.asarray(runtime.accepted_times_s, dtype=np.float64).size
            ),
            "accepted_time_sha256": _array_sha256(
                np.asarray(runtime.accepted_times_s, dtype=np.float64)
            ),
            "accepted_times_s": np.asarray(
                runtime.accepted_times_s,
                dtype=np.float64,
            ).tolist(),
        }
        for runtime in outcome.variants
    }
    first_times = np.asarray(outcome.variants[0].accepted_times_s, dtype=np.float64)
    gates = {
        "group_completed": outcome.completed,
        "all_variants_same_accepted_step_count": len(
            {runtime.state.step for runtime in outcome.variants}
        )
        == 1,
        "accepted_time_arrays_bitwise_equal": accepted_times_bitwise_equal(
            outcome.variants
        ),
        "all_variant_quality_gates": all(
            item["gate_pass"] for item in quality.values()
        ),
        "formal_source_stable_and_bound": (
            source_start.get("value")
            == source_end.get("value")
            == _json_object(PREREGISTRATION_PATH)["evidence_bindings"][
                "formal_source_tree_sha256"
            ]
        ),
        "execution_revision_stable": (
            revision_start.get("value") == revision_end.get("value")
        ),
    }
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "stage": stage,
        "status": GROUP_PASS if all(gates.values()) else FAIL_CLOSED,
        "gate_pass": all(gates.values()),
        "gates": gates,
        "failure": outcome.failure,
        "target_time_s": outcome.target_time_s,
        "target_steps": outcome.target_steps,
        "variants": [runtime.label for runtime in outcome.variants],
        "accepted_time_count": int(first_times.size),
        "accepted_time_sha256": _array_sha256(first_times),
        "accepted_times_s": first_times.tolist(),
        "accepted_time_evidence": accepted_time_evidence,
        "retry_attempts": outcome.retry_attempts,
        "wall_time_s": outcome.wall_time_s,
        "process_cpu_time_s": outcome.process_cpu_time_s,
        "identity": dict(identity),
        "source_tree_execution_start": dict(source_start),
        "source_tree_execution_end": dict(source_end),
        "git_revision_execution_start": dict(revision_start),
        "git_revision_execution_end": dict(revision_end),
        "quality": quality,
        "config_identity": {
            runtime.label: runtime.config_identity for runtime in outcome.variants
        },
        "pressure_accelerator": {
            runtime.label: runtime.pressure_accelerator for runtime in outcome.variants
        },
        "formal_g0_geometry": {
            runtime.label: {
                "bar_mask_sha256": _array_sha256(
                    runtime.grid.bar_mask.astype(np.uint8)
                ),
                "bar_cell_count": int(np.sum(runtime.grid.bar_mask)),
            }
            for runtime in outcome.variants
        },
    }


def _archive_payloads(path: Path) -> dict[str, dict[str, Any]]:
    return {
        child.name: {
            "sha256": _sha256(child),
            "size_bytes": child.stat().st_size,
        }
        for child in sorted(path.iterdir())
        if child.is_file()
    }


def _assemble_and_archive(
    runtime: CaseRuntime,
    target: Path,
    *,
    source_start: Mapping[str, object],
    source_end: Mapping[str, object],
    revision_start: Mapping[str, object],
    revision_end: Mapping[str, object],
    group_process_cpu_time_s: float,
) -> dict[str, Any]:
    tolerance = 128.0 * np.finfo(float).eps * max(runtime.config.run.duration_s, 1.0)
    result = assemble_result(
        runtime.config,
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
        backend_id=f"d2_source_attribution_{runtime.label.lower()}",
        model_name=MODEL_NAME,
        capabilities=CAPABILITIES,
        limitations=LIMITATIONS,
        numerical_thread_policy=NUMERICAL_THREAD_POLICY.to_dict(),
    )
    save_run_archive(
        target,
        runtime.config,
        result,
        extra_provenance={
            "candidate_protocol_id": PROTOCOL_ID,
            "case": runtime.label,
            "formal_g0_geometry": True,
            "group_process_cpu_time_scope": (
                "process-wide interval for all variants in the transactional group"
            ),
            "unresolved_confounder": "HARDCODED_CONTACT_ANGLE_BAND",
        },
        process_cpu_seconds=group_process_cpu_time_s,
        invocation_entry_point="run_d2_source_attribution.py",
        invocation_argument_summary={
            "protocol_id": PROTOCOL_ID,
            "case": runtime.label,
            "config_canonical_sha256": _canonical_sha256(runtime.config.to_dict()),
        },
        source_tree_execution_start=source_start,
        source_tree_execution_end=source_end,
        git_revision_execution_start=revision_start,
        git_revision_execution_end=revision_end,
    )
    loaded = load_run_archive(target)
    if loaded.result.status is not RunStatus.COMPLETED:
        raise D2ExecutionError(f"archive did not reload as completed: {target}")
    if not np.array_equal(loaded.result.time_s, EXPECTED_OUTPUT_TIME):
        raise D2ExecutionError(f"archive output time grid mismatch: {target}")
    return {
        "path": _relative(target),
        "payloads": _archive_payloads(target),
        "result_status": loaded.result.status.value,
        "frame_time_sha256": _array_sha256(loaded.result.time_s),
    }


def _case_summary_path(label: str) -> Path:
    return RAW_ROOT / "cases" / label / "case_summary.json"


def _r_group_summary_path(name: str) -> Path:
    return RAW_ROOT / name / "group_summary.json"


def _require_passing(path: Path, status: str | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise D2ExecutionError(f"required prior report is missing: {path}")
    report = _json_object(path)
    if report.get("gate_pass") is not True:
        raise D2ExecutionError(f"required prior report failed: {path}")
    if status is not None and report.get("status") != status:
        raise D2ExecutionError(f"required prior status mismatch: {path}")
    return report


def _stage_ledger_update(
    stage: str,
    status: str,
    path: Path,
    *,
    disposition: str = "EXECUTED",
) -> None:
    if STAGE_LEDGER_PATH.is_file():
        ledger = _json_object(STAGE_LEDGER_PATH)
    else:
        ledger = {
            "schema_version": 1,
            "protocol_id": PROTOCOL_ID,
            "execution_commit": _git("rev-parse", "HEAD"),
            "entries": [],
        }
    entries = ledger.setdefault("entries", [])
    if not isinstance(entries, list):
        raise D2ExecutionError("stage ledger entries are malformed")
    entries.append(
        {
            "stage": stage,
            "status": status,
            "disposition": disposition,
            "evidence_path": _relative(path),
        }
    )
    _write_json(STAGE_LEDGER_PATH, ledger)


def _run_unit_tests() -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        str(HERE / "tests"),
        "-p",
        "test_d2_source_attribution.py",
        "-v",
    ]
    completed = subprocess.run(
        command,
        cwd=HERE,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": [
            "PYTHON",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_d2_source_attribution.py",
            "-v",
        ],
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "pass": completed.returncode == 0,
    }


def command_verify() -> int:
    if any(
        path.exists()
        for path in (
            RAW_ROOT,
            REPORT_JSON,
            REPORT_MD,
            TRACKED_MANIFEST,
            TRACKED_EVIDENCE,
        )
    ):
        raise FileExistsError("D2 prior output exists before S0")
    identity = audit_execution_identity(require_no_prior_outputs=True)
    config_identities = {
        label: effective_case_config(label)[1]
        for label in (*SINGLE_CASES, *R_MULTIWAY_CASES)
    }
    scale_audit = regularisation_scale_audit()
    unit_tests = _run_unit_tests()
    left = _prepare_runtime("F6", runtime_label="F6_S0_LEFT")
    right = _prepare_runtime("F6", runtime_label="F6_S0_RIGHT")
    source_start = _capture_source("D2 S0 identical replay start")
    revision_start = _capture_revision("D2 S0 identical replay start")
    outcome = run_transactional_group(
        (left, right),
        target_time_s=None,
        target_steps=8,
    )
    source_end = _capture_source("D2 S0 identical replay end")
    revision_end = _capture_revision("D2 S0 identical replay end")
    group = _group_summary(
        outcome,
        stage="S0",
        identity=identity,
        source_start=source_start,
        source_end=source_end,
        revision_start=revision_start,
        revision_end=revision_end,
    )
    arrays_left = _runtime_arrays(left)
    arrays_right = _runtime_arrays(right)
    replay_identity = _arrays_identity(arrays_left, arrays_right)
    raw_dir = RAW_ROOT / "s0_replay"
    archives = {
        "left": _save_npz(raw_dir / "left.npz", arrays_left),
        "right": _save_npz(raw_dir / "right.npz", arrays_right),
    }
    gates = {
        "execution_identity": identity["gate_pass"],
        "all_case_config_identities": all(
            item["gate_pass"] for item in config_identities.values()
        ),
        "operator_specific_scale_audit": scale_audit["gate_pass"],
        "pure_unit_tests": unit_tests["pass"],
        "identical_cuda_eight_step_group": group["gate_pass"],
        "all_comparable_arrays_bitwise_identical": replay_identity["pass"],
        "accepted_steps_exactly_eight": left.state.step == right.state.step == 8,
        "actual_cuda_without_fallback": all(
            runtime.pressure_accelerator.get("selected") == "cuda"
            and runtime.pressure_accelerator.get("fallback") is False
            for runtime in (left, right)
        ),
    }
    report = {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "stage": "S0",
        "status": S0_PASS if all(gates.values()) else S0_FAIL,
        "gate_pass": all(gates.values()),
        "gates": gates,
        "identity": identity,
        "config_identities": config_identities,
        "source_scale_audit": scale_audit,
        "unit_tests": unit_tests,
        "identical_replay_group": group,
        "replay_array_identity": replay_identity,
        "raw_replay_archives": archives,
    }
    _write_json(S0_PATH, report)
    _stage_ledger_update("S0", report["status"], S0_PATH)
    print(
        json.dumps(
            {"status": report["status"], "path": str(S0_PATH)},
            ensure_ascii=False,
        )
    )
    return 0 if report["gate_pass"] else 2


def _required_priors_for_case(label: str) -> None:
    _require_passing(S0_PATH, S0_PASS)
    order = {
        "F6": (),
        "F9": ("F6",),
        "Z6": ("F6", "F9"),
        "Z9": ("F6", "F9", "Z6"),
        "T9": ("F6", "F9", "Z6", "Z9"),
    }
    for prior in order[label]:
        _require_passing(_case_summary_path(prior), GROUP_PASS)


def command_run_case(label: str) -> int:
    if label not in SINGLE_CASES:
        raise D2ExecutionError(f"unsupported singleton D2 case: {label}")
    summary_path = _case_summary_path(label)
    if summary_path.exists():
        raise FileExistsError(summary_path)
    identity = audit_execution_identity(require_no_prior_outputs=False)
    _required_priors_for_case(label)
    runtime = _prepare_runtime(label)
    source_start = _capture_source(f"D2 {label} full-window start")
    revision_start = _capture_revision(f"D2 {label} full-window start")
    outcome = run_transactional_group(
        (runtime,),
        target_time_s=0.010,
        target_steps=None,
    )
    source_end = _capture_source(f"D2 {label} full-window end")
    revision_end = _capture_revision(f"D2 {label} full-window end")
    stage = {
        "F6": "S1",
        "F9": "S1",
        "Z6": "S2",
        "Z9": "S2",
        "T9": "S3",
    }[label]
    summary = _group_summary(
        outcome,
        stage=stage,
        identity=identity,
        source_start=source_start,
        source_end=source_end,
        revision_start=revision_start,
        revision_end=revision_end,
    )
    summary["case"] = label
    if summary["gate_pass"]:
        summary["archives"] = {
            label: _assemble_and_archive(
                runtime,
                summary_path.parent / "archive",
                source_start=source_start,
                source_end=source_end,
                revision_start=revision_start,
                revision_end=revision_end,
                group_process_cpu_time_s=outcome.process_cpu_time_s,
            )
        }
    else:
        summary["partial_archive"] = _save_npz(
            summary_path.parent / "partial.npz",
            _runtime_arrays(runtime),
        )
    _write_json(summary_path, summary)
    _stage_ledger_update(stage, summary["status"], summary_path)
    print(
        json.dumps(
            {"status": summary["status"], "path": str(summary_path)},
            ensure_ascii=False,
        )
    )
    return 0 if summary["gate_pass"] else 2


def command_run_r_pair() -> int:
    summary_path = _r_group_summary_path("r_pair")
    if summary_path.exists():
        raise FileExistsError(summary_path)
    identity = audit_execution_identity(require_no_prior_outputs=False)
    for label in SINGLE_CASES:
        _require_passing(_case_summary_path(label), GROUP_PASS)
    variants = tuple(_prepare_runtime(label) for label in R_PAIR_CASES)
    scale_audit = regularisation_scale_audit()
    if not scale_audit["gate_pass"]:
        raise D2ExecutionError("regularisation scale audit failed")
    source_start = _capture_source("D2 S4 R pair start")
    revision_start = _capture_revision("D2 S4 R pair start")
    outcome = run_transactional_group(
        variants,
        target_time_s=0.010,
        target_steps=None,
    )
    source_end = _capture_source("D2 S4 R pair end")
    revision_end = _capture_revision("D2 S4 R pair end")
    summary = _group_summary(
        outcome,
        stage="S4",
        identity=identity,
        source_start=source_start,
        source_end=source_end,
        revision_start=revision_start,
        revision_end=revision_end,
    )
    summary["source_scale_audit"] = scale_audit
    if summary["gate_pass"]:
        summary["archives"] = {
            runtime.label: _assemble_and_archive(
                runtime,
                summary_path.parent / runtime.label,
                source_start=source_start,
                source_end=source_end,
                revision_start=revision_start,
                revision_end=revision_end,
                group_process_cpu_time_s=outcome.process_cpu_time_s,
            )
            for runtime in variants
        }
    else:
        summary["partial_archives"] = {
            runtime.label: _save_npz(
                summary_path.parent / f"{runtime.label}_partial.npz",
                _runtime_arrays(runtime),
            )
            for runtime in variants
        }
    _write_json(summary_path, summary)
    _stage_ledger_update("S4", summary["status"], summary_path)
    print(
        json.dumps(
            {"status": summary["status"], "path": str(summary_path)},
            ensure_ascii=False,
        )
    )
    return 0 if summary["gate_pass"] else 2


def command_run_r_multiway() -> int:
    summary_path = _r_group_summary_path("r_multiway")
    if summary_path.exists():
        raise FileExistsError(summary_path)
    identity = audit_execution_identity(require_no_prior_outputs=False)
    _require_passing(_r_group_summary_path("r_pair"), GROUP_PASS)
    preliminary = _require_passing(PRELIMINARY_R_PATH)
    magnitude = preliminary.get("R_magnitude_label")
    if magnitude == "SMALL":
        raise D2ExecutionError("S5 is frozen as skipped when R is SMALL")
    if magnitude not in {"MATERIAL", "MIXED_OR_INDETERMINATE"}:
        raise D2ExecutionError("preliminary R magnitude label is invalid")
    variants = tuple(_prepare_runtime(label) for label in R_MULTIWAY_CASES)
    source_start = _capture_source("D2 S5 R multiway start")
    revision_start = _capture_revision("D2 S5 R multiway start")
    outcome = run_transactional_group(
        variants,
        target_time_s=0.010,
        target_steps=None,
    )
    source_end = _capture_source("D2 S5 R multiway end")
    revision_end = _capture_revision("D2 S5 R multiway end")
    summary = _group_summary(
        outcome,
        stage="S5",
        identity=identity,
        source_start=source_start,
        source_end=source_end,
        revision_start=revision_start,
        revision_end=revision_end,
    )
    summary["triggering_R_magnitude_label"] = magnitude
    if summary["gate_pass"]:
        summary["archives"] = {
            runtime.label: _assemble_and_archive(
                runtime,
                summary_path.parent / runtime.label,
                source_start=source_start,
                source_end=source_end,
                revision_start=revision_start,
                revision_end=revision_end,
                group_process_cpu_time_s=outcome.process_cpu_time_s,
            )
            for runtime in variants
        }
    else:
        summary["partial_archives"] = {
            runtime.label: _save_npz(
                summary_path.parent / f"{runtime.label}_partial.npz",
                _runtime_arrays(runtime),
            )
            for runtime in variants
        }
    _write_json(summary_path, summary)
    _stage_ledger_update("S5", summary["status"], summary_path)
    print(
        json.dumps(
            {"status": summary["status"], "path": str(summary_path)},
            ensure_ascii=False,
        )
    )
    return 0 if summary["gate_pass"] else 2


def _write_failure(command: Sequence[str], exc: Exception) -> None:
    if FAILURE_PATH.exists():
        return
    failure = {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "status": FAIL_CLOSED,
        "command": list(command),
        "failure": {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        },
        "execution_commit": _git("rev-parse", "HEAD"),
    }
    _write_json(FAILURE_PATH, failure)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify", help="execute the complete S0 gate")
    case_parser = subparsers.add_parser("run-case", help="execute one singleton case")
    case_parser.add_argument("--case", choices=SINGLE_CASES, required=True)
    subparsers.add_parser("run-r-pair", help="execute the S4 transactional pair")
    subparsers.add_parser(
        "run-r-multiway",
        help="execute the conditional S5 five-variant group",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)
    try:
        if args.command == "verify":
            return command_verify()
        if args.command == "run-case":
            return command_run_case(args.case)
        if args.command == "run-r-pair":
            return command_run_r_pair()
        if args.command == "run-r-multiway":
            return command_run_r_multiway()
        raise AssertionError(args.command)
    except Exception as exc:
        try:
            _write_failure(arguments, exc)
        except Exception as failure_write_error:  # noqa: BLE001
            print(
                "D2 failure evidence could not be written: "
                f"{type(failure_write_error).__name__}: {failure_write_error}",
                file=sys.stderr,
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
