from __future__ import annotations

import unittest

from analyse_q1r2_early_stop import assess


def _report(*, active_fraction: float, mass_error: float) -> dict:
    source = "a" * 64
    run_audits = {}
    quality = {}
    effective_cases = []
    for case_id in (
        "space_L0_4cells",
        "space_L1_6cells",
        "space_L2_9cells",
    ):
        run_audits[case_id] = {
            "completed": True,
            "execution_source_identity_valid": True,
            "physical_signature_match": True,
            "source_tree_sha256_start": source,
            "diagnostics": {
                "pressure_accelerator_selected": "cuda",
                "pressure_accelerator_fallback": False,
            },
        }
        fine = case_id == "space_L2_9cells"
        quality[case_id] = {
            "pass": not fine or mass_error <= 1.0e-3,
            "checks": {
                "liquid_mass_error_relative": {
                    "value": mass_error if fine else 0.0,
                    "maximum": 1.0e-3,
                    "pass": not fine or mass_error <= 1.0e-3,
                },
                "liquid_volume_balance_error_relative": {
                    "value": 1.0e-11,
                    "maximum": 1.0e-3,
                    "pass": True,
                },
            },
        }
        effective_cases.append(
            {
                "id": case_id,
                "configured_step_active_fraction": (
                    active_fraction if fine else 0.99
                ),
                "minimum_configured_step_active_fraction": 0.95,
                "pass": not fine or active_fraction >= 0.95,
            }
        )
    return {
        "status": "FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE",
        "campaign_manifest_sha256": "b" * 64,
        "run_audits": run_audits,
        "numerical_quality": quality,
        "groups": {
            "spatial": {
                "status": "FAIL_EFFECTIVE_TIME_STEP_GATE",
                "effective_time_step_gate": {
                    "pass": False,
                    "cases": effective_cases,
                },
            },
            "temporal": {"status": "INCOMPLETE"},
        },
        "terminal_failures": {
            "time_L1_3.000e-06s": {
                "complete_run_archive": False,
                "terminal_archive_payload_integrity_valid": True,
                "terminal_config_identity_valid": True,
                "state": "cancelling",
                "error": {"message": None},
            }
        },
        "missing_cases": ["time_L2_1.500e-06s"],
        "claim_boundary": {"apparatus_prediction_allowed": False},
    }


class Q1R2EarlyStopTests(unittest.TestCase):
    def test_accepts_two_decisive_completed_spatial_failures(self) -> None:
        result = assess(
            _report(active_fraction=0.938, mass_error=1.32e-3),
            qualification_report_sha256="c" * 64,
            expected_source_sha256="a" * 64,
        )
        self.assertTrue(result["gate_pass"])
        self.assertTrue(
            result["execution_decision"][
                "failure_established_before_temporal_completion"
            ]
        )

    def test_rejects_stop_when_spatial_quality_passes(self) -> None:
        result = assess(
            _report(active_fraction=0.938, mass_error=0.9e-3),
            qualification_report_sha256="c" * 64,
            expected_source_sha256="a" * 64,
        )
        self.assertFalse(result["gate_pass"])


if __name__ == "__main__":
    unittest.main()
