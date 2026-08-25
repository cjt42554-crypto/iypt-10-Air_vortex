"""Completed-run loading, convergence analysis, and report emission."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from hfq_core import (
    QualificationError,
    audit_refinement_design,
    canonical_sha256,
    convergence_estimate,
    estimate_period_s,
    event_consistency,
    event_summary,
    gate_convergence,
    grid_audit,
    read_json,
    report_markdown,
    write_json,
)
from hfq_cli_common import (
    LoadedCase,
    UserFacingError,
    _configuration_gate_group,
    _effective_time_step_gate,
    _explicit_config_differences,
    _finite_gate,
    _load_case,
    _nested,
    _quality_gate,
    _run_audit,
    _series,
    _to_plain_mapping,
)

def _group_analysis(
    group_name: str,
    group_cases: list[LoadedCase],
    observables: Mapping[str, Any],
    gates: Mapping[str, Any],
    run_audits: Mapping[str, Mapping[str, Any]],
    quality: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if len(group_cases) < 3:
        return {"status": "INCOMPLETE", "reason": "fewer than three completed levels"}
    ordered = sorted(group_cases, key=lambda item: int(item.case["level"]))
    coarse, medium, fine = ordered[-3], ordered[-2], ordered[-1]
    configuration_result = _configuration_gate_group(
        ordered,
        _to_plain_mapping(gates.get("configuration")),
    )
    refinement_design_gate = _to_plain_mapping(gates.get("refinement_design"))
    raw_active_fraction = refinement_design_gate.get(
        "configured_time_step_active_fraction_min", 0.95
    )
    if isinstance(raw_active_fraction, bool) or not isinstance(
        raw_active_fraction, (int, float)
    ):
        raise UserFacingError(
            "refinement_design.configured_time_step_active_fraction_min must be numeric"
        )
    effective_time_step_result = _effective_time_step_gate(
        group_name,
        ordered,
        minimum_configured_step_fraction=float(raw_active_fraction),
    )
    if effective_time_step_result.get("pass") is not True:
        return {
            "status": "FAIL_EFFECTIVE_TIME_STEP_GATE",
            "configuration_gate": configuration_result,
            "effective_time_step_gate": effective_time_step_result,
            "topology_regime_exercised": False,
        }
    if group_name == "spatial":
        h_coarse = grid_audit(coarse.config).h_max_m
        h_medium = grid_audit(medium.config).h_max_m
        h_fine = grid_audit(fine.config).h_max_m
    else:
        h_coarse = float(_nested(coarse.config, "high_fidelity", "time_step_s"))
        h_medium = float(_nested(medium.config, "high_fidelity", "time_step_s"))
        h_fine = float(_nested(fine.config, "high_fidelity", "time_step_s"))
    if not h_fine < h_medium < h_coarse:
        return {
            "status": "INVALID_REFINEMENT_ORDER",
            "coordinates": [h_coarse, h_medium, h_fine],
        }

    smooth_names = observables.get("smooth", [])
    binary_names = observables.get("binary", [])
    smooth_gate = _to_plain_mapping(gates.get("smooth_convergence"))
    floors = _to_plain_mapping(smooth_gate.get("absolute_scale_floors"))
    smooth_results: dict[str, Any] = {}
    smooth_pass = True
    for name in smooth_names:
        try:
            estimate = convergence_estimate(
                fine=_series(fine, name),
                medium=_series(medium, name),
                coarse=_series(coarse, name),
                h_fine=h_fine,
                h_medium=h_medium,
                h_coarse=h_coarse,
                absolute_scale_floor=float(floors.get(name, 0.0)),
                safety_factor=float(smooth_gate.get("gci_safety_factor", 1.25)),
            )
            gated = gate_convergence(
                estimate,
                relative_l2_max=_finite_gate(smooth_gate, "relative_l2_max"),
                relative_linf_max=_finite_gate(smooth_gate, "relative_linf_max"),
                gci_l2_max=_finite_gate(smooth_gate, "gci_l2_max"),
                observed_order_min=_finite_gate(smooth_gate, "observed_order_min"),
            )
        except (QualificationError, UserFacingError) as exc:
            gated = {"pass": False, "reason": str(exc), "estimate": {}}
        smooth_results[str(name)] = gated
        smooth_pass = smooth_pass and bool(gated.get("pass"))

    event_gate = _to_plain_mapping(gates.get("binary_event"))
    period_s = estimate_period_s(fine.config)
    binary_results: dict[str, Any] = {}
    binary_pass = True
    topology_exercised = False
    for name in binary_names:
        try:
            summaries = [event_summary(_series(item, name)) for item in (coarse, medium, fine)]
            topology_exercised = topology_exercised or any(item.occurred for item in summaries)
            if not any(item.occurred for item in summaries):
                result = {
                    "pass": True,
                    "status": "NOT_EXERCISED",
                    "coarse": summaries[0].to_dict(),
                    "medium": summaries[1].to_dict(),
                    "fine": summaries[2].to_dict(),
                }
            else:
                result = event_consistency(
                    summaries[2],
                    summaries[1],
                    absolute_time_tolerance_s=_finite_gate(
                        event_gate, "absolute_time_tolerance_s"
                    ),
                    period_s=period_s,
                    period_fraction_tolerance=_finite_gate(
                        event_gate, "period_fraction_tolerance"
                    ),
                    duty_fraction_tolerance=_finite_gate(
                        event_gate, "duty_fraction_tolerance"
                    ),
                    transition_count_tolerance=int(
                        event_gate.get("transition_count_tolerance", 0)
                    ),
                )
                result["status"] = "EXERCISED"
        except (QualificationError, UserFacingError, ValueError) as exc:
            result = {"pass": False, "status": "ERROR", "reason": str(exc)}
        binary_results[str(name)] = result
        binary_pass = binary_pass and bool(result.get("pass"))

    selected_ids = [str(item.case["id"]) for item in (coarse, medium, fine)]
    prerequisite_pass = all(
        bool(run_audits[case_id].get("completed"))
        and bool(run_audits[case_id].get("execution_source_identity_valid"))
        and bool(run_audits[case_id].get("physical_signature_match"))
        and bool(quality[case_id].get("pass"))
        for case_id in selected_ids
    )
    prerequisite_pass = prerequisite_pass and bool(configuration_result.get("pass"))
    prerequisite_pass = prerequisite_pass and bool(
        effective_time_step_result.get("pass")
    )
    status = (
        "PASS"
        if prerequisite_pass and smooth_pass and binary_pass
        else "FAIL"
    )
    return {
        "status": status,
        "selected_case_ids_coarse_medium_fine": selected_ids,
        "refinement_coordinate_coarse_medium_fine": [h_coarse, h_medium, h_fine],
        "prerequisite_pass": prerequisite_pass,
        "configuration_gate": configuration_result,
        "effective_time_step_gate": effective_time_step_result,
        "smooth_observables": smooth_results,
        "binary_observables": binary_results,
        "topology_regime_exercised": topology_exercised,
    }

def _analyse_command(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    manifest = read_json(manifest_path)
    design_audit = audit_refinement_design(manifest_path)
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise UserFacingError("manifest has no cases")
    runs_root = args.runs.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    missing = []
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        case_id = str(case.get("id"))
        archive_id = str(case.get("reuse_case_id") or case_id)
        if not (runs_root / archive_id).exists():
            missing.append(case_id)
    loaded: dict[str, LoadedCase] = {}
    load_errors: dict[str, str] = {}
    terminal_failures: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        case_id = str(case.get("id"))
        if case_id in missing:
            continue
        try:
            loaded[case_id] = _load_case(case, manifest_path, runs_root)
        except Exception as exc:
            archive_id = str(case.get("reuse_case_id") or case_id)
            terminal_failure = _terminal_failure_record(
                runs_root / archive_id,
                generated_config=read_json(
                    manifest_path.parent / str(case.get("config_file"))
                ),
            )
            if terminal_failure is not None:
                terminal_failures[case_id] = terminal_failure
            else:
                load_errors[case_id] = f"{type(exc).__name__}: {exc}"

    expected_signature = str(manifest.get("physical_signature_sha256", ""))
    run_audits = {
        case_id: _run_audit(item, expected_signature)
        for case_id, item in loaded.items()
    }
    loaded_source_digests = {
        audit.get("source_tree_sha256_start")
        for audit in run_audits.values()
        if audit.get("source_tree_sha256_start") is not None
    }
    terminal_source_digests = {
        item.get("source_tree_sha256_start")
        for item in terminal_failures.values()
        if item.get("source_tree_sha256_start") is not None
    }
    source_digests = loaded_source_digests | terminal_source_digests
    source_evidence = [*run_audits.values(), *terminal_failures.values()]
    source_consistent = bool(source_evidence) and all(
        item.get("execution_source_identity_valid") is True
        and item.get("source_tree_sha256_start") is not None
        for item in source_evidence
    ) and len(source_digests) == 1
    runtime_digests = {
        audit.get("execution_runtime_identity_sha256")
        for audit in run_audits.values()
        if audit.get("execution_runtime_identity_sha256") is not None
    }
    runtime_consistent = len(runtime_digests) == 1 and len(run_audits) > 0
    execution_requirements = _to_plain_mapping(
        manifest.get("execution_requirements")
    )
    required_pressure_accelerator = execution_requirements.get(
        "pressure_accelerator_selected"
    )
    if required_pressure_accelerator is not None and required_pressure_accelerator not in {
        "cpu",
        "cuda",
    }:
        raise UserFacingError(
            "execution_requirements.pressure_accelerator_selected must be cpu or cuda"
        )
    loaded_pressure_accelerator_requirement_met = bool(run_audits) and all(
        _nested(
            audit,
            "diagnostics",
            "pressure_accelerator_selected",
        )
        == required_pressure_accelerator
        for audit in run_audits.values()
    ) if required_pressure_accelerator is not None else True
    execution_identity_coverage_complete = len(run_audits) == len(cases)
    pressure_accelerator_requirement_met = (
        True
        if required_pressure_accelerator is None
        else bool(
            execution_identity_coverage_complete
            and loaded_pressure_accelerator_requirement_met
        )
    )

    gates = _to_plain_mapping(manifest.get("gates"))
    quality_gates = _to_plain_mapping(gates.get("numerical_quality"))
    quality = {
        case_id: _quality_gate(item, quality_gates)
        for case_id, item in loaded.items()
    }

    grouped: dict[str, list[LoadedCase]] = {"temporal": [], "spatial": []}
    for item in loaded.values():
        group = str(item.case.get("group"))
        if group in grouped:
            grouped[group].append(item)
    observables = _to_plain_mapping(manifest.get("observables"))
    group_reports = {
        group: _group_analysis(
            group,
            items,
            observables,
            gates,
            run_audits,
            quality,
        )
        for group, items in grouped.items()
    }

    all_cases_loaded = (
        not missing
        and not load_errors
        and not terminal_failures
        and len(loaded) == len(cases)
    )
    all_run_audits_pass = all(
        audit.get("completed") is True
        and audit.get("execution_source_identity_valid") is True
        and audit.get("physical_signature_match") is True
        for audit in run_audits.values()
    ) and bool(run_audits)
    all_quality_pass = all(result.get("pass") is True for result in quality.values()) and bool(quality)
    groups_pass = all(
        group_reports[name].get("status") == "PASS" for name in ("temporal", "spatial")
    )
    topology_exercised = any(
        group_reports[name].get("topology_regime_exercised") is True
        for name in ("temporal", "spatial")
    )

    if design_audit.get("pass") is not True:
        status = "FAIL_REFINEMENT_DESIGN_GATE"
    elif terminal_failures:
        status = "FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE"
    elif not all_cases_loaded:
        status = "INCOMPLETE_MISSING_OR_UNREADABLE_RUNS"
    elif (
        not source_consistent
        or not runtime_consistent
        or not pressure_accelerator_requirement_met
        or not all_run_audits_pass
    ):
        status = "FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE"
    elif not all_quality_pass:
        status = "FAIL_NUMERICAL_QUALITY_GATE"
    elif not groups_pass:
        status = "FAIL_REFINEMENT_CONVERGENCE_GATE"
    else:
        maximum_claim = str(
            _nested(manifest, "claim_boundary", "maximum_possible_status")
            or "NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL"
        )
        if maximum_claim == "NUMERICAL_SCREENING_ONLY":
            status = "NUMERICAL_SCREENING_PASSED"
        elif maximum_claim == "NUMERICALLY_QUALIFIED_SMOOTH_REGIME_ONLY":
            status = "NUMERICALLY_QUALIFIED_SMOOTH_REGIME_ONLY"
        elif topology_exercised:
            status = "NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL"
        else:
            status = "NUMERICALLY_QUALIFIED_SMOOTH_REGIME_ONLY"

    report = {
        "schema_version": 1,
        "status": status,
        "campaign_manifest_sha256": canonical_sha256(manifest),
        "refinement_design_audit": design_audit,
        "source_tree_consistent_across_runs": source_consistent,
        "source_tree_sha256": next(iter(source_digests)) if source_consistent else None,
        "source_identity_evidence_logical_case_count": len(source_evidence),
        "execution_runtime_consistent_across_runs": runtime_consistent,
        "execution_runtime_identity_sha256": (
            next(iter(runtime_digests)) if runtime_consistent else None
        ),
        "execution_requirements": execution_requirements,
        "execution_identity_coverage": {
            "loaded_logical_case_count": len(run_audits),
            "manifest_logical_case_count": len(cases),
            "complete": execution_identity_coverage_complete,
        },
        "pressure_accelerator_requirement_met_for_loaded_runs": (
            loaded_pressure_accelerator_requirement_met
        ),
        "pressure_accelerator_requirement_met": (
            pressure_accelerator_requirement_met
        ),
        "missing_cases": missing,
        "load_errors": load_errors,
        "terminal_failures": terminal_failures,
        "run_audits": run_audits,
        "numerical_quality": quality,
        "groups": group_reports,
        "claim_boundary": {
            "experimental_validation": "NOT_PERFORMED",
            "apparatus_prediction_allowed": False,
            "absolute_critical_rpm_validated": False,
            "stable_channel_radius_validated": False,
        },
    }
    write_json(output / "qualification_report.json", report)
    (output / "qualification_report.md").write_text(
        report_markdown(report), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if (
        status.startswith("NUMERICALLY_QUALIFIED")
        or status == "NUMERICAL_SCREENING_PASSED"
    ) else 1


def _terminal_failure_record(
    archive_path: Path,
    *,
    generated_config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a structured record for an integrity-readable failed archive."""

    manifest_path = archive_path / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = read_json(manifest_path)
    except Exception:
        return None
    state = manifest.get("state")
    complete = manifest.get("complete_run_archive")
    error = manifest.get("error")
    if state != "failed" and complete is not False:
        return None
    files = _to_plain_mapping(manifest.get("files"))
    payload_checks: dict[str, dict[str, Any]] = {}
    for name, raw_identity in files.items():
        identity = _to_plain_mapping(raw_identity)
        relative = Path(str(name))
        safe_name = (
            len(relative.parts) == 1
            and relative.name not in {"", ".", ".."}
        )
        payload_path = archive_path / relative if safe_name else None
        exists = bool(payload_path is not None and payload_path.is_file())
        if exists and payload_path is not None:
            content = payload_path.read_bytes()
            actual_sha256 = hashlib.sha256(content).hexdigest()
            actual_size = len(content)
        else:
            actual_sha256 = None
            actual_size = None
        payload_checks[str(name)] = {
            "pass": bool(
                exists
                and actual_sha256 == identity.get("sha256")
                and actual_size == identity.get("size_bytes")
            ),
            "actual_sha256": actual_sha256,
            "registered_sha256": identity.get("sha256"),
            "actual_size_bytes": actual_size,
            "registered_size_bytes": identity.get("size_bytes"),
        }
    payload_integrity_valid = bool(payload_checks) and all(
        item.get("pass") is True for item in payload_checks.values()
    )
    archived_config: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    try:
        archived_config = read_json(archive_path / "config.json")
        provenance = read_json(archive_path / "provenance.json")
    except Exception:
        payload_integrity_valid = False
    archived_config_canonical_digest = (
        canonical_sha256(archived_config) if archived_config else None
    )
    manifest_config_digest = manifest.get("config_sha256")
    provenance_config_digest = provenance.get("config_sha256")
    explicit_differences = (
        _explicit_config_differences(generated_config, archived_config)
        if generated_config is not None and archived_config
        else ["<unavailable>"]
    )
    config_identity_valid = bool(
        isinstance(manifest_config_digest, str)
        and manifest_config_digest
        and manifest_config_digest == provenance_config_digest
        and payload_checks.get("config.json", {}).get("pass") is True
        and not explicit_differences
    )
    source_identity = _to_plain_mapping(provenance.get("source_identity"))
    source_valid = bool(
        manifest.get("execution_source_identity_valid") is True
        and source_identity.get("execution_source_identity_valid") is True
    )
    resources = _to_plain_mapping(provenance.get("resources"))
    peak_rss = _to_plain_mapping(resources.get("peak_resident_set_size"))
    plain_error = _to_plain_mapping(error)
    traceback_identity = payload_checks.get("traceback.txt", {})
    return {
        "archive_id": archive_path.name,
        "artifact_class": manifest.get("artifact_class"),
        "state": state,
        "complete_run_archive": complete,
        "terminal_archive_payload_integrity_valid": payload_integrity_valid,
        "payload_checks": payload_checks,
        "generated_config_explicit_paths_match": not explicit_differences,
        "generated_config_explicit_differences": explicit_differences,
        "terminal_config_identity_valid": config_identity_valid,
        "execution_source_identity_valid": source_valid,
        "source_tree_sha256_start": source_identity.get(
            "source_tree_sha256_start"
        ),
        "source_tree_sha256_end": source_identity.get(
            "source_tree_sha256_end"
        ),
        "config_sha256": manifest_config_digest,
        "archived_config_candidate_canonical_sha256": (
            archived_config_canonical_digest
        ),
        "execution_runtime_partial_identity": {
            "python": _to_plain_mapping(provenance.get("python")),
            "dependencies": _to_plain_mapping(provenance.get("dependencies")),
            "pressure_accelerator_selected": "UNAVAILABLE_IN_TERMINAL_ARCHIVE",
        },
        "execution_metrics": {
            "wall_clock_duration_s": manifest.get("duration_s"),
            "progress_message_count": manifest.get("progress_messages"),
            "peak_resident_set_size_bytes": peak_rss.get("value"),
        },
        "termination": _to_plain_mapping(manifest.get("termination")),
        "error": {
            "type": plain_error.get("type"),
            "message": plain_error.get("message"),
            "traceback_sha256": traceback_identity.get("actual_sha256"),
        },
    }
