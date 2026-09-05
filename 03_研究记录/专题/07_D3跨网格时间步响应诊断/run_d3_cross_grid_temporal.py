#!/usr/bin/env python3
"""Prepare and, only after a future authorization, execute D3R1 HF3D cases.

The result-blind R1 preregistration repairs only the candidate output-schedule
identity and separates R1 evidence from preserved V1 raw.  It authorizes
configuration generation, tests, and non-stepping audits only.  Dynamic entry
points require a separate R1 Pro authorization bound to one reviewed
implementation snapshot; that object is currently denied.  All numerical
advancement remains delegated directly to the formal HF3D implementation.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import math
import os
import subprocess
import sys
import tempfile
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
V1_PREREGISTRATION_PATH = HERE / "d3_cross_grid_temporal_preregistration.json"
PREREGISTRATION_PATH = HERE / "d3_cross_grid_temporal_preregistration_r1.json"
V1_EXECUTION_AUTHORIZATION_PATH = HERE / "d3_execution_authorization.json"
EXECUTION_AUTHORIZATION_PATH = HERE / "d3_r1_execution_authorization.json"
GENERATED_CONFIG_ROOT = HERE / "generated_configs"
STATIC_REVIEW_PATH = HERE / "artifacts" / "r1" / "d3_implementation_static_review.json"
V1_RAW_ROOT = HERE / "artifacts" / "runs"
RAW_ROOT = HERE / "artifacts" / "r1" / "runs"
S0_PATH = RAW_ROOT / "s0_identity_config_fresh_evidence_gate.json"
STAGE_LEDGER_PATH = RAW_ROOT / "stage_ledger.json"
FAILURE_PATH = RAW_ROOT / "execution_failure.json"
REPORT_JSON = HERE / "reports" / "r1" / "d3_cross_grid_temporal_report.json"
REPORT_MD = HERE / "reports" / "r1" / "d3_cross_grid_temporal_report.md"
FINAL_MANIFEST = HERE / "artifacts" / "r1" / "d3_cross_grid_temporal_manifest.json"
FINAL_EVIDENCE_ROOT = HERE / "artifacts" / "r1" / "evidence"

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
from airvortex.numerics.projection3d_gpu import probe_gpu_projection
from d3_r1_contract import (
    R1_PROTOCOL_ID,
    assert_r1_input_path,
    audit_evidence_root_isolation,
    audit_scientific_identity,
    audit_v1_frozen_evidence,
    registered_schedule_audit,
)
from d3_stage_machine import (
    audit_s0_report,
)
from d3_stage_machine import (
    audit_stage_prerequisites as audit_common_stage_prerequisites,
)

PROTOCOL_ID = R1_PROTOCOL_ID
AUTHORIZATION_ID = "D3R1_PRO_EXECUTION_AUTHORIZATION_V1"
AUTHORIZATION_ALLOWED = "AUTHORIZED_BY_PRO_FOR_BOUND_IMPLEMENTATION"
AUTHORIZATION_DENIED = "NOT_AUTHORIZED"
STATIC_READY = (
    "D3R1_IMPLEMENTATION_READY_FOR_PRO_STATIC_REVIEW__DYNAMIC_EXECUTION_NOT_AUTHORIZED"
)
S0_PASS = "D3_S0_IDENTITY_CONFIG_IMPLEMENTATION_AND_FRESH_EVIDENCE_GATE_PASS"
CASE_PASS = "D3_CASE_COMPLETE_ALL_HARD_GATES_PASS"
FAIL_CLOSED = "D3_INCONCLUSIVE_FAIL_CLOSED"
EXPECTED_CASES = (
    "G6_DT0",
    "G6_DT1",
    "G6_DT2",
    "G9_DT0",
    "G9_DT1",
    "G9_DT2",
)
STAGE_CASES = {
    "S1": EXPECTED_CASES[:3],
    "S2": EXPECTED_CASES[3:],
}
STAGE_ACTIONS = {
    "S0": "IDENTITY_CONFIG_IMPLEMENTATION_AND_FRESH_EVIDENCE_GATE",
    "S1": "FRESH_G6_DT0_DT1_DT2_SEQUENCE",
    "S2": "FRESH_G9_DT0_DT1_DT2_SEQUENCE",
    "S3": "INDEPENDENT_ANALYSIS_TRACKED_EVIDENCE_REPORT_AND_STOP_FOR_PRO_REVIEW",
}
STAGE_DESCRIPTIONS = {
    "S0": "身份、配置、实现与全新证据门；不推进 HF3D 时间步。",
    "S1": "从零依次执行 G6_DT0、G6_DT1、G6_DT2。",
    "S2": "在 S1 全部硬门通过后，从零依次执行 G9_DT0、G9_DT1、G9_DT2。",
    "S3": "独立重载六份归档、发布跟踪证据和报告，然后停止等待 Pro 复核。",
}
STAGE_ORDER = ("S0", "S1", "S2", "S3")
EXPECTED_OUTPUT_TIME = np.asarray(
    [0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.009, 0.01],
    dtype=np.float64,
)


class D3ExecutionError(RuntimeError):
    """Raised when a frozen D3 execution or identity gate fails."""


class D3PreconditionError(D3ExecutionError):
    """Reject a dynamic command before it enters a stage transaction."""


class D3CaseTransactionError(D3ExecutionError):
    """Carry any recoverable case evidence to the unique failure transaction."""

    def __init__(self, message: str, *, evidence: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.evidence = tuple(evidence)


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
    last_diagnostics: HF3DStepDiagnostics


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _git_diff_paths(base: str, tip: str = "HEAD") -> list[str]:
    """Return repository-relative paths without locale-dependent Git quoting."""

    return _git(
        "-c",
        "core.quotePath=false",
        "diff",
        "--name-only",
        base,
        tip,
    ).splitlines()


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
        raise D3ExecutionError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()


def _blob_sha(path: Path, revision: str = "HEAD") -> str:
    return _git("rev-parse", f"{revision}:{_relative(path)}")


def _is_lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _implementation_binding() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in _implementation_paths():
        try:
            blob = _blob_sha(path)
        except subprocess.CalledProcessError:
            blob = ""
        result[_relative(path)] = {
            "git_blob_sha": blob,
            "content_sha256": _sha256(path) if path.is_file() else None,
        }
    return result


def validate_execution_authorization_shape(
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the separate Pro decision object without granting execution."""

    decision = authorization.get("decision")
    binding = authorization.get("review_binding")
    ruling = authorization.get("ruling")
    status = authorization.get("status")
    expected_paths = {_relative(path) for path in _implementation_paths()}
    implementation_files = (
        binding.get("implementation_files") if isinstance(binding, Mapping) else None
    )
    common_gates = {
        "top_level_fields_exact": set(authorization)
        == {
            "schema_version",
            "authorization_id",
            "protocol_id",
            "status",
            "decision",
            "review_binding",
            "ruling",
            "instructions",
        },
        "schema_version": authorization.get("schema_version") == 1,
        "authorization_id": authorization.get("authorization_id") == AUTHORIZATION_ID,
        "protocol_id": authorization.get("protocol_id") == PROTOCOL_ID,
        "known_status": status in {AUTHORIZATION_DENIED, AUTHORIZATION_ALLOWED},
        "decision_object": isinstance(decision, Mapping),
        "binding_object": isinstance(binding, Mapping),
        "ruling_object": isinstance(ruling, Mapping),
        "decision_fields_exact": isinstance(decision, Mapping)
        and set(decision)
        == {
            "real_cuda_dynamic_execution",
            "authorized_stages",
            "automatic_follow_on",
        },
        "binding_fields_exact": isinstance(binding, Mapping)
        and set(binding)
        == {
            "reviewed_implementation_commit",
            "preregistration_git_blob_sha",
            "formal_source_tree_sha256",
            "implementation_files",
        },
        "ruling_fields_exact": isinstance(ruling, Mapping)
        and set(ruling) == {"reviewer", "ruling_document", "issued_at"},
        "instructions_present": isinstance(authorization.get("instructions"), str)
        and bool(authorization.get("instructions")),
        "automatic_follow_on_prohibited": isinstance(decision, Mapping)
        and decision.get("automatic_follow_on") == "PROHIBITED",
    }
    authorized = (
        status == AUTHORIZATION_ALLOWED
        and isinstance(decision, Mapping)
        and decision.get("real_cuda_dynamic_execution") is True
    )
    state_gates: dict[str, bool]
    if status == AUTHORIZATION_DENIED:
        state_gates = {
            "dynamic_execution_false": isinstance(decision, Mapping)
            and decision.get("real_cuda_dynamic_execution") is False,
            "authorized_stages_empty": isinstance(decision, Mapping)
            and decision.get("authorized_stages") == [],
            "reviewed_commit_absent": isinstance(binding, Mapping)
            and binding.get("reviewed_implementation_commit") is None,
            "preregistration_blob_absent": isinstance(binding, Mapping)
            and binding.get("preregistration_git_blob_sha") is None,
            "implementation_binding_empty": implementation_files == {},
            "formal_source_binding_exact": isinstance(binding, Mapping)
            and binding.get("formal_source_tree_sha256")
            == _contract()["formal_source_contract"]["required_tree_sha256"],
            "ruling_not_issued": isinstance(ruling, Mapping)
            and ruling.get("reviewer") == "PRO_REVIEW_REQUIRED"
            and ruling.get("ruling_document") is None
            and ruling.get("issued_at") is None,
        }
    else:
        implementation_entries_valid = (
            isinstance(implementation_files, Mapping)
            and set(implementation_files) == expected_paths
            and all(
                isinstance(item, Mapping)
                and set(item) == {"git_blob_sha", "content_sha256"}
                and _is_lower_hex(item.get("git_blob_sha"), 40)
                and _is_lower_hex(item.get("content_sha256"), 64)
                for item in implementation_files.values()
            )
        )
        state_gates = {
            "dynamic_execution_true": isinstance(decision, Mapping)
            and decision.get("real_cuda_dynamic_execution") is True,
            "authorized_stages_exact": isinstance(decision, Mapping)
            and decision.get("authorized_stages") == list(STAGE_ORDER),
            "reviewed_commit_bound": isinstance(binding, Mapping)
            and _is_lower_hex(binding.get("reviewed_implementation_commit"), 40),
            "preregistration_blob_bound": isinstance(binding, Mapping)
            and _is_lower_hex(binding.get("preregistration_git_blob_sha"), 40),
            "formal_source_binding_exact": isinstance(binding, Mapping)
            and binding.get("formal_source_tree_sha256")
            == _contract()["formal_source_contract"]["required_tree_sha256"],
            "implementation_binding_exact_path_set_and_hash_shape": (
                implementation_entries_valid
            ),
            "pro_ruling_identified": isinstance(ruling, Mapping)
            and ruling.get("reviewer") == "ChatGPT Pro"
            and isinstance(ruling.get("ruling_document"), str)
            and bool(ruling.get("ruling_document"))
            and isinstance(ruling.get("issued_at"), str)
            and bool(ruling.get("issued_at")),
        }
    gates = {**common_gates, **state_gates}
    return {
        "gate_pass": all(gates.values()),
        "authorized": authorized,
        "status": status,
        "gates": gates,
    }


def execution_authorization_audit(*, require_authorized: bool) -> dict[str, Any]:
    """Bind a Pro decision to one authorization-only child commit."""

    regular_file = (
        EXECUTION_AUTHORIZATION_PATH.is_file()
        and not EXECUTION_AUTHORIZATION_PATH.is_symlink()
    )
    authorization = _json_object(EXECUTION_AUTHORIZATION_PATH) if regular_file else {}
    shape = validate_execution_authorization_shape(authorization)
    try:
        authorization_blob = _blob_sha(EXECUTION_AUTHORIZATION_PATH)
    except subprocess.CalledProcessError:
        authorization_blob = ""
    gates: dict[str, bool] = {
        "regular_file_not_symlink": regular_file,
        "authorization_object_committed": bool(authorization_blob),
        "authorization_shape": shape["gate_pass"],
    }
    observed: dict[str, Any] = {
        "authorization_git_blob_sha": authorization_blob,
        "authorization_content_sha256": (
            _sha256(EXECUTION_AUTHORIZATION_PATH) if regular_file else None
        ),
    }
    if require_authorized:
        binding = authorization.get("review_binding")
        reviewed_commit = (
            binding.get("reviewed_implementation_commit")
            if isinstance(binding, Mapping)
            else None
        )
        parent_record = _git("rev-list", "--parents", "-n", "1", "HEAD").split()
        sole_parent = parent_record[1] if len(parent_record) == 2 else None
        changed_paths = (
            _git_diff_paths(str(reviewed_commit))
            if _is_lower_hex(reviewed_commit, 40)
            else []
        )
        implementation = _implementation_binding()
        source = _capture_source("D3 Pro authorization binding audit")
        ruling = authorization.get("ruling")
        ruling_path = (
            REPOSITORY_ROOT / str(ruling.get("ruling_document"))
            if isinstance(ruling, Mapping)
            and isinstance(ruling.get("ruling_document"), str)
            and ruling.get("ruling_document")
            else None
        )
        try:
            preregistration_blob = _blob_sha(PREREGISTRATION_PATH)
        except subprocess.CalledProcessError:
            preregistration_blob = ""
        try:
            ruling_blob = _blob_sha(ruling_path) if ruling_path is not None else ""
        except subprocess.CalledProcessError:
            ruling_blob = ""
        expected_implementation = (
            binding.get("implementation_files")
            if isinstance(binding, Mapping)
            else None
        )
        gates.update(
            {
                "authorized_state": shape["authorized"],
                "authorization_commit_has_one_parent": sole_parent is not None,
                "reviewed_commit_is_sole_parent": reviewed_commit == sole_parent,
                "authorization_file_is_only_change": changed_paths
                == [_relative(EXECUTION_AUTHORIZATION_PATH)],
                "implementation_binding_matches": expected_implementation
                == implementation,
                "preregistration_blob_matches": isinstance(binding, Mapping)
                and binding.get("preregistration_git_blob_sha") == preregistration_blob,
                "formal_source_tree_matches": isinstance(binding, Mapping)
                and source.get("available") is True
                and binding.get("formal_source_tree_sha256") == source.get("value"),
                "ruling_document_is_committed_regular_file": ruling_path is not None
                and ruling_path.is_file()
                and not ruling_path.is_symlink()
                and bool(ruling_blob),
            }
        )
        observed.update(
            {
                "reviewed_implementation_commit": reviewed_commit,
                "sole_parent": sole_parent,
                "changed_paths_from_reviewed_commit": changed_paths,
                "implementation_files": implementation,
                "preregistration_git_blob_sha": preregistration_blob,
                "formal_source_tree": source,
            }
        )
    return {
        "gate_pass": all(gates.values()),
        "authorized": shape["authorized"],
        "gates": gates,
        "shape_audit": shape,
        "observed": observed,
    }


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
            raise D3ExecutionError(f"configuration path is missing: {dotted}")
        value = value[token]
    return value


def _deep_set(root: dict[str, Any], dotted: str, value: Any) -> None:
    tokens = dotted.split(".")
    cursor = root
    for token in tokens[:-1]:
        child = cursor.get(token)
        if not isinstance(child, dict):
            raise D3ExecutionError(f"configuration path is not an object: {dotted}")
        cursor = child
    if tokens[-1] not in cursor:
        raise D3ExecutionError(f"configuration path is missing: {dotted}")
    cursor[tokens[-1]] = value


def _leaf_differences(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    prefix: str = "",
) -> dict[str, dict[str, Any]]:
    differences: dict[str, dict[str, Any]] = {}
    for key in sorted(set(before) | set(after)):
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


def r1_scientific_identity_audit() -> dict[str, Any]:
    """Reload both contracts and prove every frozen scientific field identical."""

    v1_contract = _json_object(V1_PREREGISTRATION_PATH)
    r1_contract = _json_object(PREREGISTRATION_PATH)
    return audit_scientific_identity(v1_contract, r1_contract)


def _contract() -> dict[str, Any]:
    contract = _json_object(PREREGISTRATION_PATH)
    if contract.get("protocol_id") != PROTOCOL_ID:
        raise D3ExecutionError("D3R1 protocol identity mismatch")
    scientific_identity = audit_scientific_identity(
        _json_object(V1_PREREGISTRATION_PATH), contract
    )
    if not scientific_identity["gate_pass"]:
        failed = [
            name for name, passed in scientific_identity["gates"].items() if not passed
        ]
        raise D3ExecutionError(
            "D3R1 scientific identity differs from V1: "
            + json.dumps(failed, ensure_ascii=False)
        )
    return contract


def validate_contract_shape(
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the machine-readable matrix before deriving any case."""

    value = _contract() if contract is None else dict(contract)
    expected_matrix = [
        ("G6_DT0", "G6", "DT0", 5.5e-6),
        ("G6_DT1", "G6", "DT1", 2.75e-6),
        ("G6_DT2", "G6", "DT2", 1.375e-6),
        ("G9_DT0", "G9", "DT0", 5.5e-6),
        ("G9_DT1", "G9", "DT1", 2.75e-6),
        ("G9_DT2", "G9", "DT2", 1.375e-6),
    ]
    observed_matrix: list[tuple[str, str, str, float]] = []
    for item in value.get("case_matrix", []):
        if not isinstance(item, Mapping):
            observed_matrix.append(("", "", "", math.nan))
            continue
        observed_matrix.append(
            (
                str(item.get("case_id")),
                str(item.get("grid")),
                str(item.get("time_step_level")),
                float(item.get("time_step_s", math.nan)),
            )
        )
    scientific_identity = r1_scientific_identity_audit()
    schedule_contract = value.get("output_schedule_contract")
    evidence_roots = value.get("evidence_roots")
    v1_failure_binding = value.get("v1_failure_binding")
    gates = {
        "protocol_id": value.get("protocol_id") == PROTOCOL_ID,
        "schema_version_r1": value.get("schema_version") == 2,
        "scientific_fields_identical_to_v1": scientific_identity["gate_pass"],
        "case_matrix_exact": observed_matrix == expected_matrix,
        "native_output_times_exact": value.get("native_output_times_s")
        == EXPECTED_OUTPUT_TIME.tolist(),
        "allowed_override_paths_exact": set(
            value.get("allowed_case_override_paths", [])
        )
        == {
            "run.duration_s",
            "run.output_interval_s",
            "high_fidelity.time_step_s",
        },
        "formal_g0_only": value.get("formal_source_contract", {}).get(
            "geometry_identity"
        )
        == "FORMAL_G0_ONLY",
        "solver_copy_prohibited": value.get("formal_source_contract", {}).get(
            "solver_copy_or_fork"
        )
        == "PROHIBITED",
        "current_dynamic_execution_false": value.get("authorization", {}).get(
            "real_cuda_dynamic_execution"
        )
        is False,
        "indexed_output_schedule_exact": isinstance(schedule_contract, Mapping)
        and schedule_contract.get("output_schedule_source")
        == "native_output_times_s indexed values"
        and schedule_contract.get("next_target_rule")
        == "next_target = registered_times[next_index]"
        and schedule_contract.get("next_index_initial") == 1
        and schedule_contract.get("repeated_floating_addition") == "PROHIBITED"
        and schedule_contract.get("interpolation") == "PROHIBITED"
        and schedule_contract.get("bitwise_native_output_times_gate") == "UNCHANGED"
        and schedule_contract.get("all_six_runs_fresh_after_R1_review") is True
        and schedule_contract.get("V1_raw_or_partial_reuse") == "PROHIBITED",
        "r1_evidence_paths_exact": isinstance(evidence_roots, Mapping)
        and evidence_roots.get("v1_raw_read_only") == "artifacts/runs/**"
        and evidence_roots.get("r1_raw") == "artifacts/r1/runs/**"
        and evidence_roots.get("r1_evidence") == "artifacts/r1/evidence/**"
        and evidence_roots.get("r1_static_review")
        == "artifacts/r1/d3_implementation_static_review.json"
        and evidence_roots.get("r1_manifest")
        == "artifacts/r1/d3_cross_grid_temporal_manifest.json"
        and evidence_roots.get("r1_report_json")
        == "reports/r1/d3_cross_grid_temporal_report.json"
        and evidence_roots.get("r1_report_markdown")
        == "reports/r1/d3_cross_grid_temporal_report.md"
        and evidence_roots.get("roots_must_be_disjoint") is True,
        "v1_failure_binding_exact": isinstance(v1_failure_binding, Mapping)
        and v1_failure_binding.get("execution_commit")
        == "90ed5c1019d8265f6e9730ff8a2d36be61deec8b"
        and v1_failure_binding.get("v1_failure_upheld") is True
        and v1_failure_binding.get("v1_raw_or_partial_reuse") == "PROHIBITED",
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "observed_matrix": observed_matrix,
        "scientific_identity_audit": scientific_identity,
    }


def _case_record(case_id: str) -> dict[str, Any]:
    records = {
        str(item.get("case_id")): dict(item)
        for item in _contract().get("case_matrix", [])
        if isinstance(item, Mapping)
    }
    if case_id not in records or case_id not in EXPECTED_CASES:
        raise D3ExecutionError(f"unknown D3 case: {case_id}")
    return records[case_id]


def _baseline_path(grid_name: str) -> Path:
    binding = _contract().get("baseline_configs", {}).get(grid_name)
    if not isinstance(binding, Mapping) or not isinstance(binding.get("path"), str):
        raise D3ExecutionError(f"baseline binding is missing for {grid_name}")
    path = REPOSITORY_ROOT / str(binding["path"])
    if not path.is_file():
        raise D3ExecutionError(f"baseline config is missing: {path}")
    return path


def effective_case_config(case_id: str) -> tuple[SimulationConfig, dict[str, Any]]:
    """Generate one effective config and prove its exact frozen allowlist diff."""

    contract = _contract()
    record = _case_record(case_id)
    grid_name = str(record["grid"])
    baseline_path = _baseline_path(grid_name)
    baseline = _json_object(baseline_path)
    effective = deepcopy(baseline)
    overrides = {
        "run.duration_s": contract["frozen_run_fields"]["run.duration_s"],
        "run.output_interval_s": contract["frozen_run_fields"]["run.output_interval_s"],
        "high_fidelity.time_step_s": float(record["time_step_s"]),
    }
    for dotted, replacement in overrides.items():
        _deep_set(effective, dotted, replacement)
    differences = _leaf_differences(baseline, effective)
    expected_differences = {
        dotted: {
            "baseline": _deep_get(baseline, dotted),
            "effective": replacement,
        }
        for dotted, replacement in overrides.items()
        if _deep_get(baseline, dotted) != replacement
    }
    common = contract["frozen_run_fields"]
    required_values = {
        "run.duration_s": common["run.duration_s"],
        "run.output_interval_s": common["run.output_interval_s"],
        "drive.ramp_duration_s": common["drive.ramp_duration_s"],
        "high_fidelity.pressure_solver_device": common[
            "high_fidelity.pressure_solver_device"
        ],
        "high_fidelity.pressure_relative_tolerance": common[
            "high_fidelity.pressure_relative_tolerance"
        ],
        "high_fidelity.cfl_controller_target_fraction": common[
            "high_fidelity.cfl_controller_target_fraction"
        ],
        "high_fidelity.maximum_cfl_step_retries": common[
            "high_fidelity.maximum_cfl_step_retries"
        ],
    }
    common_checks = {
        dotted: _deep_get(effective, dotted) == expected
        for dotted, expected in required_values.items()
    }
    binding = contract["baseline_configs"][grid_name]
    observed_blob = _blob_sha(baseline_path)
    gates = {
        "baseline_blob_matches": observed_blob == binding["git_blob_sha"],
        "observed_changes_within_allowlist": set(differences).issubset(
            set(contract["allowed_case_override_paths"])
        ),
        "changed_values_exact": differences == expected_differences,
        "all_frozen_run_fields_match": all(common_checks.values()),
        "case_time_step_matches": _deep_get(effective, "high_fidelity.time_step_s")
        == float(record["time_step_s"]),
    }
    identity = {
        "case_id": case_id,
        "grid": grid_name,
        "time_step_level": record["time_step_level"],
        "baseline_path": _relative(baseline_path),
        "baseline_git_blob_sha": observed_blob,
        "baseline_content_sha256": _sha256(baseline_path),
        "baseline_canonical_sha256": _canonical_sha256(baseline),
        "effective_canonical_sha256": _canonical_sha256(effective),
        "allowed_overrides": overrides,
        "observed_differences": differences,
        "common_execution_checks": common_checks,
        "gates": gates,
        "gate_pass": all(gates.values()),
    }
    if not identity["gate_pass"]:
        raise D3ExecutionError(f"configuration identity failed for {case_id}")
    return SimulationConfig.from_dict(effective), identity


def generated_config_path(case_id: str) -> Path:
    if case_id not in EXPECTED_CASES:
        raise D3ExecutionError(f"unknown D3 case: {case_id}")
    return GENERATED_CONFIG_ROOT / f"{case_id}.json"


def generate_configs(*, write: bool) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for case_id in EXPECTED_CASES:
        config, identity = effective_case_config(case_id)
        payload = config.to_dict()
        path = generated_config_path(case_id)
        if write:
            _write_json(path, payload)
        records[case_id] = {
            "path": _relative(path),
            "canonical_sha256": _canonical_sha256(payload),
            "identity": identity,
        }
    return {"gate_pass": True, "cases": records}


def audit_generated_configs() -> dict[str, Any]:
    expected_files = {generated_config_path(case_id) for case_id in EXPECTED_CASES}
    observed_files = (
        {path for path in GENERATED_CONFIG_ROOT.iterdir() if path.is_file()}
        if GENERATED_CONFIG_ROOT.is_dir()
        else set()
    )
    per_case: dict[str, Any] = {}
    for case_id in EXPECTED_CASES:
        path = generated_config_path(case_id)
        expected = effective_case_config(case_id)[0].to_dict()
        exists = path.is_file() and not path.is_symlink()
        observed = _json_object(path) if exists else None
        gates = {
            "regular_file_not_symlink": exists,
            "content_exact": observed == expected,
            "strict_json_round_trip": (
                observed is not None
                and _canonical_sha256(observed) == _canonical_sha256(expected)
            ),
        }
        per_case[case_id] = {
            "gate_pass": all(gates.values()),
            "gates": gates,
            "path": _relative(path),
            "content_sha256": _sha256(path) if exists else None,
            "canonical_sha256": _canonical_sha256(observed)
            if observed is not None
            else None,
        }
    gates = {
        "generated_directory_exists": GENERATED_CONFIG_ROOT.is_dir(),
        "exact_file_set": observed_files == expected_files,
        "all_cases_exact": all(item["gate_pass"] for item in per_case.values()),
    }
    return {"gate_pass": all(gates.values()), "gates": gates, "cases": per_case}


def configuration_matrix_audit() -> dict[str, Any]:
    identities: dict[str, Any] = {}
    physical_payloads: dict[str, dict[str, Any]] = {}
    allowed = set(_contract()["allowed_case_override_paths"])
    for case_id in EXPECTED_CASES:
        config, identity = effective_case_config(case_id)
        identities[case_id] = identity
        payload = config.to_dict()
        for dotted in allowed:
            _deep_set(payload, dotted, "<D3_REGISTERED_OVERRIDE>")
        physical_payloads[case_id] = payload
    group_gates = {
        grid_name: all(
            physical_payloads[case_id] == physical_payloads[case_ids[0]]
            for case_id in case_ids
        )
        for grid_name, case_ids in {
            "G6": EXPECTED_CASES[:3],
            "G9": EXPECTED_CASES[3:],
        }.items()
    }
    gates = {
        "all_case_identities": all(
            identity["gate_pass"] for identity in identities.values()
        ),
        "same_grid_physical_identity": all(group_gates.values()),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "same_grid_groups": group_gates,
        "cases": identities,
    }


def _descriptor(value: Any) -> dict[str, Any]:
    if isinstance(value, np.ndarray):
        return {
            "kind": "ndarray",
            "dtype": str(value.dtype),
            "shape": list(value.shape),
            "sha256": _array_sha256(value),
            "finite": bool(np.isfinite(value).all())
            if np.issubdtype(value.dtype, np.number)
            else True,
        }
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return {"kind": "scalar", "value": value}
    if isinstance(value, (tuple, list)):
        return {"kind": "sequence", "value": list(value)}
    raise TypeError(f"unsupported identity value: {type(value).__name__}")


def _grid_fingerprint(grid: ImmersedCartesianGrid) -> dict[str, Any]:
    return {name: _descriptor(value) for name, value in sorted(vars(grid).items())}


def _state_fingerprint(state: HF3DState) -> dict[str, Any]:
    return {
        field.name: _descriptor(getattr(state, field.name)) for field in fields(state)
    }


def _initial_identity_record(
    grid: ImmersedCartesianGrid,
    state: HF3DState,
    *,
    config_canonical_sha256: str,
) -> dict[str, Any]:
    return {
        "grid": _grid_fingerprint(grid),
        "state": _state_fingerprint(state),
        "formal_g0_bar_mask_sha256": _array_sha256(grid.bar_mask.astype(np.uint8)),
        "formal_g0_bar_cell_count": int(np.sum(grid.bar_mask)),
        "config_canonical_sha256": config_canonical_sha256,
        "state_time_s": state.time_s,
        "state_step": state.step,
    }


def initial_grid_state_identity_audit() -> dict[str, Any]:
    """Rebuild all six initial states without advancing a single time step."""

    backend = HighFidelityBackend()
    per_case: dict[str, Any] = {}
    for case_id in EXPECTED_CASES:
        config, config_identity = effective_case_config(case_id)
        grid = backend.make_grid(config)
        state = backend.initialise_state(config, grid)
        per_case[case_id] = _initial_identity_record(
            grid,
            state,
            config_canonical_sha256=config_identity["effective_canonical_sha256"],
        )
        del state, grid, config
        gc.collect()
    groups: dict[str, Any] = {}
    for grid_name, case_ids in {
        "G6": EXPECTED_CASES[:3],
        "G9": EXPECTED_CASES[3:],
    }.items():
        reference = per_case[case_ids[0]]
        gates = {
            "grid_dtype_shape_sha256_identical": all(
                per_case[case_id]["grid"] == reference["grid"]
                for case_id in case_ids[1:]
            ),
            "formal_g0_mask_identical": all(
                per_case[case_id]["formal_g0_bar_mask_sha256"]
                == reference["formal_g0_bar_mask_sha256"]
                for case_id in case_ids[1:]
            ),
            "initial_state_dtype_shape_sha256_identical": all(
                per_case[case_id]["state"] == reference["state"]
                for case_id in case_ids[1:]
            ),
            "all_states_at_zero_without_steps": all(
                per_case[case_id]["state_time_s"] == 0.0
                and per_case[case_id]["state_step"] == 0
                for case_id in case_ids
            ),
        }
        groups[grid_name] = {"gate_pass": all(gates.values()), "gates": gates}
    return {
        "gate_pass": all(group["gate_pass"] for group in groups.values()),
        "groups": groups,
        "cases": per_case,
        "time_steps_advanced": 0,
    }


def cuda_probe_audit() -> dict[str, Any]:
    probe = probe_gpu_projection()
    gates = {"cuda_fp64_probe_available": probe.available}
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "detail": probe.detail,
        "device_name": probe.device_name,
        "compute_capability": probe.compute_capability,
        "total_memory_bytes": probe.total_memory_bytes,
    }


def _contains_forbidden_d2_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        # Audit fields such as ``checkpoint_used: false`` are required evidence;
        # only stored values can identify an inadmissible source path or protocol.
        return any(_contains_forbidden_d2_reference(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_forbidden_d2_reference(item) for item in value)
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/").casefold()
    return any(
        token in normalized
        for token in (
            "d2_source_attribution",
            "06_d2",
            "06_d2数值误差来源归因",
            "iypt10_d2_quarantine",
            "checkpoint",
        )
    )


def audit_classification_input_paths(
    paths: Sequence[Path],
    *,
    allowed_root: Path = RAW_ROOT,
) -> dict[str, Any]:
    root = allowed_root.resolve()
    per_path: dict[str, Any] = {}
    for path in paths:
        resolved = path.resolve()
        within_root = resolved == root or resolved.is_relative_to(root)
        try:
            assert_r1_input_path(HERE, path)
            outside_v1_raw = True
        except ValueError:
            outside_v1_raw = False
        symlink_component = False
        cursor = path
        while True:
            if cursor.is_symlink():
                symlink_component = True
                break
            if cursor == allowed_root or cursor.parent == cursor:
                break
            cursor = cursor.parent
        gates = {
            "within_r1_raw_root": within_root,
            "outside_v1_raw_root": outside_v1_raw,
            "no_symlink_component": not symlink_component,
            "no_d2_or_checkpoint_reference": not _contains_forbidden_d2_reference(
                path.as_posix()
            ),
        }
        per_path[path.as_posix()] = {
            "gate_pass": all(gates.values()),
            "gates": gates,
        }
    gates = {
        "at_least_one_path": bool(paths),
        "all_paths_admissible": bool(paths)
        and all(item["gate_pass"] for item in per_path.values()),
    }
    return {"gate_pass": all(gates.values()), "gates": gates, "paths": per_path}


def _stage_pass_evidence(stage: str) -> list[str]:
    if stage == "S0":
        return [_relative(S0_PATH)]
    if stage in STAGE_CASES:
        return [
            _relative(_case_summary_path(case_id)) for case_id in STAGE_CASES[stage]
        ]
    if stage == "S3":
        return [
            _relative(REPORT_JSON),
            _relative(REPORT_MD),
            _relative(FINAL_MANIFEST),
            _relative(FINAL_EVIDENCE_ROOT / "stage_ledger.json"),
        ]
    raise D3ExecutionError(f"unknown D3 stage: {stage}")


def _sync_stage_ledger_summary(ledger: dict[str, Any]) -> None:
    stages = ledger.get("stages")
    if not isinstance(stages, list):
        raise D3ExecutionError("D3 stage ledger is malformed")
    failed = [
        str(item.get("stage"))
        for item in stages
        if isinstance(item, Mapping) and item.get("disposition") == "FAILED_STOPPED"
    ]
    unexecuted = [
        str(item.get("stage"))
        for item in stages
        if isinstance(item, Mapping) and item.get("disposition") == "NOT_EXECUTED"
    ]
    all_pass = all(
        isinstance(item, Mapping)
        and item.get("disposition") == "EXECUTED"
        and item.get("status") == "PASS"
        for item in stages
    )
    ledger["failed_stages"] = failed
    ledger["unexecuted_stages"] = unexecuted
    ledger["terminal_state"] = (
        FAIL_CLOSED if failed else "COMPLETE" if all_pass else "IN_PROGRESS"
    )
    ledger["failure_record"] = _relative(FAILURE_PATH) if failed else None


def stage_ledger_template(*, execution_commit: str | None) -> dict[str, Any]:
    ledger: dict[str, Any] = {
        "schema_version": 2,
        "protocol_id": PROTOCOL_ID,
        "execution_commit": execution_commit,
        "stages": [
            {
                "stage": stage,
                "action": STAGE_ACTIONS[stage],
                "human_description": STAGE_DESCRIPTIONS[stage],
                "disposition": "NOT_EXECUTED",
                "status": "NOT_EXECUTED",
                "cases": list(STAGE_CASES.get(stage, ())),
                "evidence": [],
            }
            for stage in STAGE_ORDER
        ],
        "automatic_follow_on": "PROHIBITED",
    }
    _sync_stage_ledger_summary(ledger)
    return ledger


def validate_stage_ledger_semantics(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a unique ordered state machine and exact evidence semantics."""

    stages = ledger.get("stages")
    exact_order = isinstance(stages, list) and [
        item.get("stage") if isinstance(item, Mapping) else None for item in stages
    ] == list(STAGE_ORDER)
    per_stage: dict[str, Any] = {}
    state_sequence: list[str] = []
    if isinstance(stages, list):
        for item in stages:
            if not isinstance(item, Mapping):
                continue
            stage = str(item.get("stage"))
            disposition = item.get("disposition")
            status = item.get("status")
            if disposition == "NOT_EXECUTED" and status == "NOT_EXECUTED":
                state = "NOT_EXECUTED"
                expected_evidence: list[str] = []
            elif disposition == "EXECUTED" and status == "PASS":
                state = "PASS"
                expected_evidence = (
                    _stage_pass_evidence(stage) if stage in STAGE_ORDER else []
                )
            elif disposition == "FAILED_STOPPED" and status == FAIL_CLOSED:
                state = "FAILED"
                expected_evidence = [_relative(FAILURE_PATH)]
            else:
                state = "INVALID"
                expected_evidence = []
            state_sequence.append(state)
            expected_cases = list(STAGE_CASES.get(stage, ()))
            classification_valid = (
                isinstance(item.get("classification"), str)
                and bool(item.get("classification"))
                if stage == "S3" and state == "PASS"
                else item.get("classification") is None
            )
            stage_gates = {
                "known_stage": stage in STAGE_ACTIONS,
                "action_exact": item.get("action") == STAGE_ACTIONS.get(stage),
                "human_description_exact": item.get("human_description")
                == STAGE_DESCRIPTIONS.get(stage),
                "cases_exact": item.get("cases") == expected_cases,
                "state_pair_allowed": state != "INVALID",
                "evidence_is_list": isinstance(item.get("evidence"), list),
                "evidence_paths_exact": item.get("evidence") == expected_evidence,
                "not_executed_has_no_evidence": state != "NOT_EXECUTED"
                or item.get("evidence") == [],
                "classification_semantics": classification_valid,
            }
            per_stage[stage] = {
                "gate_pass": all(stage_gates.values()),
                "gates": stage_gates,
                "state": state,
            }
    progression_valid = len(state_sequence) == len(STAGE_ORDER)
    closed = False
    failed_seen = False
    if progression_valid:
        for state in state_sequence:
            if not closed and state == "PASS":
                continue
            if not closed and state in {"NOT_EXECUTED", "FAILED"}:
                closed = True
                failed_seen = state == "FAILED"
                continue
            if closed and state == "NOT_EXECUTED":
                continue
            progression_valid = False
            break
    failed_stages = [
        STAGE_ORDER[index]
        for index, state in enumerate(state_sequence)
        if index < len(STAGE_ORDER) and state == "FAILED"
    ]
    unexecuted_stages = [
        STAGE_ORDER[index]
        for index, state in enumerate(state_sequence)
        if index < len(STAGE_ORDER) and state == "NOT_EXECUTED"
    ]
    all_pass = state_sequence == ["PASS"] * len(STAGE_ORDER)
    expected_terminal = (
        FAIL_CLOSED if failed_stages else "COMPLETE" if all_pass else "IN_PROGRESS"
    )
    expected_failure_record = _relative(FAILURE_PATH) if failed_stages else None
    execution_commit = ledger.get("execution_commit")
    gates = {
        "schema_version": ledger.get("schema_version") == 2,
        "protocol_id": ledger.get("protocol_id") == PROTOCOL_ID,
        "execution_commit_shape": execution_commit is None
        or _is_lower_hex(execution_commit, 40),
        "exact_stage_order": exact_order,
        "all_stage_semantics": set(per_stage) == set(STAGE_ORDER)
        and all(item["gate_pass"] for item in per_stage.values()),
        "legal_pass_failure_unexecuted_progression": progression_valid,
        "at_most_one_failed_stage": len(failed_stages) <= 1
        and not (failed_seen and len(failed_stages) != 1),
        "failed_stage_summary_exact": ledger.get("failed_stages") == failed_stages,
        "unexecuted_stage_summary_exact": ledger.get("unexecuted_stages")
        == unexecuted_stages,
        "terminal_state_exact": ledger.get("terminal_state") == expected_terminal,
        "failure_record_exact": ledger.get("failure_record") == expected_failure_record,
        "automatic_follow_on_prohibited": ledger.get("automatic_follow_on")
        == "PROHIBITED",
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "stages": per_stage,
        "state_sequence": state_sequence,
    }


def validate_stage_ledger_evidence_files(ledger: Mapping[str, Any]) -> dict[str, Any]:
    paths: list[str] = []
    for item in ledger.get("stages", []):
        if isinstance(item, Mapping) and isinstance(item.get("evidence"), list):
            paths.extend(str(path) for path in item["evidence"])
    per_path: dict[str, Any] = {}
    for path_text in paths:
        path = REPOSITORY_ROOT / path_text
        gates = {
            "regular_file_not_symlink": path.is_file() and not path.is_symlink(),
            "within_repository": path.resolve().is_relative_to(
                REPOSITORY_ROOT.resolve()
            ),
        }
        per_path[path_text] = {"gate_pass": all(gates.values()), "gates": gates}
    failed_stages = ledger.get("failed_stages")
    failure_semantics = True
    diagnostic_evidence_audit: dict[str, Any] = {}
    if isinstance(failed_stages, list) and failed_stages:
        if FAILURE_PATH.is_file() and not FAILURE_PATH.is_symlink():
            failure = _json_object(FAILURE_PATH)
            diagnostic_evidence = failure.get("diagnostic_evidence")
            if isinstance(diagnostic_evidence, list):
                for path_text in diagnostic_evidence:
                    path = REPOSITORY_ROOT / str(path_text)
                    resolved = path.resolve()
                    try:
                        assert_r1_input_path(HERE, path)
                        outside_v1_raw = True
                    except ValueError:
                        outside_v1_raw = False
                    path_gates = {
                        "exists": path.exists(),
                        "within_r1_raw_root": resolved == RAW_ROOT.resolve()
                        or resolved.is_relative_to(RAW_ROOT.resolve()),
                        "outside_v1_raw_root": outside_v1_raw,
                        "not_symlink": not path.is_symlink(),
                        "no_forbidden_reference": not _contains_forbidden_d2_reference(
                            str(path_text)
                        ),
                    }
                    diagnostic_evidence_audit[str(path_text)] = {
                        "gate_pass": all(path_gates.values()),
                        "gates": path_gates,
                    }
            failure_semantics = (
                failure.get("status") == FAIL_CLOSED
                and failure.get("stage") == failed_stages[0]
                and failure.get("execution_commit") == ledger.get("execution_commit")
                and isinstance(diagnostic_evidence, list)
                and all(
                    item["gate_pass"] for item in diagnostic_evidence_audit.values()
                )
            )
        else:
            failure_semantics = False
    gates = {
        "all_evidence_regular_and_within_repository": all(
            item["gate_pass"] for item in per_path.values()
        ),
        "failure_record_matches_failed_stage": failure_semantics,
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "paths": per_path,
        "diagnostic_evidence": diagnostic_evidence_audit,
    }


def stage_prerequisite_audit(
    stage: str,
    ledger: Mapping[str, Any],
    *,
    execution_commit: str,
) -> dict[str, Any]:
    """Prove a PASS prefix and untouched target/suffix without writing raw."""

    semantics = validate_stage_ledger_semantics(ledger)
    evidence = validate_stage_ledger_evidence_files(ledger)
    common = audit_common_stage_prerequisites(
        ledger,
        stage,
        stage_order=STAGE_ORDER,
        pass_evidence={item: _stage_pass_evidence(item) for item in STAGE_ORDER},
        expected_execution_commit=execution_commit,
        ledger_semantics_pass=semantics["gate_pass"],
        failure_marker_exists=FAILURE_PATH.exists(),
    )
    gates = {
        "common_stage_prerequisites": common["gate_pass"],
        "existing_evidence_files": evidence["gate_pass"],
    }
    return {
        "gate_pass": all(gates.values()),
        "stage": stage,
        "gates": gates,
        "common_audit": common,
        "ledger_semantics_audit": semantics,
        "evidence_file_audit": evidence,
    }


def assert_stage_ready(
    stage: str,
    ledger: Mapping[str, Any],
    *,
    execution_commit: str,
) -> dict[str, Any]:
    """Raise a side-effect-free precondition error before entering a stage."""

    audit = stage_prerequisite_audit(
        stage,
        ledger,
        execution_commit=execution_commit,
    )
    if not audit["gate_pass"]:
        failed = [name for name, passed in audit["gates"].items() if not passed]
        common_failed = [
            name
            for name, passed in audit["common_audit"]["gates"].items()
            if not passed
        ]
        raise D3PreconditionError(
            f"{stage} stage prerequisites rejected before transaction: "
            + json.dumps(
                {"top_level": failed, "common": common_failed},
                ensure_ascii=False,
            )
        )
    return audit


def _no_dynamic_output_audit() -> dict[str, Any]:
    paths = (RAW_ROOT, REPORT_JSON, REPORT_MD, FINAL_MANIFEST, FINAL_EVIDENCE_ROOT)
    observed = {str(path.name): path.exists() for path in paths}
    return {
        "gate_pass": not any(observed.values()),
        "observed": observed,
    }


def _run_unit_tests() -> dict[str, Any]:
    command = [
        sys.executable,
        "-Werror",
        "-m",
        "unittest",
        "discover",
        "-s",
        str(HERE / "tests"),
        "-p",
        "test_d3_cross_grid_temporal.py",
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
            "D3_GPU_PYTHON",
            "-Werror",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_d3_cross_grid_temporal.py",
            "-v",
        ],
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "pass": completed.returncode == 0,
    }


def _environment_identity() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in ("numpy", "cupy-cuda13x", "scipy"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "unavailable"
    prefix = Path(sys.prefix).absolute()
    try:
        environment = prefix.relative_to(REPOSITORY_ROOT.absolute()).as_posix()
    except ValueError:
        environment = "outside_repository"
    return {
        "python_version": sys.version.split()[0],
        "python_implementation": sys.implementation.name,
        "environment_repository_relative": environment,
        "absolute_executable_stored": False,
        "packages": packages,
    }


def _implementation_content_evidence() -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for path in _implementation_paths():
        try:
            blob = _blob_sha(path)
        except subprocess.CalledProcessError:
            blob = ""
        evidence[_relative(path)] = {
            "exists": path.is_file(),
            "git_blob_sha": blob,
            "content_sha256": _sha256(path) if path.is_file() else None,
            "size_bytes": path.stat().st_size if path.is_file() else None,
        }
    return evidence


def static_implementation_audit(*, require_clean: bool) -> dict[str, Any]:
    """Run the authorized non-stepping D3 implementation audit."""

    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    contract = _contract()
    contract_shape = validate_contract_shape(contract)
    scientific_identity = r1_scientific_identity_audit()
    v1_frozen_evidence = audit_v1_frozen_evidence(HERE)
    evidence_root_isolation = audit_evidence_root_isolation(HERE)
    schedule = registered_schedule_audit(
        contract["native_output_times_s"],
        duration_s=float(contract["frozen_run_fields"]["run.duration_s"]),
        output_interval_s=float(contract["frozen_run_fields"]["run.output_interval_s"]),
    )
    configs = configuration_matrix_audit()
    generated = audit_generated_configs()
    source = _capture_source("D3 implementation static review")
    expected_source = contract["formal_source_contract"]["required_tree_sha256"]
    initial_identity = initial_grid_state_identity_audit()
    cuda = cuda_probe_audit()
    no_dynamic = _no_dynamic_output_audit()
    unit_tests = _run_unit_tests()
    implementation = _implementation_content_evidence()
    authorization = execution_authorization_audit(require_authorized=False)
    exact_authorization = execution_authorization_audit(require_authorized=True)
    environment = _environment_identity()
    ledger = stage_ledger_template(execution_commit=None)
    ledger_audit = validate_stage_ledger_semantics(ledger)
    gates = {
        "main_branch": branch == "main",
        "clean_worktree_when_required": (not require_clean or not porcelain),
        "contract_shape": contract_shape["gate_pass"],
        "r1_scientific_fields_identical_to_v1": scientific_identity["gate_pass"],
        "v1_frozen_evidence_hashes": v1_frozen_evidence["gate_pass"],
        "r1_v1_evidence_roots_disjoint": evidence_root_isolation["gate_pass"],
        "r1_output_schedule_bitwise_contract": schedule["gate_pass"],
        "configuration_matrix": configs["gate_pass"],
        "generated_configs": generated["gate_pass"],
        "formal_source_tree_bound": source.get("available") is True
        and source.get("value") == expected_source,
        "initial_grid_mask_state_identity": initial_identity["gate_pass"],
        "cuda_probe_available": cuda["gate_pass"],
        "no_dynamic_outputs": no_dynamic["gate_pass"],
        "unit_tests": unit_tests["pass"],
        "all_implementation_files_present": all(
            item["exists"] for item in implementation.values()
        ),
        "all_implementation_files_committed": all(
            bool(item["git_blob_sha"]) for item in implementation.values()
        ),
        "separate_authorization_object_valid": authorization["gate_pass"],
        "review_snapshot_not_already_execution_authorized": not exact_authorization[
            "gate_pass"
        ],
        "dynamic_execution_still_unauthorized": contract["authorization"][
            "real_cuda_dynamic_execution"
        ]
        is False,
        "stage_ledger_semantics": ledger_audit["gate_pass"],
        "no_time_steps_advanced": initial_identity["time_steps_advanced"] == 0,
    }
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "status": STATIC_READY if all(gates.values()) else FAIL_CLOSED,
        "gate_pass": all(gates.values()),
        "gates": gates,
        "identity": {
            "branch": branch,
            "observed_head": head,
            "worktree_porcelain": porcelain,
            "clean_required": require_clean,
            "formal_source_tree": source,
            "contract_content_sha256": _sha256(PREREGISTRATION_PATH),
        },
        "contract_shape_audit": contract_shape,
        "r1_scientific_identity_audit": scientific_identity,
        "v1_frozen_evidence_audit": v1_frozen_evidence,
        "r1_v1_evidence_root_isolation_audit": evidence_root_isolation,
        "r1_registered_output_schedule_audit": schedule,
        "configuration_matrix_audit": configs,
        "generated_config_audit": generated,
        "initial_grid_mask_state_identity": initial_identity,
        "cuda_probe": cuda,
        "unit_tests": unit_tests,
        "implementation_content_evidence": implementation,
        "execution_authorization_audit": authorization,
        "exact_execution_authorization_audit": exact_authorization,
        "environment": environment,
        "dynamic_output_hygiene": no_dynamic,
        "future_stage_ledger_template": ledger,
        "future_stage_ledger_semantics_audit": ledger_audit,
        "authorization_boundary": {
            "static_tests_and_nonstepping_audits": contract["authorization"][
                "static_tests_and_nonstepping_audits"
            ],
            "real_cuda_dynamic_execution": contract["authorization"][
                "real_cuda_dynamic_execution"
            ],
            "time_steps_advanced_by_this_audit": 0,
        },
    }


def require_dynamic_execution_authorized() -> dict[str, Any]:
    audit = execution_authorization_audit(require_authorized=True)
    if not audit["gate_pass"]:
        failed = [name for name, passed in audit["gates"].items() if not passed]
        raise D3ExecutionError(
            "D3 real CUDA dynamic execution is not authorized for the exact "
            "reviewed implementation: " + json.dumps(failed, ensure_ascii=False)
        )
    return audit


def _implementation_paths() -> tuple[Path, ...]:
    # The authorization object is intentionally excluded.  A future Pro ruling
    # changes only that object in the sole child commit of the reviewed
    # implementation commit, avoiding an impossible self-hash recursion.
    return (
        HERE / "README.md",
        HERE / "audit_d3_v1_s1_failure.py",
        HERE / "artifacts" / "v1_failure" / "d3_v1_s1_failure_forensic.json",
        HERE / "artifacts" / "v1_failure" / "d3_v1_s1_failure_forensic.md",
        HERE / "d3_r1_contract.py",
        HERE / "d3_stage_machine.py",
        HERE / "run_d3_cross_grid_temporal.py",
        HERE / "analyse_d3_cross_grid_temporal.py",
        HERE / "tests" / "test_d3_cross_grid_temporal.py",
        V1_PREREGISTRATION_PATH,
        PREREGISTRATION_PATH,
        *(generated_config_path(case_id) for case_id in EXPECTED_CASES),
    )


def execution_binding_audit() -> dict[str, Any]:
    """Recheck immutable code, source, config, revision, and Pro authorization."""

    contract = _contract()
    scientific_identity = r1_scientific_identity_audit()
    v1_frozen_evidence = audit_v1_frozen_evidence(HERE)
    evidence_root_isolation = audit_evidence_root_isolation(HERE)
    schedule = registered_schedule_audit(
        contract["native_output_times_s"],
        duration_s=float(contract["frozen_run_fields"]["run.duration_s"]),
        output_interval_s=float(contract["frozen_run_fields"]["run.output_interval_s"]),
    )
    authorization = require_dynamic_execution_authorized()
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    source = _capture_source("D3 execution identity boundary")
    implementation = _implementation_binding()
    cuda = cuda_probe_audit()
    generated = audit_generated_configs()
    gates = {
        "main_branch": branch == "main",
        "clean_worktree": not porcelain,
        "pro_authorization_bound_to_exact_child_commit": authorization["gate_pass"],
        "r1_scientific_fields_identical_to_v1": scientific_identity["gate_pass"],
        "v1_frozen_evidence_hashes": v1_frozen_evidence["gate_pass"],
        "r1_v1_evidence_roots_disjoint": evidence_root_isolation["gate_pass"],
        "r1_output_schedule_bitwise_contract": schedule["gate_pass"],
        "formal_source_tree_matches": source.get("available") is True
        and source.get("value")
        == contract["formal_source_contract"]["required_tree_sha256"],
        "implementation_and_contract_committed": all(
            bool(item["git_blob_sha"]) for item in implementation.values()
        ),
        "generated_configs_exact": generated["gate_pass"],
        "actual_cuda_probe_available": cuda["gate_pass"],
    }
    result = {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "branch": branch,
        "execution_commit": head,
        "worktree_porcelain": porcelain,
        "formal_source_tree": source,
        "implementation": implementation,
        "authorization": authorization,
        "r1_scientific_identity_audit": scientific_identity,
        "v1_frozen_evidence_audit": v1_frozen_evidence,
        "r1_v1_evidence_root_isolation_audit": evidence_root_isolation,
        "r1_registered_output_schedule_audit": schedule,
        "generated_config_audit": generated,
        "cuda_probe": cuda,
    }
    if not result["gate_pass"]:
        failed = [name for name, passed in gates.items() if not passed]
        raise D3ExecutionError(
            "D3 execution binding failed: " + json.dumps(failed, ensure_ascii=False)
        )
    return result


def execution_identity_audit(*, require_fresh: bool) -> dict[str, Any]:
    """Evaluate raw-evidence state around the immutable execution binding."""

    binding = execution_binding_audit()
    head = str(binding["execution_commit"])
    evidence_gates: dict[str, bool] = {}
    if require_fresh:
        evidence_gates["no_prior_d3_raw_or_final_output"] = _no_dynamic_output_audit()[
            "gate_pass"
        ]
    else:
        s0_regular = S0_PATH.is_file() and not S0_PATH.is_symlink()
        s0 = _json_object(S0_PATH) if s0_regular else {}
        ledger_regular = (
            STAGE_LEDGER_PATH.is_file() and not STAGE_LEDGER_PATH.is_symlink()
        )
        ledger = _json_object(STAGE_LEDGER_PATH) if ledger_regular else {}
        ledger_semantics = validate_stage_ledger_semantics(ledger)
        ledger_evidence = validate_stage_ledger_evidence_files(ledger)
        s0_report_audit = audit_s0_report(
            s0,
            protocol_id=PROTOCOL_ID,
            expected_commit=head,
            expected_action=STAGE_ACTIONS["S0"],
            expected_description=STAGE_DESCRIPTIONS["S0"],
            expected_status=S0_PASS,
            expected_case_ids=EXPECTED_CASES,
        )
        s0_entries = [
            item
            for item in ledger.get("stages", [])
            if isinstance(item, Mapping) and item.get("stage") == "S0"
        ]
        s0_entry = s0_entries[0] if len(s0_entries) == 1 else {}
        evidence_gates.update(
            {
                "s0_regular_file_not_symlink": s0_regular,
                "s0_report_identity_exact": s0_report_audit["gate_pass"],
                "stage_ledger_regular_file_not_symlink": ledger_regular,
                "s0_ledger_entry_executed_pass": s0_entry.get("disposition")
                == "EXECUTED"
                and s0_entry.get("status") == "PASS",
                "s0_ledger_evidence_exact": s0_entry.get("evidence")
                == _stage_pass_evidence("S0"),
                "stage_ledger_execution_commit": ledger.get("execution_commit") == head,
                "stage_ledger_semantics": ledger_semantics["gate_pass"],
                "stage_ledger_evidence": ledger_evidence["gate_pass"],
                "no_failure_marker": not FAILURE_PATH.exists(),
                "no_final_output_before_s3": not any(
                    path.exists() for path in (REPORT_JSON, REPORT_MD, FINAL_MANIFEST)
                ),
            }
        )
    gates = {"immutable_execution_binding": binding["gate_pass"], **evidence_gates}
    result = {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "branch": binding["branch"],
        "execution_commit": head,
        "worktree_porcelain": binding["worktree_porcelain"],
        "formal_source_tree": binding["formal_source_tree"],
        "implementation": binding["implementation"],
        "authorization": binding["authorization"],
        "r1_scientific_identity_audit": binding["r1_scientific_identity_audit"],
        "v1_frozen_evidence_audit": binding["v1_frozen_evidence_audit"],
        "r1_v1_evidence_root_isolation_audit": binding[
            "r1_v1_evidence_root_isolation_audit"
        ],
        "r1_registered_output_schedule_audit": binding[
            "r1_registered_output_schedule_audit"
        ],
        "generated_config_audit": binding["generated_config_audit"],
        "cuda_probe": binding["cuda_probe"],
    }
    if not result["gate_pass"]:
        failed = [name for name, passed in gates.items() if not passed]
        raise D3ExecutionError(
            "D3 execution identity failed: " + json.dumps(failed, ensure_ascii=False)
        )
    return result


def _all_finite(value: object) -> bool:
    if isinstance(value, np.ndarray):
        return bool(np.isfinite(value).all())
    if isinstance(value, np.generic):
        return bool(np.isfinite(value))
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (str, int, bool, type(None))):
        return True
    if is_dataclass(value):
        return all(_all_finite(getattr(value, field.name)) for field in fields(value))
    if isinstance(value, Mapping):
        return all(_all_finite(item) for item in value.values())
    if isinstance(value, Sequence):
        return all(_all_finite(item) for item in value)
    return True


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


def _prepare_runtime(case_id: str) -> CaseRuntime:
    config, identity = effective_case_config(case_id)
    generated = _json_object(generated_config_path(case_id))
    if generated != config.to_dict():
        raise D3ExecutionError(f"generated config drifted before {case_id}")
    backend = HighFidelityBackend()
    grid = backend.make_grid(config)
    projection, accelerator = make_pressure_projection(config, grid)
    if not (
        accelerator.get("selected") == "cuda" and accelerator.get("fallback") is False
    ):
        raise D3ExecutionError(f"{case_id} did not select actual CUDA")
    state = backend.initialise_state(config, grid)
    initial_volume = backend._initial_liquid_volume(config, grid)
    recorder = FrameRecorder(config, grid, state, initial_volume)
    diagnostics = StepDiagnosticsBuffer(initial_volume)
    events: list[Event] = []
    tracker = TopologyEventTracker(config, events)
    zero = backend._zero_step_diagnostics(state, config, grid, initial_volume)
    recorder.record_frame(zero)
    tracker.initialise(zero.topology)
    return CaseRuntime(
        label=case_id,
        config=config,
        config_identity=identity,
        grid=grid,
        state=state,
        projection=projection,
        pressure_accelerator=dict(accelerator),
        initial_volume_m3=initial_volume,
        expected_volume_m3=initial_volume,
        recorder=recorder,
        diagnostics=diagnostics,
        events=events,
        event_tracker=tracker,
        last_diagnostics=zero,
    )


def _step_hard_gate(
    runtime: CaseRuntime,
    diagnostics: HF3DStepDiagnostics,
) -> tuple[bool, list[str]]:
    quality = _contract()["numerical_quality_hard_gates"]
    failed: list[str] = []
    if not _all_finite(runtime.state) or not _all_finite(diagnostics):
        failed.append("all_state_and_diagnostic_values_finite")
    if diagnostics.pressure_relative_residual > float(
        quality["pressure_relative_residual_max"]
    ):
        failed.append("pressure_relative_residual")
    if diagnostics.divergence_after_s_inv > float(
        quality["projected_divergence_max_s_inv"]
    ):
        failed.append("projected_divergence")
    if diagnostics.advective_cfl > float(quality["accepted_advective_cfl_max"]) * (
        1.0 + 1.0e-12
    ):
        failed.append("accepted_advective_cfl")
    balance = (
        abs(diagnostics.liquid_volume_balance_error_m3) / runtime.initial_volume_m3
    )
    if balance > float(quality["liquid_volume_balance_error_relative_max"]):
        failed.append("liquid_volume_balance_error_relative")
    if diagnostics.cfl_retry_count > int(quality["maximum_cfl_step_retries"]):
        failed.append("maximum_cfl_step_retries")
    if not (
        runtime.pressure_accelerator.get("selected") == "cuda"
        and runtime.pressure_accelerator.get("fallback") is False
    ):
        failed.append("actual_cuda_without_fallback")
    return not failed, failed


def advance_runtime_to_registered_end(runtime: CaseRuntime) -> dict[str, Any]:
    """Advance one fresh R1 case to each directly indexed registered target."""

    backend = HighFidelityBackend()
    end_time = runtime.config.run.duration_s
    registered_times = np.asarray(
        _contract()["native_output_times_s"], dtype=np.float64
    )
    schedule = registered_schedule_audit(
        registered_times,
        duration_s=end_time,
        output_interval_s=runtime.config.run.output_interval_s,
    )
    if not schedule["gate_pass"]:
        failed = [name for name, passed in schedule["gates"].items() if not passed]
        raise D3ExecutionError(
            "D3R1 registered output schedule failed before stepping: "
            + json.dumps(failed, ensure_ascii=False)
        )
    tolerance = 128.0 * np.finfo(float).eps * max(end_time, 1.0)
    start_wall = time.monotonic()
    start_cpu = time.process_time()
    total_retries = 0
    next_index = 1
    failure: dict[str, Any] | None = None
    try:
        while next_index < registered_times.size:
            next_target = float(registered_times[next_index])
            while runtime.state.time_s < next_target - tolerance:
                proposal = propose_time_step(
                    runtime.state, runtime.config, runtime.grid
                )
                remaining = next_target - runtime.state.time_s
                dt_s = min(proposal.stable_dt_s, remaining)
                if not math.isfinite(dt_s) or dt_s <= 0.0:
                    raise FloatingPointError("D3R1 trial time step became non-positive")
                limiter = (
                    "output_alignment"
                    if remaining < proposal.stable_dt_s
                    else str(proposal.selected_limiter)
                )
                initial_attempt = dt_s
                retry_count = 0
                maximum_rejected = 0.0
                while True:
                    checkpoint = runtime.projection.checkpoint_history()
                    trial_state = _clone_state(runtime.state)
                    try:
                        trial_diagnostics = backend.advance_one_step(
                            trial_state,
                            runtime.config,
                            runtime.grid,
                            runtime.projection,
                            dt_s,
                            expected_liquid_volume_m3=runtime.expected_volume_m3,
                            time_step_limiter=limiter,
                            cfl_retry_count=retry_count,
                            initial_attempt_dt_s=initial_attempt,
                            maximum_rejected_post_projection_cfl=maximum_rejected,
                        )
                    except HF3DPostProjectionCFLExceeded as exc:
                        runtime.projection.restore_history(checkpoint)
                        maximum_rejected = max(
                            maximum_rejected,
                            exc.diagnostics.post_projection_advective_cfl,
                        )
                        maximum_retries = (
                            runtime.config.high_fidelity.maximum_cfl_step_retries
                        )
                        if retry_count >= maximum_retries:
                            raise FloatingPointError(
                                "D3R1 CFL retry gate exhausted at accepted step "
                                f"{runtime.state.step + 1}"
                            ) from exc
                        dt_s = retry_time_step_s(
                            dt_s,
                            exc.diagnostics.post_projection_advective_cfl,
                            runtime.config.high_fidelity.cfl_limit,
                            runtime.config.high_fidelity.cfl_controller_target_fraction,
                        )
                        retry_count += 1
                        total_retries += 1
                        limiter = "post_projection_cfl_retry"
                        runtime.events.append(
                            Event(
                                runtime.state.time_s,
                                "d3_cfl_step_retry",
                                "Rejected D3R1 trial was rolled back before a stricter retry.",
                                {
                                    "case": runtime.label,
                                    "retry_number": retry_count,
                                    "next_dt_s": dt_s,
                                },
                            )
                        )
                        continue
                    except Exception:
                        runtime.projection.restore_history(checkpoint)
                        raise
                    runtime.state = trial_state
                    runtime.recorder.state = runtime.state
                    runtime.last_diagnostics = trial_diagnostics
                    runtime.expected_volume_m3 = (
                        trial_diagnostics.expected_liquid_volume_m3
                    )
                    runtime.event_tracker.update(
                        runtime.state.time_s,
                        trial_diagnostics,
                    )
                    runtime.diagnostics.append(
                        runtime.state.time_s,
                        trial_diagnostics,
                        runtime.config,
                    )
                    passed, failed = _step_hard_gate(runtime, trial_diagnostics)
                    if not passed:
                        raise FloatingPointError(
                            f"{runtime.label} hard gate failed: {', '.join(failed)}"
                        )
                    break
            if abs(runtime.state.time_s - next_target) > tolerance:
                raise FloatingPointError(
                    f"D3R1 skipped registered output target at index {next_index}"
                )
            runtime.state.time_s = next_target
            runtime.recorder.record_frame(runtime.last_diagnostics)
            print(
                json.dumps(
                    {
                        "stage": "d3r1_case_progress",
                        "case": runtime.label,
                        "registered_output_index": next_index,
                        "time_s": runtime.state.time_s,
                        "accepted_steps": runtime.state.step,
                        "retry_attempts": total_retries,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            next_index += 1
    except Exception as exc:  # noqa: BLE001 - preserve fail-closed evidence
        failure = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "accepted_steps": runtime.state.step,
            "simulated_time_s": runtime.state.time_s,
        }
    return {
        "completed": failure is None
        and next_index == registered_times.size
        and math.isclose(
            runtime.state.time_s, end_time, rel_tol=0.0, abs_tol=tolerance
        ),
        "failure": failure,
        "retry_attempts": total_retries,
        "registered_outputs_recorded": next_index,
        "wall_time_s": time.monotonic() - start_wall,
        "process_cpu_time_s": time.process_time() - start_cpu,
    }


def _runtime_quality(runtime: CaseRuntime, *, completed: bool) -> dict[str, Any]:
    quality = _contract()["numerical_quality_hard_gates"]
    dt = np.asarray(runtime.diagnostics.step_dt, dtype=np.float64)
    pressure = np.asarray(runtime.diagnostics.step_pressure_residual, dtype=np.float64)
    divergence = np.asarray(runtime.diagnostics.step_divergence, dtype=np.float64)
    cfl = np.asarray(runtime.diagnostics.step_post_projection_cfl, dtype=np.float64)
    retries = np.asarray(runtime.diagnostics.step_cfl_retry_count, dtype=np.float64)
    balance = np.asarray(
        runtime.recorder.observable_lists.get(
            "liquid_volume_balance_error_relative", []
        ),
        dtype=np.float64,
    )
    tolerance = max(1.0e-15, runtime.config.high_fidelity.time_step_s * 1.0e-9)
    active_fraction = (
        float(
            np.mean(np.abs(dt - runtime.config.high_fidelity.time_step_s) <= tolerance)
        )
        if dt.size
        else 0.0
    )
    clip = runtime.diagnostics.cumulative_clip / runtime.initial_volume_m3
    repair = runtime.diagnostics.cumulative_repair / runtime.initial_volume_m3
    frame_time = np.asarray(runtime.recorder.times, dtype=np.float64)
    gates = {
        "complete_to_registered_end_time": completed
        and math.isclose(
            runtime.state.time_s,
            runtime.config.run.duration_s,
            rel_tol=0.0,
            abs_tol=2.0e-15,
        ),
        "all_state_and_diagnostic_values_finite": _all_finite(runtime.state)
        and all(
            np.isfinite(value).all()
            for value in (dt, pressure, divergence, cfl, retries, balance)
        ),
        "pressure_relative_residual": bool(pressure.size)
        and float(np.max(pressure)) <= float(quality["pressure_relative_residual_max"]),
        "projected_divergence": bool(divergence.size)
        and float(np.max(divergence))
        <= float(quality["projected_divergence_max_s_inv"]),
        "accepted_advective_cfl": bool(cfl.size)
        and float(np.max(cfl))
        <= float(quality["accepted_advective_cfl_max"]) * (1.0 + 1.0e-12),
        "cumulative_phase_clip_relative": clip
        <= float(quality["cumulative_phase_clip_relative_max"]),
        "cumulative_phase_repair_relative": repair
        <= float(quality["cumulative_phase_repair_relative_max"]),
        "liquid_volume_balance_error_relative": bool(balance.size)
        and float(np.max(np.abs(balance)))
        <= float(quality["liquid_volume_balance_error_relative_max"]),
        "configured_time_step_active_fraction": active_fraction
        >= float(quality["configured_time_step_active_fraction_min"]),
        "maximum_cfl_step_retries": (
            not retries.size
            or float(np.max(retries)) <= float(quality["maximum_cfl_step_retries"])
        ),
        "actual_cuda_without_fallback": runtime.pressure_accelerator.get("selected")
        == "cuda"
        and runtime.pressure_accelerator.get("fallback") is False,
        "native_output_times_exact": bool(
            np.array_equal(frame_time, EXPECTED_OUTPUT_TIME)
        ),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "metrics": {
            "accepted_steps": runtime.state.step,
            "simulated_time_s": runtime.state.time_s,
            "configured_time_step_active_fraction": active_fraction,
            "maximum_pressure_relative_residual": float(np.max(pressure))
            if pressure.size
            else None,
            "maximum_projected_divergence_s_inv": float(np.max(divergence))
            if divergence.size
            else None,
            "maximum_advective_cfl": float(np.max(cfl)) if cfl.size else None,
            "maximum_cfl_retry_count": float(np.max(retries)) if retries.size else None,
            "cumulative_phase_clip_relative": clip,
            "cumulative_phase_repair_relative": repair,
            "maximum_liquid_volume_balance_error_relative": float(
                np.max(np.abs(balance))
            )
            if balance.size
            else None,
        },
    }


def _runtime_arrays(runtime: CaseRuntime) -> dict[str, NDArray[Any]]:
    arrays: dict[str, NDArray[Any]] = {
        "state_u_x_face": runtime.state.u_x_face,
        "state_u_y_face": runtime.state.u_y_face,
        "state_u_z_face": runtime.state.u_z_face,
        "state_phase": runtime.state.phase,
        "state_pressure_pa": runtime.state.pressure_pa,
        "frame_time_s": np.asarray(runtime.recorder.times, dtype=np.float64),
        "grid_bar_mask": runtime.grid.bar_mask.astype(np.uint8),
        "grid_active_mask": runtime.grid.active_mask.astype(np.uint8),
    }
    for name, value in vars(runtime.diagnostics).items():
        if isinstance(value, list):
            arrays[f"diagnostic_{name}"] = np.asarray(value, dtype=np.float64)
    for name, value in runtime.recorder.observable_lists.items():
        arrays[f"observable_{name}"] = np.asarray(value, dtype=np.float64)
    return arrays


def _save_partial(path: Path, runtime: CaseRuntime) -> dict[str, Any]:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **_runtime_arrays(runtime))
    return {
        "path": _relative(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _archive_payloads(path: Path) -> dict[str, Any]:
    return {
        child.name: {"sha256": _sha256(child), "size_bytes": child.stat().st_size}
        for child in sorted(path.iterdir())
        if child.is_file()
    }


def _assemble_archive(
    runtime: CaseRuntime,
    target: Path,
    *,
    source_start: Mapping[str, object],
    source_end: Mapping[str, object],
    revision_start: Mapping[str, object],
    revision_end: Mapping[str, object],
    process_cpu_time_s: float,
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
        backend_id=f"d3_cross_grid_temporal_{runtime.label.lower()}",
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
            "case_id": runtime.label,
            "fresh_from_zero": True,
            "checkpoint_used": False,
            "d2_dynamic_input_used": False,
            "formal_g0_geometry": True,
        },
        process_cpu_seconds=process_cpu_time_s,
        invocation_entry_point="run_d3_cross_grid_temporal.py",
        invocation_argument_summary={
            "protocol_id": PROTOCOL_ID,
            "case_id": runtime.label,
            "config_canonical_sha256": _canonical_sha256(runtime.config.to_dict()),
        },
        source_tree_execution_start=source_start,
        source_tree_execution_end=source_end,
        git_revision_execution_start=revision_start,
        git_revision_execution_end=revision_end,
    )
    reloaded = load_run_archive(target, verify=True)
    if reloaded.result.status is not RunStatus.COMPLETED:
        raise D3ExecutionError(f"archive did not reload as completed: {target}")
    if not np.array_equal(reloaded.result.time_s, EXPECTED_OUTPUT_TIME):
        raise D3ExecutionError(
            f"archive output times are not native D3 times: {target}"
        )
    return {
        "path": _relative(target),
        "payloads": _archive_payloads(target),
        "result_status": reloaded.result.status.value,
        "frame_time_sha256": _array_sha256(reloaded.result.time_s),
    }


def _case_summary_path(case_id: str) -> Path:
    return RAW_ROOT / "cases" / case_id / "case_summary.json"


def _require_case_pass(case_id: str) -> dict[str, Any]:
    path = _case_summary_path(case_id)
    if not path.is_file():
        raise D3ExecutionError(f"required prior D3 case is missing: {case_id}")
    summary = _json_object(path)
    if summary.get("status") != CASE_PASS or summary.get("gate_pass") is not True:
        raise D3ExecutionError(f"required prior D3 case did not pass: {case_id}")
    return summary


def _execution_stage_entry(
    stage: str,
    *,
    disposition: str,
    status: str,
    evidence: Sequence[str],
) -> dict[str, Any]:
    return {
        "stage": stage,
        "action": STAGE_ACTIONS[stage],
        "human_description": STAGE_DESCRIPTIONS[stage],
        "disposition": disposition,
        "status": status,
        "cases": list(STAGE_CASES.get(stage, ())),
        "evidence": list(evidence),
    }


def _transition_stage_entry(
    ledger: Mapping[str, Any],
    stage: str,
    *,
    disposition: str,
    status: str,
    evidence: Sequence[str],
) -> dict[str, Any]:
    """Build and validate a stage transition entirely in memory."""

    current_audit = validate_stage_ledger_semantics(ledger)
    if not current_audit["gate_pass"]:
        raise D3ExecutionError("D3 stage ledger is invalid before transition")
    result = deepcopy(dict(ledger))
    stages = result.get("stages")
    if not isinstance(stages, list):
        raise D3ExecutionError("D3 stage ledger is malformed")
    replacements = 0
    for index, item in enumerate(stages):
        if isinstance(item, Mapping) and item.get("stage") == stage:
            stages[index] = _execution_stage_entry(
                stage,
                disposition=disposition,
                status=status,
                evidence=evidence,
            )
            replacements += 1
    if replacements != 1:
        raise D3ExecutionError(
            f"D3 stage ledger has {replacements} entries for {stage}"
        )
    _sync_stage_ledger_summary(result)
    audit = validate_stage_ledger_semantics(result)
    if not audit["gate_pass"]:
        raise D3ExecutionError(f"D3 stage ledger semantic audit failed for {stage}")
    return result


def _replace_stage_entry(
    stage: str,
    *,
    disposition: str,
    status: str,
    evidence: Sequence[str],
) -> None:
    ledger = _json_object(STAGE_LEDGER_PATH)
    result = _transition_stage_entry(
        ledger,
        stage,
        disposition=disposition,
        status=status,
        evidence=evidence,
    )
    evidence_audit = validate_stage_ledger_evidence_files(result)
    if not evidence_audit["gate_pass"]:
        raise D3ExecutionError(f"D3 stage ledger evidence audit failed for {stage}")
    _write_json(STAGE_LEDGER_PATH, result)


def command_prepare_execution() -> int:
    try:
        identity = execution_identity_audit(require_fresh=True)
    except D3PreconditionError:
        raise
    except Exception as exc:
        raise D3PreconditionError(
            "S0 preconditions rejected before creating D3 raw: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    ledger = stage_ledger_template(execution_commit=identity["execution_commit"])
    if not validate_stage_ledger_semantics(ledger)["gate_pass"]:
        raise D3ExecutionError("initial S0 stage ledger semantic audit failed")
    _write_json(STAGE_LEDGER_PATH, ledger)
    initial_identity = initial_grid_state_identity_audit()
    if not initial_identity["gate_pass"]:
        raise D3ExecutionError("D3 same-grid initial identity audit failed")
    binding_end = execution_binding_audit()
    if binding_end["execution_commit"] != identity["execution_commit"]:
        raise D3ExecutionError("D3 execution binding changed during S0")
    report = {
        "schema_version": 2,
        "protocol_id": PROTOCOL_ID,
        "stage": "S0",
        "action": STAGE_ACTIONS["S0"],
        "human_description": STAGE_DESCRIPTIONS["S0"],
        "status": S0_PASS,
        "gate_pass": True,
        "execution_commit": identity["execution_commit"],
        "execution_identity_start": identity,
        "execution_binding_end": binding_end,
        "initial_grid_mask_state_identity": initial_identity,
        "fresh_evidence_policy": {
            "d2_dynamic_inputs_used": False,
            "checkpoint_used": False,
            "d3_prior_raw_present": False,
            "time_steps_advanced": 0,
        },
    }
    _write_json(S0_PATH, report)
    try:
        _replace_stage_entry(
            "S0",
            disposition="EXECUTED",
            status="PASS",
            evidence=[_relative(S0_PATH)],
        )
    except Exception as exc:
        raise D3CaseTransactionError(
            "S0 failed while committing its PASS ledger transition",
            evidence=[_relative(S0_PATH)],
        ) from exc
    print(json.dumps({"status": S0_PASS, "path": str(S0_PATH)}, ensure_ascii=False))
    return 0


def _run_one_case(case_id: str, identity: Mapping[str, Any]) -> dict[str, Any]:
    summary_path = _case_summary_path(case_id)
    if summary_path.exists():
        raise FileExistsError(summary_path)
    runtime: CaseRuntime | None = None
    recoverable_evidence: list[str] = []
    try:
        binding_start = execution_binding_audit()
        runtime = _prepare_runtime(case_id)
        initial_fingerprint = _initial_identity_record(
            runtime.grid,
            runtime.state,
            config_canonical_sha256=runtime.config_identity[
                "effective_canonical_sha256"
            ],
        )
        s0 = _json_object(S0_PATH)
        expected_initial = s0["initial_grid_mask_state_identity"]["cases"][case_id]
        initial_match = initial_fingerprint == expected_initial
        if not initial_match:
            raise D3ExecutionError(f"{case_id} initial state differs from S0")
        source_start = _capture_source(f"D3 {case_id} execution start")
        revision_start = _capture_revision(f"D3 {case_id} execution start")
        outcome = advance_runtime_to_registered_end(runtime)
        source_end = _capture_source(f"D3 {case_id} execution end")
        revision_end = _capture_revision(f"D3 {case_id} execution end")
        binding_end = execution_binding_audit()
        quality = _runtime_quality(runtime, completed=bool(outcome["completed"]))
        contract = _contract()
        source_hash = contract["formal_source_contract"]["required_tree_sha256"]
        revision = str(identity["execution_commit"])
        binding_stable = (
            binding_start["execution_commit"]
            == binding_end["execution_commit"]
            == revision
            and binding_start["implementation"] == binding_end["implementation"]
            and binding_start["authorization"]["observed"]
            == binding_end["authorization"]["observed"]
        )
        gates = {
            "run_completed": bool(outcome["completed"]),
            "all_numerical_quality_gates": quality["gate_pass"],
            "initial_grid_mask_state_matches_s0": initial_match,
            "formal_source_stable_and_bound": source_start.get("value")
            == source_end.get("value")
            == source_hash,
            "execution_revision_stable": revision_start.get("value")
            == revision_end.get("value")
            == revision,
            "authorization_and_implementation_binding_stable": binding_stable,
            "fresh_from_zero_without_checkpoint": True,
            "d2_dynamic_input_not_used": True,
        }
        summary: dict[str, Any] = {
            "schema_version": 2,
            "protocol_id": PROTOCOL_ID,
            "case_id": case_id,
            "stage": "S1" if case_id.startswith("G6_") else "S2",
            "status": CASE_PASS if all(gates.values()) else FAIL_CLOSED,
            "gate_pass": all(gates.values()),
            "gates": gates,
            "failure": outcome["failure"],
            "execution_commit": revision,
            "execution_identity": dict(identity),
            "execution_binding_start": binding_start,
            "execution_binding_end": binding_end,
            "config_identity": runtime.config_identity,
            "initial_identity": initial_fingerprint,
            "source_tree_execution_start": source_start,
            "source_tree_execution_end": source_end,
            "git_revision_execution_start": revision_start,
            "git_revision_execution_end": revision_end,
            "pressure_accelerator": runtime.pressure_accelerator,
            "quality": quality,
            "retry_attempts": outcome["retry_attempts"],
            "wall_time_s": outcome["wall_time_s"],
            "process_cpu_time_s": outcome["process_cpu_time_s"],
            "fresh_evidence": {
                "started_from_zero": True,
                "checkpoint_used": False,
                "d2_dynamic_input_used": False,
            },
        }
        if summary["gate_pass"]:
            summary["archive"] = _assemble_archive(
                runtime,
                summary_path.parent / "archive",
                source_start=source_start,
                source_end=source_end,
                revision_start=revision_start,
                revision_end=revision_end,
                process_cpu_time_s=float(outcome["process_cpu_time_s"]),
            )
        else:
            partial = _save_partial(summary_path.parent / "partial.npz", runtime)
            summary["partial_evidence"] = partial
            recoverable_evidence.append(str(partial["path"]))
        _write_json(summary_path, summary)
        recoverable_evidence.append(_relative(summary_path))
        return summary
    except Exception as exc:
        if runtime is not None:
            partial_path = summary_path.parent / "partial.npz"
            if not partial_path.exists():
                try:
                    partial = _save_partial(partial_path, runtime)
                    recoverable_evidence.append(str(partial["path"]))
                except Exception as partial_error:  # noqa: BLE001
                    print(
                        f"D3 partial evidence recovery failed for {case_id}: "
                        f"{type(partial_error).__name__}: {partial_error}",
                        file=sys.stderr,
                    )
        if summary_path.is_file() and not summary_path.is_symlink():
            recoverable_evidence.append(_relative(summary_path))
        archive_path = summary_path.parent / "archive"
        if archive_path.is_dir() and not archive_path.is_symlink():
            recoverable_evidence.append(_relative(archive_path))
        raise D3CaseTransactionError(
            f"{case_id} failed outside the normal case-summary transaction: "
            f"{type(exc).__name__}: {exc}",
            evidence=tuple(dict.fromkeys(recoverable_evidence)),
        ) from exc


def command_run_stage(stage: str) -> int:
    if stage not in STAGE_CASES:
        raise D3PreconditionError(f"unsupported D3 dynamic stage: {stage}")
    try:
        identity = execution_identity_audit(require_fresh=False)
        ledger = _json_object(STAGE_LEDGER_PATH)
        assert_stage_ready(
            stage,
            ledger,
            execution_commit=str(identity["execution_commit"]),
        )
        target_index = STAGE_ORDER.index(stage)
        for predecessor in STAGE_ORDER[:target_index]:
            for case_id in STAGE_CASES.get(predecessor, ()):
                _require_case_pass(case_id)
    except D3PreconditionError:
        raise
    except Exception as exc:
        raise D3PreconditionError(
            f"{stage} preconditions rejected before stage transaction: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    evidence: list[str] = []
    for case_id in STAGE_CASES[stage]:
        execution_identity_audit(require_fresh=False)
        try:
            summary = _run_one_case(case_id, identity)
        except D3CaseTransactionError as exc:
            exc.evidence = tuple(dict.fromkeys([*evidence, *exc.evidence]))
            raise
        evidence.append(_relative(_case_summary_path(case_id)))
        if not summary["gate_pass"]:
            failure = D3ExecutionError(
                f"{case_id} completed its evidence transaction but failed hard gates"
            )
            _record_dynamic_failure(
                stage,
                ["run-stage", "--stage", stage],
                failure,
                diagnostic_evidence=[
                    *evidence,
                    str(summary.get("partial_evidence", {}).get("path", "")),
                ],
            )
            return 2
    try:
        _replace_stage_entry(
            stage,
            disposition="EXECUTED",
            status="PASS",
            evidence=evidence,
        )
    except Exception as exc:
        raise D3CaseTransactionError(
            f"{stage} failed while committing its PASS ledger transition",
            evidence=evidence,
        ) from exc
    print(
        json.dumps(
            {"stage": stage, "status": "PASS", "evidence": evidence},
            ensure_ascii=False,
        )
    )
    return 0


def _record_dynamic_failure(
    stage: str,
    command: Sequence[str],
    exc: Exception,
    *,
    diagnostic_evidence: Sequence[str] = (),
) -> bool:
    """Converge every post-authorization exception on one failure transaction."""

    if stage not in STAGE_ORDER or not STAGE_LEDGER_PATH.is_file():
        return False
    ledger = _json_object(STAGE_LEDGER_PATH)
    if not validate_stage_ledger_semantics(ledger)["gate_pass"]:
        raise D3ExecutionError("cannot record failure against an invalid D3 ledger")
    entries = {
        str(item.get("stage")): item
        for item in ledger.get("stages", [])
        if isinstance(item, Mapping)
    }
    current = entries.get(stage, {})
    if current.get("disposition") == "FAILED_STOPPED":
        return validate_stage_ledger_evidence_files(ledger)["gate_pass"]
    if current.get("disposition") != "NOT_EXECUTED":
        raise D3ExecutionError(f"cannot fail D3 stage from current state: {stage}")
    candidate_ledger = _transition_stage_entry(
        ledger,
        stage,
        disposition="FAILED_STOPPED",
        status=FAIL_CLOSED,
        evidence=[_relative(FAILURE_PATH)],
    )
    if FAILURE_PATH.exists():
        raise D3ExecutionError(
            "D3 failure marker exists without the matching failed ledger state"
        )
    normalized_evidence: list[str] = []
    for value in dict.fromkeys(str(item) for item in diagnostic_evidence):
        if not value:
            continue
        path = REPOSITORY_ROOT / value
        resolved = path.resolve()
        if (
            path.exists()
            and not path.is_symlink()
            and (
                resolved == RAW_ROOT.resolve()
                or resolved.is_relative_to(RAW_ROOT.resolve())
            )
            and not _contains_forbidden_d2_reference(value)
        ):
            normalized_evidence.append(value)
    failure_record = {
        "schema_version": 2,
        "protocol_id": PROTOCOL_ID,
        "status": FAIL_CLOSED,
        "stage": stage,
        "command": list(command),
        "failure": {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
        },
        "diagnostic_evidence": normalized_evidence,
        "execution_commit": ledger.get("execution_commit"),
        "subsequent_stages_executed": False,
    }
    marker_written = False
    try:
        _write_json(FAILURE_PATH, failure_record)
        marker_written = True
        evidence_audit = validate_stage_ledger_evidence_files(candidate_ledger)
        if not evidence_audit["gate_pass"]:
            raise D3ExecutionError(
                f"D3 failure evidence audit failed before committing {stage}"
            )
        _write_json(STAGE_LEDGER_PATH, candidate_ledger)
    except Exception:
        if marker_written:
            FAILURE_PATH.unlink(missing_ok=True)
        raise
    return True


def _stage_for_dynamic_command(arguments: Sequence[str]) -> str | None:
    if arguments and arguments[0] == "prepare-execution":
        return "S0"
    if arguments and arguments[0] == "run-stage":
        try:
            return str(arguments[arguments.index("--stage") + 1])
        except (ValueError, IndexError):
            return None
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "generate-configs",
        help="write the six tracked configs without constructing or stepping HF3D",
    )
    static_parser = subparsers.add_parser(
        "static-audit",
        help="run the authorized non-stepping implementation audit",
    )
    static_parser.add_argument("--write", action="store_true")
    static_parser.add_argument("--require-clean", action="store_true")
    subparsers.add_parser(
        "prepare-execution",
        help="future-only S0; blocked until an exact-snapshot Pro authorization exists",
    )
    stage_parser = subparsers.add_parser(
        "run-stage",
        help="future-only real CUDA stage; blocked by the current contract",
    )
    stage_parser.add_argument("--stage", choices=("S1", "S2"), required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)
    try:
        if args.command == "generate-configs":
            report = generate_configs(write=True)
            print(
                json.dumps(
                    {
                        "status": "D3_GENERATED_CONFIGS_WRITTEN",
                        "case_count": len(report["cases"]),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "static-audit":
            report = static_implementation_audit(require_clean=args.require_clean)
            if args.write:
                _write_json(STATIC_REVIEW_PATH, report)
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "gate_pass": report["gate_pass"],
                        "time_steps_advanced": 0,
                        "path": str(STATIC_REVIEW_PATH) if args.write else None,
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if report["gate_pass"] else 2
        if args.command == "prepare-execution":
            return command_prepare_execution()
        if args.command == "run-stage":
            return command_run_stage(args.stage)
        raise AssertionError(args.command)
    except Exception as exc:
        if args.command in {"prepare-execution", "run-stage"} and not isinstance(
            exc, D3PreconditionError
        ):
            try:
                stage = _stage_for_dynamic_command(arguments)
                if stage is not None:
                    _record_dynamic_failure(
                        stage,
                        arguments,
                        exc,
                        diagnostic_evidence=getattr(exc, "evidence", ()),
                    )
            except Exception as write_error:  # noqa: BLE001
                print(
                    "D3 failure evidence could not be written: "
                    f"{type(write_error).__name__}: {write_error}",
                    file=sys.stderr,
                )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
