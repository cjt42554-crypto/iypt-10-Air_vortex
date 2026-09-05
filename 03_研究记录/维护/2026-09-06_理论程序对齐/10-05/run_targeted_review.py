#!/usr/bin/env python3
"""Rebuild the bounded 10-05 HF3D diagnosis.

This script does not load historical raw trajectories or advance physical
time.  It checks their tracked bindings, compares the historical and current
pressure/CLS contracts, runs one deterministic CPU projection counterexample,
and emits a compact machine-readable diagnosis.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import numpy as np

from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from airvortex.numerics.interface3d import mixture_property_3d
from airvortex.numerics.projection3d import VariableDensityProjection3D


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PROGRAM = REPO / "02_模拟程序"

D3_CLOSURE = REPO / (
    "03_研究记录/专题/07_D3跨网格时间步响应诊断/diagnostics/"
    "g9_dt2_projection_closure/artifacts/projection_closure_audit.json"
)
D3_CROSS_CASE = REPO / (
    "03_研究记录/专题/17_HF3D性能优化/artifacts/p0/"
    "divergence_diagnostics.json"
)
D3_CONFIG = REPO / (
    "03_研究记录/专题/07_D3跨网格时间步响应诊断/"
    "generated_configs/G9_DT2.json"
)
Q1R3_FREEZE = REPO / (
    "03_研究记录/专题/01_HF3D数值资格/reports/"
    "q1r3_freeze/freeze_manifest.json"
)
Q1R3_QUALIFICATION = REPO / (
    "03_研究记录/专题/01_HF3D数值资格/reports/"
    "q1r3_spatial_stage/qualification_report.json"
)
Q1R3_DECISION = REPO / (
    "03_研究记录/专题/01_HF3D数值资格/reports/"
    "q1r3_spatial_stage/spatial_stage_decision.json"
)
MIGRATION = REPO / "03_研究记录/维护/2026-09-05_目录重组/migration.json"
CLS_PAIRING = HERE / "cls_pairing.json"

OWNED_RUNTIME_PATHS = (
    "02_模拟程序/src/airvortex/hf3d",
    "02_模拟程序/src/airvortex/numerics/cartesian3d.py",
    "02_模拟程序/src/airvortex/numerics/interface3d.py",
    "02_模拟程序/src/airvortex/numerics/phase_transfer3d.py",
    "02_模拟程序/src/airvortex/numerics/projection3d.py",
)


def run_git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        cwd=REPO,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_tree_identity_at_commit(commit: str) -> dict[str, Any]:
    prefix = "02_模拟程序/"
    listing = subprocess.run(
        ("git", "ls-tree", "-r", "--name-only", "-z", commit, "--", prefix),
        cwd=REPO,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    paths = []
    for encoded in listing.split(b"\0"):
        if not encoded:
            continue
        path = encoded.decode("utf-8")
        relative = path.removeprefix(prefix)
        if relative in ("pyproject.toml", "run.py", "run_gui.sh") or (
            relative.startswith("src/airvortex/") and relative.endswith(".py")
        ):
            paths.append((path, relative))
    paths.sort(key=lambda item: item[1])
    digest = hashlib.sha256()
    digest.update(b"airvortex-source-tree-v1\0")
    total_bytes = 0
    for path, relative_text in paths:
        relative = relative_text.encode("utf-8")
        payload = subprocess.run(
            ("git", "show", f"{commit}:{path}"),
            cwd=REPO,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        total_bytes += len(payload)
    return {
        "algorithm": "airvortex-source-tree-v1/sha256",
        "sha256": digest.hexdigest(),
        "file_count": len(paths),
        "total_bytes": total_bytes,
    }


def worktree_paths_match_head(paths: tuple[str, ...]) -> bool:
    tracked_diff = run_git("diff", "--quiet", "HEAD", "--", *paths, check=False)
    untracked = run_git(
        "ls-files", "--others", "--exclude-standard", "--", *paths
    ).stdout.splitlines()
    return tracked_diff.returncode == 0 and not untracked


def resolve_migrated_path(old_path: str, migration: dict[str, Any]) -> str:
    candidates = [
        item
        for item in migration["prefixes"]
        if old_path == item["old"] or old_path.startswith(item["old"] + "/")
    ]
    if not candidates:
        raise RuntimeError(f"no migration prefix for {old_path}")
    selected = max(candidates, key=lambda item: len(item["old"]))
    return selected["new"] + old_path[len(selected["old"]) :]


def pressure_contract_counterexample() -> dict[str, Any]:
    grid = ImmersedCartesianGrid(
        12,
        12,
        16,
        0.045,
        0.080,
        bar_half_length_m=0.015,
        bar_radius_m=0.003,
        bar_centre_z_m=0.0045,
    )
    rng = np.random.default_rng(1005)
    velocity = tuple(
        rng.normal(size=shape) * open_mask
        for shape, open_mask in (
            (grid.face_shape_x, grid.open_x),
            (grid.face_shape_y, grid.open_y),
            (grid.face_shape_z, grid.open_z),
        )
    )
    density = mixture_property_3d(
        np.where(grid.z_cell < 0.050, 1.0, 0.0), 998.2, 1.204
    )
    configured_rtol = 1.0e-7
    divergence_target = 1.0e-4
    legacy = VariableDensityProjection3D(
        grid,
        relative_tolerance=configured_rtol,
        maximum_iterations=2000,
    )
    *_, legacy_diagnostics = legacy.project(*velocity, density, 1.0e-4)
    controlled = VariableDensityProjection3D(
        grid,
        relative_tolerance=configured_rtol,
        maximum_iterations=2000,
        maximum_divergence_s_inv=divergence_target,
    )
    *_, controlled_diagnostics = controlled.project(*velocity, density, 1.0e-4)
    return {
        "scope": (
            "Fixed-seed 12x12x16 CPU projection; identical current operator, density, "
            "velocity and dt; only the absolute divergence control is disabled in the "
            "legacy comparator."
        ),
        "seed": 1005,
        "configured_relative_tolerance": configured_rtol,
        "maximum_divergence_s_inv": divergence_target,
        "legacy_relative_only": {
            "pressure_relative_residual": legacy_diagnostics.pressure_relative_residual,
            "divergence_after_s_inv": legacy_diagnostics.divergence_after_s_inv,
            "relative_gate_pass": (
                legacy_diagnostics.pressure_relative_residual <= configured_rtol
            ),
            "absolute_divergence_gate_pass": (
                legacy_diagnostics.divergence_after_s_inv <= divergence_target
            ),
        },
        "current_absolute_control": {
            "effective_relative_tolerance": (
                controlled_diagnostics.effective_relative_tolerance
            ),
            "pressure_relative_residual": (
                controlled_diagnostics.pressure_relative_residual
            ),
            "divergence_after_s_inv": controlled_diagnostics.divergence_after_s_inv,
            "divergence_residual_identity_error_s_inv": (
                controlled_diagnostics.divergence_residual_identity_error_s_inv
            ),
            "absolute_divergence_gate_pass": (
                controlled_diagnostics.divergence_after_s_inv <= divergence_target
            ),
        },
        "regression": "02_模拟程序/tests/test_hf3d_10_05.py",
        "claim_limit": (
            "This isolates the stopping-contract defect on a synthetic CPU state; it is "
            "not the archived G9_DT2 state and does not qualify a dynamic trajectory."
        ),
    }


def git_file(commit: str, path: str) -> str:
    return run_git("show", f"{commit}:{path}").stdout


def is_ancestor(ancestor: str, descendant: str) -> bool:
    return run_git(
        "merge-base", "--is-ancestor", ancestor, descendant, check=False
    ).returncode == 0


def git_blob(commit: str, path: str) -> str:
    return run_git("rev-parse", f"{commit}:{path}").stdout.strip()


def q1r3_observables(report: dict[str, Any]) -> dict[str, Any]:
    records = report["groups"]["spatial"]["smooth_observables"]
    result: dict[str, Any] = {}
    for name, record in records.items():
        estimate = record["estimate"]
        result[name] = {
            "pass": record["pass"],
            "fine_vs_medium_relative_l2": estimate["pair_fine_medium"][
                "relative_l2"
            ],
            "fine_vs_medium_relative_linf": estimate["pair_fine_medium"][
                "relative_linf"
            ],
            "observed_order": estimate["observed_order"],
            "fine_grid_gci_l2": estimate["fine_grid_gci_l2"],
            "asymptotic_trend": estimate["asymptotic_trend"],
            "reason": estimate["reason"],
        }
    return result


def q1r3_quality(report: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for case_id in ("space_L0_4cells", "space_L1_6cells", "space_L2_9cells"):
        record = report["numerical_quality"][case_id]
        result[case_id] = {
            "pass": record["pass"],
            "projected_divergence_s_inv": record["checks"][
                "projected_divergence_s_inv"
            ]["value"],
            "pressure_relative_residual": record["checks"]["pressure_residual"][
                "value"
            ],
            "liquid_volume_balance_error_relative": record["checks"][
                "liquid_volume_balance_error_relative"
            ]["value"],
        }
    return result


def build_report() -> dict[str, Any]:
    closure = read_json(D3_CLOSURE)
    cross_case = read_json(D3_CROSS_CASE)
    freeze = read_json(Q1R3_FREEZE)
    qualification = read_json(Q1R3_QUALIFICATION)
    decision = read_json(Q1R3_DECISION)
    migration = read_json(MIGRATION)
    cls_pairing = read_json(CLS_PAIRING)

    head = run_git("rev-parse", "HEAD").stdout.strip()
    runtime_paths_match_head = worktree_paths_match_head(OWNED_RUNTIME_PATHS)
    if not runtime_paths_match_head:
        raise RuntimeError("HF3D runtime paths differ from HEAD; refuse mixed-source review")
    d3_commit = closure["execution"]["d3_execution_commit"]
    q1r3_last_source_commit = run_git(
        "log",
        "-1",
        "--format=%H",
        "fb709af^",
        "--",
        "02_模拟程序",
    ).stdout.strip()
    pressure_path = "02_模拟程序/src/airvortex/numerics/projection3d.py"
    d3_old_pressure = git_file(d3_commit, pressure_path)
    current_pressure = git_file(head, pressure_path)
    q1r3_old_backend = git_file(
        q1r3_last_source_commit,
        "02_模拟程序/src/airvortex/backends/high_fidelity_3d.py",
    )
    current_stepper = git_file(
        head, "02_模拟程序/src/airvortex/hf3d/stepper.py"
    )

    failure = closure["failure_step"]
    algebraic = closure["decomposition"]["algebraic_component"]
    operator = closure["decomposition"]["operator_defect"]
    gate = 1.0e-4
    old_relative_tolerance = 1.0e-7
    legacy_partial_path = (
        "90_候选工作区/current/07_D3跨网格时间步响应诊断/"
        "artifacts/r1/runs/cases/G9_DT2/partial.npz"
    )
    mapped_partial_path = resolve_migrated_path(legacy_partial_path, migration)
    raw_partial = REPO / mapped_partial_path
    partial_tracked = (
        run_git("ls-files", "--error-unmatch", mapped_partial_path, check=False).returncode
        == 0
    )
    history_partial_paths = sorted(
        line.split(" ", 1)[1]
        for line in run_git("rev-list", "--objects", "--all").stdout.splitlines()
        if " " in line and line.split(" ", 1)[1].endswith("partial.npz")
    )
    lfs = run_git("lfs", "ls-files", check=False)
    lfs_entries = lfs.stdout.splitlines() if lfs.returncode == 0 else []
    search_roots = [str(raw_partial.parent)]
    exact_size_candidates = ([str(raw_partial)] if raw_partial.is_file()
        and raw_partial.stat().st_size == int(closure["execution"]["partial_size_bytes"]) else [])
    nvidia = subprocess.run(
        (
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader",
        ),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    input_paths = (
        D3_CLOSURE,
        D3_CROSS_CASE,
        D3_CONFIG,
        Q1R3_FREEZE,
        Q1R3_QUALIFICATION,
        Q1R3_DECISION,
        MIGRATION,
        CLS_PAIRING,
        HERE / "quantify_cls_pairing.py",
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "review_id": "AIRVORTEX_10_05_TARGETED_HF3D_REVIEW_V1",
        "source": {
            "head": head,
            "runtime_paths_verified_against_head": list(OWNED_RUNTIME_PATHS),
            "runtime_paths_match_head": runtime_paths_match_head,
            "head_formal_source_tree": source_tree_identity_at_commit(head),
            "input_artifacts": {
                path.relative_to(REPO).as_posix(): sha256_file(path)
                for path in input_paths
            },
        },
        "hardware_runtime": {
            "nvidia_smi_exit_code": nvidia.returncode,
            "nvidia_smi": nvidia.stdout.strip(),
            "cupy_importable_in_system_python": importlib.util.find_spec("cupy")
            is not None,
            "gpu_execution_this_review": "NOT_RUN",
        },
        "d3r1_g9_dt2": {
            "historical_binding": {
                "execution_commit": d3_commit,
                "formal_source_tree_sha256": closure["execution"][
                    "formal_source_tree_sha256"
                ],
                "config_sha256_recorded": closure["execution"]["config_sha256"],
                "config_sha256_current_file": sha256_file(D3_CONFIG),
                "partial_sha256_recorded": closure["execution"][
                    "partial_sha256_before"
                ],
                "partial_size_bytes_recorded": closure["execution"][
                    "partial_size_bytes"
                ],
                "partial_present_in_current_checkout": raw_partial.is_file(),
            },
            "partial_recovery_audit": {
                "historical_path": legacy_partial_path,
                "mapped_current_path": mapped_partial_path,
                "mapped_target_present": raw_partial.is_file(),
                "mapped_target_tracked_by_git": partial_tracked,
                "partial_paths_in_all_git_object_history": history_partial_paths,
                "git_lfs_query_exit_code": lfs.returncode,
                "git_lfs_entry_count": len(lfs_entries),
                "recorded_size_search_roots": search_roots,
                "recorded_size_candidates": exact_size_candidates,
                "recovery_status": (
                    "NOT_RECOVERABLE_FROM_GIT_OR_PROJECT_INDEXED_LOCAL_STORAGE"
                ),
                "remaining_possibilities": [
                    "an unrecorded user backup containing the exact hash",
                    "full recomputation from the frozen source/config contract",
                ],
            },
            "failure": {
                "accepted_step": failure["accepted_step"],
                "time_s": failure["time_s"],
                "dt_s": failure["dt_s"],
                "measured_divergence_s_inv": failure[
                    "recomputed_max_projected_divergence_s_inv"
                ],
                "maximum_divergence_s_inv": gate,
                "gate_ratio": failure[
                    "recomputed_max_projected_divergence_s_inv"
                ]
                / gate,
                "pressure_relative_residual_l2": failure[
                    "recomputed_pressure_relative_residual_l2"
                ],
                "old_relative_residual_gate_satisfied": failure[
                    "recomputed_pressure_relative_residual_l2"
                ]
                <= old_relative_tolerance,
                "algebraic_residual_signed_explained_fraction": algebraic[
                    "signed_max_cell_explained_fraction"
                ],
                "operator_defect_max_s_inv": closure["decomposition"][
                    "max_operator_defect_s_inv"
                ],
                "operator_defect_at_failure_cell_s_inv": operator[
                    "candidate_at_divergence_max_s_inv"
                ],
                "archive_alignment_reproduced": failure[
                    "archived_scalar_reproduced"
                ],
            },
            "source_contrast": {
                "historical_pressure_blob": git_blob(d3_commit, pressure_path),
                "current_pressure_blob": git_blob(head, pressure_path),
                "historical_solver_only_used_configured_relative_tolerance": (
                    "rtol=self.relative_tolerance" in d3_old_pressure
                    and "maximum_divergence_s_inv" not in d3_old_pressure
                ),
                "current_solver_derives_effective_tolerance_from_divergence_target": (
                    "effective_rtol" in current_pressure
                    and "maximum_divergence_s_inv" in current_pressure
                    and "residual_predicted_divergence_s_inv" in current_pressure
                ),
                "current_solver_rechecks_actual_divergence": (
                    "pressure projection exceeds divergence target" in current_pressure
                ),
                "historical_execution_precedes_current_fix": is_ancestor(
                    d3_commit, "af2101b"
                ),
            },
            "diagnosis": {
                "direct_numerical_mechanism": (
                    "RELATIVE_L2_PRESSURE_STOP_DID_NOT_BOUND_ABSOLUTE_LINF_DIVERGENCE"
                ),
                "mechanism_status": "IDENTIFIED_FROM_FROZEN_DECOMPOSITION_AND_SOURCE",
                "current_implementation_status": (
                    "DIVERGENCE_TARGET_CONTROL_IMPLEMENTED_AND_BOUNDED_REGRESSION_PASS"
                ),
                "exact_failure_state_replay": "NOT_RUN_RAW_PARTIAL_AND_CUPY_RUNTIME_ABSENT",
                "full_d3r1_replay": "NOT_RUN",
                "claim_limit": (
                    "The current solver closes the stopping-criterion defect, but no "
                    "current-source CUDA trajectory has shown that G9_DT2 reaches the "
                    "registered end or that D3 converges."
                ),
            },
            "deterministic_pressure_contract_counterexample": (
                pressure_contract_counterexample()
            ),
        },
        "q1r3": {
            "historical_binding": {
                "expected_formal_source_tree_sha256": freeze[
                    "expected_formal_source_tree_sha256"
                ],
                "last_formal_program_commit_before_raw_freeze": q1r3_last_source_commit,
                "historical_source_precedes_current_fix": is_ancestor(
                    q1r3_last_source_commit, "af2101b"
                ),
                "current_formal_source_tree_sha256": source_tree_identity_at_commit(head)[
                    "sha256"
                ],
            },
            "historical_decision": decision["status"],
            "all_six_smooth_observables_failed": all(
                not record["pass"]
                for record in qualification["groups"]["spatial"][
                    "smooth_observables"
                ].values()
            ),
            "smooth_observables": q1r3_observables(qualification),
            "historical_numerical_quality": q1r3_quality(qualification),
            "source_contrast": {
                "historical_cls_used_global_clip_mass_correction": (
                    "bounded_mass_correction_masked" in q1r3_old_backend
                ),
                "historical_cls_had_no_paired_momentum_callback": (
                    "transfer_callback" not in q1r3_old_backend
                    and "transport_momentum_with_phase_transfers"
                    not in q1r3_old_backend
                ),
                "current_cls_pairs_density_and_momentum": (
                    "pair_cls_momentum" in current_stepper
                    and "transport_momentum_with_phase_transfers" in current_stepper
                ),
                "current_physical_phase_advection_retains_residual_sized_unpaired_budget": (
                    "divergence_allowance" in current_stepper
                    and "without matching " in current_stepper and "momentum flux;" in current_stepper
                ),
            },
            "diagnosis": {
                "historical_result_for_current_solver": (
                    "NON_TRANSFERABLE_AFTER_MASS_MOMENTUM_AND_PRESSURE_CONTRACT_CHANGE"
                ),
                "pressure_stop_defect_as_primary_explanation": (
                    "NOT_SUPPORTED_BY_OLD_RUN_QUALITY; ALL_THREE_SPATIAL_CASES_PASSED_"
                    "THE_DIVERGENCE_GATE"
                ),
                "bulk_channel_clearance_fix_as_explanation": (
                    "NOT_SUPPORTED; RESOLVED_CHANNEL_IS_NOT_ONE_OF_THE_SIX_SMOOTH_"
                    "OBSERVABLES"
                ),
                "remaining_mechanism_status": "NOT_UNIQUE",
                "remaining_supported_contributors": [
                    "historical unpaired CLS density/momentum update can contaminate a trajectory; the magnitude in Q1R3 is unestablished",
                    "G0 immersed-bar and diffuse-interface resolution can change forcing and observables",
                    "open-top liquid inventory response is grid dependent despite flux-corrected conservation",
                    "the current physical phase-advection clip/repair remains only residual-bounded, not exactly paired",
                ],
                "claim_limit": (
                    "The old six-observable failure remains historical evidence and is not closed. "
                    "Changed source and small tests establish neither current failure nor current convergence."
                ),
            },
            "small_domain_cls_pairing_counterexample": cls_pairing,
        },
        "verification_boundary": {
            "completed": [
                "13 bounded CPU regressions in tests/test_audit_numerics_3d.py",
                "one targeted pressure stopping-contract regression in tests/test_hf3d_10_05.py",
                "one deterministic small-domain paired/unpaired CLS counterexample",
                "tracked frozen JSON and source/config binding checks",
                "historical/current source contract comparison",
                "mapped-path, Git object/LFS, and recorded-size local partial recovery audit",
            ],
            "not_run": [
                "CUDA projection parity because CuPy is absent from system Python",
                "exact G9_DT2 failure-state projection replay because partial.npz is absent",
                "D3R1 dynamic rerun",
                "Q1R3 current-source three-grid rerun",
                "physical or experimental validation",
            ],
        },
        "recommended_next_experiments": [
            {
                "priority": 1,
                "experiment": "EXACT_G9_DT2_FAILURE_STATE_PROJECTION_REPLAY",
                "requirements": [
                    "restore the 60,848,588-byte partial.npz read-only",
                    "verify SHA-256 8f1e0973eda48c2ecc54f10001f28b988d353e85a728a16ec24f74582527cbcc",
                    "restore an isolated CuPy runtime and require explicit CUDA/no fallback",
                ],
                "scope": (
                    "Reconstruct the pre-projection velocity from the frozen corrected state and "
                    "pressure, then apply one current projection solve; do not advance dynamics."
                ),
                "acceptance": (
                    "actual projected divergence <= 1e-4 s^-1, residual identity closes, "
                    "physics/config/threshold unchanged"
                ),
            },
            {
                "priority": 2,
                "experiment": "CURRENT_SOURCE_Q1R3_THREE_GRID_PROSPECTIVE_CAMPAIGN",
                "requirements": [
                    "new protocol and evidence root because the algorithm identity changed",
                    "all three 4/6/9-cells-per-radius cases from zero",
                    "same physical inputs and frozen convergence thresholds",
                    "explicit CUDA/no fallback and new CLS/divergence diagnostics",
                ],
                "scope": (
                    "Run only if numerical qualification is resumed. A single finer case cannot "
                    "separate CLS correction from geometry, interface, or open-boundary effects."
                ),
                "acceptance": (
                    "all six smooth observables plus quality and binary-event gates; no result "
                    "may be used as physical or apparatus validation"
                ),
            },
        ],
        "overall_status": (
            "IMPLEMENTATION_LEVEL_D3_STOPPING_DEFECT_CLOSED__Q1R3_CURRENT_CONVERGENCE_"
            "UNKNOWN__GPU_AND_DYNAMIC_REPLAYS_NOT_RUN__READY_TO_PAUSE_BEFORE_EXPERIMENT"
        ),
    }
    if report["d3r1_g9_dt2"]["historical_binding"]["config_sha256_recorded"] != report[
        "d3r1_g9_dt2"
    ]["historical_binding"]["config_sha256_current_file"]:
        raise RuntimeError("G9_DT2 config hash no longer matches the frozen audit")
    if not report["d3r1_g9_dt2"]["failure"]["archive_alignment_reproduced"]:
        raise RuntimeError("frozen D3 audit did not reproduce its archived failure scalar")
    if not report["q1r3"]["all_six_smooth_observables_failed"]:
        raise RuntimeError("tracked Q1R3 report no longer records all six failures")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "diagnosis.json")
    arguments = parser.parse_args()
    report = build_report()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
