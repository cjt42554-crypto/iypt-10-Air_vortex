from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np


HERE = Path(__file__).resolve().parent
DIAGNOSTIC_ROOT = HERE.parent
if str(DIAGNOSTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(DIAGNOSTIC_ROOT))

from analyse_q1r4_diagnostics import (
    decide_d0,
    effect_ratio,
    prepare_cases,
    validate_generated_manifest,
)
from audit_q1r4_geometry import (
    assess_geometry,
    exact_capsule_second_moment_tensor,
)
from probe_q1r4_resource import (
    assess_probe_report,
    validate_probe_contract,
)


PREREGISTRATION = DIAGNOSTIC_ROOT / "q1r4_diagnostic_preregistration.json"


class Q1R4DiagnosticGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preregistration = json.loads(
            PREREGISTRATION.read_text(encoding="utf-8")
        )

    def test_preregistration_keeps_full_q1r4_closed(self) -> None:
        decision = self.preregistration["decision"]
        self.assertEqual(
            self.preregistration["decision_id"],
            "Q1R4_NOT_FROZEN__D0_DIAGNOSTIC_GATE_SELECTED",
        )
        self.assertFalse(decision["full_q1r4_campaign_frozen"])
        self.assertFalse(decision["full_q1r4_campaign_run_allowed"])
        self.assertFalse(decision["q1r3_temporal_continuation_allowed"])
        self.assertFalse(
            decision["q1r3_6_and_9_reuse_as_q1r4_qualification_evidence_allowed"]
        )

    def test_source_separation_window_and_thresholds_are_exact(self) -> None:
        protocol = self.preregistration["d0_scope"]["C_source_separation"][
            "analysis_protocol"
        ]
        self.assertEqual(protocol["common_window_s"], [0.0, 0.005])
        self.assertEqual(protocol["common_output_interval_s"], 0.001)
        self.assertEqual(protocol["eta_reg_material_threshold"], 0.5)
        self.assertEqual(protocol["eta_zero_drive_material_threshold"], 0.5)
        self.assertEqual(protocol["material_core_observable_count_min"], 2)

    def test_prepare_generates_only_registered_diagnostic_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest = prepare_cases(PREREGISTRATION, output)
            self.assertEqual(
                set(manifest["cases"]),
                {
                    "resource_probe_13p5",
                    "zero_drive_6cells",
                    "zero_drive_9cells",
                    "fixed_physical_9cells",
                },
            )
            self.assertFalse(manifest["claim_boundary"]["is_campaign_q1r4"])
            self.assertFalse(manifest["claim_boundary"]["freezes_q1r4"])
            self.assertFalse(manifest["claim_boundary"]["allows_full_q1r4_run"])
            gates = validate_generated_manifest(
                output / "cases_manifest.json", PREREGISTRATION
            )
            self.assertTrue(all(gates.values()), gates)

    def test_generated_configs_apply_exact_drive_and_regularization_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            prepare_cases(PREREGISTRATION, output)
            resource = json.loads(
                (output / "resource_probe_13p5.json").read_text(encoding="utf-8")
            )
            zero_six = json.loads(
                (output / "zero_drive_6cells.json").read_text(encoding="utf-8")
            )
            fixed = json.loads(
                (output / "fixed_physical_9cells.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [
                    resource["high_fidelity"]["radial_cells"],
                    resource["high_fidelity"]["azimuthal_cells"],
                    resource["high_fidelity"]["axial_cells"],
                ],
                [204, 204, 180],
            )
            self.assertEqual(resource["high_fidelity"]["time_step_s"], 3.75e-6)
            self.assertEqual(resource["high_fidelity"]["pressure_solver_device"], "cuda")
            self.assertEqual(zero_six["drive"]["initial_rpm"], 0.0)
            self.assertEqual(zero_six["drive"]["target_rpm"], 0.0)
            self.assertEqual(fixed["run"]["duration_s"], 0.005)
            self.assertEqual(fixed["run"]["output_interval_s"], 0.001)
            self.assertEqual(
                fixed["high_fidelity"]["interface_thickness_cells"],
                3.7777777777777777,
            )
            self.assertEqual(
                fixed["high_fidelity"]["boundary_layer_cells"],
                2.2666666666666666,
            )
            self.assertEqual(
                fixed["high_fidelity"]["surface_tension_smoothing_cells"],
                1.1333333333333333,
            )

    def test_resource_contract_rejects_any_nonregistered_grid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            prepare_cases(PREREGISTRATION, output)
            formal_source = DIAGNOSTIC_ROOT.parents[2] / "02_模拟程序/src"
            if str(formal_source) not in sys.path:
                sys.path.insert(0, str(formal_source))
            from airvortex.io.config_io import load_config_json

            config = load_config_json(output / "resource_probe_13p5.json")
            gates = validate_probe_contract(config, self.preregistration)
            self.assertTrue(all(gates.values()), gates)
            altered = json.loads(
                (output / "resource_probe_13p5.json").read_text(encoding="utf-8")
            )
            altered["high_fidelity"]["radial_cells"] = 203
            from airvortex.core import SimulationConfig

            altered_config = SimulationConfig.from_dict(altered)
            self.assertFalse(
                validate_probe_contract(altered_config, self.preregistration)["grid"]
            )

    def test_exact_capsule_second_moment_is_positive_and_axisymmetric_transverse(self) -> None:
        tensor = exact_capsule_second_moment_tensor(0.015, 0.003)
        self.assertEqual(tensor.shape, (3, 3))
        self.assertTrue(np.all(np.diag(tensor) > 0.0))
        self.assertEqual(float(tensor[1, 1]), float(tensor[2, 2]))
        self.assertTrue(np.array_equal(tensor, np.diag(np.diag(tensor))))

    @staticmethod
    def _synthetic_geometry(error: float) -> list[dict[str, object]]:
        return [
            {
                "cells_per_bar_diameter": label,
                "normalized_geometry_errors": {
                    "bar_volume_relative": error,
                    "centroid_over_bar_radius": error,
                    "second_moment_relative_frobenius": error,
                },
            }
            for label in (4.0, 6.0, 9.0, 13.5)
        ]

    def test_geometry_assessment_passes_bounded_synthetic_evidence(self) -> None:
        thresholds = self.preregistration["d0_scope"]["A_static_geometry_audit"][
            "diagnostic_boundedness_thresholds"
        ]
        result = assess_geometry(self._synthetic_geometry(0.001), thresholds)
        self.assertTrue(result["pass"])

    def test_geometry_assessment_fail_closes_material_second_moment(self) -> None:
        thresholds = self.preregistration["d0_scope"]["A_static_geometry_audit"][
            "diagnostic_boundedness_thresholds"
        ]
        levels = self._synthetic_geometry(0.001)
        levels[2]["normalized_geometry_errors"][
            "second_moment_relative_frobenius"
        ] = 0.2
        result = assess_geometry(levels, thresholds)
        self.assertFalse(result["pass"])

    def test_effect_ratio_implements_preregistered_norm_ratio(self) -> None:
        ratio = effect_ratio([3.0, 4.0], [6.0, 8.0], 1e-30)
        self.assertEqual(ratio, 0.5)

    @staticmethod
    def _synthetic_probe_report() -> dict[str, object]:
        return {
            "contract_gates": {"registered": True},
            "identity": {
                "main_branch": True,
                "worktree_clean_start_and_end": True,
                "git_head_stable": True,
                "formal_source_tree_stable": True,
            },
            "execution": {
                "completed_accepted_steps": 8,
                "accepted_step_diagnostics": [{} for _ in range(8)],
                "pressure_accelerator": {
                    "selected": "cuda",
                    "fallback": False,
                },
            },
            "resources": {
                "sample_count": 10,
                "process_rss_peak_bytes": 1,
                "host_available_memory_min_bytes": 1,
                "cuda_free_memory_min_bytes": 1,
                "cuda_global_peak_delta_from_baseline_bytes": 1,
                "cupy_pool_used_peak_bytes": 1,
                "cupy_pool_total_peak_bytes": 1,
                "nvidia_smi_process_peak_bytes": 1,
                "sampling_errors": [],
            },
            "numerical_quality": {"pass": True},
            "execution_error": None,
        }

    def test_resource_assessment_accepts_only_complete_auditable_cuda_probe(self) -> None:
        result = assess_probe_report(self._synthetic_probe_report())
        self.assertTrue(result["pass"], result)

    def test_resource_assessment_fail_closes_cuda_fallback(self) -> None:
        report = self._synthetic_probe_report()
        report["execution"]["pressure_accelerator"]["fallback"] = True
        result = assess_probe_report(report)
        self.assertFalse(result["pass"])
        self.assertFalse(result["gates"]["cuda_without_fallback"])

    def test_resource_assessment_fail_closes_missing_peak_metric(self) -> None:
        report = self._synthetic_probe_report()
        report["resources"]["nvidia_smi_process_peak_bytes"] = None
        result = assess_probe_report(report)
        self.assertFalse(result["pass"])
        self.assertFalse(result["gates"]["resource_metrics_auditable"])

    def test_decision_stops_on_resource_failure(self) -> None:
        decision = decide_d0(
            resource_pass=False,
            geometry_bounded=True,
            zero_drive_material_count=0,
            regularization_material_count=0,
            material_count_min=2,
            identity_complete=True,
        )
        self.assertEqual(
            decision["status"],
            "STOP_FULL_Q1R4__OPTIMISATION_OR_MODEL_REDESIGN_REQUIRED",
        )
        self.assertFalse(decision["full_q1r4_run_allowed"])

    def test_decision_stops_when_multiple_regularization_effects_are_material(self) -> None:
        decision = decide_d0(
            resource_pass=True,
            geometry_bounded=True,
            zero_drive_material_count=0,
            regularization_material_count=2,
            material_count_min=2,
            identity_complete=True,
        )
        self.assertEqual(
            decision["status"],
            "STOP_FULL_Q1R4__DEFINE_INTENDED_ASYMPTOTIC_MODEL_BEFORE_REFINEMENT",
        )
        self.assertFalse(decision["new_preregistration_draft_allowed"])

    def test_decision_allows_only_draft_when_all_sources_are_bounded(self) -> None:
        decision = decide_d0(
            resource_pass=True,
            geometry_bounded=True,
            zero_drive_material_count=1,
            regularization_material_count=1,
            material_count_min=2,
            identity_complete=True,
        )
        self.assertEqual(
            decision["status"],
            "DRAFT_NEW_6_9_13P5_PREREGISTRATION__DO_NOT_RUN_AUTOMATICALLY",
        )
        self.assertTrue(decision["new_preregistration_draft_allowed"])
        self.assertFalse(decision["full_q1r4_frozen"])
        self.assertFalse(decision["full_q1r4_run_allowed"])

    def test_decision_fail_closes_incomplete_identity(self) -> None:
        decision = decide_d0(
            resource_pass=True,
            geometry_bounded=True,
            zero_drive_material_count=0,
            regularization_material_count=0,
            material_count_min=2,
            identity_complete=False,
        )
        self.assertEqual(
            decision["status"],
            "STOP_FULL_Q1R4__D0_DATA_OR_SOURCE_IDENTITY_INCOMPLETE",
        )

    def test_no_campaign_q1r4_exists(self) -> None:
        self.assertFalse((DIAGNOSTIC_ROOT / "campaign_q1r4.json").exists())


if __name__ == "__main__":
    unittest.main()
