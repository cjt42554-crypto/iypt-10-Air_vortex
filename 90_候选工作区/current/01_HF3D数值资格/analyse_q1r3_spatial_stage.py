"""Bind the Q1R3 spatial-stage decision to frozen and archived evidence."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from hfq_campaign import capillary_time_step_limit
from hfq_convergence import convergence_estimate, gate_convergence
from hfq_grid import grid_audit, make_refinement_config
from hfq_json import canonical_sha256
from hfq_types import SeriesData

SPATIAL_CASE_IDS = (
    "space_L0_4cells",
    "space_L1_6cells",
    "space_L2_9cells",
)
TEMPORAL_REUSE_LOGICAL_CASE_ID = "time_L0_5.500e-06s"
TEMPORAL_NEW_CASE_IDS = (
    "time_L1_2.750e-06s",
    "time_L2_1.375e-06s",
)
EXPECTED_LOGICAL_CASE_IDS = (
    *SPATIAL_CASE_IDS,
    TEMPORAL_REUSE_LOGICAL_CASE_ID,
    *TEMPORAL_NEW_CASE_IDS,
)
EXPECTED_EARLY_STOP_CONDITION = (
    "ANY_SPATIAL_CONFIGURATION_QUALITY_EFFECTIVE_STEP_"
    "OR_CONVERGENCE_GATE_FAILS"
)
EXPECTED_EARLY_STOP_ACTION = "DO_NOT_EXECUTE_TEMPORAL_NEW_CASES"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"missing mapping: {label}")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"missing list: {label}")
    return value


def _finite_close(left: Any, right: Any) -> bool:
    try:
        left_value = float(left)
        right_value = float(right)
    except (TypeError, ValueError):
        return False
    return bool(
        math.isfinite(left_value)
        and math.isfinite(right_value)
        and math.isclose(
            left_value,
            right_value,
            rel_tol=0.0,
            abs_tol=max(1.0e-15, abs(right_value) * 1.0e-12),
        )
    )


def _archive_inventory(archive_path: Path) -> dict[str, Any]:
    manifest_path = archive_path / "manifest.json"
    manifest = _read_json(manifest_path)
    files = _mapping(manifest.get("files"), f"{manifest_path}: files")
    payloads: dict[str, dict[str, Any]] = {}
    for relative_name, raw_record in files.items():
        record = _mapping(raw_record, f"{manifest_path}: {relative_name}")
        payload_path = archive_path / str(relative_name)
        exists = payload_path.is_file()
        actual_size = payload_path.stat().st_size if exists else None
        actual_sha256 = _sha256(payload_path) if exists else None
        expected_size = record.get("size_bytes")
        expected_sha256 = record.get("sha256")
        payloads[str(relative_name)] = {
            "expected_size_bytes": expected_size,
            "actual_size_bytes": actual_size,
            "expected_sha256": expected_sha256,
            "actual_sha256": actual_sha256,
            "pass": bool(
                exists
                and actual_size == expected_size
                and actual_sha256 == expected_sha256
            ),
        }
    config_record = _mapping(
        files.get("config.json"), f"{manifest_path}: config.json"
    )
    config = _read_json(archive_path / "config.json")
    return {
        "archive_path": str(archive_path),
        "manifest_sha256": _sha256(manifest_path),
        "config_payload_sha256": config_record.get("sha256"),
        "config_canonical_sha256": canonical_sha256(config),
        "payloads": payloads,
        "payload_integrity_valid": bool(payloads)
        and all(record["pass"] for record in payloads.values()),
    }


def _component_pass(record: Mapping[str, Any]) -> bool:
    return record.get("pass") is True


def _read_timeseries(
    path: Path,
    names: tuple[str, ...],
) -> dict[str, np.ndarray]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if len(rows) < 2:
        raise ValueError(f"timeseries has fewer than two rows: {path}")
    required = ("time_s", "actual_bar_rpm", *names)
    missing = [name for name in required if name not in rows[0]]
    if missing:
        raise ValueError(f"timeseries lacks columns {missing}: {path}")
    result = {
        name: np.asarray([float(row[name]) for row in rows], dtype=np.float64)
        for name in required
    }
    time_s = result["time_s"]
    if not (
        np.all(np.isfinite(time_s))
        and np.all(np.diff(time_s) > 0.0)
        and all(np.all(np.isfinite(values)) for values in result.values())
    ):
        raise ValueError(f"timeseries contains invalid values: {path}")
    return result


def _window_series(
    data: Mapping[str, np.ndarray],
    name: str,
    start_s: float,
    end_s: float,
) -> SeriesData:
    time_s = np.asarray(data["time_s"], dtype=np.float64)
    mask = (time_s >= start_s - 1.0e-12) & (time_s <= end_s + 1.0e-12)
    if int(np.count_nonzero(mask)) < 2:
        raise ValueError(
            f"diagnostic window {start_s:g}--{end_s:g} s has fewer than two samples"
        )
    return SeriesData(
        time_s=time_s[mask],
        values=np.asarray(data[name], dtype=np.float64)[mask],
    )


def _sample_at(
    data: Mapping[str, np.ndarray],
    name: str,
    target_s: float,
) -> float:
    time_s = np.asarray(data["time_s"], dtype=np.float64)
    index = int(np.argmin(np.abs(time_s - target_s)))
    if not math.isclose(
        float(time_s[index]), target_s, rel_tol=0.0, abs_tol=1.0e-12
    ):
        raise ValueError(f"no diagnostic sample at {target_s:g} s")
    return float(np.asarray(data[name], dtype=np.float64)[index])


def _first_absolute_threshold_time(
    data: Mapping[str, np.ndarray],
    name: str,
    threshold: float,
) -> float | None:
    values = np.abs(np.asarray(data[name], dtype=np.float64))
    indices = np.flatnonzero(values >= threshold)
    if indices.size == 0:
        return None
    return float(np.asarray(data["time_s"], dtype=np.float64)[int(indices[0])])


def _posthoc_failure_diagnostic(
    *,
    campaign_manifest: Mapping[str, Any],
    campaign_manifest_path: Path,
    qualification: Mapping[str, Any],
    run_root: Path,
) -> dict[str, Any]:
    observables = _mapping(
        campaign_manifest.get("observables"), "campaign observables"
    )
    smooth_names = tuple(
        str(item) for item in _list(observables.get("smooth"), "smooth observables")
    )
    gates = _mapping(campaign_manifest.get("gates"), "campaign gates")
    smooth_gate = _mapping(gates.get("smooth_convergence"), "smooth gate")
    floors = _mapping(smooth_gate.get("absolute_scale_floors"), "scale floors")
    campaign_cases = _list(campaign_manifest.get("cases"), "campaign cases")
    spatial_cases = sorted(
        (
            item
            for item in campaign_cases
            if isinstance(item, Mapping) and item.get("group") == "spatial"
        ),
        key=lambda item: int(item.get("level", -1)),
    )
    if [str(item.get("id")) for item in spatial_cases] != list(
        SPATIAL_CASE_IDS
    ):
        raise ValueError("diagnostic requires the frozen three spatial cases")
    coordinates = [
        float(item["effective_refinement_coordinate_m"])
        for item in spatial_cases
    ]
    series = {
        case_id: _read_timeseries(
            run_root / case_id / "timeseries.csv", smooth_names
        )
        for case_id in SPATIAL_CASE_IDS
    }
    reference_time = series[SPATIAL_CASE_IDS[0]]["time_s"]
    common_time_grid = all(
        np.array_equal(reference_time, series[case_id]["time_s"])
        for case_id in SPATIAL_CASE_IDS[1:]
    )

    fine_case = spatial_cases[-1]
    fine_config_path = campaign_manifest_path.parent / str(
        fine_case["config_file"]
    )
    fine_config = _read_json(fine_config_path)
    drive = _mapping(fine_config.get("drive"), "fine drive")
    run = _mapping(fine_config.get("run"), "fine run")
    ramp_end_s = float(drive["ramp_duration_s"])
    run_end_s = float(run["duration_s"])
    steady_width = (run_end_s - ramp_end_s) / 3.0
    target_rpm = float(drive["target_rpm"])
    constant_rpm_windows_verified = all(
        np.allclose(
            data["actual_bar_rpm"][data["time_s"] >= ramp_end_s - 1.0e-12],
            target_rpm,
            rtol=0.0,
            atol=max(1.0e-12, abs(target_rpm) * 1.0e-12),
        )
        for data in series.values()
    )
    windows = [
        ("acceleration_ramp", 0.0, ramp_end_s),
        ("constant_rpm_early", ramp_end_s, ramp_end_s + steady_width),
        (
            "constant_rpm_middle",
            ramp_end_s + steady_width,
            ramp_end_s + 2.0 * steady_width,
        ),
        ("constant_rpm_late", ramp_end_s + 2.0 * steady_width, run_end_s),
    ]
    window_results: dict[str, dict[str, Any]] = {}
    for label, start_s, end_s in windows:
        observable_results: dict[str, dict[str, Any]] = {}
        for name in smooth_names:
            estimate = convergence_estimate(
                coarse=_window_series(
                    series[SPATIAL_CASE_IDS[0]], name, start_s, end_s
                ),
                medium=_window_series(
                    series[SPATIAL_CASE_IDS[1]], name, start_s, end_s
                ),
                fine=_window_series(
                    series[SPATIAL_CASE_IDS[2]], name, start_s, end_s
                ),
                h_coarse=coordinates[0],
                h_medium=coordinates[1],
                h_fine=coordinates[2],
                absolute_scale_floor=float(floors[name]),
                safety_factor=float(smooth_gate["gci_safety_factor"]),
            )
            gated = gate_convergence(
                estimate,
                relative_l2_max=float(smooth_gate["relative_l2_max"]),
                relative_linf_max=float(smooth_gate["relative_linf_max"]),
                gci_l2_max=float(smooth_gate["gci_l2_max"]),
                observed_order_min=float(smooth_gate["observed_order_min"]),
            )
            estimate_dict = estimate.to_dict()
            fine_medium = _mapping(
                estimate_dict.get("pair_fine_medium"),
                f"diagnostic fine-medium: {name}",
            )
            observable_results[name] = {
                "pass_against_frozen_thresholds": gated.get("pass") is True,
                "relative_l2_fine_medium": fine_medium.get("relative_l2"),
                "relative_linf_fine_medium": fine_medium.get("relative_linf"),
                "observed_order": estimate_dict.get("observed_order"),
                "fine_grid_gci_l2": estimate_dict.get("fine_grid_gci_l2"),
                "reason": estimate_dict.get("reason"),
            }
        pass_count = sum(
            item["pass_against_frozen_thresholds"]
            for item in observable_results.values()
        )
        window_results[label] = {
            "start_s": start_s,
            "end_s": end_s,
            "pass_count": pass_count,
            "observable_count": len(smooth_names),
            "all_smooth_observables_pass": pass_count == len(smooth_names),
            "observables": observable_results,
        }

    key_names = (
        "kinetic_energy_lab_j",
        "angular_momentum_z_kg_m2_s",
        "axisymmetry_error",
        "funnel_depth_m",
        "min_gap_to_bar_m",
        "liquid_mass_error_relative",
    )
    endpoint_values = {
        case_id: {
            "at_ramp_end": {
                name: _sample_at(series[case_id], name, ramp_end_s)
                for name in key_names
            },
            "at_run_end": {
                name: _sample_at(series[case_id], name, run_end_s)
                for name in key_names
            },
        }
        for case_id in SPATIAL_CASE_IDS
    }
    inventory_onset = {
        case_id: {
            f"absolute_{threshold:.0e}": _first_absolute_threshold_time(
                series[case_id],
                "liquid_mass_error_relative",
                threshold,
            )
            for threshold in (1.0e-6, 1.0e-4, 1.0e-3)
        }
        for case_id in SPATIAL_CASE_IDS
    }

    run_audits = _mapping(qualification.get("run_audits"), "run audits")
    fine_audit = _mapping(
        run_audits.get(SPATIAL_CASE_IDS[-1]), "fine run audit"
    )
    fine_metrics = _mapping(
        fine_audit.get("execution_metrics"), "fine execution metrics"
    )
    fine_grid = grid_audit(fine_config)
    resource_indicators = []
    for target in (13.5, 20.25):
        projected_config = make_refinement_config(
            fine_config,
            target_bar_cells_worst_axis=target,
            time_step_s=float(
                _mapping(
                    fine_config.get("high_fidelity"), "fine high fidelity"
                )["time_step_s"]
            ),
            round_to_multiple=2,
        )
        projected = grid_audit(projected_config)
        cell_ratio = projected.box_cell_count / fine_grid.box_cell_count
        capillary_ceiling_s = capillary_time_step_limit(projected_config)
        configured_dt_s = float(
            _mapping(
                fine_config.get("high_fidelity"), "fine high fidelity"
            )["time_step_s"]
        )
        static_step_multiplier = max(1.0, configured_dt_s / capillary_ceiling_s)
        resource_indicators.append(
            {
                "target_bar_diameter_cells_worst_axis": target,
                "grid_nx_ny_nz": [projected.nx, projected.ny, projected.nz],
                "box_cell_count": projected.box_cell_count,
                "cell_ratio_relative_to_q1r3_fine": cell_ratio,
                "static_capillary_time_step_ceiling_s": capillary_ceiling_s,
                "q1r3_common_time_step_statically_active": bool(
                    configured_dt_s <= capillary_ceiling_s
                ),
                "linear_cpu_rss_indicator_bytes": (
                    float(fine_metrics["peak_resident_set_size_bytes"])
                    * cell_ratio
                ),
                "linear_wall_clock_indicator_s": (
                    float(fine_metrics["wall_clock_duration_s"])
                    * cell_ratio
                ),
                "static_step_adjusted_linear_wall_clock_indicator_s": (
                    float(fine_metrics["wall_clock_duration_s"])
                    * cell_ratio
                    * static_step_multiplier
                ),
            }
        )

    late = window_results["constant_rpm_late"]
    late_failed = [
        name
        for name, item in late["observables"].items()
        if not item["pass_against_frozen_thresholds"]
    ]
    numerical_quality = _mapping(
        qualification.get("numerical_quality"), "numerical quality"
    )
    all_quality_pass = all(
        _mapping(numerical_quality[case_id], f"quality: {case_id}").get("pass")
        is True
        for case_id in SPATIAL_CASE_IDS
    )
    fine_inventory = abs(
        endpoint_values[SPATIAL_CASE_IDS[-1]]["at_run_end"][
            "liquid_mass_error_relative"
        ]
    )
    medium_inventory = abs(
        endpoint_values[SPATIAL_CASE_IDS[1]]["at_run_end"][
            "liquid_mass_error_relative"
        ]
    )
    coarse_inventory = abs(
        endpoint_values[SPATIAL_CASE_IDS[0]]["at_run_end"][
            "liquid_mass_error_relative"
        ]
    )
    return {
        "status": (
            "Q1R3_SPATIAL_FAILURE_MECHANISM_NOT_UNIQUELY_IDENTIFIED__"
            "HUMAN_SCIENTIFIC_AND_RESOURCE_DECISION_REQUIRED"
        ),
        "diagnostic_only_not_a_frozen_gate": True,
        "common_output_time_grid": common_time_grid,
        "constant_rpm_windows_verified": constant_rpm_windows_verified,
        "target_rpm": target_rpm,
        "ramp_end_s": ramp_end_s,
        "run_end_s": run_end_s,
        "window_results": window_results,
        "endpoint_values": endpoint_values,
        "inventory_change_onset_s": inventory_onset,
        "late_constant_rpm_failed_smooth_observables": late_failed,
        "cause_assessment": {
            "archive_or_source_identity_failure_supported": False,
            "registered_single_case_quality_failure_supported": not all_quality_pass,
            "failure_confined_to_acceleration_ramp_supported": bool(
                all(
                    window_results[label]["all_smooth_observables_pass"]
                    for label in (
                        "constant_rpm_early",
                        "constant_rpm_middle",
                        "constant_rpm_late",
                    )
                )
            ),
            "topology_event_trigger_supported": False,
            "non_asymptotic_grid_response_supported": bool(late_failed),
            "grid_dependent_open_boundary_inventory_response_supported": bool(
                fine_inventory > medium_inventory > coarse_inventory
                and fine_inventory >= 1.0e-3
            ),
            "numerical_nonconservation_supported": False,
            "unique_dominant_mechanism_identified": False,
        },
        "unresolved_mechanism_candidates": [
            "bar_and_brinkman_forcing_resolution",
            "diffuse_interface_and_boundary_layer_regularization",
            "open_boundary_domain_response",
            "short_transient_observation_protocol",
            "continuous_model_inadequacy",
        ],
        "next_equal_ratio_resource_indicators": resource_indicators,
        "resource_indicator_boundary": (
            "Cell-count-linear CPU RSS and static-capillary-step-adjusted wall-clock "
            "indicators only; they are not GPU-memory measurements, dynamic-limiter "
            "predictions, or feasibility guarantees."
        ),
        "next_decision_boundary": {
            "automatic_q1r4_freeze_allowed": False,
            "human_choice_required_between": [
                "same_model_shifted_6_9_13_5_path_with_new_common_step_and_all_runs",
                "same_model_finer_9_13_5_20_25_path_with_resource_preflight",
                "changed_startup_or_observation_protocol",
                "changed_numerical_regularization_boundary_or_model_identity",
            ],
            "all_paths_require_new_preregistration": True,
        },
    }


def assess(
    freeze_report: Mapping[str, Any],
    campaign_manifest: Mapping[str, Any],
    qualification: Mapping[str, Any],
    *,
    freeze_report_sha256: str,
    campaign_manifest_file_sha256: str,
    qualification_report_sha256: str,
    archive_inventory: Mapping[str, Mapping[str, Any]],
    temporal_new_case_paths_absent: Mapping[str, bool],
    expected_source_sha256: str,
) -> dict[str, Any]:
    freeze_inputs = _mapping(freeze_report.get("inputs"), "freeze inputs")
    frozen_identity = _mapping(
        freeze_report.get("frozen_identity"), "frozen identity"
    )
    protocol = _mapping(
        freeze_report.get("execution_protocol"), "freeze execution protocol"
    )
    stage_1 = _mapping(protocol.get("stage_1"), "freeze stage 1")
    stage_2 = _mapping(protocol.get("stage_2"), "freeze stage 2")
    early_stop = _mapping(protocol.get("early_stop"), "freeze early stop")

    campaign_cases = _list(campaign_manifest.get("cases"), "campaign cases")
    campaign_case_ids = [
        str(item.get("id"))
        for item in campaign_cases
        if isinstance(item, Mapping)
    ]
    observables = _mapping(
        campaign_manifest.get("observables"), "campaign observables"
    )
    expected_smooth = tuple(
        str(item) for item in _list(observables.get("smooth"), "smooth observables")
    )
    expected_binary = tuple(
        str(item) for item in _list(observables.get("binary"), "binary observables")
    )
    campaign_quality_contract = _mapping(
        campaign_manifest.get("quality_contract_audit"),
        "campaign quality contract",
    )

    groups = _mapping(qualification.get("groups"), "qualification groups")
    spatial = _mapping(groups.get("spatial"), "groups.spatial")
    temporal = _mapping(groups.get("temporal"), "groups.temporal")
    configuration = _mapping(
        spatial.get("configuration_gate"), "spatial configuration gate"
    )
    configuration_checks = _mapping(
        configuration.get("checks"), "spatial configuration checks"
    )
    effective = _mapping(
        spatial.get("effective_time_step_gate"),
        "spatial effective time-step gate",
    )
    effective_cases = _list(
        effective.get("cases"), "spatial effective time-step cases"
    )
    effective_by_id = {
        str(item.get("id")): item
        for item in effective_cases
        if isinstance(item, Mapping)
    }
    smooth = _mapping(
        spatial.get("smooth_observables"), "spatial smooth observables"
    )
    binary = _mapping(
        spatial.get("binary_observables"), "spatial binary observables"
    )

    run_audits = _mapping(qualification.get("run_audits"), "run audits")
    numerical_quality = _mapping(
        qualification.get("numerical_quality"), "numerical quality"
    )
    qualification_quality_contract = _mapping(
        qualification.get("quality_contract_audit"),
        "qualification quality contract",
    )
    refinement_design = _mapping(
        qualification.get("refinement_design_audit"),
        "qualification refinement design",
    )
    coverage = _mapping(
        qualification.get("execution_identity_coverage"),
        "execution identity coverage",
    )
    claim_boundary = _mapping(
        qualification.get("claim_boundary"), "qualification claim boundary"
    )
    missing_cases = _list(qualification.get("missing_cases"), "missing cases")
    load_errors = _mapping(qualification.get("load_errors"), "load errors")
    terminal_failures = _mapping(
        qualification.get("terminal_failures"), "terminal failures"
    )

    spatial_identity_by_case: dict[str, bool] = {}
    for case_id in SPATIAL_CASE_IDS:
        audit = _mapping(run_audits.get(case_id), f"run audit: {case_id}")
        diagnostics = _mapping(
            audit.get("diagnostics"), f"run diagnostics: {case_id}"
        )
        inventory = _mapping(
            archive_inventory.get(case_id), f"archive inventory: {case_id}"
        )
        spatial_identity_by_case[case_id] = bool(
            audit.get("completed") is True
            and audit.get("result_status") == "completed"
            and _finite_close(audit.get("actual_end_s"), audit.get("requested_end_s"))
            and audit.get("execution_source_identity_valid") is True
            and audit.get("source_tree_sha256_start") == expected_source_sha256
            and audit.get("physical_signature_match") is True
            and audit.get("physical_signature_sha256")
            == frozen_identity.get("physical_signature_sha256")
            and audit.get("generated_config_sha256")
            == audit.get("archived_config_sha256")
            == inventory.get("config_canonical_sha256")
            and inventory.get("payload_integrity_valid") is True
            and diagnostics.get("pressure_accelerator_selected") == "cuda"
            and diagnostics.get("pressure_accelerator_fallback") is False
        )

    configuration_pass = bool(
        configuration.get("pass") is True
        and configuration_checks
        and all(
            _component_pass(_mapping(record, f"configuration check: {name}"))
            for name, record in configuration_checks.items()
        )
    )
    effective_pass = bool(
        effective.get("group") == "spatial"
        and effective.get("pass") is True
        and effective.get("registered_structure_valid") is True
        and effective.get("effective_structure_valid") is True
        and set(effective_by_id) == set(SPATIAL_CASE_IDS)
        and all(
            _mapping(effective_by_id[case_id], f"effective case: {case_id}").get(
                "pass"
            )
            is True
            for case_id in SPATIAL_CASE_IDS
        )
    )

    quality_by_case: dict[str, bool] = {}
    for case_id in SPATIAL_CASE_IDS:
        quality = _mapping(
            numerical_quality.get(case_id), f"numerical quality: {case_id}"
        )
        checks = _mapping(quality.get("checks"), f"quality checks: {case_id}")
        contract = _mapping(
            quality.get("quality_contract"), f"quality contract: {case_id}"
        )
        quality_by_case[case_id] = bool(
            quality.get("pass") is True
            and quality.get("backend_fail_closed_gate_pass") is True
            and checks
            and all(
                _component_pass(_mapping(record, f"quality check: {name}"))
                for name, record in checks.items()
            )
            and contract.get("pass") is True
            and contract.get("contract_id")
            == campaign_quality_contract.get("contract_id")
        )
    numerical_quality_pass = all(quality_by_case.values())

    smooth_pass_by_observable = {
        name: bool(
            _mapping(smooth.get(name), f"smooth observable: {name}").get("pass")
            is True
        )
        for name in expected_smooth
    }
    binary_pass_by_observable = {
        name: bool(
            _mapping(binary.get(name), f"binary observable: {name}").get("pass")
            is True
        )
        for name in expected_binary
    }
    smooth_convergence_pass = bool(
        set(smooth) == set(expected_smooth)
        and all(smooth_pass_by_observable.values())
    )
    binary_consistency_pass = bool(
        set(binary) == set(expected_binary)
        and all(binary_pass_by_observable.values())
    )
    spatial_gate_results = {
        "configuration": configuration_pass,
        "effective_time_step_identity": effective_pass,
        "per_case_numerical_quality": numerical_quality_pass,
        "smooth_observable_convergence": smooth_convergence_pass,
        "binary_event_consistency": binary_consistency_pass,
    }
    spatial_stage_pass = all(spatial_gate_results.values())
    prerequisite_pass = bool(
        configuration_pass and effective_pass and numerical_quality_pass
    )
    spatial_group_result_consistent = bool(
        spatial.get("prerequisite_pass") is prerequisite_pass
        and spatial.get("status") in {"PASS", "FAIL"}
        and (spatial.get("status") == "PASS") is spatial_stage_pass
    )

    expected_loaded_logical_ids = {
        *SPATIAL_CASE_IDS,
        TEMPORAL_REUSE_LOGICAL_CASE_ID,
    }
    run_audit_keys = {str(key) for key in run_audits}
    quality_keys = {str(key) for key in numerical_quality}
    protocol_valid = bool(
        freeze_report.get("status")
        == "Q1R3_FROZEN__SPATIAL_SCREENING_AUTHORIZED"
        and freeze_report.get("gate_pass") is True
        and list(frozen_identity.get("spatial_case_ids", []))
        == list(SPATIAL_CASE_IDS)
        and list(frozen_identity.get("temporal_new_case_ids", []))
        == list(TEMPORAL_NEW_CASE_IDS)
        and frozen_identity.get("temporal_reuse_case_id")
        == SPATIAL_CASE_IDS[-1]
        and stage_1.get("analyse_before_stage_2") is True
        and list(stage_1.get("case_ids", [])) == list(SPATIAL_CASE_IDS)
        and stage_2.get("authorized_only_if_stage_1_passes") is True
        and list(stage_2.get("new_case_ids", []))
        == list(TEMPORAL_NEW_CASE_IDS)
        and stage_2.get("reuse_case_id") == SPATIAL_CASE_IDS[-1]
        and early_stop.get("condition") == EXPECTED_EARLY_STOP_CONDITION
        and early_stop.get("action") == EXPECTED_EARLY_STOP_ACTION
    )
    evidence_gates = {
        "freeze_report_authorizes_staged_spatial_screening": protocol_valid,
        "campaign_manifest_byte_identity_matches_freeze": bool(
            freeze_inputs.get("frozen_manifest_file_sha256")
            == campaign_manifest_file_sha256
        ),
        "campaign_manifest_case_inventory_exact": bool(
            campaign_case_ids == list(EXPECTED_LOGICAL_CASE_IDS)
        ),
        "qualification_report_bound_to_campaign_manifest": bool(
            qualification.get("campaign_manifest_sha256")
            == canonical_sha256(campaign_manifest)
        ),
        "qualification_report_is_expected_spatial_stage_partial": bool(
            qualification.get("status")
            == "INCOMPLETE_MISSING_OR_UNREADABLE_RUNS"
            and missing_cases == list(TEMPORAL_NEW_CASE_IDS)
            and not load_errors
            and not terminal_failures
            and temporal.get("status") == "INCOMPLETE"
        ),
        "only_expected_logical_runs_loaded": bool(
            run_audit_keys == expected_loaded_logical_ids
            and quality_keys == expected_loaded_logical_ids
            and coverage.get("complete") is False
            and coverage.get("loaded_logical_case_count") == 4
            and coverage.get("manifest_logical_case_count") == 6
        ),
        "three_spatial_archives_payload_complete_and_bound": bool(
            set(archive_inventory) == set(SPATIAL_CASE_IDS)
            and all(spatial_identity_by_case.values())
        ),
        "formal_source_and_runtime_identity_consistent": bool(
            qualification.get("source_tree_consistent_across_runs") is True
            and qualification.get("source_tree_sha256")
            == expected_source_sha256
            and qualification.get("execution_runtime_consistent_across_runs")
            is True
            and qualification.get("pressure_accelerator_requirement_met_for_loaded_runs")
            is True
        ),
        "frozen_physical_and_quality_contract_identity": bool(
            campaign_manifest.get("physical_signature_sha256")
            == frozen_identity.get("physical_signature_sha256")
            and qualification_quality_contract == campaign_quality_contract
            and qualification_quality_contract.get("pass") is True
            and refinement_design.get("pass") is True
        ),
        "temporal_new_case_paths_absent": bool(
            set(temporal_new_case_paths_absent) == set(TEMPORAL_NEW_CASE_IDS)
            and all(temporal_new_case_paths_absent.values())
        ),
        "spatial_group_result_consistent_with_component_gates": (
            spatial_group_result_consistent
        ),
        "apparatus_prediction_remains_disallowed": bool(
            claim_boundary.get("apparatus_prediction_allowed") is False
            and _mapping(
                campaign_manifest.get("claim_boundary"),
                "campaign claim boundary",
            ).get("apparatus_prediction_allowed")
            is False
        ),
    }
    decision_established = all(evidence_gates.values())
    temporal_authorized = bool(decision_established and spatial_stage_pass)
    failure_established = bool(decision_established and not spatial_stage_pass)
    if temporal_authorized:
        status = "Q1R3_SPATIAL_STAGE_PASS__TEMPORAL_EXECUTION_AUTHORIZED"
        action = "EXECUTE_TEMPORAL_NEW_CASES_SEQUENTIALLY"
    elif failure_established:
        status = "Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED"
        action = EXPECTED_EARLY_STOP_ACTION
    else:
        status = (
            "Q1R3_SPATIAL_STAGE_DECISION_NOT_ESTABLISHED__"
            "TEMPORAL_NOT_AUTHORIZED"
        )
        action = "DO_NOT_EXECUTE_TEMPORAL_NEW_CASES"

    smooth_summary: dict[str, dict[str, Any]] = {}
    for name in expected_smooth:
        record = _mapping(smooth[name], f"smooth observable: {name}")
        estimate = _mapping(record.get("estimate"), f"smooth estimate: {name}")
        fine_medium = _mapping(
            estimate.get("pair_fine_medium"), f"fine-medium pair: {name}"
        )
        smooth_summary[name] = {
            "pass": record.get("pass") is True,
            "relative_l2_fine_medium": fine_medium.get("relative_l2"),
            "relative_linf_fine_medium": fine_medium.get("relative_linf"),
            "observed_order": estimate.get("observed_order"),
            "fine_grid_gci_l2": estimate.get("fine_grid_gci_l2"),
            "asymptotic_trend": estimate.get("asymptotic_trend"),
            "reason": estimate.get("reason"),
            "thresholds": record.get("thresholds"),
        }
    binary_summary = {
        name: {
            "pass": _mapping(binary[name], f"binary observable: {name}").get(
                "pass"
            )
            is True,
            "status": _mapping(
                binary[name], f"binary observable: {name}"
            ).get("status"),
        }
        for name in expected_binary
    }

    return {
        "schema_version": 1,
        "status": status,
        "gate_pass": decision_established,
        "decision_established": decision_established,
        "spatial_stage_pass": spatial_stage_pass,
        "evidence_gates": evidence_gates,
        "spatial_gate_results": spatial_gate_results,
        "failed_spatial_gates": [
            name for name, passed in spatial_gate_results.items() if not passed
        ],
        "failed_smooth_observables": [
            name for name, passed in smooth_pass_by_observable.items() if not passed
        ],
        "inputs": {
            "freeze_report_sha256": freeze_report_sha256,
            "campaign_manifest_file_sha256": campaign_manifest_file_sha256,
            "campaign_manifest_canonical_sha256": canonical_sha256(
                campaign_manifest
            ),
            "qualification_report_sha256": qualification_report_sha256,
            "qualification_report_status": qualification.get("status"),
            "spatial_archive_inventory": dict(archive_inventory),
        },
        "frozen_identity": {
            "formal_source_tree_sha256": expected_source_sha256,
            "physical_signature_sha256": frozen_identity.get(
                "physical_signature_sha256"
            ),
            "quality_contract_id": campaign_quality_contract.get("contract_id"),
            "spatial_case_ids": list(SPATIAL_CASE_IDS),
            "temporal_reuse_case_id": SPATIAL_CASE_IDS[-1],
            "temporal_new_case_ids": list(TEMPORAL_NEW_CASE_IDS),
        },
        "spatial_identity_by_case": spatial_identity_by_case,
        "quality_pass_by_case": quality_by_case,
        "effective_time_step_gate": dict(effective),
        "configuration_gate": dict(configuration),
        "smooth_observables": smooth_summary,
        "binary_observables": binary_summary,
        "topology_regime_exercised": spatial.get("topology_regime_exercised"),
        "execution_decision": {
            "spatial_failure_established_before_temporal_execution": (
                failure_established
            ),
            "temporal_new_cases_authorized": temporal_authorized,
            "temporal_new_case_paths_absent": dict(
                temporal_new_case_paths_absent
            ),
            "remaining_temporal_runs_required_to_establish_q1r3_failure": (
                False if failure_established else None
            ),
            "action": action,
            "temporal_convergence_status": "NOT_EVALUATED",
        },
        "claim_boundary": {
            "q1r3_numerical_screening_passed": False,
            "spatial_convergence_demonstrated": spatial_stage_pass,
            "temporal_convergence_evaluated": False,
            "numerical_qualification_demonstrated": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
    }


def analyse(
    *,
    freeze_report_path: Path,
    campaign_manifest_path: Path,
    qualification_report_path: Path,
    run_root: Path,
    expected_source_sha256: str,
) -> dict[str, Any]:
    freeze_report = _read_json(freeze_report_path)
    campaign_manifest = _read_json(campaign_manifest_path)
    qualification = _read_json(qualification_report_path)
    archives = {
        case_id: _archive_inventory(run_root / case_id)
        for case_id in SPATIAL_CASE_IDS
    }
    time_paths_absent = {
        case_id: not (run_root / case_id).exists()
        for case_id in TEMPORAL_NEW_CASE_IDS
    }
    report = assess(
        freeze_report,
        campaign_manifest,
        qualification,
        freeze_report_sha256=_sha256(freeze_report_path),
        campaign_manifest_file_sha256=_sha256(campaign_manifest_path),
        qualification_report_sha256=_sha256(qualification_report_path),
        archive_inventory=archives,
        temporal_new_case_paths_absent=time_paths_absent,
        expected_source_sha256=expected_source_sha256,
    )
    if report["decision_established"] and not report["spatial_stage_pass"]:
        report["posthoc_failure_diagnostic"] = _posthoc_failure_diagnostic(
            campaign_manifest=campaign_manifest,
            campaign_manifest_path=campaign_manifest_path,
            qualification=qualification,
            run_root=run_root,
        )
    return report


def _format_number(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "n/a"
    return f"{number:.6g}"


def markdown(report: Mapping[str, Any]) -> str:
    inputs = _mapping(report.get("inputs"), "inputs")
    decision = _mapping(report.get("execution_decision"), "execution decision")
    effective = _mapping(
        report.get("effective_time_step_gate"), "effective time-step gate"
    )
    gate_rows = "\n".join(
        f"| `{name}` | {'PASS' if passed else 'FAIL'} |"
        for name, passed in _mapping(
            report.get("spatial_gate_results"), "spatial gates"
        ).items()
    )
    step_rows = "\n".join(
        "| `{id}` | {fraction:.6f} | {minimum:.6f} | {result} |".format(
            id=item["id"],
            fraction=float(item["configured_step_active_fraction"]),
            minimum=float(item["minimum_configured_step_active_fraction"]),
            result="PASS" if item.get("pass") is True else "FAIL",
        )
        for item in _list(effective.get("cases"), "effective cases")
    )
    smooth_rows = "\n".join(
        "| `{name}` | {l2} | {linf} | {order} | {gci} | {result} |".format(
            name=name,
            l2=_format_number(record.get("relative_l2_fine_medium")),
            linf=_format_number(record.get("relative_linf_fine_medium")),
            order=_format_number(record.get("observed_order")),
            gci=_format_number(record.get("fine_grid_gci_l2")),
            result="PASS" if record.get("pass") is True else "FAIL",
        )
        for name, record in _mapping(
            report.get("smooth_observables"), "smooth observables"
        ).items()
    )
    binary_rows = "\n".join(
        f"| `{name}` | `{record.get('status')}` | "
        f"{'PASS' if record.get('pass') is True else 'FAIL'} |"
        for name, record in _mapping(
            report.get("binary_observables"), "binary observables"
        ).items()
    )
    archive_rows = "\n".join(
        f"| `{case_id}` | `{record['manifest_sha256']}` | "
        f"{'PASS' if record['payload_integrity_valid'] else 'FAIL'} |"
        for case_id, record in _mapping(
            inputs.get("spatial_archive_inventory"), "archive inventory"
        ).items()
    )
    failed_names = ", ".join(
        f"`{name}`"
        for name in _list(
            report.get("failed_smooth_observables"),
            "failed smooth observables",
        )
    )
    diagnostic_block = ""
    raw_diagnostic = report.get("posthoc_failure_diagnostic")
    if isinstance(raw_diagnostic, Mapping):
        diagnostic = _mapping(raw_diagnostic, "posthoc failure diagnostic")
        windows = _mapping(diagnostic.get("window_results"), "diagnostic windows")
        window_rows = "\n".join(
            "| `{label}` | {start} | {end} | {passed}/{total} |".format(
                label=label,
                start=_format_number(record.get("start_s")),
                end=_format_number(record.get("end_s")),
                passed=record.get("pass_count"),
                total=record.get("observable_count"),
            )
            for label, record in windows.items()
        )
        inventory = _mapping(
            diagnostic.get("inventory_change_onset_s"),
            "inventory onset",
        )
        inventory_rows = "\n".join(
            "| `{case_id}` | {one} | {hundred} | {thousand} |".format(
                case_id=case_id,
                one=_format_number(record.get("absolute_1e-06")),
                hundred=_format_number(record.get("absolute_1e-04")),
                thousand=_format_number(record.get("absolute_1e-03")),
            )
            for case_id, record in inventory.items()
        )
        resources = _list(
            diagnostic.get("next_equal_ratio_resource_indicators"),
            "resource indicators",
        )
        resource_rows = "\n".join(
            "| {target} | `{grid}` | {cells} | {ratio} | {capillary} | "
            "{rss} | {wall} |".format(
                target=_format_number(
                    item.get("target_bar_diameter_cells_worst_axis")
                ),
                grid=" × ".join(str(value) for value in item["grid_nx_ny_nz"]),
                cells=item.get("box_cell_count"),
                ratio=_format_number(
                    item.get("cell_ratio_relative_to_q1r3_fine")
                ),
                capillary=_format_number(
                    item.get("static_capillary_time_step_ceiling_s")
                ),
                rss=_format_number(
                    float(item["linear_cpu_rss_indicator_bytes"])
                    / (1024.0**3)
                ),
                wall=_format_number(
                    float(
                        item[
                            "static_step_adjusted_linear_wall_clock_indicator_s"
                        ]
                    )
                    / 3600.0
                ),
            )
            for item in resources
        )
        cause = _mapping(diagnostic.get("cause_assessment"), "cause assessment")
        late_failed = ", ".join(
            f"`{name}`"
            for name in _list(
                diagnostic.get("late_constant_rpm_failed_smooth_observables"),
                "late failed observables",
            )
        )
        diagnostic_block = f"""
## 只读失败机制审计

本节是冻结失败后的诊断，不是新增门，也不能事后改变 Q1R3 的 FAIL。按原输出时间网格把 `0` 至 `0.005 s` 加速段和随后三个恒定 `300 rpm` 的 `0.005 s` 子窗分别重算同一组阈值，结果如下。

| 诊断窗口 | 起点 s | 终点 s | 达到冻结阈值的平滑量 |
|---|---:|---:|---:|
{window_rows}

最晚恒速子窗仍失败：{late_failed}。因此“失败只由加速启动段造成”不受现有证据支持：`{cause['failure_confined_to_acceleration_ramp_supported']}`。归档/源码身份异常、单算例已登记质量硬门失败和二值拓扑事件触发也均不受支持；现有证据支持的是当前 `4/6/9` 格/磁子直径路径尚未进入合格的渐近网格响应区。

液体净存量变化首次达到不同绝对量级的输出时刻如下。这里仍是开边界模型/域存量响应，不是通量修正后的数值守恒残差。

| 空间算例 | 达到 1e-6 的时刻 s | 达到 1e-4 的时刻 s | 达到 1e-3 的时刻 s |
|---|---:|---:|---:|
{inventory_rows}

粗层在 `0.02 s` 内没有达到这些量级，中层和细层依次出现且细层更早、更大；与此同时三层开边界通量修正守恒残差都约为 `1e-11`。这支持“开边界存量响应具有强网格依赖”，不支持“求解器数值不守恒”。

如果延续冻结的 `1.5` 细化比，向上平移一级的 `6/9/13.5` 路径至少要新增 `13.5` 格层；完全以当前细层作为新粗层的 `9/13.5/20.25` 路径还需要 `20.25` 格层。基于 Q1R3 细层的线性资源指标为：

| 目标格数 | 网格 | 箱体单元数 | 相对细层单元数 | 静态毛细步上限 s | 线性 CPU RSS 指标 GiB | 静态步修正墙钟指标 h |
|---:|---|---:|---:|---:|---:|---:|
{resource_rows}

两种新增分辨率的静态毛细步上限都低于 Q1R3 共同登记步长 `5.5e-6 s`。因此即使选择较省资源的 `6/9/13.5`，也不能只补跑一个 `13.5` 层并复用旧 `6/9` 归档；必须先冻结更小的共同步长并重新执行整组三层，否则实际步长身份门会失败。表中仍只是单元数和静态步长的线性指标，不是 GPU 显存实测、动态限步预测或可行性保证。

现有三层同时改变磁子/Brinkman 表示、弥散界面与边界层物理宽度、开边界离散响应；短暂窗口也仍可能影响结果。仅凭本次三层不能唯一裁决哪一项是主因：`{cause['unique_dominant_mechanism_identified']}`。

因此不能自动冻结 Q1R4。下一步需要人工在“同模型 `6/9/13.5` 新共同步长整组重跑”“同模型 `9/13.5/20.25` 更细路径并先做资源预检”“改变启动或观测窗口”“改变正则化、边界或模型身份”之间选择；每条路径都必须重新预注册，且不得用来改写 Q1R3 失败事实。
"""
    return f"""# HF3D Q1R3 空间阶段裁决

状态：`{report['status']}`。

裁决证据门全部通过：`{report['decision_established']}`。Q1R3 空间阶段通过：`{report['spatial_stage_pass']}`。原始通用分析报告的顶层状态为 `{inputs['qualification_report_status']}`，原因仅是冻结协议要求先不运行时间中、细层；本裁决只读取其中已完整形成的 `groups.spatial`，并另行核验冻结报告、生成清单和三份空间归档载荷。

## 绑定身份

- 冻结报告逐字节 SHA-256：`{inputs['freeze_report_sha256']}`。
- 生成清单逐字节 SHA-256：`{inputs['campaign_manifest_file_sha256']}`。
- 原始资格报告逐字节 SHA-256：`{inputs['qualification_report_sha256']}`。

| 空间算例 | 归档清单 SHA-256 | 全部登记载荷 |
|---|---|---:|
{archive_rows}

三份归档均完整到冻结时长，生成配置与归档配置一致，正式源码身份一致，实际选择 CUDA 且无回退。正式理论和正式程序未因本裁决而修改。

## 空间阶段门

| 门 | 结果 |
|---|---:|
{gate_rows}

配置、实际时步身份和逐算例数值质量均通过。实际登记步长命中率如下。

| 空间算例 | 实际命中率 | 冻结下限 | 结果 |
|---|---:|---:|---:|
{step_rows}

## 平滑量空间收敛

| 归档观测量 | 两最细层相对 L2 | 两最细层相对 Linf | 观测阶 | 细网格 GCI | 结果 |
|---|---:|---:|---:|---:|---:|
{smooth_rows}

六个冻结必需平滑量全部失败：{failed_names}。其中 `liquid_mass_error_relative` 在 Q1R3 质量契约中的规范语义是 `liquid_inventory_change_relative`，它没有未经标定的绝对硬门，但仍按预注册要求参与空间平滑量收敛。`min_gap_to_bar_m` 的两最细层相对误差虽小，但三层差分不能建立正观测阶，因此同样不能通过冻结收敛门。

## 二值事件一致性

| 观测量 | 状态 | 一致性门 |
|---|---|---:|
{binary_rows}

三个事件在三层均未发生，所以一致性门没有出现层间冲突，但拓扑区间并未被激发；这不能解释为接触、连通或有限气道已经得到物理验证。

## 执行裁决

- 空间失败已在时间新增算例运行前建立：`{decision['spatial_failure_established_before_temporal_execution']}`。
- 时间中、细层授权：`{decision['temporal_new_cases_authorized']}`。
- 冻结动作：`{decision['action']}`。
- 时间收敛状态：`{decision['temporal_convergence_status']}`。

依冻结早停协议，时间中层 `time_L1_2.750e-06s` 和时间细层 `time_L2_1.375e-06s` 均保持未启动；它们不再是建立本次 Q1R3 失败所需的运行。若以后要研究失败机制，必须另行预注册为失败后的诊断，不能把结果用于事后挽救冻结 Q1R3。
{diagnostic_block}

## 结论边界

Q1R3 没有通过空间数值筛查，时间收敛未评估，完整数值资格没有建立。逐算例质量门通过只说明这些归档没有触发已登记的 CFL、压力残差、投影散度、相场修复和开边界通量修正守恒硬门；它不抵消空间不收敛。更长时统计稳态、物理校准、实验验证和装置级预测均未执行，装置预测继续为 `NO-GO`。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze-report", type=Path, required=True)
    parser.add_argument("--campaign-manifest", type=Path, required=True)
    parser.add_argument("--qualification-report", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    report = analyse(
        freeze_report_path=args.freeze_report.resolve(),
        campaign_manifest_path=args.campaign_manifest.resolve(),
        qualification_report_path=args.qualification_report.resolve(),
        run_root=args.run_root.resolve(),
        expected_source_sha256=args.expected_source_sha256,
    )
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "decision_established": report["decision_established"],
                "spatial_stage_pass": report["spatial_stage_pass"],
                "temporal_new_cases_authorized": report["execution_decision"][
                    "temporal_new_cases_authorized"
                ],
            }
        )
    )
    return 0 if report["decision_established"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
