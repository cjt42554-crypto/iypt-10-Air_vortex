#!/usr/bin/env python3
"""Independently analyse D2 V2 archives and publish compact tracked evidence."""

from __future__ import annotations

import argparse
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
Q1R3_ROOT = HERE.parent / "01_HF3D数值资格"
PREREGISTRATION_PATH = HERE / "d2_source_attribution_preregistration_v2.json"
RAW_ROOT = HERE / "artifacts" / "runs"
S0_PATH = RAW_ROOT / "s0_identity_implementation.json"
PRELIMINARY_R_PATH = RAW_ROOT / "preliminary_r.json"
FAILURE_PATH = RAW_ROOT / "execution_failure.json"
RAW_LEDGER_PATH = RAW_ROOT / "stage_ledger.json"
REPORT_JSON = HERE / "reports" / "d2_source_attribution_report.json"
REPORT_MD = HERE / "reports" / "d2_source_attribution_report.md"
ARTIFACT_MANIFEST = HERE / "artifacts" / "d2_source_attribution_manifest.json"
EVIDENCE_ROOT = HERE / "artifacts" / "evidence"
TRACKED_LEDGER = EVIDENCE_ROOT / "stage_ledger.json"

if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

from airvortex.core import RunStatus
from airvortex.io import load_run_archive
from airvortex.io.archive import source_tree_identity
from airvortex.io.config_io import canonical_json_bytes, read_json_strict

PROTOCOL_ID = "D2_HF3D_NUMERICAL_SOURCE_ATTRIBUTION_V2"
FAIL_CLOSED = "D2_SOURCE_ATTRIBUTION_INCONCLUSIVE_FAIL_CLOSED"
EXPECTED_OUTPUT_TIME = np.linspace(0.0, 0.010, 11, dtype=np.float64)
PRIMARY = (
    "funnel_depth_m",
    "kinetic_energy_lab_j",
    "angular_momentum_z_kg_m2_s",
    "axisymmetry_error",
)
SECONDARY = ("min_gap_to_bar_m", "liquid_inventory_change_relative")
OBSERVABLE_ALIAS = {"liquid_inventory_change_relative": "liquid_mass_error_relative"}
REQUIRED_ARCHIVE_OBSERVABLES = (
    *PRIMARY,
    "min_gap_to_bar_m",
    "liquid_mass_error_relative",
    "liquid_volume_balance_error_relative",
    "expected_liquid_volume_m3",
    "expected_liquid_volume_change_relative",
    "phase_top_outward_flux_m3_s",
    "maximum_laboratory_speed_m_s",
    "rms_laboratory_speed_m_s",
    "maximum_surface_tension_acceleration_m_s2",
    "maximum_boundary_acceleration_m_s2",
    "actual_bar_rpm",
    "bar_angle_rad",
)
BASELINE_CONFIGS: dict[str, Path] = {
    "F6": Q1R3_ROOT / "generated_q1r3" / "space_L1_6cells.json",
    "F9": Q1R3_ROOT / "generated_q1r3" / "space_L2_9cells.json",
}
SINGLE_CASES = ("F6", "F9", "Z6", "Z9", "T9")
R_PAIR_CASES = ("R9_BASE", "R9_ALL")
R_MULTIWAY_CASES = ("R9_BASE", "R9_ALL", "R9_IF", "R9_BL", "R9_ST")


class D2AnalysisError(RuntimeError):
    """Raised when archive evidence cannot support frozen D2 arithmetic."""


@dataclass(slots=True)
class RunEvidence:
    label: str
    archive_path: Path
    config: dict[str, Any]
    config_canonical_sha256: str
    time_s: NDArray[np.float64]
    observables: dict[str, NDArray[np.float64]]
    metadata: dict[str, Any]
    manifest: dict[str, Any]
    provenance: dict[str, Any]
    fields_all_finite: bool


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
        raise D2AnalysisError(f"JSON root must be an object: {path}")
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


def _deep_get(root: Mapping[str, Any], dotted: str) -> Any:
    value: Any = root
    for token in dotted.split("."):
        if not isinstance(value, Mapping) or token not in value:
            raise D2AnalysisError(f"configuration path is missing: {dotted}")
        value = value[token]
    return value


def _deep_set(root: dict[str, Any], dotted: str, value: Any) -> None:
    tokens = dotted.split(".")
    cursor = root
    for token in tokens[:-1]:
        child = cursor.get(token)
        if not isinstance(child, dict):
            raise D2AnalysisError(f"configuration path is not an object: {dotted}")
        cursor = child
    if tokens[-1] not in cursor:
        raise D2AnalysisError(f"configuration path is missing: {dotted}")
    cursor[tokens[-1]] = value


def rms(values: NDArray[np.floating[Any]]) -> float:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size == 0 or not np.isfinite(array).all():
        raise D2AnalysisError("RMS input must be a nonempty finite array")
    return float(math.sqrt(float(np.mean(array * array))))


def initial_removed(
    values: NDArray[np.floating[Any]],
) -> NDArray[np.float64]:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size == 0 or not np.isfinite(array).all():
        raise D2AnalysisError("observable trajectory must be nonempty and finite")
    return array - array[0]


def compute_effect_metrics(
    f6: NDArray[np.floating[Any]],
    f9: NDArray[np.floating[Any]],
    effect_low: NDArray[np.floating[Any]],
    effect_high: NDArray[np.floating[Any]],
    epsilon: float,
    residual_sign: int,
) -> dict[str, Any]:
    """Compute frozen E, eta, and rho from initial-removed trajectories."""

    if not math.isfinite(epsilon) or epsilon <= 0.0:
        raise ValueError("epsilon must be finite and positive")
    if residual_sign not in (-1, 1):
        raise ValueError("residual_sign must be -1 or 1")
    d_f6, d_f9, d_low, d_high = (
        initial_removed(value) for value in (f6, f9, effect_low, effect_high)
    )
    sizes = {value.size for value in (d_f6, d_f9, d_low, d_high)}
    if len(sizes) != 1:
        raise D2AnalysisError("compared trajectories must have equal lengths")
    spatial = d_f9 - d_f6
    effect = d_high - d_low
    residual = spatial + residual_sign * effect
    baseline_scale = rms(spatial)
    denominator = max(baseline_scale, epsilon)
    return {
        "epsilon_Q": epsilon,
        "E_Q": baseline_scale,
        "effect_rms": rms(effect),
        "eta": rms(effect) / denominator,
        "residual_rms": rms(residual),
        "rho": rms(residual) / denominator,
        "delta_F6": d_f6.tolist(),
        "delta_F9": d_f9.tolist(),
        "delta_effect_low": d_low.tolist(),
        "delta_effect_high": d_high.tolist(),
        "spatial_vector": spatial.tolist(),
        "effect_vector": effect.tolist(),
        "counterfactual_residual_vector": residual.tolist(),
        "residual_sign": residual_sign,
    }


def classify_magnitude(
    eta_by_primary: Mapping[str, float],
    *,
    material_count_min: int = 2,
    material_eta_min: float = 0.5,
    small_eta_max_exclusive: float = 0.1,
) -> tuple[str, dict[str, Any]]:
    values = {name: float(value) for name, value in eta_by_primary.items()}
    if len(values) != 4 or not all(math.isfinite(value) for value in values.values()):
        raise D2AnalysisError("magnitude classification requires four finite values")
    material = {name: value >= material_eta_min for name, value in values.items()}
    count = sum(material.values())
    if count >= material_count_min:
        label = "MATERIAL"
    elif all(value < small_eta_max_exclusive for value in values.values()):
        label = "SMALL"
    else:
        label = "MIXED_OR_INDETERMINATE"
    return label, {
        "eta_by_primary": values,
        "material_flags": material,
        "material_count": count,
        "material_count_min": material_count_min,
        "material_eta_min": material_eta_min,
        "small_eta_max_exclusive": small_eta_max_exclusive,
    }


def classify_direction(
    rho_by_primary: Mapping[str, float],
    *,
    baseline_drift_by_primary: Mapping[str, float] | None = None,
    baseline_drift_limit: float = 0.1,
    signal_count_min: int = 2,
    reduction_rho_max: float = 0.8,
    increase_rho_min: float = 1.25,
) -> tuple[str, dict[str, Any]]:
    values = {name: float(value) for name, value in rho_by_primary.items()}
    if len(values) != 4 or not all(math.isfinite(value) for value in values.values()):
        raise D2AnalysisError("direction classification requires four finite values")
    drift = (
        None
        if baseline_drift_by_primary is None
        else {name: float(value) for name, value in baseline_drift_by_primary.items()}
    )
    if drift is not None and (
        len(drift) != 4 or not all(math.isfinite(value) for value in drift.values())
    ):
        raise D2AnalysisError("baseline drift requires four finite values")
    reduction = {name: value <= reduction_rho_max for name, value in values.items()}
    increase = {name: value >= increase_rho_min for name, value in values.items()}
    reduction_count = sum(reduction.values())
    increase_count = sum(increase.values())
    drift_trigger = drift is not None and any(
        value >= baseline_drift_limit for value in drift.values()
    )
    if drift_trigger:
        label = "DIRECTION_INCONCLUSIVE_BASELINE_DRIFT"
    elif reduction_count >= signal_count_min and increase_count < signal_count_min:
        label = "REDUCTION_SIGNAL"
    elif increase_count >= signal_count_min and reduction_count < signal_count_min:
        label = "INCREASE_SIGNAL"
    else:
        label = "NO_CLEAR_DIRECTION"
    return label, {
        "rho_by_primary": values,
        "reduction_flags": reduction,
        "increase_flags": increase,
        "reduction_count": reduction_count,
        "increase_count": increase_count,
        "signal_count_min": signal_count_min,
        "reduction_rho_max": reduction_rho_max,
        "increase_rho_min": increase_rho_min,
        "baseline_drift_by_primary": drift,
        "baseline_drift_limit": baseline_drift_limit if drift is not None else None,
        "baseline_drift_triggered": drift_trigger,
        "opposed_count_signals_are_no_clear_direction": True,
    }


def should_run_s5(r_magnitude_label: str) -> bool:
    if r_magnitude_label == "SMALL":
        return False
    if r_magnitude_label in {"MATERIAL", "MIXED_OR_INDETERMINATE"}:
        return True
    raise ValueError(f"unknown R magnitude label: {r_magnitude_label}")


def interaction_kappa(
    d_all: NDArray[np.floating[Any]],
    d_interface: NDArray[np.floating[Any]],
    d_boundary: NDArray[np.floating[Any]],
    d_surface: NDArray[np.floating[Any]],
    epsilon: float,
) -> dict[str, Any]:
    arrays = [
        np.asarray(value, dtype=np.float64).reshape(-1)
        for value in (d_all, d_interface, d_boundary, d_surface)
    ]
    if len({value.size for value in arrays}) != 1:
        raise D2AnalysisError("interaction trajectories must have equal lengths")
    interaction = arrays[0] - arrays[1] - arrays[2] - arrays[3]
    denominator = max(rms(arrays[0]), epsilon)
    kappa = rms(interaction) / denominator
    return {
        "kappa": kappa,
        "non_additive": kappa > 0.25,
        "threshold_exclusive": 0.25,
        "d_ALL": arrays[0].tolist(),
        "d_IF": arrays[1].tolist(),
        "d_BL": arrays[2].tolist(),
        "d_ST": arrays[3].tolist(),
        "interaction_vector": interaction.tolist(),
    }


def inventory_flux_residual(
    actual6: NDArray[np.floating[Any]],
    actual9: NDArray[np.floating[Any]],
    expected6: NDArray[np.floating[Any]],
    expected9: NDArray[np.floating[Any]],
) -> dict[str, Any]:
    a6, a9, p6, p9 = (
        np.asarray(value, dtype=np.float64).reshape(-1)
        for value in (actual6, actual9, expected6, expected9)
    )
    if len({value.size for value in (a6, a9, p6, p9)}) != 1:
        raise D2AnalysisError("inventory trajectories must have equal lengths")
    actual_difference = a9 - a6
    expected_difference = p9 - p6
    residual = actual_difference - expected_difference
    denominator = max(rms(actual_difference), 1.0e-9)
    ratio = rms(residual) / denominator
    return {
        "r_flux": ratio,
        "mediated": ratio <= 0.1,
        "threshold_max": 0.1,
        "actual_layer_difference": actual_difference.tolist(),
        "expected_open_boundary_layer_difference": expected_difference.tolist(),
        "residual": residual.tolist(),
        "label": (
            "INVENTORY_DIFFERENCE_TOP_BOUNDARY_MEDIATED" if ratio <= 0.1 else None
        ),
        "does_not_prove_boundary_condition_error": True,
        "does_not_prove_nonconservation": True,
    }


def _case_summary_path(label: str) -> Path:
    return RAW_ROOT / "cases" / label / "case_summary.json"


def _group_summary_path(name: str) -> Path:
    return RAW_ROOT / name / "group_summary.json"


def _summary_archive_path(summary: Mapping[str, Any], label: str) -> Path:
    archives = summary.get("archives")
    if not isinstance(archives, Mapping):
        raise D2AnalysisError("passing summary has no archives")
    record = archives.get(label)
    if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
        raise D2AnalysisError(f"archive path is missing for {label}")
    return REPOSITORY_ROOT / str(record["path"])


def _expected_config(label: str) -> dict[str, Any]:
    prereg = _json_object(PREREGISTRATION_PATH)
    case = prereg["cases"][label]
    base_name = str(case["base_config"])
    result = deepcopy(_json_object(BASELINE_CONFIGS[base_name]))
    for path, value in case["allowed_overrides"].items():
        _deep_set(result, str(path), value)
    return result


def _load_run_evidence(
    label: str,
    archive_path: Path,
) -> RunEvidence:
    archived = load_run_archive(archive_path, verify=True)
    if archived.result.status is not RunStatus.COMPLETED:
        raise D2AnalysisError(f"{label} archive is not completed")
    config = archived.config.to_dict()
    expected = _expected_config(label)
    if config != expected:
        raise D2AnalysisError(f"{label} archived configuration differs from contract")
    time_s = np.asarray(archived.result.time_s, dtype=np.float64).copy()
    if not np.array_equal(time_s, EXPECTED_OUTPUT_TIME):
        raise D2AnalysisError(f"{label} output time grid differs from registration")
    observables: dict[str, NDArray[np.float64]] = {}
    for name in REQUIRED_ARCHIVE_OBSERVABLES:
        if name not in archived.result.observables:
            raise D2AnalysisError(f"{label} observable is missing: {name}")
        value = np.asarray(archived.result.observables[name], dtype=np.float64).reshape(
            -1
        )
        if value.size != time_s.size or not np.isfinite(value).all():
            raise D2AnalysisError(f"{label} observable is invalid: {name}")
        observables[name] = value.copy()
    fields_all_finite = all(
        np.isfinite(np.asarray(value)).all()
        for value in archived.result.fields.values()
    )
    metadata = dict(archived.result.metadata)
    manifest = dict(archived.manifest)
    provenance = dict(archived.provenance)
    return RunEvidence(
        label=label,
        archive_path=archive_path,
        config=config,
        config_canonical_sha256=_canonical_sha256(config),
        time_s=time_s,
        observables=observables,
        metadata=metadata,
        manifest=manifest,
        provenance=provenance,
        fields_all_finite=fields_all_finite,
    )


def _load_summary_and_runs(
    summary_path: Path,
    labels: Sequence[str],
) -> tuple[dict[str, Any], dict[str, RunEvidence]]:
    summary = _json_object(summary_path)
    if summary.get("gate_pass") is not True:
        raise D2AnalysisError(f"stage summary did not pass: {summary_path}")
    runs = {
        label: _load_run_evidence(
            label,
            _summary_archive_path(summary, label),
        )
        for label in labels
    }
    return summary, runs


def _observable(run: RunEvidence, name: str) -> NDArray[np.float64]:
    archive_name = OBSERVABLE_ALIAS.get(name, name)
    return run.observables[archive_name]


def _window_indices(start: float, end: float) -> NDArray[np.int64]:
    tolerance = 256.0 * np.finfo(float).eps * max(end, 1.0)
    indices = np.flatnonzero(
        (EXPECTED_OUTPUT_TIME >= start - tolerance)
        & (EXPECTED_OUTPUT_TIME <= end + tolerance)
    )
    if indices.size < 2:
        raise D2AnalysisError("registered diagnostic window has too few frames")
    return indices


def _factor_analysis(
    name: str,
    runs: Mapping[str, RunEvidence],
    prereg: Mapping[str, Any],
    *,
    effect_low: str,
    effect_high: str,
    residual_sign: int,
    baseline_drift_run: str | None = None,
) -> dict[str, Any]:
    floors = prereg["observables"]["absolute_scale_floors"]
    all_metrics: dict[str, Any] = {}
    for observable in (*PRIMARY, *SECONDARY):
        all_metrics[observable] = compute_effect_metrics(
            _observable(runs["F6"], observable),
            _observable(runs["F9"], observable),
            _observable(runs[effect_low], observable),
            _observable(runs[effect_high], observable),
            float(floors[observable]),
            residual_sign,
        )
    eta = {observable: all_metrics[observable]["eta"] for observable in PRIMARY}
    rho = {observable: all_metrics[observable]["rho"] for observable in PRIMARY}
    magnitude_label, magnitude_detail = classify_magnitude(eta)
    beta: dict[str, float] | None = None
    beta_inputs: dict[str, Any] | None = None
    if baseline_drift_run is not None:
        beta = {}
        beta_inputs = {}
        for observable in (*PRIMARY, *SECONDARY):
            d_base = initial_removed(_observable(runs[baseline_drift_run], observable))
            d_f9 = initial_removed(_observable(runs["F9"], observable))
            denominator = max(
                float(all_metrics[observable]["E_Q"]),
                float(floors[observable]),
            )
            value = rms(d_base - d_f9) / denominator
            beta_inputs[observable] = {
                "beta_R": value,
                "delta_R9_BASE_minus_delta_F9": (d_base - d_f9).tolist(),
                "denominator": denominator,
            }
            if observable in PRIMARY:
                beta[observable] = value
    direction_label, direction_detail = classify_direction(
        rho,
        baseline_drift_by_primary=beta,
    )

    windows: dict[str, Any] = {}
    for window_name, bounds in prereg["common_execution"][
        "registered_windows_s"
    ].items():
        indices = _window_indices(float(bounds[0]), float(bounds[1]))
        windows[window_name] = {
            observable: compute_effect_metrics(
                _observable(runs["F6"], observable)[indices],
                _observable(runs["F9"], observable)[indices],
                _observable(runs[effect_low], observable)[indices],
                _observable(runs[effect_high], observable)[indices],
                float(floors[observable]),
                residual_sign,
            )
            for observable in (*PRIMARY, *SECONDARY)
        }
    source_language_allowed = (
        magnitude_label == "MATERIAL" and direction_label == "REDUCTION_SIGNAL"
    )
    return {
        "factor": name,
        "effect_low": effect_low,
        "effect_high": effect_high,
        "magnitude_label": magnitude_label,
        "direction_label": direction_label,
        "source_language_allowed": source_language_allowed,
        "source_language_rule": (
            "MATERIAL without REDUCTION_SIGNAL is sensitivity, not source attribution."
        ),
        "magnitude_classification": magnitude_detail,
        "direction_classification": direction_detail,
        "metrics": all_metrics,
        "baseline_drift_inputs": beta_inputs,
        "diagnostic_windows": windows,
    }


def _summary_hard_gate_audit(
    summary: Mapping[str, Any],
    labels: Sequence[str],
    runs: Mapping[str, RunEvidence],
    *,
    execution_commit: str,
    bound_source: str,
) -> dict[str, Any]:
    gates: dict[str, bool] = {
        "runner_summary_gate": summary.get("gate_pass") is True,
        "summary_execution_commit": (
            summary.get("identity", {}).get("execution_commit") == execution_commit
        ),
        "summary_source_start_bound": (
            summary.get("source_tree_execution_start", {}).get("value") == bound_source
        ),
        "summary_source_end_bound": (
            summary.get("source_tree_execution_end", {}).get("value") == bound_source
        ),
        "summary_accepted_times_bitwise_equal": (
            summary.get("gates", {}).get("accepted_time_arrays_bitwise_equal") is True
        ),
    }
    per_run: dict[str, Any] = {}
    for label in labels:
        run = runs[label]
        diagnostics = run.metadata.get("diagnostics", {})
        accelerator = (
            diagnostics.get("pressure_accelerator", {})
            if isinstance(diagnostics, Mapping)
            else {}
        )
        source_identity = run.provenance.get("source_identity", {})
        quality = summary.get("quality", {}).get(label, {})
        metrics = quality.get("metrics", {}) if isinstance(quality, Mapping) else {}
        run_gates = {
            "archive_integrity_loader_passed": True,
            "archived_config_exact": run.config == _expected_config(label),
            "archived_time_grid_exact": np.array_equal(
                run.time_s, EXPECTED_OUTPUT_TIME
            ),
            "archived_fields_finite": run.fields_all_finite,
            "archived_observables_finite": all(
                np.isfinite(value).all() for value in run.observables.values()
            ),
            "provenance_source_start_bound": (
                source_identity.get("source_tree_sha256_start") == bound_source
            ),
            "provenance_source_end_bound": (
                source_identity.get("source_tree_sha256_end") == bound_source
            ),
            "cuda_selected": accelerator.get("selected") == "cuda",
            "cuda_no_fallback": accelerator.get("fallback") is False,
            "duration_exact": math.isclose(
                float(diagnostics.get("simulated_duration_s", math.nan)),
                0.010,
                rel_tol=0.0,
                abs_tol=2.0e-15,
            ),
            "pressure_residual": (
                float(diagnostics.get("maximum_pressure_relative_residual", math.inf))
                <= 1.0e-7
            ),
            "projected_divergence": (
                float(diagnostics.get("maximum_projected_divergence_s_inv", math.inf))
                <= 1.0e-4
            ),
            "advective_cfl": (
                float(diagnostics.get("maximum_advective_cfl", math.inf))
                <= 0.35 * (1.0 + 1.0e-12)
            ),
            "phase_clip": (
                float(diagnostics.get("cumulative_phase_clip_relative", math.inf))
                <= 0.001
            ),
            "phase_repair": (
                float(diagnostics.get("cumulative_phase_repair_relative", math.inf))
                <= 0.001
            ),
            "configured_dt_fraction": (
                float(metrics.get("configured_time_step_active_fraction", -math.inf))
                >= 0.95
            ),
            "maximum_volume_balance": (
                float(
                    metrics.get(
                        "maximum_liquid_volume_balance_error_relative",
                        math.inf,
                    )
                )
                <= 0.001
            ),
        }
        per_run[label] = {
            "gate_pass": all(run_gates.values()),
            "gates": run_gates,
            "raw_extrema": {
                "maximum_pressure_relative_residual": diagnostics.get(
                    "maximum_pressure_relative_residual"
                ),
                "maximum_projected_divergence_s_inv": diagnostics.get(
                    "maximum_projected_divergence_s_inv"
                ),
                "maximum_advective_cfl": diagnostics.get("maximum_advective_cfl"),
                "cumulative_phase_clip_relative": diagnostics.get(
                    "cumulative_phase_clip_relative"
                ),
                "cumulative_phase_repair_relative": diagnostics.get(
                    "cumulative_phase_repair_relative"
                ),
                "configured_time_step_active_fraction": metrics.get(
                    "configured_time_step_active_fraction"
                ),
                "maximum_liquid_volume_balance_error_relative": metrics.get(
                    "maximum_liquid_volume_balance_error_relative"
                ),
                "maximum_surface_tension_acceleration_m_s2": metrics.get(
                    "maximum_surface_tension_acceleration_m_s2"
                ),
                "maximum_boundary_acceleration_m_s2": metrics.get(
                    "maximum_boundary_acceleration_m_s2"
                ),
                "maximum_absolute_top_outward_flux_m3_s": metrics.get(
                    "maximum_absolute_top_outward_flux_m3_s"
                ),
            },
            "pressure_accelerator": dict(accelerator),
            "config_canonical_sha256": run.config_canonical_sha256,
        }
    gates["all_archive_hard_gates"] = all(
        item["gate_pass"] for item in per_run.values()
    )
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "per_run": per_run,
    }


def _load_core_evidence() -> tuple[
    dict[str, dict[str, Any]],
    dict[str, RunEvidence],
]:
    summaries: dict[str, dict[str, Any]] = {}
    runs: dict[str, RunEvidence] = {}
    for label in SINGLE_CASES:
        summary, loaded = _load_summary_and_runs(_case_summary_path(label), (label,))
        summaries[label] = summary
        runs.update(loaded)
    r_summary, r_runs = _load_summary_and_runs(
        _group_summary_path("r_pair"), R_PAIR_CASES
    )
    summaries["r_pair"] = r_summary
    runs.update(r_runs)
    return summaries, runs


def preliminary_r_analysis() -> dict[str, Any]:
    if PRELIMINARY_R_PATH.exists():
        raise FileExistsError(PRELIMINARY_R_PATH)
    if FAILURE_PATH.exists():
        raise D2AnalysisError("D2 failure marker exists")
    branch = _git("branch", "--show-current")
    porcelain = _git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    if branch != "main" or porcelain:
        raise D2AnalysisError("preliminary analysis requires a clean main worktree")
    prereg = _json_object(PREREGISTRATION_PATH)
    summaries, runs = _load_core_evidence()
    head = _git("rev-parse", "HEAD")
    bound_source = prereg["evidence_bindings"]["formal_source_tree_sha256"]
    hard_gate_audits = {
        label: _summary_hard_gate_audit(
            summaries[label],
            (label,),
            {label: runs[label]},
            execution_commit=head,
            bound_source=bound_source,
        )
        for label in SINGLE_CASES
    }
    hard_gate_audits["r_pair"] = _summary_hard_gate_audit(
        summaries["r_pair"],
        R_PAIR_CASES,
        {label: runs[label] for label in R_PAIR_CASES},
        execution_commit=head,
        bound_source=bound_source,
    )
    analysis = _factor_analysis(
        "R",
        runs,
        prereg,
        effect_low="R9_BASE",
        effect_high="R9_ALL",
        residual_sign=1,
        baseline_drift_run="R9_BASE",
    )
    gates = {
        "clean_main": branch == "main" and not porcelain,
        "all_core_summaries_pass": all(
            summary.get("gate_pass") is True for summary in summaries.values()
        ),
        "independent_archive_hard_gate_audits": all(
            audit["gate_pass"] for audit in hard_gate_audits.values()
        ),
        "four_primary_metrics": set(
            analysis["magnitude_classification"]["eta_by_primary"]
        )
        == set(PRIMARY),
    }
    report = {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "stage": "S4_PRELIMINARY_R_CLASSIFICATION_FOR_S5_ONLY",
        "status": (
            "D2_PRELIMINARY_R_CLASSIFICATION_PASS"
            if all(gates.values())
            else FAIL_CLOSED
        ),
        "gate_pass": all(gates.values()),
        "gates": gates,
        "execution_commit": head,
        "R_magnitude_label": analysis["magnitude_label"],
        "R_direction_label": analysis["direction_label"],
        "S5_required": should_run_s5(analysis["magnitude_label"]),
        "R_analysis": analysis,
        "hard_gate_audits": hard_gate_audits,
        "scope": (
            "This ignored preliminary result controls only the frozen S5 "
            "conditional and is recomputed by final analysis."
        ),
    }
    _write_json(PRELIMINARY_R_PATH, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "R_magnitude_label": report["R_magnitude_label"],
                "S5_required": report["S5_required"],
                "path": str(PRELIMINARY_R_PATH),
            },
            ensure_ascii=False,
        )
    )
    return report


def _trajectory_bundle(runs: Mapping[str, RunEvidence]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, run in runs.items():
        result[label] = {
            "time_s": run.time_s.tolist(),
            "observables": {
                observable: {
                    "raw": _observable(run, observable).tolist(),
                    "initial_value_removed": initial_removed(
                        _observable(run, observable)
                    ).tolist(),
                }
                for observable in (*PRIMARY, *SECONDARY)
            },
            "inventory_budget": {
                name: run.observables[name].tolist()
                for name in (
                    "liquid_mass_error_relative",
                    "expected_liquid_volume_m3",
                    "expected_liquid_volume_change_relative",
                    "phase_top_outward_flux_m3_s",
                    "liquid_volume_balance_error_relative",
                )
            },
        }
    return result


def _zero_drive_diagnostics(
    runs: Mapping[str, RunEvidence],
    hard_gates: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label in ("Z6", "Z9"):
        run = runs[label]
        rpm = run.observables["actual_bar_rpm"]
        angle = run.observables["bar_angle_rad"]
        result[label] = {
            "prescribed_speed_and_bar_angle_remain_zero": bool(
                np.array_equal(rpm, np.zeros_like(rpm))
                and np.array_equal(angle, np.zeros_like(angle))
            ),
            "output_time_s": run.time_s.tolist(),
            "maximum_laboratory_speed_m_s": run.observables[
                "maximum_laboratory_speed_m_s"
            ].tolist(),
            "rms_laboratory_speed_m_s": run.observables[
                "rms_laboratory_speed_m_s"
            ].tolist(),
            "maximum_surface_tension_acceleration_m_s2_at_outputs": (
                run.observables["maximum_surface_tension_acceleration_m_s2"].tolist()
            ),
            "maximum_boundary_acceleration_m_s2_at_outputs": run.observables[
                "maximum_boundary_acceleration_m_s2"
            ].tolist(),
            "dense_accepted_step_maxima": hard_gates[label]["raw_extrema"],
        }
    return result


def _inventory_analysis(
    runs: Mapping[str, RunEvidence],
    hard_gates: Mapping[str, Any],
) -> dict[str, Any]:
    result = inventory_flux_residual(
        runs["F6"].observables["liquid_mass_error_relative"],
        runs["F9"].observables["liquid_mass_error_relative"],
        runs["F6"].observables["expected_liquid_volume_change_relative"],
        runs["F9"].observables["expected_liquid_volume_change_relative"],
    )
    supporting_gates = {
        "F6_quality": hard_gates["F6"]["gate_pass"],
        "F9_quality": hard_gates["F9"]["gate_pass"],
        "F6_balance": hard_gates["F6"]["gates"]["maximum_volume_balance"],
        "F9_balance": hard_gates["F9"]["gates"]["maximum_volume_balance"],
        "F6_clip": hard_gates["F6"]["gates"]["phase_clip"],
        "F9_clip": hard_gates["F9"]["gates"]["phase_clip"],
        "F6_repair": hard_gates["F6"]["gates"]["phase_repair"],
        "F9_repair": hard_gates["F9"]["gates"]["phase_repair"],
    }
    result["supporting_gates"] = supporting_gates
    result["label"] = (
        "INVENTORY_DIFFERENCE_TOP_BOUNDARY_MEDIATED"
        if result["mediated"] and all(supporting_gates.values())
        else None
    )
    result["all_run_budget_trajectories"] = {
        label: {
            "actual_inventory_change_relative": run.observables[
                "liquid_mass_error_relative"
            ].tolist(),
            "expected_open_boundary_change_relative": run.observables[
                "expected_liquid_volume_change_relative"
            ].tolist(),
            "phase_top_outward_flux_m3_s": run.observables[
                "phase_top_outward_flux_m3_s"
            ].tolist(),
            "volume_balance_error_relative": run.observables[
                "liquid_volume_balance_error_relative"
            ].tolist(),
        }
        for label, run in runs.items()
    }
    return result


def _s5_interaction(
    runs: Mapping[str, RunEvidence],
    prereg: Mapping[str, Any],
) -> dict[str, Any]:
    floors = prereg["observables"]["absolute_scale_floors"]
    result: dict[str, Any] = {}
    for observable in (*PRIMARY, *SECONDARY):
        baseline = initial_removed(_observable(runs["R9_BASE"], observable))
        d_all = initial_removed(_observable(runs["R9_ALL"], observable)) - baseline
        d_if = initial_removed(_observable(runs["R9_IF"], observable)) - baseline
        d_bl = initial_removed(_observable(runs["R9_BL"], observable)) - baseline
        d_st = initial_removed(_observable(runs["R9_ST"], observable)) - baseline
        result[observable] = interaction_kappa(
            d_all,
            d_if,
            d_bl,
            d_st,
            float(floors[observable]),
        )
    return {
        "metrics": result,
        "any_primary_non_additive": any(
            result[name]["non_additive"] for name in PRIMARY
        ),
        "linear_allocation_allowed": not any(
            result[name]["non_additive"] for name in PRIMARY
        ),
    }


def _copy_compact_file(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _publish_compact_evidence(
    summaries: Mapping[str, Mapping[str, Any]],
    runs: Mapping[str, RunEvidence],
    normalized_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    if EVIDENCE_ROOT.exists():
        raise FileExistsError(EVIDENCE_ROOT)
    copied: list[dict[str, Any]] = []
    for label, run in sorted(runs.items()):
        target_root = EVIDENCE_ROOT / "runs" / label
        for name in (
            "config.json",
            "provenance.json",
            "result.json",
            "timeseries.csv",
            "manifest.json",
        ):
            source = run.archive_path / name
            target = target_root / name
            _copy_compact_file(source, target)
            copied.append(
                {
                    "path": _relative(target),
                    "sha256": _sha256(target),
                    "size_bytes": target.stat().st_size,
                }
            )
    for name, summary in sorted(summaries.items()):
        if name in SINGLE_CASES:
            source = _case_summary_path(name)
        elif name == "r_pair":
            source = _group_summary_path("r_pair")
        elif name == "r_multiway":
            source = _group_summary_path("r_multiway")
        else:
            continue
        target = EVIDENCE_ROOT / "summaries" / f"{name}.json"
        _copy_compact_file(source, target)
        copied.append(
            {
                "path": _relative(target),
                "sha256": _sha256(target),
                "size_bytes": target.stat().st_size,
            }
        )
    if RAW_LEDGER_PATH.is_file():
        target = EVIDENCE_ROOT / "raw_stage_ledger.json"
        _copy_compact_file(RAW_LEDGER_PATH, target)
        copied.append(
            {
                "path": _relative(target),
                "sha256": _sha256(target),
                "size_bytes": target.stat().st_size,
            }
        )
    _write_json(TRACKED_LEDGER, normalized_ledger)
    copied.append(
        {
            "path": _relative(TRACKED_LEDGER),
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
    raw_files = []
    for path in sorted(item for item in RAW_ROOT.rglob("*") if item.is_file()):
        raw_files.append(
            {
                "path": _relative(path),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
                "tracked": False,
            }
        )
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "integrity_scope": (
            "Ignored raw D2 products and tracked compact evidence available "
            "at final analysis time."
        ),
        "raw_file_count": len(raw_files),
        "raw_total_size_bytes": sum(item["size_bytes"] for item in raw_files),
        "raw_files": raw_files,
        "compact_tracked_evidence": dict(compact),
        "authenticity_limit": (
            "SHA-256 detects mutation against this manifest but is not an "
            "external signature or an independent rerun."
        ),
    }


def _normalized_stage_ledger(
    *,
    s5_required: bool,
    s5_executed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "execution_commit": _git("rev-parse", "HEAD"),
        "stages": [
            {
                "stage": "S0",
                "disposition": "EXECUTED",
                "status": "PASS",
                "evidence": _relative(S0_PATH),
            },
            {
                "stage": "S1",
                "disposition": "EXECUTED",
                "status": "PASS",
                "cases": ["F6", "F9"],
            },
            {
                "stage": "S2",
                "disposition": "EXECUTED",
                "status": "PASS",
                "cases": ["Z6", "Z9"],
            },
            {
                "stage": "S3",
                "disposition": "EXECUTED",
                "status": "PASS",
                "cases": ["T9"],
            },
            {
                "stage": "S4",
                "disposition": "EXECUTED",
                "status": "PASS",
                "cases": list(R_PAIR_CASES),
            },
            {
                "stage": "S5",
                "disposition": ("EXECUTED" if s5_executed else "CONDITIONAL_SKIPPED"),
                "status": "PASS" if s5_executed else "SKIPPED_BY_FROZEN_RULE",
                "condition_required": s5_required,
                "cases": list(R_MULTIWAY_CASES) if s5_executed else [],
            },
            {
                "stage": "S6",
                "disposition": "EXECUTED_IN_FINAL_ANALYSIS",
                "status": "PASS",
            },
            {
                "stage": "S7",
                "disposition": "EXECUTED",
                "status": "PASS",
            },
        ],
        "failed_stages": [],
        "unexecuted_stages": [],
        "automatic_follow_on": "PROHIBITED",
    }


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# D2 HF3D 数值误差来源归因 V2 报告",
        "",
        "## 裁决",
        "",
        "~~~text",
        str(report["status"]),
        "~~~",
        "",
        f"执行提交：{report['identity']['execution_commit']}。",
        "",
        "D2 的结论只用于当前正式源码、正式 G0、6/9 格层和 0–0.010 s 窗口内的失败机制筛查。",
        "",
        "## 三类因素标签",
        "",
        "| 因素 | 大小标签 | 方向标签 | 可使用来源语言 |",
        "|---|---|---|---|",
    ]
    for name in ("Z", "T", "R"):
        item = report["factor_analysis"][name]
        lines.append(
            f"| {name} | {item['magnitude_label']} | "
            f"{item['direction_label']} | "
            f"{'是' if item['source_language_allowed'] else '否'} |"
        )
    lines.extend(
        [
            "",
            "MATERIAL 但没有 REDUCTION_SIGNAL 只表示结果敏感，不构成空间失败来源归因。",
            "",
            "## 主要量 eta 与 rho",
            "",
            "| 因素 | 观测量 | eta | rho |",
            "|---|---|---:|---:|",
        ]
    )
    for factor in ("Z", "T", "R"):
        metrics = report["factor_analysis"][factor]["metrics"]
        for observable in PRIMARY:
            lines.append(
                f"| {factor} | {observable} | "
                f"{metrics[observable]['eta']:.6g} | "
                f"{metrics[observable]['rho']:.6g} |"
            )
    lines.extend(
        [
            "",
            "## 条件阶段与存量预算",
            "",
            f"S5：{report['conditional_S5']['disposition']}。",
            "",
            (
                f"开放顶边界预算 r_flux = "
                f"{report['inventory_boundary_analysis']['r_flux']:.6g}；"
                f"标签：{report['inventory_boundary_analysis']['label'] or '无'}。"
            ),
            "",
            "该存量标签不证明开放边界条件错误，也不等价于数值不守恒。",
            "",
            "## 硬门",
            "",
            "| 证据组 | 结果 |",
            "|---|---|",
        ]
    )
    for name, audit in report["hard_gate_audits"].items():
        lines.append(f"| {name} | {'PASS' if audit['gate_pass'] else 'FAIL'} |")
    lines.extend(
        [
            "",
            "## 未解决混杂与结论上限",
            "",
            "- 正式接触角作用带仍为 1.5 倍最大网格间距，R 不覆盖这一路径；",
            "- D2 不撤销 Q1R3 空间失败，不证明空间或时间收敛；",
            "- 未修改或提升正式 HF3D；",
            "- 未执行物理验证或实验验证；",
            "- 不允许长期稳态、临界转速或装置级预测；",
            "- 报告后停止，等待 Pro 复核。",
            "",
            "## 远端可复核证据",
            "",
            f"跟踪清单：{_relative(ARTIFACT_MANIFEST)}。",
            "",
            f"阶段台账：{_relative(TRACKED_LEDGER)}。",
            "",
        ]
    )
    return "\n".join(lines)


def analyse_final() -> dict[str, Any]:
    if any(
        path.exists()
        for path in (REPORT_JSON, REPORT_MD, ARTIFACT_MANIFEST, EVIDENCE_ROOT)
    ):
        raise FileExistsError("D2 tracked final outputs already exist")
    if FAILURE_PATH.exists():
        raise D2AnalysisError("D2 execution failure marker exists")
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    prereg = _json_object(PREREGISTRATION_PATH)
    source = dict(source_tree_identity(FORMAL_ROOT))
    bound_source = prereg["evidence_bindings"]["formal_source_tree_sha256"]
    if branch != "main" or porcelain:
        raise D2AnalysisError("final analysis requires a clean main worktree")
    if source.get("value") != bound_source:
        raise D2AnalysisError("formal source tree no longer matches D2 binding")
    s0 = _json_object(S0_PATH)
    if s0.get("gate_pass") is not True:
        raise D2AnalysisError("S0 did not pass")

    summaries, runs = _load_core_evidence()
    hard_gate_audits: dict[str, Any] = {}
    for name in SINGLE_CASES:
        hard_gate_audits[name] = _summary_hard_gate_audit(
            summaries[name],
            (name,),
            {name: runs[name]},
            execution_commit=head,
            bound_source=bound_source,
        )
    hard_gate_audits["r_pair"] = _summary_hard_gate_audit(
        summaries["r_pair"],
        R_PAIR_CASES,
        {name: runs[name] for name in R_PAIR_CASES},
        execution_commit=head,
        bound_source=bound_source,
    )
    if not all(item["gate_pass"] for item in hard_gate_audits.values()):
        raise D2AnalysisError("at least one core archive hard-gate audit failed")

    factors = {
        "Z": _factor_analysis(
            "Z",
            runs,
            prereg,
            effect_low="Z6",
            effect_high="Z9",
            residual_sign=-1,
        ),
        "T": _factor_analysis(
            "T",
            runs,
            prereg,
            effect_low="F9",
            effect_high="T9",
            residual_sign=1,
        ),
        "R": _factor_analysis(
            "R",
            runs,
            prereg,
            effect_low="R9_BASE",
            effect_high="R9_ALL",
            residual_sign=1,
            baseline_drift_run="R9_BASE",
        ),
    }
    preliminary = _json_object(PRELIMINARY_R_PATH)
    if (
        preliminary.get("gate_pass") is not True
        or preliminary.get("R_magnitude_label") != factors["R"]["magnitude_label"]
        or preliminary.get("R_direction_label") != factors["R"]["direction_label"]
    ):
        raise D2AnalysisError("final R classification differs from S5 decision input")
    s5_required = should_run_s5(factors["R"]["magnitude_label"])
    s5_summary_path = _group_summary_path("r_multiway")
    interaction: dict[str, Any] | None = None
    s5_executed = False
    if s5_required:
        s5_summary, s5_runs = _load_summary_and_runs(
            s5_summary_path,
            R_MULTIWAY_CASES,
        )
        summaries["r_multiway"] = s5_summary
        hard_gate_audits["r_multiway"] = _summary_hard_gate_audit(
            s5_summary,
            R_MULTIWAY_CASES,
            s5_runs,
            execution_commit=head,
            bound_source=bound_source,
        )
        if not hard_gate_audits["r_multiway"]["gate_pass"]:
            raise D2AnalysisError("S5 multiway hard-gate audit failed")
        interaction = _s5_interaction(s5_runs, prereg)
        runs.update({f"S5_{name}": run for name, run in s5_runs.items()})
        s5_executed = True
    elif s5_summary_path.exists():
        raise D2AnalysisError("S5 archive exists although R magnitude is SMALL")

    flattened_hard_gates: dict[str, Any] = {}
    for audit in hard_gate_audits.values():
        flattened_hard_gates.update(audit["per_run"])
    inventory = _inventory_analysis(runs, flattened_hard_gates)
    zero = _zero_drive_diagnostics(runs, flattened_hard_gates)
    normalized_ledger = _normalized_stage_ledger(
        s5_required=s5_required,
        s5_executed=s5_executed,
    )
    compact = _publish_compact_evidence(summaries, runs, normalized_ledger)
    manifest = _raw_manifest(compact)
    _write_json(ARTIFACT_MANIFEST, manifest)
    config_differences = {
        summary_name: {
            label: {
                "allowed_overrides": identity.get("allowed_overrides"),
                "observed_differences": identity.get("observed_differences"),
                "gate_pass": identity.get("gate_pass"),
            }
            for label, identity in summary.get("config_identity", {}).items()
        }
        for summary_name, summary in summaries.items()
    }

    labels = {
        name: {
            "magnitude": factors[name]["magnitude_label"],
            "direction": factors[name]["direction_label"],
        }
        for name in ("Z", "T", "R")
    }
    status = (
        "D2_SOURCE_ATTRIBUTION_COMPLETE"
        f"__Z_{labels['Z']['magnitude']}_{labels['Z']['direction']}"
        f"__T_{labels['T']['magnitude']}_{labels['T']['direction']}"
        f"__R_{labels['R']['magnitude']}_{labels['R']['direction']}"
        "__STOP_FOR_PRO_REVIEW"
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "status": status,
        "gate_pass": True,
        "labels": labels,
        "factor_analysis": factors,
        "conditional_S5": {
            "required": s5_required,
            "executed": s5_executed,
            "disposition": (
                "EXECUTED" if s5_executed else "CONDITIONAL_SKIPPED_R_MAGNITUDE_SMALL"
            ),
            "interaction_analysis": interaction,
        },
        "zero_drive_diagnostics": zero,
        "inventory_boundary_analysis": inventory,
        "classification_trajectories": _trajectory_bundle(runs),
        "config_differences": config_differences,
        "hard_gate_audits": hard_gate_audits,
        "identity": {
            "branch": branch,
            "execution_commit": head,
            "worktree_porcelain_at_analysis_start": porcelain,
            "formal_source_tree": source,
            "bound_formal_source_tree_sha256": bound_source,
            "contract_path": _relative(PREREGISTRATION_PATH),
            "contract_content_sha256": _sha256(PREREGISTRATION_PATH),
        },
        "stage_ledger": normalized_ledger,
        "artifact_manifest": {
            "path": _relative(ARTIFACT_MANIFEST),
            "raw_file_count": manifest["raw_file_count"],
            "raw_total_size_bytes": manifest["raw_total_size_bytes"],
            "compact_tracked_file_count": compact["file_count"],
            "compact_tracked_total_size_bytes": compact["total_size_bytes"],
        },
        "unresolved_confounders": prereg["unresolved_confounders"],
        "claim_boundary": prereg["claim_boundary"],
        "conclusion_ceiling": {
            "q1r3_failure_overturned": False,
            "spatial_convergence_established": False,
            "temporal_convergence_established": False,
            "numerical_qualification_established": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "formal_solver_changed": False,
            "apparatus_prediction_allowed": False,
        },
        "stop_point": "REPORT_THEN_STOP_FOR_PRO_REVIEW",
    }
    _write_json(REPORT_JSON, report)
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(_markdown(report), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "preliminary-r",
        help="classify R only to resolve the frozen S5 conditional",
    )
    subparsers.add_parser(
        "final",
        help="independently recompute all classifications and publish evidence",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "preliminary-r":
        report = preliminary_r_analysis()
    elif args.command == "final":
        report = analyse_final()
    else:
        raise AssertionError(args.command)
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": (
                    str(PRELIMINARY_R_PATH)
                    if args.command == "preliminary-r"
                    else str(REPORT_JSON)
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
