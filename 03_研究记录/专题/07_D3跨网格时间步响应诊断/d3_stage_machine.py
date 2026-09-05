"""Pure D3 stage-prerequisite audit shared by execution and analysis.

The function in this module has no filesystem or solver side effects.  Callers
first validate their full ledger schema, then use this audit immediately before
entering S1, S2, or S3.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def audit_s0_report(
    report: Mapping[str, Any],
    *,
    protocol_id: str,
    expected_commit: str,
    expected_action: str,
    expected_description: str,
    expected_status: str,
    expected_case_ids: Sequence[str],
) -> dict[str, Any]:
    """Validate the complete S0 report identity without filesystem access."""

    identity_start = report.get("execution_identity_start")
    binding_end = report.get("execution_binding_end")
    initial_identity = report.get("initial_grid_mask_state_identity")
    fresh_policy = report.get("fresh_evidence_policy")
    binding_fields = (
        "formal_source_tree",
        "implementation",
        "authorization",
        "r1_scientific_identity_audit",
        "v1_frozen_evidence_audit",
        "r1_v1_evidence_root_isolation_audit",
        "r1_registered_output_schedule_audit",
        "generated_config_audit",
        "cuda_probe",
    )
    expected_top_level = {
        "schema_version",
        "protocol_id",
        "stage",
        "action",
        "human_description",
        "status",
        "gate_pass",
        "execution_commit",
        "execution_identity_start",
        "execution_binding_end",
        "initial_grid_mask_state_identity",
        "fresh_evidence_policy",
    }
    expected_fresh_policy = {
        "d2_dynamic_inputs_used": False,
        "checkpoint_used": False,
        "d3_prior_raw_present": False,
        "time_steps_advanced": 0,
    }
    initial_cases = (
        initial_identity.get("cases") if isinstance(initial_identity, Mapping) else None
    )
    initial_groups = (
        initial_identity.get("groups")
        if isinstance(initial_identity, Mapping)
        else None
    )
    gates = {
        "top_level_fields_exact": set(report) == expected_top_level,
        "header_identity_exact": report.get("schema_version") == 2
        and report.get("protocol_id") == protocol_id
        and report.get("stage") == "S0"
        and report.get("action") == expected_action
        and report.get("human_description") == expected_description
        and report.get("status") == expected_status
        and report.get("gate_pass") is True
        and report.get("execution_commit") == expected_commit,
        "execution_identity_start_exact": isinstance(identity_start, Mapping)
        and identity_start.get("gate_pass") is True
        and identity_start.get("branch") == "main"
        and identity_start.get("execution_commit") == expected_commit
        and identity_start.get("worktree_porcelain") == [],
        "execution_binding_end_exact": isinstance(binding_end, Mapping)
        and binding_end.get("gate_pass") is True
        and binding_end.get("branch") == "main"
        and binding_end.get("execution_commit") == expected_commit
        and binding_end.get("worktree_porcelain") == [],
        "start_end_binding_identity_exact": isinstance(identity_start, Mapping)
        and isinstance(binding_end, Mapping)
        and all(
            identity_start.get(name) == binding_end.get(name) for name in binding_fields
        ),
        "initial_identity_gate_exact": isinstance(initial_identity, Mapping)
        and initial_identity.get("gate_pass") is True
        and initial_identity.get("time_steps_advanced") == 0
        and isinstance(initial_cases, Mapping)
        and set(initial_cases) == set(expected_case_ids)
        and isinstance(initial_groups, Mapping)
        and set(initial_groups) == {"G6", "G9"}
        and all(
            isinstance(item, Mapping) and item.get("gate_pass") is True
            for item in initial_groups.values()
        ),
        "fresh_zero_step_policy_exact": fresh_policy == expected_fresh_policy,
    }
    return {"gate_pass": all(gates.values()), "gates": gates}


def audit_stage_prerequisites(
    ledger: Mapping[str, Any],
    target_stage: str,
    *,
    stage_order: Sequence[str],
    pass_evidence: Mapping[str, Sequence[str]],
    expected_execution_commit: str,
    ledger_semantics_pass: bool,
    failure_marker_exists: bool,
) -> dict[str, Any]:
    """Require an exact PASS prefix followed by an untouched target/suffix."""

    order = tuple(stage_order)
    if target_stage not in order:
        raise ValueError(f"unknown target stage: {target_stage}")
    target_index = order.index(target_stage)
    stages = ledger.get("stages")
    exact_order = isinstance(stages, list) and [
        item.get("stage") if isinstance(item, Mapping) else None for item in stages
    ] == list(order)
    per_stage: dict[str, Any] = {}
    if isinstance(stages, list):
        for index, item in enumerate(stages):
            if not isinstance(item, Mapping) or index >= len(order):
                continue
            stage = order[index]
            predecessor = index < target_index
            expected_disposition = "EXECUTED" if predecessor else "NOT_EXECUTED"
            expected_status = "PASS" if predecessor else "NOT_EXECUTED"
            expected_evidence = (
                list(pass_evidence.get(stage, ())) if predecessor else []
            )
            gates = {
                "stage_exact": item.get("stage") == stage,
                "predecessor_or_unexecuted_role_exact": (
                    item.get("disposition") == expected_disposition
                    and item.get("status") == expected_status
                ),
                "evidence_paths_exact": item.get("evidence") == expected_evidence,
                "classification_absent_before_target_execution": (
                    item.get("classification") is None
                ),
            }
            per_stage[stage] = {
                "gate_pass": all(gates.values()),
                "gates": gates,
                "role": "PREDECESSOR_PASS" if predecessor else "UNEXECUTED",
            }
    expected_unexecuted = list(order[target_index:])
    gates = {
        "full_ledger_semantics_prevalidated": ledger_semantics_pass,
        "exact_stage_order": exact_order,
        "all_stage_prerequisites_exact": set(per_stage) == set(order)
        and all(item["gate_pass"] for item in per_stage.values()),
        "execution_commit_exact": ledger.get("execution_commit")
        == expected_execution_commit,
        "failed_stages_empty": ledger.get("failed_stages") == [],
        "unexecuted_stage_summary_exact": ledger.get("unexecuted_stages")
        == expected_unexecuted,
        "terminal_state_in_progress": ledger.get("terminal_state") == "IN_PROGRESS",
        "failure_record_absent": ledger.get("failure_record") is None,
        "failure_marker_absent": not failure_marker_exists,
        "automatic_follow_on_prohibited": ledger.get("automatic_follow_on")
        == "PROHIBITED",
    }
    return {
        "gate_pass": all(gates.values()),
        "target_stage": target_stage,
        "predecessors": list(order[:target_index]),
        "unexecuted_suffix": expected_unexecuted,
        "gates": gates,
        "stages": per_stage,
    }
