from __future__ import annotations

import unittest

from analyse_q1r3_spatial_stage import (
    EXPECTED_LOGICAL_CASE_IDS,
    SPATIAL_CASE_IDS,
    TEMPORAL_NEW_CASE_IDS,
    TEMPORAL_REUSE_LOGICAL_CASE_ID,
    assess,
)
from hfq_json import canonical_sha256

SOURCE_SHA256 = "a" * 64
CAMPAIGN_FILE_SHA256 = "b" * 64


def _fixture(*, smooth_pass: bool) -> tuple[dict, dict, dict, dict]:
    smooth_names = (
        "funnel_depth_m",
        "min_gap_to_bar_m",
        "kinetic_energy_lab_j",
        "angular_momentum_z_kg_m2_s",
        "axisymmetry_error",
        "liquid_mass_error_relative",
    )
    binary_names = (
        "first_contact_proxy",
        "connection_candidate",
        "finite_channel_candidate",
    )
    quality_contract = {
        "schema_version": 1,
        "contract_id": "OPEN_BOUNDARY_INVENTORY_CONSERVATION_SPLIT_V1",
        "pass": True,
    }
    campaign = {
        "cases": [{"id": case_id} for case_id in EXPECTED_LOGICAL_CASE_IDS],
        "observables": {
            "smooth": list(smooth_names),
            "binary": list(binary_names),
        },
        "quality_contract_audit": quality_contract,
        "physical_signature_sha256": "c" * 64,
        "claim_boundary": {"apparatus_prediction_allowed": False},
    }
    freeze = {
        "status": "Q1R3_FROZEN__SPATIAL_SCREENING_AUTHORIZED",
        "gate_pass": True,
        "inputs": {"frozen_manifest_file_sha256": CAMPAIGN_FILE_SHA256},
        "frozen_identity": {
            "physical_signature_sha256": "c" * 64,
            "spatial_case_ids": list(SPATIAL_CASE_IDS),
            "temporal_reuse_case_id": SPATIAL_CASE_IDS[-1],
            "temporal_new_case_ids": list(TEMPORAL_NEW_CASE_IDS),
        },
        "execution_protocol": {
            "stage_1": {
                "case_ids": list(SPATIAL_CASE_IDS),
                "analyse_before_stage_2": True,
            },
            "early_stop": {
                "condition": (
                    "ANY_SPATIAL_CONFIGURATION_QUALITY_EFFECTIVE_STEP_"
                    "OR_CONVERGENCE_GATE_FAILS"
                ),
                "action": "DO_NOT_EXECUTE_TEMPORAL_NEW_CASES",
            },
            "stage_2": {
                "authorized_only_if_stage_1_passes": True,
                "reuse_case_id": SPATIAL_CASE_IDS[-1],
                "new_case_ids": list(TEMPORAL_NEW_CASE_IDS),
            },
        },
    }
    run_audits: dict[str, dict] = {}
    numerical_quality: dict[str, dict] = {}
    effective_cases = []
    archive_inventory: dict[str, dict] = {}
    for index, case_id in enumerate(SPATIAL_CASE_IDS):
        config_sha256 = str(index + 1) * 64
        run_audits[case_id] = {
            "completed": True,
            "result_status": "completed",
            "actual_end_s": 0.02,
            "requested_end_s": 0.02,
            "execution_source_identity_valid": True,
            "source_tree_sha256_start": SOURCE_SHA256,
            "physical_signature_match": True,
            "physical_signature_sha256": "c" * 64,
            "generated_config_sha256": config_sha256,
            "archived_config_sha256": config_sha256,
            "diagnostics": {
                "pressure_accelerator_selected": "cuda",
                "pressure_accelerator_fallback": False,
            },
        }
        numerical_quality[case_id] = {
            "pass": True,
            "backend_fail_closed_gate_pass": True,
            "checks": {"advective_cfl": {"pass": True}},
            "quality_contract": quality_contract,
        }
        effective_cases.append(
            {
                "id": case_id,
                "pass": True,
                "configured_step_active_fraction": 0.99,
                "minimum_configured_step_active_fraction": 0.95,
            }
        )
        archive_inventory[case_id] = {
            "manifest_sha256": str(index + 4) * 64,
            "config_payload_sha256": config_sha256,
            "config_canonical_sha256": config_sha256,
            "payload_integrity_valid": True,
            "payloads": {},
        }
    run_audits[TEMPORAL_REUSE_LOGICAL_CASE_ID] = dict(
        run_audits[SPATIAL_CASE_IDS[-1]]
    )
    numerical_quality[TEMPORAL_REUSE_LOGICAL_CASE_ID] = dict(
        numerical_quality[SPATIAL_CASE_IDS[-1]]
    )
    smooth = {
        name: {
            "pass": smooth_pass,
            "estimate": {
                "asymptotic_trend": smooth_pass,
                "observed_order": 1.0 if smooth_pass else None,
                "fine_grid_gci_l2": 0.01 if smooth_pass else None,
                "reason": "fixture",
                "pair_fine_medium": {
                    "relative_l2": 0.01 if smooth_pass else 0.2,
                    "relative_linf": 0.02 if smooth_pass else 0.3,
                },
            },
            "thresholds": {},
        }
        for name in smooth_names
    }
    binary = {
        name: {"pass": True, "status": "NOT_EXERCISED"}
        for name in binary_names
    }
    qualification = {
        "status": "INCOMPLETE_MISSING_OR_UNREADABLE_RUNS",
        "campaign_manifest_sha256": canonical_sha256(campaign),
        "missing_cases": list(TEMPORAL_NEW_CASE_IDS),
        "load_errors": {},
        "terminal_failures": {},
        "groups": {
            "spatial": {
                "status": "PASS" if smooth_pass else "FAIL",
                "prerequisite_pass": True,
                "configuration_gate": {
                    "pass": True,
                    "checks": {"geometry": {"pass": True}},
                },
                "effective_time_step_gate": {
                    "group": "spatial",
                    "pass": True,
                    "registered_structure_valid": True,
                    "effective_structure_valid": True,
                    "cases": effective_cases,
                },
                "smooth_observables": smooth,
                "binary_observables": binary,
                "topology_regime_exercised": False,
            },
            "temporal": {"status": "INCOMPLETE"},
        },
        "run_audits": run_audits,
        "numerical_quality": numerical_quality,
        "quality_contract_audit": quality_contract,
        "refinement_design_audit": {"pass": True},
        "execution_identity_coverage": {
            "complete": False,
            "loaded_logical_case_count": 4,
            "manifest_logical_case_count": 6,
        },
        "source_tree_consistent_across_runs": True,
        "source_tree_sha256": SOURCE_SHA256,
        "execution_runtime_consistent_across_runs": True,
        "pressure_accelerator_requirement_met_for_loaded_runs": True,
        "claim_boundary": {"apparatus_prediction_allowed": False},
    }
    return freeze, campaign, qualification, archive_inventory


def _assess(*, smooth_pass: bool, archive_integrity: bool = True) -> dict:
    freeze, campaign, qualification, archives = _fixture(
        smooth_pass=smooth_pass
    )
    if not archive_integrity:
        archives[SPATIAL_CASE_IDS[-1]]["payload_integrity_valid"] = False
    return assess(
        freeze,
        campaign,
        qualification,
        freeze_report_sha256="d" * 64,
        campaign_manifest_file_sha256=CAMPAIGN_FILE_SHA256,
        qualification_report_sha256="e" * 64,
        archive_inventory=archives,
        temporal_new_case_paths_absent={
            case_id: True for case_id in TEMPORAL_NEW_CASE_IDS
        },
        expected_source_sha256=SOURCE_SHA256,
    )


class Q1R3SpatialStageTests(unittest.TestCase):
    def test_fail_closed_when_required_smooth_observables_fail(self) -> None:
        result = _assess(smooth_pass=False)
        self.assertTrue(result["decision_established"])
        self.assertFalse(result["spatial_stage_pass"])
        self.assertEqual(len(result["failed_smooth_observables"]), 6)
        self.assertFalse(
            result["execution_decision"]["temporal_new_cases_authorized"]
        )
        self.assertEqual(
            result["status"],
            "Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED",
        )

    def test_authorizes_temporal_stage_only_after_all_spatial_gates_pass(self) -> None:
        result = _assess(smooth_pass=True)
        self.assertTrue(result["decision_established"])
        self.assertTrue(result["spatial_stage_pass"])
        self.assertTrue(
            result["execution_decision"]["temporal_new_cases_authorized"]
        )

    def test_invalid_archive_evidence_never_authorizes_temporal_stage(self) -> None:
        result = _assess(smooth_pass=True, archive_integrity=False)
        self.assertFalse(result["decision_established"])
        self.assertFalse(
            result["execution_decision"]["temporal_new_cases_authorized"]
        )
        self.assertEqual(
            result["status"],
            "Q1R3_SPATIAL_STAGE_DECISION_NOT_ESTABLISHED__"
            "TEMPORAL_NOT_AUTHORIZED",
        )


if __name__ == "__main__":
    unittest.main()
