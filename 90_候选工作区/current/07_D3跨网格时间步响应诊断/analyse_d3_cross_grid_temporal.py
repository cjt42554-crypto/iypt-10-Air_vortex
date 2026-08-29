#!/usr/bin/env python3
"""Independently reload, audit, and classify future D3 HF3D archives.

This module deliberately does not import the D3 runner.  Configuration
reconstruction, archive hard gates, vector arithmetic, label precedence, and
provenance checks are recomputed from the preregistration and formal archive
reader.  The current contract blocks the ``final`` command until a later Pro
authorization and six fresh runs exist.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
PREREGISTRATION_PATH = HERE / "d3_cross_grid_temporal_preregistration.json"
RAW_ROOT = HERE / "artifacts" / "runs"
S0_PATH = RAW_ROOT / "s0_identity_config_fresh_evidence_gate.json"
RAW_LEDGER_PATH = RAW_ROOT / "stage_ledger.json"
FAILURE_PATH = RAW_ROOT / "execution_failure.json"
REPORT_JSON = HERE / "reports" / "d3_cross_grid_temporal_report.json"
REPORT_MD = HERE / "reports" / "d3_cross_grid_temporal_report.md"
ARTIFACT_MANIFEST = HERE / "artifacts" / "d3_cross_grid_temporal_manifest.json"
EVIDENCE_ROOT = HERE / "artifacts" / "evidence"
TRACKED_LEDGER = EVIDENCE_ROOT / "stage_ledger.json"

if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

from airvortex.backends.high_fidelity_3d import HighFidelityBackend
from airvortex.core import RunStatus
from airvortex.io import load_run_archive
from airvortex.io.archive import source_tree_identity
from airvortex.io.config_io import canonical_json_bytes, read_json_strict

PROTOCOL_ID = "D3_HF3D_CROSS_GRID_TEMPORAL_RESPONSE_V1"
FAIL_CLOSED = "D3_INCONCLUSIVE_FAIL_CLOSED"
EXPECTED_CASES = (
    "G6_DT0",
    "G6_DT1",
    "G6_DT2",
    "G9_DT0",
    "G9_DT1",
    "G9_DT2",
)
PRIMARY = (
    "funnel_depth_m",
    "kinetic_energy_lab_j",
    "angular_momentum_z_kg_m2_s",
    "axisymmetry_error",
)
SECONDARY = ("min_gap_to_bar_m", "liquid_inventory_change_relative")
BINARY_EVENTS = (
    "first_contact_proxy",
    "connection_candidate",
    "finite_channel_candidate",
)
OBSERVABLE_ALIAS = {
    "liquid_inventory_change_relative": "liquid_mass_error_relative",
}
EXPECTED_OUTPUT_TIME = np.asarray(
    [0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.009, 0.01],
    dtype=np.float64,
)
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


class D3AnalysisError(RuntimeError):
    """Raised when frozen D3 evidence cannot support classification."""


@dataclass(slots=True)
class RunEvidence:
    case_id: str
    archive_path: Path
    config: dict[str, Any]
    time_s: NDArray[np.float64]
    observables: dict[str, NDArray[np.float64]]
    fields: dict[str, NDArray[Any]]
    metadata: dict[str, Any]
    provenance: dict[str, Any]
    manifest: dict[str, Any]
    summary: dict[str, Any]
    hard_gate_audit: dict[str, Any]


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
        raise D3AnalysisError(f"JSON root must be an object: {path}")
    return value


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


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()


def _deep_set(root: dict[str, Any], dotted: str, value: Any) -> None:
    tokens = dotted.split(".")
    cursor = root
    for token in tokens[:-1]:
        child = cursor.get(token)
        if not isinstance(child, dict):
            raise D3AnalysisError(f"configuration path is missing: {dotted}")
        cursor = child
    if tokens[-1] not in cursor:
        raise D3AnalysisError(f"configuration path is missing: {dotted}")
    cursor[tokens[-1]] = value


def _contract() -> dict[str, Any]:
    contract = _json_object(PREREGISTRATION_PATH)
    if contract.get("protocol_id") != PROTOCOL_ID:
        raise D3AnalysisError("D3 protocol identity mismatch")
    return contract


def rms(values: NDArray[np.floating[Any]]) -> float:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size == 0 or not np.isfinite(array).all():
        raise D3AnalysisError("RMS input must be a nonempty finite vector")
    return float(math.sqrt(float(np.mean(array * array))))


def initial_removed(
    values: NDArray[np.floating[Any]],
) -> NDArray[np.float64]:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size == 0 or not np.isfinite(array).all():
        raise D3AnalysisError("observable trajectory must be nonempty and finite")
    return array - array[0]


def safe_cosine(
    first: NDArray[np.floating[Any]],
    second: NDArray[np.floating[Any]],
) -> float | None:
    left = np.asarray(first, dtype=np.float64).reshape(-1)
    right = np.asarray(second, dtype=np.float64).reshape(-1)
    if left.shape != right.shape or left.size == 0:
        raise D3AnalysisError("cosine vectors must be nonempty and shape-identical")
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise D3AnalysisError("cosine vectors must be finite")
    left_norm = float(np.linalg.norm(left))
    right_norm = float(np.linalg.norm(right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    value = float(np.dot(left, right) / (left_norm * right_norm))
    return float(np.clip(value, -1.0, 1.0))


def observed_order(
    gamma: float,
    *,
    first_increment_rms: float,
    first_increment_scale: float,
) -> float | None:
    if not all(
        math.isfinite(value)
        for value in (gamma, first_increment_rms, first_increment_scale)
    ):
        raise ValueError("observed-order inputs must be finite")
    if gamma <= 0.0 or first_increment_rms < first_increment_scale:
        return None
    return -math.log2(gamma)


def compute_temporal_response_metrics(
    trajectories: Mapping[str, NDArray[np.floating[Any]]],
    *,
    epsilon: float,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute all frozen q/s/u/c metrics for one observable and one window."""

    if set(trajectories) != set(EXPECTED_CASES):
        raise D3AnalysisError("D3 metric input must contain exactly the six cases")
    if not math.isfinite(epsilon) or epsilon <= 0.0:
        raise ValueError("epsilon must be finite and positive")
    contract_thresholds = (
        _contract()["thresholds"] if thresholds is None else thresholds
    )
    q = {case_id: initial_removed(trajectories[case_id]) for case_id in EXPECTED_CASES}
    sizes = {value.size for value in q.values()}
    if len(sizes) != 1:
        raise D3AnalysisError("D3 trajectories must have equal lengths")
    s0 = q["G9_DT0"] - q["G6_DT0"]
    s1 = q["G9_DT1"] - q["G6_DT1"]
    s2 = q["G9_DT2"] - q["G6_DT2"]
    u61 = q["G6_DT1"] - q["G6_DT0"]
    u62 = q["G6_DT2"] - q["G6_DT1"]
    u91 = q["G9_DT1"] - q["G9_DT0"]
    u92 = q["G9_DT2"] - q["G9_DT1"]
    c1 = s1 - s0
    c2 = s2 - s1
    vector_map = {
        "s_0": s0,
        "s_1": s1,
        "s_2": s2,
        "u_G6_1": u61,
        "u_G6_2": u62,
        "u_G9_1": u91,
        "u_G9_2": u92,
        "c_1": c1,
        "c_2": c2,
    }
    vector_rms = {name: rms(vector) for name, vector in vector_map.items()}
    e0 = vector_rms["s_0"]
    spatial_denominator = max(e0, epsilon)
    c1_denominator = max(vector_rms["c_1"], epsilon)
    u61_denominator = max(vector_rms["u_G6_1"], epsilon)
    u91_denominator = max(vector_rms["u_G9_1"], epsilon)
    eta_delta_1 = vector_rms["c_1"] / spatial_denominator
    rho_common_1 = vector_rms["s_1"] / spatial_denominator
    rho_common_2 = vector_rms["s_2"] / spatial_denominator
    gamma_g6 = vector_rms["u_G6_2"] / u61_denominator
    gamma_g9 = vector_rms["u_G9_2"] / u91_denominator
    gamma_delta = vector_rms["c_2"] / c1_denominator
    cosine_g6 = safe_cosine(u61, u62)
    cosine_g9 = safe_cosine(u91, u92)
    cosine_delta = safe_cosine(c1, c2)
    negligible_fraction = float(
        contract_thresholds["second_increment_negligible_fraction_of_first_max"]
    )
    below_spatial_fraction = float(
        contract_thresholds["increment_below_spatial_scale_fraction_max"]
    )
    direction_min = float(contract_thresholds["direction_cosine_min"])
    gamma_max = float(contract_thresholds["contraction_gamma_max"])

    increment_below_scale_g6 = (
        vector_rms["u_G6_1"] < below_spatial_fraction * spatial_denominator
        and vector_rms["u_G6_2"] < below_spatial_fraction * spatial_denominator
    )
    increment_below_scale_g9 = (
        vector_rms["u_G9_1"] < below_spatial_fraction * spatial_denominator
        and vector_rms["u_G9_2"] < below_spatial_fraction * spatial_denominator
    )
    direction_consistent_g6 = vector_rms["u_G6_2"] <= negligible_fraction * max(
        vector_rms["u_G6_1"], epsilon
    ) or (cosine_g6 is not None and cosine_g6 >= direction_min)
    direction_consistent_g9 = vector_rms["u_G9_2"] <= negligible_fraction * max(
        vector_rms["u_G9_1"], epsilon
    ) or (cosine_g9 is not None and cosine_g9 >= direction_min)
    direction_consistent_delta = vector_rms["c_2"] <= negligible_fraction * max(
        vector_rms["c_1"], epsilon
    ) or (cosine_delta is not None and cosine_delta >= direction_min)
    contracting_or_below_g6 = increment_below_scale_g6 or (
        gamma_g6 <= gamma_max and direction_consistent_g6
    )
    contracting_or_below_g9 = increment_below_scale_g9 or (
        gamma_g9 <= gamma_max and direction_consistent_g9
    )
    differential_contracting = gamma_delta <= gamma_max and direction_consistent_delta
    common_reduction_primary = (
        eta_delta_1
        >= float(
            contract_thresholds["material_differential_temporal_correction_eta_min"]
        )
        and rho_common_1 <= float(contract_thresholds["common_dt_reduction_rho_max"])
        and rho_common_2 <= float(contract_thresholds["common_dt_reduction_rho_max"])
    )
    positive_primary = (
        common_reduction_primary
        and differential_contracting
        and contracting_or_below_g6
        and contracting_or_below_g9
    )
    increase_primary = (
        eta_delta_1
        >= float(
            contract_thresholds["material_differential_temporal_correction_eta_min"]
        )
        and rho_common_1 >= float(contract_thresholds["common_dt_increase_rho_min"])
        and rho_common_2 >= float(contract_thresholds["common_dt_increase_rho_min"])
    )
    small_primary = (
        eta_delta_1
        < float(
            contract_thresholds[
                "small_differential_temporal_correction_eta_max_exclusive"
            ]
        )
        and float(contract_thresholds["common_dt_reduction_rho_max"])
        <= rho_common_1
        <= float(contract_thresholds["common_dt_increase_rho_min"])
        and float(contract_thresholds["common_dt_reduction_rho_max"])
        <= rho_common_2
        <= float(contract_thresholds["common_dt_increase_rho_min"])
    )
    first_scale = below_spatial_fraction * spatial_denominator
    return {
        "epsilon_Q": epsilon,
        "E_0": e0,
        "spatial_denominator": spatial_denominator,
        "eta_delta_1": eta_delta_1,
        "rho_common_1": rho_common_1,
        "rho_common_2": rho_common_2,
        "gamma_G6": gamma_g6,
        "gamma_G9": gamma_g9,
        "gamma_delta": gamma_delta,
        "cosine_G6": cosine_g6,
        "cosine_G9": cosine_g9,
        "cosine_delta": cosine_delta,
        "observed_order_G6": observed_order(
            gamma_g6,
            first_increment_rms=vector_rms["u_G6_1"],
            first_increment_scale=first_scale,
        ),
        "observed_order_G9": observed_order(
            gamma_g9,
            first_increment_rms=vector_rms["u_G9_1"],
            first_increment_scale=first_scale,
        ),
        "observed_order_delta": observed_order(
            gamma_delta,
            first_increment_rms=vector_rms["c_1"],
            first_increment_scale=first_scale,
        ),
        "booleans": {
            "increment_below_scale_G6": increment_below_scale_g6,
            "increment_below_scale_G9": increment_below_scale_g9,
            "direction_consistent_G6": direction_consistent_g6,
            "direction_consistent_G9": direction_consistent_g9,
            "direction_consistent_delta": direction_consistent_delta,
            "contracting_or_below_scale_G6": contracting_or_below_g6,
            "contracting_or_below_scale_G9": contracting_or_below_g9,
            "differential_contracting": differential_contracting,
            "common_reduction_primary": common_reduction_primary,
            "positive_primary": positive_primary,
            "increase_primary": increase_primary,
            "small_primary": small_primary,
        },
        "vector_rms": vector_rms,
        "vectors": {name: vector.tolist() for name, vector in vector_map.items()},
        "q": {case_id: value.tolist() for case_id, value in q.items()},
        "algebra_identity_residuals": {
            "c1_minus_u9_minus_u6_max_abs": float(np.max(np.abs(c1 - (u91 - u61)))),
            "c2_minus_u9_minus_u6_max_abs": float(np.max(np.abs(c2 - (u92 - u62)))),
        },
    }


def classify_top_level(
    primary_metrics: Mapping[str, Mapping[str, Any]],
    *,
    hard_gate_pass: bool,
    minimum_primary_count: int = 2,
) -> tuple[str, dict[str, Any]]:
    """Apply the frozen fail-closed label precedence exactly once."""

    if not hard_gate_pass:
        return FAIL_CLOSED, {
            "hard_gate_pass": False,
            "precedence_winner": FAIL_CLOSED,
        }
    if set(primary_metrics) != set(PRIMARY):
        raise D3AnalysisError(
            "top-level classification requires exactly 4 primary metrics"
        )
    counts = {
        name: sum(
            bool(metric.get("booleans", {}).get(name))
            for metric in primary_metrics.values()
        )
        for name in (
            "positive_primary",
            "common_reduction_primary",
            "increase_primary",
            "small_primary",
        )
    }
    if counts["positive_primary"] >= minimum_primary_count:
        label = "COMMON_DT_REDUCTION_WITH_CONTRACTING_TEMPORAL_RESPONSE"
    elif counts["common_reduction_primary"] >= minimum_primary_count:
        label = "COMMON_DT_REDUCTION_WITHOUT_REGISTERED_CONTRACTION"
    elif counts["increase_primary"] >= minimum_primary_count:
        label = "COMMON_DT_INCREASE_SIGNAL"
    elif counts["small_primary"] == len(PRIMARY):
        label = "DIFFERENTIAL_TEMPORAL_EFFECT_SMALL_RELATIVE_TO_SPATIAL_GAP"
    else:
        label = "MIXED_OR_INDETERMINATE_CROSS_GRID_TEMPORAL_RESPONSE"
    return label, {
        "hard_gate_pass": True,
        "counts": counts,
        "minimum_primary_count": minimum_primary_count,
        "precedence_winner": label,
    }


def _contains_forbidden_d2_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        # Required audit keys include ``checkpoint_used``; only values can
        # name a forbidden trajectory, path, or foreign protocol.
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


def audit_archive_input_path(
    path: Path, *, allowed_root: Path = RAW_ROOT
) -> dict[str, Any]:
    root = allowed_root.resolve()
    resolved = path.resolve()
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
        "within_d3_raw_root": resolved == root or resolved.is_relative_to(root),
        "no_symlink_component": not symlink_component,
        "no_d2_or_checkpoint_reference": not _contains_forbidden_d2_reference(
            path.as_posix()
        ),
    }
    return {"gate_pass": all(gates.values()), "gates": gates}


def _expected_config(case_id: str) -> dict[str, Any]:
    contract = _contract()
    records = {
        str(item.get("case_id")): item
        for item in contract["case_matrix"]
        if isinstance(item, Mapping)
    }
    if case_id not in records or case_id not in EXPECTED_CASES:
        raise D3AnalysisError(f"unknown D3 case: {case_id}")
    record = records[case_id]
    binding = contract["baseline_configs"][str(record["grid"])]
    baseline_path = REPOSITORY_ROOT / str(binding["path"])
    observed_blob = _git("rev-parse", f"HEAD:{binding['path']}")
    if observed_blob != binding["git_blob_sha"]:
        raise D3AnalysisError(f"baseline Git blob mismatch for {case_id}")
    result = deepcopy(_json_object(baseline_path))
    _deep_set(result, "run.duration_s", contract["frozen_run_fields"]["run.duration_s"])
    _deep_set(
        result,
        "run.output_interval_s",
        contract["frozen_run_fields"]["run.output_interval_s"],
    )
    _deep_set(result, "high_fidelity.time_step_s", float(record["time_step_s"]))
    return result


def audit_formal_g0_mask(
    rebuilt: NDArray[Any],
    archived: NDArray[Any],
) -> dict[str, Any]:
    left = np.ascontiguousarray(np.asarray(rebuilt, dtype=np.uint8))
    right_raw = np.ascontiguousarray(np.asarray(archived))
    binary = bool(np.all((right_raw == 0) | (right_raw == 1)))
    shape = left.shape == right_raw.shape
    right = right_raw.astype(np.uint8, copy=False)
    equal = bool(shape and binary and np.array_equal(left, right))
    gates = {
        "shape_matches": shape,
        "archived_mask_binary": binary,
        "elementwise_equal": equal,
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "rebuilt_sha256": _array_sha256(left),
        "archived_sha256": _array_sha256(right),
        "rebuilt_cell_count": int(np.sum(left)),
        "archived_cell_count": int(np.sum(right)),
    }


def evaluate_numerical_quality_hard_gates(
    metrics: Mapping[str, Any],
    *,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate every registered per-run numerical quality hard gate."""

    limits = (
        _contract()["numerical_quality_hard_gates"]
        if thresholds is None
        else thresholds
    )
    required = {
        "complete_to_registered_end_time",
        "all_state_and_diagnostic_values_finite",
        "maximum_pressure_relative_residual",
        "maximum_projected_divergence_s_inv",
        "maximum_accepted_advective_cfl",
        "cumulative_phase_clip_relative",
        "cumulative_phase_repair_relative",
        "maximum_liquid_volume_balance_error_relative",
        "configured_time_step_active_fraction",
        "maximum_cfl_step_retries",
        "actual_cuda_without_fallback",
        "native_output_times_exact",
    }
    if set(metrics) != required:
        raise D3AnalysisError(
            "numerical hard-gate metric set mismatch: "
            + json.dumps(sorted(set(metrics) ^ required))
        )

    def finite_number(name: str) -> float:
        try:
            value = float(metrics[name])
        except (TypeError, ValueError):
            return math.nan
        return value

    pressure = finite_number("maximum_pressure_relative_residual")
    divergence = finite_number("maximum_projected_divergence_s_inv")
    cfl = finite_number("maximum_accepted_advective_cfl")
    clip = finite_number("cumulative_phase_clip_relative")
    repair = finite_number("cumulative_phase_repair_relative")
    balance = finite_number("maximum_liquid_volume_balance_error_relative")
    active_fraction = finite_number("configured_time_step_active_fraction")
    retries = finite_number("maximum_cfl_step_retries")
    gates = {
        "complete_to_registered_end_time": metrics["complete_to_registered_end_time"]
        is True,
        "all_state_and_diagnostic_values_finite": metrics[
            "all_state_and_diagnostic_values_finite"
        ]
        is True,
        "pressure_relative_residual": math.isfinite(pressure)
        and pressure <= float(limits["pressure_relative_residual_max"]),
        "projected_divergence": math.isfinite(divergence)
        and divergence <= float(limits["projected_divergence_max_s_inv"]),
        "accepted_advective_cfl": math.isfinite(cfl)
        and cfl <= float(limits["accepted_advective_cfl_max"]) * (1.0 + 1.0e-12),
        "cumulative_phase_clip_relative": math.isfinite(clip)
        and clip <= float(limits["cumulative_phase_clip_relative_max"]),
        "cumulative_phase_repair_relative": math.isfinite(repair)
        and repair <= float(limits["cumulative_phase_repair_relative_max"]),
        "liquid_volume_balance_error_relative": math.isfinite(balance)
        and balance <= float(limits["liquid_volume_balance_error_relative_max"]),
        "configured_time_step_active_fraction": math.isfinite(active_fraction)
        and active_fraction
        >= float(limits["configured_time_step_active_fraction_min"]),
        "maximum_cfl_step_retries": math.isfinite(retries)
        and retries <= float(limits["maximum_cfl_step_retries"]),
        "actual_cuda_without_fallback": metrics["actual_cuda_without_fallback"] is True,
        "native_output_times_exact": metrics["native_output_times_exact"] is True,
    }
    return {"gate_pass": all(gates.values()), "gates": gates}


def _case_summary_path(case_id: str) -> Path:
    return RAW_ROOT / "cases" / case_id / "case_summary.json"


def _provenance_revision_audit(
    provenance: Mapping[str, Any],
    *,
    execution_commit: str,
    source_sha256: str,
) -> dict[str, Any]:
    identity = provenance.get("source_identity")
    disk = identity.get("disk_content_tree") if isinstance(identity, Mapping) else None
    revision = identity.get("git_revision") if isinstance(identity, Mapping) else None
    disk_start = disk.get("execution_start") if isinstance(disk, Mapping) else None
    disk_end = disk.get("execution_end") if isinstance(disk, Mapping) else None
    revision_start = (
        revision.get("execution_start") if isinstance(revision, Mapping) else None
    )
    revision_end = (
        revision.get("execution_end") if isinstance(revision, Mapping) else None
    )
    extra = provenance.get("extra")
    invocation = provenance.get("invocation")
    gates = {
        "source_start_matches": isinstance(disk_start, Mapping)
        and disk_start.get("value") == source_sha256,
        "source_end_matches": isinstance(disk_end, Mapping)
        and disk_end.get("value") == source_sha256,
        "source_boundary_match": isinstance(disk, Mapping)
        and disk.get("boundary_match") is True,
        "revision_start_matches": isinstance(revision_start, Mapping)
        and revision_start.get("value") == execution_commit,
        "revision_end_matches": isinstance(revision_end, Mapping)
        and revision_end.get("value") == execution_commit,
        "revision_boundary_match": isinstance(revision, Mapping)
        and revision.get("boundary_match") is True,
        "protocol_exact": isinstance(extra, Mapping)
        and extra.get("candidate_protocol_id") == PROTOCOL_ID,
        "fresh_from_zero": isinstance(extra, Mapping)
        and extra.get("fresh_from_zero") is True,
        "checkpoint_not_used": isinstance(extra, Mapping)
        and extra.get("checkpoint_used") is False,
        "d2_dynamic_input_not_used": isinstance(extra, Mapping)
        and extra.get("d2_dynamic_input_used") is False,
        "runner_entry_point_exact": isinstance(invocation, Mapping)
        and invocation.get("entry_point") == "run_d3_cross_grid_temporal.py",
        "no_forbidden_reference": not _contains_forbidden_d2_reference(provenance),
    }
    return {"gate_pass": all(gates.values()), "gates": gates}


def _archive_quality_audit(
    *,
    case_id: str,
    archived: Any,
    expected_config: Mapping[str, Any],
    execution_commit: str,
) -> dict[str, Any]:
    contract = _contract()
    config = archived.config.to_dict()
    result = archived.result
    fields_map = result.fields
    observables_map = result.observables
    required_fields = (
        "diagnostic_step_time_s",
        "diagnostic_dt_s",
        "diagnostic_pressure_relative_residual",
        "diagnostic_projected_divergence_s_inv",
        "diagnostic_post_projection_advective_cfl",
        "diagnostic_phase_clip_absolute_volume_m3",
        "diagnostic_phase_repair_absolute_volume_m3",
        "diagnostic_cfl_retry_count",
        "stir_bar_mask_3d",
    )
    missing_fields = [name for name in required_fields if name not in fields_map]
    if missing_fields:
        raise D3AnalysisError(f"{case_id} archive fields missing: {missing_fields}")
    required_observables = tuple(
        OBSERVABLE_ALIAS.get(name, name) for name in (*PRIMARY, *SECONDARY)
    )
    required_observables += BINARY_EVENTS + ("liquid_volume_balance_error_relative",)
    missing_observables = [
        name for name in required_observables if name not in observables_map
    ]
    if missing_observables:
        raise D3AnalysisError(
            f"{case_id} archive observables missing: {missing_observables}"
        )
    arrays = {
        name: np.asarray(fields_map[name], dtype=np.float64).reshape(-1)
        for name in required_fields[:-1]
    }
    balance = np.asarray(
        observables_map["liquid_volume_balance_error_relative"],
        dtype=np.float64,
    ).reshape(-1)
    backend = HighFidelityBackend()
    rebuilt_grid = backend.make_grid(archived.config)
    initial_volume = backend._initial_liquid_volume(archived.config, rebuilt_grid)
    formal_g0 = audit_formal_g0_mask(
        rebuilt_grid.bar_mask,
        fields_map["stir_bar_mask_3d"],
    )
    del rebuilt_grid
    gc.collect()
    dt = arrays["diagnostic_dt_s"]
    configured_dt = float(config["high_fidelity"]["time_step_s"])
    tolerance = max(1.0e-15, configured_dt * 1.0e-9)
    active_fraction = (
        float(np.mean(np.abs(dt - configured_dt) <= tolerance)) if dt.size else 0.0
    )
    cumulative_clip = float(
        np.sum(arrays["diagnostic_phase_clip_absolute_volume_m3"]) / initial_volume
    )
    cumulative_repair = float(
        np.sum(arrays["diagnostic_phase_repair_absolute_volume_m3"]) / initial_volume
    )
    metadata = result.metadata
    diagnostics_metadata = metadata.get("diagnostics")
    accelerator = (
        diagnostics_metadata.get("pressure_accelerator")
        if isinstance(diagnostics_metadata, Mapping)
        else None
    )
    source_sha256 = contract["formal_source_contract"]["required_tree_sha256"]
    provenance_audit = _provenance_revision_audit(
        archived.provenance,
        execution_commit=execution_commit,
        source_sha256=source_sha256,
    )
    all_arrays = [
        np.asarray(value)
        for value in (*fields_map.values(), *observables_map.values(), result.time_s)
    ]

    def finite_max(values: NDArray[np.float64], *, absolute: bool = False) -> float:
        if not values.size:
            return math.inf
        source_values = np.abs(values) if absolute else values
        return float(np.max(source_values))

    numerical_metrics = {
        "complete_to_registered_end_time": result.status is RunStatus.COMPLETED,
        "all_state_and_diagnostic_values_finite": all(
            np.isfinite(value).all() for value in all_arrays
        ),
        "maximum_pressure_relative_residual": finite_max(
            arrays["diagnostic_pressure_relative_residual"]
        ),
        "maximum_projected_divergence_s_inv": finite_max(
            arrays["diagnostic_projected_divergence_s_inv"]
        ),
        "maximum_accepted_advective_cfl": finite_max(
            arrays["diagnostic_post_projection_advective_cfl"]
        ),
        "cumulative_phase_clip_relative": cumulative_clip,
        "cumulative_phase_repair_relative": cumulative_repair,
        "maximum_liquid_volume_balance_error_relative": finite_max(
            balance, absolute=True
        ),
        "configured_time_step_active_fraction": active_fraction,
        "maximum_cfl_step_retries": finite_max(arrays["diagnostic_cfl_retry_count"]),
        "actual_cuda_without_fallback": isinstance(accelerator, Mapping)
        and accelerator.get("selected") == "cuda"
        and accelerator.get("fallback") is False,
        "native_output_times_exact": bool(
            np.array_equal(
                np.asarray(result.time_s, dtype=np.float64), EXPECTED_OUTPUT_TIME
            )
        ),
    }
    numerical_audit = evaluate_numerical_quality_hard_gates(
        numerical_metrics,
        thresholds=contract["numerical_quality_hard_gates"],
    )
    gates = {
        **numerical_audit["gates"],
        "configuration_exact": config == expected_config,
        "formal_g0_mask_rebuilt_equal": formal_g0["gate_pass"],
        "provenance_revision_and_freshness": provenance_audit["gate_pass"],
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "metrics": {
            **numerical_metrics,
            "accepted_step_count": int(dt.size),
            "accepted_time_sha256": _array_sha256(
                np.asarray(fields_map["diagnostic_step_time_s"], dtype=np.float64)
            ),
        },
        "formal_g0_geometry": formal_g0,
        "provenance_audit": provenance_audit,
        "numerical_quality_audit": numerical_audit,
    }


def _load_case(case_id: str, *, execution_commit: str) -> RunEvidence:
    summary_path = _case_summary_path(case_id)
    if not summary_path.is_file() or summary_path.is_symlink():
        raise D3AnalysisError(f"D3 case summary is missing or symlinked: {case_id}")
    summary = _json_object(summary_path)
    archive_record = summary.get("archive")
    if not isinstance(archive_record, Mapping) or not isinstance(
        archive_record.get("path"), str
    ):
        raise D3AnalysisError(f"D3 passing archive path is missing: {case_id}")
    archive_path = REPOSITORY_ROOT / str(archive_record["path"])
    path_audit = audit_archive_input_path(archive_path)
    if not path_audit["gate_pass"]:
        raise D3AnalysisError(f"inadmissible D3 archive input path: {archive_path}")
    archived = load_run_archive(archive_path, verify=True)
    expected_config = _expected_config(case_id)
    hard_gate_audit = _archive_quality_audit(
        case_id=case_id,
        archived=archived,
        expected_config=expected_config,
        execution_commit=execution_commit,
    )
    summary_gates = {
        "summary_protocol": summary.get("protocol_id") == PROTOCOL_ID,
        "summary_case": summary.get("case_id") == case_id,
        "summary_gate_pass": summary.get("gate_pass") is True,
        "summary_execution_commit": summary.get("execution_commit") == execution_commit,
        "independent_archive_hard_gates": hard_gate_audit["gate_pass"],
        "archive_path_gate": path_audit["gate_pass"],
    }
    if not all(summary_gates.values()):
        failed = [name for name, passed in summary_gates.items() if not passed]
        raise D3AnalysisError(f"{case_id} summary/archive gates failed: {failed}")
    observables: dict[str, NDArray[np.float64]] = {}
    for name in (*PRIMARY, *SECONDARY, *BINARY_EVENTS):
        archive_name = OBSERVABLE_ALIAS.get(name, name)
        value = np.asarray(
            archived.result.observables[archive_name], dtype=np.float64
        ).reshape(-1)
        if value.size != EXPECTED_OUTPUT_TIME.size or not np.isfinite(value).all():
            raise D3AnalysisError(f"{case_id} observable is invalid: {name}")
        observables[name] = value.copy()
    return RunEvidence(
        case_id=case_id,
        archive_path=archive_path,
        config=archived.config.to_dict(),
        time_s=np.asarray(archived.result.time_s, dtype=np.float64).copy(),
        observables=observables,
        fields={
            name: np.asarray(value).copy()
            for name, value in archived.result.fields.items()
        },
        metadata=dict(archived.result.metadata),
        provenance=dict(archived.provenance),
        manifest=dict(archived.manifest),
        summary=summary,
        hard_gate_audit={
            **hard_gate_audit,
            "summary_gates": summary_gates,
            "path_audit": path_audit,
        },
    )


def _window_indices(start: float, end: float) -> NDArray[np.int64]:
    tolerance = 256.0 * np.finfo(float).eps * max(end, 1.0)
    indices = np.flatnonzero(
        (EXPECTED_OUTPUT_TIME >= start - tolerance)
        & (EXPECTED_OUTPUT_TIME <= end + tolerance)
    )
    if indices.size < 2:
        raise D3AnalysisError("registered D3 window has fewer than two native frames")
    return indices


def _all_observable_metrics(
    runs: Mapping[str, RunEvidence],
    *,
    indices: NDArray[np.int64],
) -> dict[str, Any]:
    contract = _contract()
    floors = contract["absolute_scale_floors"]
    thresholds = contract["thresholds"]
    return {
        observable: compute_temporal_response_metrics(
            {
                case_id: runs[case_id].observables[observable][indices]
                for case_id in EXPECTED_CASES
            },
            epsilon=float(floors[observable]),
            thresholds=thresholds,
        )
        for observable in (*PRIMARY, *SECONDARY)
    }


def _binary_event_audit(runs: Mapping[str, RunEvidence]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for case_id in EXPECTED_CASES:
        result[case_id] = {}
        for name in BINARY_EVENTS:
            values = runs[case_id].observables[name]
            triggered_indices = np.flatnonzero(values > 0.5)
            result[case_id][name] = {
                "triggered": bool(triggered_indices.size),
                "first_trigger_time_s": float(
                    runs[case_id].time_s[triggered_indices[0]]
                )
                if triggered_indices.size
                else None,
                "trajectory": values.tolist(),
                "classification_role": "RECORD_ONLY_NOT_A_PASS_GATE",
            }
    return result


def _same_grid_archive_identity(runs: Mapping[str, RunEvidence]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for grid_name, case_ids in {
        "G6": EXPECTED_CASES[:3],
        "G9": EXPECTED_CASES[3:],
    }.items():
        mask_hashes = {
            case_id: _array_sha256(
                np.asarray(runs[case_id].fields["stir_bar_mask_3d"], dtype=np.uint8)
            )
            for case_id in case_ids
        }
        shape_dtypes = {
            case_id: (
                str(runs[case_id].fields["stir_bar_mask_3d"].dtype),
                runs[case_id].fields["stir_bar_mask_3d"].shape,
            )
            for case_id in case_ids
        }
        gates = {
            "archived_formal_g0_masks_identical": len(set(mask_hashes.values())) == 1,
            "archived_mask_dtype_shape_identical": len(set(shape_dtypes.values())) == 1,
            "s0_initial_fingerprints_match_case_summaries": all(
                runs[case_id]
                .summary.get("gates", {})
                .get("initial_grid_mask_state_matches_s0")
                is True
                for case_id in case_ids
            ),
        }
        groups[grid_name] = {
            "gate_pass": all(gates.values()),
            "gates": gates,
            "mask_sha256": mask_hashes,
        }
    return {
        "gate_pass": all(item["gate_pass"] for item in groups.values()),
        "groups": groups,
    }


def _validate_preanalysis_ledger(
    ledger: Mapping[str, Any],
    *,
    execution_commit: str,
) -> dict[str, Any]:
    stages = ledger.get("stages")
    per_stage: dict[str, Any] = {}
    if isinstance(stages, list):
        for item in stages:
            if not isinstance(item, Mapping):
                continue
            stage = str(item.get("stage"))
            expected_cases = {
                "S0": [],
                "S1": list(EXPECTED_CASES[:3]),
                "S2": list(EXPECTED_CASES[3:]),
                "S3": [],
            }.get(stage)
            gates = {
                "known_stage": stage in STAGE_ACTIONS,
                "action_exact": item.get("action") == STAGE_ACTIONS.get(stage),
                "description_exact": item.get("human_description")
                == STAGE_DESCRIPTIONS.get(stage),
                "cases_exact": item.get("cases") == expected_cases,
            }
            if stage in {"S0", "S1", "S2"}:
                gates["prerequisite_pass"] = item.get("status") == "PASS"
            if stage == "S3":
                gates["not_yet_executed"] = item.get("disposition") == "NOT_EXECUTED"
                gates["no_premature_evidence"] = item.get("evidence") == []
            per_stage[stage] = {"gate_pass": all(gates.values()), "gates": gates}
    gates = {
        "protocol_id": ledger.get("protocol_id") == PROTOCOL_ID,
        "execution_commit": ledger.get("execution_commit") == execution_commit,
        "exact_stage_set": set(per_stage) == set(STAGE_ACTIONS),
        "all_stage_semantics": set(per_stage) == set(STAGE_ACTIONS)
        and all(item["gate_pass"] for item in per_stage.values()),
        "automatic_follow_on_prohibited": ledger.get("automatic_follow_on")
        == "PROHIBITED",
    }
    return {"gate_pass": all(gates.values()), "gates": gates, "stages": per_stage}


def _final_stage_ledger(
    ledger: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    result = deepcopy(dict(ledger))
    stages = result.get("stages")
    if not isinstance(stages, list):
        raise D3AnalysisError("D3 stage ledger is malformed")
    replacements = 0
    for index, item in enumerate(stages):
        if isinstance(item, Mapping) and item.get("stage") == "S3":
            stages[index] = {
                "stage": "S3",
                "action": STAGE_ACTIONS["S3"],
                "human_description": STAGE_DESCRIPTIONS["S3"],
                "disposition": "EXECUTED",
                "status": "PASS",
                "cases": [],
                "classification": label,
                "evidence": [
                    _relative(REPORT_JSON),
                    _relative(REPORT_MD),
                    _relative(ARTIFACT_MANIFEST),
                    _relative(TRACKED_LEDGER),
                ],
            }
            replacements += 1
    if replacements != 1:
        raise D3AnalysisError("D3 ledger must have exactly one S3 entry")
    result["failed_stages"] = []
    result["unexecuted_stages"] = []
    result["automatic_follow_on"] = "PROHIBITED"
    return result


def _copy_compact_file(source: Path, target: Path) -> dict[str, Any]:
    if not source.is_file() or source.is_symlink():
        raise D3AnalysisError(f"compact evidence source is invalid: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    shutil.copyfile(source, target)
    if _sha256(source) != _sha256(target):
        raise D3AnalysisError(f"compact evidence copy changed bytes: {source}")
    return {
        "source": _relative(source),
        "tracked_path": _relative(target),
        "sha256": _sha256(target),
        "size_bytes": target.stat().st_size,
    }


def _publish_compact_evidence(
    runs: Mapping[str, RunEvidence],
    final_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    if EVIDENCE_ROOT.exists():
        raise FileExistsError(EVIDENCE_ROOT)
    copied: list[dict[str, Any]] = []
    copied.append(
        _copy_compact_file(
            S0_PATH,
            EVIDENCE_ROOT / "s0_identity_config_fresh_evidence_gate.json",
        )
    )
    for case_id in EXPECTED_CASES:
        case_root = EVIDENCE_ROOT / "cases" / case_id
        copied.append(
            _copy_compact_file(
                _case_summary_path(case_id), case_root / "case_summary.json"
            )
        )
        for name in (
            "config.json",
            "result.json",
            "provenance.json",
            "manifest.json",
            "timeseries.csv",
        ):
            copied.append(
                _copy_compact_file(
                    runs[case_id].archive_path / name,
                    case_root / name,
                )
            )
    _write_json(TRACKED_LEDGER, final_ledger)
    copied.append(
        {
            "source": "generated_by_independent_D3_analyser",
            "tracked_path": _relative(TRACKED_LEDGER),
            "sha256": _sha256(TRACKED_LEDGER),
            "size_bytes": TRACKED_LEDGER.stat().st_size,
        }
    )
    return {
        "file_count": len(copied),
        "total_size_bytes": sum(item["size_bytes"] for item in copied),
        "files": copied,
    }


def _raw_manifest(compact: Mapping[str, Any]) -> dict[str, Any]:
    files = [
        {
            "path": _relative(path),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "tracked": False,
        }
        for path in sorted(item for item in RAW_ROOT.rglob("*") if item.is_file())
    ]
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "integrity_scope": (
            "Ignored fresh D3 raw products and tracked compact evidence present "
            "at independent final analysis."
        ),
        "raw_file_count": len(files),
        "raw_total_size_bytes": sum(item["size_bytes"] for item in files),
        "raw_files": files,
        "compact_tracked_evidence": dict(compact),
        "authenticity_limit": (
            "SHA-256 detects later mutation against this manifest but is not an "
            "external signature or independent rerun."
        ),
    }


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# D3 跨网格时间步响应诊断报告",
        "",
        "## 分类",
        "",
        "~~~text",
        str(report["status"]),
        "~~~",
        "",
        f"执行提交：{report['identity']['execution_commit']}。",
        "",
        "D3 只覆盖正式 G0、6/9 格、0–0.010 s 和三个登记共同时间步。",
        "",
        "## 主要观测量",
        "",
        "| 观测量 | eta_delta_1 | rho_common_1 | rho_common_2 | gamma_G6 | gamma_G9 | gamma_delta |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for observable in PRIMARY:
        metric = report["full_window_metrics"][observable]
        lines.append(
            f"| {observable} | {metric['eta_delta_1']:.6g} | "
            f"{metric['rho_common_1']:.6g} | {metric['rho_common_2']:.6g} | "
            f"{metric['gamma_G6']:.6g} | {metric['gamma_G9']:.6g} | "
            f"{metric['gamma_delta']:.6g} |"
        )
    lines.extend(
        [
            "",
            "余弦和 observed order 仅按登记尺度门解释；零范数时余弦记为未定义，不以任意数值替代。",
            "",
            "## 硬门与全新证据",
            "",
            "| 算例 | 独立归档硬门 |",
            "|---|---|",
        ]
    )
    for case_id in EXPECTED_CASES:
        audit = report["hard_gate_audits"][case_id]
        lines.append(f"| {case_id} | {'PASS' if audit['gate_pass'] else 'FAIL'} |")
    lines.extend(
        [
            "",
            "六个算例均要求从零开始、同一清洁执行提交、正式 G0、真实 CUDA，且不得使用 checkpoint 或 D2 动态输出。",
            "",
            "## 结论上限",
            "",
            "- D3 不撤销 Q1R3 空间阶段失败；",
            "- 即使共同时间步缩小并出现收缩响应，也不等于严格时间或空间收敛；",
            "- 数值资格仍未证明；",
            "- 未执行物理验证或实验验证；",
            "- 不允许装置预测；",
            "- 不自动进入更细网格、更小时间步、Q1R4 或 D4；",
            "- 报告后停止等待 Pro 复核。",
            "",
            "## 证据入口",
            "",
            f"跟踪清单：{_relative(ARTIFACT_MANIFEST)}。",
            "",
            f"阶段台账：{_relative(TRACKED_LEDGER)}。",
            "",
        ]
    )
    return "\n".join(lines)


def analyse_final() -> dict[str, Any]:
    """Run future-only S3 independent analysis and publish tracked evidence."""

    contract = _contract()
    authorization = contract.get("authorization")
    if (
        not isinstance(authorization, Mapping)
        or authorization.get("real_cuda_dynamic_execution") is not True
    ):
        raise D3AnalysisError(
            "D3 final analysis is blocked by the current authorization"
        )
    if authorization.get("execution_requires_new_pro_review") is not False:
        raise D3AnalysisError("D3 still requires a new Pro execution review")
    if any(
        path.exists()
        for path in (REPORT_JSON, REPORT_MD, ARTIFACT_MANIFEST, EVIDENCE_ROOT)
    ):
        raise FileExistsError("D3 final tracked output already exists")
    if FAILURE_PATH.exists():
        raise D3AnalysisError("D3 execution failure marker exists")
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    source = dict(source_tree_identity(FORMAL_ROOT))
    bound_source = contract["formal_source_contract"]["required_tree_sha256"]
    if branch != "main" or porcelain:
        raise D3AnalysisError("D3 final analysis requires a clean main worktree")
    if source.get("value") != bound_source:
        raise D3AnalysisError("formal source tree no longer matches the D3 binding")
    s0 = _json_object(S0_PATH)
    if (
        s0.get("gate_pass") is not True
        or s0.get("execution_commit") != head
        or s0.get("fresh_evidence_policy", {}).get("time_steps_advanced") != 0
    ):
        raise D3AnalysisError("D3 S0 identity/fresh-evidence gate is invalid")
    raw_ledger = _json_object(RAW_LEDGER_PATH)
    ledger_audit = _validate_preanalysis_ledger(raw_ledger, execution_commit=head)
    if not ledger_audit["gate_pass"]:
        raise D3AnalysisError("D3 preanalysis stage ledger semantic audit failed")

    runs = {
        case_id: _load_case(case_id, execution_commit=head)
        for case_id in EXPECTED_CASES
    }
    hard_gate_audits = {
        case_id: runs[case_id].hard_gate_audit for case_id in EXPECTED_CASES
    }
    hard_gate_pass = all(item["gate_pass"] for item in hard_gate_audits.values())
    same_grid_identity = _same_grid_archive_identity(runs)
    hard_gate_pass = hard_gate_pass and same_grid_identity["gate_pass"]
    full_indices = np.arange(EXPECTED_OUTPUT_TIME.size, dtype=np.int64)
    full_metrics = _all_observable_metrics(runs, indices=full_indices)
    label, classification = classify_top_level(
        {name: full_metrics[name] for name in PRIMARY},
        hard_gate_pass=hard_gate_pass,
        minimum_primary_count=int(contract["thresholds"]["minimum_primary_count"]),
    )
    diagnostic_windows: dict[str, Any] = {}
    for bounds in contract["comparison_policy"]["diagnostic_windows_s"]:
        start, end = float(bounds[0]), float(bounds[1])
        name = f"{start:.3f}_{end:.3f}_s"
        diagnostic_windows[name] = {
            "bounds_s": [start, end],
            "metrics": _all_observable_metrics(
                runs,
                indices=_window_indices(start, end),
            ),
            "can_change_top_level_label": False,
        }
    binary_events = _binary_event_audit(runs)
    final_ledger = _final_stage_ledger(raw_ledger, label=label)
    compact = _publish_compact_evidence(runs, final_ledger)
    _write_json(RAW_LEDGER_PATH, final_ledger)
    manifest = _raw_manifest(compact)
    report: dict[str, Any] = {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "status": label,
        "gate_pass": label != FAIL_CLOSED,
        "classification": classification,
        "full_window_metrics": full_metrics,
        "diagnostic_windows": diagnostic_windows,
        "binary_events": binary_events,
        "hard_gate_audits": hard_gate_audits,
        "same_grid_archive_identity": same_grid_identity,
        "identity": {
            "branch": branch,
            "execution_commit": head,
            "worktree_porcelain_at_analysis_start": porcelain,
            "formal_source_tree": source,
            "bound_formal_source_tree_sha256": bound_source,
            "contract_path": _relative(PREREGISTRATION_PATH),
            "contract_content_sha256": _sha256(PREREGISTRATION_PATH),
        },
        "stage_ledger": final_ledger,
        "artifact_manifest": {
            "path": _relative(ARTIFACT_MANIFEST),
            "raw_file_count": manifest["raw_file_count"],
            "raw_total_size_bytes": manifest["raw_total_size_bytes"],
            "compact_tracked_file_count": compact["file_count"],
            "compact_tracked_total_size_bytes": compact["total_size_bytes"],
        },
        "claim_limits": contract["claim_limits"],
        "conclusion_ceiling": {
            "q1r3_failure_overturned": False,
            "strict_temporal_convergence_established": False,
            "spatial_convergence_established": False,
            "numerical_qualification_established": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "formal_solver_changed": False,
            "apparatus_prediction_allowed": False,
        },
        "stop_point": "REPORT_THEN_STOP_FOR_PRO_REVIEW",
    }
    _write_json(ARTIFACT_MANIFEST, manifest)
    _write_json(REPORT_JSON, report)
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(_markdown(report), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "final",
        help="future-only S3; blocked until dynamic authorization and six fresh runs",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "final":
        raise AssertionError(args.command)
    report = analyse_final()
    print(
        json.dumps(
            {"status": report["status"], "report": str(REPORT_JSON)},
            ensure_ascii=False,
        )
    )
    return 0 if report["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
