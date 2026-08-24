"""Completed-run loading, convergence analysis, and report emission."""
from __future__ import annotations
import argparse
import json
from typing import Any, Mapping
from hfq_core import (
    QualificationError,
    canonical_sha256,
    convergence_estimate,
    estimate_period_s,
    event_consistency,
    event_summary,
    gate_convergence,
    grid_audit,
    report_markdown,
    write_json,
)
from hfq_cli_common import (
    LoadedCase,
    UserFacingError,
    _configuration_gate_group,
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
        "smooth_observables": smooth_results,
        "binary_observables": binary_results,
        "topology_regime_exercised": topology_exercised,
    }

def _analyse_command(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    manifest = read_json(manifest_path)
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
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        case_id = str(case.get("id"))
        if case_id in missing:
            continue
        try:
            loaded[case_id] = _load_case(case, manifest_path, runs_root)
        except Exception as exc:
            load_errors[case_id] = f"{type(exc).__name__}: {exc}"

    expected_signature = str(manifest.get("physical_signature_sha256", ""))
    run_audits = {
        case_id: _run_audit(item, expected_signature)
        for case_id, item in loaded.items()
    }
    source_digests = {
        audit.get("source_tree_sha256_start")
        for audit in run_audits.values()
        if audit.get("source_tree_sha256_start") is not None
    }
    source_consistent = len(source_digests) == 1 and len(run_audits) > 0

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

    all_cases_loaded = not missing and not load_errors and len(loaded) == len(cases)
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

    if not all_cases_loaded:
        status = "INCOMPLETE_MISSING_OR_UNREADABLE_RUNS"
    elif not source_consistent or not all_run_audits_pass:
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
        "source_tree_consistent_across_runs": source_consistent,
        "source_tree_sha256": next(iter(source_digests)) if source_consistent else None,
        "missing_cases": missing,
        "load_errors": load_errors,
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
