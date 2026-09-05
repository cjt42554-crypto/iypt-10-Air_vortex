from __future__ import annotations

import math
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_SOURCE = REPOSITORY_ROOT / "02_模拟程序" / "src"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from analyse_d2_source_attribution import (
    _array_sha256,
    audit_accepted_time_evidence,
    audit_compact_control_evidence,
    audit_execution_revision_binding,
    audit_formal_g0_mask,
    audit_preliminary_r_consistency,
    audit_zero_drive_trajectory,
    classify_direction,
    classify_magnitude,
    compute_effect_metrics,
    initial_removed,
    interaction_kappa,
    inventory_flux_residual,
    rms,
    should_run_s5,
)
from run_d2_source_attribution import (
    R_MULTIWAY_CASES,
    SINGLE_CASES,
    accepted_times_bitwise_equal,
    effective_case_config,
    regularisation_scale_audit,
    select_group_limiter,
)


class PureMetricTests(unittest.TestCase):
    def test_initial_removal_and_rms(self) -> None:
        values = np.asarray([7.0, 8.0, 10.0])
        np.testing.assert_array_equal(
            initial_removed(values),
            np.asarray([0.0, 1.0, 3.0]),
        )
        self.assertAlmostEqual(rms(np.asarray([0.0, 3.0, 4.0])), 5.0 / math.sqrt(3.0))

    def test_effect_metrics_implement_both_frozen_residual_signs(self) -> None:
        f6 = np.asarray([10.0, 11.0, 12.0])
        f9 = np.asarray([20.0, 22.0, 24.0])
        low = np.asarray([30.0, 31.0, 32.0])
        high = np.asarray([40.0, 42.0, 44.0])
        subtract = compute_effect_metrics(f6, f9, low, high, 1.0e-12, -1)
        add = compute_effect_metrics(f6, f9, low, high, 1.0e-12, 1)
        self.assertAlmostEqual(subtract["E_Q"], math.sqrt(5.0 / 3.0))
        self.assertAlmostEqual(subtract["eta"], 1.0)
        self.assertAlmostEqual(subtract["rho"], 0.0)
        self.assertAlmostEqual(add["rho"], 2.0)
        self.assertEqual(subtract["effect_vector"], [0.0, 1.0, 2.0])

    def test_magnitude_classification_all_three_branches_and_strict_small(self) -> None:
        material, detail = classify_magnitude(
            {"a": 0.5, "b": 0.7, "c": 0.01, "d": 0.02}
        )
        self.assertEqual(material, "MATERIAL")
        self.assertEqual(detail["material_count"], 2)
        small, _ = classify_magnitude({"a": 0.0, "b": 0.01, "c": 0.05, "d": 0.099999})
        self.assertEqual(small, "SMALL")
        mixed, _ = classify_magnitude({"a": 0.1, "b": 0.01, "c": 0.05, "d": 0.09})
        self.assertEqual(mixed, "MIXED_OR_INDETERMINATE")

    def test_direction_classification_and_baseline_drift_override(self) -> None:
        reduction, _ = classify_direction({"a": 0.8, "b": 0.7, "c": 1.0, "d": 1.1})
        self.assertEqual(reduction, "REDUCTION_SIGNAL")
        increase, _ = classify_direction({"a": 1.25, "b": 1.4, "c": 1.0, "d": 1.1})
        self.assertEqual(increase, "INCREASE_SIGNAL")
        unclear, _ = classify_direction({"a": 0.8, "b": 0.7, "c": 1.25, "d": 1.4})
        self.assertEqual(unclear, "NO_CLEAR_DIRECTION")
        drift, detail = classify_direction(
            {"a": 0.8, "b": 0.7, "c": 1.0, "d": 1.1},
            baseline_drift_by_primary={
                "a": 0.0,
                "b": 0.1,
                "c": 0.0,
                "d": 0.0,
            },
        )
        self.assertEqual(drift, "DIRECTION_INCONCLUSIVE_BASELINE_DRIFT")
        self.assertTrue(detail["baseline_drift_triggered"])

    def test_s5_condition_is_frozen_by_R_magnitude(self) -> None:
        self.assertFalse(should_run_s5("SMALL"))
        self.assertTrue(should_run_s5("MATERIAL"))
        self.assertTrue(should_run_s5("MIXED_OR_INDETERMINATE"))
        with self.assertRaises(ValueError):
            should_run_s5("UNKNOWN")

    def test_interaction_residual_and_threshold(self) -> None:
        d_if = np.asarray([0.0, 1.0, 2.0])
        d_bl = np.asarray([0.0, 2.0, 4.0])
        d_st = np.asarray([0.0, 3.0, 6.0])
        additive = interaction_kappa(
            d_if + d_bl + d_st,
            d_if,
            d_bl,
            d_st,
            1.0e-12,
        )
        self.assertAlmostEqual(additive["kappa"], 0.0)
        self.assertFalse(additive["non_additive"])
        nonadditive = interaction_kappa(
            np.asarray([0.0, 10.0, 20.0]),
            d_if,
            d_bl,
            d_st,
            1.0e-12,
        )
        self.assertTrue(nonadditive["non_additive"])

    def test_inventory_flux_residual_uses_expected_open_boundary_difference(
        self,
    ) -> None:
        actual6 = np.asarray([0.0, -1.0e-4, -2.0e-4])
        actual9 = np.asarray([0.0, -3.0e-4, -6.0e-4])
        expected6 = np.asarray([0.0, -0.5e-4, -1.0e-4])
        expected9 = np.asarray([0.0, -2.5e-4, -5.0e-4])
        result = inventory_flux_residual(
            actual6,
            actual9,
            expected6,
            expected9,
        )
        self.assertAlmostEqual(result["r_flux"], 0.0)
        self.assertTrue(result["mediated"])
        self.assertEqual(
            result["label"],
            "INVENTORY_DIFFERENCE_TOP_BOUNDARY_MEDIATED",
        )


class ConfigurationAndScaleTests(unittest.TestCase):
    def test_every_case_diff_is_exactly_its_allowlist(self) -> None:
        for label in (*SINGLE_CASES, *R_MULTIWAY_CASES):
            with self.subTest(label=label):
                config, identity = effective_case_config(label)
                self.assertTrue(identity["gate_pass"])
                self.assertEqual(
                    set(identity["observed_differences"]),
                    set(identity["allowed_overrides"]),
                )
                self.assertEqual(config.run.duration_s, 0.010)
                self.assertEqual(config.run.output_interval_s, 0.001)
                self.assertEqual(
                    config.high_fidelity.pressure_solver_device,
                    "cuda",
                )

    def test_zero_drive_and_half_time_step_change_only_registered_fields(self) -> None:
        z6, z_identity = effective_case_config("Z6")
        t9, t_identity = effective_case_config("T9")
        self.assertEqual(z6.drive.target_rpm, 0.0)
        self.assertEqual(
            set(z_identity["observed_differences"]),
            {"drive.target_rpm", "run.duration_s"},
        )
        self.assertEqual(t9.high_fidelity.time_step_s, 2.75e-6)
        self.assertEqual(
            set(t_identity["observed_differences"]),
            {"high_fidelity.time_step_s", "run.duration_s"},
        )

    def test_operator_specific_regularisation_widths_are_equal(self) -> None:
        audit = regularisation_scale_audit()
        self.assertTrue(audit["gate_pass"])
        widths = audit["physical_widths"]
        for name in (
            "interface_thickness",
            "boundary_layer",
            "surface_tension_smoothing",
        ):
            self.assertAlmostEqual(
                widths[name]["F6_m"],
                widths[name]["F9_m"],
                places=15,
            )
        self.assertEqual(
            widths["interface_thickness"]["spacing_identity"],
            "minimum_spacing_m",
        )
        self.assertEqual(
            widths["boundary_layer"]["spacing_identity"],
            "maximum_spacing_m",
        )
        self.assertEqual(
            audit["source_evidence"]["contact_angle_band"]["status"],
            "UNRESOLVED_GRID_SCALED_WETTING_CONFOUND",
        )


@dataclass(frozen=True)
class Proposal:
    stable_dt_s: float
    selected_limiter: str


class TransactionProtocolTests(unittest.TestCase):
    def test_group_limiter_uses_output_or_strictest_variant(self) -> None:
        proposals = [
            Proposal(5.5e-6, "registered_time_step"),
            Proposal(4.0e-6, "capillary_stability"),
            Proposal(5.0e-6, "viscous_stability"),
        ]
        self.assertEqual(
            select_group_limiter(proposals, 3.0e-6),
            "output_alignment",
        )
        self.assertEqual(
            select_group_limiter(proposals, 8.0e-6),
            "capillary_stability",
        )

    def test_accepted_time_identity_is_bitwise_not_tolerance_based(self) -> None:
        left = SimpleNamespace(accepted_times_s=[0.0, 0.1, 0.2])
        right = SimpleNamespace(accepted_times_s=[0.0, 0.1, 0.2])
        shifted = SimpleNamespace(accepted_times_s=[0.0, 0.1, np.nextafter(0.2, 1.0)])
        self.assertTrue(accepted_times_bitwise_equal((left, right)))
        self.assertFalse(accepted_times_bitwise_equal((left, shifted)))


class IndependentEvidencePatchTests(unittest.TestCase):
    @staticmethod
    def _accepted_record(values: list[float]) -> dict[str, object]:
        array = np.asarray(values, dtype=np.float64)
        return {
            "accepted_time_count": int(array.size),
            "accepted_time_sha256": _array_sha256(array),
            "accepted_times_s": array.tolist(),
        }

    def test_zero_drive_rejects_nonzero_actual_rpm(self) -> None:
        audit = audit_zero_drive_trajectory(
            np.asarray([0.0, np.nextafter(0.0, 1.0)]),
            np.zeros(2),
        )
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["gates"]["actual_bar_rpm_exact_zero"])

    def test_zero_drive_rejects_nonzero_bar_angle(self) -> None:
        audit = audit_zero_drive_trajectory(
            np.zeros(2),
            np.asarray([0.0, -np.nextafter(0.0, 1.0)]),
        )
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["gates"]["bar_angle_rad_exact_zero"])

    def test_accepted_time_audit_rejects_nextafter_last_time(self) -> None:
        left = [0.0, 0.1, 0.2]
        right = [0.0, 0.1, float(np.nextafter(0.2, 1.0))]
        summary = {
            "variants": ["left", "right"],
            "gates": {"accepted_time_arrays_bitwise_equal": True},
            "accepted_time_evidence": {
                "left": self._accepted_record(left),
                "right": self._accepted_record(right),
            },
        }
        audit = audit_accepted_time_evidence(summary, ("left", "right"))
        self.assertFalse(audit["gate_pass"])
        self.assertTrue(audit["per_variant"]["right"]["gate_pass"])
        self.assertFalse(audit["gates"]["float64_bytes_bitwise_equal"])

    def test_accepted_time_audit_rejects_same_count_different_bytes(self) -> None:
        positive_zero = self._accepted_record([0.0, 0.1, 0.2])
        negative_zero = self._accepted_record([-0.0, 0.1, 0.2])
        negative_zero["accepted_time_sha256"] = positive_zero["accepted_time_sha256"]
        summary = {
            "variants": ["left", "right"],
            "gates": {"accepted_time_arrays_bitwise_equal": True},
            "accepted_time_evidence": {
                "left": positive_zero,
                "right": negative_zero,
            },
        }
        audit = audit_accepted_time_evidence(summary, ("left", "right"))
        self.assertFalse(audit["gate_pass"])
        right_gates = audit["per_variant"]["right"]["gates"]
        self.assertTrue(right_gates["accepted_time_count_recomputed"])
        self.assertFalse(right_gates["accepted_time_sha256_recomputed"])

    def test_revision_audit_rejects_wrong_execution_commit(self) -> None:
        expected = "a" * 40
        summary = {
            "identity": {"execution_commit": "b" * 40},
            "git_revision_execution_start": {"value": expected},
            "git_revision_execution_end": {"value": expected},
        }
        audit = audit_execution_revision_binding(summary, expected)
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["gates"]["identity_execution_commit"])

    def test_revision_audit_rejects_boundary_revision_mismatch(self) -> None:
        expected = "a" * 40
        summary = {
            "identity": {"execution_commit": expected},
            "git_revision_execution_start": {"value": expected},
            "git_revision_execution_end": {"value": "b" * 40},
        }
        audit = audit_execution_revision_binding(summary, expected)
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["gates"]["git_revision_execution_end"])

    def test_formal_g0_audit_rejects_one_flipped_archive_cell(self) -> None:
        rebuilt = np.asarray([[0, 1], [1, 0]], dtype=np.uint8)
        archived = rebuilt.copy()
        archived[0, 0] = 1
        audit = audit_formal_g0_mask(rebuilt, archived)
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["gates"]["elementwise_equal"])

    def test_compact_evidence_rejects_missing_tracked_s0_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_s0 = root / "raw" / "s0.json"
            raw_preliminary = root / "raw" / "preliminary.json"
            tracked_s0 = root / "tracked" / "s0.json"
            tracked_preliminary = root / "tracked" / "preliminary.json"
            raw_s0.parent.mkdir(parents=True)
            tracked_preliminary.parent.mkdir(parents=True)
            raw_s0.write_bytes(b"s0\n")
            raw_preliminary.write_bytes(b"preliminary\n")
            tracked_preliminary.write_bytes(raw_preliminary.read_bytes())
            audit = audit_compact_control_evidence(
                raw_s0,
                raw_preliminary,
                tracked_s0,
                tracked_preliminary,
            )
            self.assertFalse(audit["gate_pass"])
            self.assertFalse(audit["gates"]["s0_tracked_copy_exists"])

    def test_compact_evidence_rejects_missing_preliminary_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_s0 = root / "raw" / "s0.json"
            raw_preliminary = root / "raw" / "preliminary.json"
            tracked_s0 = root / "tracked" / "s0.json"
            tracked_preliminary = root / "tracked" / "preliminary.json"
            raw_s0.parent.mkdir(parents=True)
            tracked_s0.parent.mkdir(parents=True)
            raw_s0.write_bytes(b"s0\n")
            raw_preliminary.write_bytes(b"preliminary\n")
            tracked_s0.write_bytes(raw_s0.read_bytes())
            audit = audit_compact_control_evidence(
                raw_s0,
                raw_preliminary,
                tracked_s0,
                tracked_preliminary,
            )
            self.assertFalse(audit["gate_pass"])
            self.assertFalse(audit["gates"]["preliminary_r_tracked_copy_exists"])

    def test_preliminary_and_final_r_labels_must_match(self) -> None:
        preliminary = {
            "gate_pass": True,
            "R_magnitude_label": "SMALL",
            "R_direction_label": "NO_CLEAR_DIRECTION",
        }
        final_r = {
            "magnitude_label": "MATERIAL",
            "direction_label": "NO_CLEAR_DIRECTION",
        }
        audit = audit_preliminary_r_consistency(preliminary, final_r)
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["gates"]["R_magnitude_label_matches"])


if __name__ == "__main__":
    unittest.main()
