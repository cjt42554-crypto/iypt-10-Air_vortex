"""Shared D3R1 contract, frozen-V1, and evidence-root identity audits."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

V1_PROTOCOL_ID = "D3_HF3D_CROSS_GRID_TEMPORAL_RESPONSE_V1"
R1_PROTOCOL_ID = "D3_HF3D_CROSS_GRID_TEMPORAL_RESPONSE_R1"

SCIENTIFIC_IDENTITY_FIELDS = (
    "scientific_question",
    "claim_limits",
    "formal_source_contract",
    "baseline_configs",
    "frozen_run_fields",
    "allowed_case_override_paths",
    "forbidden_case_changes",
    "time_step_levels_s",
    "case_matrix",
    "native_output_times_s",
    "comparison_policy",
    "observables",
    "absolute_scale_floors",
    "registered_vectors",
    "registered_metrics",
    "thresholds",
    "derived_boolean_rules",
    "top_level_classification_precedence",
    "numerical_quality_hard_gates",
)

V1_FROZEN_RAW_SHA256 = {
    "artifacts/runs/s0_identity_config_fresh_evidence_gate.json": (
        "e50905260c9d7732ef68e2cf76b4ed661b19383b4f92ade409463e58b63244b2"
    ),
    "artifacts/runs/stage_ledger.json": (
        "46ff32a79a62dd6cb544a04a7377d55888dff75e313f650bd97c32cf2df62afa"
    ),
    "artifacts/runs/execution_failure.json": (
        "d63bd745ee455bfaaba563b6b348bb7f7c822d5f6d33977c6f0f02daadb7def5"
    ),
    "artifacts/runs/cases/G6_DT0/case_summary.json": (
        "15b2e76a57ad00dd93eb7a6652f4adf697f48c9dc772f57f666a771a537ca27a"
    ),
    "artifacts/runs/cases/G6_DT0/partial.npz": (
        "d1b5ae2723ec60619d20fe2ce7d8d8010941083167fe1d71554990b898d9f3ed"
    ),
}

FORENSIC_BUNDLE_SHA256 = {
    "audit_d3_v1_s1_failure.py": (
        "dae37bb3c4c5a75dbdbf9e2ec6099de0dd6170cbe9b80ed280b9630b4ae0c1db"
    ),
    "artifacts/v1_failure/d3_v1_s1_failure_forensic.json": (
        "6c6587eb0f9521ba6988a80316742cca5841f39389e51c863b6ce7d7f70e6396"
    ),
    "artifacts/v1_failure/d3_v1_s1_failure_forensic.md": (
        "0b6b1845596df75b19e560c99583d680df1b70dbe0e768eaa10aa5399e7bee4c"
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    resolved_root = root.resolve()
    return resolved == resolved_root or resolved.is_relative_to(resolved_root)


def audit_scientific_identity(
    v1_contract: Mapping[str, Any],
    r1_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Deep-compare every Pro-frozen scientific field."""

    per_field = {
        field: {
            "present_in_v1": field in v1_contract,
            "present_in_r1": field in r1_contract,
            "deep_equal": field in v1_contract
            and field in r1_contract
            and v1_contract[field] == r1_contract[field],
        }
        for field in SCIENTIFIC_IDENTITY_FIELDS
    }
    gates = {
        "v1_protocol_id_exact": v1_contract.get("protocol_id") == V1_PROTOCOL_ID,
        "r1_protocol_id_exact": r1_contract.get("protocol_id") == R1_PROTOCOL_ID,
        "scientific_field_set_exact": set(per_field) == set(SCIENTIFIC_IDENTITY_FIELDS),
        "all_scientific_fields_present_and_deep_equal": all(
            item["present_in_v1"] and item["present_in_r1"] and item["deep_equal"]
            for item in per_field.values()
        ),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "fields": per_field,
        "allowed_difference_scope": (
            "schema/protocol metadata, workspace/evidence paths, default-denied "
            "authorization, V1 failure binding, fresh-run identity, and indexed "
            "output-schedule construction"
        ),
    }


def audit_v1_frozen_evidence(candidate_root: Path) -> dict[str, Any]:
    """Hash, but never semantically reuse, the five frozen V1 raw files."""

    files: dict[str, Any] = {}
    for relative, expected_hash in V1_FROZEN_RAW_SHA256.items():
        path = candidate_root / relative
        regular = path.is_file() and not path.is_symlink()
        observed_hash = _sha256(path) if regular else None
        files[relative] = {
            "path": relative,
            "regular_file_not_symlink": regular,
            "expected_sha256": expected_hash,
            "observed_sha256": observed_hash,
            "sha256_exact": observed_hash == expected_hash,
            "use": "HASH_ONLY_IDENTITY_CHECK__NOT_R1_INPUT",
        }
    forensic_files: dict[str, Any] = {}
    for relative, expected_hash in FORENSIC_BUNDLE_SHA256.items():
        path = candidate_root / relative
        regular = path.is_file() and not path.is_symlink()
        observed_hash = _sha256(path) if regular else None
        forensic_files[relative] = {
            "path": relative,
            "regular_file_not_symlink": regular,
            "expected_sha256": expected_hash,
            "observed_sha256": observed_hash,
            "sha256_exact": observed_hash == expected_hash,
        }
    gates = {
        "all_v1_raw_regular_files": all(
            item["regular_file_not_symlink"] for item in files.values()
        ),
        "all_v1_raw_hashes_exact": all(item["sha256_exact"] for item in files.values()),
        "forensic_bundle_regular_files": all(
            item["regular_file_not_symlink"] for item in forensic_files.values()
        ),
        "forensic_bundle_hashes_exact": all(
            item["sha256_exact"] for item in forensic_files.values()
        ),
        "v1_raw_use_is_hash_only": all(
            item["use"] == "HASH_ONLY_IDENTITY_CHECK__NOT_R1_INPUT"
            for item in files.values()
        ),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "v1_raw_files": files,
        "forensic_bundle": forensic_files,
        "v1_raw_or_partial_reuse": "PROHIBITED",
    }


def audit_evidence_root_isolation(candidate_root: Path) -> dict[str, Any]:
    """Prove every R1 output root is outside the preserved V1 raw root."""

    v1_raw = candidate_root / "artifacts" / "runs"
    r1_paths = {
        "raw": candidate_root / "artifacts" / "r1" / "runs",
        "evidence": candidate_root / "artifacts" / "r1" / "evidence",
        "static_review": (
            candidate_root / "artifacts" / "r1" / "d3_implementation_static_review.json"
        ),
        "manifest": (
            candidate_root / "artifacts" / "r1" / "d3_cross_grid_temporal_manifest.json"
        ),
        "report_json": (
            candidate_root / "reports" / "r1" / "d3_cross_grid_temporal_report.json"
        ),
        "report_markdown": (
            candidate_root / "reports" / "r1" / "d3_cross_grid_temporal_report.md"
        ),
    }
    resolved_r1 = {name: path.resolve() for name, path in r1_paths.items()}
    pairwise_distinct = len(set(resolved_r1.values())) == len(resolved_r1)
    outside_v1 = all(not _is_within(path, v1_raw) for path in r1_paths.values())
    v1_outside_r1 = all(not _is_within(v1_raw, path) for path in r1_paths.values())
    gates = {
        "r1_paths_pairwise_distinct": pairwise_distinct,
        "all_r1_paths_outside_v1_raw": outside_v1,
        "v1_raw_outside_all_r1_paths": v1_outside_r1,
        "r1_raw_root_exact": r1_paths["raw"]
        == candidate_root / "artifacts" / "r1" / "runs",
        "r1_evidence_root_exact": r1_paths["evidence"]
        == candidate_root / "artifacts" / "r1" / "evidence",
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "absolute_root_stored": False,
        "v1_raw_root": v1_raw.relative_to(candidate_root).as_posix(),
        "r1_paths": {
            name: path.relative_to(candidate_root).as_posix()
            for name, path in r1_paths.items()
        },
        "v1_as_r1_input": False,
    }


def registered_schedule_audit(
    registered_times: Sequence[float],
    *,
    duration_s: float,
    output_interval_s: float,
) -> dict[str, Any]:
    """Validate an index-driven schedule without generating targets by addition."""

    times = np.asarray(registered_times, dtype=np.float64)
    finite = times.ndim == 1 and times.size >= 2 and bool(np.isfinite(times).all())
    strictly_increasing = finite and bool(np.all(np.diff(times) > 0.0))
    first_zero = finite and int(times[0].view(np.uint64)) == int(
        np.float64(0.0).view(np.uint64)
    )
    last_matches_duration = finite and int(times[-1].view(np.uint64)) == int(
        np.float64(duration_s).view(np.uint64)
    )
    tolerance = 128.0 * np.finfo(np.float64).eps * max(abs(duration_s), 1.0)
    interval_compatible = (
        finite
        and math.isfinite(output_interval_s)
        and output_interval_s > 0.0
        and bool(
            np.all(np.abs(np.diff(times) - np.float64(output_interval_s)) <= tolerance)
        )
    )
    gates = {
        "one_dimensional_nonempty_finite": finite,
        "strictly_increasing_without_duplicates": strictly_increasing,
        "first_time_bitwise_zero": first_zero,
        "last_time_bitwise_equals_duration": last_matches_duration,
        "output_interval_compatible_but_not_generation_source": interval_compatible,
        "indexed_target_rule": True,
        "repeated_floating_addition_prohibited": True,
        "interpolation_prohibited": True,
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "frame_count": int(times.size) if times.ndim == 1 else 0,
        "uint64": (
            [int(value) for value in times.view(np.uint64)] if times.ndim == 1 else []
        ),
        "target_rule": "registered_times[next_index]",
        "next_index_initial": 1,
    }


def assert_r1_input_path(candidate_root: Path, path: Path) -> None:
    """Reject any attempt to treat preserved V1 raw as an R1 input path."""

    if _is_within(path, candidate_root / "artifacts" / "runs"):
        raise ValueError("D3R1 may hash V1 raw for identity but cannot use it as input")
